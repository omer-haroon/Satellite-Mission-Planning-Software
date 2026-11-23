from django.db import models
from django.utils import timezone


class Satellite(models.Model):
    """Fleet registry. A satellite exists from the first valid message that names it."""

    class State(models.TextChoices):
        UNKNOWN = "unknown"
        ONLINE = "online"
        OFFLINE = "offline"

    sat_id = models.CharField(max_length=32, unique=True)
    state = models.CharField(max_length=8, choices=State.choices, default=State.UNKNOWN)
    state_changed_at = models.DateTimeField(null=True, blank=True)
    first_seen = models.DateTimeField(auto_now_add=True)
    last_seen = models.DateTimeField(null=True, blank=True)  # when the last telemetry arrived
    application_id = models.PositiveIntegerField(null=True, blank=True)
    last_sequence = models.PositiveIntegerField(null=True, blank=True)
    packets_received = models.PositiveBigIntegerField(default=0)
    packets_lost = models.PositiveBigIntegerField(default=0)  # from sequence-number gaps
    sequence_resets = models.PositiveIntegerField(default=0)  # simulator restarts seen
    latest = models.ForeignKey(
        "Telemetry", null=True, blank=True, on_delete=models.SET_NULL, related_name="+"
    )

    class Meta:
        ordering = ["sat_id"]

    def __str__(self):
        return self.sat_id


class Telemetry(models.Model):
    """One decoded telemetry packet (one `mps/<sat>/telemetry/all` message = one row)."""

    satellite = models.ForeignKey(Satellite, on_delete=models.CASCADE, related_name="telemetry")
    timestamp = models.DateTimeField()  # satellite clock, from the payload
    received_at = models.DateTimeField()  # backend clock

    # CCSDS-style header
    sequence = models.PositiveIntegerField()
    version = models.PositiveIntegerField()
    packet_type = models.PositiveIntegerField()
    application_id = models.PositiveIntegerField()
    payload_length = models.PositiveIntegerField()
    size_bytes = models.PositiveIntegerField()

    # EPS
    battery_v = models.FloatField()
    battery_i = models.FloatField()
    temp_c = models.FloatField()
    # status subsystems
    obc_status = models.CharField(max_length=32)
    adcs_mode = models.CharField(max_length=32)
    link_status = models.CharField(max_length=32)
    # payload
    payload_on = models.BooleanField()
    sensor_value = models.FloatField()

    class Meta:
        indexes = [
            models.Index(fields=["satellite", "timestamp"], name="tel_sat_ts_idx"),
            models.Index(fields=["timestamp"], name="tel_ts_idx"),
        ]

    def __str__(self):
        return f"{self.satellite_id}#{self.sequence}@{self.timestamp:%H:%M:%S}"


class Alert(models.Model):
    """A limit violation. One row per *episode*: opened when the condition starts,
    resolved when it ends (not one row per packet)."""

    class Severity(models.TextChoices):
        WARNING = "warning"
        CRITICAL = "critical"

    satellite = models.ForeignKey(Satellite, on_delete=models.CASCADE, related_name="alerts")
    kind = models.CharField(max_length=32)
    severity = models.CharField(max_length=8, choices=Severity.choices)
    message = models.CharField(max_length=255)
    value = models.FloatField(null=True, blank=True)  # offending reading, if numeric
    telemetry = models.ForeignKey(
        Telemetry, null=True, blank=True, on_delete=models.SET_NULL, related_name="+"
    )
    opened_at = models.DateTimeField()  # satellite timestamp of the first bad packet
    resolved_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        indexes = [
            models.Index(fields=["satellite", "kind", "resolved_at"], name="alert_sat_kind_idx"),
            models.Index(fields=["opened_at"], name="alert_opened_idx"),
        ]

    @property
    def active(self):
        return self.resolved_at is None

    def __str__(self):
        return f"{self.satellite_id} {self.kind} ({'active' if self.active else 'resolved'})"


class RejectedMessage(models.Model):
    """Messages that failed validation, kept so 'why is my satellite missing?' is answerable.
    Not pruned automatically: keep the broker authenticated (see README) so it cannot be flooded."""

    topic = models.CharField(max_length=255)
    reason = models.CharField(max_length=255)
    payload_preview = models.TextField(blank=True)
    received_at = models.DateTimeField(default=timezone.now, db_index=True)
