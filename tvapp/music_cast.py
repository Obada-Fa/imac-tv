"""Cast music from the YouTube / YouTube Music phone apps to the TV, sound only.

`music_cast/receiver.mjs` (Node, yt-cast-receiver) shows up as "Nexus TV" in the apps' cast menu and plays
each song through its own mpv with `--no-video` (yt-dlp picks the audio stream), so nothing is ever decoded
but audio, Premium or not. The phone server keeps it running (`Supervisor`); Home reads what it plays from
`music_cast_now.json` (`now`) for the Now playing tile and the visualizer, and pauses it through mpv's
socket (`toggle`, `pause`), which the receiver passes on to the phone.

Installed by scripts/install-music-cast.sh: Node 18+ (system or ~/.local/share/imactv/node), yt-dlp
(~/.local/share/imactv/bin/yt-dlp) and `npm ci` in music_cast/. Missing pieces just leave it off."""

import json
import logging
import os
from pathlib import Path
import shutil
import socket
import subprocess
import threading
import time

LOG = logging.getLogger("imactv.music_cast")
APP_DIR = Path(__file__).resolve().parent / "music_cast"
LOCAL = Path.home() / ".local/share/imactv"
NOW_FILE = "music_cast_now.json"
SOCK_NAME = "music-mpv.sock"
CTL_NAME = "music-ctl.sock"     # receiver.mjs `serveControl`: play_pause, pause, resume, stop, next, previous, seek
COMMANDS = ("play_pause", "pause", "resume", "stop", "next", "previous", "seek", "set_position")
PORT = int(os.environ.get("IMACTV_MUSIC_PORT", "8766"))
MAX_AGE = 20.0          # s: the receiver rewrites the file every 5 s while a song is loaded
MIN_NODE = (20, 10)    # youtubei.js (pulled in by yt-cast-receiver) uses import attributes: Node 18.19 can't load it


def node_version(binary):
    """(major, minor) of a Node.js binary, (0, 0) when it doesn't run."""
    try:
        out = subprocess.run([binary, "--version"], capture_output=True, text=True, timeout=5).stdout
        major, minor = out.strip().lstrip("v").split(".")[:2]
        return int(major), int(minor)
    except (OSError, ValueError, subprocess.SubprocessError):
        return 0, 0


def node_bin():
    """A Node.js of at least MIN_NODE: the one the installer unpacked first, else the system's."""
    for cand in (str(LOCAL / "node/bin/node"), shutil.which("node")):
        if cand and Path(cand).is_file() and node_version(cand) >= MIN_NODE:
            return cand
    return None


def ytdlp_bin():
    """The installer's yt-dlp (official build, kept current) first, then any other."""
    for cand in (LOCAL / "bin/yt-dlp", Path.home() / ".local/bin/yt-dlp"):
        if cand.is_file() and os.access(cand, os.X_OK):
            return str(cand)
    return shutil.which("yt-dlp")


def ytdlp_knows(binary, option):
    """Whether this yt-dlp has `option` (an older one would refuse to start with it)."""
    try:
        out = subprocess.run([binary, "--help"], capture_output=True, text=True, timeout=20).stdout
    except (OSError, subprocess.SubprocessError):
        return False
    return option in out


def missing():
    """What the receiver still needs, as words for a log line; empty when it can run."""
    need = []
    if not node_bin():
        need.append("Node.js %d.%d+" % MIN_NODE)
    if not (APP_DIR / "node_modules/yt-cast-receiver").is_dir():
        need.append("music_cast/node_modules (npm ci)")
    if not shutil.which("mpv"):
        need.append("mpv")
    if not ytdlp_bin():
        need.append("yt-dlp")
    return need


def parse_now(raw, now=None, max_age=MAX_AGE):
    """music_cast_now.json -> a now-playing dict like nowplaying.current()'s (source "cast"), or None."""
    if not isinstance(raw, dict) or raw.get("status") not in ("Playing", "Paused"):
        return None
    updated = raw.get("updated")
    now = time.time() if now is None else now
    if not isinstance(updated, (int, float)) or now - updated > max_age:
        return None                       # the receiver died or was stopped mid-song
    return {"status": raw["status"], "title": str(raw.get("title") or "").strip() or "Music",
            "artist": str(raw.get("artist") or "").strip(), "art": raw.get("art") or None,
            "id": raw.get("id"), "position": raw.get("position"), "duration": raw.get("duration"),
            "can_next": bool(raw.get("can_next")), "can_prev": bool(raw.get("can_prev")),
            "updated": updated, "source": "cast"}


def now(data_dir):
    try:
        raw = json.loads((Path(data_dir) / NOW_FILE).read_text())
    except (OSError, ValueError):
        return None
    return parse_now(raw)


def mpv_command(data_dir, cmd, timeout=1.0):
    """One JSON IPC command to the receiver's mpv; True when mpv accepted it."""
    path = str(Path(data_dir) / SOCK_NAME)
    try:
        with socket.socket(socket.AF_UNIX) as s:
            s.settimeout(timeout)
            s.connect(path)
            s.sendall((json.dumps({"command": cmd, "request_id": 7}) + "\n").encode())
            buf = b""
            while True:
                chunk = s.recv(4096)
                if not chunk:
                    return False
                buf += chunk
                for line in buf.split(b"\n"):
                    try:
                        msg = json.loads(line)
                    except ValueError:
                        continue
                    if msg.get("request_id") == 7:
                        return msg.get("error") == "success"
    except OSError:
        return False


def control(data_dir, cmd, timeout=6.0, **params):
    """One command to the receiver's control socket (through its Player, so the phone app follows).
    True/False as the receiver answered; None when no receiver listens (an older one, or none running)."""
    if cmd not in COMMANDS:
        raise ValueError("Unknown music command")
    try:
        with socket.socket(socket.AF_UNIX) as s:
            s.settimeout(timeout)
            s.connect(str(Path(data_dir) / CTL_NAME))
            s.sendall((json.dumps({"cmd": cmd, **params}) + "\n").encode())
            buf = b""
            while b"\n" not in buf:
                chunk = s.recv(4096)
                if not chunk:
                    break
                buf += chunk
        return bool(json.loads(buf.split(b"\n")[0]).get("ok"))
    except (OSError, ValueError):
        return None


def toggle(data_dir):
    """Play/pause from the TV (Home's Now playing tile) or the phone remote; the receiver tells the phone."""
    done = control(data_dir, "play_pause")
    return mpv_command(data_dir, ["cycle", "pause"]) if done is None else done


def pause(data_dir):
    """A movie or YouTube starts: pause the cast music (never resumed by us)."""
    st = now(data_dir)
    if st and st["status"] == "Playing":
        done = control(data_dir, "pause")
        return mpv_command(data_dir, ["set_property", "pause", True]) if done is None else done
    return False


class Arbiter:
    """Only one of cast music and YouTube plays: whichever started last wins, the other is paused.

    `step(youtube_status, cast)` every few seconds -> "pause_cast", "pause_youtube" or None; also reports a new
    cast song so the TV can show it (`new_song`)."""

    def __init__(self):
        self.yt_playing = False
        self.cast_key = None          # (id) of the cast song last seen playing
        self.cast_playing = False
        self.new_song = None

    def step(self, youtube_status, cast):
        yt = youtube_status == "Playing"
        playing = bool(cast) and cast.get("status") == "Playing"
        key = cast.get("id") or cast.get("title") if cast else None
        cast_started = playing and (not self.cast_playing or key != self.cast_key)
        yt_started = yt and not self.yt_playing
        self.new_song = cast if playing and key != self.cast_key else None
        if playing:
            self.cast_key = key
        elif not cast:
            self.cast_key = None
        action = None
        if yt and playing:
            action = "pause_youtube" if cast_started and not yt_started else "pause_cast"
        self.yt_playing = yt and action != "pause_youtube"
        self.cast_playing = playing and action != "pause_cast"
        return action


class Supervisor:
    """Keeps receiver.mjs running beside the phone server: restarts it after a crash, with growing pauses."""

    def __init__(self, data_dir, popen=subprocess.Popen):
        self.data_dir = Path(data_dir)
        self.popen = popen
        self.proc = None
        self.stopping = False
        self.thread = None

    def env(self):
        env = dict(os.environ, IMACTV_DATA_DIR=str(self.data_dir), IMACTV_MUSIC_PORT=str(PORT),
                   IMACTV_MUSIC_SOCK=str(self.data_dir / SOCK_NAME), IMACTV_MUSIC_CTL=str(self.data_dir / CTL_NAME))
        ytdlp = ytdlp_bin()
        if ytdlp:
            env["IMACTV_YTDLP"] = ytdlp
        node = node_bin()
        if node and ytdlp and ytdlp_knows(ytdlp, "--js-runtimes"):
            env["IMACTV_JS_RUNTIME"] = "node:" + node    # yt-dlp solves YouTube's player challenge with it
        return env

    def start(self):
        need = missing()
        if need:
            LOG.info("Music casting is off; missing: %s", ", ".join(need))
            return False
        self.thread = threading.Thread(target=self._run, daemon=True, name="music-cast")
        self.thread.start()
        return True

    def stale_pids(self, proc_root="/proc"):
        """Receivers and their mpv left from an earlier run of this TV (same data dir): a receiver killed
        mid-song leaves its mpv playing with nothing to control it, and an old one holds the cast port."""
        receiver = str(APP_DIR / "receiver.mjs").encode()
        mpv_sock = ("--input-ipc-server=" + str(self.data_dir / SOCK_NAME)).encode()
        mine = ("IMACTV_DATA_DIR=" + str(self.data_dir)).encode()
        found = []
        for entry in Path(proc_root).glob("[0-9]*"):
            try:
                args = (entry / "cmdline").read_bytes().split(b"\0")
                if mpv_sock in args or (receiver in args and mine in (entry / "environ").read_bytes().split(b"\0")):
                    found.append(int(entry.name))
            except (OSError, ValueError):
                continue
        return [pid for pid in found if pid != os.getpid()]

    def kill_stale(self):
        for pid in self.stale_pids():
            try:
                os.kill(pid, 15)
                LOG.info("Stopped a leftover music receiver process %d", pid)
            except OSError:
                pass

    def _run(self):
        delay = 5.0
        while not self.stopping:
            self.kill_stale()
            started = time.monotonic()
            log_path = self.data_dir / "music_cast.log"
            try:
                if log_path.exists() and log_path.stat().st_size > 1_000_000:
                    log_path.unlink()
                with open(log_path, "a") as log:
                    self.proc = self.popen([node_bin(), str(APP_DIR / "receiver.mjs")], cwd=str(APP_DIR),
                                           env=self.env(), stdin=subprocess.DEVNULL, stdout=log,
                                           stderr=subprocess.STDOUT, start_new_session=True)
                LOG.info("Music receiver started (\"Nexus TV\" in the YouTube Music cast menu)")
                code = self.proc.wait()
            except OSError as exc:
                LOG.warning("Music receiver could not start: %s", exc)
                code = None
            if self.stopping:
                return
            delay = 5.0 if time.monotonic() - started > 120 else min(delay * 2, 300.0)
            LOG.warning("Music receiver exited (%s); restarting in %.0f s", code, delay)
            time.sleep(delay)

    def stop(self):
        self.stopping = True
        proc = self.proc
        if proc and proc.poll() is None:
            proc.terminate()
            try:
                proc.wait(timeout=4)
            except subprocess.TimeoutExpired:
                proc.kill()
