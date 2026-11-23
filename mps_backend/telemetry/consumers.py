from urllib.parse import parse_qs

from channels.generic.websocket import AsyncJsonWebsocketConsumer

from .broadcast import GROUP


class TelemetryConsumer(AsyncJsonWebsocketConsumer):
    """ws://<host>/ws/telemetry/[?sat_id=SAT-01]

    Server -> client messages:  {"type": "telemetry" | "status" | "alert_opened" |
    "alert_resolved", "data": {...}}  (same `data` shapes as the REST API).
    Read-only: anything the client sends is ignored (command uplink comes later)."""

    async def connect(self):
        query = parse_qs(self.scope.get("query_string", b"").decode())
        self.sat_id = (query.get("sat_id") or [None])[0]
        await self.channel_layer.group_add(GROUP, self.channel_name)
        await self.accept()

    async def disconnect(self, code):
        await self.channel_layer.group_discard(GROUP, self.channel_name)

    async def receive_json(self, content, **kwargs):
        pass

    async def telemetry_event(self, event):  # handler for {"type": "telemetry.event"}
        data = event["data"]
        if self.sat_id and data.get("sat_id") != self.sat_id:
            return
        await self.send_json({"type": event["event"], "data": data})
