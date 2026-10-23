"""Turn Torrentio stream objects into ranked playable sources."""
import re

FLAGS = {"🇬🇧": "en", "🇺🇸": "en", "🇮🇹": "it", "🇪🇸": "es", "🇫🇷": "fr", "🇩🇪": "de", "🇳🇱": "nl",
         "🇷🇺": "ru", "🇵🇹": "pt", "🇧🇷": "pt", "🇵🇱": "pl", "🇹🇷": "tr", "🇸🇦": "ar", "🇯🇵": "ja",
         "🇰🇷": "ko", "🇨🇳": "zh", "🇮🇳": "hi", "🇺🇦": "uk", "🇨🇿": "cs", "🇸🇪": "sv"}
UNITS = {"KB": 1e3, "MB": 1e6, "GB": 1e9, "TB": 1e12}
BAD = re.compile(r"\b(cam|hdcam|camrip|ts|hdts|telesync|tc|telecine|scr|screener|dvdscr|workprint)\b", re.I)
SOURCES = (("REMUX", r"remux"), ("BluRay", r"blu-?ray|bdrip|brrip|bdremux"), ("WEB-DL", r"web-?dl|webrip|web\b"),
           ("HDTV", r"hdtv"), ("DVDRip", r"dvd"))
CACHED = re.compile(r"\[(TB|RD|AD|PM|DL)\+\]|⚡|instant", re.I)
CODECS = (("AV1", r"\bav1\b"), ("HEVC", r"x265|hevc|h\.?265"), ("H264", r"x264|avc|h\.?264"))


def _quality(name, title):
    text = name + "\n" + title
    if re.search(r"\b(2160p|4k|uhd)\b", text, re.I):
        return "2160p"
    found = re.search(r"\b(1080|720|480)p\b", text, re.I)
    return found.group(1) + "p" if found else ""


def parse(stream):
    name = stream.get("name") or ""
    title = stream.get("title") or ""
    head, _, rest = title.partition("\n")
    text = name + "\n" + title
    size = re.search(r"💾\s*([\d.]+)\s*(KB|MB|GB|TB)", title, re.I)
    seeds = re.search(r"👤\s*(\d+)", title)
    languages = []
    for flag, code in FLAGS.items():
        if flag in title and code not in languages:
            languages.append(code)
    if re.search(r"multi[ -]?(audio|sub)|dual[ -]?audio", title, re.I):
        languages.append("multi")
    source = next((label for label, pattern in SOURCES if re.search(pattern, text, re.I)), "")
    codec = next((label for label, pattern in CODECS if re.search(pattern, text, re.I)), "")
    idx = stream.get("fileIdx")
    url = stream.get("url")
    url = url if isinstance(url, str) and re.match(r"https?://", url, re.I) else None
    cached = bool(url) and bool(CACHED.search(name) or CACHED.search(head)
                                or (re.search(r"torbox", name, re.I) and not re.search(r"download", name, re.I)))
    out = {"info_hash": str(stream.get("infoHash") or "").lower(),
           "file_idx": idx if isinstance(idx, int) and not isinstance(idx, bool) and idx >= 0 else None,
           "url": url, "direct": bool(url), "cached": cached,
           "title": head.strip(),
           "quality": _quality(name, title),
           "hdr": bool(re.search(r"\bhdr(10)?\b", text, re.I)),
           "dv": bool(re.search(r"\bdv\b|dolby.?vision", text, re.I)),
           "codec": codec, "source": source,
           "ten_bit": bool(re.search(r"\b10[ -]?bit\b|hi10", text, re.I)),
           "size_bytes": int(float(size.group(1)) * UNITS[size.group(2).upper()]) if size else 0,
           "seeders": int(seeds.group(1)) if seeds else 0,
           "languages": languages,
           "sources": [x for x in stream.get("sources") or [] if isinstance(x, str)]}
    out["label"] = label(out)
    return out


def label(src):
    parts = [src["quality"] or "?"]
    if src["hdr"]:
        parts[0] += " HDR"
    if src["dv"]:
        parts[0] += " DV"
    if src["source"]:
        parts.append(src["source"])
    if src["size_bytes"]:
        parts.append(f"{src['size_bytes'] / 1e9:.1f} GB" if src["size_bytes"] >= 1e8 else f"{src['size_bytes'] / 1e6:.0f} MB")
    if src.get("direct"):
        parts.append("TorBox ⚡" if src.get("cached") else "Debrid (not cached)")
    else:
        parts.append(f"{src['seeders']} seeds")
    return " · ".join(parts)


def rank(sources, cfg):
    order = cfg.get("quality_order", [])
    codecs = cfg.get("codec_order", ["H264", "", "HEVC", "AV1"])
    limit = cfg.get("max_size_gb", 25) * 1e9
    good = [s for s in sources if (len(s.get("info_hash") or "") == 40 or s.get("url")) and not BAD.search(s.get("title", ""))
            and s.get("quality") != "cam" and s.get("size_bytes", 0) <= limit]

    def key(s):
        # Quality first: a live 1080p always beats better-seeded 4K/720p; dead torrents (<2 seeds) go last.
        direct = bool(s.get("url"))
        group = (0 if s.get("cached") else 2) if direct else 1
        seeders = 0 if direct else s["seeders"]
        q = order.index(s["quality"]) if s["quality"] in order else len(order)
        # Old/weak CPUs decode H.264 in hardware; HEVC (esp. 10-bit/HDR) and AV1 fall back to the CPU and stutter.
        c = codecs.index(s.get("codec", "")) if s.get("codec", "") in codecs else len(codecs)
        heavy = bool(s.get("ten_bit") or s.get("hdr") or s.get("dv"))
        return (group, seeders < 2, q, c, heavy, seeders < 5, -seeders)
    return sorted(good, key=key)


def best(sources, cfg):
    ranked = rank(sources, cfg)
    return ranked[0] if ranked else None
