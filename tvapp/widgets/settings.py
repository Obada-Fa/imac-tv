"""TV Settings screen (Display: resolution and refresh rate) and the "Keep this resolution?" sheet.

Both go through display.py's contract (list_outputs / start_trial / confirm_trial / revert_trial /
trial_state), the same calls the phone Settings uses. xrandr runs on worker threads; the GTK side only
builds buttons. The countdown sheet is driven by the shell's tick from display.trial_state(), so a trial
started from the phone shows here too, and the sheet survives the re-layout a mode change causes.
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
import widgets  # noqa: E402
from screens import Screen, text_label  # noqa: E402

PER_ROW = 5
MIN_WIDTH = 640


def settings_css(scale=1.0):
    p = lambda n: max(1, round(n * scale))
    return f"""
.set-page {{ background-image: linear-gradient(160deg, #10141f 0%, #07080c 70%); }}
.set-title {{ font-size: {p(52)}px; font-weight: 300; color: #fff; }}
.set-section {{ font-size: {p(20)}px; font-weight: 800; letter-spacing: 2px; color: #93a4c3; }}
.set-output {{ font-size: {p(30)}px; font-weight: 700; color: #fff; }}
.set-note {{ font-size: {p(20)}px; color: rgba(255,255,255,0.62); }}
.set-btn {{ background: rgba(255,255,255,0.07); border: {p(2)}px solid rgba(255,255,255,0.18); border-radius: {p(22)}px;
           color: #fff; padding: {p(12)}px {p(26)}px; box-shadow: none; min-width: {p(190)}px; }}
.set-btn:focus {{ background: rgba(255,255,255,0.26); border-color: #fff; box-shadow: 0 0 0 {p(3)}px rgba(255,255,255,0.28); }}
.set-current {{ border-color: #60a5fa; background: rgba(96,165,250,0.16); }}
.set-btn-main {{ font-size: {p(26)}px; font-weight: 700; color: #fff; }}
.set-btn-sub {{ font-size: {p(16)}px; font-weight: 600; color: rgba(255,255,255,0.6); }}
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
        self.rows = []
        self.loading = False
        self.outputs = None
        self.busy = False
        self.retro = backgrounds.Backgrounds(display.DATA)
        scroll = Gtk.ScrolledWindow()
        scroll.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.EXTERNAL)
        scroll.set_shadow_type(Gtk.ShadowType.NONE)
        self.scroll = scroll
        self.body = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=px(10))
        self.body.set_margin_top(px(140))
        self.body.set_margin_start(px(96))
        self.body.set_margin_end(px(96))
        self.body.set_margin_bottom(px(120))
        scroll.add(self.body)
        self.pack_start(scroll, True, True, 0)
        self.title = text_label("Settings", "set-title")
        self.body.pack_start(self.title, False, False, 0)
        self.content = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=px(10))
        self.body.pack_start(self.content, False, False, px(20))
        self.render()

    # ---- Screen API
    def focus_groups(self):
        return [r for r in self.rows if r]

    def first_focus(self, flat):
        for row in self.rows:
            for b in row:
                if b.get_style_context().has_class("set-current"):
                    return b
        return flat[0] if flat else None

    def on_enter(self, returning):
        self.load()

    def reveal(self, widget):
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
        self.loading = False
        self.outputs = outs
        had = self.shell.get_focus()
        key = getattr(had, "sel_key", None) if had and had.is_ancestor(self) else None
        self.render()
        self.last_focus = next((b for row in self.rows for b in row if b.sel_key == key), None) if key else None
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
        m = text_label(main, "set-btn-main")
        m.set_halign(Gtk.Align.CENTER)
        box.pack_start(m, False, False, 0)
        s = text_label(sub or " ", "set-btn-sub")
        s.set_halign(Gtk.Align.CENTER)
        box.pack_start(s, False, False, 0)
        b.add(box)
        b.sel_key = key
        b.connect("clicked", lambda *_: on_click())
        return b

    def grid(self, buttons):
        px = self.shell.px
        for line in chunk(buttons, PER_ROW):
            hb = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=px(18))
            for b in line:
                hb.pack_start(b, False, False, 0)
            self.content.pack_start(hb, False, False, px(4))
            self.rows.append(line)

    def render(self):
        px = self.shell.px
        for c in self.content.get_children():
            self.content.remove(c)
            c.destroy()
        self.rows = []
        self.render_background()
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

    def render_background(self):
        px = self.shell.px
        cfg = widgets.load_config(display.DATA)
        lib = self.retro.list()
        use_retro = cfg["background"] == "retro" and bool(lib)
        self.content.pack_start(text_label("BACKGROUND", "set-section"), False, False, 0)
        self.grid([self.button("Photos", "Bing and Wikimedia", not use_retro, ("bg", "photos"),
                               lambda: self.choose_background("photos", "shuffle")),
                   self.button("Retro", ("%d pictures" % len(lib)) if lib else "None added yet", use_retro,
                               ("bg", "retro"), lambda: self.choose_background("retro", cfg["retro_pick"]))])
        if lib:
            pick = cfg["retro_pick"] if any(i["id"] == cfg["retro_pick"] for i in lib) else "shuffle"
            hdr = text_label("RETRO PICTURES", "set-section")
            hdr.set_margin_top(px(16))
            self.content.pack_start(hdr, False, False, 0)
            btns = [self.button("Shuffle all", "", pick == "shuffle", ("bg", "pick", "shuffle"),
                                lambda: self.choose_background("retro", "shuffle"))]
            for i in lib:
                btns.append(self.button(i["name"][:18], "%d × %d" % (i["width"], i["height"]), pick == i["id"],
                                        ("bg", "pick", i["id"]),
                                        lambda b=i["id"]: self.choose_background("retro", b)))
            self.grid(btns)
        note = ("Add pictures from your phone (Settings, Background)." if not lib else
                "Add or replace pictures from your phone. They stay at full size.")
        self.content.pack_start(text_label(note, "set-note"), False, False, 0)

    def choose_background(self, source, pick):
        if source == "retro" and not self.retro.count():
            self.shell.toast("Add a retro picture from your phone first", 4)
            return
        widgets.save_keys(display.DATA, {"background": source, "retro_pick": pick})
        self.shell.toast("Background: " + ("Retro" if source == "retro" else "Photos"), 3)
        self.render()
        self.content.show_all()
        key = ("bg", "retro" if source == "retro" else "photos")
        self.last_focus = next((b for row in self.rows for b in row if b.sel_key == key), None)
        if self.last_focus:
            self.last_focus.grab_focus()

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
