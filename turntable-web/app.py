"""Turntable web UI: now playing, play history and listening stats.

Strictly read-only. The play log is opened with SQLite's read-only mode, every
route is a GET, and the only upstream it talks to is the local Icecast mount.
"""
import datetime
import asyncio
import json
import re
import os
import random
import sqlite3
import time
from pathlib import Path

import httpx
from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles

# settings: the shared loader (turntable-id/turntable_config.py), the same one every service uses
import sys as _sys
_sys.path.insert(0, os.environ.get("TURNTABLE_CORE", os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "turntable-id")))
import turntable_config
CFG = turntable_config.load()
DB_PATH     = CFG["paths"]["db"]
COVER_DIR   = CFG["paths"]["covers"]
STREAM_URL  = turntable_config.stream_url(CFG)
SESSION_GAP = 30 * 60   # a gap longer than this starts a new listening session
STATIC      = Path(__file__).parent / "static"

app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)

CSP = (
    "default-src 'self'; "
    "img-src 'self' data: https:; "
    "style-src 'self'; "
    "font-src 'self'; "
    "media-src 'self'; "
    "connect-src 'self'; "
    "frame-ancestors 'none'; base-uri 'none'; form-action 'none'"
)


# Google Cast: the main page may load Cast's sender script (the Cast button); the receiver page
# (what a Cast device shows) may load Cast's receiver script and talk to the device's local channel.
# Both from Google's script host only; everything else keeps the policy above.
CSP_MAIN = CSP.replace("default-src 'self'; ", "default-src 'self'; script-src 'self' https://www.gstatic.com; ")
CSP_RECEIVER = (
    "default-src 'self'; "
    "script-src 'self' https://www.gstatic.com; "
    "img-src 'self' data: https:; "
    "style-src 'self' 'unsafe-inline'; "
    "font-src 'self'; "
    "media-src 'self' blob: data:; "
    "connect-src 'self' https://www.gstatic.com ws: wss:; "
    "base-uri 'none'; form-action 'none'"
)


import settings_api
app.include_router(settings_api.router)
settings_api.on_saved.append(lambda: (CFG.clear(), CFG.update(turntable_config.load())))


@app.middleware("http")
async def require_sign_in(request: Request, call_next):
    """Editing (tagging, fixing plays) needs the admin sign-in, unless the settings say otherwise."""
    if request.method == "POST" and request.url.path.startswith("/api/edit/") and CFG["web"].get("require_sign_in_to_edit", True) \
            and not settings_api.signed_in(request):
        return JSONResponse({"detail": "Sign in to make changes (Settings, at the top of the page)."}, status_code=401)
    return await call_next(request)


@app.middleware("http")
async def security_headers(request: Request, call_next):
    response = await call_next(request)
    path = request.url.path
    response.headers["Content-Security-Policy"] = (CSP_RECEIVER if path == "/static/receiver.html"
                                                   else CSP_MAIN if path == "/" else CSP)
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["Referrer-Policy"] = "no-referrer"
    response.headers["X-Frame-Options"] = "DENY"
    if request.url.path.startswith("/api/"):
        response.headers["Cache-Control"] = "no-store"
    elif request.url.path == "/" or request.url.path.startswith("/static/"):
        # check for a newer copy on every load, so an update never sticks behind a cached old file
        response.headers["Cache-Control"] = "no-cache"
    return response


# --- Data access -------------------------------------------------------------

def connect():
    """Read-only connection, or None before the ID script has created the log."""
    if not os.path.exists(DB_PATH):
        return None
    con = sqlite3.connect(f"file:{DB_PATH}?mode=ro", uri=True, timeout=5)
    con.row_factory = sqlite3.Row
    return con


def end_of(row) -> int:
    return row["ended_at"] if row["ended_at"] is not None else row["last_seen_at"]


def col(row, name, default=None):
    return row[name] if name in row.keys() else default


def play_dict(row) -> dict:
    cover = f"/covers/{row['cover_file']}" if row["cover_file"] else (row["cover_url"] or None)
    return {
        "id": row["id"],
        "release_id": col(row, "release_id"),
        "side": col(row, "side", "") or "",
        "inferred": bool(col(row, "inferred", 0)),      # filled in from the tracklist, not heard
        "manual": col(row, "source", "") == "manual",   # tagged or checked off by hand
        "position": col(row, "track_position", "") or "",
        "artist": row["artist"],
        "title": row["title"],
        "album": row["album"],
        "genre": row["genre"],
        "label": row["label"],
        "released": row["released"],
        "started_at": row["started_at"],
        "ended_at": row["ended_at"],
        "seconds": max(0, end_of(row) - row["started_at"]),
        "cover": cover,
    }


def release_cover(r):
    return f"/covers/{r['cover_file']}" if r["cover_file"] else None


def original_year(r):
    """When the music first came out (years.py: MusicBrainz or the Discogs master), else the pressing's year."""
    keys = r.keys() if hasattr(r, "keys") else ()
    oy = r["original_year"] if "original_year" in keys else None
    return oy or r["year"]


def year_sql(con, alias: str = "") -> str:
    """The same in SQL, for sorting and selecting (falls back while the column is still to be added)."""
    a = f"{alias}." if alias else ""
    cols = {c[1] for c in con.execute("PRAGMA table_info(releases)")}
    return f"COALESCE(NULLIF({a}original_year, 0), {a}year)" if "original_year" in cols else f"{a}year"


def release_dict(r, plays=None, last_played=None, spins=None) -> dict:
    return {
        "id": r["release_id"],
        "title": r["title"],
        "artist": r["artist"],
        "year": original_year(r),
        "pressing_year": r["year"] if r["year"] and original_year(r) and r["year"] != original_year(r) else None,
        "label": r["label"],
        "catno": r["catno"],
        "format": r["format"],
        "genres": json.loads(r["genres"] or "[]"),
        "styles": json.loads(r["styles"] or "[]"),
        "cover": release_cover(r),
        "date_added": r["date_added"],
        "plays": plays,          # tracks heard
        "spins": spins,          # times the record was played
        "dust": dust_level(last_played),
        "sealed": not last_played and not plays,
        "last_played": last_played,
        "discogs_url": f"https://www.discogs.com/release/{r['release_id']}",
    }


def to_secs(d):
    """'3:12' -> 192 (track lengths from Discogs); None when missing or odd."""
    import re
    m = re.fullmatch(r"\s*(?:(\d+):)?(\d+)[:.](\d{1,2})\s*", d or "")
    if not m:
        return None
    t = int(m.group(1) or 0) * 3600 + int(m.group(2)) * 60 + int(m.group(3))
    return t if 20 <= t <= 3600 else None


def how_much(con, release_id, positions) -> str:
    """'the whole record', 'side A', 'sides B to D', 'sides A and C' or 'a few tracks'."""
    positions = {p for p in positions if p}
    if not release_id or not positions:
        return ""
    rows = con.execute("SELECT position, side FROM tracks WHERE release_id = ?", (release_id,)).fetchall()
    if not rows:
        return ""
    if len(positions) >= 0.8 * len(rows):
        return "the whole record"
    by_side = {}
    for pos, side in rows:
        by_side.setdefault(side, set()).add(pos)
    sides = sorted(sd for sd, ps in by_side.items() if sd and len(ps & positions) >= max(1, 0.5 * len(ps)))
    if not sides:
        return "a few tracks"
    if len(sides) == 1:
        return f"side {sides[0]}"
    letters = "ABCDEFGHIJKLMNOP"
    run = all(letters.index(b) - letters.index(a) == 1 for a, b in zip(sides, sides[1:]) if a in letters and b in letters)
    return f"sides {sides[0]} to {sides[-1]}" if run and len(sides) > 2 else "sides " + " and ".join(sides) if len(sides) == 2 \
        else "sides " + ", ".join(sides[:-1]) + " and " + sides[-1]


DUST_DAYS = (60, 120, 180)   # dust builds up in three stages after this many days without a spin


def dust_level(last_played) -> int:
    if not last_played:
        return 0
    days = (time.time() - last_played) / 86400
    return sum(1 for d in DUST_DAYS if days >= d)


def ago_words(seconds: float) -> str:
    d = seconds / 86400
    if d < 1:
        h = max(1, round(seconds / 3600))
        return f"{h} hour{'s' if h != 1 else ''} ago"
    if d < 45:
        return f"{round(d)} day{'s' if round(d) != 1 else ''} ago"
    m = round(d / 30)
    if m < 18:
        return f"{m} month{'s' if m != 1 else ''} ago"
    return f"{round(d / 365)} years ago"


def has_catalog(con) -> bool:
    return con.execute(
        "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'releases'").fetchone() is not None \
        and "release_id" in {r[1] for r in con.execute("PRAGMA table_info(plays)")}


def local_midnight(now: float) -> int:
    lt = time.localtime(now)
    return int(time.mktime((lt.tm_year, lt.tm_mon, lt.tm_mday, 0, 0, 0, 0, 0, -1)))


SPIN_GAP = 30 * 60   # tracks from the same record this close together are one spin


def load_spins(con, since: int = 0) -> list:
    """Group plays into spins: consecutive tracks from the same record count once.

    A record is its Discogs release when matched, otherwise artist and album as Shazam
    named them. Flipping to side B, or a short pause, stays the same spin; another
    record in between, or a gap longer than SPIN_GAP, starts a new one.
    """
    has_rel = "release_id" in {r[1] for r in con.execute("PRAGMA table_info(plays)")}
    rel = "release_id" if has_rel else "NULL AS release_id"
    pos = "track_position" if has_rel else "'' AS track_position"
    rows = con.execute(
        f"SELECT {rel}, {pos}, artist, album, cover_file, cover_url, started_at, "
        "COALESCE(ended_at, last_seen_at) AS end_at FROM plays WHERE started_at >= ? ORDER BY started_at",
        (since,)).fetchall()
    spins, cur = [], None
    for r in rows:
        key = ("r", r["release_id"]) if r["release_id"] else \
              ("a", (r["artist"] or "").strip().lower(), (r["album"] or "").strip().lower())
        seconds = max(0, r["end_at"] - r["started_at"])
        if cur and cur["key"] == key and r["started_at"] - cur["end"] <= SPIN_GAP:
            cur["end"] = max(cur["end"], r["end_at"])
            cur["tracks"] += 1
            cur["seconds"] += seconds
            cur["positions"].add(r["track_position"])
        else:
            cur = {"key": key, "release_id": r["release_id"], "artist": r["artist"], "album": r["album"],
                   "cover_file": r["cover_file"], "cover_url": r["cover_url"],
                   "start": r["started_at"], "end": r["end_at"], "tracks": 1, "seconds": seconds,
                   "positions": {r["track_position"]}}
            spins.append(cur)
    return spins


def spins_by_release(con) -> dict:
    """release_id -> number of spins, all time."""
    out = {}
    for sp in load_spins(con):
        if sp["release_id"]:
            out[sp["release_id"]] = out.get(sp["release_id"], 0) + 1
    return out


def group_sessions(plays: list) -> list:
    """Plays arrive newest first; split them wherever the gap exceeds SESSION_GAP."""
    sessions = []
    for p in plays:
        end = p["ended_at"] or (p["started_at"] + p["seconds"])
        if sessions and sessions[-1]["start"] - end <= SESSION_GAP:
            s = sessions[-1]
            s["plays"].append(p)
            s["start"] = p["started_at"]
        else:
            sessions.append({"start": p["started_at"], "end": end, "plays": [p]})
    for s in sessions:
        s["seconds"] = sum(p["seconds"] for p in s["plays"])
    return sessions


# --- API ---------------------------------------------------------------------

def side_view(con, play, now: float):
    """The current side for the timeline: each track's state, where we are, and time left."""
    pos = col(play, "track_position", "")
    if not pos:
        return None
    rows = con.execute("SELECT position, side, title, duration FROM tracks WHERE release_id = ? ORDER BY seq",
                       (play["release_id"],)).fetchall()
    cur = next((r for r in rows if r["position"] == pos), None)
    if cur is None:
        return None
    side = [r for r in rows if r["side"] == cur["side"]] if cur["side"] else rows
    # what this spin has logged so far: heard, or filled in from the tracklist
    logged = {}
    for r in con.execute("SELECT track_position, MIN(inferred) FROM plays WHERE release_id = ? AND started_at >= ? "
                         "GROUP BY track_position", (play["release_id"], play["started_at"] - 3 * 3600)):
        logged[r[0]] = "probable" if r[1] else "heard"
    idx = next(i for i, r in enumerate(side) if r["position"] == pos)
    tracks = []
    for i, r in enumerate(side):
        state = "now" if i == idx else "next" if i > idx else logged.get(r["position"], "passed")
        tracks.append({"position": r["position"], "title": r["title"], "secs": to_secs(r["duration"]), "state": state})
    elapsed = max(0, int(now - play["started_at"]))
    secs = tracks[idx]["secs"]
    known = all(t["secs"] for t in tracks[idx:])
    left = (max(0, secs - elapsed) + sum(t["secs"] for t in tracks[idx + 1:])) if known else None
    return {"side": cur["side"], "tracks": tracks, "index": idx, "count": len(tracks),
            "elapsed": min(elapsed, secs) if secs else elapsed, "side_left": left,
            "last_on_side": idx == len(tracks) - 1, "durations": all(t["secs"] for t in tracks)}


@app.get("/api/now")
def now_playing():
    now = time.time()
    con = connect()
    if con is None:
        return {"state": "idle", "current": None, "today": [], "now": int(now),
                "day_start": local_midnight(now), "today_seconds": 0}
    with con:
        station = con.execute("SELECT * FROM station WHERE id = 1").fetchone()
        current = None
        if station and station["play_id"]:
            row = con.execute("SELECT * FROM plays WHERE id = ?", (station["play_id"],)).fetchone()
            current = play_dict(row) if row else None
            if current:
                # Ground Control's transmission for this track, once it's been sent
                try:
                    gc = con.execute("SELECT at, text, source FROM transmissions WHERE voice = 'ground' AND play_id = ?",
                                     (station["play_id"],)).fetchone()
                    current["ground"] = {"at": gc["at"], "text": gc["text"], "source": gc["source"]} if gc else None
                except sqlite3.OperationalError:
                    current["ground"] = None           # Ground Control hasn't run yet
            if current and current["release_id"] and has_catalog(con):
                rel = con.execute("SELECT * FROM releases WHERE release_id = ?", (current["release_id"],)).fetchone()
                if rel:
                    n = con.execute("SELECT COUNT(*) FROM plays WHERE release_id = ?", (rel["release_id"],)).fetchone()[0]
                    current["release"] = release_dict(rel, plays=n, spins=spins_by_release(con).get(rel["release_id"], 0))
                    current["side_view"] = side_view(con, row, now)
        day_start = local_midnight(now)
        rows = con.execute(
            "SELECT * FROM plays WHERE COALESCE(ended_at, last_seen_at) >= ? ORDER BY started_at",
            (day_start,),
        ).fetchall()
    today = [{"start": max(r["started_at"], day_start), "end": end_of(r)} for r in rows]
    return {
        "state": station["state"] if station else "idle",
        "current": current,
        "now": int(now),
        "day_start": day_start,
        "today": today,
        "today_seconds": sum(t["end"] - t["start"] for t in today),
    }


def spin_card(con, sp) -> dict:
    cover = None
    if sp["release_id"]:
        r = con.execute("SELECT title, artist, cover_file FROM releases WHERE release_id = ?", (sp["release_id"],)).fetchone()
        if r:
            cover = f"/covers/{r['cover_file']}" if r["cover_file"] else None
            title, artist = r["title"], r["artist"]
    if not sp["release_id"] or not cover and not sp.get("album"):
        title, artist = sp["album"] or "Unknown record", sp["artist"]
    if not cover:
        cover = f"/covers/{sp['cover_file']}" if sp.get("cover_file") else (sp.get("cover_url") or None)
    return {"release_id": sp["release_id"], "title": title, "artist": artist, "cover": cover,
            "start": sp["start"], "end": sp["end"], "seconds": sp["seconds"],
            "how": how_much(con, sp["release_id"], sp["positions"])}


@app.get("/api/summary")
def summary():
    """For the Tonight strip and the idle now-playing cards."""
    now = time.time()
    con = connect()
    out = {"tonight": None, "last_spin": None, "today": None, "waiting": None}
    if con is None:
        return out
    with con:
        recent = load_spins(con, int(now - 12 * 3600))
        if recent:
            # the latest session: spins with no more than 30 minutes between them
            sess = [recent[-1]]
            for sp in reversed(recent[:-1]):
                if sess[-1]["start"] - sp["end"] > SESSION_GAP:
                    break
                sess.append(sp)
            out["tonight"] = {"records": [spin_card(con, sp) for sp in sess], "seconds": sum(sp["seconds"] for sp in sess),
                              "since": sess[-1]["start"], "live": now - sess[0]["end"] < 600}
        last = load_spins(con, int(now - 60 * 86400))
        if last:
            out["last_spin"] = spin_card(con, last[-1])
        today = load_spins(con, local_midnight(now))
        if today:
            out["today"] = {"records": len(today), "seconds": sum(sp["seconds"] for sp in today),
                            "titles": [spin_card(con, sp)["title"] for sp in today][-3:]}
    d = dusty(limit=1)["items"]
    out["waiting"] = d[0] if d else None
    return out


@app.get("/api/history")
def history(before: int | None = None, limit: int = Query(60, ge=1, le=300)):
    con = connect()
    if con is None:
        return {"sessions": [], "next_before": None}
    with con:
        rows = con.execute(
            "SELECT * FROM plays WHERE started_at < ? ORDER BY started_at DESC LIMIT ?",
            (before or 2**62, limit),
        ).fetchall()
        plays = [play_dict(r) for r in rows]
        sessions = group_sessions(plays)
        for s in sessions:
            s["gaps"] = find_gaps(con, s["start"], s["end"])
    return {
        "sessions": sessions,
        "next_before": plays[-1]["started_at"] if len(plays) == limit else None,
    }


# --- Edits: the one way the page changes anything ------------------------------------
# The page only queues a request; the identifier validates and applies it within a second,
# so it stays the one writer of plays and the station (and updates Music Assistant too).

EDIT_KINDS = ("tag", "check", "fill", "remove", "link", "dismiss")
EDIT_RATE = 30              # requests a minute, at most
_edit_times: list = []


def edit_guard(request: Request) -> None:
    """Refuse edits that didn't come from this page.

    The page's scripts send a custom header; another website can't add one to a request
    to this app without a CORS preflight, which this app never approves. An Origin header,
    when present, must match the host the request came in on."""
    if request.headers.get("x-turntable-edit") != "1":
        raise HTTPException(403, "Edits come from the page only.")
    origin = request.headers.get("origin")
    if origin:
        from urllib.parse import urlparse
        host = request.headers.get("x-forwarded-host") or request.headers.get("host", "")
        if urlparse(origin).netloc.lower() != host.lower():
            raise HTTPException(403, "Edits come from the page only.")
    now = time.time()
    _edit_times[:] = [t for t in _edit_times if now - t < 60]
    if len(_edit_times) >= EDIT_RATE:
        raise HTTPException(429, "Too many edits at once; try again in a minute.")
    _edit_times.append(now)


def _int(v, name, lo=1, hi=2**62):
    if not isinstance(v, int) or isinstance(v, bool) or not lo <= v <= hi:
        raise HTTPException(400, f"Bad {name}.")
    return v


def _pos(v):
    if not isinstance(v, str) or not 1 <= len(v) <= 12:
        raise HTTPException(400, "Bad track position.")
    return v


def validate_edit(kind: str, body) -> dict:
    if not isinstance(body, dict):
        raise HTTPException(400, "Expected a JSON object.")
    if kind == "remove":
        return {"play_id": _int(body.get("play_id"), "play")}
    if kind in ("link", "dismiss"):
        ids = body.get("play_ids")
        if not isinstance(ids, list) or not 1 <= len(ids) <= 500:
            raise HTTPException(400, "Pick between 1 and 500 plays.")
        out = {"play_ids": [_int(i, "play") for i in ids]}
        if kind == "link":
            out["release_id"] = _int(body.get("release_id"), "record")
            out["remember"] = bool(body.get("remember"))
        return out
    out = {"release_id": _int(body.get("release_id"), "record")}
    if kind == "tag":
        out["position"] = _pos(body.get("position"))
        return out
    positions = body.get("positions")
    if not isinstance(positions, list) or not 1 <= len(positions) <= 40:
        raise HTTPException(400, "Pick between 1 and 40 tracks.")
    out["positions"] = [_pos(p) for p in positions]
    if kind == "check":
        if body.get("around") is not None:
            out["around"] = _int(body.get("around"), "time")
        return out
    out["start"] = _int(body.get("start"), "start time")
    out["end"] = _int(body.get("end"), "end time")
    if out["end"] <= out["start"]:
        raise HTTPException(400, "The gap ends before it starts.")
    return out


@app.post("/api/edit/{kind}")
async def edit(kind: str, request: Request):
    if kind not in EDIT_KINDS:
        raise HTTPException(404, "Unknown edit.")
    edit_guard(request)
    try:
        body = await request.json()
    except ValueError:
        raise HTTPException(400, "Expected JSON.")
    clean = validate_edit(kind, body)
    if not os.path.exists(DB_PATH):
        raise HTTPException(503, "The play log isn't ready yet.")
    con = sqlite3.connect(DB_PATH, timeout=5)
    try:
        with con:
            cur = con.execute("INSERT INTO edit_requests (at, kind, payload) VALUES (?, ?, ?)",
                              (int(time.time()), kind, json.dumps(clean)))
        return {"id": cur.lastrowid, "status": "pending"}
    except sqlite3.OperationalError:
        raise HTTPException(503, "The identifier hasn't set up editing yet; restart turntable-id.")
    finally:
        con.close()


@app.get("/api/edit/{req_id}")
def edit_status(req_id: int):
    con = connect()
    if con is None:
        raise HTTPException(404, "No such edit.")
    with con:
        r = con.execute("SELECT status, result FROM edit_requests WHERE id = ?", (req_id,)).fetchone()
    if not r:
        raise HTTPException(404, "No such edit.")
    return {"id": req_id, "status": r["status"], "result": r["result"]}


# --- Recordings: the last few sessions, from turntable-record -------------------------

RECORD_DIR = CFG["paths"]["recordings"]


@app.get("/api/recordings")
def recordings():
    """Finished recordings, newest first, each with a marker for every play inside it."""
    con = connect()
    if con is None:
        return {"recordings": [], "recording_now": False}
    with con:
        try:
            recs = con.execute("SELECT * FROM recordings ORDER BY started_at DESC").fetchall()
        except sqlite3.OperationalError:
            return {"recordings": [], "recording_now": False}   # the recorder hasn't run yet
        out = []
        for r in recs:
            if r["status"] != "done":
                continue
            plays = con.execute("SELECT * FROM plays WHERE COALESCE(ended_at, last_seen_at) > ? AND started_at < ? "
                                "ORDER BY started_at", (r["started_at"], r["ended_at"])).fetchall()
            marks = []
            for p in plays:
                d = play_dict(p)
                marks.append({"offset": max(0, p["started_at"] - r["started_at"]),
                              "length": max(1, min(r["ended_at"], d["started_at"] + d["seconds"]) - max(r["started_at"], d["started_at"])),
                              "title": d["title"], "artist": d["artist"], "album": d["album"], "position": d["position"],
                              "release_id": d["release_id"], "inferred": d["inferred"], "manual": d["manual"],
                              "cover": d["cover"]})
            out.append({"id": r["id"], "started_at": r["started_at"], "ended_at": r["ended_at"],
                        "seconds": r["ended_at"] - r["started_at"], "bytes": r["bytes"], "url": f"/recordings/{r['id']}.mp3",
                        "marks": marks})
        now_rec = any(r["status"] == "recording" for r in recs)
    return {"recordings": out, "recording_now": now_rec}


@app.get("/recordings/{rec_id}.mp3")
def recording_file(rec_id: int, request: Request):
    """The MP3, with byte ranges so the player can seek."""
    con = connect()
    if con is None:
        raise HTTPException(404, "No such recording.")
    with con:
        try:
            r = con.execute("SELECT file FROM recordings WHERE id = ? AND status = 'done'", (rec_id,)).fetchone()
        except sqlite3.OperationalError:
            r = None
    if not r:
        raise HTTPException(404, "No such recording.")
    path = os.path.join(RECORD_DIR, os.path.basename(r["file"]))
    if not os.path.exists(path):
        raise HTTPException(404, "That recording's file is gone.")
    return ranged(path, request, "audio/mpeg")


def ranged(path: str, request: Request, media: str):
    """An audio file, with byte ranges so the player can seek."""
    size = os.path.getsize(path)
    start, end = 0, size - 1
    rng = request.headers.get("range", "")
    if rng.startswith("bytes="):
        try:
            a, b = rng[6:].split(",")[0].split("-")
            if a:
                start, end = int(a), (int(b) if b else size - 1)
            else:                                  # "bytes=-N": the last N bytes
                start, end = max(0, size - int(b)), size - 1
        except ValueError:
            raise HTTPException(416, "Bad range.")
        if start > end or start >= size:
            return JSONResponse({"detail": "Range not satisfiable."}, status_code=416, headers={"Content-Range": f"bytes */{size}"})
        end = min(end, size - 1)

    def chunks():
        with open(path, "rb") as f:
            f.seek(start)
            left = end - start + 1
            while left > 0:
                block = f.read(min(65536, left))
                if not block:
                    break
                left -= len(block)
                yield block

    headers = {"Accept-Ranges": "bytes", "Content-Length": str(end - start + 1), "Cache-Control": "no-store"}
    if rng:
        headers["Content-Range"] = f"bytes {start}-{end}/{size}"
    return StreamingResponse(chunks(), status_code=206 if rng else 200, media_type=media, headers=headers)


# --- The album archive: each record's recorded tracks (archive.py), on the NAS or waiting on the Pi --

LIBRARY = CFG["archive"]["library"]
SPOOL_DIR = CFG["paths"]["spool"]


def recorded_tracks(con, release_id: int) -> dict:
    try:
        rows = con.execute("SELECT position, seconds, path FROM album_tracks WHERE release_id = ?", (release_id,)).fetchall()
    except sqlite3.OperationalError:
        return {}                                   # the archive hasn't run yet
    ext = lambda path: "flac" if path.lower().endswith(".flac") else "mp3"
    return {r["position"]: {"seconds": r["seconds"], "url": f"/library/{release_id}/{r['position']}.{ext(r['path'])}"} for r in rows}


@app.get("/library/{release_id}/{position}.{ext}")
def library_track(release_id: int, position: str, ext: str, request: Request):
    if ext not in ("mp3", "flac"):
        raise HTTPException(404, "No such track.")
    if not re.fullmatch(r"[A-Za-z0-9._-]{1,12}", position):
        raise HTTPException(404, "No such track.")
    con = connect()
    if con is None:
        raise HTTPException(404, "No such track.")
    with con:
        try:
            r = con.execute("SELECT path, status FROM album_tracks WHERE release_id = ? AND position = ?", (release_id, position)).fetchone()
        except sqlite3.OperationalError:
            r = None
    if not r:
        raise HTTPException(404, "That track hasn't been recorded.")
    root = LIBRARY if r["status"] == "library" else SPOOL_DIR
    path = os.path.realpath(os.path.join(root, r["path"]))
    if not path.startswith(os.path.realpath(root) + os.sep) or not os.path.exists(path):
        raise HTTPException(404, "That recording isn't reachable right now.")
    return ranged(path, request, "audio/flac" if path.lower().endswith(".flac") else "audio/mpeg")


# --- Unmatched plays: Shazam named them, but they didn't link to a record ------------------

def _nm(text: str) -> str:
    t = re.sub(r"\s*[\(\[][^\)\]]*\b(edition|remaster(ed)?|deluxe|anniversary|expanded|version|mono|stereo|explicit|clean)\b[^\)\]]*[\)\]]",
               "", (text or "").lower())
    t = re.sub(r"[^a-z0-9]+", " ", t)
    return re.sub(r"^the ", "", re.sub(r"\s+", " ", t).strip())


def _sim(a: str, b: str) -> float:
    from difflib import SequenceMatcher
    if not a or not b:
        return 0.0
    return 1.0 if a == b else SequenceMatcher(None, a, b).ratio()


@app.get("/api/unmatched")
def unmatched(limit: int = Query(40, ge=1, le=100)):
    """Unmatched plays grouped by artist and album, each with the closest records in the collection."""
    con = connect()
    if con is None:
        return {"groups": [], "total_plays": 0}
    with con:
        rows = con.execute("SELECT * FROM plays WHERE release_id IS NULL ORDER BY started_at DESC LIMIT 3000").fetchall()
        rels = con.execute(f"SELECT release_id, title, artist, {year_sql(con)} AS year, cover_file FROM releases WHERE in_collection = 1").fetchall() \
            if has_catalog(con) else []
        tracks = {}
        for r in con.execute("SELECT release_id, title FROM tracks") if rels else []:
            tracks.setdefault(r[0], []).append(_nm(r[1]))
    groups = {}
    for r in rows:
        d = play_dict(r)
        key = (_nm(d["artist"]), _nm(d["album"]))
        g = groups.setdefault(key, {"artist": d["artist"], "album": d["album"], "play_ids": [], "titles": [],
                                    "first": d["started_at"], "last": d["started_at"], "seconds": 0, "cover": d["cover"]})
        g["play_ids"].append(d["id"])
        if d["title"] not in g["titles"]:
            g["titles"].append(d["title"])
        g["first"] = min(g["first"], d["started_at"])
        g["seconds"] += d["seconds"]
    out = []
    for (a, al), g in groups.items():
        scored = []
        titles = [_nm(t) for t in g["titles"]]
        for rel in rels:
            asim, alsim = _sim(a, _nm(rel["artist"])), _sim(al, _nm(rel["title"]))
            on = tracks.get(rel["release_id"], [])
            hits = sum(1 for t in titles if any(_sim(t, x) >= 0.85 for x in on))
            score = 0.45 * asim + 0.3 * alsim + 0.25 * (hits / max(1, len(titles)))
            if score >= 0.35:
                scored.append((score, rel, hits))
        scored.sort(key=lambda x: -x[0])
        g["plays"] = len(g["play_ids"])
        g["candidates"] = [{"id": rel["release_id"], "title": rel["title"], "artist": rel["artist"], "year": rel["year"],
                            "cover": f"/covers/{rel['cover_file']}" if rel["cover_file"] else None,
                            "score": round(sc, 2), "tracks_found": hits} for sc, rel, hits in scored[:3]]
        out.append(g)
    out.sort(key=lambda g: (-g["plays"], -g["last"]))
    return {"groups": out[:limit], "total_plays": len(rows), "total_groups": len(out)}


# --- Audio telemetry: level, bands, tempo and beats, from turntable-telemetry -----------------

TELEMETRY_PATH = CFG["paths"]["telemetry"]
QUIET = {"live": False, "music": False, "level": 0.0, "bass": 0.0, "mid": 0.0, "treble": 0.0,
         "bpm": None, "confidence": 0.0, "period": None, "last_beat": None}


def read_telemetry() -> dict:
    try:
        with open(TELEMETRY_PATH) as f:
            d = json.load(f)
    except (OSError, ValueError):
        return dict(QUIET)
    d["live"] = time.time() - d.get("t", 0) < 3          # stale means the service isn't running
    return d if d["live"] else dict(QUIET)


@app.get("/api/telemetry")
def telemetry():
    """The latest reading (the round screen polls this)."""
    return JSONResponse(read_telemetry(), headers={"Cache-Control": "no-store"})


@app.get("/api/telemetry/live")
async def telemetry_live(request: Request):
    """Readings as they happen, about ten a second, as server-sent events."""
    async def events():
        last, quiet_since = None, time.time()
        while not await request.is_disconnected():
            d = read_telemetry()
            key = d.get("t")
            if key != last:
                last = key
                yield f"data: {json.dumps(d)}\n\n"
            if time.time() - quiet_since > 15:
                quiet_since = time.time()
                yield ": still here\n\n"          # keeps proxies from closing an idle stream
            await asyncio.sleep(0.1)
    return StreamingResponse(events(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-store", "X-Accel-Buffering": "no"})


GAP_MIN = 90        # unidentified audio shorter than this isn't worth filling


def find_gaps(con, start: int, end: int) -> list:
    """Stretches inside or just around a session where the check log heard music but no play
    covers it: candidates for filling in by hand."""
    try:
        checks = con.execute("SELECT at, kind FROM checks WHERE at BETWEEN ? AND ? ORDER BY at",
                             (start - SESSION_GAP, end + SESSION_GAP)).fetchall()
    except sqlite3.OperationalError:
        return []                      # no check log yet (before v1.4.0)
    runs, cur = [], None
    for at, kind in checks:
        if kind == "silence":
            cur = None
            continue
        if cur and at - cur[1] <= 50:
            cur[1] = at + 8
        else:
            cur = [at, at + 8]
            runs.append(cur)
    covered = con.execute("SELECT started_at, COALESCE(ended_at, last_seen_at) FROM plays "
                          "WHERE COALESCE(ended_at, last_seen_at) >= ? AND started_at <= ? ORDER BY started_at",
                          (start - SESSION_GAP, end + SESSION_GAP)).fetchall()
    gaps = []
    for a, b in runs:
        pos = a
        for ps, pe in covered:
            if pe <= pos or ps >= b:
                continue
            if ps - pos >= GAP_MIN:
                gaps.append({"start": pos, "end": ps})
            pos = max(pos, pe + 20)
        if b - pos >= GAP_MIN:
            gaps.append({"start": pos, "end": b})
    return gaps


@app.get("/api/stats")
def stats(days: int = Query(30, ge=0, le=3650)):
    """days=0 means all time."""
    con = connect()
    empty = {"days": days, "plays": 0, "spins": 0, "seconds": 0, "artists": 0, "albums": 0,
             "top_artists": [], "top_albums": [], "genres": [],
             "hours_of_day": [0] * 24, "weeks": []}
    if con is None:
        return empty
    now = time.time()
    since = 0 if days == 0 else int(now - days * 86400)
    dur = "MAX(0, COALESCE(ended_at, last_seen_at) - started_at)"
    with con:
        totals = con.execute(
            f"SELECT COUNT(*) AS plays, COALESCE(SUM({dur}), 0) AS seconds, "
            "COUNT(DISTINCT NULLIF(artist, '')) AS artists, "
            "COUNT(DISTINCT NULLIF(artist || '|' || album, artist || '|')) AS albums "
            "FROM plays WHERE started_at >= ?", (since,),
        ).fetchone()
        genres = con.execute(
            "SELECT genre AS name, COUNT(*) AS plays FROM plays "
            "WHERE started_at >= ? AND genre != '' GROUP BY genre ORDER BY plays DESC LIMIT 8",
            (since,),
        ).fetchall()
        starts = con.execute(
            f"SELECT started_at, {dur} AS seconds FROM plays WHERE started_at >= ?", (since,),
        ).fetchall()
        spins = load_spins(con, since)
        rel_covers = {}
        if has_catalog(con):
            rel_covers = {r[0]: r[1] for r in con.execute(
                "SELECT release_id, cover_file FROM releases WHERE cover_file != ''")}

    artists, albums = {}, {}
    for sp in spins:
        if sp["artist"]:
            a = artists.setdefault(sp["artist"], {"name": sp["artist"], "spins": 0, "plays": 0, "seconds": 0})
            a["spins"] += 1; a["plays"] += sp["tracks"]; a["seconds"] += sp["seconds"]
        if sp["album"] or sp["release_id"]:
            b = albums.setdefault(sp["key"], {"artist": sp["artist"], "album": sp["album"], "spins": 0,
                                              "plays": 0, "seconds": 0, "cover": None})
            b["spins"] += 1; b["plays"] += sp["tracks"]; b["seconds"] += sp["seconds"]
            if not b["cover"]:
                if sp["release_id"] and rel_covers.get(sp["release_id"]):
                    b["cover"] = f"/covers/{rel_covers[sp['release_id']]}"
                elif sp["cover_file"]:
                    b["cover"] = f"/covers/{sp['cover_file']}"
                elif sp["cover_url"]:
                    b["cover"] = sp["cover_url"]
    top_artists = sorted(artists.values(), key=lambda a: (-a["spins"], -a["seconds"]))[:8]
    top_albums = sorted(albums.values(), key=lambda a: (-a["spins"], -a["seconds"]))[:6]

    hours = [0] * 24
    for r in starts:
        hours[time.localtime(r["started_at"]).tm_hour] += r["seconds"]

    # Listening hours for each of the last 12 weeks (Monday-based, local time).
    today = local_midnight(now)
    week_start = today - time.localtime(today).tm_wday * 86400
    weeks = []
    for i in range(11, -1, -1):
        ws = week_start - i * 7 * 86400
        we = ws + 7 * 86400
        weeks.append({"start": ws, "seconds": sum(r["seconds"] for r in starts if ws <= r["started_at"] < we)})

    return {
        "days": days,
        "plays": totals["plays"],
        "spins": len(spins),
        "seconds": totals["seconds"],
        "artists": totals["artists"],
        "albums": totals["albums"],
        "top_artists": top_artists,
        "top_albums": top_albums,
        "genres": [dict(r) for r in genres],
        "hours_of_day": hours,
        "weeks": weeks if days == 0 or days >= 60 else weeks[-max(1, (days + 6) // 7):],
    }


@app.get("/api/grid")
def grid(days: int = Query(30, ge=1, le=90), hours: int = Query(3, ge=1, le=6)):
    """Listening seconds per block of `hours` (1 or 3) for each of the last `days` days, oldest first,
    plus a few stats for the radar."""
    if 24 % hours:
        hours = 3
    n = 24 // hours
    span = hours * 3600
    now = time.time()
    n = 24 // hours
    today = local_midnight(now)
    starts = [local_midnight(today - i * 86400 + 43200) for i in range(days - 1, -1, -1)]
    out = [{"start": d, "blocks": [0] * n} for d in starts]
    con = connect()
    if con is None:
        return {"days": out, "block_hours": hours, "summary": None}
    window_start = starts[0]
    with con:
        rows = con.execute(
            "SELECT started_at, COALESCE(ended_at, last_seen_at) AS end_at FROM plays "
            "WHERE COALESCE(ended_at, last_seen_at) >= ? ORDER BY started_at",
            (window_start,),
        ).fetchall()
    for r in rows:
        a, b = max(r["started_at"], window_start), r["end_at"]
        for i, d in enumerate(starts):
            nxt = starts[i + 1] if i + 1 < len(starts) else d + 86400
            if b <= d or a >= nxt:
                continue
            for k in range(n):
                bs, be = d + k * hours * 3600, min(d + (k + 1) * hours * 3600, nxt)
                overlap = min(b, be) - max(a, bs)
                if overlap > 0:
                    out[i]["blocks"][k] += overlap

    # the radar's stats: total, days with a spin, longest day, prime time, busiest weekday
    per_day = [sum(d["blocks"]) for d in out]
    by_hour = [0.0] * 24
    for d in out:
        for k, v in enumerate(d["blocks"]):
            for h in range(hours):
                by_hour[k * hours + h] += v / hours
    prime = max(range(24), key=lambda h: by_hour[h] + by_hour[(h + 1) % 24] + by_hour[(h + 2) % 24])
    by_wd = [0] * 7
    for d, v in zip(out, per_day):
        by_wd[time.localtime(d["start"]).tm_wday] += v
    names = ["Mondays", "Tuesdays", "Wednesdays", "Thursdays", "Fridays", "Saturdays", "Sundays"]
    total = sum(per_day)
    summ = {
        "seconds": total,
        "days_with": sum(1 for v in per_day if v >= 60),
        "longest": max(per_day) if per_day else 0,
        "prime_start": prime if total else None,
        "busiest": names[max(range(7), key=lambda i: by_wd[i])] if total else None,
    }
    return {"days": out, "block_hours": hours, "summary": summ}


# --- Collection ----------------------------------------------------------------

# --- Albums and their pressings -------------------------------------------------------------------------
# Several pressings of one album (the same Discogs master, or the same artist and title once edition
# notes are set aside) show as one record. Its main pressing is the most spun, else the first added.
_groups = {"key": None, "val": None}


def group_pressings() -> bool:
    return bool(CFG.get("collection", {}).get("group_pressings", True))


def _norm(s: str) -> str:
    s = re.sub(r"\s*[\(\[][^)\]]*[\)\]]", "", s or "")        # "(Remastered)", "[Deluxe]", Discogs' "(2)"
    return re.sub(r"[^a-z0-9]+", " ", s.lower()).strip()


def album_groups(con) -> dict:
    """release_id -> {"primary": the album's anchor (its first-added pressing), "members": [...]}.
    Shared with identification (albums.py), so plays and their display always agree."""
    import albums
    return albums.groups(con, group_pressings())


SORTS = {
    "added":  "r.date_added DESC",
    "artist": "r.artist COLLATE NOCASE, {Y}, r.title COLLATE NOCASE",      # {Y}: the original year (year_sql)
    "title":  "r.title COLLATE NOCASE",
    "year":   "{Y} IS NULL, {Y}, r.artist COLLATE NOCASE",
    "plays":  "plays DESC, last_played DESC",
    "recent": "last_played IS NULL, last_played DESC",
    "dusty":  "last_played IS NOT NULL, last_played, r.date_added",
}


@app.get("/api/collection")
def collection(q: str = Query("", max_length=80), sort: str = "added", unplayed: bool = False,
               limit: int = Query(48, ge=1, le=200), offset: int = Query(0, ge=0)):
    con = connect()
    empty = {"items": [], "total": 0, "summary": {"records": 0, "played": 0, "never_played": 0, "last_sync": None}}
    if con is None:
        return empty
    with con:
        if not has_catalog(con):
            return empty
        where, args = ["r.in_collection = 1"], []
        if q.strip():
            like = f"%{q.strip()}%"
            where.append("(r.title LIKE ? OR r.artist LIKE ? OR r.styles LIKE ? OR r.label LIKE ?)")
            args += [like] * 4
        having = "HAVING plays = 0" if unplayed else ""
        base = (
            "SELECT r.*, COUNT(p.id) AS plays, MAX(p.started_at) AS last_played "
            "FROM releases r LEFT JOIN plays p ON p.release_id = r.release_id "
            f"WHERE {' AND '.join(where)} GROUP BY r.release_id {having}"
        )
        spin_counts = spins_by_release(con)
        if group_pressings():
            return collection_grouped(con, base, args, sort, unplayed, limit, offset, spin_counts)
        total = con.execute(f"SELECT COUNT(*) FROM ({base})", args).fetchone()[0]
        if sort == "plays":
            # Most played means most spins, which SQL can't count, so sort this one here.
            rows = con.execute(f"{base} ORDER BY {SORTS['recent']}", args).fetchall()
            rows = sorted(rows, key=lambda r: -spin_counts.get(r["release_id"], 0))[offset:offset + limit]
        else:
            order = SORTS.get(sort, SORTS["added"]).replace("{Y}", year_sql(con, "r"))
            rows = con.execute(f"{base} ORDER BY {order} LIMIT ? OFFSET ?",
                               args + [limit, offset]).fetchall()
        summary = con.execute(
            "SELECT COUNT(*) AS records, "
            "SUM(EXISTS (SELECT 1 FROM plays p WHERE p.release_id = r.release_id)) AS played "
            "FROM releases r WHERE r.in_collection = 1").fetchone()
        last_sync = con.execute("SELECT value FROM meta WHERE key = 'last_sync'").fetchone()
    records, played = summary["records"] or 0, summary["played"] or 0
    return {
        "items": [release_dict(r, r["plays"], r["last_played"], spin_counts.get(r["release_id"], 0)) for r in rows],
        "total": total,
        "summary": {"records": records, "played": played, "never_played": records - played,
                    "last_sync": int(last_sync[0]) if last_sync else None},
    }


def collection_grouped(con, base, args, sort, unplayed, limit, offset, spin_counts):
    """Cargo with one sleeve per album: any pressing that matches the search brings in its album,
    whose spins, plays and last spin combine every pressing."""
    groups = album_groups(con)
    every = {r["release_id"]: r for r in con.execute(
        "SELECT r.*, COUNT(p.id) AS plays, MAX(p.started_at) AS last_played FROM releases r "
        "LEFT JOIN plays p ON p.release_id = r.release_id WHERE r.in_collection = 1 GROUP BY r.release_id")}
    matched = {r["release_id"] for r in con.execute(base.replace("HAVING plays = 0", ""), args)}
    albums = {}
    for rid in matched:
        g = groups.get(rid, {"primary": rid, "members": [rid]})
        albums.setdefault(g["primary"], g["members"])
    def agg(members):
        rows = [every[m] for m in members if m in every]
        return (sum(r["plays"] for r in rows), max([r["last_played"] for r in rows if r["last_played"]] or [None]),
                sum(spin_counts.get(m, 0) for m in members), max(r["date_added"] or "" for r in rows), len(rows))
    items = []
    for prim, members in albums.items():
        if prim not in every:
            continue
        plays, last, spins, added, n = agg(members)
        if unplayed and plays:
            continue
        items.append((every[prim], plays, last, spins, added, n, members))
    yr = lambda r: r["original_year"] or r["year"] or None
    keys = {
        "added": lambda x: tuple(-ord(c) for c in x[4]),
        "artist": lambda x: ((x[0]["artist"] or "").lower(), yr(x[0]) or 9999, (x[0]["title"] or "").lower()),
        "title": lambda x: (x[0]["title"] or "").lower(),
        "year": lambda x: (yr(x[0]) is None, yr(x[0]) or 0, (x[0]["artist"] or "").lower()),
        "plays": lambda x: (-x[3], -(x[2] or 0)),
        "recent": lambda x: (x[2] is None, -(x[2] or 0)),
        "dusty": lambda x: (x[2] is not None, x[2] or 0, x[0]["date_added"] or ""),
    }
    items.sort(key=keys.get(sort, keys["added"]))
    out = []
    for r, plays, last, spins, added, n, members in items[offset:offset + limit]:
        d = release_dict(r, plays, last, spins)
        d["pressings"] = n
        out.append(d)
    all_albums = {}
    for rid in every:
        g = groups.get(rid, {"primary": rid, "members": [rid]})
        all_albums.setdefault(g["primary"], g["members"])
    records = len(all_albums)
    played = sum(1 for ms in all_albums.values() if any(every[m]["plays"] for m in ms if m in every))
    last_sync = con.execute("SELECT value FROM meta WHERE key = 'last_sync'").fetchone()
    return {"items": out, "total": len(items),
            "summary": {"records": records, "played": played, "never_played": records - played,
                        "last_sync": int(last_sync[0]) if last_sync else None}}


@app.get("/api/release/{release_id}")
def release(release_id: int):
    con = connect()
    if con is None:
        raise HTTPException(404, "That record isn't in the collection.")
    with con:
        if not has_catalog(con):
            raise HTTPException(404, "That record isn't in the collection.")
        r = con.execute("SELECT * FROM releases WHERE release_id = ?", (release_id,)).fetchone()
        if r is None:
            raise HTTPException(404, "That record isn't in the collection.")
        # the album: this pressing and any others of the same album, whose history combines
        g = album_groups(con).get(release_id, {"members": [release_id]})
        ids = list(dict.fromkeys([release_id] + list(g["members"])))
        marks = ",".join("?" * len(ids))
        tracks = con.execute("SELECT * FROM tracks WHERE release_id = ? ORDER BY seq", (release_id,)).fetchall()
        # plays per track, matched across pressings by title (positions can differ between pressings)
        titles = {(t["release_id"], t["position"]): _norm(t["title"]) for t in
                  con.execute(f"SELECT release_id, position, title FROM tracks WHERE release_id IN ({marks})", ids)}
        by_title, by_pos = {}, {}
        for rid, pos, n, last in con.execute(f"SELECT release_id, track_position, COUNT(*), MAX(started_at) FROM plays "
                                             f"WHERE release_id IN ({marks}) AND track_position != '' GROUP BY release_id, track_position", ids):
            key = titles.get((rid, pos))
            for book, k in ((by_title, key), (by_pos, (rid, pos))):
                if k:
                    n0, l0 = book.get(k, (0, None))
                    book[k] = (n0 + n, max(l0 or 0, last or 0) or None)
        stats = con.execute(f"SELECT COUNT(*), MAX(started_at), MIN(started_at) FROM plays WHERE release_id IN ({marks})", ids).fetchone()
        recent = con.execute(f"SELECT * FROM plays WHERE release_id IN ({marks}) ORDER BY started_at DESC LIMIT 8", ids).fetchall()
        spin_counts = spins_by_release(con)
        spin_count = sum(spin_counts.get(i, 0) for i in ids)
        others = [con.execute("SELECT * FROM releases WHERE release_id = ?", (i,)).fetchone() for i in ids]
    out = release_dict(r, stats[0], stats[1], spin_count)
    # recordings: this pressing's, and the same song from another pressing where this one has none
    recorded = recorded_tracks(con, release_id)
    if len(ids) > 1:
        theirs = {}
        for i in ids[1:]:
            for pos, rec in recorded_tracks(con, i).items():
                theirs.setdefault(titles.get((i, pos)), rec)
        for t in tracks:
            if t["position"] not in recorded and theirs.get(_norm(t["title"])):
                recorded[t["position"]] = theirs[_norm(t["title"])]
    out["recorded"] = recorded
    out["first_played"] = stats[2]
    out["in_collection"] = bool(r["in_collection"])
    def track_count(t):
        if len(ids) > 1:
            return by_title.get(_norm(t["title"]), by_pos.get((release_id, t["position"]), (0, None)))
        return by_pos.get((release_id, t["position"]), (0, None))
    out["tracks"] = [{"position": t["position"], "side": t["side"], "title": t["title"], "duration": t["duration"],
                      "duration_source": col(t, "duration_source", "") or "",
                      "plays": track_count(t)[0], "last_played": track_count(t)[1]} for t in tracks]
    out["recent"] = [play_dict(p) for p in recent]
    # the pressings of this album that you own, each with its own spins
    owned = [o for o in others if o is not None and o["in_collection"]]
    out["pressings"] = [{"id": o["release_id"], "label": o["label"], "catno": o["catno"], "year": o["year"], "format": o["format"],
                         "cover": release_cover(o), "current": o["release_id"] == release_id}
                        for o in sorted(owned, key=lambda o: (o["year"] or 9999, o["release_id"]))] if len(owned) > 1 else []
    out.update(record_story(con, r, release_id, ids))
    return out


def record_story(con, r, release_id: int, ids=None) -> dict:
    """Your history with a record (time listened, spins per week, records it's often spun with),
    Beepnik's verdict on it, and Ground Control's dossier."""
    now = time.time()
    spins = load_spins(con)
    ids = set(ids or [release_id])
    mine = [sp for sp in spins if sp["release_id"] in ids]
    weeks = [0] * 12
    for sp in mine:
        k = int((now - sp["start"]) // (7 * 86400))
        if 0 <= k < 12:
            weeks[11 - k] += 1
    # sittings: spins less than a session gap apart; count the other records in the ones with this one
    together, cur, cur_end = {}, set(), None
    groups = album_groups(con)
    prim = lambda rid: groups.get(rid, {"primary": rid})["primary"] if rid else None
    def close(sitting):
        if sitting & ids:
            for rid in {prim(x) for x in sitting - ids - {None}} - {prim(release_id)}:
                together[rid] = together.get(rid, 0) + 1
    for sp in spins:
        if cur_end is not None and sp["start"] - cur_end > SESSION_GAP:
            close(cur)
            cur = set()
        cur.add(sp["release_id"])
        cur_end = max(cur_end or 0, sp["end"])
    close(cur)
    often = []
    for rid, n in sorted(together.items(), key=lambda kv: -kv[1])[:4]:
        o = con.execute("SELECT * FROM releases WHERE release_id = ?", (rid,)).fetchone()
        if o:
            often.append({"id": rid, "title": o["title"], "artist": o["artist"], "cover": release_cover(o), "times": n})

    # Beepnik's verdict, from his real taste
    verdict = None
    try:
        row = con.execute("SELECT state FROM pet_state WHERE id = 1").fetchone()
        st = json.loads(row["state"]) if row else None
    except sqlite3.OperationalError:
        st = None
    if st and st.get("hatched") and not st.get("lost"):
        mine_tags = {x.lower() for x in json.loads(r["styles"] or "[]") + json.loads(r["genres"] or "[]")}
        favs = [f for f in st.get("favorites") or [] if f.lower() in mine_tags]
        dislikes = [d for d in ((st.get("taste_detail") or {}).get("dislikes") or []) if d.lower() in mine_tags]
        craving = st.get("craving") or {}
        if craving.get("release_id") == release_id:
            verdict = {"tone": "gold", "title": "He's craving this", "why": "Play it and he'll savor every minute"}
        elif dislikes:
            verdict = {"tone": "muted", "title": "Not his thing", "why": f"{dislikes[0]} isn't for him"}
        elif favs:
            verdict = {"tone": "lilac", "title": "One of his favourites", "why": f"{favs[0]} feeds his taste"}
        elif not mine:
            verdict = {"tone": "pink", "title": "He's curious", "why": "Never played here: it'd feed his curiosity"}
        else:
            verdict = {"tone": "sky", "title": "Fine by him", "why": "Good for variety, if nothing else"}

    # Ground Control's dossier, from the details it cached
    dossier = None
    try:
        d = con.execute("SELECT data FROM release_details WHERE release_id = ?", (release_id,)).fetchone()
        if d:
            dd = json.loads(d["data"])
            dossier = {k: dd.get(k) for k in ("country", "released", "notes", "companies", "credits", "matrix")}
    except sqlite3.OperationalError:
        pass
    return {"listened": int(sum(sp["seconds"] for sp in mine)), "weeks": weeks, "often_with": often,
            "verdict": verdict, "dossier": dossier}


@app.get("/api/dusty")
def dusty(limit: int = Query(5, ge=1, le=12)):
    """A few records worth pulling out: never played first, then the longest since a spin.
    The picks rotate daily so the same three don't sit there forever."""
    con = connect()
    if con is None:
        return {"items": []}
    with con:
        if not has_catalog(con):
            return {"items": []}
        rows = con.execute(
            "SELECT r.*, COUNT(p.id) AS plays, MAX(p.started_at) AS last_played "
            "FROM releases r LEFT JOIN plays p ON p.release_id = r.release_id "
            "WHERE r.in_collection = 1 GROUP BY r.release_id "
            "HAVING last_played IS NULL OR last_played < ? "
            f"ORDER BY {SORTS['dusty']} LIMIT 24",
            (int(time.time()) - 45 * 86400,),
        ).fetchall()
        spin_counts = spins_by_release(con)
        if group_pressings():
            groups = album_groups(con)
            last_by = {r[0]: r[1] for r in con.execute("SELECT release_id, MAX(started_at) FROM plays WHERE release_id IS NOT NULL GROUP BY release_id")}
            seen, keep = set(), []
            for r in rows:
                g = groups.get(r["release_id"], {"primary": r["release_id"], "members": [r["release_id"]]})
                if g["primary"] in seen:
                    continue
                last = max([last_by[m] for m in g["members"] if m in last_by] or [0])
                if last and last >= int(time.time()) - 45 * 86400:
                    continue                                   # another pressing was played lately
                seen.add(g["primary"])
                keep.append(con.execute("SELECT r.*, (SELECT COUNT(*) FROM plays p WHERE p.release_id = r.release_id) AS plays, "
                                        "? AS last_played FROM releases r WHERE r.release_id = ?", (last or None, g["primary"])).fetchone())
            rows = keep
    pool = list(rows)
    random.Random(datetime.date.today().toordinal()).shuffle(pool)
    pool.sort(key=lambda r: r["last_played"] is not None)   # never-played stay first, shuffled among themselves
    items = []
    for r in pool[:limit]:
        d = release_dict(r, r["plays"], r["last_played"], spin_counts.get(r["release_id"], 0))
        d["story"] = dust_story(con, r)
        items.append(d)
    return {"items": items}


def dust_story(con, r) -> str:
    """Why this record deserves a spin, in one line."""
    if not r["last_played"]:
        added = (r["date_added"] or "")[:7]
        when = ""
        if added:
            try:
                when = " since " + time.strftime("%B %Y", time.strptime(added, "%Y-%m"))
            except ValueError:
                pass
        return f"In the cargo hold{when} and never played here."
    rows = con.execute("SELECT started_at, COALESCE(ended_at, last_seen_at), track_position FROM plays "
                       "WHERE release_id = ? ORDER BY started_at DESC", (r["release_id"],)).fetchall()
    positions, prev = set(), None
    for st_, en, pos in rows:          # the last spin only
        if prev is not None and prev - en > SESSION_GAP:
            break
        positions.add(pos)
        prev = st_
    how = how_much(con, r["release_id"], positions)
    tail = f", and only {how}." if how and how != "the whole record" else "."
    return f"Last spun {ago_words(time.time() - r['last_played'])}{tail}"


# --- Companion ------------------------------------------------------------------

# --- Beepnik's story: need history, journey, achievements, and talking to him ----------------------------
_story = {"cfg": None, "cfg_at": 0.0, "talk_at": 0.0}
TALK_GAP = 20          # seconds between conversations, so nobody drains the model's daily budget


def story_cfg():
    import pet_engine
    if time.time() - _story["cfg_at"] > 60:
        _story["cfg"] = pet_engine.load_config(turntable_config.CONFIG_FILE)
        _story["cfg_at"] = time.time()
    return _story["cfg"]


def pet_state(con):
    row = con.execute("SELECT state FROM pet_state WHERE id = 1").fetchone()
    return json.loads(row["state"]) if row else None


@app.get("/api/beepnik")
def beepnik_story_api():
    """Beepnik's need history (a week of daily points), his journey and his achievements."""
    import beepnik_story as bs
    con = connect()
    if con is None:
        return {"enabled": False}
    with con:
        try:
            state = pet_state(con)
        except sqlite3.OperationalError:
            state = None
        if not state:
            return {"enabled": False}
        cfg = story_cfg()
        ach = bs.progress(con, cfg, state)
        return JSONResponse({"enabled": True, "history": bs.history(con, cfg), "journey": bs.journey(con, state.get("born", 0)),
                             "achievements": ach, "earned": sum(1 for a in ach if a["done"]), "total": len(ach),
                             "traits": state.get("traits") or []}, headers={"Cache-Control": "no-store"})


def _talk(fn):
    import beepnik_story as bs
    if time.time() - _story["talk_at"] < TALK_GAP:
        raise HTTPException(429, "Beepnik is still thinking about the last one. Try again in a few seconds.")
    if not os.path.exists(DB_PATH):
        raise HTTPException(503, "Beepnik isn't running yet.")
    _story["talk_at"] = time.time()
    con = sqlite3.connect(DB_PATH, timeout=10)
    con.row_factory = sqlite3.Row
    try:
        state = pet_state(con)
        if not state or state.get("lost"):
            raise HTTPException(409, "Beepnik isn't answering right now.")
        cfg = dict(story_cfg())
        cfg["pet"] = {**cfg["pet"], "name": state.get("name") or cfg["pet"]["name"]}
        return getattr(bs, fn)(con, cfg, state)
    finally:
        con.close()


@app.post("/api/beepnik/ask")
def beepnik_ask():
    """He picks a record from your cargo that would help his lowest need, and says why."""
    return JSONResponse(_talk("ask_for_record"), headers={"Cache-Control": "no-store"})


@app.post("/api/beepnik/howareyou")
def beepnik_how():
    """He says how he's doing, from the facts of his state."""
    return JSONResponse(_talk("how_are_you"), headers={"Cache-Control": "no-store"})


@app.get("/api/pet")
def pet():
    """The companion's latest state and lines, written by the pet service."""
    con = connect()
    if con is None:
        return {"enabled": False}
    with con:
        has = {r[0] for r in con.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table' AND name IN ('pet_state', 'pet_log')")}
        if "pet_state" not in has:
            return {"enabled": False}
        row = con.execute("SELECT updated_at, state FROM pet_state WHERE id = 1").fetchone()
        if row is None:
            return {"enabled": False}
        state = json.loads(row["state"])
        # Only this probe's lines: a new probe starts with a clean transmission log.
        lines = con.execute("SELECT at, kind, text FROM pet_log WHERE at >= ? ORDER BY at DESC, id DESC LIMIT 40",
                            (state.get("born", 0),)).fetchall() if "pet_log" in has else []
        # the terminal log's other voices: Ground Control and system lines
        try:
            tx = con.execute("SELECT at, voice, text FROM transmissions WHERE at >= ? ORDER BY at DESC, id DESC LIMIT 80",
                             (state.get("born", 0) - 86400 * 7,)).fetchall()
        except sqlite3.OperationalError:
            tx = []
        try:
            state["memorials"] = con.execute("SELECT COUNT(*) FROM pet_memorials").fetchone()[0]
        except sqlite3.OperationalError:
            state["memorials"] = 0
    state["enabled"] = True
    state["stale"] = time.time() - row["updated_at"] > 180   # the service has stopped updating
    state["lines"] = [{"at": r["at"], "kind": r["kind"], "text": r["text"]} for r in lines]
    state["transmissions"] = [{"at": r["at"], "voice": r["voice"], "text": r["text"]} for r in tx]
    return state


@app.get("/api/memorials")
def memorials():
    """Point Nemo: every probe lost in space, newest first."""
    con = connect()
    if con is None:
        return {"memorials": []}
    with con:
        try:
            rows = con.execute("SELECT * FROM pet_memorials ORDER BY died DESC").fetchall()
        except sqlite3.OperationalError:
            return {"memorials": []}
    out = []
    for r in rows:
        d = dict(r)
        d["traits"] = json.loads(d["traits"] or "[]")
        out.append(d)
    return {"memorials": out}


# --- Cosmic logbook -------------------------------------------------------------

# Discogs genre -> constellation. Mirrors the pet's trait rules; first match wins.
# The constellations: shared with the pet (sectors.py, kept identical in turntable-id/).
from sectors import sector_of


@app.get("/api/chart")
def chart():
    """Every record as a star: constellation, spins, first spin and plays per track."""
    con = connect()
    empty = {"records": [], "charted": 0, "total": 0}
    if con is None:
        return empty
    with con:
        if not has_catalog(con):
            return empty
        rels = con.execute(f"SELECT release_id, title, artist, {year_sql(con)} AS year, genres, styles, cover_file FROM releases "
                           "WHERE in_collection = 1").fetchall()
        spins = {}
        for sp in load_spins(con):
            if sp["release_id"]:
                e = spins.setdefault(sp["release_id"], {"n": 0, "first": sp["start"], "last": sp["start"]})
                e["n"] += 1
                e["first"] = min(e["first"], sp["start"])
                e["last"] = max(e["last"], sp["start"])
        heard, first_heard = {}, {}
        for r in con.execute("SELECT release_id, track_position, COUNT(*), MIN(started_at) FROM plays "
                             "WHERE release_id IS NOT NULL AND track_position != '' GROUP BY release_id, track_position"):
            heard[(r[0], r[1])] = r[2]
            first_heard[(r[0], r[1])] = r[3]
        positions = {}
        for r in con.execute("SELECT release_id, position FROM tracks ORDER BY release_id, seq"):
            positions.setdefault(r[0], []).append(r[1])
    if group_pressings():
        # one star per album: fold every pressing's spins into its main pressing
        groups = album_groups(con)
        prim = lambda rid: groups.get(rid, {"primary": rid})["primary"]
        merged = {}
        for rid, e in spins.items():
            m = merged.setdefault(prim(rid), {"n": 0, "first": e["first"], "last": e["last"]})
            m["n"] += e["n"]; m["first"] = min(m["first"], e["first"]); m["last"] = max(m["last"], e["last"])
        spins = merged
        rels = [r for r in rels if prim(r["release_id"]) == r["release_id"]]
    records = []
    fresh_since = time.time() - 24 * 3600          # charted in the last day: highlighted on the chart
    today = {"systems": 0, "planets": 0}
    for r in rels:
        sp = spins.get(r["release_id"])
        poss = positions.get(r["release_id"], [])
        fresh_tracks = [i for i, pos in enumerate(poss) if (first_heard.get((r["release_id"], pos)) or 0) >= fresh_since]
        fresh = bool(sp and sp["first"] >= fresh_since)
        today["systems"] += fresh
        today["planets"] += len(fresh_tracks)
        genres = json.loads(r["genres"] or "[]")
        records.append({
            "id": r["release_id"], "title": r["title"], "artist": r["artist"], "year": r["year"],
            "sector": sector_of(genres, json.loads(r["styles"] or "[]")), "genre": genres[0] if genres else "",
            "spins": sp["n"] if sp else 0,
            "first_spin": sp["first"] if sp else None, "last_spin": sp["last"] if sp else None,
            "cover": f"/covers/{r['cover_file']}" if r["cover_file"] else None,
            "tracks": [heard.get((r["release_id"], pos), 0) for pos in poss],
            "fresh": fresh, "fresh_tracks": fresh_tracks,
        })
    charted = sum(1 for x in records if x["spins"])
    return {"records": records, "charted": charted, "total": len(records), "charted_today": today}


# --- Stream -----------------------------------------------------------------

@app.get("/stream")
async def stream(request: Request):
    """Relay the local Icecast mount, so the outside world only ever reaches this app."""
    client = httpx.AsyncClient(timeout=httpx.Timeout(10.0, read=None))
    try:
        # Pass the ICY request through so players like VLC still get track titles.
        headers = {"Icy-MetaData": "1"} if request.headers.get("icy-metadata") == "1" else {}
        upstream = await client.send(client.build_request("GET", STREAM_URL, headers=headers), stream=True)
    except httpx.HTTPError:
        await client.aclose()
        return JSONResponse({"error": "The turntable stream is offline. Check that the turntable is switched on."},
                            status_code=503)
    if upstream.status_code != 200:
        await upstream.aclose()
        await client.aclose()
        return JSONResponse({"error": "The turntable stream is offline. Check that the turntable is switched on."},
                            status_code=503)

    async def body():
        try:
            async for chunk in upstream.aiter_raw():
                if await request.is_disconnected():
                    break
                yield chunk
        finally:
            await upstream.aclose()
            await client.aclose()

    passthrough = {k: v for k, v in upstream.headers.items()
                   if k.lower() in ("icy-metaint", "icy-name", "icy-br", "icy-genre", "icy-description")}
    return StreamingResponse(
        body(),
        media_type=upstream.headers.get("content-type", "audio/mpeg"),
        headers={"Cache-Control": "no-store", "X-Accel-Buffering": "no", **passthrough},
    )


# --- Pages and files ----------------------------------------------------------

@app.api_route("/", methods=["GET", "HEAD"])
def index():
    return FileResponse(STATIC / "index.html", headers={"Cache-Control": "no-cache"})


@app.get("/static/cast-config.json")
def cast_config():
    """The Cast button's app id, from the config (the page reads it from this address)."""
    if not CFG["cast"]["app_id"]:
        raise HTTPException(404, "Cast isn't set up.")
    return {"app_id": CFG["cast"]["app_id"]}


app.mount("/static", StaticFiles(directory=STATIC), name="static")
app.mount("/covers", StaticFiles(directory=COVER_DIR, check_dir=False), name="covers")
