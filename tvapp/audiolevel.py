"""Very cheap music listener: bass/mid/treble/beat levels from the speaker monitor (never the mic).

Capture is `parec` (or `pw-record`) on the default sink monitor; DSP is pure Python on decimated blocks.
Nothing is written to disk. Failures just leave levels() at zeros with active False.
"""
import array
from operator import mul, sub
import shutil
import subprocess
import threading
import time

DEC = 8              # box-average decimation for the low/mid split (11025 -> ~1378 Hz)
SILENCE = 40.0       # overall RMS (of 32768) below which a frame counts as silence
MIN_PEAK = 300.0     # auto-gain never amplifies below this
STALE = 1.0          # s without frames -> inactive
_ZERO = {"bass": 0.0, "mid": 0.0, "treble": 0.0, "beat": 0.0, "active": False}


class Analyzer:
    """Feed one frame (array('h') of rate/fps samples) at a time; returns the levels dict."""

    def __init__(self, rate=11025, fps=20):
        self.fps = fps
        self.lp = 0.0                              # one-pole low-pass state (bass < ~200 Hz)
        self.alpha = 1.0 - pow(2.718281828, -6.2832 * 200.0 / (rate / DEC))
        self.peak = MIN_PEAK
        self.sm = [0.0, 0.0, 0.0]
        self.avg_bass = 0.0
        self.beat = 0.0
        self.since_beat = 99
        self.loud = self.quiet = 0
        self.active = False

    def feed(self, x):
        n = len(x)
        if n < DEC * 2:
            return self.out()
        m = n // DEC
        a = [s / DEC for s in map(sum, zip(*[x[i:m * DEC:DEC] for i in range(DEC)]))]
        lp, al, lo2, mi2 = self.lp, self.alpha, 0.0, 0.0
        for v in a:
            lp += al * (v - lp)
            lo2 += lp * lp
            d = v - lp
            mi2 += d * d
        self.lp = lp
        d = list(map(sub, x[1:], x[:-1]))
        hi2 = sum(map(mul, d, d)) / len(d)
        raw = [(lo2 / m) ** .5, (mi2 / m) ** .5, (hi2 ** .5) * 0.5]
        total = (sum(map(mul, x, x)) / n) ** .5
        # activity: >~0.5 s above the floor to turn on, ~0.5 s below to turn off
        hold = max(1, self.fps // 2)
        if total > SILENCE:
            self.loud, self.quiet = self.loud + 1, 0
            if self.loud >= hold:
                self.active = True
        else:
            self.quiet, self.loud = self.quiet + 1, 0
            if self.quiet >= hold:
                self.active = False
        self.peak = max(MIN_PEAK, self.peak * 0.995, *raw)   # slowly decaying shared peak
        for i, r in enumerate(raw):
            t = min(1.0, r / self.peak)
            self.sm[i] += (t - self.sm[i]) * (0.6 if t > self.sm[i] else 0.15)
        # beat: bass energy jumps well above its ~1 s average
        b = raw[0]
        self.since_beat += 1
        if (self.active and b > self.avg_bass * 1.6 and b > self.peak * 0.15
                and self.since_beat >= max(2, self.fps // 6)):
            self.beat, self.since_beat = 1.0, 0
        else:
            self.beat = max(0.0, self.beat - 1.0 / max(1, self.fps // 4))   # ~250 ms decay
        self.avg_bass += (b - self.avg_bass) * (1.0 / self.fps)
        return self.out()

    def out(self):
        if not self.active:
            return dict(_ZERO, beat=self.beat if self.beat else 0.0)
        return {"bass": self.sm[0], "mid": self.sm[1], "treble": self.sm[2], "beat": self.beat, "active": True}


def _capture_cmd(rate):
    if shutil.which("parec"):
        return ["parec", "--device=@DEFAULT_MONITOR@", "--format=s16le", "--rate=%d" % rate,
                "--channels=1", "--latency-msec=50"]
    if shutil.which("pw-record"):
        return ["pw-record", "-P", "stream.capture.sink=true", "--format=s16", "--rate=%d" % rate,
                "--channels=1", "--latency=50ms", "-"]
    return None


class AudioLevels:
    def __init__(self, rate=11025, fps=20, cmd=None):
        self.rate, self.fps = rate, fps
        self._cmd = cmd
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._thread = None
        self._proc = None
        self._lv = dict(_ZERO)
        self._t = 0.0

    def start(self):
        with self._lock:
            if self._thread and self._thread.is_alive():
                return
            self._stop.clear()
            self._thread = threading.Thread(target=self._run, daemon=True, name="audiolevel")
            self._thread.start()

    def stop(self):
        with self._lock:
            t, self._thread = self._thread, None
            self._stop.set()
            p = self._proc
        if p:
            try:
                p.kill()
            except OSError:
                pass
        if t:
            t.join(3)
        with self._lock:
            self._lv, self._proc = dict(_ZERO), None

    def levels(self):
        with self._lock:
            if time.monotonic() - self._t > STALE:
                return dict(_ZERO)
            return dict(self._lv)

    def _run(self):
        while not self._stop.is_set():
            cmd = self._cmd or _capture_cmd(self.rate)
            if cmd:
                try:
                    self._capture(cmd)
                except Exception:
                    pass
            with self._lock:
                self._lv = dict(_ZERO)
            self._stop.wait(5.0 if cmd else 60.0)

    def _capture(self, cmd):
        p = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, stdin=subprocess.DEVNULL)
        with self._lock:
            if self._stop.is_set():
                p.kill()
            self._proc = p
        an = Analyzer(self.rate, self.fps)
        size = 2 * (self.rate // self.fps)
        try:
            while not self._stop.is_set():
                buf = p.stdout.read(size)
                if len(buf) < size:
                    break
                x = array.array("h")
                x.frombytes(buf)
                lv = an.feed(x)
                with self._lock:
                    self._lv, self._t = lv, time.monotonic()
        finally:
            try:
                p.kill()
            except OSError:
                pass
            p.wait()
            p.stdout.close()
