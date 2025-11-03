# your_app/templatetags/money.py
from decimal import Decimal, ROUND_DOWN
from django import template

register = template.Library()

@register.filter
def yen(value):
    """金額用: 小数点以下切り捨てで整数へ"""
    if value in (None, ""):
        return 0
    return int(Decimal(str(value)).quantize(Decimal("1"), rounding=ROUND_DOWN))

@register.filter
def yenfmt(value):
    """円表記: 切り捨て→カンマ区切り（¥は付けない）"""
    n = yen(value)
    return f"{n:,}"