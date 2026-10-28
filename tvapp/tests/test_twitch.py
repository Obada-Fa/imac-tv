import json
import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import twitch
from media import player

TOP = {"data": {"streams": {"edges": [
    {"node": {"id": "1", "title": "Hello", "viewersCount": 1200, "previewImageURL": "http://x/t.jpg",
              "broadcaster": {"login": "Alice", "displayName": "Alice"}, "game": {"displayName": "Chess"}}},
    {"node": {"id": "2", "title": "Bad", "viewersCount": 5, "broadcaster": {"login": "bad name!"}, "game": None}},
    {"node": None},
]}}}


class FakeResponse:
    def __init__(self, payload):
        self.payload = json.dumps(payload).encode()

    def read(self, n=-1):
        return self.payload

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


class TwitchTest(unittest.TestCase):
    def setUp(self):
        twitch.clear_cache()
        os.environ.pop("IMACTV_FAKE_TWITCH", None)

    def test_login_validation(self):
        for good in ("chess", "Some_Name1", "a" * 25, "#chess", "https://www.twitch.tv/Chess?x=1"):
            self.assertRegex(twitch.clean_login(good), r"^[a-z0-9_]{1,25}$")
        for bad in ("", "a" * 26, "two words", "x;rm", "../etc", None, 5, "https://evil.example/chess"):
            with self.assertRaises(twitch.TwitchError):
                twitch.clean_login(bad)

    def test_top_streams_parses_and_caches(self):
        with mock.patch("urllib.request.urlopen", return_value=FakeResponse(TOP)) as op:
            first = twitch.top_streams()
            second = twitch.top_streams()
        self.assertEqual(op.call_count, 1)
        self.assertEqual(first, second)
        self.assertEqual(first, [{"login": "alice", "name": "Alice", "title": "Hello", "game": "Chess",
                                  "viewers": 1200, "thumbnail": "http://x/t.jpg"}])

    def test_network_failure_is_graceful_and_stale_cache_survives(self):
        with mock.patch("urllib.request.urlopen", side_effect=OSError("down")):
            with self.assertRaises(twitch.TwitchError):
                twitch.top_streams()
        with mock.patch("urllib.request.urlopen", return_value=FakeResponse(TOP)):
            twitch.top_streams()
        with mock.patch("urllib.request.urlopen", side_effect=OSError("down")), mock.patch("time.monotonic", return_value=1e9):
            self.assertEqual(len(twitch.top_streams()), 1)

    def test_graphql_errors_raise(self):
        with mock.patch("urllib.request.urlopen", return_value=FakeResponse({"errors": [{"message": "x"}]})):
            with self.assertRaises(twitch.TwitchError):
                twitch.top_streams()

    def test_find_combines_exact_and_search_without_duplicates(self):
        node = {"id": "9", "title": "T", "viewersCount": 3, "game": None, "previewImageURL": ""}
        user = {"data": {"user": {"login": "chess", "displayName": "Chess", "stream": node}}}
        search = {"data": {"searchFor": {"channels": {"edges": [
            {"item": {"login": "chess", "displayName": "Chess", "stream": node}},
            {"item": {"login": "offline", "displayName": "Off", "stream": None}}]}}}}
        with mock.patch("urllib.request.urlopen", side_effect=[FakeResponse(user), FakeResponse(search)]):
            found = twitch.find("chess")
        self.assertEqual([s["login"] for s in found], ["chess"])

    def test_find_never_puts_raw_input_in_the_query(self):
        sent = []

        def fake(req, timeout=0):
            sent.append(req.data.decode())
            return FakeResponse({"data": {"searchFor": {"channels": {"edges": []}}}})
        with mock.patch("urllib.request.urlopen", fake):
            twitch.find('a" } evil { x ')
        self.assertIn('userQuery: ' + json.dumps('a" } evil { x'), json.loads(sent[0])["query"])

    def test_fake_hook(self):
        os.environ["IMACTV_FAKE_TWITCH"] = "1"
        self.assertGreater(len(twitch.top_streams()), 3)
        self.assertEqual([s["login"] for s in twitch.find("lofi")], ["lofigirl"])

    def mpv(self):
        return player.mpv_argv("http://u", title="T", subs=[], start=30, ipc="/tmp/s.sock")

    def test_streamlink_argv(self):
        argv = twitch.streamlink_argv("Chess", mpv_argv=self.mpv(), binary="/usr/bin/streamlink", disable_ads=False)
        self.assertEqual(argv[0], "/usr/bin/streamlink")
        self.assertNotIn("--twitch-disable-ads", argv)
        self.assertIn("https://www.twitch.tv/chess", argv)
        args = argv[argv.index("--player-args") + 1]
        self.assertTrue(args.endswith(" {playerinput}"))
        for want in ("--fs", "--ontop", "--input-ipc-server=/tmp/s.sock"):
            self.assertIn(want, args)
        for unwanted in ("--start=", "http://u", "--cache-pause-initial", "--title="):
            self.assertNotIn(unwanted, args)
        self.assertEqual(argv[argv.index("--player") + 1], "mpv")

    def test_streamlink_argv_old_version_gets_flag(self):
        argv = twitch.streamlink_argv("chess", mpv_argv=self.mpv(), binary="/x/streamlink", disable_ads=True)
        self.assertIn("--twitch-disable-ads", argv)

    def test_missing_streamlink_message(self):
        with mock.patch.object(twitch, "streamlink_path", return_value=None):
            with self.assertRaisesRegex(twitch.TwitchError, "Install streamlink: re-run ./install.sh"):
                twitch.streamlink_argv("chess", mpv_argv=self.mpv())

    def test_bad_login_rejected_before_launch(self):
        with self.assertRaises(twitch.TwitchError):
            twitch.streamlink_argv("a b", mpv_argv=self.mpv(), binary="/x/streamlink", disable_ads=False)


class ControllerTwitchTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from tests import test_controller as tc
        cls.tc, cls.server = tc, tc.server

    def controller(self):
        with mock.patch.object(self.server.threading, "Thread"):
            return self.server.Controller()

    def test_play_twitch_starts_worker_and_buffering_state(self):
        c = self.controller()
        with mock.patch.object(self.server.twitch, "streamlink_path", return_value="/x/streamlink"), \
                mock.patch.object(self.server.twitch, "_supports_disable_ads", return_value=False), \
                mock.patch.object(self.server.threading, "Thread") as thread:
            out = c.action("play_twitch", {"login": "Chess", "title": "Blitz"})
        self.assertEqual(out["channel"], "chess")
        self.assertEqual((c.app, c.phase, c.media["type"], c.media["name"]), ("player", "buffering", "twitch", "Blitz"))
        argv = thread.call_args.kwargs["args"][1]
        self.assertIn("https://www.twitch.tv/chess", argv)

    def test_play_twitch_errors(self):
        c = self.controller()
        with mock.patch.object(self.server.twitch, "streamlink_path", return_value=None):
            with self.assertRaisesRegex(RuntimeError, "Install streamlink"):
                c.action("play_twitch", {"login": "chess"})
        with self.assertRaises(RuntimeError):
            c.action("play_twitch", {"login": "bad name"})
        self.assertEqual(c.app, "home")


if __name__ == "__main__":
    unittest.main()
