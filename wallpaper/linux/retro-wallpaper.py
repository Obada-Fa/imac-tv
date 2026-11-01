#!/usr/bin/env python3
"""Retro Circuit as a live Linux desktop wallpaper (X11).

One borderless desktop-type window per monitor, under every other window, showing the same page
Lively / Wallpaper Engine use (wallpaper/dist/retro-circuit). Music levels come from the TV app's
own analyser (tvapp/audiolevel.py: the speaker monitor via parec / pw-record, never the mic).
Drawing pauses while a maximized or fullscreen window covers the monitor.

    python3 wallpaper/linux/retro-wallpaper.py [--interval MIN] [--dim PCT] [--brightness PCT]
                                               [--fps N] [--no-idle] [--no-shuffle]
    python3 wallpaper/linux/retro-wallpaper.py --install     # start with the desktop session
    python3 wallpaper/linux/retro-wallpaper.py --uninstall
"""
import argparse
import json
import os
import shlex
import sys
import urllib.parse

import gi
gi.require_version("Gtk", "3.0")
gi.require_version("Gdk", "3.0")
gi.require_version("WebKit2", "4.1")
from gi.repository import Gdk, GLib, Gtk, WebKit2  # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.normpath(os.path.join(HERE, "..", ".."))
PAGE = os.path.join(ROOT, "wallpaper", "dist", "retro-circuit", "index.html")
sys.path.insert(0, os.path.join(ROOT, "tvapp"))
import audiolevel  # noqa: E402

AUTOSTART = os.path.expanduser("~/.config/autostart/retro-circuit-wallpaper.desktop")
LEVEL_MS = 33          # push levels at ~30 Hz while music plays
COVER_MS = 1500        # how often to check for windows covering the desktop


def query(args):
    q = {"interval": args.interval, "dim": args.dim, "brightness": args.brightness, "fps": args.fps}
    if args.no_idle:
        q["idle"] = 0
    if args.no_shuffle:
        q["shuffle"] = 0
    if args.demo:
        q["demo"] = 1
    return urllib.parse.urlencode(q)


class Wallpaper:
    def __init__(self, args):
        self.views = []          # (monitor geometry, webview)
        url = "file://" + urllib.parse.quote(PAGE) + "?" + query(args)
        display = Gdk.Display.get_default()
        for m in range(display.get_n_monitors()):
            geo = display.get_monitor(m).get_geometry()
            win = Gtk.Window(title="Retro Circuit")
            win.set_type_hint(Gdk.WindowTypeHint.DESKTOP)
            win.set_decorated(False)
            win.set_keep_below(True)
            win.set_skip_taskbar_hint(True)
            win.set_skip_pager_hint(True)
            win.set_accept_focus(False)
            win.stick()
            win.move(geo.x, geo.y)
            win.set_default_size(geo.width, geo.height)
            view = WebKit2.WebView()
            s = view.get_settings()
            s.set_allow_file_access_from_file_urls(True)
            s.set_enable_developer_extras(bool(os.environ.get("RETRO_WALLPAPER_DEBUG")))
            if os.environ.get("RETRO_WALLPAPER_NOGL"):
                s.set_hardware_acceleration_policy(WebKit2.HardwareAccelerationPolicy.NEVER)
            view.load_uri(url)
            win.add(view)
            win.connect("destroy", Gtk.main_quit)
            win.show_all()
            self.views.append((geo, view, win))
        self.paused = [False] * len(self.views)
        self.audio = audiolevel.AudioLevels(fps=30)
        if not args.demo:            # demo levels come from the page itself
            self.audio.start()
        self.was_active = False
        GLib.timeout_add(LEVEL_MS, self.push_levels)
        GLib.timeout_add(COVER_MS, self.check_cover)

    def js(self, view, code):
        view.evaluate_javascript(code, -1, None, None, None, None, None)

    def push_levels(self):
        lv = self.audio.levels()
        active = bool(lv.get("active"))
        if active or self.was_active:
            code = "window.wpSetLevels && wpSetLevels(%s)" % json.dumps(lv)
            for i, (_geo, view, _win) in enumerate(self.views):
                if not self.paused[i]:
                    self.js(view, code)
        self.was_active = active
        return True

    def check_cover(self):
        """Pause a monitor's page while the active window is maximized/fullscreen on it."""
        screen = Gdk.Screen.get_default()
        act = screen.get_active_window()
        covered = None
        if act is not None:
            try:
                state = act.get_state()
                if state & (Gdk.WindowState.FULLSCREEN | Gdk.WindowState.MAXIMIZED):
                    x, y, w, h = act.get_frame_extents().x, act.get_frame_extents().y, \
                        act.get_frame_extents().width, act.get_frame_extents().height
                    covered = (x + w // 2, y + h // 2)
            except Exception:
                covered = None
        for i, (geo, view, win) in enumerate(self.views):
            mine = act is not None and win.get_window() is not None and act == win.get_window()
            p = bool(covered and not mine and geo.x <= covered[0] < geo.x + geo.width
                     and geo.y <= covered[1] < geo.y + geo.height)
            if p != self.paused[i]:
                self.paused[i] = p
                self.js(view, "window.wpSetPaused && wpSetPaused(%s)" % ("true" if p else "false"))
        return True


def install(argv):
    os.makedirs(os.path.dirname(AUTOSTART), exist_ok=True)
    cmd = " ".join(shlex.quote(a) for a in [sys.executable, os.path.abspath(__file__)] + argv)
    with open(AUTOSTART, "w") as f:
        f.write("[Desktop Entry]\nType=Application\nName=Retro Circuit wallpaper\n"
                "Exec=%s\nX-GNOME-Autostart-enabled=true\nNoDisplay=true\n" % cmd)
    print("installed", AUTOSTART)


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--interval", type=float, default=5, help="minutes per picture (default 5)")
    ap.add_argument("--dim", type=int, default=0, help="darken the picture, 0..60 %%")
    ap.add_argument("--brightness", type=int, default=100, help="runner brightness, 20..200 %%")
    ap.add_argument("--fps", type=int, default=30, help="frame cap while music plays (default 30)")
    ap.add_argument("--no-idle", action="store_true", help="hide the runners when no music plays")
    ap.add_argument("--no-shuffle", action="store_true", help="show the pictures in order")
    ap.add_argument("--demo", action="store_true", help="fake music (for testing)")
    ap.add_argument("--install", action="store_true", help="start with the desktop session (keeps the other options)")
    ap.add_argument("--uninstall", action="store_true", help="remove the autostart entry")
    args = ap.parse_args()
    if args.uninstall:
        if os.path.exists(AUTOSTART):
            os.remove(AUTOSTART)
        print("removed", AUTOSTART)
        return
    if args.install:
        install([a for a in sys.argv[1:] if a != "--install"])
        return
    if not os.path.exists(PAGE):
        raise SystemExit("build it first: python3 wallpaper/build.py")
    if os.environ.get("XDG_SESSION_TYPE") == "wayland" and not os.environ.get("RETRO_WALLPAPER_FORCE"):
        raise SystemExit("this host needs an X11 session (desktop windows are not available on Wayland)")
    Wallpaper(args)
    Gtk.main()


if __name__ == "__main__":
    main()
