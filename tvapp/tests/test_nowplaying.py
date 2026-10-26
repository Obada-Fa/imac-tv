from pathlib import Path
import sys
import unittest
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import nowplaying  # noqa: E402


def props(status="Playing", **meta):
    return {"PlaybackStatus": status, "Metadata": meta}


class MatchTests(unittest.TestCase):
    def test_vacuumtube_by_bus_name_or_identity(self):
        self.assertTrue(nowplaying.looks_like_youtube("org.mpris.MediaPlayer2.rocks.shy.VacuumTube", "x", {}))
        self.assertTrue(nowplaying.looks_like_youtube("org.mpris.MediaPlayer2.foo", "VacuumTube", {}))

    def test_browser_without_url_counts_but_other_sites_do_not(self):
        self.assertTrue(nowplaying.looks_like_youtube("org.mpris.MediaPlayer2.chromium.instance1", "Chromium", {}))
        self.assertFalse(nowplaying.looks_like_youtube("org.mpris.MediaPlayer2.chromium.instance1", "Chromium",
                                                       {"xesam:url": "https://soundcloud.com/x"}))
        self.assertTrue(nowplaying.looks_like_youtube("org.mpris.MediaPlayer2.chromium.instance1", "Chromium",
                                                      {"xesam:url": "https://www.youtube.com/watch?v=1"}))

    def test_unrelated_players_are_ignored(self):
        self.assertFalse(nowplaying.looks_like_youtube("org.mpris.MediaPlayer2.spotify", "Spotify", {}))
        self.assertFalse(nowplaying.looks_like_youtube("org.mpris.MediaPlayer2.mpv", "mpv", {"xesam:url": "file:///a.mkv"}))


class ParseTests(unittest.TestCase):
    def test_playing_player_becomes_state(self):
        st = nowplaying.parse_player("org.mpris.MediaPlayer2.vacuumtube", "VacuumTube",
                                     props(**{"xesam:title": "Lo-fi beats", "xesam:artist": ["Lofi Girl", "Friend"],
                                              "mpris:artUrl": "https://i.ytimg.com/x.jpg"}))
        self.assertEqual(st, {"status": "Playing", "title": "Lo-fi beats", "artist": "Lofi Girl, Friend",
                              "art": "https://i.ytimg.com/x.jpg", "source": "youtube"})

    def test_missing_fields_get_defaults(self):
        st = nowplaying.parse_player("org.mpris.MediaPlayer2.vacuumtube", "VacuumTube", props("Paused"))
        self.assertEqual((st["status"], st["title"], st["artist"], st["art"]), ("Paused", "YouTube", "", None))

    def test_stopped_or_foreign_players_give_none(self):
        self.assertIsNone(nowplaying.parse_player("org.mpris.MediaPlayer2.vacuumtube", "V", props("Stopped")))
        self.assertIsNone(nowplaying.parse_player("org.mpris.MediaPlayer2.spotify", "Spotify", props()))

    def test_pick_best_prefers_playing(self):
        a = {"status": "Paused", "title": "a"}
        b = {"status": "Playing", "title": "b"}
        self.assertEqual(nowplaying.pick_best([None, a, b])["title"], "b")
        self.assertEqual(nowplaying.pick_best([a])["title"], "a")
        self.assertIsNone(nowplaying.pick_best([None]))


class FallbackTests(unittest.TestCase):
    def test_window_fallback(self):
        lines = ["0x01 0 vacuumtube.VacuumTube host YouTube", "0x02 0 imactv.Home host iMac TV"]
        self.assertEqual(nowplaying.window_fallback(lines)["status"], "Open")
        self.assertIsNone(nowplaying.window_fallback(["0x02 0 imactv.Home host iMac TV"]))
        self.assertIsNone(nowplaying.window_fallback([]))


if __name__ == "__main__":
    unittest.main()


class ControlTests(unittest.TestCase):
    def named(self, *players):
        return mock.patch.object(nowplaying, "_mpris_named", return_value=list(players))

    def test_unknown_command(self):
        with self.assertRaises(ValueError):
            nowplaying.control("explode")

    def test_notification_transport_calls_explicit_mpris_methods(self):
        bus, gio = mock.Mock(), mock.Mock()
        with self.named(("org.mpris.MediaPlayer2.vacuumtube", {"status": "Paused"})), \
                mock.patch.object(nowplaying, "_bus", return_value=(gio, mock.Mock(), bus)):
            for cmd in ("play", "pause", "stop"):
                nowplaying.control(cmd)
        self.assertEqual([call.args[3] for call in bus.call_sync.call_args_list], ["Play", "Pause", "Stop"])

    def test_calls_method_on_playing_player(self):
        bus, gio = mock.Mock(), mock.Mock()
        paused = ("org.mpris.MediaPlayer2.a", {"status": "Paused"})
        playing = ("org.mpris.MediaPlayer2.b", {"status": "Playing"})
        with self.named(paused, ("x", None), playing), mock.patch.object(nowplaying, "_bus", return_value=(gio, mock.Mock(), bus)):
            nowplaying.control("next")
        args = bus.call_sync.call_args.args
        self.assertEqual(args[0], "org.mpris.MediaPlayer2.b")
        self.assertEqual((args[2], args[3]), (nowplaying.PLAYER_IFACE, "Next"))

    def test_no_player_is_clear_error(self):
        with self.named(("x", None)):
            with self.assertRaisesRegex(RuntimeError, "media controls"):
                nowplaying.control("play_pause")

    def test_can_flags_only_when_published(self):
        st = nowplaying.parse_player("org.mpris.MediaPlayer2.vacuumtube", "V", dict(props(), CanGoNext=False, CanPause=True))
        self.assertEqual((st["can_next"], st["can_pause"], "can_prev" in st), (False, True, False))


class SeekTests(unittest.TestCase):
    def test_parse_position_length_track(self):
        st = nowplaying.parse_player("org.mpris.MediaPlayer2.vacuumtube", "V", dict(
            props(**{"mpris:length": 200_000_000, "mpris:trackid": "/org/mpris/track/1"}), Position=65_500_000, CanSeek=True))
        self.assertEqual((st["position"], st["length"], st["track_id"], st["can_seek"]), (65.5, 200.0, "/org/mpris/track/1", True))

    def test_position_clamped_to_length_and_missing_omitted(self):
        st = nowplaying.parse_player("org.mpris.MediaPlayer2.vacuumtube", "V", dict(props(**{"mpris:length": 10_000_000}), Position=99_000_000))
        self.assertEqual(st["position"], 10.0)
        bare = nowplaying.parse_player("org.mpris.MediaPlayer2.vacuumtube", "V", props())
        self.assertFalse({"position", "length", "track_id"} & set(bare))

    def test_microsecond_conversion_and_clamping(self):
        self.assertEqual(nowplaying.seek_micros(-10), -10_000_000)
        self.assertEqual(nowplaying.seek_micros(10 ** 6), 3600 * 10 ** 6)
        self.assertEqual(nowplaying.position_micros(30, 100), 30_000_000)
        self.assertEqual(nowplaying.position_micros(500, 100), 100_000_000)
        self.assertEqual(nowplaying.position_micros(-3, 100), 0)

    def test_seek_and_set_position_calls(self):
        bus, gio = mock.Mock(), mock.Mock()
        player = ("org.mpris.MediaPlayer2.b", {"status": "Playing", "track_id": "/t/1", "length": 100.0})
        with mock.patch.object(nowplaying, "_mpris_named", return_value=[player]), \
                mock.patch.object(nowplaying, "_bus", return_value=(gio, mock.Mock(), bus)):
            nowplaying.control("seek", 10)
            self.assertEqual(bus.call_sync.call_args.args[3], "Seek")
            nowplaying.control("set_position", 250)
            self.assertEqual(bus.call_sync.call_args.args[3], "SetPosition")
        with mock.patch.object(nowplaying, "_mpris_named", return_value=[("n", {"status": "Playing"})]), \
                mock.patch.object(nowplaying, "_bus", return_value=(gio, mock.Mock(), bus)):
            with self.assertRaisesRegex(RuntimeError, "position"):
                nowplaying.control("set_position", 5)
        with self.assertRaises(ValueError):
            nowplaying.control("seek", None)
