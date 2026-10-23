from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from media import upnext  # noqa: E402

NEXT = {"season": 1, "episode": 3, "name": "The Third"}
SEGS = [{"kind": "intro", "start": 10, "end": 40, "source": "chapters"},
        {"kind": "recap", "start": 0, "end": 8, "source": "theintrodb"},
        {"kind": "credits", "start": 1300, "end": 1400, "source": "chapters"}]


class Clock:
    def __init__(self):
        self.t = 1000.0

    def __call__(self):
        return self.t


def flow(segs=SEGS, nxt=NEXT, cfg=None, clock=None):
    clock = clock or Clock()
    f = upnext.Flow(clock)
    f.begin(nxt, segs, cfg or {})
    return f, clock


class SettingsTests(unittest.TestCase):
    def test_defaults_and_bad_values(self):
        self.assertEqual(upnext.settings({}), {"autoplay_next": True, "auto_skip_intro": False, "still_episodes": 3, "still_hours": 2.5})
        got = upnext.settings({"autoplay_next": False, "auto_skip_intro": True, "still_watching_episodes": "x", "still_watching_hours": 99})
        self.assertEqual((got["autoplay_next"], got["auto_skip_intro"], got["still_episodes"], got["still_hours"]), (False, True, 3, 2.5))

    def test_media_config_defaults_exist(self):
        from media import config
        for key in ("autoplay_next", "auto_skip_intro", "still_watching_episodes", "still_watching_hours"):
            self.assertIn(key, config.DEFAULTS)


class SegmentTests(unittest.TestCase):
    def test_skip_window_over_timeline(self):
        f, _ = flow()
        seen = [(p, (f.segment(p) or {}).get("kind")) for p in (0, 7.9, 8, 9.9, 10, 39.9, 40, 100, 1350)]
        self.assertEqual(seen, [(0, "recap"), (7.9, "recap"), (8, None), (9.9, None), (10, "intro"), (39.9, "intro"),
                                (40, None), (100, None), (1350, None)])  # credits are not skippable here

    def test_view_exposes_segment_end(self):
        f, _ = flow()
        self.assertEqual(f.view(20)["segment"], {"kind": "intro", "end": 40})
        self.assertIsNone(f.view(100)["segment"])

    def test_auto_skip_only_when_enabled_and_once(self):
        f, _ = flow()
        self.assertIsNone(f.tick(20, 1400))
        f, _ = flow(cfg={"auto_skip_intro": True})
        self.assertEqual(f.tick(20, 1400), ("skip", 40))
        self.assertIsNone(f.tick(21, 1400))
        self.assertEqual(f.tick(3, 1400), ("skip", 8))


class UpNextTests(unittest.TestCase):
    def test_countdown_starts_at_credits_and_expires(self):
        f, c = flow()
        self.assertIsNone(f.tick(1000, 1400))
        self.assertIsNone(f.view(1000)["upnext"])
        self.assertIsNone(f.tick(1300, 1400))
        up = f.view(1300)["upnext"]
        self.assertEqual((up["name"], up["season"], up["episode"], up["seconds_left"]), ("The Third", 1, 3, 10))
        c.t += 4
        self.assertIsNone(f.tick(1304, 1400))
        self.assertEqual(f.view(1304)["upnext"]["seconds_left"], 6)
        c.t += 6
        self.assertEqual(f.tick(1310, 1400), ("play_next",))

    def test_fallback_without_credits_starts_near_end(self):
        f, c = flow(segs=[])
        self.assertIsNone(f.tick(1300, 1400))
        self.assertIsNone(f.view(1300)["upnext"])
        f.tick(1386, 1400)
        self.assertIsNotNone(f.view(1386)["upnext"])

    def test_cancel_keeps_watching_and_does_not_retrigger(self):
        f, c = flow()
        f.tick(1300, 1400)
        self.assertTrue(f.cancel())
        self.assertIsNone(f.view(1300)["upnext"])
        c.t += 30
        self.assertIsNone(f.tick(1330, 1400))
        self.assertIsNone(f.on_exit(1400, 1400))   # cancelled: EOF goes to Details
        self.assertFalse(f.cancel())

    def test_play_now(self):
        f, _ = flow()
        self.assertIsNone(f.play_now())
        f.tick(1300, 1400)
        self.assertEqual(f.play_now(), ("play_next",))

    def test_paused_countdown_waits(self):
        f, c = flow()
        f.tick(1300, 1400)
        for _ in range(20):
            c.t += 1
            self.assertIsNone(f.tick(1301, 1400, paused=True))
        self.assertEqual(f.view(1301)["upnext"]["seconds_left"], 10)

    def test_no_next_episode_or_disabled(self):
        for kw in ({"nxt": None}, {"cfg": {"autoplay_next": False}}):
            f, _ = flow(**kw)
            f.tick(1300, 1400)
            self.assertIsNone(f.view(1300)["upnext"])
            self.assertIsNone(f.on_exit(1400, 1400))

    def test_mpv_exit_rolls_over_near_the_end_only(self):
        f, _ = flow(segs=[])
        self.assertEqual(f.on_exit(1390, 1400), ("play_next",))
        self.assertIsNone(f.on_exit(600, 1400))
        f.tick(1390, 1400)
        self.assertEqual(f.on_exit(1399, 1400), ("play_next",))


class StillWatchingTests(unittest.TestCase):
    def test_asks_before_the_fourth_autoplay(self):
        f, c = flow()
        for _ in range(3):
            f.autoplay_started()
        f.begin(NEXT, SEGS, {})
        f.tick(1300, 1400)
        self.assertIsNone(f.view(1300)["upnext"])
        self.assertEqual(f.view(1300)["still_watching"]["seconds_left"], 60)

    def test_two_autoplays_do_not_ask(self):
        f, _ = flow()
        f.autoplay_started(); f.autoplay_started()
        f.tick(1300, 1400)
        self.assertIsNotNone(f.view(1300)["upnext"])

    def test_asks_after_long_idle(self):
        f, c = flow()
        c.t += 2.5 * 3600 + 1
        f.tick(1300, 1400)
        self.assertIsNotNone(f.view(1300)["still_watching"])

    def test_input_resets_counter_and_clock(self):
        f, c = flow()
        for _ in range(5):
            f.autoplay_started()
        c.t += 3 * 3600
        f.note_input()
        f.tick(1300, 1400)
        self.assertIsNotNone(f.view(1300)["upnext"])
        self.assertIsNone(f.view(1300)["still_watching"])

    def test_no_answer_stops(self):
        f, c = flow()
        c.t += 3 * 3600
        f.tick(1300, 1400)
        c.t += 59
        self.assertIsNone(f.tick(1301, 1400))
        c.t += 2
        self.assertEqual(f.tick(1302, 1400), ("stop",))

    def test_continue_plays_at_credits_and_stop_stops(self):
        f, c = flow()
        c.t += 3 * 3600
        f.tick(1300, 1400)
        self.assertEqual(f.answer_still(True), ("play_next",))
        self.assertIsNone(f.answer_still(True))
        g, c = flow()
        c.t += 3 * 3600
        g.tick(1300, 1400)
        self.assertEqual(g.answer_still(False), ("stop",))

    def test_fallback_asks_early_and_continue_waits_for_the_tail(self):
        f, c = flow(segs=[])
        c.t += 3 * 3600
        f.tick(1330, 1400)
        self.assertIsNotNone(f.view(1330)["still_watching"])
        self.assertIsNone(f.answer_still(True))     # 70 s left: let the episode finish
        f.tick(1340, 1400)
        self.assertIsNone(f.view(1340)["still_watching"])
        f.tick(1390, 1400)
        self.assertIsNotNone(f.view(1390)["upnext"])  # confirmed once: no second question


if __name__ == "__main__":
    unittest.main()
