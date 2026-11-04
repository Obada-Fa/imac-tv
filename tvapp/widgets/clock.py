"""Home's clock, in the style picked in Settings (home.json "clock_style"):

- glass: the date in a frosted pill like the status chip, the time large beneath it, and a light running round the
  pill once a minute like a watch's second hand
- constellation: each digit a small star chart, stars of different sizes joined by thin lines, a few faint stars
  around them
- analog: a round frosted dial with hour bars, minute ticks and a sweeping-by-the-second accent hand, the day beside it
- simple: the time in a large light weight, the date under it
- drift: handwritten digits made of motes that regroup on the minute, the date under them

Glass, analog and drift redraw once a second (only their moving part where they can), briefly faster during a Drift
minute change; simple and constellation redraw once a minute. In ambient mode the clock stays, smaller and without
the date (Glass keeps its pill, which carries the seconds)."""
import datetime
import math
import random
import sys
from pathlib import Path

import gi
gi.require_version("Gtk", "3.0")
gi.require_version("PangoCairo", "1.0")
import cairo
from gi.repository import GLib, Gtk, Pango, PangoCairo

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from . import CLOCK_STYLES, Widget, drift, register  # noqa: E402


DAYS = ("Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday")
MONTHS = ("January", "February", "March", "April", "May", "June", "July", "August", "September",
          "October", "November", "December")
GLOW = (0.62, 0.80, 1.0)            # Home's glow colour (network.GLOW)
SIZES = {"glass": (400, 200), "constellation": (440, 196), "analog": (490, 216), "simple": (460, 190),
         "drift": (370, 180)}   # at 1080p
SECONDS = ("glass", "analog", "drift")     # styles that change every second; the others wait for the next minute
CAP = 92                            # digit height of the drawn-digit styles (Inter Light 128's cap height)
DRIFT_FRAME = (round(202 * CAP / 56), round(72 * CAP / 56))     # drift.frame's 202 x 72 grid, digits CAP tall
AMBIENT = 0.62                      # the clock's size in ambient mode
# Constellation digits: lines through "stars" on a 40 x 60 grid (digits from y 2 to 58); a digit may have two lines
GLYPHS = {
    "0": [[(20, 2), (36, 14), (34, 48), (18, 58), (3, 44), (5, 12), (20, 2)]],
    "1": [[(8, 14), (23, 2), (23, 58)]],
    "2": [[(4, 14), (20, 2), (35, 13), (3, 58), (37, 58)]],
    "3": [[(4, 8), (31, 4), (17, 28), (36, 43), (20, 58), (3, 52)]],
    "4": [[(27, 2), (3, 40), (38, 40)], [(27, 2), (27, 58)]],
    "5": [[(35, 2), (9, 2), (6, 26), (31, 28), (34, 48), (18, 58), (3, 52)]],
    "6": [[(31, 3), (6, 34), (12, 56), (30, 55), (35, 40), (22, 29), (6, 34)]],
    "7": [[(3, 2), (37, 2), (14, 58)]],
    "8": [[(20, 29), (7, 15), (20, 2), (33, 15), (20, 29)], [(20, 29), (4, 44), (20, 58), (36, 44), (20, 29)]],
    "9": [[(34, 22), (18, 30), (4, 20), (9, 5), (25, 2), (34, 10), (34, 22), (26, 40), (14, 58)]],
}
MAGNITUDES = (2.3, 2.8, 3.2, 3.8, 4.6)     # star radii at 1080p; the largest also gets a small cross flare


def fmt_clock(now):
    """English on purpose (the rest of the UI is), whatever the system locale says."""
    return now.strftime("%H:%M"), f"{DAYS[now.weekday()]} {now.day} {MONTHS[now.month - 1]}"


def clock_style(shell):
    style = (getattr(shell, "home_cfg", None) or {}).get("clock_style")
    return style if style in CLOCK_STYLES else CLOCK_STYLES[0]


def hand_angles(now):
    """(hour, minute, second) hand angles in radians, clockwise from 12."""
    sec = now.second
    minute = now.minute + sec / 60
    hour = now.hour % 12 + minute / 60
    return hour / 12 * 2 * math.pi, minute / 60 * 2 * math.pi, sec / 60 * 2 * math.pi


def star_chart(text, x0, top, cap):
    """Stars [(x, y)] and links [((x, y), (x, y))] for "HH:MM" drawn from x0, the digits cap tall; plus the colon's
    x and the total width."""
    s = cap / 56
    adv, gap, colon_w = 40 * s, 15 * s, 20 * s
    x, stars, links, colon = x0, {}, [], None
    for ch in text:
        if ch == ":":
            colon = x - gap / 2 + colon_w / 2
            x += colon_w
            continue
        for line in GLYPHS.get(ch, ()):
            pts = [(x + px * s, top + (py - 2) * s) for px, py in line]
            links += list(zip(pts, pts[1:]))
            for pt in pts:
                stars.setdefault((round(pt[0], 1), round(pt[1], 1)), None)
        x += adv + gap
    return list(stars), links, colon, x - gap - x0


def pill_trace(cr, x, y, w, h, length):
    """Trace `length` px of the pill's outline, clockwise from the middle of its top edge; return the head point."""
    r = h / 2
    straight = w - h
    cx = x + w / 2
    cr.move_to(cx, y)
    left = length
    parts = (("line", straight / 2, (x + w - r, y)),
             ("arc", math.pi * r, (x + w - r, y + r, -math.pi / 2)),
             ("line", straight, (x + r, y + h)),
             ("arc", math.pi * r, (x + r, y + r, math.pi / 2)),
             ("line", straight / 2, (cx, y)))
    head = (cx, y)
    for kind, size, geo in parts:
        if left <= 0:
            break
        t = min(1.0, left / size) if size else 1.0
        if kind == "line":
            sx, sy = cr.get_current_point()
            head = (sx + (geo[0] - sx) * t, sy + (geo[1] - sy) * t)
            cr.line_to(*head)
        else:
            ax, ay, a0 = geo
            a1 = a0 + math.pi * t
            cr.arc(ax, ay, r, a0, a1)
            head = (ax + r * math.cos(a1), ay + r * math.sin(a1))
        left -= size
    return head


class NexusClock(Gtk.DrawingArea):
    def __init__(self, shell, style="glass"):
        super().__init__()
        self.shell, self.px, self.style = shell, shell.px, style
        self.k = 1.0
        self.now = datetime.datetime.now()
        self.timer = 0
        self.previous_digits = None
        self.drift_key = None
        self.drawn_minute = None
        self.moving = None              # (x, y, w, h): the part that changes each second, from the last full draw
        self.set_has_window(False)
        self.resize_for(1.0)
        self.connect("draw", self.on_draw)
        self.connect("map", lambda *_: self.tick())
        self.connect("unmap", lambda *_: self.stop())
        self.connect("destroy", lambda *_: self.stop())

    def resize_for(self, k):
        self.k = k
        self.drawn_minute = self.moving = None
        w, h = SIZES[self.style]
        self.set_size_request(self.px(w * k), self.px(h * k))
        self.queue_draw()

    # ---- once a second (or once a minute), on the second, while on screen
    def tick(self):
        self.stop()
        before, self.now = self.now, datetime.datetime.now()
        ms = self.now.microsecond // 1000
        if self.style in SECONDS:
            delay = 1005 - ms
        else:
            delay = (60 - self.now.second) * 1000 + 5 - ms
        if self.style == "drift":
            elapsed = self.now.second + self.now.microsecond / 1e6
            if not self.cfg("visual_effects", True) or elapsed >= drift.DURATION:
                self.previous_digits = None
            elif before.minute != self.now.minute and 0 < (self.now - before).total_seconds() < 2:
                self.previous_digits = before.strftime("%H:%M")
            if self.previous_digits:
                delay = 75
        minute = self.now.replace(second=0, microsecond=0)
        if self.moving and minute == self.drawn_minute:
            self.queue_draw_area(*(int(v) for v in self.moving))   # only the pill or the dial changed
        else:
            self.queue_draw()
        if self.get_mapped():
            self.timer = GLib.timeout_add(delay, self.tick_again)

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
        if self.moving is None or not self.only_moving(cr):      # a full draw: this minute is now on screen
            self.drawn_minute = self.now.replace(second=0, microsecond=0)
        getattr(self, "draw_" + self.style)(cr, cr.get_matrix())
        return False

    def only_moving(self, cr):
        """True when this draw is clipped to the moving part (a seconds-only redraw)."""
        x1, y1, x2, y2 = cr.clip_extents()
        mx, my, mw, mh = self.moving
        return x1 >= mx - 1 and y1 >= my - 1 and x2 <= mx + mw + 1 and y2 <= my + mh + 1

    def date_below(self, cr, x, top):
        """The date line the drawn-digit styles share with Simple: 32 px Inter Regular under the time."""
        line = self.layout(cr, fmt_clock(self.now)[1], self.p(32), Pango.Weight.NORMAL)
        ink, _logical = line.get_pixel_extents()
        self.show(cr, line, x - ink.x, top - ink.y, 0.88)

    # ---- the styles
    def draw_glass(self, cr, base):
        from . import network
        p = self.p
        clock, date = fmt_clock(self.now)
        lay = self.layout(cr, date, p(22), Pango.Weight.MEDIUM)
        _ink, logical = lay.get_pixel_extents()
        pad, ph = p(26), p(54)
        x, y, w = p(10), p(10), logical.width + 2 * p(26)
        self.moving = (x - p(10), y - p(10), w + p(20), ph + p(20))
        network.rrect(cr, x, y, w, ph, ph / 2)
        self.glass(cr, base)
        self.show(cr, lay, x + pad, y + (ph - logical.height) / 2, 0.94)
        # the seconds: a light running clockwise round the pill from the top, once a minute
        length = 2 * (w - ph) + math.pi * ph
        cr.set_line_cap(1)
        cr.set_line_width(max(1.0, p(2.4)))
        cr.set_source_rgba(*GLOW, 0.9)
        head = pill_trace(cr, x, y, w, ph, length * self.now.second / 60)
        cr.stroke()
        self.bead(cr, *head)
        time_layout = self.layout(cr, clock, p(128), Pango.Weight.LIGHT, tabular=True)
        ink, _logical = time_layout.get_pixel_extents()
        self.show(cr, time_layout, x + p(2) - ink.x, y + ph + p(30) - ink.y, 0.97)

    def draw_constellation(self, cr, base):
        p, k = self.p, self.k
        cap, top, x0 = p(CAP), p(20), p(12)
        stars, links, colon, width = star_chart(fmt_clock(self.now)[0], x0, top, cap)
        rng = random.Random(7)                             # a few faint background stars, always in the same place
        cr.set_source_rgba(1, 1, 1, 1)
        for _ in range(16):
            fx, fy = p(4) + rng.random() * (width + p(40)), p(4) + rng.random() * (cap + p(30))
            cr.set_source_rgba(1, 1, 1, 0.16 + rng.random() * 0.2)
            cr.arc(fx, fy, p(0.8 + rng.random() * 0.6), 0, 2 * math.pi)
            cr.fill()
        cr.set_line_cap(1)                                 # the links stop short of the stars, as on a star chart
        gap = p(7)
        for line_w, colour in ((7, (*GLOW, 0.10)), (1.6, (1, 1, 1, 0.62))):
            cr.set_line_width(max(1.0, p(line_w)))
            cr.set_source_rgba(*colour)
            for (ax, ay), (bx, by) in links:
                d = math.dist((ax, ay), (bx, by))
                if d < 2 * gap:
                    continue
                ux, uy = (bx - ax) / d, (by - ay) / d
                cr.move_to(ax + ux * gap, ay + uy * gap)
                cr.line_to(bx - ux * gap, by - uy * gap)
            cr.stroke()
        for i, (sx, sy) in enumerate(stars):
            mag = (i * 3 + int(sx / k) // 7) % len(MAGNITUDES)
            self.star(cr, sx, sy, p(MAGNITUDES[mag]), flare=mag == len(MAGNITUDES) - 1)
        if colon is not None:
            for cy in (top + cap * 0.32, top + cap * 0.72):
                self.star(cr, colon, cy, p(3.2))
        if k == 1.0:
            self.date_below(cr, x0 - p(1), top + cap + p(30))

    def star(self, cr, x, y, r, flare=False):
        halo = cairo.RadialGradient(x, y, 0, x, y, r * 3)
        halo.add_color_stop_rgba(0, *GLOW, 0.30)
        halo.add_color_stop_rgba(1, *GLOW, 0.0)
        cr.set_source(halo)
        cr.arc(x, y, r * 3, 0, 2 * math.pi)
        cr.fill()
        if flare:
            cr.set_line_width(max(1.0, self.p(1)))
            for dx, dy in ((1, 0), (0, 1)):
                span = cairo.LinearGradient(x - dx * r * 4, y - dy * r * 4, x + dx * r * 4, y + dy * r * 4)
                span.add_color_stop_rgba(0, 1, 1, 1, 0)
                span.add_color_stop_rgba(0.5, 1, 1, 1, 0.55)
                span.add_color_stop_rgba(1, 1, 1, 1, 0)
                cr.set_source(span)
                cr.move_to(x - dx * r * 4, y - dy * r * 4)
                cr.line_to(x + dx * r * 4, y + dy * r * 4)
                cr.stroke()
        cr.set_source_rgba(1, 1, 1, 0.98)
        cr.arc(x, y, r, 0, 2 * math.pi)
        cr.fill()

    def draw_analog(self, cr, base):
        p, k = self.p, self.k
        r = p(100)
        cx, cy = r + p(6), r + p(6)
        self.moving = (0, 0, cx + r + p(6), cy + r + p(6))
        cr.new_path()
        cr.arc(cx, cy, r, 0, 2 * math.pi)
        self.glass(cr, base, rim=0.32)
        cr.set_line_cap(0)
        for i in range(60):                                # hour bars, quarters a little heavier, minute ticks
            a = i / 60 * 2 * math.pi - math.pi / 2
            ux, uy = math.cos(a), math.sin(a)
            if i % 15 == 0:
                inner, width, alpha = 0.74, 4.4, 0.97
            elif i % 5 == 0:
                inner, width, alpha = 0.78, 3.4, 0.9
            else:
                inner, width, alpha = 0.875, 1.3, 0.38
            cr.set_source_rgba(1, 1, 1, alpha)
            cr.set_line_width(max(1.0, p(width)))
            cr.move_to(cx + ux * r * inner, cy + uy * r * inner)
            cr.line_to(cx + ux * r * 0.92, cy + uy * r * 0.92)
            cr.stroke()
        hour, minute, second = hand_angles(self.now)
        cr.set_line_cap(1)
        for angle, tail, length, width in ((hour, 0.08, 0.50, 8), (minute, 0.10, 0.78, 5)):
            a = angle - math.pi / 2
            ux, uy = math.cos(a), math.sin(a)
            for dy, colour in ((p(2), (0, 0, 0, 0.32)), (0, (1, 1, 1, 0.97))):
                cr.set_source_rgba(*colour)
                cr.set_line_width(max(1.5, p(width)))
                cr.move_to(cx - ux * r * tail, cy + dy - uy * r * tail)
                cr.line_to(cx + ux * r * length, cy + dy + uy * r * length)
                cr.stroke()
        a = second - math.pi / 2                           # the seconds hand, in Home's glow, with a counterweight
        ux, uy = math.cos(a), math.sin(a)
        cr.set_source_rgba(*GLOW, 1.0)
        cr.set_line_width(max(1.0, p(2)))
        cr.move_to(cx - ux * r * 0.20, cy - uy * r * 0.20)
        cr.line_to(cx + ux * r * 0.86, cy + uy * r * 0.86)
        cr.stroke()
        cr.arc(cx - ux * r * 0.16, cy - uy * r * 0.16, p(4.5), 0, 2 * math.pi)
        cr.fill()
        cr.arc(cx, cy, p(7), 0, 2 * math.pi)
        cr.fill()
        cr.set_source_rgba(1, 1, 1, 1)
        cr.arc(cx, cy, p(2.6), 0, 2 * math.pi)
        cr.fill()
        if k == 1.0:                                       # the day beside the dial, quietly
            day, rest = fmt_clock(self.now)[1].split(" ", 1)
            big = self.layout(cr, day, p(46), Pango.Weight.LIGHT)
            small = self.layout(cr, rest, p(30), Pango.Weight.NORMAL)
            bh, sh = big.get_pixel_size()[1], small.get_pixel_size()[1]
            x, y = cx + r + p(36), cy - (bh + sh) / 2
            self.show(cr, big, x, y, 0.96)
            self.show(cr, small, x + p(1), y + bh, 0.80)

    def draw_drift(self, cr, base):
        clock = self.now.strftime("%H:%M")
        before = self.previous_digits or clock
        seconds = self.now.second + self.now.microsecond / 1e6
        progress = min(1.0, seconds / drift.DURATION) if self.previous_digits else 1.0
        phase = math.tau * seconds / 60 if self.cfg("visual_effects", True) else 0.0
        w, h = DRIFT_FRAME
        key = (self.px(w * self.k), self.px(h * self.k), clock, before, progress, phase)
        if key != self.drift_key:
            self.drift_frame = drift.frame(*key)
            self.drift_key = key
        cr.set_source_surface(self.drift_frame, 0, 0)
        cr.paint()
        if self.k == 1.0:
            self.date_below(cr, self.p(8), key[1] * 62 / 72 + self.p(26))

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
