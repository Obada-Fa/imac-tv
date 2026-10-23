#!/usr/bin/env python3
"""Build tvapp/assets/shapes.json: a handful of simple doodles for the ambient TV Home.

Source: Google's Quick, Draw! dataset ("simplified" ndjson), CC BY 4.0 -
https://github.com/googlecreativelab/quickdraw-dataset. Only the first ~1.2 MB of each category file is
fetched (HTTP Range); whole files are never downloaded. Keeps recognized drawings, simplifies each to
25-45 points, and picks the best two per category by a small quality heuristic.

    python3 scripts/build-shapes.py [out.json]
"""
import json
import math
import sys
import urllib.request
from pathlib import Path

BASE = "https://storage.googleapis.com/quickdraw_dataset/full/simplified/{}.ndjson"
CATEGORIES = ["whale", "dolphin", "owl", "bird", "swan", "squirrel", "giraffe", "horse", "cat", "rabbit", "butterfly",
              "fish", "octopus", "mountain", "tree", "sailboat", "lighthouse", "moon", "cactus", "snail"]
RANGE = 1_200_000
MIN_PTS, MAX_PTS = 25, 45
CANDIDATES = 600
OUT = Path(__file__).resolve().parents[1] / "tvapp" / "assets" / "shapes.json"


def fetch_head(cat):
    req = urllib.request.Request(BASE.format(cat), headers={"Range": f"bytes=0-{RANGE - 1}", "User-Agent": "imactv-build-shapes"})
    with urllib.request.urlopen(req, timeout=60) as r:
        data = r.read(RANGE + 1024)
    lines = data.decode("utf-8", "replace").split("\n")[:-1]      # the last line is usually cut off
    out = []
    for ln in lines:
        try:
            d = json.loads(ln)
        except ValueError:
            continue
        if d.get("recognized") is True and d.get("drawing"):
            out.append(d["drawing"])
        if len(out) >= CANDIDATES:
            break
    return out


def rdp(pts, eps):
    """Ramer-Douglas-Peucker on a list of (x, y)."""
    if len(pts) < 3:
        return list(pts)
    (x1, y1), (x2, y2) = pts[0], pts[-1]
    dx, dy = x2 - x1, y2 - y1
    norm = math.hypot(dx, dy)
    best, idx = -1.0, 0
    for i in range(1, len(pts) - 1):
        x0, y0 = pts[i]
        d = math.hypot(x0 - x1, y0 - y1) if norm == 0 else abs(dy * x0 - dx * y0 + x2 * y1 - y2 * x1) / norm
        if d > best:
            best, idx = d, i
    if best <= eps:
        return [pts[0], pts[-1]]
    return rdp(pts[: idx + 1], eps)[:-1] + rdp(pts[idx:], eps)


def simplify(drawing):
    """-> list of strokes (lists of (x, y)) with <= MAX_PTS points in total, or None if too sparse."""
    strokes = [list(zip(s[0], s[1])) for s in drawing if len(s[0]) >= 2]
    if not strokes:
        return None
    lo, hi = 0.0, 40.0
    best = None
    for _ in range(14):
        eps = (lo + hi) / 2
        s = [rdp(st, eps) for st in strokes]
        n = sum(len(x) for x in s)
        if n > MAX_PTS:
            lo = eps
        else:
            best, hi = s, eps
    if best is None or sum(len(x) for x in best) < MIN_PTS:
        return None
    return best


def score(strokes, raw_points):
    pts = [p for st in strokes for p in st]
    xs, ys = [p[0] for p in pts], [p[1] for p in pts]
    w, h = max(xs) - min(xs), max(ys) - min(ys)
    if w < 40 or h < 40:
        return None
    aspect = w / h
    n = len(pts)
    return -(0.6 * abs(len(strokes) - 2)) - abs(n - 36) / 18 - 1.2 * abs(math.log(aspect)) - (0.5 if raw_points < 25 else 0)


def normalise(strokes):
    pts = [p for st in strokes for p in st]
    x0, x1 = min(p[0] for p in pts), max(p[0] for p in pts)
    y0, y1 = min(p[1] for p in pts), max(p[1] for p in pts)
    flat, idx, out_strokes = [], 0, []
    for st in strokes:
        ids = []
        for x, y in st:
            flat.append([round((x - x0) / (x1 - x0), 3), round((y - y0) / (y1 - y0), 3)])
            ids.append(idx)
            idx += 1
        out_strokes.append(ids)
    return flat, out_strokes, round((x1 - x0) / (y1 - y0), 3)


def best_for(cat):
    scored = []
    for drawing in fetch_head(cat):
        raw = sum(len(s[0]) for s in drawing)
        strokes = simplify(drawing)
        if not strokes:
            continue
        sc = score(strokes, raw)
        if sc is not None:
            scored.append((sc, strokes))
    scored.sort(key=lambda t: -t[0])
    out = []
    for _, strokes in scored[:2]:
        pts, ids, aspect = normalise(strokes)
        out.append({"name": cat, "aspect": aspect, "pts": pts, "strokes": ids})
    return out


def main():
    out_path = Path(sys.argv[1]) if len(sys.argv) > 1 else OUT
    shapes = []
    for cat in CATEGORIES:
        try:
            got = best_for(cat)
        except Exception as exc:
            print(f"{cat}: skipped ({exc})", file=sys.stderr)
            continue
        print(f"{cat}: {len(got)} shape(s)", file=sys.stderr)
        shapes += got
    doc = {"version": 1, "license": "CC BY 4.0",
           "source": "Google Quick, Draw! dataset, https://github.com/googlecreativelab/quickdraw-dataset",
           "shapes": shapes}
    out_path.write_text(json.dumps(doc, separators=(",", ":")))
    print(f"wrote {out_path} ({out_path.stat().st_size} bytes, {len(shapes)} shapes)", file=sys.stderr)


if __name__ == "__main__":
    main()
