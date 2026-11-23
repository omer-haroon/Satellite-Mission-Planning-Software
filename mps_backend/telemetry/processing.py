"""The ingest pipeline: DFD steps 3-7 for one MQTT message.

    topic + payload -> validate -> register satellite -> check sequence
                    -> store row -> evaluate alerts -> update satellite -> push live

`Ingestor.handle_message` is the only entry point. It knows nothing about MQTT, so it
is tested without a broker. Run exactly ONE ingest process: sequence/alert state is
kept in memory per process (it is rebuilt from the database on start).
"""
from __future__ import annotations

import logging
from collections import Counter
from dataclasses import dataclass, field

from django.conf import settings
from django.db import transaction
from django.db.models import F
from django.utils import timezone

from . import broadcast, payloads, rules
from .models import Alert, RejectedMessage, Satellite, Telemetry
from .validation import ValidationError, parse_status, parse_telemetry, parse_topic

logger = logging.getLogger(__name__)


@dataclass
class _SatRuntime:
    pk: int
    last_seq: int | None = None
    open_alerts: dict = field(default_factory=dict)  # kind -> Alert pk


class Ingestor:
    def __init__(self, push=broadcast.push, temp_high_c=None, battery_low_v=None):
        self._push = push
        self._temp_high_c = settings.MPS_TEMP_HIGH_C if temp_high_c is None else temp_high_c
        self._battery_low_v = settings.MPS_BATTERY_LOW_V if battery_low_v is None else battery_low_v
        self._sats: dict[str, _SatRuntime] = {}
        self.stats = Counter()
        self._load_state()

    # ------------------------------------------------------------ start-up
    def _load_state(self):
        """Resume after a restart: continue sequence tracking and keep open alerts open."""
        for sat in Satellite.objects.all():
            self._sats[sat.sat_id] = _SatRuntime(pk=sat.pk, last_seq=sat.last_sequence)
        by_pk = {rt.pk: rt for rt in self._sats.values()}
        for alert in Alert.objects.filter(resolved_at__isnull=True):
            by_pk[alert.satellite_id].open_alerts[alert.kind] = alert.pk

    # ---------------------------------------------------------- entry point
    def handle_message(self, topic: str, payload: bytes, received_at=None) -> str:
        """Process one MQTT message. Returns: stored | duplicate | status | rejected."""
        received_at = received_at or timezone.now()
        try:
            sat_id, kind = parse_topic(topic)
            msg = (parse_status if kind == "status" else parse_telemetry)(sat_id, payload)
        except ValidationError as exc:
            self._reject(topic, payload, str(exc), received_at)
            return "rejected"
        if kind == "status":
            self._on_status(msg, received_at)
            return "status"
        return self._on_telemetry(msg, received_at)

    # ------------------------------------------------------------ registry
    def _runtime(self, sat_id: str) -> _SatRuntime:
        rt = self._sats.get(sat_id)
        if rt is None:
            sat, created = Satellite.objects.get_or_create(sat_id=sat_id)
            rt = self._sats[sat_id] = _SatRuntime(pk=sat.pk, last_seq=sat.last_sequence)
            if created:
                self.stats["satellites_discovered"] += 1
                logger.info("[registry] new satellite discovered: %s (fleet size %d)",
                            sat_id, len(self._sats))
        return rt

    # -------------------------------------------------------------- status
    def _on_status(self, msg, received_at):
        rt = self._runtime(msg.sat_id)
        changed = (Satellite.objects.filter(pk=rt.pk).exclude(state=msg.state)
                   .update(state=msg.state, state_changed_at=received_at))
        self.stats["status"] += 1
        if changed:
            logger.info("[status] %s is %s", msg.sat_id, msg.state.upper())
            self._push("status", payloads.status_payload(msg.sat_id, msg.state, received_at))

    # ----------------------------------------------------------- telemetry
    def _on_telemetry(self, msg, received_at) -> str:
        rt = self._runtime(msg.sat_id)
        verdict, lost = rules.classify_sequence(rt.last_seq, msg.sequence)
        if verdict == rules.DUPLICATE:
            self.stats["duplicates"] += 1
            return "duplicate"
        if verdict == rules.GAP:
            logger.warning("[seq] %s: GAP, %d packet(s) lost before seq %d", msg.sat_id, lost, msg.sequence)
        elif verdict == rules.RESTART:
            logger.info("[seq] %s: sequence reset to 0 (satellite restarted?)", msg.sat_id)
        elif verdict == rules.STALE:
            logger.warning("[seq] %s: out-of-order packet seq %d (last was %s)",
                           msg.sat_id, msg.sequence, rt.last_seq)

        open_alerts = dict(rt.open_alerts)  # work on a copy; commit to memory only after the DB commit
        with transaction.atomic():
            row = Telemetry.objects.create(
                satellite_id=rt.pk, timestamp=msg.timestamp, received_at=received_at,
                sequence=msg.sequence, version=msg.version, packet_type=msg.packet_type,
                application_id=msg.application_id, payload_length=msg.payload_length,
                size_bytes=msg.size_bytes, battery_v=msg.battery_v, battery_i=msg.battery_i,
                temp_c=msg.temp_c, obc_status=msg.obc_status, adcs_mode=msg.adcs_mode,
                link_status=msg.link_status, payload_on=msg.payload_on,
                sensor_value=msg.sensor_value,
            )
            events = self._update_alerts(rt.pk, msg, row, open_alerts)
            changes = {
                "last_seen": received_at,
                "application_id": msg.application_id,
                "latest": row,
                "packets_received": F("packets_received") + 1,
            }
            if verdict != rules.STALE:
                changes["last_sequence"] = msg.sequence
            if lost:
                changes["packets_lost"] = F("packets_lost") + lost
            if verdict == rules.RESTART:
                changes["sequence_resets"] = F("sequence_resets") + 1
            Satellite.objects.filter(pk=rt.pk).update(**changes)

        # committed: now update in-memory state and push to live clients
        rt.open_alerts = open_alerts
        if verdict != rules.STALE:
            rt.last_seq = msg.sequence
        self.stats["stored"] += 1
        self._push("telemetry", payloads.telemetry_payload(row, msg.sat_id))
        for event_type, data in events:
            self._push(event_type, data)
        return "stored"

    def _update_alerts(self, sat_pk, msg, row, open_alerts):
        """Open an Alert when a condition starts, resolve it when it ends. Mutates `open_alerts`."""
        active = {c.kind: c for c in rules.evaluate(msg, self._temp_high_c, self._battery_low_v)}
        events = []
        for kind, cond in active.items():
            if kind not in open_alerts:
                alert = Alert.objects.create(
                    satellite_id=sat_pk, kind=kind, severity=cond.severity, message=cond.message,
                    value=cond.value, telemetry=row, opened_at=msg.timestamp)
                open_alerts[kind] = alert.pk
                self.stats["alerts_opened"] += 1
                logger.warning("[alert] %s %s: %s", msg.sat_id, kind, cond.message)
                events.append(("alert_opened", payloads.alert_payload(alert, msg.sat_id)))
        for kind in [k for k in open_alerts if k not in active]:
            alert = Alert.objects.filter(pk=open_alerts.pop(kind)).first()
            if alert is None:  # deleted by an admin meanwhile
                continue
            alert.resolved_at = msg.timestamp
            alert.save(update_fields=["resolved_at"])
            self.stats["alerts_resolved"] += 1
            logger.info("[alert] %s %s cleared", msg.sat_id, kind)
            events.append(("alert_resolved", payloads.alert_payload(alert, msg.sat_id)))
        return events

    # ------------------------------------------------------------ rejects
    def _reject(self, topic, payload, reason, received_at):
        self.stats["rejected"] += 1
        logger.warning("REJECTED %s: %s", topic[:100], reason)
        RejectedMessage.objects.create(
            topic=topic[:255], reason=reason[:255], received_at=received_at,
            payload_preview=bytes(payload[:500]).decode("utf-8", "replace"))
