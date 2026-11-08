"""Keep the TV a TV: no desktop dock, taskbar, start menu or top bar, and no idle mouse pointer.

While Home runs:
- GNOME / Zorin / Ubuntu: dock, taskbar and menu extensions are disabled, hot corners off, and the Super key no
  longer opens the overview or a menu. Fullscreen windows then cover the stock top bar.
- Cinnamon (Linux Mint): panels auto-hide, hot corners off.
- The mouse pointer is hidden (XFixes, whole screen) after IDLE_S without movement and comes back when it moves
  (a real mouse, the phone's touchpad or air pointer).
What was changed is saved in $DATA/kiosk_restore.json before changing it and put back when Home exits
(`python3 kiosk.py restore` does it by hand). Not run in tests or scratch-dir runs (IMACTV_DATA_DIR/IMACTV_DISPLAY
set) so a developer's desktop is never touched; IMACTV_KIOSK=0 turns it off.
"""
import atexit
import ctypes
import ctypes.util
import json
import os
import re
import signal
import subprocess
import sys
import time
from pathlib import Path

DATA = Path(os.environ.get("IMACTV_DATA_DIR", str(Path.home() / ".local/share/imactv")))
RESTORE = DATA / "kiosk_restore.json"
IDLE_S = 3.0
POLL_S = 0.25
# Extensions that draw a dock, taskbar, panel or app menu.
CHROME_EXT = re.compile(r"(taskbar|dock|dash-to|zorin-menu|arcmenu|arc-menu|apps-menu|panel|places-menu)", re.I)
GSETTINGS = [   # (schema, key, kiosk value)
    ("org.gnome.desktop.interface", "enable-hot-corners", "false"),
    ("org.gnome.mutter", "overlay-key", "''"),
    ("org.gnome.shell.extensions.dash-to-dock", "autohide", "true"),
    ("org.cinnamon", "hotcorner-layout", "['expo:false:0', 'scale:false:0', 'scale:false:0', 'desktop:false:0']"),
]


def enabled():
    if os.environ.get("IMACTV_KIOSK") == "0":
        return False
    return not (os.environ.get("IMACTV_DATA_DIR") or os.environ.get("IMACTV_DISPLAY"))


def _run(args, timeout=5):
    try:
        r = subprocess.run(args, capture_output=True, text=True, timeout=timeout, check=False)
        return r.stdout.strip() if r.returncode == 0 else None
    except (OSError, subprocess.SubprocessError):
        return None


def _gget(schema, key):
    return _run(["gsettings", "get", schema, key])


def _gset(schema, key, value):
    _run(["gsettings", "set", schema, key, value])


def _cinnamon_autohide(value):
    """panels-autohide entries are 'panelId:setting'; all panels become 'true'."""
    try:
        items = json.loads(value.replace("'", '"').lstrip("@as "))
    except ValueError:
        return None
    if not items:
        return None
    return "[" + ", ".join(f"'{i.split(':')[0]}:true'" for i in items) + "]"


def hide_chrome():
    saved = {}
    if RESTORE.exists():                       # a crash left kiosk values in place: keep the real originals
        try:
            saved = json.loads(RESTORE.read_text())
        except ValueError:
            saved = {}
    first = not saved
    exts = []
    listing = _run(["gnome-extensions", "list", "--enabled"])
    for uuid in (listing or "").split():
        if CHROME_EXT.search(uuid) and "appindicator" not in uuid:
            exts.append(uuid)
    settings = {}
    for schema, key, value in GSETTINGS:
        cur = _gget(schema, key)
        if cur is not None and cur != value:
            settings[f"{schema} {key}"] = cur
    auto = _gget("org.cinnamon", "panels-autohide")
    new_auto = _cinnamon_autohide(auto) if auto else None
    if new_auto and new_auto != auto:
        settings["org.cinnamon panels-autohide"] = auto
    if first:
        saved = {"extensions": exts, "settings": settings}
    else:                                      # add only what wasn't saved before
        saved.setdefault("extensions", [])
        saved["extensions"] += [e for e in exts if e not in saved["extensions"]]
        for k, v in settings.items():
            saved.setdefault("settings", {}).setdefault(k, v)
    DATA.mkdir(parents=True, exist_ok=True)
    RESTORE.write_text(json.dumps(saved, indent=1))
    for uuid in exts:
        _run(["gnome-extensions", "disable", uuid])
    for schema, key, value in GSETTINGS:
        if f"{schema} {key}" in settings:
            _gset(schema, key, value)
    if new_auto and new_auto != auto:
        _gset("org.cinnamon", "panels-autohide", new_auto)


def restore():
    try:
        saved = json.loads(RESTORE.read_text())
    except (OSError, ValueError):
        return
    for uuid in saved.get("extensions", []):
        _run(["gnome-extensions", "enable", uuid])
    for name, value in saved.get("settings", {}).items():
        schema, key = name.split(" ", 1)
        _gset(schema, key, value)
    try:
        RESTORE.unlink()
    except OSError:
        pass


# -- idle pointer -------------------------------------------------------------------------------------------------

def cursor_loop(parent):
    """Child process: hide the pointer screen-wide when it stops moving. Ends with its parent (XFixes undoes the
    hide when our X connection closes)."""
    X = ctypes.CDLL(ctypes.util.find_library("X11") or "libX11.so.6")
    Xf = ctypes.CDLL(ctypes.util.find_library("Xfixes") or "libXfixes.so.3")
    X.XOpenDisplay.restype = ctypes.c_void_p
    X.XOpenDisplay.argtypes = [ctypes.c_char_p]
    X.XDefaultRootWindow.restype = ctypes.c_ulong
    X.XDefaultRootWindow.argtypes = [ctypes.c_void_p]
    X.XQueryPointer.argtypes = [ctypes.c_void_p, ctypes.c_ulong] + [ctypes.c_void_p] * 2 + \
        [ctypes.POINTER(ctypes.c_int)] * 4 + [ctypes.POINTER(ctypes.c_uint)]
    X.XFlush.argtypes = [ctypes.c_void_p]
    Xf.XFixesHideCursor.argtypes = Xf.XFixesShowCursor.argtypes = [ctypes.c_void_p, ctypes.c_ulong]
    dpy = X.XOpenDisplay(None)
    if not dpy:
        return
    root = X.XDefaultRootWindow(dpy)
    w1, w2 = ctypes.c_ulong(), ctypes.c_ulong()
    rx, ry, wx, wy = (ctypes.c_int() for _ in range(4))
    mask = ctypes.c_uint()
    last, moved_at, hidden = None, time.monotonic(), False
    while os.getppid() == parent:
        X.XQueryPointer(dpy, root, ctypes.byref(w1), ctypes.byref(w2), ctypes.byref(rx), ctypes.byref(ry),
                        ctypes.byref(wx), ctypes.byref(wy), ctypes.byref(mask))
        pos = (rx.value, ry.value, mask.value)
        now = time.monotonic()
        if pos != last:
            last, moved_at = pos, now
            if hidden:
                Xf.XFixesShowCursor(dpy, root)
                X.XFlush(dpy)
                hidden = False
        elif not hidden and now - moved_at > IDLE_S:
            Xf.XFixesHideCursor(dpy, root)
            X.XFlush(dpy)
            hidden = True
        time.sleep(POLL_S)


def start():
    """Called once by Home at start-up."""
    if not enabled():
        return
    try:
        hide_chrome()
    except Exception as exc:                   # noqa: BLE001 - the desktop is never worth a crash
        print("kiosk:", exc)
    atexit.register(restore)
    try:                                       # Gtk.main holds the interpreter: let GLib deliver the signals
        from gi.repository import GLib, Gtk
        for sig in (signal.SIGTERM, signal.SIGHUP, signal.SIGINT):
            GLib.unix_signal_add(GLib.PRIORITY_HIGH, sig, lambda *_: (Gtk.main_quit(), False)[1])
    except Exception:                          # noqa: BLE001
        for sig in (signal.SIGTERM, signal.SIGHUP):
            signal.signal(sig, lambda *_: sys.exit(0))
    try:
        subprocess.Popen([sys.executable, os.path.abspath(__file__), "cursor", str(os.getpid())],
                         stdin=subprocess.DEVNULL, start_new_session=False)
    except OSError as exc:
        print("kiosk cursor:", exc)


if __name__ == "__main__":
    if sys.argv[1:2] == ["cursor"]:
        cursor_loop(int(sys.argv[2]))
    elif sys.argv[1:2] == ["restore"]:
        restore()
    elif sys.argv[1:2] == ["hide"]:
        hide_chrome()
