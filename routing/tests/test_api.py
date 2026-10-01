from decimal import Decimal
from unittest import mock

import requests
from django.core.cache import cache
from django.test import TestCase, override_settings
from django.urls import reverse

from routing.models import FuelStation
from routing.services import http

# A straight route due east along latitude 35 from lon -110 to -90 (~1,130 miles).
ROUTE_COORDINATES = [[-110.0 + i * 0.5, 35.0] for i in range(41)]
ROUTE_DISTANCE_METERS = 1_820_000  # ~1,130.9 miles

GEOCODE_RESULTS = {
    "Flagstaff, AZ": [{"lat": "35.0", "lon": "-110.0", "display_name": "Flagstaff, Arizona, United States"}],
    "Memphis, TN": [{"lat": "35.0", "lon": "-90.0", "display_name": "Memphis, Tennessee, United States"}],
}


def osrm_ok(*_args, **_kwargs):
    return {
        "code": "Ok",
        "routes": [{
            "distance": ROUTE_DISTANCE_METERS,
            "duration": 61_200,
            "geometry": {"type": "LineString", "coordinates": ROUTE_COORDINATES},
        }],
    }


def fake_response(payload, status=200):
    response = mock.Mock(status_code=status)
    response.json.return_value = payload
    return response


class FakeUpstream:
    """Stands in for requests.Session.get: answers Nominatim and OSRM calls, and counts them."""

    def __init__(self, route_payload=None, route_status=200, geocode_results=None):
        self.route_payload = route_payload or osrm_ok()
        self.route_status = route_status
        self.geocode_results = GEOCODE_RESULTS if geocode_results is None else geocode_results
        self.calls = {"geocode": 0, "route": 0}

    def __call__(self, url, params=None, **kwargs):
        if "nominatim" in url:
            self.calls["geocode"] += 1
            return fake_response(self.geocode_results.get(params["q"], []))
        self.calls["route"] += 1
        return fake_response(self.route_payload, self.route_status)


@override_settings(
    GEOCODING_URL="https://nominatim.test/search",
    ROUTING_URL="https://osrm.test/route/v1/driving",
    VEHICLE_START_TANK_FRACTION=1.0,
    STATION_CORRIDOR_MILES=10,
    FUEL_MIN_SAVINGS_PER_GALLON="0",
)
class RouteApiTests(TestCase):
    url = reverse("route")
    body = {"start": "Flagstaff, AZ", "finish": "Memphis, TN"}

    @classmethod
    def setUpTestData(cls):
        def make(opis_id, lon, price, lat=35.0):
            return FuelStation(
                opis_id=opis_id, name=f"Stop {opis_id}", address=f"I-40, EXIT {opis_id}",
                city="Town", state="OK", rack_id=1, retail_price=Decimal(price), latitude=lat, longitude=lon,
            )

        FuelStation.objects.bulk_create([
            make(1, -104.0, "3.80"),   # ~340 mi
            make(2, -102.0, "3.20"),   # ~450 mi
            make(3, -98.0, "3.60"),    # ~680 mi
            make(4, -96.0, "3.10"),    # ~790 mi
            make(5, -93.0, "4.50"),    # ~960 mi
            make(6, -100.0, "1.00", lat=37.0),  # cheap but ~138 mi off the route: must be ignored
        ])

    def setUp(self):
        cache.clear()

    def post(self, upstream, body=None):
        with mock.patch.object(http._session, "get", side_effect=upstream):
            return self.client.post(self.url, body or self.body, content_type="application/json")

    def test_plans_route_with_fuel_stops(self):
        upstream = FakeUpstream()
        response = self.post(upstream)

        self.assertEqual(response.status_code, 200, response.content)
        data = response.json()
        self.assertAlmostEqual(data["distance_miles"], 1130.89, places=1)
        self.assertEqual(data["duration"], "17h 00m")
        self.assertEqual(data["route"]["type"], "LineString")
        # Collinear route points are simplified down to the two endpoints.
        self.assertEqual(data["route"]["coordinates"], [[-110.0, 35.0], [-90.0, 35.0]])

        stops = data["fuel_stops"]
        self.assertEqual([s["station_name"] for s in stops], ["Stop 2", "Stop 4"])
        self.assertNotIn("Stop 6", [s["station_name"] for s in stops])
        for stop in stops:
            self.assertAlmostEqual(stop["cost"], stop["gallons"] * stop["price_per_gallon"], delta=0.01)
        self.assertAlmostEqual(data["total_fuel_cost"], sum(s["cost"] for s in stops), places=2)
        self.assertAlmostEqual(data["total_gallons"], sum(s["gallons"] for s in stops), places=3)
        self.assertEqual(data["fuel_summary"]["fuel_remaining_at_finish_gallons"], 0)

    def test_external_calls_are_minimal_and_cached(self):
        upstream = FakeUpstream()
        self.post(upstream)
        self.assertEqual(upstream.calls, {"geocode": 2, "route": 1})

        self.post(upstream)
        self.assertEqual(upstream.calls, {"geocode": 2, "route": 1}, "second request should be fully cached")

    def test_missing_start_and_finish(self):
        response = self.client.post(self.url, {}, content_type="application/json")
        self.assertEqual(response.status_code, 400)
        fields = response.json()["error"]["fields"]
        self.assertIn("start", fields)
        self.assertIn("finish", fields)

    def test_blank_finish(self):
        response = self.client.post(self.url, {"start": "Flagstaff, AZ", "finish": "  "},
                                    content_type="application/json")
        self.assertEqual(response.status_code, 400)
        self.assertIn("finish", response.json()["error"]["fields"])

    def test_same_start_and_finish(self):
        response = self.client.post(self.url, {"start": "Memphis, TN", "finish": " memphis,   TN "},
                                    content_type="application/json")
        self.assertEqual(response.status_code, 400)

    def test_malformed_json(self):
        response = self.client.post(self.url, "{not json", content_type="application/json")
        self.assertEqual(response.status_code, 400)

    def test_get_not_allowed(self):
        self.assertEqual(self.client.get(self.url).status_code, 405)

    def test_location_not_found_or_outside_usa(self):
        response = self.post(FakeUpstream(), {"start": "Toronto, ON", "finish": "Memphis, TN"})
        self.assertEqual(response.status_code, 422)
        self.assertEqual(response.json()["error"]["code"], "location_not_found")

    def test_route_not_found(self):
        response = self.post(FakeUpstream(route_payload={"code": "NoRoute", "routes": []}, route_status=400))
        self.assertEqual(response.status_code, 422)
        self.assertEqual(response.json()["error"]["code"], "route_not_found")

    def test_routing_service_failure(self):
        response = self.post(FakeUpstream(route_payload={"message": "boom"}, route_status=503))
        self.assertEqual(response.status_code, 502)
        self.assertEqual(response.json()["error"]["code"], "external_service_error")

    def test_routing_service_timeout(self):
        def upstream(url, params=None, **kwargs):
            if "nominatim" in url:
                return fake_response(GEOCODE_RESULTS[params["q"]])
            raise requests.Timeout()

        response = self.post(upstream)
        self.assertEqual(response.status_code, 504)
        self.assertEqual(response.json()["error"]["code"], "external_service_timeout")

    def test_impossible_gap_between_stations(self):
        FuelStation.objects.filter(opis_id__in=[3, 4, 5]).delete()  # nothing after mile ~450
        response = self.post(FakeUpstream())
        self.assertEqual(response.status_code, 422)
        error = response.json()["error"]
        self.assertEqual(error["code"], "fuel_range_exceeded")
        self.assertEqual(error["max_range_miles"], 500)

    def test_no_stations_along_route(self):
        FuelStation.objects.all().delete()
        response = self.post(FakeUpstream())
        self.assertEqual(response.status_code, 422)
        self.assertEqual(response.json()["error"]["code"], "no_fuel_stations")
