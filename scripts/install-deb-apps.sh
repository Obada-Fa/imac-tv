#!/usr/bin/env bash
# ==============================================================================
# iMac TV / Zorin TV - Native .deb App Installer
# Downloads & installs official Debian/Ubuntu packages for VacuumTube & Stremio
# ==============================================================================
set -euo pipefail

BOLD='\033[1m'
GREEN='\033[0;32m'
BLUE='\033[0;34m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
NC='\033[0m'

SUDO=""
if [ "$EUID" -ne 0 ]; then
  if command -v sudo &>/dev/null; then
    SUDO="sudo"
  else
    printf "${RED}Error: Root or sudo access is required to install deb packages.${NC}\n" >&2
    exit 1
  fi
fi

TMP_DIR="$(mktemp -d /tmp/tvapps-deb.XXXXXX)"
trap 'rm -rf "$TMP_DIR"' EXIT

printf "${BLUE}${BOLD}=== Installing TV Apps via Native .deb Packages ===${NC}\n"

# 1. VacuumTube (.deb from GitHub Releases)
printf "\n${BLUE}--> Downloading & installing VacuumTube (.deb)...${NC}\n"
VT_DEB="$TMP_DIR/VacuumTube-amd64.deb"
VT_URL="https://github.com/shy1132/VacuumTube/releases/download/v2.0.1/VacuumTube-amd64.deb"

if command -v vacuumtube &>/dev/null; then
  printf "${GREEN}✓ VacuumTube binary is already installed: $(which vacuumtube)${NC}\n"
else
  printf "Downloading %s...\n" "$VT_URL"
  curl -fsSL "$VT_URL" -o "$VT_DEB"
  printf "Installing VacuumTube...\n"
  $SUDO apt-get update
  $SUDO apt-get install -y "$VT_DEB"
  printf "${GREEN}✓ VacuumTube installed successfully.${NC}\n"
fi

# 2. Stremio (.deb from dl.strem.io)
printf "\n${BLUE}--> Downloading & installing Stremio (.deb)...${NC}\n"
STREMIO_DEB="$TMP_DIR/stremio_amd64.deb"
STREMIO_URL="https://dl.strem.io/shell-linux/v4.4.168/stremio_4.4.168-1_amd64.deb"

if command -v stremio &>/dev/null; then
  printf "${GREEN}✓ Stremio binary is already installed: $(which stremio)${NC}\n"
else
  printf "Downloading %s...\n" "$STREMIO_URL"
  curl -fsSL "$STREMIO_URL" -o "$STREMIO_DEB"
  printf "Installing Stremio...\n"
  $SUDO apt-get install -y "$STREMIO_DEB" || {
    printf "${YELLOW}Resolving missing dependencies for Stremio...${NC}\n"
    $SUDO apt-get install -f -y
  }
  printf "${GREEN}✓ Stremio installed successfully.${NC}\n"
fi

# 3. VLC
printf "\n${BLUE}--> Checking VLC...${NC}\n"
if ! command -v vlc &>/dev/null; then
  $SUDO apt-get install -y vlc
fi
printf "${GREEN}✓ VLC is installed.${NC}\n"

printf "\n${GREEN}${BOLD}=== Native TV Apps installed successfully! ===${NC}\n"
