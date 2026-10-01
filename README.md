# Fuel Route API

A Django REST API that plans a driving trip between two US locations and picks
**cost-effective fuel stops** along the way, using real truck-stop diesel prices.

```
POST /api/routes/calculate/   {"start": "New York, NY", "finish": "Los Angeles, CA"}
```

returns the route geometry (GeoJSON, ready for Leaflet/Mapbox/Google Maps), distance,
duration, the fuel stops to make, how many gallons to buy at each, and the fuel cost.
It also returns a `map_url`: an interactive map of the route with every fuel stop marked.

---

## Contents

- [Architecture](#architecture)
- [Technologies](#technologies)
- [Setup](#setup)
- [Importing the fuel price CSV](#importing-the-fuel-price-csv)
- [API](#api)
- [How it works](#how-it-works)
- [Assumptions](#assumptions)
- [Performance](#performance)
- [Tests](#tests)
- [Known limitations and trade-offs](#known-limitations-and-trade-offs)

---

## Architecture

```
config/                     Django project (settings read from environment / .env)
routing/
    models.py               FuelStation model + indexes
    serializers.py          Route request + fuel station validation
    views.py                Thin views: route calculation, route map, fuel-station CRUD, health
    urls.py                 /api/health/, /api/routes/calculate/, /api/routes/map/, /api/fuel-stations/
    templates/routing/
        route_map.html      Leaflet + OpenStreetMap page for the route and fuel stops
    services/
        trip_planner.py     Orchestrates geocode -> route -> stations -> fuel plan
        geocoding_service.py  Nominatim geocoding (USA only, cached)
        routing_service.py  OSRM driving route (1 call, cached)
        station_service.py  Stations within the route corridor (1 DB query)
        fuel_optimizer.py   Pure fuel-stop optimisation (Decimal maths)
        geo.py              Haversine, route resampling + grid index, line simplification
        city_coordinates.py Offline city/state -> lat/lon lookup (CSV import + station API)
        http.py             Shared HTTP helper: timeouts and error translation
        exceptions.py       Domain errors, each with an HTTP status
    management/commands/
        import_fuel_prices.py  Idempotent CSV import
    tests/                  Unit and API tests (external APIs mocked)
data/
    fuel-prices-for-be-assessment.csv   Supplied price list
    us_places.csv                       US Census Gazetteer places (public domain)
    city_coordinate_overrides.csv       Coordinates for cities missing from the Gazetteer
postman/
    fuel-route-api.postman_collection.json
```

The view only validates input and maps domain errors to HTTP responses. All business
logic lives in `routing/services/`, and the optimiser and geometry code are pure
functions that can be tested without a database or network.

## Technologies

- Python 3.13, Django 6.1, Django REST Framework 3.18
- MySQL 8.4+ / MariaDB 10.11+ (via `mysqlclient`)
- `requests` for the external APIs and `python-dotenv` for configuration
- **[OSRM](https://project-osrm.org/)** public server for driving routes (free, no key)
- **[Nominatim](https://nominatim.org/)** (OpenStreetMap) for geocoding (free, no key)

No GIS stack (GeoDjango, PostGIS, numpy, shapely) is needed. The geographic maths is a
few small pure-Python functions.

## Setup

### Prerequisites

- Python 3.12+
- MySQL 8.4+ or MariaDB 10.11+. Django 6.1 refuses older servers, so XAMPP's bundled
  MariaDB 10.4 will **not** work as is. Either install MariaDB 11+/MySQL 8.4+, or upgrade
  XAMPP's MySQL: back up your databases with `mysqldump`, replace `xampp/mysql/bin`,
  `lib` and `share` with a newer MariaDB build, and restore the dumps.
- The server's `sql_mode` doesn't matter: the app enables `STRICT_TRANS_TABLES` on its own
  connections, so XAMPP's non-strict default is fine.
- Internet access for OSRM and Nominatim. Tests do not need it.

### 1. Install

```bash
git clone https://github.com/rrwebsolution/fuel-route-api.git
cd fuel-route-api
python -m venv venv
# Windows: venv\Scripts\activate     macOS/Linux: source venv/bin/activate
pip install -r requirements.txt
```

### 2. Create the database

```sql
CREATE DATABASE fuel_route_db CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;
-- optional dedicated user:
CREATE USER 'fuel_route'@'localhost' IDENTIFIED BY 'a-strong-password';
GRANT ALL PRIVILEGES ON fuel_route_db.* TO 'fuel_route'@'localhost';
GRANT ALL PRIVILEGES ON test_fuel_route_db.* TO 'fuel_route'@'localhost';  -- for tests
```

### 3. Configure environment variables

```bash
cp .env.example .env     # then edit .env
```

| Variable | Default | Purpose |
|---|---|---|
| `DJANGO_DEBUG` | `False` | Debug mode |
| `DJANGO_SECRET_KEY` | (required when `DJANGO_DEBUG` is false) | Django secret key |
| `DJANGO_ALLOWED_HOSTS` | `localhost,127.0.0.1` | Comma-separated hosts |
| `DB_NAME` / `DB_USER` / `DB_PASSWORD` / `DB_HOST` / `DB_PORT` | `fuel_route_db` / `root` / empty / `127.0.0.1` / `3306` | MySQL connection |
| `GEOCODING_USER_AGENT` | `fuel-route-api/1.0 (local development)` | Nominatim requires an identifying User-Agent. Placeholder contacts such as `example.com` are rejected with HTTP 403 |
| `GEOCODING_URL` | Nominatim public server | Geocoding endpoint |
| `ROUTING_URL` | OSRM public server | Routing endpoint |
| `EXTERNAL_API_TIMEOUT_SECONDS` | `15` | Timeout for each external call |
| `GEOCODING_CACHE_SECONDS` / `ROUTING_CACHE_SECONDS` | 30 days / 1 day | Cache lifetimes |
| `VEHICLE_START_TANK_FRACTION` | `1.0` | Fuel in the tank at departure (1.0 = full) |
| `STATION_CORRIDOR_MILES` | `10` | Maximum distance from the route for a station to count |
| `FUEL_MIN_SAVINGS_PER_GALLON` | `0.10` | Saving needed to justify an extra stop (`0` = strictly cheapest) |
| `ROUTE_GEOMETRY_SIMPLIFY_TOLERANCE` | `0.0005` | Simplification of the returned geometry, in degrees (about 50 m) |

No API keys are required. `.env` is git-ignored.

### 4. Migrate, import, run

```bash
python manage.py migrate
python manage.py import_fuel_prices
python manage.py runserver
```

## Importing the fuel price CSV

```bash
python manage.py import_fuel_prices                       # uses data/fuel-prices-for-be-assessment.csv
python manage.py import_fuel_prices path/to/other.csv     # or another file with the same columns
python manage.py import_fuel_prices --geocode-missing     # also resolve unknown cities online
```

**CSV schema** (as supplied, 8,151 rows):

| Column | Example | Stored as |
|---|---|---|
| `OPIS Truckstop ID` | `7` | `opis_id` (unique) |
| `Truckstop Name` | `WOODSHED OF BIG CABIN` | `name` |
| `Address` | `I-44, EXIT 283 & US-69` | `address` |
| `City` | `Big Cabin` | `city` |
| `State` | `OK` | `state` |
| `Rack ID` | `307` | `rack_id` |
| `Retail Price` | `3.00733333` | `retail_price` (Decimal, 8 dp) |

What the import does:

- **Validation:** the header must contain all seven columns, otherwise the command stops
  with an error. Rows with an empty or invalid ID or price, or a non-positive price,
  are skipped and reported with their line numbers.
- **Idempotent:** it runs one bulk upsert on `opis_id` (`INSERT … ON DUPLICATE KEY UPDATE`).
  Running it again updates prices and never creates duplicates.
- **Duplicates:** 678 truck-stop IDs appear more than once, often with different prices.
  One station is kept per ID, at the **lowest** price.
- **Non-US rows:** 620 rows in Canadian provinces are skipped, because the API serves US routes.
- **Coordinates:** the CSV has no latitude/longitude, and its addresses are highway exits
  that don't geocode reliably. Each station is placed at its **city centroid** from the
  US Census Bureau Gazetteer (`data/us_places.csv`, public domain). That covers about
  95% of stations offline. `--geocode-missing` looks up the rest on Nominatim (1 request
  per second) and appends them to `data/city_coordinate_overrides.csv`, so later imports
  stay offline. The committed overrides file already holds these, which leaves 8 of 6,626
  stations unlocated. Those are kept but ignored by route search.

Result on the supplied file: **6,626 stations** imported in about 1 second.

## API

### `POST /api/routes/calculate/`

`POST /api/route/` is an alias for the same endpoint.

**Request**

```json
{
    "start": "Chicago, IL",
    "finish": "Denver, CO"
}
```

| Field | Type | Rules |
|---|---|---|
| `start` | string | required, 1–200 characters, a place in the USA |
| `finish` | string | required, 1–200 characters, a place in the USA, different from `start` |

**Response `200 OK`** (real output; route coordinates truncated)

```json
{
  "start":  {"query": "Chicago, IL", "display_name": "Chicago, South Chicago Township, Cook County, Illinois, United States", "latitude": 41.8755616, "longitude": -87.6244212},
  "finish": {"query": "Denver, CO", "display_name": "Denver, Colorado, United States", "latitude": 39.7392364, "longitude": -104.984862},
  "distance_miles": 1004.35,
  "duration_seconds": 64152,
  "duration": "17h 49m",
  "route": {
    "type": "LineString",
    "coordinates": [[-87.624351, 41.875563], [-87.624328, 41.874452], [-87.627592, 41.874387], "..."]
  },
  "fuel_stops": [
    {
      "opis_id": 70333,
      "station_name": "KWIK STAR #932",
      "address": "I-80, EXIT 143 & CR-S14",
      "city": "Altoona",
      "state": "IA",
      "latitude": 41.650639,
      "longitude": -93.480018,
      "distance_from_start_miles": 322.6,
      "miles_off_route": 0.8,
      "price_per_gallon": 2.959,
      "gallons": 5.986,
      "cost": 17.71
    },
    {
      "opis_id": 68368,
      "station_name": "AKAL TRAVEL CENTER",
      "address": "I-80 EX 360",
      "city": "Waco",
      "state": "NE",
      "latitude": 40.897,
      "longitude": -97.461747,
      "distance_from_start_miles": 559.9,
      "miles_off_route": 5.2,
      "price_per_gallon": 2.799,
      "gallons": 44.449,
      "cost": 124.41
    }
  ],
  "total_gallons": 50.435,
  "total_fuel_cost": 142.12,
  "total_trip_fuel_cost": 312.07,
  "fuel_summary": {
    "starting_fuel_gallons": 50.0,
    "starting_fuel_used_gallons": 50.0,
    "starting_fuel_price_per_gallon": 3.399,
    "starting_fuel_cost": 169.95,
    "fuel_used_gallons": 100.435,
    "fuel_remaining_at_finish_gallons": 0.0,
    "stations_along_route": 200
  },
  "assumptions": {
    "max_range_miles": 500,
    "miles_per_gallon": 10,
    "start_tank_fraction": 1.0,
    "station_corridor_miles": 10.0,
    "min_savings_per_gallon": 0.1
  },
  "map_url": "http://127.0.0.1:8000/api/routes/map?start=Chicago%2C+IL&finish=Denver%2C+CO"
}
```

- `route` is a GeoJSON `LineString` in `[longitude, latitude]` order. It can be drawn as is,
  for example with `L.geoJSON(response.route)` in Leaflet.
- **Two fuel costs are returned:**
  - `total_fuel_cost` is the money spent at the fuel stops on the way (`total_gallons`
    bought). The truck leaves with a full tank, so a trip under 500 miles needs no stop
    and this is `$0`.
  - `total_trip_fuel_cost` is the cost of **all** the fuel the trip burns
    (`distance / 10 MPG`): `total_fuel_cost` plus the fuel used from the starting tank,
    valued at the first station along the route, where the driver would have filled up
    before leaving. The breakdown is in `fuel_summary`.
- `map_url` opens the route map (see below).
- `price_per_gallon` is the exact price from the CSV. `cost` is `gallons × price`,
  rounded to the cent per stop, and `total_fuel_cost` is the sum of those rounded costs.

**Errors** from the route calculation look like
`{"error": {"code": "...", "message": "...", ...details}}`:

| Status | `code` | When |
|---|---|---|
| 400 | `invalid_request` | Missing or blank `start`/`finish`, or the same place twice; `fields` lists the problems |
| 422 | `location_not_found` | Location can't be found in the USA (e.g. `"Paris, France"`) |
| 422 | `route_not_found` | No drivable route (e.g. Honolulu → Los Angeles) |
| 422 | `no_fuel_stations` | The trip needs fuel but no station lies along the route |
| 422 | `fuel_range_exceeded` | A stretch of more than 500 miles has no station; includes `gap_start_miles` and `gap_end_miles` |
| 502 | `external_service_error` | Geocoding/routing provider error or bad response |
| 504 | `external_service_timeout` | Geocoding/routing provider timed out |

Malformed JSON (`400`) and an unsupported method such as GET (`405`) are rejected by
Django REST Framework before the view runs, so they return DRF's standard
`{"detail": "..."}` body.

### `GET /api/routes/map/?start=...&finish=...`

An interactive map of the trip in the browser: the route line, start (A) and finish (B)
pins, a numbered pin per fuel stop (station, price, gallons, cost), and a summary panel.
It's built with Leaflet and OpenStreetMap tiles and reuses the cached geocoding and route,
so opening it after a `POST` makes **no** extra external calls. Every
`/api/routes/calculate/` response includes the link as `map_url`.

Example: <http://127.0.0.1:8000/api/routes/map/?start=New%20York,%20NY&finish=Los%20Angeles,%20CA>

### All endpoints

| Method | Endpoint | Purpose |
|---|---|---|
| `GET` | `/api/health/` | Check that the API and database are running |
| `POST` | `/api/routes/calculate/` | Calculate the route, the fuel stops and the total fuel cost |
| `GET` | `/api/routes/map/?start=&finish=` | Interactive map of the route and fuel stops (HTML) |
| `GET` | `/api/fuel-stations/` | List fuel stations (paginated, filterable) |
| `GET` | `/api/fuel-stations/{opis_id}/` | View one fuel station |
| `POST` | `/api/fuel-stations/` | Add a fuel station |
| `PUT` | `/api/fuel-stations/{opis_id}/` | Replace a station (all fields required) |
| `PATCH` | `/api/fuel-stations/{opis_id}/` | Partially update a station |
| `DELETE` | `/api/fuel-stations/{opis_id}/` | Delete a station (`204`) |

- `{opis_id}` is the OPIS Truckstop ID from the CSV, e.g. `/api/fuel-stations/7/`.
- The trailing slash is optional on every `/api/` URL. `/api/route/` and `/api/stations/`
  are kept as aliases.
- **List filters:** `state`, `city`, `search` (name/address/city), `max_price`,
  `ordering` (`price`, `-price`, `name`, `-name`), `page`, `page_size` (default 50, max 500).
- **Station body:** `opis_id`, `name`, `address`, `city`, `state` (US code), `retail_price`
  (> 0), optional `rack_id`, optional `latitude` + `longitude`. If coordinates are omitted,
  they're filled in from the city/state, the same way as the CSV import. Changes take
  effect in route calculations straight away.
- **Station errors** use DRF's standard format: `400` with one list of messages per field
  (e.g. `{"state": ["Must be a two-letter US state code, e.g. TX."]}`), `404`
  `{"detail": "No FuelStation matches the given query."}`.
- `GET /api/health/` returns `{"status": "ok", "database": "ok", "fuel_stations": 6626,
  "fuel_stations_with_coordinates": 6618}`, or `503` if the database is down.

Add a station:

```json
POST /api/fuel-stations/
{
    "opis_id": 999001,
    "name": "DEMO TRUCK STOP",
    "address": "I-40 EXIT 70",
    "city": "Amarillo",
    "state": "TX",
    "rack_id": 1,
    "retail_price": 3.159
}
```

### Postman

Import `postman/fuel-route-api.postman_collection.json`. It contains the health check,
long, medium and short routes, the route map, two error cases, the station list and search, and the full
fuel-station CRUD sequence (add → view → PUT → PATCH → delete). It uses the `baseUrl`
variable (default `http://127.0.0.1:8000`).

Or with curl:

```bash
curl -X POST http://127.0.0.1:8000/api/routes/calculate/ \
     -H "Content-Type: application/json" \
     -d '{"start": "New York, NY", "finish": "Los Angeles, CA"}'
```

## How it works

```
start, finish ──► Nominatim (x2, cached) ──► OSRM route (x1, cached)
                                                │ full-resolution geometry
                                                ▼
                         RouteIndex: resample every ~1 mile, bucket into a grid
                                                │
     MySQL: 1 bounding-box query (indexed) ──► keep stations ≤ 10 mi from the route,
                                                │ positioned by distance along it
                                                ▼
                              fuel_optimizer: choose stops and gallons
                                                ▼
                         simplify geometry (Douglas-Peucker) ──► JSON
```

### Routing provider

**OSRM** (public demo server) returns the driving route, distance and duration in **one
call**, with full geometry as GeoJSON. It needs no API key, so an evaluator can run the
project immediately. Locations are geocoded with **Nominatim** restricted to
`countrycodes=us`, which is also how locations outside the USA are rejected. A cold
request makes 3 external calls (2 geocodes and 1 route); a repeated request makes none.
Both URLs are configurable, for example to point at a self-hosted OSRM.

### Finding stations along the route

Comparing every station with every route point would be about 6,600 × 35,000 haversine
calls. Instead:

1. **Bounding-box prefilter in SQL.** The route's bounding box, widened by the corridor,
   is matched against the `(latitude, longitude)` index in one query.
2. **Resampling.** OSRM vertices can be miles apart on straight interstates, so the route
   is resampled into points about 1 mile apart. Each point carries its cumulative distance
   from the start.
3. **Grid index.** The samples are bucketed into grid cells one corridor-width tall. Each
   candidate station only checks the few cells around it, using haversine distance. A
   station is "on the route" if it is within `STATION_CORRIDOR_MILES` (10) of a sample,
   and its position is that sample's distance from the start.

Positions are scaled to OSRM's reported distance, so stop positions and the trip length
use the same scale.

### Fuel optimisation

The tank holds `500 / 10 = 50` gallons. Starting at the origin with a full tank, the
optimiser walks through the stations in route order:

1. At a station, look at every station reachable on a **full tank** (500 miles).
2. If one of them is **cheaper**, buy just enough fuel to reach the nearest cheaper one.
3. Otherwise, if the **destination** is within 500 miles, buy just enough to arrive.
4. Otherwise **fill up** here, since this is the cheapest fuel in range, and continue to the
   cheapest station in range.
5. If no station is within 500 miles and the destination isn't either, the trip is
   impossible (`fuel_range_exceeded`).

With exact comparisons (`FUEL_MIN_SAVINGS_PER_GALLON=0`) this greedy rule gives the
**cheapest possible plan** for a fixed tank and per-gallon prices. Never driving more than
500 miles without refuelling is built into rules 1–5. Every quantity is a `Decimal`,
gallons are exact (miles / 10), and cost is rounded to the cent per stop.

**Why there's a 10-cent threshold by default.** The strictly cheapest plan for
New York → Los Angeles has **15 stops**, including 1-gallon top-ups made to reach a station
that is 0.7¢ cheaper. Treating price differences under `FUEL_MIN_SAVINGS_PER_GALLON` as
ties (and skipping a top-up when the tank already reaches the next chosen station) gives
**7 stops** for **1.3% more** ($702 vs $693). Set it to `0` for the strictly cheapest plan.

| New York → Los Angeles | Stops | Fuel bought |
|---|---|---|
| `FUEL_MIN_SAVINGS_PER_GALLON=0` | 15 | $693.40 |
| `FUEL_MIN_SAVINGS_PER_GALLON=0.05` | 11 | $696.24 |
| `FUEL_MIN_SAVINGS_PER_GALLON=0.10` (default) | 7 | $702.15 |

## Assumptions

- **Vehicle:** 500-mile maximum range and 10 MPG, so a 50-gallon tank
  (`VEHICLE_MAX_RANGE_MILES`, `VEHICLE_MPG` in `config/settings.py`).
- **The vehicle departs with a full tank** (`VEHICLE_START_TANK_FRACTION=1.0`). That fuel
  isn't a purchase on the way, so trips under 500 miles need no stops and
  `total_fuel_cost` is $0. `total_trip_fuel_cost` still prices all fuel burned, valuing the
  starting tank at the first station along the route.
  Values below 1.0 are supported. With `0`, a station would have to be at mile 0.
- **Arrive empty:** the plan buys only what is needed to reach the destination.
- **Station location is the city centroid**, because the CSV has no coordinates. The
  10-mile corridor absorbs the gap between a city's centre and its interstate exit.
- **Detours are ignored:** a station's position is the nearest point on the route, and the
  extra miles to leave the highway and come back aren't added.
- **Price per station:** where an OPIS ID appears several times, the lowest price is used.
- Only US stations are imported.

## Performance

Measured locally (New York → Los Angeles, 2,794 miles, 34,638 route vertices):

| Step | Time |
|---|---|
| Geocoding + routing (cold, external) | ~2–4 s |
| Route resampling + grid index | ~40 ms |
| Station search (1 SQL query, 461 matches) | ~150 ms |
| Fuel optimisation | ~1 ms |
| Geometry simplification (34,638 → 2,589 points) | ~50 ms |
| **Repeated request (cache hit)** | **~0.3 s total** |

- **At most 3 external calls per request, and 0 when cached.** No per-station API calls.
- **One database query per request**, using `.values()` so no model instances are built,
  and no N+1 queries.
- Indexes on `(latitude, longitude)` for the bounding box, `(state, city)`, and a unique
  index on `opis_id` for the upsert.
- The response geometry is reduced to about 7% of its points (from about 800 KB to 65 KB),
  while the full geometry is still used to match stations.
- The cache is Django's local-memory cache. For multiple workers, point `CACHES` at Redis.

## Tests

```bash
python manage.py test routing
```

64 tests (about 0.4 s) cover request validation, haversine and corridor maths, the fuel
optimiser (cheaper-ahead purchases, fill-ups, the 500-mile constraint on every leg, cost
totals, the savings threshold and partial starting tanks), the CSV import (idempotency,
duplicates, malformed and non-US rows, missing columns) and the API end to end, including
every error status, plus the trip fuel cost, the route map page, the health endpoint
and fuel-station CRUD (validation, PUT/PATCH, delete). External HTTP calls are mocked, so the tests run offline. One test checks that a
request makes exactly 2 geocoding calls and 1 routing call, and none on repeat.

Tests use a MySQL test database (`test_<DB_NAME>`), so the DB user needs `CREATE DATABASE`
rights.

## Known limitations and trade-offs

- **City-level station coordinates.** Positions along the route are accurate to a few miles.
  Geocoding each highway-exit address would be more precise, but takes thousands of
  rate-limited requests and still often fails on addresses like `I-44, EXIT 283`.
- **Public OSRM and Nominatim servers** are rate-limited demo services that are fine for
  evaluation. In production, self-host OSRM or switch to a commercial provider by changing
  `ROUTING_URL` / `GEOCODING_URL` and the small client modules.
- **The default 10-cent threshold** trades a little cost for far fewer stops (see above).
  Minimising cost plus a per-stop time cost exactly would need dynamic programming
  over (station, fuel level), which seemed more than this assignment needed.
- **The corridor is distance-based**, so on a road that doubles back, or near a parallel
  highway, a station can be matched to the wrong part of the route. A 10-mile corridor
  makes this rare on interstate routes.
- **No authentication.** The station write endpoints are open for the demo. In production,
  protect `POST`/`PUT`/`PATCH`/`DELETE` with DRF permissions such as `IsAdminUser` or token auth.
- **Local-memory cache** is per process and cleared on restart.
- The original CSV has no date or time column, so prices are treated as current.
