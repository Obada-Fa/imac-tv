"""Appearance preferences must survive reloads and keep reduced effects usable."""
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import gi
gi.require_version("Gtk", "3.0")
from gi.repository import Gtk
import home
import homescreen
import settings_model
import widgets


class AppearanceTests(unittest.TestCase):
    def test_preferences_round_trip_without_overwriting_ambient_choices(self):
        with tempfile.TemporaryDirectory() as directory:
            widgets.save_keys(directory, {"shapes": "photo+animals", "visualizer": True})
            widgets.save_keys(directory, {"transparency": False, "visual_effects": False})
            cfg = widgets.load_config(directory)
            self.assertFalse(cfg["transparency"])
            self.assertFalse(cfg["visual_effects"])
            self.assertEqual(cfg["shapes"], "photo+animals")
            self.assertTrue(cfg["visualizer"])
            widgets.save_keys(directory, {"visual_effects": True})
            self.assertTrue(widgets.load_config(directory)["visualizer"])

    def test_defaults_validation_and_old_config(self):
        for key in ("transparency", "visual_effects"):
            self.assertTrue(widgets.parse_config({})[key])
            self.assertTrue(widgets.parse_config({key: "false"})[key])
            self.assertFalse(settings_model.validate(key, False))
            with self.assertRaises(ValueError):
                settings_model.validate(key, 0)

    def test_css_parses_at_tv_scales_and_both_preferences(self):
        for scale in (2 / 3, 1, 2):
            for transparency in (True, False):
                for effects in (True, False):
                    provider = Gtk.CssProvider()
                    provider.load_from_data(home.build_css(scale, {
                        "transparency": transparency, "visual_effects": effects}))

    def test_reduced_effects_stop_continuous_timer_but_allow_chrome_to_settle(self):
        screen = SimpleNamespace(cfg={"visual_effects": False}, idle=SimpleNamespace(ambient=False),
                                 chrome=1.0, canvas=mock.Mock(), widget_fading=lambda: False)
        screen.canvas.fading.return_value = False
        self.assertFalse(homescreen.AmbientHomeScreen.needs_anim(screen))
        screen.idle.ambient = True
        self.assertTrue(homescreen.AmbientHomeScreen.needs_anim(screen))
        screen.chrome = 0.0
        self.assertFalse(homescreen.AmbientHomeScreen.needs_anim(screen))
        screen.style = "network"
        screen.active = lambda: True
        screen.music_active = lambda: False
        self.assertEqual(homescreen.AmbientHomeScreen.field_target(screen), 0.0)
