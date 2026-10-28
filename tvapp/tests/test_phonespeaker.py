import io
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import phonespeaker


class FakeProc:
    def __init__(self):
        self.stdout = io.BytesIO(b"\x01\x02" * 100)
        self.terminated = False

    def terminate(self):
        self.terminated = True

    def wait(self, timeout=None):
        return 0


class PhoneSpeakerTest(unittest.TestCase):
    def setUp(self):
        self.calls = []
        self.default = "tv_sink"
        self.procs = []
        self.discord_sink = "1"

    def pactl(self, *args, timeout=3):
        self.calls.append(args)
        if args[0] == "get-default-sink":
            return self.default
        if args[0] == "set-default-sink":
            self.default = args[1]
            return ""
        if args[0] == "load-module":
            return "42"
        if args[0] == "list" and args[2] == "sinks":
            return "1\ttv_sink\n5\timactv_phone_mic\n9\t" + phonespeaker.SINK
        if args[0] == "list" and args[2] == "sink-inputs":
            # 7 = Discord, 8 = the phone mic's own feed into its private sink
            return "7\t%s\t2\tdiscord\ts16le\n8\t5\t3\tpacat\ts16le" % self.discord_sink
        return ""

    def popen(self, argv, **kw):
        self.argv = argv
        proc = FakeProc()
        self.procs.append(proc)
        return proc

    def make(self):
        return phonespeaker.PhoneSpeaker(self.pactl, self.popen)

    def test_becomes_default_output_and_moves_running_streams(self):
        speaker = self.make()
        read, stop = speaker.start()
        self.assertEqual(self.default, phonespeaker.SINK)
        self.assertIn(("move-sink-input", "7", phonespeaker.SINK), self.calls)
        self.assertNotIn(("move-sink-input", "8", phonespeaker.SINK), self.calls)
        self.assertIn(f"--device={phonespeaker.SINK}.monitor", self.argv)
        self.assertTrue(speaker.active)
        self.assertTrue(read())
        stop()

    def test_stop_restores_previous_output(self):
        speaker = self.make()
        _, stop = speaker.start()
        self.discord_sink = "9"  # Discord now plays on the phone
        stop()
        self.assertEqual(self.default, "tv_sink")
        self.assertIn(("move-sink-input", "7", "tv_sink"), self.calls)
        # The mic feed is never pushed to the TV speakers (the user heard themselves).
        self.assertNotIn(("move-sink-input", "8", "tv_sink"), self.calls)
        self.assertIn(("unload-module", "42"), self.calls)
        self.assertTrue(self.procs[0].terminated)
        self.assertFalse(speaker.active)

    def test_newer_stream_replaces_older_without_losing_previous(self):
        speaker = self.make()
        _, stop_old = speaker.start()
        _, stop_new = speaker.start()
        stop_old()  # stale handle must not tear down the new stream
        self.assertTrue(speaker.active)
        stop_new()
        self.assertEqual(self.default, "tv_sink")


if __name__ == "__main__":
    unittest.main()
