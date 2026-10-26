"""Image-locked motion, extraction and photo lifecycle; no downloaded fixtures."""
import math
from pathlib import Path
import random
import sys
import tempfile
import time
import unittest
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from widgets import contours, shapes

PATHS = [[(0.1, 0.95), (0.4, 0.6), (0.4, 0.02)],
         [(0.5, 0.95), (0.5, 0.6), (0.5, 0.02)],
         [(0.9, 0.95), (0.6, 0.6), (0.6, 0.02)]]


def make_viz(paths=PATHS):
    base = shapes.wave_shape()
    base['pts'] = [(x * 1920, y * 1080) for x, y in base['pts']]
    return shapes.Viz(base, 1920, 1080, random.Random(3), paths=paths)


def segment_distance(p, a, b):
    dx, dy = b[0] - a[0], b[1] - a[1]
    u = max(0, min(1, ((p[0] - a[0]) * dx + (p[1] - a[1]) * dy) / (dx * dx + dy * dy)))
    return math.hypot(p[0] - a[0] - u * dx, p[1] - a[1] - u * dy)


class DetectorTests(unittest.TestCase):
    def test_flat_gradient_and_isolated_stars_have_no_tracks(self):
        self.assertEqual(contours.trace([[(40, 40, 40)] * 120 for _ in range(80)]), [])
        self.assertEqual(contours.trace([[(x, x, x) for x in range(120)] for _ in range(80)]), [])
        rgb = [[(0, 0, 0)] * 120 for _ in range(80)]
        for x in range(10, 120, 15):
            for y in range(10, 80, 15):
                rgb[y][x] = (255, 255, 255)
        self.assertEqual(contours.trace(rgb), [])

    def test_noise_and_paper_grain_do_not_become_tracks(self):
        rng = random.Random(9)
        for lo, hi in ((0, 256), (85, 105)):
            rgb = [[tuple(rng.randrange(lo, hi) for _ in range(3)) for _ in range(160)] for _ in range(90)]
            self.assertEqual(contours.trace(rgb), [])

    def test_colour_edges_follow_the_wall_floor_bend(self):
        w, h = 160, 90
        rgb = []
        # Red and green have nearly equal luminance: a grayscale detector misses them.
        for y in range(h):
            spread = 1 + max(0, y - 52) / 35
            rgb.append([(200, 0, 0) if int(((x - 80) / spread + 80) // 12) % 2 else (0, 102, 0)
                        for x in range(w)])
        paths = contours.trace(rgb)
        self.assertGreaterEqual(len(paths), 4)
        bends = [p for p in paths if min(y for _, y in p) < 0.2 and max(y for _, y in p) > 0.8]
        self.assertGreaterEqual(len(bends), 3)
        self.assertTrue(any(len(p) >= 3 for p in bends))
        self.assertTrue(all(0 <= x <= 1 and 0 <= y <= 1 for p in paths for x, y in p))

    def test_curves_are_preserved_and_geometry_is_capped(self):
        rgb = [[((220, 100, 20) if int(math.hypot(x - 80, y - 70) // 12) % 2 else (10, 40, 50))
                for x in range(160)] for y in range(90)]
        paths = contours.trace(rgb)
        self.assertTrue(paths)
        self.assertLessEqual(len(paths), contours.MAX_PATHS)
        self.assertTrue(all(len(p) <= contours.MAX_POINTS for p in paths))
        self.assertTrue(any(len(p) >= 6 for p in paths))
        for path in paths:
            for x, y in path:
                radius = math.hypot(x * 159 - 80, y * 89 - 70)
                self.assertLess(abs(radius - round(radius / 12) * 12), 2.5)

    def test_invalid_provider_geometry_fails_closed(self):
        for data in (None, {}, [[(float('nan'), 0), (1, 1)]], [[(-1, 0), (1, 1)]],
                     [[(0, 0)]], PATHS * 9, [[(0, 0)] * 97]):
            self.assertEqual(shapes.valid_paths(data), [])
        self.assertEqual(shapes.valid_paths([[(0, 0), (0, 0)]]), [])


class CircuitMotionTests(unittest.TestCase):
    def test_dots_remain_on_contours_through_beats_and_reflections(self):
        mode = shapes.CircuitMode(64, 1920, 1080, random.Random(4), PATHS)
        for f in range(1200):
            if f % 7 == 0:
                mode.beat()
            mode.step(1 / 15, dict(bass=1, mid=1, bass_hit=float(f % 7 == 0)))
            pts = mode.points(f / 15, {})
            self.assertEqual(len(pts), 64)
            for train, route in enumerate(mode.route):
                path = mode.routes[route][0]
                for p in pts[train * 8:train * 8 + 8]:
                    self.assertLess(min(segment_distance(p, a, b) for a, b in zip(path, path[1:])), 1e-7)
            self.assertTrue(all(0 <= d < 2 * mode.routes[r][1][-1] for d, r in zip(mode.travel, mode.route)))
        self.assertEqual(len(mode.edges), 56)

    def test_bass_accelerates_instead_of_pushing_off_the_track(self):
        quiet = shapes.CircuitMode(64, 1920, 1080, random.Random(4), PATHS)
        loud = shapes.CircuitMode(64, 1920, 1080, random.Random(4), PATHS)
        before = quiet.travel[0]
        quiet.step(0.05, {})
        loud.beat()
        loud.step(0.05, dict(bass=1, mid=1, bass_hit=1))
        self.assertGreater(loud.travel[0] - before, (quiet.travel[0] - before) * 5)
        # Reading geometry is pure: no duplicate advance when the painter reads twice.
        self.assertEqual(loud.points(1, {}), loud.points(1, {'bass_hit': 1}))

    def test_no_tracks_means_no_circuit_and_aurora_is_retired(self):
        v = make_viz([])
        self.assertNotIn('circuit', v.names)
        self.assertNotIn('aurora', v.names)
        self.assertIn('prism', v.names)
        v.set_paths(PATHS)
        self.assertIn('circuit', v.names)
        v.switch('circuit')
        for _ in range(45):
            v.step(1 / 15, {}, auto=False)
        v.set_paths([])
        self.assertEqual(v.mode, 'edgeflow')
        self.assertNotIn('circuit', v.names)
        for _ in range(45):
            v.step(1 / 15, {}, auto=False)
        self.assertIsNone(v.prev)
        self.assertIsNone(v.frame(4, {})['heads'])

    def test_new_photo_rebuilds_routes_and_morphs_without_jumping(self):
        v = make_viz()
        v.switch('circuit')
        for _ in range(45):
            v.step(1 / 15, {}, auto=False)
        before = v.frame(3, {})['pts']
        other = [[(0.1, 0.1), (0.9, 0.1)]]
        v.set_paths(other)
        self.assertEqual(v.frame(3, {})['pts'], before)
        for _ in range(45):
            v.step(1 / 15, {}, auto=False)
        self.assertTrue(all(abs(y - 108) < 1e-7 for x, y in v.frame(6, {})['pts']))

    def test_tiny_provider_segment_does_not_crash(self):
        v = make_viz([[(0.5, 0.5), (0.500000001, 0.5)]])
        v.switch('circuit')
        v.step(1 / 15, {}, auto=False)
        self.assertEqual(len(v.frame(0, {})['pts']), 64)

    def test_frame_cost_is_bounded_with_maximum_provider_geometry(self):
        paths = [[(x / 95, 0.5 + 0.3 * math.sin(x * 0.1 + r)) for x in range(96)] for r in range(24)]
        v = make_viz(paths)
        start = time.perf_counter()
        v.switch('circuit')
        self.assertLess(time.perf_counter() - start, 0.02)
        start = time.perf_counter()
        for f in range(300):
            v.step(1 / 15, {'bass': 0.5, 'mid': 0.3}, beat=f % 8 == 0, auto=False)
            v.frame(f / 15, {})
        self.assertLess((time.perf_counter() - start) / 300, 0.003)


class CacheTests(unittest.TestCase):
    def test_photo_swap_forwards_new_paths_and_palette(self):
        import homescreen
        from types import SimpleNamespace
        item = dict(reactive_paths=PATHS, palette=[(1, 0.8, 0.4)])
        viz = mock.Mock()
        screen = SimpleNamespace(cfg={"visual_effects": True}, pending=(item, (object(), object())), viz=viz,
                                 canvas=mock.Mock(cur=object()), interval=90,
                                 update_bottom_label=mock.Mock(), base_shape=mock.Mock(return_value={'pts': []}),
                                 start_anim=mock.Mock(), preload=mock.Mock())
        homescreen.AmbientHomeScreen.swap_photo(screen)
        viz.set_paths.assert_called_once_with(PATHS)
        self.assertEqual(viz.palette, item['palette'])

    def test_cached_detection_keys_include_crop_and_file_identity(self):
        import homescreen
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            source = root / 'photo.png'
            source.write_bytes(b'first')
            with mock.patch.object(homescreen.screens, 'DATA', root), \
                 mock.patch.object(homescreen, 'cover_pixbuf', return_value=object()), \
                 mock.patch.object(homescreen, 'paths_from_pixbuf', return_value=PATHS) as detect:
                self.assertEqual(homescreen.traced_paths(source, 1920, 1080), PATHS)
                self.assertEqual(homescreen.traced_paths(source, 1920, 1080), PATHS)
                self.assertEqual(detect.call_count, 1)
                homescreen.traced_paths(source, 3440, 1440)
                self.assertEqual(detect.call_count, 2)
                source.write_bytes(b'changed file')
                homescreen.traced_paths(source, 1920, 1080)
                self.assertEqual(detect.call_count, 3)

    def test_detection_runs_in_a_child_and_matches_in_process_trace(self):
        """The tracer must not run on the app's GIL (it froze the animation), yet give the same tracks."""
        import homescreen
        from gi.repository import GLib, GdkPixbuf
        w, h = contours.TRACE_WIDTH, 216
        rgb = [[(200, 0, 0) if int(((x - w / 2) / (1 + max(0, y - 120) / 80) + w / 2) // 30) % 2 else (0, 102, 0)
                for x in range(w)] for y in range(h)]
        pb = GdkPixbuf.Pixbuf.new_from_bytes(GLib.Bytes.new(bytes(c for row in rgb for p in row for c in p)),
                                             GdkPixbuf.Colorspace.RGB, False, 8, w, h, w * 3)
        with mock.patch.object(homescreen.subprocess, 'run', wraps=homescreen.subprocess.run) as run:
            paths = homescreen.paths_from_pixbuf(pb)
        self.assertEqual(run.call_count, 1)
        self.assertGreaterEqual(len(paths), 3)
        self.assertEqual(paths, contours.valid_paths([[list(q) for q in p] for p in contours.trace(rgb)]))


if __name__ == '__main__':
    unittest.main()
