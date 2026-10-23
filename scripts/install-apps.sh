#!/usr/bin/env bash
# ==============================================================================
# iMac TV / Zorin TV - App Installer (Flatpak / Flathub)
# Installs VacuumTube (YouTube Leanback for TV). Media playback uses mpv + the
# stremio-service engine (see install-engine.sh).
# ==============================================================================
set -euo pipefail

BOLD='\033[1m'
GREEN='\033[0;32m'
BLUE='\033[0;34m'
YELLOW='\033[1;33m'
NC='\033[0m'

printf "${BLUE}${BOLD}=== Installing TV Apps (VacuumTube) via Flatpak ===${NC}\n"

# 1. Ensure flatpak is available
if ! command -v flatpak &>/dev/null; then
  printf "${YELLOW}Flatpak is not installed. Installing flatpak...${NC}\n"
  if command -v sudo &>/dev/null && [ "$EUID" -ne 0 ]; then
    sudo apt-get update && sudo apt-get install -y flatpak
  else
    apt-get update && apt-get install -y flatpak
  fi
fi

# 2. Ensure flathub remote is configured (user and system)
printf "${BLUE}Checking Flathub remote...${NC}\n"
flatpak remote-add --user --if-not-exists flathub https://dl.flathub.org/repo/flathub.flatpakrepo 2>/dev/null || true
if command -v sudo &>/dev/null && [ "$EUID" -ne 0 ]; then
  sudo flatpak remote-add --if-not-exists flathub https://dl.flathub.org/repo/flathub.flatpakrepo 2>/dev/null || true
fi

# 3. Install VacuumTube (rocks.shy.VacuumTube)
printf "\n${BLUE}--> Installing VacuumTube (YouTube Leanback TV client)...${NC}\n"
if flatpak info rocks.shy.VacuumTube &>/dev/null; then
  printf "${GREEN}✓ VacuumTube is already installed.${NC}\n"
else
  flatpak install -y flathub rocks.shy.VacuumTube || \
  flatpak install -y --user flathub rocks.shy.VacuumTube
  printf "${GREEN}✓ VacuumTube installed successfully.${NC}\n"
fi

printf "\n${GREEN}${BOLD}=== All TV Apps are ready! ===${NC}\n"
