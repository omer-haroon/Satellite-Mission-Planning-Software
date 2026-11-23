"""Read-only REST API (JSON). Filters follow the Frontend/Backend matrix:
date range + sat_id, server-side pagination, structured errors."""
from datetime import timedelta, timezone as dt_timezone

from django.conf import settings
from django.db import DatabaseError
from django.db.models import Avg, Count, Max, Min, Q
from django.db.models.functions import TruncDay, TruncHour, TruncMinute
from django.utils import timezone
from django.utils.dateparse import parse_datetime
from rest_framework import serializers
from rest_framework.exceptions import ValidationError
from rest_framework.generics import ListAPIView, RetrieveAPIView
from rest_framework.pagination import LimitOffsetPagination
from rest_framework.response import Response
from rest_framework.reverse import reverse
from rest_framework.views import APIView

from .models import Alert, RejectedMessage, Satellite, Telemetry
from .payloads import alert_payload, iso, satellite_payload, telemetry_payload


# ---------------------------------------------------------------- serializers
class _Wrap(serializers.BaseSerializer):
    """Delegates to the shared payload functions so REST and WebSocket output match."""

    def to_representation(self, obj):
        raise NotImplementedError


class TelemetrySerializer(_Wrap):
    def to_representation(self, obj):
        return telemetry_payload(obj)


class AlertSerializer(_Wrap):
    def to_representation(self, obj):
        return alert_payload(obj)


class SatelliteSerializer(_Wrap):
    def to_representation(self, obj):
        return satellite_payload(obj, timezone.now(), settings.MPS_STALE_AFTER_S)


class BigPagination(LimitOffsetPagination):
    default_limit = 100
    max_limit = 1000


# -------------------------------------------------------------------- helpers
def _datetime_param(request, name):
    raw = request.query_params.get(name)
    if raw in (None, ""):
        return None
    dt = parse_datetime(raw) or parse_datetime(raw.replace(" ", "+"))  # '+' in a URL arrives as ' '
    if dt is None:
        raise ValidationError({name: "use ISO 8601, e.g. 2026-10-12T14:30:00Z"})
    if timezone.is_naive(dt):
        dt = dt.replace(tzinfo=dt_timezone.utc)
    return dt


def _bool_param(request, name):
    raw = request.query_params.get(name)
    if raw is None or raw == "":
        return None
    if raw.lower() in ("1", "true", "yes"):
        return True
    if raw.lower() in ("0", "false", "no"):
        return False
    raise ValidationError({name: "use true or false"})


def satellite_queryset():
    return (Satellite.objects.select_related("latest")
            .annotate(active_alerts=Count("alerts", filter=Q(alerts__resolved_at__isnull=True))))


# ---------------------------------------------------------------------- views
class ApiRoot(APIView):
    def get(self, request):
        r = lambda name, **kw: reverse(name, kwargs=kw or None, request=request)  # noqa: E731
        return Response({
            "health": r("health"),
            "satellites": r("satellite-list"),
            "satellite_detail": r("satellite-list") + "{sat_id}/",
            "telemetry": r("telemetry-list") + "?sat_id=&from=&to=&order=&limit=&offset=",
            "telemetry_aggregate": r("telemetry-aggregate") + "?sat_id=&bucket=minute|hour|day&from=&to=",
            "alerts": r("alert-list") + "?sat_id=&active=&severity=&kind=&from=&to=",
            "websocket": "ws://<host>/ws/telemetry/?sat_id=<optional>",
        })


class Health(APIView):
    def get(self, request):
        now = timezone.now()
        try:
            last = Telemetry.objects.order_by("-received_at").values_list("received_at", flat=True).first()
            age = None if last is None else max(0.0, (now - last).total_seconds())
            body = {
                "status": "ok",
                "database": "ok",
                "time": iso(now),
                "satellites": Satellite.objects.count(),
                "satellites_online": Satellite.objects.filter(state="online").count(),
                "active_alerts": Alert.objects.filter(resolved_at__isnull=True).count(),
                "last_packet_at": iso(last),
                "seconds_since_last_packet": None if age is None else round(age, 1),
                # True while telemetry is flowing in, i.e. the MQTT listener is alive and fed
                "ingest_active": age is not None and age <= settings.MPS_STALE_AFTER_S,
                "rejected_last_hour": RejectedMessage.objects.filter(
                    received_at__gte=now - timedelta(hours=1)).count(),
            }
        except DatabaseError:
            return Response({"status": "error", "database": "error", "time": iso(now)}, status=503)
        return Response(body)


class SatelliteList(ListAPIView):
    serializer_class = SatelliteSerializer
    pagination_class = None  # the fleet is small; dropdowns want all of it
    queryset = satellite_queryset()


class SatelliteDetail(RetrieveAPIView):
    serializer_class = SatelliteSerializer
    lookup_field = "sat_id"
    queryset = satellite_queryset()


class TelemetryList(ListAPIView):
    """GET /api/telemetry/?sat_id=SAT-01&from=...&to=...&order=asc|desc&limit=&offset=
    (also /api/satellites/<sat_id>/telemetry/). Newest first by default."""

    serializer_class = TelemetrySerializer
    pagination_class = BigPagination

    def get_queryset(self):
        qs = Telemetry.objects.select_related("satellite")
        sat_id = self.kwargs.get("sat_id") or self.request.query_params.get("sat_id")
        if sat_id:
            qs = qs.filter(satellite__sat_id=sat_id)
        start, end = _datetime_param(self.request, "from"), _datetime_param(self.request, "to")
        if start:
            qs = qs.filter(timestamp__gte=start)
        if end:
            qs = qs.filter(timestamp__lte=end)
        order = self.request.query_params.get("order", "desc").lower()
        if order not in ("asc", "desc"):
            raise ValidationError({"order": "use asc or desc"})
        return qs.order_by("timestamp", "id") if order == "asc" else qs.order_by("-timestamp", "-id")


class TelemetryAggregate(APIView):
    """GET /api/telemetry/aggregate/?sat_id=SAT-01&bucket=minute|hour|day&from=...&to=...

    Time-bucketed avg/min/max per metric, computed in the database, for graphs over long
    ranges (raw data is ~86,000 rows per satellite per day; the raw endpoint caps at 1000).
    `sat_id` is required. At most MAX_BUCKETS buckets are returned: narrow the range or
    use a larger bucket otherwise."""

    BUCKETS = {"minute": TruncMinute, "hour": TruncHour, "day": TruncDay}
    METRICS = ("battery_v", "battery_i", "temp_c", "sensor_value")
    MAX_BUCKETS = 5000

    def get(self, request):
        sat_id = request.query_params.get("sat_id")
        if not sat_id:
            raise ValidationError({"sat_id": "required"})
        bucket = request.query_params.get("bucket", "minute").lower()
        if bucket not in self.BUCKETS:
            raise ValidationError({"bucket": "use minute, hour or day"})
        qs = Telemetry.objects.filter(satellite__sat_id=sat_id)
        start, end = _datetime_param(request, "from"), _datetime_param(request, "to")
        if start:
            qs = qs.filter(timestamp__gte=start)
        if end:
            qs = qs.filter(timestamp__lte=end)
        aggs = {"count": Count("id")}
        for m in self.METRICS:
            aggs.update({f"{m}_avg": Avg(m), f"{m}_min": Min(m), f"{m}_max": Max(m)})
        rows = list(
            qs.annotate(b=self.BUCKETS[bucket]("timestamp", tzinfo=dt_timezone.utc))
            .values("b").annotate(**aggs).order_by("b")[: self.MAX_BUCKETS + 1]
        )
        if len(rows) > self.MAX_BUCKETS:
            raise ValidationError({"range": f"more than {self.MAX_BUCKETS} buckets: narrow from/to or use a larger bucket"})
        results = [
            {"bucket_start": iso(r["b"]), "count": r["count"],
             **{m: {"avg": round(r[f"{m}_avg"], 3), "min": r[f"{m}_min"], "max": r[f"{m}_max"]}
                for m in self.METRICS}}
            for r in rows
        ]
        return Response({"sat_id": sat_id, "bucket": bucket, "count": len(results), "results": results})


class AlertList(ListAPIView):
    """GET /api/alerts/?sat_id=&active=true|false&severity=&kind=&from=&to=
    (also /api/satellites/<sat_id>/alerts/). Newest first."""

    serializer_class = AlertSerializer
    pagination_class = BigPagination

    def get_queryset(self):
        qs = Alert.objects.select_related("satellite")
        params = self.request.query_params
        sat_id = self.kwargs.get("sat_id") or params.get("sat_id")
        if sat_id:
            qs = qs.filter(satellite__sat_id=sat_id)
        active = _bool_param(self.request, "active")
        if active is not None:
            qs = qs.filter(resolved_at__isnull=active)
        if params.get("severity"):
            qs = qs.filter(severity=params["severity"])
        if params.get("kind"):
            qs = qs.filter(kind=params["kind"].upper())
        start, end = _datetime_param(self.request, "from"), _datetime_param(self.request, "to")
        if start:
            qs = qs.filter(opened_at__gte=start)
        if end:
            qs = qs.filter(opened_at__lte=end)
        return qs.order_by("-opened_at", "-id")
