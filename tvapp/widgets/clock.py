"""Big light clock + date. Stays on screen (smaller) in ambient mode."""
import datetime
import sys
from pathlib import Path

import gi
gi.require_version("Gtk", "3.0")
from gi.repository import Gtk

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from screens import text_label  # noqa: E402
from . import Widget, register  # noqa: E402


DAYS = ("Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday")
MONTHS = ("January", "February", "March", "April", "May", "June", "July", "August", "September",
          "October", "November", "December")


def fmt_clock(now):
    """English on purpose (the rest of the UI is), whatever the system locale says."""
    return now.strftime("%H:%M"), f"{DAYS[now.weekday()]} {now.day} {MONTHS[now.month - 1]}"


@register
class ClockWidget(Widget):
    id = "clock"
    slot = "topleft"
    interval = 5
    keep_in_ambient = True
    order = 0

    def build(self):
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        self.time_lbl = text_label("", "ah-time")
        self.date_lbl = text_label("", "ah-date")
        box.pack_start(self.time_lbl, False, False, 0)
        box.pack_start(self.date_lbl, False, False, 0)
        self.box = box
        self._last = None
        self.refresh()
        return box

    def refresh(self):
        t, d = fmt_clock(datetime.datetime.now())
        if (t, d) != self._last:
            self._last = (t, d)
            self.time_lbl.set_text(t)
            self.date_lbl.set_text(d)

    def on_ambient(self, on):
        ctx = self.time_lbl.get_style_context()
        (ctx.add_class if on else ctx.remove_class)("ah-time-small")
        self.date_lbl.set_visible(not on)
