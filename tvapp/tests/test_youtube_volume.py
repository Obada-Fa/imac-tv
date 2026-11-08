from pathlib import Path
import sys
import unittest
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import server
import youtube_volume


def server_frame(text, fin=True, opcode=0x1):
    payload = text.encode()
    size = len(payload)
    head = bytes([(0x80 if fin else 0) | opcode])
    if size < 126:
        return head + bytes([size]) + payload
    return head + bytes([126]) + size.to_bytes(2, "big") + payload


class FakeSock:
    def __init__(self, chunks):
        self.chunks = list(chunks)

    def recv(self, _size):
        return self.chunks.pop(0) if self.chunks else b""


class FramesTests(unittest.TestCase):
    def test_messages_stay_whole_however_reads_split_them(self):
        long = "x" * 300
        data = server_frame("one") + server_frame("two") + server_frame(long) + bytes([0x89, 0])
        data += server_frame("thr", fin=False) + server_frame("ee", opcode=0x0)
        frames = youtube_volume.Frames(FakeSock([data[5:9], data[9:40], data[40:]]), data[:5])
        self.assertEqual([frames.next() for _ in range(4)], [b"one", b"two", long.encode(), b"three"])
        with self.assertRaises(OSError):
            frames.next()

    def test_close_frame_ends_the_link(self):
        with self.assertRaises(OSError):
            youtube_volume.Frames(FakeSock([]), bytes([0x88, 0])).next()


class ChangeTests(unittest.TestCase):
    def test_reports_become_a_level_or_a_mute(self):
        self.assertEqual(youtube_volume.parse_change('{"level": 45}'), {"level": 45})
        self.assertEqual(youtube_volume.parse_change('{"level": 140.4}'), {"level": 100})
        self.assertEqual(youtube_volume.parse_change('{"muted": true}'), {"muted": True})
        for bad in (None, "", "[]", '{"level": true}', '{"level": "50"}', '{"muted": 1}', "{}"):
            self.assertIsNone(youtube_volume.parse_change(bad), bad)


class LinkTests(unittest.TestCase):
    def link(self, current=(40, False)):
        changes = []
        link = youtube_volume.Link(changes.append, lambda: current)
        link._page = "PAGE"
        sent = []
        link._send = lambda method, params=None: sent.append((method, params)) or True
        return link, changes, sent

    def test_phone_volume_reaches_the_callback(self):
        link, changes, _sent = self.link()
        link.handle({"method": "Runtime.bindingCalled", "params": {"name": youtube_volume.BINDING, "payload": '{"level": 25}'}})
        link.handle({"method": "Runtime.bindingCalled", "params": {"name": "other", "payload": '{"level": 90}'}})
        link.handle({"method": "Runtime.bindingCalled", "params": {"name": youtube_volume.BINDING, "payload": "junk"}})
        self.assertEqual(changes, [{"level": 25}])

    def test_a_failing_callback_keeps_the_link(self):
        link = youtube_volume.Link(mock.Mock(side_effect=RuntimeError("pactl")), lambda: (40, False))
        with self.assertLogs("imactv.youtube_volume", "ERROR"):
                link.handle({"method": "Runtime.bindingCalled", "params": {"name": youtube_volume.BINDING, "payload": '{"muted": true}'}})

    def test_each_new_page_gets_the_lock_hook_and_the_system_level(self):
        link, _changes, sent = self.link((35, True))
        context = {"id": 7, "auxData": {"isDefault": True, "frameId": "PAGE"}}
        link.handle({"method": "Runtime.executionContextCreated", "params": {"context": context}})
        self.assertEqual(sent[0][0], "Runtime.evaluate")
        self.assertEqual(sent[0][1]["contextId"], 7)
        self.assertIn(youtube_volume.LOCK_LEVEL, sent[0][1]["expression"])
        self.assertIn(youtube_volume.HOOK, sent[0][1]["expression"])
        self.assertIn("__imactvSync(35, true)", sent[1][1]["expression"])

    def test_frames_and_isolated_worlds_are_left_alone(self):
        link, _changes, sent = self.link()
        for aux in ({"isDefault": True, "frameId": "IFRAME"}, {"isDefault": False, "frameId": "PAGE"}):
            link.handle({"method": "Runtime.executionContextCreated", "params": {"context": {"id": 3, "auxData": aux}}})
        self.assertEqual(sent, [])

    def test_push_is_quiet_while_youtube_is_not_connected(self):
        link = youtube_volume.Link(lambda _change: None, lambda: (40, False))
        link.push(50, False)
        link.push(None, None)

    def test_ensure_connects_only_to_a_running_youtube(self):
        link = youtube_volume.Link(lambda _change: None, lambda: (40, False))
        with mock.patch.object(youtube_volume, "youtube_page", return_value=None), \
                mock.patch.object(youtube_volume.threading, "Thread") as thread:
            link.ensure()
        thread.assert_not_called()
        with mock.patch.object(youtube_volume, "youtube_page", return_value={"id": "PAGE"}), \
                mock.patch.object(youtube_volume.threading, "Thread") as thread:
            link.ensure()
        thread.assert_called_once()
        self.assertEqual(thread.call_args.kwargs["args"], ("PAGE",))

    def test_youtube_page_prefers_the_tv_page(self):
        targets = [{"id": "w", "type": "service_worker", "url": "https://www.youtube.com/sw.js"},
                   {"id": "a", "type": "page", "url": "devtools://x"},
                   {"id": "b", "type": "page", "url": "https://www.youtube.com/tv#/watch"}]
        with mock.patch.object(youtube_volume, "_targets", return_value=targets):
            self.assertEqual(youtube_volume.youtube_page()["id"], "b")
        with mock.patch.object(youtube_volume, "_targets", side_effect=OSError):
            self.assertIsNone(youtube_volume.youtube_page())


class ServerTests(unittest.TestCase):
    def controller(self):
        with mock.patch.object(server.threading, "Thread"):
            c = server.Controller()
        c._volume_osd = mock.Mock()
        c.youtube_link = mock.Mock()
        return c

    def test_youtube_volume_becomes_the_system_volume_with_the_island(self):
        c = self.controller()
        with mock.patch.object(server, "command") as run, \
                mock.patch.object(c, "_system_volume", return_value=(25, False)):
            c._youtube_volume_asked({"level": 25})
            c._youtube_volume_asked({"muted": True})
        self.assertEqual([call.args[0] for call in run.call_args_list],
                         [["pactl", "set-sink-volume", "@DEFAULT_SINK@", "25%"],
                          ["pactl", "set-sink-mute", "@DEFAULT_SINK@", "1"]])
        self.assertEqual(c._volume_osd.call_args_list, [mock.call(25, False), mock.call(25, False)])
        c.youtube_link.push.assert_not_called()   # no echo back to the phone

    def test_tv_volume_and_mute_go_to_youtube_too(self):
        c = self.controller()
        with mock.patch.object(server, "command"), \
                mock.patch.object(c, "_system_volume", return_value=(40, False)):
            c.action("volume", {"direction": 1})
            c.action("mute", {})
        self.assertEqual(c.youtube_link.push.call_args_list, [mock.call(45, False), mock.call(40, False)])


if __name__ == "__main__":
    unittest.main()


class YoutubeSearchTests(unittest.TestCase):
    def test_query_goes_to_youtubes_command_dispatcher_safely(self):
        import json
        import youtube_volume
        query = "كونان' + alert(1) + '"
        with mock.patch.object(youtube_volume, "youtube_page", return_value={"id": "T1"}), \
                mock.patch.object(youtube_volume.browser, "_page_command") as cmd:
            self.assertTrue(youtube_volume.search(query))
        js = cmd.call_args.args[2]["expression"]
        self.assertIn("const query = " + json.dumps(query) + ";", js)   # a string, not code
        # A changed #/search address is ignored by an open YouTube TV page (seen in real VacuumTube 2.0.1).
        self.assertIn("resolveCommand({searchEndpoint: {query}})", js)
        with mock.patch.object(youtube_volume, "youtube_page", return_value=None):
            self.assertFalse(youtube_volume.search("x"))

    def test_search_area_includes_the_mic_button(self):
        import youtube_volume
        self.assertIn("ytlr-search-voice", youtube_volume.SEARCH_AREA)   # where focus lands from the side menu

    def test_search_focus_reports_the_search_keyboard(self):
        import youtube_volume
        reply = {"result": {"value": {"token": "123.4-2", "query": "ناروتو"}}}
        with mock.patch.object(youtube_volume, "youtube_page", return_value={"id": "T1"}), \
                mock.patch.object(youtube_volume.browser, "_page_command", return_value=reply) as cmd:
            self.assertEqual(youtube_volume.search_focus(), {"focus": "T1:123.4-2", "query": "ناروتو"})
        self.assertIn("ytlr-search-keyboard", cmd.call_args.args[2]["expression"])
        self.assertTrue(cmd.call_args.args[2]["returnByValue"])

    def test_search_focus_is_none_away_from_search(self):
        import youtube_volume
        with mock.patch.object(youtube_volume, "youtube_page", return_value=None):
            self.assertIsNone(youtube_volume.search_focus())
        with mock.patch.object(youtube_volume, "youtube_page", return_value={"id": "T1"}):
            for reply in ({"result": {"value": None}}, {"result": {"value": "x"}}, {}):
                with mock.patch.object(youtube_volume.browser, "_page_command", return_value=reply):
                    self.assertIsNone(youtube_volume.search_focus())
            with mock.patch.object(youtube_volume.browser, "_page_command", side_effect=OSError):
                self.assertIsNone(youtube_volume.search_focus())
