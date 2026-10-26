"""Mouse / phone-pointer support for the drawn controls (glass.Pointer): a click focuses the control under it and
does what OK does; hover focuses it; hidden, insensitive and covered controls are never hit."""
from pathlib import Path
import sys
import time
import unittest
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import screens  # noqa: E402,F401  (imports the widgets package in the right order)
from widgets import chrome, glass  # noqa: E402
from gi.repository import Gtk, Gdk, GLib  # noqa: E402


class FakeShell:
    scale = 1.0
    sw, sh = 1280, 720

    def px(self, v):
        return round(v)

    def ensure_focus(self):
        pass


def drain():
    while Gtk.events_pending():
        Gtk.main_iteration_do(False)


class PointerTest(unittest.TestCase):
    def setUp(self):
        # Xvfb has no window manager: the window never becomes active, so treat "is the focus widget" as focus.
        patch = mock.patch.object(glass.Focusable, "has_focus", lambda self: self.is_focus())
        patch.start()
        self.addCleanup(patch.stop)
        self.shell = FakeShell()
        look = glass.Look({"transparency": False})
        self.hits = []
        self.win = Gtk.Window()
        self.win.move(0, 0)
        self.addCleanup(self.win.destroy)
        col = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        top = Gtk.Box()
        self.tab = chrome.NavTab(self.shell, look, "Movies", on_activate=lambda: self.hits.append("tab"))
        self.tab2 = chrome.NavTab(self.shell, look, "Series", on_activate=lambda: self.hits.append("tab2"))
        top.pack_start(self.tab, False, False, 0)
        top.pack_start(self.tab2, False, False, 0)
        col.pack_start(top, False, False, 0)
        # a card inside a horizontally scrolled row, like a Rail
        scroller = Gtk.ScrolledWindow()
        scroller.set_policy(Gtk.PolicyType.EXTERNAL, Gtk.PolicyType.NEVER)
        row = Gtk.Box()
        self.cards = [chrome.PosterCard(self.shell, look, {"name": f"Card {i}"}, "poster", 190, 285,
                                        on_activate=lambda i=i: self.hits.append(f"card{i}")) for i in range(3)]
        for c in self.cards:
            row.pack_start(c, False, False, 0)
        scroller.add(row)
        col.pack_start(scroller, False, False, 0)
        self.toggle_state = [False]

        def flip():
            self.toggle_state[0] = not self.toggle_state[0]
            self.row.set_value(toggle=self.toggle_state[0])
            self.hits.append("row")
        self.row = glass.GlassRow(self.shell, None, look, "Visual effects", toggle=False, on_activate=flip)
        col.pack_start(self.row, False, False, 0)
        self.off = glass.GlassRow(self.shell, None, look, "Disabled", on_activate=lambda: self.hits.append("off"))
        self.off.set_sensitive(False)
        col.pack_start(self.off, False, False, 0)
        self.win.add(col)
        self.win.set_default_size(1280, 720)
        self.clicked = []
        self.pointer = glass.Pointer(self.win, on_click=self.clicked.append)
        self.win.show_all()
        drain()

    def centre(self, w):
        x, y = w.translate_coordinates(self.win, w.get_allocated_width() // 2, w.get_allocated_height() // 2)
        org = self.win.get_window().get_origin()
        return x + org[-2], y + org[-1]

    def press(self, w, button=1):
        """A real GdkEventButton, delivered through the window's button-press-event signal."""
        xr, yr = self.centre(w)
        ev = Gdk.Event.new(Gdk.EventType.BUTTON_PRESS)
        ev.button.window = self.win.get_window()
        ev.button.button = button
        ev.button.x_root, ev.button.y_root = float(xr), float(yr)
        org = self.win.get_window().get_origin()
        ev.button.x, ev.button.y = float(xr - org[-2]), float(yr - org[-1])
        ev.button.time = Gtk.get_current_event_time()
        seat = Gdk.Display.get_default().get_default_seat()
        ev.set_device(seat.get_pointer())
        return self.win.emit("button-press-event", ev)

    def test_click_on_a_tab_focuses_and_activates_it(self):
        self.assertTrue(self.press(self.tab2))
        self.assertEqual(self.hits, ["tab2"])
        self.assertTrue(self.tab2.is_focus())
        self.assertEqual(self.clicked, [self.tab2])

    def test_click_on_a_card_in_a_scrolled_row(self):
        self.assertTrue(self.press(self.cards[1]))
        self.assertEqual(self.hits, ["card1"])
        self.assertTrue(self.cards[1].is_focus())

    def test_click_on_a_settings_row_flips_its_toggle_like_ok(self):
        self.press(self.row)
        self.assertEqual(self.hits, ["row"])
        self.assertTrue(self.row.toggle)
        self.press(self.row)
        self.assertFalse(self.row.toggle)

    def test_ok_and_click_do_the_same(self):
        ok = Gdk.Event.new(Gdk.EventType.KEY_PRESS)
        ok.key.keyval = Gdk.KEY_Return
        self.row.grab_focus()
        self.row.on_key(self.row, ok.key)
        self.press(self.row)
        self.assertEqual(self.hits, ["row", "row"])

    def test_disabled_right_button_and_empty_space_do_nothing(self):
        self.assertFalse(self.press(self.off))
        self.assertFalse(self.press(self.tab, button=3))
        org = self.win.get_window().get_origin()
        self.assertIsNone(self.pointer.at(org[-2] + 1270, org[-1] + 710))
        self.assertEqual(self.hits, [])

    def test_hidden_and_covered_roots_are_not_hit(self):
        self.cards[0].hide()
        drain()
        xr, yr = self.centre(self.cards[1])
        self.assertIs(self.pointer.at(xr, yr), self.cards[1])
        self.pointer.root = lambda: None             # e.g. the loading screen is up
        self.assertIsNone(self.pointer.at(xr, yr))

    def test_hover_focuses_once_throttled(self):
        xr, yr = self.centre(self.cards[2])
        motion = mock.Mock(x_root=xr, y_root=yr)
        with mock.patch.object(GLib, "timeout_add", return_value=7) as ta:
            for _ in range(20):                       # a burst of motion events: one timer, one pick
                self.pointer.on_motion(self.win, motion)
        self.assertEqual(ta.call_count, 1)
        self.pointer.hover_now()
        self.assertTrue(self.cards[2].is_focus())
        self.assertEqual(self.hits, [])               # hover never activates
        self.tab.grab_focus()                         # the remote moves on; resting the pointer doesn't steal it
        self.pointer.on_motion(self.win, motion)
        self.pointer.hover_now()
        self.assertTrue(self.tab.is_focus())

    def test_settings_rail_node_and_picture_tile(self):
        look = glass.Look({"transparency": False})
        node = glass.RailNode(self.shell, None, look, "Display", "display", glass.TINTS["Display"],
                              on_activate=lambda: self.hits.append("node"))
        tile = glass.PictureTile(self.shell, None, look, "Focus", "Nature", None, False,
                                 on_activate=lambda: self.hits.append("tile"))
        box = Gtk.Box()
        box.pack_start(node, False, False, 0)
        box.pack_start(tile, False, False, 0)
        self.win.get_child().get_children()[0].pack_start(box, False, False, 0)     # the top row: on screen
        self.win.show_all()
        end = time.monotonic() + 3                    # until the frame clock has laid the new widgets out
        while time.monotonic() < end and min(node.get_allocated_width(), tile.get_allocated_width()) <= 1:
            Gtk.main_iteration_do(False)
            time.sleep(0.005)
        self.press(node)                              # the label side counts too, not only the hexagon
        self.press(tile)
        self.assertEqual(self.hits, ["node", "tile"])
        self.assertTrue(tile.is_focus())

    def test_pick_prefers_the_topmost(self):
        ov = Gtk.Overlay()
        under = glass.GlassRow(self.shell, None, glass.Look({}), "Under")
        over = glass.GlassRow(self.shell, None, glass.Look({}), "Over")
        ov.add(under)
        ov.add_overlay(over)
        win = Gtk.Window()
        self.addCleanup(win.destroy)
        win.add(ov)
        win.show_all()
        drain()
        self.assertIs(glass.pick(win, 20, 20), over)


if __name__ == "__main__":
    unittest.main()
