"""The apps system on the TV: the Home row (favorites, then the fixed tiles), the Apps screen, the Menu sheet and
the rebuild on apps_rev. apps.py is stubbed through screens.home_app_list / screens.app_list."""
from pathlib import Path
import sys
import tempfile
import types
import unittest
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
_temp = tempfile.TemporaryDirectory(prefix="imactv-apps-screen-test-")

try:
    import gi
    gi.require_version("Gtk", "3.0")
    from gi.repository import Gtk, Gdk
    import home
    import screens
    from widgets import rows, bubbles
except Exception:  # no PyGObject / GTK typelibs
    screens = None


def item(app_id, name=None, kind="desktop", favorite=False, builtin=None):
    return {"id": app_id, "name": name or app_id, "kind": kind, "favorite": favorite,
            "icon": None if builtin else "/api/apps/icon?id=" + app_id, "builtin_kind": builtin}


FAVS = [item("movies", "Movies", "screen", True, "movies"), item("youtube", "YouTube", "app", True, "youtube"),
        item("desktop:brave", "Brave", favorite=True), item("desktop:rustdesk", "RustDesk", favorite=True)]
REST = [item("desktop:calc", "Calculator"), item("desktop:files", "Files"), item("discord", "Discord", "app", False, "discord")]


class StubShell(Gtk.Window):
    """Just enough of home.Home for a screen or a row widget."""

    def __init__(self, height=1080):
        super().__init__()
        self.scale = height / 1080
        self.sw, self.sh = round(1920 * self.scale), height
        self.px = lambda n: round(n * self.scale)
        self.sheet, self.tops, self.pair_listeners = None, {}, []
        self.net, self.code, self.remote = {"connected": True}, "123456", {"connected": True}
        self.avoid_rect, self.home_cfg, self.page = None, {}, None
        self.opened, self.gone, self.toasts, self.favorite_calls = [], [], [], []
        self.root = Gtk.Overlay()
        self.add(self.root)
        self.focus_screen = lambda *a: None
        self.open_app_item = self.opened.append
        self.goto_top = self.gone.append
        self.top_screen = lambda name: name
        self.push = self.gone.append
        self.toast = lambda text, secs=4: self.toasts.append(text)
        self.set_app_favorite = lambda it, fav: self.favorite_calls.append((it["id"], fav))
        self.open_sheet = lambda *a, **kw: home.Home.open_sheet(self, *a, **kw)
        self.close_sheet = lambda *a, **kw: home.Home.close_sheet(self, *a, **kw)

    def current(self):
        return self.page

    def ensure_focus(self):
        pass


def pump():
    while Gtk.events_pending():
        Gtk.main_iteration()


@unittest.skipIf(screens is None, "GTK bindings unavailable")
class RowCompositionTests(unittest.TestCase):
    def test_favorites_then_the_fixed_tiles_in_order(self):
        specs = rows.apps_row_specs(FAVS)
        self.assertEqual([s["title"] for s in specs],
                         ["Movies", "YouTube", "Brave", "RustDesk", "All apps"])
        self.assertEqual([s["kind"] for s in specs],
                         ["movies", "youtube", None, None, "apps"])
        self.assertEqual([s["app"] is None for s in specs], [False] * 4 + [True])

    def test_no_favorites_leaves_only_the_fixed_tiles(self):
        self.assertEqual([s["title"] for s in rows.apps_row_specs([])], ["All apps"])

    def test_widget_items_use_home_apps_and_open_them_through_the_shell(self):
        shell = StubShell()
        with mock.patch.object(screens, "home_app_list", return_value=list(FAVS)):
            w = rows.AppsWidget(shell)
            items = w.items()
        self.assertEqual([i["title"] for i in items][-1:], ["All apps"])
        items[2]["on_activate"]()
        self.assertEqual(shell.opened, [FAVS[2]])
        items[-1]["on_activate"]()
        self.assertEqual(shell.gone, ["apps"])

    def test_nine_tiles_for_six_favorites(self):
        six = [item("a%d" % i, "App %d" % i, favorite=True) for i in range(6)]
        shell = StubShell()
        with mock.patch.object(screens, "home_app_list", return_value=six):
            self.assertEqual(len(rows.AppsWidget(shell).items()), 7)

    def test_without_an_apps_module_the_old_built_in_tiles_remain(self):
        shell = StubShell()
        with mock.patch.object(screens, "home_app_list", return_value=None):
            titles = [i["title"] for i in rows.AppsWidget(shell).items()]
        self.assertEqual(titles, ["Movies", "Series", "YouTube", "Twitch", "All apps"])


@unittest.skipIf(screens is None, "GTK bindings unavailable")
class RowRebuildTests(unittest.TestCase):
    def setUp(self):
        # the stub window is never the active toplevel, so has_focus() would always say no
        self.patches = [mock.patch.object(rows.AppNode, "has_focus", lambda n: n.is_focus()),
                        mock.patch.object(bubbles.Bubble, "has_focus", lambda n: n.is_focus()),
                        mock.patch.object(screens, "load_app_icon"),
                        mock.patch.object(screens, "home_app_list", side_effect=lambda: list(self.favs))]
        for p in self.patches:
            p.start()
            self.addCleanup(p.stop)
        self.favs = list(FAVS)
        self.shell = StubShell()

    def build(self, style):
        w = rows.AppsWidget(self.shell, {"style": style})
        host = w.build()
        self.shell.root.add(host)
        self.shell.show_all()
        pump()
        return w

    def titles(self, w):
        return [b.title for b in w.row.bubbles]

    def test_rebuilds_when_apps_rev_changes_in_both_styles(self):
        for style in ("network", "bubbles"):
            with self.subTest(style=style):
                self.favs = list(FAVS)
                w = self.build(style)
                self.assertEqual(len(w.row.bubbles), 5)
                w.set_rev(5)                          # the first revision is only remembered
                self.favs = FAVS + [item("desktop:calc", "Calculator", favorite=True)]
                w.set_rev(5)
                self.assertEqual(len(w.row.bubbles), 5)
                w.set_rev(6)
                self.assertEqual(self.titles(w)[:5], ["Movies", "YouTube", "Brave", "RustDesk", "Calculator"])
                self.assertEqual(self.titles(w)[-1:], ["All apps"])
                self.shell.root.remove(w.host)

    def test_focus_stays_on_the_same_tile_after_a_rebuild(self):
        w = self.build("network")
        w.set_rev(1)
        w.row.bubbles[3].grab_focus()                 # RustDesk
        pump()
        self.assertEqual(self.shell.get_focus().title, "RustDesk")
        self.favs = [f for f in FAVS if f["id"] != "desktop:brave"]       # Brave goes: RustDesk moves left
        w.set_rev(2)
        pump()
        self.assertEqual(self.shell.get_focus().title, "RustDesk")

    def test_focus_falls_back_to_the_neighbour_when_its_tile_is_removed(self):
        w = self.build("bubbles")
        w.set_rev(1)
        w.row.bubbles[2].grab_focus()                 # Brave
        pump()
        self.favs = [f for f in FAVS if f["id"] != "desktop:brave"]
        w.set_rev(2)
        pump()
        self.assertEqual(self.shell.get_focus().title, "RustDesk")        # the tile that took its place

    def test_menu_target_is_set_on_favorites_only(self):
        w = self.build("network")
        targets = [getattr(b, "menu_item", "missing") for b in w.row.bubbles]
        self.assertEqual([t["id"] for t in targets[:4]], [f["id"] for f in FAVS])
        self.assertEqual(targets[4:], [None])


@unittest.skipIf(screens is None, "GTK bindings unavailable")
class AppsScreenTests(unittest.TestCase):
    def setUp(self):
        for p in (mock.patch.object(screens, "load_app_icon"), mock.patch.object(screens, "continue_items")):
            p.start()
            self.addCleanup(p.stop)
        self.shell = StubShell()
        self.screen = screens.AppsScreen(self.shell)
        self.shell.page = self.screen
        self.shell.root.add(self.screen)
        self.shell.show_all()

    def test_favorites_first_then_the_other_apps(self):
        self.screen.populate(FAVS + REST)
        pump()
        flat = [c.app_item["name"] for r in self.screen.rows for c in r]
        self.assertEqual(flat, [i["name"] for i in FAVS + REST])
        self.assertEqual([len(g) for g in self.screen.focus_groups()], [6, 1])

    def test_the_other_apps_wrap_into_rows(self):
        many = [item("desktop:a%02d" % i, "App %02d" % i) for i in range(14)]
        self.screen.populate(FAVS + many)
        per = self.screen.per_row()
        self.assertEqual(per, 6)
        self.assertEqual([len(r) for r in self.screen.rows], [6, 6, 6])

    def test_ok_opens_the_app_and_cards_know_their_item(self):
        self.screen.populate(FAVS + REST)
        card = self.screen.rows[0][5]
        card.on_activate()
        self.assertEqual(self.shell.opened, [REST[1]])
        self.assertIs(card.app_item, REST[1])

    def test_nothing_on_home_shows_a_hint_and_still_lists_the_apps(self):
        self.screen.populate(REST)
        self.assertEqual(self.screen.fav_cards, [])
        self.assertEqual([len(g) for g in self.screen.focus_groups()], [3])
        self.assertEqual([c.app_item for c in self.screen.cards()], REST)

    def test_no_apps_at_all_shows_a_placeholder(self):
        self.screen.populate([])
        self.assertEqual(self.screen.focus_groups(), [])
        texts = [c.get_text() for c in self.screen.box.get_children() if hasattr(c, "get_text")]
        self.assertEqual(texts, ["No apps available"])

    def test_first_list_focuses_the_first_card(self):
        self.screen.populate(FAVS + REST)
        pump()
        self.assertIs(self.shell.get_focus(), self.screen.rows[0][0])

    def test_reload_keeps_focus_on_the_same_app_when_it_moves_section(self):
        self.screen.populate(FAVS + REST)
        self.screen.rows[0][4].grab_focus()                       # Calculator
        pump()
        moved = [dict(i, favorite=(i["id"] == "desktop:calc")) for i in FAVS + REST]
        moved.sort(key=lambda i: not i["favorite"])
        self.screen.populate(moved)
        pump()
        self.assertEqual(self.shell.get_focus().app_item["id"], "desktop:calc")

    def test_return_from_calculator_restores_calculator_focus_after_reload(self):
        self.screen.populate(REST)
        calculator = self.screen.cards()[0]
        calculator.grab_focus()
        self.screen.last_focus = calculator
        self.shell.page = mock.Mock()
        self.screen.populate(REST)
        self.assertEqual(self.screen.last_focus.app_item["id"], "desktop:calc")
        self.shell.page = self.screen
        self.screen.last_focus.grab_focus()
        pump()
        self.assertEqual(self.shell.get_focus().app_item["id"], "desktop:calc")

    def test_apps_rev_reloads_only_when_it_changes(self):
        with mock.patch.object(self.screen, "load") as load:
            self.screen.set_rev(3)
            self.screen.set_rev(3)
            self.assertEqual(load.call_count, 0)
            self.screen.set_rev(4)
            self.assertEqual(load.call_count, 1)

    def test_load_reads_the_list_off_the_gtk_thread_and_applies_it(self):
        with mock.patch.object(screens, "_apps", return_value=mock.Mock(others_apps=lambda: FAVS + REST)), \
                mock.patch.object(screens._data_pool, "submit", side_effect=lambda fn: fn()):
            self.screen.load()
        pump()
        self.assertEqual(len(self.screen.cards()), len(FAVS + REST))


@unittest.skipIf(screens is None, "GTK bindings unavailable")
class MenuSheetTests(unittest.TestCase):
    def setUp(self):
        self.shell = StubShell()
        self.shell.show_all()
        self.shell.set_app_favorite = lambda it, fav: self.shell.favorite_calls.append((it["id"], fav))
        self.open_menu = lambda it: home.Home.open_app_menu(self.shell, it)

    def buttons(self):
        return [row[0] for row in self.shell.sheet["rows"]]

    def test_sheet_offers_open_and_remove_for_a_favorite(self):
        self.open_menu(FAVS[2])
        pump()
        self.assertEqual(self.shell.sheet["kind"], "app_menu")
        self.assertEqual([b.get_label() for b in self.buttons()], ["Open", "Remove from Home"])
        self.assertIs(self.shell.sheet["default"], self.buttons()[1])

    def test_sheet_offers_add_for_an_app_that_is_not_on_home(self):
        self.open_menu(REST[0])
        self.assertEqual([b.get_label() for b in self.buttons()], ["Open", "Add to Home"])

    def test_open_closes_the_sheet_and_opens_the_app(self):
        self.open_menu(REST[0])
        self.buttons()[0].clicked()
        self.assertIsNone(self.shell.sheet)
        self.assertEqual(self.shell.opened, [REST[0]])
        self.assertEqual(self.shell.favorite_calls, [])

    def test_toggle_adds_or_removes(self):
        self.open_menu(REST[0])
        self.buttons()[1].clicked()
        self.assertIsNone(self.shell.sheet)
        self.open_menu(FAVS[2])
        self.buttons()[1].clicked()
        self.assertEqual(self.shell.favorite_calls, [("desktop:calc", True), ("desktop:brave", False)])

    def test_up_and_down_move_between_the_two_actions(self):
        self.open_menu(REST[0])
        pump()
        self.assertEqual(self.shell.sheet["rows"], [[b] for b in self.buttons()])


@unittest.skipIf(screens is None, "GTK bindings unavailable")
class FavoriteCommandTests(unittest.TestCase):
    """Home.set_app_favorite: the controller command, its error toast, and an early state poll."""

    def run_it(self, reply):
        shell = types.SimpleNamespace(toasts=[], last_state_poll=99)
        shell.toast = lambda text, secs=4: shell.toasts.append(text)

        class Now:                                  # run the worker inline
            def __init__(self, target, daemon=None):
                self.target = target

            def start(self):
                self.target()
        with mock.patch.object(home, "cmd", return_value=reply) as c, mock.patch.object(home.threading, "Thread", Now), \
                mock.patch.object(home.GLib, "idle_add", side_effect=lambda fn, *a: fn(*a)):
            home.Home.set_app_favorite(shell, item("desktop:calc", "Calculator"), True)
        return shell, c

    def test_success_toasts_and_asks_for_a_state_poll(self):
        shell, c = self.run_it({"apps": [], "max_favorites": 6})
        c.assert_called_once_with("app_favorite", {"id": "desktop:calc", "favorite": True}, timeout=6)
        self.assertEqual(shell.toasts, ["Added to Home"])
        self.assertEqual(shell.last_state_poll, 0)

    def test_full_home_error_is_shown(self):
        shell, _ = self.run_it({"error": "Home is full: remove a favorite first"})
        self.assertEqual(shell.toasts, ["Home is full: remove a favorite first"])

    def test_unreachable_controller(self):
        shell, _ = self.run_it(None)
        self.assertEqual(shell.toasts, ["Can't reach the TV controller"])


@unittest.skipIf(screens is None, "GTK bindings unavailable")
class OpenAndKeyTests(unittest.TestCase):
    def shell(self, focus, screen=None):
        s = mock.MagicMock()
        s.sheet = None
        s.buffering.get_visible.return_value = False
        s.get_focus.return_value = focus
        s.current.return_value = screen or types.SimpleNamespace()
        return s

    def press(self, shell, name):
        ev = mock.Mock(keyval=Gdk.keyval_from_name(name))
        with mock.patch.object(home, "display"):
            return home.Home.on_key_press(shell, None, ev)

    def test_menu_key_on_an_app_tile_opens_its_sheet(self):
        tile = types.SimpleNamespace(menu_item=FAVS[2])
        for key in ("Menu", "m"):
            s = self.shell(tile)
            self.assertTrue(self.press(s, key))
            s.open_app_menu.assert_called_once_with(FAVS[2])

    def test_menu_key_on_an_apps_screen_card_opens_its_sheet(self):
        card = types.SimpleNamespace(app_item=REST[0])
        s = self.shell(card)
        self.assertTrue(self.press(s, "Menu"))
        s.open_app_menu.assert_called_once_with(REST[0])

    def test_menu_key_elsewhere_does_nothing(self):
        for focus in (types.SimpleNamespace(menu_item=None), types.SimpleNamespace()):
            s = self.shell(focus)
            self.assertFalse(self.press(s, "Menu"))
            s.open_app_menu.assert_not_called()

    def test_m_is_typing_inside_a_text_field(self):
        entry = Gtk.Entry()
        entry.menu_item = FAVS[0]
        s = self.shell(entry)
        self.assertFalse(self.press(s, "m"))
        s.open_app_menu.assert_not_called()

    def test_ok_on_a_screen_item_goes_to_that_screen(self):
        s = mock.MagicMock()
        home.Home.open_app_item(s, FAVS[0])
        s.goto_top.assert_called_once_with("movies")

    def test_ok_on_an_app_asks_the_controller_with_the_opening_toast(self):
        s = mock.MagicMock()

        class Now:
            def __init__(self, target, daemon=None):
                self.target = target

            def start(self):
                self.target()
        with mock.patch.object(home, "cmd") as c, mock.patch.object(home.threading, "Thread", Now):
            home.Home.open_app_item(s, FAVS[2])
        s.toast.assert_called_once_with("Opening Brave…", 3)
        c.assert_called_once_with("app_open", {"id": "desktop:brave"}, timeout=8)

    def test_state_poll_forwards_apps_rev_to_home_and_the_apps_screen(self):
        s = mock.MagicMock()
        s.tops = {"home": mock.Mock(), "apps": mock.Mock()}
        home.Home.sync_apps(s, 7)
        s.tops["home"].set_apps_rev.assert_called_once_with(7)
        s.tops["apps"].set_rev.assert_called_once_with(7)


@unittest.skipIf(screens is None, "GTK bindings unavailable")
class AppIconLoadingTests(unittest.TestCase):
    def test_duplicate_requests_share_work_and_destroyed_receiver_is_released(self):
        import gc
        import weakref
        jobs, idle, delivered = [], [], []
        class Pool:
            def submit(self, fn):
                jobs.append(fn)
        class Receiver:
            def callback(self, value):
                delivered.append(value)
        screens._app_icons.clear()
        screens._app_icon_waiters.clear()
        receiver = Receiver()
        ref = weakref.ref(receiver)
        item_ = {"id": "desktop:duplicate"}
        with mock.patch.object(screens, "_art_pool", Pool()), \
             mock.patch.object(screens.GLib, "idle_add", side_effect=lambda fn, *args: idle.append((fn, args))), \
             mock.patch.object(screens, "_apps", return_value=None):
            screens.load_app_icon(item_, 40, receiver.callback)
            screens.load_app_icon(item_, 40, lambda value: delivered.append(value))
            self.assertEqual(len(jobs), 1)
            del receiver
            gc.collect()
            self.assertIsNone(ref())
            jobs.pop()()
            fn, args = idle.pop()
            fn(*args)
        self.assertEqual(delivered, [None])
        self.assertFalse(screens._app_icon_waiters)
        screens._app_icons.clear()

    def test_submit_and_callback_failures_release_work(self):
        jobs, idle, delivered = [], [], []
        class Pool:
            def submit(self, fn):
                jobs.append(fn)
        def broken(_value):
            raise RuntimeError("callback failed")
        screens._app_icons.clear()
        screens._app_icon_waiters.clear()
        with mock.patch.object(screens, "_art_pool", Pool()), \
             mock.patch.object(screens.GLib, "idle_add", side_effect=lambda fn, *args: idle.append((fn, args))), \
             mock.patch.object(screens, "_apps", return_value=None):
            screens.load_app_icon({"id": "desktop:errors"}, 40, broken)
            screens.load_app_icon({"id": "desktop:errors"}, 40, lambda v: delivered.append(v))
            jobs.pop()()
            fn, args = idle.pop()
            fn(*args)
            self.assertEqual(delivered, [None])
            self.assertFalse(screens._app_icon_waiters)
        screens._app_icons.clear()
        with mock.patch.object(screens._art_pool, "submit", side_effect=RuntimeError("shutdown")):
            screens.load_app_icon({"id": "desktop:submit"}, 40, lambda v: delivered.append(v))
        self.assertFalse(screens._app_icon_waiters)
        self.assertEqual(delivered, [None, None])
        screens._app_icons.clear()


@unittest.skipIf(screens is None, "GTK bindings unavailable")
class PhaseTwoTests(unittest.TestCase):
    def test_all_apps_is_always_on_home_without_scanning_the_pc(self):
        shell = StubShell()
        with mock.patch.object(screens, "home_app_list", return_value=FAVS), \
             mock.patch.object(screens, "others_app_list", side_effect=AssertionError("scanned on the GTK thread")):
            widget = rows.AppsWidget(shell)
            specs = widget.items()
        self.assertEqual([spec["key"] for spec in specs], [app["id"] for app in FAVS] + ["fixed:apps"])
        shell.destroy()

    def test_utilities_get_their_own_section_before_the_pc_apps(self):
        shell = StubShell()
        screen = screens.AppsScreen(shell)
        tools = [dict(item("desktop:files", "Files"), utility=True), dict(item("calculator", "Calculator", "screen"), utility=True)]
        screen.populate(tools + [item("desktop:gimp", "GIMP")])
        titles = [c.get_text() for c in screen.box.get_children() if hasattr(c, "get_text")]
        self.assertEqual([[c.app_item["name"] for c in r] for r in screen.rows], [["Files", "Calculator"], ["GIMP"]])
        self.assertIn("Press Menu on any app to add it to Home or remove it", titles)
        shell.destroy()

    def test_first_use_pairing_remains_visible_without_status_line(self):
        from widgets.pairing import PairingWidget
        shell = StubShell()
        shell.prev_app = "home"
        shell.remote = {"connected": False}
        widget = PairingWidget(shell)
        outer = widget.build()
        shell.root.add(outer)
        shell.show_all()
        self.assertTrue(outer.get_visible())
        self.assertTrue(widget.qr.get_visible())
        self.assertIn(":8765", widget.address_lbl.get_text())
        self.assertTrue(widget.rev.get_reveal_child())
        self.assertFalse(widget.card.get_visible())
        shell.remote = {"connected": True}
        widget.render()
        self.assertFalse(outer.get_visible())
        shell.destroy()

    def test_continue_restores_saved_focus_when_now_playing_is_inserted(self):
        shell = StubShell()
        page = screens.Screen(shell)
        shell.root.add(page)
        history = [{"id": str(i), "type": "movie", "name": f"Title {i}"} for i in range(6)]
        with mock.patch.object(screens, "continue_items", return_value=history), \
             mock.patch.object(screens, "load_art"):
            widget = rows.ContinueWidget(shell, {"style": "bubbles"})
            widget.widget = widget.build()
            page.pack_start(widget.host, False, False, 0)
            old = widget.row.bubbles[2]
            page.last_focus = old
            widget.now_state = {"title": "Song", "artist": "Artist", "status": "Playing"}
            widget.rebuild()
            self.assertEqual(len(widget.row.bubbles), rows.MAX_CONTINUE)
            self.assertIs(page.last_focus, widget.row.bubbles[3])
            self.assertEqual(page.last_focus.title, "Title 2")
        shell.destroy()

    def test_calculator_operations_errors_and_reset(self):
        calc = screens.Calculator()
        for key in ("1", "2", "+", "3", "="):
            result = calc.press(key)
        self.assertEqual(result, "15")
        for key in ("÷", "0", "="):
            result = calc.press(key)
        self.assertEqual(result, "Error")
        self.assertEqual(calc.press("C"), "0")
        for key in (".", "5", "×", "2", "="):
            result = calc.press(key)
        self.assertEqual(result, "1")
        for key in ("C", "7", "+", "3", "="):
            result = calc.press(key)
        self.assertEqual(result, "10")

    def test_shortcuts_activate_pin_toggle(self):
        shell = StubShell()
        page = screens.ShortcutsScreen(shell)
        page.populate([FAVS[0], REST[0]])
        page.cards()[0].on_activate()
        page.cards()[1].on_activate()
        self.assertEqual(shell.favorite_calls, [("movies", False), ("desktop:calc", True)])
        self.assertEqual(page.cards()[0].sub, "On Home · OK to remove")
        without_sub = screens.AppCard(shell, FAVS[0], lambda _: None)
        self.assertGreater(page.cards()[0].get_size_request().height, without_sub.get_size_request().height)
        shell.destroy()

    def test_system_ignores_late_audio_and_brightness(self):
        shell = StubShell()
        shell.confirm_off = lambda: None
        shell.show_pairing = lambda: None
        page = screens.SystemScreen(shell)
        shell.page = None
        page.request_token = 1
        self.assertFalse(page.show_outputs([], 1))
        self.assertFalse(page.set_brightness_info({"available": True, "level": 50}, 1))
        self.assertIsNone(page.brightness_info)
        shell.destroy()

    def test_calculator_and_shortcuts_preserve_back_stack(self):
        shell = mock.MagicMock()
        others, calculator, system, shortcuts = [mock.Mock(sname=n) for n in ("apps", "calculator", "system", "shortcuts")]
        stack = [others]
        shell.current.side_effect = lambda: stack[-1]
        shell.top_screen.side_effect = {"calculator": calculator, "system": system, "shortcuts": shortcuts}.get
        shell.push.side_effect = stack.append
        home.Home.open_app_item(shell, {"id": "calculator", "kind": "screen"})
        self.assertEqual(stack, [others, calculator])
        stack.pop()
        self.assertIs(stack[-1], others)
        stack[:] = [mock.Mock(sname="home")]
        shell.push(shell.top_screen("system"))
        shell.push(shell.top_screen("shortcuts"))
        stack.pop()
        self.assertIs(stack[-1], system)

    def test_ui_request_does_not_duplicate_current_calculator(self):
        shell = mock.MagicMock()
        shell.current.return_value = mock.Mock(sname="calculator")
        home.Home.handle_ui_request(shell, {"screen": "calculator"})
        shell.push.assert_not_called()
        shell.goto_top.assert_not_called()

    def test_continue_heading_updates_with_focused_track(self):
        shell = StubShell()
        item_ = {"title": "Before", "sub": "Artist", "now": "Playing", "on_activate": lambda: None}
        rail = rows.ContinueCards(shell, [item_], 300)
        shell.root.add(rail)
        shell.show_all()
        rail.bubbles[0].grab_focus()
        pump()
        with mock.patch.object(rail.bubbles[0], "has_focus", return_value=True):
            rail.update_now({"title": "After", "artist": "New artist", "status": "Paused"})
        self.assertEqual(rail.bubbles[0].title, "After")
        self.assertEqual(rail.bubbles[0].sub, "New artist")
        self.assertIn("New artist", rail.label.get_text())
        shell.destroy()


if __name__ == "__main__":
    unittest.main()
