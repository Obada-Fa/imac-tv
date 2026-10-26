"""Home's drawn glass language as a small kit for menus and overlays (Settings first; the loading screen, the
header and the phone follow). GTK3 CSS cannot draw hexagons or frost, so every piece here is cairo-drawn with
network.py's own shape helpers (poly_points, poly_path, chamfer_rect, rrect, paint_frost, draw_glow).

API (all sizes are 1080p px; pass shell.px()-scaled values)
  Look(cfg)                    node_shape, tile_shape, transparency, effects, read from a home.json config
  tile_path(cr, shape, x, y, w, h, s=1.0, grow=0)
                               "rounded" or "chamfer" tile outline (the tile_shape setting)
  Backdrop(shell, look)        full-size DrawingArea: Home's photo blurred + dimmed (navy without a photo or with
                               transparency off) and a faint static constellation, rendered once per size into a
                               cached surface. It is also the frost SOURCE for every piece below (it quacks like
                               Home's PhotoCanvas: blur_layers(), blur_k, transparency), so redraws of a small
                               region always line up with what is behind it.
  GlassPanel(shell, src, look, fade=0, scroller=None)
                               Gtk.Box that paints a frosted tile (tile_shape) behind its children; fade = px of
                               solid-then-fading glass over content cut at the panel's (or scroller's) edges
  GlassField(shell, src, look, child)
                               glass well around an entry; hairline brightens while the child has focus
  draw_badge(cr, base, widget, src, look, r, glyph, tint, f, lit)
                               the hexagon/octagon glass node (Home's RoundNode body) with a line glyph
  draw_toggle(cr, shape, cx, cy, r, on, f, tint)
                               hex toggle: filled tint = on, hairline outline = off
  draw_check(cr, shape, cx, cy, r, tint)   small filled hexagon with a check (the "selected" mark)
  glyph(cr, name, cx, cy, s)   line icons: appearance ambient display playback connections system check phone
  draw_signal(cr, shape, x, y, s, level)   four rising small hexagons on baseline y, `level` 0..4 lit
  draw_links(cr, pts, scale, alpha)        constellation lines + dust diamonds for [(x, y)]
  Focusable(shell, src, look)  base DrawingArea: OK/Enter/space activate, focus easing at <= 15 fps (only while
                               it moves), sel_key, Home's focus (scale, 22% -> 90% hairline, glow)
  GlassRow(...)                borderless row: title (+ sub) left; value right in a quieter weight, or a toggle
  RailNode(...)                badge + label for a vertical rail of categories; .selected lights it up
  PictureTile(...)             frosted tile with a picture, title/sub inside, hex check when selected
  SectionLabel(shell, text)    small letter-spaced caps behind a tiny hexagon marker
  SignalLine(shell, net)       hex signal glyph + quiet "name · ip" text

Calm cost is zero: nothing here owns a timer except the shared focus ticker, which runs only while some
widget is still easing toward its focus state and stops by itself."""
import math
import sys
from pathlib import Path

import gi
gi.require_version("Gtk", "3.0")
gi.require_version("Pango", "1.0")
gi.require_version("PangoCairo", "1.0")
from gi.repository import Gtk, Gdk, GLib, Pango, PangoCairo

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from .bubbles import _icon, ease  # noqa: E402
from .network import (poly_points, poly_path, chamfer_rect, rrect, paint_frost, draw_glow, dust_points,  # noqa: E402
                      link_pairs, line_alpha, point_in_poly, LINE_MAX)

try:
    import cairo
except Exception:  # pragma: no cover
    cairo = None

FRAME_MS = 67                 # focus easing at <= 15 fps
FONT = "Inter, Noto Sans, Sans"
ACCENT = (0.62, 0.80, 1.0)    # Home's glow colour, the default tint
NAVY = ((0, 0.05, 0.07, 0.12), (0.55, 0.07, 0.09, 0.15), (1, 0.02, 0.03, 0.06))   # Home's no-photo gradient
SOLID = (0.075, 0.105, 0.15)  # Home's glass with transparency off
TINTS = {"Appearance": (0.62, 0.42, 0.84), "Ambient": (0.36, 0.52, 0.86), "Display": (0.28, 0.66, 0.62),
         "Playback": (0.86, 0.56, 0.32), "Connections": (0.30, 0.62, 0.84), "System": (0.50, 0.56, 0.66)}


def scale_of(shell):
    return getattr(shell, "scale", None) or shell.px(1000) / 1000.0


class Look:
    """The four switches every piece follows."""

    def __init__(self, cfg=None):
        cfg = cfg or {}
        self.node_shape = "octagon" if cfg.get("node_shape") == "octagon" else "hexagon"
        self.tile_shape = "chamfer" if cfg.get("tile_shape") == "chamfer" else "rounded"
        self.transparency = bool(cfg.get("transparency", True))
        self.effects = bool(cfg.get("visual_effects", True))

    def key(self):
        return (self.node_shape, self.tile_shape, self.transparency, self.effects)


# ---------------------------------------------------------------- shapes and small drawings

def tile_path(cr, shape, x, y, w, h, s=1.0, grow=0.0):
    """Home's tile outline: rounded corners (10 px at 1080p) or cut corners (16 px)."""
    if shape == "chamfer":
        chamfer_rect(cr, x, y, w, h, 16 * s + grow)
    else:
        cr.new_path()
        rrect(cr, x, y, w, h, 10 * s + grow)


def glyph(cr, name, cx, cy, s, alpha=0.92):
    """Line icons in the stroke style of Home's node icons; s ~ half-size."""
    if name in ("system", "settings"):
        _icon(cr, "settings", cx, cy, s)
        return
    if name in ("movies", "series", "search", "youtube"):
        _icon(cr, name, cx, cy, s)
        return
    cr.save()
    cr.set_line_width(max(2.0, s * 0.11))
    cr.set_line_cap(cairo.LINE_CAP_ROUND)
    cr.set_line_join(cairo.LINE_JOIN_ROUND)
    cr.set_source_rgba(1, 1, 1, alpha)
    cr.new_path()
    if name == "appearance":                      # half-filled disc: light and shade
        cr.arc(cx, cy, s * 0.55, 0, 2 * math.pi)
        cr.stroke()
        cr.arc(cx, cy, s * 0.55, math.pi / 2, 1.5 * math.pi)
        cr.close_path()
        cr.fill()
    elif name == "ambient":                       # crescent moon and a small star
        cr.arc(cx - s * 0.08, cy + s * 0.04, s * 0.52, 0, 2 * math.pi)
        cr.new_sub_path()
        cr.arc(cx + s * 0.18, cy - s * 0.16, s * 0.42, 0, 2 * math.pi)
        cr.set_fill_rule(cairo.FILL_RULE_EVEN_ODD)
        cr.save()
        cr.clip()
        cr.arc(cx - s * 0.08, cy + s * 0.04, s * 0.52, 0, 2 * math.pi)
        cr.fill()
        cr.restore()
        cr.new_path()
        d = s * 0.14
        sx, sy = cx + s * 0.48, cy - s * 0.5
        cr.move_to(sx, sy - d)
        cr.line_to(sx + d, sy)
        cr.line_to(sx, sy + d)
        cr.line_to(sx - d, sy)
        cr.close_path()
        cr.fill()
    elif name == "display":                       # screen on a stand
        w, h = s * 1.3, s * 0.86
        rrect(cr, cx - w / 2, cy - h / 2 - s * 0.12, w, h, s * 0.12)
        cr.stroke()
        cr.move_to(cx - s * 0.3, cy + s * 0.58)
        cr.line_to(cx + s * 0.3, cy + s * 0.58)
        cr.stroke()
    elif name == "playback":                      # play in a ring
        cr.arc(cx, cy, s * 0.6, 0, 2 * math.pi)
        cr.stroke()
        t = s * 0.26
        cr.move_to(cx - t * 0.6, cy - t)
        cr.line_to(cx + t * 1.0, cy)
        cr.line_to(cx - t * 0.6, cy + t)
        cr.close_path()
        cr.fill()
    elif name in ("connections", "phone"):        # phone with two signal arcs
        w, h = s * 0.62, s * 1.12
        x0 = cx - w / 2 - (s * 0.16 if name == "connections" else 0)
        rrect(cr, x0, cy - h / 2, w, h, s * 0.12)
        cr.stroke()
        cr.arc(x0 + w / 2, cy + h / 2 - s * 0.16, s * 0.05, 0, 2 * math.pi)
        cr.fill()
        if name == "connections":
            for k in (0.32, 0.56):
                cr.arc(x0 + w * 0.5, cy - s * 0.05, s * (0.42 + k), -0.55, 0.55)
                cr.stroke()
    elif name == "check":
        cr.move_to(cx - s * 0.48, cy + s * 0.02)
        cr.line_to(cx - s * 0.14, cy + s * 0.36)
        cr.line_to(cx + s * 0.5, cy - s * 0.34)
        cr.stroke()
    cr.restore()


def tint_fill(cr, r, tint, a0, a1):
    """Home's RoundNode tint: a radial gradient from the upper left, over the current clip."""
    tr, tg, tb = tint
    g = cairo.RadialGradient(-r * 0.3, -r * 0.4, 0, -r * 0.3, -r * 0.4, r * 1.3)
    g.add_color_stop_rgba(0, tr, tg, tb, a0)
    g.add_color_stop_rgba(1, tr, tg, tb, a1)
    cr.set_source(g)
    cr.paint()


def draw_badge(cr, base, widget, src, look, r, name, tint, f, lit=0.0):
    """The glass node body at the origin: glow, frost, tint (stronger when lit), hairline, glyph."""
    shape = look.node_shape
    cr.set_line_join(cairo.LINE_JOIN_ROUND)
    if look.effects:
        draw_glow(cr, f, lambda c, g: poly_path(c, poly_points(shape, r + g)))
    poly_path(cr, poly_points(shape, r))
    cr.save()
    cr.clip()
    paint_frost(cr, base, widget, src)
    cr.set_source_rgba(1, 1, 1, 0.10 + 0.05 * f)
    cr.paint()
    tint_fill(cr, r, tint or ACCENT, 0.16 + 0.34 * lit, 0.03 + 0.09 * lit)
    cr.restore()
    poly_path(cr, poly_points(shape, r - 0.6))
    cr.set_source_rgba(1, 1, 1, max(0.24 + 0.7 * f, 0.42 * lit))
    cr.set_line_width(1)
    cr.stroke()
    if name:
        glyph(cr, name, 0, 0, r * 0.46, 0.62 + 0.3 * max(f, lit))


def draw_toggle(cr, shape, cx, cy, r, on, f=0.0, tint=None):
    """Hex toggle: filled tint with a bright rim = on; an empty hairline = off."""
    pts = poly_points(shape, r, cx, cy)
    cr.save()
    cr.set_line_join(cairo.LINE_JOIN_ROUND)
    if on:
        tr, tg, tb = tint or ACCENT
        g = cairo.LinearGradient(cx - r, cy - r, cx + r, cy + r)
        g.add_color_stop_rgba(0, tr, tg, tb, 0.95)
        g.add_color_stop_rgba(1, tr * 0.6, tg * 0.6, tb * 0.7, 0.9)
        poly_path(cr, pts)
        cr.set_source(g)
        cr.fill()
        poly_path(cr, poly_points(shape, r * 0.34, cx, cy))     # the lit core
        cr.set_source_rgba(1, 1, 1, 0.92)
        cr.fill()
        poly_path(cr, poly_points(shape, r - 0.5, cx, cy))
        cr.set_source_rgba(1, 1, 1, 0.75 + 0.25 * f)
    else:
        poly_path(cr, poly_points(shape, r - 0.5, cx, cy))
        cr.set_source_rgba(1, 1, 1, 0.38 + 0.4 * f)
    cr.set_line_width(1.2)
    cr.stroke()
    cr.restore()


def draw_check(cr, shape, cx, cy, r, tint=None):
    """The 'selected' mark: a small filled hexagon with a check."""
    tr, tg, tb = tint or ACCENT
    cr.save()
    poly_path(cr, poly_points(shape, r, cx, cy))
    cr.set_source_rgba(tr * 0.75, tg * 0.75, tb * 0.8, 0.92)
    cr.fill_preserve()
    cr.set_source_rgba(1, 1, 1, 0.85)
    cr.set_line_width(1)
    cr.stroke()
    glyph(cr, "check", cx, cy, r * 0.62, 1.0)
    cr.restore()


def draw_signal(cr, shape, x, y, s, level):
    """Four small glass hexagons rising left to right, standing on baseline y; s = height of the tallest. The
    first `level` are lit, the rest are faint glass. -> the glyph's width."""
    flat = 0.924 if shape == "octagon" else 0.866          # centre-to-flat-bottom, in radii
    gap = max(2.0, s * 0.24)
    cr.save()
    cr.set_line_join(cairo.LINE_JOIN_ROUND)
    for i in range(4):
        r = s / (2 * flat) * (0.58 + 0.42 * i / 3)
        cx, cy = x + r, y - r * flat
        poly_path(cr, poly_points(shape, r, cx, cy))
        cr.set_source_rgba(1, 1, 1, 0.9 if i < level else 0.2)
        cr.fill_preserve()
        cr.set_source_rgba(1, 1, 1, 0.95 if i < level else 0.42)
        cr.set_line_width(1)
        cr.stroke()
        x += 2 * r + gap
    cr.restore()
    return x - gap


def signal_level(net):
    if not net or not net.get("connected"):
        return 0
    if net.get("type") != "wifi":
        return 4
    return max(1, min(4, 1 + int((net.get("signal") or 0) / 25)))


def in_rects(x, y, rects):
    return any(rx <= x <= rx + rw and ry <= y <= ry + rh for rx, ry, rw, rh in rects)


def crosses(a, b, rects, steps=12):
    """Whether the segment a-b passes through any of rects (sampled)."""
    return any(in_rects(a[0] + (b[0] - a[0]) * k / steps, a[1] + (b[1] - a[1]) * k / steps, rects)
               for k in range(steps + 1))


def draw_links(cr, pts, scale, alpha=1.0, n_nodes=0, ks=2, avoid=()):
    """Static constellation: thin lines between near points and tiny dust diamonds (points after n_nodes).
    avoid = [(x, y, w, h)] kept clear of dust and lines (labels, panels)."""
    if avoid:
        pts = pts[:n_nodes] + [q for q in pts[n_nodes:] if not in_rects(q[0], q[1], avoid)]
    maxd = LINE_MAX * scale
    cr.save()
    cr.set_line_cap(cairo.LINE_CAP_ROUND)
    cr.set_line_width(max(1.0, 1.2 * scale))
    for i, j, d in link_pairs(pts, [ks] * len(pts), maxd):
        if avoid and crosses(pts[i], pts[j], avoid):
            continue
        cr.set_source_rgba(1, 1, 1, line_alpha(d, maxd) * alpha)
        cr.move_to(*pts[i])
        cr.line_to(*pts[j])
        cr.stroke()
    cr.set_source_rgba(1, 1, 1, 0.42 * alpha)
    d = 3.0 * scale
    for x, y in pts[n_nodes:]:
        cr.move_to(x, y - d)
        cr.line_to(x + d, y)
        cr.line_to(x, y + d)
        cr.line_to(x - d, y)
        cr.close_path()
        cr.fill()
    cr.restore()


def spaced_attrs(px, spacing):
    attrs = Pango.AttrList()
    if spacing:
        attrs.insert(Pango.attr_letter_spacing_new(int(px * spacing * Pango.SCALE)))
    return attrs


class TextCache:
    """Pango layouts per widget, keyed by everything that shapes them."""

    def __init__(self, widget):
        self.widget, self.cache = widget, {}

    def layout(self, text, px, weight, width, align=Pango.Alignment.LEFT, spacing=0.0, wrap=False):
        key = (text, round(px), weight, round(width), align, spacing, wrap)
        lay = self.cache.get(key)
        if lay is None:
            lay = Pango.Layout.new(self.widget.get_pango_context())
            fd = Pango.FontDescription(FONT)
            fd.set_absolute_size(px * Pango.SCALE)
            fd.set_weight(weight)
            lay.set_font_description(fd)
            lay.set_width(int(max(1, width) * Pango.SCALE))
            if wrap:
                lay.set_wrap(Pango.WrapMode.WORD_CHAR)
            else:
                lay.set_ellipsize(Pango.EllipsizeMode.END)
            lay.set_alignment(align)
            lay.set_attributes(spaced_attrs(px, spacing))
            lay.set_text(text, -1)
            if len(self.cache) > 16:
                self.cache.clear()
            self.cache[key] = lay
        return lay

    def draw(self, cr, text, px, weight, x, y, width, rgba, align=Pango.Alignment.LEFT, spacing=0.0, halo=0.0,
             wrap=False):
        """Draw at (x, y) = top-left; returns the layout's pixel height. halo = Home's soft text shadow."""
        lay = self.layout(text, px, weight, width, align, spacing, wrap)
        if halo > 0:
            cr.set_source_rgba(0, 0, 0, halo * rgba[3])
            for dx, dy in ((-1.5, 0), (1.5, 0), (0, -1.5), (0, 2), (1.5, 2), (-1.5, 2)):
                cr.move_to(x + dx, y + dy)
                PangoCairo.show_layout(cr, lay)
        cr.set_source_rgba(*rgba)
        cr.move_to(x, y)
        PangoCairo.show_layout(cr, lay)
        return lay.get_pixel_size()[1]


# ---------------------------------------------------------------- the backdrop (and frost source)

def mean_luminance(surf, step=2):
    """Mean luminance 0..1 of an RGB24 surface (every `step`-th pixel)."""
    surf.flush()
    data, stride = surf.get_data(), surf.get_stride()
    tot = n = 0
    for y in range(0, surf.get_height(), step):
        row = y * stride
        for x in range(0, surf.get_width(), step):
            o = row + x * 4
            tot += 0.2126 * data[o + 2] + 0.7152 * data[o + 1] + 0.0722 * data[o]
            n += 1
    return tot / n / 255.0 if n else 0.3


def dim_for(lum, target, least, most=0.8):
    """Black overlay alpha that brings luminance `lum` down to `target` (bright photos get more)."""
    if lum <= 0:
        return least
    return max(least, min(most, 1.0 - target / lum))


def soften(surf, up=2):
    """Upscale a small surface by `up` while averaging five half-pixel-shifted copies: chained, this turns Home's
    1/16 photo sample into a smooth blur without the blocky look of one big bilinear stretch."""
    w, h = surf.get_width() * up, surf.get_height() * up
    out = cairo.ImageSurface(cairo.FORMAT_RGB24, w, h)
    c = cairo.Context(out)
    for i, (dx, dy) in enumerate(((0, 0), (0.5, 0.5), (-0.5, 0.5), (0.5, -0.5), (-0.5, -0.5))):
        c.save()
        c.translate(dx * up, dy * up)
        c.scale(up, up)
        c.set_source_surface(surf, 0, 0)
        c.get_source().set_filter(cairo.FILTER_BILINEAR)
        c.get_source().set_extend(cairo.EXTEND_PAD)
        c.paint_with_alpha(1.0 / (i + 1))
        c.restore()
    out.flush()
    return out


class Backdrop(Gtk.DrawingArea):
    """Home's photo, blurred and dimmed, with a faint static constellation. Rendered once per size/photo into a
    client-side surface; every redraw (any dirty region) is one blit. Acts as the frost source of the kit.
    avoid: optional callable -> [(x, y, w, h)] in this widget's coordinates kept free of constellation lines."""
    TARGET = 0.15          # mean luminance of the dimmed backdrop
    PANEL_TARGET = 0.27    # ... and of the frost inside glass panels

    def __init__(self, shell, look, seed=7):
        super().__init__()
        self.shell, self.look, self.seed = shell, look, seed
        self.blur = None            # the Home photo's small blurred copy, snapshotted in snapshot()
        self.soft = None            # that copy, smoothed to 1/4 size: what frost and the backdrop sample
        self.blur_k = 4
        self.avoid = None
        self.lum = 0.3
        self.surf = None
        self.surf_key = None
        self.set_has_window(False)
        self.connect("draw", self.on_draw)
        self.snapshot()

    # PhotoCanvas protocol (paint_frost / QRTile)
    @property
    def transparency(self):
        return self.look.transparency

    def blur_layers(self):
        return [(self.soft, 1.0)] if self.soft is not None and self.look.transparency else []

    def snapshot(self):
        """Take Home's current photo. Kept until the next call so a partly redrawn screen never mixes photos."""
        photo = getattr(self.shell, "photo_canvas", None)
        blur = getattr(photo, "blur_cur", None) if photo is not None else None
        if blur is not self.blur:
            self.blur = blur
            self.soft = soften(soften(blur)) if blur is not None and cairo else None
            self.lum = mean_luminance(blur) if blur is not None and cairo else 0.3
            if self.soft is not None and photo.get_allocated_width() > 1:
                self.blur_k = photo.get_allocated_width() / self.soft.get_width()
            self.queue_draw()

    def dim(self):
        return dim_for(self.lum, self.TARGET, 0.5, 0.85)

    def panel_dim(self):
        """How much a glass panel darkens its frost so white text keeps its contrast on bright photos."""
        return dim_for(self.lum, self.PANEL_TARGET, 0.16, 0.7)

    def set_look(self, look):
        if look.key() != self.look.key():
            self.look = look
            self.queue_draw()

    def render(self, w, h):
        surf = cairo.ImageSurface(cairo.FORMAT_RGB24, w, h)
        cr = cairo.Context(surf)
        s = h / 1080.0
        layers = self.blur_layers()
        if layers:
            k = w / layers[0][0].get_width()
            cr.save()
            cr.scale(k, k)
            cr.set_source_surface(layers[0][0], 0, 0)
            cr.get_source().set_filter(cairo.FILTER_BILINEAR)
            cr.get_source().set_extend(cairo.EXTEND_PAD)
            cr.paint()
            cr.restore()
            cr.set_source_rgba(0.015, 0.025, 0.05, self.dim())
            cr.paint()
        else:
            g = cairo.LinearGradient(0, 0, w, h)
            for stop, r, gg, b in NAVY:
                g.add_color_stop_rgb(stop, r, gg, b)
            cr.set_source(g)
            cr.paint()
        side = cairo.LinearGradient(0, 0, w * 0.4, 0)     # a little deeper behind the rail, like Home's clock side
        side.add_color_stop_rgba(0, 0, 0, 0, 0.28)
        side.add_color_stop_rgba(1, 0, 0, 0, 0)
        cr.set_source(side)
        cr.paint()
        draw_links(cr, dust_points(w, h, self.seed, n=30, margin=0.03), s, alpha=0.55, avoid=self.avoid_rects())
        surf.flush()
        return surf

    def avoid_rects(self):
        return tuple(tuple(round(v) for v in r) for r in (self.avoid() if self.avoid else ()))

    def on_draw(self, _w, cr):
        if cairo is None:
            return False
        w, h = self.get_allocated_width(), self.get_allocated_height()
        key = (w, h, id(self.blur), self.look.transparency, self.avoid_rects())
        if self.surf is None or self.surf_key != key:
            self.surf, self.surf_key = self.render(w, h), key
        cr.set_source_surface(self.surf, 0, 0)
        cr.paint()
        return False


# ---------------------------------------------------------------- containers

def paint_panel(cr, widget, src, look, w, h, s, dim=0.22, tint=0.07, hair=0.22, mask=None):
    """Frosted tile filling (0, 0, w, h) of `widget`, in the look's tile shape. mask = a cairo pattern: paint the
    glass only through it (used to fade scrolled content out under the panel's edges)."""
    base = cr.get_matrix()
    tile_path(cr, look.tile_shape, 0.5, 0.5, w - 1, h - 1, s * 1.6)
    cr.save()
    cr.clip_preserve()
    if mask is not None:
        cr.push_group()
    paint_frost(cr, base, widget, src)
    if look.transparency:
        cr.set_source_rgba(0.015, 0.025, 0.05, dim)
        cr.paint()
    cr.set_source_rgba(1, 1, 1, tint)
    cr.paint()
    if mask is not None:
        cr.pop_group_to_source()
        cr.mask(mask)
    cr.restore()
    cr.set_source_rgba(1, 1, 1, hair)
    cr.set_line_width(1)
    cr.stroke()


class GlassPanel(Gtk.Box):
    """A box painting a frosted tile behind its children. dim=None follows the source's panel_dim()."""

    def __init__(self, shell, src, look, dim=None, fade=0, scroller=None, **kw):
        kw.setdefault("orientation", Gtk.Orientation.VERTICAL)
        super().__init__(**kw)
        self.shell, self.src, self.look, self.dim, self.fade = shell, src, look, dim, fade
        self.scroller = None
        self.set_has_window(False)
        self.connect("draw", self.on_draw)
        if fade:
            self.connect_after("draw", self.on_draw_over)
        if scroller is not None:
            self.watch(scroller)

    def watch(self, scroller):
        """Fade at `scroller`'s edges instead of the panel's, and only on a side where content is cut off."""
        self.scroller = scroller
        adj = scroller.get_vadjustment()
        for sig in ("value-changed", "changed"):
            adj.connect(sig, lambda *_: self.queue_draw())

    def panel_dim(self):
        return self.dim if self.dim is not None else getattr(self.src, "panel_dim", lambda: 0.22)()

    def on_draw(self, _w, cr):
        if cairo is not None:
            paint_panel(cr, self, self.src, self.look, self.get_allocated_width(), self.get_allocated_height(),
                        scale_of(self.shell), dim=self.panel_dim())
        return False

    def fade_bands(self):
        """[(y_edge, direction)] of the edges to fade: direction +1 = content continues above (fade downward
        from a top edge), -1 = continues below. Without a scroller: both panel edges, always."""
        h = self.get_allocated_height()
        sc = self.scroller
        if sc is None:
            return [(0, 1), (h, -1)]
        pos = sc.translate_coordinates(self, 0, 0)
        if not pos:
            return []
        adj = sc.get_vadjustment()
        top, bottom = pos[1], pos[1] + sc.get_allocated_height()
        out = []
        if adj.get_value() > 0.5:
            out.append((top, 1))
        if adj.get_value() + adj.get_page_size() < adj.get_upper() - 0.5:
            out.append((bottom, -1))
        return out

    def on_draw_over(self, _w, cr):
        """fade = px: repaint the glass over the children at the edges where content is cut: fully opaque for
        `fade` px (anything sliced by the edge disappears, never a half letter), then fading out over `fade` px
        more, so the rest melts into the glass."""
        if cairo is None:
            return False
        w, h, f = self.get_allocated_width(), self.get_allocated_height(), self.fade
        for edge, d in self.fade_bands():
            y0, y1 = (edge, edge + 2 * f) if d > 0 else (edge - 2 * f, edge)
            g = cairo.LinearGradient(0, edge, 0, edge + d * 2 * f)
            g.add_color_stop_rgba(0, 0, 0, 0, 1)
            g.add_color_stop_rgba(0.5, 0, 0, 0, 1)
            g.add_color_stop_rgba(1, 0, 0, 0, 0)
            cr.save()
            cr.rectangle(0, y0, w, y1 - y0)       # only the band: rows redrawn mid-panel cost nothing extra
            cr.clip()
            paint_panel(cr, self, self.src, self.look, w, h, scale_of(self.shell), dim=self.panel_dim(), mask=g)
            cr.restore()
        return False


class GlassField(Gtk.Box):
    """A glass well around an entry (make the entry itself transparent with CSS)."""

    def __init__(self, shell, src, look, child):
        super().__init__()
        self.shell, self.src, self.look, self.child = shell, src, look, child
        self.set_has_window(False)
        p = shell.px
        child.set_margin_start(p(22))
        child.set_margin_end(p(22))
        self.pack_start(child, True, True, 0)
        self.connect("draw", self.on_draw)
        for sig in ("focus-in-event", "focus-out-event"):
            child.connect(sig, lambda *_: self.queue_draw() or False)

    def on_draw(self, _w, cr):
        if cairo is None:
            return False
        w, h = self.get_allocated_width(), self.get_allocated_height()
        f = 1.0 if self.child.has_focus() else 0.0
        if self.look.effects and f:
            draw_glow(cr, f, lambda c, g: tile_path(c, self.look.tile_shape, 2 - g, 2 - g, w - 4 + 2 * g,
                                                   h - 4 + 2 * g, scale_of(self.shell), g))
        tile_path(cr, self.look.tile_shape, 0.5, 0.5, w - 1, h - 1, scale_of(self.shell))
        cr.set_source_rgba(0, 0, 0, 0.18)
        cr.fill_preserve()
        cr.set_source_rgba(1, 1, 1, 0.22 + 0.68 * f)
        cr.set_line_width(1)
        cr.stroke()
        return False


# ---------------------------------------------------------------- focus easing

class _Ticker:
    """One shared <= 15 fps timer for every widget that is still easing; idle otherwise."""

    def __init__(self):
        self.live, self.src, self.last = set(), 0, 0.0

    def add(self, w):
        self.live.add(w)
        if not self.src:
            self.last = GLib.get_monotonic_time() / 1e6
            self.src = GLib.timeout_add(FRAME_MS, self.step)

    def step(self):
        now = GLib.get_monotonic_time() / 1e6
        dt, self.last = now - self.last, now
        for w in list(self.live):
            if w.dead or not w.step(dt):
                self.live.discard(w)
        if not self.live:
            self.src = 0
            return False
        return True


TICKER = _Ticker()


class Focusable(Gtk.DrawingArea):
    """Windowless, focusable drawn control. Subclasses implement paint(cr, base, f, w, h).
    f = focus amount 0..1 (eased while effects are on, 0/1 otherwise)."""
    rate = 10.0

    def __init__(self, shell, src, look, on_activate=None, key=None, width=-1, height=-1):
        super().__init__()
        self.shell, self.src, self.look = shell, src, look
        self.on_activate = on_activate or (lambda: None)
        self.sel_key = key
        self.f = 0.0
        self.dead = False
        self.text = TextCache(self)
        self.set_has_window(False)
        self.set_can_focus(True)
        self.set_size_request(width, height)
        self.connect("draw", self.on_draw)
        self.connect("focus-in-event", self.on_focus)
        self.connect("focus-out-event", self.on_focus)
        self.connect("key-press-event", self.on_key)
        self.connect("destroy", lambda *_: setattr(self, "dead", True))

    def target(self):
        return 1.0 if self.has_focus() else 0.0

    def on_focus(self, *_):
        if self.look.effects:
            TICKER.add(self)
        else:
            self.f = self.target()
        self.queue_draw()
        return False

    def step(self, dt):
        t = self.target()
        self.f = ease(self.f, t, dt, self.rate)
        if abs(self.f - t) < 0.02:
            self.f = t
        self.queue_draw()
        return self.f != t

    def on_key(self, _w, ev):
        if Gdk.keyval_name(ev.keyval) in ("Return", "KP_Enter", "space"):
            self.press()
            return True
        return False

    def press(self):
        if self.is_sensitive():
            self.on_activate()

    def set_sensitive(self, on):
        Gtk.DrawingArea.set_sensitive(self, on)
        self.queue_draw()

    def on_draw(self, _w, cr):
        if cairo is None:
            return False
        f = self.f if self.look.effects else float(self.has_focus())
        self.paint(cr, cr.get_matrix(), f, self.get_allocated_width(), self.get_allocated_height())
        return False

    def paint(self, cr, base, f, w, h):
        raise NotImplementedError

    def focus_tile(self, cr, f, x, y, w, h, sc=1.0, fill=0.11):
        """Home's focus on a tile-shaped area: scale about its centre, glow, lighter glass, bright hairline.
        Draws nothing at f == 0 (rows are borderless at rest)."""
        if f <= 0.003:
            return
        s = scale_of(self.shell)
        cx, cy = x + w / 2, y + h / 2
        cr.save()
        cr.translate(cx, cy)
        cr.scale(sc, sc)
        x0, y0 = -w / 2, -h / 2
        shape = self.look.tile_shape
        if self.look.effects:
            draw_glow(cr, f, lambda c, g: tile_path(c, shape, x0 - g, y0 - g, w + 2 * g, h + 2 * g, s, g))
        tile_path(cr, shape, x0, y0, w, h, s)
        cr.set_source_rgba(1, 1, 1, fill * f)
        cr.fill()
        tile_path(cr, shape, x0 + 0.5, y0 + 0.5, w - 1, h - 1, s)
        cr.set_source_rgba(1, 1, 1, 0.9 * f)
        cr.set_line_width(1)
        cr.stroke()
        cr.restore()


# ---------------------------------------------------------------- controls

class GlassRow(Focusable):
    """A borderless row: title (+ sub) on the left; on the right a value in a quieter weight, a hex toggle
    (toggle=True/False) or a selected check (current=True). Focus = Home's tile focus."""
    SCALE = 1.012

    def __init__(self, shell, src, look, title, sub="", value="", toggle=None, current=False, on_activate=None,
                 key=None, tint=None, height=None):
        p = shell.px
        self.pad_x, self.pad_y = p(18), p(10)
        h = height or (p(92) if sub else p(76))
        super().__init__(shell, src, look, on_activate, key, -1, h + 2 * self.pad_y)
        self.title, self.sub, self.value, self.toggle, self.current = title, sub, value, toggle, current
        self.tint = tint

    def set_value(self, value=None, toggle=None):
        self.value, self.toggle = value if value is not None else self.value, toggle
        self.queue_draw()

    def paint(self, cr, base, f, w, h):
        p, s = self.shell.px, scale_of(self.shell)
        on = self.is_sensitive()
        x, y, bw, bh = self.pad_x, self.pad_y, w - 2 * self.pad_x, h - 2 * self.pad_y
        self.focus_tile(cr, f, x, y, bw, bh, 1 + (self.SCALE - 1) * f)
        k = 1.0 if on else 0.42
        ix, right = x + p(28), x + bw - p(28)
        if self.toggle is not None:
            r = p(13)
            draw_toggle(cr, self.look.node_shape, right - r, y + bh / 2, r, self.toggle and on, f, self.tint)
            right -= 2 * r + p(16)
            vt = "On" if self.toggle else "Off"
        elif self.current:
            r = p(13)
            draw_check(cr, self.look.node_shape, right - r, y + bh / 2, r, self.tint)
            right -= 2 * r + p(16)
            vt = self.value
        else:
            vt = self.value
        vw = 0
        if vt:
            lay = self.text.layout(vt, p(21), Pango.Weight.NORMAL, max(p(80), bw * 0.42), Pango.Alignment.RIGHT)
            vw = min(lay.get_pixel_size()[0], bw * 0.42)
            lh = lay.get_pixel_size()[1]
            self.text.draw(cr, vt, p(21), Pango.Weight.NORMAL, right - bw * 0.42, y + (bh - lh) / 2, bw * 0.42,
                           (1, 1, 1, (0.56 + 0.3 * f) * k), Pango.Alignment.RIGHT)
        tw = max(p(60), right - vw - p(24) - ix)
        title_px, sub_px = p(24), p(18)
        th = title_px * 1.25 + (sub_px * 1.4 if self.sub else 0)
        ty = y + (bh - th) / 2
        self.text.draw(cr, self.title, title_px, Pango.Weight.NORMAL, ix, ty, tw, (1, 1, 1, (0.84 + 0.16 * f) * k))
        if self.sub:
            self.text.draw(cr, self.sub, sub_px, Pango.Weight.NORMAL, ix, ty + title_px * 1.32, tw,
                           (1, 1, 1, (0.5 + 0.2 * f) * k))


class RailNode(Focusable):
    """One category on a vertical rail: a glass badge (node shape) with a glyph and a label to its right.
    selected = the category on show (lit tint, brighter label); focus = Home's node focus."""
    FOCUS_SCALE = 1.22

    def __init__(self, shell, src, look, title, glyph_name, tint, on_activate=None, key=None, indent=0):
        p = shell.px
        self.r = p(27)
        self.indent = indent
        super().__init__(shell, src, look, on_activate, key, p(330), p(92))
        self.title, self.glyph, self.tint = title, glyph_name, tint
        self.selected = False

    def set_selected(self, on):
        if on != self.selected:
            self.selected = on
            self.queue_draw()

    def centre(self):
        p = self.shell.px
        return p(30) + self.indent + self.r, self.get_allocated_height() / 2

    def inside(self, x, y):
        cx, cy = self.centre()
        return point_in_poly(x - cx, y - cy, poly_points(self.look.node_shape, self.r))

    def paint(self, cr, base, f, w, h):
        p = self.shell.px
        cx, cy = self.centre()
        lit = 1.0 if self.selected else 0.0
        cr.save()
        cr.translate(cx, cy)
        sc = 1 + (self.FOCUS_SCALE - 1) * f if self.look.effects else 1.0
        cr.scale(sc, sc)
        draw_badge(cr, base, self, self.src, self.look, self.r, self.glyph, self.tint, f, lit)
        cr.restore()
        lx = cx + self.r * 1.22 + p(22)
        a = 0.6 + 0.3 * lit + 0.1 * f
        px = p(24)
        lh = self.text.layout(self.title, px, Pango.Weight.NORMAL, w - lx).get_pixel_size()[1]
        self.text.draw(cr, self.title, px, Pango.Weight.MEDIUM if (lit or f > 0.5) else Pango.Weight.NORMAL,
                       lx, cy - lh / 2, w - lx, (1, 1, 1, min(1.0, a)), halo=0.35)


class PictureTile(Focusable):
    """A frosted tile showing a picture (Home's TileNode look); title/sub inside at the bottom left; a hex check
    in the corner when selected."""
    FOCUS_SCALE = 1.06

    def __init__(self, shell, src, look, title, sub, surface, selected, on_activate=None, key=None,
                 width=420, height=236):
        p = shell.px
        self.tw, self.th = p(width), p(height)
        self.pad = p(22)
        super().__init__(shell, src, look, on_activate, key, self.tw + 2 * self.pad, self.th + 2 * self.pad)
        self.title, self.sub, self.art, self.selected = title, sub, surface, selected

    def paint(self, cr, base, f, w, h):
        p, s = self.shell.px, scale_of(self.shell)
        tw, th = self.tw, self.th
        sc = 1 + (self.FOCUS_SCALE - 1) * f if self.look.effects else 1.0
        shape = self.look.tile_shape
        cr.save()
        cr.translate(w / 2, h / 2)
        cr.scale(sc, sc)
        x, y = -tw / 2, -th / 2
        if self.look.effects:
            draw_glow(cr, f, lambda c, g: tile_path(c, shape, x - g, y - g, tw + 2 * g, th + 2 * g, s, g))
        tile_path(cr, shape, x, y, tw, th, s)
        cr.save()
        cr.clip()
        paint_frost(cr, base, self, self.src)
        cr.set_source_rgba(1, 1, 1, 0.10 + 0.04 * f)
        cr.paint()
        if self.art is not None:
            cr.save()
            cr.translate(x, y)
            cr.scale(tw / self.art.get_width(), th / self.art.get_height())
            cr.set_source_surface(self.art, 0, 0)
            cr.paint_with_alpha(0.8 + 0.2 * f)
            cr.restore()
        shade = cairo.LinearGradient(0, y + th * 0.4, 0, y + th)
        shade.add_color_stop_rgba(0, 0, 0, 0, 0)
        shade.add_color_stop_rgba(1, 0, 0, 0, 0.66)
        cr.set_source(shade)
        cr.paint()
        cr.restore()
        tile_path(cr, shape, x + 0.5, y + 0.5, tw - 1, th - 1, s)
        cr.set_source_rgba(1, 1, 1, 0.22 + 0.7 * f)
        cr.set_line_width(1)
        cr.stroke()
        if self.selected:
            r = p(15)
            draw_check(cr, self.look.node_shape, x + tw - p(16) - r, y + p(16) + r, r)
        tx, inner = x + p(18), tw - p(36)
        sub_px, title_px = p(16), p(22)
        ty = y + th - p(16) - sub_px * 1.35 - title_px * 1.3
        self.text.draw(cr, self.title, title_px, Pango.Weight.SEMIBOLD, tx, ty, inner, (1, 1, 1, 0.9 + 0.1 * f),
                       halo=0.3)
        self.text.draw(cr, self.sub, sub_px, Pango.Weight.NORMAL, tx, ty + title_px * 1.35, inner,
                       (1, 1, 1, 0.66 + 0.2 * f), halo=0.3)
        cr.restore()


class SectionLabel(Gtk.DrawingArea):
    """Small letter-spaced caps behind a tiny hexagon marker."""

    def __init__(self, shell, text, look, top=28):
        super().__init__()
        self.shell, self.label, self.look = shell, text.upper(), look
        self.text = TextCache(self)
        p = shell.px
        self.set_size_request(-1, p(top + 30))
        self.top = p(top)
        self.set_has_window(False)
        self.connect("draw", self.on_draw)

    def on_draw(self, _w, cr):
        p = self.shell.px
        w, h = self.get_allocated_width(), self.get_allocated_height()
        px = p(15)
        cy = self.top + px * 0.62
        x = p(46)
        poly_path(cr, poly_points(self.look.node_shape, p(5), x, cy))
        cr.set_source_rgba(1, 1, 1, 0.55)
        cr.set_line_width(1)
        cr.stroke()
        self.text.draw(cr, self.label, px, Pango.Weight.SEMIBOLD, x + p(16), self.top, w - x, (1, 1, 1, 0.56),
                       spacing=0.16)
        return False


class SignalLine(Gtk.DrawingArea):
    """The connection line without emoji: a hex signal glyph and quiet 'name · ip' text."""

    def __init__(self, shell, look):
        super().__init__()
        self.shell, self.look, self.net = shell, look, {}
        self.text = TextCache(self)
        self.set_size_request(-1, shell.px(40))
        self.set_has_window(False)
        self.connect("draw", self.on_draw)

    def set_net(self, net):
        self.net = net or {}
        self.queue_draw()

    def line(self):
        net = self.net
        if not net.get("connected"):
            name = "Offline"
        elif net.get("type") == "wifi":
            name = net.get("ssid") or "Wi-Fi"
        elif net.get("type") == "ethernet":
            name = "Wired"
        else:
            name = "Online"
        ip = net.get("ip")
        return "%s  ·  %s" % (name, ip) if ip and net.get("connected") else name

    def on_draw(self, _w, cr):
        p = self.shell.px
        w, h = self.get_allocated_width(), self.get_allocated_height()
        tx = p(92)
        lay = self.text.layout(self.line(), p(20), Pango.Weight.NORMAL, w - tx)
        ty = (h - lay.get_pixel_size()[1]) / 2
        base = ty + lay.get_baseline() / Pango.SCALE           # the glyph stands on the text's baseline
        draw_signal(cr, self.look.node_shape, 0, base, p(18), signal_level(self.net))
        self.text.draw(cr, self.line(), p(20), Pango.Weight.NORMAL, tx, ty, w - tx, (1, 1, 1, 0.72))
        return False


class RailLinks(Gtk.Box):
    """A vertical box of RailNodes joined by thin constellation lines (drawn behind them); the links of the
    selected node are brighter."""

    def __init__(self, shell, **kw):
        kw.setdefault("orientation", Gtk.Orientation.VERTICAL)
        super().__init__(**kw)
        self.shell = shell
        self.nodes = []
        self.set_has_window(False)
        self.connect("draw", self.on_draw)

    def add_node(self, node):
        self.nodes.append(node)
        self.pack_start(node, False, False, 0)

    def on_draw(self, _w, cr):
        if cairo is None or len(self.nodes) < 2:
            return False
        s = scale_of(self.shell)
        pts = []
        for n in self.nodes:
            cx, cy = n.centre()
            pos = n.translate_coordinates(self, int(cx), int(cy))
            if pos:
                pts.append((pos[0], pos[1], n.selected))
        cr.set_line_cap(cairo.LINE_CAP_ROUND)
        for (x0, y0, a), (x1, y1, b) in zip(pts, pts[1:]):
            r = self.nodes[0].r * 1.05          # stop at the badges' rims
            d = math.hypot(x1 - x0, y1 - y0) or 1
            ux, uy = (x1 - x0) / d, (y1 - y0) / d
            cr.move_to(x0 + ux * r, y0 + uy * r)
            cr.line_to(x1 - ux * r, y1 - uy * r)
            cr.set_source_rgba(1, 1, 1, 0.55 if (a or b) else 0.24)
            cr.set_line_width(max(1.2, 1.3 * s) * (1.5 if (a or b) else 1))
            cr.stroke()
        return False
