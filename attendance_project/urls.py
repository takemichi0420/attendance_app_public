"""attendance_project/urls.py – Refactored
=================================================
- qr_top ビューを廃止し、トップは DashboardView へリダイレクト
- include() を名前空間付きで宣言し衝突を回避
- DEBUG 時のみ static() を追加（本番は Web サーバーに任せる）
- admin サイトを有効化
"""
from __future__ import annotations

from django.conf import settings
from django.conf.urls.static import static
from django.contrib import admin
from django.urls import include, path
from django.views.generic import RedirectView

urlpatterns: list[path] = [
    # ホーム → 勤怠ダッシュボードにリダイレクト
    path(
        "",
        RedirectView.as_view(pattern_name="attendance:qr_top", permanent=False),
        name="home",
    ),

    # 管理サイト
    path("admin/", admin.site.urls),

    # 勤怠アプリ
    path("attendance/", include(("attendance_app.urls", "attendance"), namespace="attendance")),

    # 給与アプリ
    path("payroll/", include(("payroll.urls", "payroll"), namespace="payroll")),
]

# 開発環境のみメディアファイルを Django が配信
if settings.DEBUG:
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)
