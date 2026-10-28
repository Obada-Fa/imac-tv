# iMac TV

The controller is `server.py`, TV Home is `home.py`, media logic is in `media/`, and the phone remote is in `web/`.
No cloud account is needed for the remote. The iMac must be powered on and on the same LAN.

## UI reference for contributors

The [current screenshot gallery](previews/current/README.md) shows Home, every
Settings category, choice and power menus, Movies, Details, Search, Sources,
stream startup, and the phone remote/settings. The user confirmed that the
current Settings layout matches their installed iMac on 27 October 2026.
Captures use sample data and default appearance; backgrounds and selected shapes
can differ on an installed device.

Use `widgets/glass.py` and `widgets/chrome.py` as the shared design components.
Render the running code before changing a menu; don't copy legacy screenshot
styling or generic dialog classes. Run `python3 scripts/ui-preview.py --check`
to detect source changes since the captures. Older menu screenshots are clearly
separated in [the archive](previews/archive/README.md).

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

## Twitch (no ads)

The Home apps row has a Twitch tile that opens a "Live now" rail of the most-watched streams (OK plays one).
On the phone, the Search tab has a Twitch block with a channel box and the same top streams.
Playback runs `streamlink` (it filters Twitch's ad segments out of the HLS stream) piped into mpv with the same
flags and IPC socket as movies, so volume, pause and the phone's now-playing work unchanged. Listings come from
Twitch's public web GraphQL endpoint (no login), cached for a minute; `twitch.py` holds the queries and the
command line. `./install.sh` installs streamlink (apt, then `pip --user` when apt's is older than 6). Without it
the Twitch tile reports "Install streamlink: re-run ./install.sh". Current streamlink drops ads by default and
no longer has `--twitch-disable-ads`, so the flag is added only when the installed version still lists it.
For a screenshot or test without network set `IMACTV_FAKE_TWITCH=1` (or a path to a JSON list of streams).

## Media

TV Home is our own catalog UI: it browses Cinemeta, lists Torrentio sources and
plays the best one in `mpv` through the `stremio-service` engine
(`imactv-engine.service`, `127.0.0.1:11470`, installed by `scripts/install-engine.sh`).
Add-ons, subtitle languages (Arabic, Dutch, English by default), cache cap and
quality order live in `~/.local/share/imactv/media.json`; see `media/CONTRACT.md`.
Nothing is kept on disk: the engine cache is capped (`cache_bytes`, 3 GB) and the
torrent is removed when playback ends. Continue Watching comes from
`~/.local/share/imactv/history.json`.

### Playback help

During playback the controller samples mpv once a second. Five distinct buffering
stalls of at least one second within two minutes (or one continuous 20-second
stall) offer a smaller source. Startup loading, pauses and seeking are excluded.
Sustained frame loss or growing audio/video desynchronization for ten seconds,
with at least three seconds buffered, instead offers a lower resolution or a
simpler format, preferring 1080p H.264 SDR for demanding 4K sources. These are
likely causes; the app does not claim that the internet plan or GPU is at fault.

A **Switch and continue / Keep watching** window appears above the video, and
the phone Remote tab shows the same recommendation. Physical keyboard arrows
select either button; Enter activates it and Escape dismisses it. The phone
D-pad's OK switches and Back dismisses. Each cause is offered once per source;
there is no automatic switch. Failed startup or a player error offers another
source immediately when one is available, with **Not now** as the dismiss option.
Known-dead torrents, uncached direct links and sources already tried in the same
recovery sequence are excluded. Without a suitable alternative playback continues
as usual, or the existing playback error is shown.

Switching briefly pauses the old source, captures its current timestamp, and
starts the replacement there, preserving whether playback was paused. A failed
source uses its last known position, including a resume position when startup
failed. Opening the replacement may take a moment. Different cuts of a movie or
episode can have different timing; this is timestamp continuation, not automatic
scene matching. Source URLs and TorBox credentials stay on the controller.

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

The phone keeps its clock, glass controls and blurred photo background, with no
decorative constellation lines or dots. This simpler appearance applies across
the phone's Remote, browsing and Settings screens.

### Phone hardware volume buttons

The browser remote's on-screen volume controls send commands to the TV. Phone
hardware volume buttons are not currently connected to those commands: the Web
Media Session API has no remote-volume action, and mobile browsers do not reliably
deliver those button presses to page JavaScript, especially in the background.
Adding the web remote to the home screen does not grant native volume handling.

The Flutter **Nexus Remote** app has an Android implementation in its TV section.
It opens this exact web remote full screen, with the same clock,
menus, catalog and playback functions. Pair with the six-digit code, then enable
**Settings → Connections → Nexus Remote → TV volume buttons**. A native
`MediaSession` / `VolumeProvider` and connected-device foreground service send
volume commands across Nexus sections while open, and from other apps/the lock
screen while TV playback is active. Android can fall back to phone volume when
TV playback is paused or stopped. Turn it off in
the app or its notification. Changing TV credentials, revoked pairing, or a
persistent lost connection ends the session. Other active phone media apps and
device battery restrictions can affect Android's routing; real-device validation
is still pending. This implementation does not change the browser's capabilities.

The shared Flutter iPhone runner supports the on-screen TV controls, with no
background hardware-volume interception. Build and signing on a Mac remain
pending. Nexus source, setup, current screenshots and native tests are in the
[Nexus Remote repository](https://github.com/Obada-Fa/nexus-remote).
Download the Android preview APK from its
[GitHub releases](https://github.com/Obada-Fa/nexus-remote/releases).
See the
[Web Media Session actions](https://w3c.github.io/mediasession/#enumdef-mediasessionaction)
and [Android remote-volume API](https://developer.android.com/reference/android/media/session/MediaSession#setPlaybackToRemote(android.media.VolumeProvider)).

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
