from django import template
from decimal import Decimal, ROUND_DOWN, ROUND_HALF_UP
from datetime import timedelta

register = template.Library()

@register.filter
def floor(value):
    try:
        return Decimal(value).quantize(Decimal("1"), rounding=ROUND_DOWN)
    except:
        return value


# 0.25 時間単位で四捨五入して表示するフィルター
@register.filter
def hours_qtr(value):
    """
    timedelta または Decimal を 0.25 時間単位で四捨五入して表示。
    例:
      timedelta(hours=2, minutes=10) → 2.25
      Decimal('7.62') → 7.62
    """
    if value is None:
        return Decimal("0.00")

    # すでに Decimal 型の場合（DB保存済みなど）
    if isinstance(value, Decimal):
        return value.quantize(Decimal("0.00"))

    # timedelta の場合
    if isinstance(value, timedelta):
        hours = Decimal(value.total_seconds()) / Decimal(3600)
        q = (hours * Decimal(4)).quantize(Decimal("1"), rounding=ROUND_HALF_UP)
        return (q / Decimal(4)).quantize(Decimal("0.00"))

    # それ以外は 0.00 扱い
    return Decimal("0.00")