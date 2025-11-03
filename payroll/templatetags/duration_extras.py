# payroll/templatetags/duration_extras.py
from __future__ import annotations

from datetime import timedelta
from decimal import Decimal, ROUND_DOWN, ROUND_HALF_UP
from typing import Any

from django import template

register = template.Library()

# ---------------------------------------------------------------------
# 基本ユーティリティ
# ---------------------------------------------------------------------
def _to_timedelta(value: Any) -> timedelta:
    """
    受け取った値をできるだけ timedelta に正規化する。
    - timedelta はそのまま
    - 数値(int/float/Decimal) は秒として解釈
    - "HH:MM" / "HH:MM:SS" 文字列も軽く対応
    - それ以外/パース失敗は 0
    """
    if isinstance(value, timedelta):
        return value
    if value is None:
        return timedelta(0)

    if isinstance(value, (int, float, Decimal)):
        return timedelta(seconds=float(value))

    if isinstance(value, str):
        try:
            parts = [int(p) for p in value.split(":")]
            if len(parts) == 2:
                h, m = parts
                s = 0
            else:
                h, m, s = (parts + [0, 0, 0])[:3]
            return timedelta(hours=h, minutes=m, seconds=s)
        except Exception:
            return timedelta(0)

    return timedelta(0)


def _hours(value: Any) -> Decimal:
    """timedelta 等を **時間(Decimal)** に変換。"""
    td = _to_timedelta(value)
    return Decimal(td.total_seconds()) / Decimal(3600)


# ---------------------------------------------------------------------
# フィルタ群
# ---------------------------------------------------------------------
@register.filter(name="hours2f")
def hours2f(value: Any) -> Decimal:
    """
    時間(小数)を **小数第3位以下切り捨て**（=2桁表記に相当）
    例: 12.3456h -> 12.34
    """
    return _hours(value).quantize(Decimal("0.01"), rounding=ROUND_DOWN)


@register.filter(name="hhmm")
def hhmm(value: Any) -> str:
    """
    'HH:MM' 表示（分は切り捨て）
    """
    td = _to_timedelta(value)
    total_min = int(td.total_seconds() // 60)
    h, m = divmod(total_min, 60)
    return f"{h}:{m:02d}"


@register.filter(name="hours_qtr")
def hours_qtr(value: Any) -> str:
    """
    **15分刻み(0.25h)で四捨五入**して 2桁文字列で返す（例: '8.75'）
    - 8:45 -> 8.75
    - 8:37:30 -> 8.63 ≒ 8.75（四捨五入）
    """
    h = _hours(value)
    quarters = (h * Decimal(4)).quantize(Decimal("1"), rounding=ROUND_HALF_UP)  # 0.25h単位へ
    rounded = quarters / Decimal(4)
    return f"{rounded.quantize(Decimal('0.00'))}"


# 後方互換: 既存テンプレートで `|duration:"h"` 等を使っていても動くように
@register.filter(name="duration")
def duration(value: Any, unit: str = "h"):
    """
    互換フィルタ:
      - 'h' : **小数第3位以下切り捨て**（=2桁相当）
      - 'm' : 分（整数, 切り捨て）
      - 's' : 秒（整数）
    """
    td = _to_timedelta(value)
    if unit == "m":
        return int(td.total_seconds() // 60)
    if unit == "s":
        return int(td.total_seconds())
    return _hours(td).quantize(Decimal("0.01"), rounding=ROUND_DOWN)