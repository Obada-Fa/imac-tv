"""Arabic subtitles from SubDL and Subsource, the two sites most Arabic subtitles are on.

The OpenSubtitles add-on has few Arabic subtitles, so when a key for either site is set (TV Settings →
Connections, or the phone's Settings) a playing title also gets the Arabic subtitles that best fit the
release being played: the one whose release name shares the most words with it. Both sites hand out
zip files; the right file is pulled out (by episode for season packs), turned into UTF-8 (Arabic
subtitles are often Windows-1256) and kept in $DATA/cache/subs for mpv. Keys live in arabic.json (0600).
Nothing runs unless something plays, and API answers are cached.
"""
import hashlib
import io
import json
import re
import time
import urllib.error
import urllib.parse
import urllib.request
import zipfile

from . import arabic, config

SUBDL_API = "https://api.subdl.com/api/v1/subtitles"
SUBDL_DL = "https://dl.subdl.com"
SUBSOURCE_API = "https://api.subsource.net/api/v1"
UA = "NexusTV/1.0"
TIMEOUT = 8
TTL = 6 * 3600
SUB_EXT = (".srt", ".ass", ".ssa", ".vtt", ".sub")
MAX_ZIP = 20_000_000
PROVIDERS = ("subdl", "subsource")
NAMES = {"subdl": "SubDL", "subsource": "Subsource"}
STOP = {"the", "a", "of", "and", "mkv", "mp4", "avi", "x264", "h264", "x265", "h265", "hevc", "aac", "ac3",
        "ddp5", "dd5", "1", "0", "web", "dl", "rip"}


class SubtitleError(Exception):
    pass


def key(provider):
    value = arabic.load_creds().get(provider + "_key")
    return value.strip() if isinstance(value, str) else ""


def status():
    return {p: bool(key(p)) for p in PROVIDERS}


def available():
    return any(key(p) for p in PROVIDERS)


def wanted(chosen):
    """Arabic subtitles are only fetched for normal releases: an Arabic dub, Arabic-subtitled or Arabic
    release from ArabP2P already has what's needed."""
    return available() and not (chosen or {}).get("site")


# ---------- helpers ----------
def _cache_dir():
    path = config.data_dir() / "cache" / "subs"
    path.mkdir(parents=True, exist_ok=True)
    return path


def _get(url, headers=None, raw=False, ttl=TTL):
    path = _cache_dir() / (hashlib.sha256(url.encode()).hexdigest() + (".bin" if raw else ".json"))
    try:
        if time.time() - path.stat().st_mtime < ttl:
            data = path.read_bytes()
            return data if raw else json.loads(data)
    except (OSError, ValueError):
        pass
    req = urllib.request.Request(url, headers=dict({"User-Agent": UA, "Accept": "*/*"}, **(headers or {})))
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
            data = r.read(MAX_ZIP + 1)
    except urllib.error.HTTPError as exc:
        if exc.code in (401, 403):
            raise SubtitleError("key rejected") from None
        if exc.code == 404:
            return b"" if raw else {}
        raise SubtitleError(f"HTTP {exc.code}") from None
    except (urllib.error.URLError, OSError) as exc:
        raise SubtitleError(str(exc)) from None
    if len(data) > MAX_ZIP:
        raise SubtitleError("file too large")
    try:
        result = data if raw else json.loads(data)
    except ValueError:
        raise SubtitleError("bad answer") from None
    try:
        tmp = path.with_suffix(".tmp")
        tmp.write_bytes(data)
        tmp.replace(path)
    except OSError:
        pass
    return result


def parse_video_id(kind, video_id):
    """("tt123", season, episode); season/episode are None for movies."""
    parts = str(video_id).split(":")
    if kind == "series" and len(parts) == 3:
        try:
            return parts[0], int(parts[1]), int(parts[2])
        except ValueError:
            pass
    return parts[0], None, None


def episode_of(name):
    """(season, episode) named in a release/file name, or None."""
    m = re.search(r"(?<![a-z0-9])s(\d{1,2})[ ._-]?e(\d{1,4})(?!\d)", name, re.I) \
        or re.search(r"(?<!\d)(\d{1,2})x(\d{1,4})(?!\d)", name)
    return (int(m.group(1)), int(m.group(2))) if m else None


def episode_range(name):
    """S01E01-E03 / S01E01-03 → (season, first, last), else None."""
    m = re.search(r"s(\d{1,2})[ ._-]?e(\d{1,4})[ ._-]*-[ ._-]*e?(\d{1,4})(?!\d)", name, re.I)
    return (int(m.group(1)), int(m.group(2)), int(m.group(3))) if m else None


def fits_episode(name, season, episode, pack_ok=False):
    if season is None:
        return True
    rng = episode_range(name)
    if rng:
        return rng[0] == season and rng[1] <= episode <= rng[2]
    found = episode_of(name)
    if found:
        return found == (season, episode)
    return pack_ok


def tokens(name):
    name = re.sub(r"\.(mkv|mp4|avi|srt|ass)$", "", (name or "").lower())
    return {t for t in re.split(r"[^a-z0-9]+", name) if t and t not in STOP}


def similarity(release, candidate):
    a, b = tokens(release), tokens(candidate)
    if not a or not b:
        return 0.0
    score = len(a & b) / len(a | b)
    group = re.search(r"-([a-z0-9]+)$", re.sub(r"\.(mkv|mp4|avi)$", "", (release or "").lower()))
    if group and group.group(1) in b:
        score += 0.3                    # same release group: timings almost always match
    return score


def to_utf8(data):
    if data.startswith(b"\xef\xbb\xbf"):
        data = data[3:]
    if data.startswith((b"\xff\xfe", b"\xfe\xff")):
        return data.decode("utf-16").encode()
    try:
        data.decode("utf-8")
        return data
    except UnicodeDecodeError:
        return data.decode("cp1256", errors="replace").encode()


def pick_file(blob, season, episode):
    """(filename, bytes) of the subtitle in a zip (or a bare subtitle file) that fits the episode."""
    if not blob.startswith(b"PK"):
        return ("subtitle.srt", blob) if blob.strip() else None
    try:
        archive = zipfile.ZipFile(io.BytesIO(blob))
    except zipfile.BadZipFile:
        return None
    names = [n for n in archive.namelist() if n.lower().endswith(SUB_EXT) and not n.endswith("/")
             and archive.getinfo(n).file_size < 5_000_000]
    if season is not None and len(names) > 1:
        names = [n for n in names if fits_episode(n.rsplit("/", 1)[-1], season, episode)]
    if not names:
        return None
    names.sort(key=lambda n: (not n.lower().endswith(".srt"), n))
    return names[0], archive.read(names[0])


# ---------- providers ----------
def subdl_candidates(imdb_id, season, episode):
    k = key("subdl")
    if not k:
        return []
    q = {"api_key": k, "imdb_id": imdb_id, "type": "tv" if season is not None else "movie",
         "languages": "AR", "subs_per_page": 30}
    if season is not None:
        q.update(season_number=season, episode_number=episode)
    data = _get(SUBDL_API + "?" + urllib.parse.urlencode(q))
    out = []
    for s in (data or {}).get("subtitles") or []:
        if not isinstance(s, dict) or not isinstance(s.get("url"), str):
            continue
        lang = str(s.get("language") or s.get("lang") or "").lower()
        if lang and lang not in ("ar", "arabic"):
            continue
        name = str(s.get("release_name") or s.get("name") or "")
        pack = bool(s.get("full_season")) or (season is not None and not episode_of(name)
                                              and s.get("episode") in (None, 0))
        if season is not None and not fits_episode(name, season, episode, pack_ok=True):
            ep = s.get("episode")
            if not (isinstance(ep, int) and ep == episode):
                continue
        url = s["url"] if s["url"].startswith("http") else SUBDL_DL + s["url"]
        out.append({"provider": "subdl", "name": name, "url": url, "headers": {}, "pack": pack})
    return out


def subsource_candidates(imdb_id, season, episode):
    k = key("subsource")
    if not k:
        return []
    headers = {"X-API-Key": k, "Accept": "application/json"}
    q = {"searchType": "imdb", "imdb": imdb_id}
    if season is not None:
        q["season"] = season
    found = (_get(f"{SUBSOURCE_API}/movies/search?" + urllib.parse.urlencode(q), headers) or {}).get("data") or []
    found = [m for m in found if isinstance(m, dict) and m.get("movieId")
             and (season is None or m.get("season") in (None, season))]
    if not found:
        return []
    movie_id = found[0]["movieId"]
    q = {"movieId": movie_id, "language": "arabic", "limit": 100}
    subs = (_get(f"{SUBSOURCE_API}/subtitles?" + urllib.parse.urlencode(q), headers) or {}).get("data") or []
    out = []
    for s in subs:
        if not isinstance(s, dict) or not s.get("subtitleId"):
            continue
        info = s.get("releaseInfo")
        names = [str(x) for x in info] if isinstance(info, list) else [str(info or "")]
        name = max(names, key=len) if names else ""
        if season is not None and not any(fits_episode(n, season, episode, pack_ok=(s.get("files") or 1) > 1) for n in names):
            continue
        out.append({"provider": "subsource", "name": name, "names": names, "headers": headers,
                    "url": f"{SUBSOURCE_API}/subtitles/{int(s['subtitleId'])}/download",
                    "pack": (s.get("files") or 1) > 1})
    return out


def rank(candidates, release):
    def score(c):
        best = max(similarity(release, n) for n in (c.get("names") or [c["name"]]))
        return (best - (0.05 if c.get("pack") else 0), c["provider"] == "subdl")
    return sorted(candidates, key=score, reverse=True)


def find(kind, video_id, release="", limit=2):
    """Up to `limit` Arabic subtitles as tracks for mpv: {"url": local path, "lang": "ara", "label": ...}."""
    imdb_id, season, episode = parse_video_id(kind, video_id)
    if not imdb_id.startswith("tt"):
        return []
    candidates = []
    for fetch in (subdl_candidates, subsource_candidates):
        try:
            candidates += fetch(imdb_id, season, episode)
        except SubtitleError:
            continue
    tracks = []
    for c in rank(candidates, release):
        if len(tracks) >= limit:
            break
        try:
            picked = pick_file(_get(c["url"], c["headers"], raw=True), season, episode)
        except SubtitleError:
            continue
        if not picked:
            continue
        filename, data = picked
        ext = "." + filename.rsplit(".", 1)[-1].lower() if "." in filename else ".srt"
        path = _cache_dir() / (hashlib.sha256((c["url"] + filename).encode()).hexdigest()[:24] + ext)
        try:
            path.write_bytes(to_utf8(data))
        except OSError:
            continue
        tracks.append({"url": str(path), "lang": "ara", "label": f"Arabic · {NAMES[c['provider']]}"})
    return tracks


def check_key(provider, value):
    """ValueError: rejected; RuntimeError: the site couldn't be reached."""
    if provider == "subdl":
        url = SUBDL_API + "?" + urllib.parse.urlencode({"api_key": value, "imdb_id": "tt0111161", "type": "movie",
                                                       "languages": "AR", "subs_per_page": 1})
        headers = {}
    elif provider == "subsource":
        url = f"{SUBSOURCE_API}/movies/search?searchType=imdb&imdb=tt0111161"
        headers = {"X-API-Key": value}
    else:
        raise ValueError("Unknown subtitle site")
    req = urllib.request.Request(url, headers=dict({"User-Agent": UA, "Accept": "application/json"}, **headers))
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
            body = r.read(200_000)
    except urllib.error.HTTPError as exc:
        if exc.code in (401, 403):
            raise ValueError(f"{NAMES[provider]} rejected this key") from None
        raise RuntimeError(f"Couldn't reach {NAMES[provider]} to check the key") from None
    except (urllib.error.URLError, OSError):
        raise RuntimeError(f"Couldn't reach {NAMES[provider]} to check the key") from None
    try:
        data = json.loads(body)
    except ValueError:
        raise RuntimeError(f"Couldn't check the key with {NAMES[provider]}") from None
    if isinstance(data, dict) and data.get("status") is False:
        raise ValueError(f"{NAMES[provider]} rejected this key")
