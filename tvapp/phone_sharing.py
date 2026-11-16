"""Temporary phone media and the receivers that share the TV display."""
import io
import json
import logging
from pathlib import Path
import re
import secrets
import shutil
import socket
import subprocess
import tempfile
import time
import threading

from PIL import Image, ImageCms, ImageOps

LIMIT = 5 * 1024**3
MAX_PHOTO_PIXELS = 100_000_000
PHOTO_CODECS = {"mjpeg", "png", "webp", "hevc", "av1"}
VIDEO_FORMATS = {"mov", "mp4", "matroska", "webm", "mpegts", "avi"}
LOG = logging.getLogger(__name__)
SRGB = ImageCms.ImageCmsProfile(ImageCms.createProfile("sRGB"))


def normalize_photo(path, output):
    """Bake the camera profile into sRGB pixels before mpv decodes the still."""
    try:
        with Image.open(path) as source:
            if source.width * source.height > MAX_PHOTO_PIXELS:
                raise ValueError("This photo is too large to display")
            photo = ImageOps.exif_transpose(source)
            profile = photo.info.get("icc_profile")
            alpha = None
            if photo.mode in ("RGBA", "LA", "PA") or "transparency" in photo.info:
                alpha = photo.convert("RGBA").getchannel("A")
            if photo.mode not in ("RGB", "CMYK", "L", "LAB"):
                photo = photo.convert("RGB")
            if profile:
                try:
                    camera = ImageCms.getOpenProfile(io.BytesIO(profile))
                    photo = ImageCms.profileToProfile(photo, camera, SRGB, outputMode="RGB")
                except (ImageCms.PyCMSError, OSError, ValueError):
                    LOG.warning("Could not apply the phone photo's embedded color profile")
            if photo.mode != "RGB":
                photo = photo.convert("RGB")
            if alpha is not None:
                photo.putalpha(alpha)
            photo.save(output, format="PNG", icc_profile=SRGB.tobytes(), compress_level=1)
    except (Image.DecompressionBombError, OSError, SyntaxError) as exc:
        raise ValueError("This photo could not be decoded") from exc


def inspect_file(path):
    with path.open("rb") as source:
        header = source.read(64)
    box_end = min(len(header), int.from_bytes(header[:4], "big"))
    brands = [header[8:12]] + [header[i:i + 4] for i in range(16, box_end, 4)]
    if header[4:8] == b"ftyp" and any(
            brand in {b"heic", b"heix", b"heim", b"heis", b"mif1", b"avif"} for brand in brands):
        return "heif"
    probe = subprocess.run(
        ["ffprobe", "-v", "error", "-protocol_whitelist", "file", "-show_entries",
         "format=format_name:stream=codec_type,codec_name", "-of", "json", str(path)],
        capture_output=True, timeout=12,
    )
    if probe.returncode:
        raise ValueError("This photo or video format could not be opened")
    data = json.loads(probe.stdout)
    formats = set(data.get("format", {}).get("format_name", "").split(","))
    for stream in data.get("streams", []):
        if stream["codec_type"] != "video":
            continue
        if formats & VIDEO_FORMATS:
            return "video"
        if stream.get("codec_name") in PHOTO_CODECS and any(
                f.endswith("_pipe") or f in {"image2", "jpeg_pipe"} for f in formats):
            return "photo"
    raise ValueError("Choose a supported photo or video")


class Sharing:
    def __init__(self, data, env, acquire, release, changed):
        self.root = data / "phone-sharing"
        shutil.rmtree(self.root, ignore_errors=True)
        self.root.mkdir(mode=0o700, parents=True)
        self.env = env
        self.acquire = acquire
        self.release = release
        self.changed = changed
        self.lock = threading.RLock()
        self.session = None
        self.revision = 0
        self.process = None
        self.receiver = None
        self.stream = None
        self.remuxer = None
        self.used = 0
        self.player = shutil.which("mpv")
        self.probe = shutil.which("ffprobe")
        self.heif = shutil.which("heif-convert") or shutil.which("heif-dec")
        self.uxplay = shutil.which("uxplay")
        self.gst = shutil.which("gst-inspect-1.0")
        self.airplay_retry_at = 0
        self.error = None

    def state(self):
        with self.lock:
            session = self.session
            out = {"media": bool(self.player and self.probe), "airplay": bool(self.uxplay),
                   "mirror": bool(self.player and self.gst), "airplay_ready": self.receiver is not None,
                   "error": self.error}
            if session:
                index = session["index"]
                items = session["items"]
                out.update(source=session["source"], index=index, count=len(items),
                           item={key: items[index][key] for key in ("name", "kind")} if items else None)
            return out

    def stop(self, *, return_home=True):
        with self.lock:
            self.revision += 1
            session, self.session = self.session, None
            if not session:
                return
            if session["source"] == "airplay":
                self.restart_airplay()
            stream, self.stream = self.stream, None
            if stream:
                try:
                    stream.shutdown(socket.SHUT_RD)
                except OSError:
                    pass
            remuxer, self.remuxer = self.remuxer, None
            if remuxer and remuxer.poll() is None:
                remuxer.kill()
                remuxer.wait()
            self._kill_player()
            for item in session["items"]:
                item["path"].unlink(missing_ok=True)
                self.used -= item["size"]
            self.changed()
            if return_home:
                self.release()

    def _kill_player(self):
        proc, self.process = self.process, None
        if proc and proc.poll() is None:
            proc.terminate()
            try:
                proc.wait(timeout=1)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait()
        (self.root / "mpv.sock").unlink(missing_ok=True)

    def _start(self, source):
        self.stop(return_home=False)
        self.acquire()
        self.error = None
        self.session = {"source": source, "id": secrets.token_hex(16), "items": [], "index": 0}
        self.changed()
        return self.session

    def upload(self, source, size, name, session_id):
        if not (self.player and self.probe):
            raise RuntimeError("Install mpv and ffmpeg on the TV")
        with self.lock:
            if size <= 0 or size > LIMIT - self.used or shutil.disk_usage(self.root).free < size + 128 * 1024**2:
                raise ValueError("There is not enough temporary space for this file")
            revision = self.revision
            self.used += size
        path = None
        owned = False
        try:
            with tempfile.NamedTemporaryFile(dir=self.root, delete=False) as target:
                path = Path(target.name)
                left = size
                while left:
                    block = source.read(min(1 << 20, left))
                    if not block:
                        raise ValueError("Upload ended early")
                    target.write(block)
                    left -= len(block)
            kind = inspect_file(path)
            if kind in ("photo", "heif"):
                with tempfile.TemporaryDirectory(dir=self.root) as temp:
                    decoded = path
                    if kind == "heif":
                        if not self.heif:
                            raise RuntimeError("Install the TV’s HEIF decoder to open this photo")
                        decoded = Path(temp) / "decoded.png"
                        result = subprocess.run([self.heif, str(path), str(decoded)],
                                                capture_output=True, timeout=30)
                        if result.returncode or not decoded.is_file():
                            raise ValueError("The HEIF photo could not be opened")
                    normalized = Path(temp) / "display.png"
                    normalize_photo(decoded, normalized)
                    rendered_size = normalized.stat().st_size
                    with self.lock:
                        if rendered_size - size > LIMIT - self.used:
                            raise ValueError("There is not enough temporary space for this photo")
                        normalized.replace(path)
                        self.used += rendered_size - size
                        size = rendered_size
                    kind = "photo"
            with self.lock:
                if self.revision != revision:
                    raise ValueError("This sharing session ended")
                if session_id:
                    session = self.session
                    if not session or session["source"] != "files" or session["id"] != session_id:
                        raise ValueError("This sharing session ended")
                else:
                    session = self._start("files")
                item = {"path": path, "name": name[:160] or "Phone media", "kind": kind, "size": size}
                session["items"].append(item)
                try:
                    if len(session["items"]) == 1:
                        self._show_item()
                except Exception:
                    session["items"].pop()
                    self.stop()
                    raise
                owned = True
                self.changed()
                return {"id": session["id"], **self.state()}
        finally:
            if not owned:
                if path is not None:
                    path.unlink(missing_ok=True)
                with self.lock:
                    self.used -= size

    def mirror(self, request):
        if not (self.player and self.gst):
            raise RuntimeError("Install mpv and GStreamer on the TV")
        session = None
        try:
            with self.lock:
                session = self._start("android")
                self.stream = request.connection
                audio = request.headers.get("X-Nexus-Audio") == "pcm"
                remuxer = self.remuxer = subprocess.Popen(
                    ["/usr/bin/python3", str(Path(__file__).with_name("mirror_input.py")),
                     "audio" if audio else "silent"], env=self.env, stdin=subprocess.PIPE,
                    stdout=subprocess.PIPE,
                )
                try:
                    self.process = self._spawn_player(
                        ["--profile=low-latency", "--demuxer=lavf", "--demuxer-lavf-format=matroska", "-"],
                        input_stream=remuxer.stdout,
                    )
                finally:
                    remuxer.stdout.close()
                threading.Thread(target=self._watch_player, args=(self.process,), daemon=True).start()
            with remuxer.stdin:
                while True:
                    line = request.rfile.readline(32)
                    if not line:
                        break
                    if not line.endswith(b"\r\n"):
                        raise ValueError("Invalid mirror chunk")
                    count = int(line.strip(), 16)
                    if count == 0:
                        break
                    if not 0 < count <= 2 * 1024**2:
                        raise ValueError("Invalid mirror chunk size")
                    data = request.rfile.read(count)
                    if len(data) != count or request.rfile.read(2) != b"\r\n":
                        break
                    remuxer.stdin.write(data)
        except (BrokenPipeError, ConnectionError, TimeoutError):
            pass
        finally:
            with self.lock:
                if session is not None and self.session is session:
                    self.stop()

    def _spawn_player(self, args, *, input_stream=subprocess.DEVNULL):
        return subprocess.Popen(
            [self.player, "--no-config", "--no-terminal", "--fs", "--ontop", "--hwdec=auto-safe",
             "--title=Nexus TV Sharing", *args], env=self.env,
            stdin=input_stream, stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )

    def _show_item(self):
        self._kill_player()
        item = self.session["items"][self.session["index"]]
        self.process = self._spawn_player([
            f"--input-ipc-server={self.root / 'mpv.sock'}",
            "--keep-open=yes", "--image-display-duration=inf", "--loop-file=no",
            "--load-scripts=no", "--autoload-files=no", "--ytdl=no", "--load-unsafe-playlists=no",
            "--demuxer-lavf-o=protocol_whitelist=file", "--", str(item["path"]),
        ])
        threading.Thread(target=self._watch_player, args=(self.process,), daemon=True).start()

    def _watch_player(self, process):
        code = process.wait()
        with self.lock:
            if self.process is process:
                self.stop()
                if code:
                    self.error = "The TV could not play this media"
                    self.changed()

    def control(self, cmd):
        with self.lock:
            if cmd == "restart_airplay":
                if self.session and self.session["source"] == "airplay":
                    self.stop()
                else:
                    self.restart_airplay()
            elif cmd == "stop":
                self.stop()
                self.error = None
            elif cmd in ("previous", "next", "pause", "rewind", "forward"):
                s = self.session
                if s and s["source"] == "files":
                    if cmd in ("previous", "next"):
                        index = max(0, min(len(s["items"]) - 1, s["index"] + (1 if cmd == "next" else -1)))
                        if index != s["index"]:
                            s["index"] = index
                            self._show_item()
                    else:
                        command = ["cycle", "pause"] if cmd == "pause" else ["seek", -10 if cmd == "rewind" else 10]
                        with socket.socket(socket.AF_UNIX) as conn:
                            conn.settimeout(1)
                            conn.connect(str(self.root / "mpv.sock"))
                            conn.sendall(json.dumps({"command": command}).encode() + b"\n")
            else:
                raise ValueError("Unknown sharing action")
            return self.state()

    def ensure_airplay(self):
        with self.lock:
            if time.monotonic() >= self.airplay_retry_at:
                self.start_airplay()

    def start_airplay(self):
        if not self.uxplay or self.receiver:
            return
        self.airplay_retry_at = time.monotonic() + 20
        # UxPlay also renders the PIN on screen when the iPhone requests it.
        try:
            proc = subprocess.Popen(
                ["stdbuf", "-oL", self.uxplay, "-n", "Nexus TV", "-nh", "-pin", "-fs", "-p", "7000", "-d"],
                env=self.env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
                errors="replace",
            )
        except OSError:
            self.error = "Could not start AirPlay"
            self.changed()
            return
        self.receiver = proc
        self.error = None
        self.changed()
        threading.Thread(target=self._watch_airplay, args=(proc,), daemon=True).start()

    def restart_airplay(self):
        with self.lock:
            old, self.receiver = self.receiver, None
            if old and old.poll() is None:
                old.terminate()
                try:
                    old.wait(timeout=2)
                except subprocess.TimeoutExpired:
                    old.kill()
                    old.wait()
            self.start_airplay()
            self.changed()

    def _watch_airplay(self, process):
        with process.stdout:
            for line in process.stdout:
                with self.lock:
                    if self.receiver is not process:
                        break
                    if "Received unencrypted codec packet from client" in line:
                        if not self.session or self.session["source"] != "airplay":
                            self._start("airplay")
                    elif re.search(r"mirror tcp socket is closed|lost connection with client|^Open connections: 0", line):
                        if self.session and self.session["source"] == "airplay":
                            self.stop()
        process.wait()
        with self.lock:
            if self.receiver is process:
                self.receiver = None
                if process.returncode:
                    self.error = "Could not start AirPlay. Check Avahi and restart the receiver."
                if self.session and self.session["source"] == "airplay":
                    self.stop()
                self.changed()
