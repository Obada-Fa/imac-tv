#!/usr/bin/env bash
# Save a TorBox API key for TV Home: Torrentio then returns TorBox direct links,
# which play straight away (cached) instead of torrenting.
# The key goes to $DATA/torbox.key (mode 600, atomic write); the phone remote uses the same file.
# Usage: scripts/set-torbox-key.sh            (prompts, key not echoed)
#        scripts/set-torbox-key.sh --remove
set -euo pipefail
DATA="${IMACTV_DATA_DIR:-$HOME/.local/share/imactv}"
if [ "${1:-}" = "--remove" ]; then
  KEY=""
else
  read -rsp "TorBox API key (torbox.app → Settings → API key): " KEY; echo
  [ -n "$KEY" ] || { echo "No key given" >&2; exit 1; }
fi
mkdir -p -m 700 "$DATA"
KEY="$KEY" DATA="$DATA" python3 - <<'PY'
import json, os, re, sys
data = os.environ["DATA"]
key = os.environ["KEY"].strip()
path, tmp = os.path.join(data, "torbox.key"), os.path.join(data, "torbox.key.tmp")
if key:
    if not re.fullmatch(r"[A-Za-z0-9-]{16,128}", key):
        sys.exit("That doesn't look like a TorBox API key")
    with os.fdopen(os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600), "w") as f:
        f.write(key + "\n")
        f.flush()
        os.fsync(f.fileno())
    os.chmod(tmp, 0o600)
    os.replace(tmp, path)
elif os.path.exists(path):
    os.unlink(path)
# an old media.json entry would otherwise be the fallback
cfg_path = os.path.join(data, "media.json")
try:
    cfg = json.load(open(cfg_path))
except (OSError, ValueError):
    cfg = None
if isinstance(cfg, dict) and cfg.pop("torbox_key", None) is not None:
    with os.fdopen(os.open(cfg_path + ".tmp", os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600), "w") as f:
        json.dump(cfg, f, indent=2)
    os.replace(cfg_path + ".tmp", cfg_path)
PY
rm -rf "$DATA/cache/addons"  # drop source lists fetched with the old (or no) key
systemctl --user restart tvremote.service 2>/dev/null || echo "Restart the TV controller to apply."
if [ -n "$KEY" ]; then echo "TorBox key saved."; else echo "TorBox key removed."; fi
