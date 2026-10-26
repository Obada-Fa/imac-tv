"""Phone Settings tab: /api/settings and the display_* / youtube_close actions (display module mocked)."""
import json
import threading
import unittest
from unittest import mock
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer


from test_torbox import TorboxCase, server, KEY

OUT = {"name": "HDMI-1", "connected": True, "current": {"mode": "1920x1080", "rate": 60.0},
       "modes": [{"mode": "1920x1080", "rates": [60.0, 50.0]}]}
IDLE = {"active": False}
RUNNING = {"active": True, "remaining": 12.0, "output": "HDMI-1", "mode": "1280x720", "rate": 60.0}


def fake_display(**over):
    d = mock.MagicMock()
    d.DisplayError = server.display.DisplayError
    d.list_outputs.return_value = [OUT]
    d.trial_state.return_value = IDLE
    d.start_trial.return_value = RUNNING
    d.confirm_trial.return_value = True
    d.revert_trial.return_value = True
    for k, v in over.items():
        setattr(d, k, v)
    return d


class SettingsTests(TorboxCase):
    def patch(self, d):
        p = mock.patch.object(server, "display", d)
        p.start()
        self.addCleanup(p.stop)
        return d

    def test_appearance_preferences_are_shared_with_phone(self):
        self.patch(fake_display())
        for key in ("transparency", "visual_effects"):
            self.c.settings_action("setting_set", {"key": key, "value": False})
            self.assertFalse(self.c.settings()["values"][key])

    def test_get_lists_outputs_trial_and_youtube(self):
        d = self.patch(fake_display())
        s = self.c.settings()
        self.assertEqual(s["outputs"], [OUT])
        self.assertEqual(s["trial"], IDLE)
        self.assertEqual(s["youtube_close_after_paused_min"], 0)
        d.check_trial.assert_called_once()

    def test_get_survives_xrandr_failure(self):
        d = self.patch(fake_display())
        d.list_outputs.side_effect = server.display.DisplayError("xrandr missing")
        s = self.c.settings()
        self.assertEqual(s["outputs"], [])
        self.assertIn("xrandr missing", s["display_error"])

    def test_torbox_settings_share_the_controller_key_without_exposing_it(self):
        self.patch(fake_display())
        self.assertEqual(self.c.settings()["torbox"], {"configured": False})
        with self.good():
            self.c.action("torbox_key", {"key": KEY})
        saved = self.c.settings()
        self.assertEqual(saved["torbox"], {"configured": True})
        self.assertNotIn(KEY, json.dumps(saved))
        self.c.action("torbox_remove", {})
        self.assertEqual(self.c.settings()["torbox"], {"configured": False})

    def test_start_confirm_revert(self):
        d = self.patch(fake_display())
        r = self.c.action("display_trial", {"output": "HDMI-1", "mode": "1280x720", "rate": 60})
        self.assertEqual(r["trial"], RUNNING)
        d.start_trial.assert_called_once_with("HDMI-1", "1280x720", 60.0)
        self.assertEqual(self.c.action("display_keep", {})["kept"], True)
        self.assertEqual(self.c.action("display_revert", {})["reverted"], True)

    def test_bad_params_and_display_error_mapping(self):
        d = self.patch(fake_display())
        for bad in ({}, {"output": "x", "mode": "1x1"}, {"output": "x", "mode": "1x1", "rate": "60"},
                    {"output": 1, "mode": "1x1", "rate": 60}):
            with self.assertRaises(ValueError):
                self.c.action("display_trial", bad)
        d.start_trial.assert_not_called()
        d.start_trial.side_effect = server.display.DisplayError("refused")
        with self.assertRaisesRegex(RuntimeError, "refused"):
            self.c.action("display_trial", {"output": "HDMI-1", "mode": "1280x720", "rate": 60})

    def test_youtube_close_saved_and_other_keys_kept(self):
        (self.data / "media.json").write_text(json.dumps({"autoplay_next": False}))
        self.assertEqual(self.c.action("youtube_close", {"minutes": 30})["youtube_close_after_paused_min"], 30)
        saved = json.loads((self.data / "media.json").read_text())
        self.assertEqual(saved, {"autoplay_next": False, "youtube_close_after_paused_min": 30})
        self.patch(fake_display())
        self.assertEqual(self.c.settings()["youtube_close_after_paused_min"], 30)
        for bad in (15, "30", None, True, -1):
            with self.assertRaises(ValueError):
                self.c.action("youtube_close", {"minutes": bad})

    def test_settings_actions_do_not_hold_the_action_lock(self):
        self.patch(fake_display())
        with self.c.action_lock:
            seen = []
            t = threading.Thread(target=lambda: seen.append(self.c.action("display_keep", {})))
            t.start(); t.join(3)
            self.assertFalse(t.is_alive())


class SettingsHttpTests(TorboxCase):
    def test_unpaired_401_and_error_json(self):
        server.CONTROL = self.c
        httpd = ThreadingHTTPServer(("127.0.0.1", 0), server.Handler)
        threading.Thread(target=httpd.serve_forever, daemon=True).start()
        self.addCleanup(httpd.server_close)
        self.addCleanup(httpd.shutdown)
        base = f"http://127.0.0.1:{httpd.server_address[1]}"
        with self.assertRaises(urllib.error.HTTPError) as ctx:
            urllib.request.urlopen(base + "/api/settings", timeout=5)
        self.assertEqual(ctx.exception.code, 401)
        d = fake_display()
        d.start_trial.side_effect = server.display.DisplayError("refused")
        with mock.patch.object(server, "display", d), mock.patch.object(server.Handler, "authorised", return_value=True):
            body = json.dumps({"action": "display_trial", "params": {"output": "a", "mode": "1280x720", "rate": 60}}).encode()
            req = urllib.request.Request(base + "/api/commands", data=body, headers={"Content-Type": "application/json"}, method="POST")
            with self.assertRaises(urllib.error.HTTPError) as ctx:
                urllib.request.urlopen(req, timeout=5)
            self.assertEqual(ctx.exception.code, 503)
            self.assertIn("refused", json.loads(ctx.exception.read())["error"])
            got = json.loads(urllib.request.urlopen(base + "/api/settings", timeout=5).read())
            self.assertEqual(got["outputs"], [OUT])


class PreferenceTests(TorboxCase):
    def setUp(self):
        super().setUp()
        patcher = mock.patch.object(server, "DATA", self.data)
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_preserves_values_and_defaults(self):
        home = {'weather': {'place': 'Home', 'lat': 52, 'lon': 5},
                'ambient': {'interval': 180, 'idle': 240, 'idle_music': 60, 'extension': 7},
                'style': 'network', 'node_shape': 'octagon', 'visualizer': True}
        media = {'addons': {'meta': 'https://example.test'}, 'codec_order': ['AV1'], 'cache_bytes': 123,
                 'max_size_gb': 7, 'sub_default': 'ara'}
        (self.data / 'home.json').write_text(json.dumps(home))
        (self.data / 'media.json').write_text(json.dumps(media))
        with mock.patch.object(server, 'display', fake_display()):
            self.assertTrue(self.c.settings()['values']['autoplay_next'])
            self.c.action('setting_set', {'key': 'idle', 'value': 300})
            home['ambient']['idle'] = 300
            self.assertEqual(json.loads((self.data / 'home.json').read_text()), home)
            self.c.action('setting_set', {'key': 'autoplay_next', 'value': False})
            self.assertFalse(self.c.flow.cfg['autoplay_next'])
            media['autoplay_next'] = False
            self.assertEqual(json.loads((self.data / 'media.json').read_text()), media)
            self.c.action('setting_set', {'key': 'style', 'value': 'bubbles'})
            saved = self.c.settings()['values']
            self.assertEqual(saved['node_shape'], 'octagon')
            self.assertTrue(saved['visualizer'])

    def test_rejects_unknown_and_unusable_values_without_writing(self):
        for key, value in [('engine', 'http://example.test'), ('style', 'invented'), ('visualizer', 1),
                           ('idle', True), ('idle', 9), ('idle', float('inf')), ('interval', 12.5),
                           ('still_watching_episodes', 0), ('still_watching_hours', 25),
                           ('sub_default', '../secret'), ('sub_langs', ['eng', 'eng']),
                           ('quality_order', ['1080p']), ('widgets', ['unknown'])]:
            with self.subTest(key=key, value=value), self.assertRaises(ValueError):
                self.c.action('setting_set', {'key': key, 'value': value})
        self.assertFalse((self.data / 'home.json').exists())
        self.assertFalse((self.data / 'media.json').exists())

    def test_preserves_order_and_widget_extensions(self):
        (self.data / 'home.json').write_text(json.dumps({'widgets': ['clock', 'extension']}))
        self.c.action('setting_set', {'key': 'widgets', 'value': ['weather']})
        self.assertEqual(json.loads((self.data / 'home.json').read_text())['widgets'], ['weather', 'extension'])
        order = ['2160p', '1080p', '720p', '480p']
        self.c.action('setting_set', {'key': 'quality_order', 'value': order})
        self.c.action('setting_set', {'key': 'sub_langs', 'value': ['dut', 'ara', 'eng']})
        saved = json.loads((self.data / 'media.json').read_text())
        self.assertEqual(saved['quality_order'], order)
        self.assertEqual(saved['sub_langs'], ['dut', 'ara', 'eng'])


if __name__ == "__main__":
    unittest.main()
