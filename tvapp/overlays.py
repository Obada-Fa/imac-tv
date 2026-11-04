"""Where an island over another app is drawn, so its glass is never an old copy of the picture behind it.

YouTube (VacuumTube) and the TV browser (Brave) draw it in their own page, over their local debugging port: the
page frosts its own live picture under the island's outline, and the island's frame lies on top. mpv lays it over
its own video as an overlay, solid. Each frame comes in as a cairo ARGB32 surface with the island's outline as an
SVG path, both in device pixels. Nothing is sent while an island holds still."""

import base64
import io
import json
import logging
import os
from pathlib import Path
import socket
import tempfile
import threading

import browser
import youtube_volume

LOG = logging.getLogger("imactv.overlays")
MPV_SOCK = os.environ.get("IMACTV_MPV_SOCK", "/tmp/mpv.sock")
MPV_OVERLAY = 7                 # mpv's overlay slot for the island; the skip button uses osd-overlay, apart
ELEMENT = "__imactvIsland"

# One function in the page shows a frame: it makes the island's element the first time, and again after a reload.
# The element is sized in device pixels and scaled by the page's zoom, so the frame and its outline line up. The
# frame is decoded from its bytes onto a canvas, which a page's rules for images can't refuse; a frame that
# decodes after a newer one is dropped.
SHOW = """((png, outline, w, h) => {
  let box = document.getElementById("%(id)s");
  if (!box) {
    box = document.createElement("div");
    box.id = "%(id)s";
    box.style.cssText = "position:fixed;top:0;left:50%%;margin:0;padding:0;border:0;z-index:2147483647;" +
      "pointer-events:none;transform-origin:50%% 0;contain:strict;";
    const frost = document.createElement("div");
    frost.style.cssText = "position:absolute;inset:0;backdrop-filter:blur(18px) saturate(1.4);" +
      "-webkit-backdrop-filter:blur(18px) saturate(1.4);";
    const frame = document.createElement("canvas");
    frame.style.cssText = "position:absolute;inset:0;width:100%%;height:100%%;";
    box.append(frost, frame);
    box.seq = 0;
  }
  const host = document.fullscreenElement || document.documentElement;
  if (box.parentNode !== host) host.appendChild(box);
  const seq = ++box.seq;
  const bytes = Uint8Array.from(atob(png), (c) => c.charCodeAt(0));
  createImageBitmap(new Blob([bytes], {type: "image/png"})).then((picture) => {
    if (seq !== box.seq || !box.isConnected) return picture.close();
    const frame = box.lastChild;
    box.style.width = w + "px";
    box.style.height = h + "px";
    box.style.transform = "translateX(-50%%) scale(" + 1 / (window.devicePixelRatio || 1) + ")";
    box.firstChild.style.clipPath = "path('" + outline + "')";
    if (frame.width !== w || frame.height !== h) { frame.width = w; frame.height = h; }
    const cr = frame.getContext("2d");
    cr.clearRect(0, 0, w, h);
    cr.drawImage(picture, 0, 0);
    picture.close();
  });
  return true;
})""" % {"id": ELEMENT}
HIDE = f"(() => {{ const box = document.getElementById({json.dumps(ELEMENT)}); if (box) box.remove(); }})()"
VISIBLE = "document.visibilityState === 'visible'"


def svg_path(path):
    """A cairo path (from `copy_path`, in device pixels) as SVG path data, closed along the screen edge."""
    out = []
    for kind, points in path:
        if kind == 0:
            out.append("M%.1f %.1f" % points)
        elif kind == 1:
            out.append("L%.1f %.1f" % points)
        elif kind == 2:
            out.append("C%.1f %.1f %.1f %.1f %.1f %.1f" % points)
        elif kind == 3:
            out.append("Z")
    if out and out[-1] != "Z":
        out.append("Z")
    return " ".join(out)


def png_data(surface):
    """The frame as base64 PNG."""
    buf = io.BytesIO()
    surface.write_to_png(buf)
    return base64.b64encode(buf.getvalue()).decode()


class PageTarget:
    """Draws the island in a page over a DevTools port: a lasting connection, opened on the first frame and kept
    while the page lives. Replies are read and dropped on a thread of their own."""

    def __init__(self, port, pick):
        self.port, self.pick = port, pick       # pick(timeout) -> the page target dict, or None
        self.frost = LiveFrost()
        self._sock = None
        self._page = None
        self._lock = threading.Lock()
        self._next_id = 0

    def connect(self, timeout=0.4):
        """True once there is a connection to the page in front; reconnects if that page changed."""
        page = self.pick(timeout)
        if not page:
            self.close()
            return False
        if self._sock is not None and page.get("id") == self._page:
            return True
        self.close()
        try:
            sock, _rest = browser._open_page(page["id"], timeout, self.port)
        except (OSError, ValueError, KeyError):
            return False
        sock.settimeout(None)
        with self._lock:
            self._sock, self._page = sock, page["id"]
        threading.Thread(target=self._drain, args=(sock,), daemon=True, name="island-page").start()
        return True

    def show(self, surface, outline):
        width, height = surface.get_width(), surface.get_height()
        args = ", ".join(json.dumps(v) for v in (png_data(surface), outline, width, height))
        return self._evaluate(f"{SHOW}({args})")

    def hide(self):
        self._evaluate(HIDE)

    def close(self):
        with self._lock:
            sock, self._sock, self._page = self._sock, None, None
        if sock is not None:
            try:
                sock.close()
            except OSError:
                pass

    def _evaluate(self, expression):
        with self._lock:
            if self._sock is None:
                return False
            self._next_id += 1
            message = json.dumps({"id": self._next_id, "method": "Runtime.evaluate",
                                  "params": {"expression": expression}})
            try:
                self._sock.sendall(browser._ws_frame(message.encode()))
                return True
            except OSError:
                sock, self._sock, self._page = self._sock, None, None
        try:
            sock.close()
        except OSError:
            pass
        return False

    def _drain(self, sock):
        try:
            while sock.recv(65536):
                pass
        except OSError:
            pass
        with self._lock:
            if self._sock is sock:
                self._sock, self._page = None, None


def youtube_target():
    return PageTarget(youtube_volume.PORT, lambda timeout: youtube_volume.youtube_page(timeout))


def front_tab(timeout):
    """The Brave tab on screen: the debugging port doesn't say which one is in front, so ask each tab."""
    try:
        tabs = browser.tabs(timeout)
    except (OSError, ValueError):
        return None
    if len(tabs) > 1:
        for tab in tabs:
            try:
                reply = browser._page_command(tab["id"], "Runtime.evaluate",
                                              {"expression": VISIBLE, "returnByValue": True}, timeout)
            except (OSError, ValueError):
                continue
            if (reply.get("result") or {}).get("value") is True:
                return tab
    return tabs[0] if tabs else None


def browser_target():
    return PageTarget(browser.PORT, front_tab)


class MpvTarget:
    """Lays the island over mpv's video with `overlay-add`: the frame goes into a small file that mpv maps, in two
    halves used in turn, so mpv never reads a frame while the next one is written."""

    def __init__(self, path=None):
        self.path = path or MPV_SOCK
        self.frost = SolidFrost()
        self._sock = None
        self._file = None
        self._half = 0
        self._size = None
        self._osd = None

    def connect(self, timeout=0.4):
        if self._sock is not None:
            return True
        sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        try:
            sock.settimeout(timeout)
            sock.connect(self.path)
        except OSError:
            sock.close()
            return False
        self._sock = sock
        self._osd = self._osd_width()
        if not self._osd:
            self.close()
            return False
        return True

    def _osd_width(self):
        """The width mpv draws its overlays at: its window's, in pixels."""
        reply = self._request(["get_property", "osd-dimensions"], 31)
        data = (reply or {}).get("data") or {}
        width = data.get("w")
        return int(width) if isinstance(width, (int, float)) and width > 0 else None

    def _request(self, command, request_id):
        if not self._send({"command": command, "request_id": request_id}):
            return None
        data = b""
        try:
            while True:
                chunk = self._sock.recv(65536)
                if not chunk:
                    return None
                data += chunk
                while b"\n" in data:
                    line, data = data.split(b"\n", 1)
                    try:
                        message = json.loads(line)
                    except ValueError:
                        continue
                    if message.get("request_id") == request_id:
                        return message
        except OSError:
            return None

    def _send(self, message):
        if self._sock is None:
            return False
        try:
            self._sock.sendall((json.dumps(message) + "\n").encode())
            return True
        except OSError:
            self.close()
            return False

    def _ensure_file(self, size):
        if self._file is not None and self._size == size:
            return True
        self._drop_file()
        folder = "/dev/shm" if os.path.isdir("/dev/shm") else None
        try:
            fd, name = tempfile.mkstemp(prefix="imactv-island-", suffix=".bgra", dir=folder)
            os.close(fd)
            self._file = Path(name)
            with open(self._file, "wb") as out:
                out.truncate(2 * size)
        except OSError:
            self._file = None
            return False
        self._size = size
        return True

    def show(self, surface, outline=None):
        surface.flush()
        width, height, stride = surface.get_width(), surface.get_height(), surface.get_stride()
        size = stride * height
        if self._sock is None or not self._ensure_file(size):
            return False
        offset = self._half * size
        try:
            with open(self._file, "r+b") as out:
                out.seek(offset)
                out.write(bytes(surface.get_data()))    # cairo's ARGB32 is premultiplied BGRA in memory
        except OSError:
            return False
        self._half = 1 - self._half
        x = max(0, (self._osd - width) // 2)
        # mpv's own reply ("success") is not waited for: it is read and dropped with the next request's
        if not self._send({"command": ["overlay-add", MPV_OVERLAY, x, 0, str(self._file), offset, "bgra",
                                       width, height, stride]}):
            return False
        self._discard()
        return True

    def _discard(self):
        """Read away mpv's replies and events, so its socket never fills."""
        try:
            self._sock.setblocking(False)
            while self._sock.recv(65536):
                pass
        except (BlockingIOError, InterruptedError):
            pass
        except OSError:
            self.close()
            return
        finally:
            if self._sock is not None:
                self._sock.setblocking(True)

    def hide(self):
        self._send({"command": ["overlay-remove", MPV_OVERLAY]})
        self.close()

    def close(self):
        if self._sock is not None:
            try:
                self._sock.close()
            except OSError:
                pass
        self._sock = None
        self._drop_file()

    def _drop_file(self):
        if self._file is not None:
            try:
                self._file.unlink()
            except OSError:
                pass
        self._file, self._size = None, None


class LiveFrost:
    """What the island frosts in a page: nothing itself; the page blurs its live picture under the outline."""
    transparency = True
    live = True

    def blur_layers(self):
        return []


class SolidFrost:
    """Solid glass, where nothing can be seen through: over mpv, and in the island's own window."""
    transparency = False

    def blur_layers(self):
        return []
