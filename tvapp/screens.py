"""10-foot media screens for TV Home: rails, browse, search, details, sources, overlays.

Everything that touches the network runs in worker threads and is applied with GLib.idle_add.
The window shell (stack, back stack, key routing, polling) lives in home.py.
"""
import hashlib
import json
import os
from pathlib import Path
import sys
import threading
import time
import urllib.error
import urllib.request
import weakref
from collections import OrderedDict
from concurrent.futures import ThreadPoolExecutor

import gi
gi.require_version("Gtk", "3.0")
gi.require_version("GdkPixbuf", "2.0")
from gi.repository import Gtk, Gdk, GdkPixbuf, GLib, Pango

try:
    gi.require_foreign("cairo")
    import cairo
except Exception:  # rounded corners are cosmetic
    cairo = None

BASE = Path(__file__).resolve().parent
if str(BASE) not in sys.path:
    sys.path.insert(0, str(BASE))
DATA = Path(os.environ.get("IMACTV_DATA_DIR", str(Path.home() / ".local/share/imactv")))
ASSETS = BASE / "assets"
ART = DATA / "art"

try:
    from media import addons
except Exception:  # pragma: no cover
    addons = None
try:
    from media import history
except Exception:
    history = None

METAHUB = "https://images.metahub.space"
BACK_KEYS = ("Escape", "BackSpace", "XF86Back")
ARROWS = ("Left", "Right", "Up", "Down")


# ---------------------------------------------------------------- pure helpers

def fmt_time(seconds):
    seconds = int(max(0, seconds or 0))
    h, rem = divmod(seconds, 3600)
    m, s = divmod(rem, 60)
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}"


def grid_move(xs, loc, key):
    """Focus-grid navigation. xs: rows of widget centre-x values; loc: (row, col) or None.
    Left/Right stay in the row; Up/Down jump to the nearest column of the next non-empty row."""
    rows = [list(r) for r in xs]
    live = [i for i, r in enumerate(rows) if r]
    if not live:
        return None
    if loc is None or not (0 <= loc[0] < len(rows)) or not rows[loc[0]]:
        return (live[0], 0)
    r, c = loc
    c = min(c, len(rows[r]) - 1)
    if key == "Left":
        return (r, max(0, c - 1))
    if key == "Right":
        return (r, min(len(rows[r]) - 1, c + 1))
    step = -1 if key == "Up" else 1
    nr = r + step
    while 0 <= nr < len(rows) and not rows[nr]:
        nr += step
    if not 0 <= nr < len(rows):
        return (r, c)
    x = rows[r][c]
    return (nr, min(range(len(rows[nr])), key=lambda i: abs(rows[nr][i] - x)))


def fresh_request(req, handled_ts, now, max_age=30):
    """True when a ui_request.json payload is new (newer than handled) and not stale."""
    if not isinstance(req, dict) or not isinstance(req.get("screen"), str):
        return False
    ts = req.get("ts")
    if isinstance(ts, bool) or not isinstance(ts, (int, float)):
        return False
    return ts > handled_ts and now - ts <= max_age


def stream_ref(source):
    """Stream selector for play_media; file_idx only when it is a real int."""
    ref = {"info_hash": source.get("info_hash")}
    idx = source.get("file_idx")
    if isinstance(idx, int) and not isinstance(idx, bool):
        ref["file_idx"] = idx
    return ref


def episodes_of(videos):
    """Regular episodes sorted by (season, episode); specials (season 0) only if nothing else."""
    eps = [v for v in videos or [] if isinstance(v.get("season"), int) and isinstance(v.get("episode"), int)]
    regular = [v for v in eps if v["season"] > 0]
    return sorted(regular or eps, key=lambda v: (v["season"], v["episode"]))


def pick_default_episode(videos, entries, series_id):
    """(season, episode) to continue: unfinished latest entry, else the one after a finished one, else first."""
    eps = episodes_of(videos)
    if not eps:
        return None
    mine = [e for e in (entries or {}).values()
            if isinstance(e, dict) and e.get("id") == series_id and isinstance(e.get("season"), int)]
    if mine:
        last = max(mine, key=lambda e: e.get("updated") or 0)
        key = (last["season"], last.get("episode"))
        order = [(v["season"], v["episode"]) for v in eps]
        if key in order:
            if not last.get("finished"):
                return key
            i = order.index(key)
            return order[min(i + 1, len(order) - 1)]
    return (eps[0]["season"], eps[0]["episode"])


def resume_seconds(entries, video_id):
    e = (entries or {}).get(video_id)
    if not e or e.get("finished"):
        return 0
    pos = e.get("position") or 0
    return pos if isinstance(pos, (int, float)) and pos >= 60 else 0


def video_id_for(kind, item_id, season=None, episode=None):
    return f"{item_id}:{season}:{episode}" if kind == "series" and season is not None else item_id


def poster_url(item):
    return item.get("poster") or (f"{METAHUB}/poster/medium/{item['id']}/img" if str(item.get("id", "")).startswith("tt") else None)


def backdrop_url(item):
    return item.get("background") or (f"{METAHUB}/background/large/{item['id']}/img" if str(item.get("id", "")).startswith("tt") else None)


def item_from_meta(m, kind=None):
    mid = m.get("id") or m.get("imdb_id")
    if not mid:
        return None
    genres = m.get("genres") or m.get("genre") or []
    return {"type": m.get("type") or kind, "id": mid, "name": m.get("name") or mid,
            "poster": m.get("poster"), "background": m.get("background"), "logo": m.get("logo"),
            "description": m.get("description") or "", "year": str(m.get("releaseInfo") or m.get("year") or ""),
            "rating": m.get("imdbRating"), "genres": [g for g in genres if isinstance(g, str)],
            "runtime": m.get("runtime") or ""}


def item_from_history(e):
    kind = e.get("type") or "movie"
    pos, dur = e.get("position") or 0, e.get("duration") or 0
    sub = ""
    if kind == "series" and e.get("season") is not None:
        sub = f"S{e['season']} E{e.get('episode')}"
    if pos and dur and not e.get("finished"):
        left = fmt_time(max(0, dur - pos))
        sub = (sub + " · " if sub else "") + left + " left"
    elif not pos and sub:
        sub += " · Up next"
    return {"type": kind, "id": e.get("id"), "name": e.get("name") or e.get("id"), "poster": e.get("poster"),
            "season": e.get("season"), "episode": e.get("episode"), "subtitle": sub,
            "progress": (pos / dur) if pos and dur and not e.get("finished") else None}


def history_entries():
    if history:
        try:
            return history.load()
        except Exception:
            pass
    try:
        data = json.loads((DATA / "history.json").read_text())
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def continue_items():
    entries = None
    if history and hasattr(history, "continue_watching"):
        try:
            entries = history.continue_watching(20)
        except Exception:
            entries = None
    if entries is None:  # history module missing or broken: derive it ourselves
        rows = [e for e in history_entries().values() if isinstance(e, dict) and not e.get("finished")
                and (e.get("position") or 0) > 60 and e.get("id")]
        entries = sorted(rows, key=lambda e: e.get("updated") or 0, reverse=True)[:20]
    seen, items = set(), []
    for e in entries:
        if not e.get("id") or (e.get("type"), e.get("id")) in seen:
            continue
        seen.add((e.get("type"), e.get("id")))
        items.append(item_from_history(e))
    return items


def catalog_items(kind, genre=None):
    metas = addons.catalog(kind, "top", genre=genre) if genre else addons.catalog(kind, "top")
    return [i for i in (item_from_meta(m, kind) for m in metas) if i]


def search_items(kind, query):
    return [i for i in (item_from_meta(m, kind) for m in addons.search(kind, query)) if i]


# ---------------------------------------------------------------- network helpers

def local_api(path):
    try:
        token = (DATA / "home_token").read_text().strip()
    except OSError:
        token = ""
    req = urllib.request.Request("http://127.0.0.1:8765" + path, headers={"Cookie": "imactv=" + token})
    try:
        with urllib.request.urlopen(req, timeout=1.5) as response:
            return json.load(response)
    except Exception:
        return None


def cmd(action, params=None, timeout=3):
    payload = json.dumps({"action": action, "params": params or {}}).encode()
    try:
        token = (DATA / "home_token").read_text().strip()
    except OSError:
        token = ""
    req = urllib.request.Request("http://127.0.0.1:8765/api/commands", data=payload,
                                 headers={"Content-Type": "application/json", "Cookie": "imactv=" + token},
                                 method="POST")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as response:
            return json.load(response)
    except urllib.error.HTTPError as exc:
        try:
            return json.load(exc)
        except Exception:
            return None
    except Exception:
        return None


_data_pool = ThreadPoolExecutor(4, thread_name_prefix="data")
_art_pool = ThreadPoolExecutor(6, thread_name_prefix="art")
_failed = {}
_mem = OrderedDict()


def fetch_file(url):
    """Download artwork into $DATA/art (hash-named) and return the path, or None."""
    if not url or not url.startswith(("http://", "https://")):
        return None
    path = ART / (hashlib.sha1(url.encode()).hexdigest() + ".img")
    try:
        if path.stat().st_size > 0:
            return path
    except OSError:
        pass
    if time.time() - _failed.get(url, 0) < 120:
        return None
    try:
        ART.mkdir(parents=True, exist_ok=True)
        req = urllib.request.Request(url, headers={"User-Agent": "imactv/1.0"})
        with urllib.request.urlopen(req, timeout=10) as r:
            blob = r.read(8_000_000)
        if not blob:
            raise OSError("empty")
        tmp = path.with_suffix(".part%d" % threading.get_ident())
        tmp.write_bytes(blob)
        tmp.replace(path)
        return path
    except Exception:
        _failed[url] = time.time()
        return None


def cover_pixbuf(path, width, height):
    """Scale artwork to fill width x height and crop the overflow without distorting it."""
    source = GdkPixbuf.Pixbuf.new_from_file(str(path))
    k = max(width / source.get_width(), height / source.get_height())
    sw, sh = max(width, round(source.get_width() * k)), max(height, round(source.get_height() * k))
    scaled = source.scale_simple(sw, sh, GdkPixbuf.InterpType.BILINEAR)
    return scaled.new_subpixbuf(max(0, (sw - width) // 2), max(0, (sh - height) // 2), width, height)


def fit_pixbuf(path, width, height):
    """Scale to fit inside width x height, keeping aspect and alpha (for logos)."""
    source = GdkPixbuf.Pixbuf.new_from_file(str(path))
    k = min(width / source.get_width(), height / source.get_height())
    return source.scale_simple(max(1, round(source.get_width() * k)), max(1, round(source.get_height() * k)),
                               GdkPixbuf.InterpType.BILINEAR)


def round_pixbuf(pb, radius):
    if not cairo or radius <= 0:
        return pb
    try:
        w, h = pb.get_width(), pb.get_height()
        surf = cairo.ImageSurface(cairo.FORMAT_ARGB32, w, h)
        ctx = cairo.Context(surf)
        r = radius
        ctx.new_sub_path()
        for cx, cy, a in ((w - r, r, -90), (w - r, h - r, 0), (r, h - r, 90), (r, r, 180)):
            ctx.arc(cx, cy, r, a * 3.14159265 / 180, (a + 90) * 3.14159265 / 180)
        ctx.close_path()
        ctx.clip()
        Gdk.cairo_set_source_pixbuf(ctx, pb, 0, 0)
        ctx.paint()
        return Gdk.pixbuf_get_from_surface(surf, 0, 0, w, h) or pb
    except Exception:
        return pb


def load_art(url, w, h, cb, radius=0, fit=False):
    """Fetch + scale in a worker; cb(pixbuf_or_None) runs on the GTK thread."""
    key = (url, w, h, radius, fit)
    if key in _mem:
        _mem.move_to_end(key)
        GLib.idle_add(cb, _mem[key])
        return

    def job():
        pb = None
        path = fetch_file(url)
        if path:
            try:
                pb = fit_pixbuf(path, w, h) if fit else round_pixbuf(cover_pixbuf(path, w, h), radius)
            except Exception:
                pb = None
        if pb is not None and w * h < 400_000:
            _mem[key] = pb
            while len(_mem) > 220:
                _mem.popitem(last=False)
        GLib.idle_add(cb, pb)
    _art_pool.submit(job)


def animate(adj, target, ms=200, _anims={}):
    start = adj.get_value()
    if abs(target - start) < 1:
        return
    key = id(adj)
    old = _anims.pop(key, None)
    if old:
        GLib.source_remove(old)
    t0 = time.monotonic()

    def step():
        p = min(1.0, (time.monotonic() - t0) * 1000 / ms)
        adj.set_value(start + (target - start) * (1 - (1 - p) ** 3))
        if p >= 1:
            _anims.pop(key, None)
            return False
        return True
    _anims[key] = GLib.timeout_add(16, step)


# ---------------------------------------------------------------- widgets

def text_label(text, style=None, wrap=False, lines=0, ellipsize=False):
    w = Gtk.Label(label=text)
    if style:
        w.get_style_context().add_class(style)
    w.set_halign(Gtk.Align.START)
    w.set_xalign(0)
    w.set_line_wrap(wrap)
    if wrap:
        w.set_line_wrap_mode(Pango.WrapMode.WORD_CHAR)
    if lines:
        w.set_lines(lines)
    if ellipsize or lines:
        w.set_ellipsize(Pango.EllipsizeMode.END)
    return w


class ArtImage(Gtk.Image):
    """Image that loads remote art asynchronously and drops stale results."""

    def __init__(self, w, h, radius=0, fit=False):
        super().__init__()
        self.w, self.h, self.radius, self.fit = w, h, radius, fit
        if fit:
            self.set_alignment(0, 1)
        self.set_size_request(w, h)
        self.token = 0

    def set_url(self, url):
        self.token += 1
        tok = self.token
        self.clear()
        if not url:
            return

        def done(pb):
            if tok == self.token and pb is not None:
                self.set_from_pixbuf(pb)
            return False
        load_art(url, self.w, self.h, done, self.radius, self.fit)


_skeletons = weakref.WeakSet()
_shimmer = {"on": False, "started": False}


def skeleton(w, h):
    box = Gtk.Box()
    box.get_style_context().add_class("skel")
    box.set_size_request(w, h)
    _skeletons.add(box)
    if not _shimmer["started"]:
        _shimmer["started"] = True

        def tick():
            _shimmer["on"] = not _shimmer["on"]
            for s in list(_skeletons):
                ctx = s.get_style_context()
                (ctx.add_class if _shimmer["on"] else ctx.remove_class)("skel-b")
            return True
        GLib.timeout_add(700, tick)
    return box


CARD_DIMS = {"poster": (190, 285), "wide": (330, 186), "episode": (300, 169), "app": (300, 96)}


def make_card(shell, item, kind, on_activate, on_focus):
    px = shell.px
    btn = Gtk.Button()
    btn.set_relief(Gtk.ReliefStyle.NONE)
    btn.get_style_context().add_class("card")
    btn.item = item
    if kind == "pill":
        btn.get_style_context().add_class("pill")
        btn.add(Gtk.Label(label=item.get("name", "")))
    elif kind == "app":
        btn.get_style_context().add_class("app-card")
        box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=px(16))
        box.set_margin_start(px(20))
        box.set_margin_end(px(28))
        box.set_margin_top(px(14))
        box.set_margin_bottom(px(14))
        icon = ASSETS / "app-icons" / item.get("icon", "")
        try:
            pb = GdkPixbuf.Pixbuf.new_from_file_at_scale(str(icon), px(64), px(64), True)
            box.pack_start(Gtk.Image.new_from_pixbuf(pb), False, False, 0)
        except (GLib.Error, TypeError):
            box.pack_start(Gtk.Image.new_from_icon_name("applications-multimedia", Gtk.IconSize.DIALOG), False, False, 0)
        box.pack_start(text_label(item.get("name", ""), "card-title"), False, False, 0)
        btn.add(box)
    else:
        w, h = (px(v) for v in CARD_DIMS[kind])
        col = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=px(8))
        ov = Gtk.Overlay()
        img = ArtImage(w, h, px(12))
        img.get_style_context().add_class("card-art")
        if kind == "poster":
            url = poster_url(item)
        elif kind == "episode":
            url = item.get("thumbnail")
        else:
            url = backdrop_url(item) or poster_url(item)
        img.set_url(url)
        ov.add(img)
        if item.get("progress") is not None:
            bar = Gtk.ProgressBar()
            bar.set_fraction(max(0.0, min(1.0, float(item["progress"]))))
            bar.set_valign(Gtk.Align.END)
            bar.set_margin_start(px(10))
            bar.set_margin_end(px(10))
            bar.set_margin_bottom(px(10))
            ov.add_overlay(bar)
        col.pack_start(ov, False, False, 0)
        for text, style in ((item.get("name", ""), "card-title"), (item.get("subtitle", ""), "card-sub")):
            if text or style == "card-title":
                lab = text_label(text, style, ellipsize=True)
                lab.set_max_width_chars(1)
                lab.set_size_request(w, -1)
                lab.set_margin_start(px(2))
                col.pack_start(lab, False, False, 0)
        btn.add(col)
    btn.set_margin_start(px(8))
    btn.set_margin_end(px(8))
    btn.set_margin_top(px(10))
    btn.set_margin_bottom(px(10))
    btn.connect("clicked", lambda *_: on_activate(item))
    btn.connect("focus-in-event", lambda *_: on_focus(item, btn) or False)
    return btn


class Rail(Gtk.Box):
    """Titled horizontal row of cards with skeleton / error / empty states."""

    def __init__(self, shell, title, loader=None, kind="poster", on_activate=None, on_focus=None,
                 hide_empty=True, quiet=False, limit=24):
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=shell.px(2))
        self.shell, self.title, self.loader, self.kind = shell, title, loader, kind
        self.on_activate, self.on_focus = on_activate or (lambda i: None), on_focus or (lambda i, b: None)
        self.hide_empty, self.quiet, self.limit = hide_empty, quiet, limit
        self.on_ready = None
        self.cards, self.items, self.token, self.state = [], [], 0, "idle"
        self.set_no_show_all(True)
        self.title_label = text_label(title, "rail-title") if title else None
        if self.title_label:
            self.title_label.set_margin_start(shell.px(8))
            self.pack_start(self.title_label, False, False, 0)
        self.scroller = Gtk.ScrolledWindow()
        self.scroller.set_policy(Gtk.PolicyType.EXTERNAL, Gtk.PolicyType.NEVER)
        self.scroller.set_shadow_type(Gtk.ShadowType.NONE)
        self.row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=0)
        self.scroller.add(self.row)
        self.pack_start(self.scroller, False, False, 0)
        if not quiet:
            self.show_rail()

    def show_rail(self):
        self.show()
        if self.title_label:
            self.title_label.show()
        self.scroller.show_all()

    def _clear(self):
        for c in self.row.get_children():
            self.row.remove(c)
            c.destroy()
        self.cards = []

    def _skeleton(self):
        self._clear()
        w, h = (self.shell.px(v) for v in CARD_DIMS.get(self.kind, CARD_DIMS["wide"]))
        for _ in range(8):
            s = skeleton(w, h + self.shell.px(46))
            s.set_margin_start(self.shell.px(8))
            s.set_margin_end(self.shell.px(8))
            s.set_margin_top(self.shell.px(10))
            s.set_margin_bottom(self.shell.px(10))
            self.row.pack_start(s, False, False, 0)
        self.show_rail()

    def load(self, loader=None, skeleton_first=True):
        loader = loader or self.loader
        if not loader:
            return
        self.token += 1
        tok = self.token
        self.state = "loading"
        if skeleton_first and not self.quiet:
            self._skeleton()

        def work():
            try:
                res, err = loader(), None
            except Exception as exc:
                res, err = None, exc
            GLib.idle_add(self._done, tok, res, err, loader)
        _data_pool.submit(work)

    def start(self):
        if self.state == "idle":
            self.load()

    def reload(self):
        self.load(skeleton_first=False)

    def _done(self, tok, res, err, loader):
        if tok != self.token:
            return False
        if err is not None:
            self.state = "error"
            self._error(loader)
        else:
            self.state = "ready"
            self.set_items(res or [])
        return False

    def _error(self, loader):
        had = any(c.has_focus() for c in self.cards)
        self._clear()
        card = Gtk.Button()
        card.set_relief(Gtk.ReliefStyle.NONE)
        card.get_style_context().add_class("card")
        card.get_style_context().add_class("err-card")
        card.set_size_request(self.shell.px(520), self.shell.px(110))
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=self.shell.px(4))
        box.pack_start(text_label("Couldn't load this row", "card-title"), False, False, 0)
        box.pack_start(text_label("Check the internet connection, then press OK to retry", "card-sub"), False, False, 0)
        box.set_margin_start(self.shell.px(24))
        box.set_valign(Gtk.Align.CENTER)
        card.add(box)
        card.set_margin_start(self.shell.px(8))
        card.set_margin_top(self.shell.px(10))
        card.set_margin_bottom(self.shell.px(10))
        card.connect("clicked", lambda *_: self.load(loader))
        card.connect("focus-in-event", lambda *_: self.on_focus(None, card) or False)
        self.row.pack_start(card, False, False, 0)
        self.cards = [card]
        self.show_rail()
        self.row.show_all()
        if had:
            card.grab_focus()
        self.shell.ensure_focus()

    def set_items(self, items):
        items = list(items)[:self.limit]
        focused = next((i for i, c in enumerate(self.cards) if c.has_focus()), None)
        self.items = items
        self._clear()
        if not items:
            if self.hide_empty:
                self.hide()
            else:
                self.row.pack_start(text_label("Nothing here yet", "card-sub"), False, False, 0)
                self.show_rail()
        else:
            for item in items:
                card = make_card(self.shell, item, self.kind, self.on_activate, self.on_focus)
                self.row.pack_start(card, False, False, 0)
                self.cards.append(card)
            self.show_rail()
            self.row.show_all()
            if focused is not None:
                self.cards[min(focused, len(self.cards) - 1)].grab_focus()
            elif self.on_ready:
                self.on_ready(self)
        self.shell.ensure_focus()

    def reveal_h(self, btn):
        pos = btn.translate_coordinates(self.row, 0, 0)
        if not pos:
            return
        adj = self.scroller.get_hadjustment()
        x, w = pos[0], btn.get_allocated_width()
        cur, page, m = adj.get_value(), adj.get_page_size(), self.shell.px(70)
        if x < cur + m:
            animate(adj, max(0, x - m))
        elif x + w > cur + page - m:
            animate(adj, x + w - page + m)


class Screen(Gtk.Box):
    header = False
    ephemeral = False
    sname = ""

    def __init__(self, shell):
        super().__init__(orientation=Gtk.Orientation.VERTICAL)
        self.shell = shell
        self.last_focus = None

    def focus_groups(self):
        return []

    def first_focus(self, flat):
        return flat[0] if flat else None

    def on_enter(self, returning):
        pass

    def reveal(self, widget):
        rail = widget.get_ancestor(Rail)
        if rail:
            rail.reveal_h(widget)


class RailsScreen(Screen):
    """Pinned hero for the focused item over vertically scrolling rails (Home, Browse)."""
    header = True

    def __init__(self, shell):
        super().__init__(shell)
        self.rails = []
        self.hero_item = None
        self.auto_focus = True
        self.hero_timer = 0
        self.hero_token = 0
        self.hero_h = max(shell.px(440), round(shell.sh * 0.45))
        self.pack_start(self.make_hero(), False, False, 0)
        self.vscroll = Gtk.ScrolledWindow()
        self.vscroll.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.EXTERNAL)
        self.vscroll.set_shadow_type(Gtk.ShadowType.NONE)
        self.rails_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=shell.px(6))
        self.rails_box.set_margin_start(shell.px(60))
        self.rails_box.set_margin_end(shell.px(60))
        self.rails_box.set_margin_bottom(shell.px(380))
        self.vscroll.add(self.rails_box)
        self.pack_start(self.vscroll, True, True, 0)

    def make_hero(self):
        s, px = self.shell, self.shell.px
        ov = Gtk.Overlay()
        ov.set_size_request(s.sw, self.hero_h)
        self.hero_img = Gtk.Image()
        self.hero_img.set_size_request(s.sw, self.hero_h)
        ov.add(self.hero_img)
        for cls, valign, size in (("hero-fade", Gtk.Align.FILL, -1), ("hero-bottom-fade", Gtk.Align.END, px(200))):
            fade = Gtk.Box()
            fade.get_style_context().add_class(cls)
            fade.set_valign(valign)
            fade.set_size_request(-1, size)
            ov.add_overlay(fade)
        body = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=px(8))
        body.set_valign(Gtk.Align.END)
        body.set_margin_start(px(76))
        body.set_margin_bottom(px(18))
        self.h_kicker = text_label("", "hero-kicker")
        self.h_title = text_label("", "hero-title", wrap=True, lines=2)
        self.h_title.set_max_width_chars(30)
        self.h_info = text_label("", "hero-info")
        self.h_copy = text_label("", "hero-copy", wrap=True, lines=3)
        self.h_copy.set_max_width_chars(72)
        for w in (self.h_kicker, self.h_title, self.h_info, self.h_copy):
            body.pack_start(w, False, False, 0)
        ov.add_overlay(body)
        return ov

    def rail_ready(self, rail):
        """Until the user moves, keep focus on the first populated rail as rows load in."""
        if not self.auto_focus or rail not in self.rails:
            return
        focus = self.shell.get_focus()
        cur = focus.get_ancestor(Rail) if focus is not None else None
        if cur is None or (cur in self.rails and self.rails.index(rail) < self.rails.index(cur)):
            rail.cards[0].grab_focus()
            self.reveal(rail.cards[0])

    def add_rail(self, rail):
        rail.on_ready = self.rail_ready
        self.rails.append(rail)
        self.rails_box.pack_start(rail, False, False, 0)
        return rail

    def make_rail(self, title, loader, kind="poster", **kw):
        return self.add_rail(Rail(self.shell, title, loader, kind, self.activate, self.focus_item, **kw))

    def activate(self, item):
        if item.get("app"):
            self.shell.open_app(item["app"])
        else:
            self.shell.open_details(item)

    def focus_groups(self):
        return [r.cards for r in self.rails if r.cards and r.get_visible()]

    def focus_item(self, item, btn):
        rail = btn.get_ancestor(Rail)
        if rail in self.rails:
            i = self.rails.index(rail)
            for r in self.rails[i:i + 3]:
                r.start()
        if item:
            self.set_hero(item)
        return False

    def set_hero(self, item):
        if item is self.hero_item:
            return
        self.hero_item = item
        kicker = (item.get("type") or "").upper()
        if item.get("progress") is not None:
            kicker = "CONTINUE WATCHING"
        self.h_kicker.set_text(kicker or (item.get("kicker") or ""))
        self.h_title.set_text(item.get("name") or "")
        parts = [item.get("year"), item.get("runtime"),
                 ("★ " + str(item["rating"])) if item.get("rating") else "", " · ".join((item.get("genres") or [])[:3])]
        self.h_info.set_text("  ·  ".join(p for p in parts if p) or item.get("subtitle") or "")
        self.h_copy.set_text(item.get("description") or item.get("tagline") or "")
        if self.hero_timer:
            GLib.source_remove(self.hero_timer)
        self.hero_timer = GLib.timeout_add(160, self._hero_image, item)

    def _hero_image(self, item):
        self.hero_timer = 0
        self.hero_token += 1
        tok = self.hero_token
        url = backdrop_url(item) if item.get("id") else None
        if not url:
            self.hero_img.clear()
            return False

        def done(pb):
            if tok == self.hero_token and pb is not None:
                self.hero_img.set_from_pixbuf(pb)
            return False
        load_art(url, self.shell.sw, self.hero_h, done)
        return False

    def reveal(self, widget):
        super().reveal(widget)
        rail = widget.get_ancestor(Rail)
        if rail:
            pos = rail.translate_coordinates(self.rails_box, 0, 0)
            if pos:
                animate(self.vscroll.get_vadjustment(), max(0, pos[1] - self.shell.px(4)), 220)

    def scroll_top(self):
        self.vscroll.get_vadjustment().set_value(0)

    def on_enter(self, returning):
        for r in self.rails[:3]:
            r.start()


class HomeScreen(RailsScreen):
    sname = "home"

    def __init__(self, shell):
        super().__init__(shell)
        self.cont = self.make_rail("Continue watching", continue_items, "wide", quiet=True)
        self.make_rail("Popular movies", lambda: catalog_items("movie"))
        self.make_rail("Popular series", lambda: catalog_items("series"))
        apps = self.make_rail("Apps", None, "app")
        apps.set_items([{"app": "youtube", "name": "YouTube", "icon": "vacuumtube.svg",
                         "kicker": "APP", "tagline": "Watch YouTube on the big screen with VacuumTube."}])
        apps.state = "ready"
        for title, kind, genre in (("Action movies", "movie", "Action"), ("Comedy movies", "movie", "Comedy"),
                                   ("Drama series", "series", "Drama"), ("Animated series", "series", "Animation")):
            self.make_rail(title, lambda k=kind, g=genre: catalog_items(k, g))
        self.cont.start()

    def on_enter(self, returning):
        if returning and self.cont.state != "idle":
            self.cont.reload()
        super().on_enter(returning)

    def first_focus(self, flat):
        groups = self.focus_groups()
        return groups[0][0] if groups else None


GENRES = {"movie": ["Action", "Comedy", "Drama", "Thriller", "Sci-Fi", "Horror", "Animation", "Documentary"],
          "series": ["Drama", "Comedy", "Crime", "Animation", "Sci-Fi", "Mystery", "Documentary", "Reality-TV"]}


class BrowseScreen(RailsScreen):
    def __init__(self, shell, kind):
        super().__init__(shell)
        self.kind = kind
        self.sname = "movies" if kind == "movie" else "series"
        noun = "movies" if kind == "movie" else "series"
        self.make_rail("Popular " + noun, lambda: catalog_items(kind))
        for g in GENRES[kind]:
            self.make_rail(g, lambda g=g: catalog_items(kind, g))

    def first_focus(self, flat):
        groups = self.focus_groups()
        return groups[0][0] if groups else None


class SearchScreen(Screen):
    header = True
    sname = "search"

    def __init__(self, shell):
        super().__init__(shell)
        px = shell.px
        self.token = 0
        self.timer = 0
        self.set_margin_top(px(130))
        self.set_margin_start(px(68))
        self.set_margin_end(px(68))
        self.entry = Gtk.Entry()
        self.entry.set_placeholder_text("Search movies and series")
        self.entry.get_style_context().add_class("search-entry")
        self.entry.set_size_request(-1, px(76))
        self.entry.connect("changed", self.on_changed)
        self.entry.connect("activate", lambda *_: self.run())
        self.pack_start(self.entry, False, False, 0)
        self.info = text_label("Type on your phone to search", "section-note")
        self.info.set_margin_top(px(14))
        self.info.set_margin_bottom(px(10))
        self.pack_start(self.info, False, False, 0)
        scroll = Gtk.ScrolledWindow()
        scroll.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.EXTERNAL)
        scroll.set_shadow_type(Gtk.ShadowType.NONE)
        self.scroll = scroll
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=px(6))
        box.set_margin_bottom(px(200))
        scroll.add(box)
        self.pack_start(scroll, True, True, 0)
        self.movies = Rail(shell, "Movies", kind="poster", on_activate=shell.open_details, quiet=True)
        self.series = Rail(shell, "Series", kind="poster", on_activate=shell.open_details, quiet=True)
        self.counts = {}
        for r in (self.movies, self.series):
            box.pack_start(r, False, False, 0)

    def focus_groups(self):
        return [[self.entry]] + [r.cards for r in (self.movies, self.series) if r.cards and r.get_visible()]

    def first_focus(self, flat):
        return self.entry

    def on_enter(self, returning):
        self.entry.grab_focus()

    def set_query(self, text):
        self.entry.set_text(text)
        self.run()

    def on_changed(self, entry):
        if self.timer:
            GLib.source_remove(self.timer)
        self.timer = GLib.timeout_add(500, self.run)

    def run(self):
        if self.timer:
            GLib.source_remove(self.timer)
        self.timer = 0
        q = self.entry.get_text().strip()
        self.token += 1
        tok = self.token
        self.counts = {}
        if not q:
            for r in (self.movies, self.series):
                r.token += 1
                r.set_items([])
            self.info.set_text("Type on your phone to search")
            return False
        self.info.set_text(f"Searching for “{q}”…")
        for kind, rail in (("movie", self.movies), ("series", self.series)):
            rail.hide_empty = True
            rail.load(lambda k=kind: search_items(k, q))
            self.watch(rail, tok, q)
        return False

    def watch(self, rail, tok, q):
        def check():
            if tok != self.token:
                return False
            if rail.state in ("loading", "idle"):
                return True
            self.counts[id(rail)] = (rail.state, len(rail.items))
            if len(self.counts) == 2:
                total = sum(n for _s, n in self.counts.values())
                failed = all(s == "error" for s, _n in self.counts.values())
                if failed:
                    self.info.set_text("Can't reach the catalog. Check the internet connection.")
                elif total == 0:
                    self.info.set_text(f"No results for “{q}”")
                else:
                    self.info.set_text(f"Results for “{q}”")
            return False
        GLib.timeout_add(150, check)

    def reveal(self, widget):
        super().reveal(widget)
        rail = widget.get_ancestor(Rail)
        if rail:
            pos = rail.translate_coordinates(self.scroll.get_child(), 0, 0)
            if pos:
                animate(self.scroll.get_vadjustment(), max(0, pos[1] - self.shell.px(4)), 220)
        else:
            animate(self.scroll.get_vadjustment(), 0, 200)


class DetailsScreen(Screen):
    ephemeral = True

    def __init__(self, shell, item, season=None, episode=None):
        super().__init__(shell)
        px = shell.px
        self.item = dict(item)
        self.kind = self.item.get("type") or "movie"
        self.meta_ok = False
        self.videos = []
        self.season_rail = None
        self.ep_season = None
        self.sel = None
        want = (season if season is not None else item.get("season"), episode if episode is not None else item.get("episode"))
        self.want = want if want[0] is not None and want[1] is not None else None
        self.entries = history_entries()
        self.backdrop_tok = 0

        ov = Gtk.Overlay()
        self.backdrop = Gtk.Image()
        self.backdrop.set_size_request(shell.sw, shell.sh)
        ov.add(self.backdrop)
        for cls, valign, size in (("hero-fade", Gtk.Align.FILL, -1), ("hero-bottom-fade", Gtk.Align.END, shell.sh // 2)):
            f = Gtk.Box()
            f.get_style_context().add_class(cls)
            f.set_valign(valign)
            f.set_size_request(-1, size)
            ov.add_overlay(f)
        col = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=px(10))
        col.set_valign(Gtk.Align.END)
        col.set_margin_start(px(76))
        col.set_margin_end(px(60))
        col.set_margin_bottom(px(30))
        self.logo = ArtImage(px(520), px(150), fit=True)
        self.logo.set_halign(Gtk.Align.START)
        self.title = text_label(self.item.get("name", ""), "det-title", wrap=True, lines=2)
        self.title.set_max_width_chars(34)
        self.info = text_label("", "det-info")
        self.desc = text_label(self.item.get("description", ""), "det-desc", wrap=True, lines=4)
        self.desc.set_max_width_chars(78)
        self.logo.set_no_show_all(True)
        col.pack_start(self.logo, False, False, 0)
        col.pack_start(self.title, False, False, 0)
        col.pack_start(self.info, False, False, 0)
        col.pack_start(self.desc, False, False, px(4))
        actions = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=px(14))
        actions.set_margin_top(px(10))
        actions.set_margin_bottom(px(6))
        self.play_btn = Gtk.Button(label="▶  Play")
        self.play_btn.get_style_context().add_class("hero-primary")
        self.play_btn.connect("clicked", lambda *_: self.play())
        self.src_btn = Gtk.Button(label="☰  Sources")
        self.src_btn.get_style_context().add_class("hero-secondary")
        self.src_btn.connect("clicked", lambda *_: self.sources())
        actions.pack_start(self.play_btn, False, False, 0)
        actions.pack_start(self.src_btn, False, False, 0)
        col.pack_start(actions, False, False, 0)
        self.actions = [self.play_btn, self.src_btn]
        self.season_rail = Rail(shell, None, kind="pill", on_activate=self.pick_season, quiet=True)
        self.ep_rail = Rail(shell, None, kind="episode", on_activate=self.play_episode,
                            on_focus=self.focus_episode, quiet=True, limit=500)
        col.pack_start(self.season_rail, False, False, 0)
        col.pack_start(self.ep_rail, False, False, 0)
        ov.add_overlay(col)
        self.pack_start(ov, True, True, 0)
        self.render_basic()
        self.update_buttons()
        self.fetch()

    # -- data
    def render_basic(self):
        it = self.item
        self.title.set_text(it.get("name") or "")
        self.desc.set_text(it.get("description") or "")
        parts = [it.get("year"), it.get("runtime"), ("★ " + str(it["rating"])) if it.get("rating") else "",
                 " · ".join((it.get("genres") or [])[:3])]
        self.info.set_text("  ·  ".join(p for p in parts if p))
        self.load_backdrop(backdrop_url(it))

    def load_backdrop(self, url):
        self.backdrop_tok += 1
        tok = self.backdrop_tok
        if not url:
            return

        def done(pb):
            if tok == self.backdrop_tok and pb is not None:
                self.backdrop.set_from_pixbuf(pb)
            return False
        load_art(url, self.shell.sw, self.shell.sh, done)

    def fetch(self):
        if not addons:
            return
        kind, mid = self.kind, self.item.get("id")

        def work():
            try:
                res, err = addons.meta(kind, mid), None
            except Exception as exc:
                res, err = None, exc
            GLib.idle_add(self.apply_meta, res, err)
        _data_pool.submit(work)

    def apply_meta(self, m, err):
        if err is not None or not m:
            if not self.item.get("description"):
                self.desc.set_text("Couldn't load details. Check the internet connection and go back to try again.")
            return False
        self.meta_ok = True
        full = item_from_meta(m, self.kind)
        if full:
            keep = {k: self.item[k] for k in ("season", "episode", "progress", "subtitle") if k in self.item}
            self.item.update({k: v for k, v in full.items() if v})
            self.item.update(keep)
        self.render_basic()
        if m.get("logo"):
            self.logo.set_url(m["logo"])
            self.logo.show()
            self.title.hide()
            self.title.set_no_show_all(True)
        self.videos = m.get("videos") or []
        if self.kind == "series":
            self.build_series()
        self.update_buttons()
        self.shell.ensure_focus()
        return False

    # -- series
    def seasons(self):
        return sorted({v["season"] for v in episodes_of(self.videos)})

    def build_series(self):
        eps = episodes_of(self.videos)
        if not eps:
            return
        if not self.sel:
            pick = self.want or pick_default_episode(self.videos, self.entries, self.item["id"])
            self.sel = pick
        seasons = self.seasons()
        self.season_rail.set_items([{"name": "Specials" if s == 0 else f"Season {s}", "season": s} for s in seasons])
        self.fill_episodes(self.sel[0])
        if len(seasons) <= 1:
            self.season_rail.hide()
        if self.sel:
            self.shell.after(60, self.scroll_to_selected)

    def fill_episodes(self, season):
        items = []
        for v in episodes_of(self.videos):
            if v["season"] != season:
                continue
            vid = video_id_for("series", self.item["id"], v["season"], v["episode"])
            e = self.entries.get(vid) or {}
            name = v.get("name") or v.get("title") or f"Episode {v['episode']}"
            done = bool(e.get("finished"))
            prog = (e.get("position") / e["duration"]) if (e.get("duration") and e.get("position") and not done) else None
            items.append({"type": "series", "id": self.item["id"], "season": v["season"], "episode": v["episode"],
                          "name": ("✓ " if done else "") + f"E{v['episode']} · {name}", "thumbnail": v.get("thumbnail"),
                          "progress": prog, "subtitle": ""})
        self.ep_rail.set_items(items)
        self.ep_season = season

    def scroll_to_selected(self):
        for c in self.ep_rail.cards:
            it = c.item
            if self.sel and (it["season"], it["episode"]) == tuple(self.sel):
                self.ep_rail.reveal_h(c)
        return False

    def pick_season(self, item):
        s = item["season"]
        self.fill_episodes(s)
        first = self.ep_rail.items[0] if self.ep_rail.items else None
        if first:
            self.sel = (first["season"], first["episode"])
            self.update_buttons()

    def focus_episode(self, item, btn):
        if item and item.get("season") is not None:
            self.sel = (item["season"], item["episode"])
            self.update_buttons()
        return False

    def select_episode(self, season, episode):
        if season is None or episode is None:
            return
        self.sel = (season, episode)
        if self.videos and getattr(self, "ep_season", None) != season:
            self.fill_episodes(season)
        self.update_buttons()
        self.shell.after(60, self.scroll_to_selected)

    # -- playback
    def target(self):
        t = {"type": self.kind, "id": self.item["id"]}
        if self.kind == "series":
            sel = self.sel or self.want
            if sel:
                t["season"], t["episode"] = sel
        return t

    def resume(self):
        t = self.target()
        return resume_seconds(self.entries, video_id_for(self.kind, t["id"], t.get("season"), t.get("episode")))

    def update_buttons(self):
        t = self.target()
        ep = f" S{t['season']} E{t['episode']}" if "season" in t else ""
        res = self.resume()
        if res:
            self.play_btn.set_label(f"▶  Resume{ep}  {fmt_time(res)}")
        else:
            self.play_btn.set_label(f"▶  Play{ep}")

    def refresh_history(self):
        self.entries = history_entries()
        if self.kind == "series" and self.videos and self.ep_season is not None:
            self.fill_episodes(self.ep_season)
        self.update_buttons()

    def play(self, params=None):
        params = dict(params or self.target())
        res = self.resume() if params == self.target() else resume_seconds(
            self.entries, video_id_for(self.kind, params["id"], params.get("season"), params.get("episode")))
        if res:
            params["start"] = res
        if self.kind == "series" and "season" not in params:
            self.shell.toast("Episodes are still loading")
            return
        self.shell.play(params, self.item.get("name"))

    def play_episode(self, item):
        self.sel = (item["season"], item["episode"])
        self.update_buttons()
        self.play()

    def sources(self):
        t = self.target()
        if self.kind == "series" and "season" not in t:
            self.shell.toast("Episodes are still loading")
            return
        t["name"] = self.item.get("name")
        self.shell.push(SourcesScreen(self.shell, t, self.resume()))

    # -- focus
    def focus_groups(self):
        return [self.actions, self.season_rail.cards if len(self.season_rail.cards) > 1 else [], self.ep_rail.cards]

    def first_focus(self, flat):
        return self.play_btn

    def on_enter(self, returning):
        if returning:
            self.refresh_history()


class SourcesScreen(Screen):
    ephemeral = True

    def __init__(self, shell, media, start=0):
        super().__init__(shell)
        px = shell.px
        self.media, self.start, self.rows, self.token = media, start, [], 0
        self.set_margin_start(px(120))
        self.set_margin_end(px(120))
        self.set_margin_top(px(70))
        self.set_margin_bottom(px(50))
        ep = f"  ·  S{media['season']} E{media['episode']}" if "season" in media else ""
        self.pack_start(text_label("Choose a source", "rail-title"), False, False, 0)
        self.pack_start(text_label((media.get("name") or "") + ep, "section-note"), False, False, 0)
        self.stack_msg = text_label("", "empty-copy", wrap=True)
        self.stack_msg.set_margin_top(px(30))
        self.pack_start(self.stack_msg, False, False, 0)
        self.retry = Gtk.Button(label="Try again")
        self.retry.get_style_context().add_class("hero-secondary")
        self.retry.set_halign(Gtk.Align.START)
        self.retry.set_margin_top(px(20))
        self.retry.set_no_show_all(True)
        self.retry.connect("clicked", lambda *_: self.load())
        self.pack_start(self.retry, False, False, 0)
        self.spinner = Gtk.Spinner()
        self.spinner.set_size_request(px(56), px(56))
        self.spinner.set_halign(Gtk.Align.START)
        self.spinner.set_margin_top(px(20))
        self.pack_start(self.spinner, False, False, 0)
        self.scroll = Gtk.ScrolledWindow()
        self.scroll.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.EXTERNAL)
        self.scroll.set_shadow_type(Gtk.ShadowType.NONE)
        self.scroll.set_margin_top(px(24))
        self.list = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=px(4))
        self.scroll.add(self.list)
        self.pack_start(self.scroll, True, True, 0)
        self.load()

    def load(self):
        self.token += 1
        tok = self.token
        self.retry.hide()
        self.stack_msg.set_text("Finding the best sources…")
        self.spinner.show()
        self.spinner.start()
        params = {k: v for k, v in self.media.items() if k in ("type", "id", "season", "episode")}

        def work():
            res = cmd("sources", params, timeout=25)
            GLib.idle_add(self.done, tok, res)
        _data_pool.submit(work)

    def done(self, tok, res):
        if tok != self.token:
            return False
        self.spinner.stop()
        self.spinner.hide()
        sources = (res or {}).get("sources") if isinstance(res, dict) else None
        for r in self.list.get_children():
            self.list.remove(r)
            r.destroy()
        self.rows = []
        if res is None or sources is None:
            msg = (res or {}).get("error") if isinstance(res, dict) else None
            self.stack_msg.set_text(msg or "Couldn't reach the TV controller. Try again in a moment.")
            self.retry.show()
        elif not sources:
            self.stack_msg.set_text("No sources found for this title yet.")
            self.retry.show()
        else:
            self.stack_msg.set_text("")
            self.stack_msg.hide()
            px = self.shell.px
            for i, src in enumerate(sources):
                self.add_row(src, i == 0, px)
            self.list.show_all()
        self.shell.ensure_focus()
        if self.rows:
            self.rows[0].grab_focus()
        elif self.retry.get_visible():
            self.retry.grab_focus()
        return False

    def add_row(self, src, best, px):
        btn = Gtk.Button()
        btn.set_relief(Gtk.ReliefStyle.NONE)
        btn.get_style_context().add_class("src-row")
        box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=px(20))
        badge = text_label("BEST" if best else "", "badge-best" if best else "badge-none")
        badge.set_size_request(px(86), -1)
        badge.set_valign(Gtk.Align.CENTER)
        box.pack_start(badge, False, False, 0)
        col = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=px(2))
        col.pack_start(text_label(src.get("label") or src.get("title") or "Source", "src-label", ellipsize=True), False, False, 0)
        langs = src.get("languages") or []
        if langs:
            col.pack_start(text_label(" · ".join(str(l) for l in langs), "card-sub", ellipsize=True), False, False, 0)
        box.pack_start(col, True, True, 0)
        btn.add(box)
        btn.connect("clicked", lambda *_: self.choose(src))
        self.list.pack_start(btn, False, False, 0)
        self.rows.append(btn)

    def choose(self, src):
        params = {k: v for k, v in self.media.items() if k in ("type", "id", "season", "episode")}
        params["stream"] = stream_ref(src)
        if self.start:
            params["start"] = self.start
        self.shell.pop()
        self.shell.play(params, self.media.get("name"))

    def focus_groups(self):
        return [[r] for r in self.rows] or ([[self.retry]] if self.retry.get_visible() else [])

    def reveal(self, widget):
        pos = widget.translate_coordinates(self.list, 0, 0)
        if pos:
            adj = self.scroll.get_vadjustment()
            y, h = pos[1], widget.get_allocated_height()
            cur, page = adj.get_value(), adj.get_page_size()
            if y < cur + 10:
                animate(adj, max(0, y - 10), 160)
            elif y + h > cur + page - 10:
                animate(adj, y + h - page + 10, 160)


class BufferingOverlay(Gtk.Box):
    def __init__(self, shell):
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=shell.px(14))
        px = shell.px
        self.get_style_context().add_class("buffer-overlay")
        self.set_halign(Gtk.Align.FILL)
        self.set_valign(Gtk.Align.FILL)
        self.set_no_show_all(True)
        self.spinner = Gtk.Spinner()
        self.spinner.set_size_request(px(96), px(96))
        self.spinner.set_halign(Gtk.Align.CENTER)
        self.title = text_label("", "buffer-title", wrap=True)
        self.title.set_halign(Gtk.Align.CENTER)
        self.title.set_justify(Gtk.Justification.CENTER)
        self.source = text_label("", "buffer-line")
        self.source.set_halign(Gtk.Align.CENTER)
        self.status = text_label("Connecting…", "buffer-status")
        self.status.set_halign(Gtk.Align.CENTER)
        self.hint = text_label("Press Back to cancel", "buffer-line")
        self.hint.set_halign(Gtk.Align.CENTER)
        top, bottom = Gtk.Box(), Gtk.Box()
        self.pack_start(top, True, True, 0)
        for w in (self.spinner, self.title, self.source, self.status, self.hint):
            self.pack_start(w, False, False, 0)
        self.pack_start(bottom, True, True, 0)

    def show_for(self, state):
        buf = state.get("buffering") or {}
        media = state.get("media") or {}
        name = media.get("name") or ""
        if media.get("season") is not None:
            name += f"  ·  S{media['season']} E{media.get('episode')}"
        self.title.set_text(name or "Starting playback")
        self.source.set_text(buf.get("source") or "")
        speed = (buf.get("speed") or 0) / 1_000_000
        self.status.set_text(f"Connecting  ·  {int(buf.get('peers') or 0)} peers  ·  {speed:.1f} MB/s")
        if not self.get_visible():
            self.show()
            for c in self.get_children():
                c.show()
            self.spinner.start()

    def hide_now(self):
        if self.get_visible():
            self.spinner.stop()
            self.hide()


class Toast(Gtk.Label):
    def __init__(self, shell):
        super().__init__()
        self.get_style_context().add_class("toast")
        self.set_halign(Gtk.Align.CENTER)
        self.set_valign(Gtk.Align.END)
        self.set_margin_bottom(shell.px(70))
        self.set_no_show_all(True)
        self.timer = 0

    def say(self, text, secs=4):
        self.set_text(text)
        self.show()
        if self.timer:
            GLib.source_remove(self.timer)
        self.timer = GLib.timeout_add(int(secs * 1000), self._hide)

    def _hide(self):
        self.timer = 0
        self.hide()
        return False


def build_css(scale=1.0):
    p = lambda n: round(n * scale)
    return f"""
window {{ font-family: "Inter", "Noto Sans", "Noto Sans Arabic", sans-serif; }}
.header-bar {{ background-image: linear-gradient(180deg, rgba(0,0,0,0.80), rgba(0,0,0,0)); padding: {p(30)}px {p(76)}px {p(40)}px; }}
.rail-title {{ font-size: {p(30)}px; font-weight: 800; color: #f1f3f6; }}
.card {{ background: #15171c; border: {p(4)}px solid transparent; border-radius: {p(16)}px; padding: 0; color: #fff; transition: all 140ms ease-out; }}
.card:hover {{ background: #1c1f26; }}
.card:focus {{ background: #20242c; border-color: #ffffff; box-shadow: 0 0 0 {p(5)}px rgba(255,255,255,0.30), 0 {p(14)}px {p(34)}px rgba(0,0,0,0.85); }}
.card-title {{ font-size: {p(21)}px; font-weight: 700; color: #cfd3da; padding-top: {p(2)}px; }}
.card:focus .card-title {{ color: #ffffff; }}
.card-sub {{ font-size: {p(17)}px; color: #8e95a3; }}
.card-art {{ border-radius: {p(12)}px; }}
.pill {{ border-radius: {p(30)}px; padding: {p(8)}px {p(24)}px; font-size: {p(21)}px; font-weight: 700; background: rgba(40,44,54,0.9); }}
.pill label {{ font-size: {p(21)}px; font-weight: 700; color: #e6e9ee; }}
.app-card {{ background: #1b1e26; }}
.err-card {{ background: #2a1519; }}
.skel {{ background: #181a20; border-radius: {p(16)}px; }}
.skel-b {{ background: #20232b; }}
.hero-title {{ font-size: {p(58)}px; }}
.hero-info {{ font-size: {p(22)}px; font-weight: 600; color: #d4d8df; }}
.hero-copy {{ font-size: {p(22)}px; color: #cdd1d8; }}
.det-title {{ font-size: {p(72)}px; font-weight: 900; color: #fff; }}
.det-info {{ font-size: {p(23)}px; font-weight: 600; color: #d6dae1; }}
.det-desc {{ font-size: {p(23)}px; color: #dcdfe5; }}
.hero-primary, .hero-secondary {{ transition: all 140ms ease-out; }}
.hero-primary:focus {{ background: #ffffff; box-shadow: 0 0 0 {p(6)}px rgba(255,255,255,0.55); }}
.hero-primary {{ background: rgba(255,255,255,0.80); }}
.hero-secondary:focus {{ background: #ffffff; color: #111; border-color: #fff; box-shadow: 0 0 0 {p(6)}px rgba(255,255,255,0.55); }}
.search-entry {{ background: rgba(255,255,255,0.08); color: #fff; border: {p(3)}px solid rgba(255,255,255,0.22); border-radius: {p(18)}px; padding: 0 {p(28)}px; font-size: {p(34)}px; font-weight: 600; caret-color: #fff; }}
.search-entry:focus {{ border-color: #fff; box-shadow: 0 0 0 {p(5)}px rgba(255,255,255,0.30); background: rgba(255,255,255,0.14); }}
.src-row {{ background: rgba(255,255,255,0.05); border: {p(3)}px solid transparent; border-radius: {p(16)}px; padding: {p(16)}px {p(24)}px; transition: all 140ms ease-out; }}
.src-row:focus {{ background: rgba(255,255,255,0.16); border-color: #fff; box-shadow: 0 0 0 {p(4)}px rgba(255,255,255,0.3); }}
.src-label {{ font-size: {p(26)}px; font-weight: 700; color: #fff; }}
.badge-best {{ font-size: {p(16)}px; font-weight: 900; letter-spacing: 2px; color: #111; background: #facc15; border-radius: {p(8)}px; padding: {p(4)}px {p(10)}px; }}
.badge-none {{ font-size: {p(16)}px; }}
.buffer-overlay {{ background: rgba(0,0,0,0.93); }}
.buffer-title {{ font-size: {p(52)}px; font-weight: 800; color: #fff; }}
.buffer-line {{ font-size: {p(22)}px; color: #9aa1ad; }}
.buffer-status {{ font-size: {p(28)}px; font-weight: 600; color: #e6e9ee; }}
.toast {{ background: rgba(30,34,44,0.97); color: #fff; font-size: {p(24)}px; font-weight: 600; border: {p(2)}px solid rgba(255,255,255,0.3); border-radius: {p(16)}px; padding: {p(16)}px {p(34)}px; }}
"""
