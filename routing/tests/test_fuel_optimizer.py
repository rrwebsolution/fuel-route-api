from dataclasses import dataclass
from decimal import Decimal

from django.test import SimpleTestCase

from routing.services.exceptions import FuelRangeExceededError, NoFuelStationsError
from routing.services.fuel_optimizer import plan_fuel_stops

MAX_RANGE = 500
MPG = 10


@dataclass(frozen=True)
class Station:
    name: str
    miles_from_start: float
    price: Decimal


def station(name, miles, price):
    return Station(name, miles, Decimal(price))


def assert_never_runs_dry(test, plan, distance, start_fraction=1):
    """Replay the plan and check the tank never goes negative or above capacity."""
    capacity = Decimal(MAX_RANGE) / MPG
    fuel = capacity * Decimal(str(start_fraction))
    position = Decimal(0)
    for stop in plan.stops:
        fuel -= (stop.miles_from_start - position) / MPG
        test.assertGreaterEqual(fuel, 0, f"ran out of fuel before {stop.station.name}")
        fuel += stop.gallons
        test.assertLessEqual(fuel, capacity, f"overfilled at {stop.station.name}")
        position = stop.miles_from_start
    fuel -= (Decimal(str(distance)) - position) / MPG
    test.assertGreaterEqual(fuel, 0, "ran out of fuel before the destination")


class FuelOptimizerTests(SimpleTestCase):
    def plan(self, stations, distance, start_fraction=1):
        return plan_fuel_stops(stations, distance, MAX_RANGE, MPG, start_fraction)

    def test_short_trip_on_a_full_tank_needs_no_fuel(self):
        plan = self.plan([station("A", 100, "3.00")], distance=450)
        self.assertEqual(plan.stops, [])
        self.assertEqual(plan.total_cost, Decimal("0.00"))
        self.assertEqual(plan.fuel_used_gallons, Decimal("45"))

    def test_buys_only_enough_to_reach_a_cheaper_station(self):
        stations = [
            station("expensive", 400, "4.00"),
            station("cheap", 700, "3.00"),
        ]
        plan = self.plan(stations, distance=1000)

        names = [stop.station.name for stop in plan.stops]
        self.assertEqual(names, ["expensive", "cheap"])
        # Arrive at 400 with 10 gal; need 30 gal to cover the 300 miles to "cheap".
        self.assertEqual(plan.stops[0].gallons, Decimal("20"))
        # From 700 the destination (300 miles) is in range: buy exactly 30 gal.
        self.assertEqual(plan.stops[1].gallons, Decimal("30"))
        assert_never_runs_dry(self, plan, 1000)

    def test_fills_up_at_cheapest_station_when_nothing_cheaper_is_ahead(self):
        stations = [
            station("cheap", 450, "3.00"),
            station("pricier", 800, "4.00"),
            station("priciest", 900, "5.00"),
        ]
        plan = self.plan(stations, distance=1200)

        self.assertEqual(plan.stops[0].station.name, "cheap")
        self.assertEqual(plan.stops[0].gallons, Decimal("45"))  # 5 gal left + 45 = full tank
        self.assertEqual(plan.stops[1].station.name, "pricier")
        self.assertEqual(plan.stops[1].gallons, Decimal("25"))  # just enough for the last 400 miles
        assert_never_runs_dry(self, plan, 1200)

    def test_prefers_cheapest_reachable_station(self):
        stations = [
            station("A", 300, "3.50"),
            station("B", 450, "3.10"),
            station("C", 480, "3.90"),
            station("D", 900, "3.20"),
        ]
        plan = self.plan(stations, distance=1300)
        self.assertNotIn("C", [stop.station.name for stop in plan.stops])
        self.assertEqual(plan.stops[0].station.name, "B")
        assert_never_runs_dry(self, plan, 1300)

    def test_every_leg_respects_500_mile_range_on_a_long_route(self):
        stations = [station(f"S{m}", m, f"{3 + (m % 7) / 10:.2f}") for m in range(120, 2800, 130)]
        plan = self.plan(stations, distance=2800)
        assert_never_runs_dry(self, plan, 2800)
        positions = [Decimal(0)] + [stop.miles_from_start for stop in plan.stops] + [Decimal(2800)]
        for previous, current in zip(positions, positions[1:]):
            self.assertLessEqual(current - previous, MAX_RANGE)

    def test_total_cost_uses_each_stops_own_price(self):
        stations = [station("A", 400, "3.333"), station("B", 800, "4.10")]
        plan = self.plan(stations, distance=1200)

        expected = sum((stop.gallons * stop.price_per_gallon).quantize(Decimal("0.01")) for stop in plan.stops)
        self.assertEqual(plan.total_cost, expected)
        self.assertEqual(plan.total_gallons, sum(stop.gallons for stop in plan.stops))
        # 1200 miles at 10 mpg = 120 gal, 50 of which came from the starting tank.
        self.assertEqual(plan.total_gallons, Decimal("70"))
        self.assertEqual(plan.stops[0].cost, Decimal("133.32"))  # 40 gal * 3.333 = 133.32

    def test_gap_longer_than_range_raises(self):
        stations = [station("A", 300, "3.00"), station("B", 900, "3.00")]
        with self.assertRaises(FuelRangeExceededError) as ctx:
            self.plan(stations, distance=1200)
        self.assertEqual(ctx.exception.details["gap_start_miles"], 300.0)

    def test_first_station_out_of_initial_range_raises(self):
        with self.assertRaises(FuelRangeExceededError):
            self.plan([station("A", 600, "3.00")], distance=1000)

    def test_last_station_cannot_reach_destination_raises(self):
        with self.assertRaises(FuelRangeExceededError):
            self.plan([station("A", 400, "3.00")], distance=1000)

    def test_no_stations_on_long_route_raises(self):
        with self.assertRaises(NoFuelStationsError):
            self.plan([], distance=800)

    def test_savings_threshold_skips_marginally_cheaper_stops(self):
        stations = [
            station("A", 400, "3.00"),
            station("B", 600, "2.98"),  # only 2 cents cheaper
            station("C", 850, "3.01"),  # within 5 cents of B and farther along
        ]
        strict = self.plan(stations, distance=1300)
        relaxed = plan_fuel_stops(stations, 1300, MAX_RANGE, MPG, min_savings_per_gallon=Decimal("0.05"))

        self.assertEqual([s.station.name for s in strict.stops], ["A", "B", "C"])
        self.assertEqual([s.station.name for s in relaxed.stops], ["A", "C"])
        self.assertLessEqual(strict.total_cost, relaxed.total_cost)
        assert_never_runs_dry(self, relaxed, 1300)

    def test_savings_threshold_avoids_tiny_top_ups(self):
        stations = [
            station("A", 10, "3.00"),
            station("B", 400, "3.05"),
            station("C", 800, "3.50"),
        ]
        strict = self.plan(stations, distance=1100)
        relaxed = plan_fuel_stops(stations, 1100, MAX_RANGE, MPG, min_savings_per_gallon=Decimal("0.10"))

        self.assertEqual(strict.stops[0].station.name, "A")
        self.assertEqual(strict.stops[0].gallons, Decimal("1"))  # 1-gallon top-up 10 miles in
        self.assertEqual([s.station.name for s in relaxed.stops], ["B", "C"])
        assert_never_runs_dry(self, relaxed, 1100)

    def test_partial_starting_tank_is_configurable(self):
        stations = [station("A", 100, "3.00")]
        plan = self.plan(stations, distance=550, start_fraction=0.5)
        # 250 miles of fuel at start; at mile 100 we hold 15 gal and need 45 to finish.
        self.assertEqual(plan.stops[0].gallons, Decimal("30"))
        assert_never_runs_dry(self, plan, 550, start_fraction=0.5)
