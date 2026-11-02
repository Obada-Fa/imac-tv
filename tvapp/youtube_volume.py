"""Keep YouTube at full level, so only the system volume sets how loud it is.

VacuumTube applies its own saved volume to the video every 100 ms. Its page shares the JavaScript
world with that code, so through a local debugging port we make the video element ignore the level
and the mute (muting from the phone's YouTube app would otherwise silence it for good) and hide
VacuumTube's volume bar. The player still reads back what it set, so it doesn't fight this."""

import json
import os
from pathlib import Path
import urllib.request

import browser

PORT = int(os.environ.get("IMACTV_YOUTUBE_PORT", "9334"))
FLAG = f"--remote-debugging-port={PORT}"
_opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
LOCK_LEVEL = """(() => {
  // Each property is locked once per page; pages locked before mute was added still get it.
  const lock = (name, flag, forced) => {
    if (window[flag]) return false;
    window[flag] = true;
    const real = Object.getOwnPropertyDescriptor(HTMLMediaElement.prototype, name);
    const asked = new WeakMap();
    Object.defineProperty(HTMLMediaElement.prototype, name, {
      configurable: true, enumerable: real.enumerable,
      get() { return asked.has(this) ? asked.get(this) : real.get.call(this); },
      set(value) { asked.set(this, value); real.set.call(this, forced); },
    });
    document.querySelectorAll("video, audio").forEach((media) => {
      asked.set(media, real.get.call(media));
      real.set.call(media, forced);
    });
    return true;
  };
  const level = lock("volume", "__imactvVolumeLocked", 1);
  const mute = lock("muted", "__imactvMuteLocked", false);
  if (level) {
    const style = document.createElement("style");
    style.textContent = "#vt-volume-indicator { display: none !important; }";
    document.documentElement.appendChild(style);
  }
  return level || mute;
})()"""


def ensure_flag(home=None):
    """VacuumTube also reads switches from flags.txt, so the port is open however it gets started."""
    home = Path(home) if home else Path.home()
    for folder in (home / ".config/VacuumTube", home / ".var/app/rocks.shy.VacuumTube/config/VacuumTube"):
        path = folder / "flags.txt"
        try:
            if not folder.is_dir():
                continue
            text = path.read_text() if path.exists() else ""
            if "remote-debugging-port" not in text:
                path.write_text((text.rstrip() + " " + FLAG).strip() + "\n")
        except OSError:
            continue


def reachable(timeout=0.5):
    """Whether a running YouTube has the port open; one started by an older version doesn't."""
    try:
        with _opener.open(f"http://127.0.0.1:{PORT}/json/version", timeout=timeout):
            return True
    except (OSError, ValueError):
        return False


def enforce(timeout=0.5):
    """Lock the level in every open YouTube page. Does nothing while YouTube isn't running."""
    try:
        with _opener.open(f"http://127.0.0.1:{PORT}/json/list", timeout=timeout) as response:
            targets = json.loads(response.read().decode("utf-8", "replace"))
        for target in targets:
            if target.get("type") in ("page", "iframe"):
                browser._page_command(target["id"], "Runtime.evaluate", {"expression": LOCK_LEVEL}, timeout, PORT)
    except (OSError, ValueError, KeyError):
        pass
