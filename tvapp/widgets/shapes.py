"""Ambient shapes: dots that glide onto a drawing (the photo's horizon, or a doodle) and a calm music visualizer.

Pure math, no GTK: horizon tracing, mesh building, doodle placement, the Morph state machine, and the
visualizer mapping. homescreen.py drives it and draws it."""
import json
import math
import random
from pathlib import Path

TRACE_W = 240          # photo is analysed at this width
TRACE_POINTS = 48
MORPH_IN = 3.0         # seconds for dots to glide onto the shape
MORPH_OUT = 1.4        # seconds to release them back to drifting dust


# ---------------------------------------------------------------- horizon tracing

def _rowsmooth(row, k=2):
    n = len(row)
    out = []
    for x in range(n):
        a, b = max(0, x - k), min(n, x + k + 1)
        out.append(sum(row[a:b]) / (b - a))
    return out


def _median(vals):
    v = sorted(vals)
    return v[len(v) // 2]


def rdp(pts, eps):
    """Ramer-Douglas-Peucker: keeps peaks and saddles, drops flat runs."""
    if len(pts) < 3:
        return list(pts)
    (x1, y1), (x2, y2) = pts[0], pts[-1]
    dx, dy = x2 - x1, y2 - y1
    norm = math.hypot(dx, dy) or 1e-9
    best, idx = -1.0, 0
    for i in range(1, len(pts) - 1):
        d = abs(dy * pts[i][0] - dx * pts[i][1] + x2 * y1 - y2 * x1) / norm
        if d > best:
            best, idx = d, i
    if best <= eps:
        return [pts[0], pts[-1]]
    return rdp(pts[: idx + 1], eps)[:-1] + rdp(pts[idx:], eps)


def trace_horizon(gray, blue=None, min_valid=0.6):
    """Trace the SKY boundary of a photo: per column, the top-most edge where the region above is sky-like
    (smooth, and brighter or bluer than what is below) and the region below is textured land.
    gray: rows of 0..255 (~240 wide); blue: optional rows of (B - R). -> {"pts", "links", "kind"} with
    normalised points, or None when there is no clear sky line (forest, crowd, cave, flat colour)."""
    h = len(gray)
    w = len(gray[0]) if h else 0
    if h < 20 or w < 40:
        return None
    rows = [_rowsmooth(r, 1) for r in gray]
    brows = [_rowsmooth(r, 1) for r in blue] if blue else None
    win = 6
    # texture energy per pixel: |dL/dy| + |dL/dx|, then box-averaged over the window when queried
    tex = [[(abs(rows[y + 1][x] - rows[y][x]) + abs(rows[y][min(w - 1, x + 1)] - rows[y][x]))
            for x in range(w)] for y in range(h - 1)]
    cs = []                                    # cumulative sums down each column for fast window means
    for x in range(w):
        acc, col = 0.0, [0.0]
        for y in range(h - 1):
            acc += tex[y][x]
            col.append(acc)
        cs.append(col)

    def tex_mean(x, y0, y1):                   # rows y0..y1-1, columns x-1..x+1
        tot = 0.0
        for xx in (max(0, x - 1), x, min(w - 1, x + 1)):
            tot += cs[xx][y1] - cs[xx][y0]
        return tot / (3 * (y1 - y0))

    y_lo, y_hi = win + 1, int(h * 0.8)
    n = y_hi - y_lo
    # edge score: a luminance step with SMOOTH sky above (clouds and gradients are fine) and textured land
    # below, with a bias towards the upper edges so the ridge wins over the snow line below it
    score = []
    skyish = []
    for x in range(w):
        col, sk = [], []
        for y in range(y_lo, y_hi):
            la = (rows[y - 1][x] + rows[y - 2][x] + rows[y - 3][x]) / 3
            lb = (rows[y + 1][x] + rows[y + 2][x] + rows[y + 3][x]) / 3
            edge = abs(la - lb)
            above = tex_mean(x, y - win, y)
            below = tex_mean(x, y + 1, min(h - 1, y + 1 + win))
            wa = 1.0 / (1.0 + (above / 7.0) ** 2)
            wb = max(0.05, min(1.0, (below - 8.0) / 30.0))
            col.append(edge * wa * wb * (1.0 + 0.8 * (1.0 - y / h)))
            up = brows is not None and (sum(brows[y - k][x] for k in range(2, 2 + win)) / win > 5 and
                                        sum(brows[y - k][x] for k in range(2, 2 + win)) / win >
                                        sum(brows[min(h - 1, y + k)][x] for k in range(2, 2 + win)) / win + 8)
            sk.append(la > lb + 4 or up)
        score.append(col)
        skyish.append(sk)
    # dynamic programming seam, |dy| <= 7 per column
    lam = 0.6
    best_prev = score[0][:]
    back = []
    for x in range(1, w):
        cur, bk = [], []
        for y in range(n):
            bv, by = -1e18, y
            for dy in range(-7, 8):
                yy = y + dy
                if 0 <= yy < n:
                    v = best_prev[yy] - lam * abs(dy)
                    if v > bv:
                        bv, by = v, yy
            cur.append(bv + score[x][y])
            bk.append(by)
        best_prev = cur
        back.append(bk)
    yi = max(range(n), key=lambda i: best_prev[i])
    path = [yi]
    for bk in reversed(back):
        yi = bk[yi]
        path.append(yi)
    path.reverse()
    good = sum(1 for x in range(w) if score[x][path[x]] >= 14.0)
    sky = sum(1 for x in range(w) if skyish[x][path[x]])
    pinned = sum(1 for v in path if v <= 1 or v >= n - 2)
    if good / w < min_valid or sky / w < 0.7 or pinned / w > 0.4:
        return None
    ys = [y_lo + v for v in path]
    valid = list(range(w))
    # fill gaps by interpolation, then median-filter (peaks wider than ~3 columns survive) and lightly smooth
    filled = []
    for x in range(w):
        if ys[x] is not None:
            filled.append(float(ys[x]))
            continue
        left = max((i for i in valid if i < x), default=None)
        right = min((i for i in valid if i > x), default=None)
        if left is None:
            filled.append(float(ys[right]))
        elif right is None:
            filled.append(float(ys[left]))
        else:
            u = (x - left) / (right - left)
            filled.append(ys[left] + (ys[right] - ys[left]) * u)
    med = [_median(filled[max(0, x - 3): x + 4]) for x in range(w)]
    sm = [sum(med[max(0, x - 1): x + 2]) / len(med[max(0, x - 1): x + 2]) for x in range(w)]
    if sum(1 for v in sm if v >= y_hi - 2) / w > 0.4:
        return None                           # "sky" fills the frame: no real horizon
    line = [((x + 0.5) / w, (v + 0.5) / h) for x, v in enumerate(sm)]
    return mesh_from_line(line, h)


def mesh_from_line(line, h, n=TRACE_POINTS):
    """Dense samples at peaks and saddles, sparse on flat runs (RDP); plus a sparse second row below with
    a few cross-links, so it reads as a low-poly ridge rather than a zig-zag."""
    lo, hi = 0.0, 0.2
    best = rdp(line, hi)
    for _ in range(14):
        eps = (lo + hi) / 2
        cand = rdp(line, eps)
        if len(cand) > n:
            lo = eps
        else:
            best, hi = cand, eps
    row1 = best
    m = len(row1)
    row2, idx2 = [], []
    for k in range(2, m - 2, 4):
        x, y = row1[k]
        row2.append((x, min(0.96, y + 0.055 + 0.03 * ((k * 7) % 3) / 2)))
        idx2.append(k)
    pts = row1 + row2
    links = [(k, k + 1) for k in range(m - 1)]
    base = m
    links += [(base + k, base + k + 1) for k in range(len(row2) - 1)]
    for k, i in enumerate(idx2):
        links += [(base + k, i - 1), (base + k, i), (base + k, i + 1)]
    return {"pts": pts, "links": [(a, b) for a, b in links if a != b], "kind": "photo"}


def wave_shape():
    """Fallback for music on a photo with no horizon: a gentle low-poly wave across the lower third."""
    n = TRACE_POINTS
    line = [(0.04 + 0.92 * k / (n - 1), 0.68 + 0.05 * math.sin(k / (n - 1) * 6.283 * 2.2)) for k in range(n)]
    sh = mesh_from_line(line, 1)
    sh["kind"] = "wave"
    return sh


def constellation_shape(width, height, rings=3, sides=8):
    """Centered, independent geometric mesh for music visualization: nested octagonal rings."""
    cx, cy = width / 2.0, height / 2.0
    radius = min(width, height) * 0.34
    pts = []
    links = []
    for ring in range(rings):
        r = radius * (ring + 1) / rings
        phase = (ring % 2) * math.pi / sides
        start = len(pts)
        pts.extend((cx + r * math.cos(2 * math.pi * i / sides + phase),
                    cy + r * math.sin(2 * math.pi * i / sides + phase)) for i in range(sides))
        links.extend((start + i, start + (i + 1) % sides) for i in range(sides))
        if ring:
            prev = start - sides
            links.extend((prev + i, start + i) for i in range(sides))
            links.extend((prev + i, start + (i + 1) % sides) for i in range(sides))
    return {"pts": pts, "links": links, "kind": "constellation"}


# ---------------------------------------------------------------- doodles (Quick, Draw! slice)

def load_doodles(path):
    """assets/shapes.json -> [{"name", "pts": [(x, y)] 0..1, "strokes": [[idx...]], "aspect"}]; [] if missing/broken."""
    try:
        data = json.loads(Path(path).read_text())
        out = []
        for s in data.get("shapes", []):
            pts = [(float(x), float(y)) for x, y in s["pts"]]
            strokes = [[int(i) for i in st] for st in s["strokes"]]
            if len(pts) >= 5 and all(0 <= i < len(pts) for st in strokes for i in st):
                out.append({"name": str(s.get("name", "")), "pts": pts, "strokes": strokes,
                            "aspect": float(s.get("aspect") or 1.0)})
        return out
    except (OSError, ValueError, KeyError, TypeError):
        return []


def place_doodle(shape, sw, sh, rng):
    """Doodle in an empty area of an ambient screen (clock top-left, caption bottom-left stay clear)."""
    hgt = 0.42 * sh
    wid = hgt * shape["aspect"]
    if wid > 0.4 * sw:
        wid, hgt = 0.4 * sw, 0.4 * sw / shape["aspect"]
    cx = rng.choice((0.72, 0.52, 0.32)) * sw
    cy = rng.uniform(0.44, 0.54) * sh
    pts = [(cx + (x - 0.5) * wid, cy + (y - 0.5) * hgt) for x, y in shape["pts"]]
    links = []
    for st in shape["strokes"]:
        links += list(zip(st, st[1:]))
    return {"pts": pts, "links": links, "kind": "animal", "name": shape["name"]}


# ---------------------------------------------------------------- morph

def smooth(p):
    p = max(0.0, min(1.0, p))
    return p * p * (3 - 2 * p)


class Morph:
    """Dots glide from their drifting positions onto a target shape and back. State only; no drawing."""

    def __init__(self):
        self.p = 0.0
        self.dir = 0
        self.target = None
        self.pending = None
        self.cx = self.cy = 0.0
        self.rmax = 1.0

    def _set(self, shape):
        self.target = shape
        pts = shape["pts"]
        self.cx = sum(x for x, _ in pts) / len(pts)
        self.cy = sum(y for _, y in pts) / len(pts)
        self.rmax = max(1.0, max(math.hypot(x - self.cx, y - self.cy) for x, y in pts))

    def start(self, shape):
        """Form `shape`; if another is showing, dissolve it first."""
        if self.target is not None and self.p > 0:
            self.pending = shape
            self.dir = -1
        else:
            self._set(shape)
            self.dir = 1

    def release(self):
        self.pending = None
        if self.target is not None:
            self.dir = -1

    @property
    def moving(self):
        return self.dir != 0

    @property
    def kind(self):
        return self.target["kind"] if self.target else None

    def e(self):
        return smooth(self.p)

    def step(self, dt):
        if self.dir > 0:
            self.p = min(1.0, self.p + dt / MORPH_IN)
            if self.p >= 1.0:
                self.dir = 0
        elif self.dir < 0:
            self.p = max(0.0, self.p - dt / MORPH_OUT)
            if self.p <= 0.0:
                if self.pending is not None:
                    self._set(self.pending)
                    self.pending = None
                    self.dir = 1
                else:
                    self.target = None
                    self.dir = 0

    def positions(self, origin, t):
        """origin(i, t) -> (x, y): where dot i would be drifting. -> list of shape-dot positions now."""
        e = self.e()
        out = []
        for i, (tx, ty) in enumerate(self.target["pts"]):
            ox, oy = origin(i, t)
            out.append((ox + (tx - ox) * e, oy + (ty - oy) * e))
        return out

    def alphas(self, n_dust, dust_alpha):
        """Dots that were dust start at dust_alpha; extra dots start invisible. All reach 1."""
        e = self.e()
        return [(dust_alpha if i < n_dust else 0.0) * (1 - e) + e for i in range(len(self.target["pts"]))]


# ---------------------------------------------------------------- visualizer

def ease_levels(cur, target, dt, attack=12.0, release=2.2):
    """Smooth the audio levels: quick to rise, ~1.5 s to settle. -> new dict."""
    out = {}
    for k in ("bass", "mid", "treble", "beat"):
        c, t = cur.get(k, 0.0), float(target.get(k, 0.0))
        rate = attack if t > c else release
        out[k] = c + (t - c) * (1 - math.exp(-rate * max(0.0, dt)))
    return out


def viz_points(pts, centre, bass, scale_amp=0.045):
    """Breathe the shape out about its centroid: bass 0..1 -> up to +scale_amp (rest = unchanged)."""
    k = 1 + scale_amp * max(0.0, min(1.0, bass))
    cx, cy = centre
    return [(cx + (x - cx) * k, cy + (y - cy) * k) for x, y in pts]


def wave_boost(dist_norm, beat, width=0.18, strength=0.55):
    """Extra line brightness at normalised distance from the centre: a ring that runs outward as the beat decays."""
    front = (1.0 - max(0.0, min(1.0, beat))) * 1.3
    return strength * beat * math.exp(-((dist_norm - front) / width) ** 2)


def twinkle(i, t, treble):
    """Per-dot 0..1 sparkle that grows with treble."""
    return max(0.0, min(1.0, treble * (0.5 + 0.5 * math.sin(t * 9.0 + i * 2.399))))
