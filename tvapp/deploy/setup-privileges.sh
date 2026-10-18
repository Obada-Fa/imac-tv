#!/usr/bin/env bash
set -euo pipefail

TARGET_USER="${SUDO_USER:-$USER}"

# TV maintenance operations need password-free privileges for poweroff and session restart
file=/tmp/imactv-sudoers.$$
trap 'rm -f "$file"' EXIT

cat > "$file" <<EOF
$TARGET_USER ALL=(root) NOPASSWD: /usr/bin/systemctl poweroff, /bin/systemctl poweroff, /usr/bin/systemctl restart display-manager.service, /usr/bin/systemctl restart gdm.service, /usr/bin/systemctl restart gdm3.service, /usr/bin/systemctl restart lightdm.service
EOF

chmod 600 "$file"
visudo -cf "$file"
install -o root -g root -m 440 "$file" /etc/sudoers.d/imactv-tv
visudo -cf /etc/sudoers.d/imactv-tv
printf 'Sudoers rules successfully configured for %s in /etc/sudoers.d/imactv-tv\n' "$TARGET_USER"
