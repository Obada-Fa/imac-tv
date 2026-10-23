#!/usr/bin/env python3
"""Preview detected paths and Circuit over any local image, using the live painter.

python3 scripts/circuit-preview.py IMAGE OUTPUT_PREFIX [--seconds 8]
GIF + still + path diagnostic. AVIF decoding depends on the installed pixbuf loader;
convert to PNG first if needed. Analysis/rendering never touch user settings.
"""
import argparse
import io
import math
from pathlib import Path
import random
import sys
from types import SimpleNamespace

from PIL import Image
import cairo

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tvapp'))
import homescreen
from widgets import shapes


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('image')
    ap.add_argument('output')
    ap.add_argument('--seconds', type=int, default=8)
    args = ap.parse_args()
    w, h, fps = 768, 432, 15
    pb = homescreen.cover_pixbuf(args.image, w, h)
    paths = homescreen.paths_from_pixbuf(pb)
    if not paths:
        raise SystemExit('No reliable contours: this image would use the normal visualizers.')
    out = Path(args.output)
    out.parent.mkdir(parents=True, exist_ok=True)
    bg, small = homescreen.load_surface(args.image, w, h)
    base = shapes.wave_shape()
    base['pts'] = [(x * w, y * h) for x, y in base['pts']]
    viz = shapes.Viz(base, w, h, random.Random(4), paths=paths,
                     palette=homescreen.surface_palette(small), modes=['circuit'])
    viz.switch('circuit')
    viz.prev, viz.prev_perm, viz.blend = None, None, 1.0
    screen = SimpleNamespace(viz=viz, glow=None, shell=SimpleNamespace(scale=w / 1920),
                             cfg={'node_shape': 'hexagon'})
    screen.glow_sprite = lambda: homescreen.AmbientHomeScreen.glow_sprite(screen)
    frames, eased = [], {}
    for f in range(fps * args.seconds):
        t = f / fps
        kick = math.exp(-(t % 0.5) * 12)
        hat = math.exp(-(t % 0.25) * 25)
        eased = shapes.ease_levels(eased, dict(bass=0.2 + kick * 0.65, mid=0.45 + 0.2 * math.sin(t),
                                  treble=0.15 + 0.4 * hat, beat=kick, bass_hit=kick, treble_hit=hat), 1 / fps)
        viz.step(1 / fps, eased, t=t, onset=int(t * 2))
        fr = viz.frame(t, eased)
        surf = cairo.ImageSurface(cairo.FORMAT_RGB24, w, h)
        cr = cairo.Context(surf)
        cr.set_source_surface(bg)
        cr.paint()
        homescreen.AmbientHomeScreen.draw_viz(screen, cr, dict(scale=w / 1920, t=t, pts=fr['pts'],
            lv=eased, viz=fr, alphas=fr['alpha'], links=fr['links'], tails=None))
        cr.set_source_rgba(1, 1, 1, 0.85)
        cr.select_font_face('Sans')
        cr.set_font_size(15)
        cr.move_to(24, 30)
        cr.show_text('CIRCUIT / IMAGE-LOCKED RUNNERS')
        cr.set_font_size(9)
        cr.move_to(24, h - 18)
        cr.show_text('ACTUAL RENDERER / AUTOMATIC CONTOURS / SYNTHETIC 120 BPM')
        buf = io.BytesIO()
        surf.write_to_png(buf)
        buf.seek(0)
        frames.append(Image.open(buf).convert('RGB'))
        if f == fps * 2:
            surf.write_to_png(str(out.with_suffix('.png')))
    palette = frames[0].quantize(colors=256)
    frames = [frame.quantize(palette=palette, dither=Image.Dither.NONE) for frame in frames]
    frames[0].save(out.with_suffix('.gif'), save_all=True, append_images=frames[1:],
                   duration=round(1000 / fps), loop=0, optimize=True)
    # Diagnostic uses the same cover crop, to make alignment reviewable.
    cr = cairo.Context(bg)
    cr.set_source_rgb(0.1, 1, 0.6)
    cr.set_line_width(1)
    for path in paths:
        cr.move_to(path[0][0] * w, path[0][1] * h)
        for x, y in path[1:]:
            cr.line_to(x * w, y * h)
        cr.stroke()
    bg.write_to_png(str(out.with_name(out.name + '-paths').with_suffix('.png')))
    print(f'{len(paths)} paths, {sum(map(len, paths))} vertices; {out.with_suffix(".gif").resolve()}')


if __name__ == '__main__':
    main()
