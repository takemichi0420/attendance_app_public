import datetime as _dt
from django.conf import settings
from django.utils import timezone
from payroll.models import PayrollSetting
from payroll.services import generate_monthly_payroll

def generate_monthly_if_closing_day() -> int:
    """
    締め日のときだけ、その年月の月次給与を一括再計算する。
    return: 作成/更新した件数
    """
    today = timezone.localdate()
    setting = PayrollSetting.objects.first()
    if not setting:
        return 0

    closing = int(setting.closing_day or 25)
    if today.day != closing:
        return 0

    ym = today.strftime("%Y%m")
    results = generate_monthly_payroll(ym)
    return len(results)