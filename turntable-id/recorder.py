#!/usr/bin/env python3
"""Record listening sessions from the Icecast stream, keeping the last few.

It listens to the stream all the time but keeps only the last minute in memory, so
nothing touches the disk until a session starts. When the identifier reports music (the
station leaves idle), that minute is written out first, so the needle drop is caught even
though the identifier takes up to about 20 seconds to notice. Recording carries on through
side flips and short pauses, and stops after STOP_AFTER seconds of quiet, trimming the
silent tail. The MP3 is copied byte for byte: no re-encoding, no quality loss, next to no CPU.

Settings come from the environment (the systemd unit):
  TURNTABLE_STREAM   the Icecast mount            http://127.0.0.1:8000/turntable
  RECORD_DIR         where files go                /var/lib/turntable/recordings
  RECORD_KEEP        listening sessions to keep    5
  RECORD_KBPS        the stream's bitrate          320
  RECORD_PREROLL     seconds kept in memory        60
  RECORD_STOP_AFTER  quiet seconds before stopping 600
  RECORD_MIN_FREE_GB don't record below this free  2
"""
import collections
import logging
import os
import shutil
import sqlite3
import sys
import threading
import time

import requests
import turntable_config
CFG = turntable_config.load()

log = logging.getLogger("turntable-record")

R = CFG["recording"]
DB_PATH = CFG["paths"]["db"]
STREAM = turntable_config.stream_url(CFG)
RECORD_DIR = CFG["paths"]["recordings"]
KEEP = R["keep_sessions"]
KBPS = R["kbps"]
PREROLL = R["preroll"]
STOP_AFTER = R["stop_after"]
MIN_FREE = float(R["min_free_gb"]) * 1e9
TAIL = R["tail"]                     # seconds kept after the last music, so a fade-out isn't clipped
MIN_LENGTH = R["min_seconds"]        # shorter sessions aren't worth keeping
BYTES_PER_SEC = KBPS * 1000 // 8

SCHEMA = """
CREATE TABLE IF NOT EXISTS recordings (
    id          INTEGER PRIMARY KEY,
    started_at  INTEGER NOT NULL,          -- when the first byte in the file was played
    ended_at    INTEGER,                   -- when the last byte was played (set when finished)
    file        TEXT NOT NULL,             -- file name inside RECORD_DIR
    bytes       INTEGER NOT NULL DEFAULT 0,
    kbps        INTEGER NOT NULL,
    status      TEXT NOT NULL              -- recording | done
);
"""


class Stream(threading.Thread):
    """Reads the Icecast stream forever: always into the in-memory pre-roll, and into a file
    while recording. Reconnects on its own."""

    def __init__(self):
        super().__init__(daemon=True)
        self.lock = threading.Lock()
        self.ring = collections.deque()        # pre-roll chunks
        self.ring_bytes = 0
        self.out = None                        # open file while recording
        self.written = 0

    def run(self):
        while True:
            try:
                with requests.get(STREAM, stream=True, timeout=(5, 30),
                                  headers={"User-Agent": "turntable-record"}) as r:   # no Icy-MetaData: plain MP3
                    r.raise_for_status()
                    for chunk in r.iter_content(chunk_size=8192):
                        if chunk:
                            self.take(chunk)
            except requests.RequestException as e:
                log.warning("Stream: %s; reconnecting in 5 s", e)
            time.sleep(5)

    def take(self, chunk: bytes):
        with self.lock:
            if self.out:
                self.out.write(chunk)
                self.written += len(chunk)
            self.ring.append(chunk)
            self.ring_bytes += len(chunk)
            while self.ring_bytes > PREROLL * BYTES_PER_SEC and len(self.ring) > 1:
                self.ring_bytes -= len(self.ring.popleft())

    def start_file(self, path: str) -> float:
        """Open a file, write the pre-roll into it, and keep recording. Returns the time the
        file's first byte was played (now, minus the pre-roll's length)."""
        with self.lock:
            self.out = open(path, "wb")
            for c in self.ring:
                self.out.write(c)
            self.written = self.ring_bytes
            return time.time() - self.ring_bytes / BYTES_PER_SEC

    def stop_file(self) -> int:
        with self.lock:
            n = self.written
            if self.out:
                self.out.close()
            self.out = None
            return n

    def flush(self):
        with self.lock:
            if self.out:
                self.out.flush()


def connect():
    db = sqlite3.connect(DB_PATH, timeout=10)
    db.row_factory = sqlite3.Row
    db.executescript(SCHEMA)
    return db


def station_active(db) -> bool:
    row = db.execute("SELECT state FROM station WHERE id = 1").fetchone()
    return bool(row) and row[0] in ("playing", "unknown")


SESSION_BREAK = 2 * 3600     # recordings closer together than this belong to one listening session


def prune(db):
    """Keep the newest KEEP listening sessions (every recording file in each: a session can be
    several files, split by pauses or a stream dropout); delete the rest and any stray files."""
    done = db.execute("SELECT id, file, started_at, ended_at FROM recordings WHERE status = 'done' "
                      "ORDER BY started_at DESC").fetchall()
    sessions, older = 0, None
    drop = []
    for r in done:
        if older is None or older["started_at"] - (r["ended_at"] or r["started_at"]) > SESSION_BREAK:
            sessions += 1
        older = r
        if sessions > KEEP:
            drop.append(r)
    for r in drop:
        try:
            os.remove(os.path.join(RECORD_DIR, r["file"]))
        except FileNotFoundError:
            pass
        with db:
            db.execute("DELETE FROM recordings WHERE id = ?", (r["id"],))
        log.info("Deleted an older recording: %s", r["file"])
    known = {r[0] for r in db.execute("SELECT file FROM recordings")}
    for name in os.listdir(RECORD_DIR):
        if name not in known and (name.endswith(".mp3") or name.endswith(".part")):
            os.remove(os.path.join(RECORD_DIR, name))


def finish(db, rec_id: int, path: str, started: float, last_music: float, written: int):
    """Trim the silent tail, then keep the session (or drop it when it's too short)."""
    keep_secs = max(0, last_music + TAIL - started)
    keep_bytes = min(written, int(keep_secs * BYTES_PER_SEC))
    if keep_secs < MIN_LENGTH:
        os.remove(path)
        with db:
            db.execute("DELETE FROM recordings WHERE id = ?", (rec_id,))
        log.info("Session too short to keep (%.0f s)", keep_secs)
        return
    with open(path, "r+b") as f:
        f.truncate(keep_bytes)
    final = path[:-len(".part")]
    os.replace(path, final)
    with db:
        db.execute("UPDATE recordings SET ended_at = ?, file = ?, bytes = ?, status = 'done' WHERE id = ?",
                   (int(started + keep_bytes / BYTES_PER_SEC), os.path.basename(final), keep_bytes, rec_id))
    log.info("Saved a %.0f-minute session (%.0f MB): %s", keep_secs / 60, keep_bytes / 1e6, os.path.basename(final))
    prune(db)


def main():
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    os.makedirs(RECORD_DIR, exist_ok=True)
    db = connect()
    # A recording left open by a crash or restart: finish it as best we can.
    for r in db.execute("SELECT id, started_at, file FROM recordings WHERE status = 'recording'").fetchall():
        path = os.path.join(RECORD_DIR, r["file"])
        if os.path.exists(path):
            size = os.path.getsize(path)
            finish(db, r["id"], path, r["started_at"], r["started_at"] + size / BYTES_PER_SEC - TAIL, size)
        else:
            with db:
                db.execute("DELETE FROM recordings WHERE id = ?", (r["id"],))
    # Entries whose file has gone (deleted by hand, say) are tidied away.
    for r in db.execute("SELECT id, file FROM recordings WHERE status = 'done'").fetchall():
        if not os.path.exists(os.path.join(RECORD_DIR, r["file"])):
            with db:
                db.execute("DELETE FROM recordings WHERE id = ?", (r["id"],))
    prune(db)

    stream = Stream()
    stream.start()
    log.info("Listening to %s (keeping the last %d sessions in %s)", STREAM, KEEP, RECORD_DIR)
    rec = None                  # (id, path, started)
    last_music = 0.0
    while True:
        time.sleep(2)
        try:
            active = station_active(db)
        except sqlite3.Error as e:
            log.warning("Database: %s", e)
            continue
        now = time.time()
        if active:
            last_music = now
            if rec is None:
                free = shutil.disk_usage(RECORD_DIR).free
                if free < MIN_FREE:
                    log.warning("Only %.1f GB free; not recording this session", free / 1e9)
                    continue
                path = os.path.join(RECORD_DIR, time.strftime("session-%Y%m%d-%H%M%S.mp3.part"))
                started = stream.start_file(path)
                with db:
                    cur = db.execute("INSERT INTO recordings (started_at, file, kbps, status) VALUES (?, ?, ?, 'recording')",
                                     (int(started), os.path.basename(path), KBPS))
                rec = (cur.lastrowid, path, started)
                log.info("Recording a new session (with %d s of pre-roll)", now - started)
        if rec:
            stream.flush()
            written = stream.written
            with db:
                db.execute("UPDATE recordings SET bytes = ? WHERE id = ?", (written, rec[0]))
            if now - last_music > STOP_AFTER:
                written = stream.stop_file()
                finish(db, rec[0], rec[1], rec[2], last_music, written)
                rec = None


if __name__ == "__main__":
    sys.exit(main())
