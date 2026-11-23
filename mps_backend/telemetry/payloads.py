"""One JSON shape for telemetry, shared by the REST API and the WebSocket push.

The shape deliberately mirrors the simulator's MQTT message (nested by subsystem),
plus `id` and `received_at`, so the frontend sees the same structure everywhere.
"""
from datetime import timezone as dt_timezone


def iso(dt):
    """Aware datetime -> ISO-8601 UTC string ending in 'Z' (None stays None)."""
    if dt is None:
        return None
    return dt.astimezone(dt_timezone.utc).isoformat().replace("+00:00", "Z")


def telemetry_payload(t, sat_id=None):
    return {
        "id": t.id,
        "sat_id": sat_id or t.satellite.sat_id,
        "timestamp": iso(t.timestamp),
        "received_at": iso(t.received_at),
        "packet": {
            "version": t.version,
            "type": t.packet_type,
            "application_id": t.application_id,
            "sequence": t.sequence,
            "payload_length": t.payload_length,
            "size_bytes": t.size_bytes,
        },
        "eps": {"battery_v": t.battery_v, "battery_i": t.battery_i, "temp_c": t.temp_c},
        "obc": {"status": t.obc_status},
        "adcs": {"mode": t.adcs_mode},
        "comms": {"link_status": t.link_status},
        "payload": {"payload_on": t.payload_on, "sensor_value": t.sensor_value},
    }


def alert_payload(a, sat_id=None):
    return {
        "id": a.id,
        "sat_id": sat_id or a.satellite.sat_id,
        "kind": a.kind,
        "severity": a.severity,
        "message": a.message,
        "value": a.value,
        "opened_at": iso(a.opened_at),
        "resolved_at": iso(a.resolved_at),
        "active": a.resolved_at is None,
    }


def satellite_payload(s, now, stale_after_s):
    """`s` should come from views.satellite_queryset() (adds `active_alerts`, loads `latest`)."""
    age = None if s.last_seen is None else max(0.0, (now - s.last_seen).total_seconds())
    return {
        "sat_id": s.sat_id,
        "state": s.state,
        "state_changed_at": iso(s.state_changed_at),
        "stale": age is None or age > stale_after_s,
        "first_seen": iso(s.first_seen),
        "last_seen": iso(s.last_seen),
        "seconds_since_last_packet": None if age is None else round(age, 1),
        "application_id": s.application_id,
        "last_sequence": s.last_sequence,
        "packets_received": s.packets_received,
        "packets_lost": s.packets_lost,
        "sequence_resets": s.sequence_resets,
        "active_alerts": getattr(s, "active_alerts", 0),
        "latest": telemetry_payload(s.latest, s.sat_id) if s.latest else None,
    }


def status_payload(sat_id, state, changed_at):
    return {"sat_id": sat_id, "state": state, "state_changed_at": iso(changed_at)}
