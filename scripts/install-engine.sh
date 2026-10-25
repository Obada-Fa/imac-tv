#!/usr/bin/env bash
# ==============================================================================
# iMac TV - Streaming engine installer
# Installs the headless stremio-service (torrent streaming server on
# 127.0.0.1:11470) and a systemd user unit `imactv-engine.service` that runs its
# bundled server.js directly (no tray icon). Falls back to Flatpak.
#
# With a URL argument (install-engine.sh http://host:11470) nothing is installed:
# the TV is pointed at that existing Stremio server via media.json instead.
# ==============================================================================
set -euo pipefail

BOLD='\033[1m'; GREEN='\033[0;32m'; BLUE='\033[0;34m'; YELLOW='\033[1;33m'; NC='\033[0m'
SHARE=/usr/share/stremio-service
UNIT="$HOME/.config/systemd/user/imactv-engine.service"
SUDO=""; [ "$EUID" -ne 0 ] && SUDO="sudo"

if [ -n "${1:-}" ]; then
  ENGINE_URL="${1%/}"
  MEDIA_JSON="${IMACTV_DATA_DIR:-$HOME/.local/share/imactv}/media.json"
  printf "${BLUE}${BOLD}=== Using existing Stremio server at %s ===${NC}\n" "$ENGINE_URL"
  mkdir -p "$(dirname "$MEDIA_JSON")"
  python3 - "$MEDIA_JSON" "$ENGINE_URL" <<'PYEOF'
import json, sys
path, url = sys.argv[1], sys.argv[2]
try:
    with open(path) as f:
        data = json.load(f)
    if not isinstance(data, dict):
        data = {}
except (OSError, ValueError):
    data = {}
data["engine"] = url
with open(path, "w") as f:
    json.dump(data, f, indent=2)
PYEOF
  # Any HTTP status means a server answered; 000 means nothing is listening.
  CODE="$(curl -s -o /dev/null -w '%{http_code}' --max-time 5 "$ENGINE_URL/stats.json" || true)"
  if [ "$CODE" != "000" ] && [ -n "$CODE" ]; then
    printf "${GREEN}✓ Stremio server answers at %s${NC}\n" "$ENGINE_URL"
  else
    printf "${YELLOW}Saved, but %s is not answering yet. Make sure Stremio/stremio-service is running there\n" "$ENGINE_URL"
    printf "and reachable from this machine (LAN or Tailscale, port open).${NC}\n"
  fi
  exit 0
fi

# A Stremio server already on 11470 that isn't ours (stremio-service tray, Stremio app): use it as-is
# instead of adding a second one that would fight it for the port.
if ! systemctl --user is-active --quiet imactv-engine.service 2>/dev/null; then
  CODE="$(curl -s -o /dev/null -w '%{http_code}' --max-time 3 http://127.0.0.1:11470/stats.json || true)"
  if [ -n "$CODE" ] && [ "$CODE" != "000" ]; then
    printf "${GREEN}✓ Using the Stremio server already running on 127.0.0.1:11470${NC}\n"
    printf "${YELLOW}  Keep Stremio/stremio-service starting at login, or the TV can't play movies.${NC}\n"
    exit 0
  fi
fi

printf "${BLUE}${BOLD}=== Installing streaming engine (stremio-service) ===${NC}\n"

USER_SHARE="$HOME/.local/share/stremio-service"

if [ ! -x "$SHARE/stremio-runtime" ] && [ ! -x "$USER_SHARE/stremio-runtime" ]; then
  TMP_DIR="$(mktemp -d)"; trap 'rm -rf "$TMP_DIR"' EXIT
  URL="$(curl -fsSL https://api.github.com/repos/Stremio/stremio-service/releases/latest \
    | grep -o 'https://[^"]*stremio-service_amd64\.deb' | head -1 || true)"
  if [ -n "$URL" ] && curl -fsSL "$URL" -o "$TMP_DIR/stremio-service.deb"; then
    if [ -n "$SUDO" ] && sudo -n true 2>/dev/null; then
      $SUDO apt-get install -y "$TMP_DIR/stremio-service.deb" \
        || { $SUDO apt-get install -f -y; true; }
    else
      mkdir -p "$USER_SHARE" "$TMP_DIR/extracted"
      dpkg -x "$TMP_DIR/stremio-service.deb" "$TMP_DIR/extracted"
      cp -r "$TMP_DIR/extracted/usr/share/stremio-service/"* "$USER_SHARE/"
    fi
  fi
fi

if [ -x "$SHARE/stremio-runtime" ]; then
  # The package ships a tray app plus the bundled node runtime and server.js;
  # run the latter directly so no tray icon appears. ffmpeg/ffprobe sit beside it.
  EXEC="$SHARE/stremio-runtime server.js"; WORKDIR="$SHARE"
elif [ -x "$USER_SHARE/stremio-runtime" ]; then
  EXEC="$USER_SHARE/stremio-runtime server.js"; WORKDIR="$USER_SHARE"
else
  printf "${YELLOW}.deb unavailable; falling back to Flatpak com.stremio.Service${NC}\n"
  command -v flatpak >/dev/null || { if sudo -n true 2>/dev/null; then $SUDO apt-get install -y flatpak; fi; }
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
