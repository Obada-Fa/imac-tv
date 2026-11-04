"""Aligned Continue watching cards and Home shortcuts in the selected appearance."""
import sys
from pathlib import Path

import gi
gi.require_version("Gtk", "3.0")
from gi.repository import Gtk, Pango

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import screens  # noqa: E402
from . import Widget, register  # noqa: E402
from .bubbles import BubbleRow, RowBase  # noqa: E402
from . import network, chrome  # noqa: E402

MAX_CONTINUE = 5


class RowWidget(Widget):
    slot = "row"
    # vertical placement as fractions of screen height: top, height
    top, height = 0.5, 0.2
    seed = 1
    base = 1.0
    max_r = 80          # bubble radius cap, px at 1080p
    width_frac = 0.8
    spread = 0.16
    round_nodes = False

    def build(self):
        self.host = Gtk.Box()  # swappable container so the row can be rebuilt
        self.host.set_no_show_all(True)
        self.row = None
        self.rebuild()
        return self.host

    def items(self):
        """Generic item dicts: title, sub, poster_url, art_url, progress, kind, now, on_activate."""
        return []

    @property
    def style(self):
        return self.config.get("style", "network")

    def make_row(self, items, h):
        sh = self.shell
        if self.style == "bubbles":
            specs = []
            for it in items:
                spec = {"title": it["title"], "sub": it.get("sub") or "", "progress": it.get("progress"),
                        "on_activate": it["on_activate"]}
                if it.get("kind"):
                    spec["kind"] = it["kind"]
                else:
                    spec["poster_url"] = it.get("poster_url") or it.get("art_url") or ""
                if it.get("now"):
                    spec["eq"] = it["now"]
                    spec["big"] = 1.22
                specs.append(spec)
            return BubbleRow(sh, specs, h, seed=self.seed, base=self.base, spread=self.spread,
                             max_r=sh.px(self.max_r), width_frac=self.width_frac)
        return network.NodeRow(sh, items, h, seed=self.seed, round_nodes=self.round_nodes,
                               shape=self.config.get("node_shape", "hexagon"),
                               chamfer=self.config.get("tile_shape") == "chamfer")

    def rebuild(self, focus_first=False):
        had = None
        focused = False
        owner = self.host.get_ancestor(screens.Screen)
        if self.row:
            for i, b in enumerate(self.row.bubbles):
                if b.has_focus() or (owner is not None and owner.last_focus is b):
                    had = i
                    focused = b.has_focus()
        for c in self.host.get_children():
            self.host.remove(c)
            c.destroy()
        items = self.items()
        self.row = None
        h = round(self.shell.sh * self.height)
        if items:
            self.row = self.make_row(items, h)
            self.row.show_all()
            self.host.pack_start(self.row, False, False, 0)
        self.host.set_visible(bool(items))
        self.host.set_size_request(-1, h if items else 0)
        if self.row and (focus_first or had is not None):
            idx = 0 if focus_first else self.focus_target(had, items)
            target = self.row.bubbles[idx]
            if owner is not None:
                owner.last_focus = target
            if focus_first or focused:
                GLib_idle(target.grab_focus)

    def focus_target(self, had, items):
        """Index to focus after a rebuild that had focus on tile `had`."""
        return min(had, len(self.row.bubbles) - 1)

    def focus_items(self):
        return self.row.focus_items() if self.row else []

    def animate(self, dt, now):
        if self.row:
            self.row.avoid = getattr(self.shell, "avoid_rect", None)
            self.row.animate(dt, now)


def GLib_idle(fn):
    from gi.repository import GLib
    GLib.idle_add(lambda: fn() and False)


@register
class ContinueWidget(RowWidget):
    id = "continue"
    top, height = 0.26, 0.42
    seed = 3
    max_r, width_frac = 92, 0.78
    interval = 300

    def __init__(self, shell, config=None):
        super().__init__(shell, config)
        self.now_state = None
        self.keys, self.prev_keys = [], []

    def items(self):
        out = []
        st = self.now_state
        if st:
            out.append({"key": "youtube", "title": st.get("title") or "YouTube", "sub": st.get("artist") or "",
                        "art_url": st.get("art"), "poster_url": st.get("art") or "", "now": st.get("status") or "Open",
                        "on_activate": lambda: self.shell.open_app("youtube")})
        room = MAX_CONTINUE - bool(st)
        for item in screens.continue_items()[:room]:
            out.append({"key": (item.get("type"), item.get("id"), item.get("season"), item.get("episode")),
                        "title": item.get("name") or "", "sub": item.get("subtitle") or "",
                        "poster_url": screens.poster_url(item) or "",
                        "art_url": screens.backdrop_url(item) or screens.poster_url(item),
                        "progress": item.get("progress"),
                        "on_activate": (lambda it=item: self.shell.open_details(it))})
        self.prev_keys, self.keys = self.keys, [item["key"] for item in out]
        return out

    def focus_target(self, had, items):
        old = self.prev_keys[had] if had < len(self.prev_keys) else None
        return self.keys.index(old) if old in self.keys else min(had, len(items) - 1)

    def make_row(self, items, h):
        return ContinueCards(self.shell, items, h)

    def refresh(self):
        self.rebuild()

    def on_enter(self, returning):
        if returning:
            self.rebuild()

    def set_now(self, state, auto_focus=False):
        """New YouTube state from the poller. Rebuild only when the node appears/disappears; otherwise update in place."""
        appeared = bool(state) != bool(self.now_state)
        self.now_state = state
        if appeared:
            self.rebuild(focus_first=bool(state) and auto_focus)
        elif state and self.row:
            self.row.update_now(state)


class ContinueCards(Gtk.Box):
    """A stable landscape rail; the focused title remains readable in full above the cards."""
    def __init__(self, shell, items, height):
        super().__init__(orientation=Gtk.Orientation.VERTICAL)
        self.bubbles = []
        heading = screens.text_label("Continue watching", "section-title")
        heading.set_halign(Gtk.Align.START)
        heading.set_margin_start(shell.px(110))
        self.pack_start(heading, False, False, 0)
        self.label = screens.text_label("", "ah-continue-detail")
        self.label.set_halign(Gtk.Align.START)
        self.label.set_line_wrap(True)
        self.label.set_line_wrap_mode(Pango.WrapMode.WORD_CHAR)
        self.label.set_max_width_chars(85)
        self.label.set_width_chars(65)
        self.label.set_margin_start(shell.px(110))
        self.pack_start(self.label, False, False, 0)
        row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL)
        row.set_margin_start(shell.px(96))
        self.pack_start(row, False, False, 0)
        for item in items:
            info = {"name": item["title"], "subtitle": item.get("sub", ""), "progress": item.get("progress")}
            card = chrome.PosterCard(shell, chrome.look(shell), info, "wide", shell.px(270), shell.px(152), item["on_activate"])
            card.now = item.get("now")
            card.connect("focus-in-event", lambda c, _e: self.show_title(c) or False)
            url = item.get("art_url") or item.get("poster_url")
            if url:
                screens.load_art(url, shell.px(270), shell.px(152), lambda pb, c=card: c.set_pixbuf(pb) if pb is not None and not c.dead else None)
            row.pack_start(card, False, False, 0)
            self.bubbles.append(card)

    def show_title(self, card):
        detail = " · ".join(x for x in (card.title, card.sub) if x)
        self.label.set_text(detail)

    def focus_items(self):
        return list(self.bubbles)

    def animate(self, dt, now):
        pass

    def update_now(self, state):
        if self.bubbles and self.bubbles[0].now:
            card = self.bubbles[0]
            card.title = state.get("title") or "YouTube"
            card.sub = state.get("artist") or ""
            card.now = state.get("status") or "Open"
            if card.has_focus():
                self.show_title(card)
            card.queue_draw()


# ---------------------------------------------------------------- the Apps row

FIXED_TILES = (("All apps", "apps", "apps"),)
# Built-in tiles shown while there is no apps module at all, so Home is never an empty row
FALLBACK = (("Movies", "movies", "screen"), ("Series", "series", "screen"), ("YouTube", "youtube", "app"),
            ("Twitch", "twitch", "screen"))


def fallback_apps():
    return [{"id": i, "name": n, "kind": k, "favorite": True, "icon": None, "builtin_kind": i} for n, i, k in FALLBACK]


def apps_row_specs(favorites, others=True):
    """The Apps row in order: saved favorites followed by All apps.
    -> [{"key", "title", "kind", "app"}]; "app" is the apps.py item for a favorite (None for a fixed tile)."""
    out = [{"key": it["id"], "title": it.get("name") or it["id"], "kind": it.get("builtin_kind"), "app": it}
           for it in favorites]
    if others:
        out += [{"key": "fixed:" + kind, "title": title, "kind": kind, "app": None} for title, kind, _t in FIXED_TILES]
    return out


class AppNode(network.RoundNode):
    """A network-style action node that can carry a desktop app's icon (or its first letter) instead of a glyph."""

    def __init__(self, shell, r, title="", kind=None, on_activate=None, seed=0, shape="hexagon", app=None):
        super().__init__(shell, r, title, kind, on_activate, seed, shape)
        self.app, self.icon, self.tint = app, None, None
        self._icon_dead = False
        self.connect("destroy", lambda *_: setattr(self, "_icon_dead", True))
        if app and not kind and app.get("icon"):
            screens.load_app_icon(app, round(r * 1.2), self.set_icon)

    def set_icon(self, pb):
        if not self._icon_dead and pb is not None and network.cairo:
            side = self.r * 1.05
            k = side / max(pb.get_width(), pb.get_height())
            w, h = max(1, round(pb.get_width() * k)), max(1, round(pb.get_height() * k))
            surf = network.cairo.ImageSurface(network.cairo.FORMAT_ARGB32, w, h)
            cr = network.cairo.Context(surf)
            cr.scale(w / pb.get_width(), h / pb.get_height())
            network.Gdk.cairo_set_source_pixbuf(cr, pb, 0, 0)
            cr.paint()
            surf.flush()
            self.icon, self.tint = surf, screens.app_tint(pb, (0.8, 0.85, 1.0))
            self.queue_draw()
        return False

    def draw_body(self, cr, base, f, sc):
        if not self.app or self.kind:
            return super().draw_body(cr, base, f, sc)
        r = self.r
        cairo = network.cairo
        cr.set_line_join(cairo.LINE_JOIN_ROUND)
        self.glow(cr, f, lambda c, g: network.poly_path(c, network.poly_points(self.shape, r + g)))
        network.poly_path(cr, network.poly_points(self.shape, r))
        cr.save()
        cr.clip_preserve()
        network.paint_frost(cr, base, self, self.photo)
        tr, tg, tb = self.tint or (0.8, 0.85, 1.0)
        cr.set_source_rgba(1, 1, 1, 0.10 + 0.05 * f)
        cr.paint()
        g = cairo.RadialGradient(-r * 0.3, -r * 0.4, 0, -r * 0.3, -r * 0.4, r * 1.3)
        g.add_color_stop_rgba(0, tr, tg, tb, 0.30)
        g.add_color_stop_rgba(1, tr, tg, tb, 0.05)
        cr.set_source(g)
        cr.paint()
        cr.restore()
        network.poly_path(cr, network.poly_points(self.shape, r - 0.6))
        cr.set_source_rgba(1, 1, 1, 0.24 + 0.7 * f)
        cr.set_line_width(1)
        cr.stroke()
        if self.icon is not None:
            cr.set_source_surface(self.icon, -self.icon.get_width() / 2, -self.icon.get_height() / 2)
            cr.paint()
        else:
            self.text(cr, (self.app.get("name") or "?")[:1].upper(), round(r * 0.9), Pango.Weight.LIGHT,
                      -r * 0.6, -r * 0.62, r * 1.2, (1, 1, 1, 0.85))
        self.text(cr, self.title, 17, Pango.Weight.SEMIBOLD, -r * 1.4, r + 8, r * 2.8, (1, 1, 1, 0.78 + 0.22 * f))


class AppNodeRow(RowBase):
    """The Apps row in the network style: small glass nodes in an irregular strip. It widens with the tile count so
    nine tiles keep the spacing seven had."""

    def __init__(self, shell, specs, height, seed=1, shape="hexagon"):
        super().__init__(shell, height)
        n = len(specs)
        width_frac = min(0.8, 0.55 + 0.08 * max(0, n - 7))
        d = 2 * round(56 * shell.scale)
        lay = network.row_layout([(d, d)] * n, shell.sw, height - shell.px(34), seed=seed, width_frac=width_frac,
                                 spread=0.1, gap_scale=2.6)
        for i, (spec, p) in enumerate(zip(specs, lay)):
            node = AppNode(shell, d // 2, title=spec["title"], kind=spec.get("kind"), on_activate=spec["on_activate"],
                           seed=seed * 17 + i, shape=shape, app=spec.get("app"))
            self.add_node(node, round(p["cx"] - node.cx), round(p["cy"] - node.cy))


@register
class AppsWidget(RowWidget):
    """Home's apps row: the favorites (apps.home_apps(), oldest first) in an irregular strip, then All apps.
    It rebuilds when apps_rev moves, keeping focus on the same tile."""
    id = "apps"
    top, height = 0.70, 0.235
    seed = 11
    max_r, width_frac, spread = 66, 0.5, 0.2
    round_nodes = True

    def __init__(self, shell, config=None):
        super().__init__(shell, config)
        self.rev = None
        self.keys, self.prev_keys = [], []
        self.apps = []

    def favorites(self):
        favs = screens.home_app_list()
        return fallback_apps() if favs is None else favs

    def items(self):
        sh = self.shell
        self.apps = self.favorites()
        specs = apps_row_specs(self.apps)
        fixed = {"apps": lambda: sh.push(sh.top_screen("apps")), "search": lambda: sh.goto_top("search"),
                 "settings": lambda: sh.goto_top("settings")}
        out = []
        for sp in specs:
            sp["on_activate"] = fixed[sp["kind"]] if sp["app"] is None else (lambda it=sp["app"]: sh.open_app_item(it))
            out.append(sp)
        self.prev_keys, self.keys = self.keys, [sp["key"] for sp in out]
        self.width_frac = 0.5 + 0.11 * max(0, len(out) - 7)       # bubbles: keep their size as the row grows
        return out

    def make_row(self, items, h):
        sh = self.shell
        if self.style == "bubbles":
            specs = [{"title": it["title"], "on_activate": it["on_activate"],
                      **({"kind": it["kind"]} if it.get("kind") else {"app": it["app"]})} for it in items]
            row = BubbleRow(sh, specs, h, seed=self.seed, base=self.base, spread=self.spread,
                            max_r=sh.px(self.max_r), width_frac=self.width_frac)
        else:
            row = AppNodeRow(sh, items, h, seed=self.seed, shape=self.config.get("node_shape", "hexagon"))
        for node, it in zip(row.bubbles, items):
            node.menu_item = it["app"]                    # the Menu key's target (fixed tiles have none)
        return row

    def focus_target(self, had, items):
        """After a rebuild: stay on the same tile; if it is gone, on the one now at its place."""
        old = self.prev_keys[had] if had < len(self.prev_keys) else None
        return self.keys.index(old) if old in self.keys else min(had, len(items) - 1)

    def set_rev(self, rev):
        if rev is None or rev == self.rev:
            return
        first = self.rev is None
        self.rev = rev
        if not first:
            self.rebuild()

    def on_enter(self, returning):
        """Back on Home: pick up apps installed or removed meanwhile (cheap: only when the tiles differ)."""
        if returning and [sp["key"] for sp in apps_row_specs(self.favorites())] != self.keys:
            self.rebuild()
