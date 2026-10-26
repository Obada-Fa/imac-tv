"""Shared editable settings contract for the TV and phone. No preference migration."""
import math
import re

CATEGORIES = ('Appearance', 'Ambient', 'Display', 'Playback', 'Connections', 'System')
# key, category, label, kind, choices/range, Network-only
FIELDS = [
    ('transparency', 'Appearance', 'Transparent surfaces', 'bool', [], False),
    ('visual_effects', 'Appearance', 'Visual effects', 'bool', [], False),
    ('style', 'Appearance', 'Home layout', 'choice', ['network', 'bubbles'], False),
    ('widgets', 'Appearance', 'Visible widgets', 'multi', ['clock', 'weather', 'pairing', 'continue', 'apps'], False),
    ('node_shape', 'Appearance', 'Node shape', 'choice', ['hexagon', 'octagon'], True),
    ('tile_shape', 'Appearance', 'Tile shape', 'choice', ['rounded', 'chamfer'], True),
    ('qr_style', 'Appearance', 'Pairing QR style', 'choice', ['glass', 'light'], False),
    ('interval', 'Ambient', 'Background rotation (seconds)', 'number', [10, 3600], False),
    ('idle', 'Ambient', 'Idle delay (seconds)', 'number', [10, 7200], False),
    ('idle_music', 'Ambient', 'Music idle delay (seconds)', 'number', [10, 7200], False),
    ('visualizer', 'Ambient', 'Music visualizer', 'bool', [], True),
    ('shapes', 'Ambient', 'Ambient shapes', 'choice', ['photo', 'photo+animals', 'off'], True),
    ('sub_default', 'Playback', 'Default subtitle language', 'language', [], False),
    ('sub_langs', 'Playback', 'Subtitle language order', 'languages', [], False),
    ('quality_order', 'Playback', 'Video quality order', 'order', ['1080p', '720p', '2160p', '480p'], False),
    ('autoplay_next', 'Playback', 'Autoplay next episode', 'bool', [], False),
    ('auto_skip_intro', 'Playback', 'Automatically skip intros', 'bool', [], False),
    ('still_watching_episodes', 'Playback', 'Still watching after episodes', 'number', [1, 50], False),
    ('still_watching_hours', 'Playback', 'Still watching after hours', 'number', [0.1, 24], False),
]
MEDIA_KEYS = {f[0] for f in FIELDS if f[1] == 'Playback'}
AMBIENT_KEYS = {'interval', 'idle', 'idle_music'}
LABELS = {'photo': 'Photo outlines', 'photo+animals': 'Photo outlines and animals', 'off': 'Off',
          'network': 'Network', 'bubbles': 'Bubbles', 'ara': 'Arabic', 'dut': 'Nederlands', 'eng': 'English'}


def label(value):
    if isinstance(value, bool):
        return 'On' if value else 'Off'
    if isinstance(value, list):
        return ', '.join(label(v) for v in value)
    return LABELS.get(str(value), str(value).capitalize())


def values(home, media):
    return {key: (media[key] if key in MEDIA_KEYS else home['ambient'][key] if key in AMBIENT_KEYS else home[key])
            for key, *_ in FIELDS}


def validate(key, value):
    field = next((f for f in FIELDS if f[0] == key), None)
    if field is None:
        raise ValueError('Unknown setting')
    _, _, _, kind, options, _ = field
    valid = False
    if kind == 'bool':
        valid = isinstance(value, bool)
    elif kind == 'choice':
        valid = value in options
    elif kind == 'number':
        valid = (not isinstance(value, bool) and isinstance(value, (int, float)) and math.isfinite(value)
                 and options[0] <= value <= options[1]
                 and (key == 'still_watching_hours' or int(value) == value))
    elif kind == 'language':
        valid = isinstance(value, str) and re.fullmatch(r'[a-z]{2,3}', value) is not None
    elif kind in ('multi', 'languages', 'order'):
        valid = isinstance(value, list) and all(isinstance(v, str) for v in value)
        if valid:
            valid = len(value) <= 40 and len(value) == len(set(value))
            if kind == 'languages':
                valid = valid and bool(value) and all(re.fullmatch(r'[a-z]{2,3}', v) for v in value)
            else:
                valid = valid and all(v in options for v in value)
                if kind == 'order':
                    valid = valid and set(value) == set(options)
    if not valid:
        raise ValueError('Invalid value for ' + field[2])
    return value
