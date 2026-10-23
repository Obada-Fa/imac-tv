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
        row = "0x04600004  0 1376  0 0 2560 1440 mpv.mpv obada-iMac Movie"
        with mock.patch.object(server, "command", return_value=row):
            self.assertEqual(server.window_for("player")["id"], "0x04600004")
            self.assertIsNone(server.window_for("stremio"))
        self.assertEqual(set(server.APPS), {"youtube"})

    def test_old_stremio_selection_maps_to_home(self):
        for old in ("stremio", "vlc"):
            server.Controller._save_json(server.DATA / "selection.json", {"boot_id": server.BOOT_ID, "app": old})
            with mock.patch.object(server.threading, "Thread"):
                self.assertEqual(server.Controller().app, "home")
        with self.assertRaises(ValueError):
            with mock.patch.object(server.threading, "Thread"):
                server.Controller().select("stremio")

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
        c.app, c.phase = "youtube", "ready"
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

    def worker(self, c, stream=None, mpv_results=None, healthy=True, found=None, tracks=(), loop=False, local=True):
        c.action_lock = server.threading.RLock()
        with mock.patch.object(server.threading, "Thread"), mock.patch.object(server, "playback", return_value={"active": False}):
            c.action("play_media", {"type": "movie", "id": "tt0063350"})
        gen = c.generation
        src = found if found is not None else [{"name": "Torrentio\n1080p", "title": "Movie 1080p WEB-DL x264\n\U0001f464 50 \U0001f4be 2 GB", "infoHash": "d" * 40, "fileIdx": 1,
                                                 "sources": ["tracker:udp://t:1/announce", "dht:" + "d" * 40]}]
        proc = mock.Mock()
        proc.poll.return_value = None
        calls = []

        def fake_mpv(*args):
            calls.append(args)
            if args[:2] == ("get_property", "time-pos"):
                return 5.0
            if args[:2] == ("get_property", "track-list"):
                return list(tracks)
            return None
        self.calls, self.eng_holder = calls, {}
        with mock.patch.object(server.media_engine, "Engine") as eng, \
                mock.patch.object(server.addons, "meta", return_value={"name": "Night"}), \
                mock.patch.object(server.addons, "streams", return_value=src), \
                mock.patch.object(server.addons, "subtitles", return_value=[{"url": "https://s/a.srt", "lang": "ara"}]), \
                mock.patch.object(server.subprocess, "Popen", return_value=proc) as popen, \
                mock.patch.object(server, "mpv", side_effect=fake_mpv) as mpv_mock, \
                mock.patch.object(server.time, "sleep"), mock.patch.object(c, "_show_home"), \
                mock.patch.object(server, "window_for", return_value={"id": "0x5"}), \
                mock.patch.object(server, "activate") as activate, \
                mock.patch.object(c, "_playback_loop") as watcher:
            eng.return_value.healthy.return_value = healthy
            eng.return_value.is_local = local
            eng.return_value.url.side_effect = lambda h, i: f"http://e/{h}/{i}"
            eng.return_value.stats.return_value = {"peers": 4, "speed": 1000.0, "downloaded": 1}
            self.eng_holder["eng"] = eng.return_value
            c._play_worker(gen, stream, 12.0)
        self.watcher, self.activate = watcher, activate
        return popen, proc

    def test_worker_launches_best_source_and_reaches_playing(self):
        c = self.controller()
        popen, _ = self.worker(c)
        argv = popen.call_args.args[0]
        self.assertEqual(argv[0], "mpv")
        self.assertEqual(argv[-1], "http://e/" + "d" * 40 + "/1")
        self.assertFalse(any(a.startswith("--sub-file") for a in argv))
        self.assertIn("--slang=en,eng,ar,ara,nl,dut,nld", argv)
        self.assertIn("--start=12", argv)
        self.assertIn(("sub-add", "https://s/a.srt", "select", "Arabic", "ara"), self.calls)
        self.eng_holder["eng"].configure_cache.assert_called_once_with(3221225472)
        self.watcher.assert_called_once()
        self.activate.assert_called_once_with({"id": "0x5"})
        self.eng_holder["eng"].create.assert_called_once_with("d" * 40, ["tracker:udp://t:1/announce", "dht:" + "d" * 40])
        self.assertIn("--ontop", argv)
        self.assertTrue(any(a.startswith("--log-file=") and a.endswith("mpv.log") for a in argv))
        self.assertEqual(c.session["entry"]["name"], "Night")
        self.assertEqual((c.app, c.phase, c.buffering), ("player", "playing", None))
        self.assertEqual(c.media["name"], "Night")

    def test_remote_engine_leaves_cache_settings_alone(self):
        c = self.controller()
        popen, _ = self.worker(c, local=False)
        popen.assert_called_once()
        self.eng_holder["eng"].configure_cache.assert_not_called()

    def test_subs_select_default_language_and_prefer_embedded(self):
        c = self.controller()
        self.worker(c, found=None, tracks=[])
        picked = [{"url": "https://s/%s.srt" % l, "lang": l, "label": l.title()} for l in ("ara", "dut", "eng")]
        with mock.patch.object(server, "mpv", return_value=[]) as m:
            c._attach_subs(picked, {"sub_langs": ["ara", "dut", "eng"], "sub_default": "dut"})
        flags = {call.args[4]: call.args[2] for call in m.call_args_list if call.args[0] == "sub-add"}
        self.assertEqual(flags, {"ara": "auto", "dut": "select", "eng": "auto"})
        embedded = [{"type": "sub", "id": 7, "lang": "eng"}]
        with mock.patch.object(server, "mpv", return_value=embedded) as m:
            c._attach_subs(picked, {"sub_langs": ["ara", "dut", "eng"], "sub_default": "eng"})
        self.assertNotIn("select", [call.args[2] for call in m.call_args_list if call.args[0] == "sub-add"])
        m.assert_called_with("set_property", "sid", 7)

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
        self.assertIn("Can't reach the Stremio server at http://127.0.0.1:11470", c.error)

    def test_sources_action_returns_ranked(self):
        c = self.controller()
        raw = [{"name": "Torrentio\n720p", "title": "A 720p\n\U0001f464 9 \U0001f4be 1 GB", "infoHash": "1" * 40},
               {"name": "Torrentio\n1080p", "title": "B 1080p\n\U0001f464 9 \U0001f4be 2 GB", "infoHash": "2" * 40}]
        with mock.patch.object(server.addons, "streams", return_value=raw):
            out = c.action("sources", {"type": "movie", "id": "tt1"})
        self.assertEqual([s["quality"] for s in out["sources"]], ["1080p", "720p"])
        with self.assertRaises(ValueError):
            c.action("sources", {"type": "movie", "id": "bad"})

    def test_media_stream_url_validation(self):
        self.assertEqual(server.media_stream({"url": "https://tb/x"}), {"url": "https://tb/x", "info_hash": None, "file_idx": None})
        for bad in ("ftp://x/y", "javascript:alert(1)", "https://" + "a" * 2048, "", 5):
            with self.assertRaises(ValueError):
                server.media_stream({"url": bad})

    def test_worker_plays_url_stream_without_engine(self):
        c = self.controller()
        popen, _ = self.worker(c, stream=server.media_stream({"url": "https://tb/x.mkv"}), found=[], healthy=False)
        self.assertEqual(popen.call_args.args[0][-1], "https://tb/x.mkv")
        eng = self.eng_holder["eng"]
        eng.healthy.assert_not_called()
        eng.create.assert_not_called()
        eng.stats.assert_not_called()
        self.assertIsNone(c.session["info_hash"])
        with mock.patch.object(server, "mpv"), mock.patch.object(c, "_stop_proc"):
            self.assertTrue(c._end_playback())
        eng.remove.assert_not_called()

    def test_worker_auto_picks_cached_torbox_stream(self):
        c = self.controller()
        found = [{"name": "[TB+] Torrentio\n1080p", "title": "M 1080p x264\n\U0001f4be 2 GB", "url": "https://tb/auto"}]
        popen, _ = self.worker(c, found=found, healthy=False)
        self.assertEqual(popen.call_args.args[0][-1], "https://tb/auto")
        self.eng_holder["eng"].create.assert_not_called()

    def test_browse_find_title_continue_actions(self):
        c = self.controller()
        metas = [{"id": "tt1", "type": "movie", "name": "A", "releaseInfo": "1999-2001", "poster": "p", "extra": 1}] * 60
        with mock.patch.object(server.addons, "catalog", return_value=metas) as cat:
            out = c.action("browse", {"type": "movie", "skip": 20})
        cat.assert_called_once_with("movie", "top", skip=20)
        self.assertEqual(len(out["items"]), 50)
        self.assertEqual(out["items"][0], {"id": "tt1", "type": "movie", "name": "A", "year": "1999", "poster": "p"})
        for bad in ({"type": "tv"}, {"type": "movie", "skip": -1}):
            with self.assertRaises(ValueError):
                c.action("browse", bad)
        with mock.patch.object(server.addons, "catalog", side_effect=server.addons.AddonError("down")):
            with self.assertRaises(RuntimeError):
                c.action("browse", {"type": "movie"})

        def search(kind, q):
            if kind == "series":
                raise server.addons.AddonError("x")
            return [{"id": "tt2", "type": "movie", "name": q, "year": 2020}]
        with mock.patch.object(server.addons, "search", side_effect=search):
            out = c.action("find", {"query": "  dune "})
            self.assertEqual((out["movies"][0]["name"], out["movies"][0]["year"], out["series"]), ("dune", "2020", []))
        with mock.patch.object(server.addons, "search", side_effect=server.addons.AddonError("x")):
            with self.assertRaises(RuntimeError):
                c.action("find", {"query": "dune"})
        for bad in ("   ", "x" * 81, None):
            with self.assertRaises(ValueError):
                c.action("find", {"query": bad})

        meta = {"id": "tt3", "name": "Show", "releaseInfo": "2010", "runtime": "45 min", "genres": ["Drama"],
                "videos": [{"season": 0, "episode": 1, "name": "Sp"}, {"season": 2, "episode": 1, "name": "B"},
                           {"season": 1, "episode": 2, "name": "A2"}, {"season": 1, "episode": 1, "name": "A1", "released": "r"}]}
        with mock.patch.object(server.addons, "meta", return_value=meta), \
                mock.patch.object(server.history, "resume_position", return_value=42.0):
            out = c.action("title", {"type": "series", "id": "tt3"})
            self.assertEqual([s["season"] for s in out["seasons"]], [1, 2, 0])
            self.assertEqual([e["episode"] for e in out["seasons"][0]["episodes"]], [1, 2])
            self.assertEqual((out["resume"], out["runtime"], out["genres"], out["type"]), (0, "45 min", ["Drama"], "series"))
            out = c.action("title", {"type": "movie", "id": "tt3"})
            self.assertEqual(out["resume"], 42.0)
            self.assertNotIn("seasons", out)
        with self.assertRaises(ValueError):
            c.action("title", {"type": "movie", "id": "bad"})

        entry = {"id": "tt3", "type": "series", "name": "Show", "poster": "p", "season": 1, "episode": 2,
                 "position": 10.0, "duration": 100.0, "video_id": "tt3:1:2"}
        with mock.patch.object(server.history, "continue_watching", return_value=[entry]):
            row = c.action("continue", {})["items"][0]
        self.assertEqual((row["id"], row["season"], row["episode"], row["position"], row["duration"]), ("tt3", 1, 2, 10.0, 100.0))
        self.assertNotIn("video_id", row)

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

    def test_volume_uses_mpv_while_playing(self):
        c = self.controller()
        with mock.patch.object(server, "playback", return_value={"active": True}), \
                mock.patch.object(server, "mpv", return_value=85) as m, mock.patch.object(server, "command") as run:
            self.assertEqual(c.action("volume", {"direction": 1}), {"volume": 85})
            m.assert_any_call("osd-msg-bar", "add", "volume", 5)
            c.action("mute", {})
            m.assert_any_call("osd-msg", "cycle", "mute")
            run.assert_not_called()

    def test_playback_reports_lang_and_audio(self):
        tracks = [{"type": "sub", "id": 1, "lang": "ara", "selected": True}, {"type": "audio", "id": 1, "lang": "eng", "title": "Stereo"}]
        answers = {"time-pos": 3, "duration": 9, "pause": False, "media-title": "T", "track-list": tracks}
        with mock.patch.object(server, "mpv", side_effect=lambda cmd, prop: answers[prop]):
            out = server.playback()
        self.assertEqual(out["subtitles"][0]["lang"], "ara")
        self.assertEqual(out["audio"], [{"id": 1, "title": "Stereo", "lang": "eng", "selected": False}])


class LifecycleTests(unittest.TestCase):
    def controller(self, phase="playing"):
        with mock.patch.object(server.threading, "Thread"):
            c = server.Controller()
        eng = mock.Mock()
        c.app, c.phase, c.generation = "player", phase, 5
        c.media = {"type": "series", "id": "tt9", "video_id": "tt9:1:2", "season": 1, "episode": 2, "name": "Show S01E02"}
        c.session = {"engine": eng, "info_hash": "a" * 40, "pos": 100.0, "dur": 1000.0,
                     "entry": {"type": "series", "id": "tt9", "video_id": "tt9:1:2", "season": 1, "episode": 2,
                               "name": "Show", "poster": "http://p"}}
        c.proc = mock.Mock(**{"poll.return_value": None})
        self.eng = eng
        return c

    def ui(self):
        return server.Controller._read_json(server.DATA / "ui_request.json", {})

    def test_back_during_buffering_cancels_and_requests_details(self):
        c = self.controller("buffering")
        proc = c.proc
        with mock.patch.object(server, "playback", return_value={"active": False}), \
                mock.patch.object(server, "mpv", side_effect=OSError), mock.patch.object(c, "_show_home"), \
                mock.patch.object(server.threading, "Thread"):
            self.assertEqual(c.action("back", {}), {"ok": True})
        proc.terminate.assert_called_once()
        self.eng.remove.assert_called_once_with("a" * 40)
        self.assertEqual((c.app, c.phase, c.session), ("home", "ready", None))
        self.assertGreater(c.generation, 5)
        ui = self.ui()
        self.assertEqual((ui["screen"], ui["media"]), ("details", {"type": "series", "id": "tt9", "season": 1, "episode": 2}))

    def test_back_while_playing_records_final_position(self):
        c = self.controller()
        answers = {"time-pos": 500.0, "duration": 1000.0}
        quit_calls = []

        def fake(*args):
            if args[0] == "quit":
                quit_calls.append(1)
            return answers.get(args[-1])
        with mock.patch.object(server, "playback", return_value={"active": True}), \
                mock.patch.object(server, "mpv", side_effect=fake), mock.patch.object(server.threading, "Thread"):
            c.action("back", {})
        self.assertEqual(quit_calls, [1])
        self.assertEqual(server.history.load()["tt9:1:2"]["position"], 500.0)
        self.assertEqual(server.history.load()["tt9:1:2"]["poster"], "http://p")
        self.eng.remove.assert_called_once()

    def test_home_action_while_playing_requests_home(self):
        c = self.controller()
        with mock.patch.object(server, "playback", return_value={"active": True}), \
                mock.patch.object(server, "mpv", return_value=None), mock.patch.object(server.threading, "Thread"):
            c.action("home", {})
        self.assertEqual(self.ui()["screen"], "home")
        self.assertEqual(c.app, "home")

    def test_mpv_exit_cleans_up_once(self):
        c = self.controller()
        c.proc.poll.return_value = 0
        c.session["pos"] = 950.0
        with mock.patch.object(server, "mpv", return_value=None) as m, mock.patch.object(server.time, "sleep"), \
                mock.patch.object(server.threading, "Thread"):
            m.side_effect = OSError
            c._playback_loop(5)
        self.eng.remove.assert_called_once_with("a" * 40)
        self.assertEqual((c.app, c.phase), ("home", "ready"))
        entry = server.history.load()["tt9:1:2"]
        self.assertTrue(entry["finished"] or entry["position"] == 950.0)
        self.assertEqual(self.ui()["screen"], "details")

    def test_play_media_uses_resume_position_unless_explicit(self):
        server.history.record({"type": "movie", "id": "tt77", "video_id": "tt77", "name": "M", "position": 300, "duration": 6000})
        c = self.controller()
        c.session = None
        with mock.patch.object(server.threading, "Thread") as thread, mock.patch.object(server, "playback", return_value={"active": False}):
            c.action("play_media", {"type": "movie", "id": "tt77"})
            self.assertEqual(thread.call_args.kwargs["args"][2], 300.0)
            c.action("play_media", {"type": "movie", "id": "tt77", "start": 0})
            self.assertEqual(thread.call_args.kwargs["args"][2], 0.0)

    def test_open_search_switches_home_and_writes_request(self):
        c = self.controller()
        with mock.patch.object(server, "playback", return_value={"active": False}), mock.patch.object(server, "mpv", side_effect=OSError), \
                mock.patch.object(server.threading, "Thread"):
            self.assertEqual(c.action("open_search", {}), {"ok": True, "target": "home"})
        self.assertEqual((c.app, self.ui()["screen"]), ("home", "search"))
        self.assertIn("ts", self.ui())

    def test_open_search_stays_on_youtube(self):
        c = self.controller()
        c.app = "youtube"
        with mock.patch.object(server, "window_for", return_value={"id": "0x9"}), mock.patch.object(server, "activate") as act, \
                mock.patch.object(server, "command") as cmd, mock.patch.object(c, "select") as select, \
                mock.patch.object(c, "_ui_request") as ui:
            self.assertEqual(c.action("open_search", {}), {"ok": True, "target": "youtube"})
        act.assert_called_once_with({"id": "0x9"})
        cmd.assert_called_once_with(["xdotool", "key", "--clearmodifiers", "s"], check=False)
        select.assert_not_called()
        ui.assert_not_called()
        self.assertEqual(c.app, "youtube")

    def test_select_home_requests_home_and_ts_increases(self):
        c = self.controller()
        c.session = None
        with mock.patch.object(server, "playback", return_value={"active": False}), mock.patch.object(server, "mpv", side_effect=OSError), \
                mock.patch.object(server.threading, "Thread"):
            c.select("home")
            first = self.ui()
            c.action("open_search", {})
        self.assertEqual(first["screen"], "home")
        self.assertEqual(self.ui()["screen"], "search")
        self.assertGreater(self.ui()["ts"], first["ts"])

    def test_end_playback_runs_once(self):
        c = self.controller()
        with mock.patch.object(server, "mpv", return_value=None):
            self.assertTrue(c._end_playback())
            with mock.patch.object(server, "playback", return_value={"active": False}):
                self.assertFalse(c._end_playback())
        self.eng.remove.assert_called_once()

    def _ready(self):
        with mock.patch.object(server.threading, "Thread"):
            c = server.Controller()
        c.app, c.phase = "youtube", "ready"
        return c

    def test_move_uses_xtest_without_spawning(self):
        c = self._ready()
        with mock.patch.object(c, "_focus_target") as focus, mock.patch.object(c.pointer, "move", return_value=True) as move, \
                mock.patch.object(server, "command") as run:
            c.action("move", {"dx": 5, "dy": -3})
            c.action("move", {"dx": 1, "dy": 1})
        self.assertEqual(move.call_args_list[0].args[0], [(5, -3)])
        run.assert_not_called()
        self.assertEqual(focus.call_count, 1)  # throttled to once per 2 s

    def test_move_falls_back_to_xdotool(self):
        c = self._ready()
        with mock.patch.object(c, "_focus_target"), mock.patch.object(c.pointer, "move", return_value=False), \
                mock.patch.object(server, "command") as run:
            c.action("move", {"dx": 5, "dy": -3})
        self.assertEqual(run.call_args.args[0], ["xdotool", "mousemove_relative", "--", "5", "-3"])

    def test_move_steps_are_validated(self):
        c = self._ready()
        with mock.patch.object(c, "_focus_target"), mock.patch.object(c.pointer, "move", return_value=True) as move:
            c.action("move", {"steps": [[1, 2], [3, 4]]})
            self.assertEqual(move.call_args.args[0], [(1, 2), (3, 4)])
            for bad in ([], [[1, 2]] * 21, [[1]], [[301, 0]], "x"):
                with self.assertRaises(ValueError):
                    c.action("move", {"steps": bad})

    def test_click_uses_xtest(self):
        c = self._ready()
        with mock.patch.object(c, "_focus_target"), mock.patch.object(c.pointer, "click", return_value=True) as click, \
                mock.patch.object(server, "command") as run:
            c.action("scrolldown", {})
        click.assert_called_once_with(5)
        run.assert_not_called()

    def _series(self, c, phase="buffering"):
        c.phase = phase
        c.media = {"type": "series", "id": "tt1", "video_id": "tt1:1:10", "season": 1, "episode": 10,
                   "name": "Show", "next": {"season": 2, "episode": 1, "name": "Pilot 2"}}

    def test_status_next_only_for_active_series(self):
        c = self._ready()
        self._series(c)
        with mock.patch.object(server, "playback", return_value={"active": False}), mock.patch.object(server, "mpv", side_effect=OSError), \
                mock.patch.object(server, "command", return_value=""):
            self.assertEqual(c.status()["media"]["next"], {"season": 2, "episode": 1, "name": "Pilot 2"})
            c.phase = "ready"
            self.assertIsNone(c.status()["media"]["next"])
            c.media = {"type": "movie", "id": "tt2", "video_id": "tt2", "next": None}
            c.phase = "buffering"
            self.assertIsNone(c.status()["media"]["next"])

    def test_next_episode_plays_stored_next(self):
        c = self._ready()
        self._series(c, "ready")
        with mock.patch.object(c, "play_media", return_value={"ok": True}) as play:
            c.action("next_episode", {})
        play.assert_called_once_with({"type": "series", "id": "tt1", "season": 2, "episode": 1})
        c.media["next"] = None
        with self.assertRaisesRegex(RuntimeError, "No next episode"):
            c.action("next_episode", {})


if __name__ == "__main__":
    unittest.main()
