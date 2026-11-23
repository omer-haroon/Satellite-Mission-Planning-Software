"""Live push to WebSocket clients (DFD step 7) through the Channels layer.

Push is best-effort: if Redis/the layer is down, ingestion must keep storing data,
so failures are logged (at most once per 30 s) and swallowed.
"""
import logging
import time

from asgiref.sync import async_to_sync
from channels.layers import get_channel_layer

logger = logging.getLogger(__name__)
GROUP = "telemetry"
_last_warning = 0.0


def push(event_type, data):
    """event_type: 'telemetry' | 'status' | 'alert_opened' | 'alert_resolved'."""
    global _last_warning
    layer = get_channel_layer()
    if layer is None:
        return
    try:
        async_to_sync(layer.group_send)(
            GROUP, {"type": "telemetry.event", "event": event_type, "data": data}
        )
    except Exception as exc:  # noqa: BLE001 - never let push problems stop ingestion
        now = time.monotonic()
        if now - _last_warning > 30:
            _last_warning = now
            logger.warning("live push failed (%s: %s); data is still being stored",
                           type(exc).__name__, exc)
