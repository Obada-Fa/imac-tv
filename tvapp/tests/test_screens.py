from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
_temp = tempfile.TemporaryDirectory(prefix="imactv-screens-test-")

try:
    import screens
except Exception as exc:  # no PyGObject / GTK typelibs
    screens = None
    SKIP = str(exc)


@unittest.skipIf(screens is None, "GTK bindings unavailable")
class GridMoveTests(unittest.TestCase):
    xs = [[100, 300, 500, 700], [], [120, 420]]

    def test_horizontal_clamps(self):
        self.assertEqual(screens.grid_move(self.xs, (0, 0), "Left"), (0, 0))
        self.assertEqual(screens.grid_move(self.xs, (0, 3), "Right"), (0, 3))
        self.assertEqual(screens.grid_move(self.xs, (0, 1), "Right"), (0, 2))

    def test_vertical_skips_empty_rows_and_uses_nearest_x(self):
        self.assertEqual(screens.grid_move(self.xs, (0, 3), "Down"), (2, 1))
        self.assertEqual(screens.grid_move(self.xs, (0, 0), "Down"), (2, 0))
        self.assertEqual(screens.grid_move(self.xs, (2, 1), "Up"), (0, 2))

    def test_edges_stay_and_lost_focus_recovers(self):
        self.assertEqual(screens.grid_move(self.xs, (0, 2), "Up"), (0, 2))
        self.assertEqual(screens.grid_move(self.xs, (2, 0), "Down"), (2, 0))
        self.assertEqual(screens.grid_move(self.xs, None, "Down"), (0, 0))
        self.assertEqual(screens.grid_move([[], []], None, "Down"), None)
        self.assertEqual(screens.grid_move([[], [5]], None, "Left"), (1, 0))


@unittest.skipIf(screens is None, "GTK bindings unavailable")
class LogicTests(unittest.TestCase):
    def test_fresh_request(self):
        now = 1000.0
        ok = {"screen": "search", "ts": 990.0}
        self.assertTrue(screens.fresh_request(ok, 0, now))
        self.assertFalse(screens.fresh_request(ok, 990.0, now))  # already handled
        self.assertFalse(screens.fresh_request({"screen": "home", "ts": 900.0}, 0, now))  # stale
        self.assertFalse(screens.fresh_request({"screen": "home"}, 0, now))
        self.assertFalse(screens.fresh_request({"ts": 999.0}, 0, now))
        self.assertFalse(screens.fresh_request(None, 0, now))
        self.assertFalse(screens.fresh_request({"screen": "home", "ts": True}, 0, now))

    def test_stream_ref_only_int_file_idx(self):
        self.assertEqual(screens.stream_ref({"info_hash": "h", "file_idx": 2}), {"info_hash": "h", "file_idx": 2})
        self.assertEqual(screens.stream_ref({"info_hash": "h", "file_idx": None}), {"info_hash": "h"})
        self.assertEqual(screens.stream_ref({"info_hash": "h", "file_idx": "3"}), {"info_hash": "h"})
        self.assertEqual(screens.stream_ref({"info_hash": "h", "file_idx": True}), {"info_hash": "h"})

    def test_fmt_time(self):
        self.assertEqual(screens.fmt_time(754), "12:34")
        self.assertEqual(screens.fmt_time(3723), "1:02:03")
        self.assertEqual(screens.fmt_time(None), "0:00")

    def test_default_episode(self):
        vids = [{"season": 0, "episode": 1}, {"season": 1, "episode": 1}, {"season": 1, "episode": 2}, {"season": 2, "episode": 1}]
        self.assertEqual(screens.pick_default_episode(vids, {}, "tt1"), (1, 1))
        unfinished = {"tt1:1:2": {"id": "tt1", "season": 1, "episode": 2, "finished": False, "updated": 5}}
        self.assertEqual(screens.pick_default_episode(vids, unfinished, "tt1"), (1, 2))
        done = {"tt1:1:2": {"id": "tt1", "season": 1, "episode": 2, "finished": True, "updated": 5}}
        self.assertEqual(screens.pick_default_episode(vids, done, "tt1"), (2, 1))
        other = {"tt9:1:2": {"id": "tt9", "season": 1, "episode": 2, "finished": False, "updated": 5}}
        self.assertEqual(screens.pick_default_episode(vids, other, "tt1"), (1, 1))
        self.assertIsNone(screens.pick_default_episode([], {}, "tt1"))

    def test_resume_seconds(self):
        e = {"tt1": {"position": 754, "finished": False}, "tt2": {"position": 30}, "tt3": {"position": 900, "finished": True}}
        self.assertEqual(screens.resume_seconds(e, "tt1"), 754)
        self.assertEqual(screens.resume_seconds(e, "tt2"), 0)
        self.assertEqual(screens.resume_seconds(e, "tt3"), 0)
        self.assertEqual(screens.resume_seconds(e, "nope"), 0)

    def test_continue_items_fallback_reads_history_file(self):
        import json
        saved, saved_data = screens.history, screens.DATA
        screens.history, screens.DATA = None, Path(_temp.name)
        try:
            (Path(_temp.name) / "history.json").write_text(json.dumps({
                "tt1": {"type": "movie", "id": "tt1", "name": "A", "position": 600, "duration": 6000, "finished": False, "updated": 2},
                "tt2": {"type": "movie", "id": "tt2", "name": "B", "position": 10, "duration": 6000, "updated": 3},
                "tt3:1:2": {"type": "series", "id": "tt3", "name": "C", "season": 1, "episode": 2, "position": 100, "duration": 1000, "updated": 4}}))
            items = screens.continue_items()
            self.assertEqual([i["id"] for i in items], ["tt3", "tt1"])
            self.assertEqual(items[0]["subtitle"].split(" · ")[0], "S1 E2")
            self.assertAlmostEqual(items[1]["progress"], 0.1)
        finally:
            screens.history, screens.DATA = saved, saved_data

    def test_item_from_meta(self):
        item = screens.item_from_meta({"id": "tt1", "name": "X", "genre": ["Drama"], "releaseInfo": "2020"}, "movie")
        self.assertEqual((item["id"], item["type"], item["genres"], item["year"]), ("tt1", "movie", ["Drama"], "2020"))
        self.assertIsNone(screens.item_from_meta({"name": "no id"}))


if __name__ == "__main__":
    unittest.main()
