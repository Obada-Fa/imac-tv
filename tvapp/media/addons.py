"""Stremio add-on protocol over HTTP with a small TTL cache."""
import hashlib
import json
import re
import shutil
import threading
import time
import urllib.error
import urllib.request
from urllib.parse import quote

from . import config

TTL = {"catalog": 6 * 3600, "meta": 6 * 3600, "stream": 30 * 60, "subtitles": 6 * 3600}
TYPES = ("movie", "series")


class AddonError(Exception):
    pass


def clear_cache():
    """Drop cached add-on responses; stream lists embed the TorBox key (or its absence)."""
    shutil.rmtree(config.data_dir() / "cache" / "addons", ignore_errors=True)


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
        raise AddonError(config.redact(f"Add-on request failed: {exc}")) from None
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
    if re.search(r"[\u0600-\u06FF]", query or ""):
        return search_arabic(kind, query)
    url = f"{config.load()['addons']['meta']}/catalog/{_path(kind, 'top')}/search={quote(query, safe='')}.json"
    return _fetch(url, TTL["catalog"]).get("metas", [])


def search_arabic(kind, query):
    """Cinemeta only knows English titles: an Arabic query is looked up on TMDB/Wikidata, then shown from Cinemeta."""
    from . import arabic
    import concurrent.futures

    def one(imdb_id):
        try:
            item = meta(kind, imdb_id)
        except AddonError:
            return None
        if not item or not item.get("id") or item.get("type") not in (None, kind):
            return None
        return {k: item.get(k) for k in ("id", "type", "name", "poster", "releaseInfo", "year", "genres")}
    ids = arabic.find_titles(kind, query.strip())
    if not ids:
        return []
    with concurrent.futures.ThreadPoolExecutor(max_workers=min(8, len(ids))) as pool:
        found = [m for m in pool.map(one, ids) if m]
    for m in found:     # searched in Arabic: these shows play their Arabic releases unless told otherwise
        arabic.remember_language(m["id"], "dub", soft=True)
    return found


def meta(kind, item_id):
    _check_type(kind)
    url = f"{config.load()['addons']['meta']}/meta/{_path(kind, item_id)}.json"
    return _fetch(url, TTL["meta"]).get("meta") or {}


def running_number(videos, video_id):
    """Episode number counted across all seasons (specials left out), e.g. One Piece S22E10 → 1095."""
    parts = video_id.split(":")
    if len(parts) != 3 or not isinstance(videos, list):
        return None
    try:
        season, episode = int(parts[1]), int(parts[2])
    except ValueError:
        return None
    eps = sorted({(v.get("season"), v.get("episode")) for v in videos if isinstance(v, dict)
                  and isinstance(v.get("season"), int) and isinstance(v.get("episode"), int) and v["season"] > 0})
    return eps.index((season, episode)) + 1 if (season, episode) in eps else None


def aired_years(videos, video_id):
    """{"season": (first, last), "show": (first, last)} air years from Cinemeta's episode dates."""
    parts = video_id.split(":")
    if len(parts) != 3 or not isinstance(videos, list):
        return {}
    out, show = {}, []
    for v in videos:
        if not isinstance(v, dict) or not isinstance(v.get("season"), int) or v["season"] < 1:
            continue
        date = str(v.get("released") or v.get("firstAired") or "")[:4]
        if not date.isdigit():
            continue
        show.append(int(date))
        if str(v["season"]) == parts[1]:
            out.setdefault("season", []).append(int(date))
    if show:
        out["show"] = (min(show), max(show))
    if out.get("season"):
        out["season"] = (min(out["season"]), max(out["season"]))
    return out


def _arabic_streams(kind, video_id, box):
    from . import arabic
    try:
        if not arabic.enabled():
            return
        imdb_id = video_id.split(":")[0]
        try:
            item = meta(kind, imdb_id)
        except AddonError:
            item = {}
        year = str(item.get("releaseInfo") or item.get("year") or "")[:4]
        box.extend(arabic.streams(kind, video_id, item.get("name") or "", year if year.isdigit() else "",
                                  running_number(item.get("videos"), video_id), aired_years(item.get("videos"), video_id)))
    except Exception:   # an Arabic site problem must never break normal sources
        pass


def streams(kind, video_id):
    _check_type(kind)
    found, failure, extra = [], None, []
    side = threading.Thread(target=_arabic_streams, args=(kind, video_id, extra), daemon=True)
    side.start()
    for base in config.load()["addons"]["streams"]:
        try:
            found += _fetch(f"{base.rstrip('/')}/stream/{_path(kind, video_id)}.json", TTL["stream"]).get("streams", [])
        except AddonError as exc:
            failure = exc
    side.join(20)
    found = list(extra) + found
    if not found and failure:
        raise failure
    return found


def subtitles(kind, video_id, extra=None):
    _check_type(kind)
    tail = "/" + quote(extra, safe="=&") if extra else ""
    url = f"{config.load()['addons']['subtitles']}/subtitles/{_path(kind, video_id)}{tail}.json"
    return _fetch(url, TTL["subtitles"]).get("subtitles", [])
