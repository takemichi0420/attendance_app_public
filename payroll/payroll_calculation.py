"""
payroll.payroll_calculation
===========================

固定給スタッフ向け「日割・時間割控除方式(A–H)」の計算ユーティリティ。

Public API
----------
- DeductMethod            : CharField 用の列挙値 + choices
- daily_or_hourly_unit()  : 月給を各方式に従って *単価* に変換
- fixed_salary_pay()      : 月給から控除後の支給額を算出
- wage_deduct_choices()   : Django `choices` 用ヘルパ
"""

from __future__ import annotations

import calendar as _cal
from dataclasses import dataclass
from datetime import date
from decimal import Decimal, ROUND_HALF_UP
from typing import Final, Iterable

import datetime as _dt
from django.utils import timezone as _tz

# Public symbols explicitly exported by this module (for clarity)
__all__ = [
    "DeductMethod",
    "daily_or_hourly_unit",
    "fixed_salary_pay",
    "wage_deduct_choices",
    "calc_daily_duration",
    "apply_special_rate",
]


# --------------------------------------------------------------------------- #
# 1. Enum-like constants (CharField 互換)
# --------------------------------------------------------------------------- #
class DeductMethod:
    """CharField 保存前提の“列挙値”コンテナ（互換性のため Enum は使わない）"""

    # A–H 方式
    DAY_CALENDAR:    Final[str] = "calendar"      # A) 暦日割
    DAY_FIXED30:     Final[str] = "fixed30"       # B) 30 日固定割
    DAY_WORKING:     Final[str] = "working"       # C) 所定労働日割
    HOUR_WORKING:    Final[str] = "working_hour"  # D) 所定労働時間割
    HOUR_AVERAGE:    Final[str] = "hourly_avg"    # E) 173.8h 平均割
    WEEKLY:          Final[str] = "weekly"        # F) 4.33 週割
    NOWORK_NOPAY:    Final[str] = "nowork"        # G) ノーワーク＝ノーペイ
    NO_DEDUCT:       Final[str] = "no_deduct"     # H) 控除なし

    # Django choices
    CHOICES: Final[tuple[tuple[str, str], ...]] = (
        (DAY_CALENDAR,  "暦日割 (A)"),
        (DAY_FIXED30,   "30 日固定割 (B)"),
        (DAY_WORKING,   "所定労働日割 (C)"),
        (HOUR_WORKING,  "所定労働時間割 (D)"),
        (HOUR_AVERAGE,  "173.8 時間割 (E)"),
        (WEEKLY,        "4.33 週割 (F)"),
        (NOWORK_NOPAY,  "欠勤控除 (G)"),
        (NO_DEDUCT,     "控除なし (H)"),
    )

    @classmethod
    def labels(cls) -> dict[str, str]:
        return dict(cls.CHOICES)


# --------------------------------------------------------------------------- #
# 2. Internal helpers
# --------------------------------------------------------------------------- #
def _to_decimal(value: int | float | Decimal) -> Decimal:
    """Decimal(2 位, 四捨五入) に正規化."""
    return Decimal(value).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


# --------------------------------------------------------------------------- #
# Break/Lunch deduction helpers (moved here for payroll calc integration)
# --------------------------------------------------------------------------- #
def _ensure_aware(dt: _dt.datetime) -> _dt.datetime:
    """
    Ensure a datetime is timezone-aware.
    - If naive, assume current Django timezone and make it aware.
    - If aware, return as-is.
    """
    if _tz.is_naive(dt):
        return _tz.make_aware(dt, _tz.get_current_timezone())
    return dt

LUNCH_START = _dt.time(12, 0)
LUNCH_END = _dt.time(13, 0)
REST_FIXED = _dt.timedelta(minutes=15)

def _overlap(start1, end1, start2, end2):
    """Return overlap timedelta between [start1,end1] and [start2,end2]."""
    latest_start = max(start1, start2)
    earliest_end = min(end1, end2)
    return max(earliest_end - latest_start, _dt.timedelta())

def calc_daily_duration(cin: _dt.datetime, cout: _dt.datetime) -> _dt.timedelta:
    """
    1日の実働:
      - 12:00〜13:00 を“重なった分だけ”控除
      - さらに常に 15 分控除
      - マイナスは 0 に丸め
    """
    # Accept both naive and aware datetimes:
    start = _tz.localtime(_ensure_aware(cin))
    end   = _tz.localtime(_ensure_aware(cout))
    if end <= start:
        return _dt.timedelta(0)

    dur = end - start

    # その日の12:00-13:00窓を作成（出勤が翌日跨ぎでも cin の日付を基準にする想定）
    noon_s = start.replace(hour=12, minute=0, second=0, microsecond=0)
    noon_e = start.replace(hour=13, minute=0, second=0, microsecond=0)

    dur -= _overlap(start, end, noon_s, noon_e)
    dur -= REST_FIXED

    if dur < _dt.timedelta(0):
        dur = _dt.timedelta(0)
    return dur


def _weekdays_in_month(year: int, month: int) -> int:
    """当月の平日(Mon–Fri)日数を返す."""
    _, last = _cal.monthrange(year, month)
    return sum(1 for d in range(1, last + 1) if date(year, month, d).weekday() < 5)


def _working_hours_in_month(year: int, month: int, daily_hours: int = 8) -> int:
    """平日を所定労働日とみなし、月間労働時間(暫定)を返す."""
    return _weekdays_in_month(year, month) * daily_hours


def _is_special_or_holiday(d: date, setting) -> bool:
    """日付が週休日 or 正月/盆/GW なら True"""
    wd = d.weekday()
    if wd in (setting.weekly_holidays or []):
        return True
    if setting.new_year_from and setting.new_year_to and setting.new_year_from <= d <= setting.new_year_to:
        return True
    if setting.bon_from and setting.bon_to and setting.bon_from <= d <= setting.bon_to:
        return True
    if setting.gw_from and setting.gw_to and setting.gw_from <= d <= setting.gw_to:
        return True
    return False


def apply_special_rate(hours: Decimal, d: date, setting, base_rate: Decimal = Decimal("1.35")) -> Decimal:
    """休日や特別期間なら倍率を掛ける"""
    if _is_special_or_holiday(d, setting):
        return hours * base_rate
    return hours


# --------------------------------------------------------------------------- #
# 3. Public helpers
# --------------------------------------------------------------------------- #
def daily_or_hourly_unit(
    *,
    salary: int | float | Decimal,
    method: str,
    target_date: date | None = None,
    calendar_days: int | None = None,
    working_days: int | None = None,
    working_hours: int | None = None,
    default_daily_hours: int = 8,
) -> Decimal:
    """
    月給 ⇒ 単価(日/時間) 変換。

    Parameters
    ----------
    salary : 月額固定給
    method : DeductMethod のいずれか
    target_date : 対象月の日付 (None なら今日)
    calendar_days / working_days / working_hours :
        分母を外部で確定済みなら与える。未指定なら自動計算。
    default_daily_hours : `working_hours` を自動算出する際の 1 日あたり時間
    """
    td = target_date or date.today()
    year, month = td.year, td.month

    # ── 分母計算 ───────────────────────────────────────────────
    calendar_days = calendar_days or _cal.monthrange(year, month)[1]
    working_days = working_days or _weekdays_in_month(year, month)
    working_hours = working_hours or working_days * default_daily_hours
    sal = _to_decimal(salary)

    # ── 方式ごとの分母マッピング ──────────────────────────────
    denominators: dict[str, Decimal] = {
        DeductMethod.DAY_CALENDAR:  Decimal(calendar_days),
        DeductMethod.DAY_FIXED30:   Decimal(30),
        DeductMethod.DAY_WORKING:   Decimal(working_days),
        DeductMethod.HOUR_WORKING:  Decimal(working_hours),
        DeductMethod.HOUR_AVERAGE:  Decimal("173.8"),
        DeductMethod.WEEKLY:        Decimal("4.33") * Decimal(5),  # 週単価→日単価に合わせる
    }

    if method in denominators:
        return sal / denominators[method]

    if method in (DeductMethod.NOWORK_NOPAY, DeductMethod.NO_DEDUCT):
        # 単価計算のみなので暦日割を採用
        return sal / Decimal(calendar_days)

    raise ValueError(f"Unsupported deduction method: {method}")


def fixed_salary_pay(
    *,
    salary: int | float | Decimal,
    method: str,
    worked_days: int | None = None,
    worked_hours: int | None = None,
    calendar_days: int | None = None,
    working_days: int | None = None,
    target_date: date | None = None,
) -> Decimal:
    """
    控除方式ごとの実支給額を返す。

    - G/H 方式の場合は `worked_days` / `worked_hours` を必ず渡す。
    """
    unit = daily_or_hourly_unit(
        salary=salary,
        method=method,
        target_date=target_date,
        calendar_days=calendar_days,
        working_days=working_days,
    )
    sal = _to_decimal(salary)

    # --- proportional methods ------------------------------------------
    proportional_by_days: tuple[str, ...] = (
        DeductMethod.DAY_CALENDAR,
        DeductMethod.DAY_FIXED30,
        DeductMethod.DAY_WORKING,
        DeductMethod.WEEKLY,
    )
    if method in proportional_by_days:
        return unit * Decimal(worked_days or 0)

    proportional_by_hours: tuple[str, ...] = (
        DeductMethod.HOUR_WORKING,
        DeductMethod.HOUR_AVERAGE,
    )
    if method in proportional_by_hours:
        return unit * Decimal(worked_hours or 0)

    # --- special cases --------------------------------------------------
    if method == DeductMethod.NOWORK_NOPAY:
        if worked_days is None:
            raise ValueError("worked_days must be provided for NOWORK_NOPAY method")
        cal_days = calendar_days or _cal.monthrange(date.today().year, date.today().month)[1]
        absent_days = cal_days - worked_days
        return sal - unit * Decimal(absent_days)

    if method == DeductMethod.NO_DEDUCT:
        return sal

    raise ValueError(f"Unsupported deduction method: {method}")


# --------------------------------------------------------------------------- #
# 4. Django Model / Form helper
# --------------------------------------------------------------------------- #
def wage_deduct_choices() -> Iterable[tuple[str, str]]:
    """`choices=` にそのまま渡せる tuple を返す（ラベル順ソート済）"""
    return tuple(sorted(DeductMethod.CHOICES, key=lambda x: x[1]))


# --------------------------------------------------------------------------- #
# Backward compatibility: ensure `calc_daily_duration` can be imported from
# `payroll.payroll_calculation` even if it lives in `payroll.utils`.
# If already defined in this module, this block is a no-op.
if "calc_daily_duration" not in globals():
    try:
        from .utils import calc_daily_duration  # type: ignore  # noqa:F401
    except Exception:
        # Silently ignore to avoid import-time failures; tests will catch this.
        pass
# --------------------------------------------------------------------------- #
# 5. Lightweight helper for standalone tests
# --------------------------------------------------------------------------- #
@dataclass(slots=True)
class StaffSnapshot:
    salary: Decimal
    deduct_method: str

    def pay(
        self,
        *,
        worked_days: int,
        worked_hours: int,
        calendar_days: int,
        working_days: int,
        target_date: date,
    ) -> Decimal:
        return fixed_salary_pay(
            salary=self.salary,
            method=self.deduct_method,
            worked_days=worked_days,
            worked_hours=worked_hours,
            calendar_days=calendar_days,
            working_days=working_days,
            target_date=target_date,
        )


# --------------------------------------------------------------------------- #
# 6. Quick CLI check
# --------------------------------------------------------------------------- #
if __name__ == "__main__":
    import pprint

    salary = 300_000
    today = date.today()
    cal_days = _cal.monthrange(today.year, today.month)[1]
    wk_days = _weekdays_in_month(today.year, today.month)
    worked_days, worked_hours = 20, 160

    results = {
        key: f"¥{fixed_salary_pay(salary=salary,
                                  method=key,
                                  worked_days=worked_days,
                                  worked_hours=worked_hours,
                                  calendar_days=cal_days,
                                  working_days=wk_days):,.2f}"
        for key, _ in DeductMethod.CHOICES
    }
    pprint.pp(results, compact=True)