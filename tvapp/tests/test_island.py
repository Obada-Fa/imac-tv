from pathlib import Path
import sys
from types import SimpleNamespace
import unittest
from unittest import mock

import cairo

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import home
import server
from widgets import island, volume_hud
from gi.repository import Gtk


def shell():
    return SimpleNamespace(px=lambda n: round(n), get_window=lambda: None)


def settle(notice, until=2.0):
    """Run a notice's current phase to `until` seconds, as its timer would, without the timer."""
    t = 0.0
    while t <= until and notice.step(t):
        t += 1 / 30
    return t


class KindTests(unittest.TestCase):
    def test_icon_follows_the_words(self):
        for text, kind in (("Could not reach the controller", "error"), ("Couldn't change the resolution: x", "error"),
                           ("Phone connected", "phone"), ("Opening YouTube…", "play"), ("Starting Dune…", "play"),
                           ("A TV update is available. Select Update now on Home to install it.", "update"),
                           ("Added to Home", "done"), ("TorBox settings saved", "done"), ("Theme: Retro", "info")):
            self.assertEqual(island.notice_kind(text), kind, text)


class NoticeMotionTests(unittest.TestCase):
    def setUp(self):
        self.notice = island.NoticeIsland(shell())
        self.addCleanup(self.notice.destroy)

    def test_drops_as_a_bead_then_unfurls_to_fit_the_words(self):
        n = self.notice
        n.say("Phone connected", 3)
        n.stop()
        W, H = n.full
        n.step(0.1)
        self.assertLess(n.w, n.px(n.BEAD) * 1.1)             # still the bead, dropping out of the edge
        self.assertEqual(n.text_alpha, 0.0)
        widest = max(n.step(k / 30) and n.w for k in range(4, 24))
        self.assertGreater(widest, W)                          # a little overshoot as it unfurls
        self.assertLess(widest, W * 1.08)                      # within the room kept for it
        settle(n)
        self.assertEqual((n.w, n.h, n.text_alpha), (W, H, 1.0))
        self.assertLess(n.room()[0], n.px(n.MAX_TEXT) + 200)

    def test_long_words_take_two_lines_then_an_ellipsis(self):
        n = self.notice
        n.say("word " * 300, 3)
        n.stop()
        self.assertEqual(n.layout.get_line_count(), 2)
        self.assertEqual(n.full[1], max(n.px(n.TALL), n.layout.get_pixel_size()[1] + n.px(30)))

    def test_folds_into_the_bead_then_tucks_into_the_edge(self):
        n = self.notice
        n.say("Theme: Retro", 3)
        n.stop()
        settle(n)
        n.close()
        n.stop()
        n.step(0.2)
        self.assertLess(n.w, n.full[0])
        self.assertEqual(n.text_alpha, 0.0)
        n.step(island.CLOSE_S)
        self.assertEqual((n.w, n.h), (0.0, 0.0))

    def test_a_new_notice_reshapes_the_open_island(self):
        n = self.notice
        n.say("Phone connected", 3)
        n.stop()
        settle(n)
        n.say("Could not reach the controller", 5)
        n.stop()
        self.assertEqual((n.phase, n.kind), ("morph", "error"))
        self.assertIsNotNone(n.old)
        settle(n)
        self.assertEqual(n.w, n.full[0])
        self.assertIsNone(n.old)

    def test_the_same_notice_again_only_stays_longer(self):
        n = self.notice
        n.say("Phone connected", 3)
        n.stop()
        settle(n)
        with mock.patch.object(n, "begin") as begin:
            n.say("Phone connected", 3)
        begin.assert_not_called()

    def test_steps_aside_for_the_volume_and_comes_back(self):
        n = self.notice
        n.say("Phone connected", 6)
        n.stop()
        settle(n)
        n.hold()
        self.assertEqual(n.phase, "close")
        self.assertEqual(n.pending[0], "Phone connected")
        n.say("Theme: Retro", 3)                               # a new one waits too
        self.assertEqual(n.pending[0], "Theme: Retro")
        n.vanish()
        n.release()
        self.assertEqual((n.phase, n.text), ("open", "Theme: Retro"))

    def test_dismiss_forgets_a_waiting_notice(self):
        n = self.notice
        n.hold()
        n.say("Phone connected", 3)
        n.dismiss()
        n.release()
        self.assertEqual(n.phase, "closed")

    def test_draws_at_every_stage(self):
        n = self.notice
        n.say("A TV update is available. Select Update now on Home to install it.", 3)
        n.stop()
        surface = cairo.ImageSurface(cairo.FORMAT_ARGB32, *n.room())
        with mock.patch.object(n, "get_allocated_width", return_value=n.room()[0]):
            for t in (0.05, 0.2, 0.5, 0.8, 1.5):
                n.step(t)
                n.on_draw(n, cairo.Context(surface))
            for glyph_kind in island.TINTS:
                island.badge(cairo.Context(surface), glyph_kind, 40, 30, 16, 1.0)


class WindowBackdropTests(unittest.TestCase):
    """Over another app the island's window paints that app's picture, as no compositor can be trusted to."""

    def test_grabs_the_topmost_window_that_covers_it(self):
        app = Gtk.Window()
        app.set_app_paintable(True)
        app.connect("draw", lambda _w, cr: (cr.set_source_rgb(0.0, 0.5, 1.0), cr.paint()) and False)
        app.resize(600, 200)
        app.move(0, 0)
        app.show_all()
        self.addCleanup(app.destroy)
        for _ in range(50):
            Gtk.main_iteration_do(False)
        window = volume_hud.VolumeOverlay(shell())
        self.addCleanup(window.destroy)
        window.placed, window.at = (300, 60), (50, 10)
        with mock.patch.object(window, "candidates", return_value=[app.get_window()]):
            self.assertTrue(window.grab())
        self.assertEqual((window.picture.get_width(), window.picture.get_height()), (300, 60))
        self.assertGreater(window.blur_k, 1)
        self.assertEqual(window.blur_layers()[0][0], window.soft)
        self.assertIs(window.pill.frost_source(), window)       # the island frosts the app's picture
        surface = cairo.ImageSurface(cairo.FORMAT_ARGB32, 300, 60)
        window.paint_backdrop(window, cairo.Context(surface))
        b, g, r = surface.get_data()[0:3]
        self.assertEqual((r, g), (0, 128))
        self.assertGreater(b, 240)

    def test_a_window_that_does_not_cover_it_is_skipped(self):
        window = volume_hud.VolumeOverlay(shell())
        self.addCleanup(window.destroy)
        small = mock.Mock(get_origin=lambda: (True, 0, 0), get_width=lambda: 10, get_height=lambda: 10)
        window.placed, window.at = (300, 60), (50, 10)
        with mock.patch.object(window, "candidates", return_value=[small]):
            self.assertFalse(window.grab())
        self.assertIsNone(window.picture)


class ShellTests(unittest.TestCase):
    def make(self, prev_app="home", buffering=False):
        return SimpleNamespace(prev_app=prev_app, buffering=mock.Mock(get_visible=lambda: buffering),
                               notice=mock.Mock(), notice_overlay=mock.Mock())

    def test_on_home_the_notice_joins_homes_glass(self):
        for state in (self.make("home"), self.make(None), self.make("player", buffering=True)):
            home.Home.toast(state, "Phone connected", 3)
            state.notice.say.assert_called_once_with("Phone connected", 3, None)
            state.notice_overlay.dismiss.assert_called_once()

    def test_over_an_app_the_notice_gets_its_own_window(self):
        state = self.make("youtube")
        home.Home.toast(state, "Could not reach the controller", 5)
        state.notice.dismiss.assert_called_once()
        start = state.notice_overlay.reveal.call_args[0][0]
        start()
        state.notice_overlay.island.say.assert_called_once_with("Could not reach the controller", 5, None)

    def test_notices_come_back_only_once_no_volume_island_shows(self):
        notice = mock.Mock()
        pill = SimpleNamespace(phase="open")
        state = SimpleNamespace(notice=notice, notice_overlay=None, volume_pill=pill, volume_overlay=None)
        state.notice_islands = lambda: home.Home.notice_islands(state)
        home.Home.volume_hidden(state)
        notice.release.assert_not_called()
        state.volume_switching = True
        pill.phase = "closed"
        home.Home.volume_hidden(state)
        notice.release.assert_not_called()
        state.volume_switching = False
        home.Home.volume_hidden(state)
        notice.release.assert_called_once()

    def test_the_server_leaves_update_notices_to_the_island(self):
        self.assertFalse(hasattr(server.Controller, "_notify_update"))


if __name__ == "__main__":
    unittest.main()
