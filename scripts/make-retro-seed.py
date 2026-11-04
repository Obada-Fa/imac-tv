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


# New originals use a 960 × 540 design space, then rasterize to the same 4K canvas as the first set.
def poster(background):
    s, c = canvas()
    c.set_source_rgb(*rgb(background))
    c.paint()
    c.scale(W / 960, H / 540)
    return s, c


def polygon(c, points, color):
    c.move_to(*points[0])
    for point in points[1:]:
        c.line_to(*point)
    c.close_path()
    c.set_source_rgb(*rgb(color))
    c.fill()


def disc(c, x, y, radius, color):
    c.arc(x, y, radius, 0, math.tau)
    c.set_source_rgb(*rgb(color))
    c.fill()


def paper(c, seed):
    c.identity_matrix()
    grain(c, random.Random(seed), .047)


def chromatic_interchange():
    """Stacked screen-printed racetrack lanes with contrasting ink traps at crossings."""
    s, c = poster(0x162D32)
    cream, red, orange, gold, teal = 0xF1D7A4, 0xDC4A30, 0xE98029, 0xD8AB47, 0x478881
    lines = [
        ((-70, 143), [(170, 143, 238, 143, 322, 264), (461, 463, 579, 524, 1005, 462)], [red, orange, gold, cream]),
        ((411, -68), [(417, 66, 547, 120, 689, 110), (820, 102, 832, 230, 750, 280),
                      (630, 362, 595, 405, 635, 600)], [teal, cream, gold]),
        ((-54, 512), [(103, 492, 287, 366, 344, 221), (399, 77, 550, 27, 630, 105),
                      (731, 215, 759, 304, 1007, 200)], [cream, red, orange, gold, teal]),
    ]
    for start, curves, colors in lines:
        for i, col in enumerate([0x152B30] + colors):
            c.move_to(*start)
            for curve in curves:
                c.curve_to(*curve)
            c.set_line_width(13 * (len(colors) - i + .5) + (7 if not i else 0))
            c.set_source_rgb(*rgb(col))
            c.stroke()
    # Registration targets printed deep into the empty corners.
    for x, y in ((71, 60), (899, 487)):
        c.set_source_rgba(*rgb(cream), .5)
        c.set_line_width(.8)
        c.arc(x, y, 10, 0, math.tau)
        c.move_to(x - 16, y)
        c.line_to(x + 16, y)
        c.move_to(x, y - 16)
        c.line_to(x, y + 16)
        c.stroke()
    paper(c, 21)
    return s


def velvet_oscillator():
    """Vary the spacing and curvature of silk-screen lines to sculpt an anamorphic fold."""
    s, c = poster(0x1D142B)
    grad = cairo.LinearGradient(70, 0, 950, 0)
    palette = ((0, 0x61345F), (.20, 0xBD6487), (.38, 0xF8AC81), (.52, 0xCF465B),
               (.68, 0xFFDDB4), (.82, 0x996CA7), (1, 0x405982))
    for pos, col in palette:
        grad.add_color_stop_rgb(pos, *rgb(col))
    for k in range(-75, 180):
        for x in range(-3, 966, 3):
            # Local phase modulation around two off-centre lenses pinches and opens the grooves.
            y = k * 5.9
            dx = (x - 590) / 220
            dy = (y - 250) / 182
            bulge = math.exp(-(.9 * dx * dx + .6 * dy * dy))
            y += 132 * math.sin(dx * 2.0 + .75 * dy) * bulge + 48 * math.sin(x / 217 + k / 27)
            if x == -3:
                c.move_to(x, y)
            else:
                c.line_to(x, y)
        c.set_source(grad)
        c.set_line_width(2.1 + 1.05 * math.sin(k / 18))
        c.stroke()
    paper(c, 22)
    return s


def quarter_credit():
    """Isometric midnight arcade blocks. Project the floors and their windows in one coordinate system."""
    rng = random.Random(23)
    s, c = poster(0x17263B)
    def project(u, v, h):
        return 472 + 61 * (u - v), -126 + 27 * (u + v) - h

    for depth in range(-2, 38):
        for u in range(-4, 24):
            v = depth - u
            x, y = project(u, v, 0)
            if not (-90 < x < 1040 and -20 < y < 800):
                continue
            height = rng.choice([16, 42, 57, 76, 95, 122, 154])
            side = rng.uniform(.65, .93)
            a, b, d, e = (project(u + du, v + dv, height) for du, dv in
                           ((0, 0), (side, 0), (side, side), (0, side)))
            bb, dd, ee = (project(u + du, v + dv, 0) for du, dv in ((side, 0), (side, side), (0, side)))
            top, left, right, light = rng.choice([
                (0x316D81, 0x253A53, 0x1C2943, 0x84CBCC), (0xC06B75, 0x733758, 0x3A2C4C, 0xF8AB84),
                (0x5176A6, 0x2C3C65, 0x1C2745, 0xAFBCDB), (0x328584, 0x234B61, 0x163244, 0x74D1B8)])
            polygon(c, [e, d, dd, ee], left)
            polygon(c, [d, b, bb, dd], right)
            polygon(c, [a, b, d, e], top)
            for z in range(8, height - 4, 10):
                for j in range(1, 5):
                    if rng.random() < .38:
                        t = j * side / 5
                        polygon(c, [project(u + t, v + side, z), project(u + t + .08, v + side, z),
                                    project(u + t + .08, v + side, z + 2), project(u + t, v + side, z + 2)], light)
            if height > 90 and rng.random() < .65:
                for inset in (.14, .19):
                    p = [project(u + du, v + dv, height) for du, dv in
                         ((inset, inset), (side-inset, inset), (side-inset, side-inset), (inset, side-inset))]
                    polygon(c, p, light if inset == .14 else top)
    paper(c, 23)
    return s


def palm_motor_inn():
    """Screen-printed desert motel at blue hour, with breeze blocks, palms and a lit pool."""
    rng = random.Random(24)
    s, c = poster(0xDE9579)
    bg = cairo.LinearGradient(0, 0, 0, 322)
    bg.add_color_stop_rgb(0, *rgb(0x575379))
    bg.add_color_stop_rgb(.65, *rgb(0xD5878C))
    bg.add_color_stop_rgb(1, *rgb(0xF3BC8A))
    c.set_source(bg)
    c.paint()
    disc(c, 673, 132, 65, 0xF7CB92)
    polygon(c, [(0, 276), (86, 244), (124, 251), (230, 207), (268, 216), (323, 204), (472, 263),
                (571, 231), (601, 241), (728, 191), (834, 238), (891, 225), (960, 251), (960, 355), (0, 355)], 0x875C76)
    polygon(c, [(0, 313), (188, 276), (292, 288), (418, 248), (522, 274), (788, 259), (960, 298),
                (960, 400), (0, 400)], 0x554863)
    polygon(c, [(0, 335), (960, 291), (960, 540), (0, 540)], 0xC18879)
    polygon(c, [(-50, 241), (602, 284), (602, 328), (-50, 383)], 0xC68A75)
    for j in range(10):
        x = j * 62
        polygon(c, [(x+4, 254+x*.064), (x+46, 257+x*.064), (x+46, 355-x*.065), (x+4, 358-x*.065)], 0x324554)
        polygon(c, [(x+7, 257+x*.064), (x+36, 259+x*.064), (x+36, 291), (x+7, 291)], 0xE6AB79 if j%3 else 0x67878C)
    polygon(c, [(-35, 223), (649, 277), (586, 289), (-35, 251)], 0x40394C)
    polygon(c, [(-35, 220), (654, 274), (649, 278), (-35, 235)], 0xF3CF9F)
    pool = [(89, 448), (703, 333), (1001, 360), (1001, 407), (421, 540), (188, 540)]
    polygon(c, pool, 0xEFE0B5)
    polygon(c, [(116, 452), (705, 338), (984, 364), (983, 400), (423, 533), (201, 533)], 0x377E83)
    c.save()
    for i, point in enumerate([(116, 452), (705, 338), (984, 364), (983, 400), (423, 533), (201, 533)]):
        (c.move_to if i == 0 else c.line_to)(*point)
    c.close_path()
    c.clip()
    for i in range(550):
        x, y = rng.uniform(106, 995), rng.uniform(338, 537)
        c.set_source_rgba(*rgb(0xB4DCB4), rng.uniform(.12, .53))
        c.set_line_width(rng.uniform(.8, 2))
        c.move_to(x, y)
        c.rel_line_to(rng.uniform(2, (y - 300) * .22), rng.uniform(-1, .4))
        c.stroke()
    c.restore()
    for x, y, drift, h in [(834, 305, -12, 171), (927, 319, -22, 223), (58, 370, 33, 255)]:
        tx, ty = x + drift, y - h
        polygon(c, [(x-4, y), (x+6, y), (tx+2, ty), (tx-1, ty)], 0x303747)
        for j in range(12):
            a = j * math.tau / 12 + rng.uniform(-.09, .09)
            span = h * rng.uniform(.19, .30)
            dx, dy = math.cos(a)*span, math.sin(a)*span*.58
            for k in range(1, 14):
                t = k / 14
                px, py = tx+dx*t, ty+dy*t+19*t*t
                leaf = (1 - .7*t) * h * .09
                polygon(c, [(px,py), (px+dx/12,py+dy/9+2),
                            (px+dx*.15,py+leaf+dy*.15)], 0x303747)
    paper(c, 24)
    return s


def midnight_pinball():
    """A close-up of bumpers, lighted lanes and the reflected playfield in a steel ball."""
    s, c = poster(0x18243A)
    c.translate(502, 257)
    c.rotate(-.29)
    for y in (-174, 122):
        for i, color in enumerate((0xC95065, 0xED885E, 0xEBCB85)):
            c.set_source_rgb(*rgb(color))
            c.set_line_width(7)
            c.move_to(-690, y+13*i)
            c.curve_to(-230, y+13*i, -240, y-90+13*i, -58, y-90+13*i)
            c.line_to(620, y-90+13*i)
            c.stroke()
    for x, y, r in ((-303, -120, 112), (-58, -30, 92), (-256, 137, 91), (356, 216, 145)):
        disc(c, x+7, y+12, r+5, 0x0F192A)
        for fraction, color in ((1,0x9AA8AC), (.964,0x243249), (.90,0xF49C74), (.86,0x88394E), (.78,0xE0C18C), (.75,0xB34658)):
            disc(c, x, y, r*fraction, color)
        # Stagger small triangular fins on the cap like a turbine.
        for j in range(11):
            a = j*math.tau/11
            polygon(c, [(x + r*f*math.cos(a+da), y+r*f*math.sin(a+da))
                        for f,da in ((.21,0),(.66,.15),(.56,.44))], 0xF2BC83)
        disc(c, x, y, r*.21, 0x53283F)
        disc(c, x, y, r*.14, 0xD99B75)
    # Deep polished metal ball: mirror bands represent the ceiling tubes and the dark furniture.
    x, y, r = 162, 46, 49
    disc(c, x+6, y+9, r+1, 0x101A2E)
    sheen = cairo.RadialGradient(x-r*.28, y-r*.38, 0, x, y, r)
    for t, col in ((0,0xFDF1CD),(.12,0xE3DDC6),(.25,0x738A99),(.45,0x35506C),(.54,0xCBA394),
                   (.57,0x2B3042),(.71,0x182235),(.87,0x4D5667),(.97,0xB1BBBB),(1,0x5F7581)):
        sheen.add_color_stop_rgb(t,*rgb(col))
    c.arc(x,y,r,0,math.tau)
    c.set_source(sheen)
    c.fill()
    for x, y in ((213,-162), (251,-162), (289,-162), (-9,191), (29,191), (67,191)):
        polygon(c, [(x-11,y+8),(x,y-12),(x+11,y+8)], 0xECC17D)
    paper(c, 25)
    return s


def phosphor_fantasia():
    """Long-exposure Lissajous figures on a green phosphor oscilloscope."""
    s, c = poster(0x071A20)
    phosphor = cairo.RadialGradient(540, 270, 0, 540, 270, 595)
    phosphor.add_color_stop_rgb(0, *rgb(0x21473F))
    phosphor.add_color_stop_rgb(1, *rgb(0x05151F))
    c.set_source(phosphor)
    c.paint()
    c.set_source_rgba(*rgb(0x7ABD9C), .19)
    c.set_line_width(.65)
    for x in range(-12, 961, 48):
        c.move_to(x, 0)
        c.line_to(x, 540)
    for y in range(-18, 541, 48):
        c.move_to(0, y)
        c.line_to(960, y)
    c.stroke()
    for k in range(25):
        pts = []
        for j in range(801):
            t = j*math.tau/800
            phase = k*.021
            x = 482 + 310*math.sin(2*t + phase) + 52*math.sin(5*t + phase)
            y = 260 + 157*math.sin(3*t - phase*.7) + 48*math.cos(t+phase)
            pts.append((x, y))
        color = 0xEAD291 if k > 15 else 0x68E2AE
        glow_line(c, pts, color, 1, ((9,.018),(3,.075),(1,.74)))
    # Printed scale marks on the screen surround.
    for x in range(52, 919, 12):
        c.set_source_rgba(*rgb(0xACCEB2), .42)
        c.rectangle(x, 493, 1, 8 if (x-52)%60==0 else 3)
        c.fill()
    paper(c, 26)
    return s


PICTURES = {
    "Neon Grid Horizon": neon_grid_horizon,
    "Neon Lake Mountains": neon_lake_mountains,
    "Stripe Corner": stripe_corner,
    "Sunburst Scallops": scallops,
    "Wave Bands": wave_bands,
    "Retro Sun": retro_sun,
    "Chromatic Interchange": chromatic_interchange,
    "Velvet Oscillator": velvet_oscillator,
    "Quarter Credit": quarter_credit,
    "Palm Motor Inn": palm_motor_inn,
    "Midnight Pinball": midnight_pinball,
    "Phosphor Fantasia": phosphor_fantasia,
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
