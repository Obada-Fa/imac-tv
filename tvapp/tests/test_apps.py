import json
import os
import sys
from pathlib import Path
import tempfile
import unittest
from unittest import mock
import zlib
import struct

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import apps

DESKTOP = """[Desktop Entry]
Type=Application
Name=%s
Exec=%s
Icon=%s
%s"""


def png(path, size=8):
    raw = b"".join(b"\x00" + b"\xff\x00\x00\xff" * size for _ in range(size))

    def chunk(kind, body):
        return struct.pack(">I", len(body)) + kind + body + struct.pack(">I", zlib.crc32(kind + body))
    path.write_bytes(b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", size, size, 8, 6, 0, 0, 0))
                     + chunk(b"IDAT", zlib.compress(raw)) + chunk(b"IEND", b""))


class AppsTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        self.data, self.apps, self.apps2, self.icons = root / "data", root / "a", root / "b", root / "icons"
        for d in (self.data, self.apps, self.apps2, self.icons):
            d.mkdir()
        env = {"IMACTV_DATA_DIR": str(self.data), "IMACTV_APPS_DIRS": f"{self.apps}:{self.apps2}",
               "IMACTV_ICON_DIRS": str(self.icons)}
        patcher = mock.patch.dict(os.environ, env)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.addCleanup(self.tmp.cleanup)
        apps.refresh()

    def write(self, name, title, exec_="true", extra="", icon="", folder=None):
        (folder or self.apps).joinpath(name + ".desktop").write_text(DESKTOP % (title, exec_, icon, extra))
        apps.refresh()

    def ids(self, items=None):
        return [i["id"] for i in (items if items is not None else apps.list_apps())]

    def test_scan_filtering(self):
        self.write("good", "Good")
        self.write("hidden", "Hidden", extra="NoDisplay=true")
        self.write("gone", "Gone", extra="Hidden=true")
        self.write("term", "Term", extra="Terminal=true")
        self.write("tryexec", "Try", extra="TryExec=/nonexistent/binary")
        (self.apps / "link.desktop").write_text("[Desktop Entry]\nType=Link\nName=Link\nExec=true\n")
        (self.apps / "dir.desktop").write_text("[Desktop Entry]\nType=Directory\nName=Dir\n")
        self.assertIn("desktop:good", self.ids())
        for bad in ("hidden", "gone", "term", "tryexec", "link", "dir"):
            self.assertNotIn("desktop:" + bad, self.ids(), bad)

    def test_duplicate_id_earlier_directory_wins(self):
        self.write("same", "First", folder=self.apps)
        self.write("same", "Second", folder=self.apps2)
        names = {i["id"]: i["name"] for i in apps.list_apps()}
        self.assertEqual(names["desktop:same"], "First")

    def test_sorting_and_item_shape(self):
        self.write("b", "banana")
        self.write("a", "Apple")
        items = apps.list_apps()
        rest = [i["name"] for i in items if not i["favorite"] and i["kind"] == "desktop"]
        self.assertEqual(rest, ["Apple", "banana"])
        desktop = next(i for i in items if i["id"] == "desktop:a")
        self.assertEqual(set(desktop), {"id", "name", "kind", "favorite", "icon", "builtin_kind"})
        self.assertEqual(desktop["icon"], "/api/apps/icon?id=desktop%3Aa")
        self.assertIsNone(desktop["builtin_kind"])
        movies = next(i for i in items if i["id"] == "movies")
        self.assertEqual((movies["kind"], movies["builtin_kind"], movies["icon"]), ("screen", "movies", None))

    def test_default_favorites_skip_unavailable(self):
        self.write("rustdesk", "RustDesk")
        self.write("brave-browser", "Brave")
        self.assertEqual(self.ids(apps.home_apps()), apps.DEFAULT_FAVORITES)
        self.assertEqual(self.ids()[:4], apps.DEFAULT_FAVORITES)

    def test_scoped_catalog_and_saved_pins(self):
        self.write("brave-browser", "Brave", exec_="brave-browser")
        self.write("org.rustdesk.RustDesk", "RustDesk")
        self.write("other", "Other")
        (self.data / "home.json").write_text(json.dumps({"favorites": ["desktop:other", "desktop:gone", "movies"]}))
        self.assertEqual(self.ids(apps.home_apps()), ["desktop:other", "movies"])
        self.assertEqual(self.ids(apps.others_apps()), ["desktop:brave-browser", "desktop:org.rustdesk.RustDesk", "calculator"])
        self.assertNotIn("desktop:other", self.ids(apps.others_apps()))
        candidates = self.ids(apps.shortcut_candidates())
        self.assertEqual(candidates[:2], ["desktop:other", "movies"])
        self.assertEqual(json.loads((self.data / "home.json").read_text())["favorites"], ["desktop:other", "desktop:gone", "movies"])
        self.assertEqual(apps.item("calculator")["kind"], "screen")
        utilities = apps.others_apps()
        self.assertEqual([i["name"] for i in utilities], ["Web Browser", "Remote Desktop", "Calculator"])
        self.assertTrue(all(i.get("description") for i in utilities))
        self.assertEqual(apps.item("desktop:brave-browser")["name"], "Brave")

    def test_scoped_does_not_parse_unrelated_entries(self):
        self.write("unrelated", "Unrelated")
        with mock.patch.object(apps, "_entry", wraps=apps._entry) as entry:
            apps.shortcut_candidates()
            self.assertFalse(any(c.args[1] == "unrelated" for c in entry.call_args_list))

    def test_builtin_apps_only_when_available(self):
        with mock.patch.dict(os.environ, {"IMACTV_APPS_BUILTINS": "discord"}):
            ids = self.ids()
        self.assertIn("discord", ids)
        self.assertNotIn("youtube", ids)

    def test_favorite_limit_and_message(self):
        for n in range(3):
            self.write(f"x{n}", f"X{n}")
        self.write("rustdesk", "RustDesk")
        self.write("brave-browser", "Brave")
        apps.set_favorite("desktop:brave-browser", True)
        apps.set_favorite("desktop:rustdesk", True)
        self.assertEqual(len(apps.home_apps()), 6)
        with self.assertRaisesRegex(ValueError, "Home is full: remove a favorite first"):
            apps.set_favorite("desktop:x0", True)
        apps.set_favorite("movies", False)
        result = apps.set_favorite("desktop:x0", True)
        self.assertEqual(self.ids(result)[5], "desktop:x0")
        self.assertEqual(len(apps.home_apps()), 6)

    def test_oldest_first_and_idempotent(self):
        self.write("x", "X")
        apps.set_favorite("movies", False)
        apps.set_favorite("desktop:x", True)
        rev = apps.apps_rev()
        apps.set_favorite("desktop:x", True)      # already there: no change
        self.assertEqual(apps.apps_rev(), rev)
        self.assertEqual(self.ids(apps.home_apps())[-1], "desktop:x")
        self.assertTrue(all(i["favorite"] for i in apps.home_apps()))

    def test_unknown_id_refused(self):
        for bad in ("desktop:nope", "", None, "../etc/passwd", 5):
            with self.assertRaises(ValueError):
                apps.set_favorite(bad, True)
        with self.assertRaises(ValueError):
            apps.set_favorite("movies", "yes")
        self.assertFalse((self.data / "home.json").exists())
        self.assertIsNone(apps.lookup("desktop:nope"))
        self.assertIsNone(apps.launch_argv("../x"))

    def test_home_json_keys_preserved_and_rev_bumps(self):
        (self.data / "home.json").write_text(json.dumps({"style": "bubbles", "ambient": {"idle": 99}, "visualizer": "x"}))
        self.write("x", "X")
        self.assertEqual(apps.apps_rev(), 0)
        apps.set_favorite("desktop:x", True)
        saved = json.loads((self.data / "home.json").read_text())
        self.assertEqual((saved["style"], saved["ambient"], saved["visualizer"]), ("bubbles", {"idle": 99}, "x"))
        self.assertEqual(saved["favorites"][-1], "desktop:x")
        self.assertEqual(apps.apps_rev(), 1)
        apps.set_favorite("desktop:x", False)
        self.assertEqual(apps.apps_rev(), 2)
        self.assertEqual(json.loads((self.data / "home.json").read_text())["style"], "bubbles")
        self.assertEqual([p.name for p in self.data.iterdir() if p.name.endswith(".tmp")], [])

    def test_unavailable_favorites_stay_stored(self):
        (self.data / "home.json").write_text(json.dumps({"favorites": ["desktop:gone", "movies"]}))
        self.assertEqual(self.ids(apps.home_apps()), ["movies"])
        self.write("x", "X")
        apps.set_favorite("desktop:x", True)
        self.assertEqual(json.loads((self.data / "home.json").read_text())["favorites"], ["desktop:gone", "movies", "desktop:x"])

    def test_empty_favorites_is_respected(self):
        (self.data / "home.json").write_text(json.dumps({"favorites": []}))
        self.assertEqual(apps.home_apps(), [])

    def test_scoped_api_dispatch_and_legacy_payload(self):
        import server
        handler = mock.Mock()
        handler.authorised.return_value = True
        for query, name in (("", "list_apps"), ("?scope=home", "home_apps"),
                            ("?scope=others", "others_apps"), ("?scope=shortcuts", "shortcut_candidates")):
            handler.path = "/api/apps" + query
            with mock.patch.object(apps, name, return_value=[{"id": name}]) as catalog:
                server.Handler.do_GET(handler)
            catalog.assert_called_once_with()
            handler.respond.assert_called_with(200, {"apps": [{"id": name}], "max_favorites": apps.MAX_FAVORITES})
        handler.path = "/api/apps?scope=bogus"
        server.Handler.do_GET(handler)
        handler.respond.assert_called_with(400, {"error": "Unknown apps scope"})

    def test_exec_parsing(self):
        self.assertEqual(apps.exec_argv('brave --incognito %U'), ["brave", "--incognito"])
        self.assertEqual(apps.exec_argv('"/opt/My App/run" --name "a b" %f %F %i %c'), ["/opt/My App/run", "--name", "a b"])
        self.assertEqual(apps.exec_argv("env FOO=bar app 100%%"), ["env", "FOO=bar", "app", "100%"])
        self.assertEqual(apps.exec_argv("flatpak run --branch=stable --file-forwarding org.x.App @@u %U @@"),
                         ["flatpak", "run", "--branch=stable", "--file-forwarding", "org.x.App"])
        self.assertEqual(apps.exec_argv("unterminated 'quote"), [])
        self.write("shell", "Shell", exec_="sh -c 'echo; rm -rf /' %u")
        self.assertEqual(apps.launch_argv("desktop:shell"), ["sh", "-c", "echo; rm -rf /"])

    def test_window_hints_and_matching(self):
        self.write("brave-browser", "Brave", exec_="/usr/bin/brave-browser-stable %U", extra="StartupWMClass=Brave-browser")
        self.write("org.rustdesk.RustDesk", "RustDesk", exec_="flatpak run org.rustdesk.RustDesk")
        self.write("firefox", "Firefox", exec_="firefox %U", extra="StartupWMClass=Firefox")
        self.assertTrue(apps.matches_window("desktop:firefox", "Navigator.firefox"))
        # Brave runs as the TV's own kiosk window; a normal Brave window is not it
        self.assertTrue(apps.matches_window("desktop:brave-browser", "brave-browser (/data/brave-tv).imactvbrave"))
        self.assertFalse(apps.matches_window("desktop:brave-browser", "brave-browser.brave-browser"))
        self.assertTrue(apps.matches_window("desktop:org.rustdesk.RustDesk", "rustdesk.rustdesk"))
        self.assertFalse(apps.matches_window("desktop:brave-browser", "firefox.firefox"))
        self.assertNotIn("flatpak", apps.window_hints("desktop:org.rustdesk.RustDesk"))
        self.assertEqual(apps.window_hints("desktop:nope"), [])

    def test_icon_from_absolute_path_and_theme(self):
        png(self.icons / "abs.png")
        self.write("abs", "Abs", icon=str(self.icons / "abs.png"))
        out = apps.icon_file("desktop:abs")
        self.assertTrue(out and Path(out).read_bytes().startswith(b"\x89PNG"))
        theme = self.icons / "hicolor" / "128x128" / "apps"
        theme.mkdir(parents=True)
        png(theme / "themed.png")
        small = self.icons / "hicolor" / "16x16" / "apps"
        small.mkdir(parents=True)
        png(small / "themed.png", 2)
        self.write("themed", "Themed", icon="themed")
        self.assertEqual(Path(apps._find_icon("themed")).parent, theme)
        self.assertTrue(Path(apps.icon_file("desktop:themed")).is_file())
        self.assertEqual(apps.icon_file("desktop:themed"), apps.icon_file("desktop:themed"))

    def test_icon_source_cache_ttl_negative_and_refresh(self):
        self.write("icon", "Icon", icon="missing")
        with mock.patch.object(apps, "_find_icon", wraps=apps._find_icon) as find:
            self.assertIsNone(apps.icon_file("desktop:icon"))
            self.assertIsNone(apps.icon_file("desktop:icon"))
            self.assertEqual(find.call_count, 1)
            png(self.icons / "missing.png")
            apps.refresh()
            first = apps.icon_file("desktop:icon")
            self.assertTrue(first)
            self.assertEqual(apps.icon_file("desktop:icon"), first)
            self.assertEqual(find.call_count, 2)
            with mock.patch.object(apps.time, "monotonic", return_value=10**10):
                self.assertEqual(apps.icon_file("desktop:icon"), first)
            self.assertEqual(find.call_count, 3)
            os.utime(self.icons / "missing.png", ns=(1, 1))
            self.assertNotEqual(apps.icon_file("desktop:icon"), first)

    def test_icon_never_raises(self):
        self.write("noicon", "No")
        self.write("missing", "Missing", icon="does-not-exist")
        for bad in ("desktop:noicon", "desktop:missing", "desktop:zzz", "movies", None, 3):
            self.assertIsNone(apps.icon_file(bad))


if __name__ == "__main__":
    unittest.main()
