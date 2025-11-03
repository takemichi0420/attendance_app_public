from django.urls import path
from payroll import views
from payroll.views import (
    PayrollListView,
    StaffMonthPayrollView,        # スタッフID +（任意）YYYYMM
    PayrollConfigView,
    StaffListView, StaffDeleteView,
    StaffCSVImportView, MonthlyPayrollCSVView,
    CloneStaffView,
    StaffMonthlyPayrollCSVView,
    MonthlyPayrollCSVView,
)

from django.views.generic import RedirectView

app_name = "payroll"


urlpatterns = [
    # 会社設定
    path("settings/", PayrollConfigView.as_view(), name="payroll_config"),

    # 月次 CSV
    path("export/<str:year_month>.csv", MonthlyPayrollCSVView.as_view(),
         name="payroll_export_csv"),

    # スタッフ CRUD・CSV
    path("staff/", StaffListView.as_view(), name="staff_list"),
    path("staff/create/", RedirectView.as_view(pattern_name="attendance:staff_create"), name="staff_create"),
    path("staff/<int:pk>/edit/", views.StaffEditView.as_view(), name="staff_edit"),
    path("staff/<int:pk>/delete/", StaffDeleteView.as_view(), name="staff_delete"),
    path("staff/csv-import/", StaffCSVImportView.as_view(), name="staff_csv_import"),
    path("staff/<int:pk>/clone/", CloneStaffView.as_view(), name="staff_clone"),
    path("staff/csv/", views.StaffListCSVView.as_view(), name="staff_export_csv"),
    #詳細ページ
    path("staff/<int:staff_id>/<str:year_month>/csv/",
        StaffMonthlyPayrollCSVView.as_view(),
        name="payroll_export_staff_csv"),
    # ---- 新しいスタッフIDルート（当月 or 指定月）----
    path("staff/<int:staff_id>/", StaffMonthPayrollView.as_view(),
         name="payroll_staff_detail"),                       # 当月
    path("staff/<int:staff_id>/<str:year_month>/", StaffMonthPayrollView.as_view(),
         name="payroll_staff_detail_month"),                 # 指定 YYYYMM

    # 月次一覧
    path("", PayrollListView.as_view(), name="payroll_list"),
]