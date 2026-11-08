// Only app-specific controls. The complete remote UI is served by the TV itself.
// Use its existing row/switch classes; no second theme or remote implementation.
(() => {
  if (!window.NexusTv || window.NexusNative) return;
  const access = document.getElementById('phone-access');
  if (!access) return;
  const make = (tag, cls, text) => {
    const node = document.createElement(tag); node.className = cls || '';
    if (text) node.textContent = text;
    return node;
  };
  const send = action => window.NexusTv.postMessage(JSON.stringify({action}));
  let native = {supported: false, enabled: false, busy: false};
  let media = null;
  // Use the existing phone-media button for the Android session as well. A
  // WebView silent-audio session would compete with the remote volume provider.
  if (typeof TVMediaControls === 'function') {
    const original = {toggle: TVMediaControls.prototype.toggle,
      update: TVMediaControls.prototype.update, paint: TVMediaControls.prototype.paint,
      disconnect: TVMediaControls.prototype.disconnect};
    TVMediaControls.prototype.toggle = function () {
      if (!native.supported) return original.toggle.call(this);
      send(native.enabled ? 'stop' : 'start');
    };
    TVMediaControls.prototype.update = function (state) {
      if (!native.supported) return original.update.call(this, state);
      if (this.started) { this.enabled = false; this.release(); }
      media = this;
      this.track = state?.gui === false ? null : this.current(state);
      this.paint();
    };
    TVMediaControls.prototype.paint = function () {
      if (!native.supported) return original.paint.call(this);
      this.button.hidden = !this.track;
      this.button.disabled = !!native.busy;
      this.button.setAttribute('aria-pressed', String(!!native.enabled));
      this.button.textContent = native.enabled ? 'Phone media controls on · Turn off' : 'Show in phone media controls';
    };
    TVMediaControls.prototype.disconnect = function () {
      if (!native.supported) return original.disconnect.call(this);
      this.track = null; this.paint();
    };
  }
  const section = make('section'); section.id = 'nexus-app-controls';
  section.append(make('h3', '', 'Phone app'));
  const rows = make('div', 'glass rows'); section.append(rows);
  const rowText = (title, subtitle) => {
    const text = make('span', 'rt', title); text.append(make('small', '', subtitle));
    return text;
  };
  const volume = make('button', 'row'); volume.type = 'button';
  volume.setAttribute('role', 'switch');
  volume.append(rowText('TV volume buttons', 'Also works outside the app and on the lock screen while the TV is playing.'));
  const value = make('span', 'rv', 'Off'); volume.append(value, make('i', 'htog'));
  volume.onclick = () => send(volume.getAttribute('aria-checked') === 'true' ? 'stop' : 'start');
  rows.append(volume);
  const mic = make('button', 'row'); mic.type = 'button';
  mic.setAttribute('role', 'switch');
  mic.append(rowText('Phone microphone', 'Makes this phone the TV\'s default microphone while connected.'));
  const micValue = make('span', 'rv', 'Off'); mic.append(micValue, make('i', 'htog'));
  mic.onclick = () => send(mic.getAttribute('aria-checked') === 'true' ? 'mic_stop' : 'mic_start');
  rows.append(mic);
  const speaker = make('button', 'row'); speaker.type = 'button';
  speaker.setAttribute('role', 'switch');
  speaker.append(rowText('Phone speaker', 'Plays the TV\'s sound on this phone and makes it the default output, so Discord is heard here.'));
  const speakerValue = make('span', 'rv', 'Off'); speaker.append(speakerValue, make('i', 'htog'));
  speaker.onclick = () => send(speaker.getAttribute('aria-checked') === 'true' ? 'speaker_stop' : 'speaker_start');
  rows.append(speaker);
  const error = make('p', 'note'); error.setAttribute('role', 'status');
  for (const [title, subtitle, action] of [
    ['Switch TV', 'Connect this phone to another TV', 'address'],
  ]) {
    const button = make('button', 'row'); button.type = 'button';
    button.append(rowText(title, subtitle), make('i', 'chev'));
    button.onclick = () => send(action); rows.append(button);
  }
  section.append(error); access.after(section);
  // TV connection loss disables TV-action buttons. Host navigation must still
  // work, especially on iPhone where there is no Android Back button.
  const hostButtons = Array.from(rows.children);
  const keepHostControls = () => {
    for (const button of hostButtons) {
      const disabled = (button === volume && !!native.busy) || (button === mic && !!native.mic?.busy) ||
        (button === speaker && !!native.speaker?.busy);
      if (button.disabled !== disabled) button.disabled = disabled;
    }
  };
  if (typeof MutationObserver === 'function') {
    new MutationObserver(keepHostControls).observe(rows,
      {attributes: true, attributeFilter: ['disabled'], subtree: true});
  }
  const pairing = document.querySelector('#pair .pair-card');
  if (pairing) {
    const footer = make('p', 'meta');
    for (const [title, action] of [['Switch TV', 'address']]) {
      const button = make('button', 'quiet', title); button.type = 'button';
      button.onclick = () => send(action); footer.append(button);
    }
    pairing.append(footer);
  }
  window.NexusNative = {
    update(state) {
      native = state;
      keepHostControls();
      if (media) media.paint();
      volume.hidden = !state.supported;
      volume.disabled = !!state.busy;
      volume.setAttribute('aria-checked', String(!!state.enabled));
      value.textContent = state.busy ? 'Connecting…' : state.enabled ? 'On' : 'Off';
      const m = state.mic || {};
      mic.hidden = !m.supported;
      mic.disabled = !!m.busy;
      mic.setAttribute('aria-checked', String(!!m.enabled));
      micValue.textContent = m.busy ? 'Connecting…' : m.enabled ? 'On' : 'Off';
      const sp = state.speaker || {};
      speaker.hidden = !sp.supported;
      speaker.disabled = !!sp.busy;
      speaker.setAttribute('aria-checked', String(!!sp.enabled));
      speakerValue.textContent = sp.busy ? 'Connecting…' : sp.enabled ? 'On' : 'Off';
      const message = state.error || m.error || sp.error || '';
      error.textContent = message;
      error.hidden = !message;
    },
  };
  send('status');
})();
