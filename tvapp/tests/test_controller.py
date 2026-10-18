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


if __name__ == "__main__":
    unittest.main()
