from django.apps import AppConfig

class PayrollConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "payroll"

    def ready(self):
        # 定期実行は cron + management command で行うため、ここでは何もしない。
        pass