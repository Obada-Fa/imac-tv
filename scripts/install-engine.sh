#!/usr/bin/env bash
# ==============================================================================
# iMac TV - Streaming engine installer
# Installs the headless stremio-service (torrent streaming server on
# 127.0.0.1:11470) and a systemd user unit `imactv-engine.service` that runs its
# bundled server.js directly (no tray icon). Falls back to Flatpak.
# ==============================================================================
set -euo pipefail

BOLD='\033[1m'; GREEN='\033[0;32m'; BLUE='\033[0;34m'; YELLOW='\033[1;33m'; NC='\033[0m'
SHARE=/usr/share/stremio-service
UNIT="$HOME/.config/systemd/user/imactv-engine.service"
SUDO=""; [ "$EUID" -ne 0 ] && SUDO="sudo"

printf "${BLUE}${BOLD}=== Installing streaming engine (stremio-service) ===${NC}\n"

if [ ! -x "$SHARE/stremio-runtime" ]; then
  TMP_DIR="$(mktemp -d)"; trap 'rm -rf "$TMP_DIR"' EXIT
  URL="$(curl -fsSL https://api.github.com/repos/Stremio/stremio-service/releases/latest \
    | grep -o 'https://[^"]*stremio-service_amd64\.deb' | head -1 || true)"
  if [ -n "$URL" ] && curl -fsSL "$URL" -o "$TMP_DIR/stremio-service.deb"; then
    $SUDO apt-get install -y "$TMP_DIR/stremio-service.deb" \
      || { $SUDO apt-get install -f -y; true; }
  fi
fi

if [ -x "$SHARE/stremio-runtime" ]; then
  # The package ships a tray app plus the bundled node runtime and server.js;
  # run the latter directly so no tray icon appears. ffmpeg/ffprobe sit beside it.
  EXEC="$SHARE/stremio-runtime server.js"; WORKDIR="$SHARE"
else
  printf "${YELLOW}.deb unavailable; falling back to Flatpak com.stremio.Service${NC}\n"
  command -v flatpak >/dev/null || $SUDO apt-get install -y flatpak
  flatpak remote-add --user --if-not-exists flathub https://dl.flathub.org/repo/flathub.flatpakrepo 2>/dev/null || true
  flatpak info com.stremio.Service &>/dev/null || flatpak install -y --user flathub com.stremio.Service
  EXEC="/usr/bin/flatpak run com.stremio.Service"; WORKDIR="$HOME"
fi

mkdir -p "$HOME/.config/systemd/user" "$HOME/.stremio-server"
cat > "$UNIT" <<UNITEOF
[Unit]
Description=iMac TV streaming engine (stremio-service server.js)
After=network.target

[Service]
Type=simple
WorkingDirectory=$WORKDIR
ExecStart=$EXEC
Restart=always
RestartSec=3
Environment=NO_CORS=1

[Install]
WantedBy=default.target
UNITEOF
systemctl --user daemon-reload
systemctl --user enable --now imactv-engine.service
for _ in $(seq 1 20); do
  curl -fsS --max-time 2 http://127.0.0.1:11470/settings >/dev/null 2>&1 && { printf "${GREEN}✓ Engine is up on 127.0.0.1:11470${NC}\n"; exit 0; }
  sleep 1
done
printf "${YELLOW}Engine unit installed but not answering yet; check: journalctl --user -u imactv-engine${NC}\n"
