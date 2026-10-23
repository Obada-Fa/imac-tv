"""YouTube music vs video: the classifier, the per-channel override store, the pop-up rules and the
ambient delay. Nothing here touches D-Bus, X or the network."""
import importlib.util
import json
import os
from pathlib import Path
import struct
import tempfile
import unittest
from unittest import mock
import zlib

import nowplaying

try:
    import homescreen
except Exception:  # pragma: no cover
    homescreen = None


def png(w, h):
    ihdr = struct.pack(">IIBBBBB", w, h, 8, 2, 0, 0, 0)
    return b"\x89PNG\r\n\x1a\n" + struct.pack(">I", 13) + b"IHDR" + ihdr + struct.pack(">I", zlib.crc32(b"IHDR" + ihdr))


def jpeg(w, h):
    app0 = b"\xff\xe0" + struct.pack(">H", 16) + b"JFIF\x00\x01\x01\x00\x00\x01\x00\x01\x00\x00"
    sof = b"\xff\xc0" + struct.pack(">HBHHB", 11, 8, h, w, 1) + b"\x01\x11\x00"
    return b"\xff\xd8" + app0 + sof + b"\xff\xd9"


def track(title, artist="", status="Playing", **extra):
    return dict({"status": status, "title": title, "artist": artist, "art": None, "source": "youtube"}, **extra)


class ClassifyTests(unittest.TestCase):
    MUSIC = [
        ("Bohemian Rhapsody", "Queen - Topic", "topic channel"),
        ("Blinding Lights", "The Weeknd - topic", "topic channel"),
        ("Adele - Hello (Official Audio)", "Adele", "title: official audio"),
        ("Numb (Audio)", "Linkin Park", "title: audio"),
        ("Song [Audio]", "Someone", "title: audio"),
        ("Coldplay - Yellow (Lyrics)", "7clouds", "title: lyrics"),
        ("Hotel California Lyric Video", "Eagles", "title: lyric video"),
        ("Tame Impala - Let It Happen (Official Visualizer)", "Tame Impala", "title: visualizer"),
        ("Visualiser for the new track", "Artist", "title: visualiser"),
        ("Pink Floyd - The Dark Side of the Moon (Full Album)", "Pink Floyd", "title: full album"),
        ("lofi hip hop - FULL ALBUM 2024", "Lofi Girl", "title: full album"),
    ]
    VIDEO = [
        ("Building a Raspberry Pi Cluster", "Jeff Geerling"),
        ("Adele - Hello (Official Music Video)", "AdeleVEVO"),
        ("The Audio Engineer's Guide to Mixing", "Mixing Tips"),   # "Audio" outside brackets isn't a signal
        ("Top 10 goals of the season", "Sky Sports"),
        ("Topic: why cats purr", "Science Channel"),
        ("YouTube", ""),
    ]

    def test_music_signals(self):
        for title, artist, reason in self.MUSIC:
            with self.subTest(title=title):
                self.assertEqual(nowplaying.classify({"title": title, "artist": artist}), ("music", reason))

    def test_everything_else_is_video(self):
        for title, artist in self.VIDEO:
            with self.subTest(title=title):
                self.assertEqual(nowplaying.classify({"title": title, "artist": artist}), ("video", "no music signal"))
        self.assertEqual(nowplaying.classify(None)[0], "video")

    def test_art_shape(self):
        meta = {"title": "Something", "artist": "Band"}
        self.assertEqual(nowplaying.classify(meta, art_size=(544, 544)), ("music", "square art"))
        self.assertEqual(nowplaying.classify(meta, art_size=(500, 520)), ("music", "square art"))
        self.assertEqual(nowplaying.classify(meta, art_size=(1280, 720))[0], "video")
        self.assertEqual(nowplaying.classify(meta, art_size=(480, 360))[0], "video")
        self.assertEqual(nowplaying.classify(meta, art_size=(0, 0))[0], "video")
        self.assertEqual(nowplaying.classify(meta, art_size="junk")[0], "video")

    def test_category(self):
        meta = {"title": "Something", "artist": "Band"}
        self.assertEqual(nowplaying.classify(meta, category="Music"), ("music", "category Music"))
        self.assertEqual(nowplaying.classify(meta, category="Gaming")[0], "video")

    def test_override_beats_heuristics(self):
        topic = {"title": "Song", "artist": "Queen - Topic"}
        self.assertEqual(nowplaying.classify(topic, overrides={"queen - topic": "video"}), ("video", "override"))
        vlog = {"title": "My day", "artist": "  Some  Vlogger "}
        self.assertEqual(nowplaying.classify(vlog, art_size=(1280, 720), overrides={"some vlogger": "music"}),
                         ("music", "override"))
        self.assertEqual(nowplaying.classify(vlog, overrides={"some vlogger": "bogus"})[0], "video")

    def test_image_size_headers(self):
        self.assertEqual(nowplaying.image_size(png(544, 544)), (544, 544))
        self.assertEqual(nowplaying.image_size(jpeg(1280, 720)), (1280, 720))
        self.assertEqual(nowplaying.image_size(b"GIF89a" + struct.pack("<HH", 300, 200)), (300, 200))
        self.assertIsNone(nowplaying.image_size(b"not an image"))
        self.assertIsNone(nowplaying.image_size(b""))

    def test_video_id_and_track_key(self):
        self.assertEqual(nowplaying.video_id({"url": "https://www.youtube.com/watch?v=dQw4w9WgXcQ&t=3"}), "dQw4w9WgXcQ")
        self.assertEqual(nowplaying.video_id({"art": "https://i.ytimg.com/vi/dQw4w9WgXcQ/hqdefault.jpg"}), "dQw4w9WgXcQ")
        self.assertIsNone(nowplaying.video_id({"art": "file:///tmp/.org.chromium.Chromium.abc"}))
        self.assertIsNone(nowplaying.track_key(None))
        self.assertIsNone(nowplaying.track_key({"status": "Open", "title": "YouTube"}))
        self.assertNotEqual(nowplaying.track_key(track("a")), nowplaying.track_key(track("b")))
        self.assertEqual(nowplaying.track_key(track("a")), nowplaying.track_key(track("a", status="Paused")))

    def test_parse_player_keeps_url(self):
        props = {"PlaybackStatus": "Playing", "Metadata": {"xesam:title": "x", "xesam:url": "https://youtube.com/watch?v=abcdefghijk"}}
        self.assertEqual(nowplaying.parse_player("org.mpris.MediaPlayer2.vacuumtube", "VacuumTube", props)["url"],
                         "https://youtube.com/watch?v=abcdefghijk")


class ClassifierCacheTests(unittest.TestCase):
    def test_slow_lookups_run_once_and_are_cached(self):
        reads, cats = [], []
        c = nowplaying.Classifier(read_head=lambda url: reads.append(url) or png(500, 500),
                                  category=lambda vid: cats.append(vid) or "Music", spawn=lambda fn: fn())
        state = track("Song", "Band", art="https://i.ytimg.com/vi/abcdefghijk/sq.jpg")
        self.assertEqual(c.mode(state), ("music", "square art", False))
        self.assertEqual(c.mode(state), ("music", "square art", False))
        self.assertEqual(reads, ["https://i.ytimg.com/vi/abcdefghijk/sq.jpg"])
        self.assertEqual(cats, ["abcdefghijk"])

    def test_pending_until_the_worker_finishes(self):
        queued = []
        c = nowplaying.Classifier(read_head=lambda url: jpeg(1280, 720), category=lambda vid: "Music",
                                  spawn=queued.append)
        state = track("Talk", "Chan", url="https://www.youtube.com/watch?v=abcdefghijk")
        self.assertEqual(c.mode(state), ("video", "no music signal", True))
        self.assertEqual(c.mode(state)[2], True)             # not started twice
        self.assertEqual(len(queued), 1)
        queued.pop()()
        self.assertEqual(c.mode(state), ("music", "category Music", False))

    def test_no_url_no_lookup_and_failures_are_cached(self):
        c = nowplaying.Classifier(read_head=mock.Mock(side_effect=OSError("gone")),
                                  category=mock.Mock(side_effect=AssertionError("must not fetch")),
                                  spawn=lambda fn: fn())
        state = track("Talk", "Chan", art="file:///tmp/missing.png")
        self.assertEqual(c.mode(state), ("video", "no music signal", False))
        c.mode(state)
        self.assertEqual(c.read_head.call_count, 1)
        c.category.assert_not_called()

    def test_quick_music_skips_lookups(self):
        c = nowplaying.Classifier(read_head=mock.Mock(), category=mock.Mock(), spawn=lambda fn: fn())
        self.assertEqual(c.mode(track("x (Lyrics)", art="https://a/b.jpg"))[0], "music")
        c.read_head.assert_not_called()


class OverrideStoreTests(unittest.TestCase):
    def test_cap_drops_the_oldest(self):
        modes = {}
        for i in range(205):
            modes = nowplaying.remember_mode(modes, f"Channel {i}", "music")
        self.assertEqual(len(modes), 200)
        self.assertNotIn("channel 0", modes)
        self.assertIn("channel 204", modes)
        modes = nowplaying.remember_mode(modes, "Channel 5", "video")     # re-chosen: now the newest
        for i in range(205, 400):
            modes = nowplaying.remember_mode(modes, f"Channel {i}", "music")
        self.assertEqual(modes.get("channel 5"), "video")
        self.assertEqual(len(modes), 200)

    def test_save_keeps_other_home_json_keys(self):
        with tempfile.TemporaryDirectory() as d:
            Path(d, "home.json").write_text(json.dumps({"style": "bubbles", "youtube_modes": {"old": "video", "bad": 3}}))
            nowplaying.save_mode(d, "Lofi Girl", "music")
            raw = json.loads(Path(d, "home.json").read_text())
            self.assertEqual(raw["style"], "bubbles")
            self.assertEqual(raw["youtube_modes"], {"old": "video", "lofi girl": "music"})
            self.assertEqual(nowplaying.load_modes(d), {"old": "video", "lofi girl": "music"})

    def test_load_tolerates_missing_and_broken_files(self):
        with tempfile.TemporaryDirectory() as d:
            self.assertEqual(nowplaying.load_modes(d), {})
            Path(d, "home.json").write_text("{nope")
            self.assertEqual(nowplaying.load_modes(d), {})
            nowplaying.save_mode(d, "A", "video")
            self.assertEqual(nowplaying.load_modes(d), {"a": "video"})

    def test_mode_file_round_trip_and_fallback(self):
        with tempfile.TemporaryDirectory() as d:
            state = track("Some talk", "Chan")
            self.assertEqual(nowplaying.read_mode(d, state), ("video", "no music signal"))
            nowplaying.write_mode_file(d, nowplaying.track_key(state), "music", "category Music")
            self.assertEqual(nowplaying.read_mode(d, state), ("music", "category Music"))
            self.assertEqual(nowplaying.read_mode(d, track("Other", "Chan"))[0], "video")    # stale entry ignored
            self.assertEqual(nowplaying.read_mode(d, None), (None, None))


class PopUpRuleTests(unittest.TestCase):
    BASE = dict(app="home", status="Playing", movie=False, screen_live=True, dismissed=None, popped=None)

    def pop(self, mode="video", key="k1", **kw):
        return nowplaying.pop_up(mode, key, **dict(self.BASE, **kw))

    def test_rules(self):
        self.assertTrue(self.pop())
        self.assertFalse(self.pop(mode="music"))
        self.assertFalse(self.pop(movie=True))                 # an mpv movie is playing
        self.assertFalse(self.pop(screen_live=False))          # screen off
        self.assertFalse(self.pop(dismissed="k1"))             # user went back to Home on this track
        self.assertFalse(self.pop(popped="k1"))                # already shown
        self.assertFalse(self.pop(app="youtube"))
        self.assertFalse(self.pop(status="Paused"))
        self.assertFalse(self.pop(pending=True))               # wait for the category/art answer
        self.assertFalse(self.pop(key=None))

    def test_new_track_pops_again(self):
        self.assertTrue(self.pop(key="k2", dismissed="k1", popped="k1"))


_temp = tempfile.TemporaryDirectory(prefix="imactv-ytmode-")


def _server():
    env = {"IMACTV_DATA_DIR": _temp.name, "IMACTV_FAKE_SCREEN": "on"}
    with mock.patch.dict(os.environ, env):
        spec = importlib.util.spec_from_file_location("imactv_server_ytmode", Path(__file__).resolve().parents[1] / "server.py")
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
    return mod


class ServerPopUpTests(unittest.TestCase):
    """The server's tick: classify on every track change, pop a video up, never during a movie / screen off /
    after the user went Home."""

    @classmethod
    def setUpClass(cls):
        cls.server = _server()

    def setUp(self):
        s = self.server
        self.patches = [mock.patch.object(s.subprocess, "Popen"), mock.patch.object(s.Controller, "_show_home"),
                        mock.patch.object(s.Controller, "_minimize_youtube"), mock.patch.object(s.threading, "Thread"),
                        mock.patch.object(s, "command", return_value=""),
                        mock.patch.object(s, "window_for", return_value={"id": "0x1", "pid": "5"}),
                        mock.patch.object(s, "activate")]
        for p in self.patches:
            p.start()
        self.activate = s.activate
        self.c = s.Controller()
        self.c.app = "home"
        self.c.yt = nowplaying.Classifier(read_head=lambda u: b"", category=lambda v: None, spawn=lambda fn: fn())
        self.c._movie_active = mock.Mock(return_value=False)
        self.c._screen_live = mock.Mock(return_value=True)
        self.now = track("Building a NAS", "Some Tech")
        self.cur = mock.patch.object(s.nowplaying, "current", side_effect=lambda: self.now)
        self.cur.start()

    def tearDown(self):
        self.cur.stop()
        for p in self.patches:
            p.stop()

    def tick(self):
        self.c._yt_cache = None
        self.c._youtube_tick()

    def test_video_pops_once(self):
        self.tick()
        self.assertEqual(self.c.app, "youtube")
        self.assertEqual(self.activate.call_count, 1)
        self.c.app = "home"
        self.tick()                                   # same track: not again
        self.assertEqual(self.activate.call_count, 1)

    def test_music_stays_home_then_autoplay_video_pops(self):
        self.now = track("Song", "Band - Topic")
        self.tick()
        self.assertEqual(self.c.app, "home")
        self.assertEqual(self.c._yt_decided[0], "music")
        self.now = track("Making of the album", "Band")
        self.tick()
        self.assertEqual(self.c.app, "youtube")

    def test_no_pop_during_movie_or_screen_off(self):
        self.c._movie_active.return_value = True
        self.tick()
        self.assertEqual(self.c.app, "home")
        self.c._movie_active.return_value = False
        self.c._screen_live.return_value = False
        self.tick()
        self.assertEqual(self.c.app, "home")
        self.c._screen_live.return_value = True
        self.tick()
        self.assertEqual(self.c.app, "youtube")

    def test_home_dismisses_the_track(self):
        self.tick()
        self.assertEqual(self.c.app, "youtube")
        self.c.select("home")
        self.c._yt_popped = None                      # even if it had never been popped by us
        self.tick()
        self.assertEqual(self.c.app, "home")
        self.now = track("Next video", "Some Tech")
        self.tick()
        self.assertEqual(self.c.app, "youtube")

    def test_mode_in_state_json_and_phone_override(self):
        self.now = track("Talk", "Podcast Chan")
        self.c._movie_active.return_value = True      # keep it from popping while we look
        self.c._yt_cache = None
        yt = self.c.youtube_now()
        self.assertEqual((yt["youtube_mode"], yt["youtube_reason"]), ("video", "no music signal"))
        out = self.c.action("youtube_mode", {"mode": "music"})
        self.assertEqual(out["youtube_now"]["youtube_mode"], "music")
        self.assertEqual(nowplaying.load_modes(self.server.DATA).get("podcast chan"), "music")
        saved = json.loads((self.server.DATA / nowplaying.MODE_FILE).read_text())
        self.assertEqual(saved["mode"], "music")
        with self.assertRaises(ValueError):
            self.c.action("youtube_mode", {"mode": "loud"})

    def test_phone_show_video_pops_even_after_dismiss(self):
        self.now = track("Song", "Band - Topic")
        self.tick()
        self.c._yt_dismissed = nowplaying.track_key(self.now)
        self.c.action("youtube_mode", {"mode": "video"})
        self.assertEqual(self.c.app, "youtube")
        self.c.action("youtube_mode", {"mode": "music"})
        self.assertEqual(self.c.app, "home")


@unittest.skipIf(homescreen is None, "GTK bindings unavailable")
class AmbientDelayTests(unittest.TestCase):
    def test_delay_choice(self):
        d = homescreen.idle_delay
        self.assertEqual(d(120, 30, {"status": "Playing", "youtube_mode": "music"}), 15)
        self.assertEqual(d(120, 30, {"status": "Playing", "youtube_mode": "video"}), 30)
        self.assertEqual(d(120, 30, {"status": "Playing"}), 30)
        self.assertEqual(d(120, 30, {"status": "Paused", "youtube_mode": "music"}), 120)
        self.assertEqual(d(120, 30, None), 120)
        self.assertEqual(d(10, 30, {"status": "Playing", "youtube_mode": "music"}), 10)


if __name__ == "__main__":
    unittest.main()
