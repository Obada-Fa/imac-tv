#!/usr/bin/env python3
"""Per-process CPU over an interval, from /proc. Usage: measure.py SECONDS [label]
Reports % of one core per group, CPU burned in short-lived helper children, and a census of
helper processes seen (polled at 50 Hz, so very short ones are undercounted; 'forks' is exact but includes threads)."""
import collections, os, sys, time
secs = float(sys.argv[1]); label = sys.argv[2] if len(sys.argv) > 2 else ""
HZ = os.sysconf("SC_CLK_TCK")
GROUPS = [("home.py", "home.py"), ("gl_ambient", "gl_ambient.py"), ("server.py", "server.py"), ("kiosk", "kiosk.py"),
          ("Xorg", "/Xorg"), ("vacuumtube", "vacuumtube"), ("brave", "brave"), ("mpv", "mpv"), ("pulseaudio", "pulseaudio"),
          ("pipewire", "pipewire"), ("stremio-engine", "stremio"), ("cast-receiver", "receiver.mjs"), ("xfwm4/wm", "xfwm4"),
          ("parec", "parec")]
def snap():
    out = {}
    for p in os.listdir("/proc"):
        if not p.isdigit(): continue
        try:
            with open(f"/proc/{p}/stat") as f: s = f.read()
            with open(f"/proc/{p}/cmdline") as f: cmd = f.read().replace("\0", " ").strip()
        except OSError: continue
        comm = s[s.index("(") + 1:s.rindex(")")]; f = s[s.rindex(")") + 2:].split()
        out[int(p)] = (comm, cmd, int(f[1]), int(f[11]) + int(f[12]), int(f[13]) + int(f[14]), int(f[19]))
    return out
def stat(key):
    with open("/proc/stat") as f:
        for line in f:
            if line.startswith(key + " "): return [int(x) for x in line.split()[1:]]
def group(comm, cmd):
    for name, needle in GROUPS:
        if needle in cmd or needle == comm: return name
    return "other:" + comm
a, cpu0, forks0, ctx0, t0 = snap(), stat("cpu"), stat("processes")[0], stat("ctxt")[0], time.time()
known, census = set(a), collections.Counter()
while time.time() - t0 < secs:
    time.sleep(0.02)
    for p in os.listdir("/proc"):
        if p.isdigit() and int(p) not in known:
            known.add(int(p))
            try:
                with open(f"/proc/{p}/cmdline") as f: c = f.read().split("\0")
                with open(f"/proc/{p}/stat") as f: pp = int(f.read().rsplit(")", 1)[1].split()[1])
            except OSError: continue
            if pp in a and c and c[0]:
                census[(group(a[pp][0], a[pp][1]), os.path.basename(c[0]) + (" " + c[1] if len(c) > 1 and c[0].endswith(("python3", "sh", "bash", "env")) else ""))] += 1
b, cpu1, dt = snap(), stat("cpu"), time.time() - t0
own, kids, rss = collections.Counter(), collections.Counter(), collections.Counter()
for pid, (comm, cmd, ppid, t, ct, start) in b.items():
    g = group(comm, cmd); old = a.get(pid)
    if old and old[5] == start:
        own[g] += t - old[3]; kids[g] += ct - old[4]
    else:
        own[g] += t
    try:
        with open(f"/proc/{pid}/statm") as f: rss[g] += int(f.read().split()[1]) * 4 // 1024
    except OSError: pass
tot = sum(cpu1) - sum(cpu0); idle = (cpu1[3] + cpu1[4]) - (cpu0[3] + cpu0[4])
pc = lambda j: 100.0 * j / HZ / dt
print(f"== {label}  {dt:.0f}s  cores={os.cpu_count()}  whole machine busy: {100 * (tot - idle) / tot * os.cpu_count():.1f}% of one core")
print(f"   forks+threads/s: {(stat('processes')[0] - forks0) / dt:.1f}   context switches/s: {(stat('ctxt')[0] - ctx0) / dt:.0f}")
print(f"   {'group':22}{'own %core':>10}{'reaped children %core':>24}{'RSS MB':>8}")
shown = 0
for g, j in sorted(own.items(), key=lambda kv: -(kv[1] + kids[kv[0]])):
    if pc(j + kids[g]) < 0.15 and not any(g == n for n, _ in GROUPS[:4]): continue
    print(f"   {g:22}{pc(j):>10.1f}{pc(kids[g]):>24.1f}{rss[g]:>8}"); shown += j
print(f"   helper processes seen, per minute (parent → command):")
for (g, c), n in census.most_common(14): print(f"     {n * 60 / dt:>6.0f}  {g} → {c}")
