from pathlib import Path
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
_temp = tempfile.TemporaryDirectory(prefix="imactv-homescreen-test-")

try:
    import homescreen
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


if __name__ == "__main__":
    unittest.main()
