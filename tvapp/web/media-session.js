/* The TV plays the real media. A silent HTML audio loop gives mobile browsers
 * a local session to expose in the notification shade / lock screen. */
class TVMediaControls {
  constructor({button, send, readState, message}) {
    this.button = button;
    this.send = send;
    this.readState = readState;
    this.message = message;
    this.session = navigator.mediaSession;
    this.supported = Boolean(this.session && typeof this.session.setActionHandler === 'function' &&
      typeof MediaMetadata === 'function');
    this.enabled = false;
    this.started = false;
    this.blocked = false;
    this.track = null;
    this.signature = '';
    this.handlers = new Set();
    this.audio = new Audio();
    this.audio.loop = true;
    this.audio.preload = 'none';
    // Do not auto-resume after the phone gives audio focus to another app.
    this.audio.addEventListener('pause', () => {
      if (this.started) { this.blocked = true; this.paint(); }
    });
    this.audio.addEventListener('error', () => {
      if (this.started) {
        this.release(); this.blocked = true; this.paint();
        this.message('Phone controls could not start. Tap to try again.');
      }
    });
    button.hidden = !this.supported;
    button.onclick = () => this.toggle();
    this.paint();
    window.addEventListener('pagehide', () => this.release());
  }

  current(state) {
    const pb = state?.playback, media = state?.media || {}, yt = state?.youtube_now;
    if (pb?.active) {
      const episode = media.type === 'series' && media.season != null && media.episode != null ?
        `S${media.season} · E${media.episode}` : '';
      return {kind: 'mpv', title: media.name || pb.title || 'Video', artist: episode || 'iMac TV',
        art: media.poster, paused: Boolean(pb.paused), duration: pb.duration, position: pb.position,
        seekable: pb.duration > 0, next: Boolean(media.next), previous: false, pausable: true};
    }
    if (yt && ['Playing', 'Paused'].includes(yt.status)) {
      return {kind: 'youtube', title: yt.title || 'YouTube', artist: yt.artist || 'YouTube on iMac TV',
        art: yt.art, paused: yt.status === 'Paused', duration: yt.length, position: yt.position,
        seekable: yt.can_seek !== false && yt.length > 0 && Number.isFinite(yt.position),
        next: yt.can_next !== false, previous: yt.can_prev !== false, pausable: yt.can_pause !== false};
    }
    return null;
  }

  paint() {
    this.button.setAttribute('aria-pressed', String(this.enabled));
    this.button.textContent = this.blocked ? 'Resume phone media controls' :
      this.enabled ? 'Phone media controls on · Turn off' : 'Show in phone media controls';
    this.button.title = 'Notification shade and lock screen. Keep this remote tab open.';
  }

  async toggle() {
    if (this.enabled && !this.blocked) {
      this.enabled = false; this.release(); this.paint(); return;
    }
    this.enabled = true;
    this.blocked = false;
    if (!this.track) {
      this.message('Phone media controls will appear when the TV plays.');
      this.paint(); return;
    }
    await this.start();
  }

  async start() {
    if (this.starting || !this.track || !this.enabled || this.blocked) return;
    if (!this.audio.getAttribute('src')) {
      // Eight seconds of unsigned 8-bit PCM silence; Chrome needs >=5 s for
      // notification audio focus. Muting the element suppresses the session.
      const pcm = new Uint8Array(8000 * 8).fill(128), header = new ArrayBuffer(44);
      const view = new DataView(header);
      const tag = (at, s) => [...s].forEach((c, i) => view.setUint8(at + i, c.charCodeAt(0)));
      tag(0, 'RIFF'); view.setUint32(4, 36 + pcm.length, true); tag(8, 'WAVE'); tag(12, 'fmt ');
      view.setUint32(16, 16, true); view.setUint16(20, 1, true); view.setUint16(22, 1, true);
      view.setUint32(24, 8000, true); view.setUint32(28, 8000, true);
      view.setUint16(32, 1, true); view.setUint16(34, 8, true);
      tag(36, 'data'); view.setUint32(40, pcm.length, true);
      this.url = URL.createObjectURL(new Blob([header, pcm], {type: 'audio/wav'}));
      this.audio.src = this.url;
    }
    this.starting = true;
    const url = this.url;
    try {
      await this.audio.play();
      if (this.url !== url || !this.enabled || !this.track) return;
      this.started = true;
      this.publish();
    } catch (error) {
      if (this.url !== url) return; // disabled or disconnected during play()
      this.blocked = true;
      this.message('Tap Resume phone media controls to enable notification controls.');
    } finally {
      this.starting = false;
      this.paint();
    }
  }

  update(state) {
    this.track = state?.gui === false ? null : this.current(state);
    this.at = performance.now();
    if (!this.track || !this.supported) { this.release(); return; }
    if (this.started) this.publish();
    else if (this.enabled && !this.blocked) this.start();
  }

  position() {
    const t = this.track;
    const elapsed = t.paused ? 0 : (performance.now() - this.at) / 1000;
    return Math.max(0, Math.min(t.duration, (Number(t.position) || 0) + elapsed));
  }

  handler(action, available) {
    if (available === this.handlers.has(action)) return;
    try {
      this.session.setActionHandler(action, available ? details => { this.dispatch(action, details); } : null);
      if (available) this.handlers.add(action); else this.handlers.delete(action);
    } catch (error) { /* An individual action may be unsupported. */ }
  }

  publish() {
    const t = this.track;
    if (!t || !this.started) return;
    const signature = JSON.stringify([t.title, t.artist, t.art]);
    if (signature !== this.signature) {
      let artwork = [];
      try {
        const url = new URL(t.art, location.href);
        if (t.art && ['http:', 'https:'].includes(url.protocol)) artwork = [{src: url.href}];
      } catch (error) {}
      this.session.metadata = new MediaMetadata({title: t.title, artist: t.artist, album: 'iMac TV', artwork});
      this.signature = signature;
    }
    this.session.playbackState = t.paused ? 'paused' : 'playing';
    this.handler('play', t.pausable);
    this.handler('pause', t.pausable);
    this.handler('stop', true);
    this.handler('seekbackward', t.seekable);
    this.handler('seekforward', t.seekable);
    this.handler('seekto', t.seekable);
    this.handler('nexttrack', t.next);
    this.handler('previoustrack', t.previous);
    try {
      if (t.seekable && Number.isFinite(t.duration)) {
        this.session.setPositionState({duration: t.duration, position: this.position(), playbackRate: 1});
      } else this.session.setPositionState();
    } catch (error) { /* Position reporting is optional. */ }
  }

  async dispatch(action, details = {}) {
    if (!this.enabled || !this.started || !this.track || !this.handlers.has(action)) return;
    const kind = this.track.kind;
    try {
      // Background timers can be throttled: get the TV's actual state before
      // choosing a target or calculating a relative seek. Never toggle pause.
      const state = await this.readState();
      this.update(state);
      const t = this.track;
      if (!this.enabled || !this.started || !t || t.kind !== kind || !this.handlers.has(action)) return;
      let command, params;
      if (action === 'play' || action === 'pause') {
        command = kind === 'youtube' ? 'youtube_media' : 'set_paused';
        params = kind === 'youtube' ? {cmd: action} : {paused: action === 'pause'};
      } else if (action === 'stop') {
        command = kind === 'youtube' ? 'youtube_media' : 'back';
        params = kind === 'youtube' ? {cmd: 'stop'} : {};
      } else if (action === 'nexttrack' || action === 'previoustrack') {
        command = kind === 'youtube' ? 'youtube_media' : 'next_episode';
        params = kind === 'youtube' ? {cmd: action === 'nexttrack' ? 'next' : 'previous'} : {};
      } else {
        const offset = Number.isFinite(details.seekOffset) && details.seekOffset > 0 ? details.seekOffset : 10;
        const seconds = action === 'seekto' ? details.seekTime : this.position() + (action === 'seekbackward' ? -offset : offset);
        if (!Number.isFinite(seconds)) return;
        const position = Math.max(0, Math.min(t.duration, 86400, seconds));
        command = kind === 'youtube' ? 'youtube_media' : 'seek';
        params = kind === 'youtube' ? {cmd: 'set_position', position} : {seconds: position};
      }
      await this.send(command, params);
      this.update(await this.readState());
    } catch (error) {
      this.disconnect();
      this.message(error.message || 'Reconnect to the TV to use phone media controls.');
    }
  }

  release() {
    if (!this.started && !this.url && !this.signature) return;
    this.started = false;
    this.audio.pause();
    this.audio.removeAttribute('src');
    this.audio.load();
    if (this.url) URL.revokeObjectURL(this.url);
    this.url = null;
    if (!this.supported) return;
    for (const action of [...this.handlers]) this.handler(action, false);
    this.session.metadata = null;
    this.session.playbackState = 'none';
    try { this.session.setPositionState(); } catch (error) {}
    this.signature = '';
  }

  disconnect() { this.track = null; this.release(); }
}
