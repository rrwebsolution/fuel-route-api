"""Finds the imported fuel stations that sit along a calculated route."""

from dataclasses import dataclass
from decimal import Decimal

from routing.models import FuelStation

from .geo import RouteIndex

STATION_FIELDS = ("id", "opis_id", "name", "address", "city", "state", "retail_price", "latitude", "longitude")


@dataclass(frozen=True)
class RouteStation:
    id: int
    opis_id: int
    name: str
    address: str
    city: str
    state: str
    price: Decimal
    latitude: float
    longitude: float
    miles_from_start: float
    miles_off_route: float


def find_stations_along_route(route_index: RouteIndex):
    """
    Return stations within the route corridor, ordered by distance from the start.

    One indexed bounding-box query narrows thousands of stations down to the
    route's area; the exact corridor test then runs in memory against the route
    index, so no per-station database or API calls are made.
    """
    box = route_index.bounding_box
    candidates = (
        FuelStation.objects.filter(
            latitude__range=(box.min_lat, box.max_lat),
            longitude__range=(box.min_lon, box.max_lon),
        )
        .values(*STATION_FIELDS)
    )

    stations = []
    for row in candidates:
        position = route_index.locate(row["latitude"], row["longitude"])
        if position is None:
            continue
        stations.append(RouteStation(
            id=row["id"],
            opis_id=row["opis_id"],
            name=row["name"],
            address=row["address"],
            city=row["city"],
            state=row["state"],
            price=row["retail_price"],
            latitude=row["latitude"],
            longitude=row["longitude"],
            miles_from_start=position.miles_from_start,
            miles_off_route=position.miles_off_route,
        ))
    stations.sort(key=lambda s: (s.miles_from_start, s.price))
    return stations
