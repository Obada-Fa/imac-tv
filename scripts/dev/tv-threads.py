import os, sys, time
def find(n):
    for p in os.listdir("/proc"):
        if p.isdigit():
            try:
                c = open(f"/proc/{p}/cmdline").read()
            except OSError: continue
            if n in c and "python3" in c and "threads.py" not in c: return int(p)
def tsnap(pid):
    o = {}
    for t in os.listdir(f"/proc/{pid}/task"):
        try:
            s = open(f"/proc/{pid}/task/{t}/stat").read(); f = s[s.rindex(")") + 2:].split()
            o[t] = (int(f[11]), int(f[12]), s[s.index("(") + 1:s.rindex(")")])
        except OSError: pass
    return o
def io(pid):
    try: return dict(l.split(": ") for l in open(f"/proc/{pid}/io").read().splitlines())
    except OSError: return {}
def lo():
    for l in open("/proc/net/dev"):
        if l.strip().startswith("lo:"): f = l.split(":")[1].split(); return int(f[1]), int(f[0])
pids = {n: find(n) for n in ("server.py", "home.py", "gl_ambient.py")}
a = {n: (tsnap(p), io(p)) for n, p in pids.items() if p}; l0 = lo(); T = 20; time.sleep(T)
l1 = lo(); print(f"loopback: {(l1[0]-l0[0])/T:.0f} packets/s, {(l1[1]-l0[1])/T/1024:.0f} KB/s")
for n, p in pids.items():
    if not p: continue
    b, ib = tsnap(p), io(p); ia = a[n][1]
    print(f"{n} pid {p}: threads now {len(b)}, new threads in {T}s: {len(set(b)-set(a[n][0]))}, syscalls r/w per s: {(int(ib.get('syscr',0))-int(ia.get('syscr',0)))/T:.0f}/{(int(ib.get('syscw',0))-int(ia.get('syscw',0)))/T:.0f}")
    rows = sorted(((b[t][0]-a[n][0].get(t,(0,0))[0], b[t][1]-a[n][0].get(t,(0,0))[1], t, b[t][2]) for t in b), reverse=True, key=lambda r: r[0]+r[1])[:6]
    for u, s, t, name in rows: print(f"   tid {t} {name:16} user {u/T:.1f}%  sys {s/T:.1f}%")
