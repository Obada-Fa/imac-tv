#!/usr/bin/env python3
"""Serve the real phone UI with sample state for documentation screenshots.

python3 scripts/phone-preview.py --port 8876
Loopback only. No controller imports, real credentials, settings writes or TV actions.
Capture the browser at 390 × 844, then stop this process when done.
"""
import argparse
import json
import math
import time
from pathlib import Path
import sys
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlsplit

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tvapp"))
import settings_model
import twitch
import widgets
import wallpapers
from media.config import DEFAULTS


SAMPLE_APPS = ["Brave", "RustDesk", "Firefox", "Files", "Terminal", "VLC media player", "Spotify", "Steam", "LibreOffice Writer",
               "Settings", "Calculator", "Text Editor", "OBS Studio", "GIMP", "Kodi",
               "An application with a really quite remarkably long name that cannot possibly fit on one line"]


def sample_apps(count):
    # Stable TV shortcuts plus inert sample PC entries for the legacy full-list view.
    items = [
        {"id": "movies", "name": "Movies", "kind": "screen", "builtin_kind": "movies"},
        {"id": "series", "name": "Series", "kind": "screen", "builtin_kind": "series"},
        {"id": "youtube", "name": "YouTube", "kind": "app", "builtin_kind": "youtube"},
        {"id": "twitch", "name": "Twitch", "kind": "screen", "builtin_kind": "twitch"},
        {"id": "desktop:brave-browser", "name": "Web Browser", "kind": "desktop", "builtin_kind": None,
         "description": "Browse the web with Brave"},
        {"id": "desktop:org.rustdesk.RustDesk", "name": "Remote Desktop", "kind": "desktop", "builtin_kind": None,
         "description": "Connect with RustDesk"},
        {"id": "calculator", "name": "Calculator", "kind": "screen", "builtin_kind": "calculator",
         "description": "Calculate using your remote"},
    ]
    for i in range(count):
        base = SAMPLE_APPS[i % len(SAMPLE_APPS)]
        name = base if i < len(SAMPLE_APPS) else f"{base} {i}"
        slug = f"{base.lower().replace(' ', '-')}-{i}"
        items.append({"id": f"desktop:{slug}", "name": name, "kind": "desktop", "builtin_kind": None})
    defaults = {"movies", "series", "youtube", "twitch"}
    for i, app in enumerate(items):
        app["favorite"] = app["id"] in defaults
        app["icon"] = None if i % 3 == 2 else "/api/apps/icon?id=" + app["id"].replace(":", "%3A")
    return items


class Preview(SimpleHTTPRequestHandler):
    native_bridge = None
    apps = []
    max_favorites = 6
    apps_rev = 1
    desktop_app = None   # id of a "running" desktop app, for the touchpad view
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
        elif path == "/api/apps":
            scope = (parse_qs(urlsplit(self.path).query).get("scope") or [""])[0]
            if scope not in ("", "home", "others", "shortcuts"):
                self.json({"error": "Unknown apps scope"}, 400)
                return
            self.json(self.apps_payload(scope))
        elif path == "/api/apps/icon":
            ident = (parse_qs(urlsplit(self.path).query).get("id") or [""])[0]
            if ident.startswith("desktop:") and hash(ident) % 7 == 0:
                self.json({"error": "no icon"}, 404)   # a missing icon file: the sheet shows a letter instead
                return
            hue = sum(map(ord, ident)) * 47 % 360   # a stand-in icon: a coloured disc per app
            body = (f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 48 48"><circle cx="24" cy="24" r="22" fill="hsl({hue},55%,48%)"/>'
                    f'<circle cx="24" cy="24" r="9" fill="hsl({hue},70%,86%)"/></svg>').encode()
            self.send_response(200)
            self.send_header("Content-Type", "image/svg+xml")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        elif path == "/api/state":
            extra = {}
            if self.desktop_app:
                extra = {"app": self.desktop_app, "app_name": next((a["name"] for a in self.apps if a["id"] == self.desktop_app), "App")}
            self.json({**{"apps_rev": self.apps_rev}, "app": "home", "phase": "ready", "gui": True, "playback": {"active": False},
                       "volume": 45, "muted": False, "brightness": {"level": 80, "available": True},
                       "fullscreen": True, "screen_off": False, "timer": {"active": False},
                       "torbox": {"configured": True}, "remote": {"connected": True, "count": 1},
                       "updates": {"status": "up_to_date", "message": "Your TV is up to date."}, **extra})
        elif path == "/api/system":
            gb = 1024 ** 3
            wave = math.sin(time.time() / 3)   # the numbers drift so live updates show
            self.json({"cpu": round(23.4 + 15 * wave, 1), "cores": 4, "load": [0.82, 0.71, 0.6], "uptime": 93784,
                       "memory": {"total": 8 * gb, "used": int(5.6 * gb), "swap_total": 2 * gb, "swap_used": gb // 4},
                       "disk": {"total": 240 * gb, "used": 131 * gb}, "network": {"rx": int(2_400_000 * (1.2 + wave)), "tx": 180_000},
                       "battery": None, "temperature": round(64 + 6 * wave, 1), "gpu_temperature": 58.0,
                       "top_cpu": [{"pid": 1, "name": "Discord", "cpu": 12.5, "memory": 610_000_000},
                                   {"pid": 2, "name": "python3", "cpu": 4.1, "memory": 120_000_000},
                                   {"pid": 3, "name": "Xorg", "cpu": 3.0, "memory": 90_000_000}],
                       "top_memory": [],
                       "apps": [{"key": "discord", "title": "Discord", "running": True, "pids": [1], "memory": 610_000_000, "cpu": 12.5},
                                {"key": "youtube", "title": "YouTube", "running": False, "pids": [], "memory": 0, "cpu": 0.0},
                                {"key": "player", "title": "Video player", "running": False, "pids": [], "memory": 0, "cpu": 0.0}]})
        elif path == "/api/settings":
            self.json({"outputs": [], "trial": {"active": False}, "display_error": "",
                       "torbox": {"configured": True}, "fields": settings_model.FIELDS,
                       "values": settings_model.values(widgets.parse_config({}), DEFAULTS),
                       "background": {"source": widgets.DEFAULT_CONFIG["background"],
                                      "wanted": widgets.DEFAULT_CONFIG["background"], "retro": []},
                       "youtube_close_after_paused_min": 0})
        elif path in {f"/api/themes/{key}.jpg" for key in wallpapers.BUNDLED}:
            source = Path(path).stem
            body = Path(wallpapers.pictures(source)[0]["path"]).read_bytes()
            self.send_response(200)
            self.send_header("Content-Type", "image/jpeg")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        elif path == "/api/themes/focus.jpg":
            body = (ROOT / "tvapp/assets/preview-stremio-space.png").read_bytes()
            self.send_response(200)
            self.send_header("Content-Type", "image/png")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        elif path == "/api/twitch":
            q = (parse_qs(urlsplit(self.path).query).get("q") or [""])[0].lower()
            self.json({"streams": [s for s in twitch.FAKE_STREAMS
                                   if not q or q in s["login"] or q in s["name"].lower() or q in s["title"].lower()]})
        elif path.startswith("/api/"):
            self.json({"error": "This preview has no TV connection."}, 404)
        else:
            super().do_GET()

    def apps_payload(self, scope=""):
        favorites = [a for a in self.apps if a["favorite"]]
        utility_ids = ["desktop:brave-browser", "desktop:org.rustdesk.RustDesk", "calculator"]
        default_ids = ["movies", "series", "youtube", "twitch"]
        if scope == "home":
            items = favorites
        elif scope == "others":
            ids = [ident for ident in utility_ids if any(a["id"] == ident and not a["favorite"] for a in self.apps)]
            by_id = {a["id"]: a for a in self.apps}
            items = [by_id[ident] for ident in ids]
        elif scope == "shortcuts":
            ordered_ids = [a["id"] for a in favorites]
            ordered_ids.extend(ident for ident in default_ids + utility_ids if ident not in ordered_ids)
            by_id = {a["id"]: a for a in self.apps}
            items = [by_id[ident] for ident in ordered_ids if ident in by_id]
        else:  # preserve the older full installed-app list for compatibility checks
            items = self.apps
        if scope == "":
            items = sorted(items, key=lambda a: (not a["favorite"], a["name"].lower() if not a["favorite"] else ""))
        return {"apps": items,
                "max_favorites": self.max_favorites}

    def do_POST(self):
        # Only the apps commands act, on in-memory sample data; everything else stays disabled
        if urlsplit(self.path).path == "/api/commands":
            try:
                req = json.loads(self.rfile.read(int(self.headers.get("Content-Length") or 0)) or b"{}")
            except ValueError:
                req = {}
            action, params = req.get("action"), req.get("params") or {}
            if action == "app_favorite":
                app = next((a for a in self.apps if a["id"] == params.get("id")), None)
                if not app:
                    return self.json({"error": "Unknown app"}, 400)
                if params.get("favorite") and not app["favorite"] and sum(a["favorite"] for a in self.apps) >= self.max_favorites:
                    return self.json({"error": "Home is full: remove a favorite first"}, 400)
                app["favorite"] = bool(params.get("favorite"))
                type(self).apps_rev += 1
                return self.json(self.apps_payload())
            if action == "app_open":
                app = next((a for a in self.apps if a["id"] == params.get("id")), None)
                if not app:
                    return self.json({"error": "Unknown app"}, 400)
                return self.json({"app": app["id"], "phase": "opening"})
        self.json({"error": "Documentation preview: TV commands and settings changes are disabled."}, 403)


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--port", type=int, default=8876)
    ap.add_argument("--native-bridge", type=Path, help="Preview the Flutter app's bridge with inert native controls")
    ap.add_argument("--apps", type=int, default=12, help="How many sample PC apps the Apps sheet lists")
    ap.add_argument("--max-favorites", type=int, default=6)
    ap.add_argument("--desktop-app", action="store_true", help="Pretend a desktop app is selected on the TV (touchpad view)")
    args = ap.parse_args()
    Preview.apps = sample_apps(args.apps)
    Preview.max_favorites = args.max_favorites
    if args.desktop_app:
        Preview.desktop_app = next(a["id"] for a in Preview.apps if a["kind"] == "desktop")
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
