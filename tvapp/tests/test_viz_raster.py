from pathlib import Path
import sys
import unittest
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

try:
    import cairo
    import homescreen
except Exception:  # no PyGObject / GTK typelibs
    homescreen = None

W, H = 200, 120


def photo():
    s = cairo.ImageSurface(cairo.FORMAT_RGB24, W, H)
    c = cairo.Context(s)
    g = cairo.LinearGradient(0, 0, W, H)
    g.add_color_stop_rgb(0, 0.9, 0.4, 0.1)
    g.add_color_stop_rgb(1, 0.1, 0.5, 0.8)
    c.set_source(g)
    c.paint()
    return s


def lines(cr):
    cr.set_line_width(2)
    cr.set_source_rgba(1, 1, 1, 0.5)
    for i in range(5):
        cr.move_to(10 * i, 5)
        cr.line_to(190 - 7 * i, 110)
        cr.stroke()
    cr.set_source_rgba(0.2, 0.9, 0.4, 0.3)
    cr.arc(100, 60, 30, 0, 6.28)
    cr.fill()


def pixels(surf):
    surf.flush()
    return bytes(surf.get_data())


@unittest.skipIf(homescreen is None, "GTK bindings unavailable")
class FramebufferTests(unittest.TestCase):
    def canvas(self, dim=1.0, extra=lines):
        c = homescreen.PhotoCanvas()
        c.cur = photo()
        c.dim = dim
        c.extra = extra
        patches = (mock.patch.object(c, "get_allocated_width", return_value=W),
                   mock.patch.object(c, "get_allocated_height", return_value=H))
        for p in patches:
            p.start()
            self.addCleanup(p.stop)
        return c

    def render(self, c, clip=None):
        out = cairo.ImageSurface(cairo.FORMAT_RGB24, W, H)
        cr = cairo.Context(out)
        if clip:
            cr.rectangle(*clip)
            cr.clip()
        c.on_draw(None, cr)
        return out

    def direct(self, c, clip=None):
        out = cairo.ImageSurface(cairo.FORMAT_RGB24, W, H)
        cr = cairo.Context(out)
        if clip:
            cr.rectangle(*clip)
            cr.clip()
        homescreen.paint_background(c, cr, W, H)
        c.extra(cr)
        return out

    def assertClose(self, a, b, tol=2):
        a, b = pixels(a), pixels(b)
        self.assertEqual(len(a), len(b))
        self.assertLessEqual(max(abs(x - y) for x, y in zip(a, b)), tol)

    def test_same_pixels_dimmed(self):
        c = self.canvas()
        self.assertClose(self.render(c), self.direct(c))

    def test_same_pixels_undimmed_and_partial_dim(self):
        for dim in (0.0, 0.5):
            c = self.canvas(dim=dim)
            self.assertClose(self.render(c), self.direct(c))

    def test_partial_clip_leaves_outside_untouched(self):
        c = self.canvas()
        clip = (40, 20, 80, 60)
        out = self.render(c, clip)
        self.assertClose(out, self.direct(c, clip))
        data = pixels(out)
        self.assertEqual(data[:4], b"\0\0\0\0")      # outside the clip nothing was painted

    def test_framebuffer_lifecycle(self):
        c = self.canvas()
        self.render(c)
        fb = c.fb
        self.assertIsNotNone(fb)
        self.render(c)
        self.assertIs(c.fb, fb)                       # reused
        c.extra = None
        self.render(c)
        self.assertIsNone(c.fb)                       # freed when nothing is drawn over the photo
        c.extra = lines
        with mock.patch.object(c, "get_allocated_width", return_value=W + 10):
            out = cairo.ImageSurface(cairo.FORMAT_RGB24, W + 10, H)
            c.on_draw(None, cairo.Context(out))
        self.assertEqual(c.fb.get_width(), W + 10)    # recreated on size change


@unittest.skipIf(homescreen is None, "GTK bindings unavailable")
class BatchTests(unittest.TestCase):
    def test_groups_by_colour_alpha_width(self):
        red, white = (1, 0, 0), (1, 1, 1)
        segs = [(red, 0.50, 1.0, 0, 0, 1, 1), (red, 0.51, 1.1, 2, 2, 3, 3),
                (white, 0.50, 1.0, 4, 4, 5, 5), (red, 0.50, 2.0, 6, 6, 7, 7)]
        out = homescreen.batch_segments(segs, 8)
        self.assertEqual(len(out), 3)
        self.assertEqual(out[0][0], red)
        self.assertEqual(out[0][3], [(0, 0, 1, 1), (2, 2, 3, 3)])
        self.assertEqual(out[1][0], white)

    def test_quantization(self):
        seg = lambda a, w: ((1, 1, 1), a, w, 0, 0, 1, 1)
        out = homescreen.batch_segments([seg(0.33, 1.2), seg(0.001, 1.3), seg(0.0, 1.0), seg(2.0, 1.76)], 8)
        self.assertEqual(len(out), 3)                 # alpha 0 dropped; tiny alpha kept at the lowest step
        self.assertAlmostEqual(out[0][1], 0.375)      # 0.33 -> 3/8
        self.assertEqual(out[0][2], 1.0)              # 1.2 -> 1.0
        self.assertAlmostEqual(out[1][1], 0.125)
        self.assertEqual(out[1][2], 1.5)              # 1.3 -> 1.5
        self.assertAlmostEqual(out[2][1], 1.0)        # clamped to amax
        self.assertEqual(out[2][2], 2.0)

    def test_amax_scales_levels(self):
        out = homescreen.batch_segments([((1, 1, 1), 0.07, 1.0, 0, 0, 1, 1)], 6, amax=0.15)
        self.assertAlmostEqual(out[0][1], 0.075)

    def test_stroke_batches_one_stroke_per_group(self):
        cr = mock.Mock()
        homescreen.stroke_batches(cr, [((1, 1, 1), 0.5, 1.0, [(0, 0, 1, 1), (2, 2, 3, 3)]),
                                       ((1, 0, 0), 0.5, 1.0, [(4, 4, 5, 5)])])
        self.assertEqual(cr.stroke.call_count, 2)
        self.assertEqual(cr.move_to.call_count, 3)


if __name__ == "__main__":
    unittest.main()
