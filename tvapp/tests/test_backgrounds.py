"""Retro background library, settings round trip, phone actions/upload, and Home's source switching."""
import json
from pathlib import Path
import sys
import tempfile
import threading
import unittest
from types import SimpleNamespace
from unittest import mock
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import backgrounds  # noqa: E402
import widgets  # noqa: E402
from gi.repository import GdkPixbuf  # noqa: E402
from test_torbox import TorboxCase, server  # noqa: E402


def image(path, w, h, fmt="png", rgb=0x336699ff):
    pb = GdkPixbuf.Pixbuf.new(GdkPixbuf.Colorspace.RGB, False, 8, w, h)
    pb.fill(rgb)
    pb.savev(str(path), fmt, [], [])
    return Path(path)


class LibraryTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.src = self.root / "src"
        self.src.mkdir()
        self.lib = backgrounds.Backgrounds(self.root / "data")

    def test_import_keeps_original_resolution_and_bytes(self):
        src = image(self.src / "Neon Stripes.png", 2560, 1440)
        r = self.lib.import_file(src)
        self.assertEqual(r["status"], "added")
        item = self.lib.next()
        self.assertEqual(item["source"], "retro")
        self.assertNotIn("reactive_paths", item)          # Home runs the analysis for Retro
        self.assertEqual(Path(item["path"]).read_bytes(), src.read_bytes())
        self.assertEqual(GdkPixbuf.Pixbuf.get_file_info(item["path"])[1:], (2560, 1440))
        self.assertEqual(self.lib.list()[0]["name"], "Neon Stripes")
        self.assertTrue(self.lib.thumb_path(r["id"]).is_file())

    def test_higher_resolution_replaces_in_place_and_smaller_is_kept(self):
        a = self.lib.import_file(image(self.src / "scallop.png", 1280, 720))
        small = self.lib.import_file(image(self.src / "scallop.jpg", 960, 540, "jpeg"))
        self.assertEqual(small, {"id": a["id"], "status": "kept"})
        before = Path(self.lib.next()["path"])
        big = self.lib.import_file(image(self.src / "scallop.bmp", 3840, 2160, "bmp"))
        self.assertEqual((big["id"], big["status"]), (a["id"], "replaced"))
        self.assertEqual(len(self.lib.list()), 1)
        self.assertEqual(self.lib.list()[0]["width"], 3840)
        self.assertFalse(before.exists() and before.suffix != Path(self.lib.next()["path"]).suffix)
        self.assertEqual(GdkPixbuf.Pixbuf.get_file_info(self.lib.next()["path"])[1:], (3840, 2160))

    def test_explicit_replace_and_identical_upload(self):
        a = self.lib.import_file(image(self.src / "one.png", 1920, 1080))
        same = self.lib.import_file(self.src / "one.png", "renamed.png")
        self.assertEqual(same["status"], "kept")
        r = self.lib.import_file(image(self.src / "other.png", 1600, 900, rgb=0xaa2200ff), replace=a["id"])
        self.assertEqual((r["id"], r["status"]), (a["id"], "replaced"))
        self.assertEqual(self.lib.list()[0]["width"], 1600)
        with self.assertRaises(backgrounds.BackgroundError):
            self.lib.import_file(self.src / "other.png", replace="0" * 12)

    def test_failures_are_readable_and_leave_library_unchanged(self):
        junk = self.src / "junk.png"
        junk.write_bytes(b"not an image at all")
        with self.assertRaisesRegex(backgrounds.BackgroundError, "isn't a picture"):
            self.lib.import_file(junk)
        with self.assertRaisesRegex(backgrounds.BackgroundError, "too small"):
            self.lib.import_file(image(self.src / "tiny.png", 200, 100))
        with self.assertRaises(backgrounds.BackgroundError):
            self.lib.import_file(self.src / "missing.png")
        self.assertEqual(self.lib.list(), [])
        self.assertIsNone(self.lib.next())

    def test_dropped_in_files_are_adopted_and_vanished_are_forgotten(self):
        self.lib.dir.mkdir(parents=True)
        image(self.lib.dir / "dropped.png", 1920, 1080)
        (self.lib.dir / "notes.txt").write_text("x")
        self.assertEqual([i["name"] for i in self.lib.list()], ["dropped"])
        (self.lib.dir / "dropped.png").unlink()
        self.assertEqual(self.lib.list(), [])

    def test_next_cycles_and_can_be_pinned_to_one(self):
        ids = [self.lib.import_file(image(self.src / ("p%d.png" % n), 1280 + n * 10, 720, rgb=0x10000000 * n + 0xff))["id"]
               for n in range(3)]
        seen = {self.lib.next()["id"] for _ in range(3)}
        self.assertEqual(seen, set(ids))
        for _ in range(3):
            self.assertEqual(self.lib.next(ids[1])["id"], ids[1])
        self.assertIsNone(self.lib.next("f" * 12))

    def test_remove(self):
        a = self.lib.import_file(image(self.src / "x.png", 1280, 720))
        self.assertTrue(self.lib.remove(a["id"]))
        self.assertEqual(self.lib.list(), [])
        with self.assertRaises(backgrounds.BackgroundError):
            self.lib.remove(a["id"])

    def test_corrupt_index_recovers_by_rescanning(self):
        a = self.lib.import_file(image(self.src / "x.png", 1280, 720))
        self.lib.index_path.write_text("{bad json")
        self.assertEqual(len(self.lib.list()), 1)
        self.assertEqual(len(self.lib.list()), 1)


class ConfigTests(unittest.TestCase):
    def test_defaults_and_validation(self):
        cfg = widgets.parse_config({})
        self.assertEqual((cfg["background"], cfg["retro_pick"]), ("photos", "shuffle"))
        for bad in ("retr0", 5, None, ["retro"]):
            self.assertEqual(widgets.parse_config({"background": bad})["background"], "photos")
        for bad in ("../../etc", "ABCDEF123456", "abc", 7, ""):
            self.assertEqual(widgets.parse_config({"retro_pick": bad})["retro_pick"], "shuffle")
        cfg = widgets.parse_config({"background": "retro", "retro_pick": "0123456789ab"})
        self.assertEqual((cfg["background"], cfg["retro_pick"]), ("retro", "0123456789ab"))

    def test_round_trip_keeps_other_keys(self):
        with tempfile.TemporaryDirectory() as d:
            (Path(d) / "home.json").write_text(json.dumps({"style": "bubbles", "display": {"output": "HDMI-1", "mode": "1920x1080"}}))
            cfg = widgets.save_keys(d, {"background": "retro", "retro_pick": "0123456789ab"})
            self.assertEqual(cfg["background"], "retro")
            again = widgets.load_config(d)
            self.assertEqual((again["background"], again["retro_pick"], again["style"]), ("retro", "0123456789ab", "bubbles"))
            raw = json.loads((Path(d) / "home.json").read_text())
            self.assertEqual(raw["display"]["mode"], "1920x1080")
            (Path(d) / "home.json").write_text("{corrupt")
            self.assertEqual(widgets.load_config(d)["background"], "photos")


class PhoneTests(TorboxCase):
    def setUp(self):
        super().setUp()
        p = mock.patch.object(server, "DATA", self.data)
        p.start()
        self.addCleanup(p.stop)
        self.c.retro = backgrounds.Backgrounds(self.data)
        self.src = self.data / "src"
        self.src.mkdir()

    def test_choose_requires_pictures_and_persists(self):
        with self.assertRaisesRegex(ValueError, "Add a retro picture"):
            self.c.action("background_set", {"source": "retro"})
        r = self.c.retro.import_file(image(self.src / "a.png", 1280, 720))
        res = self.c.action("background_set", {"source": "retro", "pick": r["id"]})
        self.assertEqual((res["background"]["source"], res["background"]["pick"]), ("retro", r["id"]))
        cfg = widgets.load_config(self.data)
        self.assertEqual((cfg["background"], cfg["retro_pick"]), ("retro", r["id"]))
        self.assertEqual(self.c.settings()["background"]["retro"][0]["id"], r["id"])
        for bad in ({"source": "tv"}, {"source": "retro", "pick": "0" * 12}, {"source": "retro", "pick": 4}, {}):
            with self.assertRaises(ValueError):
                self.c.action("background_set", bad)

    def test_removing_the_last_picture_falls_back_to_photos(self):
        r = self.c.retro.import_file(image(self.src / "a.png", 1280, 720))
        self.c.action("background_set", {"source": "retro", "pick": r["id"]})
        out = self.c.action("retro_remove", {"id": r["id"]})
        self.assertEqual(out["background"]["source"], "photos")
        self.assertEqual(out["background"]["wanted"], "retro")
        with self.assertRaises(ValueError):
            self.c.action("retro_remove", {"id": r["id"]})

    def test_upload_and_thumbnail_over_http(self):
        server.CONTROL = self.c
        httpd = ThreadingHTTPServer(("127.0.0.1", 0), server.Handler)
        threading.Thread(target=httpd.serve_forever, daemon=True).start()
        self.addCleanup(httpd.server_close)
        self.addCleanup(httpd.shutdown)
        base = f"http://127.0.0.1:{httpd.server_address[1]}"
        data = image(self.src / "up.png", 1920, 1080).read_bytes()
        with self.assertRaises(urllib.error.HTTPError) as ctx:
            urllib.request.urlopen(urllib.request.Request(base + "/api/retro/upload?name=a.png", data=data, method="POST"), timeout=5)
        self.assertEqual(ctx.exception.code, 401)
        with mock.patch.object(server.Handler, "authorised", return_value=True):
            req = urllib.request.Request(base + "/api/retro/upload?name=Up%20Load.png", data=data, method="POST")
            got = json.loads(urllib.request.urlopen(req, timeout=10).read())
            self.assertEqual(got["result"]["status"], "added")
            bid = got["result"]["id"]
            stored = Path(self.c.retro.next()["path"])
            self.assertEqual(stored.read_bytes(), data)              # untouched, full resolution
            thumb = urllib.request.urlopen(base + "/api/retro/thumb/" + bid, timeout=5)
            self.assertEqual(thumb.headers["Content-Type"], "image/jpeg")
            bad = urllib.request.Request(base + "/api/retro/upload?name=x.png", data=b"nope" * 100, method="POST")
            with self.assertRaises(urllib.error.HTTPError) as ctx:
                urllib.request.urlopen(bad, timeout=5)
            self.assertEqual(ctx.exception.code, 400)
            self.assertIn("isn't a picture", json.loads(ctx.exception.read())["error"])
            with self.assertRaises(urllib.error.HTTPError) as ctx:
                urllib.request.urlopen(base + "/api/retro/thumb/..%2F..%2Fetc", timeout=5)
            self.assertEqual(ctx.exception.code, 404)
            self.assertEqual(len(self.c.retro.list()), 1)
        self.assertEqual(list((self.data / "retro-incoming").iterdir()), [])


class HomeSwitchTests(unittest.TestCase):
    def home(self, retro_count, listing=()):
        import homescreen
        retro = mock.Mock()
        retro.count.return_value = retro_count
        retro.list.return_value = list(listing)
        return homescreen, SimpleNamespace(bg_source="photos", bg_pick="shuffle", bg_gen=0, retro=retro,
                                           library=mock.Mock(), pending=("x",), loading=True, photo_due=5.0,
                                           preload=mock.Mock())

    def test_provider_choice_and_empty_retro_fallback(self):
        hs, h = self.home(2, [{"id": "a" * 12}])
        self.assertEqual(hs.AmbientHomeScreen.provider(h), (h.library, None))
        h.bg_source = "retro"
        self.assertEqual(hs.AmbientHomeScreen.provider(h), (h.retro, None))
        h.bg_pick = "a" * 12
        self.assertEqual(hs.AmbientHomeScreen.provider(h), (h.retro, "a" * 12))
        h.bg_pick = "b" * 12                                    # picture was removed: shuffle instead
        self.assertEqual(hs.AmbientHomeScreen.provider(h), (h.retro, None))
        h.retro.count.return_value = 0
        self.assertEqual(hs.AmbientHomeScreen.provider(h), (h.library, None))

    def test_switch_drops_stale_loads_and_reloads(self):
        hs, h = self.home(1)
        hs.AmbientHomeScreen.set_background(h, "retro", "shuffle")
        self.assertEqual((h.bg_source, h.bg_gen, h.pending, h.loading, h.photo_due), ("retro", 1, None, False, 0.0))
        h.preload.assert_called_once()
        hs.AmbientHomeScreen.set_background(h, "retro", "shuffle")
        h.preload.assert_called_once()                          # unchanged: nothing to do
        h.pending = ("fresh",)
        self.assertFalse(hs.AmbientHomeScreen.preloaded(h, {}, object(), 0))
        self.assertEqual(h.pending, ("fresh",))                 # the stale result was ignored

    def test_circuit_paths_only_for_retro_items(self):
        import homescreen
        with tempfile.TemporaryDirectory() as d, mock.patch.object(homescreen, "traced_paths", return_value=[[(0, 0), (1, 1)]]) as tp, \
                mock.patch.object(homescreen, "load_surface", return_value=(mock.Mock(), mock.Mock())), \
                mock.patch.object(homescreen, "traced", return_value=None), \
                mock.patch.object(homescreen, "surface_palette", return_value=None), \
                mock.patch.object(homescreen.GLib, "idle_add") as idle, \
                mock.patch.object(homescreen.threading, "Thread", side_effect=lambda target, daemon: SimpleNamespace(start=target)):
            for source, expect in (("retro", [[(0, 0), (1, 1)]]), ("bing", [])):
                prov = mock.Mock()
                prov.next.return_value = {"path": "/p.png", "source": source}
                h = SimpleNamespace(style="network", viz_on=True, shell=SimpleNamespace(sw=1920, sh=1080), loading=False,
                                    bg_gen=0, retro=mock.Mock(), library=prov, preloaded=None)
                h.provider = lambda prov=prov: (prov, None)
                homescreen.AmbientHomeScreen.preload(h)
                item = idle.call_args[0][1]
                self.assertEqual(item["reactive_paths"], expect)


if __name__ == "__main__":
    unittest.main()
