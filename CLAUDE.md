# iMac TV: notes for Claude sessions

General orchestrator rules live in `~/.claude/CLAUDE.md` ("Orchestrator mode").

## Project facts
- **Runtime:** the GTK3 10-foot TV app (`tvapp/home.py` shell, `tvapp/homescreen.py` ambient Home, `tvapp/widgets/`, `tvapp/screens.py` Browse/Search/Details), the phone web remote (`tvapp/server.py`, `tvapp/web/`), and media and playback (`tvapp/media/`, mpv, stremio-service).
- **Target TV:** a friend's Zorin laptop (Skylake i5 with Intel HD 520, Mesa GL, X11; 8 GB, 4 Docker containers). Claude has no access to it; the user tests there with `cd ~/tvremote && git pull && ./install.sh`.
- **CPU budget:** GTK3/cairo is software-rendered. Stay at about 0–2% of one core when calm and about 10% while animating, with ≤15 fps and dirty-region redraws only. Rasterize into a client-side ImageSurface and upload once per frame (`PhotoCanvas.on_draw` framebuffer): many strokes on the X surface cost the TV's Xorg ~60% of a core. Measure with `scripts/viz-bench.sh tvapp 30` (Xvfb is only a rough stand-in for the TV's Xorg). GL in GTK3 was measured at 40–50% CPU, so don't use it.
- **Tests:** `cd tvapp && xvfb-run -a python3 -m unittest discover -s tests -q`. Never run them with DISPLAY=`:0`.
- **Running the server in tests:** set `IMACTV_DISPLAY` (private Xvfb), `IMACTV_DATA_DIR` (scratch dir), and `IMACTV_MPV_SOCK` for smoke runs.
- **Test-only hooks:** `IMACTV_FAKE_NOWPLAYING`, `IMACTV_FAKE_LEVELS`, `IMACTV_VIZ_MODES`.
- **Settings files:**
  - `~/.local/share/imactv/home.json`: style, node_shape, tile_shape, shapes, visualizer, qr_style, and ambient interval/idle/idle_music.
  - `~/.local/share/imactv/media.json`: autoplay_next, auto_skip_intro, still_watching_*.

## Current state (2026-10-23, HEAD a8c6d16, pushed)
- **Ambient Home:**
  - changing Bing/Wikimedia photo, clock, weather
  - network style (default): hexagon nodes and frosted tiles; bubbles is the alternative style
  - glass QR drop-down when no phone is connected
  - YouTube Now playing node
  - ambient mode after 120 s, or 30 s while YouTube plays: drifting particles and sky-line trace, weather line, and a music visualizer led by the edge-flow style
- **Phone:**
  - tabs drive the TV screens
  - search scoped to the current tab
  - decluttered Remote tab
  - YouTube mini player over MPRIS
- **Playback:** skip intro/recap (file chapters, then TheIntroDB), Up next countdown, "Are you still watching?".
- **YouTube behind Home:** the window is minimized so the video stops rendering; audio is auto-resumed if minimizing paused it.
- **Not yet verified on the real TV:**
  - VacuumTube's MPRIS controls
  - CPU saved by minimizing
  - the visualizer with real music
  - mpv OSD prompts and OK routing
  - TheIntroDB coverage
  - live phone connect/disconnect for the QR drop-down
- **Open decision:** which Quick, Draw! animals to keep (suggested: whale, swan, cat, rabbit, horse, giraffe, snail). Animals are off by default.
