#!/usr/bin/env bash
# Start, stop, restart or check Nexus TV's music casting ("Nexus TV" in the YouTube Music cast menu).
# Installed as ~/.local/bin/nexus-tv-cast. "stop" keeps casting off across restarts until "start".
set -euo pipefail

cmd="${1:-status}"
case "$cmd" in start|stop|restart|status) ;; *) echo "Usage: nexus-tv-cast [start|stop|restart|status]" >&2; exit 2 ;; esac
data="${IMACTV_DATA_DIR:-$HOME/.local/share/imactv}"
token="$(cat "$data/home_token" 2>/dev/null || true)"
[ -n "$token" ] || { echo "No TV token in $data: start Nexus TV once first." >&2; exit 1; }

json="$(curl -fsS --max-time 20 -H 'Content-Type: application/json' -H "Cookie: imactv=$token" \
  -d "{\"action\":\"music_cast_service\",\"params\":{\"cmd\":\"$cmd\"}}" http://127.0.0.1:8765/api/commands 2>/dev/null)" || {
  echo "Nexus TV's controller isn't answering on port 8765 (or it is too old: run ./install.sh)." >&2
  exit 1
}
printf '%s' "$json" | python3 -c '
import json, sys
s = (json.load(sys.stdin).get("result") or {}).get("music_cast_service") or {}
text = {"running": "running: \"Nexus TV\" is in the YouTube Music cast menu (port %s)" % s.get("port"),
        "starting": "starting", "off": "off",
        "missing": "not available, missing: " + ", ".join(s.get("missing") or [])}.get(s.get("state"), "unknown")
print("Music casting:", text)
sys.exit(0 if s.get("state") in ("running", "starting", "off") else 1)'
