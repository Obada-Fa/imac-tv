#!/usr/bin/env python3
"""Render current GTK menus with sample data, on a private Xvfb display.

xvfb-run -a -s '-screen 0 1920x1080x24' python3 scripts/ui-preview.py
No TV service, real settings, add-ons, or network access are used.
"""
import argparse
from contextlib import ExitStack
import datetime
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tvapp"))


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--output", type=Path, default=ROOT / "tvapp/previews/current")
    ap.add_argument("--check", action="store_true", help="Verify source hashes and screenshot files without rendering")
    args = ap.parse_args()
    if args.check:
        errors = []
        for manifest_path in sorted(args.output.glob("*manifest.json")):
            manifest = json.loads(manifest_path.read_text())
            for filename, expected in manifest["source_sha256"].items():
                path = ROOT / filename
                if not path.exists() or hashlib.sha256(path.read_bytes()).hexdigest() != expected:
                    errors.append("Source changed since capture: " + filename)
            for capture in manifest["captures"]:
                if not (args.output / capture["file"]).is_file():
                    errors.append("Screenshot missing: " + capture["file"])
        if not list(args.output.glob("*manifest.json")):
            errors.append("No capture manifest found")
        if errors:
            raise SystemExit("\n".join(errors))
        print("Screenshot files and captured source hashes match.")
        return
    if not os.environ.get("DISPLAY") or os.environ["DISPLAY"] in (":0", ":0.0"):
        raise SystemExit("Run on a private Xvfb display, never the user's desktop.")
    out = args.output.resolve()
    out.mkdir(parents=True, exist_ok=True)
    (ROOT / "build").mkdir(exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="ui-preview-", dir=ROOT / "build") as scratch, ExitStack() as stack:
        stack.enter_context(mock.patch.dict(os.environ, {"IMACTV_DATA_DIR": scratch}))
        import home
        import homescreen
        import screens
        from widgets import chrome, settings, pairing
        from gi.repository import Gtk, Gdk, GLib
        from media import streams
        for module in (screens, settings.display, chrome):
            stack.enter_context(mock.patch.object(module, "DATA", Path(scratch)))
        stack.enter_context(mock.patch("threading.Thread"))
        stack.enter_context(mock.patch.object(screens._data_pool, "submit"))
        stack.enter_context(mock.patch.object(homescreen.ambient, "Library", return_value=None))
        stack.enter_context(mock.patch.object(homescreen.AmbientHomeScreen, "preload"))
        stack.enter_context(mock.patch.object(settings, "cmd", return_value=None))
        art = ROOT / "tvapp/assets/preview-stremio-space.png"
        def load_art(_url, width, height, done):
            done(screens.cover_pixbuf(art, width, height))
        stack.enter_context(mock.patch.object(screens, "load_art", side_effect=load_art))
        items = [dict(type="movie", id=f"tt99000{i}", name=name, year="2026", runtime="2h 4m",
                      genres=["Adventure", "Drama"], poster="sample", background="sample",
                      description="A new journey begins. Find your next favourite story.")
                 for i, name in enumerate(("Beyond the horizon", "A quiet place", "The long way home",
                                           "Into the blue", "After midnight", "Wild earth"))]
        stack.enter_context(mock.patch.object(screens, "continue_items", return_value=items[:3]))
        outputs = [{"name": "eDP-1", "connected": True, "primary": True, "current": {"mode": "1920x1080", "rate": 60},
                    "preferred": {"mode": "1920x1080", "rate": 60},
                    "modes": [{"mode": "1920x1080", "rates": [60, 30]}, {"mode": "1280x720", "rates": [60]}]}]
        sources = [streams.parse({"name": "[TB+] Torrentio " + q,
                    "title": f"Beyond the horizon {q} {fmt}\n💾 {size} GB", "url": "https://example.invalid/" + q})
                   for q, fmt, size in (("1080p", "WEB-DL H264", 5), ("2160p", "REMUX HEVC", 20), ("720p", "WEB-DL H264", 2))]
        captures = []

        class Shell(Gtk.Window):
            def __init__(self, height):
                super().__init__(title="UI preview — sample data")
                self.scale = height / 1080
                self.sw, self.sh = round(1920 * self.scale), height
                self.px = lambda n: round(n * self.scale)
                self.set_default_size(self.sw, self.sh)
                self.tops, self.sheet, self.pair_listeners = {}, None, []
                self.net = {"connected": True, "type": "wifi", "ssid": "Preview Wi-Fi", "ip": "192.0.2.1", "signal": 80}
                self.code, self.remote = "123456", {"connected": True, "count": 1}
                self.avoid_rect = None
                self.page = None
                for name in ("toast", "show_pairing", "trigger_network_refresh", "goto_top", "open_app", "open_details", "show_power"):
                    setattr(self, name, lambda *a, **kw: None)
                self.open_sheet = lambda *a, **kw: home.Home.open_sheet(self, *a, **kw)
                self.close_sheet = lambda *a, **kw: home.Home.close_sheet(self, *a, **kw)
                self.focus_screen = lambda *a: None
                self.root = Gtk.Overlay()
                self.add(self.root)

            def current(self):
                return self.page

            def ensure_focus(self):
                pass

        def capture(shell, name, focus=None):
            shell.show_all()
            if focus:
                focus.grab_focus()
            loop = GLib.MainLoop()
            def save():
                width, height = shell.get_size()
                pb = Gdk.pixbuf_get_from_window(shell.get_window(), 0, 0, width, height)
                filename = f"{name}-{shell.sh}.png"
                pb.savev(str(out / filename), "png", ["compression"], ["9"])
                captures.append({"file": filename, "width": width, "height": height, "data": "sample"})
                loop.quit()
                return False
            GLib.timeout_add(220, save)
            loop.run()

        for height in (720, 1080):
            css = Gtk.CssProvider()
            css.load_from_data(home.build_css(height / 1080))
            Gtk.StyleContext.add_provider_for_screen(Gdk.Screen.get_default(), css, 600)
            shell = Shell(height)
            page = homescreen.AmbientHomeScreen(shell)
            shell.page = page
            shell.root.add(page)
            bg, blur = homescreen.load_surface(art, shell.sw, shell.sh)
            page.canvas.show_surface(bg, blur, animate=False)
            page.canvas.extra = None
            clock = next(w for w in page.items if w.id == "clock")
            clock.time_lbl.set_text("20:30")
            clock.date_lbl.set_text("Tuesday 27 October")
            capture(shell, "home", page.first_focus([]))
            page.dispose()
            shell.destroy()

            shell = Shell(height)
            page = settings.SettingsScreen(shell)
            shell.page = page
            shell.root.add(page)
            page.outputs = outputs
            for i, category in enumerate(settings.settings_model.CATEGORIES):
                page.select_category(category)
                capture(shell, "settings" if i == 0 else "settings-" + category.lower(), page.tabs[i])
            page.choice_sheet("Node shape", ["hexagon", "octagon"], "hexagon", lambda value: None)
            capture(shell, "setting-choice", shell.sheet["default"])
            shell.close_sheet()
            pairing.show_power_sheet(shell)
            capture(shell, "power", shell.sheet["default"])
            shell.destroy()

            shell = Shell(height)
            page = screens.BrowseScreen(shell, "movie")
            shell.page = page
            shell.root.add(page)
            for rail in page.rails[:2]:
                rail.state = "ready"
                rail.set_items(items)
            page.set_hero(items[0])
            if page.hero_timer:
                GLib.source_remove(page.hero_timer)
                page.hero_timer = 0
            page.hero_img.set_pixbuf(screens.cover_pixbuf(art, shell.sw, page.hero_h))
            capture(shell, "movies", page.rails[0].cards[0])
            shell.destroy()

            shell = Shell(height)
            page = screens.DetailsScreen(shell, items[0])
            shell.page = page
            shell.root.add(page)
            page.meta_ok = True
            page.update_buttons()
            capture(shell, "details", page.play_btn)
            shell.destroy()

            shell = Shell(height)
            page = screens.SearchScreen(shell)
            shell.page = page
            shell.root.add(page)
            capture(shell, "search", page.entry)
            shell.destroy()

            shell = Shell(height)
            page = screens.SourcesScreen(shell, items[0])
            shell.page = page
            shell.root.add(page)
            page.done(page.token, {"ok": True, "result": {"sources": sources}})
            capture(shell, "sources", page.rows[0])
            shell.destroy()

            shell = Shell(height)
            page = chrome.LoadingView(shell, chrome.look(shell))
            shell.root.add(page)
            page.set_text(title=items[0]["name"], status="Preparing your stream  ·  12 peers  ·  2.4 MB/s",
                          meta="1080p  ·  WEB-DL  ·  5.0 GB", kicker="Getting ready")
            page.set_art(poster=screens.cover_pixbuf(art, shell.px(260), shell.px(390)),
                         art=screens.cover_pixbuf(art, shell.sw, shell.sh))
            capture(shell, "loading")
            shell.destroy()
            Gtk.StyleContext.remove_provider_for_screen(Gdk.Screen.get_default(), css)
        paths = sorted((ROOT / "tvapp").glob("*.py")) + sorted((ROOT / "tvapp/widgets").glob("*.py"))
        manifest = {"generated_utc": datetime.datetime.now(datetime.timezone.utc).isoformat(),
                    "source_commit": subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip(),
                    "method": "Actual GTK widgets on private Xvfb; sample data; default appearance; no shell header",
                    "source_sha256": {str(p.relative_to(ROOT)): hashlib.sha256(p.read_bytes()).hexdigest() for p in paths},
                    "captures": captures}
        (out / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
        print(f"Rendered {len(captures)} current UI screenshots in {out}")


if __name__ == "__main__":
    main()
