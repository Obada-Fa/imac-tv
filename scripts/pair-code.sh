#!/usr/bin/env bash
# Print the Nexus TV pairing code and the addresses a phone can use (Wi-Fi and Tailscale).
# Installed as ~/.local/bin/nexus-tv-pair. Works over SSH too: the code is only given to this PC itself.
set -euo pipefail

json="$(curl -fsS --max-time 3 http://127.0.0.1:8765/api/pair-status 2>/dev/null)" || {
  echo "Nexus TV's phone server isn't answering on port 8765. Is the TV app running?" >&2
  exit 1
}
code="$(printf '%s' "$json" | python3 -c 'import json,sys; print(json.load(sys.stdin).get("code",""))')"
[ -n "$code" ] || { echo "The TV didn't give a code (this TV app may be too old: run ./install.sh)." >&2; exit 1; }

lan="$(ip route get 1.1.1.1 2>/dev/null | sed -n 's/.* src \([0-9.]*\).*/\1/p' | head -n1)"
ts="$(ip -4 -o addr show 2>/dev/null | awk '{split($4,a,"/"); split(a[1],o,".");
      if (o[1]==100 && o[2]>=64 && o[2]<=127) {print a[1]; exit}}')"

printf 'Pairing code: %s %s   (valid for 5 minutes, once)\n' "${code:0:3}" "${code:3:3}"
[ -n "$lan" ] && printf 'Wi-Fi:        %s:8765\n' "$lan"
[ -n "$ts" ] && printf 'Tailscale:    %s:8765\n' "$ts"
printf '\nIn the Nexus TV app: Enter address, then type the code on the page that opens.\n'
