"""Home's clock: a frosted glass dial beside the time in a large light weight, with the date under it.

The dial's hands sit on a small hexagon, like Home's nodes, and the seconds run round its rim as a lit arc that
fades behind its head. It redraws once a second, only its own small area, and only while it is on screen. In
ambient mode it stays, smaller and without the date."""
import datetime
import math
import sys
from pathlib import Path

import cairo
import gi
gi.require_version("Gtk", "3.0")
gi.require_version("PangoCairo", "1.0")
from gi.repository import GLib, Gtk, Pango, PangoCairo

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from . import Widget, register  # noqa: E402


DAYS = ("Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday")
MONTHS = ("January", "February", "March", "April", "May", "June", "July", "August", "September",
          "October", "November", "December")
GLOW = (0.62, 0.80, 1.0)            # Home's glow colour (network.GLOW)
# At 1080p: the dial, the gap after it, the time's and the date's sizes, and the whole clock's room
DIAL, GAP, TIME_PX, DATE_PX, WIDTH, HEIGHT = 188, 40, 124, 30, 640, 200
AMBIENT = 0.62                      # the clock's size in ambient mode


def fmt_clock(now):
    """English on purpose (the rest of the UI is), whatever the system locale says."""
    return now.strftime("%H:%M"), f"{DAYS[now.weekday()]} {now.day} {MONTHS[now.month - 1]}"


def hand_angles(now):
    """(hour, minute, second) hand angles in radians, clockwise from 12."""
    sec = now.second
    minute = now.minute + sec / 60
    hour = now.hour % 12 + minute / 60
    return hour / 12 * 2 * math.pi, minute / 60 * 2 * math.pi, sec / 60 * 2 * math.pi


class NexusClock(Gtk.DrawingArea):
    def __init__(self, shell):
        super().__init__()
        self.shell, self.px = shell, shell.px
        self.k = 1.0
        self.now = datetime.datetime.now()
        self.timer = 0
        self.layouts = {}
        self.set_has_window(False)
        self.resize_for(1.0)
        self.connect("draw", self.on_draw)
        self.connect("map", lambda *_: self.tick())
        self.connect("unmap", lambda *_: self.stop())
        self.connect("destroy", lambda *_: self.stop())

    def resize_for(self, k):
        self.k = k
        self.set_size_request(self.px(WIDTH * k), self.px(HEIGHT * k))
        self.queue_draw()

    # ---- once a second, on the second, while on screen
    def tick(self):
        self.stop()
        self.now = datetime.datetime.now()
        self.queue_draw()
        self.timer = GLib.timeout_add(1005 - self.now.microsecond // 1000, self.tick_again)

    def tick_again(self):
        self.timer = 0
        if self.get_mapped():
            self.tick()
        return False

    def stop(self):
        if self.timer:
            GLib.source_remove(self.timer)
            self.timer = 0

    # ---- drawing
    def layout(self, cr, text, size, weight, tabular=False):
        layout = PangoCairo.create_layout(cr)
        desc = Pango.FontDescription("Inter")
        desc.set_absolute_size(size * Pango.SCALE)
        desc.set_weight(weight)
        layout.set_font_description(desc)
        if tabular:                             # the digits keep their width, so the time never jiggles
            attrs = Pango.AttrList()
            attrs.insert(Pango.attr_font_features_new("tnum"))
            layout.set_attributes(attrs)
        layout.set_text(text, -1)
        return layout

    @staticmethod
    def show(cr, layout, x, y, alpha):
        """Text with a soft shadow, so it reads over any photo."""
        for dx, dy, a in ((0, 2, 0.30), (0, 1, 0.35)):
            cr.move_to(x + dx, y + dy)
            cr.set_source_rgba(0, 0, 0, a * alpha)
            PangoCairo.show_layout(cr, layout)
        cr.move_to(x, y)
        cr.set_source_rgba(1, 1, 1, alpha)
        PangoCairo.show_layout(cr, layout)

    def on_draw(self, _w, cr):
        p = lambda n: self.px(n) * self.k  # noqa: E731
        h = self.get_allocated_height()
        base = cr.get_matrix()
        r = p(DIAL) / 2
        cx, cy = r + 2, h / 2
        self.dial(cr, base, cx, cy, r)
        clock, date = fmt_clock(self.now)
        x = cx + r + p(GAP)
        time_layout = self.layout(cr, clock, p(TIME_PX), Pango.Weight.ULTRALIGHT, tabular=True)
        ink, logical = time_layout.get_pixel_extents()
        show_date = self.k == 1.0
        date_layout = self.layout(cr, date, p(DATE_PX), Pango.Weight.NORMAL) if show_date else None
        date_h = date_layout.get_pixel_size()[1] if date_layout else 0
        block = ink.height + (p(14) + date_h if date_layout else 0)
        top = cy - block / 2 - ink.y               # the digits' ink, not the font's line, centred on the dial
        self.show(cr, time_layout, x - ink.x, top, 0.97)
        if date_layout:
            self.show(cr, date_layout, x, top + ink.y + ink.height + p(14), 0.86)
        return False

    @staticmethod
    def hand(cr, length, width, tail):
        """A hand pointing up from the origin: widest near the hub, narrowing to a rounded tip, with a short tail."""
        w, tip = width / 2, width * 0.16
        cr.new_path()
        cr.move_to(-w * 0.55, tail)
        cr.line_to(-w, 0)
        cr.line_to(-tip, -length + tip)
        cr.arc(0, -length + tip, tip, math.pi, 0)
        cr.line_to(w, 0)
        cr.line_to(w * 0.55, tail)
        cr.close_path()

    def dial(self, cr, base, cx, cy, r):
        k = self.k
        # glass: frosted from the photo behind, tinted, with a light rim that is brighter towards the top left
        cr.save()
        cr.arc(cx, cy, r, 0, 2 * math.pi)
        cr.clip_preserve()
        from . import network             # here: widgets.network imports the widget registry
        network.paint_frost(cr, base, self, getattr(self.shell, "photo_canvas", None))
        cr.set_source_rgba(0.015, 0.025, 0.05, 0.30)
        cr.paint()
        sheen = cairo.LinearGradient(cx - r, cy - r, cx + r, cy + r)
        sheen.add_color_stop_rgba(0, 1, 1, 1, 0.12)
        sheen.add_color_stop_rgba(0.55, 1, 1, 1, 0.02)
        sheen.add_color_stop_rgba(1, 1, 1, 1, 0.0)
        cr.set_source(sheen)
        cr.paint()
        cr.restore()
        rim = cairo.LinearGradient(cx - r, cy - r, cx + r, cy + r)
        rim.add_color_stop_rgba(0, 1, 1, 1, 0.55)
        rim.add_color_stop_rgba(0.5, 1, 1, 1, 0.16)
        rim.add_color_stop_rgba(1, 1, 1, 1, 0.30)
        cr.set_source(rim)
        cr.set_line_width(max(1.0, 1.4 * k))
        cr.arc(cx, cy, r - 0.7, 0, 2 * math.pi)
        cr.stroke()

        hour, minute, second = hand_angles(self.now)
        ring = r - self.px(9) * k
        # the seconds: a faint track, and a lit arc from 12 that brightens towards its head
        cr.set_line_cap(cairo.LINE_CAP_ROUND)
        cr.set_line_width(max(1.5, self.px(3) * k))
        cr.set_source_rgba(1, 1, 1, 0.08)
        cr.arc(cx, cy, ring, 0, 2 * math.pi)
        cr.stroke()
        if second > 0:
            steps = max(1, round(second / (2 * math.pi) * 60))
            for i in range(steps):
                a0 = -math.pi / 2 + second * i / steps
                a1 = -math.pi / 2 + second * (i + 1) / steps
                t = (i + 1) / steps
                cr.set_source_rgba(*GLOW, 0.05 + 0.85 * t ** 2.2)
                cr.arc(cx, cy, ring, a0, a1)
                cr.stroke()
        head = -math.pi / 2 + second
        hx, hy = cx + ring * math.cos(head), cy + ring * math.sin(head)
        for rad, a in ((10, 0.10), (6.5, 0.22)):
            cr.set_source_rgba(*GLOW, a)
            cr.arc(hx, hy, self.px(rad) * k, 0, 2 * math.pi)
            cr.fill()
        cr.set_source_rgba(1, 1, 1, 0.98)
        cr.arc(hx, hy, self.px(3.6) * k, 0, 2 * math.pi)
        cr.fill()

        # ticks: twelve hour marks, the quarters longest; minute dots between them
        outer = ring - self.px(10) * k
        for i in range(60):
            a = i / 60 * 2 * math.pi - math.pi / 2
            ca, sa = math.cos(a), math.sin(a)
            if i % 5:
                cr.set_source_rgba(1, 1, 1, 0.28)
                cr.arc(cx + outer * ca, cy + outer * sa, max(0.8, self.px(1.1) * k), 0, 2 * math.pi)
                cr.fill()
                continue
            length = self.px(16 if i % 15 == 0 else 10) * k
            cr.set_line_width(max(1.2, self.px(3 if i % 15 == 0 else 2) * k))
            cr.set_source_rgba(1, 1, 1, 0.92 if i % 15 == 0 else 0.62)
            cr.move_to(cx + outer * ca, cy + outer * sa)
            cr.line_to(cx + (outer - length) * ca, cy + (outer - length) * sa)
            cr.stroke()

        # tapered hands with a soft shadow under them; the minute hand carries a lit line
        for angle, length, width, lit in ((hour, 0.50, 9.0, False), (minute, 0.74, 6.0, True)):
            for dy, colour in ((self.px(2.5) * k, (0, 0, 0, 0.32)), (0, (1, 1, 1, 0.97))):
                cr.save()
                cr.translate(cx, cy + dy)
                cr.rotate(angle)
                self.hand(cr, r * length, self.px(width) * k, self.px(12) * k)
                cr.set_source_rgba(*colour)
                cr.fill()
                cr.restore()
            if lit:
                cr.save()
                cr.translate(cx, cy)
                cr.rotate(angle)
                cr.set_line_width(max(1.0, self.px(1.6) * k))
                cr.set_source_rgba(*GLOW, 0.95)
                cr.move_to(0, -r * length * 0.22)
                cr.line_to(0, -r * length * 0.86)
                cr.stroke()
                cr.restore()
        # the hub: a small lit hexagon, like Home's nodes
        hub = self.px(8.5) * k
        cr.new_path()
        for i in range(6):
            a = math.pi / 6 + i * math.pi / 3
            cr.line_to(cx + hub * math.cos(a), cy + hub * math.sin(a))
        cr.close_path()
        cr.set_source_rgba(*GLOW, 1.0)
        cr.fill_preserve()
        cr.set_source_rgba(1, 1, 1, 0.9)
        cr.set_line_width(max(1.0, 1.4 * k))
        cr.stroke()
        cr.set_source_rgba(1, 1, 1, 1)
        cr.arc(cx, cy, hub * 0.32, 0, 2 * math.pi)
        cr.fill()


@register
class ClockWidget(Widget):
    id = "clock"
    slot = "topleft"
    interval = 0                    # the clock keeps its own second
    keep_in_ambient = True
    order = 0

    def build(self):
        self.face = NexusClock(self.shell)
        self.box = self.face
        return self.face

    def refresh(self):
        self.face.tick()

    def on_ambient(self, on):
        self.face.resize_for(AMBIENT if on else 1.0)
