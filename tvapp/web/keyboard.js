/* The phone IME edits an invisible field. Only edit operations and key chords go to the TV. */
const TV_SENTINEL = '\u200b';
const tvSegmenter = typeof Intl.Segmenter === 'function' ? new Intl.Segmenter(undefined, {granularity: 'grapheme'}) : null;
function tvEdit(previous, value) {
  const split = text => tvSegmenter ? Array.from(tvSegmenter.segment(text), part => part.segment) : Array.from(text);
  const before = split(previous), after = split(value);
  let n = 0;
  while (n < before.length && n < after.length && before[n] === after[n]) n++;
  let erase = before.length - n;
  const text = after.slice(n).join('');
  if (!n && before[0] === TV_SENTINEL && (erase > 1 || text)) erase--;
  return {erase, text};
}

class TVKeyboard {
  constructor(send) {
    this.send = send;
    this.dock = document.getElementById('ime-dock');
    this.input = document.getElementById('ime-field');
    this.tray = document.getElementById('ime-tray');
    this.modifiers = new Set();
    this.queue = Promise.resolve();
    this.session = null;
    this.observed = null;
    this.dock.onsubmit = e => { e.preventDefault(); this.key('Return'); };
    this.input.addEventListener('blur', () => this.close());
    this.input.addEventListener('input', e => { if (!e.isComposing) this.edit(); });
    this.input.addEventListener('compositionend', () => this.edit());
    this.input.addEventListener('keydown', e => {
      if (!this.session || e.isComposing || e.keyCode === 229) return;
      const keys = {Enter:'Return', Escape:'Escape', Tab:'Tab', ArrowLeft:'Left', ArrowRight:'Right',
        ArrowUp:'Up', ArrowDown:'Down', Delete:'Delete'};
      if (keys[e.key]) { e.preventDefault(); this.key(keys[e.key]); }
    });
    this.input.addEventListener('beforeinput', e => {
      if (e.inputType === 'insertLineBreak' || e.inputType === 'insertParagraph') {
        e.preventDefault(); this.key('Return');
      }
    });
    this.input.addEventListener('select', () => {
      const end = this.input.value.length;
      if (this.input.selectionStart !== end || this.input.selectionEnd !== end) this.input.setSelectionRange(end, end);
    });
    const make = (row, label, handler, aria = label) => {
      const b = document.createElement('button');
      b.type = 'button'; b.textContent = label; b.setAttribute('aria-label', aria);
      b.onclick = handler; row.append(b); return b;
    };
    this.dock.addEventListener('pointerdown', e => { if (e.target.closest('button')) e.preventDefault(); });
    const row = document.getElementById('ime-row');
    for (const [label, mod] of [['⊞','super'],['Ctrl','ctrl'],['Alt','alt'],['Shift','shift']]) {
      const b = make(row, label, () => {
        if (mod === 'super' && this.modifiers.has(mod)) {
          this.clearModifiers(); this.key('Super_L'); return;
        }
        if (this.modifiers.has(mod)) this.modifiers.delete(mod); else this.modifiers.add(mod);
        b.setAttribute('aria-pressed', String(this.modifiers.has(mod)));
      }, mod === 'super' ? 'Windows key' : label);
      b.dataset.mod = mod; b.setAttribute('aria-pressed','false');
    }
    make(row, 'Esc', () => this.key('Escape'));
    make(row, 'Tab', () => this.key('Tab'));
    const arrows = make(row, '✥', () => {}, 'Slide for arrow keys');
    let last = null;
    arrows.onpointerdown = e => { arrows.setPointerCapture(e.pointerId); last = [e.clientX, e.clientY]; };
    arrows.onpointermove = e => {
      if (!last) return;
      const dx = e.clientX - last[0], dy = e.clientY - last[1], horizontal = Math.abs(dx) >= Math.abs(dy);
      const delta = horizontal ? dx : dy;
      for (let n = Math.floor(Math.abs(delta) / 24); n > 0; n--) {
        this.key(horizontal ? (delta < 0 ? 'Left' : 'Right') : (delta < 0 ? 'Up' : 'Down'));
        last[horizontal ? 0 : 1] += Math.sign(delta) * 24;
      }
    };
    arrows.onlostpointercapture = () => { last = null; };
    arrows.style.touchAction = 'none';
    this.more = make(row, '•••', () => {
      this.tray.hidden = !this.tray.hidden; this.more.setAttribute('aria-expanded', String(!this.tray.hidden));
    }, 'More keys');
    make(row, '⌄', () => this.close(), 'Hide keyboard');
    const keys = {'←':'Left','↑':'Up','↓':'Down','→':'Right',Home:'Home',End:'End',PgUp:'Prior',PgDn:'Next',Del:'Delete'};
    for (let i = 1; i <= 12; i++) keys['F'+i] = 'F'+i;
    for (const [label, key] of Object.entries(keys)) make(this.tray, label, () => this.key(key), key);
    const resize = () => {
      if (!this.session) return;
      const vp = window.visualViewport;
      const height = vp ? vp.height : window.innerHeight;
      const inset = vp ? Math.max(0, innerHeight - vp.height - vp.offsetTop) : 0;
      this.dock.style.bottom = inset + 'px';
      if (this.height - height > 100) this.imeVisible = true;
      else if (this.imeVisible && height >= this.height - 40) this.close();
    };
    window.visualViewport?.addEventListener('resize', resize);
    window.addEventListener('resize', resize);
  }

  reset() {
    this.previous = this.input.value = TV_SENTINEL;
    this.input.setSelectionRange(1, 1);
  }
  clearModifiers() {
    this.modifiers.clear();
    this.dock.querySelectorAll('[data-mod]').forEach(b => b.setAttribute('aria-pressed','false'));
  }
  open(app, focus, pending = false) {
    this.close();
    this.dismissedSearch = null;
    const session = {app, focus, pending, buffered: []};
    this.session = session;
    this.height = window.visualViewport?.height || window.innerHeight;
    this.imeVisible = false;
    this.dock.style.bottom = '0px';
    this.dock.hidden = false;
    this.reset();
    this.input.focus({preventScroll:true});
    if (pending) session.timeout = setTimeout(() => { if (this.session === session) this.close(); }, 5000);
    return session;
  }
  close() {
    const session = this.session;
    if (!session) return;
    clearTimeout(session.timeout);
    if (session.pending) this.dismissedSearch = {app:session.app, until:Date.now()+5000};
    this.session = null;
    this.input.blur();
    this.reset(); this.clearModifiers();
    this.tray.hidden = true; this.more.setAttribute('aria-expanded','false');
    this.dock.hidden = true;
  }
  observe(app, focus, eligible) {
    const token = focus ? app + '/' + focus : null;
    const changed = token !== this.observed;
    this.observed = token;
    const s = this.session;
    if (!eligible) { this.close(); return; }
    if (s?.pending) {
      if (s.ready && app === s.app && focus && (app !== 'home' || focus.startsWith('search:'))) {
        clearTimeout(s.timeout);
        s.pending = false; s.focus = focus;
        for (const op of s.buffered) this.dispatch(op);
        s.buffered = [];
      }
      return;
    }
    if (s && (s.app !== app || (s.focus && s.focus !== focus))) this.close();
    if (!changed || !focus) return;
    if (this.dismissedSearch?.app === app && Date.now() < this.dismissedSearch.until) {
      this.dismissedSearch = null; return;
    }
    if (this.session && !this.session.focus) { this.session.focus = focus; this.reset(); }
    else this.open(app, focus);
  }
  dispatch(op) {
    const s = this.session;
    if (!s) return;
    if (s.pending) { s.buffered.push(op); return; }
    const params = {app:s.app, focus:s.focus, ...op};
    this.queue = this.queue.then(async () => {
      if (s.failed) return;
      const response = await this.send('keyboard_input', params);
      if (!response?.result?.applied) throw Error('Focus changed');
    }).catch(() => { s.failed = true; if (s === this.session) this.close(); });
  }
  key(key) {
    this.dispatch({key, mods:[...this.modifiers]});
    this.clearModifiers(); this.reset();
  }
  edit() {
    if (!this.session) return;
    const value = this.input.value;
    const {erase, text} = tvEdit(this.previous, value);
    this.previous = value;
    if (!erase && !text) return;
    const symbols = {' ':'space','-':'minus','=':'equal','[':'bracketleft',']':'bracketright',
      '\\':'backslash',';':'semicolon',"'":'apostrophe',',':'comma','.':'period','/':'slash','`':'grave'};
    const key = /^[a-z0-9]$/i.test(text) ? text.toLowerCase() : symbols[text];
    if (this.modifiers.size && key && !erase) { this.key(key); return; }
    // Bound each command even for a long paste.
    const chars = Array.from(text);
    let remaining = erase;
    while (remaining > 500) { this.dispatch({erase:500}); remaining -= 500; }
    this.dispatch({erase:remaining, text:chars.slice(0,500).join('')});
    for (let i = 500; i < chars.length; i += 500) this.dispatch({text:chars.slice(i,i+500).join('')});
    this.clearModifiers();
    if (!value.startsWith(TV_SENTINEL) || (value.length > 256 && value.endsWith(' '))) this.reset();
  }
}
