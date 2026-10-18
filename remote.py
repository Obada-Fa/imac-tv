#!/usr/bin/env python3
"""
iMac TV Remote - control this machine's X11 desktop from a phone browser.

  * No xdotool. Key/pointer injection goes straight to libX11 + libXtst
    through ctypes, both already installed. Nothing needs sudo.
  * A shared secret token, or the server returns 403.
  * TARGET WINDOW: XTest delivers to whatever has *real X input focus*, which
    is often a terminal or browser rather than the app you are watching. So
    pressing STREMIO or YOUTUBE sets a target, and every key/pointer action is
    delivered to that target explicitly. This is what makes it behave like a
    TV remote instead of a keyboard that vanishes into the wrong window.
  * Touchpad (relative pointer, click, right-click, scroll) and an on-screen
    keyboard plus a "type this text" box.
"""

import atexit
import ctypes
import html
import json
import os
import secrets
import subprocess
import sys
import threading
import time
import urllib.parse
from http.cookies import SimpleCookie
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

PORT = 8765
HERE = os.path.dirname(os.path.abspath(__file__))
SECRET_FILE = os.path.join(HERE, "secret.txt")
COOKIE = "tvremote"
DISPLAY = os.environ.get("DISPLAY", ":0")
SCALE = 2.5          # phone pixels -> screen pixels for the touchpad

# ----------------------------------------------------------------- X backend

_x11 = ctypes.CDLL("libX11.so.6")
_xtst = ctypes.CDLL("libXtst.so.6")

# libX11 is NOT thread-safe. This server is multi-threaded (one HTTP handler
# thread per request, plus the background app-focus thread), so this call is
# mandatory and must happen before any other Xlib call, including
# XOpenDisplay. Without it, concurrent X calls segfault the process.
_x11.XInitThreads.restype = ctypes.c_int
_x11.XInitThreads.argtypes = []
if _x11.XInitThreads() == 0:
    sys.exit("tvremote: XInitThreads() failed - Xlib is not thread-safe here")

# Install a safe X error handler so BadWindow / BadMatch on unmapped or closing
# windows does not terminate the daemon via Xlib's default exit(1).
_X_ERR_HANDLER = ctypes.CFUNCTYPE(ctypes.c_int, ctypes.c_void_p, ctypes.c_void_p)
def _x_error_handler(display_ptr, error_event_ptr):
    return 0
_c_x_error_handler = _X_ERR_HANDLER(_x_error_handler)
_x11.XSetErrorHandler.argtypes = [ctypes.c_void_p]
_x11.XSetErrorHandler.restype = ctypes.c_void_p
_x11.XSetErrorHandler(_c_x_error_handler)

_x11.XOpenDisplay.restype = ctypes.c_void_p
_x11.XOpenDisplay.argtypes = [ctypes.c_char_p]
_x11.XCloseDisplay.argtypes = [ctypes.c_void_p]
_x11.XStringToKeysym.restype = ctypes.c_ulong
_x11.XStringToKeysym.argtypes = [ctypes.c_char_p]
_x11.XKeysymToKeycode.restype = ctypes.c_ubyte
_x11.XKeysymToKeycode.argtypes = [ctypes.c_void_p, ctypes.c_ulong]
_x11.XFlush.argtypes = [ctypes.c_void_p]
_x11.XSetInputFocus.argtypes = [ctypes.c_void_p, ctypes.c_ulong,
                                ctypes.c_int, ctypes.c_ulong]
_x11.XGetInputFocus.argtypes = [ctypes.c_void_p, ctypes.POINTER(ctypes.c_ulong)]
_x11.XQueryPointer.argtypes = [ctypes.c_void_p, ctypes.c_void_p] + \
    [ctypes.POINTER(ctypes.c_ulong)] * 2 + [ctypes.POINTER(ctypes.c_int)] * 4 + \
    [ctypes.POINTER(ctypes.c_uint)]
_x11.XQueryPointer.restype = ctypes.c_int
_x11.XDefaultRootWindow.restype = ctypes.c_ulong
_x11.XDefaultRootWindow.argtypes = [ctypes.c_void_p]
_x11.XDefaultScreen.restype = ctypes.c_int
_x11.XDefaultScreen.argtypes = [ctypes.c_void_p]
_x11.XDisplayWidth.restype = ctypes.c_int
_x11.XDisplayWidth.argtypes = [ctypes.c_void_p, ctypes.c_int]
_x11.XDisplayHeight.restype = ctypes.c_int
_x11.XDisplayHeight.argtypes = [ctypes.c_void_p, ctypes.c_int]
_x11.XWarpPointer.argtypes = [ctypes.c_void_p, ctypes.c_void_p,
                               ctypes.c_void_p, ctypes.c_int, ctypes.c_int,
                               ctypes.c_uint, ctypes.c_uint, ctypes.c_int,
                               ctypes.c_int]
_xtst.XTestFakeKeyEvent.argtypes = [ctypes.c_void_p, ctypes.c_uint,
                                    ctypes.c_int, ctypes.c_ulong]
_xtst.XTestFakeMotionEvent.argtypes = [ctypes.c_void_p, ctypes.c_int,
                                       ctypes.c_int, ctypes.c_int, ctypes.c_ulong]
_xtst.XTestFakeButtonEvent.argtypes = [ctypes.c_void_p, ctypes.c_uint,
                                       ctypes.c_int, ctypes.c_ulong]

_dpy = None
# Reentrant: _deliver() holds the lock and then calls helpers that also
# take it (e.g. move_pointer -> _hide_cursor).
_lock = threading.RLock()


def display():
    global _dpy
    if _dpy is None:
        _dpy = _x11.XOpenDisplay(DISPLAY.encode())
        if not _dpy:
            sys.exit("tvremote: cannot open X display %r" % DISPLAY)
    return _dpy


@atexit.register
def _close():
    # Without this, interpreter shutdown segfaults inside libX11.
    if _dpy:
        try:
            _x11.XCloseDisplay(_dpy)
        except Exception:
            pass


def _keysym(name):
    return _x11.XStringToKeysym(name.encode())


def _keycode(name):
    ks = _keysym(name)
    return 0 if ks == 0 else _x11.XKeysymToKeycode(display(), ks)


def tap(name, modifier=None, hold=0.045):
    d = display()
    kc = _keycode(name)
    if not kc:
        raise ValueError("no keycode for %r" % name)
    mkc = _keycode(modifier) if modifier else 0
    if mkc:
        _xtst.XTestFakeKeyEvent(d, mkc, 1, 0)
    _xtst.XTestFakeKeyEvent(d, kc, 1, 0)
    _x11.XFlush(d)
    time.sleep(hold)
    _xtst.XTestFakeKeyEvent(d, kc, 0, 0)
    if mkc:
        _xtst.XTestFakeKeyEvent(d, mkc, 0, 0)
    _x11.XFlush(d)


# shift-state characters: keysym -> needs Shift_L to type correctly
_SHIFTED = set("~!@#$%^&*()_+{}|:\"<>?ABCDEFGHIJKLMNOPQRSTUVWXYZ")


def type_text(text):
    """Type a literal string one character at a time."""
    for ch in text:
        if ch == "\n":
            tap("Return")
            continue
        if ch == "\t":
            tap("Tab")
            continue
        if ch == " ":
            tap("space")
            continue
        ks = _keysym(ch)
        if ks == 0:
            continue
        kc = _x11.XKeysymToKeycode(display(), ks)
        if not kc:
            continue
        if ch in _SHIFTED:
            tap(ch.lower() if ch.isalpha() else ch, modifier="Shift_L")
        else:
            tap(ch)
        time.sleep(0.012)


# ------------------------------------------------------------------ pointer
#
# NOTE: hiding the cursor during touchpad use was tried and removed. Building
# an invisible cursor needs XCreatePixmap/XCreatePixmapCursor, and XDefaultDepth
# returns 0 through ctypes on this box, so the server rejects the depth-0
# pixmap with BadValue. libXlib's default error handler then calls exit() from
# a worker thread, which takes the whole service down with SIGSEGV. Not worth
# it for a cosmetic touch; the pointer stays visible, which is useful
# feedback anyway.


def _screen_size():
    d = display()
    s = _x11.XDefaultScreen(d)
    return _x11.XDisplayWidth(d, s), _x11.XDisplayHeight(d, s)


def _pointer_pos():
    d = display()
    with _lock:
        root = _x11.XDefaultRootWindow(d)
        rr = ctypes.c_ulong(); cr = ctypes.c_ulong()
        rx = ctypes.c_int(); ry = ctypes.c_int()
        wx = ctypes.c_int(); wy = ctypes.c_int()
        mask = ctypes.c_uint()
        _x11.XQueryPointer(d, root, ctypes.byref(rr), ctypes.byref(cr),
                           ctypes.byref(rx), ctypes.byref(ry),
                           ctypes.byref(wx), ctypes.byref(wy),
                           ctypes.byref(mask))
    return rx.value, ry.value


def move_pointer(dx, dy):
    """Relative pointer motion: read where it is, step it, put it back."""
    d = display()
    w, h = _screen_size()
    x, y = _pointer_pos()
    nx = max(0, min(w - 1, x + int(dx * SCALE)))
    ny = max(0, min(h - 1, y + int(dy * SCALE)))
    screen = _x11.XDefaultScreen(d)
    _xtst.XTestFakeMotionEvent(d, screen, nx, ny, 0)
    _x11.XFlush(d)


def click(button=1, hold=0.06):
    d = display()
    _xtst.XTestFakeButtonEvent(d, button, 1, 0)
    _x11.XFlush(d)
    time.sleep(hold)
    _xtst.XTestFakeButtonEvent(d, button, 0, 0)
    _x11.XFlush(d)


def scroll(direction):
    click(4 if direction > 0 else 5, hold=0.03)


# -------------------------------------------------------------- target window

_target = None          # window id the remote is currently driving
_target_title = ""
_focus_wanted = threading.Event()


def _set_input_focus(win):
    d = display()
    # RevertToPointerRoot = 2: if the window dies, focus falls to the pointer
    # root instead of getting stuck on a dead window.
    _x11.XSetInputFocus(d, win, 2, 0)
    _x11.XFlush(d)


def _apply_target():
    """Give the target window the real X input focus, if we have a target."""
    if _target is None:
        return False
    try:
        _set_input_focus(_target)
        return True
    except Exception:
        return False


def _deliver(fn):
    """Run fn() against the target window (falling back to current focus)."""
    with _lock:
        _apply_target()
        fn()


def windows():
    try:
        out = subprocess.run(["wmctrl", "-l"], capture_output=True,
                             text=True, timeout=5).stdout
    except Exception:
        return []
    result = []
    for line in out.splitlines():
        parts = line.split(None, 2)
        if len(parts) == 3:
            result.append((int(parts[0], 16), parts[2]))
    return result


def find_window(match):
    for wid, title in windows():
        if match.lower() in title.lower():
            return wid, title
    return None, ""


def set_target(match, launch_cmd=None):
    """Focus the window whose title contains `match` and make it the target.

    If it is not running, launch it and return immediately; a background
    thread picks up the window once it appears, so the phone button never
    hangs waiting.
    """
    global _target, _target_title
    wid, title = find_window(match)
    if wid is not None:
        _target, _target_title = wid, title
        shell("wmctrl -i -a %d" % wid)
        shell("wmctrl -i -r %d -b add,fullscreen" % wid)
        _apply_target()
        return True

    if launch_cmd:
        shell(launch_cmd)

        def _wait_and_focus():
            for _ in range(30):
                time.sleep(1)
                w, t = find_window(match)
                if w is not None:
                    _set_target_quiet(w, t)
                    return
        threading.Thread(target=_wait_and_focus, daemon=True).start()
    return False


def _set_target_quiet(wid, title):
    global _target, _target_title
    _target, _target_title = wid, title
    shell("wmctrl -i -a %d" % wid)
    shell("wmctrl -i -r %d -b add,fullscreen" % wid)
    _apply_target()


# ------------------------------------------------------------- sleep timer
#
# A TV box that stays on all night is a waste, and getting up to switch it
# off is worse. The phone can arm a countdown, change it, or cancel it.
#
# Powering off needs root. Rather than let polkit throw a password dialog
# onto the TV at the moment the timer fires (awful mid-film), there is one
# narrow sudoers grant: exactly `systemctl poweroff` and `systemctl suspend`,
# no wildcards. See /etc/sudoers.d/zz-tvremote-power.

TIMER_FILE = os.path.join(HERE, "timer.json")
DRYRUN_FILE = os.path.expanduser("~/.local/share/tvremote-dryrun")
TIMER_MODES = ("off", "suspend")
# The UI talks in modes ("off"), systemctl wants actions ("poweroff").
# Getting this mapping wrong meant a POWER OFF timer silently did nothing.
MODE_ACTION = {"off": "poweroff", "suspend": "suspend"}

_timer_lock = threading.Lock()
_timer = {"deadline": None, "mode": "off"}


def _power(action):
    """Run systemctl poweroff/suspend. Returns True if it was really run."""
    if action not in ("poweroff", "suspend"):
        raise ValueError("refusing unknown power action %r" % action)
    if os.path.exists(DRYRUN_FILE):
        with open(os.path.join(HERE, "power.log"), "a") as fh:
            fh.write("%s DRYRUN systemctl %s\n" % (time.strftime("%F %T"), action))
        return False
    subprocess.Popen(["sudo", "-n", "/usr/bin/systemctl", action],
                     stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                     start_new_session=True)
    return True


def _timer_load():
    try:
        with open(TIMER_FILE) as fh:
            data = json.load(fh)
        if isinstance(data.get("deadline"), (int, float)):
            _timer["deadline"] = float(data["deadline"])
        if data.get("mode") in TIMER_MODES:
            _timer["mode"] = data["mode"]
    except (OSError, ValueError):
        pass


def _timer_save():
    tmp = TIMER_FILE + ".tmp"
    with open(tmp, "w") as fh:
        json.dump(_timer, fh)
    os.replace(tmp, TIMER_FILE)


def timer_cmd(p):
    """set / extend / shorten / clear the countdown, and pick the mode."""
    with _timer_lock:
        if p.get("mode") in TIMER_MODES:
            _timer["mode"] = p["mode"]
        if p.get("clear"):
            _timer["deadline"] = None
        elif p.get("t") is not None:
            mins = max(1.0, float(p["t"]))
            _timer["deadline"] = time.time() + mins * 60
        elif p.get("add") is not None:
            mins = float(p["add"])
            base = _timer["deadline"] or time.time()
            _timer["deadline"] = max(time.time() + 30, base + mins * 60)
        _timer_save()
        return dict(_timer)


def timer_info():
    with _timer_lock:
        dl = _timer["deadline"]
        if not dl:
            return {"active": False, "mode": _timer["mode"]}
        return {"active": True, "deadline": dl, "mode": _timer["mode"],
                "remaining": max(0.0, dl - time.time())}


def _timer_loop():
    while True:
        time.sleep(1)
        action = None
        with _timer_lock:
            dl = _timer["deadline"]
            if dl and time.time() >= dl:
                action = MODE_ACTION.get(_timer["mode"])
                _timer["deadline"] = None
                _timer_save()
        if action:
            try:
                _power(action)
            except Exception as exc:                    # noqa: BLE001
                with open(os.path.join(HERE, "error.log"), "a") as fh:
                    fh.write("timer %r -> %r\n" % (action, exc))


# ------------------------------------------------------------------ helpers

def run(args):
    subprocess.Popen(args, stdout=subprocess.DEVNULL,
                     stderr=subprocess.DEVNULL, start_new_session=True)


def shell(command):
    subprocess.Popen(["bash", "-lc", command], stdout=subprocess.DEVNULL,
                     stderr=subprocess.DEVNULL, start_new_session=True)


def act_vol(delta):
    shell("pactl set-sink-volume @DEFAULT_SINK@ %+d%%" % delta)


# ------------------------------------------------------------------ actions

def act_fullscreen():
    """Toggle fullscreen on the target window via wmctrl.

    Preferred over sending F11: wmctrl sets the EWMH state directly, so it
    works whatever keybindings the app happens to have. (Sending F11 into
    Stremio was never verified to do anything.)
    """
    if _target is None:
        tap("F11")
        return
    out = subprocess.run(["xprop", "-id", str(_target), "_NET_WM_STATE"],
                         capture_output=True, text=True, timeout=5).stdout
    if "_NET_WM_STATE_FULLSCREEN" in out:
        shell("wmctrl -i -r %d -b remove,fullscreen" % _target)
    else:
        shell("wmctrl -i -r %d -b add,fullscreen" % _target)


def is_mpv_running():
    try:
        import socket
        s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        s.settimeout(0.1)
        s.connect("/tmp/mpv.sock")
        s.close()
        return True
    except Exception:
        return False


def mpv_cmd(*args):
    try:
        import socket
        s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        s.settimeout(0.3)
        s.connect("/tmp/mpv.sock")
        msg = json.dumps({"command": list(args)}) + "\n"
        s.sendall(msg.encode())
        s.close()
        return True
    except Exception:
        return False


def get_playback_info():
    """Retrieve live playback position, duration, status, and subtitle tracks.

    Checks MPV IPC socket first; falls back to VLC MPRIS via D-Bus.
    """
    if is_mpv_running():
        try:
            import socket
            s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            s.settimeout(0.3)
            s.connect("/tmp/mpv.sock")
            cmds = [
                {"command": ["get_property", "time-pos"]},
                {"command": ["get_property", "duration"]},
                {"command": ["get_property", "pause"]},
                {"command": ["get_property", "media-title"]},
                {"command": ["get_property", "track-list"]}
            ]
            s.sendall(("\n".join(json.dumps(c) for c in cmds) + "\n").encode())
            resps, buf = [], ""
            while len(resps) < len(cmds):
                chunk = s.recv(4096).decode()
                if not chunk:
                    break
                buf += chunk
                while "\n" in buf:
                    l, buf = buf.split("\n", 1)
                    if l.strip():
                        resps.append(json.loads(l))
            s.close()
            pos = resps[0].get("data", 0) if len(resps) > 0 else 0
            dur = resps[1].get("data", 0) if len(resps) > 1 else 0
            paused = resps[2].get("data", False) if len(resps) > 2 else False
            title = resps[3].get("data", "") if len(resps) > 3 else ""
            tracks = resps[4].get("data", []) if len(resps) > 4 else []
            subs = []
            for t in tracks:
                if t.get("type") == "sub":
                    subs.append({
                        "id": t.get("id"),
                        "title": t.get("title") or t.get("lang") or ("Track %s" % t.get("id")),
                        "selected": bool(t.get("selected"))
                    })
            return {
                "active": True, "player": "mpv",
                "status": "Paused" if paused else "Playing",
                "position": round(float(pos or 0), 1),
                "duration": round(float(dur or 0), 1),
                "title": title or "MPV",
                "subtitles": subs
            }
        except Exception:
            pass

    try:
        import dbus
        bus = dbus.SessionBus()
        vlc = bus.get_object("org.mpris.MediaPlayer2.vlc", "/org/mpris/MediaPlayer2")
        props = dbus.Interface(vlc, "org.freedesktop.DBus.Properties")
        status = str(props.Get("org.mpris.MediaPlayer2.Player", "PlaybackStatus"))
        if status in ("Playing", "Paused"):
            pos = float(props.Get("org.mpris.MediaPlayer2.Player", "Position")) / 1e6
            meta = props.Get("org.mpris.MediaPlayer2.Player", "Metadata")
            dur = float(meta.get("mpris:length", 0)) / 1e6
            title = str(meta.get("xesam:title", "VLC"))
            if "/" in title:
                title = title.split("/")[-1]
            title = urllib.parse.unquote(title)
            return {
                "active": True, "player": "vlc",
                "status": status,
                "position": round(pos, 1),
                "duration": round(dur, 1),
                "title": title,
                "subtitles": []
            }
    except Exception:
        pass

    return {"active": False, "player": "", "status": "Stopped", "position": 0, "duration": 0, "subtitles": []}


def act_seek(target_sec):
    try:
        sec = float(target_sec)
    except (TypeError, ValueError):
        return False
    if is_mpv_running():
        return mpv_cmd("set_property", "time-pos", sec)
    try:
        import dbus
        bus = dbus.SessionBus()
        vlc = bus.get_object("org.mpris.MediaPlayer2.vlc", "/org/mpris/MediaPlayer2")
        player = dbus.Interface(vlc, "org.mpris.MediaPlayer2.Player")
        props = dbus.Interface(vlc, "org.freedesktop.DBus.Properties")
        meta = props.Get("org.mpris.MediaPlayer2.Player", "Metadata")
        trackid = meta.get("mpris:trackid", "/org/videolan/vlc/playlist/0")
        player.SetPosition(trackid, dbus.Int64(int(sec * 1e6)))
        return True
    except Exception:
        return False


def act_subcycle():
    if is_mpv_running():
        mpv_cmd("cycle", "sub")
        return
    try:
        vlc_check = subprocess.run(["pgrep", "-x", "vlc"], capture_output=True, text=True, timeout=1).stdout
        if vlc_check.strip():
            _deliver(lambda: tap("v"))
            return
    except Exception:
        pass
    cur = ""
    try:
        cur = subprocess.run(["xdotool", "getactivewindow", "getwindowname"],
                             capture_output=True, text=True, timeout=1).stdout.strip().lower()
    except Exception:
        pass
    tgt = (_target_title or "").lower()
    if "vacuumtube" in cur or "youtube" in cur or "vacuumtube" in tgt or "youtube" in tgt:
        _deliver(lambda: tap("c"))
    else:
        _deliver(lambda: tap("v"))


def act_subprev():
    if is_mpv_running():
        mpv_cmd("cycle", "sub", "down")
        return
    _deliver(lambda: tap("v", modifier="Shift_L"))


def act_subtoggle():
    if is_mpv_running():
        mpv_cmd("cycle", "sub-visibility")
        return
    cur = ""
    try:
        cur = subprocess.run(["xdotool", "getactivewindow", "getwindowname"],
                             capture_output=True, text=True, timeout=1).stdout.strip().lower()
    except Exception:
        pass
    tgt = (_target_title or "").lower()
    if "vacuumtube" in cur or "youtube" in cur or "vacuumtube" in tgt or "youtube" in tgt:
        _deliver(lambda: tap("c"))
    else:
        _deliver(lambda: tap("v"))


def act_subset(sid):
    if is_mpv_running():
        mpv_cmd("set_property", "sid", sid)


def act_subdelay(delta):
    if is_mpv_running():
        try:
            if str(delta).lower() == "reset":
                mpv_cmd("set_property", "sub-delay", 0)
            else:
                mpv_cmd("add", "sub-delay", float(delta))
        except Exception:
            pass
        return
    try:
        if str(delta).lower() == "reset":
            return
        d = float(delta)
        if d > 0:
            _deliver(lambda: tap("h"))
        else:
            _deliver(lambda: tap("g"))
    except Exception:
        pass


def act_play():
    if is_mpv_running():
        mpv_cmd("cycle", "pause")
    else:
        _deliver(lambda: tap("space"))


def act_back():
    """Smart back button: stops playback, or navigates back in TV apps.

    - If MPV or VLC is playing external media, stops playback and refocuses Stremio.
    - If YouTube / VacuumTube is active, sends Escape (the Leanback back button).
    - If Stremio is active, sends BackSpace (Stremio ignores Escape for navigation).
    - Otherwise defaults to Escape.
    """
    if is_mpv_running():
        mpv_cmd("quit")
        set_target("Stremio")
        return

    try:
        vlc_check = subprocess.run(["pgrep", "-x", "vlc"], capture_output=True, text=True, timeout=1).stdout
        if vlc_check.strip():
            subprocess.run(["pkill", "-x", "vlc"], timeout=1)
            set_target("Stremio")
            return
    except Exception:
        pass

    cur = ""
    try:
        cur = subprocess.run(["xdotool", "getactivewindow", "getwindowname"],
                             capture_output=True, text=True, timeout=1).stdout.strip().lower()
    except Exception:
        pass
    tgt = (_target_title or "").lower()

    is_youtube = ("vacuumtube" in cur or "youtube" in cur or
                  "vacuumtube" in tgt or "youtube" in tgt)

    if is_youtube:
        _deliver(lambda: tap("Escape"))
    elif "stremio" in cur or "stremio" in tgt:
        _deliver(lambda: tap("BackSpace"))
    else:
        _deliver(lambda: tap("Escape"))


def act_rewind():
    if is_mpv_running():
        mpv_cmd("seek", -10)
    else:
        _deliver(lambda: tap("Left"))


def act_forward():
    if is_mpv_running():
        mpv_cmd("seek", 10)
    else:
        _deliver(lambda: tap("Right"))


def act_dpad_left():
    if is_mpv_running():
        mpv_cmd("seek", -10)
    else:
        _deliver(lambda: tap("Left"))


def act_dpad_right():
    if is_mpv_running():
        mpv_cmd("seek", 10)
    else:
        _deliver(lambda: tap("Right"))


def act_dpad_up():
    if is_mpv_running():
        mpv_cmd("seek", 60)
    else:
        _deliver(lambda: tap("Up"))


def act_dpad_down():
    if is_mpv_running():
        mpv_cmd("seek", -60)
    else:
        _deliver(lambda: tap("Down"))


def act_dpad_ok():
    if is_mpv_running():
        mpv_cmd("show-progress")
    else:
        _deliver(lambda: tap("Return"))


def act_home():
    if is_mpv_running():
        mpv_cmd("quit")
    wid_s, _ = find_window("Stremio")
    wid_y, _ = find_window("VacuumTube")
    try:
        cur = subprocess.run(["xdotool", "getwindowfocus", "getwindowname"],
                             capture_output=True, text=True, timeout=2).stdout.lower()
    except Exception:
        cur = ""
    if "vacuumtube" in cur or "youtube" in cur:
        set_target("Stremio", "/home/obada/.local/bin/stremio-handoff")
    elif "stremio" in cur:
        set_target("VacuumTube", "flatpak run --user rocks.shy.VacuumTube")
    elif wid_s:
        set_target("Stremio", "/home/obada/.local/bin/stremio-handoff")
    elif wid_y:
        set_target("VacuumTube", "flatpak run --user rocks.shy.VacuumTube")
    else:
        set_target("Stremio", "/home/obada/.local/bin/stremio-handoff")


_last_nav_time = 0.0
_nav_lock = threading.Lock()

def _debounce_nav(fn):
    global _last_nav_time
    with _nav_lock:
        now = time.monotonic()
        if now - _last_nav_time < 0.085:
            return
        _last_nav_time = now
    fn()

ACTIONS = {
    # D-pad and OK: smartly routes to MPV when playing, or target TV app
    "up":         lambda p: _debounce_nav(act_dpad_up),
    "down":       lambda p: _debounce_nav(act_dpad_down),
    "left":       lambda p: _debounce_nav(act_dpad_left),
    "right":      lambda p: _debounce_nav(act_dpad_right),
    "ok":         lambda p: _debounce_nav(act_dpad_ok),
    "back":       lambda p: _debounce_nav(act_back),
    "esc":        lambda p: _debounce_nav(lambda: _deliver(lambda: tap("Escape"))),
    "play":       lambda p: act_play(),
    "rewind":     lambda p: act_rewind(),
    "forward":    lambda p: act_forward(),
    "seek":       lambda p: act_seek(p.get("t", 0)),
    "subcycle":   lambda p: act_subcycle(),
    "subprev":    lambda p: act_subprev(),
    "subtoggle":  lambda p: act_subtoggle(),
    "subset":     lambda p: act_subset(p.get("id")),
    "subdelay":   lambda p: act_subdelay(p.get("d")),
    "fullscreen": lambda p: _deliver(act_fullscreen),
    "menu":       lambda p: _deliver(lambda: tap("Menu", "Alt_L")),

    # touchpad
    "move":       lambda p: _deliver(lambda: move_pointer(
        float(p.get("dx", 0)), float(p.get("dy", 0)))),
    "leftclick":  lambda p: _deliver(lambda: click(1)),
    "rightclick": lambda p: _deliver(lambda: click(3)),
    "middleclick": lambda p: _deliver(lambda: click(2)),
    "scrollup":   lambda p: _deliver(lambda: scroll(1)),
    "scrolldown": lambda p: _deliver(lambda: scroll(-1)),

    # volume (independent of the target window)
    "volup":      lambda p: act_vol(5),
    "voldown":    lambda p: act_vol(-5),
    "mute":       lambda p: shell("pactl set-sink-mute @DEFAULT_SINK@ toggle"),

    # keyboard
    "type":       lambda p: _deliver(lambda: type_text(p.get("t", "")[:300])),
    "bs":         lambda p: _deliver(lambda: tap("BackSpace")),
    "shift":      lambda p: _deliver(lambda: tap("shift", hold=0.01)),

    # apps / focus
    "stremio":    lambda p: (mpv_cmd("quit") if is_mpv_running() else None,
                             set_target("Stremio", "/home/obada/.local/bin/stremio-handoff")),
    "youtube":    lambda p: (mpv_cmd("quit") if is_mpv_running() else None,
                             set_target("VacuumTube", "flatpak run --user rocks.shy.VacuumTube")),
    "vlc":        lambda p: (mpv_cmd("quit") if is_mpv_running() else None,
                             set_target("vlc", "/usr/bin/vlc")),
    # sleep timer
    "sleeptimer": lambda p: timer_cmd(p),
    "sleepnow":   lambda p: _power("suspend"),
    "poweroffnow": lambda p: _power("poweroff"),

    "close":      lambda p: (mpv_cmd("quit") if is_mpv_running() else shell("wmctrl -c")),
    "home":       lambda p: act_home(),
    "target":     lambda p: None,
}


# --------------------------------------------------------------------- page

PAGE = """<!doctype html>
<html>
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1, maximum-scale=1, user-scalable=no, viewport-fit=cover">
<meta name="apple-mobile-web-app-capable" content="yes">
<meta name="mobile-web-app-capable" content="yes">
<meta name="apple-mobile-web-app-status-bar-style" content="black-translucent">
<meta name="theme-color" content="#0b0b0f">
<title>iMac Remote</title>
<style>
  * { box-sizing:border-box; -webkit-tap-highlight-color:transparent; }
  html,body { margin:0; padding:0; height:100%; overscroll-behavior:none; }
  body {
    background:#0b0b0f; color:#fff; overflow:hidden;
    font-family:-apple-system,BlinkMacSystemFont,system-ui,sans-serif;
    -webkit-user-select:none; user-select:none; touch-action:manipulation;
  }

  /* ---------- shell ---------- */
  #app { display:flex; flex-direction:column; height:100dvh;
         padding:calc(env(safe-area-inset-top) + 8px) 14px
                 calc(env(safe-area-inset-bottom) + 10px); }
  .bar { display:flex; align-items:center; justify-content:space-between;
         flex:0 0 auto; margin-bottom:4px; }
  .tgt { font-size:12px; color:#5f6368; letter-spacing:.08em; text-transform:uppercase;
         white-space:nowrap; overflow:hidden; text-overflow:ellipsis; max-width:62%; }
  .tgt.live { color:#4ad07a; }
  .hint { font-size:11px; color:#3a3d42; }

  /* ---------- d-pad ---------- */
  .dpad { display:grid; grid-template-columns:repeat(3,1fr);
          grid-template-rows:repeat(3,1fr); gap:8px;
          flex:1 1 auto; min-height:0; max-width:420px; width:100%;
          margin:0 auto; }
  .dpad button { border-radius:18px; font-size:30px; background:#1c1c22;
                 border:0; color:#e8e8ea; }
  .dpad button.down { background:#4a4a55; }
  .dpad .ok { background:#2b2b34; font-size:19px; font-weight:700; letter-spacing:.06em; }
  .dpad .ok.down { background:#5a5a68; }
  .dpad .up    { grid-area:1/2; }
  .dpad .left  { grid-area:2/1; }
  .dpad .ok    { grid-area:2/2; }
  .dpad .right { grid-area:2/3; }
  .dpad .down  { grid-area:3/2; }

  /* ---------- bottom deck ---------- */
  .deck { flex:0 0 auto; display:flex; flex-direction:column; gap:8px;
          max-width:420px; width:100%; margin:10px auto 0; }
  .row { display:flex; gap:8px; }
  .row button { flex:1; height:56px; border:0; border-radius:15px;
                background:#1c1c22; color:#e8e8ea; font-size:17px; }
  .row button.down { background:#4a4a55; }
  .apps-top { max-width:420px; width:100%; margin:4px auto 8px; flex:0 0 auto; gap:8px; }
  .row .app { font-size:13px; letter-spacing:.05em; font-weight:700; height:52px;
              display:flex; align-items:center; justify-content:center; gap:8px; }
  .row .app svg.logo { width:22px; height:22px; flex:0 0 22px; display:block; filter:drop-shadow(0 1px 2px rgba(0,0,0,0.5)); }
  .row .app.stremio { background:linear-gradient(135deg,#3c1a63,#240d3d); color:#dfc2ff; border:1px solid #5a2e8c; }
  .row .app.youtube { background:linear-gradient(135deg,#5e1313,#330a0a); color:#ffc2c2; border:1px solid #851c1c; }
  /* scrubber bar */
  .scrub-wrap { display:flex; align-items:center; gap:8px; width:100%; max-width:420px;
                margin:0 auto 3px; padding:0 4px; opacity:0.3; transition:opacity .2s ease; }
  .scrub-wrap.on { opacity:1; }
  .scrub-time { font-size:12px; font-variant-numeric:tabular-nums; color:#8e929b;
                font-weight:600; min-width:38px; text-align:center; }
  .scrub-box { flex:1; display:flex; align-items:center; position:relative; }
  #scrub-bar { -webkit-appearance:none; appearance:none; width:100%; height:7px;
               border-radius:4px; background:#252833; outline:none; margin:0; cursor:pointer; }
  #scrub-bar::-webkit-slider-thumb { -webkit-appearance:none; appearance:none; width:18px; height:18px;
                                     border-radius:50%; background:#4a8eff; border:2px solid #fff;
                                     box-shadow:0 1px 4px rgba(0,0,0,0.6); cursor:pointer; }
  #scrub-bar::-moz-range-thumb { width:18px; height:18px; border-radius:50%; background:#4a8eff;
                                 border:2px solid #fff; box-shadow:0 1px 4px rgba(0,0,0,0.6); cursor:pointer; }

  .row .media       { background:#22252e; font-size:15px; font-weight:600; }
  .row .media.playbtn { background:linear-gradient(135deg,#244882,#172c52); color:#8ec3ff; font-weight:700; border:1px solid #376bb8; flex:1.4; }
  .row .backbtn     { background:#2e241c; color:#ffb885; font-size:15px; font-weight:700; border:1px solid #5c3c26; flex:1.3; }
  .row .escbtn      { background:#26202e; color:#d6b0ff; font-size:14px; font-weight:700; border:1px solid #4a3461; flex:1; }
  .row .tool  { background:#16233d; font-size:13px; font-weight:600; }
  .row .tool.subtool { background:linear-gradient(135deg,#231c38,#161026); color:#cfb2ff; border:1px solid #4a3473; }
  .row .wide  { flex:2; }

  /* ---------- modal panels ---------- */
  .sheet { position:fixed; inset:0; background:#0b0b0f; z-index:20;
           display:none; flex-direction:column;
           padding:calc(env(safe-area-inset-top) + 10px) 14px
                   calc(env(safe-area-inset-bottom) + 12px); }
  .sheet.on { display:flex; }
  .sheet .bar { margin-bottom:12px; }
  .sheet h2 { font-size:13px; letter-spacing:.16em; text-transform:uppercase;
              color:#7a7d82; margin:0; font-weight:600; }
  .close { height:40px; min-width:78px; border:0; border-radius:12px;
           background:#2a1c1c; color:#ff8a80; font-size:15px; }

  /* touchpad */
  #pad { flex:1 1 auto; min-height:0; width:100%; background:#121317;
         border:2px solid #26272e; border-radius:22px; position:relative;
         touch-action:none; display:flex; align-items:center;
         justify-content:center; overflow:hidden; }
  #pad.live { border-color:#3a7bd5; }
  #pad .glyph { color:#23242b; font-size:80px; pointer-events:none; }
  .tip { position:absolute; bottom:14px; left:0; right:0; text-align:center;
              font-size:12px; color:#4a4d54; pointer-events:none; }
  #pad.fired { background:#171a20; }

  /* keyboard */
  #typebox { width:100%; padding:14px; font-size:18px; border-radius:13px;
             border:1px solid #2a2b32; background:#15161a; color:#fff;
             font-family:inherit; margin-bottom:9px; }
  .kb { display:grid; grid-template-columns:repeat(10,1fr); gap:5px;
        flex:0 0 auto; }
  .kb button { height:52px; font-size:17px; border:0; border-radius:10px;
               background:#1c1c22; color:#e8e8ea; }
  .kb button.down { background:#4a4a55; }
  .kb .sp2 { grid-column:span 2; font-size:14px; }

  /* numbers */
  .num { display:grid; grid-template-columns:repeat(3,1fr); gap:9px;
         flex:1 1 auto; min-height:0; max-width:420px; width:100%;
         margin:0 auto; }
  .num button { border:0; border-radius:18px; background:#1c1c22; color:#e8e8ea;
                font-size:30px; }
  .num button.down { background:#4a4a55; }
  .num .sm { font-size:15px; }

  /* sleep timer */
  .cdwrap { flex:0 0 auto; text-align:center; padding:6px 0 14px; }
  .cd { font-size:58px; font-weight:200; letter-spacing:.02em; line-height:1.05;
        font-variant-numeric:tabular-nums; }
  .cd.armed { color:#fff; }
  .cdlabel { font-size:12px; color:#6a6d73; letter-spacing:.14em;
             text-transform:uppercase; margin-bottom:2px; }
  .cdmode { font-size:12px; color:#f0a04b; min-height:16px; }
  .sheetbody { flex:1 1 auto; overflow-y:auto; -webkit-overflow-scrolling:touch; }
  .lbl { font-size:11px; color:#6a6d73; letter-spacing:.12em; text-transform:uppercase;
         margin:14px 0 7px; }
  .row.presets { max-width:420px; width:100%; margin-left:auto; margin-right:auto; }
  .row.presets button { height:52px; font-size:16px; flex:1; }
  .row.presets button.sel { background:#2f4a3a; color:#8fe0b0; }
  .row.presets button.danger { background:#3a1f1f; color:#ff9a90; }
  .row.now { max-width:420px; width:100%; margin:18px auto 0; }
  .row.now .nowbtn { height:60px; font-size:15px; letter-spacing:.04em;
                     background:#2a2118; color:#f0b070; }
  .row.now .nowbtn.danger { background:#3a1f1f; color:#ff9a90; }
  .tiny { font-size:11px; color:#4a4d54; text-align:center; margin:12px 0 4px;
          line-height:1.5; }
</style>
</head>
<body>

<div id="app">
  <div class="bar">
    <div class="tgt" id="tgt">no target</div>
    <div class="hint" id="hint">iMac TV</div>
  </div>

  <!-- Top App Switcher Row -->
  <div class="row apps-top">
    <button class="app stremio" onclick="tap(this,'stremio')">
      <svg class="logo" viewBox="0 0 128 128">
        <defs>
          <linearGradient id="stremio-grad" x1="0%" y1="100%" x2="100%" y2="0%">
            <stop offset="0%" stop-color="#1155d9"/>
            <stop offset="100%" stop-color="#7b5bf5"/>
          </linearGradient>
        </defs>
        <path d="M121.58 58.84 69.16 6.42a7.47 7.47 0 0 0-10.56 0L6.18 58.84a7.47 7.47 0 0 0 0 10.56l52.42 52.42c2.92 2.92 7.64 2.92 10.56 0l52.42-52.42c2.91-2.91 2.91-7.64 0-10.56z" fill="url(#stremio-grad)"/>
        <path d="M82.79 62.86 58.15 45.73c-1.26-.88-2.99.03-2.99 1.57v34.25c0 1.54 1.73 2.44 2.99 1.56l24.64-17.12c1.09-.76 1.09-2.37 0-3.13z" fill="#fff"/>
      </svg>
      <span>STREMIO</span>
    </button>
    <button class="app youtube" onclick="tap(this,'youtube')">
      <svg class="logo" viewBox="0 0 24 24">
        <path fill="#FF0000" d="M23.498 6.186a3.016 3.016 0 0 0-2.122-2.136C19.505 3.5 12 3.5 12 3.5s-7.505 0-9.377.55a3.016 3.016 0 0 0-2.122 2.136C0 8.07 0 12 0 12s0 3.93.502 5.814a3.016 3.016 0 0 0 2.122 2.136c1.871.55 9.376.55 9.376.55s7.505 0 9.377-.55a3.016 3.016 0 0 0 2.122-2.136C24 15.93 24 12 24 12s0-3.93-.502-5.814z"/>
        <polygon fill="#FFFFFF" points="9.545,15.568 15.773,12 9.545,8.432"/>
      </svg>
      <span>YOUTUBE</span>
    </button>
    <button class="app vlc" onclick="tap(this,'vlc')">
      <svg class="logo" viewBox="0 0 48 48">
        <path fill="#FF8800" fill-rule="evenodd" d="m11 27.01c-.81 0-1.54.49-1.85 1.23l-5 12C3.6 41.56 4.57 43 6 43h36c1.43 0 2.39-1.45 1.85-2.77l-5-12c-.31-.75-1.04-1.23-1.85-1.23z"/>
        <path fill="#FF8800" fill-rule="evenodd" d="m24 3c-.91-.01-1.83.14-2.75.51-.57.23-1 .7-1.17 1.29l-1.84 6.22c3.17 12.01 8.77 2.22 11.52 0l-1.84-6.22c-.18-.62-.66-1.11-1.27-1.32-.85-.3-1.74-.47-2.65-.48zm-8.13 16.02-2.22 7.5c1.05 9.18 11.9 5.07 20.61-.26l-2.14-7.24c-5.5 2.18-8.03-3.24-16.25 0z"/>
        <path fill="#FFFFFF" fill-rule="evenodd" d="m18.24 11.01-2.37 8c6.13 2.75 10.75 2.19 16.25 0l-2.37-8c-2.76 2-8.76 2-11.52 0zm-2.37 8-3.79 12.81c-.24.83.07 1.72.77 2.21 3.08 2.15 7.09 3.01 11.07 2.98 3.99-.04 7.98-.99 11.12-2.91.77-.47 1.13-1.4 0.87-2.27l-1.65-5.57c-8.7 5.27-18.46 1.52-20.61.26l2.22-7.5z"/>
      </svg>
      <span>VLC</span>
    </button>
  </div>

  <div class="dpad">
    <button class="up"    id="btn-up"    data-act="up">&#9650;</button>
    <button class="left"  id="btn-left"  data-act="left">&#9664;</button>
    <button class="ok"    id="btn-ok"    data-act="ok">OK</button>
    <button class="right" id="btn-right" data-act="right">&#9654;</button>
    <button class="down"  id="btn-down"  data-act="down">&#9660;</button>
  </div>

  <div class="deck">
    <!-- Media scrubber bar -->
    <div id="scrub-wrap" class="scrub-wrap">
      <span class="scrub-time" id="scrub-cur">0:00</span>
      <div class="scrub-box">
        <input type="range" id="scrub-bar" min="0" max="100" value="0" step="1">
      </div>
      <span class="scrub-time" id="scrub-total">0:00</span>
    </div>

    <!-- Media transport -->
    <div class="row">
      <button class="media" onclick="tap(this,'rewind')">&#9198; &minus;10s</button>
      <button class="media playbtn" onclick="tap(this,'play')">&#9654;&#10074;&#10074; PLAY</button>
      <button class="media" onclick="tap(this,'forward')">&#9197; +10s</button>
    </div>
    <!-- Navigation & Volume -->
    <div class="row">
      <button class="backbtn" id="btn-back" onclick="tap(this,'back')">&#8617; BACK</button>
      <button class="escbtn" id="btn-esc" onclick="tap(this,'esc')">ESC</button>
      <button id="btn-voldown" data-act="voldown">&#8722;</button>
      <button onclick="tap(this,'mute')">MUTE</button>
      <button id="btn-volup" data-act="volup">+</button>
      <button onclick="tap(this,'fullscreen')">&#9974;</button>
    </div>
    <!-- Tools -->
    <div class="row">
      <button class="tool subtool" onclick="openSheet('subs')">&#128172; SUBS</button>
      <button class="tool" onclick="openSheet('pad')">&#128421; POINTER</button>
      <button class="tool" onclick="openSheet('kb')">&#9000; KEYS</button>
      <button class="tool" onclick="openSheet('sleept')">&#9200; TIMER</button>
    </div>
  </div>
</div>

<!-- ============ SLEEP TIMER SHEET ============ -->
<div class="sheet" id="sheet-sleept">
  <div class="bar">
    <h2>Sleep Timer</h2>
    <button class="close" onclick="closeSheet()">Done</button>
  </div>

  <div class="cdwrap">
    <div class="cdlabel" id="cdlabel">no timer set</div>
    <div class="cd" id="cd">--:--</div>
    <div class="cdmode" id="cdmode"></div>
  </div>

  <div class="sheetbody">
    <div class="lbl">turn off after</div>
    <div class="row presets">
      <button onclick="arm(15)">15m</button>
      <button onclick="arm(30)">30m</button>
      <button onclick="arm(45)">45m</button>
      <button onclick="arm(60)">1h</button>
      <button onclick="arm(90)">90m</button>
      <button onclick="arm(120)">2h</button>
    </div>

    <div class="lbl">change it</div>
    <div class="row presets">
      <button onclick="bump(30)">+30m</button>
      <button onclick="bump(10)">+10m</button>
      <button onclick="bump(-10)">&#8722;10m</button>
      <button onclick="bump(-30)">&#8722;30m</button>
      <button class="danger" onclick="cancelTimer()">CANCEL</button>
    </div>

    <div class="lbl">what it should do</div>
    <div class="row presets">
      <button id="modeOff" onclick="setMode('off')">POWER OFF</button>
      <button id="modeSus" onclick="setMode('suspend')">SLEEP</button>
    </div>

    <div class="row now">
      <button class="nowbtn" onclick="goNow('suspend')">SLEEP NOW</button>
      <button class="nowbtn danger" onclick="goNow('poweroff')">POWER OFF NOW</button>
    </div>
    <div class="tiny">SLEEP keeps everything running and wakes in seconds.
       POWER OFF shuts the iMac down.</div>
  </div>
</div>

<!-- ============ SUBTITLES SHEET ============ -->
<div class="sheet" id="sheet-subs">
  <div class="bar">
    <h2>Subtitles</h2>
    <button class="close" onclick="closeSheet()">Done</button>
  </div>

  <div class="sheetbody">
    <div class="lbl">Quick Subtitle Controls</div>
    <div class="row presets" style="flex-wrap:wrap">
      <button style="flex:2;height:54px;font-weight:700" onclick="tap(this,'subcycle')">&#128172; Next Subtitle (Cycle)</button>
      <button style="flex:1;height:54px" onclick="tap(this,'subprev')">&#9664; Prev</button>
      <button style="flex:1;height:54px" onclick="tap(this,'subtoggle')">&#8856; Toggle</button>
    </div>

    <div class="lbl">Sync / Audio Delay</div>
    <div class="row presets">
      <button onclick="send('subdelay','d=-0.1');buzz()">&minus;100ms</button>
      <button onclick="send('subdelay','d=-0.05');buzz()">&minus;50ms</button>
      <button onclick="send('subdelay','d=reset');buzz()">Reset</button>
      <button onclick="send('subdelay','d=0.05');buzz()">+50ms</button>
      <button onclick="send('subdelay','d=0.1');buzz()">+100ms</button>
    </div>

    <div id="sub-tracks" style="margin-top:10px"></div>

    <div class="tiny" style="margin-top:18px">
      Pressing <b>Next Subtitle</b> cycles language tracks on your TV with instant on-screen feedback.
    </div>
  </div>
</div>

<!-- ============ POINTER SHEET ============ -->
<div class="sheet" id="sheet-pad">
  <div class="bar">
    <h2>Pointer</h2>
    <button class="close" onclick="closeSheet()">Done</button>
  </div>
  <div id="pad"><div class="glyph">&#8596;</div>
    <div class="tip">tap = click &middot; hold = right&#8209;click &middot; drag = move</div>
  </div>
  <div class="row" style="margin-top:9px;max-width:420px;width:100%;margin-left:auto;margin-right:auto">
    <button onclick="tap(this,'scrollup')">&#8593; Scroll</button>
    <button onclick="tap(this,'scrolldown')">&#8595; Scroll</button>
  </div>
</div>

<!-- ============ KEYBOARD SHEET ============ -->
<div class="sheet" id="sheet-kb">
  <div class="bar">
    <h2>Keyboard</h2>
    <button class="close" onclick="closeSheet()">Done</button>
  </div>
  <input id="typebox" placeholder="type here, then hit TYPE" autocomplete="off"
         autocapitalize="off" autocorrect="off" spellcheck="false">
  <div class="row" style="margin-bottom:8px">
    <button class="wide" onclick="doType()">TYPE IT</button>
    <button onclick="tap(this,'bs')">&#9003; Del</button>
  </div>
  <div class="kb" id="kb"></div>
</div>

<!-- ============ NUMBERS SHEET ============ -->
<div class="sheet" id="sheet-num">
  <div class="bar">
    <h2>Numbers</h2>
    <button class="close" onclick="closeSheet()">Done</button>
  </div>
  <div class="num" id="num"></div>
  <div class="row" style="margin-top:9px;max-width:420px;width:100%;margin-left:auto;margin-right:auto">
    <button onclick="tap(this,'bs')">&#9003; Del</button>
    <button onclick="tap(this,'ok')">ENTER</button>
  </div>
</div>

<script>
/* ------------------------------------------------------------------ net ---
   send(): silent. Used for touchpad drags and hold-to-repeat, so the
   phone never buzzes continuously.
   tap():  one deliberate press -> sends + one short buzz.
   hold(): press-and-hold auto-repeat, like a real TV remote.            */
var lastNav = 0;
function send(a, q){
  var now = Date.now();
  if(['up','down','left','right','ok','back','esc'].indexOf(a) !== -1){
    if(now - lastNav < 90) return;
    lastNav = now;
  }
  var u = 'cmd/' + a;
  if(q) u += '?' + q;
  fetch(u, {method:'POST', credentials:'same-origin'}).catch(function(){});
}
function buzz(){
  if(navigator.vibrate){
    try { navigator.vibrate(10); } catch(e){}
  }
}
function tap(el, a, q){
  if(el){
    el.classList.add('down');
    setTimeout(function(){ el.classList.remove('down'); }, 100);
  }
  send(a, q);
  buzz();
}

function bindHold(el, action){
  if(!el) return;
  var tDelay = null, tRepeat = null, isHeld = false;

  function press(ev){
    if(ev) ev.preventDefault();
    if(isHeld) return;
    isHeld = true;
    el.classList.add('down');
    send(action);
    buzz();

    tDelay = setTimeout(function(){
      tRepeat = setInterval(function(){
        send(action);
      }, 160);
    }, 420);
  }

  function release(ev){
    if(!isHeld) return;
    isHeld = false;
    el.classList.remove('down');
    if(tDelay){ clearTimeout(tDelay); tDelay = null; }
    if(tRepeat){ clearInterval(tRepeat); tRepeat = null; }
  }

  el.addEventListener('pointerdown', press);
  el.addEventListener('pointerup', release);
  el.addEventListener('pointercancel', release);
  el.addEventListener('pointerleave', release);
  el.addEventListener('contextmenu', function(ev){ ev.preventDefault(); });
  el.addEventListener('click', function(ev){ ev.preventDefault(); });
}

function bindTap(el, action){
  if(!el) return;
  var touched = false;

  function fire(ev){
    if(ev) ev.preventDefault();
    if(touched) return;
    touched = true;
    setTimeout(function(){ touched = false; }, 120);
    tap(el, action);
  }

  el.addEventListener('pointerdown', fire);
  el.addEventListener('click', function(ev){ ev.preventDefault(); });
}

function dpadInit(){
  bindHold(document.getElementById('btn-up'), 'up');
  bindHold(document.getElementById('btn-left'), 'left');
  bindHold(document.getElementById('btn-right'), 'right');
  bindHold(document.getElementById('btn-down'), 'down');
  bindTap(document.getElementById('btn-ok'), 'ok');
  bindTap(document.getElementById('btn-back'), 'back');
  bindTap(document.getElementById('btn-esc'), 'esc');
  bindHold(document.getElementById('btn-volup'), 'volup');
  bindHold(document.getElementById('btn-voldown'), 'voldown');
}

/* --------------------------------------------------------------- sheets --- */
function openSheet(which){
  ['pad','kb','num','sleept'].forEach(function(k){
    document.getElementById('sheet-'+k).classList.toggle('on', k===which);
  });
  document.getElementById('app').style.visibility = 'hidden';
  if(which==='kb') buildKb();
  if(which==='num') buildNum();
  if(which==='sleept'){ pollTimer(); }
  buzz();
}
function closeSheet(){
  ['pad','kb','num','sleept'].forEach(function(k){
    document.getElementById('sheet-'+k).classList.remove('on');
  });
  document.getElementById('app').style.visibility = 'visible';
  if(timerPoll){ clearInterval(timerPoll); timerPoll = null; }
  buzz();
}

/* ------------------------------------------------------------- pointer ----
   tap            -> left click
   hold (no move) -> right click
   drag           -> relative movement, silent
   The old version needed a separate CLICK button, which is not how anyone
   uses a touchpad. */
var padEl = null, pStart = null, pLast = null, pMoved = false;
var pAcc = {x:0,y:0}, pTimer = null, pHoldTimer = null, TAP_SLOP = 10;

function padInit(){
  padEl = document.getElementById('pad');
  padEl.addEventListener('pointerdown', function(e){
    e.preventDefault();
    padEl.setPointerCapture(e.pointerId);
    padEl.classList.add('live');
    pStart = pLast = {x:e.clientX, y:e.clientY, t:Date.now()};
    pMoved = false;
    pHoldTimer = setTimeout(function(){
      if(!pMoved){ send('rightclick'); buzz(); padEl.classList.add('fired'); }
    }, 550);
  });
  padEl.addEventListener('pointermove', function(e){
    if(!pStart) return;
    var dx = e.clientX - pLast.x, dy = e.clientY - pLast.y;
    if(Math.abs(e.clientX - pStart.x) > TAP_SLOP ||
       Math.abs(e.clientY - pStart.y) > TAP_SLOP){
      if(!pMoved){ pMoved = true; clearTimeout(pHoldTimer); }
    }
    if(!pMoved) return;
    pAcc.x += dx; pAcc.y += dy;
    pLast = {x:e.clientX, y:e.clientY};
    if(!pTimer) pTimer = setTimeout(padFlush, 45);
  });
  function end(e){
    if(!pStart) return;
    clearTimeout(pHoldTimer);
    if(pTimer){ clearTimeout(pTimer); pTimer = null; padFlush(); }
    if(!pMoved){
      /* a tap that never became a drag: left click */
      send('leftclick'); buzz(); padEl.classList.add('fired');
    }
    pStart = null; pMoved = false;
    setTimeout(function(){ padEl.classList.remove('live','fired'); }, 140);
  }
  padEl.addEventListener('pointerup', end);
  padEl.addEventListener('pointercancel', end);
}
function padFlush(){
  pTimer = null;
  if(pAcc.x || pAcc.y){
    send('move', 'dx=' + Math.round(pAcc.x) + '&dy=' + Math.round(pAcc.y));
    pAcc = {x:0,y:0};
  }
}

/* ------------------------------------------------------------ keyboard --- */
var shift = false;
var ROWS = [
  ['1','2','3','4','5','6','7','8','9','0'],
  ['q','w','e','r','t','y','u','i','o','p'],
  ['a','s','d','f','g','h','j','k','l','.'],
  ['z','x','c','v','b','n','m','-','_','@']
];
function doType(){
  var t = document.getElementById('typebox').value;
  if(t) send('type', 't=' + encodeURIComponent(t));
}
function buildKb(){
  var kb = document.getElementById('kb');
  kb.innerHTML = '';
  ROWS.forEach(function(row){
    row.forEach(function(ch){
      var b = document.createElement('button');
      b.textContent = shift ? ch.toUpperCase() : ch;
      b.addEventListener('pointerdown', function(e){
        e.preventDefault(); b.classList.add('down');
        send('type','t='+encodeURIComponent(shift?ch.toUpperCase():ch)); buzz();
      });
      ['pointerup','pointercancel','pointerleave'].forEach(function(ev){
        b.addEventListener(ev, function(){ b.classList.remove('down'); });
      });
      kb.appendChild(b);
    });
  });
  function wide(label, fn){
    var b = document.createElement('button');
    b.className = 'sp2'; b.textContent = label;
    b.addEventListener('pointerdown', function(e){ e.preventDefault(); fn(b); });
    kb.appendChild(b);
  }
  wide('SHIFT', function(b){ b.classList.add('down');
    shift = !shift; buildKb();
    setTimeout(function(){ b.classList.remove('down'); }, 110); });
  wide('SPACE', function(b){ b.classList.add('down'); send('type','t=%20'); buzz();
    setTimeout(function(){ b.classList.remove('down'); }, 110); });
  wide('DEL',   function(b){ b.classList.add('down'); send('bs'); buzz();
    setTimeout(function(){ b.classList.remove('down'); }, 110); });
  wide('CLR',   function(b){ b.classList.add('down');
    document.getElementById('typebox').value = ''; buzz();
    setTimeout(function(){ b.classList.remove('down'); }, 110); });
}

/* ------------------------------------------------------------- numbers --- */
function buildNum(){
  var n = document.getElementById('num');
  n.innerHTML = '';
  for(var i=1;i<=9;i++) addNum(n, String(i), '');
  addNum(n, 'CLEAR', 'sm');
  addNum(n, '0', '');
  addNum(n, '␣', '');
}
function addNum(parent, label, cls){
  var b = document.createElement('button');
  if(cls) b.className = cls;
  b.textContent = label;
  b.addEventListener('pointerdown', function(e){
    e.preventDefault(); b.classList.add('down');
    if(label === 'CLEAR'){
      for(var k=0;k<12;k++) send('bs');
    } else if(label === '␣') {
      send('type','t=%20');
    } else {
      send('type','t='+label);
    }
    buzz();
  });
  ['pointerup','pointercancel','pointerleave'].forEach(function(ev){
    b.addEventListener(ev, function(){ b.classList.remove('down'); });
  });
  parent.appendChild(b);
}

/* ------------------------------------------------------------ sleep timer --
   The countdown is shown on the phone, but the deadline lives on the PC, so
   it keeps counting even if the phone locks, the browser is killed, or you
   close the page. The phone is only ever a view onto it.                  */
var timerPoll = null;

function arm(mins){ send('sleeptimer', 't=' + mins); buzz(); setTimeout(pollTimer, 250); }
function bump(mins){ send('sleeptimer', 'add=' + mins); buzz(); setTimeout(pollTimer, 250); }
function cancelTimer(){ send('sleeptimer', 'clear=1'); buzz(); setTimeout(pollTimer, 250); }
function setMode(m){ send('sleeptimer', 'mode=' + m); buzz(); setTimeout(pollTimer, 250); }

function goNow(what){
  if(what === 'poweroff'){
    if(!confirm('Power the iMac off now?')) return;
    send('poweroffnow');
  } else {
    send('sleepnow');
  }
  buzz();
}

function fmt(sec){
  sec = Math.max(0, Math.round(sec));
  var h = Math.floor(sec/3600), m = Math.floor((sec%3600)/60), s = sec%60;
  var out = (h ? h + ':' + (m<10?'0':'') : '') + m + ':' + (s<10?'0':'') + s;
  return out;
}

function pollTimer(){
  if(timerPoll) clearInterval(timerPoll);
  var tick = function(){
    fetch('timer', {credentials:'same-origin'})
      .then(function(r){ return r.ok ? r.json() : null; })
      .then(function(t){
        if(!t) return;
        var cd = document.getElementById('cd');
        var lbl = document.getElementById('cdlabel');
        var mode = document.getElementById('cdmode');
        if(t.active){
          cd.textContent = fmt(t.remaining);
          cd.classList.add('armed');
          lbl.textContent = 'until ' + (t.mode === 'suspend' ? 'sleep' : 'power off');
          mode.textContent = 'change or cancel any time below';
        } else {
          cd.textContent = '--:--';
          cd.classList.remove('armed');
          lbl.textContent = 'no timer set';
          mode.textContent = '';
        }
        var off = document.getElementById('modeOff');
        var sus = document.getElementById('modeSus');
        if(off) off.classList.toggle('sel', t.mode === 'off');
        if(sus) sus.classList.toggle('sel', t.mode === 'suspend');
      })
      .catch(function(){});
  };
  tick();
  timerPoll = setInterval(tick, 1000);
}

/* ---------------------------------------------------------------- boot --- */
padInit(); dpadInit();
fetch('state', {credentials:'same-origin'})
  .then(function(r){ return r.ok ? r.text() : ''; })
  .then(function(t){
    if(!t) return;
    var el = document.getElementById('tgt');
    el.textContent = t;
    if(t.indexOf('driving') === 0) el.classList.add('live');
  })
  .catch(function(){});
setInterval(function(){
  fetch('state', {credentials:'same-origin'})
    .then(function(r){ return r.ok ? r.text() : ''; })
    .then(function(t){
      if(!t) return;
      var el = document.getElementById('tgt');
      el.textContent = t;
      el.classList.toggle('live', t.indexOf('driving') === 0);
    }).catch(function(){});
}, 5000);
</script>
</body>
</html>
"""


# ------------------------------------------------------------------- secret

def load_secret():
    try:
        with open(SECRET_FILE) as fh:
            value = fh.read().strip()
            if value:
                return value
    except OSError:
        pass
    value = secrets.token_urlsafe(12)
    with open(SECRET_FILE, "w") as fh:
        fh.write(value + "\n")
    os.chmod(SECRET_FILE, 0o600)
    return value


SECRET = load_secret()


def local_ip():
    try:
        out = subprocess.run(
            ["ip", "-4", "-o", "addr", "show", "scope", "global"],
            capture_output=True, text=True, timeout=5).stdout
        for line in out.splitlines():
            parts = line.split()
            if "inet" in parts:
                return parts[parts.index("inet") + 1].split("/")[0]
    except Exception:
        pass
    return "127.0.0.1"


def target_info():
    if is_mpv_running():
        return "▶ PLAYING (MPV)"
    cur = ""
    try:
        cur = subprocess.run(["xdotool", "getactivewindow", "getwindowname"],
                             capture_output=True, text=True, timeout=1).stdout.strip()
    except Exception:
        pass
    title = cur or _target_title
    if not title:
        return "READY"
    lt = title.lower()
    if "vlc" in lt:
        return "▶ VLC PLAYER"
    if "stremio" in lt:
        return "STREMIO"
    if "vacuumtube" in lt or "youtube" in lt:
        return "YOUTUBE TV"
    return ("DRIVING: %s" % title)[:25]


# ------------------------------------------------------------------ handler

class Handler(BaseHTTPRequestHandler):
    server_version = "tvremote"

    def log_message(self, *a):
        pass

    def _authorised(self, query):
        cookie = SimpleCookie(self.headers.get("Cookie", ""))
        if cookie.get(COOKIE) and cookie[COOKIE].value == SECRET:
            return True
        return dict(urllib.parse.parse_qsl(query)).get("k") == SECRET

    def _send(self, code, body=b"", ctype="text/html; charset=utf-8", extra=()):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        for k, v in extra:
            self.send_header(k, v)
        self.end_headers()
        if body:
            self.wfile.write(body)

    def do_GET(self):
        split = urllib.parse.urlsplit(self.path)

        if not self._authorised(split.query):
            self._send(403, b"<body style='background:#111;color:#eee;"
                            b"font-family:sans-serif;padding:40px'>"
                            b"<h2>Wrong or missing key</h2><p>Open the full "
                            b"link including the <code>?k=...</code> part."
                            b"</p></body>")
            return

        if split.path == "/state":
            self._send(200, target_info().encode(), "text/plain; charset=utf-8")
            return

        if split.path == "/timer":
            self._send(200, json.dumps(timer_info()).encode(),
                       "application/json")
            return

        if split.path == "/pointer":
            try:
                x, y = _pointer_pos()
                body = "%d %d" % (x, y)
            except Exception as exc:               # noqa: BLE001
                body = "error %r" % (exc,)
            self._send(200, body.encode(), "text/plain; charset=utf-8")
            return

        if dict(urllib.parse.parse_qsl(split.query)).get("k") == SECRET:
            self._send(302, extra=[("Set-Cookie",
                                   "%s=%s; Path=/; Max-Age=31536000; "
                                   "SameSite=Lax" % (COOKIE, SECRET)),
                                  ("Location", "/")])
            return

        page = PAGE.replace("__IP__", html.escape(local_ip())).encode()
        self._send(200, page, extra=[("Cache-Control", "no-store")])

    def do_POST(self):
        split = urllib.parse.urlsplit(self.path)
        if not self._authorised(split.query):
            self._send(403, b"", "text/plain")
            return
        if not split.path.startswith("/cmd/"):
            self._send(404, b"", "text/plain")
            return

        action = urllib.parse.unquote(split.path[len("/cmd/"):])
        fn = ACTIONS.get(action)
        if fn is None:
            self._send(404, b"", "text/plain")
            return
        params = dict(urllib.parse.parse_qsl(split.query))
        try:
            fn(params)
        except Exception as exc:                       # noqa: BLE001
            with open(os.path.join(HERE, "error.log"), "a") as fh:
                fh.write("%s %r -> %r\n" % (action, params, exc))
        self._send(204, b"", "text/plain")


if __name__ == "__main__":
    display()          # fail fast if DISPLAY is wrong
    _timer_load()
    threading.Thread(target=_timer_loop, daemon=True).start()
    print("tvremote listening on 0.0.0.0:%d (display %s)" % (PORT, DISPLAY))
    print("phone URL:  http://%s:%d/?k=%s" % (local_ip(), PORT, SECRET))
    if os.path.exists(DRYRUN_FILE):
        print("WARNING: DRYRUN set - power actions will NOT run")
    ThreadingHTTPServer(("0.0.0.0", PORT), Handler).serve_forever()
