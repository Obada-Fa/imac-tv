"""A small TV window that can appear above mpv without revealing the Home screen."""
from gi.repository import Gtk, Gdk


class PlaybackSuggestion(Gtk.Window):
    def __init__(self, prompt, respond, scale=1):
        super().__init__(title="Playback help")
        self.set_decorated(False)
        self.set_keep_above(True)
        self.set_skip_taskbar_hint(True)
        self.set_type_hint(Gdk.WindowTypeHint.DIALOG)
        self.set_position(Gtk.WindowPosition.CENTER_ALWAYS)
        self.set_default_size(round(800 * scale), -1)
        self.set_resizable(False)
        self.get_style_context().add_class("qr-dialog")
        self.respond = respond
        self.connect("delete-event", lambda *_: self.answer("dismiss"))
        self.connect("key-press-event", self.on_key)
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=round(18 * scale))
        box.set_border_width(round(28 * scale))
        self.add(box)

        def label(text, style):
            widget = Gtk.Label(label=text, xalign=0)
            widget.set_line_wrap(True)
            widget.set_max_width_chars(55)
            widget.get_style_context().add_class(style)
            box.pack_start(widget, False, False, 0)
        label(prompt["title"], "qr-dialog-title")
        label(prompt["message"], "hero-copy")
        label("Current: " + prompt["current"], "section-note")
        label("Try: " + prompt["alternative"], "hero-copy")
        label("Continue from your current position. Different editions may have different timing.", "section-note")
        row = Gtk.Box(spacing=round(16 * scale))
        box.pack_start(row, False, False, round(10 * scale))
        self.buttons = []
        for text, choice, style in (("Switch and continue", "switch", "hero-primary"),
                                    ("Not now" if prompt["failed"] else "Keep watching", "dismiss", "hero-secondary")):
            button = Gtk.Button(label=text)
            button.get_style_context().add_class(style)
            button.connect("clicked", lambda _b, c=choice: self.answer(c))
            row.pack_start(button, True, True, 0)
            self.buttons.append(button)
        label("Remote: OK to switch · Back to dismiss", "section-note")
        self.show_all()
        self.buttons[0].grab_focus()
        self.present()

    def answer(self, choice):
        self.set_sensitive(False)
        self.respond(choice)
        return True

    def on_key(self, _window, event):
        key = Gdk.keyval_name(event.keyval)
        if key in ("Escape", "BackSpace", "XF86Back", "Back"):
            return self.answer("dismiss")
        if key in ("Left", "Right", "Up", "Down"):
            other = self.buttons[1] if self.get_focus() is self.buttons[0] else self.buttons[0]
            other.grab_focus()
            return True
        return False
