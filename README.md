# Satellite Telemetry Simulator  ->  MQTT  (multi-satellite)

The C++ simulator simulates a **fleet** of satellites (1 to 500). Each one generates, encodes
(24-byte CCSDS-style packet) and decodes telemetry once per second, and every decoded packet is
**published to an MQTT broker**. A local JSONL black-box log (`logs/telemetry_log.jsonl`, one line
per packet, each with its `sat_id`) is still written.

```
SAT-01 --\                                                   /--> MySQL
SAT-02 ---+-- MQTT --> Mosquitto broker --> Django (paho) --+---> Redis/Channels --WebSocket--> React
SAT-nn --/                                                   (no per-satellite configuration)
```

## What makes it a fleet (not a hard-coded SAT-01)

* **Own identity:** each satellite has its own `sat_id` and CCSDS `application_id` (100, 101, 102, ...).
* **Own state:** independent battery/temperature/sensor values (slightly different starting points),
  independent 16-bit packet sequence counter.
* **Own MQTT connection and Last Will:** if one satellite drops, only *its* status goes `offline`.
* **Backend needs no change when satellites are added:** it subscribes with the wildcard
  `mps/+/telemetry/all` and takes `sat_id` from the topic. A satellite exists from its first message.

## Topic contract (give this to the backend developer)

| Topic | Payload | QoS | Retained |
|---|---|---|---|
| `mps/<sat_id>/telemetry/all` | whole packet, nested by subsystem (same shape as the JSONL log) | 1 | no |
| `mps/<sat_id>/telemetry/eps` | `sat_id, timestamp, sequence, battery_v, battery_i, temp_c` | 1 | no |
| `mps/<sat_id>/telemetry/obc` | `sat_id, timestamp, sequence, status` | 1 | no |
| `mps/<sat_id>/telemetry/adcs` | `sat_id, timestamp, sequence, mode` | 1 | no |
| `mps/<sat_id>/telemetry/comms` | `sat_id, timestamp, sequence, link_status` | 1 | no |
| `mps/<sat_id>/telemetry/payload` | `sat_id, timestamp, sequence, payload_on, sensor_value` | 1 | no |
| `mps/<sat_id>/status` | `{"sat_id","state":"online"\|"offline"}` | 1 | **yes** (+ Last Will) |

* `<sat_id>`: letters, digits, `-` and `_`, max 32 chars (it becomes part of the topic, so `/ + #`
  and spaces are rejected at startup). Default names are `SAT-01`, `SAT-02`, ...
* Status strings: `obc.status` = `NOMINAL` \| `FAULT`, `adcs.mode` = `SUN_POINTING` \| `FAULT`,
  `comms.link_status` = `OK` \| `LOST`. Consumers must not assume the values are constant.
* `timestamp` is ISO-8601 UTC. `sequence` is a 16-bit per-satellite counter that wraps 65535 -> 0.

Backend subscriptions: `mps/+/telemetry/all` (one message = one DB row) **or** the five subsystem
topics listed explicitly, plus `mps/+/status`. Do not use `mps/+/telemetry/+` in `both` mode: it
also matches `all`, so every packet would be stored twice. The backend should always check that the
`sat_id` in the payload equals the one in the topic.

Example `all` message:
```json
{"sat_id":"SAT-02","timestamp":"2026-10-01T20:41:07Z",
 "packet":{"version":1,"type":1,"application_id":101,"sequence":0,"payload_length":16,"size_bytes":24},
 "eps":{"battery_v":8.20,"battery_i":0.36,"temp_c":42.03},
 "obc":{"status":"NOMINAL"},"adcs":{"mode":"SUN_POINTING"},
 "comms":{"link_status":"OK"},"payload":{"payload_on":true,"sensor_value":96.94}}
```

## Configuration (environment variables)

| Variable | Default | Meaning |
|---|---|---|
| `SAT_COUNT` | `1` | simulate N satellites named `SAT-01`..`SAT-NN` (1-500) |
| `SAT_IDS` | - | explicit list, e.g. `"PK-SAT-1,CUBE_7"` (overrides `SAT_COUNT`) |
| `MQTT_HOST` / `MQTT_PORT` | `localhost` / `1883` (`8883` with TLS) | broker |
| `MQTT_MODE` | `both` | `combined`, `subsystem` or `both` |
| `MQTT_USER` / `MQTT_PASS` | none | broker credentials (same for all satellites) |
| `MQTT_TLS` / `MQTT_CA_FILE` | off | `1` = `ssl://`, CA certificate in PEM |
| `SIM_VERBOSE` | `0` | `1` = also print hex dump of every packet |
| `FAULT_INJECTION` | `1` | `0` = no faults, healthy telemetry only |
| `FAULT_MEAN_INTERVAL_S` | `180` | average quiet time between faults of one satellite (seconds) |
| `FAULT_TYPES` | all | subset of `low_battery,high_temp,comms_loss,obc_fault,adcs_fault` |

Examples:
```
SAT_COUNT=10 ./bin/satellite_simulator
SAT_IDS="ALPHA,BRAVO,CHARLIE" MQTT_MODE=combined MQTT_HOST=192.168.1.20 ./bin/satellite_simulator
```
Windows (cmd): `set SAT_COUNT=10` then `bin\satellite_simulator.exe`.

## Build

Needs a C++17 compiler, CMake and the Eclipse Paho MQTT C++ library.

* **Ubuntu/Debian:** `sudo apt install libpaho-mqttpp-dev libpaho-mqtt-dev libssl-dev cmake g++`
* **Windows:** `vcpkg install paho-mqttpp3:x64-windows`, then
  `cmake -S . -B build -DCMAKE_TOOLCHAIN_FILE=%VCPKG_ROOT%/scripts/buildsystems/vcpkg.cmake`

```
cmake -S . -B build -DCMAKE_BUILD_TYPE=Release
cmake --build build --config Release
```
The binary is written to `bin/`. Run it from the project root (it opens `logs/telemetry_log.jsonl`).

## Run / test

```
mosquitto -v                                   # terminal 1: broker
mosquitto_sub -v -t "mps/#"                    # terminal 2: watch raw traffic
SAT_COUNT=3 ./bin/satellite_simulator          # terminal 3: simulator
python backend_reference/mqtt_ingest.py        # terminal 4: stand-in for the backend (pip install paho-mqtt)
```
For a broker on another machine use `docs/mosquitto-dev.conf` (Mosquitto 2 is localhost-only by default).

## Fault injection

Each satellite occasionally develops one fault, at a random moment and for a random duration,
then recovers. Faults appear **in the telemetry values** - no new JSON fields, so the topic
contract above is unchanged. Satellites are independent (own random faults).

| Fault | What the ground station sees | Lasts | Counts as fault when |
|---|---|---|---|
| Low battery | `eps.battery_v` sinks gradually from ~8.2 V to ~6.6 V, then recovers | 45-90 s | `battery_v < 7.0` |
| High temperature | `eps.temp_c` climbs gradually from ~41 C to ~75 C, then cools | 30-75 s | `temp_c > 60` |
| Communication loss | `comms.link_status` = `LOST` | 10-30 s | `link_status != "OK"` |
| OBC fault | `obc.status` = `FAULT` | 10-40 s | `status != "NOMINAL"` |
| ADCS fault | `adcs.mode` = `FAULT` | 15-45 s | `mode == "FAULT"` |

* Battery and temperature ramp (about 0.04 V/s and 1.5 C/s) instead of jumping, and ramp back
  after the fault ends, so they look like real trends on a chart.
* Packet encoding: status bytes use `1` = normal and `2` = fault/lost.
* A `LOST` link means the satellite *reports* a bad link; the packet itself is still published
  (and logged), so the backend can show it. Real packet loss would show up as sequence gaps.
* The console marks faulty packets with `** FAULT: ... **` (derived from the decoded values) and
  prints `[FAULT] SAT-01  LOW_BATTERY injected (for about 62 s)` / `cleared` events.
* Only one fault per satellite at a time. Default is about one fault every 3 minutes per
  satellite; for a quick demo use e.g. `FAULT_MEAN_INTERVAL_S=15`. Faults can be limited, e.g.
  `FAULT_TYPES=low_battery,comms_loss`, or disabled with `FAULT_INJECTION=0`.
* `backend_reference/mqtt_ingest.py` prints an `ALERT` for each of these conditions.

```
SAT_COUNT=3 FAULT_MEAN_INTERVAL_S=15 ./bin/satellite_simulator
FAULT_TYPES=high_temp SAT_IDS="ALPHA" ./bin/satellite_simulator
```

## Behaviour worth knowing

* **Broker down at start or mid-run:** connecting is non-blocking, so all satellites keep their 1 Hz
  cadence and keep writing the JSONL log; each retries every 5 s. Packets produced during an outage
  are **not** re-sent over MQTT (they exist only in the log file).
* **Ctrl+C** publishes `offline` for every satellite. **Crash / kill -9:** the broker publishes each
  satellite's Last Will.
* One TCP connection per satellite (hence the 500 limit). 100 satellites x 6 topics = 600 msg/s was
  tested against a local Mosquitto without losing a message; test your own broker/network before
  going bigger.
* The JSON comes from the *decoded* packet, so file, MQTT and wire format always agree.
* Console output is one compact line per satellite per second; `SIM_VERBOSE=1` adds the hex dump.

## Backend: what the reference consumer demonstrates (DFD steps 3-7)

`backend_reference/mqtt_ingest.py` validates every message against a schema and rejects bad ones,
registers a satellite when it first appears, and detects packet loss per satellite from the
sequence numbers (gap, duplicate, restart, 16-bit wrap). Places marked `>>> DJANGO <<<` show where
to plug in the real code:

```python
Satellite.objects.get_or_create(sat_id=sat_id)                  # fleet registry
Telemetry.objects.create(satellite=sat, timestamp=..., ...)      # index on (satellite, timestamp)
async_to_sync(get_channel_layer().group_send)(
    "telemetry", {"type": "telemetry.update", "data": data})    # React clients, unchanged
```
Run it as its own process (`python manage.py ingest_mqtt`). The React dashboard should use a
satellite selector fed from the `Satellite` table rather than a fixed list.
