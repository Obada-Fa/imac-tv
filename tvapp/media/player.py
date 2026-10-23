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


def mpv_argv(url, *, title, subs, start=0, ipc="/tmp/mpv.sock", slang=None):
    argv = ["mpv", "--fs", f"--input-ipc-server={ipc}", "--force-window=immediate", "--keep-open=no",
            "--idle=no", "--no-terminal", f"--title={title}", f"--force-media-title={title}",
            f"--slang={slang or DEFAULT_SLANG}",
            "--cache=yes", "--demuxer-max-bytes=200MiB", "--demuxer-readahead-secs=90",
            "--cache-pause-initial=yes", "--network-timeout=60",
            "--sub-font=Noto Sans", "--sub-font-size=42", "--sub-border-size=2.5",
            "--osd-font=Inter", "--osd-font-size=34", "--osd-bar-align-y=0.9"]
    for sub in subs or []:
        argv.append("--sub-file=" + sub["url"])
    if start and start > 0:
        argv.append(f"--start={float(start):g}")
    argv += ["--", url]
    return argv
