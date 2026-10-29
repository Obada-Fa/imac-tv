"""The iMac's vitals on the TV: three glowing ring gauges (CPU, memory, heat) in Home's glass language.

The arc is load; a soft halo around each ring takes its colour and strength from temperature (cool blue ->
amber -> red), so a hot, busy machine glows before you read a number. Each ring sits inside a hairline in the
node shape, joined to the next by a constellation line, like Home's network.

  VitalsCard(shell, src, look)  the Settings -> System card: rings + uptime, disk, network, busiest processes.
                                card.app_rows() -> [(box, [focusables])]: Discord / YouTube / Video player with
                                Restart (Open when closed) and Close (OK twice) sending app_restart / app_close.
  peek(shell)                   a compact glass pull-down over Home (three mini rings + one line) for 8 s;
                                calling it again restarts the 8 s, any key dismisses it.

Cost: nothing runs while hidden. While shown, sysinfo.Sampler is read once a second in a worker thread; value
changes ease over 0.5 s at <= 15 fps and then the animation timer stops (0 fps). Each ring is rasterized into
its own client-side ImageSurface and only its rect is invalidated; the peek is one cached surface, so Home
redrawing under it costs a single blit.

Test hook: IMACTV_FAKE_VITALS = "1" (built-in canned sample), a JSON object, or a path to a JSON file."""
import json
import math
import os
import sys
import threading
import time
from pathlib import Path

import gi
gi.require_version("Gtk", "3.0")
gi.require_version("Pango", "1.0")
from gi.repository import Gtk, Gdk, GLib, Pango

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import sysinfo  # noqa: E402
from screens import cmd  # noqa: E402
from widgets import glass  # noqa: E402
from widgets.network import poly_points, poly_path  # noqa: E402

cairo = glass.cairo

FRAME_MS = glass.FRAME_MS      # <= 15 fps while easing
EASE_S = 0.5
SAMPLE_MS = 1000
PEEK_MS = 8000
THREADED = True                # tests set False: samples and commands then run inline

COOL, AMBER, RED = (0.45, 0.72, 1.0), (1.0, 0.70, 0.30), (1.0, 0.34, 0.30)
RUNNING = (0.36, 0.86, 0.66)
GIB = 1024 ** 3

CANNED = {
    "cpu": 38.0, "cores": 4, "uptime": 3 * 86400 + 4 * 3600 + 17 * 60,
    "memory": {"total": 8 * GIB, "used": int(5.4 * GIB), "swap_total": 2 * GIB, "swap_used": int(0.3 * GIB)},
    "disk": {"total": 238 * 10 ** 9, "used": 146 * 10 ** 9},
    "network": {"rx": 2_400_000, "tx": 118_000},
    "temperature": 71.0, "gpu_temperature": 58.0, "load": [1.2, 0.9, 0.8],
    "top_cpu": [{"pid": 2211, "name": "vacuumtube", "cpu": 14.2, "memory": 612 * 2 ** 20},
                {"pid": 1980, "name": "Xorg", "cpu": 6.1, "memory": 140 * 2 ** 20},
                {"pid": 3120, "name": "dockerd", "cpu": 2.4, "memory": 96 * 2 ** 20}],
    "apps": [{"key": "discord", "title": "Discord", "running": True, "pids": [4100], "memory": 412 * 2 ** 20, "cpu": 1.8},
             {"key": "youtube", "title": "YouTube", "running": True, "pids": [2211], "memory": 612 * 2 ** 20, "cpu": 14.2},
             {"key": "player", "title": "Video player", "running": False, "pids": [], "memory": 0, "cpu": 0.0}],
}


# ---------------------------------------------------------------- colour, halo and formatting

def clamp(v, lo=0.0, hi=1.0):
    return lo if v < lo else hi if v > hi else v


def mix(a, b, t):
    return tuple(x + (y - x) * t for x, y in zip(a, b))


def heat_color(frac):
    """0..1 -> cool blue, amber at 0.55, red at 1."""
    frac = clamp(frac)
    return mix(COOL, AMBER, frac / 0.55) if frac <= 0.55 else mix(AMBER, RED, (frac - 0.55) / 0.45)


def temp_frac(temp, lo=45.0, hi=90.0):
    """°C -> 0..1 heat (45 °C cool, 90 °C hot); None without a sensor."""
    return None if temp is None else clamp((temp - lo) / (hi - lo))


def halo_alpha(load, heat):
    """The halo brightens with load OR heat (NexusLink's vitals hero)."""
    return 0.06 + 0.30 * clamp(max(load, heat or 0.0))


def fmt_bytes(n):
    n = float(n or 0)
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1000 or unit == "GB":
            return ("%d %s" if n >= 100 or unit == "B" else "%.1f %s") % (n, unit)
        n /= 1000.0


def fmt_gib(n):
    return "%.1f" % (n / GIB)


def fmt_uptime(secs):
    if secs is None:
        return "–"
    d, rest = divmod(int(secs), 86400)
    h, m = rest // 3600, rest % 3600 // 60
    return "%d d %d h" % (d, h) if d else "%d h %d min" % (h, m) if h else "%d min" % m


def gauges(s):
    """The three rings for a sample: [{key, label, value, heat, text, caption}] (value/heat 0..1)."""
    cpu = float(s.get("cpu") or 0) / 100
    ct, gt = s.get("temperature"), s.get("gpu_temperature")
    mem = s.get("memory") or {}
    used, total = mem.get("used", 0), mem.get("total", 0)
    mload = used / total if total else 0.0
    hot = max([t for t in (ct, gt) if t is not None], default=None)
    return [
        {"key": "cpu", "label": "CPU", "value": cpu, "heat": temp_frac(ct),
         "text": "%d%%" % round(cpu * 100), "caption": ("%d°C" % round(ct)) if ct is not None else "%d cores" % (s.get("cores") or 1)},
        # memory has no thermometer: its glow follows pressure (calm below half, red when nearly full)
        {"key": "memory", "label": "Memory", "value": mload, "heat": clamp((mload - 0.5) / 0.45),
         "text": "%d%%" % round(mload * 100), "caption": "%s / %s GB" % (fmt_gib(used), fmt_gib(total)) if total else "–"},
        {"key": "heat", "label": "Heat", "value": clamp((hot - 30) / 70) if hot is not None else 0.0, "heat": temp_frac(hot),
         "text": ("%d°" % round(hot)) if hot is not None else "–",
         "caption": ("GPU %d°C" % round(gt)) if gt is not None else ("CPU" if ct is not None else "no sensor")},
    ]


def summary_line(s):
    """The peek's single line."""
    net = s.get("network") or {}
    disk = s.get("disk") or {}
    parts = ["Up " + fmt_uptime(s.get("uptime"))]
    if disk.get("total"):
        parts.append("Disk %d%%" % round(100 * disk["used"] / disk["total"]))
    parts.append("↓ %s/s  ↑ %s/s" % (fmt_bytes(net.get("rx")), fmt_bytes(net.get("tx"))))
    return "   ·   ".join(parts)


# ---------------------------------------------------------------- sampling

_SAMPLER = None


def fake_sample():
    spec = os.environ.get("IMACTV_FAKE_VITALS")
    if not spec:
        return None
    if spec == "1":
        return json.loads(json.dumps(CANNED))
    try:
        return json.loads(spec) if spec.lstrip().startswith("{") else json.loads(Path(spec).read_text())
    except (OSError, ValueError):
        return json.loads(json.dumps(CANNED))


_PROCS = {"at": None, "data": {}}
PROCS_EVERY = 3.0              # the per-process scan (busiest, apps) is ~20 ms with ~450 processes


def take_sample(clock=time.monotonic):
    """System-wide numbers every call; the process list (top_cpu, apps) at most every PROCS_EVERY s."""
    fake = fake_sample()
    if fake is not None:
        return fake
    global _SAMPLER
    if _SAMPLER is None:          # created on first use; kept so a reopened view has a baseline for rates
        _SAMPLER = sysinfo.Sampler()
    now = clock()
    if _PROCS["at"] is None or now - _PROCS["at"] >= PROCS_EVERY:
        full = _SAMPLER.sample()
        _PROCS["at"], _PROCS["data"] = now, {"top_cpu": full["top_cpu"], "apps": full["apps"]}
    s = _SAMPLER.system()
    s.update(_PROCS["data"])
    return s


class Easer:
    """Values that ease to new targets over EASE_S (cubic ease-out); idle once all arrive."""
    MIN_STEP = 0.006

    def __init__(self, duration=EASE_S):
        self.duration = duration
        self.cur, self.start, self.target = {}, {}, {}
        self.t = duration

    def set(self, values):
        """New targets; returns True when something now has to move."""
        changed = False
        for k, v in values.items():
            self.cur.setdefault(k, 0.0)          # first values sweep in from zero
            if abs(v - self.target.get(k, self.cur[k])) < self.MIN_STEP:
                continue                         # under a pixel of arc: not worth a frame
            changed = True
            self.target[k] = v
        if changed:
            self.start = dict(self.cur)
            self.t = 0.0
        return self.moving

    @property
    def moving(self):
        return self.t < self.duration

    def step(self, dt):
        """Advance; returns True while still moving."""
        self.t = min(self.duration, self.t + dt)
        p = self.t / self.duration
        e = 1 - (1 - p) ** 3
        for k, v in self.target.items():
            a = self.start.get(k, v)
            self.cur[k] = v if p >= 1 else a + (v - a) * e
        return self.moving

    def get(self, k, default=0.0):
        return self.cur.get(k, default)


class Live:
    """1 Hz sampling and the easing timer for one visible view. stop() leaves no GLib source behind."""

    def __init__(self, on_sample, on_frame):
        self.on_sample, self.on_frame = on_sample, on_frame
        self.sample_src = self.anim_src = 0
        self.running = self.busy = False
        self.gen = 0
        self.last = 0.0

    def start(self):
        if self.running:
            return
        self.running = True
        self.gen += 1
        self.poll()
        self.sample_src = GLib.timeout_add(SAMPLE_MS, self.poll)

    def stop(self):
        self.running = False
        self.gen += 1
        for src in (self.sample_src, self.anim_src):
            if src:
                GLib.source_remove(src)
        self.sample_src = self.anim_src = 0

    def timers(self):
        return [s for s in (self.sample_src, self.anim_src) if s]

    def poll(self):
        if not self.running:
            self.sample_src = 0
            return False
        if not self.busy:
            self.busy = True
            if THREADED:
                threading.Thread(target=self.work, args=(self.gen,), daemon=True).start()
            else:
                self.work(self.gen)
        return True

    def work(self, gen):
        try:
            s = take_sample()
        except Exception as exc:  # pragma: no cover - /proc oddities
            print("vitals:", exc)
            s = None
        if THREADED:
            GLib.idle_add(self.got, gen, s)
        else:
            self.got(gen, s)

    def got(self, gen, s):
        self.busy = False
        if gen == self.gen and self.running and s is not None:
            self.on_sample(s)
        return False

    def animate(self):
        if self.running and not self.anim_src:
            self.last = time.monotonic()
            self.anim_src = GLib.timeout_add(FRAME_MS, self.frame)

    def frame(self):
        now = time.monotonic()
        dt, self.last = now - self.last, now
        if not self.running or not self.on_frame(dt):
            self.anim_src = 0
            return False
        return True


# ---------------------------------------------------------------- drawing

def paint_ring(cr, text, shape, cx, cy, r, value, heat, label, big, caption, s, mini=False):
    """One gauge centred on (cx, cy) with ring radius r. heat None = colour by load."""
    tint = heat_color(value if heat is None else heat)
    glow = halo_alpha(value, heat)
    lw = max(2.0, r * (0.13 if mini else 0.11))
    # halo: a soft radial wash peaking just outside the ring
    g = cairo.RadialGradient(cx, cy, r * 0.15, cx, cy, r * 1.55)
    g.add_color_stop_rgba(0.0, *tint, (0.05 + 0.16 * clamp(max(value, heat or 0))) * 0.6)
    g.add_color_stop_rgba(0.62, *tint, glow)
    g.add_color_stop_rgba(1.0, *tint, 0.0)
    cr.arc(cx, cy, r * 1.55, 0, 2 * math.pi)
    cr.set_source(g)
    cr.fill()
    # the node-shaped hairline the ring lives in
    poly_path(cr, poly_points(shape, r * 1.3, cx, cy))
    cr.set_source_rgba(1, 1, 1, 0.07)
    cr.fill_preserve()
    cr.set_source_rgba(1, 1, 1, 0.20 + 0.25 * glow)
    cr.set_line_width(1)
    cr.stroke()
    # track, then the value arc with a wide faint underglow and a bright spark at its tip
    cr.arc(cx, cy, r, 0, 2 * math.pi)
    cr.set_source_rgba(1, 1, 1, 0.10)
    cr.set_line_width(lw)
    cr.stroke()
    v = clamp(value)
    if v > 0.004:
        a0, a1 = -math.pi / 2, -math.pi / 2 + v * 2 * math.pi
        cr.set_line_cap(cairo.LINE_CAP_ROUND)
        cr.arc(cx, cy, r, a0, a1)
        cr.set_source_rgba(*tint, 0.22)
        cr.set_line_width(lw * 2.4)
        cr.stroke()
        cr.arc(cx, cy, r, a0, a1)
        cr.set_source_rgba(*mix((1, 1, 1), tint, 0.78), 0.96)
        cr.set_line_width(lw)
        cr.stroke()
        cr.set_line_cap(cairo.LINE_CAP_BUTT)
        cr.arc(cx + r * math.cos(a1), cy + r * math.sin(a1), lw * 0.36, 0, 2 * math.pi)
        cr.set_source_rgba(1, 1, 1, 0.95)
        cr.fill()
    # numbers inside, the name outside below the hairline
    w = r * 1.8
    big_px = r * (0.46 if mini else 0.40)
    cap_px = max(10, r * 0.15)
    lay = text.layout(big, big_px, Pango.Weight.LIGHT, w, Pango.Alignment.CENTER)
    bh = lay.get_pixel_size()[1]
    top = cy - (bh + (0 if mini else cap_px * 1.3)) / 2
    text.draw(cr, big, big_px, Pango.Weight.LIGHT, cx - w / 2, top, w, (1, 1, 1, 0.96), Pango.Alignment.CENTER)
    if not mini and caption:
        text.draw(cr, caption, cap_px, Pango.Weight.NORMAL, cx - w / 2, top + bh, w, (1, 1, 1, 0.62),
                  Pango.Alignment.CENTER)
    lab_px = max(10, (15 if not mini else 13) * s)
    text.draw(cr, label.upper(), lab_px, Pango.Weight.SEMIBOLD, cx - w, cy + r * 1.3 + 10 * s, 2 * w,
              (1, 1, 1, 0.66), Pango.Alignment.CENTER, spacing=0.16)


def paint_links(cr, centres, R, s):
    """Constellation lines between neighbouring rings with a dust diamond halfway."""
    for (x0, y0), (x1, y1) in zip(centres, centres[1:]):
        a, b = x0 + R * 1.3 + 8 * s, x1 - R * 1.3 - 8 * s
        if b <= a:
            continue
        cr.move_to(a, y0)
        cr.line_to(b, y1)
        cr.set_source_rgba(1, 1, 1, 0.16)
        cr.set_line_width(1)
        cr.stroke()
        mx, d = (a + b) / 2, 3.2 * s
        cr.move_to(mx, y0 - d)
        cr.line_to(mx + d, y0)
        cr.line_to(mx, y0 + d)
        cr.line_to(mx - d, y0)
        cr.close_path()
        cr.set_source_rgba(1, 1, 1, 0.42)
        cr.fill()


def dirty(widget, x, y, w, h):
    """Invalidate a rect given in the widget's own coordinates. Windowless widgets inside a viewport saw
    queue_draw_area() land off by their allocation (half a card never repainted), so go through the GdkWindow."""
    win = widget.get_window()
    if win is None:
        return
    a = widget.get_allocation()
    rect = Gdk.Rectangle()
    rect.x, rect.y, rect.width, rect.height = int(a.x + x), int(a.y + y), int(w), int(h)
    win.invalidate_rect(rect, True)


def eased_gauges(easer, gs):
    out = []
    for g in gs:
        g = dict(g)
        g["value"] = easer.get(g["key"])
        if g["heat"] is not None:
            g["heat"] = easer.get(g["key"] + ".h")
        out.append(g)
    return out


def targets(gs):
    t = {}
    for g in gs:
        t[g["key"]] = g["value"]
        if g["heat"] is not None:
            t[g["key"] + ".h"] = g["heat"]
    return t


class VitalsCard(Gtk.DrawingArea):
    """Settings -> System: the three rings on the left, uptime / disk / network / busiest on the right.
    Samples only while mapped."""

    def __init__(self, shell, src, look):
        super().__init__()
        self.shell, self.src, self.look = shell, src, look
        p = shell.px
        self.s = glass.scale_of(shell)
        self.R = p(78)
        self.slot = p(250)
        self.x0, self.cy = p(40), p(30) + self.R * 1.3
        self.set_size_request(-1, p(330))
        self.set_has_window(False)
        self.text = glass.TextCache(self)
        self.easer = Easer()
        self.sample = None
        self.gs = []
        self.ring_surf = {}           # key -> (state key, ImageSurface)
        self.stats_surf = None        # (state key, w, ImageSurface)
        self.apps = []                # AppRow controllers
        self.live = Live(self.got, self.step)
        self.connect("draw", self.on_draw)
        self.connect("map", lambda *_: self.live.start())
        self.connect("unmap", lambda *_: self.stop())
        self.connect("destroy", lambda *_: self.stop())

    def stop(self):
        self.live.stop()
        for a in self.apps:
            a.disarm()

    # ---- data
    def got(self, s):
        self.sample = s
        self.gs = gauges(s)
        if self.easer.set(targets(self.gs)):
            self.live.animate()
        self.invalidate_rings()
        if not self.stats_surf or self.stats_surf[0][0] != self.stats_key():
            dirty(self, *self.stats_rect())
        apps = {a["key"]: a for a in s.get("apps") or []}
        for a in self.apps:
            a.update(apps.get(a.key))

    def step(self, dt):
        more = self.easer.step(dt)
        self.invalidate_rings()
        return more

    # ---- geometry
    def centres(self):
        return [(self.x0 + self.slot / 2 + i * self.slot, self.cy) for i in range(3)]

    def ring_rect(self, i):
        cx, cy = self.centres()[i]
        half = self.slot / 2
        return int(cx - half), int(cy - self.R * 1.6), int(self.slot), int(self.R * 3.3)

    def stats_rect(self):
        x = int(self.x0 + 3 * self.slot + self.shell.px(40))
        return x, 0, max(1, self.get_allocated_width() - x - self.shell.px(30)), self.get_allocated_height()

    def invalidate_rings(self):
        for i, g in enumerate(eased_gauges(self.easer, self.gs)):
            state = (round(g["value"], 3), round(g["heat"] or -1, 3), g["text"], g["caption"], self.look.node_shape)
            if self.ring_surf.get(g["key"], (None,))[0] != state:
                self.ring_surf[g["key"]] = (state, None)
                dirty(self, *self.ring_rect(i))

    # ---- painting
    def render_ring(self, i, g):
        x, y, w, h = self.ring_rect(i)
        surf = cairo.ImageSurface(cairo.FORMAT_ARGB32, w, h)
        cr = cairo.Context(surf)
        cx, cy = self.centres()[i]
        paint_ring(cr, self.text, self.look.node_shape, cx - x, cy - y, self.R, g["value"], g["heat"], g["label"],
                   g["text"], g["caption"], self.s)
        return surf

    def stats_key(self):
        s = self.sample or {}
        net, disk = s.get("network") or {}, s.get("disk") or {}
        return (fmt_uptime(s.get("uptime")), fmt_bytes(net.get("rx")), fmt_bytes(net.get("tx")),
                fmt_bytes(disk.get("total", 0) - disk.get("used", 0)),
                tuple((t.get("name"), t.get("cpu")) for t in (s.get("top_cpu") or [])[:3]))

    def render_stats(self, w, h):
        p, s = self.shell.px, self.sample or {}
        surf = cairo.ImageSurface(cairo.FORMAT_ARGB32, w, h)
        cr = cairo.Context(surf)
        disk, net = s.get("disk") or {}, s.get("network") or {}
        rows = [("Uptime", fmt_uptime(s.get("uptime"))),
                ("Disk", "%d%%  ·  %s free" % (round(100 * disk["used"] / disk["total"]), fmt_bytes(disk["total"] - disk["used"]))
                 if disk.get("total") else "–"),
                ("Network", "↓ %s/s    ↑ %s/s" % (fmt_bytes(net.get("rx")), fmt_bytes(net.get("tx"))))]
        y = p(26)
        lab_w = p(130)
        for label, value in rows:
            self.text.draw(cr, label.upper(), p(15), Pango.Weight.SEMIBOLD, 0, y + p(6), lab_w, (1, 1, 1, 0.52), spacing=0.16)
            self.text.draw(cr, value, p(23), Pango.Weight.NORMAL, lab_w, y, w - lab_w, (1, 1, 1, 0.9))
            y += p(50)
        y += p(8)
        self.text.draw(cr, "BUSIEST", p(15), Pango.Weight.SEMIBOLD, 0, y, w, (1, 1, 1, 0.52), spacing=0.16)
        y += p(32)
        top = (s.get("top_cpu") or [])[:3]
        peak = max([t.get("cpu", 0) for t in top] + [10.0])
        for t in top:
            share = float(t.get("cpu") or 0)
            bar_w = (w - lab_w - p(90)) * share / peak
            glass.tile_path(cr, "rounded", lab_w, y + p(10), max(p(4), bar_w), p(8), self.s * 0.3)
            cr.set_source_rgba(*heat_color(clamp(share / 50)), 0.75)
            cr.fill()
            self.text.draw(cr, t.get("name") or "?", p(19), Pango.Weight.NORMAL, 0, y, lab_w - p(12), (1, 1, 1, 0.8))
            self.text.draw(cr, "%.1f%%" % share, p(19), Pango.Weight.NORMAL, w - p(90), y, p(90), (1, 1, 1, 0.62),
                           Pango.Alignment.RIGHT)
            y += p(36)
        if not top:
            self.text.draw(cr, "Measuring…", p(19), Pango.Weight.NORMAL, 0, y, w, (1, 1, 1, 0.5))
        return surf

    def on_draw(self, _w, cr):
        if cairo is None:
            return False
        clip = cr.clip_extents()
        if not self.gs:
            self.gs = gauges({})
        paint_links(cr, self.centres(), self.R, self.s)
        for i, g in enumerate(eased_gauges(self.easer, self.gs)):
            x, y, w, h = self.ring_rect(i)
            if x > clip[2] or x + w < clip[0] or y > clip[3] or y + h < clip[1]:
                continue
            state, surf = self.ring_surf.get(g["key"], (None, None))
            if surf is None:
                surf = self.render_ring(i, g)
                self.ring_surf[g["key"]] = (state, surf)
            cr.set_source_surface(surf, x, y)
            cr.paint()
        x, y, w, h = self.stats_rect()
        if w > 40 and x < clip[2]:
            key = (self.stats_key(), w, h)
            if not self.stats_surf or self.stats_surf[0] != key:
                self.stats_surf = (key, self.render_stats(w, h))
            cr.set_source_surface(self.stats_surf[1], x, y)
            cr.paint()
        return False

    # ---- apps
    def app_rows(self):
        """[(box, [restart, close])] for the Settings screen to pack and add to its focus rows."""
        out = []
        for key, spec in sysinfo.MANAGED.items():
            a = AppRow(self.shell, self.src, self.look, key, spec["title"])
            self.apps.append(a)
            out.append((a.box, a.focusables()))
        return out


# ---------------------------------------------------------------- apps

def send(shell, action, params, busy_text):
    """Fire one controller command off the UI thread; toast the failure."""
    def work():
        res = cmd(action, params, timeout=10)
        err = None
        if res is None:
            err = "Can't reach the TV controller"
        elif isinstance(res, dict) and (res.get("error") or res.get("ok") is False):
            err = str(res.get("error") or "That didn't work")
        if err:
            (GLib.idle_add if THREADED else (lambda f, *a: f(*a)))(shell.toast, err, 5)
    shell.toast(busy_text, 3)
    if THREADED:
        threading.Thread(target=work, daemon=True).start()
    else:
        work()


class AppInfo(Gtk.DrawingArea):
    """Name + state on the left of an app row: a lit hex when running."""

    def __init__(self, shell, look, title):
        super().__init__()
        self.shell, self.look, self.title = shell, look, title
        self.state = None
        self.text = glass.TextCache(self)
        self.set_has_window(False)
        self.set_hexpand(True)
        self.connect("draw", self.on_draw)

    def set_state(self, app):
        if app and app.get("running"):
            st = (True, "Running  ·  %s  ·  %.1f%% CPU" % (fmt_bytes(app.get("memory")), app.get("cpu") or 0))
        else:
            st = (False, "Not running" if app is not None else "…")
        if st != self.state:
            self.state = st
            self.queue_draw()

    def on_draw(self, _w, cr):
        p = self.shell.px
        h = self.get_allocated_height()
        on, sub = self.state or (False, "…")
        cx, cy = p(46), h / 2
        poly_path(cr, poly_points(self.look.node_shape, p(9), cx, cy))
        if on:
            cr.set_source_rgba(*RUNNING, 0.9)
            cr.fill_preserve()
        cr.set_source_rgba(1, 1, 1, 0.55 if on else 0.35)
        cr.set_line_width(1)
        cr.stroke()
        w = self.get_allocated_width() - p(76)
        self.text.draw(cr, self.title, p(24), Pango.Weight.NORMAL, p(70), cy - p(30), w, (1, 1, 1, 0.9))
        self.text.draw(cr, sub, p(18), Pango.Weight.NORMAL, p(70), cy + p(2), w, (1, 1, 1, 0.55))
        return False


class ActionRow(glass.GlassRow):
    """A GlassRow that can turn red (an armed Close)."""
    DANGER = (0.95, 0.38, 0.34)
    danger = False

    def paint(self, cr, base, f, w, h):
        if self.danger:
            x, y = self.pad_x, self.pad_y
            glass.tile_path(cr, self.look.tile_shape, x, y, w - 2 * x, h - 2 * y, glass.scale_of(self.shell))
            cr.set_source_rgba(*self.DANGER, 0.30)
            cr.fill()
        super().paint(cr, base, f, w, h)


class AppRow:
    """One managed app: info, Restart (Open when closed; none for the video player) and Close, which needs OK
    twice: the first press arms it ("OK to close", red) for 4 s or until focus leaves."""
    ARM_MS = 4000

    def __init__(self, shell, src, look, key, title):
        p = shell.px
        self.shell, self.key, self.title = shell, key, title
        self.running = False
        self.armed = 0                 # the arm timeout source
        self.box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=p(2))
        self.box.set_margin_start(p(6))
        self.info = AppInfo(shell, look, title)
        self.box.pack_start(self.info, True, True, 0)
        self.restart = None
        if key in ("discord", "youtube"):
            self.restart = glass.GlassRow(shell, src, look, "Restart", key=("vitals", key, "restart"),
                                          on_activate=self.on_restart, height=p(64))
            self.restart.set_size_request(p(230), self.restart.get_size_request()[1])
            self.box.pack_start(self.restart, False, False, 0)
        self.close = ActionRow(shell, src, look, "Close", key=("vitals", key, "close"),
                                    on_activate=self.on_close, height=p(64))
        self.close.set_size_request(p(300), self.close.get_size_request()[1])
        self.close.connect("focus-out-event", lambda *_: self.disarm() and False)
        self.close.connect("destroy", lambda *_: self.disarm())
        self.box.pack_start(self.close, False, False, 0)
        self.close.set_sensitive(False)

    def focusables(self):
        return [w for w in (self.restart, self.close) if w is not None]

    def update(self, app):
        self.info.set_state(app)
        self.running = bool(app and app.get("running"))
        if self.restart is not None:
            self.restart.title = "Restart" if self.running else "Open"
            self.restart.queue_draw()
        if not self.running:
            self.disarm()
            if self.close.has_focus() and self.restart is not None:
                self.restart.grab_focus()
        self.close.set_sensitive(self.running)

    def on_restart(self):
        if self.running:
            send(self.shell, "app_restart", {"app": self.key}, "Restarting %s…" % self.title)
        else:
            send(self.shell, "select", {"app": self.key}, "Opening %s…" % self.title)

    def on_close(self):
        if not self.running:
            return
        if not self.armed:
            self.armed = GLib.timeout_add(self.ARM_MS, self.disarm)
            self.close.title, self.close.danger = "OK to close", True
            self.close.queue_draw()
            return
        self.disarm()
        send(self.shell, "app_close", {"app": self.key}, "Closing %s…" % self.title)

    def disarm(self):
        """Back to plain "Close" (also the arm timeout's callback)."""
        if self.armed:
            GLib.source_remove(self.armed)
            self.armed = 0
        if self.close.title != "Close":
            self.close.title, self.close.danger = "Close", False
            self.close.queue_draw()
        return False


# ---------------------------------------------------------------- the Home peek

class Peek(Gtk.DrawingArea):
    """Compact glass pull-down at the top of the screen: three mini rings and one line. Rendered into one
    cached surface (frost included) whenever a value moves, so redraws from Home underneath are a blit."""
    APPEAR_S = 0.28
    QUIET_S = 0.45                 # key presses this soon after a show are the key that opened it

    def __init__(self, shell):
        super().__init__()
        import display
        import widgets
        p = shell.px
        self.shell = shell
        self.look = glass.Look(widgets.load_config(display.DATA))
        self.s = glass.scale_of(shell)
        self.R, self.slot = p(50), p(190)
        self.W, self.H = 3 * self.slot + p(80), p(262)
        self.set_size_request(self.W, self.H)
        self.set_halign(Gtk.Align.CENTER)
        self.set_valign(Gtk.Align.START)
        self.set_margin_top(p(22))
        self.set_has_window(False)
        self.text = glass.TextCache(self)
        self.easer = Easer()
        self.gs = gauges({})
        self.sample = {}
        self.fb = None
        self.appear = 0.0
        self.dead = False
        self.shown = False
        self.hide_src = 0
        self.event_id = 0
        self.held = None
        self.shown_at = 0.0
        self.last_press = (None, 0.0)
        self.live = Live(self.got, self.step)
        self.connect("draw", self.on_draw)
        self.connect("destroy", self.on_destroy)

    def on_destroy(self, *_):
        self.dead = True
        self.hide_peek()

    # ---- lifetime
    def show_peek(self):
        if self.hide_src:
            GLib.source_remove(self.hide_src)
        self.hide_src = GLib.timeout_add(PEEK_MS, self.hide_peek)
        self.shown_at = time.monotonic()
        ev = Gtk.get_current_event()
        if ev is not None and ev.type == Gdk.EventType.KEY_PRESS:
            self.held = ev.get_keyval()[1]
        if self.shown:
            return
        self.shown = True
        self.appear = 0.0
        self.shell.root.add_overlay(self)
        self.show()
        self.event_id = self.shell.connect("event", self.on_event)
        self.live.start()
        self.live.animate()

    def hide_peek(self):
        """Remove it and every timer; the object can be shown again."""
        if self.hide_src:
            GLib.source_remove(self.hide_src)
        self.hide_src = 0
        self.live.stop()
        if self.event_id:
            self.shell.disconnect(self.event_id)
            self.event_id = 0
        if self.shown:
            self.shown = False
            self.held = None
            self.fb = None
            if self.get_parent() is not None and not self.dead:
                self.shell.root.remove(self)
        return False

    def timers(self):
        return self.live.timers() + ([self.hide_src] if self.hide_src else [])

    def on_event(self, _w, ev):
        """Any key dismisses (and is used up) - except the key that opened it, and its auto-repeat."""
        if ev.type == Gdk.EventType.KEY_RELEASE:
            if ev.get_keyval()[1] == self.held:
                self.held = None
            return False
        if ev.type != Gdk.EventType.KEY_PRESS:
            return False
        kv, now = ev.get_keyval()[1], time.monotonic()
        prev, self.last_press = self.last_press, (kv, now)
        if kv == self.held or now - self.shown_at < self.QUIET_S or (prev[0] == kv and now - prev[1] < 0.15):
            return False
        self.hide_peek()
        return True

    # ---- data
    def got(self, s):
        before = (summary_line(self.sample) if self.sample else None, [(g["text"], g["caption"]) for g in self.gs])
        self.sample = s
        self.gs = gauges(s)
        moving = self.easer.set(targets(self.gs))
        if moving:
            self.live.animate()
        if moving or before != (summary_line(s), [(g["text"], g["caption"]) for g in self.gs]):
            self.fb = None
            self.queue_draw()

    def step(self, dt):
        self.appear = min(1.0, self.appear + dt / self.APPEAR_S)
        more = self.easer.step(dt)
        self.fb = None
        self.queue_draw()
        return more or self.appear < 1.0

    # ---- painting
    def render(self):
        p = self.shell.px
        surf = cairo.ImageSurface(cairo.FORMAT_ARGB32, self.W, self.H)
        cr = cairo.Context(surf)
        src = getattr(self.shell, "photo_canvas", None)
        glass.paint_panel(cr, self, src, self.look, self.W, self.H, self.s,
                          dim=0.34, tint=0.08, hair=0.30)
        cy = p(28) + self.R * 1.3
        centres = [(p(40) + self.slot / 2 + i * self.slot, cy) for i in range(3)]
        paint_links(cr, centres, self.R, self.s)
        for (cx, cy), g in zip(centres, eased_gauges(self.easer, self.gs)):
            paint_ring(cr, self.text, self.look.node_shape, cx, cy, self.R, g["value"], g["heat"], g["label"],
                       g["text"], g["caption"], self.s, mini=True)
        line = summary_line(self.sample) if self.sample else "Measuring…"
        self.text.draw(cr, line, p(19), Pango.Weight.NORMAL, p(30), self.H - p(52), self.W - p(60),
                       (1, 1, 1, 0.78), Pango.Alignment.CENTER)
        return surf

    def on_draw(self, _w, cr):
        if cairo is None or not self.shown:
            return False
        if self.fb is None:
            self.fb = self.render()
        e = 1 - (1 - self.appear) ** 3
        cr.set_source_surface(self.fb, 0, 0)
        cr.paint_with_alpha(e)
        return False


def peek(shell):
    """Show (or extend) the vitals pull-down over the current screen for 8 s. Nothing exists until the first
    call; once hidden, no timer, handler or sampling is left running."""
    p = getattr(shell, "_vitals_peek", None)
    if p is None or p.dead:
        p = Peek(shell)
        shell._vitals_peek = p
    p.show_peek()
    return p
