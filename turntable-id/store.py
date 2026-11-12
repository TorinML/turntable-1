"""Play log for the turntable: one row per identified track, plus the station's current state.

Only the ID script writes here. The web app opens the same file read-only.
"""
import logging
import os
import sqlite3
import time

try:
    import requests
except ImportError:          # the web app's environment on a hand-built install may not have it; it imports
    class _NoRequests:       # this module only for code that never makes a request there
        RequestException = OSError
        def __getattr__(self, name):
            raise RuntimeError("the requests library isn't installed")
    requests = _NoRequests()

from catalog import Catalog
import turntable_config
CFG = turntable_config.load()

DB_PATH   = CFG["paths"]["db"]
COVER_DIR = CFG["paths"]["covers"]

log = logging.getLogger("turntable-id")

SCHEMA = """
CREATE TABLE IF NOT EXISTS plays (
    id            INTEGER PRIMARY KEY,
    started_at    INTEGER NOT NULL,      -- unix seconds, first time the track was identified
    last_seen_at  INTEGER NOT NULL,      -- unix seconds, most recent check that still heard it
    ended_at      INTEGER,               -- NULL while it's the current track
    artist        TEXT NOT NULL DEFAULT '',
    title         TEXT NOT NULL DEFAULT '',
    album         TEXT NOT NULL DEFAULT '',
    genre         TEXT NOT NULL DEFAULT '',
    label         TEXT NOT NULL DEFAULT '',
    released      TEXT NOT NULL DEFAULT '',
    shazam_key    TEXT NOT NULL DEFAULT '',
    isrc          TEXT NOT NULL DEFAULT '',
    cover_url     TEXT NOT NULL DEFAULT '',
    cover_file    TEXT NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS plays_started ON plays(started_at);
CREATE INDEX IF NOT EXISTS plays_artist  ON plays(artist);

CREATE TABLE IF NOT EXISTS releases (
    release_id    INTEGER PRIMARY KEY,   -- Discogs release id
    title         TEXT NOT NULL DEFAULT '',
    artist        TEXT NOT NULL DEFAULT '',
    year          INTEGER,
    label         TEXT NOT NULL DEFAULT '',
    catno         TEXT NOT NULL DEFAULT '',
    format        TEXT NOT NULL DEFAULT '',
    genres        TEXT NOT NULL DEFAULT '[]',   -- JSON list
    styles        TEXT NOT NULL DEFAULT '[]',   -- JSON list
    cover_url     TEXT NOT NULL DEFAULT '',
    cover_file    TEXT NOT NULL DEFAULT '',
    date_added    TEXT NOT NULL DEFAULT '',     -- when it joined the collection (ISO)
    in_collection INTEGER NOT NULL DEFAULT 1,   -- 0 once removed from Discogs
    tracks_synced INTEGER NOT NULL DEFAULT 0,
    synced_at     INTEGER
);

CREATE TABLE IF NOT EXISTS tracks (
    release_id  INTEGER NOT NULL,
    seq         INTEGER NOT NULL,
    position    TEXT NOT NULL DEFAULT '',
    side        TEXT NOT NULL DEFAULT '',
    title       TEXT NOT NULL DEFAULT '',
    duration    TEXT NOT NULL DEFAULT '',
    PRIMARY KEY (release_id, seq)
);

CREATE TABLE IF NOT EXISTS meta (
    key    TEXT PRIMARY KEY,
    value  TEXT
);

-- Track lengths found elsewhere when Discogs lists none for a pressing (lengths.py).
-- Kept apart from tracks so a Discogs refresh, which rewrites tracklists, can't lose them.
CREATE TABLE IF NOT EXISTS track_lengths (
    release_id  INTEGER NOT NULL,
    position    TEXT NOT NULL,
    title       TEXT NOT NULL DEFAULT '',
    secs        INTEGER NOT NULL,
    source      TEXT NOT NULL,              -- discogs-master | musicbrainz | itunes
    found_at    INTEGER NOT NULL,
    PRIMARY KEY (release_id, position)
);
-- When each record was last checked for missing lengths, and how many were still missing.
CREATE TABLE IF NOT EXISTS length_checks (
    release_id  INTEGER PRIMARY KEY,
    checked_at  INTEGER NOT NULL,
    missing     INTEGER NOT NULL
);

-- Edits from the web page, applied by the identifier (the one writer of plays and the station).
CREATE TABLE IF NOT EXISTS edit_requests (
    id        INTEGER PRIMARY KEY,
    at        INTEGER NOT NULL,
    kind      TEXT NOT NULL,                -- tag | check | fill | remove
    payload   TEXT NOT NULL,                -- JSON
    status    TEXT NOT NULL DEFAULT 'pending',   -- pending | done | failed
    result    TEXT NOT NULL DEFAULT '',
    done_at   INTEGER
);

-- Taught matches: when Shazam names this artist and album, it's this record (from the unmatched view).
CREATE TABLE IF NOT EXISTS match_aliases (
    artist      TEXT NOT NULL,              -- normalized the way catalog.py compares names
    album       TEXT NOT NULL,
    release_id  INTEGER NOT NULL,
    created_at  INTEGER NOT NULL,
    PRIMARY KEY (artist, album)
);

CREATE TABLE IF NOT EXISTS checks (
    id       INTEGER PRIMARY KEY,
    at       INTEGER NOT NULL,              -- when the sample was taken
    kind     TEXT NOT NULL,                 -- silence | nomatch | match | pending
    rms      REAL,
    play_id  INTEGER                        -- the play open at the time, if any
);
CREATE INDEX IF NOT EXISTS checks_at ON checks(at);

CREATE TABLE IF NOT EXISTS station (
    id          INTEGER PRIMARY KEY CHECK (id = 1),
    state       TEXT NOT NULL,           -- playing | idle | unknown
    play_id     INTEGER,
    updated_at  INTEGER NOT NULL
);
"""


# Columns added to existing tables after step 1. Applied once, on startup.
MIGRATIONS = {
    "plays": [
        ("release_id", "INTEGER"),
        ("track_position", "TEXT NOT NULL DEFAULT ''"),
        ("side", "TEXT NOT NULL DEFAULT ''"),
        ("inferred", "INTEGER NOT NULL DEFAULT 0"),   # 1 = filled in from the tracklist, not heard by Shazam
        ("source", "TEXT NOT NULL DEFAULT ''"),       # 'manual' = tagged or checked off by hand on the page
    ],
    "tracks": [
        ("duration_source", "TEXT NOT NULL DEFAULT ''"),  # '' = Discogs lists it; else where lengths.py found it
    ],
    "releases": [
        ("master_id", "INTEGER"),          # Discogs master: the album across all its pressings
        ("original_year", "INTEGER"),      # when the music first came out, not this pressing (years.py)
        ("original_year_source", "TEXT"),  # musicbrainz, discogs master, pressing or manual
        ("media_condition", "TEXT"),       # from your Discogs collection fields, if you fill them in
        ("sleeve_condition", "TEXT"),
    ],
}

CHECK_DAYS = 45   # how long the check log is kept


def migrate(db) -> None:
    for table, cols in MIGRATIONS.items():
        have = {r[1] for r in db.execute(f"PRAGMA table_info({table})")}
        for name, decl in cols:
            if name not in have:
                db.execute(f"ALTER TABLE {table} ADD COLUMN {name} {decl}")
    db.execute("CREATE INDEX IF NOT EXISTS plays_release ON plays(release_id)")
    # Plays moved aside as false identifications. Same columns as plays, plus why and when.
    if db.execute("SELECT 1 FROM sqlite_master WHERE name = 'plays_quarantine'").fetchone() is None:
        db.execute("CREATE TABLE plays_quarantine AS SELECT * FROM plays WHERE 0")
        db.execute("ALTER TABLE plays_quarantine ADD COLUMN reason TEXT NOT NULL DEFAULT ''")
        db.execute("ALTER TABLE plays_quarantine ADD COLUMN quarantined_at INTEGER")
    have_q = {r[1] for r in db.execute("PRAGMA table_info(plays_quarantine)")}
    for r in db.execute("PRAGMA table_info(plays)").fetchall():
        if r[1] not in have_q:
            db.execute(f"ALTER TABLE plays_quarantine ADD COLUMN {r[1]} {r[2] or 'TEXT'}")
    db.commit()


def connect(path: str = DB_PATH):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    db = sqlite3.connect(path, timeout=15)
    db.row_factory = sqlite3.Row
    db.execute("PRAGMA journal_mode=WAL")
    db.executescript(SCHEMA)
    migrate(db)
    return db


class Store:
    def __init__(self, path: str = DB_PATH, cover_dir: str = COVER_DIR):
        os.makedirs(cover_dir, exist_ok=True)
        self.cover_dir = cover_dir
        # WAL lets the web app read while this process writes.
        self.db = connect(path)
        self.catalog = Catalog(self.db)
        self._checks = 0
        self._close_dangling()

    def _close_dangling(self) -> None:
        """A crash or reboot can leave a play open; close it at its last confirmed moment."""
        with self.db:
            self.db.execute("UPDATE plays SET ended_at = last_seen_at WHERE ended_at IS NULL")
            self._set_state("idle", None)

    def _set_state(self, state: str, play_id) -> None:
        self.db.execute(
            "INSERT INTO station (id, state, play_id, updated_at) VALUES (1, ?, ?, ?) "
            "ON CONFLICT(id) DO UPDATE SET state = excluded.state, "
            "play_id = excluded.play_id, updated_at = excluded.updated_at",
            (state, play_id, int(time.time())),
        )

    def _close_open(self) -> None:
        self.db.execute("UPDATE plays SET ended_at = last_seen_at WHERE ended_at IS NULL")

    def start_play(self, info: dict, match=None, started_at=None, inferred: bool = False,
                   cover_file: str = None, source: str = "") -> int:
        """A new track: close the previous one and open a row for this one.
        `started_at` backdates it (a track confirmed on its second check began at the first);
        `inferred` marks a track filled in from the tracklist rather than heard by Shazam."""
        now = int(time.time())
        start = int(started_at) if started_at else now
        if cover_file is None:
            cover_file = self._cache_cover(info)
        release_id, position, side = match if match else self._match(info, now)
        with self.db:
            # The previous track ends where this one begins, if that's earlier than its last check.
            self.db.execute("UPDATE plays SET ended_at = MIN(last_seen_at, MAX(started_at, ?)) WHERE ended_at IS NULL",
                            (start,))
            cur = self.db.execute(
                "INSERT INTO plays (started_at, last_seen_at, artist, title, album, genre, label, "
                "released, shazam_key, isrc, cover_url, cover_file, release_id, track_position, side, inferred, source) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (start, max(start, now), info["artist"], info["title"], info["album"], info.get("genre", ""),
                 info.get("label", ""), info.get("released", ""), info.get("key", ""), info.get("isrc", ""),
                 info.get("cover", ""), cover_file, release_id, position, side, 1 if inferred else 0, source),
            )
            play_id = cur.lastrowid
            self._set_state("playing", play_id)
        return play_id

    def has_cargo(self) -> bool:
        """Whether the collection has synced, so matching only cargo makes sense (a fresh install hasn't)."""
        try:
            return bool(self.db.execute("SELECT 1 FROM releases WHERE in_collection = 1 LIMIT 1").fetchone())
        except Exception:
            return False

    def identify(self, info: dict):
        """Which record in the crate (and which track) this is: (release_id, position, side) or None."""
        m = self._match(info, int(time.time()))
        if not m[0]:
            return None
        try:                                    # plays belong to the album, whichever pressing matched
            import albums
            return tuple(albums.to_anchor(self.db, m[0], m[1], m[2] if len(m) > 2 else None))
        except Exception:
            return m

    def current(self):
        """The open play, if something is playing."""
        row = self.db.execute(
            "SELECT p.* FROM station s JOIN plays p ON p.id = s.play_id "
            "WHERE s.id = 1 AND s.state = 'playing' AND p.ended_at IS NULL").fetchone()
        return dict(row) if row else None

    def confirm_inferred(self) -> None:
        """Shazam heard the track we'd inferred: it's no longer a guess."""
        with self.db:
            self.db.execute("UPDATE plays SET inferred = 0, last_seen_at = ? WHERE ended_at IS NULL",
                            (int(time.time()),))

    def start_inferred(self, release_id: int, track: dict, started_at: float) -> int:
        """Open a play for the next track on the record, as a guess from the tracklist."""
        rel = self.db.execute("SELECT title, artist, cover_file FROM releases WHERE release_id = ?",
                              (release_id,)).fetchone()
        info = {"title": track["title"], "artist": rel["artist"], "album": rel["title"]}
        return self.start_play(info, match=(release_id, track["position"], track["side"]),
                               started_at=started_at, inferred=True, cover_file=rel["cover_file"] or "")

    def log_check(self, kind: str, level: float, at: float) -> None:
        """Record one check (when its sample started, and what it heard) for the inference."""
        cur = self.db.execute("SELECT play_id FROM station WHERE id = 1").fetchone()
        with self.db:
            self.db.execute("INSERT INTO checks (at, kind, rms, play_id) VALUES (?, ?, ?, ?)",
                            (int(at), kind, round(level, 1), cur[0] if cur else None))
        self._checks += 1
        if self._checks % 500 == 1:
            with self.db:
                self.db.execute("DELETE FROM checks WHERE at < ?", (int(time.time()) - CHECK_DAYS * 86400,))

    def _match(self, info: dict, now: int):
        """Find the copy in the collection, if any. Never raises."""
        try:
            prev = self.catalog.previous_release(now)
            m = self.catalog.match(info["artist"], info["title"], info["album"], prev)
            if m:
                log.info("Matched to collection: release %s, %s", m[0], m[1] or "position unknown")
                return m
        except Exception as e:
            log.warning("Collection match failed: %s", e)
        return None, "", ""

    def still_playing(self) -> None:
        """The current track was heard again; extend it."""
        with self.db:
            self.db.execute(
                "UPDATE plays SET last_seen_at = ? WHERE ended_at IS NULL", (int(time.time()),)
            )

    def stop(self, state: str) -> None:
        """Silence (idle) or an unidentifiable record (unknown): close the open play."""
        with self.db:
            self._close_open()
            self._set_state(state, None)

    def _cache_cover(self, info: dict) -> str:
        """Keep a local copy of the artwork so the web UI never depends on an outside CDN."""
        url, key = info.get("cover"), info.get("key")
        if not url or not key or not key.isalnum():
            return ""
        name = f"{key}.jpg"
        path = os.path.join(self.cover_dir, name)
        if os.path.exists(path):
            return name
        try:
            r = requests.get(url, timeout=10)
            r.raise_for_status()
            tmp = path + ".part"
            with open(tmp, "wb") as f:
                f.write(r.content)
            os.replace(tmp, path)
            return name
        except (requests.RequestException, OSError) as e:
            log.warning("Cover download failed: %s", e)
            return ""
