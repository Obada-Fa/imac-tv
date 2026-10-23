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
