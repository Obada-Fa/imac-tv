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


class Wiring(unittest.TestCase):
    def test_server_starts_it_and_handles_the_tv_toggle(self):
        src = (Path(music_cast.__file__).parent / "server.py").read_text()
        self.assertIn("music_cast.Supervisor(DATA).start()", src)
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


if __name__ == "__main__":
    unittest.main()
