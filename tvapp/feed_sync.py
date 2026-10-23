#!/usr/bin/env python3
"""One-time import of the Stremio library into the TV watch history, plus artwork helper."""
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import urllib.error
import urllib.request
from datetime import datetime
import sys

BASE = Path(__file__).resolve().parent
if str(BASE) not in sys.path:
    sys.path.insert(0, str(BASE))
from media import history  # noqa: E402

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


def import_history(items=None):
    """One-time import of Stremio's library_recent into history.json; no-op when history exists."""
    if (DATA / "history.json").exists():
        return 0
    imported = 0
    for item in (stremio_items() if items is None else items):
        state = item.get("state") or {}
        item_id = str(item.get("_id") or "")
        kind = item.get("type")
        if not item_id.startswith("tt") or kind not in ("movie", "series") or not item.get("name"):
            continue
        video_id = str(state.get("video_id") or item_id)
        parts = video_id.split(":")
        season = episode = None
        if kind == "series":
            try:
                season, episode = int(parts[-2]), int(parts[-1])
            except (ValueError, IndexError):
                continue
            video_id = f"{item_id}:{season}:{episode}"
        else:
            video_id = item_id
        history.record({"type": kind, "id": item_id, "video_id": video_id, "season": season, "episode": episode,
                        "name": item.get("name"), "poster": item.get("poster") or None,
                        "position": int(state.get("timeOffset") or 0) / 1000,
                        "duration": int(state.get("duration") or 0) / 1000,
                        "updated": _timestamp(state.get("lastWatched"))})
        imported += 1
    return imported


def _timestamp(value):
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00")).timestamp()
    except ValueError:
        return None


def main():
    DATA.mkdir(mode=0o700, parents=True, exist_ok=True)
    try:
        import_history()
    except (OSError, ValueError, sqlite3.Error):
        pass


if __name__ == "__main__":
    main()
