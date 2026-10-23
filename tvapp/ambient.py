"""Ambient background photo library: Bing daily + Wikimedia Commons POTD, cached on disk at screen size.

Pure data module (GdkPixbuf only for scaling). Network/parse failures are logged to stderr and swallowed.
"""
import json
import os
from pathlib import Path
import random
import re
import sys
import threading
import time
import urllib.parse
import urllib.request
from datetime import date, timedelta

import gi
gi.require_version("GdkPixbuf", "2.0")
from gi.repository import GdkPixbuf

UA = "imactv/1.0"
BING = "https://www.bing.com/HPImageArchive.aspx?format=js&idx=0&n=8&mkt=en-GB"
WIKI = "https://api.wikimedia.org/feed/v1/wikipedia/en/featured/{:%Y/%m/%d}"
REFRESH_EVERY = 24 * 3600
MIN_ASPECT = 1.3
WIKI_DAYS = 8
WIKI_SIZES = (500, 960, 1280, 1920)


def _log(*a):
    print("ambient:", *a, file=sys.stderr)


def _get(url, timeout=20):
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read()


def _getjson(url):
    return json.loads(_get(url))


def _bing_candidates(doc):
    out = []
    for im in doc.get("images", []):
        base = im.get("urlbase") or ""
        if not base.startswith("/"):
            continue
        out.append({
            "id": "bing-" + str(im.get("startdate") or im.get("hsh") or base),
            "url": "https://www.bing.com" + base + "_1920x1080.jpg",
            "caption": (im.get("title") or "").strip(),
            "credit": re.sub(r"^.*\(©\s*|\)\s*$", "", im.get("copyright") or "").strip(),
            "caption_full": (im.get("copyright") or "").strip(),
            "source": "bing", "aspect": 16 / 9,
        })
    for c in out:  # copyright reads "Place (© Photographer)": show place as caption, title as fallback
        place = c.pop("caption_full").split(" (©")[0].strip()
        c["caption"] = place or c["caption"]
    return out


def _wiki_candidate(day, doc):
    im = doc.get("image") or {}
    full, thumb = im.get("image") or {}, im.get("thumbnail") or {}
    w, h = full.get("width") or 0, full.get("height") or 0
    src = thumb.get("source") or ""
    if not (w and h and src):
        return None
    if w / h < MIN_ASPECT:
        return None
    url = src.split("?")[0]
    size = max([x for x in WIKI_SIZES if x <= min(WIKI_SIZES[-1], w)] or [WIKI_SIZES[0]])  # API only serves standard sizes
    url, n = re.subn(r"/\d+px-([^/]+)$", "/%dpx-\\1" % size, url)
    if not n:
        return None
    desc = ((im.get("description") or {}).get("text") or "").strip()
    if not desc:
        desc = re.sub(r"^File:|\.\w+$", "", im.get("title") or "").replace("_", " ")
    if len(desc) > 140:
        desc = desc[:137].rsplit(" ", 1)[0] + "…"
    artist = ((im.get("artist") or {}).get("text") or "").strip()
    lic = ((im.get("license") or {}).get("type") or "").strip()
    credit = ", ".join(x for x in (artist, lic) if x) or "Wikimedia Commons"
    return {"id": "wikimedia-%s" % day.strftime("%Y%m%d"), "url": url, "caption": desc,
            "credit": credit, "source": "wikimedia", "aspect": w / h}


class Library:
    def __init__(self, data_dir, width, height, max_images=40, max_bytes=150 * 2**20):
        self.dir = Path(data_dir) / "ambient"
        self.index_path = self.dir / "index.json"
        self.w, self.h = int(width), int(height)
        self.max_images, self.max_bytes = max_images, max_bytes
        self._lock = threading.Lock()
        self._busy = threading.Lock()
        self._queue = []
        self._last = None

    # ---- index ----
    def _read(self):
        try:
            d = json.loads(self.index_path.read_text())
            if not isinstance(d, dict):
                raise ValueError("index not a dict")
            ims = [i for i in d.get("images", []) if isinstance(i, dict) and i.get("file") and i.get("id")]
            return {"checked": float(d.get("checked") or 0), "images": ims}
        except FileNotFoundError:
            pass
        except Exception as e:
            _log("bad index:", e)
        return {"checked": 0.0, "images": []}

    def _write_index(self, idx):
        self.dir.mkdir(parents=True, exist_ok=True)
        tmp = self.index_path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(idx))
        os.replace(tmp, self.index_path)

    # ---- public ----
    def items(self):
        ims = sorted(self._read()["images"], key=lambda i: i.get("added", 0), reverse=True)
        out = []
        for i in ims:
            p = self.dir / Path(str(i["file"])).name
            if p.is_file():
                out.append({"path": str(p), "caption": i.get("caption", ""), "credit": i.get("credit", ""),
                            "source": i.get("source", "")})
        return out

    def next(self):
        with self._lock:
            have = {i["path"]: i for i in self.items()}
            if not have:
                self._queue = []
                return None
            self._queue = [p for p in self._queue if p in have]
            if not self._queue:
                self._queue = list(have)
                random.shuffle(self._queue)
                if len(self._queue) > 1 and self._queue[0] == self._last:
                    self._queue.append(self._queue.pop(0))
            self._last = self._queue.pop(0)
            return dict(have[self._last])

    def refresh_async(self, force=False, on_done=None):
        def run():
            n = 0
            try:
                n = self._refresh(force)
            except Exception as e:
                _log("refresh failed:", e)
            if on_done:
                try:
                    on_done(n)
                except Exception as e:
                    _log("on_done failed:", e)
        threading.Thread(target=run, daemon=True, name="ambient-refresh").start()

    # ---- worker ----
    def _refresh(self, force):
        if not self._busy.acquire(blocking=False):
            return 0
        try:
            idx = self._read()
            if not force and time.time() - idx["checked"] < REFRESH_EVERY:
                return 0
            have = {i["id"] for i in idx["images"]}
            cands = []
            try:
                cands += _bing_candidates(_getjson(BING))
            except Exception as e:
                _log("bing:", e)
            today = date.today()
            for k in range(WIKI_DAYS):
                day = today - timedelta(days=k)
                if "wikimedia-%s" % day.strftime("%Y%m%d") in have:
                    continue
                try:
                    c = _wiki_candidate(day, _getjson(WIKI.format(day)))
                    if c:
                        cands.append(c)
                except Exception as e:
                    _log("wikimedia %s:" % day, e)
            added = 0
            now = time.time()
            for c in cands:
                if c["id"] in have or c["aspect"] < MIN_ASPECT:
                    continue
                try:
                    name = self._download(c)
                except Exception as e:
                    _log("download %s:" % c["id"], e)
                    continue
                if not name:
                    continue
                have.add(c["id"])
                idx["images"].append({"id": c["id"], "file": name, "caption": c["caption"],
                                      "credit": c["credit"], "source": c["source"], "added": now - added})
                added += 1
            if cands:  # offline/failed fetches don't start the 24 h wait
                idx["checked"] = time.time()
            self._prune(idx)
            self._write_index(idx)
            return added
        finally:
            self._busy.release()

    def _download(self, c):
        data = _get(c["url"], timeout=40)
        loader = GdkPixbuf.PixbufLoader()
        try:
            loader.write(data)
        finally:
            loader.close()
        pb = loader.get_pixbuf()
        if not pb:
            return None
        sw, sh = pb.get_width(), pb.get_height()
        if sh == 0 or sw / sh < MIN_ASPECT:
            return None
        s = max(self.w / sw, self.h / sh)
        nw, nh = max(self.w, round(sw * s)), max(self.h, round(sh * s))
        pb = pb.scale_simple(nw, nh, GdkPixbuf.InterpType.BILINEAR)
        x, y = (nw - self.w) // 2, (nh - self.h) // 2
        pb = pb.new_subpixbuf(x, y, self.w, self.h)
        self.dir.mkdir(parents=True, exist_ok=True)
        name = re.sub(r"[^A-Za-z0-9._-]", "_", c["id"]) + ".jpg"
        tmp = self.dir / (name + ".tmp")
        pb.savev(str(tmp), "jpeg", ["quality"], ["85"])
        os.replace(tmp, self.dir / name)
        return name

    def _prune(self, idx):
        ims = sorted(idx["images"], key=lambda i: i.get("added", 0), reverse=True)
        keep, total = [], 0
        for i in ims:
            p = self.dir / Path(str(i["file"])).name
            try:
                size = p.stat().st_size
            except OSError:
                continue
            if len(keep) >= self.max_images or (keep and total + size > self.max_bytes):
                try:
                    p.unlink()
                except OSError:
                    pass
                continue
            keep.append(i)
            total += size
        idx["images"] = keep
        keepf = {str(i["file"]) for i in keep}
        for p in self.dir.glob("*.jpg"):  # orphans
            if p.name not in keepf:
                try:
                    p.unlink()
                except OSError:
                    pass
