#!/usr/bin/env bash
# Save a TorBox API key for TV Home: Torrentio then returns TorBox direct links,
# which play straight away (cached) instead of torrenting.
# Usage: scripts/set-torbox-key.sh            (prompts, key not echoed)
#        scripts/set-torbox-key.sh --remove
set -euo pipefail
DATA="${IMACTV_DATA_DIR:-$HOME/.local/share/imactv}"
FILE="$DATA/media.json"
if [ "${1:-}" = "--remove" ]; then
  KEY=""
else
  read -rsp "TorBox API key (torbox.app → Settings → API key): " KEY; echo
  [ -n "$KEY" ] || { echo "No key given" >&2; exit 1; }
fi
mkdir -p "$DATA"
KEY="$KEY" FILE="$FILE" python3 - <<'PY'
import json, os
path = os.environ["FILE"]
try:
    cfg = json.load(open(path))
except (OSError, ValueError):
    cfg = {}
cfg["torbox_key"] = os.environ["KEY"].strip()
tmp = path + ".tmp"
with open(os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600), "w") as f:
    json.dump(cfg, f, indent=2)
os.replace(tmp, path)
PY
chmod 600 "$FILE"
rm -rf "$DATA/cache/addons"  # drop source lists fetched without the key
systemctl --user restart tvremote.service 2>/dev/null || echo "Restart the TV controller to apply."
if [ -n "$KEY" ]; then echo "TorBox key saved."; else echo "TorBox key removed."; fi
