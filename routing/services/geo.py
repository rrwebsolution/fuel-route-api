"""
Pure-Python geographic helpers (no external calls, no GIS dependencies).

Coordinates follow GeoJSON order, [longitude, latitude], unless named otherwise.
"""

import math
from collections import defaultdict
from dataclasses import dataclass

EARTH_RADIUS_MILES = 3958.8
METERS_PER_MILE = 1609.344
MILES_PER_DEGREE_LATITUDE = 69.0


def haversine_miles(lat1, lon1, lat2, lon2):
    """Great-circle distance between two points, in miles."""
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    d_phi = phi2 - phi1
    d_lambda = math.radians(lon2 - lon1)
    a = math.sin(d_phi / 2) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(d_lambda / 2) ** 2
    return 2 * EARTH_RADIUS_MILES * math.asin(math.sqrt(a))


@dataclass(frozen=True)
class BoundingBox:
    min_lat: float
    max_lat: float
    min_lon: float
    max_lon: float


@dataclass(frozen=True)
class RoutePosition:
    miles_from_start: float
    miles_off_route: float


class RouteIndex:
    """
    Answers "how far along the route is the point nearest to (lat, lon)?" quickly.

    The route is resampled into points roughly `sample_spacing_miles` apart (so long,
    straight segments don't leave gaps), and those samples are bucketed into a grid
    whose cells are `max_offset_miles` tall. A query then only inspects the few cells
    around the station instead of every route point.
    """

    def __init__(self, coordinates, max_offset_miles, sample_spacing_miles=1.0, total_distance_miles=None):
        if len(coordinates) < 2:
            raise ValueError("A route needs at least two coordinates.")
        self.max_offset_miles = max_offset_miles
        self.samples = _resample(coordinates, sample_spacing_miles)
        measured_length = self.samples[-1][2]

        # Positions are reported on the routing provider's distance scale so they line
        # up with the total trip distance (the polyline is a slight under-estimate).
        scale = total_distance_miles / measured_length if total_distance_miles and measured_length else 1.0
        self.samples = [(lat, lon, miles * scale) for lat, lon, miles in self.samples]
        self.length_miles = self.samples[-1][2]

        self._cell_degrees = max_offset_miles / MILES_PER_DEGREE_LATITUDE
        self._grid = defaultdict(list)
        for sample in self.samples:
            self._grid[self._cell(sample[0], sample[1])].append(sample)

        lats = [s[0] for s in self.samples]
        lons = [s[1] for s in self.samples]
        self.bounding_box = _expand_bbox(min(lats), max(lats), min(lons), max(lons), max_offset_miles)

    def _cell(self, lat, lon):
        return math.floor(lat / self._cell_degrees), math.floor(lon / self._cell_degrees)

    def locate(self, lat, lon):
        """Return the RoutePosition of the nearest route sample, or None if it is too far away."""
        row, col = self._cell(lat, lon)
        # A degree of longitude shrinks with latitude, so look at more columns further north.
        col_reach = math.ceil(1 / max(math.cos(math.radians(lat)), 0.1))
        best = None
        for r in range(row - 1, row + 2):
            for c in range(col - col_reach, col + col_reach + 1):
                for s_lat, s_lon, miles in self._grid.get((r, c), ()):
                    offset = haversine_miles(lat, lon, s_lat, s_lon)
                    if offset <= self.max_offset_miles and (best is None or offset < best.miles_off_route):
                        best = RoutePosition(miles_from_start=miles, miles_off_route=offset)
        return best


def _resample(coordinates, spacing_miles):
    """Return [(lat, lon, miles_from_start), ...] with points at most ~spacing_miles apart."""
    first_lon, first_lat = coordinates[0]
    samples = [(first_lat, first_lon, 0.0)]
    travelled = 0.0
    for (lon1, lat1), (lon2, lat2) in zip(coordinates, coordinates[1:]):
        segment = haversine_miles(lat1, lon1, lat2, lon2)
        if segment == 0:
            continue
        steps = max(1, math.ceil(segment / spacing_miles))
        for step in range(1, steps + 1):
            fraction = step / steps
            samples.append((
                lat1 + (lat2 - lat1) * fraction,
                lon1 + (lon2 - lon1) * fraction,
                travelled + segment * fraction,
            ))
        travelled += segment
    return samples


def _expand_bbox(min_lat, max_lat, min_lon, max_lon, margin_miles):
    lat_margin = margin_miles / MILES_PER_DEGREE_LATITUDE
    widest_lat = max(abs(min_lat), abs(max_lat))
    lon_margin = margin_miles / (MILES_PER_DEGREE_LATITUDE * max(math.cos(math.radians(widest_lat)), 0.1))
    return BoundingBox(min_lat - lat_margin, max_lat + lat_margin, min_lon - lon_margin, max_lon + lon_margin)


def simplify_line(coordinates, tolerance):
    """
    Douglas-Peucker simplification (planar, in degrees) to shrink the geometry sent to
    clients. Iterative to avoid recursion limits on long routes.
    """
    if len(coordinates) <= 2 or tolerance <= 0:
        return list(coordinates)

    keep = [False] * len(coordinates)
    keep[0] = keep[-1] = True
    stack = [(0, len(coordinates) - 1)]
    while stack:
        first, last = stack.pop()
        x1, y1 = coordinates[first]
        x2, y2 = coordinates[last]
        dx, dy = x2 - x1, y2 - y1
        length = math.hypot(dx, dy)
        max_distance, index = 0.0, None
        for i in range(first + 1, last):
            x0, y0 = coordinates[i]
            if length == 0:
                distance = math.hypot(x0 - x1, y0 - y1)
            else:
                distance = abs(dy * x0 - dx * y0 + x2 * y1 - y2 * x1) / length
            if distance > max_distance:
                max_distance, index = distance, i
        if index is not None and max_distance > tolerance:
            keep[index] = True
            stack.append((first, index))
            stack.append((index, last))
    return [point for point, kept in zip(coordinates, keep) if kept]
