import importlib.util
import json
import logging
import os
from pathlib import Path
import stat
import sys
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
_boot = tempfile.TemporaryDirectory(prefix="imactv-torbox-boot-")
os.environ.setdefault("IMACTV_DATA_DIR", _boot.name)
spec = importlib.util.spec_from_file_location("imactv_server_tb", Path(__file__).resolve().parents[1] / "server.py")
server = importlib.util.module_from_spec(spec)
spec.loader.exec_module(server)
from media import addons, config  # noqa: E402

KEY = "tbk-0123456789abcdefABCDEF"


class FakeResponse:
    def __init__(self, body):
        self.body = json.dumps(body).encode()

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def read(self, n=-1):
        return self.body


def http_error(code):
    return urllib.error.HTTPError(server.TORBOX_ME, code, "no", {}, None)


class TorboxCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="imactv-torbox-")
        self.addCleanup(self.tmp.cleanup)
        patcher = mock.patch.dict(os.environ, {"IMACTV_DATA_DIR": self.tmp.name})
        patcher.start()
        self.addCleanup(patcher.stop)
        self.data = Path(self.tmp.name)
        with mock.patch.object(server.threading, "Thread"):
            self.c = server.Controller()

    def good(self):
        return mock.patch.object(server.urllib.request, "urlopen", return_value=FakeResponse({"success": True, "data": {}}))


class KeyActionTests(TorboxCase):
    def test_format_rejection(self):
        for bad in ("", "short", "has space in it 1234567890", "a" * 129, "bad/chars/1234567890123", None, 5):
            self.c.torbox_tries.clear()
            with mock.patch.object(server.urllib.request, "urlopen") as opener:
                with self.assertRaisesRegex(ValueError, "doesn't look like a TorBox API key"):
                    self.c.action("torbox_key", {"key": bad})
                opener.assert_not_called()
        self.assertFalse((self.data / "torbox.key").exists())

    def test_rejected_key_is_not_stored(self):
        for failure in (http_error(401), http_error(403)):
            with mock.patch.object(server.urllib.request, "urlopen", side_effect=failure):
                with self.assertRaisesRegex(ValueError, "TorBox rejected this key"):
                    self.c.action("torbox_key", {"key": KEY})
        with mock.patch.object(server.urllib.request, "urlopen", return_value=FakeResponse({"success": False})):
            with self.assertRaisesRegex(ValueError, "TorBox rejected this key"):
                self.c.action("torbox_key", {"key": KEY})
        self.assertFalse((self.data / "torbox.key").exists())

    def test_network_error(self):
        with mock.patch.object(server.urllib.request, "urlopen", side_effect=urllib.error.URLError("down")):
            with self.assertRaisesRegex(RuntimeError, "Couldn't reach TorBox"):
                self.c.action("torbox_key", {"key": KEY})
        with mock.patch.object(server.urllib.request, "urlopen", side_effect=http_error(500)):
            with self.assertRaisesRegex(RuntimeError, "Couldn't reach TorBox"):
                self.c.action("torbox_key", {"key": KEY})

    def test_success_writes_private_file_and_clears_cache(self):
        cache = self.data / "cache" / "addons"
        cache.mkdir(parents=True)
        (cache / "x.json").write_text("{}")
        with self.good() as opener:
            out = self.c.action("torbox_key", {"key": "  " + KEY + " "})
        self.assertEqual(out, {"configured": True})
        request = opener.call_args.args[0]
        self.assertEqual(request.full_url, "https://api.torbox.app/v1/api/user/me")
        self.assertEqual(request.get_header("Authorization"), "Bearer " + KEY)
        self.assertEqual(opener.call_args.kwargs["timeout"], 8)
        path = self.data / "torbox.key"
        self.assertEqual(path.read_text().strip(), KEY)
        self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o600)
        self.assertFalse((self.data / "torbox.key.tmp").exists())
        self.assertFalse(cache.exists())
        self.assertIn("torbox=" + KEY, config.load()["addons"]["streams"][0])

    def test_status_never_contains_key(self):
        with self.good():
            self.c.action("torbox_key", {"key": KEY})
        with mock.patch.object(server, "mpv", side_effect=OSError), mock.patch.object(server, "command", return_value=""), \
                mock.patch.object(server, "playback", return_value={"active": False}), mock.patch.object(server, "brightness", return_value=None):
            state = self.c.status()
        self.assertEqual(state["torbox"], {"configured": True})
        self.assertNotIn(KEY, json.dumps(state))

    def test_rate_limit(self):
        with mock.patch.object(server.urllib.request, "urlopen", side_effect=http_error(401)):
            for _ in range(5):
                with self.assertRaisesRegex(ValueError, "rejected"):
                    self.c.action("torbox_key", {"key": KEY})
            with self.assertRaisesRegex(RuntimeError, "Too many tries, wait a few minutes"):
                self.c.action("torbox_key", {"key": KEY})
        with mock.patch.object(server.time, "monotonic", return_value=server.time.monotonic() + 601):
            with self.good():
                self.assertTrue(self.c.action("torbox_key", {"key": KEY})["configured"])

    def test_remove(self):
        with self.good():
            self.c.action("torbox_key", {"key": KEY})
        (self.data / "media.json").write_text(json.dumps({"torbox_key": "legacy-key-0123456789", "max_size_gb": 5}))
        cache = self.data / "cache" / "addons"
        cache.mkdir(parents=True)
        self.assertEqual(self.c.action("torbox_remove", {}), {"configured": False})
        self.assertFalse((self.data / "torbox.key").exists())
        self.assertFalse(cache.exists())
        self.assertEqual(json.loads((self.data / "media.json").read_text()), {"max_size_gb": 5})
        self.assertEqual(config.read_key(), "")


class SourcesTests(TorboxCase):
    def raw(self):
        return [{"name": "[TB+] Torrentio\n1080p", "title": "M 1080p x264\n\U0001f4be 2 GB", "url": f"https://torrentio.strem.fun/resolve/torbox/{KEY}/abc/0"},
                {"name": "Torrentio\n720p", "title": "B 720p\n\U0001f464 9 \U0001f4be 1 GB", "infoHash": "2" * 40}]

    def test_sources_hide_url_and_resolve_ref(self):
        with mock.patch.object(server.addons, "streams", return_value=self.raw()):
            out = self.c.action("sources", {"type": "movie", "id": "tt1"})
        self.assertNotIn(KEY, json.dumps(out))
        self.assertTrue(all("url" not in s for s in out["sources"]))
        direct = [s for s in out["sources"] if "ref" in s]
        self.assertEqual(len(direct), 1)
        stream = self.c._resolve_stream({"ref": direct[0]["ref"]})
        self.assertIn(KEY, stream["url"])

    def test_bad_or_expired_ref_and_phone_url(self):
        for bad in ({"ref": 0}, {"ref": -1}, {"ref": True}, {"ref": "0"}):
            with self.assertRaisesRegex(ValueError, "expired"):
                self.c._resolve_stream(bad)
        with self.assertRaises(ValueError):
            self.c._resolve_stream({"url": "https://evil.example/x"})
        with mock.patch.object(server.addons, "streams", return_value=self.raw()):
            self.c.action("sources", {"type": "movie", "id": "tt1"})
        with mock.patch.object(server.time, "monotonic", return_value=server.time.monotonic() + 1900):
            with self.assertRaisesRegex(ValueError, "expired"):
                self.c._resolve_stream({"ref": 0})


class RedactTests(TorboxCase):
    def test_redact(self):
        with self.good():
            self.c.action("torbox_key", {"key": KEY})
        url = f"https://torrentio.strem.fun/resolve/torbox/{KEY}/abc/0"
        for text in (url, f"x {KEY} y", f"/sort=q|torbox={KEY}/manifest", "Authorization: Bearer " + KEY):
            self.assertNotIn(KEY, config.redact(text))
        self.assertEqual(config.redact("plain text"), "plain text")

    def test_addon_error_is_redacted(self):
        url = f"https://torrentio.strem.fun/resolve/torbox/{KEY}/x"
        with mock.patch.object(addons.urllib.request, "urlopen", side_effect=urllib.error.URLError(f"bad {url}")):
            with self.assertRaises(addons.AddonError) as ctx:
                addons._fetch(url, 0)
        self.assertNotIn(KEY, str(ctx.exception))

    def test_play_fail_error_is_redacted(self):
        self.c.generation = 3
        with mock.patch.object(self.c, "_stop_proc"), mock.patch.object(self.c, "_show_home"):
            self.c._play_fail(3, RuntimeError(f"mpv failed on https://x/resolve/torbox/{KEY}/f"))
        self.assertNotIn(KEY, self.c.error)

    def test_log_filter(self):
        record = logging.LogRecord("imactv", logging.INFO, "f", 1, "url %s", (f"https://h/resolve/torbox/{KEY}/f",), None)
        server._RedactFilter().filter(record)
        self.assertNotIn(KEY, record.getMessage())


class ConfigTests(TorboxCase):
    def test_file_beats_media_json(self):
        (self.data / "media.json").write_text(json.dumps({"torbox_key": "oldkey-0123456789"}))
        self.assertEqual(config.read_key(), "oldkey-0123456789")
        config.write_key(KEY)
        self.assertEqual(config.read_key(), KEY)
        streams = config.load()["addons"]["streams"][0]
        self.assertIn("torbox=" + KEY, streams)
        self.assertNotIn("oldkey", streams)

    def test_player_log_is_private(self):
        log = self.data / "mpv.log"
        log.write_text("old")
        log.chmod(0o644)
        os.close(os.open(log, os.O_WRONLY | os.O_CREAT, 0o600))
        os.chmod(log, 0o600)
        self.assertEqual(stat.S_IMODE(log.stat().st_mode), 0o600)


class HttpAuthTests(TorboxCase):
    def test_unpaired_phone_gets_401_and_nothing_runs(self):
        server.CONTROL = self.c
        httpd = ThreadingHTTPServer(("127.0.0.1", 0), server.Handler)
        threading.Thread(target=httpd.serve_forever, daemon=True).start()
        self.addCleanup(httpd.server_close)
        self.addCleanup(httpd.shutdown)
        port = httpd.server_address[1]
        for action in ("torbox_key", "torbox_remove"):
            body = json.dumps({"action": action, "params": {"key": KEY}}).encode()
            req = urllib.request.Request(f"http://127.0.0.1:{port}/api/commands", data=body,
                                         headers={"Content-Type": "application/json"}, method="POST")
            with self.assertRaises(urllib.error.HTTPError) as ctx:
                urllib.request.urlopen(req, timeout=5)
            self.assertEqual(ctx.exception.code, 401)
        self.assertFalse((self.data / "torbox.key").exists())

    def test_command_log_line_has_only_the_action(self):
        import inspect
        src = inspect.getsource(server.Handler.do_POST)
        line = [l for l in src.splitlines() if "Command from" in l][0]
        self.assertIn("self.client_address[0], action)", line)


if __name__ == "__main__":
    unittest.main()
