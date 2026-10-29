import os
from pathlib import Path
import sys
import unittest
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from widgets import glass, vitals  # noqa: E402
from gi.repository import Gtk, GLib  # noqa: E402


def pump(seconds=0.0):
    """Run the GLib loop for `seconds` (and drain pending events)."""
    if seconds:
        loop = GLib.MainLoop()
        GLib.timeout_add(int(seconds * 1000), lambda: loop.quit() or False)
        loop.run()
    while Gtk.events_pending():
        Gtk.main_iteration_do(False)


class FakeShell(Gtk.Window):
    def __init__(self):
        super().__init__()
        self.scale = 1.0
        self.px = lambda n: round(n * self.scale)
        self.toasts = []
        self.toast = lambda text, secs=4: self.toasts.append(text)
        self.root = Gtk.Overlay()
        self.add(self.root)
        self.root.add(Gtk.Box())
        self.set_default_size(1280, 720)


class ColourTest(unittest.TestCase):
    def test_heat_colour_runs_blue_amber_red(self):
        for frac, want in ((0, vitals.COOL), (0.55, vitals.AMBER), (1, vitals.RED), (-3, vitals.COOL), (7, vitals.RED)):
            for got, w in zip(vitals.heat_color(frac), want):
                self.assertAlmostEqual(got, w)
        mid = vitals.heat_color(0.3)
        self.assertTrue(vitals.COOL[0] < mid[0] < vitals.AMBER[0])

    def test_temperature_fraction(self):
        self.assertIsNone(vitals.temp_frac(None))
        self.assertEqual(vitals.temp_frac(30), 0.0)
        self.assertEqual(vitals.temp_frac(45), 0.0)
        self.assertAlmostEqual(vitals.temp_frac(67.5), 0.5)
        self.assertEqual(vitals.temp_frac(105), 1.0)

    def test_halo_follows_load_or_heat(self):
        self.assertAlmostEqual(vitals.halo_alpha(0, None), 0.06)
        self.assertAlmostEqual(vitals.halo_alpha(1, 0), 0.36)
        self.assertAlmostEqual(vitals.halo_alpha(0.2, 1), 0.36)
        self.assertAlmostEqual(vitals.halo_alpha(0.5, 0.25), 0.21)

    def test_gauges_from_sample(self):
        cpu, mem, heat = vitals.gauges(vitals.CANNED)
        self.assertEqual((cpu["key"], cpu["text"], cpu["caption"]), ("cpu", "38%", "71°C"))
        self.assertAlmostEqual(cpu["heat"], vitals.temp_frac(71))
        self.assertEqual(mem["text"], "67%")
        self.assertEqual(mem["caption"], "5.4 / 8.0 GB")
        self.assertEqual((heat["text"], heat["caption"]), ("71°", "GPU 58°C"))
        self.assertAlmostEqual(heat["value"], (71 - 30) / 70)

    def test_gauges_without_sensors_or_data(self):
        cpu, mem, heat = vitals.gauges({"cpu": 5, "cores": 2})
        self.assertIsNone(cpu["heat"])
        self.assertEqual(cpu["caption"], "2 cores")
        self.assertEqual((mem["value"], mem["caption"]), (0.0, "–"))
        self.assertEqual((heat["text"], heat["caption"], heat["heat"]), ("–", "no sensor", None))

    def test_formatting(self):
        self.assertEqual(vitals.fmt_uptime(3 * 86400 + 4 * 3600 + 60), "3 d 4 h")
        self.assertEqual(vitals.fmt_uptime(2 * 3600 + 5 * 60), "2 h 5 min")
        self.assertEqual(vitals.fmt_uptime(None), "–")
        self.assertEqual(vitals.fmt_bytes(2_400_000), "2.4 MB")
        self.assertEqual(vitals.fmt_bytes(118_000), "118 KB")
        self.assertEqual(vitals.fmt_bytes(12), "12 B")
        self.assertIn("Disk 61%", vitals.summary_line(vitals.CANNED))

    def test_fake_hook(self):
        with mock.patch.dict(os.environ, {"IMACTV_FAKE_VITALS": "1"}):
            self.assertEqual(vitals.take_sample()["cpu"], vitals.CANNED["cpu"])
        with mock.patch.dict(os.environ, {"IMACTV_FAKE_VITALS": '{"cpu": 91}'}):
            self.assertEqual(vitals.take_sample()["cpu"], 91)
        with mock.patch.dict(os.environ, {"IMACTV_FAKE_VITALS": ""}):
            self.assertIsNone(vitals.fake_sample())


class SamplingTest(unittest.TestCase):
    def test_process_scan_only_every_few_seconds(self):
        sampler = mock.Mock()
        sampler.sample.return_value = {"top_cpu": [{"name": "x", "cpu": 1}], "apps": []}
        sampler.system.side_effect = lambda: {"cpu": 10.0}
        now = [100.0]
        with mock.patch.object(vitals, "_SAMPLER", sampler), \
                mock.patch.dict(vitals._PROCS, {"at": None, "data": {}}), \
                mock.patch.dict(os.environ, {"IMACTV_FAKE_VITALS": ""}):
            for _ in range(4):
                s = vitals.take_sample(lambda: now[0])
                now[0] += 1.0
        self.assertEqual(sampler.system.call_count, 4)
        self.assertEqual(sampler.sample.call_count, 2)       # t = 100 and t = 103
        self.assertEqual((s["cpu"], s["top_cpu"][0]["name"]), (10.0, "x"))


    def test_system_reads_rates_without_a_process_scan(self):
        import sysinfo
        from tests.test_sysinfo import FakeRoot
        fake = FakeRoot()
        self.addCleanup(fake.dir.cleanup)
        now = [100.0]
        sampler = sysinfo.Sampler(fake.root, clock=lambda: now[0])
        with mock.patch.object(sampler, "processes", side_effect=AssertionError("no scan")):
            self.assertEqual(sampler.system()["cpu"], 0.0)
            now[0] += 2
            fake.tick(busy=400, total=1600, rx=3000, tx=1500)
            s = sampler.system()
        self.assertEqual(s["cpu"], 50.0)
        self.assertEqual(s["network"], {"rx": 1000, "tx": 500})
        self.assertEqual((s["uptime"], s["temperature"]), (3600, 61.0))
        self.assertEqual(s["memory"]["used"], 5000000 * 1024)


class EasingTest(unittest.TestCase):
    def test_eases_over_half_a_second_then_idles(self):
        e = vitals.Easer()
        self.assertTrue(e.set({"cpu": 0.5}))        # sweeps in from zero
        self.assertEqual(e.get("cpu"), 0.0)
        e.step(0.1)
        first = e.get("cpu")
        self.assertTrue(0 < first < 0.5)
        steps = 1
        while e.step(1 / 15):
            steps += 1
        self.assertEqual(e.get("cpu"), 0.5)
        self.assertLessEqual(steps, 8)             # ~0.5 s at 15 fps
        self.assertFalse(e.moving)
        self.assertFalse(e.set({"cpu": 0.5}))       # same value: nothing to animate

    def test_new_target_mid_way_continues_from_current(self):
        e = vitals.Easer()
        e.set({"cpu": 1.0})
        e.step(0.25)
        mid = e.get("cpu")
        self.assertTrue(e.set({"cpu": 0.0}))
        e.step(0.01)
        self.assertLess(abs(e.get("cpu") - mid), 0.1)


class LiveTest(unittest.TestCase):
    def setUp(self):
        patcher = mock.patch.object(vitals, "THREADED", False)
        patcher.start()
        self.addCleanup(patcher.stop)
        env = mock.patch.dict(os.environ, {"IMACTV_FAKE_VITALS": "1"})
        env.start()
        self.addCleanup(env.stop)

    def test_card_samples_only_while_shown(self):
        shell = FakeShell()
        card = vitals.VitalsCard(shell, None, glass.Look())
        shell.root.add_overlay(card)
        self.assertEqual(card.live.timers(), [])
        shell.show_all()
        pump()
        self.assertTrue(card.live.running)
        self.assertEqual(card.sample["cpu"], vitals.CANNED["cpu"])
        self.assertTrue(card.live.anim_src)          # easing toward the first sample
        pump(0.8)
        self.assertEqual(card.live.anim_src, 0)      # arrived: 0 fps
        self.assertTrue(card.live.sample_src)
        self.assertAlmostEqual(card.easer.get("cpu"), 0.38)
        card.hide()
        pump()
        self.assertFalse(card.live.running)
        self.assertEqual(card.live.timers(), [])
        shell.destroy()

    def test_stale_sample_after_hide_is_dropped(self):
        shell = FakeShell()
        card = vitals.VitalsCard(shell, None, glass.Look())
        shell.root.add_overlay(card)
        shell.show_all()
        pump()
        gen = card.live.gen
        card.hide()
        pump()
        card.sample = None
        card.live.got(gen, {"cpu": 99})
        self.assertIsNone(card.sample)
        self.assertEqual(card.live.timers(), [])
        shell.destroy()

    def test_peek_shows_restarts_and_leaves_nothing(self):
        shell = FakeShell()
        shell.show_all()
        p = vitals.peek(shell)
        pump()
        self.assertTrue(p.shown)
        self.assertIs(p.get_parent(), shell.root)
        first = p.hide_src
        self.assertTrue(first)
        self.assertIs(vitals.peek(shell), p)          # again while shown: same pull-down, a fresh 8 s
        self.assertNotEqual(p.hide_src, first)
        p.hide_peek()
        pump()
        self.assertFalse(p.shown)
        self.assertIsNone(p.get_parent())
        self.assertEqual(p.timers(), [])
        self.assertEqual(p.event_id, 0)
        self.assertIs(vitals.peek(shell), p)          # and it can come back
        p.hide_peek()
        shell.destroy()

    def test_peek_hides_itself_after_eight_seconds(self):
        shell = FakeShell()
        shell.show_all()
        with mock.patch.object(vitals, "PEEK_MS", 300):
            p = vitals.peek(shell)
            pump(0.6)
        self.assertFalse(p.shown)
        self.assertEqual(p.timers(), [])
        shell.destroy()

    def test_any_key_dismisses_peek_except_the_opening_one(self):
        from gi.repository import Gdk
        shell = FakeShell()
        shell.show_all()
        p = vitals.peek(shell)
        p.held = Gdk.KEY_Return

        def key(kv, kind=Gdk.EventType.KEY_PRESS):
            ev = Gdk.Event.new(kind)
            ev.keyval = kv
            return ev
        p.shown_at -= 5                               # past the quiet window
        self.assertFalse(p.on_event(shell, key(Gdk.KEY_Return)))           # the held key repeating
        self.assertFalse(p.on_event(shell, key(Gdk.KEY_Return, Gdk.EventType.KEY_RELEASE)))
        self.assertTrue(p.shown)
        self.assertTrue(p.on_event(shell, key(Gdk.KEY_Down)))               # used up, and gone
        self.assertFalse(p.shown)
        self.assertEqual(p.timers(), [])
        shell.destroy()


class AppRowTest(unittest.TestCase):
    def setUp(self):
        for name, value in (("THREADED", False),):
            patcher = mock.patch.object(vitals, name, value)
            patcher.start()
            self.addCleanup(patcher.stop)
        self.cmd = mock.patch.object(vitals, "cmd", return_value={"ok": True}).start()
        self.addCleanup(mock.patch.stopall)
        self.shell = FakeShell()
        self.addCleanup(self.shell.destroy)

    def row(self, key, running=True):
        r = vitals.AppRow(self.shell, None, glass.Look(), key, key.title())
        r.update({"key": key, "running": running, "memory": 1, "cpu": 0.5})
        return r

    def test_restart_sends_app_restart(self):
        r = self.row("discord")
        r.restart.press()
        self.cmd.assert_called_once_with("app_restart", {"app": "discord"}, timeout=10)

    def test_restart_opens_a_closed_app(self):
        r = self.row("youtube", running=False)
        self.assertEqual(r.restart.title, "Open")
        r.restart.press()
        self.cmd.assert_called_once_with("select", {"app": "youtube"}, timeout=10)

    def test_close_needs_ok_twice(self):
        r = self.row("discord")
        r.close.press()
        self.cmd.assert_not_called()
        self.assertTrue(r.armed)
        self.assertEqual(r.close.title, "OK to close")
        r.close.press()
        self.cmd.assert_called_once_with("app_close", {"app": "discord"}, timeout=10)
        self.assertFalse(r.armed)
        self.assertEqual(r.close.title, "Close")

    def test_close_disarms_by_itself(self):
        r = self.row("player")
        self.assertIsNone(r.restart)                 # the video player can only be closed
        with mock.patch.object(vitals.AppRow, "ARM_MS", 100):
            r.close.press()
        pump(0.3)
        self.assertFalse(r.armed)
        r.close.press()
        self.cmd.assert_not_called()                 # that press only armed it again
        r.disarm()

    def test_close_disabled_when_not_running(self):
        r = self.row("discord", running=False)
        self.assertFalse(r.close.get_sensitive())
        r.close.press()
        self.cmd.assert_not_called()

    def test_failure_is_toasted(self):
        self.cmd.return_value = None
        r = self.row("discord")
        r.restart.press()
        self.assertIn("Can't reach the TV controller", self.shell.toasts)


if __name__ == "__main__":
    unittest.main()
