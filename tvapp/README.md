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

The phone remote follows the TV state: D-pad and Search while browsing, a
buffering card (source, peers, speed, Cancel), and a Now Playing card with scrub
bar, skip, subtitle/audio pickers and subtitle delay. Stop returns to the
title's details. VacuumTube provides YouTube.

## Testing

Run `python3 -m unittest discover -s tests -v` in this directory. The test
suite uses a temporary data directory and does not connect to the TV.
