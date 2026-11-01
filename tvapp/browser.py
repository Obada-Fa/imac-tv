"""Brave as a TV app: a kiosk window on its own profile, driven from Home's menu and the phone.

Plain Python (no GTK), so the controller and the TV process can both import it. Chromium refuses a debugging port
on its default profile, so the TV browser has its own (logins and extensions of a normal Brave don't carry over).
The port listens on 127.0.0.1 only. Only http(s) addresses and the fixed PAGES are ever opened."""
import base64
import json
import os
from pathlib import Path
import re
import socket
import struct
import urllib.error
from urllib.parse import quote, urlencode, urlsplit
import urllib.request

PORT = int(os.environ.get("IMACTV_BRAVE_PORT", "9333"))
WM_CLASS = "ImacTvBrave"
MENU_TITLE = "iMac TV Browser"
START_URL = "https://search.brave.com/"
SCALE = "1.5"
SHORTCUTS = {"back": "alt+Left", "forward": "alt+Right", "reload": "ctrl+r", "zoom_in": "ctrl+plus",
             "zoom_out": "ctrl+minus", "zoom_reset": "ctrl+0"}
PAGES = {"shields": "brave://settings/shields", "settings": "brave://settings", "bookmarks": "brave://bookmarks",
         "history": "brave://history", "downloads": "brave://downloads"}
_DOMAIN = re.compile(r"^(localhost|[\w-]+(\.[\w-]+)+)(:\d+)?([/?#]\S*)?$")
_TAB_ID = re.compile(r"^[0-9A-Fa-f-]{8,64}$")
_opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))


def is_brave_argv(argv):
    """A natively installed Brave. Flatpak and snap sandboxes may not reach the profile, so they stay plain apps."""
    return bool(argv) and "/snap/" not in argv[0] and os.path.basename(argv[0]).startswith("brave")


def profile_dir():
    return Path(os.environ.get("IMACTV_DATA_DIR", str(Path.home() / ".local/share/imactv"))) / "brave-tv"


def tv_argv(argv):
    return [*argv, f"--user-data-dir={profile_dir()}", f"--remote-debugging-port={PORT}", "--kiosk",
            f"--class={WM_CLASS}", f"--force-device-scale-factor={SCALE}", "--enable-spatial-navigation",
            "--no-first-run", "--no-default-browser-check", "--hide-crash-restore-bubble", START_URL]


def target_url(text):
    """The address to open for what was typed: a web address, or a Brave Search for anything else."""
    text = text.strip() if isinstance(text, str) else ""
    if not text or len(text) > 2000:
        raise ValueError("Type a website or a search")
    parts = urlsplit(text)
    if parts.scheme in ("http", "https") and parts.netloc and not re.search(r"\s", text):
        return text
    if _DOMAIN.match(text):
        return "https://" + text
    return "https://search.brave.com/search?" + urlencode({"q": text})


def _call(path, method="GET", timeout=2):
    req = urllib.request.Request(f"http://127.0.0.1:{PORT}/json/{path}", method=method)
    with _opener.open(req, timeout=timeout) as response:
        return response.read().decode("utf-8", "replace")


def tabs(timeout=2):
    """Open pages, as [{id, title, url}]; raises OSError while the browser isn't up."""
    pages = json.loads(_call("list", timeout=timeout))
    return [{"id": p["id"], "title": p.get("title") or p.get("url", ""), "url": p.get("url", "")}
            for p in pages if p.get("type") == "page"]


def new_tab(url):
    return _call("new?" + quote(url, safe=""), "PUT")


def _tab_call(verb, tab_id):
    if not isinstance(tab_id, str) or not _TAB_ID.match(tab_id):
        raise ValueError("Unknown tab")
    _call(f"{verb}/{tab_id}")


def activate_tab(tab_id):
    _tab_call("activate", tab_id)


def close_tab(tab_id):
    _tab_call("close", tab_id)


def view(window_title, timeout=0.4):
    """What the phone shows: the tabs with the one in front marked. The debugging port doesn't say which tab is in
    front, but the window title does. None while the browser isn't answering."""
    try:
        pages = tabs(timeout)
    except (OSError, ValueError):
        return None
    title = re.sub(r" - Brave$", "", window_title or "")
    front = next((p for p in pages if p["title"] == title), pages[0] if pages else None)
    return {"tabs": [{**p, "active": p is front} for p in pages]}


def navigate(tab_id, url):
    """Load an address in an open tab, so its Back history stays. One command over the tab's DevTools WebSocket."""
    if not isinstance(tab_id, str) or not _TAB_ID.match(tab_id):
        raise ValueError("Unknown tab")
    with socket.create_connection(("127.0.0.1", PORT), timeout=2) as sock:
        key = base64.b64encode(os.urandom(16)).decode()
        sock.sendall((f"GET /devtools/page/{tab_id} HTTP/1.1\r\nHost: 127.0.0.1:{PORT}\r\nUpgrade: websocket\r\n"
                      f"Connection: Upgrade\r\nSec-WebSocket-Key: {key}\r\nSec-WebSocket-Version: 13\r\n\r\n").encode())
        reply = b""
        while b"\r\n\r\n" not in reply:
            chunk = sock.recv(4096)
            if not chunk:
                raise OSError("The tab closed the connection")
            reply += chunk
        if b" 101 " not in reply.split(b"\r\n", 1)[0]:
            raise OSError("The tab refused the connection")
        sock.sendall(_ws_frame(json.dumps({"id": 1, "method": "Page.navigate", "params": {"url": url}}).encode()))
        sock.recv(4096)             # the answer: nothing in it changes what happens next


def _ws_frame(payload):
    """One masked text frame, as a WebSocket client has to send it."""
    size = len(payload)
    head = bytes([0x81, 0x80 | size]) if size < 126 else bytes([0x81, 0x80 | 126]) + struct.pack(">H", size)
    mask = os.urandom(4)
    return head + mask + bytes(b ^ mask[i % 4] for i, b in enumerate(payload))
