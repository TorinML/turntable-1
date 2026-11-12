"""Edits from the web page, applied by the identifier.

The web app only queues requests in edit_requests; this module applies them, so the
identifier stays the one writer of plays and the station, and a tag updates Music
Assistant and Home Assistant just like a Shazam match would. Every request is
validated again here, whatever the page sent.

  tag     the record and track playing now (when it's unrecognized, or wrong)
  check   tracks of a record played during a session, placed by the tracklist
  fill    tracks of a record that played in an unidentified gap
  remove  a wrong play, moved to quarantine (restorable with infer.py --restore)

Manual plays have source = 'manual': the gap-filling trusts them as anchors and never
quarantines them.
"""
import json
import logging
import time

import catalog
import infer

log = logging.getLogger("turntable-id")

SPIN_WINDOW = 3 * 3600     # how far around a moment to look for the rest of a record's spin
DEFAULT_SECS = 210         # a track's assumed length when Discogs lists none


class EditError(Exception):
    pass


def _release(db, rid):
    r = db.execute("SELECT release_id, title, artist, cover_file FROM releases WHERE release_id = ? AND in_collection = 1",
                   (int(rid),)).fetchone()
    if not r:
        raise EditError("That record isn't in the cargo hold.")
    return r


def _tracks(db, rid, positions):
    tl = infer.tracklist(db, rid)
    by_pos = {t["position"]: t for t in tl}
    picked = []
    for p in positions:
        if p not in by_pos:
            raise EditError(f"Track {p} isn't on that record.")
        picked.append(by_pos[p])
    return sorted(picked, key=lambda t: t["seq"]), tl


def _insert(db, rel, track, start, end):
    db.execute(
        "INSERT INTO plays (started_at, last_seen_at, ended_at, artist, title, album, release_id, track_position, "
        "side, cover_file, inferred, source) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0, 'manual')",
        (int(start), int(end), int(end), rel["artist"], track["title"], rel["title"], rel["release_id"],
         track["position"], track["side"], rel["cover_file"] or ""))


def _secs(t):
    return t["secs"] or DEFAULT_SECS


# --- tag: what's playing now ------------------------------------------------------------

def run_start(db, now):
    """Start of the current stretch of continuous, unidentified-or-not audio, from the check log."""
    start = now
    for at, kind in db.execute("SELECT at, kind FROM checks WHERE at > ? ORDER BY at DESC", (now - 6 * 3600,)):
        if kind == "silence" or start - at > infer.CHECK_GAP:
            break
        start = at
    return start


def tag(db, station, payload, now):
    rel = _release(db, payload["release_id"])
    (track,), _ = _tracks(db, rel["release_id"], [payload["position"]])
    cur = station.store.current()
    if cur and cur.get("source") != "manual":
        # correcting a wrong identification: it started when the wrong one did
        start = cur["started_at"]
        infer.move_to_quarantine(db, cur["id"], "corrected by hand")
        db.commit()
    else:
        last = db.execute("SELECT MAX(COALESCE(ended_at, last_seen_at)) FROM plays").fetchone()[0] or 0
        begin = max(run_start(db, now), last)
        # The track playing now began at most its length ago; when the unidentified stretch is
        # longer than that, earlier tracks played first, so assume it's about halfway through.
        start = begin if now - begin <= _secs(track) else now - _secs(track) / 2
    info = {"title": track["title"], "artist": rel["artist"], "album": rel["title"]}
    station.store.start_play(info, match=(rel["release_id"], track["position"], track["side"]), started_at=start,
                             cover_file=rel["cover_file"] or "", source="manual")
    station.publish(f'{rel["artist"]} - {track["title"]}', dict(info, cover=""))
    station.misses = 0
    station.pending = None
    return f'Tagged {track["position"]} {track["title"]} from {rel["title"]}.', start


# --- check: tracks played during a session ----------------------------------------------------

def check(db, payload, now):
    rel = _release(db, payload["release_id"])
    picked, tl = _tracks(db, rel["release_id"], payload["positions"])
    around = int(payload.get("around") or now)
    rows = db.execute("SELECT track_position, started_at, COALESCE(ended_at, last_seen_at) FROM plays "
                      "WHERE release_id = ? AND started_at BETWEEN ? AND ?",
                      (rel["release_id"], around - SPIN_WINDOW, around + SPIN_WINDOW)).fetchall()
    placed = {r[0]: (r[1], r[2]) for r in rows}
    seq = {t["position"]: i for i, t in enumerate(tl)}
    todo = [t for t in picked if t["position"] not in placed]
    if not todo:
        raise EditError("Those tracks are already logged for that session.")
    # Place each track against its neighbours in the tracklist: after the one before it,
    # or before the one after it; with neither, from the moment the session points at.
    for _ in range(len(todo) + 1):
        progress = False
        for t in list(todo):
            i = seq[t["position"]]
            prev = tl[i - 1]["position"] if i > 0 else None
            nxt = tl[i + 1]["position"] if i + 1 < len(tl) else None
            if prev in placed and (tl[i - 1]["side"] == t["side"] or not nxt in placed):
                s = placed[prev][0] + _secs(tl[i - 1])
            elif nxt in placed:
                s = placed[nxt][0] - _secs(t)
            else:
                continue
            placed[t["position"]] = (s, s + _secs(t))
            todo.remove(t)
            progress = True
        if not todo or not progress:
            break
    # Nothing next to them is logged. Tracks that come earlier on the record than everything
    # logged go before it (backwards from the first logged track); later ones go after the last.
    logged = [(seq[p], v) for p, v in placed.items() if p not in {t["position"] for t in todo}]
    first = min(logged, key=lambda x: x[0]) if logged else None
    last = max(logged, key=lambda x: x[0]) if logged else None
    early = [t for t in todo if first and seq[t["position"]] < first[0]]
    late = [t for t in todo if t not in early]
    end = first[1][0] if first else around
    for t in reversed(early):
        placed[t["position"]] = (end - _secs(t), end)
        end -= _secs(t)
    s = max(last[1][1], around) if last else around
    for t in late:
        placed[t["position"]] = (s, s + _secs(t))
        s += _secs(t)
    added = [t for t in picked if t["position"] not in {r[0] for r in rows}]
    with db:
        for t in added:
            a, b = placed[t["position"]]
            _insert(db, rel, t, a, b)
    first = min(placed[t["position"]][0] for t in added)
    names = ", ".join(t["position"] for t in added)
    return f'Checked off {names} on {rel["title"]}.', first


# --- fill: an unidentified gap -----------------------------------------------------------------

def fill(db, payload, now):
    rel = _release(db, payload["release_id"])
    picked, _ = _tracks(db, rel["release_id"], payload["positions"])
    start, end = int(payload["start"]), int(payload["end"])
    if not (0 < start < end <= now + 60) or end - start > 4 * 3600:
        raise EditError("That gap's times don't make sense.")
    total = sum(_secs(t) for t in picked)
    # Lengths as listed, squeezed if they'd overrun the gap (so the next play isn't overlapped).
    scale = min(1.0, (end - start) / total) if total else 1.0
    with db:
        s = start
        for t in picked:
            d = _secs(t) * scale
            _insert(db, rel, t, s, s + d)
            s += d
    return f'Filled the gap with {", ".join(t["position"] for t in picked)} from {rel["title"]}.', start


# --- remove: a wrong play ------------------------------------------------------------------------

def remove(db, station, payload, now):
    pid = int(payload["play_id"])
    row = db.execute("SELECT id, title, started_at, ended_at FROM plays WHERE id = ?", (pid,)).fetchone()
    if not row:
        raise EditError("That play is already gone.")
    open_now = row["ended_at"] is None
    infer.move_to_quarantine(db, pid, "removed by hand")
    db.commit()
    if open_now:
        station.store.stop("unknown")
        station.publish("Turntable - Vinyl", None, "unknown")
    return f'Removed {row["title"]}.', row["started_at"]


# --- link and dismiss: from the unmatched view ----------------------------------------------------

def link(db, payload, now):
    """Put unmatched plays on a record (each on its track, by title), and optionally teach the
    matcher an alias so future plays of that artist and album match on their own."""
    rel = _release(db, payload["release_id"])
    tl = infer.tracklist(db, rel["release_id"])
    ids = [int(i) for i in payload["play_ids"]]
    rows = db.execute(f"SELECT id, artist, album, title, started_at FROM plays WHERE release_id IS NULL "
                      f"AND id IN ({','.join('?' * len(ids))})", ids).fetchall()
    if not rows:
        raise EditError("Those plays are already matched or gone.")
    keys, placed = set(), 0
    with db:
        for r in rows:
            t = catalog.norm(r["title"], "title")
            best = max(tl, key=lambda x: catalog.sim(t, catalog.norm(x["title"], "title")), default=None)
            hit = best if best and catalog.sim(t, catalog.norm(best["title"], "title")) >= 0.8 else None
            db.execute("UPDATE plays SET release_id = ?, track_position = ?, side = ? WHERE id = ?",
                       (rel["release_id"], hit["position"] if hit else "", hit["side"] if hit else "", r["id"]))
            placed += bool(hit)
            keys.add((catalog.norm(r["artist"], "artist"), catalog.norm(r["album"] or "", "title")))
        taught = 0
        if payload.get("remember"):
            for a, al in keys:
                if a:
                    db.execute("INSERT INTO match_aliases (artist, album, release_id, created_at) VALUES (?, ?, ?, ?) "
                               "ON CONFLICT(artist, album) DO UPDATE SET release_id = excluded.release_id",
                               (a, al, rel["release_id"], int(now)))
                    taught += 1
            db.execute("INSERT INTO meta (key, value) VALUES ('alias_version', ?) "
                       "ON CONFLICT(key) DO UPDATE SET value = excluded.value", (str(now),))
    msg = f'Linked {len(rows)} {"play" if len(rows) == 1 else "plays"} to {rel["title"]}'
    msg += f" ({placed} on their tracks)" if placed != len(rows) else ""
    msg += ", and the matcher will recognize it next time." if taught else "."
    return msg, min(r["started_at"] for r in rows)


def dismiss(db, payload, now):
    """Plays that aren't from any record you own (false identifications): to quarantine."""
    ids = [int(i) for i in payload["play_ids"]]
    rows = db.execute(f"SELECT id, started_at FROM plays WHERE id IN ({','.join('?' * len(ids))})", ids).fetchall()
    if not rows:
        raise EditError("Those plays are already gone.")
    for r in rows:
        infer.move_to_quarantine(db, r["id"], "not in the collection (by hand)")
    db.commit()
    return f'Moved {len(rows)} {"play" if len(rows) == 1 else "plays"} to quarantine.', min(r["started_at"] for r in rows)


# --- the queue -----------------------------------------------------------------------------------

def apply_pending(db, station) -> int:
    """Apply every waiting request, oldest first. Returns how many were handled."""
    rows = db.execute("SELECT id, kind, payload FROM edit_requests WHERE status = 'pending' ORDER BY id").fetchall()
    for rid, kind, payload in rows:
        now = time.time()
        try:
            p = json.loads(payload)
            if kind == "tag":
                msg, since = tag(db, station, p, now)
            elif kind == "check":
                msg, since = check(db, p, now)
            elif kind == "fill":
                msg, since = fill(db, p, now)
            elif kind == "remove":
                msg, since = remove(db, station, p, now)
            elif kind == "link":
                msg, since = link(db, p, now)
            elif kind == "dismiss":
                msg, since = dismiss(db, p, now)
            else:
                raise EditError(f"Unknown edit '{kind}'.")
            status = "done"
            log.info("Edit %d (%s): %s", rid, kind, msg)
            try:   # let the gap-filling take the new anchors into account
                infer.reconcile(db, min(since, now) - 6 * 3600)
            except Exception as e:
                log.warning("Reconcile after edit %d failed: %s", rid, e)
        except (EditError, KeyError, ValueError, TypeError) as e:
            status, msg = "failed", str(e) if isinstance(e, EditError) else f"Bad request: {e}"
            log.info("Edit %d (%s) failed: %s", rid, kind, msg)
        except Exception as e:
            status, msg = "failed", f"Couldn't apply it: {e}"
            log.warning("Edit %d (%s) error: %s", rid, kind, e)
        with db:
            db.execute("UPDATE edit_requests SET status = ?, result = ?, done_at = ? WHERE id = ?",
                       (status, msg, int(time.time()), rid))
    # keep a month of history
    if rows:
        with db:
            db.execute("DELETE FROM edit_requests WHERE at < ?", (int(time.time()) - 30 * 86400,))
    return len(rows)
