"""Display control for iMac TV: resolution modes through xrandr, a keep-or-revert trial, and whether any
screen is showing the app at all.

No GTK here: server.py (a separate process) imports this too. Parsing and decisions are pure functions;
the subprocess and file reads are thin wrappers around them.

Contract (the TV Settings screen and the phone Settings use exactly these):

    list_outputs() -> [{"name": "HDMI-1", "connected": True, "primary": False,
                        "current": {"mode": "1920x1080", "rate": 60.0} | None,     # None: output is off
                        "preferred": {"mode": "1920x1080", "rate": 60.0} | None,
                        "modes": [{"mode": "1920x1080", "rates": [60.0, 50.0, 59.94]}, ...],
                        "geometry": {"x", "y", "w", "h"} | None}]                   # extra, may be ignored
    apply_mode(output, mode, rate) -> None                 raises DisplayError (xrandr missing / refused)
    start_trial(output, mode, rate, seconds=15) -> trial_state()
        applies the mode and remembers the previous one; reverts by itself after `seconds` unless confirmed.
        Starting a new trial while one runs keeps the original mode as the one to go back to.
    confirm_trial() -> bool       keep it: saves home.json "display": {"output", "mode", "rate"}
    revert_trial() -> bool        go back to the previous mode now
    trial_state() -> {"active": True, "remaining": 12.4, "output", "mode", "rate"} | {"active": False}
    check_trial() -> bool         revert if the time is up (the TV app calls this every second; it is cheap)
    restore_saved() -> bool       app start-up: re-apply the saved mode if that output is connected

The trial lives in DATA/display_trial.json (flock-guarded), so a trial started from the phone shows its
countdown on the TV and either process can confirm, revert or time it out.

Screen-off idle:

    screen_live() -> bool         False when no screen can be showing the app: every DRM connector
                                  disconnected, no connected connector enabled or all of them DPMS-off, the lid
                                  closed with only the built-in panel, or `xset q` saying "Monitor is Off".
    subscribe(cb) / unsubscribe(cb)    cb(live: bool) on the GTK main loop when it changes
    start_watch()                 polls every 5 s (file reads; `xset q` at most every 10 s), GTK process only
    poke()                        user input: re-check now, and count the screen as live for a minute

Test hooks: IMACTV_FAKE_SCREEN=on|off|<path to a file holding on/off>; IMACTV_FAKE_XRANDR=<path to an
`xrandr --query` text> makes list/apply work on that file instead of the X server (apply moves the `*`).
"""
import fcntl
import glob
import json
import os
from pathlib import Path
import re
import subprocess
import threading
import time

DATA = Path(os.environ.get("IMACTV_DATA_DIR", str(Path.home() / ".local/share/imactv")))
TRIAL_SECONDS = 15
INTERNAL = ("eDP", "LVDS", "DSI")       # built-in laptop panels: off when the lid is closed
POLL_SECONDS = 5
XSET_EVERY = 10.0
INPUT_GRACE = 60.0


class DisplayError(RuntimeError):
    pass


# ---------------------------------------------------------------- xrandr parsing (pure)

_OUT_RE = re.compile(r"^(\S+) (connected|disconnected|unknown connection)( primary)?"
                     r"(?: (\d+)x(\d+)\+(-?\d+)\+(-?\d+))?")
_RATE_RE = re.compile(r"^(\d+(?:\.\d+)?)([*+]*)$")


def _mode_line(line):
    """'   1920x1080     60.00*+  50.00    59.94 +' -> ("1920x1080", [(60.0, cur, pref), ...]) or None."""
    parts = line.split()
    if not parts or not re.match(r"^\d+x\d+", parts[0]):
        return None
    rates = []
    for tok in parts[1:]:
        if tok.strip("*+") == "":                     # a lone "+" / "*" belongs to the rate before it
            if rates:
                r, c, p = rates[-1]
                rates[-1] = (r, c or "*" in tok, p or "+" in tok)
            continue
        m = _RATE_RE.match(tok)
        if not m:
            continue                                   # --verbose style columns: ignore
        rates.append((round(float(m.group(1)), 2), "*" in m.group(2), "+" in m.group(2)))
    return parts[0], rates


def parse_xrandr(text):
    """`xrandr --query` output -> the list_outputs() structure."""
    outputs = []
    cur = None
    for line in (text or "").splitlines():
        if not line.strip() or line.startswith("Screen "):
            continue
        if not line[0].isspace():
            m = _OUT_RE.match(line)
            cur = None
            if m:
                geo = None
                if m.group(4):
                    geo = {"w": int(m.group(4)), "h": int(m.group(5)), "x": int(m.group(6)), "y": int(m.group(7))}
                cur = {"name": m.group(1), "connected": m.group(2) == "connected", "primary": bool(m.group(3)),
                       "current": None, "preferred": None, "modes": [], "geometry": geo}
                outputs.append(cur)
            continue
        if cur is None:
            continue
        ml = _mode_line(line)
        if not ml:
            continue
        name, rates = ml
        entry = next((e for e in cur["modes"] if e["mode"] == name), None)
        if entry is None:
            entry = {"mode": name, "rates": []}
            cur["modes"].append(entry)
        for rate, is_cur, is_pref in rates:
            if rate not in entry["rates"]:
                entry["rates"].append(rate)
            if is_cur and cur["geometry"] is not None:
                cur["current"] = {"mode": name, "rate": rate}
            if is_pref and cur["preferred"] is None:
                cur["preferred"] = {"mode": name, "rate": rate}
    return outputs


def mode_size(mode):
    """'1920x1080' / '1920x1080i' / '1280x720_60.00' -> (1920, 1080), else None."""
    m = re.match(r"^(\d+)x(\d+)", mode or "")
    return (int(m.group(1)), int(m.group(2))) if m else None


def find_output(outputs, name):
    return next((o for o in outputs if o["name"] == name), None)


def has_mode(output, mode, rate=None):
    entry = next((e for e in output["modes"] if e["mode"] == mode), None)
    if entry is None:
        return False
    return rate is None or any(abs(r - float(rate)) < 0.02 for r in entry["rates"])


def best_rate(output, entry):
    """The rate a resolution button uses: current, else preferred, else the highest at or below 60 Hz."""
    for ref in (output.get("current"), output.get("preferred")):
        if ref and ref["mode"] == entry["mode"] and ref["rate"] in entry["rates"]:
            return ref["rate"]
    low = [r for r in entry["rates"] if r <= 60.5]
    return max(low) if low else (max(entry["rates"]) if entry["rates"] else None)


# ---------------------------------------------------------------- xrandr I/O

def _x_env():
    env = dict(os.environ)
    env["DISPLAY"] = os.environ.get("IMACTV_DISPLAY") or os.environ.get("DISPLAY") or ":0"
    if os.environ.get("IMACTV_XAUTHORITY"):
        env["XAUTHORITY"] = os.environ["IMACTV_XAUTHORITY"]
    return env


def _fake_path():
    p = os.environ.get("IMACTV_FAKE_XRANDR")
    return Path(p) if p else None


def query_text():
    fake = _fake_path()
    if fake:
        try:
            return fake.read_text()
        except OSError:
            return ""
    try:
        res = subprocess.run(["xrandr", "--query"], capture_output=True, text=True, timeout=8, env=_x_env())
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise DisplayError("xrandr is not available: %s" % exc)
    if res.returncode != 0:
        raise DisplayError((res.stderr or "xrandr failed").strip())
    return res.stdout


def list_outputs():
    return parse_xrandr(query_text())


def tv_output(outputs):
    """The output the TV app is most likely on: primary if it is on, else the first one that is on."""
    on = [o for o in outputs if o["connected"] and o["geometry"]]
    return next((o for o in on if o["primary"]), on[0] if on else None)


def fake_geometry():
    """(w, h) of the TV output under IMACTV_FAKE_XRANDR, else None (the real size comes from GDK)."""
    if not _fake_path():
        return None
    out = tv_output(list_outputs())
    return (out["geometry"]["w"], out["geometry"]["h"]) if out else None


def fake_apply(text, output, mode, rate):
    """Move the `*` of an `xrandr --query` text to output/mode/rate (the IMACTV_FAKE_XRANDR hook)."""
    lines, cur = [], None
    size = mode_size(mode)
    for line in text.splitlines():
        if line and not line[0].isspace():
            m = _OUT_RE.match(line)
            cur = m.group(1) if m else None
            if cur == output and size and m.group(2) == "connected":
                head = "%s connected%s %dx%d+%s+%s" % (output, m.group(3) or "", size[0], size[1],
                                                       m.group(6) or "0", m.group(7) or "0")
                line = head + line[m.end():]
        elif cur == output:
            line = line.replace("*", " ")
            parts = line.split()
            if parts and parts[0] == mode:
                line = re.sub(r"(?<![\d.])%s(?![\d])" % re.escape("%.2f" % float(rate)),
                              lambda mm: mm.group(0) + "*", line, count=1)
        lines.append(line)
    return "\n".join(lines) + "\n"


def apply_mode(output, mode, rate):
    """Switch `output` to `mode` at `rate` Hz. Raises DisplayError."""
    if not output or not mode:
        raise DisplayError("No output or mode")
    fake = _fake_path()
    if fake:
        try:
            fake.write_text(fake_apply(fake.read_text(), output, mode, rate))
        except OSError as exc:
            raise DisplayError(str(exc))
        return
    argv = ["xrandr", "--output", str(output), "--mode", str(mode)]
    if rate:
        argv += ["--rate", "%.2f" % float(rate)]
    try:
        res = subprocess.run(argv, capture_output=True, text=True, timeout=15, env=_x_env())
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise DisplayError("xrandr failed: %s" % exc)
    if res.returncode != 0:
        raise DisplayError((res.stderr or "xrandr refused the mode").strip())


# ---------------------------------------------------------------- saved choice (home.json "display")

def _home_json(data_dir=None):
    return Path(data_dir or DATA) / "home.json"


def saved_mode(data_dir=None):
    try:
        d = json.loads(_home_json(data_dir).read_text()).get("display")
    except (OSError, ValueError, AttributeError):
        return None
    if isinstance(d, dict) and isinstance(d.get("output"), str) and isinstance(d.get("mode"), str):
        try:
            rate = float(d.get("rate")) if d.get("rate") is not None else None
        except (TypeError, ValueError):
            rate = None
        return {"output": d["output"], "mode": d["mode"], "rate": rate}
    return None


def save_mode(output, mode, rate, data_dir=None):
    path = _home_json(data_dir)
    try:
        cfg = json.loads(path.read_text())
        if not isinstance(cfg, dict):
            cfg = {}
    except (OSError, ValueError):
        cfg = {}
    cfg["display"] = {"output": output, "mode": mode, "rate": rate}
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(cfg, indent=2) + "\n")
    os.replace(tmp, path)


def restore_plan(saved, outputs):
    """(output, mode, rate) to apply at start-up, or None when nothing is saved, the output is gone or the
    mode is already current."""
    if not saved:
        return None
    out = find_output(outputs, saved["output"])
    if not out or not out["connected"] or not has_mode(out, saved["mode"], saved["rate"]):
        return None
    cur = out["current"]
    if cur and cur["mode"] == saved["mode"] and (saved["rate"] is None or abs(cur["rate"] - saved["rate"]) < 0.02):
        return None
    return out["name"], saved["mode"], saved["rate"]


def restore_saved():
    try:
        plan = restore_plan(saved_mode(), list_outputs())
        if plan:
            apply_mode(*plan)
            return True
    except DisplayError as exc:
        print("display restore:", exc)
    return False


# ---------------------------------------------------------------- keep-or-revert trial

class Trials:
    """The 15 s trial as a small state machine over a JSON file. `apply(output, mode, rate)`, `outputs()`,
    `save(output, mode, rate)` and `clock()` (wall time: shared across processes) are injected."""

    def __init__(self, path, apply=None, outputs=None, save=None, clock=time.time):
        self.path = Path(path)
        self.apply = apply or apply_mode
        self.outputs = outputs or list_outputs
        self.save = save or save_mode
        self.clock = clock
        self._tlock = threading.Lock()

    def _locked(self):
        lf = open(str(self.path) + ".lock", "a+")
        fcntl.flock(lf, fcntl.LOCK_EX)
        return lf

    def _read(self):
        try:
            d = json.loads(self.path.read_text())
            return d if isinstance(d, dict) and d.get("output") else None
        except (OSError, ValueError):
            return None

    def _write(self, d):
        tmp = self.path.with_name(self.path.name + ".tmp")
        tmp.write_text(json.dumps(d))
        os.replace(tmp, self.path)

    def _clear(self):
        try:
            self.path.unlink()
        except OSError:
            pass

    def start(self, output, mode, rate, seconds=TRIAL_SECONDS):
        rate = float(rate) if rate is not None else None
        with self._tlock:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            lf = self._locked()
            try:
                out = find_output(self.outputs(), output)
                if not out or not out["connected"]:
                    raise DisplayError("%s is not connected" % output)
                if not has_mode(out, mode, rate):
                    raise DisplayError("%s does not offer %s" % (output, mode))
                old = self._read()
                if old and old["output"] == output:
                    prev = old["prev"]                    # chained trials still go back to the original
                else:
                    if old:
                        self._revert_locked(old)
                    if not out["current"]:
                        raise DisplayError("%s is off" % output)
                    prev = dict(out["current"])
                self.apply(output, mode, rate)
                self._write({"output": output, "mode": mode, "rate": rate, "prev": prev,
                             "deadline": self.clock() + seconds, "seconds": seconds})
            finally:
                lf.close()
        return self.state()

    def state(self):
        d = self._read()
        if not d:
            return {"active": False}
        return {"active": True, "remaining": max(0.0, round(d["deadline"] - self.clock(), 1)),
                "output": d["output"], "mode": d["mode"], "rate": d["rate"]}

    def confirm(self):
        with self._tlock:
            lf = self._locked()
            try:
                d = self._read()
                if not d:
                    return False
                self._clear()
                self.save(d["output"], d["mode"], d["rate"])
                return True
            finally:
                lf.close()

    def _revert_locked(self, d):
        self._clear()
        prev = d.get("prev") or {}
        try:
            self.apply(d["output"], prev["mode"], prev.get("rate"))
        except (DisplayError, KeyError) as exc:
            print("display revert:", exc)

    def revert(self):
        with self._tlock:
            lf = self._locked()
            try:
                d = self._read()
                if not d:
                    return False
                self._revert_locked(d)
                return True
            finally:
                lf.close()

    def check(self):
        """Revert when the time is up. True when it reverted."""
        if not self.path.exists():                     # the common case: one stat
            return False
        d = self._read()
        if d and self.clock() >= d["deadline"]:
            with self._tlock:
                lf = self._locked()
                try:
                    d = self._read()                    # someone may have confirmed meanwhile
                    if d and self.clock() >= d["deadline"]:
                        self._revert_locked(d)
                        return True
                finally:
                    lf.close()
        return False


_trials = None


def _t():
    global _trials
    if _trials is None:
        _trials = Trials(DATA / "display_trial.json")
    return _trials


def start_trial(output, mode, rate, seconds=TRIAL_SECONDS):
    st = _t().start(output, mode, rate, seconds)
    timer = threading.Timer(seconds + 0.3, check_trial)   # times out even if the TV app isn't running
    timer.daemon = True
    timer.start()
    return st


def confirm_trial():
    return _t().confirm()


def revert_trial():
    return _t().revert()


def trial_state():
    return _t().state()


def check_trial():
    return _t().check()


# ---------------------------------------------------------------- is any screen showing the app?

def sysfs_screen(drm_root="/sys/class/drm", lid_glob="/proc/acpi/button/lid/*/state"):
    """True/False from the kernel's DRM connectors (and the lid), None when there is nothing to go on."""
    conns = []
    for d in sorted(glob.glob(os.path.join(drm_root, "card*-*"))):
        name = os.path.basename(d).split("-", 1)[1]
        if name.startswith("Writeback"):
            continue

        def rd(f):
            try:
                with open(os.path.join(d, f)) as fh:
                    return fh.read().strip()
            except OSError:
                return ""
        conns.append((name, rd("status"), rd("enabled"), rd("dpms")))
    if not conns:
        return None
    lid_closed = False
    for f in glob.glob(lid_glob):
        try:
            with open(f) as fh:
                lid_closed = lid_closed or "closed" in fh.read()
        except OSError:
            pass
    return screen_from_connectors(conns, lid_closed)


def screen_from_connectors(conns, lid_closed=False):
    """conns: [(name, status, enabled, dpms)] as sysfs spells them. Pure."""
    live = [c for c in conns if c[1] == "connected"]
    if lid_closed:
        live = [c for c in live if not c[0].startswith(INTERNAL)]
    if not live:
        return False
    if all(c[2] in ("enabled", "disabled") for c in live):     # the driver reports it: trust it
        live = [c for c in live if c[2] == "enabled"]
        if not live:
            return False
    if all(c[3] in ("On", "Off", "Standby", "Suspend") for c in live) and all(c[3] != "On" for c in live):
        return False
    return True


def xset_monitor(text):
    """`xset q` -> False when it says "Monitor is Off"/Standby/Suspend, True when "Monitor is On", else None."""
    m = re.search(r"Monitor is (\w+)", text or "")
    if not m:
        return None
    return m.group(1) == "On"


def _run_xset():
    try:
        res = subprocess.run(["xset", "q"], capture_output=True, text=True, timeout=2, env=_x_env())
        return res.stdout
    except (OSError, subprocess.TimeoutExpired):
        return ""


def fake_screen(value):
    """IMACTV_FAKE_SCREEN: "on"/"off", or a path to a file holding one of them. None: no fake."""
    if not value:
        return None
    v = value.strip().lower()
    if v in ("on", "off"):
        return v == "on"
    try:
        with open(value) as fh:
            return fh.read().strip().lower() != "off"
    except OSError:
        return True


class ScreenWatch:
    """Polls; tells listeners when the answer changes. Everything is injected for tests."""

    def __init__(self, sysfs=sysfs_screen, xset=_run_xset, clock=time.monotonic, fake=None):
        self.sysfs, self.xset, self.clock = sysfs, xset, clock
        self.fake = fake if fake is not None else os.environ.get("IMACTV_FAKE_SCREEN")
        self.live = True
        self.listeners = []
        self.last_xset = -1e9
        self.xset_live = None
        self.grace_until = -1e9
        self.source = 0

    def measure(self, force_xset=False):
        f = fake_screen(self.fake)
        if f is not None:
            return f
        sys_live = self.sysfs()
        if sys_live is False:
            return False
        now = self.clock()
        if force_xset or now - self.last_xset >= XSET_EVERY:
            self.last_xset = now
            self.xset_live = xset_monitor(self.xset())
        return self.xset_live is not False

    def poll(self, force_xset=False):
        live = self.measure(force_xset)
        if not live and self.clock() < self.grace_until and fake_screen(self.fake) is None:
            live = True
        self._set(live)
        return True

    def poke(self):
        """Input arrived. If we thought the screen was off, look again; someone pressing keys is watching."""
        if self.live:
            return
        self.grace_until = self.clock() + INPUT_GRACE
        self.poll(force_xset=True)

    def _set(self, live):
        if live == self.live:
            return
        self.live = live
        for cb in list(self.listeners):
            try:
                cb(live)
            except Exception as exc:
                print("display listener:", exc)

    def start(self):
        if self.source:
            return
        from gi.repository import GLib
        self.poll()
        self.source = GLib.timeout_add_seconds(POLL_SECONDS, self.poll)


watch = ScreenWatch()


def screen_live():
    return watch.live


def subscribe(cb):
    if cb not in watch.listeners:
        watch.listeners.append(cb)


def unsubscribe(cb):
    if cb in watch.listeners:
        watch.listeners.remove(cb)


def start_watch():
    watch.start()


def poke():
    watch.poke()
