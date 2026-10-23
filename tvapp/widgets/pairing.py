"""Pairing UI: a compact glass status line (Wi-Fi · IP · power) in the top-right, a glass panel that drops down
from it while no phone remote is connected, and the full-size pairing / power-confirm sheets.

The sheets are in-window overlays handled by the shell's normal key routing (Home.open_sheet), never separate
Gtk.Dialog windows: the phone remote sends keys to the Home window, which a modal dialog would swallow."""
import math
import sys
import threading
from pathlib import Path

import gi
gi.require_version("Gtk", "3.0")
from gi.repository import Gtk, Gdk, GLib

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import screens  # noqa: E402
from screens import text_label, cmd  # noqa: E402
from . import Widget, register  # noqa: E402
from .netinfo import (get_network_info, current_code, fmt_code, paired_phones, net_line,  # noqa: E402
                      qr_matrix, remote_connected)

try:
    import cairo
except Exception:  # pragma: no cover
    cairo = None

QUIET = 4        # quiet-zone width in modules


class QRTile(Gtk.DrawingArea):
    """A QR code on glass. style "glass": frosted dark tile with white modules (~90 %), crisp, never blurred.
    style "light": frosted light tile with dark modules (the fallback if a phone camera can't read inverted codes)."""

    def __init__(self, shell, module_px, style="glass"):
        super().__init__()
        self.shell, self.style = shell, style
        self.module = max(2, module_px)
        self.matrix = None
        self.set_halign(Gtk.Align.CENTER)
        self.connect("draw", self.on_draw)

    def set_text(self, text):
        self.matrix = qr_matrix(text)
        n = len(self.matrix) + 2 * QUIET if self.matrix else 29
        side = n * self.module
        self.set_size_request(side, side)
        self.queue_draw()

    def draw_tile(self, cr, side, matrix, style, module, frost=None):
        """Pure drawing, also used to render test images."""
        r = side * 0.06
        cr.new_sub_path()
        for cx, cy, a in ((side - r, r, -90), (side - r, side - r, 0), (r, side - r, 90), (r, r, 180)):
            cr.arc(cx, cy, r, math.radians(a), math.radians(a + 90))
        cr.close_path()
        cr.save()
        cr.clip_preserve()
        if frost:
            frost(cr)
        if style == "light":
            cr.set_source_rgba(1, 1, 1, 0.86)
        else:
            cr.set_source_rgba(0.04, 0.06, 0.11, 0.62)
        cr.paint()
        cr.restore()
        cr.set_source_rgba(1, 1, 1, 0.30)
        cr.set_line_width(1)
        cr.stroke()
        if not matrix:
            return
        cr.set_antialias(cairo.ANTIALIAS_NONE)       # modules stay hard-edged: no blur or smoothing on them
        if style == "light":
            cr.set_source_rgba(0.04, 0.06, 0.1, 0.95)
        else:
            cr.set_source_rgba(1, 1, 1, 0.92)
        for y, row in enumerate(matrix):
            for x, dark in enumerate(row):
                if dark:
                    cr.rectangle((x + QUIET) * module, (y + QUIET) * module, module, module)
        cr.fill()

    def on_draw(self, _w, cr):
        if cairo is None:
            return False
        photo = getattr(self.shell, "photo_canvas", None)
        base = cr.get_matrix()
        side = self.get_allocated_width()

        def frost(c):
            from .network import paint_frost
            paint_frost(c, base, self, photo)
        self.draw_tile(cr, side, self.matrix, self.style, self.module, frost)
        return False


def qr_style(shell):
    return (getattr(shell, "home_cfg", None) or {}).get("qr_style", "glass")


# ---------------------------------------------------------------- sheets (in-window overlays)

def _glass_button(label, primary=False):
    b = Gtk.Button(label=label)
    b.get_style_context().add_class("ah-dbtn")
    if primary:
        b.get_style_context().add_class("ah-dbtn-hot")
    return b


def show_pairing_sheet(shell):
    """Full-size pairing view. Closes with Back / Escape / Done / the remote's Home, and by itself when a phone
    connects."""
    p = shell.px
    box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=p(12))
    box.get_style_context().add_class("ah-sheet")
    title = text_label("Connect your phone", "ah-dtitle")
    sub = text_label("Point your camera at the code, or open the address below.", "ah-dsub")
    qr = QRTile(shell, p(11), qr_style(shell))
    code_lbl = text_label("", "ah-dcode")
    addr = text_label("", "ah-daddr")
    note = text_label("The code refreshes every five minutes.", "ah-dsub")
    done = _glass_button("Done", True)
    done.set_halign(Gtk.Align.CENTER)
    done.connect("clicked", lambda *_: shell.close_sheet())
    for w in (title, sub, code_lbl, addr, note):
        w.set_halign(Gtk.Align.CENTER)
    for w in (title, sub, qr, code_lbl, addr, note):
        box.pack_start(w, False, False, 0)
    box.pack_start(done, False, False, p(10))

    def refresh():
        net, code = shell.net or {}, shell.code or "------"
        ip = net.get("ip", "127.0.0.1")
        qr.set_text(f"http://{ip}:8765/?pair={code}")
        code_lbl.set_text(fmt_code(code))
        addr.set_text(f"{ip}:8765")
    refresh()
    shell.open_sheet("pairing", box, [[done]], default=done, refresh=refresh)


def show_power_sheet(shell):
    """Turn-off confirmation; Cancel has the focus."""
    p = shell.px
    box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=p(16))
    box.get_style_context().add_class("ah-sheet")
    title = text_label("Turn off the iMac?", "ah-dtitle")
    sub = text_label("To use the TV again, press its physical power button.", "ah-dsub")
    cancel, off = _glass_button("Cancel", True), _glass_button("Turn off")
    cancel.connect("clicked", lambda *_: shell.close_sheet())

    def do_off(*_):
        shell.close_sheet()
        threading.Thread(target=lambda: cmd("poweroff", {"confirm": True}), daemon=True).start()
    off.connect("clicked", do_off)
    row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=p(20))
    row.set_halign(Gtk.Align.CENTER)
    row.pack_start(cancel, False, False, 0)
    row.pack_start(off, False, False, 0)
    for w in (title, sub):
        w.set_halign(Gtk.Align.CENTER)
        box.pack_start(w, False, False, 0)
    box.pack_start(row, False, False, p(8))
    shell.open_sheet("power", box, [[cancel, off]], default=cancel)


# kept for callers that used the old dialog helpers
def show_dialog(shell):
    show_pairing_sheet(shell)


def confirm_off(shell):
    show_power_sheet(shell)


# ---------------------------------------------------------------- the top-right widget

@register
class PairingWidget(Widget):
    id = "pairing"
    slot = "topright"
    interval = 4
    order = 0

    def build(self):
        p = self.shell.px
        shell = self.shell
        self.last_url = ""
        self.panel_on = False
        outer = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=p(10))
        outer.set_halign(Gtk.Align.END)
        # compact status line
        card = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=p(4))
        card.get_style_context().add_class("ah-card")
        card.get_style_context().add_class("ah-chip")
        card.set_halign(Gtk.Align.END)
        self.pair_btn = Gtk.Button()
        self.pair_btn.set_relief(Gtk.ReliefStyle.NONE)
        self.pair_btn.get_style_context().add_class("ah-cardbtn")
        self.pair_btn.connect("clicked", lambda *_: show_pairing_sheet(shell))
        self.net_lbl = text_label("", "ah-net")
        self.pair_btn.add(self.net_lbl)
        self.power_btn = Gtk.Button(label="⏻")
        self.power_btn.set_relief(Gtk.ReliefStyle.NONE)
        self.power_btn.get_style_context().add_class("ah-power")
        self.power_btn.connect("clicked", lambda *_: show_power_sheet(shell))
        card.pack_start(self.pair_btn, False, False, 0)
        card.pack_start(self.power_btn, False, False, 0)
        self.card = card
        # drop-down panel
        self.rev = Gtk.Revealer()
        self.rev.set_transition_type(Gtk.RevealerTransitionType.SLIDE_DOWN)
        self.rev.set_transition_duration(300)
        panel = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=p(8))
        panel.get_style_context().add_class("ah-card")
        panel.get_style_context().add_class("ah-drop")
        panel.set_halign(Gtk.Align.END)
        self.drop_title = text_label("Scan to connect your phone", "ah-kicker")
        panel.set_size_request(p(320), -1)
        self.qr = QRTile(shell, p(5), qr_style(shell))
        self.code_lbl = text_label("--- ---", "ah-code")
        self.code_lbl.set_halign(Gtk.Align.CENTER)
        self.drop_title.set_halign(Gtk.Align.CENTER)
        for w in (self.drop_title, self.qr, self.code_lbl):
            panel.pack_start(w, False, False, 0)
        self.panel = panel
        self.rev.add(panel)
        self.rev.set_reveal_child(False)
        outer.pack_start(card, False, False, 0)
        outer.pack_start(self.rev, False, False, 0)
        self.outer = outer
        shell.pair_listeners.append(self.render)
        self.render()
        return outer

    def focus_items(self):
        return [self.pair_btn, self.power_btn]

    def link_point(self, photo):
        """Centre of the status line in canvas coordinates + whether it holds focus (the line is the network node)."""
        w = self.card.get_allocated_width()
        pos = self.card.translate_coordinates(photo, w // 2, self.card.get_allocated_height() // 2)
        if not pos or w < 2:
            return None
        return pos[0], pos[1], self.pair_btn.has_focus() or self.power_btn.has_focus()

    def drop_rect(self):
        """Window rect (x, y, w, h) the open drop-down will occupy, or None while it is retracted. Other widgets
        keep clear of it."""
        if not self.panel_on:
            return None
        pos = self.outer.translate_coordinates(self.shell, 0, 0)
        ow = self.outer.get_allocated_width()
        if not pos or ow < 2:
            return None
        pw = self.panel.get_preferred_width()[1]
        ph = self.panel.get_preferred_height()[1]
        ch = self.card.get_allocated_height()
        return (pos[0] + ow - pw, pos[1], pw, ch + self.shell.px(10) + ph)

    def refresh(self):
        self.shell.trigger_network_refresh()

    def render(self, *_):
        sh = self.shell
        net, code = sh.net or {}, sh.code or "------"
        ip = net.get("ip", "127.0.0.1")
        self.net_lbl.set_text(f"{net_line(net)}  ·  {ip}")
        self.code_lbl.set_text(fmt_code(code))
        url = f"http://{ip}:8765/?pair={code}"
        if url != self.last_url:
            self.last_url = url
            self.qr.set_text(url)
        connected = remote_connected(sh.remote, paired_phones)
        want = (not connected) and sh.prev_app in (None, "home") and sh.net is not None
        if want != self.panel_on:
            self.panel_on = want
            self.fade(want)
        return False

    def fade(self, show):
        """Slide (revealer) + fade (opacity) over ~300 ms."""
        if show:
            self.panel.set_opacity(0.0)
        self.rev.set_reveal_child(show)
        t0 = GLib.get_monotonic_time()

        def step():
            k = min(1.0, (GLib.get_monotonic_time() - t0) / 300000.0)
            self.panel.set_opacity(k if show else 1 - k)
            return k < 1.0
        GLib.timeout_add(33, step)
