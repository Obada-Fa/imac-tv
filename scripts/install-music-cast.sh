#!/usr/bin/env bash
# Music casting ("Nexus TV" in the YouTube / YouTube Music cast menu, sound only): Node.js 20.10+, yt-dlp and
# the receiver's npm packages. No sudo needed: missing pieces go under ~/.local/share/imactv.
# Never fatal: without these the TV works as before, just without music casting.
# env: MUSIC_DIR (default tvapp/music_cast next to this script), IMACTV_LOCAL (default ~/.local/share/imactv)
set -uo pipefail
HERE=$(cd "$(dirname "$0")" && pwd)
MUSIC_DIR=${MUSIC_DIR:-$HERE/../tvapp/music_cast}
LOCAL=${IMACTV_LOCAL:-$HOME/.local/share/imactv}
NODE_LINE=${NODE_LINE:-v22}            # an LTS line; a Node 20.10+ already on the system is used as is
warn() { printf '\033[1;33m%s\033[0m\n' "$*"; }
ok() { printf '\033[0;32m✓ %s\033[0m\n' "$*"; }

# Node 20.10+: the receiver's youtubei.js uses import attributes, which Node 18.19 (Zorin's) can't parse
node_ok() {
  local v; v=$("$1" --version 2>/dev/null | sed -n 's/^v\([0-9]*\)\.\([0-9]*\).*/\1 \2/p')
  [ -n "$v" ] || return 1
  set -- $v
  [ "$1" -gt 20 ] || { [ "$1" -eq 20 ] && [ "$2" -ge 10 ]; }
}

# ---- Node.js 20.10+ with npm
NODE=""
NPM=""
if [ -x "$LOCAL/node/bin/node" ] && node_ok "$LOCAL/node/bin/node"; then
  NODE=$LOCAL/node/bin/node; NPM=$LOCAL/node/bin/npm
elif command -v node >/dev/null && node_ok "$(command -v node)" && command -v npm >/dev/null; then
  NODE=$(command -v node); NPM=$(command -v npm)
elif [ "$(uname -m)" = x86_64 ]; then
  printf 'Downloading Node.js (%s LTS) for music casting...\n' "$NODE_LINE"
  BASE="https://nodejs.org/dist/latest-${NODE_LINE}.x"
  TMP=$(mktemp -d)
  NAME=$(curl -fsSL --max-time 30 "$BASE/SHASUMS256.txt" 2>/dev/null | tee "$TMP/SUMS" | grep -o 'node-v[0-9.]*-linux-x64\.tar\.xz' | head -1)
  if [ -n "$NAME" ] && curl -fsSL --max-time 600 -o "$TMP/$NAME" "$BASE/$NAME" \
     && (cd "$TMP" && grep " $NAME\$" SUMS | sha256sum -c --status) \
     && tar -xJf "$TMP/$NAME" -C "$TMP"; then
    rm -rf "$LOCAL/node" && mkdir -p "$LOCAL" && mv "$TMP/${NAME%.tar.xz}" "$LOCAL/node"
    NODE=$LOCAL/node/bin/node; NPM=$LOCAL/node/bin/npm
  else
    warn "Could not download Node.js; music casting stays off."
  fi
  rm -rf "$TMP"
else
  warn "No Node.js 20.10+ for $(uname -m); music casting stays off. Install nodejs 20.10+ and run this again."
fi
[ -n "$NODE" ] && ok "Node.js $("$NODE" --version)"

# ---- yt-dlp: the official build (bundles what YouTube's player challenge needs), kept current
YTDLP=$LOCAL/bin/yt-dlp
if [ -x "$YTDLP" ]; then
  "$YTDLP" -U >/dev/null 2>&1 || true
elif [ "$(uname -m)" = x86_64 ]; then
  printf 'Downloading yt-dlp for music casting...\n'
  REL=https://github.com/yt-dlp/yt-dlp/releases/latest/download
  TMP=$(mktemp -d)
  if curl -fsSL --max-time 600 -o "$TMP/yt-dlp_linux" "$REL/yt-dlp_linux" \
     && curl -fsSL --max-time 30 -o "$TMP/SUMS" "$REL/SHA2-256SUMS" \
     && (cd "$TMP" && grep ' yt-dlp_linux$' SUMS | sha256sum -c --status); then
    mkdir -p "$LOCAL/bin" && install -m 755 "$TMP/yt-dlp_linux" "$YTDLP"
  else
    warn "Could not download yt-dlp; casting uses any yt-dlp already installed."
  fi
  rm -rf "$TMP"
fi
[ -x "$YTDLP" ] && ok "yt-dlp $("$YTDLP" --version 2>/dev/null)"

# ---- the receiver's packages (exact versions from package-lock.json, no install scripts)
if [ -n "$NODE" ] && [ -n "$NPM" ] && [ -f "$MUSIC_DIR/package-lock.json" ]; then
  if (cd "$MUSIC_DIR" && PATH="$(dirname "$NODE"):$PATH" "$NPM" ci --omit=dev --ignore-scripts --no-audit --no-fund --loglevel=error) \
     && (cd "$MUSIC_DIR" && "$NODE" -e "import('./receiver.mjs').then(() => process.exit(0), (e) => { console.error(e.message); process.exit(1); })"); then
    ok "Music casting ready: cast to \"Nexus TV\" from the YouTube Music app"
  else
    warn "npm could not install the music receiver; music casting stays off."
  fi
fi
command -v mpv >/dev/null || warn "mpv is missing (sudo apt install mpv); music casting needs it."
exit 0
