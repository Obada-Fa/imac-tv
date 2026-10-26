"""Behavior at the ambient music boundary, without constructing a GTK window."""
from pathlib import Path
from types import SimpleNamespace
import sys
import unittest
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

try:
    import homescreen
except Exception:  # no GTK bindings
    homescreen = None


@unittest.skipIf(homescreen is None, "GTK bindings unavailable")
class AmbientMusicTests(unittest.TestCase):
    def test_ambient_canvas_keeps_the_sharp_photo_and_music_only_dims_it(self):
        # 801ffa4 painted the 1/16 blurred copy in ambient mode; on the TV that read as "just a blurry
        # background" whenever nothing played. Ambient keeps the real photo; the visualizer only darkens it.
        cairo = homescreen.cairo

        def solid(w, h, color):
            surf = cairo.ImageSurface(cairo.FORMAT_ARGB32, w, h)
            cr = cairo.Context(surf)
            cr.set_source_rgb(*color)
            cr.paint()
            return surf

        photo = solid(320, 192, (0, 1, 0))
        canvas = SimpleNamespace(ambient=True, cur=photo, blur_cur=solid(20, 12, (1, 0, 0)),
                                 blur_k=16, extra=None, prev=None, fade=1.0, dim=0.0,
                                 get_allocated_width=lambda: 320,
                                 get_allocated_height=lambda: 192)

        def center_rgb():
            out = cairo.ImageSurface(cairo.FORMAT_ARGB32, 320, 192)
            homescreen.PhotoCanvas.on_draw(canvas, None, cairo.Context(out))
            out.flush()
            p = 96 * out.get_stride() + 160 * 4
            b, g, r, _a = out.get_data()[p:p + 4]
            return r, g, b

        still = center_rgb()
        self.assertEqual(still, (0, 255, 0))
        canvas.dim = 1.0
        music = center_rgb()
        self.assertGreater(music[1], 120)
        self.assertLess(music[1], still[1])
        self.assertLess(music[0], 10)
        canvas.ambient, canvas.dim = False, 0.0
        self.assertEqual(center_rgb(), (0, 255, 0))

    def test_titleless_audio_starts_listening_and_can_form_visualizer(self):
        screen = SimpleNamespace(cfg={"visual_effects": True}, style="network", idle=SimpleNamespace(ambient=True),
                                 viz_on=True, chrome=0.0, shaped=False, audio=None,
                                 fake_levels=False, audio_retry_due=0,
                                 active=lambda: True, begin_shapes=mock.Mock(),
                                 start_shape_timer=mock.Mock())
        screen.start_audio = mock.Mock(side_effect=lambda: setattr(screen, "audio", mock.Mock()))
        screen.stop_audio = mock.Mock()
        homescreen.AmbientHomeScreen.sync_shapes(screen, 10)
        screen.start_audio.assert_called_once()
        screen.start_shape_timer.assert_called_once()

        screen.shell = SimpleNamespace(sw=1920, sh=1080)
        screen.now_state = None
        screen.shapes_mode, screen.trace, screen.doodles = "photo", None, []
        screen.base_shape = lambda: homescreen.AmbientHomeScreen.base_shape(screen)
        screen.audio.levels.return_value = {"active": True}
        screen.music_active = lambda: homescreen.AmbientHomeScreen.music_active(screen)
        self.assertTrue(screen.music_active())
        shape = homescreen.AmbientHomeScreen.pick_shape(screen, 11)
        # The dots first gather on the photo's sky-line (a calm wave when it has none); the visualizer
        # modes, the constellation among them, then grow out of that shape.
        self.assertEqual(shape["kind"], "wave")
        self.assertTrue(shape["pts"])

    def test_inactive_home_stops_capture(self):
        screen = SimpleNamespace(active=lambda: False, stop_anim=mock.Mock(),
                                 audio=mock.Mock(), stop_audio=mock.Mock(), shape_id=0)
        self.assertTrue(homescreen.AmbientHomeScreen.supervise(screen))
        screen.stop_anim.assert_called_once()
        screen.stop_audio.assert_called_once()

    def test_wake_stops_capture_and_restores_photo(self):
        audio = mock.Mock()
        screen = SimpleNamespace(idle=mock.Mock(), canvas=mock.Mock(ambient=True),
                                 items=[], audio=audio, shaped=True, dust_amt=1.0,
                                 morph=mock.Mock(), start_shape_timer=mock.Mock(),
                                 start_anim=mock.Mock(), update_bottom_label=mock.Mock())
        screen.idle.touch.return_value = True
        screen.stop_audio = mock.Mock(side_effect=lambda: setattr(screen, "audio", None))
        screen.wake_shapes = lambda: homescreen.AmbientHomeScreen.wake_shapes(screen)
        self.assertTrue(homescreen.AmbientHomeScreen.poke(screen))
        self.assertFalse(screen.canvas.ambient)
        screen.stop_audio.assert_called_once()
        screen.morph.release.assert_called_once()
        screen.update_bottom_label.assert_called_once()

    def test_song_replaces_photo_label_and_stale_title_clears(self):
        screen = SimpleNamespace(idle=SimpleNamespace(ambient=False),
                                 current_item={"caption": "Mountain", "credit": "A", "source": "bing"},
                                 cap=mock.Mock(), credit=mock.Mock())
        screen.now_state = {"status": "Playing", "title": "Song", "artist": "Singer"}
        homescreen.AmbientHomeScreen.update_bottom_label(screen)
        screen.cap.set_text.assert_called_with("Song")
        screen.credit.set_text.assert_called_with("Singer")

        screen.idle.ambient = True       # paused in ambient: the photo's caption comes back (was blank)
        screen.now_state = {"status": "Paused", "title": "Song", "artist": "Singer"}
        homescreen.AmbientHomeScreen.update_bottom_label(screen)
        screen.cap.set_text.assert_called_with("Mountain")
        screen.credit.set_text.assert_called_with("A · Bing")

        screen.idle.ambient = False
        screen.now_state = None
        homescreen.AmbientHomeScreen.update_bottom_label(screen)
        screen.cap.set_text.assert_called_with("Mountain")
        screen.credit.set_text.assert_called_with("A · Bing")

    def test_photo_swap_does_not_reform_music_geometry(self):
        item = {"path": "unused", "caption": "Other photo"}
        target = object()
        screen = SimpleNamespace(cfg={"visual_effects": True}, pending=(item, (object(), object())),
                                 canvas=mock.Mock(cur=object()), now_state=None,
                                 idle=SimpleNamespace(ambient=True), photo_due=0, interval=90,
                                 shaped=True, morph=SimpleNamespace(kind="constellation", target=target), viz=None,
                                 cap=mock.Mock(), credit=mock.Mock(),
                                 start_anim=mock.Mock(), preload=mock.Mock(),
                                 update_bottom_label=mock.Mock())
        homescreen.AmbientHomeScreen.swap_photo(screen)
        self.assertIs(screen.current_item, item)
        self.assertIs(screen.morph.target, target)

    def test_loaded_photo_matches_swap_contract(self):
        path = Path(__file__).resolve().parents[1] / "assets" / "preview-hero.png"
        self.assertEqual(len(homescreen.load_surface(path, 320, 180)), 2)

    def test_visualizer_off_does_not_start_audio(self):
        screen = SimpleNamespace(cfg={"visual_effects": True}, style="network", idle=SimpleNamespace(ambient=True),
                                 viz_on=False, chrome=0.0, shaped=False, audio=None,
                                 fake_levels=False, audio_retry_due=0,
                                 active=lambda: True, begin_shapes=mock.Mock(),
                                 start_audio=mock.Mock(), stop_audio=mock.Mock(),
                                 start_shape_timer=mock.Mock())
        homescreen.AmbientHomeScreen.sync_shapes(screen, 10)
        screen.start_audio.assert_not_called()


if __name__ == "__main__":
    unittest.main()


@unittest.skipIf(homescreen is None, "GTK bindings unavailable")
class QuietFieldTests(unittest.TestCase):
    def make(self, music):
        import random
        from widgets import shapes
        screen = SimpleNamespace(cfg={"visual_effects": True}, style="network", idle=SimpleNamespace(ambient=True), chrome=0.0,
                                 active=lambda: True, shell=SimpleNamespace(sw=1920, sh=1080, scale=1.0),
                                 rng=random.Random(1), field=None, field_geom=None, field_cells=set(),
                                 music=music, invalidate_cells=mock.Mock())
        screen.music_active = lambda: screen.music
        screen.field_target = lambda: homescreen.AmbientHomeScreen.field_target(screen)
        return screen

    def test_field_needs_no_photo_trace_and_returns_within_two_seconds_of_music_stopping(self):
        screen = self.make(True)
        clock = 0.0
        for _ in range(10):                                  # music playing: no particles
            clock += 0.11
            homescreen.AmbientHomeScreen.step_field(screen, 0.11, clock)
        self.assertEqual(screen.field.amt, 0.0)
        self.assertIsNone(screen.field_geom)
        screen.music = False                                 # music stops at clock
        stopped = clock
        while screen.field.amt < 0.95 and clock - stopped < 5:
            clock += 0.11
            homescreen.AmbientHomeScreen.step_field(screen, 0.11, clock)
        self.assertLess(clock - stopped, 2.0)
        self.assertTrue(screen.field_geom["pts"])
        screen.idle.ambient = False                          # waking fades it away again
        for _ in range(15):
            homescreen.AmbientHomeScreen.step_field(screen, 0.11, clock)
        self.assertIsNone(screen.field_geom)


@unittest.skipIf(homescreen is None, "GTK bindings unavailable")
class WidgetMusicReportTests(unittest.TestCase):
    def test_widgets_hear_music_start_and_stop_in_ambient(self):
        widget = mock.Mock(spec=["on_music"])
        plain = SimpleNamespace()
        for style in ("network", "bubbles"):
            screen = SimpleNamespace(style=style, idle=SimpleNamespace(ambient=True), items=[widget, plain],
                                     music=True, active=lambda: False, viz_on=False, audio=None,
                                     chrome=1.0, fake_levels=False)
            screen.music_active = lambda: screen.music
            homescreen.AmbientHomeScreen.sync_shapes(screen, 1.0)
            widget.on_music.assert_called_with(True)
            screen.music = False
            homescreen.AmbientHomeScreen.sync_shapes(screen, 2.0)
            widget.on_music.assert_called_with(False)
        widget.on_music.reset_mock()
        screen.idle.ambient = False
        homescreen.AmbientHomeScreen.sync_shapes(screen, 3.0)
        widget.on_music.assert_not_called()
