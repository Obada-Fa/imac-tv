"""What is YouTube (VacuumTube / Chromium / Electron) doing in the background? MPRIS on the session bus first,
then a plain window lookup. Pure parsing/matching is separate from the D-Bus calls so it can be tested."""
import json
import os
import re
import subprocess
import threading
import time
from collections import OrderedDict

MPRIS = "org.mpris.MediaPlayer2"
PLAYER_IFACE = "org.mpris.MediaPlayer2.Player"
BROWSERISH = ("chromium", "electron", "chrome")


def looks_like_youtube(bus_name, identity, meta):
    """Does this MPRIS player look like the TV's YouTube app?"""
    name = f"{bus_name} {identity}".lower()
    url = str((meta or {}).get("xesam:url") or "").lower()
    if "youtube" in url or "youtu.be" in url:
        return True
    if "vacuumtube" in name:
        return True
    # a generic browser/Electron player only counts when it isn't clearly on another site
    return any(k in name for k in BROWSERISH) and not url


def parse_player(bus_name, identity, props):
    """props: unpacked org.mpris.MediaPlayer2.Player properties -> now-playing dict, or None if not YouTube."""
    meta = props.get("Metadata") or {}
    status = props.get("PlaybackStatus") or ""
    if status not in ("Playing", "Paused") or not looks_like_youtube(bus_name, identity, meta):
        return None
    artist = meta.get("xesam:artist")
    if isinstance(artist, (list, tuple)):
        artist = ", ".join(str(a) for a in artist if a)
    title = str(meta.get("xesam:title") or "").strip() or "YouTube"
    state = {"status": status, "title": title, "artist": str(artist or "").strip(),
             "art": str(meta.get("mpris:artUrl") or "") or None, "source": "youtube"}
    for key, prop in (("can_next", "CanGoNext"), ("can_prev", "CanGoPrevious"), ("can_pause", "CanPause"),
                      ("can_seek", "CanSeek")):
        if prop in props:
            state[key] = bool(props[prop])
    length = _seconds(meta.get("mpris:length"))
    if length:
        state["length"] = length
    position = _seconds(props.get("Position"))
    if position is not None:
        state["position"] = min(position, length) if length else position
    if meta.get("mpris:trackid"):
        state["track_id"] = str(meta["mpris:trackid"])
    if meta.get("xesam:url"):
        state["url"] = str(meta["xesam:url"])
    return state


def _seconds(micro):
    """MPRIS microseconds -> seconds (None when missing/invalid)."""
    if isinstance(micro, bool) or not isinstance(micro, (int, float)) or micro < 0:
        return None
    return round(micro / 1e6, 2)


def clamp(value, low, high):
    return max(low, min(high, value))


def seek_micros(offset):
    """Relative seek in seconds -> microseconds, limited to +-1 hour."""
    return int(clamp(float(offset), -3600, 3600) * 1e6)


def position_micros(position, length=None):
    """Absolute position in seconds -> microseconds, kept inside the track when its length is known."""
    return int(clamp(float(position), 0, length if length else 86400) * 1e6)


def pick_best(candidates):
    """Prefer a Playing player over a Paused one."""
    cands = [c for c in candidates if c]
    cands.sort(key=lambda c: c["status"] != "Playing")
    return cands[0] if cands else None


def window_fallback(lines):
    """`wmctrl -lx` output -> {"status": "Open", ...} when a VacuumTube window exists."""
    for line in lines:
        low = line.lower()
        if "vacuumtube" in low or "youtube" in low:
            return {"status": "Open", "title": "YouTube", "artist": "", "art": None, "source": "youtube"}
    return None


def _bus():
    import gi
    gi.require_version("Gio", "2.0")
    from gi.repository import Gio, GLib
    # User services normally inherit the session bus address; fall back to the standard socket if not.
    if not os.environ.get("DBUS_SESSION_BUS_ADDRESS"):
        sock = f"/run/user/{os.getuid()}/bus"
        if os.path.exists(sock):
            os.environ["DBUS_SESSION_BUS_ADDRESS"] = "unix:path=" + sock
    return Gio, GLib, Gio.bus_get_sync(Gio.BusType.SESSION, None)


def _mpris_players():
    return [state for _name, state in _mpris_named()]


def _mpris_named():
    """[(bus_name, state-or-None)] for every MPRIS player."""
    Gio, GLib, bus = _bus()

    def call(dest, path, iface, method, args, reply):
        return bus.call_sync(dest, path, iface, method, args, GLib.VariantType(reply),
                             Gio.DBusCallFlags.NONE, 800, None).unpack()
    names = call("org.freedesktop.DBus", "/org/freedesktop/DBus", "org.freedesktop.DBus", "ListNames", None, "(as)")[0]
    out = []
    for name in names:
        if not name.startswith(MPRIS + "."):
            continue
        try:
            (identity,) = call(name, "/org/mpris/MediaPlayer2", "org.freedesktop.DBus.Properties", "Get",
                               GLib.Variant("(ss)", (MPRIS, "Identity")), "(v)")
            (props,) = call(name, "/org/mpris/MediaPlayer2", "org.freedesktop.DBus.Properties", "GetAll",
                            GLib.Variant("(s)", (PLAYER_IFACE,)), "(a{sv})")
            try:  # Position isn't signalled, so ask for it explicitly every time
                (pos,) = call(name, "/org/mpris/MediaPlayer2", "org.freedesktop.DBus.Properties", "Get",
                              GLib.Variant("(ss)", (PLAYER_IFACE, "Position")), "(v)")
                props["Position"] = pos
            except Exception:
                props.pop("Position", None)
            out.append((name, parse_player(name, str(identity), props)))
        except Exception:
            continue
    return out


COMMANDS = {"play_pause": "PlayPause", "next": "Next", "previous": "Previous", "seek": "Seek", "set_position": "SetPosition"}


def control(cmd, value=None):
    """Send a command to the MPRIS player current() would pick.
    seek: value = relative seconds; set_position: value = absolute seconds."""
    method = COMMANDS.get(cmd)
    if not method:
        raise ValueError("Unknown media command")
    if cmd in ("seek", "set_position") and (isinstance(value, bool) or not isinstance(value, (int, float))):
        raise ValueError("Seconds required")
    try:
        named = [(n, st) for n, st in _mpris_named() if st]
    except Exception as exc:
        raise RuntimeError("Can't reach the media bus") from exc
    named.sort(key=lambda item: item[1]["status"] != "Playing")
    if not named:
        raise RuntimeError("YouTube isn't publishing media controls")
    name, state = named[0]
    Gio, GLib, bus = _bus()
    args = None
    if cmd == "seek":
        args = GLib.Variant("(x)", (seek_micros(value),))
    elif cmd == "set_position":
        if not state.get("track_id"):
            raise RuntimeError("This player can't jump to a position")
        args = GLib.Variant("(ox)", (state["track_id"], position_micros(value, state.get("length"))))
    bus.call_sync(name, "/org/mpris/MediaPlayer2", PLAYER_IFACE, method, args, None,
                  Gio.DBusCallFlags.NONE, 1500, None)
    return True


def select_playing(rows, exclude=()):
    """rows: [(bus_name, identity, status)] -> bus names to pause: every Playing player except mpv's own
    and anything whose bus name or identity contains one of the `exclude` words."""
    skip = ("mpv",) + tuple(str(e).lower() for e in exclude if e)
    return [name for name, identity, status in rows
            if status == "Playing" and not any(k in f"{name} {identity}".lower() for k in skip)]


def _playing_rows():
    Gio, GLib, bus = _bus()

    def get(dest, iface, prop):
        return bus.call_sync(dest, "/org/mpris/MediaPlayer2", "org.freedesktop.DBus.Properties", "Get",
                             GLib.Variant("(ss)", (iface, prop)), GLib.VariantType("(v)"),
                             Gio.DBusCallFlags.NONE, 800, None).unpack()[0]
    names = bus.call_sync("org.freedesktop.DBus", "/org/freedesktop/DBus", "org.freedesktop.DBus", "ListNames",
                          None, GLib.VariantType("(as)"), Gio.DBusCallFlags.NONE, 800, None).unpack()[0]
    rows = []
    for name in names:
        if not name.startswith(MPRIS + "."):
            continue
        try:
            rows.append((name, str(get(name, MPRIS, "Identity")), str(get(name, PLAYER_IFACE, "PlaybackStatus"))))
        except Exception:
            continue
    return rows


def pause_all_playing(exclude=()):
    """Pause every MPRIS player that is Playing (mpv's own excluded). Returns the bus names paused."""
    Gio, GLib, bus = _bus()
    paused = []
    for name in select_playing(_playing_rows(), exclude):
        try:
            bus.call_sync(name, "/org/mpris/MediaPlayer2", PLAYER_IFACE, "Pause", None, None,
                          Gio.DBusCallFlags.NONE, 1500, None)
            paused.append(name)
        except Exception:
            continue
    return paused


def paused_close_step(since, status, now, minutes):
    """One tick of the close-after-paused timer. since = monotonic time the player became Paused (or None).
    Returns (new_since, close_now). minutes <= 0 disables it."""
    if status != "Paused" or not minutes or minutes <= 0:
        return None, False
    if since is None:
        return now, False
    return since, now - since >= minutes * 60


def _window_lines():
    try:
        return subprocess.run(["wmctrl", "-lx"], capture_output=True, text=True, timeout=2).stdout.splitlines()
    except Exception:
        return []


def current():
    """Blocking (call from a worker thread). Dict or None.
    IMACTV_FAKE_NOWPLAYING=<json> (or @<path to a json file>, re-read each call so a smoke run can change
    tracks) short-circuits for screenshots/tests. It may carry title/artist/status/art/url."""
    fake = os.environ.get("IMACTV_FAKE_NOWPLAYING")
    if fake:
        try:
            if fake.startswith("@"):
                with open(fake[1:]) as fh:
                    fake = fh.read()
            return json.loads(fake) or None
        except (OSError, ValueError):
            return None
    try:
        best = pick_best(_mpris_players())
    except Exception:
        best = None
    return best or window_fallback(_window_lines())


# ---------------------------------------------------------------- music or video?
# VacuumTube is Electron, so its MPRIS comes from Chromium's MediaSession bridge: xesam:title, xesam:artist
# (the channel), xesam:album, mpris:artUrl, mpris:length and a generated mpris:trackid. Chromium publishes
# no xesam:url, so the YouTube category lookup only runs when some build does (or the art URL names a video).

MUSIC_TITLE = re.compile(r"official\s+audio|\(\s*audio\s*\)|\[\s*audio\s*\]|lyric\s+video|\blyrics?\b|"
                         r"visuali[sz]er|full\s+album", re.I)
MODES = ("music", "video")
MODES_CAP = 200
VIDEO_ID = re.compile(r"(?:[?&]v=|youtu\.be/|/vi(?:_webp)?/|/embed/|/shorts/)([A-Za-z0-9_-]{11})")


def artist_key(artist):
    """The per-channel override key: case and spacing folded."""
    return " ".join(str(artist or "").lower().split())


def track_key(state):
    """Identity of the current track; a change means classify again."""
    if not state or state.get("status") not in ("Playing", "Paused"):
        return None
    if (not state.get("title") or state.get("title") == "YouTube") and not (state.get("artist") or video_id(state)):
        return None
    return "\x1f".join((str(state.get("title") or ""), str(state.get("artist") or ""), video_id(state) or ""))


def video_id(state):
    """The YouTube video id from xesam:url or an i.ytimg.com art URL, else None."""
    for key in ("url", "art"):
        m = VIDEO_ID.search(str((state or {}).get(key) or ""))
        if m:
            return m.group(1)
    return None


def is_square(size, tolerance=0.06):
    """(w, h) of the art -> True for album-art squares, False for 16:9/4:3 thumbnails, None if unknown."""
    try:
        w, h = size
        if w <= 0 or h <= 0:
            return None
    except (TypeError, ValueError):
        return None
    return abs(w / h - 1.0) <= tolerance


def classify(meta, art_size=None, category=None, overrides=None):
    """-> ("music" | "video", reason). meta: the now-playing dict (title, artist). An override for the
    channel beats every heuristic; anything without a music signal is a video."""
    meta = meta or {}
    artist, title = str(meta.get("artist") or "").strip(), str(meta.get("title") or "")
    forced = (overrides or {}).get(artist_key(artist or title))
    if forced in MODES:
        return forced, "override"
    if artist.lower().endswith(" - topic"):
        return "music", "topic channel"
    m = MUSIC_TITLE.search(title)
    if m:
        return "music", "title: " + " ".join(m.group(0).lower().strip("()[] ").split())
    if is_square(art_size):
        return "music", "square art"
    if str(category or "").strip().lower() == "music":
        return "music", "category Music"
    return "video", "no music signal"


def image_size(data):
    """(w, h) from the first bytes of a PNG, JPEG, GIF or WebP, else None."""
    if not data:
        return None
    if data[:8] == b"\x89PNG\r\n\x1a\n" and len(data) >= 24:
        return int.from_bytes(data[16:20], "big"), int.from_bytes(data[20:24], "big")
    if data[:6] in (b"GIF87a", b"GIF89a") and len(data) >= 10:
        return int.from_bytes(data[6:8], "little"), int.from_bytes(data[8:10], "little")
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP" and len(data) >= 30:
        kind = data[12:16]
        if kind == b"VP8X":
            return 1 + int.from_bytes(data[24:27], "little"), 1 + int.from_bytes(data[27:30], "little")
        if kind == b"VP8 ":
            return int.from_bytes(data[26:28], "little") & 0x3FFF, int.from_bytes(data[28:30], "little") & 0x3FFF
        if kind == b"VP8L":
            b = data[21:25]
            return 1 + (((b[1] & 0x3F) << 8) | b[0]), 1 + (((b[3] & 0x0F) << 10) | (b[2] << 2) | (b[1] >> 6))
        return None
    if data[:2] == b"\xff\xd8":
        i = 2
        while i + 9 < len(data):
            if data[i] != 0xFF:
                i += 1
                continue
            marker = data[i + 1]
            if marker in (0xD8, 0x01) or 0xD0 <= marker <= 0xD7 or marker == 0xFF:
                i += 1 if marker == 0xFF else 2
                continue
            seg = int.from_bytes(data[i + 2:i + 4], "big")
            if 0xC0 <= marker <= 0xCF and marker not in (0xC4, 0xC8, 0xCC):
                return int.from_bytes(data[i + 7:i + 9], "big"), int.from_bytes(data[i + 5:i + 7], "big")
            i += 2 + seg
    return None


CATEGORY = re.compile(rb'"category"\s*:\s*"([^"]{1,40})"')


def _read_head(url, limit=65536, timeout=3.0):
    """The first bytes of a file:// path or http(s) URL (art headers sit at the start)."""
    if url.startswith("file://"):
        from urllib.parse import unquote, urlsplit
        with open(unquote(urlsplit(url).path), "rb") as fh:
            return fh.read(limit)
    if url.startswith("/"):
        with open(url, "rb") as fh:
            return fh.read(limit)
    if not url.startswith(("http://", "https://")):
        return b""
    import urllib.request
    req = urllib.request.Request(url, headers={"Range": f"bytes=0-{limit - 1}", "User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.read(limit)


def fetch_category(vid, timeout=4.0, limit=1_500_000):
    """YouTube's category for a video id from its watch page ("Music", "Gaming", ...), or None."""
    import urllib.request
    req = urllib.request.Request(f"https://www.youtube.com/watch?v={vid}",
                                 headers={"User-Agent": "Mozilla/5.0", "Accept-Language": "en"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        body = resp.read(limit)
    m = CATEGORY.search(body)
    return m.group(1).decode("utf-8", "replace") if m else None


class _Cache:
    """Small LRU of finished lookups plus the set still running in a worker thread."""

    def __init__(self, cap=256):
        self.cap, self.done, self.running = cap, OrderedDict(), set()

    def get(self, key):
        if key in self.done:
            self.done.move_to_end(key)
            return True, self.done[key]
        return False, None

    def put(self, key, value):
        self.done[key] = value
        self.done.move_to_end(key)
        while len(self.done) > self.cap:
            self.done.popitem(last=False)


class Classifier:
    """classify() plus the slow signals: art size per art URL and category per video id, each fetched once,
    off the caller's thread. mode(state) never blocks; `pending` says a slow answer may still change it."""

    def __init__(self, read_head=_read_head, category=fetch_category, spawn=None):
        self.read_head, self.category = read_head, category
        self.spawn = spawn or (lambda fn: threading.Thread(target=fn, daemon=True).start())
        self.lock = threading.Lock()
        self.arts, self.cats = _Cache(), _Cache()

    def _lookup(self, cache, key, work):
        with self.lock:
            hit, value = cache.get(key)
            if hit or key in cache.running:
                return hit, value
            cache.running.add(key)

        def run():
            try:
                value = work()
            except Exception:
                value = None
            with self.lock:
                cache.running.discard(key)
                cache.put(key, value)
        self.spawn(run)
        with self.lock:      # a synchronous spawn (tests) has already finished
            return cache.get(key)

    def mode(self, state, overrides=None):
        """-> (mode, reason, pending)."""
        quick = classify(state, overrides=overrides)
        if quick[0] == "music" or quick[1] == "override":
            return quick + (False,)
        pending, size, cat = False, None, None
        art = str((state or {}).get("art") or "")
        if art:
            done, size = self._lookup(self.arts, art, lambda: image_size(self.read_head(art)))
            pending |= not done
        vid = video_id(state)
        if vid:
            done, cat = self._lookup(self.cats, vid, lambda: self.category(vid))
            pending |= not done
        return classify(state, art_size=size, category=cat, overrides=overrides) + (pending,)


def load_modes(data_dir):
    """home.json youtube_modes: {artist_key: "music"|"video"}."""
    try:
        with open(os.path.join(str(data_dir), "home.json")) as fh:
            raw = json.load(fh)
    except (OSError, ValueError):
        return {}
    modes = raw.get("youtube_modes") if isinstance(raw, dict) else None
    if not isinstance(modes, dict):
        return {}
    return {str(k): v for k, v in modes.items() if v in MODES}


def remember_mode(modes, artist, mode, cap=MODES_CAP):
    """New overrides dict with artist's choice moved to the end (newest); the oldest drop beyond cap."""
    key = artist_key(artist)
    out = OrderedDict((k, v) for k, v in (modes or {}).items() if v in MODES and k != key)
    if key and mode in MODES:
        out[key] = mode
    while len(out) > cap:
        out.popitem(last=False)
    return dict(out)


_modes_lock = threading.Lock()


def save_mode(data_dir, artist, mode, cap=MODES_CAP):
    """Remember artist -> mode in home.json, keeping every other key. Returns the new overrides."""
    path = os.path.join(str(data_dir), "home.json")
    with _modes_lock:
        try:
            with open(path) as fh:
                raw = json.load(fh)
        except (OSError, ValueError):
            raw = {}
        if not isinstance(raw, dict):
            raw = {}
        current = raw.get("youtube_modes") if isinstance(raw.get("youtube_modes"), dict) else {}
        raw["youtube_modes"] = remember_mode(current, artist, mode, cap)
        tmp = f"{path}.{os.getpid()}.tmp"
        with open(tmp, "w") as fh:
            json.dump(raw, fh, indent=2)
        os.replace(tmp, path)
        return raw["youtube_modes"]


# The server classifies (it owns the slow lookups and the pop-up); Home reads the answer from this file.
MODE_FILE = "youtube_mode.json"


def write_mode_file(data_dir, key, mode, reason):
    path = os.path.join(str(data_dir), MODE_FILE)
    tmp = f"{path}.{os.getpid()}.tmp"
    with open(tmp, "w") as fh:
        json.dump({"key": key, "mode": mode, "reason": reason, "ts": time.time()}, fh)
    os.replace(tmp, path)


def read_mode(data_dir, state):
    """(mode, reason) the server decided for this track, else the quick local guess (no network)."""
    key = track_key(state)
    if not key:
        return None, None
    try:
        with open(os.path.join(str(data_dir), MODE_FILE)) as fh:
            saved = json.load(fh)
        if saved.get("key") == key and saved.get("mode") in MODES:
            return saved["mode"], saved.get("reason")
    except (OSError, ValueError, AttributeError):
        pass
    return classify(state, overrides=load_modes(data_dir))


def pop_up(mode, key, *, app, status, movie, screen_live, dismissed, popped, pending=False):
    """Should VacuumTube come up fullscreen now? Only for a playing video, decided (no lookup pending), while
    Home is in front, no movie plays and the screen is on, and not again for a track already shown or one the
    user sent back to Home."""
    return bool(mode == "video" and key and not pending and app == "home" and status == "Playing"
                and not movie and screen_live and key != dismissed and key != popped)
