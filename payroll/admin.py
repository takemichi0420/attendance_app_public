from django.contrib import admin
from .models import (
PayrollSetting,
SpecialPeriod,
MonthlyPayroll,
StaffProfile, Staff,
PayrollInfo
)
from django.utils.safestring import mark_safe


@admin.register(PayrollSetting)
class PayrollSettingAdmin(admin.ModelAdmin):
    def has_add_permission(self, request):
        # レコードは 1 件だけ
        return not PayrollSetting.objects.exists()

admin.site.register(SpecialPeriod)


# ① StaffProfileAdmin  ★QR 表示付き★
@admin.register(StaffProfile)
class StaffProfileAdmin(admin.ModelAdmin):
    list_display = (
        'staff_name',
        'staff_wage_type',
        'staff_hourly_rate',
        'staff_monthly_salary',
        'qr_image_tag',          # ← QR カラムも表示したいなら追加
    )
    list_select_related = ('staff',)

    def staff_name(self, obj):
        return obj.staff.name
    staff_name.short_description = '氏名'
    staff_name.admin_order_field = 'staff__name'

    def staff_wage_type(self, obj):
        return obj.staff.get_wage_type_display()
    staff_wage_type.short_description = '賃金種別'

    def staff_hourly_rate(self, obj):
        return obj.staff.hourly_rate
    staff_hourly_rate.short_description = '時給'

    def staff_monthly_salary(self, obj):
        return obj.staff.monthly_salary
    staff_monthly_salary.short_description = '月給'

    def qr_image_tag(self, obj):
        if obj.qr_image:
            return mark_safe(
                f'<a href="{obj.qr_image.url}" target="_blank">'
                f'<img src="{obj.qr_image.url}" width="80" alt="QR"></a>'
            )
        return '-'
    qr_image_tag.short_description = 'QR'


@admin.register(MonthlyPayroll)
class MonthlyPayrollAdmin(admin.ModelAdmin):
# ── 一覧に出す列 ──────────────────────────
    list_display = (
        "year_month",
        "staff_name",              # ← StaffProfile → Staff → name
        "gross_pay",
        "employment_insurance",    # 自動計算された金額を閲覧だけ
        "net_pay",
    )
    list_filter = ("year_month",)

    # ── 金額は編集させたくない ─────────────────
    readonly_fields = ("employment_insurance",)   # ← フォーム上は読み取り専用
    # 完全に非表示にしたいなら:
    # exclude = ("employment_insurance",)

    # N+1 回避
    list_select_related = ("staff",)

    # ── 氏名カラム用ヘルパー ─────────────────
    def staff_name(self, obj):
        return obj.staff.staff.name
    staff_name.short_description = "氏名"
    staff_name.admin_order_field = "staff__name"


@admin.register(PayrollInfo)
class PayrollInfoAdmin(admin.ModelAdmin):
    list_display = ("staff","employment_insured","resident_tax",
                    "withholding_tax","health_insurance","pension","commute_allowance")
    search_fields = ("staff__name",)