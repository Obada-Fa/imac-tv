"""The perf harness (IMACTV_PERF_LOG) and the cheaper visualizer-start paths it led to."""
import json
import os
from pathlib import Path
from types import SimpleNamespace
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import perflog

try:
    import homescreen
except Exception:  # no GTK bindings
    homescreen = None


class FakeClock:
    def __init__(self):
        self.t = 100.0

    def __call__(self):
        return self.t


class PerfLogTests(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.path = os.path.join(self.dir.name, "perf.jsonl")

    def tearDown(self):
        self.dir.cleanup()

    def test_off_by_default(self):
        old = os.environ.pop("IMACTV_PERF_LOG", None)
        try:
            self.assertIsNone(perflog.from_env())
        finally:
            if old is not None:
                os.environ["IMACTV_PERF_LOG"] = old

    def test_records_ticks_draws_and_marks_then_summarises_per_phase(self):
        clk = FakeClock()
        log = perflog.PerfLog(self.path, clock=clk)
        for k in range(4):                        # a 15 fps timer whose frames repaint the whole screen
            start = clk()
            clk.t += 0.002
            log.tick("shape", start, "viz_start", viz=1.0)
            log.draw_begin(100.0)
            log.layer("photo", 0.5)
            log.layer("kids", 2.0)
            clk.t += 0.006
            log.draw_end("viz_start", timers="shape")
            clk.t += 0.058
        log.mark("viz_switch", ms=3.5, mode="terrain")
        start = clk()
        clk.t += 0.001
        log.tick("shape", start, "switch")
        log.flush()
        recs = perflog.load(self.path)
        self.assertEqual([r["ev"] for r in recs].count("draw"), 4)
        draw = next(r for r in recs if r["ev"] == "draw")
        self.assertAlmostEqual(draw["ms"], 6.0, places=3)
        self.assertAlmostEqual(draw["chrome"], 5.5, places=3)      # the kids are part of "everything else"
        self.assertEqual(draw["area"], 100.0)
        res = perflog.summarize(recs)
        self.assertEqual(list(res), ["viz_start", "switch"])
        v = res["viz_start"]
        self.assertEqual(v["frames"], 4)
        self.assertAlmostEqual(v["draw_max"], 6.0, places=3)
        self.assertAlmostEqual(v["ticks"]["shape"]["mean"], 2.0, places=3)
        self.assertEqual(v["jank"], 0)
        self.assertIn("| viz_start |", perflog.table(res))

    def test_windows_count_full_screen_draws_after_a_switch(self):
        recs = [{"ev": "mark", "t": 1.0, "name": "viz_switch", "mode": "terrain", "ms": 3.0}]
        recs += [{"ev": "draw", "t": 1.0 + k * 0.066, "ms": 4.0, "area": 100.0 if k < 2 else 40.0,
                  "phase": "switch"} for k in range(10)]
        recs += [{"ev": "tick", "t": 1.0, "timer": "shape", "ms": 5.0, "phase": "switch"}]
        (w,) = perflog.windows(recs)
        self.assertEqual((w["what"], w["draws"], w["full"]), ("viz_switch:terrain", 10, 2))
        self.assertAlmostEqual(w["busy_ms"], 45.0)
        self.assertAlmostEqual(w["worst_tick"], 5.0)
        self.assertIn("viz_switch:terrain", perflog.windows_table([w]))

    def test_cpu_samples_go_to_the_phase_they_cover(self):
        recs = [{"ev": "cpu", "t": 0.0, "proc": 1.0}, {"ev": "draw", "t": 0.5, "phase": "viz", "ms": 1, "area": 1},
                {"ev": "cpu", "t": 1.0, "proc": 1.25}]
        self.assertAlmostEqual(perflog._cpu_by_phase(recs)["viz"]["proc"], 25.0)


@unittest.skipIf(homescreen is None, "GTK bindings unavailable")
class CheaperStartTests(unittest.TestCase):
    def test_dim_fade_is_a_few_full_repaints_not_one_per_frame(self):
        levels = []
        p = 0.0
        while p < 1.0:                             # 1.6 s at 15 fps
            p = homescreen.fade_step(p, 1.0, 0.066, homescreen.DIM_FADE)
            levels.append(homescreen.dim_level(p))
        changes = sum(1 for a, b in zip([0.0] + levels, levels) if a != b)
        self.assertGreaterEqual(len(levels), 20)
        self.assertEqual(changes, homescreen.DIM_STEPS)
        self.assertEqual(levels[-1], 1.0)
        self.assertEqual(homescreen.dim_level(0.0), 0.0)

    def test_pre_darkened_photo_matches_the_overlay_and_is_built_once(self):
        cairo = homescreen.cairo
        photo = cairo.ImageSurface(cairo.FORMAT_RGB24, 64, 36)
        c = cairo.Context(photo)
        c.set_source_rgb(0.2, 0.8, 0.4)
        c.paint()
        canvas = SimpleNamespace(cur=photo, prev=None, fade=1.0, dim=0.5, extra=None, dark=None,
                                 get_allocated_width=lambda: 64, get_allocated_height=lambda: 36)

        def pixel():
            out = cairo.ImageSurface(cairo.FORMAT_ARGB32, 64, 36)
            homescreen.PhotoCanvas.on_draw(canvas, None, cairo.Context(out))
            out.flush()
            return bytes(out.get_data()[18 * out.get_stride() + 32 * 4:][:3])

        self.assertIsNone(homescreen.dimmed(canvas))          # mid-fade: painted directly, nothing cached
        mid = pixel()
        canvas.dim = 1.0
        surf = homescreen.dimmed(canvas)
        self.assertIsNotNone(surf)
        self.assertIs(homescreen.dimmed(canvas), surf)          # cached
        cached = pixel()
        canvas.dark = None
        direct = homescreen.dimmed(canvas)
        self.assertIsNotNone(direct)
        # the old path: photo + overlay at full dim
        out = cairo.ImageSurface(cairo.FORMAT_ARGB32, 64, 36)
        c = cairo.Context(out)
        c.set_source_surface(photo, 0, 0)
        c.paint()
        c.set_source_rgba(0.01, 0.015, 0.035, homescreen.MUSIC_DIM)
        c.paint()
        out.flush()
        old = bytes(out.get_data()[18 * out.get_stride() + 32 * 4:][:3])
        self.assertEqual(cached, old)
        self.assertLess(cached[1], mid[1])
        canvas.dim = 0.0
        self.assertIsNone(homescreen.dimmed(canvas))
        self.assertIsNone(canvas.dark)                          # the copy is freed when the music stops

    def test_onset_counter_drives_beats_with_fallback(self):
        scr = SimpleNamespace(last_onset=None, beat_raw=0.0)
        beat = lambda tgt: homescreen.AmbientHomeScreen.detect_beat(scr, tgt)
        fake = homescreen.AmbientHomeScreen.fake
        seen = [beat(fake(None, k * 0.066)) for k in range(60)]  # 4 s of fake music at 15 fps, 1.8 beats/s
        self.assertIn(sum(seen), (6, 7, 8))
        self.assertFalse(beat({"onset": scr.last_onset, "beat": 1.0}))     # the counter decides, not "beat"
        lv = fake(None, 1.0)
        for k in ("onset", "bass_hit", "mid_hit", "treble_hit"):
            self.assertIn(k, lv)
        scr.last_onset, scr.beat_raw = None, 0.0
        self.assertTrue(beat({"beat": 0.9}))                   # today's audiolevel: the old jump check
        self.assertFalse(beat({"beat": 0.95}))

    def test_dirty_region_only_covers_what_draw_viz_paints(self):
        lv = {"bass": 0.5, "mid": 0.5, "treble": 0.2, "beat": 0.0}
        scr = SimpleNamespace(lv=lv)
        pts = [(100, 100), (900, 100), (100, 900), (1500, 900)]
        alphas = [1.0, 1.0, 0.0, 1.0]                        # dot 2 is hidden
        links = [(0, 1, 1.0), (0, 2, 1.0), (1, 3, 0.02)]     # 0-2 ends on a hidden dot, 1-3 is too faint
        fr = {"depth": None, "trail": 0.0, "pulses": []}
        kept, geom = homescreen.AmbientHomeScreen.viz_drawn(scr, pts, alphas, links, fr, None)
        self.assertEqual(kept, [(0, 1, 1.0)])
        cells = homescreen.network.geometry_cells(geom, margin=22)
        c = homescreen.network.CELL
        self.assertIn((100 // c, 100 // c), cells)
        self.assertNotIn((100 // c, 900 // c), cells)        # nothing drawn there
        self.assertIn((1500 // c, 900 // c), cells)           # a visible dot without links still repaints

    def test_shape_timer_carries_the_particles_and_crossfade(self):
        scr = SimpleNamespace(shape_id=7, shape_period=homescreen.SHAPE_MS, field_id=0,
                              active=lambda: True)
        scr.shape_drives = lambda: homescreen.AmbientHomeScreen.shape_drives(scr)
        self.assertTrue(homescreen.AmbientHomeScreen.shape_drives(scr))
        homescreen.AmbientHomeScreen.start_field_timer(scr)
        self.assertEqual(scr.field_id, 0)                     # no second timer while the shape timer runs
        scr.shape_period = 500                                # silent audio: the shape timer idles
        self.assertFalse(homescreen.AmbientHomeScreen.shape_drives(scr))


if __name__ == "__main__":
    unittest.main()
