import json
from pathlib import Path
import sys
import tempfile
import threading
import unittest
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import weather  # noqa: E402

GEO = {"city": "Utrecht", "latitude": "52.0907", "longitude": "5.1214", "ip": "1.2.3.4", "country": "NL"}


def forecast(code=3, is_day=1, hourly=None):
    times = ["2026-10-23T%02d:00" % h for h in range(24)]
    wc = [3] * 24
    pr = [0.0] * 24
    for h, (c, mm) in (hourly or {}).items():
        wc[h], pr[h] = c, mm
    return {"current": {"time": "2026-10-23T17:00", "temperature_2m": 11.4, "weather_code": code, "is_day": is_day},
            "hourly": {"time": times, "weather_code": wc, "precipitation": pr},
            "daily": {"temperature_2m_max": [13.0], "temperature_2m_min": [7.5]}}


class WeatherTest(unittest.TestCase):
    def setUp(self):
        self._t = tempfile.TemporaryDirectory()
        self.dir = Path(self._t.name)
        self.urls = []

    def tearDown(self):
        self._t.cleanup()

    def run_refresh(self, fc=None, geo=GEO, **kw):
        def get(url, timeout=15):
            self.urls.append(url)
            if "geojs" in url:
                if geo is None:
                    raise OSError("down")
                return geo
            return fc or forecast()
        with mock.patch.object(weather, "_getjson", get):
            return weather._refresh(self.dir, kw.get("location"), kw.get("force", False))

    def test_none_when_empty(self):
        self.assertIsNone(weather.current(self.dir))

    def test_codes(self):
        self.assertEqual(weather.describe(0, True), ("Clear", "☀"))
        self.assertEqual(weather.describe(0, False)[1], "🌙")
        self.assertEqual(weather.describe(61)[0], "Light rain")
        self.assertEqual(weather.describe(95)[1], "⛈")
        self.assertEqual(weather.describe(999)[0], "Unknown")

    def test_full_result_and_cache(self):
        w = self.run_refresh(forecast(code=2))
        self.assertEqual((w["temp"], w["label"], w["high"], w["low"], w["place"], w["is_day"]),
                         (11.4, "Partly cloudy", 13.0, 7.5, "Utrecht", True))
        self.assertEqual(weather.current(self.dir), w)

    def test_next_rain_and_clearing(self):
        w = self.run_refresh(forecast(code=3, hourly={20: (61, 0.5), 21: (61, 0.5)}))
        self.assertEqual(w["next"], "Light rain from 20:00")
        w = self.run_refresh(forecast(code=61, hourly={18: (61, 0.5), 19: (61, 0.3)}), force=True)
        self.assertEqual(w["next"], "Clearing by 20:00")
        w = self.run_refresh(forecast(), force=True)
        self.assertEqual(w["next"], "")

    def test_throttle_30min(self):
        self.run_refresh()
        n = len(self.urls)
        self.run_refresh()
        self.assertEqual(len(self.urls), n)
        d = json.loads((self.dir / "weather.json").read_text())
        d["weather"]["updated"] -= 31 * 60
        (self.dir / "weather.json").write_text(json.dumps(d))
        self.run_refresh()
        self.assertEqual(len([u for u in self.urls if "open-meteo" in u]), 2)
        self.assertEqual(len([u for u in self.urls if "geojs" in u]), 1)  # location cached 7 days

    def test_location_override_skips_geo(self):
        w = self.run_refresh(location={"place": "Oslo", "lat": 59.9139, "lon": 10.7522})
        self.assertFalse([u for u in self.urls if "geojs" in u])
        self.assertEqual(w["place"], "Oslo")
        self.assertIn("latitude=59.91", self.urls[0])

    def test_geo_failure_defaults_rotterdam(self):
        w = self.run_refresh(geo=None)
        self.assertEqual(w["place"], "Rotterdam")
        self.assertIn("latitude=51.92", self.urls[-1])

    def test_only_place_latlon_stored(self):
        self.run_refresh()
        d = json.loads((self.dir / "weather.json").read_text())
        self.assertEqual(d["location"], {"place": "Utrecht", "lat": 52.09, "lon": 5.12})
        self.assertNotIn("1.2.3.4", json.dumps(d))

    def test_forecast_failure_returns_none_keeps_cache(self):
        w = self.run_refresh()
        with mock.patch.object(weather, "_getjson", side_effect=OSError):
            self.assertIsNone(weather._refresh(self.dir, None, True))
        self.assertEqual(weather.current(self.dir), w)

    def test_async_thread(self):
        got, ev = [], threading.Event()
        def done(r):
            got.append(r)
            ev.set()
        with mock.patch.object(weather, "_getjson", lambda u, timeout=15: GEO if "geojs" in u else forecast()):
            weather.refresh_async(self.dir, on_done=done)
            self.assertTrue(ev.wait(10))
        self.assertEqual(got[0]["place"], "Utrecht")


if __name__ == "__main__":
    unittest.main()
