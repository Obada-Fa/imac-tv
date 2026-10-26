# iMac TV / Zorin TV 📺📱

> Transform your iMac or PC running **Zorin OS** (or Ubuntu/Debian) into a smart TV experience, complete with an artwork-rich TV Home interface, a responsive mobile web remote, a built-in movie & series catalog played with **mpv**, and **VacuumTube (YouTube Leanback)**.

![TV Home Interface](tvapp/previews/imac-tv-final-live.png)

---

## Features

- 🛋️ **TV Home Interface (`home.py`)**:
  - Fullscreen 10-foot user interface built with GTK3 and CSS styling.
  - Our own catalog UI: browse, search, details, episodes and a source picker for movies and series (Stremio add-on protocol: Cinemeta metadata, Torrentio streams, OpenSubtitles v3).
  - **Continue Watching** rail from local playback history; hero banner with backdrop artwork.
  - Quick-launch for **YouTube (VacuumTube)**.
  - On-screen clock, Wi-Fi status, IP address, and dynamic pairing code / QR code.

- 📱 **Mobile Web Remote Control (`web/` & `server.py`)**:
  - Zero app install needed on your phone — runs directly in any mobile web browser.
  - **D-Pad & Navigation**: Smooth remote navigation (Up, Down, Left, Right, OK, Back, Home).
  - **Touchpad & Mouse Mode**: Fluid pointer trackpad with click, right-click, and two-finger scroll.
  - **State-aware**: the remote switches between browsing (D-pad, Search), buffering (source, peers, speed, Cancel) and a **Now Playing** card (scrub bar, play/pause, ±10/±60 s, subtitle and audio pickers, subtitle delay, Stop).
  - **Search & typing**: tap Search, then use your phone's native keyboard; text is sent to the TV as you type.
  - **System Controls**: Hardware volume, mute, display brightness, screen blanking, sleep timer, and clean power-off.
  - **Security & Pairing**: Pair once using the on-screen 6-digit code or QR code; secure device cookie migration.

- **Settings on TV and phone**:
  - Six categories: Appearance, Ambient, Display, Playback, Connections, and System.
  - On TV, use Up/Down in the category rail, OK/Right to enter controls, and Back to return to the rail. Categories remember focus and scroll position.
  - On the phone, the gear opens Settings; the three-dot button opens Quick actions for timers, playback controls, and power-off.
  - Upload or replace Retro images from the phone under Appearance. Both devices can enable, disable, or remove images.
  - Network-only controls are disabled in Bubbles layout while retaining their saved values. Display changes still require timed Keep/Revert confirmation.

- 🍿 **Playback**:
  - **mpv** plays everything; the headless **stremio-service** engine (`imactv-engine.service`, `127.0.0.1:11470`) streams torrents over HTTP.
  - **Subtitles**: Arabic, Nederlands and English are fetched automatically and always selectable.
  - **No video stays on disk**: the engine cache is capped (3 GB by default) and the torrent is removed when playback stops.
  - **Add-ons** are configurable in `~/.local/share/imactv/media.json` (`addons`, `sub_langs`, `cache_bytes`, `max_size_gb`, `quality_order`).
  - **VacuumTube**: the YouTube Leanback TV UI with ad-blocking and remote navigation.
  - **YouTube video/music mode**: a playing video comes forward fullscreen once per track. Pressing Home keeps that track in the background without minimizing or resuming it; the next video can come forward again. Music stays behind Home, with the visualizer after 30 seconds without input (or the quiet idle setting if shorter).
  - Music detection uses channel/title, artwork and available YouTube category metadata. For unlabelled songs, choose Music on the phone remote; the choice is remembered for the channel, or the title when no channel is available. If media metadata briefly disappears, a mode choice waits up to 15 seconds for it to return.

---

## Installation on Zorin OS

### 1. Clone the Repository

```bash
git clone https://github.com/Obada-Fa/imac-tv.git ~/tvremote
cd ~/tvremote
```

### 2. Run the Installer

Run the automated installer script:

```bash
./install.sh
```

This single command will:
1. Install all necessary system APT packages (`python3-gi`, `gir1.2-gtk-3.0`, `wmctrl`, `xdotool`, `mpv`, Noto and Inter fonts, etc.; Inter is downloaded if the distro lacks it).
2. Set up **VacuumTube** via Flathub / Flatpak.
3. Install **stremio-service** (official `.deb` from GitHub releases, Flatpak `com.stremio.Service` as fallback) and enable `imactv-engine.service`, which runs the bundled `server.js` headless. Use `--skip-engine` to skip, or `--engine-url URL` to use an existing Stremio server (see below).
4. Configure `tvremote.service` as a systemd user service (auto-starts on boot).
5. Configure desktop autostart so **TV Home** launches automatically on login.
6. Set up passwordless permissions for power off and desktop restart.

### Using an existing Stremio server

The engine **is** Stremio's streaming server. Torrentio, Cinemeta and OpenSubtitles are queried directly by the app (defaults in `tvapp/media/config.py`), not taken from your Stremio account, so only the streaming server is needed.

To use a server on another machine instead of installing one locally:

```bash
./install.sh --engine-url http://HOST:11470
# or just the engine step:
scripts/install-engine.sh http://HOST:11470
```

or write `{"engine": "http://HOST:11470"}` into `~/.local/share/imactv/media.json` by hand and restart the controller.

- The server must be reachable from the TV machine on port 11470 (LAN or Tailscale). Stremio's server binds `0.0.0.0:11470` by default.
- For a remote engine the app does not push its cache cap (`cache_bytes`) onto it; that cap only applies to a local engine.

---

## App Installation Options

The installer defaults to Flatpak, which is the official and most reliable distribution method on Zorin OS. Additional options are available:

### Option A: Flatpak (Default & Recommended)
```bash
./install.sh --flatpak-apps
```
Installs `rocks.shy.VacuumTube` from Flathub.

### Option B: Native `.deb` Packages
```bash
./install.sh --deb-apps
```
Or directly run:
```bash
bash scripts/install-deb-apps.sh
```
Downloads the official VacuumTube `.deb` from GitHub and installs it with `apt`.

### Option C: Build VacuumTube from Source
```bash
./install.sh --build-vacuumtube
```
Or directly run:
```bash
bash scripts/build-vacuumtube.sh
```
Clones `https://github.com/shy1132/VacuumTube.git`, installs Node.js/npm dependencies, and builds the Linux `.deb` and `.AppImage` packages using `electron-builder`.

---

## How to Pair Your Phone Remote

1. Ensure your phone is connected to the **same Wi-Fi network** as the TV.
2. Look at the TV Home screen: you will see your machine's IP address (e.g., `http://192.168.1.100:8765`) and a 6-digit pairing code.
3. On your phone, open the URL or click **"Pair phone / QR"** on the TV to scan the QR code.
4. Enter the 6-digit pairing code on your phone when prompted.
5. Your phone is now paired! The remote interface will load instantly.

```
┌──────────────────────────────────────────────┐
│  TV HOME SCREEN                              │
│  LAN: http://192.168.1.100:8765   PIN: 580708│
└──────────────────────────────────────────────┘
                       ▲
                       │ Pair via Browser / QR
                       ▼
┌──────────────────────────────────────────────┐
│  PHONE REMOTE INTERFACE                      │
│  [ Home ] [ YouTube ]   [ Search ]           │
│  [ ▲ ] [ ◀ ] [ OK ] [ ▶ ] [ ▼ ]              │
│  Now Playing · scrub · subtitles · audio     │
└──────────────────────────────────────────────┘
```

---

## Project Structure

```
├── install.sh                  # Complete automated installer
├── uninstall.sh                # Clean uninstallation script
├── scripts/
│   ├── install-apps.sh         # Installs VacuumTube via Flatpak
│   ├── install-engine.sh       # stremio-service + imactv-engine.service
│   ├── install-deb-apps.sh     # Downloads & installs native .deb packages
│   └── build-vacuumtube.sh     # Builds VacuumTube from source (electron-builder)
├── tvapp/
│   ├── server.py               # TV Controller HTTP server & X11 input injector
│   ├── home.py                 # Fullscreen GTK3 TV Home interface
│   ├── media/                  # Add-on client, source ranking, subtitles, engine, mpv launcher
│   ├── feed_sync.py            # Legacy recommendations syncer
│   ├── web/                    # Mobile web remote UI (HTML, CSS, JS)
│   ├── assets/                 # App icons and preview artwork
│   ├── deploy/                 # Systemd service, sudoers, and smoke test scripts
│   ├── previews/               # UI preview screenshots
│   └── tests/                  # Automated unit test suite
├── imac_eq.py                  # PulseAudio / LADSPA DSP equalizer (for iMac built-in speakers)
└── remote.py                   # Standalone legacy X11 remote server
```

---

## Testing & Verification

Run the automated test suite:

```bash
cd tvapp
python3 -m unittest discover -s tests -v
```

All controller, network parsing, and pairing security tests should pass.

---

## Uninstallation

To remove the TV controller and engine services, autostart entries, and sudoers rules:

```bash
./uninstall.sh
```
*(VacuumTube, mpv and stremio-service packages are kept).*


### Appearance and lighter rendering

TV Settings → Appearance (also available in the phone Settings tab) includes:
- **Transparent surfaces:** turn off for solid menu panels and Home tiles.
- **Visual effects:** turn off decorative Home motion, photo crossfades, ambient
  shapes/visualizers, and menu shadows/transitions. Focus remains visible and the
  playback spinner still communicates activity. Your saved ambient choices are
  retained when effects are turned back on.

Settings, movie/series browsing, details, sources and stream startup share the
Home-inspired midnight-blue palette, rounded surfaces and pale-blue focus accent.
Preferences persist in `home.json` and apply without restarting.
