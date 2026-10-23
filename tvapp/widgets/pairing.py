"""Glass pairing card (QR + code + Wi-Fi + power) that shrinks to a chip once a phone is paired, and the full dialog."""
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
from .netinfo import get_network_info, make_qr_pixbuf, current_code, fmt_code, paired_phones, net_line  # noqa: E402


def confirm_off(shell):
    dialog = Gtk.MessageDialog(transient_for=shell, modal=True, message_type=Gtk.MessageType.QUESTION,
                               buttons=Gtk.ButtonsType.NONE, text="Turn off the iMac?")
    dialog.format_secondary_text("To use the TV again, press its physical power button.")
    dialog.add_buttons("Cancel", Gtk.ResponseType.CANCEL, "Turn off", Gtk.ResponseType.OK)
    shell.modal_open = True
    answer = dialog.run()
    shell.modal_open = False
    if answer == Gtk.ResponseType.OK:
        threading.Thread(target=lambda: cmd("poweroff", {"confirm": True}), daemon=True).start()
    dialog.destroy()


def show_dialog(shell):
    """Full-size pairing dialog in the glass look: big QR, code, address."""
    p = shell.px
    code = current_code()
    ip = get_network_info().get("ip", "127.0.0.1")
    url = f"http://{ip}:8765/?pair={code}"
    dialog = Gtk.Dialog(title="Connect your phone", transient_for=shell, modal=True)
    dialog.set_decorated(False)
    dialog.get_style_context().add_class("ah-dialog")
    area = dialog.get_content_area()
    area.set_spacing(p(14))
    for side in ("start", "end"):
        getattr(area, f"set_margin_{side}")(p(70))
    area.set_margin_top(p(48))
    area.set_margin_bottom(p(44))

    def centred(widget):
        widget.set_halign(Gtk.Align.CENTER)
        return widget
    area.pack_start(centred(text_label("Connect your phone", "ah-dtitle")), False, False, 0)
    area.pack_start(centred(text_label("Point your camera at the code, or open the address below.", "ah-dsub")),
                    False, False, 0)
    qr = make_qr_pixbuf(url, p(340))
    frame = Gtk.EventBox()
    frame.get_style_context().add_class("ah-qr-frame")
    frame.set_halign(Gtk.Align.CENTER)
    frame.add(Gtk.Image.new_from_pixbuf(qr) if qr else Gtk.Label(label="QR unavailable"))
    area.pack_start(frame, False, False, p(10))
    area.pack_start(centred(text_label(fmt_code(code), "ah-dcode")), False, False, 0)
    area.pack_start(centred(text_label(f"{ip}:8765", "ah-daddr")), False, False, 0)
    area.pack_start(centred(text_label("The code refreshes every five minutes.", "ah-dsub")), False, False, 0)
    close = Gtk.Button(label="Done")
    close.get_style_context().add_class("ah-dbtn")
    close.set_halign(Gtk.Align.CENTER)
    close.connect("clicked", lambda *_: dialog.response(Gtk.ResponseType.CLOSE))
    area.pack_start(close, False, False, p(12))
    area.show_all()
    close.grab_focus()
    dialog.connect("key-press-event", lambda _w, e: dialog.response(Gtk.ResponseType.CLOSE)
                   if Gdk.keyval_name(e.keyval) in ("Escape", "BackSpace", "XF86Back") else False)
    shell.modal_open = True
    dialog.run()
    shell.modal_open = False
    dialog.destroy()
    shell.ensure_focus()


@register
class PairingWidget(Widget):
    id = "pairing"
    slot = "topright"
    interval = 4
    order = 0

    def build(self):
        p = self.shell.px
        self.net, self.code, self.phones = {}, "------", 0
        self.last_url = ""
        card = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=p(10))
        card.get_style_context().add_class("ah-card")
        self.pair_btn = Gtk.Button()
        self.pair_btn.set_relief(Gtk.ReliefStyle.NONE)
        self.pair_btn.get_style_context().add_class("ah-cardbtn")
        self.pair_btn.connect("clicked", lambda *_: show_dialog(self.shell))
        inner = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=p(16))
        self.qr_frame = Gtk.EventBox()
        self.qr_frame.get_style_context().add_class("ah-qr-frame")
        self.qr = Gtk.Image()
        self.qr.set_size_request(p(104), p(104))
        self.qr_frame.add(self.qr)
        self.qr_frame.set_valign(Gtk.Align.CENTER)
        col = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=p(2))
        col.set_valign(Gtk.Align.CENTER)
        self.kicker = text_label("Scan to pair your phone", "ah-kicker")
        self.code_lbl = text_label("--- ---", "ah-code")
        self.net_lbl = text_label("", "ah-net")
        for w in (self.kicker, self.code_lbl, self.net_lbl):
            col.pack_start(w, False, False, 0)
        inner.pack_start(self.qr_frame, False, False, 0)
        inner.pack_start(col, False, False, 0)
        self.pair_btn.add(inner)
        self.power_btn = Gtk.Button(label="⏻")
        self.power_btn.set_relief(Gtk.ReliefStyle.NONE)
        self.power_btn.get_style_context().add_class("ah-power")
        self.power_btn.set_valign(Gtk.Align.CENTER)
        self.power_btn.connect("clicked", lambda *_: confirm_off(self.shell))
        card.pack_start(self.pair_btn, False, False, 0)
        card.pack_start(self.power_btn, False, False, 0)
        self.card = card
        self.full_widgets = (self.qr_frame, self.kicker, self.code_lbl)
        self.render()
        return card

    def focus_items(self):
        return [self.pair_btn, self.power_btn]

    def link_point(self, photo):
        """Centre of the card in canvas coordinates + whether it holds focus (the card is a network node too)."""
        pos = self.card.translate_coordinates(photo, self.card.get_allocated_width() // 2,
                                              self.card.get_allocated_height() // 2)
        if not pos or self.card.get_allocated_width() < 2:
            return None
        return pos[0], pos[1], self.pair_btn.has_focus() or self.power_btn.has_focus()

    def refresh(self):
        def worker():
            net, code, phones = get_network_info(), current_code(), paired_phones()
            GLib.idle_add(self.apply, net, code, phones)
        threading.Thread(target=worker, daemon=True).start()

    def apply(self, net, code, phones):
        self.net, self.code, self.phones = net, code, phones
        self.render()
        return False

    def render(self):
        chip = self.phones >= 1
        ctx = self.card.get_style_context()
        (ctx.add_class if chip else ctx.remove_class)("ah-chip")
        for w in self.full_widgets:
            w.set_visible(not chip)
            if not chip:
                w.show_all()
        self.code_lbl.set_text(fmt_code(self.code))
        ip = self.net.get("ip", "127.0.0.1")
        self.net_lbl.set_text(net_line(self.net) if chip else f"{net_line(self.net)}   ·   {ip}")
        url = f"http://{ip}:8765/?pair={self.code}"
        if url != self.last_url and not chip:
            self.last_url = url
            pb = make_qr_pixbuf(url, self.shell.px(104))
            if pb:
                self.qr.set_from_pixbuf(pb)
