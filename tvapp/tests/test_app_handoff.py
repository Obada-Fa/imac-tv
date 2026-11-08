"""The Nexus TV phone app: /api/hello finds TVs, and an Android phone that scans the QR is sent to the app."""
import json
import threading
import unittest
import urllib.request
from http.server import ThreadingHTTPServer
from unittest import mock

from test_torbox import TorboxCase, server

ANDROID = "Mozilla/5.0 (Linux; Android 14; Pixel 8) AppleWebKit/537.36 Chrome/130.0 Mobile Safari/537.36"
IPHONE = "Mozilla/5.0 (iPhone; CPU iPhone OS 18_0 like Mac OS X) AppleWebKit/605.1.15 Mobile/15E148"


class AppHandoffTests(TorboxCase):
    def setUp(self):
        super().setUp()
        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), server.Handler)
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()
        self.addCleanup(self.httpd.server_close)
        self.addCleanup(self.httpd.shutdown)
        self.base = f"http://127.0.0.1:{self.httpd.server_port}"
        # An unpaired phone: the page itself shows the pairing screen.
        auth = mock.patch.object(server.Handler, "authorised", return_value=False)
        auth.start()
        self.addCleanup(auth.stop)

    def get(self, path, ua=ANDROID):
        req = urllib.request.Request(self.base + path, headers={"User-Agent": ua})
        opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor())
        with opener.open(req, timeout=5) as r:
            return r.status, r.read().decode()

    def test_hello_needs_no_pairing(self):
        with mock.patch.object(server.socket, "gethostname", return_value="zorin-laptop.local"):
            status, body = self.get("/api/hello")
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(body), {"app": "nexus-tv", "name": "Zorin laptop"})

    def test_android_qr_scan_opens_the_app_with_a_browser_fallback(self):
        _, body = self.get("/?pair=123456")
        port = self.httpd.server_port
        self.assertIn(f"intent://pair?host=127.0.0.1:{port}&amp;code=123456#Intent;scheme=nexustv;"
                      "package=com.nexus.tv;", body)
        self.assertIn(f"S.browser_fallback_url=http%3A%2F%2F127.0.0.1%3A{port}%2F%3Fpair%3D123456%26web%3D1;end",
                      body)
        self.assertIn(f'href="http://127.0.0.1:{port}/?pair=123456&amp;web=1"', body)

    def test_browser_fallback_app_webview_and_iphone_get_the_remote(self):
        for path, ua in (("/?pair=123456&web=1", ANDROID), ("/?pair=123456&app=1", ANDROID),
                         ("/?pair=123456", IPHONE), ("/", ANDROID), ("/?pair=12x", ANDROID)):
            _, body = self.get(path, ua)
            self.assertNotIn("intent://", body, path + " " + ua[:20])
            self.assertIn("Nexus TV Remote", body)

    def test_a_strange_host_header_is_not_put_in_the_page(self):
        req = urllib.request.Request(self.base + "/?pair=123456",
                                     headers={"User-Agent": ANDROID, "Host": 'evil"><script>'})
        with urllib.request.urlopen(req, timeout=5) as r:
            self.assertNotIn("intent://", r.read().decode())


if __name__ == "__main__":
    unittest.main()


class PairStatusTests(TorboxCase):
    """/api/pair-status gives the code only to this PC itself and to paired phones."""

    def setUp(self):
        super().setUp()
        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), server.Handler)
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()
        self.addCleanup(self.httpd.server_close)
        self.addCleanup(self.httpd.shutdown)
        self.base = f"http://127.0.0.1:{self.httpd.server_port}"
        control = mock.patch.object(server, "CONTROL", mock.Mock(renew_pairing=lambda: "123456"))
        control.start()
        self.addCleanup(control.stop)

    def status(self, client, paired=False):
        real = server.Handler.__init__

        def init(handler, request, address, srv):
            real(handler, request, (client, address[1]), srv)
        with mock.patch.object(server.Handler, "__init__", init), \
                mock.patch.object(server.Handler, "authorised", return_value=paired):
            with urllib.request.urlopen(self.base + "/api/pair-status", timeout=5) as r:
                return json.loads(r.read())

    def test_this_pc_gets_the_code(self):
        answer = self.status("127.0.0.1")
        self.assertEqual(answer, {"paired": False, "code": "123456"})

    def test_an_unpaired_phone_does_not(self):
        for client in ("192.168.1.30", "100.101.5.7"):
            self.assertEqual(self.status(client), {"paired": False}, client)

    def test_a_paired_phone_does(self):
        self.assertEqual(self.status("192.168.1.30", paired=True), {"paired": True, "code": "123456"})
