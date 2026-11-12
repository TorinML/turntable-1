"""Albums and their pressings, shared by identification, the Discogs sync and the web app.

Pressings of one album (the same Discogs master, or the same artist and title once edition notes like
"(Remastered)" are set aside) are one album. Plays belong to the album, not a pressing (Shazam can't
tell pressings apart), so each album has one fixed anchor: the pressing added to the collection first.
New plays are recorded against it, and plays logged against another pressing are moved onto it."""
import re
import time

_cache = {"key": None, "val": None}


def enabled() -> bool:
    try:
        import turntable_config
        return bool(turntable_config.load().get("collection", {}).get("group_pressings", True))
    except Exception:
        return True


def norm(s: str) -> str:
    s = re.sub(r"\s*[\(\[][^)\]]*[\)\]]", "", s or "")          # "(Remastered)", "[Deluxe]", Discogs' "(2)"
    return re.sub(r"[^a-z0-9]+", " ", s.lower()).strip()


def groups(db, on: bool = True) -> dict:
    """release_id -> {"primary": anchor id, "members": [ids, anchor first]}, for every release. The anchor
    is the pressing added first (then the lowest id), so it never moves. With `on` false, each release
    is its own album."""
    key = (on,) + tuple(db.execute("SELECT COUNT(*), MAX(synced_at), MAX(date_added), SUM(in_collection) FROM releases").fetchone())
    if _cache["key"] == key:
        return _cache["val"]
    rows = db.execute("SELECT release_id, master_id, artist, title, date_added, in_collection FROM releases").fetchall()
    parent = {r[0]: r[0] for r in rows}
    def find(x):
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x
    if on:
        seen = {}
        for rid, master, artist, title, _, _ in rows:
            for k in ((f"m{master}" if master else None), f"a{norm(artist)}|{norm(title)}"):
                if k and k != "a|":
                    if k in seen:
                        parent[find(rid)] = find(seen[k])
                    else:
                        seen[k] = rid
    info = {r[0]: r for r in rows}
    sets = {}
    for rid in parent:
        sets.setdefault(find(rid), []).append(rid)
    out = {}
    for ids in sets.values():
        owned = [i for i in ids if info[i][5]] or ids
        owned.sort(key=lambda i: (info[i][4] or "9999", i))
        for i in ids:
            out[i] = {"primary": owned[0], "members": owned}
    _cache.update(key=key, val=out)
    return out


def to_anchor(db, release_id, position, side=None):
    """Where a play of this pressing's track belongs: (anchor, position, side) on the album's anchor
    pressing, matching the track by title (positions can differ between pressings)."""
    if not release_id or not enabled():
        return release_id, position, side
    g = groups(db, True).get(release_id)
    if not g or g["primary"] == release_id:
        return release_id, position, side
    anchor = g["primary"]
    title = db.execute("SELECT title FROM tracks WHERE release_id = ? AND position = ?", (release_id, position)).fetchone()
    if title:
        for pos, s2, t in db.execute("SELECT position, side, title FROM tracks WHERE release_id = ?", (anchor,)):
            if norm(t) == norm(title[0]):
                return anchor, pos, s2
    return anchor, position, side


def fold_plays(db) -> int:
    """Move plays logged against a pressing onto its album's anchor. Returns how many moved."""
    if not enabled():
        return 0
    moved = 0
    for rid, g in groups(db, True).items():
        if g["primary"] == rid:
            continue
        rows = db.execute("SELECT id, track_position FROM plays WHERE release_id = ?", (rid,)).fetchall()
        if not rows:
            continue
        with db:
            for pid, pos in rows:
                anchor, apos, _ = to_anchor(db, rid, pos)
                db.execute("UPDATE plays SET release_id = ?, track_position = ? WHERE id = ?", (anchor, apos or pos, pid))
                moved += 1
    return moved
