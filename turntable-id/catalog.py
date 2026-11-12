"""Match a Shazam result to a record in the Discogs collection.

Shazam often names a remaster, deluxe edition or compilation instead of the copy
on the platter, so matching compares normalized artist, album and track titles,
and prefers the record that was already playing if the new track is on it.
"""
import difflib
import re
import time
import unicodedata

PAREN       = re.compile(r"\s*[\(\[][^\)\]]*[\)\]]")
DASH_SUFFIX = re.compile(r"\s+-\s+.*\b(remaster(ed)?|version|mix|edit|live|mono|stereo|demo|single)\b.*$", re.I)
FEAT        = re.compile(r"\s+(feat\.?|ft\.?|featuring)\s+.*$", re.I)
DISCOGS_NUM = re.compile(r"\s*\(\d+\)$")
CONTINUITY_WINDOW = 15 * 60   # a previous track this recent can anchor the match


def norm(s: str, kind: str = "title") -> str:
    s = s or ""
    if kind == "artist":
        s = DISCOGS_NUM.sub("", s.strip())
        s = FEAT.sub("", s)
    else:
        s = DASH_SUFFIX.sub("", s)
    s = PAREN.sub("", s)
    s = unicodedata.normalize("NFKD", s)
    s = "".join(c for c in s if not unicodedata.combining(c))
    s = s.lower().replace("&", " and ")
    s = re.sub(r"[^a-z0-9 ]+", " ", s)
    s = re.sub(r"^the\s+", "", s.strip())
    return " ".join(s.split())


def sim(a: str, b: str) -> float:
    if not a or not b:
        return 0.0
    if a == b:
        return 1.0
    ratio = difflib.SequenceMatcher(None, a, b).ratio()
    if min(len(a), len(b)) > 3 and (a in b or b in a):
        ratio = max(ratio, 0.9)
    return ratio


def side_of(position: str) -> str:
    """'A1' -> 'A', 'B' -> 'B', '1-3' or '3' (CD) -> ''."""
    m = re.match(r"^([A-Za-z]+)", (position or "").strip())
    return m.group(1).upper() if m else ""


class Catalog:
    """In-memory copy of the collection, reloaded whenever a sync bumps the version."""

    _UNLOADED = object()   # distinct from "no version recorded yet", so a fresh database still loads

    def __init__(self, db):
        self.db = db
        self.version = Catalog._UNLOADED
        self.releases = []
        self.aliases = {}          # (artist, album) as Shazam names them -> release_id

    def _refresh(self):
        row = self.db.execute("SELECT value FROM meta WHERE key = 'catalog_version'").fetchone()
        arow = self.db.execute("SELECT value FROM meta WHERE key = 'alias_version'").fetchone()
        version = (row[0] if row else None, arow[0] if arow else None)   # a new alias reloads too
        if version == self.version:
            return
        releases = {}
        for r in self.db.execute("SELECT release_id, title, artist FROM releases WHERE in_collection = 1"):
            releases[r[0]] = {
                "id": r[0],
                "title": norm(r[1], "title"),
                "artist": norm(r[2], "artist"),
                "various": norm(r[2], "artist") in ("various", "various artists"),
                "tracks": [],
            }
        for t in self.db.execute("SELECT release_id, position, title FROM tracks ORDER BY release_id, seq"):
            if t[0] in releases:
                releases[t[0]]["tracks"].append((norm(t[2], "title"), t[1] or ""))
        self.releases = list(releases.values())
        self.aliases = {}
        try:
            for a, al, rid in self.db.execute("SELECT artist, album, release_id FROM match_aliases"):
                if rid in releases:
                    self.aliases[(a, al)] = rid
        except Exception:
            pass                   # no aliases table yet
        self.version = version

    @staticmethod
    def _track_hit(release, title):
        """Return the track position if `title` is on this release, else None."""
        if not title:
            return None
        for t, pos in release["tracks"]:
            if t == title:
                return pos
        for t, pos in release["tracks"]:
            if sim(t, title) >= 0.88:
                return pos
        return None

    def match(self, artist: str, title: str, album: str, prev_release=None):
        """Return (release_id, position, side) or None."""
        self._refresh()
        if not self.releases:
            return None
        a, t, al = norm(artist, "artist"), norm(title, "title"), norm(album, "title")

        # An alias you taught it (from the unmatched view) wins outright.
        rid = self.aliases.get((a, al))
        if rid is not None:
            for r in self.releases:
                if r["id"] == rid:
                    pos = self._track_hit(r, t) or ""
                    return r["id"], pos, side_of(pos)

        if prev_release is not None:
            for r in self.releases:
                if r["id"] == prev_release:
                    pos = self._track_hit(r, t)
                    if pos is not None:
                        return r["id"], pos, side_of(pos)
                    break

        best, best_score = None, 0.0
        for r in self.releases:
            asim = sim(a, r["artist"])
            if asim < 0.5 and not r["various"]:
                continue
            alsim = sim(al, r["title"]) if al else 0.0
            pos = self._track_hit(r, t)
            if r["various"]:
                score = 0.55 * (pos is not None) + 0.45 * alsim
                ok = pos is not None and alsim >= 0.6
            else:
                score = 0.45 * asim + 0.35 * alsim + 0.3 * (pos is not None)
                ok = asim >= 0.75 and (pos is not None or alsim >= 0.8)
            if ok and score > best_score:
                best, best_score = (r["id"], pos or "", side_of(pos or "")), score
        return best if best_score >= 0.65 else None

    def previous_release(self, before_ts: int):
        """The release of the most recent matched play, if it ended recently enough."""
        row = self.db.execute(
            "SELECT release_id, COALESCE(ended_at, last_seen_at) FROM plays "
            "WHERE release_id IS NOT NULL AND started_at < ? ORDER BY started_at DESC LIMIT 1",
            (before_ts,),
        ).fetchone()
        if row and before_ts - row[1] <= CONTINUITY_WINDOW:
            return row[0]
        return None


def backfill(db, log=None, rematch_all: bool = False) -> int:
    """Match plays to the collection, oldest first so continuity works. Returns matches made."""
    cat = Catalog(db)
    where = "" if rematch_all else "WHERE release_id IS NULL"
    rows = db.execute(f"SELECT id, started_at, artist, title, album FROM plays {where} ORDER BY started_at").fetchall()
    made = 0
    with db:
        for pid, started, artist, title, album in rows:
            prev = cat.previous_release(started)
            m = cat.match(artist, title, album, prev)
            if m:
                db.execute("UPDATE plays SET release_id = ?, track_position = ?, side = ? WHERE id = ?",
                           (m[0], m[1], m[2], pid))
                made += 1
            elif rematch_all:
                db.execute("UPDATE plays SET release_id = NULL, track_position = '', side = '' WHERE id = ?", (pid,))
    if log:
        log.info("Matched %d of %d plays to the collection", made, len(rows))
    return made
