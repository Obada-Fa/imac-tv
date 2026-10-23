"""Weather line under the clock: icon, temperature, place and the "next" hint. Hidden until data exists."""
import sys
import threading
from pathlib import Path

import gi
gi.require_version("Gtk", "3.0")
from gi.repository import Gtk, GLib

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import screens  # noqa: E402
from screens import text_label  # noqa: E402
from . import Widget, register  # noqa: E402

try:
    import weather
except Exception:  # pragma: no cover - session A's module missing
    weather = None


def weather_text(w):
    """(icon, temp, place_line) for a weather dict; place_line = 'Place · next' (next optional)."""
    if not isinstance(w, dict) or w.get("temp") is None:
        return None
    try:
        temp = "%d°" % round(float(w["temp"]))
    except (TypeError, ValueError):
        return None
    line = " · ".join(x for x in (w.get("place") or "", w.get("next") or "") if x)
    return (w.get("icon") or "", temp, line)


@register
class WeatherWidget(Widget):
    id = "weather"
    slot = "topleft"
    interval = 30 * 60
    order = 1

    def build(self):
        box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=self.shell.px(14))
        box.set_margin_top(self.shell.px(10))
        self.icon = text_label("", "ah-wicon")
        self.temp = text_label("", "ah-wtemp")
        self.line = text_label("", "ah-wline")
        for w in (self.icon, self.temp, self.line):
            w.set_valign(Gtk.Align.CENTER)
            box.pack_start(w, False, False, 0)
        box.set_no_show_all(True)
        self.box = box
        self.loc = self.config.get("weather") or None
        self.apply(weather.current(screens.DATA) if weather else None)
        return box

    def apply(self, w):
        parts = weather_text(w)
        if not parts:
            self.box.hide()
            return False
        self.icon.set_text(parts[0])
        self.temp.set_text(parts[1])
        self.line.set_text(parts[2])
        for c in self.box.get_children():
            c.show()
        self.box.show()
        return False

    def refresh(self):
        if weather:
            weather.refresh_async(screens.DATA, self.loc,
                                  on_done=lambda w: GLib.idle_add(self.apply, w) if w else None)
