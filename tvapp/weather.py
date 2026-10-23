"""Open-Meteo weather with keyless IP geolocation. Only place + rounded lat/lon are ever stored (never the IP)."""
import json
import os
from pathlib import Path
import sys
import threading
import time
import urllib.parse
import urllib.request

UA = "imactv/1.0"
DEFAULT_LOCATION = {"place": "Rotterdam", "lat": 51.92, "lon": 4.48}
GEO_URL = "https://get.geojs.io/v1/ip/geo.json"
GEO_EVERY = 7 * 86400
WEATHER_EVERY = 30 * 60
_lock = threading.Lock()

# WMO code -> (label, day icon, night icon)
_WMO = {
    0: ("Clear", "☀", "🌙"), 1: ("Mostly clear", "☀", "🌙"), 2: ("Partly cloudy", "⛅", "☁"), 3: ("Cloudy", "☁", "☁"),
    45: ("Fog", "🌫", "🌫"), 48: ("Freezing fog", "🌫", "🌫"),
    51: ("Light drizzle", "🌧", "🌧"), 53: ("Drizzle", "🌧", "🌧"), 55: ("Heavy drizzle", "🌧", "🌧"),
    56: ("Freezing drizzle", "🌧", "🌧"), 57: ("Freezing drizzle", "🌧", "🌧"),
    61: ("Light rain", "🌧", "🌧"), 63: ("Rain", "🌧", "🌧"), 65: ("Heavy rain", "🌧", "🌧"),
    66: ("Freezing rain", "🌧", "🌧"), 67: ("Freezing rain", "🌧", "🌧"),
    71: ("Light snow", "❄", "❄"), 73: ("Snow", "❄", "❄"), 75: ("Heavy snow", "❄", "❄"), 77: ("Snow grains", "❄", "❄"),
    80: ("Light showers", "🌧", "🌧"), 81: ("Showers", "🌧", "🌧"), 82: ("Heavy showers", "🌧", "🌧"),
    85: ("Snow showers", "❄", "❄"), 86: ("Heavy snow showers", "❄", "❄"),
    95: ("Thunderstorm", "⛈", "⛈"), 96: ("Thunderstorm, hail", "⛈", "⛈"), 99: ("Thunderstorm, hail", "⛈", "⛈"),
}


def _log(*a):
    print("weather:", *a, file=sys.stderr)


def describe(code, is_day=True):
    label, d, n = _WMO.get(code, ("Unknown", "☁", "☁"))
    return label, d if is_day else n


def _wet(code):
    return code in (51, 53, 55, 56, 57, 61, 63, 65, 66, 67, 71, 73, 75, 77, 80, 81, 82, 85, 86, 95, 96, 99)


def _next_line(cur_code, hours):
    """hours: [(HH:MM, code, precip_mm)] for the next few hours."""
    if not hours:
        return ""
    raining = _wet(cur_code)
    for t, code, mm in hours:
        wet = _wet(code) or mm >= 0.2
        if not raining and wet:
            label = describe(code)[0].lower() if _wet(code) else "rain"
            return "%s from %s" % (label.capitalize(), t)
        if raining and not wet:
            return "Clearing by %s" % t
    return ""


def _path(data_dir):
    return Path(data_dir) / "weather.json"


def _load(data_dir):
    try:
        d = json.loads(_path(data_dir).read_text())
        return d if isinstance(d, dict) else {}
    except Exception:
        return {}


def _save(data_dir, d):
    p = _path(data_dir)
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(d))
    os.replace(tmp, p)


def current(data_dir):
    w = _load(data_dir).get("weather")
    if not isinstance(w, dict) or "temp" not in w:
        return None
    return w


def _getjson(url, timeout=15):
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read())


def _clean_loc(loc):
    return {"place": str(loc.get("place") or "")[:60], "lat": round(float(loc["lat"]), 2), "lon": round(float(loc["lon"]), 2)}


def _locate(data_dir, store):
    loc = store.get("location")
    if isinstance(loc, dict) and time.time() - float(store.get("located", 0)) < GEO_EVERY:
        try:
            return _clean_loc(loc)
        except Exception:
            pass
    try:
        g = _getjson(GEO_URL)
        new = _clean_loc({"place": g.get("city") or g.get("region") or g.get("country") or "Home",
                          "lat": g["latitude"], "lon": g["longitude"]})
    except Exception as e:
        _log("geolocation failed:", e)
        if isinstance(loc, dict):
            try:
                return _clean_loc(loc)
            except Exception:
                pass
        return dict(DEFAULT_LOCATION)
    store["location"], store["located"] = new, time.time()
    return new


def _fetch(loc):
    q = urllib.parse.urlencode({
        "latitude": loc["lat"], "longitude": loc["lon"],
        "current": "temperature_2m,weather_code,is_day",
        "hourly": "precipitation,weather_code",
        "daily": "temperature_2m_max,temperature_2m_min",
        "timezone": "auto", "forecast_days": 2})
    d = _getjson("https://api.open-meteo.com/v1/forecast?" + q)
    c = d["current"]
    code, is_day = int(c["weather_code"]), bool(c.get("is_day", 1))
    label, icon = describe(code, is_day)
    now = str(c.get("time", ""))[:13]  # YYYY-MM-DDTHH
    h = d.get("hourly") or {}
    times = h.get("time") or []
    hours = []
    for i, t in enumerate(times):
        if t[:13] > now and len(hours) < 6:
            hours.append((t[11:16], int(h["weather_code"][i] or 0), float(h["precipitation"][i] or 0)))
    return {"temp": float(c["temperature_2m"]), "code": code, "label": label, "icon": icon, "is_day": is_day,
            "high": float(d["daily"]["temperature_2m_max"][0]), "low": float(d["daily"]["temperature_2m_min"][0]),
            "place": loc["place"], "next": _next_line(code, hours), "updated": time.time()}


def refresh_async(data_dir, location=None, force=False, on_done=None):
    def run():
        res = None
        try:
            res = _refresh(data_dir, location, force)
        except Exception as e:
            _log("refresh failed:", e)
            res = None
        if on_done:
            try:
                on_done(res)
            except Exception as e:
                _log("on_done failed:", e)
    threading.Thread(target=run, daemon=True, name="weather-refresh").start()


def _refresh(data_dir, location, force):
    if not _lock.acquire(blocking=False):
        return None
    try:
        store = _load(data_dir)
        w = store.get("weather") if isinstance(store.get("weather"), dict) else None
        if location:
            loc = _clean_loc(location)
        else:
            loc = _locate(data_dir, store)
        fresh = w and time.time() - float(w.get("updated", 0)) < WEATHER_EVERY
        if fresh and not force and w.get("place") == loc["place"]:
            return w
        if not location:
            _save(data_dir, store)
        try:
            w = _fetch(loc)
        except Exception as e:
            _log("forecast failed:", e)
            return None
        store["weather"] = w
        _save(data_dir, store)
        return w
    finally:
        _lock.release()
