from channels.layers import get_channel_layer
from channels.testing import WebsocketCommunicator
from django.test import SimpleTestCase

from config.asgi import application
from telemetry.broadcast import GROUP


async def publish(event, data):
    await get_channel_layer().group_send(GROUP, {"type": "telemetry.event", "event": event, "data": data})


class WebSocketTests(SimpleTestCase):
    async def test_receives_live_events(self):
        ws = WebsocketCommunicator(application, "/ws/telemetry/", headers=[(b"origin", b"http://localhost:5173")])
        ok, _ = await ws.connect()
        self.assertTrue(ok)
        await publish("telemetry", {"sat_id": "SAT-01", "n": 1})
        self.assertEqual(await ws.receive_json_from(), {"type": "telemetry", "data": {"sat_id": "SAT-01", "n": 1}})
        await publish("alert_opened", {"sat_id": "SAT-02"})
        self.assertEqual((await ws.receive_json_from())["type"], "alert_opened")
        await ws.disconnect()

    async def test_sat_id_filter(self):
        ws = WebsocketCommunicator(application, "/ws/telemetry/?sat_id=SAT-02",
                                   headers=[(b"origin", b"http://localhost:5173")])
        await ws.connect()
        await publish("telemetry", {"sat_id": "SAT-01"})
        await publish("telemetry", {"sat_id": "SAT-02"})
        self.assertEqual((await ws.receive_json_from())["data"]["sat_id"], "SAT-02")
        self.assertTrue(await ws.receive_nothing())
        await ws.disconnect()

    async def test_foreign_origin_is_refused(self):
        ws = WebsocketCommunicator(application, "/ws/telemetry/", headers=[(b"origin", b"http://evil.example")])
        ok, _ = await ws.connect()
        self.assertFalse(ok)
