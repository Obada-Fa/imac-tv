#!/usr/bin/env python3
"""Render Aurora and Prism with the production Cairo painter and synthetic music.

Usage: python3 scripts/viz-preview.py [output.gif]
No display, playback, or user settings are touched. Requires Cairo, GTK and Pillow.
"""
import io
import math
from pathlib import Path
import random
import sys
from types import SimpleNamespace

from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tvapp'))
import cairo
import homescreen
from widgets import shapes


def main():
    width, height, fps = 768, 432, 15
    names = ('aurora', 'prism')
    screens = []
    for name in names:
        base = shapes.wave_shape()
        base['pts'] = [(x * width, y * height) for x, y in base['pts']]
        viz = shapes.Viz(base, width, height, random.Random(8), modes=[name])
        viz.switch(name)
        # Preview the settled mode; live Home still uses its normal 2.8-second morph.
        viz.prev, viz.prev_perm, viz.blend = None, None, 1.0
        screen = SimpleNamespace(viz=viz, glow=None, shell=SimpleNamespace(scale=width / 1920),
                                 cfg={'node_shape': 'hexagon'}, previous=None)
        screen.glow_sprite = lambda s=screen: homescreen.AmbientHomeScreen.glow_sprite(s)
        screens.append(screen)
    frames = []
    eased = {}
    for f in range(fps * 6):
        t = f / fps
        kick = math.exp(-((t % 0.5) * 12))
        hat = math.exp(-((t % 0.25) * 25))
        lv = dict(bass=0.25 + 0.6 * kick, mid=0.4 + 0.2 * math.sin(t * 1.4),
                  treble=0.2 + 0.4 * hat, beat=kick, bass_hit=kick,
                  mid_hit=0.3 * hat, treble_hit=hat)
        eased = shapes.ease_levels(eased, lv, 1 / fps)
        lv = eased
        surf = cairo.ImageSurface(cairo.FORMAT_RGB24, width, height * 2)
        cr = cairo.Context(surf)
        for row, (name, screen) in enumerate(zip(names, screens)):
            cr.save()
            cr.translate(0, height * row)
            cr.rectangle(0, 0, width, height)
            cr.clip()
            bg = cairo.LinearGradient(0, 0, width, height)
            bg.add_color_stop_rgb(0, 0.025, 0.055, 0.095)
            bg.add_color_stop_rgb(1, 0.07, 0.025, 0.08)
            cr.set_source(bg)
            cr.paint()
            screen.viz.step(1 / fps, lv, t=t, onset=int(t * 2))
            fr = screen.viz.frame(t, lv)
            tails = ([(x - 4 * (x - px), y - 4 * (y - py))
                      for (x, y), (px, py) in zip(fr['pts'], screen.previous)]
                     if screen.previous else None)
            screen.previous = fr['pts']
            g = dict(scale=width / 1920, t=t, pts=fr['pts'], lv=lv, viz=fr,
                     alphas=fr['alpha'], links=fr['links'], tails=tails)
            homescreen.AmbientHomeScreen.draw_viz(screen, cr, g)
            cr.set_source_rgb(0.85, 0.91, 1)
            cr.select_font_face('Sans', cairo.FONT_SLANT_NORMAL, cairo.FONT_WEIGHT_NORMAL)
            cr.set_font_size(18)
            cr.move_to(30, 40)
            cr.show_text(name.upper())
            cr.set_source_rgb(0.45, 0.55, 0.67)
            cr.set_font_size(11)
            cr.move_to(30, height - 25)
            cr.show_text('PRODUCTION RENDERER  /  SYNTHETIC 120 BPM')
            cr.restore()
        buf = io.BytesIO()
        surf.write_to_png(buf)
        buf.seek(0)
        frames.append(Image.open(buf).convert('RGB').quantize(colors=96))
    target = Path(sys.argv[1] if len(sys.argv) > 1 else 'tvapp/previews/viz-aurora-prism.gif')
    target.parent.mkdir(parents=True, exist_ok=True)
    frames[0].save(target, save_all=True, append_images=frames[1:], duration=1000 // fps, loop=0)
    print(target.resolve())


if __name__ == '__main__':
    main()
