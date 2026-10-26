#!/usr/bin/env python3
"""Artwork-led, remote-friendly TV Home for iMac TV."""
import datetime
import fcntl
import json
import os
from pathlib import Path
import subprocess
import sys
import threading
import time

import gi
gi.require_version("Gtk", "3.0")
gi.require_version("GdkPixbuf", "2.0")
from gi.repository import Gtk, Gdk, GdkPixbuf, GLib

sys.path.insert(0, str(Path(__file__).resolve().parent))
import screens  # noqa: E402
from screens import (local_api, cmd, text_label, cover_pixbuf, grid_move, fresh_request,  # noqa: E402,F401
                     BrowseScreen, SearchScreen, DetailsScreen, SourcesScreen,
                     BufferingOverlay, Toast, BACK_KEYS as _BACK, ARROWS)

BACK_KEYS = tuple(_BACK) + ("Back",)    # GDK names the XF86Back keysym "Back"
from homescreen import AmbientHomeScreen, home_css, search_scope  # noqa: E402
from widgets.netinfo import get_network_info, make_qr_pixbuf, current_code, fmt_code, remote_connected  # noqa: E402,F401
import widgets
from widgets import pairing as pairing_ui  # noqa: E402
from widgets import chrome, glass  # noqa: E402
from theme import theme_css
from widgets.settings import SettingsScreen, show_trial_sheet, settings_css  # noqa: E402
import display  # noqa: E402

BASE = Path(__file__).resolve().parent
DATA = screens.DATA


def ui_scale(height):
    """Everything is drawn for 1080 rows; other heights scale it (720p gets 2/3-size text, 4K double)."""
    return max(0.5, height / 1080.0)


def build_css(scale=1.0, preferences=None):
    return f"""
window {{ background: #000; color: #f5f6f8; font-family: "Inter", "Noto Sans", "Noto Sans Arabic", sans-serif; }}
.page {{ background: #000; }}
.brand {{ font-size: {round(32 * scale)}px; font-weight: 900; letter-spacing: 2px; color: #fff; }}
.header-note {{ font-size: {round(24 * scale)}px; font-weight: 600; color: #d0d4dc; }}
.nav-button {{ background: transparent; border: 0; border-bottom: {round(4 * scale)}px solid transparent; border-radius: 0; color: #bdc1c9; font-size: {round(22 * scale)}px; font-weight: 700; padding: {round(6 * scale)}px {round(8 * scale)}px {round(12 * scale)}px; }}
.nav-button:focus, .nav-current {{ color: #fff; border-bottom-color: #fff; font-weight: 800; }}
.conn-pill {{ background: rgba(22, 25, 34, 0.92); border: {round(2 * scale)}px solid rgba(255, 255, 255, 0.22); border-radius: {round(16 * scale)}px; padding: {round(6 * scale)}px {round(14 * scale)}px; color: #fff; }}
.conn-pill:focus {{ border-color: #60a5fa; background: rgba(35, 43, 60, 0.98); box-shadow: 0 0 0 {round(3 * scale)}px rgba(96, 165, 250, 0.45); }}
.conn-wifi {{ font-size: {round(19 * scale)}px; font-weight: 700; color: #4ade80; }}
.conn-wifi-off {{ font-size: {round(19 * scale)}px; font-weight: 700; color: #f87171; }}
.conn-ip {{ font-size: {round(18 * scale)}px; font-weight: 600; color: #cbd5e1; }}
.conn-code {{ font-size: {round(22 * scale)}px; font-weight: 900; color: #facc15; font-family: monospace; letter-spacing: 1.5px; }}
.conn-qr-frame {{ background: #ffffff; border-radius: {round(8 * scale)}px; padding: {round(3 * scale)}px; }}
.conn-btn {{ background: rgba(35, 40, 52, 0.92); border: {round(2 * scale)}px solid rgba(255, 255, 255, 0.35); border-radius: {round(14 * scale)}px; color: #ffffff; font-size: {round(20 * scale)}px; font-weight: 700; padding: {round(10 * scale)}px {round(18 * scale)}px; }}
.conn-btn:focus {{ background: #2563eb; border-color: #ffffff; box-shadow: 0 0 0 {round(3 * scale)}px rgba(37, 99, 235, 0.5); }}
.hero-fade {{ background-image: linear-gradient(90deg, rgba(0,0,0,0.98) 0%, rgba(0,0,0,0.85) 32%, rgba(0,0,0,0.40) 58%, rgba(0,0,0,0.10) 100%); }}
.hero-bottom-fade {{ background-image: linear-gradient(0deg, rgba(0,0,0,0.95), rgba(0,0,0,0)); }}
.hero-kicker {{ font-size: {round(20 * scale)}px; font-weight: 800; letter-spacing: 2px; color: #f7c968; }}
.hero-title {{ font-size: {round(68 * scale)}px; font-weight: 900; color: #fff; }}
.hero-copy {{ font-size: {round(25 * scale)}px; color: #e2e3e7; }}
.hero-primary {{ background: #fff; border: {round(2 * scale)}px solid #fff; border-radius: {round(14 * scale)}px; color: #111; font-size: {round(23 * scale)}px; font-weight: 800; padding: {round(14 * scale)}px {round(28 * scale)}px; }}
.hero-primary:focus {{ box-shadow: 0 0 0 {round(4 * scale)}px rgba(255,255,255,0.5); }}
.hero-secondary {{ background: rgba(30,32,38,0.88); border: {round(2 * scale)}px solid rgba(255,255,255,0.4); border-radius: {round(14 * scale)}px; color: #fff; font-size: {round(23 * scale)}px; font-weight: 700; padding: {round(14 * scale)}px {round(26 * scale)}px; }}
.hero-secondary:focus {{ background: #363941; border-color: #fff; }}
.app-button {{ background: rgba(29,30,36,0.88); border: {round(2 * scale)}px solid rgba(255,255,255,0.24); border-radius: {round(18 * scale)}px; color: #fff; padding: {round(14 * scale)}px {round(22 * scale)}px; }}
.app-button:focus {{ background: rgba(57,59,67,0.98); border-color: #fff; box-shadow: 0 0 0 {round(3 * scale)}px rgba(255,255,255,0.35); }}
.app-name {{ font-size: {round(24 * scale)}px; font-weight: 800; color: #fff; }}
.app-heading {{ font-size: {round(18 * scale)}px; font-weight: 800; letter-spacing: 1.5px; color: #d7d9df; }}
.section-title {{ font-size: {round(34 * scale)}px; font-weight: 800; color: #f3f4f6; }}
.section-note {{ font-size: {round(18 * scale)}px; color: #aab0ba; }}
.feed-card {{ background: #16171a; border: {round(3 * scale)}px solid transparent; border-radius: {round(18 * scale)}px; color: #fff; padding: 0; }}
.feed-card:focus {{ border-color: #fff; box-shadow: 0 0 0 {round(4 * scale)}px rgba(255,255,255,0.4); }}
.feed-shade {{ background-image: linear-gradient(0deg, rgba(0,0,0,0.95), rgba(0,0,0,0.68) 65%, rgba(0,0,0,0)); padding: {round(20 * scale)}px {round(18 * scale)}px {round(14 * scale)}px; }}
.feed-title {{ font-size: {round(23 * scale)}px; font-weight: 800; color: #fff; }}
.feed-copy {{ font-size: {round(17 * scale)}px; color: #e1e2e5; }}
.empty-feed {{ background: #151619; border: {round(2 * scale)}px solid #303237; border-radius: {round(16 * scale)}px; color: #b4b7bd; padding: {round(18 * scale)}px {round(24 * scale)}px; }}
.empty-copy {{ font-size: {round(19 * scale)}px; color: #c4c7ce; }}
.status {{ font-size: {round(18 * scale)}px; font-weight: 600; color: #b5d8be; }}
.paircode {{ font-size: {round(18 * scale)}px; font-weight: 600; color: #a4aab4; }}
.qr-dialog {{ background: #11141c; color: #f8fafc; border: {round(3 * scale)}px solid #3b82f6; border-radius: {round(24 * scale)}px; padding: {round(24 * scale)}px; }}
.qr-dialog-title {{ font-size: {round(36 * scale)}px; font-weight: 900; color: #ffffff; }}
.qr-dialog-sub {{ font-size: {round(22 * scale)}px; color: #94a3b8; }}
.qr-dialog-code {{ font-size: {round(46 * scale)}px; font-weight: 900; letter-spacing: 5px; color: #facc15; font-family: monospace; }}
.qr-dialog-url {{ font-size: {round(22 * scale)}px; font-weight: 700; color: #60a5fa; font-family: monospace; }}
.progress trough {{ min-height: {round(6 * scale)}px; background: rgba(255,255,255,0.25); }}
.progress progress {{ min-height: {round(6 * scale)}px; background: #fff; }}
.tv-scrollbar {{ opacity: 0; }}
button {{ outline: none; }}
""".encode() + screens.build_css(scale).encode() + home_css(scale).encode() + settings_css(scale).encode() + theme_css(scale, preferences).encode()



class Home(Gtk.Window):
    """Window shell: header, screen stack with back stack, key routing, polling."""

    def __init__(self):
        super().__init__(title="iMac TV")
        subprocess.run(["xset", "s", "off", "-dpms"], stdout=subprocess.DEVNULL,
                       stderr=subprocess.DEVNULL, check=False)
        self.fullscreen()
        self.connect("delete-event", lambda *_: True)
        self.connect("key-press-event", self.on_key_press)
        self.connect("set-focus", self.on_set_focus)
        self.add_events(Gdk.EventMask.POINTER_MOTION_MASK | Gdk.EventMask.BUTTON_PRESS_MASK)
        self.connect("motion-notify-event", self.on_pointer)
        self.connect("button-press-event", self.on_pointer)
        screen = Gdk.Screen.get_default()
        if not display.trial_state()["active"]:
            display.restore_saved()               # the resolution confirmed in Settings, if that output is here
        self.sw, self.sh = self.target_size()
        self.set_default_size(self.sw, self.sh)
        self.scale = ui_scale(self.sh)
        self.css = None
        self.load_css()
        self.geom_id = 0
        self.trial_key = None
        screen.connect("size-changed", lambda *_: self.geometry_soon())
        screen.connect("monitors-changed", lambda *_: self.geometry_soon())
        display.start_watch()
        display.subscribe(self.on_screen_live)

        self.last_qr_text = ""
        self.last_net_check = 0
        self.last_state_poll = 0
        self.state_loading = False
        self.fast_until = 0
        self.prev_app = None
        self.last_error = None
        self.state_seen = False
        self.modal_open = False     # legacy flag, no longer set: overlays use self.sheet
        self.sheet = None
        self.net = None
        self.code = None
        self.remote = None
        self.remote_paired_at = None
        self.pair_listeners = []
        self.home_cfg = {}
        self.avoid_rect = None
        self.ui_handled = 0.0
        self.ui_mtime = None
        self.viz_written = (None, 0.0)          # last viz.json payload and when it was written
        self.tick_n = 0
        self.tops = {}
        self.back = []
        self.seq = 0

        root = Gtk.Overlay()
        self.root = root
        self.add(root)
        self.stack = Gtk.Stack()
        self.stack.set_transition_type(Gtk.StackTransitionType.CROSSFADE)
        self.stack.set_transition_duration(200)
        root.add(self.stack)
        self.header = self.make_header()
        self.header.set_valign(Gtk.Align.START)
        root.add_overlay(self.header)
        self.buffering = BufferingOverlay(self)
        self.buffering.connect("map", lambda *_: self.covered(True))
        self.buffering.connect("unmap", lambda *_: self.covered(False))
        root.add_overlay(self.buffering)
        self.toaster = Toast(self)
        root.add_overlay(self.toaster)

        # mouse / the phone's Pointer mode: clicks and hover reach the drawn (windowless) controls by hit test
        self.pointer = glass.Pointer(self, root=self.pointer_root, on_click=self.pointer_clicked,
                                     on_hover=lambda w: self.pointer_moved())
        self.trigger_network_refresh()
        self.show_all()
        self.buffering.hide()
        self.toaster.hide()
        self.push(self.top_screen("home"))
        GLib.timeout_add(500, self.tick)
        fake = os.environ.get("IMACTV_FAKE_BUFFERING")      # test hook: show the loading screen with this state
        if fake:
            try:
                self.fake_buffering = json.loads(fake)
                GLib.timeout_add(1500, lambda: self.buffering.show_for(self.fake_buffering) and False)
            except ValueError:
                pass

    # ---- geometry / resolution
    def load_css(self):
        screen = Gdk.Screen.get_default()
        if self.css:
            Gtk.StyleContext.remove_provider_for_screen(screen, self.css)
        self.css = Gtk.CssProvider()
        cfg = widgets.load_config(DATA)
        self.css.load_from_data(build_css(self.scale, cfg))
        Gtk.StyleContext.add_provider_for_screen(screen, self.css, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)
        if chrome.refresh_look(self, cfg):          # drawn pieces share one Look: repaint them in the new one
            self.queue_draw()

    def target_size(self):
        """Size of the monitor we are (or will be) fullscreen on."""
        fake = display.fake_geometry()
        if fake:
            return fake
        disp = Gdk.Display.get_default()
        win = self.get_window()
        mon = disp.get_monitor_at_window(win) if win else None
        mon = mon or disp.get_primary_monitor() or (disp.get_monitor(0) if disp.get_n_monitors() else None)
        if mon:
            g = mon.get_geometry()
            if g.width > 0 and g.height > 0:
                return g.width, g.height
        screen = Gdk.Screen.get_default()
        return screen.get_width(), screen.get_height()

    def geometry_soon(self, ms=400):
        if self.geom_id:
            GLib.source_remove(self.geom_id)
        self.geom_id = GLib.timeout_add(ms, self.check_geometry)

    def check_geometry(self):
        self.geom_id = 0
        w, h = self.target_size()
        if (w, h) != (self.sw, self.sh):
            self.relayout(w, h)
        return False

    def display_changed(self):
        """A mode was applied, kept or reverted (here or from the phone): re-measure, refresh Settings."""
        for ms in (300, 1500, 4000):              # X / the WM settle at their own pace
            GLib.timeout_add(ms, lambda: self.check_geometry() and False)
        cur = self.current()
        if isinstance(cur, SettingsScreen):
            cur.load()
        self.sync_trial()
        return False

    def relayout(self, w, h):
        """New monitor size: rebuild CSS, header and screens at the new size; reopen Settings if it was open.
        Ephemeral screens (Details, Sources) close; the trial sheet comes back from the next tick."""
        top = self.current().sname if self.current() else "home"
        settings = self.tops.get("settings")
        if settings:
            settings.remember()
        self.sw, self.sh = w, h
        self.scale = ui_scale(h)
        self.close_sheet()
        self.load_css()
        for child in list(self.stack.get_children()):
            if hasattr(child, "dispose"):
                child.dispose()
            self.stack.remove(child)
            child.destroy()
        self.back, self.tops, self.pair_listeners = [], {}, []
        self.root.remove(self.header)
        self.header.destroy()
        self.header = self.make_header()
        self.header.set_valign(Gtk.Align.START)
        self.root.add_overlay(self.header)
        self.root.reorder_overlay(self.header, 0)     # under the buffering overlay and toasts
        self.header.show_all()
        self.last_qr_text = ""
        self.trigger_network_refresh()
        self.push(self.top_screen("home"))
        if top in ("movies", "series", "search", "settings"):
            self.push(self.top_screen(top))
        self.set_default_size(w, h)               # after the old, larger screens are gone (they'd clamp it)
        self.resize(w, h)
        self.fullscreen()                         # ask the WM to re-fit us to the new monitor size
        self.sync_trial()

    def pointer_root(self):
        """Where pointer clicks can land: an open sheet only; nothing under the loading screen."""
        if self.buffering.get_visible():
            return None
        return self.sheet["widget"] if self.sheet else self.root

    def pointer_moved(self):
        screen = self.current()
        if hasattr(screen, "auto_focus"):
            screen.auto_focus = False

    def pointer_clicked(self, widget):
        """A click moved focus to `widget`: bring it into view as arrow keys would, before it is activated."""
        self.pointer_moved()
        screen = self.current()
        if screen and not self.sheet and widget.is_ancestor(screen):
            screen.reveal(widget)

    def covered(self, on):
        """The loading screen covers everything: unmap the screens and header under it, so its animation never
        repaints them (and Home's own timers stop); bring them back, with focus, when it goes."""
        self.stack.set_visible(not on)
        cur = self.current()
        self.header.set_visible(not on and bool(cur and cur.header))
        if not on:
            GLib.idle_add(self.focus_screen)

    def sync_trial(self):
        """Show / update / close the "Keep this resolution?" sheet from display.trial_state(); time it out."""
        if display.check_trial():
            self.display_changed()
            return False
        st = display.trial_state()
        key = (st.get("output"), st.get("mode"), st.get("rate")) if st["active"] else None
        if key != self.trial_key:                 # started / ended, maybe by the phone: re-measure
            self.trial_key = key
            self.display_changed()
            return False
        sh = self.sheet
        if st["active"]:
            if sh and sh["kind"] == "trial":
                sh["countdown"](st)
            else:
                show_trial_sheet(self, st)
        elif sh and sh["kind"] == "trial":
            self.close_sheet("trial")
        return False

    def on_screen_live(self, live):
        if live:                                  # back from screen-off: catch up now
            self.last_state_poll = 0
            self.last_net_check = 0

    # ---- helpers used by screens
    def px(self, n):
        return round(n * self.scale)

    def after(self, ms, fn):
        GLib.timeout_add(ms, lambda: fn() and False)

    def toast(self, text, secs=4):
        self.toaster.say(text, secs)

    # ---- header
    def make_header(self):
        """Home's language on the top bar: a light wordmark, tabs with a lit hexagon under the current one, a hex
        signal glyph, the phone chip, a small light clock and a hex power button (all drawn by widgets.chrome)."""
        look = chrome.look(self)
        bar = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=self.px(20))
        bar.get_style_context().add_class("header-bar")
        bar.pack_start(chrome.Wordmark(self, look), False, False, 0)
        nav = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=self.px(2))
        self.nav_buttons = {}
        for label, name in (("Home", "home"), ("Movies", "movies"), ("Series", "series"), ("Search", "search"),
                            ("Settings", "settings")):
            b = chrome.NavTab(self, look, label, "settings" if name == "settings" else None,
                              on_activate=lambda n=name: self.goto_top(n))
            nav.pack_start(b, False, False, 0)
            self.nav_buttons[name] = b
        bar.pack_start(nav, False, False, self.px(16))

        right = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=self.px(18))
        right.set_valign(Gtk.Align.CENTER)
        self.wifi_label = chrome.NetLabel(self, look)
        right.pack_start(self.wifi_label, False, False, self.px(4))
        self.conn_pill = chrome.PhoneChip(self, None, look, on_activate=lambda: self.show_pairing())
        right.pack_start(self.conn_pill, False, False, 0)
        self.clock = chrome.HeaderClock(self, look)
        right.pack_start(self.clock, False, False, self.px(6))
        self.power_btn = chrome.PowerButton(self, None, look, on_activate=lambda: self.confirm_off())
        right.pack_start(self.power_btn, False, False, 0)
        bar.pack_end(right, False, False, 0)
        self.header_row = list(self.nav_buttons.values()) + [self.conn_pill, self.power_btn]
        self.clock.set_now(datetime.datetime.now())
        return bar

    def set_phone_lines(self, connected):
        self.conn_pill.set_lines(*(("Phone connected", "Scan to pair another") if connected
                                   else ("Connect phone", "Scan to pair")))

    # ---- screens / back stack
    def top_screen(self, name):
        if name not in self.tops:
            self.tops[name] = {"home": AmbientHomeScreen, "search": SearchScreen, "settings": SettingsScreen,
                               "movies": lambda s: BrowseScreen(s, "movie"),
                               "series": lambda s: BrowseScreen(s, "series")}[name](self)
        return self.tops[name]

    def current(self):
        return self.back[-1] if self.back else None

    def push(self, screen):
        if screen.get_parent() is None:
            self.seq += 1
            self.stack.add_named(screen, f"s{self.seq}")
        screen.show_all()
        self.back.append(screen)
        self.stack.set_visible_child(screen)
        self.activate(screen, False)

    def drop(self, old):
        if old.ephemeral:
            GLib.timeout_add(500, lambda: old.destroy() and False)

    def pop(self):
        if len(self.back) <= 1:
            return False
        self.drop(self.back.pop())
        prev = self.back[-1]
        self.stack.set_visible_child(prev)
        self.activate(prev, True)
        return True

    def goto_top(self, name):
        while len(self.back) > 1:
            self.drop(self.back.pop())
        if name == "home":
            home = self.back[0]
            self.stack.set_visible_child(home)
            home.scroll_top()
            home.last_focus = None
            self.activate(home, True)
        else:
            self.push(self.top_screen(name))

    def activate(self, screen, returning):
        self.header.set_visible(screen.header)
        for name, b in self.nav_buttons.items():
            b.set_current((name == "home" and len(self.back) == 1) or
                          (len(self.back) > 1 and self.back[1].sname == name))
        screen.on_enter(returning)
        GLib.idle_add(self.focus_screen, screen)

    def open_details(self, item, season=None, episode=None):
        self.push(DetailsScreen(self, item, season, episode))

    def open_app(self, app):
        self.toast("Opening " + app.title() + "…", 3)
        threading.Thread(target=lambda: cmd("select", {"app": app}), daemon=True).start()

    def notify_pair(self):
        for fn in list(self.pair_listeners):
            fn()
        return False

    def play(self, params, name=None):
        self.toast("Starting " + (name or "playback") + "…", 5)
        self.fast_until = time.monotonic() + 30

        def run():
            res = cmd("play_media", params, timeout=8)
            err = None
            if res is None:
                err = "Can't reach the TV controller"
            elif isinstance(res, dict) and (res.get("error") or res.get("ok") is False):
                err = str(res.get("error") or "Couldn't start playback")
            if err:
                GLib.idle_add(self.toast, err, 5)
        threading.Thread(target=run, daemon=True).start()

    # ---- focus
    # ---- in-window sheets (pairing view, power confirm): no separate windows, so remote keys always reach us
    def open_sheet(self, kind, content, rows, default=None, refresh=None):
        self.close_sheet()
        scrim = Gtk.EventBox()
        scrim.get_style_context().add_class("ah-scrim")
        holder = Gtk.Box()
        holder.set_halign(Gtk.Align.CENTER)
        holder.set_valign(Gtk.Align.CENTER)
        holder.pack_start(content, False, False, 0)
        scrim.add(holder)
        self.root.add_overlay(scrim)
        scrim.show_all()
        self.sheet = {"kind": kind, "widget": scrim, "rows": rows, "default": default or rows[0][0],
                      "refresh": refresh}
        self.sheet["default"].grab_focus()

    def close_sheet(self, kind=None):
        sh = self.sheet
        if not sh or (kind and sh["kind"] != kind):
            return False
        self.sheet = None
        self.root.remove(sh["widget"])
        sh["widget"].destroy()
        GLib.idle_add(self.focus_screen)
        return True

    def groups(self):
        if self.sheet:
            return [[w for w in row if w.get_visible()] for row in self.sheet["rows"]]
        screen = self.current()
        rows = [self.header_row] if screen and screen.header else []
        rows += screen.focus_groups() if screen else []
        return [[w for w in row if w.get_visible()] for row in rows]

    def center_x(self, w, i):
        pos = w.translate_coordinates(self, 0, 0)
        return pos[0] + w.get_allocated_width() / 2 if pos and w.get_allocated_width() > 1 else i * 100

    def focus_screen(self, screen=None):
        if self.sheet:
            self.sheet["default"].grab_focus()
            return False
        screen = screen or self.current()
        if not screen:
            return False
        flat = [w for g in self.groups() for w in g]
        target = screen.last_focus if screen.last_focus in flat else screen.first_focus(
            [w for g in screen.focus_groups() for w in g])
        if target is None and flat:
            target = flat[0]
        if target:
            target.grab_focus()
            screen.reveal(target)
        return False

    def ensure_focus(self):
        f = self.get_focus()
        if self.sheet:
            if f is None or not any(f is w for row in self.sheet["rows"] for w in row):
                self.sheet["default"].grab_focus()
            return False
        if f is None or not f.get_mapped() or not f.get_sensitive():
            GLib.idle_add(self.focus_screen)
        return False

    def on_set_focus(self, _w, widget):
        screen = self.current()
        if screen and widget is not None and widget.is_ancestor(screen):
            screen.last_focus = widget
            if isinstance(screen, SettingsScreen):
                screen.track_focus(widget)

    def navigate(self, key):
        groups = self.groups()
        current = self.get_focus()
        loc = next(((r, c) for r, g in enumerate(groups) for c, w in enumerate(g) if w is current), None)
        xs = [[self.center_x(w, i) for i, w in enumerate(g)] for g in groups]
        dest = grid_move(xs, loc, key)
        if dest is None:
            return
        target = groups[dest[0]][dest[1]]
        target.grab_focus()
        screen = self.current()
        if screen:
            screen.reveal(target)

    def go_back(self):
        if self.pop():
            return
        screen = self.current()
        if hasattr(screen, "scroll_top"):
            screen.scroll_top()
        screen.last_focus = None
        self.focus_screen(screen)

    def on_pointer(self, *_):
        display.poke()
        screen = self.current()
        if hasattr(screen, "poke"):
            screen.poke()
        return False

    def on_key_press(self, _window, event):
        key = Gdk.keyval_name(event.keyval)
        display.poke()                  # someone is pressing keys: the screen is probably on
        screen = self.current()
        if hasattr(screen, "poke") and screen.poke(True):
            return True  # first key only wakes the ambient screen
        if self.buffering.get_visible():
            if key in BACK_KEYS:
                self.buffering.hide_now()
                self.last_state_poll = 0
                threading.Thread(target=lambda: cmd("back"), daemon=True).start()
            return True
        if self.sheet and self.sheet["kind"] == "trial" and key in BACK_KEYS:
            self.sheet["revert"]()
            return True
        if self.sheet:
            if key in ("p", "P") and self.sheet["kind"] == "trial":
                return True
            if key in BACK_KEYS or key in ("p", "P"):
                self.close_sheet()
                return True
            if key in ARROWS:
                self.navigate(key)
                return True
            return False        # Enter / space reach the focused sheet button
        if hasattr(screen, "handle_key") and screen.handle_key(key):
            return True
        focus = self.get_focus()
        in_entry = isinstance(focus, Gtk.Entry)
        if key in BACK_KEYS:
            if key == "BackSpace" and in_entry and focus.get_text():
                return False
            self.go_back()
            return True
        if in_entry and key in ("Left", "Right", "Home", "End"):
            return False
        if key in ("p", "P") and not in_entry:
            self.show_pairing()
            return True
        if key in ARROWS:
            if hasattr(self.current(), "auto_focus"):
                self.current().auto_focus = False
            self.navigate(key)
            return True
        return False

    # ---- ui_request.json
    def check_ui_request(self):
        path = DATA / "ui_request.json"
        try:
            mtime = path.stat().st_mtime
        except OSError:
            return
        if mtime == self.ui_mtime:
            return
        self.ui_mtime = mtime
        try:
            req = json.loads(path.read_text())
        except (OSError, ValueError):
            return
        if fresh_request(req, self.ui_handled, time.time()):
            self.ui_handled = req["ts"]
            self.handle_ui_request(req)

    def handle_ui_request(self, req):
        name = req.get("screen")
        if name == "viz_next":                  # phone remote: next visualizer effect, Home stays as it is
            home = self.tops.get("home")
            if home is not None and hasattr(home, "next_viz") and home.next_viz():
                self.write_viz_state(force=True)
            return
        self.close_sheet()
        if name == "search":
            scope = search_scope(getattr(self.current(), "sname", ""))
            self.goto_top("search")
            if hasattr(self.tops["search"], "set_scope"):
                self.tops["search"].set_scope(scope)
            if isinstance(req.get("query"), str):
                self.tops["search"].set_query(req["query"])
        elif name == "details" and isinstance(req.get("media"), dict) and req["media"].get("id"):
            self.show_media(req["media"])
        elif name in ("home", "movies", "series", "settings"):
            self.goto_top(name)
        else:
            return
        self.present()

    # ---- viz.json: tells the controller (and so the phone) whether a visualizer effect is showing
    def write_viz_state(self, force=False):
        home = self.tops.get("home")
        state = home.viz_status() if home is not None and hasattr(home, "viz_status") else {"active": False, "mode": None}
        now = time.monotonic()
        last, at = self.viz_written
        # rewritten every 5 s while active: the controller treats an old file as Home gone
        if not force and state == last and not (state["active"] and now - at >= 5):
            return
        self.viz_written = (state, now)
        path = DATA / "viz.json"
        try:
            tmp = path.with_suffix(".tmp")
            tmp.write_text(json.dumps(state))
            os.replace(tmp, path)
        except OSError:
            pass

    def show_media(self, media):
        """Details for media {type,id,season?,episode?}: refresh the open one or push a new one."""
        top = self.current()
        if isinstance(top, SourcesScreen):
            self.pop()
            top = self.current()
        if isinstance(top, DetailsScreen) and top.item.get("id") == media.get("id"):
            top.refresh_history()
            top.select_episode(media.get("season"), media.get("episode"))
            self.focus_screen(top)
        else:
            self.open_details({"type": media.get("type") or "movie", "id": media["id"],
                               "name": media.get("name") or media["id"]},
                              media.get("season"), media.get("episode"))

    def show_pairing(self):
        pairing_ui.show_dialog(self)

    def confirm_off(self):
        pairing_ui.confirm_off(self)

    def trigger_network_refresh(self):
        def worker():
            net = get_network_info()
            code = current_code()
            GLib.idle_add(self.update_network_ui, net, code)
        threading.Thread(target=worker, daemon=True).start()

    def update_network_ui(self, net, code):
        self.net, self.code = net, code
        for fn in list(self.pair_listeners):
            fn()
        if self.sheet and self.sheet["refresh"]:
            self.sheet["refresh"]()
        self.wifi_label.set_net(net)
        self.set_phone_lines(remote_connected(self.remote, lambda: False))
        return False

    # ---- state polling
    def poll_state(self):
        state = local_api("/api/state")
        GLib.idle_add(self.apply_state, state)

    def set_remote(self, remote):
        """Presence controls pairing UI; only a newly paired device gets a connected toast."""
        was = None if self.remote is None else remote_connected(self.remote, lambda: False)
        self.remote = remote if isinstance(remote, dict) else None
        self.set_phone_lines(remote_connected(self.remote, lambda: False))
        now = remote_connected(self.remote, lambda: False)
        paired_at = (self.remote or {}).get("paired_at")
        newly_paired = False
        if isinstance(paired_at, (int, float)):
            if self.remote_paired_at is not None:
                newly_paired = paired_at > self.remote_paired_at
                self.remote_paired_at = max(self.remote_paired_at, paired_at)
            else:
                self.remote_paired_at = paired_at       # startup baseline: existing phones stay quiet
        if newly_paired or (now and was is False):
            self.close_sheet("pairing")
        if newly_paired:
            GLib.idle_add(self.toast, "Phone connected", 3)

    def apply_state(self, state):
        self.state_loading = False
        if not state:
            return False
        self.settings_status = state
        settings = self.tops.get("settings")
        if settings and settings.category == "System":
            settings.update_system_status(state)
        self.set_remote(state.get("remote"))
        settings = self.tops.get("settings")
        if settings and not settings.torbox_busy and "torbox" in state:
            settings.update_torbox(state["torbox"]["configured"])
        GLib.idle_add(self.notify_pair)
        app, phase = state.get("app"), state.get("phase")
        if phase == "buffering":
            self.buffering.show_for(state)
            self.toaster.hide()
            self.fast_until = max(self.fast_until, time.monotonic() + 5)
        else:
            self.buffering.hide_now()
        err = state.get("error") or ""
        if self.state_seen and err and err != self.last_error:
            self.toast(err, 6)
        self.last_error = err
        if self.prev_app == "player" and app == "home" and self.state_seen:
            media = state.get("media")
            if isinstance(media, dict) and media.get("id"):
                self.show_media(media)
                self.present()
        self.prev_app = app
        self.state_seen = True
        return False

    def sync_home_settings(self):
        home = self.tops.get('home')
        if home is None:
            return
        stamp = home.home_stamp()
        if stamp == getattr(self, 'settings_stamp', None):
            return
        self.settings_stamp = stamp
        cfg = widgets.load_config(DATA)
        structural = ('style', 'widgets', 'node_shape', 'tile_shape', 'qr_style', 'shapes', 'visualizer', 'transparency', 'visual_effects')
        if not any(cfg[k] != home.cfg[k] for k in structural):
            home.cfg = cfg
            self.home_cfg = cfg
            home.interval = cfg['ambient']['interval']
            home.idle_quiet = cfg['ambient']['idle']
            home.idle_music = cfg['ambient']['idle_music']
            return
        self.load_css()
        was_current = self.current() is home
        # Pairing listeners belong to the old Home widgets.
        old_listeners = {getattr(w, 'render', None) for w in home.items}
        self.pair_listeners = [fn for fn in self.pair_listeners if fn not in old_listeners]
        home.dispose()
        replacement = AmbientHomeScreen(self)
        self.tops['home'] = replacement
        self.seq += 1
        self.stack.add_named(replacement, 's%d' % self.seq)
        replacement.show_all()
        self.back = [replacement if screen is home else screen for screen in self.back]
        if was_current:
            self.stack.set_visible_child(replacement)
            self.activate(replacement, True)
        self.stack.remove(home)
        home.destroy()

    def tick(self):
        self.tick_n += 1
        if self.tick_n % 2 == 0:
            self.sync_home_settings()
        self.check_ui_request()
        self.write_viz_state()
        self.sync_trial()
        live = display.screen_live()
        if self.tick_n % 2 == 0:
            now = time.monotonic()
            if live:                    # screen off: no clock, no Wi-Fi label, /api/state every 30 s
                self.clock.set_now(datetime.datetime.now())
                if now - self.last_net_check >= 4:
                    self.last_net_check = now
                    self.trigger_network_refresh()
            busy = self.buffering.get_visible() or now < self.fast_until or self.prev_app == "player"
            every = (1 if busy else 3) if live else 30
            if not self.state_loading and now - self.last_state_poll >= every:
                self.last_state_poll = now
                self.state_loading = True
                threading.Thread(target=self.poll_state, daemon=True).start()
        if self.tick_n % 4 == 0:
            self.ensure_focus()
        return True


if __name__ == "__main__":
    DATA.mkdir(mode=0o700, parents=True, exist_ok=True)
    lock_file = (DATA / "home.lock").open("a+")
    try:
        fcntl.flock(lock_file, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        raise SystemExit(0)
    Home()
    Gtk.main()
