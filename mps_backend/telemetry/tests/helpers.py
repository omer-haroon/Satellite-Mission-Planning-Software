import json

# A real `mps/SAT-01/telemetry/all` message captured from the C++ simulator.
REAL_SAMPLE = (b'{"sat_id":"SAT-01","timestamp":"2026-10-12T14:38:09Z","packet":{"version":1,"type":1,'
               b'"application_id":100,"sequence":0,"payload_length":16,"size_bytes":24},"eps":{"battery_v":8.20,'
               b'"battery_i":0.41,"temp_c":38.27},"obc":{"status":"NOMINAL"},"adcs":{"mode":"SUN_POINTING"},'
               b'"comms":{"link_status":"OK"},"payload":{"payload_on":true,"sensor_value":101.91}}')


def make(sat="SAT-01", seq=0, ts="2026-10-12T14:38:09Z", **over):
    """Build a telemetry payload (bytes). Override e.g. temp_c=75, obc='FAULT'."""
    d = json.loads(REAL_SAMPLE)
    d["sat_id"], d["timestamp"] = sat, ts
    d["packet"]["sequence"] = seq
    d["eps"].update({k: over[k] for k in ("battery_v", "battery_i", "temp_c") if k in over})
    if "obc" in over:
        d["obc"]["status"] = over["obc"]
    if "adcs" in over:
        d["adcs"]["mode"] = over["adcs"]
    if "link" in over:
        d["comms"]["link_status"] = over["link"]
    return json.dumps(d).encode()


def topic(sat="SAT-01"):
    return f"mps/{sat}/telemetry/all"


def status(sat="SAT-01", state="online"):
    return json.dumps({"sat_id": sat, "state": state}).encode()
