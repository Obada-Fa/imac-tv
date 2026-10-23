from pathlib import Path
import json
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
_temp = tempfile.TemporaryDirectory(prefix="imactv-widgets-test-")

try:
    import widgets
    from widgets import bubbles, network
except Exception as exc:  # no PyGObject / GTK typelibs
    widgets = None


@unittest.skipIf(widgets is None, "GTK bindings unavailable")
class HomeConfigTests(unittest.TestCase):
    def test_missing_and_broken_files_give_defaults(self):
        d = Path(_temp.name)
        self.assertEqual(widgets.load_config(d), widgets.parse_config(None))
        (d / "home.json").write_text("{not json")
        cfg = widgets.load_config(d)
        self.assertEqual(cfg["widgets"], ["clock", "weather", "pairing", "continue", "apps"])
        self.assertEqual(cfg["ambient"], {"interval": 90, "idle": 120, "idle_music": 30})
        self.assertEqual(cfg["weather"], {})

    def test_valid_file_is_merged_over_defaults(self):
        cfg = widgets.parse_config({"widgets": ["clock", "apps", "clock", 7], "ambient": {"idle": 30},
                                    "weather": {"place": " Delft ", "lat": 52.0, "lon": 4.36}})
        self.assertEqual(cfg["widgets"], ["clock", "apps"])
        self.assertEqual(cfg["ambient"], {"interval": 90, "idle": 30, "idle_music": 30})
        self.assertEqual(cfg["weather"], {"place": "Delft", "lat": 52.0, "lon": 4.36})

    def test_bad_values_fall_back(self):
        cfg = widgets.parse_config({"widgets": "clock", "ambient": {"interval": "x", "idle": True},
                                    "weather": {"place": "Nowhere"}})
        self.assertEqual(cfg["widgets"], widgets.DEFAULT_CONFIG["widgets"])
        self.assertEqual(cfg["ambient"], {"interval": 90, "idle": 120, "idle_music": 30})
        self.assertEqual(cfg["weather"], {})  # a place without coordinates is ignored
        self.assertEqual(widgets.parse_config([1, 2])["widgets"], widgets.DEFAULT_CONFIG["widgets"])

    def test_numbers_are_clamped(self):
        cfg = widgets.parse_config({"ambient": {"interval": 1, "idle": 10**6, "idle_music": 2}})
        self.assertEqual(cfg["ambient"], {"interval": 10, "idle": 7200, "idle_music": 10})

    def test_style_defaults_to_network_and_accepts_bubbles(self):
        self.assertEqual(widgets.parse_config(None)["style"], "network")
        self.assertEqual(widgets.parse_config({"style": "bubbles"})["style"], "bubbles")
        self.assertEqual(widgets.parse_config({"style": "neon"})["style"], "network")
        self.assertEqual(widgets.parse_config({"style": 3})["style"], "network")

    def test_empty_widget_list_is_allowed(self):
        self.assertEqual(widgets.parse_config({"widgets": []})["widgets"], [])

    def test_builtins_are_registered(self):
        for wid in ("clock", "weather", "pairing", "continue", "apps"):
            self.assertIn(wid, widgets.REGISTRY)


@unittest.skipIf(widgets is None, "GTK bindings unavailable")
class BubbleLayoutTests(unittest.TestCase):
    def test_counts_and_empty(self):
        self.assertEqual(bubbles.cluster_layout(0, 1920, 300), [])
        for n in range(1, 8):
            self.assertEqual(len(bubbles.cluster_layout(n, 1920, 300, seed=n)), n)

    def test_left_to_right_inside_bounds_without_overlap(self):
        for seed in range(1, 30):
            for n in (1, 2, 4, 5, 7):
                lay = bubbles.cluster_layout(n, 1920, 380, seed=seed, max_r=100, width_frac=0.8)
                for a, b in zip(lay, lay[1:]):
                    self.assertLess(a["cx"], b["cx"])
                    gap = ((a["cx"] - b["cx"]) ** 2 + (a["cy"] - b["cy"]) ** 2) ** 0.5
                    self.assertGreater(gap, a["r"] + b["r"])
                for p in lay:
                    self.assertGreaterEqual(p["cx"] - p["r"], 0)
                    self.assertLessEqual(p["cx"] + p["r"], 1920)
                    self.assertLessEqual(p["r"], 100 * 1.2)
                    self.assertGreaterEqual(p["cy"] - p["r"], 0)
                    self.assertLessEqual(p["cy"] + p["r"], 380)
                if n >= 2:
                    span = lay[-1]["cx"] + lay[-1]["r"] - (lay[0]["cx"] - lay[0]["r"])
                    self.assertLessEqual(span, 1920 * 0.8 + 1)

    def test_irregular_but_deterministic(self):
        a = bubbles.cluster_layout(5, 1920, 380, seed=3)
        self.assertEqual(a, bubbles.cluster_layout(5, 1920, 380, seed=3))
        self.assertGreater(len({round(p["r"]) for p in a}), 2)       # varied sizes
        self.assertGreater(len({round(p["cy"]) for p in a}), 2)      # not on one line

    def test_lean_pushes_neighbours_away(self):
        out = bubbles.lean_offsets(5, 2, 20)
        self.assertEqual(out[2], 0)
        self.assertLess(out[1], 0)
        self.assertGreater(out[3], 0)
        self.assertGreater(abs(out[1]), abs(out[0]))
        self.assertEqual(bubbles.lean_offsets(3, None, 20), [0.0] * 3)

    def test_ease_and_hit(self):
        self.assertAlmostEqual(bubbles.ease(0, 1, 0), 0)
        self.assertGreater(bubbles.ease(0, 1, 1), 0.99)
        self.assertTrue(bubbles.in_circle(3, 4, 0, 0, 5))
        self.assertFalse(bubbles.in_circle(4, 4, 0, 0, 5))


@unittest.skipIf(widgets is None, "GTK bindings unavailable")
class NetworkMathTests(unittest.TestCase):
    def test_dust_is_deterministic_and_inside(self):
        a = network.dust_points(1920, 1080, seed=5)
        self.assertEqual(a, network.dust_points(1920, 1080, seed=5))
        self.assertNotEqual(a, network.dust_points(1920, 1080, seed=6))
        self.assertEqual(len(a), 25)
        self.assertTrue(all(0 < x < 1920 and 0 < y < 1080 for x, y in a))

    def test_line_alpha_falls_with_length(self):
        self.assertAlmostEqual(network.line_alpha(0, 400), 0.34)
        self.assertGreater(network.line_alpha(100, 400), network.line_alpha(300, 400))
        self.assertAlmostEqual(network.line_alpha(400, 400), 0.07)
        self.assertAlmostEqual(network.line_alpha(900, 400), 0.07)

    def test_links_use_nearest_neighbours_within_range(self):
        pts = [(0, 0), (10, 0), (20, 0), (30, 0), (1000, 0)]
        links = network.link_pairs(pts, [1] * 5, 100)
        pairs = {(i, j) for i, j, _ in links}
        self.assertIn((0, 1), pairs)
        self.assertTrue(all(4 not in p for p in pairs))          # too far from everything
        self.assertTrue(all(i < j for i, j, _ in links))
        two = {(i, j) for i, j, _ in network.link_pairs(pts, [2] * 5, 100)}
        self.assertTrue(pairs <= two)

    def test_drift_is_small_smooth_and_per_seed(self):
        for t in (0, 1.3, 7.7):
            dx, dy = network.drift(t, 4, 5)
            self.assertLessEqual(abs(dx), 5.0001)
            self.assertLessEqual(abs(dy), 5.0001)
        self.assertNotEqual(network.drift(1, 1, 5), network.drift(1, 2, 5))
        a, b = network.drift(1.0, 3, 10), network.drift(1.05, 3, 10)
        self.assertLess(abs(a[0] - b[0]) + abs(a[1] - b[1]), 3)

    def test_row_layout_is_ordered_inside_and_fits(self):
        for seed in range(1, 25):
            for n in (1, 3, 5, 6):
                sizes = [(280, 158)] * n
                lay = network.row_layout(sizes, 1920, 440, seed=seed, width_frac=0.86)
                self.assertEqual(len(lay), n)
                for (w, h), a, b in zip(sizes, lay, lay[1:] + [None]):
                    self.assertGreaterEqual(a["cx"] - w / 2, 0)
                    self.assertLessEqual(a["cx"] + w / 2, 1920)
                    self.assertGreaterEqual(a["cy"] - h / 2, 0)
                    self.assertLessEqual(a["cy"] + h / 2, 440)
                    if b:
                        self.assertGreater(b["cx"] - a["cx"], w)    # no overlap, strictly left to right

    def test_polygon_shapes_and_hit_testing(self):
        hexa = network.poly_points("hexagon", 50)
        octa = network.poly_points("octagon", 50)
        self.assertEqual((len(hexa), len(octa)), (6, 8))
        self.assertAlmostEqual(hexa[0][0], 50)                    # flat-top: a vertex points right
        self.assertTrue(network.point_in_poly(0, 0, hexa))
        self.assertTrue(network.point_in_poly(45, 0, hexa))
        self.assertFalse(network.point_in_poly(45, 40, hexa))      # corner of the bounding box is outside
        self.assertFalse(network.point_in_poly(60, 0, hexa))
        self.assertTrue(network.point_in_poly(45, 18, octa))
        self.assertFalse(network.point_in_poly(48, 48, octa))      # octagon cuts its corners

    def test_shape_config(self):
        cfg = widgets.parse_config({"node_shape": "octagon", "tile_shape": "chamfer"})
        self.assertEqual((cfg["node_shape"], cfg["tile_shape"]), ("octagon", "chamfer"))
        cfg = widgets.parse_config({"node_shape": "circle", "tile_shape": 4})
        self.assertEqual((cfg["node_shape"], cfg["tile_shape"]), ("hexagon", "rounded"))

    def test_dirty_cells_cover_lines_and_dust(self):
        geom = {"pts": [(10, 10), (330, 10), (500, 500)], "n": 2, "links": [(0, 1, 320.0)]}
        cells = network.geometry_cells(geom)
        self.assertIn((0, 0), cells)
        self.assertIn((5, 0), cells)           # end of the line
        self.assertIn((2, 0), cells)           # middle of the line
        self.assertIn((7, 7), cells)           # the dust point at (500, 500)
        self.assertNotIn((0, 7), cells)


@unittest.skipIf(widgets is None, "GTK bindings unavailable")
class ClockTests(unittest.TestCase):
    def test_date_is_english_regardless_of_locale(self):
        import datetime
        from widgets import clock
        self.assertEqual(clock.fmt_clock(datetime.datetime(2026, 10, 23, 21, 47)), ("21:47", "Friday 23 October"))
        self.assertEqual(clock.fmt_clock(datetime.datetime(2026, 1, 4, 7, 5)), ("07:05", "Sunday 4 January"))


@unittest.skipIf(widgets is None, "GTK bindings unavailable")
class PairingHelpersTests(unittest.TestCase):
    def test_paired_phones_counts_devices_minus_the_tv_itself(self):
        from widgets import netinfo
        d = Path(tempfile.mkdtemp(dir=_temp.name))
        self.assertEqual(netinfo.paired_phones(d), 0)
        (d / "devices.json").write_text(json.dumps({"a": {}}))
        self.assertEqual(netinfo.paired_phones(d), 0)
        (d / "devices.json").write_text(json.dumps({"a": {}, "b": {}, "c": {}}))
        self.assertEqual(netinfo.paired_phones(d), 2)
        (d / "devices.json").write_text("garbage")
        self.assertEqual(netinfo.paired_phones(d), 0)

    def test_fmt_code(self):
        from widgets import netinfo
        self.assertEqual(netinfo.fmt_code("482913"), "482 913")
        self.assertEqual(netinfo.fmt_code("---"), "---")


if __name__ == "__main__":
    unittest.main()
