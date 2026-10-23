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
        self.assertGreater(o["bass"], 3 * o["mid"])

    def test_treble_dominant(self):
        o = run(audiolevel.Analyzer(RATE, FPS), sine(4000, frames=40))
        self.assertGreater(o["treble"], 0.5)
        self.assertGreater(o["treble"], 3 * o["bass"])

    def test_silence_inactive(self):
        o = run(audiolevel.Analyzer(RATE, FPS), array.array("h", [0] * N * 40))
        self.assertEqual(o, {"bass": 0.0, "mid": 0.0, "treble": 0.0, "beat": 0.0, "active": False})

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
            for k in ("bass", "mid", "treble", "beat"):
                self.assertTrue(0.0 <= o[k] <= 1.0)


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
