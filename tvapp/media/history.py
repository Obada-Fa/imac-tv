"""Watch history in $DATA/history.json, keyed by video_id. Offline-safe and cheap."""
import hashlib
import json
import os
import threading
import time
from urllib.parse import quote

from . import config

FINISHED_RATIO = 0.92
MIN_RESUME = 60
_lock = threading.RLock()
FIELDS = ("type", "id", "video_id", "season", "episode", "name", "poster", "position", "duration", "finished", "updated")


def _path():
    return config.data_dir() / "history.json"


def load():
    try:
        data = json.loads(_path().read_text())
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def _save(data):
    path = _path()
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    temp = path.with_suffix(".json.tmp")
    temp.write_text(json.dumps(data))
    temp.chmod(0o600)
    os.replace(temp, path)


def _number(value):
    return float(value) if isinstance(value, (int, float)) and not isinstance(value, bool) and value >= 0 else 0.0


def is_finished(position, duration):
    return duration > 0 and position / duration >= FINISHED_RATIO


def record(entry):
    """Merge one playback snapshot; empty name/poster never overwrite known values."""
    video_id = entry.get("video_id")
    if not isinstance(video_id, str) or not video_id:
        raise ValueError("video_id required")
    with _lock:
        data = load()
        old = data.get(video_id, {})
        new = {key: entry.get(key) if entry.get(key) not in (None, "") else old.get(key) for key in FIELDS}
        new["video_id"] = video_id
        new["position"] = _number(entry.get("position", old.get("position")))
        # A zero duration (mpv not ready yet) must not erase what we already know.
        new["duration"] = _number(entry.get("duration")) or _number(old.get("duration"))
        new["finished"] = is_finished(new["position"], new["duration"])
        new["updated"] = entry.get("updated") or time.time()
        data[video_id] = new
        _save(data)
        return new


def resume_position(video_id):
    item = load().get(video_id)
    if not item or item.get("finished"):
        return 0.0
    return _number(item.get("position")) if _number(item.get("position")) > 0 else 0.0


def cached_videos(kind, item_id):
    """Episode list from the add-on meta cache only; never touches the network. [] when unknown."""
    try:
        url = f"{config.load()['addons']['meta']}/meta/{quote(kind, safe=':')}/{quote(item_id, safe=':')}.json"
        path = config.data_dir() / "cache" / "addons" / (hashlib.sha256(url.encode()).hexdigest() + ".json")
        return (json.loads(path.read_text()).get("meta") or {}).get("videos") or []
    except (OSError, ValueError, AttributeError):
        return []


def _next_episode(item, videos):
    try:
        season, episode = int(item["season"]), int(item["episode"])
        found = videos(item["type"], item["id"]) or []
    except (KeyError, TypeError, ValueError):
        return None
    wanted = (season, episode + 1)
    for video in found:
        try:
            if (int(video.get("season")), int(video.get("episode"))) == wanted:
                return {"type": item["type"], "id": item["id"], "video_id": f"{item['id']}:{wanted[0]}:{wanted[1]}",
                        "season": wanted[0], "episode": wanted[1], "name": item.get("name"),
                        "poster": item.get("poster"), "position": 0.0, "duration": 0.0,
                        "finished": False, "updated": item.get("updated", 0)}
        except (TypeError, ValueError, AttributeError):
            continue
    return None


def next_video(videos, season, episode):
    """The episode after (season, episode) in a meta `videos` list, rolling over to the next season.
    Returns {"season", "episode", "name"} or None. Specials (season 0) only follow season 0."""
    try:
        season, episode = int(season), int(episode)
    except (TypeError, ValueError):
        return None
    best = None
    for video in videos or []:
        try:
            key = (int(video.get("season")), int(video.get("episode")))
        except (TypeError, ValueError, AttributeError):
            continue
        if key <= (season, episode) or (key[0] == 0 and season != 0) or key[1] < 1:
            continue
        if best is None or key < best[0]:
            best = (key, video.get("name") or video.get("title"))
    if not best:
        return None
    return {"season": best[0][0], "episode": best[0][1], "name": best[1]}


def continue_watching(limit=20, videos=cached_videos):
    """Newest first: unfinished entries past 60s, plus the next episode after a finished one."""
    data = load()
    out, seen = [], set()
    for item in sorted(data.values(), key=lambda i: i.get("updated", 0), reverse=True):
        if item.get("finished"):
            if item.get("type") != "series":
                continue
            nxt = _next_episode(item, videos)
            # Skip when that episode already has its own (newer or in-progress) entry.
            if not nxt or nxt["video_id"] in data:
                continue
            item = nxt
        elif _number(item.get("position")) <= MIN_RESUME:
            continue
        if item["video_id"] in seen:
            continue
        seen.add(item["video_id"])
        out.append(item)
        if len(out) >= limit:
            break
    return out
