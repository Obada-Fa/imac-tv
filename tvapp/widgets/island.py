"""The TV's island: glass joined to the top edge of the screen, for the volume and for notices.

Over Home it is frosted from Home's photo. Over another app it is drawn by that app where it can be: YouTube's and
the TV browser's page frost their own live picture under it, mpv lays it over its video; elsewhere it is solid glass
in its own small window (see `IslandOverApps`). Islands are idle
until they show: no timer runs while they are tucked away, and over Home nothing redraws while they hold still."""

import math
import subprocess
import time

import cairo
import gi

gi.require_version("Gtk", "3.0")
gi.require_version("GdkX11", "3.0")
from gi.repository import Gdk, GdkX11, GLib, Gtk, Pango, PangoCairo  # noqa: E402

import overlays  # noqa: E402
from widgets import network  # noqa: E402

SHOULDER, CORNER = 16, 28
FRAME_MS = 33           # 30 fps, only while an island moves, in a small part of the screen


def edge_outline(cr, left, right, height, shoulder, corner):
    """Flat along the screen edge, flaring into it at the shoulders, rounded below. The path stays open along the
    edge, so a stroke only follows the part that is on screen; a fill closes it along the edge."""
    s, r = min(shoulder, height / 2), min(corner, height / 2, (right - left) / 2)
    cr.new_path()
    cr.arc_negative(right + s, s, s, -math.pi / 2, -math.pi)
    cr.arc(right - r, height - r, r, 0, math.pi / 2)
    cr.arc(left + r, height - r, r, math.pi / 2, math.pi)
    cr.arc_negative(left - s, s, s, 0, -math.pi / 2)


def paint_glass(cr, base, widget, photo):
    """Fill the outline just traced with the island's glass, frosted from `photo` or tinted without one, and draw
    its rim. Uses up the path."""
    cr.save()
    cr.clip_preserve()
    network.paint_frost(cr, base, widget, photo)
    if getattr(photo, "transparency", True):
        cr.set_source_rgba(0.015, 0.025, 0.05, 0.34)
        cr.paint()
    cr.set_source_rgba(1, 1, 1, 0.07)
    cr.paint()
    cr.restore()
    cr.set_source_rgba(1, 1, 1, 0.22)
    cr.set_line_width(1)
    cr.stroke()


class IslandWindow(Gtk.Window):
    """The island's own window, over apps that can't draw it themselves (anything but YouTube, the TV browser and
    mpv), or when they don't answer: a small window on the top screen edge that takes no focus or clicks.

    GNOME on X11 paints a separate window's see-through part black over VacuumTube, whatever its alpha and shape,
    so the island here is solid glass and the window paints the app's picture around it, read once when it
    appears (and when it changes size): those few pixels at the shoulders are all that come from a copy."""

    def __init__(self, shell):
        super().__init__(type=Gtk.WindowType.POPUP)
        self.shell = shell
        self.frost = overlays.SolidFrost()
        self.placed, self.at = None, (0, 0)
        self.source, self.picture, self.frame = None, None, None
        self.set_app_paintable(True)
        self.set_accept_focus(False)
        self.set_type_hint(Gdk.WindowTypeHint.NOTIFICATION)
        visual = self.get_screen().get_rgba_visual()
        if visual:
            self.set_visual(visual)     # where the compositor does blend, a failed read still shows the app
        self.connect("draw", self.paint)
        self.connect("realize", lambda *_: self.get_window().input_shape_combine_region(cairo.Region(), 0, 0))

    def show_frame(self, surface, _outline=None):
        size = (surface.get_width(), surface.get_height())
        if not self.get_visible() or size != self.placed:
            self.place(size)
            self.grab()
        self.frame = surface
        if not self.get_visible():
            self.show()
        self.get_window().raise_()
        self.queue_draw()
        return True

    def hide_frame(self):
        self.hide()
        self.source = self.picture = self.frame = None

    def place(self, size):
        """Size the window for the island and centre it on the top edge of the TV's monitor."""
        width, height = self.placed = size
        x, y, full = self.placement()
        self.at = (x + (full - width) // 2, y)
        self.resize(width, height)
        self.move(*self.at)

    def placement(self):
        """(x, y, width) of the monitor the TV app is on."""
        display = Gdk.Display.get_default()
        window = self.shell.get_window()
        monitor = (display.get_monitor_at_window(window) if window else None) or display.get_primary_monitor() or display.get_monitor(0)
        area = monitor.get_geometry()
        return area.x, area.y, area.width

    def paint(self, _w, cr):
        cr.set_operator(cairo.OPERATOR_SOURCE)
        if self.picture is not None:
            cr.set_source_surface(self.picture, 0, 0)
        else:
            cr.set_source_rgba(0, 0, 0, 0)
        cr.paint()
        cr.set_operator(cairo.OPERATOR_OVER)
        if self.frame is not None:
            cr.set_source_surface(self.frame, 0, 0)
            cr.paint()
        return False

    def candidates(self):
        """Windows from the top of the stack down, the app in front first."""
        try:
            out = subprocess.run(["xprop", "-root", "-notype", "_NET_CLIENT_LIST_STACKING"], capture_output=True,
                                 text=True, timeout=1).stdout
            ids = [int(x, 16) for x in out.split("#", 1)[1].replace(",", " ").split()]
        except (OSError, subprocess.SubprocessError, IndexError, ValueError):
            return []
        display = Gdk.Display.get_default()
        return [w for w in (GdkX11.X11Window.foreign_new_for_display(display, xid) for xid in reversed(ids)) if w]

    def grab(self):
        """Copy the picture under this window from the topmost window that covers it; False if there is none."""
        if not self.placed:
            return False
        (x, y), (w, h) = self.at, self.placed
        display = Gdk.Display.get_default()
        for source in self.candidates():
            if source == self.get_window():
                continue
            display.error_trap_push()
            _ok, sx, sy = source.get_origin()
            covers = sx <= x and sy <= y and sx + source.get_width() >= x + w and sy + source.get_height() >= y + h
            pixbuf = Gdk.pixbuf_get_from_window(source, x - sx, y - sy, w, h) if covers else None
            if display.error_trap_pop() or pixbuf is None:
                continue
            self.source = source
            self.picture = Gdk.cairo_surface_create_from_pixbuf(pixbuf, 1, None)
            return True
        self.source = self.picture = None
        return False


class IslandOverApps:
    """An island over YouTube, movies and other apps, when Home's window is behind them. The island isn't in any
    window: each frame it moves is drawn into a small picture and handed to what the app in front can show,
    chosen when the island appears: YouTube's or the TV browser's own page (frosted live by the page), mpv's
    video (solid), or else the island's own window (solid)."""

    def __init__(self, shell, island):
        self.shell, self.island = shell, island
        self.on_hidden = None
        self.target = None
        self.targets = {}               # made the first time each is needed, then kept with their connections
        island.on_frame = self.island_moved
        island.on_hidden = self.island_hidden

    def reveal(self, start):
        """Set the island going with `start()`; it appears with its first frame."""
        start()

    def dismiss(self):
        self.island.dismiss()

    def kind(self):
        app = getattr(self.shell, "prev_app", None)
        if app == "youtube":
            return "youtube"
        if app == "player":
            return "mpv"
        if getattr(self.shell, "tv_browser", False):
            return "browser"
        return "window"

    def make(self, kind):
        if kind == "youtube":
            return overlays.youtube_target()
        if kind == "browser":
            return overlays.browser_target()
        if kind == "mpv":
            return overlays.MpvTarget()
        return WindowTarget(IslandWindow(self.shell))

    def choose(self):
        """The target for the app in front; the island's own window when that app doesn't answer."""
        for kind in (self.kind(), "window"):
            target = self.targets.get(kind) or self.targets.setdefault(kind, self.make(kind))
            if target.connect():
                return target
        return self.targets["window"]

    def island_moved(self):
        if self.target is None:
            self.target = self.choose()
        self.island.frost = self.target.frost
        surface, outline = self.render()
        if surface is None:
            return
        if not self.target.show(surface, outline) and not isinstance(self.target, WindowTarget):
            self.target = self.targets.get("window") or self.targets.setdefault("window", self.make("window"))
            self.island.frost = self.target.frost
            surface, outline = self.render()
            self.target.show(surface, outline)

    def render(self):
        """The island's frame now, and its outline as SVG path data, both in device pixels."""
        width, height = (max(1, int(v)) for v in self.island.room())
        surface = cairo.ImageSurface(cairo.FORMAT_ARGB32, width, height)
        self.island.render_width = width
        try:
            cr = cairo.Context(surface)
            if self.island.trace(cr) is None:
                return None, None
            cr.identity_matrix()
            outline = overlays.svg_path(cr.copy_path())
            cr = cairo.Context(surface)
            gray = cairo.FontOptions()
            gray.set_antialias(cairo.ANTIALIAS_GRAY)    # coloured subpixel edges would show on a see-through frame
            cr.set_font_options(gray)
            self.island.on_draw(self.island, cr)
        finally:
            self.island.render_width = 0
        surface.flush()
        return surface, outline

    def island_hidden(self):
        if self.target is not None:
            self.target.hide()
            self.target = None
        if self.on_hidden:
            self.on_hidden()

    def destroy(self):
        for target in self.targets.values():
            target.close()
        self.targets.clear()


class WindowTarget:
    """The island's own window as a target, like the page and mpv ones."""

    def __init__(self, window):
        self.window = window
        self.frost = window.frost

    def connect(self, timeout=0.4):
        return True

    def show(self, surface, outline=None):
        return self.window.show_frame(surface, outline)

    def hide(self):
        self.window.hide_frame()

    def close(self):
        self.window.destroy()


# ---------------------------------------------------------------- notices

TINTS = {"info": (0.55, 0.75, 1.0), "play": (0.55, 0.75, 1.0), "phone": (0.55, 0.75, 1.0),
         "error": (1.0, 0.6, 0.32), "done": (0.42, 0.88, 0.62), "update": (0.42, 0.88, 0.62)}
CLOSE_S = 0.38


def notice_kind(text):
    """A notice's icon, from its words, for the many callers that only pass text."""
    low = text.lower()
    if low.startswith(("could not", "couldn't", "can't", "cannot", "failed", "unable", "error")) or "not reach" in low:
        return "error"
    if low.startswith("phone"):
        return "phone"
    if low.startswith(("opening", "starting")):
        return "play"
    if "update" in low and "available" in low:
        return "update"
    if low.startswith(("added", "removed")) or low.endswith("saved"):
        return "done"
    return "info"


def spring(t, damp, freq):
    """How far a released spring still is from rest: 1 at the start, briefly below 0 (the overshoot), then 0."""
    return math.exp(-damp * t) * math.cos(freq * t)


def smooth(e):
    e = min(1.0, max(0.0, e))
    return e * e * (3 - 2 * e)


def glyph(cr, kind, x, y, r):
    """The white mark inside a notice's badge."""
    if kind == "error":
        cr.move_to(x, y - r * 0.48)
        cr.line_to(x, y + r * 0.1)
        cr.stroke()
        cr.arc(x, y + r * 0.44, r * 0.1, 0, 2 * math.pi)
        cr.fill()
    elif kind == "done":
        cr.move_to(x - r * 0.42, y + r * 0.02)
        cr.line_to(x - r * 0.12, y + r * 0.32)
        cr.line_to(x + r * 0.44, y - r * 0.3)
        cr.stroke()
    elif kind == "play":
        cr.move_to(x - r * 0.26, y - r * 0.42)
        cr.line_to(x + r * 0.46, y)
        cr.line_to(x - r * 0.26, y + r * 0.42)
        cr.close_path()
        cr.fill()
    elif kind == "phone":
        w, h, c = r * 0.72, r * 1.16, r * 0.16
        cr.new_sub_path()
        cr.arc(x + w / 2 - c, y - h / 2 + c, c, -math.pi / 2, 0)
        cr.arc(x + w / 2 - c, y + h / 2 - c, c, 0, math.pi / 2)
        cr.arc(x - w / 2 + c, y + h / 2 - c, c, math.pi / 2, math.pi)
        cr.arc(x - w / 2 + c, y - h / 2 + c, c, math.pi, 1.5 * math.pi)
        cr.close_path()
        cr.stroke()
        cr.arc(x, y + h / 2 - r * 0.24, r * 0.07, 0, 2 * math.pi)
        cr.fill()
    elif kind == "update":
        cr.move_to(x, y - r * 0.48)
        cr.line_to(x, y + r * 0.16)
        cr.move_to(x - r * 0.3, y - r * 0.12)
        cr.line_to(x, y + r * 0.18)
        cr.line_to(x + r * 0.3, y - r * 0.12)
        cr.move_to(x - r * 0.42, y + r * 0.46)
        cr.line_to(x + r * 0.42, y + r * 0.46)
        cr.stroke()
    else:
        cr.arc(x, y - r * 0.42, r * 0.1, 0, 2 * math.pi)
        cr.fill()
        cr.move_to(x, y - r * 0.1)
        cr.line_to(x, y + r * 0.46)
        cr.stroke()


def badge(cr, kind, x, y, r, alpha):
    """A notice's icon: a softly tinted disc with a white mark."""
    if alpha <= 0.01 or r < 1:
        return
    tint = TINTS.get(kind, TINTS["info"])
    cr.save()
    cr.new_path()
    cr.arc(x, y, r, 0, 2 * math.pi)
    cr.set_source_rgba(*tint, 0.28 * alpha)
    cr.fill_preserve()
    cr.set_source_rgba(*tint, 0.6 * alpha)
    cr.set_line_width(max(1.0, r * 0.07))
    cr.stroke()
    cr.set_source_rgba(1, 1, 1, alpha)
    cr.set_line_width(max(1.5, r * 0.14))
    cr.set_line_cap(cairo.LINE_CAP_ROUND)
    cr.set_line_join(cairo.LINE_JOIN_ROUND)
    glyph(cr, kind, x, y, r)
    cr.restore()


class NoticeIsland(Gtk.DrawingArea):
    """A notice on the top screen edge. It drops out of the edge as a small bead holding its icon, unfurls from the
    centre to fit its words while the icon slides to its place and the words fade in, catches one sweep of light,
    holds, then folds back into the bead and tucks away. A new notice while one shows reshapes the island in place.
    The volume island has the edge first: a notice waits for it, or steps aside and comes back after."""

    HEIGHT, TALL, BEAD, MAX_TEXT = 62, 90, 62, 760
    PAD, BADGE, GAP, END = 15, 16, 14, 30

    def __init__(self, shell, frosted=True):
        super().__init__()
        self.shell, self.px, self.frosted = shell, shell.px, frosted
        self.set_has_window(False)
        self.set_halign(Gtk.Align.CENTER)
        self.set_valign(Gtk.Align.START)
        self.set_can_focus(False)
        self.set_no_show_all(True)
        self.set_size_request(1, 1)
        self.connect("draw", self.on_draw)
        self.connect("destroy", lambda *_: self.stop())
        self.on_frame = self.on_hidden = self.frost = None
        self.phase = "closed"               # closed, open, morph (a new notice in place), close
        self.started = 0.0
        self.timer = self.hold_timer = 0
        self.until = 0.0
        self.held, self.pending = False, None
        self.text, self.kind, self.layout = "", "info", None
        self.full = (1.0, 1.0)              # the island fully open for this notice, in px
        self.old = None                     # (layout, kind) of the notice being replaced
        self.w = self.h = 0.0               # the island now, in px
        self.w0 = self.h0 = self.a0 = 0.0   # where the current move started
        self.text_alpha = self.old_alpha = 0.0
        self.shine = -1.0
        self.room_size = (1, 1)
        self.render_width = 0               # set while it is drawn off screen, for an app's own overlay

    def span(self):
        return self.render_width or self.get_allocated_width()

    # ---- what callers use
    def say(self, text, secs=4, kind=None):
        kind = kind or notice_kind(text)
        if self.held:
            self.pending = (text, secs, kind)
            return
        if self.phase in ("open", "morph") and text == self.text:
            self.keep(secs)                 # the same notice again: just keep it up longer
            return
        visible = self.phase != "closed"
        self.old = (self.layout, self.kind) if visible and self.text_alpha > 0.05 else None
        self.text, self.kind = text, kind
        self.layout = self.make_layout(text)
        self.full = self.measure()
        self.begin("morph" if visible else "open")
        self.room_size = self.room_for(max(self.full[0], self.w), max(self.full[1], self.h))
        self.set_size_request(*self.room_size)
        self.show()
        self.keep(secs)
        self.run()

    def close(self):
        """Tuck the notice away now, with its animation."""
        self.cancel_hold()
        if self.phase in ("open", "morph"):
            self.begin("close")
            self.run()

    def dismiss(self):
        """Gone at once, without the animation, and not coming back after the volume island."""
        self.pending = None
        self.vanish()

    def vanish(self):
        self.stop()
        self.cancel_hold()
        was = self.phase != "closed"
        self.phase, self.w, self.h, self.old = "closed", 0.0, 0.0, None
        self.hide()
        if was and self.on_hidden:
            self.on_hidden()

    def hold(self):
        """The volume island takes the edge: step aside, and come back after with the time that was left."""
        self.held = True
        if self.phase in ("open", "morph"):
            left = self.until - time.monotonic()
            if left > 0.8:
                self.pending = (self.text, left, self.kind)
            self.close()

    def release(self):
        self.held = False
        if self.pending:
            text, secs, kind = self.pending
            self.pending = None
            self.say(text, max(2.0, secs), kind)

    def room(self):
        return self.room_size

    # ---- layout
    def make_layout(self, text):
        layout = PangoCairo.create_layout(cairo.Context(cairo.ImageSurface(cairo.FORMAT_A8, 1, 1)))
        layout.set_font_description(Pango.FontDescription("Inter Semi-Bold %d" % self.px(16)))
        layout.set_width(self.px(self.MAX_TEXT) * Pango.SCALE)
        layout.set_wrap(Pango.WrapMode.WORD_CHAR)
        layout.set_height(-2)               # two lines at most, then an ellipsis
        layout.set_ellipsize(Pango.EllipsizeMode.END)
        layout.set_text(text, -1)
        return layout

    def measure(self):
        """(width, height) of the fully open island for the current words, in px."""
        px = self.px
        tw, th = self.layout.get_pixel_size()
        height = px(self.HEIGHT) if self.layout.get_line_count() < 2 else max(px(self.TALL), th + px(30))
        return float(px(self.PAD) + 2 * px(self.BADGE) + px(self.GAP) + tw + px(self.END)), float(height)

    def room_for(self, width, height):
        """Space for the island with its overshoot and shoulders; px changes when the TV's resolution does."""
        return round(width * 1.08) + 2 * self.px(SHOULDER) + 4, round(height * 1.08) + 2

    # ---- timing
    def keep(self, secs):
        self.cancel_hold()
        self.until = time.monotonic() + secs
        self.hold_timer = GLib.timeout_add(int(secs * 1000), self.hold_done)

    def hold_done(self):
        self.hold_timer = 0
        self.close()
        return False

    def cancel_hold(self):
        if self.hold_timer:
            GLib.source_remove(self.hold_timer)
            self.hold_timer = 0

    def begin(self, phase):
        self.phase, self.started = phase, time.monotonic()
        self.w0, self.h0, self.a0 = self.w, self.h, self.text_alpha
        self.old_alpha = self.text_alpha if phase == "morph" else 0.0
        self.shine = -1.0

    def run(self):
        if not self.timer:
            self.timer = GLib.timeout_add(FRAME_MS, self.animate)
        self.frame()

    def stop(self):
        if self.timer:
            GLib.source_remove(self.timer)
            self.timer = 0

    def frame(self):
        self.queue_draw()
        if self.on_frame:
            self.on_frame()

    def animate(self):
        moving = self.step(time.monotonic() - self.started)
        if not moving and self.phase == "close":
            self.timer = 0
            self.vanish()
            return False
        self.frame()
        if moving:
            return True
        self.timer = 0
        return False

    def step(self, t):
        """Move the island to where it is `t` seconds into its current phase; False once it is at rest."""
        W, H = self.full
        bead = self.px(self.BEAD)
        if self.phase == "open":
            drop = 1.0 if t >= 0.45 else 1 - spring(t, 14, 13)                 # the bead drops out of the edge
            u = 0.0 if t < 0.14 else 1.0 if t >= 0.75 else 1 - spring(t - 0.14, 11, 11)  # then unfurls
            self.w, self.h = bead * drop + (W - bead) * u, H * drop
            self.text_alpha = smooth((u - 0.4) / 0.5)
            return self.sweep(t, 0.62)
        if self.phase == "morph":
            k = 0.0 if t >= 0.6 else spring(t, 11, 11)
            self.w, self.h = W + (self.w0 - W) * k, H + (self.h0 - H) * k
            self.old_alpha = self.a0 * (1 - smooth(t / 0.1))
            self.text_alpha = smooth((t - 0.1) / 0.25)
            if self.old_alpha <= 0:
                self.old = None
            return self.sweep(t, 0.4)
        if self.phase == "close":
            self.text_alpha = self.a0 * (1 - smooth(t / 0.1))
            folded = min(self.w0, bead * self.h0 / H)                           # back into the bead
            w = self.w0 + (folded - self.w0) * smooth((t - 0.04) / 0.22)
            e = min(1.0, max(0.0, (t - 0.22) / 0.16))                           # then into the edge, quickening
            self.w, self.h = w * (1 - e * e), self.h0 * (1 - e * e)
            return t < CLOSE_S
        return False

    def sweep(self, t, start):
        """One sweep of light across the glass once the island has settled; True while anything still moves."""
        self.shine = (t - start) / 0.6 if start <= t < start + 0.6 else -1.0
        return t < start + 0.6

    # ---- drawing
    def frost_source(self):
        """What the glass frosts: its window's picture of the app behind, or Home's photo."""
        return self.frost if self.frost is not None else getattr(self.shell, "photo_canvas", None) if self.frosted else None

    def trace(self, cr):
        """Trace the island's outline; (left, width, height), or None while it is tucked away."""
        if self.h < 0.5 or self.w < 0.5:
            return None
        left = self.span() / 2 - self.w / 2
        grown = min(1.0, self.h / self.px(self.HEIGHT))     # the shoulders grow in with the bead
        edge_outline(cr, left, left + self.w, self.h, self.px(SHOULDER) * grown, self.px(CORNER))
        return left, self.w, self.h

    def on_draw(self, _w, cr):
        base = cr.get_matrix()
        place = self.trace(cr)
        if not place:
            return False
        left, width, height = place
        paint_glass(cr, base, self, self.frost_source())
        self.trace(cr)
        cr.clip()
        if self.shine >= 0:
            band = max(self.px(70), width * 0.22)
            x = left - band + (width + 2 * band) * smooth(self.shine)
            light = cairo.LinearGradient(x - band, 0, x + band, height)    # a slanted band
            light.add_color_stop_rgba(0, 1, 1, 1, 0)
            light.add_color_stop_rgba(0.5, 1, 1, 1, 0.13)
            light.add_color_stop_rgba(1, 1, 1, 1, 0)
            cr.set_source(light)
            cr.paint()
        self.paint_content(cr, width, height)
        return False

    def paint_content(self, cr, width, height):
        """The icon rides the island's left end, from the bead's centre out to its place, and the words follow it,
        fading in as the unfurling glass uncovers them."""
        px = self.px
        H = self.full[1]
        y = height / 2
        x = self.span() / 2 - max(0.0, width / 2 - px(self.PAD) - px(self.BADGE))
        r = px(self.BADGE) * min(1.0, height / H)
        words = x + px(self.BADGE) + px(self.GAP)
        if self.old and self.old_alpha > 0.01:
            layout, kind = self.old
            badge(cr, kind, x, y, r, self.old_alpha)
            self.paint_text(cr, layout, words, y, self.old_alpha)
        badge(cr, self.kind, x, y, r, self.text_alpha if self.phase == "morph" else 1.0)
        if self.text_alpha > 0.01:
            rise = (1 - self.text_alpha) * px(6) if self.phase != "close" else 0
            self.paint_text(cr, self.layout, words, y + rise, self.text_alpha)

    @staticmethod
    def paint_text(cr, layout, x, y, alpha):
        """The words, vertically centred on y, with a faint shadow."""
        PangoCairo.update_layout(cr, layout)
        top = y - layout.get_pixel_size()[1] / 2
        cr.move_to(x + 1, top + 1)
        cr.set_source_rgba(0, 0, 0, 0.45 * alpha)
        PangoCairo.show_layout(cr, layout)
        cr.move_to(x, top)
        cr.set_source_rgba(1, 1, 1, alpha)
        PangoCairo.show_layout(cr, layout)
