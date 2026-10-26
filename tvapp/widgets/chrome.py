"""Home's glass language for the rest of the TV app: the header, the media screens and the stream loading screen.
Built on widgets/glass.py (Look, Focusable, TextCache, draw_badge, draw_signal, ...) and network.py's shapes; like
the kit, every piece is cairo-drawn because GTK3 CSS cannot draw hexagons or frost.

API (sizes are 1080p px; pieces scale with shell.px)
  look(shell) / refresh_look(shell)
                         one glass.Look per shell, shared by every piece here and updated in place when home.json
                         changes (so a Settings toggle reaches cards that already exist)
  SharedBackdrop(shell, look)
                         glass.Backdrop whose rendered surface and smoothed photo are shared by every screen
                         (one 1080p surface instead of one per screen)
  ArtLayer(shell, look, w, h, fade_bottom, under=None)
                         title art, crisp, fading out to the left and bottom so the backdrop shows through; rendered
                         once per picture; also a frost source (blur of backdrop + art) for glass on top of it
  PosterCard(shell, look, item, kind, art_w, art_h, on_activate)
                         poster / wide / episode card: art in tile_shape, Home's focus (scale, 22% -> 90% hairline,
                         glow), quiet title + sub below, a thin progress line
  GlassButton(shell, src, look, label, glyph=None, primary=False, sub="", width=None)
                         glass tile with a hex-framed glyph: Details' Play / Sources, season chips, retry cards
  RailTitle(shell, look, text) / Kicker(shell, look)
                         rail heading behind a small hexagon; letter-spaced small-caps kicker with set_text()
  SourceRow(shell, look, src, best, first, last, on_activate)
                         borderless glass row with a constellation marker column; the best one is lit
  HexSpinner(shell, look)  small busy hexagon (a point orbiting the outline), runs only while mapped
  Header pieces: Wordmark, NavTab (current = lit hexagon marker), PhoneChip, NetLabel, HeaderClock, PowerButton
  LoadingView(shell, look)
                         the stream loading screen: blurred title art (or Home's backdrop), the poster in a large
                         glass hexagon, a light point orbiting its rim with a fading trail at <= 15 fps that redraws
                         only the hexagon's box; with Visual effects off a stepping vertex tick at 2 fps
  orbit_point(pts, u)    point at fraction u of a closed polygon's perimeter

Calm cost is zero: cards and the header use the kit's shared focus ticker; only LoadingView and HexSpinner own a
timer, and only while they are on screen."""
import math
import os
import sys
from collections import OrderedDict
from pathlib import Path

import gi
gi.require_version("Gtk", "3.0")
gi.require_version("Pango", "1.0")
gi.require_version("PangoCairo", "1.0")
from gi.repository import Gtk, Gdk, GdkPixbuf, GLib, Pango, PangoCairo

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from . import glass  # noqa: E402
from .glass import (Look, Focusable, TextCache, tile_path, draw_badge, draw_signal, signal_level, draw_links,  # noqa: E402
                    soften, mean_luminance, dim_for, scale_of, ACCENT, NAVY, FONT)
from .network import poly_points, poly_path, draw_glow, dust_points, paint_frost  # noqa: E402

try:
    import cairo
except Exception:  # pragma: no cover
    cairo = None

DATA = Path(os.environ.get("IMACTV_DATA_DIR", str(Path.home() / ".local/share/imactv")))
FRAME_MS = 67          # <= 15 fps
TICK_MS = 500          # Visual effects off: the loading tick steps twice a second


# ---------------------------------------------------------------- the shared look

def _config():
    try:
        import widgets
        return widgets.load_config(DATA)
    except Exception:
        return {}


def look(shell):
    lk = getattr(shell, "chrome_look", None)
    if lk is None:
        lk = Look(_config())
        try:
            shell.chrome_look = lk
        except Exception:
            pass
    return lk


def refresh_look(shell, cfg=None):
    """Re-read home.json into the shell's shared Look, in place. -> True when anything changed."""
    lk = look(shell)
    new = Look(cfg if cfg is not None else _config())
    if new.key() == lk.key():
        return False
    lk.node_shape, lk.tile_shape, lk.transparency, lk.effects = new.key()
    return True


# ---------------------------------------------------------------- small drawings

def glyph(cr, name, cx, cy, s, alpha=0.92):
    """glass.glyph plus the few this module needs: play, list, power."""
    if name not in ("play", "list", "power", "retry"):
        glass.glyph(cr, name, cx, cy, s, alpha)
        return
    cr.save()
    cr.set_line_width(max(1.6, s * 0.12))
    cr.set_line_cap(cairo.LINE_CAP_ROUND)
    cr.set_line_join(cairo.LINE_JOIN_ROUND)
    cr.set_source_rgba(1, 1, 1, alpha)
    cr.new_path()
    if name == "play":
        t = s * 0.46
        cr.move_to(cx - t * 0.62, cy - t)
        cr.line_to(cx + t * 0.95, cy)
        cr.line_to(cx - t * 0.62, cy + t)
        cr.close_path()
        cr.fill()
    elif name == "list":
        for k in (-1, 0, 1):
            y = cy + k * s * 0.36
            cr.arc(cx - s * 0.46, y, s * 0.07, 0, 2 * math.pi)
            cr.fill()
            cr.move_to(cx - s * 0.22, y)
            cr.line_to(cx + s * 0.52, y)
            cr.stroke()
    elif name == "power":
        cr.arc(cx, cy + s * 0.06, s * 0.5, -math.pi / 2 + 0.75, -math.pi / 2 - 0.75 + 2 * math.pi)
        cr.stroke()
        cr.move_to(cx, cy - s * 0.58)
        cr.line_to(cx, cy - s * 0.02)
        cr.stroke()
    elif name == "retry":
        cr.arc(cx, cy, s * 0.5, -math.pi * 0.35, math.pi * 1.45)
        cr.stroke()
        x, y = cx + s * 0.5 * math.cos(-math.pi * 0.35), cy + s * 0.5 * math.sin(-math.pi * 0.35)
        cr.move_to(x - s * 0.26, y - s * 0.04)
        cr.line_to(x + s * 0.02, y + s * 0.02)
        cr.line_to(x + s * 0.02, y - s * 0.28)
        cr.stroke()
    cr.restore()


def hex_marker(cr, shape, cx, cy, r, lit=1.0, alpha=1.0):
    """The small marker hexagon: lit = filled accent with a white core; unlit = a hairline outline."""
    pts = poly_points(shape, r, cx, cy)
    if lit > 0:
        tr, tg, tb = ACCENT
        poly_path(cr, pts)
        cr.set_source_rgba(tr, tg, tb, 0.9 * lit * alpha)
        cr.fill_preserve()
        cr.set_source_rgba(1, 1, 1, (0.5 + 0.45 * lit) * alpha)
        cr.set_line_width(1)
        cr.stroke()
    else:
        poly_path(cr, pts)
        cr.set_source_rgba(1, 1, 1, 0.5 * alpha)
        cr.set_line_width(1)
        cr.stroke()


def orbit_point(pts, u):
    """The point at fraction u (wrapped to 0..1) of the closed polygon pts' perimeter."""
    n = len(pts)
    u = u % 1.0
    k = u * n
    i = int(k) % n
    f = k - int(k)
    (x0, y0), (x1, y1) = pts[i], pts[(i + 1) % n]
    return x0 + (x1 - x0) * f, y0 + (y1 - y0) * f


def focus_line(look_, f):
    """The hairline's width: 1 px; 2 px on the focused piece when Visual effects is off (no scale, no glow)."""
    return 1.0 if look_.effects else 1.0 + f


def spill(widget, mx, my=None):
    """Let a drawn widget paint its focus glow mx / my px past its allocation (GTK clips to the allocation)."""
    my = mx if my is None else my

    def grow(_w, alloc):
        r = Gdk.Rectangle()
        r.x, r.y, r.width, r.height = alloc.x - mx, alloc.y - my, alloc.width + 2 * mx, alloc.height + 2 * my
        widget.set_clip(r)
    widget.connect("size-allocate", grow)


def pixbuf_surface(pb):
    """A GdkPixbuf as a cairo ImageSurface (one copy)."""
    w, h = pb.get_width(), pb.get_height()
    surf = cairo.ImageSurface(cairo.FORMAT_ARGB32, w, h)
    cr = cairo.Context(surf)
    Gdk.cairo_set_source_pixbuf(cr, pb, 0, 0)
    cr.paint()
    surf.flush()
    return surf


def small_copy(src, w, h, tw=120):
    """A tiny RGB24 copy of a surface (box-filtered) for blurring; tw = target width."""
    k = tw / float(w)
    th = max(2, round(h * k))
    out = cairo.ImageSurface(cairo.FORMAT_RGB24, tw, th)
    cr = cairo.Context(out)
    cr.set_source_rgb(*NAVY[1][1:])
    cr.paint()
    cr.scale(k, k)
    cr.set_source_surface(src, 0, 0)
    cr.get_source().set_filter(cairo.FILTER_GOOD)
    cr.paint()
    out.flush()
    return out


def paint_backdrop(cr, w, h, soft, lum, look_, seed=7, avoid=(), target=0.15, least=0.5, most=0.85, dust=0.55):
    """glass.Backdrop.render's picture, for any smoothed source: blurred photo dimmed to `target` luminance (navy
    when there is none or transparency is off), a deeper left side and a faint still constellation."""
    if soft is not None and look_.transparency:
        cr.save()
        cr.scale(w / soft.get_width(), h / soft.get_height())
        cr.set_source_surface(soft, 0, 0)
        cr.get_source().set_filter(cairo.FILTER_BILINEAR)
        cr.get_source().set_extend(cairo.EXTEND_PAD)
        cr.paint()
        cr.restore()
        cr.set_source_rgba(0.015, 0.025, 0.05, dim_for(lum, target, least, most))
        cr.paint()
    else:
        g = cairo.LinearGradient(0, 0, w, h)
        for stop, r, gg, b in NAVY:
            g.add_color_stop_rgb(stop, r, gg, b)
        cr.set_source(g)
        cr.paint()
    side = cairo.LinearGradient(0, 0, w * 0.4, 0)
    side.add_color_stop_rgba(0, 0, 0, 0, 0.28)
    side.add_color_stop_rgba(1, 0, 0, 0, 0)
    cr.set_source(side)
    cr.paint()
    if dust:
        draw_links(cr, dust_points(w, h, seed, n=30, margin=0.03), h / 1080.0, alpha=dust, avoid=avoid)


# ---------------------------------------------------------------- the shared backdrop

class SharedBackdrop(glass.Backdrop):
    """glass.Backdrop with its smoothed photo and rendered surface shared between instances: Movies, Series,
    Search, Details and Sources all show the same picture, so it is rendered (and held) once."""
    _soft = {}                      # id(blur) -> (blur, soft, lum)
    _surfs = OrderedDict()          # key -> (blur, surface)

    def snapshot(self):
        photo = getattr(self.shell, "photo_canvas", None)
        blur = getattr(photo, "blur_cur", None) if photo is not None else None
        if blur is self.blur and (blur is None or self.soft is not None):
            return
        self.blur = blur
        if blur is None or cairo is None:
            self.soft, self.lum = None, 0.3
        else:
            ent = SharedBackdrop._soft.get(id(blur))
            if ent is None or ent[0] is not blur:
                ent = (blur, soften(soften(blur)), mean_luminance(blur))
                SharedBackdrop._soft.clear()
                SharedBackdrop._soft[id(blur)] = ent
            self.soft, self.lum = ent[1], ent[2]
            if photo.get_allocated_width() > 1:
                self.blur_k = photo.get_allocated_width() / self.soft.get_width()
        self.queue_draw()

    def surface(self, w, h):
        key = (w, h, id(self.blur), self.look.transparency, self.seed, self.avoid_rects())
        ent = SharedBackdrop._surfs.get(key)
        if ent is None or ent[0] is not self.blur:
            ent = (self.blur, self.render(w, h))
            SharedBackdrop._surfs[key] = ent
            while len(SharedBackdrop._surfs) > 3:
                SharedBackdrop._surfs.popitem(last=False)
        else:
            SharedBackdrop._surfs.move_to_end(key)
        return ent[1]

    def render(self, w, h):
        surf = cairo.ImageSurface(cairo.FORMAT_RGB24, w, h)
        paint_backdrop(cairo.Context(surf), w, h, self.soft, self.lum, self.look, self.seed, self.avoid_rects())
        surf.flush()
        return surf

    def on_draw(self, _w, cr):
        if cairo is None:
            return False
        w, h = self.get_allocated_width(), self.get_allocated_height()
        if w > 1 and h > 1:
            cr.set_source_surface(self.surface(w, h), 0, 0)
            cr.paint()
        return False


# ---------------------------------------------------------------- title art

class ArtLayer(Gtk.DrawingArea):
    """Title art drawn crisp at its natural size, fading out to the left (behind the text) and over the bottom
    `fade_bottom` px, so whatever is under it (the shared backdrop) shows through. Rendered once per picture into
    an ARGB surface; a redraw is one blit. Also a frost source for glass laid over it: a blur of `under` (the
    backdrop) with the faded art on top, so frosted buttons match what is behind them."""

    def __init__(self, shell, look_, width, height, fade_bottom, under=None, left=(0.0, 0.3, 0.62)):
        super().__init__()
        self.shell, self.look, self.under = shell, look_, under
        self.w, self.h, self.fade_bottom, self.left = width, height, fade_bottom, left
        self.pb = None
        self.surf = None
        self.soft = None
        self.blur_k = 4
        self.set_size_request(width, height)
        self.set_has_window(False)
        self.connect("draw", self.on_draw)

    @property
    def transparency(self):
        return self.look.transparency

    def set_pixbuf(self, pb):
        if pb is self.pb:
            return
        self.pb = pb
        self.surf = self.render() if pb is not None and cairo else None
        self.soft = None
        self.queue_draw()

    def clear(self):
        self.set_pixbuf(None)

    def fade_mask(self):
        """The left + bottom fade as one A8 mask, built once per size."""
        key = (self.w, self.h, self.fade_bottom, self.left)
        if getattr(self, "mask_key", None) != key:
            w, h = self.w, self.h
            m = cairo.ImageSurface(cairo.FORMAT_A8, w, h)
            cr = cairo.Context(m)
            a, b, c = self.left
            g = cairo.LinearGradient(0, 0, w, 0)
            g.add_color_stop_rgba(a, 0, 0, 0, 0.0)
            g.add_color_stop_rgba(b, 0, 0, 0, 0.2)
            g.add_color_stop_rgba(c, 0, 0, 0, 0.8)
            g.add_color_stop_rgba(1, 0, 0, 0, 1.0)
            cr.set_source(g)
            cr.paint()
            if self.fade_bottom:
                cr.set_operator(cairo.OPERATOR_DEST_IN)
                v = cairo.LinearGradient(0, h - self.fade_bottom, 0, h)
                v.add_color_stop_rgba(0, 0, 0, 0, 1)
                v.add_color_stop_rgba(0.55, 0, 0, 0, 0.35)
                v.add_color_stop_rgba(1, 0, 0, 0, 0)
                cr.set_source(v)
                cr.paint()
            m.flush()
            self.mask, self.mask_key = m, key
        return self.mask

    def render(self):
        """The picture through the fade mask: one pass per new picture."""
        w, h = self.w, self.h
        surf = cairo.ImageSurface(cairo.FORMAT_ARGB32, w, h)
        cr = cairo.Context(surf)
        pw, ph = self.pb.get_width(), self.pb.get_height()
        Gdk.cairo_set_source_pixbuf(cr, self.pb, (w - pw) / 2, (h - ph) / 2)
        cr.mask_surface(self.fade_mask(), 0, 0)
        surf.flush()
        return surf

    def blur_layers(self):
        if not self.look.transparency:
            return []
        if self.soft is None:
            self.soft = self.make_soft()
        return [(self.soft, 1.0)] if self.soft is not None else []

    def make_soft(self):
        if cairo is None:
            return None
        w, h = self.get_allocated_width(), self.get_allocated_height()
        if w < 2 or h < 2:
            w, h = self.w, self.h
        comp = cairo.ImageSurface(cairo.FORMAT_RGB24, w, h)
        cr = cairo.Context(comp)
        cr.set_source_rgb(*NAVY[1][1:])
        cr.paint()
        under = self.under
        if under is not None and hasattr(under, "surface"):
            pos = self.translate_coordinates(under, 0, 0) or (0, 0)
            uw, uh = under.get_allocated_width(), under.get_allocated_height()
            if uw > 1 and uh > 1:
                cr.set_source_surface(under.surface(uw, uh), -pos[0], -pos[1])
                cr.paint()
        if self.surf is not None:
            cr.set_source_surface(self.surf, 0, 0)
            cr.paint()
        comp.flush()
        small = small_copy(comp, w, h, 120)
        soft = soften(soften(small))
        self.blur_k = w / soft.get_width()
        return soft

    def on_draw(self, _w, cr):
        if self.surf is not None:
            w = self.get_allocated_width()
            cr.set_source_surface(self.surf, (w - self.w) / 2 if w > self.w else 0, 0)
            cr.paint()
        return False


# ---------------------------------------------------------------- cards

def tile_art(pb, w, h, shape, s, hair=0.22):
    """Card art baked once into the tile shape (ARGB, transparent outside the corners) with its resting hairline,
    so a card at rest costs one blit. pb=None bakes the empty glass placeholder."""
    surf = cairo.ImageSurface(cairo.FORMAT_ARGB32, w, h)
    cr = cairo.Context(surf)
    tile_path(cr, shape, 0, 0, w, h, s)
    if pb is None:
        cr.set_source_rgba(1, 1, 1, 0.06)
        cr.fill()
    else:
        cr.save()
        cr.clip()
        pw, ph = pb.get_width(), pb.get_height()
        if (pw, ph) != (w, h):
            cr.scale(w / pw, h / ph)
        Gdk.cairo_set_source_pixbuf(cr, pb, 0, 0)
        cr.paint()
        cr.restore()
    if hair:
        tile_path(cr, shape, 0.5, 0.5, w - 1, h - 1, s)
        cr.set_source_rgba(1, 1, 1, hair)
        cr.set_line_width(1)
        cr.stroke()
    surf.flush()
    return surf


class PosterCard(Focusable):
    """A media card: art in the tile shape at its exact size, a quiet title (+ sub) under it, a thin progress line
    for unfinished titles. Focus is Home's: the art scales up, its hairline goes 22% -> 90% and it glows; the text
    brightens and steps down out of the way of the grown art."""
    FOCUS_SCALE = 1.06
    rate = 12.0

    def __init__(self, shell, look_, item, kind, art_w, art_h, on_activate=None):
        p = shell.px
        self.aw, self.ah = art_w, art_h
        self.pad_x, self.pad_top = p(14), p(26)
        self.title_px, self.sub_px = p(20), p(17)
        self.gap = p(12)
        self.sub = item.get("subtitle") or ""
        text_h = round(self.title_px * 1.35 + (self.sub_px * 1.4 if self.sub else 0))
        h = self.pad_top + art_h + self.gap + text_h + p(16)
        super().__init__(shell, None, look_, on_activate, None, art_w + 2 * self.pad_x, h)
        self.item, self.kind = item, kind
        self.title = item.get("name") or ""
        self.progress = item.get("progress")
        self.pb = None
        self.art = None
        self.art_key = None
        self.lit = None
        self.set_valign(Gtk.Align.START)
        spill(self, shell.px(16), 0)              # into the gap between cards

    def set_pixbuf(self, pb):
        self.pb = pb
        self.art = self.lit = None
        self.queue_draw()

    def art_surface(self):
        if cairo is None:
            return None
        key = (self.look.tile_shape, self.aw, self.ah, self.pb is None)
        if self.art is None or self.art_key != key:
            self.art = tile_art(self.pb, self.aw, self.ah, self.look.tile_shape, scale_of(self.shell))
            self.art_key = key
            self.lit = None
        return self.art

    GLOW = 20       # px around the art kept in the focus layer (Home's glow reaches 15)

    def focus_layer(self, f):
        """The focused look (glow, art grown about its centre, lighter glass, bright hairline) rendered into one
        client-side surface, cached per focus step: while the card sits focused and the row scrolls under it,
        every frame is a single blit instead of a few strokes on the X surface."""
        art = self.art_surface()
        key = (round(f * 24), self.art_key, self.look.effects)
        if self.lit is not None and self.lit[0] == key:
            return self.lit[1]
        s, shape = scale_of(self.shell), self.look.tile_shape
        aw, ah = self.aw, self.ah
        g = round(self.GLOW * s) + 2
        sc = 1 + (self.FOCUS_SCALE - 1) * f if self.look.effects else 1.0
        surf = cairo.ImageSurface(cairo.FORMAT_ARGB32, int(aw * sc) + 2 * g + 2, int(ah * sc) + 2 * g + 2)
        cr = cairo.Context(surf)
        cr.translate(surf.get_width() / 2, surf.get_height() / 2)
        cr.scale(sc, sc)
        x, y = -aw / 2, -ah / 2
        if self.look.effects:
            draw_glow(cr, f, lambda c, gg: tile_path(c, shape, x - gg, y - gg, aw + 2 * gg, ah + 2 * gg, s, gg))
        cr.set_source_surface(art, x, y)
        cr.get_source().set_filter(cairo.FILTER_BILINEAR)
        cr.paint()
        tile_path(cr, shape, x, y, aw, ah, s)
        cr.set_source_rgba(1, 1, 1, 0.05 * f)
        cr.fill()
        tile_path(cr, shape, x + 0.5, y + 0.5, aw - 1, ah - 1, s)
        cr.set_source_rgba(1, 1, 1, 0.87 * f)              # over the baked 22% line: 22% -> 90%
        cr.set_line_width(focus_line(self.look, f))
        cr.stroke()
        surf.flush()
        self.lit = (key, surf)
        return surf

    def paint(self, cr, base, f, w, h):
        p = self.shell.px
        aw, ah = self.aw, self.ah
        sc = 1 + (self.FOCUS_SCALE - 1) * f if self.look.effects else 1.0
        cx, cy = self.pad_x + aw / 2, self.pad_top + ah / 2
        if f <= 0.003:
            self.lit = None                                  # nothing focused here: drop the focus layer
            art = self.art_surface()
            if art is not None:
                cr.set_source_surface(art, self.pad_x, self.pad_top)
                cr.paint()
        else:
            layer = self.focus_layer(f)
            cr.set_source_surface(layer, round(cx - layer.get_width() / 2), round(cy - layer.get_height() / 2))
            cr.paint()
        if self.progress is not None:
            frac = max(0.0, min(1.0, float(self.progress)))
            lx, ly, lw = cx + (-aw / 2 + p(14)) * sc, cy + (ah / 2 - p(14)) * sc, (aw - p(28)) * sc
            cr.set_line_cap(cairo.LINE_CAP_ROUND)
            cr.set_line_width(max(2.0, p(4)))
            cr.set_source_rgba(0, 0, 0, 0.45)
            cr.move_to(lx, ly)
            cr.line_to(lx + lw, ly)
            cr.stroke()
            cr.set_source_rgba(1, 1, 1, 0.92)
            cr.move_to(lx, ly)
            cr.line_to(lx + max(1.0, lw * frac), ly)
            cr.stroke()
        ty = self.pad_top + ah + self.gap + (sc - 1) * ah / 2
        weight = Pango.Weight.MEDIUM if f > 0.5 else Pango.Weight.NORMAL
        self.text.draw(cr, self.title, self.title_px, weight, self.pad_x + p(2), ty, aw - p(2),
                       (1, 1, 1, 0.74 + 0.26 * f), halo=0.3)
        if self.sub:
            self.text.draw(cr, self.sub, self.sub_px, Pango.Weight.NORMAL, self.pad_x + p(2),
                           ty + self.title_px * 1.35, aw - p(2), (1, 1, 1, 0.5 + 0.2 * f), halo=0.3)


class GlassButton(Focusable):
    """A frosted tile in tile_shape with a hex-framed glyph and a label (+ quiet sub). primary = the badge is lit.
    Rest: 22% hairline; focus: Home's scale, glow and 90% hairline. width=None sizes it to the text."""
    FOCUS_SCALE = 1.04

    def __init__(self, shell, src, look_, label, glyph_name=None, primary=False, sub="", width=None,
                 on_activate=None, height=None, px=24):
        p = shell.px
        self.glyph, self.primary, self.sub = glyph_name, primary, sub
        self.label_px, self.sub_px = p(px), p(18)
        self.r = p(19) if glyph_name else 0
        self.pad = p(14)                         # room for the focus scale and glow
        self.bh = height or (p(92) if sub else p(68))
        self.fixed_w = width
        super().__init__(shell, src, look_, on_activate, None, -1, self.bh + 2 * self.pad)
        self.label = label
        self.resize()
        spill(self, p(10))

    def set_label(self, label):
        if label != self.label:
            self.label = label
            self.resize()
            self.queue_draw()

    def get_label(self):
        return self.label

    def inner_x(self):
        p = self.shell.px
        return p(22) + (2 * self.r + p(16) if self.glyph else 0)

    def resize(self):
        p = self.shell.px
        if self.fixed_w:
            self.bw = self.fixed_w
        else:
            lw = self.text.layout(self.label, self.label_px, Pango.Weight.NORMAL, 4000).get_pixel_size()[0]
            if self.sub:
                lw = max(lw, self.text.layout(self.sub, self.sub_px, Pango.Weight.NORMAL, 4000).get_pixel_size()[0])
            self.bw = self.inner_x() + lw + p(30)
        self.set_size_request(self.bw + 2 * self.pad, self.bh + 2 * self.pad)

    def paint(self, cr, base, f, w, h):
        p, s = self.shell.px, scale_of(self.shell)
        shape = self.look.tile_shape
        bw, bh = self.bw, self.bh
        sc = 1 + (self.FOCUS_SCALE - 1) * f if self.look.effects else 1.0
        on = self.is_sensitive()
        cr.save()
        cr.translate(self.pad + bw / 2, self.pad + bh / 2)
        cr.scale(sc, sc)
        x, y = -bw / 2, -bh / 2
        if self.look.effects:
            draw_glow(cr, f, lambda c, g: tile_path(c, shape, x - g, y - g, bw + 2 * g, bh + 2 * g, s, g))
        tile_path(cr, shape, x, y, bw, bh, s)
        cr.save()
        cr.clip()
        paint_frost(cr, base, self, self.src)
        if self.look.transparency:
            cr.set_source_rgba(0.015, 0.025, 0.05, 0.28)
            cr.paint()
        cr.set_source_rgba(1, 1, 1, (0.13 if self.primary else 0.07) + 0.10 * f)
        cr.paint()
        cr.restore()
        tile_path(cr, shape, x + 0.5, y + 0.5, bw - 1, bh - 1, s)
        cr.set_source_rgba(1, 1, 1, 0.22 + 0.68 * f)
        cr.set_line_width(focus_line(self.look, f))
        cr.stroke()
        tx = x + (p(22) if not self.glyph else self.inner_x())
        if self.glyph:
            bx = x + p(22) + self.r
            cr.save()
            cr.translate(bx, 0)
            draw_badge(cr, base, self, self.src, self.look, self.r, None, ACCENT, f, 1.0 if self.primary else 0.0)
            glyph(cr, self.glyph, 0, 0, self.r * 0.62, 0.7 + 0.3 * max(f, float(self.primary)))
            cr.restore()
        k = 1.0 if on else 0.45
        tw = bw - (tx - x) - p(20)
        lh = self.label_px * 1.25 + (self.sub_px * 1.4 if self.sub else 0)
        ty = -lh / 2
        weight = Pango.Weight.MEDIUM if (self.primary or f > 0.5) else Pango.Weight.NORMAL
        self.text.draw(cr, self.label, self.label_px, weight, tx, ty, tw, (1, 1, 1, (0.86 + 0.14 * f) * k))
        if self.sub:
            self.text.draw(cr, self.sub, self.sub_px, Pango.Weight.NORMAL, tx, ty + self.label_px * 1.32, tw,
                           (1, 1, 1, (0.56 + 0.2 * f) * k))
        cr.restore()


class RailTitle(Gtk.DrawingArea):
    """A rail heading: a small hexagon marker and the title in Home's light weight."""

    def __init__(self, shell, look_, text, px=26):
        super().__init__()
        self.shell, self.look, self.label = shell, look_, text
        self.text = TextCache(self)
        self.px = shell.px(px)
        self.set_size_request(-1, round(self.px * 1.6))
        self.set_has_window(False)
        self.connect("draw", self.on_draw)

    def on_draw(self, _w, cr):
        p = self.shell.px
        w, h = self.get_allocated_width(), self.get_allocated_height()
        lay = self.text.layout(self.label, self.px, Pango.Weight.NORMAL, w)
        ty = (h - lay.get_pixel_size()[1]) / 2
        hex_marker(cr, self.look.node_shape, p(14) + p(6), h / 2 + p(1), p(6), lit=0.0, alpha=1.1)
        self.text.draw(cr, self.label, self.px, Pango.Weight.NORMAL, p(14) + p(24), ty, w - p(40),
                       (1, 1, 1, 0.86), halo=0.35)
        return False


class Kicker(Gtk.DrawingArea):
    """Letter-spaced small caps behind a tiny lit hexagon (the hero's 'MOVIE', 'CONTINUE WATCHING')."""

    def __init__(self, shell, look_, text="", px=16):
        super().__init__()
        self.shell, self.look, self.label = shell, look_, text.upper()
        self.text = TextCache(self)
        self.px = shell.px(px)
        self.set_size_request(-1, round(self.px * 1.7))
        self.set_has_window(False)
        self.connect("draw", self.on_draw)

    def set_text(self, text):
        text = (text or "").upper()
        if text != self.label:
            self.label = text
            self.queue_draw()

    def get_text(self):
        return self.label

    def on_draw(self, _w, cr):
        if not self.label:
            return False
        p = self.shell.px
        w, h = self.get_allocated_width(), self.get_allocated_height()
        lay = self.text.layout(self.label, self.px, Pango.Weight.SEMIBOLD, w, spacing=0.18)
        ty = (h - lay.get_pixel_size()[1]) / 2
        hex_marker(cr, self.look.node_shape, p(7), h / 2, p(6), lit=1.0)
        self.text.draw(cr, self.label, self.px, Pango.Weight.SEMIBOLD, p(24), ty, w - p(24), (1, 1, 1, 0.7),
                       spacing=0.18, halo=0.4)
        return False


# ---------------------------------------------------------------- sources

class SourceRow(Focusable):
    """A borderless glass row for one stream: a marker column of small hexagons joined by a faint line (the best
    source's marker lit), the label, languages as a quiet '·' line, and 'BEST MATCH' in small caps."""
    SCALE = 1.01

    def __init__(self, shell, look_, src, best=False, first=False, last=False, on_activate=None):
        p = shell.px
        self.pad_x, self.pad_y = p(10), p(6)
        langs = src.get("languages") or []
        self.sub = "  ·  ".join(str(lang).upper() if len(str(lang)) <= 3 else str(lang) for lang in langs)
        bh = p(92) if self.sub else p(76)
        super().__init__(shell, None, look_, on_activate, None, -1, bh + 2 * self.pad_y)
        self.label = src.get("label") or src.get("title") or "Source"
        self.best, self.first, self.last = best, first, last

    def paint(self, cr, base, f, w, h):
        p = self.shell.px
        x, y, bw, bh = self.pad_x, self.pad_y, w - 2 * self.pad_x, h - 2 * self.pad_y
        self.focus_tile(cr, f, x, y, bw, bh, 1 + (self.SCALE - 1) * f if self.look.effects else 1.0)
        mx, my = x + p(40), h / 2
        r = p(8)
        cr.set_source_rgba(1, 1, 1, 0.22)
        cr.set_line_width(max(1.0, p(1.2)))
        if not self.first:
            cr.move_to(mx, 0)
            cr.line_to(mx, my - r * 1.2)
            cr.stroke()
        if not self.last:
            cr.move_to(mx, my + r * 1.2)
            cr.line_to(mx, h)
            cr.stroke()
        hex_marker(cr, self.look.node_shape, mx, my, r if self.best else r * 0.8, lit=1.0 if self.best else 0.0,
                   alpha=0.8 + 0.2 * f)
        ix = mx + p(36)
        right = x + bw - p(28)
        vw = 0
        if self.best:
            px = p(15)
            lay = self.text.layout("BEST MATCH", px, Pango.Weight.SEMIBOLD, p(240), spacing=0.18)
            vw = lay.get_pixel_size()[0]
            self.text.draw(cr, "BEST MATCH", px, Pango.Weight.SEMIBOLD, right - vw, my - lay.get_pixel_size()[1] / 2,
                           vw + p(4), (1, 1, 1, 0.62 + 0.3 * f), spacing=0.18)
        tw = max(p(60), right - vw - p(30) - ix)
        title_px, sub_px = p(25), p(18)
        th = title_px * 1.25 + (sub_px * 1.4 if self.sub else 0)
        ty = y + (bh - th) / 2
        self.text.draw(cr, self.label, title_px, Pango.Weight.NORMAL, ix, ty, tw, (1, 1, 1, 0.84 + 0.16 * f))
        if self.sub:
            self.text.draw(cr, self.sub, sub_px, Pango.Weight.NORMAL, ix, ty + title_px * 1.34, tw,
                           (1, 1, 1, 0.5 + 0.2 * f), spacing=0.06)


# ---------------------------------------------------------------- busy marks

def draw_orbit(cr, shape, cx, cy, r, u, s, effects=True, trail=0.3, alpha=1.0):
    """The busy mark on a hexagon of radius r: with effects, a light point at fraction u of the rim trailing a
    fading line and lighting the vertices it passes; without, the vertex at u lit and the rim edge up to it."""
    pts = poly_points(shape, r, cx, cy)
    n = len(pts)
    cr.save()
    cr.set_line_cap(cairo.LINE_CAP_ROUND)
    cr.set_line_join(cairo.LINE_JOIN_ROUND)
    if effects:
        segs = 18

        def seg(i):
            a0 = u - trail * (1 - i / segs)
            a1 = u - trail * (1 - (i + 1) / segs)
            cr.move_to(*orbit_point(pts, a0))
            corner = math.ceil(a0 * n)
            while corner / n < a1:                    # keep the trail on the rim through corners
                cr.line_to(*pts[corner % n])
                corner += 1
            cr.line_to(*orbit_point(pts, a1))

        for i in range(segs):                         # a soft accent glow under the line, then the line
            k = (i + 1) / segs
            seg(i)
            cr.set_source_rgba(ACCENT[0], ACCENT[1], ACCENT[2], 0.22 * k * alpha)
            cr.set_line_width(max(3.0, 9 * s * k))
            cr.stroke()
        for i in range(segs):
            k = (i + 1) / segs
            seg(i)
            cr.set_source_rgba(1, 1, 1, 0.95 * k ** 1.4 * alpha)
            cr.set_line_width(max(1.2, 3.0 * s * (0.35 + 0.65 * k)))
            cr.stroke()
        d = 4.0 * s
        for i, (vx, vy) in enumerate(pts):            # vertices passed light up and fade over a lap
            age = (u - i / n) % 1.0
            a = max(0.0, 1.0 - age / 0.75) * 0.9 * alpha
            if a <= 0.02:
                continue
            cr.set_source_rgba(1, 1, 1, a)
            cr.move_to(vx, vy - d)
            cr.line_to(vx + d, vy)
            cr.line_to(vx, vy + d)
            cr.line_to(vx - d, vy)
            cr.close_path()
            cr.fill()
        px, py = orbit_point(pts, u)
        g = cairo.RadialGradient(px, py, 0, px, py, 22 * s)
        g.add_color_stop_rgba(0, 1, 1, 1, 0.85 * alpha)
        g.add_color_stop_rgba(0.3, ACCENT[0], ACCENT[1], ACCENT[2], 0.42 * alpha)
        g.add_color_stop_rgba(1, ACCENT[0], ACCENT[1], ACCENT[2], 0)
        cr.set_source(g)
        cr.arc(px, py, 22 * s, 0, 2 * math.pi)
        cr.fill()
        cr.set_source_rgba(1, 1, 1, alpha)
        cr.arc(px, py, max(2.0, 4.2 * s), 0, 2 * math.pi)
        cr.fill()
    else:
        k = int(u * n) % n
        cr.set_source_rgba(1, 1, 1, 0.85 * alpha)
        cr.set_line_width(max(1.5, 2.2 * s))
        cr.move_to(*pts[(k - 1) % n])
        cr.line_to(*pts[k])
        cr.stroke()
        cr.arc(pts[k][0], pts[k][1], max(2.5, 5 * s), 0, 2 * math.pi)
        cr.fill()
    cr.restore()


class _Animated:
    """A frame timer that runs only while the widget is mapped: 15 fps with effects, a 2 fps tick without."""
    PERIOD = 2.6            # seconds per lap

    def anim_init(self):
        self.u = 0.0
        self.anim_src = 0
        self.frames = 0
        self.last_t = 0.0
        self.connect("map", lambda *_: self.anim_start())
        self.connect("unmap", lambda *_: self.anim_stop())
        self.connect("destroy", lambda *_: self.anim_stop())

    def anim_start(self):
        if self.anim_src:
            return
        self.last_t = GLib.get_monotonic_time() / 1e6
        self.anim_ms = FRAME_MS if self.look.effects else TICK_MS
        self.anim_src = GLib.timeout_add(self.anim_ms, self.anim_step)

    def anim_stop(self):
        if self.anim_src:
            GLib.source_remove(self.anim_src)
            self.anim_src = 0

    def anim_step(self):
        now = GLib.get_monotonic_time() / 1e6
        dt, self.last_t = now - self.last_t, now
        if self.look.effects:
            self.u = (self.u + dt / self.PERIOD) % 1.0
        else:
            n = len(poly_points(self.look.node_shape, 1))
            self.u = ((round(self.u * n) + 1) % n) / n
        self.frames += 1
        self.invalidate_frame()
        if self.anim_ms != (FRAME_MS if self.look.effects else TICK_MS):     # Visual effects was switched
            self.anim_src = 0
            self.anim_start()
            return False
        return True


class HexSpinner(Gtk.DrawingArea, _Animated):
    """A small hexagon with a point orbiting its rim (Sources' 'finding sources'); timer only while mapped."""

    def __init__(self, shell, look_, r=18):
        Gtk.DrawingArea.__init__(self)
        self.shell, self.look = shell, look_
        self.r = shell.px(r)
        m = shell.px(14)
        self.set_size_request(2 * (self.r + m), 2 * (self.r + m))
        self.set_has_window(False)
        self.connect("draw", self.on_draw)
        self.anim_init()

    def invalidate_frame(self):
        self.queue_draw()

    def on_draw(self, _w, cr):
        w, h = self.get_allocated_width(), self.get_allocated_height()
        s = scale_of(self.shell)
        poly_path(cr, poly_points(self.look.node_shape, self.r, w / 2, h / 2))
        cr.set_source_rgba(1, 1, 1, 0.3)
        cr.set_line_width(1)
        cr.stroke()
        draw_orbit(cr, self.look.node_shape, w / 2, h / 2, self.r, self.u, s * 0.7, self.look.effects, trail=0.35)
        return False


# ---------------------------------------------------------------- header

class Wordmark(Gtk.DrawingArea):
    """'iMac TV' in Home's light weight after a small hexagon mark (a lit core in a hairline rim)."""

    def __init__(self, shell, look_, text="iMac TV"):
        super().__init__()
        self.shell, self.look, self.label = shell, look_, text
        self.text = TextCache(self)
        p = shell.px
        self.px = p(30)
        lw = self.text.layout(text, self.px, Pango.Weight.LIGHT, 4000, spacing=0.04).get_pixel_size()[0]
        self.set_size_request(p(44) + lw + p(4), p(52))
        self.set_has_window(False)
        self.connect("draw", self.on_draw)

    def on_draw(self, _w, cr):
        p = self.shell.px
        w, h = self.get_allocated_width(), self.get_allocated_height()
        shape = self.look.node_shape
        cx, cy = p(15), h / 2
        poly_path(cr, poly_points(shape, p(14), cx, cy))
        cr.set_source_rgba(1, 1, 1, 0.75)
        cr.set_line_width(max(1.0, p(1.4)))
        cr.stroke()
        hex_marker(cr, shape, cx, cy, p(5.5), lit=1.0)
        lay = self.text.layout(self.label, self.px, Pango.Weight.LIGHT, w, spacing=0.04)
        self.text.draw(cr, self.label, self.px, Pango.Weight.LIGHT, p(44), (h - lay.get_pixel_size()[1]) / 2,
                       w - p(44), (1, 1, 1, 0.96), spacing=0.04, halo=0.4)
        return False


class NavTab(Focusable):
    """A header tab: the label in Home's weight (+ an optional glyph). current = a small lit hexagon under it;
    focus = Home's tile focus around the label (scale, glow, 90% hairline)."""
    SCALE = 1.04

    def __init__(self, shell, look_, label, glyph_name=None, on_activate=None):
        p = shell.px
        self.label, self.glyph = label, glyph_name
        self.px = p(23)
        self.current = False
        super().__init__(shell, None, look_, on_activate, None, -1, p(84))
        lw = self.text.layout(label, self.px, Pango.Weight.MEDIUM, 4000).get_pixel_size()[0]
        self.lw = lw + (p(34) if glyph_name else 0)
        self.set_size_request(self.lw + p(40), p(84))
        spill(self, p(10), p(18))

    def set_current(self, on):
        if on != self.current:
            self.current = on
            self.queue_draw()

    def paint(self, cr, base, f, w, h):
        p = self.shell.px
        cy = p(36)
        bw, bh = self.lw + p(32), p(50)
        sc = 1 + (self.SCALE - 1) * f if self.look.effects else 1.0
        self.focus_tile(cr, f, (w - bw) / 2, cy - bh / 2, bw, bh, sc, fill=0.12)
        x = (w - self.lw) / 2
        a = 0.66 + 0.34 * max(f, float(self.current))
        if self.glyph:
            glyph(cr, self.glyph, x + p(12), cy, p(12), a)
            x += p(34)
        weight = Pango.Weight.MEDIUM if (self.current or f > 0.5) else Pango.Weight.NORMAL
        lay = self.text.layout(self.label, self.px, weight, 4000)
        self.text.draw(cr, self.label, self.px, weight, x, cy - lay.get_pixel_size()[1] / 2, w - x,
                       (1, 1, 1, a), halo=0.45)
        if self.current:
            hex_marker(cr, self.look.node_shape, w / 2, cy + bh / 2 + p(10), p(5.5), lit=1.0)
            if self.look.effects:
                draw_glow(cr, 0.6, lambda c, g: poly_path(c, poly_points(self.look.node_shape, p(5.5) + g * 0.5,
                                                                         w / 2, cy + bh / 2 + p(10))))


class PhoneChip(Focusable):
    """'Connect phone' without a box: a small glass hexagon with a phone glyph and two quiet lines."""

    def __init__(self, shell, src, look_, on_activate=None):
        p = shell.px
        self.lines = ("Connect phone", "Scan to pair")
        self.r = p(19)
        super().__init__(shell, src, look_, on_activate, None, -1, p(84))
        self.resize()
        spill(self, p(10), p(18))

    def resize(self):
        p = self.shell.px
        w = max(self.text.layout(t, px, Pango.Weight.NORMAL, 4000).get_pixel_size()[0]
                for t, px in zip(self.lines, (p(19), p(16))))
        self.tw = w
        self.set_size_request(p(30) + 2 * self.r + p(14) + w + p(26), p(84))

    def set_lines(self, top, bottom):
        if (top, bottom) != self.lines:
            self.lines = (top, bottom)
            self.resize()
            self.queue_draw()

    def paint(self, cr, base, f, w, h):
        p = self.shell.px
        cy = p(36)
        bw, bh = w - p(12), p(58)
        self.focus_tile(cr, f, p(6), cy - bh / 2, bw, bh, 1.0, fill=0.12)
        cx = p(20) + self.r
        cr.save()
        cr.translate(cx, cy)
        draw_badge(cr, base, self, self.src, self.look, self.r, None, ACCENT, f, 0.0)
        glyph(cr, "phone", 0, 0, self.r * 0.62, 0.72 + 0.28 * f)
        cr.restore()
        tx = cx + self.r + p(14)
        top, bottom = self.lines
        self.text.draw(cr, top, p(19), Pango.Weight.NORMAL, tx, cy - p(24), w - tx, (1, 1, 1, 0.82 + 0.18 * f),
                       halo=0.45)
        self.text.draw(cr, bottom, p(16), Pango.Weight.NORMAL, tx, cy + p(1), w - tx, (1, 1, 1, 0.5 + 0.2 * f),
                       halo=0.45)


class NetLabel(Gtk.DrawingArea):
    """The network name after glass.draw_signal's rising hexagons; no emoji, no colour."""

    def __init__(self, shell, look_):
        super().__init__()
        self.shell, self.look, self.net = shell, look_, {}
        self.text = TextCache(self)
        self.name = "Connecting…"
        self.set_has_window(False)
        self.connect("draw", self.on_draw)
        self.resize()

    def label_for(self, net):
        if not net or not net.get("connected"):
            return "Offline"
        if net.get("type") == "wifi":
            return (net.get("ssid") or "Wi-Fi")[:18]
        if net.get("type") == "ethernet":
            return "Ethernet"
        return "Online"

    def set_net(self, net):
        net = net or {}
        name = self.label_for(net)
        if name != self.name or signal_level(net) != signal_level(self.net):
            self.net, self.name = net, name
            self.resize()
            self.queue_draw()

    def get_text(self):
        return self.name

    def resize(self):
        p = self.shell.px
        lw = self.text.layout(self.name, p(19), Pango.Weight.NORMAL, 4000).get_pixel_size()[0]
        self.set_size_request(self.text_x() + lw + p(4), p(84))

    def text_x(self):
        p = self.shell.px
        flat = 0.924 if self.look.node_shape == "octagon" else 0.866
        s, gap = p(15), max(2.0, p(15) * 0.2)
        return round(sum(2 * s / (2 * flat) * (0.5 + 0.5 * i / 3) for i in range(4)) + 3 * gap) + p(12)

    def on_draw(self, _w, cr):
        p = self.shell.px
        w, h = self.get_allocated_width(), self.get_allocated_height()
        lay = self.text.layout(self.name, p(19), Pango.Weight.NORMAL, 4000)
        ty = p(36) - lay.get_pixel_size()[1] / 2
        base = ty + lay.get_baseline() / Pango.SCALE
        level = signal_level(self.net) if self.net else 0
        draw_signal(cr, self.look.node_shape, 0, base, p(15), level)
        tx = self.text_x()
        self.text.draw(cr, self.name, p(19), Pango.Weight.NORMAL, tx, ty, w - tx,
                       (1, 1, 1, 0.72 if level else 0.5), halo=0.45)
        return False


class HeaderClock(Gtk.DrawingArea):
    """Home's clock, small: the time in a light weight after a quiet date."""

    def __init__(self, shell, look_):
        super().__init__()
        self.shell, self.look = shell, look_
        self.text = TextCache(self)
        self.date, self.time = "", ""
        self.set_has_window(False)
        self.set_size_request(shell.px(250), shell.px(84))
        self.connect("draw", self.on_draw)

    def set_now(self, now):
        date, time_ = now.strftime("%a %-d %b"), now.strftime("%H:%M")
        if (date, time_) != (self.date, self.time):
            self.date, self.time = date, time_
            self.queue_draw()

    def get_text(self):
        return f"{self.date}  ·  {self.time}"

    def on_draw(self, _w, cr):
        p = self.shell.px
        w, h = self.get_allocated_width(), self.get_allocated_height()
        tl = self.text.layout(self.time, p(36), Pango.Weight.LIGHT, 4000)
        tw, th = tl.get_pixel_size()
        dl = self.text.layout(self.date, p(19), Pango.Weight.NORMAL, 4000)
        dw, dh = dl.get_pixel_size()
        cy = p(36)
        x = w - tw
        self.text.draw(cr, self.time, p(36), Pango.Weight.LIGHT, x, cy - th / 2, tw + 2, (1, 1, 1, 0.95), halo=0.45)
        self.text.draw(cr, self.date, p(19), Pango.Weight.NORMAL, x - p(16) - dw, cy - dh / 2 + p(3), dw + 2,
                       (1, 1, 1, 0.62), halo=0.45)
        return False


class PowerButton(Focusable):
    """The power glyph in a small glass hexagon; focus = Home's node focus (scale, glow, bright rim)."""
    FOCUS_SCALE = 1.2

    def __init__(self, shell, src, look_, on_activate=None):
        p = shell.px
        self.r = p(20)
        super().__init__(shell, src, look_, on_activate, None, p(64), p(84))
        spill(self, p(10), p(18))

    def paint(self, cr, base, f, w, h):
        cr.save()
        cr.translate(w / 2, self.shell.px(36))
        sc = 1 + (self.FOCUS_SCALE - 1) * f if self.look.effects else 1.0
        cr.scale(sc, sc)
        draw_badge(cr, base, self, self.src, self.look, self.r, None, ACCENT, f, 0.0)
        glyph(cr, "power", 0, 0, self.r * 0.62, 0.7 + 0.3 * f)
        cr.restore()


# ---------------------------------------------------------------- the stream loading screen

ORBIT_GAP = 14          # px at 1080p between the glass rim and the busy mark's track
KICKER = 40             # px at 1080p for the small-caps line above the title


def loading_layout(w, h, lines=1):
    """Where the loading screen's pieces go, for a w x h screen: hexagon centre + radius, the text block's origin
    and width, and the hexagon's redraw box (rim + glow + the orbiting point's halo)."""
    s = h / 1080.0
    r = 205 * s
    x0 = 150 * s
    title_h = 88 * s * max(1, min(2, lines))
    block = 2 * 0.924 * r + 64 * s + KICKER * s + title_h + 20 * s + 36 * s + 12 * s + 30 * s + 52 * s + 26 * s
    top = max(60 * s, (h - block) / 2 - 10 * s)
    cx, cy = x0 + r, top + 0.924 * r
    m = (ORBIT_GAP + 34) * s                  # the orbit runs ORBIT_GAP outside the rim; its halo and glow
    box = (int(cx - r - m), int(cy - r - m), int(2 * (r + m)) + 1, int(2 * (r + m)) + 1)
    tx, ky = x0, cy + 0.924 * r + 64 * s
    ty = ky + KICKER * s
    return {"ky": ky, "s": s, "r": r, "orbit": r + ORBIT_GAP * s, "cx": cx, "cy": cy, "box": box, "tx": tx, "ty": ty,
            "tw": min(w - x0 - 120 * s, 1250 * s)}


class LoadingView(Gtk.DrawingArea, _Animated):
    """The stream loading screen as one drawn widget. The still parts (blurred, dimmed title art or Home's
    backdrop; the poster clipped into a large glass hexagon with its glow and rim) are rendered once into a
    client-side surface; the busy mark is rasterized per frame into a small surface of the hexagon's box and
    blitted once, and only that box is invalidated; text is redrawn only when it changes."""

    def __init__(self, shell, look_):
        Gtk.DrawingArea.__init__(self)
        self.shell, self.look = shell, look_
        self.text = TextCache(self)
        self.title, self.status, self.meta, self.kicker = "", "", "", ""
        self.hint = "Press Back to cancel"
        self.poster = None            # GdkPixbuf
        self.art = None               # GdkPixbuf (background art)
        self.bg = None
        self.bg_key = None
        self.frame = None
        self.title_lay = None
        self.set_has_window(False)
        self.connect("draw", self.on_draw)
        self.anim_init()

    # -- state
    def set_text(self, title=None, status=None, meta=None, kicker=None):
        changed = False
        if kicker is not None and kicker.upper() != self.kicker:
            self.kicker = kicker.upper()
            changed = True
            title = self.title if title is None else title
            self.title = None                     # a new kicker redraws the whole block
        for name, val in (("title", title), ("status", status), ("meta", meta)):
            if val is not None and val != getattr(self, name):
                setattr(self, name, val)
                changed = True
        if changed:
            if title is not None:
                self.title_lay = None
                self.bg = None        # the block moves with the title's line count
                self.queue_draw()
            else:
                self.queue_text()

    def set_art(self, poster=None, art=None):
        """poster / art: GdkPixbufs, or None to keep what is there; pass False to clear."""
        if poster is not None:
            self.poster = poster or None
        if art is not None:
            self.art = art or None
        self.bg = None
        self.queue_draw()

    def clear_art(self):
        self.poster = self.art = None
        self.bg = None
        self.queue_draw()

    # -- geometry
    def layout(self):
        w, h = self.get_allocated_width(), self.get_allocated_height()
        lines = self.title_lines(w, h)
        return loading_layout(w, h, lines)

    def title_layout(self, width, px):
        if self.title_lay is None or self.title_lay[0] != (width, px):
            lay = Pango.Layout.new(self.get_pango_context())
            fd = Pango.FontDescription(FONT)
            fd.set_absolute_size(px * Pango.SCALE)
            fd.set_weight(Pango.Weight.LIGHT)
            lay.set_font_description(fd)
            lay.set_width(int(max(1, width) * Pango.SCALE))
            lay.set_wrap(Pango.WrapMode.WORD_CHAR)
            lay.set_ellipsize(Pango.EllipsizeMode.END)
            lay.set_height(-2)                       # at most two lines
            lay.set_text(self.title or "Starting playback", -1)
            self.title_lay = ((width, px), lay)
        return self.title_lay[1]

    def title_lines(self, w, h):
        s = h / 1080.0
        lay = self.title_layout(loading_layout(w, h)["tw"], round(72 * s))
        return max(1, min(2, lay.get_line_count()))

    def text_rect(self, L):
        s = L["s"]
        return (int(L["tx"] - 10 * s), int(L["ky"] - 10 * s), int(L["tw"] + 30 * s),
                int(self.get_allocated_height() - L["ky"] + 10 * s))

    def queue_text(self):
        if self.get_allocated_width() > 1:
            self.queue_draw_area(*self.text_rect(self.layout()))

    def invalidate_frame(self):
        if self.get_allocated_width() > 1:
            self.queue_draw_area(*self.layout()["box"])

    # -- the still picture
    def render_bg(self, w, h, L):
        surf = cairo.ImageSurface(cairo.FORMAT_RGB24, w, h)
        cr = cairo.Context(surf)
        s, r, cx, cy = L["s"], L["r"], L["cx"], L["cy"]
        avoid = (L["box"], self.text_rect(L))
        soft, lum = None, 0.3
        if self.art is not None:
            src = pixbuf_surface(self.art)
            small = small_copy(src, self.art.get_width(), self.art.get_height(), 96)
            soft, lum = soften(soften(small)), mean_luminance(small, 1)
        else:
            bd = SharedBackdrop(self.shell, self.look)
            soft, lum = bd.soft, bd.lum
        paint_backdrop(cr, w, h, soft, lum, self.look, seed=11, avoid=avoid, target=0.12, least=0.55, most=0.88)
        shape = self.look.node_shape
        pts = poly_points(shape, r, cx, cy)
        if self.look.effects:
            draw_glow(cr, 0.55, lambda c, g: poly_path(c, poly_points(shape, r + g, cx, cy)))
        poly_path(cr, pts)
        cr.save()
        cr.clip()
        if self.poster is not None:
            pw, ph = self.poster.get_width(), self.poster.get_height()
            k = max(2 * r / pw, 2 * r / ph)
            cr.translate(cx - pw * k / 2, cy - ph * k * 0.42)
            cr.scale(k, k)
            Gdk.cairo_set_source_pixbuf(cr, self.poster, 0, 0)
            cr.get_source().set_filter(cairo.FILTER_GOOD)
            cr.paint()
            cr.identity_matrix()
            g = cairo.LinearGradient(0, cy - r, 0, cy + r)      # Home's glass sheen over the picture
            g.add_color_stop_rgba(0, 1, 1, 1, 0.10)
            g.add_color_stop_rgba(0.45, 1, 1, 1, 0.0)
            g.add_color_stop_rgba(1, 0, 0, 0, 0.28)
            cr.set_source(g)
            cr.paint()
        else:
            cr.set_source_rgba(1, 1, 1, 0.08)
            cr.paint()
            cr.identity_matrix()
            glyph(cr, "playback", cx, cy, r * 0.32, 0.55)
        cr.restore()
        poly_path(cr, poly_points(shape, r - 0.6, cx, cy))
        cr.set_source_rgba(1, 1, 1, 0.5)
        cr.set_line_width(max(1.0, 1.2 * s))
        cr.stroke()
        poly_path(cr, poly_points(shape, L["orbit"], cx, cy))        # the busy mark's faint track
        cr.set_source_rgba(1, 1, 1, 0.12)
        cr.set_line_width(1)
        cr.stroke()
        surf.flush()
        return surf

    def background(self, w, h, L):
        key = (w, h, id(self.poster), id(self.art), self.look.key(), L["box"], self.text_rect(L))
        if self.bg is None or self.bg_key != key:
            self.bg, self.bg_key = self.render_bg(w, h, L), key
        return self.bg

    # -- drawing
    def on_draw(self, _w, cr):
        if cairo is None:
            return False
        w, h = self.get_allocated_width(), self.get_allocated_height()
        if w < 2 or h < 2:
            return False
        L = self.layout()
        bg = self.background(w, h, L)
        x0, y0, x1, y1 = cr.clip_extents()
        bx, by, bw, bh = L["box"]
        cr.set_source_surface(bg, 0, 0)
        cr.paint()
        if x0 < bx + bw and x1 > bx and y0 < by + bh and y1 > by:
            if self.frame is None or (self.frame.get_width(), self.frame.get_height()) != (bw, bh):
                self.frame = cairo.ImageSurface(cairo.FORMAT_RGB24, bw, bh)
            fc = cairo.Context(self.frame)
            fc.set_source_surface(bg, -bx, -by)
            fc.paint()
            draw_orbit(fc, self.look.node_shape, L["cx"] - bx, L["cy"] - by, L["orbit"], self.u, L["s"],
                       self.look.effects)
            self.frame.flush()
            cr.set_source_surface(self.frame, bx, by)
            cr.paint()
        tr = self.text_rect(L)
        if x0 < tr[0] + tr[2] and x1 > tr[0] and y0 < tr[1] + tr[3] and y1 > tr[1]:
            self.draw_text(cr, L)
        return False

    def draw_text(self, cr, L):
        s, tx, ty, tw = L["s"], L["tx"], L["ty"], L["tw"]
        if self.kicker:
            kpx = round(17 * s)
            hex_marker(cr, self.look.node_shape, tx + 7 * s, L["ky"] + kpx * 0.62, 6 * s, lit=1.0)
            self.text.draw(cr, self.kicker, kpx, Pango.Weight.SEMIBOLD, tx + 24 * s, L["ky"], tw, (1, 1, 1, 0.68),
                           spacing=0.18, halo=0.3)
        lay = self.title_layout(tw, round(72 * s))
        cr.set_source_rgba(0, 0, 0, 0.3)
        for dx, dy in ((0, 2), (1.5, 2), (-1.5, 2)):
            cr.move_to(tx + dx, ty + dy)
            PangoCairo.show_layout(cr, lay)
        cr.set_source_rgba(1, 1, 1, 0.97)
        cr.move_to(tx, ty)
        PangoCairo.show_layout(cr, lay)
        y = ty + lay.get_pixel_size()[1] + 20 * s
        y += self.text.draw(cr, self.status, round(26 * s), Pango.Weight.NORMAL, tx, y, tw, (1, 1, 1, 0.84),
                            halo=0.3) + 12 * s
        if self.meta:
            self.text.draw(cr, self.meta, round(20 * s), Pango.Weight.NORMAL, tx, y, tw, (1, 1, 1, 0.52),
                           spacing=0.03, halo=0.3)
        y += 30 * s + 52 * s
        self.text.draw(cr, self.hint, round(18 * s), Pango.Weight.NORMAL, tx, y, tw, (1, 1, 1, 0.36), spacing=0.04)
