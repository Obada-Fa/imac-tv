const {test} = require('node:test');
const assert = require('node:assert/strict');
const vm = require('node:vm');
const fs = require('node:fs');
const path = require('node:path');
const source = fs.readFileSync(path.join(__dirname, '../assets/tv-native.js'), 'utf8');

function fixture(supported = true) {
  const messages = [];
  class Element {
    constructor(tag) { this.tag = tag; this.children = []; this.attributes = {}; }
    append(...nodes) { this.children.push(...nodes); }
    setAttribute(key, value) { this.attributes[key] = value; }
    getAttribute(key) { return this.attributes[key]; }
    after(node) { this.sibling = node; }
  }
  class Media {
    toggle() { this.originalToggle = true; }
    update() { this.originalUpdate = true; }
    paint() { this.originalPaint = true; }
    disconnect() { this.originalDisconnect = true; }
    current(state) { return state.track; }
    release() { this.released = true; this.started = false; }
  }
  const access = new Element('div');
  const context = vm.createContext({
    window: {NexusTv: {postMessage: message => messages.push(JSON.parse(message))}},
    document: {getElementById: id => id === 'phone-access' ? access : null,
      querySelector: () => null,
      createElement: tag => new Element(tag)}, TVMediaControls: Media,
  });
  vm.runInContext(source, context);
  const native = context.window.NexusNative;
  native.update({supported, enabled: false, busy: false});
  const rows = access.sibling.children[1];
  return {context, native, access, rows, messages, Element, Media};
}

test('only adds native settings using the existing glass rows', () => {
  const f = fixture();
  assert.equal(f.rows.className, 'glass rows');
  assert.equal(f.rows.children.length, 4);
  const volume = f.rows.children[0];
  assert.equal(volume.className, 'row');
  assert.equal(volume.getAttribute('role'), 'switch');
  volume.onclick();
  assert.equal(f.messages.at(-1).action, 'start');
  assert.equal(volume.getAttribute('aria-checked'), 'false');
  f.native.update({supported: true, enabled: true});
  assert.equal(volume.getAttribute('aria-checked'), 'true');
  volume.onclick();
  assert.equal(f.messages.at(-1).action, 'stop');
  const mic = f.rows.children[1];
  mic.onclick(); assert.equal(f.messages.at(-1).action, 'mic_start');
  f.native.update({supported: true, mic: {supported: true, enabled: true}});
  mic.onclick(); assert.equal(f.messages.at(-1).action, 'mic_stop');
  const speaker = f.rows.children[2];
  speaker.onclick(); assert.equal(f.messages.at(-1).action, 'speaker_start');
  f.native.update({supported: true, speaker: {supported: true, enabled: true}});
  assert.equal(speaker.getAttribute('aria-checked'), 'true');
  speaker.onclick(); assert.equal(f.messages.at(-1).action, 'speaker_stop');
  f.rows.children[3].onclick(); assert.equal(f.messages.at(-1).action, 'address');
});

test('Android phone-media button uses native controls without a competing audio loop', () => {
  const f = fixture();
  const media = new f.Media(); media.button = new f.Element('button');
  media.update({gui: true, track: {title: 'Film'}});
  assert.equal(media.button.hidden, false);
  media.toggle();
  assert.equal(f.messages.at(-1).action, 'start');
  assert.equal(media.originalToggle, undefined);
  assert.equal(media.originalUpdate, undefined);
  f.native.update({supported: true, enabled: true});
  assert.equal(media.button.getAttribute('aria-pressed'), 'true');
  assert.match(media.button.textContent, /Turn off/);
  media.toggle(); assert.equal(f.messages.at(-1).action, 'stop');
});

test('iPhone retains existing browser media behavior and hides Android-only switch', () => {
  const f = fixture(false);
  assert.equal(f.rows.children[0].hidden, true);
  const media = new f.Media();
  media.toggle(); media.update({}); media.paint();
  assert.equal(media.originalToggle, true);
  assert.equal(media.originalUpdate, true);
  assert.equal(media.originalPaint, true);
});

test('plain browsers are untouched and reinjection does not duplicate controls', () => {
  vm.runInNewContext(source, {window: {}});
  const f = fixture();
  const section = f.access.sibling;
  vm.runInContext(source, f.context);
  assert.equal(f.access.sibling, section);
  assert.equal(f.messages.filter(message => message.action === 'status').length, 1);
});
