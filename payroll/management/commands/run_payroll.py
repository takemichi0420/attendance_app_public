from datetime import date, timedelta
from django.core.management.base import BaseCommand
from django.utils.timezone import now
from payroll.models import PayrollSetting
from payroll.services import build_month_pdf 
from django.core.mail import EmailMessage
from django.conf import settings
# import boto3  # ← S3 用

class Command(BaseCommand):
    help = "締め日に給与 PDF をメール送信（＋S3 保存 opcional）"

    def handle(self, *args, **kwargs):
        today = now().date()
        setting = PayrollSetting.objects.first()
        closing = setting.closing_day

        # ---- 締め日チェック ----
        last_day = (today.replace(day=1) + timedelta(days=32)).replace(day=1) - timedelta(days=1)
        is_closing = (
            today.day == closing or              # 通常
            (today == last_day and closing > last_day.day)  # 30日以下の月でも締め越えを吸収
        )
        if not is_closing:
            self.stdout.write("Today is not closing day → skip")
            return

        # ---- 対象年月の決定 ----
        target_month = today.replace(day=1) - timedelta(days=1)  # 前月末

        pdf_bytes = build_month_pdf(target_month.year, target_month.month)

        # ---- メール送信 ----
        subject = f"{target_month:%Y年%m月} 給与台帳"
        body    = f"{target_month:%Y/%m} 分の給与台帳をお送りします。"
        msg = EmailMessage(subject, body,
                           settings.DEFAULT_FROM_EMAIL,
                           [settings.ADMINS[0][1]])
        msg.attach(f"payroll_{target_month:%Y%m}.pdf",
                   pdf_bytes, "application/pdf")
        msg.send()
        self.stdout.write(self.style.SUCCESS("PDF mailed!"))

        # ---- S3 保存（必要ならコメント解除） ----
        # s3 = boto3.client('s3')
        # s3.put_object(
        #     Bucket='your-bucket',
        #     Key=f"payroll/payroll_{target_month:%Y%m}.pdf",
        #     Body=pdf_bytes,
        #     ContentType="application/pdf"
        # )