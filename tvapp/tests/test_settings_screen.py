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


if __name__ == "__main__":
    unittest.main()
