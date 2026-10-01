from django.urls import re_path

from .views import FuelStationDetailView, FuelStationListView, HealthView, RouteView

# Trailing slash is optional so e.g. /api/health and /api/health/ both work (also for POST).
urlpatterns = [
    re_path(r"^health/?$", HealthView.as_view(), name="health"),
    re_path(r"^routes/calculate/?$", RouteView.as_view(), name="route-calculate"),
    re_path(r"^fuel-stations/?$", FuelStationListView.as_view(), name="fuel-station-list"),
    re_path(r"^fuel-stations/(?P<opis_id>\d+)/?$", FuelStationDetailView.as_view(), name="fuel-station-detail"),
    # Older aliases kept for compatibility.
    re_path(r"^route/?$", RouteView.as_view(), name="route"),
    re_path(r"^stations/?$", FuelStationListView.as_view(), name="station-list"),
    re_path(r"^stations/(?P<opis_id>\d+)/?$", FuelStationDetailView.as_view(), name="station-detail"),
]
