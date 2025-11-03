# ============================================================
# payroll/views.py  (Refactored & cleaned)
# ============================================================
from __future__ import annotations

import csv
import io
import re
import datetime as _dt
from dataclasses import dataclass
from decimal import Decimal, ROUND_DOWN, ROUND_HALF_UP
from functools import lru_cache
from typing import Any, Dict
from urllib.parse import quote

from django.contrib import messages
from django.contrib.admin.views.decorators import staff_member_required
from django.contrib.auth.mixins import LoginRequiredMixin
from django.http import Http404, HttpRequest, HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse, reverse_lazy
from django.utils import timezone
from django.utils.encoding import smart_str
from django.utils.decorators import method_decorator
from django.views import View
from django.views.generic import (
    CreateView,
    DeleteView,
    DetailView,
    FormView,
    ListView,
    UpdateView,
)

from payroll.choices import WageType
from django.db.models import Q
from payroll.forms import (
    CSVUploadForm,
    PayrollInfoForm,
    PayrollSettingForm,
    WageTypeSelectForm,
    HourlyStaffForm,
    SalaryStaffForm,
)
from attendance_app.forms import StaffForm
import payroll.utils as p_utils
from payroll.models import (
    MonthlyPayroll,
    PayrollInfo,
    PayrollSetting,
    Staff as PayrollStaff,  # Proxy to attendance_app.Staff
    StaffProfile,
)
from payroll.services import build_monthly_payroll, compute_work_durations

# --------------------------- 実働集計関数（StaffMonthPayrollViewと共通） ---------------------------

def _actual_work_durations_for_month(staff, yymm):
    """Payroll services の集計ロジックで実働を取得（締め日対応）。"""
    setting = _company_setting()
    try:
        # 締め日設定を確実に反映して集計
        from payroll.services import compute_work_durations
        durs = compute_work_durations(staff, ym=yymm, company=setting)
        return durs.normal, durs.special, durs.holiday
    except Exception as e:
        import logging
        logging.warning(f"Work duration aggregation failed: {e}")
        return _dt.timedelta(), _dt.timedelta(), _dt.timedelta()



# --------------------------- common helpers ---------------------------

def _yen_floor(x: Decimal | int | float) -> int:
    """金額は小数点以下切り捨て"""
    return int(Decimal(x).quantize(Decimal("1"), rounding=ROUND_DOWN))

def today_yymm() -> str:
    """JST基準の当月 YYYYMM。"""
    return timezone.localdate().strftime("%Y%m")

def normalize_yymm(value: str | None) -> str:
    """YYYYMM を返す。無効なら当月。"""
    return value if value and re.fullmatch(r"\d{6}", value) else today_yymm()

def _d(v: _dt.timedelta | None) -> _dt.timedelta:
    """timedelta None -> 0"""
    return v or _dt.timedelta()

def _hours(td: _dt.timedelta | None) -> Decimal:
    """timedelta -> 時間(Decimal)"""
    if not td:
        return Decimal("0")
    return Decimal(td.total_seconds()) / Decimal(3600)

@lru_cache(maxsize=1)
def _company_setting() -> PayrollSetting | None:
    """会社設定（存在しない場合もある想定でNone可）。"""
    try:
        return PayrollSetting.objects.first()
    except Exception:
        return None

def _amount_breakdown(payroll: MonthlyPayroll, staff: PayrollStaff) -> dict[str, int]:
    """
    金額内訳を dict で返す: {'normal': int, 'special': int, 'holiday': int}
      - 時給: 特別/休日は「時給 × 時間 × special_rate」
      - 固定給: normal = gross_pay、special = holiday = 0
    """
    if getattr(staff, "wage_type", "") != WageType.HOURLY:
        return {"normal": int(payroll.gross_pay or 0), "special": 0, "holiday": 0}

    hr = Decimal(staff.hourly_rate or 0)
    rate = Decimal(str(getattr(_company_setting(), "special_rate", 1) or 1))

    total = payroll.total_hours or _dt.timedelta()
    special = payroll.special_hours or _dt.timedelta()
    holiday = payroll.holiday_hours or _dt.timedelta()
    # Patch: adjust normal calculation for special/holiday zero case
    if (special.total_seconds() == 0 and holiday.total_seconds() == 0):
        normal = payroll.normal_hours or total
    else:
        normal = total - special - holiday

    h_normal  = _hours_qtr_decimal(normal)
    h_special = _hours_qtr_decimal(special)
    h_holiday = _hours_qtr_decimal(holiday)
    return {
        "normal":  _yen_floor(hr * h_normal),
        "special": _yen_floor(hr * h_special * rate),
        "holiday": _yen_floor(hr * h_holiday * rate),
    }


def _hours_qtr_decimal(td: _dt.timedelta | None) -> Decimal:
    """
    timedelta → 0.25h 単位に四捨五入した Decimal(時間)
    例) 8:45 -> 8.75, 8:37 -> 8.50
    """
    td = td or _dt.timedelta()
    h = Decimal(td.total_seconds()) / Decimal(3600)
    q = (h * Decimal(4)).quantize(Decimal("1"), rounding=ROUND_HALF_UP)
    return q / Decimal(4)

# --------------------------- mixins / snapshot ---------------------------

class SelectedYMMixin:
    """
    ?ym=YYYYMM（もしくは YYYY-MM）を受け取り、テンプレ共通の
    selected_ym / default_ym を付与する。
    """
    ym_param_name = "ym"
    _selected_ym: str | None = None

    def get_selected_ym(self) -> str:
        if self._selected_ym is not None:
            return self._selected_ym
        raw = self.request.GET.get(self.ym_param_name)
        # 'YYYY-MM' も許容
        resolved = normalize_yymm(raw.replace("-", "") if raw else raw)
        self._selected_ym = resolved
        return resolved

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx.setdefault("selected_ym", self.get_selected_ym())
        ctx.setdefault("default_ym", timezone.now().strftime("%Y-%m"))
        return ctx


@dataclass
class StaffMonthlySnapshot:
    """一覧/CSVのための1スタッフ1か月のスナップショット"""
    staff: PayrollStaff
    gross_pay: int
    employment_ins: int
    health_ins: int
    pension: int
    resident_tax: int
    withholding_tax: int
    commute_allowance: int
    net_pay: int
    social_ins_total: int
    # 時間・内訳は表示用途
    hours_total: str
    hours_normal: str
    hours_special: str
    hours_holiday: str
    amount_normal: int
    amount_special: int
    amount_holiday: int
    is_hourly: bool


def compute_staff_snapshot(staff: PayrollStaff, yymm: str) -> StaffMonthlySnapshot:
    """
    build_monthly_payroll を実行し、一覧/CSVで使う値をまとめて返す。
    ここを1箇所に集約することで、画面とCSVの数値齟齬を防止。
    """
    p = build_monthly_payroll(staff, yymm)
    # 控除の素は PayrollInfo だが、明細で最終金額が決まるものは p を優先
    gross = int(p.gross_pay or 0)
    ei    = int(p.employment_insurance or 0)
    hi    = int(p.health_insurance or 0)
    pe    = int(p.pension or 0)
    rt    = int(p.resident_tax or 0)
    wt    = int(p.withholding_tax or 0)
    ca    = int(p.commute_allowance or 0)
    net   = int(p.net_pay or 0)

    total   = _d(p.total_hours)
    special = _d(p.special_hours)
    holiday = _d(p.holiday_hours)
    normal  = total - special - holiday

    br = _amount_breakdown(p, staff)

    return StaffMonthlySnapshot(
        staff=staff,
        gross_pay=gross,
        employment_ins=ei,
        health_ins=hi,
        pension=pe,
        resident_tax=rt,
        withholding_tax=wt,
        commute_allowance=ca,
        net_pay=net,
        social_ins_total=hi + pe,
        hours_total=p_utils._h_str(total),
        hours_normal=p_utils._h_str(normal),
        hours_special=p_utils._h_str(special),
        hours_holiday=p_utils._h_str(holiday),
        amount_normal=int(br["normal"]),
        amount_special=int(br["special"]),
        amount_holiday=int(br["holiday"]),
        is_hourly=(staff.wage_type == WageType.HOURLY),
    )


# --------------------------- 1) 会社設定 ---------------------------

@method_decorator(staff_member_required, name="dispatch")
class PayrollConfigView(UpdateView):
    model = PayrollSetting
    form_class = PayrollSettingForm
    template_name = "payroll/payroll_config.html"
    success_url = reverse_lazy("payroll:payroll_config")

    def get_object(self, queryset=None):
        return PayrollSetting.objects.first()

    def form_valid(self, form):
        messages.success(self.request, "給与設定を保存しました。")
        # Clear the cached company setting so changes take effect immediately
        _company_setting.cache_clear()
        return super().form_valid(form)

    def form_invalid(self, form):
        messages.error(self.request, "保存に失敗しました。入力内容をご確認ください。")
        return super().form_invalid(form)
    
# --------------------------- 2) スタッフ CRUD/一覧 ---------------------------

class StaffListView(ListView):
    model = PayrollStaff
    template_name = "payroll/payroll_list.html"
    context_object_name = "staffs"

    def get_queryset(self):
        return (
            PayrollStaff.objects
            .select_related("payroll_info")
            .order_by("id")
        )

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)

        raw  = self.request.GET.get("ym")              # "YYYY-MM" or "YYYYMM" or None
        yymm = normalize_yymm(raw.replace("-", "") if raw else raw)

        staffs = list(self.object_list)                # ← Django の object_list をベースに

        for s in staffs:
            mp = build_monthly_payroll(s, yymm)

            # 実働ロジックに統一（StaffMonthPayrollViewと同じ集計）
            normal, special, holiday = _actual_work_durations_for_month(s, yymm)
            total = normal + special + holiday

            # 更新: mp の hours を最新値で上書きして、_amount_breakdown() に反映
            mp.normal_hours = normal
            mp.special_hours = special
            mp.holiday_hours = holiday

            # 手当内訳（切り捨て）: 最新の時間で計算
            br = _amount_breakdown(mp, s)
            s.basic_pay     = int(br["normal"])        # 基本給（時給×通常 or 固定給）
            s.allow_holiday = int(br["holiday"])       # 休日手当
            s.allow_special = int(br["special"])       # 特別期間手当

            s.commute_allowance = int(mp.commute_allowance or 0)

            # 総支給（= 基本給 + 休日 + 特別 + 通勤手当）※通勤手当は別列
            s.gross_pay          = s.basic_pay + s.allow_holiday + s.allow_special + s.commute_allowance

            # 控除・手当等（int化）
            s.health_ins         = int(mp.health_insurance or 0)
            s.pension            = int(mp.pension or 0)
            s.social_ins_total   = s.health_ins + s.pension
            s.resident_tax       = int(mp.resident_tax or 0)
            s.withholding_tax    = int(mp.withholding_tax or 0)
            s.employment_ins     = int(mp.employment_insurance or 0)
            s.commute_allowance  = int(mp.commute_allowance or 0)
            s.net_pay            = int(mp.net_pay or 0)

            # 自前計算（画面の総支給と整合）
            deductions = (
                s.employment_ins +
                s.health_ins +
                s.pension +
                s.resident_tax +
                s.withholding_tax
            )
            s.net_pay = s.gross_pay - deductions

            s.td_special = special
            s.td_holiday = holiday
            s.td_normal  = normal

            # 追加：0.25h 丸めを適用（明細と同じロジック）
            n_q = _hours_qtr_decimal(normal)
            s_q = _hours_qtr_decimal(special)
            h_q = _hours_qtr_decimal(holiday)


            # 表示用の文字列（小数2桁）
            s.hours_normal  = f"{n_q.quantize(Decimal('0.00'), rounding=ROUND_DOWN)}"
            s.hours_special = f"{s_q.quantize(Decimal('0.00'), rounding=ROUND_DOWN)}"
            s.hours_holiday = f"{h_q.quantize(Decimal('0.00'), rounding=ROUND_DOWN)}"

        # テンプレが staffs を回すのでここを必ず上書き
        ctx["staffs"] = staffs

        # 年月 UI 用
        ctx["selected_ym"] = yymm
        ctx["default_ym"]  = timezone.now().strftime("%Y-%m")
        ctx["month_input_value"] = f"{yymm[:4]}-{yymm[4:]}"
        return ctx


class StaffEditView(View):
    template_name = "payroll/staff_edit.html"

    def get(self, request, pk: int):
        staff = get_object_or_404(PayrollStaff, pk=pk)
        pi, _ = PayrollInfo.objects.get_or_create(staff=staff)
        return render(request, self.template_name, {
            "staff": staff,
            "form": StaffForm(instance=staff),
            "pi_form": PayrollInfoForm(instance=pi),
            "next": request.GET.get("next", ""),
        })

    def post(self, request, pk: int):
        staff = get_object_or_404(PayrollStaff, pk=pk)
        pi, _ = PayrollInfo.objects.get_or_create(staff=staff)
        form = StaffForm(request.POST, instance=staff)
        pi_form = PayrollInfoForm(request.POST, instance=pi)

        if form.is_valid() and pi_form.is_valid():
            staff = form.save()
            pi = pi_form.save()

            # ★ ユーザーが選んだ年月を取得
            raw_ym = request.POST.get("ym")
            yymm = normalize_yymm(raw_ym)

            # 選択された年月で再計算
            build_monthly_payroll(staff, yymm)

            messages.success(request, f"{yymm} の給与を再計算しました。")
            nxt = request.POST.get("next") or request.GET.get("next")
            return redirect(nxt or reverse("payroll:staff_list"))

        # バリデーション失敗時
        messages.error(request, "保存に失敗しました。入力内容をご確認ください。")
        return render(request, self.template_name, {
            "staff": staff,
            "form": form,
            "pi_form": pi_form,
            "next": request.POST.get("next", ""),
        })

@method_decorator(staff_member_required, name="dispatch")
class StaffDeleteView(DeleteView):
    model = PayrollStaff
    template_name = "payroll/staff_confirm_delete.html"
    success_url = reverse_lazy("payroll:staff_list")


class CloneStaffView(View):
    """指定スタッフの複製下書きフォーム"""
    def get(self, request, pk: int):
        original = get_object_or_404(PayrollStaff, pk=pk)
        draft = original.clone_values()
        form_cls = HourlyStaffForm if draft.wage_type == WageType.HOURLY else SalaryStaffForm
        tpl = "payroll/staff_create_hourly.html" if draft.wage_type == WageType.HOURLY else "payroll/staff_create_salary.html"
        return render(request, tpl, {"form": form_cls(instance=draft), "clone": True})


# --------------------------- 3) CSV import / export ---------------------------

class StaffCSVImportView(FormView):
    template_name = "payroll/csv_import.html"
    form_class = CSVUploadForm
    success_url = reverse_lazy("payroll:staff_list")

    def form_valid(self, form):
        text = form.cleaned_data["file"].read().decode("utf-8")
        reader = csv.DictReader(io.StringIO(text))
        for row in reader:
            PayrollStaff.objects.update_or_create(
                name=row["name"],
                defaults={
                    "wage_type": row.get("wage_type"),
                    "hourly_rate": row.get("hourly_rate") or None,
                    "monthly_salary": row.get("monthly_salary") or None,
                },
            )
        messages.success(self.request, "CSV 取り込み完了")
        return super().form_valid(form)


class MonthlyPayrollCSVView(View):
    header = [
        "ID", "氏名", "総支給", "雇保", "厚生年金", "健康保険",
        "住民税", "源泉", "通勤手当", "差引", "実働h", "内特別", "内休日",
    ]

    def get(self, request, year_month: str):
        qs = (MonthlyPayroll.objects
              .filter(year_month=year_month)
              .select_related("staff")
              .order_by("staff__name"))
        dt = _dt.datetime.strptime(year_month, "%Y%m")
        encoded = quote(smart_str(f"{dt.year}年{dt.month}月.csv"))
        resp = HttpResponse(content_type="text/csv")
        resp["Content-Disposition"] = f"attachment; filename*=UTF-8''{encoded}"
        w = csv.writer(resp)
        w.writerow(self.header)
        for p in qs:
            w.writerow([
                p.staff.id, p.staff.name, p.gross_pay, p.employment_insurance, p.pension,
                p.health_insurance, p.resident_tax, p.withholding_tax, p.commute_allowance,
                p.net_pay,
                round(_d(p.total_hours).total_seconds()/3600, 2),
                round(_d(p.special_hours).total_seconds()/3600, 2),
                round(_d(p.holiday_hours).total_seconds()/3600, 2),
            ])
        return resp


class StaffCreateDynamicView(FormView):
    """HTMX: 賃金種別ごとの項目差し替え"""
    template_name = "payroll/staff_create_dynamic.html"
    form_class = WageTypeSelectForm

    def post(self, request: HttpRequest, *args, **kwargs):
        wt = request.POST.get("wage_type")
        subform_cls = HourlyStaffForm if wt == WageType.HOURLY else SalaryStaffForm
        subform = subform_cls(request.POST)
        pi_form = PayrollInfoForm(request.POST)
        if subform.is_valid() and pi_form.is_valid():
            staff = subform.save(commit=False)
            staff.wage_type = wt
            staff.save()
            pi = pi_form.save(commit=False)
            pi.staff = staff
            pi.save()
            messages.success(request, "スタッフを登録しました")
            return redirect("payroll:staff_list")
        ctx = self.get_context_data(form=self.form_class(initial={"wage_type": wt}),
                                    subform=subform, pi_form=pi_form)
        return self.render_to_response(ctx)

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx.setdefault("subform", HourlyStaffForm())
        ctx.setdefault("pi_form", PayrollInfoForm())
        return ctx


class PartialSubForm(View):
    """HTMXの部分テンプレ：賃金種別ごとの項目だけ返す"""
    def get(self, request, *args, **kwargs):
        wt = request.GET.get("wage_type", WageType.HOURLY)
        form = HourlyStaffForm() if wt == WageType.HOURLY else SalaryStaffForm()
        tpl = "payroll/partials/fields_hourly.html" if wt == WageType.HOURLY else "payroll/partials/fields_salary.html"
        return render(request, tpl, {"form": form})


# --------------------------- 5) 月次一覧 / 詳細 ---------------------------

class PayrollListView(SelectedYMMixin, ListView):
    model = MonthlyPayroll
    template_name = "payroll/payroll_list.html"
    paginate_by = 50
    context_object_name = "payrolls"

    def get_queryset(self):
        ym = self.get_selected_ym()
        return (MonthlyPayroll.objects
                .filter(year_month=ym)
                .select_related("staff")
                .order_by("staff__name"))



class PayrollDetailView(DetailView):
    model = MonthlyPayroll
    template_name = "payroll/payroll_detail.html"
    context_object_name = "payroll"

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)

        original: MonthlyPayroll = self.object
        p: MonthlyPayroll = build_monthly_payroll(original.staff, original.year_month)

        # ============================================================
        # 勤務時間の集計ルール適用（丸め処理 or 実働）
        # ============================================================
        setting = _company_setting()
        rule = getattr(setting, "worktime_rule", "rounded") if setting else "rounded"

        if rule == "raw":
            try:
                normal_td, special_td, holiday_td = _actual_work_durations_for_month(p.staff, p.year_month)
                total_td = normal_td + special_td + holiday_td
            except Exception:
                total_td = _d(p.total_hours)
                special_td = _d(p.special_hours)
                holiday_td = _d(p.holiday_hours)
                normal_td = total_td - special_td - holiday_td
        else:
            total_td = _d(p.total_hours)
            special_td = _d(p.special_hours)
            holiday_td = _d(p.holiday_hours)
            normal_td = total_td - special_td - holiday_td

        normal_td_raw = normal_td
        special_td_raw = special_td
        holiday_td_raw = holiday_td
        total_td_raw = total_td

# For raw mode, keep the raw (unrounded) timedeltas for HH:MM display
        if rule == "raw":
            normal_td_raw_disp = normal_td_raw
            special_td_raw_disp = special_td_raw
            holiday_td_raw_disp = holiday_td_raw
            total_td_raw_disp = total_td_raw
        else:
            normal_td_raw_disp = normal_td
            special_td_raw_disp = special_td
            holiday_td_raw_disp = holiday_td
            total_td_raw_disp = total_td

        # 小数表示は常に四捨五入（0.25h単位）
        normal_hours_val = _hours_qtr_decimal(normal_td)
        special_hours_val = _hours_qtr_decimal(special_td)
        holiday_hours_val = _hours_qtr_decimal(holiday_td)
        total_hours_val = _hours_qtr_decimal(total_td)

        # (Removed debug_hours from context)

        # ============================================================
        # 以下、給与計算・控除等は従来通り
        # ============================================================
        # 総支給（一覧と同じ：各科目を円未満切り捨て→合算＋通勤）
        br = _amount_breakdown(p, p.staff)  # {'normal','special','holiday'}
        commute = int(p.commute_allowance or 0)
        gross_calc = (
            int(br["normal"])
            + int(br["special"])
            + int(br["holiday"])
            + commute
        )

        # 控除合計
        deductions = sum(int(x or 0) for x in (
            p.employment_insurance,
            p.health_insurance,
            p.pension,
            p.resident_tax,
            p.withholding_tax,
        ))
        net_calc = gross_calc - deductions

        # object 側へも同期（テンプレで payroll.gross_pay を見ても一致）
        # ※ model に @property net_pay があるならセットはしない
        try:
            p.gross_pay = gross_calc
        except Exception:
            pass

        ctx.update({
            "object": p,
            "payroll": p,
            "staff": p.staff,
            "ym": p.year_month,
            "year_month": p.year_month,

            # Display values for working hours (according to rule)
            "normal_hours": normal_hours_val,
            "special_hours": special_hours_val,
            "holiday_hours": holiday_hours_val,
            "total_hours": total_hours_val,
            "breakdown": br,
            "commute_allowance": commute,

            "gross_pay": gross_calc,
            "deductions_total": deductions,
            "net_pay": net_calc,
            # rounded_total_hours is now the display value depending on rule
            "rounded_total_hours": total_hours_val,
            "worktime_rule": rule,
        })

        # ============================================================
        # HH:MM形式の表示用文字列（丸め or 実働を反映）
        # ============================================================
        def td_to_hhmm(td):
            """timedelta → H:MM 表示（丸め or 実働で切り替え）"""
            if not td:
                return "0:00"
            # Always interpret as an actual timedelta (for raw mode, pass raw; for rounded, pass rounded)
            total_seconds = td.total_seconds()
            hours = int(total_seconds // 3600)
            minutes = int(round((total_seconds % 3600) / 60))
            return f"{hours}:{minutes:02d}"

        # For HH:MM, use raw values in raw mode, rounded in rounded mode
        ctx["normal_hours_hhmm"] = td_to_hhmm(normal_td_raw_disp)
        ctx["special_hours_hhmm"] = td_to_hhmm(special_td_raw_disp)
        ctx["holiday_hours_hhmm"] = td_to_hhmm(holiday_td_raw_disp)
        ctx["total_hours_hhmm"] = td_to_hhmm(total_td_raw_disp)

        return ctx        
    
class StaffPayrollDetailView(PayrollDetailView):
    """互換ラッパー（/payroll/<monthly_pk>/staff/<pk>/ の旧ルートに対応）。"""
    def get(self, request, *args, **kwargs):
        if "pk" not in kwargs:
            raise Http404("MonthlyPayroll pk is required")
        return super().get(request, *args, **kwargs)

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        original: MonthlyPayroll = self.object
        p: MonthlyPayroll = build_monthly_payroll(original.staff, original.year_month)

        ctx["object"] = p
        ctx["payroll"] = p
        ctx["staff"] = p.staff
        ctx["ym"] = p.year_month
        ctx["year_month"] = p.year_month

        # 画面用に“表示値”として渡すのは保存済みの値
        ctx["gross_pay"] = p.gross_pay
        ctx["commute_allowance"] = p.commute_allowance
        ctx["net_pay"] = p.net_pay
        return ctx
    

class StaffMonthPayrollView(LoginRequiredMixin, View):
    """
    /payroll/staff/<staff_id>/            … 当月
    /payroll/staff/<staff_id>/<YYYYMM>/   … パス指定
    さらに ?ym=YYYY-MM / YYYYMM で上書きできるようにする
    """
    template_name = "payroll/payroll_detail.html"

    def get(self, request, staff_id: int, year_month: str | None = None):
        raw = request.GET.get("ym")
        yymm = normalize_yymm(raw.replace("-", "")) if raw else normalize_yymm(year_month)

        staff_obj = get_object_or_404(PayrollStaff, pk=staff_id)
        StaffProfile.objects.get_or_create(staff=staff_obj)
        p = build_monthly_payroll(staff_obj, yymm)

        # 会社設定から丸め/実働ルールを取得
        company = _company_setting()
        rule = getattr(company, "worktime_rule", "rounded") if company else "rounded"
        is_actual = (rule == "raw")

        # 勤務時間の取得
        # 1) 実働（打刻ベース）を常に計算しておく（昼休憩控除＋日ごと15分控除込み）
        actual_normal_td, actual_special_td, actual_holiday_td = _actual_work_durations_for_month(staff_obj, yymm)
        actual_total_td = actual_normal_td + actual_special_td + actual_holiday_td

        # 2) 表示/計算に使う月合計はルールで切り替え
        if rule == "raw":
            normal_td = actual_normal_td
            special_td = actual_special_td
            holiday_td = actual_holiday_td
            total_td = actual_total_td
        else:
            # 丸めモード: 金額/小数のベースは既存の集計値（キャッシュ）
            total_td = _d(p.total_hours)
            special_td = _d(p.special_hours)
            holiday_td = _d(p.holiday_hours)
            normal_td = total_td - special_td - holiday_td

        # H:MM形式に変換
        def td_to_hhmm(td):
            if not td:
                return "0:00"
            total_seconds = td.total_seconds()
            hours = int(total_seconds // 3600)
            minutes = int(round((total_seconds % 3600) / 60))
            return f"{hours}:{minutes:02d}"

        # 総支給・控除・差引支給（区分別の td から直接内訳を作る：時給のみ）
        if getattr(staff_obj, "wage_type", "") == WageType.HOURLY:
            hr = Decimal(staff_obj.hourly_rate or 0)
            rate = Decimal(str(getattr(company, "special_rate", 1) or 1)) if company else Decimal("1")
            h_normal  = _hours_qtr_decimal(normal_td)
            h_special = _hours_qtr_decimal(special_td)
            h_holiday = _hours_qtr_decimal(holiday_td)
            br = {
                "normal":  _yen_floor(hr * h_normal),
                "special": _yen_floor(hr * h_special * rate),
                "holiday": _yen_floor(hr * h_holiday * rate),
            }
        else:
            # 固定給は従来どおり（gross を normal に立て、他は 0）
            br = _amount_breakdown(p, staff_obj)
        commute = int(p.commute_allowance or 0)
        gross_calc = sum(int(br[k]) for k in ["normal", "special", "holiday"]) + commute
        deductions = sum(int(x or 0) for x in (
            p.employment_insurance, p.health_insurance, p.pension,
            p.resident_tax, p.withholding_tax,
        ))
        net_calc = gross_calc - deductions

        # 時間(小数)は _hours_qtr_decimal() を使い、各区分ごとに
        ctx = {
            "payroll": p,
            "object": p,
            "staff": staff_obj,
            "ym": yymm,
            "year_month": yymm,
            "normal_hours": _hours_qtr_decimal(normal_td),
            "special_hours": _hours_qtr_decimal(special_td),
            "holiday_hours": _hours_qtr_decimal(holiday_td),
            "normal_hours_hhmm": td_to_hhmm(normal_td),
            "special_hours_hhmm": td_to_hhmm(special_td),
            "holiday_hours_hhmm": td_to_hhmm(holiday_td),
            "breakdown": br,
            "commute_allowance": commute,
            "gross_pay": gross_calc,
            "deductions_total": deductions,
            "net_pay": net_calc,
            "month_input_value": f"{yymm[:4]}-{yymm[4:]}",
            "default_ym": timezone.now().strftime("%Y-%m"),
            "is_actual_mode": is_actual,
            "worktime_rule": rule,
        }

        # 丸めモード用（右側HH:MM）は “実働” を 15分単位（0.25h）に四捨五入して表示
        rounded_normal_td  = _dt.timedelta(hours=float(_hours_qtr_decimal(actual_normal_td)))
        rounded_special_td = _dt.timedelta(hours=float(_hours_qtr_decimal(actual_special_td)))
        rounded_holiday_td = _dt.timedelta(hours=float(_hours_qtr_decimal(actual_holiday_td)))

        ctx["normal_hours_hhmm_rounded"] = td_to_hhmm(rounded_normal_td)
        ctx["special_hours_hhmm_rounded"] = td_to_hhmm(rounded_special_td)
        ctx["holiday_hours_hhmm_rounded"] = td_to_hhmm(rounded_holiday_td)
        ctx["normal_hours_hhmm_actual"] = td_to_hhmm(normal_td)
        ctx["special_hours_hhmm_actual"] = td_to_hhmm(special_td)
        ctx["holiday_hours_hhmm_actual"] = td_to_hhmm(holiday_td)
        return render(request, self.template_name, ctx)

# --------------------------- CSV ---------------------------

class StaffListCSVView(View):
    """給与スタッフ一覧CSV（画面と同一ロジック/順番）"""
    def get(self, request):
        raw = request.GET.get("ym")
        ym  = normalize_yymm(raw.replace("-", "") if raw else raw)
        qs  = PayrollStaff.objects.select_related("payroll_info").order_by("id")

        resp = HttpResponse(content_type="text/csv; charset=utf-8")
        resp["Content-Disposition"] = (
            f"attachment; filename*=UTF-8''{quote(smart_str('給与スタッフ一覧.csv'))}"
        )
        w = csv.writer(resp)
        w.writerow([
            "ID","氏名","通常時間h","特別時間h","休日時間h",
            "基本給","休日手当","特別期間","通勤手当","総支給額",
            "厚生年金","健康保険","社会保険合計","雇用保険","住民税","源泉","差引支給額",
        ])

        for s in qs:
            mp = build_monthly_payroll(s, ym)

            # 実働時間（締め日反映）
            normal_td, special_td, holiday_td = _actual_work_durations_for_month(s, ym)
            mp.normal_hours = normal_td
            mp.special_hours = special_td
            mp.holiday_hours = holiday_td

            # 時間（小数2桁）
            normal_h = f"{_hours_qtr_decimal(normal_td):.2f}"
            special_h = f"{_hours_qtr_decimal(special_td):.2f}"
            holiday_h = f"{_hours_qtr_decimal(holiday_td):.2f}"

            # 金額計算（画面と同じ）
            br = _amount_breakdown(mp, s)
            basic = int(br["normal"])
            hol = int(br["holiday"])
            sp = int(br["special"])
            commute_amt = int(mp.commute_allowance or 0)
            gross = basic + hol + sp + commute_amt

            # 控除
            health = int(mp.health_insurance or 0)
            pension = int(mp.pension or 0)
            social = health + pension
            employment = int(mp.employment_insurance or 0)
            resident = int(mp.resident_tax or 0)
            withhold = int(mp.withholding_tax or 0)
            net = gross - (employment + health + pension + resident + withhold)

            # CSV出力
            w.writerow([
                s.id, f"{s.name}",
                normal_h, special_h, holiday_h,
                basic, hol, sp, commute_amt, gross,
                pension, health, social, employment, resident, withhold, net,
            ])
        return resp
#　ーーーーーーーーーーーーーーーーーーーーーーーーーーーーーーーーーーーーーーーーーー 
class StaffMonthlyPayrollCSVView(View):
    """
    詳細ページ用 CSV（スタッフ×年月）
    画面と同じ“区分表”→“支給・控除”の二段構成で出力
    """
    def get(self, request, staff_id: int, year_month: str):
        ym = normalize_yymm(year_month)
        staff = get_object_or_404(PayrollStaff, pk=staff_id)

        # 最新値で計算（画面と同じ関数を使用）
        p = build_monthly_payroll(staff, ym)

        # 実働時間を最新の締め日設定で再計算（明細ページと同様に）
        normal_td, special_td, holiday_td = _actual_work_durations_for_month(staff, ym)
        p.normal_hours = normal_td
        p.special_hours = special_td
        p.holiday_hours = holiday_td

        # 時間（表示用 0.00h, 小数2桁で表示: HTML明細と同じロジック）
        total   = normal_td + special_td + holiday_td
        special = special_td
        holiday = holiday_td
        normal  = normal_td
        h_normal  = f"{_hours_qtr_decimal(normal):.2f}"
        h_special = f"{_hours_qtr_decimal(special):.2f}"
        h_holiday = f"{_hours_qtr_decimal(holiday):.2f}"

        # 金額（画面と同じ切り捨てロジック）
        br = _amount_breakdown(p, staff)  # {'normal','special','holiday'} -> int
        basic   = int(br["normal"])
        sp      = int(br["special"])
        hol     = int(br["holiday"])
        commute = int(p.commute_allowance or 0)
        gross_calc = (
            int(br["normal"])
            + int(br["special"])
            + int(br["holiday"])
            + commute
        )
        gross = gross_calc

        employ  = int(p.employment_insurance or 0)
        health  = int(p.health_insurance or 0)
        pension = int(p.pension or 0)
        resident= int(p.resident_tax or 0)
        withhold= int(p.withholding_tax or 0)
        net     = gross - (employ + health + pension + resident + withhold)

        # ファイル名: 田中_2025年08月_明細.csv
        dt = _dt.datetime.strptime(ym, "%Y%m")
        filename = f"{staff.name}様_{dt.year}年{dt.month:02d}月_明細.csv"
        resp = HttpResponse(content_type="text/csv; charset=utf-8")
        resp["Content-Disposition"] = (
            f"attachment; filename*=UTF-8''{quote(smart_str(filename))}"
        )
        w = csv.writer(resp)

        # ヘッダ情報
        w.writerow(["氏名", f"{staff.name} 様"])
        w.writerow(["年月", f"{dt.year}-{dt.month:02d}"])
        w.writerow([])

        # --- 区分テーブル ---
        w.writerow(["区分", "時間 h", "金額 円"])
        w.writerow(["通常",     h_normal,  basic])
        w.writerow(["特別期間", h_special, sp])
        w.writerow(["休日出勤", h_holiday, hol])
        w.writerow([])

        # --- 支給・控除 ---
        w.writerow(["支給・控除", "", ""])
        w.writerow(["通勤手当",   "", commute])
        w.writerow(["総支給額",   "", gross])       # 太字に見えないが値は同じ
        w.writerow(["雇用保険",   "", employ])
        w.writerow(["健康保険",   "", health])
        w.writerow(["厚生年金",   "", pension])
        w.writerow(["住民税",     "", resident])
        w.writerow(["源泉徴収",   "", withhold])
        w.writerow(["差引支給額", "", net])        # 画面と一致

        return resp