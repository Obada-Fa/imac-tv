#!/usr/bin/env bash
# ==============================================================================
# iMac TV / Zorin TV - Uninstaller
# Stops and removes TV remote service, autostart entries, and sudo privileges
# ==============================================================================
set -euo pipefail

BOLD='\033[1m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
NC='\033[0m'

printf "${YELLOW}${BOLD}=== Uninstalling iMac TV / Zorin TV ===${NC}\n"

# 1. Stop and disable systemd user service
if systemctl --user is-active tvremote.service &>/dev/null; then
  printf "Stopping tvremote.service...\n"
  systemctl --user stop tvremote.service || true
fi

if systemctl --user is-enabled tvremote.service &>/dev/null; then
  printf "Disabling tvremote.service...\n"
  systemctl --user disable tvremote.service || true
fi

if [ -f "$HOME/.config/systemd/user/imactv-engine.service" ]; then
  printf "Removing imactv-engine.service...\n"
  systemctl --user disable --now imactv-engine.service || true
  rm -f "$HOME/.config/systemd/user/imactv-engine.service"
fi

rm -f "$HOME/.config/systemd/user/tvremote.service"
systemctl --user daemon-reload || true

# 2. Remove autostart desktop entries
printf "Removing autostart desktop entries...\n"
rm -f "$HOME/.config/autostart/imactv-home.desktop"
rm -f "$HOME/.config/autostart/stremio-tv.desktop"

# 3. Remove sudoers rule
if [ -f "/etc/sudoers.d/imactv-tv" ]; then
  printf "Removing /etc/sudoers.d/imactv-tv...\n"
  if [ "$EUID" -ne 0 ]; then
    sudo rm -f "/etc/sudoers.d/imactv-tv"
  else
    rm -f "/etc/sudoers.d/imactv-tv"
  fi
fi

# 4. Remove symlink if present
if [ -L "$HOME/tvremote/tvapp" ]; then
  rm -f "$HOME/tvremote/tvapp"
fi

printf "\n${GREEN}${BOLD}✓ iMac TV has been uninstalled successfully.${NC}\n"
printf "Note: App installations (VacuumTube, mpv, stremio-service) were kept intact.\n"
