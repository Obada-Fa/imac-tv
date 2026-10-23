import importlib.util
import os
from pathlib import Path
import tempfile
import unittest
from unittest import mock

_temp = tempfile.TemporaryDirectory(prefix="imactv-test-")
os.environ["IMACTV_DATA_DIR"] = _temp.name
import nowplaying
from media import player, config

spec = importlib.util.spec_from_file_location("imactv_server_audio", Path(__file__).resolve().parents[1] / "server.py")
server = importlib.util.module_from_spec(spec)
spec.loader.exec_module(server)

SINKS = ("0\talsa_output.pci-0000_00_1b.0.analog-stereo\tPipeWire\ts32le 2ch 48000Hz\tSUSPENDED\n"
         "1\talsa_output.pci-0000_00_03.0.hdmi-stereo\tPipeWire\ts32le 2ch 48000Hz\tIDLE\n")


class AudioTests(unittest.TestCase):
    def test_select_playing(self):
        rows = [("org.mpris.MediaPlayer2.vacuumtube", "VacuumTube", "Playing"),
                ("org.mpris.MediaPlayer2.firefox", "Firefox", "Paused"),
                ("org.mpris.MediaPlayer2.mpv", "mpv", "Playing"),
                ("org.mpris.MediaPlayer2.spotify", "Spotify", "Playing")]
        self.assertEqual(nowplaying.select_playing(rows),
                         ["org.mpris.MediaPlayer2.vacuumtube", "org.mpris.MediaPlayer2.spotify"])
        self.assertEqual(nowplaying.select_playing(rows, exclude=("spotify",)), ["org.mpris.MediaPlayer2.vacuumtube"])

    def test_close_timer(self):
        step = nowplaying.paused_close_step
        self.assertEqual(step(None, "Paused", 100, 0), (None, False))
        self.assertEqual(step(None, "Paused", 100, 5), (100, False))
        self.assertEqual(step(100, "Paused", 399, 5), (100, False))
        self.assertEqual(step(100, "Paused", 400, 5), (100, True))
        self.assertEqual(step(100, "Playing", 500, 5), (None, False))
        self.assertEqual(step(100, None, 500, 5), (None, False))

    def test_hdmi_detection(self):
        self.assertIn("not HDMI", player.audio_warning(SINKS, "alsa_output.pci-0000_00_1b.0.analog-stereo"))
        self.assertIsNone(player.audio_warning(SINKS, "alsa_output.pci-0000_00_03.0.hdmi-stereo"))
        self.assertIsNone(player.audio_warning(SINKS.splitlines()[0], "alsa_output.pci-0000_00_1b.0.analog-stereo"))
        self.assertIsNone(player.audio_warning("", ""))

    def test_volume_helpers(self):
        self.assertEqual(player.sink_volume("Volume: front-left: 42000 /  64% / -11.9 dB"), 64)
        self.assertIsNone(player.sink_volume(""))
        self.assertIn("50%", player.volume_osd(50))
        self.assertEqual(player.volume_osd(50, True), "Muted")
        self.assertIn("--volume=100", player.mpv_argv("u", title="T", subs=[]))

    def test_config_default(self):
        self.assertEqual(config.DEFAULTS["youtube_close_after_paused_min"], 0)

    def test_volume_action_uses_pactl_even_while_mpv_plays(self):
        calls = []

        def fake_command(argv, **kw):
            calls.append(argv)
            if argv[1] == "get-sink-volume":
                return "Volume: front-left: 1 / 50% / -1 dB"
            if argv[1] == "get-sink-mute":
                return "Mute: no"
            return ""
        with mock.patch.object(server.subprocess, "Popen"), mock.patch.object(server.Controller, "_watch"), \
             mock.patch.object(server, "command", side_effect=fake_command), \
             mock.patch.object(server, "mpv") as mpv:
            c = server.Controller()
            self.assertEqual(c.action("volume", {"direction": 1}), {"volume": 55})
            self.assertIn(["pactl", "set-sink-volume", "@DEFAULT_SINK@", "55%"], calls)
            self.assertEqual(mpv.call_args[0][0], "show-text")
            self.assertTrue(all(a[0] != "osd-msg-bar" for a, _ in mpv.call_args_list))
            c.action("mute", {})
            self.assertIn(["pactl", "set-sink-mute", "@DEFAULT_SINK@", "toggle"], calls)
            self.assertEqual(c.status()["volume"], 50)
            self.assertIn("audio_warning", c.status())

    def test_yield_audio_minimizes_without_resume(self):
        with mock.patch.object(server.Controller, "_watch"), mock.patch.object(server.subprocess, "Popen"), \
             mock.patch.object(server.nowplaying, "pause_all_playing", return_value=["x"]) as pause, \
             mock.patch.object(server, "window_for", return_value={"id": "0x1"}), \
             mock.patch.object(server, "minimize") as mini, \
             mock.patch.object(server.nowplaying, "control") as control:
            server.Controller()._yield_audio()
            pause.assert_called_once()
            mini.assert_called_once_with({"id": "0x1"})
            control.assert_not_called()


if __name__ == "__main__":
    unittest.main()
