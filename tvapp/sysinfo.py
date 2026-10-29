"""Read-only iMac stats for the phone's System panel, straight from /proc and /sys.

Rates (CPU %, per-process CPU, network) are deltas against the previous sample, so the
first call reports zeros. Samples are cached for a second however often the phone polls."""
import os
import threading
import time
from pathlib import Path

# Processes the phone can restart or close. Matched on /proc/<pid>/comm and cmdline; never on
# anything the phone sends.
MANAGED = {
    "discord": {"title": "Discord", "match": ("com.discordapp.discord", "/discord/discord", "discord")},
    "youtube": {"title": "YouTube", "match": ("vacuumtube", "rocks.shy.vacuumtube")},
    "player": {"title": "Video player", "match": ("mpv", "streamlink")},
}


def _read(path):
    try:
        return Path(path).read_text()
    except (OSError, UnicodeDecodeError):
        return ""


def temps(root="/"):
    """(cpu, gpu) in °C: Intel coretemp and the iMac's SMC GPU sensors; None when absent."""
    base = Path(root)
    cpu = []
    for sensor in (base / "sys/class/hwmon").glob("hwmon*/temp*_input"):
        try:
            if (sensor.parent / "name").read_text().strip() == "coretemp":
                value = int(sensor.read_text()) / 1000
                if 0 < value < 120:
                    cpu.append(value)
        except (OSError, ValueError):
            continue
    gpu = []
    for label in (base / "sys/devices/platform/applesmc.768").glob("temp*_label"):
        try:
            if label.read_text().strip() in ("TG0D", "TG0H", "TG0p"):
                value = int(label.with_name(label.name.replace("_label", "_input")).read_text()) / 1000
                if 0 < value < 120:
                    gpu.append(value)
        except (OSError, ValueError):
            continue
    return (max(cpu) if cpu else None), (max(gpu) if gpu else None)


def parse_cpu(text):
    """/proc/stat -> (busy, total) jiffies of the 'cpu' line."""
    for line in text.splitlines():
        if line.startswith("cpu "):
            values = [int(v) for v in line.split()[1:]]
            idle = values[3] + (values[4] if len(values) > 4 else 0)
            return sum(values) - idle, sum(values)
    return 0, 0


def parse_meminfo(text):
    fields = {}
    for line in text.splitlines():
        name, _, rest = line.partition(":")
        parts = rest.split()
        if parts and parts[0].isdigit():
            fields[name] = int(parts[0]) * 1024
    total = fields.get("MemTotal", 0)
    available = fields.get("MemAvailable", fields.get("MemFree", 0))
    swap_total = fields.get("SwapTotal", 0)
    return {"total": total, "used": max(0, total - available),
            "swap_total": swap_total, "swap_used": max(0, swap_total - fields.get("SwapFree", 0))}


def parse_netdev(text):
    """/proc/net/dev -> (rx, tx) bytes summed over every interface except loopback and docker bridges."""
    rx = tx = 0
    for line in text.splitlines()[2:]:
        name, _, rest = line.partition(":")
        name = name.strip()
        if not rest or name == "lo" or name.startswith(("docker", "veth", "br-")):
            continue
        parts = rest.split()
        if len(parts) >= 9:
            rx += int(parts[0])
            tx += int(parts[8])
    return rx, tx


def parse_proc_stat(text):
    """/proc/<pid>/stat -> (comm, utime + stime). comm may contain spaces and parentheses."""
    left, right = text.find("("), text.rfind(")")
    if left < 0 or right < 0:
        return None
    fields = text[right + 2:].split()
    if len(fields) < 13:
        return None
    return text[left + 1:right], int(fields[11]) + int(fields[12])


def managed_key(comm, cmdline):
    comm, cmdline = comm.lower(), cmdline.lower()
    for key, spec in MANAGED.items():
        for needle in spec["match"]:
            if needle == comm or ("/" in needle or "." in needle) and needle in cmdline:
                return key
    return None


class Sampler:
    def __init__(self, root="/", clock=time.monotonic):
        self.root = Path(root)
        self.clock = clock
        self.lock = threading.Lock()
        self.page = os.sysconf("SC_PAGE_SIZE") if hasattr(os, "sysconf") else 4096
        self.ticks = os.sysconf("SC_CLK_TCK") if hasattr(os, "sysconf") else 100
        self.cores = os.cpu_count() or 1
        self.prev = None
        self.cached = None

    def _path(self, rel):
        return self.root / rel

    def processes(self):
        """{pid: (comm, cpu_jiffies, rss_bytes, cmdline)} for every readable process."""
        found = {}
        for entry in self._path("proc").iterdir() if self._path("proc").is_dir() else ():
            if not entry.name.isdigit():
                continue
            stat = parse_proc_stat(_read(entry / "stat"))
            if not stat:
                continue
            statm = _read(entry / "statm").split()
            rss = int(statm[1]) * self.page if len(statm) > 1 and statm[1].isdigit() else 0
            cmdline = _read(entry / "cmdline").replace("\0", " ").strip()
            found[int(entry.name)] = (stat[0], stat[1], rss, cmdline)
        return found

    def sample(self):
        with self.lock:
            now = self.clock()
            if self.cached and now - self.cached[0] < 1.0:
                return self.cached[1]
            busy, total = parse_cpu(_read(self._path("proc/stat")))
            rx, tx = parse_netdev(_read(self._path("proc/net/dev")))
            procs = self.processes()
            prev = self.prev
            elapsed = now - prev["time"] if prev else 0
            cpu = 0.0
            if prev and total > prev["total"]:
                cpu = 100.0 * (busy - prev["busy"]) / (total - prev["total"])
            net = {"rx": 0, "tx": 0}
            if prev and elapsed > 0:
                net = {"rx": max(0, int((rx - prev["rx"]) / elapsed)), "tx": max(0, int((tx - prev["tx"]) / elapsed))}
            rows, apps = [], {key: {"key": key, "title": spec["title"], "running": False, "pids": [], "memory": 0, "cpu": 0.0}
                              for key, spec in MANAGED.items()}
            for pid, (comm, jiffies, rss, cmdline) in procs.items():
                share = 0.0
                if prev and elapsed > 0 and pid in prev["procs"]:
                    share = 100.0 * (jiffies - prev["procs"][pid]) / self.ticks / elapsed / self.cores
                rows.append({"pid": pid, "name": comm, "cpu": round(max(0.0, share), 1), "memory": rss})
                key = managed_key(comm, cmdline)
                if key:
                    app = apps[key]
                    app["running"] = True
                    app["pids"].append(pid)
                    app["memory"] += rss
                    app["cpu"] = round(app["cpu"] + max(0.0, share), 1)
            self.prev = {"time": now, "busy": busy, "total": total, "rx": rx, "tx": tx,
                         "procs": {pid: info[1] for pid, info in procs.items()}}
            load = _read(self._path("proc/loadavg")).split()
            uptime = _read(self._path("proc/uptime")).split()
            try:
                disk = os.statvfs(self.root)
                disk_info = {"total": disk.f_blocks * disk.f_frsize,
                             "used": (disk.f_blocks - disk.f_bfree) * disk.f_frsize}
            except OSError:
                disk_info = None
            cpu_temp, gpu_temp = temps(self.root)
            result = {
                "cpu": round(max(0.0, min(100.0, cpu)), 1), "cores": self.cores,
                "memory": parse_meminfo(_read(self._path("proc/meminfo"))),
                "load": [float(v) for v in load[:3]] if len(load) >= 3 else None,
                "uptime": int(float(uptime[0])) if uptime else None,
                "disk": disk_info, "network": net, "battery": self.battery(),
                "temperature": cpu_temp, "gpu_temperature": gpu_temp,
                "top_cpu": sorted(rows, key=lambda r: r["cpu"], reverse=True)[:5],
                "top_memory": sorted(rows, key=lambda r: r["memory"], reverse=True)[:5],
                "apps": list(apps.values()),
            }
            self.cached = (now, result)
            return result

    def system(self):
        """The cheap, system-wide part of sample() (no per-process scan): cpu, memory, network, uptime,
        disk and temperatures. Its rates use their own baseline, so it can run between full samples."""
        with self.lock:
            now = self.clock()
            busy, total = parse_cpu(_read(self._path("proc/stat")))
            rx, tx = parse_netdev(_read(self._path("proc/net/dev")))
            prev = getattr(self, "sys_prev", None)
            elapsed = now - prev["time"] if prev else 0
            cpu = 0.0
            if prev and total > prev["total"]:
                cpu = 100.0 * (busy - prev["busy"]) / (total - prev["total"])
            net = {"rx": 0, "tx": 0}
            if prev and elapsed > 0:
                net = {"rx": max(0, int((rx - prev["rx"]) / elapsed)), "tx": max(0, int((tx - prev["tx"]) / elapsed))}
            self.sys_prev = {"time": now, "busy": busy, "total": total, "rx": rx, "tx": tx}
            uptime = _read(self._path("proc/uptime")).split()
            try:
                disk = os.statvfs(self.root)
                disk_info = {"total": disk.f_blocks * disk.f_frsize,
                             "used": (disk.f_blocks - disk.f_bfree) * disk.f_frsize}
            except OSError:
                disk_info = None
            cpu_temp, gpu_temp = temps(self.root)
            return {"cpu": round(max(0.0, min(100.0, cpu)), 1), "cores": self.cores,
                    "memory": parse_meminfo(_read(self._path("proc/meminfo"))),
                    "uptime": int(float(uptime[0])) if uptime else None, "disk": disk_info, "network": net,
                    "temperature": cpu_temp, "gpu_temperature": gpu_temp}

    def battery(self):
        for supply in sorted(self._path("sys/class/power_supply").glob("BAT*")):
            capacity = _read(supply / "capacity").strip()
            if capacity.isdigit():
                return {"percent": int(capacity), "status": _read(supply / "status").strip() or None}
        return None

    def pids(self, key):
        """Current pids of one managed app, fresh (not cached)."""
        return sorted(pid for pid, (comm, _, _, cmdline) in self.processes().items()
                      if managed_key(comm, cmdline) == key)
