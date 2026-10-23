import importlib.util
import os
from pathlib import Path
import tempfile
import unittest
from unittest import mock

_temp = tempfile.TemporaryDirectory(prefix="imactv-test-")
os.environ["IMACTV_DATA_DIR"] = _temp.name
spec = importlib.util.spec_from_file_location("imactv_server", Path(__file__).resolve().parents[1] / "server.py")
server = importlib.util.module_from_spec(spec)
spec.loader.exec_module(server)


class ControllerTests(unittest.TestCase):
    def test_window_uses_class_and_title_columns(self):
        row = "0x04600004  0 1376  0 0 2560 1440 com.stremio.Stremio obada-iMac Stremio"
        with mock.patch.object(server, "command", return_value=row):
            self.assertEqual(server.window_for("stremio")["id"], "0x04600004")

    def test_pairing_revokes_code_and_limits_guesses(self):
        with mock.patch.object(server.threading, "Thread"):
            c = server.Controller()
        token = c.pair(c.pair_code, "192.168.68.12")
        self.assertTrue(c.authenticated({"imactv": mock.Mock(value=token)}, ""))
        with self.assertRaises(ValueError):
            c.pair("111111", "192.168.68.12")
        for _ in range(4):
            try:
                c.pair("111111", "192.168.68.12")
            except ValueError:
                pass
        with self.assertRaisesRegex(ValueError, "Too many attempts"):
            c.pair("111111", "192.168.68.12")

    def test_old_timer_cannot_turn_off_new_boot(self):
        server.Controller._save_json(server.DATA / "timer.json",
                                     {"boot_id": "older-boot", "deadline": 1})
        with mock.patch.object(server.threading, "Thread"):
            c = server.Controller()
        self.assertIsNone(c.timer["deadline"])

    def test_unicode_text_is_sent_as_one_string(self):
        with mock.patch.object(server.threading, "Thread"):
            c = server.Controller()
        c.app, c.phase = "stremio", "ready"
        with mock.patch.object(c, "_focus_target"), mock.patch.object(server, "playback", return_value={"active": False}), mock.patch.object(server, "command") as run:
            c.action("type", {"text": "a.b+é@example.com"})
        self.assertEqual(run.call_args.args[0][-1], "a.b+é@example.com")

    def test_unsupported_seek_reports_error(self):
        with mock.patch.object(server.threading, "Thread"):
            c = server.Controller()
        with mock.patch.object(server, "playback", return_value={"active": False}):
            with self.assertRaisesRegex(RuntimeError, "MPV"):
                c.action("seek", {"seconds": 20})

    def test_revoke_other_phones_keeps_current_phone_and_home(self):
        with mock.patch.object(server.threading, "Thread"):
            c = server.Controller()
        phone = c.new_device()
        other = c.new_device()
        c.revoke(phone, "other_phones")
        self.assertTrue(c.authenticated({"imactv": mock.Mock(value=phone)}, ""))
        self.assertFalse(c.authenticated({"imactv": mock.Mock(value=other)}, ""))
        home = (server.DATA / "home_token").read_text().strip()
        self.assertTrue(c.authenticated({"imactv": mock.Mock(value=home)}, ""))

    def test_network_info_parses_wifi_and_ip(self):
        import home
        with mock.patch("subprocess.check_output") as sub:
            def fake_output(args, **kwargs):
                cmd_str = " ".join(args)
                if "ip route get" in cmd_str:
                    return "1.1.1.1 via 192.168.68.1 dev wlp9s0 src 192.168.68.124 uid 1000\n"
                if "device wifi list" in cmd_str:
                    return "no:OtherNet:40\nyes:MyWifi:85\n"
                return ""
            sub.side_effect = fake_output
            info = home.get_network_info()
            self.assertTrue(info["connected"])
            self.assertEqual(info["type"], "wifi")
            self.assertEqual(info["ssid"], "MyWifi")
            self.assertEqual(info["signal"], 85)
            self.assertEqual(info["ip"], "192.168.68.124")

    def test_qr_pixbuf_generates_correct_dimensions(self):
        import home
        pb = home.make_qr_pixbuf("http://192.168.68.124:8765/?pair=123456", 80)
        self.assertIsNotNone(pb)
        self.assertEqual(pb.get_width(), 80)
        self.assertEqual(pb.get_height(), 80)


class PlayMediaTests(unittest.TestCase):
    def controller(self):
        with mock.patch.object(server.threading, "Thread"):
            return server.Controller()

    def test_validation_rejects_bad_params(self):
        c = self.controller()
        bad = [{}, {"type": "music", "id": "tt1"}, {"type": "movie", "id": "x1"}, {"type": "movie", "id": "tt1; rm"},
               {"type": "movie", "id": "tt1", "season": 1}, {"type": "series", "id": "tt1"},
               {"type": "series", "id": "tt1", "season": 0, "episode": 1},
               {"type": "series", "id": "tt1", "season": True, "episode": 1},
               {"type": "movie", "id": "tt1", "stream": {"info_hash": "zz", "file_idx": 0}},
               {"type": "movie", "id": "tt1", "stream": {"info_hash": "a" * 40, "file_idx": -1}},
               {"type": "movie", "id": "tt1", "start": -5}]
        with mock.patch.object(server.threading, "Thread") as thread, mock.patch.object(server, "playback", return_value={"active": False}):
            for params in bad:
                with self.assertRaises(ValueError, msg=params):
                    c.action("play_media", params)
            thread.assert_not_called()
        self.assertEqual(c.phase, "ready")

    def test_play_media_sets_buffering_state_and_starts_worker(self):
        c = self.controller()
        with mock.patch.object(server.threading, "Thread") as thread, mock.patch.object(server, "playback", return_value={"active": False}):
            out = c.action("play_media", {"type": "series", "id": "tt0903747", "season": 1, "episode": 2})
        self.assertTrue(out["ok"])
        thread.return_value.start.assert_called_once()
        self.assertEqual((c.app, c.phase), ("player", "buffering"))
        self.assertEqual(c.media["video_id"], "tt0903747:1:2")
        self.assertEqual(server.Controller._read_json(server.DATA / "last_media.json", {})["video_id"], "tt0903747:1:2")

    def worker(self, c, stream=None, mpv_results=(None, 5.0), healthy=True, found=None):
        c.action_lock = server.threading.RLock()
        with mock.patch.object(server.threading, "Thread"), mock.patch.object(server, "playback", return_value={"active": False}):
            c.action("play_media", {"type": "movie", "id": "tt0063350"})
        gen = c.generation
        src = found if found is not None else [{"name": "Torrentio\n1080p", "title": "Movie 1080p WEB-DL x264\n\U0001f464 50 \U0001f4be 2 GB", "infoHash": "d" * 40, "fileIdx": 1}]
        proc = mock.Mock()
        proc.poll.return_value = None
        with mock.patch.object(server.media_engine, "Engine") as eng, \
                mock.patch.object(server.addons, "meta", return_value={"name": "Night"}), \
                mock.patch.object(server.addons, "streams", return_value=src), \
                mock.patch.object(server.addons, "subtitles", return_value=[{"url": "https://s/a.srt", "lang": "ara"}]), \
                mock.patch.object(server.subprocess, "Popen", return_value=proc) as popen, \
                mock.patch.object(server, "mpv", side_effect=list(mpv_results)), \
                mock.patch.object(server.time, "sleep"), mock.patch.object(c, "_show_home"):
            eng.return_value.healthy.return_value = healthy
            eng.return_value.url.side_effect = lambda h, i: f"http://e/{h}/{i}"
            eng.return_value.stats.return_value = {"peers": 4, "speed": 1000.0, "downloaded": 1}
            c._play_worker(gen, stream, 12.0)
        return popen, proc

    def test_worker_launches_best_source_and_reaches_playing(self):
        c = self.controller()
        popen, _ = self.worker(c)
        argv = popen.call_args.args[0]
        self.assertEqual(argv[0], "mpv")
        self.assertEqual(argv[-1], "http://e/" + "d" * 40 + "/1")
        self.assertIn("--sub-file=https://s/a.srt", argv)
        self.assertIn("--start=12", argv)
        self.assertEqual((c.app, c.phase, c.buffering), ("player", "playing", None))
        self.assertEqual(c.media["name"], "Night")

    def test_worker_uses_explicit_stream_and_skips_sources(self):
        c = self.controller()
        popen, _ = self.worker(c, stream={"info_hash": "e" * 40, "file_idx": 4}, found=[])
        self.assertEqual(popen.call_args.args[0][-1], "http://e/" + "e" * 40 + "/4")

    def test_worker_failure_returns_home_with_error(self):
        c = self.controller()
        popen, _ = self.worker(c, found=[])
        popen.assert_not_called()
        self.assertEqual((c.app, c.phase, c.error), ("home", "ready", "No playable sources found"))
        c = self.controller()
        self.worker(c, healthy=False)
        self.assertIn("torrent engine", c.error)

    def test_sources_action_returns_ranked(self):
        c = self.controller()
        raw = [{"name": "Torrentio\n720p", "title": "A 720p\n\U0001f464 9 \U0001f4be 1 GB", "infoHash": "1" * 40},
               {"name": "Torrentio\n1080p", "title": "B 1080p\n\U0001f464 9 \U0001f4be 2 GB", "infoHash": "2" * 40}]
        with mock.patch.object(server.addons, "streams", return_value=raw):
            out = c.action("sources", {"type": "movie", "id": "tt1"})
        self.assertEqual([s["quality"] for s in out["sources"]], ["1080p", "720p"])
        with self.assertRaises(ValueError):
            c.action("sources", {"type": "movie", "id": "bad"})

    def test_audio_action_sets_aid_only_while_playing(self):
        c = self.controller()
        with mock.patch.object(server, "playback", return_value={"active": False}):
            with self.assertRaises(RuntimeError):
                c.action("audio", {"id": 2})
        with mock.patch.object(server, "playback", return_value={"active": True}), mock.patch.object(server, "mpv") as m:
            c.action("audio", {"id": 2})
            m.assert_called_with("set_property", "aid", 2)
            with self.assertRaises(ValueError):
                c.action("audio", {"id": "x"})

    def test_playback_reports_lang_and_audio(self):
        tracks = [{"type": "sub", "id": 1, "lang": "ara", "selected": True}, {"type": "audio", "id": 1, "lang": "eng", "title": "Stereo"}]
        answers = {"time-pos": 3, "duration": 9, "pause": False, "media-title": "T", "track-list": tracks}
        with mock.patch.object(server, "mpv", side_effect=lambda cmd, prop: answers[prop]):
            out = server.playback()
        self.assertEqual(out["subtitles"][0]["lang"], "ara")
        self.assertEqual(out["audio"], [{"id": 1, "title": "Stereo", "lang": "eng", "selected": False}])


if __name__ == "__main__":
    unittest.main()
