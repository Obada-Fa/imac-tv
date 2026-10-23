#!/usr/bin/env bash
# Benchmark the ambient-Home music visualizer under a private Xvfb (Xvfb CPU ~ Xorg CPU on the TV).
# usage: scripts/viz-bench.sh [tvapp_dir=tvapp] [secs_per_mode=30] [mode ...=edgeflow ridge constellation terrain aurora prism]
# env:   SHOT_DIR=<dir> keeps one screenshot per mode (default: discarded with the scratch dir)
# Never touches DISPLAY :0 or ~/.local/share/imactv. Kills only the PIDs it started.
set -u
HERE=$(cd "$(dirname "$0")" && pwd)
TVAPP=$(cd "${1:-$HERE/../tvapp}" && pwd) || exit 1
SECS=${2:-30}
shift 2 2>/dev/null || shift $# 2>/dev/null
MODES=("$@")
[ ${#MODES[@]} -gt 0 ] || MODES=(edgeflow ridge constellation terrain aurora prism)
HELPER="$HERE/viz_bench.py"
ROOT=$(mktemp -d /tmp/viz-bench.XXXXXX)
PIDS=()

cleanup() {
  local p
  for p in "${PIDS[@]}"; do kill -TERM "$p" 2>/dev/null; done
  sleep 1
  for p in "${PIDS[@]}"; do kill -KILL "$p" 2>/dev/null; done
  # strays the app spawned (parec, dbus-daemon, ...): only those whose environment names our scratch dir
  for e in /proc/[0-9]*/environ; do
    if { tr '\0' '\n' <"$e"; } 2>/dev/null | grep -qF "IMACTV_DATA_DIR=$ROOT/"; then
      p=${e#/proc/}; p=${p%/environ}; kill -KILL "$p" 2>/dev/null
    fi
  done
  rm -rf "$ROOT"
}
trap cleanup EXIT
trap 'exit 130' INT TERM

free_display() { local n=90; while [ -e "/tmp/.X11-unix/X$n" ] || [ -e "/tmp/.X$n-lock" ]; do n=$((n+1)); done; echo "$n"; }
free_port() { python3 -c 'import socket;s=socket.socket();s.bind(("127.0.0.1",0));print(s.getsockname()[1])'; }

echo "viz-bench: $TVAPP  ${SECS}s/mode  modes: ${MODES[*]}"
echo "mode           | python % | Xvfb %  | total % | draw ms | area % | fps"
echo "---------------|----------|---------|---------|---------|--------|------"
ROWS=""
for mode in "${MODES[@]}"; do
  D=$ROOT/$mode; mkdir -p "$D/data"
  printf '{"ambient": {"idle": 10, "idle_music": 10, "interval": 3600}}\n' >"$D/data/home.json"
  N=$(free_display)
  Xvfb ":$N" -screen 0 1920x1080x24 -nolisten tcp >"$D/xvfb.log" 2>&1 &
  XPID=$!; PIDS+=("$XPID")
  for _ in $(seq 50); do [ -e "/tmp/.X11-unix/X$N" ] && break; sleep 0.1; done
  env DISPLAY=":$N" IMACTV_DISPLAY=":$N" IMACTV_DATA_DIR="$D/data" IMACTV_MPV_SOCK="$D/mpv.sock" \
      IMACTV_PORT="$(free_port)" IMACTV_FAKE_LEVELS=1 IMACTV_VIZ_MODES="$mode" IMACTV_VIZ_EVERY=1 \
      IMACTV_PERF_LOG="$D/perf.jsonl" IMACTV_PERF_XPID="$XPID" \
      python3 "$TVAPP/home.py" >"$D/home.log" 2>&1 &
  PPID_=$!; PIDS+=("$PPID_")
  # wait for the settled visualizer phase
  ok=0
  for _ in $(seq 120); do
    kill -0 "$PPID_" 2>/dev/null || break
    if grep -q '"phase": "viz"' "$D/perf.jsonl" 2>/dev/null; then
      if [ "$mode" = ridge ] || grep -q "\"mode\": \"$mode\"" "$D/perf.jsonl" 2>/dev/null; then
        ok=1; break
      fi
    fi
    sleep 0.5
  done
  if [ $ok = 0 ]; then echo "$mode: visualizer never reached (see below)"; tail -5 "$D/home.log"; cleanup_one=1
    for p in "$PPID_" "$XPID"; do kill -TERM "$p" 2>/dev/null; done; continue; fi
  sleep 5   # warm up
  T0=$(python3 "$HELPER" now "$D/perf.jsonl"); C0=$(python3 "$HELPER" cpu "$PPID_" "$XPID"); W0=$(date +%s.%N)
  sleep $((SECS/2))
  [ -n "${SHOT_DIR:-}" ] && { mkdir -p "$SHOT_DIR"; DISPLAY=":$N" import -window root "$SHOT_DIR/viz-$mode.png" 2>/dev/null; }
  sleep $((SECS - SECS/2))
  C1=$(python3 "$HELPER" cpu "$PPID_" "$XPID"); W1=$(date +%s.%N); T1=$(python3 "$HELPER" now "$D/perf.jsonl")
  read -r P0 X0 <<<"$C0"; read -r P1 X1 <<<"$C1"
  WALL=$(python3 -c "print($W1-$W0)")
  python3 "$HELPER" row "$TVAPP" "$D/perf.jsonl" "$T0" "$T1" "$mode" \
      "$(python3 -c "print($P1-$P0)")" "$(python3 -c "print($X1-$X0)")" "$WALL" | tee -a "$ROOT/rows"
  for p in "$PPID_" "$XPID"; do kill -TERM "$p" 2>/dev/null; done
  sleep 1
done
