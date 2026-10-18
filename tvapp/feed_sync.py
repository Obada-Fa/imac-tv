#!/usr/bin/env python3
"""Build TV home rails from the signed-in Stremio cache."""
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import urllib.error
import urllib.request
from datetime import date

DATA = Path(os.environ.get("IMACTV_DATA_DIR", str(Path.home() / ".local/share/imactv")))
STREMIO_DBS = [
    Path.home() / ".var/app/com.stremio.Stremio/data/stremio/storage",
    Path.home() / ".stremio-server/storage",
    Path.home() / ".local/share/stremio/storage",
    Path.home() / ".local/share/Smart Code ltd/Stremio",
    Path.home() / ".config/stremio/storage",
]


def find_database():
    for base_dir in STREMIO_DBS:
        if base_dir.exists():
            matches = list(base_dir.glob("*/*/LocalStorage/localstorage.sqlite3"))
            if matches:
                return matches[0]
            matches = list(base_dir.glob("**/localstorage.sqlite3"))
            if matches:
                return matches[0]
    return None


def stremio_items():
    path = find_database()
    if not path:
        return []
    db = sqlite3.connect(f"file:{path}?mode=ro", uri=True, timeout=2)
    try:
        row = db.execute("SELECT value FROM ItemTable WHERE key = ?",
                         ("library_recent",)).fetchone()
    finally:
        db.close()
    if not row:
        return []
    data = json.loads(row[0].decode("utf-16le"))
    items = data.get("items", {})
    return list(items.values()) if isinstance(items, dict) else items


def artwork(item, art_dir):
    imdb_id = item.get("_id", "")
    if not imdb_id.startswith("tt"):
        return ""
    art_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
    target = art_dir / (hashlib.sha256(imdb_id.encode()).hexdigest()[:20] + ".jpg")
    if target.is_file() and target.stat().st_size > 3000:
        return str(target)
    urls = (
        f"https://images.metahub.space/background/large/{imdb_id}/img",
        f"https://images.metahub.space/poster/large/{imdb_id}/img",
    )
    for url in urls:
        try:
            request = urllib.request.Request(url, headers={"User-Agent": "iMacTV/1.0"})
            with urllib.request.urlopen(request, timeout=5) as response:
                content_type = response.headers.get("Content-Type", "")
                payload = response.read(3_000_001)
            if content_type.startswith("image/") and 3000 < len(payload) <= 3_000_000:
                target.write_bytes(payload)
                return str(target)
        except (OSError, urllib.error.URLError):
            continue
    poster = item.get("poster", "")
    return poster if poster.startswith("https://") else ""


def build_feed():
    art_dir = DATA / "artwork"
    candidates = []
    recent = []
    completed_movies = []
    for item in stremio_items():
        state = item.get("state") or {}
        last_watched = str(state.get("lastWatched") or "")
        if last_watched and item.get("name"):
            try:
                watched_date = date.fromisoformat(last_watched[:10])
                recent_subtitle = f"{item.get('type', 'Video').capitalize()} · Watched {watched_date:%b %-d}"
            except ValueError:
                recent_subtitle = "Recently watched on Stremio"
            recent.append((last_watched, item, recent_subtitle))
            duration = int(state.get("duration") or 0)
            watched = int(state.get("timeWatched") or 0)
            offset = int(state.get("timeOffset") or 0)
            if item.get("type") == "movie" and duration >= 1_800_000 and (
                    max(watched, offset) / duration >= 0.9 or
                    int(state.get("flaggedWatched") or 0) > 0):
                completed_movies.append((last_watched, item))
        duration = int(state.get("duration") or 0)
        times_watched = int(state.get("timesWatched") or 0)
        offset = int(state.get("timeOffset") or 0)
        watched = int(state.get("timeWatched") or 0)
        if duration <= 0 or times_watched <= 0 or not last_watched:
            continue
        progress = max(0.0, min(1.0, offset / duration))
        media_type = item.get("type", "video")
        if media_type == "movie" and (progress >= 0.97 or
                                      (offset < 15_000 and watched / duration >= 0.95)):
            continue
        video_id = str(state.get("video_id") or "")
        parts = video_id.split(":")
        subtitle = media_type.capitalize()
        if media_type == "series" and len(parts) >= 3:
            try:
                subtitle = f"Season {int(parts[-2])} · Episode {int(parts[-1])}"
            except ValueError:
                subtitle = "Series"
        if media_type == "series" and (offset < 15_000 or progress >= 0.97):
            subtitle += " · Up next"
            progress = None
        else:
            if media_type == "movie" and offset < 15_000:
                progress = max(0.0, min(1.0, watched / duration))
            remaining_ms = max(0, duration * (1 - progress))
            subtitle += f" · {max(1, round(remaining_ms / 60_000))} min left"
        candidates.append((last_watched, item, subtitle, progress))
    candidates.sort(key=lambda entry: entry[0], reverse=True)
    recent.sort(key=lambda entry: entry[0], reverse=True)
    completed_movies.sort(key=lambda entry: entry[0], reverse=True)
    continue_watching = []
    for _, source, subtitle, progress in candidates[:6]:
        card = {
            "title": source.get("name") or "Continue watching",
            "subtitle": subtitle,
            "app": "stremio",
            "source": "STREMIO",
            "category": "continue_watching",
            "image": artwork(source, art_dir),
        }
        if progress is not None:
            card["progress"] = progress
        continue_watching.append(card)
    continue_titles = {item["title"] for item in continue_watching}
    recent_watched = []
    for _, source, subtitle in recent:
        title = source.get("name") or "Recently watched"
        if title in continue_titles:
            continue
        recent_watched.append({
            "title": title,
            "subtitle": subtitle,
            "app": "stremio",
            "source": "STREMIO",
            "category": "recently_watched",
            "image": artwork(source, art_dir),
        })
        if len(recent_watched) == 6:
            break
    used_titles = continue_titles | {item["title"] for item in recent_watched}
    watch_again = []
    for _, source in completed_movies:
        title = source.get("name") or ""
        if not title or title in used_titles:
            continue
        watch_again.append({
            "title": title,
            "subtitle": "Movie · From your Stremio history",
            "app": "stremio",
            "source": "STREMIO",
            "category": "watch_again",
            "image": artwork(source, art_dir),
        })
        if len(watch_again) == 6:
            break
    return {"continue_watching": continue_watching,
            "recently_watched": recent_watched[:6],
            "watch_again": watch_again, "stremio": [], "youtube": []}


def main():
    DATA.mkdir(mode=0o700, parents=True, exist_ok=True)
    payload = json.dumps(build_feed(), ensure_ascii=False, indent=2)
    target = DATA / "recommendations.json"
    if target.is_file() and target.read_text() == payload:
        return
    temporary = target.with_suffix(".json.tmp")
    temporary.write_text(payload)
    temporary.replace(target)


if __name__ == "__main__":
    main()
