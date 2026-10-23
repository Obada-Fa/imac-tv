(() => {
  'use strict';
  const $ = id => document.getElementById(id);
  const remote = $('remote'), pair = $('pair'), sheet = $('sheet'), browser = $('browser'), tabbar = $('tabbar');
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
    $('connection').title = label; $('connection').setAttribute('aria-label', label);
    $('connection').classList.toggle('off', !value);
    document.querySelectorAll('#controls button, footer button, .apps button, #tabbar button, .pane button, #sheet-content .tsheet button').forEach(b => b.disabled = !value);
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
    state = s;
    pair.hidden = true; $('connection').hidden = false;
    connected(Boolean(s.gui), s.gui ? '● Connected' : '● TV starting');
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
    b.type = 'button'; b.textContent = label; b.classList.toggle('on', on); b.onclick = () => { buzz(8); fn(); };
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
    $('yt-range').value = pos; $('yt-pos').textContent = clock(pos); $('yt-dur').textContent = '-' + clock(len - pos);
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
    if (Date.now() > ytPending) { $('yt-play').textContent = yt.status === 'Playing' ? '⏸' : '▶'; }
    const mode = $('yt-mode'), known = !open && (yt.youtube_mode === 'music' || yt.youtube_mode === 'video');
    mode.hidden = !known;
    if (known) {
      mode.dataset.mode = yt.youtube_mode === 'music' ? 'video' : 'music';
      mode.textContent = yt.youtube_mode === 'music' ? '📺 Show video' : '🎵 Show visualizer';
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
    buzz(8);
    const play = $('yt-play'), before = play.textContent;
    if (b === play) { play.textContent = before === '⏸' ? '▶' : '⏸'; ytPending = Date.now() + 2500; }
    await ytSend({cmd: b.dataset.yt}, () => { if (b === play) play.textContent = before; });
    ytPending = 0;
  });
  [[$('yt-back'), -10], [$('yt-fwd'), 10]].forEach(([b, offset]) => b.onclick = () => {
    buzz(8);
    if (ytState && ytState.position != null) { ytState.position = Math.max(0, ytPosition() + offset); ytAt = Date.now(); ytTick(); }
    ytSend({cmd: 'seek', offset});
  });
  const yr = $('yt-range');
  yr.addEventListener('input', () => { ytDragging = true; $('yt-pos').textContent = clock(+yr.value); $('yt-dur').textContent = '-' + clock((ytState && ytState.length || 0) - yr.value); });
  yr.addEventListener('change', () => {
    const position = +yr.value;
    if (ytState) { ytState.position = position; ytAt = Date.now(); }
    ytSend({cmd: 'set_position', position}).then(() => { ytDragging = false; });
  });
  $('yt-mode').onclick = async () => {
    buzz(8);
    try { const r = await send('youtube_mode', {mode: $('yt-mode').dataset.mode}); if (r && r.result) renderYoutube(r.result.youtube_now, $('controls').dataset.view); }
    catch (e) {}
  };
  [$('yt-open'), $('yt-show')].forEach(b => b.onclick = () => { buzz(8); send('select', {app: 'youtube'}).catch(() => {}); });

  // Playback prompts: each button is one server action; the TV answers the same prompts with OK / Back.
  [['np-skip', 'skip_segment', {}], ['un-play', 'upnext', {choice: 'play'}], ['un-cancel', 'upnext', {choice: 'cancel'}],
   ['st-continue', 'still_watching', {choice: 'continue'}], ['st-stop', 'still_watching', {choice: 'stop'}]]
    .forEach(([id, action, params]) => $(id).onclick = () => { buzz(8); send(action, params).then(() => refresh()).catch(() => {}); });

  function renderView(s, pb, name) {
    const view = pb.active ? 'playing' : s.phase === 'buffering' ? 'buffering' : 'browse';
    const c = $('controls');
    if (c.dataset.view !== view) {
      c.dataset.view = view;
      if (view !== 'browse') { closeSearch(); showTab('remote'); }
      if (view !== 'playing') { subDelay = 0; sigs = {}; if (sheetName === 'subs' || sheetName === 'audio') closeSheet(); }
    }
    renderYoutube(s.youtube_now, view);
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
      $('np-play').textContent = pb.paused ? '▶' : '⏸';
      if (!draggingSeek) {
        $('seek').max = Math.max(1, pb.duration || 1);
        $('seek').value = pb.position || 0;
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
    $('search-open').querySelector('span').textContent = label;
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
    buzz(10);
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
  $('np-next').onclick = () => { buzz(10); fire('next_episode'); };
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
      buzz(8);
      if (['select', 'home', 'back', 'open_search', 'seek', 'subtitle', 'audio', 'play', 'youtube_media', 'youtube_mode', 'skip_segment', 'upnext', 'still_watching', 'restart_app', 'recover', 'timer', 'poweroff', 'brightness', 'screen', 'fullscreen'].includes(action)) refresh();
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
    sheetName = name; sheet.hidden = false; updateTabbar();
    $('sheet-title').textContent = {type: 'Type on TV', more: 'More', off: 'Turn off', torbox: 'Connect TorBox', subs: 'Subtitles', audio: 'Audio'}[name];
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
      content.innerHTML = `<label>Turn off after</label><div class="sheet-row"><button data-minutes="15">15m</button><button data-minutes="30">30m</button><button data-minutes="60">1h</button><button data-minutes="90">1h 30m</button></div><p id="timer-status">No timer set</p><div class="sheet-row"><button data-minutes="0">Cancel timer</button></div><label>Playback</label><div id="subtitles"></div><div class="sheet-row"><button data-action="left">Rewind</button><button data-action="right">Forward</button></div><label>Recovery</label><div class="sheet-row"><button data-action="restart_app">Restart app</button><button data-action="recover">Recover TV Home</button></div><div class="sheet-row"><button id="restart-desktop">Restart desktop session</button></div><label>TorBox</label><div id="torbox-row"></div><label>Phone access</label><div class="sheet-row"><button id="revoke-others">Disconnect other phones</button><button id="retire-link">Disable old link</button></div><div class="sheet-row"><button id="turn-off" class="danger">Turn off iMac</button></div><p id="heat-info"></p>`;
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
    renderTorboxRow();
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
      if (sheetName === 'torbox') closeSheet();
      if (done) done();
      refresh();
    };
  }

  function renderTorboxRow() {
    const row = $('torbox-row');
    if (!row || !state || !state.torbox) { if (row) row.replaceChildren(); return; }
    const on = !!state.torbox.configured;
    if (row.dataset.on === String(on)) return;
    row.dataset.on = String(on);
    const p = document.createElement('p'); p.textContent = on ? 'TorBox: connected' : 'TorBox: not connected';
    const r = document.createElement('div'); r.className = 'sheet-row';
    const b = document.createElement('button'); b.type = 'button';
    if (on) {
      b.textContent = 'Remove key';
      b.onclick = async () => {
        if (!confirm('Remove the TorBox key from the TV?')) return;
        try { await send('torbox_remove'); if (state) state.torbox = {configured: false}; renderTorboxRow(); }
        catch (e) { message(e.message); }
      };
    } else {
      b.textContent = 'Connect TorBox';
      b.onclick = () => { skipSet(false); openTorbox(null); };
    }
    r.append(b); row.replaceChildren(p, r);
  }

  function closeSheet() { sheet.hidden = true; sheetName = ''; titleToken++; updateTabbar(); }
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
        if (gesture && !gesture.moved && !gesture.multi) { gesture.right = true; buzz(15); quiet('rightclick', {}); }
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
    if (!cancelled && !gesture.moved && !gesture.right && !gesture.multi) { buzz(8); quiet('click', {}); }
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
    remote.hidden = tab !== 'remote';
    browser.hidden = tab === 'remote';
    for (const id of ['movie', 'series', 'search', 'settings']) $('tab-' + id).hidden = tab !== id;
    tabbar.querySelectorAll('button').forEach(b => b.classList.toggle('on', b.dataset.tab === tab));
    updateTabbar();
    if ((tab === 'movie' || tab === 'series') && online && !loaded[tab]) loadTab(tab);
    if (tab === 'settings' && online) loadSettings();
    else stopTrialPoll();
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
    buzz(8);
    const changed = b.dataset.tab !== tab;
    showTab(b.dataset.tab);
    if (changed && tvScreen[b.dataset.tab]) {
      try { Promise.resolve(send('show', {screen: tvScreen[b.dataset.tab]})).catch(() => {}); } catch (e) {}
    }
  });


  // ---- Settings tab: display modes (with a keep-or-revert trial) and the YouTube auto-close ----
  let setState = null, trialTimer = null, setBusy = false;
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
    if (!outs.length) {
      const p = document.createElement('p'); p.className = 'muted';
      p.textContent = setState.display_error || 'No connected screen found'; box.append(p);
    }
    for (const o of outs) {
      const card = document.createElement('div'); card.className = 'card setcard';
      const h = document.createElement('h4'); h.textContent = o.name;
      const now = document.createElement('div'); now.className = 'now';
      const cur = o.current;
      now.textContent = cur ? `Now ${sizeText(cur.mode)} at ${rateText(cur.rate)}` : 'Screen is off';
      card.append(h, now);
      const modes = (o.modes || []).filter(m => wideEnough(m.mode) && !/i$/.test(m.mode));
      const lbl1 = document.createElement('div'); lbl1.className = 'lbl'; lbl1.textContent = 'Resolution';
      const res = document.createElement('div'); res.className = 'chips';
      for (const m of modes) {
        const b = document.createElement('button'); b.type = 'button'; b.textContent = sizeText(m.mode);
        if (cur && cur.mode === m.mode) b.classList.add('cur', 'on');
        const t0 = (cur && cur.mode === m.mode && m.rates.includes(cur.rate)) ? cur.rate : m.rates[0];
        b.onclick = () => startTrial(o.name, m.mode, t0);
        res.append(b);
      }
      card.append(lbl1, res);
      const curMode = cur && modes.find(m => m.mode === cur.mode);
      if (curMode && curMode.rates.length > 1) {
        const lbl2 = document.createElement('div'); lbl2.className = 'lbl'; lbl2.textContent = 'Refresh rate';
        const rates = document.createElement('div'); rates.className = 'chips';
        for (const r of curMode.rates) {
          const b = document.createElement('button'); b.type = 'button'; b.textContent = rateText(r);
          if (cur.rate === r) b.classList.add('cur', 'on');
          b.onclick = () => startTrial(o.name, curMode.mode, r);
          rates.append(b);
        }
        card.append(lbl2, rates);
      }
      box.append(card);
    }
    renderBackground();
    const min = setState.youtube_close_after_paused_min || 0;
    document.querySelectorAll('#yt-close button').forEach(b => b.classList.toggle('on', Number(b.dataset.min) === min));
  }
  function renderBackground() {
    const bg = setState.background || {source: 'photos', retro: []};
    document.querySelectorAll('#bg-source button').forEach(b => b.classList.toggle('on', b.dataset.src === bg.source));
    const list = $('bg-list');
    list.replaceChildren();
    $('bg-retro').hidden = !bg.retro.length;
    const active = bg.retro.filter(r => r.on).length;
    $('bg-count').textContent = active + ' of ' + bg.retro.length + ' on';
    for (const r of bg.retro) {
      const b = document.createElement('button'); b.type = 'button'; b.className = 'bgitem';
      b.classList.toggle('on', r.on);
      b.setAttribute('role', 'switch'); b.setAttribute('aria-checked', String(r.on));
      const i = document.createElement('img'); i.alt = ''; i.loading = 'lazy'; i.src = '/api/retro/thumb/' + r.id; b.append(i);
      const tick = document.createElement('i'); tick.className = 'bgtick'; tick.textContent = r.on ? '✓' : ''; b.append(tick);
      const n = document.createElement('span'); n.textContent = r.name; b.append(n);
      const m = document.createElement('small'); m.textContent = r.width + ' × ' + r.height; b.append(m);
      b.onclick = () => settingsCall('retro_toggle', {id: r.id, enabled: !r.on});
      const d = document.createElement('button'); d.type = 'button'; d.className = 'bgdel'; d.textContent = '✕';
      d.setAttribute('aria-label', 'Remove ' + r.name);
      d.onclick = ev => { ev.stopPropagation(); if (confirm('Remove "' + r.name + '"?')) settingsCall('retro_remove', {id: r.id}); };
      b.append(d); list.append(b);
    }
    $('bg-note').textContent = bg.wanted === 'retro' && !bg.retro.length ? 'No retro pictures yet, so Focus is showing.' : '';
  }
  document.querySelectorAll('#bg-source button').forEach(b => b.onclick = () => {
    const bg = (setState && setState.background) || {};
    if (b.dataset.src === 'retro' && !(bg.retro || []).length) { $('bg-note').textContent = 'Add a picture first.'; return; }
    settingsCall('background_set', {source: b.dataset.src});
  });
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
  $('trial-keep').onclick = () => { buzz(8); settingsCall('display_keep'); };
  $('trial-revert').onclick = () => { buzz(8); settingsCall('display_revert'); };
  document.querySelectorAll('#yt-close button').forEach(b => b.onclick = () => {
    buzz(8); settingsCall('youtube_close', {minutes: Number(b.dataset.min)});
  });

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
    b.onclick = () => { buzz(8); openTitle(item); };
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
      fill(pane.querySelector('.grid'), list, more);
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
      buzz(12);
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
      el.append(btn(`▶ Resume from ${fmt(t.resume)}`, () => play({})), btn('Start over', () => play({start: 0})));
    } else {
      el.append(btn('▶ Play on TV', () => play({})));
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
  checkAutoPair();
  refresh(); setInterval(refresh, 2000);
})();
