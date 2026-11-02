import json
from pathlib import Path
import sys
import tempfile
import time
from types import SimpleNamespace
import unittest
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import home
import server
from widgets import volume_hud


class FillTests(unittest.TestCase):
    def test_fill_follows_level_and_mute_empties_it(self):
        self.assertEqual(volume_hud.filled(45, False), 0.45)
        self.assertEqual(volume_hud.filled(45, True), 0.0)
        self.assertEqual(volume_hud.filled(140, False), 1.0)


class IslandMotionTests(unittest.TestCase):
    def test_island_width_follows_the_level(self):
        for percent, muted, share in ((70, True, 0.2), (0, False, 0.2), (20, False, 0.36), (100, False, 1.0)):
            self.assertAlmostEqual(volume_hud.island_width(volume_hud.filled(percent, muted)), share)

    def test_spring_overshoots_then_settles_open(self):
        sizes = [volume_hud.opening(0.0, n / 30) for n in range(16)]
        self.assertEqual(sizes[0], 0.0)
        self.assertGreater(max(sizes), 1.0)
        self.assertLess(max(sizes), 1.1)
        self.assertEqual(volume_hud.opening(0.0, volume_hud.OPEN_S), 1.0)

    def test_closing_reaches_the_edge_from_any_size(self):
        self.assertEqual(volume_hud.closing(0.7, 0), 0.7)
        self.assertEqual(volume_hud.closing(0.7, volume_hud.CLOSE_S), 0.0)


class RoutingTests(unittest.TestCase):
    def shell(self, screen):
        return SimpleNamespace(current=lambda: screen, volume_pill=mock.Mock(), volume_overlay=None)

    def test_ambient_home_gets_only_the_hairline(self):
        screen = SimpleNamespace(volume_line=mock.Mock(), idle=SimpleNamespace(ambient=True))
        shell = self.shell(screen)
        home.Home.show_volume(shell, 60, False)
        screen.volume_line.show_level.assert_called_once_with(60, False)
        shell.volume_pill.show_level.assert_not_called()

    def test_awake_home_and_settings_get_the_pill(self):
        awake = SimpleNamespace(volume_line=mock.Mock(), idle=SimpleNamespace(ambient=False))
        for screen in (awake, SimpleNamespace()):
            shell = self.shell(screen)
            home.Home.show_volume(shell, 60, True)
            shell.volume_pill.show_level.assert_called_once_with(60, True)
        awake.volume_line.show_level.assert_not_called()


class OverAppTests(unittest.TestCase):
    def test_over_an_app_the_island_gets_its_own_window(self):
        screen = SimpleNamespace(volume_line=mock.Mock(), idle=SimpleNamespace(ambient=True))
        shell = SimpleNamespace(current=lambda: screen, volume_pill=mock.Mock(), volume_overlay=mock.Mock())
        home.Home.show_volume(shell, 60, False, True)
        shell.volume_overlay.show_level.assert_called_once_with(60, False)
        screen.volume_line.show_level.assert_not_called()
        shell.volume_pill.show_level.assert_not_called()

    def test_back_on_home_the_overlay_goes_away(self):
        shell = SimpleNamespace(current=lambda: SimpleNamespace(), volume_pill=mock.Mock(), volume_overlay=mock.Mock())
        home.Home.show_volume(shell, 60, False)
        shell.volume_overlay.dismiss.assert_called_once()
        shell.volume_pill.show_level.assert_called_once_with(60, False)


class YoutubeLockTests(unittest.TestCase):
    def test_flag_is_added_once_and_keeps_other_flags(self):
        with tempfile.TemporaryDirectory() as folder:
            config = Path(folder, ".config/VacuumTube")
            config.mkdir(parents=True)
            (config / "flags.txt").write_text("--disable-gpu\n")
            server.youtube_volume.ensure_flag(folder)
            server.youtube_volume.ensure_flag(folder)
            self.assertEqual((config / "flags.txt").read_text(), "--disable-gpu " + server.youtube_volume.FLAG + "\n")
            self.assertFalse(Path(folder, ".var").exists())     # an install that isn't there stays untouched

    def test_running_youtube_without_the_port_is_replaced(self):
        controller = SimpleNamespace()
        with mock.patch.object(server.youtube_volume, "reachable", return_value=False), \
                mock.patch.object(server, "command") as command, mock.patch.object(server.time, "sleep"), \
                mock.patch.object(server, "window_for", return_value=None):
            self.assertIsNone(server.Controller._replace_unlocked_youtube(controller, {"id": "0x1"}))
        command.assert_called_once()

    def test_running_youtube_with_the_port_is_left_alone(self):
        with mock.patch.object(server.youtube_volume, "reachable", return_value=True), \
                mock.patch.object(server, "command") as command:
            self.assertEqual(server.Controller._replace_unlocked_youtube(SimpleNamespace(), {"id": "0x1"}), {"id": "0x1"})
        command.assert_not_called()


class EventTests(unittest.TestCase):
    def test_server_writes_the_level_for_home(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder, "volume_event.json")
            controller = SimpleNamespace(volume_path=path, _save_json=server.Controller._save_json, app="home")
            server.Controller._volume_osd(controller, 35, True)
            event = json.loads(path.read_text())
            self.assertEqual((event["percent"], event["muted"], event["over"]), (35, True, False))
            controller.app = "youtube"
            server.Controller._volume_osd(controller, 35, False)
            self.assertTrue(json.loads(path.read_text())["over"])

    def test_home_ignores_an_old_or_repeated_event(self):
        with tempfile.TemporaryDirectory() as folder, mock.patch.object(home, "DATA", Path(folder)):
            path = Path(folder, "volume_event.json")
            shell = SimpleNamespace(volume_seen=0.0, show_volume=mock.Mock())
            path.write_text(json.dumps({"percent": 20, "muted": False, "ts": time.time() - 60}))
            home.Home.volume_file_changed(shell)
            shell.show_volume.assert_not_called()
            path.write_text(json.dumps({"percent": 20, "muted": False, "ts": time.time()}))
            home.Home.volume_file_changed(shell)
            home.Home.volume_file_changed(shell)
            shell.show_volume.assert_called_once_with(20, False, False)


if __name__ == "__main__":
    unittest.main()
