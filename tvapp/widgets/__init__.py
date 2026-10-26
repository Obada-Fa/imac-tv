"""Home widget registry.

A widget is a small class: give it an `id`, a `slot` (where Home places it) and a `build(shell)` that returns
a Gtk widget. Decorate it with @register and import its module at the bottom of this file; the Home layout
comes from DATA/home.json ({"widgets": [...ids...]}), so a new widget is one file plus one id in that list.

Optional hooks: `interval` (seconds, 0 = never) + `refresh()` called on the GTK thread, `focus_items()` (the
focusable widgets of a D-pad row), `keep_in_ambient` + `on_ambient(on)`, `activate()`.
"""
import json
import os
from pathlib import Path
import re
import threading

DEFAULT_CONFIG = {
    "widgets": ["clock", "weather", "pairing", "continue", "apps"],
    "weather": {},
    "ambient": {"interval": 90, "idle": 120, "idle_music": 30},   # idle_music: while YouTube plays
    "style": "network",
    "transparency": True,
    "visual_effects": True,
    "node_shape": "hexagon",
    "tile_shape": "rounded",
    "shapes": "photo",
    "visualizer": True,
    "qr_style": "glass",
    "background": "photos",      # "photos" (Bing/Wikimedia) or "retro" (imported images, with Circuit)
    "retro_off": [],             # retro background ids switched off (everything is on by default)
}
STYLES = ("network", "bubbles")
NODE_SHAPES = ("hexagon", "octagon")
TILE_SHAPES = ("rounded", "chamfer")
QR_STYLES = ("glass", "light")
BACKGROUNDS = ("photos", "retro")
SHAPE_MODES = ("photo", "photo+animals", "off")

REGISTRY = {}

# home.json "style": "network" (default, glass tiles joined by lines) or "bubbles" (round drifting bubbles).

# Slots: where Home puts a widget. "row" widgets are bubble rows (D-pad rows, top to bottom).
SLOTS = ("topleft", "topright", "row")


class Widget:
    id = ""
    slot = "topleft"
    interval = 0
    keep_in_ambient = False
    # D-pad row ordering: lower = higher on screen
    order = 0

    def __init__(self, shell, config=None):
        self.shell = shell
        self.config = config or {}
        self.widget = None

    def build(self):
        raise NotImplementedError

    def refresh(self):
        pass

    def focus_items(self):
        return []

    def on_ambient(self, on):
        pass

    def on_enter(self, returning):
        pass

    def activate(self):
        pass

    def animate(self, dt, now):
        """Called by Home's animation timer (<= 20 fps, only while visible)."""


def register(cls):
    REGISTRY[cls.id] = cls
    return cls


def _int(v, default, lo, hi):
    if isinstance(v, bool) or not isinstance(v, (int, float)):
        return default
    return max(lo, min(hi, int(v)))


def parse_config(raw):
    """Merge a decoded home.json over the defaults; anything malformed falls back."""
    cfg = {"widgets": list(DEFAULT_CONFIG["widgets"]), "weather": {},
           "ambient": dict(DEFAULT_CONFIG["ambient"]), "style": DEFAULT_CONFIG["style"],
           "node_shape": DEFAULT_CONFIG["node_shape"], "tile_shape": DEFAULT_CONFIG["tile_shape"],
           "shapes": DEFAULT_CONFIG["shapes"], "visualizer": DEFAULT_CONFIG["visualizer"],
           "qr_style": DEFAULT_CONFIG["qr_style"], "background": DEFAULT_CONFIG["background"],
           "retro_off": []}
    cfg.update(transparency=True, visual_effects=True)
    if not isinstance(raw, dict):
        return cfg
    for key in ("transparency", "visual_effects"):
        if isinstance(raw.get(key), bool):
            cfg[key] = raw[key]
    if raw.get("style") in STYLES:
        cfg["style"] = raw["style"]
    if raw.get("node_shape") in NODE_SHAPES:
        cfg["node_shape"] = raw["node_shape"]
    if raw.get("qr_style") in QR_STYLES:
        cfg["qr_style"] = raw["qr_style"]
    if raw.get("background") in BACKGROUNDS:
        cfg["background"] = raw["background"]
    off = raw.get("retro_off")
    if isinstance(off, list):
        cfg["retro_off"] = sorted({i for i in off if isinstance(i, str) and re.fullmatch(r"[0-9a-f]{12}", i)})[:400]
    if raw.get("shapes") in SHAPE_MODES:
        cfg["shapes"] = raw["shapes"]
    if isinstance(raw.get("visualizer"), bool):
        cfg["visualizer"] = raw["visualizer"]
    if raw.get("tile_shape") in TILE_SHAPES:
        cfg["tile_shape"] = raw["tile_shape"]
    ws = raw.get("widgets")
    if isinstance(ws, list):
        ids = [w for w in ws if isinstance(w, str)]
        seen = []
        for w in ids:
            if w not in seen:
                seen.append(w)
        if seen or ws == []:
            cfg["widgets"] = seen
    w = raw.get("weather")
    if isinstance(w, dict):
        loc = {}
        if isinstance(w.get("place"), str) and w["place"].strip():
            loc["place"] = w["place"].strip()
        for k in ("lat", "lon"):
            v = w.get(k)
            if isinstance(v, (int, float)) and not isinstance(v, bool):
                loc[k] = float(v)
        if "lat" in loc and "lon" in loc:
            loc.setdefault("place", "")
            cfg["weather"] = loc
    a = raw.get("ambient")
    if isinstance(a, dict):
        cfg["ambient"]["interval"] = _int(a.get("interval"), 90, 10, 3600)
        cfg["ambient"]["idle"] = _int(a.get("idle"), 120, 10, 7200)
        cfg["ambient"]["idle_music"] = _int(a.get("idle_music"), 30, 10, 7200)
    return cfg


_save_lock = threading.Lock()


def save_keys(data_dir, updates):
    """Merge `updates` into home.json (atomic replace), keeping every other key. Returns the parsed config."""
    path = Path(data_dir) / "home.json"
    with _save_lock:
        try:
            raw = json.loads(path.read_text())
        except (OSError, ValueError):
            raw = {}
        if not isinstance(raw, dict):
            raw = {}
        updates = dict(updates)
        if isinstance(updates.get("ambient"), dict) and isinstance(raw.get("ambient"), dict):
            updates["ambient"] = dict(raw["ambient"], **updates["ambient"])
        raw.update(updates)
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_name("%s.%d.tmp" % (path.name, os.getpid()))
        tmp.write_text(json.dumps(raw, indent=2) + "\n")
        os.replace(tmp, path)
    return parse_config(raw)


def load_config(data_dir):
    try:
        raw = json.loads((Path(data_dir) / "home.json").read_text())
    except (OSError, ValueError):
        raw = None
    return parse_config(raw)


def build_all(shell, cfg):
    """Instantiate + build the configured widgets (unknown ids are skipped)."""
    out = []
    for wid in cfg["widgets"]:
        cls = REGISTRY.get(wid)
        if not cls:
            continue
        try:
            w = cls(shell, cfg)
            w.widget = w.build()
        except Exception as exc:  # one broken widget must not take Home down
            import sys
            print("widget", wid, "failed:", exc, file=sys.stderr)
            continue
        out.append(w)
    return out


from . import clock, weather_widget, pairing, rows  # noqa: E402,F401  (register built-ins)
