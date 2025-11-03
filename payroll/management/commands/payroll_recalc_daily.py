# payroll/management/commands/payroll_recalc_daily.py
"""
毎日夜中2時に当月分の給与を自動再計算するコマンド。
RDS上の本番DBを対象とし、税率・控除設定・勤務時間などをもとに
MonthlyPayroll テーブルを更新します。
"""

from django.core.management.base import BaseCommand
from django.utils import timezone
from decimal import Decimal
from payroll.models import Staff, MonthlyPayroll
from payroll.payroll_calculation import fixed_salary_pay
from attendance_app.models import AttendanceLog
from datetime import timedelta, datetime as dt, time as dtime
from collections import defaultdict

class Command(BaseCommand):
    help = "当月分の給与を自動再計算（夜中2時実行）"

    def handle(self, *args, **options):
        today = timezone.localdate()
        ym = today.strftime("%Y%m")
        self.stdout.write(f"🕑 自動給与再計算開始: {ym}")

        # === 会社設定取得 (同じロジック as _company_setting in views.py) ===
        try:
            from payroll.models import PayrollSetting
            setting = PayrollSetting.objects.first()
        except Exception:
            setting = None
        # fallback values
        lunch_start = getattr(setting, "lunch_break_from", dtime(12, 0)) if setting else dtime(12, 0)
        lunch_end   = getattr(setting, "lunch_break_to", dtime(13, 0)) if setting else dtime(13, 0)
        weekly_raw = getattr(setting, "weekly_holidays", []) if setting else []
        try:
            weekly = {int(x) for x in (weekly_raw or [])}
        except Exception:
            weekly = set()
        # special periods
        def _get_date(f):
            try:
                return getattr(setting, f) if setting else None
            except Exception:
                return None
        new_year_from = _get_date("new_year_from")
        new_year_to   = _get_date("new_year_to")
        bon_from      = _get_date("bon_from")
        bon_to        = _get_date("bon_to")
        gw_from       = _get_date("gw_from")
        gw_to         = _get_date("gw_to")
        special_rate  = Decimal(str(getattr(setting, "special_rate", 1) or 1))

        # Helper for special period
        def is_special(local_date):
            def in_range(s, e, d):
                return s and e and s <= d <= e
            return (
                in_range(new_year_from, new_year_to, local_date)
                or in_range(bon_from, bon_to, local_date)
                or in_range(gw_from, gw_to, local_date)
            )

        # Helper: 0.25h単位四捨五入
        def hours_qtr_decimal(td):
            h = Decimal(td.total_seconds()) / Decimal(3600)
            q = (h * Decimal(4)).quantize(Decimal("1"), rounding=Decimal.ROUND_HALF_UP)
            return q / Decimal(4)

        # Helper: yen floor
        def yen_floor(x):
            return int(Decimal(x).quantize(Decimal("1"), rounding=Decimal.ROUND_DOWN))

        for staff in Staff.objects.all():
            try:
                # 勤怠ログ取得（当月分）
                first_day = today.replace(day=1)
                last_day = (first_day + timedelta(days=32)).replace(day=1) - timedelta(days=1)
                tz = timezone.get_current_timezone()
                dt_first = dt.combine(first_day, dtime(0, 0), tzinfo=tz)
                dt_last = dt.combine(last_day + timedelta(days=1), dtime(0, 0), tzinfo=tz)
                logs = AttendanceLog.objects.filter(
                    staff=staff, timestamp__gte=dt_first, timestamp__lt=dt_last
                ).order_by("timestamp")

                if not logs.exists():
                    continue  # 勤怠なし → スキップ

                # --- Compute work durations as in views.py's _actual_work_durations_for_month ---
                per_day = defaultdict(lambda: {"normal": timedelta(),
                                               "special": timedelta(),
                                               "holiday": timedelta()})
                in_ts = None
                for log in logs:
                    act = (getattr(log, "action", "") or "").lower()
                    if act == "in":
                        in_ts = log.timestamp
                    elif act == "out" and in_ts:
                        i = in_ts.astimezone(tz)
                        o = log.timestamp.astimezone(tz)
                        dur = o - i
                        d = i.date()
                        # lunch overlap deduction (same-day)
                        L1 = dt.combine(d, lunch_start, tzinfo=tz)
                        L2 = dt.combine(d, lunch_end,   tzinfo=tz)
                        overlap = max(timedelta(), min(o, L2) - max(i, L1))
                        dur -= overlap
                        if dur < timedelta():
                            dur = timedelta()
                        # choose bucket
                        if is_special(d):
                            bucket = "special"
                        elif d.weekday() in weekly:
                            bucket = "holiday"
                        else:
                            bucket = "normal"
                        per_day[d][bucket] += dur
                        in_ts = None
                # apply flat 15min per workday, priority: normal→special→holiday
                FLAT = timedelta(minutes=15)
                total_normal  = timedelta()
                total_special = timedelta()
                total_holiday = timedelta()
                for d, parts in per_day.items():
                    remaining = FLAT
                    # normal
                    take = min(parts["normal"], remaining)
                    parts["normal"] -= take
                    remaining -= take
                    # special
                    if remaining > timedelta():
                        take = min(parts["special"], remaining)
                        parts["special"] -= take
                        remaining -= take
                    # holiday
                    if remaining > timedelta():
                        take = min(parts["holiday"], remaining)
                        parts["holiday"] -= take
                        remaining -= take
                    total_normal  += parts["normal"]
                    total_special += parts["special"]
                    total_holiday += parts["holiday"]
                # worked_days = number of days with any attendance
                worked_days = len(per_day)
                # worked_hours: sum all buckets (for backward compat)
                total_td = total_normal + total_special + total_holiday
                worked_hours = Decimal(total_td.total_seconds()) / Decimal(3600)

                # --- Categorized hours for payroll breakdown ---
                # For hourly: normal/special/holiday (0.25h rounding), for salary: just gross
                payroll, _ = MonthlyPayroll.objects.get_or_create(staff=staff, year_month=ym)
                gross = 0
                if getattr(staff, "wage_type", "") == getattr(Staff, "WageType", None) and getattr(Staff.WageType, "SALARY", None) and getattr(staff, "salary", None):
                    gross = fixed_salary_pay(
                        salary=staff.salary,
                        method=getattr(staff.payroll_info, "deduct_method", None),
                        worked_days=worked_days,
                        worked_hours=float(worked_hours),
                        target_date=today,
                    )
                    payroll.gross_pay = int(gross)
                    payroll.total_hours = total_td
                    payroll.normal_hours = total_td
                    payroll.special_hours = timedelta()
                    payroll.holiday_hours = timedelta()
                elif getattr(staff, "wage_type", "") == getattr(Staff, "WageType", None) and getattr(Staff.WageType, "HOURLY", None) and getattr(staff, "hourly_rate", None):
                    hr = Decimal(staff.hourly_rate or 0)
                    h_normal  = hours_qtr_decimal(total_normal)
                    h_special = hours_qtr_decimal(total_special)
                    h_holiday = hours_qtr_decimal(total_holiday)
                    gross = yen_floor(hr * h_normal) + yen_floor(hr * h_special * special_rate) + yen_floor(hr * h_holiday * special_rate)
                    payroll.gross_pay = int(gross)
                    payroll.total_hours = total_td
                    payroll.normal_hours = total_normal
                    payroll.special_hours = total_special
                    payroll.holiday_hours = total_holiday
                else:
                    # fallback: just set gross to 0, skip
                    payroll.gross_pay = 0
                    payroll.total_hours = total_td
                    payroll.normal_hours = total_td
                    payroll.special_hours = timedelta()
                    payroll.holiday_hours = timedelta()

                # Set other fields as before
                pi = getattr(staff, "payroll_info", None)
                payroll.commute_allowance = int(getattr(pi, "commute_allowance", 0) or 0)
                payroll.health_insurance = int(getattr(pi, "health_insurance", 0) or 0)
                payroll.pension = int(getattr(pi, "pension", 0) or 0)
                payroll.resident_tax = int(getattr(pi, "resident_tax", 0) or 0)
                payroll.withholding_tax = int(getattr(pi, "withholding_tax", 0) or 0)
                payroll.employment_insurance = int(getattr(pi, "employment_insured", False) and getattr(pi, "health_insurance", 0) or 0)
                payroll.save()

                self.stdout.write(f"✅ {staff.name} の給与データを更新しました。")
            except Exception as e:
                self.stderr.write(f"❌ {staff.name} の再計算中にエラー発生: {e}")

        self.stdout.write("🎉 当月の給与自動再計算が完了しました。")