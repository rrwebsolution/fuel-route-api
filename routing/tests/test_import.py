import shutil
import tempfile
from decimal import Decimal
from io import StringIO
from pathlib import Path

from django.core.management import CommandError, call_command
from django.test import SimpleTestCase, TestCase

from routing.models import FuelStation
from routing.services.city_coordinates import normalize_city

HEADER = "OPIS Truckstop ID,Truckstop Name,Address,City,State,Rack ID,Retail Price\n"
PLACES = (
    "state,name,latitude,longitude\n"
    "OK,Big Cabin town,36.53,-95.22\n"
    "AZ,Gila Bend town,32.95,-112.72\n"
    "TX,Fort Worth city,32.78,-97.35\n"
)


class ImportFuelPricesTests(TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp)
        (self.tmp / "places.csv").write_text(PLACES, encoding="utf-8")

    def run_import(self, csv_text):
        csv_path = self.tmp / "prices.csv"
        csv_path.write_text(csv_text, encoding="utf-8")
        out = StringIO()
        call_command(
            "import_fuel_prices", str(csv_path),
            places=str(self.tmp / "places.csv"), overrides=str(self.tmp / "overrides.csv"), stdout=out,
        )
        return out.getvalue()

    def test_imports_and_geocodes_by_city(self):
        self.run_import(HEADER + '7,WOODSHED OF BIG CABIN,"I-44, EXIT 283 & US-69",Big Cabin,OK,307,3.00733333\n')
        station = FuelStation.objects.get(opis_id=7)
        self.assertEqual(station.name, "WOODSHED OF BIG CABIN")
        self.assertEqual(station.address, "I-44, EXIT 283 & US-69")
        self.assertEqual(station.retail_price, Decimal("3.00733333"))
        self.assertEqual((station.latitude, station.longitude), (36.53, -95.22))

    def test_duplicate_ids_keep_lowest_price(self):
        self.run_import(
            HEADER
            + '20,PILOT TRAVEL CENTER #1243,"I-8, EXIT 119",Gila Bend,AZ,930,3.899\n'
            + '20,PILOT #1243,"I-8, EXIT 119",Gila Bend,AZ,930,3.799\n'
        )
        self.assertEqual(FuelStation.objects.count(), 1)
        self.assertEqual(FuelStation.objects.get().retail_price, Decimal("3.799"))

    def test_reimport_updates_instead_of_duplicating(self):
        self.run_import(HEADER + "7,STOP,Exit 1,Big Cabin,OK,307,3.00\n")
        self.run_import(HEADER + "7,STOP RENAMED,Exit 1,Big Cabin,OK,307,3.50\n")
        self.assertEqual(FuelStation.objects.count(), 1)
        station = FuelStation.objects.get()
        self.assertEqual(station.name, "STOP RENAMED")
        self.assertEqual(station.retail_price, Decimal("3.5"))

    def test_skips_malformed_and_non_us_rows(self):
        output = self.run_import(
            HEADER
            + "abc,BAD ID,Exit 1,Big Cabin,OK,1,3.00\n"
            + "8,BAD PRICE,Exit 1,Big Cabin,OK,1,n/a\n"
            + "9,NEGATIVE,Exit 1,Big Cabin,OK,1,-1\n"
            + "10,,Exit 1,Big Cabin,OK,1,3.00\n"
            + "11,CANADA STOP,Hwy 1,Calgary,AB,1,1.50\n"
            + "12,GOOD,Exit 2,Fort Worth,TX,1,3.10\n"
        )
        self.assertEqual(list(FuelStation.objects.values_list("opis_id", flat=True)), [12])
        self.assertIn("Malformed rows skipped: 4", output)
        self.assertIn("Non-US rows skipped: 1", output)

    def test_unknown_city_is_imported_without_coordinates(self):
        output = self.run_import(HEADER + "13,NOWHERE STOP,Exit 9,Nowheresville,KS,1,3.00\n")
        station = FuelStation.objects.get(opis_id=13)
        self.assertIsNone(station.latitude)
        self.assertIn("Stations without coordinates", output)

    def test_missing_columns_is_an_error(self):
        with self.assertRaisesMessage(CommandError, "Retail Price"):
            self.run_import("OPIS Truckstop ID,Truckstop Name,Address,City,State,Rack ID\n1,A,B,C,OK,1\n")

    def test_missing_file_is_an_error(self):
        with self.assertRaises(CommandError):
            call_command("import_fuel_prices", str(self.tmp / "missing.csv"), stdout=StringIO())


class NormalizeCityTests(SimpleTestCase):
    def test_equivalent_spellings_match(self):
        self.assertEqual(normalize_city("Mc Calla"), normalize_city("McCalla"))
        self.assertEqual(normalize_city("Saint Johns"), normalize_city("St. Johns"))
        self.assertEqual(normalize_city("Ft Worth"), normalize_city("Fort Worth"))
        self.assertEqual(normalize_city("Winston Salem"), normalize_city("Winston-Salem"))
