# payroll/models.py  ──────────────────────────────────────────────────────────
# *Single-source* Staff 統合版（Attendance_App の Staff を共有）
# 給与固有の項目は PayrollInfo へ分離して責務を明確化しました。
from __future__ import annotations

import uuid
from datetime import date, datetime, time
from decimal import Decimal, ROUND_DOWN
from typing import List

from django.conf import settings
from django.core.validators import (
    MinValueValidator, MaxValueValidator, RegexValidator
)
from django.db import models
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

# 勤怠アプリ本体の Staff を再利用
from attendance_app.models import Staff as BaseStaff, StaffProfile as AttendanceProfile

from payroll.payroll_calculation import fixed_salary_pay, DeductMethod

# ─────────────────────────
# 休日曜日の定数
# ─────────────────────────
WEEKDAY_CHOICES: List[tuple[int, str]] = [
    (0, "月曜日"), (1, "火曜日"), (2, "水曜日"),
    (3, "木曜日"), (4, "金曜日"), (5, "土曜日"), (6, "日曜日"),
]

# ─────────────────────────
# 1. 会社全体の給与設定
# ─────────────────────────
class PayrollSetting(models.Model):
    closing_day = models.PositiveSmallIntegerField(
        default=25, validators=[MinValueValidator(1), MaxValueValidator(31)],
        help_text="月次締め日 (1–31)",
    )
    special_rate = models.DecimalField(
        max_digits=4, decimal_places=2, default=1.35, help_text="休日・特別期間の時給倍率"
    )
    employment_ins_rate = models.DecimalField(
        "雇用保険率", max_digits=7, decimal_places=6,
        default=Decimal("0.006"), help_text="例 0.006 = 0.6%"
    )

    # 勤務時間集計ルール
    WORKTIME_RULE_CHOICES = [
        ("rounded", "丸め処理あり"),
        ("raw", "実働そのまま"),
    ]
    worktime_rule = models.CharField(
        "勤務時間の集計ルール",
        max_length=16,
        choices=WORKTIME_RULE_CHOICES,
        default="rounded",
        help_text="勤務時間集計時のルールを選択してください。",
    )

    # 正月・盆・GW等と任意の休日
    # 正月
    new_year_from = models.DateField("正月開始日", null=True, blank=True)
    new_year_to   = models.DateField("正月終了日", null=True, blank=True)

    # お盆
    bon_from = models.DateField("盆開始日", null=True, blank=True)
    bon_to   = models.DateField("盆終了日", null=True, blank=True)

    # GW等
    gw_from       = models.DateField("GW開始日", null=True, blank=True)
    gw_to         = models.DateField("GW終了日", null=True, blank=True)
    # 休日
    weekly_holidays = models.JSONField(default=list, blank=True)

    lunch_break_from = models.TimeField("昼休憩開始時刻", default=time(12, 0))
    lunch_break_to = models.TimeField("昼休憩終了時刻", default=time(13, 0))

    def __str__(self) -> str:  # pragma: no cover
        return "給与設定"

# ─────────────────────────
# 2. 勤怠アプリの Staff を Proxy でそのまま利用
# ─────────────────────────
class Staff(BaseStaff):
    """給与アプリから参照する Proxy Staff"""
    class Meta:
        proxy = True
        app_label = "payroll"          # admin サイドバーを分けたい場合
        verbose_name = "Staff"
        verbose_name_plural = "Staffs"

# ─────────────────────────
# 3. Payroll 固有情報をまとめる One-to-One
# ─────────────────────────
class PayrollInfo(models.Model):
    staff = models.OneToOneField(
        Staff, on_delete=models.CASCADE, related_name="payroll_info"
    )

    deduct_method  = models.CharField(
        "控除方式", max_length=12, choices=DeductMethod.CHOICES,
        default=DeductMethod.NO_DEDUCT,
        blank=True,
        null=True,
    )
    employment_insured = models.BooleanField("雇用保険加入", default=False)
    resident_tax       = models.DecimalField("住民税(¥)", max_digits=8,
                                             decimal_places=0, null=True, blank=True)
    withholding_tax    = models.DecimalField("源泉所得税(¥)", max_digits=8,
                                             decimal_places=0, null=True, blank=True)
    health_insurance   = models.DecimalField("健康保険料(¥)", max_digits=8,
                                             decimal_places=0, null=True, blank=True)
    pension            = models.DecimalField("厚生年金(¥)", max_digits=8,
                                             decimal_places=0, null=True, blank=True)
    commute_allowance  = models.DecimalField("通勤手当(¥)", max_digits=7,
                                             decimal_places=0, null=True, blank=True)

    class Meta:
        verbose_name = "Payroll Info"
        verbose_name_plural = "Payroll Infos"

    def __str__(self) -> str:  # pragma: no cover
        return f"{self.staff.name} – payroll info"

# ─────────────────────────
# 4. StaffProfile（QR 用） – 勤怠のモデルを再利用
#    Proxy は不要、別 FK も不要なので薄いラッパーだけ置く
# ─────────────────────────
class StaffProfile(AttendanceProfile):
    class Meta:
        proxy = True
        app_label = "payroll"
        verbose_name = "Staff profile"
        verbose_name_plural = "Staff profiles"

# ─────────────────────────
# 5. 勤怠ログ & その他既存モデル
# ─────────────────────────
class WorkLog(models.Model):
    staff     = models.ForeignKey(StaffProfile, on_delete=models.CASCADE)
    clock_in  = models.DateTimeField()
    clock_out = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ("clock_in",)

    def duration(self):
        from payroll.utils import calc_daily_duration
        return calc_daily_duration(self.clock_in, self.clock_out) if self.clock_out else None


class SpecialPeriod(models.Model):
    name       = models.CharField(max_length=30, default="未設定")
    start      = models.DateField()
    end        = models.DateField()
    multiplier = models.DecimalField(max_digits=4, decimal_places=2, default=1.35)

    def __str__(self) -> str:  # pragma: no cover
        return f"{self.name} {self.start:%Y/%m/%d}–{self.end:%m/%d}"

# ─────────────────────────
# 6. 月次給与集計
# ─────────────────────────
class MonthlyPayrollQuerySet(models.QuerySet):
    def for_staff_month(self, profile_id: int, ym: str):
        return self.filter(staff_id=profile_id, year_month=ym)

class MonthlyPayroll(models.Model):
    staff = models.ForeignKey("attendance_app.Staff", on_delete=models.CASCADE)
    year_month = models.CharField(max_length=6, validators=[RegexValidator(r"^\d{6}$")])

    total_hours   = models.DurationField(default=timezone.timedelta)
    normal_hours = models.DurationField(default=timezone.timedelta)
    special_hours = models.DurationField(default=timezone.timedelta)
    holiday_hours = models.DurationField(default=timezone.timedelta)

    gross_pay         = models.PositiveIntegerField(default=0)
    commute_allowance = models.PositiveIntegerField(default=0)

    # deductions
    employment_insurance = models.PositiveIntegerField(default=0)
    resident_tax         = models.PositiveIntegerField(default=0)
    withholding_tax      = models.PositiveIntegerField(default=0)
    health_insurance     = models.PositiveIntegerField(default=0)
    pension              = models.PositiveIntegerField(default=0)

    objects = MonthlyPayrollQuerySet.as_manager()

    class Meta:
        unique_together = ("staff", "year_month")
        ordering = ("year_month", "staff_id")
        indexes = [models.Index(fields=["staff", "year_month"])]

    # convenience
    @property
    def social_insurance(self) -> int:
        return (self.health_insurance or 0) + (self.pension or 0)

    @property
    def net_pay(self) -> int:
        return self.gross_pay - (
            self.commute_allowance +
            self.employment_insurance +
            self.resident_tax +
            self.withholding_tax +
            self.social_insurance
        )

    def __str__(self) -> str:  # pragma: no cover
        return f"{self.year_month} {self.staff.name}"