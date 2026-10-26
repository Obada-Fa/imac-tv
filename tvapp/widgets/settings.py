"""Category settings for the TV and the shared timed display confirmation.

Display operations run on workers; the shell owns the confirmation countdown so
it survives resolution changes and trials initiated by a paired phone.
"""
import sys
import threading
from pathlib import Path

import gi
gi.require_version("Gtk", "3.0")
from gi.repository import Gtk, Gdk, GLib

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import backgrounds  # noqa: E402
import display  # noqa: E402
import settings_model
import widgets  # noqa: E402
from widgets import glass, pairing  # noqa: E402
from screens import Screen, text_label, cmd, cover_pixbuf, ASSETS  # noqa: E402
from media import config as media_config  # noqa: E402

PER_ROW = 5
MIN_WIDTH = 640


def settings_css(scale=1.0):
    p = lambda n: max(1, round(n * scale))
    return f"""
.set-page {{ background: #0b121c; }}
.set-title {{ font-size: {p(54)}px; font-weight: 200; color: #fff; text-shadow: 0 2px 3px rgba(0,0,0,0.45); }}
.set-output {{ font-size: {p(58)}px; font-weight: 200; color: #fff; }}
.set-note {{ font-size: {p(20)}px; font-weight: 400; color: rgba(255,255,255,0.6); }}
.set-sheet-title {{ font-size: {p(40)}px; font-weight: 300; color: #fff; }}
entry.set-entry, entry.set-entry:focus {{ background-color: transparent; background-image: none; border: none;
    box-shadow: none; outline: none;
    color: #fff; font-size: {p(24)}px; padding: {p(18)}px 0; caret-color: #fff; }}
.set-page scrolledwindow, .set-page viewport {{ background: transparent; border: none; }}
.set-count {{ font-size: {p(30)}px; font-weight: 300; color: #fff; }}
"""


def fmt_mode(mode):
    size = display.mode_size(mode)
    if not size:
        return mode
    return "%d × %d%s" % (size[0], size[1], "i" if mode.endswith("i") else "")


def fmt_rate(rate):
    if rate is None:
        return ""
    return ("%d Hz" % round(rate)) if abs(rate - round(rate)) < 0.005 else ("%.2f Hz" % rate)


def tv_modes(output):
    """Resolution buttons for one output: [(mode, rate, tags)], interlaced and tiny modes left out."""
    out = []
    pref = (output.get("preferred") or {}).get("mode")
    cur = (output.get("current") or {}).get("mode")
    for e in output["modes"]:
        size = display.mode_size(e["mode"])
        if not size or size[0] < MIN_WIDTH or e["mode"].endswith("i") or not e["rates"]:
            continue
        tags = []
        if e["mode"] == cur:
            tags.append("Current")
        if e["mode"] == pref:
            tags.append("Recommended")
        out.append((e["mode"], display.best_rate(output, e), tags))
    return out


def tv_rates(output):
    """Refresh-rate buttons for the current mode: [(rate, is_current)], highest first."""
    cur = output.get("current")
    if not cur:
        return []
    entry = next((e for e in output["modes"] if e["mode"] == cur["mode"]), None)
    rates = sorted(set(entry["rates"]) if entry else [], reverse=True)
    return [(r, abs(r - cur["rate"]) < 0.02) for r in rates]


def output_label(output):
    name = output["name"]
    kind = "Built-in screen" if name.startswith(display.INTERNAL) else "TV"
    return "%s · %s" % (kind, name)


def chunk(seq, n):
    return [seq[i:i + n] for i in range(0, len(seq), n)]


SUBTITLES = {
    "Appearance": "Make it yours. Shape the look and choose a lighter experience.",
    "Ambient": "Set the mood for the moments between watching.",
    "Display": "A comfortable picture, matched to your screen.",
    "Playback": "Your languages, quality and watching preferences.",
    "Connections": "Keep your phone and streaming services close.",
    "System": "Everything you need to keep your TV running smoothly.",
}
GLYPHS = {"Appearance": "appearance", "Ambient": "ambient", "Display": "display", "Playback": "playback",
          "Connections": "connections", "System": "system"}
RAIL_INDENT = (0, 30, 10, 38, 6, 26)      # a loose constellation, not a straight list
HEADINGS = {'transparency': 'Surfaces & motion', 'style': 'Home screen', 'interval': 'Timing',
            'visualizer': 'While idle', 'sub_default': 'Languages & quality', 'autoplay_next': 'Episodes'}
_ART = {}


def art_surface(path, width, height):
    """Cover-cropped picture as a cairo surface, cached per file + size (re-renders happen on every change)."""
    if not path or glass.cairo is None:
        return None
    try:
        key = (str(path), width, height, Path(path).stat().st_mtime)
    except OSError:
        return None
    surf = _ART.get(key)
    if surf is None:
        try:
            surf = Gdk.cairo_surface_create_from_pixbuf(cover_pixbuf(path, width, height), 1, None)
        except (GLib.Error, OSError):
            return None
        if len(_ART) > 48:
            _ART.clear()
        _ART[key] = surf
    return surf


class SettingsScreen(Screen):
    """Home's language on a menu: the blurred Home photo with a still constellation, a rail of glass nodes
    joined by lines, and a frosted panel of borderless rows (all drawn by widgets.glass)."""
    header = True
    sname = "settings"

    def __init__(self, shell):
        super().__init__(shell)
        px = shell.px
        self.get_style_context().add_class("set-page")
        self.disposed = False
        self.connect("destroy", self.on_destroy)
        self.rows = []
        self.torbox_busy = False
        self.loading = False
        self.outputs = None
        self.busy = False
        self.qr_refresh = None
        self.signal = None
        self.retro = backgrounds.Backgrounds(display.DATA)
        self.memory = getattr(shell, 'settings_memory', {'category': 'Appearance', 'controls': {}, 'scroll': {}})
        shell.settings_memory = self.memory
        self.category = self.memory['category']
        self.look = glass.Look(widgets.load_config(display.DATA))
        overlay = Gtk.Overlay()
        self.pack_start(overlay, True, True, 0)
        self.backdrop = glass.Backdrop(shell, self.look)
        overlay.add(self.backdrop)
        layout = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=px(36))
        layout.set_margin_top(px(146))
        layout.set_margin_start(px(52))
        layout.set_margin_end(px(64))
        layout.set_margin_bottom(px(52))
        overlay.add_overlay(layout)
        rail = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        rail.set_valign(Gtk.Align.START)
        title = text_label("Settings", "set-title")
        title.set_margin_start(px(30))
        title.set_margin_bottom(px(26))
        rail.pack_start(title, False, False, 0)
        self.rail = glass.RailLinks(shell)
        self.tabs = []
        for i, category in enumerate(settings_model.CATEGORIES):
            b = glass.RailNode(shell, self.backdrop, self.look, category, GLYPHS[category], glass.TINTS[category],
                               lambda c=category: self.enter_category(c), ('category', category),
                               indent=px(RAIL_INDENT[i % len(RAIL_INDENT)]))
            b.connect('focus-in-event', lambda _b, _e, c=category: self.select_category(c) if self.shell.current() is self else False)
            self.rail.add_node(b)
            self.tabs.append(b)
        rail.pack_start(self.rail, False, False, 0)
        layout.pack_start(rail, False, False, 0)
        self.rail_box = rail
        self.backdrop.avoid = self.avoid_rects
        panel = glass.GlassPanel(shell, self.backdrop, self.look, fade=px(32))   # >= the tallest scrolled text line
        self.panel = panel
        layout.pack_start(panel, True, True, 0)
        head = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=px(6))     # stays put; only rows scroll
        head.set_margin_top(px(34))
        head.set_margin_start(px(66))
        head.set_margin_end(px(66))
        self.title = text_label(self.category, 'set-output')
        self.subtitle = text_label("", "set-note", wrap=True)
        head.pack_start(self.title, False, False, 0)
        head.pack_start(self.subtitle, False, False, 0)
        panel.pack_start(head, False, False, 0)
        scroll = Gtk.ScrolledWindow()
        scroll.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.EXTERNAL)
        scroll.set_shadow_type(Gtk.ShadowType.NONE)
        scroll.set_margin_start(px(20))
        scroll.set_margin_end(px(20))
        scroll.set_margin_bottom(px(10))
        self.scroll = scroll
        self.body = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        self.body.set_margin_bottom(px(40))
        scroll.add(self.body)
        panel.pack_start(scroll, True, True, 0)
        panel.watch(scroll)
        self.content = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        self.body.pack_start(self.content, False, False, 0)
        if hasattr(shell, 'pair_listeners'):
            shell.pair_listeners.append(self.on_pair)
        self.render()
        if self.memory.get("in_content"):
            key = self.memory["controls"].get(self.category)
            self.last_focus = next((b for row in self.rows for b in row if b.sel_key == key), None)

    def avoid_rects(self):
        """Keep the backdrop's constellation off the rail's labels."""
        pos = self.rail_box.translate_coordinates(self.backdrop, 0, 0)
        w, h = self.rail_box.get_allocated_width(), self.rail_box.get_allocated_height()
        return [(pos[0], pos[1], w, h)] if pos and w > 1 else []

    def on_destroy(self, *_):
        self.disposed = True
        listeners = getattr(self.shell, 'pair_listeners', None)
        if listeners and self.on_pair in listeners:
            listeners.remove(self.on_pair)

    # ---- Screen API
    def focus_groups(self):
        controls = [[b for b in row if b.get_sensitive()] for row in self.rows]
        return [[b] for b in self.tabs] + [row for row in controls if row]

    def first_focus(self, flat):
        return self.tabs[settings_model.CATEGORIES.index(self.category)]

    def track_focus(self, widget):
        self.memory["in_content"] = widget not in self.tabs
        if widget in [b for row in self.rows for b in row]:
            self.memory["controls"][self.category] = widget.sel_key

    def remember(self):
        focus = self.shell.get_focus()
        if focus in [b for row in self.rows for b in row]:
            self.memory['controls'][self.category] = focus.sel_key
        self.memory['scroll'][self.category] = self.scroll.get_vadjustment().get_value()

    def select_category(self, category):
        if category != self.category:
            self.remember()
            self.category = category
            self.memory['category'] = category
            self.render()
        return False

    def enter_category(self, category):
        self.select_category(category)
        flat = [b for row in self.rows for b in row if b.get_sensitive()]
        key = self.memory['controls'].get(category)
        target = next((b for b in flat if b.sel_key == key), flat[0] if flat else None)
        if target:
            target.grab_focus()
            self.reveal(target)

    def handle_key(self, key):
        focus = self.shell.get_focus()
        if focus in self.tabs:
            index = self.tabs.index(focus)
            if key in ('Up', 'Down'):
                self.tabs[max(0, min(len(self.tabs) - 1, index + (1 if key == 'Down' else -1)))].grab_focus()
                return True
            if key in ('Right', 'Return', 'KP_Enter', 'space'):
                self.enter_category(self.category)
                return True
        elif focus and focus.is_ancestor(self):
            if key in ('Escape', 'BackSpace', 'XF86Back', 'Back') and not (isinstance(focus, Gtk.Entry) and key == 'BackSpace' and focus.get_text()):
                self.remember()
                self.tabs[settings_model.CATEGORIES.index(self.category)].grab_focus()
                return True
            if key in ('Up', 'Down'):
                rows = [[b for b in row if b.get_sensitive()] for row in self.rows]
                rows = [row for row in rows if row]
                index = next((i for i, row in enumerate(rows) if focus in row), None)
                if index is not None:
                    column = rows[index].index(focus)
                    row = rows[max(0, min(len(rows) - 1, index + (1 if key == 'Down' else -1)))]
                    target = row[min(column, len(row) - 1)]
                    target.grab_focus()
                    self.reveal(target)
                    return True
            if key in ('Left', 'Right'):
                row = next((row for row in self.rows if focus in row), [])
                if row and not isinstance(focus, Gtk.Entry):
                    index = row.index(focus) + (1 if key == 'Right' else -1)
                    if 0 <= index < len(row):
                        row[index].grab_focus()
                    elif key == 'Left':
                        self.remember()
                        self.tabs[settings_model.CATEGORIES.index(self.category)].grab_focus()
                    return True
        return False

    def on_enter(self, returning):
        self.backdrop.snapshot()
        self.load()

    def reveal(self, widget):
        if widget in self.tabs:
            return
        adj = self.scroll.get_vadjustment()
        pos = widget.translate_coordinates(self.body, 0, 0)
        if not pos:
            return
        y, h, page, m = pos[1], widget.get_allocated_height(), adj.get_page_size(), self.shell.px(110)
        if y - m < adj.get_value():
            adj.set_value(max(0, y - m))
        elif y + h + m > adj.get_value() + page:
            adj.set_value(y + h + m - page)

    # ---- data
    def load(self):
        if self.loading:
            return
        self.loading = True

        def work():
            try:
                outs = display.list_outputs()
            except display.DisplayError as exc:
                print("settings:", exc)
                outs = []
            GLib.idle_add(self.loaded, outs)
        threading.Thread(target=work, daemon=True).start()

    def loaded(self, outs):
        if self.disposed:
            return False
        self.loading = False
        self.outputs = outs
        had = self.shell.get_focus()
        key = getattr(had, "sel_key", None) if had and had.is_ancestor(self) else None
        self.remember()
        self.render()
        self.last_focus = next((b for row in self.focus_groups() for b in row if b.sel_key == key), None) if key else None
        if self.shell.current() is self and not self.shell.sheet:
            self.shell.focus_screen(self)
        return False

    def on_pair(self, *_):
        if self.disposed or self.category != 'Connections':
            return False
        if self.qr_refresh:
            self.qr_refresh()
        if self.signal:
            self.signal.set_net(getattr(self.shell, 'net', None))
        return False

    # ---- view
    def button(self, main, sub, current, key, on_click, value='', toggle=None):
        """A borderless glass row (the name is kept from the old button-based screen)."""
        return glass.GlassRow(self.shell, self.backdrop, self.look, main, sub, value, toggle, current, on_click, key)

    def grid(self, buttons, columns=1, into=None):
        px = self.shell.px
        for line in chunk(buttons, columns):
            if columns == 1:
                (into or self.content).pack_start(line[0], False, False, 0)
            else:
                hb = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=px(2))
                hb.set_margin_start(px(6))
                for b in line:
                    hb.pack_start(b, False, False, 0)
                (into or self.content).pack_start(hb, False, False, 0)
            self.rows.append(line)

    def note(self, text, into=None, top=4):
        label = text_label(text, "set-note", wrap=True)
        label.set_margin_start(self.shell.px(46))
        label.set_margin_end(self.shell.px(46))
        label.set_margin_top(self.shell.px(top))
        label.set_margin_bottom(self.shell.px(8))
        (into or self.content).pack_start(label, False, False, 0)
        return label

    def section(self, title, into=None):
        (into or self.content).pack_start(glass.SectionLabel(self.shell, title, self.look), False, False, 0)

    def render(self):
        for c in self.content.get_children():
            self.content.remove(c)
            c.destroy()
        self.rows = []
        self.qr_refresh = self.signal = None
        look = glass.Look(widgets.load_config(display.DATA))
        if look.key() != self.look.key():
            self.look = look
            self.backdrop.set_look(look)
            for w in self.tabs + [self.panel]:
                w.look = look
                w.queue_draw()
        self.title.set_text(self.category)
        self.subtitle.set_text(SUBTITLES[self.category])
        for b in self.tabs:
            b.set_selected(b.sel_key[1] == self.category)
        self.rail.queue_draw()
        if self.category == 'Appearance':
            self.render_background()
        if self.category in ('Appearance', 'Ambient', 'Playback'):
            self.render_fields()
        if self.category == 'Playback':
            cfg = media_config.load()
            mins = cfg['youtube_close_after_paused_min']
            self.section('YouTube')
            self.grid([self.button('Close YouTube when paused', '', False, ('youtube',),
                                   lambda: self.choice_sheet('Close YouTube when paused', [0, 30, 60], mins,
                                                             lambda v: self.apply_setting('youtube_close', {'minutes': v}),
                                                             labels=['Never', 'After 30 min', 'After 60 min']),
                                   value='Never' if not mins else '%s min' % mins)])
        if self.category == 'Connections':
            self.render_connections()
        if self.category == 'System':
            self.render_system()
        if self.category == 'Display':
            self.render_display()
        self.content.show_all()
        GLib.idle_add(self.restore_scroll)

    def render_display(self):
        if self.outputs is None:
            self.note("Looking for screens…", top=18)
        shown = [o for o in (self.outputs or []) if o["connected"] and o["modes"]]
        if self.outputs is not None and not shown:
            self.note("This screen's resolution can't be changed here.", top=18)
        for out in sorted(shown, key=lambda o: (not o["primary"], o["name"])):
            cur = out.get("current")
            self.section(output_label(out))
            self.note(("Now %s  ·  %s" % (fmt_mode(cur["mode"]), fmt_rate(cur["rate"]))) if cur else "Switched off", top=0)
            self.section('Resolution')
            self.grid([self.button(fmt_mode(m), "", "Current" in tags, (out["name"], m),
                                   lambda o=out["name"], m=m, r=r: self.pick(o, m, r),
                                   value="  ·  ".join(t for t in tags if t != "Current"))
                       for m, r, tags in tv_modes(out)])
            rates = tv_rates(out)
            if len(rates) > 1:
                self.section('Refresh rate')
                self.grid([self.button(fmt_rate(r), "", is_cur, (out["name"], cur["mode"], r),
                                       lambda o=out["name"], m=cur["mode"], r=r: self.pick(o, m, r))
                           for r, is_cur in rates])
        if shown:
            self.note("A new resolution goes back by itself after %d seconds unless you keep it."
                      % display.TRIAL_SECONDS, top=22)

    def render_connections(self):
        px = self.shell.px
        self.section('Phone')
        pair = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=px(30))
        card, self.qr_refresh = pairing.qr_card(self.shell, px(6), photo=self.backdrop)
        card.set_valign(Gtk.Align.START)
        card.set_margin_start(px(40))
        card.set_margin_top(px(6))
        pair.pack_start(card, False, False, 0)
        side = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        self.signal = glass.SignalLine(self.shell, self.look)
        self.signal.set_net(getattr(self.shell, 'net', None))
        self.signal.set_margin_start(px(46))
        self.signal.set_margin_top(px(8))
        side.pack_start(self.signal, False, False, 0)
        self.note('Scan the code with your phone on the same Wi-Fi, or open the address and enter the code.',
                  into=side, top=6)
        self.grid([self.button('Show the code full screen', '', False, ('pairing',), self.shell.show_pairing),
                   self.button('Disconnect other phones', '', False, ('access', 'other_phones'),
                               lambda: self.confirm_access('other_phones')),
                   self.button('Disable legacy link', '', False, ('access', 'old_link'),
                               lambda: self.confirm_access('old_link'))], into=side)
        pair.pack_start(side, True, True, 0)
        self.content.pack_start(pair, False, False, 0)
        self.render_torbox()

    def render_torbox(self):
        px = self.shell.px
        self.section('TorBox')
        self.torbox_status = self.note("", top=0)
        self.torbox_entry = Gtk.Entry()
        self.torbox_entry.set_visibility(False)
        self.torbox_entry.set_max_length(128)
        self.torbox_entry.set_placeholder_text("Paste your TorBox API key")
        self.torbox_entry.get_style_context().add_class("set-entry")
        self.torbox_entry.sel_key = ("torbox", "key")
        self.torbox_entry.connect("activate", lambda *_: self.save_torbox())
        field = glass.GlassField(self.shell, self.backdrop, self.look, self.torbox_entry)
        field.set_margin_start(px(30))
        field.set_margin_end(px(30))
        field.set_margin_top(px(4))
        field.set_margin_bottom(px(6))
        self.content.pack_start(field, False, False, 0)
        self.rows.append([self.torbox_entry])
        self.torbox_save = self.button("Save API key", "Checked with TorBox", False, ("torbox", "save"), self.save_torbox)
        self.torbox_remove = self.button("Remove key", "Disconnect TorBox", False, ("torbox", "remove"),
                                         lambda: self.change_torbox("torbox_remove"))
        self.grid([self.torbox_save, self.torbox_remove])
        self.update_torbox(bool(media_config.read_key()))
        self.note("You can also add or change this key from your phone's Settings.", top=6)

    def update_torbox(self, configured):
        if self.category != "Connections":
            return
        self.torbox_status.set_text("Connected" if configured else "Not connected  ·  add an API key to stream")
        self.torbox_remove.set_sensitive(configured and not self.torbox_busy)

    def save_torbox(self):
        key = self.torbox_entry.get_text().strip()
        self.torbox_entry.set_text("")
        if not key:
            self.shell.toast("Paste your TorBox API key first", 4)
            return
        self.change_torbox("torbox_key", {"key": key})

    def change_torbox(self, action, params=None):
        if self.torbox_busy:
            return
        self.torbox_busy = True
        for w in (self.torbox_entry, self.torbox_save, self.torbox_remove):
            w.set_sensitive(False)
        self.shell.toast("Checking with TorBox…" if action == "torbox_key" else "Removing key…", 3)

        def work():
            result = cmd(action, params, timeout=15)
            GLib.idle_add(self.torbox_changed, result)
        threading.Thread(target=work, daemon=True).start()

    def torbox_changed(self, result):
        self.torbox_busy = False
        if self.category != "Connections":
            return False
        self.torbox_entry.set_sensitive(True)
        self.torbox_save.set_sensitive(True)
        self.update_torbox(bool(media_config.read_key()))
        ok = result and result.get("ok")
        self.shell.toast("TorBox settings saved" if ok else (result or {}).get("error", "Couldn't reach the controller"),
                         3 if ok else 6)
        self.torbox_save.grab_focus()
        return False

    def render_background(self):
        px = self.shell.px
        cfg = widgets.load_config(display.DATA)
        lib = self.retro.list()
        use_retro = cfg["background"] == "retro" and bool(lib)
        home = self.shell.tops.get('home')
        photo = getattr(home, 'current_item', None) or {}
        self.section('Background')
        self.grid([
            self.picture_button('Focus', 'Nature photography', not use_retro, ('bg', 'photos'),
                                lambda: self.choose_background('photos'),
                                photo.get('path') or ASSETS / 'preview-youtube-nature.png'),
            self.picture_button('Retro', '%d pictures' % len(lib) if lib else 'Add pictures from your phone',
                                use_retro, ('bg', 'retro'), lambda: self.choose_background('retro'),
                                self.retro.thumb_path(lib[0]['id']) if lib else None),
        ], columns=2)
        if lib and use_retro:
            off = set(cfg["retro_off"])
            on = {i["id"] for i in lib} - off or {i["id"] for i in lib}
            self.section('Your pictures  ·  %d of %d on' % (len(on), len(lib)))
            pictures = []
            for item in lib:
                bid = item['id']
                pictures.append(self.picture_button(item['name'], 'In rotation' if bid in on else 'Paused',
                                bid in on, ('bg', 'pic', bid), lambda b=bid: self.picture_options(b),
                                self.retro.thumb_path(bid), width=300, height=169))
            self.grid(pictures, columns=3)

    def picture_button(self, title, sub, current, key, action, path, width=440, height=248):
        px = self.shell.px
        return glass.PictureTile(self.shell, self.backdrop, self.look, title, sub,
                                 art_surface(path, px(width), px(height)), current, action, key, width, height)

    def picture_options(self, bid):
        item = next((item for item in self.retro.list() if item['id'] == bid), None)
        if not item:
            return
        enabled = bid not in widgets.load_config(display.DATA)['retro_off']
        self.choice_sheet(item['name'], ['toggle', 'remove'], None,
                          lambda action: self.toggle_retro(bid) if action == 'toggle' else self.confirm_remove(bid),
                          labels=['Pause in rotation' if enabled else 'Include in rotation', 'Remove picture…'])

    def toggle_retro(self, bid):
        lib = self.retro.list()
        cfg = widgets.load_config(display.DATA)
        off = set(cfg["retro_off"]) & {i["id"] for i in lib}
        if bid in off:
            off.discard(bid)
        else:
            off.add(bid)
            if len(off) >= len(lib):
                self.shell.toast("Keep at least one Retro background on", 4)
                return
        widgets.save_keys(display.DATA, {"retro_off": sorted(off)})
        self.rerender(("bg", "pic", bid))

    def rerender(self, key):
        self.render()
        self.content.show_all()
        self.last_focus = next((b for row in self.rows for b in row if b.sel_key == key), None)
        if self.last_focus:
            self.last_focus.grab_focus()

    def choose_background(self, source):
        if source == "retro" and not self.retro.count():
            self.shell.toast("Add a retro picture from your phone first", 4)
            return
        widgets.save_keys(display.DATA, {"background": source})
        self.shell.toast("Theme: " + ("Retro" if source == "retro" else "Focus"), 3)
        self.rerender(("bg", "retro" if source == "retro" else "photos"))

    def restore_scroll(self):
        if self.disposed:
            return False
        self.scroll.get_vadjustment().set_value(self.memory['scroll'].get(self.category, 0))
        return False

    def confirm_remove(self, bid):
        self.choice_sheet('Remove this picture?', [False, True], False,
                          lambda yes: self.apply_setting('retro_remove', {'id': bid}) if yes else None,
                          labels=['Cancel', 'Remove'])

    # ---- sheets: a glass panel over the scrim, same rows as the screen
    def sheet_panel(self, title, note=None, width=760):
        px = self.shell.px
        panel = glass.GlassPanel(self.shell, self.backdrop, self.look)
        panel.set_size_request(px(width), -1)
        inner = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=px(4))
        inner.set_margin_top(px(40))
        inner.set_margin_bottom(px(30))
        inner.set_margin_start(px(20))
        inner.set_margin_end(px(20))
        head = text_label(title, 'set-sheet-title', wrap=True)
        head.set_margin_start(px(46))
        head.set_margin_end(px(46))
        head.set_margin_bottom(px(4 if note else 14))
        inner.pack_start(head, False, False, 0)
        if note:
            self.note(note, into=inner, top=0)
        panel.pack_start(inner, True, True, 0)
        return panel, inner

    def choice_sheet(self, title, choices, current, save, labels=None):
        panel, inner = self.sheet_panel(title)
        buttons = []

        def choose(value):
            self.shell.close_sheet()
            save(value)
        for i, value in enumerate(choices):
            b = self.button(labels[i] if labels else settings_model.label(value), '', value == current,
                            ('choice', i), lambda v=value: choose(v))
            inner.pack_start(b, False, False, 0)
            buttons.append(b)
        self.shell.open_sheet('setting', panel, [[b] for b in buttons], default=buttons[choices.index(current)] if current in choices else buttons[0])

    def multi_sheet(self, key, value, title, options):
        """On/off per option with hex toggles; each press saves and the sheet stays open."""
        panel, inner = self.sheet_panel(title, 'Select an item to turn it on or off.')
        state = [x for x in value if x in options]
        buttons = []

        def flip(option, row):
            if option in state:
                state.remove(option)
            else:
                state.append(option)
            row.set_value(toggle=option in state)
            self.apply_setting('setting_set', {'key': key, 'value': list(state)}, ('setting', key), rebuild=False)
        for option in options:
            row = self.button(settings_model.label(option), '', False, ('multi', option), None, toggle=option in state)
            row.on_activate = lambda o=option, r=row: flip(o, r)
            inner.pack_start(row, False, False, 0)
            buttons.append(row)
        done = self.button('Done', '', False, ('save',), self.shell.close_sheet)
        inner.pack_start(done, False, False, 0)
        buttons.append(done)
        self.shell.open_sheet('setting', panel, [[b] for b in buttons], default=buttons[0])

    def render_fields(self):
        cfg = widgets.load_config(display.DATA)
        values = settings_model.values(cfg, media_config.load())
        for key, category, title, kind, options, network in settings_model.FIELDS:
            if category != self.category:
                continue
            if key in HEADINGS:
                self.section(HEADINGS[key])
            disabled = network and cfg['style'] == 'bubbles'
            value = values[key]
            action = lambda k=key, v=value, t=title, typ=kind, opts=options: self.edit_setting(k, v, t, typ, opts)
            if disabled:
                b = self.button(title, 'Available with the Network layout  ·  saved: ' + settings_model.label(value),
                                False, ('setting', key), action)
            elif kind == 'bool':
                b = self.button(title, '', False, ('setting', key), action, toggle=bool(value))
            else:
                b = self.button(title, '', False, ('setting', key), action, value=settings_model.label(value))
            b.set_sensitive(not disabled)
            self.grid([b])

    def edit_setting(self, key, value, title, kind, options, reorder=True):
        save = lambda v: self.apply_setting('setting_set', {'key': key, 'value': v}, ('setting', key))
        if kind in ('order', 'languages') and reorder:
            self.order_sheet(key, value, title, kind, options)
            return
        if kind == 'bool':                        # OK flips it right here, like a switch
            save(not value)
            return
        if kind == 'choice':
            self.choice_sheet(title, options, value, save)
            return
        if kind == 'multi':
            self.multi_sheet(key, value, title, options)
            return
        note = 'Comma separated, first preferred' if kind in ('languages', 'order') else ('Range: %s–%s' % tuple(options) if kind == 'number' else 'Use a two or three letter language code, e.g. eng')
        panel, inner = self.sheet_panel(title, note)
        entry = Gtk.Entry()
        entry.get_style_context().add_class('set-entry')
        entry.set_text(', '.join(value) if isinstance(value, list) else str(value))
        field = glass.GlassField(self.shell, self.backdrop, self.look, entry)
        px = self.shell.px
        field.set_margin_start(px(30))
        field.set_margin_end(px(30))
        field.set_margin_bottom(px(8))
        inner.pack_start(field, False, False, 0)

        def submit():
            try:
                raw = entry.get_text().strip()
                v = float(raw) if kind == 'number' else [v.strip() for v in raw.split(',')] if kind in ('languages', 'order') else raw
                settings_model.validate(key, v)
            except ValueError as exc:
                self.shell.toast(str(exc), 4)
                return
            self.shell.close_sheet()
            save(v)
        b = self.button('Save', '', False, ('save',), submit)
        entry.connect('activate', lambda *_: submit())
        inner.pack_start(b, False, False, 0)
        self.shell.open_sheet('setting', panel, [[entry], [b]], default=entry)

    def order_sheet(self, key, value, title, kind, options):
        px = self.shell.px
        panel, inner = self.sheet_panel(title, 'First is preferred. Select an item to move it to first.')
        scroll = Gtk.ScrolledWindow()
        scroll.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        scroll.set_max_content_height(px(430))
        scroll.set_propagate_natural_height(True)
        items = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        scroll.add(items)
        inner.pack_start(scroll, True, True, 0)
        buttons = []
        for index, item in enumerate(value):
            b = self.button('%d   %s' % (index + 1, settings_model.label(item)), '', False, ('order', item),
                            lambda v=item: self.order_sheet(key, [v] + [x for x in value if x != v], title, kind, options),
                            value='First' if index == 0 else 'Move to first')
            items.pack_start(b, False, False, 0)
            buttons.append(b)
        if kind == 'languages':
            edit = self.button('Edit language codes', 'Add or remove languages', False, ('edit',),
                               lambda: self.edit_setting(key, value, title, kind, options, reorder=False))
            inner.pack_start(edit, False, False, 0)
            buttons.append(edit)

        def save():
            self.shell.close_sheet()
            self.apply_setting('setting_set', {'key': key, 'value': value}, ('setting', key))
        done = self.button('Save order', '', False, ('save',), save)
        inner.pack_start(done, False, False, 0)
        buttons.append(done)
        self.shell.open_sheet('setting', panel, [[b] for b in buttons], default=buttons[0])

    def apply_setting(self, action, params, key=None, rebuild=True):
        self.remember()

        def work():
            result = cmd(action, params, timeout=15)
            GLib.idle_add(done, result)

        def done(result):
            if self.disposed:
                return False
            if not result or not result.get('ok'):
                self.shell.toast((result or {}).get('error', 'Could not reach the controller'), 5)
            if rebuild or not self.shell.sheet:
                self.rerender(key)
            return False
        threading.Thread(target=work, daemon=True).start()

    def confirm_access(self, mode):
        self.choice_sheet('Disconnect other phones?' if mode == 'other_phones' else 'Disable legacy link?', [False, True], False,
                          lambda yes: self.revoke_access(mode) if yes else None, labels=['Cancel', 'Confirm'])

    def revoke_access(self, mode):
        def work():
            from screens import post_local
            result = post_local('/api/revoke', {'mode': mode})
            GLib.idle_add(self.shell.toast, 'Phone access updated' if result and result.get('ok') else 'Could not update phone access', 4)
        threading.Thread(target=work, daemon=True).start()

    def render_system(self):
        self.section('Software updates')
        self.update_status = self.note('', top=0)
        self.update_check = self.button('Check for updates', 'Checks GitHub for a new TV version', False,
                                        ('update_check',), lambda: self.apply_setting('update_check', {}))
        self.update_install = self.button('Update now', 'Installs the update and restarts the TV app; playback stops', False,
                                          ('update_install',), lambda: self.apply_setting('update_install', {}))
        self.grid([self.update_check, self.update_install])
        self.section('Maintenance')
        for title, sub, action in [('Restart current app', 'Close and reopen what is playing', 'restart_app'),
                                   ('Recover TV Home', 'Bring the TV back to this screen', 'recover'),
                                   ('Restart desktop session', 'Stops playback', 'restart_session')]:
            def invoke(a=action):
                if a == 'restart_session':
                    self.choice_sheet('Restart desktop and stop playback?', [False, True], False,
                                      lambda yes: self.apply_setting(a, {'confirm': True}) if yes else None, labels=['Cancel', 'Restart'])
                else:
                    self.apply_setting(a, {})
            self.grid([self.button(title, sub, False, (action,), invoke)])
        self.section('Status')
        state = getattr(self.shell, 'settings_status', {}) or {}
        self.system_status = self.note("", top=0)
        self.update_system_status(state)

    def update_system_status(self, state):
        updates = state.get('updates') or {}
        self.update_status.set_text(updates.get('message', 'Waiting for the controller…'))
        self.update_check.set_sensitive(bool(updates) and not updates.get('busy'))
        self.update_install.set_sensitive(bool(updates.get('can_update')) and not updates.get('busy'))
        self.system_status.set_text('Controller %s  ·  CPU %s  ·  GPU %s' % (
            'connected' if state else 'unavailable',
            str(state.get('temperature') or '–') + '°C', str(state.get('gpu_temperature') or '–') + '°C'))

    # ---- actions
    def pick(self, output, mode, rate):
        out = display.find_output(self.outputs or [], output)
        cur = out and out.get("current")
        if cur and cur["mode"] == mode and abs(cur["rate"] - (rate or 0)) < 0.02 and not display.trial_state()["active"]:
            self.shell.toast("Already using %s at %s" % (fmt_mode(mode), fmt_rate(rate)), 3)
            return
        if self.busy:
            return
        self.busy = True
        shell = self.shell

        def work():
            err = None
            try:
                display.start_trial(output, mode, rate)
            except display.DisplayError as exc:
                err = str(exc)
            GLib.idle_add(self.picked, err)
        threading.Thread(target=work, daemon=True).start()
        shell.toast("Switching to %s…" % fmt_mode(mode), 2)

    def picked(self, err):
        self.busy = False
        if err:
            self.shell.toast("Couldn't change the resolution: " + err[:80], 6)
        self.shell.display_changed()
        return False

def show_trial_sheet(shell, state):
    """'Keep this resolution?' with Keep (focused) / Revert. Returns the countdown updater."""
    from widgets.pairing import _glass_button
    p = shell.px
    box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=p(16))
    box.get_style_context().add_class("ah-sheet")
    title = text_label("Keep this resolution?", "ah-dtitle")
    sub = text_label("%s at %s" % (fmt_mode(state["mode"]), fmt_rate(state["rate"])), "ah-dsub")
    count = text_label("", "set-count")
    keep, revert = _glass_button("Keep", True), _glass_button("Revert")

    def finish(fn):
        shell.close_sheet("trial")

        def work():
            fn()
            GLib.idle_add(shell.display_changed)
        threading.Thread(target=work, daemon=True).start()
    keep.connect("clicked", lambda *_: finish(display.confirm_trial))
    revert.connect("clicked", lambda *_: finish(display.revert_trial))
    row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=p(20))
    row.set_halign(Gtk.Align.CENTER)
    row.pack_start(keep, False, False, 0)
    row.pack_start(revert, False, False, 0)
    for w in (title, sub, count):
        w.set_halign(Gtk.Align.CENTER)
        box.pack_start(w, False, False, 0)
    box.pack_start(row, False, False, p(8))

    def update(st):
        count.set_text("Reverting in %d s" % max(0, round(st.get("remaining", 0))))
    update(state)
    shell.open_sheet("trial", box, [[keep, revert]], default=keep)
    shell.sheet["countdown"] = update
    shell.sheet["revert"] = lambda: finish(display.revert_trial)
    return update
