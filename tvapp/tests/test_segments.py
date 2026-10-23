import io
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest import mock
import urllib.error

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from media import segments  # noqa: E402

FIX = Path(__file__).resolve().parent / "fixtures"
GOT = (FIX / "theintrodb_got_s01e01.json").read_text()
MISS = (FIX / "theintrodb_404.json").read_text()


class Resp:
    def __init__(self, body):
        self.body = body.encode()

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def read(self, n=-1):
        return self.body


def http(code, body=MISS):
    return urllib.error.HTTPError("u", code, "x", {}, io.BytesIO(body.encode()))


def ch(*pairs):
    return [{"title": t, "time": s} for t, s in pairs]


class ChapterTest(unittest.TestCase):
    def test_mapping(self):
        c = ch(("Recap", 0), ("Opening", 60), ("Chapter 1", 150), ("Chapter 2", 1400), ("Ending", 2700),
               ("Next Episode Preview", 2780))
        s = segments.from_chapters(c, 2800)
        self.assertEqual([(x["kind"], x["start"], x["end"]) for x in s],
                         [("recap", 0, 60), ("intro", 60, 150), ("credits", 2700, 2780), ("preview", 2780, 2800)])
        self.assertTrue(all(x["source"] == "chapters" for x in s))

    def test_titles(self):
        k = segments._kind
        for t, want in [("Intro", "intro"), ("OP", "intro"), ("OP1", "intro"), ("Opening Credits", "intro"),
                        ("Générique", "intro"), ("Vorspann", "intro"), ("Previously on Lost", "recap"),
                        ("End Credits", "credits"), ("ED", "credits"), ("Outro", "credits"),
                        ("Générique de fin", "credits"), ("Closing Credits", "credits"), ("Preview", "preview"),
                        ("Chapter 3", None), ("Ed's story", None), ("", None), (None, None)]:
            self.assertEqual(k(t), want, t)

    def test_sanity(self):
        c = ch(("Intro", 0), ("a", 3), ("Intro", 100), ("b", 400),   # 3 s too short; 300 s intro too long
               ("Recap", 1500), ("c", 1600),                          # recap too late
               ("Credits", 1000), ("d", 1100),                        # credits too early
               ("Credits", 2300))
        s = segments.from_chapters(c, 2400)
        self.assertEqual([x["kind"] for x in s], ["credits"])
        self.assertEqual(s[0]["start"], 2300)

    def test_bad_input(self):
        self.assertEqual(segments.from_chapters(None, 100), [])
        self.assertEqual(segments.from_chapters([{"title": "Intro"}], 100), [])
        self.assertEqual(segments.from_chapters(ch(("Intro", 10)), 0), [])   # no end known

    def test_unsorted_input(self):
        s = segments.from_chapters(ch(("Chapter 1", 90), ("Intro", 10)), 0)
        self.assertEqual([(x["start"], x["end"]) for x in s], [(10, 90)])


class MergeActiveTest(unittest.TestCase):
    def test_merge_precedence(self):
        a = [{"kind": "intro", "start": 10, "end": 70, "source": "chapters"}]
        b = [{"kind": "intro", "start": 5, "end": 50, "source": "theintrodb"},
             {"kind": "credits", "start": 2000, "end": 2400, "source": "theintrodb"}]
        m = segments.merge(a, b)
        self.assertEqual([(x["kind"], x["source"]) for x in m], [("intro", "chapters"), ("credits", "theintrodb")])
        self.assertEqual(segments.merge([], b), sorted(b, key=lambda s: s["start"]))
        self.assertEqual(segments.merge(None, None), [])

    def test_active(self):
        s = [{"kind": "intro", "start": 10, "end": 70, "source": "chapters"},
             {"kind": "credits", "start": 2000, "end": float("inf"), "source": "theintrodb"}]
        self.assertIsNone(segments.active(s, 9.9))
        self.assertEqual(segments.active(s, 10)["kind"], "intro")
        self.assertIsNone(segments.active(s, 70))
        self.assertEqual(segments.active(s, 99999)["kind"], "credits")
        self.assertIsNone(segments.active([], 5))


class LookupTest(unittest.TestCase):
    def setUp(self):
        self._t = tempfile.TemporaryDirectory()
        self.dir = Path(self._t.name)

    def tearDown(self):
        self._t.cleanup()

    def look(self, **kw):
        return segments.lookup("tt0944947", 1, 1, data_dir=self.dir, **kw)

    def age(self, days):
        f = next((self.dir / "segments").glob("*.json"))
        d = json.loads(f.read_text())
        d["t"] -= days * 86400
        f.write_text(json.dumps(d))

    def test_parse_real_response(self):
        with mock.patch("urllib.request.urlopen", return_value=Resp(GOT)) as u:
            s = self.look(duration=3700)
        self.assertEqual([(x["kind"], x["source"]) for x in s], [("intro", "theintrodb"), ("credits", "theintrodb")])
        self.assertAlmostEqual(s[0]["start"], 435.691)
        self.assertAlmostEqual(s[0]["end"], 534.851)
        self.assertEqual(s[1]["end"], 3700)
        req = u.call_args[0][0]
        self.assertEqual(req.full_url, "https://api.theintrodb.org/v4/media?imdb_id=tt0944947&season=1&episode=1")
        self.assertEqual(req.get_header("User-agent"), "imactv/1.0")

    def test_open_end_without_duration_and_id_priority(self):
        with mock.patch("urllib.request.urlopen", return_value=Resp(GOT)) as u:
            s = self.look(tvdb_id=121361, tmdb_id=1399)
        self.assertEqual(s[1]["end"], float("inf"))
        url = u.call_args[0][0].full_url
        self.assertIn("tvdb_id=121361", url)
        self.assertNotIn("tt0944947", url)
        self.assertNotIn("tmdb", url)

    def test_movie_has_no_season(self):
        body = '{"credits":[{"start_ms":8300000,"end_ms":null}]}'
        with mock.patch("urllib.request.urlopen", return_value=Resp(body)) as u:
            segments.lookup("tt0111161", None, None, data_dir=self.dir)
        self.assertEqual(u.call_args[0][0].full_url, "https://api.theintrodb.org/v4/media?imdb_id=tt0111161")

    def test_hit_cached_then_expires(self):
        with mock.patch("urllib.request.urlopen", return_value=Resp(GOT)) as u:
            self.look(); self.look()
            self.assertEqual(u.call_count, 1)
            self.age(29)
            self.look()
            self.assertEqual(u.call_count, 1)
            self.age(2)
            self.look()
            self.assertEqual(u.call_count, 2)

    def test_miss_cached_three_days(self):
        with mock.patch("urllib.request.urlopen", side_effect=http(404)) as u:
            self.assertEqual(self.look(), [])
            self.assertEqual(self.look(), [])
            self.assertEqual(u.call_count, 1)
            self.age(4)
            self.look()
            self.assertEqual(u.call_count, 2)

    def test_failures_return_empty_and_are_not_cached(self):
        for exc in (http(429), http(500), urllib.error.URLError("down"), TimeoutError(), ValueError()):
            with mock.patch("urllib.request.urlopen", side_effect=exc):
                self.assertEqual(self.look(), [])
        with mock.patch("urllib.request.urlopen", return_value=Resp("not json")):
            self.assertEqual(self.look(), [])
        self.assertFalse((self.dir / "segments").exists())

    def test_corrupt_cache_and_bad_ids(self):
        (self.dir / "segments").mkdir()
        (self.dir / "segments/imdb0944947-s1e1.json").write_text("{bad")
        with mock.patch("urllib.request.urlopen", return_value=Resp(GOT)):
            self.assertEqual(len(self.look()), 2)
        with mock.patch("urllib.request.urlopen") as u:
            self.assertEqual(segments.lookup("garbage", 1, 1, data_dir=self.dir), [])
            self.assertEqual(segments.lookup(None, 1, 1, data_dir=self.dir), [])
            u.assert_not_called()

    def test_sanity_applies_to_db(self):
        doc = ('{"intro":[{"start_ms":null,"end_ms":2000},{"start_ms":0,"end_ms":300000}],'
               '"credits":[{"start_ms":100000,"end_ms":null}]}')
        with mock.patch("urllib.request.urlopen", return_value=Resp(doc)):
            self.assertEqual(self.look(duration=3000), [])


if __name__ == "__main__":
    unittest.main()
