(() => {
  'use strict';
  const $ = id => document.getElementById(id);
  const remote = $('remote'), pair = $('pair'), sheet = $('sheet'), browser = $('browser'), tabbar = $('tabbar');
  let state = null, online = false, mode = 'dpad', sheetName = '', draggingSeek = false;
  let draggingBrightness = false, brightnessReason = '', updatePending = false;
  const phoneMedia = new TVMediaControls({button: $('phone-media'), send, message,
    readState: async () => {
      const s = await api('/api/state');
      render(s);
      return s;
    }});

  async function api(path, data) {
    const options = {credentials: 'same-origin'};
    if (data !== undefined) {
      options.method = 'POST';
      options.headers = {'Content-Type': 'application/json'};
      options.body = JSON.stringify(data);
    }
    const response = await fetch(path, options);
    const result = await response.json();
    if (!response.ok) throw new Error(result.error || 'The TV did not respond');
    return result;
  }

  let toastTimer = null, lastError = '';
  function message(text) {
    $('message').hidden = !text;
    $('message').textContent = text || '';
    clearTimeout(toastTimer);
    if (text) toastTimer = setTimeout(() => { if ($('message').textContent === text) message(''); }, 6000);
  }
  // ---- Haptics: the only place this page vibrates ----
  // Feedback belongs to the finger, never to the network: nothing here is called after a request settles.
  //   press  8 ms, once per press, at touch-down (pointerdown). The same tap's click is then swallowed.
  //          In lists that scroll (posters, settings rows, sheets) a touch-down may be the start of a scroll,
  //          so there the buzz waits for the click (the tap's release) instead. Keyboard presses buzz on click.
  //   tick   4 ms per hold-to-repeat step (directions, volume), never more than one per 170 ms.
  //   long   [12, 60, 22] once, when a long-press action fires (touchpad hold = right click).
  // Off in Settings › System; a no-op where navigator.vibrate is missing (iOS Safari, desktop).
  const HAPTIC = {press: 8, tick: 4, long: [12, 60, 22]};
  let haptics = true, hapticAt = -1e9, felt = null;
  try { haptics = localStorage.getItem('remote-haptics') !== 'off'; } catch (e) {}
  const canVibrate = typeof navigator.vibrate === 'function';
  function haptic(kind) {
    if (!haptics || !canVibrate) return false;
    const now = performance.now();
    if (kind === 'tick' && now - hapticAt < 170) return false;
    hapticAt = now;
    try { navigator.vibrate(HAPTIC[kind]); } catch (e) { return false; }
    return true;
  }
  const PRESSABLE = 'button, [role=switch], label.row, .ptile';
  const SCROLLS = '.pane, #sheet-content, .hrow, #settings-tabs';
  document.addEventListener('pointerdown', e => {
    felt = null;
    const el = e.target.closest(PRESSABLE);
    if (!el || el.disabled || el.closest(SCROLLS) || el.closest('#pad')) return;
    felt = el; haptic('press');
  }, true);
  document.addEventListener('click', e => {
    const el = e.target.closest(PRESSABLE);
    if (!el || el.disabled) return;
    if (felt === el) { felt = null; return; }      // already felt at touch-down
    felt = null;
    if (e.detail === 0 || el.closest(SCROLLS)) haptic('press');
  }, true);

  // ---- Shapes: Home's node shape (hexagon, or octagon when the TV says so) as SVG ----
  const SHAPES = {hexagon: [6, 0], octagon: [8, Math.PI / 8]};
  let nodeShape = 'hexagon';
  try { if (localStorage.getItem('node-shape') === 'octagon') nodeShape = 'octagon'; } catch (e) {}
  function polyPts(r, cx = 0, cy = 0) {
    const [n, rot] = SHAPES[nodeShape];
    return Array.from({length: n}, (_, i) => [cx + r * Math.cos(rot + 2 * Math.PI * i / n), cy + r * Math.sin(rot + 2 * Math.PI * i / n)]);
  }
  const ptsAttr = pts => pts.map(([x, y]) => `${+x.toFixed(2)},${+y.toFixed(2)}`).join(' ');
  const shapeBox = () => nodeShape === 'octagon' ? [Math.cos(Math.PI / 8), Math.cos(Math.PI / 8)] : [1, Math.sqrt(3) / 2];
  // One node image: optional tinted radial body (Home's RoundNode fill), hairline, and an inner mark.
  function nodeUri({base = 0, tint = '158,204,255', a0 = 0, a1 = 0, line = 0, solid = false, core = '', inset = 0.955}) {
    const [ex, ey] = shapeBox(), poly = ptsAttr(polyPts(inset)), [r, g, b] = tint.split(',');
    const c = `rgb(${r},${g},${b})`, dark = `rgb(${Math.round(r * .6)},${Math.round(g * .6)},${Math.round(b * .7)})`;
    let s = `<svg xmlns="http://www.w3.org/2000/svg" viewBox="${-ex} ${-ey} ${2 * ex} ${2 * ey}"><defs>` +
      `<radialGradient id="r" cx="30%" cy="25%" r="85%"><stop offset="0" stop-color="${c}" stop-opacity="${a0}"/><stop offset="1" stop-color="${c}" stop-opacity="${a1}"/></radialGradient>` +
      `<linearGradient id="l" x1="0" y1="0" x2="1" y2="1"><stop offset="0" stop-color="${c}" stop-opacity=".95"/><stop offset="1" stop-color="${dark}" stop-opacity=".9"/></linearGradient></defs>`;
    if (base) s += `<polygon points="${poly}" fill="#fff" fill-opacity="${base}"/>`;
    if (a0) s += `<polygon points="${poly}" fill="url(#r)"/>`;
    if (solid) s += `<polygon points="${poly}" fill="url(#l)"/>`;
    if (line) s += `<polygon points="${poly}" fill="none" stroke="#fff" stroke-opacity="${line}" stroke-width="1" stroke-linejoin="round" vector-effect="non-scaling-stroke"/>`;
    s += core + '</svg>';
    return `url("data:image/svg+xml,${encodeURIComponent(s)}")`;
  }
  const TINTS = {appearance: '158,107,214', ambient: '92,133,219', display: '71,168,158', playback: '219,143,82',
    connections: '77,158,214', system: '128,143,168'};
  function applyShape(shape) {
    nodeShape = shape === 'octagon' ? 'octagon' : 'hexagon';
    try { localStorage.setItem('node-shape', nodeShape); } catch (e) {}
    const [ex, ey] = shapeBox(), root = document.documentElement.style;
    const coreDot = `<polygon points="${ptsAttr(polyPts(0.34))}" fill="#fff" fill-opacity=".92"/>`;
    const check = '<path d="M-.4 .02-.12 .3.42-.28" fill="none" stroke="#fff" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round" vector-effect="non-scaling-stroke"/>';
    const plus = '<path d="M0-.36V.36M-.36 0H.36" fill="none" stroke="#fff" stroke-opacity=".85" stroke-width="1.5" stroke-linecap="round" vector-effect="non-scaling-stroke"/>';
    root.setProperty('--hx-ar', (ex / ey).toFixed(4));
    root.setProperty('--hx', nodeUri({base: .07, a0: .2, a1: .035, line: .26}));
    root.setProperty('--hx-press', nodeUri({base: .13, tint: '185,219,255', a0: .55, a1: .2, line: .9}));
    root.setProperty('--hx-lit', nodeUri({base: .08, a0: .46, a1: .12, line: .5}));
    root.setProperty('--hx-on', nodeUri({solid: true, line: .75, core: coreDot}));
    root.setProperty('--hx-off', nodeUri({line: .4}));
    root.setProperty('--hx-mark', nodeUri({line: .6, inset: .9}));
    root.setProperty('--hx-check', nodeUri({solid: true, line: .8, core: check}));
    root.setProperty('--hx-plus', nodeUri({base: .07, a0: .2, a1: .035, line: .4, core: plus}));
    document.querySelectorAll('#settings-tabs [role=tab]').forEach(paintBadge);
    buildHive();
    if (setState) renderSettings();
  }
  function paintBadge(b) {
    const tint = TINTS[b.id.replace('category-', '')] || '158,204,255', badge = b.querySelector('.badge');
    badge.style.setProperty('--tint', tint);
    badge.style.setProperty('--badge', nodeUri({base: .08, tint, a0: .2, a1: .04, line: .26}));
    badge.style.setProperty('--badge-lit', nodeUri({base: .1, tint, a0: .7, a1: .22, line: .55}));
  }

  // ---- Backdrop: Home's navy, the TV's photo blurred far back, a faint constellation; drawn once per size ----
  let skyPhoto = null, skySize = '';
  function drawSky(force) {
    const c = $('sky'), w = innerWidth, h = Math.max(innerHeight, c.clientHeight || 0);
    const size = w + 'x' + Math.round(h / 120);
    if (!force && size === skySize) return;
    skySize = size;
    const dpr = Math.min(2, devicePixelRatio || 1), ctx = c.getContext('2d');
    c.width = Math.round(w * dpr); c.height = Math.round(h * dpr);
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    const navy = ctx.createLinearGradient(0, 0, 0, h);
    navy.addColorStop(0, 'rgb(13,18,31)'); navy.addColorStop(0.55, 'rgb(18,23,38)'); navy.addColorStop(1, 'rgb(5,8,15)');
    ctx.fillStyle = navy; ctx.fillRect(0, 0, w, h);
    if (skyPhoto) {
      const iw = skyPhoto.naturalWidth, ih = skyPhoto.naturalHeight, k = Math.max(w / iw, h / ih) * 1.15;
      const dw = iw * k, dh = ih * k, dx = (w - dw) / 2, dy = (h - dh) / 2;
      ctx.globalAlpha = 0.8;
      if ('filter' in ctx) { ctx.filter = 'blur(26px) saturate(1.1)'; ctx.drawImage(skyPhoto, dx, dy, dw, dh); ctx.filter = 'none'; }
      else {   // no canvas filters: a tiny copy scaled back up is a blur too
        const t = document.createElement('canvas'); t.width = 18; t.height = Math.max(8, Math.round(18 * ih / iw));
        t.getContext('2d').drawImage(skyPhoto, 0, 0, t.width, t.height);
        ctx.imageSmoothingQuality = 'high'; ctx.drawImage(t, dx, dy, dw, dh);
      }
      ctx.globalAlpha = 1;
      const dim = ctx.createLinearGradient(0, 0, 0, h);
      dim.addColorStop(0, 'rgba(8,12,22,.42)'); dim.addColorStop(0.6, 'rgba(8,12,22,.58)'); dim.addColorStop(1, 'rgba(4,6,12,.84)');
      ctx.fillStyle = dim; ctx.fillRect(0, 0, w, h);
    }
    // Same recipe as Home's dust: k nearest neighbours within a reach, alpha fading with distance.
    let seed = 7; const rnd = () => (seed = (seed * 16807) % 2147483647) / 2147483647;
    const n = Math.max(16, Math.min(44, Math.round(w * h / 12000))), pts = [];
    for (let i = 0; i < n; i++) pts.push([rnd() * w, rnd() * h]);
    const reach = Math.min(190, w * 0.5);
    ctx.lineWidth = 1; ctx.lineCap = 'round';
    const done = new Set();
    pts.forEach((p, i) => {
      pts.map((q, j) => [j, Math.hypot(q[0] - p[0], q[1] - p[1])]).filter(([j, d]) => j !== i && d < reach)
        .sort((a, b) => a[1] - b[1]).slice(0, 2).forEach(([j, d]) => {
          const key = Math.min(i, j) + ':' + Math.max(i, j);
          if (done.has(key)) return; done.add(key);
          ctx.strokeStyle = `rgba(255,255,255,${(0.17 * (1 - d / reach)).toFixed(3)})`;
          ctx.beginPath(); ctx.moveTo(p[0], p[1]); ctx.lineTo(pts[j][0], pts[j][1]); ctx.stroke();
        });
    });
    ctx.fillStyle = 'rgba(255,255,255,.34)';
    for (const [x, y] of pts) { const d = 1.8; ctx.beginPath(); ctx.moveTo(x, y - d); ctx.lineTo(x + d, y); ctx.lineTo(x, y + d); ctx.lineTo(x - d, y); ctx.fill(); }
  }
  function setSkyPhoto(src) {
    if (!src || (skyPhoto && skyPhoto.dataset.src === src)) return;
    const img = new Image();
    img.dataset.src = src;
    img.onload = () => { skyPhoto = img; drawSky(true); };
    img.src = src;
  }
  let skyTimer = 0;
  window.addEventListener('resize', () => { clearTimeout(skyTimer); skyTimer = setTimeout(() => { drawSky(); buildHiveLinks(); }, 150); });

  // ---- The honeycomb: OK node, a ring of four directions around it, four satellite nodes on its diagonal edges ----
  // A hexagon has six sides at 60°, a D-pad needs four at 90°, so the ring around OK is cut into four:
  // Up/Down are its flat top and bottom, Left/Right the chevrons that point their way. The satellites sit
  // edge to edge on the ring's four diagonal sides, as cells of one honeycomb. Units are CSS px at full size.
  const HIVE = {ro: 124, ri: 58, gap: 5, rs: 34, sgap: 6, pad: 4};
  const ICONS = {
    back: '<path d="M10 6 4 12l6 6M4.5 12H15a5 5 0 0 1 5 5v1"/>',
    home: '<path d="M4 11 12 4.5 20 11M6.5 9.5V19.5h4v-5h3v5h4V9.5"/>',
    type: '<rect x="3" y="6.5" width="18" height="11" rx="2"/><path d="M7 10.5h.01M10.3 10.5h.01M13.7 10.5h.01M17 10.5h.01M8 14h8"/>',
    search: '<circle cx="11" cy="11" r="6"/><path d="m15.5 15.5 4.5 4.5"/>',
    more: '<circle cx="6" cy="12" r=".9"/><circle cx="12" cy="12" r=".9"/><circle cx="18" cy="12" r=".9"/>',
  };
  let hiveGeo = null;
  function rayHit(o, u, poly) {
    let best = Infinity;
    for (let i = 0; i < poly.length; i++) {
      const [ax, ay] = poly[i], [bx, by] = poly[(i + 1) % poly.length], ex = bx - ax, ey = by - ay;
      const den = u[0] * ey - u[1] * ex;
      if (Math.abs(den) < 1e-9) continue;
      const t = ((ax - o[0]) * ey - (ay - o[1]) * ex) / den, s = ((ax - o[0]) * u[1] - (ay - o[1]) * u[0]) / den;
      if (t > 0 && s >= -1e-6 && s <= 1 + 1e-6) best = Math.min(best, t);
    }
    return [o[0] + u[0] * best, o[1] + u[1] * best];
  }
  const deg = a => a * Math.PI / 180;
  function ringSegment(a0, a1, inner, outer, gap) {
    const side = (a, sign) => { const u = [Math.cos(deg(a)), Math.sin(deg(a))]; return [[-u[1] * gap / 2 * sign, u[0] * gap / 2 * sign], u]; };
    const [o0, u0] = side(a0, 1), [o1, u1] = side(a1, -1);
    const between = (poly, rev) => {
      const out = poly.map(p => [p, ((Math.atan2(p[1], p[0]) * 180 / Math.PI - a0) % 360 + 720) % 360])
        .filter(([, r]) => r > 0.5 && r < a1 - a0 - 0.5).sort((a, b) => a[1] - b[1]).map(([p]) => p);
      return rev ? out.reverse() : out;
    };
    return [rayHit(o0, u0, inner), rayHit(o0, u0, outer), ...between(outer), rayHit(o1, u1, outer), rayHit(o1, u1, inner), ...between(inner, true)];
  }
  function buildHive() {
    const hive = $('hive');
    if (!hive) return;
    const [n] = SHAPES[nodeShape], ca = Math.cos(Math.PI / n), {ro, ri, gap, rs, sgap, pad} = HIVE;
    const outer = polyPts(ro), inner = polyPts(ri + gap / ca), apO = ro * ca, apI = (ri + gap / ca) * ca;
    const hex = nodeShape === 'hexagon';
    const sectors = hex ? {right: [-60, 60], down: [60, 120], left: [120, 240], up: [240, 300]}
      : {right: [-45, 45], down: [45, 135], left: [135, 225], up: [225, 315]};
    const satAngles = hex ? [210, 330, 150, 30] : [225, 315, 135, 45];
    const cells = [];   // {el, poly, glyph:[x,y], icon|arrow|label}
    for (const [dir, [a0, a1]] of Object.entries(sectors)) {
      const mid = deg((a0 + a1) / 2), r = (apI + apO) / 2;
      cells.push({el: hive.querySelector(`#dpad [data-action=${dir}]`), poly: ringSegment(a0, a1, inner, outer, gap),
        glyph: [Math.cos(mid) * r, Math.sin(mid) * r], arrow: mid});
    }
    cells.push({el: hive.querySelector('#dpad [data-action=ok]'), poly: polyPts(ri), glyph: [0, 0], label: 'OK'});
    const sats = [hive.querySelector('.sat[data-action=back]'), hive.querySelector('.sat[data-action=home]'), $('search-open'), $('more-open')];
    const satIcons = ['back', 'home', 'type', 'more'], d = apO + sgap + rs * ca;
    const satCentres = satAngles.map(a => [Math.cos(deg(a)) * d, Math.sin(deg(a)) * d]);
    sats.forEach((el, i) => cells.push({el, poly: polyPts(rs, ...satCentres[i]), glyph: satCentres[i], icon: satIcons[i]}));
    const all = cells.flatMap(c => c.poly), X0 = Math.min(...all.map(p => p[0])) - pad, Y0 = Math.min(...all.map(p => p[1])) - pad;
    const W = Math.max(...all.map(p => p[0])) + pad - X0, H = Math.max(...all.map(p => p[1])) + pad - Y0;
    hive.style.setProperty('--hive-w', W.toFixed(1) + 'px');
    hive.style.setProperty('--hive-ar', (W / H).toFixed(4));
    const place = (el, x0, y0, x1, y1) => {
      el.style.left = ((x0 - X0) / W * 100).toFixed(3) + '%'; el.style.top = ((y0 - Y0) / H * 100).toFixed(3) + '%';
      el.style.width = ((x1 - x0) / W * 100).toFixed(3) + '%'; el.style.height = ((y1 - y0) / H * 100).toFixed(3) + '%';
    };
    for (const c of cells) {
      const xs = c.poly.map(p => p[0]), ys = c.poly.map(p => p[1]);
      const x0 = Math.min(...xs), y0 = Math.min(...ys), x1 = Math.max(...xs), y1 = Math.max(...ys);
      place(c.el, x0, y0, x1, y1);
      c.el.style.setProperty('--ox', ((c.glyph[0] - x0) / (x1 - x0) * 100).toFixed(1) + '%');
      c.el.style.setProperty('--oy', ((c.glyph[1] - y0) / (y1 - y0) * 100).toFixed(1) + '%');
      const [gx, gy] = c.glyph;
      let mark = '';
      if (c.arrow !== undefined) {
        const s = 7.5, ux = Math.cos(c.arrow), uy = Math.sin(c.arrow);
        const tip = [gx + ux * s * 0.55, gy + uy * s * 0.55], back = [gx - ux * s * 0.45, gy - uy * s * 0.45];
        mark = `<g class="g"><path d="M${(back[0] - uy * s).toFixed(2)} ${(back[1] + ux * s).toFixed(2)}L${tip[0].toFixed(2)} ${tip[1].toFixed(2)}L${(back[0] + uy * s).toFixed(2)} ${(back[1] - ux * s).toFixed(2)}"/></g>`;
      } else if (c.label) {
        mark = `<text class="label" x="${gx}" y="${gy}">${c.label}</text>`;
      } else {
        const k = rs * 0.66 / 24;
        mark = `<g class="g" transform="translate(${(gx - 12 * k).toFixed(2)} ${(gy - 12 * k).toFixed(2)}) scale(${k.toFixed(4)})">${ICONS[c.icon]}</g>`;
      }
      const keep = c.el.querySelector('.sr');
      c.el.innerHTML = `<svg viewBox="${x0} ${y0} ${x1 - x0} ${y1 - y0}" aria-hidden="true" focusable="false"><polygon class="base" points="${ptsAttr(c.poly)}"/><polygon class="face" points="${ptsAttr(c.poly)}"/>${mark}</svg>`;
      if (keep) c.el.append(keep);
    }
    // Pointer mode: the touchpad takes the ring's place, in the ring's shape.
    const padEl = $('pad'), ox = outer.map(p => p[0]), oy = outer.map(p => p[1]);
    const px0 = Math.min(...ox), py0 = Math.min(...oy), px1 = Math.max(...ox), py1 = Math.max(...oy);
    place(padEl, px0, py0, px1, py1);
    padEl.style.clipPath = `polygon(${outer.map(([x, y]) => `${((x - px0) / (px1 - px0) * 100).toFixed(2)}% ${((y - py0) / (py1 - py0) * 100).toFixed(2)}%`).join(',')})`;
    const face = padEl.querySelector('.pad-face');
    face.setAttribute('viewBox', `${px0} ${py0} ${px1 - px0} ${py1 - py0}`);
    face.innerHTML = `<polygon points="${ptsAttr(polyPts(ro - 0.5))}"/>`;
    hiveGeo = {X0, Y0, W, H, ro, apO, satCentres, rs};
    buildHiveLinks();
  }
  // Thin constellation lines from the outer satellites to a few dust points, as Home links its nodes.
  function buildHiveLinks() {
    const svg = $('hive-links');
    if (!svg || !hiveGeo) return;
    const {X0, Y0, W, H, ro, apO, satCentres, rs} = hiveGeo;
    svg.setAttribute('viewBox', `${X0} ${Y0} ${W} ${H}`);
    const far = ([x, y]) => polyPts(rs, x, y).reduce((a, b) => (Math.abs(b[0]) > Math.abs(a[0]) ? b : a));
    const top = ([x, y]) => polyPts(rs, x, y).reduce((a, b) => (b[1] < a[1] || (b[1] === a[1] && Math.abs(b[0]) < Math.abs(a[0])) ? b : a));
    const bottom = ([x, y]) => polyPts(rs, x, y).reduce((a, b) => (b[1] > a[1] || (b[1] === a[1] && Math.abs(b[0]) < Math.abs(a[0])) ? b : a));
    const [ul, ur, ll, lr] = satCentres, side = ro + 40;
    const dust = {nw: [-side + 6, -apO - 14], w: [-side - 4, 2], sw: [-side + 14, apO + 12], ne: [side - 6, -apO - 16], e: [side + 2, -4], se: [side - 12, apO + 14],
      n: [-8, -apO - 22], s: [10, apO + 22]};
    const lines = [[far(ul), dust.nw, .26], [far(ul), dust.w, .2], [far(ll), dust.w, .22], [far(ll), dust.sw, .18],
      [far(ur), dust.ne, .24], [far(ur), dust.e, .2], [far(lr), dust.e, .22], [far(lr), dust.se, .18],
      [top(ul), dust.n, .16], [top(ur), dust.n, .18], [bottom(ll), dust.s, .14], [bottom(lr), dust.s, .16], [dust.nw, dust.n, .1]];
    svg.innerHTML = lines.map(([a, b, o]) => `<line x1="${a[0].toFixed(1)}" y1="${a[1].toFixed(1)}" x2="${b[0].toFixed(1)}" y2="${b[1].toFixed(1)}" stroke-opacity="${o}"/>`).join('') +
      Object.values(dust).map(([x, y]) => `<path class="dust" d="M${x} ${y - 2.4}l2.4 2.4-2.4 2.4-2.4-2.4z"/>`).join('');
  }

  // Pause bars while playing, a play triangle while paused; drawn, so no emoji font gets a say.
  function playGlyph(b, playing) {
    const v = playing ? '1' : '0';
    if (b.dataset.playing === v) return;
    b.dataset.playing = v;
    b.innerHTML = playing ? '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M9 6.5v11M15 6.5v11"/></svg>'
      : '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M8.5 6.2v11.6L18 12z"/></svg>';
  }
  // Home's clock and date line; one timer, woken once a minute.
  let remoteLabel = 'Connecting', clockTimer = 0;
  function tickClock() {
    clearTimeout(clockTimer);
    const now = new Date();
    $('clock').textContent = now.toLocaleTimeString([], {hour: '2-digit', minute: '2-digit', hourCycle: 'h23'});
    const day = now.toLocaleDateString([], {weekday: 'short', day: 'numeric', month: 'short'});
    $('remote-meta').textContent = [day, remoteLabel].filter(Boolean).join(' · ');
    clockTimer = setTimeout(tickClock, 60000 - now.getSeconds() * 1000 - now.getMilliseconds() + 50);
  }
  const fillRange = input => {
    const min = Number(input.min) || 0, max = Number(input.max) || 100, v = Number(input.value) || 0;
    input.style.setProperty('--fill', (max > min ? Math.max(0, Math.min(100, (v - min) / (max - min) * 100)) : 0).toFixed(1) + '%');
  };

  function connected(value, label) {
    const was = online;
    online = value;
    if (!value) phoneMedia.disconnect();
    if (was !== value && setState) renderConnections();
    $('connection').title = label; $('connection').setAttribute('aria-label', label);
    $('connection').classList.toggle('off', !value);
    document.body.classList.toggle('offline', !value);
    remoteLabel = label.replace(/^●\s*/, '');
    tickClock();
    document.querySelectorAll('#controls button, footer button, .apps button, #tabbar button, .pane button, #sheet-content .tsheet button').forEach(b => b.disabled = !value);
    renderUpdateStatus();
  }

  function showPair() {
    $('connection').hidden = true; pair.hidden = false; remote.hidden = true; browser.hidden = true; sheet.hidden = true; sheetName = '';
    connected(false, 'Pair this phone');
    updateTabbar();
  }

  function fmt(seconds) {
    seconds = Math.max(0, Math.floor(Number(seconds) || 0));
    const m = Math.floor(seconds / 60), s = String(seconds % 60).padStart(2, '0');
    return `${m}:${s}`;
  }

  function render(s) {
    renderTorboxRow('set-torbox-row', s.torbox);
    state = s;
    if (!setState && !settingsAsked) { settingsAsked = true; loadSettings(); }   // node shape + Home's photo
    pair.hidden = true; $('connection').hidden = false;
    connected(Boolean(s.gui), s.gui ? '● Connected' : '● TV starting');
    if (s.gui) phoneMedia.update(s);
    applyTab();
    const pb = s.playback || {}, name = (s.media && s.media.name) || pb.title;
    const notice = s.thermal_warning ? '⚠ TV is running hot' : s.phase === 'opening' ? 'Opening…' : !s.gui ? 'Waiting for TV screen' : '';
    $('notice').textContent = notice; $('notice').hidden = !notice;
    $('notice').classList.toggle('warn', Boolean(s.thermal_warning));
    renderView(s, pb, name);
    document.querySelectorAll('.apps button').forEach(b => b.classList.toggle('selected', b.dataset.app === s.app));
    const vol = s.volume == null ? 'Volume' : `${s.muted ? 'Muted · ' : ''}${s.volume}%`;
    $('volume-label').textContent = vol; $('np-volume-label').textContent = vol;
    const light = s.brightness;
    const off = Boolean(s.screen_off);
    const reason = light === undefined ? 'Restart the TV controller to update' :
      light.level == null ? 'This display has no adjustable backlight' :
      light.available === false ? 'Run setup-privileges.sh on the TV to allow brightness control' : '';
    const power = $('screen-off');
    power.classList.toggle('off', off);
    power.setAttribute('aria-pressed', String(off));
    power.setAttribute('aria-label', off ? 'Turn the TV screen back on' : 'Turn the TV screen off');
    $('brightness-level').classList.toggle('off', off);
    // The slider stays live while the screen is blank: DPMS hides the output and
    // leaves the backlight alone, so the level set now is the one seen on wake.
    $('brightness').disabled = Boolean(reason);
    $('brightness').title = off ? 'The screen is off; audio keeps playing' : reason || 'TV screen brightness';
    if (off) {
      $('brightness-level').textContent = 'Off';
    } else if (!reason && !draggingBrightness) {
      $('brightness').value = light.level;
      $('brightness-level').textContent = `${light.level}%`;
      fillRange($('brightness'));
    }
    if (reason && reason !== brightnessReason) {
      brightnessReason = reason;
      message(reason);
    } else if (!reason) {
      brightnessReason = '';
    }
    $('fullscreen').classList.toggle('active', Boolean(s.fullscreen));
    $('fullscreen').setAttribute('aria-pressed', String(Boolean(s.fullscreen)));
    if (s.error) { if (s.error !== lastError) { lastError = s.error; message(s.error); } }
    else {
      lastError = '';
      if ($('message').textContent === 'Reconnecting to the TV…') message('');
    }
    const preferred = 'dpad';
    const remembered = sessionStorage.getItem('mode-' + s.app);
    if (mode !== (remembered || preferred) && !sheetName && !searching) setMode(remembered || preferred);
    if (sheetName === 'more') renderMoreDynamic();
    if (tab === 'settings' && setState) {
      for (const [key, , , , , network] of setState.fields || []) {
        const control = $('setting-' + key);
        if (control && network) control.disabled = !online || setState.values.style === 'bubbles';
      }
    }
  }

  const LANG = {ara: 'Arabic', ar: 'Arabic', dut: 'Dutch', nld: 'Dutch', nl: 'Dutch', eng: 'English', en: 'English',
    bul: 'Bulgarian', chi: 'Chinese', zho: 'Chinese', zh: 'Chinese', hrv: 'Croatian', cze: 'Czech', ces: 'Czech', dan: 'Danish',
    fin: 'Finnish', fre: 'French', fra: 'French', fr: 'French', ger: 'German', deu: 'German', de: 'German', gre: 'Greek', ell: 'Greek',
    heb: 'Hebrew', hun: 'Hungarian', ind: 'Indonesian', ita: 'Italian', it: 'Italian', jpn: 'Japanese', ja: 'Japanese', kor: 'Korean',
    nor: 'Norwegian', nob: 'Norwegian', per: 'Persian', fas: 'Persian', pol: 'Polish', pl: 'Polish', por: 'Portuguese', pt: 'Portuguese',
    pob: 'Portuguese (BR)', rum: 'Romanian', ron: 'Romanian', rus: 'Russian', ru: 'Russian', spa: 'Spanish', es: 'Spanish',
    swe: 'Swedish', sv: 'Swedish', tha: 'Thai', tur: 'Turkish', tr: 'Turkish', ukr: 'Ukrainian', vie: 'Vietnamese', hin: 'Hindi'};
  const PREFERRED = ['Arabic', 'Dutch', 'English'];
  // Language first ("English · Surround 5.1"): audio titles are often just the channel layout.
  const langName = t => {
    const code = String(t.lang || '').toLowerCase(), title = String(t.title || '').trim();
    const lang = code && code !== 'und' ? (LANG[code] || code.toUpperCase()) : '';
    if (lang && title.toLowerCase().startsWith(lang.toLowerCase())) return title;  // "Chinese (Traditional)"
    const detail = title && title.toLowerCase() !== code ? title : '';
    return [lang, detail].filter(Boolean).join(' · ') || `Track ${t.id}`;
  };
  const isPreferred = t => PREFERRED.includes(LANG[String(t.lang || '').toLowerCase()]);
  let sigs = {}, subDelay = 0, searching = false, sent = '', typeTimer = null;

  function chip(label, on, fn) {
    const b = document.createElement('button');
    b.type = 'button'; b.textContent = label; b.classList.toggle('on', on); b.onclick = () => { fn(); };
    return b;
  }
  function labelled(tracks) {
    const seen = {};
    return tracks.map(t => { const n = langName(t); seen[n] = (seen[n] || 0) + 1; return [t, seen[n] > 1 ? `${n} ${seen[n]}` : n]; });
  }

  // Mini player for YouTube running behind TV Home. Hidden while our own mpv card is showing.
  let ytPending = 0, ytState = null, ytAt = 0, ytDragging = false;
  const clock = sec => { sec = Math.max(0, Math.round(sec)); const h = Math.floor(sec / 3600), m = Math.floor(sec % 3600 / 60), r = sec % 60; return (h ? h + ':' + String(m).padStart(2, '0') : m) + ':' + String(r).padStart(2, '0'); };
  function ytPosition() {
    if (!ytState || ytState.position == null) return 0;
    const live = ytState.status === 'Playing' ? (Date.now() - ytAt) / 1000 : 0;
    return Math.min(ytState.length || Infinity, ytState.position + live);
  }
  function ytTick() {
    if (!ytState || ytDragging || $('yt').hidden) return;
    const pos = ytPosition(), len = ytState.length || 0;
    $('yt-range').value = pos; fillRange($('yt-range')); $('yt-pos').textContent = clock(pos); $('yt-dur').textContent = '-' + clock(len - pos);
  }
  setInterval(ytTick, 250);
  function renderYoutube(yt, view) {
    const bar = $('yt');
    bar.hidden = !yt || view === 'playing';
    ytState = bar.hidden ? null : yt; ytAt = Date.now();
    if (bar.hidden) return;
    const open = yt.status === 'Open';
    bar.classList.toggle('open', open);
    $('yt-title').textContent = open ? 'YouTube is open' : (yt.title || 'YouTube');
    $('yt-artist').textContent = open ? '' : (yt.artist || '');
    $('yt-artist').hidden = open || !yt.artist;
    const art = $('yt-art');
    if (yt.art && !open) { if (art.getAttribute('src') !== yt.art) art.src = yt.art; art.hidden = false; art.onerror = () => { art.hidden = true; }; }
    else { art.hidden = true; art.removeAttribute('src'); }
    document.querySelector('#yt .yt-ctl').hidden = open;
    $('yt-show').hidden = !open;
    const seekable = !open && yt.can_seek !== false && yt.length > 0 && yt.position != null;
    $('yt-seekrow').hidden = !seekable;
    $('yt-back').hidden = $('yt-fwd').hidden = !seekable;
    if (seekable) { $('yt-range').max = yt.length; ytTick(); }
    $('yt-prev').hidden = yt.can_prev === false;
    $('yt-next').hidden = yt.can_next === false;
    $('yt-play').hidden = yt.can_pause === false;
    if (Date.now() > ytPending) playGlyph($('yt-play'), yt.status === 'Playing');
    const mode = $('yt-mode'), known = !open && (yt.youtube_mode === 'music' || yt.youtube_mode === 'video');
    mode.hidden = !known;
    if (known) {
      mode.dataset.mode = yt.youtube_mode === 'music' ? 'video' : 'music';
      mode.textContent = yt.youtube_mode === 'music' ? 'Show video' : 'Show visualizer';
      const who = document.createElement('small');
      who.textContent = 'always, for this channel';
      mode.appendChild(who);
    }
  }
  async function ytSend(params, onFail) {
    try { const r = await send('youtube_media', params); if (r && r.result) renderYoutube(r.result.youtube_now, $('controls').dataset.view); }
    catch (e) { if (onFail) onFail(); }
  }
  document.querySelectorAll('#yt [data-yt]').forEach(b => b.onclick = async () => {
    
    const play = $('yt-play'), before = play.dataset.playing === '1';
    if (b === play) { playGlyph(play, !before); ytPending = Date.now() + 2500; }
    await ytSend({cmd: b.dataset.yt}, () => { if (b === play) playGlyph(play, before); });
    ytPending = 0;
  });
  [[$('yt-back'), -10], [$('yt-fwd'), 10]].forEach(([b, offset]) => b.onclick = () => {
    
    if (ytState && ytState.position != null) { ytState.position = Math.max(0, ytPosition() + offset); ytAt = Date.now(); ytTick(); }
    ytSend({cmd: 'seek', offset});
  });
  const yr = $('yt-range');
  yr.addEventListener('input', () => { ytDragging = true; fillRange(yr); $('yt-pos').textContent = clock(+yr.value); $('yt-dur').textContent = '-' + clock((ytState && ytState.length || 0) - yr.value); });
  yr.addEventListener('change', () => {
    const position = +yr.value;
    if (ytState) { ytState.position = position; ytAt = Date.now(); }
    ytSend({cmd: 'set_position', position}).then(() => { ytDragging = false; });
  });
  $('yt-mode').onclick = async () => {
    
    try { const r = await send('youtube_mode', {mode: $('yt-mode').dataset.mode}); if (r && r.result) renderYoutube(r.result.youtube_now, $('controls').dataset.view); }
    catch (e) {}
  };
  [$('yt-open'), $('yt-show')].forEach(b => b.onclick = () => { send('select', {app: 'youtube'}).catch(() => {}); });

  // Playback prompts: each button is one server action; the TV answers the same prompts with OK / Back.
  [['np-skip', 'skip_segment', {}], ['un-play', 'upnext', {choice: 'play'}], ['un-cancel', 'upnext', {choice: 'cancel'}],
   ['st-continue', 'still_watching', {choice: 'continue'}], ['st-stop', 'still_watching', {choice: 'stop'}]]
    .forEach(([id, action, params]) => $(id).onclick = () => { send(action, params).then(() => refresh()).catch(() => {}); });

  const VIZ_NAMES = {edgeflow: 'Edge flow', ridge: 'Ridge', constellation: 'Constellation', terrain: 'Terrain',
    prism: 'Prism', circuit: 'Circuit', aurora: 'Aurora'};
  function renderViz(viz, view) {
    const on = Boolean(viz && viz.active) && view !== 'playing';
    $('viz-next').hidden = !on;
    if (on) $('viz-mode').textContent = VIZ_NAMES[viz.mode] || viz.mode || '';
  }
  $('viz-next').onclick = () => { send('viz_next').then(() => setTimeout(refresh, 600)).catch(() => {}); };

  function renderView(s, pb, name) {
    const view = pb.active ? 'playing' : s.phase === 'buffering' ? 'buffering' : 'browse';
    const c = $('controls');
    if (c.dataset.view !== view) {
      c.dataset.view = view;
      if (view !== 'browse') { closeSearch(); showTab('remote'); }
      if (view !== 'playing') { subDelay = 0; sigs = {}; if (sheetName === 'subs' || sheetName === 'audio') closeSheet(); }
    }
    renderYoutube(s.youtube_now, view);
    renderViz(s.visualizer, view);
    $('apps').hidden = view === 'playing';
    $('browse').hidden = view !== 'browse';
    $('buffer').hidden = view !== 'buffering';
    $('nowplaying').hidden = view !== 'playing';
    $('mode').hidden = view !== 'browse';
    if (view === 'buffering') {
      const b = s.buffering || {};
      $('buf-name').textContent = name || 'Starting…';
      $('buf-source').textContent = [b.stage || 'Finding the best source…', b.source].filter(Boolean).join(' · ');
      $('buf-stats').textContent = b.source ? `${b.peers || 0} peers · ${((b.speed || 0) / 1048576).toFixed(1)} MB/s` : '';
    } else if (view === 'playing') {
      const m = s.media || {};
      const seg = s.segment, un = s.upnext, sw = s.still_watching;
      $('np-skip').hidden = !seg || Boolean(un || sw);
      if (seg) $('np-skip').textContent = seg.kind === 'recap' ? 'Skip recap' : 'Skip intro';
      $('np-upnext').hidden = !un;
      if (un) {
        $('un-sec').textContent = un.seconds_left;
        $('un-title').textContent = `S${String(un.season).padStart(2, '0')}E${String(un.episode).padStart(2, '0')} · ${un.name}`;
      }
      $('np-still').hidden = !sw;
      if (sw) $('st-sec').textContent = sw.seconds_left;
      $('np-title').textContent = name || 'Video';
      const ep = m.season != null && m.episode != null ? `S${String(m.season).padStart(2, '0')} · E${String(m.episode).padStart(2, '0')}` : '';
      const sub = [ep, m.episode_name || m.episode_title].filter(Boolean).join(' · ');
      $('np-sub').textContent = sub; $('np-sub').hidden = !sub;
      const poster = $('np-poster');
      if (m.poster) { if (poster.getAttribute('src') !== m.poster) poster.src = m.poster; poster.hidden = false; poster.onerror = () => { poster.hidden = true; }; }
      else poster.hidden = true;
      playGlyph($('np-play'), !pb.paused);
      if (!draggingSeek) {
        $('seek').max = Math.max(1, pb.duration || 1);
        $('seek').value = pb.position || 0;
        fillRange($('seek'));
        $('position').textContent = fmt(pb.position);
        $('duration').textContent = '-' + fmt((pb.duration || 0) - (pb.position || 0));
      }
      const nx = s.next || (s.media && s.media.next);
      $('np-next').hidden = !nx;
      if (nx) {
        $('np-next-label').textContent = `S${String(nx.season).padStart(2, '0')}E${String(nx.episode).padStart(2, '0')}`;
        $('np-next').title = nx.name || '';
      }
      const subs = pb.subtitles || [], audio = pb.audio || [];
      const subSel = labelled(subs).find(([t]) => t.selected);
      $('pick-sub-label').textContent = (subSel ? subSel[1] : 'Off') + ' ▾';
      const audSel = labelled(audio).find(([t]) => t.selected);
      $('pick-audio').hidden = audio.length < 2;
      $('pick-audio-label').textContent = (audSel ? audSel[1] : audio.length ? 'Auto' : '—') + ' ▾';
      $('np-delay') && ($('np-delay').textContent = delayText());
      if (sheetName === 'subs' || sheetName === 'audio') renderTrackSheet();
    }
    $('search-open').classList.toggle('on', searching);
    const yt = s.app === 'youtube', label = yt ? 'Search YouTube' : 'Type on TV';
    const sr = $('search-open').querySelector('.sr');
    if (sr) sr.textContent = label;
    $('search-open').setAttribute('aria-label', label);
    $('search-input').placeholder = yt ? 'Type to search YouTube on the TV' : 'Type to search on the TV';
  }

  function closeSearch() {
    searching = false; sent = ''; clearTimeout(typeTimer);
    $('search-box').hidden = true; $('search-input').value = '';
  }
  // Send only what changed: appended text is typed, removed text becomes backspaces.
  async function flushTyping() {
    clearTimeout(typeTimer);
    const value = $('search-input').value;
    let common = 0;
    while (common < sent.length && common < value.length && sent[common] === value[common]) common++;
    const del = sent.length - common, add = value.slice(common);
    sent = value;
    try {
      for (let i = 0; i < del; i++) await send('delete');
      if (add) await send('type', {text: add});
    } catch (e) { /* toast already shown */ }
  }
  async function openSearch() {
    
    searching = true;
    $('search-box').hidden = false; $('search-input').value = ''; sent = '';
    $('search-input').focus();  // inside the tap so the native keyboard opens
    try { await send('open_search'); } catch (e) { return; }
    $('search-input').focus();
  }
  $('search-open').onclick = openSearch;
  $('search-input').addEventListener('input', () => { clearTimeout(typeTimer); typeTimer = setTimeout(flushTyping, 400); });
  $('search-box').onsubmit = async e => {
    e.preventDefault(); await flushTyping(); fire('enter');
    // Drop the keyboard so the D-pad is reachable again.
    $('search-input').value = ''; sent = ''; $('search-input').blur();
  };
  $('search-del').onclick = async () => {
    const i = $('search-input');
    if (i.value) { i.value = i.value.slice(0, -1); await flushTyping(); } else fire('delete');
    i.focus();
  };
  document.querySelectorAll('[data-skip]').forEach(b => b.onclick = () => {
    const p = state && state.playback; if (!p || !p.active) return;
    fire('seek', {seconds: Math.max(0, Math.min(p.duration || 86400, (p.position || 0) + Number(b.dataset.skip)))});
  });
  const delayText = () => `Delay ${subDelay > 0 ? '+' : ''}${subDelay.toFixed(1)}s`;
  $('np-next').onclick = () => { fire('next_episode'); };
  $('pick-sub').onclick = () => openSheet('subs');
  $('pick-audio').onclick = () => openSheet('audio');

  // Bottom-sheet track picker: Off + preferred languages first, the rest under "More languages".
  function renderTrackSheet() {
    const kind = sheetName, pb = (state && state.playback) || {};
    const tracks = kind === 'subs' ? pb.subtitles || [] : pb.audio || [];
    const sig = kind + JSON.stringify(tracks.map(t => [t.id, t.selected]));
    const content = $('sheet-content');
    if (content.dataset.sig === sig) return;
    content.dataset.sig = sig;
    const pick = t => { if (kind === 'subs') fire('subtitle', {id: t ? t.id : 'no'}); else fire('audio', {id: t.id}); };
    const row = (label, on, fn) => { const b = chip((on ? '✓ ' : '') + label, on, fn); return b; };
    const named = labelled(tracks);
    const top = [], more = [];
    for (const [t, l] of named) (isPreferred(t) ? top : more).push([t, l]);
    const rank = ([t]) => PREFERRED.indexOf(LANG[String(t.lang || '').toLowerCase()]);
    top.sort((a, b) => rank(a) - rank(b));
    const list = document.createElement('div'); list.className = 'tracklist';
    if (kind === 'subs') list.append(row('Off', !tracks.some(t => t.selected), () => pick(null)));
    for (const [t, l] of top) list.append(row(l, t.selected, () => pick(t)));
    if (more.length) {
      const h = document.createElement('h3'); h.textContent = 'More languages'; list.append(h);
      for (const [t, l] of more) list.append(row(l, t.selected, () => pick(t)));
    }
    if (!tracks.length) { const p = document.createElement('p'); p.textContent = 'No tracks in this video.'; list.append(p); }
    const parts = [list];
    if (kind === 'subs') {
      const d = document.createElement('div'); d.className = 'delay';
      d.innerHTML = '<button type="button" aria-label="Subtitles earlier">−</button><span id="np-delay"></span><button type="button" aria-label="Subtitles later">＋</button>';
      const [minus, , plus] = d.children, label = d.children[1];
      label.textContent = delayText();
      const bump = x => () => { subDelay = Math.round((subDelay + x) * 10) / 10; label.textContent = delayText(); fire('subdelay', {delta: x}); };
      minus.onclick = bump(-0.1); plus.onclick = bump(0.1);
      parts.push(d);
    }
    content.replaceChildren(...parts);
  }

  async function refresh() {
    try {
      const s = await api('/api/state');
      render(s);
    } catch (err) {
      if (err.message.includes('Pair')) { showPair(); return; }
      if (!pair.hidden) return;
      connected(false, '● Reconnecting');
      message('Reconnecting to the TV…');
    }
  }

  async function send(action, params = {}) {
    if (!online) return;
    try {
      const result = await api('/api/commands', {action, params});
      message('');
      
      if (['update_check', 'update_install', 'select', 'home', 'back', 'open_search', 'seek', 'subtitle', 'audio', 'play', 'set_paused', 'youtube_media', 'youtube_mode', 'skip_segment', 'upnext', 'still_watching', 'restart_app', 'recover', 'timer', 'poweroff', 'brightness', 'screen', 'fullscreen'].includes(action)) refresh();
      return result;
    } catch (err) {
      message(err.message);
      throw err;
    }
  }

  function fire(action, params) { send(action, params).catch(() => {}); }

  function setMode(which) {
    mode = which;
    $('dpad').hidden = which === 'pointer';
    $('pad').hidden = which !== 'pointer';
    $('mode').textContent = which === 'pointer' ? 'Directions' : 'Pointer';
    $('hive').classList.toggle('pointer', which === 'pointer');
    if (state) sessionStorage.setItem('mode-' + state.app, which);
  }

  function openSheet(name) {
    sheetName = name; sheet.hidden = false; updateTabbar();
    $('sheet-title').textContent = {type: 'Type on TV', more: 'Quick actions', off: 'Turn off', torbox: 'Connect TorBox', subs: 'Subtitles', audio: 'Audio'}[name];
    const content = $('sheet-content');
    delete content.dataset.sig;
    if (name === 'subs' || name === 'audio') { renderTrackSheet(); return; }
    if (name === 'type') {
      content.innerHTML = `<p>Send text to the selected TV app. Select its search box first.</p><form id="text-form"><input type="text" id="text-input" autocomplete="off" autocorrect="off" spellcheck="false" placeholder="Type with your Android keyboard"><div class="sheet-row"><button type="submit">Send text</button><button type="button" data-action="enter">Enter</button><button type="button" data-action="delete">⌫ Delete</button></div></form><div class="feedback" id="sheet-feedback"></div>`;
      $('text-form').onsubmit = e => {
        e.preventDefault(); const value = $('text-input').value;
        if (!value) return;
        send('type', {text: value}).then(() => {$('sheet-feedback').textContent = 'Text sent'; $('text-input').value = '';})
          .catch(err => {$('sheet-feedback').textContent = err.message;});
      };
      $('text-input').focus();
    } else if (name === 'torbox') {
      renderTorbox(content);
    } else if (name === 'off') {
      content.innerHTML = `<p>Full shutdown keeps the iMac cool. To start it again, press the physical power button.</p><div class="sheet-row"><button id="off-confirm" class="danger">Turn off iMac</button><button id="off-cancel">Cancel</button></div>`;
      $('off-confirm').onclick = () => fire('poweroff', {confirm: true});
      $('off-cancel').onclick = closeSheet;
    } else {
      content.innerHTML = `<label>Turn off after</label><div class="sheet-row"><button data-minutes="15">15m</button><button data-minutes="30">30m</button><button data-minutes="60">1h</button><button data-minutes="90">1h 30m</button></div><p id="timer-status">No timer set</p><div class="sheet-row"><button data-minutes="0">Cancel timer</button></div><label>Playback</label><div id="subtitles"></div><div class="sheet-row"><button data-action="left">Rewind</button><button data-action="right">Forward</button></div><div class="sheet-row"><button id="turn-off" class="danger">Turn off iMac</button></div>`;
      $('turn-off').onclick = () => openSheet('off');
      content.querySelectorAll('[data-minutes]').forEach(b => b.onclick = () => {
        send('timer', {minutes: Number(b.dataset.minutes) || null}).then(refresh).catch(() => {});
      });
      renderMoreDynamic();
    }
  }

  function renderMoreDynamic() {
    if (!state || sheetName !== 'more') return;
    const timer = state.timer;
    $('timer-status').textContent = timer.active ? `Turns off at ${new Date(timer.deadline * 1000).toLocaleTimeString([], {hour: '2-digit', minute: '2-digit'})}` : 'No timer set';
    const sub = $('subtitles'); sub.replaceChildren();
    if (!state.capabilities.subtitles) {
      sub.textContent = 'Subtitle tracks appear while MPV plays a video.';
      return;
    }
    const row = document.createElement('div'); row.className = 'sheet-row';
    for (const track of state.playback.subtitles) {
      const b = document.createElement('button');
      b.textContent = `${track.selected ? '✓ ' : ''}${langName(track)}`;
      b.onclick = () => fire('subtitle', {id: track.id}); row.append(b);
    }
    const off = document.createElement('button'); off.textContent = 'Off';
    off.onclick = () => fire('subtitle', {id: 'no'}); row.append(off);
    sub.append(row);
  }

  // ---- TorBox key: the value lives only in a local variable, for as long as the request takes ----
  let torboxDone = null;
  const skipGet = () => { try { return localStorage.getItem('torbox-skip') === '1'; } catch (e) { return false; } };
  const skipSet = on => { try { on ? localStorage.setItem('torbox-skip', '1') : localStorage.removeItem('torbox-skip'); } catch (e) {} };
  const needsTorbox = () => !!(state && state.torbox && state.torbox.configured === false && !skipGet());

  function openTorbox(done) {
    torboxDone = done || null;
    openSheet('torbox');
  }

  function renderTorbox(content) {
    const done = torboxDone; torboxDone = null;
    content.innerHTML = `<p>Cached movies start instantly. Find your key at torbox.app → Settings → API.</p><form id="tb-form" autocomplete="off"><div class="keyrow"><input type="password" id="tb-key" name="tb-key" autocomplete="off" autocapitalize="off" autocorrect="off" spellcheck="false" enterkeyhint="done" maxlength="128" placeholder="Paste your TorBox API key" aria-label="TorBox API key"><button type="button" id="tb-eye" aria-label="Show key" aria-pressed="false">👁</button></div><div class="sheet-row"><button type="submit" id="tb-save" class="primary">${done ? 'Save &amp; play' : 'Save'}</button>${done ? '<button type="button" id="tb-skip">Use torrents instead</button>' : ''}</div></form><p id="tb-error" class="tb-error" role="alert"></p><p class="muted">Sent only to your TV, which checks it with TorBox and keeps it there. It's never shown again.</p>`;
    const input = $('tb-key'), err = $('tb-error'), save = $('tb-save'), skip = $('tb-skip');
    $('tb-eye').onclick = () => {
      const show = input.type === 'password';
      input.type = show ? 'text' : 'password';
      $('tb-eye').setAttribute('aria-pressed', String(show));
      $('tb-eye').setAttribute('aria-label', show ? 'Hide key' : 'Show key');
    };
    if (skip) skip.onclick = () => { skipSet(true); closeSheet(); if (done) done(); };
    $('tb-form').onsubmit = async e => {
      e.preventDefault();
      if (save.disabled) return;
      const key = input.value.trim();
      input.value = ''; input.type = 'password'; input.blur();
      $('tb-eye').setAttribute('aria-pressed', 'false');
      if (!key) { err.textContent = 'Paste your TorBox API key first'; return; }
      err.textContent = '';
      const label = save.textContent;
      save.disabled = true; if (skip) skip.disabled = true; $('tb-eye').disabled = true;
      save.textContent = 'Checking with TorBox…';
      try {
        await send('torbox_key', {key});
      } catch (ex) {
        if (sheetName !== 'torbox') return;
        err.textContent = ex.message || 'Could not save the key';
        save.textContent = label; save.disabled = false; if (skip) skip.disabled = false; $('tb-eye').disabled = false;
        return;
      }
      skipSet(false);
      if (state) state.torbox = {configured: true};
      renderTorboxRow('set-torbox-row', {configured: true});
      if (sheetName === 'torbox') closeSheet();
      if (done) done();
      refresh();
    };
  }

  // Small builders for the glass rows that Settings is made of.
  function el(tag, cls, text) {
    const e = document.createElement(tag);
    if (cls) e.className = cls;
    if (text != null) e.textContent = text;
    return e;
  }
  function rowText(title, sub) {
    const t = el('span', 'rt', title);
    if (sub) t.append(el('small', '', sub));
    return t;
  }
  function actionRow(title, sub, fn, cls) {
    const b = el('button', 'row' + (cls ? ' ' + cls : '')); b.type = 'button';
    b.append(rowText(title, sub), el('i', 'chev')); b.onclick = fn;
    return b;
  }
  function section(container, title) {
    container.append(el('h3', '', title));
    const card = el('div', 'glass rows'); container.append(card);
    return card;
  }

  function renderTorboxRow(id = 'torbox-row', torbox = state && state.torbox) {
    const row = $(id);
    if (!row || !torbox) return;
    const on = !!torbox.configured;
    if (row.dataset.on === String(on)) return;
    row.dataset.on = String(on);
    const status = el('div', 'row static');
    status.append(rowText(on ? 'Connected' : 'Not connected', on ? 'Cached movies start instantly' : 'Add an API key to stream cached movies'),
      el('span', 'rv', on ? 'TorBox' : ''));
    const parts = [status, actionRow(on ? 'Change API key' : 'Add API key', 'Checked with TorBox, kept on the TV', () => openTorbox(null))];
    if (on) {
      parts.push(actionRow('Remove key', '', async () => {
        if (!confirm('Remove the TorBox key from the TV?')) return;
        try {
          await send('torbox_remove');
          if (state) state.torbox = {configured: false};
          renderTorboxRow(); renderTorboxRow('set-torbox-row', {configured: false});
        }
        catch (e) { message(e.message); }
      }, 'danger'));
    }
    row.replaceChildren(...parts);
  }

  function closeSheet() { sheet.hidden = true; sheetName = ''; titleToken++; updateTabbar(); }
  $('sheet-close').onclick = closeSheet;
  $('mode').onclick = () => setMode(mode === 'pointer' ? 'dpad' : 'pointer');
  // One request per drag: the readout follows the thumb, the TV changes on release.
  $('brightness').addEventListener('pointerdown', () => draggingBrightness = true);
  $('brightness').addEventListener('input', () => {
    draggingBrightness = true;
    $('brightness-level').textContent = `${$('brightness').value}%`;
    fillRange($('brightness'));
  });
  $('brightness').addEventListener('change', () => {
    draggingBrightness = false;
    send('brightness', {level: Number($('brightness').value)}).catch(() => {});
  });
  // A tap that lands on the thumb fires no change event, so release the latch here
  // or the slider would stop tracking the TV for the rest of the session.
  document.addEventListener('pointerup', () => draggingBrightness = false);
  document.addEventListener('pointercancel', () => draggingBrightness = false);

  $('type-open').onclick = () => openSheet('type');
  $('more-open').onclick = () => openSheet('more');
  // A plain toggle: the server reads the live X state, so a press always flips
  // the screen even if this phone is looking at a poll that is a second old.
  $('screen-off').onclick = () => fire('screen');
  $('seek').addEventListener('pointerdown', () => draggingSeek = true);
  $('seek').addEventListener('input', () => {
    const v = Number($('seek').value), max = Number($('seek').max);
    $('position').textContent = fmt(v); $('duration').textContent = '-' + fmt(max - v);
    fillRange($('seek'));
  });
  $('seek').addEventListener('change', () => {
    fire('seek', {seconds: Number($('seek').value)}); draggingSeek = false;
  });
  document.querySelectorAll('.apps button').forEach(b => b.onclick = () => fire('select', {app: b.dataset.app}));

  // One event path per press. A physical keyboard click has detail=0.
  const held = new Map();
  document.addEventListener('pointerdown', e => {
    const b = e.target.closest('[data-action]');
    if (!b || b.disabled) return;
    e.preventDefault();   // the touch-down buzz comes from the capture listener at the top
    b.classList.add('pressed');
    const action = b.dataset.action;
    const params = action === 'volume' ? {direction: Number(b.dataset.direction)} : {};
    fire(action, params);
    try { b.setPointerCapture(e.pointerId); } catch (x) {}
    if (['up', 'down', 'left', 'right', 'volume'].includes(action)) {
      const delay = setTimeout(() => {
        const interval = setInterval(() => { haptic('tick'); fire(action, params); }, 180);
        held.set(b, {interval});
      }, 450);
      held.set(b, {delay});
    }
  });
  function release(e) {
    const b = e.target.closest('[data-action]');
    if (!b) return;
    b.classList.remove('pressed');
    const h = held.get(b);
    if (h) { clearTimeout(h.delay); clearInterval(h.interval); held.delete(b); }
  }
  document.addEventListener('pointerup', release);
  document.addEventListener('pointercancel', release);
  document.addEventListener('lostpointercapture', release);
  function releaseAll() {
    for (const [button, timer] of held) {
      clearTimeout(timer.delay); clearInterval(timer.interval);
      button.classList.remove('pressed');
    }
    held.clear();
    document.querySelectorAll('.pressed').forEach(b => b.classList.remove('pressed'));
  }
  window.addEventListener('blur', releaseAll);
  document.addEventListener('visibilitychange', () => { if (document.hidden) releaseAll(); });
  document.addEventListener('click', e => {
    const b = e.target.closest('[data-action]');
    if (b && !b.disabled && e.detail === 0) {
      fire(b.dataset.action, b.dataset.action === 'volume' ? {direction: Number(b.dataset.direction)} : {});
    }
  });

  // ---- Touchpad: quiet, coalesced per frame, one move request in flight ----
  const pad = $('pad');
  const quiet = (action, params) => online ? api('/api/commands', {action, params}).catch(() => {}) : Promise.resolve();
  const touches = new Map();   // pointerId -> {x, y, t}
  let gesture = null;          // {start, moved, right, multi, hold}
  let queue = [], remX = 0, remY = 0, moveBusy = false, rafId = 0, rafTimer = 0, scrollAcc = 0, scrollBusy = false;

  function takeSteps() {
    let q = queue; queue = [];
    if (q.length > 20) {   // squeeze into at most 20 pairs by summing neighbours
      const per = Math.ceil(q.length / 20), out = [];
      for (let i = 0; i < q.length; i += per) {
        let x = 0, y = 0;
        for (const [a, b] of q.slice(i, i + per)) { x += a; y += b; }
        out.push([x, y]);
      }
      q = out;
    }
    const c = v => Math.max(-300, Math.min(300, v));
    return q.map(([x, y]) => [c(x), c(y)]).filter(([x, y]) => x || y);
  }
  async function flushMove() {
    cancelAnimationFrame(rafId); clearTimeout(rafTimer); rafId = 0; rafTimer = 0;
    if (moveBusy || !queue.length) return;
    const steps = takeSteps();
    if (!steps.length) return;
    moveBusy = true;
    await quiet('move', steps.length === 1 ? {dx: steps[0][0], dy: steps[0][1]} : {steps});
    moveBusy = false;
    if (queue.length) scheduleMove();
  }
  // rAF coalesces per frame; the timeout covers webviews that pause rAF.
  const scheduleMove = () => { if (!rafId && !moveBusy) { rafId = requestAnimationFrame(flushMove); rafTimer = setTimeout(flushMove, 40); } };
  async function flushScroll() {
    if (scrollBusy) return;
    scrollBusy = true;
    while (Math.abs(scrollAcc) >= 24) {
      const down = scrollAcc < 0;     // finger up = page down
      scrollAcc += down ? 24 : -24;
      await quiet(down ? 'scrolldown' : 'scrollup', {});
    }
    scrollBusy = false;
  }
  // 1.5x for slow movement up to 3x for fast flicks (speed in px/ms).
  function gain(dx, dy, dt) {
    const v = Math.hypot(dx, dy) / Math.max(4, dt);
    return 1.5 + Math.min(1, Math.max(0, (v - 0.2) / 1.6)) * 1.5;
  }
  pad.addEventListener('contextmenu', e => e.preventDefault());
  pad.addEventListener('pointerdown', e => {
    if (!online) return;
    e.preventDefault(); try { pad.setPointerCapture(e.pointerId); } catch (x) {}
    touches.set(e.pointerId, {x: e.clientX, y: e.clientY, t: e.timeStamp});
    if (!gesture) {
      gesture = {sx: e.clientX, sy: e.clientY, moved: false, right: false, multi: false};
      gesture.hold = setTimeout(() => {
        if (gesture && !gesture.moved && !gesture.multi) { gesture.right = true; haptic('long'); quiet('rightclick', {}); }
      }, 550);
    } else {
      gesture.multi = true; clearTimeout(gesture.hold);
      queue = []; remX = remY = 0;
    }
  });
  pad.addEventListener('pointermove', e => {
    const t = touches.get(e.pointerId);
    if (!t || !gesture) return;
    const dx = e.clientX - t.x, dy = e.clientY - t.y, dt = e.timeStamp - t.t;
    t.x = e.clientX; t.y = e.clientY; t.t = e.timeStamp;
    if (gesture.multi) {
      if (touches.size >= 2) scrollAcc += dy / touches.size;
      flushScroll();
      return;
    }
    if (!gesture.moved) {
      if (Math.abs(e.clientX - gesture.sx) + Math.abs(e.clientY - gesture.sy) <= 7) return;
      gesture.moved = true; clearTimeout(gesture.hold);
    }
    if (!dx && !dy) return;
    const g = gain(dx, dy, dt);
    remX += dx * g; remY += dy * g;
    const ix = Math.trunc(remX), iy = Math.trunc(remY);
    if (!ix && !iy) return;
    remX -= ix; remY -= iy;
    queue.push([ix, iy]);
    scheduleMove();
  });
  function endTouch(e, cancelled) {
    if (!touches.delete(e.pointerId) || !gesture) return;
    if (touches.size) return;
    clearTimeout(gesture.hold);
    if (!cancelled && !gesture.moved && !gesture.right && !gesture.multi) { quiet('click', {}); }
    gesture = null; scrollAcc = 0; remX = remY = 0;
  }
  pad.addEventListener('pointerup', e => endTouch(e, false));
  pad.addEventListener('pointercancel', e => endTouch(e, true));
  pad.addEventListener('wheel', e => {e.preventDefault(); quiet(e.deltaY > 0 ? 'scrolldown' : 'scrollup', {});}, {passive: false});

  $('pair-form').onsubmit = async e => {
    e.preventDefault();
    try {
      await api('/api/pair', {code: $('code').value});
      $('pair-error').textContent = '';
      refresh();
    } catch (err) { $('pair-error').textContent = err.message; }
  };

  async function checkAutoPair() {
    try {
      const params = new URLSearchParams(window.location.search);
      const code = params.get('pair') || params.get('code');
      if (code && /^[0-9]{6}$/.test(code)) {
        $('code').value = code;
        await api('/api/pair', {code});
        $('pair-error').textContent = '';
        window.history.replaceState({}, '', window.location.pathname);
        refresh();
      }
    } catch (err) {
      $('pair-error').textContent = err.message;
    }
  }

  // ---- Tabs: Remote / Movies / Series / Search ----
  let tab = 'remote', titleToken = 0, findFocused = false;
  try { const t = sessionStorage.getItem('tab'); if (['remote', 'movie', 'series', 'search', 'settings'].includes(t)) tab = t; } catch (e) {}
  const loaded = {movie: false, series: false}, items = {movie: [], series: []}, busy = {};
  let continueItems = null;

  function updateTabbar() {
    tabbar.hidden = !pair.hidden || !sheet.hidden || findFocused;
  }
  function applyTab() {
    const settingsWasHidden = $('tab-settings').hidden;
    remote.hidden = tab !== 'remote';
    browser.hidden = tab === 'remote';
    for (const id of ['movie', 'series', 'search', 'settings']) $('tab-' + id).hidden = tab !== id;
    tabbar.querySelectorAll('button').forEach(b => b.classList.toggle('on', b.dataset.tab === tab));
    updateTabbar();
    if ((tab === 'movie' || tab === 'series') && online && !loaded[tab]) loadTab(tab);
    if (tab === 'settings' && online) {
      loadSettings();
      if (settingsWasHidden) requestAnimationFrame(() => document.querySelector('#settings-tabs [aria-selected=true]')?.scrollIntoView({block: 'nearest', inline: 'nearest'}));
    } else stopTrialPoll();
  }
  function showTab(name) {
    if (name === tab) { applyTab(); return; }
    tab = name;
    try { sessionStorage.setItem('tab', name); } catch (e) {}
    applyTab();
    window.scrollTo(0, 0);
  }
  // A tap on Movies/Series also puts the matching screen on the TV (fire-and-forget; the server ignores it while something is playing).
  const tvScreen = {movie: 'movies', series: 'series'};
  tabbar.querySelectorAll('button').forEach(b => b.onclick = () => {
    
    const changed = b.dataset.tab !== tab;
    showTab(b.dataset.tab);
    if (changed && tvScreen[b.dataset.tab]) {
      try { Promise.resolve(send('show', {screen: tvScreen[b.dataset.tab]})).catch(() => {}); } catch (e) {}
    }
  });


  // ---- Settings tab: display modes (with a keep-or-revert trial) and the YouTube auto-close ----
  let setState = null, trialTimer = null, setBusy = false, settingsAsked = false;
  const rateText = r => (Math.round(r * 100) / 100) + ' Hz';
  const sizeText = m => m.replace('x', ' × ');
  const wideEnough = m => (parseInt(m, 10) || 0) >= 640;
  function stopTrialPoll() { clearInterval(trialTimer); trialTimer = null; }
  async function loadSettings() {
    try {
      setState = await api('/api/settings');
      $('set-error').textContent = setState.display_error || '';
      renderSettings();
    } catch (err) { $('set-error').textContent = err.message; }
  }
  function renderSettings() {
    renderTorboxRow('set-torbox-row', setState.torbox);
    const t = setState.trial || {active: false};
    $('trial').hidden = !t.active;
    if (t.active) {
      $('trial-mode').textContent = sizeText(t.mode) + ' · ' + rateText(t.rate);
      $('trial-sec').textContent = Math.max(0, Math.ceil(t.remaining));
      if (!trialTimer) trialTimer = setInterval(pollTrial, 1000);
    } else stopTrialPoll();
    const box = $('set-display');
    box.replaceChildren();
    const outs = (setState.outputs || []).filter(o => o.connected);
    if (!outs.length) box.append(el('p', 'note', setState.display_error || 'No connected screen found'));
    for (const o of outs) {
      const cur = o.current, card = section(box, o.name);
      const now = el('div', 'row static');
      now.append(rowText('Now showing'), el('span', 'rv', cur ? `${sizeText(cur.mode)} · ${rateText(cur.rate)}` : 'Screen is off'));
      card.append(now);
      const modes = (o.modes || []).filter(m => wideEnough(m.mode) && !/i$/.test(m.mode));
      const res = el('div', 'chips pad-chips');
      for (const m of modes) {
        const b = el('button', '', sizeText(m.mode) + (o.preferred && o.preferred.mode === m.mode ? ' · Recommended' : '')); b.type = 'button';
        if (cur && cur.mode === m.mode) b.classList.add('cur', 'on');
        const t0 = (cur && cur.mode === m.mode && m.rates.includes(cur.rate)) ? cur.rate : m.rates[0];
        b.onclick = () => startTrial(o.name, m.mode, t0);
        res.append(b);
      }
      card.append(el('div', 'lbl', 'Resolution'), res);
      const curMode = cur && modes.find(m => m.mode === cur.mode);
      if (curMode && curMode.rates.length > 1) {
        const rates = el('div', 'chips pad-chips');
        for (const r of curMode.rates) {
          const b = el('button', '', rateText(r)); b.type = 'button';
          if (cur.rate === r) b.classList.add('cur', 'on');
          b.onclick = () => startTrial(o.name, curMode.mode, r);
          rates.append(b);
        }
        card.append(el('div', 'lbl', 'Refresh rate'), rates);
      }
    }
    if ((setState.values || {}).node_shape && setState.values.node_shape !== nodeShape) { applyShape(setState.values.node_shape); return; }
    skyFromSettings();
    renderBackground();
    renderFields();
    for (const [key, , , , , network] of setState.fields || []) {
      const control = $("setting-" + key);
      if (network && control) control.disabled = !online || setState.values.style === "bubbles";
    }
    const min = setState.youtube_close_after_paused_min || 0;
    document.querySelectorAll('#yt-close button').forEach(b => b.classList.toggle('on', Number(b.dataset.min) === min));
  }
  // Home's photo (Focus) or the first retro picture that's on, far behind everything.
  function skyFromSettings() {
    const bg = setState && setState.background;
    if (!bg) return;
    const retro = bg.source === 'retro' && (bg.retro.find(r => r.on) || bg.retro[0]);
    setSkyPhoto(retro ? '/api/retro/thumb/' + retro.id : '/api/themes/focus.jpg');
  }
  function renderBackground() {
    const bg = setState.background || {source: 'photos', retro: []};
    const list = $('bg-list');
    list.replaceChildren();
    $('bg-retro').hidden = !bg.retro.length || bg.source !== 'retro';
    for (const button of document.querySelectorAll('#bg-source button')) {
      const on = button.dataset.src === bg.source;
      const first = bg.retro.find(r => r.on) || bg.retro[0];
      const src = button.dataset.src === 'photos' ? '/api/themes/focus.jpg' : first ? '/api/retro/thumb/' + first.id : '';
      const url = src ? `url("${src}")` : '';
      if (button.style.backgroundImage !== url) button.style.backgroundImage = url;
      button.classList.toggle('on', on);
      button.setAttribute('aria-pressed', String(on));
    }
    $('bg-retro-sub').textContent = bg.retro.length ? `${bg.retro.length} picture${bg.retro.length === 1 ? '' : 's'}` : 'Add your own pictures';
    const active = bg.retro.filter(r => r.on).length;
    $('bg-count').textContent = active + ' of ' + bg.retro.length + ' on';
    for (const r of bg.retro) {
      const b = el('button', 'bgitem'); b.type = 'button';
      b.classList.toggle('on', r.on);
      b.setAttribute('role', 'switch'); b.setAttribute('aria-checked', String(r.on));
      const i = el('img'); i.alt = ''; i.loading = 'lazy'; i.src = '/api/retro/thumb/' + r.id;
      b.append(i, el('i', 'hcheck'), el('span', '', r.name), el('small', '', r.width + ' × ' + r.height));
      b.onclick = () => settingsCall('retro_toggle', {id: r.id, enabled: !r.on});
      const d = el('button', 'bgdel', '✕'); d.type = 'button';
      d.setAttribute('aria-label', 'Remove ' + r.name);
      d.onclick = ev => { ev.stopPropagation(); if (confirm('Remove "' + r.name + '"?')) settingsCall('retro_remove', {id: r.id}); };
      const item = el('div', 'bgentry'); item.append(b, d); list.append(item);
    }
    $('bg-note').textContent = bg.wanted === 'retro' && !bg.retro.length ? 'No retro pictures yet, so Focus is showing.' : '';
  }
  document.querySelectorAll('#bg-source button').forEach(b => b.onclick = () => {
    const bg = (setState && setState.background) || {};
    if (b.dataset.src === 'retro' && !(bg.retro || []).length) { $('bg-note').textContent = 'Add a picture first.'; return; }
    settingsCall('background_set', {source: b.dataset.src});
  });
  $('bg-add').onclick = () => $('bg-file').click();
  $('bg-file').onchange = async ev => {
    const f = ev.target.files[0]; ev.target.value = '';
    if (!f || setBusy) return;
    setBusy = true; $('bg-note').textContent = 'Sending ' + f.name + '…';
    try {
      const r = await fetch('/api/retro/upload?name=' + encodeURIComponent(f.name), {method: 'POST', credentials: 'same-origin', body: f});
      const res = await r.json();
      if (!r.ok) throw new Error(res.error || 'The TV did not accept that picture');
      const st = res.result.status;
      $('bg-note').textContent = st === 'added' ? 'Added.' : st === 'replaced' ? 'Replaced with the larger picture.' : 'Already have this picture (or a larger one).';
      $('set-error').textContent = '';
    } catch (err) { $('set-error').textContent = err.message; $('bg-note').textContent = ''; }
    setBusy = false;
    await loadSettings();
  };
  async function settingsCall(action, params) {
    if (setBusy) return;
    setBusy = true;
    try {
      await send(action, params);
      $('set-error').textContent = '';
    } catch (err) { $('set-error').textContent = err.message; }
    setBusy = false;
    await loadSettings();
  }
  function startTrial(output, mode, rate) {
    const t = setState && setState.trial;
    if (t && t.active && t.mode === mode && t.rate === rate) return;
    settingsCall('display_trial', {output, mode, rate});
  }
  async function pollTrial() {
    if (tab !== 'settings') { stopTrialPoll(); return; }
    try {
      const s = await api('/api/settings');
      setState = s; renderSettings();
    } catch (err) { /* keep counting; the TV reverts on its own */ }
  }
  $('trial-keep').onclick = () => { settingsCall('display_keep'); };
  $('trial-revert').onclick = () => { settingsCall('display_revert'); };
  document.querySelectorAll('#yt-close button').forEach(b => b.onclick = () => {
    settingsCall('youtube_close', {minutes: Number(b.dataset.min)});
  });

  $('settings-open').onclick = () => showTab('settings');
  const categories = ['Appearance', 'Ambient', 'Display', 'Playback', 'Connections', 'System'];
  let settingsCategory = 'appearance';
  try { const saved = sessionStorage.getItem('settings-category'); if (categories.some(c => c.toLowerCase() === saved)) settingsCategory = saved; } catch (e) {}
  function selectCategory(id, focus = false) {
    settingsCategory = id;
    try { sessionStorage.setItem('settings-category', id); } catch (e) {}
    categories.forEach(c => {
      const key = c.toLowerCase(), b = $('category-' + key), active = key === id;
      b.setAttribute('aria-selected', String(active)); b.tabIndex = active ? 0 : -1;
      $('settings-' + key).hidden = !active;
      if (active) { if (focus) b.focus(); b.scrollIntoView({block: 'nearest', inline: 'nearest'}); }
    });
    $('settings-sub').textContent = CATEGORY_SUB[id] || '';
  }
  // The TV's subtitles and glyphs for each category, so both screens say the same thing.
  const CATEGORY_SUB = {appearance: 'Make it yours. Shape the look and choose a lighter experience.',
    ambient: 'Set the mood for the moments between watching.', display: 'A comfortable picture, matched to your screen.',
    playback: 'Your languages, quality and watching preferences.', connections: 'Keep your phone and streaming services close.',
    system: 'Everything you need to keep your TV running smoothly.'};
  const CATEGORY_GLYPH = {
    appearance: '<circle cx="12" cy="12" r="7"/><path d="M12 5a7 7 0 0 1 0 14z" fill="currentColor" stroke="none"/>',
    ambient: '<path d="M16.5 15.8A6.6 6.6 0 0 1 9.2 6a6.6 6.6 0 1 0 7.3 9.8z"/>',
    display: '<rect x="4" y="5.5" width="16" height="10.5" rx="1.5"/><path d="M9.5 19.5h5M12 16v3.5"/>',
    playback: '<circle cx="12" cy="12" r="7.5"/><path d="M10.3 9v6l5-3z"/>',
    connections: '<rect x="5" y="5" width="7" height="14" rx="1.6"/><path d="M15.5 9.5a3.5 3.5 0 0 1 0 5M18 7.5a6.5 6.5 0 0 1 0 9"/>',
    system: '<circle cx="12" cy="12" r="2.6"/><path d="M12 4.5v2.2M12 17.3v2.2M4.5 12h2.2M17.3 12h2.2M6.7 6.7l1.6 1.6M15.7 15.7l1.6 1.6M6.7 17.3l1.6-1.6M15.7 8.3l1.6-1.6"/>'};
  categories.forEach((c, i) => {
    const b = document.createElement('button'); b.type = 'button'; b.id = 'category-' + c.toLowerCase();
    b.innerHTML = `<span class="badge" aria-hidden="true"><svg viewBox="0 0 24 24">${CATEGORY_GLYPH[c.toLowerCase()]}</svg></span><span>${c}</span>`;
    paintBadge(b);
    b.setAttribute('role', 'tab'); b.setAttribute('aria-controls', 'settings-' + c.toLowerCase());
    b.onclick = () => selectCategory(c.toLowerCase());
    b.onkeydown = e => {
      const next = e.key === 'ArrowRight' ? (i + 1) % 6 : e.key === 'ArrowLeft' ? (i + 5) % 6 : e.key === 'Home' ? 0 : e.key === 'End' ? 5 : null;
      if (next !== null) { e.preventDefault(); selectCategory(categories[next].toLowerCase(), true); }
    };
    $('settings-tabs').append(b);
  });
  selectCategory(settingsCategory);
  const settingLabel = v => ({network: 'Network', bubbles: 'Bubbles', rounded: 'Rounded', chamfer: 'Chamfer', hexagon: 'Hexagon', octagon: 'Octagon', glass: 'Glass', light: 'Light', photo: 'Photo outlines', 'photo+animals': 'Photo outlines and animals', off: 'Off'}[v] || String(v));
  function orderPicker(key, title, value, languages, save) {
    let ordered = [...value];
    sheetName = 'setting'; sheet.hidden = false; updateTabbar();
    $('sheet-title').textContent = title;
    const content = $('sheet-content');
    const draw = () => {
      content.replaceChildren();
      const hint = document.createElement('p'); hint.textContent = 'First item is preferred.'; content.append(hint);
      ordered.forEach((v, i) => {
        const row = document.createElement('div'); row.className = 'order-row';
        const label = document.createElement('span'); label.textContent = LANG[v] || v; row.append(label);
        for (const [text, delta] of [['Move up', -1], ['Move down', 1]]) {
          const b = document.createElement('button'); b.type = 'button'; b.textContent = delta < 0 ? '↑' : '↓'; b.setAttribute('aria-label', text + ' ' + (LANG[v] || v));
          b.disabled = i + delta < 0 || i + delta >= ordered.length;
          b.onclick = () => { [ordered[i], ordered[i + delta]] = [ordered[i + delta], ordered[i]]; draw(); content.querySelectorAll('.order-row')[i + delta].querySelector('button:not(:disabled)')?.focus(); }; row.append(b);
        }
        if (languages) { const remove = document.createElement('button'); remove.type = 'button'; remove.textContent = 'Remove'; remove.setAttribute('aria-label', 'Remove ' + (LANG[v] || v)); remove.disabled = ordered.length === 1; remove.onclick = () => { ordered.splice(i, 1); draw(); }; row.append(remove); }
        content.append(row);
      });
      if (languages) {
        const label = document.createElement('label'); label.textContent = 'Add language code'; label.htmlFor = 'add-language';
        const input = document.createElement('input'); input.id = 'add-language'; input.placeholder = 'e.g. eng'; input.maxLength = 3;
        const add = document.createElement('button'); add.type = 'button'; add.textContent = 'Add';
        add.onclick = () => { const code = input.value.trim().toLowerCase(); if (!/^[a-z]{2,3}$/.test(code) || ordered.includes(code)) { input.setCustomValidity('Enter a new two or three letter language code'); input.reportValidity(); return; } ordered.push(code); draw(); };
        input.oninput = () => input.setCustomValidity(''); content.append(label, input, add);
      }
      const done = document.createElement('button'); done.type = 'button'; done.textContent = 'Save order'; done.onclick = () => { closeSheet(); save(ordered); }; content.append(done);
    };
    draw(); content.querySelector('button:not(:disabled)')?.focus();
  }

  // Section labels as on the TV: a heading starts at its first key.
  const HEADINGS = {transparency: 'Surfaces & motion', style: 'Home screen', interval: 'Timing', visualizer: 'While idle',
    sub_default: 'Languages & quality', autoplay_next: 'Episodes'};
  const WIDGET_NAMES = {clock: 'Clock', weather: 'Weather', pairing: 'Pairing', continue: 'Continue watching', apps: 'Apps'};
  function switchRow(title, sub, on, fn) {
    const b = el('button', 'row'); b.type = 'button'; b.setAttribute('role', 'switch'); b.setAttribute('aria-checked', String(on));
    const v = el('span', 'rv', on ? 'On' : 'Off');
    b.append(rowText(title, sub), v, el('i', 'htog'));
    // The toggle answers the finger at once; the reload after the save confirms it.
    b.onclick = () => { const now = b.getAttribute('aria-checked') !== 'true'; b.setAttribute('aria-checked', String(now)); v.textContent = now ? 'On' : 'Off'; fn(now); };
    return b;
  }
  function renderFields() {
    for (const category of ['appearance', 'ambient', 'playback']) {
      const container = $('fields-' + category);
      // Keep a half-typed value intact during the display countdown poll.
      const active = document.activeElement;
      if (container.contains(active) && active.matches('input, select')) continue;
      container.replaceChildren();
      let card = null;
      for (const [key, group, title, kind, options, network] of setState.fields || []) {
        if (group.toLowerCase() !== category) continue;
        if (!card || HEADINGS[key]) card = section(container, HEADINGS[key] || 'General');
        const value = setState.values[key];
        const save = value => settingsCall('setting_set', {key, value});
        const parked = network && setState.values.style === 'bubbles';
        const sub = parked ? 'Available with the Network layout. Your choice is kept.' : '';
        let row, control;
        if (kind === 'bool') {
          row = control = switchRow(title, sub, !!value, save);
        } else if (kind === 'choice') {
          row = el('label', 'row');
          control = el('select');
          for (const v of options) { const o = el('option', '', settingLabel(v)); o.value = v; control.append(o); }
          control.value = value; control.onchange = () => save(control.value);
          control.setAttribute('aria-label', title);
          row.append(rowText(title, sub), el('span', 'rv', settingLabel(value)), el('i', 'chev'), control);
        } else if (kind === 'order' || kind === 'languages') {
          row = control = actionRow(title, sub, () => orderPicker(key, title, value, kind === 'languages', save));
          row.insertBefore(el('span', 'rv', value.map(v => LANG[v] || v).join(' → ')), row.lastChild);
        } else if (kind === 'multi') {
          row = el('div', 'row static');
          row.append(rowText(title, sub), el('span', 'rv', `${value.filter(v => options.includes(v)).length} of ${options.length}`));
          card.append(row);
          control = el('div', 'multi');
          for (const v of options) {
            const on = value.includes(v);
            const b = switchRow(WIDGET_NAMES[v] || settingLabel(v), '', on, now => save(now ? [...value.filter(x => options.includes(x)), v] : value.filter(x => options.includes(x) && x !== v)));
            b.classList.add('sub'); b.setAttribute('aria-label', title + ': ' + v);
            card.append(b);
          }
          row = null;
        } else {
          row = el('label', 'row');
          control = el('input', 'rv'); control.type = kind === 'number' ? 'number' : 'text';
          control.value = Array.isArray(value) ? value.join(', ') : value;
          control.setAttribute('aria-label', title);
          if (kind === 'number') { control.min = options[0]; control.max = options[1]; control.step = key === 'still_watching_hours' ? '0.1' : '1'; control.inputMode = 'decimal'; }
          else { control.autocapitalize = 'off'; control.spellcheck = false; control.maxLength = 3; }
          control.enterKeyHint = 'done';
          // Saved when the value is committed (keyboard Done or leaving the field), like a TV row.
          control.onchange = () => { if (control.reportValidity()) save(kind === 'number' ? Number(control.value) : control.value.trim().toLowerCase()); };
          control.onkeydown = e => { if (e.key === 'Enter') control.blur(); };
          row.append(rowText(title, sub), control);
        }
        control.id = 'setting-' + key;
        if (parked) control.disabled = true;
        if (row) card.append(row);
      }
    }
    renderConnections();
    renderSystem();
  }
  // Connections: the TV's glass QR card says "scan to connect"; here the same card shows the link it made.
  function renderConnections() {
    const access = $('phone-access'); access.replaceChildren();
    const card = el('div', 'glass linkcard' + (online ? '' : ' offline'));
    const sig = el('div', 'sigline'), bars = el('span', 'sig');
    for (let i = 0; i < 4; i++) bars.append(el('i', online || i === 0 ? '' : 'dim'));
    sig.append(bars, el('span', '', online ? 'iMac TV · paired' : 'iMac TV · not reachable'));
    const L = [44, 48], R = [256, 48], r = 30;
    const node = ([x, y], lit) => `<polygon class="node${lit ? ' lit' : ''}" points="${ptsAttr(polyPts(r, x, y))}"/>`;
    const dots = [[104, 40], [150, 55], [196, 41]];
    const wire = [[L[0] + r, L[1]], ...dots, [R[0] - r, R[1]]];
    const art = `<svg class="linkart" viewBox="0 0 300 96" aria-hidden="true">` +
      wire.slice(1).map((p, i) => `<line class="wire${online ? '' : ' off'}" x1="${wire[i][0]}" y1="${wire[i][1]}" x2="${p[0]}" y2="${p[1]}"/>`).join('') +
      dots.map(([x, y]) => `<path class="dot" d="M${x} ${y - 2.6}l2.6 2.6-2.6 2.6-2.6-2.6z"/>`).join('') +
      node(L, online) + node(R, online) +
      `<rect class="gl" x="${L[0] - 5.5}" y="${L[1] - 10}" width="11" height="20" rx="2.2"/><path class="gl" d="M${L[0] - 1.5} ${L[1] + 6.5}h3"/>` +
      `<rect class="gl" x="${R[0] - 11}" y="${R[1] - 8.5}" width="22" height="14" rx="1.6"/><path class="gl" d="M${R[0] - 4.5} ${R[1] + 9.5}h9"/></svg>`;
    const wrap = el('div'); wrap.innerHTML = art;
    card.append(sig, wrap.firstChild, el('h4', '', online ? 'This phone is connected' : 'Reconnecting…'),
      el('p', 'meta', 'Pair another phone with the QR code on TV Home.'));
    const rows = el('div', 'glass rows');
    for (const [title, sub, mode, question] of [['Disconnect other phones', 'Every phone except this one pairs again', 'other_phones', 'Disconnect every other paired phone?'],
      ['Disable legacy link', 'Phones on the old link must pair again', 'old_link', 'Disable the old remote link? Phones using it must pair again.']]) {
      const b = actionRow(title, sub, async () => { if (!confirm(question)) return; try { await api('/api/revoke', {mode}); refresh(); } catch (e) { message(e.message); } });
      b.disabled = mode === 'old_link' && state && !state.old_link_active;
      rows.append(b);
    }
    access.append(card, rows);
  }
  function renderSystem() {
    const system = $('settings-system'); system.replaceChildren();
    const updates = section(system, 'Software updates');
    const updateText = el('p', 'note'); updateText.id = 'update-status'; updateText.setAttribute('role', 'status');
    updates.append(updateText);
    const check = actionRow('Check for updates', 'Checks GitHub for a new TV version', () => fire('update_check'));
    check.id = 'update-check'; updates.append(check);
    const install = actionRow('Update now', 'Installs and restarts the TV app; playback stops', installUpdate);
    install.id = 'update-install'; updates.append(install);
    const feel = section(system, 'This phone');
    const sw = switchRow('Remote haptics', canVibrate ? 'One light tap as you press' : 'This browser cannot vibrate', haptics, on => {
      haptics = on;
      try { localStorage.setItem('remote-haptics', on ? 'on' : 'off'); } catch (e) {}
      haptic('press');
    });
    sw.id = 'haptics'; feel.append(sw);
    const fix = section(system, 'Maintenance');
    for (const [title, sub, action] of [['Restart current app', 'Closes and reopens what is on screen', 'restart_app'],
      ['Recover TV Home', 'Brings Home back to the front', 'recover'], ['Restart desktop session', 'Stops the current video', 'restart_session']]) {
      fix.append(actionRow(title, sub, () => { if (action === 'restart_session' && !confirm('Restart the TV desktop? This stops the current video.')) return; fire(action, action === 'restart_session' ? {confirm: true} : {}); }));
    }
    system.append(el('h3', '', 'Status'));
    const deg = v => (v == null ? '—' : Math.round(v) + '°C');
    system.append(el('p', 'note', `${online ? 'Connected' : 'Offline'} · CPU ${deg(state && state.temperature)} · GPU ${deg(state && state.gpu_temperature)}`));
    renderUpdateStatus();
  }

  async function installUpdate() {
    if (updatePending) return;
    updatePending = true;
    renderUpdateStatus();
    try { await send('update_install'); }
    catch (err) { /* send displays the error */ }
    finally { updatePending = false; renderUpdateStatus(); }
  }
  $('update-notice').onclick = installUpdate;
  function renderUpdateStatus() {
    const u = (state && state.updates) || {};
    const busy = Boolean(u.busy || updatePending);
    const notice = $('update-notice');
    notice.hidden = !(u.available || updatePending || ['queued', 'updating', 'restarting'].includes(u.status));
    notice.disabled = !online || busy || !u.can_update;
    notice.textContent = busy ? (u.message || 'Starting update…') : 'TV update available · Update now';
    notice.title = 'Installs the update and restarts the TV app. Playback will stop.';
    if ($('update-status')) $('update-status').textContent = u.message || 'Waiting for the controller…';
    if ($('update-check')) $('update-check').disabled = !online || busy || !state?.updates;
    if ($('update-install')) $('update-install').disabled = !online || busy || !u.can_update;
  }

  const data = r => (r && r.result) || {};
  function posterEl(item) {
    const b = document.createElement('button');
    b.type = 'button'; b.className = 'poster'; b.disabled = !online;
    const art = document.createElement('div'); art.className = 'art';
    if (item.poster) {
      const img = document.createElement('img');
      img.loading = 'lazy'; img.alt = ''; img.src = item.poster;
      img.onerror = () => { img.remove(); art.textContent = item.name || ''; };
      art.append(img);
    } else art.textContent = item.name || 'Untitled';
    const nm = document.createElement('div'); nm.className = 'nm';
    nm.textContent = item.name || '';
    b.append(art, nm);
    if (item.position > 0 && item.duration > 0) {
      const p = document.createElement('div'); p.className = 'prog';
      const i = document.createElement('i'); i.style.width = Math.min(100, item.position / item.duration * 100) + '%';
      p.append(i); b.append(p);
    }
    b.onclick = () => { openTitle(item); };
    return b;
  }
  function fill(el, list, append) {
    if (!append) el.replaceChildren();
    el.append(...list.map(posterEl));
  }

  async function loadTab(type, more) {
    if (busy[type]) return;
    if (!more && loaded[type]) return;
    const pane = $(type === 'movie' ? 'tab-movie' : 'tab-series');
    busy[type] = true;
    pane.querySelector('.spin').hidden = false;
    pane.querySelector('.more').hidden = true;
    try {
      const jobs = [send('browse', {type, skip: more ? items[type].length : 0})];
      if (!more && continueItems === null) jobs.push(send('continue').then(r => { continueItems = data(r).items || []; }).catch(() => { continueItems = []; }));
      const r = await jobs[0];
      if (!r) throw new Error('Not connected');
      const list = data(r).items || [];
      items[type] = more ? items[type].concat(list) : list;
      fill(pane.querySelector('.pbrowse .grid'), list, more);
      loaded[type] = true;
      pane.querySelector('.more').hidden = !list.length;
      if (jobs[1]) await jobs[1];
      const cont = (continueItems || []).filter(i => i.type === type);
      pane.querySelector('.cont').hidden = !cont.length;
      fill(pane.querySelector('.hrow'), cont, false);
      if (!list.length && !more && !items[type].length) message('Nothing to show here yet');
    } catch (e) { /* toast already shown by send() */ }
    busy[type] = false;
    pane.querySelector('.spin').hidden = true;
  }
  document.querySelectorAll('.pane .more').forEach(b => b.onclick = () => loadTab(b.closest('.pane').dataset.type, true));

  // ---- Title sheet ----
  async function openTitle(item) {
    const token = ++titleToken;
    sheetName = 'title'; sheet.hidden = false; updateTabbar();
    $('sheet-title').textContent = item.name || 'Title';
    const content = $('sheet-content');
    content.innerHTML = '<div class="spin"><div class="spinner"></div></div>';
    let t;
    try { t = data(await send('title', {type: item.type, id: item.id})); } catch (e) { if (token === titleToken) closeSheet(); return; }
    if (token !== titleToken || sheetName !== 'title') return;
    const el = document.createElement('div'); el.className = 'tsheet';
    const bg = t.background || t.poster || item.poster;
    if (bg) { const h = document.createElement('div'); h.className = 'hero'; h.style.backgroundImage = `url("${String(bg).replace(/"/g, '%22')}")`; el.append(h); }
    const h2 = document.createElement('h2'); h2.textContent = t.name || item.name || ''; el.append(h2);
    const meta = [t.year || item.year, t.runtime, Array.isArray(t.genres) ? t.genres.join(', ') : t.genres].filter(Boolean).join(' · ');
    if (meta) { const m = document.createElement('div'); m.className = 'meta'; m.textContent = meta; el.append(m); }
    if (t.description) {
      const d = document.createElement('p'); d.className = 'desc'; d.textContent = t.description;
      d.onclick = () => d.classList.toggle('open'); el.append(d);
    }
    const type = t.type || item.type, id = t.id || item.id;
    const play = async params => {
      if (needsTorbox()) { openTorbox(() => play(params)); return; }
      
      try { await send('play_media', Object.assign({type, id}, params)); } catch (e) { return; }
      closeSheet(); showTab('remote'); refresh();
    };
    const btn = (label, fn) => { const b = document.createElement('button'); b.type = 'button'; b.className = 'playbtn'; b.textContent = label; b.disabled = !online; b.onclick = fn; return b; };
    if (type === 'series') {
      const seasons = (t.seasons || []).filter(s => s.episodes && s.episodes.length);
      const chipBox = document.createElement('div'); chipBox.className = 'chips'; chipBox.style.margin = '10px 0';
      const eps = document.createElement('div'); eps.className = 'eps';
      let cur = (seasons.find(s => s.season >= 1) || seasons[0] || {}).season;
      const draw = () => {
        chipBox.replaceChildren(...seasons.map(s => chip(s.season === 0 ? 'Specials' : `Season ${s.season}`, s.season === cur, () => { cur = s.season; draw(); })));
        const s = seasons.find(x => x.season === cur);
        eps.replaceChildren(...((s && s.episodes) || []).map(ep => {
          const b = document.createElement('button'); b.type = 'button'; b.disabled = !online;
          const n = document.createElement('b'); n.textContent = ep.episode;
          const nm = document.createElement('span'); nm.textContent = ep.name || `Episode ${ep.episode}`;
          b.append(n, nm);
          if (ep.released) { const r = document.createElement('small'); r.textContent = String(ep.released).slice(0, 10); b.append(r); }
          b.onclick = () => play({season: cur, episode: ep.episode});
          return b;
        }));
      };
      if (seasons.length) { draw(); el.append(chipBox, eps); }
      else { const p = document.createElement('p'); p.className = 'muted'; p.textContent = 'No episodes found.'; el.append(p); }
    } else if (t.resume > 0) {
      el.append(btn(`Resume from ${fmt(t.resume)}`, () => play({})), btn('Start over', () => play({start: 0})));
    } else {
      el.append(btn('Play on TV', () => play({})));
    }
    content.replaceChildren(el);
  }

  // ---- Search tab: results on the phone, keyboard stays out of the way ----
  const fi = $('find-input');
  let findTimer = null, findSeq = 0;
  fi.addEventListener('focus', () => { findFocused = true; updateTabbar(); });
  fi.addEventListener('blur', () => { findFocused = false; updateTabbar(); });
  async function runFind() {
    clearTimeout(findTimer);
    const q = fi.value.trim(), seq = ++findSeq;
    if (q.length < 2) { showFind(null); return; }
    $('find-spin').hidden = false; $('find-empty').textContent = '';
    try {
      const r = data(await send('find', {query: q}));
      if (seq !== findSeq) return;
      showFind(r);
    } catch (e) { if (seq === findSeq) showFind(null); }
  }
  function showFind(r) {
    $('find-spin').hidden = true;
    const movies = (r && r.movies) || [], series = (r && r.series) || [];
    fill($('find-movies').querySelector('.grid'), movies, false);
    fill($('find-series').querySelector('.grid'), series, false);
    $('find-movies').hidden = !movies.length; $('find-series').hidden = !series.length;
    $('find-empty').textContent = r && !movies.length && !series.length ? 'No results' : '';
  }
  fi.addEventListener('input', () => {
    $('find-clear').hidden = !fi.value;
    clearTimeout(findTimer);
    if (fi.value.trim().length < 2) { findSeq++; showFind(null); return; }
    findTimer = setTimeout(runFind, 450);
  });
  $('find-form').onsubmit = e => { e.preventDefault(); fi.blur(); runFind(); };
  $('find-clear').onclick = () => { fi.value = ''; $('find-clear').hidden = true; findSeq++; clearTimeout(findTimer); showFind(null); };
  const results = $('find-results');
  // Not on window scroll: Android scrolls the page while the keyboard opens, which would blur it at once.
  results.addEventListener('touchstart', () => fi.blur(), {passive: true});

  // ---- Movies / Series panes: search scoped to that kind ----
  document.querySelectorAll('.pane[data-type]').forEach(pane => {
    const type = pane.dataset.type, input = pane.querySelector('.pfind input'), clear = pane.querySelector('.pclear');
    const res = pane.querySelector('.presults'), browse = pane.querySelector('.pbrowse');
    const spin = pane.querySelector('.rspin'), empty = pane.querySelector('.pempty'), grid = res.querySelector('.grid');
    let timer = null, seq = 0;
    function show(list, searching) {
      res.hidden = !searching; browse.hidden = searching;
    }
    function reset() { seq++; clearTimeout(timer); spin.hidden = true; empty.textContent = ''; grid.replaceChildren(); show(null, false); }
    async function run() {
      clearTimeout(timer);
      const q = input.value.trim(), mine = ++seq;
      if (q.length < 2) { reset(); return; }
      show(null, true); spin.hidden = false; empty.textContent = '';
      try {
        const r = data(await send('find', {query: q}));
        if (mine !== seq) return;
        const list = (type === 'movie' ? r.movies : r.series) || [];
        spin.hidden = true; fill(grid, list, false);
        empty.textContent = list.length ? '' : 'No results';
      } catch (e) { if (mine === seq) { spin.hidden = true; grid.replaceChildren(); } }
    }
    input.addEventListener('focus', () => { findFocused = true; updateTabbar(); });
    input.addEventListener('blur', () => { findFocused = false; updateTabbar(); });
    input.addEventListener('input', () => {
      clear.hidden = !input.value;
      clearTimeout(timer);
      if (input.value.trim().length < 2) { reset(); return; }
      timer = setTimeout(run, 450);
    });
    pane.querySelector('.pfind').onsubmit = e => { e.preventDefault(); input.blur(); run(); };
    clear.onclick = () => { input.value = ''; clear.hidden = true; reset(); };
    // Not on window scroll: Android scrolls the page while the keyboard opens, which would blur it at once.
    res.addEventListener('touchstart', () => input.blur(), {passive: true});
  });

  document.addEventListener('visibilitychange', () => { if (!document.hidden) refresh(); });
  applyShape(nodeShape);
  drawSky(true);
  tickClock();
  checkAutoPair();
  refresh(); setInterval(refresh, 2000);
})();
