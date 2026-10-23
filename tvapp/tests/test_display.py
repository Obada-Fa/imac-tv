import json
import os
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import display  # noqa: E402

LAPTOP_TV = """\
Screen 0: minimum 320 x 200, current 3286 x 1080, maximum 16384 x 16384
eDP-1 connected primary 1366x768+0+0 (normal left inverted right x axis y axis) 309mm x 173mm
   1366x768      60.00*+  40.00
   1360x768      59.80    59.96
   1280x720      60.00    59.99    59.86    59.74
   1024x768      60.04    60.00
HDMI-1 connected 1920x1080+1366+0 (normal left inverted right x axis y axis) 1600mm x 900mm
   1920x1080     60.00 +  50.00    59.94    30.00*   25.00    24.00    29.97    23.98
   1920x1080i    60.00    50.00    59.94
   1280x720      60.00    50.00    59.94
   1920x1080     60.00    59.94
   720x576       50.00
DP-1 disconnected (normal left inverted right x axis y axis)
HDMI-2 disconnected (normal left inverted right x axis y axis)
"""

DISCONNECTED_HDMI = """\
Screen 0: minimum 8 x 8, current 1920 x 1080, maximum 32767 x 32767
eDP1 connected primary 1920x1080+0+0 (normal left inverted right x axis y axis) 344mm x 194mm
   1920x1080     60.02*+  59.93    47.99
   1680x1050     59.95    59.88
   1400x1050     59.98
HDMI1 disconnected (normal left inverted right x axis y axis)
   1920x1080 (0x47) 148.500MHz +HSync +VSync
VIRTUAL1 disconnected (normal left inverted right x axis y axis)
"""

ODD_RATES = """\
Screen 0: minimum 320 x 200, current 3840 x 2160, maximum 16384 x 16384
HDMI-A-0 connected 3840x2160+0+0 (normal left inverted right x axis y axis) 1218mm x 685mm
   3840x2160     30.00*   29.97    25.00    24.00    23.98    60.00 +
   2560x1440    143.97   119.88    99.95
   1920x1080    120.00   119.88    60.00    59.94
   1280x720_60.00  59.86
DisplayPort-0 connected (normal left inverted right x axis y axis)
   1920x1080     60.00 +
"""


class ParseTest(unittest.TestCase):
    def test_laptop_and_tv(self):
        outs = display.parse_xrandr(LAPTOP_TV)
        self.assertEqual([o["name"] for o in outs], ["eDP-1", "HDMI-1", "DP-1", "HDMI-2"])
        edp, hdmi = outs[0], outs[1]
        self.assertTrue(edp["primary"] and edp["connected"])
        self.assertEqual(edp["current"], {"mode": "1366x768", "rate": 60.0})
        self.assertEqual(edp["preferred"], {"mode": "1366x768", "rate": 60.0})
        self.assertEqual(edp["geometry"], {"w": 1366, "h": 768, "x": 0, "y": 0})
        self.assertEqual(hdmi["current"], {"mode": "1920x1080", "rate": 30.0})
        self.assertEqual(hdmi["preferred"], {"mode": "1920x1080", "rate": 60.0})   # "60.00 +" spaced flag
        names = [m["mode"] for m in hdmi["modes"]]
        self.assertEqual(names, ["1920x1080", "1920x1080i", "1280x720", "720x576"])   # repeats merged
        self.assertEqual(hdmi["modes"][0]["rates"], [60.0, 50.0, 59.94, 30.0, 25.0, 24.0, 29.97, 23.98])
        self.assertFalse(outs[2]["connected"])
        self.assertIsNone(outs[2]["current"])
        self.assertEqual(outs[2]["modes"], [])

    def test_disconnected_hdmi_with_verbose_lines(self):
        outs = display.parse_xrandr(DISCONNECTED_HDMI)
        hdmi = display.find_output(outs, "HDMI1")
        self.assertFalse(hdmi["connected"])
        self.assertIsNone(hdmi["current"])
        self.assertEqual(hdmi["modes"], [{"mode": "1920x1080", "rates": []}])   # "(0x47) 148.500MHz" ignored
        edp = display.find_output(outs, "eDP1")
        self.assertEqual(edp["current"], {"mode": "1920x1080", "rate": 60.02})

    def test_odd_rates_and_custom_mode_names(self):
        outs = display.parse_xrandr(ODD_RATES)
        tv = outs[0]
        self.assertEqual(tv["current"], {"mode": "3840x2160", "rate": 30.0})
        self.assertEqual(tv["preferred"], {"mode": "3840x2160", "rate": 60.0})
        self.assertEqual(tv["modes"][1]["rates"], [143.97, 119.88, 99.95])
        self.assertEqual(tv["modes"][3]["mode"], "1280x720_60.00")
        self.assertEqual(display.mode_size("1280x720_60.00"), (1280, 720))
        self.assertEqual(display.mode_size("1920x1080i"), (1920, 1080))
        dp = outs[1]
        self.assertTrue(dp["connected"])
        self.assertIsNone(dp["current"])                 # connected but switched off
        self.assertIsNone(dp["geometry"])

    def test_best_rate(self):
        tv = display.parse_xrandr(ODD_RATES)[0]
        self.assertEqual(display.best_rate(tv, tv["modes"][0]), 30.0)      # current
        self.assertEqual(display.best_rate(tv, tv["modes"][1]), 143.97)    # nothing <= 60: highest
        self.assertEqual(display.best_rate(tv, tv["modes"][2]), 60.0)

    def test_empty_and_garbage(self):
        self.assertEqual(display.parse_xrandr(""), [])
        self.assertEqual(display.parse_xrandr("Can't open display\n"), [])

    def test_fake_apply_moves_the_star(self):
        text = display.fake_apply(LAPTOP_TV, "HDMI-1", "1280x720", 50.0)
        hdmi = display.find_output(display.parse_xrandr(text), "HDMI-1")
        self.assertEqual(hdmi["current"], {"mode": "1280x720", "rate": 50.0})
        self.assertEqual(hdmi["geometry"]["w"], 1280)
        edp = display.find_output(display.parse_xrandr(text), "eDP-1")
        self.assertEqual(edp["current"], {"mode": "1366x768", "rate": 60.0})


class RestoreTest(unittest.TestCase):
    def test_plan(self):
        outs = display.parse_xrandr(LAPTOP_TV)
        self.assertIsNone(display.restore_plan(None, outs))
        self.assertEqual(display.restore_plan({"output": "HDMI-1", "mode": "1920x1080", "rate": 60.0}, outs),
                         ("HDMI-1", "1920x1080", 60.0))
        self.assertIsNone(display.restore_plan({"output": "HDMI-1", "mode": "1920x1080", "rate": 30.0}, outs))
        self.assertIsNone(display.restore_plan({"output": "DP-1", "mode": "1920x1080", "rate": 60.0}, outs))
        self.assertIsNone(display.restore_plan({"output": "HDMI-1", "mode": "800x600", "rate": 60.0}, outs))

    def test_save_keeps_other_keys(self):
        with tempfile.TemporaryDirectory() as d:
            Path(d, "home.json").write_text(json.dumps({"style": "bubbles"}))
            display.save_mode("HDMI-1", "1280x720", 60.0, data_dir=d)
            cfg = json.loads(Path(d, "home.json").read_text())
            self.assertEqual(cfg["style"], "bubbles")
            self.assertEqual(display.saved_mode(d), {"output": "HDMI-1", "mode": "1280x720", "rate": 60.0})


class FakeX:
    def __init__(self, text):
        self.text = text
        self.applied = []
        self.saved = []
        self.t = 1000.0

    def apply(self, o, m, r):
        self.applied.append((o, m, r))
        self.text = display.fake_apply(self.text, o, m, r)

    def outputs(self):
        return display.parse_xrandr(self.text)

    def save(self, o, m, r):
        self.saved.append((o, m, r))


class TrialTest(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.x = FakeX(LAPTOP_TV)
        self.trials = display.Trials(Path(self.dir.name) / "trial.json", apply=self.x.apply,
                                     outputs=self.x.outputs, save=self.x.save, clock=lambda: self.x.t)

    def tearDown(self):
        self.dir.cleanup()

    def current(self, name="HDMI-1"):
        return display.find_output(self.x.outputs(), name)["current"]

    def test_times_out_and_reverts(self):
        st = self.trials.start("HDMI-1", "1280x720", 60.0)
        self.assertEqual(st, {"active": True, "remaining": 15.0, "output": "HDMI-1", "mode": "1280x720",
                              "rate": 60.0})
        self.assertEqual(self.current(), {"mode": "1280x720", "rate": 60.0})
        self.x.t += 10
        self.assertFalse(self.trials.check())
        self.assertEqual(self.trials.state()["remaining"], 5.0)
        self.x.t += 5
        self.assertTrue(self.trials.check())
        self.assertEqual(self.current(), {"mode": "1920x1080", "rate": 30.0})
        self.assertEqual(self.trials.state(), {"active": False})
        self.assertEqual(self.x.saved, [])
        self.assertFalse(self.trials.check())

    def test_confirm_saves_and_stops_the_clock(self):
        self.trials.start("HDMI-1", "1280x720", 50.0, seconds=15)
        self.x.t += 3
        self.assertTrue(self.trials.confirm())
        self.assertEqual(self.x.saved, [("HDMI-1", "1280x720", 50.0)])
        self.x.t += 60
        self.assertFalse(self.trials.check())
        self.assertEqual(self.current(), {"mode": "1280x720", "rate": 50.0})
        self.assertFalse(self.trials.confirm())

    def test_manual_revert(self):
        self.trials.start("HDMI-1", "1280x720", 60.0)
        self.assertTrue(self.trials.revert())
        self.assertEqual(self.current(), {"mode": "1920x1080", "rate": 30.0})
        self.assertFalse(self.trials.revert())

    def test_chained_trials_return_to_the_original(self):
        self.trials.start("HDMI-1", "1280x720", 60.0)
        self.x.t += 5
        st = self.trials.start("HDMI-1", "720x576", 50.0)
        self.assertEqual(st["remaining"], 15.0)
        self.trials.revert()
        self.assertEqual(self.current(), {"mode": "1920x1080", "rate": 30.0})

    def test_trial_on_another_output_reverts_the_first(self):
        self.trials.start("HDMI-1", "1280x720", 60.0)
        self.trials.start("eDP-1", "1024x768", 60.0)
        self.assertEqual(self.current(), {"mode": "1920x1080", "rate": 30.0})
        self.assertEqual(self.current("eDP-1"), {"mode": "1024x768", "rate": 60.0})

    def test_rejects_unknown_or_disconnected(self):
        with self.assertRaises(display.DisplayError):
            self.trials.start("DP-1", "1920x1080", 60.0)
        with self.assertRaises(display.DisplayError):
            self.trials.start("HDMI-1", "640x480", 60.0)
        self.assertEqual(self.x.applied, [])
        self.assertEqual(self.trials.state(), {"active": False})

    def test_state_shared_through_the_file(self):
        self.trials.start("HDMI-1", "1280x720", 60.0)
        other = display.Trials(self.trials.path, apply=self.x.apply, outputs=self.x.outputs, save=self.x.save,
                               clock=lambda: self.x.t + 4)
        self.assertEqual(other.state()["remaining"], 11.0)
        self.assertTrue(other.confirm())
        self.assertEqual(self.trials.state(), {"active": False})


class ScreenTest(unittest.TestCase):
    def test_connectors(self):
        f = display.screen_from_connectors
        self.assertTrue(f([("HDMI-A-1", "connected", "enabled", "On")]))
        self.assertFalse(f([("HDMI-A-1", "disconnected", "disabled", "Off"), ("DP-1", "disconnected", "disabled", "Off")]))
        self.assertFalse(f([("HDMI-A-1", "connected", "disabled", "Off")]))          # output switched off
        self.assertFalse(f([("HDMI-A-1", "connected", "enabled", "Off")]))           # DPMS off
        self.assertTrue(f([("HDMI-A-1", "connected", "enabled", "On"), ("DP-1", "connected", "disabled", "Off")]))
        self.assertFalse(f([("eDP-1", "connected", "enabled", "On")], lid_closed=True))
        self.assertTrue(f([("eDP-1", "connected", "enabled", "On"), ("HDMI-A-1", "connected", "enabled", "On")],
                          lid_closed=True))
        self.assertTrue(f([("eDP-1", "connected", "", "")]))                          # driver says nothing

    def test_sysfs_reader(self):
        with tempfile.TemporaryDirectory() as d:
            def conn(name, status, enabled, dpms):
                p = Path(d, "drm", name)
                p.mkdir(parents=True)
                for k, v in (("status", status), ("enabled", enabled), ("dpms", dpms)):
                    Path(p, k).write_text(v + "\n")
            lid = Path(d, "lid", "LID0")
            lid.mkdir(parents=True)
            (lid / "state").write_text("state:      open\n")
            drm, lids = str(Path(d, "drm")), str(Path(d, "lid", "*", "state"))
            self.assertIsNone(display.sysfs_screen(drm, lids))
            conn("card0-eDP-1", "connected", "enabled", "On")
            conn("card0-HDMI-A-1", "disconnected", "disabled", "Off")
            conn("card0-Writeback-1", "unknown", "disabled", "On")
            self.assertTrue(display.sysfs_screen(drm, lids))
            (lid / "state").write_text("state:      closed\n")
            self.assertFalse(display.sysfs_screen(drm, lids))

    def test_xset(self):
        self.assertFalse(display.xset_monitor("DPMS is Enabled\n  Monitor is Off\n"))
        self.assertFalse(display.xset_monitor("  Monitor is in Standby\n") is True)
        self.assertTrue(display.xset_monitor("DPMS is Enabled\n  Monitor is On\n"))
        self.assertIsNone(display.xset_monitor("Server does not have the DPMS Extension"))

    def watch(self, sysfs, xset_text, fake=""):
        self.t = 0.0
        self.xset_calls = 0
        state = {"sysfs": sysfs, "xset": xset_text}

        def xset():
            self.xset_calls += 1
            return state["xset"]
        w = display.ScreenWatch(sysfs=lambda: state["sysfs"], xset=xset, clock=lambda: self.t, fake=fake)
        return w, state

    def test_watch_notifies_on_change_and_limits_xset(self):
        w, state = self.watch(True, "Monitor is On")
        seen = []
        w.listeners.append(seen.append)
        for _ in range(4):                  # 4 polls in 15 s: xset twice (10 s apart)
            w.poll()
            self.t += 5
        self.assertEqual(self.xset_calls, 2)
        self.assertEqual(seen, [])
        state["xset"] = "Monitor is Off"
        self.t += 10
        w.poll()
        self.assertEqual(seen, [False])
        self.assertFalse(w.live)
        state["sysfs"] = False              # sysfs alone decides: no xset needed
        calls = self.xset_calls
        self.t += 20
        w.poll()
        self.assertEqual(self.xset_calls, calls)
        state["sysfs"], state["xset"] = True, "Monitor is On"
        self.t += 10
        w.poll()
        self.assertEqual(seen, [False, True])

    def test_poke_rechecks_and_grants_grace(self):
        w, state = self.watch(False, "Monitor is On")
        seen = []
        w.listeners.append(seen.append)
        w.poll()
        self.assertEqual(seen, [False])
        w.poke()                             # someone pressed a key: count the screen as live for a minute
        self.assertEqual(seen, [False, True])
        self.t += 30
        w.poll()
        self.assertTrue(w.live)
        self.t += 31
        w.poll()
        self.assertFalse(w.live)

    def test_fake_hook(self):
        w, _ = self.watch(True, "Monitor is On", fake="off")
        w.poll()
        self.assertFalse(w.live)
        self.assertEqual(self.xset_calls, 0)
        with tempfile.TemporaryDirectory() as d:
            p = Path(d, "screen")
            p.write_text("on")
            w.fake = str(p)
            w.poll()
            self.assertTrue(w.live)
            p.write_text("off\n")
            w.poll()
            self.assertFalse(w.live)


if __name__ == "__main__":
    unittest.main()
