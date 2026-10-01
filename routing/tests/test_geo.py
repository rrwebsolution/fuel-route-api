from django.test import SimpleTestCase

from routing.services.geo import RouteIndex, haversine_miles, simplify_line


class HaversineTests(SimpleTestCase):
    def test_same_point_is_zero(self):
        self.assertEqual(haversine_miles(40.0, -100.0, 40.0, -100.0), 0)

    def test_new_york_to_los_angeles(self):
        distance = haversine_miles(40.7128, -74.0060, 34.0522, -118.2437)
        self.assertAlmostEqual(distance, 2445, delta=5)

    def test_one_degree_of_latitude_is_about_69_miles(self):
        self.assertAlmostEqual(haversine_miles(35.0, -100.0, 36.0, -100.0), 69.1, delta=0.2)


class RouteIndexTests(SimpleTestCase):
    # A straight east-west route along latitude 35 from lon -100 to -98 (~113 miles).
    ROUTE = [[-100.0, 35.0], [-98.0, 35.0]]

    def test_samples_long_segments_so_midpoints_are_found(self):
        index = RouteIndex(self.ROUTE, max_offset_miles=5)
        position = index.locate(35.0, -99.0)  # halfway, far from both vertices
        self.assertIsNotNone(position)
        self.assertAlmostEqual(position.miles_from_start, index.length_miles / 2, delta=1)
        self.assertLess(position.miles_off_route, 1)

    def test_point_outside_corridor_is_ignored(self):
        index = RouteIndex(self.ROUTE, max_offset_miles=5)
        self.assertIsNone(index.locate(35.2, -99.0))  # ~14 miles north

    def test_point_inside_corridor_reports_offset(self):
        index = RouteIndex(self.ROUTE, max_offset_miles=10)
        position = index.locate(35.1, -99.0)  # ~6.9 miles north
        self.assertAlmostEqual(position.miles_off_route, 6.9, delta=0.5)

    def test_positions_are_scaled_to_provider_distance(self):
        index = RouteIndex(self.ROUTE, max_offset_miles=5, total_distance_miles=200)
        self.assertAlmostEqual(index.length_miles, 200)
        self.assertAlmostEqual(index.locate(35.0, -98.0).miles_from_start, 200, delta=2)

    def test_bounding_box_covers_route_plus_corridor(self):
        box = RouteIndex(self.ROUTE, max_offset_miles=10).bounding_box
        self.assertLess(box.min_lon, -100.0)
        self.assertGreater(box.max_lon, -98.0)
        self.assertAlmostEqual(box.max_lat - 35.0, 10 / 69, places=3)


class SimplifyLineTests(SimpleTestCase):
    def test_removes_collinear_points_and_keeps_corners(self):
        line = [[0, 0], [1, 0], [2, 0], [3, 0], [3, 1], [3, 2]]
        self.assertEqual(simplify_line(line, tolerance=0.01), [[0, 0], [3, 0], [3, 2]])

    def test_keeps_endpoints_of_short_lines(self):
        self.assertEqual(simplify_line([[0, 0], [1, 1]], tolerance=1), [[0, 0], [1, 1]])
