"""The TV browser's menu: a band over Brave's kiosk window, in place of the toolbar it hides.

A window of its own, since Brave is a separate process. The controller sees it by its title (browser.MENU_TITLE) and
sends the phone's keys here while it is open. It only exists while open, and has no timers, so it costs nothing."""
import threading

import gi
gi.require_version("Gtk", "3.0")
from gi.repository import Gtk, Gdk, GLib, Pango

import browser
from screens import cmd

CLOSE_KEYS = ("Escape", "Menu", "Back", "BackSpace")
TAB_LIMIT = 6
ACTIONS = (("Back", "back"), ("Forward", "forward"), ("Reload", "reload"), ("Zoom −", "zoom_out"),
           ("100%", "zoom_reset"), ("Zoom +", "zoom_in"), ("Home", None), ("Close Brave", None))
PAGES = (("Shields", "shields"), ("Bookmarks", "bookmarks"), ("History", "history"), ("Downloads", "downloads"),
         ("Settings", "settings"))


def menu_css(scale):
    p = lambda n: max(1, round(n * scale))
    return f"""
.bm-band {{ background: #0f1b28; }}
.bm-note {{ color: rgba(255,255,255,0.6); font-size: {p(22)}px; }}
button.bm-btn {{ background: rgba(255,255,255,0.08); background-image: none; color: #fff; box-shadow: none;
    border: {p(3)}px solid transparent; border-radius: {p(16)}px; padding: {p(14)}px {p(24)}px;
    font-size: {p(26)}px; font-weight: 400; text-shadow: none; -gtk-icon-shadow: none; }}
button.bm-btn label {{ color: #fff; }}
button.bm-btn:focus {{ background: #2d536d; border-color: #c0e8f8; outline: none; }}
entry.bm-entry {{ background: rgba(255,255,255,0.08); background-image: none; color: #fff; box-shadow: none;
    border: {p(3)}px solid transparent; border-radius: {p(16)}px; padding: {p(14)}px {p(24)}px;
    font-size: {p(30)}px; min-height: {p(44)}px; caret-color: #fff; }}
entry.bm-entry:focus {{ border-color: #c0e8f8; background: #1d3546; }}
"""


class BrowserMenu(Gtk.Window):
    def __init__(self, scale=1.0):
        super().__init__(title=browser.MENU_TITLE)
        self.px = lambda n: max(1, round(n * scale))
        provider = Gtk.CssProvider()
        provider.load_from_data(menu_css(scale).encode())
        Gtk.StyleContext.add_provider_for_screen(Gdk.Screen.get_default(), provider,
                                                 Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION + 1)
        self.set_decorated(False)
        self.set_keep_above(True)
        self.set_skip_taskbar_hint(True)
        self.set_skip_pager_hint(True)
        self.connect("delete-event", lambda *_: self.close() or True)
        self.connect("key-press-event", self.on_key)
        self.fetch = 0

        band = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=self.px(16))
        self.get_style_context().add_class("bm-band")
        band.set_border_width(self.px(32))
        self.add(band)

        self.entry = Gtk.Entry(placeholder_text="Search or type a website")
        self.entry.get_style_context().add_class("bm-entry")
        self.entry.connect("activate", self.on_entry)
        band.pack_start(self.entry, False, False, 0)

        self.action_row = self.row(band)
        for label, do in ACTIONS:
            handler = {"Home": lambda *_: self.run("home"),
                       "Close Brave": self.command("quit")}.get(label) or self.command(do)
            self.action_row.pack_start(self.button(label, handler), False, False, 0)
        self.back_button = self.action_row.get_children()[0]

        self.tab_row = self.row(band)

        page_row = self.row(band)
        for label, do in PAGES:
            page_row.pack_start(self.button(label, self.command(do)), False, False, 0)
        band.show_all()

    def row(self, parent):
        box = Gtk.Box(spacing=self.px(12))
        parent.pack_start(box, False, False, 0)
        return box

    def button(self, label, handler):
        button = Gtk.Button(label=label)
        button.get_style_context().add_class("bm-btn")
        button.connect("clicked", handler)
        return button

    def command(self, do, **params):
        return lambda *_: self.run("browser", {"do": do, **params})

    def run(self, action, params=None):
        self.close()
        def work():
            try:
                cmd(action, params or {}, timeout=8)
            except Exception as exc:
                print("browser menu:", exc)
        threading.Thread(target=work, daemon=True).start()

    def on_entry(self, entry):
        text = entry.get_text().strip()
        if text:
            self.run("browser", {"do": "open", "text": text})

    def on_key(self, _window, event):
        key = Gdk.keyval_name(event.keyval)
        in_entry = self.get_focus() is self.entry
        if key in CLOSE_KEYS and not (in_entry and key == "BackSpace"):
            self.close()
            return True
        text = event.string
        if not in_entry and text and text.isprintable() and not text.isspace():
            # The phone's keyboard types wherever the focus is: send it to the address box
            self.entry.grab_focus_without_selecting()
            self.entry.set_text(self.entry.get_text() + text)
            self.entry.set_position(-1)
            return True
        return False

    def toggle(self):
        if self.get_visible():
            self.close()
        else:
            self.open()

    def open(self):
        monitor = Gdk.Display.get_default().get_primary_monitor() or Gdk.Display.get_default().get_monitor(0)
        area = monitor.get_geometry()
        self.set_default_size(area.width, -1)
        self.set_size_request(area.width, -1)
        self.move(area.x, area.y)
        self.entry.set_text("")
        self.show_tabs(None)
        self.back_button.grab_focus()
        self.present_with_time(Gtk.get_current_event_time() or Gdk.CURRENT_TIME)
        self.get_window().raise_()              # over Brave's fullscreen window, whichever way the WM stacks a new one
        self.get_window().focus(Gdk.CURRENT_TIME)
        self.fetch += 1
        threading.Thread(target=self.load_tabs, args=(self.fetch,), daemon=True).start()

    def close(self):
        self.fetch += 1
        self.hide()

    def load_tabs(self, fetch):
        try:
            found = browser.tabs()
        except (OSError, ValueError):
            found = None
        GLib.idle_add(lambda: fetch == self.fetch and self.show_tabs(found) and False)

    def show_tabs(self, tabs):
        focused = self.get_focus()
        for child in self.tab_row.get_children():
            self.tab_row.remove(child)
            child.destroy()
        if tabs is None:
            note = Gtk.Label(label="Tabs")
            note.get_style_context().add_class("bm-note")
            self.tab_row.pack_start(note, False, False, 0)
        else:
            for tab in tabs[:TAB_LIMIT]:
                button = self.button(tab["title"], self.command("tab", id=tab["id"]))
                label = button.get_child()
                label.set_ellipsize(Pango.EllipsizeMode.END)
                label.set_max_width_chars(16)
                self.tab_row.pack_start(button, False, False, 0)
            self.tab_row.pack_start(self.button("New tab", self.command("new_tab")), False, False, 0)
            self.tab_row.pack_start(self.button("Close tab", self.command("close_tab")), False, False, 0)
        self.tab_row.show_all()
        if focused is not None and focused.get_parent() is None:
            self.back_button.grab_focus()
        return False
