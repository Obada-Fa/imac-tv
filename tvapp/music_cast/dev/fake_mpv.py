#!/usr/bin/env python3
"""Stand-in for mpv's JSON IPC (only what the music receiver uses). Songs last DUR seconds."""
import json, os, socket, sys, threading, time
DUR = float(os.environ.get("FAKE_DUR", "2.0"))
sock_path = next(a.split("=", 1)[1] for a in sys.argv if a.startswith("--input-ipc-server="))
open(os.environ.get("FAKE_ARGS", "/dev/null"), "w").write(json.dumps(sys.argv[1:]))
st = {"file": None, "pause": False, "pos": 0.0, "t": time.monotonic(), "gen": 0}
clients, lock = [], threading.Lock()
def emit(ev):
    for c in list(clients):
        try: c.sendall((json.dumps(ev) + "\n").encode())
        except OSError: pass
def pos():
    return st["pos"] if st["pause"] or not st["file"] else st["pos"] + time.monotonic() - st["t"]
def setpause(v):
    st["pos"], st["t"], st["pause"] = pos(), time.monotonic(), v
    emit({"event": "property-change", "id": 1, "name": "pause", "data": v})
def play(url, gen):
    time.sleep(0.3)
    if gen != st["gen"]: return
    if "bad" in url:
        st["file"] = None; emit({"event": "end-file", "reason": "error"}); return
    st.update(file=url, pos=0.0, t=time.monotonic()); emit({"event": "start-file"}); emit({"event": "file-loaded"})
    while gen == st["gen"]:
        time.sleep(0.05)
        if st["file"] and pos() >= DUR:
            st["file"] = None; emit({"event": "end-file", "reason": "eof"}); return
def handle(c):
    buf = b""
    while True:
        d = c.recv(4096)
        if not d: return
        buf += d
        while b"\n" in buf:
            line, buf = buf.split(b"\n", 1)
            m = json.loads(line); cmd = m["command"]; data = None; err = "success"
            if cmd[0] == "get_property":
                data = {"time-pos": pos() if st["file"] else None, "duration": DUR if st["file"] else None, "pause": st["pause"]}.get(cmd[1])
                if data is None: err = "property unavailable"
            elif cmd[0] == "set_property" and cmd[1] == "pause": setpause(bool(cmd[2]))
            elif cmd[0] == "loadfile":
                if st["file"]: emit({"event": "end-file", "reason": "stop"})
                st["gen"] += 1; st["file"] = None
                threading.Thread(target=play, args=(cmd[1], st["gen"]), daemon=True).start()
            elif cmd[0] == "seek": st["pos"], st["t"] = float(cmd[1]), time.monotonic()
            elif cmd[0] == "stop":
                st["gen"] += 1
                if st["file"]: st["file"] = None; emit({"event": "end-file", "reason": "stop"})
            c.sendall((json.dumps({"request_id": m.get("request_id"), "error": err, "data": data}) + "\n").encode())
srv = socket.socket(socket.AF_UNIX); srv.bind(sock_path); srv.listen()
while True:
    c, _ = srv.accept(); clients.append(c); threading.Thread(target=handle, args=(c,), daemon=True).start()
