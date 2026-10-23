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


class GenerativeVizTests(unittest.TestCase):
    def setUp(self):
        import random
        self.rng = random.Random(7)
        base = shapes.wave_shape()
        base["pts"] = [(x * 1920, y * 1080) for x, y in base["pts"]]
        self.base = base
        self.doodles = [{"name": "box", "pts": [(0, 0), (1, 0), (1, 1), (0, 1), (0.5, 0.5)],
                         "strokes": [[0, 1, 2, 3, 0], [0, 4, 2]], "aspect": 1.0}]
        self.lv = {"bass": 0.6, "mid": 0.5, "treble": 0.4, "beat": 0.0}

    def test_every_mode_moves_the_same_dots_on_screen(self):
        for name in shapes.VIZ_MODES:
            viz = shapes.Viz(self.base, 1920, 1080, self.rng, doodles=self.doodles, modes=[name])
            viz.switch(name)
            for f in range(40):
                viz.step(1 / 15, self.lv, beat=f % 9 == 0, t=f / 15)
            fr = viz.frame(3.0, self.lv)
            self.assertEqual(viz.mode, name)
            self.assertEqual(len(fr["pts"]), viz.n)
            self.assertEqual(len(fr["alpha"]), viz.n)
            shown = [p for p, a in zip(fr["pts"], fr["alpha"]) if a > 0]
            self.assertGreater(len(shown), 20, name)
            self.assertTrue(all(-200 < x < 2120 and -200 < y < 1280 for x, y in shown), name)
            self.assertTrue(all(0 <= i < viz.n and 0 <= j < viz.n for i, j, _ in fr["links"]), name)

    def test_ridge_at_rest_is_exactly_the_still_shape(self):
        viz = shapes.Viz(self.base, 1920, 1080, self.rng)
        fr = viz.frame(0.0, {})
        m = len(self.base["pts"])
        self.assertEqual(fr["pts"][:m], self.base["pts"])
        self.assertTrue(all(a == 0 for a in fr["alpha"][m:]))

    def test_mode_change_flows_from_where_the_dots_are(self):
        viz = shapes.Viz(self.base, 1920, 1080, self.rng, modes=["constellation", "terrain"])
        before = viz.frame(1.0, self.lv)["pts"]
        viz.switch(t=1.0, lv=self.lv)
        self.assertEqual(viz.mode, "constellation")
        start = viz.frame(1.0, self.lv)["pts"]
        self.assertTrue(all(abs(a[0] - b[0]) < 1e-6 and abs(a[1] - b[1]) < 1e-6 for a, b in zip(before, start)))
        for _ in range(int(shapes.MODE_MORPH * 15) + 2):
            viz.step(1 / 15, self.lv, t=1.0)
        self.assertTrue(viz.settled)

    def test_modes_rotate_on_their_own_and_randomly(self):
        viz = shapes.Viz(self.base, 1920, 1080, self.rng, doodles=self.doodles, every=5)
        seen = set()
        for f in range(15 * 60):
            viz.step(1 / 15, self.lv, t=f / 15)
            seen.add(viz.mode)
        self.assertGreaterEqual(len(seen), 4)

    def test_beats_send_pulses_along_edges_and_relink_the_constellation(self):
        viz = shapes.Viz(self.base, 1920, 1080, self.rng, modes=["constellation"])
        viz.switch("constellation")
        for _ in range(60):
            viz.step(1 / 15, self.lv)
        links0 = {(i, j) for i, j, _ in viz.frame(0, self.lv)["links"]}
        viz.step(1 / 15, self.lv, beat=True)
        self.assertTrue(viz.frame(0, self.lv)["pulses"])
        for f in range(150):
            viz.step(1 / 15, self.lv, beat=f % 10 == 0)
        links1 = {(i, j) for i, j, _ in viz.frame(0, self.lv)["links"]}
        self.assertNotEqual(links0, links1)

    def test_palette_follows_the_photo_and_falls_back(self):
        w, h = 8, 4
        warm = bytes([20, 90, 230, 0] * (w * h))          # BGRx orange
        pal = shapes.palette_from_bgrx(warm, w, h, w * 4)
        self.assertEqual(len(pal), 3)
        r, g, b = pal[0]
        self.assertGreater(r, b)
        gray = bytes([128, 128, 128, 0] * (w * h))
        self.assertEqual(shapes.palette_from_bgrx(gray, w, h, w * 4), shapes.DEFAULT_PALETTE)


class FieldAndEdgeFlowTests(unittest.TestCase):
    def setUp(self):
        import random
        self.rng = random.Random(3)

    def test_field_is_sparse_slow_and_renews_from_outside(self):
        f = shapes.Field(1920, 1080, self.rng)
        self.assertTrue(30 <= f.n <= 50)
        f.step(1.0, 1.0)
        for q in f.p:
            self.assertLess(q["v"], 20)                     # a few px/s up to ~14
        entered = False
        for _ in range(60 * 9):                             # a minute at ~9 fps
            f.step(0.11, 1.0)
            for q in f.p:
                if q["x"] < -10 or q["x"] > 1930 or q["y"] < -10 or q["y"] > 1090:
                    entered = True
        self.assertTrue(entered)                            # particles pass through the outside margin
        fr = f.frame(1.0)
        self.assertEqual(len(fr["pts"]), f.n)
        self.assertTrue(fr["links"])
        # nothing pops: alpha is ~0 for particles outside the screen
        for (x, y), a in zip(fr["pts"], fr["alpha"]):
            if x < -100 or x > 2020 or y < -100 or y > 1180:
                self.assertLess(a, 0.02)

    def test_field_fades_in_within_two_seconds(self):
        f = shapes.Field(1920, 1080, self.rng)
        t = 0.0
        while t < 1.9:
            f.step(0.11, 1.0)
            t += 0.11
        self.assertGreater(f.amt, 0.99)
        while t < 4.0:
            f.step(0.11, 0.0)
            t += 0.11
        self.assertEqual(f.amt, 0.0)

    def test_edgeflow_enters_and_leaves_and_music_drives_it(self):
        mode = shapes.EdgeFlowMode(shapes.VIZ_N, 1920, 1080, self.rng)
        quiet = {"bass": 0.0, "mid": 0.0, "treble": 0.0}
        loud = {"bass": 0.9, "mid": 0.8, "treble": 0.6}
        seen_out = False
        for f in range(15 * 60):
            mode.step(1 / 15, quiet)
            mode.points(f / 15, quiet)
            seen_out = seen_out or any(not (0 <= x <= 1920 and 0 <= y <= 1080) and l
                                       for x, y, l in zip(mode.px, mode.py, mode.live))
        self.assertTrue(seen_out)
        n_quiet = sum(a > 0.1 for a in mode.alpha)
        for f in range(15 * 40):
            mode.step(1 / 15, loud)
        self.assertGreater(sum(a > 0.1 for a in mode.alpha), n_quiet)
        self.assertTrue(mode.links())

    def test_rotation_prefers_edgeflow_and_drops_centred_styles(self):
        import collections
        self.assertEqual(shapes.MODE_WEIGHTS["edgeflow"], max(shapes.MODE_WEIGHTS.values()))
        for gone in ("rings", "wire", "orbits"):
            self.assertNotIn(gone, shapes.VIZ_MODES)
        base = shapes.wave_shape()
        base["pts"] = [(x * 1920, y * 1080) for x, y in base["pts"]]
        viz = shapes.Viz(base, 1920, 1080, self.rng, every=2)
        seen = collections.Counter()
        last = None
        for f in range(15 * 2 * 90):
            viz.step(1 / 15, {"bass": 0.3, "mid": 0.3, "treble": 0.2, "beat": 0}, t=f / 15)
            if viz.mode != last:
                seen[viz.mode] += 1
                last = viz.mode
        self.assertEqual(seen.most_common(1)[0][0], "edgeflow")
