import io
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import phonemic


class FakeProc:
    def __init__(self):
        self.stdin = io.BytesIO()
        self.terminated = False

    def terminate(self):
        self.terminated = True

    def wait(self, timeout=None):
        return 0


class PhoneMicTest(unittest.TestCase):
    def setUp(self):
        self.calls = []
        self.default = "mic0"
        self.procs = []

    def pactl(self, *args, timeout=3):
        self.calls.append(args)
        if args[0] == "get-default-source":
            return self.default
        if args[0] == "set-default-source":
            self.default = args[1]
        if args[0] == "load-module":
            return str(10 + sum(1 for c in self.calls if c[0] == "load-module"))
        return ""

    def popen(self, argv, **kw):
        proc = FakeProc()
        proc.argv = argv
        self.procs.append(proc)
        return proc

    def mic(self):
        return phonemic.PhoneMic(self.pactl, self.popen)

    def test_switches_default_and_restores(self):
        mic = self.mic()
        write, stop = mic.start(48000, 1)
        self.assertEqual(self.default, phonemic.SOURCE)
        self.assertTrue(mic.active)
        self.assertIn("--rate=48000", self.procs[0].argv)
        stop()
        self.assertEqual(self.default, "mic0")
        self.assertFalse(mic.active)
        self.assertTrue(self.procs[0].terminated)
        # The virtual devices stay loaded so Discord keeps its selection across reconnects.
        self.assertEqual([c[0] for c in self.calls].count("unload-module"), 0)
        mic.start(48000, 1)
        self.assertEqual([c[0] for c in self.calls].count("load-module"), 2)  # reused, not rebuilt
        mic.close()
        self.assertEqual([c[0] for c in self.calls].count("unload-module"), 2)

    def test_format_change_rebuilds_devices(self):
        mic = self.mic()
        _, stop = mic.start(48000, 1)
        stop()
        mic.start(16000, 1)
        self.assertEqual([c[0] for c in self.calls].count("load-module"), 4)
        self.assertEqual([c[0] for c in self.calls].count("unload-module"), 2)

    def test_newer_stream_replaces_older_without_losing_original(self):
        mic = self.mic()
        _, stop_old = mic.start(48000, 1)
        _, stop_new = mic.start(16000, 1)
        stop_old()  # stale handle must not tear down the new stream
        self.assertTrue(mic.active)
        self.assertEqual(self.default, phonemic.SOURCE)
        stop_new()
        self.assertEqual(self.default, "mic0")

    def test_rejects_bad_format(self):
        with self.assertRaises(ValueError):
            self.mic().start(12345, 1)
        with self.assertRaises(ValueError):
            self.mic().start(48000, 6)


if __name__ == "__main__":
    unittest.main()
