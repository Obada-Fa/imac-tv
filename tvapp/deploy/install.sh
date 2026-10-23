#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
DEFAULT_APP_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
app_dir="${TVAPP_DIR:-$DEFAULT_APP_DIR}"

if [[ ! -f "$app_dir/server.py" ]]; then
  if [[ -f "$HOME/tvremote/tvapp/server.py" ]]; then
    app_dir="$HOME/tvremote/tvapp"
  else
    printf 'Error: Cannot find tvapp/server.py at %s\n' "$app_dir" >&2
    exit 1
  fi
fi

# Ensure ~/tvremote/tvapp points to app_dir for backwards compatibility
if [[ "$app_dir" != "$HOME/tvremote/tvapp" ]]; then
  mkdir -p "$HOME/tvremote"
  ln -sfn "$app_dir" "$HOME/tvremote/tvapp"
fi
backup_dir="$HOME/.local/share/imactv/backups/$(date +%Y%m%d-%H%M%S)"
mkdir -p "$backup_dir" "$HOME/.config/systemd/user" "$HOME/.config/autostart"
chmod 700 "$backup_dir"
if test -f "$HOME/.config/systemd/user/tvremote.service"; then
  cp -a "$HOME/.config/systemd/user/tvremote.service" "$backup_dir/tvremote.service"
fi
if test -f "$HOME/.config/autostart/imactv-home.desktop"; then
  cp -a "$HOME/.config/autostart/imactv-home.desktop" "$backup_dir/imactv-home.desktop"
fi
if test -f "$HOME/.config/autostart/mintwelcome.desktop"; then
  cp -a "$HOME/.config/autostart/mintwelcome.desktop" "$backup_dir/mintwelcome.desktop"
fi

cat > "$HOME/.config/systemd/user/tvremote.service" <<EOF
[Unit]
Description=iMac TV controller
After=network.target

[Service]
Type=simple
ExecStart=/usr/bin/python3 $app_dir/server.py
Restart=always
RestartSec=3
Environment=PYTHONUNBUFFERED=1

[Install]
WantedBy=default.target
EOF

# Stremio desktop is no longer used; drop any leftover autostart override.
rm -f "$HOME/.config/autostart/stremio-tv.desktop"

cat > "$HOME/.config/autostart/imactv-home.desktop" <<EOF
[Desktop Entry]
Type=Application
Name=iMac TV Home
Exec=/usr/bin/python3 $app_dir/home.py
Terminal=false
NoDisplay=true
X-Mint-autostart-enabled=true
EOF

cat > "$HOME/.config/autostart/mintwelcome.desktop" <<'EOF'
[Desktop Entry]
Type=Application
Name=Mint Welcome (disabled for iMac TV)
Exec=/bin/true
Hidden=true
EOF

systemctl --user daemon-reload
systemctl --user enable tvremote.service
systemctl --user restart tvremote.service
# The TV screen (home.py) is a separate process: close it so the controller relaunches it with the new code.
pkill -f "python3 .*/home.py" 2>/dev/null || true
for try in $(seq 1 20); do
  if curl -fsS --max-time 2 http://127.0.0.1:8765/api/pair-status | grep -q '"paired"'; then
    printf 'Controller ready. Backup: %s\n' "$backup_dir"
    exit 0
  fi
  sleep 1
done
printf 'Controller did not start; restoring the old service.\n' >&2
if test -f "$backup_dir/tvremote.service"; then
  cp -a "$backup_dir/tvremote.service" "$HOME/.config/systemd/user/tvremote.service"
fi
systemctl --user daemon-reload
exit 1
