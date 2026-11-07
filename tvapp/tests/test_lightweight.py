"""CPU savings: calm Home holds still when nobody uses it, and /api/state is shared between pollers."""
import importlib.util
import os
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest import mock

_temp = tempfile.TemporaryDirectory(prefix="imactv-test-")
os.environ["IMACTV_DATA_DIR"] = _temp.name
spec = importlib.util.spec_from_file_location("imactv_server_light", Path(__file__).resolve().parents[1] / "server.py")
server = importlib.util.module_from_spec(spec)
spec.loader.exec_module(server)

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
try:
    import homescreen
except Exception:  # no GTK bindings
    homescreen = None


def calm_home(**kw):
    H = homescreen.AmbientHomeScreen
    screen = SimpleNamespace(cfg={"visual_effects": True}, idle=SimpleNamespace(ambient=False),
                             canvas=SimpleNamespace(fading=lambda: False), chrome=1.0, style="network",
                             dust_amt=1.0, morph=SimpleNamespace(moving=False, target=None), items=[],
                             stir_until=0.0, shape_drives=lambda: False, widget_fading=lambda: False,
                             shape_candidate=lambda: False)
    screen.__dict__.update(kw)
    screen.stirred = lambda: H.stirred(screen)
    return screen


@unittest.skipIf(homescreen is None, "GTK bindings unavailable")
class CalmHomeSettles(unittest.TestCase):
    def needs(self, screen):
        return homescreen.AmbientHomeScreen.needs_anim(screen)

    def test_calm_home_stops_animating_once_settled(self):
        # Calm Home used to run its 20 fps timer forever (~14% of a core on Xvfb, ~30% on the TV).
        self.assertFalse(self.needs(calm_home()))

    def test_recent_input_keeps_the_tiles_moving(self):
        with mock.patch.object(homescreen.time, "monotonic", return_value=100.0):
            self.assertTrue(self.needs(calm_home(stir_until=103.0)))
            self.assertFalse(self.needs(calm_home(stir_until=99.0)))

    def test_fades_and_easing_still_run_while_settled(self):
        self.assertTrue(self.needs(calm_home(chrome=0.5)))
        self.assertTrue(self.needs(calm_home(dust_amt=0.4)))
        self.assertTrue(self.needs(calm_home(canvas=SimpleNamespace(fading=lambda: True))))
        self.assertTrue(self.needs(calm_home(morph=SimpleNamespace(moving=True, target=None))))
        self.assertTrue(self.needs(calm_home(widget_fading=lambda: True)))

    def test_a_playing_tile_keeps_its_bars_moving(self):
        row = SimpleNamespace(slot="row", focus_items=lambda: [SimpleNamespace(now="Playing")])
        self.assertTrue(self.needs(calm_home(items=[row])))
        paused = SimpleNamespace(slot="row", focus_items=lambda: [SimpleNamespace(now="Paused")])
        self.assertFalse(self.needs(calm_home(items=[paused])))

    def test_ambient_rules_are_unchanged(self):
        screen = calm_home(idle=SimpleNamespace(ambient=True), chrome=0.0, dust_amt=0.0)
        self.assertFalse(self.needs(screen))
        screen.chrome = 0.3
        self.assertTrue(self.needs(screen))

    def test_input_stirs_the_tiles(self):
        screen = calm_home(idle=mock.Mock(), start_anim=mock.Mock())
        screen.idle.touch.return_value = False
        with mock.patch.object(homescreen.time, "monotonic", return_value=50.0):
            self.assertFalse(homescreen.AmbientHomeScreen.poke(screen))
        self.assertEqual(screen.stir_until, 50.0 + homescreen.SETTLE_S)
        screen.start_anim.assert_called_once()

    def test_unchanged_links_are_not_repainted(self):
        canvas = mock.Mock()
        canvas.fading.return_value = False
        node = SimpleNamespace(link_center=lambda _c: (10, 20), has_focus=lambda: False, now=None)
        row = SimpleNamespace(slot="row", focus_items=lambda: [node])
        screen = SimpleNamespace(items=[row], canvas=canvas, morph=SimpleNamespace(target=None), dust=[],
                                 shell=SimpleNamespace(scale=1.0), links_key=None, prev_cells=set())
        H = homescreen.AmbientHomeScreen
        self.assertTrue(H.update_links(screen, 5.0))
        self.assertEqual(canvas.queue_draw_region.call_count, 1)
        self.assertFalse(H.update_links(screen, 5.0))          # settled: same nodes, same drift clock
        self.assertEqual(canvas.queue_draw_region.call_count, 1)
        self.assertTrue(H.update_links(screen, 5.1))
        self.assertEqual(canvas.queue_draw_region.call_count, 2)


class SharedStatus(unittest.TestCase):
    def ctl(self):
        calls = []
        obj = SimpleNamespace(STATUS_TTL=1.0, calls=calls)
        obj.status = lambda: (calls.append(1), {"n": len(calls)})[1]
        return obj

    def test_pollers_within_a_second_share_one_answer(self):
        c = self.ctl()
        with mock.patch.object(server.time, "monotonic", side_effect=[10.0, 10.4, 10.9, 11.2]):
            answers = [server.Controller.shared_status(c) for _ in range(4)]
        self.assertEqual([a["n"] for a in answers], [1, 1, 1, 2])

    def test_a_command_makes_the_next_answer_fresh(self):
        c = self.ctl()
        with mock.patch.object(server.time, "monotonic", side_effect=[10.0, 10.1]):
            server.Controller.shared_status(c)
            server.Controller.status_changed(c)
            self.assertEqual(server.Controller.shared_status(c)["n"], 2)

    def test_windows_are_listed_once_per_status(self):
        lister = mock.Mock(return_value=[{"id": "0x1", "pid": "1", "class": "mpv.mpv", "title": "x"}])
        with mock.patch.object(server, "_list_windows", lister):
            with server.window_snapshot():
                server.window_for("player")
                server.window_for("home")
                with server.window_snapshot():       # nested: still the same list
                    server.windows()
            self.assertEqual(lister.call_count, 1)
            server.windows()
            server.windows()
            self.assertEqual(lister.call_count, 3)   # outside a snapshot every call looks again

    def test_xdpyinfo_is_checked_every_few_seconds_not_every_poll(self):
        c = SimpleNamespace(GUI_TTL=5.0)
        with mock.patch.object(server, "command", return_value="name of display: :0") as cmd, \
                mock.patch.object(server.time, "monotonic", side_effect=[0.0, 1.0, 4.9, 5.1]):
            self.assertEqual([server.Controller._gui_up(c) for _ in range(4)], [True] * 4)
        self.assertEqual(cmd.call_count, 2)

    def test_state_endpoint_uses_the_shared_answer_and_posts_invalidate(self):
        import inspect
        self.assertIn("CONTROL.shared_status()", inspect.getsource(server.Handler.do_GET))
        self.assertIn("status_changed", inspect.getsource(server.Handler.do_POST))


if __name__ == "__main__":
    unittest.main()
