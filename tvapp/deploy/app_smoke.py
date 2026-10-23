#!/usr/bin/env python3
"""Open each installed TV app, verify its window, and return to TV Home."""
import json
import os
from pathlib import Path
import subprocess
import time
from urllib.request import Request, urlopen

base = "http://127.0.0.1:8765"
secret = (Path.home() / "tvremote/secret.txt").read_text().strip()
headers = {"Cookie": "tvremote=" + secret, "Content-Type": "application/json"}
env = dict(os.environ, DISPLAY=":0", XAUTHORITY=str(Path.home() / ".Xauthority"))


def state():
    with urlopen(Request(base + "/api/state", headers=headers), timeout=5) as response:
        return json.load(response)


def command(app):
    body = json.dumps({"action": "select", "params": {"app": app}}).encode()
    with urlopen(Request(base + "/api/commands", data=body, headers=headers), timeout=15) as response:
        return json.load(response)


for app, expected in (("youtube", "VacuumTube"),):
    print("Opening", app, flush=True)
    command(app)
    for attempt in range(50):
        status = state()
        if status["app"] == app and status["phase"] == "ready":
            break
        if status["error"]:
            raise RuntimeError(status["error"])
        time.sleep(1)
    else:
        raise RuntimeError(f"{app} did not become ready")
    active = subprocess.run(["xdotool", "getactivewindow", "getwindowname"],
                            capture_output=True, text=True, env=env, timeout=3)
    assert expected.lower() in active.stdout.lower(), active.stdout
    print(app, "ready; active:", active.stdout.strip(), flush=True)
    command("home")
    for attempt in range(10):
        active = subprocess.run(["xdotool", "getactivewindow", "getwindowname"],
                                capture_output=True, text=True, env=env, timeout=3)
        if "iMac TV" in active.stdout:
            break
        time.sleep(1)
    else:
        raise RuntimeError("Home did not regain focus")
    print("Returned Home", flush=True)
print("App switching passed")
