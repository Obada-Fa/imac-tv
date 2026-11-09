import fs from 'node:fs'; import path from 'node:path'; import assert from 'node:assert/strict';
import { fileURLToPath } from 'node:url';
import net from 'node:net';
import { Mpv, MpvPlayer, control, serveControl } from '../receiver.mjs';
// Player checks against a stand-in mpv (dev/fake_mpv.py). Needs node_modules: npm ci in music_cast/.
const HERE = path.dirname(fileURLToPath(import.meta.url));
import { PLAYER_STATUSES as P, DefaultLogger } from 'yt-cast-receiver';
const dir = fs.mkdtempSync('/tmp/nexus-music-');
process.env.FAKE_ARGS = path.join(dir, 'args.json'); const ARGS = process.env.FAKE_ARGS;
const nowFile = path.join(dir, 'now.json'), volFile = path.join(dir, 'vol.json');
const now = () => JSON.parse(fs.readFileSync(nowFile, 'utf8'));
const wait = (ms) => new Promise((r) => setTimeout(r, ms));
let vol = { level: 40, muted: false };
const mpv = new Mpv({ bin: path.join(HERE, 'fake_mpv.py'), sock: path.join(dir, 'mpv.sock'), ytdlp: '/x/yt-dlp', jsRuntime: 'node:/n/node' });
const player = new MpvPlayer(mpv, { nowFile, volumeFile: volFile, meta: async (id) => ({ title: 'Song ' + id, artist: 'Artist' }),
  volume: { get: async () => vol, set: async (v) => { vol = v; } } });
const lg = new DefaultLogger(); lg.setLevel?.('error'); player.setLogger(lg);
const video = (id) => ({ id, client: { key: 'YTMUSIC', name: 'YouTube Music' } });
try {
  assert.equal(await player.play(video('A1'), 0), true);
  assert.equal(player.status, P.PLAYING); await wait(150);
  let n = now(); console.log('playing:', n.status, n.title, n.artist, n.art);
  assert.equal(n.status, 'Playing'); assert.equal(n.title, 'Song A1'); assert.equal(n.can_next, false);
  const args = JSON.parse(fs.readFileSync(ARGS, 'utf8')); console.log('mpv args:', args.join(' '));
  assert.ok(args.includes('--no-video') && args.includes('--ytdl-format=bestaudio/best'));
  await player.pause(); assert.equal(player.status, P.PAUSED); await wait(100); assert.equal(now().status, 'Paused');
  await mpv.command(['set_property', 'pause', false]); await wait(200);          // resumed from the TV
  assert.equal(player.status, P.PLAYING, 'external resume reaches the phone'); assert.equal(now().status, 'Playing');
  await mpv.command(['set_property', 'pause', true]); await wait(200);
  assert.equal(player.status, P.PAUSED, 'external pause reaches the phone'); await player.resume();
  assert.equal(await player.seek(1.0), true); const p = await player.getPosition(); assert.ok(p >= 0.9 && p < 1.6, 'seek ' + p);
  await player.setVolume({ level: 65, muted: false }); assert.equal(vol.level, 65);
  assert.equal(JSON.parse(fs.readFileSync(volFile, 'utf8')).percent, 65);
  assert.equal(await player.play(video('bad1'), 0), false, 'unplayable song reports failure');
  assert.equal(await player.play(video('nojs1'), 0), true, 'plays without a JS runtime');
  assert.deepEqual(mpv.ytdlModes[0], {}, 'no JS runtime leads');
  assert.equal(await player.play(video('jsonly1'), 0), true, 'falls back to the JS runtime');
  assert.equal(mpv.ytdlModes[0]['js-runtimes'], 'node:/n/node', 'the mode that worked leads');
  assert.equal(await player.play(video('nojs2'), 0), true, 'falls back the other way too');
  assert.ok(!args.some((a) => a.startsWith('--ytdl-raw-options')), 'no JS runtime on the command line');
  // the TV / phone remote control socket
  assert.equal(await player.play(video('C3'), 0), true);
  assert.equal(await control(player, { cmd: 'play_pause' }), true); assert.equal(player.status, P.PAUSED);
  assert.equal(await control(player, { cmd: 'play_pause' }), true); assert.equal(player.status, P.PLAYING);
  assert.equal(await control(player, { cmd: 'nonsense' }), false);
  const ctl = path.join(dir, 'ctl.sock'), srv = serveControl(player, ctl); await wait(50);
  const ask = (msg) => new Promise((res) => { const c = net.createConnection(ctl); let b = '';
    c.on('connect', () => c.write(JSON.stringify(msg) + '\n')); c.on('data', (d) => { b += d; }); c.on('end', () => res(JSON.parse(b))); });
  assert.equal((await ask({ cmd: 'pause' })).ok, true); assert.equal(player.status, P.PAUSED);
  assert.equal((await ask({ cmd: 'pause' })).ok, true, 'pausing a paused song is fine');
  assert.equal((await ask({ cmd: 'resume' })).ok, true); assert.equal(player.status, P.PLAYING);
  assert.equal((await ask({ cmd: 'stop' })).ok, true); await wait(100);
  assert.equal(now().status, 'Stopped', 'stop from the TV'); srv.close();
  console.log('control socket ok');
  assert.equal(await player.play(video('B2'), 0), true);
  await wait(2600);                                                            // the song ends: queue empty -> stop
  console.log('after end:', player.status, now().status);
  assert.equal(now().status, 'Stopped');
  console.log('ALL PLAYER CHECKS PASSED');
} finally { mpv.quit(); fs.rmSync(dir, { recursive: true, force: true }); }
process.exit(0);
