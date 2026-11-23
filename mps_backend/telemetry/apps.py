from django.apps import AppConfig
from django.db.backends.signals import connection_created


def _sqlite_wal(sender, connection, **kwargs):
    # WAL lets the web process read while the ingest process writes (SQLite only).
    if connection.vendor == "sqlite":
        connection.cursor().execute("PRAGMA journal_mode=WAL;")


class TelemetryConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "telemetry"

    def ready(self):
        connection_created.connect(_sqlite_wal)
