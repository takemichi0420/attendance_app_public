# attendance_app/views.py

"""Refactored views.py
-------------------------------------------------------------------------------
- 【旧フロー】QR → 判定 → （1分以内なら警告）→ 登録 → 結果表示 → 取消
    qr_top / qr_checkin / warn_recent_action / qr_done / cancel_last
- 【新フロー】ログイン後ダッシュボード + ボタン打刻(RegisterActionView)
- スタッフ一覧（設定画面相当）：StaffListView
- ログイン/ログアウト：CustomLoginView / CustomLogoutView

ポリシー
- 直近1分以内は「出勤/退勤の種類に関係なく」連続打刻を禁止
- 管理者(superuser)は dashboard ではなく QR トップへ誘導
- QR 成功時はキオスク端末のログインをログアウト（画面描画が必要なページでは
  表示後にログアウト）
"""


import re, secrets, qrcode
from typing import Any, Optional

from django.contrib import messages
from django.contrib.auth import logout
from django.contrib.auth.views import LoginView, LogoutView
from django.contrib.auth.mixins import LoginRequiredMixin
from django.contrib.auth.decorators import login_required
from django.db import transaction
from django.http import (
    HttpRequest, HttpResponse, JsonResponse
)
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse_lazy
from django.utils import timezone
from django.views import View
from django.views.decorators.http import require_http_methods
from django.views.decorators.csrf import csrf_exempt
from django.views.generic import (
    TemplateView, ListView, CreateView, DetailView, UpdateView, DeleteView
)
from django.db import DatabaseError

from .forms import StaffForm, LogSearchForm, AttendanceLogForm, PayrollInfoForm
from .models import AttendanceLog, CancelLog, Staff, StaffProfile  
from .utils import now_jst
from io import BytesIO



class RetiredStaffListView(LoginRequiredMixin, ListView):
    model = Staff
    template_name = "retired_staff_list.html"
    context_object_name = "staffs"

    def get_queryset(self):
        return Staff.objects.filter(is_retired=True).order_by("id")


# 復職処理ビュー
class RehireStaffView(LoginRequiredMixin, View):
    """退職者の復職処理ビュー"""
    def post(self, request, pk):
        staff = get_object_or_404(Staff, pk=pk)
        if not staff.is_retired:
            messages.warning(request, f"{staff.name} さんはすでに在職中です。")
            return redirect("attendance:retired_staff_list")

        staff.rehire()
        messages.success(request, f"{staff.name} さんを復職扱いにしました。")
        return redirect("attendance:retired_staff_list")

__all__ = [
    # 新フロー
    "DashboardView",
    "RegisterActionView",
    "StaffListView",
    "CustomLoginView",
    "CustomLogoutView",
    # 旧QRフロー
    "qr_top",
    "qr_checkin",
    "warn_recent_action",
    "qr_done",
    "cancel_last",
    # API
    "qr_clock",
    # その他
    "StaffCreateView",
    "StaffLogsView",
    "StaffQRDetailView",
    "LogUpdateView",
    "LogDeleteView",
]


# =============================================================================
# 共通：日付ユーティリティ
# =============================================================================

def today_yymm() -> str:
    """JST 当月 YYYYMM。"""
    return timezone.localdate().strftime("%Y%m")


def normalize_yymm(value: str | None) -> str:
    """YYYYMM を返す。無効なら当月。"""
    return value if value and re.fullmatch(r"\d{6}", value) else today_yymm()


# =============================================================================
# 直近連続打刻の共通制御
# =============================================================================

# 直近 1 分以内の連続打刻を禁止
ACTION_INTERVAL_SEC: int = 60


def _last_punch(staff: Staff) -> AttendanceLog | None:
    """直近1件（出勤/退勤どちらでも）を返す。"""
    return (
        AttendanceLog.objects
        .filter(staff=staff)
        .only("action", "timestamp")
        .order_by("-timestamp")
        .first()
    )


def _within_interval_any(staff: Staff, sec: int = ACTION_INTERVAL_SEC) -> bool:
    """直近1件が sec 秒以内なら True（種類は無視）
    UTC（aware datetime）で比較する。JST変換は行わず、USE_TZ=True前提。
    """
    last = _last_punch(staff)
    if not last:
        return False

    now = timezone.now()  # aware(UTC)
    last_ts = last.timestamp  # aware(UTC)

    # 念のため aware を保証
    if timezone.is_naive(now):
        now = timezone.make_aware(now)
    if timezone.is_naive(last_ts):
        last_ts = timezone.make_aware(last_ts)

    diff = abs((now - last_ts).total_seconds())
    return diff < sec


def _create_punch(staff: Staff, action_type: str, request: HttpRequest | None = None) -> AttendanceLog:
    """打刻レコードを1行作成して返す（Idempotency-Key対応）"""

    from datetime import timezone as dt_timezone, timedelta

    JST = dt_timezone(timedelta(hours=9))
    jst_now = timezone.now().astimezone(JST)

    # Idempotency-Key handling
    key = None
    if request:
        key = request.headers.get("X-Idempotency-Key") or request.POST.get("key")
        if key and AttendanceLog.objects.filter(staff=staff, idempotency_key=key).exists():
            return AttendanceLog.objects.filter(staff=staff, idempotency_key=key).first()

    return AttendanceLog.objects.create(
        staff=staff,
        action=action_type,         # "in" or "out"
        original_ts=now_jst(),
        timestamp=timezone.now(),
        idempotency_key=key,
    )

# --- QR再生成ヘルパ -------------------------------------------------
def _regen_qr_for_profile(profile: StaffProfile) -> None:
    """QRトークンを再発行（画像生成メソッドがあれば呼ぶ）。"""
    profile.qr_token = secrets.token_urlsafe(16)
    # モデルに専用メソッドがある場合:
    for meth in ("build_qr", "generate_qr", "refresh_qr"):
        fn = getattr(profile, meth, None)
        if callable(fn):
            try:
                fn()  # 画像再生成など
            except Exception:
                pass
            break
    profile.save()

@login_required
def staff_qr_png(request, pk: int):
    """StaffProfile の現在のトークンから PNG を動的生成して返す。"""
    profile = get_object_or_404(StaffProfile, pk=pk)

    buf = BytesIO()
    # 必要に応じてエンコード内容を変更（URL等にしたい場合はここで組み立て）
    qrcode.make(profile.qr_token).save(buf, format="PNG")

    resp = HttpResponse(buf.getvalue(), content_type="image/png")
    resp["Cache-Control"] = "no-store"  # キャッシュ抑止（更新直後も確実に新画像）
    return resp

# =============================================================================
# KIOSK（QR端末）用：成功時の自動ログアウト
# =============================================================================

# 打刻後にこの端末のログインを落とすか（True: 常に落とす）
KIOSK_LOGOUT_AFTER_QR = True
# 「管理者だけ落としたい」場合は True（superuser のみログアウト）
KIOSK_LOGOUT_ADMIN_ONLY = False


def _logout_kiosk_user(request: HttpRequest) -> None:
    """
    QR 打刻成功時に、この端末のログイン中ユーザーをログアウト。
    kiosk 端末（受付PCなど）で管理者がログインしっぱなしにならないように。
    """
    if not KIOSK_LOGOUT_AFTER_QR:
        return
    u = getattr(request, "user", None)
    if not (u and u.is_authenticated):
        return
    if KIOSK_LOGOUT_ADMIN_ONLY and not u.is_superuser:
        return
    logout(request)


# =============================================================================
# 新フロー：ダッシュボード & ボタン打刻
# =============================================================================

class DashboardView(LoginRequiredMixin, TemplateView):
    """勤怠ダッシュボード（ログイン後のトップ）"""
    template_name = "dashboard.html"

    def dispatch(self, request, *args, **kwargs):
        # 管理者はダッシュボードではなく QR トップへ
        if getattr(request.user, "is_superuser", False):
            return redirect("attendance:qr_top")
        return super().dispatch(request, *args, **kwargs)

    def get_context_data(self, **kwargs: Any) -> dict[str, Any]:
        ctx = super().get_context_data(**kwargs)
        staff = getattr(self.request.user, "staff", None)
        ctx["staff"] = staff
        if staff:
            ctx["last_log"] = (
                AttendanceLog.objects.filter(staff=staff).order_by("-timestamp").first()
            )
            today = timezone.localdate()
            ctx["today_logs"] = (
                AttendanceLog.objects.filter(staff=staff, timestamp__date=today)
                .order_by("timestamp")
            )
        return ctx


class RegisterActionView(LoginRequiredMixin, View):
    """
    出勤／退勤を登録する共通ビュー（ボタン・API 兼用）。
    - staff_id が POST に含まれる場合 → 管理者の代理打刻
    - 含まれない場合 → ログイン中ユーザー自身の打刻
    - ★ 種別に関係なく直近1分以内の連続打刻は拒否
    """
    def post(self, request: HttpRequest, *args: Any, **kwargs: Any) -> HttpResponse:
        staff: Optional[Staff]
        staff_id = request.POST.get("staff_id")

        # --- スタッフ判定（代理打刻 or 本人） ------------------------------
        if staff_id:
            if not (request.user.is_superuser or request.user.has_perm("attendance.add_attendancelog")):
                messages.error(request, "代理打刻の権限がありません。")
                return redirect("attendance:dashboard")
            staff = get_object_or_404(Staff, pk=staff_id)
        else:
            staff = getattr(request.user, "staff", None)
            if staff is None:
                messages.error(request, "スタッフ情報が登録されていません。")
                return redirect("attendance:dashboard")

        # --- 種別判定 ------------------------------------------------------
        action_type = (request.POST.get("action") or "").lower()  # "in" / "out"
        if action_type not in {"in", "out"}:
            messages.error(request, "不正な打刻種別です。")
            return redirect("attendance:dashboard")

        # --- 直近1分以内の連続打刻は全面拒否 ------------------------------
        if _within_interval_any(staff):
            messages.warning(request, "直前の打刻から1分以内のため登録できません。")
            return redirect("attendance:dashboard")

        # --- 登録 ----------------------------------------------------------
        _create_punch(staff, action_type, request)
        messages.success(request, ("出勤" if action_type == "in" else "退勤") + "を登録しました。")
        return redirect("attendance:dashboard")


# =============================================================================
# 認証
# =============================================================================

class CustomLoginView(LoginView):
    template_name = "login.html"
    redirect_authenticated_user = True

    def get_success_url(self):
        # 管理者は QR トップへ/スタッフはダッシュボードへ
        if getattr(self.request.user, "is_superuser", False):
            return reverse_lazy("attendance:qr_top")
        return reverse_lazy("attendance:dashboard")


class CustomLogoutView(LogoutView):
    next_page = reverse_lazy("attendance:login")


# =============================================================================
# 旧フロー：QR → 判定 → (警告) → 登録 → 結果 → 取消
# =============================================================================

def qr_top(request: HttpRequest) -> HttpResponse:
    """【1】QR読み取り待ち"""
    return render(request, "qr_top.html")


@require_http_methods(["POST", "GET"])
def qr_checkin(request: HttpRequest) -> HttpResponse:
    """【2】QRチェックイン判定ビュー"""
    token = (request.POST.get("token") or request.GET.get("token") or "").strip()
    if not token:
        messages.error(request, "QRトークンが見つかりません。")
        return redirect("attendance:qr_top")

    try:
        profile = StaffProfile.objects.select_related("staff").get(qr_token=token)
        # まず DB 障害を優先    
    except DatabaseError:
        return render(
            request,
            "db_error.html",
            {"staff": None, "action": None},
            status=500,
        )
        # DBが正常でもスタッフが未登録の場合
    except StaffProfile.DoesNotExist:
        return render(
            request,
            "unregistered_staff.html",
            status=404)

    staff = profile.staff
    request.session["qr_staff_id"] = staff.id  # 後続（取消など）で使う

    # 次に押すべきアクション（見出し用）
    last_any = _last_punch(staff)
    next_action = "in" if (not last_any or last_any.action == "out") else "out"

    # ★ 種別に関係なく直近1分なら警告画面へ
    if _within_interval_any(staff):
        request.session["qr_next_action"] = next_action
        return render(
            request,
            "warn_recent_action.html",
            {
                "staff": staff,
                "action": next_action,
                "action_label": "出勤" if next_action == "in" else "退勤",
                "recent_log": last_any,
            },
        )

    # 問題なければ直接登録へ
    return _register_and_redirect_done(request, staff, next_action)


@require_http_methods(["POST"])
def warn_recent_action(request: HttpRequest) -> HttpResponse:
    """【3】警告画面（続行 or 中止）"""
    decision = request.POST.get("decision")  # "continue" or "cancel"
    staff_id = request.session.get("qr_staff_id")
    next_action = request.session.get("qr_next_action")

    if not staff_id or not next_action:
        messages.error(request, "セッションが切れました。最初からやり直してください。")
        return redirect("attendance:qr_top")

    staff = get_object_or_404(Staff, pk=staff_id)

    if decision == "cancel":
        messages.info(request, "打刻を中止しました。")
        return redirect("attendance:qr_top")

    # ★ “続行”しても、直近1分以内なら最終拒否
    if _within_interval_any(staff):
        messages.warning(request, "直前の打刻から1分以内のため登録できません。")
        return redirect("attendance:qr_top")

    return _register_and_redirect_done(request, staff, next_action)


def _register_and_redirect_done(
    request: HttpRequest, staff: Staff, action_type: str
) -> HttpResponse:
    
    # 【4】登録 → 【5】結果画面へ（障害時はエラーページへ）
    try:
        # raise DatabaseError("テスト用：手動で発生させたDBエラー")

        log = _create_punch(staff, action_type, request)
    
    except DatabaseError:
        return render(
            request,
            "db_error.html",
            {"staff": staff, "action": action_type},
            status=500,
        )


    request.session["qr_staff_id"] = staff.id
    request.session["last_log_id"] = log.id
    return redirect("attendance:qr_done")


def qr_done(request: HttpRequest) -> HttpResponse:
    """登録結果表示（このタイミングで端末ログアウト）"""
    staff_id = request.session.get("qr_staff_id")
    if not staff_id:
        messages.error(request, "セッションが切れました。最初からやり直してください。")
        return redirect("attendance:qr_top")

    staff = get_object_or_404(Staff, pk=staff_id)

    today = timezone.localdate()
    logs = (
        AttendanceLog.objects
        .filter(staff=staff, timestamp__date=today)
        .order_by("timestamp")
    )

    last_log_id = request.session.get("last_log_id")
    last_log = AttendanceLog.objects.filter(pk=last_log_id).first() if last_log_id else None
    action_label = last_log.get_action_display() if last_log else "打刻"
    action = last_log.action if last_log else None

    # 表示に必要な値を作った後に、端末ユーザーをログアウト
    _logout_kiosk_user(request)

    return render(
        request,
        "qr_done.html",
        {
            "staff": staff,
            "logs": logs,
            "last_log_id": last_log_id,
            "action": action,
            "action_label": action_label,
        },
    )


@require_http_methods(["POST"])
@transaction.atomic
def cancel_last(request: HttpRequest) -> HttpResponse:
    """【6】取消処理 → 【7】取消完了（結果再表示）"""
    staff_id = request.session.get("qr_staff_id")
    if not staff_id:
        messages.error(request, "セッションが切れました。最初からやり直してください。")
        return redirect("attendance:qr_top")

    staff = get_object_or_404(Staff, pk=staff_id)

    last_log = (
        AttendanceLog.objects.filter(staff=staff).order_by("-timestamp").first()
    )
    if not last_log:
        messages.warning(request, "取消対象の打刻がありません。")
        return redirect("attendance:qr_done")

    CancelLog.objects.create(
        staff=staff,
        canceled_log=last_log,
        canceled_at=timezone.now(),
    )
    last_log.delete()

    messages.success(request, "直前の打刻を取消しました。")
    return redirect("attendance:qr_done")


# =============================================================================
# スタッフ設定/ログ一覧/QR詳細/ログ編集・削除
# =============================================================================

class StaffListView(LoginRequiredMixin, ListView):
    """スタッフ一覧／設定画面。テンプレは settings_staff_profile.html を再利用。"""
    model = Staff
    template_name = "settings_staff_profile.html"
    context_object_name = "staffs"
    paginate_by = 50

    def get_queryset(self):
        qs = super().get_queryset().select_related("profile")
        qs = qs.filter(is_retired=False)
        q = self.request.GET.get("q")
        wage = self.request.GET.get("wage")
        if q:
            qs = qs.filter(name__icontains=q)
        if wage in {Staff.WageType.HOURLY, Staff.WageType.SALARY}:
            qs = qs.filter(wage_type=wage)
        return qs.order_by("id")

     # 一括操作（削除／QR再生成）を処理
    def post(self, request, *args, **kwargs):
        # 権限（必要に応じて調整）
        if not (request.user.is_superuser or request.user.has_perm("attendance.change_staff")):
            messages.error(request, "一括操作の権限がありません。")
            return redirect(request.META.get("HTTP_REFERER", request.path))

        action = (request.POST.get("bulk_action") or "").strip()
        # テンプレの name に合わせる（StaffProfile の pk が入ってくる）
        ids = [pk for pk in request.POST.getlist("selected_profiles") if pk]

        if not action or not ids:
            messages.warning(request, "操作と対象を選択してください。")
            return redirect(request.META.get("HTTP_REFERER", request.path))

        profiles = StaffProfile.objects.filter(pk__in=ids).select_related("staff")

        if action == "regen_qr":
            for p in profiles:
                _regen_qr_for_profile(p)
            messages.success(request, f"{profiles.count()}件のQRコードを再生成しました。")

        elif action == "delete":
            # profile 経由でスタッフを削除
            staffs = Staff.objects.filter(profile__in=profiles)
            count = staffs.count()
            staffs.delete()
            messages.success(request, f"{count}件のスタッフを削除しました。")

        else:
            messages.error(request, "不明な一括操作です。")

        # 元の一覧に戻す（フィルタ維持したければ HTTP_REFERER を使う）
        return redirect(request.META.get("HTTP_REFERER", request.path))


class StaffCreateView(LoginRequiredMixin, View):
    template_name = "staff_create.html"

    def get(self, request):
        return render(request, self.template_name, {
            "form": StaffForm(),
            "pi_form": PayrollInfoForm(),
        })

    def post(self, request):
        form = StaffForm(request.POST)
        pi_form = PayrollInfoForm(request.POST)
        if form.is_valid() and pi_form.is_valid():
            staff = form.save()
            profile, _ = StaffProfile.objects.get_or_create(staff=staff)  # QR発行
            pi = pi_form.save(commit=False)
            pi.staff = staff
            pi.save()
            messages.success(request, "スタッフを登録しました。")
            return redirect("attendance:staff_list")
        return render(request, self.template_name, {
            "form": form,
            "pi_form": pi_form,
        })

class StaffLogsView(LoginRequiredMixin, ListView):
    """
    スタッフの打刻ログ一覧。
    - /attendance/staff/<pk>/logs/
    - GET パラメータ date_from / date_to で期間フィルタ
    """
    model = AttendanceLog
    template_name = "staff_logs.html"
    context_object_name = "logs"
    paginate_by = 50

    def get_queryset(self):
        self.staff = get_object_or_404(Staff, pk=self.kwargs["pk"])
        qs = AttendanceLog.objects.filter(staff=self.staff).order_by("-timestamp")

        # 期間フィルタ（任意）
        self.search_form = LogSearchForm(self.request.GET or None)
        if self.search_form.is_valid():
            df = self.search_form.cleaned_data.get("date_from")
            dt = self.search_form.cleaned_data.get("date_to")
            if df:
                qs = qs.filter(timestamp__date__gte=df)
            if dt:
                qs = qs.filter(timestamp__date__lte=dt)

        return qs

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx["staff"] = self.staff
        ctx["form"] = getattr(self, "search_form", LogSearchForm())
        return ctx


class StaffQRDetailView(LoginRequiredMixin, DetailView):
    """QR 画像をポップアップで表示するだけの簡易ビュー。"""
    model = StaffProfile
    template_name = "staff_qr_detail.html"


class LogUpdateView(LoginRequiredMixin, UpdateView):
    model = AttendanceLog
    form_class = AttendanceLogForm
    template_name = "log_form.html"

    def get_success_url(self):
        # 編集後は該当スタッフのログ一覧へ戻す
        return reverse_lazy("attendance:staff_logs", kwargs={"pk": self.object.staff_id})


class LogDeleteView(LoginRequiredMixin, DeleteView):
    model = AttendanceLog

    def get_success_url(self):
        return reverse_lazy("attendance:staff_logs", kwargs={"pk": self.object.staff_id})


# =============================================================================
# QR スキャナ用 API エンドポイント
# =============================================================================

@csrf_exempt                      # KIOSK端末でCSRFクッキーを持てない場合に限り有効化。可能なら外す。
@require_http_methods(["POST"])
def qr_clock(request: HttpRequest) -> HttpResponse:
    """
    QRスキャナから叩く簡易API:
      - POST token=...&action=in|out
      - 成功時 JSON {"ok": true, "id": ...}
      - 直近重複は 409
      - 成功時はこの端末のログインをログアウト
    """
    token = (request.POST.get("token") or "").strip()
    action = (request.POST.get("action") or "in").lower()
    if action not in {"in", "out"}:
        action = "in"

    if not token:
        return JsonResponse({"ok": False, "msg": "missing token"}, status=400)

    profile = (
        StaffProfile.objects.select_related("staff").filter(qr_token=token).first()
    )
    if not profile:
        return JsonResponse({"ok": False, "msg": "invalid token"}, status=400)

    staff = profile.staff

    # ★ 種別に関係なく直近1分は 409
    if _within_interval_any(staff):
        return JsonResponse({"ok": False, "msg": "duplicate"}, status=409)

    log = _create_punch(staff, action, request)
    _logout_kiosk_user(request)

    return JsonResponse(
        {
            "ok": True,
            "id": log.id,
            "staff": staff.name,
            "action": action,
            "ts": timezone.localtime(log.timestamp).isoformat(),
        }
    )

