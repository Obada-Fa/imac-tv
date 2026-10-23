"""Skip-intro / auto-play-next / "still watching?" decisions, kept pure so they can be tested with a fake clock.

The controller feeds `tick()` once a second with what mpv reports and executes the returned event:
  ("skip", end_seconds) · ("play_next",) · ("stop",)
Everything else (OSD text, phone cards) is read from `view()`.
"""
import time

from . import segments

COUNTDOWN = 10          # seconds of "Next episode in N s"
STILL_TIMEOUT = 60      # seconds to answer "Are you still watching?"
TAIL = 15               # no credits data: start the countdown this close to the end
STILL_TAIL = 75         # no credits data: ask "still watching?" this early so there's time to answer
EOF_SLACK = 30          # mpv exiting this close to the end counts as "the episode ended"
SKIP_KINDS = ("intro", "recap")


def settings(cfg):
    """Validated knobs from media.json (bad values fall back to the defaults)."""
    def num(key, default, low, high):
        v = cfg.get(key, default)
        return float(v) if isinstance(v, (int, float)) and not isinstance(v, bool) and low <= v <= high else default
    return {"autoplay_next": cfg.get("autoplay_next", True) is not False,
            "auto_skip_intro": cfg.get("auto_skip_intro", False) is True,
            "still_episodes": int(num("still_watching_episodes", 3, 1, 50)),
            "still_hours": num("still_watching_hours", 2.5, 0.1, 24)}


class Flow:
    def __init__(self, clock=time.monotonic):
        self.clock = clock
        self.cfg = settings({})
        self.count = 0                      # consecutive autoplayed episodes without input
        self.last_input = clock()
        self.begin(None, [], None)

    # --- per-episode setup ---
    def begin(self, next_ep, segs, cfg):
        """A new episode started. `next_ep` = {"season","episode","name"} or None (movies, finale)."""
        if cfg is not None:
            self.cfg = settings(cfg)
        self.next_ep = next_ep
        self.segments = list(segs or [])
        self.state = None                   # None | "upnext" | "still"
        self.deadline = 0.0
        self.dismissed = False              # user cancelled the countdown this episode
        self.confirmed = False              # user answered "continue" this episode
        self.skipped = set()
        self.announced = None
        self._last = None
        self.pos, self.dur = 0.0, 0.0

    def set_segments(self, segs):
        self.segments = list(segs or [])

    def user_started(self):
        """The user picked something to play: that's input."""
        self.note_input()

    def autoplay_started(self):
        self.count += 1

    def note_input(self):
        self.last_input = self.clock()
        self.count = 0

    # --- queries ---
    def need_still(self):
        return (self.count >= self.cfg["still_episodes"] and self.cfg["still_episodes"] > 0) or \
            self.clock() - self.last_input > self.cfg["still_hours"] * 3600

    def eligible(self):
        return bool(self.next_ep) and self.cfg["autoplay_next"] and not self.dismissed

    def segment(self, position):
        """Intro/recap the position is inside (for the Skip button), else None."""
        seg = segments.active(self.segments, position)
        return seg if seg and seg["kind"] in SKIP_KINDS else None

    def view(self, position):
        """What status() shows the phone."""
        seg = self.segment(position)
        left = max(0, int(self.deadline - self.clock() + 0.999)) if self.state else 0
        nxt = self.next_ep or {}
        return {"segment": {"kind": seg["kind"], "end": seg["end"]} if seg else None,
                "upnext": {"name": nxt.get("name") or f"Episode {nxt.get('episode')}", "season": nxt.get("season"),
                           "episode": nxt.get("episode"), "seconds_left": left} if self.state == "upnext" and nxt else None,
                "still_watching": {"seconds_left": left} if self.state == "still" else None}

    # --- the state machine ---
    def tick(self, position, duration, paused=False):
        now = self.clock()
        if self._last is not None and paused and self.state:
            self.deadline += now - self._last   # a paused countdown waits
        self._last = now
        self.pos, self.dur = position, duration
        seg = segments.active(self.segments, position)
        if seg and seg["kind"] in SKIP_KINDS and self.cfg["auto_skip_intro"]:
            key = (seg["kind"], seg["start"])
            if key not in self.skipped and seg["end"] != float("inf"):
                self.skipped.add(key)
                return ("skip", seg["end"])
        if self.state == "upnext":
            return ("play_next",) if now >= self.deadline else None
        if self.state == "still":
            return ("stop",) if now >= self.deadline else None
        if not self.eligible():
            return None
        remaining = duration - position if duration and duration > 0 else None
        in_credits = bool(seg and seg["kind"] == "credits")
        if self.need_still() and not self.confirmed:
            if in_credits or (remaining is not None and remaining <= STILL_TAIL and not self._has_credits()):
                self.state, self.deadline = "still", now + STILL_TIMEOUT
            return None
        if in_credits or (remaining is not None and remaining <= TAIL and not self._has_credits()):
            self.state, self.deadline = "upnext", now + COUNTDOWN
        return None

    def _has_credits(self):
        return any(s["kind"] == "credits" for s in self.segments)

    def on_exit(self, position, duration):
        """mpv went away. Returns the event to run, or None to just return to Details."""
        if self.state == "upnext":
            return ("play_next",)
        near_end = bool(duration) and duration > 0 and duration - position <= EOF_SLACK
        if self.state is None and near_end and self.eligible() and (not self.need_still() or self.confirmed):
            return ("play_next",)
        return None

    # --- answers ---
    def play_now(self):
        if self.state == "upnext":
            return ("play_next",)
        return None

    def cancel(self):
        """Back during the countdown: keep watching this episode."""
        if self.state == "upnext":
            self.state, self.dismissed = None, True
            return True
        return False

    def answer_still(self, keep_going):
        if self.state != "still":
            return None
        self.note_input()
        if not keep_going:
            self.state = None
            return ("stop",)
        self.state, self.confirmed = None, True
        # at/after credits: go now; mid-episode (fallback tail) the normal countdown follows
        return ("play_next",) if self._at_end() else None

    def _at_end(self):
        seg = segments.active(self.segments, self.pos)
        return bool((seg and seg["kind"] == "credits") or (self.dur and self.dur - self.pos <= TAIL))
