import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

try:
    from widgets import weather_widget as ww
except Exception:  # pragma: no cover
    ww = None

W = {"temp": 12.4, "icon": "☁", "place": "Oslo", "next": "Rain from 23:00"}


@unittest.skipIf(ww is None, "GTK bindings unavailable")
class AmbientWeatherTests(unittest.TestCase):
    def test_compact_text(self):
        self.assertEqual(ww.compact_text(W), "☁ 12° · rain from 23:00")
        self.assertEqual(ww.compact_text({"temp": 3, "icon": "☀"}), "☀ 3°")
        self.assertIsNone(ww.compact_text(None))
        self.assertIsNone(ww.compact_text({"icon": "x"}))

    def test_visibility(self):
        v = ww.ambient_line_visible
        self.assertTrue(v(True, False, True))       # ambient, no music
        self.assertFalse(v(True, True, True))       # music hides it
        self.assertFalse(v(False, False, True))     # normal mode: the Home row, not the line
        self.assertFalse(v(True, False, False))     # no data yet


if __name__ == "__main__":
    unittest.main()
