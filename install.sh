#!/usr/bin/env bash
# ==============================================================================
# iMac TV / Zorin TV - Complete Automated Installer
# Sets up TV Home, Mobile Web Remote, System Service, VacuumTube, mpv & streaming engine
# ==============================================================================
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
APP_DIR="$ROOT_DIR/tvapp"

BOLD='\033[1m'
GREEN='\033[0;32m'
BLUE='\033[0;34m'
YELLOW='\033[1;33m'
CYAN='\033[0;36m'
RED='\033[0;31m'
NC='\033[0m'

APP_MODE="flatpak"
SKIP_APPS=false
SKIP_ENGINE=false
SKIP_PRIVILEGES=false

usage() {
  cat <<EOF
Usage: ./install.sh [OPTIONS]

Options:
  --flatpak-apps       Install VacuumTube via Flatpak (Default, recommended on Zorin OS)
  --deb-apps           Install VacuumTube via official .deb packages
  --build-vacuumtube   Build VacuumTube from source (requires node/npm)
  --skip-apps          Skip installing apps (use if already installed)
  --skip-engine        Skip the stremio-service streaming engine
  --skip-privileges    Skip configuring sudoers rule for poweroff/desktop restart
  -h, --help           Show this help message
EOF
  exit 0
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --flatpak-apps)
      APP_MODE="flatpak"
      shift
      ;;
    --deb-apps)
      APP_MODE="deb"
      shift
      ;;
    --build-vacuumtube)
      APP_MODE="build"
      shift
      ;;
    --skip-apps)
      SKIP_APPS=true
      shift
      ;;
    --skip-engine)
      SKIP_ENGINE=true
      shift
      ;;
    --skip-privileges)
      SKIP_PRIVILEGES=true
      shift
      ;;
    -h|--help)
      usage
      ;;
    *)
      printf "${RED}Unknown option: %s${NC}\n" "$1"
      usage
      ;;
  esac
done

printf "${CYAN}${BOLD}"
cat << "BANNER"
  ___ __  __            _____ _   _ 
 |_ _|  \/  | __ _  ___|_   _| | | |
  | || |\/| |/ _` |/ __| | | | | | |
  | || |  | | (_| | (__  | | | |_| |
 |___|_|  |_|\__,_|\___| |_|  \___/ 
      Turn your PC / iMac into a Smart TV
BANNER
printf "${NC}\n"

# 1. System APT Dependencies
printf "${BLUE}${BOLD}[1/4] Installing system dependencies via apt...${NC}\n"
APT_PKGS=(
  python3
  python3-gi
  gir1.2-gtk-3.0
  gir1.2-gdkpixbuf-2.0
  python3-qrcode
  python3-pil
  wmctrl
  xdotool
  x11-utils
  libx11-6
  libxtst6
  pulseaudio-utils
  curl
  flatpak
  unzip
  mpv
  fonts-noto-core
  fonts-noto-ui-core
  fonts-inter
)
# Optional packages: install when the distro has them, otherwise carry on.
OPTIONAL_PKGS=(fonts-noto-core fonts-noto-ui-core fonts-inter)
REQUIRED_PKGS=()
for pkg in "${APT_PKGS[@]}"; do
  if [[ " ${OPTIONAL_PKGS[*]} " == *" $pkg "* ]]; then
    apt-cache show "$pkg" &>/dev/null || continue
  fi
  REQUIRED_PKGS+=("$pkg")
done
APT_PKGS=("${REQUIRED_PKGS[@]}")

MISSING_PKGS=()
for pkg in "${APT_PKGS[@]}"; do
  if ! dpkg -s "$pkg" &>/dev/null; then
    MISSING_PKGS+=("$pkg")
  fi
done

if [ ${#MISSING_PKGS[@]} -gt 0 ]; then
  printf "Installing missing packages: %s\n" "${MISSING_PKGS[*]}"
  if [ "$EUID" -ne 0 ]; then
    sudo apt-get update && sudo apt-get install -y "${MISSING_PKGS[@]}"
  else
    apt-get update && apt-get install -y "${MISSING_PKGS[@]}"
  fi
else
  printf "${GREEN}✓ All system packages are already installed.${NC}\n"
fi

# 2. TV Applications (VacuumTube) and streaming engine
printf "\n${BLUE}${BOLD}[2/4] Setting up VacuumTube and the streaming engine...${NC}\n"
if [ "$SKIP_APPS" = true ]; then
  printf "${YELLOW}Skipping apps installation as requested (--skip-apps).${NC}\n"
elif [ "$APP_MODE" = "build" ]; then
  printf "Building VacuumTube from source...\n"
  bash "$ROOT_DIR/scripts/build-vacuumtube.sh"
elif [ "$APP_MODE" = "deb" ]; then
  bash "$ROOT_DIR/scripts/install-deb-apps.sh"
else
  bash "$ROOT_DIR/scripts/install-apps.sh"
fi

# Inter font fallback when fonts-inter is not packaged
if ! fc-list 2>/dev/null | grep -qi 'Inter'; then
  FONT_DIR="$HOME/.local/share/fonts/Inter"
  TMP_FONT="$(mktemp -d)"
  URL="$(curl -fsSL https://api.github.com/repos/rsms/inter/releases/latest \
    | grep -o 'https://[^"]*Inter-[0-9.]*\.zip' | head -1 || true)"
  if [ -n "$URL" ] && curl -fsSL "$URL" -o "$TMP_FONT/inter.zip" && command -v unzip &>/dev/null; then
    mkdir -p "$FONT_DIR"
    unzip -qo "$TMP_FONT/inter.zip" 'extras/ttf/*.ttf' 'Inter*.ttf' -d "$FONT_DIR" 2>/dev/null || true
    fc-cache -f "$FONT_DIR" || true
  else
    printf "${YELLOW}Inter font not installed; TV Home will fall back to system fonts.${NC}\n"
  fi
  rm -rf "$TMP_FONT"
fi

if [ "$SKIP_ENGINE" = true ]; then
  printf "${YELLOW}Skipping streaming engine (--skip-engine).${NC}\n"
else
  bash "$ROOT_DIR/scripts/install-engine.sh"
fi

# 3. Sudo Privileges for TV Power Off / Desktop Session Restart
printf "\n${BLUE}${BOLD}[3/4] Configuring maintenance privileges...${NC}\n"
if [ "$SKIP_PRIVILEGES" = true ]; then
  printf "${YELLOW}Skipping sudo privileges configuration (--skip-privileges).${NC}\n"
else
  if [ "$EUID" -ne 0 ]; then
    sudo bash "$APP_DIR/deploy/setup-privileges.sh"
  else
    bash "$APP_DIR/deploy/setup-privileges.sh"
  fi
fi

# 4. Deploy Controller Service and Autostart
printf "\n${BLUE}${BOLD}[4/4] Deploying TV Controller and Autostart...${NC}\n"
TVAPP_DIR="$APP_DIR" bash "$APP_DIR/deploy/install.sh"

# Get Local IP
IP="$(hostname -I 2>/dev/null | awk '{print $1}' || echo "localhost")"

printf "\n${GREEN}${BOLD}==================================================================${NC}\n"
printf "${GREEN}${BOLD}           iMac TV / Zorin TV Installation Complete!             ${NC}\n"
printf "${GREEN}${BOLD}==================================================================${NC}\n"
printf "\n"
printf "  ${BOLD}1. TV Home:${NC} Will start automatically on login. You can test it now by running:\n"
printf "     ${CYAN}python3 %s/home.py${NC}\n\n" "$APP_DIR"
printf "  ${BOLD}2. Phone Remote Control:${NC}\n"
printf "     Open your mobile browser on the same Wi-Fi network:\n"
printf "     ${CYAN}${BOLD}http://%s:8765${NC}\n\n" "$IP"
printf "  ${BOLD}3. Pairing:${NC}\n"
printf "     The TV Home screen displays a 6-digit code and a QR code.\n"
printf "     Scan the QR code or enter the code on your phone to connect!\n\n"
printf "  ${BOLD}Installed:${NC}\n"
printf "     - ${BOLD}VacuumTube:${NC} YouTube Leanback TV interface with adblocker and remote/gamepad nav\n"
printf "     - ${BOLD}Movies & series:${NC} built-in catalog (Cinemeta/Torrentio add-ons) played with mpv\n"
printf "     - ${BOLD}Engine:${NC} imactv-engine.service (stremio-service) on 127.0.0.1:11470\n"
printf "     Add-ons are configurable in ~/.local/share/imactv/media.json\n\n"
