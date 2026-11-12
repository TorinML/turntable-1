"""Ground Control: mission control radioing up facts about the track that's playing.

One transmission per track, sent the moment a new play appears (heard or probable), never on a
timer. Everything it says comes from data: the collection, Discogs release details (credits,
companies, the run-out matrix), the wantlist, your own play history and live telemetry. The LLM
only phrases the facts; a reply that adds a number or a name the facts didn't contain is thrown
away, and the facts are sent as plain text instead.

Also here: the transmissions table (Ground Control and system lines, read by the terminal log),
and the Discogs details cache, filled lazily here and in bulk by the nightly sync.
"""
import json
import logging
import os
import re
import threading
import time

try:
    import requests
except ImportError:          # the web app's environment on a hand-built install may not have it; it imports
    class _NoRequests:       # this module only for code that never makes a request there
        RequestException = OSError
        def __getattr__(self, name):
            raise RuntimeError("the requests library isn't installed")
    requests = _NoRequests()
import turntable_config
CFG = turntable_config.load()

log = logging.getLogger("turntable-pet.ground")
DAY = 86400
TELEMETRY = CFG["paths"]["telemetry"]
DETAILS_MAX_AGE = 120 * DAY

SCHEMA = """
CREATE TABLE IF NOT EXISTS transmissions (
    id       INTEGER PRIMARY KEY,
    at       INTEGER NOT NULL,
    voice    TEXT NOT NULL,              -- ground or system
    text     TEXT NOT NULL,
    play_id  INTEGER,                    -- the play a Ground Control line is about
    source   TEXT NOT NULL DEFAULT ''    -- llm or facts (Ground Control); event kind (system)
);
CREATE INDEX IF NOT EXISTS transmissions_at ON transmissions(at);
CREATE UNIQUE INDEX IF NOT EXISTS transmissions_play ON transmissions(play_id) WHERE voice = 'ground';
CREATE TABLE IF NOT EXISTS release_details (
    release_id  INTEGER PRIMARY KEY,
    fetched_at  INTEGER NOT NULL,
    data        TEXT NOT NULL            -- the parts of the Discogs release Ground Control uses
);
CREATE TABLE IF NOT EXISTS wantlist (
    release_id  INTEGER PRIMARY KEY,
    master_id   INTEGER,
    title       TEXT NOT NULL,
    artist      TEXT NOT NULL,
    year        INTEGER,
    added       TEXT NOT NULL DEFAULT ''
);
"""


def ensure_schema(db):
    with db:
        db.executescript(SCHEMA)


def add_system(db, text: str, kind: str):
    with db:
        db.execute("INSERT INTO transmissions (at, voice, text, source) VALUES (?, 'system', ?, ?)",
                   (int(time.time()), text, kind))


# --- Discogs release details --------------------------------------------------------------------

KEEP_COMPANIES = ("Mastered At", "Lacquer Cut At", "Pressed By", "Recorded At", "Mixed At", "Manufactured By", "Plated By")
KEEP_ROLES = ("Producer", "Mastered By", "Lacquer Cut By", "Engineer", "Recorded By", "Mixed By", "Arranged By",
              "Written-By", "Composed By", "Featuring", "Vocals", "Performer", "Conductor", "Orchestra")


def slim_release(r: dict) -> dict:
    """Just what Ground Control relays, so the cache stays small."""
    def people(lst):
        out = []
        for a in lst or []:
            role = (a.get("role") or "").strip()
            if any(role.startswith(k) for k in KEEP_ROLES):
                out.append({"role": role, "name": re.sub(r"\s*\(\d+\)$", "", a.get("anv") or a.get("name") or "")})
        return out[:12]
    return {
        "country": r.get("country") or "",
        "released": r.get("released") or "",
        "notes": (r.get("notes") or "").strip()[:600],
        "companies": [{"type": c.get("entity_type_name", ""), "name": re.sub(r"\s*\(\d+\)$", "", c.get("name", ""))}
                      for c in r.get("companies") or [] if c.get("entity_type_name") in KEEP_COMPANIES][:8],
        "credits": people(r.get("extraartists")),
        "matrix": [{"value": i.get("value", ""), "description": i.get("description", "")}
                   for i in r.get("identifiers") or [] if (i.get("type") or "").startswith("Matrix")][:8],
        "tracks": {t.get("position", ""): people(t.get("extraartists")) for t in r.get("tracklist") or [] if t.get("position")},
    }


def details(db, release_id: int, api=None) -> dict | None:
    row = db.execute("SELECT fetched_at, data FROM release_details WHERE release_id = ?", (release_id,)).fetchone()
    if row and time.time() - row[0] < DETAILS_MAX_AGE:
        return json.loads(row[1])
    if api is None:
        return json.loads(row[1]) if row else None
    try:
        data = slim_release(api.get(f"/releases/{release_id}"))
    except BaseException as e:            # the Discogs client exits on a bad token: never let that reach us
        log.warning("Discogs details for %s: %s", release_id, e)
        return json.loads(row[1]) if row else None
    with db:
        db.execute("INSERT OR REPLACE INTO release_details (release_id, fetched_at, data) VALUES (?, ?, ?)",
                   (release_id, int(time.time()), json.dumps(data)))
    return data


def discogs_api():
    token = CFG["secrets"]["discogs"]["token"]
    if not token:
        return None
    try:
        from discogs_sync import Discogs
        return Discogs(token)
    except Exception:
        return None


# --- Facts --------------------------------------------------------------------------------------

def ordinal(n: int) -> str:
    return f"{n}{'th' if 10 <= n % 100 <= 20 else {1: 'st', 2: 'nd', 3: 'rd'}.get(n % 10, 'th')}"


def live_tempo():
    try:
        with open(TELEMETRY) as f:
            t = json.load(f)
        if t.get("music") and t.get("bpm") and time.time() - t.get("t", 0) < 10:
            return round(t["bpm"])
    except Exception:
        pass
    return None


def spin_start(db, play) -> float:
    """When this spin of the record began (tracks of one record with gaps under 30 minutes)."""
    rid, t = play["release_id"], play["started_at"]
    for (s, e) in db.execute("SELECT started_at, COALESCE(ended_at, last_seen_at) FROM plays WHERE release_id = ? "
                             "AND started_at < ? ORDER BY started_at DESC LIMIT 60", (rid, t)):
        if t - (e or s) > 1800:
            break
        t = s
    return t


def facts_for(db, play: dict, api=None) -> list:
    """[(key, fact text)] for this play, track facts first."""
    out = []
    pos, title = play.get("track_position") or "", play.get("title") or ""
    rid = play.get("release_id")
    lead = f"Tracking {pos}, '{title}'" if pos else f"Tracking '{title}'"
    if not rid:
        out.append(("track", f"{lead} by {play.get('artist')}" + (f", from {play['album']}" if play.get("album") else "")))
        out.append(("unowned", "Not a record from the cargo hold"))
        if play.get("released"):
            m = re.match(r"\s*(\d{4})", str(play["released"]))
            if m:
                out.append(("released", f"Released in {m.group(1)}"))
        w = db.execute("SELECT title FROM wantlist WHERE lower(artist) = lower(?) LIMIT 1", (play.get("artist") or "",)).fetchone()
        if w:
            out.append(("want", f"On your wantlist from this artist: {w[0]}"))
        return out
    out.append(("track", lead))
    # the track's own history and credits
    n = db.execute("SELECT COUNT(*) FROM plays WHERE release_id = ? AND track_position = ? AND id <= ?",
                   (rid, pos, play["id"])).fetchone()[0] if pos else 0
    if n == 1:
        out.append(("track_first", "First time this track has played here"))
    elif n > 1:
        out.append(("track_n", f"The {ordinal(n)} play of this track here"))
    d = details(db, rid, api) or {}
    for c in (d.get("tracks") or {}).get(pos, [])[:2]:
        out.append((f"tcredit:{c['role']}", f"{c['role']}: {c['name']}"))
    bpm = live_tempo()
    if bpm:
        out.append(("bpm", f"Running at about {bpm} BPM"))
    # the record (these rotate through a spin)
    cols = {c[1] for c in db.execute("PRAGMA table_info(releases)")}
    extra = ", ".join(c if c in cols else f"NULL AS {c}" for c in ("original_year", "media_condition", "sleeve_condition"))
    r = db.execute(f"SELECT title, artist, year, {extra.split(', ')[0]}, label, catno, {', '.join(extra.split(', ')[1:])} "
                   "FROM releases WHERE release_id = ?", (rid,)).fetchone()
    if r:
        rtitle, artist, year, oyear, label, catno, mcond, scond = r
        side = (pos[:1] or "").upper()
        if oyear and year and oyear != year:
            out.append(("rec:years", f"This pressing is from {year}; the music first came out in {oyear}"))
        elif year:
            out.append(("rec:years", f"Released in {year}"))
        if d.get("country"):
            out.append(("rec:country", f"Pressed for {d['country']}" + (f" on {label}" if label else "")))
        elif label:
            out.append(("rec:label", f"On {label}" + (f", catalog {catno}" if catno else "")))
        for c in d.get("companies") or []:
            out.append((f"rec:co:{c['type']}", f"{c['type']} {c['name']}"))
        for c in d.get("credits") or []:
            out.append((f"rec:cr:{c['role']}", f"{c['role']}: {c['name']}"))
        mx = [m for m in d.get("matrix") or [] if side and m["description"].upper().startswith(("SIDE " + side, side))]
        if mx:
            out.append((f"rec:matrix:{side}", f"Side {side} run-out matrix {mx[0]['value']}"))
        if mcond:
            out.append(("rec:cond", f"Logged condition: media {mcond}" + (f", sleeve {scond}" if scond else "")))
        spins = db.execute("SELECT COUNT(DISTINCT date(started_at, 'unixepoch', 'localtime')) FROM plays WHERE release_id = ?", (rid,)).fetchone()[0]
        first = db.execute("SELECT MIN(started_at) FROM plays WHERE release_id = ?", (rid,)).fetchone()[0]
        if spins <= 1:
            out.append(("rec:first", "The first time this record has been spun here"))
        elif first:
            out.append(("rec:days", f"Spun here on {spins} different days since {time.strftime('%B %Y', time.localtime(first))}"))
        w = db.execute("SELECT title FROM wantlist WHERE lower(artist) = lower(?) AND release_id != ? LIMIT 1",
                       (artist or "", rid)).fetchone()
        if w:
            out.append(("rec:want", f"On your wantlist from this artist: {w[0]}"))
    return out


def choose(db, play: dict, facts: list, per_track: int = 2, per_record: int = 2) -> list:
    """The track's facts, plus a couple of record facts not yet relayed during this spin."""
    track = [f for f in facts if not f[0].startswith("rec:")][: 1 + per_track]
    record = [f for f in facts if f[0].startswith("rec:")]
    if play.get("release_id") and record:
        key = f"{play['release_id']}:{int(spin_start(db, play))}"
        row = db.execute("SELECT value FROM meta WHERE key = 'ground_used'").fetchone()
        used = json.loads(row[0]) if row else {}
        said = set(used.get(key, []))
        fresh = [f for f in record if f[0] not in said][:per_record]
        if fresh:
            used = {key: sorted(said | {f[0] for f in fresh})}          # only the current spin is remembered
            with db:
                db.execute("INSERT OR REPLACE INTO meta (key, value) VALUES ('ground_used', ?)", (json.dumps(used),))
        record = fresh
    return track + record


# --- Phrasing -----------------------------------------------------------------------------------

def as_text(chosen: list) -> str:
    return ". ".join(f[1].rstrip(".") for f in chosen) + "."


def check(text: str, chosen: list) -> bool:
    """Every number and capitalized name in the reply must appear in the facts."""
    facts = " ".join(f[1] for f in chosen)
    for num in re.findall(r"\d[\d,.]*", text):
        if num.strip(".,") not in facts:
            return False
    lower = facts.lower()
    for word in re.findall(r"\b[A-Z][a-zA-Z'\-]{2,}\b", text):
        if word.lower() not in lower and word not in ("Ground", "Control", "Tracking", "Side", "The", "This", "That",
                                                      "First", "Running", "Logged", "Pressed", "Released", "On", "Your",
                                                      "Copy", "Confirmed", "Noted", "Roger", "Probe", "Over", "Now"):
            return False
    return len(text.split()) <= 45


def phrase(cfg: dict, chosen: list):
    """(text, source). The LLM may only rephrase; anything it adds sends us back to the facts."""
    llm, g = cfg.get("llm", {}), cfg.get("ground", {})
    plain = as_text(chosen)
    if not g.get("use_llm", True) or not llm.get("enabled", True) or (llm.get("provider", "ollama") == "ollama" and not llm.get("url")):
        return plain, "facts"
    prompt = ("You are Ground Control, mission control for a small space probe that travels on music. "
              "Relay the facts below as one or two short sentences in terse, calm radio style, present tense. "
              "Use only these facts. Do not add any name, date, number, place or claim that is not listed. "
              "No greeting, no sign-off, no quotation marks.\n\nFacts:\n" + "\n".join(f"- {f[1]}" for f in chosen))
    try:
        import llm as llm_mod
        raw, _ = llm_mod.generate(llm, "", prompt, temperature=0.3, max_tokens=90, timeout=llm.get("timeout_seconds", 30))
        text = (raw or "").strip().strip('"').replace("\n", " ")
        text = re.sub(r"^(ground control|gc)\s*[:,-]\s*", "", text, flags=re.I).strip()
        if text and check(text, chosen):
            return text, "llm"
        log.info("Ground Control reply rejected (added detail): %s", text)
    except Exception as e:
        log.info("Ground Control LLM unavailable: %s", e)
    return plain, "facts"


# --- The listener -------------------------------------------------------------------------------

class GroundControl(threading.Thread):
    """Watches the station and sends one transmission per new play, the moment it appears."""

    def __init__(self, db_path: str, get_cfg, poll: float = 2.0):
        super().__init__(daemon=True, name="ground-control")
        self.db_path, self.get_cfg, self.poll = db_path, get_cfg, poll
        self.api = None

    def run(self):
        from store import connect
        db = connect(self.db_path)
        ensure_schema(db)
        self.api = discogs_api()
        log.info("Ground Control listening")
        while True:
            try:
                self.step(db)
            except Exception as e:
                log.warning("Ground Control: %s", e)
            time.sleep(self.poll)

    def step(self, db):
        cfg = self.get_cfg()
        if not cfg.get("ground", {}).get("enabled", True):
            return
        row = db.execute("SELECT state, play_id FROM station WHERE id = 1").fetchone()
        if not row or row[0] != "playing" or not row[1]:
            return
        pid = row[1]
        if db.execute("SELECT 1 FROM transmissions WHERE voice = 'ground' AND play_id = ?", (pid,)).fetchone():
            return                                    # already sent for this track
        cols = [c[1] for c in db.execute("PRAGMA table_info(plays)")]
        p = db.execute(f"SELECT {', '.join(cols)} FROM plays WHERE id = ?", (pid,)).fetchone()
        if not p:
            return
        play = dict(zip(cols, p))
        if not play.get("title"):
            return
        facts = facts_for(db, play, self.api)
        chosen = choose(db, play, facts, cfg.get("ground", {}).get("track_facts", 2), cfg.get("ground", {}).get("record_facts", 2))
        text, source = phrase(cfg, chosen)
        with db:
            db.execute("INSERT OR IGNORE INTO transmissions (at, voice, text, play_id, source) VALUES (?, 'ground', ?, ?, ?)",
                       (int(time.time()), text, pid, source))
        log.info("Ground Control (%s): %s", source, text)
