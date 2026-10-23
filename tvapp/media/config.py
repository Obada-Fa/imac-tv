"""Media settings: defaults merged with $IMACTV_DATA_DIR/media.json."""
import json
import os
import re
from pathlib import Path

DEFAULTS = {
    "addons": {
        "meta": "https://v3-cinemeta.strem.io",
        "streams": ["https://torrentio.strem.fun/sort=qualitysize|qualityfilter=cam,scr,unknown"],
        "subtitles": "https://opensubtitles-v3.strem.io",
    },
    "sub_langs": ["ara", "dut", "eng"],
    "sub_default": "eng",
    "engine": "http://127.0.0.1:11470",
    "cache_bytes": 3221225472,
    "max_size_gb": 25,
    "quality_order": ["1080p", "720p", "2160p", "480p"],
    "codec_order": ["H264", "", "HEVC", "AV1"],  # "" = codec not named in the release title
    "autoplay_next": True,          # roll into the next episode when credits start / the file ends
    "auto_skip_intro": False,       # skip intros and recaps without asking
    "still_watching_episodes": 3,   # autoplayed episodes in a row before "Are you still watching?"
    "still_watching_hours": 2.5,    # ...or this long without any remote input
    "torbox_key": "",  # legacy place for the TorBox key; $DATA/torbox.key wins when present
}

KEY_RE = re.compile(r"^[A-Za-z0-9-]{16,128}$")
KEY_FILE = "torbox.key"
_KEY_URL = re.compile(r"(/resolve/[^/\s]+/|torbox=|Bearer\s+)[^/\s|&\"']+", re.I)


def data_dir():
    return Path(os.environ.get("IMACTV_DATA_DIR", str(Path.home() / ".local/share/imactv")))


def _merge(base, extra):
    out = dict(base)
    for key, value in extra.items():
        if isinstance(value, dict) and isinstance(out.get(key), dict):
            out[key] = _merge(out[key], value)
        else:
            out[key] = value
    return out


def with_torbox(url, key):
    """Add torbox=KEY to a Torrentio add-on URL's option segment, unless it already has a debrid key."""
    if "torrentio" not in url or "torbox=" in url:
        return url
    base, _, opts = (url.rstrip("/") + "/").partition(".strem.fun/")
    if not _:
        return url
    opts = opts.rstrip("/")
    return f"{base}.strem.fun/{opts + '|' if opts else ''}torbox={key}"


def redact(text):
    """Mask TorBox keys in text bound for logs or error messages (debrid URLs embed the key)."""
    text = _KEY_URL.sub(lambda m: m.group(1) + "***", str(text))
    key = read_key()
    if key and key in text:
        text = text.replace(key, "***")
    return text


def read_key():
    """The stored TorBox key: $DATA/torbox.key first, then media.json's torbox_key. '' when none."""
    try:
        key = (data_dir() / KEY_FILE).read_text().strip()
        if key:
            return key
    except OSError:
        pass
    try:
        user = json.loads((data_dir() / "media.json").read_text())
        key = user.get("torbox_key") if isinstance(user, dict) else ""
    except (OSError, ValueError):
        key = ""
    return key.strip() if isinstance(key, str) else ""


def write_key(key):
    """Atomically store the key as $DATA/torbox.key, mode 0600 from the moment it is created."""
    directory = data_dir()
    directory.mkdir(mode=0o700, parents=True, exist_ok=True)
    path, tmp = directory / KEY_FILE, directory / (KEY_FILE + ".tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as f:
        f.write(key.strip() + "\n")
        f.flush()
        os.fsync(f.fileno())
    os.chmod(tmp, 0o600)
    os.replace(tmp, path)


def remove_key():
    """Forget the key everywhere it can be stored (file and legacy media.json entry)."""
    try:
        (data_dir() / KEY_FILE).unlink()
    except OSError:
        pass
    path = data_dir() / "media.json"
    try:
        user = json.loads(path.read_text())
    except (OSError, ValueError):
        return
    if isinstance(user, dict) and user.pop("torbox_key", None) is not None:
        tmp = path.with_suffix(".tmp")
        with os.fdopen(os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600), "w") as f:
            json.dump(user, f, indent=2)
        os.replace(tmp, path)


def load():
    try:
        user = json.loads((data_dir() / "media.json").read_text())
    except (OSError, ValueError):
        user = {}
    cfg = _merge(DEFAULTS, user if isinstance(user, dict) else {})
    cfg["torbox_key"] = ""
    key = read_key()
    if key:
        cfg["addons"] = dict(cfg["addons"], streams=[with_torbox(u, key) for u in cfg["addons"]["streams"]])
    return cfg
