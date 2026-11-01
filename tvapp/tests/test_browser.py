"""The TV browser: its launch flags, what may be opened, and how the controller routes phone keys around its menu."""
import importlib.util
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest import mock

_temp = tempfile.TemporaryDirectory(prefix="imactv-browser-test-")
os.environ["IMACTV_DATA_DIR"] = _temp.name
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import browser
spec = importlib.util.spec_from_file_location("imactv_server_browser", Path(__file__).resolve().parents[1] / "server.py")
server = importlib.util.module_from_spec(spec)
spec.loader.exec_module(server)

KIOSK = {"id": "0x9", "pid": "5", "class": "brave-browser (/d/brave-tv).imactvbrave", "title": "Page - Brave"}
MENU = {"id": "0x7", "pid": "6", "class": "home.py.home.py", "title": browser.MENU_TITLE}


class UrlTests(unittest.TestCase):
    def test_addresses_and_searches(self):
        self.assertEqual(browser.target_url(" https://a.org/x?y=1 "), "https://a.org/x?y=1")
        self.assertEqual(browser.target_url("nos.nl/sport"), "https://nos.nl/sport")
        self.assertEqual(browser.target_url("localhost:8000"), "https://localhost:8000")
        self.assertEqual(browser.target_url("best pizza"), "https://search.brave.com/search?q=best+pizza")

    def test_other_schemes_are_only_searched_for(self):
        for text in ("file:///etc/passwd", "javascript:alert(1)", "brave://settings", "chrome://flags"):
            self.assertTrue(browser.target_url(text).startswith("https://search.brave.com/search?q="), text)

    def test_empty_is_refused(self):
        for text in ("", "   ", None, "x" * 2001):
            with self.assertRaises(ValueError):
                browser.target_url(text)

    def test_tab_ids_cannot_reach_other_endpoints(self):
        for tab in ("../close/ABCDEF12", "", None, "new?file:///x"):
            with self.assertRaises(ValueError):
                browser.activate_tab(tab)


class LaunchTests(unittest.TestCase):
    def test_only_a_native_brave_becomes_the_tv_browser(self):
        self.assertTrue(browser.is_brave_argv(["/usr/bin/brave-browser-stable"]))
        for argv in (["flatpak", "run", "com.brave.Browser"], ["/snap/bin/brave"], ["firefox"], []):
            self.assertFalse(browser.is_brave_argv(argv), argv)

    def test_flags_keep_the_debug_port_off_the_default_profile(self):
        argv = browser.tv_argv(["brave"])
        self.assertIn("--kiosk", argv)
        self.assertIn(f"--user-data-dir={browser.profile_dir()}", argv)
        self.assertFalse([a for a in argv if a.startswith("--remote-debugging-address")])


class ViewTests(unittest.TestCase):
    PAGES = [{"id": "A1B2C3D4", "title": "One", "url": "u1"}, {"id": "A1B2C3D5", "title": "Two", "url": "u2"}]

    def active(self, title):
        with mock.patch.object(browser, "tabs", return_value=self.PAGES), \
                mock.patch.object(browser, "field_focused", return_value=False):
            return [t["id"] for t in browser.view(title)["tabs"] if t["active"]]

    def test_window_title_marks_the_tab_in_front(self):
        self.assertEqual(self.active("Two - Brave"), ["A1B2C3D5"])

    def test_unknown_title_still_marks_one_tab(self):
        self.assertEqual(self.active("Something else"), ["A1B2C3D4"])

    def test_view_says_when_a_page_field_has_focus(self):
        with mock.patch.object(browser, "tabs", return_value=self.PAGES), \
                mock.patch.object(browser, "field_focused", return_value=True) as check:
            self.assertTrue(browser.view("Two - Brave")["typing"])
        self.assertEqual(check.call_args.args[0], "A1B2C3D5")

    def test_field_check_survives_a_dead_tab(self):
        with mock.patch.object(browser, "_page_command", side_effect=OSError):
            self.assertFalse(browser.field_focused("A1B2C3D4"))

    def test_server_frames_of_each_length_are_read(self):
        sock = mock.Mock()
        self.assertEqual(browser._ws_read(bytes([0x81, 2]) + b"ok", sock), b"ok")
        self.assertEqual(browser._ws_read(bytes([0x81, 126, 1, 44]) + b"x" * 300, sock), b"x" * 300)
        sock.recv.side_effect = [b"lo", b""]
        self.assertEqual(browser._ws_read(bytes([0x81, 5]) + b"hel", sock), b"hello")

    def test_no_browser_means_no_view(self):
        with mock.patch.object(browser, "tabs", side_effect=OSError):
            self.assertIsNone(browser.view("x"))

    def test_long_frames_use_the_extended_length(self):
        self.assertEqual(browser._ws_frame(b"x")[:2], bytes([0x81, 0x81]))
        self.assertEqual(browser._ws_frame(b"x" * 300)[:4], bytes([0x81, 0xFE, 1, 44]))


class WindowListTests(unittest.TestCase):
    def test_class_with_a_profile_path(self):
        rows = ("0x00400002  0 225 0 0 1280 720  brave-browser (/a b/brave-tv).ImacTvBrave  host Page A - Brave\n"
                "0x04600004  0 1376  0 0 2560 1440 mpv.mpv host Movie one")
        with mock.patch.object(server, "command", return_value=rows):
            found = server.windows()
        self.assertEqual(found[0]["class"], "brave-browser (/a b/brave-tv).imactvbrave")
        self.assertEqual(found[0]["title"], "Page A - Brave")
        self.assertEqual((found[1]["class"], found[1]["title"]), ("mpv.mpv", "Movie one"))


class RoutingTests(unittest.TestCase):
    def setUp(self):
        for target in (mock.patch.object(server.subprocess, "Popen"), mock.patch.object(server.Controller, "_show_home"),
                       mock.patch.object(server.Controller, "_watch"), mock.patch.object(server.Controller, "_yield_audio"),
                       mock.patch.object(server, "playback", return_value={"active": False}),
                       mock.patch.object(server.apps, "is_tv_browser", return_value=True)):
            target.start()
            self.addCleanup(target.stop)
        with mock.patch.object(server.threading, "Thread"):
            self.c = server.Controller()
        self.c.app, self.c.phase = "desktop:brave-browser", "ready"
        self.calls = []
        self.windows = [KIOSK]
        for target in (mock.patch.object(server, "windows", side_effect=lambda: self.windows),
                       mock.patch.object(server, "window_for", return_value=KIOSK),
                       mock.patch.object(server, "activate"),
                       mock.patch.object(server, "command", side_effect=lambda argv, **k: self.calls.append(argv) or "")):
            target.start()
            self.addCleanup(target.stop)

    def press(self, action):
        return self.c.action(action, {})

    def test_menu_key_asks_home_for_the_menu(self):
        with mock.patch.object(self.c, "_ui_request") as ui:
            self.press("menu")
        ui.assert_called_once_with("browser_menu")
        self.assertEqual(self.calls, [])

    def test_back_is_history_back_without_the_menu(self):
        self.press("back")
        self.assertEqual(self.calls, [["xdotool", "key", "--clearmodifiers", "alt+Left"]])
        server.activate.assert_called_once_with(KIOSK)

    def test_keys_go_to_the_open_menu(self):
        self.windows = [KIOSK, MENU]
        self.press("back")
        self.assertEqual(self.calls, [["wmctrl", "-i", "-R", "0x7"], ["xdotool", "key", "--clearmodifiers", "Escape"]])
        server.activate.assert_not_called()

    def tabs_patch(self, *ids):
        return mock.patch.object(browser, "tabs", return_value=[{"id": i, "title": i, "url": ""} for i in ids])

    def test_closing_the_last_tab_keeps_the_browser_open(self):
        with self.tabs_patch("ABCDEF12"), mock.patch.object(browser, "new_tab") as new, \
                mock.patch.object(browser, "close_tab") as close:
            self.c.browser_do({"do": "close_tab", "id": "ABCDEF12"})
        new.assert_called_once_with(browser.START_URL)
        close.assert_called_once_with("ABCDEF12")

    def test_closing_one_of_several_tabs_leaves_the_rest(self):
        with self.tabs_patch("ABCDEF12", "ABCDEF13"), mock.patch.object(browser, "new_tab") as new, \
                mock.patch.object(browser, "close_tab") as close:
            self.c.browser_do({"do": "close_tab", "id": "ABCDEF13"})
        new.assert_not_called()
        close.assert_called_once_with("ABCDEF13")

    def test_address_loads_in_the_tab_in_front(self):
        with self.tabs_patch("ABCDEF12", "ABCDEF13"), mock.patch.object(browser, "navigate") as nav, \
                mock.patch.object(browser, "new_tab") as new:
            self.c.browser_do({"do": "open", "text": "nos.nl"})
        nav.assert_called_once_with("ABCDEF12", "https://nos.nl")
        new.assert_not_called()

    def test_address_falls_back_to_a_new_tab(self):
        with self.tabs_patch("ABCDEF12"), mock.patch.object(browser, "navigate", side_effect=OSError), \
                mock.patch.object(browser, "new_tab") as new:
            self.c.browser_do({"do": "open", "text": "nos.nl"})
        new.assert_called_once_with("https://nos.nl")

    def test_unknown_command_is_refused(self):
        with self.assertRaises(ValueError):
            self.c.browser_do({"do": "rm"})


if __name__ == "__main__":
    unittest.main()
