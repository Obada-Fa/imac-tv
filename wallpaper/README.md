# Retro Circuit wallpaper

The TV's Retro pictures with the Circuit visualizer: glowing runners travel along each picture's
contours and speed up, pulse and sparkle with whatever music the computer plays.
The pictures change every few minutes with a crossfade, and the runners flow onto the new picture's lines.

## Build

```bash
python3 wallpaper/build.py
```

The build traces the pictures in `tvapp/assets/retro/` with the TV app's own contour code. Pictures with
no usable lines are skipped. Pass extra folders to include more pictures: `python3 wallpaper/build.py ~/Pictures/retro`.
It writes `wallpaper/dist/retro-circuit/` and `wallpaper/dist/retro-circuit.zip`.

## Windows

- **Lively Wallpaper** (free): drag `retro-circuit.zip` into Lively. Settings: right-click the wallpaper, then *Customise*.
- **Wallpaper Engine**: open the editor, choose *Create Wallpaper*, then pick `index.html` from the unzipped folder.
  Settings appear in the wallpaper's properties.

Both apps send the system audio spectrum to the page; nothing else needs to be installed.

Settings: minutes per picture, darken picture, runner brightness, max FPS with music,
keep runners drifting without music, and shuffle.

## Linux (X11)

```bash
python3 wallpaper/linux/retro-wallpaper.py
```

This opens one desktop window per monitor, behind all other windows. Music comes from the speaker
monitor through `parec` or `pw-record`, using the TV app's analyser. Drawing pauses while a maximized
or fullscreen window covers that monitor. Options: `--interval`, `--dim`, `--brightness`, `--fps`,
`--no-idle`, `--no-shuffle`. Add `--install` to start it with your session; `--uninstall` removes it.

Wayland is not supported, because ordinary apps can't place a window on the desktop there. On GNOME
with the desktop icons extension, the icons may end up under the wallpaper window.

## Testing in a browser

Serve the built folder (`python3 -m http.server` inside `dist/retro-circuit`) and open
`index.html?demo=1` for a fake 120 bpm track. Other query options: `interval`, `dim`, `brightness`,
`fps`, `idle=0`, `shuffle=0`, `seed`.
