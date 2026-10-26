// Run with: node --test tvapp/tests/test_phone_media.js
const {test} = require('node:test');
const assert = require('node:assert/strict');
const {readFileSync} = require('node:fs');
const vm = require('node:vm');
const source = readFileSync(require('node:path').join(__dirname, '../web/media-session.js'), 'utf8');

const movie = (over = {}) => ({playback: {active: true, title: 'Movie', position: 50, duration: 100,
  paused: false, ...over}, media: {name: 'My movie', poster: 'https://example.com/poster.jpg'}});
const youtube = (over = {}) => ({playback: {active: false}, youtube_now: {status: 'Playing',
  title: 'Song', artist: 'Singer', art: 'https://i.ytimg.com/art.jpg', length: 200, position: 20, ...over}});
const idle = {playback: {active: false}, youtube_now: {status: 'Open'}};
const settled = () => new Promise(resolve => setImmediate(resolve));

function setup({unsupported = false, noPosition = false, rejectPlay = false, unsupportedAction} = {}) {
  const handlers = {}, calls = [], messages = [], positions = [], events = {};
  let state = idle, time = 0, blob, revoked = false;
  const session = {metadata: null, playbackState: 'none', setActionHandler(action, handler) {
    if (action === unsupportedAction) throw new TypeError('Unsupported');
    handlers[action] = handler;
  }};
  if (!noPosition) session.setPositionState = value => {
    if (value) assert.ok(value.playbackRate > 0, 'position rate must be positive, even when paused');
    positions.push(value);
  };
  class Audio {
    constructor() { this.src = ''; this.listeners = {}; this.paused = true; }
    addEventListener(name, fn) { this.listeners[name] = fn; }
    getAttribute() { return this.src; }
    removeAttribute() { this.src = ''; }
    async play() { if (rejectPlay) throw new Error('Gesture required'); this.paused = false; }
    pause() { this.paused = true; this.listeners.pause?.(); }
    load() {}
  }
  const button = {setAttribute(name, value) { this[name] = value; }};
  const context = vm.createContext({navigator: {mediaSession: unsupported ? undefined : session},
    MediaMetadata: class { constructor(value) { Object.assign(this, value); } }, Audio,
    URL: class extends URL {
      static createObjectURL(value) { blob = value; return 'blob:silence'; }
      static revokeObjectURL() { revoked = true; }
    }, Blob, location: {href: 'http://192.168.1.100:8765/'}, performance: {now: () => time},
    window: {addEventListener(name, fn) { events[name] = fn; }}});
  vm.runInContext(source + '\nglobalThis.Controls = TVMediaControls;', context);
  const controls = new context.Controls({button, message: s => messages.push(s),
    readState: async () => state,
    send: async (command, params) => calls.push([command, JSON.parse(JSON.stringify(params))])});
  return {controls, button, session, handlers, calls, messages, positions, events,
    setState(s) { state = s; controls.update(s); }, fresh(s) { state = s; },
    advance(ms) { time += ms; }, allowPlay() { rejectPlay = false; },
    get blob() { return blob; }, get revoked() { return revoked; }};
}

test('opt in exposes movie metadata and seek controls through a valid silent audio resource', async () => {
  const h = setup(); h.setState(movie());
  assert.equal(h.session.metadata, null);
  await h.button.onclick();
  assert.equal(h.session.metadata.title, 'My movie');
  assert.equal(h.session.metadata.artwork[0].src, 'https://example.com/poster.jpg');
  assert.equal(h.session.playbackState, 'playing');
  assert.equal(typeof h.handlers.seekto, 'function');
  assert.ok(!h.handlers.nexttrack);
  const wave = Buffer.from(await h.blob.arrayBuffer());
  assert.equal(wave.toString('ascii', 0, 4), 'RIFF');
  assert.equal(wave.readUInt32LE(40) / wave.readUInt32LE(28), 8);
  assert.ok(wave.subarray(44).every(sample => sample === 128));
  assert.equal(h.controls.audio.muted, undefined);
});

test('repeated notification pause is idempotent and play explicitly resumes mpv', async () => {
  const h = setup(); h.setState(movie()); await h.button.onclick();
  await h.controls.dispatch('pause'); await h.controls.dispatch('pause');
  await h.controls.dispatch('play');
  assert.deepEqual(h.calls, [['set_paused', {paused: true}], ['set_paused', {paused: true}], ['set_paused', {paused: false}]]);
  h.setState(movie({paused: true}));
  assert.equal(h.session.playbackState, 'paused');
  assert.equal(h.positions.at(-1).position, 50);
});

test('YouTube notification controls use MPRIS play, pause, previous, next, seek and stop', async () => {
  const h = setup(); h.setState(youtube()); await h.button.onclick();
  for (const action of ['pause', 'play', 'previoustrack', 'nexttrack']) await h.controls.dispatch(action);
  await h.controls.dispatch('seekto', {seekTime: 75});
  await h.controls.dispatch('stop');
  assert.deepEqual(h.calls, [
    ['youtube_media', {cmd: 'pause'}], ['youtube_media', {cmd: 'play'}],
    ['youtube_media', {cmd: 'previous'}], ['youtube_media', {cmd: 'next'}],
    ['youtube_media', {cmd: 'set_position', position: 75}], ['youtube_media', {cmd: 'stop'}]]);
});

test('relative seeks refresh stale background state and clamp to the TV timeline', async () => {
  const h = setup(); h.setState(movie()); await h.button.onclick();
  h.advance(120000); h.fresh(movie({position: 95}));
  await h.controls.dispatch('seekforward');
  assert.deepEqual(h.calls.at(-1), ['seek', {seconds: 100}]);
  h.fresh(movie({position: 5}));
  await h.controls.dispatch('seekbackward', {seekOffset: 60});
  assert.deepEqual(h.calls.at(-1), ['seek', {seconds: 0}]);
});

test('mpv takes priority, next episode is conditional, and disabled actions never send', async () => {
  const h = setup();
  h.setState({...movie(), youtube_now: youtube().youtube_now}); await h.button.onclick();
  assert.equal(h.session.metadata.title, 'My movie');
  await h.controls.dispatch('nexttrack'); assert.equal(h.calls.length, 0);
  h.setState({...movie(), media: {type: 'series', season: 2, episode: 3, next: {episode: 4}}});
  assert.equal(h.session.metadata.artist, 'S2 · E3');
  await h.controls.dispatch('nexttrack');
  assert.deepEqual(h.calls.at(-1), ['next_episode', {}]);
  h.setState(youtube({can_seek: false, can_next: false, can_prev: false, can_pause: false}));
  for (const action of ['seekto', 'nexttrack', 'previoustrack', 'pause', 'play']) assert.ok(!h.handlers[action]);
  assert.equal(h.positions.at(-1), undefined);
});

test('idle, disconnect, page exit and opt out clear metadata, actions and the loop', async () => {
  for (const end of ['idle', 'disconnect', 'pagehide', 'toggle']) {
    const h = setup(); h.setState(youtube()); await h.button.onclick();
    if (end === 'idle') h.setState(idle);
    else if (end === 'disconnect') h.controls.disconnect();
    else if (end === 'pagehide') h.events.pagehide();
    else await h.button.onclick();
    assert.equal(h.session.metadata, null);
    assert.equal(h.session.playbackState, 'none');
    assert.equal(h.controls.audio.src, ''); assert.ok(h.revoked);
    assert.ok(Object.values(h.handlers).every(handler => handler === null));
  }
});

test('a source switch or a stopped TV during a background action cannot control the wrong player', async () => {
  for (const fresh of [youtube(), idle, {...movie(), gui: false}]) {
    const h = setup(); h.setState(movie()); await h.button.onclick(); h.fresh(fresh);
    await h.controls.dispatch('pause'); assert.equal(h.calls.length, 0);
  }
  const h = setup(); h.setState(movie()); await h.button.onclick();
  let resolve;
  h.controls.readState = () => new Promise(r => { resolve = r; });
  const pending = h.controls.dispatch('pause');
  await h.button.onclick(); resolve(movie()); await pending;
  assert.equal(h.calls.length, 0);
});

test('autoplay denial is recoverable by a tap, and focus loss does not fight other phone apps', async () => {
  const h = setup({rejectPlay: true}); h.setState(movie()); await h.button.onclick();
  assert.equal(h.controls.started, false); assert.ok(h.controls.blocked);
  h.allowPlay(); await h.button.onclick(); assert.ok(h.controls.started);
  h.controls.audio.pause(); h.setState(movie()); await settled();
  assert.ok(h.controls.audio.paused); assert.ok(h.controls.blocked);
  await h.button.onclick(); assert.equal(h.controls.audio.paused, false);
});

test('unsupported browsers/actions/position reporting degrade without breaking other controls', async () => {
  const unsupported = setup({unsupported: true}); unsupported.setState(movie());
  assert.ok(unsupported.button.hidden);
  const h = setup({noPosition: true, unsupportedAction: 'seekto'});
  h.setState(movie()); await h.button.onclick();
  assert.ok(h.controls.started); assert.equal(h.handlers.seekto, undefined);
  await h.controls.dispatch('pause'); assert.equal(h.calls[0][0], 'set_paused');
});

test('connection failure releases stale controls and reports the failure', async () => {
  const h = setup(); h.setState(movie()); await h.button.onclick();
  h.controls.readState = async () => { throw new Error('TV disconnected'); };
  await h.controls.dispatch('pause');
  assert.equal(h.calls.length, 0); assert.equal(h.session.metadata, null);
  assert.equal(h.messages.at(-1), 'TV disconnected');
});
