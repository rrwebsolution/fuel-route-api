"""
Offline city/state -> coordinates lookup used when importing fuel stations.

The fuel-price CSV has no latitude/longitude, only a highway-exit style address
plus city and state. Stations are therefore positioned at their city's centroid,
taken from the US Census Bureau Gazetteer "places" file (public domain), with an
optional overrides file for cities the Gazetteer does not cover.
"""

import csv
import re
from functools import lru_cache
from pathlib import Path

from django.conf import settings

DATA_DIR = Path(settings.BASE_DIR) / "data"
PLACES_PATH = DATA_DIR / "us_places.csv"
OVERRIDES_PATH = DATA_DIR / "city_coordinate_overrides.csv"

US_STATES = {
    "AL", "AK", "AZ", "AR", "CA", "CO", "CT", "DE", "DC", "FL", "GA", "HI", "ID", "IL", "IN", "IA", "KS",
    "KY", "LA", "ME", "MD", "MA", "MI", "MN", "MS", "MO", "MT", "NE", "NV", "NH", "NJ", "NM", "NY", "NC",
    "ND", "OH", "OK", "OR", "PA", "RI", "SC", "SD", "TN", "TX", "UT", "VT", "VA", "WA", "WV", "WI", "WY",
}

# Census names end with a legal/statistical area suffix, e.g. "Oklahoma City city",
# "Abanda CDP" or "Nashville-Davidson metropolitan government (balance)".
PLACE_SUFFIX_RE = re.compile(
    r"\s+(city and borough|consolidated government|metropolitan government|unified government|"
    r"urban county|city|town|village|CDP|borough|municipality|plantation|corporation)?"
    r"(\s*\(balance\))?$",
    re.IGNORECASE,
)
WORD_REPLACEMENTS = [(r"\bSAINT\b", "ST"), (r"\bFORT\b", "FT"), (r"\bMOUNT\b", "MT")]


def normalize_city(name):
    """Normalize a city name so "Mc Calla", "McCalla" and "St. Johns"/"Saint Johns" compare equal."""
    value = name.upper()
    for pattern, replacement in WORD_REPLACEMENTS:
        value = re.sub(pattern, replacement, value)
    return re.sub(r"[^A-Z0-9]", "", value)


class CityCoordinateIndex:
    def __init__(self):
        self._coordinates = {}

    def lookup(self, city, state):
        return self._coordinates.get((state.upper(), normalize_city(city)))

    def add(self, city, state, latitude, longitude, overwrite=True):
        key = (state.upper(), normalize_city(city))
        if overwrite or key not in self._coordinates:
            self._coordinates[key] = (float(latitude), float(longitude))

    def __len__(self):
        return len(self._coordinates)

    @classmethod
    def from_files(cls, places_path, overrides_path=None):
        index = cls()
        aliases = []
        with open(places_path, encoding="utf-8", newline="") as handle:
            for row in csv.DictReader(handle):
                base_name = PLACE_SUFFIX_RE.sub("", row["name"]).strip()
                index.add(base_name, row["state"], row["latitude"], row["longitude"], overwrite=False)
                # Some names legitimately end in a suffix word, e.g. "Carson City".
                aliases.append((row["name"], row))
                # Consolidated governments, e.g. "Nashville-Davidson metropolitan government (balance)".
                if "-" in base_name:
                    aliases.append((base_name.split("-")[0], row))
                # e.g. "Boise City city" is listed by the CSV as "Boise".
                if base_name.upper().endswith(" CITY"):
                    aliases.append((base_name[:-5], row))
        for alias, row in aliases:
            index.add(alias, row["state"], row["latitude"], row["longitude"], overwrite=False)

        if overrides_path and overrides_path.exists():
            with open(overrides_path, encoding="utf-8", newline="") as handle:
                for row in csv.DictReader(handle):
                    index.add(row["city"], row["state"], row["latitude"], row["longitude"])
        return index


@lru_cache(maxsize=1)
def default_city_index():
    """The bundled Gazetteer + overrides index, loaded once per process."""
    return CityCoordinateIndex.from_files(PLACES_PATH, OVERRIDES_PATH)
