import json
from pathlib import Path
import sys
import tempfile
import threading
import time
import unittest
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import ambient  # noqa: E402
from gi.repository import GdkPixbuf  # noqa: E402


def jpeg(w, h):
    pb = GdkPixbuf.Pixbuf.new(GdkPixbuf.Colorspace.RGB, False, 8, w, h)
    pb.fill(0x336699ff)
    ok, buf = pb.save_to_bufferv("jpeg", [], [])
    return bytes(buf)


BING = {"images": [
    {"startdate": "20261022", "urlbase": "/th?id=OHR.Wolves_EN-GB1", "title": "Partners in the wild",
     "copyright": "Two grey wolves, Yellowstone (© John Morrison/Getty Images)"},
    {"startdate": "20261021", "urlbase": "/th?id=OHR.Park_EN-GB2", "title": "Park",
     "copyright": "Naejangsan (© Someone)"}]}


def wiki(w, h):
    return {"image": {"title": "File:A_b.jpg",
            "thumbnail": {"source": "https://thumb.wikimedia.org/wikipedia/commons/thumb/7/71/A_b.jpg/960px-A_b.jpg?utm_source=x", "width": 640, "height": 512},
            "image": {"source": "https://upload.wikimedia.org/x.jpg", "width": w, "height": h},
            "artist": {"text": "Jane"}, "license": {"type": "CC BY-SA 4.0"},
            "description": {"text": "A mushroom"}}}


def fake_get(wikis=None, bing=BING, calls=None):
    def get(url, timeout=20):
        if calls is not None:
            calls.append(url)
        if "bing.com/HPImage" in url:
            return json.dumps(bing).encode()
        if "feed/v1" in url:
            return json.dumps(wikis if wikis is not None else {}).encode()
        return jpeg(640, 360)
    return get


class LibTest(unittest.TestCase):
    def setUp(self):
        self._t = tempfile.TemporaryDirectory()
        self.dir = Path(self._t.name)
        self.lib = ambient.Library(self.dir, 320, 180)

    def tearDown(self):
        self._t.cleanup()

    def refresh(self, force=False, **kw):
        with mock.patch.object(ambient, "_get", fake_get(**kw)):
            return self.lib._refresh(force)

    def test_empty(self):
        self.assertEqual(self.lib.items(), [])
        self.assertIsNone(self.lib.next())

    def test_bing_parse_and_crop(self):
        calls = []
        n = self.refresh(calls=calls)
        self.assertEqual(n, 2)
        self.assertIn("https://www.bing.com/th?id=OHR.Wolves_EN-GB1_1920x1080.jpg", calls)
        its = self.lib.items()
        self.assertEqual(its[0]["caption"], "Two grey wolves, Yellowstone")
        self.assertEqual(its[0]["credit"], "John Morrison/Getty Images")
        self.assertEqual(its[0]["source"], "bing")
        pb = GdkPixbuf.Pixbuf.new_from_file(its[0]["path"])
        self.assertEqual((pb.get_width(), pb.get_height()), (320, 180))
        self.assertEqual(set(json.loads((self.dir / "ambient/index.json").read_text())["images"][0]),
                         {"id", "file", "caption", "credit", "source", "added"})

    def test_wikimedia_parse_and_thumb_url(self):
        calls = []
        self.refresh(wikis=wiki(4000, 2500), bing={"images": []}, calls=calls)
        its = self.lib.items()
        self.assertTrue(its and all(i["source"] == "wikimedia" for i in its))
        self.assertEqual(its[0]["caption"], "A mushroom")
        self.assertEqual(its[0]["credit"], "Jane, CC BY-SA 4.0")
        thumbs = [c for c in calls if "thumb.wikimedia" in c]
        self.assertTrue(thumbs and thumbs[0].endswith("/1920px-A_b.jpg"))
        self.assertNotIn("?", thumbs[0])

    def test_portrait_skipped(self):
        self.assertEqual(self.refresh(wikis=wiki(2000, 3000), bing={"images": []}), 0)
        self.assertEqual(self.lib.items(), [])

    def test_throttle_24h(self):
        self.refresh()
        calls = []
        self.assertEqual(self.refresh(calls=calls), 0)
        self.assertEqual(calls, [])
        idx = json.loads(self.lib.index_path.read_text())
        idx["checked"] = time.time() - 25 * 3600
        self.lib.index_path.write_text(json.dumps(idx))
        self.refresh(calls=calls)
        self.assertTrue(calls)
        calls.clear()
        self.refresh(force=True, calls=calls)
        self.assertTrue(calls)

    def test_prune_count(self):
        self.lib.max_images = 1
        self.refresh()
        self.assertEqual(len(self.lib.items()), 1)
        self.assertEqual(len(list((self.dir / "ambient").glob("*.jpg"))), 1)

    def test_prune_bytes(self):
        self.lib.max_bytes = 1
        self.refresh()
        self.assertEqual(len(self.lib.items()), 1)  # always keeps the newest

    def test_rotation_no_repeats(self):
        self.refresh()
        seen = [self.lib.next()["path"] for _ in range(2)]
        self.assertEqual(len(set(seen)), 2)
        seen = [self.lib.next()["path"] for _ in range(2)]
        self.assertEqual(len(set(seen)), 2)

    def test_corrupt_index(self):
        (self.dir / "ambient").mkdir()
        (self.dir / "ambient/index.json").write_text("{nope")
        self.assertEqual(self.lib.items(), [])
        self.assertEqual(self.refresh(), 2)

    def test_network_failure_swallowed(self):
        with mock.patch.object(ambient, "_get", side_effect=OSError("down")):
            self.assertEqual(self.lib._refresh(True), 0)
        self.assertEqual(self.lib.items(), [])

    def test_async_on_done_thread(self):
        got, ev = [], threading.Event()
        def done(n):
            got.append((n, threading.current_thread() is not threading.main_thread()))
            ev.set()
        with mock.patch.object(ambient, "_get", fake_get()):
            self.lib.refresh_async(on_done=done)
            self.assertTrue(ev.wait(10))
        self.assertEqual(got, [(2, True)])


if __name__ == "__main__":
    unittest.main()
