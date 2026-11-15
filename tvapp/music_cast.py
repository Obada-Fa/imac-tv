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
SERVICE_FILE = "music_cast_service.json"   # {"enabled": false} once casting is switched off in Settings
NET_FILE = "music_cast_net.json"     # receiver.mjs: the interfaces and addresses it is discoverable on
CODE_FILE = "music_cast_code.json"   # receiver.mjs: the current "Link with TV code"
CTL_NAME = "music-ctl.sock"     # receiver.mjs `serveControl`: play_pause, pause, resume, stop, next, previous, seek
COMMANDS = ("play_pause", "pause", "resume", "stop", "next", "previous", "seek", "set_position")
PORT = int(os.environ.get("IMACTV_MUSIC_PORT", "8766"))
MAX_AGE = 20.0          # s: the receiver rewrites the file every 5 s while a song is loaded
MIN_NODE = (20, 10)    # youtubei.js (pulled in by yt-cast-receiver) uses import attributes: Node 18.19 can't load it


def node_version(binary):
    """(major, minor) of a Node.js binary, (0, 0) when it doesn't run."""
    try:
        out = subprocess.run([binary, "--version"], capture_output=True, text=True, timeout=30).stdout   # a cold boot can take >5 s to page Node in
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


def request(data_dir, msg, timeout=6.0):
    """One JSON line to the receiver's control socket; its answer ({"ok", "data"?, "error"?}), or None when no
    receiver listens (an older one, or none running)."""
    try:
        with socket.socket(socket.AF_UNIX) as s:
            s.settimeout(timeout)
            s.connect(str(Path(data_dir) / CTL_NAME))
            s.sendall((json.dumps(msg) + "\n").encode())
            buf = b""
            while b"\n" not in buf:
                chunk = s.recv(4096)
                if not chunk:
                    break
                buf += chunk
        answer = json.loads(buf.split(b"\n")[0])
        return answer if isinstance(answer, dict) else None
    except (OSError, ValueError):
        return None


def control(data_dir, cmd, timeout=6.0, **params):
    """One command to the receiver's control socket (through its Player, so the phone app follows).
    True/False as the receiver answered; None when no receiver listens (an older one, or none running)."""
    if cmd not in COMMANDS:
        raise ValueError("Unknown music command")
    answer = request(data_dir, {"cmd": cmd, **params}, timeout)
    return None if answer is None else bool(answer.get("ok"))


def format_code(code):
    digits = "".join(ch for ch in str(code or "") if ch.isdigit())
    return " ".join(digits[i:i + 3] for i in range(0, len(digits), 3))


def pair_code(data_dir):
    """"Link with TV code" for the YouTube / YouTube Music app (Settings -> Watch on TV -> Enter TV code).
    Asking starts the receiver fetching codes for 15 minutes. {"code": "324 419 547 823" or None, "pending",
    "error"}; None when no receiver answers."""
    answer = request(data_dir, {"cmd": "pair_code"})
    if not answer or not answer.get("ok"):
        return None
    data = answer.get("data") or {}
    code, error = data.get("code"), None
    try:
        saved = json.loads((Path(data_dir) / CODE_FILE).read_text())
        code = code or saved.get("code")
        error = saved.get("error")
    except (OSError, ValueError, AttributeError):
        pass
    return {"code": format_code(code) or None, "pending": not code and not error, "error": error}


# ---- "Why isn't Nexus TV in my cast menu?": Settings -> Connections -> Check casting, and `nexus-tv-cast check`
SSDP = ("239.255.255.250", 1900)
DIAL_ST = "urn:dial-multiscreen-org:service:dial:1"


def ssdp_search(local, timeout=2.0):
    """Search for DIAL devices from the TV's own address `local`, as a phone on that network would; the
    LOCATION of each answer."""
    found = []
    msg = ("M-SEARCH * HTTP/1.1\r\nHOST: 239.255.255.250:1900\r\nMAN: \"ssdp:discover\"\r\nMX: 1\r\n"
           "ST: %s\r\n\r\n" % DIAL_ST).encode()
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_TTL, 2)
            s.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_IF, socket.inet_aton(local))
            s.bind((local, 0))
            s.sendto(msg, SSDP)
            end = time.monotonic() + timeout
            while time.monotonic() < end:
                s.settimeout(max(0.05, end - time.monotonic()))
                try:
                    data, _ = s.recvfrom(4096)
                except socket.timeout:
                    break
                for line in data.decode("latin-1").split("\r\n"):
                    if line.upper().startswith("LOCATION:"):
                        found.append(line.split(":", 1)[1].strip())
    except OSError:
        pass
    return found


def device_page(address, port, timeout=3.0):
    """Whether the receiver's DIAL page answers on `address` (what a phone fetches after the search)."""
    import urllib.request
    try:
        with urllib.request.urlopen("http://%s:%d/ytcr/ssdp/device-desc.xml" % (address, port), timeout=timeout) as r:
            return b"<friendlyName>" in r.read(8192)
    except (OSError, ValueError):
        return False


def firewall(etc="/etc"):
    """The firewall that can hide the TV: "ufw" when Ubuntu/Zorin's firewall is switched on, else None."""
    try:
        if "ENABLED=yes" in (Path(etc) / "ufw/ufw.conf").read_text():
            return "ufw"
    except OSError:
        pass
    return None


def check(data_dir, status, search=ssdp_search, page=device_page, etc="/etc", clock=time.time):
    """Each step a phone goes through to list "Nexus TV": [(ok True/False/None for advice, text)]."""
    data_dir = Path(data_dir)
    state = (status or {}).get("state")
    lines = [(state == "running", "Receiver: " + describe(status))]
    if state != "running":
        if state == "missing":
            lines.append((None, "Run ./install.sh again on this PC to install what is missing."))
        elif state == "off":
            lines.append((None, "Switch \"Cast from YouTube Music\" on."))
        return lines
    try:
        net = json.loads((data_dir / NET_FILE).read_text())
    except (OSError, ValueError):
        net = None
    if not isinstance(net, dict):
        lines.append((None, "This receiver is too old to check: restart casting after updating the TV."))
        return lines
    port = int(net.get("port") or PORT)
    addresses = [a for a in net.get("addresses") or [] if isinstance(a, dict) and a.get("address")]
    if not addresses:
        lines.append((False, "No Wi-Fi or Ethernet address on %s: connect this PC to the phone's Wi-Fi."
                      % (", ".join(net.get("ifaces") or []) or "any network card")))
        return lines
    ours = ["://%s:%d/" % (a["address"], port) for a in addresses]   # Wi-Fi and Ethernet on one subnet: either will do
    for a in addresses:
        addr = a["address"]
        page_ok = page(addr, port)
        found = [loc for loc in search(addr) if any(o in loc for o in ours)]
        if page_ok and found:
            lines.append((True, "Discoverable on %s (%s)" % (addr, a.get("iface"))))
        elif not page_ok:
            lines.append((False, "Its page doesn't answer on %s:%d: restart casting." % (addr, port)))
        else:
            lines.append((False, "It doesn't answer searches on %s (%s): restart casting." % (addr, a.get("iface"))))
    lines.extend(heard_lines(net, port, clock()))
    if firewall(etc) == "ufw":
        lines.append((None, "The firewall is on: phones can't reach the TV unless it allows UDP 1900 and TCP %d "
                            "(sudo ufw allow 1900/udp && sudo ufw allow %d/tcp, or run ./install.sh again)." % (port, port)))
    lines.append((None, "The phone must be on the same Wi-Fi (not a guest network, which hides other devices). "
                        "Still missing? Use Link with TV code in the YouTube app: it works on any network."))
    return lines


def ago(seconds):
    seconds = max(0, int(seconds))
    return "%d s" % seconds if seconds < 90 else "%d min" % (seconds // 60) if seconds < 5400 else "%d h" % (seconds // 3600)


def heard_lines(net, port, now):
    """Did a phone's own search reach this PC? The receiver notes every search from another device
    (lan.mjs noteSearch); searching from the TV itself can't tell."""
    heard = net.get("heard")
    if not isinstance(heard, list):                  # a receiver from before this was kept
        return []
    recent = [h for h in heard if isinstance(h, dict) and now - float(h.get("at") or 0) < 600]
    if recent:
        h = recent[0]
        if not h.get("answered_from"):
            return [(False, "%s searched %s ago but is on another network than this PC: put both on the same Wi-Fi."
                     % (h.get("address"), ago(now - h["at"])))]
        return [(True, "A device at %s searched for TVs %s ago and was answered. Not listed there? The phone "
                       "couldn't open this PC's page (TCP %d, firewall), or the app needs closing and reopening."
                 % (h.get("address"), ago(now - h["at"]), port))]
    since = now - float(net.get("started") or now)
    return [(None, "No phone's search has reached this PC%s. Open the cast menu in the YouTube Music app, then "
                   "check again within a few minutes. Still none: the router keeps Wi-Fi devices apart (client "
                   "isolation, guest network), the phone is on mobile data or a VPN, or the firewall drops "
                   "UDP 1900. Link with TV code works in all of those cases."
             % (" in the %s since casting started" % ago(since) if since > 5 else ""))]


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

    `step(youtube_status, cast)` returns "pause_cast", "pause_youtube" or None and also reports a new
    cast song so the TV can show it (`new_song`)."""

    def __init__(self):
        self.yt_playing = False
        self.cast_key = None          # (id) of the cast song last seen playing
        self.cast_playing = False
        self.new_song = None
        self.pending = None

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
            action = self.pending
            if action is None or cast_started or yt_started:
                action = "pause_youtube" if cast_started and not yt_started else "pause_cast"
        self.pending = action
        self.yt_playing = yt
        self.cast_playing = playing
        return action


def describe(status):
    """One line for Settings from Supervisor.status()."""
    state = (status or {}).get("state")
    if state == "running":
        return "On · Receiver is running"
    if state == "starting":
        return "Starting…"
    if state == "missing":
        return "Not available  ·  missing: " + ", ".join(status.get("missing") or ["?"]) + "  ·  run install.sh again"
    if state == "off":
        return "Off  ·  the TV is not in the cast menu"
    return "Could not reach the controller"


class Supervisor:
    """Keeps receiver.mjs running beside the phone server: restarts it after a crash, with growing pauses."""

    def __init__(self, data_dir, popen=subprocess.Popen):
        self.data_dir = Path(data_dir)
        self.popen = popen
        self.proc = None
        self.stopping = False
        self.thread = None
        self.need = []                 # what was missing at the last start, for Settings
        self.gen = 0                   # each _run belongs to one start; a stop or restart retires it
        self.lock = threading.Lock()   # start/stop/restart come from the watcher and from Settings

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

    # ---- on/off, kept in $DATA/music_cast_service.json so "off" survives a restart of the TV
    def enabled(self):
        try:
            return json.loads((self.data_dir / SERVICE_FILE).read_text()).get("enabled") is not False
        except (OSError, ValueError, AttributeError):
            return True

    def set_enabled(self, on):
        path = self.data_dir / SERVICE_FILE
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps({"enabled": bool(on)}))
        os.replace(tmp, path)

    def running(self):
        proc = self.proc
        return bool(proc and proc.poll() is None)

    def status(self):
        """For Settings on the TV and the phone: {"enabled", "state", "missing"}. state is one of
        running, starting (enabled, the receiver is not up yet), missing (something it needs is not installed), off."""
        on = self.enabled()
        if not on:
            state = "off"
        elif self.running():
            state = "running"
        elif self.need and not self.thread:
            state = "missing"
        else:
            state = "starting"
        return {"enabled": on, "state": state, "missing": list(self.need) if state == "missing" else [], "port": PORT}

    def start(self, force=False):
        """Start the receiver if casting is switched on (force: switch it on first). True when it is starting."""
        with self.lock:
            if force:
                self.set_enabled(True)
            elif not self.enabled():
                LOG.info("Music casting is switched off in Settings")
                return False
            if self.thread and self.thread.is_alive():
                return True
            self.stopping = False
            self.need = missing()
            if self.need:
                LOG.info("Music casting is off; missing: %s", ", ".join(self.need))
                # Re-check every 30 s for 5 minutes: right after boot a slow disk or load can make a present
                # Node look missing (a 2010 iMac took over 5 s to page Node in at login).
                threading.Thread(target=self._retry_start, daemon=True, name="music-cast-retry").start()
                return False
            self._spawn()
            return True

    def _spawn(self):
        self.gen += 1
        self.thread = threading.Thread(target=self._run, args=(self.gen,), daemon=True, name="music-cast")
        self.thread.start()

    def _retry_start(self):
        for _ in range(10):
            time.sleep(30)
            with self.lock:
                if self.stopping or (self.thread and self.thread.is_alive()) or not self.enabled():
                    return
                self.need = missing()
                if not self.need:
                    LOG.info("Music casting dependencies found; starting the receiver")
                    self._spawn()
                    return

    def restart(self):
        self.stop()
        return self.start(force=True)

    def switch_off(self):
        """Settings: stop the receiver and keep it off until it is started again (also after a reboot)."""
        self.set_enabled(False)
        self.stop()

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

    def _run(self, gen=None):
        gen = self.gen if gen is None else gen
        delay = 5.0
        while not self.stopping and gen == self.gen:
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
            if self.stopping or gen != self.gen:
                return
            delay = 5.0 if time.monotonic() - started > 120 else min(delay * 2, 300.0)
            LOG.warning("Music receiver exited (%s); restarting in %.0f s", code, delay)
            waited = 0.0
            while waited < delay and not self.stopping and gen == self.gen:     # a stop or restart ends the wait
                time.sleep(0.5)
                waited += 0.5

    def stop(self):
        """Stop the receiver and its mpv (it starts again with start() or the next start of the TV)."""
        with self.lock:
            self.stopping = True
            self.gen += 1
            self.thread = None
        proc = self.proc
        if proc and proc.poll() is None:
            proc.terminate()
            try:
                proc.wait(timeout=4)
            except subprocess.TimeoutExpired:
                proc.kill()
        self.kill_stale()              # its mpv, if the receiver died before stopping it
