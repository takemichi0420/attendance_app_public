# attendance_app/admin.py
from django.contrib import admin
from django.utils.html import format_html

from .models import Staff, StaffProfile, AttendanceLog, CancelLog


@admin.register(Staff)
class StaffAdmin(admin.ModelAdmin):
    list_display = ("id", "name", "wage_type", "display_wage", "created_at")
    list_filter = ("wage_type",)
    search_fields = ("name",)
    ordering = ("id",)


@admin.register(StaffProfile)
class StaffProfileAdmin(admin.ModelAdmin):
    list_display = ("staff", "qr_token", "qr_image_preview")
    readonly_fields = ("qr_token", "qr_image")

    def qr_image_preview(self, obj):
        if obj.qr_image:
            return format_html('<img src="{}" width="80" />', obj.qr_image.url)
        return "-"
    qr_image_preview.short_description = "QR画像"


@admin.register(AttendanceLog)
class AttendanceLogAdmin(admin.ModelAdmin):
    list_display = ("id", "staff", "action", "original_ts", "timestamp")
    list_filter = ("action", "staff")
    search_fields = ("staff__name",)
    ordering = ("-timestamp",)


@admin.register(CancelLog)
class CancelLogAdmin(admin.ModelAdmin):
    list_display = ("id", "staff", "canceled_log", "canceled_at")
    search_fields = ("staff__name",)
    autocomplete_fields = ("staff", "canceled_log")
    ordering = ("-canceled_at",)