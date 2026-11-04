"""Home's clock, in the style picked in Settings (home.json "clock_style"):

- glass: the time on a frosted glass tile like Home's, the date above it, and the minute filling a lit hairline
- constellation: the digits drawn as Home's network, lit links between small nodes, the colon breathing
- analog: a frosted glass dial in Home's node shape, two hands and a seconds bead inside its rim, the day beside it
- simple: the time in a large light weight, the date under it

It redraws once a second, only its own small area, and only while it is on screen. In ambient mode it stays,
smaller and without the date."""
import datetime
import math
import sys
from pathlib import Path

import gi
gi.require_version("Gtk", "3.0")
gi.require_version("PangoCairo", "1.0")
from gi.repository import GLib, Gtk, Pango, PangoCairo

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from . import Widget, register  # noqa: E402


DAYS = ("Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday")
MONTHS = ("January", "February", "March", "April", "May", "June", "July", "August", "September",
          "October", "November", "December")
STYLES = ("glass", "constellation", "analog", "simple")
GLOW = (0.62, 0.80, 1.0)            # Home's glow colour (network.GLOW)
SIZES = {"glass": (470, 214), "constellation": (520, 196), "analog": (440, 196), "simple": (460, 190)}   # at 1080p
AMBIENT = 0.62                      # the clock's size in ambient mode
# Seven-segment digits: a top, b top right, c bottom right, d bottom, e bottom left, f top left, g middle
SEGMENTS = {"0": "abcdef", "1": "bc", "2": "abged", "3": "abgcd", "4": "fgbc", "5": "afgcd", "6": "afgedc",
            "7": "abc", "8": "abcdefg", "9": "abcdfg"}
# Each segment joins two of a digit's six nodes: 0 top left, 1 top right, 2 middle left, 3 middle right,
# 4 bottom left, 5 bottom right
LINKS = {"a": (0, 1), "b": (1, 3), "c": (3, 5), "d": (4, 5), "e": (2, 4), "f": (0, 2), "g": (2, 3)}


def fmt_clock(now):
    """English on purpose (the rest of the UI is), whatever the system locale says."""
    return now.strftime("%H:%M"), f"{DAYS[now.weekday()]} {now.day} {MONTHS[now.month - 1]}"


def clock_style(shell):
    style = (getattr(shell, "home_cfg", None) or {}).get("clock_style")
    return style if style in STYLES else STYLES[0]


def hand_angles(now):
    """(hour, minute, second) hand angles in radians, clockwise from 12."""
    sec = now.second
    minute = now.minute + sec / 60
    hour = now.hour % 12 + minute / 60
    return hour / 12 * 2 * math.pi, minute / 60 * 2 * math.pi, sec / 60 * 2 * math.pi


def lit_links(text):
    """The node pairs lit for each character of "HH:MM": a set per character (the colon's is empty)."""
    return [set(LINKS[s] for s in SEGMENTS.get(ch, "")) for ch in text]


def hexagon(cr, cx, cy, r):
    cr.new_path()
    for i in range(6):
        a = i * math.pi / 3
        cr.line_to(cx + r * math.cos(a), cy + r * math.sin(a))
    cr.close_path()


class NexusClock(Gtk.DrawingArea):
    def __init__(self, shell, style="glass"):
        super().__init__()
        self.shell, self.px, self.style = shell, shell.px, style
        self.k = 1.0
        self.now = datetime.datetime.now()
        self.timer = 0
        self.set_has_window(False)
        self.resize_for(1.0)
        self.connect("draw", self.on_draw)
        self.connect("map", lambda *_: self.tick())
        self.connect("unmap", lambda *_: self.stop())
        self.connect("destroy", lambda *_: self.stop())

    def resize_for(self, k):
        self.k = k
        w, h = SIZES[self.style]
        self.set_size_request(self.px(w * k), self.px(h * k))
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

    # ---- shared pieces
    def p(self, n):
        return self.px(n) * self.k

    def cfg(self, key, default):
        return (getattr(self.shell, "home_cfg", None) or {}).get(key, default)

    @staticmethod
    def layout(cr, text, size, weight, tabular=False, spacing=0):
        layout = PangoCairo.create_layout(cr)
        desc = Pango.FontDescription("Inter")
        desc.set_absolute_size(size * Pango.SCALE)
        desc.set_weight(weight)
        layout.set_font_description(desc)
        attrs = Pango.AttrList()
        if tabular:                             # the digits keep their width, so the time never jiggles
            attrs.insert(Pango.attr_font_features_new("tnum"))
        if spacing:
            attrs.insert(Pango.attr_letter_spacing_new(int(spacing * Pango.SCALE)))
        layout.set_attributes(attrs)
        layout.set_text(text, -1)
        return layout

    @staticmethod
    def show(cr, layout, x, y, alpha):
        """Text with a soft shadow, so it reads over any photo."""
        for dy, a in ((2, 0.28), (1, 0.32)):
            cr.move_to(x, y + dy)
            cr.set_source_rgba(0, 0, 0, a * alpha)
            PangoCairo.show_layout(cr, layout)
        cr.move_to(x, y)
        cr.set_source_rgba(1, 1, 1, alpha)
        PangoCairo.show_layout(cr, layout)

    def glass(self, cr, base, rim=0.22):
        """Fill the path just traced with Home's frosted glass and draw its rim. Uses up the path."""
        from . import network             # here: widgets.network imports the widget registry
        cr.save()
        cr.clip_preserve()
        network.paint_frost(cr, base, self, getattr(self.shell, "photo_canvas", None))
        cr.set_source_rgba(0.015, 0.025, 0.05, 0.30)
        cr.paint()
        cr.set_source_rgba(1, 1, 1, 0.06)
        cr.paint()
        cr.restore()
        cr.set_source_rgba(1, 1, 1, rim)
        cr.set_line_width(max(1.0, 1.2 * self.k))
        cr.stroke()

    def bead(self, cr, x, y):
        """A lit bead: a soft glow round a white core."""
        for rad, colour in ((8, (*GLOW, 0.16)), (5, (*GLOW, 0.32)), (3, (1, 1, 1, 1.0))):
            cr.set_source_rgba(*colour)
            cr.arc(x, y, self.p(rad), 0, 2 * math.pi)
            cr.fill()

    def on_draw(self, _w, cr):
        getattr(self, "draw_" + self.style)(cr, cr.get_matrix())
        return False

    # ---- the styles
    def draw_glass(self, cr, base):
        from . import network
        p, k = self.p, self.k
        clock, date = fmt_clock(self.now)
        x, y = 2, 2
        w, h = self.get_allocated_width() - 4, self.get_allocated_height() - 4
        if self.cfg("tile_shape", "rounded") == "chamfer":
            network.chamfer_rect(cr, x, y, w, h, p(22))
        else:
            network.rrect(cr, x, y, w, h, p(30))
        self.glass(cr, base)
        pad = p(34)
        if k == 1.0:
            kicker = self.layout(cr, date.upper(), p(17), Pango.Weight.SEMIBOLD, spacing=p(2.2))
            self.show(cr, kicker, x + pad, y + p(26), 0.72)
        time_layout = self.layout(cr, clock, p(108), Pango.Weight.LIGHT, tabular=True)
        ink, _logical = time_layout.get_pixel_extents()
        top = y + (p(60) if k == 1.0 else p(24)) - ink.y
        self.show(cr, time_layout, x + pad - ink.x, top, 0.97)
        # the minute so far: a lit hairline along the bottom, with a bead at its head
        line_y, left, right = y + h - p(26), x + pad, x + w - pad
        cr.set_line_cap(1)
        cr.set_line_width(max(1.0, p(2.4)))
        cr.set_source_rgba(1, 1, 1, 0.12)
        cr.move_to(left, line_y)
        cr.line_to(right, line_y)
        cr.stroke()
        head = left + (right - left) * self.now.second / 60
        if head > left:
            cr.set_source_rgba(*GLOW, 0.9)
            cr.move_to(left, line_y)
            cr.line_to(head, line_y)
            cr.stroke()
        self.bead(cr, head, line_y)

    def draw_constellation(self, cr, base):
        p, k = self.p, self.k
        clock, date = fmt_clock(self.now)
        dw, dh, gap, colon, lean = p(62), p(112), p(30), p(40), p(10)
        x, top = p(10), p(16)
        nodes, links, colon_x = [], [], 0.0
        for ch, lit in zip(clock, lit_links(clock)):
            if ch == ":":
                colon_x = x - gap / 2 + colon / 2 + lean / 2
                x += colon
                continue
            pts = [(x + lean, top), (x + dw + lean, top), (x + lean / 2, top + dh / 2),
                   (x + dw + lean / 2, top + dh / 2), (x, top + dh), (x + dw, top + dh)]
            links += [(pts[a], pts[b], (a, b) in lit) for a, b in LINKS.values()]
            used = {i for pair in lit for i in pair}
            nodes += [(pt, i in used) for i, pt in enumerate(pts)]
            x += dw + gap
        cr.set_line_cap(1)
        cr.set_source_rgba(1, 1, 1, 0.09)                  # the unlit links, faintly
        cr.set_line_width(max(1.0, p(1.4)))
        for a, b, on in links:
            if not on:
                cr.move_to(*a)
                cr.line_to(*b)
        cr.stroke()
        for width, colour in ((11, (*GLOW, 0.16)), (5, (*GLOW, 0.35)), (2.6, (1, 1, 1, 0.97))):
            cr.set_source_rgba(*colour)                    # the lit ones, glowing
            cr.set_line_width(max(1.0, p(width)))
            for a, b, on in links:
                if on:
                    cr.move_to(*a)
                    cr.line_to(*b)
            cr.stroke()
        for (cx, cy), on in nodes:
            hexagon(cr, cx, cy, p(5.5 if on else 3.2))
            if on:
                cr.set_source_rgba(*GLOW, 1.0)
                cr.fill_preserve()
                cr.set_source_rgba(1, 1, 1, 0.95)
                cr.set_line_width(max(1.0, p(1.2)))
                cr.stroke()
            else:
                cr.set_source_rgba(1, 1, 1, 0.18)
                cr.fill()
        bright = 1.0 if self.now.second % 2 == 0 else 0.4  # the colon breathes with the seconds
        for cy in (top + dh * 0.3, top + dh * 0.7):
            for rad, a in ((11, 0.14), (6, 1.0)):
                hexagon(cr, colon_x, cy, p(rad))
                cr.set_source_rgba(*GLOW, a * bright)
                cr.fill()
        if k == 1.0:
            line = self.layout(cr, date, p(28), Pango.Weight.NORMAL)
            self.show(cr, line, p(4), top + dh + p(22), 0.86)

    def draw_analog(self, cr, base):
        from . import network
        p, k = self.p, self.k
        r = p(90)
        cx, cy = r + p(6), r + p(6)
        network.poly_path(cr, network.poly_points(self.cfg("node_shape", "hexagon"), r, cx, cy))
        self.glass(cr, base, rim=0.32)
        hour, minute, second = hand_angles(self.now)
        for i in range(12):                                # twelve beads, the quarters larger
            a = i / 12 * 2 * math.pi - math.pi / 2
            d = r * 0.68
            cr.set_source_rgba(1, 1, 1, 0.85 if i % 3 == 0 else 0.4)
            cr.arc(cx + d * math.cos(a), cy + d * math.sin(a), p(3.2 if i % 3 == 0 else 2), 0, 2 * math.pi)
            cr.fill()
        cr.set_line_cap(1)
        for angle, length, width in ((hour, 0.40, 6.5), (minute, 0.60, 3.6)):
            a = angle - math.pi / 2
            for dy, colour in ((p(2), (0, 0, 0, 0.3)), (0, (1, 1, 1, 0.97))):
                cr.set_source_rgba(*colour)
                cr.set_line_width(max(1.5, p(width)))
                cr.move_to(cx, cy + dy)
                cr.line_to(cx + r * length * math.cos(a), cy + dy + r * length * math.sin(a))
                cr.stroke()
        a = second - math.pi / 2                           # the seconds: a bead running round inside the rim
        self.bead(cr, cx + r * 0.82 * math.cos(a), cy + r * 0.82 * math.sin(a))
        cr.set_source_rgba(*GLOW, 1.0)
        cr.arc(cx, cy, p(5), 0, 2 * math.pi)
        cr.fill()
        if k == 1.0:                                       # the day beside the dial, quietly
            day, rest = fmt_clock(self.now)[1].split(" ", 1)
            big = self.layout(cr, day, p(40), Pango.Weight.LIGHT)
            small = self.layout(cr, rest, p(26), Pango.Weight.NORMAL)
            bh, sh = big.get_pixel_size()[1], small.get_pixel_size()[1]
            x, y = cx + r + p(34), cy - (bh + sh) / 2
            self.show(cr, big, x, y, 0.95)
            self.show(cr, small, x, y + bh, 0.72)

    def draw_simple(self, cr, base):
        p, k = self.p, self.k
        clock, date = fmt_clock(self.now)
        time_layout = self.layout(cr, clock, p(128), Pango.Weight.ULTRALIGHT, tabular=True)
        ink, _logical = time_layout.get_pixel_extents()
        self.show(cr, time_layout, p(2) - ink.x, p(8) - ink.y, 0.97)
        if k == 1.0:
            line = self.layout(cr, date, p(32), Pango.Weight.NORMAL)
            self.show(cr, line, p(4), p(8) + ink.height + p(22), 0.88)


@register
class ClockWidget(Widget):
    id = "clock"
    slot = "topleft"
    interval = 0                    # the clock keeps its own second
    keep_in_ambient = True
    order = 0

    def build(self):
        self.face = NexusClock(self.shell, clock_style(self.shell))
        self.box = self.face
        return self.face

    def refresh(self):
        self.face.tick()

    def on_ambient(self, on):
        self.face.resize_for(AMBIENT if on else 1.0)
