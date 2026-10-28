"""Round, softly drifting bubbles: pure layout math plus the cairo-drawn Bubble / BubbleRow widgets."""
import math
import random
import sys
from pathlib import Path

import gi
gi.require_version("Gtk", "3.0")
gi.require_version("Pango", "1.0")
gi.require_version("PangoCairo", "1.0")
from gi.repository import Gtk, Gdk, GLib, Pango, PangoCairo

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import screens  # noqa: E402

try:
    import cairo
except Exception:  # pragma: no cover
    cairo = None

PAD = 1.62           # widget side = diameter * PAD: room for swell, glow and lean
EXTENT = 1.35        # vertical half-extent of a bubble incl. swell/ring/drift, in radii
SWELL = 0.16         # focused bubble grows by this fraction
ENTER_KEYS = ("Return", "KP_Enter", "space")


# ---------------------------------------------------------------- pure math

def cluster_layout(n, width, height, seed=1, base=1.0, spread=0.18, max_r=None, width_frac=0.92):
    """Loose, slightly irregular cluster of n circles in a width x height strip, left to right.
    Returns [{"cx","cy","r"}]. Radii vary, vertical offsets wander, and gaps open into small clusters.
    Circles never overlap and centres are strictly increasing in x (D-pad order == visual order)."""
    if n <= 0:
        return []
    rng = random.Random(seed * 7919 + n)
    r0 = min(height * 0.5 / (1 + spread * 2), width * width_frac / (n * 2.8), max_r or 1e9) * base
    radii = [r0 * rng.uniform(0.78, 1.18) for _ in range(n)]
    gaps = []
    for i in range(n - 1):
        g = rng.uniform(0.6, 1.1) * r0
        if (i + 1) % 3 == 0 and rng.random() < 0.8:      # a breath between clusters
            g += rng.uniform(0.9, 1.6) * r0
        gaps.append(g)
    total = sum(2 * r for r in radii) + sum(gaps)
    if total > width * width_frac:                       # squeeze to fit
        k = width * width_frac / total
        radii = [r * k for r in radii]
        gaps = [g * k for g in gaps]
        total *= k
    x = (width - total) / 2
    out = []
    sign = rng.choice((-1, 1))
    for i, r in enumerate(radii):
        x += r
        wander = rng.uniform(0.35, 1.0) * sign * height * spread
        room = max(0.0, height / 2 - r * EXTENT)   # keep swell, ring and drift inside the row
        cy = height / 2 + max(-room, min(room, wander))
        out.append({"cx": x, "cy": cy, "r": r})
        x += r + (gaps[i] if i < n - 1 else 0)
        sign = -sign
    return out


def lean_offsets(n, focused, strength):
    """Horizontal push (px) for each of n bubbles in a row when `focused` swells: neighbours lean away."""
    if focused is None or not 0 <= focused < n:
        return [0.0] * n
    out = []
    for i in range(n):
        d = i - focused
        out.append(0.0 if d == 0 else math.copysign(strength / (1 + abs(d) * 0.9), d))
    return out


def ease(cur, target, dt, rate=9.0):
    """Frame-rate independent exponential approach."""
    return cur + (target - cur) * (1 - math.exp(-rate * max(0.0, dt)))


def in_circle(x, y, cx, cy, r):
    return (x - cx) ** 2 + (y - cy) ** 2 <= r * r


# ---------------------------------------------------------------- drawing helpers

def _icon(cr, kind, cx, cy, s):
    """Tiny line icons for action bubbles; s ~ half-size."""
    cr.save()
    cr.set_line_width(max(2.0, s * 0.11))
    cr.set_line_cap(cairo.LINE_CAP_ROUND)
    cr.set_line_join(cairo.LINE_JOIN_ROUND)
    cr.set_source_rgba(1, 1, 1, 0.92)
    if kind == "search":
        cr.arc(cx - s * 0.1, cy - s * 0.1, s * 0.5, 0, 2 * math.pi)
        cr.stroke()
        cr.move_to(cx + s * 0.27, cy + s * 0.27)
        cr.line_to(cx + s * 0.52, cy + s * 0.52)
        cr.stroke()
    elif kind == "settings":     # a gear: ring plus eight teeth
        cr.arc(cx, cy, s * 0.24, 0, 2 * math.pi)
        cr.stroke()
        for i in range(8):
            a = i * math.pi / 4
            cr.move_to(cx + math.cos(a) * s * 0.42, cy + math.sin(a) * s * 0.42)
            cr.line_to(cx + math.cos(a) * s * 0.62, cy + math.sin(a) * s * 0.62)
            cr.stroke()
        cr.arc(cx, cy, s * 0.43, 0, 2 * math.pi)
        cr.stroke()
    elif kind == "discord":      # a chat bubble with two eyes
        w, h, r = s * 1.3, s * 1.0, s * 0.34
        x0, y0 = cx - w / 2, cy - h / 2 - s * 0.06
        cr.new_sub_path()
        for ax, ay, a in ((x0 + w - r, y0 + r, -90), (x0 + w - r, y0 + h - r, 0),
                          (x0 + r, y0 + h - r, 90), (x0 + r, y0 + r, 180)):
            cr.arc(ax, ay, r, math.radians(a), math.radians(a + 90))
        cr.close_path()
        cr.set_source_rgba(0.40, 0.45, 0.96, 0.95)
        cr.fill()
        cr.move_to(cx - s * 0.3, y0 + h - s * 0.02)
        cr.line_to(cx - s * 0.5, y0 + h + s * 0.3)
        cr.line_to(cx - s * 0.05, y0 + h - s * 0.02)
        cr.close_path()
        cr.fill()
        cr.set_source_rgba(1, 1, 1, 1)
        for ex in (-0.26, 0.26):
            cr.arc(cx + s * ex, cy - s * 0.04, s * 0.12, 0, 2 * math.pi)
            cr.fill()
    elif kind == "twitch":       # the Twitch chat glyph: a purple speech block with two bars
        w, h, r = s * 1.2, s * 1.1, s * 0.16
        x0, y0 = cx - w / 2, cy - h / 2 - s * 0.05
        cr.new_sub_path()
        for ax, ay, a in ((x0 + w - r, y0 + r, -90), (x0 + w - r, y0 + h - r, 0),
                          (x0 + r, y0 + h - r, 90), (x0 + r, y0 + r, 180)):
            cr.arc(ax, ay, r, math.radians(a), math.radians(a + 90))
        cr.close_path()
        cr.set_source_rgba(0.57, 0.27, 1.0, 0.95)
        cr.fill()
        cr.move_to(cx - s * 0.5, y0 + h - s * 0.02)
        cr.line_to(cx - s * 0.5, y0 + h + s * 0.3)
        cr.line_to(cx - s * 0.15, y0 + h - s * 0.02)
        cr.close_path()
        cr.fill()
        cr.set_source_rgba(1, 1, 1, 1)
        cr.set_line_width(s * 0.14)
        for ex in (-0.2, 0.2):
            cr.move_to(cx + s * ex, cy - s * 0.3)
            cr.line_to(cx + s * ex, cy + s * 0.08)
            cr.stroke()
    elif kind == "series":
        for i, dy in enumerate((-0.42, 0.0, 0.42)):
            cr.set_source_rgba(1, 1, 1, 0.92 - i * 0.25)
            cr.move_to(cx - s * 0.6, cy + s * dy)
            cr.line_to(cx + s * 0.6, cy + s * dy)
            cr.stroke()
    else:  # movies / youtube: rounded screen with a play triangle
        w, h, r = s * 1.3, s * 0.92, s * 0.22
        x0, y0 = cx - w / 2, cy - h / 2
        cr.new_sub_path()
        for ax, ay, a in ((x0 + w - r, y0 + r, -90), (x0 + w - r, y0 + h - r, 0),
                          (x0 + r, y0 + h - r, 90), (x0 + r, y0 + r, 180)):
            cr.arc(ax, ay, r, math.radians(a), math.radians(a + 90))
        cr.close_path()
        if kind == "youtube":
            cr.set_source_rgba(0.93, 0.18, 0.2, 0.95)
            cr.fill()
            cr.set_source_rgba(1, 1, 1, 1)
        else:
            cr.stroke()
        t = s * 0.24
        cr.move_to(cx - t * 0.6, cy - t)
        cr.line_to(cx + t * 1.0, cy)
        cr.line_to(cx - t * 0.6, cy + t)
        cr.close_path()
        cr.fill()
    cr.restore()


TINTS = {"movies": (0.36, 0.52, 0.86), "series": (0.62, 0.42, 0.84),
         "youtube": (0.82, 0.30, 0.32), "discord": (0.36, 0.40, 0.90), "twitch": (0.57, 0.27, 1.0), "search": (0.28, 0.66, 0.62),
         "settings": (0.50, 0.56, 0.66)}


def _fit_cover(cr, pb, d):
    """Paint pixbuf `pb` scaled to cover a d x d square at the origin."""
    k = max(d / pb.get_width(), d / pb.get_height())
    cr.save()
    cr.translate((d - pb.get_width() * k) / 2, (d - pb.get_height() * k) / 2)
    cr.scale(k, k)
    Gdk.cairo_set_source_pixbuf(cr, pb, 0, 0)
    cr.paint()
    cr.restore()


class Bubble(Gtk.DrawingArea):
    """One focusable round bubble: poster (with optional progress ring) or glass action disc."""

    def __init__(self, shell, r, title="", sub="", kind=None, poster_url=None, progress=None,
                 on_activate=None, seed=0, eq=None):
        super().__init__()
        self.now = eq
        self.shell, self.r = shell, r
        self.title, self.sub, self.kind = title, sub, kind
        self.progress = progress
        self.on_activate = on_activate or (lambda: None)
        self.rng = random.Random(seed * 31 + 5)
        self.phase = [self.rng.uniform(0, 6.28) for _ in range(4)]
        self.speed = [self.rng.uniform(0.28, 0.55) for _ in range(3)]
        self.focus_amt = 0.0
        self.lean = 0.0
        self.lean_target = 0.0
        self.poster = None
        self.body = None
        self.t = 0.0
        self.label_h = round(shell.px(54)) if (poster_url is not None or sub or (title and not kind)) else 0
        self.side = round(2 * r * PAD)
        self.set_size_request(self.side, self.side + self.label_h)
        self.set_has_window(False)   # square widgets overlap; the row hit-tests circles instead
        self.set_can_focus(True)
        self.connect("draw", self.on_draw)
        self.connect("focus-in-event", lambda *_: self.queue_draw() or False)
        self.connect("focus-out-event", lambda *_: self.queue_draw() or False)
        self.connect("key-press-event", self.on_key)
        self.layouts = {}
        if poster_url:
            d = round(2 * r * (1 + SWELL))
            load_any(poster_url, d, d, self.set_poster)

    # ---- state
    def set_poster(self, pb):
        if pb is not None:
            self.poster, self.body = pb, None
            self.queue_draw()
        return False

    def centre(self):
        return self.side / 2, self.side / 2

    def half_extent(self):
        return self.r * 1.25, self.r * 1.25

    def update_now(self, state):
        self.now = state.get("status")
        self.title = state.get("title") or self.title
        self.sub = state.get("artist") or ""

    def tick(self, dt, now):
        self.t = now
        want = 1.0 if self.has_focus() else 0.0
        self.focus_amt = ease(self.focus_amt, want, dt)
        self.lean = ease(self.lean, self.lean_target, dt, 7.0)
        self.queue_draw()

    # ---- input
    def inside(self, x, y):
        cx, cy = self.centre()
        return in_circle(x, y, cx + self.lean, cy, self.r * (1 + SWELL * max(self.focus_amt, 0.5)))

    def on_key(self, _w, ev):
        if Gdk.keyval_name(ev.keyval) in ENTER_KEYS:
            self.on_activate()
            return True
        return False

    # ---- drawing
    def text(self, cr, text, px, weight, x, y, width, rgba, align=Pango.Alignment.CENTER):
        key = (text, px, weight, width)
        lay = self.layouts.get(key)
        ctx = self.get_pango_context()
        if lay is None:
            lay = Pango.Layout.new(ctx)
            fd = Pango.FontDescription("Inter, Noto Sans, Sans")
            fd.set_absolute_size(px * Pango.SCALE)
            fd.set_weight(weight)
            lay.set_font_description(fd)
            lay.set_width(int(width * Pango.SCALE))
            lay.set_ellipsize(Pango.EllipsizeMode.END)
            lay.set_alignment(align)
            lay.set_text(text, -1)
            if len(self.layouts) > 8:
                self.layouts.clear()
            self.layouts[key] = lay
        cr.set_source_rgba(0, 0, 0, 0.45 * rgba[3])
        for dx, dy in ((-2, 0), (2, 0), (0, -2), (0, 3), (2, 3), (-2, 3)):   # soft halo
            cr.move_to(x + dx, y + dy)
            PangoCairo.show_layout(cr, lay)
        cr.set_source_rgba(*rgba)
        cr.move_to(x, y)
        PangoCairo.show_layout(cr, lay)
        return lay.get_pixel_size()[1]

    def render_body(self):
        """Pre-render the bubble face at its largest (focused) size so frames only blit+scale it."""
        R = self.r * (1 + SWELL)
        d = math.ceil(2 * R)
        surf = cairo.ImageSurface(cairo.FORMAT_ARGB32, d, d)
        cr = cairo.Context(surf)
        cr.arc(d / 2, d / 2, R, 0, 2 * math.pi)
        cr.clip()
        if self.poster is not None:
            _fit_cover(cr, self.poster, d)
            sh = cairo.LinearGradient(0, d * 0.55, 0, d)
            sh.add_color_stop_rgba(0, 0, 0, 0, 0)
            sh.add_color_stop_rgba(1, 0, 0, 0, 0.35)
            cr.set_source(sh)
            cr.paint()
        else:
            tr, tg, tb = TINTS.get(self.kind, (0.5, 0.55, 0.65))
            g = cairo.LinearGradient(0, 0, d, d)
            g.add_color_stop_rgba(0, tr, tg, tb, 0.62)
            g.add_color_stop_rgba(1, tr * 0.45, tg * 0.45, tb * 0.55, 0.78)
            cr.set_source(g)
            cr.paint()
            hi = cairo.RadialGradient(d * 0.35, d * 0.28, 0, d * 0.35, d * 0.28, d * 0.7)
            hi.add_color_stop_rgba(0, 1, 1, 1, 0.28)
            hi.add_color_stop_rgba(1, 1, 1, 1, 0)
            cr.set_source(hi)
            cr.paint()
            if self.kind:
                _icon(cr, self.kind, d / 2, d / 2 - d * 0.1, d * 0.2)
                cr.set_source_rgba(1, 1, 1, 0.95)
                lay = Pango.Layout.new(self.get_pango_context())
                fd = Pango.FontDescription("Inter, Noto Sans, Sans")
                fd.set_absolute_size(round(d * 0.135) * Pango.SCALE)
                fd.set_weight(Pango.Weight.SEMIBOLD)
                lay.set_font_description(fd)
                lay.set_width(int(d * 0.9 * Pango.SCALE))
                lay.set_alignment(Pango.Alignment.CENTER)
                lay.set_text(self.title, -1)
                cr.move_to(d * 0.05, d * 0.64)
                PangoCairo.show_layout(cr, lay)
        cr.reset_clip()
        cr.new_path()
        cr.arc(d / 2, d / 2, R - 0.75, 0, 2 * math.pi)
        cr.set_source_rgba(1, 1, 1, 0.22)
        cr.set_line_width(1.5)
        cr.stroke()
        return surf

    def on_draw(self, _w, cr):
        if cairo is None:
            return False
        if self.body is None:
            self.body = self.render_body()
        effects = getattr(self.shell, "home_cfg", {}).get("visual_effects", True)
        f = self.focus_amt if effects else float(self.has_focus())
        s = self.shell.scale
        t = self.t
        cx = self.side / 2 + self.lean + math.sin(t * self.speed[0] + self.phase[0]) * 5 * s
        cy = self.side / 2 + math.sin(t * self.speed[1] + self.phase[1]) * 6 * s
        breath = 1 + 0.018 * math.sin(t * self.speed[2] + self.phase[2])
        rad = self.r * (1 + SWELL * f) * breath
        # soft shadow / glow
        if f > 0.02:
            g = cairo.RadialGradient(cx, cy, rad * 0.9, cx, cy, rad * 1.38)
            g.add_color_stop_rgba(0, 1, 1, 1, 0.34 * f)
            g.add_color_stop_rgba(1, 1, 1, 1, 0)
            cr.set_source(g)
            cr.arc(cx, cy, rad * 1.38, 0, 2 * math.pi)
            cr.fill()
        else:
            g = cairo.RadialGradient(cx, cy + rad * 0.1, rad * 0.95, cx, cy + rad * 0.1, rad * 1.25)
            g.add_color_stop_rgba(0, 0, 0, 0, 0.30)
            g.add_color_stop_rgba(1, 0, 0, 0, 0)
            cr.set_source(g)
            cr.arc(cx, cy + rad * 0.1, rad * 1.25, 0, 2 * math.pi)
            cr.fill()
        # face
        k = rad / (self.r * (1 + SWELL))
        bd = self.body.get_width()
        cr.save()
        cr.translate(cx - bd * k / 2, cy - bd * k / 2)
        cr.scale(k, k)
        cr.set_source_surface(self.body, 0, 0)
        cr.get_source().set_filter(cairo.FILTER_BILINEAR)
        cr.paint_with_alpha(0.93 + 0.07 * f if getattr(self.shell, "home_cfg", {}).get("transparency", True) else 1.0)
        cr.restore()
        # progress ring
        if self.progress:
            ring = rad + 7 * s
            cr.set_line_width(4 * s)
            cr.set_source_rgba(1, 1, 1, 0.18 + 0.35 * f)
            cr.arc(cx, cy, ring, 0, 2 * math.pi)
            cr.stroke()
            cr.set_line_cap(cairo.LINE_CAP_ROUND)
            cr.set_source_rgba(1, 0.86, 0.55, 0.95)
            cr.arc(cx, cy, ring, -math.pi / 2, -math.pi / 2 + 2 * math.pi * min(1.0, self.progress))
            cr.stroke()
        elif f > 0.02:
            cr.set_line_width(3 * s)
            cr.set_source_rgba(1, 1, 1, 0.85 * f)
            cr.arc(cx, cy, rad + 5 * s, 0, 2 * math.pi)
            cr.stroke()
        if self.now:
            draw_eq(cr, cx + rad * 0.52, cy - rad * 0.52, 16 * s, t, self.now == "Playing")
        # label under poster bubbles
        if self.label_h and self.title:
            ty = self.side - 4 * s + 2
            w = 2 * self.r * 1.1
            a = 0.62 + 0.38 * f
            h = self.text(cr, self.title, round(21 * s), Pango.Weight.SEMIBOLD, (self.side - w) / 2, ty, w,
                          (1, 1, 1, a))
            if self.sub:
                self.text(cr, self.sub, round(16 * s), Pango.Weight.NORMAL, (self.side - w) / 2, ty + h + 1, w,
                          (1, 1, 1, 0.5 + 0.3 * f))
        return False


def load_any(url, w, h, cb):
    """Like screens.load_art, but also takes file:// URLs (MPRIS art can be a local file)."""
    if str(url).startswith("file://"):
        import threading
        from urllib.parse import unquote

        def job():
            try:
                pb = screens.cover_pixbuf(unquote(url[7:]), w, h)
            except Exception:
                pb = None
            GLib.idle_add(cb, pb)
        threading.Thread(target=job, daemon=True).start()
    else:
        screens.load_art(url, w, h, cb)


def draw_eq(cr, x, y, h, t, playing, alpha=0.95):
    """Three little "now playing" bars with their baseline at y; frozen when paused."""
    cr.save()
    cr.set_source_rgba(1, 1, 1, alpha)
    w = h * 0.22
    for i, (spd, ph, rest) in enumerate(((4.1, 0.0, 0.55), (5.3, 1.7, 0.9), (3.4, 3.1, 0.4))):
        k = 0.35 + 0.65 * abs(math.sin(t * spd + ph)) if playing else rest
        bh = h * k
        cr.rectangle(x + i * w * 1.6, y - bh, w, bh)
        cr.fill()
    cr.restore()


class RowBase(Gtk.EventBox):
    """A full-width strip of focusable nodes (bubbles or network nodes), laid out by the subclass.
    Pointer: hovering a node focuses it, clicking activates it (hit-tested here, not per widget, because the
    square widgets overlap)."""
    lean_strength = 20

    def __init__(self, shell, height):
        super().__init__()
        self.set_visible_window(False)
        self.add_events(Gdk.EventMask.POINTER_MOTION_MASK | Gdk.EventMask.BUTTON_PRESS_MASK)
        self.shell = shell
        self.bubbles = []
        self.home, self.shift, self.avoid = {}, {}, None
        self.fixed = Gtk.Fixed()
        self.add(self.fixed)
        self.set_size_request(shell.sw, height)
        self.connect("motion-notify-event", self.on_motion)
        self.connect("button-press-event", self.on_press)

    def hit(self, x, y):
        best = None
        for b in self.bubbles:
            pos = b.translate_coordinates(self, 0, 0)
            if pos and b.inside(x - pos[0], y - pos[1]):
                best = b
        return best

    def on_motion(self, _w, ev):
        b = self.hit(ev.x, ev.y)
        if b and not b.has_focus():
            b.grab_focus()
            screen = self.shell.current()
            if hasattr(screen, "auto_focus"):
                screen.auto_focus = False
        return False

    def on_press(self, _w, ev):
        b = self.hit(ev.x, ev.y) if ev.button == 1 else None
        if b:
            b.grab_focus()
            b.on_activate()
            return True
        return False

    def focus_items(self):
        return list(self.bubbles)

    def add_node(self, node, x, y):
        """Place a node in the row; remembers its home position so it can glide aside and back."""
        self.fixed.put(node, x, y)
        self.home[node] = (x, y)
        self.shift[node] = [0.0, 0.0]
        self.bubbles.append(node)

    def apply_avoid(self, dt):
        """Keep nodes out of self.avoid (a window-coordinate rect, or None): they glide left or down, and back."""
        origin = self.translate_coordinates(self.shell, 0, 0) or (0, 0)
        m = self.shell.px(28)
        for b in self.bubbles:
            hx, hy = self.home[b]
            cx, cy = b.centre()
            hw, hh = b.half_extent()
            tx = ty = 0.0
            if self.avoid:
                ax, ay, aw, ah = self.avoid
                l, r = origin[0] + hx + cx - hw, origin[0] + hx + cx + hw
                t, bt = origin[1] + hy + cy - hh, origin[1] + hy + cy + hh
                if r > ax - m and l < ax + aw + m and bt > ay - m and t < ay + ah + m:
                    left = r - (ax - m)
                    down = (ay + ah + m) - t
                    if left <= down * 1.4:
                        tx = -left
                    else:
                        ty = down
            s = self.shift[b]
            nx, ny = ease(s[0], tx, dt, 5.0), ease(s[1], ty, dt, 5.0)
            if abs(nx - s[0]) > 0.4 or abs(ny - s[1]) > 0.4 or (not self.avoid and (abs(nx) < 0.5) != (abs(s[0]) < 0.5)):
                self.fixed.move(b, round(hx + nx), round(hy + ny))
                s[0], s[1] = nx, ny

    def animate(self, dt, now):
        self.apply_avoid(dt)
        idx = next((i for i, b in enumerate(self.bubbles) if b.has_focus()), None)
        for b, off in zip(self.bubbles, lean_offsets(len(self.bubbles), idx, self.lean_strength * self.shell.scale)):
            b.lean_target = off
            b.tick(dt, now)

    def update_now(self, state):
        for b in self.bubbles:
            if getattr(b, "now", None):
                b.update_now(state)


class BubbleRow(RowBase):
    """A strip of bubbles laid out as a loose cluster; animates focus swell + neighbour lean + drift."""

    def __init__(self, shell, specs, height, seed=1, base=1.0, spread=0.18, max_r=None, width_frac=0.8):
        super().__init__(shell, height)
        label = shell.px(54) if any(sp.get("poster_url") is not None for sp in specs) else 0
        lay = cluster_layout(len(specs), shell.sw, height - label, seed=seed, base=base, spread=spread,
                             max_r=max_r, width_frac=width_frac)
        for i, (spec, p) in enumerate(zip(specs, lay)):
            spec = dict(spec)
            r = p["r"] * spec.pop("big", 1.0)
            b = Bubble(shell, r, seed=seed * 13 + i, **spec)
            self.add_node(b, round(p["cx"] - b.side / 2), round(p["cy"] - b.side / 2))
