"""Business rules (DFD step 4): alert conditions and sequence-number analysis.

Pure Python, no Django. Thresholds come from settings (MPS_TEMP_HIGH_C, MPS_BATTERY_LOW_V);
the temperature rule (> 60 C) is the one given in the Data Flow Diagram, the battery
limit matches the simulator's fault injection (< 7.0 V). Severities are a starting
point: adjust SEVERITY below to taste.
"""
from __future__ import annotations

from dataclasses import dataclass

from .validation import ParsedTelemetry

SEVERITY = {
    "BATTERY_LOW": "warning",
    "TEMP_HIGH": "warning",
    "ADCS_FAULT": "warning",
    "OBC_FAULT": "critical",
    "COMMS_LOST": "critical",
}


@dataclass(frozen=True)
class Condition:
    kind: str
    severity: str
    message: str
    value: float | None = None


def evaluate(t: ParsedTelemetry, temp_high_c: float = 60.0, battery_low_v: float = 7.0):
    """Return the alert conditions that are true for this packet."""
    found = []
    if t.temp_c > temp_high_c:
        found.append(Condition("TEMP_HIGH", SEVERITY["TEMP_HIGH"],
                               f"Temperature {t.temp_c:.2f} C above {temp_high_c:g} C", t.temp_c))
    if t.battery_v < battery_low_v:
        found.append(Condition("BATTERY_LOW", SEVERITY["BATTERY_LOW"],
                               f"Battery {t.battery_v:.2f} V below {battery_low_v:g} V", t.battery_v))
    if t.obc_status != "NOMINAL":
        found.append(Condition("OBC_FAULT", SEVERITY["OBC_FAULT"], f"OBC status {t.obc_status}"))
    if t.adcs_mode == "FAULT":
        found.append(Condition("ADCS_FAULT", SEVERITY["ADCS_FAULT"], "ADCS in FAULT mode"))
    if t.link_status != "OK":
        found.append(Condition("COMMS_LOST", SEVERITY["COMMS_LOST"], f"Comms link {t.link_status}"))
    return found


# --------------------------------------------------------------- sequences
FIRST, OK, DUPLICATE, GAP, RESTART, STALE = "first", "ok", "duplicate", "gap", "restart", "stale"


def classify_sequence(prev: int | None, seq: int):
    """Compare a packet's 16-bit sequence counter with the previous one for that satellite.

    Returns (verdict, packets_lost):
      first      no previous packet known
      ok         exactly the next number (65535 -> 0 wraps normally)
      duplicate  same number again (MQTT QoS 1 may redeliver): do not store twice
      gap        numbers skipped: packets_lost = how many
      restart    jumped back to 0: the simulator was restarted
      stale      arrived 'behind' the latest one (out of order): store, but do not count loss
    """
    if prev is None:
        return FIRST, 0
    if seq == prev:
        return DUPLICATE, 0
    delta = (seq - prev) & 0xFFFF
    if delta == 1:
        return OK, 0
    if seq == 0:
        return RESTART, 0
    if delta >= 0x8000:
        return STALE, 0
    return GAP, delta - 1
