# Nexus TV phone app

The Android app for a Nexus TV (`com.nexus.tv`). The remote itself is the TV's own phone page
(`tvapp/web/`) in a WebView; the app adds:

- **TV choice:** every TV it has opened is saved (newest first). When the last TV doesn't answer,
  it shows "Can't reach <TV>" with Try again, your saved TVs, TVs found on this Wi-Fi (it asks
  port 8765 on every address of the phone's /24 for `/api/hello`), Scan the QR code, and Enter
  address. "Switch TV" in the remote's settings opens the same page.
- **QR opens the app:** the TV's QR is `http://<ip>:8765/?pair=<code>`. On Android the TV answers that
  with `web/open-app.html`, which opens `nexustv://pair?host=<ip:port>&code=<code>` (an `intent://`
  link with a browser fallback): the app opens and the TV's page pairs itself with the code; without the
  app, Chrome continues to the web remote. Links only open TVs on private addresses.
- **In-app QR scanner:** its own camera page (`lib/scan_page.dart`, `mobile_scanner` with ML Kit bundled), so it
  works without Play services; it asks for the camera permission the first time.
- **No camera / Tailscale:** Enter address (Wi-Fi or Tailscale `100.x` address), then type the code shown on
  the TV (Settings → Pair phone, which also shows the Tailscale address) or printed by `nexus-tv-pair` on the PC.
- **Android extras:** hardware volume buttons for the TV (also on the lock screen), phone microphone
  and phone speaker (`assets/tv-native.js` adds their switches to the TV's page).

Build: `bash scripts/build-phone-app.sh` (writes `dist/nexus-tv-<version>-android.apk`). It is
signed with this machine's debug key, the same key as earlier Nexus TV builds, so it installs over them.

Tests: `flutter test` here, and `node --test test/tv-native.test.cjs`.

Moved here from the nexusRemote repo (its `tv` flavor) on 2026-11-08; Nexus Remote no longer has a TV page.
