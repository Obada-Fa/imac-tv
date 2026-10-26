# iMac TV media contract (shared by all agents — do not deviate without the orchestrator)

## Package layout: tvapp/media/ (Python 3 stdlib only)
- `config.py`  — `load()` returns dict merged from defaults + `$IMACTV_DATA_DIR/media.json`:
  `{"addons": {"meta": "https://v3-cinemeta.strem.io", "streams": ["https://torrentio.strem.fun/sort=qualitysize|qualityfilter=cam,scr,unknown"],
    "subtitles": "https://opensubtitles-v3.strem.io"}, "sub_langs": ["ara","dut","eng"],
    "engine": "http://127.0.0.1:11470", "cache_bytes": 3221225472, "max_size_gb": 25,
    "quality_order": ["1080p","2160p","720p","480p"]}`
  DATA dir resolution identical to server.py (`IMACTV_DATA_DIR` env or `~/.local/share/imactv`).
- `addons.py` — `catalog(type, catalog_id="top", genre=None, skip=0)`, `search(type, query)`,
  `meta(type, id)`, `streams(type, video_id)`, `subtitles(type, video_id, extra=None)`.
  type ∈ {"movie","series"}. video_id = "tt123" for movies, "tt123:S:E" for episodes.
  Returns parsed lists/dicts from the Stremio add-on protocol (`/catalog/{type}/{id}[/genre=..&skip=..].json`, `/meta/{type}/{id}.json`, `/stream/{type}/{id}.json`, `/subtitles/{type}/{id}[/extra].json`).
  TTL JSON cache under `$DATA/cache/addons/` (catalog/meta 6h, streams 30 min, subs 6h). Timeouts 8s. Raises `addons.AddonError` on failure.
- `streams.py` — `parse(stream) -> Source` dict:
  `{"info_hash","file_idx","title","quality","hdr": bool,"dv": bool,"codec","source","size_bytes","seeders","languages":[..],"label"}`
  label example: `"1080p · WEB-DL · 2.4 GB · 312 seeds"`. `rank(sources, cfg) -> list` (filtered: no CAM/TS/SCR, size<=max_size_gb, must have info_hash), `best(sources, cfg) -> Source|None`.
- `engine.py` — `Engine(base_url)`: `healthy() -> bool` (any HTTP response counts as up), `is_local` (True for 127.0.0.1/localhost/::1), `url(info_hash, file_idx) -> str`
  (`{base}/{hash}/{idx}`), `stats(info_hash, file_idx) -> {"peers": int, "speed": float bytes/s, "downloaded": int} | None`,
  `remove(info_hash)`, `configure_cache(bytes)`.
- `subs.py` — `pick(sub_list, langs, per_lang=2) -> [{"url","lang","label"}]` normalising lang codes (ar/ara, nl/dut/nld, en/eng). Labels: "Arabic", "Nederlands", "English".
- `player.py` — `mpv_argv(url, *, title, subs, start=0, ipc="/tmp/mpv.sock") -> list[str]` (pure). Runtime lifecycle (launch/watch/stop) added in wave 2.
- `history.py` (wave 2) — `$DATA/history.json`: `{ "<video_id>": {"type","id","video_id","season","episode","name","poster","position","duration","finished","updated"} }`.
  API: `load()`, `record(entry)`, `resume_position(video_id) -> float`, `continue_watching(limit=20) -> list` (newest first, unfinished, position>60s; finished episode → next episode entry if known).
  finished = position/duration >= 0.92.

## Server actions (POST /api/commands {action, params}) — existing auth unchanged
- `play_media` params: `{type: "movie"|"series", id: "tt…", season?: int, episode?: int, stream?: {info_hash, file_idx}, start?: float}`.
  Validates input, resolves sources (unless `stream` given), picks best, fetches subs, launches mpv via `player.mpv_argv`.
  Returns `{"ok": true, "source": label}` quickly — heavy work happens in a background thread; errors surface in state.error.
- `sources` params `{type, id, season?, episode?}` → `{"sources": [Source...]}` ranked (for the TV "Sources" screen and phone).
- `audio` params `{id: int|"no"}` → sets mpv `aid` (only while playing).
- `subtitle` (existing) `{id: int|"no"}`; `subdelay` (existing).
- `open_search` params `{}` → server tells TV Home to show Search screen (writes `$DATA/ui_request.json` `{"screen":"search","ts":..}` which home.py polls), returns `{"ok": true}`.
- `back` while playing: quits mpv, sets app "home", and TV Home returns to the Details screen of the media that was playing (home.py reads `state.media` / last_media).
- `playback_suggestion` params `{id: string, choice: "switch"|"dismiss"}` answers
  the current health prompt. Stale IDs are ignored. Switch captures the live
  timestamp (or last known position after failure) and opens the internally held
  source via `play_media`; clients never submit its URL. OK/Back answer the same
  prompt while it is active, ahead of skip/up-next handling.

## GET /api/state additions (keep all existing fields)
- `app`: now one of `"home" | "youtube" | "player"`.
- `phase`: `"ready" | "opening" | "buffering" | "playing"`.
- `buffering`: `{"peers": int, "speed": float, "source": label} | null` (non-null only while phase=="buffering").
- `media`: `{"type","id","video_id","season","episode","name"} | null` — current or last-played item (`last_media` persists after stop so Home can return to Details).
- `playback` (existing shape) plus: each `subtitles[]` entry gains `"lang"`; new `"audio": [{"id","title","lang","selected"}]`.
- `playback_suggestion`: null or `{id, reason: "bandwidth"|"decode"|"unplayable",
  title, message, current: source_label, alternative: source_label, failed: bool}`.
  Available after playback failure as well as while playing. Contains display
  labels only; URLs and trackers remain private.

## UI ↔ server
- home.py browses metadata by importing `media.addons` directly (in a thread; never block GTK main loop).
- Playback is always started through the server action `play_media` (home.py uses its existing `cmd(action, params)` helper).
