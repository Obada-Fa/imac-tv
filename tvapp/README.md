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
and are tried before torrents; torrents remain the fallback. On the TV machine run:

```bash
scripts/set-torbox-key.sh
```

It prompts for the key (not echoed), stores it as `torbox_key` in
`~/.local/share/imactv/media.json` (mode 600), clears the cached source lists and
restarts the controller. `scripts/set-torbox-key.sh --remove` turns it off.

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

## Testing

Run `python3 -m unittest discover -s tests -v` in this directory. The test
suite uses a temporary data directory and does not connect to the TV.
