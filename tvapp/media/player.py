"""mpv command line for streamed playback. Runtime lifecycle is handled by the controller."""


DEFAULT_SLANG = "ar,ara,nl,dut,nld,en,eng"
ALIASES = {"ara": "ar,ara", "dut": "nl,dut,nld", "eng": "en,eng"}


def slang_arg(langs, default=None):
    """--slang value with the default language first, e.g. [ara, dut, eng] + eng -> en,eng,ar,ara,..."""
    order = list(langs or [])
    if default in order:
        order.remove(default)
        order.insert(0, default)
    return ",".join(ALIASES.get(lang, lang) for lang in order) or DEFAULT_SLANG


def sink_volume(text):
    """`pactl get-sink-volume` output -> first channel's percent, or None."""
    import re
    m = re.search(r"(\d+)%", text or "")
    return int(m.group(1)) if m else None


def audio_warning(sinks_text, default_sink):
    """`pactl list short sinks` + default sink name -> warning text when an HDMI sink exists but isn't the default."""
    names = [line.split("\t")[1] if "\t" in line else (line.split() + ["", ""])[1] for line in (sinks_text or "").splitlines() if line.strip()]
    hdmi = [n for n in names if "hdmi" in n.lower()]
    if hdmi and "hdmi" not in (default_sink or "").lower():
        return f"Default audio output is {default_sink or 'unknown'}, not HDMI ({hdmi[0]}); TV sound may be silent"
    return None


def volume_osd(percent, muted=False, width=20):
    """Text bar for mpv's show-text."""
    if percent is None:
        return "Volume unavailable"
    if muted:
        return "Muted"
    filled = round(max(0, min(100, percent)) * width / 100)
    return f"Volume {'█' * filled}{'░' * (width - filled)} {percent}%"


def _rounded_rect(w, h, r):
    """ASS drawing of a rounded rectangle at the origin."""
    k = r * 0.45  # distance from a corner to its Bezier control points
    return (f"m {r} 0 l {w - r} 0 b {w - k} 0 {w} {k} {w} {r} l {w} {h - r} b {w} {h - k} {w - k} {h} {w - r} {h} "
            f"l {r} {h} b {k} {h} 0 {h - k} 0 {h - r} l 0 {r} b 0 {k} {k} 0 {r} 0")


def skip_button_ass(kind):
    """Skip button for mpv's osd-overlay (ass-events, 1920x1080): Home's dark glass with the focused ice edge."""
    w, h, r = 272, 80, 18
    x, y = 1920 - 72 - w, 1080 - 112 - h
    shape = _rounded_rect(w, h, r)
    chevrons = "".join(f"m {cx} 0 l {cx + 12} 12 l {cx} 24 l {cx + 5} 24 l {cx + 17} 12 l {cx + 5} 0 " for cx in (0, 15))
    place = f"\\an7\\pos({x},{y})\\shad0"
    return "\n".join((
        f"{{{place}\\bord0\\blur14\\1c&H000000&\\1a&H78&\\p1}}{shape}{{\\p0}}",
        f"{{{place}\\bord2.5\\1c&H2E1F14&\\1a&H26&\\3c&HF8E8C0&\\3a&H00&\\p1}}{shape}{{\\p0}}",
        f"{{\\an4\\pos({x + 40},{y + h / 2:g})\\bord0\\shad0\\fnInter\\fs34\\1c&HFFFFFF&\\1a&H00&}}Skip {kind.capitalize()}",
        f"{{\\an4\\pos({x + w - 70},{y + h / 2:g})\\bord0\\shad0\\1c&HF8E8C0&\\1a&H00&\\p1}}{chevrons.strip()}{{\\p0}}",
    ))


def mpv_argv(url, *, title, subs, start=0, ipc="/tmp/mpv.sock", slang=None, log_file=None):
    # --force-window=no: the window maps with the first video frame, so TV Home's loading screen
    # shows until then. --ontop: GNOME opens new windows behind the focused fullscreen Home.
    argv = ["mpv", "--fs", "--ontop", f"--input-ipc-server={ipc}", "--force-window=no", "--keep-open=no",
            "--idle=no", "--no-terminal", "--volume=100", f"--title={title}", f"--force-media-title={title}",
            f"--slang={slang or DEFAULT_SLANG}",
            "--hwdec=auto-safe", "--cache=yes", "--demuxer-max-bytes=200MiB", "--demuxer-readahead-secs=90",
            "--cache-pause-initial=yes", "--network-timeout=60",
            "--sub-font=Noto Sans", "--sub-font-size=42", "--sub-border-size=2.5",
            "--osd-font=Inter", "--osd-font-size=34", "--osd-bar-align-y=0.9"]
    for sub in subs or []:
        argv.append("--sub-file=" + sub["url"])
    if log_file:
        argv.append(f"--log-file={log_file}")
    if start and start > 0:
        argv.append(f"--start={float(start):g}")
    argv += ["--", url]
    return argv
