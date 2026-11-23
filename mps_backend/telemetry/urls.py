from django.urls import path

from . import views

urlpatterns = [
    path("", views.ApiRoot.as_view(), name="api-root"),
    path("health/", views.Health.as_view(), name="health"),
    path("satellites/", views.SatelliteList.as_view(), name="satellite-list"),
    path("satellites/<str:sat_id>/", views.SatelliteDetail.as_view(), name="satellite-detail"),
    path("satellites/<str:sat_id>/telemetry/", views.TelemetryList.as_view(), name="satellite-telemetry"),
    path("satellites/<str:sat_id>/alerts/", views.AlertList.as_view(), name="satellite-alerts"),
    path("telemetry/aggregate/", views.TelemetryAggregate.as_view(), name="telemetry-aggregate"),
    path("telemetry/", views.TelemetryList.as_view(), name="telemetry-list"),
    path("alerts/", views.AlertList.as_view(), name="alert-list"),
]
