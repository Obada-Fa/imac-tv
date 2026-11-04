"""Bundled wallpaper collections and the shuffle used by all Home backgrounds."""
import json
import random
import threading
from functools import lru_cache
from pathlib import Path

ASSETS = Path(__file__).resolve().parent / "assets"
NAMES = {"photos": "Focus", "retro": "Retro", "astro": "Astro", "topographic": "Topographic"}
BUNDLED = {"astro": "Planetary photography", "topographic": "Contour maps and shaded relief"}


@lru_cache(maxsize=len(BUNDLED))
def pictures(source):
    """Read a shipped collection in cover-first order. Each record is ready for Home."""
    folder = ASSETS / source
    manifest = json.loads((folder / "credits.json").read_text())
    return tuple({"path": str(folder / filename), "source": source,
                  "caption": info["name"], "credit": info["credit"]}
                 for filename, info in manifest.items())


class Rotation:
    """Finish a shuffled cycle before repeating, including when the available pictures change."""
    def __init__(self):
        self.queue = []
        self.last = None

    def pick(self, items):
        have = {item["path"]: item for item in items}
        self.queue = [path for path in self.queue if path in have]
        if not have:
            return None
        if not self.queue:
            self.queue = list(have)
            random.shuffle(self.queue)
            if len(self.queue) > 1 and self.queue[0] == self.last:
                self.queue.append(self.queue.pop(0))
        self.last = self.queue.pop(0)
        return dict(have[self.last])


class Collection:
    def __init__(self, source):
        self.source = source
        self.rotation = Rotation()
        self.lock = threading.Lock()

    def next(self):
        with self.lock:
            return self.rotation.pick(pictures(self.source))
