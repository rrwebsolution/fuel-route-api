"""
Orchestrates a trip: geocode -> route -> stations along route -> fuel plan.

External calls per request (uncached): 2 geocoding + 1 routing. Repeated requests
for the same places are served from cache with zero external calls.
"""

from decimal import ROUND_HALF_UP, Decimal

from django.conf import settings

from . import geocoding_service, routing_service
from .exceptions import RouteNotFoundError
from .fuel_optimizer import CENTS, plan_fuel_stops
from .geo import RouteIndex, simplify_line
from .station_service import find_stations_along_route

GALLON_PRECISION = Decimal("0.001")


def plan_trip(start_query, finish_query):
    start = geocoding_service.geocode(start_query)
    finish = geocoding_service.geocode(finish_query)
    route = routing_service.get_route(start, finish)
    if route.distance_miles <= 0 or len(route.coordinates) < 2:
        raise RouteNotFoundError("The start and finish resolve to the same place; there is no route to plan.")

    route_index = RouteIndex(
        route.coordinates,
        max_offset_miles=settings.STATION_CORRIDOR_MILES,
        total_distance_miles=route.distance_miles,
    )
    stations = find_stations_along_route(route_index)
    plan = plan_fuel_stops(
        stations,
        total_distance_miles=route.distance_miles,
        max_range_miles=settings.VEHICLE_MAX_RANGE_MILES,
        mpg=settings.VEHICLE_MPG,
        start_tank_fraction=settings.VEHICLE_START_TANK_FRACTION,
        min_savings_per_gallon=settings.FUEL_MIN_SAVINGS_PER_GALLON,
    )

    fuel_remaining = plan.starting_fuel_gallons + plan.total_gallons - plan.fuel_used_gallons
    trip_cost = trip_fuel_cost(plan, stations)
    return {
        "start": _location(start),
        "finish": _location(finish),
        "distance_miles": round(route.distance_miles, 2),
        "duration_seconds": round(route.duration_seconds),
        "duration": format_duration(route.duration_seconds),
        "route": {
            "type": "LineString",
            "coordinates": simplify_line(route.coordinates, settings.ROUTE_GEOMETRY_SIMPLIFY_TOLERANCE),
        },
        "fuel_stops": [_fuel_stop(stop) for stop in plan.stops],
        "total_gallons": plan.total_gallons.quantize(GALLON_PRECISION),
        "total_fuel_cost": plan.total_cost,
        "total_trip_fuel_cost": trip_cost["total"],
        "fuel_summary": {
            "starting_fuel_gallons": plan.starting_fuel_gallons.quantize(GALLON_PRECISION),
            "starting_fuel_used_gallons": trip_cost["starting_fuel_used"].quantize(GALLON_PRECISION),
            "starting_fuel_price_per_gallon": trip_cost["starting_price"],
            "starting_fuel_cost": trip_cost["starting_cost"],
            "fuel_used_gallons": plan.fuel_used_gallons.quantize(GALLON_PRECISION),
            "fuel_remaining_at_finish_gallons": fuel_remaining.quantize(GALLON_PRECISION),
            "stations_along_route": len(stations),
        },
        "assumptions": {
            "max_range_miles": settings.VEHICLE_MAX_RANGE_MILES,
            "miles_per_gallon": settings.VEHICLE_MPG,
            "start_tank_fraction": settings.VEHICLE_START_TANK_FRACTION,
            "station_corridor_miles": settings.STATION_CORRIDOR_MILES,
            "min_savings_per_gallon": Decimal(settings.FUEL_MIN_SAVINGS_PER_GALLON),
        },
    }


def trip_fuel_cost(plan, stations):
    """
    Cost of all the fuel the trip burns, not just what is bought on the way.

    Fuel bought at stops is used up by the finish, so the rest of the fuel burned came
    from the starting tank. That fuel is valued at the first station along the route,
    i.e. where the driver would have filled up before leaving.
    """
    starting_fuel_used = max(plan.fuel_used_gallons - plan.total_gallons, Decimal("0"))
    if not stations:
        return {"total": None, "starting_fuel_used": starting_fuel_used,
                "starting_price": None, "starting_cost": None}
    starting_price = stations[0].price
    starting_cost = (starting_fuel_used * starting_price).quantize(CENTS, rounding=ROUND_HALF_UP)
    return {
        "total": plan.total_cost + starting_cost,
        "starting_fuel_used": starting_fuel_used,
        "starting_price": starting_price.normalize(),
        "starting_cost": starting_cost,
    }


def format_duration(seconds):
    minutes = round(seconds / 60)
    hours, minutes = divmod(minutes, 60)
    return f"{hours}h {minutes:02d}m"


def _location(location):
    return {
        "query": location.query,
        "display_name": location.display_name,
        "latitude": location.latitude,
        "longitude": location.longitude,
    }


def _fuel_stop(stop):
    station = stop.station
    return {
        "opis_id": station.opis_id,
        "station_name": station.name,
        "address": station.address,
        "city": station.city,
        "state": station.state,
        "latitude": station.latitude,
        "longitude": station.longitude,
        "distance_from_start_miles": round(float(stop.miles_from_start), 1),
        "miles_off_route": round(station.miles_off_route, 1),
        "price_per_gallon": stop.price_per_gallon.normalize(),
        "gallons": stop.gallons.quantize(GALLON_PRECISION),
        "cost": stop.cost,
    }
