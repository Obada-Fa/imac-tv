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
    for key, prop in (("can_next", "CanGoNext"), ("can_prev", "CanGoPrevious"), ("can_pause", "CanPause")):
        if prop in props:
            state[key] = bool(props[prop])
    return state


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
            out.append((name, parse_player(name, str(identity), props)))
        except Exception:
            continue
    return out


COMMANDS = {"play_pause": "PlayPause", "next": "Next", "previous": "Previous"}


def control(cmd):
    """Send play_pause / next / previous to the MPRIS player current() would pick."""
    method = COMMANDS.get(cmd)
    if not method:
        raise ValueError("Unknown media command")
    try:
        named = [(n, st) for n, st in _mpris_named() if st]
    except Exception as exc:
        raise RuntimeError("Can't reach the media bus") from exc
    named.sort(key=lambda item: item[1]["status"] != "Playing")
    if not named:
        raise RuntimeError("YouTube isn't publishing media controls")
    Gio, GLib, bus = _bus()
    bus.call_sync(named[0][0], "/org/mpris/MediaPlayer2", PLAYER_IFACE, method, None, None,
                  Gio.DBusCallFlags.NONE, 1500, None)
    return True


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
