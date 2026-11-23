from django.contrib import admin

from .models import Alert, RejectedMessage, Satellite, Telemetry


@admin.register(Satellite)
class SatelliteAdmin(admin.ModelAdmin):
    list_display = ("sat_id", "state", "last_seen", "packets_received", "packets_lost")


@admin.register(Telemetry)
class TelemetryAdmin(admin.ModelAdmin):
    list_display = ("satellite", "sequence", "timestamp", "battery_v", "temp_c", "obc_status", "link_status")
    list_filter = ("satellite",)
    list_select_related = ("satellite",)
    date_hierarchy = "timestamp"
    show_full_result_count = False


@admin.register(Alert)
class AlertAdmin(admin.ModelAdmin):
    list_display = ("satellite", "kind", "severity", "opened_at", "resolved_at")
    list_filter = ("severity", "kind", "satellite")


@admin.register(RejectedMessage)
class RejectedMessageAdmin(admin.ModelAdmin):
    list_display = ("received_at", "topic", "reason")
