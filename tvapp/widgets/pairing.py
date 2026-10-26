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


TINT = 0.11          # white tint of the frosted glass
TARGET_BG = 0.30     # background luminance (0..1) the QR area is darkened down to, at most
MAX_DARK = 0.8


def lum(r, g, b):
    return (0.2126 * r + 0.7152 * g + 0.0722 * b) / 255.0


def region_luminance(surf, x, y, w, h):
    """Mean luminance (0..1) of a rectangle of an RGB24 cairo surface (clamped to its bounds)."""
    sw, sh = surf.get_width(), surf.get_height()
    x0, y0 = max(0, int(x)), max(0, int(y))
    x1, y1 = min(sw, int(x + w) + 1), min(sh, int(y + h) + 1)
    if x1 <= x0 or y1 <= y0:
        return 0.3
    surf.flush()
    data, stride = surf.get_data(), surf.get_stride()
    tot = n = 0
    for yy in range(y0, y1):
        row = yy * stride
        for xx in range(x0, x1):
            o = row + xx * 4
            tot += lum(data[o + 2], data[o + 1], data[o])
            n += 1
    return tot / n if n else 0.3


def darkening_for(bg_lum, tint=TINT, target=TARGET_BG):
    """Least black overlay (alpha) that brings the glass behind the QR down to `target` luminance. Dark photos
    need almost none, bright ones more."""
    seen = bg_lum * (1 - tint) + tint          # what the white glass tint does to the blurred photo
    if seen <= target:
        return 0.0
    return min(MAX_DARK, 1.0 - target / seen)


def rrect_path(cr, x, y, w, h, r):
    r = min(r, w / 2, h / 2)
    cr.new_sub_path()
    cr.arc(x + w - r, y + r, r, -math.pi / 2, 0)
    cr.arc(x + w - r, y + h - r, r, 0, math.pi / 2)
    cr.arc(x + r, y + h - r, r, math.pi / 2, math.pi)
    cr.arc(x + r, y + r, r, math.pi, 1.5 * math.pi)
    cr.close_path()


def paint_glass(cr, widget, photo, w, h, radius, base=None):
    """Frosted glass with a light tint: blurred-photo crop + white tint + hairline border."""
    from .network import paint_frost
    rrect_path(cr, 0.5, 0.5, w - 1, h - 1, radius)
    cr.save()
    cr.clip_preserve()
    paint_frost(cr, base or cr.get_matrix(), widget, photo)
    cr.set_source_rgba(1, 1, 1, TINT)
    cr.paint()
    cr.restore()
    cr.set_source_rgba(1, 1, 1, 0.28)
    cr.set_line_width(1)
    cr.stroke()


def paint_feathered_dark(cr, x, y, w, h, alpha, feather):
    """A soft-edged dark patch (no hard box): nested rounded rects, each adding a little, so the darkness ramps up
    smoothly from `feather` px outside the area to full `alpha` just inside it."""
    if alpha <= 0.004:
        return
    steps = 16
    per = 1.0 - (1.0 - alpha) ** (1.0 / steps)
    inset = feather * 0.35
    for i in range(steps):
        g = feather * (1 - i / (steps - 1)) - inset
        rrect_path(cr, x - g, y - g, w + 2 * g, h + 2 * g, max(4.0, feather * 0.9 + g))
        cr.set_source_rgba(0, 0, 0, per)
        cr.fill()


class QRTile(Gtk.DrawingArea):
    """A QR code sitting directly on the glass: white modules (~92 %), crisp, never blurred. Behind the code
    and its quiet zone only, the glass is darkened just enough (measured per photo) to keep it scannable.
    style "light" instead draws dark modules on a frosted light tile (fallback for cameras that can't read
    inverted codes)."""

    def __init__(self, shell, module_px, style="glass", photo=None):
        super().__init__()
        self.shell, self.style, self.photo = shell, style, photo
        self.module = max(2, module_px)
        self.matrix = None
        self.dark_cache = (None, 0.0)
        self.pad = 0
        self.set_halign(Gtk.Align.CENTER)
        self.set_has_window(False)
        self.connect("draw", self.on_draw)

    def set_text(self, text):
        self.matrix = qr_matrix(text)
        n = len(self.matrix) + 2 * QUIET if self.matrix else 29
        side = n * self.module
        self.pad = self.shell.px(24)          # room for the feathered contrast patch (a widget clips its drawing)
        self.set_size_request(side + 2 * self.pad, side + 2 * self.pad)
        self.queue_draw()

    def darkening(self, photo, ox, oy, side):
        """Alpha of the contrast patch for the photo currently under the QR (cached per photo + position)."""
        layers = photo.blur_layers() if photo else []
        if not layers:
            return 0.25
        key = (tuple(id(s) for s, _ in layers), round(ox), round(oy))
        if self.dark_cache[0] == key:
            return self.dark_cache[1]
        k = photo.blur_k
        m = 10
        worst = max(region_luminance(s, (ox - m) / k, (oy - m) / k, (side + 2 * m) / k, (side + 2 * m) / k)
                    for s, _ in layers)
        a = darkening_for(worst)
        self.dark_cache = (key, a)
        return a

    def paint_qr(self, cr, side, photo, origin):
        """Drawing shared with the test renderer. origin = QR top-left in photo (screen) coordinates."""
        matrix, module = self.matrix, self.module
        if self.style == "light":
            rrect_path(cr, 0, 0, side, side, side * 0.06)
            cr.set_source_rgba(1, 1, 1, 0.80)
            cr.fill()
            cr.set_source_rgba(0.04, 0.06, 0.1, 0.95)
        else:
            paint_feathered_dark(cr, 0, 0, side, side, self.darkening(photo, origin[0], origin[1], side),
                                 self.shell.px(30))
            cr.set_source_rgba(1, 1, 1, 0.93)
        if not matrix:
            return
        cr.set_antialias(cairo.ANTIALIAS_NONE)
        for y, row in enumerate(matrix):
            for x, dark in enumerate(row):
                if dark:
                    cr.rectangle((x + QUIET) * module, (y + QUIET) * module, module, module)
        cr.fill()
        cr.set_antialias(cairo.ANTIALIAS_DEFAULT)

    def on_draw(self, _w, cr):
        if cairo is None:
            return False
        photo = self.photo or getattr(self.shell, "photo_canvas", None)
        pad = getattr(self, "pad", 0)
        pos = self.translate_coordinates(photo, pad, pad) if photo else None
        cr.translate(pad, pad)
        self.paint_qr(cr, self.get_allocated_width() - 2 * pad, photo, pos or (0, 0))
        return False


class GlassBox(Gtk.Box):
    """A box that paints frosted light glass (blurred photo crop + faint white tint + hairline) behind its children."""

    def __init__(self, shell, *a, photo=None, **kw):
        super().__init__(*a, **kw)
        self.shell, self.photo = shell, photo
        self.set_has_window(False)

    def do_draw(self, cr):
        if cairo is not None:
            w, h = self.get_allocated_width(), self.get_allocated_height()
            paint_glass(cr, self, self.photo or getattr(self.shell, "photo_canvas", None), w, h,
                        min(self.shell.px(30), h / 2))
        return Gtk.Box.do_draw(self, cr)


def qr_style(shell):
    return (getattr(shell, "home_cfg", None) or {}).get("qr_style", "glass")


def qr_card(shell, module_px=None, photo=None):
    """Home's glass QR drop-down as a stand-alone card for other screens: the same GlassBox, kicker, QRTile and
    code. photo = the frost source (anything with blur_layers()/blur_k/transparency; default Home's photo).
    -> (card, refresh) where refresh() re-reads shell.net / shell.code."""
    p = shell.px
    card = GlassBox(shell, orientation=Gtk.Orientation.VERTICAL, spacing=p(8), photo=photo)
    card.get_style_context().add_class("ah-drop")
    kicker = text_label("Scan to connect your phone", "ah-kicker")
    kicker.set_halign(Gtk.Align.CENTER)
    kicker.set_margin_top(p(14))
    qr = QRTile(shell, module_px or p(5), qr_style(shell), photo=photo)
    code = text_label("--- ---", "ah-code")
    code.set_halign(Gtk.Align.CENTER)
    for w in (kicker, qr, code):
        card.pack_start(w, False, False, 0)
    last = [None]

    def refresh(*_):
        net, c = getattr(shell, "net", None) or {}, getattr(shell, "code", None) or "------"
        url = f"http://{net.get('ip', '127.0.0.1')}:8765/?pair={c}"
        code.set_text(fmt_code(c))
        if url != last[0]:
            last[0] = url
            qr.set_text(url)
        return False
    refresh()
    return card, refresh


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
    # Home keeps its photo-backed glass presentation. Content screens use the
    # same QR renderer in a quieter horizontal layout.
    content_screen = getattr(shell.current(), 'sname', 'home') != 'home'
    box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=p(12))
    box.get_style_context().add_class("ah-sheet")
    if content_screen:
        box.get_style_context().add_class('pair-sheet')
    title = text_label("Connect your phone", "ah-dtitle")
    sub = text_label("Point your camera at the code, or open the address below.", "ah-dsub")
    qr = QRTile(shell, p(7) if content_screen else p(11), qr_style(shell))
    code_lbl = text_label("", "ah-dcode")
    addr = text_label("", "ah-daddr")
    note = text_label("The code refreshes every five minutes.", "ah-dsub")
    done = _glass_button("Done", True)
    done.set_halign(Gtk.Align.CENTER)
    done.connect("clicked", lambda *_: shell.close_sheet())
    if content_screen:
        row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=p(40))
        copy = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=p(18))
        copy.set_valign(Gtk.Align.CENTER)
        sub.set_text('Scan with your camera.\nKeep your phone on the same Wi-Fi as your TV.')
        sub.set_line_wrap(True)
        sub.set_max_width_chars(38)
        note.set_text('Or open the address and enter this code.')
        note.set_max_width_chars(38)
        note.set_line_wrap(True)
        for w in (title, sub, note, addr, code_lbl, done):
            w.set_halign(Gtk.Align.START)
            copy.pack_start(w, False, False, 0)
        row.pack_start(qr, False, False, 0)
        row.pack_start(copy, False, False, 0)
        box.pack_start(row, False, False, 0)
    else:
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
        outer = GlassBox(shell, orientation=Gtk.Orientation.VERTICAL, spacing=0)
        outer.set_halign(Gtk.Align.END)
        # compact status line
        card = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=p(4))
        card.get_style_context().add_class("ah-line")
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
        panel.get_style_context().add_class("ah-drop")
        panel.set_halign(Gtk.Align.CENTER)
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
        return (pos[0] + ow - pw, pos[1], pw, ch + ph)

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
