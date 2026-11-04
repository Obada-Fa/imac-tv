from pathlib import Path
import sys
from types import SimpleNamespace
import unittest
from unittest import mock

import cairo

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import home
import overlays
import server
from widgets import island, volume_hud
from gi.repository import Gdk, Gtk


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


class FakeTarget:
    def __init__(self, answers=True, shows=True):
        self.answers, self.shows = answers, shows
        self.frost = overlays.LiveFrost()
        self.frames, self.hidden = [], 0

    def connect(self, timeout=0.4):
        return self.answers

    def show(self, surface, outline):
        self.frames.append((surface.get_width(), surface.get_height(), outline))
        return self.shows

    def hide(self):
        self.hidden += 1

    def close(self):
        pass


class OverAppsTests(unittest.TestCase):
    """Over another app the island is drawn by that app where it can be, never from an old copy of its picture."""

    def host(self, app, tv_browser=False, **targets):
        host = volume_hud.VolumeOverlay(SimpleNamespace(px=lambda n: round(n), get_window=lambda: None,
                                                        prev_app=app, tv_browser=tv_browser))
        made = []
        host.make = lambda kind: made.append(kind) or targets.get(kind) or FakeTarget()
        return host, made

    def test_each_app_gets_its_own_target(self):
        for app, tv_browser, kind in (("youtube", False, "youtube"), ("player", False, "mpv"),
                                      ("desktop:brave-browser", True, "browser"), ("desktop:org.gnome.Nautilus", False, "window")):
            host, made = self.host(app, tv_browser)
            host.choose()
            self.assertEqual(made, [kind], app)

    def test_an_app_that_does_not_answer_leaves_it_to_the_window(self):
        window = FakeTarget()
        host, made = self.host("youtube", youtube=FakeTarget(answers=False), window=window)
        self.assertIs(host.choose(), window)
        self.assertEqual(made, ["youtube", "window"])

    def test_frames_go_to_the_app_and_stop_when_it_is_hidden(self):
        page = FakeTarget()
        host, _made = self.host("youtube", youtube=page)
        host.pill.show_level(40)
        host.pill.stop_timers()
        host.pill.started -= 0.2                            # a fifth of a second into opening
        host.pill.animate()
        self.assertTrue(page.frames)
        width, height, outline = page.frames[-1]
        self.assertEqual((width, height), host.pill.room())
        self.assertTrue(outline.startswith("M") and outline.endswith("Z"))
        self.assertIs(host.pill.frost_source(), page.frost)
        host.pill.dismiss()
        self.assertEqual(page.hidden, 1)
        self.assertIsNone(host.target)

    def test_a_page_that_stops_answering_hands_over_to_the_window(self):
        page, window = FakeTarget(shows=False), FakeTarget()
        host, _made = self.host("youtube", youtube=page, window=window)
        host.pill.show_level(70)
        host.pill.stop_timers()
        host.pill.started -= 0.2
        host.pill.animate()
        self.assertTrue(window.frames)
        self.assertIs(host.target, window)

    def test_the_frame_is_the_island_alone(self):
        host, _made = self.host("player", mpv=FakeTarget())
        host.pill.show_level(100)
        host.pill.size = 1.0
        host.island.frost = overlays.SolidFrost()
        surface, _outline = host.render()
        data = surface.get_data()
        corner = data[0:4]                                  # beside the island: nothing
        middle = (surface.get_height() // 2) * surface.get_stride() + (surface.get_width() // 2) * 4
        self.assertEqual(bytes(corner), bytes(4))
        self.assertEqual(data[middle + 3], 255)             # solid glass over mpv
        self.assertEqual(host.pill.render_width, 0)


class MpvTargetTests(unittest.TestCase):
    def test_lays_the_frame_over_the_video_in_two_halves(self):
        import json, socket, tempfile, threading
        folder = tempfile.mkdtemp()
        path = folder + "/mpv.sock"
        server_sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        server_sock.bind(path)
        server_sock.listen(1)
        seen = []

        def mpv():
            conn, _ = server_sock.accept()
            data = b""
            with conn:
                while True:
                    chunk = conn.recv(65536)
                    if not chunk:
                        return
                    data += chunk
                    while b"\n" in data:
                        line, data = data.split(b"\n", 1)
                        message = json.loads(line)
                        seen.append(message["command"])
                        if message["command"][0] == "get_property":
                            conn.sendall(json.dumps({"data": {"w": 1920, "h": 1080}, "error": "success",
                                                     "request_id": message["request_id"]}).encode() + b"\n")
                        else:
                            try:
                                conn.sendall(b'{"error":"success"}\n')
                            except BrokenPipeError:
                                return
        thread = threading.Thread(target=mpv, daemon=True)
        thread.start()
        target = overlays.MpvTarget(path)
        self.assertTrue(target.connect())
        surface = cairo.ImageSurface(cairo.FORMAT_ARGB32, 400, 70)
        self.assertTrue(target.show(surface))
        self.assertTrue(target.show(surface))
        frames = list(target._file.parent.glob(target._file.name))
        self.assertEqual(len(frames), 1)
        target.hide()
        thread.join(2)
        server_sock.close()
        adds = [c for c in seen if c[0] == "overlay-add"]
        self.assertEqual([c[1:4] for c in adds], [[overlays.MPV_OVERLAY, 760, 0]] * 2)
        self.assertEqual([c[5] for c in adds], [0, 400 * 4 * 70])
        self.assertEqual(adds[0][6:], ["bgra", 400, 70, 1600])
        self.assertEqual(seen[-1], ["overlay-remove", overlays.MPV_OVERLAY])
        self.assertFalse(frames[0].exists())                # its file goes with it

    def test_no_mpv_no_target(self):
        self.assertFalse(overlays.MpvTarget("/nonexistent/mpv.sock").connect())


class OutlineTests(unittest.TestCase):
    def test_cairo_path_becomes_closed_svg(self):
        surface = cairo.ImageSurface(cairo.FORMAT_ARGB32, 200, 80)
        cr = cairo.Context(surface)
        island.edge_outline(cr, 30, 170, 60, 16, 28)
        svg = overlays.svg_path(cr.copy_path())
        self.assertTrue(svg.startswith("M") and svg.endswith("Z"))
        self.assertIn("C", svg)


class UpdateIslandTests(unittest.TestCase):
    def setUp(self):
        self.pressed = []
        self.island = island.UpdateIsland(shell(), lambda: self.pressed.append(1))

    def test_stays_while_the_update_waits_and_ok_installs_it(self):
        u = self.island
        u.show_update("available")
        self.assertEqual((u.phase, u.text, u.kind), ("open", "Update available", "update"))
        self.assertEqual(u.hold_timer, 0)                   # no timer tucks it away
        self.assertTrue(u.get_sensitive())
        enter = mock.Mock(keyval=Gdk.keyval_from_name("Return"))
        self.assertTrue(u.on_key(u, enter))
        self.assertEqual(self.pressed, [1])
        u.stop()

    def test_while_updating_it_cannot_be_pressed(self):
        u = self.island
        u.show_update("updating")
        self.assertEqual(u.text, "Updating the TV…")
        self.assertFalse(u.get_sensitive())
        u.press()
        self.assertEqual(self.pressed, [])
        u.show_update(None)
        self.assertEqual(u.phase, "close")
        u.stop()

    def test_steps_aside_and_comes_back_for_good(self):
        u = self.island
        u.show_update("available")
        u.hold()
        self.assertEqual(u.pending[0], "Update available")
        u.vanish()
        u.release()
        self.assertEqual(u.phase, "open")
        self.assertEqual(u.until, float("inf"))
        u.stop()

    def test_state_from_the_controller(self):
        self.assertEqual(home.update_state({}), None)
        self.assertEqual(home.update_state({"available": True, "can_update": True}), "available")
        self.assertEqual(home.update_state({"available": True, "busy": True}), "checking")
        self.assertEqual(home.update_state({"status": "updating"}), "updating")
        self.assertEqual(home.update_state({"available": True, "can_update": False}), None)


class ShellTests(unittest.TestCase):
    def make(self, prev_app="home", buffering=False):
        return SimpleNamespace(prev_app=prev_app, buffering=mock.Mock(get_visible=lambda: buffering),
                               notice=mock.Mock(), notice_overlay=mock.Mock(), update_notice=mock.Mock())

    def test_on_home_the_notice_joins_homes_glass(self):
        for state in (self.make("home"), self.make(None), self.make("player", buffering=True)):
            home.Home.toast(state, "Phone connected", 3)
            state.notice.say.assert_called_once_with("Phone connected", 3, None)
            state.update_notice.hold.assert_called_once()   # a waiting update steps aside for it
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
