from django.apps import AppConfig


class AttendanceAppConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'attendance_app'

# シグナルモジュールをインポートして登録
    def ready(self):
        import attendance_app.signals  # noqa