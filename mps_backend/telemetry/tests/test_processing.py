from datetime import timedelta

from django.test import TestCase
from django.utils import timezone

from telemetry.models import Alert, RejectedMessage, Satellite, Telemetry
from telemetry.processing import Ingestor

from .helpers import REAL_SAMPLE, make, status, topic


class IngestorTests(TestCase):
    def setUp(self):
        self.pushed = []
        self.ing = self.new_ingestor()

    def new_ingestor(self):
        return Ingestor(push=lambda kind, data: self.pushed.append((kind, data)))

    def send(self, sat="SAT-01", seq=0, **kw):
        return self.ing.handle_message(topic(sat), make(sat, seq, **kw))

    def sat(self, sat="SAT-01"):
        return Satellite.objects.get(sat_id=sat)

    def test_stores_row_and_registers_satellite(self):
        self.assertEqual(self.ing.handle_message(topic(), REAL_SAMPLE), "stored")
        s = self.sat()
        t = Telemetry.objects.get()
        self.assertEqual((s.packets_received, s.last_sequence, s.application_id), (1, 0, 100))
        self.assertEqual(s.latest_id, t.id)
        self.assertEqual((t.battery_v, t.temp_c, t.obc_status), (8.2, 38.27, "NOMINAL"))
        self.assertEqual(self.pushed[0][0], "telemetry")
        self.assertEqual(self.pushed[0][1]["eps"]["battery_v"], 8.2)

    def test_fleet_is_discovered_dynamically(self):
        for sat in ("SAT-01", "SAT-02", "PK-SAT-9"):
            self.send(sat)
        self.assertEqual(Satellite.objects.count(), 3)
        self.assertEqual(self.ing.stats["satellites_discovered"], 3)

    def test_normal_sequence_and_wraparound(self):
        self.send(seq=65534)
        self.send(seq=65535)
        self.send(seq=0)
        self.assertEqual(self.sat().packets_lost, 0)
        self.assertEqual(self.sat().packets_received, 3)

    def test_duplicate_is_not_stored_twice(self):
        self.send(seq=3)
        self.assertEqual(self.send(seq=3), "duplicate")
        self.assertEqual(Telemetry.objects.count(), 1)
        self.assertEqual(self.sat().packets_received, 1)

    def test_gap_counts_lost_packets_but_still_stores(self):
        self.send(seq=1)
        self.send(seq=5)
        s = self.sat()
        self.assertEqual((s.packets_lost, s.packets_received, s.last_sequence), (3, 2, 5))

    def test_restart_detected(self):
        self.send(seq=170)
        self.send(seq=0, ts="2026-10-12T14:50:00Z")
        s = self.sat()
        self.assertEqual((s.sequence_resets, s.packets_lost, s.last_sequence), (1, 0, 0))

    def test_out_of_order_is_stored_without_resetting_position(self):
        self.send(seq=10)
        self.send(seq=9)
        s = self.sat()
        self.assertEqual((s.last_sequence, s.packets_lost, s.packets_received), (10, 0, 2))

    def test_alert_opens_once_and_resolves(self):
        self.send(seq=0)
        for seq in (1, 2, 3):  # three consecutive hot packets -> ONE alert
            self.send(seq=seq, temp_c=70.0 + seq)
        alerts = Alert.objects.all()
        self.assertEqual(alerts.count(), 1)
        a = alerts.get()
        self.assertEqual((a.kind, a.severity, a.value, a.resolved_at), ("TEMP_HIGH", "warning", 71.0, None))
        self.send(seq=4, ts="2026-10-12T14:38:20Z", temp_c=45.0)
        a.refresh_from_db()
        self.assertIsNotNone(a.resolved_at)
        kinds = [k for k, _ in self.pushed]
        self.assertEqual((kinds.count("alert_opened"), kinds.count("alert_resolved")), (1, 1))

    def test_several_alert_kinds_are_independent(self):
        self.send(seq=0, obc="FAULT", link="LOST")
        self.assertEqual(set(Alert.objects.values_list("kind", flat=True)), {"OBC_FAULT", "COMMS_LOST"})
        self.send(seq=1, link="LOST")  # OBC recovered, link still lost
        self.assertEqual(set(Alert.objects.filter(resolved_at=None).values_list("kind", flat=True)), {"COMMS_LOST"})

    def test_alerts_are_per_satellite(self):
        self.send("SAT-01", 0, temp_c=80)
        self.send("SAT-02", 0, temp_c=80)
        self.assertEqual(Alert.objects.count(), 2)

    def test_state_survives_listener_restart(self):
        self.send(seq=7, temp_c=80)
        fresh = self.new_ingestor()  # new process, same database
        self.assertEqual(fresh.handle_message(topic(), make("SAT-01", 7)), "duplicate")
        fresh.handle_message(topic(), make("SAT-01", 8, temp_c=81))
        self.assertEqual(Alert.objects.count(), 1)  # the still-open alert is not duplicated
        fresh.handle_message(topic(), make("SAT-01", 9, temp_c=40))
        self.assertIsNotNone(Alert.objects.get().resolved_at)

    def test_status_messages(self):
        self.assertEqual(self.ing.handle_message("mps/SAT-01/status", status(state="online")), "status")
        self.assertEqual(self.sat().state, "online")
        self.ing.handle_message("mps/SAT-01/status", status(state="online"))  # unchanged: no event
        self.ing.handle_message("mps/SAT-01/status", status(state="offline"))
        self.assertEqual(self.sat().state, "offline")
        self.assertEqual([k for k, _ in self.pushed], ["status", "status"])

    def test_rejected_messages_are_recorded_and_nothing_else_changes(self):
        self.assertEqual(self.ing.handle_message(topic(), b"garbage"), "rejected")
        self.assertEqual(self.ing.handle_message(topic("SAT-02"), make("SAT-01", 0)), "rejected")  # id mismatch
        self.assertEqual(self.ing.handle_message("mps/SAT-01/status", b'{"sat_id":"SAT-01","state":"x"}'), "rejected")
        self.assertEqual(RejectedMessage.objects.count(), 3)
        self.assertEqual((Telemetry.objects.count(), Satellite.objects.count()), (0, 0))

    def test_rejected_binary_payload_does_not_crash(self):
        self.assertEqual(self.ing.handle_message(topic(), b"\xff\xfe\x00"), "rejected")

    def test_received_at_is_recorded(self):
        when = timezone.now() - timedelta(minutes=5)
        self.ing.handle_message(topic(), REAL_SAMPLE, received_at=when)
        self.assertEqual(Telemetry.objects.get().received_at, when)
