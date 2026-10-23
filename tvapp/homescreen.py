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
import math
import hashlib
import json
import os
import random

import screens
from screens import Screen, text_label, cover_pixbuf
import widgets
from widgets import network, shapes

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

ANIM_MS = 50            # 20 fps ceiling
CHROME_FADE = 1.0       # seconds, bubbles+widgets fading out/in
PHOTO_FADE = 1.5        # seconds, crossfade
REFRESH_PHOTOS = 6 * 3600
NOW_EVERY = 3.0          # seconds between YouTube now-playing polls (only while Home is visible)
SHAPE_MS = 66            # ~15 fps while a shape is morphing or the visualizer is running
ANIMAL_EVERY = 300.0     # seconds between doodles (shapes: photo+animals)
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
.ah-card {{ background-color: rgba(18,22,32,0.50); border: 1px solid rgba(255,255,255,0.20); border-radius: {p(30)}px; padding: {p(10)}px {p(14)}px {p(10)}px {p(14)}px; }}
.ah-chip {{ border-radius: {p(40)}px; padding: {p(2)}px {p(8)}px; background-color: rgba(18,22,32,0.38); }}
.ah-cardbtn {{ background: transparent; border: {p(2)}px solid transparent; border-radius: {p(22)}px; padding: {p(8)}px {p(12)}px; box-shadow: none; }}
.ah-cardbtn:hover {{ background: rgba(255,255,255,0.07); }}
.ah-cardbtn:focus {{ background: rgba(255,255,255,0.14); border-color: rgba(255,255,255,0.85); }}
.ah-kicker {{ font-size: {p(20)}px; color: rgba(255,255,255,0.75); font-weight: 500; }}
.ah-code {{ font-size: {p(44)}px; font-weight: 300; letter-spacing: {p(4)}px; color: #fff; }}
.ah-net {{ font-size: {p(18)}px; color: rgba(255,255,255,0.62); }}
.ah-power {{ background: transparent; border: {p(2)}px solid transparent; border-radius: {p(40)}px; color: rgba(255,255,255,0.8); font-size: {p(26)}px; padding: {p(6)}px {p(14)}px; box-shadow: none; }}
.ah-power:hover {{ background: rgba(255,255,255,0.08); }}
.ah-power:focus {{ background: rgba(255,255,255,0.14); border-color: rgba(255,255,255,0.85); color: #fff; }}
.ah-qr-frame {{ background: #fff; border-radius: {p(14)}px; padding: {p(6)}px; }}
.ah-cap {{ font-size: {p(22)}px; color: rgba(255,255,255,0.72); text-shadow: 0 1px 3px rgba(0,0,0,0.8); }}
.ah-credit {{ font-size: {p(17)}px; color: rgba(255,255,255,0.55); text-shadow: 0 1px 3px rgba(0,0,0,0.8); }}
.ah-dialog {{ background-image: linear-gradient(160deg, #1b2236, #0d1019); border: 1px solid rgba(255,255,255,0.22); border-radius: {p(34)}px; color: #fff; }}
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


def traced(path, pb):
    """Horizon trace for a photo, cached next to the app data (DATA/shapecache) and keyed by file name + size."""
    try:
        st = os.stat(path)
        key = hashlib.sha1(f"v2:{os.path.basename(path)}:{st.st_size}:{int(st.st_mtime)}".encode()).hexdigest()[:20]
        cache = screens.DATA / "shapecache"
        f = cache / (key + ".json")
        try:
            return json.loads(f.read_text())["trace"]
        except (OSError, ValueError, KeyError):
            pass
        trace = shapes.trace_horizon(*gray_rows(pb))
        cache.mkdir(parents=True, exist_ok=True)
        f.write_text(json.dumps({"trace": trace}))
        old = sorted(cache.glob("*.json"), key=lambda p: p.stat().st_mtime)[:-80]
        for p in old:
            p.unlink(missing_ok=True)
        return trace
    except Exception as exc:
        print("home trace:", exc)
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
    return surf, small, traced(path, pb)


class PhotoCanvas(Gtk.DrawingArea):
    """Full-screen photo with a crossfade; draws a calm dark gradient when there is no photo."""

    def __init__(self):
        super().__init__()
        self.cur = self.prev = None
        self.blur_cur = self.blur_prev = None
        self.blur_k = BLUR_K
        self.extra = None       # hook(cr): the network lines are drawn here, in the same pass as the photo
        self.fade = 1.0
        self.dim = 0.0
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
        if self.cur is None:
            g = cairo.LinearGradient(0, 0, w, h)
            g.add_color_stop_rgb(0, 0.05, 0.07, 0.12)
            g.add_color_stop_rgb(0.55, 0.07, 0.09, 0.15)
            g.add_color_stop_rgb(1, 0.02, 0.03, 0.06)
            cr.set_source(g)
            cr.paint()
            return False
        if self.prev is not None and self.fade < 1.0:
            cr.set_source_surface(self.prev, 0, 0)
            cr.paint()
            e = self.fade * self.fade * (3 - 2 * self.fade)
            cr.set_source_surface(self.cur, 0, 0)
            cr.paint_with_alpha(e)
        else:
            cr.set_operator(cairo.OPERATOR_SOURCE)
            cr.set_source_surface(self.cur, 0, 0)
            cr.paint()
            cr.set_operator(cairo.OPERATOR_OVER)
        if self.extra:
            self.extra(cr)
        return False


# ---------------------------------------------------------------- the screen

class AmbientHomeScreen(Screen):
    sname = "home"
    header = False

    def __init__(self, shell):
        super().__init__(shell)
        px = shell.px
        self.cfg = widgets.load_config(screens.DATA)
        amb = self.cfg["ambient"]
        self.interval = amb["interval"]
        self.idle = IdleTracker(amb["idle"], time.monotonic())
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
        self.trace = None
        self.doodles = (shapes.load_doodles(screens.ASSETS / "shapes.json")
                        if self.shapes_mode == "photo+animals" else [])
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
        self.fake_levels = bool(os.environ.get("IMACTV_FAKE_LEVELS"))   # test/screenshot hook only
        self.rng = random.Random()
        self.extra_origin = []

        overlay = Gtk.Overlay()
        self.pack_start(overlay, True, True, 0)
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
        overlay.add_overlay(right)
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

        GLib.timeout_add(1000, self.supervise)
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
            for w in self.items:
                if w.keep_in_ambient:
                    w.on_ambient(False)
            self.wake_shapes()
            self.start_anim()
        return woke

    def enter_ambient(self):
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
        item, (surf, blur, trace) = self.pending
        self.pending = None
        self.current_item = item
        self.trace = trace
        cap, credit = photo_label(item)
        self.cap.set_text(cap)
        self.credit.set_text(credit)
        self.canvas.show_surface(surf, blur, animate=self.canvas.cur is not None)
        self.photo_due = time.monotonic() + self.interval
        if self.shaped and self.morph.kind == "photo":
            self.reform(time.monotonic())
        self.start_anim()
        self.preload()

    # ---- timers
    def active(self):
        """Home is what the user is looking at: mapped, not minimised, no other app in front."""
        if not self.get_mapped() or self.shell.prev_app not in (None, "home"):
            return False
        win = self.shell.get_window()
        return not (win and win.get_state() & Gdk.WindowState.ICONIFIED)

    def supervise(self):
        now = time.monotonic()
        if not self.active():
            self.stop_anim()
            return True
        if self.idle.check(now):
            self.enter_ambient()
        for w in self.items:
            if w.interval and now >= self.due.get(w.id, 0):
                self.due[w.id] = now + w.interval
                try:
                    w.refresh()
                except Exception as exc:
                    print("widget", w.id, "refresh:", exc)
        if nowplaying and self.cont and now >= self.now_due and not self.now_busy:
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
        self.start_anim()
        return True

    def poll_now(self):
        self.now_busy = True

        def work():
            try:
                state = nowplaying.current()
            except Exception:
                state = None
            GLib.idle_add(self.set_now, state)
        threading.Thread(target=work, daemon=True).start()

    def set_now(self, state):
        self.now_busy = False
        key = lambda s: (s["status"], s["title"], s["artist"]) if s else None
        if key(state) != key(self.now_state):
            self.now_state = state
            if self.cont:
                self.cont.set_now(state, auto_focus=self.auto_focus)
        return False

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
        network.draw_network(cr, self.geom, self.chrome, 0.0 if owned else self.dust_amt)
        self.draw_shape(cr)

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
            return True
        st = self.now_state
        if st and st.get("status") == "Playing":
            return True
        return bool(self.audio and self.audio.levels().get("active"))

    def shape_candidate(self):
        return self.shapes_mode != "off" and (self.trace is not None) or (self.viz_on and self.music_active())

    def pick_shape(self, now):
        sw, sh = self.shell.sw, self.shell.sh
        if self.shapes_mode == "photo+animals" and self.doodles and now - self.last_animal >= ANIMAL_EVERY:
            self.last_animal = now
            return shapes.place_doodle(self.rng.choice(self.doodles), sw, sh, self.rng)
        if self.shapes_mode != "off" and self.trace:
            return {"pts": [(x * sw, y * sh) for x, y in self.trace["pts"]], "links": self.trace["links"],
                    "kind": "photo"}
        if self.viz_on and self.music_active():
            sp = shapes.wave_shape()
            sp["pts"] = [(x * sw, y * sh) for x, y in sp["pts"]]
            return sp
        return None

    def begin_shapes(self, now):
        """Called once the tiles/labels have faded: dust glides onto the chosen shape."""
        if self.style != "network" or self.shaped or not self.idle.ambient or self.chrome > 0.0:
            return
        shape = self.pick_shape(now)
        if shape:
            self.shaped = True
            self.shape_since = now
            self.morph.start(shape)
            self.start_shape_timer()

    def reform(self, now, prefer_other=False):
        """New photo (or the 5-minute rotation) while ambient: dissolve and form the next shape."""
        if not (self.shaped and self.idle.ambient):
            return
        if prefer_other and self.morph.kind == "photo" and self.doodles:
            self.last_animal = -ANIMAL_EVERY
        elif prefer_other:
            self.last_animal = now
        shape = self.pick_shape(now)
        self.shape_since = now
        if shape:
            self.morph.start(shape)
        else:
            self.shaped = False
            self.morph.release()
        self.start_shape_timer()

    def wake_shapes(self):
        self.shaped = False
        self.morph.release()
        if self.audio:
            self.stop_audio()
        self.dust_amt = max(self.dust_amt, 0.0)
        self.start_shape_timer()

    def sync_shapes(self, now):
        """1 s housekeeping: start shapes after the fade, rotate, drop the wave when music stops, run audio."""
        if self.style != "network":
            return
        if self.idle.ambient and self.active():
            self.begin_shapes(now)
            if self.shaped:
                if self.morph.kind == "wave" and not self.music_active() and self.morph.dir >= 0:
                    self.shaped = False
                    self.morph.release()
                    self.start_shape_timer()
                elif (self.shapes_mode == "photo+animals" and self.doodles and not self.morph.moving
                      and now - self.shape_since >= ANIMAL_EVERY):
                    self.reform(now, prefer_other=True)
        want = self.viz_on and self.idle.ambient and self.active() and self.music_active() and self.chrome <= 0.0
        if want and not self.audio and not self.fake_levels:
            self.start_audio()
        elif not want and self.audio:
            self.stop_audio()
        if want or self.audio:
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
        try:
            self.audio.stop()
        except Exception:
            pass
        self.audio = None

    def fake(self, t):
        beat = max(0.0, 1.0 - ((t * 1.8) % 1.0) * 2.5)
        return {"bass": 0.5 + 0.5 * math.sin(t * 3.1), "mid": 0.5 + 0.4 * math.sin(t * 1.7),
                "treble": 0.5 + 0.5 * math.sin(t * 5.3), "beat": beat, "active": True}

    def shape_needs_timer(self):
        lv = self.lv
        return (self.morph.moving or bool(self.audio) or self.fake_levels and self.idle.ambient
                or max(lv.values()) > 0.01)

    def start_shape_timer(self):
        if self.shape_id or not self.active():
            return
        if self.morph.target is not None or ((self.audio or self.fake_levels) and self.idle.ambient):
            self.last_shape = time.monotonic()
            self.shape_period = SHAPE_MS
            self.shape_id = GLib.timeout_add(SHAPE_MS, self.shape_tick)

    def shape_tick(self):
        now = time.monotonic()
        dt = min(0.2, now - self.last_shape)
        self.last_shape = now
        if not self.active():
            self.shape_id = 0
            return False
        self.step_shape(dt, now)
        if not self.shape_needs_timer():
            self.shape_id = 0
            return False
        lively = self.morph.moving or self.fake_levels or max(self.lv.values()) > 0.01
        period = SHAPE_MS if lively else 250          # audio is on but silent: just keep an eye on the levels
        if period != self.shape_period:
            self.shape_period = period
            self.shape_id = GLib.timeout_add(period, self.shape_tick)
            return False
        return True

    def step_shape(self, dt, now):
        """Advance the morph and the visualizer, cache this frame's geometry, repaint only what moved."""
        m = self.morph
        m.step(dt)
        if self.fake_levels and self.idle.ambient:
            tgt = self.fake(now)
        elif self.audio:
            try:
                tgt = self.audio.levels()
            except Exception:
                tgt = {}
        else:
            tgt = {}
        self.lv = shapes.ease_levels(self.lv, tgt, dt)
        if m.target is None:
            if self.shape_geom is not None:
                self.shape_geom = None
                self.invalidate_cells(self.shape_cells)
                self.shape_cells = set()
            return
        if (not m.moving and self.shape_geom is not None and max(self.lv.values()) < 0.01
                and not self.fake_levels):
            return                                  # holding still and silent: nothing to repaint
        s = self.shell.scale
        pts = m.positions(self.origin, now)
        alphas = m.alphas(len(self.dust), self.dust_amt)
        if max(self.lv["bass"], 0.0) > 0.01:
            pts = shapes.viz_points(pts, (m.cx, m.cy), self.lv["bass"])
        links = [(i, j, math.hypot(pts[i][0] - pts[j][0], pts[i][1] - pts[j][1])) for i, j in m.target["links"]]
        self.shape_geom = {"pts": pts, "links": links, "alphas": alphas, "t": now, "scale": s, "e": m.e(),
                           "centre": (m.cx, m.cy), "rmax": m.rmax, "lv": dict(self.lv)}
        cells = network.geometry_cells({"pts": pts, "n": 0, "links": links}, margin=12)
        dirty = cells | self.shape_cells
        self.shape_cells = cells
        self.invalidate_cells(dirty)

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
        s, t, pts, lv, e = g["scale"], g["t"], g["pts"], g["lv"], g["e"]
        cx, cy = g["centre"]
        rmax = g["rmax"] * 1.05
        cr.save()
        cr.set_line_cap(cairo.LINE_CAP_ROUND)
        cr.set_line_width(max(1.3, 1.3 * s))
        for i, j, d in g["links"]:
            a = 0.30 * e * e + 0.10 * lv["mid"] * e
            if lv["beat"] > 0.02:
                mx, my = (pts[i][0] + pts[j][0]) / 2, (pts[i][1] + pts[j][1]) / 2
                a += shapes.wave_boost(math.hypot(mx - cx, my - cy) / rmax, lv["beat"]) * e
            if a < 0.01:
                continue
            cr.set_source_rgba(1, 1, 1, min(0.9, a))
            cr.move_to(*pts[i])
            cr.line_to(*pts[j])
            cr.stroke()
        for i, (x, y) in enumerate(pts):
            tw = shapes.twinkle(i, t, lv["treble"]) if lv["treble"] > 0.02 else 0.0
            a = g["alphas"][i] * (0.5 + 0.4 * tw)
            if a < 0.01:
                continue
            d = 3.2 * s * (1 + 0.7 * tw)
            cr.set_source_rgba(1, 1, 1, min(1.0, a))
            cr.move_to(x, y - d)
            cr.line_to(x + d, y)
            cr.line_to(x, y + d)
            cr.line_to(x - d, y)
            cr.close_path()
            cr.fill()
        cr.restore()

    def needs_anim(self):
        return ((not self.idle.ambient) or self.canvas.fading() or self.chrome > 0.0
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
