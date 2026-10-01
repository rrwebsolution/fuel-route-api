from decimal import Decimal

from django.test import TestCase

from routing.models import FuelStation


class StationAndHealthApiTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        FuelStation.objects.bulk_create([
            FuelStation(opis_id=1, name="PILOT #1", address="I-35 EXIT 1", city="Dallas", state="TX",
                        retail_price=Decimal("3.20"), latitude=32.7, longitude=-96.8),
            FuelStation(opis_id=2, name="LOVES #2", address="I-10 EXIT 2", city="Houston", state="TX",
                        retail_price=Decimal("2.90"), latitude=29.7, longitude=-95.3),
            FuelStation(opis_id=3, name="PILOT #3", address="I-40 EXIT 3", city="Tulsa", state="OK",
                        retail_price=Decimal("3.50")),
        ])

    def test_health(self):
        for url in ("/api/health", "/api/health/"):
            response = self.client.get(url)
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.json(), {
                "status": "ok", "database": "ok", "fuel_stations": 3, "fuel_stations_with_coordinates": 2,
            })

    def test_station_list_is_paginated(self):
        data = self.client.get("/api/stations").json()
        self.assertEqual(data["count"], 3)
        self.assertEqual([s["opis_id"] for s in data["results"]], [1, 2, 3])
        self.assertEqual(data["results"][1]["retail_price"], 2.9)

    def test_station_list_filters(self):
        def ids(query):
            return [s["opis_id"] for s in self.client.get(f"/api/stations/?{query}").json()["results"]]

        self.assertEqual(ids("state=tx"), [1, 2])
        self.assertEqual(ids("city=Tulsa"), [3])
        self.assertEqual(ids("search=pilot"), [1, 3])
        self.assertEqual(ids("max_price=3.25"), [1, 2])
        self.assertEqual(ids("state=TX&ordering=price"), [2, 1])

    def test_invalid_max_price(self):
        response = self.client.get("/api/stations/?max_price=cheap")
        self.assertEqual(response.status_code, 400)
        self.assertIn("max_price", response.json())

    def test_station_detail(self):
        response = self.client.get("/api/stations/2")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["name"], "LOVES #2")
        self.assertEqual(self.client.get("/api/stations/999/").status_code, 404)

    def test_routes_calculate_endpoint_is_routed(self):
        for url in ("/api/routes/calculate/", "/api/routes/calculate"):
            response = self.client.post(url, {}, content_type="application/json")
            self.assertEqual(response.status_code, 400)  # reaches the route view (validation error)
            self.assertIn("start", response.json()["error"]["fields"])

    def test_post_route_without_trailing_slash_is_routed(self):
        response = self.client.post("/api/route", {}, content_type="application/json")
        self.assertEqual(response.status_code, 400)  # reaches the view (validation error), not 404


class FuelStationCrudTests(TestCase):
    url = "/api/fuel-stations/"

    @classmethod
    def setUpTestData(cls):
        FuelStation.objects.create(
            opis_id=100, name="PILOT #100", address="I-35 EXIT 1", city="Dallas", state="TX",
            rack_id=1, retail_price=Decimal("3.20"), latitude=32.7, longitude=-96.8,
        )

    def payload(self, **overrides):
        data = {
            "opis_id": 200, "name": "LOVES #200", "address": "I-40 EXIT 9", "city": "Amarillo",
            "state": "tx", "rack_id": 5, "retail_price": 3.159,
        }
        data.update(overrides)
        return data

    def test_create_station_resolves_city_coordinates(self):
        response = self.client.post(self.url, self.payload(), content_type="application/json")
        self.assertEqual(response.status_code, 201, response.content)
        data = response.json()
        self.assertEqual(data["state"], "TX")
        self.assertAlmostEqual(data["latitude"], 35.2, delta=0.2)  # Amarillo from the Gazetteer
        self.assertTrue(FuelStation.objects.filter(opis_id=200).exists())

    def test_create_station_with_explicit_coordinates(self):
        body = self.payload(latitude=35.0, longitude=-101.0)
        data = self.client.post(self.url, body, content_type="application/json").json()
        self.assertEqual((data["latitude"], data["longitude"]), (35.0, -101.0))

    def test_create_validation_errors(self):
        cases = [
            self.payload(opis_id=100),                    # duplicate OPIS ID
            self.payload(state="ON"),                     # not a US state
            self.payload(retail_price=0),                 # price must be positive
            self.payload(latitude=35.0),                  # latitude without longitude
            self.payload(latitude=95, longitude=-100),    # out of range
            {"name": "missing fields"},
        ]
        for body in cases:
            with self.subTest(body=body):
                response = self.client.post(self.url, body, content_type="application/json")
                self.assertEqual(response.status_code, 400)

    def test_put_replaces_station(self):
        body = self.payload(opis_id=100, name="PILOT RENAMED", city="Houston", state="TX", retail_price=2.99)
        response = self.client.put(f"{self.url}100/", body, content_type="application/json")
        self.assertEqual(response.status_code, 200, response.content)
        station = FuelStation.objects.get(opis_id=100)
        self.assertEqual(station.name, "PILOT RENAMED")
        self.assertEqual(station.retail_price, Decimal("2.99"))
        self.assertAlmostEqual(station.latitude, 29.8, delta=0.2)  # moved to Houston

    def test_put_requires_all_fields(self):
        response = self.client.put(f"{self.url}100/", {"retail_price": 3.0}, content_type="application/json")
        self.assertEqual(response.status_code, 400)

    def test_patch_updates_only_given_fields(self):
        response = self.client.patch(f"{self.url}100", {"retail_price": 3.05}, content_type="application/json")
        self.assertEqual(response.status_code, 200, response.content)
        station = FuelStation.objects.get(opis_id=100)
        self.assertEqual(station.retail_price, Decimal("3.05"))
        self.assertEqual((station.name, station.latitude), ("PILOT #100", 32.7))

    def test_delete_station(self):
        response = self.client.delete(f"{self.url}100/")
        self.assertEqual(response.status_code, 204)
        self.assertFalse(FuelStation.objects.filter(opis_id=100).exists())
        self.assertEqual(self.client.delete(f"{self.url}100/").status_code, 404)

    def test_list_and_detail(self):
        self.assertEqual(self.client.get(self.url).json()["count"], 1)
        self.assertEqual(self.client.get(f"{self.url}100").json()["name"], "PILOT #100")
