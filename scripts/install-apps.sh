#!/usr/bin/env bash
# ==============================================================================
# iMac TV / Zorin TV - App Installer (Flatpak / Flathub)
# Installs VacuumTube (YouTube Leanback for TV), Stremio, and VLC.
# ==============================================================================
set -euo pipefail

BOLD='\033[1m'
GREEN='\033[0;32m'
BLUE='\033[0;34m'
YELLOW='\033[1;33m'
NC='\033[0m'

printf "${BLUE}${BOLD}=== Installing TV Apps (VacuumTube & Stremio) via Flatpak ===${NC}\n"

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

# 4. Install Stremio (com.stremio.Stremio)
printf "\n${BLUE}--> Installing Stremio...${NC}\n"
if flatpak info com.stremio.Stremio &>/dev/null || command -v stremio &>/dev/null; then
  printf "${GREEN}✓ Stremio is already installed.${NC}\n"
else
  flatpak install -y flathub com.stremio.Stremio || \
  flatpak install -y --user flathub com.stremio.Stremio
  printf "${GREEN}✓ Stremio installed successfully.${NC}\n"
fi

# 5. Check or install VLC (media player)
printf "\n${BLUE}--> Checking VLC...${NC}\n"
if command -v vlc &>/dev/null || flatpak info org.videolan.VLC &>/dev/null; then
  printf "${GREEN}✓ VLC is already available.${NC}\n"
else
  flatpak install -y flathub org.videolan.VLC || \
  flatpak install -y --user flathub org.videolan.VLC || true
fi

printf "\n${GREEN}${BOLD}=== All TV Apps are ready! ===${NC}\n"
