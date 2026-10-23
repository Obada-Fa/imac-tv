(() => {
  'use strict';
  const $ = id => document.getElementById(id);
  const remote = $('remote'), pair = $('pair'), sheet = $('sheet');
  let state = null, online = false, mode = 'dpad', sheetName = '', draggingSeek = false;
  let draggingBrightness = false, brightnessReason = '';

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
  const buzz = ms => { try { if (navigator.vibrate) navigator.vibrate(ms); } catch (e) {} };

  function connected(value, label) {
    online = value;
    $('connection').textContent = label;
    $('connection').classList.toggle('off', !value);
    document.querySelectorAll('#controls button, footer button, .apps button').forEach(b => b.disabled = !value);
  }

  function showPair() {
    pair.hidden = false; remote.hidden = true; sheet.hidden = true;
    connected(false, 'Pair this phone');
  }

  function fmt(seconds) {
    seconds = Math.max(0, Math.floor(Number(seconds) || 0));
    const m = Math.floor(seconds / 60), s = String(seconds % 60).padStart(2, '0');
    return `${m}:${s}`;
  }

  function render(s) {
    state = s;
    pair.hidden = true; remote.hidden = false;
    connected(Boolean(s.gui), s.gui ? '● Connected' : '● TV starting');
    const appNames = {home: 'TV Home', youtube: 'YouTube', player: 'Player'};
    const pb = s.playback || {}, name = (s.media && s.media.name) || pb.title;
    $('app-name').textContent = (pb.active || s.phase === 'buffering') && name ? name : (appNames[s.app] || s.app);
    $('phase').textContent = s.phase === 'opening' ? 'Opening…' : s.phase === 'buffering' ? 'Buffering…' :
      pb.active ? (pb.paused ? 'Paused' : 'Playing') : s.gui ? 'Ready' : 'Waiting for TV screen';
    renderView(s, pb, name);
    const heat = [];
    if (s.temperature != null) heat.push(`${Math.round(s.temperature)}° CPU`);
    if (s.gpu_temperature != null) heat.push(`${Math.round(s.gpu_temperature)}° GPU`);
    $('temperature').textContent = (s.thermal_warning ? '⚠ Hot · ' : '') + heat.join(' · ');
    $('temperature').classList.toggle('warn', Boolean(s.thermal_warning));
    document.querySelectorAll('.apps button').forEach(b => b.classList.toggle('selected', b.dataset.app === s.app));
    $('volume-label').textContent = s.volume == null ? 'Volume' : `${s.muted ? 'Muted · ' : ''}${s.volume}%`;
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
    }
    if (reason && reason !== brightnessReason) {
      brightnessReason = reason;
      message(reason);
    } else if (!reason) {
      brightnessReason = '';
    }
    $('fullscreen').classList.toggle('active', Boolean(s.fullscreen));
    $('fullscreen').setAttribute('aria-pressed', String(Boolean(s.fullscreen)));
    if (s.error) { if (s.error !== lastError) { lastError = s.error; buzz([30, 40, 30]); message(s.error); } }
    else {
      lastError = '';
      if ($('message').textContent === 'Reconnecting to the TV…') message('');
    }
    const preferred = 'dpad';
    const remembered = sessionStorage.getItem('mode-' + s.app);
    if (mode !== (remembered || preferred) && !sheetName && !searching) setMode(remembered || preferred);
    if (sheetName === 'more') renderMoreDynamic();
  }

  const LANG = {ara: 'Arabic', ar: 'Arabic', dut: 'Nederlands', nld: 'Nederlands', nl: 'Nederlands', eng: 'English', en: 'English'};
  const langName = t => LANG[String(t.lang || '').toLowerCase()] || t.title || t.lang || `Track ${t.id}`;
  let sigs = {}, subDelay = 0, searching = false, sent = '', typeTimer = null;

  function chips(el, key, items, make) {
    const sig = JSON.stringify(items.map(t => [t.id, t.selected]));
    if (sigs[key] === sig) return;
    sigs[key] = sig; el.replaceChildren(...make());
  }
  function chip(label, on, fn) {
    const b = document.createElement('button');
    b.textContent = label; b.classList.toggle('on', on); b.onclick = () => { buzz(8); fn(); };
    return b;
  }
  function labelled(tracks) {
    const seen = {};
    return tracks.map(t => { const n = langName(t); seen[n] = (seen[n] || 0) + 1; return [t, seen[n] > 1 ? `${n} ${seen[n]}` : n]; });
  }

  function renderView(s, pb, name) {
    const view = pb.active ? 'playing' : s.phase === 'buffering' ? 'buffering' : 'browse';
    const c = $('controls');
    if (c.dataset.view !== view) {
      c.dataset.view = view;
      if (view !== 'browse') closeSearch();
      if (view !== 'playing') { subDelay = 0; sigs = {}; }
    }
    $('browse').hidden = view !== 'browse';
    $('buffer').hidden = view !== 'buffering';
    $('nowplaying').hidden = view !== 'playing';
    $('mode').hidden = view !== 'browse';
    if (view === 'buffering') {
      const b = s.buffering || {};
      $('buf-name').textContent = name || 'Starting…';
      $('buf-source').textContent = b.source || 'Finding the best source…';
      $('buf-stats').textContent = b.peers != null ? `${b.peers} peers · ${((b.speed || 0) / 1048576).toFixed(1)} MB/s` : '';
    } else if (view === 'playing') {
      $('np-title').textContent = name || 'Video';
      $('np-play').textContent = pb.paused ? '▶' : '⏸';
      if (!draggingSeek) {
        $('seek').max = Math.max(1, pb.duration || 1);
        $('seek').value = pb.position || 0;
        $('position').textContent = fmt(pb.position);
        $('duration').textContent = '-' + fmt((pb.duration || 0) - (pb.position || 0));
      }
      const subs = pb.subtitles || [], audio = pb.audio || [];
      chips($('np-subs'), 'sub', subs, () => {
        const out = labelled(subs).map(([t, l]) => chip(l, t.selected, () => fire('subtitle', {id: t.id})));
        out.push(chip('Off', !subs.some(t => t.selected), () => fire('subtitle', {id: 'no'})));
        return out;
      });
      $('np-audio-wrap').hidden = audio.length < 2;
      chips($('np-audio'), 'aud', audio, () => labelled(audio).map(([t, l]) => chip(l, t.selected, () => fire('audio', {id: t.id}))));
      $('np-delay').textContent = `Delay ${subDelay > 0 ? '+' : ''}${subDelay.toFixed(1)}s`;
    }
    $('search-open').classList.toggle('on', searching);
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
    buzz(10);
    searching = true;
    $('search-box').hidden = false; $('search-input').value = ''; sent = '';
    $('search-input').focus();  // inside the tap so the native keyboard opens
    try { await send('open_search'); } catch (e) { return; }
    $('search-input').focus();
  }
  $('search-open').onclick = openSearch;
  $('search-input').addEventListener('input', () => { clearTimeout(typeTimer); typeTimer = setTimeout(flushTyping, 400); });
  $('search-box').onsubmit = async e => { e.preventDefault(); await flushTyping(); fire('enter'); };
  $('search-del').onclick = async () => {
    const i = $('search-input');
    if (i.value) { i.value = i.value.slice(0, -1); await flushTyping(); } else fire('delete');
    i.focus();
  };
  document.querySelectorAll('[data-skip]').forEach(b => b.onclick = () => {
    const p = state && state.playback; if (!p || !p.active) return;
    fire('seek', {seconds: Math.max(0, Math.min(p.duration || 86400, (p.position || 0) + Number(b.dataset.skip)))});
  });
  document.querySelectorAll('[data-subdelay]').forEach(b => b.onclick = () => {
    const d = Number(b.dataset.subdelay);
    subDelay = Math.round((subDelay + d) * 10) / 10; $('np-delay').textContent = `Delay ${subDelay > 0 ? '+' : ''}${subDelay.toFixed(1)}s`;
    fire('subdelay', {delta: d});
  });

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
      buzz(8);
      if (['select', 'home', 'back', 'open_search', 'seek', 'subtitle', 'audio', 'play', 'restart_app', 'recover', 'timer', 'poweroff', 'brightness', 'screen', 'fullscreen'].includes(action)) refresh();
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
    if (state) sessionStorage.setItem('mode-' + state.app, which);
  }

  function openSheet(name) {
    sheetName = name; sheet.hidden = false;
    $('sheet-title').textContent = {type: 'Type on TV', more: 'More', off: 'Turn off'}[name];
    const content = $('sheet-content');
    if (name === 'type') {
      content.innerHTML = `<p>Send text to the selected TV app. Select its search box first.</p><form id="text-form"><input type="text" id="text-input" autocomplete="off" autocorrect="off" spellcheck="false" placeholder="Type with your Android keyboard"><div class="sheet-row"><button type="submit">Send text</button><button type="button" data-action="enter">Enter</button><button type="button" data-action="delete">⌫ Delete</button></div></form><div class="feedback" id="sheet-feedback"></div>`;
      $('text-form').onsubmit = e => {
        e.preventDefault(); const value = $('text-input').value;
        if (!value) return;
        send('type', {text: value}).then(() => {$('sheet-feedback').textContent = 'Text sent'; $('text-input').value = '';})
          .catch(err => {$('sheet-feedback').textContent = err.message;});
      };
      $('text-input').focus();
    } else if (name === 'off') {
      content.innerHTML = `<p>Full shutdown keeps the iMac cool. To start it again, press the physical power button.</p><div class="sheet-row"><button id="off-confirm" class="danger">Turn off iMac</button><button id="off-cancel">Cancel</button></div>`;
      $('off-confirm').onclick = () => fire('poweroff', {confirm: true});
      $('off-cancel').onclick = closeSheet;
    } else {
      content.innerHTML = `<label>Turn off after</label><div class="sheet-row"><button data-minutes="15">15m</button><button data-minutes="30">30m</button><button data-minutes="60">1h</button><button data-minutes="90">1h 30m</button></div><p id="timer-status">No timer set</p><div class="sheet-row"><button data-minutes="0">Cancel timer</button></div><label>Playback</label><div id="subtitles"></div><div class="sheet-row"><button data-action="left">Rewind</button><button data-action="right">Forward</button></div><label>Recovery</label><div class="sheet-row"><button data-action="restart_app">Restart app</button><button data-action="recover">Recover TV Home</button></div><div class="sheet-row"><button id="restart-desktop">Restart desktop session</button></div><label>Phone access</label><div class="sheet-row"><button id="revoke-others">Disconnect other phones</button><button id="retire-link">Disable old link</button></div><div class="sheet-row"><button id="turn-off" class="danger">Turn off iMac</button></div><p id="heat-info"></p>`;
      $('turn-off').onclick = () => openSheet('off');
      $('restart-desktop').onclick = () => {
        if (confirm('Restart the TV desktop? This stops the current video.')) fire('restart_session', {confirm: true});
      };
      $('revoke-others').onclick = async () => {
        if (!confirm('Disconnect every other paired phone?')) return;
        try { await api('/api/revoke', {mode: 'other_phones'}); message('Other phones disconnected'); }
        catch (err) { message(err.message); }
      };
      $('retire-link').onclick = async () => {
        if (!confirm('Disable the old remote link? Phones using it must pair again.')) return;
        try { await api('/api/revoke', {mode: 'old_link'}); message('Old link disabled'); refresh(); }
        catch (err) { message(err.message); }
      };
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
    $('heat-info').textContent = state.thermal_warning ? 'The iMac is running hot. Stop playback and let it cool.' :
      `CPU: ${state.temperature == null ? 'unknown' : Math.round(state.temperature) + '°C'} · GPU: ${state.gpu_temperature == null ? 'unknown' : Math.round(state.gpu_temperature) + '°C'}`;
    $('retire-link').hidden = !state.old_link_active;
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

  function closeSheet() { sheet.hidden = true; sheetName = ''; }
  $('sheet-close').onclick = closeSheet;
  $('mode').onclick = () => setMode(mode === 'pointer' ? 'dpad' : 'pointer');
  // One request per drag: the readout follows the thumb, the TV changes on release.
  $('brightness').addEventListener('pointerdown', () => draggingBrightness = true);
  $('brightness').addEventListener('input', () => {
    draggingBrightness = true;
    $('brightness-level').textContent = `${$('brightness').value}%`;
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
    e.preventDefault(); buzz(8);
    const action = b.dataset.action;
    const params = action === 'volume' ? {direction: Number(b.dataset.direction)} : {};
    fire(action, params);
    b.setPointerCapture(e.pointerId);
    if (['up', 'down', 'left', 'right', 'volume'].includes(action)) {
      const delay = setTimeout(() => {
        const interval = setInterval(() => fire(action, params), 180);
        held.set(b, {interval});
      }, 450);
      held.set(b, {delay});
    }
  });
  function release(e) {
    const b = e.target.closest('[data-action]');
    if (!b) return;
    const h = held.get(b);
    if (h) { clearTimeout(h.delay); clearInterval(h.interval); held.delete(b); }
  }
  document.addEventListener('pointerup', release);
  document.addEventListener('pointercancel', release);
  document.addEventListener('click', e => {
    const b = e.target.closest('[data-action]');
    if (b && e.detail === 0) {
      fire(b.dataset.action, b.dataset.action === 'volume' ? {direction: Number(b.dataset.direction)} : {});
    }
  });

  const pad = $('pad');
  let gesture = null;
  let motion = {dx: 0, dy: 0, timer: null};
  function flushMotion() {
    motion.timer = null;
    if (!motion.dx && !motion.dy) return;
    const dx = Math.max(-300, Math.min(300, motion.dx));
    const dy = Math.max(-300, Math.min(300, motion.dy));
    motion.dx -= dx; motion.dy -= dy;
    fire('move', {dx, dy});
    if (motion.dx || motion.dy) motion.timer = setTimeout(flushMotion, 50);
  }
  pad.addEventListener('pointerdown', e => {
    if (!online) return;
    e.preventDefault(); pad.setPointerCapture(e.pointerId);
    gesture = {id: e.pointerId, x: e.clientX, y: e.clientY, moved: false, right: false};
    gesture.hold = setTimeout(() => {
      if (gesture && !gesture.moved) { gesture.right = true; fire('rightclick'); }
    }, 550);
  });
  pad.addEventListener('pointermove', e => {
    if (!gesture || e.pointerId !== gesture.id) return;
    const dx = e.clientX - gesture.x, dy = e.clientY - gesture.y;
    if (Math.abs(dx) + Math.abs(dy) > 7) { gesture.moved = true; clearTimeout(gesture.hold); }
    if (gesture.moved) {
      gesture.x = e.clientX; gesture.y = e.clientY;
      if (Math.abs(dx) + Math.abs(dy) > 1) {
        motion.dx += Math.round(dx * 2); motion.dy += Math.round(dy * 2);
        if (!motion.timer) motion.timer = setTimeout(flushMotion, 50);
      }
    }
  });
  function finish(cancelled) {
    if (!gesture) return;
    clearTimeout(gesture.hold);
    if (!cancelled && !gesture.moved && !gesture.right) fire('click');
    gesture = null;
  }
  pad.addEventListener('pointerup', () => finish(false));
  pad.addEventListener('pointercancel', () => finish(true));
  pad.addEventListener('wheel', e => {e.preventDefault(); fire(e.deltaY > 0 ? 'scrolldown' : 'scrollup');}, {passive: false});

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

  document.addEventListener('visibilitychange', () => { if (!document.hidden) refresh(); });
  checkAutoPair();
  refresh(); setInterval(refresh, 2000);
})();
