"""attendance_app.urls – refactored for CBV 化 + Bootstrap5 対応

効率重視でルーティングを最小限に整理。
- 旧 warn_recent_action / qr_done / qr_top テンプレートは messages トーストに統合したので URL を削除
- register_action を POST 専用エンドポイントにまとめ、QR もボタンも共通
- namespaced URL で reverse() がシンプルに
"""

from django.urls import path

from . import views
from django.views.generic.base import RedirectView
from django.conf import settings
from django.conf.urls.static import static


app_name = "attendance"

urlpatterns: list[path] = [
   # QR フロー
    path("", views.qr_top, name="qr_top"),                                 # 【1】トップ（QR読み取り待ち）
    path("qr/check/", views.qr_checkin, name="qr_checkin"),                   # 【2】チェックイン判定
    path("qr/warn/", views.warn_recent_action, name="warn_recent_action"),    # 【3】重複警告（続行 or 中止）
    path("qr/done/", views.qr_done, name="qr_done"),                          # 【5】結果表示（取消ボタンあり）
    path("qr/cancel/", views.cancel_last, name="cancel_last"),                # 【6】取消処理

    # ダッシュボード（ログイン後のトップ）
    path("dashboard/", views.DashboardView.as_view(), name="dashboard"),

    # 打刻登録（出勤 / 退勤）
    path("register/", views.RegisterActionView.as_view(), name="register_action"),

    # スタッフ CRUD
    path("staff/", views.StaffListView.as_view(), name="staff_list"),
    path("staff/create/", views.StaffCreateView.as_view(), name="staff_create"),
    path("staff/retired/", views.RetiredStaffListView.as_view(), name="retired_staff_list"),
    path("staff/<int:pk>/rehire/", views.RehireStaffView.as_view(), name="rehire_staff"),

    # スタッフ勤怠ログ一覧
    path("staff/<int:pk>/logs/", views.StaffLogsView.as_view(), name="staff_logs"),
    path("log/<int:pk>/edit/", views.LogUpdateView.as_view(), name="log_edit"),
    path("log/<int:pk>/delete/", views.LogDeleteView.as_view(), name="log_delete"),


    # QR 画像詳細（ポップアップ用）
    path("staff/profile/<int:pk>/qr/", views.StaffQRDetailView.as_view(), name="staff_qr_detail"),
    path("staff/<int:pk>/qr.png", views.staff_qr_png, name="staff_qr_png"),

    # 認証
    path("login/", views.CustomLoginView.as_view(), name="login"),
    path("logout/", views.CustomLogoutView.as_view(), name="logout"),
    path("qr/clock/", views.qr_clock, name="qr_clock"),
]