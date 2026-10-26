"""TV prompt navigation, dismissal, and synchronization with controller state."""
from pathlib import Path
from types import SimpleNamespace
import sys
import unittest
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import gi
gi.require_version("Gtk", "3.0")
from gi.repository import Gtk, Gdk
from widgets.playback import PlaybackSuggestion
import home


PROMPT = {"id": "one", "title": "Playback keeps buffering", "message": "Try a smaller source.",
          "current": "2160p · REMUX · 20.0 GB", "alternative": "1080p · WEB-DL · 5.0 GB", "failed": False}


class PlaybackPromptTests(unittest.TestCase):
    def drain(self):
        while Gtk.events_pending():
            Gtk.main_iteration_do(False)

    def test_buttons_keyboard_and_back(self):
        for choice in ("switch", "dismiss"):
            respond = mock.Mock()
            dialog = PlaybackSuggestion(PROMPT, respond, 2 / 3)
            self.drain()
            self.assertIs(dialog.get_focus(), dialog.buttons[0])
            if choice == "dismiss":
                dialog.on_key(dialog, SimpleNamespace(keyval=Gdk.KEY_Right))
                self.assertIs(dialog.get_focus(), dialog.buttons[1])
            dialog.get_focus().clicked()
            respond.assert_called_once_with(choice)
            self.assertFalse(dialog.get_sensitive())
            dialog.destroy()
        respond = mock.Mock()
        dialog = PlaybackSuggestion(dict(PROMPT, failed=True), respond)
        self.assertEqual(dialog.buttons[1].get_label(), "Not now")
        dialog.on_key(dialog, SimpleNamespace(keyval=Gdk.KEY_Escape))
        respond.assert_called_once_with("dismiss")
        dialog.destroy()

    def test_polling_does_not_recreate_or_refocus_prompt(self):
        shell = SimpleNamespace(playback_prompt_id=None, playback_prompt=None, scale=2 / 3)
        home.Home.update_playback_prompt(shell, PROMPT)
        dialog = shell.playback_prompt
        self.drain()
        dialog.buttons[1].grab_focus()
        home.Home.update_playback_prompt(shell, PROMPT)
        self.assertIs(shell.playback_prompt, dialog)
        self.assertIs(dialog.get_focus(), dialog.buttons[1])
        home.Home.update_playback_prompt(shell, None)
        self.assertIsNone(shell.playback_prompt)
        self.assertIsNone(shell.playback_prompt_id)

    def test_failed_command_allows_retry_and_success_closes_prompt(self):
        shell = SimpleNamespace(playback_prompt_id=None, playback_prompt=None, scale=2 / 3,
                                toast=mock.Mock(), last_state_poll=10)
        home.Home.update_playback_prompt(shell, PROMPT)
        dialog = shell.playback_prompt
        def thread(target, **kw):
            return mock.Mock(start=target)
        with mock.patch.object(home.threading, "Thread", side_effect=thread), \
                mock.patch.object(home.screens, "cmd", return_value=None) as cmd:
            dialog.buttons[0].clicked()
            self.drain()
            self.assertTrue(dialog.get_sensitive())
            shell.toast.assert_called_once()
            cmd.return_value = {"ok": True}
            dialog.buttons[0].clicked()
            self.drain()
        self.assertIsNone(shell.playback_prompt)
        self.assertEqual(shell.last_state_poll, 0)
        cmd.assert_called_with("playback_suggestion", {"id": "one", "choice": "switch"}, timeout=15)


if __name__ == "__main__":
    unittest.main()
