"""The "network" (constellation) style: frosted-glass tiles and round nodes joined by thin lines, with dust
points, slow organic drift, depth-of-field focus and sparks running along the lines that touch the focus.

Pure math (layout, links, drift) is separate from the cairo-drawn Node widgets so it can be unit tested.
Lines/dust/sparks are drawn in ONE pass by draw_network(), hooked into the photo canvas; nodes are widgets."""
import math
import random
import sys
from pathlib import Path

import gi
gi.require_version("Gtk", "3.0")
gi.require_version("Pango", "1.0")
from gi.repository import Gtk, Gdk, GdkPixbuf, Pango

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import screens  # noqa: E402
from .bubbles import RowBase, Bubble, _icon, TINTS, ease, in_circle, draw_eq, load_any  # noqa: E402

try:
    import cairo
except Exception:  # pragma: no cover
    cairo = None

FOCUS_SCALE = 1.25
EXT = 1.45            # widget side relative to the node's resting size (room for focus scale + glow)
LINE_MAX = 420        # px at 1080p: farthest two points still get linked
DUST_N = 25
GLOW = (0.62, 0.80, 1.0)


# ---------------------------------------------------------------- pure math

def drift(t, seed, amp):
    """Slow independent sin/cos wander, periods 2.5-3.5 s: (dx, dy) in px."""
    rng = random.Random(seed * 977 + 13)
    p1, p2 = rng.uniform(2.5, 3.5), rng.uniform(2.5, 3.5)
    f1, f2 = rng.uniform(0, 6.283), rng.uniform(0, 6.283)
    return amp * math.sin(6.283 * t / p1 + f1), amp * math.cos(6.283 * t / p2 + f2)


def dust_points(w, h, seed, n=DUST_N, margin=0.04):
    """Deterministic scatter of decorative points: [(x, y)]."""
    rng = random.Random(seed)
    mx, my = w * margin, h * margin
    return [(rng.uniform(mx, w - mx), rng.uniform(my, h - my)) for _ in range(n)]


def line_alpha(d, max_dist):
    """0.32 for touching points down to 0.06 at the maximum link distance."""
    k = max(0.0, min(1.0, d / max_dist))
    return 0.34 - (0.34 - 0.07) * k


def link_pairs(points, ks, max_dist):
    """Link each point to its ks[i] nearest neighbours within max_dist. points: [(x, y)]. -> sorted [(i, j, d)], i < j."""
    out = {}
    for i, (x, y) in enumerate(points):
        near = sorted(((math.hypot(x - u, y - v), j) for j, (u, v) in enumerate(points) if j != i))
        for d, j in near[:ks[i]]:
            if d <= max_dist:
                out[(min(i, j), max(i, j))] = d
    return sorted((i, j, d) for (i, j), d in out.items())


def row_layout(sizes, width, height, seed=1, width_frac=0.86, spread=0.2, gap_scale=1.0):
    """Irregular left-to-right placement of rectangles/circles given as (w, h) sizes in a width x height strip.
    -> [{"cx", "cy"}] with strictly increasing cx and no overlap."""
    n = len(sizes)
    if n == 0:
        return []
    rng = random.Random(seed * 4421 + n)
    mean = sum(w for w, _ in sizes) / n
    gaps = []
    for i in range(n - 1):
        g = rng.uniform(0.18, 0.4) * mean * gap_scale
        if (i + 1) % 3 == 0 and rng.random() < 0.7:
            g += rng.uniform(0.3, 0.7) * mean
        gaps.append(g)
    total = sum(w for w, _ in sizes) + sum(gaps)
    sumw, sumg = sum(w for w, _ in sizes), sum(gaps)
    if total > width * width_frac and sumg > 0:       # squeeze the gaps so the strip fits
        k = max(0.1, (width * width_frac - sumw) / sumg)
        gaps = [g * k for g in gaps]
        total = sumw + sum(gaps)
    x = (width - total) / 2
    out, sign = [], rng.choice((-1, 1))
    for i, (w, h) in enumerate(sizes):
        room = max(0.0, height / 2 - h * EXT / 2)
        cy = height / 2 + max(-room, min(room, sign * rng.uniform(0.4, 1.0) * height * spread))
        out.append({"cx": x + w / 2, "cy": cy})
        x += w + (gaps[i] if i < n - 1 else 0)
        sign = -sign
    return out


# ---------------------------------------------------------------- drawing helpers

def rrect(cr, x, y, w, h, r):
    r = min(r, w / 2, h / 2)
    cr.new_sub_path()
    cr.arc(x + w - r, y + r, r, -math.pi / 2, 0)
    cr.arc(x + w - r, y + h - r, r, 0, math.pi / 2)
    cr.arc(x + r, y + h - r, r, math.pi / 2, math.pi)
    cr.arc(x + r, y + r, r, math.pi, 1.5 * math.pi)
    cr.close_path()


def poly_points(shape, r, cx=0.0, cy=0.0):
    """Regular polygon vertices: "hexagon" (flat-top, points left/right) or "octagon" (flat sides, flat top)."""
    n, rot = (8, math.pi / 8) if shape == "octagon" else (6, 0.0)
    return [(cx + r * math.cos(rot + 2 * math.pi * i / n), cy + r * math.sin(rot + 2 * math.pi * i / n))
            for i in range(n)]


def point_in_poly(x, y, pts):
    """Even-odd ray cast."""
    inside = False
    j = len(pts) - 1
    for i in range(len(pts)):
        xi, yi, xj, yj = *pts[i], *pts[j]
        if (yi > y) != (yj > y) and x < (xj - xi) * (y - yi) / (yj - yi) + xi:
            inside = not inside
        j = i
    return inside


def poly_path(cr, pts):
    cr.new_path()
    cr.move_to(*pts[0])
    for p in pts[1:]:
        cr.line_to(*p)
    cr.close_path()


def chamfer_rect(cr, x, y, w, h, c):
    """Rectangle with its four corners cut off by c."""
    c = min(c, w / 2, h / 2)
    poly_path(cr, [(x + c, y), (x + w - c, y), (x + w, y + c), (x + w, y + h - c),
                   (x + w - c, y + h), (x + c, y + h), (x, y + h - c), (x, y + c)])


def blur_pixbuf(pb):
    """One-time cheap blur: down to 1/8 and back up (bilinear)."""
    w, h = pb.get_width(), pb.get_height()
    small = pb.scale_simple(max(2, w // 5), max(2, h // 5), GdkPixbuf.InterpType.BILINEAR)
    return small.scale_simple(w, h, GdkPixbuf.InterpType.BILINEAR)


def paint_frost(cr, base, widget, photo):
    """Fill the current clip with the blurred, screen-fixed crop of the photo behind `widget`."""
    if photo is not None and not getattr(photo, "transparency", True):
        cr.set_source_rgb(0.075, 0.105, 0.15)
        cr.paint()
        return
    layers = photo.blur_layers() if photo else []
    if not layers:
        cr.set_source_rgba(0.14, 0.17, 0.24, 0.7)
        cr.paint()
        return
    pos = widget.translate_coordinates(photo, 0, 0) or (0, 0)
    cr.save()
    cr.set_matrix(base)            # clip stays (device space); only the source is re-anchored to the screen
    cr.translate(-pos[0], -pos[1])
    k = photo.blur_k
    cr.scale(k, k)
    for surf, a in layers:
        cr.set_source_surface(surf, 0, 0)
        src = cr.get_source()
        src.set_filter(cairo.FILTER_BILINEAR)
        src.set_extend(cairo.EXTEND_PAD)
        cr.paint_with_alpha(a)
    cr.restore()


# ---------------------------------------------------------------- nodes

class Node(Gtk.DrawingArea):
    """Focusable windowless glass node. Subclasses draw the body at the origin (already drifted + scaled)."""
    text = Bubble.text
    amp = 5

    def __init__(self, shell, w, h, seed=0, on_activate=None, label_h=0):
        super().__init__()
        self.shell, self.w0, self.h0, self.seed = shell, w, h, seed
        self.on_activate = on_activate or (lambda: None)
        self.fw = round(w * EXT)
        self.body_h = round(h * EXT)
        self.fh = self.body_h + label_h
        self.cx, self.cy = self.fw / 2, self.body_h / 2
        self.set_size_request(self.fw, self.fh)
        self.set_has_window(False)
        self.set_can_focus(True)
        self.focus_amt = 0.0
        self.t = 0.0
        self.layouts = {}
        self.now = None
        self.connect("draw", self.on_draw)
        self.connect("focus-in-event", lambda *_: self.queue_draw() or False)
        self.connect("focus-out-event", lambda *_: self.queue_draw() or False)
        self.connect("key-press-event", self.on_key)

    def centre(self):
        return self.cx, self.cy

    def half_extent(self):
        return self.w0 * 0.58, self.h0 * 0.58

    def on_key(self, _w, ev):
        if Gdk.keyval_name(ev.keyval) in ("Return", "KP_Enter", "space"):
            self.on_activate()
            return True
        return False

    def tick(self, dt, now):
        self.t = now
        self.focus_amt = ease(self.focus_amt, 1.0 if self.has_focus() else 0.0, dt, 8.0)
        self.queue_draw()

    def offset(self):
        return drift(self.t, self.seed, self.amp * self.shell.scale)

    def link_center(self, photo):
        pos = self.translate_coordinates(photo, int(self.cx), int(self.cy))
        if not pos:
            return None
        dx, dy = self.offset()
        return pos[0] + dx, pos[1] + dy

    def inside(self, x, y):
        raise NotImplementedError

    def update_now(self, state):
        pass

    def on_draw(self, _w, cr):
        if cairo is None:
            return False
        base = cr.get_matrix()
        dx, dy = self.offset()
        effects = getattr(self.shell, "home_cfg", {}).get("visual_effects", True)
        f = self.focus_amt if effects else float(self.has_focus())
        sc = 1 + (FOCUS_SCALE - 1) * f
        cr.translate(self.cx + dx, self.cy + dy)
        cr.scale(sc, sc)
        self.draw_body(cr, base, f, sc)
        return False

    def draw_body(self, cr, base, f, sc):
        raise NotImplementedError

    def glow(self, cr, f, shape):
        """Soft cool glow as a few widening translucent outlines of `shape(cr, grow)`."""
        if f < 0.02:
            return
        for i in range(1, 6):
            shape(cr, i * 3.0)
            cr.set_source_rgba(*GLOW, 0.20 * f * (1 - i / 6))
            cr.set_line_width(3.2)
            cr.stroke()


class TileNode(Node):
    """~16:9 frosted tile showing artwork through the glass; title bottom-left, optional progress + now-playing bars."""

    def __init__(self, shell, w, h, title="", sub="", art_url=None, progress=None, eq=None, kicker="",
                 on_activate=None, seed=0, chamfer=False):
        super().__init__(shell, w, h, seed, on_activate)
        self.chamfer = chamfer
        self.title, self.sub, self.progress, self.now, self.kicker = title, sub, progress, eq, kicker
        self.sharp = self.soft = None
        self.photo = getattr(shell, "photo_canvas", None)
        if art_url:
            aw, ah = round(w * FOCUS_SCALE), round(h * FOCUS_SCALE)
            load_any(art_url, aw, ah, self.set_art)

    def set_art(self, pb):
        if pb is not None and cairo:
            self.sharp = Gdk.cairo_surface_create_from_pixbuf(pb, 1, None)
            self.soft = Gdk.cairo_surface_create_from_pixbuf(blur_pixbuf(pb), 1, None)
            self.queue_draw()
        return False

    def update_now(self, state):
        self.now = state.get("status")
        self.title = state.get("title") or self.title
        self.sub = state.get("artist") or ""
        self.kicker = "YOUTUBE · " + ("PLAYING" if self.now == "Playing" else "PAUSED" if self.now == "Paused" else "OPEN")

    def inside(self, x, y):
        k = 1 + (FOCUS_SCALE - 1) * max(self.focus_amt, 0.4)
        return abs(x - self.cx) <= self.w0 * k / 2 and abs(y - self.cy) <= self.h0 * k / 2

    def draw_body(self, cr, base, f, sc):
        w, h, rad = self.w0, self.h0, 10
        x, y = -w / 2, -h / 2
        shape = (lambda c, xx, yy, ww, hh, g: chamfer_rect(c, xx, yy, ww, hh, 16 + g)) if self.chamfer else \
            (lambda c, xx, yy, ww, hh, g: rrect(c, xx, yy, ww, hh, rad + g))
        self.glow(cr, f, lambda c, g: shape(c, x - g, y - g, w + 2 * g, h + 2 * g, g))
        shape(cr, x, y, w, h, 0)
        cr.save()
        cr.clip_preserve()
        paint_frost(cr, base, self, self.photo)
        cr.set_source_rgba(1, 1, 1, 0.10 + 0.04 * f)
        cr.paint()
        aw, ah = w * FOCUS_SCALE, h * FOCUS_SCALE
        for surf, alpha in ((self.soft, 0.6 * (1 - f)), (self.sharp, f)):
            if surf is not None and alpha > 0.01:
                cr.save()
                cr.translate(x, y)
                cr.scale(w / aw, h / ah)
                cr.set_source_surface(surf, 0, 0)
                cr.paint_with_alpha(alpha)
                cr.restore()
        shade = cairo.LinearGradient(0, y + h * 0.45, 0, y + h)
        shade.add_color_stop_rgba(0, 0, 0, 0, 0)
        shade.add_color_stop_rgba(1, 0, 0, 0, 0.62)
        cr.set_source(shade)
        cr.paint()
        if self.progress:
            cr.set_source_rgba(1, 1, 1, 0.22)
            cr.rectangle(x, y + h - 3, w, 3)
            cr.fill()
            cr.set_source_rgba(1, 0.86, 0.55, 0.95)
            cr.rectangle(x, y + h - 3, w * min(1.0, self.progress), 3)
            cr.fill()
        cr.restore()
        cr.new_path()
        shape(cr, x + 0.5, y + 0.5, w - 1, h - 1, 0)
        cr.set_source_rgba(1, 1, 1, 0.22 + 0.7 * f)
        cr.set_line_width(1)
        cr.stroke()
        a = 0.78 + 0.22 * f
        tw = w - 24
        if self.kicker:
            self.text(cr, self.kicker, 12, Pango.Weight.BOLD, x + 12, y + 10, tw - 30, (1, 1, 1, 0.85),
                      align=Pango.Alignment.LEFT)
        lines = [(self.title, 18, Pango.Weight.SEMIBOLD, (1, 1, 1, a))]
        if self.sub:
            lines.append((self.sub, 13, Pango.Weight.NORMAL, (1, 1, 1, 0.6 + 0.3 * f)))
        total = sum(px * 1.35 for _, px, _, _ in lines)
        ty = y + h - 10 - total - (4 if self.progress else 0)
        for text, px, wt, col in lines:
            self.text(cr, text, px, wt, x + 12, ty, tw, col, align=Pango.Alignment.LEFT)
            ty += px * 1.35
        if self.now:
            draw_eq(cr, x + w - 30, y + 28, 16, self.t, self.now == "Playing")


class RoundNode(Node):
    """Small glass action node (hexagon or octagon): icon with a label below. r = circumradius."""
    amp = 6

    def __init__(self, shell, r, title="", kind=None, on_activate=None, seed=0, shape="hexagon"):
        super().__init__(shell, 2 * r, 2 * r, seed, on_activate, label_h=round(shell.px(34)))
        self.r, self.title, self.kind, self.shape = r, title, kind, shape
        self.photo = getattr(shell, "photo_canvas", None)

    def inside(self, x, y):
        k = 1 + (FOCUS_SCALE - 1) * max(self.focus_amt, 0.4)
        return point_in_poly((x - self.cx) / k, (y - self.cy) / k, poly_points(self.shape, self.r))

    def draw_body(self, cr, base, f, sc):
        r = self.r
        cr.set_line_join(cairo.LINE_JOIN_ROUND)
        self.glow(cr, f, lambda c, g: poly_path(c, poly_points(self.shape, r + g)))
        poly_path(cr, poly_points(self.shape, r))
        cr.save()
        cr.clip_preserve()
        paint_frost(cr, base, self, self.photo)
        tr, tg, tb = TINTS.get(self.kind, (0.8, 0.85, 1.0))
        cr.set_source_rgba(1, 1, 1, 0.10 + 0.05 * f)
        cr.paint()
        g = cairo.RadialGradient(-r * 0.3, -r * 0.4, 0, -r * 0.3, -r * 0.4, r * 1.3)
        g.add_color_stop_rgba(0, tr, tg, tb, 0.30)
        g.add_color_stop_rgba(1, tr, tg, tb, 0.05)
        cr.set_source(g)
        cr.paint()
        cr.restore()
        poly_path(cr, poly_points(self.shape, r - 0.6))
        cr.set_source_rgba(1, 1, 1, 0.24 + 0.7 * f)
        cr.set_line_width(1)
        cr.stroke()
        if self.kind:
            _icon(cr, self.kind, 0, 0, r * 0.42)
        self.text(cr, self.title, 17, Pango.Weight.SEMIBOLD, -r * 1.4, r + 8, r * 2.8,
                  (1, 1, 1, 0.78 + 0.22 * f))


class NodeRow(RowBase):
    """Row of glass nodes (tiles or round) in an irregular strip."""
    lean_strength = 0

    def __init__(self, shell, specs, height, seed=1, round_nodes=False, width_frac=0.86, shape="hexagon",
                 chamfer=False):
        width_frac = 0.55 if round_nodes else width_frac
        super().__init__(shell, height)
        s = shell.scale
        n = len(specs)
        if round_nodes:
            sizes = [(2 * round(56 * s), 2 * round(56 * s))] * n
        else:
            units = sum(1.28 if sp.get("now") else 1 for sp in specs)
            tw = min(round(290 * s), round(shell.sw * width_frac / (units + 0.35 * max(0, n - 1) + 0.01)))
            sizes = [(round(tw * (1.28 if sp.get("now") else 1)), round(tw * 9 / 16 * (1.28 if sp.get("now") else 1)))
                     for sp in specs]
        lay = row_layout(sizes, shell.sw, height - (shell.px(34) if round_nodes else 0), seed=seed,
                         width_frac=width_frac, spread=0.1 if round_nodes else 0.2,
                         gap_scale=2.6 if round_nodes else 1.0)
        for i, (spec, (w, h), p) in enumerate(zip(specs, sizes, lay)):
            if round_nodes:
                node = RoundNode(shell, w // 2, title=spec["title"], kind=spec.get("kind"),
                                 on_activate=spec["on_activate"], seed=seed * 17 + i, shape=shape)
            else:
                now = spec.get("now")
                node = TileNode(shell, w, h, title=spec["title"], sub=spec.get("sub") or "",
                                art_url=spec.get("art_url"), progress=spec.get("progress"), eq=now,
                                kicker=("YOUTUBE · " + ("PLAYING" if now == "Playing" else "PAUSED" if now == "Paused"
                                                        else "OPEN")) if now else "",
                                on_activate=spec["on_activate"], seed=seed * 17 + i, chamfer=chamfer)
            self.add_node(node, round(p["cx"] - node.cx), round(p["cy"] - node.cy))


# ---------------------------------------------------------------- the line layer

CELL = 64


def compute_geometry(shell, nodes, dust, t):
    """Positions + links for one frame. nodes: [(x, y, focused, hub)] in canvas coords; dust: [(x, y, seed)]."""
    s = shell.scale
    pts = [(x, y) for x, y, _, _ in nodes]
    ks = [3] * len(pts)
    for x, y, seed in dust:
        dx, dy = drift(t, seed, 10 * s)
        pts.append((x + dx, y + dy))
        ks.append(2)
    return {"pts": pts, "n": len(nodes), "t": t, "scale": s,
            "links": link_pairs(pts, ks, LINE_MAX * s),
            "focus": next((i for i, n in enumerate(nodes) if n[2]), None),
            "hub": next((i for i, n in enumerate(nodes) if n[3]), None)}


def geometry_cells(geom, cell=CELL, margin=10):
    """Coarse grid cells (cx, cy) the lines, dust and sparks of this frame can touch: the dirty region to repaint."""
    cells = set()

    def mark(x, y):
        for gx in range(int((x - margin) // cell), int((x + margin) // cell) + 1):
            for gy in range(int((y - margin) // cell), int((y + margin) // cell) + 1):
                cells.add((gx, gy))
    for x, y in geom["pts"][geom["n"]:]:
        mark(x, y)
    pts = geom["pts"]
    for i, j, d in geom["links"]:
        steps = max(1, int(d // (cell / 2)))
        for k in range(steps + 1):
            u = k / steps
            mark(pts[i][0] + (pts[j][0] - pts[i][0]) * u, pts[i][1] + (pts[j][1] - pts[i][1]) * u)
    return cells


def draw_network(cr, geom, chrome, dust_amt):
    """Lines between nodes/dust, dust dots and sparks (sparks run toward the focused node, away from the hub)."""
    if not geom or (chrome <= 0.003 and dust_amt <= 0.003):
        return
    s, t, pts, n = geom["scale"], geom["t"], geom["pts"], geom["n"]
    focus, hub, links = geom["focus"], geom["hub"], geom["links"]
    maxd = LINE_MAX * s
    cr.save()
    cr.set_line_cap(cairo.LINE_CAP_ROUND)
    for i, j, d in links:
        touch = focus is not None and focus in (i, j)
        a = (0.75 if touch else line_alpha(d, maxd)) * chrome
        if a < 0.01:
            continue
        cr.set_source_rgba(1, 1, 1, a)
        cr.set_line_width(max(1.3, 1.3 * s) * (2 if touch else 1))
        cr.move_to(*pts[i])
        cr.line_to(*pts[j])
        cr.stroke()
    for x, y in pts[n:]:
        cr.set_source_rgba(1, 1, 1, 0.5 * dust_amt)
        d = 3.2 * s                                   # tiny diamonds, to sit next to the hexagons
        cr.move_to(x, y - d)
        cr.line_to(x + d, y)
        cr.line_to(x, y + d)
        cr.line_to(x - d, y)
        cr.close_path()
        cr.fill()
    for i, j, d in links:
        for centre, inward, a0 in ((focus, True, 0.95), (hub, False, 0.6)):
            if centre is None or centre not in (i, j):
                continue
            other = j if centre == i else i
            u = (t * 0.55 + ((i * 31 + j * 17) % 10) / 10.0) % 1.0
            a, b = (pts[other], pts[centre]) if inward else (pts[centre], pts[other])
            for tail, fade in ((0.0, 1.0), (0.035, 0.55), (0.07, 0.25)):
                uu = u - tail
                if uu < 0:
                    continue
                cr.set_source_rgba(1, 1, 1, a0 * fade * chrome)
                cr.arc(a[0] + (b[0] - a[0]) * uu, a[1] + (b[1] - a[1]) * uu, (2.6 - tail * 10) * s, 0, 2 * math.pi)
                cr.fill()
            if centre == focus:
                break
    cr.restore()
