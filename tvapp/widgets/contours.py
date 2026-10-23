"""Small, dependency-free colour-edge tracing, run only by the photo loader.

Coordinates are normalised to the *displayed crop*, not the original image.
The result is bounded polylines; animation never reads pixels or searches edges.
"""
import math

from .shapes import rdp, valid_paths

TRACE_WIDTH = 384
MAX_PATHS = 24
MAX_POINTS = 96


def trace(rgb):
    """RGB rows (0..255, <=384 px wide) -> long, clean edge polylines.

    Smooth texture, take the strongest colour-channel gradient (also sees edges
    of equal luminance), thin it, then follow its tangent. Short specks/stars and
    texture fragments are discarded. No lines are invented across empty areas.
    """
    h = len(rgb)
    w = len(rgb[0]) if h else 0
    if w < 16 or h < 16:
        return []
    # Separable [1, 2, 1] filter suppresses paper grain without erasing thin neon.
    sm = [[[sum(rgb[y][min(w - 1, max(0, x + d))][c] * weight
                for d, weight in ((-1, 1), (0, 2), (1, 1))) / 4
            for c in range(3)] for x in range(w)] for y in range(h)]
    sm = [[[sum(sm[min(h - 1, max(0, y + d))][x][c] * weight
                for d, weight in ((-1, 1), (0, 2), (1, 1))) / 4
            for c in range(3)] for x in range(w)] for y in range(h)]
    mag, tangent = {}, {}
    for y in range(1, h - 1):
        for x in range(1, w - 1):
            grads = [(sm[y][x + 1][c] - sm[y][x - 1][c],
                      sm[y + 1][x][c] - sm[y - 1][x][c]) for c in range(3)]
            gx, gy = max(grads, key=lambda g: g[0] * g[0] + g[1] * g[1])
            m = math.hypot(gx, gy)
            mag[x, y] = m
            tangent[x, y] = (-gy / m, gx / m) if m else (0, 0)
    strengths = sorted(mag.values())
    high = max(8.0, strengths[int(len(strengths) * 0.90)] * 0.5)
    low = max(3.5, high * 0.35)
    edge = {}
    for (x, y), m in mag.items():
        if m < low:
            continue
        tx, ty = tangent[x, y]
        gx, gy = ty, -tx
        if abs(gy) < abs(gx) * 0.414:
            dx, dy = 1, 0
        elif abs(gx) < abs(gy) * 0.414:
            dx, dy = 0, 1
        else:
            dx, dy = 1, 1 if gx * gy > 0 else -1
        if m >= mag.get((x - dx, y - dy), 0) and m > mag.get((x + dx, y + dy), 0):
            edge[x, y] = m
    # Dense high-frequency texture does not offer trustworthy visual tracks.
    if len(edge) > w * h * 0.28:
        return []
    remaining = set(edge)
    offsets = [(dx, dy, math.hypot(dx, dy)) for dy in range(-2, 3) for dx in range(-2, 3)
               if (dx or dy) and math.hypot(dx, dy) <= 2.3]

    def walk(start, direction):
        path, p = [], start
        while True:
            candidates = []
            tx, ty = direction
            for dx, dy, d in offsets:
                q = p[0] + dx, p[1] + dy
                if q not in remaining:
                    continue
                alignment = (tx * dx + ty * dy) / d
                if alignment < 0.2:  # permits a wall/floor corner, rejects reversing onto parallel edges
                    continue
                qtx, qty = tangent[q]
                agreement = abs((qtx * dx + qty * dy) / d)
                if agreement < 0.25:
                    continue
                candidates.append((alignment + agreement * 0.3 - d * 0.35, q, dx / d, dy / d))
            if not candidates:
                break
            _, q, tx, ty = max(candidates)
            remaining.remove(q)
            path.append(q)
            # Follow local edge orientation, preserving travel direction.
            qtx, qty = tangent[q]
            if qtx * tx + qty * ty < 0:
                qtx, qty = -qtx, -qty
            direction = (tx * 0.4 + qtx * 0.6, ty * 0.4 + qty * 0.6)
            p = q
        return path

    paths = []
    for seed in sorted(edge, key=lambda p: (-edge[p], p)):
        if seed not in remaining or edge[seed] < high:
            continue
        remaining.remove(seed)
        tx, ty = tangent[seed]
        path = list(reversed(walk(seed, (-tx, -ty)))) + [seed] + walk(seed, (tx, ty))
        length = sum(math.dist(a, b) for a, b in zip(path, path[1:]))
        if (length < w * 0.13 or len(path) < 12 or
                sum(edge[p] >= high for p in path) < len(path) * 0.35):
            continue
        # RDP runs only on capped input to bound recursion on noisy images.
        stride = max(1, len(path) // 500)
        sample = path[::stride]
        if sample[-1] != path[-1]:
            sample.append(path[-1])
        simple = rdp(sample, 0.95)
        if len(simple) > MAX_POINTS or len(simple) > 2 + length * 0.16:
            continue
        paths.append((length, simple))
    paths.sort(key=lambda p: -p[0])
    selected = paths[:MAX_PATHS]
    if len(selected) < 3 or sum(length for length, _ in selected) < w:
        return []
    return [[(x / (w - 1), y / (h - 1)) for x, y in path] for _, path in selected]


def main():
    """`python3 -m widgets.contours` (cwd tvapp): "<w> <h>\\n" + packed RGB bytes on stdin -> JSON paths.

    The app runs trace() in this child process, so its pure-Python pixel loops never hold the
    app's GIL (that froze the animation for ~0.5-2 s on every new background).
    """
    import json
    import sys
    data = sys.stdin.buffer.read()
    head, _, px = data.partition(b"\n")
    w, h = (int(v) for v in head.split())
    if len(px) != w * h * 3:
        raise SystemExit("contours: expected %d bytes, got %d" % (w * h * 3, len(px)))
    rgb = [[tuple(px[(y * w + x) * 3:(y * w + x) * 3 + 3]) for x in range(w)] for y in range(h)]
    json.dump(trace(rgb), sys.stdout)


if __name__ == "__main__":
    main()
