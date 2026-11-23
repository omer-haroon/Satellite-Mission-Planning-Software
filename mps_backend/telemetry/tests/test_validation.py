import json

from django.test import SimpleTestCase

from telemetry.validation import (MAX_PAYLOAD_BYTES, ValidationError, parse_status,
                                  parse_telemetry, parse_topic)

from .helpers import REAL_SAMPLE


class TopicTests(SimpleTestCase):
    def test_valid_topics(self):
        self.assertEqual(parse_topic("mps/SAT-01/telemetry/all"), ("SAT-01", "telemetry"))
        self.assertEqual(parse_topic("mps/PK_SAT-1/status"), ("PK_SAT-1", "status"))

    def test_invalid_topics(self):
        for bad in ("foo/SAT-01/status", "mps/SAT-01/telemetry/eps", "mps/SAT-01", "mps//status",
                    "mps/SAT 01/status", "mps/" + "A" * 33 + "/status", "mps/SAT-01/telemetry/all/x"):
            with self.subTest(bad=bad), self.assertRaises(ValidationError):
                parse_topic(bad)


class TelemetryParseTests(SimpleTestCase):
    def mutate(self, fn):
        d = json.loads(REAL_SAMPLE)
        fn(d)
        return json.dumps(d).encode()

    def test_real_simulator_message_parses(self):
        t = parse_telemetry("SAT-01", REAL_SAMPLE)
        self.assertEqual((t.sequence, t.application_id, t.battery_v, t.temp_c), (0, 100, 8.2, 38.27))
        self.assertEqual((t.obc_status, t.adcs_mode, t.link_status), ("NOMINAL", "SUN_POINTING", "OK"))
        self.assertTrue(t.payload_on)
        self.assertEqual(t.timestamp.isoformat(), "2026-10-12T14:38:09+00:00")

    def test_extra_fields_are_ignored(self):
        parse_telemetry("SAT-01", self.mutate(lambda d: d.update(new_field=1)))

    def test_fault_values_are_valid_data(self):  # a fault must be stored, not rejected
        t = parse_telemetry("SAT-01", self.mutate(lambda d: d["eps"].update(temp_c=75.0, battery_v=6.6)))
        self.assertEqual(t.temp_c, 75.0)

    def assert_rejected(self, payload, sat="SAT-01", fragment=""):
        with self.assertRaises(ValidationError) as cm:
            parse_telemetry(sat, payload)
        self.assertIn(fragment, str(cm.exception))

    def test_rejections(self):
        cases = {
            "not json": (b"{nope", "valid"),
            "json array": (b"[1,2]", "object"),
            "bad utf8": (b"\xff\xfe", "UTF-8"),
            "NaN": (REAL_SAMPLE.replace(b"38.27", b"NaN"), "non-finite"),
            "Infinity": (REAL_SAMPLE.replace(b"38.27", b"Infinity"), "non-finite"),
            "too large": (b" " * (MAX_PAYLOAD_BYTES + 1), "too large"),
            "missing section": (self.mutate(lambda d: d.pop("eps")), "eps"),
            "missing field": (self.mutate(lambda d: d["eps"].pop("temp_c")), "temp_c"),
            "string number": (self.mutate(lambda d: d["eps"].update(temp_c="41")), "temp_c"),
            "bool as number": (self.mutate(lambda d: d["eps"].update(temp_c=True)), "temp_c"),
            "bool as sequence": (self.mutate(lambda d: d["packet"].update(sequence=True)), "sequence"),
            "float sequence": (self.mutate(lambda d: d["packet"].update(sequence=1.5)), "sequence"),
            "negative sequence": (self.mutate(lambda d: d["packet"].update(sequence=-1)), "sequence"),
            "sequence > 16 bit": (self.mutate(lambda d: d["packet"].update(sequence=70000)), "sequence"),
            "implausible temp": (self.mutate(lambda d: d["eps"].update(temp_c=9999)), "plausible"),
            "payload_on not bool": (self.mutate(lambda d: d["payload"].update(payload_on=1)), "payload_on"),
            "status not text": (self.mutate(lambda d: d["obc"].update(status=5)), "status"),
            "status weird chars": (self.mutate(lambda d: d["obc"].update(status="<script>")), "status"),
            "bad timestamp format": (self.mutate(lambda d: d.update(timestamp="2026-10-12 14:38:09")), "timestamp"),
            "timestamp missing": (self.mutate(lambda d: d.pop("timestamp")), "timestamp"),
            "sat_id mismatch": (self.mutate(lambda d: d.update(sat_id="SAT-99")), "match"),
        }
        for name, (payload, fragment) in cases.items():
            with self.subTest(name):
                self.assert_rejected(payload, fragment=fragment)


class StatusParseTests(SimpleTestCase):
    def test_ok(self):
        self.assertEqual(parse_status("SAT-01", b'{"sat_id":"SAT-01","state":"offline"}').state, "offline")

    def test_bad(self):
        for payload in (b'{"sat_id":"SAT-01","state":"maybe"}', b'{"sat_id":"SAT-02","state":"online"}',
                        b'{"sat_id":"SAT-01"}', b"nope"):
            with self.subTest(payload=payload), self.assertRaises(ValidationError):
                parse_status("SAT-01", payload)
