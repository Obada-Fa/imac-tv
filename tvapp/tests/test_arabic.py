import json
import os
import tempfile
import unittest
from unittest import mock

from media import addons, arabic, config, health, streams, torbox

FILE_HEADER_PAGE = """
<div class="file-header"><span class="tor_link">المحقق كونان [05] [م1] مدبلج 720p</span>
 <a href="index.php?page=torrents&amp;category=100">Dub</a>
 <a href="download.php?id=111&amp;f=conan05.torrent">dl</a>
 <span class="size">350.5 MB</span><span title="Seeders">12</span><span title="Leechers">3</span>
 <span title='24-03-01 10:00:00 PM'>x</span></div>
<div class="file-header"><span class="tor_link">المحقق كونان الموسم الاول كامل مدبلج</span>
 <a href="magnet:?xt=urn:btih:ABCDEF0123456789ABCDEF0123456789ABCDEF01&amp;tr=udp://t">m</a>
 <span class="size">8.1 GB</span><span title="Seeders"><b>4</b></span></div>
<div class="file-header"><span class="tor_link">المحقق كونان [06] [م1] مترجم</span>
 <a href="download.php?id=113&amp;f=conan06.torrent">dl</a></div>
<div class="file-header"><span class="tor_link">no link here</span></div>
"""

# The layout ArabP2P serves to a signed-in member (2026-11), trimmed to one row.
REAL_ROW = """<table id="torrents_list_p"><tr><td><div class="file-list"><div class="file-item">
<div class="file-header"><div class="filename"> <a name='t_url' href="index.php?page=torrent-details&amp;id=116292"
 class="screenshot" rel="https://i/x.jpg"><span class="tor_link">ليجو ون بيس [مدبلج] Lego One Piece [2026] [S01] [1080p] </span></a></div>
<button class="download-btn"><a href="download.php?id=116292&amp;f=%D9%84.torrent">تحميل</a></button></div>
<div class="file-meta"><div class="meta-group"><span class="category"><a name='cat_url'
 href='index.php?page=torrents&amp;category=100'>انمي مدبلج عربي</a> - <a name='cat_url'
 href='index.php?page=torrents&amp;category=100'>مسلسلات</a></span><span class="size">1.01 GB</span></div>
<div class="stats-group"><span class="green" title="Seeders">▲ <a style='color: green;' href='index.php?page=peers&amp;id=116292'
 title='x'>11</a></span><span class="red" title="Leechers">▼ <a href='index.php?page=peers&amp;id=116292'>2</a></span></div>
</div></div></div></td></tr></table>"""

TABLE_PAGE = """<table id="torrents_list_p"><tbody>
<tr><td><a href="index.php?page=torrents&amp;category=7">c</a></td>
<td><a href="index.php?page=torrent-details&amp;id=900">باب الحارة [12] [م2] 1080p</a></td>
<td><a href="download.php?id=900&amp;f=bab.torrent">d</a></td><td><span class="size">1.5 GB</span></td>
<td><span title="Seeders">7</span></td></tr>
</tbody></table>"""


class Temp(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.env = mock.patch.dict(os.environ, {"IMACTV_DATA_DIR": self.tmp.name})
        self.env.start()
        arabic._links.clear()

    def tearDown(self):
        self.env.stop()
        self.tmp.cleanup()


class Parsing(unittest.TestCase):
    def test_episode_formats(self):
        self.assertEqual(arabic.parse_episode("المحقق كونان [05] [م1] مدبلج"), (1, [5]))
        self.assertEqual(arabic.parse_episode("Show S02E03 1080p"), (2, [3]))
        self.assertEqual(arabic.parse_episode("Show S01E01-E04"), (1, [1, 2, 3, 4]))
        self.assertEqual(arabic.parse_episode("مسلسل الحلقة 14 الموسم الثاني"), (2, [14]))
        self.assertEqual(arabic.parse_episode("مسلسل الموسم الاول كامل"), (1, []))
        self.assertEqual(arabic.parse_episode("[E08] [S01] Yango"), (1, [8]))
        self.assertIsNone(arabic.parse_episode("A movie 2019 1080p"))

    def test_file_episode(self):
        self.assertEqual(arabic.file_episode("Conan/Conan S01E07 720p.mkv"), 7)
        self.assertEqual(arabic.file_episode("[Spacetoon] Conan - 12.mp4"), 12)
        self.assertEqual(arabic.file_episode("كونان الحلقة 03.mp4"), 3)
        self.assertEqual(arabic.file_episode("Conan [021] 1080p x264.mkv"), 21)

    def test_dub_markers(self):
        self.assertTrue(arabic.is_dub("ون بيس مدبلج"))
        self.assertTrue(arabic.is_dub("anything", category="100"))
        self.assertFalse(arabic.is_dub("ون بيس مترجم"))
        self.assertTrue(arabic.subtitle_only("ون بيس مترجم"))
        self.assertFalse(arabic.subtitle_only("ون بيس مترجم مدبلج"))

    def test_normalised_match(self):
        self.assertTrue(arabic.matches("المحقق كونان [05]", ["المحقّق كونان"], ""))
        self.assertTrue(arabic.matches("Detective.Conan.S01E05", [], "Detective Conan"))
        self.assertFalse(arabic.matches("Conan the Barbarian", [], "Detective Conan"))

    def test_file_header_layout(self):
        rows = arabic.parse_results(FILE_HEADER_PAGE)
        self.assertEqual([r["id"] for r in rows], ["111", "ABCDEF0123456789ABCDEF0123456789ABCDEF01", "113"])
        first = rows[0]
        self.assertEqual(first["download"], "download.php?id=111&f=conan05.torrent")
        self.assertEqual((first["seeders"], first["leechers"], first["category"]), (12, 3, "100"))
        self.assertEqual(first["size"], 350_500_000)
        self.assertTrue(rows[1]["magnet"].startswith("magnet:?xt=urn:btih:ABCDEF"))
        self.assertIn("&tr=", rows[1]["magnet"])
        self.assertEqual(rows[1]["seeders"], 4)

    def test_real_layout(self):
        row = arabic.parse_results(REAL_ROW)[0]
        self.assertEqual((row["id"], row["seeders"], row["leechers"], row["category"]), ("116292", 11, 2, "100"))
        self.assertEqual(row["size"], 1_010_000_000)
        self.assertEqual(arabic.language(row), "dub")

    def test_real_titles(self):
        self.assertEqual(arabic.parse_episode("باب الحارة [2015][م7][WEB DL][SHAHID][1080p]"), (7, []))
        self.assertEqual(arabic.parse_episode("المحقق كونان الجزء الحادي عشر كامل مدبلج عربي"), (11, []))
        self.assertEqual(arabic.parse_episode("ون بيس [مدبلج][مترجم][62 92][1999][NF][1080p]")[1][:2], [62, 63])
        self.assertEqual(arabic.parse_episode("One Piece 1999 (ح01 ح51) مدبلج")[1][-1], 51)
        self.assertEqual(arabic.parse_episode("المحقق كونان الجزء الثاني عشر الحلقات 11 12 مدبلجة"), (12, [11, 12]))
        self.assertEqual(arabic.parse_episode("حلقات كونان نسخة نتفلكس (0284~0332)[1080x1440]")[1][0], 284)
        found = arabic.parse_release("ون بيس One Piece [E1156 E1180][SHAHID][1080p]")
        self.assertFalse(found["explicit"])
        self.assertEqual(found["eps"][0], 1156)

    def test_language_from_category(self):
        self.assertEqual(arabic.language({"title": "باب الحارة [م7][SHAHID]", "category": "89"}), "arabic")
        self.assertEqual(arabic.language({"title": "One Piece [E1156][SHAHID]", "category": "103"}), "subs")
        self.assertEqual(arabic.language({"title": "x مدبلج", "category": None}), "dub")

    def test_name_must_lead(self):
        self.assertFalse(arabic.matches("ليجو ون بيس [مدبلج]", ["ون بيس"], "One Piece"))
        self.assertTrue(arabic.matches("انمي ون بيس [ح1 ح832]", ["ون بيس"], ""))
        self.assertTrue(arabic.matches("[إعادة رفع ح13] باب الحارة [م7]", ["باب الحارة"], ""))
        self.assertFalse(arabic.matches("Lego One Piece", [], "One Piece"))

    def test_movie_needs_its_own_title(self):
        name = ["المحقق كونان: اللحظة الأخيرة"]
        self.assertTrue(arabic.movie_matches("المحقق كونان الفيلم الأول ( اللحظة الأخيرة ) مدبلج", name, ""))
        self.assertFalse(arabic.movie_matches("المحقق كونان الفلم الثالث عشر (مطاردة الغراب )", name, ""))
        self.assertEqual(arabic.search_terms(name), ["المحقق كونان: اللحظة الأخيرة", "المحقق كونان"])

    def test_year_fits_season(self):
        self.assertFalse(arabic.year_fits("ون بيس [S01 S02][2023][NF]", (1999, 2000)))
        self.assertTrue(arabic.year_fits("باب الحارة [2015][م7]", (2015, 2015)))
        self.assertTrue(arabic.year_fits("no year here", (1999, 2000)))

    def test_table_layout(self):
        rows = arabic.parse_results(TABLE_PAGE)
        self.assertEqual(len(rows), 1)
        self.assertEqual((rows[0]["id"], rows[0]["title"], rows[0]["seeders"]), ("900", "باب الحارة [12] [م2] 1080p", 7))
        self.assertEqual(rows[0]["size"], 1_500_000_000)


class Streams(Temp):
    def fake(self, rows):
        return [mock.patch.object(arabic, "enabled", return_value=True),
                mock.patch.object(arabic, "arabic_names", return_value=["المحقق كونان"]),
                mock.patch.object(arabic, "search_site", return_value=rows)]

    def run_streams(self, rows, *args):
        patches = self.fake(rows)
        for p in patches:
            p.start()
        try:
            return arabic.streams(*args)
        finally:
            for p in patches:
                p.stop()

    def test_episode_and_pack_kept_subtitled_labelled(self):
        rows = arabic.parse_results(FILE_HEADER_PAGE)
        got = self.run_streams(rows, "series", "tt0131179:1:5", "Case Closed")
        titles = [s["title"].split("\n")[0] for s in got]
        self.assertEqual(titles, ["المحقق كونان [05] [م1] مدبلج 720p", "المحقق كونان الموسم الاول كامل مدبلج"])
        self.assertIn("Season pack", got[1]["title"])
        self.assertTrue(all(arabic.owns(s["url"]) for s in got))
        self.assertEqual(got[0]["nexusEpisodes"], [5])
        subs = self.run_streams(rows, "series", "tt0131179:1:6", "x")
        self.assertTrue(any("Arabic subs" in s["title"] for s in subs))

    def test_running_number_and_huge_pack(self):
        rows = [{"id": "1", "title": "ون بيس One Piece [E1156 E1180][SHAHID]", "category": "103", "size": 11e9,
                 "seeders": 8, "magnet": None, "download": "download.php?id=1&f=a"},
                {"id": "2", "title": "ون بيس One Piece [E0001 E1155] [CR]", "category": "101", "size": 186e9,
                 "seeders": 13, "magnet": None, "download": "download.php?id=2&f=b"}]
        with mock.patch.object(arabic, "arabic_names", return_value=["ون بيس"]), \
                mock.patch.object(arabic, "enabled", return_value=True), \
                mock.patch.object(arabic, "search_site", return_value=rows):
            got = arabic.streams("series", "tt0388629:22:70", "One Piece", "", 1160)
            self.assertEqual([g["nexusEpisodes"] for g in got], [[1160, 70]])
            self.assertEqual(arabic.streams("series", "tt0388629:1:5", "One Piece", "", 5), [])   # 186 GB pack: no

    def test_other_episode_or_season_skipped(self):
        rows = arabic.parse_results(FILE_HEADER_PAGE)
        self.assertEqual(len(self.run_streams(rows, "series", "tt0131179:1:9", "x")), 1)   # only the pack
        self.assertEqual(self.run_streams(rows, "series", "tt0131179:2:5", "x"), [])

    def test_off_without_setup(self):
        self.assertFalse(arabic.enabled())
        self.assertEqual(arabic.streams("movie", "tt1"), [])

    def test_parsed_source_label_and_ranking(self):
        rows = arabic.parse_results(FILE_HEADER_PAGE)
        src = [streams.parse(s) for s in self.run_streams(rows, "series", "tt0131179:1:5", "x")]
        self.assertEqual(src[0]["site"], "ArabP2P")
        self.assertTrue(src[0]["dub"])
        self.assertIn("ArabP2P · Arabic dub", src[0]["label"])
        self.assertIn("Season pack", src[1]["label"])
        torrent = streams.parse({"infoHash": "a" * 40, "title": "Case Closed S01E05 1080p WEB-DL\n👤 50 💾 1 GB"})
        ranked = streams.rank(src + [torrent], {"quality_order": ["1080p", "720p"]})
        self.assertEqual(ranked[0]["site"], "ArabP2P")                     # Arabic first in the list
        self.assertFalse(streams.best(src + [torrent], {}).get("site"))   # ...but not auto-played
        self.assertEqual(streams.best(src + [torrent], {"prefer_arabic": True})["site"], "ArabP2P")
        self.assertEqual(streams.best(src + [torrent], {}, True)["site"], "ArabP2P")
        self.assertEqual(streams.best(src, {})["site"], "ArabP2P")         # only Arabic: play it

    def test_health_never_swaps_language(self):
        arab = streams.parse({"name": "ArabP2P", "url": arabic.SITE + "/download.php?id=1&f=a",
                              "title": "x 1080p\n🇸🇦 Arabic dub 💾 4 GB 👤 3"})
        cached = streams.parse({"name": "Torrentio\nTB+ 720p", "url": "https://t/resolve/1", "title": "y 720p\n💾 1 GB"})
        self.assertIsNone(health.alternative(arab, [arab, cached], "bandwidth"))


class Credentials(Temp):
    def test_saved_private_and_enabled_needs_torbox(self):
        arabic.update_creds(arabp2p={"user": "u", "pass": "p"})
        path = config.data_dir() / arabic.CREDS_FILE
        self.assertEqual(path.stat().st_mode & 0o777, 0o600)
        self.assertEqual(arabic.status_public(), {"arabp2p": True, "user": "u", "tmdb": False, "ready": False})
        self.assertFalse(arabic.enabled())
        config.write_key("k" * 32)
        self.assertTrue(arabic.enabled())
        arabic.update_creds(arabp2p=None)
        self.assertFalse(arabic.status_public()["arabp2p"])
        self.assertNotIn("p", json.loads(path.read_text()).values())

    def test_cached_failure_not_remembered(self):
        calls = []
        self.assertIsNone(arabic.cached(("k",), 60, lambda: calls.append(1)))
        self.assertEqual(arabic.cached(("k",), 60, lambda: [1]), [1])
        self.assertEqual(arabic.cached(("k",), 60, lambda: [2]), [1])


class FetchTorrent(Temp):
    def test_magnet_from_search_needs_no_site(self):
        row = arabic.parse_results(FILE_HEADER_PAGE)[1]
        url = arabic.source_url(row)
        arabic._remember(url, row)
        arabic._links.clear()                      # survives a restart via links.json
        kind, value = arabic.fetch_torrent(url)
        self.assertEqual(kind, "magnet")
        self.assertTrue(value.startswith("magnet:"))

    def test_download_uses_session(self):
        row = arabic.parse_results(FILE_HEADER_PAGE)[0]
        url = arabic.source_url(row)
        arabic._remember(url, row)
        with mock.patch.object(arabic, "_session_get", return_value=b"d8:announce...e") as get:
            self.assertEqual(arabic.fetch_torrent(url), ("file", b"d8:announce...e"))
        get.assert_called_once_with("download.php?id=111&f=conan05.torrent")

    def test_html_instead_of_torrent_is_an_error(self):
        url = arabic.SITE + "/download.php?id=5&f=x.torrent"
        with mock.patch.object(arabic, "_session_get", return_value=b"<html>login</html>"):
            with self.assertRaises(arabic.ArabicError):
                arabic.fetch_torrent(url)

    def test_foreign_url_refused(self):
        with self.assertRaises(arabic.ArabicError):
            arabic.fetch_torrent("https://evil.example/download.php?id=1")


class TorBoxPrepare(Temp):
    def setUp(self):
        super().setUp()
        config.write_key("k" * 32)

    def test_waits_reports_and_picks_episode(self):
        polls = iter([{"id": 7, "download_state": "downloading", "progress": 0.4, "download_speed": 2e6, "seeds": 3},
                      {"id": 7, "download_state": "completed", "download_finished": True, "files": [
                          {"id": 0, "short_name": "Conan - 04.mkv", "size": 5},
                          {"id": 1, "short_name": "Conan - 05.mkv", "size": 6},
                          {"id": 2, "short_name": "cover.jpg", "size": 1}]}])
        calls = []

        def fake_call(method, path, key, params=None, body=None, content_type=None, timeout=20):
            calls.append((method, path, params))
            if path == "torrents/createtorrent":
                self.assertIn(b'name="magnet"', body)
                self.assertIn(b'name="seed"', body)
                return {"torrent_id": 7, "hash": "h"}
            if path == "torrents/mylist":
                return next(polls)
            if path == "torrents/requestdl":
                self.assertEqual(params["file_id"], 1)
                return "https://cdn.torbox/f"
        stages = []
        with mock.patch.object(arabic, "fetch_torrent", return_value=("magnet", "magnet:?xt=urn:btih:x")), \
                mock.patch.object(torbox, "_call", side_effect=fake_call):
            url = torbox.prepare(arabic.SITE + "/x", episode=5, on_progress=lambda *a: stages.append(a), sleep=lambda s: None)
        self.assertEqual(url, "https://cdn.torbox/f")
        self.assertIn("TorBox is downloading… 40%", [s[3] for s in stages])
        self.assertEqual([c[1] for c in calls].count("torrents/mylist"), 2)

    def test_file_upload_and_cancel(self):
        def fake_call(method, path, key, params=None, body=None, content_type=None, timeout=20):
            if path == "torrents/createtorrent":
                self.assertIn(b'filename="release.torrent"', body)
                return {"torrent_id": 1}
            return {"id": 1, "download_state": "downloading", "progress": 10}
        flags = iter([False, False, True])
        with mock.patch.object(arabic, "fetch_torrent", return_value=("file", b"d4:infoe")), \
                mock.patch.object(torbox, "_call", side_effect=fake_call):
            with self.assertRaises(torbox.Cancelled):
                torbox.prepare(arabic.SITE + "/x", cancelled=lambda: next(flags), sleep=lambda s: None)

    def test_missing_episode_in_pack(self):
        t = {"files": [{"id": 0, "short_name": "Conan - 01.mkv"}, {"id": 1, "short_name": "Conan - 02.mkv"}]}
        self.assertIsNone(torbox.pick_file(t["files"], 9))
        self.assertEqual(torbox.pick_file(t["files"][:1], 9)["id"], 0)   # single file: that's the one
        self.assertEqual(torbox.progress({"progress": 55}), 0.55)

    def test_error_codes_are_friendly(self):
        import urllib.error, io
        err = urllib.error.HTTPError("u", 400, "bad", {}, io.BytesIO(b'{"success":false,"error":"ACTIVE_LIMIT"}'))
        with mock.patch("urllib.request.urlopen", side_effect=err):
            with self.assertRaisesRegex(torbox.TorBoxError, "as many torrents"):
                torbox._call("GET", "torrents/mylist", "k")


class ArabicSearch(Temp):
    def test_arabic_query_goes_to_title_lookup(self):
        with mock.patch.object(arabic, "find_titles", return_value=["tt0131179"]) as find, \
                mock.patch.object(addons, "meta", return_value={"id": "tt0131179", "type": "series", "name": "Case Closed",
                                                                "poster": "p"}):
            got = addons.search("series", "كونان")
        find.assert_called_once_with("series", "كونان")
        self.assertEqual(got[0]["name"], "Case Closed")

    def test_streams_merge_arabic_first_and_survive_failures(self):
        with mock.patch.object(addons, "_fetch", return_value={"streams": [{"infoHash": "a" * 40}]}), \
                mock.patch.object(arabic, "enabled", return_value=True), \
                mock.patch.object(addons, "meta", return_value={"name": "X", "releaseInfo": "2001"}), \
                mock.patch.object(arabic, "streams", return_value=[{"name": "ArabP2P", "url": arabic.SITE + "/d"}]):
            got = addons.streams("movie", "tt1")
        self.assertEqual(got[0]["name"], "ArabP2P")
        with mock.patch.object(addons, "_fetch", return_value={"streams": [{"infoHash": "a" * 40}]}), \
                mock.patch.object(arabic, "enabled", side_effect=RuntimeError("boom")):
            self.assertEqual(len(addons.streams("movie", "tt1")), 1)


if __name__ == "__main__":
    unittest.main()


class Languages(unittest.TestCase):
    def test_player_tracks_follow_the_release(self):
        from media import player
        self.assertEqual(player.source_langs({"site": "ArabP2P", "lang": "dub"}, "en,eng"), (player.ARABIC, "en,eng"))
        alang, slang = player.source_langs({"site": "ArabP2P", "lang": "subs"}, "en,eng")
        self.assertIsNone(alang)
        self.assertTrue(slang.startswith("ar,ara"))
        self.assertEqual(player.source_langs({"lang": "dub"}, "en"), (None, "en"))     # not an Arabic source
        argv = player.mpv_argv("u", title="t", subs=[], alang=player.ARABIC)
        self.assertIn("--alang=ar,ara,arb,arabic", argv)
        self.assertNotIn("--alang", " ".join(player.mpv_argv("u", title="t", subs=[])))

    def test_dub_categories_searched_for_the_main_name(self):
        asked = []
        with mock.patch.object(arabic, "enabled", return_value=True), \
                mock.patch.object(arabic, "arabic_names", return_value=["المحقق كونان", "المتحري كونان"]), \
                mock.patch.object(arabic, "search_site", side_effect=lambda q, c=0: asked.append((q, c)) or []):
            arabic.streams("series", "tt0131179:1:1", "Detective Conan")
        self.assertIn(("المحقق كونان", "100"), asked)
        self.assertIn(("المحقق كونان", "102"), asked)
        self.assertNotIn(("المتحري كونان", "100"), asked)
        self.assertEqual(streams.label({"quality": "1080p", "hdr": False, "dv": False, "source": "", "size_bytes": 0,
                                        "site": "ArabP2P", "lang": "subs"}), "1080p · ArabP2P · Arabic subtitles")


class ShowLanguage(Temp):
    def test_picked_choice_beats_search_and_is_remembered(self):
        self.assertIsNone(arabic.preferred_language("tt1"))
        arabic.remember_language("tt1", "dub", soft=True)          # found by an Arabic search
        self.assertEqual(arabic.preferred_language("tt1"), "dub")
        arabic.remember_language("tt1", None)                      # the user picked a normal source
        arabic.remember_language("tt1", "dub", soft=True)          # a later Arabic search doesn't undo that
        self.assertIsNone(arabic.preferred_language("tt1"))
        arabic.remember_language("tt1", "subs")
        self.assertEqual(arabic.preferred_language("tt1"), "subs")
        arabic.remember_language("bad id", "dub")
        self.assertNotIn("bad id", arabic._shows())

    def test_best_follows_the_show_language(self):
        mk = lambda lang, q: streams.parse({"name": "ArabP2P", "url": arabic.SITE + f"/download.php?id={lang}{q}&f=x",
                                            "title": f"x {q}\n🇸🇦 {arabic.LANG_LABEL[lang]} 💾 1 GB 👤 5"})
        torrent = streams.parse({"infoHash": "b" * 40, "title": "Show S01E01 1080p\n👤 90 💾 1 GB"})
        found = [mk("dub", "720p"), mk("subs", "1080p"), torrent]
        self.assertFalse(streams.best(found, {}).get("site"))
        self.assertEqual(streams.best(found, {}, None, "subs")["lang"], "subs")
        self.assertEqual(streams.best(found, {}, None, "dub")["lang"], "dub")

    def test_phone_choice_and_best_match(self):
        self.assertEqual(arabic.choice("tt2"), "auto")
        arabic.remember_language("tt2", "dub", soft=True)          # found by an Arabic search: still "best match"
        self.assertEqual(arabic.choice("tt2"), "auto")
        arabic.set_choice("tt2", "original")                       # watching with someone who doesn't speak Arabic
        self.assertEqual((arabic.choice("tt2"), arabic.preferred_language("tt2")), ("original", None))
        arabic.remember_language("tt2", "dub", soft=True)          # searching again doesn't undo the pick
        self.assertEqual(arabic.choice("tt2"), "original")
        arabic.set_choice("tt2", "auto")                           # best match: back to what the search found
        self.assertEqual((arabic.choice("tt2"), arabic.preferred_language("tt2")), ("auto", "dub"))
        arabic.set_choice("tt2", "subs")
        self.assertEqual((arabic.choice("tt2"), arabic.preferred_language("tt2")), ("subs", "subs"))
        arabic.set_choice("tt3", "original")
        arabic.set_choice("tt3", "auto")                           # nothing was found by a search: forgotten
        self.assertNotIn("tt3", arabic._shows())
        for bad in (("tt2", "klingon"), ("bad", "dub")):
            with self.assertRaises(ValueError):
                arabic.set_choice(*bad)

    def test_best_quality_within_the_language(self):
        mk = lambda lang, q: streams.parse({"name": "ArabP2P", "url": arabic.SITE + f"/download.php?id={lang}{q}&f=x",
                                            "title": f"x {q}\n🇸🇦 {arabic.LANG_LABEL[lang]} 💾 1 GB 👤 5"})
        t = lambda q, h: streams.parse({"infoHash": h * 40, "title": f"Show S01E01 {q}\n👤 90 💾 1 GB"})
        found = [mk("dub", "720p"), mk("dub", "1080p"), t("1080p", "b"), t("720p", "c")]
        self.assertEqual(streams.best(found, {}, quality="720p")["info_hash"], "c" * 40)
        self.assertEqual(streams.best(found, {}, None, "dub", "720p")["quality"], "720p")
        got = streams.best(found, {"quality_order": ["2160p", "1080p", "720p"]}, None, "dub", "2160p")         # no 4K dub: the dub still wins over quality
        self.assertEqual((got["lang"], got["quality"]), ("dub", "1080p"))
        self.assertFalse(streams.best(found, {"prefer_arabic": True}, False).get("site"))

    def test_wikidata_kinds_filter_results(self):
        hits = [("tt0131179", "series"), ("tt1073223", "movie"), ("tt5", None)]
        with mock.patch.object(arabic, "_wikidata_titles", return_value=hits):
            self.assertEqual(arabic.find_titles("series", "المحقق كونان"), ["tt0131179", "tt5"])
            self.assertEqual(arabic.find_titles("movie", "المحقق كونان"), ["tt1073223", "tt5"])

    def test_arabic_search_marks_shows(self):
        with mock.patch.object(arabic, "find_titles", return_value=["tt0131179", "tt9"]), \
                mock.patch.object(addons, "meta", side_effect=lambda k, i: {"id": i, "type": "series" if i == "tt0131179"
                                                                              else "movie", "name": i}):
            got = addons.search("series", "كونان")
        self.assertEqual([m["id"] for m in got], ["tt0131179"])        # a film is not a series result
        self.assertEqual(arabic.preferred_language("tt0131179"), "dub")
        self.assertEqual(addons.running_number([{"season": 0, "episode": 1}, {"season": 1, "episode": 1},
                                                {"season": 1, "episode": 2}, {"season": 2, "episode": 1}],
                                               "tt1:2:1"), 3)
