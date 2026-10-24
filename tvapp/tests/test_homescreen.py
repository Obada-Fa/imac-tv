from pathlib import Path
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
_temp = tempfile.TemporaryDirectory(prefix="imactv-homescreen-test-")

try:
    import homescreen
    import home as shell_module
except Exception as exc:  # no PyGObject / GTK typelibs
    homescreen = None


@unittest.skipIf(homescreen is None, "GTK bindings unavailable")
class IdleTests(unittest.TestCase):
    def test_enters_ambient_after_idle_and_only_once(self):
        t = homescreen.IdleTracker(120, now=0)
        self.assertFalse(t.check(119))
        self.assertTrue(t.check(120))
        self.assertFalse(t.check(500))
        self.assertTrue(t.ambient)

    def test_first_input_wakes_and_is_swallowed(self):
        t = homescreen.IdleTracker(10, now=0)
        t.check(20)
        self.assertTrue(t.touch(21))     # woke: caller must swallow this key
        self.assertFalse(t.ambient)
        self.assertFalse(t.touch(22))    # next key navigates
        self.assertFalse(t.check(31))
        self.assertTrue(t.check(32))     # idle clock restarted at the last input

    def test_input_before_ambient_postpones_it(self):
        t = homescreen.IdleTracker(10, now=0)
        self.assertFalse(t.touch(8))
        self.assertFalse(t.check(15))
        self.assertTrue(t.check(18))


@unittest.skipIf(homescreen is None, "GTK bindings unavailable")
class HelperTests(unittest.TestCase):
    def test_search_scope_follows_the_screen_we_leave(self):
        self.assertEqual(homescreen.search_scope("movies"), "movie")
        self.assertEqual(homescreen.search_scope("series"), "series")
        for other in ("home", "search", "", None):
            self.assertIsNone(homescreen.search_scope(other))

    def test_photo_label(self):
        self.assertEqual(homescreen.photo_label(None), ("", ""))
        self.assertEqual(homescreen.photo_label({"caption": "Lofoten, Norway", "credit": "A. Photog", "source": "bing"}),
                         ("Lofoten, Norway", "A. Photog · Bing"))
        self.assertEqual(homescreen.photo_label({"caption": "x", "credit": "", "source": "bing"}), ("x", "Bing"))
        self.assertEqual(homescreen.photo_label({"caption": "x", "credit": "Wikimedia Commons", "source": "wikimedia"})[1],
                         "Wikimedia Commons")

    def test_bottom_label_prefers_track_metadata_and_restores_photo(self):
        photo = {"caption": "Lofoten", "credit": "A. Photog", "source": "bing"}
        self.assertEqual(homescreen.bottom_label(photo, {"status": "Playing", "title": "Song", "artist": "Artist"}),
                         ("Song", "Artist"))
        self.assertEqual(homescreen.bottom_label(photo, {"status": "Paused", "title": "Song"}),
                         ("Lofoten", "A. Photog · Bing"))
        self.assertEqual(homescreen.bottom_label(photo, {"status": "Stopped", "title": "Song"}),
                         ("Lofoten", "A. Photog · Bing"))
        # ambient without music keeps the photo's caption (it used to go blank with the blurred photo)
        self.assertEqual(homescreen.bottom_label(photo, None), ("Lofoten", "A. Photog · Bing"))

    def test_idle_delay_is_shorter_while_youtube_plays(self):
        self.assertEqual(homescreen.idle_delay(120, 30, {"status": "Playing", "title": "x"}), 30)
        self.assertEqual(homescreen.idle_delay(120, 30, {"status": "Paused"}), 120)
        self.assertEqual(homescreen.idle_delay(120, 30, None), 120)
        self.assertEqual(homescreen.idle_delay(20, 30, {"status": "Playing"}), 20)   # never longer than idle

    def test_fade_step_is_linear_and_clamped(self):
        self.assertAlmostEqual(homescreen.fade_step(1.0, 0.0, 0.25, 1.0), 0.75)
        self.assertEqual(homescreen.fade_step(0.1, 0.0, 0.5, 1.0), 0.0)
        self.assertEqual(homescreen.fade_step(0.9, 1.0, 5, 1.0), 1.0)
        self.assertEqual(homescreen.fade_step(0.0, 1.0, 0.1, 0), 1.0)


@unittest.skipIf(homescreen is None, "GTK bindings unavailable")
class UiRequestTests(unittest.TestCase):
    def setUp(self):
        import home
        self.home = home
        self.search = mock.Mock(spec=["set_scope", "set_query"])
        self.shell = mock.Mock()
        self.shell.tops = {"search": self.search}

    def run_req(self, req, current_sname):
        self.shell.current.return_value = mock.Mock(sname=current_sname)
        self.home.Home.handle_ui_request(self.shell, req)

    def test_search_is_scoped_by_current_top_screen(self):
        for sname, scope in (("movies", "movie"), ("series", "series"), ("home", None)):
            self.search.reset_mock()
            self.run_req({"screen": "search", "query": "dune"}, sname)
            self.shell.goto_top.assert_called_with("search")
            self.search.set_scope.assert_called_once_with(scope)
            self.search.set_query.assert_called_once_with("dune")

    def test_search_without_set_scope_still_works(self):
        self.shell.tops = {"search": mock.Mock(spec=["set_query"])}
        self.run_req({"screen": "search"}, "movies")
        self.shell.goto_top.assert_called_with("search")

    def test_movies_series_home_go_to_top(self):
        for name in ("movies", "series", "home"):
            self.run_req({"screen": name}, "home")
            self.shell.goto_top.assert_called_with(name)
            self.shell.present.assert_called()


@unittest.skipIf(homescreen is None, "GTK bindings unavailable")
class SheetRoutingTests(unittest.TestCase):
    """Pairing view and power confirm are in-window sheets, so the phone remote's keys always reach Home."""

    def key(self, shell, name):
        from gi.repository import Gdk
        ev = mock.Mock(keyval=Gdk.keyval_from_name(name))
        return shell_module.Home.on_key_press(shell, None, ev)

    def make(self, sheet):
        shell = mock.Mock()
        shell.buffering.get_visible.return_value = False
        shell.sheet = sheet
        shell.current.return_value = mock.Mock(spec=[])     # no poke(): not the ambient screen
        shell.get_focus.return_value = None
        return shell

    def test_back_keys_and_p_close_an_open_sheet(self):
        for k in ("Escape", "BackSpace", "XF86Back", "Back", "p"):
            shell = self.make({"kind": "pairing"})
            self.assertTrue(self.key(shell, k))
            shell.close_sheet.assert_called_once_with()
            shell.go_back.assert_not_called()

    def test_arrows_navigate_inside_the_sheet_and_enter_reaches_the_button(self):
        shell = self.make({"kind": "power"})
        self.assertTrue(self.key(shell, "Right"))
        shell.navigate.assert_called_once_with("Right")
        shell.reset_mock()
        self.assertFalse(self.key(shell, "Return"))          # left to the focused button
        shell.close_sheet.assert_not_called()

    def test_without_a_sheet_back_still_goes_back_and_p_opens_pairing(self):
        shell = self.make(None)
        self.assertTrue(self.key(shell, "Escape"))
        shell.go_back.assert_called_once_with()
        shell.reset_mock()
        self.assertTrue(self.key(shell, "p"))
        shell.show_pairing.assert_called_once_with()

    def test_no_gtk_dialog_windows_remain(self):
        root = Path(__file__).resolve().parents[1]
        for rel in ("home.py", "homescreen.py", "widgets/pairing.py"):
            code = (root / rel).read_text()
            for bad in ("Gtk.Dialog(", "Gtk.MessageDialog(", ".run()"):
                self.assertNotIn(bad, code, f"{rel} uses {bad}")

    def test_remote_connected_uses_server_field_else_fallback(self):
        from widgets.netinfo import remote_connected
        self.assertTrue(remote_connected({"connected": True}, lambda: False))
        self.assertFalse(remote_connected({"connected": False, "devices": 3}, lambda: True))
        self.assertTrue(remote_connected(None, lambda: True))         # old server: any phone ever paired
        self.assertFalse(remote_connected({}, lambda: False))


@unittest.skipIf(homescreen is None, "GTK bindings unavailable")
class VizRemoteTests(unittest.TestCase):
    def screen(self, mode="edgeflow", names=("edgeflow", "ridge", "prism"), stop=False):
        viz = mock.Mock(mode=mode, names=list(names))
        return mock.Mock(viz=viz, viz_stop=stop, lv={})

    def test_next_viz_steps_through_modes_in_order(self):
        s = self.screen(mode="prism")
        self.assertTrue(homescreen.AmbientHomeScreen.next_viz(s))
        self.assertEqual(s.viz.switch.call_args[0][0], "edgeflow")       # wraps around
        self.assertEqual(homescreen.AmbientHomeScreen.viz_status(s), {"active": True, "mode": "prism"})

    def test_next_viz_ignored_without_a_running_visualizer(self):
        s = self.screen(stop=True)
        self.assertFalse(homescreen.AmbientHomeScreen.next_viz(s))
        s.viz.switch.assert_not_called()
        self.assertEqual(homescreen.AmbientHomeScreen.viz_status(s), {"active": False, "mode": None})
        s.viz = None
        self.assertFalse(homescreen.AmbientHomeScreen.next_viz(s))


if __name__ == "__main__":
    unittest.main()
