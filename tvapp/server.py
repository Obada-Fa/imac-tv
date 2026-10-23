#!/usr/bin/env python3
"""iMac TV controller. The HTTP process deliberately never opens X11."""
import hashlib
import hmac
import json
import logging
import os
from pathlib import Path
import secrets
import shutil
import socket
import re
import subprocess
import sys
import threading
import time
from http import HTTPStatus
from http.cookies import SimpleCookie
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlsplit

BASE = Path(__file__).resolve().parent
if str(BASE) not in sys.path:
    sys.path.insert(0, str(BASE))
from media import addons, config as media_config, engine as media_engine, history, player, streams as media_streams, subs as media_subs
DATA = Path(os.environ.get("IMACTV_DATA_DIR", str(Path.home() / ".local/share/imactv")))
DATA.mkdir(mode=0o700, parents=True, exist_ok=True)
WEB = BASE / "web"
PORT = int(os.environ.get("IMACTV_PORT", "8765"))
BOOT_ID = Path("/proc/sys/kernel/random/boot_id").read_text().strip()
DISPLAY_ENV = dict(os.environ, DISPLAY=":0", XAUTHORITY=str(Path.home() / ".Xauthority"),
                   DBUS_SESSION_BUS_ADDRESS=f"unix:path=/run/user/{os.getuid()}/bus")
def resolve_app_command(app):
    if app == "youtube":
        bin_path = shutil.which("vacuumtube") or shutil.which("VacuumTube")
        if bin_path:
            return [bin_path]
        opt_bin = Path("/opt/VacuumTube/vacuumtube")
        if opt_bin.is_file() and os.access(opt_bin, os.X_OK):
            return [str(opt_bin)]
        return ["flatpak", "run", "rocks.shy.VacuumTube"]
    return APPS.get(app, (app, [app]))[1]


APPS = {
    "youtube": ("VacuumTube", resolve_app_command("youtube")),
}
# The backlight node is root-only, so writes go through a fixed root helper that
# setup-privileges.sh installs alongside the sudo rule.
BRIGHTNESS_HELPER = "/usr/local/sbin/imactv-brightness"
LOG = logging.getLogger("imactv")


def command(argv, *, timeout=3, env=None, check=True):
    result = subprocess.run(argv, capture_output=True, text=True, timeout=timeout,
                            env=env or DISPLAY_ENV)
    if check and result.returncode:
        raise RuntimeError((result.stderr or result.stdout or "command failed").strip()[:300])
    return result.stdout.strip()


def windows():
    try:
        lines = command(["wmctrl", "-lpGx"], timeout=2).splitlines()
    except Exception:
        return []
    found = []
    for line in lines:
        part = line.split(None, 9)
        if len(part) >= 10:
            found.append({"id": part[0], "class": part[7].lower(), "title": part[9]})
    return found


def window_for(app):
    if app == "home":
        return next((w for w in windows() if w["title"] == "iMac TV"), None)
    if app == "youtube":
        return next((w for w in windows() if any(k in w["class"] or k in w["title"].lower() for k in ("vacuumtube", "rocks.shy.vacuumtube", "youtube"))), None)
    if app == "player":
        return next((w for w in windows() if w["class"].startswith("mpv") or "mpv" in w["class"].split(".")), None)
    return None


def activate(win):
    command(["xdotool", "windowactivate", "--sync", win["id"]], check=False)
    command(["wmctrl", "-i", "-a", win["id"]])
    command(["wmctrl", "-i", "-r", win["id"], "-b", "add,fullscreen"], check=False)


def fullscreen_state(window_id):
    state = command(["xprop", "-id", window_id, "_NET_WM_STATE"], timeout=1, check=False)
    return "_NET_WM_STATE_FULLSCREEN" in state


def backlight_device():
    """The panel backlight node, preferring the built-in eDP display."""
    try:
        found = sorted(d for d in Path("/sys/class/backlight").iterdir()
                       if (d / "max_brightness").exists())
    except OSError:
        return None
    for device in found:
        try:
            if "edp" in str((device / "device").resolve()).lower():
                return device
        except OSError:
            continue
    return found[0] if found else None


def brightness():
    device = backlight_device()
    if device:
        try:
            level = int((device / "brightness").read_text())
            highest = int((device / "max_brightness").read_text())
            return {"level": round(level * 100 / highest) if highest else None,
                    "available": Path(BRIGHTNESS_HELPER).exists()}
        except (OSError, ValueError):
            pass
    return {"level": None, "available": False}


def screen_blanked():
    """True when the X server has the output blanked, by the remote or its own timer."""
    return "Monitor is Off" in command(["xset", "q"], timeout=2, check=False)


def mpv(*args):
    path = "/tmp/mpv.sock"
    with socket.socket(socket.AF_UNIX) as sock:
        sock.settimeout(0.8)
        sock.connect(path)
        request_id = secrets.randbelow(2**31)
        sock.sendall((json.dumps({"command": list(args), "request_id": request_id}) + "\n").encode())
        data = b""
        while len(data) < 262144:
            chunk = sock.recv(8192)
            if not chunk:
                break
            data += chunk
            while b"\n" in data:
                line, data = data.split(b"\n", 1)
                response = json.loads(line)
                if response.get("request_id") == request_id:
                    if response.get("error") != "success":
                        raise RuntimeError("Player: " + str(response.get("error")))
                    return response.get("data")
    raise RuntimeError("Player did not acknowledge the command")


def playback():
    try:
        pos = mpv("get_property", "time-pos") or 0
        duration = mpv("get_property", "duration") or 0
        paused = bool(mpv("get_property", "pause"))
        title = mpv("get_property", "media-title") or "Video"
        tracks = mpv("get_property", "track-list") or []
        def listing(kind):
            return [{"id": t.get("id"), "title": t.get("title") or t.get("lang") or f"Track {t.get('id')}",
                     "lang": t.get("lang"), "selected": bool(t.get("selected"))}
                    for t in tracks if t.get("type") == kind]
        return {"active": True, "title": title, "position": pos, "duration": duration,
                "paused": paused, "subtitles": listing("sub"), "audio": listing("audio")}
    except (OSError, RuntimeError, ValueError, TimeoutError):
        return {"active": False}


MEDIA_ID = re.compile(r"^tt\d+$")
HASH = re.compile(r"^[0-9a-fA-F]{40}$")


def _count(value, name, minimum):
    if isinstance(value, bool) or not isinstance(value, int) or not minimum <= value <= 100000:
        raise ValueError(f"Invalid {name}")
    return value


def media_target(params):
    kind, item = params.get("type"), params.get("id")
    if kind not in ("movie", "series"):
        raise ValueError("Type must be movie or series")
    if not isinstance(item, str) or not MEDIA_ID.match(item):
        raise ValueError("Invalid title id")
    season, episode = params.get("season"), params.get("episode")
    if kind == "movie":
        if season is not None or episode is not None:
            raise ValueError("Movies have no season or episode")
        return kind, item, None, None, item
    season, episode = _count(season, "season", 1), _count(episode, "episode", 1)
    return kind, item, season, episode, f"{item}:{season}:{episode}"


def media_stream(value):
    if value is None:
        return None
    if not isinstance(value, dict) or not isinstance(value.get("info_hash"), str) or not HASH.match(value["info_hash"]):
        raise ValueError("Invalid stream")
    idx = value.get("file_idx")
    return {"info_hash": value["info_hash"].lower(), "file_idx": None if idx is None else _count(idx, "file index", 0)}


class Controller:
    def __init__(self):
        self.lock = threading.RLock()
        self.action_lock = threading.RLock()
        self.home_lock = threading.Lock()
        self.selection_path = DATA / "selection.json"
        selection = self._read_json(self.selection_path, {})
        self.app = selection.get("app", "home") if selection.get("boot_id") == BOOT_ID else "home"
        if self.app not in (*APPS, "home"):  # also maps retired "stremio"/"vlc" selections
            self.app = "home"
        self.phase = "ready"
        self.error = ""
        self.generation = 0
        self.buffering = None
        self.proc = None
        self.session = None
        self.cache_ready = False
        self.ui_path = DATA / "ui_request.json"
        self.media_path = DATA / "last_media.json"
        self.media = self._read_json(self.media_path, None)
        self.pair_code = ""
        self.pair_expiry = 0
        self.pair_attempts = {}
        self.sessions_path = DATA / "devices.json"
        self.sessions = self._read_json(self.sessions_path, {})
        previous_home_token = DATA / "home_token"
        try:
            self.sessions.pop(hashlib.sha256(previous_home_token.read_text().strip().encode()).hexdigest(), None)
        except OSError:
            pass
        home_token = self.new_device()
        previous_home_token.write_text(home_token)
        previous_home_token.chmod(0o600)
        self.timer_path = DATA / "timer.json"
        saved = self._read_json(self.timer_path, {})
        self.timer = saved if saved.get("boot_id") == BOOT_ID else {"boot_id": BOOT_ID, "deadline": None}
        self._save_json(self.timer_path, self.timer)
        self.legacy_secret = self._legacy_secret()
        self.renew_pairing()
        self.started = time.monotonic()
        threading.Thread(target=self._watch, daemon=True).start()

    @staticmethod
    def _read_json(path, default):
        try:
            return json.loads(path.read_text())
        except (OSError, ValueError):
            return default

    @staticmethod
    def _save_json(path, obj):
        temp = path.with_suffix(path.suffix + ".tmp")
        temp.write_text(json.dumps(obj))
        temp.chmod(0o600)
        os.replace(temp, path)

    @staticmethod
    def _legacy_secret():
        if (DATA / "legacy_retired").exists():
            return ""
        try:
            return (BASE.parent / "secret.txt").read_text().strip()
        except OSError:
            return ""

    def revoke(self, token, mode):
        digest = hashlib.sha256(token.encode()).hexdigest() if token else ""
        with self.lock:
            if digest not in self.sessions:
                raise ValueError("This phone must be paired before changing access")
            if mode == "old_link":
                (DATA / "legacy_retired").touch(mode=0o600)
                self.legacy_secret = ""
            elif mode == "other_phones":
                home_token = (DATA / "home_token").read_text().strip()
                keep = {digest, hashlib.sha256(home_token.encode()).hexdigest()}
                self.sessions = {key: value for key, value in self.sessions.items() if key in keep}
                self._save_json(self.sessions_path, self.sessions)
            else:
                raise ValueError("Unknown access setting")
            return {"ok": True}

    def renew_pairing(self):
        with self.lock:
            if time.time() < self.pair_expiry:
                return self.pair_code
            self.pair_code = f"{secrets.randbelow(1000000):06d}"
            self.pair_expiry = time.time() + 300
            self._save_json(DATA / "pairing.json", {"code": self.pair_code, "expires": self.pair_expiry})
            return self.pair_code

    def pair(self, code, address):
        with self.lock:
            now = time.time()
            attempts = [t for t in self.pair_attempts.get(address, []) if now - t < 300]
            if len(attempts) >= 5:
                raise ValueError("Too many attempts; wait five minutes")
            attempts.append(now)
            self.pair_attempts[address] = attempts
            if now >= self.pair_expiry or not hmac.compare_digest(str(code), self.pair_code):
                raise ValueError("The pairing code is incorrect or expired")
            token = self.new_device()
            self.pair_expiry = 0
            self.renew_pairing()
            return token

    def new_device(self):
        with self.lock:
            now = time.time()
            token = secrets.token_urlsafe(32)
            self.sessions[hashlib.sha256(token.encode()).hexdigest()] = {"created": now, "last": now}
            self._save_json(self.sessions_path, self.sessions)
            return token

    def authenticated(self, cookie, query):
        if cookie:
            token = cookie.get("imactv")
            if token and hashlib.sha256(token.value.encode()).hexdigest() in self.sessions:
                return True
            old = cookie.get("tvremote")
            if old and self.legacy_secret and hmac.compare_digest(old.value, self.legacy_secret):
                return True
        old_url = parse_qs(query).get("k", [""])[0]
        return bool(self.legacy_secret and old_url and hmac.compare_digest(old_url, self.legacy_secret))

    def _watch(self):
        while True:
            time.sleep(2)
            self.renew_pairing()
            with self.lock:
                deadline = self.timer.get("deadline")
                if deadline and time.time() >= deadline:
                    self.timer["deadline"] = None
                    self._save_json(self.timer_path, self.timer)
                    threading.Thread(target=self._power_off, daemon=True).start()
                if self.app != "home" and self.phase == "ready" and time.monotonic() - self.started > 12 and not window_for(self.app) and not playback()["active"]:
                    self.app, self.phase, self.error = "home", "ready", "The app closed."
                    self.generation += 1
                    self._save_json(self.selection_path, {"boot_id": BOOT_ID, "app": "home"})
                    threading.Thread(target=self._show_home, daemon=True).start()
                if self.app == "home" and not window_for("home") and not self.home_lock.locked():
                    threading.Thread(target=self._show_home, daemon=True).start()

    def _show_home(self):
        if not self.home_lock.acquire(blocking=False):
            return
        try:
            try:
                command(["xdpyinfo"], timeout=1)
            except Exception:
                return
            win = window_for("home")
            if not win:
                subprocess.Popen(["/usr/bin/python3", str(BASE / "home.py")], env=DISPLAY_ENV,
                                 stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                                 stderr=subprocess.DEVNULL, start_new_session=True)
                for _ in range(20):
                    time.sleep(0.5)
                    win = window_for("home")
                    if win:
                        break
            if win:
                activate(win)
        finally:
            self.home_lock.release()

    def _launch(self, app, generation):
        try:
            win = window_for(app)
            if not win:
                app_cmd = resolve_app_command(app)
                subprocess.Popen(app_cmd, env=DISPLAY_ENV, stdin=subprocess.DEVNULL,
                                 stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                                 start_new_session=True)
            for _ in range(90):
                with self.lock:
                    if generation != self.generation:
                        return
                win = window_for(app)
                if win:
                    activate(win)
                    with self.lock:
                        if generation == self.generation:
                            self.phase, self.error = "ready", ""
                    return
                time.sleep(0.5)
            raise RuntimeError(f"{app.title()} did not open")
        except Exception as exc:
            with self.lock:
                if generation == self.generation:
                    self.app, self.phase, self.error = "home", "ready", str(exc)
            self._show_home()

    def select(self, app):
        if app != "home" and app not in APPS:
            raise ValueError("Unknown app")
        self._end_playback()
        with self.lock:
            self.generation += 1
            generation = self.generation
            self.app, self.phase, self.error = app, "ready" if app == "home" else "opening", ""
            self.buffering = None
            self._save_json(self.selection_path, {"boot_id": BOOT_ID, "app": app})
        self._stop_proc()
        if app == "home":
            self._show_home()
        else:
            threading.Thread(target=self._launch, args=(app, generation), daemon=True).start()
        return {"app": app, "phase": self.phase}

    def _stop_proc(self):
        proc, self.proc = self.proc, None
        if proc and proc.poll() is None:
            proc.terminate()

    def _current(self, generation):
        with self.lock:
            return generation == self.generation

    def _ui_request(self, screen, media=None):
        request = {"screen": screen, "ts": time.time()}
        if media:
            request["media"] = media
        self._save_json(self.ui_path, request)

    def _snapshot(self):
        """Current media identity plus the latest known position, as a history entry."""
        with self.lock:
            session = self.session
            if not session:
                return None
            entry = dict(session["entry"])
            entry["position"], entry["duration"] = session["pos"], session["dur"]
            return entry

    def _record(self):
        entry = self._snapshot()
        if entry and (entry["position"] > 0 or entry["duration"] > 0):
            try:
                history.record(entry)
            except (OSError, ValueError):
                LOG.exception("Could not save watch history")

    def _end_playback(self):
        """Idempotent teardown: final history record, quit mpv, drop the torrent. True if a session was ended."""
        if not self.session and not self._mpv_active():
            return False
        try:
            if self.session:
                pos, dur = mpv("get_property", "time-pos"), mpv("get_property", "duration")
                with self.lock:
                    if self.session:
                        if isinstance(pos, (int, float)):
                            self.session["pos"] = float(pos)
                        if isinstance(dur, (int, float)) and dur > 0:
                            self.session["dur"] = float(dur)
        except (OSError, RuntimeError, ValueError, TimeoutError):
            pass
        self._record()
        try:
            mpv("quit")
        except (OSError, RuntimeError, ValueError, TimeoutError):
            pass
        with self.lock:
            session, self.session = self.session, None
        self._stop_proc()
        if session:
            session["engine"].remove(session["info_hash"])
        return bool(session)

    def _mpv_active(self):
        return playback()["active"]

    def _return_home(self, screen="details"):
        with self.lock:
            self.generation += 1
            self.app, self.phase, self.buffering = "home", "ready", None
            media = self.media
            self._save_json(self.selection_path, {"boot_id": BOOT_ID, "app": "home"})
        if screen == "details" and media:
            self._ui_request("details", {key: media.get(key) for key in ("type", "id", "season", "episode")})
        else:
            self._ui_request("home")
        threading.Thread(target=self._show_home, daemon=True).start()

    def stop_playback(self, screen):
        self._end_playback()
        self._return_home(screen)
        return {"ok": True}

    def play_media(self, params):
        kind, item, season, episode, video_id = media_target(params)
        stream = media_stream(params.get("stream"))
        start = params.get("start")
        if start is None:
            start = history.resume_position(video_id)
        elif isinstance(start, bool) or not isinstance(start, (int, float)) or not 0 <= start <= 1000000:
            raise ValueError("Invalid start position")
        self._end_playback()
        self._stop_proc()
        with self.lock:
            self.generation += 1
            generation = self.generation
            self.app, self.phase, self.error = "player", "buffering", ""
            self.buffering = {"peers": 0, "speed": 0.0, "source": ""}
            self.media = {"type": kind, "id": item, "video_id": video_id, "season": season,
                          "episode": episode, "name": item}
            self._save_json(self.media_path, self.media)
            self._save_json(self.selection_path, {"boot_id": BOOT_ID, "app": "home"})
        threading.Thread(target=self._play_worker, args=(generation, stream, float(start)), daemon=True).start()
        return {"ok": True, "source": "Selected source" if stream else ""}

    def _play_fail(self, generation, exc):
        LOG.warning("Playback failed: %s", exc)
        with self.lock:
            if generation != self.generation:
                return
            self.app, self.phase, self.error, self.buffering = "home", "ready", str(exc)[:300], None
            session, self.session = self.session, None
        self._stop_proc()
        if session:
            session["engine"].remove(session["info_hash"])
        self._show_home()

    def _attach_subs(self, tracks, cfg):
        """Load every picked external sub with a proper label/lang, then select the default language."""
        order = list(cfg["sub_langs"])
        default = media_subs.normalize(cfg.get("sub_default", "eng"))
        if default in order:
            order.remove(default)
            order.insert(0, default)
        try:
            embedded = [t for t in (mpv("get_property", "track-list") or []) if t.get("type") == "sub"]
        except (OSError, RuntimeError, ValueError, TimeoutError):
            embedded = []
        emb = {}
        for track in embedded:
            emb.setdefault(media_subs.normalize(track.get("lang")), track.get("id"))
        target = next((lang for lang in order if lang in emb or any(t["lang"] == lang for t in tracks)), None)
        selected = target in emb or target is None
        for sub in tracks:
            flag = "auto"
            if not selected and sub["lang"] == target:
                flag, selected = "select", True
            try:
                mpv("sub-add", sub["url"], flag, sub["label"], sub["lang"])
            except (OSError, RuntimeError, ValueError, TimeoutError):
                continue
        if target in emb:
            try:
                mpv("set_property", "sid", emb[target])
            except (OSError, RuntimeError, ValueError, TimeoutError):
                pass

    def _playback_loop(self, generation, interval=5.0):
        """Poll position every few seconds for history; finish cleanly when mpv goes away."""
        misses = 0
        while self._current(generation):
            time.sleep(interval)
            if not self._current(generation):
                return
            proc = self.proc
            try:
                pos, dur = mpv("get_property", "time-pos"), mpv("get_property", "duration")
                misses = 0
                with self.lock:
                    if generation == self.generation and self.session:
                        if isinstance(pos, (int, float)):
                            self.session["pos"] = float(pos)
                        if isinstance(dur, (int, float)) and dur > 0:
                            self.session["dur"] = float(dur)
                self._record()
            except (OSError, RuntimeError, ValueError, TimeoutError):
                misses += 1
            if (proc is not None and proc.poll() is not None) or misses >= 3:
                if self._current(generation) and self._end_playback():
                    self._return_home("details")
                return

    def _play_worker(self, generation, stream, start):
        try:
            with self.lock:
                kind, item, video_id = self.media["type"], self.media["id"], self.media["video_id"]
                season, episode = self.media.get("season"), self.media.get("episode")
            cfg = media_config.load()
            torrents = media_engine.Engine(cfg["engine"])
            if not torrents.healthy():
                raise RuntimeError("The torrent engine is not running")
            if not self.cache_ready:
                torrents.configure_cache(cfg["cache_bytes"])
                self.cache_ready = True
            try:
                meta = addons.meta(kind, item) or {}
            except addons.AddonError:
                meta = {}
            base_name = meta.get("name") or item
            name = base_name + (f" S{season:02d}E{episode:02d}" if season else "")
            if stream:
                info_hash, file_idx, label = stream["info_hash"], stream["file_idx"], "Selected source"
            else:
                found = [media_streams.parse(s) for s in addons.streams(kind, video_id)]
                chosen = media_streams.best(found, cfg)
                if not chosen:
                    raise RuntimeError("No playable sources found")
                info_hash, file_idx, label = chosen["info_hash"], chosen["file_idx"], chosen["label"]
            try:
                tracks = media_subs.pick(addons.subtitles(kind, video_id), cfg["sub_langs"])
            except addons.AddonError:
                tracks = []
            if not self._current(generation):
                return
            with self.lock:
                self.media["name"] = name
                self._save_json(self.media_path, self.media)
                self.buffering["source"] = label
                self.session = {"engine": torrents, "info_hash": info_hash, "pos": 0.0, "dur": 0.0,
                                "entry": {"type": kind, "id": item, "video_id": video_id, "season": season,
                                          "episode": episode, "name": base_name, "poster": meta.get("poster")}}
            try:
                os.unlink("/tmp/mpv.sock")
            except OSError:
                pass
            slang = player.slang_arg(cfg["sub_langs"], media_subs.normalize(cfg.get("sub_default", "eng")))
            argv = player.mpv_argv(torrents.url(info_hash, file_idx), title=name, subs=[], start=start, slang=slang)
            proc = subprocess.Popen(argv, env=DISPLAY_ENV, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                                    stderr=subprocess.DEVNULL, start_new_session=True)
            with self.lock:
                if generation != self.generation:
                    proc.terminate()
                    return
                self.proc = proc
            for _ in range(240):
                if not self._current(generation):
                    return
                if proc.poll() is not None:
                    raise RuntimeError("The player stopped before playback began")
                stats = torrents.stats(info_hash, file_idx)
                if stats:
                    with self.lock:
                        if generation == self.generation and self.buffering:
                            self.buffering.update(peers=stats["peers"], speed=stats["speed"])
                try:
                    if mpv("get_property", "time-pos") is not None:
                        with self.lock:
                            if generation == self.generation:
                                self.phase, self.buffering = "playing", None
                        break
                except (OSError, RuntimeError, ValueError, TimeoutError):
                    pass
                time.sleep(0.75)
            else:
                raise RuntimeError("Timed out waiting for the stream to start")
            self._attach_subs(tracks, cfg)
            self._playback_loop(generation)
        except Exception as exc:
            self._play_fail(generation, exc)

    def set_brightness(self, level):
        if isinstance(level, bool) or not isinstance(level, int) or not 5 <= level <= 100:
            raise ValueError("Brightness must be between 5 and 100")
        if not backlight_device():
            raise RuntimeError("This display has no adjustable backlight")
        if not Path(BRIGHTNESS_HELPER).exists():
            raise RuntimeError("Run setup-privileges.sh on the TV to allow brightness control")
        command(["sudo", "-n", BRIGHTNESS_HELPER, str(level)], timeout=5)
        return brightness()

    def set_screen(self, on):
        """Blank the output with DPMS and leave everything else running.

        The backlight is never used for this: this panel keeps glowing at a zero
        backlight, and a physical keypress wakes DPMS, which would then leave the
        screen awake but unlit with no visible way back.

        ``on=None`` means toggle, decided from the live X state rather than from
        whatever the phone last polled, so a stale press still flips the screen.
        """
        if on is None:
            on = screen_blanked()
        elif not isinstance(on, bool):
            raise ValueError("Screen state must be true or false")
        command(["xset", "dpms", "force", "on" if on else "off"], timeout=3)
        if screen_blanked() != (not on):
            raise RuntimeError("The TV screen did not respond to the remote")
        return {"screen_off": not on}

    def toggle_fullscreen(self):
        win = window_for(self.app)
        if not win:
            raise RuntimeError("TV window is unavailable; use Recover")
        command(["wmctrl", "-i", "-a", win["id"]], check=False)
        state = "remove" if fullscreen_state(win["id"]) else "add"
        command(["wmctrl", "-i", "-r", win["id"], "-b", state + ",fullscreen"], check=False)
        return {"fullscreen": state == "add"}

    def _power_off(self):
        try:
            command(["sudo", "-n", "/usr/bin/systemctl", "poweroff"], timeout=4)
        except Exception as exc:
            LOG.exception("Power off failed")
            with self.lock:
                self.error = "Could not turn off: " + str(exc)

    def status(self):
        with self.lock:
            app, phase, error = self.app, self.phase, self.error
            buffering = dict(self.buffering) if self.buffering and phase == "buffering" else None
            current_media = dict(self.media) if self.media else None
            deadline = self.timer.get("deadline")
        try:
            volume = command(["pactl", "get-sink-volume", "@DEFAULT_SINK@"], timeout=1)
            percent = int(volume.split("%", 1)[0].rsplit("/", 1)[-1].strip())
            muted = command(["pactl", "get-sink-mute", "@DEFAULT_SINK@"], timeout=1).endswith("yes")
        except Exception:
            percent, muted = None, None
        temps = []
        for sensor in Path("/sys/class/hwmon").glob("hwmon*/temp*_input"):
            try:
                if (sensor.parent / "name").read_text().strip() == "coretemp":
                    value = int(sensor.read_text()) / 1000
                    if 0 < value < 120:
                        temps.append(value)
            except (OSError, ValueError):
                continue
        gpu_temps = []
        smc = Path("/sys/devices/platform/applesmc.768")
        for label in smc.glob("temp*_label"):
            try:
                if label.read_text().strip() in ("TG0D", "TG0H", "TG0p"):
                    value = int(label.with_name(label.name.replace("_label", "_input")).read_text()) / 1000
                    if 0 < value < 120:
                        gpu_temps.append(value)
            except (OSError, ValueError):
                continue
        try:
            gui = bool(command(["xdpyinfo"], timeout=1, check=False))
        except Exception:
            gui = False
        player = playback()
        cpu_temp = max(temps) if temps else None
        gpu_temp = max(gpu_temps) if gpu_temps else None
        win = window_for(app) if gui else None
        return {"app": app, "phase": phase, "error": error, "buffering": buffering, "media": current_media,
                "gui": gui, "volume": percent, "muted": muted,
                "old_link_active": bool(self.legacy_secret),
                "playback": player, "temperature": cpu_temp, "gpu_temperature": gpu_temp,
                "brightness": brightness(), "screen_off": screen_blanked(),
                "fullscreen": bool(win) and fullscreen_state(win["id"]),
                "thermal_warning": (cpu_temp is not None and cpu_temp >= 80) or
                                   (gpu_temp is not None and gpu_temp >= 85),
                "timer": {"active": bool(deadline), "deadline": deadline},
                "capabilities": {"timeline": player["active"], "subtitles": player["active"], "audio": player["active"]}}

    def action(self, action, params):
        with self.action_lock:
            if action == "select":
                return self.select(params.get("app"))
            if action == "timer":
                minutes = params.get("minutes")
                if minutes is not None and minutes not in (15, 30, 60, 90):
                    raise ValueError("Use a timer preset")
                with self.lock:
                    self.timer["deadline"] = time.time() + minutes * 60 if minutes else None
                    self._save_json(self.timer_path, self.timer)
                return {"deadline": self.timer["deadline"]}
            if action == "poweroff":
                if params.get("confirm") is not True:
                    raise ValueError("Confirm power off")
                threading.Thread(target=self._power_off, daemon=True).start()
                return {"turning_off": True}
            if action == "recover":
                self._show_home()
                if not window_for("home"):
                    raise RuntimeError("TV Home could not open; restart the desktop session")
                return {"recovered": True}
            if action == "restart_session":
                if params.get("confirm") is not True:
                    raise ValueError("Confirm desktop restart")
                # Restarting the display manager runs its configured autologin and restores TV Home.
                dm_service = None
                for svc in ("display-manager.service", "gdm.service", "gdm3.service", "lightdm.service"):
                    try:
                        allowed = command(["sudo", "-n", "-l", "/usr/bin/systemctl", "restart", svc], timeout=2, check=False)
                        if allowed and "not allowed" not in allowed.lower():
                            dm_service = svc
                            break
                    except Exception:
                        continue
                if not dm_service:
                    dm_service = "display-manager.service"
                def restart(svc_name=dm_service):
                    time.sleep(1)
                    try:
                        command(["sudo", "-n", "/usr/bin/systemctl", "restart", svc_name], timeout=15)
                    except Exception:
                        LOG.exception("Desktop restart failed")
                        with self.lock:
                            self.error = "Desktop restart failed; TV controller is still available"
                threading.Thread(target=restart, daemon=True).start()
                return {"restarting": True}
            if action == "restart_app":
                app = self.app
                if app == "home":
                    raise ValueError("No app is selected")
                win = window_for(app)
                if win:
                    command(["wmctrl", "-i", "-c", win["id"]], check=False)
                    with self.lock:
                        self.phase = "opening"
                    for _ in range(20):
                        if not window_for(app):
                            break
                        time.sleep(0.25)
                return self.select(app)
            if action == "volume":
                direction = params.get("direction")
                if direction not in (-1, 1):
                    raise ValueError("Invalid volume direction")
                volume = self.status()["volume"]
                if volume is None:
                    raise RuntimeError("Audio output is unavailable")
                command(["pactl", "set-sink-volume", "@DEFAULT_SINK@", f"{max(0, min(100, volume + direction * 5))}%"])
                return {"volume": max(0, min(100, volume + direction * 5))}
            if action == "mute":
                command(["pactl", "set-sink-mute", "@DEFAULT_SINK@", "toggle"])
                return {"ok": True}
            if action == "brightness":
                return self.set_brightness(params.get("level"))
            if action == "screen":
                return self.set_screen(params.get("on"))
            if action == "fullscreen":
                return self.toggle_fullscreen()
            if action == "play_media":
                return self.play_media(params)
            if action == "open_search":
                if self.app != "home":
                    self.select("home")
                self._ui_request("search")
                return {"ok": True}
            if action == "sources":
                kind, _, _, _, video_id = media_target(params)
                try:
                    found = [media_streams.parse(s) for s in addons.streams(kind, video_id)]
                except addons.AddonError as exc:
                    raise RuntimeError(str(exc)) from exc
                return {"sources": media_streams.rank(found, media_config.load())}
            if action in ("seek", "subtitle", "subdelay", "audio"):
                if not playback()["active"]:
                    raise RuntimeError("This control is available only while MPV is playing")
                if action == "seek":
                    seconds = float(params.get("seconds", 0))
                    if not 0 <= seconds <= 86400:
                        raise ValueError("Invalid position")
                    mpv("set_property", "time-pos", seconds)
                elif action in ("subtitle", "audio"):
                    value = params.get("id")
                    if value != "no" and (isinstance(value, bool) or not isinstance(value, int)):
                        raise ValueError("Invalid track")
                    mpv("set_property", "sid" if action == "subtitle" else "aid", value)
                else:
                    delta = float(params.get("delta", 0))
                    if abs(delta) > 5:
                        raise ValueError("Invalid subtitle delay")
                    mpv("add", "sub-delay", delta)
                return {"ok": True}
            player = playback()["active"]
            if action == "back" and (self.app == "player" or player):
                return self.stop_playback("details")
            if action == "play" and self.app == "home" and not player:
                raise RuntimeError("Open an app to play video")
            if action in ("play", "left", "right", "up", "down", "ok") and player:
                mapping = {"play": ("cycle", "pause"),
                           "left": ("seek", -10), "right": ("seek", 10),
                           "up": ("seek", 60), "down": ("seek", -60),
                           "ok": ("show-progress",)}
                mpv(*mapping[action])
                return {"ok": True}
            if action == "home":
                if self.app == "player" or player:
                    return self.stop_playback("home")
                return self.select("home")
            if action == "back":
                action = "Escape"
            keymap = {"left": "Left", "right": "Right", "up": "Up", "down": "Down",
                      "ok": "Return", "play": "space", "enter": "Return", "delete": "BackSpace",
                      "escape": "Escape"}
            if action in keymap or action in ("BackSpace", "Escape"):
                self._focus_target()
                command(["xdotool", "key", "--clearmodifiers", keymap.get(action, action)])
                return {"ok": True}
            if action == "type":
                value = params.get("text", "")
                if not isinstance(value, str) or not value or len(value) > 500:
                    raise ValueError("Text must contain 1–500 characters")
                self._focus_target()
                command(["xdotool", "type", "--clearmodifiers", "--delay", "1", "--", value], timeout=10)
                return {"typed": len(value)}
            if action == "move":
                dx, dy = int(params.get("dx", 0)), int(params.get("dy", 0))
                if max(abs(dx), abs(dy)) > 300:
                    raise ValueError("Pointer movement too large")
                self._focus_target()
                command(["xdotool", "mousemove_relative", "--", str(dx), str(dy)])
                return {"ok": True}
            if action in ("click", "rightclick", "scrollup", "scrolldown"):
                self._focus_target()
                button = {"click": 1, "rightclick": 3, "scrollup": 4, "scrolldown": 5}[action]
                command(["xdotool", "click", str(button)])
                return {"ok": True}
            raise ValueError("Unknown action")

    def _focus_target(self):
        if self.phase != "ready":
            raise RuntimeError("App is still opening")
        win = window_for(self.app)
        if not win:
            raise RuntimeError("TV window is unavailable; use Recover")
        activate(win)


CONTROL = None


class Handler(BaseHTTPRequestHandler):
    def log_message(self, format, *args):
        LOG.info(format, *args)

    def respond(self, status, data, *, cookie=None):
        body = json.dumps(data).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(body)))
        if cookie:
            self.send_header("Set-Cookie", f"imactv={cookie}; Path=/; Max-Age=2592000; HttpOnly; SameSite=Strict")
        self.end_headers()
        self.wfile.write(body)

    def authorised(self, query):
        cookie = SimpleCookie()
        try:
            cookie.load(self.headers.get("Cookie", ""))
        except Exception:
            pass
        return CONTROL.authenticated(cookie, query)

    def do_GET(self):
        parsed = urlsplit(self.path)
        if parsed.path == "/" and self.authorised(parsed.query):
            cookies = SimpleCookie()
            try:
                cookies.load(self.headers.get("Cookie", ""))
            except Exception:
                pass
            if not cookies.get("imactv"):
                token = CONTROL.new_device()
                self.send_response(302)
                self.send_header("Location", "/")
                self.send_header("Cache-Control", "no-store")
                self.send_header("Set-Cookie", f"imactv={token}; Path=/; Max-Age=2592000; HttpOnly; SameSite=Strict")
                self.end_headers()
                return
        if parsed.path == "/" or parsed.path in ("/app.css", "/app.js"):
            name = "index.html" if parsed.path == "/" else parsed.path.lstrip("/")
            path = WEB / name
            payload = path.read_bytes()
            ctype = "text/html" if name.endswith("html") else "text/css" if name.endswith("css") else "text/javascript"
            self.send_response(200)
            self.send_header("Content-Type", ctype + "; charset=utf-8")
            self.send_header("Cache-Control", "no-store")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)
            return
        if parsed.path == "/api/state":
            if not self.authorised(parsed.query):
                self.respond(401, {"error": "Pair this phone using the code on the TV"})
                return
            self.respond(200, CONTROL.status())
            return
        if parsed.path == "/api/pair-status":
            CONTROL.renew_pairing()
            self.respond(200, {"paired": self.authorised(parsed.query), "code": CONTROL.pair_code})
            return
        self.respond(404, {"error": "Not found"})

    def do_POST(self):
        parsed = urlsplit(self.path)
        origin = self.headers.get("Origin")
        host = self.headers.get("Host", "")
        if origin and origin not in ("http://" + host, "https://" + host):
            self.respond(403, {"error": "Invalid origin"})
            return
        try:
            if self.headers.get("Transfer-Encoding", "").lower() == "chunked":
                chunks = []
                total = 0
                while True:
                    line = self.rfile.readline().strip()
                    if not line:
                        break
                    chunk_len = int(line, 16)
                    if chunk_len == 0:
                        self.rfile.readline()
                        break
                    total += chunk_len
                    if total > 4096:
                        raise ValueError("Invalid request size")
                    chunks.append(self.rfile.read(chunk_len))
                    self.rfile.readline()
                raw = b"".join(chunks)
            else:
                size = int(self.headers.get("Content-Length", "0"))
                if size <= 0 or size > 4096:
                    raise ValueError("Invalid request size")
                raw = self.rfile.read(size)
            body = json.loads(raw)
            if not isinstance(body, dict):
                raise ValueError("Expected an object")
            if parsed.path == "/api/pair":
                token = CONTROL.pair(body.get("code", ""), self.client_address[0])
                self.respond(200, {"paired": True}, cookie=token)
                return
            if parsed.path == "/api/commands":
                if not self.authorised(parsed.query):
                    self.respond(401, {"error": "Phone is not paired"})
                    return
                # Log the action only. The journal is a plain text file, and Type
                # carries whatever was typed into the TV search box.
                action = body.get("action")
                LOG.info("Command from %s: %r", self.client_address[0], action)
                result = CONTROL.action(action, body.get("params") or {})
                self.respond(200, {"ok": True, "result": result})
                return
            if parsed.path == "/api/revoke":
                cookie = SimpleCookie()
                cookie.load(self.headers.get("Cookie", ""))
                token = cookie.get("imactv")
                result = CONTROL.revoke(token.value if token else "", body.get("mode"))
                self.respond(200, result)
                return
            self.respond(404, {"error": "Not found"})
        except (ValueError, TypeError) as exc:
            LOG.info("Rejected request: %s", exc)
            self.respond(400, {"error": str(exc)})
        except (RuntimeError, OSError, subprocess.TimeoutExpired) as exc:
            LOG.exception("Command failed")
            self.respond(503, {"error": str(exc)[:300]})


def serve():
    global CONTROL
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    CONTROL = Controller()
    server = ThreadingHTTPServer(("0.0.0.0", PORT), Handler)
    server.daemon_threads = True
    LOG.info("Listening on port %d", PORT)
    server.serve_forever()


if __name__ == "__main__":
    serve()
