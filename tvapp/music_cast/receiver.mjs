// Nexus TV music receiver: the YouTube and YouTube Music phone apps cast to "Nexus TV" and the TV plays
// the sound only, through mpv (yt-dlp picks the audio stream), so no video is ever fetched or decoded.
// yt-cast-receiver speaks YouTube's cast protocol; this file is the player it drives.
//
// Started and restarted by the phone server (tvapp/music_cast.py). Environment:
//   IMACTV_DATA_DIR    where the pairing store and music_cast_now.json live
//   IMACTV_MUSIC_SOCK  mpv's JSON IPC socket
//   IMACTV_MUSIC_PORT  DIAL port the phone apps discover (default 8766)
//   IMACTV_MUSIC_MPV   mpv binary (default "mpv"); IMACTV_YTDLP: yt-dlp for mpv's ytdl hook
//   IMACTV_JS_RUNTIME  passed to yt-dlp as --js-runtimes (set only when that yt-dlp knows the option)
//   IMACTV_MUSIC_NAME  name in the cast menu (default "Nexus TV")
import { spawn, execFile } from 'node:child_process';
import fs from 'node:fs';
import net from 'node:net';
import path from 'node:path';
import os from 'node:os';
import YouTubeCastReceiver, { Player, PLAYER_STATUSES, RESET_PLAYER_ON_DISCONNECT_POLICIES } from 'yt-cast-receiver';

const DATA = process.env.IMACTV_DATA_DIR || path.join(os.homedir(), '.local/share/imactv');
const SOCK = process.env.IMACTV_MUSIC_SOCK || path.join(DATA, 'music-mpv.sock');
const PORT = Number(process.env.IMACTV_MUSIC_PORT || 8766);
const MPV = process.env.IMACTV_MUSIC_MPV || 'mpv';
const YTDLP = process.env.IMACTV_YTDLP || '';
const JS_RUNTIME = process.env.IMACTV_JS_RUNTIME || '';   // e.g. node:/usr/bin/node, for YouTube's player challenge
const NAME = process.env.IMACTV_MUSIC_NAME || 'Nexus TV';
const NOW_FILE = path.join(DATA, 'music_cast_now.json');
const STORE_FILE = path.join(DATA, 'music_cast_store.json');
const VOLUME_FILE = path.join(DATA, 'volume_event.json');
const LOAD_TIMEOUT_MS = 45000;      // yt-dlp on a weak laptop can take a while to resolve a stream
const HEARTBEAT_MS = 5000;          // the TV treats a now-playing file older than ~20 s as gone

const log = (...a) => console.log(new Date().toISOString(), ...a);

function writeJson(file, value) {
  const tmp = `${file}.tmp`;
  try {
    fs.writeFileSync(tmp, JSON.stringify(value));
    fs.renameSync(tmp, file);
  } catch (e) {
    log('write failed', file, e.message);
  }
}

// ---- mpv over its JSON IPC socket
export class Mpv {
  constructor({ bin = MPV, sock = SOCK, ytdlp = YTDLP, jsRuntime = JS_RUNTIME, spawnFn = spawn } = {}) {
    this.bin = bin;
    this.jsRuntime = jsRuntime;
    this.sock = sock;
    this.ytdlp = ytdlp;
    this.spawnFn = spawnFn;
    this.proc = null;
    this.conn = null;
    this.nextId = 1;
    this.waiting = new Map();
    this.listeners = new Set();
    this.buf = '';
  }

  onEvent(fn) { this.listeners.add(fn); return () => this.listeners.delete(fn); }

  async ensure() {
    if (this.conn && !this.conn.destroyed) return;
    if (!this.proc || this.proc.exitCode !== null) {
      try { fs.unlinkSync(this.sock); } catch { /* not there */ }
      const args = ['--idle=yes', '--no-video', '--no-terminal', '--really-quiet', '--audio-display=no',
        '--ytdl-format=bestaudio/best', `--input-ipc-server=${this.sock}`, '--volume=100', '--title=Nexus TV music'];
      if (this.ytdlp) args.push(`--script-opts=ytdl_hook-ytdl_path=${this.ytdlp}`);
      if (this.jsRuntime) args.push(`--ytdl-raw-options=js-runtimes=${this.jsRuntime}`);
      this.proc = this.spawnFn(this.bin, args, { stdio: 'ignore' });
      this.proc.on('exit', (code) => { log('mpv exited', code); this.conn = null; });
    }
    for (let i = 0; i < 50; i++) {
      try {
        await this.#connect();
        await this.command(['observe_property', 1, 'pause']);
        return;
      } catch {
        await new Promise((r) => setTimeout(r, 100));
      }
    }
    throw new Error('mpv did not open its socket');
  }

  #connect() {
    return new Promise((resolve, reject) => {
      const c = net.createConnection(this.sock);
      c.once('connect', () => {
        this.conn = c;
        this.buf = '';
        c.on('data', (d) => this.#data(d));
        c.on('close', () => {
          if (this.conn === c) this.conn = null;
          for (const [, w] of this.waiting) w.reject(new Error('mpv closed'));
          this.waiting.clear();
        });
        resolve();
      });
      c.once('error', reject);
    });
  }

  #data(chunk) {
    this.buf += chunk.toString();
    let nl;
    while ((nl = this.buf.indexOf('\n')) >= 0) {
      const line = this.buf.slice(0, nl);
      this.buf = this.buf.slice(nl + 1);
      if (!line.trim()) continue;
      let msg;
      try { msg = JSON.parse(line); } catch { continue; }
      if (msg.request_id && this.waiting.has(msg.request_id)) {
        const w = this.waiting.get(msg.request_id);
        this.waiting.delete(msg.request_id);
        if (msg.error === 'success') w.resolve(msg.data);
        else w.reject(new Error(msg.error));
      } else if (msg.event) {
        for (const fn of this.listeners) fn(msg);
      }
    }
  }

  command(cmd) {
    if (!this.conn) return Promise.reject(new Error('mpv not connected'));
    const id = this.nextId++;
    return new Promise((resolve, reject) => {
      this.waiting.set(id, { resolve, reject });
      this.conn.write(JSON.stringify({ command: cmd, request_id: id }) + '\n');
      setTimeout(() => {
        if (this.waiting.delete(id)) reject(new Error('mpv timeout'));
      }, 5000);
    });
  }

  async get(name, fallback = 0) {
    try { return (await this.command(['get_property', name])) ?? fallback; } catch { return fallback; }
  }

  quit() {
    try { this.proc?.kill('SIGTERM'); } catch { /* gone */ }
  }
}

// ---- system volume (the one the TV and VacuumTube share)
function pactl(args) {
  return new Promise((resolve) => {
    execFile('pactl', args, { timeout: 2000 }, (err, stdout) => resolve(err ? '' : String(stdout)));
  });
}

async function systemVolume() {
  const vol = await pactl(['get-sink-volume', '@DEFAULT_SINK@']);
  const mute = await pactl(['get-sink-mute', '@DEFAULT_SINK@']);
  const m = vol.match(/(\d+)%/);
  return { level: m ? Number(m[1]) : 50, muted: /yes/.test(mute) };
}

// ---- the player yt-cast-receiver drives
const STATUS_NAMES = { [PLAYER_STATUSES.PLAYING]: 'Playing', [PLAYER_STATUSES.PAUSED]: 'Paused',
  [PLAYER_STATUSES.LOADING]: 'Loading' };

export class MpvPlayer extends Player {
  constructor(mpv, { nowFile = NOW_FILE, volumeFile = VOLUME_FILE, meta = fetchMeta, volume = null } = {}) {
    super();
    this.mpv = mpv;
    this.nowFile = nowFile;
    this.volumeFile = volumeFile;
    this.meta = meta;
    this.volumeIo = volume || { get: systemVolume, set: setSystemVolume };
    this.track = null;           // {id, title, artist, art}
    this.loadGen = 0;
    this.heartbeat = null;
    mpv.onEvent((e) => this.#event(e));
    this.on('state', () => this.#publish());
  }

  async doPlay(video, position) {
    await this.mpv.ensure();
    const gen = ++this.loadGen;
    this.track = { id: video.id, title: '', artist: '', art: `https://i.ytimg.com/vi/${video.id}/hqdefault.jpg` };
    this.meta(video.id).then((m) => {
      if (gen === this.loadGen && m) { Object.assign(this.track, m); this.#publish(); }
    }).catch(() => {});
    const loaded = new Promise((resolve) => {
      const timer = setTimeout(() => { off(); resolve(false); }, LOAD_TIMEOUT_MS);
      const off = this.mpv.onEvent((e) => {
        if (gen !== this.loadGen) { clearTimeout(timer); off(); resolve(false); return; }
        if (e.event === 'file-loaded') { clearTimeout(timer); off(); resolve(true); }
        else if (e.event === 'end-file' && e.reason === 'error') { clearTimeout(timer); off(); resolve(false); }
      });
    });
    await this.mpv.command(['set_property', 'pause', false]);
    await this.mpv.command(['loadfile', `https://www.youtube.com/watch?v=${video.id}`, 'replace']);
    const ok = await loaded;
    if (ok && position > 1) {
      try { await this.mpv.command(['seek', position, 'absolute']); } catch { /* starts from 0 */ }
    }
    if (!ok) log('could not play', video.id);
    return ok;
  }

  async doPause() { return this.#set('pause', true); }
  async doResume() { return this.#set('pause', false); }

  async doStop() {
    this.loadGen++;
    try { await this.mpv.command(['stop']); } catch { /* already stopped */ }
    this.track = null;
    return true;
  }

  async doSeek(position) {
    try { await this.mpv.command(['seek', position, 'absolute']); return true; } catch { return false; }
  }

  async doSetVolume(volume) {
    await this.volumeIo.set(volume);
    writeJson(this.volumeFile, { percent: volume.level, muted: Boolean(volume.muted), ts: Date.now() / 1000,
      over: false });
    return true;
  }

  async doGetVolume() { return this.volumeIo.get(); }
  async doGetPosition() { return Number(await this.mpv.get('time-pos', 0)) || 0; }
  async doGetDuration() { return Number(await this.mpv.get('duration', 0)) || 0; }

  async #set(name, value) {
    try { await this.mpv.command(['set_property', name, value]); return true; } catch { return false; }
  }

  #event(e) {
    if (e.event === 'end-file' && e.reason === 'eof' && this.status === PLAYER_STATUSES.PLAYING) {
      this.next().catch((err) => log('next failed', err.message));     // the queue (or autoplay) goes on
    } else if (e.event === 'property-change' && e.name === 'pause' && this.track) {
      // paused or resumed from the TV (Home's Now playing tile): tell the phone
      if (e.data === true && this.status === PLAYER_STATUSES.PLAYING) {
        this.notifyExternalStateChange(PLAYER_STATUSES.PAUSED);
      } else if (e.data === false && this.status === PLAYER_STATUSES.PAUSED) {
        this.notifyExternalStateChange(PLAYER_STATUSES.PLAYING);
      }
    }
  }

  async #publish() {
    const name = STATUS_NAMES[this.status];
    clearInterval(this.heartbeat);
    if (!name || !this.track) {
      writeJson(this.nowFile, { status: 'Stopped', updated: Date.now() / 1000 });
      return;
    }
    const write = async () => writeJson(this.nowFile, {
      status: name, ...this.track, position: await this.doGetPosition(), duration: await this.doGetDuration(),
      updated: Date.now() / 1000,
    });
    await write();
    this.heartbeat = setInterval(write, HEARTBEAT_MS);
  }
}

async function setSystemVolume(volume) {
  await pactl(['set-sink-volume', '@DEFAULT_SINK@', `${Math.max(0, Math.min(150, Math.round(volume.level)))}%`]);
  await pactl(['set-sink-mute', '@DEFAULT_SINK@', volume.muted ? '1' : '0']);
}

// Title and artist from YouTube's public oEmbed (no key, no account); YouTube Music's auto-generated
// channels are "Artist - Topic".
export async function fetchMeta(id) {
  const url = `https://www.youtube.com/oembed?format=json&url=${encodeURIComponent(`https://www.youtube.com/watch?v=${id}`)}`;
  const res = await fetch(url, { signal: AbortSignal.timeout(5000) });
  if (!res.ok) return null;
  const j = await res.json();
  return { title: String(j.title || ''), artist: String(j.author_name || '').replace(/ - Topic$/, ''),
    art: j.thumbnail_url || undefined };
}

// Pairing data survives restarts in the TV's data dir (the library's default writes into the cwd).
class FileStore {
  constructor(file) { this.file = file; }
  #read() { try { return JSON.parse(fs.readFileSync(this.file, 'utf8')); } catch { return {}; } }
  setLogger() {}
  async set(key, value) { const all = this.#read(); all[key] = value; writeJson(this.file, all); }
  async get(key) { const v = this.#read()[key]; return v === undefined ? null : v; }
  async clear() { writeJson(this.file, {}); }
}

async function main() {
  fs.mkdirSync(DATA, { recursive: true });
  const mpv = new Mpv();
  const player = new MpvPlayer(mpv);
  const receiver = new YouTubeCastReceiver(player, {
    device: { name: NAME, screenName: NAME, brand: 'Nexus', model: 'Nexus TV' },
    dial: { port: PORT },
    app: { resetPlayerOnDisconnectPolicy: RESET_PLAYER_ON_DISCONNECT_POLICIES.ALL_EXPLICITLY_DISCONNECTED },
    dataStore: new FileStore(STORE_FILE),
    logLevel: process.env.IMACTV_MUSIC_LOG || 'warn',
  });
  receiver.on('senderConnect', (s) => log('phone connected:', s?.device?.name || s?.name || 'sender'));
  receiver.on('senderDisconnect', (s) => log('phone disconnected:', s?.device?.name || s?.name || 'sender'));
  receiver.on('error', (e) => log('receiver error:', e?.message || e));
  receiver.on('terminate', (e) => { log('receiver terminated:', e?.message || e); process.exit(1); });
  const shutdown = async () => {
    try { await receiver.stop(); } catch { /* exiting anyway */ }
    writeJson(NOW_FILE, { status: 'Stopped', updated: Date.now() / 1000 });
    mpv.quit();
    process.exit(0);
  };
  process.on('SIGTERM', shutdown);
  process.on('SIGINT', shutdown);
  await receiver.start();
  writeJson(NOW_FILE, { status: 'Stopped', updated: Date.now() / 1000 });
  log(`Nexus TV music receiver on port ${PORT}`);
}

if (import.meta.url === `file://${process.argv[1]}`) {
  main().catch((e) => { log('failed to start:', e?.stack || e); process.exit(1); });
}
