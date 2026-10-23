"""Intro / recap / credits / preview segments: mpv chapters first, TheIntroDB (v4, keyless) as fallback.

segment = {"kind": "intro"|"recap"|"credits"|"preview", "start": s, "end": s, "source": "chapters"|"theintrodb"}
Only ids are ever sent to TheIntroDB. Answers are cached under DATA/segments/ (hits 30 days, misses 3 days).
"""
import json
from pathlib import Path
import re
import time
import urllib.error
import urllib.parse
import urllib.request

from . import config

API = "https://api.theintrodb.org/v4/media"
KINDS = ("intro", "recap", "credits", "preview")
HIT_TTL, MISS_TTL = 30 * 86400, 3 * 86400
MIN_LEN, MAX_INTRO = 5.0, 240.0
EARLY, LATE = 0.40, 0.75          # intros/recaps start in the first 40%, credits in the last 25%

_RULES = (  # first match wins; order matters ("opening credits" is an intro, "end credits" is credits)
    ("recap", re.compile(r"\b(recap|previously|pr[ée]c[ée]demment|bisher|resumen)\b")),
    ("preview", re.compile(r"\b(preview|next (episode|time|on)|coming (up|soon)|prochainement|vorschau)\b")),
    ("intro", re.compile(r"\bopening credits\b|g[ée]n[ée]rique d.?ouverture|^(op|opening|intro)\s*\d*$")),
    ("credits", re.compile(r"\b(credits|ending|outro|abspann|nachspann)\b|g[ée]n[ée]rique de fin|^ed\s*\d*$")),
    ("intro", re.compile(r"\b(intro|introduction|opening|vorspann|g[ée]n[ée]rique|title sequence)\b")),
)


def _kind(title):
    t = re.sub(r"\s+", " ", str(title or "").strip().lower())
    for kind, rx in _RULES:
        if rx.search(t):
            return kind
    return None


def _clean(segs, duration):
    """Sanity rules; fraction checks only apply when the duration is known."""
    out = []
    for s in segs:
        length = s["end"] - s["start"]
        if length < MIN_LEN or s["start"] < 0:
            continue
        if s["kind"] == "intro" and length > MAX_INTRO:
            continue
        if duration and duration > 0:
            if s["kind"] in ("intro", "recap") and s["start"] > duration * EARLY:
                continue
            if s["kind"] == "credits" and s["start"] < duration * LATE:
                continue
        out.append(s)
    return sorted(out, key=lambda s: (s["start"], s["end"]))


def from_chapters(chapters, duration):
    """mpv `chapter-list` -> segments. Each chapter ends where the next begins (or at duration)."""
    try:
        chs = sorted(({"title": c.get("title"), "time": float(c["time"])} for c in chapters or []),
                     key=lambda c: c["time"])
        duration = float(duration or 0)
    except (TypeError, ValueError, KeyError, AttributeError):
        return []
    segs = []
    for i, c in enumerate(chs):
        kind = _kind(c["title"])
        if not kind:
            continue
        end = chs[i + 1]["time"] if i + 1 < len(chs) else duration
        if end <= c["time"]:
            continue
        segs.append({"kind": kind, "start": c["time"], "end": end, "source": "chapters"})
    return _clean(segs, duration)


def merge(chapter_segs, db_segs):
    have = {s["kind"] for s in chapter_segs or []}
    out = list(chapter_segs or []) + [s for s in db_segs or [] if s["kind"] not in have]
    return sorted(out, key=lambda s: (s["start"], s["end"]))


def active(segments, position):
    for s in segments or []:
        if s["start"] <= position < s["end"]:
            return s
    return None


def _parse(doc, duration=None):
    """TheIntroDB MediaResponse -> segments. null start = file start; null end = until the file ends."""
    out = []
    if not isinstance(doc, dict):
        return out
    for kind in KINDS:
        for t in doc.get(kind) or []:
            try:
                s = 0.0 if t.get("start_ms") is None else float(t["start_ms"]) / 1000
                e = float(t["end_ms"]) / 1000 if t.get("end_ms") is not None else (float(duration) if duration else float("inf"))
            except (TypeError, ValueError, AttributeError, KeyError):
                continue
            out.append({"kind": kind, "start": s, "end": e, "source": "theintrodb"})
    return _clean(out, duration)


def _ident(imdb_id, tvdb_id, tmdb_id):
    """The one id to send, TVDB > TMDB > IMDb (the server resolves in that order anyway)."""
    if tvdb_id:
        return "tvdb_id", str(int(tvdb_id))
    if tmdb_id:
        return "tmdb_id", str(int(tmdb_id))
    if imdb_id and re.fullmatch(r"tt\d{7,9}", str(imdb_id)):
        return "imdb_id", str(imdb_id)
    return None


def _cache_path(data_dir, ident, season, episode):
    name = "%s%s" % (ident[0][:4], ident[1])
    if season is not None and episode is not None:
        name += "-s%de%d" % (int(season), int(episode))
    return (data_dir or config.data_dir()) / "segments" / (name + ".json")


def _fetch(ident, season, episode):
    """Returns (doc|None, cacheable). 404 = known miss; anything else failing is not cached."""
    q = {ident[0]: ident[1]}
    if season is not None and episode is not None:
        q.update(season=int(season), episode=int(episode))
    req = urllib.request.Request(API + "?" + urllib.parse.urlencode(q),
                                 headers={"User-Agent": "imactv/1.0", "Accept": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=6) as r:
            return json.loads(r.read(200_000)), True
    except urllib.error.HTTPError as e:
        return None, e.code == 404
    except (urllib.error.URLError, OSError, ValueError):
        return None, False


def lookup(imdb_id, season, episode, tvdb_id=None, tmdb_id=None, data_dir=None, duration=None):
    """Cached, never raises. `duration` (s) only resolves open-ended segments (credits usually run to the
    end) and enables the position sanity rules; without it an open end is float('inf')."""
    try:
        ident = _ident(imdb_id, tvdb_id, tmdb_id)
        if not ident:
            return []
        path = _cache_path(Path(data_dir) if data_dir else None, ident, season, episode)
        try:
            c = json.loads(path.read_text())
            if time.time() - float(c["t"]) < (HIT_TTL if c.get("doc") else MISS_TTL):
                return _parse(c.get("doc"), duration)
        except (OSError, ValueError, KeyError, TypeError):
            pass
        doc, cacheable = _fetch(ident, season, episode)
        if doc is not None and not isinstance(doc, dict):
            doc, cacheable = None, False
        if doc is not None and not any(doc.get(k) for k in KINDS):
            doc = None   # e.g. episode-list fallback: nothing usable, treat as a miss
        if cacheable:
            try:
                path.parent.mkdir(parents=True, exist_ok=True)
                tmp = path.with_suffix(".tmp")
                tmp.write_text(json.dumps({"t": time.time(), "doc": doc}))
                tmp.replace(path)
            except OSError:
                pass
        return _parse(doc, duration)
    except Exception:
        return []
