import logging

from django.conf import settings
from django.core.management.base import BaseCommand

from telemetry.mqtt_client import MqttListener
from telemetry.processing import Ingestor


class Command(BaseCommand):
    help = ("Run the MQTT listener: subscribe to mps/+/telemetry/all and mps/+/status, validate "
            "every message, store it and push it to live clients. Run ONE instance.")

    def add_arguments(self, parser):
        parser.add_argument("--verbose", action="store_true",
                            help="log the outcome of every single message (noisy)")

    def handle(self, *args, **options):
        log = logging.getLogger("telemetry")
        if settings.CHANNEL_LAYERS["default"]["BACKEND"].endswith("InMemoryChannelLayer"):
            log.warning("REDIS_URL is not set: data is stored and served by the REST API, but live "
                        "WebSocket push cannot reach the web server process (set REDIS_URL for that)")
        ingestor = Ingestor()
        cfg = settings.MQTT
        log.info("starting MQTT listener -> broker %s:%s (%d satellites already known)",
                 cfg["HOST"], cfg["PORT"], len(ingestor._sats))
        MqttListener(ingestor, cfg, verbose=options["verbose"]).run_forever()
