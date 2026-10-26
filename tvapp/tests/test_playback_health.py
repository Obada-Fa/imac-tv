import sys
from pathlib import Path
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from media import health


class Clock:
    now = 0
    def __call__(self):
        return self.now


class MonitorTests(unittest.TestCase):
    def setUp(self):
        self.clock = Clock()
        self.monitor = health.Monitor(self.clock)
        self.position = 0
        self.drops = 0
        self.sample = {"paused-for-cache": False, "seeking": False, "demuxer-cache-duration": 20,
                       "frame-drop-count": 0, "estimated-vf-fps": 24}
        for _ in range(12):
            self.tick()

    def tick(self, cache=False, drop=0, paused=False, **extra):
        self.clock.now += 1
        if not cache and not paused:
            self.position += 1
        self.drops += drop
        sample = dict(self.sample, **{"paused-for-cache": cache, "frame-drop-count": self.drops}, **extra)
        return self.monitor.tick(self.position, paused, sample)

    def stall(self):
        self.assertIsNone(self.tick(cache=True))
        result = self.tick(cache=True)
        self.tick()
        return result

    def test_five_distinct_stalls_trigger_once(self):
        for _ in range(4):
            self.assertIsNone(self.stall())
        self.assertEqual(self.stall(), "bandwidth")
        self.assertIsNone(self.stall())

    def test_single_long_stall_and_no_double_counting(self):
        results = [self.tick(cache=True) for _ in range(25)]
        self.assertEqual(results.count("bandwidth"), 1)
        self.assertEqual(len(self.monitor.stalls), 1)

    def test_old_stalls_expire(self):
        for _ in range(4):
            self.stall()
        for _ in range(121):
            self.tick()
        self.assertIsNone(self.stall())

    def test_initial_loading_pause_and_seeks_are_ignored(self):
        fresh = health.Monitor(self.clock)
        for _ in range(50):
            self.clock.now += 1
            self.assertIsNone(fresh.tick(0, False, {"paused-for-cache": True}))
        self.assertFalse(fresh.started)
        for _ in range(20):
            self.assertIsNone(self.tick(cache=True, paused=True, drop=30))
        self.assertEqual(len(self.monitor.stalls), 0)
        self.position += 100
        for _ in range(5):
            self.assertIsNone(self.tick(cache=True, drop=30, seeking=True))
        self.assertEqual(len(self.monitor.stalls), 0)

    def test_sustained_drops_with_healthy_buffer(self):
        results = [self.tick(drop=5) for _ in range(12)]
        self.assertEqual(results.count("decode"), 1)
        self.assertIsNone(self.tick(drop=10))

    def test_low_or_unknown_buffer_and_isolated_drops_do_not_blame_device(self):
        for value in (None, 0, 1, float("nan")):
            for _ in range(15):
                self.assertIsNone(self.tick(drop=30, **{"demuxer-cache-duration": value}))
        self.assertIsNone(self.tick(drop=30))
        for _ in range(20):
            self.assertIsNone(self.tick())

    def test_sampling_gap_resets_drop_evidence(self):
        for _ in range(8):
            self.tick(drop=5)
        self.clock.now += 30
        self.assertIsNone(self.tick(drop=200))
        self.assertEqual(self.monitor.bad_seconds, 0)

    def test_growing_av_desync_detects_decoder_falling_behind(self):
        results = [self.tick(avsync=1 + i * .2) for i in range(12)]
        self.assertEqual(results.count("decode"), 1)

    def test_constant_av_offset_does_not_trigger_without_dropped_frames(self):
        for _ in range(20):
            self.assertIsNone(self.tick(avsync=1))


def source(key, quality="2160p", gb=20, codec="HEVC", **extra):
    return dict(info_hash=key * 40, file_idx=0, quality=quality, size_bytes=gb * 1e9,
                codec=codec, seeders=20, label=key, **extra)


class AlternativeTests(unittest.TestCase):
    def test_smaller_same_resolution_before_reducing_resolution(self):
        current = source("a")
        ten = source("b", gb=10)
        small = source("c", "1080p", 3)
        self.assertIs(health.alternative(current, [current, small, ten], "bandwidth"), ten)

    def test_prefers_1080_h264_sdr_for_decoding(self):
        current = source("a", hdr=True)
        easy = source("b", "1080p", 5, "H264")
        same4k = source("c", codec="H264")
        hdr = source("d", "1080p", 3, "HEVC", hdr=True)
        self.assertIs(health.alternative(current, [same4k, hdr, easy], "decode"), easy)

    def test_unknown_sizes_can_use_lower_resolution(self):
        self.assertIsNotNone(health.alternative(source("a", gb=0), [source("b", "1080p", 0)], "bandwidth"))
        self.assertIsNone(health.alternative(source("a"), [source("b", gb=0)], "bandwidth"))

    def test_dead_uncached_same_and_previously_tried_sources_are_excluded(self):
        current = source("a")
        dead = dict(source("b", gb=10), seeders=0)
        uncached = source("c", gb=10, direct=True, cached=False, url="https://example.com/c")
        tried = source("d", gb=10)
        self.assertIsNone(health.alternative(current, [current, dead, uncached, tried], "bandwidth", {health.identity(tried)}))

    def test_unknown_cause_can_use_another_same_format_source(self):
        current = source("a", "1080p", 2, "H264")
        other = source("b", "1080p", 2, "H264")
        self.assertIs(health.alternative(current, [current, other], "unplayable"), other)
        self.assertIsNone(health.alternative(current, [other], "decode"))

    def test_bandwidth_does_not_upgrade_resolution(self):
        self.assertIsNone(health.alternative(source("a", "1080p"), [source("b", gb=5)], "bandwidth"))


if __name__ == "__main__":
    unittest.main()
