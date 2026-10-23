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
        if e >= 1.0:
            return list(self.target["pts"])          # fully formed: the drift no longer shows
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


# ---------------------------------------------------------------- generative visualizer modes
#
# While music plays the ambient dots run one of several modes and morph between them. Every mode moves the
# same VIZ_N dots ("slots"); a mode may hide some (alpha 0). Morphs pair dots left-to-right so the cloud
# flows across instead of scrambling. All positions are screen pixels; nothing here touches cairo.

VIZ_N = 64
VIZ_MODES = ("ridge", "constellation", "wire", "rings", "orbits", "terrain", "doodle")
MODE_MORPH = 2.8                 # seconds for one mode to flow into the next
MODE_EVERY = (45.0, 100.0)       # seconds between random mode changes within a track
FIRST_CHANGE = (10.0, 18.0)      # the photo's ridge plays first, then the first random mode
PULSE_HOP = 0.32                 # seconds for a beat pulse to run along one edge
DEFAULT_PALETTE = [(0.72, 0.84, 1.0), (0.80, 0.94, 1.0), (1.0, 0.86, 0.72)]


def palette_from_bgrx(data, w, h, stride, n=3):
    """Pastel accent colours from a small photo copy (cairo RGB24 bytes): the photo's strongest hues,
    lifted to light tints so lines stay readable. -> [(r, g, b)] 0..1, falls back to cool blues."""
    import colorsys
    bins = [0.0] * 12
    for y in range(0, h, 2):
        row = y * stride
        for x in range(0, w, 2):
            p = row + 4 * x
            b, g, r = data[p] / 255.0, data[p + 1] / 255.0, data[p + 2] / 255.0
            hh, s, v = colorsys.rgb_to_hsv(r, g, b)
            if s > 0.18 and v > 0.18:
                bins[int(hh * 12) % 12] += s * v
    top = [k for k in sorted(range(12), key=lambda k: -bins[k]) if bins[k] > 0][:n]
    if not top:
        return list(DEFAULT_PALETTE)
    out = [colorsys.hsv_to_rgb((k + 0.5) / 12, 0.32, 1.0) for k in top]
    while len(out) < n:
        out.append(DEFAULT_PALETTE[len(out)])
    return out


def _nearest_edges(pts, k):
    """Each point linked to its k nearest neighbours (2D or 3D tuples), deduplicated."""
    edges = set()
    for i, p in enumerate(pts):
        near = sorted((sum((a - b) ** 2 for a, b in zip(p, q)), j) for j, q in enumerate(pts) if j != i)
        for _, j in near[:k]:
            edges.add((min(i, j), max(i, j)))
    return sorted(edges)


class _Mode:
    name = ""
    trail = 0.0                  # 0..1: how much of a motion streak each dot leaves

    def __init__(self, n, sw, sh, rng):
        self.n, self.sw, self.sh, self.rng = n, sw, sh, rng
        self.edges = []
        self.alpha = [1.0] * n
        self.depth = None        # per-dot 0 (far) .. 1 (near), or None for flat modes
        self.kick = 0.0          # decays from 1 after each beat
        self.ph = rng.uniform(0, 2 * math.pi)

    def step(self, dt, lv):
        self.kick = max(0.0, self.kick - dt * 1.5)
        self.ph += dt * (0.5 + 1.2 * lv.get("mid", 0.0))

    def beat(self):
        self.kick = 1.0

    def links(self):
        return [(i, j, 1.0) for i, j in self.edges]

    def points(self, t, lv):
        raise NotImplementedError


class StillMode(_Mode):
    """Whatever the dots held before the music (a doodle, an older ridge): the first mode flows out of it."""
    name = "still"

    def __init__(self, n, sw, sh, rng, shape):
        super().__init__(n, sw, sh, rng)
        pts = list(shape["pts"])[:n]
        self.alpha = [1.0] * len(pts) + [0.0] * (n - len(pts))
        self.base = pts + [pts[-1]] * (n - len(pts))
        self.edges = [(a, b) for a, b in shape["links"] if a < n and b < n]

    def points(self, t, lv):
        return self.base


class RidgeMode(_Mode):
    """The photo's own sky-line: peaks lift with the bass, a slow swell runs along it, beats ripple outward."""
    name = "ridge"

    def __init__(self, n, sw, sh, rng, base):
        super().__init__(n, sw, sh, rng)
        pts = list(base["pts"])
        self.m = len(pts)
        ys = [y for _, y in pts]
        top, bot = min(ys), max(ys)
        self.lift = [(bot - y) / (bot - top + 1e-6) for y in ys]
        self.base = pts + [pts[k % self.m] for k in range(n - self.m)]
        self.alpha = [1.0] * self.m + [0.0] * (n - self.m)
        self.lift += [0.0] * (n - self.m)
        self.edges = list(base["links"])
        self.k = rng.uniform(5.0, 9.0)
        self.kx = 0.5

    def beat(self):
        super().beat()
        self.kx = self.rng.uniform(0.2, 0.8)

    def points(self, t, lv):
        bass, mid, sw, sh = lv.get("bass", 0.0), lv.get("mid", 0.0), self.sw, self.sh
        out = []
        for (x, y), l in zip(self.base, self.lift):
            u = x / sw
            dy = -0.05 * sh * (0.25 + 0.75 * l) * (0.6 * bass + 0.4 * mid * math.sin(u * self.k + self.ph))
            if self.kick > 0.0:
                ring = abs(u - self.kx) - (1.0 - self.kick) * 0.7
                dy -= 0.03 * sh * self.kick * math.exp(-(ring / 0.07) ** 2)
            out.append((x, y + dy))
        return out


class ConstellationMode(_Mode):
    """Scattered stars that slowly drift; every beat a couple of links let go and re-link to a neighbour."""
    name = "constellation"

    def __init__(self, n, sw, sh, rng):
        super().__init__(n, sw, sh, rng)
        cx, cy, rx, ry = 0.57 * sw, 0.50 * sh, 0.31 * sw, 0.34 * sh
        pts, gap, tries = [], 0.07 * sh, 0
        while len(pts) < n:
            tries += 1
            if tries % 400 == 0:
                gap *= 0.85
            a, r = rng.uniform(0, 2 * math.pi), math.sqrt(rng.random())
            p = (cx + rx * r * math.cos(a), cy + ry * r * math.sin(a))
            if all(math.hypot(p[0] - q[0], p[1] - q[1]) >= gap for q in pts):
                pts.append(p)
        self.base = pts
        self.cx, self.cy = cx, cy
        self.seed = [rng.uniform(0, 2 * math.pi) for _ in range(n)]
        self.near = [[j for _, j in sorted((math.hypot(p[0] - q[0], p[1] - q[1]), j)
                                           for j, q in enumerate(pts) if j != i)[:6]] for i, p in enumerate(pts)]
        self.ea = {e: 1.0 for e in _nearest_edges(pts, 2)}       # edge -> current alpha
        self.et = dict.fromkeys(self.ea, 1.0)                      # edge -> target alpha

    def beat(self):
        super().beat()
        for _ in range(2):
            i = self.rng.randrange(self.n)
            mine = [e for e, a in self.et.items() if a > 0 and i in e]
            if len(mine) > 1:
                self.et[self.rng.choice(mine)] = 0.0
            j = self.rng.choice(self.near[i][1:5])
            e = (min(i, j), max(i, j))
            self.et[e] = 1.0
            self.ea.setdefault(e, 0.0)

    def step(self, dt, lv):
        super().step(dt, lv)
        for e in list(self.ea):
            a, tgt = self.ea[e], self.et[e]
            a = min(tgt, a + dt * 1.4) if a < tgt else max(tgt, a - dt * 1.0)
            if a <= 0.0 and tgt <= 0.0:
                del self.ea[e], self.et[e]
            else:
                self.ea[e] = a

    def links(self):
        return [(i, j, a) for (i, j), a in self.ea.items()]

    def points(self, t, lv):
        z = 1.0 + 0.07 * lv.get("bass", 0.0) + 0.03 * self.kick
        amp = 0.012 * self.sw
        out = []
        for (x, y), s in zip(self.base, self.seed):
            x = self.cx + (x - self.cx) * z + amp * math.sin(t * 0.21 + s)
            y = self.cy + (y - self.cy) * z + amp * math.cos(t * 0.17 + s * 1.3)
            out.append((x, y))
        return out


class WireMode(_Mode):
    """A slowly turning wireframe (geodesic sphere or torus) in perspective; bass pushes it toward you."""
    name = "wire"

    def __init__(self, n, sw, sh, rng):
        super().__init__(n, sw, sh, rng)
        self.kind = rng.choice(("sphere", "torus", "sphere"))
        if self.kind == "sphere":
            g = math.pi * (3 - math.sqrt(5))
            pts = []
            for k in range(n):
                z = 1 - 2 * (k + 0.5) / n
                r = math.sqrt(1 - z * z)
                pts.append((r * math.cos(k * g), r * math.sin(k * g), z))
            self.edges = _nearest_edges(pts, 3)
        else:
            a = max(4, round(math.sqrt(n * 2)))
            b = max(3, n // a)
            pts = []
            for i in range(a):
                for j in range(b):
                    u, v = 2 * math.pi * i / a, 2 * math.pi * j / b
                    rr = 0.72 + 0.30 * math.cos(v)
                    pts.append((rr * math.cos(u), 0.30 * math.sin(v), rr * math.sin(u)))
            for i in range(a):
                for j in range(b):
                    k = i * b + j
                    self.edges += [(k, ((i + 1) % a) * b + j), (k, i * b + (j + 1) % b)]
            self.alpha = [1.0] * len(pts) + [0.0] * (n - len(pts))
            pts += [pts[0]] * (n - len(pts))
        self.p3 = pts
        self.yaw = rng.uniform(0, 2 * math.pi)
        self.tilt = rng.uniform(0.35, 0.75)
        self.spin = rng.choice((-1, 1)) * rng.uniform(0.10, 0.17)
        self.cx, self.cy, self.r = 0.58 * sw, 0.50 * sh, 0.30 * sh

    def step(self, dt, lv):
        super().step(dt, lv)
        self.yaw += dt * self.spin * (1.0 + 1.5 * lv.get("mid", 0.0) + 2.0 * self.kick)

    def points(self, t, lv):
        cy_, sy = math.cos(self.yaw), math.sin(self.yaw)
        tl = self.tilt + 0.08 * math.sin(t * 0.13)
        ct, st = math.cos(tl), math.sin(tl)
        r = self.r * (1.0 + 0.12 * lv.get("bass", 0.0) + 0.04 * self.kick)
        out, depth = [], []
        for x, y, z in self.p3:
            x, z = x * cy_ + z * sy, -x * sy + z * cy_
            y, z = y * ct - z * st, y * st + z * ct
            k = 3.2 / (3.2 - z)
            out.append((self.cx + x * r * k, self.cy + y * r * k))
            depth.append((z + 1) / 2)
        self.depth = depth
        return out


class RingsMode(_Mode):
    """Concentric polygons turning against each other, joined by spokes; beats send a ring of light outward."""
    name = "rings"

    def __init__(self, n, sw, sh, rng):
        super().__init__(n, sw, sh, rng)
        rings = rng.choice((3, 4, 4))
        weights = [k + 1 for k in range(rings)]
        counts = [max(4, round((n - 1) * w / sum(weights))) for w in weights]
        while sum(counts) > n - 1:
            counts[counts.index(max(counts))] -= 1
        self.ring = [None]
        self.ang = [0.0]
        self.edges = []
        start = 1
        for k, c in enumerate(counts):
            off = rng.uniform(0, 2 * math.pi)
            for i in range(c):
                self.ring.append(k)
                self.ang.append(off + 2 * math.pi * i / c)
                self.edges.append((start + i, start + (i + 1) % c))
                if k == 0:
                    self.edges.append((0, start + i))
            start += c
        used = len(self.ring)
        for i in range(1, used):                       # a spoke from each dot to the nearest dot one ring in
            k = self.ring[i]
            if k:
                inner = [j for j in range(1, used) if self.ring[j] == k - 1]
                j = min(inner, key=lambda j: abs(math.remainder(self.ang[j] - self.ang[i], 2 * math.pi)))
                if i % 2 == 0:
                    self.edges.append((j, i))
        self.alpha = [1.0] * used + [0.0] * (n - used)
        self.ring += [None] * (n - used)
        self.ang += [0.0] * (n - used)
        self.rings = rings
        self.speed = [rng.uniform(0.04, 0.10) * (1 if k % 2 else -1) for k in range(rings)]
        self.turn = [0.0] * rings
        self.cx, self.cy, self.r = 0.57 * sw, 0.50 * sh, 0.36 * sh
        self.lobes = rng.choice((3, 4, 5))

    def step(self, dt, lv):
        super().step(dt, lv)
        for k in range(self.rings):
            self.turn[k] += dt * self.speed[k] * (1.0 + 1.2 * lv.get("mid", 0.0))

    def points(self, t, lv):
        bass, mid = lv.get("bass", 0.0), lv.get("mid", 0.0)
        front = (1.0 - self.kick) * 1.3
        out = []
        for k, a in zip(self.ring, self.ang):
            if k is None:
                out.append((self.cx, self.cy))
                continue
            rn = (k + 1) / self.rings
            a += self.turn[k]
            r = rn * (1.0 + 0.07 * bass + 0.06 * mid * math.sin(self.lobes * a + self.ph))
            r += 0.10 * self.kick * math.exp(-((rn - front) / 0.18) ** 2)
            out.append((self.cx + self.r * r * math.cos(a), self.cy + self.r * r * math.sin(a)))
        return out


class OrbitsMode(_Mode):
    """Dots on tilted elliptical orbits, inner ones faster; they leave short streaks."""
    name = "orbits"
    trail = 1.0

    def __init__(self, n, sw, sh, rng):
        super().__init__(n, sw, sh, rng)
        m = rng.randint(3, 5)
        R = 0.40 * sh
        self.orb = []
        for k in range(m):
            a = R * (0.32 + 0.68 * k / (m - 1))
            self.orb.append({"a": a * 1.35, "b": a * rng.uniform(0.35, 0.62), "rot": rng.uniform(-0.6, 0.6),
                             "w": rng.choice((-1, 1)) * 0.22 / (a / R) ** 1.5, "ph": rng.uniform(0, 6.3)})
        sizes = [o["a"] for o in self.orb]
        counts = [max(3, round(n * s / sum(sizes))) for s in sizes]
        while sum(counts) > n:
            counts[counts.index(max(counts))] -= 1
        while sum(counts) < n:
            counts[counts.index(min(counts))] += 1
        self.who, self.off = [], []
        start = 0
        for k, c in enumerate(counts):
            for i in range(c):
                self.who.append(k)
                self.off.append(2 * math.pi * i / c + rng.uniform(-0.15, 0.15))
                if rng.random() > 0.35:
                    self.edges.append((start + i, start + (i + 1) % c))
            start += c
        self.cx, self.cy = 0.57 * sw, 0.50 * sh

    def step(self, dt, lv):
        super().step(dt, lv)
        for o in self.orb:
            o["ph"] += dt * o["w"] * (0.7 + 1.3 * lv.get("mid", 0.0) + 2.5 * self.kick)

    def points(self, t, lv):
        z = 1.0 + 0.06 * lv.get("bass", 0.0)
        out, depth = [], []
        for k, off in zip(self.who, self.off):
            o = self.orb[k]
            a = o["ph"] + off
            x, y = o["a"] * z * math.cos(a), o["b"] * z * math.sin(a)
            c, s = math.cos(o["rot"]), math.sin(o["rot"])
            out.append((self.cx + x * c - y * s, self.cy + x * s + y * c))
            depth.append(0.5 + 0.5 * math.sin(a))
        self.depth = depth
        return out


class TerrainMode(_Mode):
    """A low-poly landscape seen from above the ground: swells roll toward you, bass raises a wave."""
    name = "terrain"

    def __init__(self, n, sw, sh, rng):
        super().__init__(n, sw, sh, rng)
        cols = 11
        rows = max(3, n // cols)
        self.grid = [(c / (cols - 1) * 2 - 1, r / (rows - 1)) for r in range(rows) for c in range(cols)]
        used = len(self.grid)
        for r in range(rows):
            for c in range(cols):
                k = r * cols + c
                if c + 1 < cols:
                    self.edges.append((k, k + 1))
                if r + 1 < rows:
                    self.edges.append((k, k + cols))
                    if c + 1 < cols:
                        self.edges.append((k, k + cols + 1) if (r + c) % 2 else (k + 1, k + cols))
        self.alpha = [1.0] * used + [0.0] * (n - used)
        self.grid += [self.grid[-1]] * (n - used)
        self.waves = [(rng.uniform(1.5, 3.5), rng.uniform(3.0, 6.0), rng.uniform(0.5, 1.0), rng.uniform(0, 6.3),
                       rng.uniform(0.04, 0.08)) for _ in range(3)]
        self.cx, self.near_y, self.horizon = 0.60 * sw, 0.80 * sh, 0.40 * sh

    def points(self, t, lv):
        bass, mid = lv.get("bass", 0.0), lv.get("mid", 0.0)
        front = 1.0 - (1.0 - self.kick) * 1.2
        out, depth = [], []
        for x, z in self.grid:
            h = sum(a * math.sin(kx * x + kz * z + w * t + p) for kx, kz, w, p, a in self.waves) * (0.6 + 0.6 * mid)
            h += 0.10 * bass * (1.0 - z) * (0.6 + 0.4 * math.sin(3 * x + self.ph))
            h += 0.08 * self.kick * math.exp(-((z - front) / 0.16) ** 2)
            p = 1.0 / (1.0 + 1.9 * z)
            y = self.horizon + (self.near_y - self.horizon) * (p - 1 / 2.9) / (1 - 1 / 2.9)
            out.append((self.cx + x * 0.33 * self.sw * p, y - h * self.sh * 0.9 * p))
            depth.append(1.0 - z)
        self.depth = depth
        return out


class DoodleMode(_Mode):
    """A Quick, Draw! animal traced by the dots; it breathes with the bass and hops on beats."""
    name = "doodle"

    def __init__(self, n, sw, sh, rng, doodle):
        super().__init__(n, sw, sh, rng)
        placed = place_doodle(doodle, sw, sh, rng)
        P = placed["pts"]
        strokes = [st for st in doodle["strokes"] if len(st) >= 2]
        lens = [sum(math.hypot(P[a][0] - P[b][0], P[a][1] - P[b][1]) for a, b in zip(st, st[1:])) or 1.0
                for st in strokes]
        counts = [max(2, round(n * L / sum(lens))) for L in lens]
        while sum(counts) > n:
            counts[counts.index(max(counts))] -= 1
        pts = []
        for st, L, c in zip(strokes, lens, counts):
            start = len(pts)
            for q in range(c):
                d, acc = L * q / max(1, c - 1), 0.0
                for a, b in zip(st, st[1:]):
                    seg = math.hypot(P[a][0] - P[b][0], P[a][1] - P[b][1])
                    if acc + seg >= d or b == st[-1]:
                        u = 0.0 if seg == 0 else min(1.0, (d - acc) / seg)
                        pts.append((P[a][0] + (P[b][0] - P[a][0]) * u, P[a][1] + (P[b][1] - P[a][1]) * u))
                        break
                    acc += seg
            self.edges += [(k, k + 1) for k in range(start, len(pts) - 1)]
        self.alpha = [1.0] * len(pts) + [0.0] * (n - len(pts))
        pts += [pts[-1]] * (n - len(pts))
        self.base = pts
        self.cx = sum(x for x, _ in pts) / n
        self.cy = sum(y for _, y in pts) / n

    def points(self, t, lv):
        z = 1.0 + 0.06 * lv.get("bass", 0.0)
        hop = 0.025 * self.sh * math.sin(math.pi * self.kick) if self.kick > 0 else 0.0
        wob = 3.0 * self.sh / 1080 * (0.3 + lv.get("mid", 0.0))
        return [(self.cx + (x - self.cx) * z + wob * math.sin(t * 1.3 + i * 0.7),
                 self.cy + (y - self.cy) * z - hop + wob * math.cos(t * 1.1 + i * 0.9))
                for i, (x, y) in enumerate(self.base)]


class Viz:
    """The running visualizer: the current mode, the one it is flowing out of, beat pulses and colours."""

    def __init__(self, base, sw, sh, rng, doodles=(), palette=None, modes=None, every=None, start=None):
        self.base = base
        self.n = max(VIZ_N, len(base["pts"]))
        self.sw, self.sh, self.rng = sw, sh, rng
        self.doodles = list(doodles)
        self.names = [m for m in (modes or VIZ_MODES) if m in VIZ_MODES and (m != "doodle" or self.doodles)]
        self.ordered = bool(modes)       # an explicit list (screenshots) plays in order instead of at random
        self.every = every
        self.palette = list(palette or DEFAULT_PALETTE)
        self.cur = self.build("ridge")
        self.perm = list(range(self.n))
        self.prev = self.prev_perm = None
        self.blend = 1.0
        self.pulses = []
        self.last = None
        self.since = 0.0
        self.next_change = every or rng.uniform(*FIRST_CHANGE)
        self.colour = 0
        if start is not None and start is not base and start.get("pts"):
            self.prev, self.prev_perm = StillMode(self.n, sw, sh, rng, start), list(range(self.n))
            self.blend = 0.0

    def build(self, name):
        n, sw, sh, rng = self.n, self.sw, self.sh, self.rng
        if name == "ridge":
            return RidgeMode(n, sw, sh, rng, self.base)
        if name == "doodle" and self.doodles:
            return DoodleMode(n, sw, sh, rng, rng.choice(self.doodles))
        cls = {"constellation": ConstellationMode, "wire": WireMode, "rings": RingsMode,
               "orbits": OrbitsMode, "terrain": TerrainMode}.get(name, ConstellationMode)
        return cls(n, sw, sh, rng)

    @property
    def mode(self):
        return self.cur.name

    @property
    def settled(self):
        return self.prev is None

    def set_base(self, base):
        """New photo: later ridges follow the new sky-line (a showing ridge flows into the new one)."""
        if len(base["pts"]) > self.n:
            return
        self.base = base
        if self.cur.name == "ridge":
            self.switch("ridge")

    def switch(self, name=None, t=0.0, lv=None):
        lv = lv or {}
        if name is None:
            pool = [m for m in self.names if m != self.cur.name] or self.names or ["ridge"]
            if self.ordered:
                k = self.names.index(self.cur.name) + 1 if self.cur.name in self.names else 0
                name = self.names[k % len(self.names)]
            else:
                name = self.rng.choice(pool)
        mode = self.build(name)
        mp = mode.points(t, lv)
        now = self.last or self.slot(self.cur, self.perm, self.cur.points(t, lv))
        order_m = sorted(range(self.n), key=lambda k: (mode.alpha[k] == 0, mp[k][0]))
        order_s = sorted(range(self.n), key=lambda i: now[i][0])
        perm = [0] * self.n
        for a, b in zip(order_m, order_s):
            perm[a] = b
        self.prev, self.prev_perm = self.cur, self.perm
        self.cur, self.perm = mode, perm
        self.blend = 0.0
        self.since = 0.0
        self.next_change = self.every or self.rng.uniform(*MODE_EVERY)
        self.colour += 1

    def slot(self, mode, perm, pts):
        out = [None] * self.n
        for k, p in enumerate(pts):
            out[perm[k]] = p
        return out

    def step(self, dt, lv, beat=False, t=0.0, auto=True):
        self.cur.step(dt, lv)
        if self.prev:
            self.prev.step(dt, lv)
        if beat:
            self.cur.beat()
            self.spawn_pulses()
        if self.prev:
            self.blend = min(1.0, self.blend + dt / MODE_MORPH)
            if self.blend >= 1.0:
                self.prev = self.prev_perm = None
        self.since += dt
        if (auto and self.prev is None and any(m != self.cur.name for m in self.names)
                and self.since >= self.next_change):
            self.switch(t=t, lv=lv)
        live = []
        for p in self.pulses:
            p["u"] += dt / PULSE_HOP
            if p["u"] >= 1.0:
                p["hops"] -= 1
                nxt = [e for e in self._adj.get(p["b"], ()) if e != p["a"]] if p["hops"] > 0 else []
                if not nxt:
                    continue
                p["a"], p["b"], p["u"] = p["b"], self.rng.choice(nxt), p["u"] - 1.0
            live.append(p)
        self.pulses = live

    def spawn_pulses(self):
        links = [(self.perm[i], self.perm[j]) for i, j, a in self.cur.links()
                 if a > 0.5 and self.cur.alpha[i] > 0 and self.cur.alpha[j] > 0]
        if not links:
            return
        adj = {}
        for a, b in links:
            adj.setdefault(a, []).append(b)
            adj.setdefault(b, []).append(a)
        self._adj = adj
        for _ in range(self.rng.randint(2, 3)):
            if len(self.pulses) >= 9:
                break
            a, b = self.rng.choice(links)
            if self.rng.random() < 0.5:
                a, b = b, a
            self.pulses.append({"a": a, "b": b, "u": 0.0, "hops": self.rng.randint(2, 4)})

    _adj = {}

    def frame(self, t, lv):
        """-> {"pts", "alpha", "depth", "links": [(i, j, a)], "pulses": [(x0, y0, x1, y1, a)], "trail", "hue"}"""
        cur = self.cur
        pts = self.slot(cur, self.perm, cur.points(t, lv))
        alpha = self.slot(cur, self.perm, cur.alpha)
        depth = self.slot(cur, self.perm, cur.depth) if cur.depth else None
        links = {}
        e = 1.0
        if self.prev:
            e = smooth(self.blend)
            pv = self.prev
            pp = self.slot(pv, self.prev_perm, pv.points(t, lv))
            pa = self.slot(pv, self.prev_perm, pv.alpha)
            pd = self.slot(pv, self.prev_perm, pv.depth) if pv.depth else [0.7] * self.n
            cd = depth or [0.7] * self.n
            pts = [(x0 + (x1 - x0) * e, y0 + (y1 - y0) * e) for (x0, y0), (x1, y1) in zip(pp, pts)]
            alpha = [a0 + (a1 - a0) * e for a0, a1 in zip(pa, alpha)]
            depth = [d0 + (d1 - d0) * e for d0, d1 in zip(pd, cd)] if (pv.depth or cur.depth) else None
            for i, j, a in pv.links():
                k = (self.prev_perm[i], self.prev_perm[j])
                links[k] = links.get(k, 0.0) + a * (1 - e)
        for i, j, a in cur.links():
            k = (self.perm[i], self.perm[j])
            links[k] = links.get(k, 0.0) + a * e
        pulses = []
        for p in self.pulses:
            (x0, y0), (x1, y1) = pts[p["a"]], pts[p["b"]]
            u0 = max(0.0, p["u"] - 0.35)
            pulses.append((x0 + (x1 - x0) * u0, y0 + (y1 - y0) * u0,
                           x0 + (x1 - x0) * p["u"], y0 + (y1 - y0) * p["u"], 1.0))
        self.last = pts
        trail = cur.trail * e + (self.prev.trail * (1 - e) if self.prev else 0.0)
        return {"pts": pts, "alpha": alpha, "depth": depth, "trail": trail, "pulses": pulses,
                "links": [(i, j, a) for (i, j), a in links.items() if a > 0.01],
                "colour": self.colour}
