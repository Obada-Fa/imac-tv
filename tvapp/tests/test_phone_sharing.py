import io
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import Mock, patch

from PIL import Image, ImageCms

from phone_sharing import normalize_photo, Sharing


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
        normalize = patch("phone_sharing.normalize_photo",
                          side_effect=lambda source, output: output.write_bytes(source.read_bytes()))
        normalize.start()
        self.addCleanup(normalize.stop)

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


class DisplayPhoto(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.source = Path(temp.name) / "phone-photo"
        self.output = Path(temp.name) / "display-photo"

    def test_converts_profile_pixels_and_embeds_srgb(self):
        camera_profile = ImageCms.ImageCmsProfile(ImageCms.createProfile("LAB"))
        Image.new("LAB", (2, 1), (134, 170, 145)).save(
            self.source, format="TIFF", icc_profile=camera_profile.tobytes())
        normalize_photo(self.source, self.output)
        with Image.open(self.output) as photo:
            self.assertEqual((photo.format, photo.mode), ("PNG", "RGB"))
            self.assertIn("srgb", ImageCms.getProfileName(io.BytesIO(photo.info["icc_profile"])).lower())
            for actual, expected in zip(photo.getpixel((0, 0)), (195, 93, 99)):
                self.assertAlmostEqual(actual, expected, delta=2)

    def test_applies_orientation_to_rgb_png(self):
        photo = Image.new("RGB", (2, 1), "red")
        photo.putpixel((1, 0), (0, 0, 255))
        orientation = Image.Exif()
        orientation[274] = 6
        photo.save(self.source, format="PNG", exif=orientation)
        normalize_photo(self.source, self.output)
        with Image.open(self.output) as display:
            self.assertEqual(display.size, (1, 2))
            self.assertEqual(display.getpixel((0, 0)), (255, 0, 0))
            self.assertEqual(display.getpixel((0, 1)), (0, 0, 255))
