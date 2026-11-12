#!/usr/bin/env python3
"""Original release years: when each record's music first came out, not when your copy was pressed.

Sources, all checked for each record:
  musicbrainz      the release group's first release date (the album across every edition
                   worldwide). Only a confident match counts: MusicBrainz scores it 90 or more,
                   and both the title (edition words like "Deluxe" ignored) and the artist match.
  discogs master   the year on the record's Discogs master, when it has one.
  pressing         the year of your own pressing, only when neither of the above is known.

The earliest year among MusicBrainz and the Discogs master wins (a reissue can't be older than
the original, but a master listed under a later reissue can be). The source is saved alongside.

  years.py                      check records without a confident year yet (up to --limit)
  years.py --all                recheck every record (a few seconds each: MusicBrainz asks for 1 a second)
  years.py --dry-run            show what would change, change nothing
  years.py --set 1234567 1972   fix one record by hand (release id, year); never overwritten
  years.py --show               list every record's year and where it came from
"""
import argparse
import datetime
import logging
import os
import re
import sqlite3
import sys
from difflib import SequenceMatcher
import turntable_config
CFG = turntable_config.load()

log = logging.getLogger("turntable-years")
DB_PATH = CFG["paths"]["db"]
MB_API = "https://musicbrainz.org/ws/2"

EDITION = re.compile(r"\s*[\(\[][^\)\]]*\b(deluxe|edition|remaster(ed)?|anniversary|expanded|bonus|version|mono|stereo|"
                     r"reissue|special|collector'?s?|legacy|explicit|clean)\b[^\)\]]*[\)\]]", re.I)


def album_norm(t: str) -> str:
    t = EDITION.sub("", t or "").lower().replace("&", " and ")
    t = re.sub(r"[^a-z0-9 ]+", " ", t)
    return re.sub(r"\s+", " ", t).strip()


def artist_norm(a: str) -> str:
    a = re.sub(r"\s*\(\d+\)$", "", a or "")            # Discogs disambiguation: "Nas (2)"
    a = a.lower().replace("&", " and ")
    a = re.sub(r"^the\s+", "", a)
    return re.sub(r"\s+", " ", re.sub(r"[^a-z0-9 ]+", " ", a)).strip()


def close(a: str, b: str, ratio: float) -> bool:
    return bool(a and b) and (a == b or SequenceMatcher(None, a, b).ratio() >= ratio)


def year_of(date: str):
    m = re.match(r"\s*(\d{4})", date or "")
    y = int(m.group(1)) if m else 0
    return y if 1900 <= y <= datetime.date.today().year + 1 else None


def from_musicbrainz(web, title: str, artist: str):
    """The first release year of the matching MusicBrainz release group, or None."""
    t, a = album_norm(title), artist_norm(artist)
    if not t:
        return None
    q = f'releasegroup:"{album_norm(title) or title}" AND artist:"{artist}"'
    found = web.get(f"{MB_API}/release-group", {"query": q, "fmt": "json", "limit": 10}) or {}
    best = None
    for g in found.get("release-groups", []):
        if int(g.get("score", 0)) < 90 or not close(album_norm(g.get("title", "")), t, 0.9):
            continue
        credit = " ".join(c.get("name", "") + c.get("joinphrase", "") for c in g.get("artist-credit", []))
        names = [artist_norm(credit)] + [artist_norm(c.get("artist", {}).get("name", "")) for c in g.get("artist-credit", [])]
        if not any(close(n, a, 0.85) for n in names):
            continue
        y = year_of(g.get("first-release-date"))
        if y and (best is None or y < best):
            best = y
    return best


def from_discogs_master(api, master_id):
    if not master_id:
        return None
    try:
        m = api.get(f"/masters/{master_id}")
    except Exception as e:
        log.warning("Discogs master %s: %s", master_id, e)
        return None
    return year_of(str(m.get("year") or ""))


def decide(mb, master, pressing):
    """The year and where it came from."""
    credible = [(y, rank, s) for y, rank, s in ((mb, 0, "musicbrainz"), (master, 1, "discogs master")) if y]
    if credible:
        y, _, src = min(credible)          # the earliest; on a tie, MusicBrainz
        return y, src
    if pressing:
        return pressing, "pressing"
    return None, None


def ensure_columns(db):
    cols = {c[1] for c in db.execute("PRAGMA table_info(releases)")}
    with db:
        if "original_year" not in cols:
            db.execute("ALTER TABLE releases ADD COLUMN original_year INTEGER")
        if "original_year_source" not in cols:
            db.execute("ALTER TABLE releases ADD COLUMN original_year_source TEXT")
        if "master_id" not in cols:
            db.execute("ALTER TABLE releases ADD COLUMN master_id INTEGER")


def run(db, web, api, limit: int = 60, everything: bool = False, dry_run: bool = False) -> dict:
    """Check records' original years. Returns counts by source."""
    ensure_columns(db)
    where = "in_collection = 1 AND COALESCE(original_year_source, '') != 'manual'"
    if not everything:      # records without a confident year: never checked, or only the pressing's
        where += " AND COALESCE(original_year_source, '') IN ('', 'pressing')"
    rows = db.execute(f"SELECT release_id, title, artist, year, master_id, original_year, original_year_source "
                      f"FROM releases WHERE {where} ORDER BY release_id LIMIT ?", (limit,)).fetchall()
    counts = {}
    for rid, title, artist, pressing, master_id, old, old_src in rows:
        mb = None
        try:
            mb = from_musicbrainz(web, title, artist)
        except Exception as e:
            log.warning("MusicBrainz for %s by %s: %s", title, artist, e)
        master = from_discogs_master(api, master_id) if api else None
        year, src = decide(mb, master, year_of(str(pressing or "")))
        counts[src or "unknown"] = counts.get(src or "unknown", 0) + 1
        if year != old or src != old_src:
            log.info("%s by %s: %s (%s)%s", title, artist, year, src,
                     f", was {old} ({old_src})" if old else "")
        if not dry_run:
            with db:
                db.execute("UPDATE releases SET original_year = ?, original_year_source = ? WHERE release_id = ?",
                           (year or 0, src or "pressing", rid))
    return counts


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--all", action="store_true", help="recheck every record, not just ones without a confident year")
    ap.add_argument("--limit", type=int, default=100000, help="most records to check this run")
    ap.add_argument("--dry-run", action="store_true", help="show what would change, change nothing")
    ap.add_argument("--set", nargs=2, metavar=("RELEASE_ID", "YEAR"), help="fix one record's year by hand")
    ap.add_argument("--show", action="store_true", help="list every record's year and its source")
    args = ap.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    db = sqlite3.connect(DB_PATH, timeout=10)
    ensure_columns(db)
    if args.set:
        rid, y = int(args.set[0]), int(args.set[1])
        with db:
            n = db.execute("UPDATE releases SET original_year = ?, original_year_source = 'manual' WHERE release_id = ?",
                           (y, rid)).rowcount
        print(f"Set release {rid} to {y}." if n else f"No release {rid} in the collection.")
        return
    if args.show:
        for rid, title, artist, pressing, y, src in db.execute(
                "SELECT release_id, title, artist, year, original_year, original_year_source FROM releases "
                "WHERE in_collection = 1 ORDER BY artist, title"):
            print(f"{rid:>9}  {(y or '?')!s:>4}  {src or 'not checked':<15} {artist} - {title}"
                  + (f"   (your pressing: {pressing})" if pressing and y and pressing != y else ""))
        return
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    from lengths import Web
    api = None
    token = CFG["secrets"]["discogs"]["token"]
    if token:
        from discogs_sync import Discogs
        api = Discogs(token)
    counts = run(db, Web(), api, args.limit, args.all, args.dry_run)
    total = sum(counts.values())
    print(f"Checked {total} records" + (" (dry run, nothing saved)" if args.dry_run else "") + ": "
          + ", ".join(f"{n} from {s}" for s, n in sorted(counts.items(), key=lambda kv: -kv[1])))


if __name__ == "__main__":
    main()
