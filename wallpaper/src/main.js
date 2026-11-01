// Retro Circuit wallpaper: cycles the retro pictures and runs the Circuit visualizer on their contours.
// Hosts: Lively Wallpaper and Wallpaper Engine (audio spectrum + properties), the Linux host in
// wallpaper/linux/ (pushes finished levels via wpSetLevels), or a plain browser (?demo=1 fakes music).
"use strict";

(() => {
  const DATA = window.WALLPAPER_DATA || {images: []};
  const params = new URLSearchParams(location.search);
  const DEMO = params.has("demo");
  const QUIET_FPS = 12;          // music off: the runners drift slowly, so a low rate is enough

  const props = {
    interval: +(params.get("interval") || 5),   // minutes per picture
    fps: +(params.get("fps") || 30),             // cap while music plays
    dim: +(params.get("dim") || 0),              // 0..60 % darkening of the picture
    brightness: +(params.get("brightness") || 100), // runners, 20..200 %
    idle_runners: params.get("idle") !== "0",    // keep drifting without music
    shuffle: params.get("shuffle") !== "0",
  };

  const rng = makeRng(params.has("seed") ? +params.get("seed") : (Date.now() & 0x7fffffff));
  const canvas = document.getElementById("viz");
  const bgs = [document.getElementById("bg0"), document.getElementById("bg1")];
  const dimEl = document.getElementById("dim");
  const renderer = new Renderer(canvas);
  const viz = new CircuitViz(rng);
  const analyzer = new LV.Analyzer();

  let order = [], pos = -1, shown = 0, current = null;
  let sw = 0, sh = 0;
  let hostLevels = null, hostSeen = 0;   // levels pushed by a host (Linux) or analysed from a spectrum
  let lv = {bass: 0, mid: 0, treble: 0, beat: 0, bass_hit: 0, mid_hit: 0, treble_hit: 0};
  let vis = props.idle_runners ? 1 : 0;  // runner visibility, eased
  let nextSwitch = 0, lastT = null, lastDraw = 0;
  let paused = false;                    // the Linux host pauses while a window covers the desktop

  function reorder() {
    order = DATA.images.map((_, i) => i);
    if (props.shuffle) order = rng.sample(order, order.length);
  }

  function routesFor(img) {
    return coverPaths(img.paths, img.w, img.h, sw, sh);
  }

  function show(index, instant) {
    const img = DATA.images[index];
    if (!img) return;
    current = img;
    const el = bgs[shown ^ 1];
    const swap = () => {
      const prev = bgs[shown];
      prev.classList.remove("on");
      el.style.display = "";
      el.classList.add("on");
      shown ^= 1;
      // once faded out, stop compositing the old picture (a full-screen layer every frame)
      clearTimeout(prev.hideTimer);
      prev.hideTimer = setTimeout(() => { if (!prev.classList.contains("on")) prev.style.display = "none"; },
                                  instant ? 0 : 3000);
      viz.set(routesFor(img), sw, sh, img.palette, instant);
    };
    el.src = img.file;
    (el.decode ? el.decode() : Promise.resolve()).then(swap, swap);
  }

  function advance(instant) {
    if (!DATA.images.length) return;
    pos++;
    if (pos >= order.length) {
      reorder();
      pos = 0;
    }
    show(order[pos], instant);
    nextSwitch = performance.now() + Math.max(0.25, props.interval) * 60000;
  }

  function resize() {
    sw = window.innerWidth;
    sh = window.innerHeight;
    renderer.resize(sw, sh, window.devicePixelRatio || 1);
    if (current) viz.set(routesFor(current), sw, sh, current.palette, true);
  }

  function applyProps() {
    dimEl.style.opacity = Math.max(0, Math.min(0.6, props.dim / 100));
    nextSwitch = Math.min(nextSwitch || Infinity, performance.now() + Math.max(0.25, props.interval) * 60000);
  }

  function setProp(name, value) {
    if (!(name in props)) return;
    props[name] = typeof props[name] === "boolean" ? !!value : +value;
    if (name === "shuffle") reorder();
    applyProps();
  }

  // ---------------------------------------------------------------- hosts

  // Lively Wallpaper
  window.livelyAudioListener = (arr) => { hostLevels = analyzer.feed(arr, performance.now()); hostSeen = performance.now(); };
  window.livelyPropertyListener = (name, val) => setProp(name, val);

  // Wallpaper Engine
  if (window.wallpaperRegisterAudioListener) {
    window.wallpaperRegisterAudioListener((arr) => {
      hostLevels = analyzer.feed(arr, performance.now(), true);
      hostSeen = performance.now();
    });
  }
  window.wallpaperPropertyListener = {
    applyUserProperties(p) {
      for (const k of Object.keys(p)) if (p[k] && "value" in p[k]) setProp(k, p[k].value);
    },
    applyGeneralProperties(p) {
      if (p.fps) props.fps = +p.fps;
    },
  };

  // Linux host (wallpaper/linux/retro-wallpaper.py)
  window.wpSetLevels = (l) => { hostLevels = l; hostSeen = performance.now(); };
  window.wpSetProps = (p) => { for (const k of Object.keys(p)) setProp(k, p[k]); };
  window.wpSetPaused = (b) => {
    const was = paused;
    paused = !!b;
    if (was && !paused) { lastT = null; requestAnimationFrame(tick); }
  };

  // ---------------------------------------------------------------- loop

  function levelsNow(now) {
    if (DEMO) return LV.demo(now / 1000);
    if (hostLevels && now - hostSeen < 1000) return hostLevels;
    return LV.zero(hostLevels ? hostLevels.onset : 0);
  }

  function tick(now) {
    if (paused) return;
    requestAnimationFrame(tick);
    const target = levelsNow(now);
    const playing = !!target.active;
    const want = playing || props.idle_runners ? 1 : 0;
    // all faded out and nothing to show: skip drawing entirely
    if (want === 0 && vis === 0) {
      lastT = now;
      if (now >= nextSwitch) advance(false);
      return;
    }
    const fps = playing ? Math.max(5, Math.min(60, props.fps)) : QUIET_FPS;
    if (now - lastDraw < 1000 / fps - 2) return;
    lastDraw = now;
    const dt = lastT === null ? 1 / fps : Math.min(0.25, (now - lastT) / 1000);
    lastT = now;
    vis += (want - vis) * Math.min(1, dt / 0.8);
    if (Math.abs(want - vis) < 0.01) vis = want;
    if (now >= nextSwitch) advance(false);
    if (!viz.cur) return;
    lv = LV.ease(lv, target, dt);
    viz.step(dt, lv, target.onset);
    renderer.draw(viz.frame(), lv, now / 1000, viz.palette, vis * props.brightness / 100);
    if (vis === 0) renderer.ctx.clearRect(0, 0, canvas.width, canvas.height);
  }

  window.addEventListener("resize", resize);
  resize();
  applyProps();
  reorder();
  advance(true);
  requestAnimationFrame(tick);
})();
