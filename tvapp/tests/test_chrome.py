"""Home's language outside Home (widgets/chrome.py and its use in screens.py): the loading screen's orbiting mark
and its effects-off tick, the header's current-tab marker, Home's focus on cards, the shared look and backdrop."""
import datetime
from pathlib import Path
import sys
import unittest
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import cairo  # noqa: E402
import screens  # noqa: E402
from widgets import chrome, glass  # noqa: E402
from widgets.network import poly_points  # noqa: E402
from gi.repository import Gtk, GLib, GdkPixbuf  # noqa: E402


class FakeShell:
    scale = 1.0
    sw, sh = 1920, 1080

    def px(self, v):
        return round(v)

    def ensure_focus(self):
        pass


def surface(w, h):
    s = cairo.ImageSurface(cairo.FORMAT_ARGB32, w, h)
    return s, cairo.Context(s)


def pixel(surf, x, y):
    surf.flush()
    o = int(y) * surf.get_stride() + int(x) * 4
    b, g, r, a = surf.get_data()[o:o + 4]
    return r, g, b, a


def drain():
    while Gtk.events_pending():
        Gtk.main_iteration_do(False)


def look(**kw):
    cfg = {"transparency": False}
    cfg.update(kw)
    return glass.Look(cfg)


def solid_pixbuf(w, h, rgba=0x3366ccff):
    pb = GdkPixbuf.Pixbuf.new(GdkPixbuf.Colorspace.RGB, True, 8, w, h)
    pb.fill(rgba)
    return pb


class OrbitTest(unittest.TestCase):
    def test_point_walks_the_outline(self):
        pts = poly_points("hexagon", 100)
        self.assertEqual(chrome.orbit_point(pts, 0.0), pts[0])
        x, y = chrome.orbit_point(pts, 0.5)                     # half way round = the opposite vertex
        self.assertAlmostEqual(x, pts[3][0])
        self.assertAlmostEqual(y, pts[3][1])
        mx, my = chrome.orbit_point(pts, 1 / 12)                # middle of the first edge
        self.assertAlmostEqual(mx, (pts[0][0] + pts[1][0]) / 2)
        self.assertEqual(chrome.orbit_point(pts, 1.25), chrome.orbit_point(pts, 0.25))   # wraps

    def test_octagon_has_eight_stops(self):
        pts = poly_points("octagon", 50)
        self.assertAlmostEqual(chrome.orbit_point(pts, 3 / 8)[0], pts[3][0])

    def test_layout_box_holds_the_orbit_and_text_sits_below(self):
        for w, h in ((1920, 1080), (1280, 720)):
            L = chrome.loading_layout(w, h)
            bx, by, bw, bh = L["box"]
            o = L["orbit"]
            self.assertGreater(o, L["r"])
            self.assertLessEqual(bx, L["cx"] - o - 20 * L["s"])
            self.assertGreaterEqual(bx + bw, L["cx"] + o + 20 * L["s"])
            self.assertLessEqual(by, L["cy"] - o)
            self.assertGreater(L["ty"], by + bh - 40 * L["s"])     # title below the hexagon
            self.assertGreater(L["ty"], L["ky"])                   # kicker above the title
            self.assertLess(bw * bh, w * h * 0.15)                 # the animated box is a small part of the screen
        big, small = chrome.loading_layout(1920, 1080), chrome.loading_layout(1280, 720)
        self.assertAlmostEqual(small["r"], big["r"] * 2 / 3)

    def test_two_line_title_moves_the_block_up(self):
        one, two = chrome.loading_layout(1920, 1080, 1), chrome.loading_layout(1920, 1080, 2)
        self.assertLess(two["cy"], one["cy"])

    def test_effects_off_mark_is_a_lit_vertex(self):
        surf, cr = surface(240, 240)
        chrome.draw_orbit(cr, "hexagon", 120, 120, 100, 0.0, 1.0, effects=False)
        self.assertGreater(pixel(surf, 220, 120)[3], 200)           # vertex 0, lit
        self.assertEqual(pixel(surf, 20, 120)[3], 0)                # the opposite vertex stays dark


class BufferingLinesTest(unittest.TestCase):
    def test_status_and_meta(self):
        k, t, s, m = screens.buffering_lines({
            "media": {"name": "Interstellar"},
            "buffering": {"stage": "Connecting to peers…", "peers": 12, "speed": 2_400_000, "downloaded": 0,
                          "source": "1080p HDR · WEB-DL · 2.6 GB · 190 seeds"}})
        self.assertEqual((k, t), ("Getting ready", "Interstellar"))
        self.assertEqual(s, "Connecting to peers  ·  12 peers  ·  2.4 MB/s")
        self.assertEqual(m, "1080p HDR  ·  WEB-DL  ·  2.6 GB")          # seed count left out

    def test_episode_goes_to_the_kicker_and_empty_state(self):
        k, t, s, m = screens.buffering_lines({"media": {"name": "Below", "season": 1, "episode": 3}})
        self.assertEqual(k, "Season 1  ·  Episode 3")
        self.assertEqual(t, "Below")
        self.assertEqual(s, "Connecting")
        self.assertEqual(m, "")
        self.assertEqual(screens.buffering_lines({})[1], "Starting playback")


class LoadingViewTest(unittest.TestCase):
    def make(self, effects=True):
        win = Gtk.Window()
        win.set_default_size(640, 360)
        view = chrome.LoadingView(FakeShell(), look(visual_effects=effects))
        win.add(view)
        self.addCleanup(win.destroy)
        return win, view

    def test_timer_runs_only_while_shown(self):
        win, view = self.make()
        self.assertEqual(view.anim_src, 0)
        win.show_all()
        drain()
        self.assertTrue(view.anim_src)
        self.assertEqual(view.anim_ms, chrome.FRAME_MS)
        self.assertLessEqual(1000 / view.anim_ms, 15)
        view.hide()
        drain()
        self.assertEqual(view.anim_src, 0)

    def test_frame_invalidates_only_the_hexagon_box(self):
        win, view = self.make()
        win.show_all()
        drain()
        with mock.patch.object(view, "queue_draw_area") as qda, mock.patch.object(view, "queue_draw") as qd:
            view.anim_step()
        qd.assert_not_called()
        qda.assert_called_once_with(*view.layout()["box"])

    def test_status_change_redraws_only_the_text(self):
        win, view = self.make()
        view.set_text("Title", "Connecting", "1080p")
        win.show_all()
        drain()
        with mock.patch.object(view, "queue_draw_area") as qda, mock.patch.object(view, "queue_draw") as qd:
            view.set_text(status="Loading video  ·  3 peers")
        qd.assert_not_called()
        x, y, w, h = qda.call_args[0]
        bx, by, bw, bh = view.layout()["box"]
        self.assertGreaterEqual(y, by + bh - 20)                   # below the hexagon

    def test_effects_off_ticks_vertex_by_vertex_at_2fps(self):
        win, view = self.make(effects=False)
        win.show_all()
        drain()
        self.assertEqual(view.anim_ms, chrome.TICK_MS)
        seen = []
        for _ in range(7):
            view.anim_step()
            seen.append(round(view.u * 6) % 6)
        self.assertEqual(seen, [1, 2, 3, 4, 5, 0, 1])

    def test_switching_effects_retimes_the_animation(self):
        win, view = self.make(effects=True)
        win.show_all()
        drain()
        view.look.effects = False
        self.assertFalse(view.anim_step())                          # this source ends, a 2 fps one replaces it
        self.assertEqual(view.anim_ms, chrome.TICK_MS)
        self.assertTrue(view.anim_src)

    def test_draws_with_and_without_art(self):
        win, view = self.make()
        win.show_all()
        drain()
        for poster in (None, solid_pixbuf(200, 300)):
            view.set_art(poster=poster or False, art=solid_pixbuf(160, 90) if poster else False)
            surf, cr = surface(640, 360)
            view.on_draw(view, cr)
            L = view.layout()
            r, g, b, a = pixel(surf, L["cx"], L["cy"])
            self.assertEqual(a, 255)
            if poster:
                self.assertGreater(b, r)                            # the poster shows inside the hexagon


class HeaderTest(unittest.TestCase):
    def test_current_tab_gets_a_lit_hexagon(self):
        shell = FakeShell()
        tab = chrome.NavTab(shell, look(), "Movies")
        w, h = tab.get_size_request()
        probe = (w // 2, 36 + 25 + 10)                              # the marker under the label

        def marker(current):
            tab.set_current(current)
            surf, cr = surface(w, h)
            tab.paint(cr, cr.get_matrix(), 0.0, w, h)
            return pixel(surf, *probe)
        r, g, b, a = marker(True)
        self.assertGreater(a, 200)
        self.assertGreater(b, r)                                    # the accent, not white
        self.assertTrue(tab.current)
        self.assertEqual(marker(False)[3], 0)

    def test_focus_draws_homes_tile_only_when_focused(self):
        tab = chrome.NavTab(FakeShell(), look(visual_effects=False), "Series")     # no scale: a fixed edge
        w, h = tab.get_size_request()
        edge = ((w - tab.lw - 32) // 2, 36)                          # the focus tile's left hairline
        alphas = []
        for f in (0.0, 1.0):
            surf, cr = surface(w, h)
            tab.paint(cr, cr.get_matrix(), f, w, h)
            alphas.append(max(pixel(surf, edge[0] + d, edge[1])[3] for d in (-1, 0, 1)))
        self.assertEqual(alphas[0], 0)
        self.assertGreater(alphas[1], 120)

    def test_phone_chip_net_and_clock(self):
        shell = FakeShell()
        chip = chrome.PhoneChip(shell, None, look())
        w0 = chip.get_size_request()[0]
        chip.set_lines("Phone connected", "Scan to pair another")
        self.assertEqual(chip.lines, ("Phone connected", "Scan to pair another"))
        self.assertGreater(chip.get_size_request()[0], w0)
        net = chrome.NetLabel(shell, look())
        net.set_net({"connected": True, "type": "wifi", "ssid": "Jamil", "signal": 80})
        self.assertEqual(net.get_text(), "Jamil")
        net.set_net({"connected": False})
        self.assertEqual(net.get_text(), "Offline")
        clock = chrome.HeaderClock(shell, look())
        with mock.patch.object(clock, "queue_draw") as qd:
            clock.set_now(datetime.datetime(2026, 10, 26, 6, 29, 5))
            clock.set_now(datetime.datetime(2026, 10, 26, 6, 29, 40))     # same minute: no redraw
        self.assertEqual(qd.call_count, 1)
        self.assertEqual(clock.time, "06:29")


class CardTest(unittest.TestCase):
    def card(self, **kw):
        c = chrome.PosterCard(FakeShell(), look(**kw), {"name": "Hope", "subtitle": "2026"}, "poster", 190, 285)
        c.set_pixbuf(solid_pixbuf(190, 285))
        return c

    def render(self, card, f):
        w, h = card.get_size_request()
        surf, cr = surface(w, h)
        card.paint(cr, cr.get_matrix(), f, w, h)
        return surf

    def test_focus_scales_glows_and_brightens_the_hairline(self):
        card = self.card()
        rest, focus = self.render(card, 0.0), self.render(card, 1.0)
        top = card.pad_top
        above = top - 8                                              # outside the art at rest
        self.assertEqual(pixel(rest, card.pad_x + 95, above)[3], 0)
        self.assertGreater(pixel(focus, card.pad_x + 95, above)[3], 0)          # grown art / glow
        edge_x = card.pad_x + 95
        self.assertLess(pixel(rest, card.pad_x - 6, top + 140)[3], 5)
        self.assertGreater(pixel(focus, card.pad_x - 6, top + 140)[3], 5)      # glow beside it

    def test_effects_off_has_no_scale_or_glow_but_a_heavier_line(self):
        card = self.card(visual_effects=False)
        focus = self.render(card, 1.0)
        self.assertEqual(pixel(focus, card.pad_x + 95, card.pad_top - 8)[3], 0)
        self.assertEqual(chrome.focus_line(card.look, 1.0), 2.0)
        self.assertEqual(chrome.focus_line(look(), 1.0), 1.0)

    def test_art_is_baked_in_the_tile_shape(self):
        card = self.card(tile_shape="chamfer")
        corner = pixel(self.render(card, 0.0), card.pad_x + 2, card.pad_top + 2)
        self.assertEqual(corner[3], 0)                              # the cut corner is empty
        card.look.tile_shape = "rounded"
        self.assertIsNot(card.art_surface(), None)
        self.assertEqual(card.art_key[0], "rounded")                # re-baked for the new shape

    def test_rail_cards_are_drawn_and_focusable_placeholders_are_not(self):
        shell = FakeShell()
        with mock.patch.object(screens, "load_art"):
            rail = screens.Rail(shell, "Popular", kind="poster")
            rail._skeleton()
            kids = rail.row.get_children()
            self.assertTrue(kids and all(not k.get_can_focus() for k in kids))
            rail.set_items([{"id": "tt1", "name": "A"}, {"id": "tt2", "name": "B"}])
        self.assertEqual(len(rail.cards), 2)
        self.assertTrue(all(isinstance(c, chrome.PosterCard) and c.get_can_focus() for c in rail.cards))
        self.assertEqual(rail.cards[0].item["id"], "tt1")


class SharedTest(unittest.TestCase):
    def test_refresh_look_updates_in_place(self):
        shell = FakeShell()
        lk = chrome.look(shell)
        self.assertFalse(chrome.refresh_look(shell, {"node_shape": lk.node_shape, "tile_shape": lk.tile_shape,
                                                     "transparency": lk.transparency,
                                                     "visual_effects": lk.effects}))
        self.assertTrue(chrome.refresh_look(shell, {"node_shape": "octagon", "visual_effects": False}))
        self.assertIs(chrome.look(shell), lk)
        self.assertEqual((lk.node_shape, lk.effects), ("octagon", False))

    def test_backdrop_surface_is_shared(self):
        shell = FakeShell()
        a, b = chrome.SharedBackdrop(shell, look()), chrome.SharedBackdrop(shell, look())
        self.assertIs(a.surface(320, 180), b.surface(320, 180))
        a.avoid = lambda: [(0, 0, 100, 100)]
        self.assertIsNot(a.surface(320, 180), b.surface(320, 180))

    def test_glass_button_label_resizes(self):
        b = chrome.GlassButton(FakeShell(), None, look(), "Play", "play", primary=True)
        w0 = b.get_size_request()[0]
        b.set_label("Resume S1 E2  ·  12:04")
        self.assertEqual(b.get_label(), "Resume S1 E2  ·  12:04")
        self.assertGreater(b.get_size_request()[0], w0)
        pressed = []
        b.on_activate = lambda: pressed.append(1)
        b.press()
        self.assertEqual(pressed, [1])


class SpinnerTest(unittest.TestCase):
    def test_spinner_timer_follows_mapping(self):
        win = Gtk.Window()
        sp = chrome.HexSpinner(FakeShell(), look())
        win.add(sp)
        self.addCleanup(win.destroy)
        win.show_all()
        drain()
        self.assertTrue(sp.anim_src)
        win.hide()
        drain()
        self.assertEqual(sp.anim_src, 0)


if __name__ == "__main__":
    unittest.main()
