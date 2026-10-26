"""The drawn glass kit (widgets/glass.py): shapes follow the settings, toggles show their state, the
transparency/effects switches take their plain paths, and nothing keeps a timer running when calm."""
from pathlib import Path
import sys
import unittest
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import cairo  # noqa: E402
from widgets import glass  # noqa: E402
from gi.repository import Gtk  # noqa: E402
from widgets.network import poly_points, point_in_poly  # noqa: E402


class Solid:
    """A frost source with transparency off: paint_frost must never sample it."""
    transparency = False
    blur_k = 1

    def blur_layers(self):
        raise AssertionError("frost sampled with transparency off")


def surface(w=120, h=120):
    s = cairo.ImageSurface(cairo.FORMAT_ARGB32, w, h)
    return s, cairo.Context(s)


def pixel(surf, x, y):
    surf.flush()
    o = y * surf.get_stride() + x * 4
    b, g, r, a = surf.get_data()[o:o + 4]
    return r, g, b, a


class FakeShell:
    scale = 1.0

    def px(self, v):
        return round(v)


def drain():
    while Gtk.events_pending():
        Gtk.main_iteration_do(False)


class LookTest(unittest.TestCase):
    def test_defaults_and_values(self):
        d = glass.Look({})
        self.assertEqual((d.node_shape, d.tile_shape, d.transparency, d.effects), ("hexagon", "rounded", True, True))
        o = glass.Look({"node_shape": "octagon", "tile_shape": "chamfer", "transparency": False,
                        "visual_effects": False})
        self.assertEqual(o.key(), ("octagon", "chamfer", False, False))
        self.assertEqual(glass.Look({"node_shape": "blob"}).node_shape, "hexagon")


class ShapeTest(unittest.TestCase):
    def path_ops(self, shape):
        _s, cr = surface()
        glass.tile_path(cr, shape, 10, 10, 100, 60)
        return [op for op, _pts in cr.copy_path()]

    def test_tile_shape_rounded_vs_chamfer(self):
        self.assertIn(cairo.PATH_CURVE_TO, self.path_ops("rounded"))
        ops = self.path_ops("chamfer")
        self.assertNotIn(cairo.PATH_CURVE_TO, ops)
        self.assertEqual(ops.count(cairo.PATH_LINE_TO), 7)        # eight corners, cut

    def test_badge_follows_node_shape(self):
        r = 40
        probe = (round(0.9 * r), round(0.3 * r))                   # inside the octagon, outside the hexagon
        self.assertTrue(point_in_poly(*probe, poly_points("octagon", r)))
        self.assertFalse(point_in_poly(*probe, poly_points("hexagon", r)))
        alphas = {}
        for shape in ("hexagon", "octagon"):
            surf, cr = surface()
            cr.translate(60, 60)
            look = glass.Look({"node_shape": shape, "visual_effects": False})
            glass.draw_badge(cr, cr.get_matrix(), None, Solid(), look, r, "system", None, 0.0)
            alphas[shape] = pixel(surf, 60 + probe[0], 60 + probe[1])[3]
        self.assertEqual(alphas["hexagon"], 0)
        self.assertGreater(alphas["octagon"], 200)

    def test_toggle_on_is_filled_off_is_hairline(self):
        def centre_and_ring(on):
            surf, cr = surface(60, 60)
            glass.draw_toggle(cr, "hexagon", 30, 30, 20, on)
            return pixel(surf, 30, 30)[3], pixel(surf, 30 + 13, 30)[3]
        on_centre, on_ring = centre_and_ring(True)
        off_centre, off_ring = centre_and_ring(False)
        self.assertGreater(on_centre, 200)
        self.assertGreater(on_ring, 200)
        self.assertEqual(off_centre, 0)
        self.assertEqual(off_ring, 0)

    def test_avoid_rects_keep_lines_off(self):
        pts = [(0, 50), (100, 50), (200, 50)]
        self.assertTrue(glass.crosses(pts[0], pts[1], [(40, 40, 20, 20)]))
        self.assertFalse(glass.crosses(pts[1], pts[2], [(40, 40, 20, 20)]))


class SwitchesTest(unittest.TestCase):
    def test_transparency_off_panel_is_solid(self):
        surf, cr = surface(200, 100)
        glass.paint_panel(cr, None, Solid(), glass.Look({"transparency": False}), 200, 100, 1.0)
        r, g, b, a = pixel(surf, 100, 50)
        self.assertEqual(a, 255)
        expect = [round((c * 0.93 + 0.07) * 255) for c in glass.SOLID]     # Home's solid glass + white tint
        for got, want in zip((r, g, b), expect):
            self.assertAlmostEqual(got, want, delta=2)

    def test_dim_follows_photo_brightness(self):
        self.assertEqual(glass.dim_for(0.1, 0.15, 0.5), 0.5)
        self.assertGreater(glass.dim_for(0.8, 0.15, 0.5), glass.dim_for(0.5, 0.15, 0.5))
        self.assertLessEqual(glass.dim_for(1.0, 0.15, 0.5, 0.85), 0.85)


class FocusTest(unittest.TestCase):
    def setUp(self):
        # Xvfb has no window manager, so the test window is never active and GTK sends no focus-in events:
        # treat "is the window's focus widget" as focus, as on the TV where the window is always active.
        patch = mock.patch.object(glass.Focusable, 'has_focus', lambda self: self.is_focus())
        patch.start()
        self.addCleanup(patch.stop)
        self.win = Gtk.Window()
        self.box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        self.win.add(self.box)
        self.addCleanup(self.win.destroy)

    def row(self, effects, **kw):
        row = glass.GlassRow(FakeShell(), Solid(), glass.Look({"visual_effects": effects, "transparency": False}),
                             "Title", key=("k",), **kw)
        self.box.pack_start(row, False, False, 0)
        return row

    def test_effects_off_snaps_without_a_timer(self):
        a, b = self.row(False), self.row(False)
        self.win.show_all()
        drain()
        b.grab_focus()
        b.on_focus()
        self.assertEqual(b.f, 1.0)
        self.assertNotIn(b, glass.TICKER.live)

    def test_effects_on_eases_then_the_ticker_stops(self):
        a, b = self.row(True), self.row(True)
        self.win.show_all()
        drain()
        b.grab_focus()
        b.on_focus()
        self.assertIn(b, glass.TICKER.live)
        self.assertTrue(glass.TICKER.src)
        for _ in range(40):
            glass.TICKER.last -= 0.07              # pretend 70 ms passed between frames
            if not glass.TICKER.step():
                break
        self.assertEqual(b.f, 1.0)
        self.assertEqual(glass.TICKER.src, 0)
        self.assertFalse(glass.TICKER.live)

    def test_activate_and_toggle_state(self):
        hits = []
        row = self.row(False, toggle=False, on_activate=lambda: hits.append(1))
        row.press()
        self.assertEqual(hits, [1])
        row.set_value(toggle=True)
        self.assertTrue(row.toggle)
        row.set_sensitive(False)
        row.press()
        self.assertEqual(hits, [1])


class BackdropTest(unittest.TestCase):
    def test_rendered_once_per_size(self):
        win = Gtk.Window()
        self.addCleanup(win.destroy)
        bd = glass.Backdrop(FakeShell(), glass.Look({}))
        win.add(bd)
        win.set_default_size(320, 180)
        calls = []
        real = bd.render
        bd.render = lambda w, h: calls.append((w, h)) or real(w, h)
        win.show_all()
        drain()
        for _ in range(3):
            surf, cr = surface(320, 180)
            bd.on_draw(bd, cr)
        self.assertEqual(len(calls), 1)
        self.assertEqual(bd.blur_layers(), [])           # no Home photo: navy, nothing to sample

    def test_snapshot_follows_home_photo(self):
        shell = FakeShell()
        small = cairo.ImageSurface(cairo.FORMAT_RGB24, 12, 7)
        shell.photo_canvas = mock.Mock(blur_cur=small)
        shell.photo_canvas.get_allocated_width.return_value = 192
        bd = glass.Backdrop(shell, glass.Look({}))
        layers = bd.blur_layers()
        self.assertEqual(len(layers), 1)
        self.assertEqual(layers[0][0].get_width(), 48)    # smoothed to 4x the 1/16 sample
        self.assertAlmostEqual(bd.blur_k, 4.0)
        bd.set_look(glass.Look({"transparency": False}))
        self.assertEqual(bd.blur_layers(), [])
        self.assertFalse(bd.transparency)


if __name__ == "__main__":
    unittest.main()
