"""Stremio add-on protocol over HTTP with a small TTL cache."""
import hashlib
import json
import time
import urllib.error
import urllib.request
from urllib.parse import quote

from . import config

TTL = {"catalog": 6 * 3600, "meta": 6 * 3600, "stream": 30 * 60, "subtitles": 6 * 3600}
TYPES = ("movie", "series")


class AddonError(Exception):
    pass


def _check_type(kind):
    if kind not in TYPES:
        raise AddonError("Unknown media type")


def _fetch(url, ttl):
    cache = config.data_dir() / "cache" / "addons"
    path = cache / (hashlib.sha256(url.encode()).hexdigest() + ".json")
    try:
        if time.time() - path.stat().st_mtime < ttl:
            return json.loads(path.read_text())
    except (OSError, ValueError):
        pass
    request = urllib.request.Request(url, headers={"User-Agent": "imactv/1.0", "Accept": "application/json"})
    try:
        with urllib.request.urlopen(request, timeout=8) as response:
            data = json.loads(response.read(8_000_000))
    except (urllib.error.URLError, OSError, ValueError) as exc:
        raise AddonError(f"Add-on request failed: {exc}") from exc
    try:
        cache.mkdir(parents=True, exist_ok=True)
        temp = path.with_suffix(".tmp")
        temp.write_text(json.dumps(data))
        temp.replace(path)
    except OSError:
        pass
    return data


def _path(*parts):
    return "/".join(quote(str(p), safe=":") for p in parts)


def catalog(kind, catalog_id="top", genre=None, skip=0):
    _check_type(kind)
    extra = []
    if genre:
        extra.append("genre=" + quote(genre, safe=""))
    if skip:
        extra.append(f"skip={int(skip)}")
    tail = "/" + "&".join(extra) if extra else ""
    url = f"{config.load()['addons']['meta']}/catalog/{_path(kind, catalog_id)}{tail}.json"
    return _fetch(url, TTL["catalog"]).get("metas", [])


def search(kind, query):
    _check_type(kind)
    url = f"{config.load()['addons']['meta']}/catalog/{_path(kind, 'top')}/search={quote(query, safe='')}.json"
    return _fetch(url, TTL["catalog"]).get("metas", [])


def meta(kind, item_id):
    _check_type(kind)
    url = f"{config.load()['addons']['meta']}/meta/{_path(kind, item_id)}.json"
    return _fetch(url, TTL["meta"]).get("meta") or {}


def streams(kind, video_id):
    _check_type(kind)
    found, failure = [], None
    for base in config.load()["addons"]["streams"]:
        try:
            found += _fetch(f"{base.rstrip('/')}/stream/{_path(kind, video_id)}.json", TTL["stream"]).get("streams", [])
        except AddonError as exc:
            failure = exc
    if not found and failure:
        raise failure
    return found


def subtitles(kind, video_id, extra=None):
    _check_type(kind)
    tail = "/" + quote(extra, safe="=&") if extra else ""
    url = f"{config.load()['addons']['subtitles']}/subtitles/{_path(kind, video_id)}{tail}.json"
    return _fetch(url, TTL["subtitles"]).get("subtitles", [])
