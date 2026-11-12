#!/usr/bin/env python3
"""Pull the Discogs collection into the local play log database.

Fetches every release in the collection (folder 0 = "All"), each release's
tracklist, and its cover art, then matches past plays to the records you own.
Safe to run any time; runs nightly from a systemd timer.

Usage:
  discogs_sync.py              normal sync (new tracklists and covers only)
  discogs_sync.py --refresh    refetch every tracklist and cover
  discogs_sync.py --rematch    re-run matching over all plays, not just unmatched ones
"""
import argparse
import json
import logging
import os
import re
import sys
import time

import requests

from catalog import backfill, side_of
from store import COVER_DIR, DB_PATH, connect
import turntable_config
CFG = turntable_config.load()

API        = "https://api.discogs.com"
USER       = CFG["discogs"]["user"]
TOKEN      = CFG["secrets"]["discogs"]["token"]
USER_AGENT = "Turntable-1/1.0 (personal record collection sync)"
MIN_GAP    = 1.1      # seconds between requests; Discogs allows 60 a minute with a token
LENGTHS_PER_NIGHT = 40   # records looked up for missing track lengths per sync (lengths.py)
YEARS_PER_NIGHT = 60     # records looked up for their original release year per sync
DETAILS_PER_NIGHT = 80   # records whose credits, companies and matrix are fetched per sync

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("discogs-sync")


class Discogs:
    def __init__(self, token: str):
        self.s = requests.Session()
        self.s.headers.update({"User-Agent": USER_AGENT, "Authorization": f"Discogs token={token}"})
        self.last = 0.0

    def _wait(self):
        gap = time.monotonic() - self.last
        if gap < MIN_GAP:
            time.sleep(MIN_GAP - gap)
        self.last = time.monotonic()

    def get(self, url: str, params=None, raw: bool = False):
        for attempt in range(5):
            self._wait()
            r = self.s.get(url if url.startswith("http") else API + url, params=params, timeout=30)
            if r.status_code == 429:
                log.info("Discogs rate limit reached; waiting a minute")
                time.sleep(60)
                continue
            if r.status_code >= 500:
                time.sleep(5 * (attempt + 1))
                continue
            if r.status_code == 401:
                sys.exit("Discogs rejected the token (401). Check DISCOGS_TOKEN in /etc/turntable-id.env.")
            if r.status_code == 404 and "/users/" in url:
                sys.exit(f"Discogs has no user named '{USER}', or their collection is hidden. "
                         "Check DISCOGS_USER in /etc/turntable-id.env.")
            r.raise_for_status()
            remaining = int(r.headers.get("X-Discogs-Ratelimit-Remaining", "60"))
            if remaining < 3:
                time.sleep(30)
            return r.content if raw else r.json()
        raise RuntimeError(f"Discogs kept failing for {url}")


DISCOGS_NUM = re.compile(r"\s*\(\d+\)$")


def artist_name(artists: list) -> str:
    """Join Discogs artist credits the way the release shows them, without '(2)' suffixes."""
    out = ""
    for a in artists or []:
        name = DISCOGS_NUM.sub("", a.get("anv") or a.get("name", ""))
        join = (a.get("join") or "").strip()
        out += name + (f" {join} " if join and join != "," else (", " if join == "," else " "))
    return " ".join(out.split()).rstrip(",")


def format_text(formats: list) -> str:
    parts = []
    for f in formats or []:
        name = f.get("name", "")
        if f.get("qty") and str(f["qty"]) != "1":
            name = f"{f['qty']}x {name}"
        bits = [name] + (f.get("descriptions") or [])
        parts.append(", ".join(b for b in bits if b))
    return "; ".join(parts)


def flatten_tracklist(tracklist: list) -> list:
    """Discogs nests sub-tracks under 'index' entries and mixes in headings; keep real tracks."""
    out = []
    for t in tracklist or []:
        kind = t.get("type_", "track")
        if kind == "index":
            out.extend(s for s in t.get("sub_tracks", []) if s.get("type_", "track") == "track")
        elif kind == "track":
            out.append(t)
    return out


def condition_fields(api: Discogs) -> dict:
    """Your collection's custom field ids for media and sleeve condition, if any."""
    try:
        fields = api.get(f"/users/{USER}/collection/fields").get("fields", [])
    except Exception as e:
        log.warning("Collection fields: %s", e)
        return {}
    out = {}
    for f in fields:
        name = (f.get("name") or "").lower()
        if "media" in name and "condition" in name:
            out["media"] = f.get("id")
        elif "sleeve" in name and "condition" in name:
            out["sleeve"] = f.get("id")
    return out


def sync_collection(api: Discogs, db) -> list:
    seen, page, pages = set(), 1, 1
    now = int(time.time())
    cond = condition_fields(api)
    has_cond = "media_condition" in {c[1] for c in db.execute("PRAGMA table_info(releases)")}
    while page <= pages:
        data = api.get(f"/users/{USER}/collection/folders/0/releases",
                       {"per_page": 100, "page": page, "sort": "added", "sort_order": "desc"})
        pages = data.get("pagination", {}).get("pages", 1)
        with db:
            for item in data.get("releases", []):
                bi = item.get("basic_information", {})
                rid = bi.get("id")
                if not rid or rid in seen:
                    continue
                seen.add(rid)
                label = (bi.get("labels") or [{}])[0]
                db.execute(
                    "INSERT INTO releases (release_id, title, artist, year, label, catno, format, genres, "
                    "styles, cover_url, date_added, master_id, in_collection, synced_at) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1, ?) "
                    "ON CONFLICT(release_id) DO UPDATE SET title=excluded.title, artist=excluded.artist, "
                    "year=excluded.year, label=excluded.label, catno=excluded.catno, format=excluded.format, "
                    "genres=excluded.genres, styles=excluded.styles, cover_url=excluded.cover_url, "
                    "date_added=excluded.date_added, master_id=excluded.master_id, in_collection=1, "
                    "synced_at=excluded.synced_at",
                    (rid, bi.get("title", ""), artist_name(bi.get("artists")), bi.get("year") or None,
                     DISCOGS_NUM.sub("", label.get("name", "")), label.get("catno", ""),
                     format_text(bi.get("formats")), json.dumps(bi.get("genres") or []),
                     json.dumps(bi.get("styles") or []), bi.get("cover_image", ""),
                     item.get("date_added", ""), bi.get("master_id") or None, now),
                )
                if has_cond and cond:
                    notes = {n.get("field_id"): (n.get("value") or "").strip() for n in item.get("notes") or []}
                    db.execute("UPDATE releases SET media_condition = ?, sleeve_condition = ? WHERE release_id = ?",
                               (notes.get(cond.get("media")) or None, notes.get(cond.get("sleeve")) or None, rid))
        log.info("Collection page %d of %d: %d records so far", page, pages, len(seen))
        page += 1

    if seen:
        with db:
            marks = ",".join("?" * len(seen))
            removed = db.execute(
                f"UPDATE releases SET in_collection = 0 WHERE in_collection = 1 AND release_id NOT IN ({marks})",
                tuple(seen)).rowcount
        if removed:
            log.info("%d records are no longer in the collection", removed)
    return sorted(seen)


def sync_tracklists(api: Discogs, db, refresh: bool) -> None:
    cond = "in_collection = 1" + ("" if refresh else " AND tracks_synced = 0")
    ids = [r[0] for r in db.execute(f"SELECT release_id FROM releases WHERE {cond}")]
    if ids:
        log.info("Fetching %d tracklists (about %d min)", len(ids), max(1, round(len(ids) * MIN_GAP / 60)))
    for n, rid in enumerate(ids, 1):
        try:
            rel = api.get(f"/releases/{rid}")
        except requests.HTTPError as e:
            log.warning("Release %s: %s", rid, e)
            continue
        tracks = flatten_tracklist(rel.get("tracklist"))
        with db:
            db.execute("DELETE FROM tracks WHERE release_id = ?", (rid,))
            db.executemany(
                "INSERT INTO tracks (release_id, seq, position, side, title, duration) VALUES (?, ?, ?, ?, ?, ?)",
                [(rid, i, t.get("position", ""), side_of(t.get("position", "")), t.get("title", ""),
                  t.get("duration", "")) for i, t in enumerate(tracks)],
            )
            db.execute("UPDATE releases SET tracks_synced = 1 WHERE release_id = ?", (rid,))
        if n % 25 == 0:
            log.info("Tracklists: %d of %d", n, len(ids))


def sync_covers(api: Discogs, db, refresh: bool) -> None:
    os.makedirs(COVER_DIR, exist_ok=True)
    rows = db.execute("SELECT release_id, cover_url, cover_file FROM releases "
                      "WHERE in_collection = 1 AND cover_url != ''").fetchall()
    todo = [r for r in rows if refresh or not r[2] or not os.path.exists(os.path.join(COVER_DIR, r[2]))]
    if todo:
        log.info("Downloading %d covers", len(todo))
    for rid, url, _ in todo:
        name = f"d{rid}.jpg"
        path = os.path.join(COVER_DIR, name)
        try:
            data = api.get(url, raw=True)
        except (requests.RequestException, RuntimeError) as e:
            log.warning("Cover for %s: %s", rid, e)
            continue
        tmp = path + ".part"
        with open(tmp, "wb") as f:
            f.write(data)
        os.replace(tmp, path)
        with db:
            db.execute("UPDATE releases SET cover_file = ? WHERE release_id = ?", (name, rid))


def sync_original_years(api: Discogs, db, limit: int) -> int:
    """When each record's music first came out. A pressing's own year can be a reissue's (a 2019
    repress of a 1972 album says 2019); the Discogs master knows the original. Records with no
    master are their own original."""
    with db:
        db.execute("UPDATE releases SET original_year = year WHERE original_year IS NULL "
                   "AND (master_id IS NULL OR master_id = 0) AND year IS NOT NULL")
    todo = db.execute("SELECT release_id, master_id, year FROM releases WHERE original_year IS NULL AND master_id > 0 "
                      "AND in_collection = 1 LIMIT ?", (limit,)).fetchall()
    done = 0
    for rid, mid, year in todo:
        try:
            m = api.get(f"/masters/{mid}")
            orig = m.get("year") or year
        except Exception as e:
            log.warning("Master %s for release %s: %s", mid, rid, e)
            orig = year
        with db:
            db.execute("UPDATE releases SET original_year = ? WHERE release_id = ?", (orig or 0, rid))
        done += 1
    return done


def sync_wantlist(api: Discogs, db) -> int:
    """Your Discogs wantlist, for Ground Control to mention."""
    import ground
    ground.ensure_schema(db)
    rows, page, pages = [], 1, 1
    while page <= pages:
        data = api.get(f"/users/{USER}/wants", {"per_page": 100, "page": page})
        pages = data.get("pagination", {}).get("pages", 1)
        for w in data.get("wants", []):
            bi = w.get("basic_information", {})
            if bi.get("id"):
                rows.append((bi["id"], bi.get("master_id") or None, bi.get("title", ""), artist_name(bi.get("artists")),
                             bi.get("year") or None, w.get("date_added", "")))
        page += 1
    with db:
        db.execute("DELETE FROM wantlist")
        db.executemany("INSERT OR REPLACE INTO wantlist VALUES (?, ?, ?, ?, ?, ?)", rows)
    return len(rows)


def sync_details(api: Discogs, db, limit: int) -> int:
    """Release details (credits, companies, matrix) for records not yet cached, a batch a night."""
    import ground
    ground.ensure_schema(db)
    todo = [r[0] for r in db.execute(
        "SELECT r.release_id FROM releases r LEFT JOIN release_details d USING (release_id) WHERE r.in_collection = 1 "
        "AND (d.release_id IS NULL OR d.fetched_at < ?) ORDER BY d.fetched_at IS NOT NULL, r.release_id LIMIT ?",
        (int(time.time()) - 120 * 86400, limit))]
    for rid in todo:
        try:
            data = ground.slim_release(api.get(f"/releases/{rid}"))
        except Exception as e:
            log.warning("Details for %s: %s", rid, e)
            continue
        with db:
            db.execute("INSERT OR REPLACE INTO release_details (release_id, fetched_at, data) VALUES (?, ?, ?)",
                       (rid, int(time.time()), json.dumps(data)))
    return len(todo)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--refresh", action="store_true", help="refetch every tracklist and cover")
    ap.add_argument("--rematch", action="store_true", help="re-run matching over all plays")
    ap.add_argument("--no-lengths", action="store_true", help="skip looking up missing track lengths")
    args = ap.parse_args()

    if not USER or not TOKEN:
        sys.exit("Set DISCOGS_USER and DISCOGS_TOKEN in /etc/turntable-id.env first.")

    db = connect(DB_PATH)
    api = Discogs(TOKEN)
    started = time.time()

    ids = sync_collection(api, db)
    sync_tracklists(api, db, args.refresh)
    import lengths
    restored = lengths.apply_cached(db)          # a refresh rewrites tracklists: put found lengths back
    if restored:
        log.info("Restored %d track lengths found earlier from other sources", restored)
    sync_covers(api, db, args.refresh)
    # Ground Control's data: the wantlist, and release details for a batch of records
    try:
        log.info("Wantlist: %d records", sync_wantlist(api, db))
        log.info("Release details: fetched %d", sync_details(api, db, DETAILS_PER_NIGHT))
    except Exception as e:
        log.warning("Ground Control data: %s", e)
    # original release years (MusicBrainz and the Discogs master): records not yet confidently dated
    try:
        import years
        from lengths import Web
        counts = years.run(db, Web(), api, YEARS_PER_NIGHT)
        if counts:
            log.info("Original release years: %s", ", ".join(f"{n} from {s}" for s, n in counts.items()))
    except Exception as e:
        log.warning("Original release years: %s", e)
    if not args.no_lengths:
        stats = lengths.fill_missing(db, api, limit=LENGTHS_PER_NIGHT)
        if stats["records"]:
            log.info("Track lengths: checked %d records, found %d, %d still missing",
                     stats["records"], stats["filled"], stats["still_missing"])

    with db:
        db.execute("INSERT INTO meta (key, value) VALUES ('catalog_version', ?) "
                   "ON CONFLICT(key) DO UPDATE SET value = excluded.value", (str(int(time.time())),))
        db.execute("INSERT INTO meta (key, value) VALUES ('last_sync', ?) "
                   "ON CONFLICT(key) DO UPDATE SET value = excluded.value", (str(int(time.time())),))
    backfill(db, log, rematch_all=args.rematch)
    try:                                              # a new pressing may have joined an album: its plays move onto the album
        import albums
        n = albums.fold_plays(db)
        if n:
            log.info("Moved %d plays onto their album's first pressing", n)
    except Exception as e:
        log.warning("Folding plays onto albums: %s", e)
    log.info("Sync finished: %d records in %.0f s", len(ids), time.time() - started)


if __name__ == "__main__":
    main()
