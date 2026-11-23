# MPS Backend: MQTT listener + REST/WebSocket API

Receives the simulator's telemetry from the MQTT broker, **validates** it, **stores** it,
raises **alerts**, and serves it to the future React dashboard over **REST** and **WebSocket**.

```
C++ simulator -> Mosquitto -> [ ingest_mqtt ] -> database -> [ REST API ] -> React dashboard
                                   |                              ^
                                   +-- Redis pub/sub --> [ WebSocket push ]
```

Two processes, as in the architecture diagram: the **listener** (`manage.py ingest_mqtt`)
and the **web server** (Daphne: REST + WebSocket). They share the database (and Redis for live push).

## Run it (Windows `cmd`; on Linux/macOS use `source venv/bin/activate` and `export`)

```bat
cd mps_backend
python -m venv venv
venv\Scripts\activate
pip install -r requirements.txt
python manage.py migrate
```

Then, in separate terminals (venv active in the two Python ones):

| # | Command | What it is |
|---|---------|-----------|
| 1 | `"C:\Program Files\mosquitto\mosquitto.exe" -v` | broker (as in your report) |
| 2 | `bin\Release\satellite_simulator.exe` | simulator (run from the simulator folder) |
| 3 | `python manage.py ingest_mqtt` | **the listener**. Run exactly one instance |
| 4 | `python manage.py runserver` | REST + WebSocket on http://127.0.0.1:8000 |

Open **http://127.0.0.1:8000/api/** in a browser and click through (DRF's browsable API).

Live push between the two processes needs Redis. Without it the REST API works fully, but the
WebSocket only carries events produced inside the web process. Set
`REDIS_URL=redis://127.0.0.1:6379/0` for both processes (on Windows run Redis via Docker or WSL).
Settings come from environment variables or a `.env` file (see `.env.example`);
MySQL is selected with `DB_ENGINE=mysql`.

## What the listener does with each message

Subscriptions: `mps/+/telemetry/all` and `mps/+/status` (QoS 1). The five per-subsystem topics are
ignored on purpose: `all` already contains everything, and one message = one database row.

1. **Topic check**: `mps/<sat>/telemetry/all` or `mps/<sat>/status`, valid satellite id.
2. **Validation** (`validation.py`): payload <= 4 KB, UTF-8 JSON object, all sections present, correct
   types (a boolean is *not* a number; NaN/Infinity rejected), integer ranges (16-bit sequence, ...),
   physically plausible values, strict UTC timestamp, and `sat_id` in the payload must equal the topic.
   Extra JSON fields are ignored. Failures go to the log and the `RejectedMessage` table; they never
   crash the listener.
3. **Registry**: an unknown satellite id creates a new `Satellite` row (no hard-coded fleet).
4. **Sequence check**: duplicates (QoS 1 redelivery) are dropped; gaps add to `packets_lost`;
   a jump back to 0 counts as a simulator restart; 16-bit wrap-around is handled.
5. **Store** one `Telemetry` row; update the satellite (`latest`, `last_seen`, counters).
6. **Alerts** (`rules.py`): one `Alert` per *episode* (opened when a condition starts, resolved when it
   ends): `TEMP_HIGH` (> 60 C, from your DFD), `BATTERY_LOW` (< 7.0 V), `OBC_FAULT`, `ADCS_FAULT`,
   `COMMS_LOST`. Thresholds: `MPS_TEMP_HIGH_C`, `MPS_BATTERY_LOW_V`. Severities are in
   `rules.SEVERITY` (my defaults; adjust as you like).
7. **Push** to WebSocket clients.

`status` messages (retained + Last Will) set `online`/`offline` immediately.

## REST API (read-only, JSON)

| Endpoint | Purpose |
|---|---|
| `GET /api/health/` | DB check, `ingest_active`, seconds since last packet, rejected count |
| `GET /api/satellites/` | fleet: state, `stale`, counters, `active_alerts`, **`latest` telemetry** |
| `GET /api/satellites/<sat_id>/` | one satellite |
| `GET /api/telemetry/` | history. Params: `sat_id`, `from`, `to` (ISO 8601, e.g. `2026-10-14T17:00:00Z`), `order=asc` or `desc`, `limit` (max 1000), `offset` |
| `GET /api/satellites/<sat_id>/telemetry/` | same, for one satellite |
| `GET /api/alerts/` | Params: `sat_id`, `active=true` or `false`, `severity`, `kind`, `from`, `to` |
| `GET /api/satellites/<sat_id>/alerts/` | same, for one satellite |

Telemetry JSON mirrors the simulator's MQTT message (`packet`, `eps`, `obc`, `adcs`, `comms`, `payload`)
plus `id` and `received_at`. Invalid query parameters return `400 {"<param>": "<reason>"}`.
CORS allows `http://localhost:3000` and `:5173` (`CORS_ALLOWED_ORIGINS`).

## WebSocket

`ws://127.0.0.1:8000/ws/telemetry/` (optional `?sat_id=SAT-01`). Server to client only:

```json
{"type": "telemetry", "data": { "...same shape as REST..." }}
```

`type` is one of `telemetry`, `status`, `alert_opened`, `alert_resolved`.

## Tests

```bat
python manage.py test telemetry
```

42 tests: validation (including a real captured simulator message), alert rules, sequence logic, the
ingest pipeline against a real database, every API endpoint, and the WebSocket. Passes on Django 4.2 and
5.2. Also verified end to end with the real C++ simulator, Mosquitto and Redis (3 satellites, faults
raised and cleared, listener restart, simulator restart, malformed messages).

## Not done yet / known limits

* **No authentication on the API yet.** The Security plan calls for JWT + roles: add DRF auth and
  permission classes in `REST_FRAMEWORK` (settings.py). Broker auth/TLS is supported by the listener
  (`MQTT_USER`, `MQTT_PASS`, `MQTT_TLS`, `MQTT_CA_FILE`) but untested here because my dev broker was
  anonymous. Until the broker requires authentication, anyone who can reach it can fill the
  `RejectedMessage` table.
* **Packets published while the listener is down are lost** (clean MQTT session). The next packet
  records them as `packets_lost`. A persistent session is possible if you need replay.
* Run **one** listener only; per-satellite sequence/alert state is held in that process.
* MySQL is configured but was not run here (SQLite was). There is no retention or downsampling yet:
  at 1 Hz the telemetry table grows by about 86,000 rows per satellite per day.
* Telecommand uplink is not implemented; the WebSocket ignores client messages.
