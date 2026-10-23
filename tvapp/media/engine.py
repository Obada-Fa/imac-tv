"""Client for the headless stremio-service torrent streaming server."""
import json
import urllib.error
import urllib.parse
import urllib.request

LOCAL_HOSTS = {"127.0.0.1", "localhost", "::1"}
# Public trackers added to every torrent, so a source picked without Torrentio's list still finds peers fast.
TRACKERS = ("udp://tracker.opentrackr.org:1337/announce", "udp://open.demonii.com:1337/announce",
            "udp://open.stealth.si:80/announce", "udp://tracker.torrent.eu.org:451/announce",
            "udp://exodus.desync.com:6969/announce", "udp://tracker.openbittorrent.com:6969/announce")


class Engine:
    def __init__(self, base_url):
        self.base = base_url.rstrip("/")

    def _get(self, path, timeout=2, data=None):
        request = urllib.request.Request(self.base + path, data=data,
                                         headers={"Content-Type": "application/json"} if data else {})
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return response.read(2_000_000)

    @property
    def is_local(self):
        return urllib.parse.urlsplit(self.base).hostname in LOCAL_HOSTS

    def healthy(self):
        try:
            self._get("/stats.json", timeout=3)
            return True
        except urllib.error.HTTPError:
            return True  # the server answered, just not with 2xx
        except (urllib.error.URLError, OSError, ValueError):
            return False

    def create(self, info_hash, sources=()):
        """Hand the server the torrent with its peer sources, as the Stremio app does before streaming.

        Without this the server only searches DHT, which can take minutes or never find peers.
        """
        peers = [x for x in sources if isinstance(x, str)]
        peers += ["tracker:" + t for t in TRACKERS if "tracker:" + t not in peers]
        peers.append("dht:" + info_hash)
        body = {"torrent": {"infoHash": info_hash}, "peerSearch": {"sources": peers, "min": 40, "max": 150}}
        try:
            self._get(f"/{info_hash}/create", timeout=10, data=json.dumps(body).encode())
            return True
        except (urllib.error.URLError, OSError, ValueError):
            return False

    def url(self, info_hash, file_idx):
        return f"{self.base}/{info_hash}/{-1 if file_idx is None else int(file_idx)}"

    def stats(self, info_hash, file_idx):
        try:
            data = json.loads(self._get(f"/{info_hash}/{-1 if file_idx is None else int(file_idx)}/stats.json"))
        except (urllib.error.URLError, OSError, ValueError):
            return None
        if not isinstance(data, dict):
            return None
        peers = data.get("swarmConnections", data.get("peers", 0))
        return {"peers": int(peers or 0), "speed": float(data.get("downloadSpeed") or 0),
                "downloaded": int(data.get("downloaded") or 0)}

    def remove(self, info_hash):
        try:
            self._get(f"/{info_hash}/remove")
        except (urllib.error.URLError, OSError):
            pass

    def configure_cache(self, size):
        try:
            self._get("/settings", data=json.dumps({"cacheSize": int(size)}).encode())
        except (urllib.error.URLError, OSError):
            pass
