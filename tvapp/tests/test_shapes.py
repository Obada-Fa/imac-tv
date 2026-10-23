from pathlib import Path
import json
import math
import random
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "widgets"))
import shapes  # noqa: E402  (pure module, no GTK)

W, H = 240, 135


def img(fn):
    return [[fn(x, y) for x in range(W)] for y in range(H)]


def ridge(x):
    return 60 + 18 * math.sin(x / 25) + 8 * math.sin(x / 9)


def land(x, y):
    return 50 + 0.2 * y + 30 * math.sin(x * 1.7) * math.sin(y * 2.3)     # textured, darker than the sky


def sky(x, y):
    return 200 - 0.3 * y                                                  # smooth and bright


class TraceTests(unittest.TestCase):
    def test_follows_a_clear_ridge(self):
        tr = shapes.trace_horizon(img(lambda x, y: sky(x, y) if y < ridge(x) else land(x, y)))
        self.assertIsNotNone(tr)
        row1 = tr["pts"][:shapes.TRACE_POINTS]
        err = [abs(p[1] * H - ridge(p[0] * W)) for p in row1]
        self.assertLess(sum(err) / len(err), 3)
        self.assertTrue(all(0 <= x <= 1 and 0 <= y <= 1 for x, y in tr["pts"]))
        self.assertTrue(all(0 <= i < len(tr["pts"]) and 0 <= j < len(tr["pts"]) and i != j for i, j in tr["links"]))
        self.assertGreater(len(tr["links"]), len(row1))        # a mesh, not just a polyline

    def test_clouds_do_not_hide_the_ridge(self):
        def f(x, y):
            if y < ridge(x):
                return sky(x, y) - (70 if 12 < y < 22 and 40 < x < 200 else 0)     # a smooth dark cloud band
            return land(x, y)
        tr = shapes.trace_horizon(img(f))
        self.assertIsNotNone(tr)
        err = [abs(p[1] * H - ridge(p[0] * W)) for p in tr["pts"][:shapes.TRACE_POINTS]]
        self.assertLess(sorted(err)[len(err) // 2], 4)

    def test_a_peak_survives(self):
        def peak(x):
            return 70 - max(0, 40 - abs(x - 120))              # a spike 40 px high, 80 px wide
        tr = shapes.trace_horizon(img(lambda x, y: sky(x, y) if y < peak(x) else land(x, y)))
        self.assertIsNotNone(tr)
        top = min(p[1] for p in tr["pts"]) * H
        self.assertLess(top, 40)

    def test_dark_ceiling_over_bright_rock_is_not_a_sky(self):
        # a cave: smooth dark above, lit textured rock below -> brighter below, so not sky
        self.assertIsNone(shapes.trace_horizon(img(lambda x, y: 20 if y < ridge(x) else 120 + 30 * math.sin(x * 1.7) * math.sin(y * 2.3))))

    def test_photos_without_a_line_are_skipped(self):
        rng = random.Random(3)
        self.assertIsNone(shapes.trace_horizon(img(lambda x, y: 120)))
        self.assertIsNone(shapes.trace_horizon(img(lambda x, y: rng.randint(0, 255))))
        self.assertIsNone(shapes.trace_horizon(img(
            lambda x, y: 80 + 40 * math.sin(x * 1.3 + y * 0.7) * math.sin(y * 2.1) + rng.randint(-25, 25))))
        self.assertIsNone(shapes.trace_horizon(img(lambda x, y: 100 + (x * 3 + y) % 11)))   # faint texture
        self.assertIsNone(shapes.trace_horizon([[1] * 10] * 5))                              # too small

    def test_wave_fallback(self):
        w = shapes.wave_shape()
        self.assertEqual(w["kind"], "wave")
        self.assertGreater(len(w["pts"]), 40)


class DoodleTests(unittest.TestCase):
    def test_bundled_shapes_are_valid_and_small(self):
        path = Path(__file__).resolve().parents[1] / "assets" / "shapes.json"
        self.assertLess(path.stat().st_size, 40 * 1024)
        doodles = shapes.load_doodles(path)
        self.assertGreaterEqual(len(doodles), 30)
        for d in doodles:
            self.assertTrue(25 <= len(d["pts"]) <= 45, d["name"])
            self.assertTrue(all(0 <= x <= 1 and 0 <= y <= 1 for x, y in d["pts"]))
        self.assertIn("CC BY 4.0", json.loads(path.read_text())["license"])

    def test_missing_or_broken_file_gives_nothing(self):
        d = Path(tempfile.mkdtemp())
        self.assertEqual(shapes.load_doodles(d / "nope.json"), [])
        (d / "bad.json").write_text("{")
        self.assertEqual(shapes.load_doodles(d / "bad.json"), [])
        (d / "odd.json").write_text(json.dumps({"shapes": [{"pts": [[0, 0]] * 6, "strokes": [[0, 99]]}]}))
        self.assertEqual(shapes.load_doodles(d / "odd.json"), [])

    def test_placement_stays_in_empty_areas(self):
        sh = {"name": "x", "pts": [(0, 0), (1, 0), (1, 1), (0, 1), (0.5, 0.5)], "strokes": [[0, 1, 2, 3, 0]], "aspect": 1.2}
        for seed in range(30):
            p = shapes.place_doodle(sh, 1920, 1080, random.Random(seed))
            xs, ys = [x for x, _ in p["pts"]], [y for _, y in p["pts"]]
            self.assertTrue(0 < min(xs) and max(xs) < 1920 and 0.1 * 1080 < min(ys) and max(ys) < 0.9 * 1080)
            self.assertFalse(min(xs) < 420 and min(ys) < 140)      # keeps off the clock corner
            self.assertEqual(len(p["links"]), 4)


class MorphTests(unittest.TestCase):
    shape = {"pts": [(100.0, 100.0), (200.0, 100.0), (200.0, 200.0)], "links": [(0, 1), (1, 2)], "kind": "photo"}

    @staticmethod
    def origin(i, t):
        return (0.0, 0.0)

    def test_forms_then_holds(self):
        m = shapes.Morph()
        self.assertFalse(m.moving)
        m.start(self.shape)
        self.assertTrue(m.moving)
        m.step(shapes.MORPH_IN / 2)
        mid = m.positions(self.origin, 0)[0]
        self.assertTrue(0 < mid[0] < 100)
        m.step(shapes.MORPH_IN)
        self.assertFalse(m.moving)
        self.assertEqual(m.positions(self.origin, 0)[1], (200.0, 100.0))
        self.assertEqual(m.alphas(2, 0.3), [1.0, 1.0, 1.0])

    def test_extra_dots_fade_in_and_release_returns_to_none(self):
        m = shapes.Morph()
        m.start(self.shape)
        m.step(0.1)
        a = m.alphas(2, 0.5)
        self.assertGreater(a[0], 0.5)
        self.assertLess(a[2], 0.1)
        m.step(10)
        m.release()
        m.step(shapes.MORPH_OUT + 0.1)
        self.assertIsNone(m.target)
        self.assertFalse(m.moving)

    def test_new_shape_dissolves_first(self):
        m = shapes.Morph()
        m.start(self.shape)
        m.step(10)
        other = dict(self.shape, kind="animal")
        m.start(other)
        self.assertEqual(m.kind, "photo")          # still the old one while dissolving
        m.step(shapes.MORPH_OUT + 0.1)
        self.assertEqual(m.kind, "animal")
        self.assertEqual(m.dir, 1)


class VizTests(unittest.TestCase):
    def test_constellation_is_centered_nested_geometry_independent_of_photos(self):
        mesh = shapes.constellation_shape(1920, 1080)
        self.assertEqual(mesh["kind"], "constellation")
        self.assertEqual(len(mesh["pts"]), 24)
        self.assertEqual(len(mesh["links"]), 8 * (1 + 3 + 3))
        xs, ys = [x for x, _ in mesh["pts"]], [y for _, y in mesh["pts"]]
        self.assertAlmostEqual((min(xs) + max(xs)) / 2, 960)
        self.assertAlmostEqual((min(ys) + max(ys)) / 2, 540)
        self.assertTrue(all(0 <= a < len(mesh["pts"]) and 0 <= b < len(mesh["pts"])
                            for a, b in mesh["links"]))

    def test_levels_rise_fast_and_settle_in_about_two_seconds(self):
        lv = shapes.ease_levels({}, {"bass": 1.0, "beat": 1.0}, 0.1)
        self.assertGreater(lv["bass"], 0.6)
        for _ in range(40):                      # 2 s at 20 fps with silence
            lv = shapes.ease_levels(lv, {}, 0.05)
        self.assertLess(max(lv.values()), 0.02)

    def test_breathing_is_bounded(self):
        pts = [(0.0, 0.0), (100.0, 0.0)]
        hi = shapes.viz_points(pts, (50.0, 0.0), 1.0)
        lo = shapes.viz_points(pts, (50.0, 0.0), 0.0)
        self.assertAlmostEqual(hi[1][0] - hi[0][0], 100 * 1.045)
        self.assertAlmostEqual(lo[1][0] - lo[0][0], 100.0)

    def test_beat_wave_runs_outward(self):
        self.assertGreater(shapes.wave_boost(0.0, 1.0), shapes.wave_boost(0.9, 1.0))
        self.assertGreater(shapes.wave_boost(0.6, 0.5), shapes.wave_boost(0.0, 0.5))
        self.assertEqual(shapes.wave_boost(0.5, 0.0), 0.0)

    def test_twinkle_needs_treble(self):
        self.assertEqual(shapes.twinkle(3, 1.0, 0.0), 0.0)
        self.assertTrue(all(0 <= shapes.twinkle(i, 2.0, 1.0) <= 1 for i in range(20)))


class ConfigTests(unittest.TestCase):
    def test_shape_and_visualizer_settings(self):
        import widgets
        d = widgets.parse_config(None)
        self.assertEqual((d["shapes"], d["visualizer"]), ("photo", True))
        c = widgets.parse_config({"shapes": "photo+animals", "visualizer": False})
        self.assertEqual((c["shapes"], c["visualizer"]), ("photo+animals", False))
        c = widgets.parse_config({"shapes": "dragons", "visualizer": "yes"})
        self.assertEqual((c["shapes"], c["visualizer"]), ("photo", True))
        self.assertEqual(widgets.parse_config({"shapes": "off"})["shapes"], "off")


if __name__ == "__main__":
    unittest.main()
