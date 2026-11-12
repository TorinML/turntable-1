#!/usr/bin/env python3
"""Fill in track lengths that Discogs doesn't list for your pressing.

The side timeline, the flip timer and the gap-filling all need track lengths, and some
Discogs pages leave them blank. This looks for the missing ones elsewhere, in order:

  1. discogs-master  other versions of the same album on Discogs (same token, nothing new)
  2. musicbrainz     MusicBrainz, matched by barcode, then catalog number, then title
  3. itunes          Apple's iTunes Search API, a last resort for popular albums

Only blanks are filled: a length Discogs lists is never touched. A length is only taken
when that track's title clearly matches, from a source whose tracklist matches the album
as a whole. Lengths found are kept in their own table (track_lengths), so a Discogs
refresh can't lose them; records checked without success are retried after 30 days.

Usage:
  lengths.py --dry-run              show what would be filled, change nothing
  lengths.py                        fill missing lengths across the collection
  lengths.py --release 1234567      just one record (always rechecked)
  lengths.py --recheck              include records checked in the last 30 days
  lengths.py --limit 40             stop after this many records
  lengths.py --sources musicbrainz,itunes   choose which sources to use
"""
import argparse
import logging
import os
import re
import sys
import time
from difflib import SequenceMatcher

import requests
import turntable_config
CFG = turntable_config.load()

log = logging.getLogger("lengths")

RECHECK_DAYS = 30
MB_API = "https://musicbrainz.org/ws/2"
ITUNES = "https://itunes.apple.com"
MB_CONTACT = CFG["identify"]["musicbrainz_contact"] or "personal record collection"
USER_AGENT = f"Turntable-1/1.6 ( {MB_CONTACT} )"
ALL_SOURCES = ("discogs-master", "musicbrainz", "itunes")


# --- matching ------------------------------------------------------------------------

EXTRA = re.compile(r"\s*[\(\[][^\)\]]*\b(remaster(ed)?|mono|stereo|version|mix|edit|live|demo|bonus|single)\b[^\)\]]*[\)\]]", re.I)


def norm(title: str) -> str:
    t = EXTRA.sub("", title or "").lower()
    t = t.replace("&", " and ")
    t = re.sub(r"\s+-\s+(\d{4} )?remaster.*$", "", t)
    t = re.sub(r"[^a-z0-9 ]+", " ", t)
    return re.sub(r"\s+", " ", t).strip()


def same_title(a: str, b: str) -> bool:
    x, y = norm(a), norm(b)
    if not x or not y:
        return False
    return x == y or SequenceMatcher(None, x, y).ratio() >= 0.88


def to_secs(d) -> int:
    """'3:12' -> 192; milliseconds (ints over 20000) -> seconds; 0 when unusable."""
    if isinstance(d, (int, float)):
        s = int(round(d / 1000)) if d > 20000 else int(d)
    else:
        m = re.fullmatch(r"\s*(?:(\d+):)?(\d+)[:.](\d{1,2})\s*", d or "")
        if not m:
            return 0
        s = int(m.group(1) or 0) * 3600 + int(m.group(2)) * 60 + int(m.group(3))
    return s if 20 <= s <= 3600 else 0


def fmt(secs: int) -> str:
    h, rest = divmod(int(secs), 3600)
    m, s = divmod(rest, 60)
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}"


def match(ours: list, theirs: list) -> dict:
    """Lengths for our blank tracks from a candidate tracklist: {position: secs}.

    `ours`: [{"position", "title", "duration"}]; `theirs`: [(position, title, secs)].
    The candidate must be the same album: at least half of our titles must appear in it.
    Each blank then needs a title match, preferring the same position when titles repeat."""
    if not ours or not theirs:
        return {}
    hits = sum(1 for t in ours if any(same_title(t["title"], th[1]) for th in theirs))
    if hits < max(2, len(ours) // 2):
        return {}
    out = {}
    for t in ours:
        if t["duration"]:
            continue
        cands = [th for th in theirs if th[2] and same_title(t["title"], th[1])]
        if not cands:
            continue
        exact = [th for th in cands if (th[0] or "").upper() == t["position"].upper()]
        out[t["position"]] = (exact or cands)[0][2]
    return out


# --- sources -------------------------------------------------------------------------

class Web:
    """Polite JSON fetching: a minimum gap per host, retries, and None for not found."""

    def __init__(self, gaps=None):
        self.s = requests.Session()
        self.s.headers.update({"User-Agent": USER_AGENT, "Accept": "application/json"})
        self.gaps = gaps or {"musicbrainz.org": 1.2, "itunes.apple.com": 3.5}
        self.last = {}

    def get(self, url: str, params=None):
        host = url.split("/")[2]
        wait = self.gaps.get(host, 1.0) - (time.monotonic() - self.last.get(host, 0))
        if wait > 0:
            time.sleep(wait)
        for attempt in range(3):
            self.last[host] = time.monotonic()
            r = self.s.get(url, params=params, timeout=20)
            if r.status_code == 404:
                return None
            if r.status_code in (429, 503):
                time.sleep(10 * (attempt + 1))
                continue
            r.raise_for_status()
            return r.json()
        return None


def discogs_tracks(rel: dict) -> list:
    from discogs_sync import flatten_tracklist
    return [(t.get("position", ""), t.get("title", ""), to_secs(t.get("duration", "")))
            for t in flatten_tracklist(rel.get("tracklist"))]


def from_discogs_master(api, rel: dict, ours: list):
    """Other versions of the same album on Discogs: the main release, then a few vinyl versions."""
    master = rel.get("master_id")
    if not master:
        return
    m = api.get(f"/masters/{master}")
    tried = {rel.get("id")}
    main = m.get("main_release")
    if main and main not in tried:
        tried.add(main)
        yield f"Discogs release {main}", discogs_tracks(api.get(f"/releases/{main}"))
    versions = api.get(f"/masters/{master}/versions", {"per_page": 50}).get("versions", [])
    vinyl = [v for v in versions if "vinyl" in (v.get("format") or "").lower() or "lp" in (v.get("format") or "").lower()]
    for v in (vinyl or versions)[:6]:
        if v.get("id") in tried:
            continue
        tried.add(v.get("id"))
        if len(tried) > 4:
            break
        yield f"Discogs release {v['id']}", discogs_tracks(api.get(f"/releases/{v['id']}"))


def mb_tracks(release: dict) -> list:
    out = []
    for medium in release.get("media", []) or []:
        for t in medium.get("tracks", []) or []:
            out.append((t.get("number", ""), t.get("title", ""), to_secs(t.get("length") or 0)))
    return out


def from_musicbrainz(web: Web, rel: dict, ours: list):
    """MusicBrainz releases found by barcode, then catalog number, then artist and title."""
    artist, title = rel.get("_artist", ""), rel.get("title", "")
    barcodes = [re.sub(r"\D", "", i.get("value", "")) for i in rel.get("identifiers", []) or []
                if i.get("type") == "Barcode"]
    catnos = [l.get("catno", "") for l in rel.get("labels", []) or [] if l.get("catno") and l.get("catno") != "none"]
    queries = [f"barcode:{b}" for b in barcodes if len(b) >= 8]
    queries += [f'catno:"{c}" AND artist:"{artist}"' for c in catnos[:1]]
    queries.append(f'release:"{title}" AND artist:"{artist}"')
    seen = set()
    for q in queries:
        found = web.get(f"{MB_API}/release", {"query": q, "fmt": "json", "limit": 5}) or {}
        for r in found.get("releases", [])[:3]:
            if r.get("id") in seen or int(r.get("score", 0)) < 85:
                continue
            seen.add(r["id"])
            full = web.get(f"{MB_API}/release/{r['id']}", {"inc": "recordings", "fmt": "json"})
            if full:
                yield f"MusicBrainz {r['id']}", mb_tracks(full)


def from_itunes(web: Web, rel: dict, ours: list):
    """Albums on the iTunes store with a matching name and artist."""
    artist, title = rel.get("_artist", ""), rel.get("title", "")
    found = web.get(f"{ITUNES}/search", {"term": f"{artist} {title}", "entity": "album", "limit": 5}) or {}
    for a in found.get("results", [])[:3]:
        if not same_title(a.get("collectionName", ""), title):
            continue
        songs = web.get(f"{ITUNES}/lookup", {"id": a["collectionId"], "entity": "song"}) or {}
        tracks = [(str(s.get("trackNumber", "")), s.get("trackName", ""), to_secs(s.get("trackTimeMillis") or 0))
                  for s in songs.get("results", []) if s.get("wrapperType") == "track"]
        yield f"iTunes {a['collectionId']}", tracks


# --- the filler ----------------------------------------------------------------------------

def our_tracks(db, rid: int) -> list:
    return [{"position": r[0], "title": r[1], "duration": r[2]}
            for r in db.execute("SELECT position, title, duration FROM tracks WHERE release_id = ? ORDER BY seq", (rid,))]


def fill_release(db, api, web, rid: int, sources=ALL_SOURCES) -> list:
    """Find lengths for one record's blank tracks. Returns [(position, title, secs, source, where)]."""
    ours = our_tracks(db, rid)
    blank = {t["position"] for t in ours if not t["duration"]}
    if not blank:
        return []
    rel = api.get(f"/releases/{rid}")
    artist = db.execute("SELECT artist FROM releases WHERE release_id = ?", (rid,)).fetchone()
    rel["_artist"] = artist[0] if artist else ""
    found = []
    makers = {"discogs-master": lambda: from_discogs_master(api, rel, ours),
              "musicbrainz": lambda: from_musicbrainz(web, rel, ours),
              "itunes": lambda: from_itunes(web, rel, ours)}
    for source in sources:
        if not blank:
            break
        try:
            for where, theirs in makers[source]():
                got = match([t for t in ours if t["position"] in blank or t["duration"]], theirs)
                for pos, secs in got.items():
                    if pos in blank:
                        title = next(t["title"] for t in ours if t["position"] == pos)
                        found.append((pos, title, secs, source, where))
                        blank.discard(pos)
                if not blank:
                    break
        except (requests.RequestException, RuntimeError, KeyError, ValueError) as e:
            log.warning("Record %s: %s lookup failed (%s)", rid, source, e)
    return found


def save(db, rid: int, found: list) -> None:
    now = int(time.time())
    with db:
        for pos, title, secs, source, _ in found:
            db.execute("INSERT INTO track_lengths (release_id, position, title, secs, source, found_at) VALUES (?, ?, ?, ?, ?, ?) "
                       "ON CONFLICT(release_id, position) DO UPDATE SET title = excluded.title, secs = excluded.secs, "
                       "source = excluded.source, found_at = excluded.found_at", (rid, pos, title, secs, source, now))
        missing = db.execute("SELECT COUNT(*) FROM tracks WHERE release_id = ? AND duration = ''", (rid,)).fetchone()[0]
        db.execute("INSERT INTO length_checks (release_id, checked_at, missing) VALUES (?, ?, ?) "
                   "ON CONFLICT(release_id) DO UPDATE SET checked_at = excluded.checked_at, missing = excluded.missing",
                   (rid, now, missing - len(found)))
    apply_cached(db, rid)


def apply_cached(db, rid: int = None) -> int:
    """Copy found lengths into blank tracks. Called after every Discogs sync too, since a
    refresh rewrites tracklists. Never overwrites a length Discogs itself lists."""
    where, args = ("AND t.release_id = ?", (rid,)) if rid else ("", ())
    rows = db.execute(
        "SELECT t.release_id, t.position, l.secs, l.source, t.title, l.title FROM tracks t "
        "JOIN track_lengths l ON l.release_id = t.release_id AND l.position = t.position "
        f"WHERE t.duration = '' {where}", args).fetchall()
    n = 0
    with db:
        for r_id, pos, secs, source, title, saved_title in rows:
            if saved_title and not same_title(title, saved_title):
                continue          # the tracklist changed under us; don't trust the old length
            db.execute("UPDATE tracks SET duration = ?, duration_source = ? WHERE release_id = ? AND position = ? AND duration = ''",
                       (fmt(secs), source, r_id, pos))
            n += 1
    return n


def candidates(db, recheck: bool, limit: int = None) -> list:
    cut = 0 if recheck else int(time.time()) - RECHECK_DAYS * 86400
    rows = db.execute(
        "SELECT r.release_id, r.title, r.artist, SUM(t.duration = '') AS blank FROM releases r "
        "JOIN tracks t ON t.release_id = r.release_id "
        "LEFT JOIN length_checks c ON c.release_id = r.release_id "
        "WHERE r.in_collection = 1 AND (c.checked_at IS NULL OR c.checked_at < ?) "
        "GROUP BY r.release_id HAVING blank > 0 ORDER BY blank DESC", (cut,)).fetchall()
    return rows[:limit] if limit else rows


def fill_missing(db, api, limit: int = None, dry_run: bool = False, recheck: bool = False,
                 only: int = None, sources=ALL_SOURCES, web: Web = None, verbose: bool = False) -> dict:
    web = web or Web()
    todo = [r for r in candidates(db, True) if r[0] == only] if only else candidates(db, recheck, limit)
    stats = {"records": 0, "filled": 0, "still_missing": 0}
    for rid, title, artist, blank in todo:
        found = fill_release(db, api, web, rid, sources)
        stats["records"] += 1
        stats["filled"] += len(found)
        stats["still_missing"] += blank - len(found)
        if verbose:
            print(f"{artist} - {title}: {len(found)} of {blank} missing lengths found")
            for pos, t, secs, source, where in found:
                print(f"    + {pos:<4} {t[:46]:<46} {fmt(secs):>6}  from {where}")
        if not dry_run:
            save(db, rid, found)
    return stats


def main() -> None:
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    from store import DB_PATH, connect
    from discogs_sync import Discogs, TOKEN

    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--dry-run", action="store_true", help="show what would be filled, change nothing")
    ap.add_argument("--release", type=int, help="just this Discogs release id")
    ap.add_argument("--recheck", action="store_true", help="include records checked in the last 30 days")
    ap.add_argument("--limit", type=int, help="stop after this many records")
    ap.add_argument("--sources", default=",".join(ALL_SOURCES), help="comma-separated, in order")
    args = ap.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    if not TOKEN:
        sys.exit("Set DISCOGS_TOKEN in /etc/turntable-id.env first (the master-release lookup uses it).")
    sources = [s.strip() for s in args.sources.split(",") if s.strip() in ALL_SOURCES]

    db = connect(DB_PATH)
    print("Dry run, nothing will change:" if args.dry_run else "Filling missing track lengths:")
    stats = fill_missing(db, Discogs(TOKEN), limit=args.limit, dry_run=args.dry_run, recheck=args.recheck,
                         only=args.release, sources=sources, verbose=True)
    print(f"\n{stats['records']} records checked: {stats['filled']} lengths found, "
          f"{stats['still_missing']} still missing.")
    if stats["filled"] and not args.dry_run:
        print("Next: re-run the gap-filling so past plays benefit, with  ./bin/python infer.py --history --dry-run")


if __name__ == "__main__":
    main()
