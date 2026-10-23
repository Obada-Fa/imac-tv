#!/usr/bin/env bash
set -euo pipefail
backup_dir="${1:-$(find "$HOME/.local/share/imactv/backups" -mindepth 1 -maxdepth 1 -type d | sort | tail -1)}"
test -f "$backup_dir/tvremote.service"
cp -a "$backup_dir/tvremote.service" "$HOME/.config/systemd/user/tvremote.service"
if test -f "$backup_dir/imactv-home.desktop"; then
  cp -a "$backup_dir/imactv-home.desktop" "$HOME/.config/autostart/imactv-home.desktop"
else
  cat > "$HOME/.config/autostart/imactv-home.desktop" <<'EOF'
[Desktop Entry]
Type=Application
Name=iMac TV Home (disabled)
Exec=/bin/true
Hidden=true
EOF
fi
if test -f "$backup_dir/mintwelcome.desktop"; then
  cp -a "$backup_dir/mintwelcome.desktop" "$HOME/.config/autostart/mintwelcome.desktop"
else
  rm -f "$HOME/.config/autostart/mintwelcome.desktop"
fi
systemctl --user daemon-reload
systemctl --user restart tvremote.service
printf 'Restored controller and startup settings from %s\n' "$backup_dir"
