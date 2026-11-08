"""Hand a torrent (magnet or .torrent file) to TorBox and wait for a playable link.

Used for private-tracker releases (ArabP2P): TorBox downloads them with the user's passkey and
seeds them back, so the account keeps its ratio. Runs only while the user waits for a picked source.
"""
import json
import logging
import re
import time
import uuid
import urllib.error
import urllib.parse
import urllib.request

from . import arabic, config

API = "https://api.torbox.app/v1/api"
POLL_S = 2.0
WAIT_S = 45 * 60
SEED_AUTO = 1   # TorBox decides; it keeps seeding private torrents
ERRORS = {
    "ACTIVE_LIMIT": "TorBox is already downloading as many torrents as your plan allows",
    "DOWNLOAD_TOO_LARGE": "This release is too big for your TorBox plan",
    "MONTHLY_LIMIT": "Your TorBox plan's monthly limit is used up",
    "COOLDOWN_LIMIT": "TorBox asks to wait a moment before adding another torrent",
    "BOZO_TORRENT": "TorBox couldn't read this torrent",
    "PLAN_RESTRICTED_FEATURE": "Your TorBox plan doesn't allow this",
    "NO_SERVERS_AVAILABLE_ERROR": "TorBox is busy, try again in a minute",
    "DOWNLOAD_SERVER_ERROR": "TorBox had a problem downloading this release",
}


LOG = logging.getLogger("imactv")


class TorBoxError(Exception):
    pass


class Cancelled(Exception):
    pass


def _call(method, path, key, params=None, body=None, content_type=None, timeout=20):
    url = f"{API}/{path}" + ("?" + urllib.parse.urlencode(params) if params else "")
    headers = {"Authorization": "Bearer " + key, "Accept": "application/json", "User-Agent": "imactv/1.0"}
    if content_type:
        headers["Content-Type"] = content_type
    req = urllib.request.Request(url, data=body, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            data = json.loads(r.read(4_000_000))
    except urllib.error.HTTPError as exc:
        try:
            data = json.loads(exc.read(200_000))
        except (OSError, ValueError):
            data = None
        if not isinstance(data, dict):
            if exc.code in (401, 403):
                raise TorBoxError("TorBox rejected the API key") from None
            raise TorBoxError(f"TorBox answered {exc.code}") from None
    except (urllib.error.URLError, OSError, ValueError):
        raise TorBoxError("Couldn't reach TorBox") from None
    if not isinstance(data, dict) or data.get("success") is False:
        code = (data or {}).get("error") if isinstance(data, dict) else None
        detail = (data or {}).get("detail") if isinstance(data, dict) else None
        raise TorBoxError(ERRORS.get(code) or config.redact(detail or code or "TorBox refused the request"))
    return data.get("data")


def _multipart(fields, file_field=None):
    boundary = "----nexustv" + uuid.uuid4().hex
    out = []
    for name, value in fields.items():
        out.append(f'--{boundary}\r\nContent-Disposition: form-data; name="{name}"\r\n\r\n{value}\r\n'.encode())
    if file_field:
        name, filename, data = file_field
        out.append(f'--{boundary}\r\nContent-Disposition: form-data; name="{name}"; filename="{filename}"\r\n'
                   f'Content-Type: application/x-bittorrent\r\n\r\n'.encode() + data + b"\r\n")
    out.append(f"--{boundary}--\r\n".encode())
    return b"".join(out), f"multipart/form-data; boundary={boundary}"


def create(key, kind, value):
    fields = {"seed": str(SEED_AUTO), "allow_zip": "false"}
    if kind == "magnet":
        fields["magnet"] = value
        body, ctype = _multipart(fields)
    else:
        body, ctype = _multipart(fields, ("file", "release.torrent", value))
    data = _call("POST", "torrents/createtorrent", key, body=body, content_type=ctype, timeout=40)
    tid = (data or {}).get("torrent_id") if isinstance(data, dict) else None
    if tid is None and isinstance(data, dict):
        tid = data.get("id")
    if tid is None:
        raise TorBoxError("TorBox didn't say which torrent it added")
    return int(tid)


def info(key, torrent_id):
    data = _call("GET", "torrents/mylist", key, {"id": torrent_id, "bypass_cache": "true"})
    if isinstance(data, list):
        data = next((t for t in data if str(t.get("id")) == str(torrent_id)), None)
    if not isinstance(data, dict):
        raise TorBoxError("TorBox lost track of this torrent")
    return data


def progress(t):
    """0..1 from TorBox's progress (seen as 0..1 and as 0..100)."""
    try:
        p = float(t.get("progress") or 0)
    except (TypeError, ValueError):
        return 0.0
    return p / 100 if p > 1 else p


def ready(t):
    state = str(t.get("download_state") or "").lower()
    return bool(t.get("download_finished") or t.get("download_present")) or state in ("cached", "completed", "uploading")


def pick_file(files, episode=None):
    """The video for this episode (an int, or numbers to try in order: season-relative, then running)."""
    tries = [e for e in (episode if isinstance(episode, (list, tuple)) else [episode]) if e is not None]
    videos = [f for f in files or [] if isinstance(f, dict)
              and str(f.get("short_name") or f.get("name") or "").lower().endswith(arabic.VIDEO_EXT)
              and not re.search(r"\bsample\b", str(f.get("name") or ""), re.I)]
    if not videos:
        return None
    if tries and len(videos) > 1:
        for wanted in tries:
            hits = [f for f in videos if arabic.file_episode(str(f.get("short_name") or f.get("name"))) == wanted]
            if hits:
                return max(hits, key=lambda f: f.get("size") or 0)
        return None
    return max(videos, key=lambda f: f.get("size") or 0)


def link(key, torrent_id, file_id):
    data = _call("GET", "torrents/requestdl", key, {"token": key, "torrent_id": torrent_id, "file_id": file_id})
    if isinstance(data, dict):
        data = data.get("url") or data.get("link")
    if not isinstance(data, str) or not re.match(r"^https?://", data):
        raise TorBoxError("TorBox didn't give a download link")
    return data


def prepare(url, episode=None, on_progress=None, cancelled=lambda: False, sleep=time.sleep):
    """Direct https link for a private-tracker source. on_progress(fraction, speed_bytes, seeds, stage)."""
    key = config.read_key()
    if not key:
        raise TorBoxError("Add your TorBox API key in Settings to play Arabic sources")
    report = on_progress or (lambda *a: None)
    report(0.0, 0.0, 0, "Getting the torrent from ArabP2P…")
    kind, value = arabic.fetch_torrent(url)
    if cancelled():
        raise Cancelled()
    report(0.0, 0.0, 0, "Adding it to TorBox…")
    torrent_id = create(key, kind, value)
    deadline = time.monotonic() + WAIT_S
    first = True
    while True:
        if cancelled():
            raise Cancelled()
        t = info(key, torrent_id)
        if first:   # field names only, to check TorBox's (undocumented) shape from the log
            LOG.info("TorBox torrent %s: fields %s, state %r, progress %r", torrent_id, sorted(t),
                     t.get("download_state"), t.get("progress"))
            first = False
        if ready(t):
            break
        state = str(t.get("download_state") or "").lower()
        if state.startswith("error") or state in ("failed",):
            raise TorBoxError("TorBox couldn't download this release")
        frac = progress(t)
        speed = float(t.get("download_speed") or 0)
        seeds = int(t.get("seeds") or 0)
        if "stalled" in state or (seeds == 0 and frac == 0 and state not in ("metadl", "checkingresumedata")):
            stage = "TorBox is waiting for seeders…"
        else:
            stage = f"TorBox is downloading… {int(frac * 100)}%"
        report(frac, speed, seeds, stage)
        if time.monotonic() > deadline:
            raise TorBoxError("TorBox is still downloading; try again later (it keeps going)")
        sleep(POLL_S)
    chosen = pick_file(t.get("files"), episode)
    if chosen is None:
        raise TorBoxError("Couldn't find this episode in the release" if episode not in (None, [], ())
                          else "This release has no video file")
    report(1.0, 0.0, int(t.get("seeds") or 0), "Opening TorBox link…")
    return link(key, torrent_id, chosen.get("id", 0))
