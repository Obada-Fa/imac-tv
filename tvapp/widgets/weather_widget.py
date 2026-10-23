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


FADE_S = 0.6        # ambient line fade in/out
SETTLE_S = 1.5      # after entering ambient, wait for Home's music check before showing the line


def compact_text(w):
    """'☁ 12° · rain from 23:00' for the small ambient line (no place), or None without data."""
    parts = weather_text(w)
    if not parts:
        return None
    nxt = (w.get("next") or "").strip()
    head = " ".join(x for x in (parts[0], parts[1]) if x)
    return head + (" · " + nxt[:1].lower() + nxt[1:] if nxt else "")


def ambient_line_visible(ambient, music, has_data):
    """The compact ambient line shows only in ambient mode, with data, while no music plays."""
    return bool(ambient and has_data and not music)


@register
class WeatherWidget(Widget):
    id = "weather"
    slot = "topleft"
    interval = 30 * 60
    keep_in_ambient = True      # on_ambient swaps the Home row for a compact line under the small clock
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
        self.amb = text_label("", "ah-wamb")
        css = Gtk.CssProvider()    # own style: homescreen.py's stylesheet belongs to another session
        css.load_from_data((".ah-wamb { font-size: %dpx; color: rgba(255,255,255,0.80); "
                            "text-shadow: 0 2px 3px rgba(0,0,0,0.55); }" % self.shell.px(24)).encode())
        self.amb.get_style_context().add_provider(css, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)
        self.amb.set_no_show_all(True)
        self.amb.set_opacity(0.0)
        box.pack_start(self.amb, False, False, 0)
        box.set_no_show_all(True)
        self.box = box
        self.ambient = False
        self.music = False
        self.has_data = False
        self.fade_id = 0
        self.settle = 0.0
        self.data = None
        self.loc = self.config.get("weather") or None
        self.apply(weather.current(screens.DATA) if weather else None)
        return box

    def want(self):
        return ambient_line_visible(self.ambient, self.music, self.has_data)

    def on_ambient(self, on):
        self.ambient = bool(on)
        self.settle = SETTLE_S if on else 0.0
        self.layout()
        self.fade_to()

    def on_music(self, playing):
        """Home tells us (about once a second while ambient) whether music is playing."""
        playing = bool(playing)
        if playing != self.music:
            self.music = playing
            self.fade_to()

    def layout(self):
        """Home row in normal mode; in ambient only the compact line, whose space is kept (opacity only)."""
        text = compact_text(self.data)
        self.has_data = text is not None
        if self.ambient:
            for c in (self.icon, self.temp, self.line):
                c.hide()
            self.amb.set_text(text or "")
            self.amb.set_visible(self.has_data)
            self.box.set_visible(self.has_data)
        else:
            self.amb.hide()
            self.amb.set_opacity(0.0)
            if self.has_data:
                for c in (self.icon, self.temp, self.line):
                    c.show()
            self.box.set_visible(self.has_data)

    def fade_to(self):
        if not self.fade_id and self.amb.get_opacity() != float(self.want()):
            self.fade_id = GLib.timeout_add(50, self.fade_tick)

    def fade_tick(self):
        target = 1.0 if self.want() else 0.0
        if self.settle > 0 and target > 0:       # still waiting for the first music report
            self.settle -= 0.05
            return True
        cur = self.amb.get_opacity()
        step = 0.05 / FADE_S
        cur = min(target, cur + step) if cur < target else max(target, cur - step)
        self.amb.set_opacity(cur)
        if cur == target:
            self.fade_id = 0
            return False
        return True

    def apply(self, w):
        parts = weather_text(w)
        self.data = w if parts else None
        if not parts:
            self.layout()
            self.fade_to()
            return False
        self.icon.set_text(parts[0])
        self.temp.set_text(parts[1])
        self.line.set_text(parts[2])
        self.layout()
        self.fade_to()
        return False

    def refresh(self):
        if weather:
            weather.refresh_async(screens.DATA, self.loc,
                                  on_done=lambda w: GLib.idle_add(self.apply, w) if w else None)
