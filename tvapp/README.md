# iMac TV

The controller is `server.py`, TV Home is `home.py`, and the Android remote is in `web/`.
No cloud account is needed for the remote. The iMac must be powered on and on the same LAN.

## Install

Copy this directory to `~/tvremote/tvapp` on the iMac, then run
`bash ~/tvremote/tvapp/deploy/install.sh` there. The installer backs up the
existing service and Stremio autostart settings. Use
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

## Home feeds

`home.py` runs `feed_sync.py` at startup and every ten minutes. The adapter
reads Stremio's local `library_recent` cache read-only and builds Continue
Watching, Recently Watched, and Watch Again rails in
`~/.local/share/imactv/recommendations.json`; downloaded artwork is cached in
the same data directory. No account password or session token is copied into
the feed file. Arrow keys move between the app shelf and content rails; focusing
a card previews that title in the hero banner. Selecting a card opens its app.
VacuumTube's local profile must have a signed-in YouTube session and a separate
feed adapter before personalized YouTube rows can be shown.

## Testing

Run `python3 -m unittest discover -s tests -v` in this directory. The test
suite uses a temporary data directory and does not connect to the TV.
