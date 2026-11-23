"""MQTT glue (paho-mqtt 2.x): connect, subscribe, hand every message to the Ingestor.

Subscriptions follow the simulator's topic contract:
    mps/+/telemetry/all   one message = one complete packet = one database row
    mps/+/status          online/offline (retained, plus the broker's Last Will)
The five per-subsystem topics are not needed: `all` already contains everything,
and subscribing to both would store every packet twice.
"""
from __future__ import annotations

import logging
import time

import paho.mqtt.client as mqtt
from django.db import close_old_connections

logger = logging.getLogger(__name__)

SUBSCRIPTIONS = [("mps/+/telemetry/all", 1), ("mps/+/status", 1)]
SUMMARY_EVERY_S = 10


class MqttListener:
    def __init__(self, ingestor, cfg: dict, verbose: bool = False):
        self.ingestor = ingestor
        self.cfg = cfg
        self.verbose = verbose
        self._last_summary = time.monotonic()
        self._summary_base = dict(ingestor.stats)

        self.client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id=cfg["CLIENT_ID"])
        if cfg["USER"]:
            self.client.username_pw_set(cfg["USER"], cfg["PASSWORD"])
        if cfg["TLS"]:
            self.client.tls_set(ca_certs=cfg["CA_FILE"] or None)
        self.client.on_connect = self._on_connect
        self.client.on_disconnect = self._on_disconnect
        self.client.on_message = self._on_message
        self.client.reconnect_delay_set(min_delay=1, max_delay=30)

    # ---------------------------------------------------------------- callbacks
    def _on_connect(self, client, userdata, flags, reason_code, properties):
        if reason_code != 0:
            logger.error("broker refused the connection: %s", reason_code)
            return
        # (re)subscribe on every connect so it survives broker restarts
        client.subscribe(SUBSCRIPTIONS)
        logger.info("connected to %s:%s; subscribed to %s", self.cfg["HOST"], self.cfg["PORT"],
                    [topic for topic, _ in SUBSCRIPTIONS])

    def _on_disconnect(self, client, userdata, flags, reason_code, properties):
        if reason_code != 0:
            logger.warning("lost connection to broker (%s); retrying automatically", reason_code)

    def _on_message(self, client, userdata, msg):
        try:
            close_old_connections()  # long-running process: drop DB connections the server timed out
            result = self.ingestor.handle_message(msg.topic, msg.payload)
            if self.verbose:
                logger.info("%s -> %s", msg.topic, result)
        except Exception:  # noqa: BLE001 - one bad message must never stop the listener
            logger.exception("unexpected error while processing %s", msg.topic)
        self._maybe_summary()

    def _maybe_summary(self):
        now = time.monotonic()
        if now - self._last_summary < SUMMARY_EVERY_S:
            return
        stats = self.ingestor.stats
        delta = {k: stats[k] - self._summary_base.get(k, 0) for k in
                 ("stored", "duplicates", "rejected", "alerts_opened")}
        logger.info("last %.0fs: stored=%d duplicates=%d rejected=%d new_alerts=%d",
                    now - self._last_summary, delta["stored"], delta["duplicates"],
                    delta["rejected"], delta["alerts_opened"])
        self._last_summary, self._summary_base = now, dict(stats)

    # --------------------------------------------------------------------- run
    def run_forever(self):
        """Blocks. Retries the first connection too, so the backend may start before the broker."""
        self.client.connect_async(self.cfg["HOST"], self.cfg["PORT"], keepalive=30)
        try:
            self.client.loop_forever(retry_first_connection=True)
        except KeyboardInterrupt:
            logger.info("stopping (Ctrl+C)")
        finally:
            self.client.disconnect()
