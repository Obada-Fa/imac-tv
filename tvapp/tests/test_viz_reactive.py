"""The visualizer reacts to the analyser's onsets and per-band hits (pure Python, no display)."""
import random
from pathlib import Path
import sys
import time
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from widgets import shapes  # noqa: E402

CALM = {"bass": 0.3, "mid": 0.3, "treble": 0.2, "beat": 0.0}


def base_shape():
    base = shapes.wave_shape()
    base["pts"] = [(x * 1920, y * 1080) for x, y in base["pts"]]
    return base


def viz_of(name, seed=5):
    v = shapes.Viz(base_shape(), 1920, 1080, random.Random(seed), modes=[name])
    v.switch(name)
    for f in range(60):                          # finish the morph
        v.step(1 / 15, CALM, t=f / 15, auto=False)
    return v


class EaseTest(unittest.TestCase):
    def test_fast_anti_jitter_keeps_dips_and_passes_hits(self):
        lv = shapes.ease_levels({}, {"bass": 1.0, "bass_hit": 1.0, "treble_hit": 0.4, "onset": 9}, 1 / 15)
        self.assertGreater(lv["bass"], 0.8)                   # a kick shows the same frame
        self.assertEqual((lv["bass_hit"], lv["treble_hit"], lv["mid_hit"]), (1.0, 0.4, 0.0))
        self.assertNotIn("onset", lv)                         # only floats: callers take max(lv.values())
        for _ in range(4):                                    # ~270 ms between two kicks at 120 bpm
            lv = shapes.ease_levels(lv, {"bass": 0.05}, 1 / 15)
        self.assertLess(lv["bass"], 0.15)


class OnsetTest(unittest.TestCase):
    def test_each_onset_increment_is_one_beat(self):
        v = viz_of("constellation")
        v.every = 1e9
        beats = 0
        for f, o in enumerate([3, 3, 4, 4, 4, 5, 9, 9]):     # 9: several onsets since the last frame -> one beat
            v.cur.kick = 0.0
            v.step(1 / 15, CALM, beat=(o == 4 and f == 2), t=f / 15, onset=o)   # beat flag on the same frame
            beats += v.cur.kick == 1.0
        self.assertEqual(beats, 3)

    def test_onset_in_lv_and_bass_hit_jump_both_count(self):
        v = viz_of("ridge")
        v.every = 1e9
        v.step(1 / 15, dict(CALM, onset=1), t=0)
        v.cur.kick = 0.0
        v.step(1 / 15, dict(CALM, onset=2), t=0.1)
        self.assertEqual(v.cur.kick, 1.0)
        v = viz_of("ridge")
        v.every = 1e9
        hits = [0.0, 1.0, 0.68, 0.31, 0.1, 0.0, 0.95, 0.5]
        beats = 0
        for f, h in enumerate(hits):
            v.cur.kick = 0.0
            v.step(1 / 15, dict(CALM, bass_hit=h), t=f / 15)
            beats += v.cur.kick == 1.0
        self.assertEqual(beats, 2)

    def test_no_beats_while_stopping(self):
        v = viz_of("edgeflow")
        v.cur.kick = 0.0
        v.step(1 / 15, dict(CALM, bass_hit=1.0), t=0, auto=False)
        self.assertEqual(v.cur.kick, 0.0)


class HitsMoveEveryModeTest(unittest.TestCase):
    def test_bass_hit_moves_the_dots(self):
        for name in ("edgeflow", "ridge", "constellation", "terrain"):
            v = viz_of(name)
            calm = v.cur.points(4.0, CALM)
            hit = v.cur.points(4.0, dict(CALM, bass_hit=1.0))
            moved = max(abs(a[0] - b[0]) + abs(a[1] - b[1]) for a, b, al in zip(calm, hit, v.cur.alpha) if al > 0)
            self.assertGreater(moved, 12.0, name)            # px at 1080p: a visible punch

    def test_treble_hit_sparkles_some_dots(self):
        for name in ("edgeflow", "constellation", "terrain"):
            v = viz_of(name)
            v.step(1 / 15, CALM, t=5.0)
            self.assertIsNone(v.frame(5.0, CALM)["spark"])
            lv = dict(CALM, treble_hit=1.0)
            v.step(1 / 15, lv, t=5.1)
            fr = v.frame(5.1, lv)
            lit = [k for k, s in enumerate(fr["spark"]) if s > 0.9]
            shown = sum(a > 0.3 for a in fr["alpha"])
            self.assertTrue(shown // 8 <= len(lit) <= shown // 3, (name, len(lit), shown))
            plain = v.slot(v.cur, v.perm, v.cur.depth) if v.cur.depth else [0.75] * v.n
            self.assertTrue(all(fr["depth"][k] >= min(1.25, plain[k] + 0.45) - 1e-9 for k in lit), name)
            for f in range(5):                               # the hit decays -> the sparkle goes away
                lv = dict(CALM, treble_hit=[0.68, 0.46, 0.2, 0.05, 0.0][f])
                v.step(1 / 15, lv, t=5.2 + f / 15)
            self.assertIsNone(v.frame(5.6, lv)["spark"])

    def test_ridge_at_rest_unchanged_by_zero_hits(self):
        v = shapes.Viz(base_shape(), 1920, 1080, random.Random(1))
        rest = {"bass": 0.0, "mid": 0.0, "treble": 0.0, "beat": 0.0, "bass_hit": 0.0, "mid_hit": 0.0,
                "treble_hit": 0.0}
        self.assertEqual(v.cur.points(1.0, rest)[:len(base_shape()["pts"])], base_shape()["pts"])


class CostTest(unittest.TestCase):
    """Generous bounds (CI machines vary); the report has the real numbers."""

    def test_switch_and_frame_are_cheap(self):
        v = viz_of("edgeflow")
        lv = dict(CALM, bass_hit=0.5, treble_hit=0.6, mid_hit=0.2)
        for name in shapes.VIZ_MODES:
            t0 = time.perf_counter()
            v.switch(name, t=1.0, lv=lv)
            self.assertLess(time.perf_counter() - t0, 0.010, name)
            t0 = time.perf_counter()
            for f in range(30):
                v.step(1 / 15, lv, t=1 + f / 15, auto=False)
                v.frame(1 + f / 15, lv)
            self.assertLess((time.perf_counter() - t0) / 30, 0.003, name)


if __name__ == "__main__":
    unittest.main()
