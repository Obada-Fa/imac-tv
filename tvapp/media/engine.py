"""Client for the headless stremio-service torrent streaming server."""
import json
import urllib.error
import urllib.request


class Engine:
    def __init__(self, base_url):
        self.base = base_url.rstrip("/")

    def _get(self, path, timeout=2, data=None):
        request = urllib.request.Request(self.base + path, data=data,
                                         headers={"Content-Type": "application/json"} if data else {})
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return response.read(2_000_000)

    def healthy(self):
        try:
            self._get("/stats.json", timeout=1.5)
            return True
        except (urllib.error.URLError, OSError, ValueError):
            return False

    def url(self, info_hash, file_idx):
        return f"{self.base}/{info_hash}/{int(file_idx)}"

    def stats(self, info_hash, file_idx):
        try:
            data = json.loads(self._get(f"/{info_hash}/{int(file_idx)}/stats.json"))
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
