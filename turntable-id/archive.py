#!/usr/bin/env python3
"""Turntable-1 album archive: every record from the cargo hold, saved track by track as you play it.

It works a side at a time, from the session recordings (the recorder's copy of the stream, MP3
320; the stream itself is never touched). When a side of a record you own has played through:

  1. it finds the side's true edges in the recording: the needle's first music after the lead-in,
     and where the music stops before the run-out (quiet grooves, on any record);
  2. it lays the tracklist's lengths across the side, scaled to the side's real length (a pressing
     can run a little fast or slow), and moves each boundary to the quietest moment within a few
     seconds: the gap between tracks, or the dip in a crossfade on albums that run together;
  3. if the side's length matches the tracklist's, every track on it is saved; if the needle was
     lifted, the tracks before that point are. A record with no listed lengths is saved only when
     the gaps heard account for every track;
  4. each track is copied out of the recording as it is (no re-encoding), tagged (title, artist,
     album artist, album, track number, original year, genre) with the cover, to a spool on the
     Pi, then moved to the library (the NAS): Artist/Album (Year)/NN - Title.mp3, with cover.jpg.

The first complete recording of each track is kept; later plays of it are skipped.

  archive.py            run as a service
  archive.py --once     one pass, then exit
  archive.py --status   what's recorded, and why recent sides were or weren't
"""
import argparse
import json
import logging
import os
import re
import shutil
import sqlite3
import subprocess
import sys
import time

import numpy as np
import turntable_config
CFG = turntable_config.load()

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
log = logging.getLogger("turntable-archive")
DB_PATH = CFG["paths"]["db"]
RECORD_DIR = CFG["paths"]["recordings"]
SPOOL_DIR = CFG["paths"]["spool"]
LIBRARY = CFG["archive"]["library"]
COVERS_DIR = CFG["paths"]["covers"]
OLD_CAPTURE = "/var/lib/turntable/capture"        # v1.22's raw capture, no longer used
SETTLE = 90                    # a side is cut this many seconds after it ends
LOOK_BACK = 6 * 86400          # sides older than this are left alone
SR = 11025                     # analysis rate
FRAME = 0.05                   # analysis frame, seconds
SIDE_TOLERANCE = (15.0, 0.05)  # a side matches its tracklist within 15 s or 5%, whichever is more
SNAP = 5.0                     # a boundary moves at most this far to find the quiet

SCHEMA = """
CREATE TABLE IF NOT EXISTS album_tracks (
    release_id   INTEGER NOT NULL,
    position     TEXT NOT NULL,
    track_no     INTEGER NOT NULL,
    title        TEXT NOT NULL,
    path         TEXT NOT NULL,          -- relative to the library (and to the spool, while it waits there)
    seconds      REAL NOT NULL,
    recorded_at  INTEGER NOT NULL,       -- when this take was played
    status       TEXT NOT NULL,          -- spool (on the Pi, waiting) or library (on the NAS)
    PRIMARY KEY (release_id, position)
);
CREATE TABLE IF NOT EXISTS archive_sides (
    release_id  INTEGER NOT NULL,
    side        TEXT NOT NULL,
    started_at  INTEGER NOT NULL,        -- when this spin of the side began (play log)
    at          INTEGER NOT NULL,
    result      TEXT NOT NULL,
    PRIMARY KEY (release_id, side, started_at)
);
"""


def connect():
    db = sqlite3.connect(DB_PATH, timeout=10)
    db.row_factory = sqlite3.Row
    db.execute("PRAGMA journal_mode=WAL")
    db.executescript(SCHEMA)
    return db


# --- The session recordings ----------------------------------------------------------------------

def recording_for(db, a: float, b: float):
    """The session recording holding a..b: (path, the time its first byte played, its end)."""
    for r in db.execute("SELECT * FROM recordings WHERE started_at <= ? ORDER BY started_at DESC LIMIT 4", (a + 1,)):
        path = os.path.join(RECORD_DIR, r["file"])
        if not os.path.exists(path):
            continue
        bps = (r["kbps"] or 320) * 1000 / 8
        end = r["ended_at"] if r["status"] == "done" and r["ended_at"] else r["started_at"] + os.path.getsize(path) / bps
        if end >= b - 1:
            return path, float(r["started_at"]), float(end)
    return None


def decode(rec, a: float, b: float):
    """Mono audio at the analysis rate for a..b (clipped to the recording): (audio, actual start)."""
    path, r0, r1 = rec
    a, b = max(a, r0), min(b, r1)
    if b - a < 1:
        return None
    raw = subprocess.run(["ffmpeg", "-nostdin", "-loglevel", "error", "-ss", f"{a - r0:.3f}", "-t", f"{b - a:.3f}",
                          "-i", path, "-ac", "1", "-ar", str(SR), "-f", "s16le", "-"], capture_output=True, timeout=300).stdout
    return (np.frombuffer(raw, dtype="<i2").astype(np.float32) / 32768.0, a) if raw else None


def levels(x: np.ndarray) -> np.ndarray:
    n = int(SR * FRAME)
    k = len(x) // n
    if k == 0:
        return np.zeros(0)
    f = x[:k * n].reshape(k, n)
    return 20 * np.log10(np.sqrt(np.mean(f ** 2, axis=1)) + 1e-9)


def quiet_threshold(db: np.ndarray) -> float:
    music = db[db > -50]
    p80 = float(np.percentile(music, 80)) if len(music) else -18.0
    return min(-38.0, p80 - 22.0)


def quiet_runs(db: np.ndarray, thr: float, min_secs: float) -> list:
    """[(first frame, frame after)] of runs below the threshold lasting at least min_secs."""
    q = db < thr
    runs, i = [], 0
    while i < len(q):
        if q[i]:
            j = i
            while j < len(q) and q[j]:
                j += 1
            if (j - i) * FRAME >= min_secs:
                runs.append((i, j))
            i = j
        else:
            i += 1
    return runs


def smooth(db: np.ndarray, frames: int = 4) -> np.ndarray:
    return np.convolve(db, np.ones(frames) / frames, mode="same") if len(db) >= frames else db


class Side:
    """The levels across one side spin, for finding its edges and the quiet between tracks."""

    def __init__(self, rec, a: float, b: float):
        got = decode(rec, a, b)
        if got is None:
            raise RuntimeError("the recording doesn't hold this side")
        x, self.t0 = got
        self.db = levels(x)
        self.thr = quiet_threshold(self.db)

    def t(self, i: int) -> float:
        return self.t0 + i * FRAME

    def i(self, t: float) -> int:
        return int(round((t - self.t0) / FRAME))

    def needle(self, near: float):
        """The needle's first music: music after at least a second of quiet, the last such before
        about `near` (the play log can catch the first track up to 20 s late)."""
        q = self.db < self.thr
        lo, hi = max(20, self.i(near - 60)), min(len(q), self.i(near + 25))
        onsets = [k for k in range(lo, hi) if not q[k] and q[k - 20:k].all()]
        if not onsets:
            return None
        before = [k for k in onsets if self.t(k) <= near + 3]
        return self.t(max(before) if before else min(onsets)) - 0.25

    def runout(self, after: float, near: float):
        """Where the music stops before the run-out: the first 5 s of quiet after `after`,
        searched up to a minute past `near`."""
        lo, hi = max(0, self.i(after)), min(len(self.db), self.i(near + 60))
        for a, b in quiet_runs(self.db[lo:hi], self.thr, 5.0):
            return self.t(lo + a) + 0.5
        return None

    def snap(self, t: float):
        """The quietest moment near t: (end of the previous track, start of the next)."""
        lo, hi = max(0, self.i(t - SNAP)), min(len(self.db), self.i(t + SNAP))
        if hi - lo < 4:
            return t, t, "listed"
        runs = quiet_runs(self.db[lo:hi], max(self.thr, float(np.percentile(self.db[lo:hi], 10)) + 3), 0.3)
        if runs:
            a, b = min(runs, key=lambda r: abs(self.t(lo + (r[0] + r[1]) / 2) - t))
            if self.t(lo + a) <= t + SNAP:
                return self.t(lo + a), self.t(lo + b), "gap"
        sm = smooth(self.db[lo:hi])
        k = lo + int(np.argmin(sm))
        return self.t(k), self.t(k), "dip"


def safe(name: str) -> str:
    name = re.sub(r'[\\/:*?"<>|\x00-\x1f]', "", name or "").strip().rstrip(".")
    return (name or "Unknown")[:120]



def cover_path(db, rid):
    r = db.execute("SELECT cover_file FROM releases WHERE release_id = ?", (rid,)).fetchone()
    if r and r[0]:
        path = os.path.join(COVERS_DIR, os.path.basename(r[0]))
        if os.path.exists(path):
            return path
    return None


def end_of(p):
    return p["ended_at"] or p["last_seen_at"] or p["started_at"]


def side_of(p, sides: dict) -> str:
    s = (p["side"] or "").strip().upper()
    if s:
        return s
    return sides.get((p["release_id"], p["track_position"]), "")


def side_spins(db, now: float) -> list:
    """Consecutive plays of one side of one record, finished at least SETTLE seconds ago."""
    sides = {(r[0], r[1]): (r[2] or "").upper() for r in db.execute("SELECT release_id, position, side FROM tracks")}
    plays = db.execute("SELECT * FROM plays WHERE started_at > ? ORDER BY started_at", (now - LOOK_BACK,)).fetchall()
    spins, cur = [], None
    for p in plays:
        side = side_of(p, sides) if p["release_id"] else ""
        if cur and p["release_id"] == cur["rid"] and side == cur["side"] and p["started_at"] - cur["end"] <= 25:
            cur["end"] = max(cur["end"], end_of(p)); cur["plays"].append(p)
            continue
        if cur:
            spins.append(cur)
        cur = {"rid": p["release_id"], "side": side, "start": p["started_at"], "end": end_of(p), "plays": [p]} if p["release_id"] and side else None
    if cur:
        spins.append(cur)
    station = db.execute("SELECT state FROM station WHERE id = 1").fetchone()
    playing = station is not None and station[0] == "playing"
    out = []
    for k, sp in enumerate(spins):
        last = k == len(spins) - 1
        if sp["end"] > now - SETTLE or (last and playing and any(p["ended_at"] is None for p in sp["plays"])):
            continue                                   # still playing, or only just ended
        out.append(sp)
    return out


def tracks_on(db, rid: int, side: str) -> list:
    import infer
    return [t for t in infer.tracklist(db, rid) if (t.get("side") or "").upper() == side]


def plan_side(db, sp: dict, rec) -> dict:
    """Where each track on this side spin starts and ends, and which are complete."""
    tl = tracks_on(db, sp["rid"], sp["side"])
    if not tl:
        return {"ok": False, "why": "no tracklist for this side"}
    S = Side(rec, sp["start"] - 75, sp["end"] + 75)
    needle = S.needle(sp["start"])
    if needle is None:
        return {"ok": False, "why": "the needle drop wasn't found"}
    runout = S.runout(max(needle + 30, sp["end"] - 40), sp["end"])
    secs = [t["secs"] for t in tl]
    cuts = []
    if all(secs):
        listed = float(sum(secs))
        tol = max(SIDE_TOLERANCE[0], SIDE_TOLERANCE[1] * listed)
        if runout is not None and abs((runout - needle) - listed) <= tol:
            scale, stop, how = (runout - needle) / listed, runout, "whole side"
        elif runout is not None and (runout - needle) < listed - tol:
            scale, stop, how = 1.0, runout, "needle lifted"      # the side stopped early: keep what played in full
        elif runout is None:
            scale, stop, how = 1.0, sp["end"] + 20, "run-out not heard"
        else:
            return {"ok": False, "why": f"side ran {runout - needle:.0f} s against a listed {listed:.0f} s"}
        bounds, cum = [], 0.0
        for k in range(len(tl) - 1):
            cum += secs[k]
            bounds.append(S.snap(needle + scale * cum))
        starts = [needle] + [b[1] for b in bounds]
        ends = [b[0] for b in bounds] + [runout if (runout is not None and how == "whole side") else needle + scale * listed]
        for k, t in enumerate(tl):
            a, b = starts[k], ends[k]
            complete = b <= stop + 1 and abs((b - a) - scale * secs[k]) <= max(8.0, 0.08 * secs[k])
            cuts.append({"track": t, "no": t["seq"], "a": a, "b": b, "ok": complete})
        n_ok = sum(c["ok"] for c in cuts)
        return {"ok": n_ok > 0, "why": f"{how}: {n_ok} of {len(tl)} complete", "cuts": cuts}
    # No listed lengths: only the gaps heard can place the tracks
    if runout is None:
        return {"ok": False, "why": "no listed lengths, and the run-out wasn't heard"}
    lo, hi = S.i(needle + 20), S.i(runout - 20)
    runs = quiet_runs(S.db[lo:hi], S.thr, 0.6)
    if len(runs) != len(tl) - 1:
        return {"ok": False, "why": f"no listed lengths, and {len(runs)} gaps heard for {len(tl)} tracks"}
    starts = [needle] + [S.t(lo + r[1]) for r in runs]
    ends = [S.t(lo + r[0]) for r in runs] + [runout]
    cuts = [{"track": t, "no": t["seq"], "a": starts[k], "b": ends[k], "ok": True} for k, t in enumerate(tl)]
    return {"ok": True, "why": f"gaps: {len(tl)} of {len(tl)} complete", "cuts": cuts}


def export(db, rid: int, cut: dict, rec, played_at: float) -> str:
    """Copy one track out of the recording (no re-encoding), tag it, and spool it. Returns its path
    relative to the library."""
    r = db.execute("SELECT * FROM releases WHERE release_id = ?", (rid,)).fetchone()
    cols = r.keys()
    year = (r["original_year"] if "original_year" in cols and r["original_year"] else None) or r["year"] or ""
    styles = json.loads(r["styles"] or "[]") if r["styles"] else []
    genres = json.loads(r["genres"] or "[]") if r["genres"] else []
    t = cut["track"]
    album_dir = f"{safe(r['artist'])}/{safe(r['title'])}" + (f" ({year})" if year else "")
    rel = f"{album_dir}/{cut['no']:02d} - {safe(t['title'])}.mp3"
    out = os.path.join(SPOOL_DIR, rel)
    os.makedirs(os.path.dirname(out), exist_ok=True)
    path, r0, _ = rec
    cover = cover_path(db, rid)
    tags = {"title": t["title"], "artist": r["artist"], "album_artist": r["artist"], "album": r["title"],
            "track": str(cut["no"]), "date": str(year), "genre": (styles or genres or [""])[0],
            "comment": f"Recorded from vinyl by Turntable-1, {time.strftime('%Y-%m-%d', time.localtime(played_at))}"}
    cmd = ["ffmpeg", "-nostdin", "-y", "-loglevel", "error", "-ss", f"{cut['a'] - r0:.3f}", "-t", f"{cut['b'] - cut['a']:.3f}", "-i", path]
    if cover:
        cmd += ["-i", cover]
    cmd += ["-map", "0:a"]
    if cover:
        cmd += ["-map", "1:v", "-disposition:v", "attached_pic", "-metadata:s:v", "title=Album cover", "-metadata:s:v", "comment=Cover (front)"]
    cmd += ["-c", "copy", "-id3v2_version", "3"]
    for k, v in tags.items():
        if v:
            cmd += ["-metadata", f"{k}={v}"]
    tmp = out + ".part"
    try:
        subprocess.run(cmd + ["-f", "mp3", tmp], check=True, capture_output=True, timeout=300)
        os.replace(tmp, out)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)
    if cover and not os.path.exists(os.path.join(SPOOL_DIR, album_dir, "cover.jpg")):
        shutil.copyfile(cover, os.path.join(SPOOL_DIR, album_dir, "cover.jpg"))
    return rel


def process(db) -> int:
    import infer
    infer.forget_tracklists()                      # tracklists can change with a Discogs sync
    saved, now = 0, time.time()
    done = {(r[0], r[1], r[2]) for r in db.execute("SELECT release_id, side, started_at FROM archive_sides")}
    for sp in side_spins(db, now):
        key = (sp["rid"], sp["side"], int(sp["start"]))
        if key in done:
            continue
        owned = db.execute("SELECT 1 FROM releases WHERE release_id = ? AND in_collection = 1", (sp["rid"],)).fetchone()
        have = {r[0] for r in db.execute("SELECT position FROM album_tracks WHERE release_id = ?", (sp["rid"],))}
        tl = tracks_on(db, sp["rid"], sp["side"])
        result = None
        if not owned:
            result = "not in the cargo hold"
        elif tl and all(t["position"] in have for t in tl):
            result = "already recorded"
        else:
            rec = recording_for(db, sp["start"] - 75, sp["end"] + 75) or recording_for(db, sp["start"] - 5, sp["end"] + 5)
            if rec is None:
                result = "no session recording holds this side"
            else:
                try:
                    plan = plan_side(db, sp, rec)
                except Exception as e:
                    plan = {"ok": False, "why": f"error: {e}"}
                result = plan["why"]
                for c in plan.get("cuts", []):
                    if not c["ok"] or c["track"]["position"] in have:
                        continue
                    try:
                        rel = export(db, sp["rid"], c, rec, sp["start"])
                    except Exception as e:
                        log.warning("Saving %s: %s", c["track"]["title"], e)
                        continue
                    with db:
                        db.execute("INSERT OR IGNORE INTO album_tracks (release_id, position, track_no, title, path, seconds, recorded_at, status) "
                                   "VALUES (?, ?, ?, ?, ?, ?, ?, 'spool')", (sp["rid"], c["track"]["position"], c["no"], c["track"]["title"],
                                                                             rel, round(c["b"] - c["a"], 2), int(sp["start"])))
                    log.info("Recorded %s (%.0f s)", rel, c["b"] - c["a"])
                    saved += 1
        with db:
            db.execute("INSERT OR REPLACE INTO archive_sides (release_id, side, started_at, at, result) VALUES (?, ?, ?, ?, ?)",
                       (key[0], key[1], key[2], int(now), result))
        title = db.execute("SELECT title FROM releases WHERE release_id = ?", (sp["rid"],)).fetchone()
        log.info("%s, side %s: %s", title[0] if title else sp["rid"], sp["side"], result)
    return saved


# --- To the library, and tidying up --------------------------------------------------------------

def library_ready() -> bool:
    try:
        if not os.path.isdir(LIBRARY):
            return False
        probe = os.path.join(LIBRARY, ".turntable-write-test")
        with open(probe, "w") as f:
            f.write("ok")
        os.unlink(probe)
        return True
    except OSError:
        return False


def move_to_library(db) -> int:
    waiting = db.execute("SELECT release_id, position, path FROM album_tracks WHERE status = 'spool'").fetchall()
    if not waiting or not library_ready():
        return 0
    moved = 0
    for w in waiting:
        src, dst = os.path.join(SPOOL_DIR, w["path"]), os.path.join(LIBRARY, w["path"])
        try:
            os.makedirs(os.path.dirname(dst), exist_ok=True)
            shutil.copyfile(src, dst + ".part")
            os.replace(dst + ".part", dst)
            cov_src = os.path.join(os.path.dirname(src), "cover.jpg")
            cov_dst = os.path.join(os.path.dirname(dst), "cover.jpg")
            if os.path.exists(cov_src) and not os.path.exists(cov_dst):
                shutil.copyfile(cov_src, cov_dst)
            with db:
                db.execute("UPDATE album_tracks SET status = 'library' WHERE release_id = ? AND position = ?", (w["release_id"], w["position"]))
            os.unlink(src)
            moved += 1
        except OSError as e:
            log.warning("Moving %s to the library: %s", w["path"], e)
            break                                         # the library went away; try again later
    for d, _, files in sorted(os.walk(SPOOL_DIR), reverse=True):
        if d != SPOOL_DIR and all(f == "cover.jpg" for f in files):
            if not db.execute("SELECT 1 FROM album_tracks WHERE status = 'spool' AND path LIKE ?",
                              (os.path.relpath(d, SPOOL_DIR) + "/%",)).fetchone():
                shutil.rmtree(d, ignore_errors=True)
    if moved:
        log.info("Moved %d track(s) to the library", moved)
    return moved


def tidy(db):
    """v1.22's raw capture isn't used any more: clear what's left of it."""
    if os.path.isdir(OLD_CAPTURE):
        for name in os.listdir(OLD_CAPTURE):
            if name.endswith(".flac"):
                try:
                    os.unlink(os.path.join(OLD_CAPTURE, name))
                except OSError:
                    pass


def status(db):
    rows = db.execute("SELECT r.artist, r.title, COUNT(t.position) AS n, SUM(t.status = 'spool') AS waiting, "
                      "(SELECT COUNT(*) FROM tracks k WHERE k.release_id = r.release_id) AS total "
                      "FROM album_tracks t JOIN releases r USING (release_id) GROUP BY r.release_id ORDER BY r.artist, r.title").fetchall()
    for r in rows:
        print(f"{r['artist']} - {r['title']}: {r['n']} of {r['total']} tracks" + (f" ({r['waiting']} waiting for the library)" if r["waiting"] else ""))
    print(f"\nLibrary: {LIBRARY} ({'reachable' if library_ready() else 'not reachable'})")
    print("Recent sides:")
    for r in db.execute("SELECT s.*, r.title FROM archive_sides s LEFT JOIN releases r USING (release_id) WHERE s.at > ? "
                        "ORDER BY s.started_at DESC LIMIT 30", (time.time() - 7 * 86400,)):
        print(f"  {time.strftime('%a %H:%M', time.localtime(r['started_at']))}  {r['title'] or r['release_id']}, side {r['side']}: {r['result']}")


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--once", action="store_true")
    ap.add_argument("--status", action="store_true")
    args = ap.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    os.makedirs(SPOOL_DIR, exist_ok=True)
    db = connect()
    if args.status:
        status(db)
        return
    log.info("Album archive: from the session recordings in %s, to %s", RECORD_DIR, LIBRARY)
    while True:
        for step in (process, move_to_library, tidy):
            try:
                step(db)
            except Exception as e:
                log.warning("%s: %s", step.__name__, e)
        if args.once:
            break
        time.sleep(30)


if __name__ == "__main__":
    main()
