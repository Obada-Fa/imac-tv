"""Frame-timing log for the ambient Home (IMACTV_PERF_LOG=path). Off by default: from_env() returns None and
the call sites guard with `if PERF:`, so a normal run pays one global lookup per frame.

One JSON object per line:
  {"ev": "tick", "timer": "anim"|"shape"|"field", "t", "ms", "gap", "phase", ...parts in ms}
  {"ev": "draw", "t", "ms", "gap", "area" (% of the screen redrawn), "phase", ...layers in ms}
  {"ev": "mark", "t", "name", ...}
summarize(path) folds them into a table per phase (see phase names in homescreen.perf_phase()).
"""
import atexit
import json
import os
import time

JANK_MS = 100.0


class PerfLog:
    def __init__(self, path, clock=time.perf_counter):
        self.path = path
        self.clock = clock
        self.t0 = clock()
        self.buf = []
        self.last_tick = {}
        self.last_draw = None
        self.frame = None
        self.flushed = self.t0
        self.fh = open(path, "w")
        atexit.register(self.flush)

    def now(self):
        return self.clock()

    def ms(self, start):
        return (self.clock() - start) * 1000.0

    def _emit(self, rec):
        self.buf.append(rec)
        if self.clock() - self.flushed > 1.0:     # once a second: CPU sample + write (survives SIGTERM)
            self.buf.append(self.cpu_sample())
            self.flush()

    def flush(self):
        self.flushed = self.clock()
        if self.buf and not self.fh.closed:
            self.fh.write("".join(json.dumps(r) + "\n" for r in self.buf))
            self.fh.flush()
        self.buf = []

    def cpu_sample(self):
        """Whole-process CPU (all threads) and, when IMACTV_PERF_XPID names it, the X server's, in seconds."""
        rec = {"ev": "cpu", "t": round(self.clock() - self.t0, 4), "proc": round(time.process_time(), 4)}
        xpid = os.environ.get("IMACTV_PERF_XPID")
        if xpid:
            try:
                f = open("/proc/%s/stat" % xpid).read().rsplit(")", 1)[1].split()
                rec["x"] = (int(f[11]) + int(f[12])) / os.sysconf("SC_CLK_TCK")
            except (OSError, ValueError, IndexError):
                pass
        return rec

    def mark(self, name, **kw):
        self._emit(dict(kw, ev="mark", t=round(self.clock() - self.t0, 4), name=name))

    def tick(self, timer, start, phase, **parts):
        """A timer callback that began at `start` (clock()) has finished; parts are sub-timings in ms."""
        t = start - self.t0
        prev = self.last_tick.get(timer)
        self.last_tick[timer] = t
        rec = {"ev": "tick", "timer": timer, "t": round(t, 4), "ms": round(self.ms(start), 3),
               "gap": round((t - prev) * 1000.0, 1) if prev is not None else None, "phase": phase}
        rec.update({k: round(v, 3) for k, v in parts.items()})
        self._emit(rec)

    def draw_begin(self, area):
        self.frame = {"start": self.clock(), "area": area, "layers": {}}

    def layer(self, name, ms):
        if self.frame is not None:
            lay = self.frame["layers"]
            lay[name] = lay.get(name, 0.0) + ms

    def draw_end(self, phase, **state):
        f, self.frame = self.frame, None
        if f is None:
            return
        t = f["start"] - self.t0
        total = self.ms(f["start"])
        canvas = sum(v for k, v in f["layers"].items() if k != "kids" and not k.startswith("k_"))
        rec = {"ev": "draw", "t": round(t, 4), "ms": round(total, 3), "area": round(f["area"], 2),
               "gap": round((t - self.last_draw) * 1000.0, 1) if self.last_draw is not None else None,
               "phase": phase, "chrome": round(max(0.0, total - canvas), 3)}
        rec.update({k: round(v, 3) for k, v in f["layers"].items()})
        rec.update(state)
        self.last_draw = t
        self._emit(rec)


def from_env():
    path = os.environ.get("IMACTV_PERF_LOG")
    if not path:
        return None
    try:
        return PerfLog(path)
    except OSError as exc:
        print("perf log:", exc)
        return None


def clip_area(cr, sw, sh):
    """% of the screen inside the cairo clip (the dirty region GTK asked us to repaint)."""
    try:
        rects = cr.copy_clip_rectangle_list()
        a = sum(r[2] * r[3] for r in rects)
    except Exception:
        x0, y0, x1, y1 = cr.clip_extents()
        a = (x1 - x0) * (y1 - y0)
    return 100.0 * a / max(1, sw * sh)


# ---------------------------------------------------------------- summary

def _stats(vals):
    vals = [v for v in vals if v is not None]
    if not vals:
        return None, None
    return sum(vals) / len(vals), max(vals)


def load(path):
    with open(path) as fh:
        return [json.loads(line) for line in fh if line.strip()]


def _cpu_by_phase(recs):
    """CPU % per phase from the 1 s samples: each interval goes to the phase most of its records were in."""
    recs = sorted(recs, key=lambda r: r["t"])
    samples = [r for r in recs if r["ev"] == "cpu"]
    acc = {}
    for a, b in zip(samples, samples[1:]):
        votes = {}
        for r in recs:
            if a["t"] <= r["t"] < b["t"] and r.get("phase"):
                votes[r["phase"]] = votes.get(r["phase"], 0) + 1
        if not votes:
            continue
        p = max(votes, key=votes.get)
        d = acc.setdefault(p, {"secs": 0.0, "proc": 0.0, "x": 0.0, "hasx": False})
        d["secs"] += b["t"] - a["t"]
        d["proc"] += b["proc"] - a["proc"]
        if "x" in a and "x" in b:
            d["x"] += b["x"] - a["x"]
            d["hasx"] = True
    return {p: {"proc": 100.0 * d["proc"] / d["secs"], "x": 100.0 * d["x"] / d["secs"] if d["hasx"] else None}
            for p, d in acc.items() if d["secs"] > 0}


def summarize(recs, phases=None):
    """-> {phase: {...}} with draw ms mean/max, % redrawn, draws/s, jank count, timer ticks and overlaps."""
    order = []
    out = {}
    cpu = _cpu_by_phase(recs)
    recs = sorted((r for r in recs if r["ev"] not in ("mark", "cpu") and r.get("phase")), key=lambda r: r["t"])
    for k, r in enumerate(recs):
        p = r["phase"]
        if p not in out:
            order.append(p)
            out[p] = {"draws": [], "ticks": {}, "secs": 0.0}
        d = out[p]
        if k + 1 < len(recs):               # time in a phase = the gaps that start in it (phases interleave)
            d["secs"] += min(0.5, recs[k + 1]["t"] - r["t"])
        if r["ev"] == "draw":
            d["draws"].append(r)
        else:
            d["ticks"].setdefault(r["timer"], []).append(r)
    res = {}
    for p in (phases or order):
        if p not in out:
            continue
        d = out[p]
        draws = d["draws"]
        span = max(1e-3, d["secs"])
        ms_mean, ms_max = _stats([r["ms"] for r in draws])
        area_mean, area_max = _stats([r["area"] for r in draws])
        layers = {}
        for k in ("photo", "dim", "links", "field", "shape", "chrome", "kids"):
            m, x = _stats([r.get(k) for r in draws])
            if m is not None:
                layers[k] = (m, x)
        ticks = {}
        for name, ts in d["ticks"].items():
            m, x = _stats([r["ms"] for r in ts])
            ticks[name] = {"n": len(ts), "per_s": len(ts) / span, "mean": m, "max": x}
        busy = [r["ms"] for r in draws] + [r["ms"] for ts in d["ticks"].values() for r in ts]
        res[p] = {
            "secs": span, "frames": len(draws), "fps": len(draws) / span,
            "draw_mean": ms_mean, "draw_max": ms_max, "area_mean": area_mean, "area_max": area_max,
            "layers": layers, "ticks": ticks,
            "jank": sum(1 for r in draws if r.get("gap") and r["gap"] > JANK_MS),
            "both_timers": sum(1 for r in draws if r.get("timers", "").count("+") >= 1),
            "cpu_pct": 100.0 * sum(busy) / 1000.0 / span,
            "proc_pct": cpu.get(p, {}).get("proc"), "x_pct": cpu.get(p, {}).get("x"),
        }
    return res


def windows(recs, secs=2.5):
    """Fixed windows after each visualizer start and mode switch: main-thread ms (draws + timer callbacks),
    number of draws, full-screen draws, screen-area redrawn (sum of %), worst draw. Same length before/after."""
    starts = [(r["t"], r["name"] + (":" + r["mode"] if r.get("mode") else "")) for r in recs
              if r["ev"] == "mark" and r["name"] in ("viz_new", "viz_switch")]
    out = []
    for t0, name in starts:
        w = [r for r in recs if r["ev"] in ("draw", "tick") and t0 - 0.07 <= r["t"] < t0 + secs]
        draws = [r for r in w if r["ev"] == "draw"]
        out.append({"at": t0, "what": name, "busy_ms": sum(r["ms"] for r in w), "draws": len(draws),
                    "full": sum(1 for r in draws if r["area"] >= 99.0),
                    "area_sum": sum(r["area"] for r in draws),
                    "worst": max((r["ms"] for r in draws), default=0.0),
                    "worst_tick": max((r["ms"] for r in w if r["ev"] == "tick"), default=0.0)})
    return out


def windows_table(rows):
    lines = ["| at s | what | main-thread ms | draws | full-screen draws | screens redrawn | worst draw ms | worst tick ms |",
             "|---|---|---|---|---|---|---|---|"]
    for r in rows:
        lines.append("| %.1f | %s | %.0f | %d | %d | %.1f | %.1f | %.1f |" % (
            r["at"], r["what"], r["busy_ms"], r["draws"], r["full"], r["area_sum"] / 100.0, r["worst"], r["worst_tick"]))
    return "\n".join(lines)


def table(res):
    lines = ["| phase | s | draws/s | draw ms mean/max | photo | dim | viz/shape | chrome | % redrawn mean/max "
             "| ticks/s (ms mean/max) | 2 timers | jank>100ms | main-thread % | app CPU % | X CPU % |",
             "|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|"]

    def mm(v):
        return "%.1f/%.1f" % v if v else "-"

    def pc(v):
        return "%.0f" % v if v is not None else "-"
    for p, r in res.items():
        tk = ", ".join("%s %.1f (%.1f/%.1f)" % (k, v["per_s"], v["mean"], v["max"]) for k, v in sorted(r["ticks"].items()))
        L = r["layers"]
        lines.append("| %s | %.1f | %.1f | %s | %s | %s | %s | %s | %s | %s | %d | %d | %.1f | %s | %s |" % (
            p, r["secs"], r["fps"], mm((r["draw_mean"], r["draw_max"]) if r["draw_mean"] is not None else None),
            mm(L.get("photo")), mm(L.get("dim")), mm(L.get("shape")), mm(L.get("chrome")),
            mm((r["area_mean"], r["area_max"]) if r["area_mean"] is not None else None),
            tk or "-", r["both_timers"], r["jank"], r["cpu_pct"], pc(r["proc_pct"]), pc(r["x_pct"])))
    return "\n".join(lines)


if __name__ == "__main__":
    import sys
    recs = load(sys.argv[1])
    print(table(summarize(recs)))
    print()
    print(windows_table(windows(recs)))
