#!/usr/bin/env python3
"""Render the bundled starter Retro backgrounds (original artwork, 3840x2160) into tvapp/assets/retro/.

Usage: scripts/make-retro-seed.py [outdir]
Procedural and seeded: every picture is drawn from parameters (stripe counts, vanishing points, palettes), so the
result is reproducible and has no third-party licence. Needs only pycairo and GdkPixbuf (already app deps).
"""
import math
import os
import random
import sys

import cairo
import gi
gi.require_version("GdkPixbuf", "2.0")
from gi.repository import GdkPixbuf

W, H = 3840, 2160


def rgb(h):
    return ((h >> 16) & 255) / 255, ((h >> 8) & 255) / 255, (h & 255) / 255


def canvas():
    s = cairo.ImageSurface(cairo.FORMAT_RGB24, W, H)
    return s, cairo.Context(s)


def vgrad(c, stops, y0=0, y1=H):
    g = cairo.LinearGradient(0, y0, 0, y1)
    for off, col, *a in stops:
        g.add_color_stop_rgba(off, *rgb(col), a[0] if a else 1)
    c.set_source(g)
    c.rectangle(0, y0, W, y1 - y0)
    c.fill()


def stars(c, rng, n, ymax, size=2.6):
    for _ in range(n):
        x, y = rng.random() * W, rng.random() ** 1.3 * ymax
        r = size * (0.25 + rng.random() ** 3)
        c.set_source_rgba(1, 1, 1, 0.25 + 0.7 * rng.random())
        c.arc(x, y, r, 0, 6.2832)
        c.fill()


def ridge(rng, y_base, amp, rough=0.55, steps=9):
    """Midpoint-displacement skyline across the width: [(x, y)]."""
    pts = [(0, y_base + (rng.random() - .5) * amp), (W, y_base + (rng.random() - .5) * amp)]
    a = amp
    for _ in range(steps):
        out = []
        for p, q in zip(pts, pts[1:]):
            out += [p, ((p[0] + q[0]) / 2, (p[1] + q[1]) / 2 + (rng.random() - .5) * a)]
        out.append(pts[-1])
        pts, a = out, a * rough
    return pts


def fill_ridge(c, pts, bottom, col, alpha=1):
    c.move_to(0, bottom)
    for x, y in pts:
        c.line_to(x, y)
    c.line_to(W, bottom)
    c.close_path()
    c.set_source_rgba(*rgb(col), alpha)
    c.fill()


def grain(c, rng, amount=0.035):
    """Fine film grain from a tiled noise patch (keeps gradients from banding in the 8-bit output)."""
    n = 512
    t = cairo.ImageSurface(cairo.FORMAT_ARGB32, n, n)
    d = t.get_data()
    for i in range(0, n * n * 4, 4):
        v = rng.randrange(256)
        d[i] = d[i + 1] = d[i + 2] = v * 0 + v
        d[i + 3] = 255
    t.mark_dirty()
    p = cairo.SurfacePattern(t)
    p.set_extend(cairo.EXTEND_REPEAT)
    c.save()
    c.set_operator(cairo.OPERATOR_OVERLAY)
    c.set_source(p)
    c.paint_with_alpha(amount)
    c.restore()


def vignette(c, strength=0.45):
    g = cairo.RadialGradient(W / 2, H / 2, H * 0.35, W / 2, H / 2, W * 0.62)
    g.add_color_stop_rgba(0, 0, 0, 0, 0)
    g.add_color_stop_rgba(1, 0, 0, 0, strength)
    c.set_source(g)
    c.paint()


def glow_line(c, pts, col, width, passes=((26, .06), (14, .12), (6, .3), (1, 1))):
    for k, a in passes:
        c.set_line_width(width * k / 2 if k > 1 else width)
        c.set_source_rgba(*rgb(col), a)
        c.move_to(*pts[0])
        for p in pts[1:]:
            c.line_to(*p)
        c.stroke()


# --------------------------------------------------------------------------------------------- pictures
def neon_grid_horizon():
    rng = random.Random(11)
    s, c = canvas()
    hy = H * 0.56
    vgrad(c, [(0, 0x120626), (.55, 0x3b0f5e), (1, 0x8a1fa0)], 0, hy)
    stars(c, rng, 1500, hy * .8)
    for i, (b, a, col) in enumerate(((hy - 40, 150, 0x2a0f45), (hy - 10, 110, 0x1c0a33))):
        fill_ridge(c, ridge(rng, b - 170 + i * 40, a * 2.2), hy, col)
    vgrad(c, [(0, 0xff4bd8, .0), (1, 0xff4bd8, .55)], hy - 160, hy)
    vgrad(c, [(0, 0x0b0418), (1, 0x3a0b66)], hy, H)
    c.set_line_cap(cairo.LINE_CAP_ROUND)
    vx = W / 2
    for i in range(-44, 45):                         # converging verticals
        x = vx + i * 330
        c.set_line_width(2 + abs(i) * 0.02)
        c.set_source_rgba(1, .45, .95, 0.85)
        c.move_to(vx + i * 14, hy)
        c.line_to(x * 1 + (x - vx) * 2.6 - (x - vx), H)
        c.stroke()
    for k in range(1, 26):                           # horizontals with perspective spacing
        t = (k / 25) ** 2.2
        y = hy + (H - hy) * t
        c.set_line_width(1.5 + 4 * t)
        c.set_source_rgba(.95, .4, 1, 0.35 + .55 * (1 - t) ** .3)
        c.move_to(0, y)
        c.line_to(W, y)
        c.stroke()
    for k, a in ((90, .10), (40, .22), (14, .6)):    # horizon glow
        vgrad(c, [(0, 0xffd6fb, 0), (.5, 0xffd6fb, a), (1, 0xffd6fb, 0)], hy - k, hy + k)
    c.set_source_rgba(1, .88, 1, .85)
    c.rectangle(0, hy - 5, W, 10)
    c.fill()
    vignette(c, .5)
    grain(c, rng)
    return s


def neon_lake_mountains():
    rng = random.Random(23)
    s, c = canvas()
    hy = H * 0.66
    vgrad(c, [(0, 0x02030f), (.5, 0x0a1450), (.9, 0x1d6fd0), (1, 0x2aa1ff)], 0, hy)
    stars(c, rng, 2600, hy * .85, 2.2)
    far = ridge(rng, hy - 330, 360, .5)
    fill_ridge(c, far, hy, 0x0a1038)
    near = ridge(rng, hy - 260, 820, .58)
    fill_ridge(c, near, hy, 0x06081f)
    for k in range(22):                              # wireframe contour lines following the near ridge
        c.set_line_width(1.8)
        c.set_source_rgba(.5, .6, 1, .55 - k * .02)
        c.move_to(0, near[0][1] + k * 15)
        for x, y in near[::2]:
            c.line_to(x, min(hy - 2, y + k * 15 + 6 * math.sin(x * .03 + k)))
        c.stroke()
    vgrad(c, [(0, 0x0b1450), (1, 0x2a0d55)], hy, H)
    for k in range(40):                              # water scanlines
        y = hy + (H - hy) * (k / 40) ** 1.6
        c.set_source_rgba(.5, .4, 1, .12)
        c.set_line_width(1 + k * .06)
        c.move_to(0, y)
        c.line_to(W, y)
        c.stroke()
    road = []
    for i in range(121):                             # neon ribbon curving to the horizon
        t = i / 120
        x = W * (0.01 + 0.98 * t)
        y = hy + 14 + (H * 0.97 - hy) * (1 - t) ** 2.4
        road.append((x, y))
    road += [(W * .985, hy + 12), (W + 20, hy + 12)]
    glow_line(c, road, 0xff2bd0, 7)
    glow_line(c, [(x, y + 24) for x, y in road], 0x42a8ff, 3, ((18, .05), (8, .15), (1, .8)))
    for gx, col in ((W * .12, 0xff2bd0), (W * .88, 0x2a8bff)):
        g = cairo.RadialGradient(gx, hy, 0, gx, hy, 300)
        g.add_color_stop_rgba(0, *rgb(col), .55)
        g.add_color_stop_rgba(1, *rgb(col), 0)
        c.set_source(g)
        c.paint()
    vignette(c, .35)
    grain(c, rng, .03)
    return s


def stripe_corner():
    """Retro stripes running up a wall and bending onto the floor (one-point perspective corner)."""
    rng = random.Random(5)
    s, c = canvas()
    vgrad(c, [(0, 0x8a7440), (.6, 0xa58b4b), (1, 0x6c5a2e)])
    cols = [0x6b7f5e, 0x6f9ea0, 0xd19a14, 0xcc5a12, 0xb82d1e, 0x6b150d]
    n, sw, gap = len(cols), 130, 38
    x0 = W / 2 - n * (sw + gap) / 2
    cy = H * 0.58                                    # where wall meets floor
    vx, vy = W / 2, H * 0.35
    for i, col in enumerate(cols):
        xa, xb = x0 + i * (sw + gap), x0 + i * (sw + gap) + sw
        c.set_source_rgb(*rgb(col))
        c.rectangle(xa, 0, sw, cy)                   # wall
        c.fill()
        fa = vx + (xa - vx) * 3.2                    # floor flares out from the same x towards the viewer
        fb = vx + (xb - vx) * 3.2
        c.move_to(xa, cy)
        c.line_to(xb, cy)
        c.line_to(fb, H + 40)
        c.line_to(fa, H + 40)
        c.close_path()
        c.fill()
    # lighting + paper texture
    vgrad(c, [(0, 0x000000, .25), (.4, 0x000000, 0), (1, 0x000000, .35)])
    for _ in range(900):
        x, y, r = rng.random() * W, rng.random() * H, 20 + rng.random() * 140
        c.set_source_rgba(0, 0, 0, .012 + rng.random() * .02)
        c.arc(x, y, r, 0, 6.2832)
        c.fill()
    vignette(c, .6)
    grain(c, rng, .06)
    return s


def scallops():
    """Scalloped / arched pattern: rows of overlapping half-discs in a warm 70s palette."""
    rng = random.Random(8)
    s, c = canvas()
    pal = [0xe9b44c, 0xd8572a, 0xb3321f, 0x2f6f73, 0x143b4a, 0xf0dca8]
    c.set_source_rgb(*rgb(0x143b4a))
    c.paint()
    r = 230
    rows = int(H / (r * .42)) + 3
    for j in range(rows - 1, -1, -1):
        y = j * r * .42
        for i in range(-1, int(W / (2 * r)) + 2):
            x = i * 2 * r + (r if j % 2 else 0)
            for k in range(3):
                rr = r * (1 - k * .26)
                c.set_source_rgb(*rgb(pal[(j // 2 * 2 + k * 2 + (i % 2)) % len(pal)] if k < 2 else pal[5]))
                c.arc(x, y, rr, 0, math.pi)          # half disc, curved side down
                c.fill()
    vgrad(c, [(0, 0, .0), (1, 0x000000, .28)])
    vignette(c, .35)
    grain(c, rng, .05)
    return s


def wave_bands():
    s, c = canvas()
    rng = random.Random(3)
    cols = [0xb2452a, 0xe0a62b, 0xd8431f, 0xb2181b, 0xe8d28c, 0x0f1c2b, 0x15416b, 0x0f1c2b, 0x3a9689, 0x0f1c2b,
            0x3a9689, 0x6f7040, 0xe3b02b, 0xb83a1e]
    vgrad(c, [(0, 0xa8412a), (1, 0x7a2e22)])
    n = len(cols)
    for i in range(n):
        top = H * (.12 + i * .066)
        c.move_to(0, H)
        c.line_to(0, top + 120)
        for x in range(0, W + 40, 40):
            t = x / W
            c.line_to(x, top + 300 * math.sin(t * 3.1 + 2.2) * (1 - t * .3) + 90 * i * (1 - t) * .35)
        c.line_to(W, H)
        c.close_path()
        c.set_source_rgb(*rgb(cols[i]))
        c.fill_preserve()
        c.set_source_rgba(0, 0, 0, .35)
        c.set_line_width(4)
        c.stroke()
    vgrad(c, [(0, 0, .0), (1, 0x000000, .2)])
    grain(c, rng, .03)
    return s


def retro_sun():
    rng = random.Random(17)
    s, c = canvas()
    hy = H * 0.6
    vgrad(c, [(0, 0x0d0221), (.5, 0x3a0d63), (.85, 0xc4306f), (1, 0xff7a3d)], 0, hy)
    stars(c, rng, 900, hy * .55)
    cx, cy, R = W / 2, hy - 20, 560
    sun = cairo.LinearGradient(0, cy - R, 0, cy + R)
    sun.add_color_stop_rgb(0, *rgb(0xffe14a))
    sun.add_color_stop_rgb(.55, *rgb(0xff6a3d))
    sun.add_color_stop_rgb(1, *rgb(0xe0208f))
    c.save()
    c.arc(cx, cy, R, 0, 6.2832)
    c.clip()
    c.set_source(sun)
    c.paint()
    skyc = cairo.LinearGradient(0, 0, 0, hy)         # same sky ramp, so the cuts read as gaps
    for off, col in ((0, 0x0d0221), (.5, 0x3a0d63), (.85, 0xc4306f), (1, 0xff7a3d)):
        skyc.add_color_stop_rgb(off, *rgb(col))
    c.set_source(skyc)
    for k in range(9):                               # horizontal cuts, thicker towards the bottom
        y = cy - R * (.62 - k * .075)
        c.rectangle(cx - R, y, 2 * R, 5 + k * 3.4)
        c.fill()
    c.restore()
    fill_ridge(c, ridge(rng, hy - 60, 150, .5), hy, 0x1a0836)
    vgrad(c, [(0, 0x12062a), (1, 0x04010d)], hy, H)
    c.set_line_cap(cairo.LINE_CAP_ROUND)
    for k in range(1, 22):
        t = (k / 21) ** 2.1
        y = hy + (H - hy) * t
        c.set_line_width(1.5 + 4 * t)
        c.set_source_rgba(1, .35, .85, .3 + .6 * (1 - t))
        c.move_to(0, y)
        c.line_to(W, y)
        c.stroke()
    for i in range(-40, 41):
        c.set_line_width(2.5)
        c.set_source_rgba(1, .35, .85, .75)
        c.move_to(cx + i * 16, hy)
        c.line_to(cx + i * 16 * 14, H)
        c.stroke()
    vgrad(c, [(0, 0xff9ad8, 0), (.5, 0xff9ad8, .5), (1, 0xff9ad8, 0)], hy - 50, hy + 50)
    vignette(c, .45)
    grain(c, rng, .03)
    return s


PICTURES = {
    "Neon Grid Horizon": neon_grid_horizon,
    "Neon Lake Mountains": neon_lake_mountains,
    "Stripe Corner": stripe_corner,
    "Sunburst Scallops": scallops,
    "Wave Bands": wave_bands,
    "Retro Sun": retro_sun,
}


def main():
    out = sys.argv[1] if len(sys.argv) > 1 else os.path.join(os.path.dirname(__file__), "..", "tvapp", "assets", "retro")
    os.makedirs(out, exist_ok=True)
    for name, fn in PICTURES.items():
        surf = fn()
        png = os.path.join(out, name.replace(" ", "-").lower() + ".png")
        tmp = png + ".tmp.png"
        surf.write_to_png(tmp)
        pb = GdkPixbuf.Pixbuf.new_from_file(tmp)
        jpg = png[:-4] + ".jpg"
        pb.savev(jpg, "jpeg", ["quality"], ["93"])
        os.unlink(tmp)
        print(jpg, os.path.getsize(jpg) // 1024, "KB")


if __name__ == "__main__":
    main()
