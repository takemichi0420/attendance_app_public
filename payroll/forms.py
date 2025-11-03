from django import forms
from django.core.exceptions import ValidationError
from .models import PayrollSetting
from .models import WEEKDAY_CHOICES
from .models import PayrollInfo
import datetime

class PayrollSettingForm(forms.ModelForm):
    """給与設定フォーム: 給与設定画面で使用されるフォーム"""

    weekly_holidays = forms.MultipleChoiceField(
        label="週休日設定",
        required=False,
        choices=WEEKDAY_CHOICES,
        widget=forms.CheckboxSelectMultiple
    )

    class Meta:
        model = PayrollSetting
        fields = [
            "closing_day",
            "special_rate",
            "weekly_holidays",
            "employment_ins_rate",
            "new_year_from", "new_year_to",
            "bon_from", "bon_to",
            "gw_from", "gw_to",
            "lunch_break_from", "lunch_break_to",
            "worktime_rule",
        ]
        labels = {
            "closing_day": "締日",
            "special_rate": "特別期間の時給倍率",
            "employment_ins_rate": "雇用保険率",
            "weekly_holidays": "週休日",
            "new_year_from": "正月開始日",
            "new_year_to": "正月終了日",
            "bon_from": "盆開始日",
            "bon_to": "盆終了日",
            "gw_from": "GW開始日",
            "gw_to": "GW終了日",
            "lunch_break_from": "昼休憩開始時刻",
            "lunch_break_to": "昼休憩終了時刻",
            "worktime_rule": "勤務時間の集計ルール",
        }
        widgets = {
            "closing_day": forms.NumberInput(attrs={"class": "form-control", "min": 1, "max": 31}),
            "special_rate": forms.NumberInput(attrs={"class": "form-control", "step": "0.01", "min": "1.0"}),
            "employment_ins_rate": forms.NumberInput(attrs={"class": "form-control", "step": "0.000001", "min": "0.0"}),
            "new_year_from": forms.DateInput(attrs={"class": "form-control", "type": "date"}),
            "new_year_to": forms.DateInput(attrs={"class": "form-control", "type": "date"}),
            "bon_from": forms.DateInput(attrs={"class": "form-control", "type": "date"}),
            "bon_to": forms.DateInput(attrs={"class": "form-control", "type": "date"}),
            "gw_from": forms.DateInput(attrs={"class": "form-control", "type": "date"}),
            "gw_to": forms.DateInput(attrs={"class": "form-control", "type": "date"}),
            "worktime_rule": forms.Select(attrs={"class": "form-select"}),
        }
        
class CSVUploadForm(forms.Form):
    """スタッフデータをCSVで一括取り込みするフォーム"""
    file = forms.FileField(
        label="CSVファイルを選択",
        widget=forms.ClearableFileInput(attrs={"class": "form-control"})
    )



# ─────────────────────────────────────────────────────────────
# PayrollInfoForm
# スタッフごとの控除・手当情報を入力するフォーム。
# Staff作成/編集時にセットで利用する。
# ─────────────────────────────────────────────────────────────
class PayrollInfoForm(forms.ModelForm):
    """
    スタッフごとの控除・手当情報を入力するフォーム。
    Staff作成/編集時にセットで利用する。
    """
    class Meta:
        model = PayrollInfo
        fields = [
             "employment_insured", # 雇用保険加入  
            "commute_allowance",   # 通勤手当
            "health_insurance",    # 健康保険
            "pension",             # 厚生年金
            "resident_tax",        # 住民税
            "withholding_tax",     # 源泉徴収
        ]
        widgets = {
            "employment_insured": forms.CheckboxInput(attrs={"class": "form-check-input"}),
            "commute_allowance": forms.NumberInput(attrs={"class": "form-control"}),
            "health_insurance": forms.NumberInput(attrs={"class": "form-control"}),
            "pension": forms.NumberInput(attrs={"class": "form-control"}),
            "resident_tax": forms.NumberInput(attrs={"class": "form-control"}),
            "withholding_tax": forms.NumberInput(attrs={"class": "form-control"}),
        }


# 追加フォーム
from attendance_app.models import Staff

# ────────────────────────────────
# 賃金区分を選択するフォーム
# ────────────────────────────────
class WageTypeSelectForm(forms.Form):
    wage_type = forms.ChoiceField(
        label="賃金区分",
        choices=Staff.WageType.choices,
        widget=forms.RadioSelect
    )

# ────────────────────────────────
# 時給スタッフ用フォーム
# ────────────────────────────────
class HourlyStaffForm(forms.ModelForm):
    class Meta:
        model = Staff
        fields = ["name", "wage_type", "hourly_rate", "commute_allowance"]
        widgets = {
            "name": forms.TextInput(attrs={"class": "form-control"}),
            "wage_type": forms.RadioSelect,
            "hourly_rate": forms.NumberInput(attrs={"class": "form-control"}),
            "commute_allowance": forms.NumberInput(attrs={"class": "form-control"}),
        }

# ────────────────────────────────
# 固定給スタッフ用フォーム
# ────────────────────────────────
class SalaryStaffForm(forms.ModelForm):
    class Meta:
        model = Staff
        fields = ["name", "wage_type", "monthly_salary", "commute_allowance"]
        widgets = {
            "name": forms.TextInput(attrs={"class": "form-control"}),
            "wage_type": forms.RadioSelect,
            "monthly_salary": forms.NumberInput(attrs={"class": "form-control"}),
            "commute_allowance": forms.NumberInput(attrs={"class": "form-control"}),
        }