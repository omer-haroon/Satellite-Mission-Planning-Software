"""
Reference MQTT consumer for the MPS ground-station backend.

Stand-alone (no Django needed) so you can test the simulator first.
The two places marked  >>> DJANGO <<<  are where the real backend plugs in.

    pip install paho-mqtt
    python mqtt_ingest.py                      # combined topic  (default)
    python mqtt_ingest.py --mode subsystem     # one topic per subsystem
    MQTT_HOST=192.168.1.20 MQTT_USER=mps MQTT_PASS=secret python mqtt_ingest.py
"""
import argparse
import json
import os
import sys
from datetime import datetime

import paho.mqtt.client as mqtt

# ---------------------------------------------------------------- topics
TOPICS = {
    "combined":  [("mps/+/telemetry/all", 1)],
    # '+' would also match 'all', so list the five subsystem topics explicitly
    "subsystem": [(f"mps/+/telemetry/{s}", 1)
                  for s in ("eps", "obc", "adcs", "comms", "payload")],
}
STATUS_TOPIC = ("mps/+/status", 1)

# ------------------------------------------------------------ validation
# DFD step 3: "validates JSON schema".  Required keys + types per message kind.
ENVELOPE = {"sat_id": str, "timestamp": str}
COMBINED_SCHEMA = {
    "packet":  {"sequence": int, "version": int, "type": int,
                "application_id": int, "payload_length": int, "size_bytes": int},
    "eps":     {"battery_v": (int, float), "battery_i": (int, float), "temp_c": (int, float)},
    "obc":     {"status": str},
    "adcs":    {"mode": str},
    "comms":   {"link_status": str},
    "payload": {"payload_on": bool, "sensor_value": (int, float)},
}
SUBSYSTEM_SCHEMA = {
    "eps":     {"battery_v": (int, float), "battery_i": (int, float), "temp_c": (int, float)},
    "obc":     {"status": str},
    "adcs":    {"mode": str},
    "comms":   {"link_status": str},
    "payload": {"payload_on": bool, "sensor_value": (int, float)},
}


def _check(obj, schema, where):
    for key, typ in schema.items():
        if key not in obj:
            raise ValueError(f"{where}: missing '{key}'")
        if not isinstance(obj[key], typ):
            raise ValueError(f"{where}: '{key}' has wrong type")


def validate(kind, data):
    """kind is 'all' or a subsystem name. Raises ValueError if invalid."""
    _check(data, ENVELOPE, kind)
    datetime.strptime(data["timestamp"], "%Y-%m-%dT%H:%M:%SZ")  # ISO-8601 UTC
    if kind == "all":
        for section, schema in COMBINED_SCHEMA.items():
            if section not in data or not isinstance(data[section], dict):
                raise ValueError(f"all: missing section '{section}'")
            _check(data[section], schema, section)
    else:
        _check(data, {"sequence": int}, kind)
        _check(data, SUBSYSTEM_SCHEMA[kind], kind)


# ------------------------------------------------- business rules (DFD step 4)
# Limits match the simulator's fault injection (see README "Fault injection").
TEMP_HIGH_C = 60
BATTERY_LOW_V = 7.0


def alerts_for(kind, data):
    """Turn out-of-limit telemetry into alert strings (works for 'all' and subsystem topics)."""
    def section(name):
        return data.get(name) if kind == "all" else (data if kind == name else None)

    out = []
    eps, obc, adcs, comms = section("eps"), section("obc"), section("adcs"), section("comms")
    if eps:
        if eps["temp_c"] > TEMP_HIGH_C:
            out.append(f"TEMP HIGH {eps['temp_c']} C (>{TEMP_HIGH_C})")
        if eps["battery_v"] < BATTERY_LOW_V:
            out.append(f"BATTERY LOW {eps['battery_v']} V (<{BATTERY_LOW_V})")
    if obc and obc["status"] != "NOMINAL":
        out.append(f"OBC {obc['status']}")
    if adcs and adcs["mode"] == "FAULT":
        out.append("ADCS FAULT")
    if comms and comms["link_status"] != "OK":
        out.append(f"COMMS LINK {comms['link_status']}")
    return out


# ------------------------------------------------- fleet tracking
# The backend never has a hard-coded satellite list: a satellite exists the
# moment its first valid message arrives (topic wildcard '+' = any sat_id).
known_sats = set()
last_seq = {}          # (sat_id, kind) -> last sequence seen


def track(sat_id, kind, data):
    """Registry + packet-loss detection. Returns False for duplicates."""
    if sat_id not in known_sats:
        known_sats.add(sat_id)
        # >>> DJANGO <<<  Satellite.objects.get_or_create(sat_id=sat_id)
        print(f"[registry] new satellite discovered: {sat_id}  (fleet size {len(known_sats)})")

    seq = data["packet"]["sequence"] if kind == "all" else data["sequence"]
    prev = last_seq.get((sat_id, kind))
    last_seq[(sat_id, kind)] = seq
    if prev is None:
        return True
    if seq == prev:                       # QoS 1 may redeliver: ignore duplicate
        return False
    if seq == (prev + 1) & 0xFFFF:        # normal next packet (16-bit counter wraps 65535 -> 0)
        return True
    if seq == 0:                          # jumped back to 0 = simulator was restarted
        print(f"[seq] {sat_id}/{kind}: sequence reset to 0 (satellite restarted?)")
    else:
        lost = (seq - prev - 1) & 0xFFFF
        print(f"[seq] {sat_id}/{kind}: GAP, {lost} packet(s) lost before seq {seq}")
        # >>> DJANGO <<<  create an Alert / increment a packet-loss counter
    return True


# --------------------------------------------------------------- handlers
def handle_telemetry(sat_id, kind, data):
    """>>> DJANGO <<<  replace the prints with:
         1. Telemetry.objects.create(...)               (DFD step 5, MySQL)
         2. async_to_sync(channel_layer.group_send)(    (DFD step 7, WebSocket push)
                "telemetry", {"type": "telemetry.update", "data": data})
         3. Alert.objects.create(...) for each alert
    """
    seq = data["packet"]["sequence"] if kind == "all" else data["sequence"]
    print(f"[{data['timestamp']}] {sat_id:7s} {kind:8s} seq={seq:<5d}", end=" ")
    if kind == "all":
        e = data["eps"]
        print(f"V={e['battery_v']} I={e['battery_i']} T={e['temp_c']} "
              f"obc={data['obc']['status']} adcs={data['adcs']['mode']} "
              f"link={data['comms']['link_status']} sensor={data['payload']['sensor_value']}")
    else:
        print({k: v for k, v in data.items() if k not in ("sat_id", "timestamp", "sequence")})
    for a in alerts_for(kind, data):
        print("   !! ALERT:", a)


def handle_status(sat_id, data):
    print(f"[status] {sat_id} is {data.get('state', '?').upper()}")


# --------------------------------------------------------------- MQTT glue
def on_connect(client, userdata, flags, reason_code, properties):
    if reason_code != 0:
        print("connect refused:", reason_code)
        return
    # (re)subscribe on every connect, so it survives broker restarts
    client.subscribe(TOPICS[userdata["mode"]] + [STATUS_TOPIC])
    print("connected; subscribed to", [t for t, _ in TOPICS[userdata["mode"]]] + [STATUS_TOPIC[0]])


def on_message(client, userdata, msg):
    parts = msg.topic.split("/")            # mps/<sat>/telemetry/<kind>  |  mps/<sat>/status
    try:
        data = json.loads(msg.payload)
        sat_id = parts[1]
        if parts[2] == "status":
            handle_status(sat_id, data)
            return
        kind = parts[3]
        if data.get("sat_id") != sat_id:     # topic and payload must agree
            raise ValueError("sat_id in payload does not match topic")
        validate(kind, data)
    except (ValueError, KeyError, IndexError, json.JSONDecodeError) as exc:
        print(f"REJECTED {msg.topic}: {exc}", file=sys.stderr)  # DJANGO: log + counter
        return
    if track(sat_id, kind, data):
        handle_telemetry(sat_id, kind, data)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--mode", choices=TOPICS, default="combined")
    args = ap.parse_args()

    client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2,
                         client_id="mps-backend-ingest",
                         userdata={"mode": args.mode})
    if os.getenv("MQTT_USER"):
        client.username_pw_set(os.getenv("MQTT_USER"), os.getenv("MQTT_PASS", ""))
    if os.getenv("MQTT_TLS") == "1":
        client.tls_set(ca_certs=os.getenv("MQTT_CA_FILE") or None)
    client.on_connect, client.on_message = on_connect, on_message
    client.reconnect_delay_set(min_delay=1, max_delay=30)
    client.connect(os.getenv("MQTT_HOST", "localhost"),
                   int(os.getenv("MQTT_PORT", "8883" if os.getenv("MQTT_TLS") == "1" else "1883")),
                   keepalive=30)
    client.loop_forever()


if __name__ == "__main__":
    main()
