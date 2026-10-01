"""Driving routes from the public OSRM server: one HTTP call per (uncached) route."""

from dataclasses import dataclass

from django.conf import settings
from django.core.cache import cache

from .exceptions import ExternalServiceError, RouteNotFoundError
from .geo import METERS_PER_MILE
from .http import get_json

# OSRM codes meaning "no drivable route between these points" rather than a server fault.
NO_ROUTE_CODES = {"NoRoute", "NoSegment", "NoMatch"}


@dataclass(frozen=True)
class Route:
    coordinates: list  # [[longitude, latitude], ...] in GeoJSON order
    distance_miles: float
    duration_seconds: float


def get_route(start, finish):
    """Fetch the driving route between two Locations, with full-resolution geometry."""
    coordinates = f"{start.longitude:.6f},{start.latitude:.6f};{finish.longitude:.6f},{finish.latitude:.6f}"
    key = f"route:{coordinates}"
    cached = cache.get(key)
    if cached is not None:
        return Route(**cached)

    # OSRM answers 400 with a JSON "code" for unroutable requests, so accept it here.
    payload = get_json(
        f"{settings.ROUTING_URL}/{coordinates}",
        "routing",
        params={"overview": "full", "geometries": "geojson", "steps": "false", "alternatives": "false"},
        accepted_statuses=(200, 400),
    )
    code = payload.get("code")
    if code in NO_ROUTE_CODES or (code == "Ok" and not payload.get("routes")):
        raise RouteNotFoundError("No driving route was found between the start and finish locations.")
    if code != "Ok":
        raise ExternalServiceError("The routing service could not calculate the route.", upstream_code=code)

    best = payload["routes"][0]
    data = {
        "coordinates": best["geometry"]["coordinates"],
        "distance_miles": best["distance"] / METERS_PER_MILE,
        "duration_seconds": best["duration"],
    }
    cache.set(key, data, settings.ROUTING_CACHE_SECONDS)
    return Route(**data)
