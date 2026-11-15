"""Wi-Fi from the couch: Settings -> Network on the TV and the phone's Settings, through NetworkManager's
nmcli. Nothing here runs in the background; every call is one or two short nmcli commands."""
import os
import shutil
import subprocess

WIFI_TYPE = "802-11-wireless"


def split(line):
    """One line of `nmcli -t`: fields are joined by ':' and a ':' or '\\' inside a field is escaped."""
    out, cur, i = [], [], 0
    while i < len(line):
        c = line[i]
        if c == "\\" and i + 1 < len(line):
            cur.append(line[i + 1])
            i += 2
            continue
        if c == ":":
            out.append("".join(cur))
            cur = []
        else:
            cur.append(c)
        i += 1
    out.append("".join(cur))
    return out


def run_nmcli(args, timeout=20):
    """(exit code, stdout, stderr) of nmcli in the C locale, so its messages can be read."""
    env = dict(os.environ, LC_ALL="C")
    try:
        p = subprocess.run(["nmcli"] + list(args), capture_output=True, text=True, timeout=timeout, env=env)
        return p.returncode, p.stdout, p.stderr
    except subprocess.TimeoutExpired:
        return 124, "", "Timeout"
    except OSError as exc:
        return 127, "", str(exc)


def friendly(err):
    """nmcli's error in words for the TV."""
    low = (err or "").lower()
    if "secrets were required" in low or "802-1x" in low or "invalid" in low and "password" in low or "psk" in low:
        return "That password didn't work"
    if "no network with ssid" in low:
        return "That network is out of range now"
    if "timeout" in low:
        return "The network didn't answer in time"
    if "not authorized" in low or "insufficient privileges" in low:
        return "This account isn't allowed to change the Wi-Fi on this PC"
    text = (err or "").strip().splitlines()[-1:] or ["Couldn't connect"]
    return text[0].replace("Error: ", "")[:160]


class Wifi:
    def __init__(self, run=run_nmcli, have=None):
        self.run = run
        self.have = (lambda: shutil.which("nmcli") is not None) if have is None else have

    def lines(self, args, timeout=20):
        code, out, _ = self.run(args, timeout)
        return [split(l) for l in out.splitlines() if l.strip()] if code == 0 else []

    def saved(self):
        """Names of the Wi-Fi networks this PC remembers."""
        return {f[0] for f in self.lines(["-t", "-f", "NAME,TYPE", "connection", "show"]) if len(f) >= 2 and f[1] == WIFI_TYPE}

    def status(self, rescan=False):
        """{"available", "enabled", "ssid", "signal", "ethernet", "networks": [{ssid, signal, secure, active, saved}]}:
        the strongest access point of each name, the connected one first, then by signal."""
        out = {"available": False, "enabled": False, "ssid": "", "signal": 0, "ethernet": False, "networks": []}
        if not self.have():
            return out
        for f in self.lines(["-t", "-f", "DEVICE,TYPE,STATE", "device"]):
            if len(f) < 3:
                continue
            if f[1] == "wifi":
                out["available"] = True
            if f[1] == "ethernet" and f[2].startswith("connected"):
                out["ethernet"] = True
        if not out["available"]:
            return out
        radio = self.lines(["-t", "-f", "WIFI", "radio"])
        out["enabled"] = bool(radio) and radio[0][0] == "enabled"
        if not out["enabled"]:
            return out
        saved = self.saved()
        best = {}
        for f in self.lines(["-t", "-f", "IN-USE,SSID,SIGNAL,SECURITY", "device", "wifi", "list",
                             "--rescan", "yes" if rescan else "no"], timeout=30):
            if len(f) < 4 or not f[1]:                   # hidden networks have no name to show
                continue
            try:
                signal = int(f[2])
            except ValueError:
                signal = 0
            net = {"ssid": f[1], "signal": signal, "secure": f[3] not in ("", "--"), "active": f[0].strip() == "*",
                   "saved": f[1] in saved}
            old = best.get(f[1])
            if old is None or (net["active"], signal) > (old["active"], old["signal"]):
                best[f[1]] = net
        out["networks"] = sorted(best.values(), key=lambda n: (not n["active"], -n["signal"], n["ssid"].lower()))
        active = next((n for n in out["networks"] if n["active"]), None)
        if active:
            out["ssid"], out["signal"] = active["ssid"], active["signal"]
        return out

    def connect(self, ssid, password=""):
        """(ok, message). A remembered network needs no password; a new password replaces the remembered one."""
        ssid = (ssid or "").strip()
        if not ssid:
            return False, "Choose a network"
        was_saved = ssid in self.saved()
        if was_saved and not password:
            code, _, err = self.run(["--wait", "30", "connection", "up", "id", ssid], 40)
        else:
            if was_saved:
                self.run(["connection", "delete", "id", ssid], 10)
            args = ["--wait", "30", "device", "wifi", "connect", ssid]
            if password:
                args += ["password", password]
            code, out, err = self.run(args, 45)
            err = err or out
            if code != 0:                                # don't keep a profile holding a wrong password
                self.run(["connection", "delete", "id", ssid], 10)
        return (True, "Connected to " + ssid) if code == 0 else (False, friendly(err))

    def forget(self, ssid):
        code, _, err = self.run(["connection", "delete", "id", ssid or ""], 10)
        return (True, "Forgot " + ssid) if code == 0 else (False, friendly(err))

    def power(self, on):
        code, _, err = self.run(["radio", "wifi", "on" if on else "off"], 10)
        return (True, "Wi-Fi is " + ("on" if on else "off")) if code == 0 else (False, friendly(err))


def describe(status):
    """One line for a settings row."""
    if not status.get("available"):
        return "Wired network" if status.get("ethernet") else "No Wi-Fi card in this PC"
    if not status.get("enabled"):
        return "Off"
    if status.get("ssid"):
        return status["ssid"]
    return "Not connected" + ("  ·  wired network in use" if status.get("ethernet") else "")
