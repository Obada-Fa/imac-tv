// Music levels for the wallpaper: a port of tvapp/audiolevel.py's Analyzer that starts from a host's
// frequency spectrum (Lively / Wallpaper Engine) instead of raw samples. Same outputs: bass/mid/treble
// 0..1, per-band hits, a decaying beat envelope and an onset counter.
"use strict";

const LV = (() => {
  const FLOOR = [0.04, 0.015, 0.006];   // per-band auto-gain never amplifies below these
  const CROSS = 0.08;
  const SILENCE = 0.004;                // mean spectrum value below which a frame counts as silence
  const PEAK_HALF = 4.0, ATTACK = 0.010, RELEASE = 0.07, HIT_TAU = 0.085, BEAT_DECAY = 0.25;
  const REFRACTORY = [0.14, 0.10, 0.07];
  const FLUX_K = 1.6, FLUX_MIN = [0.20, 0.16, 0.16], LEVEL_MIN = [0.30, 0.25, 0.25];
  // bands as bins of a 64-bin spectrum (scaled to the real length)
  const BANDS = [[0, 4], [4, 22], [22, 64]];

  function zero(onset) {
    return {bass: 0, mid: 0, treble: 0, beat: 0, active: false, onset,
            bass_hit: 0, mid_hit: 0, treble_hit: 0};
  }

  class Analyzer {
    constructor() {
      this.peak = FLOOR.slice();
      this.sm = [0, 0, 0];
      this.hit = [0, 0, 0];
      this.prev = [[0, 0], [0, 0], [0, 0]];
      this.flux = [0.05, 0.05, 0.05];
      this.since = [99, 99, 99];
      this.beat = 0;
      this.loud = this.quiet = 0;
      this.active = false;
      this.onset = 0;
      this.last = null;
      this.out = zero(0);
    }

    // spectrum: non-negative values, low to high frequency. stereo: Wallpaper Engine's 128 = 64 left + 64 right
    feed(spectrum, now, stereo) {
      const dt = this.last === null ? 1 / 30 : Math.min(0.2, Math.max(0.005, (now - this.last) / 1000));
      this.last = now;
      let s = spectrum;
      if (stereo && s.length >= 128) {
        const half = s.length >> 1;
        s = Array.from({length: half}, (_, k) => (s[k] + s[k + half]) / 2);
      }
      const scale = s.length / 64;
      const raw = BANDS.map(([a, b]) => {
        const lo = Math.floor(a * scale), hi = Math.max(lo + 1, Math.floor(b * scale));
        let e = 0;
        for (let k = lo; k < hi; k++) e += s[k] * s[k];
        return Math.sqrt(e / (hi - lo));
      });
      let total = 0;
      for (const v of s) total += v;
      total /= s.length;
      const hold = Math.max(1, Math.round(0.5 / dt));
      if (total > SILENCE) {
        this.loud++; this.quiet = 0;
        if (this.loud >= hold) this.active = true;
      } else {
        this.quiet++; this.loud = 0;
        if (this.quiet >= hold) this.active = false;
      }
      const pdec = Math.pow(0.5, dt / PEAK_HALF);
      const att = 1 - Math.exp(-dt / ATTACK), rel = 1 - Math.exp(-dt / RELEASE);
      const hdec = Math.exp(-dt / HIT_TAU), fa = 1 - Math.exp(-dt);
      const pk = this.peak = this.peak.map((p, i) => Math.max(FLOOR[i], p * pdec, raw[i]));
      const top = Math.max(...pk);
      const hit = [false, false, false];
      for (let i = 0; i < 3; i++) {
        const r = raw[i];
        const lv = r / Math.sqrt(pk[i] * top);
        const sm = this.sm[i];
        this.sm[i] = sm + (lv - sm) * (lv > sm ? att : rel);
        const v = r / Math.max(pk[i], CROSS * top);
        const pv = this.prev[i];
        const rise = v - Math.min(pv[0], pv[1]);
        pv[0] = pv[1]; pv[1] = v;
        this.since[i] += dt;
        if (this.active && rise > FLUX_MIN[i] && rise > FLUX_K * this.flux[i] && v > LEVEL_MIN[i]
            && this.since[i] >= REFRACTORY[i] - 0.5 * dt) {
          hit[i] = true;
          this.since[i] = 0;
          this.hit[i] = 1;
        } else {
          const h = this.hit[i] * hdec;
          this.hit[i] = h > 0.02 ? h : 0;
        }
        this.flux[i] += (Math.max(0, rise) - this.flux[i]) * fa;
      }
      if (hit[0] || (hit[1] && this.since[0] > 1.0)) {
        this.onset++;
        this.beat = 1;
      } else {
        this.beat = Math.max(0, this.beat - dt / BEAT_DECAY);
      }
      this.out = this.active
        ? {bass: this.sm[0], mid: this.sm[1], treble: this.sm[2], beat: this.beat, active: true,
           onset: this.onset, bass_hit: this.hit[0], mid_hit: this.hit[1], treble_hit: this.hit[2]}
        : zero(this.onset);
      return this.out;
    }
  }

  // ?demo=1: a synthetic 120 bpm track, for previews in a normal browser
  function demo(t) {
    const beatT = t % 0.5, bar = Math.floor(t / 0.5);
    const b = Math.max(0, 1 - beatT / 0.25);
    return {bass: 0.35 + 0.5 * Math.exp(-beatT * 8), mid: 0.45 + 0.15 * Math.sin(t * 1.3),
            treble: 0.3 + 0.2 * Math.sin(t * 3.1), beat: b, active: true, onset: bar,
            bass_hit: Math.exp(-beatT / 0.085), mid_hit: (bar % 2 ? Math.exp(-beatT / 0.085) : 0),
            treble_hit: (bar % 4 === 3 ? Math.exp(-beatT / 0.085) : 0)};
  }

  // ease_levels from widgets/shapes.py
  function ease(cur, target, dt, attack = 30, release = 9) {
    const out = {};
    for (const k of ["bass", "mid", "treble", "beat"]) {
      const c = cur[k] || 0, t = +target[k] || 0;
      out[k] = c + (t - c) * (1 - Math.exp(-(t > c ? attack : release) * Math.max(0, dt)));
    }
    for (const k of ["bass_hit", "mid_hit", "treble_hit"]) out[k] = Math.max(0, Math.min(1, +target[k] || 0));
    return out;
  }

  return {Analyzer, zero, demo, ease};
})();
