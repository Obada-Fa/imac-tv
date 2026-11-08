#!/usr/bin/env python3
"""Check the ArabP2P source by hand: sign in, search, show what Nexus TV would parse.

Asks for the login in the terminal (or reads two lines, username then password, from a pipe); nothing is saved except the result pages (to --save, optional),
so the parser can be checked against the real site. Usage:
    python3 scripts/arabic-check.py [query ...] [--save DIR]
"""
import getpass
import os
import sys
import tempfile
import urllib.parse
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "tvapp"))
os.environ.setdefault("IMACTV_DATA_DIR", tempfile.mkdtemp(prefix="arabic-check-"))   # never touches real settings

from media import arabic  # noqa: E402


def main():
    args = sys.argv[1:]
    save = None
    if "--save" in args:
        i = args.index("--save")
        save = Path(args[i + 1])
        del args[i:i + 2]
        save.mkdir(parents=True, exist_ok=True)
    queries = args or ["المحقق كونان", "ون بيس", "باب الحارة"]
    if sys.stdin.isatty():
        user = input("ArabP2P username: ").strip()
        password = getpass.getpass("ArabP2P password (not shown): ")
    else:   # from a login window: zenity --forms ... | arabic-check.py
        user, password = sys.stdin.readline().strip(), sys.stdin.readline().rstrip("\n")
    if not user or not password:
        print("No login given.")
        return 1
    try:
        arabic.check_login(user, password)
    except (ValueError, RuntimeError) as exc:
        print("Sign-in failed:", exc)
        return 1
    print("Signed in.\n")
    arabic.update_creds(arabp2p={"user": user, "pass": password})   # scratch data dir only
    first = None
    for q in queries:
        for cat in (0, 100):
            qs = urllib.parse.urlencode({"page": "torrents", "search": q, "category": cat, "active": 0})
            with arabic._lock:
                page = arabic._session_get("index.php?" + qs).decode("utf-8", "replace")
            if save:
                (save / f"search-{len(list(save.glob('search-*')))}-cat{cat}.html").write_text(page)
            rows = arabic.parse_results(page)
            print(f"'{q}' category {cat}: {len(rows)} results, page {len(page)} bytes")
            for r in rows[:6]:
                parsed = arabic.parse_episode(r["title"])
                print(f"   {r['title'][:70]}")
                print(f"      seeds {r['seeders']}  size {r['size'] / 1e9:.2f} GB  cat {r['category']}  "
                      f"{'magnet' if r['magnet'] else 'torrent'}  episode {parsed}  dub {arabic.is_dub(r['title'], r['category'])}")
                first = first or r
    if first and first.get("download"):
        with arabic._lock:
            data = arabic._session_get(first["download"])
        ok = data.startswith(b"d")
        print("\nTorrent download:", "OK" if ok else "FAILED (got a web page)",
              "· private torrent" if b"7:privatei1e" in data else "")
    arabic.update_creds(arabp2p=None)
    print("\nDone. Login forgotten." + (f" Pages saved in {save}" if save else ""))
    return 0


if __name__ == "__main__":
    sys.exit(main())
