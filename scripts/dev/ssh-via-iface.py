#!/usr/bin/env python3
"""ssh ProxyCommand: connect to host:port through one network interface (SO_BINDTODEVICE)."""
import os, select, socket, sys
host, port, dev = sys.argv[1], int(sys.argv[2]), sys.argv[3]
s = socket.socket()
s.setsockopt(socket.SOL_SOCKET, socket.SO_BINDTODEVICE, dev.encode())
s.settimeout(8); s.connect((host, port)); s.settimeout(None)
while True:
    r, _, _ = select.select([0, s], [], [])
    if 0 in r:
        d = os.read(0, 65536)
        if not d: break
        s.sendall(d)
    if s in r:
        d = s.recv(65536)
        if not d: break
        os.write(1, d)
