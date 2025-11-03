import uuid
from datetime import date, datetime
from io import BytesIO
from typing import Final

from PIL import Image
from django.conf import settings
from django.core.files.base import ContentFile
from django.core.validators import MinValueValidator
from django.db import models
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from .utils import generate_qr_png, now_jst

# ---------------------------------------------------------------------------
# Staff
# ---------------------------------------------------------------------------

class Staff(models.Model):
    """従業員マスタ。"""

    class WageType(models.TextChoices):
        HOURLY = "hourly", _("時給")
        SALARY = "salary", _("固定給")

    WAGE_TYPE_CHOICES = WageType.choices
    name: str = models.CharField("氏名", max_length=50, unique=True)
    wage_type: str = models.CharField(
        "賃金種別", max_length=6, choices=WageType.choices, default=WageType.HOURLY
    )
    hourly_rate: int | None = models.PositiveIntegerField(
        "時給", blank=True, null=True, validators=[MinValueValidator(1)]
    )
    monthly_salary: int | None = models.PositiveIntegerField(
        "固定給", blank=True, null=True, validators=[MinValueValidator(1)]
    )

    # 控除項目
    commute_allowance: int = models.PositiveIntegerField("通勤手当", default=0)
    health_insurance: int = models.PositiveIntegerField("健康保険", default=0)
    pension: int = models.PositiveIntegerField("厚生年金", default=0)
    resident_tax: int = models.PositiveIntegerField("住民税", default=0)
    withholding_tax: int = models.PositiveIntegerField("源泉所得税", default=0)

    # 退職情報
    is_retired = models.BooleanField("退職済み", default=False)
    retired_date = models.DateField("退職日", blank=True, null=True)

    created_at: datetime = models.DateTimeField(auto_now_add=True)
    updated_at: datetime = models.DateTimeField(auto_now=True)

    def __str__(self) -> str:  # pragma: no cover
        return f"{self.pk:03d} | {self.name}"

    # ------------------------------------------------------------------
    # Validation
    # ------------------------------------------------------------------
    def clean(self) -> None:  # noqa: C901
        from django.core.exceptions import ValidationError

        if self.wage_type == self.WageType.HOURLY and not self.hourly_rate:
            raise ValidationError({"hourly_rate": "時給を入力してください。"})
        if self.wage_type == self.WageType.SALARY and not self.monthly_salary:
            raise ValidationError({"monthly_salary": "固定給を入力してください。"})

    # ------------------------------------------------------------------
    # Helper
    # ------------------------------------------------------------------
    @property
    def display_wage(self) -> str:
        if self.wage_type == self.WageType.HOURLY:
            return f"¥{self.hourly_rate:,}/h"
        return f"¥{self.monthly_salary:,}/月"

    def retire(self):
        """退職処理: フラグON + 退職日記録 + QRトークン再生成"""
        if not self.is_retired:
            self.is_retired = True
            self.retired_date = timezone.localdate()
            self.save(update_fields=["is_retired", "retired_date"])
            if hasattr(self, "profile"):
                self.profile.regenerate_qr()

    def rehire(self):
        """復職処理: フラグOFF + 退職日クリア + QRトークン再生成"""
        if self.is_retired:
            self.is_retired = False
            self.retired_date = None
            self.save(update_fields=["is_retired", "retired_date"])
            if hasattr(self, "profile"):
                self.profile.regenerate_qr()


# ---------------------------------------------------------------------------
# StaffProfile (QR)
# ---------------------------------------------------------------------------
class StaffProfile(models.Model):
    """QR トークン・画像を保持する OneToOne プロファイル。"""

    staff      = models.OneToOneField(Staff, on_delete=models.CASCADE, related_name="profile")
    qr_token   = models.CharField(max_length=32, unique=True, editable=False, db_index=True)
    qr_image   = models.ImageField(upload_to="staff_qr", blank=True, null=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    # ------------------------------------------------------------------ #
    # 自動発行: save() 時に token / 画像が無ければ生成
    # ------------------------------------------------------------------ #
    def save(self, *args, **kwargs):
        if not self.qr_token:
            self.qr_token = uuid.uuid4().hex

        if not self.qr_image:
            img: Image.Image = generate_qr_png(self.qr_token)
            buf = BytesIO()
            img.save(buf, format="PNG")
            fname = f"staff_qr_{self.staff.pk or 'tmp'}.png"
            self.qr_image.save(fname, ContentFile(buf.getvalue()), save=False)

        super().save(*args, **kwargs)

    # ------------------------------------------------------------------ #
    # 手動再生成したい時だけ呼ぶメソッド（管理画面のアクションなどで利用）
    # ------------------------------------------------------------------ #
    def regenerate_qr(self) -> None:
        """トークンを更新して QR を再生成して保存。"""
        self.qr_token = uuid.uuid4().hex
        img: Image.Image = generate_qr_png(self.qr_token)
        buf = BytesIO()
        img.save(buf, format="PNG")
        fname = f"staff_qr_{self.staff.pk}.png"
        self.qr_image.save(fname, ContentFile(buf.getvalue()), save=False)
        self.save(update_fields=["qr_token", "qr_image"])

    def __str__(self) -> str:  # pragma: no cover
        return f"Profile of {self.staff.name}"

# ---------------------------------------------------------------------------
# Attendance / Cancel Log
# ---------------------------------------------------------------------------
class AttendanceLogQuerySet(models.QuerySet):
    """日付フィルターのショートカットを提供するカスタム QuerySet"""

    def on(self, target_date: date) -> "AttendanceLogQuerySet":
        start = datetime.combine(target_date, datetime.min.time(), tzinfo=timezone.utc)
        end = start + timezone.timedelta(days=1)
        return self.filter(timestamp__range=(start, end))


class AttendanceLog(models.Model):
    """出勤 / 退勤 レコード"""

    class Action(models.TextChoices):
        CHECK_IN = "in", _("出勤")
        CHECK_OUT = "out", _("退勤")

    staff: Staff = models.ForeignKey(Staff, on_delete=models.CASCADE, related_name="logs")
    action: str = models.CharField(max_length=3, choices=Action.choices)
    timestamp: datetime = models.DateTimeField("サーバタイムスタンプ", default=timezone.now)
    # --- Idempotency-Key フィールド追加（重複防止用） ---
    idempotency_key = models.CharField(
        max_length=64,
        blank=True,
        null=True,
        db_index=True,
        help_text="重複防止用のIdempotencyキー"
    )
    original_ts: datetime = models.DateTimeField(
        "端末タイムスタンプ (JST)", default=timezone.now)
    objects = AttendanceLogQuerySet.as_manager()

    class Meta:
        ordering = ("-timestamp",)

    def __str__(self) -> str:  # pragma: no cover
        return f"{self.staff.name} {self.get_action_display()} @ {timezone.localtime(self.timestamp):%Y-%m-%d %H:%M:%S}"


class CancelLog(models.Model):
    """最後の打刻を取り消した履歴を残す。"""

    staff: Staff = models.ForeignKey(Staff, on_delete=models.CASCADE, related_name="cancel_logs", null=True, blank=True)
    canceled_log: AttendanceLog = models.ForeignKey(AttendanceLog, on_delete=models.CASCADE)
    canceled_at: datetime = models.DateTimeField(default=timezone.now)

    def __str__(self) -> str:  # pragma: no cover
        return f"Cancel {self.canceled_log_id} by {self.staff.name}"
