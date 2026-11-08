import io
import json
import os
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from media import addons, arabic, arabsubs  # noqa: E402


def zip_of(files):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        for name, data in files.items():
            z.writestr(name, data)
    return buf.getvalue()


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.env = mock.patch.dict(os.environ, {"IMACTV_DATA_DIR": self.tmp.name})
        self.env.start()

    def tearDown(self):
        self.env.stop()
        self.tmp.cleanup()


class Parsing(Base):
    def test_episode_names(self):
        self.assertEqual(arabsubs.episode_of("Show.S02E05.1080p.WEB"), (2, 5))
        self.assertEqual(arabsubs.episode_of("show 2x05 hdtv"), (2, 5))
        self.assertIsNone(arabsubs.episode_of("Movie.2019.1080p"))
        self.assertEqual(arabsubs.episode_range("Show.S01E01-E03"), (1, 1, 3))
        self.assertTrue(arabsubs.fits_episode("Show.S01E01-03.WEB", 1, 2))
        self.assertFalse(arabsubs.fits_episode("Show.S01E04.WEB", 1, 2))
        self.assertTrue(arabsubs.fits_episode("Show.Season.1.WEB", 1, 2, pack_ok=True))
        self.assertFalse(arabsubs.fits_episode("Show.Season.1.WEB", 1, 2))

    def test_similarity_prefers_same_group(self):
        rel = "Dune.Part.Two.2024.1080p.WEB-DL.DDP5.1.H.264-FLUX"
        a = arabsubs.similarity(rel, "Dune.Part.Two.2024.1080p.WEB-DL.H264-FLUX")
        b = arabsubs.similarity(rel, "Dune.Part.Two.2024.2160p.BluRay.REMUX-FGT")
        self.assertGreater(a, b)

    def test_cp1256_becomes_utf8(self):
        text = "مرحبا بالعالم"
        self.assertEqual(arabsubs.to_utf8(text.encode("cp1256")).decode("utf-8"), text)
        self.assertEqual(arabsubs.to_utf8(b"\xef\xbb\xbf" + text.encode()).decode(), text)

    def test_pick_file_by_episode(self):
        blob = zip_of({"Show.S01E01.srt": b"1", "Show.S01E02.srt": b"2", "readme.txt": b"x"})
        self.assertEqual(arabsubs.pick_file(blob, 1, 2), ("Show.S01E02.srt", b"2"))
        self.assertIsNone(arabsubs.pick_file(blob, 1, 9))
        self.assertEqual(arabsubs.pick_file(zip_of({"movie.srt": b"m"}), None, None), ("movie.srt", b"m"))


class Find(Base):
    def test_nothing_without_keys(self):
        self.assertFalse(arabsubs.available())
        self.assertEqual(arabsubs.find("movie", "tt1"), [])
        self.assertFalse(arabsubs.wanted({"title": "x"}))

    def test_arabp2p_sources_skip(self):
        arabic.update_creds(subdl_key="k" * 20)
        self.assertTrue(arabsubs.wanted({"title": "x"}))
        self.assertFalse(arabsubs.wanted({"site": "ArabP2P"}))
        self.assertTrue(arabic.status_public()["subdl"])
        self.assertFalse(arabic.status_public()["subsource"])

    def test_find_ranks_and_extracts(self):
        arabic.update_creds(subdl_key="k" * 20, subsource_key="s" * 20)
        calls = []

        def fake_get(url, headers=None, raw=False, ttl=0):
            calls.append(url)
            if url.startswith(arabsubs.SUBDL_API):
                self.assertIn("languages=AR", url)
                self.assertIn("season_number=1", url)
                return {"subtitles": [
                    {"release_name": "Show.S01E02.720p.HDTV-AAA", "url": "/subtitle/1.zip", "lang": "arabic"},
                    {"release_name": "Show.S01E02.1080p.WEB-DL-GRP", "url": "/subtitle/2.zip", "lang": "arabic"},
                    {"release_name": "Show.S01E03.1080p.WEB-DL-GRP", "url": "/subtitle/3.zip", "lang": "arabic"}]}
            if "/movies/search" in url:
                self.assertEqual(headers["X-API-Key"], "s" * 20)
                return {"data": [{"movieId": 77, "season": 1}]}
            if "/subtitles?" in url:
                return {"data": [{"subtitleId": 9, "releaseInfo": ["Show.S01E02.1080p.WEB-DL-GRP"], "files": 1}]}
            if url.endswith("/2.zip"):
                return zip_of({"Show.S01E02.srt": "سطر".encode("cp1256")})
            if url.endswith("/9/download"):
                return zip_of({"a.srt": b"subsource"})
            return zip_of({"x.srt": b"other"})

        with mock.patch.object(arabsubs, "_get", fake_get):
            tracks = arabsubs.find("series", "tt5:1:2", "Show.S01E02.1080p.WEB-DL.x264-GRP")
        self.assertEqual(len(tracks), 2)
        self.assertEqual([t["label"] for t in tracks], ["Arabic · SubDL", "Arabic · Subsource"])
        self.assertTrue(all(t["lang"] == "ara" for t in tracks))
        self.assertEqual(Path(tracks[0]["url"]).read_text(encoding="utf-8"), "سطر")
        self.assertFalse(any(u.endswith("/3.zip") for u in calls))     # another episode is never fetched

    def test_rejected_key_is_skipped(self):
        arabic.update_creds(subdl_key="k" * 20)

        def broken(*a, **k):
            raise arabsubs.SubtitleError("key rejected")
        with mock.patch.object(arabsubs, "_get", broken):
            self.assertEqual(arabsubs.find("movie", "tt1"), [])


class ArabicFilters(Base):
    def test_skipped_releases(self):
        self.assertTrue(arabic.skipped("ناروتو مدبلج تونسي الحلقة 3"))
        self.assertTrue(arabic.skipped("المؤسس عثمان - خلف الكواليس"))
        self.assertFalse(arabic.skipped("ناروتو مدبلج الحلقة 3"))

    def test_name_overrides_lead(self):
        Path(self.tmp.name, "arabic_names.json").write_text(json.dumps({"tt9": ["المحقق كونان", "not arabic"]}))
        with mock.patch.object(arabic, "_arabic_names_found", return_value=["كونان", "المحقق كونان"]):
            self.assertEqual(arabic.arabic_names("series", "tt9"), ["المحقق كونان", "كونان"])
            self.assertEqual(arabic.arabic_names("series", "tt8"), ["كونان", "المحقق كونان"])


class AnimeCatalog(Base):
    def test_maps_kitsu_to_imdb_and_dedupes(self):
        metas = [{"id": "kitsu:1", "imdb_id": "tt100", "type": "series", "name": "Show"},
                 {"id": "kitsu:2", "imdb_id": "tt100", "type": "series", "name": "Show 2"},
                 {"id": "kitsu:3", "type": "series", "name": "No imdb"},
                 {"id": "kitsu:4", "imdb_id": "tt200", "type": "movie", "name": "Film"}]
        with mock.patch.object(addons, "_fetch", return_value={"metas": metas}) as fetch:
            out = addons.anime_catalog("kitsu-anime-popular", "Slice of Life")
        self.assertIn("/catalog/anime/kitsu-anime-popular/genre=Slice%20of%20Life.json", fetch.call_args[0][0])
        self.assertEqual([(m["id"], m["type"], m["name"]) for m in out],
                         [("tt100", "series", "Show"), ("tt200", "movie", "Film")])


if __name__ == "__main__":
    unittest.main()
