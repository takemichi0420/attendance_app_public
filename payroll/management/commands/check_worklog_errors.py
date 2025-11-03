from django.core.management.base import BaseCommand
from django.utils import timezone
from django.core.mail import send_mail
from django.conf import settings
from django.db.models import F

from payroll.models import WorkLog


class Command(BaseCommand):
    """昨日分の勤怠ログをチェックし、エラーがあれば管理者へメールする"""

    help = "Detects missing clock‑outs and long (>12h) shifts, then emails ADMINS"

    def handle(self, *args, **options):
        today = timezone.localdate()
        target_date = today - timezone.timedelta(days=1)

        logs = WorkLog.objects.filter(clock_in__date=target_date)

        missing_out = logs.filter(clock_out__isnull=True)
        long_shift = logs.exclude(clock_out__isnull=True).filter(
            clock_out__gte=F('clock_in') + timezone.timedelta(hours=12)
        )

        if not missing_out.exists() and not long_shift.exists():
            self.stdout.write(self.style.SUCCESS("✓ No errors for {}".format(target_date)))
            return

        lines: list[str] = [f"【{target_date} 勤怠エラー一覧】\n"]
        if missing_out.exists():
            lines.append("\n■ 打刻漏れ（退勤なし）")
            lines.extend([f"  - {w.staff.name} | 出勤: {w.clock_in.strftime('%H:%M')}" for w in missing_out])

        if long_shift.exists():
            lines.append("\n■ 長時間勤務（12h 超）")
            for w in long_shift:
                dur = w.clock_out - w.clock_in
                hh = dur.total_seconds() / 3600
                lines.append(f"  - {w.staff.name} | {hh:.1f}h ({w.clock_in:%H:%M}–{w.clock_out:%H:%M})")

        body = "\n".join(lines)
        subject = f"[勤怠チェック] {target_date} のエラー検出"
        send_mail(subject, body, settings.DEFAULT_FROM_EMAIL, [a[1] for a in settings.ADMINS])
        self.stdout.write(self.style.WARNING("✉︎ Issues emailed to ADMINS"))