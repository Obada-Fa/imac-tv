const {test} = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const source = fs.readFileSync(path.join(__dirname, '../youtube_volume.py'), 'utf8');
const lock = source.match(/LOCK_LEVEL = """([\s\S]*?)"""/)[1];

function page() {
  const styles = [];
  let changed;
  class Media {}
  for (const [name, initial] of [['volume', .4], ['muted', true]]) {
    const values = new WeakMap();
    Object.defineProperty(Media.prototype, name, {
      configurable: true,
      get() { return values.has(this) ? values.get(this) : initial; },
      set(value) { values.set(this, value); },
    });
  }
  const video = new Media();
  const realVolume = Object.getOwnPropertyDescriptor(Media.prototype, 'volume').get;
  const document = {
    documentElement:null,
    getElementById:id => styles.find(s => s.id === id),
    createElement:() => ({}),
    querySelectorAll:() => [video],
  };
  const window = {};
  const context = vm.createContext({
    document, window, HTMLMediaElement:Media,
    MutationObserver:class { constructor(callback) { changed = callback; } observe() {} },
  });
  return {
    styles, document, window, video,
    run:() => vm.runInContext(lock, context),
    changed:() => changed(),
    actualVolume:() => realVolume.call(video),
    mount:() => { document.documentElement = {appendChild:style => styles.push(style)}; changed(); },
  };
}

test('an early execution context installs suppression once the document appears', () => {
  const p = page();
  p.run();
  assert.equal(p.styles.length, 0);
  p.mount();
  assert.equal(p.styles.length, 1);
  assert.match(p.styles[0].textContent, /#vt-volume-indicator/);
  assert.match(p.styles[0].textContent, /lazy-mdx-volume/);
  p.video.volume = .2;
  assert.equal(p.video.volume, .2);
  assert.equal(p.actualVolume(), 1);
});

test('style recovery works after media locking and does not stack observers or styles', () => {
  const p = page();
  p.run(); p.mount();
  const observer = p.window.__imactvVolumeObserver;
  p.styles.length = 0;
  p.changed();
  p.run(); p.changed();
  assert.equal(p.styles.length, 1);
  assert.equal(p.window.__imactvVolumeObserver, observer);
  p.styles.length = 0;
  p.run();
  assert.equal(p.styles.length, 1);
});
