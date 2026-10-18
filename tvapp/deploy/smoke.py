#!/usr/bin/env python3
"""Non-destructive remote smoke checks for the deployed controller."""
import http.cookiejar
import json
from pathlib import Path
import time
from urllib.request import build_opener, HTTPCookieProcessor, Request

BASE = "http://127.0.0.1:8765"
opener = build_opener(HTTPCookieProcessor(http.cookiejar.CookieJar()))


def request(path, body=None):
    data = json.dumps(body).encode() if body else None
    token = (Path.home() / ".local/share/imactv/home_token").read_text().strip()
    headers = {"Cookie": "imactv=" + token}
    if data:
        headers["Content-Type"] = "application/json"
    with opener.open(Request(BASE + path, data=data, headers=headers), timeout=5) as response:
        return json.load(response)


for attempt in range(20):
    try:
        state = request("/api/state")
        break
    except Exception:
        time.sleep(0.5)
else:
    raise RuntimeError("TV controller did not open its port")
print("initial:", state["app"], state["phase"], "graphical:", state["gui"])
assert state["gui"], "X11 is unavailable"
request("/api/commands", {"action": "select", "params": {"app": "home"}})
for attempt in range(20):
    state = request("/api/state")
    if state["app"] == "home" and state["phase"] == "ready":
        break
    time.sleep(0.5)
else:
    raise RuntimeError("TV Home did not become ready")
print("TV Home ready; volume:", state["volume"], "temperature:", state["temperature"])
