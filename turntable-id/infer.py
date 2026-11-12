#!/usr/bin/env python3
"""Fill in tracks Shazam missed, using the record's Discogs tracklist.

Shazam often misses tracks on a record, especially obscure ones, and occasionally
"hears" something that isn't playing at all. This module uses context to fix both:

  sandwiched   a gap between two tagged tracks of the same record is filled with the
               tracks that come between them, if their lengths fit the gap
  backward     unidentified audio just before a tagged track is matched against the
               tracks before it on that side (needs the check log, so not for old history)
  forward      audio that carries on after a tagged track, with no silence, is filled
               with the following tracks on the side (also needs the check log)
  quarantine   a brief one-off identification that doesn't fit the record around it
               (a different record, or a track out of order) is moved aside to
               plays_quarantine. Nothing is deleted; it can be restored.

Inferred tracks are stored as ordinary plays with inferred = 1. Running reconcile again
rebuilds them from scratch, so it is safe to run as often as you like.

Usage (history tool):
  infer.py --history --dry-run    show what would change across all past plays
  infer.py --history              apply it
  infer.py --since 2026-10-06     only from that date
  infer.py --restore              move every quarantined play back
"""
import argparse
import logging
import os
import re
import sys
import time
from collections import Counter

log = logging.getLogger("turntable-id")

SAMPLE_SECS = 8
CHECK_GAP = 50       # checks further apart than this break a run of continuous audio
PLAYS_GAP = 300      # old history (no check log): plays this close together count as continuous
SAME_RECORD_GAP = 1500   # ...or this close, when the next tagged track is from the same record
BLIP_SECS = 45       # a play heard for no longer than this may be a false identification
FLIP_SECS = 300      # time allowed for flipping or changing a record inside a gap
SLACK = 25           # live inference waits this long past a track's length before moving on


def gap_ends(db, lo: float, hi: float) -> list:
    """Ends of the silences between tracks that telemetry heard, between two times."""
    try:
        return [r[0] for r in db.execute("SELECT end FROM audio_gaps WHERE end BETWEEN ? AND ? ORDER BY end", (lo, hi))]
    except Exception:
        return []                    # no telemetry, or no gaps heard yet


def gap_near(db, t: float, before: float, after: float, not_before: float = 0):
    """The gap end nearest `t`, within `before` seconds earlier or `after` later (and not before
    `not_before`), or None."""
    ends = [e for e in gap_ends(db, t - before, t + after) if e >= not_before]
    return min(ends, key=lambda e: abs(e - t)) if ends else None


def to_secs(d: str):
    """'3:12' -> 192, '1:02:03' -> 3723, '' -> None."""
    m = re.fullmatch(r"\s*(?:(\d+):)?(\d+)[:.](\d{1,2})\s*", d or "")
    if not m:
        return None
    h, mi, s = int(m.group(1) or 0), int(m.group(2)), int(m.group(3))
    total = h * 3600 + mi * 60 + s
    return total if 20 <= total <= 3600 else None


def tol(x: float) -> float:
    return max(40.0, 0.15 * x)


_tracklists = {}


def tracklist(db, release_id: int) -> list:
    if release_id not in _tracklists:
        rows = db.execute("SELECT seq, position, side, title, duration FROM tracks WHERE release_id = ? ORDER BY seq",
                          (release_id,)).fetchall()
        _tracklists[release_id] = [{"seq": r[0], "position": r[1], "side": r[2], "title": r[3], "secs": to_secs(r[4])}
                                   for r in rows]
    return _tracklists[release_id]


def forget_tracklists() -> None:
    _tracklists.clear()


def index_of(tl: list, position: str):
    for i, t in enumerate(tl):
        if t["position"] == position:
            return i
    return None


def next_track(db, release_id: int, position: str, same_side: bool = True):
    tl = tracklist(db, release_id)
    i = index_of(tl, position)
    if i is None or i + 1 >= len(tl):
        return None
    nxt = tl[i + 1]
    if same_side and nxt["side"] != tl[i]["side"]:
        return None
    return nxt


def follows(db, release_id: int, cur_position: str, new_position: str) -> bool:
    """True if new_position comes later on the same side as cur_position (the next track or a skip ahead)."""
    tl = tracklist(db, release_id)
    i, j = index_of(tl, cur_position), index_of(tl, new_position)
    return i is not None and j is not None and j > i and tl[i]["side"] == tl[j]["side"]


# --- runs of continuous audio -----------------------------------------------------------

def runs_from_checks(checks: list) -> list:
    runs, cur, last = [], None, None
    for at, kind in checks:
        if kind == "silence":
            if cur:
                runs.append(cur)
            cur = None
        else:
            if cur and last is not None and at - last > CHECK_GAP:
                runs.append(cur)
                cur = None
            if cur is None:
                cur = {"start": at, "end": at + SAMPLE_SECS, "edges": True}
            cur["end"] = at + SAMPLE_SECS
        last = at
    if cur:
        runs.append(cur)
    return runs


def runs_from_plays(plays: list) -> list:
    """Old history has no check log, so plays close together stand in for continuous audio.
    The edges of these runs aren't trustworthy, so only sandwiched gaps are filled."""
    runs, cur = [], None
    for p in plays:
        gap = p["started_at"] - cur["end"] if cur else None
        same_record = cur and p["release_id"] and p["release_id"] == cur["release"]
        # A longer gap is allowed back to the same record (a side flip with untagged tracks);
        # the sandwich check still has to find tracks whose lengths fit it.
        if cur and (gap <= PLAYS_GAP or (same_record and gap <= SAME_RECORD_GAP)):
            cur["end"] = max(cur["end"], p["end"])
        else:
            cur = {"start": p["started_at"], "end": p["end"], "edges": False, "release": None}
            runs.append(cur)
        if p["release_id"]:
            cur["release"] = p["release_id"]
    return runs


# --- reconcile ------------------------------------------------------------------------------

PLAY_COLS = ("id, started_at, last_seen_at, ended_at, COALESCE(ended_at, last_seen_at) AS end, "
             "artist, title, album, release_id, track_position, inferred, source")


def reconcile(db, since: float, until: float = None, dry_run: bool = False, verbose: bool = False) -> dict:
    """Rebuild inferred tracks and quarantine blips between since and until."""
    until = until or time.time()
    open_row = db.execute("SELECT s.play_id, p.started_at FROM station s JOIN plays p ON p.id = s.play_id "
                          "WHERE s.id = 1 AND s.state = 'playing' AND p.ended_at IS NULL").fetchone()
    open_id, open_start = (open_row[0], open_row[1]) if open_row else (None, None)

    plays = [dict(r) for r in db.execute(
        f"SELECT {PLAY_COLS} FROM plays WHERE started_at >= ? AND started_at < ? AND inferred = 0 ORDER BY started_at",
        (since, until))]
    old_inferred = [r[0] for r in db.execute(
        "SELECT id FROM plays WHERE inferred = 1 AND ended_at IS NOT NULL AND started_at >= ? AND started_at < ?",
        (since, until))]
    checks = db.execute("SELECT at, kind FROM checks WHERE at >= ? AND at < ? ORDER BY at", (since, until)).fetchall()

    first_check = checks[0][0] if checks else None
    runs = runs_from_checks(checks)
    early = [p for p in plays if first_check is None or p["started_at"] < first_check]
    runs += runs_from_plays(early)

    new_rows, quarantine = [], []
    for run in sorted(runs, key=lambda r: r["start"]):
        inside = [p for p in plays if run["start"] - 30 <= p["started_at"] <= run["end"] + 5]
        # A run with a track still playing in it is handled live, not filled forward here.
        live = open_start is not None and open_start >= run["start"] - 30
        rows, bad = fill_run(db, run, inside, open_id, live)
        new_rows += rows
        quarantine += bad

    # Don't infer a track that a real play already covers.
    real = {(p["release_id"], p["track_position"]) : p for p in plays if p["release_id"]}
    kept = []
    for r in new_rows:
        dup = real.get((r["release_id"], r["track_position"]))
        if dup and abs(dup["started_at"] - r["started_at"]) < 600:
            continue
        kept.append(r)

    summary = {"inferred": kept, "quarantined": quarantine, "replaced": len(old_inferred)}
    if verbose:
        describe(summary)
    if dry_run:
        return summary
    with db:
        if old_inferred:
            db.executemany("DELETE FROM plays WHERE id = ?", [(i,) for i in old_inferred])
        for r in kept:
            db.execute(
                "INSERT INTO plays (started_at, last_seen_at, ended_at, artist, title, album, release_id, "
                "track_position, side, cover_file, inferred) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1)",
                (r["started_at"], r["ended_at"], r["ended_at"], r["artist"], r["title"], r["album"],
                 r["release_id"], r["track_position"], r["side"], r["cover_file"]))
        for q in quarantine:
            move_to_quarantine(db, q["id"], q["reason"])
    return summary


def fill_run(db, run: dict, inside: list, open_id, live: bool):
    anchors_all = [p for p in inside if p["release_id"] and p["track_position"]]
    if not anchors_all:
        return [], []
    # The record this run belongs to: the one with the most tagged tracks in it.
    rid = Counter(p["release_id"] for p in anchors_all).most_common(1)[0][0]
    tl = tracklist(db, rid)
    if not tl:
        return [], []
    rel = db.execute("SELECT title, artist, cover_file FROM releases WHERE release_id = ?", (rid,)).fetchone()
    if rel is None:
        return [], []

    # Anchors: tagged tracks of this record that move forward through the tracklist.
    anchors, bad = [], []
    for p in sorted(anchors_all, key=lambda p: p["started_at"]):
        if p["release_id"] != rid:
            continue
        i = index_of(tl, p["track_position"])
        if i is None:
            continue
        brief = p["end"] - p["started_at"] <= BLIP_SECS
        if anchors and i <= anchors[-1][1]:
            if brief and p["id"] != open_id and i != anchors[-1][1] and p.get("source") != "manual":
                bad.append({"id": p["id"], "reason": "out of order on its own record", "title": p["title"], "at": p["started_at"]})
            continue
        anchors.append((p, i))
    if not anchors:
        return [], bad

    # Brief identifications of something else, inside this record's span, are false blips.
    if run["edges"]:
        span_start, span_end = run["start"], run["end"]
    else:   # no check log: allow a few minutes either side of the tagged tracks
        span_start = max(run["start"], anchors[0][0]["started_at"] - 300)
        span_end = min(run["end"], anchors[-1][0]["end"] + 300)
    for p in inside:
        if p["release_id"] == rid or p["id"] == open_id or p.get("source") == "manual":
            continue
        if p["end"] - p["started_at"] <= BLIP_SECS and span_start <= p["started_at"] <= span_end:
            bad.append({"id": p["id"], "reason": "brief match to another record mid-side", "title": p["title"], "at": p["started_at"]})

    def make(t, start, end):
        return {"release_id": rid, "track_position": t["position"], "side": t["side"], "title": t["title"],
                "artist": rel["artist"], "album": rel["title"], "cover_file": rel["cover_file"] or "",
                "started_at": int(start), "ended_at": int(max(start + 30, end))}

    def end_of(p, i):
        d = tl[i]["secs"]
        return p["started_at"] + d if d else max(p["last_seen_at"] + 10, p["started_at"] + 60)

    rows = []
    # Sandwiched gaps.
    for (a, ia), (b, ib) in zip(anchors, anchors[1:]):
        slots = tl[ia + 1:ib]
        if not slots:
            continue
        avail = b["started_at"] - end_of(a, ia)
        known = sum(t["secs"] for t in slots if t["secs"])
        unknown = sum(1 for t in slots if not t["secs"])
        flips = sum(1 for x, y in zip(tl[ia:ib], tl[ia + 1:ib + 1]) if x["side"] != y["side"])
        if unknown == 0:
            fits = -tol(known) <= avail - known <= tol(known) + flips * FLIP_SECS
        else:
            fits = 60 * unknown <= avail - known <= 480 * unknown + flips * FLIP_SECS
        if not fits:
            continue
        each_unknown = (max(0.0, avail - known) / unknown) if unknown else 0
        # Put any flip time at the side change, so tracks keep their real lengths.
        flip_pad = (avail - known - each_unknown * unknown) / flips if flips and unknown == 0 and avail > known else 0
        cursor = end_of(a, ia)
        prev_side = tl[ia]["side"]
        for t in slots:
            if t["side"] != prev_side:
                cursor += flip_pad
                prev_side = t["side"]
            d = t["secs"] or each_unknown
            if unknown == 0 and not flips:
                d *= max(.85, min(1.15, avail / known)) if known else 1
            rows.append(make(t, cursor, cursor + d))
            cursor += d

    # Snap filled tracks to the gaps heard between them: a start within 15 s of a gap moves onto it.
    if rows:
        lo, hi = min(r["started_at"] for r in rows) - 20, max(r["ended_at"] for r in rows) + 20
        ends = gap_ends(db, lo, hi)
        if ends:
            rows.sort(key=lambda r: r["started_at"])
            for k, r in enumerate(rows):
                g = min(ends, key=lambda e: abs(e - r["started_at"]))
                if abs(g - r["started_at"]) <= 15 and (k == 0 or g > rows[k - 1]["started_at"] + 30):
                    r["started_at"] = int(g)
                    if k:
                        rows[k - 1]["ended_at"] = int(g)

    if run["edges"]:
        # Backward: unidentified audio before the first anchor, on the same side.
        f, jf = anchors[0]
        stretch = f["started_at"] - run["start"]
        before = []
        k = jf - 1
        while k >= 0 and tl[k]["side"] == tl[jf]["side"]:
            before.append(tl[k])
            k -= 1
        if before and stretch > 45:
            best, total = None, 0.0
            for n, t in enumerate(before, 1):
                if not t["secs"]:
                    if n == 1 and 60 <= stretch <= 600:
                        best = (1, stretch)
                    break
                total += t["secs"]
                if abs(stretch - total) <= tol(total) and (best is None or abs(stretch - total) <= abs(stretch - best[1])):
                    best = (n, total)
            if best is None and before[0]["secs"] and stretch >= 0.4 * before[0]["secs"] and stretch < before[0]["secs"]:
                best = (1, stretch)        # the needle went down partway through the track before
            if best:
                end = f["started_at"]
                for t in before[:best[0]]:
                    d = t["secs"] or best[1]
                    rows.append(make(t, max(run["start"], end - d), end))
                    end -= d

        # Forward: audio carrying on after the last anchor, unless that run is still playing.
        l, jl = anchors[-1]
        if l["id"] != open_id and not live:
            cursor = end_of(l, jl)
            k = jl + 1
            while k < len(tl) and tl[k]["side"] == tl[jl]["side"]:
                t = tl[k]
                remaining = run["end"] - cursor
                if t["secs"]:
                    if remaining < 0.5 * t["secs"]:
                        break
                    rows.append(make(t, cursor, min(cursor + t["secs"], run["end"])))
                    cursor += t["secs"]
                else:
                    if remaining >= 90:
                        rows.append(make(t, cursor, min(cursor + 240, run["end"])))
                    break
                k += 1
    return rows, bad


# --- quarantine ------------------------------------------------------------------------------

def quarantine_columns(db) -> list:
    play_cols = [r[1] for r in db.execute("PRAGMA table_info(plays)")]
    q_cols = {r[1] for r in db.execute("PRAGMA table_info(plays_quarantine)")}
    return [c for c in play_cols if c in q_cols]


def move_to_quarantine(db, play_id: int, reason: str) -> None:
    cols = ", ".join(quarantine_columns(db))
    db.execute(f"INSERT INTO plays_quarantine ({cols}, reason, quarantined_at) "
               f"SELECT {cols}, ?, ? FROM plays WHERE id = ?", (reason, int(time.time()), play_id))
    db.execute("DELETE FROM plays WHERE id = ?", (play_id,))


def restore_quarantine(db, since: float = 0) -> int:
    cols = ", ".join(quarantine_columns(db))
    with db:
        n = db.execute(f"INSERT INTO plays ({cols}) SELECT {cols} FROM plays_quarantine WHERE started_at >= ?",
                       (since,)).rowcount
        db.execute("DELETE FROM plays_quarantine WHERE started_at >= ?", (since,))
    return n


# --- reporting and command line ------------------------------------------------------------------

def stamp(t) -> str:
    return time.strftime("%Y-%m-%d %H:%M", time.localtime(t))


def describe(summary: dict) -> None:
    for r in sorted(summary["inferred"], key=lambda r: r["started_at"]):
        mins = (r["ended_at"] - r["started_at"]) / 60
        print(f"  + {stamp(r['started_at'])}  {r['track_position']:<4} {r['title'][:48]:<48} {r['album'][:30]}  ({mins:.1f} min)")
    for q in sorted(summary["quarantined"], key=lambda q: q["at"]):
        print(f"  - {stamp(q['at'])}  quarantine '{q['title'][:48]}': {q['reason']}")
    print(f"  {len(summary['inferred'])} tracks inferred, {len(summary['quarantined'])} blips quarantined"
          + (f" ({summary['replaced']} earlier inferences rebuilt)" if summary.get("replaced") else ""))


def main() -> None:
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    from store import DB_PATH, connect

    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--history", action="store_true", help="reconcile all past plays")
    ap.add_argument("--since", help="reconcile from this date (YYYY-MM-DD)")
    ap.add_argument("--dry-run", action="store_true", help="show the changes without making them")
    ap.add_argument("--restore", action="store_true", help="move every quarantined play back")
    args = ap.parse_args()

    db = connect(DB_PATH)
    if args.restore:
        print(f"Restored {restore_quarantine(db)} quarantined plays. Inferred tracks are untouched; "
              "run --history again to rebuild them.")
        return
    if not (args.history or args.since):
        ap.print_help()
        return
    since = time.mktime(time.strptime(args.since, "%Y-%m-%d")) if args.since else 0
    print("Dry run, nothing will change:" if args.dry_run else "Reconciling:")
    reconcile(db, since, dry_run=args.dry_run, verbose=True)


if __name__ == "__main__":
    main()
