"""Retro background library: user-imported images kept at their original resolution.

DATA/retro/ holds the originals untouched (never the analysis copies or previews), index.json, and small
thumbs/ for the phone. Files copied straight into the folder are adopted on the next scan. Importing a picture
with the same name as an existing one but more pixels replaces it in place (same id), so a higher-resolution
version swaps in without losing the selection; Home's contour cache keys on size and mtime and re-traces it.

Used by the phone server (import/list/choose) and Home (the provider: `next()` has the same boundary as
ambient.Library.next, and leaves `reactive_paths` out so Home runs its contour analysis for Retro only).
"""
import hashlib
import json
import os
from pathlib import Path
import random
import re
import sys
import threading
import time

import gi
gi.require_version("GdkPixbuf", "2.0")
from gi.repository import GdkPixbuf

MAX_BYTES = 64 * 2**20
MAX_PIXELS = 60_000_000
MAX_SIDE = 16384
MIN_WIDTH = 640
MAX_IMAGES = 200
SEED_DIR = Path(__file__).resolve().parent / "assets" / "retro"
THUMB_W = 320
CREDIT = "User-provided image"
EXTS = {"jpeg": ".jpg", "png": ".png", "webp": ".webp", "bmp": ".bmp", "tiff": ".tif", "gif": ".gif",
        "avif": ".avif", "heif": ".heic", "jxl": ".jxl", "svg": ".svg"}
SKIP_FORMATS = {"gif", "svg"}          # animated / vector: not wallpapers


class BackgroundError(ValueError):
    """A message fit to show on the phone/TV."""


def _log(*a):
    print("backgrounds:", *a, file=sys.stderr)


def supported_formats():
    return sorted(f.get_name() for f in GdkPixbuf.Pixbuf.get_formats() if f.get_name() not in SKIP_FORMATS)


def probe(path):
    """(format, width, height) from the header only, or an BackgroundError saying why it can't be used."""
    fmt, w, h = GdkPixbuf.Pixbuf.get_file_info(str(path))
    if fmt is None:
        raise BackgroundError("This file isn't a picture this TV can open. Supported: "
                           + ", ".join(supported_formats()))
    name = fmt.get_name()
    if name in SKIP_FORMATS:
        raise BackgroundError("Animated and vector images can't be used as backgrounds")
    if w < MIN_WIDTH or h < MIN_WIDTH * 9 // 16:
        raise BackgroundError("Picture is too small (%d × %d); use at least %d px wide" % (w, h, MIN_WIDTH))
    if w > MAX_SIDE or h > MAX_SIDE or w * h > MAX_PIXELS:
        raise BackgroundError("Picture is too large (%d × %d)" % (w, h))
    return name, w, h


def _stem(name):
    return re.sub(r"[^a-z0-9]+", " ", Path(str(name)).stem.lower()).strip()


def _title(name):
    t = re.sub(r"[_\-]+", " ", Path(str(name)).stem).strip()
    return (t[:60] or "Retro background")


def clean_name(name):
    base = Path(str(name or "")).name
    base = re.sub(r"[\x00-\x1f]", "", base)[:120]
    return base or "background"


def valid_id(v):
    return isinstance(v, str) and re.fullmatch(r"[0-9a-f]{12}", v) is not None


class Backgrounds:
    def __init__(self, data_dir):
        self.dir = Path(data_dir) / "retro"
        self.thumbs = self.dir / "thumbs"
        self.index_path = self.dir / "index.json"
        self._lock = threading.RLock()
        self._queue = []
        self._last = None

    # ---- index
    def _read(self):
        try:
            d = json.loads(self.index_path.read_text())
            ims = d.get("images") if isinstance(d, dict) else None
            return [i for i in ims if isinstance(i, dict) and valid_id(i.get("id")) and i.get("file")] \
                if isinstance(ims, list) else []
        except FileNotFoundError:
            return []
        except (OSError, ValueError) as exc:
            _log("bad index:", exc)
            return []

    def _write(self, images):
        self.dir.mkdir(mode=0o700, parents=True, exist_ok=True)
        tmp = self.index_path.with_name("index.json.%d.tmp" % os.getpid())
        tmp.write_text(json.dumps({"images": images}))
        os.replace(tmp, self.index_path)

    def _file(self, rec):
        return self.dir / Path(str(rec["file"])).name

    def _thumb(self, rec):
        return self.thumbs / (rec["id"] + ".jpg")

    def _make_thumb(self, rec):
        try:
            pb = GdkPixbuf.Pixbuf.new_from_file_at_scale(str(self._file(rec)), THUMB_W, -1, True)
            self.thumbs.mkdir(parents=True, exist_ok=True)
            tmp = self._thumb(rec).with_suffix(".tmp")
            pb.savev(str(tmp), "jpeg", ["quality"], ["80"])
            os.replace(tmp, self._thumb(rec))
        except Exception as exc:
            _log("thumbnail:", exc)

    # ---- public
    def scan(self):
        """Reconcile the folder with the index: adopt dropped-in files, forget vanished ones."""
        with self._lock:
            images = self._read()
            known = {Path(str(i["file"])).name for i in images}
            changed = False
            keep = []
            for rec in images:
                if self._file(rec).is_file():
                    keep.append(rec)
                else:
                    changed = True
            images = keep
            try:
                names = sorted(p.name for p in self.dir.iterdir()
                               if p.is_file() and not p.name.startswith(".") and p.name != "index.json"
                               and not p.name.endswith(".tmp"))
            except OSError:
                names = []
            for n in names:
                if n in known or len(images) >= MAX_IMAGES:
                    continue
                try:
                    fmt, w, h = probe(self.dir / n)
                except Exception:
                    continue
                rec = {"id": hashlib.sha256(n.encode()).hexdigest()[:12], "file": n, "name": _title(n),
                       "width": w, "height": h, "added": (self.dir / n).stat().st_mtime}
                if any(i["id"] == rec["id"] for i in images):
                    continue
                images.append(rec)
                changed = True
            if changed:
                self._write(images)
            return images

    def list(self):
        """[{id, name, width, height}] newest first (what the phone and TV settings show)."""
        ims = sorted(self.scan(), key=lambda i: i.get("added", 0), reverse=True)
        return [{"id": i["id"], "name": i.get("name", ""), "width": int(i.get("width") or 0),
                 "height": int(i.get("height") or 0)} for i in ims]

    def count(self):
        return len(self.scan())

    def thumb_path(self, bid):
        if not valid_id(bid):
            return None
        for rec in self.scan():
            if rec["id"] == bid:
                t = self._thumb(rec)
                if not t.is_file():
                    self._make_thumb(rec)
                return t if t.is_file() else None
        return None

    def import_file(self, src, name=None, replace=None):
        """Copy a picture in at full resolution. Returns {"id", "status"}: added | replaced | kept (an existing
        picture of the same name already has as many pixels). `replace` forces a replacement of that id."""
        src = Path(src)
        name = clean_name(name or src.name)
        try:
            size = src.stat().st_size
        except OSError:
            raise BackgroundError("Couldn't read the file")
        if size <= 0 or size > MAX_BYTES:
            raise BackgroundError("Picture must be under %d MB" % (MAX_BYTES // 2**20))
        fmt, w, h = probe(src)
        digest = hashlib.sha256()
        with open(src, "rb") as fh:
            for block in iter(lambda: fh.read(1 << 20), b""):
                digest.update(block)
        sha = digest.hexdigest()
        with self._lock:
            images = self.scan()
            target = None
            if replace is not None:
                target = next((r for r in images if r["id"] == replace), None)
                if target is None:
                    raise BackgroundError("That background no longer exists")
            for rec in images:
                if rec.get("sha") == sha:
                    return {"id": rec["id"], "status": "kept"}
            if replace is None:
                stem = _stem(name)
                same = [r for r in images if _stem(r.get("name", "")) == stem and stem]
                if same:
                    old = same[0]
                    if int(old.get("width") or 0) * int(old.get("height") or 0) >= w * h:
                        return {"id": old["id"], "status": "kept"}
                    target = old
            if target is None and len(images) >= MAX_IMAGES:
                raise BackgroundError("Background library is full; remove one first")
            self.dir.mkdir(mode=0o700, parents=True, exist_ok=True)
            bid = target["id"] if target else sha[:12]
            if not target and any(r["id"] == bid for r in images):
                bid = hashlib.sha256((sha + str(time.time())).encode()).hexdigest()[:12]
            fname = "%s%s" % (bid, EXTS.get(fmt, ".img"))
            tmp = self.dir / (fname + ".tmp")
            with open(src, "rb") as fi, open(tmp, "wb") as fo:
                while True:
                    block = fi.read(1 << 20)
                    if not block:
                        break
                    fo.write(block)
            os.replace(tmp, self.dir / fname)            # new inode/mtime: Home re-traces the contours
            rec = {"id": bid, "file": fname, "name": target.get("name") if target else _title(name),
                   "width": w, "height": h, "added": time.time(), "sha": sha}
            if target:
                old_file = self._file(target)
                if old_file.name != fname:
                    old_file.unlink(missing_ok=True)
                images = [rec if r["id"] == bid else r for r in images]
            else:
                images.append(rec)
            self._write(images)
            self._thumb(rec).unlink(missing_ok=True)
            self._make_thumb(rec)
            return {"id": bid, "status": "replaced" if target else "added"}

    def remove(self, bid):
        with self._lock:
            images = self.scan()
            rec = next((r for r in images if r["id"] == bid), None)
            if not rec:
                raise BackgroundError("That background no longer exists")
            self._file(rec).unlink(missing_ok=True)
            self._thumb(rec).unlink(missing_ok=True)
            self._write([r for r in images if r["id"] != bid])
            return True

    def seed(self, assets_dir):
        """First run only: import the bundled starter pictures (scripts/make-retro-seed.py). A marker file keeps
        pictures the user later removes from coming back. Returns how many were added."""
        marker = self.dir / ".seeded"
        added = 0
        with self._lock:
            if marker.exists():
                return 0
            try:
                files = sorted(Path(assets_dir).glob("*.jpg"))
            except OSError:
                files = []
            for f in files:
                try:
                    name = f.stem.replace("-", " ").title() + f.suffix
                    added += self.import_file(f, name)["status"] == "added"
                except Exception as exc:
                    _log("seed %s:" % f.name, exc)
            if files:
                self.dir.mkdir(mode=0o700, parents=True, exist_ok=True)
                marker.write_text(str(added))
        return added

    # ---- provider boundary (same as ambient.Library)
    def items(self, off=()):
        """Pictures that are switched on. Switching every one off shows them all rather than nothing."""
        recs = sorted(self.scan(), key=lambda i: i.get("added", 0), reverse=True)
        on = [r for r in recs if r["id"] not in set(off)] or recs
        out = []
        for rec in on:
            out.append({"path": str(self._file(rec)), "caption": rec.get("name", ""), "credit": CREDIT,
                        "source": "retro", "id": rec["id"]})
        return out

    def next(self, off=()):
        with self._lock:
            have = {i["path"]: i for i in self.items(off)}
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
