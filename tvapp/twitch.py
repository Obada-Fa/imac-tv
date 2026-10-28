"""Ad-free Twitch: live stream listing through Twitch's public GQL endpoint, and the streamlink launch line.

Streamlink filters Twitch's ad segments out of the HLS stream (current releases do this by default;
older ones need --twitch-disable-ads), then hands the video to mpv. Nothing here needs a Twitch login.
"""
import json
import os
import re
import shlex
import shutil
import subprocess
import threading
import time
import urllib.request

GQL_URL = "https://gql.twitch.tv/gql"
CLIENT_ID = "kimne78kx3ncx6brgo4mv6wki5h1ko"   # the public web player's id, no account involved
LOGIN_RE = re.compile(r"^[A-Za-z0-9_]{1,25}$")
CACHE_SECONDS = 60
TIMEOUT = 6
MAX_BYTES = 1 << 20
INSTALL_HINT = "Install streamlink: re-run ./install.sh"

STREAM_FIELDS = ("id title viewersCount previewImageURL(width: 640, height: 360) "
                 "broadcaster { login displayName } game { displayName }")
TOP_QUERY = "query { streams(first: %d) { edges { node { %s } } } }"
USER_QUERY = 'query { user(login: "%s") { login displayName stream { %s } } }'
SEARCH_QUERY = ('query { searchFor(userQuery: %s, platform: "web", options: {targets: [{index: CHANNEL}]}) '
                '{ channels { edges { item { ... on User { login displayName stream { %s } } } } } } }')

FAKE_STREAMS = [
    {"login": "chess", "name": "Chess", "title": "Speed Chess Championship: Semi-finals", "game": "Chess",
     "viewers": 18342, "thumbnail": ""},
    {"login": "lofigirl", "name": "Lofi Girl", "title": "lofi hip hop radio - beats to relax/study to", "game": "Music",
     "viewers": 12980, "thumbnail": ""},
    {"login": "speedrunners", "name": "Speedrunners", "title": "Any% relay race, new world record attempt",
     "game": "Super Mario 64", "viewers": 7411, "thumbnail": ""},
    {"login": "cookingwithjo", "name": "CookingWithJo", "title": "Sunday ramen from scratch", "game": "Food & Drink",
     "viewers": 2210, "thumbnail": ""},
    {"login": "nightowl", "name": "NightOwl", "title": "Cozy farming and chatting", "game": "Stardew Valley",
     "viewers": 954, "thumbnail": ""},
    {"login": "retrodan", "name": "RetroDan", "title": "Blind playthrough: Chrono Trigger", "game": "Chrono Trigger",
     "viewers": 611, "thumbnail": ""},
]


class TwitchError(RuntimeError):
    """A failure whose message is safe to show on the TV or the phone."""


def valid_login(value):
    return isinstance(value, str) and bool(LOGIN_RE.match(value.strip().lstrip("#")))


def clean_login(value):
    """Normalized channel login, or TwitchError. Accepts '#name' and full twitch.tv URLs."""
    if isinstance(value, str):
        value = value.strip()
        match = re.match(r"^(?:https?://)?(?:www\.)?twitch\.tv/([^/?#]+)", value, re.I)
        value = (match.group(1) if match else value).lstrip("#")
    if not valid_login(value):
        raise TwitchError("That doesn't look like a Twitch channel name")
    return value.lower()


def _fake():
    """IMACTV_FAKE_TWITCH: '1' for the built-in list, or a path to a JSON list of stream dicts. Test-only."""
    spec = os.environ.get("IMACTV_FAKE_TWITCH")
    if not spec:
        return None
    if spec == "1":
        return [dict(s) for s in FAKE_STREAMS]
    try:
        with open(spec, encoding="utf-8") as fh:
            data = json.load(fh)
        return [dict(s) for s in data] if isinstance(data, list) else []
    except (OSError, ValueError):
        return []


def _post(query):
    body = json.dumps({"query": query}).encode()
    req = urllib.request.Request(GQL_URL, data=body, headers={"Client-ID": CLIENT_ID, "Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT) as response:
            data = json.loads(response.read(MAX_BYTES))
    except Exception as exc:
        raise TwitchError("Couldn't reach Twitch") from exc
    if not isinstance(data, dict) or data.get("errors") or not isinstance(data.get("data"), dict):
        raise TwitchError("Twitch didn't answer as expected")
    return data["data"]


def _stream(node, user=None):
    """One normalized stream dict from a GQL node, or None when it lacks a valid live channel."""
    if not isinstance(node, dict):
        return None
    who = node.get("broadcaster") or user or {}
    login = who.get("login")
    if not valid_login(login):
        return None
    game = node.get("game")
    return {"login": login.lower(), "name": who.get("displayName") or login,
            "title": str(node.get("title") or "")[:200], "game": (game or {}).get("displayName") or "",
            "viewers": int(node.get("viewersCount") or 0), "thumbnail": node.get("previewImageURL") or ""}


_cache = {}
_lock = threading.Lock()


def _cached(key, fetch):
    now = time.monotonic()
    with _lock:
        hit = _cache.get(key)
        if hit and now - hit[0] < CACHE_SECONDS:
            return hit[1]
    try:
        value = fetch()
    except TwitchError:
        if hit:                        # serve stale rather than an empty screen
            return hit[1]
        raise
    with _lock:
        _cache[key] = (time.monotonic(), value)
    return value


def clear_cache():
    with _lock:
        _cache.clear()


def top_streams(limit=24):
    """Most-watched live streams, as [{login, name, title, game, viewers, thumbnail}]."""
    fake = _fake()
    if fake is not None:
        return fake[:limit]
    limit = max(1, min(int(limit), 50))

    def fetch():
        edges = (_post(TOP_QUERY % (limit, STREAM_FIELDS)).get("streams") or {}).get("edges") or []
        return [s for s in (_stream((e or {}).get("node")) for e in edges) if s]
    return _cached(("top", limit), fetch)


def find(query, limit=12):
    """Live channels matching a name (or one exact channel). Offline channels are left out."""
    query = (query or "").strip() if isinstance(query, str) else ""
    if not query:
        return []
    fake = _fake()
    if fake is not None:
        needle = query.lower()
        return [s for s in fake if needle in s["login"] or needle in s["name"].lower() or needle in s["title"].lower()][:limit]

    def fetch():
        out = []
        if valid_login(query):
            user = _post(USER_QUERY % (query, STREAM_FIELDS)).get("user") or {}
            if user.get("stream"):
                out.append(_stream(user["stream"], user))
        edges = (_post(SEARCH_QUERY % (json.dumps(query[:60]), STREAM_FIELDS)).get("searchFor") or {}) \
            .get("channels", {}).get("edges") or []
        for edge in edges:
            item = (edge or {}).get("item") or {}
            if item.get("stream"):
                out.append(_stream(item["stream"], item))
        seen, unique = set(), []
        for s in out:
            if s and s["login"] not in seen:
                seen.add(s["login"])
                unique.append(s)
        return unique[:limit]
    return _cached(("find", query.lower(), limit), fetch)


# ---------------------------------------------------------------- playback

_help_cache = {}


def _supports_disable_ads(binary):
    """Streamlink 6+ always drops ad segments and removed the flag; older ones need it."""
    if binary not in _help_cache:
        try:
            out = subprocess.run([binary, "--help"], capture_output=True, text=True, timeout=10).stdout
        except (OSError, subprocess.SubprocessError):
            out = ""
        _help_cache[binary] = "--twitch-disable-ads" in out
    return _help_cache[binary]


def streamlink_path():
    return shutil.which("streamlink") or shutil.which("streamlink", path=os.path.expanduser("~/.local/bin"))


def streamlink_argv(login, *, mpv_argv, binary=None, disable_ads=None):
    """streamlink command that plays twitch.tv/<login> in mpv with the given mpv flags (from player.mpv_argv).

    `mpv_argv` is a full mpv command line; its flags go to streamlink's --player-args and its trailing
    '-- <url>' is replaced by streamlink's own input ('-': a pipe, so mpv never sees a Twitch URL)."""
    login = clean_login(login)
    binary = binary or streamlink_path()
    if not binary:
        raise TwitchError(INSTALL_HINT)
    if disable_ads is None:
        disable_ads = _supports_disable_ads(binary)
    flags = list(mpv_argv[1:])
    if "--" in flags:
        flags = flags[:flags.index("--")]
    # Live streams: no resume, no "cache before start" stall; streamlink supplies the window title.
    flags = [f for f in flags if not f.startswith(("--cache-pause-initial", "--start=", "--demuxer-readahead-secs",
                                                    "--title=", "--force-media-title="))]
    argv = [binary, "--quiet", "--twitch-low-latency", "--retry-streams", "3", "--retry-max", "3"]
    if disable_ads:
        argv.append("--twitch-disable-ads")
    argv += ["--player", mpv_argv[0], "--player-args", shlex.join(flags) + " {playerinput}",
             "--title", "{author} - {title}", f"https://www.twitch.tv/{login}", "best,720p60,720p,480p,worst"]
    return argv
