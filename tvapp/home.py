#!/usr/bin/env python3
"""Artwork-led, remote-friendly TV Home for iMac TV."""
import datetime
import fcntl
import io
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import threading
import time
import urllib.request

import gi
gi.require_version("Gtk", "3.0")
gi.require_version("GdkPixbuf", "2.0")
from gi.repository import Gtk, Gdk, GdkPixbuf, GLib

try:
    import qrcode
except ImportError:
    qrcode = None

BASE = Path(__file__).resolve().parent
DATA = Path(os.environ.get("IMACTV_DATA_DIR", str(Path.home() / ".local/share/imactv")))
ASSETS = BASE / "assets"
ICONS = ASSETS / "app-icons"


def build_css(scale=1.0):
    return f"""
window {{ background: #000; color: #f5f6f8; font-family: Sans; }}
.page {{ background: #000; }}
.brand {{ font-size: {round(32 * scale)}px; font-weight: 900; letter-spacing: 2px; color: #fff; }}
.header-note {{ font-size: {round(24 * scale)}px; font-weight: 600; color: #d0d4dc; }}
.nav-button {{ background: transparent; border: 0; border-bottom: {round(4 * scale)}px solid transparent; border-radius: 0; color: #bdc1c9; font-size: {round(22 * scale)}px; font-weight: 700; padding: {round(6 * scale)}px {round(8 * scale)}px {round(12 * scale)}px; }}
.nav-button:focus, .nav-current {{ color: #fff; border-bottom-color: #fff; font-weight: 800; }}
.conn-pill {{ background: rgba(22, 25, 34, 0.92); border: {round(2 * scale)}px solid rgba(255, 255, 255, 0.22); border-radius: {round(16 * scale)}px; padding: {round(6 * scale)}px {round(14 * scale)}px; color: #fff; }}
.conn-pill:focus {{ border-color: #60a5fa; background: rgba(35, 43, 60, 0.98); box-shadow: 0 0 0 {round(3 * scale)}px rgba(96, 165, 250, 0.45); }}
.conn-wifi {{ font-size: {round(19 * scale)}px; font-weight: 700; color: #4ade80; }}
.conn-wifi-off {{ font-size: {round(19 * scale)}px; font-weight: 700; color: #f87171; }}
.conn-ip {{ font-size: {round(18 * scale)}px; font-weight: 600; color: #cbd5e1; }}
.conn-code {{ font-size: {round(22 * scale)}px; font-weight: 900; color: #facc15; font-family: monospace; letter-spacing: 1.5px; }}
.conn-qr-frame {{ background: #ffffff; border-radius: {round(8 * scale)}px; padding: {round(3 * scale)}px; }}
.conn-btn {{ background: rgba(35, 40, 52, 0.92); border: {round(2 * scale)}px solid rgba(255, 255, 255, 0.35); border-radius: {round(14 * scale)}px; color: #ffffff; font-size: {round(20 * scale)}px; font-weight: 700; padding: {round(10 * scale)}px {round(18 * scale)}px; }}
.conn-btn:focus {{ background: #2563eb; border-color: #ffffff; box-shadow: 0 0 0 {round(3 * scale)}px rgba(37, 99, 235, 0.5); }}
.hero-fade {{ background-image: linear-gradient(90deg, rgba(0,0,0,0.98) 0%, rgba(0,0,0,0.85) 32%, rgba(0,0,0,0.40) 58%, rgba(0,0,0,0.10) 100%); }}
.hero-bottom-fade {{ background-image: linear-gradient(0deg, rgba(0,0,0,0.95), rgba(0,0,0,0)); }}
.hero-kicker {{ font-size: {round(20 * scale)}px; font-weight: 800; letter-spacing: 2px; color: #f7c968; }}
.hero-title {{ font-size: {round(68 * scale)}px; font-weight: 900; color: #fff; }}
.hero-copy {{ font-size: {round(25 * scale)}px; color: #e2e3e7; }}
.hero-primary {{ background: #fff; border: {round(2 * scale)}px solid #fff; border-radius: {round(14 * scale)}px; color: #111; font-size: {round(23 * scale)}px; font-weight: 800; padding: {round(14 * scale)}px {round(28 * scale)}px; }}
.hero-primary:focus {{ box-shadow: 0 0 0 {round(4 * scale)}px rgba(255,255,255,0.5); }}
.hero-secondary {{ background: rgba(30,32,38,0.88); border: {round(2 * scale)}px solid rgba(255,255,255,0.4); border-radius: {round(14 * scale)}px; color: #fff; font-size: {round(23 * scale)}px; font-weight: 700; padding: {round(14 * scale)}px {round(26 * scale)}px; }}
.hero-secondary:focus {{ background: #363941; border-color: #fff; }}
.app-button {{ background: rgba(29,30,36,0.88); border: {round(2 * scale)}px solid rgba(255,255,255,0.24); border-radius: {round(18 * scale)}px; color: #fff; padding: {round(14 * scale)}px {round(22 * scale)}px; }}
.app-button:focus {{ background: rgba(57,59,67,0.98); border-color: #fff; box-shadow: 0 0 0 {round(3 * scale)}px rgba(255,255,255,0.35); }}
.app-name {{ font-size: {round(24 * scale)}px; font-weight: 800; color: #fff; }}
.app-heading {{ font-size: {round(18 * scale)}px; font-weight: 800; letter-spacing: 1.5px; color: #d7d9df; }}
.section-title {{ font-size: {round(34 * scale)}px; font-weight: 800; color: #f3f4f6; }}
.section-note {{ font-size: {round(18 * scale)}px; color: #aab0ba; }}
.feed-card {{ background: #16171a; border: {round(3 * scale)}px solid transparent; border-radius: {round(18 * scale)}px; color: #fff; padding: 0; }}
.feed-card:focus {{ border-color: #fff; box-shadow: 0 0 0 {round(4 * scale)}px rgba(255,255,255,0.4); }}
.feed-shade {{ background-image: linear-gradient(0deg, rgba(0,0,0,0.95), rgba(0,0,0,0.68) 65%, rgba(0,0,0,0)); padding: {round(20 * scale)}px {round(18 * scale)}px {round(14 * scale)}px; }}
.feed-title {{ font-size: {round(23 * scale)}px; font-weight: 800; color: #fff; }}
.feed-copy {{ font-size: {round(17 * scale)}px; color: #e1e2e5; }}
.empty-feed {{ background: #151619; border: {round(2 * scale)}px solid #303237; border-radius: {round(16 * scale)}px; color: #b4b7bd; padding: {round(18 * scale)}px {round(24 * scale)}px; }}
.empty-copy {{ font-size: {round(19 * scale)}px; color: #c4c7ce; }}
.status {{ font-size: {round(18 * scale)}px; font-weight: 600; color: #b5d8be; }}
.paircode {{ font-size: {round(18 * scale)}px; font-weight: 600; color: #a4aab4; }}
.qr-dialog {{ background: #11141c; color: #f8fafc; border: {round(3 * scale)}px solid #3b82f6; border-radius: {round(24 * scale)}px; padding: {round(24 * scale)}px; }}
.qr-dialog-title {{ font-size: {round(36 * scale)}px; font-weight: 900; color: #ffffff; }}
.qr-dialog-sub {{ font-size: {round(22 * scale)}px; color: #94a3b8; }}
.qr-dialog-code {{ font-size: {round(46 * scale)}px; font-weight: 900; letter-spacing: 5px; color: #facc15; font-family: monospace; }}
.qr-dialog-url {{ font-size: {round(22 * scale)}px; font-weight: 700; color: #60a5fa; font-family: monospace; }}
.progress trough {{ min-height: {round(6 * scale)}px; background: rgba(255,255,255,0.25); }}
.progress progress {{ min-height: {round(6 * scale)}px; background: #fff; }}
.tv-scrollbar {{ opacity: 0; }}
button {{ outline: none; }}
""".encode()


def get_network_info():
    """Discover active LAN IP, connection type (Wi-Fi/Ethernet), and Wi-Fi signal/SSID."""
    info = {"connected": False, "type": "none", "ssid": "", "signal": 0, "ip": "127.0.0.1"}

    try:
        route_out = subprocess.check_output(["ip", "route", "get", "1.1.1.1"], text=True, timeout=1)
        m = re.search(r"src\s+([0-9\.]+)", route_out)
        if m:
            info["ip"] = m.group(1)
    except Exception:
        pass
    if info["ip"] == "127.0.0.1":
        try:
            ips = subprocess.check_output(["hostname", "-I"], text=True, timeout=1).split()
            for ip in ips:
                if ip.startswith(("192.168.", "10.", "172.")) and not ip.startswith(("172.17.", "172.18.")):
                    info["ip"] = ip
                    break
        except Exception:
            pass

    try:
        wf_out = subprocess.check_output(
            ["nmcli", "-t", "-f", "ACTIVE,SSID,SIGNAL", "device", "wifi", "list", "--rescan", "no"],
            text=True, timeout=1
        )
        for line in wf_out.strip().splitlines():
            if line.startswith("yes:"):
                parts = line.split(":")
                if len(parts) >= 3:
                    info["connected"] = True
                    info["type"] = "wifi"
                    info["ssid"] = parts[1]
                    try:
                        info["signal"] = int(parts[2])
                    except ValueError:
                        info["signal"] = 75
                    break
    except Exception:
        pass

    if info["type"] != "wifi":
        try:
            dev_out = subprocess.check_output(
                ["nmcli", "-t", "-f", "TYPE,STATE", "device"],
                text=True, timeout=1
            )
            for line in dev_out.strip().splitlines():
                if line.startswith("ethernet:connected"):
                    info["connected"] = True
                    info["type"] = "ethernet"
                    break
        except Exception:
            pass

    if info["type"] == "none" and info["ip"] != "127.0.0.1":
        info["connected"] = True

    return info


def make_qr_pixbuf(text, size=320):
    """Generate in-memory GdkPixbuf QR code."""
    if not qrcode:
        return None
    try:
        qr = qrcode.QRCode(box_size=6, border=2)
        qr.add_data(text)
        qr.make(fit=True)
        img = qr.make_image(fill_color="black", back_color="white")
        buf = io.BytesIO()
        img.save(buf, format="PNG")
        loader = GdkPixbuf.PixbufLoader()
        loader.write(buf.getvalue())
        loader.close()
        pb = loader.get_pixbuf()
        if size and (pb.get_width() != size or pb.get_height() != size):
            return pb.scale_simple(size, size, GdkPixbuf.InterpType.NEAREST)
        return pb
    except Exception:
        return None


def local_api(path):
    try:
        token = (DATA / "home_token").read_text().strip()
    except OSError:
        token = ""
    req = urllib.request.Request("http://127.0.0.1:8765" + path,
                                 headers={"Cookie": "imactv=" + token})
    try:
        with urllib.request.urlopen(req, timeout=1.5) as response:
            return json.load(response)
    except Exception:
        return None


def cmd(action, params=None):
    payload = json.dumps({"action": action, "params": params or {}}).encode()
    try:
        token = (DATA / "home_token").read_text().strip()
    except OSError:
        token = ""
    req = urllib.request.Request("http://127.0.0.1:8765/api/commands", data=payload,
                                 headers={"Content-Type": "application/json",
                                          "Cookie": "imactv=" + token}, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=3) as response:
            return json.load(response)
    except Exception:
        return None


def text_label(text, style, wrap=False):
    widget = Gtk.Label(label=text)
    if style:
        widget.get_style_context().add_class(style)
    widget.set_halign(Gtk.Align.START)
    widget.set_xalign(0)
    widget.set_line_wrap(wrap)
    return widget


def cover_pixbuf(path, width, height):
    """Scale artwork to fill a card and crop the overflow without distorting it."""
    source = GdkPixbuf.Pixbuf.new_from_file(str(path))
    scale = max(width / source.get_width(), height / source.get_height())
    scaled_width = max(width, round(source.get_width() * scale))
    scaled_height = max(height, round(source.get_height() * scale))
    scaled = source.scale_simple(scaled_width, scaled_height, GdkPixbuf.InterpType.BILINEAR)
    x = max(0, (scaled_width - width) // 2)
    y = max(0, (scaled_height - height) // 2)
    return scaled.new_subpixbuf(x, y, width, height)


class Home(Gtk.Window):
    def __init__(self):
        super().__init__(title="iMac TV")
        subprocess.run(["xset", "s", "off", "-dpms"], stdout=subprocess.DEVNULL,
                       stderr=subprocess.DEVNULL, check=False)
        self.set_default_size(2560, 1440)
        self.fullscreen()
        self.connect("delete-event", lambda *_: True)
        self.connect("key-press-event", self.on_key_press)
        screen = Gdk.Screen.get_default()
        sw = screen.get_width()
        sh = screen.get_height()
        self.scale = max(1.0, sh / 1080.0)

        provider = Gtk.CssProvider()
        provider.load_from_data(build_css(self.scale))
        Gtk.StyleContext.add_provider_for_screen(screen, provider,
            Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)

        scroll = Gtk.ScrolledWindow()
        scroll.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        scroll.set_overlay_scrolling(True)
        scroll.get_vscrollbar().get_style_context().add_class("tv-scrollbar")
        self.scroll = scroll
        self.add(scroll)
        self.page = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        scroll.add(self.page)

        self.hero_width = sw
        self.hero_height = max(round(520 * self.scale), min(round(760 * self.scale), round(sh * 0.52)))
        self.featured_item = None
        self.default_featured = None

        self.last_qr_text = ""
        self.last_qr_pixbuf = None
        self.last_net_check = 0

        self.make_hero()
        self.feed_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=round(16 * self.scale))
        self.feed_box.set_margin_start(round(68 * self.scale))
        self.feed_box.set_margin_end(round(68 * self.scale))
        self.feed_box.set_margin_top(round(22 * self.scale))
        self.rails = []
        self.page.pack_start(self.feed_box, False, False, 0)
        self.feed_mtime = None
        self.last_state_poll = 0
        self.feed_sync_due = time.monotonic() + 600
        self.state_loading = False
        self.feed_syncing = False
        self.refresh_feeds(force=True)
        self.sync_personal_feed()
        self.make_footer()

        self.show_all()
        self.hero_primary.grab_focus()
        self.trigger_network_refresh()
        GLib.timeout_add_seconds(1, self.tick)

    def make_header(self):
        bar = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=round(20 * self.scale))
        bar.set_hexpand(True)
        bar.pack_start(text_label("IMAC TV", "brand"), False, False, 0)

        self.nav = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=round(18 * self.scale))
        self.nav_buttons = []
        for label, action in (("Home", self.go_home),
                              ("Discover", lambda: self.open_app("stremio")),
                              ("Apps", self.focus_apps)):
            nav_button = Gtk.Button(label=label)
            nav_button.set_relief(Gtk.ReliefStyle.NONE)
            nav_button.get_style_context().add_class("nav-button")
            if label == "Home":
                nav_button.get_style_context().add_class("nav-current")
            nav_button.connect("clicked", lambda _b, fn=action: fn())
            self.nav.pack_start(nav_button, False, False, 0)
            self.nav_buttons.append(nav_button)
        bar.pack_start(self.nav, False, False, round(12 * self.scale))

        right_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=round(16 * self.scale))
        right_box.set_halign(Gtk.Align.END)
        right_box.set_valign(Gtk.Align.CENTER)

        self.wifi_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=round(6 * self.scale))
        self.wifi_box.set_valign(Gtk.Align.CENTER)
        self.wifi_label = text_label("📶 Detecting Wi-Fi…", "conn-wifi")
        self.wifi_box.pack_start(self.wifi_label, False, False, 0)
        right_box.pack_start(self.wifi_box, False, False, round(4 * self.scale))

        self.conn_pill = Gtk.Button()
        self.conn_pill.set_relief(Gtk.ReliefStyle.NONE)
        self.conn_pill.get_style_context().add_class("conn-pill")
        self.conn_pill.connect("clicked", lambda *_: self.show_pairing())

        pill_content = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=round(12 * self.scale))
        pill_content.set_valign(Gtk.Align.CENTER)

        qr_frame = Gtk.EventBox()
        qr_frame.get_style_context().add_class("conn-qr-frame")
        self.qr_image = Gtk.Image()
        qr_thumb_sz = round(68 * self.scale)
        self.qr_image.set_size_request(qr_thumb_sz, qr_thumb_sz)
        qr_frame.add(self.qr_image)
        pill_content.pack_start(qr_frame, False, False, 0)

        info_col = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=round(2 * self.scale))
        info_col.set_valign(Gtk.Align.CENTER)
        self.ip_label = text_label("Connecting…", "conn-ip")
        self.code_label = text_label("Code: ------", "conn-code")
        info_col.pack_start(self.ip_label, False, False, 0)
        info_col.pack_start(self.code_label, False, False, 0)
        pill_content.pack_start(info_col, False, False, 0)

        self.conn_pill.add(pill_content)
        right_box.pack_start(self.conn_pill, False, False, 0)

        self.pair_btn = Gtk.Button(label="📱 Pair / QR")
        self.pair_btn.get_style_context().add_class("conn-btn")
        self.pair_btn.connect("clicked", lambda *_: self.show_pairing())
        right_box.pack_start(self.pair_btn, False, False, 0)

        self.clock = text_label("", "header-note")
        self.clock.set_valign(Gtk.Align.CENTER)
        right_box.pack_start(self.clock, False, False, round(6 * self.scale))

        bar.pack_end(right_box, False, False, 0)
        self.header_buttons = list(self.nav_buttons) + [self.conn_pill, self.pair_btn]
        return bar

    def make_hero(self):
        hero = Gtk.Overlay()
        hero.set_size_request(self.hero_width, self.hero_height)
        hero.set_hexpand(True)
        self.hero_image = Gtk.Image()
        self.hero_image.set_size_request(self.hero_width, self.hero_height)
        hero.add(self.hero_image)

        fade = Gtk.EventBox()
        fade.get_style_context().add_class("hero-fade")
        fade.set_halign(Gtk.Align.FILL)
        fade.set_valign(Gtk.Align.FILL)
        hero.add_overlay(fade)
        bottom_fade = Gtk.EventBox()
        bottom_fade.get_style_context().add_class("hero-bottom-fade")
        bottom_fade.set_halign(Gtk.Align.FILL)
        bottom_fade.set_valign(Gtk.Align.END)
        bottom_fade.set_size_request(-1, round(240 * self.scale))
        hero.add_overlay(bottom_fade)

        body = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        body.set_halign(Gtk.Align.FILL)
        body.set_valign(Gtk.Align.FILL)
        body.set_margin_start(round(76 * self.scale))
        body.set_margin_end(round(76 * self.scale))
        body.set_margin_top(round(34 * self.scale))
        body.set_margin_bottom(round(27 * self.scale))
        body.pack_start(self.make_header(), False, False, 0)
        body.pack_start(Gtk.Box(), True, True, 0)

        feature = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=round(10 * self.scale))
        self.hero_kicker = text_label("YOUR STREMIO HOME", "hero-kicker")
        self.hero_title = text_label("Find something to watch", "hero-title", True)
        self.hero_title.set_max_width_chars(28)
        self.hero_copy = text_label("Your recent titles will appear here.", "hero-copy", True)
        self.hero_copy.set_max_width_chars(65)
        feature.pack_start(self.hero_kicker, False, False, 0)
        feature.pack_start(self.hero_title, False, False, 0)
        feature.pack_start(self.hero_copy, False, False, 0)

        actions = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=round(14 * self.scale))
        self.hero_primary = Gtk.Button(label="  Open Stremio  ")
        self.hero_primary.get_style_context().add_class("hero-primary")
        self.hero_primary.connect("clicked", lambda *_: self.open_featured())
        yt = Gtk.Button(label="  Explore YouTube  ")
        yt.get_style_context().add_class("hero-secondary")
        yt.connect("clicked", lambda *_: self.open_app("youtube"))
        actions.pack_start(self.hero_primary, False, False, 0)
        actions.pack_start(yt, False, False, 0)
        feature.pack_start(actions, False, False, round(14 * self.scale))
        self.hero_buttons = [self.hero_primary, yt]
        body.pack_start(feature, False, False, 0)
        body.pack_start(Gtk.Box(), True, True, 0)
        body.pack_start(self.make_apps(), False, False, 0)
        hero.add_overlay(body)
        self.page.pack_start(hero, False, False, 0)

    def icon_widget(self, app, size=60):
        icon_file = {
            "stremio": ICONS / "stremio.svg",
            "youtube": ICONS / "vacuumtube.svg",
        }.get(app)
        if icon_file and icon_file.exists():
            try:
                pixbuf = GdkPixbuf.Pixbuf.new_from_file_at_scale(str(icon_file), size, size, True)
                return Gtk.Image.new_from_pixbuf(pixbuf)
            except GLib.Error:
                pass
        icon_names = {"vlc": ("vlc", "org.videolan.VLC"), "youtube": ("rocks.shy.VacuumTube",)}
        theme = Gtk.IconTheme.get_default()
        for name in icon_names.get(app, ()):
            try:
                return Gtk.Image.new_from_pixbuf(theme.load_icon(name, size, 0))
            except GLib.Error:
                continue
        return Gtk.Image.new_from_icon_name("applications-multimedia", Gtk.IconSize.DIALOG)

    def make_apps(self):
        shelf = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=round(12 * self.scale))
        shelf.pack_start(text_label("YOUR APPS", "app-heading"), False, False, 0)
        row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=round(16 * self.scale))
        self.app_buttons = []
        app_w = round(290 * self.scale)
        app_h = round(86 * self.scale)
        icon_sz = round(60 * self.scale)
        for title, app in (("Stremio", "stremio"), ("YouTube", "youtube"), ("VLC", "vlc")):
            button = Gtk.Button()
            button.set_relief(Gtk.ReliefStyle.NONE)
            button.get_style_context().add_class("app-button")
            button.set_size_request(app_w, app_h)
            content = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=round(16 * self.scale))
            content.pack_start(self.icon_widget(app, icon_sz), False, False, 0)
            content.pack_start(text_label(title, "app-name"), False, False, 0)
            button.add(content)
            button.connect("clicked", lambda _b, app=app: self.open_app(app))
            row.pack_start(button, False, False, 0)
            self.app_buttons.append(button)
        shelf.pack_start(row, False, False, 0)
        return shelf

    def make_feed_header(self, title, subtitle):
        header = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=round(14 * self.scale))
        header.pack_start(text_label(title, "section-title"), False, False, 0)
        if subtitle:
            header.pack_start(text_label(subtitle, "section-note"), False, False, 0)
        return header

    def feed_art(self, item, width=520, height=292):
        image_path = item.get("image")
        if image_path:
            try:
                return Gtk.Image.new_from_pixbuf(cover_pixbuf(image_path, width, height))
            except (OSError, GLib.Error, TypeError):
                pass
        placeholder = Gtk.EventBox()
        placeholder.set_size_request(width, height)
        return placeholder

    def refresh_feeds(self, force=False):
        """Render feed snapshots supplied by the Stremio and VacuumTube adapters."""
        path = DATA / "recommendations.json"
        try:
            mtime = path.stat().st_mtime
        except OSError:
            mtime = None
        if not force and mtime == self.feed_mtime:
            return
        self.feed_mtime = mtime
        try:
            feeds = json.loads(path.read_text()) if mtime else {}
        except (OSError, ValueError):
            feeds = {}
        if not feeds or not any(feeds.values()):
            sample_path = BASE / "previews/sample-recommendations.json"
            if sample_path.is_file():
                try:
                    feeds = json.loads(sample_path.read_text())
                except Exception:
                    pass
        for child in self.feed_box.get_children():
            self.feed_box.remove(child)
        self.rails = []
        sections = (
            ("Continue watching", "continue_watching", "stremio",
             "Your recent Stremio playback will appear here"),
            ("Recently watched", "recently_watched", "stremio",
             "Your recent Stremio activity will appear here"),
            ("Watch again · Movies", "watch_again", "stremio",
             "Movies from your Stremio history will appear here"),
            ("Picked for you · Stremio", "stremio", "stremio",
             "Recommendations from your Stremio catalogs"),
            ("From your YouTube home", "youtube", "youtube",
             "Sign in to VacuumTube to show your personal YouTube feed here"),
        )
        for title, key, app, empty_copy in sections:
            items = feeds.get(key, [])
            if key != "continue_watching" and not items:
                continue
            self.feed_box.pack_start(self.make_feed_header(title, ""), False, False, 0)
            if items:
                cards = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=round(18 * self.scale))
                buttons = []
                for item in items[:6]:
                    button = self.feed_card(item, app)
                    cards.pack_start(button, False, False, 0)
                    buttons.append(button)
                rail = Gtk.ScrolledWindow()
                rail.set_policy(Gtk.PolicyType.AUTOMATIC, Gtk.PolicyType.NEVER)
                rail.set_overlay_scrolling(True)
                rail.get_hscrollbar().get_style_context().add_class("tv-scrollbar")
                rail.set_size_request(-1, round(305 * self.scale))
                rail.add(cards)
                self.feed_box.pack_start(rail, False, False, 0)
                self.rails.append((buttons, rail, cards))
            else:
                empty = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=round(16 * self.scale))
                empty.get_style_context().add_class("empty-feed")
                empty.set_size_request(round(720 * self.scale), round(84 * self.scale))
                empty.pack_start(self.icon_widget(app, round(40 * self.scale)), False, False, 0)
                empty.pack_start(text_label(empty_copy, "empty-copy", True), False, False, 0)
                self.feed_box.pack_start(empty, False, False, 0)
        self.feed_box.show_all()
        featured = (feeds.get("continue_watching") or feeds.get("recently_watched") or
                    feeds.get("stremio") or feeds.get("youtube") or [])
        if featured:
            self.default_featured = featured[0]
            self.set_featured(featured[0])

    def sync_personal_feed(self):
        if self.feed_syncing:
            return True
        self.feed_syncing = True

        def sync():
            try:
                subprocess.run([sys.executable, str(BASE / "feed_sync.py")],
                               stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                               timeout=45, check=False)
            except (OSError, subprocess.SubprocessError):
                pass
            finally:
                GLib.idle_add(self.finish_feed_sync)
            return False

        threading.Thread(target=sync, daemon=True).start()
        return True

    def finish_feed_sync(self):
        self.feed_syncing = False
        self.refresh_feeds()
        return False

    def feed_card(self, item, fallback_app):
        app = item.get("app", fallback_app)
        card = Gtk.Button()
        card.set_relief(Gtk.ReliefStyle.NONE)
        card_w = round(520 * self.scale)
        card_h = round(292 * self.scale)
        card.set_size_request(card_w, card_h)
        card.get_style_context().add_class("feed-card")
        panel = Gtk.Overlay()
        panel.add(self.feed_art(item, card_w, card_h))
        shade = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=round(5 * self.scale))
        shade.get_style_context().add_class("feed-shade")
        shade.set_halign(Gtk.Align.FILL)
        shade.set_valign(Gtk.Align.END)
        shade.pack_start(text_label(item.get("title", "Untitled"), "feed-title", True),
                         False, False, 0)
        subtitle = item.get("subtitle", "")
        progress = item.get("progress")
        if progress is not None:
            subtitle = f"{subtitle}  ·  {round(float(progress) * 100)}% watched".strip(" ·")
        shade.pack_start(text_label(subtitle, "feed-copy", True), False, False, 0)
        if progress is not None:
            bar = Gtk.ProgressBar()
            bar.set_fraction(max(0.0, min(1.0, float(progress))))
            bar.set_margin_top(round(8 * self.scale))
            shade.pack_start(bar, False, False, 0)
        panel.add_overlay(shade)
        card.add(panel)
        card.connect("clicked", lambda *_: self.open_app(app))
        card.connect("focus-in-event", lambda _button, _event: self.on_card_focus(item))
        return card

    def set_featured(self, item):
        if self.featured_item == item:
            return
        self.featured_item = item
        category = item.get("category")
        watching = category == "continue_watching" or item.get("progress") is not None
        source = item.get("source", "STREMIO")
        label = {"continue_watching": "CONTINUE WATCHING",
                 "recently_watched": "RECENTLY WATCHED",
                 "watch_again": "WATCH AGAIN"}.get(category,
                                              "CONTINUE WATCHING" if watching else "FEATURED")
        self.hero_kicker.set_text(label + "  ·  " + source)
        self.hero_title.set_text(item.get("title") or "Find something to watch")
        self.hero_copy.set_text(item.get("subtitle") or "Open your library to watch")
        self.hero_primary.set_label("  Open Stremio  " if item.get("app") == "stremio"
                                    else "  Open YouTube  ")
        image_path = item.get("image") or ""
        try:
            if Path(image_path).is_file():
                self.hero_image.set_from_pixbuf(
                    cover_pixbuf(image_path, self.hero_width, self.hero_height))
            else:
                self.hero_image.clear()
        except (OSError, GLib.Error, TypeError):
            self.hero_image.clear()

    def on_card_focus(self, item):
        self.set_featured(item)
        return False

    def open_featured(self):
        self.open_app((self.featured_item or {}).get("app", "stremio"))

    def make_footer(self):
        row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=round(20 * self.scale))
        row.set_margin_start(round(68 * self.scale))
        row.set_margin_end(round(68 * self.scale))
        row.set_margin_top(round(26 * self.scale))
        row.set_margin_bottom(round(34 * self.scale))
        self.status = text_label("Ready to watch", "status")
        row.pack_start(self.status, False, False, 0)
        self.pair_label = text_label("", "paircode")
        row.pack_start(self.pair_label, True, True, 0)
        connect = Gtk.Button(label="Pair phone / QR")
        connect.get_style_context().add_class("hero-secondary")
        connect.connect("clicked", lambda *_: self.show_pairing())
        row.pack_end(connect, False, False, 0)
        power = Gtk.Button(label="Power off")
        power.get_style_context().add_class("hero-secondary")
        power.connect("clicked", lambda *_: self.confirm_off())
        row.pack_end(power, False, False, 0)
        self.footer_buttons = [power, connect]
        self.page.pack_start(row, False, False, 0)

    def open_app(self, app):
        names = {"stremio": "Stremio", "youtube": "YouTube", "vlc": "VLC"}
        self.status.set_text("Opening " + names.get(app, app.title()) + "…")
        def launch():
            result = cmd("select", {"app": app})
            GLib.idle_add(self.status.set_text,
                          "Ready to watch" if result else "TV controller is not responding")
            return False
        threading.Thread(target=launch, daemon=True).start()

    def go_home(self):
        self.scroll.get_vadjustment().set_value(0)
        if self.default_featured:
            self.set_featured(self.default_featured)
        self.hero_primary.grab_focus()

    def focus_apps(self):
        if self.app_buttons:
            self.app_buttons[0].grab_focus()

    def focus_groups(self):
        groups = [self.header_buttons, self.hero_buttons, self.app_buttons]
        groups.extend(buttons for buttons, _rail, _cards in self.rails)
        groups.append(self.footer_buttons)
        return [group for group in groups if group]

    def reveal_focus(self, widget):
        position = widget.translate_coordinates(self.page, 0, 0)
        if position:
            _x, y = position
            adjustment = self.scroll.get_vadjustment()
            height = widget.get_allocation().height
            top = adjustment.get_value()
            bottom = top + adjustment.get_page_size()
            if y < top + 40:
                adjustment.set_value(max(0, y - 40))
            elif y + height > bottom - 35:
                adjustment.set_value(y + height - adjustment.get_page_size() + 35)
        for buttons, rail, cards in self.rails:
            if widget not in buttons:
                continue
            position = widget.translate_coordinates(cards, 0, 0)
            if not position:
                return
            x, _y = position
            adjustment = rail.get_hadjustment()
            width = widget.get_allocation().width
            left = adjustment.get_value()
            right = left + adjustment.get_page_size()
            if x < left + 12:
                adjustment.set_value(max(0, x - 12))
            elif x + width > right - 12:
                adjustment.set_value(x + width - adjustment.get_page_size() + 12)
            return

    def on_key_press(self, _window, event):
        key = Gdk.keyval_name(event.keyval)
        if key in ("Escape", "BackSpace", "XF86Back"):
            self.go_home()
            return True
        if key in ("p", "P"):
            self.show_pairing()
            return True
        if key not in ("Left", "Right", "Up", "Down"):
            return False
        groups = self.focus_groups()
        current = self.get_focus()
        location = next(((row, col) for row, group in enumerate(groups)
                         for col, button in enumerate(group) if button is current), None)
        if location is None:
            target = self.hero_primary
        else:
            row, col = location
            if key == "Left":
                target = groups[row][max(0, col - 1)]
            elif key == "Right":
                target = groups[row][min(len(groups[row]) - 1, col + 1)]
            else:
                next_row = max(0, min(len(groups) - 1,
                                      row + (-1 if key == "Up" else 1)))
                target = groups[next_row][min(col, len(groups[next_row]) - 1)]
        target.grab_focus()
        self.reveal_focus(target)
        return True

    def show_pairing(self):
        code = "------"
        try:
            info = json.loads((DATA / "pairing.json").read_text())
            if time.time() < info.get("expires", 0):
                code = str(info.get("code", "------"))
        except (OSError, ValueError, KeyError):
            pass
        if code == "------":
            res = local_api("/api/pair-status")
            if res and "code" in res:
                code = str(res["code"])

        net = get_network_info()
        ip = net.get("ip", "127.0.0.1")
        url = f"http://{ip}:8765/?pair={code}"

        dialog = Gtk.Dialog(title="Connect Phone Remote", transient_for=self, modal=True)
        dialog.set_default_size(round(740 * self.scale), round(800 * self.scale))
        dialog.get_style_context().add_class("qr-dialog")

        content = dialog.get_content_area()
        content.set_spacing(round(16 * self.scale))
        content.set_margin_start(round(36 * self.scale))
        content.set_margin_end(round(36 * self.scale))
        content.set_margin_top(round(26 * self.scale))
        content.set_margin_bottom(round(24 * self.scale))

        title = text_label("Connect Your Phone Remote", "qr-dialog-title")
        title.set_halign(Gtk.Align.CENTER)
        content.pack_start(title, False, False, 0)

        sub = text_label("Scan this QR code with your phone camera to pair instantly without typing.", "qr-dialog-sub")
        sub.set_halign(Gtk.Align.CENTER)
        content.pack_start(sub, False, False, 0)

        qr_pb = make_qr_pixbuf(url, round(360 * self.scale))
        qr_frame = Gtk.EventBox()
        qr_frame.get_style_context().add_class("conn-qr-frame")
        qr_frame.set_halign(Gtk.Align.CENTER)
        if qr_pb:
            qr_img = Gtk.Image.new_from_pixbuf(qr_pb)
        else:
            qr_img = Gtk.Label(label="QR Code")
        qr_frame.add(qr_img)
        content.pack_start(qr_frame, False, False, round(10 * self.scale))

        code_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=round(6 * self.scale))
        code_box.set_halign(Gtk.Align.CENTER)

        formatted_code = f"{code[:3]} {code[3:]}" if len(code) == 6 else code
        code_lbl = text_label(f"Pairing Code:  {formatted_code}", "qr-dialog-code")
        code_lbl.set_halign(Gtk.Align.CENTER)
        code_box.pack_start(code_lbl, False, False, 0)

        url_lbl = text_label(f"Or open browser:  http://{ip}:8765", "qr-dialog-url")
        url_lbl.set_halign(Gtk.Align.CENTER)
        code_box.pack_start(url_lbl, False, False, 0)

        note_lbl = text_label("Rotating security code automatically refreshes every 5 minutes.", "section-note")
        note_lbl.set_halign(Gtk.Align.CENTER)
        code_box.pack_start(note_lbl, False, False, round(4 * self.scale))

        content.pack_start(code_box, False, False, 0)

        close_btn = Gtk.Button(label="Done / Close")
        close_btn.get_style_context().add_class("hero-primary")
        close_btn.set_halign(Gtk.Align.CENTER)
        close_btn.connect("clicked", lambda *_: dialog.response(Gtk.ResponseType.CLOSE))
        content.pack_start(close_btn, False, False, round(10 * self.scale))

        content.show_all()
        close_btn.grab_focus()

        dialog.connect("key-press-event", lambda _w, e: dialog.response(Gtk.ResponseType.CLOSE) if Gdk.keyval_name(e.keyval) in ("Escape", "BackSpace", "XF86Back") else False)

        dialog.run()
        dialog.destroy()

    def confirm_off(self):
        dialog = Gtk.MessageDialog(transient_for=self, modal=True,
                                   message_type=Gtk.MessageType.QUESTION,
                                   buttons=Gtk.ButtonsType.NONE,
                                   text="Turn off the iMac?")
        dialog.format_secondary_text("To use the TV again, press its physical power button.")
        dialog.add_buttons("Cancel", Gtk.ResponseType.CANCEL,
                           "Turn off", Gtk.ResponseType.OK)
        if dialog.run() == Gtk.ResponseType.OK:
            threading.Thread(target=lambda: cmd("poweroff", {"confirm": True}),
                              daemon=True).start()
        dialog.destroy()

    def poll_state(self):
        state = local_api("/api/state")
        GLib.idle_add(self.apply_state, state)

    def apply_state(self, state):
        self.state_loading = False
        if state:
            if state.get("phase") == "opening":
                self.status.set_text("Opening " + state.get("app", "app").title() + "…")
            elif state.get("error"):
                self.status.set_text(state["error"])
            else:
                self.status.set_text("Ready to watch")
        return False

    def trigger_network_refresh(self):
        def worker():
            net = get_network_info()
            code = "------"
            try:
                info = json.loads((DATA / "pairing.json").read_text())
                if time.time() < info.get("expires", 0):
                    code = str(info.get("code", "------"))
            except (OSError, ValueError, KeyError):
                pass
            if code == "------":
                res = local_api("/api/pair-status")
                if res and "code" in res:
                    code = str(res["code"])
            GLib.idle_add(self.update_network_ui, net, code)
        threading.Thread(target=worker, daemon=True).start()

    def update_network_ui(self, net, code):
        if net.get("type") == "wifi" and net.get("connected"):
            ssid = net.get("ssid", "Wi-Fi")
            signal = net.get("signal", 0)
            self.wifi_label.set_text(f"● 📶 {ssid} ({signal}%)")
            self.wifi_label.get_style_context().remove_class("conn-wifi-off")
            self.wifi_label.get_style_context().add_class("conn-wifi")
        elif net.get("type") == "ethernet" and net.get("connected"):
            self.wifi_label.set_text("● 🖧 Wired LAN")
            self.wifi_label.get_style_context().remove_class("conn-wifi-off")
            self.wifi_label.get_style_context().add_class("conn-wifi")
        elif net.get("connected"):
            self.wifi_label.set_text("● 🌐 Connected")
            self.wifi_label.get_style_context().remove_class("conn-wifi-off")
            self.wifi_label.get_style_context().add_class("conn-wifi")
        else:
            self.wifi_label.set_text("● ⚠ Offline")
            self.wifi_label.get_style_context().remove_class("conn-wifi")
            self.wifi_label.get_style_context().add_class("conn-wifi-off")

        ip = net.get("ip", "127.0.0.1")
        self.ip_label.set_text(f"http://{ip}:8765")
        formatted_code = f"{code[:3]} {code[3:]}" if len(code) == 6 else code
        self.code_label.set_text(f"Code: {formatted_code}")

        url = f"http://{ip}:8765/?pair={code}"
        if url != self.last_qr_text:
            self.last_qr_text = url
            pb = make_qr_pixbuf(url, round(68 * self.scale))
            if pb:
                self.qr_image.set_from_pixbuf(pb)

        self.pair_label.set_text(f"Phone remote  ·  {ip}:8765  ·  Pairing code  {formatted_code}")
        return False

    def tick(self):
        self.clock.set_text(datetime.datetime.now().strftime("%a, %b %-d   %H:%M"))
        if time.monotonic() - self.last_net_check >= 4:
            self.last_net_check = time.monotonic()
            self.trigger_network_refresh()

        if not self.state_loading and time.monotonic() - self.last_state_poll >= 5:
            self.last_state_poll = time.monotonic()
            self.state_loading = True
            threading.Thread(target=self.poll_state, daemon=True).start()
        self.refresh_feeds()
        if time.monotonic() >= self.feed_sync_due:
            self.feed_sync_due = time.monotonic() + 600
            self.sync_personal_feed()
        return True


if __name__ == "__main__":
    DATA.mkdir(mode=0o700, parents=True, exist_ok=True)
    lock_file = (DATA / "home.lock").open("a+")
    try:
        fcntl.flock(lock_file, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        raise SystemExit(0)
    Home()
    Gtk.main()
