"""The phone preview mirrors scoped app lists without connecting to or commanding a TV."""
import importlib.util
from pathlib import Path
import unittest

SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "phone-preview.py"
spec = importlib.util.spec_from_file_location("imactv_phone_preview", SCRIPT)
preview = importlib.util.module_from_spec(spec)
spec.loader.exec_module(preview)


class PreviewCatalogTests(unittest.TestCase):
    def setUp(self):
        preview.Preview.apps = preview.sample_apps(2)
        preview.Preview.max_favorites = 6

    def ids(self, scope):
        handler = preview.Preview.__new__(preview.Preview)
        return [item["id"] for item in handler.apps_payload(scope)["apps"]]

    def test_others_is_only_unpinned_supported_utilities(self):
        self.assertEqual(self.ids("others"), [
            "desktop:brave-browser", "desktop:org.rustdesk.RustDesk", "calculator"])
        self.assertNotIn("desktop:firefox-0", self.ids("others"))

    def test_shortcuts_and_home_use_shared_in_memory_pins(self):
        shortcuts = self.ids("shortcuts")
        self.assertEqual(shortcuts[:4], ["movies", "series", "youtube", "twitch"])
        self.assertEqual(shortcuts[4:], ["desktop:brave-browser", "desktop:org.rustdesk.RustDesk", "calculator"])
        self.assertEqual(set(self.ids("home")), {"movies", "series", "youtube", "twitch"})
        brave = next(a for a in preview.Preview.apps if a["id"] == "desktop:brave-browser")
        brave["favorite"] = True
        self.assertIn(brave["id"], self.ids("home"))
        self.assertIn(brave["id"], self.ids("shortcuts"))
        self.assertNotIn(brave["id"], self.ids("others"))

    def test_legacy_unscoped_list_remains_full_and_scoped_utilities_have_descriptions(self):
        self.assertIn("desktop:brave-0", self.ids(""))
        handler = preview.Preview.__new__(preview.Preview)
        by_id = {a["id"]: a for a in handler.apps_payload("shortcuts")["apps"]}
        self.assertEqual(by_id["desktop:brave-browser"]["description"], "Browse the web with Brave")
        self.assertEqual(by_id["calculator"]["description"], "Calculate using your remote")


if __name__ == "__main__":
    unittest.main()
