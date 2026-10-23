"""Ambient Home: a slowly changing full-screen photo, clock, weather, pairing card and floating bubbles.

Layout comes from DATA/home.json through the widget registry (see widgets/__init__.py). CPU is the budget:
one 1 s supervisor timer always, plus a <= 20 fps animation timer that only runs while Home is the visible,
un-iconified screen with no app on top, and (except for photo/chrome fades) not in ambient mode.
"""
import math
import threading
import time

import gi
gi.require_version("Gtk", "3.0")
from gi.repository import Gtk, Gdk, GdkPixbuf, GLib

import datetime
import hashlib
import json
import os
import random

import screens
from screens import Screen, text_label, cover_pixbuf
import widgets
from widgets import network, shapes
import perflog

PERF = perflog.from_env()       # IMACTV_PERF_LOG=path: per-frame timings (None, and free, when unset)

try:
    import cairo
except Exception:  # pragma: no cover
    cairo = None
try:
    import audiolevel
except Exception:  # pragma: no cover - session A's module missing: no visualizer
    audiolevel = None
try:
    import nowplaying
except Exception:  # pragma: no cover
    nowplaying = None
try:
    import ambient
except Exception:  # pragma: no cover - data service missing: gradient only
    ambient = None
try:
    import display
except Exception:  # pragma: no cover
    display = None

ANIM_MS = 50            # 20 fps ceiling
CHROME_FADE = 1.0       # seconds, bubbles+widgets fading out/in
PHOTO_FADE = 1.5        # seconds, crossfade
REFRESH_PHOTOS = 6 * 3600
NOW_EVERY = 3.0          # seconds between YouTube now-playing polls (only while Home is visible)
SHAPE_MS = 66            # ~15 fps while a shape is morphing or the visualizer is running
FIELD_MS = 110           # ~9 fps: the quiet floating particles drift a few px/s
ANIMAL_EVERY = 300.0     # seconds between doodles while still (shapes: photo+animals)
MUSIC_DIM = 0.22         # how far the photo darkens behind the visualizer
DIM_FADE = 1.6           # seconds for the photo to darken when the visualizer starts
DIM_STEPS = 8            # ... in this many full-screen repaints (each step is ~3% darker: reads as a fade)
BLUR_K = 16              # frosted glass samples a 1/16 copy of the photo


# ---------------------------------------------------------------- pure logic

class IdleTracker:
    """Ambient mode: idle_after seconds without input -> ambient; the first input wakes (and is swallowed)."""

    def __init__(self, idle_after, now=0.0):
        self.idle_after = idle_after
        self.last = now
        self.ambient = False

    def touch(self, now):
        """Register input. Returns True when it woke us from ambient (callers swallow that key)."""
        self.last = now
        woke, self.ambient = self.ambient, False
        return woke

    def check(self, now):
        """Returns True exactly when ambient mode has just begun."""
        if not self.ambient and now - self.last >= self.idle_after:
            self.ambient = True
            return True
        return False


MUSIC_IDLE = 15.0       # seconds before the visualizer when YouTube plays music behind Home


def idle_delay(quiet, music, state, audio=MUSIC_IDLE):
    """Seconds of no input before ambient: shorter while YouTube plays in the background, shortest for music
    (the server's youtube_mode). Never longer than the quiet delay."""
    if not (state and state.get("status") == "Playing"):
        return quiet
    return min(quiet, audio if state.get("youtube_mode") == "music" else music)


def search_scope(sname):
    """Which catalog the phone/TV search should be scoped to, from the screen we are leaving."""
    return {"movies": "movie", "series": "series"}.get(sname)


def photo_label(item):
    """(caption, credit) lines for the bottom-left corner."""
    if not item:
        return "", ""
    cap = (item.get("caption") or "").strip()
    credit = (item.get("credit") or "").strip()
    src = {"bing": "Bing", "wikimedia": "Wikimedia Commons"}.get(item.get("source"), item.get("source") or "")
    parts = []
    for x in (credit, src):
        if x and x not in parts:
            parts.append(x)
    tail = " · ".join(parts)
    return cap, tail


def bottom_label(item, state=None):
    """Track title/artist while something is playing, otherwise the photo's caption and credit."""
    state = state or {}
    title = (state.get("title") or "").strip()
    if state.get("status") == "Playing" and title:
        return title, (state.get("artist") or "").strip()
    return photo_label(item)


def fade_step(value, target, dt, seconds):
    """Linear approach of value toward target taking `seconds` for a full 0..1 swing."""
    step = dt / seconds if seconds > 0 else 1.0
    if value < target:
        return min(target, value + step)
    return max(target, value - step)


def home_css(scale=1.0):
    p = lambda n: round(n * scale)
    sh = "text-shadow: 0 2px 3px rgba(0,0,0,0.55);"
    return f"""
.ah-time {{ font-size: {p(128)}px; font-weight: 200; color: #fff; {sh} }}
.ah-time-small {{ font-size: {p(56)}px; font-weight: 300; color: rgba(255,255,255,0.85); }}
.ah-date {{ font-size: {p(32)}px; font-weight: 400; color: rgba(255,255,255,0.88); {sh} }}
.ah-wicon {{ font-size: {p(34)}px; }}
.ah-wtemp {{ font-size: {p(34)}px; font-weight: 500; color: #fff; {sh} }}
.ah-wline {{ font-size: {p(23)}px; color: rgba(255,255,255,0.72); {sh} }}
.ah-line {{ padding: {p(2)}px {p(8)}px; }}
.ah-drop {{ padding: {p(10)}px {p(26)}px {p(22)}px; }}
.ah-scrim {{ background-color: rgba(4,6,12,0.62); }}
.ah-sheet {{ background-image: linear-gradient(160deg, rgba(34,42,62,0.88), rgba(12,15,24,0.92)); border: 1px solid rgba(255,255,255,0.24); border-radius: {p(34)}px; padding: {p(44)}px {p(70)}px; }}
.ah-dbtn-hot {{ background: rgba(255,255,255,0.16); }}
.ah-cardbtn {{ background: transparent; border: {p(2)}px solid transparent; border-radius: {p(22)}px; padding: {p(8)}px {p(12)}px; box-shadow: none; }}
.ah-cardbtn:hover {{ background: rgba(255,255,255,0.07); }}
.ah-cardbtn:focus {{ background: rgba(255,255,255,0.14); border-color: rgba(255,255,255,0.85); }}
.ah-kicker {{ font-size: {p(20)}px; color: rgba(255,255,255,0.88); font-weight: 400; text-shadow: 0 1px 3px rgba(0,0,0,0.65); }}
.ah-code {{ font-size: {p(44)}px; font-weight: 300; letter-spacing: {p(4)}px; color: #fff; text-shadow: 0 2px 3px rgba(0,0,0,0.6); }}
.ah-net {{ font-size: {p(18)}px; color: rgba(255,255,255,0.85); text-shadow: 0 1px 3px rgba(0,0,0,0.65); }}
.ah-power {{ background: transparent; border: {p(2)}px solid transparent; border-radius: {p(40)}px; color: rgba(255,255,255,0.8); font-size: {p(26)}px; padding: {p(6)}px {p(14)}px; box-shadow: none; }}
.ah-power:hover {{ background: rgba(255,255,255,0.08); }}
.ah-power:focus {{ background: rgba(255,255,255,0.14); border-color: rgba(255,255,255,0.85); color: #fff; }}
.ah-qr-frame {{ background: #fff; border-radius: {p(14)}px; padding: {p(6)}px; }}
.ah-cap {{ font-size: {p(22)}px; color: rgba(255,255,255,0.72); text-shadow: 0 1px 3px rgba(0,0,0,0.8); }}
.ah-credit {{ font-size: {p(17)}px; color: rgba(255,255,255,0.55); text-shadow: 0 1px 3px rgba(0,0,0,0.8); }}
.ah-dtitle {{ font-size: {p(42)}px; font-weight: 300; color: #fff; }}
.ah-dsub {{ font-size: {p(22)}px; color: rgba(255,255,255,0.62); }}
.ah-dcode {{ font-size: {p(64)}px; font-weight: 200; letter-spacing: {p(8)}px; color: #fff; }}
.ah-daddr {{ font-size: {p(26)}px; color: rgba(255,255,255,0.8); font-family: monospace; }}
.ah-dbtn {{ background: rgba(255,255,255,0.12); border: {p(2)}px solid rgba(255,255,255,0.35); border-radius: {p(40)}px; color: #fff; font-size: {p(24)}px; padding: {p(10)}px {p(48)}px; box-shadow: none; }}
.ah-dbtn:focus {{ background: rgba(255,255,255,0.28); border-color: #fff; }}
"""


# ---------------------------------------------------------------- photo canvas

def gray_rows(pb):
    """Small grayscale rows of a pixbuf (~240 px wide) for horizon tracing."""
    sm = pb.scale_simple(shapes.TRACE_W, max(20, round(shapes.TRACE_W * pb.get_height() / pb.get_width())),
                         GdkPixbuf.InterpType.BILINEAR)
    w, h, rs, nc, d = sm.get_width(), sm.get_height(), sm.get_rowstride(), sm.get_n_channels(), sm.get_pixels()
    gray = [[(d[y * rs + x * nc] * 77 + d[y * rs + x * nc + 1] * 150 + d[y * rs + x * nc + 2] * 29) >> 8
             for x in range(w)] for y in range(h)]
    blue = [[d[y * rs + x * nc + 2] - d[y * rs + x * nc] for x in range(w)] for y in range(h)]
    return gray, blue


def traced(path, w, h):
    """Worker-thread: horizon trace for a photo, cached in DATA/shapecache keyed by file name + size
    (the photo is only decoded again on a cache miss)."""
    try:
        st = os.stat(path)
        key = hashlib.sha1(f"v2:{os.path.basename(path)}:{st.st_size}:{int(st.st_mtime)}".encode()).hexdigest()[:20]
        cache = screens.DATA / "shapecache"
        f = cache / (key + ".json")
        try:
            return json.loads(f.read_text())["trace"]
        except (OSError, ValueError, KeyError):
            pass
        trace = shapes.trace_horizon(*gray_rows(cover_pixbuf(path, w, h)))
        cache.mkdir(parents=True, exist_ok=True)
        f.write_text(json.dumps({"trace": trace}))
        old = sorted(cache.glob("*.json"), key=lambda p: p.stat().st_mtime)[:-80]
        for p in old:
            p.unlink(missing_ok=True)
        return trace
    except Exception as exc:
        print("home trace:", exc)
        return None


def surface_palette(small):
    """Accent colours for the visualizer from the small (frosted-glass) copy of a photo."""
    try:
        small.flush()
        return shapes.palette_from_bgrx(bytes(small.get_data()), small.get_width(), small.get_height(),
                                        small.get_stride())
    except Exception as exc:
        print("home palette:", exc)
        return None


def load_surface(path, w, h):
    """Worker-thread: decode, cover-crop and bake a soft scrim (readable clock/caption) into an ARGB surface."""
    pb = cover_pixbuf(path, w, h)
    surf = cairo.ImageSurface(cairo.FORMAT_RGB24, w, h)
    cr = cairo.Context(surf)
    Gdk.cairo_set_source_pixbuf(cr, pb, 0, 0)
    cr.paint()
    top = cairo.LinearGradient(0, 0, 0, h * 0.45)
    top.add_color_stop_rgba(0, 0, 0, 0, 0.50)
    top.add_color_stop_rgba(1, 0, 0, 0, 0)
    cr.set_source(top)
    cr.paint()
    bot = cairo.LinearGradient(0, h * 0.5, 0, h)
    bot.add_color_stop_rgba(0, 0, 0, 0, 0)
    bot.add_color_stop_rgba(1, 0, 0, 0, 0.62)
    cr.set_source(bot)
    cr.paint()
    left = cairo.LinearGradient(0, 0, w * 0.45, 0)
    left.add_color_stop_rgba(0, 0, 0, 0, 0.38)
    left.add_color_stop_rgba(1, 0, 0, 0, 0)
    cr.set_source(left)
    cr.paint()
    cr.set_source_rgba(0, 0, 0, 0.22)
    cr.paint()
    small = cairo.ImageSurface(cairo.FORMAT_RGB24, max(2, w // BLUR_K), max(2, h // BLUR_K))
    sc = cairo.Context(small)
    sc.scale(small.get_width() / w, small.get_height() / h)
    sc.set_source_surface(surf, 0, 0)
    sc.get_source().set_filter(cairo.FILTER_BEST)
    sc.paint()
    return surf, small


class PhotoCanvas(Gtk.DrawingArea):
    """Full-screen photo with a crossfade; draws a calm dark gradient when there is no photo."""

    def __init__(self):
        super().__init__()
        self.cur = self.prev = None
        self.blur_cur = self.blur_prev = None
        self.blur_k = BLUR_K
        self.extra = None       # hook(cr): the network lines are drawn here, in the same pass as the photo
        self.ambient = False    # ambient mode shows the same sharp photo; dim darkens it while music plays
        self.fade = 1.0
        self.dim = 0.0          # drawn darkness 0..1, in 1/DIM_STEPS steps (the screen eases it)
        self.fb = None          # the client-side framebuffer the lines are drawn into (see on_draw)
        self.dark = None        # (photo, dim, the photo pre-darkened to that dim)
        self.set_has_window(False)
        self.connect("draw", self.on_draw)

    def show_surface(self, surf, blur=None, animate=True):
        self.prev, self.cur = self.cur, surf
        self.blur_prev, self.blur_cur = self.blur_cur, blur
        self.fade = 0.0 if (animate and self.prev is not None) else 1.0
        if self.fade >= 1.0:
            self.prev = self.blur_prev = None
        self.queue_draw()

    def blur_layers(self):
        """[(surface, alpha)] to paint (bottom first) for frosted glass, following the crossfade."""
        if self.blur_cur is None:
            return []
        if self.blur_prev is not None and self.fade < 1.0:
            return [(self.blur_prev, 1.0), (self.blur_cur, self.fade * self.fade * (3 - 2 * self.fade))]
        return [(self.blur_cur, 1.0)]

    def fading(self):
        return self.fade < 1.0

    def step(self, dt):
        if self.fade < 1.0:
            self.fade = fade_step(self.fade, 1.0, dt, PHOTO_FADE)
            if self.fade >= 1.0:
                self.prev = self.blur_prev = None
            self.queue_draw()

    def on_draw(self, _w, cr):
        w, h = self.get_allocated_width(), self.get_allocated_height()
        if not self.extra:
            self.fb = None                          # free the framebuffer once nothing is drawn over the photo
            paint_background(self, cr, w, h)
            return False
        # The lines are many tiny strokes: rasterize them here (pixman) and upload the result once, instead of
        # having the X server trace every segment.
        fb = self.fb
        if fb is None or fb.get_width() != w or fb.get_height() != h:
            fb = self.fb = cairo.ImageSurface(cairo.FORMAT_RGB24, w, h)
        fcr = cairo.Context(fb)
        for x, y, rw, rh in clip_rects(cr, w, h):
            fcr.rectangle(x, y, rw, rh)
        fcr.clip()
        paint_background(self, fcr, w, h)
        self.extra(fcr)
        t0 = PERF.now() if PERF else 0.0
        fb.flush()
        cr.set_operator(cairo.OPERATOR_SOURCE)
        cr.set_source_surface(fb, 0, 0)
        cr.paint()
        cr.set_operator(cairo.OPERATOR_OVER)
        if PERF:
            PERF.layer("blit", PERF.ms(t0))
        return False


WHITE = (1, 1, 1)


def paint_background(self, cr, w, h):
    """The photo (or gradient / crossfade) plus the visualizer's dim overlay, onto any context."""
    t0 = PERF.now() if PERF else 0.0
    if self.cur is None:
        g = cairo.LinearGradient(0, 0, w, h)
        g.add_color_stop_rgb(0, 0.05, 0.07, 0.12)
        g.add_color_stop_rgb(0.55, 0.07, 0.09, 0.15)
        g.add_color_stop_rgb(1, 0.02, 0.03, 0.06)
        cr.set_source(g)
        cr.paint()
    elif self.prev is not None and self.fade < 1.0:
        cr.set_source_surface(self.prev, 0, 0)
        cr.paint()
        e = self.fade * self.fade * (3 - 2 * self.fade)
        cr.set_source_surface(self.cur, 0, 0)
        cr.paint_with_alpha(e)
    else:
        dark = dimmed(self)
        cr.set_operator(cairo.OPERATOR_SOURCE)
        cr.set_source_surface(dark or self.cur, 0, 0)
        cr.paint()
        cr.set_operator(cairo.OPERATOR_OVER)
        if dark is not None:
            if PERF:
                PERF.layer("photo", PERF.ms(t0))
            return
    if PERF:
        PERF.layer("photo", PERF.ms(t0))
        t0 = PERF.now()
    if self.dim > 0.003:            # the visualizer's stage: the same sharp photo, a little darker
        cr.set_source_rgba(0.01, 0.015, 0.035, MUSIC_DIM * self.dim)
        cr.paint()
    if PERF:
        PERF.layer("dim", PERF.ms(t0))


def clip_rects(cr, w, h):
    """[(x, y, w, h)] of cr's clip, or the whole area when it cannot be listed."""
    try:
        rects = cr.copy_clip_rectangle_list()
        if rects:
            return [(int(r.x), int(r.y), int(r.width), int(r.height)) for r in rects]
        ext = cr.clip_extents()
        if ext[2] > ext[0] and ext[3] > ext[1]:
            x0, y0, x1, y1 = int(ext[0]), int(ext[1]), math.ceil(ext[2]), math.ceil(ext[3])
            return [(x0, y0, x1 - x0, y1 - y0)]
    except Exception:
        pass
    return [(0, 0, w, h)]


def batch_segments(segs, alpha_steps=8, amax=1.0):
    """Group line segments so each (colour, alpha, width) is one stroke. segs: (rgb, alpha, width, x0, y0, x1, y1).
    Alpha is quantized to alpha_steps levels over 0..amax (never to zero for a visible segment), width to 0.5 px;
    segments with alpha <= 0 are dropped.
    Returns [(rgb, alpha, width, [(x0, y0, x1, y1), ...])] in first-seen order."""
    groups = {}
    for rgb, a, wd, x0, y0, x1, y1 in segs:
        if a <= 0:
            continue
        qa = max(1, round(min(a, amax) / amax * alpha_steps)) * amax / alpha_steps
        key = (tuple(rgb), qa, max(0.5, round(wd * 2) / 2))
        groups.setdefault(key, []).append((x0, y0, x1, y1))
    return [(k[0], k[1], k[2], v) for k, v in groups.items()]


def stroke_batches(cr, batches):
    for rgb, a, wd, lines in batches:
        cr.set_source_rgba(*rgb, a)
        cr.set_line_width(wd)
        for x0, y0, x1, y1 in lines:
            cr.move_to(x0, y0)
            cr.line_to(x1, y1)
        cr.stroke()


def dimmed(canvas):
    """The still photo fully darkened, rendered once: while the visualizer runs, each partial repaint is one
    blit instead of the photo plus a darkening pass. None while not fully dimmed (the fade steps paint the
    overlay directly: each step is a full repaint anyway)."""
    if canvas.dim < 1.0 or canvas.cur is None or cairo is None:
        if getattr(canvas, "dark", None) is not None:
            canvas.dark = None                  # free the copy (one screen of pixels) once the music stops
        return None
    d = getattr(canvas, "dark", None)
    if d is not None and d[0] is canvas.cur and d[1] == canvas.dim:
        return d[2]
    src = canvas.cur
    surf = cairo.ImageSurface(cairo.FORMAT_RGB24, src.get_width(), src.get_height())
    c = cairo.Context(surf)
    c.set_operator(cairo.OPERATOR_SOURCE)
    c.set_source_surface(src, 0, 0)
    c.paint()
    c.set_operator(cairo.OPERATOR_OVER)
    c.set_source_rgba(0.01, 0.015, 0.035, MUSIC_DIM * canvas.dim)
    c.paint()
    surf.flush()
    canvas.dark = (src, canvas.dim, surf)
    return surf


def viz_base_alpha(lv):
    """The visualizer's link opacity before per-link weights: a background, music modulates, never dominates."""
    gain = 0.55 + 0.45 * min(1.0, lv["bass"] + lv["mid"])
    return (0.11 + 0.06 * lv["mid"] + 0.10 * lv["beat"]) * gain


def viz_link_alpha(base_a, la, alphas, depth, i, j):
    dd = (depth[i] + depth[j]) / 2 if depth else 0.75
    return base_a * la * min(alphas[i], alphas[j]) * (0.30 + 0.70 * dd)


def dim_level(progress):
    """Eased fade progress 0..1 -> the darkness actually drawn, in 1/DIM_STEPS steps."""
    p = max(0.0, min(1.0, progress))
    return round(p * p * (3 - 2 * p) * DIM_STEPS) / DIM_STEPS


# ---------------------------------------------------------------- the screen

class AmbientHomeScreen(Screen):
    sname = "home"
    header = False

    def __init__(self, shell):
        super().__init__(shell)
        px = shell.px
        self.cfg = widgets.load_config(screens.DATA)
        shell.home_cfg = self.cfg
        amb = self.cfg["ambient"]
        self.interval = amb["interval"]
        self.idle = IdleTracker(amb["idle"], time.monotonic())
        self.idle_quiet, self.idle_music = amb["idle"], amb["idle_music"]
        self.chrome = 1.0
        self.anim_id = 0
        self.last_anim = time.monotonic()
        self.photo_due = 0.0
        self.pending = None
        self.loading = False
        self.current_item = None
        self.library = ambient.Library(screens.DATA, shell.sw, shell.sh) if ambient else None
        self.style = self.cfg["style"]
        self.auto_focus = True
        self.dust_amt = 1.0
        self.now_state = None
        self.now_due = 0.0
        self.now_busy = False
        self.dust = []
        self.geom = None
        self.prev_cells = set()
        self.shapes_mode = self.cfg["shapes"] if self.style == "network" else "off"
        self.viz_on = self.cfg["visualizer"] and self.style == "network"
        self.morph = shapes.Morph()
        self.trace = None              # the current photo's sky-line (normalised), or None
        self.palette = None            # the current photo's accent colours for the visualizer
        self.doodles = shapes.load_doodles(screens.ASSETS / "shapes.json") if self.style == "network" else []
        self.viz = None                # shapes.Viz while the music visualizer runs
        self.viz_stop = False          # music stopped: flowing back to the still sky-line
        self.viz_prev = None           # last frame's dot positions (motion streaks)
        self.beat_raw = 0.0
        self.last_onset = None         # audio.levels()["onset"] at the last frame (a change = a beat)
        self.dim_t = 0.0               # dim fade progress; the canvas draws it in DIM_STEPS steps
        self.viz_modes = [m for m in os.environ.get("IMACTV_VIZ_MODES", "").split(",") if m] or None  # screenshot hook
        self.viz_every = float(os.environ.get("IMACTV_VIZ_EVERY") or 0) or None                      # screenshot hook
        self.glow = None
        self.perf_viz_ms = 0.0
        self.field = None              # shapes.Field: the quiet floating particles (no music)
        self.field_id = 0
        self.field_geom = None
        self.field_cells = set()
        self.last_field = 0.0
        self.still = None              # the still shape the dots hold (sky-line, doodle or wave)
        self.shaped = False            # a shape has been chosen for this ambient stretch
        self.shape_since = 0.0
        self.last_animal = -ANIMAL_EVERY
        self.shape_geom = None
        self.shape_cells = set()
        self.shape_id = 0
        self.shape_period = SHAPE_MS
        self.last_shape = 0.0
        self.lv = {"bass": 0.0, "mid": 0.0, "treble": 0.0, "beat": 0.0}
        self.audio = None
        self.audio_retry_due = 0.0
        self.fake_levels = bool(os.environ.get("IMACTV_FAKE_LEVELS"))   # test/screenshot hook only
        self.fake_from = time.monotonic() + float(os.environ.get("IMACTV_FAKE_LEVELS_AFTER") or 0)  # perf runs
        self.rng = random.Random()
        self.extra_origin = []
        self.disposed = False

        overlay = Gtk.Overlay()
        self.pack_start(overlay, True, True, 0)
        if PERF:
            overlay.connect("draw", self.perf_draw_begin)
            overlay.connect_after("draw", self.perf_draw_end)
        self.canvas = PhotoCanvas()
        overlay.add(self.canvas)
        shell.photo_canvas = self.canvas
        if self.style == "network":
            seed = datetime.date.today().toordinal()
            self.dust = [(x, y, seed * 100 + i) for i, (x, y) in
                         enumerate(network.dust_points(shell.sw, shell.sh, seed))]
            xr = random.Random(seed)
            self.extra_origin = [(xr.uniform(0.04, 0.96) * shell.sw, xr.uniform(0.06, 0.94) * shell.sh,
                                  seed * 100 + 50 + i) for i in range(160)]
            self.canvas.extra = self.draw_links

        self.items = widgets.build_all(shell, self.cfg)
        self.cont = next((w for w in self.items if hasattr(w, "set_now")), None)
        self.chrome_widgets = []
        self.due = {}
        left = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        left.set_halign(Gtk.Align.START)
        left.set_valign(Gtk.Align.START)
        left.set_margin_start(px(96))
        left.set_margin_top(px(56))
        right = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        right.set_halign(Gtk.Align.END)
        right.set_valign(Gtk.Align.START)
        right.set_margin_end(px(72))
        right.set_margin_top(px(64))
        overlay.add_overlay(left)
        for w in sorted(self.items, key=lambda i: i.order):
            if w.slot == "topleft":
                left.pack_start(w.widget, False, False, 0)
            elif w.slot == "topright":
                right.pack_start(w.widget, False, False, 0)
            else:
                w.widget.set_halign(Gtk.Align.START)
                w.widget.set_valign(Gtk.Align.START)
                w.widget.set_margin_top(round(shell.sh * getattr(w, "top", 0.5)))
                overlay.add_overlay(w.widget)
            if not w.keep_in_ambient:
                self.chrome_widgets.append(w.widget)
            self.due[w.id] = 0.0

        overlay.add_overlay(right)      # after the rows: the pairing drop-down floats above the tiles
        cap = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        cap.set_halign(Gtk.Align.START)
        cap.set_valign(Gtk.Align.END)
        cap.set_margin_start(px(96))
        cap.set_margin_bottom(px(40))
        cap.set_size_request(px(470), -1)
        self.cap = text_label("", "ah-cap")
        self.credit = text_label("", "ah-credit")
        cap.pack_start(self.cap, False, False, 0)
        cap.pack_start(self.credit, False, False, 0)
        overlay.add_overlay(cap)
        cap.set_no_show_all(False)
        for w in (self.cap, self.credit):
            w.set_ellipsize(3)
            w.set_max_width_chars(34)
        self.credit.set_opacity(0.82)

        if PERF:                            # the widgets drawn over the photo, timed apart from GTK's own work
            for n, kid in enumerate(overlay.get_children()):
                if kid is not self.canvas:
                    kid.connect("draw", self.perf_kid_begin)
                    kid.connect_after("draw", self.perf_kid_end, "%d_%s" % (n, ",".join(
                        type(c).__name__ for c in getattr(kid, "get_children", lambda: [])()[:2])))

        GLib.timeout_add(1000, self.supervise)
        if display:
            display.subscribe(self.on_screen)
        if self.library:
            self.library.refresh_async(on_done=lambda added: GLib.idle_add(self.photos_changed))
            self.refresh_due = time.monotonic() + REFRESH_PHOTOS
        self.preload()

    # ---- Screen API
    def scroll_top(self):
        pass

    def rows(self):
        rows = [w for w in self.items if w.slot == "topright"]
        rows = [w.focus_items() for w in sorted(rows, key=lambda i: i.order)]
        body = sorted((w for w in self.items if w.slot == "row"), key=lambda i: getattr(i, "top", 0))
        rows += [w.focus_items() for w in body]
        return rows

    def focus_groups(self):
        return [r for r in self.rows() if r]

    def first_focus(self, flat):
        body = [w.focus_items() for w in sorted((w for w in self.items if w.slot == "row"),
                                                  key=lambda i: getattr(i, "top", 0))]
        for r in body:
            if r:
                return r[0]
        return flat[0] if flat else None

    def on_enter(self, returning):
        self.poke()
        for w in self.items:
            w.on_enter(returning)
        self.supervise()

    # ---- ambient mode / input
    def poke(self, key=False):
        """Register user input; True when it only woke the screen from ambient mode (swallow the key)."""
        woke = self.idle.touch(time.monotonic())
        if woke:
            self.canvas.ambient = False
            self.canvas.queue_draw()
            self.update_bottom_label()
            for w in self.items:
                if w.keep_in_ambient:
                    w.on_ambient(False)
            self.wake_shapes()
            self.start_anim()
        return woke

    def enter_ambient(self):
        self.canvas.ambient = True
        self.canvas.queue_draw()
        self.update_bottom_label()
        for w in self.items:
            if w.keep_in_ambient:
                w.on_ambient(True)
        self.start_anim()

    # ---- photos
    def preload(self):
        if self.loading or not self.library:
            return
        self.loading = True

        def work():
            item, surf = None, None
            try:
                item = self.library.next()
                if item:
                    surf = load_surface(item["path"], self.shell.sw, self.shell.sh)   # (photo, blurred copy)
                    trace = traced(item["path"], self.shell.sw, self.shell.sh) if self.style == "network" else None
                    item = dict(item, trace=trace, palette=surface_palette(surf[1]))
            except Exception as exc:
                print("home photo:", exc)
                surf = None
            GLib.idle_add(self.preloaded, item, surf)
        threading.Thread(target=work, daemon=True).start()

    def preloaded(self, item, surf):
        self.loading = False
        if surf is not None:
            self.pending = (item, surf)
            if self.canvas.cur is None:
                self.swap_photo()
        return False

    def photos_changed(self):
        if self.canvas.cur is None and not self.pending:
            self.preload()
        return False

    def swap_photo(self):
        if not self.pending:
            self.preload()
            return
        item, (surf, blur) = self.pending
        self.pending = None
        self.current_item = item
        self.trace = item.get("trace") if item else None
        self.palette = item.get("palette") if item else None
        self.update_bottom_label()
        self.canvas.show_surface(surf, blur, animate=self.canvas.cur is not None)
        self.photo_due = time.monotonic() + self.interval
        if self.viz:
            base = self.base_shape()
            if base:
                self.viz.set_base(base)
        elif self.shaped and self.morph.kind == "photo":
            self.reform(time.monotonic())
        self.start_anim()
        self.preload()

    # ---- timers
    def active(self):
        """Home is what the user is looking at: mapped, not minimised, no other app in front, and some screen is
        on to show it (display.screen_live)."""
        if getattr(self, "disposed", False) or not self.get_mapped() or self.shell.prev_app not in (None, "home"):
            return False
        if display and not display.screen_live():
            return False
        win = self.shell.get_window()
        return not (win and win.get_state() & Gdk.WindowState.ICONIFIED)

    def on_screen(self, live):
        """display.screen_live() changed. Off: supervise() sees active() False and stops every timer, the audio
        capture, photo rotation and widget (weather) polling. On: one photo + widget refresh right away."""
        if self.disposed:
            return
        if live:
            for k in self.due:
                self.due[k] = 0.0
            self.photo_due = 0.0
            self.now_due = 0.0
            self.poke()                             # coming back is like a key press: show the chrome
        self.supervise()

    def dispose(self):
        """The shell is rebuilding (resolution change): stop everything this screen started."""
        self.disposed = True
        if display:
            display.unsubscribe(self.on_screen)
        self.stop_anim()
        self.stop_audio()
        for attr in ("shape_id", "field_id"):
            if getattr(self, attr):
                GLib.source_remove(getattr(self, attr))
                setattr(self, attr, 0)

    def supervise(self):
        now = time.monotonic()
        if getattr(self, "disposed", False):
            return False
        if not self.active():
            self.stop_anim()
            if self.audio:
                self.stop_audio()
            if self.shape_id:
                GLib.source_remove(self.shape_id)
                self.shape_id = 0
            return True
        if self.shell.sheet:
            self.idle.touch(now)          # an open pairing/power sheet counts as use
        self.idle.idle_after = idle_delay(self.idle_quiet, self.idle_music, self.now_state)
        if self.idle.check(now):
            self.enter_ambient()
        for w in self.items:
            if w.interval and now >= self.due.get(w.id, 0):
                self.due[w.id] = now + w.interval
                try:
                    w.refresh()
                except Exception as exc:
                    print("widget", w.id, "refresh:", exc)
        if nowplaying and now >= self.now_due and not self.now_busy:
            self.now_due = now + NOW_EVERY
            self.poll_now()
        if self.library and now >= self.photo_due and self.pending:
            self.swap_photo()
        elif self.library and now >= self.photo_due and not self.loading and self.canvas.cur is None:
            self.preload()
        if self.library and now >= getattr(self, "refresh_due", 1e18):
            self.refresh_due = now + REFRESH_PHOTOS
            self.library.refresh_async(on_done=lambda added: GLib.idle_add(self.photos_changed))
        self.sync_shapes(now)
        self.sync_field()
        self.start_anim()
        return True

    def poll_now(self):
        self.now_busy = True

        def work():
            try:
                state = nowplaying.current()
                if state and hasattr(nowplaying, "read_mode"):
                    mode, _reason = nowplaying.read_mode(screens.DATA, state)
                    state = dict(state, youtube_mode=mode)
            except Exception:
                state = None
            GLib.idle_add(self.set_now, state)
        threading.Thread(target=work, daemon=True).start()

    def set_now(self, state):
        self.now_busy = False
        key = lambda s: (s["status"], s["title"], s["artist"]) if s else None
        if key(state) != key(self.now_state):
            new_track = bool(state and self.now_state and state.get("title") != self.now_state.get("title"))
            self.now_state = state
            if self.viz and new_track and state.get("status") == "Playing" and not self.viz_stop:
                self.viz.switch(t=time.monotonic(), lv=self.lv)          # every track gets a fresh mode
            if self.cont:
                self.cont.set_now(state, auto_focus=self.auto_focus)
            self.update_bottom_label()
        elif state:
            self.now_state = state          # same track; the music/video answer may have arrived
        return False

    def update_bottom_label(self):
        cap, credit = bottom_label(self.current_item, self.now_state)
        self.cap.set_text(cap)
        self.credit.set_text(credit)

    def update_links(self, now):
        """Network style: recompute node/dust/link geometry for this frame and repaint only the cells touched
        by this frame's and the previous frame's lines (the photo underneath is a full-screen blit)."""
        nodes = []
        for w in self.items:
            if w.slot == "row":
                for n in w.focus_items():
                    c = n.link_center(self.canvas) if hasattr(n, "link_center") else None
                    if c:
                        nodes.append((c[0], c[1], n.has_focus(), bool(getattr(n, "now", None))))
            elif hasattr(w, "link_point"):
                p = w.link_point(self.canvas)
                if p:
                    nodes.append((p[0], p[1], p[2], False))
        dust = [] if self.morph.target is not None else self.dust     # the morph draws the dots while it owns them
        self.geom = network.compute_geometry(self.shell, nodes, dust, now)
        if self.canvas.fading():
            self.canvas.queue_draw()
            return
        cells = network.geometry_cells(self.geom) | self.prev_cells
        self.prev_cells = network.geometry_cells(self.geom)
        region = cairo.Region()
        c = network.CELL
        for gx, gy in cells:
            region.union(cairo.RectangleInt(gx * c, gy * c, c, c))
        self.canvas.queue_draw_region(region)

    def draw_links(self, cr):
        owned = self.morph.target is not None        # the morph draws the dots while it owns them
        if PERF:
            t0 = PERF.now()
            network.draw_network(cr, self.geom, self.chrome, 0.0 if owned else self.dust_amt)
            PERF.layer("links", PERF.ms(t0))
            t0 = PERF.now()
            self.draw_field(cr)
            PERF.layer("field", PERF.ms(t0))
            t0 = PERF.now()
            self.draw_shape(cr)
            PERF.layer("shape", PERF.ms(t0))
            return
        network.draw_network(cr, self.geom, self.chrome, 0.0 if owned else self.dust_amt)
        self.draw_field(cr)
        self.draw_shape(cr)

    # ---- perf harness (IMACTV_PERF_LOG)
    def perf_phase(self):
        if not self.idle.ambient:
            return "home"
        if self.chrome > 0.0:
            return "to_ambient"
        v = self.viz
        if v is None:
            return "morph" if self.morph.moving else "still"
        if self.viz_stop:
            return "viz_stop"
        if self.canvas.dim < 1.0:
            return "viz_start"
        return "viz" if v.settled else "switch"

    def perf_timers(self):
        return "+".join(n for n, i in (("anim", self.anim_id), ("shape", self.shape_id), ("field", self.field_id)) if i)

    def perf_draw_begin(self, _w, cr):
        PERF.draw_begin(perflog.clip_area(cr, self.shell.sw, self.shell.sh))
        return False

    def perf_draw_end(self, _w, _cr):
        PERF.draw_end(self.perf_phase(), timers=self.perf_timers(), dim=round(self.canvas.dim, 3))
        return False

    def perf_kid_begin(self, _w, _cr):
        self.perf_kid_t = PERF.now()
        return False

    def perf_kid_end(self, _w, _cr, name):
        PERF.layer("kids", PERF.ms(self.perf_kid_t))
        PERF.layer("k_" + name, PERF.ms(self.perf_kid_t))
        return False

    def perf_wrap_viz(self, viz):
        """Time Viz.switch() (it builds the next mode) on the main thread: shapes.py's cost, logged here."""
        inner = viz.switch

        def switch(*a, **kw):
            t0 = PERF.now()
            inner(*a, **kw)
            PERF.mark("viz_switch", ms=round(PERF.ms(t0), 3), mode=viz.mode)
        viz.switch = switch

    # ---- quiet floating particles
    def field_target(self):
        """1 while ambient is fully faded in and nothing plays; the field needs no photo trace."""
        return 1.0 if (self.style == "network" and self.idle.ambient and self.chrome <= 0.0
                       and self.active() and not self.music_active()) else 0.0

    def sync_field(self):
        if self.style != "network":
            return
        if self.field_target() > 0.0 or (self.field is not None and self.field.amt > 0.0):
            self.start_field_timer()

    def widget_fading(self):
        return any(getattr(w, "fade_id", 0) for w in getattr(self, "items", ()))

    def shape_drives(self):
        """The 15 fps shape timer is running: it also steps the particles and the photo crossfade, so the
        screen gets one repaint per frame instead of one per timer."""
        return bool(self.shape_id) and self.shape_period == SHAPE_MS

    def start_field_timer(self):
        if self.field_id or not self.active() or self.shape_drives():
            return
        self.last_field = time.monotonic()
        self.field_id = GLib.timeout_add(FIELD_MS, self.field_tick)

    def field_tick(self):
        if PERF:
            t0 = PERF.now()
            ok = self._field_tick()
            PERF.tick("field", t0, self.perf_phase())
            return ok
        return self._field_tick()

    def _field_tick(self):
        now = time.monotonic()
        dt = min(0.5, now - self.last_field)
        self.last_field = now
        if not self.active() or self.shape_drives():     # the shape timer carries the particles from here
            self.field_id = 0
            return False
        self.step_field(dt, now)
        if self.field_target() <= 0.0 and (self.field is None or self.field.amt <= 0.0):
            self.field_id = 0
            return False
        return True

    def step_field(self, dt, now):
        """Advance the particles and repaint only the cells they (and last frame's) touch."""
        if self.field is None:
            self.field = shapes.Field(self.shell.sw, self.shell.sh, self.rng)
        f = self.field
        f.step(dt, self.field_target())
        if f.amt <= 0.0:
            if self.field_geom is not None:
                self.field_geom = None
                self.invalidate_cells(self.field_cells)
                self.field_cells = set()
            return
        s = self.shell.scale
        fr = f.frame(now, reach=0.16 * self.shell.sw)
        fr["links"] = [(i, j, a) for i, j, a in fr["links"] if a > 0.01]
        geo = [(i, j, math.hypot(fr["pts"][i][0] - fr["pts"][j][0], fr["pts"][i][1] - fr["pts"][j][1]))
               for i, j, _ in fr["links"]]
        cells = network.geometry_cells({"pts": fr["pts"], "n": 0, "links": geo}, margin=round(8 * s))
        self.field_geom = dict(fr, scale=s)
        dirty = cells | self.field_cells
        self.field_cells = cells
        self.invalidate_cells(dirty)

    def draw_field(self, cr):
        g = self.field_geom
        if not g:
            return
        s, pts, al = g["scale"], g["pts"], g["alpha"]
        cr.save()
        cr.set_line_cap(cairo.LINE_CAP_ROUND)
        wd = max(1.0, 1.0 * s)
        segs = [(WHITE, min(0.32, 0.34 * a), wd, *pts[i], *pts[j]) for i, j, a in g["links"]]
        stroke_batches(cr, batch_segments(segs, 8, 0.32))
        for (x, y), a in zip(pts, al):
            if a < 0.02:
                continue
            d = 3.0 * s
            cr.set_source_rgba(1, 1, 1, min(0.85, 0.9 * a))
            cr.move_to(x, y - d)
            cr.line_to(x + d, y)
            cr.line_to(x, y + d)
            cr.line_to(x - d, y)
            cr.close_path()
            cr.fill()
        cr.restore()

    # ---- ambient shapes + visualizer
    def origin(self, i, t):
        """Where dot i drifts when it is not part of a shape (first ones are the dust; the rest are spares)."""
        pool = self.dust if i < len(self.dust) else self.extra_origin
        j = i if i < len(self.dust) else (i - len(self.dust)) % len(self.extra_origin)
        x, y, seed = pool[j]
        dx, dy = network.drift(t, seed, 10 * self.shell.scale)
        return x + dx, y + dy

    def music_active(self):
        if self.fake_levels:
            return time.monotonic() >= self.fake_from
        st = self.now_state
        if st and st.get("status") == "Playing":
            return True
        return bool(self.audio and self.audio.levels().get("active"))

    def shape_candidate(self):
        return self.shapes_mode != "off" and (self.trace is not None) or (self.viz_on and self.music_active())

    def base_shape(self):
        """The photo's sky-line in screen pixels (or a calm wave when the photo has none and music plays)."""
        sw, sh = self.shell.sw, self.shell.sh
        if self.shapes_mode != "off" and self.trace:
            return {"pts": [(x * sw, y * sh) for x, y in self.trace["pts"]], "links": self.trace["links"],
                    "kind": "photo"}
        if self.viz_on and self.music_active():
            sp = shapes.wave_shape()
            sp["pts"] = [(x * sw, y * sh) for x, y in sp["pts"]]
            return sp
        return None

    def pick_shape(self, now):
        if (self.shapes_mode == "photo+animals" and self.doodles and now - self.last_animal >= ANIMAL_EVERY
                and not self.music_active()):
            self.last_animal = now
            return shapes.place_doodle(self.rng.choice(self.doodles), self.shell.sw, self.shell.sh, self.rng)
        return self.base_shape()

    def begin_shapes(self, now):
        """Called once the tiles/labels have faded: dust glides onto the chosen shape."""
        if self.style != "network" or self.shaped or not self.idle.ambient or self.chrome > 0.0:
            return
        shape = self.pick_shape(now)
        if shape:
            self.shaped = True
            self.shape_since = now
            self.viz = None
            self.still = shape
            self.morph.start(shape)
            self.start_shape_timer()

    def reform(self, now, prefer_other=False):
        """New photo (or the 5-minute doodle rotation) while still: dissolve and form the next shape."""
        if not (self.shaped and self.idle.ambient) or self.viz:
            return
        if prefer_other and self.morph.kind == "photo" and self.doodles:
            self.last_animal = -ANIMAL_EVERY
        elif prefer_other:
            self.last_animal = now
        shape = self.pick_shape(now)
        self.shape_since = now
        if shape:
            self.still = shape
            self.morph.start(shape)
        else:
            self.shaped = False
            self.morph.release()
        self.start_shape_timer()

    def wake_shapes(self):
        self.shaped = False
        self.viz_stop = True              # the visualizer keeps moving while its dots glide home
        self.morph.release()
        if self.audio:
            self.stop_audio()
        self.dust_amt = max(self.dust_amt, 0.0)
        self.start_shape_timer()

    def start_viz(self):
        """Music while the dots hold the still shape: the sky-line starts to move, then the modes take over."""
        base = self.base_shape()
        if not base or len(base["pts"]) > shapes.VIZ_N:
            return
        start = self.still if self.morph.target is not None else None     # flow out of what is showing
        t0 = PERF.now() if PERF else 0.0
        self.viz = shapes.Viz(base, self.shell.sw, self.shell.sh, self.rng, doodles=self.doodles,
                              palette=self.palette, modes=self.viz_modes, every=self.viz_every, start=start)
        if PERF:
            PERF.mark("viz_new", ms=round(PERF.ms(t0), 3))
            self.perf_wrap_viz(self.viz)
        self.viz_stop = False
        if self.morph.target is None:
            self.morph.start(base)
        self.still = base
        self.start_shape_timer()

    def sync_shapes(self, now):
        """1 s housekeeping: start shapes after the fade, run the visualizer while music plays, ease back
        to the still sky-line when it stops, rotate doodles, and run audio capture."""
        listeners = [w for w in getattr(self, "items", ()) if hasattr(w, "on_music")]
        if self.idle.ambient and listeners:        # the weather line hides while music plays; any style
            playing = self.music_active()
            for w in listeners:
                w.on_music(playing)
        if self.style != "network":
            return
        if self.idle.ambient and self.active():
            self.begin_shapes(now)
        if self.idle.ambient and self.active() and self.shaped:
            music = self.viz_on and self.music_active()
            viz = self.viz
            if music and (viz is None or self.viz_stop) and self.morph.dir >= 0:
                if viz is None:
                    self.start_viz()
                self.viz_stop = False            # music came back while flowing home: carry on
            elif viz is not None and not music and not self.viz_stop:
                self.viz_stop = True             # flow back to the ridge; step_shape lands it on the still shape
                if self.still is None or self.still.get("kind") != "photo":
                    self.shaped = False          # no sky-line to rest on: dissolve now, the particle field takes over
                    self.morph.release()
                elif viz.mode != "ridge":
                    base = self.base_shape()
                    if base:
                        viz.set_base(base)
                    viz.switch("ridge", t=now, lv=self.lv)
                self.start_shape_timer()
            elif viz is None and not music and self.morph.dir >= 0 and self.morph.kind == "wave":
                self.shaped = False
                self.morph.release()
                self.start_shape_timer()
            elif (viz is None and self.shapes_mode == "photo+animals" and self.doodles
                  and not self.morph.moving and now - self.shape_since >= ANIMAL_EVERY):
                self.reform(now, prefer_other=True)
        listen = self.viz_on and self.idle.ambient and self.active() and self.chrome <= 0.0
        if listen and not self.audio and not self.fake_levels and now >= self.audio_retry_due:
            self.start_audio()
            if not self.audio:
                self.audio_retry_due = now + 30.0
        elif not listen and self.audio:
            self.stop_audio()
        if listen or self.audio:
            self.start_shape_timer()

    def start_audio(self):
        if audiolevel is None:
            return
        try:
            self.audio = audiolevel.AudioLevels(rate=11025, fps=15)
            self.audio.start()
        except Exception as exc:
            print("home audio:", exc)
            self.audio = None

    def stop_audio(self):
        if self.audio:
            try:
                self.audio.stop()
            except Exception:
                pass
        self.audio = None

    def fake(self, t):
        beat = max(0.0, 1.0 - ((t * 1.8) % 1.0) * 2.5)
        hit = max(0.0, 1.0 - ((t * 1.8) % 1.0) * 6.0)
        return {"bass": 0.5 + 0.5 * math.sin(t * 3.1), "mid": 0.5 + 0.4 * math.sin(t * 1.7),
                "treble": 0.5 + 0.5 * math.sin(t * 5.3), "beat": beat, "active": True,
                "onset": int(t * 1.8), "bass_hit": hit, "mid_hit": hit * 0.6,
                "treble_hit": hit if int(t * 1.8) % 2 else 0.0}

    def shape_needs_timer(self):
        lv = self.lv
        return (self.morph.moving or bool(self.audio) or self.fake_levels and self.idle.ambient
                or self.viz is not None or self.canvas.dim > 0.0 or max(lv.values()) > 0.01)

    def start_shape_timer(self):
        if self.shape_id or not self.active():
            return
        if (self.morph.target is not None or self.viz is not None
                or ((self.audio or self.fake_levels) and self.idle.ambient)):
            self.last_shape = time.monotonic()
            self.shape_period = SHAPE_MS
            self.shape_id = GLib.timeout_add(SHAPE_MS, self.shape_tick)

    def shape_tick(self):
        if PERF:
            t0 = PERF.now()
            self.perf_viz_ms = 0.0
            ok = self._shape_tick()
            PERF.tick("shape", t0, self.perf_phase(), viz=self.perf_viz_ms)
            return ok
        return self._shape_tick()

    def _shape_tick(self):
        now = time.monotonic()
        dt = min(0.2, now - self.last_shape)
        self.last_shape = now
        if not self.active():
            self.shape_id = 0
            return False
        self.step_shape(dt, now)
        if self.shape_period == SHAPE_MS:        # one master timer: the particles and the crossfade ride along
            if not self.field_id and self.style == "network" and (
                    self.field_target() > 0.0 or (self.field is not None and self.field.amt > 0.0)):
                self.step_field(dt, now)
            if self.idle.ambient and self.canvas.fading() and not self.anim_id:
                self.canvas.step(dt)
        if not self.shape_needs_timer():
            self.shape_id = 0
            self.shape_handoff()
            return False
        lively = (self.morph.moving or self.fake_levels and now >= self.fake_from or self.viz is not None
                  or self.canvas.dim > 0.0 or max(self.lv.values()) > 0.01)
        period = SHAPE_MS if lively else 500          # audio is on but silent: just keep an eye on the levels
        if period != self.shape_period:
            self.shape_period = period
            self.shape_id = GLib.timeout_add(period, self.shape_tick)
            self.shape_handoff()
            return False
        return True

    def shape_handoff(self):
        """The shape timer stopped or slowed down: the particles and a running crossfade get their own timers."""
        self.sync_field()
        self.start_anim()

    def detect_beat(self, tgt):
        """audiolevel counts detected beats ("onset"): a new count is a beat. Without it, a jump in "beat"."""
        raw = float(tgt.get("beat", 0.0))
        if "onset" in tgt:
            onset = tgt["onset"]
            beat = self.last_onset is not None and onset != self.last_onset
            self.last_onset = onset
        else:
            beat = raw > 0.6 and raw > self.beat_raw + 0.3
        self.beat_raw = raw
        return beat

    def step_shape(self, dt, now):
        """Advance the morph and the visualizer, cache this frame's geometry, repaint only what moved."""
        m = self.morph
        m.step(dt)
        if self.fake_levels and self.idle.ambient:
            tgt = self.fake(now) if now >= self.fake_from else {}
        elif self.audio:
            try:
                tgt = self.audio.levels()
            except Exception:
                tgt = {}
        else:
            tgt = {}
        beat = self.detect_beat(tgt)
        self.lv = shapes.ease_levels(self.lv, tgt, dt)
        dim = 1.0 if (self.viz is not None and not self.viz_stop and self.idle.ambient) else 0.0
        if dim > self.dim_t == 0.0 and self.widget_fading():
            dim = 0.0                          # let the weather line finish hiding first: one fade at a time
        if self.dim_t != dim:
            self.dim_t = fade_step(self.dim_t, dim, dt, DIM_FADE)
            level = dim_level(self.dim_t)
            if level != self.canvas.dim:       # the whole photo darkens: DIM_STEPS full repaints, not one a frame
                self.canvas.dim = level
                self.canvas.queue_draw()
        if m.target is None:
            self.viz = None
            self.viz_prev = None
            if self.shape_geom is not None:
                self.shape_geom = None
                self.invalidate_cells(self.shape_cells)
                self.shape_cells = set()
            return
        fr = None
        if self.viz is not None:
            t0 = PERF.now() if PERF else 0.0
            self.viz.step(dt, self.lv, beat=beat and not self.viz_stop, t=now, auto=not self.viz_stop)
            fr = self.viz.frame(now, self.lv)
            if PERF:
                self.perf_viz_ms = PERF.ms(t0)
            m.target = dict(self.still or m.target, pts=fr["pts"], links=[])
            if (self.viz_stop and self.viz.settled and self.viz.mode == "ridge" and self.idle.ambient
                    and max(self.lv.values()) < 0.02 and self.still and self.still.get("kind") == "photo"
                    and not m.moving):
                m.target = self.still = self.viz.base
                self.viz = None
                self.viz_stop = False
                self.viz_prev = None
                fr = None
            elif self.viz_stop and self.viz.settled and self.idle.ambient and not m.moving and not (
                    self.still and self.still.get("kind") == "photo"):
                self.shaped = False                    # no sky-line to rest on: back to drifting dust
                m.release()
        elif (not m.moving and self.shape_geom is not None and max(self.lv.values()) < 0.01
              and not (self.fake_levels and now >= self.fake_from)):
            return                                  # holding still and silent: nothing to repaint
        s = self.shell.scale
        pts = m.positions(self.origin, now)
        alphas = m.alphas(len(self.dust), self.dust_amt)
        if fr is None:
            if self.lv["bass"] > 0.01:
                pts = shapes.viz_points(pts, (m.cx, m.cy), self.lv["bass"])
            links = [(i, j, 1.0) for i, j in m.target["links"]]
        else:
            alphas = [a * b for a, b in zip(alphas, fr["alpha"])]
            links = fr["links"]
        tails = None
        if fr is not None and fr["trail"] > 0.02 and self.viz_prev and len(self.viz_prev) == len(pts):
            tails = [(x - 4 * (x - px), y - 4 * (y - py)) for (x, y), (px, py) in zip(pts, self.viz_prev)]
        self.viz_prev = pts if fr is not None else None
        if fr is not None:
            links, geom = self.viz_drawn(pts, alphas, links, fr, tails)
        else:
            geom = {"pts": pts, "n": 0, "links": [(i, j, math.hypot(pts[i][0] - pts[j][0], pts[i][1] - pts[j][1]))
                                                  for i, j, _ in links]}
        self.shape_geom = {"pts": pts, "links": links, "alphas": alphas, "t": now, "scale": s, "e": m.e(),
                           "centre": (m.cx, m.cy), "rmax": m.rmax, "lv": dict(self.lv), "viz": fr,
                           "tails": tails, "beat": beat}
        cells = network.geometry_cells(geom, margin=round(22 * s))
        dirty = cells | self.shape_cells
        self.shape_cells = cells
        self.invalidate_cells(dirty)

    def viz_drawn(self, pts, alphas, links, fr, tails):
        """Only what draw_viz will actually paint (same alpha cut-offs): the links to stroke, and the geometry
        whose cells are repainted. Faint links and hidden dots no longer widen the dirty region."""
        base_a = viz_base_alpha(self.lv)
        depth = fr["depth"]
        links = [(i, j, la) for i, j, la in links if viz_link_alpha(base_a, la, alphas, depth, i, j) >= 0.01]
        hyp = math.hypot
        geo = [(i, j, hyp(pts[i][0] - pts[j][0], pts[i][1] - pts[j][1])) for i, j, _ in links]
        geo += [(i, i, 0.0) for i in range(len(pts)) if alphas[i] >= 0.02]       # a dot: one cell block
        geo_pts = pts
        if tails:
            n, tr = len(pts), fr["trail"]
            geo_pts = pts + tails
            geo += [(i, n + i, hyp(pts[i][0] - tails[i][0], pts[i][1] - tails[i][1]))
                    for i in range(n) if alphas[i] * 0.15 * tr > 0.01]
        if fr["pulses"]:                          # the pulse heads carry a glow: repaint around them too
            k = len(geo_pts)
            geo_pts = list(geo_pts) + [(x1, y1) for _x0, _y0, x1, y1, _a in fr["pulses"]]
            geo += [(q, q, 0.0) for q in range(k, len(geo_pts))]
        return links, {"pts": geo_pts, "n": len(geo_pts), "links": geo}

    def invalidate_cells(self, cells):
        if not cells:
            return
        region = cairo.Region()
        c = network.CELL
        for gx, gy in cells:
            region.union(cairo.RectangleInt(gx * c, gy * c, c, c))
        self.canvas.queue_draw_region(region)

    def draw_shape(self, cr):
        g = self.shape_geom
        if not g:
            return
        if g["viz"] is not None:
            self.draw_viz(cr, g)
            return
        s, t, pts, lv, e = g["scale"], g["t"], g["pts"], g["lv"], g["e"]
        cx, cy = g["centre"]
        rmax = g["rmax"] * 1.05
        cr.save()
        cr.set_line_cap(cairo.LINE_CAP_ROUND)
        wd = max(1.3, 1.3 * s)
        segs = []
        for i, j, _a in g["links"]:
            a = 0.30 * e * e + 0.10 * lv["mid"] * e
            if lv["beat"] > 0.02:
                mx, my = (pts[i][0] + pts[j][0]) / 2, (pts[i][1] + pts[j][1]) / 2
                a += shapes.wave_boost(math.hypot(mx - cx, my - cy) / rmax, lv["beat"]) * e
            if a < 0.01:
                continue
            segs.append((WHITE, min(0.9, a), wd, *pts[i], *pts[j]))
        stroke_batches(cr, batch_segments(segs, 8, 0.9))
        for i, (x, y) in enumerate(pts):
            a = g["alphas"][i] * 0.85
            if a < 0.01:
                continue
            d = 3.2 * s
            cr.set_source_rgba(1, 1, 1, min(1.0, a))
            cr.move_to(x, y - d)
            cr.line_to(x + d, y)
            cr.line_to(x, y + d)
            cr.line_to(x - d, y)
            cr.close_path()
            cr.fill()
        cr.restore()

    def glow_sprite(self):
        """A soft round light, rendered once and stamped (as a mask, so any colour) where it is needed."""
        if self.glow is None:
            r = max(8, round(22 * self.shell.scale))
            surf = cairo.ImageSurface(cairo.FORMAT_A8, 2 * r, 2 * r)
            c = cairo.Context(surf)
            g = cairo.RadialGradient(r, r, 0, r, r, r)
            g.add_color_stop_rgba(0, 0, 0, 0, 1.0)
            g.add_color_stop_rgba(0.25, 0, 0, 0, 0.45)
            g.add_color_stop_rgba(1, 0, 0, 0, 0.0)
            c.set_source(g)
            c.paint()
            self.glow = (surf, r)
        return self.glow

    def draw_viz(self, cr, g):
        s, t, pts, lv, fr = g["scale"], g["t"], g["pts"], g["lv"], g["viz"]
        alphas, depth, glyph = g["alphas"], fr["depth"], fr.get("glyph")
        pal = self.viz.palette if self.viz else shapes.DEFAULT_PALETTE
        k = fr["colour"]
        line, spark, halo = pal[k % len(pal)], pal[(k + 1) % len(pal)], pal[(k + 2) % len(pal)]
        bass, mid, beat = lv["bass"], lv["mid"], lv["beat"]
        cr.save()
        cr.set_line_cap(cairo.LINE_CAP_ROUND)
        if g["tails"]:
            tr = fr["trail"]
            wd = max(1.0, 1.6 * s)
            segs = []
            for i, (x, y) in enumerate(pts):
                a = alphas[i] * 0.15 * tr
                if a > 0.01:
                    tx, ty = g["tails"][i]
                    segs.append((halo, a, wd, x, y, tx, ty))
            stroke_batches(cr, batch_segments(segs, 6, 0.15))
        gain = 0.55 + 0.45 * min(1.0, bass + mid)          # a background: music modulates, never dominates
        base_a = viz_base_alpha(lv)
        segs = []
        for i, j, la in g["links"]:
            dd = (depth[i] + depth[j]) / 2 if depth else 0.75
            a = viz_link_alpha(base_a, la, alphas, depth, i, j)
            if a < 0.01:
                continue
            segs.append((line, min(0.8, a), max(1.0, 1.1 * s * (0.6 + 0.6 * dd)), *pts[i], *pts[j]))
        stroke_batches(cr, batch_segments(segs, 8, 0.8))
        if fr["pulses"]:
            wd = max(1.6, 2.4 * s)
            segs = [(spark, 0.6 * a, wd, x0, y0, x1, y1) for x0, y0, x1, y1, a in fr["pulses"]]
            stroke_batches(cr, batch_segments(segs, 6, 0.6))
        sprite, r = self.glow_sprite()
        hub = self.cfg["node_shape"]
        outlines = []                   # the white glyph outlines, stroked in batches after the loop
        for i, (x, y) in enumerate(pts):
            a = alphas[i]
            if a < 0.02:
                continue
            dd = depth[i] if depth else 0.75
            tw = shapes.twinkle(i, t, lv["treble"]) if lv["treble"] > 0.02 else 0.0
            if i % 5 == 0 and dd > 0.45 and (beat > 0.05 or tw > 0.3):
                cr.set_source_rgba(*halo, min(0.5, (0.35 * beat + 0.25 * tw) * a * dd))
                cr.mask_surface(sprite, x - r, y - r)
            d = (2.0 + 2.2 * dd) * s * (1 + 0.18 * bass + 0.5 * tw)
            gl = glyph[i] if glyph else (4 if i % 5 == 0 else 0)
            if gl in (1, 2, 3):
                ang = fr["ang"][i]
                al = min(0.7, (0.30 + 0.35 * dd + 0.3 * tw) * a * gain)
                wd = max(1.0, 1.1 * s)
                if gl == 3:
                    dx, dy = math.cos(ang) * d * 2.4, math.sin(ang) * d * 2.4
                    outlines.append((WHITE, al, wd, x - dx, y - dy, x + dx, y + dy))
                else:
                    gr = d * (2.2 if gl == 1 else 1.9)     # not r: that is the glow sprite's radius
                    n = 3 if gl == 1 else 6
                    ring = [(x + gr * math.cos(ang + 6.2832 * q / n), y + gr * math.sin(ang + 6.2832 * q / n))
                            for q in range(n)]
                    cr.move_to(*ring[0])
                    for p in ring[1:]:
                        cr.line_to(*p)
                    cr.close_path()
                    cr.set_source_rgba(*line, 0.07 * a * gain)
                    cr.fill()
                    outlines.extend((WHITE, al, wd, *ring[q - 1], *ring[q]) for q in range(n))
            elif gl == 4:
                poly = network.poly_points(hub, d * 2.0, x, y)
                network.poly_path(cr, poly)
                cr.set_source_rgba(*line, min(0.22, 0.06 + 0.10 * mid + 0.06 * tw) * a)
                cr.fill_preserve()
                cr.set_source_rgba(1, 1, 1, min(0.6, (0.35 + 0.30 * dd) * a))
                cr.set_line_width(max(1.0, 1.3 * s))
                cr.stroke()
            else:
                cr.set_source_rgba(1, 1, 1, min(0.8, (0.30 + 0.40 * dd + 0.3 * tw) * a * gain))
                cr.move_to(x, y - d)
                cr.line_to(x + d, y)
                cr.line_to(x, y + d)
                cr.line_to(x - d, y)
                cr.close_path()
                cr.fill()
        stroke_batches(cr, batch_segments(outlines, 8, 0.7))
        for _x0, _y0, x1, y1, a in fr["pulses"]:
            cr.set_source_rgba(*spark, 0.45 * a)
            cr.mask_surface(sprite, x1 - r, y1 - r)
        cr.restore()

    def needs_anim(self):
        fading = self.canvas.fading() and not (self.idle.ambient and self.shape_drives())
        return ((not self.idle.ambient) or fading or self.chrome > 0.0
                or (self.style == "network" and self.dust_amt > 0.0 and self.morph.target is None
                    and not self.shape_candidate()))

    def start_anim(self):
        if not self.anim_id and self.active() and self.needs_anim():
            self.last_anim = time.monotonic()
            self.anim_id = GLib.timeout_add(ANIM_MS, self.anim_step)

    def stop_anim(self):
        if self.anim_id:
            GLib.source_remove(self.anim_id)
            self.anim_id = 0

    def anim_step(self):
        if PERF:
            t0 = PERF.now()
            ok = self._anim_step()
            PERF.tick("anim", t0, self.perf_phase())
            return ok
        return self._anim_step()

    def _anim_step(self):
        now = time.monotonic()
        dt = min(0.2, now - self.last_anim)
        self.last_anim = now
        if not self.active():
            self.anim_id = 0
            return False
        self.canvas.step(dt)
        target = 0.0 if self.idle.ambient else 1.0
        if self.chrome != target:
            self.chrome = fade_step(self.chrome, target, dt, CHROME_FADE)
            for w in self.chrome_widgets:
                w.set_opacity(self.chrome)
        if self.style == "network":
            hold = self.idle.ambient and self.shape_candidate()
            self.dust_amt = fade_step(self.dust_amt, 1.0 if hold else target, dt, 2.2)
        pw = next((w for w in self.items if hasattr(w, "drop_rect")), None)
        self.shell.avoid_rect = pw.drop_rect() if pw else None
        if self.chrome > 0.0:
            for w in self.items:
                w.animate(dt, now)
        if self.style == "network":
            self.update_links(now)
            if self.morph.target is not None and not self.shape_id:
                self.step_shape(dt, now)           # waking up: the dots are gliding back to their drift
            self.begin_shapes(now)
        if not self.needs_anim():
            self.anim_id = 0
            return False
        return True
