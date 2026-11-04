"""Handwritten strokes for the Drift clock, sampled once into matching sets of motes."""
import math
from bisect import bisect_left
from functools import lru_cache

import cairo

# Each stroke starts with a pen position, followed by straight or cubic segments.
STROKES = {
    "0": [[(24, 7), (45, 1, 35, 57, 13, 57), (-5, 57, 3, 12, 24, 7)]],
    "1": [[(9, 22), (22, 14, 25, 10, 28, 7), (20, 57)]],
    "2": [[(3, 24), (9, 3, 36, 3, 35, 19), (34, 32, 7, 43, 2, 58), (17, 54, 25, 55, 34, 57)]],
    "3": [[(4, 16), (19, 1, 45, 6, 31, 22), (27, 27, 20, 30, 15, 31),
           (45, 21, 35, 63, 12, 57), (7, 56, 4, 54, 2, 52)]],
    "4": [[(31, 6), (19, 21, 8, 34, 3, 39), (20, 38, 31, 37, 38, 37)], [(30, 22), (24, 58)]],
    "5": [[(36, 8), (13, 8), (8, 30), (35, 14, 43, 48, 21, 56), (12, 60, 4, 56, 2, 51)]],
    "6": [[(34, 8), (15, 10, -3, 39, 8, 54), (23, 68, 44, 34, 28, 30), (20, 26, 10, 32, 6, 40)]],
    "7": [[(4, 12), (19, 8, 29, 9, 38, 10), (23, 30, 16, 44, 11, 58)]],
    "8": [[(23, 31), (-2, 20, 16, 3, 29, 8), (49, 16, 23, 30, 15, 34),
           (-8, 48, 9, 64, 24, 56), (42, 46, 34, 37, 23, 31)]],
    "9": [[(32, 22), (31, -3, 0, 9, 6, 29), (15, 44, 31, 28, 34, 13), (32, 30, 21, 51, 12, 58)]],
}
MOTES = 28
DURATION = 1.6


@lru_cache(maxsize=10)
def samples(digit):
    cr = cairo.Context(cairo.RecordingSurface(cairo.CONTENT_ALPHA, None))
    for start, *segments in STROKES[digit]:
        cr.move_to(*start)
        for segment in segments:
            if len(segment) == 2:
                cr.line_to(*segment)
            else:
                cr.curve_to(*segment)

    ends, lines = [], []
    length = 0.0
    for kind, point in cr.copy_path_flat():
        if kind == cairo.PATH_LINE_TO:
            step = math.dist(previous, point)
            if step:
                lines.append((previous, point, step))
                length += step
                ends.append(length)
        previous = point

    points = []
    for i in range(MOTES):
        distance = length * (i + 0.25) / MOTES
        j = bisect_left(ends, distance)
        start, end, step = lines[j]
        t = 1 - (ends[j] - distance) / step
        points.append(tuple(a + (b - a) * t for a, b in zip(start, end)))
    return points


def frame(width, height, clock, previous, progress, phase):
    surface = cairo.ImageSurface(cairo.FORMAT_ARGB32, width, height)
    cr = cairo.Context(surface)
    cr.scale(width / 202, height / 72)
    dots = [(100, 28, 1.15), (100, 44, 1.15)]
    for place, left in zip((0, 1, 3, 4), (3, 48, 110, 155)):
        new, old = samples(clock[place]), samples(previous[place])
        changing = new is not old
        for i, ((x, y), (ox, oy)) in enumerate(zip(new, old)):
            seed = i * 2.4 + left
            if changing:
                t = min(1.0, max(0.0, progress * 1.15 - 0.15 * i / (MOTES - 1)))
                t = t * t * (3 - 2 * t)
                arc = math.sin(math.pi * t)
                x = ox + (x - ox) * t + 5 * arc * math.sin(seed)
                y = oy + (y - oy) * t - 4 * arc
            dots.append((left + x + .45 * math.sin(seed + phase), 4 + y + .55 * math.cos(seed - phase),
                         1.45 if i % 4 == 0 else 1.1))

    # Composite once onto the widget. A soft underlay keeps the points legible on light photos.
    for radius, dy, color in ((1.9, .65, (0.05, .08, .12, .30)), (1.4, 0, (.64, .81, 1, .23)),
                              (1, 0, (.91, .96, 1, .96))):
        cr.set_source_rgba(*color)
        for x, y, r in dots:
            cr.new_sub_path()
            cr.arc(x, y + dy, r * radius, 0, math.tau)
        cr.fill()
    return surface
