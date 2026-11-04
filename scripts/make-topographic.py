#!/usr/bin/env python3
"""Render the original Topographic wallpaper plates. Build-time only: numpy, scipy and Pillow."""
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageFont


WIDTH, HEIGHT = 1920, 1080
OUT = Path(__file__).resolve().parents[1] / 'tvapp' / 'assets' / 'topographic'
Y, X = np.mgrid[0:HEIGHT, 0:WIDTH].astype(np.float32) / HEIGHT


def grain_field(rng, scale):
    """A coarse random field, interpolated isotropically in image space."""
    cols = max(3, round(WIDTH / HEIGHT * scale))
    samples = rng.normal(0, 0.65, (scale, cols)).astype(np.float32)
    return np.asarray(Image.fromarray(samples).resize((WIDTH, HEIGHT), Image.Resampling.BICUBIC))


def color(value):
    return np.frombuffer(bytes.fromhex(value), dtype=np.uint8).astype(np.float32)


def paint_level(z, levels):
    colors = [color(ink) for _, ink in levels]
    positions = [n for n, _ in levels]
    return np.stack([np.interp(z, positions, [ink[i] for ink in colors]) for i in range(3)], axis=2)


def mountain(x, y, angle, length, width, power=1.5):
    u = np.cos(angle) * x + np.sin(angle) * y
    v = -np.sin(angle) * x + np.cos(angle) * y
    return np.exp(-(np.abs(u / length) ** power + np.abs(v / width) ** power))


def caldera(u, v, broad, ridge):
    x, y = u - 1.12, v - 0.54
    theta = np.arctan2(y, x)
    r = np.sqrt((x * .96 + .16 * y) ** 2 + (y * 1.16) ** 2)
    rim = .30 + .045 * np.sin(theta * 3 + .5) + .023 * np.cos(theta * 7)
    volcanic_slopes = 1.07 * np.exp(-np.abs((r - rim) / .165) ** 1.28)
    crater = -.52 * np.exp(-(r / .20) ** 2)
    flank = .54 * mountain(u - .12, v - .91, .9, .40, .17)
    return volcanic_slopes + crater + flank + .15 * broad + ridge * .18


def sound(u, v, broad, ridge):
    # The channel widens to the left. Small branching inlets drain the uplands.
    middle = .48 + .10 * np.sin(u * 5.3) + .10 * np.cos(2.3 * u)
    main = np.abs(v - middle) - (.026 + .12 * (1 - u / 1.9) ** 2)
    north = np.abs(u - .47 - .68 * v - .04 * np.sin(v * 13)) - .029
    east = np.abs(u - 1.27 + .5 * v + .04 * np.cos(v * 14)) - .022
    south = np.abs(u - .70 - .32 * v + .04 * np.cos(v * 17)) - .023
    shore = np.minimum.reduce([main, np.maximum(north, v - .52),
                               np.maximum(east, v - middle), np.maximum(south, middle - v)])
    island = mountain(u - .205, v - .665, -.35, .068, .027)
    return (np.tanh(shore * 5.7) + island * .65 + .13 * broad
            + ridge * np.clip(shore * 4, .10, .88))


def canyon(u, v, broad, ridge):
    # A ribbon canyon with tight goosenecks. Distance to its meandering spine
    # determines the cliff bands. Work on a small distance map, then interpolate.
    from scipy.spatial import cKDTree
    t = np.linspace(-.5, 2.3, 3200)
    spine = np.c_[t + .147 * np.sin(t * 17 + .4),
                  .51 + .20 * np.sin(t * 9.0 - .8) + .11 * np.sin(t * 3.4)]
    distance, _ = cKDTree(spine).query(np.stack((u[::3, ::3], v[::3, ::3]), axis=-1))
    d = np.asarray(Image.fromarray(distance.astype(np.float32)).resize((WIDTH, HEIGHT), Image.Resampling.BICUBIC))
    return np.tanh((d - .078) * 7) + .19 * broad + ridge * np.clip(d, .045, .36)


def folded(u, v, broad, ridge):
    # Long oblique thrust ridges, with spurs and cirques on both flanks.
    z = .18 * broad + ridge * .65
    for cx, cy, length, width, amp, angle in (
        (1.11,.42,.65,.073,.96,-.57), (.52,.79,.60,.080,.72,-.61),
        (1.49,.74,.47,.082,.95,-.78), (.31,.23,.46,.07,.46,-.55),
        (1.42,.22,.27,.037,.53,.62), (.78,.64,.26,.044,.57,.64),
        (1.16,.45,.25,.042,.70,.64), (.97,.54,.20,.044,.53,.67)
    ):
        z += amp * mountain(u - cx, v - cy, angle, length, width, 1.15)
    return z


# Elevations are normalized art coordinates. Index lines are labelled in nominal metres.
PLATES = [
    ('ember-caldera', caldera, 507, [(-.55,'10181f'),(-.16,'243543'),(.05,'34404a'),
      (.35,'5b4c4f'),(.72,'965f50'),(1.02,'c78363'),(1.35,'d4a880')], '241f28', 'ffce92', .074),
    ('glacier-sound', sound, 172, [(-.70,'102c39'),(-.32,'184957'),(-.02,'48838c'),
      (.015,'879b8f'),(.18,'97a399'),(.52,'788d86'),(.78,'96aba7'),(1.08,'ccd4c8'),
      (1.35,'dedbd1')], '203e45', 'bed7c4', .077),
    ('ochre-narrows', canyon, 331, [(-.60,'293f46'),(-.49,'40605f'),(-.40,'9b876e'),
      (-.18,'985d48'),(.10,'ab7452'),(.49,'c18d60'),(.78,'d1ab79'),(1.14,'cfb28a')],
      '533e35', 'ffe3a8', .077),
    ('folded-range', folded, 825, [(-.16,'1c3436'),(.05,'28443d'),(.33,'45604e'),
      (.58,'657a5f'),(.80,'8a9473'),(1.06,'a8a68b'),(1.37,'cdc7ac'),(1.65,'d9d8c7')],
      '192e2c', 'e0d6a8', .072)
]


def render(slug, terrain, seed, levels, ink, highlight, interval):
    rng = np.random.default_rng(seed)
    broad = grain_field(rng, 5) * .70 + grain_field(rng, 11) * .30
    # Smooth domain warping breaks regular contours while preserving their continuity.
    u = X + grain_field(rng, 5) * .036 + grain_field(rng, 15) * .014 + grain_field(rng, 43) * .004
    v = Y + grain_field(rng, 5) * .040 + grain_field(rng, 14) * .016 + grain_field(rng, 37) * .005
    ridge = np.zeros_like(X)
    for scale, amp in ((9,.34), (19,.18), (38,.085), (81,.037), (166,.013)):
        ridge += (0.55 - np.abs(grain_field(rng, scale))) * amp
    z = terrain(u, v, broad, ridge)
    gy, gx = np.gradient(z)
    slope = np.maximum(np.hypot(gx, gy), .00001)
    surface = paint_level(z, levels)
    # A northwest raking light gives the inked slopes a cut-paper relief.
    light = np.clip((gx + gy * .73) * 105, -.35, .38)
    surface *= 1 + light[:, :, None]
    count = np.rint(z / interval)
    distance = np.abs(z - count * interval) / slope
    index = np.remainder(count, 5) == 0
    strength = np.clip(np.where(index, 1.65, 1.02) - distance, 0, 1)
    strength *= np.where(index, .75, .46)
    # A pale engraved line reads better than dark ink through the deeper water.
    under = (z < -.02) if slug == 'glacier-sound' else (z < -.13) if slug == 'ember-caldera' else False
    contour_color = np.where(np.asarray(under)[..., None], color(highlight), color(ink))
    surface += (contour_color - surface) * strength[:, :, None]
    surface *= (1 - .08 * ((X - .89) ** 2 + (Y - .53) ** 2))[:, :, None]
    surface += rng.normal(0, 1.8, z.shape)[:, :, None]
    image = Image.fromarray(np.clip(surface, 0, 255).astype(np.uint8))
    # Survey registration ticks, faint enough to disappear behind the home controls.
    ticks = ImageDraw.Draw(image, 'RGBA')
    for x in range(49, WIDTH, 180):
        for y in range(48, HEIGHT, 180):
            ticks.line((x-4, y, x+4, y), fill=(243,233,205,79))
            ticks.line((x, y-4, x, y+4), fill=(243,233,205,79))
    # Break a few index contours for inline elevation labels, aligned to the local slope.
    font = ImageFont.truetype('DejaVuSans.ttf', 13)
    for y in range(120, HEIGHT - 90, 280):
        for x in range(130, WIDTH - 100, 400):
            window = np.s_[y-65:y+65, x-85:x+85]
            rows, cols = np.nonzero(index[window] & (distance[window] < .4)
                                    & (slope[window] < .011) & (count[window] != 0))
            if not len(rows):
                continue
            j = rng.integers(len(rows))
            cx, cy = x-85+cols[j], y-65+rows[j]
            angle = np.degrees(np.arctan2(gx[cy, cx], gy[cy, cx]))
            angle = (angle + 90) % 180 - 90
            value = str(int(count[cy, cx]) * 25)
            label = Image.new('RGBA', (round(font.getlength(value)) + 8, 20))
            text = ImageDraw.Draw(label)
            fill = tuple(int(c) for c in np.median(surface[cy-4:cy+5, cx-4:cx+5], axis=(0,1)))
            text.rounded_rectangle((0, 0, label.width-1, 19), radius=5, fill=(*fill, 228))
            text.text((label.width/2, 9), value, font=font, anchor='mm',
                      fill=(243,233,205,200) if sum(fill)<320 else (42,47,41,207))
            label = label.rotate(angle, Image.Resampling.BICUBIC, expand=True)
            image.paste(label, (cx-label.width//2, cy-label.height//2), label)
    return image


if __name__ == '__main__':
    OUT.mkdir(parents=True, exist_ok=True)
    for plate in PLATES:
        render(*plate).save(OUT / (plate[0] + '.jpg'), quality=88, optimize=True, progressive=True)
