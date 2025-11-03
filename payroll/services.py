# payroll/services.py
"""
給与計算サービス

公開 API
--------
- build_monthly_payroll(staff, ym, ...)
    1人のスタッフについて YYYYMM の給与を集計・保存して返す
- generate_monthly_payroll(ym, *, staffs=None)
    指定月の全スタッフ（または渡された集合）を一括再計算

設計メモ
--------
- 勤怠は attendance_app.AttendanceLog（IN/OUT）を使って集計
- MonthlyPayroll.staff は attendance_app.Staff
- 休憩控除は utils.calc_daily_duration() に委譲
- 時間の丸めは「日単位」で 30 分未満切り捨て／30 分以上切り上げ（1 時間単位）
- 金額の丸めは四捨五入（1 円単位）
"""

from __future__ import annotations

import datetime as _dt
from dataclasses import dataclass
from decimal import Decimal, ROUND_HALF_UP, ROUND_DOWN
from typing import Iterable, Iterator, Tuple

from django.db import transaction
from django.utils import timezone

# 勤怠側
from attendance_app.models import AttendanceLog, Staff

# 給与側
from payroll.models import MonthlyPayroll, PayrollSetting, SpecialPeriod
from payroll.utils import calc_daily_duration


# =============================================================================
# 丸めポリシー
# =============================================================================


# 切り捨て円化
def _yen_floor(x) -> int:
    return int(Decimal(x).quantize(Decimal("1"), rounding=ROUND_DOWN))

def _hours(td: _dt.timedelta | None) -> Decimal:
    """timedelta → 時間(Decimal)。誤差回避のため Decimal で扱う。"""
    if not td:
        return Decimal("0")
    return (Decimal(td.total_seconds()) / Decimal(3600)).quantize(Decimal("0.0001"))


def _parse_ym(ym: str) -> Tuple[int, int]:
    """'YYYYMM' -> (year, month)"""
    if len(ym) != 6 or not ym.isdigit():
        raise ValueError(f"Invalid YYYYMM: {ym}")
    return int(ym[:4]), int(ym[4:])



def _ensure_aware(dt: _dt.datetime, tz) -> _dt.datetime:
    """datetime を指定タイムゾーンの aware に揃える。"""
    if timezone.is_naive(dt):
        return timezone.make_aware(dt, timezone=tz)
    return dt.astimezone(tz)


def _coerce_period_start(value: _dt.date | _dt.datetime, tz) -> _dt.datetime:
    """期間開始値を tz aware datetime (inclusive) に変換。"""
    if isinstance(value, _dt.datetime):
        return _ensure_aware(value, tz)
    if isinstance(value, _dt.date):
        return _ensure_aware(_dt.datetime.combine(value, _dt.time(0, 0)), tz)
    raise TypeError("first_day must be date or datetime")


def _coerce_period_end(value: _dt.date | _dt.datetime, tz) -> _dt.datetime:
    """期間終了値を tz aware datetime (exclusive) に変換。"""
    if isinstance(value, _dt.datetime):
        return _ensure_aware(value, tz)
    if isinstance(value, _dt.date):
        next_day = value + _dt.timedelta(days=1)
        return _ensure_aware(_dt.datetime.combine(next_day, _dt.time(0, 0)), tz)
    raise TypeError("last_day must be date or datetime")


def _closing_day(company: PayrollSetting | None) -> int:
    """PayrollSetting から締め日を取得（異常値は 31 として扱う）。"""
    raw = getattr(company, "closing_day", None)
    try:
        day = int(raw)
    except (TypeError, ValueError):
        return 31
    return min(max(day, 1), 31)


def _resolve_period(year: int, month: int, company: PayrollSetting | None, tz) -> tuple[_dt.datetime, _dt.datetime]:
    """締め日設定を考慮して集計期間 (inclusive, exclusive) を返す。"""
    day = _closing_day(company)
    if day >= 28:
        start = _ensure_aware(_dt.datetime(year, month, 1, 0, 0), tz)
        if month == 12:
            end = _ensure_aware(_dt.datetime(year + 1, 1, 1, 0, 0), tz)
        else:
            end = _ensure_aware(_dt.datetime(year, month + 1, 1, 0, 0), tz)
        return start, end

    prev_month = 12 if month == 1 else month - 1
    prev_year = year - 1 if month == 1 else year
    start = _ensure_aware(_dt.datetime(prev_year, prev_month, day + 1, 0, 0), tz)
    end = _ensure_aware(_dt.datetime(year, month, day + 1, 0, 0), tz)
    return start, end



def resolve_payroll_period(
    ym: str,
    *,
    company: PayrollSetting | None = None,
    tz=None,
) -> tuple[_dt.datetime, _dt.datetime]:
    """締め日設定を考慮した期間 (start, end) を返す。"""
    tz = tz or timezone.get_current_timezone()
    year, month = _parse_ym(ym)
    if company is None:
        company = PayrollSetting.objects.first()
    return _resolve_period(year, month, company, tz)


def compute_work_durations(
    staff: Staff,
    *,
    ym: str | None = None,
    start: _dt.datetime | _dt.date | None = None,
    end: _dt.datetime | _dt.date | None = None,
    company: PayrollSetting | None = None,
) -> WorkDurations:
    """指定期間の実働時間を PayrollSetting の締め日に沿って集計する。"""
    base_staff = getattr(staff, "staff", staff)
    tz = timezone.get_current_timezone()
    if company is None:
        company = PayrollSetting.objects.first()
    if start is None or end is None:
        if ym is None:
            raise ValueError("Either ym or both start/end must be provided.")
        year, month = _parse_ym(ym)
        start_dt, end_dt = _resolve_period(year, month, company, tz)
    else:
        start_dt = _coerce_period_start(start, tz)
        end_dt = _coerce_period_end(end, tz)
    return _aggregate_durations(base_staff, start_dt, end_dt, company)

def _as_date(v) -> _dt.date | None:
    """Date/DateTime/None を date に正規化。"""
    if v is None:
        return None
    if isinstance(v, _dt.datetime):
        return v.date()
    if isinstance(v, _dt.date):
        return v
    return None


# =============================================================================
# データ構造
# =============================================================================

@dataclass(frozen=True)
class WorkDurations:
    normal: _dt.timedelta
    special: _dt.timedelta
    holiday: _dt.timedelta

    @property
    def total(self) -> _dt.timedelta:
        return self.normal + self.special + self.holiday


@dataclass(frozen=True)
class PayBreakdown:
    # 支給内訳
    base: int          # 基本給（時給×通常時間 or 固定給）
    special: int       # 特別期間手当
    holiday: int       # 休日手当
    commute: int       # 通勤手当
    gross: int         # 総支給（= base + special + holiday + [commute]）

    # 控除
    employment_ins: int
    resident_tax: int
    withholding: int
    health: int
    pension: int

    # 差引
    net: int


# =============================================================================
# 週休日・特別期間
# =============================================================================

def _weekly_holidays(company: PayrollSetting | None) -> set[int]:
    """週休日（0=Mon … 6=Sun）を PayrollSetting から返す。"""
    if not company:
        return set()

    val = getattr(company, "weekly_holidays", None)
    if isinstance(val, (list, tuple, set)):
        try:
            return {int(x) for x in val}
        except Exception:
            pass

    mapping = {
        "is_mon_holiday": 0, "is_tue_holiday": 1, "is_wed_holiday": 2,
        "is_thu_holiday": 3, "is_fri_holiday": 4, "is_sat_holiday": 5, "is_sun_holiday": 6,
    }
    return {wd for field, wd in mapping.items() if getattr(company, field, False)}


def _collect_special_ranges(first_day: _dt.date, last_day: _dt.date,
                            company: PayrollSetting | None) -> list[tuple[_dt.date, _dt.date]]:
    """当月に重なる「特別期間」の date range リストを返す。"""
    ranges: list[tuple[_dt.date, _dt.date]] = []

    for sp in SpecialPeriod.objects.filter(start__lte=last_day, end__gte=first_day):
        ranges.append((sp.start, sp.end))

    if company:
        for s_name, e_name in [("new_year_from", "new_year_to"), ("bon_from", "bon_to")]:
            s = _as_date(getattr(company, s_name, None))
            e = _as_date(getattr(company, e_name, None))
            if s and e:
                ranges.append((s, e))
    return ranges


# =============================================================================
# 勤怠 → 日別に集計してから四捨五入し、月合計へ
# =============================================================================


def _iter_inout_pairs(staff: Staff, start: _dt.datetime, end: _dt.datetime) -> Iterator[tuple[_dt.datetime, _dt.datetime]]:
    """期間内の AttendanceLog から (IN, OUT) ペアを返す（時系列）。OUT 無しは除外。"""
    cin: _dt.datetime | None = None
    qs = (AttendanceLog.objects
          .filter(staff=staff, timestamp__gte=start, timestamp__lt=end)
          .order_by("timestamp"))
    for lg in qs:
        if lg.action == AttendanceLog.Action.CHECK_IN:
            cin = lg.timestamp
        elif lg.action == AttendanceLog.Action.CHECK_OUT and cin:
            yield cin, lg.timestamp
            cin = None



def _aggregate_durations(staff: Staff, start: _dt.datetime, end: _dt.datetime,
                         company: PayrollSetting | None) -> WorkDurations:
    """
    IN/OUT を読み、日ごとの通常/特別/休日時間を合算。
    - 各 IN-OUT 区間から 12:00–13:00 の重複分を除外
    - その日の合計からさらに必ず 15 分を休憩として差し引く
    - 日ごとの控除 15 分は normal → special → holiday の順に割り当てて引く（下回らない）
    """
    period_start_date = start.date()
    if end <= start:
        period_end_date = period_start_date
    else:
        period_end_date = (end - _dt.timedelta(days=1)).date()
        if period_end_date < period_start_date:
            period_end_date = period_start_date

    special_ranges = _collect_special_ranges(period_start_date, period_end_date, company)
    w_holidays = _weekly_holidays(company)

    daily: dict[_dt.date, dict[str, _dt.timedelta]] = {}

    def _add(day: _dt.date, kind: str, td: _dt.timedelta):
        if td <= _dt.timedelta(0):
            return
        b = daily.setdefault(day, {"normal": _dt.timedelta(),
                                   "special": _dt.timedelta(),
                                   "holiday": _dt.timedelta()})
        b[kind] += td

    for cin, cout in _iter_inout_pairs(staff, start, end):
        lcin = timezone.localtime(cin)
        lcout = timezone.localtime(cout)
        day = lcin.date()
        wd = lcin.weekday()

        if any(s <= day <= e for s, e in special_ranges):
            kind = "special"
        elif wd in w_holidays:
            kind = "holiday"
        else:
            kind = "normal"

        lunch_start_time = getattr(company, "lunch_break_from", _dt.time(12, 0))
        lunch_end_time = getattr(company, "lunch_break_to", _dt.time(13, 0))
        lunch_start = lcin.replace(hour=lunch_start_time.hour, minute=lunch_start_time.minute, second=0, microsecond=0)
        lunch_end = lcin.replace(hour=lunch_end_time.hour, minute=lunch_end_time.minute, second=0, microsecond=0)

        dur = lcout - lcin
        overlap_start = max(lcin, lunch_start)
        overlap_end = min(lcout, lunch_end)
        if overlap_end > overlap_start:
            dur -= (overlap_end - overlap_start)

        if dur < _dt.timedelta(0):
            dur = _dt.timedelta(0)

        _add(day, kind, dur)

    for day, b in daily.items():
        remains = _dt.timedelta(minutes=15)
        for key in ("normal", "special", "holiday"):
            take = min(b[key], remains)
            b[key] -= take
            remains -= take
            if remains <= _dt.timedelta(0):
                break

    normal = sum((b["normal"] for b in daily.values()), _dt.timedelta())
    special = sum((b["special"] for b in daily.values()), _dt.timedelta())
    holiday = sum((b["holiday"] for b in daily.values()), _dt.timedelta())

    return WorkDurations(normal=normal, special=special, holiday=holiday)


# =============================================================================
# 金額計算
# =============================================================================


def build_monthly_payroll(
    staff: Staff,
    ym: str,
    *,
    first_day: _dt.datetime | _dt.date | None = None,
    last_day: _dt.datetime | _dt.date | None = None,
    company: PayrollSetting | None = None,
    include_commute_in_gross: bool = True,
) -> MonthlyPayroll:
    """1人の Staff の給与を集計して保存し、MonthlyPayroll を返す。"""

    # attendance_app.Staff を想定。Proxy 経由でも耐える
    staff = getattr(staff, "staff", staff)

    y, m = _parse_ym(ym)
    if company is None:
        company = PayrollSetting.objects.first()

    tz = timezone.get_current_timezone()
    if first_day is not None and last_day is not None:
        start_dt = _coerce_period_start(first_day, tz)
        end_dt = _coerce_period_end(last_day, tz)
    else:
        start_dt, end_dt = _resolve_period(y, m, company, tz)

    # ---- 勤務時間（通常/特別/休日）を月次で集計 ----
    durs = _aggregate_durations(staff, start_dt, end_dt, company)
    # ---- 月トータルの実働時間を集計・15分単位で四捨五入 ----
    def _round_qtr(hours: Decimal) -> Decimal:
        return (hours * 4).quantize(Decimal("0"), rounding=ROUND_HALF_UP) / 4

    # 各区分ごとに15分単位で四捨五入
    normal_hours = _round_qtr(_hours(durs.normal))
    special_hours = _round_qtr(_hours(durs.special))
    holiday_hours = _round_qtr(_hours(durs.holiday))
    total_hours = _round_qtr(_hours(durs.total))

    # ---- 支給額（科目ごとに円未満切り捨て）----
    special_rate = Decimal(str(getattr(company, "special_rate", 1) or 1))
    if staff.wage_type == "hourly":
        hr = Decimal(staff.hourly_rate or 0)
        base_i = _yen_floor(hr * total_hours)
        sp_i = 0
        hol_i = 0
    else:
        base_i = _yen_floor(Decimal(staff.monthly_salary or 0))
        sp_i = 0
        hol_i = 0

    info = getattr(staff, "payroll_info", None)
    commute_i = _yen_floor(getattr(info, "commute_allowance", 0) or 0)
    core_gross = base_i + sp_i + hol_i
    gross = core_gross + (commute_i if include_commute_in_gross else 0)

    # ---- 控除 ----
    employment_ins = 0
    if info and getattr(info, "employment_insured", False) and company:
        rate = Decimal(str(getattr(company, "employment_ins_rate", 0) or 0))
        employment_ins = _yen_floor(Decimal(gross) * rate)

    resident_tax = _yen_floor(getattr(info, "resident_tax", 0) or 0)
    withholding = _yen_floor(getattr(info, "withholding_tax", 0) or 0)
    health = _yen_floor(getattr(info, "health_insurance", 0) or 0)
    pension = _yen_floor(getattr(info, "pension", 0) or 0)

    net = gross - (employment_ins + health + pension + resident_tax + withholding)

    # ---- 保存 ----
    mp, _ = MonthlyPayroll.objects.update_or_create(
        staff=staff,
        year_month=ym,
        defaults={
            "total_hours":           _dt.timedelta(hours=float(total_hours)),
            "special_hours":         _dt.timedelta(hours=float(special_hours)),
            "holiday_hours":         _dt.timedelta(hours=float(holiday_hours)),
            "gross_pay":             gross,
            "commute_allowance":     commute_i,
            "employment_insurance":  employment_ins,
            "resident_tax":          resident_tax,
            "withholding_tax":       withholding,
            "health_insurance":      health,
            "pension":               pension,
        },
    )
    return mp



def generate_monthly_payroll(
    ym: str,
    *,
    staffs: Iterable[Staff] | None = None,
    include_commute_in_gross: bool = True,
) -> list[MonthlyPayroll]:
    """指定月の対象スタッフを一括再計算して保存。"""
    year, month = _parse_ym(ym)
    company = PayrollSetting.objects.first()
    tz = timezone.get_current_timezone()
    start_dt, end_dt = _resolve_period(year, month, company, tz)

    if staffs is None:
        staffs = Staff.objects.all()

    results: list[MonthlyPayroll] = []
    with transaction.atomic():
        for s in staffs:
            mp = build_monthly_payroll(
                s, ym,
                first_day=start_dt,
                last_day=end_dt,
                company=company,
                include_commute_in_gross=include_commute_in_gross,
            )
            results.append(mp)
    return results

