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

import screens
from screens import Screen, text_label, cover_pixbuf
import widgets
from widgets import network

try:
    import cairo
except Exception:  # pragma: no cover
    cairo = None
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

        overlay = Gtk.Overlay()
        self.pack_start(overlay, True, True, 0)
        self.canvas = PhotoCanvas()
        overlay.add(self.canvas)
        shell.photo_canvas = self.canvas
        if self.style == "network":
            seed = datetime.date.today().toordinal()
            self.dust = [(x, y, seed * 100 + i) for i, (x, y) in
                         enumerate(network.dust_points(shell.sw, shell.sh, seed))]
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
        item, (surf, blur) = self.pending
        self.pending = None
        self.current_item = item
        cap, credit = photo_label(item)
        self.cap.set_text(cap)
        self.credit.set_text(credit)
        self.canvas.show_surface(surf, blur, animate=self.canvas.cur is not None)
        self.photo_due = time.monotonic() + self.interval
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
        self.geom = network.compute_geometry(self.shell, nodes, self.dust, now)
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
        network.draw_network(cr, self.geom, self.chrome, self.dust_amt)

    def needs_anim(self):
        return ((not self.idle.ambient) or self.canvas.fading() or self.chrome > 0.0
                or (self.style == "network" and self.dust_amt > 0.0))

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
            self.dust_amt = fade_step(self.dust_amt, target, dt, 2.2)
        if self.chrome > 0.0:
            for w in self.items:
                w.animate(dt, now)
        if self.style == "network":
            self.update_links(now)
        if not self.needs_anim():
            self.anim_id = 0
            return False
        return True
