"""Topic + payload validation (DFD step 3: "validates JSON schema").

Pure Python, no Django: every function either returns a parsed object or raises
ValidationError with a human-readable reason.

Design choices
* Strict about types and ranges: a boolean is NOT accepted as a number, NaN/Infinity
  are rejected, and numbers must be physically plausible.
* Lenient about extra JSON keys (ignored) and about the *set* of status strings
  (any short token is accepted; alerts decide what counts as a fault), so the
  simulator can add fields or modes without breaking the backend.
"""
from __future__ import annotations

import json
import math
import re
from dataclasses import dataclass
from datetime import datetime, timezone

MAX_PAYLOAD_BYTES = 4096
SAT_ID_RE = re.compile(r"^[A-Za-z0-9_-]{1,32}$")  # same rule the simulator enforces
TOKEN_RE = re.compile(r"^[A-Za-z0-9_ .-]{1,32}$")  # e.g. NOMINAL, SUN_POINTING, LOST
TIMESTAMP_FORMAT = "%Y-%m-%dT%H:%M:%SZ"  # ISO-8601 UTC, whole seconds

# Plausibility limits (reject corrupt values, not faults: a 75 C fault is still valid data).
LIMITS = {
    "battery_v": (0.0, 60.0),
    "battery_i": (-100.0, 100.0),
    "temp_c": (-150.0, 250.0),
    "sensor_value": (-1e6, 1e6),
}
U8, U16 = 0xFF, 0xFFFF


class ValidationError(ValueError):
    pass


@dataclass(frozen=True)
class ParsedTelemetry:
    sat_id: str
    timestamp: datetime
    sequence: int
    version: int
    packet_type: int
    application_id: int
    payload_length: int
    size_bytes: int
    battery_v: float
    battery_i: float
    temp_c: float
    obc_status: str
    adcs_mode: str
    link_status: str
    payload_on: bool
    sensor_value: float


@dataclass(frozen=True)
class ParsedStatus:
    sat_id: str
    state: str  # "online" | "offline"


# ------------------------------------------------------------------ topics
def parse_topic(topic: str):
    """'mps/<sat>/telemetry/all' -> (sat, 'telemetry');  'mps/<sat>/status' -> (sat, 'status')."""
    parts = topic.split("/")
    if len(parts) < 3 or parts[0] != "mps":
        raise ValidationError("unsupported topic")
    sat_id = parts[1]
    if not SAT_ID_RE.match(sat_id):
        raise ValidationError(f"invalid satellite id in topic: {sat_id[:40]!r}")
    if len(parts) == 3 and parts[2] == "status":
        return sat_id, "status"
    if len(parts) == 4 and parts[2] == "telemetry" and parts[3] == "all":
        return sat_id, "telemetry"
    raise ValidationError("unsupported topic")


# ----------------------------------------------------------------- helpers
def _reject_constant(name):
    raise ValidationError(f"non-finite number in JSON ({name})")


def _load_json_object(payload: bytes) -> dict:
    if len(payload) > MAX_PAYLOAD_BYTES:
        raise ValidationError(f"payload too large ({len(payload)} bytes)")
    try:
        data = json.loads(payload.decode("utf-8"), parse_constant=_reject_constant)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValidationError(f"not valid UTF-8 JSON: {exc}") from None
    if not isinstance(data, dict):
        raise ValidationError("JSON payload must be an object")
    return data


def _section(obj: dict, name: str) -> dict:
    value = obj.get(name)
    if not isinstance(value, dict):
        raise ValidationError(f"missing or invalid section '{name}'")
    return value


def _int(obj: dict, key: str, where: str, hi: int) -> int:
    value = obj.get(key)
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValidationError(f"{where}.{key} must be an integer")
    if not 0 <= value <= hi:
        raise ValidationError(f"{where}.{key}={value} out of range 0..{hi}")
    return value


def _float(obj: dict, key: str, where: str) -> float:
    value = obj.get(key)
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValidationError(f"{where}.{key} must be a number")
    value = float(value)
    if not math.isfinite(value):
        raise ValidationError(f"{where}.{key} must be finite")
    lo, hi = LIMITS[key]
    if not lo <= value <= hi:
        raise ValidationError(f"{where}.{key}={value} outside plausible range {lo}..{hi}")
    return value


def _token(obj: dict, key: str, where: str) -> str:
    value = obj.get(key)
    if not isinstance(value, str) or not TOKEN_RE.match(value):
        raise ValidationError(f"{where}.{key} must be a short text token")
    return value


def _check_envelope(data: dict, sat_id: str) -> None:
    if data.get("sat_id") != sat_id:
        raise ValidationError("sat_id in payload does not match topic")


# ---------------------------------------------------------------- messages
def parse_telemetry(sat_id: str, payload: bytes) -> ParsedTelemetry:
    data = _load_json_object(payload)
    _check_envelope(data, sat_id)

    raw_ts = data.get("timestamp")
    if not isinstance(raw_ts, str):
        raise ValidationError("timestamp missing")
    try:
        timestamp = datetime.strptime(raw_ts, TIMESTAMP_FORMAT).replace(tzinfo=timezone.utc)
    except ValueError:
        raise ValidationError(f"timestamp not ISO-8601 UTC (YYYY-MM-DDTHH:MM:SSZ): {raw_ts[:40]!r}") from None

    packet, eps = _section(data, "packet"), _section(data, "eps")
    obc, adcs = _section(data, "obc"), _section(data, "adcs")
    comms, pay = _section(data, "comms"), _section(data, "payload")

    payload_on = pay.get("payload_on")
    if not isinstance(payload_on, bool):
        raise ValidationError("payload.payload_on must be true/false")

    return ParsedTelemetry(
        sat_id=sat_id,
        timestamp=timestamp,
        sequence=_int(packet, "sequence", "packet", U16),
        version=_int(packet, "version", "packet", U8),
        packet_type=_int(packet, "type", "packet", U8),
        application_id=_int(packet, "application_id", "packet", U16),
        payload_length=_int(packet, "payload_length", "packet", U16),
        size_bytes=_int(packet, "size_bytes", "packet", U16),
        battery_v=_float(eps, "battery_v", "eps"),
        battery_i=_float(eps, "battery_i", "eps"),
        temp_c=_float(eps, "temp_c", "eps"),
        obc_status=_token(obc, "status", "obc"),
        adcs_mode=_token(adcs, "mode", "adcs"),
        link_status=_token(comms, "link_status", "comms"),
        payload_on=payload_on,
        sensor_value=_float(pay, "sensor_value", "payload"),
    )


def parse_status(sat_id: str, payload: bytes) -> ParsedStatus:
    data = _load_json_object(payload)
    _check_envelope(data, sat_id)
    state = data.get("state")
    if state not in ("online", "offline"):
        raise ValidationError(f"status.state must be 'online' or 'offline', got {str(state)[:20]!r}")
    return ParsedStatus(sat_id=sat_id, state=state)
