#!/usr/bin/env bash
set -euo pipefail

TARGET_USER="${SUDO_USER:-$USER}"

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# TV maintenance operations need password-free privileges for poweroff, session restart, and backlight control
file=/tmp/imactv-sudoers.$$
trap 'rm -f "$file"' EXIT

if [[ -f "$SCRIPT_DIR/imactv-brightness" ]]; then
  install -o root -g root -m 755 "$SCRIPT_DIR/imactv-brightness" /usr/local/sbin/imactv-brightness
fi

cat > "$file" <<EOF
$TARGET_USER ALL=(root) NOPASSWD: /usr/bin/systemctl poweroff, /bin/systemctl poweroff, /usr/bin/systemctl restart display-manager.service, /usr/bin/systemctl restart gdm.service, /usr/bin/systemctl restart gdm3.service, /usr/bin/systemctl restart lightdm.service, /usr/local/sbin/imactv-brightness
EOF

chmod 600 "$file"
visudo -cf "$file"
install -o root -g root -m 440 "$file" /etc/sudoers.d/imactv-tv
visudo -cf /etc/sudoers.d/imactv-tv
printf 'Sudoers rules successfully configured for %s in /etc/sudoers.d/imactv-tv\n' "$TARGET_USER"
