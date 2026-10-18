#!/usr/bin/env python3
"""Exercise pairing and state without sending any TV actions."""
import http.cookiejar
import json
import os
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import build_opener, HTTPCookieProcessor, Request

root = "http://127.0.0.1:" + os.environ.get("IMACTV_PORT", "18765")
data = Path(os.environ["IMACTV_DATA_DIR"])
client = build_opener(HTTPCookieProcessor(http.cookiejar.CookieJar()))

try:
    client.open(root + "/api/state", timeout=3)
    raise AssertionError("Unauthenticated state was accepted")
except HTTPError as exc:
    assert exc.code == 401

code = json.loads((data / "pairing.json").read_text())["code"]
request = Request(root + "/api/pair", data=json.dumps({"code": code}).encode(),
                  headers={"Content-Type": "application/json"}, method="POST")
with client.open(request, timeout=3) as response:
    assert json.load(response)["paired"]

with client.open(root + "/api/state", timeout=3) as response:
    state = json.load(response)
    assert state["app"] == "home"
    assert state["phase"] == "ready"
print("HTTP pairing and authenticated state passed")
