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

_guards = []


def setUpModule():
    # Controller() starts a real _watch thread and select()/stop_playback() spawn _show_home threads;
    # without these guards a test run launches TV Home (and other apps) on the user's display.
    for target in (mock.patch.object(server.subprocess, "Popen"), mock.patch.object(server.Controller, "_show_home"),
                   mock.patch.object(server.Controller, "_watch"), mock.patch.object(server.Controller, "_yield_audio")):
        target.start()
        _guards.append(target)


def tearDownModule():
    while _guards:
        _guards.pop().stop()


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

    def worker(self, c, stream=None, mpv_results=None, healthy=True, found=None, tracks=(), loop=False, local=True, paused=False):
        c.action_lock = server.threading.RLock()
        with mock.patch.object(server.threading, "Thread"), mock.patch.object(server, "playback", return_value={"active": False}):
            c.action("play_media", {"type": "movie", "id": "tt0063350"})
        gen = c.generation
        c.recovery_paused = paused
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

    def test_worker_preserves_paused_state_during_source_switch(self):
        popen, _ = self.worker(self.controller(), paused=True)
        self.assertIn("--pause=yes", popen.call_args.args[0])

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

    def test_show_writes_ui_request_for_valid_screens(self):
        c = self.controller()
        c.app, c.phase, c.buffering = "home", "ready", None
        with mock.patch.object(server, "playback", return_value={"active": False}):
            for screen in ("movies", "series", "home"):
                self.assertEqual(c.action("show", {"screen": screen}), {"ok": True})
                self.assertEqual(self.ui()["screen"], screen)
            with self.assertRaises(ValueError):
                c.action("show", {"screen": "settings"})
            with self.assertRaises(ValueError):
                c.action("show", {})

    def test_show_ignored_unless_home_idle(self):
        c = self.controller()
        with mock.patch.object(c, "_ui_request") as ui, mock.patch.object(server, "playback", return_value={"active": False}):
            c.app, c.phase, c.buffering = "youtube", "ready", None
            self.assertEqual(c.action("show", {"screen": "movies"}), {"ok": True, "ignored": True})
            c.app, c.phase = "player", "buffering"
            self.assertEqual(c.action("show", {"screen": "series"}), {"ok": True, "ignored": True})
            c.app, c.phase, c.buffering = "home", "ready", None
        with mock.patch.object(c, "_ui_request") as ui, mock.patch.object(server, "playback", return_value={"active": True}):
            self.assertEqual(c.action("show", {"screen": "movies"}), {"ok": True, "ignored": True})
        ui.assert_not_called()
        self.assertEqual(c.app, "home")

    def test_visualizer_state_reads_fresh_viz_json_only(self):
        c = self.controller()
        path = server.DATA / "viz.json"
        path.unlink(missing_ok=True)
        self.assertEqual(c.visualizer_state(), {"active": False, "mode": None})
        path.write_text('{"active": true, "mode": "prism"}')
        self.assertEqual(c.visualizer_state(), {"active": True, "mode": "prism"})
        old = server.time.time() - 60
        os.utime(path, (old, old))                      # Home stopped writing it
        self.assertEqual(c.visualizer_state(), {"active": False, "mode": None})
        path.unlink()

    def test_viz_next_requests_home_only_while_visualizer_runs(self):
        c = self.controller()
        with mock.patch.object(c, "visualizer_state", return_value={"active": False, "mode": None}), \
                mock.patch.object(c, "_ui_request") as ui:
            self.assertEqual(c.action("viz_next", {}), {"ok": True, "ignored": True})
        ui.assert_not_called()
        with mock.patch.object(c, "visualizer_state", return_value={"active": True, "mode": "ridge"}):
            self.assertEqual(c.action("viz_next", {}), {"ok": True})
        self.assertEqual(self.ui()["screen"], "viz_next")

    def test_youtube_media_validates_and_invalidates_cache(self):
        c = self.controller()
        with self.assertRaises(ValueError):
            c.action("youtube_media", {"cmd": "volume_up"})
        state = {"status": "Paused", "title": "t", "artist": "", "art": None, "source": "youtube"}
        with mock.patch.object(server.nowplaying, "control") as ctl, mock.patch.object(server.nowplaying, "current", return_value=state), \
                mock.patch.object(server.time, "sleep"):
            c._yt_cache = (server.time.monotonic(), None)
            self.assertEqual(c.action("youtube_media", {"cmd": "play_pause"}),
                             {"youtube_now": dict(state, youtube_mode="video", youtube_reason="no music signal")})
        ctl.assert_called_once_with("play_pause", None)

    def test_notification_pause_sets_mpv_state_without_toggling(self):
        c = self.controller()
        with mock.patch.object(server, "playback", return_value={"active": True}), mock.patch.object(server, "mpv") as mpv:
            for paused in (True, True, False):
                c.action("set_paused", {"paused": paused})
            self.assertEqual(mpv.call_args_list, [mock.call("set_property", "pause", True),
                                                mock.call("set_property", "pause", True),
                                                mock.call("set_property", "pause", False)])
            for bad in (None, "true", 0, 1):
                with self.assertRaises(ValueError):
                    c.action("set_paused", {"paused": bad})
        with mock.patch.object(server, "playback", return_value={"active": False}), mock.patch.object(server, "mpv") as mpv:
            with self.assertRaisesRegex(RuntimeError, "only while MPV"):
                c.action("set_paused", {"paused": True})
            mpv.assert_not_called()

    def test_notification_youtube_transport_uses_explicit_commands(self):
        c = self.controller()
        with mock.patch.object(server.nowplaying, "control") as ctl, \
                mock.patch.object(c, "youtube_now", return_value=None), mock.patch.object(server.time, "sleep"):
            for cmd in ("play", "pause", "stop"):
                c.action("youtube_media", {"cmd": cmd})
        self.assertEqual(ctl.call_args_list, [mock.call("play", None), mock.call("pause", None), mock.call("stop", None)])

    def test_youtube_media_seek_and_set_position_validate_and_clamp(self):
        c = self.controller()
        with mock.patch.object(server.nowplaying, "control") as ctl, mock.patch.object(server.nowplaying, "current", return_value=None), \
                mock.patch.object(server.time, "sleep"):
            c.action("youtube_media", {"cmd": "seek", "offset": -10})
            c.action("youtube_media", {"cmd": "seek", "offset": 99999})
            c.action("youtube_media", {"cmd": "set_position", "position": -5})
            for bad in ({"cmd": "seek"}, {"cmd": "seek", "offset": "10"}, {"cmd": "set_position", "position": True},
                        {"cmd": "seek", "offset": float("nan")}):
                with self.assertRaises(ValueError):
                    c.action("youtube_media", bad)
        self.assertEqual([call.args for call in ctl.call_args_list],
                         [("seek", -10), ("seek", 3600), ("set_position", 0)])

    def test_youtube_now_is_cached_and_survives_errors(self):
        c = self.controller()
        c._yt_cache = None
        with mock.patch.object(server.nowplaying, "current", side_effect=RuntimeError("bus")) as cur:
            self.assertIsNone(c.youtube_now())
            self.assertIsNone(c.youtube_now())
        self.assertEqual(cur.call_count, 1)
        c._yt_cache = None
        with mock.patch.object(server.nowplaying, "current", return_value={"status": "Open"}):
            self.assertEqual(c.youtube_now(), {"status": "Open"})

    def test_status_includes_youtube_now(self):
        c = self.controller()
        c._yt_cache = (server.time.monotonic(), {"status": "Open"})
        with mock.patch.object(server, "mpv", side_effect=OSError), mock.patch.object(server, "command", side_effect=OSError), \
                mock.patch.object(server, "playback", return_value={"active": False}), mock.patch.object(server, "brightness", return_value={}), \
                mock.patch.object(server, "screen_blanked", return_value=False):
            self.assertEqual(c.status()["youtube_now"], {"status": "Open"})

    def test_display_env_override(self):
        with mock.patch.dict(os.environ, {}, clear=False):
            for k in ("IMACTV_DISPLAY", "IMACTV_XAUTHORITY"):
                os.environ.pop(k, None)
            self.assertEqual(server._display_env()["DISPLAY"], ":0")
            os.environ["IMACTV_DISPLAY"] = ":97"
            os.environ["XAUTHORITY"] = "/tmp/xa"
            env = server._display_env()
            self.assertEqual((env["DISPLAY"], env["XAUTHORITY"]), (":97", "/tmp/xa"))
            os.environ["IMACTV_XAUTHORITY"] = "/tmp/other"
            self.assertEqual(server._display_env()["XAUTHORITY"], "/tmp/other")

    def test_remote_presence(self):
        c = self.controller()
        phone, other = c.new_device(), c.new_device()
        home = (server.DATA / "home_token").read_text().strip()
        jar = lambda t: {"imactv": mock.Mock(value=t)}
        self.assertEqual(c.remote_state(), {"connected": False, "last_seen": None, "devices": c.remote_state()["devices"], "paired_at": c.remote_state()["paired_at"]})
        base = c.remote_state()["devices"]
        paired_at = c.remote_state()["paired_at"]
        with mock.patch.object(server, "command", return_value="192.168.68.5"), mock.patch.object(server.time, "time", return_value=1000.0):
            c.seen(jar(home), "192.168.68.20")      # the TV's own session
            c.seen(jar(phone), "127.0.0.1")         # local caller
            c.seen(jar(phone), "192.168.68.5")      # this machine's own address
            c.seen({}, "192.168.68.20")             # no cookie
            c.seen(jar("unpaired"), "192.168.68.20")
            self.assertFalse(c.remote_state()["connected"])
            c.seen(jar(phone), "192.168.68.20")
        with mock.patch.object(server.time, "time", return_value=1044.0):
            self.assertEqual(c.remote_state(), {"connected": True, "last_seen": 1000.0, "devices": base, "paired_at": paired_at})
        with mock.patch.object(server.time, "time", return_value=1046.0):
            self.assertFalse(c.remote_state()["connected"])
            self.assertEqual(c.remote_state()["last_seen"], 1000.0)
        with mock.patch.object(server.time, "time", return_value=1100.0):
            c.seen(jar(other), "192.168.68.21")
            self.assertTrue(c.remote_state()["connected"])
        c.sessions.pop(server.hashlib.sha256(other.encode()).hexdigest())
        c.sessions.pop(server.hashlib.sha256(phone.encode()).hexdigest())
        with mock.patch.object(server.time, "time", return_value=1101.0):
            self.assertFalse(c.remote_state()["connected"])
            self.assertEqual(c.remote_state()["devices"], base - 2)

    def test_remote_pairing_timestamp_survives_reconnect_and_controller_restart(self):
        c = self.controller()
        with mock.patch.object(server.time, "time", return_value=4000000000.0):
            c.renew_pairing()
            token = c.pair(c.pair_code, "192.168.68.20")
        paired_at = c.remote_state()["paired_at"]
        self.assertEqual(paired_at, 4000000000.0)
        jar = {"imactv": mock.Mock(value=token)}
        with mock.patch.object(c, "_is_local", return_value=False):
            c.seen(jar, "192.168.68.20")
            c.presence.clear()
            c.seen(jar, "192.168.68.20")
        self.assertEqual(c.remote_state()["paired_at"], paired_at)
        self.assertEqual(self.controller().remote_state()["paired_at"], paired_at)

    def test_status_has_remote_block(self):
        c = self.controller()
        with mock.patch.object(server, "mpv", side_effect=OSError), mock.patch.object(server, "command", side_effect=OSError), \
                mock.patch.object(server, "playback", return_value={"active": False}), mock.patch.object(server, "brightness", return_value={}), \
                mock.patch.object(server, "screen_blanked", return_value=False):
            self.assertEqual(set(c.status()["remote"]), {"connected", "last_seen", "devices", "paired_at"})

    def test_select_home_keeps_youtube_running_and_select_youtube_restores_it(self):
        c = self.controller()
        yt, home = {"id": "0x9", "class": "vacuumtube", "title": "YouTube"}, {"id": "0x1", "class": "x", "title": "iMac TV"}
        with mock.patch.object(server, "window_for", side_effect=lambda a: {"youtube": yt, "home": home}.get(a)), \
                mock.patch.object(server, "minimize") as mini, mock.patch.object(c, "_show_home"), mock.patch.object(c, "_end_playback"), \
                mock.patch.object(server.threading, "Thread"):
            c.select("home")
        mini.assert_not_called()
        with mock.patch.object(server, "window_for", return_value=yt), mock.patch.object(server, "activate") as act, \
                mock.patch.object(server.time, "sleep"):
            c.generation += 1
            c._launch("youtube", c.generation)
        act.assert_called_once_with(yt)
        with mock.patch.object(server, "command") as run:
            server.minimize(yt)
        run.assert_called_once_with(["xdotool", "windowminimize", "0x9"], check=False)

    def test_activate_restores_iconified_window_before_fullscreen(self):
        with mock.patch.object(server, "command") as run:
            server.activate({"id": "0x9"})
        self.assertEqual(run.call_args_list, [
            mock.call(["xdotool", "windowmap", "0x9"], check=False),
            mock.call(["wmctrl", "-i", "-R", "0x9"]),
            mock.call(["wmctrl", "-i", "-r", "0x9", "-b", "add,fullscreen"], check=False),
        ])

    def test_select_home_without_windows_is_safe(self):
        c = self.controller()
        for wins in ({}, {"youtube": {"id": "0x9"}}):   # no YouTube; YouTube but Home isn't up yet
            with mock.patch.object(server, "window_for", side_effect=lambda a, w=wins: w.get(a)), mock.patch.object(server, "minimize") as mini, \
                    mock.patch.object(c, "_show_home"), mock.patch.object(c, "_end_playback"):
                c.select("home")
            mini.assert_not_called()
        with mock.patch.object(server, "window_for", side_effect=OSError), mock.patch.object(c, "_show_home"), mock.patch.object(c, "_end_playback"):
            c.select("home")

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



class PlaybackPromptTests(unittest.TestCase):
    """Skip intro, up next and "still watching" as the controller drives them (fake mpv, no waiting)."""
    controller = LifecycleTests.controller
    ui = LifecycleTests.ui
    SEGS = [{"kind": "intro", "start": 10, "end": 40, "source": "chapters"},
            {"kind": "credits", "start": 900, "end": 1000, "source": "chapters"}]
    NEXT = {"season": 1, "episode": 3, "name": "Third"}

    def prepared(self, pos=20.0):
        c = self.controller()
        with c.lock:
            c.flow.begin(self.NEXT, self.SEGS, {})
        self.pos = pos
        return c

    def fake(self, log=None):
        def run(*args):
            if log is not None:
                log.append(args)
            return {"time-pos": self.pos, "duration": 1000.0, "pause": False}.get(args[-1]) if args[0] == "get_property" else None
        return run

    def test_ok_inside_intro_skips_otherwise_shows_progress(self):
        c = self.prepared(20.0)
        log = []
        with mock.patch.object(server, "playback", return_value={"active": True}), mock.patch.object(server, "mpv", side_effect=self.fake(log)):
            self.assertEqual(c.action("ok", {})["handled"], "skip")
            self.assertIn(("seek", 40, "absolute"), log)
            self.pos = 100.0
            log.clear()
            self.assertEqual(c.action("ok", {}), {"ok": True})
            self.assertIn(("show-progress",), log)

    def test_status_exposes_segment_upnext_and_still(self):
        c = self.prepared(20.0)
        self.assertEqual(c._flow_view({"active": True, "position": 20})["segment"], {"kind": "intro", "end": 40})
        self.assertEqual(c._flow_view({"active": False}), {"segment": None, "upnext": None, "still_watching": None})
        with c.lock:
            c.flow.tick(950, 1000)
        view = c._flow_view({"active": True, "position": 950})
        self.assertEqual(view["upnext"]["episode"], 3)
        self.assertIsNone(view["still_watching"])

    def test_back_cancels_countdown_then_stops_normally(self):
        c = self.prepared(950.0)
        with c.lock:
            c.flow.tick(950, 1000)
        with mock.patch.object(server, "playback", return_value={"active": True}), mock.patch.object(server, "mpv", side_effect=self.fake()), \
                mock.patch.object(server.threading, "Thread"):
            self.assertEqual(c.action("back", {})["handled"], "cancel")
            self.assertEqual(c.app, "player")
            c.action("back", {})
        self.assertEqual(c.app, "home")

    def test_ok_during_countdown_starts_next_episode_with_prefetched_source(self):
        c = self.prepared(950.0)
        chosen = {"info_hash": "b" * 40, "file_idx": 1, "label": "x", "sources": []}
        c.prefetch = {"video_id": "tt9:1:3", "chosen": chosen}
        with c.lock:
            c.flow.tick(950, 1000)
        with mock.patch.object(server, "playback", return_value={"active": True}), mock.patch.object(server, "mpv", side_effect=self.fake()), \
                mock.patch.object(server.threading, "Thread") as thread:
            self.assertEqual(c.action("ok", {})["handled"], "play_next")
        self.assertEqual(c.media["episode"], 3)
        self.assertEqual(thread.call_args.kwargs["args"][3], chosen)
        self.assertEqual(c.flow.count, 1)

    def test_manual_play_resets_autoplay_count_but_autoplay_does_not(self):
        c = self.prepared()
        c.flow.count = 2
        with mock.patch.object(server, "playback", return_value={"active": False}), mock.patch.object(server, "mpv", side_effect=OSError), \
                mock.patch.object(server.threading, "Thread"):
            c.action("play_media", {"type": "series", "id": "tt9", "season": 1, "episode": 4})
        self.assertEqual(c.flow.count, 0)

    def test_still_watching_answers(self):
        for choice, expect_app in (("stop", "home"), ("continue", "player")):
            c = self.prepared(950.0)
            c.flow.count = 3
            with c.lock:
                c.flow.begin(self.NEXT, self.SEGS, {})
                c.flow.tick(950, 1000)
            self.assertIsNotNone(c.flow.view(950)["still_watching"])
            with mock.patch.object(server, "playback", return_value={"active": True}), mock.patch.object(server, "mpv", side_effect=self.fake()), \
                    mock.patch.object(server.threading, "Thread"):
                c.action("still_watching", {"choice": choice})
            self.assertEqual(c.app, expect_app)
            self.assertEqual(c.media["episode"], 3 if choice == "continue" else 2)

    def test_prompt_actions_validate(self):
        c = self.prepared()
        for action, choice in (("upnext", "maybe"), ("still_watching", "play")):
            with self.assertRaises(ValueError):
                c.action(action, {"choice": choice})

    def test_skip_segment_action(self):
        c = self.prepared(20.0)
        log = []
        with mock.patch.object(server, "mpv", side_effect=self.fake(log)):
            c.action("skip_segment", {})
            self.pos = 100.0
            with self.assertRaises(RuntimeError):
                c.action("skip_segment", {})
        self.assertIn(("seek", 40, "absolute"), log)

    def test_any_action_is_input(self):
        c = self.prepared()
        c.flow.count = 2
        with mock.patch.object(server, "mpv", return_value=None), mock.patch.object(server, "playback", return_value={"active": False}):
            c.action("timer", {"minutes": None})
        self.assertEqual(c.flow.count, 0)

    def test_flow_loop_runs_events_and_osd(self):
        c = self.prepared(950.0)
        log = []
        with c.lock:
            c.flow.tick(950, 1000)
            c.flow.deadline = 0     # countdown already over
        gen = c.generation
        with mock.patch.object(server, "mpv", side_effect=self.fake(log)), mock.patch.object(server.time, "sleep"), \
                mock.patch.object(server.threading, "Thread"):
            c._flow_loop(gen)
        self.assertEqual(c.media["episode"], 3)
        self.pos = 20.0

    def test_intro_announce_and_keybind(self):
        c = self.prepared(20.0)
        log = []
        calls = {"n": 0}

        def stop_after(*_):
            calls["n"] += 1
            if calls["n"] > 1:
                c.generation += 1
        with mock.patch.object(server, "mpv", side_effect=self.fake(log)), mock.patch.object(server.time, "sleep", side_effect=stop_after), \
                mock.patch.object(server.threading, "Thread"):
            c._flow_loop(c.generation)
        self.assertIn(("show-text", "Press OK to skip intro", 5000), log)
        self.assertIn(("keybind", "ENTER", "seek 40 absolute"), log)

    def test_exit_near_end_rolls_into_next_episode(self):
        c = self.prepared(990.0)
        c.proc.poll.return_value = 0
        c.session["pos"], c.session["dur"] = 990.0, 1000.0
        with c.lock:
            c.flow.begin(self.NEXT, [], {})
        with mock.patch.object(server, "mpv", side_effect=OSError), mock.patch.object(server.time, "sleep"), \
                mock.patch.object(server.threading, "Thread"):
            c._playback_loop(5)
        self.assertEqual((c.app, c.media["episode"]), ("player", 3))
        self.assertTrue(server.history.load()["tt9:1:2"]["finished"])

    def test_load_segments_merges_chapters_and_db(self):
        c = self.prepared()
        answers = {"chapter-list": [{"title": "Intro", "time": 5.0}, {"title": "Scene", "time": 60.0}], "duration": 1000.0}
        db = [{"kind": "credits", "start": 900, "end": 1000.0, "source": "theintrodb"}]
        with mock.patch.object(server, "mpv", side_effect=lambda *a: answers[a[-1]]), \
                mock.patch.object(server.media_segments, "lookup", return_value=db) as look:
            c._load_segments(c.generation, "series", "tt9", 1, 2, {"tvdb_id": 81189, "moviedb_id": 1396})
        self.assertEqual([s["kind"] for s in c.flow.segments], ["intro", "credits"])
        self.assertEqual(look.call_args.kwargs["tvdb_id"], 81189)
        self.assertEqual(look.call_args.kwargs["duration"], 1000.0)


class PlaybackRecoveryTests(unittest.TestCase):
    controller = LifecycleTests.controller

    def setup_source(self):
        c = self.controller()
        raw = [{"name": "[TB+] Torrentio 2160p", "title": "Show 2160p REMUX HEVC\n💾 20 GB",
                "url": "https://example.com/secret/original"},
               {"name": "[TB+] Torrentio 1080p", "title": "Show 1080p x264\n💾 5 GB",
                "url": "https://example.com/secret/alternative"}]
        c.current_source = server.media_streams.parse(raw[0])
        c.health_tried.add(server.media_health.identity(c.current_source))
        c.recovery_position = 100
        return c, raw

    def offer(self, c, raw, reason="decode"):
        def thread(target, **kwargs):
            return mock.Mock(start=target)
        with mock.patch.object(server.addons, "streams", return_value=raw), \
                mock.patch.object(server.threading, "Thread", side_effect=thread):
            c._offer_alternative(c.generation, reason)

    def test_offer_public_fields_do_not_leak_credentials(self):
        c, raw = self.setup_source()
        self.offer(c, raw)
        public = c._health_view()
        self.assertEqual(public["reason"], "decode")
        self.assertIn("1080p", public["alternative"])
        self.assertNotIn("secret", str(public))
        self.assertNotIn("url", public)
        self.assertIn("secret", c.health_suggestion["source"]["url"])

    def test_switch_freezes_then_reads_current_position_and_preserves_pause(self):
        for paused in (False, True):
            c, raw = self.setup_source()
            self.offer(c, raw)
            prompt = c.health_suggestion
            def mpv(*args):
                return {"time-pos": 187.375, "pause": paused}.get(args[-1])
            with mock.patch.object(server, "mpv", side_effect=mpv) as ipc, \
                    mock.patch.object(c, "play_media", return_value={"ok": True}) as play:
                c.action("playback_suggestion", {"id": prompt["id"], "choice": "switch"})
            self.assertEqual(ipc.call_args_list[:3], [mock.call("get_property", "pause"),
                mock.call("set_property", "pause", True), mock.call("get_property", "time-pos")])
            self.assertEqual(play.call_args.args[0], {"type": "series", "id": "tt9", "season": 1, "episode": 2, "start": 187.375})
            self.assertEqual(play.call_args.kwargs, {"prefetched": prompt["source"], "recovery": True, "paused": paused})

    def test_failed_source_uses_last_position_and_remote_ok_works_without_player(self):
        c, raw = self.setup_source()
        c.app, c.phase = "home", "ready"
        self.offer(c, raw, "unplayable")
        with mock.patch.object(server, "mpv", side_effect=OSError), mock.patch.object(c, "play_media") as play:
            c.action("ok", {})
        self.assertEqual(play.call_args.args[0]["start"], 100)

    def test_dismiss_and_stale_responses_do_not_switch(self):
        c, raw = self.setup_source()
        self.offer(c, raw)
        token = c.health_suggestion["id"]
        with mock.patch.object(c, "play_media") as play, mock.patch.object(server, "mpv") as ipc:
            self.assertTrue(c.action("playback_suggestion", {"id": "old", "choice": "switch"})["ignored"])
            self.assertIsNotNone(c.health_suggestion)
            c.action("back", {})
            self.assertIsNone(c.health_suggestion)
            c.action("playback_suggestion", {"id": token, "choice": "switch"})
        play.assert_not_called()
        ipc.assert_not_called()
        # Once dismissed, this cause cannot create another prompt for the same source.
        self.offer(c, raw)
        self.assertIsNone(c.health_suggestion)

    def test_no_alternative_and_old_generation_create_no_prompt(self):
        c, raw = self.setup_source()
        self.offer(c, raw[:1])
        self.assertIsNone(c.health_suggestion)
        c.health_requests.clear()
        def stale(*args):
            c.generation += 1
            return raw
        with mock.patch.object(server.addons, "streams", side_effect=stale), \
                mock.patch.object(server.threading, "Thread", side_effect=lambda target, **kw: mock.Mock(start=target)):
            c._offer_alternative(c.generation, "decode")
        self.assertIsNone(c.health_suggestion)

    def test_pending_recommendation_cannot_reappear_during_stop(self):
        c, raw = self.setup_source()
        def stop_during_fetch(*args):
            with mock.patch.object(server, "mpv", side_effect=OSError):
                c._end_playback()
            return raw
        with mock.patch.object(server.addons, "streams", side_effect=stop_during_fetch), \
                mock.patch.object(server.threading, "Thread", side_effect=lambda target, **kw: mock.Mock(start=target)):
            c._offer_alternative(c.generation, "decode")
        self.assertIsNone(c.health_suggestion)

    def test_failure_records_position_and_offers_another_source(self):
        c, raw = self.setup_source()
        with mock.patch.object(c, "_record") as record, mock.patch.object(c, "_offer_alternative") as offer:
            c._play_fail(c.generation, RuntimeError("decode failed"))
        record.assert_called_once()
        self.assertEqual(c.recovery_position, 100)
        offer.assert_called_once_with(c.generation, "unplayable")
        self.assertEqual((c.app, c.phase), ("home", "ready"))

    def test_startup_failure_keeps_original_resume_timestamp(self):
        c, raw = self.setup_source()
        c.session = None
        c.recovery_position = 923.5
        with mock.patch.object(c, "_offer_alternative"):
            c._play_fail(c.generation, RuntimeError("startup failed"))
        self.assertEqual(c.recovery_position, 923.5)

    def test_player_error_offers_recovery_but_normal_exit_does_not(self):
        for code in (0, 2):
            c, raw = self.setup_source()
            c.proc.poll.return_value = code
            with mock.patch.object(server, "mpv", side_effect=OSError), \
                    mock.patch.object(server.time, "sleep"), \
                    mock.patch.object(server.threading, "Thread"), \
                    mock.patch.object(c, "_offer_alternative") as offer:
                c._playback_loop(c.generation)
            self.assertEqual(offer.called, code != 0)

    def test_health_tick_tolerates_optional_properties(self):
        c, raw = self.setup_source()
        c.health_monitor = mock.Mock()
        c.health_monitor.tick.return_value = "decode"
        with mock.patch.object(server, "mpv", side_effect=RuntimeError("property unavailable")), \
                mock.patch.object(c, "_offer_alternative") as offer:
            c._health_tick(c.generation, 125, False)
        self.assertEqual(c.session["pos"], 125)
        offer.assert_called_once_with(c.generation, "decode")
        self.assertTrue(all(value is None for value in c.health_monitor.tick.call_args.args[2].values()))

    def test_source_metadata_survives_explicit_selection(self):
        c, raw = self.setup_source()
        with mock.patch.object(server.addons, "streams", return_value=raw):
            result = c.action("sources", {"type": "series", "id": "tt9", "season": 1, "episode": 2})
        ref = next(s["ref"] for s in result["sources"] if s["quality"] == "2160p")
        selected = c._resolve_stream({"ref": ref})
        self.assertEqual(selected["size_bytes"], 20e9)
        self.assertEqual(selected["quality"], "2160p")

    def test_new_play_clears_prompt_and_recovery_keeps_tried_sources(self):
        c, raw = self.setup_source()
        self.offer(c, raw)
        original = set(c.health_tried)
        with mock.patch.object(server, "mpv", side_effect=OSError), mock.patch.object(server.threading, "Thread"):
            c.play_media({"type": "movie", "id": "tt77", "start": 123}, recovery=True)
            self.assertEqual(c.health_tried, original)
            self.assertEqual(c.recovery_position, 123)
            self.assertIsNone(c.health_suggestion)
            c.play_media({"type": "movie", "id": "tt88", "start": 0})
        self.assertFalse(c.health_tried)


if __name__ == "__main__":
    unittest.main()
