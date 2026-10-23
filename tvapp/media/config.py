"""Media settings: defaults merged with $IMACTV_DATA_DIR/media.json."""
import json
import os
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
    "torbox_key": "",  # TorBox API key; when set, Torrentio returns TorBox direct links
}


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


def load():
    try:
        user = json.loads((data_dir() / "media.json").read_text())
    except (OSError, ValueError):
        user = {}
    cfg = _merge(DEFAULTS, user if isinstance(user, dict) else {})
    key = cfg.get("torbox_key")
    if isinstance(key, str) and key.strip():
        cfg["addons"] = dict(cfg["addons"], streams=[with_torbox(u, key.strip()) for u in cfg["addons"]["streams"]])
    return cfg
