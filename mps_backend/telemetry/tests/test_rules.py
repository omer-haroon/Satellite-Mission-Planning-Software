from django.test import SimpleTestCase

from telemetry import rules
from telemetry.validation import parse_telemetry

from .helpers import make


def conds(**over):
    return {c.kind for c in rules.evaluate(parse_telemetry("SAT-01", make(**over)))}


class AlertRuleTests(SimpleTestCase):
    def test_healthy(self):
        self.assertEqual(conds(), set())

    def test_each_condition(self):
        self.assertEqual(conds(temp_c=60.5), {"TEMP_HIGH"})
        self.assertEqual(conds(temp_c=60.0), set())  # rule is strictly > 60
        self.assertEqual(conds(battery_v=6.9), {"BATTERY_LOW"})
        self.assertEqual(conds(battery_v=7.0), set())
        self.assertEqual(conds(obc="FAULT"), {"OBC_FAULT"})
        self.assertEqual(conds(adcs="FAULT"), {"ADCS_FAULT"})
        self.assertEqual(conds(link="LOST"), {"COMMS_LOST"})

    def test_several_at_once(self):
        self.assertEqual(conds(temp_c=70, obc="FAULT"), {"TEMP_HIGH", "OBC_FAULT"})


class SequenceTests(SimpleTestCase):
    def test_verdicts(self):
        c = rules.classify_sequence
        self.assertEqual(c(None, 7), (rules.FIRST, 0))
        self.assertEqual(c(5, 6), (rules.OK, 0))
        self.assertEqual(c(65535, 0), (rules.OK, 0))          # 16-bit wrap is normal
        self.assertEqual(c(5, 5), (rules.DUPLICATE, 0))
        self.assertEqual(c(5, 9), (rules.GAP, 3))
        self.assertEqual(c(65534, 2), (rules.GAP, 3))         # gap across the wrap: 65535, 0, 1
        self.assertEqual(c(171, 0), (rules.RESTART, 0))
        self.assertEqual(c(100, 99), (rules.STALE, 0))        # out of order, never a huge 'loss'
