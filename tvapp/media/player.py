"""mpv command line for streamed playback. Runtime lifecycle is handled by the controller."""


def mpv_argv(url, *, title, subs, start=0, ipc="/tmp/mpv.sock"):
    argv = ["mpv", "--fs", f"--input-ipc-server={ipc}", "--force-window=immediate", "--keep-open=no",
            "--idle=no", "--no-terminal", f"--title={title}", f"--force-media-title={title}",
            "--slang=ar,ara,nl,dut,nld,en,eng",
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
