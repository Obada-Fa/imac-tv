"""The TV's volume indicators: a small pill over Home and Settings, and a hairline under the ambient clock.

Both are idle until the volume changes: no timer runs and nothing is redrawn while they are hidden."""

import math
import time

import cairo
import gi

gi.require_version("Gtk", "3.0")
from gi.repository import GLib, Gtk, Pango, PangoCairo  # noqa: E402

from widgets import network  # noqa: E402

HOLD_MS = 1600
FADE_MS = 66            # 15 fps, the TV's animation budget
FADE_STEP = 0.25
ISLAND_MS = 33          # the island's spring needs 30 fps, for half a second, in a small part of the screen
OPEN_S, CLOSE_S = 0.5, 0.24


def filled(percent, muted):
    """Bar fill 0..1; a muted bar is empty whatever the level behind it."""
    return 0.0 if muted else max(0, min(100, percent)) / 100


def island_width(level):
    """The island's width as a share of its full width: 20% when silent, 100% at full volume."""
    return 0.2 + 0.8 * level


def opening(start, t):
    """The island's size while it opens: a spring from `start` that overshoots a little and settles on 1."""
    if t >= OPEN_S:
        return 1.0
    return 1 - (1 - start) * math.exp(-11 * t) * math.cos(13 * t)


def closing(start, t):
    """The island's size while it tucks away: slow at first, then quickly into the edge."""
    e = min(1.0, t / CLOSE_S)
    return start * (1 - e * e * (3 - 2 * e))


class VolumeIndicator(Gtk.DrawingArea):
    """Shows a level, holds it, then fades out. Subclasses paint it."""

    def __init__(self, width, height):
        super().__init__()
        self.percent, self.muted = 0, False
        self.level_timer = self.fade_timer = 0
        self.set_has_window(False)
        self.set_size_request(width, height)
        self.set_no_show_all(True)
        self.connect("draw", self.on_draw)
        self.connect("destroy", lambda *_: self.stop_timers())

    def show_level(self, percent, muted=False):
        self.percent, self.muted = percent, bool(muted)
        self.stop_timers()
        self.set_opacity(1.0)
        self.show()
        self.queue_draw()
        self.level_timer = GLib.timeout_add(HOLD_MS, self.begin_fade)

    def stop_timers(self):
        for name in ("level_timer", "fade_timer"):
            if getattr(self, name):
                GLib.source_remove(getattr(self, name))
                setattr(self, name, 0)

    def begin_fade(self):
        self.level_timer = 0
        self.fade_timer = GLib.timeout_add(FADE_MS, self.fade_step)
        return False

    def fade_step(self):
        opacity = self.get_opacity() - FADE_STEP
        if opacity > 0.01:
            self.set_opacity(opacity)
            return True
        self.fade_timer = 0
        self.dismiss()
        return False

    def dismiss(self):
        self.stop_timers()
        self.hide()

    def text(self, cr, text, font, x, y, alpha, end=False):
        """One line of text, vertically centred on y, starting at x (or ending there, with end)."""
        layout = PangoCairo.create_layout(cr)
        layout.set_font_description(Pango.FontDescription(font))
        layout.set_text(text, -1)
        width, height = layout.get_pixel_size()
        x = x - width if end else x
        cr.move_to(x + 1, y - height / 2 + 1)
        cr.set_source_rgba(0, 0, 0, 0.5 * alpha)
        PangoCairo.show_layout(cr, layout)
        cr.move_to(x, y - height / 2)
        cr.set_source_rgba(1, 1, 1, alpha)
        PangoCairo.show_layout(cr, layout)


class VolumePill(VolumeIndicator):
    """Home and Settings: a frosted glass island joined to the top edge of the screen. It springs out of the edge,
    its width shows the level and glides between levels, and it tucks back in. The timer only runs during those moments."""

    WIDTH, HEIGHT, SHOULDER, CORNER = 400, 62, 16, 28

    def __init__(self, shell):
        self.shell, self.px = shell, shell.px
        px = shell.px
        super().__init__(round(px(self.WIDTH) * 1.14) + 2 * px(self.SHOULDER), round(px(self.HEIGHT) * 1.14))
        self.set_halign(Gtk.Align.CENTER)
        self.set_valign(Gtk.Align.START)
        self.set_can_focus(False)
        self.phase = "closed"
        self.size = self.size_from = 0.0    # 0 = tucked into the edge, 1 = fully open
        self.started = 0.0
        self.bar = 0.0

    def show_level(self, percent, muted=False):
        self.percent, self.muted = percent, bool(muted)
        if self.phase != "open":
            if self.phase == "closed":
                self.bar = filled(percent, muted)
            self.begin("open")
            self.show()
        if self.level_timer:
            GLib.source_remove(self.level_timer)
        self.level_timer = GLib.timeout_add(HOLD_MS, self.begin_close)
        self.run()

    def begin(self, phase):
        self.phase, self.size_from, self.started = phase, self.size, time.monotonic()

    def begin_close(self):
        self.level_timer = 0
        self.begin("close")
        self.run()
        return False

    def run(self):
        if not self.fade_timer:
            self.fade_timer = GLib.timeout_add(ISLAND_MS, self.animate)
        self.queue_draw()

    def animate(self):
        t = time.monotonic() - self.started
        target = filled(self.percent, self.muted)
        self.bar += (target - self.bar) * 0.4
        if abs(target - self.bar) < 0.002:
            self.bar = target
        if self.phase == "close":
            self.size = closing(self.size_from, t)
            if t >= CLOSE_S:
                self.fade_timer = 0
                self.dismiss()
                return False
        else:
            self.size = opening(self.size_from, t)
        self.queue_draw()
        if self.phase == "open" and t >= OPEN_S and self.bar == target:
            self.size = 1.0
            self.fade_timer = 0
            return False
        return True

    def dismiss(self):
        self.stop_timers()
        self.phase, self.size = "closed", 0.0
        self.hide()

    def outline(self, cr, cx, width, height):
        """Flat along the screen edge, flaring into it at the shoulders, rounded below. The path stays open
        along the edge, so a stroke only follows the part that is on screen."""
        left, right = cx - width / 2, cx + width / 2
        s, r = self.px(self.SHOULDER), self.px(self.CORNER)
        cr.new_path()
        cr.arc_negative(right + s, s, s, -math.pi / 2, -math.pi)
        cr.arc(right - r, height - r, r, 0, math.pi / 2)
        cr.arc(left + r, height - r, r, math.pi / 2, math.pi)
        cr.arc_negative(left - s, s, s, 0, -math.pi / 2)

    def on_draw(self, _w, cr):
        px, cx = self.px, self.get_allocated_width() / 2
        if self.size <= 0.01:
            return False
        base = cr.get_matrix()
        cr.translate(cx, 0)                 # the whole island, contents too, grows out of the screen edge
        cr.scale(self.size, self.size)
        cr.translate(-cx, 0)
        width, height = px(self.WIDTH) * island_width(self.bar), px(self.HEIGHT)
        self.outline(cr, cx, width, height)
        cr.save()
        cr.clip_preserve()
        photo = getattr(self.shell, "photo_canvas", None)
        network.paint_frost(cr, base, self, photo)
        if getattr(photo, "transparency", True):
            cr.set_source_rgba(0.015, 0.025, 0.05, 0.34)
            cr.paint()
        cr.set_source_rgba(1, 1, 1, 0.07)
        cr.paint()
        cr.restore()
        cr.set_source_rgba(1, 1, 1, 0.22)
        cr.set_line_width(1)
        cr.stroke()
        y = height / 2
        self.speaker(cr, cx - width / 2 + px(28), y, px(12), 1.0)   # this inset centres it in the silent island
        alpha = max(0.0, min(1.0, (self.bar - 0.01) / 0.03))
        if alpha > 0:
            self.text(cr, str(self.percent), "Inter Semi-Bold %d" % px(17), cx + width / 2 - px(24), y, alpha, end=True)
        return False

    def speaker(self, cr, x, y, size, alpha):
        """A small speaker; waves for the level, a cross when silent."""
        cr.set_source_rgba(1, 1, 1, alpha)
        cr.move_to(x, y - size * 0.32)
        cr.line_to(x + size * 0.36, y - size * 0.32)
        cr.line_to(x + size * 0.86, y - size * 0.76)
        cr.line_to(x + size * 0.86, y + size * 0.76)
        cr.line_to(x + size * 0.36, y + size * 0.32)
        cr.line_to(x, y + size * 0.32)
        cr.close_path()
        cr.fill()
        cr.set_line_width(max(1.5, size * 0.15))
        cr.set_line_cap(cairo.LINE_CAP_ROUND)
        cx = x + size * 1.1
        if self.muted or not self.percent:
            d = size * 0.34
            cr.move_to(cx + size * 0.1, y - d)
            cr.line_to(cx + size * 0.1 + 2 * d, y + d)
            cr.move_to(cx + size * 0.1 + 2 * d, y - d)
            cr.line_to(cx + size * 0.1, y + d)
            cr.stroke()
            return
        for reach, level in ((0.42, 0), (0.8, 50)):
            if self.percent > level:
                cr.new_sub_path()
                cr.arc(cx - size * 0.1, y, size * reach, -0.8, 0.8)
                cr.stroke()


class AmbientVolumeLine(VolumeIndicator):
    """Ambient mode: a hairline under the clock, with a faint number. Its room is kept for all of ambient
    mode, so the weather line never moves when the volume changes; it takes no room outside ambient."""

    def __init__(self, px):
        self.px = px
        super().__init__(px(250), px(20))
        self.set_halign(Gtk.Align.START)

    def reserve(self, on):
        self.stop_timers()
        self.set_opacity(0.0)
        self.set_visible(on)

    def dismiss(self):
        self.stop_timers()
        self.set_opacity(0.0)

    def on_draw(self, _w, cr):
        px, h = self.px, self.get_allocated_height()
        right, y = px(190), h / 2
        cr.set_line_width(max(1, px(2)))
        cr.set_source_rgba(1, 1, 1, 0.20)
        cr.move_to(0, y)
        cr.line_to(right, y)
        cr.stroke()
        fill = filled(self.percent, self.muted)
        if fill:
            cr.set_source_rgba(1, 1, 1, 0.72)
            cr.move_to(0, y)
            cr.line_to(right * fill, y)
            cr.stroke()
        self.text(cr, "Muted" if self.muted else str(self.percent), "Inter %d" % px(14), right + px(12), y, 0.62)
        return False
