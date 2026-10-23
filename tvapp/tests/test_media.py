import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest import mock
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
_temp = tempfile.TemporaryDirectory(prefix="imactv-media-test-")
os.environ["IMACTV_DATA_DIR"] = _temp.name

from media import addons, config, engine, player, streams, subs  # noqa: E402

FIX = Path(__file__).parent / "fixtures"


def fixture(name):
    return json.loads((FIX / name).read_text())


class ConfigTests(unittest.TestCase):
    def test_defaults_and_override_merge(self):
        (Path(_temp.name) / "media.json").write_text(json.dumps({"max_size_gb": 5, "addons": {"subtitles": "http://x"}}))
        try:
            cfg = config.load()
        finally:
            (Path(_temp.name) / "media.json").unlink()
        self.assertEqual(cfg["max_size_gb"], 5)
        self.assertEqual(cfg["addons"]["subtitles"], "http://x")
        self.assertIn("torrentio", cfg["addons"]["streams"][0])
        self.assertEqual(config.load()["max_size_gb"], 25)


class StreamTests(unittest.TestCase):
    def test_parse_torrentio_title(self):
        src = streams.parse({"name": "Torrentio\n1080p", "infoHash": "A" * 40, "fileIdx": 3,
                             "title": "Movie.2019.1080p.WEB-DL.x264\n👤 312 💾 2.4 GB ⚙️ ThePirateBay\n🇬🇧 / 🇮🇹"})
        self.assertEqual((src["quality"], src["source"], src["codec"], src["seeders"]), ("1080p", "WEB-DL", "H264", 312))
        self.assertEqual(src["size_bytes"], 2_400_000_000)
        self.assertEqual(src["file_idx"], 3)
        self.assertEqual(src["info_hash"], "a" * 40)
        self.assertEqual(src["languages"], ["en", "it"])
        self.assertEqual(src["label"], "1080p · WEB-DL · 2.4 GB · 312 seeds")

    def test_hdr_and_4k_and_multi_audio(self):
        src = streams.parse({"name": "Torrentio\n4k HDR", "infoHash": "b" * 40,
                             "title": "X 2160p HDR10 HEVC\n👤 4 💾 15.77 GB ⚙️ 1337x\nMulti Audio"})
        self.assertEqual((src["quality"], src["hdr"], src["codec"], src["file_idx"]), ("2160p", True, "HEVC", None))
        self.assertIn("multi", src["languages"])

    def test_real_fixtures_parse_and_rank(self):
        cfg = config.load()
        for name in ("torrentio_movie.json", "torrentio_series.json"):
            parsed = [streams.parse(s) for s in fixture(name)["streams"]]
            ranked = streams.rank(parsed, cfg)
            self.assertTrue(ranked)
            best = streams.best(parsed, cfg)
            self.assertEqual(best["quality"], "1080p")
            self.assertGreaterEqual(best["seeders"], 5)
            self.assertLessEqual(best["size_bytes"], cfg["max_size_gb"] * 1e9)

    def test_rank_filters_cam_oversize_and_missing_hash(self):
        def mk(**kw):
            base = {"info_hash": "c" * 40, "file_idx": 0, "title": "Movie", "quality": "1080p", "hdr": False,
                    "dv": False, "size_bytes": 1e9, "seeders": 50}
            return dict(base, **kw)
        cfg = config.load()
        pool = [mk(title="Movie HDCAM"), mk(size_bytes=40e9), mk(info_hash=""), mk(quality="720p", seeders=500)]
        self.assertEqual(streams.best(pool, cfg)["quality"], "720p")
        self.assertIsNone(streams.best(pool[:3], cfg))

    def test_rank_prefers_seeded_over_better_quality(self):
        a = {"info_hash": "a" * 40, "title": "a", "quality": "1080p", "dv": False, "size_bytes": 1e9, "seeders": 1}
        b = dict(a, info_hash="b" * 40, quality="720p", seeders=200)
        self.assertEqual(streams.best([a, b], config.load())["info_hash"], "b" * 40)


class SubsTests(unittest.TestCase):
    def test_pick_normalises_and_limits(self):
        lst = [{"url": f"https://s/{i}", "lang": lang} for i, lang in
               enumerate(["ar", "ara", "ara", "nld", "dut", "en", "eng", "eng", "fre"])]
        picked = subs.pick(lst, ["ara", "dut", "eng"])
        self.assertEqual([p["lang"] for p in picked], ["ara", "ara", "dut", "dut", "eng", "eng"])
        self.assertEqual([p["label"] for p in picked[::2]], ["Arabic", "Nederlands", "English"])

    def test_pick_real_fixture_and_rejects_bad_urls(self):
        picked = subs.pick(fixture("opensubs.json")["subtitles"], ["ara", "dut", "eng"])
        self.assertTrue(picked and all(p["lang"] == "eng" for p in picked))
        self.assertEqual(subs.pick([{"url": "file:///etc/passwd", "lang": "eng"}], ["eng"]), [])


class PlayerTests(unittest.TestCase):
    def test_argv(self):
        argv = player.mpv_argv("http://h/u", title="T", subs=[{"url": "https://s/1.srt"}], start=95.5)
        for flag in ("--fs", "--input-ipc-server=/tmp/mpv.sock", "--force-window=immediate", "--keep-open=no",
                     "--slang=ar,ara,nl,dut,nld,en,eng", "--sub-file=https://s/1.srt", "--start=95.5",
                     "--title=T", "--force-media-title=T", "--sub-font=Noto Sans", "--osd-font=Inter", "--cache=yes"):
            self.assertIn(flag, argv)
        self.assertTrue(any(a.startswith("--demuxer-max-bytes=") for a in argv))
        self.assertEqual(argv[-2:], ["--", "http://h/u"])
        self.assertFalse(any(a.startswith("--start") for a in player.mpv_argv("u", title="T", subs=[])))


class AddonTests(unittest.TestCase):
    def fake(self, payload):
        class Resp:
            def __enter__(s): return s
            def __exit__(s, *a): return False
            def read(s, n=-1): return json.dumps(payload).encode()
        return mock.patch.object(addons.urllib.request, "urlopen", return_value=Resp())

    def test_urls_and_cache(self):
        with self.fake(fixture("torrentio_movie.json")) as op:
            first = addons.streams("movie", "tt0063350")
            again = addons.streams("movie", "tt0063350")
        self.assertEqual(first, again)
        self.assertEqual(op.call_count, 1)
        self.assertTrue(op.call_args.args[0].full_url.endswith("/stream/movie/tt0063350.json"))

    def test_series_search_and_catalog_paths(self):
        with self.fake({"streams": []}) as op:
            addons.streams("series", "tt0903747:1:1")
        self.assertTrue(op.call_args.args[0].full_url.endswith("/stream/series/tt0903747:1:1.json"))
        with self.fake({"metas": [{"id": "tt1"}]}) as op:
            self.assertEqual(addons.search("movie", "night of"), [{"id": "tt1"}])
            addons.catalog("movie", "top", genre="Horror", skip=100)
        self.assertTrue(op.call_args_list[0].args[0].full_url.endswith("/catalog/movie/top/search=night%20of.json"))
        self.assertTrue(op.call_args.args[0].full_url.endswith("/catalog/movie/top/genre=Horror&skip=100.json"))

    def test_meta_and_errors(self):
        with self.fake(fixture("cinemeta_meta.json")):
            self.assertEqual(addons.meta("movie", "tt0063350")["name"], "Night of the Living Dead")
        with mock.patch.object(addons.urllib.request, "urlopen", side_effect=OSError("down")):
            with self.assertRaises(addons.AddonError):
                addons.meta("movie", "tt9999999")
        with self.assertRaises(addons.AddonError):
            addons.meta("music", "tt1")


class EngineTests(unittest.TestCase):
    def test_url_and_stats(self):
        eng = engine.Engine("http://127.0.0.1:11470/")
        self.assertEqual(eng.url("a" * 40, 2), f"http://127.0.0.1:11470/{'a' * 40}/2")
        with mock.patch.object(eng, "_get", return_value=b'{"swarmConnections": 7, "downloadSpeed": 1500.5, "downloaded": 99}'):
            self.assertEqual(eng.stats("a" * 40, 0), {"peers": 7, "speed": 1500.5, "downloaded": 99})
        with mock.patch.object(eng, "_get", side_effect=OSError):
            self.assertIsNone(eng.stats("a" * 40, 0))
            self.assertFalse(eng.healthy())


if __name__ == "__main__":
    unittest.main()
