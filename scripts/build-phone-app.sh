#!/usr/bin/env bash
# Build the Nexus TV phone app into dist/nexus-tv-<version>-android.apk (+ SHA256SUMS).
set -euo pipefail
root="$(cd "$(dirname "$0")/.." && pwd)"
flutter=${FLUTTER:-$(command -v flutter || echo "$HOME/flutter/bin/flutter")}
version="$(sed -n 's/^version: \([^+]*\)+.*/\1/p' "$root/mobile/pubspec.yaml")"
cd "$root/mobile"
"$flutter" build apk --release
mkdir -p "$root/dist"
out="nexus-tv-$version-android.apk"
cp build/app/outputs/flutter-apk/app-release.apk "$root/dist/$out"
(cd "$root/dist" && sha256sum "$out" > "nexus-tv-$version-SHA256SUMS.txt")
ls -la "$root/dist/$out"
