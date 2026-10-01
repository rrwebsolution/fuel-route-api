"""
Chooses where to refuel and how much to buy so total fuel cost is minimal.

Greedy "cheaper station ahead" strategy (optimal for a fixed tank, a linear
price per gallon, and the freedom to buy any amount):

  At each station, look at every station reachable on a full tank.
  * If one of them is cheaper, buy just enough fuel to reach the nearest cheaper one.
  * Otherwise, if the destination is within a full tank, buy just enough to arrive.
  * Otherwise fill the tank here (this is the cheapest fuel in range) and drive
    to the cheapest station within range.

The start is treated as a point where fuel can't be bought: the vehicle drives on
its initial fuel to the first station, and every later decision follows the rules
above. All quantities are Decimals; currency is rounded per stop to cents.
"""

from dataclasses import dataclass, field
from decimal import ROUND_HALF_UP, Decimal

from .exceptions import FuelRangeExceededError, NoFuelStationsError

CENTS = Decimal("0.01")
MILE_PRECISION = Decimal("0.001")


@dataclass(frozen=True)
class FuelStop:
    station: object  # any object with .price (Decimal) and the attributes the caller needs
    miles_from_start: Decimal
    gallons: Decimal
    price_per_gallon: Decimal
    cost: Decimal


@dataclass
class FuelPlan:
    stops: list = field(default_factory=list)
    total_gallons: Decimal = Decimal("0")
    total_cost: Decimal = Decimal("0.00")
    starting_fuel_gallons: Decimal = Decimal("0")
    fuel_used_gallons: Decimal = Decimal("0")


def to_miles(value):
    return Decimal(str(value)).quantize(MILE_PRECISION)


def plan_fuel_stops(
    stations, total_distance_miles, max_range_miles, mpg, start_tank_fraction=1, min_savings_per_gallon=0
):
    """
    Build the cheapest FuelPlan for a trip.

    `stations` are objects with `.miles_from_start` and `.price`; they need not be
    sorted. A station only counts as "cheaper" when it saves more than
    `min_savings_per_gallon`; 0 gives the strictly cheapest plan, a few cents trades a
    negligible cost increase for far fewer stops. Raises FuelRangeExceededError when
    some part of the route is longer than the vehicle can drive between stations.
    """
    threshold = Decimal(str(min_savings_per_gallon))
    mpg = Decimal(str(mpg))
    max_range = Decimal(str(max_range_miles))
    destination = to_miles(total_distance_miles)
    fuel_miles = (max_range * Decimal(str(start_tank_fraction))).quantize(MILE_PRECISION)

    plan = FuelPlan(
        starting_fuel_gallons=fuel_miles / mpg,
        fuel_used_gallons=destination / mpg,
    )
    if destination <= fuel_miles:
        return _finalise(plan)

    route_stations = sorted(
        ((to_miles(s.miles_from_start), s) for s in stations if 0 <= s.miles_from_start <= total_distance_miles),
        key=lambda item: (item[0], item[1].price),
    )
    if not route_stations:
        raise NoFuelStationsError(
            "The trip needs refuelling but no fuel stations were found along the route.",
            distance_miles=float(destination),
            max_range_miles=float(max_range),
        )

    # Drive from the start to the first station on the initial fuel.
    first_position = route_stations[0][0]
    if first_position > fuel_miles:
        raise _range_error(Decimal("0"), first_position, max_range)
    position, current = first_position, 0
    fuel_miles -= first_position

    while True:
        price = route_stations[current][1].price
        reachable = []
        for index in range(current + 1, len(route_stations)):
            if route_stations[index][0] - position > max_range:
                break
            reachable.append(index)
        cheaper = next((i for i in reachable if price - route_stations[i][1].price > threshold), None)

        if cheaper is None and destination - position <= max_range:
            _buy(plan, route_stations[current], destination - position - fuel_miles, mpg)
            return _finalise(plan)

        if cheaper is not None:
            next_index = cheaper
            _buy(plan, route_stations[current], route_stations[next_index][0] - position - fuel_miles, mpg)
        else:
            if not reachable:
                next_position = route_stations[current + 1][0] if current + 1 < len(route_stations) else destination
                raise _range_error(position, next_position, max_range)
            # Head for the cheapest station in range; among (near-)ties prefer the farthest one.
            best_price = min(route_stations[i][1].price for i in reachable)
            next_index = max(i for i in reachable if route_stations[i][1].price - best_price <= threshold)
            # Fill up here, unless the tank already reaches that station and its fuel is
            # no meaningfully dearer than ours (topping up would only add a stop).
            next_is_reachable = route_stations[next_index][0] - position <= fuel_miles
            if not (next_is_reachable and route_stations[next_index][1].price - price <= threshold):
                _buy(plan, route_stations[current], max_range - fuel_miles, mpg)
                fuel_miles = max_range

        next_position = route_stations[next_index][0]
        fuel_miles = max(fuel_miles, next_position - position) - (next_position - position)
        position, current = next_position, next_index


def _buy(plan, route_station, miles, mpg):
    if miles <= 0:
        return
    position, station = route_station
    gallons = miles / mpg
    cost = (gallons * station.price).quantize(CENTS, rounding=ROUND_HALF_UP)
    plan.stops.append(FuelStop(
        station=station,
        miles_from_start=position,
        gallons=gallons,
        price_per_gallon=station.price,
        cost=cost,
    ))


def _finalise(plan):
    plan.total_gallons = sum((stop.gallons for stop in plan.stops), Decimal("0"))
    plan.total_cost = sum((stop.cost for stop in plan.stops), Decimal("0.00"))
    return plan


def _range_error(from_miles, to_miles_, max_range):
    return FuelRangeExceededError(
        f"No fuel station within {max_range} miles after mile {from_miles:.1f} of the route; "
        "the vehicle cannot complete this trip.",
        gap_start_miles=float(from_miles),
        gap_end_miles=float(to_miles_),
        max_range_miles=float(max_range),
    )
