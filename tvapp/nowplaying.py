"""What is YouTube (VacuumTube / Chromium / Electron) doing in the background? MPRIS on the session bus first,
then a plain window lookup. Pure parsing/matching is separate from the D-Bus calls so it can be tested."""
import os
import subprocess

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
    IMACTV_FAKE_NOWPLAYING=<json> short-circuits for screenshots/tests."""
    fake = os.environ.get("IMACTV_FAKE_NOWPLAYING")
    if fake:
        import json
        try:
            return json.loads(fake) or None
        except ValueError:
            return None
    try:
        best = pick_best(_mpris_players())
    except Exception:
        best = None
    return best or window_fallback(_window_lines())
