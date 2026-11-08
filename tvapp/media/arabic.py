"""Arabic releases from ArabP2P, offered as extra sources that play through TorBox.

Nothing here runs in the background: a search happens only when a title's sources are asked for
(or when the user searches in Arabic), and every answer is cached on disk. ArabP2P is a private
tracker with ratio rules, so its torrents are only ever handed to TorBox (which seeds them back),
never streamed from the TV itself.

Arabic releases are named in Arabic, so a title is matched by its Arabic names: TMDB's Arabic
translations and alternative titles when a TMDB key is set, Wikidata's Arabic labels otherwise.
Matching and episode parsing follow Arabarr (media-stack-kit).
"""
import hashlib
import html
import http.cookiejar
import json
import os
import re
import threading
import time
import urllib.error
import urllib.parse
import urllib.request

from . import config

SITE = "https://www.arabp2p.net"
NAME = "ArabP2P"
UA = "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"
CREDS_FILE = "arabic.json"
TTL = {"names": 7 * 86400, "search": 30 * 60, "titles": 6 * 3600}
LOGIN_EVERY = 1800
TIMEOUT = 12
MAX_NAMES = 3
DUB_MARKERS = ("مدبلج", "بالفصحى", "سبيستون", "دبلجه", "دبلجة")
DUB_MARKERS_RE = re.compile(r"\b(SPACETOON|DUBBED|AR[ .-]?DUB)\b", re.I)   # SHAHID/AWAAN are just the source
# ArabP2P categories (seen on the site, 2026-11): what the release is, which the title often doesn't say.
DUB_CATS = {"71", "98", "100", "102"}       # Arabic-dubbed anime (series, films, episodes) and dubbed series
SUB_CATS = {"99", "101", "103"}             # anime with Arabic subtitles
FILM_CATS = {"41", "98", "99"}              # Arabic films, dubbed and subtitled anime films
VIDEO_EXT = (".mkv", ".mp4", ".avi", ".m4v", ".ts", ".webm", ".mov", ".wmv", ".mpg", ".mpeg")


class ArabicError(Exception):
    pass


# ---------- credentials ($DATA/arabic.json, 0600) ----------
def load_creds():
    try:
        data = json.loads((config.data_dir() / CREDS_FILE).read_text())
    except (OSError, ValueError):
        data = {}
    return data if isinstance(data, dict) else {}


def save_creds(data):
    directory = config.data_dir()
    directory.mkdir(mode=0o700, parents=True, exist_ok=True)
    path, tmp = directory / CREDS_FILE, directory / (CREDS_FILE + ".tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as f:
        json.dump(data, f)
    os.chmod(tmp, 0o600)
    os.replace(tmp, path)


def update_creds(**changes):
    data = load_creds()
    for key, value in changes.items():
        if value is None:
            data.pop(key, None)
        else:
            data[key] = value
    save_creds(data)
    clear_cache()


def account():
    acc = load_creds().get("arabp2p")
    if isinstance(acc, dict) and isinstance(acc.get("user"), str) and isinstance(acc.get("pass"), str) \
            and acc["user"] and acc["pass"]:
        return acc
    return None


def tmdb_key():
    key = load_creds().get("tmdb_key")
    return key.strip() if isinstance(key, str) else ""


def status_public():
    acc = account()
    return {"arabp2p": bool(acc), "user": acc["user"] if acc else "", "tmdb": bool(tmdb_key()),
            "ready": bool(acc) and bool(config.read_key())}


def enabled():
    """Arabic sources need an ArabP2P login and a TorBox key (private torrents never go to the TV's own engine)."""
    return bool(account()) and bool(config.read_key())


# ---------- small disk cache ----------
def _cache_dir():
    return config.data_dir() / "cache" / "arabic"


def clear_cache():
    import shutil
    shutil.rmtree(_cache_dir(), ignore_errors=True)
    with _lock:
        _links.clear()


def cached(key, ttl, fn):
    path = _cache_dir() / (hashlib.sha256(json.dumps(key, ensure_ascii=False).encode()).hexdigest() + ".json")
    try:
        if time.time() - path.stat().st_mtime < ttl:
            return json.loads(path.read_text())
    except (OSError, ValueError):
        pass
    value = fn()
    if value is None:          # a failure: try again next time instead of remembering it
        return None
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(value, ensure_ascii=False))
        tmp.replace(path)
    except OSError:
        pass
    return value


def _json(url, headers=None, timeout=TIMEOUT):
    req = urllib.request.Request(url, headers=dict({"User-Agent": "NexusTV/1.0 (home media app)",
                                                     "Accept": "application/json"}, **(headers or {})))
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.loads(r.read(4_000_000))
    except (urllib.error.URLError, OSError, ValueError) as exc:
        raise ArabicError(f"Request failed: {exc}") from None


# ---------- Arabic text ----------
def norm(s):
    s = re.sub(r"[ؐ-ًؚ-ٰٟـ]", "", s or "")
    s = re.sub("[أإآٱ]", "ا", s)
    for a, b in (("ى", "ي"), ("ة", "ه"), ("ؤ", "و"), ("ئ", "ي")):
        s = s.replace(a, b)
    s = re.sub(r"[^\w\s]|_", " ", s.lower())
    return " " + " ".join(s.split()) + " "


def has_ar(s):
    return bool(re.search(r"[؀-ۿ]", s or ""))


ORDINALS = [("الاول", "الاولي"), ("الثاني", "الثانيه"), ("الثالث", "الثالثه"), ("الرابع", "الرابعه"),
            ("الخامس", "الخامسه"), ("السادس", "السادسه"), ("السابع", "السابعه"), ("الثامن", "الثامنه"),
            ("التاسع", "التاسعه"), ("العاشر", "العاشره")]
ORD = {w: i for i, pair in enumerate(ORDINALS, 1) for w in pair}
ORD.update({"الحادي": 1, "الحاديه": 1})    # الحادي عشر = 11th
YEAR = r"(?:19[5-9]\d|20[0-3]\d)"


def _span(nums):
    """Two numbers mean a range (E01 E16 → 1..16); more are a list."""
    nums = [int(x) for x in nums]
    if len(nums) == 2 and nums[1] > nums[0]:
        return list(range(nums[0], nums[1] + 1))
    return nums


def parse_episode(t):
    """(season, [episodes]) from an Arabic or English release title; [] episodes = whole season. None if unknown."""
    found = parse_release(t)
    return (found["season"], found["eps"]) if found else None


def parse_release(t):
    """{"season", "eps", "explicit"}: explicit is False when no season was named (episodes may be absolute)."""
    t = re.sub(r"\[\s*" + YEAR + r"\s*\]|\(\s*" + YEAR + r"\s*\)", " ", t)   # [2015] is a year, not an episode
    n = norm(t)
    full = " كامل " in n or " كامله " in n or bool(re.search(r"\b(complete|full[ .-]?season)\b", t, re.I))
    season = None
    m = re.search(r"(?<![a-z])s(\d{1,2})(?!\d)", t, re.I)
    if m:
        season = int(m.group(1))
    if season is None:
        m = re.search(r"\[\s*(?:(\d{1,2})\s*م|م\s*(\d{1,2}))\s*\]", t)
        if m:
            season = int(m.group(1) or m.group(2))
    if season is None:
        m = re.search(r" (?:الجزء|الموسم) (\S+) (عشر )?", n)
        if m:
            w = m.group(1)
            season = int(w) if w.isdigit() else ORD.get(w)
            if season and m.group(2) and not w.isdigit():
                season += 10
    explicit = season is not None
    eps = []
    m = (re.search(r"(?<![a-z])s\d{1,2}((?:\s*[-,]?\s*e\d{1,4})+)", t, re.I)
         or re.search(r"\[\s*(e\d{1,4}(?:\s*[-, ]\s*e\d{1,4})*)\s*\]", t, re.I))
    if m:
        eps = [int(x) for x in re.findall(r"e(\d{1,4})", m.group(1), re.I)]
        if len(eps) == 2 and "," not in m.group(1):
            eps = list(range(min(eps), max(eps) + 1))
    if not eps and not full:
        ha = re.findall(r"(?<![\u0600-\u06FF])ح\s?(\d{1,4})(?!\d)", t)          # (ح01 ح51), ح13
        m = (re.search(r"\[\s*(\d{1,4})(?:\s*[-~ ]+\s*(\d{1,4}))?\s*\]", t)    # [05], [62 92], [01 ~ 123]
             or re.search(r"\(\s*(\d{1,4})\s*[-~ ]+\s*(\d{1,4})\s*\)", t)      # (0284~0332), (1 1076)
             or re.search(r" الحلقات (\d{1,4}) (?:الي |و )?(\d{1,4}) ", n))       # الحلقات 11 12
        if ha:
            eps = _span(ha[:2]) if len(ha) <= 2 else [int(x) for x in ha]
        elif m:
            eps = _span([g for g in m.groups() if g])
        else:
            m = (re.search(r" (?:ال)?حلقه (\d{1,4}) ", n)
                 or re.search(r"(?<![\w.])(?:ep?\.?\s?|episode\s)(\d{1,4})(?!\d)", t, re.I))
            if m:
                eps = [int(m.group(1))]
    if full:
        eps = []
    if season is None and (eps or full):
        season = 1
    if season is None or len(eps) > 2000:
        return None
    return {"season": season, "eps": sorted(set(eps)), "explicit": explicit}


def file_episode(name):
    """Episode number in a file name inside a pack: S01E05, [05], E05, - 05, الحلقة 05, or a bare 05."""
    base = re.sub(r"\.[A-Za-z0-9]{2,4}$", "", name.rsplit("/", 1)[-1])
    base = re.sub(r"(2160|1080|720|576|480)p|[xh]\.?26[45]|\b(19|20)\d{2}\b", " ", base, flags=re.I)
    for pattern in (r"s\d{1,2}\s*e(\d{1,4})", r"\[\s*(\d{1,4})\s*\]", r"(?<![a-z])e(?:p|pisode)?\.?\s?(\d{1,4})(?!\d)",
                    r"\s-\s(\d{1,4})(?!\d)", r"حلق[ةه]\s*(\d{1,4})"):
        m = re.search(pattern, base, re.I)
        if m:
            return int(m.group(1))
    nums = re.findall(r"(?<!\d)(\d{1,4})(?!\d)", base)
    return int(nums[-1]) if nums else None


def is_dub(title, category=None):
    return str(category) in DUB_CATS or any(m in title for m in DUB_MARKERS) or bool(DUB_MARKERS_RE.search(title))


def subtitle_only(title, category=None):
    return str(category) in SUB_CATS or ("مترجم" in title and not is_dub(title, category))


def language(row):
    """'dub' (Arabic audio over a foreign show), 'subs' (Arabic subtitles) or 'arabic' (an Arabic original)."""
    title, cat = row.get("title", ""), row.get("category")
    if str(cat) in DUB_CATS:
        return "dub"
    if str(cat) in SUB_CATS:
        return "subs"
    if is_dub(title, cat):
        return "dub"
    return "subs" if subtitle_only(title, cat) else "arabic"


# ---------- names ----------
def _uniq_ar(cands):
    uniq = {}
    for c in cands:
        if isinstance(c, str) and has_ar(c) and norm(c).strip():
            uniq.setdefault(norm(c), c.strip())
    return sorted(uniq.values(), key=len)


def _tmdb(path, **q):
    key = tmdb_key()
    headers = {}
    if len(key) > 40:          # a v4 "Read Access Token"
        headers["Authorization"] = "Bearer " + key
    else:
        q["api_key"] = key
    return _json(f"https://api.themoviedb.org/3/{path}?{urllib.parse.urlencode(q)}", headers)


def check_tmdb_key(key):
    """ValueError: rejected; RuntimeError: TMDB unreachable."""
    headers = {"User-Agent": "NexusTV/1.0", "Accept": "application/json"}
    url = "https://api.themoviedb.org/3/configuration"
    if len(key) > 40:
        headers["Authorization"] = "Bearer " + key
    else:
        url += "?api_key=" + urllib.parse.quote(key)
    try:
        with urllib.request.urlopen(urllib.request.Request(url, headers=headers), timeout=8) as r:
            r.read(100_000)
    except urllib.error.HTTPError as exc:
        if exc.code in (401, 403):
            raise ValueError("TMDB rejected this key") from None
        raise RuntimeError("Couldn't reach TMDB to check the key") from None
    except (urllib.error.URLError, OSError):
        raise RuntimeError("Couldn't reach TMDB to check the key") from None


def _names_tmdb(kind, imdb_id):
    found = _tmdb(f"find/{imdb_id}", external_source="imdb_id")
    hits = found.get("tv_results" if kind == "series" else "movie_results") or []
    if not hits:
        return []
    tmdb_kind = "tv" if kind == "series" else "movie"
    d = _tmdb(f"{tmdb_kind}/{hits[0]['id']}", append_to_response="alternative_titles,translations")
    field = "name" if kind == "series" else "title"
    cands = [d.get("original_name"), d.get("original_title"), d.get(field)]
    alt = d.get("alternative_titles") or {}
    cands += [x.get("title") for x in alt.get("results") or alt.get("titles") or []]
    cands += [(x.get("data") or {}).get(field) for x in (d.get("translations") or {}).get("translations", [])
              if x.get("iso_639_1") == "ar"]
    return _uniq_ar(cands)


WIKIDATA = "https://www.wikidata.org/w/api.php?"


def _names_wikidata(imdb_id):
    s = _json(WIKIDATA + urllib.parse.urlencode({"action": "query", "list": "search", "format": "json", "srprop": "",
                                                 "srsearch": f"haswbstatement:P345={imdb_id}", "srlimit": 2}))
    ids = [x["title"] for x in (s.get("query") or {}).get("search", []) if re.match(r"^Q\d+$", x.get("title", ""))]
    if not ids:
        return []
    e = _json(WIKIDATA + urllib.parse.urlencode({"action": "wbgetentities", "ids": "|".join(ids), "format": "json",
                                                 "props": "labels|aliases", "languages": "ar|arz"}))
    cands = []
    for ent in (e.get("entities") or {}).values():
        cands += [v.get("value") for v in (ent.get("labels") or {}).values()]
        for lang in ("ar", "arz"):
            cands += [a.get("value") for a in (ent.get("aliases") or {}).get(lang, [])]
    return _uniq_ar(cands)


def arabic_names(kind, imdb_id):
    """Arabic titles for an IMDb id, shortest first (short names match more release titles)."""
    def load():
        try:
            return _names_tmdb(kind, imdb_id) if tmdb_key() else _names_wikidata(imdb_id)
        except (ArabicError, KeyError, TypeError, AttributeError):
            return None
    names = cached(("names", kind, imdb_id, bool(tmdb_key())), TTL["names"], load)
    return names or []


def find_titles(kind, query, limit=8):
    """IMDb ids for an Arabic query (Arabic search on the TV and phone)."""
    def load():
        ids = []
        try:
            if tmdb_key():
                path = "search/tv" if kind == "series" else "search/movie"
                for hit in (_tmdb(path, query=query, language="ar").get("results") or [])[:limit]:
                    ext = _tmdb(f"{'tv' if kind == 'series' else 'movie'}/{hit['id']}/external_ids")
                    if isinstance(ext.get("imdb_id"), str) and ext["imdb_id"].startswith("tt"):
                        ids.append(ext["imdb_id"])
            else:
                s = _json(WIKIDATA + urllib.parse.urlencode({"action": "query", "list": "search", "format": "json",
                                                             "srprop": "", "srlimit": limit * 2,
                                                             "srsearch": f"{query} haswbstatement:P345"}))
                qids = [x["title"] for x in (s.get("query") or {}).get("search", [])
                        if re.match(r"^Q\d+$", x.get("title", ""))]
                if qids:
                    e = _json(WIKIDATA + urllib.parse.urlencode({"action": "wbgetentities", "ids": "|".join(qids),
                                                                 "format": "json", "props": "claims"}))
                    for q in qids:
                        claims = ((e.get("entities") or {}).get(q) or {}).get("claims") or {}
                        for c in claims.get("P345", []):
                            v = ((c.get("mainsnak") or {}).get("datavalue") or {}).get("value")
                            if isinstance(v, str) and v.startswith("tt") and v not in ids:
                                ids.append(v)
                                break
        except (ArabicError, KeyError, TypeError, AttributeError):
            return None
        return ids[:limit]
    return cached(("titles", kind, query, bool(tmdb_key())), TTL["titles"], load) or []


# ---------- ArabP2P ----------
_lock = threading.Lock()
_jar = http.cookiejar.CookieJar()
_opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(_jar))
_state = {"login": 0.0, "user": ""}
_links = {}   # source url -> {"magnet": ..., "download": ...}


def _open(url, data=None, timeout=TIMEOUT):
    req = urllib.request.Request(url, data=data, headers={"User-Agent": UA, "Referer": SITE + "/"})
    with _opener.open(req, timeout=timeout) as r:
        return r.read(8_000_000)


def _logged_out(page):
    return 'name="pwd"' in page or "تحتاج لتسجيل عضوية" in page


def _login(acc):
    data = urllib.parse.urlencode({"uid": acc["user"], "pwd": acc["pass"]}).encode()
    _jar.clear()
    page = _open(SITE + "/index.php?page=login", data).decode("utf-8", "replace")
    if "logout.php" not in page:
        page = _open(SITE + "/index.php").decode("utf-8", "replace")
    if "logout.php" not in page:
        raise ValueError("ArabP2P didn't accept that username and password")
    _state.update(login=time.time(), user=acc["user"])


def check_login(user, password):
    """ValueError: rejected; RuntimeError: site unreachable."""
    with _lock:
        try:
            _login({"user": user, "pass": password})
        except (urllib.error.URLError, OSError):
            raise RuntimeError("Couldn't reach ArabP2P") from None


def _session_get(path):
    """GET a page with a live session, logging in (again) when needed. Caller holds _lock."""
    acc = account()
    if not acc:
        raise ArabicError("ArabP2P isn't set up")
    if time.time() - _state["login"] > LOGIN_EVERY or _state["user"] != acc["user"]:
        _login(acc)
    body = _open(SITE + "/" + path.lstrip("/"))
    if body[:1] != b"d" and _logged_out(body[:200_000].decode("utf-8", "replace")):
        _login(acc)
        body = _open(SITE + "/" + path.lstrip("/"))
    return body


UNITS = {"B": 1, "KB": 1e3, "KIB": 1024, "MB": 1e6, "MIB": 1024 ** 2, "GB": 1e9, "GIB": 1024 ** 3, "TB": 1e12, "TIB": 1024 ** 4}


def _size(text):
    m = re.search(r"([\d.,]+)\s*([KMGT]i?B)\b", text or "", re.I)
    if not m:
        return 0
    try:
        return int(float(m.group(1).replace(",", "")) * UNITS[m.group(2).upper()])
    except (ValueError, KeyError):
        return 0


def _text(fragment):
    return html.unescape(re.sub(r"<[^>]+>", " ", fragment or "")).strip()


def parse_results(page):
    """Rows of an ArabP2P torrents page: [{id, title, magnet, download, size, seeders, leechers, category}]."""
    if 'class="file-header"' in page:
        chunks = re.split(r'(?=<div class="file-header")', page)[1:]
    else:
        body = page.split('id="torrents_list_p"', 1)[-1]
        chunks = re.split(r"(?=<tr[\s>])", body)
    rows, seen = [], set()
    for chunk in chunks:
        title = re.search(r'<span class="tor_link">(.*?)</span>', chunk, re.S)
        details = re.search(r'href=["\'](index\.php\?page=torrent-details&(?:amp;)?id=([\w]+)[^"\']*)["\'][^>]*>(.*?)</a>',
                            chunk, re.S)
        title = _text(title.group(1)) if title else (_text(details.group(3)) if details else "")
        magnet = re.search(r"magnet:\?xt=urn:btih:[^'\"<> ]+", chunk)
        download = re.search(r'href=["\'](download\.php\?id=([\w]+)&(?:amp;)?f=[^"\']+)["\']', chunk)
        if not title or not (magnet or download):
            continue
        tid = (download.group(2) if download else None) or (details.group(2) if details else None) \
            or re.search(r"btih:([0-9a-zA-Z]+)", magnet.group(0)).group(1)
        if tid in seen:
            continue
        seen.add(tid)
        size = re.search(r'<span[^>]*class=["\']size["\'][^>]*>(.*?)</span>', chunk, re.S)
        seeds = re.search(r'title=["\']Seeders["\'][^>]*>(.*?)</span>', chunk, re.S)   # "▲ <a ...>11</a>"
        leech = re.search(r'title=["\']Leechers["\'][^>]*>(.*?)</span>', chunk, re.S)
        seeds, leech = (re.search(r"\d+", _text(x.group(1))) if x else None for x in (seeds, leech))
        cat = re.findall(r"page=torrents&(?:amp;)?category=(\d+)", chunk)
        rows.append({"id": tid, "title": title,
                     "magnet": html.unescape(magnet.group(0)) if magnet else None,
                     "download": html.unescape(download.group(1)) if download else None,
                     "size": _size(_text(size.group(1)) if size else _text(chunk)),
                     "seeders": int(seeds.group(0)) if seeds else 0,
                     "leechers": int(leech.group(0)) if leech else 0,
                     "category": cat[-1] if cat else None})
    return rows


def search_site(query, category=0):
    def load():
        qs = urllib.parse.urlencode({"page": "torrents", "search": query, "category": category, "active": 0})
        with _lock:
            try:
                page = _session_get("index.php?" + qs).decode("utf-8", "replace")
            except (urllib.error.URLError, OSError, ValueError, ArabicError):
                return None
        return parse_results(page)
    return cached(("search", query, category, account()["user"] if account() else ""), TTL["search"], load) or []


def source_url(row):
    if row.get("download"):
        return f"{SITE}/{row['download']}"
    return f"{SITE}/index.php?page=torrent-details&id={urllib.parse.quote(str(row['id']))}"


def owns(url):
    return isinstance(url, str) and url.startswith(SITE + "/")


def _remember(url, row):
    with _lock:
        _links[url] = {"magnet": row.get("magnet"), "download": row.get("download")}
    try:
        path = _cache_dir() / "links.json"
        try:
            known = json.loads(path.read_text())
        except (OSError, ValueError):
            known = {}
        known[url] = _links[url]
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(dict(list(known.items())[-500:])))
        tmp.replace(path)
    except OSError:
        pass


def fetch_torrent(url):
    """('magnet', str) or ('file', bytes) for a source URL from streams(). Downloads use the logged-in session."""
    if not owns(url):
        raise ArabicError("Not an ArabP2P source")
    link = _links.get(url)
    if link is None:
        try:
            link = json.loads((_cache_dir() / "links.json").read_text()).get(url)
        except (OSError, ValueError):
            link = None
    if link and link.get("magnet"):
        return "magnet", link["magnet"]
    rel = link.get("download") if link else None
    if not rel and "download.php?" in url:
        rel = url[len(SITE) + 1:]
    with _lock:
        try:
            if not rel:   # a details page: find the magnet or .torrent link on it
                page = _session_get(url[len(SITE) + 1:]).decode("utf-8", "replace")
                magnet = re.search(r"magnet:\?xt=urn:btih:[^'\"<> ]+", page)
                if magnet:
                    return "magnet", html.unescape(magnet.group(0))
                found = re.search(r'href=["\'](download\.php\?id=[\w]+&(?:amp;)?f=[^"\']+)["\']', page)
                if not found:
                    raise ArabicError("ArabP2P has no download for this release")
                rel = html.unescape(found.group(1))
            data = _session_get(rel)
        except (urllib.error.URLError, OSError) as exc:
            raise ArabicError(f"Couldn't download the torrent from ArabP2P: {exc}") from None
        except ValueError as exc:
            raise ArabicError(str(exc)) from None
    if not data.startswith(b"d"):
        raise ArabicError("ArabP2P didn't send a torrent file (is the account still active?)")
    return "file", data


# ---------- matching ----------
def _latin(s):
    return " " + " ".join(re.sub(r"[^a-z0-9]+", " ", (s or "").lower()).split()) + " "


# Words that may come before a show's name in a release title ("فيلم المحقق كونان", "انمي ون بيس").
LEADING = {"فيلم", "الفيلم", "انمي", "الانمي", "مسلسل", "المسلسل", "حلقات", "حلقه", "تكمله", "كرتون", "رسوم",
           "مترجم", "مدبلج", "مدبلجه", "جميع", "الحلقات"}
STOP = {"في", "من", "و", "ال", "the", "of", "a", "and", "ع", "على", "الي", "عن"}


def _leads(text, phrase, extra_ok):
    """phrase occurs in text and the word before it (if any) is allowed: 'ليجو ون بيس' is not One Piece."""
    start = text.find(phrase)
    while start >= 0:
        before = text[:start].split()
        if not before or extra_ok(before[-1]):
            return True
        start = text.find(phrase, start + 1)
    return False


def matches(title, names, english):
    n = norm(title)
    ok_ar = lambda w: w in LEADING or any(c.isdigit() for c in w) or not has_ar(w)
    if any(_leads(n, norm(name), ok_ar) for name in names if norm(name).strip()):
        return True
    words = _latin(english).strip()
    ok_en = lambda w: has_ar(w) or any(c.isdigit() for c in w)
    return len(words) >= 4 and _leads(_latin(title) if not has_ar(title) else " " + " ".join(
        re.sub(r"[^a-z0-9\u0600-\u06FF]+", " ", title.lower()).split()) + " ", f" {words} ", ok_en)


def movie_matches(title, names, english):
    """A film needs most words of its own title: one Conan film must not match all of them."""
    words_t = set(norm(title).split())
    has = lambda ws: sum(w in words_t for w in ws)
    for name in names:
        parts = re.split(r"\s*[:\-–|]\s*", name, maxsplit=1)
        head, sub = (parts + [""])[:2]
        head_w = [w for w in norm(head).split() if w not in STOP]
        sub_w = [w for w in norm(sub).split() if w not in STOP]
        if sub_w:   # "<series>: <film title>": the film's own words decide
            if has(head_w) == len(head_w) and has(sub_w) >= max(1, round(len(sub_w) * 0.6)):
                return True
        elif len(head_w) >= 2 and has(head_w) == len(head_w):
            return True
        elif len(head_w) == 1 and _leads(norm(title), norm(name), lambda w: w in LEADING or not has_ar(w)):
            return True
    words = _latin(english).strip()
    return len(words) >= 4 and f" {words} " in _latin(title)


def _years(title):
    return [int(y) for y in re.findall(r"(?<!\d)(19[5-9]\d|20[0-3]\d)(?!\d)", title)]


def year_fits(title, aired):
    """aired: (first, last) years the season (or show) aired; a year in the title must be near them."""
    years = _years(title)
    if not years or not aired:
        return True
    lo, hi = aired
    return any(lo - 1 <= y <= hi + 1 for y in years)


LANG_LABEL = {"dub": "Arabic dub", "subs": "Arabic subs", "arabic": "Arabic"}


def describe(row, lang, pack=False):
    parts = ["🇸🇦 " + LANG_LABEL[lang]] + (["Season pack"] if pack else [])
    if row.get("size"):
        parts.append(f"💾 {row['size'] / 1e9:.2f} GB")
    parts.append(f"👤 {row.get('seeders', 0)}")
    return row["title"] + "\n" + " ".join(parts)


MAX_QUERIES = 4


def search_terms(names):
    """The site matches the query as plain text: also try the part before ':' / '-' and the first two words
    ("المحقق كونان: نجمة المليون دولار" → "المحقق كونان")."""
    out = []
    for name in names:
        out.append(name)
        head = re.split(r"\s*[:\-–|(]\s*", name)[0].strip()
        if head and head != name:
            out.append(head)
        words = head.split()
        if len(words) > 2:
            out.append(" ".join(words[:2]))
    return list(dict.fromkeys(x for x in out if len(x) >= 3))


MAX_PACK = 80e9   # TorBox fetches a whole pack before one episode plays; beyond this it isn't worth it


def streams(kind, video_id, english="", year="", absolute=None, aired=None):
    """Stremio-style stream objects for ArabP2P releases of this movie/episode. [] when not set up.

    absolute: the episode's running number across all seasons; many anime releases count that way (E1156).
    aired: {"season": (first, last), "show": (first, last)} years, to tell a 1999 show from its 2023 remake."""
    aired = aired or {}
    if not enabled() or kind not in ("movie", "series"):
        return []
    imdb_id, season, episode = (video_id.split(":") + [None, None])[:3]
    season = int(season) if season else None
    episode = int(episode) if episode else None
    names = arabic_names(kind, imdb_id)[:MAX_NAMES]
    queries = list(dict.fromkeys(search_terms(names) + ([english] if english and len(english) >= 3 else [])))[:MAX_QUERIES]
    out, seen = [], set()
    for query in queries:
        for row in search_site(query):
            if row["id"] in seen:
                continue
            if not (movie_matches if kind == "movie" else matches)(row["title"], names, english):
                continue
            lang, pack, running = language(row), False, False
            film = str(row.get("category")) in FILM_CATS
            if film != (kind == "movie") and row.get("category") is not None:
                continue
            if kind == "series":
                found = parse_release(row["title"])
                if not found:
                    continue
                eps = found["eps"]
                same_season = found["season"] == season and (not eps or episode in eps)
                running = (not found["explicit"] and absolute and eps and absolute in eps
                           and not (found["season"] == season and episode in eps))
                if not (same_season or running):
                    continue
                if not year_fits(row["title"], aired.get("season") if found["explicit"] else aired.get("show")):
                    continue
                pack = not eps or len(eps) > 1
                if pack and row.get("size", 0) > MAX_PACK:
                    continue
            elif year:
                years = re.findall(r"(?<!\d)(19\d{2}|20\d{2})(?!\d)", row["title"])
                if years and all(abs(int(y) - int(year)) > 1 for y in years):
                    continue
                if re.search(r"(?<![a-z])s\d{1,2}e\d", row["title"], re.I):
                    continue
            seen.add(row["id"])
            url = source_url(row)
            _remember(url, row)
            out.append({"name": NAME, "title": describe(row, lang, pack), "url": url,
                        "behaviorHints": {"bingeGroup": "arabp2p-" + lang},
                        **({"nexusEpisodes": [n for n in ([absolute, episode] if running else [episode, absolute])
                                              if n]} if kind == "series" else {})})
    return out
