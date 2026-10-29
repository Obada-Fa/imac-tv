import os
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import sysinfo

MEMINFO = """MemTotal:        8000000 kB
MemFree:          500000 kB
MemAvailable:    3000000 kB
SwapTotal:       2000000 kB
SwapFree:        1500000 kB
"""
NETDEV = """Inter-|   Receive                                                |  Transmit
 face |bytes    packets errs drop fifo frame compressed multicast|bytes    packets errs drop fifo colls carrier compressed
    lo: 999999 10 0 0 0 0 0 0 999999 10 0 0 0 0 0 0
  wlp2s0: {rx} 10 0 0 0 0 0 0 {tx} 10 0 0 0 0 0 0
docker0: 5555 1 0 0 0 0 0 0 5555 1 0 0 0 0 0 0
"""


def stat_line(pid, comm, jiffies):
    rest = ["S"] + ["0"] * 10 + [str(jiffies), "0"] + ["0"] * 10
    return f"{pid} ({comm}) " + " ".join(rest)


class FakeRoot:
    def __init__(self):
        self.dir = tempfile.TemporaryDirectory()
        self.root = Path(self.dir.name)
        self.write("proc/meminfo", MEMINFO)
        self.write("proc/loadavg", "0.50 0.40 0.30 1/200 999")
        self.write("proc/uptime", "3600.5 100.0")
        self.write("sys/class/power_supply/BAT0/capacity", "87")
        self.write("sys/class/power_supply/BAT0/status", "Discharging")
        self.write("sys/class/hwmon/hwmon1/name", "coretemp")
        self.write("sys/class/hwmon/hwmon1/temp1_input", "61000")
        self.tick(busy=100, total=1000, rx=1000, tx=500)

    def write(self, rel, text):
        path = self.root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)

    def proc(self, pid, comm, jiffies, rss_pages=10, cmdline=""):
        self.write(f"proc/{pid}/stat", stat_line(pid, comm, jiffies))
        self.write(f"proc/{pid}/statm", f"100 {rss_pages} 0 0 0 0 0")
        self.write(f"proc/{pid}/cmdline", cmdline.replace(" ", "\0"))

    def tick(self, busy, total, rx, tx):
        idle = total - busy
        self.write("proc/stat", f"cpu  {busy} 0 0 {idle} 0 0 0 0 0 0\ncpu0 1 0 0 1 0 0 0 0 0 0\n")
        self.write("proc/net/dev", NETDEV.format(rx=rx, tx=tx))


class SysinfoTest(unittest.TestCase):
    def setUp(self):
        self.fake = FakeRoot()
        self.addCleanup(self.fake.dir.cleanup)
        self.now = 100.0
        self.sampler = sysinfo.Sampler(self.fake.root, clock=lambda: self.now)
        self.sampler.page, self.sampler.ticks, self.sampler.cores = 4096, 100, 2

    def test_parsers(self):
        self.assertEqual(sysinfo.parse_cpu("cpu  10 0 5 80 5 0 0 0\n"), (15, 100))
        mem = sysinfo.parse_meminfo(MEMINFO)
        self.assertEqual(mem["total"], 8000000 * 1024)
        self.assertEqual(mem["used"], 5000000 * 1024)
        self.assertEqual(mem["swap_used"], 500000 * 1024)
        self.assertEqual(sysinfo.parse_netdev(NETDEV.format(rx=10, tx=20)), (10, 20))
        self.assertEqual(sysinfo.parse_proc_stat(stat_line(5, "Web Content (x)", 42)), ("Web Content (x)", 42))
        self.assertIsNone(sysinfo.parse_proc_stat("garbage"))

    def test_managed_apps_match_by_name_only(self):
        self.assertEqual(sysinfo.managed_key("Discord", "/app/discord/Discord --type=renderer"), "discord")
        self.assertEqual(sysinfo.managed_key("bwrap", "bwrap --args 41 com.discordapp.Discord"), "discord")
        self.assertEqual(sysinfo.managed_key("mpv", "mpv --fs -"), "player")
        self.assertEqual(sysinfo.managed_key("streamlink", "/usr/bin/python3 /usr/bin/streamlink"), "player")
        self.assertEqual(sysinfo.managed_key("vacuumtube", "/opt/VacuumTube/vacuumtube"), "youtube")
        self.assertIsNone(sysinfo.managed_key("bash", "bash -c echo discord"))
        self.assertIsNone(sysinfo.managed_key("python3", "python3 server.py"))

    def test_rates_between_samples(self):
        self.fake.proc(10, "Discord", 100, rss_pages=1000, cmdline="/app/discord/Discord")
        self.fake.proc(11, "bash", 50)
        first = self.sampler.sample()
        self.assertEqual(first["cpu"], 0.0)
        self.assertEqual(first["network"], {"rx": 0, "tx": 0})
        self.now += 2
        self.fake.tick(busy=400, total=1600, rx=3000, tx=1500)
        self.fake.proc(10, "Discord", 300, rss_pages=1000, cmdline="/app/discord/Discord")
        result = self.sampler.sample()
        self.assertEqual(result["cpu"], 50.0)
        self.assertEqual(result["network"], {"rx": 1000, "tx": 500})
        discord = next(a for a in result["apps"] if a["key"] == "discord")
        self.assertTrue(discord["running"])
        self.assertEqual(discord["pids"], [10])
        self.assertEqual(discord["memory"], 1000 * 4096)
        self.assertEqual(discord["cpu"], 50.0)  # 200 jiffies / 100 Hz / 2 s / 2 cores
        self.assertEqual(result["top_cpu"][0]["name"], "Discord")
        self.assertEqual(result["uptime"], 3600)
        self.assertEqual(result["load"], [0.5, 0.4, 0.3])
        self.assertEqual(result["battery"], {"percent": 87, "status": "Discharging"})
        self.assertEqual(result["temperature"], 61.0)
        self.assertFalse(next(a for a in result["apps"] if a["key"] == "player")["running"])

    def test_cached_for_a_second(self):
        first = self.sampler.sample()
        self.now += 0.5
        self.assertIs(self.sampler.sample(), first)
        self.now += 1
        self.assertIsNot(self.sampler.sample(), first)

    def test_pids(self):
        self.fake.proc(20, "mpv", 1, cmdline="mpv --fs -")
        self.fake.proc(21, "Discord", 1, cmdline="/app/discord/Discord")
        self.assertEqual(self.sampler.pids("player"), [20])


if __name__ == "__main__":
    unittest.main()
