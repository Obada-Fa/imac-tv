# Performance: budgets, baseline and how to measure

Nexus TV has to stay light on old hardware. Numbers from Xvfb are not representative (ambient measured ~5% of a
core there and 30–55% on a real machine), so every change is measured on a real TV.

## Reference machine

The 2010 iMac (iMac11,3): i5-760 (4 cores, no hyper-threading), 16 GB, Radeon HD 5850 with hardware GL,
spinning disk, Linux Mint 21.3 (XFCE, X11), 2560×1440. All figures are percent of one core.

## Budgets on the reference machine

| State | Budget (Home + renderer + Xorg) |
|---|---|
| Home awake, settled | ≤ 3% |
| Home awake, something playing behind | ≤ 5% plus the player |
| Quiet ambient | ≤ 5% |
| Ambient with the music visualizer | ≤ 10% plus the player |

## Baseline, 2026-11-10 (commit 4feef9a)

| State | Home | GPU renderer | Xorg | Notes |
|---|---|---|---|---|
| Watching YouTube | 2 | 0 | 2 | VacuumTube 24 |
| Home awake, settled | 15 | 0 | 16 | xfdesktop 6; right after start 6 + 4 |
| Home awake, playing tile | 64–73 (+15 helpers) | 0 | 44–62 | the worst state |
| Quiet ambient | 15 | 11 | 5 | |
| Ambient + visualizer | 23–34 | 18–21 | 4–5 | VacuumTube 8–12 (sound only) |

The server and its helper processes (pactl, wmctrl, xset…) cost about 1.5% in every state.

## After the 2026-11-10 fixes

Measured on the reference machine with the working tree copied beside the installed app.

| State | Before: Home / renderer / Xorg | After: Home / renderer / Xorg | What changed |
|---|---|---|---|
| Home awake, settled | 5.8 / 0 / 4.4 (fresh start) | 3.5 / 0 / 0.6 | pairing labels are no longer re-laid out on every network check; network check every 20 s instead of 4 |
| Home awake, playing tile | 73 / 0 / 44 | 3.9 / 0 / 0.8 | only the tile's bars move (5 fps); all of Home no longer animates at 20 fps |
| Quiet ambient | 13.3 / 14.0 / 3.8, PulseAudio 2.5 | 7.8 / 6.2 / 2.3, PulseAudio ~0 | particles stepped at 5 Hz, renderer capped at 30 fps, speakers sampled 3 s in 20 |
| Ambient + visualizer (edge-flow, fake levels) | 37.8 / 25.1 / 4.2 | not measured yet | the dirty-cell walk is skipped while the GPU draws (it was 55% of each step in a profile) |

Still over budget and next in line:

- **Visualizer.** Home steps `shapes.Viz` in Python at 30 Hz and the renderer rebuilds every frame's geometry in
  Python at 60 fps. The fix is to move the stepping and the geometry into shaders so Home sleeps in ambient.
- **Quiet ambient.** The same: `shapes.Field.frame` (links between particles) is ~5 ms a step on this machine.
- **The clock.** The drift style redraws once a second (~18 ms a draw at 1440p, about 2% of a core).
- **Home's memory** grew from 309 MB to 454 MB in 35 minutes on the installed build; not investigated.
- **One unexplained minute** of the server at 27% of a core while a phone was connected.

## How to measure

Copy the scripts to the TV and run them there; they read `/proc` and need no root.

    scripts/dev/tv-measure.py 60 "label"     # per-process CPU, helper processes started, memory
    scripts/dev/tv-threads.py                # 20 s of per-thread CPU and syscall rates for server/Home/renderer
    IMACTV_PERF_LOG=/tmp/perf.jsonl python3 tvapp/home.py     # per-frame log; python3 tvapp/perflog.py /tmp/perf.jsonl
    python3 scripts/dev/trace-home.py tvapp  # counts every redraw-causing GTK call by caller -> /tmp/nexus-trace.txt

Silent test hooks: `IMACTV_FAKE_NOWPLAYING=@file.json` (a playing tile without sound), `IMACTV_FAKE_LEVELS=1`
(the visualizer without sound). Never test on someone's real display with sound.

`scripts/dev/ssh-via-iface.py` is an ssh ProxyCommand that connects through one network interface, for a TV that
is only reachable over Wi-Fi from a PC that also has a wired connection.

## Lessons

- `Gtk.Label.set_text` always asks for a new layout, even with the same text. A label refreshed by a poller
  repaints its whole container: compare first.
- An animation that never ends ("while something is playing") defeats every idle saving. Give the moving part its
  own small region and timer.
