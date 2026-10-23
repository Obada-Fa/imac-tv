"""Focusable rows: continue-watching (plus the YouTube "now playing" node first) and the action nodes
Movies / Series / YouTube / Search. Each renders as bubbles or as network nodes depending on home.json "style"."""
import sys
from pathlib import Path

import gi
gi.require_version("Gtk", "3.0")
from gi.repository import Gtk

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import screens  # noqa: E402
from . import Widget, register  # noqa: E402
from .bubbles import BubbleRow  # noqa: E402
from . import network  # noqa: E402

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
        if self.row:
            for i, b in enumerate(self.row.bubbles):
                if b.has_focus():
                    had = i
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
            idx = 0 if focus_first else min(had, len(self.row.bubbles) - 1)
            target = self.row.bubbles[idx]
            GLib_idle(target.grab_focus)

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

    def items(self):
        out = []
        st = self.now_state
        if st:
            out.append({"title": st.get("title") or "YouTube", "sub": st.get("artist") or "",
                        "art_url": st.get("art"), "poster_url": st.get("art") or "", "now": st.get("status") or "Open",
                        "on_activate": lambda: self.shell.open_app("youtube")})
        room = MAX_CONTINUE - (1 if st and self.style == "network" else 0)
        for item in screens.continue_items()[:room]:
            out.append({"title": item.get("name") or "", "sub": item.get("subtitle") or "",
                        "poster_url": screens.poster_url(item) or "",
                        "art_url": screens.backdrop_url(item) or screens.poster_url(item),
                        "progress": item.get("progress"),
                        "on_activate": (lambda it=item: self.shell.open_details(it))})
        return out

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


@register
class AppsWidget(RowWidget):
    id = "apps"
    top, height = 0.70, 0.235
    seed = 11
    max_r, width_frac, spread = 66, 0.5, 0.2
    round_nodes = True

    def items(self):
        sh = self.shell
        return [
            {"title": "Movies", "kind": "movies", "on_activate": lambda: sh.goto_top("movies")},
            {"title": "Series", "kind": "series", "on_activate": lambda: sh.goto_top("series")},
            {"title": "YouTube", "kind": "youtube", "on_activate": lambda: sh.open_app("youtube")},
            {"title": "Search", "kind": "search", "on_activate": lambda: sh.goto_top("search")},
        ]
