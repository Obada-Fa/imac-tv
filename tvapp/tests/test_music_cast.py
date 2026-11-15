"""Music casting from the YouTube Music app: now-playing parsing, TV controls over mpv's socket, supervision."""
import json
import os
from pathlib import Path
import socket
import sys
import tempfile
import threading
import time
import unittest
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import music_cast  # noqa: E402


class NowPlaying(unittest.TestCase):
    def test_a_playing_song_becomes_a_cast_now_state(self):
        st = music_cast.parse_now({"status": "Playing", "title": "Song", "artist": "Band", "art": "http://a/b.jpg",
                                   "id": "x1", "position": 12.5, "duration": 200, "updated": 1000.0}, now=1005.0)
        self.assertEqual((st["status"], st["title"], st["artist"], st["source"]), ("Playing", "Song", "Band", "cast"))

    def test_stopped_stale_or_broken_files_mean_nothing_plays(self):
        self.assertIsNone(music_cast.parse_now({"status": "Stopped", "updated": 1000.0}, now=1001.0))
        self.assertIsNone(music_cast.parse_now({"status": "Playing", "title": "x", "updated": 1000.0}, now=1030.0))
        self.assertIsNone(music_cast.parse_now({"status": "Playing"}, now=1.0))
        self.assertIsNone(music_cast.parse_now(["nope"]))

    def test_a_song_without_metadata_yet_still_shows(self):
        st = music_cast.parse_now({"status": "Paused", "updated": 10.0}, now=11.0)
        self.assertEqual((st["title"], st["artist"]), ("Music", ""))

    def test_now_reads_the_receivers_file(self):
        with tempfile.TemporaryDirectory() as d:
            self.assertIsNone(music_cast.now(d))
            Path(d, music_cast.NOW_FILE).write_text(json.dumps({"status": "Playing", "title": "T", "updated": time.time()}))
            self.assertEqual(music_cast.now(d)["title"], "T")


class FakeMpv:
    """Answers mpv JSON IPC commands on a Unix socket and records them."""
    def __init__(self, path):
        self.commands = []
        self.srv = socket.socket(socket.AF_UNIX)
        self.srv.bind(path)
        self.srv.listen()
        threading.Thread(target=self.serve, daemon=True).start()

    def serve(self):
        while True:
            try:
                c, _ = self.srv.accept()
            except OSError:
                return
            with c:
                msg = json.loads(c.recv(4096).split(b"\n")[0])
                self.commands.append(msg["command"])
                c.sendall(b'{"event":"property-change","name":"pause","data":true}\n' +
                          json.dumps({"request_id": msg["request_id"], "error": "success"}).encode() + b"\n")


class TvControls(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp(dir="/tmp")       # short path: Unix socket names are limited to ~100 bytes
        self.mpv = FakeMpv(os.path.join(self.dir, music_cast.SOCK_NAME))

    def tearDown(self):
        import shutil
        self.mpv.srv.close()
        shutil.rmtree(self.dir, ignore_errors=True)

    def test_ok_on_the_now_playing_tile_toggles_pause(self):
        self.assertTrue(music_cast.toggle(self.dir))
        self.assertEqual(self.mpv.commands, [["cycle", "pause"]])

    def test_a_movie_pauses_cast_music_only_while_something_plays(self):
        self.assertFalse(music_cast.pause(self.dir))
        self.assertEqual(self.mpv.commands, [])
        Path(self.dir, music_cast.NOW_FILE).write_text(json.dumps({"status": "Playing", "updated": time.time()}))
        self.assertTrue(music_cast.pause(self.dir))
        self.assertEqual(self.mpv.commands, [["set_property", "pause", True]])

    def test_no_receiver_running_is_not_an_error(self):
        with tempfile.TemporaryDirectory(dir="/tmp") as empty:
            self.assertFalse(music_cast.toggle(empty))


class FakeControl:
    """Stands in for receiver.mjs's control socket: records each command and answers ok."""
    def __init__(self, path, ok=True):
        self.commands, self.ok = [], ok
        self.srv = socket.socket(socket.AF_UNIX)
        self.srv.bind(path)
        self.srv.listen()
        threading.Thread(target=self.serve, daemon=True).start()

    def serve(self):
        while True:
            try:
                c, _ = self.srv.accept()
            except OSError:
                return
            with c:
                self.commands.append(json.loads(c.recv(4096).split(b"\n")[0]))
                c.sendall(json.dumps({"ok": self.ok}).encode() + b"\n")


class ReceiverControl(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp(dir="/tmp")
        self.ctl = FakeControl(os.path.join(self.dir, music_cast.CTL_NAME))
        self.mpv = FakeMpv(os.path.join(self.dir, music_cast.SOCK_NAME))

    def tearDown(self):
        import shutil
        self.ctl.srv.close()
        self.mpv.srv.close()
        shutil.rmtree(self.dir, ignore_errors=True)

    def test_commands_go_through_the_receiver_not_mpv(self):
        self.assertTrue(music_cast.control(self.dir, "next"))
        self.assertTrue(music_cast.control(self.dir, "seek", offset=-10))
        self.assertTrue(music_cast.toggle(self.dir))
        self.assertEqual(self.ctl.commands, [{"cmd": "next"}, {"cmd": "seek", "offset": -10}, {"cmd": "play_pause"}])
        self.assertEqual(self.mpv.commands, [])

    def test_pause_asks_the_receiver_only_while_playing(self):
        now = Path(self.dir, music_cast.NOW_FILE)
        now.write_text(json.dumps({"status": "Paused", "updated": time.time()}))
        self.assertFalse(music_cast.pause(self.dir))
        now.write_text(json.dumps({"status": "Playing", "updated": time.time()}))
        self.assertTrue(music_cast.pause(self.dir))
        self.assertEqual(self.ctl.commands, [{"cmd": "pause"}])

    def test_unknown_commands_are_refused(self):
        with self.assertRaises(ValueError):
            music_cast.control(self.dir, "rm -rf")

    def test_no_control_socket_is_none(self):
        with tempfile.TemporaryDirectory(dir="/tmp") as empty:
            self.assertIsNone(music_cast.control(empty, "stop"))


class OnePlayer(unittest.TestCase):
    """Cast music and YouTube: whichever started last plays, the other is paused."""
    song = {"status": "Playing", "id": "a", "title": "A"}

    def test_youtube_starting_pauses_the_music(self):
        arb = music_cast.Arbiter()
        self.assertIsNone(arb.step("Paused", self.song))
        self.assertEqual(arb.new_song, self.song)
        self.assertIsNone(arb.step("Paused", self.song))
        self.assertIsNone(arb.new_song)
        self.assertEqual(arb.step("Playing", self.song), "pause_cast")
        self.assertIsNone(arb.step("Playing", dict(self.song, status="Paused")))

    def test_casting_a_song_pauses_youtube(self):
        arb = music_cast.Arbiter()
        self.assertIsNone(arb.step("Playing", None))
        self.assertEqual(arb.step("Playing", self.song), "pause_youtube")
        self.assertIsNone(arb.step("Paused", self.song))

    def test_resuming_the_music_pauses_youtube_again(self):
        arb = music_cast.Arbiter()
        arb.step("Paused", self.song)
        self.assertEqual(arb.step("Playing", self.song), "pause_cast")
        self.assertIsNone(arb.step("Playing", dict(self.song, status="Paused")))
        self.assertEqual(arb.step("Playing", self.song), "pause_youtube")

    def test_next_song_while_youtube_plays_pauses_youtube(self):
        arb = music_cast.Arbiter()
        arb.step(None, self.song)
        self.assertEqual(arb.step("Playing", self.song), "pause_cast")
        arb.step("Playing", dict(self.song, status="Paused"))
        self.assertEqual(arb.step("Playing", {"status": "Playing", "id": "b"}), "pause_youtube")

    def test_both_already_playing_keeps_youtube(self):
        self.assertEqual(music_cast.Arbiter().step("Playing", self.song), "pause_cast")

    def test_pause_retries_keep_the_same_winner_until_the_loser_pauses(self):
        arb = music_cast.Arbiter()
        arb.step("Playing", None)
        self.assertEqual(arb.step("Playing", self.song), "pause_youtube")
        self.assertEqual(arb.step("Playing", self.song), "pause_youtube")
        self.assertIsNone(arb.step("Paused", self.song))
        self.assertEqual(arb.step("Playing", self.song), "pause_cast")
        self.assertEqual(arb.step("Playing", self.song), "pause_cast")

    def test_server_settles_them_and_has_music_controls(self):
        src = (Path(music_cast.__file__).parent / "server.py").read_text()
        self.assertIn("self._one_player(", src)
        self.assertIn('action == "music_media"', src)


class Supervision(unittest.TestCase):
    def test_missing_pieces_leave_it_off(self):
        with mock.patch.object(music_cast, "node_bin", return_value=None), \
                mock.patch.object(music_cast.shutil, "which", return_value=None), \
                mock.patch.object(music_cast, "ytdlp_bin", return_value=None):
            need = music_cast.missing()
            self.assertTrue(any("Node.js" in n for n in need) and "mpv" in need and "yt-dlp" in need)
            sup = music_cast.Supervisor("/tmp/x", popen=mock.Mock())
            self.assertFalse(sup.start())
            sup.popen.assert_not_called()

    def test_node_18_19_does_not_count(self):
        # Zorin ships Node 18.19.1, which can't load youtubei.js (import attributes): the receiver crashed with it.
        local = str(music_cast.LOCAL / "node/bin/node")
        versions = {"/usr/bin/node": (18, 19), local: (22, 23)}
        with mock.patch.object(music_cast.shutil, "which", return_value="/usr/bin/node"), \
                mock.patch.object(music_cast.Path, "is_file", return_value=True), \
                mock.patch.object(music_cast, "node_version", side_effect=lambda b: versions.get(b, (0, 0))):
            self.assertEqual(music_cast.node_bin(), local)
            versions[local] = (0, 0)                          # no downloaded Node: 18.19 still isn't enough
            self.assertIsNone(music_cast.node_bin())
            versions["/usr/bin/node"] = (20, 11)
            self.assertEqual(music_cast.node_bin(), "/usr/bin/node")

    def test_the_receiver_gets_the_tv_data_dir_socket_and_ytdlp(self):
        sup = music_cast.Supervisor("/data/tv")
        with mock.patch.object(music_cast, "ytdlp_bin", return_value="/y/yt-dlp"), \
                mock.patch.object(music_cast, "node_bin", return_value="/n/node"), \
                mock.patch.object(music_cast, "ytdlp_knows", return_value=True):
            env = sup.env()
        self.assertEqual(env["IMACTV_DATA_DIR"], "/data/tv")
        self.assertEqual(env["IMACTV_MUSIC_SOCK"], "/data/tv/" + music_cast.SOCK_NAME)
        self.assertEqual((env["IMACTV_YTDLP"], env["IMACTV_JS_RUNTIME"]), ("/y/yt-dlp", "node:/n/node"))

    def test_an_old_ytdlp_gets_no_js_runtime_option(self):
        with mock.patch.object(music_cast, "ytdlp_bin", return_value="/y/yt-dlp"), \
                mock.patch.object(music_cast, "node_bin", return_value="/n/node"), \
                mock.patch.object(music_cast, "ytdlp_knows", return_value=False):
            self.assertNotIn("IMACTV_JS_RUNTIME", music_cast.Supervisor("/d").env())

    def test_leftover_receiver_and_its_mpv_are_found_but_not_other_tvs(self):
        with tempfile.TemporaryDirectory() as root:
            data = "/data/tv"
            rec = str(music_cast.APP_DIR / "receiver.mjs")
            procs = {"101": ([b"node", rec.encode()], [b"IMACTV_DATA_DIR=" + data.encode()]),
                     "102": ([b"mpv", b"--idle=yes", ("--input-ipc-server=" + data + "/" + music_cast.SOCK_NAME).encode()], []),
                     "103": ([b"node", rec.encode()], [b"IMACTV_DATA_DIR=/other"]),
                     "104": ([b"mpv", b"movie.mkv"], [])}
            for pid, (args, env) in procs.items():
                Path(root, pid).mkdir()
                Path(root, pid, "cmdline").write_bytes(b"\0".join(args) + b"\0")
                Path(root, pid, "environ").write_bytes(b"\0".join(env) + b"\0")
            self.assertEqual(sorted(music_cast.Supervisor(data).stale_pids(root)), [101, 102])


class ServiceControl(unittest.TestCase):
    """Settings -> Playback -> Music casting, the phone's switch and `nexus-tv-cast`."""

    def make(self, data, alive=None):
        def popen(*_a, **_k):
            up = threading.Event()                 # each receiver process lives until it is terminated
            up.set()
            proc = mock.Mock()
            proc.poll.side_effect = lambda: None if up.is_set() else 0
            proc.wait.side_effect = lambda timeout=None: self.block(up)
            proc.terminate.side_effect = up.clear
            return proc
        sup = music_cast.Supervisor(data, popen=mock.Mock(side_effect=popen))
        sup.kill_stale = mock.Mock()
        return sup

    @staticmethod
    def block(up):
        while up.is_set():
            time.sleep(0.01)
        return 0

    def wait_for(self, check):
        for _ in range(200):
            if check():
                return True
            time.sleep(0.01)
        return False

    def test_stop_keeps_it_off_until_started_again(self):
        with tempfile.TemporaryDirectory() as data, mock.patch.object(music_cast, "missing", return_value=[]), \
                mock.patch.object(music_cast, "node_bin", return_value="/n/node"), \
                mock.patch.object(music_cast, "ytdlp_bin", return_value=None):
            sup = self.make(data)
            self.assertEqual(sup.status()["state"], "starting")
            self.assertTrue(sup.start())
            self.assertTrue(self.wait_for(sup.running))
            self.assertEqual(sup.status()["state"], "running")
            sup.switch_off()
            self.assertEqual(sup.status(), {"enabled": False, "state": "off", "missing": [], "port": music_cast.PORT})
            self.assertFalse(music_cast.Supervisor(data).enabled())          # survives a restart of the TV
            self.assertFalse(music_cast.Supervisor(data, popen=mock.Mock()).start())
            self.assertTrue(sup.start(force=True))
            self.assertTrue(self.wait_for(sup.running))
            self.assertTrue(sup.enabled())
            sup.stop()

    def test_restart_runs_one_receiver(self):
        with tempfile.TemporaryDirectory() as data, mock.patch.object(music_cast, "missing", return_value=[]), \
                mock.patch.object(music_cast, "node_bin", return_value="/n/node"), \
                mock.patch.object(music_cast, "ytdlp_bin", return_value=None):
            sup = self.make(data)
            sup.start()
            self.assertTrue(self.wait_for(lambda: sup.popen.call_count == 1))
            first = sup.thread
            sup.stop()
            sup.start()
            self.assertTrue(self.wait_for(lambda: sup.popen.call_count == 2))
            first.join(2)
            self.assertFalse(first.is_alive())                               # the old loop ended, it did not respawn
            time.sleep(0.1)
            self.assertEqual(sup.popen.call_count, 2)
            sup.stop()

    def test_a_slow_boot_reports_what_is_missing(self):
        with tempfile.TemporaryDirectory() as data, \
                mock.patch.object(music_cast, "missing", return_value=["Node.js 20.10+"]), \
                mock.patch.object(music_cast.threading, "Thread") as thread:
            sup = music_cast.Supervisor(data, popen=mock.Mock())
            self.assertFalse(sup.start())
            thread.assert_called_once()                                      # the 30 s re-check
            self.assertEqual(thread.call_args.kwargs["target"], sup._retry_start)
            self.assertEqual(sup.status()["state"], "missing")
            self.assertIn("Node.js", music_cast.describe(sup.status()))

    def test_server_action(self):
        src = (Path(music_cast.__file__).parent / "server.py").read_text()
        self.assertIn('"music_cast_service"', src)
        self.assertIn("MUSIC.switch_off()", src)


class Wiring(unittest.TestCase):
    def test_server_starts_it_and_handles_the_tv_toggle(self):
        src = (Path(music_cast.__file__).parent / "server.py").read_text()
        self.assertIn("MUSIC = music_cast.Supervisor(DATA)", src)
        self.assertIn("MUSIC.start()", src)
        self.assertIn('action == "music_toggle"', src)
        self.assertIn("music_cast.pause(DATA)", src)

    def test_receiver_plays_sound_only(self):
        js = (music_cast.APP_DIR / "receiver.mjs").read_text()
        self.assertIn("'--no-video'", js)
        self.assertIn("--ytdl-format=bestaudio/best", js)
        lock = json.loads((music_cast.APP_DIR / "package-lock.json").read_text())
        self.assertEqual(lock["packages"]["node_modules/yt-cast-receiver"]["version"], "2.1.0")



class TileLabel(unittest.TestCase):
    def test_cast_songs_say_music_and_youtube_stays_youtube(self):
        try:
            from widgets import network
        except Exception:
            self.skipTest("GTK bindings unavailable")
        self.assertEqual(network.now_kicker("Playing", "cast"), "MUSIC · PLAYING")
        self.assertEqual(network.now_kicker("Paused", "youtube"), "YOUTUBE · PAUSED")
        self.assertEqual(network.now_kicker(None), "YOUTUBE · OPEN")



class ReceiverPlayer(unittest.TestCase):
    def test_player_against_a_stand_in_mpv(self):
        """Runs music_cast/dev/test_player.mjs when the receiver's packages are installed (npm ci)."""
        import subprocess
        node = music_cast.node_bin()
        if not node or not (music_cast.APP_DIR / "node_modules/yt-cast-receiver").is_dir():
            self.skipTest("Node 18+ and music_cast/node_modules needed")
        out = subprocess.run([node, str(music_cast.APP_DIR / "dev/test_player.mjs")], capture_output=True,
                             text=True, timeout=60)
        self.assertIn("ALL PLAYER CHECKS PASSED", out.stdout, out.stdout + out.stderr)


class Discoverable(unittest.TestCase):
    """Settings -> Playback -> Check casting / Link with TV code, and `nexus-tv-cast check|code`."""
    def setUp(self):
        self.dir = Path(tempfile.mkdtemp(dir="/tmp"))
        self.running = {"enabled": True, "state": "running", "missing": [], "port": 8766}

    def tearDown(self):
        import shutil
        shutil.rmtree(self.dir, ignore_errors=True)

    def net(self, addresses):
        (self.dir / music_cast.NET_FILE).write_text(json.dumps(
            {"port": 8766, "ifaces": ["wlp2s0"], "addresses": [{"iface": "wlp2s0", "address": a} for a in addresses]}))

    def test_codes_read_in_threes(self):
        self.assertEqual(music_cast.format_code("324419547823"), "324 419 547 823")
        self.assertEqual(music_cast.format_code(None), "")

    def test_a_working_receiver_checks_out(self):
        self.net(["192.168.1.40"])
        lines = music_cast.check(self.dir, self.running, etc=str(self.dir),
                                 search=lambda a: ["http://%s:8766/ytcr/ssdp/device-desc.xml" % a],
                                 page=lambda a, p: True)
        self.assertEqual(lines[1], (True, "Discoverable on 192.168.1.40 (wlp2s0)"))
        self.assertFalse(any(ok is False for ok, _ in lines))
        self.assertIn("Link with TV code", lines[-1][1])

    def test_unanswered_search_and_firewall_are_named(self):
        self.net(["192.168.1.40"])
        (self.dir / "ufw").mkdir()
        (self.dir / "ufw/ufw.conf").write_text("ENABLED=yes\nLOGLEVEL=low\n")
        lines = music_cast.check(self.dir, self.running, etc=str(self.dir),
                                 search=lambda a: ["http://172.17.0.1:8766/ytcr/ssdp/device-desc.xml"],
                                 page=lambda a, p: True)
        self.assertEqual(lines[1][0], False)
        self.assertIn("doesn't answer searches", lines[1][1])
        self.assertTrue(any("sudo ufw allow 1900/udp" in t for _, t in lines))

    def test_no_wifi_address(self):
        self.net([])
        lines = music_cast.check(self.dir, self.running, search=lambda a: [], page=lambda a, p: False)
        self.assertEqual(lines[-1][0], False)
        self.assertIn("Wi-Fi", lines[-1][1])

    def test_off_or_missing_stops_early_with_advice(self):
        lines = music_cast.check(self.dir, {"enabled": False, "state": "off"})
        self.assertEqual([ok for ok, _ in lines], [False, None])
        lines = music_cast.check(self.dir, {"state": "missing", "missing": ["mpv"]})
        self.assertIn("install.sh", lines[-1][1])

    def test_pair_code_from_the_receiver(self):
        srv = socket.socket(socket.AF_UNIX)
        srv.bind(str(self.dir / music_cast.CTL_NAME))
        srv.listen()
        answers = [{"ok": True, "data": {"code": None, "pending": True}},
                   {"ok": True, "data": {"code": "111222333444", "pending": False}}]

        def serve():
            for answer in answers:
                c, _ = srv.accept()
                with c:
                    self.assertEqual(json.loads(c.recv(4096))["cmd"], "pair_code")
                    c.sendall(json.dumps(answer).encode() + b"\n")
        threading.Thread(target=serve, daemon=True).start()
        self.assertEqual(music_cast.pair_code(self.dir), {"code": None, "pending": True, "error": None})
        self.assertEqual(music_cast.pair_code(self.dir)["code"], "111 222 333 444")
        srv.close()
        self.assertIsNone(music_cast.pair_code(self.dir))                 # no receiver

    def test_search_finds_a_local_responder(self):
        """ssdp_search really talks SSDP: a stand-in device on 127.0.0.1 answers the M-SEARCH."""
        dev = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        dev.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            dev.bind(("", 1900))
            dev.setsockopt(socket.IPPROTO_IP, socket.IP_ADD_MEMBERSHIP,
                           socket.inet_aton("239.255.255.250") + socket.inet_aton("127.0.0.1"))
        except OSError as exc:
            dev.close()
            self.skipTest("no multicast on lo here: %s" % exc)

        def answer():
            dev.settimeout(3)
            try:
                while True:
                    msg, frm = dev.recvfrom(4096)
                    if music_cast.DIAL_ST.encode() in msg:
                        dev.sendto(b"HTTP/1.1 200 OK\r\nLOCATION: http://127.0.0.1:8766/ytcr/ssdp/device-desc.xml\r\n\r\n", frm)
                        return
            except OSError:
                pass
        threading.Thread(target=answer, daemon=True).start()
        try:
            found = music_cast.ssdp_search("127.0.0.1", timeout=1.5)
        finally:
            dev.close()
        if not found:
            self.skipTest("multicast loopback on lo not delivered here")
        self.assertIn("http://127.0.0.1:8766/ytcr/ssdp/device-desc.xml", found)

    def test_server_and_settings_offer_it(self):
        src = (Path(music_cast.__file__).parent / "server.py").read_text()
        self.assertIn('"check", "pair_code"', src)
        settings = (Path(music_cast.__file__).parent / "widgets/settings.py").read_text()
        self.assertIn("YouTube TV code", settings)
        self.assertIn("_code_tick", settings)
        self.assertIn("'Check casting'", settings)
        js = (music_cast.APP_DIR / "receiver.mjs").read_text()
        self.assertIn("bindToInterfaces", js)
        self.assertIn("patchSsdp", js)

    def test_lan_rules_in_node(self):
        import subprocess
        node = music_cast.node_bin()
        if not node:
            self.skipTest("Node 20.10+ needed")
        out = subprocess.run([node, str(music_cast.APP_DIR / "dev/test_lan.mjs")], capture_output=True,
                             text=True, timeout=60)
        self.assertIn("ALL LAN CHECKS PASSED", out.stdout, out.stdout + out.stderr)


if __name__ == "__main__":
    unittest.main()
