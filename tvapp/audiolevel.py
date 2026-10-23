"""Very cheap music listener: bass/mid/treble levels, per-band hits and beat onsets from the speaker monitor
(never the mic).

Capture is `parec` (or `pw-record`) on the default sink monitor; DSP is pure Python on small blocks (~33 ms).
Nothing is written to disk. Failures just leave levels() at zeros with active False.
Test hook: IMACTV_LEVELS_WAV=path plays a 16-bit WAV file (looped, in real time) instead of capturing.
"""
import array
import math
from operator import mul, sub
import os
import shutil
import subprocess
import threading
import time
import wave

DEC = 8              # box-average decimation for the low/mid split (11025 -> ~1378 Hz)
SILENCE = 40.0       # overall RMS (of 32768) below which a frame counts as silence
FLOOR = (300.0, 150.0, 150.0)   # per-band auto-gain never amplifies below these (bass, mid, treble)
CROSS = 0.08         # ...nor a band's onsets to more than 1/CROSS of the loudest band, so noise stays quiet
# levels use the geometric mean of the band's peak and the loudest band's peak: a band 20 dB under the bass
# still reads ~0.3 instead of ~0.1, while the balance between bands stays honest
PEAK_HALF = 4.0      # s for a band's peak to halve once the music gets quieter
ATTACK, RELEASE = 0.010, 0.07   # s: level envelope time constants (dips between beats stay visible)
HIT_TAU = 0.085      # s: a band hit decays 1 -> ~0.1 in ~200 ms
BEAT_DECAY = 0.25    # s: the beat envelope runs 1 -> 0 linearly
REFRACTORY = (0.14, 0.10, 0.07)  # s: shortest gap between two onsets of one band
FLUX_K, FLUX_MIN = 1.6, (0.20, 0.16, 0.16)   # onset: rise > FLUX_K x its running mean and > FLUX_MIN
LEVEL_MIN = (0.30, 0.25, 0.25)  # ...and the band at least this loud (of its own peak)
ANALYSIS_FPS = 30    # AudioLevels analyses in blocks of rate/ANALYSIS_FPS samples (at least the fps it is given)
STALE = 1.0          # s without frames -> inactive
LATENCY_MS = 25
KEYS = ("bass", "mid", "treble")

_onsets = [0]        # process-wide beat counter: never resets, so consumers can diff it across restarts


def zero(onset=None):
    """The silent / inactive levels dict (onset keeps its value)."""
    return {"bass": 0.0, "mid": 0.0, "treble": 0.0, "beat": 0.0, "active": False,
            "onset": _onsets[0] if onset is None else onset, "bass_hit": 0.0, "mid_hit": 0.0, "treble_hit": 0.0}


class Analyzer:
    """Feed one frame (array('h') of rate/fps samples) at a time; returns the levels dict."""

    def __init__(self, rate=11025, fps=20):
        self.fps = fps
        dt = 1.0 / fps
        self.lp = [0.0, 0.0, 0.0, 0.0]             # 2-pole low-pass (bass < ~200 Hz) and 2-pole high-pass states
        self.alpha = 1.0 - math.exp(-6.2832 * 200.0 / (rate / DEC))
        self.peak = list(FLOOR)
        self.pdec = 0.5 ** (dt / PEAK_HALF)
        self.att, self.rel = 1.0 - math.exp(-dt / ATTACK), 1.0 - math.exp(-dt / RELEASE)
        self.hdec = math.exp(-dt / HIT_TAU)
        self.sm = [0.0, 0.0, 0.0]
        self.hit = [0.0, 0.0, 0.0]
        self.prev = [[0.0, 0.0], [0.0, 0.0], [0.0, 0.0]]   # last two normalised levels per band
        self.flux = [0.05, 0.05, 0.05]             # running mean of the positive rises
        self.fa = 1.0 - math.exp(-dt / 1.0)
        self.since = [99.0, 99.0, 99.0]            # s since each band's last onset
        self.refr = [r - 0.5 * dt for r in REFRACTORY]
        self.beat = 0.0
        self.bdec = dt / BEAT_DECAY
        self.loud = self.quiet = 0
        self.active = False

    @property
    def onset(self):
        return _onsets[0]

    def feed(self, x):
        n = len(x)
        if n < DEC * 4:
            return self.out()
        m = n // DEC
        a = [s / DEC for s in map(sum, zip(*[x[i:m * DEC:DEC] for i in range(DEC)]))]
        l1, l2, h1, h2 = self.lp
        al, lo2, mi2 = self.alpha, 0.0, 0.0
        for v in a:
            l1 += al * (v - l1)
            l2 += al * (l1 - l2)
            lo2 += l2 * l2
            h1 += al * (v - h1)                     # high-pass twice: an 80 Hz kick leaks ~-20 dB into mid
            d = v - h1
            h2 += al * (d - h2)
            e = d - h2
            mi2 += e * e
        self.lp = [l1, l2, h1, h2]
        d = list(map(sub, x[1:], x[:-1]))
        d = list(map(sub, d[1:], d[:-1]))          # 2nd difference: ~+12 dB/oct, kicks barely leak into treble
        hi2 = sum(map(mul, d, d)) / len(d)
        raw = ((lo2 / m) ** .5, (mi2 / m) ** .5, (hi2 ** .5) * 0.25)
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
        pk = self.peak = [max(f, p * self.pdec, r) for f, p, r in zip(FLOOR, self.peak, raw)]  # per-band auto-gain
        top = max(pk)
        dt = 1.0 / self.fps
        hit = [False, False, False]
        for i in range(3):
            r = raw[i]
            lv = r / (pk[i] * top) ** .5
            s = self.sm[i]
            self.sm[i] = s + (lv - s) * (self.att if lv > s else self.rel)
            v = r / max(pk[i], CROSS * top)
            # onset: the band jumps well above its level of the last two frames (a rise split over two
            # blocks still counts), relative to how much this band usually wobbles
            pv = self.prev[i]
            rise = v - min(pv)
            pv[0], pv[1] = pv[1], v
            self.since[i] += dt
            if (self.active and rise > FLUX_MIN[i] and rise > FLUX_K * self.flux[i] and v > LEVEL_MIN[i]
                    and self.since[i] >= self.refr[i]):
                hit[i] = True
                self.since[i] = 0.0
                self.hit[i] = 1.0
            else:
                h = self.hit[i] * self.hdec
                self.hit[i] = h if h > 0.02 else 0.0
            self.flux[i] += (max(0.0, rise) - self.flux[i]) * self.fa
        # beat: a kick (bass onset); songs without bass fall back to mid onsets
        if hit[0] or (hit[1] and self.since[0] > 1.0):
            _onsets[0] += 1
            self.beat = 1.0
        else:
            self.beat = max(0.0, self.beat - self.bdec)
        return self.out()

    def out(self):
        if not self.active:
            return zero()
        sm, h = self.sm, self.hit
        return {"bass": sm[0], "mid": sm[1], "treble": sm[2], "beat": self.beat, "active": True,
                "onset": _onsets[0], "bass_hit": h[0], "mid_hit": h[1], "treble_hit": h[2]}


def read_wav(path, rate=11025):
    """16-bit PCM WAV -> mono array('h') at `rate` (nearest-sample resampling: test tracks only)."""
    with wave.open(path, "rb") as w:
        ch, width, src = w.getnchannels(), w.getsampwidth(), w.getframerate()
        if width != 2:
            raise ValueError("16-bit WAV only")
        x = array.array("h")
        x.frombytes(w.readframes(w.getnframes()))
    if ch > 1:
        x = array.array("h", (sum(x[k:k + ch]) // ch for k in range(0, len(x) - ch + 1, ch)))
    if src != rate:
        x = array.array("h", (x[int(k * src / rate)] for k in range(int(len(x) * rate / src))))
    return x


def levels_from_wav(path, rate=11025, fps=ANALYSIS_FPS):
    """Every frame's levels dict for a WAV file, as fast as possible (tests and tuning)."""
    x, an, hop = read_wav(path, rate), Analyzer(rate, fps), rate // fps
    return [an.feed(x[k:k + hop]) for k in range(0, len(x) - hop + 1, hop)]


def _capture_cmd(rate):
    if shutil.which("parec"):
        return ["parec", "--device=@DEFAULT_MONITOR@", "--format=s16le", "--rate=%d" % rate,
                "--channels=1", "--latency-msec=%d" % LATENCY_MS]
    if shutil.which("pw-record"):
        return ["pw-record", "-P", "stream.capture.sink=true", "--format=s16", "--rate=%d" % rate,
                "--channels=1", "--latency=%dms" % LATENCY_MS, "-"]
    return None


class AudioLevels:
    """levels() -> {bass, mid, treble, beat, bass_hit, mid_hit, treble_hit: 0..1, onset: int, active: bool}.
    `fps` is the slowest rate the caller polls at; analysis runs at least at ANALYSIS_FPS so onsets are tight."""

    def __init__(self, rate=11025, fps=20, cmd=None):
        self.rate, self.fps = rate, fps
        self.afps = max(fps, ANALYSIS_FPS)
        self._cmd = cmd
        self._wav = os.environ.get("IMACTV_LEVELS_WAV") if cmd is None else None
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._thread = None
        self._proc = None
        self._lv = zero()
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
            self._lv, self._proc = zero(), None

    def levels(self):
        with self._lock:
            if time.monotonic() - self._t > STALE:
                return zero()
            return dict(self._lv)

    def _run(self):
        while not self._stop.is_set():
            if self._wav:
                try:
                    self._play_wav(self._wav)
                except Exception:
                    pass
                cmd = True
            else:
                cmd = self._cmd or _capture_cmd(self.rate)
                if cmd:
                    try:
                        self._capture(cmd)
                    except Exception:
                        pass
            with self._lock:
                self._lv = zero()
            self._stop.wait(5.0 if cmd else 60.0)

    def _publish(self, lv):
        with self._lock:
            self._lv, self._t = lv, time.monotonic()

    def _play_wav(self, path):
        x = read_wav(path, self.rate)
        an, hop = Analyzer(self.rate, self.afps), self.rate // self.afps
        due = time.monotonic()
        while not self._stop.is_set():
            for k in range(0, len(x) - hop + 1, hop):
                due += hop / self.rate
                if self._stop.wait(max(0.0, due - time.monotonic())):
                    return
                self._publish(an.feed(x[k:k + hop]))

    def _capture(self, cmd):
        p = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, stdin=subprocess.DEVNULL)
        with self._lock:
            if self._stop.is_set():
                p.kill()
            self._proc = p
        an = Analyzer(self.rate, self.afps)
        size = 2 * (self.rate // self.afps)
        try:
            while not self._stop.is_set():
                buf = p.stdout.read(size)
                if len(buf) < size:
                    break
                x = array.array("h")
                x.frombytes(buf)
                self._publish(an.feed(x))
        finally:
            try:
                p.kill()
            except OSError:
                pass
            p.wait()
            p.stdout.close()
