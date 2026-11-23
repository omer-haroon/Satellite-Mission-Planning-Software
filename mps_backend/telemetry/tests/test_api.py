from datetime import timedelta

from django.test import TestCase
from django.utils import timezone
from rest_framework.test import APIClient

from telemetry.processing import Ingestor

from .helpers import make, status, topic


class ApiTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        ing = Ingestor(push=lambda *a: None)
        now = timezone.now().replace(microsecond=0)
        for sat in ("SAT-01", "SAT-02"):
            ing.handle_message(f"mps/{sat}/status", status(sat))
            for seq in range(10):  # 10 packets, one per second, ending "now"
                ts = (now - timedelta(seconds=9 - seq)).strftime("%Y-%m-%dT%H:%M:%SZ")
                hot = {"temp_c": 75.0} if (sat == "SAT-01" and 3 <= seq <= 5) else {}
                ing.handle_message(topic(sat), make(sat, seq, ts=ts, **hot))
        cls.now = now

    def setUp(self):
        self.c = APIClient()

    def get(self, url, expect=200):
        r = self.c.get(url)
        self.assertEqual(r.status_code, expect, getattr(r, "data", None))
        return r.json()

    def test_root_lists_endpoints(self):
        self.assertIn("satellites", self.get("/api/"))

    def test_health(self):
        h = self.get("/api/health/")
        self.assertEqual((h["status"], h["database"], h["satellites"], h["satellites_online"]), ("ok", "ok", 2, 2))
        self.assertTrue(h["ingest_active"])
        self.assertEqual(h["active_alerts"], 0)

    def test_satellite_list_has_latest_and_counts(self):
        sats = self.get("/api/satellites/")
        self.assertEqual([s["sat_id"] for s in sats], ["SAT-01", "SAT-02"])
        s = sats[0]
        self.assertEqual((s["state"], s["packets_received"], s["last_sequence"], s["stale"]),
                         ("online", 10, 9, False))
        self.assertEqual(s["latest"]["packet"]["sequence"], 9)
        self.assertEqual(s["active_alerts"], 0)  # the hot episode ended

    def test_satellite_detail_and_404(self):
        self.assertEqual(self.get("/api/satellites/SAT-02/")["sat_id"], "SAT-02")
        self.get("/api/satellites/NOPE/", expect=404)

    def test_telemetry_default_newest_first_and_shape(self):
        page = self.get("/api/telemetry/")
        self.assertEqual(page["count"], 20)
        first = page["results"][0]
        self.assertEqual(set(first), {"id", "sat_id", "timestamp", "received_at", "packet", "eps", "obc",
                                      "adcs", "comms", "payload"})
        self.assertTrue(first["timestamp"].endswith("Z"))

    def test_telemetry_filter_order_pagination(self):
        asc = self.get("/api/telemetry/?sat_id=SAT-01&order=asc&limit=4")
        self.assertEqual(asc["count"], 10)
        self.assertEqual([r["packet"]["sequence"] for r in asc["results"]], [0, 1, 2, 3])
        self.assertIsNotNone(asc["next"])
        nxt = self.get("/api/telemetry/?sat_id=SAT-01&order=asc&limit=4&offset=4")
        self.assertEqual([r["packet"]["sequence"] for r in nxt["results"]], [4, 5, 6, 7])
        desc = self.get("/api/satellites/SAT-01/telemetry/?limit=2")
        self.assertEqual([r["packet"]["sequence"] for r in desc["results"]], [9, 8])

    def test_telemetry_date_range(self):
        start = (self.now - timedelta(seconds=6)).strftime("%Y-%m-%dT%H:%M:%SZ")
        end = (self.now - timedelta(seconds=4)).strftime("%Y-%m-%dT%H:%M:%SZ")
        page = self.get(f"/api/telemetry/?sat_id=SAT-02&from={start}&to={end}&order=asc")
        self.assertEqual([r["packet"]["sequence"] for r in page["results"]], [3, 4, 5])

    def test_limit_is_capped(self):
        self.assertEqual(self.get("/api/telemetry/?limit=999999")["count"], 20)

    def test_bad_parameters_give_structured_400(self):
        for url, field in (("/api/telemetry/?from=yesterday", "from"), ("/api/telemetry/?order=sideways", "order"),
                           ("/api/alerts/?active=maybe", "active")):
            with self.subTest(url=url):
                self.assertIn(field, self.get(url, expect=400))

    def test_alerts(self):
        page = self.get("/api/alerts/")
        self.assertEqual(page["count"], 1)
        a = page["results"][0]
        self.assertEqual((a["sat_id"], a["kind"], a["severity"], a["active"]), ("SAT-01", "TEMP_HIGH", "warning", False))
        self.assertIsNotNone(a["resolved_at"])
        self.assertEqual(self.get("/api/alerts/?active=true")["count"], 0)
        self.assertEqual(self.get("/api/alerts/?active=false")["count"], 1)
        self.assertEqual(self.get("/api/alerts/?sat_id=SAT-02")["count"], 0)
        self.assertEqual(self.get("/api/satellites/SAT-01/alerts/?kind=temp_high")["count"], 1)
        self.assertEqual(self.get("/api/alerts/?severity=critical")["count"], 0)

    def test_stale_satellite_flag(self):
        from telemetry.models import Satellite
        Satellite.objects.filter(sat_id="SAT-02").update(last_seen=timezone.now() - timedelta(minutes=5))
        sats = {s["sat_id"]: s for s in self.get("/api/satellites/")}
        self.assertTrue(sats["SAT-02"]["stale"])
        self.assertFalse(sats["SAT-01"]["stale"])

    def test_cors_header_for_react_dev_server(self):
        r = self.c.get("/api/health/", HTTP_ORIGIN="http://localhost:5173")
        self.assertEqual(r["Access-Control-Allow-Origin"], "http://localhost:5173")


class AggregateAndExportTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        ing = Ingestor(push=lambda *a: None)
        # 120 packets, one per second, starting on a minute boundary -> exactly 2 minute-buckets
        for seq in range(120):
            ts = f"2026-10-12T14:{seq // 60:02d}:{seq % 60:02d}Z"
            ing.handle_message(topic(), make("SAT-01", seq, ts=ts, temp_c=30.0 + (seq % 60)))

    def test_minute_buckets(self):
        r = APIClient().get("/api/telemetry/aggregate/?sat_id=SAT-01&bucket=minute")
        self.assertEqual(r.status_code, 200)
        body = r.json()
        self.assertEqual(body["count"], 2)
        first = body["results"][0]
        self.assertEqual((first["bucket_start"], first["count"]), ("2026-10-12T14:00:00Z", 60))
        self.assertEqual((first["temp_c"]["min"], first["temp_c"]["max"]), (30.0, 89.0))
        self.assertEqual(first["temp_c"]["avg"], 59.5)

    def test_hour_bucket_and_range(self):
        r = APIClient().get("/api/telemetry/aggregate/?sat_id=SAT-01&bucket=hour&to=2026-10-12T14:00:29Z")
        self.assertEqual(r.json()["results"][0]["count"], 30)

    def test_validation(self):
        c = APIClient()
        self.assertEqual(c.get("/api/telemetry/aggregate/").status_code, 400)
        self.assertEqual(c.get("/api/telemetry/aggregate/?sat_id=SAT-01&bucket=week").status_code, 400)

    def test_export_csv(self):
        import io
        from django.core.management import call_command
        buf = io.StringIO()
        call_command("export_telemetry", "--sat", "SAT-01", stdout=buf)
        lines = buf.getvalue().strip().splitlines()
        self.assertEqual(len(lines), 121)  # header + 120 rows
        self.assertTrue(lines[0].startswith("sat_id,timestamp,received_at"))
        self.assertIn("2026-10-12T14:00:00Z", lines[1])
