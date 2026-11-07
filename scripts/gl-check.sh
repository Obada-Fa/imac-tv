#!/usr/bin/env bash
# Run ON THE TV (it draws on the real screen for ~20 s): does the GPU visualizer run smoothly and cheaply here?
# Prints the GL renderer Nexus TV detected, then shows the visualizer full screen with fake music and reports
# frames per second, its own CPU, Xorg's CPU and the Intel GPU clock. Press nothing; it ends by itself.
# usage: scripts/gl-check.sh [seconds=20]
set -u
HERE=$(cd "$(dirname "$0")/.." && pwd)
SECS=${1:-20}
export DISPLAY=${DISPLAY:-:0}
cd "$HERE/tvapp" || exit 1
echo "== detection"
python3 -c 'import gpu, json; r = gpu.probe(); print(json.dumps(r)); print("GPU visualizer by default:", "yes" if r["hw"] else "no (cairo)")'
freq() { for f in /sys/class/drm/card*/gt_act_freq_mhz; do [ -r "$f" ] && { cat "$f"; return; }; done; echo "?"; }
echo "== GPU clock before: $(freq) MHz"
( sleep $((SECS / 2)); echo "== GPU clock while drawing: $(freq) MHz" ) &
echo "== drawing for ${SECS}s"
python3 gl_ambient.py --demo "$SECS"
wait
