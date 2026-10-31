import os
from pathlib import Path
import stat
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
_temp = tempfile.TemporaryDirectory(prefix="imactv-history-test-")
os.environ["IMACTV_DATA_DIR"] = _temp.name

from media import history  # noqa: E402

VIDEOS = [{"season": 1, "episode": e} for e in (1, 2, 3)]


def ep(n, **kw):
    base = {"type": "series", "id": "tt1", "video_id": f"tt1:1:{n}", "season": 1, "episode": n, "name": "Show",
            "poster": "http://p", "position": 100, "duration": 1000}
    return dict(base, **kw)


class HistoryTests(unittest.TestCase):
    def setUp(self):
        self._env = os.environ.get("IMACTV_DATA_DIR")
        os.environ["IMACTV_DATA_DIR"] = _temp.name
        p = Path(_temp.name) / "history.json"
        if p.exists():
            p.unlink()

    def tearDown(self):
        os.environ["IMACTV_DATA_DIR"] = self._env

    def test_record_resume_and_permissions(self):
        history.record(ep(1, position=250))
        self.assertEqual(history.resume_position("tt1:1:1"), 250)
        self.assertEqual(stat.S_IMODE((Path(_temp.name) / "history.json").stat().st_mode), 0o600)
        self.assertEqual(history.resume_position("missing"), 0)
        history.record(ep(1, position=0, duration=0, name="", poster=None))
        stored = history.load()["tt1:1:1"]
        self.assertEqual((stored["name"], stored["poster"], stored["duration"]), ("Show", "http://p", 1000))

    def test_finished_threshold_and_no_resume(self):
        history.record(ep(1, position=919))
        self.assertFalse(history.load()["tt1:1:1"]["finished"])
        history.record(ep(1, position=920))
        self.assertTrue(history.load()["tt1:1:1"]["finished"])
        self.assertEqual(history.resume_position("tt1:1:1"), 0)

    def test_continue_watching_filters_and_orders(self):
        history.record(ep(1, position=30, updated=1))
        movie = {"type": "movie", "id": "tt2", "video_id": "tt2", "name": "M", "position": 500, "duration": 5000}
        history.record(dict(movie, updated=3))
        history.record(dict(movie, id="tt3", video_id="tt3", position=4900, updated=4))
        history.record(ep(2, position=200, updated=2))
        ids = [i["video_id"] for i in history.continue_watching(videos=lambda *a: VIDEOS)]
        self.assertEqual(ids, ["tt2", "tt1:1:2"])

    def test_next_episode_after_finished(self):
        history.record(ep(1, position=990, updated=10))
        out = history.continue_watching(videos=lambda k, i: VIDEOS)
        self.assertEqual([(i["video_id"], i["position"]) for i in out], [("tt1:1:2", 0)])
        self.assertEqual(out[0]["poster"], "http://p")
        self.assertEqual(history.continue_watching(videos=lambda k, i: [VIDEOS[0]]), [])
        history.record(ep(3, position=990, updated=11))
        self.assertEqual(history.continue_watching(videos=lambda k, i: VIDEOS), [])  # the last one watched was the finale

    def continued(self):
        videos = [{"season": 1, "episode": e} for e in range(1, 11)] + [{"season": 2, "episode": 1}]
        return [i["video_id"] for i in history.continue_watching(videos=lambda k, i: videos)]

    def test_gap_follows_the_latest_episode_not_the_old_unfinished_one(self):
        history.record(ep(3, position=500, updated=1))
        history.record(ep(8, position=990, updated=2))
        self.assertEqual(self.continued(), ["tt1:1:9"])
        history.record(ep(9, position=400, updated=3))
        self.assertEqual(self.continued(), ["tt1:1:9"])

    def test_gap_when_the_latest_is_unfinished_or_barely_started(self):
        history.record(ep(3, position=500, updated=1))
        history.record(ep(8, position=20, updated=2))
        self.assertEqual(self.continued(), ["tt1:1:8"])

    def test_after_a_season_finale_goes_to_the_next_season(self):
        history.record(ep(3, position=500, updated=1))
        history.record(ep(10, position=990, updated=2))
        self.assertEqual(self.continued(), ["tt1:2:1"])

    def test_other_titles_keep_their_own_cards(self):
        history.record(ep(3, position=500, updated=1))
        history.record(dict(ep(5, position=500, updated=2), id="tt9", video_id="tt9:1:5"))
        self.assertEqual(self.continued(), ["tt9:1:5", "tt1:1:3"])


class NextVideoTests(unittest.TestCase):
    def test_rolls_over_season_and_skips_specials(self):
        videos = [{"season": 0, "episode": 1}, {"season": 1, "episode": 9}, {"season": 1, "episode": 10},
                  {"season": 2, "episode": 1, "name": "Premiere"}, {"season": 2, "episode": 2}]
        self.assertEqual(history.next_video(videos, 1, 10), {"season": 2, "episode": 1, "name": "Premiere"})
        self.assertEqual(history.next_video(videos, 1, 9)["episode"], 10)
        self.assertIsNone(history.next_video(videos, 2, 2))
        self.assertIsNone(history.next_video(None, 1, 1))
