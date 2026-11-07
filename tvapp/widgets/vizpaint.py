"""What the music visualizer draws, independent of how: one description, two painters.

`paint(p, g, palette, hub, glow_r)` walks one frame of the visualizer (`HomeScreen.shape_geom` with a "viz" frame)
and calls the painter `p`:
  p.segs(segs, alpha_steps, amax)   segs: [(rgb, alpha, width, x0, y0, x1, y1)], round caps
  p.fill(poly, rgb, alpha)          a filled polygon [(x, y), ...]
  p.outline(poly, rgb, alpha, wd)   a closed polygon's outline
  p.glow(x, y, rgb, alpha)          the soft round light (radius glow_r), as a mask in any colour
The cairo painter lives in homescreen.py (software path); gl_ambient.py has the GPU one. Pure Python: no GTK, so
the GPU renderer process can import it.
"""
import math

try:
    from . import shapes
except ImportError:                       # imported as a top-level module by the renderer process
    import shapes  # type: ignore

WHITE = (1, 1, 1)


def poly_points(shape, r, cx=0.0, cy=0.0):
    """Regular polygon vertices: "hexagon" (flat-top, points left/right) or "octagon" (flat sides, flat top)."""
    n, rot = (8, math.pi / 8) if shape == "octagon" else (6, 0.0)
    return [(cx + r * math.cos(rot + 2 * math.pi * i / n), cy + r * math.sin(rot + 2 * math.pi * i / n))
            for i in range(n)]


def batch_segments(segs, alpha_steps=8, amax=1.0):
    """Group line segments so each (colour, alpha, width) is one stroke. segs: (rgb, alpha, width, x0, y0, x1, y1).
    Alpha is quantized to alpha_steps levels over 0..amax (never to zero for a visible segment), width to 0.5 px;
    segments with alpha <= 0 are dropped.
    Returns [(rgb, alpha, width, [(x0, y0, x1, y1), ...])] in first-seen order."""
    groups = {}
    for rgb, a, wd, x0, y0, x1, y1 in segs:
        if a <= 0:
            continue
        qa = max(1, round(min(a, amax) / amax * alpha_steps)) * amax / alpha_steps
        key = (tuple(rgb), qa, max(0.5, round(wd * 2) / 2))
        groups.setdefault(key, []).append((x0, y0, x1, y1))
    return [(k[0], k[1], k[2], v) for k, v in groups.items()]


def viz_base_alpha(lv):
    """The visualizer's link opacity before per-link weights: a background, music modulates, never dominates."""
    gain = 0.55 + 0.45 * min(1.0, lv["bass"] + lv["mid"])
    return (0.11 + 0.06 * lv["mid"] + 0.10 * lv["beat"]) * gain


def viz_link_alpha(base_a, la, alphas, depth, i, j):
    dd = (depth[i] + depth[j]) / 2 if depth else 0.75
    return base_a * la * min(alphas[i], alphas[j]) * (0.30 + 0.70 * dd)


def paint(p, g, pal, hub):
    s, t, pts, lv, fr = g["scale"], g["t"], g["pts"], g["lv"], g["viz"]
    alphas, depth, glyph = g["alphas"], fr["depth"], fr.get("glyph")
    k = fr["colour"]
    line, spark, halo = pal[k % len(pal)], pal[(k + 1) % len(pal)], pal[(k + 2) % len(pal)]
    bass, mid, beat = lv["bass"], lv["mid"], lv["beat"]
    if g["tails"]:
        tr = fr["trail"]
        wd = max(1.0, 1.6 * s)
        segs = []
        for i, (x, y) in enumerate(pts):
            a = alphas[i] * 0.15 * tr
            if a > 0.01:
                tx, ty = g["tails"][i]
                segs.append((halo, a, wd, x, y, tx, ty))
        p.segs(segs, 6, 0.15)
    gain = 0.55 + 0.45 * min(1.0, bass + mid)          # a background: music modulates, never dominates
    base_a = viz_base_alpha(lv)
    segs = []
    for i, j, la in g["links"]:
        dd = (depth[i] + depth[j]) / 2 if depth else 0.75
        a = viz_link_alpha(base_a, la, alphas, depth, i, j)
        if a < 0.01:
            continue
        segs.append((line, min(0.8, a), max(1.0, 1.1 * s * (0.6 + 0.6 * dd)), *pts[i], *pts[j]))
    p.segs(segs, 8, 0.8)
    if fr["pulses"]:
        wd = max(1.6, 2.4 * s)
        p.segs([(spark, 0.6 * a, wd, x0, y0, x1, y1) for x0, y0, x1, y1, a in fr["pulses"]], 6, 0.6)
    outlines = []                   # the white glyph outlines, stroked in batches after the loop
    heads = fr.get("heads")
    for i, (x, y) in enumerate(pts):
        a = alphas[i]
        if a < 0.02:
            continue
        dd = depth[i] if depth else 0.75
        tw = shapes.twinkle(i, t, lv["treble"]) if lv["treble"] > 0.02 else 0.0
        if i % 5 == 0 and dd > 0.45 and (beat > 0.05 or tw > 0.3):
            p.glow(x, y, halo, min(0.5, (0.35 * beat + 0.25 * tw) * a * dd))
        if heads and heads[i] > 0.01:
            p.glow(x, y, spark, (0.22 + 0.35 * beat) * a * heads[i])
        d = (2.0 + 2.2 * dd) * s * (1 + 0.18 * bass + 0.5 * tw)
        gl = glyph[i] if glyph else (4 if i % 5 == 0 else 0)
        if gl in (1, 2, 3):
            ang = fr["ang"][i]
            al = min(0.7, (0.30 + 0.35 * dd + 0.3 * tw) * a * gain)
            wd = max(1.0, 1.1 * s)
            if gl == 3:
                dx, dy = math.cos(ang) * d * 2.4, math.sin(ang) * d * 2.4
                outlines.append((WHITE, al, wd, x - dx, y - dy, x + dx, y + dy))
            else:
                gr = d * (2.2 if gl == 1 else 1.9)
                n = 3 if gl == 1 else 6
                ring = [(x + gr * math.cos(ang + 6.2832 * q / n), y + gr * math.sin(ang + 6.2832 * q / n))
                        for q in range(n)]
                p.fill(ring, line, 0.07 * a * gain)
                outlines.extend((WHITE, al, wd, *ring[q - 1], *ring[q]) for q in range(n))
        elif gl == 4:
            poly = poly_points(hub, d * 2.0, x, y)
            p.fill(poly, line, min(0.22, 0.06 + 0.10 * mid + 0.06 * tw) * a)
            p.outline(poly, WHITE, min(0.6, (0.35 + 0.30 * dd) * a), max(1.0, 1.3 * s))
        else:
            p.fill([(x, y - d), (x + d, y), (x, y + d), (x - d, y)], WHITE,
                   min(0.8, (0.30 + 0.40 * dd + 0.3 * tw) * a * gain))
    p.segs(outlines, 8, 0.7)
    for _x0, _y0, x1, y1, a in fr["pulses"]:
        p.glow(x1, y1, spark, 0.45 * a)
