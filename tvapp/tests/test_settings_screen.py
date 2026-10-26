from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import display  # noqa: E402
from tests.test_display import LAPTOP_TV, ODD_RATES  # noqa: E402
from widgets import settings  # noqa: E402


class SettingsHelpersTest(unittest.TestCase):
    def test_modes_skip_interlaced_and_tag(self):
        hdmi = display.find_output(display.parse_xrandr(LAPTOP_TV), "HDMI-1")
        modes = settings.tv_modes(hdmi)
        self.assertEqual([m for m, _r, _t in modes], ["1920x1080", "1280x720", "720x576"])
        self.assertEqual(modes[0], ("1920x1080", 30.0, ["Current", "Recommended"]))
        self.assertEqual(modes[1], ("1280x720", 60.0, []))

    def test_rates_for_current_mode(self):
        tv = display.parse_xrandr(ODD_RATES)[0]
        self.assertEqual(settings.tv_rates(tv), [(60.0, False), (30.0, True), (29.97, False), (25.0, False),
                                                 (24.0, False), (23.98, False)])
        off = display.parse_xrandr(ODD_RATES)[1]
        self.assertEqual(settings.tv_rates(off), [])

    def test_labels(self):
        self.assertEqual(settings.fmt_mode("1920x1080"), "1920 × 1080")
        self.assertEqual(settings.fmt_mode("1280x720_60.00"), "1280 × 720")
        self.assertEqual(settings.fmt_rate(60.0), "60 Hz")
        self.assertEqual(settings.fmt_rate(59.94), "59.94 Hz")
        self.assertEqual(settings.output_label({"name": "eDP-1"}), "Built-in screen · eDP-1")
        self.assertEqual(settings.chunk([1, 2, 3, 4, 5, 6, 7], 5), [[1, 2, 3, 4, 5], [6, 7]])

    def test_ui_scale(self):
        import home
        self.assertEqual(home.ui_scale(1080), 1.0)
        self.assertAlmostEqual(home.ui_scale(720), 2 / 3)
        self.assertEqual(home.ui_scale(2160), 2.0)
        self.assertEqual(home.ui_scale(300), 0.5)


class CategoryNavigationTest(unittest.TestCase):
    def setUp(self):
        import tempfile
        from unittest import mock
        from gi.repository import Gtk
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        patch = mock.patch.object(display, 'DATA', Path(self.tmp.name))
        patch.start()
        self.addCleanup(patch.stop)
        patch_media = mock.patch.dict('os.environ', {'IMACTV_DATA_DIR': self.tmp.name})
        patch_media.start()
        self.addCleanup(patch_media.stop)
        from gi.repository import Gdk
        provider = Gtk.CssProvider()
        provider.load_from_data(settings.settings_css(2 / 3).encode())
        Gtk.StyleContext.add_provider_for_screen(Gdk.Screen.get_default(), provider, 600)
        self.shell = Gtk.Window()
        self.shell.px = lambda v: round(v * 2 / 3)
        self.shell.tops = {}
        self.shell.sheet = None
        self.shell.toast = mock.Mock()
        self.shell.show_pairing = mock.Mock()
        self.screen = settings.SettingsScreen(self.shell)
        self.shell.current = lambda: self.screen
        self.shell.focus_screen = lambda screen: (screen.last_focus or screen.first_focus([])).grab_focus()
        self.shell.add(self.screen)
        self.shell.set_default_size(1280, 720)
        self.shell.show_all()
        self.drain()
        self.addCleanup(self.shell.destroy)

    def drain(self):
        from gi.repository import Gtk
        while Gtk.events_pending():
            Gtk.main_iteration_do(False)

    def test_rail_content_back_and_refresh_focus(self):
        screen = self.screen
        self.assertEqual(screen.category, 'Appearance')
        screen.tabs[1].grab_focus()
        self.drain()
        self.assertEqual(screen.category, 'Ambient')
        self.assertTrue(screen.handle_key('Right'))
        self.drain()
        target = self.shell.get_focus()
        self.assertIn(target, [b for row in screen.rows for b in row])
        key = target.sel_key
        screen.loaded([])
        self.drain()
        self.assertEqual(self.shell.get_focus().sel_key, key)
        self.assertTrue(screen.handle_key('Escape'))
        self.assertIs(self.shell.get_focus(), screen.tabs[1])
        screen.loaded([])
        self.assertIs(self.shell.get_focus(), screen.tabs[1])

    def test_bubbles_preserves_values_and_categories_render(self):
        import widgets
        widgets.save_keys(display.DATA, {'style': 'bubbles', 'node_shape': 'octagon', 'visualizer': True})
        self.screen.render()
        node = next(b for row in self.screen.rows for b in row if b.sel_key == ('setting', 'node_shape'))
        self.assertFalse(node.get_sensitive())
        for category in settings.settings_model.CATEGORIES:
            self.screen.select_category(category)
            self.drain()
        self.assertLessEqual(self.screen.get_preferred_height().minimum_height, 720)
        cfg = widgets.load_config(display.DATA)
        self.assertEqual(cfg['node_shape'], 'octagon')
        self.assertTrue(cfg['visualizer'])

    def test_remembers_control_across_categories_and_rebuild(self):
        screen = self.screen
        screen.enter_category('Playback')
        self.drain()
        target = screen.rows[-1][0]
        target.grab_focus()
        screen.track_focus(target)
        screen.reveal(target)
        self.drain()
        screen.remember()
        key = target.sel_key
        screen.enter_category('Ambient')
        screen.enter_category('Playback')
        self.drain()
        self.assertEqual(self.shell.get_focus().sel_key, key)
        screen.track_focus(self.shell.get_focus())
        screen.remember()
        restored = settings.SettingsScreen(self.shell)
        self.assertEqual(restored.category, 'Playback')
        self.assertEqual(restored.last_focus.sel_key, key)
        restored.destroy()


if __name__ == "__main__":
    unittest.main()
