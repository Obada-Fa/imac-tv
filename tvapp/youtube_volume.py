"""One volume for YouTube and the TV: the system volume.

VacuumTube applies its own saved volume to the video every 100 ms. Its page shares the JavaScript
world with that code, so through a local debugging port we make the video element ignore the level
and the mute and hide VacuumTube's volume bar; the player still reads back what it set, so it doesn't
fight this.

The phone's YouTube app sends its volume to the TV as a command, which VacuumTube takes as its own
level. A lasting connection to the page (`Link`) sees that command first and reports it, so the
system volume follows and the TV shows its volume island; the system level goes back the same way,
so VacuumTube, and the phone with it, shows the system volume too."""

import json
import logging
import math
import os
from pathlib import Path
import struct
import threading
import urllib.request

import browser

LOG = logging.getLogger("imactv.youtube_volume")
PORT = int(os.environ.get("IMACTV_YOUTUBE_PORT", "9334"))
FLAG = f"--remote-debugging-port={PORT}"
VIDEO_FLAG = "--enable-blink-features=AudioVideoTracks"   # lets the page switch the picture off (VIDEO below)
FLAGS = [FLAG, VIDEO_FLAG]
BINDING = "__imactvVolume"
_opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
LOCK_LEVEL = """(() => {
  // Each property is locked once per page; pages locked before mute was added still get it.
  const lock = (name, flag, forced) => {
    if (window[flag]) return false;
    window[flag] = true;
    const real = Object.getOwnPropertyDescriptor(HTMLMediaElement.prototype, name);
    const asked = new WeakMap();
    Object.defineProperty(HTMLMediaElement.prototype, name, {
      configurable: true, enumerable: real.enumerable,
      get() { return asked.has(this) ? asked.get(this) : real.get.call(this); },
      set(value) { asked.set(this, value); real.set.call(this, forced); },
    });
    document.querySelectorAll("video, audio").forEach((media) => {
      asked.set(media, real.get.call(media));
      real.set.call(media, forced);
    });
    return true;
  };
  const level = lock("volume", "__imactvVolumeLocked", 1);
  const mute = lock("muted", "__imactvMuteLocked", false);
  // A new execution context can arrive before <html>. Keep suppression independent
  // of the media locks and restore it if YouTube replaces the document's styles.
  const styleId = "__imactvVolumeStyle";
  const hideIndicator = () => {
    if (!document.documentElement || document.getElementById(styleId)) return;
    const style = document.createElement("style");
    style.id = styleId;
    style.textContent = '#vt-volume-indicator, [idomkey="lazy-mdx-volume"] { display: none !important; }';
    document.documentElement.appendChild(style);
  };
  if (!window.__imactvVolumeObserver) {
    window.__imactvVolumeObserver = new MutationObserver(hideIndicator);
    window.__imactvVolumeObserver.observe(document, {childList: true, subtree: true});
  }
  hideIndicator();
  return level || mute;
})()"""
VIDEO = """(() => {
  // Music behind Home: nobody sees the picture, so stop decoding it (the sound plays on). With
  // AudioVideoTracks, unselecting the video track makes Chromium skip video decoding entirely; a YouTube
  // started before that flag gets the smallest picture instead.
  if (window.__imactvVideo) return false;
  let want = true, timer = 0, lowered = false;
  const smallest = (on) => {
    const p = document.querySelector("#movie_player, .html5-video-player");
    if (!p || typeof p.setPlaybackQualityRange !== "function" || on === !lowered) return;
    try { on ? p.setPlaybackQualityRange("auto", "auto") : p.setPlaybackQualityRange("tiny", "tiny"); lowered = !on; }
    catch (e) {}
  };
  const apply = () => {
    let tracks = false;
    document.querySelectorAll("video").forEach((v) => {
      const list = v.videoTracks;
      if (!list) return;
      tracks = true;
      if (!want) { for (let i = 0; i < list.length; i++) if (list[i].selected) list[i].selected = false; }
      else if (list.length && !Array.prototype.some.call(list, (t) => t.selected)) list[0].selected = true;
    });
    if (!tracks) smallest(want);
  };
  window.__imactvVideo = (on) => {
    want = !!on;
    apply();
    clearInterval(timer);
    timer = want ? 0 : setInterval(apply, 1000);   // the next track starts with its picture on: switch it off too
    return want;
  };
  return true;
})()"""
H264 = """(() => {
  // Prefer H.264 over VP9/AV1: far cheaper to decode on a laptop CPU, and the one YouTube codec most GPUs
  // decode. YouTube picks from what the page says it can play.
  if (window.__imactvH264) return false;
  window.__imactvH264 = true;
  const heavy = (type) => /vp0?9|av01/i.test(String(type));
  const ms = window.MediaSource;
  if (ms && ms.isTypeSupported) {
    const real = ms.isTypeSupported.bind(ms);
    ms.isTypeSupported = (type) => !heavy(type) && real(type);
  }
  const canPlay = HTMLMediaElement.prototype.canPlayType;
  HTMLMediaElement.prototype.canPlayType = function (type) { return heavy(type) ? "" : canPlay.call(this, type); };
  const mc = navigator.mediaCapabilities;
  if (mc && mc.decodingInfo) {
    const info = mc.decodingInfo.bind(mc);
    mc.decodingInfo = (cfg) => heavy(cfg && cfg.video && cfg.video.contentType)
      ? Promise.resolve({supported: false, smooth: false, powerEfficient: false}) : info(cfg);
  }
  return true;
})()"""
HOOK = """(() => {
  // YouTube resolves the phone's volume as a command, which VacuumTube's own wrapper swallows: wrap
  // around that wrapper to report it, and send the TV's level back in through it.
  if (window.__imactvSync) return false;
  let instance = null, synced = false, state = null, pending = null, pushing = false;
  const send = (type, value) => {
    const action = {volumeControlType: "VOLUME_CONTROL_ACTION_TYPE_" + type};
    if (value !== undefined) action.volumeControlValue = value;
    pushing = true;
    try { instance.resolveCommand({volumeControlAction: action}); } finally { pushing = false; }
  };
  window.__imactvSync = (level, muted) => {
    if (!instance) { pending = [level, muted]; return; }
    if (!synced) return;   // without VacuumTube, YouTube would show its own volume bar for it
    if (!state || state.level !== level) send("SET_ABSOLUTE", level);
    if (!state || state.muted !== muted) send(muted ? "MUTE" : "UNMUTE");
    state = {level, muted};
  };
  const asked = (action) => {
    const type = action.volumeControlType;
    if (type === "VOLUME_CONTROL_ACTION_TYPE_MUTE") return {muted: true};
    if (type === "VOLUME_CONTROL_ACTION_TYPE_UNMUTE") return {muted: false};
    const level = Math.round(Number(action.volumeControlValue));
    if (type === "VOLUME_CONTROL_ACTION_TYPE_SET_ABSOLUTE" && Number.isFinite(level))
      return {level: Math.max(0, Math.min(100, level))};
    return null;
  };
  const hook = (target, vacuumTube) => {
    const inner = target.resolveCommand;
    target.resolveCommand = function (command) {
      const change = command && command.volumeControlAction && !pushing && asked(command.volumeControlAction);
      if (change) {
        state = Object.assign({}, state, change);
        if (typeof window.__imactvVolume === "function") window.__imactvVolume(JSON.stringify(change));
      }
      return inner.apply(this, arguments);
    };
    instance = target;
    synced = vacuumTube;
    if (pending) window.__imactvSync(...pending);
  };
  const started = Date.now();
  const timer = setInterval(() => {
    const waited = Date.now() - started;
    if (waited > 60000) return clearInterval(timer);
    for (const key in window._yttv || {}) {
      const target = window._yttv[key] && window._yttv[key].instance;
      if (!target || typeof target.resolveCommand !== "function") continue;
      const vacuumTube = String(target.resolveCommand).includes("inputModifiers");
      if (!vacuumTube && waited < 10000) return;   // VacuumTube wraps it within moments of loading
      clearInterval(timer);
      hook(target, vacuumTube);
      return;
    }
  }, 100);
  return true;
})()"""


def ensure_flag(home=None):
    """VacuumTube also reads switches from flags.txt, so the port is open however it gets started."""
    home = Path(home) if home else Path.home()
    for folder in (home / ".config/VacuumTube", home / ".var/app/rocks.shy.VacuumTube/config/VacuumTube"):
        path = folder / "flags.txt"
        try:
            if not folder.is_dir():
                continue
            text = path.read_text() if path.exists() else ""
            missing = [f for f in FLAGS if f.split("=")[0] not in text]
            if missing:
                path.write_text(" ".join([text.strip(), *missing]).strip() + "\n")
        except OSError:
            continue


def reachable(timeout=0.5):
    """Whether a running YouTube has the port open; one started by an older version doesn't."""
    try:
        with _opener.open(f"http://127.0.0.1:{PORT}/json/version", timeout=timeout):
            return True
    except (OSError, ValueError):
        return False


def _targets(timeout):
    with _opener.open(f"http://127.0.0.1:{PORT}/json/list", timeout=timeout) as response:
        return json.loads(response.read().decode("utf-8", "replace"))


def enforce(timeout=0.5):
    """Lock the level in every open YouTube page. Does nothing while YouTube isn't running."""
    try:
        for target in _targets(timeout):
            if target.get("type") in ("page", "iframe"):
                browser._page_command(target["id"], "Runtime.evaluate", {"expression": LOCK_LEVEL}, timeout, PORT)
    except (OSError, ValueError, KeyError):
        pass


def youtube_page(timeout=0.5):
    """The YouTube page target, or None while YouTube isn't running."""
    try:
        pages = [t for t in _targets(timeout) if t.get("type") == "page" and isinstance(t.get("id"), str)]
    except (OSError, ValueError, AttributeError):
        return None
    return next((p for p in pages if "youtube.com/tv" in str(p.get("url"))), pages[0] if pages else None)


def parse_change(payload):
    """{"level": 0-100} or {"muted": bool} from the page's report, or None."""
    try:
        data = json.loads(payload)
    except (TypeError, ValueError):
        return None
    if not isinstance(data, dict):
        return None
    if isinstance(data.get("muted"), bool):
        return {"muted": data["muted"]}
    level = data.get("level")
    if isinstance(level, (int, float)) and not isinstance(level, bool) and math.isfinite(level):
        return {"level": max(0, min(100, round(level)))}
    return None


class Frames:
    """Whole messages from a DevTools WebSocket, however the reads split them."""

    def __init__(self, sock, data=b""):
        self.sock, self.data, self.parts = sock, bytes(data), []

    def next(self):
        while True:
            frame = self._take()
            if frame is None:
                chunk = self.sock.recv(65536)
                if not chunk:
                    raise OSError("YouTube closed the connection")
                self.data += chunk
                continue
            fin, opcode, payload = frame
            if opcode == 0x8:
                raise OSError("YouTube closed the connection")
            if opcode in (0x0, 0x1, 0x2):   # pings and pongs are skipped
                self.parts.append(payload)
                if fin:
                    message, self.parts = b"".join(self.parts), []
                    return message

    def _take(self):
        data = self.data
        if len(data) < 2:
            return None
        size, start = data[1] & 0x7F, 2
        if size == 126:
            if len(data) < 4:
                return None
            size, start = struct.unpack(">H", data[2:4])[0], 4
        elif size == 127:
            if len(data) < 10:
                return None
            size, start = struct.unpack(">Q", data[2:10])[0], 10
        mask = data[start:start + 4] if data[1] & 0x80 else b""
        start += len(mask)
        if len(data) < start + size:
            return None
        payload, self.data = data[start:start + size], data[start + size:]
        if mask:
            payload = bytes(b ^ mask[i % 4] for i, b in enumerate(payload))
        return bool(data[0] & 0x80), data[0] & 0x0F, payload


class Link:
    """A lasting connection to the YouTube page: what the phone's YouTube app asks for comes in as it
    happens, and the system level goes out. Call `ensure` every few seconds; it reconnects after a
    restart, and the page is set up again after every reload."""

    def __init__(self, on_change, current):
        self.on_change = on_change   # gets {"level": 0-100} or {"muted": bool}
        self.current = current       # () -> (percent, muted) of the system volume
        self._lock = threading.Lock()
        self._sock = None
        self._page = None
        self._thread = None
        self._next_id = 0
        self.video_on = True         # False while YouTube only plays sound behind Home

    def set_video(self, on):
        """Decode YouTube's picture (True) or only its sound (False). Kept for the next page and reconnect."""
        self.video_on = bool(on)
        self._send("Runtime.evaluate", {"expression": f"window.__imactvVideo && window.__imactvVideo("
                                                      f"{json.dumps(self.video_on)})"})

    def ensure(self, timeout=0.5):
        if self._thread and self._thread.is_alive():
            return
        page = youtube_page(timeout)
        if page:
            self._thread = threading.Thread(target=self._run, args=(page["id"],), daemon=True,
                                            name="youtube-volume")
            self._thread.start()

    def push(self, percent, muted):
        """Have YouTube show the system level. Does nothing while it isn't connected."""
        if percent is None:
            return
        self._send("Runtime.evaluate", {"expression": f"window.__imactvSync && window.__imactvSync("
                                                      f"{int(percent)}, {json.dumps(bool(muted))})"})

    def _send(self, method, params=None):
        with self._lock:
            if self._sock is None:
                return False
            self._next_id += 1
            message = json.dumps({"id": self._next_id, "method": method, "params": params or {}})
            try:
                self._sock.sendall(browser._ws_frame(message.encode()))
                return True
            except OSError:
                return False

    def _run(self, page_id):
        try:
            sock, rest = browser._open_page(page_id, 2, PORT)
        except (OSError, ValueError):
            return
        sock.settimeout(None)   # it waits for YouTube's next message for as long as it runs
        frames = Frames(sock, rest)
        with self._lock:
            self._sock, self._page = sock, page_id
        try:
            self._send("Runtime.addBinding", {"name": BINDING})
            self._send("Page.addScriptToEvaluateOnNewDocument", {"source": H264})   # before YouTube's own scripts
            self._send("Runtime.enable")   # lists the page's contexts now, and each new one after a reload
            while True:
                message = frames.next()
                if b'"Runtime.bindingCalled"' in message or b'"Runtime.executionContextCreated"' in message:
                    self.handle(json.loads(message))
        except (OSError, ValueError):
            pass
        finally:
            with self._lock:
                self._sock = None
            sock.close()

    def handle(self, message):
        method, params = message.get("method"), message.get("params") or {}
        if method == "Runtime.bindingCalled" and params.get("name") == BINDING:
            change = parse_change(params.get("payload"))
            if change:
                try:
                    self.on_change(change)
                except Exception:
                    LOG.exception("Could not apply YouTube's volume")
        elif method == "Runtime.executionContextCreated":
            context = params.get("context") or {}
            aux = context.get("auxData") or {}
            if aux.get("isDefault") and aux.get("frameId") == self._page and "id" in context:
                self._send("Runtime.evaluate", {"expression": ";\n".join((LOCK_LEVEL, HOOK, H264, VIDEO,
                                                                         f"window.__imactvVideo({json.dumps(self.video_on)})")),
                                                "contextId": context["id"]})
                try:
                    self.push(*self.current())
                except Exception:
                    LOG.exception("Could not read the system volume for YouTube")
