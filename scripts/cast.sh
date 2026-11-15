#!/usr/bin/env bash
# Start, stop, restart, check or link Nexus TV's music casting ("Nexus TV" in the YouTube Music cast menu).
# Installed as ~/.local/bin/nexus-tv-cast. "stop" keeps casting off across restarts until "start".
set -euo pipefail

cmd="${1:-status}"
case "$cmd" in start|stop|restart|status|check|code) ;; *) echo "Usage: nexus-tv-cast [start|stop|restart|status|check|code]" >&2; exit 2 ;; esac
# check: why "Nexus TV" might be missing from the cast menu. code: a "Link with TV code" for the YouTube app.
api="$cmd"; [ "$cmd" = code ] && api=pair_code
data="${IMACTV_DATA_DIR:-$HOME/.local/share/imactv}"
token="$(cat "$data/home_token" 2>/dev/null || true)"
[ -n "$token" ] || { echo "No TV token in $data: start Nexus TV once first." >&2; exit 1; }

ask() {
  curl -fsS --max-time 30 -H 'Content-Type: application/json' -H "Cookie: imactv=$token" \
    -d "{\"action\":\"music_cast_service\",\"params\":{\"cmd\":\"$1\"}}" http://127.0.0.1:8765/api/commands 2>/dev/null
}
json="$(ask "$api")" || {
  echo "Nexus TV's controller isn't answering on port 8765 (or it is too old: run ./install.sh)." >&2
  exit 1
}
if [ "$cmd" = code ]; then                      # YouTube takes a few seconds to hand out the first code
  for _ in 1 2 3 4 5 6 7 8 9 10; do
    printf '%s' "$json" | grep -q '"pending": *true' || break
    sleep 1; json="$(ask pair_code)" || break
  done
fi
printf '%s' "$json" | CMD="$cmd" python3 -c '
import json, os, sys
r = json.load(sys.stdin).get("result") or {}
s = r.get("music_cast_service") or {}
cmd = os.environ["CMD"]
text = {"running": "running: \"Nexus TV\" is in the YouTube Music cast menu (port %s)" % s.get("port"),
        "starting": "starting", "off": "off",
        "missing": "not available, missing: " + ", ".join(s.get("missing") or [])}.get(s.get("state"), "unknown")
if cmd == "check":
    for line in r.get("check") or []:
        print({True: "  ok   ", False: "  FAIL ", None: "  note "}[line.get("ok")] + line.get("text", ""))
    sys.exit(0 if all(l.get("ok") is not False for l in r.get("check") or []) else 1)
if cmd == "code":
    p = r.get("pair_code")
    if not p:
        print("Music casting isn\x27t running: " + text); sys.exit(1)
    if p.get("code"):
        print("Link with TV code: " + p["code"])
        print("In the YouTube app: Settings > Watch on TV > Enter TV code. Good for about 5 minutes.")
        sys.exit(0)
    print("No code yet" + (": " + p["error"] if p.get("error") else ", try again in a few seconds.")); sys.exit(1)
print("Music casting:", text)
sys.exit(0 if s.get("state") in ("running", "starting", "off") else 1)'
