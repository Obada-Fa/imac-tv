# Retro backgrounds + Circuit: Claude handoff

## Prompt to give Claude

Build the Retro background system and its settings on top of the existing Circuit
visualizer in this checkout. Read this document and the repository instructions
first. Keep Prism. Aurora has been removed from automatic rotation.

Add a persisted Retro background choice to the TV settings and phone settings,
with a way to return to the existing photo sources. Use the user's retro images
as the initial reference material: perspective stripes turning from floor to
wall, neon landscapes, and scalloped/arched patterns. Make the background library
extensible to arbitrary imported images and higher-resolution replacements. Preserve
the original-resolution backgrounds; never use the small analysis copies or GIF
previews as production wallpapers. No image-specific coordinates or filenames.
Reuse existing settings validation,
persistence, photo loading, transitions, and controls.

Circuit already detects image contours, caches them, and runs music-reactive
particles along them. Connect the new background provider to that implementation;
do not duplicate it or hardcode paths for specific filenames. Preserve the
`network` rendering style because it hosts the visualizer; Retro should be a
background choice, not a replacement widget renderer. Keep the CPU budget and
64-point renderer. Validate source switching, crop alignment, import failures,
quiet/music behavior, and settings round trips. Show the result and commit/push.

## What already exists

- `tvapp/widgets/contours.py`: pure-Python RGB edge detection, run on a 384-pixel-wide
  copy of the final cover crop. It sees colour boundaries, straight edges, curves,
  and bends; it suppresses small specks, grain, and dense noise. It is **not** a 3D
  scene or semantic wall detector. Low-contrast/busy pictures can have no usable
  paths, and some picture contours can be missed.
- `tvapp/homescreen.py:traced_paths(path, width, height)`: worker-only cached
  extraction. Key includes algorithm version, full path, file size, nanosecond
  mtime, and display dimensions. Cache holds at most 80 entries in the existing
  app data directory's `pathcache/`.
- `tvapp/widgets/shapes.py:CircuitMode`: eight luminous runners, each with eight
  dots trailing along its contour. Bass/beat hits accelerate travel; mids control
  pace; treble uses the existing sparkle mechanism. Endpoints reflect continuously.
  Colours follow the background. Runners move along 2D polylines; they don't leave
  the image edges on bass hits. No pixel analysis in animation or painting.
- `Viz(..., paths=...)` and `Viz.set_paths(...)`: make Circuit eligible when tracks
  exist, replace routes on a photo change, and fall back to edge flow when tracks
  disappear. Standard random weights are edgeflow 6, circuit 6 (when eligible),
  ridge/constellation/terrain/prism 2 each. The usual initial ridge and 2.8-second
  mode morph are preserved. Aurora's implementation remains solely for the earlier
  archived preview; it is absent from automatic rotation.
- Home already extracts paths in `preload`, carries them through `pending`, updates
  the paths and palette in `swap_photo`, and supplies them in `start_viz`.

## Background-provider contract

The existing `library.next()` boundary returns an item such as:

```python
{
    "path": "/absolute/path/to/decodable-image.png",
    "caption": "Retro geometry",
    "credit": "User-provided image",
    "source": "retro",
}
```

Omit `reactive_paths` for automatic analysis. The worker processes the same
`cover_pixbuf(path, screen_width, screen_height)` crop that the canvas paints.
It sets `item['reactive_paths']` to the detected paths before the photo is shown.
Analysis currently runs for any network-style background with the visualizer on.
To restrict this to Retro, have non-Retro providers explicitly return
`reactive_paths: []` (and preserve that key through their item adapters).

Optional provider-supplied geometry:

```python
item["reactive_paths"] = [
    [(0.12, 0.98), (0.42, 0.58), (0.42, 0.02)],
    # More contours, each a polyline ...
]
```

This is an API example, **not** coordinates to use for the reference artwork.
At most 24 paths and 96 vertices per path; each coordinate must be finite and in
[0, 1]. Coordinates refer to the **final displayed cover crop**, not the source
image. Invalid data fails closed to `[]`; adjacent duplicates are removed. Explicit
`[]` disables extraction and Circuit for that item. If you import precomputed
paths in original-image space, transform them for the crop before supplying them.
Do not save cropped coordinates without also associating them with the crop.

`ambient.Library.items()` currently returns only path/caption/credit/source; it
would need to preserve optional metadata if your new index uses it. You can also
supply an alternate provider implementing the same `next()` boundary.

Image decoding uses GdkPixbuf. The supplied AVIF was converted to PNG for this
session's preview because the installed Pillow lacked AVIF support; the production
import flow should check actual decoder support and convert unsupported input
once at import, or show a useful import error. Do not run ffmpeg per animation
frame or add an unconditional runtime OpenCV/Pillow dependency.

## Verify and preview

```bash
xvfb-run -a python3 -m unittest discover -s tvapp/tests
python3 scripts/circuit-preview.py /path/to/background.jpg tvapp/previews/my-circuit
BENCH_IMAGE=/path/to/background.jpg scripts/viz-bench.sh tvapp 30 circuit prism
```

The preview writes an animated GIF, a still, and a `-paths.png` diagnostic using the
production painter and synthetic 120 BPM audio. The diagnostic shows extracted
routes only; the actual effect lights short moving segments. Preview rendering
requires Pillow in addition to the app's Cairo/GTK dependencies. The app does not.

The benchmark isolates app data and Xvfb, fixes the local background, and waits for
the requested mode. Circuit without a usable source image should not be reported
as a successful benchmark. Do not compare against the old, invalid mode-labelled
ridge baseline. Results are software-rendering measurements on this machine,
not a guarantee for the target TV.

This change intentionally does not implement Retro settings or install background
assets. The three `tvapp/previews/circuit-*.gif` files show the effect on the user's
reference images. Unit tests generate their own images and do not depend on files
in Downloads. Preserve the user's unrelated untracked `CLAUDE.md`.

## Implemented (Retro background library)

- `tvapp/backgrounds.py`: `Backgrounds(DATA)` keeps originals untouched in `DATA/retro/` (+ `index.json`, `thumbs/`),
  adopts files dropped into the folder, rejects undecodable/animated/tiny/huge files with readable errors, replaces a
  same-named picture in place when the new one has more pixels (or via explicit `replace=<id>`), and provides the
  `next()` provider boundary. Items omit `reactive_paths`; `homescreen.preload` runs contour analysis for
  `source == "retro"` only (photo providers get `[]`).
- home.json keys `background` ("photos" | "retro") and `retro_pick` ("shuffle" | id), validated in `widgets.parse_config`,
  written with `widgets.save_keys`. Home re-reads them (mtime check in `supervise`), drops stale preloads, and switches.
  An empty Retro library falls back to photos.
- Phone: Settings > Background (source, pick, thumbnails, add, remove); `POST /api/retro/upload?name=` takes raw bytes;
  actions `background_set`, `retro_remove`. TV: Settings > Background.
- Tests: `tvapp/tests/test_backgrounds.py`.
