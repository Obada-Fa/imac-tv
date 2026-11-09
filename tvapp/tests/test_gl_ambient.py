"""The GPU visualizer: protocol, interpolation, painter, Home's hand-over to it and back, the live renderer."""
import os
from pathlib import Path
import subprocess
import sys
import time
import unittest
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(1, str(Path(__file__).resolve().parents[1] / "widgets"))

import gl_ambient  # noqa: E402

try:
    import gi
    gi.require_version("Gtk", "3.0")
    from gi.repository import Gtk, Gdk, GLib
    import cairo
    import homescreen
except Exception:  # no GTK
    homescreen = None


def frame(t, x=0.0, n=3):
    return {"t": t, "scale": 1.0, "pts": [(x + i, 10.0) for i in range(n)], "tails": None,
            "alphas": [1.0] * n, "links": [(0, 1, 1.0)], "lv": {"bass": t, "mid": 0.0, "treble": 0.0, "beat": 0.0},
            "fade": 1.0, "dim": 1.0, "cur": None, "prev": None, "pal": [(1, 0, 0)], "hub": "hexagon",
            "glow_r": 10, "viz": {"depth": None, "glyph": None, "ang": [0.0] * n, "heads": None, "colour": 0,
                                  "trail": 0.0, "pulses": []}}


class ProtocolTest(unittest.TestCase):
    def test_unpacker_reassembles_split_messages(self):
        data = gl_ambient.pack(("show", True)) + gl_ambient.pack(("frame", {"t": 1.0}))
        u = gl_ambient.Unpacker()
        out = []
        for i in range(0, len(data), 3):
            out += u.feed(data[i:i + 3])
        self.assertEqual(out, [("show", True), ("frame", {"t": 1.0})])


class InterpolationTest(unittest.TestCase):
    def test_blends_positions_and_levels(self):
        a, b = frame(1.0, x=0.0), frame(2.0, x=10.0)
        f = gl_ambient.interpolate(a, b, 0.5)
        self.assertEqual(f["pts"][0], (5.0, 10.0))
        self.assertAlmostEqual(f["lv"]["bass"], 1.5)
        self.assertAlmostEqual(f["t"], 1.5)
        self.assertIs(f["links"], b["links"])

    def test_different_counts_use_the_newer_frame(self):
        a, b = frame(1.0, n=3), frame(2.0, n=4)
        self.assertIs(gl_ambient.interpolate(a, b, 0.5), b)
        self.assertIs(gl_ambient.interpolate(None, b, 0.5), b)

    def test_particles_blend_and_links_fade(self):
        a = {"scale": 1.0, "pts": [(0.0, 0.0), (10.0, 0.0), (500.0, 0.0)], "alpha": [1.0, 0.0, 0.0],
             "links": [(0, 1, 0.4)]}
        b = {"scale": 1.0, "pts": [(2.0, 0.0), (12.0, 0.0), (-50.0, 300.0)], "alpha": [0.5, 1.0, 0.2],
             "links": [(1, 2, 0.2)]}
        f = gl_ambient.interpolate_field(a, b, 0.5)
        self.assertEqual(f["pts"][0], (1.0, 0.0))
        self.assertAlmostEqual(f["alpha"][0], 0.75)
        self.assertEqual(f["pts"][2], (-50.0, 300.0))       # left and came back in elsewhere: no sweep across
        self.assertEqual(f["alpha"][2], 0.2)
        self.assertEqual(f["links"], [(0, 1, 0.2), (1, 2, 0.1)])   # one fading out, one fading in

    def test_particles_blend_without_a_shape(self):
        def quiet(t, x):
            f = dict(frame(t, n=0), viz=None)
            f["field"] = {"scale": 1.0, "pts": [(x, 5.0)], "alpha": [1.0], "links": []}
            return f
        f = gl_ambient.interpolate(quiet(1.0, 0.0), quiet(2.0, 10.0), 0.25)
        self.assertEqual(f["field"]["pts"], [(2.5, 5.0)])
        self.assertAlmostEqual(f["t"], 1.25)
        g = dict(frame(2.0, n=4), field=quiet(2.0, 10.0)["field"])     # a shape appears: particles still glide
        self.assertEqual(gl_ambient.interpolate(quiet(1.0, 0.0), g, 0.5)["field"]["pts"], [(5.0, 5.0)])

    def test_pick_brackets_render_time(self):
        fs = [frame(1.0), frame(2.0), frame(3.0)]
        fa, fb, u = gl_ambient.pick(fs, 2.25)
        self.assertIs(fa, fs[1])
        self.assertIs(fb, fs[2])
        self.assertAlmostEqual(u, 0.25)
        self.assertEqual(gl_ambient.pick(fs, 9.0), (None, fs[2], 1.0))
        self.assertEqual(gl_ambient.pick(fs, 0.0), (None, fs[0], 1.0))
        self.assertEqual(gl_ambient.pick([], 1.0), (None, None, 1.0))


class PainterTest(unittest.TestCase):
    def test_primitives_become_triangles(self):
        p = gl_ambient.Painter(10)
        p.segs([((1, 1, 1), 0.5, 2.0, 0, 0, 10, 0), ((1, 1, 1), 0.0, 2.0, 0, 0, 10, 0)], 8, 0.4)
        self.assertEqual(len(p.v), 6 * 8)                 # one quad; the invisible one is dropped
        self.assertEqual(p.v[5], 0.4)                     # alpha capped like batch_segments
        self.assertEqual(p.v[6], -9.0)                    # solid
        p = gl_ambient.Painter(10)
        p.fill([(0, 0), (1, 0), (1, 1), (0, 1)], (1, 1, 1), 0.5)
        self.assertEqual(len(p.v), 6 * 8)
        p = gl_ambient.Painter(10)
        p.glow(50, 50, (1, 0, 0), 0.3)
        self.assertEqual(len(p.v), 6 * 8)
        self.assertEqual((p.v[0], p.v[1], p.v[6], p.v[7]), (40, 40, -1.0, -1.0))

    def test_paints_a_whole_frame(self):
        import vizpaint
        p = gl_ambient.Painter(10)
        vizpaint.paint(p, frame(1.0), [(1, 0, 0), (0, 1, 0), (0, 0, 1)], "hexagon")
        self.assertGreater(len(p.v), 0)
        self.assertEqual(len(p.v) % 8, 0)

    def test_paints_particles_and_a_still_shape(self):
        import vizpaint
        p = gl_ambient.Painter(10)
        vizpaint.paint_field(p, {"scale": 1.0, "pts": [(0, 0), (50, 0), (90, 0)], "alpha": [1.0, 0.5, 0.0],
                                 "links": [(0, 1, 0.5), (1, 2, 0.0)]})
        self.assertEqual(len(p.v), (6 + 6 + 6) * 8)      # one line, two diamonds; the invisible ones dropped
        p = gl_ambient.Painter(10)
        g = dict(frame(1.0), viz=None, e=1.0, centre=(0.0, 0.0), rmax=100.0)
        vizpaint.paint_still(p, g)
        self.assertEqual(len(p.v), (6 + 3 * 6) * 8)      # one line, three diamonds


@unittest.skipIf(homescreen is None, "GTK bindings unavailable")
class HomeHandOverTest(unittest.TestCase):
    def screen(self, **kw):
        s = mock.Mock()
        s.gl, s.gl_on, s.gl_holes, s.gl_sent = None, False, None, set()
        s.viz, s.viz_stop = mock.Mock(palette=[(1, 0, 0)]), False
        s.idle.ambient = True
        s.canvas = mock.Mock(dim=1.0, cur=None, prev=None, fade=1.0, only=None)
        s.canvas.fading.return_value = False
        s.shape_geom = dict(frame(1.0), viz={"x": 1})
        s.field_geom = None
        s.style, s.chrome, s.dust_amt = "network", 0.0, 0.0
        s.shell.scale = 1.0
        s.cfg = {"gpu_viz": "auto", "node_shape": "hexagon"}
        s.active.return_value = True
        s.glow_sprite.return_value = (None, 10)
        s.gl_hole_rects.return_value = [(10, 10, 100, 50)]
        for k, v in kw.items():
            setattr(s, k, v)
        S = homescreen.AmbientHomeScreen
        s.gl_wanted = lambda: S.gl_wanted(s)
        s.gl_hide = lambda: S.gl_hide(s)
        s.gl_stop = lambda: S.gl_stop(s)
        s.gl_frame = lambda keys: S.gl_frame(s, keys)
        return s

    def setUp(self):
        gl_ambient.GLLink.failed = False

    def tearDown(self):
        gl_ambient.GLLink.failed = False

    def test_no_gpu_never_starts_a_renderer(self):
        s = self.screen()
        with mock.patch.object(homescreen.gpu, "use_gpu_viz", return_value=False):
            homescreen.AmbientHomeScreen.gl_sync(s)
        s.gl_start.assert_not_called()
        self.assertFalse(s.gl_on)

    def test_hands_frames_to_a_ready_renderer_and_shows_it(self):
        link = mock.Mock(ready=True)
        link.alive.return_value = True
        s = self.screen(gl=link)
        with mock.patch.object(homescreen.gpu, "use_gpu_viz", return_value=True):
            homescreen.AmbientHomeScreen.gl_sync(s)
        self.assertTrue(s.gl_on)
        link.show.assert_called_once_with(True)
        sent = link.frame.call_args[0][0]
        self.assertEqual(sent["pts"], s.shape_geom["pts"])
        self.assertEqual(sent["dim"], 1.0)
        link.send.assert_any_call(("holes", [(10, 10, 100, 50)]))
        self.assertIsNotNone(s.canvas.only)                  # cairo now paints only the holes

    def test_renderer_dies_falls_back_to_cairo_for_the_session(self):
        link = mock.Mock(ready=True, error="boom")
        link.alive.return_value = False
        s = self.screen(gl=link, gl_on=True)
        with mock.patch.object(homescreen.gpu, "use_gpu_viz", return_value=True):
            homescreen.AmbientHomeScreen.gl_sync(s)
        self.assertTrue(gl_ambient.GLLink.failed)
        link.stop.assert_called_once()
        self.assertFalse(s.gl_on)
        self.assertIsNone(s.gl)
        self.assertIsNone(s.canvas.only)
        s.canvas.queue_draw.assert_called()                 # the whole visualizer back on cairo
        s2 = self.screen()
        with mock.patch.object(homescreen.gpu, "use_gpu_viz", return_value=True):
            self.assertFalse(homescreen.AmbientHomeScreen.gl_wanted(s2))

    def test_leaving_the_stage_hides_it(self):
        link = mock.Mock(ready=True)
        link.alive.return_value = True
        s = self.screen(gl=link, gl_on=True)
        s.idle.ambient = False                               # woken up: the tiles come back on cairo
        with mock.patch.object(homescreen.gpu, "use_gpu_viz", return_value=True):
            homescreen.AmbientHomeScreen.gl_sync(s)
        link.show.assert_called_once_with(False)
        self.assertFalse(s.gl_on)

    def test_quiet_particles_go_to_the_gpu(self):
        link = mock.Mock(ready=True)
        link.alive.return_value = True
        fg = {"scale": 1.0, "pts": [(1.0, 2.0)], "alpha": [1.0], "links": [], "t": 5.0}
        s = self.screen(gl=link, shape_geom=None, field_geom=fg, viz=None)
        s.canvas.dim = 0.0
        with mock.patch.object(homescreen.gpu, "use_gpu_viz", return_value=True):
            homescreen.AmbientHomeScreen.gl_sync(s)
        self.assertTrue(s.gl_on)
        sent = link.frame.call_args[0][0]
        self.assertEqual(sent["field"]["pts"], [(1.0, 2.0)])
        self.assertEqual((sent["pts"], sent["t"], sent["dim"]), ([], 5.0, 0.0))

    def test_still_shape_carries_what_paint_still_needs(self):
        link = mock.Mock(ready=True)
        link.alive.return_value = True
        g = dict(frame(1.0), viz=None, e=0.8, centre=(3.0, 4.0), rmax=50.0)
        s = self.screen(gl=link, shape_geom=g, viz=None)
        with mock.patch.object(homescreen.gpu, "use_gpu_viz", return_value=True):
            homescreen.AmbientHomeScreen.gl_sync(s)
        sent = link.frame.call_args[0][0]
        self.assertEqual((sent["e"], sent["centre"], sent["rmax"], sent["viz"]), (0.8, (3.0, 4.0), 50.0, None))

    def test_not_while_cairo_still_paints_outside_the_frame(self):
        with mock.patch.object(homescreen.gpu, "use_gpu_viz", return_value=True):
            s = self.screen(chrome=0.4)                      # tiles still fading out
            self.assertFalse(homescreen.AmbientHomeScreen.gl_wanted(s))
            s = self.screen(dust_amt=0.6)                    # the network's dust drifts on its own
            s.morph.target = None
            self.assertFalse(homescreen.AmbientHomeScreen.gl_wanted(s))
            s = self.screen(shape_geom=None)                 # nothing moves on the stage
            self.assertFalse(homescreen.AmbientHomeScreen.gl_wanted(s))
            s = self.screen(style="bubbles")
            self.assertFalse(homescreen.AmbientHomeScreen.gl_wanted(s))
            self.assertTrue(homescreen.AmbientHomeScreen.gl_wanted(self.screen()))

    def test_another_app_in_front_hides_it(self):
        """supervise() stops the timer when Home isn't active; the GPU window must go with it, or the
        renderer keeps drawing the full screen behind YouTube."""
        link = mock.Mock(ready=True)
        link.alive.return_value = True
        s = self.screen(gl=link, gl_on=True, disposed=False, audio=None, shape_id=7)
        s.active.return_value = False
        with mock.patch.object(homescreen.GLib, "source_remove"):
            homescreen.AmbientHomeScreen.supervise(s)
        link.show.assert_called_once_with(False)
        self.assertFalse(s.gl_on)
        self.assertEqual(s.shape_id, 0)

    def test_photo_canvas_clips_redraws_to_the_holes(self):
        cv = homescreen.PhotoCanvas()
        cv.only = cairo.Region(cairo.RectangleInt(0, 0, 10, 10))
        with mock.patch.object(Gtk.DrawingArea, "queue_draw_region") as q:
            cv.queue_draw_region(cairo.Region(cairo.RectangleInt(100, 100, 10, 10)))
            q.assert_not_called()                            # nothing of it shows
            cv.queue_draw()
            self.assertEqual(q.call_args[0][1].get_extents().width, 10)

    def test_cursor_hidden_in_ambient(self):
        win = mock.Mock()
        win.get_display.return_value = Gdk.Display.get_default()
        s = mock.Mock()
        s.get_toplevel.return_value.get_window.return_value = win
        homescreen.AmbientHomeScreen.ambient_cursor(s, True)
        self.assertEqual(win.set_cursor.call_args[0][0].get_cursor_type(), Gdk.CursorType.BLANK_CURSOR)
        homescreen.AmbientHomeScreen.ambient_cursor(s, False)
        win.set_cursor.assert_called_with(None)


@unittest.skipIf(homescreen is None or not os.environ.get("DISPLAY"), "needs a (private) X display")
class StaleFramesTest(unittest.TestCase):
    def test_stops_drawing_once_home_stops_feeding(self):
        r = gl_ambient.Renderer.__new__(gl_ambient.Renderer)
        r.shown, r.fed_at = True, 100.0
        self.assertTrue(r.drawing(100.0 + gl_ambient.STALE_S / 2))
        self.assertFalse(r.drawing(100.0 + gl_ambient.STALE_S + 0.01))   # settled: keep the last image
        r.shown = False
        self.assertFalse(r.drawing(100.0))

    def test_present_never_spins_faster_than_60fps(self):
        r = gl_ambient.Renderer.__new__(gl_ambient.Renderer)
        r.vsync, r.dpy, r.win = True, None, None              # a swap that returns at once (window not on screen)
        r.x = mock.Mock()
        r.draw = lambda now: None
        t0 = time.monotonic()
        for _ in range(6):
            r.present()
        self.assertGreaterEqual(time.monotonic() - t0, 5 / 60)


class LiveRendererTest(unittest.TestCase):
    def test_renderer_maps_a_child_window_and_quits(self):
        win = Gtk.Window()
        win.set_default_size(320, 240)
        win.show_all()
        while Gtk.events_pending():
            Gtk.main_iteration()
        xid = win.get_window().get_xid()
        link = gl_ambient.GLLink(xid, 320, 240)
        try:
            for _ in range(100):
                if link.ready or not link.alive():
                    break
                time.sleep(0.05)
            self.assertTrue(link.ready, link.error)
            link.send(("holes", [(0, 0, 50, 50)]))
            link.frame(frame(time.monotonic()))
            link.show(True)
            mapped = False
            for _ in range(60):
                out = subprocess.run(["xwininfo", "-children", "-id", str(xid)], capture_output=True, text=True).stdout
                kids = [ln.split()[0] for ln in out.splitlines() if ln.strip().startswith("0x")]
                for k in kids:
                    info = subprocess.run(["xwininfo", "-id", k], capture_output=True, text=True).stdout
                    if "IsViewable" in info:
                        mapped = True
                if mapped:
                    break
                time.sleep(0.05)
            self.assertTrue(mapped, "the GPU window never showed")
            self.assertTrue(link.alive())
        finally:
            link.stop()
            link.proc.wait(timeout=5)
            win.destroy()
        self.assertIsNotNone(link.proc.poll())

    def test_bad_parent_reports_failure(self):
        link = gl_ambient.GLLink(0x3FFFFFF, 320, 240)            # no such window
        try:
            for _ in range(100):
                if not link.alive():
                    break
                time.sleep(0.05)
            self.assertFalse(link.alive())
        finally:
            link.stop()
            link.proc.wait(timeout=5)


if __name__ == "__main__":
    unittest.main()
