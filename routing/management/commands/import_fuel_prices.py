"""
Import the OPIS truck-stop fuel price CSV into the FuelStation table.

Safe to run repeatedly: rows are upserted on the OPIS Truckstop ID, so a re-import
updates prices instead of creating duplicates.
"""

import csv
import time
from decimal import Decimal, InvalidOperation
from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.db import connection, transaction

from routing.models import FuelStation
from routing.services.city_coordinates import DATA_DIR, OVERRIDES_PATH, PLACES_PATH, US_STATES, CityCoordinateIndex
from routing.services.exceptions import TripPlanningError
from routing.services.http import get_json

REQUIRED_COLUMNS = ["OPIS Truckstop ID", "Truckstop Name", "Address", "City", "State", "Rack ID", "Retail Price"]
UPDATE_FIELDS = ["name", "address", "city", "state", "rack_id", "retail_price", "latitude", "longitude", "updated_at"]
MAX_REPORTED_ERRORS = 10


class Command(BaseCommand):
    help = "Import fuel stations and prices from the OPIS CSV (idempotent upsert on OPIS Truckstop ID)."

    def add_arguments(self, parser):
        parser.add_argument(
            "csv_path", nargs="?", default=str(DATA_DIR / "fuel-prices-for-be-assessment.csv"),
            help="Path to the fuel price CSV.",
        )
        parser.add_argument("--places", default=str(PLACES_PATH),
                            help="Census Gazetteer derived city coordinates.")
        parser.add_argument("--overrides", default=str(OVERRIDES_PATH),
                            help="Extra city coordinates, used before the Gazetteer.")
        parser.add_argument("--geocode-missing", action="store_true",
                            help="Look up unmatched cities on Nominatim (1 request/second) and save them "
                                 "to the overrides file for future imports.")

    def handle(self, *args, **options):
        csv_path = Path(options["csv_path"])
        overrides_path = Path(options["overrides"])
        if not csv_path.exists():
            raise CommandError(f"CSV file not found: {csv_path}")

        rows, errors, skipped_non_us = self._read_rows(csv_path)
        stations = self._deduplicate(rows)

        coordinates = CityCoordinateIndex.from_files(Path(options["places"]), overrides_path)
        missing = sorted({(s.city, s.state) for s in stations if coordinates.lookup(s.city, s.state) is None})
        if missing and options["geocode_missing"]:
            self._geocode_missing(missing, coordinates, overrides_path)

        unlocated = 0
        for station in stations:
            location = coordinates.lookup(station.city, station.state)
            if location is None:
                unlocated += 1
            else:
                station.latitude, station.longitude = location

        self._upsert(stations)

        self.stdout.write(self.style.SUCCESS(f"Imported {len(stations)} fuel stations from {len(rows)} valid rows."))
        self.stdout.write(f"  Duplicate rows merged (lowest price kept): {len(rows) - len(stations)}")
        self.stdout.write(f"  Non-US rows skipped: {skipped_non_us}")
        self.stdout.write(f"  Malformed rows skipped: {len(errors)}")
        for message in errors[:MAX_REPORTED_ERRORS]:
            self.stdout.write(self.style.WARNING(f"    {message}"))
        if unlocated:
            self.stdout.write(self.style.WARNING(
                f"  Stations without coordinates (ignored by route search): {unlocated}. "
                "Re-run with --geocode-missing to resolve them."
            ))

    def _read_rows(self, csv_path):
        rows, errors, skipped_non_us = [], [], 0
        with open(csv_path, encoding="utf-8-sig", newline="") as handle:
            reader = csv.DictReader(handle)
            missing_columns = [c for c in REQUIRED_COLUMNS if c not in (reader.fieldnames or [])]
            if missing_columns:
                raise CommandError(f"CSV is missing required columns: {', '.join(missing_columns)}")

            for line_number, raw in enumerate(reader, start=2):
                try:
                    station = self._parse_row(raw)
                except ValueError as exc:
                    errors.append(f"line {line_number}: {exc}")
                    continue
                if station.state not in US_STATES:
                    skipped_non_us += 1
                    continue
                rows.append(station)
        return rows, errors, skipped_non_us

    @staticmethod
    def _parse_row(raw):
        values = {column: (raw.get(column) or "").strip() for column in REQUIRED_COLUMNS}
        for column in ("OPIS Truckstop ID", "Truckstop Name", "City", "State", "Retail Price"):
            if not values[column]:
                raise ValueError(f"'{column}' is empty")
        try:
            opis_id = int(values["OPIS Truckstop ID"])
            rack_id = int(values["Rack ID"]) if values["Rack ID"] else None
        except ValueError:
            raise ValueError("'OPIS Truckstop ID' and 'Rack ID' must be whole numbers") from None
        try:
            price = Decimal(values["Retail Price"])
        except InvalidOperation:
            raise ValueError(f"invalid 'Retail Price' {values['Retail Price']!r}") from None
        if not price.is_finite() or price <= 0:
            raise ValueError(f"'Retail Price' must be positive, got {values['Retail Price']!r}")

        return FuelStation(
            opis_id=opis_id,
            name=values["Truckstop Name"][:255],
            address=values["Address"][:255],
            city=values["City"][:100],
            state=values["State"].upper(),
            rack_id=rack_id,
            retail_price=price.quantize(Decimal("0.00000001")),
        )

    @staticmethod
    def _deduplicate(rows):
        """The CSV lists some truck stops several times; keep the lowest price per OPIS ID."""
        best = {}
        for station in rows:
            current = best.get(station.opis_id)
            if current is None or station.retail_price < current.retail_price:
                best[station.opis_id] = station
        return list(best.values())

    def _geocode_missing(self, missing, coordinates, overrides_path):
        self.stdout.write(f"Geocoding {len(missing)} unmatched cities via Nominatim (about 1 per second)...")
        found = []
        for city, state in missing:
            try:
                results = get_json(
                    settings.GEOCODING_URL, "geocoding",
                    params={"city": city, "state": state, "country": "us", "format": "jsonv2", "limit": 1},
                )
            except TripPlanningError as exc:
                if exc.details.get("upstream_status") == 403:
                    raise CommandError(
                        "Nominatim refused the request (HTTP 403). Set GEOCODING_USER_AGENT to an identifying "
                        "value; placeholder contacts such as example.com are blocked."
                    ) from exc
                self.stdout.write(self.style.WARNING(f"  {city}, {state}: {exc.message}"))
                results = []
            if results:
                lat, lon = float(results[0]["lat"]), float(results[0]["lon"])
                coordinates.add(city, state, lat, lon)
                found.append((state, city, lat, lon))
            time.sleep(1)  # Nominatim usage policy: max 1 request per second.

        if found:
            write_header = not overrides_path.exists() or overrides_path.stat().st_size == 0
            with open(overrides_path, "a", encoding="utf-8", newline="") as handle:
                writer = csv.writer(handle)
                if write_header:
                    writer.writerow(["state", "city", "latitude", "longitude"])
                writer.writerows(found)
        self.stdout.write(f"  Resolved {len(found)} of {len(missing)} cities (saved to {overrides_path.name}).")

    @staticmethod
    def _upsert(stations):
        options = {"update_conflicts": True, "update_fields": UPDATE_FIELDS, "batch_size": 1000}
        # PostgreSQL/SQLite need the conflict target; MySQL/MariaDB infer it from unique keys.
        if connection.features.supports_update_conflicts_with_target:
            options["unique_fields"] = ["opis_id"]
        with transaction.atomic():
            FuelStation.objects.bulk_create(stations, **options)
