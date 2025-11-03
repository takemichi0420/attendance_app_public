# attendance_app/forms.py
from __future__ import annotations

from django import forms
from django.forms import modelformset_factory

from .models import(
    Staff,
    AttendanceLog,
    )

from payroll.models import PayrollInfo

# ─────────────────────────────────────────────────────────────
# 1) スタッフ新規作成 / 単体編集用フォーム
# ─────────────────────────────────────────────────────────────
class StaffForm(forms.ModelForm):
    """スタッフ基本情報（氏名・賃金区分・時給/固定給）"""
    class Meta:
        model = Staff
        fields = ["name", "wage_type", "hourly_rate", "monthly_salary", "is_retired"]
        widgets = {
            "wage_type": forms.RadioSelect,
        }
        labels = {
            "name": "氏名",
            "wage_type": "賃金種別",
            "hourly_rate": "時給(¥/時)",
            "monthly_salary": "固定給(¥/月)",
            "is_retired": "退職済みにする",
        }

    def clean(self):
        cleaned = super().clean()
        wage = cleaned.get("wage_type")
        hourly = cleaned.get("hourly_rate")
        salary = cleaned.get("monthly_salary")
        if wage == Staff.WageType.HOURLY and not hourly:
            self.add_error("hourly_rate", "時給を入力してください。")
        if wage == Staff.WageType.SALARY and not salary:
            self.add_error("monthly_salary", "固定給を入力してください。")
        return cleaned


class PayrollInfoForm(forms.ModelForm):
    """控除・手当用フォーム"""
    class Meta:
        model = PayrollInfo
        fields = [
            "employment_insured",
            "resident_tax",
            "withholding_tax",
            "health_insurance",
            "pension",
            "commute_allowance",
        ]

# ─────────────────────────────────────────────────────────────
# 2) 一覧編集（設定ページなどで使う）用フォーム
#    * 1) と同じ項目を採用。必要なら分けて運用してOK。
# ─────────────────────────────────────────────────────────────
class StaffListEditForm(StaffForm):
    """settings_staff_profile.html の一覧編集など向けに流用"""
    pass


# ─────────────────────────────────────────────────────────────
# 3) 一覧編集用 FormSet
# ─────────────────────────────────────────────────────────────
StaffFormSet = modelformset_factory(
    Staff,
    form=StaffListEditForm,
    extra=0,
    can_delete=False,
)


# ─────────────────────────────────────────────────────────────
# 4) 出退勤ログの期間検索（そのまま維持）
# ─────────────────────────────────────────────────────────────
class LogSearchForm(forms.Form):
    date_from = forms.DateField(
        label="開始日",
        required=False,
        widget=forms.DateInput(attrs={"type": "date"}),
    )
    date_to = forms.DateField(
        label="終了日",
        required=False,
        widget=forms.DateInput(attrs={"type": "date"}),
    )


# ─────────────────────────────────────────────────────────────
# 打刻ログ編集
class AttendanceLogForm(forms.ModelForm):
    class Meta:
        model = AttendanceLog
        fields = ["timestamp", "action"]  # 必要なら "original_ts" も
        widgets = {
            "timestamp": forms.DateTimeInput(attrs={"class": "form-control", "type": "datetime-local"}),
            "action": forms.Select(attrs={"class": "form-select"}),
        }