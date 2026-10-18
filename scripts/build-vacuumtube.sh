#!/usr/bin/env bash
# ==============================================================================
# iMac TV / Zorin TV - Build VacuumTube from Source
# Builds the YouTube Leanback desktop Electron app using electron-builder
# ==============================================================================
set -euo pipefail

BOLD='\033[1m'
GREEN='\033[0;32m'
BLUE='\033[0;34m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
NC='\033[0m'

printf "${BLUE}${BOLD}=== Building VacuumTube from Source ===${NC}\n"

# 1. Check git, node, npm
for cmd in git node npm; do
  if ! command -v "$cmd" &>/dev/null; then
    printf "${YELLOW}Missing '$cmd'. Installing nodejs and npm...${NC}\n"
    if command -v sudo &>/dev/null && [ "$EUID" -ne 0 ]; then
      sudo apt-get update && sudo apt-get install -y git nodejs npm
    else
      apt-get update && apt-get install -y git nodejs npm
    fi
    break
  fi
done

BUILD_DIR="$(mktemp -d /tmp/build-vacuumtube.XXXXXX)"
printf "Working directory: %s\n" "$BUILD_DIR"

printf "\n${BLUE}--> Cloning VacuumTube repository...${NC}\n"
git clone --depth 1 https://github.com/shy1132/VacuumTube.git "$BUILD_DIR/VacuumTube"
cd "$BUILD_DIR/VacuumTube"

printf "\n${BLUE}--> Installing dependencies...${NC}\n"
npm install

printf "\n${BLUE}--> Building Linux packages (deb & AppImage)...${NC}\n"
npm run linux:build

DIST_DIR="$BUILD_DIR/VacuumTube/dist"
if [ ! -d "$DIST_DIR" ]; then
  printf "${RED}Error: Build directory 'dist' not found.${NC}\n" >&2
  exit 1
fi

DEB_PKG="$(find "$DIST_DIR" -name "*.deb" | head -n 1 || true)"
APPIMAGE_PKG="$(find "$DIST_DIR" -name "*.AppImage" | head -n 1 || true)"

printf "\n${GREEN}${BOLD}✓ Build complete!${NC}\n"
if [ -n "$DEB_PKG" ]; then
  printf "Built .deb: %s\n" "$DEB_PKG"
  read -r -p "Would you like to install the built .deb package now? [Y/n] " response
  response=${response:-Y}
  if [[ "$response" =~ ^[Yy] ]]; then
    if command -v sudo &>/dev/null && [ "$EUID" -ne 0 ]; then
      sudo apt-get install -y "$DEB_PKG"
    else
      apt-get install -y "$DEB_PKG"
    fi
    printf "${GREEN}✓ VacuumTube installed successfully!${NC}\n"
  fi
fi

if [ -n "$APPIMAGE_PKG" ]; then
  printf "Built AppImage: %s\n" "$APPIMAGE_PKG"
fi

printf "\n${GREEN}Build script finished.${NC}\n"
