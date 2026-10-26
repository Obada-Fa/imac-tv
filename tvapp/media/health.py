"""Playback health and safer source choices. No I/O; the controller supplies mpv samples."""
from collections import deque
import math
import time


def number(value, default=0):
    return value if isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value) else default


class Monitor:
    """Count distinct stalls, and sustained frame loss while data is available."""
    def __init__(self, clock=time.monotonic):
        self.clock = clock
        self.grace = clock() + 10
        self.last = None
        self.started = False
        self.stalls = deque()
        self.stall_start = None
        self.stall_counted = False
        self.bad_seconds = 0
        self.reported = set()

    def tick(self, position, paused, sample):
        now = self.clock()
        drops = number(sample.get("frame-drop-count")) + number(sample.get("decoder-frame-drop-count"))
        sync = abs(number(sample.get("avsync")))
        previous = self.last
        self.last = (now, position, drops, sync)
        dt = now - previous[0] if previous else 0
        if not self.started:
            if previous and position > previous[1] and not paused and sample.get("paused-for-cache") is False and sample.get("seeking") is not True:
                self.started = True
                self.grace = now + 10
            else:
                return None  # initial loading is not a playback stall
        jumped = previous and abs(position - previous[1]) > max(3, dt * 2)
        if paused or sample.get("seeking") is True or jumped or dt > 3:
            self.grace = now + 5
        if now < self.grace or not previous or dt <= 0 or dt > 3:
            self.stall_start = None
            self.stall_counted = False
            self.bad_seconds = 0
            return None
        while self.stalls and now - self.stalls[0] > 120:
            self.stalls.popleft()
        if sample.get("paused-for-cache") is True:
            if self.stall_start is None:
                self.stall_start = now
            if now - self.stall_start >= 1 and not self.stall_counted:
                self.stalls.append(now)
                self.stall_counted = True
            self.bad_seconds = 0
            if len(self.stalls) >= 5 or now - self.stall_start >= 20:
                return self._report("bandwidth")
            return None
        self.stall_start, self.stall_counted = None, False
        # Missing buffer telemetry must never be treated as evidence of a healthy buffer.
        buffered = number(sample.get("demuxer-cache-duration")) >= 3
        lost = max(0, drops - previous[2])
        fps = number(sample.get("estimated-vf-fps"), 24) or 24
        struggling = lost >= max(3, fps * dt * 0.10) or (sync >= .75 and sync - previous[3] > .05 * dt)
        self.bad_seconds = self.bad_seconds + dt if buffered and struggling else 0
        if self.bad_seconds >= 10:
            return self._report("decode")
        return None

    def _report(self, reason):
        if reason not in self.reported:
            self.reported.add(reason)
            return reason
        return None


def identity(src):
    """Internal only: direct URLs may contain credentials."""
    if src.get("url"):
        return ("url", src["url"])
    return ("torrent", src.get("info_hash"), src.get("file_idx"))


def resolution(src):
    return {"2160p": 2160, "1080p": 1080, "720p": 720, "480p": 480}.get(src.get("quality"), 0)


def alternative(current, ranked, reason, excluded=()):
    """Prefer a meaningful reduction; never suggest a known-dead or already tried source."""
    blocked = set(excluded) | {identity(current)}
    candidates = [s for s in ranked if identity(s) not in blocked
                  and (s.get("cached") or (not s.get("direct") and s.get("seeders", 0) >= 2))]
    cq, size = resolution(current), current.get("size_bytes", 0)
    if reason == "bandwidth":
        candidates = [s for s in candidates if (size and 0 < s.get("size_bytes", 0) <= size * .75
                                                and (not cq or 0 < resolution(s) <= cq))
                      or (not size and cq and 0 < resolution(s) < cq)]
        def key(s):
            return (resolution(s) != cq, not s.get("cached"),
                    abs(s.get("size_bytes", 0) - size * .5) if size else -resolution(s))
    else:
        # A lower resolution or a known H.264 SDR encode is easier on older devices.
        def easier(s):
            q = resolution(s)
            simple = s.get("codec") == "H264" and not any(s.get(k) for k in ("hdr", "dv", "ten_bit"))
            return (cq and 0 < q < cq) or (q and (not cq or q <= cq) and simple
                    and (current.get("codec") != "H264" or any(current.get(k) for k in ("hdr", "dv", "ten_bit"))))
        candidates = [s for s in candidates if easier(s)]
        def key(s):
            q = resolution(s)
            return (q > 1080, s.get("codec") != "H264", bool(s.get("hdr") or s.get("dv") or s.get("ten_bit")),
                    -q, not s.get("cached"))
        if reason == "unplayable" and not candidates:
            candidates = [s for s in ranked if identity(s) not in blocked
                          and (s.get("cached") or (not s.get("direct") and s.get("seeders", 0) >= 2))]
    return min(candidates, key=key) if candidates else None


def message(reason):
    return {
        "bandwidth": ("Playback keeps buffering", "This source isn't arriving fast enough. Wi-Fi, the server or torrent peers may be slow. Try a smaller version."),
        "decode": ("This video appears too demanding", "Video is falling behind or dropping frames even though data is buffered. This device may play a lower resolution or simpler format more smoothly."),
        "unplayable": ("This source could not play", "The player could not continue with this source. Try another version; the cause may be the format, the source or the connection."),
    }[reason]
