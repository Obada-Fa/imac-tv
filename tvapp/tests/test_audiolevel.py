import array
import math
from pathlib import Path
import os
import sys
import time
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import audiolevel  # noqa: E402

RATE, FPS = 11025, 20
N = RATE // FPS


def sine(freq, amp=8000, frames=1, start=0):
    return array.array("h", (int(amp * math.sin(2 * math.pi * freq * (start + i) / RATE)) for i in range(N * frames)))


def run(an, samples):
    out = None
    for k in range(0, len(samples) - N + 1, N):
        out = an.feed(samples[k:k + N])
    return out


class AnalyzerTest(unittest.TestCase):
    def test_bass_dominant(self):
        o = run(audiolevel.Analyzer(RATE, FPS), sine(80, frames=40))
        self.assertTrue(o["active"])
        self.assertGreater(o["bass"], 0.5)
        self.assertGreater(o["bass"], 3 * o["treble"])
        self.assertGreater(o["bass"], 2 * o["mid"])      # levels blend band and overall gain: leaks read as ~sqrt

    def test_treble_dominant(self):
        o = run(audiolevel.Analyzer(RATE, FPS), sine(4000, frames=40))
        self.assertGreater(o["treble"], 0.5)
        self.assertGreater(o["treble"], 3 * o["bass"])

    def test_silence_inactive(self):
        o = run(audiolevel.Analyzer(RATE, FPS), array.array("h", [0] * N * 40))
        self.assertEqual(o, {"bass": 0.0, "mid": 0.0, "treble": 0.0, "beat": 0.0, "active": False,
                             "onset": audiolevel._onsets[0], "bass_hit": 0.0, "mid_hit": 0.0, "treble_hit": 0.0})

    def test_active_needs_half_second_and_drops(self):
        an = audiolevel.Analyzer(RATE, FPS)
        self.assertFalse(run(an, sine(80, frames=5))["active"])
        self.assertTrue(run(an, sine(80, frames=10))["active"])
        self.assertFalse(run(an, array.array("h", [0] * N * 15))["active"])

    def test_beats_on_clicks(self):
        an = audiolevel.Analyzer(RATE, FPS)
        beats = 0
        for f in range(80):  # 4 s, a bass thump every 500 ms (10 frames) over quiet hum
            x = sine(80, amp=300, start=f * N)
            if f % 10 == 5:
                x = sine(60, amp=12000, start=f * N)
            o = an.feed(x)
            if o["beat"] == 1.0:
                beats += 1
        self.assertGreaterEqual(beats, 5)
        self.assertLessEqual(beats, 9)

    def test_beat_decays(self):
        an = audiolevel.Analyzer(RATE, FPS)
        run(an, sine(80, amp=200, frames=30))
        an.feed(sine(60, amp=14000))
        self.assertEqual(an.beat, 1.0)
        for _ in range(6):
            an.feed(sine(80, amp=200))
        self.assertEqual(an.beat, 0.0)

    def test_values_in_range(self):
        an = audiolevel.Analyzer(RATE, FPS)
        for f in range(30):
            o = an.feed(sine(200 + f * 90, amp=32000, start=f * N))
            for k in ("bass", "mid", "treble", "beat", "bass_hit", "mid_hit", "treble_hit"):
                self.assertTrue(0.0 <= o[k] <= 1.0)

    def test_onset_counts_every_beat_and_never_goes_back(self):
        an = audiolevel.Analyzer(RATE, FPS)
        first = an.onset
        seen = [first]
        for f in range(80):
            x = sine(80, amp=300, start=f * N)
            if f % 10 == 5:
                x = sine(60, amp=12000, start=f * N)
            o = an.feed(x)
            seen.append(o["onset"])
            if o["beat"] == 1.0:
                self.assertEqual(o["onset"], seen[-2] + 1)
        self.assertEqual(seen, sorted(seen))
        self.assertGreaterEqual(seen[-1] - first, 5)
        quiet = run(an, array.array("h", [0] * N * 20))         # inactive dicts keep the count
        self.assertFalse(quiet["active"])
        self.assertEqual(quiet["onset"], seen[-1])
        self.assertEqual(audiolevel.AudioLevels().levels()["onset"], seen[-1])   # a fresh instance too


def synth(path, secs=6.0, bpm=120, seed=3):
    """Kick on every beat, closed hats on the off 16ths (one in each kick's tail), a soft mid pad."""
    import random
    import wave
    rng = random.Random(seed)
    n = int(secs * RATE)
    y = [0.0] * n
    step = 60.0 / bpm
    kicks = [0.5 + k * step for k in range(int((secs - 0.5) / step))]
    hats = [t + off for t in kicks for off in (0.125, 0.375)]
    for t0 in kicks:
        s, ph = int(t0 * RATE), 0.0
        for i in range(min(n - s, int(0.25 * RATE))):
            ph += 2 * math.pi * (50 + 100 * math.exp(-i / RATE / 0.03)) / RATE
            y[s + i] += 22000 * math.exp(-i / RATE / 0.07) * math.sin(ph)
    for t0 in hats:
        s, last = int(t0 * RATE), 0.0
        for i in range(min(n - s, int(0.06 * RATE))):
            w = rng.uniform(-1, 1)
            y[s + i] += 5000 * math.exp(-i / RATE / 0.015) * (w - last)      # differenced noise: bright
            last = w
    for i in range(n):
        tt = i / RATE
        y[i] += 1200 * (math.sin(2 * math.pi * 330 * tt) + math.sin(2 * math.pi * 415 * tt)
                        + math.sin(2 * math.pi * 494 * tt)) * (0.8 + 0.2 * math.sin(tt))
    x = array.array("h", (max(-32767, min(32767, int(v))) for v in y))
    with wave.open(path, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(RATE)
        w.writeframes(x.tobytes())
    return kicks, hats


class SyntheticTrackTest(unittest.TestCase):
    FPS = audiolevel.ANALYSIS_FPS

    @classmethod
    def setUpClass(cls):
        import tempfile
        cls.tmp = tempfile.TemporaryDirectory()
        cls.wav = os.path.join(cls.tmp.name, "track.wav")
        cls.kicks, cls.hats = synth(cls.wav)
        cls.lv = audiolevel.levels_from_wav(cls.wav, RATE, cls.FPS)

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def frame(self, t):
        return int(t * self.FPS)

    def test_onsets_line_up_with_the_kicks(self):
        ons = [f for f in range(1, len(self.lv)) if self.lv[f]["onset"] != self.lv[f - 1]["onset"]]
        kicks = [self.frame(t) for t in self.kicks if t >= 1.0]     # the first kick lands while auto-gain warms up
        for k in kicks:
            self.assertTrue(any(abs(f - k) <= 1 for f in ons), "kick at frame %d missed (%s)" % (k, ons))
        stray = [f for f in ons if f >= kicks[0] - 1 and not any(abs(f - k) <= 1 for k in kicks)]
        self.assertEqual(stray, [])
        for f in ons:
            self.assertEqual(self.lv[f]["onset"] - self.lv[f - 1]["onset"], 1)
            self.assertEqual(self.lv[f]["beat"], 1.0)
            self.assertGreater(self.lv[f]["bass_hit"], 0.9)

    def test_bass_dips_between_kicks(self):
        for t in self.kicks[2:]:
            k = self.frame(t)
            peak = max(self.lv[f]["bass"] for f in range(k, k + 3))
            dip = min(self.lv[f]["bass"] for f in range(k + 6, k + 14))      # 200..470 ms after the kick
            self.assertGreater(peak, 0.8)
            self.assertLess(dip, 0.25)
            self.assertLess(self.lv[k + 7]["bass_hit"], 0.1)                # the hit is a short transient

    def test_treble_hits_on_hats_even_while_the_bass_is_loud(self):
        for t in self.hats[4:]:
            f = self.frame(t)
            self.assertTrue(any(self.lv[g]["treble_hit"] > 0.9 for g in (f, f + 1)), "hat at %.3f s" % t)
        loud = [self.frame(t + 0.125) for t in self.kicks[2:]]           # the hats inside a kick's tail
        self.assertTrue(all(max(self.lv[f]["bass"], self.lv[f - 1]["bass"]) > 0.4 for f in loud))
        self.assertTrue(all(max(self.lv[f]["treble_hit"], self.lv[f + 1]["treble_hit"]) > 0.9 for f in loud))
        for t in self.kicks[2:]:                                            # kicks alone don't sparkle
            f = self.frame(t)
            self.assertLess(self.lv[f]["treble_hit"], 0.5)

    def test_dsp_cost_per_frame(self):
        x = audiolevel.read_wav(self.wav, RATE)
        an, hop = audiolevel.Analyzer(RATE, self.FPS), RATE // self.FPS
        frames = [x[k:k + hop] for k in range(0, len(x) - hop + 1, hop)]
        t0 = time.perf_counter()
        for fr in frames:
            an.feed(fr)
        per = (time.perf_counter() - t0) / len(frames)
        self.assertLess(per, 0.002)

    def test_wav_hook_plays_in_real_time(self):
        old = os.environ.get("IMACTV_LEVELS_WAV")
        os.environ["IMACTV_LEVELS_WAV"] = self.wav
        try:
            a = audiolevel.AudioLevels(rate=RATE, fps=15)
        finally:
            if old is None:
                os.environ.pop("IMACTV_LEVELS_WAV")
            else:
                os.environ["IMACTV_LEVELS_WAV"] = old
        a.start()
        try:
            o0, deadline, seen = a.levels()["onset"], time.time() + 6, set()
            while time.time() < deadline and a.levels()["onset"] < o0 + 2:
                seen.update(k for k, v in a.levels().items() if v)
                time.sleep(0.02)
            self.assertGreaterEqual(a.levels()["onset"], o0 + 2)
            self.assertTrue(a.levels()["active"])
            self.assertIn("treble_hit", seen)
        finally:
            a.stop()
        self.assertFalse(a.levels()["active"])


def alive(pid):
    try:
        os.kill(pid, 0)
        with open("/proc/%d/stat" % pid) as f:
            return f.read().split(")")[1].split()[0] != "Z"
    except OSError:
        return False


class CaptureTest(unittest.TestCase):
    CMD = [sys.executable, "-c",
           "import sys,math,time,struct\n"
           "i=0\n"
           "while True:\n"
           " b=b''.join(struct.pack('<h',int(8000*math.sin(2*math.pi*80*(i+k)/11025))) for k in range(551));i+=551\n"
           " sys.stdout.buffer.write(b);sys.stdout.buffer.flush();time.sleep(0.05)\n"]

    def test_start_stop_start_no_orphans(self):
        a = audiolevel.AudioLevels(cmd=self.CMD)
        a.start(); a.start()
        deadline = time.time() + 8
        while time.time() < deadline and not a.levels()["active"]:
            time.sleep(0.1)
        self.assertGreater(a.levels()["bass"], 0.3)
        pid = a._proc.pid
        a.stop(); a.stop()
        self.assertFalse(alive(pid))
        self.assertFalse(a.levels()["active"])
        a.start()
        deadline = time.time() + 8
        while time.time() < deadline and not a.levels()["active"]:
            time.sleep(0.1)
        self.assertTrue(a.levels()["active"])
        pid = a._proc.pid
        a.stop()
        self.assertFalse(alive(pid))

    def test_missing_command_is_harmless(self):
        a = audiolevel.AudioLevels(cmd=["/nonexistent/parec"])
        a.start()
        time.sleep(0.3)
        self.assertEqual(a.levels()["active"], False)
        a.stop()


if __name__ == "__main__":
    unittest.main()
