// The TV's Circuit visualizer (tvapp/widgets/shapes.py CircuitMode + the parts of Viz it uses, and
// homescreen.py draw_viz), ported for the wallpaper. Positions are CSS pixels.
"use strict";

const VIZ_N = 64;
const MODE_MORPH = 2.8;          // s for one picture's runners to flow onto the next picture's contours
const PULSE_HOP = 0.32;          // s for a beat pulse to run along one link
const SPARK_SHARE = 5, SPARK_DEPTH = 0.5;
const DEFAULT_PALETTE = [[0.72, 0.84, 1.0], [0.80, 0.94, 1.0], [1.0, 0.86, 0.72]];

const smooth = (p) => p * p * (3 - 2 * p);

// Mulberry32: seeded so a reload looks the same for the same seed
function makeRng(seed) {
  let a = seed >>> 0;
  const rnd = () => {
    a = (a + 0x6D2B79F5) >>> 0;
    let t = a;
    t = Math.imul(t ^ (t >>> 15), t | 1);
    t ^= t + Math.imul(t ^ (t >>> 7), t | 61);
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  };
  rnd.int = (lo, hi) => lo + Math.floor(rnd() * (hi - lo + 1));
  rnd.choice = (arr) => arr[Math.floor(rnd() * arr.length)];
  rnd.sample = (arr, k) => {
    const c = arr.slice();
    for (let i = c.length - 1; i > 0; i--) {
      const j = Math.floor(rnd() * (i + 1));
      [c[i], c[j]] = [c[j], c[i]];
    }
    return c.slice(0, k);
  };
  return rnd;
}

// Image-normalised paths -> screen-pixel polylines inside the cover crop, split where they leave the screen.
function coverPaths(paths, iw, ih, sw, sh) {
  const k = Math.max(sw / iw, sh / ih);
  const ox = (sw - iw * k) / 2, oy = (sh - ih * k) / 2;
  const inside = (x, y) => x >= 0 && x <= sw && y >= 0 && y <= sh;
  const runs = [];
  for (const path of paths) {
    let run = [];
    const pts = path.map(([x, y]) => [ox + x * iw * k, oy + y * ih * k]);
    for (let i = 1; i < pts.length; i++) {
      const c = clipSegment(pts[i - 1], pts[i], sw, sh);
      if (!c) {
        if (run.length > 1) runs.push(run);
        run = [];
        continue;
      }
      const [a, b] = c;
      const last = run[run.length - 1];
      if (!last || Math.abs(last[0] - a[0]) > 0.5 || Math.abs(last[1] - a[1]) > 0.5) {
        if (run.length > 1) runs.push(run);
        run = [a];
      }
      run.push(b);
      if (!inside(...pts[i])) {
        runs.push(run);
        run = [];
      }
    }
    if (run.length > 1) runs.push(run);
  }
  const len = (r) => r.reduce((s, p, i) => (i ? s + Math.hypot(p[0] - r[i - 1][0], p[1] - r[i - 1][1]) : 0), 0);
  return runs.map((r) => [len(r), r]).filter(([l]) => l >= Math.min(sw, sh) * 0.12)
    .sort((a, b) => b[0] - a[0]).slice(0, 24).map(([, r]) => r);
}

// Liang-Barsky clip of segment a-b to [0,w]x[0,h] -> [a', b'] or null
function clipSegment(a, b, w, h) {
  let t0 = 0, t1 = 1;
  const dx = b[0] - a[0], dy = b[1] - a[1];
  const p = [-dx, dx, -dy, dy], q = [a[0], w - a[0], a[1], h - a[1]];
  for (let i = 0; i < 4; i++) {
    if (p[i] === 0) {
      if (q[i] < 0) return null;
    } else {
      const r = q[i] / p[i];
      if (p[i] < 0) { if (r > t1) return null; if (r > t0) t0 = r; }
      else { if (r < t0) return null; if (r < t1) t1 = r; }
    }
  }
  return [[a[0] + t0 * dx, a[1] + t0 * dy], [a[0] + t1 * dx, a[1] + t1 * dy]];
}

class CircuitMode {
  // routes: screen-pixel polylines (from coverPaths)
  constructor(n, sw, sh, rng, routes) {
    this.n = n; this.sw = sw; this.sh = sh;
    this.kick = 0;
    this.routes = [];
    for (const path of routes) {
      const pts = [], lengths = [0];
      for (const p of path) {
        if (pts.length) {
          const q = pts[pts.length - 1];
          const d = Math.hypot(p[0] - q[0], p[1] - q[1]);
          if (d === 0) continue;
          lengths.push(lengths[lengths.length - 1] + d);
        }
        pts.push(p);
      }
      if (pts.length > 1) this.routes.push([pts, lengths]);
    }
    this.trains = Math.max(1, Math.floor(n / 8));
    this.route = Array.from({length: this.trains}, (_, k) => Math.floor(k * this.routes.length / this.trains));
    this.travel = this.route.map((r) => rng() * this.routes[r][1][this.routes[r][1].length - 1] * 2);
    this.edges = [];
    for (let k = 0; k < this.trains; k++) for (let j = 0; j < 7; j++) this.edges.push([k * 8 + j, k * 8 + j + 1]);
    this.heads = Array.from({length: n}, (_, k) => (k % 8 === 0 && k < this.trains * 8 ? 1 : 0));
    this.alpha = Array.from({length: n}, (_, k) => (k >= this.trains * 8 ? 0 : k % 8 === 0 ? 1 : 0.8 * (1 - (k % 8) / 8)));
    this.depth = Array.from({length: n}, (_, k) => (k % 8 === 0 ? 1 : 0.65));
  }

  step(dt, lv) {
    this.kick = Math.max(0, this.kick - dt * 1.5);
    const speed = this.sh * (0.018 + 0.075 * (lv.mid || 0) + 0.06 * (lv.bass || 0)
                             + 0.24 * this.kick + 0.12 * (lv.bass_hit || 0));
    for (let k = 0; k < this.route.length; k++) {
      const ls = this.routes[this.route[k]][1];
      this.travel[k] = (this.travel[k] + dt * speed) % (2 * ls[ls.length - 1]);
    }
  }

  beat() { this.kick = 1; }

  links() { return this.edges.map(([i, j]) => [i, j, 1]); }

  sample(route, distance) {
    const [pts, lengths] = this.routes[route];
    const length = lengths[lengths.length - 1];
    distance = ((distance % (2 * length)) + 2 * length) % (2 * length);
    if (distance > length) distance = 2 * length - distance;
    let lo = 0, hi = lengths.length;            // bisect_right
    while (lo < hi) { const m = (lo + hi) >> 1; if (distance < lengths[m]) hi = m; else lo = m + 1; }
    const j = Math.min(pts.length - 1, Math.max(1, lo));
    const u = (distance - lengths[j - 1]) / (lengths[j] - lengths[j - 1]);
    const [x, y] = pts[j - 1], [xx, yy] = pts[j];
    return [x + (xx - x) * u, y + (yy - y) * u];
  }

  points() {
    const spacing = 0.010 * this.sh, out = [];
    for (let k = 0; k < this.route.length; k++)
      for (let j = 0; j < 8; j++) out.push(this.sample(this.route[k], this.travel[k] - j * spacing));
    while (out.length < this.n) out.push(out[out.length - 1]);
    return out;
  }
}

// Viz, cut down to Circuit: morphs between pictures, beat pulses, treble sparks, colour cycling.
class CircuitViz {
  constructor(rng) {
    this.rng = rng;
    this.n = VIZ_N;
    this.cur = null; this.perm = null;
    this.prev = null; this.prevPerm = null;
    this.blend = 1;
    this.pulses = [];
    this.adj = new Map();
    this.last = null;
    this.colour = 0;
    this.onset = null;
    this.hit = [0, 0, 0];
    this.spark = new Map();
    this.sparkAmt = 0;
    this.palette = DEFAULT_PALETTE;
  }

  // a new picture (or size): morph the runners onto it unless instant
  set(routes, sw, sh, palette, instant) {
    const mode = new CircuitMode(this.n, sw, sh, this.rng, routes);
    this.palette = palette && palette.length ? palette : DEFAULT_PALETTE;
    this.colour++;
    this.pulses = [];
    if (!this.cur || instant) {
      this.cur = mode;
      this.perm = Array.from({length: this.n}, (_, i) => i);
      this.prev = null;
      this.blend = 1;
      this.last = null;
      return;
    }
    const mp = mode.points();
    const now = this.last || this.slot(this.perm, this.cur.points());
    const orderM = [...Array(this.n).keys()].sort((a, b) =>
      ((mode.alpha[a] === 0) - (mode.alpha[b] === 0)) || (mp[a][0] - mp[b][0]));
    const orderS = [...Array(this.n).keys()].sort((a, b) => now[a][0] - now[b][0]);
    const perm = new Array(this.n);
    orderM.forEach((a, k) => { perm[a] = orderS[k]; });
    this.prev = this.cur; this.prevPerm = this.perm;
    this.cur = mode; this.perm = perm;
    this.blend = 0;
  }

  slot(perm, vals) {
    const out = new Array(this.n);
    vals.forEach((v, k) => { out[perm[k]] = v; });
    return out;
  }

  jumped(k, v) {
    const j = v > 0.8 && v > this.hit[k] + 0.3;
    this.hit[k] = v;
    return j;
  }

  step(dt, lv, onset) {
    this.cur.step(dt, lv);
    if (this.prev) this.prev.step(dt, lv);
    let kicked = this.jumped(0, lv.bass_hit || 0);
    if (onset !== undefined && onset !== null) {
      kicked = this.onset !== null && onset !== this.onset;
      this.onset = onset;
    }
    this.jumped(1, lv.mid_hit || 0);
    if (this.jumped(2, lv.treble_hit || 0)) {
      const shown = [...Array(this.n).keys()].filter((k) => this.cur.alpha[k] > 0.3);
      this.spark = new Map(this.rng.sample(shown, Math.min(shown.length, Math.max(1, Math.floor(shown.length / SPARK_SHARE))))
        .map((k) => [k, 1]));
    }
    this.sparkAmt = this.spark.size ? (lv.treble_hit || 0) : 0;
    if (this.sparkAmt <= 0.02) this.spark = new Map();
    if (kicked) {
      this.cur.beat();
      this.spawnPulses();
    }
    if (this.prev) {
      this.blend = Math.min(1, this.blend + dt / MODE_MORPH);
      if (this.blend >= 1) this.prev = this.prevPerm = null;
    }
    const live = [];
    for (const p of this.pulses) {
      p.u += dt / PULSE_HOP;
      if (p.u >= 1) {
        p.hops--;
        const nxt = p.hops > 0 ? (this.adj.get(p.b) || []).filter((e) => e !== p.a) : [];
        if (!nxt.length) continue;
        p.a = p.b; p.b = this.rng.choice(nxt); p.u -= 1;
      }
      live.push(p);
    }
    this.pulses = live;
  }

  spawnPulses() {
    const links = this.cur.links().filter(([i, j, a]) => a > 0.5 && this.cur.alpha[i] > 0 && this.cur.alpha[j] > 0)
      .map(([i, j]) => [this.perm[i], this.perm[j]]);
    if (!links.length) return;
    this.adj = new Map();
    for (const [a, b] of links) {
      if (!this.adj.has(a)) this.adj.set(a, []);
      if (!this.adj.has(b)) this.adj.set(b, []);
      this.adj.get(a).push(b); this.adj.get(b).push(a);
    }
    for (let q = this.rng.int(2, 3); q > 0 && this.pulses.length < 9; q--) {
      let [a, b] = this.rng.choice(links);
      if (this.rng() < 0.5) [a, b] = [b, a];
      this.pulses.push({a, b, u: 0, hops: this.rng.int(2, 4)});
    }
  }

  frame() {
    const cur = this.cur;
    let pts = this.slot(this.perm, cur.points());
    let alpha = this.slot(this.perm, cur.alpha);
    let depth = this.slot(this.perm, cur.depth);
    let heads = this.slot(this.perm, cur.heads);
    const links = new Map();
    const key = (i, j) => i * 1000 + j;
    let e = 1;
    if (this.prev) {
      e = smooth(this.blend);
      const pv = this.prev, pp = this.prevPerm;
      const ppts = this.slot(pp, pv.points()), pa = this.slot(pp, pv.alpha);
      const pd = this.slot(pp, pv.depth), ph = this.slot(pp, pv.heads);
      pts = pts.map(([x1, y1], i) => [ppts[i][0] + (x1 - ppts[i][0]) * e, ppts[i][1] + (y1 - ppts[i][1]) * e]);
      alpha = alpha.map((a1, i) => pa[i] + (a1 - pa[i]) * e);
      depth = depth.map((d1, i) => pd[i] + (d1 - pd[i]) * e);
      heads = heads.map((h1, i) => ph[i] + (h1 - ph[i]) * e);
      for (const [i, j, a] of pv.links()) {
        const k = key(pp[i], pp[j]);
        links.set(k, (links.get(k) || 0) + a * (1 - e));
      }
    }
    for (const [i, j, a] of cur.links()) {
      const k = key(this.perm[i], this.perm[j]);
      links.set(k, (links.get(k) || 0) + a * e);
    }
    const pulses = this.pulses.map((p) => {
      const [x0, y0] = pts[p.a], [x1, y1] = pts[p.b];
      const u0 = Math.max(0, p.u - 0.35);
      return [x0 + (x1 - x0) * u0, y0 + (y1 - y0) * u0, x0 + (x1 - x0) * p.u, y0 + (y1 - y0) * p.u, 1];
    });
    if (this.spark.size) {
      for (const k of this.spark.keys()) {
        const i = this.perm[k];
        depth[i] = Math.min(1.25, depth[i] + SPARK_DEPTH * this.sparkAmt);
      }
    }
    this.last = pts;
    const out = [];
    for (const [k, a] of links) if (a > 0.01) out.push([Math.floor(k / 1000), k % 1000, a]);
    return {pts, alpha, depth, heads, pulses, links: out, colour: this.colour};
  }
}

function twinkle(i, t, treble) {
  return Math.max(0, Math.min(1, treble * (0.5 + 0.5 * Math.sin(t * 9 + i * 2.399))));
}

// homescreen.py draw_viz for the Circuit (diamond dots, glowing heads, beat pulses)
class Renderer {
  constructor(canvas) {
    this.canvas = canvas;
    this.ctx = canvas.getContext("2d");
    this.glow = null;
    this.tints = new Map();
  }

  resize(sw, sh, dpr) {
    this.canvas.width = Math.round(sw * dpr);
    this.canvas.height = Math.round(sh * dpr);
    this.dpr = dpr;
    this.s = sh / 1080;
    this.glow = null;
    this.tints.clear();
  }

  // a soft round light per colour, rendered once and stamped with globalAlpha
  sprite(rgb) {
    const r = Math.max(8, Math.round(22 * this.s));
    const id = rgb.join(",");
    let c = this.tints.get(id);
    if (!c) {
      c = document.createElement("canvas");
      c.width = c.height = 2 * r;
      const g = c.getContext("2d");
      const col = rgb.map((v) => Math.round(v * 255)).join(",");
      const gr = g.createRadialGradient(r, r, 0, r, r, r);
      gr.addColorStop(0, `rgba(${col},1)`);
      gr.addColorStop(0.25, `rgba(${col},0.45)`);
      gr.addColorStop(1, `rgba(${col},0)`);
      g.fillStyle = gr;
      g.fillRect(0, 0, 2 * r, 2 * r);
      this.tints.set(id, c);
    }
    return [c, r];
  }

  draw(fr, lv, t, palette, gainMul) {
    const ctx = this.ctx, s = this.s;
    ctx.setTransform(this.dpr, 0, 0, this.dpr, 0, 0);
    ctx.clearRect(0, 0, this.canvas.width / this.dpr, this.canvas.height / this.dpr);
    const k = fr.colour, pal = palette;
    const line = pal[k % pal.length], spark = pal[(k + 1) % pal.length], halo = pal[(k + 2) % pal.length];
    const css = (rgb, a) => `rgba(${Math.round(rgb[0] * 255)},${Math.round(rgb[1] * 255)},${Math.round(rgb[2] * 255)},${a})`;
    const {bass, mid, beat, treble} = lv;
    const {pts, alpha, depth} = fr;
    const gain = (0.55 + 0.45 * Math.min(1, bass + mid)) * gainMul;
    const baseA = (0.11 + 0.06 * mid + 0.10 * beat) * (0.55 + 0.45 * Math.min(1, bass + mid)) * gainMul;
    ctx.lineCap = "round";
    ctx.strokeStyle = css(line, 1);
    for (const [i, j, la] of fr.links) {
      const dd = (depth[i] + depth[j]) / 2;
      const a = baseA * la * Math.min(alpha[i], alpha[j]) * (0.30 + 0.70 * dd);
      if (a < 0.01) continue;
      ctx.globalAlpha = Math.min(0.8, a);
      ctx.lineWidth = Math.max(1, 1.1 * s * (0.6 + 0.6 * dd));
      ctx.beginPath();
      ctx.moveTo(pts[i][0], pts[i][1]);
      ctx.lineTo(pts[j][0], pts[j][1]);
      ctx.stroke();
    }
    if (fr.pulses.length) {
      ctx.strokeStyle = css(spark, 1);
      ctx.lineWidth = Math.max(1.6, 2.4 * s);
      for (const [x0, y0, x1, y1, a] of fr.pulses) {
        ctx.globalAlpha = 0.6 * a * gainMul;
        ctx.beginPath();
        ctx.moveTo(x0, y0);
        ctx.lineTo(x1, y1);
        ctx.stroke();
      }
    }
    const [haloS, r] = this.sprite(halo), [sparkS] = this.sprite(spark);
    ctx.fillStyle = "#fff";
    for (let i = 0; i < pts.length; i++) {
      const a = alpha[i];
      if (a < 0.02) continue;
      const [x, y] = pts[i], dd = depth[i];
      const tw = treble > 0.02 ? twinkle(i, t, treble) : 0;
      if (i % 5 === 0 && dd > 0.45 && (beat > 0.05 || tw > 0.3)) {
        ctx.globalAlpha = Math.min(0.5, (0.35 * beat + 0.25 * tw) * a * dd) * gainMul;
        ctx.drawImage(haloS, x - r, y - r);
      }
      if (fr.heads[i] > 0.01) {
        ctx.globalAlpha = (0.22 + 0.35 * beat) * a * fr.heads[i] * gainMul;
        ctx.drawImage(sparkS, x - r, y - r);
      }
      const d = (2.0 + 2.2 * dd) * s * (1 + 0.18 * bass + 0.5 * tw);
      ctx.globalAlpha = Math.min(0.8, (0.30 + 0.40 * dd + 0.3 * tw) * a * gain);
      ctx.beginPath();
      ctx.moveTo(x, y - d);
      ctx.lineTo(x + d, y);
      ctx.lineTo(x, y + d);
      ctx.lineTo(x - d, y);
      ctx.closePath();
      ctx.fill();
    }
    for (const [, , x1, y1, a] of fr.pulses) {
      ctx.globalAlpha = 0.45 * a * gainMul;
      ctx.drawImage(sparkS, x1 - r, y1 - r);
    }
    ctx.globalAlpha = 1;
  }
}
