"""Dump stored telemetry to CSV (one row per packet, oldest first) for offline analysis,
e.g. training / evaluating the anomaly-detection model.

    python manage.py export_telemetry --out telemetry.csv
    python manage.py export_telemetry --sat SAT-01 --from 2026-11-01T00:00:00Z --to 2026-11-14T00:00:00Z
"""
import csv

from django.core.management.base import BaseCommand, CommandError
from django.utils.dateparse import parse_datetime

from telemetry.models import Telemetry
from telemetry.payloads import iso

COLUMNS = ["sat_id", "timestamp", "received_at", "sequence", "battery_v", "battery_i", "temp_c",
           "obc_status", "adcs_mode", "link_status", "payload_on", "sensor_value"]


def _dt(raw, flag):
    dt = parse_datetime(raw) if raw else None
    if raw and (dt is None or dt.tzinfo is None):
        raise CommandError(f"{flag}: use ISO 8601 with a timezone, e.g. 2026-11-14T00:00:00Z")
    return dt


class Command(BaseCommand):
    help = "Export stored telemetry to CSV (stdout by default)."

    def add_arguments(self, parser):
        parser.add_argument("--sat", help="only this satellite id, e.g. SAT-01")
        parser.add_argument("--from", dest="start", help="ISO 8601, inclusive")
        parser.add_argument("--to", dest="end", help="ISO 8601, inclusive")
        parser.add_argument("--out", help="output file (default: stdout)")

    def handle(self, *args, **opts):
        qs = Telemetry.objects.select_related("satellite").order_by("satellite__sat_id", "timestamp", "id")
        if opts["sat"]:
            qs = qs.filter(satellite__sat_id=opts["sat"])
        if (start := _dt(opts["start"], "--from")):
            qs = qs.filter(timestamp__gte=start)
        if (end := _dt(opts["end"], "--to")):
            qs = qs.filter(timestamp__lte=end)
        # stdout wrapper appends its own newline per write(), so the csv writer adds none there
        out = open(opts["out"], "w", newline="", encoding="utf-8") if opts["out"] else self.stdout
        try:
            w = csv.writer(out, lineterminator="\n" if opts["out"] else "")
            w.writerow(COLUMNS)
            n = 0
            for t in qs.iterator(chunk_size=5000):
                w.writerow([t.satellite.sat_id, iso(t.timestamp), iso(t.received_at), t.sequence,
                            t.battery_v, t.battery_i, t.temp_c, t.obc_status, t.adcs_mode,
                            t.link_status, int(t.payload_on), t.sensor_value])
                n += 1
        finally:
            if opts["out"]:
                out.close()
        if opts["out"]:
            self.stderr.write(f"exported {n} rows to {opts['out']}")
