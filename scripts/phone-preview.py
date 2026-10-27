#!/usr/bin/env python3
"""Serve the real phone UI with sample state for documentation screenshots.

python3 scripts/phone-preview.py --port 8876
Loopback only. No controller imports, real credentials, settings writes or TV actions.
Capture the browser at 390 × 844, then stop this process when done.
"""
import argparse
import json
from pathlib import Path
import sys
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tvapp"))
import settings_model
import widgets
from media.config import DEFAULTS


class Preview(SimpleHTTPRequestHandler):
    native_bridge = None
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=str(ROOT / "tvapp/web"), **kwargs)

    def log_message(self, *args):
        pass

    def json(self, data, status=200):
        body = json.dumps(data).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        path = urlsplit(self.path).path
        if self.native_bridge and path in ("/", "/index.html", "/native-preview.js"):
            if path == "/native-preview.js":
                body = ("window.NexusTv={postMessage(raw){const action=JSON.parse(raw).action;"
                        "if(['status','start','stop'].includes(action))window.NexusNative?.update({supported:true,enabled:action==='start'});}};\n"
                        + self.native_bridge.read_text()).encode()
                content_type = "application/javascript"
            else:
                body = (ROOT / "tvapp/web/index.html").read_text().replace(
                    "</head>", '<script src="/native-preview.js" defer></script></head>').encode()
                content_type = "text/html; charset=utf-8"
            self.send_response(200)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        elif path == "/api/state":
            self.json({"app": "home", "phase": "ready", "gui": True, "playback": {"active": False},
                       "volume": 45, "muted": False, "brightness": {"level": 80, "available": True},
                       "fullscreen": True, "screen_off": False, "timer": {"active": False},
                       "torbox": {"configured": True}, "remote": {"connected": True, "count": 1},
                       "updates": {"status": "up_to_date", "message": "Your TV is up to date."}})
        elif path == "/api/settings":
            self.json({"outputs": [], "trial": {"active": False}, "display_error": "",
                       "torbox": {"configured": True}, "fields": settings_model.FIELDS,
                       "values": settings_model.values(widgets.parse_config({}), DEFAULTS),
                       "background": {"source": "photos", "wanted": "photos", "retro": []},
                       "youtube_close_after_paused_min": 0})
        elif path == "/api/themes/focus.jpg":
            body = (ROOT / "tvapp/assets/preview-stremio-space.png").read_bytes()
            self.send_response(200)
            self.send_header("Content-Type", "image/png")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        elif path.startswith("/api/"):
            self.json({"error": "This preview has no TV connection."}, 404)
        else:
            super().do_GET()

    def do_POST(self):
        self.json({"error": "Documentation preview: TV commands and settings changes are disabled."}, 403)


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--port", type=int, default=8876)
    ap.add_argument("--native-bridge", type=Path, help="Preview the Flutter app's bridge with inert native controls")
    args = ap.parse_args()
    if args.native_bridge:
        Preview.native_bridge = args.native_bridge.resolve(strict=True)
    server = ThreadingHTTPServer(("127.0.0.1", args.port), Preview)
    print(f"Sample phone UI: http://127.0.0.1:{args.port}/", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
