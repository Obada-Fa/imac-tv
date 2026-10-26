"""Category settings for the TV and the shared timed display confirmation.

Display operations run on workers; the shell owns the confirmation countdown so
it survives resolution changes and trials initiated by a paired phone.
"""
import sys
import threading
from pathlib import Path

import gi
gi.require_version("Gtk", "3.0")
from gi.repository import Gtk, GLib

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import backgrounds  # noqa: E402
import display  # noqa: E402
import settings_model
import widgets  # noqa: E402
from screens import Screen, text_label, cmd  # noqa: E402
from media import config as media_config  # noqa: E402

PER_ROW = 5
MIN_WIDTH = 640


def settings_css(scale=1.0):
    p = lambda n: max(1, round(n * scale))
    return f"""
.set-page {{ background: #11151c; }}
.set-title {{ font-size: {p(52)}px; font-weight: 300; color: #fff; }}
.set-section {{ font-size: {p(20)}px; font-weight: 800; letter-spacing: 2px; color: #93a4c3; }}
.set-output {{ font-size: {p(30)}px; font-weight: 700; color: #fff; }}
.set-note {{ font-size: {p(20)}px; color: rgba(255,255,255,0.62); }}
.set-btn {{ background: rgba(255,255,255,0.07); border: {p(2)}px solid rgba(255,255,255,0.18); border-radius: {p(10)}px;
           color: #fff; padding: {p(12)}px {p(26)}px; box-shadow: none; min-width: {p(150)}px; }}
.set-btn:focus {{ background: rgba(255,255,255,0.26); border-color: #fff; box-shadow: 0 0 0 {p(3)}px rgba(255,255,255,0.28); }}
.set-current {{ border-color: #a8cfff; background: rgba(96,165,250,0.16); }}
.set-btn-main {{ font-size: {p(26)}px; font-weight: 700; color: #fff; }}
.set-btn-sub {{ font-size: {p(20)}px; font-weight: 600; color: rgba(255,255,255,0.6); }}
.set-current .set-btn-sub {{ color: #93c5fd; }}
.set-count {{ font-size: {p(30)}px; font-weight: 700; color: #facc15; }}
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


class SettingsScreen(Screen):
    header = True
    sname = "settings"

    def __init__(self, shell):
        super().__init__(shell)
        px = shell.px
        self.get_style_context().add_class("set-page")
        self.disposed = False
        self.connect("destroy", lambda *_: setattr(self, "disposed", True))
        self.rows = []
        self.torbox_busy = False
        self.loading = False
        self.outputs = None
        self.busy = False
        self.retro = backgrounds.Backgrounds(display.DATA)
        self.memory = getattr(shell, 'settings_memory', {'category': 'Appearance', 'controls': {}, 'scroll': {}})
        shell.settings_memory = self.memory
        self.category = self.memory['category']
        layout = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=px(36))
        layout.set_margin_top(px(140))
        layout.set_margin_start(px(64))
        layout.set_margin_end(px(64))
        layout.set_margin_bottom(px(40))
        self.pack_start(layout, True, True, 0)
        rail = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=px(8))
        rail.get_style_context().add_class("set-rail")
        rail.set_valign(Gtk.Align.START)
        rail.pack_start(text_label("Settings", "set-title"), False, False, px(12))
        self.tabs = []
        for category in settings_model.CATEGORIES:
            b = self.button(category, '', category == self.category, ('category', category),
                            lambda c=category: self.enter_category(c))
            b.connect('focus-in-event', lambda _b, _e, c=category: self.select_category(c) if self.shell.current() is self else False)
            rail.pack_start(b, False, False, 0)
            self.tabs.append(b)
        layout.pack_start(rail, False, False, 0)
        scroll = Gtk.ScrolledWindow()
        scroll.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        scroll.set_shadow_type(Gtk.ShadowType.NONE)
        self.scroll = scroll
        self.body = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=px(16))
        self.body.set_margin_bottom(px(80))
        scroll.add(self.body)
        layout.pack_start(scroll, True, True, 0)
        self.title = text_label(self.category, 'set-output')
        self.body.pack_start(self.title, False, False, 0)
        self.subtitle = text_label("", "set-note", wrap=True)
        self.body.pack_start(self.subtitle, False, False, 0)
        self.content = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=px(12))
        self.body.pack_start(self.content, False, False, 0)
        self.render()
        if self.memory.get("in_content"):
            key = self.memory["controls"].get(self.category)
            self.last_focus = next((b for row in self.rows for b in row if b.sel_key == key), None)

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
                flat = [b for row in self.rows for b in row if b.get_sensitive()]
                if focus in flat:
                    i = flat.index(focus)
                    target = flat[max(0, min(len(flat) - 1, i + (1 if key == 'Down' else -1)))]
                    target.grab_focus()
                    self.reveal(target)
                    return True
        return False

    def on_enter(self, returning):
        self.load()

    def reveal(self, widget):
        if widget in self.tabs:
            return
        adj = self.scroll.get_vadjustment()
        pos = widget.translate_coordinates(self.body, 0, 0)
        if not pos:
            return
        y, h, page, m = pos[1], widget.get_allocated_height(), adj.get_page_size(), self.shell.px(140)
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

    # ---- view
    def button(self, main, sub, current, key, on_click):
        px = self.shell.px
        b = Gtk.Button()
        b.set_relief(Gtk.ReliefStyle.NONE)
        b.get_style_context().add_class("set-btn")
        if current:
            b.get_style_context().add_class("set-current")
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=px(2))
        m = text_label(main, "set-btn-main", wrap=True)
        m.set_max_width_chars(44)
        m.set_halign(Gtk.Align.CENTER)
        box.pack_start(m, False, False, 0)
        s = text_label(sub or " ", "set-btn-sub", wrap=True)
        s.set_max_width_chars(54)
        s.set_halign(Gtk.Align.CENTER)
        if sub:
            box.pack_start(s, False, False, 0)
        b.add(box)
        b.sel_key = key
        b.connect("clicked", lambda *_: on_click())
        return b

    def grid(self, buttons):
        px = self.shell.px
        for line in chunk(buttons, 1):
            hb = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=px(18))
            for b in line:
                hb.pack_start(b, True, True, 0)
            self.content.pack_start(hb, False, False, px(4))
            self.rows.append(line)

    def render(self):
        px = self.shell.px
        for c in self.content.get_children():
            self.content.remove(c)
            c.destroy()
        self.rows = []
        self.title.set_text(self.category)
        self.subtitle.set_text({
            "Appearance": "Make it yours. Shape the look and choose a lighter experience.",
            "Ambient": "Set the mood for the moments between watching.",
            "Display": "A comfortable picture, matched to your screen.",
            "Playback": "Your languages, quality and watching preferences.",
            "Connections": "Keep your phone and streaming services close.",
            "System": "Everything you need to keep your TV running smoothly.",
        }[self.category])
        for b in self.tabs:
            ctx = b.get_style_context()
            (ctx.add_class if b.sel_key[1] == self.category else ctx.remove_class)('set-current')
        if self.category == 'Appearance':
            self.content.pack_start(text_label(
                "Turn off Transparent surfaces for solid panels. Turn off Visual effects "
                "to pause decorative motion, photo fades and ambient visualizers.",
                "set-note", wrap=True), False, False, px(8))
        if self.category in ('Appearance', 'Ambient', 'Playback'):
            self.render_fields()
        if self.category == "Appearance":
            self.render_background()
        if self.category == 'Playback':
            cfg = media_config.load()
            self.grid([self.button('YouTube paused auto-close', 'Off' if not cfg['youtube_close_after_paused_min'] else str(cfg['youtube_close_after_paused_min']) + ' min', False, ('youtube',),
                                   lambda: self.choice_sheet('YouTube paused auto-close', [0, 30, 60], cfg['youtube_close_after_paused_min'], lambda v: self.apply_setting('youtube_close', {'minutes': v})))])
        if self.category == 'Connections':
            self.render_torbox()
            self.grid([self.button('Phone pairing', 'Show QR code and pairing code', False, ('pairing',), self.shell.show_pairing),
                       self.button('Disconnect other phones', '', False, ('access', 'other_phones'), lambda: self.confirm_access('other_phones')),
                       self.button('Disable legacy link', '', False, ('access', 'old_link'), lambda: self.confirm_access('old_link'))])
        if self.category == 'System':
            self.render_system()
        if self.category != 'Display':
            self.content.show_all()
            GLib.idle_add(self.restore_scroll)
            return
        sec = text_label("DISPLAY", "set-section")
        sec.set_margin_top(px(30))
        self.content.pack_start(sec, False, False, 0)
        if self.outputs is None:
            self.content.pack_start(text_label("Looking for screens…", "set-note"), False, False, 0)
        shown = [o for o in (self.outputs or []) if o["connected"] and o["modes"]]
        if self.outputs is not None and not shown:
            self.content.pack_start(text_label("This screen's resolution can't be changed here.", "set-note"),
                                    False, False, 0)
        for out in sorted(shown, key=lambda o: (not o["primary"], o["name"])):
            head = text_label(output_label(out), "set-output")
            head.set_margin_top(px(18))
            self.content.pack_start(head, False, False, 0)
            cur = out.get("current")
            note = ("Now %s at %s" % (fmt_mode(cur["mode"]), fmt_rate(cur["rate"]))) if cur else "Switched off"
            self.content.pack_start(text_label(note, "set-note"), False, False, 0)
            res = text_label("RESOLUTION", "set-section")
            res.set_margin_top(px(16))
            self.content.pack_start(res, False, False, 0)
            self.grid([self.button(fmt_mode(m), " · ".join(tags), "Current" in tags, (out["name"], m),
                                   lambda o=out["name"], m=m, r=r: self.pick(o, m, r))
                       for m, r, tags in tv_modes(out)])
            rates = tv_rates(out)
            if len(rates) > 1:
                rl = text_label("REFRESH RATE", "set-section")
                rl.set_margin_top(px(16))
                self.content.pack_start(rl, False, False, 0)
                self.grid([self.button(fmt_rate(r), "Current" if is_cur else "", is_cur, (out["name"], cur["mode"], r),
                                       lambda o=out["name"], m=cur["mode"], r=r: self.pick(o, m, r))
                           for r, is_cur in rates])
        if shown:
            foot = text_label("A new resolution goes back by itself after %d seconds unless you keep it."
                              % display.TRIAL_SECONDS, "set-note")
            foot.set_margin_top(px(26))
            self.content.pack_start(foot, False, False, 0)
        self.content.show_all()
        GLib.idle_add(self.restore_scroll)

    def render_torbox(self):
        px = self.shell.px
        self.content.pack_start(text_label("TORBOX", "set-section"), False, False, 0)
        self.torbox_status = text_label("", "set-note")
        self.content.pack_start(self.torbox_status, False, False, 0)
        self.torbox_entry = Gtk.Entry()
        self.torbox_entry.set_visibility(False)
        self.torbox_entry.set_max_length(128)
        self.torbox_entry.set_placeholder_text("Paste your TorBox API key")
        self.torbox_entry.get_style_context().add_class("search-entry")
        self.torbox_entry.set_size_request(-1, px(70))
        self.torbox_entry.sel_key = ("torbox", "key")
        self.torbox_entry.connect("activate", lambda *_: self.save_torbox())
        self.content.pack_start(self.torbox_entry, False, False, 0)
        self.rows.append([self.torbox_entry])
        self.torbox_save = self.button("Save API key", "Checked with TorBox", False, ("torbox", "save"), self.save_torbox)
        self.torbox_remove = self.button("Remove key", "Disconnect TorBox", False, ("torbox", "remove"),
                                         lambda: self.change_torbox("torbox_remove"))
        self.grid([self.torbox_save, self.torbox_remove])
        self.update_torbox(bool(media_config.read_key()))
        self.content.pack_start(text_label("You can also add or change this key from your phone's Settings.", "set-note"),
                                False, False, 0)

    def update_torbox(self, configured):
        if self.category != "Connections":
            return
        self.torbox_status.set_text("TorBox: " + ("connected" if configured else "not connected"))
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
        self.content.pack_start(text_label("THEMES", "set-section"), False, False, 0)
        self.grid([self.button("Focus", "Nature photos", not use_retro, ("bg", "photos"),
                               lambda: self.choose_background("photos")),
                   self.button("Retro", ("%d pictures" % len(lib)) if lib else "None added yet", use_retro,
                               ("bg", "retro"), lambda: self.choose_background("retro"))])
        home = self.shell.tops.get('home')
        photo = getattr(home, 'current_item', None) or {}
        for row in self.rows:
            for button in row:
                if button.sel_key == ('bg', 'photos'):
                    self.add_thumbnail(button, photo.get('path'))
                elif button.sel_key == ('bg', 'retro') and lib:
                    self.add_thumbnail(button, self.retro.thumb_path(lib[0]['id']))
        if lib:
            off = set(cfg["retro_off"])
            on = {i["id"] for i in lib} - off or {i["id"] for i in lib}
            hdr = text_label("RETRO BACKGROUNDS · %d OF %d ON" % (len(on), len(lib)), "set-section")
            hdr.set_margin_top(px(16))
            self.content.pack_start(hdr, False, False, 0)
            for item in lib:
                bid = item['id']
                button = self.button(item['name'], 'On' if bid in on else 'Off', bid in on,
                                     ('bg', 'pic', bid), lambda b=bid: self.toggle_retro(b))
                self.add_thumbnail(button, self.retro.thumb_path(bid))
                self.grid([button, self.button('Remove ' + item['name'], '', False, ('remove', bid),
                                              lambda b=bid: self.confirm_remove(b))])
        note = ("Add pictures from your phone (Settings, Appearance)." if not lib else
                "Select a picture to switch it on or off. Add or replace pictures from your phone.")
        self.content.pack_start(text_label(note, "set-note"), False, False, 0)

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

    def add_thumbnail(self, button, path):
        if not path:
            return
        from gi.repository import GdkPixbuf
        try:
            pix = GdkPixbuf.Pixbuf.new_from_file_at_scale(str(path), self.shell.px(180), self.shell.px(90), True)
            image = Gtk.Image.new_from_pixbuf(pix)
            button.get_child().pack_start(image, False, False, 0)
            button.get_child().reorder_child(image, 0)
        except Exception:
            pass

    def confirm_remove(self, bid):
        self.choice_sheet('Remove this picture?', [False, True], False,
                          lambda yes: self.apply_setting('retro_remove', {'id': bid}) if yes else None,
                          labels=['Cancel', 'Remove'])

    def choice_sheet(self, title, choices, current, save, labels=None):
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=self.shell.px(12))
        box.get_style_context().add_class('ah-sheet')
        box.pack_start(text_label(title, 'set-output'), False, False, 0)
        buttons = []
        def choose(value):
            self.shell.close_sheet()
            save(value)
        for i, value in enumerate(choices):
            b = self.button(labels[i] if labels else settings_model.label(value), '', value == current,
                            ('choice', i), lambda v=value: choose(v))
            box.pack_start(b, False, False, 0)
            buttons.append(b)
        self.shell.open_sheet('setting', box, [[b] for b in buttons], default=buttons[choices.index(current)] if current in choices else buttons[0])

    def render_fields(self):
        cfg = widgets.load_config(display.DATA)
        values = settings_model.values(cfg, media_config.load())
        for key, category, title, kind, options, network in settings_model.FIELDS:
            if category != self.category:
                continue
            disabled = network and cfg['style'] == 'bubbles'
            value = values[key]
            b = self.button(title, 'Available with Network; saved: ' + settings_model.label(value) if disabled else settings_model.label(value),
                            False, ('setting', key), lambda k=key, v=value, t=title, typ=kind, opts=options: self.edit_setting(k, v, t, typ, opts))
            box = b.get_child()
            box.set_orientation(Gtk.Orientation.HORIZONTAL)
            labels = box.get_children()
            labels[0].set_halign(Gtk.Align.START)
            labels[0].set_xalign(0)
            labels[0].set_max_width_chars(34)
            box.set_child_packing(labels[0], True, True, 0, Gtk.PackType.START)
            labels[1].set_halign(Gtk.Align.END)
            labels[1].set_xalign(1)
            labels[1].set_max_width_chars(34)
            b.set_sensitive(not disabled)
            self.grid([b])

    def edit_setting(self, key, value, title, kind, options, reorder=True):
        save = lambda v: self.apply_setting('setting_set', {'key': key, 'value': v}, ('setting', key))
        if kind in ('order', 'languages') and reorder:
            self.order_sheet(key, value, title, kind, options)
            return
        if kind in ('choice', 'bool'):
            self.choice_sheet(title, options if kind == 'choice' else [False, True], value, save)
            return
        if kind == 'multi':
            self.choice_sheet(title, options, None,
                              lambda v: save([x for x in value if x in options and x != v] if v in value else [x for x in value if x in options] + [v]),
                              labels=[('On · ' if v in value else 'Off · ') + v for v in options])
            return
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=self.shell.px(16))
        box.get_style_context().add_class('ah-sheet')
        box.pack_start(text_label(title, 'set-note'), False, False, 0)
        note = 'Comma separated, first preferred' if kind in ('languages', 'order') else ('Range: %s–%s' % tuple(options) if kind == 'number' else 'Use a two or three letter language code, e.g. eng')
        box.pack_start(text_label(note, 'set-note'), False, False, 0)
        entry = Gtk.Entry()
        entry.set_text(', '.join(value) if isinstance(value, list) else str(value))
        box.pack_start(entry, False, False, 0)
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
        box.pack_start(b, False, False, 0)
        self.shell.open_sheet('setting', box, [[entry], [b]], default=entry)

    def order_sheet(self, key, value, title, kind, options):
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=self.shell.px(10))
        box.get_style_context().add_class('ah-sheet')
        box.pack_start(text_label(title, 'set-output'), False, False, 0)
        box.pack_start(text_label('First is preferred. Select an item to move it to first.', 'set-note'), False, False, 0)
        scroll = Gtk.ScrolledWindow()
        scroll.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        scroll.set_max_content_height(self.shell.px(400))
        scroll.set_propagate_natural_height(True)
        items = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=self.shell.px(8))
        scroll.add(items)
        box.pack_start(scroll, True, True, 0)
        buttons = []
        for index, item in enumerate(value):
            b = self.button('%d. %s' % (index + 1, settings_model.label(item)), 'Move to first', False, ('order', item),
                            lambda v=item: self.order_sheet(key, [v] + [x for x in value if x != v], title, kind, options))
            items.pack_start(b, False, False, 0)
            buttons.append(b)
        if kind == 'languages':
            edit = self.button('Edit language codes', 'Add or remove languages', False, ('edit',),
                               lambda: self.edit_setting(key, value, title, kind, options, reorder=False))
            box.pack_start(edit, False, False, 0)
            buttons.append(edit)
        def save():
            self.shell.close_sheet()
            self.apply_setting('setting_set', {'key': key, 'value': value}, ('setting', key))
        done = self.button('Save order', '', False, ('save',), save)
        box.pack_start(done, False, False, 0)
        buttons.append(done)
        self.shell.open_sheet('setting', box, [[b] for b in buttons], default=buttons[0])

    def apply_setting(self, action, params, key=None):
        self.remember()
        def work():
            result = cmd(action, params, timeout=15)
            GLib.idle_add(done, result)
        def done(result):
            if not result or not result.get('ok'):
                self.shell.toast((result or {}).get('error', 'Could not reach the controller'), 5)
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
        for title, action in [('Restart current app', 'restart_app'), ('Recover TV Home', 'recover'), ('Restart desktop session', 'restart_session')]:
            def invoke(a=action):
                if a == 'restart_session':
                    self.choice_sheet('Restart desktop and stop playback?', [False, True], False,
                                      lambda yes: self.apply_setting(a, {'confirm': True}) if yes else None, labels=['Cancel', 'Restart'])
                else:
                    self.apply_setting(a, {})
            self.grid([self.button(title, '', False, (action,), invoke)])
        state = getattr(self.shell, 'settings_status', {}) or {}
        self.system_status = text_label("", "set-note")
        self.content.pack_start(self.system_status, False, False, 0)
        self.update_system_status(state)

    def update_system_status(self, state):
        self.system_status.set_text('Controller: %s · CPU: %s · GPU: %s' % (
            'connected' if state else 'unavailable',
            str(state.get('temperature') or 'unknown') + '°C', str(state.get('gpu_temperature') or 'unknown') + '°C'))

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
