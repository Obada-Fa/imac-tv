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

## Current state (2026-11-04)
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
- **Twitch (ad-free):** Apps-row tile + `TwitchScreen` rail (top live streams) and a phone Search-tab block; `twitch.py` lists via public GQL and launches streamlink→mpv through `Controller.play_twitch`. Not verified on the real TV (needs streamlink; real Twitch ad filtering untested).
- **Brave as a TV app:** `tvapp/browser.py` launches a natively installed Brave in kiosk mode on its own profile (`~/.local/share/imactv/brave-tv`; logins and extensions need setting up there once) with a local debugging port. `widgets/browser_menu.py` is the TV menu. The phone's Remote tab switches to a browser layout while it runs: address bar, page buttons, a full touchpad with a one-finger scroll edge, and a Browser sheet (tabs, zoom, Shields, Menu on TV). Not verified on the real TV (Mutter stacking, CPU).
- **YouTube behind Home:** the window is minimized so the video stops rendering; audio is auto-resumed if minimizing paused it.
- **YouTube volume is the system volume:** `tvapp/youtube_volume.py` keeps the video element at full level over a local debugging port (9334). A lasting DevTools connection (`Link`) wraps VacuumTube's command hook: volume and mute from the phone's YouTube app set the system volume and show the TV island, and the TV's own volume and mute go back into VacuumTube, so the phone shows the same level. Checked in headless Chrome against a stand-in page; not verified in real VacuumTube or with the real phone app.
- **Volume on the TV:** the server writes `volume_event.json`; Home watches it and shows `widgets/volume_hud.py`: a frosted island on the top screen edge over Home and Settings, a hairline under the clock in ambient. Over other apps `island.IslandOverApps` draws each island frame off screen and hands it to the app in front (`tvapp/overlays.py`): YouTube (VacuumTube, port 9334) and the TV browser (Brave, port 9333) show it in their own page over DevTools, frosted live by the page's `backdrop-filter`; mpv (movies, series, Twitch) lays it over its video with `overlay-add` (solid glass); any other app, or one that doesn't answer, gets solid glass in its own `IslandWindow` (Zorin's GNOME paints a separate window's see-through part black). No picture of the app is copied while the island shows, so the old stale-bar glitch over video is gone. In-page island checked in headless Chrome; mpv overlay only against a stand-in mpv socket. Not verified on the real TV.
- **Notices are islands:** `shell.toast()` shows `island.NoticeIsland` at the top edge (bead drops out, widens to fit the text, one light sweep, folds back), with icons per kind (phone, play, warning, done/update, info). It shows over apps too, steps aside while the volume island shows, and replaces the old bottom `Toast` and the server's `notify-send` update banner. Not verified on the real TV.
- **Not yet verified on the real TV:**
  - VacuumTube's MPRIS controls
  - the YouTube volume lock, including on the Flatpak build
  - CPU saved by minimizing
  - the visualizer with real music
  - mpv OSD prompts and OK routing
  - TheIntroDB coverage
  - live phone connect/disconnect for the QR drop-down
- **All apps (Home tile, phone grid button):** `apps.others_apps()` lists the PC's utilities first (`apps.UTILITIES`: browser, Files, Disks, Disk Usage, System Monitor, Terminal, PC Settings, Software, Remote Desktop, then Calculator, with friendly names), then every other installed `.desktop` app. Menu on any card on the TV, or the star on the phone, pins it to Home (max 8). Home's apps row is the irregular scattered strip again (not left-aligned).
- **Open decision:** which Quick, Draw! animals to keep (suggested: whale, swan, cat, rabbit, horse, giraffe, snail). Animals are off by default.
