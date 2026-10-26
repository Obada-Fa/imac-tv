# iMac TV

The controller is `server.py`, TV Home is `home.py`, media logic is in `media/`, and the phone remote is in `web/`.
No cloud account is needed for the remote. The iMac must be powered on and on the same LAN.

## Install

Copy this directory to `~/tvremote/tvapp` on the iMac, then run
`bash ~/tvremote/tvapp/deploy/install.sh` there. The installer backs up the
existing service and autostart settings. Use
`bash ~/tvremote/tvapp/deploy/rollback.sh` to restore the latest backup.
Run `sudo bash ~/tvremote/tvapp/deploy/setup-privileges.sh` once to allow
password-free full shutdown and desktop recovery. Its sudo rule permits only
`systemctl poweroff` and `systemctl restart lightdm.service` for `obada`.
The graphical session should also start `home.py` immediately after deployment;
the autostart entry covers subsequent boots.

## Pairing and recovery

TV Home shows the current LAN address and rotating six-digit pairing code.
On Android, open that address and enter the code. An existing remote link with
`?k=` migrates to a separate device cookie. The old token remains available
for migration while the previous setup is still present.

The More panel offers app restart and TV Home recovery. The controller service
does not depend on X11 being available at startup. Restart desktop session
restarts LightDM and relies on the configured `obada` autologin. The More panel
can revoke other paired phones or disable the old `?k=` link after migration.
Power Off is full shutdown;
the physical power button is required to start the iMac again.

## App updates

The controller checks `origin/main` on GitHub at startup and every 30 minutes.
Every new commit on main is an update; a GitHub release/tag alone does not trigger one.
When an update is available, the desktop shows a notification (when `notify-send`
and a desktop notification service are available), and TV Home shows an **Update now**
button. The phone's Remote tab shows the same button. On either device,
**Settings → System → Software updates** shows progress, errors, a manual
**Check for updates**, and **Update now**.

One click downloads and validates the code, fast-forwards main, runs
`tvapp/deploy/install.sh` to refresh the controller service and Home autostart,
then restarts the TV app. Playback stops and the remote reconnects automatically.
Settings, pairing and playback history remain in `~/.local/share/imactv`.
This updates the TV app; system packages, VacuumTube and the streaming engine
are managed by the full installer separately.

Updates require a Git checkout on `main`, Git access to `origin`, and a running
user systemd session. Local edits, untracked files, local commits outside
origin/main, or rewritten remote history block installation with an explanation;
the updater never resets, stashes, pushes, or discards local work. If offline,
the check reports the connection problem and can be retried. It never installs
an update without a click. Only the local TV and paired phones can trigger it.

To enable this feature on an existing iMac installation, pull this version once
and redeploy from the repository directory:

```bash
git pull --ff-only origin main
bash tvapp/deploy/install.sh
```

The apply worker uses a separate `imactv-update.service` transient user unit so
restarting `tvremote.service` cannot kill it. Progress is stored in
`~/.local/share/imactv/update.json`; logs are available with
`journalctl --user -u imactv-update.service`. If deployment fails after download,
the error offers a retry; the downloaded code is retained.

## Home and ambient mode

Home keeps its rotating photo background, clock and launcher widgets during normal use. After the
idle delay (`ambient.idle`, 120 s; `ambient.idle_music`, 30 s, while YouTube plays in the background)
the tiles fade away, the clock shrinks, and the sharp photo stays with its caption. With no music the
screen is calm but never empty: about 40 particles drift a few px/s, entering from outside the screen
edges and leaving again, with thin links between near neighbours (~9 fps, dirty regions only; it works on
every photo). When the photo has a traced sky-line the dots also glide onto it and hold still.

While music plays (YouTube's now-playing status, or sound on the speaker monitor) the photo dims a
little and the visualizer runs as a background, not a centrepiece. The sky-line starts to move, then every
track (and every minute or so) the dots flow into a randomly chosen style, weighted: edge flow 6 (dots,
triangles, hexagons and line fragments enter from outside the screen, cross it on gentle curves with links
forming between them and leave again; the music sets their speed, how many are alive and their brightness),
ridge 2, a re-linking full-screen constellation 2, a wide low-poly terrain 2, and prism 2
(a rotating crystal ribbon that opens on bass hits). Circuit 6 joins the rotation when the current
image has usable outlines: eight light runners follow its straight lines, bends, and curves, accelerating
on bass hits and sparkling on treble. The worker traces the displayed crop once and caches its paths;
the animation still uses 64 dots, fixed links, and the batched dirty-region renderer. Aurora has been
removed from automatic rotation. Bass breathes, beats send light along the links, treble twinkles;
colours come from the photo. When the music
stops the particle field fades back in within about a second (no waiting for the next photo). The caption
shows the song and artist while playback metadata is available. The first remote or keyboard input wakes
Home and is swallowed.
While a visualizer effect is showing, the phone remote shows a **Next effect** button (with the current
effect's name) that steps to the next mode in rotation; Home reports this through `viz.json` in the data
directory, and the phone action is `viz_next {}`.

Render a repeatable preview over an image with
`python3 scripts/circuit-preview.py /path/to/image.jpg tvapp/previews/circuit-demo`.
For isolated 1080p measurements on the same background, run
`BENCH_IMAGE=/path/to/image.jpg scripts/viz-bench.sh tvapp 30 circuit prism`.
The benchmark waits for the requested mode before sampling. The background-provider API,
limitations, and prompt for implementing Retro settings are in
[the Circuit handoff](../docs/retro-circuit-handoff.md).

## Media

TV Home is our own catalog UI: it browses Cinemeta, lists Torrentio sources and
plays the best one in `mpv` through the `stremio-service` engine
(`imactv-engine.service`, `127.0.0.1:11470`, installed by `scripts/install-engine.sh`).
Add-ons, subtitle languages (Arabic, Dutch, English by default), cache cap and
quality order live in `~/.local/share/imactv/media.json`; see `media/CONTRACT.md`.
Nothing is kept on disk: the engine cache is capped (`cache_bytes`, 3 GB) and the
torrent is removed when playback ends. Continue Watching comes from
`~/.local/share/imactv/history.json`.

### TorBox

With a TorBox key, Torrentio returns TorBox direct links. Cached ones play at once
and are tried before torrents; torrents remain the fallback.

**From the phone:** open **Settings → TorBox → Add API key**. You can change or
remove the key there too. Play also prompts when no key is set.
**From TV Home on a laptop or PC:** open **Settings → TorBox**, paste the key
and select **Save API key** (or press Enter). Both screens configure the same
computer; saving on the paired phone applies immediately to that computer's
playback without restarting. Separate computers each need their own setup.
The controller checks the key against
`api.torbox.app` over HTTPS, stores it as `~/.local/share/imactv/torbox.key`
(mode 600, written atomically), clears the cached source lists and uses it from
then on. Only paired phones can do this, and key entry is limited to 5 tries per
10 minutes. Phone actions: `torbox_key {key}`, `torbox_remove {}`;
`/api/state` only reports `torbox.configured`.

**From a shell** on the TV machine:

```bash
scripts/set-torbox-key.sh            # prompts, key not echoed
scripts/set-torbox-key.sh --remove
```

It writes the same `torbox.key` file (and drops any old `torbox_key` from
`media.json`, which is still read as a fallback; the file wins), clears the cached
source lists and restarts the controller.

**Where the key can and can't appear.** It never goes to the phone: `sources`
returns an opaque `ref` instead of the direct link, which stays on the TV, and
logs and error text are passed through `media.config.redact()`. It is in
`torbox.key`, in the `mpv.log` (created mode 600) and in mpv's command line
(visible in the process list to local users; this is a single-user machine).
Remaining risk: the phone-to-TV link is plain HTTP on the LAN, so anyone who can
sniff the Wi-Fi can read the key while it is typed.

### Using an existing Stremio server

The engine **is** Stremio's streaming server. Torrentio, Cinemeta and OpenSubtitles are queried directly by the app (defaults in `tvapp/media/config.py`), not taken from your Stremio account, so only the streaming server is needed.

To use a server on another machine instead of installing one locally:

```bash
./install.sh --engine-url http://HOST:11470
# or just the engine step:
scripts/install-engine.sh http://HOST:11470
```

or write `{"engine": "http://HOST:11470"}` into `~/.local/share/imactv/media.json` by hand and restart the controller.

- The server must be reachable from the TV machine on port 11470 (LAN or Tailscale). Stremio's server binds `0.0.0.0:11470` by default.
- For a remote engine the app does not push its cache cap (`cache_bytes`) onto it; that cap only applies to a local engine.

The phone remote follows the TV state: D-pad and Search while browsing, a
buffering card (source, peers, speed, Cancel), and a Now Playing card with scrub
bar, skip, subtitle/audio pickers and subtitle delay. Stop returns to the
title's details. VacuumTube provides YouTube.

### Phone notification and lock-screen controls

On the phone's Remote tab, tap **Show in phone media controls** while something
is playing on the TV. In browsers supporting the Media Session API (such as
Chrome on Android), the notification shade and lock screen can then show the
title, artwork, play/pause and seeking. YouTube also offers previous/next when
the player supports them; series offer Next when another episode is available.
Tap **Phone media controls on · Turn off** to disable it. If playback needs a
fresh tap, the button changes to **Resume phone media controls**.

Keep the remote browser tab open and the phone connected to the TV's network.
The browser runs a silent audio loop to expose the remote session; real sound
continues on the TV. Starting it can take audio focus from other phone media.
No notification permission or app install is required, and the LAN HTTP remote
still works. Available buttons and background lifetime depend on the phone and
browser; closing the tab or suspending the browser ends the controls. Volume
remains in the remote page because the web Media Session API has no TV volume
action. Unsupported browsers keep the usual remote controls.

## Testing

Run `python3 -m unittest discover -s tests -v` in this directory. The test
suite uses a temporary data directory and does not connect to the TV.
Run `node --test tests/test_phone_media.js` for the phone media session tests.
