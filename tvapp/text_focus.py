"""Publish an opaque focus token for the phone. No field contents cross this boundary."""
import json
import os
import time

from gi.repository import GLib, Gtk


class TextFocus:
    def __init__(self, window, path):
        self.window, self.path = window, path
        self.field = self.external = None
        self.external_pid = None
        self.token = None
        self.written = 0
        window.connect_after("set-focus", lambda *_: self.update())
        window.connect("notify::is-active", lambda *_: self.update())
        # Native desktop apps can report their editable focus via the accessibility bus.
        try:
            import gi
            gi.require_version("Atspi", "2.0")
            from gi.repository import Atspi
            self.atspi = Atspi
            self.listener = Atspi.EventListener.new(self.accessible_focus)
            self.listener.register("object:state-changed:focused")
            self.listener.register("window:deactivate")
        except (ImportError, ValueError, GLib.Error):
            pass
        self.update()

    def accessible_focus(self, event, *_):
        try:
            if event.type.startswith("window:deactivate"):
                if self.external_pid == event.source.get_process_id():
                    self.external = self.external_pid = None
            elif event.detail1:
                self.external = self.external_pid = None
                if not self.window.is_active() and event.source.get_state_set().contains(self.atspi.StateType.EDITABLE):
                    self.external, self.external_pid = event.source, event.source.get_process_id()
            elif event.source == self.external:
                self.external = self.external_pid = None
            self.update()
        except GLib.Error:
            self.external = self.external_pid = None
            self.update()

    def update(self):
        if self.window.is_active():
            self.external = self.external_pid = None
            widget = self.window.get_focus()
            field = widget if (isinstance(widget, (Gtk.Entry, Gtk.TextView)) and widget.get_editable()
                               and widget.get_mapped() and widget.is_sensitive()) else None
            pid = os.getpid()
        else:
            field, pid = self.external, self.external_pid
        now = time.monotonic()
        if field == self.field and self.written and (field is None or now - self.written < 3):
            return
        if field != self.field:
            self.field = field
            self.token = str(time.monotonic_ns()) if field is not None else None
            if isinstance(field, Gtk.Entry) and field.get_name() == "nexus-search-field":
                self.token = "search:" + self.token
        try:
            tmp = self.path.with_suffix(".tmp")
            tmp.write_text(json.dumps({"token": self.token, "pid": pid}))
            os.replace(tmp, self.path)
            self.written = now
        except OSError:
            self.written = 0
