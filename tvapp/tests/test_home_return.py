"""YouTube -> Home must show Home at once: no slow lookups before raising it, and Home animates from the first frame."""
import importlib.util
import os
from pathlib import Path
import sys
import tempfile
import types
import unittest
from unittest import mock

_temp = tempfile.TemporaryDirectory(prefix="imactv-test-")
os.environ.setdefault("IMACTV_DATA_DIR", _temp.name)
spec = importlib.util.spec_from_file_location("imactv_server_home_return", Path(__file__).resolve().parents[1] / "server.py")
server = importlib.util.module_from_spec(spec)
spec.loader.exec_module(server)

HOME_ROW = "0x00600003  0 1234  0 0 1920 1080 home.py.Home host iMac TV"


class InlineThread:
    """threading.Thread stand-in that runs its target on start(), so the order of work is observable."""
    def __init__(self, target=None, args=(), kwargs=None, daemon=None):
        self.target, self.args, self.kwargs = target, args, kwargs or {}

    def start(self):
        self.target(*self.args, **self.kwargs)


class SelectHomeTests(unittest.TestCase):
    def setUp(self):
        for target in (mock.patch.object(server.Controller, "_watch"), mock.patch.object(server.subprocess, "Popen")):
            target.start()
            self.addCleanup(target.stop)
        with mock.patch.object(server.threading, "Thread"):
            self.c = server.Controller()
        self.c.app = "youtube"
        self.calls = []

    def run_select(self, track=None):
        c, calls = self.c, self.calls
        state = {"status": "Playing", "title": track, "artist": "Channel", "source": "youtube"} if track else None

        def now():
            calls.append("mpris")
            return state

        def command(argv, **_kw):
            calls.append(" ".join(argv[:3]))
            return HOME_ROW if argv[:2] == ["wmctrl", "-lpGx"] else ""
        with mock.patch.object(c, "youtube_now", side_effect=now), mock.patch.object(c, "_end_playback"), \
                mock.patch.object(c, "_ui_request", side_effect=lambda s: calls.append("ui " + s)), \
                mock.patch.object(server, "command", side_effect=command), \
                mock.patch.object(server.threading, "Thread", InlineThread):
            return c.select("home")

    def test_home_is_raised_before_any_mpris_lookup(self):
        self.assertEqual(self.run_select("Some video"), {"app": "home", "phase": "ready"})
        self.assertEqual(self.calls, ["ui home", "wmctrl -lpGx", "xdotool windowmap 0x00600003", "wmctrl -i -R",
                                      "wmctrl -i -r", "mpris"])

    def test_no_xdpyinfo_when_home_window_exists(self):
        self.run_select()
        self.assertNotIn("xdpyinfo", self.calls)

    def test_track_left_behind_is_dismissed_after_home_is_up(self):
        self.c._yt_key = "old"
        self.run_select("Some video")
        self.assertEqual(self.c._yt_dismissed, server.nowplaying.track_key(
            {"status": "Playing", "title": "Some video", "artist": "Channel", "source": "youtube"}))
        self.assertEqual(self.c._yt_home_until, 0.0)

    def test_without_a_track_the_tick_window_stays_open(self):
        self.c._yt_key = "old"
        self.run_select(None)
        self.assertEqual(self.c._yt_dismissed, "old")
        self.assertGreater(self.c._yt_home_until, server.time.monotonic())

    def test_late_lookup_does_not_touch_a_newer_selection(self):
        c = self.c
        c._yt_dismissed = "kept"
        with mock.patch.object(c, "youtube_now", return_value={"status": "Playing", "title": "x", "artist": "y"}):
            generation = c.generation
            c.generation += 1
            c._dismiss_youtube(generation)
        self.assertEqual(c._yt_dismissed, "kept")

    def test_home_window_missing_checks_x_before_starting_home(self):
        calls = []

        def command(argv, **_kw):
            calls.append(argv[0])
            raise RuntimeError("no display")
        with mock.patch.object(server, "command", side_effect=command), \
                mock.patch.object(server, "window_for", return_value=None):
            self.c._show_home()
        self.assertEqual(calls, ["xdpyinfo"])
        server.subprocess.Popen.assert_not_called()


class HomeShellTests(unittest.TestCase):
    """Home shell methods on a stand-in object (a real Home window is fullscreen and starts timers)."""
    @classmethod
    def setUpClass(cls):
        import home
        cls.home = home

    def shell(self):
        s = types.SimpleNamespace(ui_at=0.0, prev_app="youtube", last_state_poll=123.0, state_loading=True,
                                  tops={}, sheet=None)
        s.close_sheet = mock.Mock()
        s.goto_top = mock.Mock(side_effect=lambda _name: s.seen.append(s.prev_app))
        s.present = mock.Mock()
        s.seen = []
        return s

    def test_ui_request_makes_home_active_before_switching_screens(self):
        s = self.shell()
        self.home.Home.handle_ui_request(s, {"screen": "home", "ts": 1})
        self.assertEqual(s.seen, ["home"])     # on_enter/supervise already see Home in front
        self.assertEqual(s.last_state_poll, 0)
        self.assertGreater(s.ui_at, 0)
        s.present.assert_called_once_with()

    def test_viz_next_does_not_count_as_coming_home(self):
        s = self.shell()
        self.home.Home.handle_ui_request(s, {"screen": "viz_next", "ts": 1})
        self.assertEqual(s.prev_app, "youtube")

    def test_poll_started_before_the_ui_request_is_ignored(self):
        s = self.shell()
        s.ui_at = 50.0
        self.assertFalse(self.home.Home.apply_state(s, {"app": "youtube", "phase": "ready"}, 49.0))
        self.assertEqual(s.prev_app, "youtube")
        self.assertEqual(s.last_state_poll, 0)
        self.assertFalse(s.state_loading)

    def test_peek_vitals_calls_the_module_and_survives_failures(self):
        fake = types.ModuleType("widgets.vitals")
        fake.peek = mock.Mock()
        import widgets
        with mock.patch.dict(sys.modules, {"widgets.vitals": fake}), mock.patch.object(widgets, "vitals", fake, create=True):
            s = object()
            self.home.Home.peek_vitals(s)
            fake.peek.assert_called_once_with(s)
            fake.peek.side_effect = RuntimeError("boom")
            self.home.Home.peek_vitals(s)
        saved = widgets.__dict__.pop("vitals", None)
        try:
            with mock.patch.dict(sys.modules, {"widgets.vitals": None}):    # the module is missing
                self.home.Home.peek_vitals(object())
        finally:
            if saved is not None:
                widgets.vitals = saved

    def test_info_key_opens_vitals(self):
        from gi.repository import Gdk, Gtk
        self.assertIn("Info", self.home.VITALS_KEYS)
        for name, focus in (("i", None), ("I", None), ("i", Gtk.Entry())):
            s = types.SimpleNamespace(sheet=None, current=lambda: None, peek_vitals=mock.Mock(), get_focus=lambda f=focus: f,
                                      buffering=mock.Mock(**{"get_visible.return_value": False}))
            event = types.SimpleNamespace(keyval=Gdk.keyval_from_name(name))
            with mock.patch.object(self.home.display, "poke"):
                handled = self.home.Home.on_key_press(s, None, event)
            if focus is None:
                self.assertTrue(handled)
                s.peek_vitals.assert_called_once_with()
            else:
                self.assertFalse(handled)       # typing an "i" in a text field stays a letter
                s.peek_vitals.assert_not_called()


if __name__ == "__main__":
    unittest.main()
