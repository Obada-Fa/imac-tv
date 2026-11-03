"""Apps system: what is installed on the PC, which of it is on Home, and how to launch and match it.

Everything here is plain Python (no GTK), so the controller process and the TV process can both import it.
Items are `{"id", "name", "kind", "favorite", "icon", "builtin_kind"}`; kind is "screen" (movies, series,
twitch), "app" (youtube, discord) or "desktop" (id "desktop:<desktop file name>"). Favorites live in
home.json under "favorites"; "apps_rev" there changes whenever they do, so other processes notice.

Only ids found by the scan are ever launched: clients never send a command line."""
import hashlib
import json
import os
import shlex
import shutil
import subprocess
import threading
import tempfile
import time
from pathlib import Path
from collections import OrderedDict
from urllib.parse import quote

import browser

MAX_FAVORITES = 6
SCAN_SECONDS = 60
DEFAULT_FAVORITES = ["movies", "series", "youtube", "twitch"]
FULL_ERROR = "Home is full: remove a favorite first"
ICON_SIZE = 256

BUILTINS = [
    ("movies", "Movies", "screen"),
    ("series", "Series", "screen"),
    ("twitch", "Twitch", "screen"),
    ("youtube", "YouTube", "app"),
    ("discord", "Discord", "app"),
    ("calculator", "Calculator", "screen"),
]
# Desktop entries for things the TV already offers as built-in tiles
_BUILTIN_ENTRIES = {"rocks.shy.vacuumtube", "vacuumtube", "com.discordapp.discord", "discord"}
# Exec basenames that say nothing about the window class
_GENERIC_EXEC = {"env", "flatpak", "sh", "bash", "snap", "python3", "python", "gtk-launch", "xdg-open", "sudo"}

_lock = threading.Lock()
_scan = {"time": 0.0, "key": None, "entries": {}}
_builtin_cache = {"time": 0.0, "have": {}}
_icon_sources = OrderedDict()
ICON_SOURCE_LIMIT = 128
ICON_SOURCE_TTL = 60


def data_dir():
    return Path(os.environ.get("IMACTV_DATA_DIR", str(Path.home() / ".local/share/imactv")))


def _home_path():
    return data_dir() / "home.json"


def _read_home():
    try:
        raw = json.loads(_home_path().read_text())
    except (OSError, ValueError):
        return {}
    return raw if isinstance(raw, dict) else {}


# ---- .desktop scanning ------------------------------------------------------------------------------------

def scan_dirs():
    override = os.environ.get("IMACTV_APPS_DIRS")
    if override is not None:
        return [Path(p) for p in override.split(":") if p]
    home = Path.home()
    data_dirs = os.environ.get("XDG_DATA_DIRS") or "/usr/local/share:/usr/share"
    dirs = [home / ".local/share/applications"]
    dirs += [Path(p) / "applications" for p in data_dirs.split(":") if p]
    dirs += [home / ".local/share/flatpak/exports/share/applications",
             Path("/var/lib/flatpak/exports/share/applications"),
             Path("/var/lib/snapd/desktop/applications")]
    seen, out = set(), []
    for d in dirs:
        if d not in seen:
            seen.add(d)
            out.append(d)
    return out


def parse_desktop(text):
    """The [Desktop Entry] group of a .desktop file as {key: value} (localized keys dropped)."""
    entry, inside = {}, False
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("["):
            inside = line == "[Desktop Entry]"
            continue
        if not inside or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        if "[" in key:
            continue
        entry.setdefault(key, value.strip())
    return entry


def _flag(value):
    return str(value).strip().lower() == "true"


def exec_argv(line):
    """Exec= line -> argv. Field codes (%u, %F, @@u ...) are dropped, %% becomes %; no shell is involved."""
    try:
        parts = shlex.split(line or "")
    except ValueError:
        return []
    argv, skip = [], False
    for part in parts:
        if part in ("@@", "@@u"):
            skip = not skip         # flatpak's "@@u ... @@" brackets the file arguments
            continue
        if skip:
            continue
        if len(part) == 2 and part[0] == "%" and part[1].isalpha():
            continue
        argv.append(part.replace("%%", "%"))
    return argv


def _have_exec(name):
    if not name:
        return True
    if "/" in name:
        return os.access(name, os.X_OK)
    return shutil.which(name) is not None


def _entry(path, did):
    try:
        data = parse_desktop(path.read_text(errors="replace"))
    except OSError:
        return None
    if data.get("Type", "Application") != "Application":
        return None
    if _flag(data.get("NoDisplay")) or _flag(data.get("Hidden")) or _flag(data.get("Terminal")):
        return None
    if not _have_exec(data.get("TryExec")):
        return None
    argv = exec_argv(data.get("Exec", ""))
    name = data.get("Name")
    if not argv or not name:
        return None
    if did.lower() in _BUILTIN_ENTRIES or os.path.basename(argv[0]).lower() in ("vacuumtube", "discord"):
        return None
    return {"id": "desktop:" + did, "name": name, "kind": "desktop", "icon_name": data.get("Icon", ""),
            "argv": argv, "wmclass": data.get("StartupWMClass", ""), "desktop_id": did}


def scan(force=False):
    """{id: entry} for the installed apps; cached for SCAN_SECONDS. Earlier directories win."""
    key = (tuple(str(d) for d in scan_dirs()),)
    with _lock:
        if not force and _scan["key"] == key and time.monotonic() - _scan["time"] < SCAN_SECONDS:
            return _scan["entries"]
    found = {}
    for base in scan_dirs():
        if not base.is_dir():
            continue
        for root, _dirs, files in os.walk(base):
            for fname in sorted(files):
                if not fname.endswith(".desktop"):
                    continue
                rel = Path(root, fname).relative_to(base).with_suffix("")
                did = "-".join(rel.parts)
                if "desktop:" + did in found:
                    continue
                entry = _entry(Path(root, fname), did)
                if entry:
                    found["desktop:" + did] = entry
    with _lock:
        _scan.update(time=time.monotonic(), key=key, entries=found)
    return found


def refresh():
    """Drop the cached scan and builtin availability (tests, and after an install)."""
    with _lock:
        _scan.update(time=0.0, key=None, entries={})
        _builtin_cache.update(time=0.0, have={})
        _icon_sources.clear()


def _builtin_have():
    """{"youtube": bool, "discord": bool}. IMACTV_APPS_BUILTINS (comma list) fixes it for tests."""
    forced = os.environ.get("IMACTV_APPS_BUILTINS")
    if forced is not None:
        names = {n.strip() for n in forced.split(",")}
        return {"youtube": "youtube" in names, "discord": "discord" in names}
    if os.environ.get("IMACTV_APPS_DIRS") is not None:
        return {"youtube": True, "discord": True}
    with _lock:
        if time.monotonic() - _builtin_cache["time"] < SCAN_SECONDS and _builtin_cache["have"]:
            return _builtin_cache["have"]

    def flatpak(app_id):
        if not shutil.which("flatpak"):
            return False
        try:
            return subprocess.run(["flatpak", "info", app_id], capture_output=True, timeout=3).returncode == 0
        except (OSError, subprocess.SubprocessError):
            return False
    yt = bool(shutil.which("vacuumtube") or shutil.which("VacuumTube") or os.access("/opt/VacuumTube/vacuumtube", os.X_OK)
              or flatpak("rocks.shy.VacuumTube"))
    dc = bool(shutil.which("discord") or flatpak("com.discordapp.Discord"))
    have = {"youtube": yt, "discord": dc}
    with _lock:
        _builtin_cache.update(time=time.monotonic(), have=have)
    return have


# ---- items ------------------------------------------------------------------------------------------------

def _catalog():
    """{id: item without 'favorite'} for everything launchable on this PC, in a stable order."""
    have = _builtin_have()
    out = {}
    for bid, name, kind in BUILTINS:
        if kind == "app" and not have.get(bid):
            continue
        out[bid] = {"id": bid, "name": name, "kind": kind, "icon": None, "builtin_kind": bid}
    for did, e in scan().items():
        out[did] = {"id": did, "name": e["name"], "kind": "desktop",
                    "icon": "/api/apps/icon?id=" + quote(did, safe=""), "builtin_kind": None}
    return out


def _stored_favorites():
    raw = _read_home().get("favorites")
    if isinstance(raw, list):
        return [x for x in raw if isinstance(x, str)]
    return list(DEFAULT_FAVORITES)


def _favorite_ids(catalog):
    """Visible favorites, oldest first, de-duplicated, at most MAX_FAVORITES."""
    out = []
    for fid in _stored_favorites():
        if fid in catalog and fid not in out:
            out.append(fid)
    return out[:MAX_FAVORITES]


def list_apps():
    catalog = _catalog()
    favs = _favorite_ids(catalog)
    items = [dict(catalog[f], favorite=True) for f in favs]
    rest = sorted((i for i in catalog.values() if i["id"] not in favs), key=lambda i: (i["name"].casefold(), i["id"]))
    return items + [dict(i, favorite=False) for i in rest]


def _provider(did):
    """Recognize supported utility desktop IDs without parsing unrelated entries."""
    name = did.lower().removeprefix("desktop:")
    if name in ("brave", "brave-browser", "brave-browser-stable", "com.brave.browser"):
        return "brave"
    if name in ("rustdesk", "org.rustdesk.rustdesk"):
        return "rustdesk"
    return None


def _scoped_catalog():
    """Resolve only known shortcuts and saved desktop IDs, without building the full app catalog."""
    have = _builtin_have()
    out = {bid: {"id": bid, "name": name, "kind": kind, "icon": None, "builtin_kind": bid}
           for bid, name, kind in BUILTINS if kind != "app" or have.get(bid)}
    wanted = {i for i in _stored_favorites() if i.startswith("desktop:")}
    candidates = wanted | {"desktop:" + name for name in (
        "brave", "brave-browser", "brave-browser-stable", "com.brave.Browser",
        "rustdesk", "org.rustdesk.RustDesk")}
    # Standard XDG/Flatpak exports put these entries directly in applications/.
    for app_id in sorted(candidates):
        did = app_id[len("desktop:"):]
        if not did or "/" in did or "\\" in did or did in (".", ".."):
            continue
        for base in scan_dirs():
            entry = _entry(base / (did + ".desktop"), did)
            if entry:
                out[app_id] = {"id": app_id, "name": entry["name"], "kind": "desktop",
                               "icon": "/api/apps/icon?id=" + quote(app_id, safe=""), "builtin_kind": None}
                break
    # Desktop IDs can encode subdirectories; use the legacy scanner only for
    # saved IDs not resolved above, preserving existing pins in that case.
    missing = wanted - out.keys()
    if missing:
        for app_id, entry in scan().items():
            if app_id in missing:
                out[app_id] = {"id": app_id, "name": entry["name"], "kind": "desktop",
                               "icon": "/api/apps/icon?id=" + quote(app_id, safe=""), "builtin_kind": None}
    for app_id, item in out.items():
        provider = _provider(app_id)
        if provider == "brave":
            item.update(name="Web Browser", description="Browse the web with Brave")
        elif provider == "rustdesk":
            item.update(name="Remote Desktop", description="Connect with RustDesk")
        elif app_id == "calculator":
            item.update(description="Calculate using your remote")
    return out


def home_apps():
    catalog = _scoped_catalog()
    return [dict(catalog[f], favorite=True) for f in _favorite_ids(catalog)]


def shortcut_candidates():
    """Entertainment, supported utilities and saved pins, in stable order."""
    catalog = _scoped_catalog()
    favs = _favorite_ids(catalog)
    ids = list(favs)
    for app_id in DEFAULT_FAVORITES:
        if app_id in catalog and app_id not in ids:
            ids.append(app_id)
    for provider in ("brave", "rustdesk"):
        matches = [i for i in sorted(catalog) if _provider(i) == provider]
        if matches and not any(_provider(i) == provider for i in ids):
            ids.append(matches[0])
    if "calculator" not in ids:
        ids.append("calculator")
    return [dict(catalog[i], favorite=i in favs) for i in ids]


def others_apps():
    """Supported utilities not already pinned on Home."""
    return [i for i in shortcut_candidates() if not i["favorite"] and
            (i["id"] == "calculator" or _provider(i["id"]))]


def lookup(app_id):
    """The scan entry (argv, wmclass ...) for a desktop id, else None."""
    return scan().get(app_id) if isinstance(app_id, str) else None


def item(app_id):
    return _catalog().get(app_id) if isinstance(app_id, str) else None


def apps_rev():
    rev = _read_home().get("apps_rev")
    return rev if isinstance(rev, int) and not isinstance(rev, bool) else 0


def set_favorite(app_id, favorite):
    """Star or unstar. ValueError for an unknown id or a full Home; returns list_apps()."""
    from widgets import save_keys
    if not isinstance(favorite, bool):
        raise ValueError("favorite must be true or false")
    catalog = _catalog()
    stored = _stored_favorites()
    if app_id not in catalog and not (not favorite and app_id in stored):
        raise ValueError("Unknown app")
    visible = _favorite_ids(catalog)
    if favorite:
        if app_id in visible:
            return list_apps()
        if len(visible) >= MAX_FAVORITES:
            raise ValueError(FULL_ERROR)
        new = [f for f in stored if f != app_id] + [app_id]
    else:
        if app_id not in stored:
            return list_apps()
        new = [f for f in stored if f != app_id]
    save_keys(data_dir(), {"favorites": new, "apps_rev": apps_rev() + 1})
    return list_apps()


# ---- windows ----------------------------------------------------------------------------------------------

def window_hints(app_id):
    """Lowercase names a window class may carry: StartupWMClass, the desktop id, the Exec basename."""
    e = lookup(app_id)
    if not e:
        return []
    if browser.is_brave_argv(e["argv"]):    # the TV's own Brave only: a normal Brave window is someone else's
        return [browser.WM_CLASS.lower()]
    hints = []
    for h in (e["wmclass"], e["desktop_id"], e["desktop_id"].rsplit(".", 1)[-1], os.path.basename(e["argv"][0])):
        h = (h or "").lower()
        if h and h not in hints and h not in _GENERIC_EXEC:
            hints.append(h)
    return hints


def matches_window(app_id, wm_class):
    """True when a wmctrl class ("instance.Class", lowercased) belongs to this app."""
    wm_class = (wm_class or "").lower()
    pieces = {wm_class, *wm_class.split(".")}
    return any(h in pieces for h in window_hints(app_id))


def launch_argv(app_id):
    e = lookup(app_id)
    if not e:
        return None
    return browser.tv_argv(e["argv"]) if browser.is_brave_argv(e["argv"]) else list(e["argv"])


def is_tv_browser(app_id):
    e = lookup(app_id) if isinstance(app_id, str) and app_id.startswith("desktop:") else None
    return bool(e) and browser.is_brave_argv(e["argv"])


# ---- icons ------------------------------------------------------------------------------------------------

def _icon_bases():
    home = Path.home()
    bases = [home / ".local/share/icons", home / ".icons",
             home / ".local/share/flatpak/exports/share/icons", Path("/var/lib/flatpak/exports/share/icons")]
    override = os.environ.get("IMACTV_ICON_DIRS")
    if override is not None:
        return [Path(p) for p in override.split(":") if p]
    data_dirs = os.environ.get("XDG_DATA_DIRS") or "/usr/local/share:/usr/share"
    bases += [Path(p) / "icons" for p in data_dirs.split(":") if p]
    bases.append(Path("/usr/share/pixmaps"))
    return bases


def _size_of(path):
    for part in path.parts:
        head, x, tail = part.partition("x")
        if x and head.isdigit() and tail.isdigit():
            return int(head)
    return 512 if "scalable" in path.parts else 64     # unsized files


def _find_icon(name):
    """Best file for an Icon= name: a PNG/SVG of at least ~128 px if there is one, else the largest."""
    if not name:
        return None
    if os.path.isabs(name):
        return Path(name) if os.path.isfile(name) else None
    candidates = []
    for base in _icon_bases():
        if not base.is_dir():
            continue
        for ext in ("png", "svg"):
            direct = base / f"{name}.{ext}"      # /usr/share/pixmaps
            if direct.is_file():
                candidates.append(direct)
        for pattern in (f"*/*/apps/{name}.*", f"*/apps/*/{name}.*", f"*/*/*/apps/{name}.*"):
            candidates += [p for p in base.glob(pattern) if p.suffix in (".png", ".svg")]
    if not candidates:
        return None

    def rank(path):
        size = _size_of(path)
        return (0 if size >= 128 else 1, size if size >= 128 else -size, 0 if path.suffix == ".png" else 1)
    return min(candidates, key=rank)


def _cached_icon_source(name):
    key = (name, tuple(str(p) for p in _icon_bases()))
    now = time.monotonic()
    with _lock:
        cached = _icon_sources.get(key)
        if cached and now - cached[0] < ICON_SOURCE_TTL:
            _icon_sources.move_to_end(key)
            return cached[1]
    source = _find_icon(name)
    with _lock:
        _icon_sources[key] = (now, source)
        _icon_sources.move_to_end(key)
        while len(_icon_sources) > ICON_SOURCE_LIMIT:
            _icon_sources.popitem(last=False)
    return source


def _render_png(src, dest):
    """Write src (png/svg/xpm) to dest as a PNG no larger than ICON_SIZE. False if nothing can read it."""
    try:
        import gi
        gi.require_version("GdkPixbuf", "2.0")
        from gi.repository import GdkPixbuf
        pix = GdkPixbuf.Pixbuf.new_from_file_at_scale(str(src), ICON_SIZE, ICON_SIZE, True)
        pix.savev(str(dest), "png", [], [])
        return True
    except Exception:
        pass
    if src.suffix == ".png":
        try:
            shutil.copyfile(src, dest)
            return True
        except OSError:
            return False
    return False


def icon_file(app_id):
    """Path of a cached PNG for a desktop app, or None. Never raises."""
    try:
        e = lookup(app_id)
        if not e:
            return None
        src = _cached_icon_source(e.get("icon_name"))
        if not src:
            return None
        stamp = f"{src}:{src.stat().st_mtime_ns}"
        cache = data_dir() / "icon_cache"
        dest = cache / (hashlib.sha1(f"{app_id}:{stamp}".encode()).hexdigest() + ".png")
        if dest.is_file():
            return str(dest)
        cache.mkdir(parents=True, exist_ok=True)
        fd, temp_name = tempfile.mkstemp(prefix=dest.stem + ".", suffix=".tmp", dir=cache)
        os.close(fd)
        tmp = Path(temp_name)
        try:
            if _render_png(src, tmp):
                os.replace(tmp, dest)
                return str(dest)
        finally:
            tmp.unlink(missing_ok=True)
    except Exception:
        pass
    return None
