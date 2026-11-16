import io
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import Mock, patch

from phone_sharing import Sharing


class UploadedMedia(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.acquire, self.release = Mock(), Mock()
        self.sharing = Sharing(Path(self.temp.name), {}, self.acquire, self.release, Mock())
        self.sharing.player = self.sharing.probe = "available"
        self.sharing._show_item = Mock()
        probe = patch("phone_sharing.inspect_file", return_value="photo")
        probe.start()
        self.addCleanup(probe.stop)

    def test_photos_queue_in_order_and_stop_cleans_up(self):
        first = self.sharing.upload(io.BytesIO(b"first"), 5, "one.jpg", None)
        same = self.sharing.upload(io.BytesIO(b"second"), 6, "two.jpg", first["id"])
        self.assertEqual(same["count"], 2)
        self.assertEqual(same["item"]["name"], "one.jpg")
        self.assertEqual(self.sharing.control("next")["item"]["name"], "two.jpg")
        self.acquire.assert_called_once()
        self.sharing.stop()
        self.release.assert_called_once()
        self.assertEqual(list(self.sharing.root.iterdir()), [])
        self.assertEqual(self.sharing.used, 0)

    def test_stopped_upload_cannot_reopen_sharing(self):
        reading, resume = threading.Event(), threading.Event()
        class Pending:
            def read(self, _):
                reading.set()
                resume.wait(3)
                return b"photo"

        errors = []
        def upload():
            try:
                self.sharing.upload(Pending(), 5, "late.jpg", None)
            except ValueError as err:
                errors.append(str(err))

        worker = threading.Thread(target=upload)
        worker.start()
        try:
            self.assertTrue(reading.wait(3))
            self.sharing.stop()
        finally:
            resume.set()
            worker.join(4)
        self.assertEqual(errors, ["This sharing session ended"])
        self.assertEqual(list(self.sharing.root.iterdir()), [])
        self.acquire.assert_not_called()

    def test_short_body_leaves_space_and_playback_intact(self):
        with self.assertRaisesRegex(ValueError, "Upload ended early"):
            self.sharing.upload(io.BytesIO(b"short"), 100, "partial.jpg", None)
        self.assertEqual(self.sharing.used, 0)
        self.assertEqual(list(self.sharing.root.iterdir()), [])
        self.acquire.assert_not_called()

    def test_upload_cannot_append_to_replacement_session(self):
        original = self.sharing.upload(io.BytesIO(b"a"), 1, "a.jpg", None)
        replacement = self.sharing.upload(io.BytesIO(b"b"), 1, "b.jpg", None)
        self.assertNotEqual(original["id"], replacement["id"])
        with self.assertRaisesRegex(ValueError, "This sharing session ended"):
            self.sharing.upload(io.BytesIO(b"c"), 1, "c.jpg", original["id"])
        self.assertEqual(self.sharing.state()["item"]["name"], "b.jpg")
        self.assertEqual(self.sharing.used, 1)
