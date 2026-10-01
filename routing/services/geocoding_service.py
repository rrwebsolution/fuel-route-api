"""Turns free-text US locations into coordinates using OpenStreetMap Nominatim."""

import hashlib
from dataclasses import dataclass

from django.conf import settings
from django.core.cache import cache

from .exceptions import LocationNotFoundError
from .http import get_json


@dataclass(frozen=True)
class Location:
    query: str
    latitude: float
    longitude: float
    display_name: str


def _cache_key(query):
    digest = hashlib.sha256(" ".join(query.lower().split()).encode()).hexdigest()
    return f"geocode:{digest}"


def geocode(query):
    """Resolve `query` to a Location inside the USA (results are cached)."""
    key = _cache_key(query)
    cached = cache.get(key)
    if cached is not None:
        return Location(query=query, **cached)

    results = get_json(
        settings.GEOCODING_URL,
        "geocoding",
        params={
            "q": query,
            "format": "jsonv2",
            "countrycodes": "us",  # restricts matches to the USA
            "limit": 1,
        },
    )
    if not results:
        raise LocationNotFoundError(f"Could not find '{query}' in the USA.", location=query)

    match = results[0]
    data = {
        "latitude": float(match["lat"]),
        "longitude": float(match["lon"]),
        "display_name": match.get("display_name", query),
    }
    cache.set(key, data, settings.GEOCODING_CACHE_SECONDS)
    return Location(query=query, **data)
