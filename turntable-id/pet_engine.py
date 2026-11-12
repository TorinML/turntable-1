"""The companion's needs, mood, growth and cravings, worked out from the play log.

Everything is recomputed from history on each tick, so the pet survives reboots,
config edits apply retroactively, and there's no separate state to drift.
"""
import copy
import datetime
import json
import re
import math
import logging
import random
import time

import curiosity as curiosity_mod
import insights
import sectors
import flight
import lifecycle

try:
    import tomllib
except ModuleNotFoundError:  # Python < 3.11
    import tomli as tomllib

log = logging.getLogger("turntable-pet")

DEFAULTS = {
    "pet": {"name": "Beep", "born": ""},
    "personality": {
        "description": "A tiny, eager chrome satellite who lives on vinyl.",
        "snark": 3, "enthusiasm": 7, "max_words": 40, "emoji": False,
        "family_friendly": True, "catchphrases": [],
    },
    "taste": {"favorites": [], "dislikes": [], "learn": True, "learn_days": 30, "learn_count": 3,
              "learn_dislikes": True, "dislike_min_skips": 3, "dislike_share": 0.35, "dislike_count": 2},
    "needs": {
        "fuel": {"full_after_minutes": 60, "empty_after_hours": 120},
        "variety": {"window_days": 7, "target_styles": 6, "min_minutes": 5, "half_life_days": 3,
                    "style_target": 6, "genre_target": 4, "era_target": 4, "style_weight": 0.6, "fullness_minutes": 50,
                    "repeat_limit": 2, "repeat_penalty": 15},
        "curiosity": {"record_new": 15, "dusty_boost": 12, "dusty_days": 60, "side_new": 5, "track_new": 2,
                      "artist_new": 4, "artist_gap_days": 30, "familiar_spins": 3, "familiar_days": 14,
                      "drain_per_minute": 0.6, "streak_step": 0.5, "streak_max": 2.5, "half_life_days": 1.5},
        "taste": {"window_days": 3, "full_after_minutes": 60, "half_life_days": 2},
    },
    "mood": {
        "weights": {"fuel": 1.0, "variety": 0.8, "curiosity": 0.6, "taste": 0.8},
        "low": 25,
        "bands": [[85, "ecstatic"], [65, "happy"], [45, "content"], [25, "restless"], [0, "glum"]],
    },
    "orbit": {
        "stages": [[0, "pad", "On the launch pad"], [2, "leo", "Low Earth orbit"], [5, "geo", "Geostationary orbit"],
                   [10, "lunar", "Lunar transfer"], [20, "deep", "Deep space"]],
        "grace_days": 7, "decay_hours_per_day": 2, "max_drop": 1, "recovery_multiplier": 2,   # before v1.13; unused
        "burn_cost": 8, "burn_gain": 0.35, "burn_minutes": 20, "reserve": 30, "hold_level": 20,
        "station_keeping_per_day": 2, "reentry_altitude": 1.0, "survive_days": 14, "ceiling": 0,
    },
    "signal": {
        "tiers": [[0, "beacon", "Beacon"], [5, "relay", "Relay"], [15, "explorer", "Explorer"],
                  [40, "surveyor", "Surveyor"], [60, "array", "Array"], [80, "voyager", "Voyager"],
                  [100, "observatory", "Observatory"]],
        "min_minutes": 5,
    },
    "traits": {"window_days": 30, "min_share": 0.25, "max": 2},
    "life": {"enabled": True, "danger_below": 15, "alive_after": 20, "lost_hours": 12, "launch_fuel": 0,
             "distress_days": 3, "mayday_days": 1},
    "survey": {"enabled": True, "expire_hours": 48, "cooldown_hours": 6, "reward": 25,
               "chart_min": 3, "chart_max": 5, "minutes": 10},
    # The stylus: hours of play since it was installed, against its rated life. `installed` is a
    # date (YYYY-MM-DD); left empty, hours count from the first play logged.
    "stylus": {"installed": "", "rated_hours": 1000, "warn_at": [75, 90, 100]},
    # Ground Control: one transmission per track, grounded in collection data (ground.py)
    "ground": {"enabled": True, "use_llm": True, "track_facts": 2, "record_facts": 2},
    "panel": {"ticker_speed": 70, "ticker_gap_seconds": 2, "beat_offset_ms": 800, "collapse_idle_minutes": 60},
    "craving": {"satisfy_minutes": 10, "expire_hours": 72, "cooldown_hours": 4, "expired_rest_hours": 0,
                "always": True, "avoid_recent": 10, "bonus": 15, "bonus_half_life_hours": 12},
    "quips": {"min_gap_seconds": 240, "track_chance": 0.5, "idle_minutes": 10, "quiet_start": "23:00",
              "quiet_end": "07:00", "blocklist": []},
    "llm": {"enabled": True, "provider": "ollama", "api_url": "", "api_model": "", "reasoning": "", "daily_limit": 0,
            "url": "http://127.0.0.1:11434", "model": "llama3.2:3b", "temperature": 0.9,
            "timeout_seconds": 20, "keep_alive": "15m"},
    "canned": {},
}

NEEDS = ("fuel", "variety", "curiosity", "taste")
ORBIT_IDS = {"pad", "leo", "geo", "lunar", "deep"}
TIER_IDS = ["beacon", "relay", "explorer", "surveyor", "array", "voyager", "observatory"]
OLD_TIER_IDS = ["beacon", "relay", "explorer", "surveyor", "observatory"]     # before v1.18, for the floor

# Discogs genres (and Shazam's coarser genres) mapped to a visual trait. First match wins.
TRAIT_RULES = [
    ("jazz", ("jazz",)),
    ("hiphop", ("hip hop", "hip-hop", "rap")),
    ("funk", ("funk", "soul", "r&b", "disco")),
    ("electronic", ("electronic", "dance", "house", "techno")),
    ("folk", ("folk", "country", "world", "americana")),
    ("classical", ("classical", "opera", "orchestral")),
    ("blues", ("blues",)),
    ("rock", ("rock", "alternative", "metal", "punk", "indie")),
    ("pop", ("pop",)),
]
TRAIT_LABELS = {
    "jazz": "brass plating", "rock": "rivets and scorch marks", "electronic": "an LED band",
    "funk": "mirror-ball facets", "folk": "a wood-grain band", "classical": "a gilded ring",
    "hiphop": "a speaker cone", "pop": "candy stripes", "blues": "a midnight-blue finish",
    "screen": "a film-strip band", "comedy": "a red clown nose", "stories": "reading glasses", "holiday": "fairy lights",
}
HISTORY_DAYS = 31


# --- Config -------------------------------------------------------------------

def _merge(base: dict, over: dict, path: str = "") -> dict:
    out = copy.deepcopy(base)
    for k, v in over.items():
        if k in out and isinstance(out[k], dict) and isinstance(v, dict) and k != "weights":
            out[k] = _merge(out[k], v, f"{path}{k}.")
        elif k in out and out[k] is not None and not isinstance(v, type(out[k])) \
                and not (isinstance(out[k], float) and isinstance(v, int)):
            log.warning("Config %s%s has the wrong type; using the default", path, k)
        else:
            out[k] = v
    return out


def load_config(path: str) -> dict:
    try:
        with open(path, "rb") as f:
            user = tomllib.load(f)
    except FileNotFoundError:
        log.warning("No config at %s; using defaults", path)
        user = {}
    except tomllib.TOMLDecodeError as e:
        log.error("Config %s has a syntax error (%s); keeping the previous settings until it's fixed", path, e)
        raise
    cfg = _merge(DEFAULTS, user)
    cfg["mood"]["bands"] = sorted(cfg["mood"]["bands"], key=lambda b: -b[0])
    if "growth" in user:
        log.info("The [growth] section is from v1.1 and is no longer used; orbits replace it (see [orbit])")
    cfg["orbit"]["stages"] = sorted(cfg["orbit"]["stages"], key=lambda s: s[0])
    cfg["signal"]["tiers"] = sorted(cfg["signal"]["tiers"], key=lambda s: s[0])
    for s in cfg["orbit"]["stages"]:
        if s[1] not in ORBIT_IDS:
            log.warning("Orbit id %r isn't one of %s; it will be drawn as %s", s[1], sorted(ORBIT_IDS), "leo")
    for t in cfg["signal"]["tiers"]:
        if t[1] not in TIER_IDS:
            log.warning("Signal tier id %r isn't one of %s; it will be drawn as a beacon", t[1], TIER_IDS)
    return cfg


# --- Helpers ------------------------------------------------------------------

def clamp(v: float) -> float:
    return max(0.0, min(100.0, v))


def tags_of(play: dict) -> list:
    """Styles (or genres) for a play: Discogs if matched, otherwise Shazam's genre."""
    if play["styles"]:
        return play["styles"]
    if play["genres"]:
        return play["genres"]
    return [play["genre"]] if play["genre"] else []


def matches_any(tags: list, patterns: list) -> bool:
    low = [t.lower() for t in tags]
    return any(p.lower() in t for p in patterns for t in low)


def parse_hhmm(s: str) -> int:
    h, m = s.split(":")
    return int(h) * 60 + int(m)


def in_quiet_hours(cfg: dict, now: float) -> bool:
    try:
        start, end = parse_hhmm(cfg["quips"]["quiet_start"]), parse_hhmm(cfg["quips"]["quiet_end"])
    except (ValueError, AttributeError):
        return False
    if start == end:
        return False
    lt = time.localtime(now)
    cur = lt.tm_hour * 60 + lt.tm_min
    return start <= cur < end if start < end else (cur >= start or cur < end)


def born_ts(cfg: dict, db) -> int:
    if cfg["pet"]["born"]:
        try:
            return int(time.mktime(datetime.date.fromisoformat(cfg["pet"]["born"]).timetuple()))
        except ValueError:
            log.warning("pet.born isn't a YYYY-MM-DD date; ignoring it")
    row = db.execute("SELECT value FROM meta WHERE key = 'pet_born'").fetchone()
    if row:
        return int(row[0])
    now = int(time.time())
    with db:
        db.execute("INSERT OR IGNORE INTO meta (key, value) VALUES ('pet_born', ?)", (str(now),))
    return now


# --- Data ----------------------------------------------------------------------

YEAR_SQL = "r.year"


def load_plays(db, since: int) -> list:
    global YEAR_SQL
    has_releases = db.execute("SELECT 1 FROM sqlite_master WHERE name = 'releases'").fetchone() is not None
    if has_releases:
        cols = {c[1] for c in db.execute("PRAGMA table_info(releases)")}
        # the original release year once the nightly sync has found it, else the pressing's year
        YEAR_SQL = "COALESCE(NULLIF(r.original_year, 0), r.year)" if "original_year" in cols else "r.year"
    if has_releases:
        rows = db.execute(
            "SELECT p.id, p.started_at, COALESCE(p.ended_at, p.last_seen_at) AS end_at, p.artist, p.title, "
            "p.album, p.genre, p.release_id, p.track_position, p.side, r.styles, r.genres, r.title AS rtitle, "
            f"{YEAR_SQL} AS year, p.released "
            "FROM plays p LEFT JOIN releases r ON r.release_id = p.release_id "
            "WHERE COALESCE(p.ended_at, p.last_seen_at) >= ? ORDER BY p.started_at", (since,)).fetchall()
    else:
        rows = db.execute(
            "SELECT id, started_at, COALESCE(ended_at, last_seen_at) AS end_at, artist, title, album, genre, "
            "NULL AS release_id, '' AS track_position, '' AS side, NULL AS styles, NULL AS genres, NULL AS rtitle, "
            "NULL AS year, released "
            "FROM plays WHERE COALESCE(ended_at, last_seen_at) >= ? ORDER BY started_at", (since,)).fetchall()
    out = []
    for r in rows:
        year = r["year"] or 0
        if not year and r["released"]:               # not one of yours: Shazam's release date
            m = re.match(r"\s*(\d{4})", str(r["released"]))
            year = int(m.group(1)) if m else 0
        out.append({
            "id": r["id"], "start": r["started_at"], "end": max(r["end_at"], r["started_at"]),
            "artist": r["artist"], "title": r["title"], "album": r["rtitle"] or r["album"],
            "genre": r["genre"], "release_id": r["release_id"],
            "position": r["track_position"] or "", "side": r["side"] or "",
            "styles": json.loads(r["styles"]) if r["styles"] else [],
            "genres": json.loads(r["genres"]) if r["genres"] else [],
            "year": year if 1900 <= year <= 2100 else 0,
        })
    return out


def previous_play_of(db, release_id: int, before: int):
    row = db.execute("SELECT MAX(started_at) FROM plays WHERE release_id = ? AND started_at < ?",
                     (release_id, before)).fetchone()
    return row[0] if row else None


# --- Needs ------------------------------------------------------------------------

def need_fuel(cfg, plays, start_ts, now, initial: float = 60.0):
    c = cfg["needs"]["fuel"]
    fill = 100.0 / max(1, c["full_after_minutes"] * 60)
    drain = 100.0 / max(1, c["empty_after_hours"] * 3600)
    level, t = initial, start_ts
    for p in plays:
        s, e = max(p["start"], t), p["end"]
        if e <= t:
            continue
        level = clamp(level - drain * (s - t))
        level = clamp(level + fill * (e - s))
        t = e
    return clamp(level - drain * max(0, now - t))


def _effective(weights: dict) -> float:
    """How many things' worth: exp of the Shannon entropy. Four even styles count 4; one style
    with a sliver of another counts barely more than 1."""
    tot = sum(weights.values())
    if tot <= 0:
        return 0.0
    return math.exp(-sum((w / tot) * math.log(w / tot) for w in weights.values() if w > 0))


def need_variety(cfg, plays, now):
    """How mixed your recent listening is, in styles and in release eras. Listening time counts
    toward a style and a decade, weighted toward the last few days (halving every
    `half_life_days`, with no hard cut-off, so old sessions fade out instead of dropping off a
    cliff), and each side is scored by how many things' worth it's spread across:
    `style_target` styles' worth and `era_target` decades' worth fill it. Styles count
    `style_weight` of the score, eras the rest. The same artist several records running still
    costs `repeat_penalty` a record past `repeat_limit`."""
    c = cfg["needs"]["variety"]
    since = now - 31 * 86400           # everything loaded; the half-life does the forgetting
    half = max(0.1, c.get("half_life_days", 3)) * 86400
    styles, eras = {}, {}
    for p in plays:
        if p["end"] < since:
            continue
        w = max(0, p["end"] - max(p["start"], since)) * 0.5 ** (max(0, now - p["end"]) / half)
        if w <= 0:
            continue
        # the mix is measured by constellation (as on the logbook), so a deep dive into one genre's
        # many styles still reads as one thing; Shazam's genre stands in for records you don't own
        genres = p.get("genres") or ([p["genre"]] if p.get("genre") else [])
        sty = p.get("styles") or ([p["genre"]] if p.get("genre") and not p.get("genres") else [])
        lab = sectors.sector_label(genres, sty)
        styles[lab] = styles.get(lab, 0) + w
        if p.get("year"):
            decade = f"{p['year'] // 10 * 10}s"
            eras[decade] = eras.get(decade, 0) + w
    ns, ne = _effective(styles), _effective(eras)
    sw = min(1.0, max(0.0, c.get("style_weight", 0.6)))
    score = 100.0 * (sw * min(1.0, ns / max(1, c.get("genre_target", 4))) + (1 - sw) * min(1.0, ne / max(1, c.get("era_target", 4))))
    # How much recent listening backs it up, on a smooth curve: as old sessions fade the score eases
    # down day by day, and new listening builds it back gradually (about two-thirds full at
    # `fullness_minutes` of weighted listening, nearly full at three times that).
    total = sum(styles.values())
    score *= 1.0 - math.exp(-total / (max(5, c.get("fullness_minutes", 50)) * 60))

    # Same artist several records running: count consecutive albums by the latest artist.
    streak, last_artist, last_album = 0, None, None
    for p in reversed(plays):
        if last_artist is None:
            last_artist, last_album, streak = p["artist"], p["album"], 1
        elif p["artist"] != last_artist:
            break
        elif p["album"] != last_album:
            streak += 1
            last_album = p["album"]
    penalty = max(0, streak - c["repeat_limit"]) * c["repeat_penalty"]

    def shares(d, top=None, key=None):
        tot = sum(d.values()) or 1
        items = sorted(d.items(), key=key or (lambda kv: -kv[1]))
        if top and len(items) > top:
            rest = sum(v for _, v in items[top - 1:])
            items = items[:top - 1] + [("Other", rest)]
        return [[k, round(v / tot, 3)] for k, v in items]
    detail = {"styles_worth": round(ns, 1), "eras_worth": round(ne, 1),
              "targets": {"styles": c.get("genre_target", 4), "eras": c.get("era_target", 4)}, "by": "constellation",
              "styles": shares(styles, top=8), "eras": shares(eras, key=lambda kv: kv[0])}
    return round(clamp(score - penalty)), round(ns, 1), streak, detail


def curiosity_events(db, cfg, plays):
    """Records spun that had never been played here, or not for `dusty_days`. One event per record per day."""
    c = cfg["needs"]["curiosity"]
    seen, events = set(), []
    for p in plays:
        if not p["release_id"]:
            continue
        key = (p["release_id"], time.localtime(p["start"]).tm_yday)
        if key in seen:
            continue
        seen.add(key)
        prev = previous_play_of(db, p["release_id"], p["start"] - 6 * 3600)
        if prev is None:
            events.append((p, "never_played", c["never_played_boost"]))
        elif p["start"] - prev >= c["dusty_days"] * 86400:
            events.append((p, "dusty", c["dusty_boost"]))
    return events


def need_curiosity(cfg, events, now):
    """A meter: each unplayed or dusty record fills it, and it halves every `half_life_days`.
    Capped at 100 as each boost lands (not after adding them all up), so a burst of first spins
    can't hold it at 100 for days."""
    half = max(0.1, cfg["needs"]["curiosity"]["half_life_days"]) * 86400
    level, t = 0.0, None
    for p, _, boost in sorted(events, key=lambda e: e[0]["start"]):
        if t is not None:
            level *= 0.5 ** ((p["start"] - t) / half)
        level = min(100.0, level + boost)
        t = p["start"]
    if t is not None:
        level *= 0.5 ** ((now - t) / half)
    return clamp(level)


def favorites(cfg, plays, now):
    t = cfg["taste"]
    if t["favorites"]:
        return list(t["favorites"]), False
    if not t["learn"]:
        return [], False
    since = now - t["learn_days"] * 86400
    # Learn from listening older than the days Taste judges, so a fresh session can't make its
    # own style a favorite and then score itself as perfect taste.
    until = now - cfg["needs"]["taste"]["window_days"] * 86400
    seconds = {}
    for p in plays:
        if p["end"] < since or p["start"] > until:
            continue
        for tag in tags_of(p):
            seconds[tag] = seconds.get(tag, 0) + (p["end"] - max(p["start"], since))
    top = sorted(seconds.items(), key=lambda kv: -kv[1])[: t["learn_count"]]
    return [k for k, _ in top], True


def _secs(text) -> int:
    """A track length like "4:05" (or "1:02:30") in seconds; 0 if unknown."""
    try:
        n = 0
        for part in str(text or "").strip().split(":"):
            n = n * 60 + int(part)
        return n if ":" in str(text) else 0
    except ValueError:
        return 0


def dislikes(cfg, db, plays, favs, now):
    """The styles it dislikes, and whether it learned them. Set by hand, they're used as they are.
    Otherwise it learns them from skips, honestly: "if you won't sit through it, neither will I". A skip
    is a track cut short (under 40% of its listed length, then something else or silence). A style
    becomes a dislike with enough skips (`dislike_min_skips`) making up enough of its plays
    (`dislike_share`) in the last `learn_days`; never a favorite, at most `dislike_count` at a time,
    and it fades on its own once you stop skipping it."""
    t = cfg["taste"]
    if t["dislikes"]:
        return list(t["dislikes"]), False
    if not t.get("learn_dislikes", True):
        return [], False
    since = now - t["learn_days"] * 86400
    lengths = {(r[0], r[1]): _secs(r[2]) for r in db.execute("SELECT release_id, position, duration FROM tracks")}
    fav = {f.lower() for f in favs}
    counts = {}
    for p in plays:
        if p["start"] < since or p["end"] > now - 120 or not p.get("release_id") or not p.get("position"):
            continue
        length = lengths.get((p["release_id"], p["position"]), 0)
        if length < 60:
            continue                                     # no listed length: can't tell a skip
        skipped = (p["end"] - p["start"]) < 0.4 * length
        for tag in tags_of(p):
            c = counts.setdefault(tag, [0, 0])
            c[0] += 1
            c[1] += skipped
    found = [(tag, sk / n, sk) for tag, (n, sk) in counts.items()
             if sk >= t.get("dislike_min_skips", 3) and sk / n >= t.get("dislike_share", .35) and tag.lower() not in fav]
    found.sort(key=lambda x: (-x[1], -x[2]))
    return [tag for tag, _, _ in found[: t.get("dislike_count", 2)]], True


def need_taste(cfg, plays, favs, now):
    """A meter, like fuel: minutes in its favorite styles fill it (full after `full_after_minutes`),
    minutes in styles it dislikes drain it, everything else leaves it be. It halves every
    `half_life_days`, so it drops when nothing plays instead of holding at the last share."""
    c = cfg["needs"]["taste"]
    half = max(0.1, c.get("half_life_days", 2)) * 86400
    rate = 100.0 / (max(1, c.get("full_after_minutes", 60)) * 60)
    dislikes = cfg["taste"]["dislikes"]
    level, t = 0.0, None
    for p in plays:
        if t is not None:
            level *= 0.5 ** (max(0, p["start"] - t) / half)
        secs = max(0, p["end"] - p["start"])
        tags = tags_of(p)
        if favs and matches_any(tags, favs):
            level += rate * secs
        if dislikes and matches_any(tags, dislikes):
            level -= 1.2 * rate * secs
        level = min(100.0, max(0.0, level))
        t = p["end"]
    if t is not None:
        level *= 0.5 ** (max(0, now - t) / half)
    return clamp(level)


# --- Cravings -----------------------------------------------------------------------

def craving(db, cfg, need, recent_tags, favs, now, avoid=()):
    """A record from the cargo hold that would help the given need.

    Picks at random (not the same pick all day), skips records it craved recently, and
    falls back to records that haven't been played for a while when the need-specific
    choice has nothing to offer, so there's always something to want."""
    if db.execute("SELECT 1 FROM sqlite_master WHERE name = 'releases'").fetchone() is None:
        return None
    rows = db.execute(
        "SELECT r.release_id, r.title, r.artist, r.cover_file, r.styles, r.genres, MAX(p.started_at) AS last_played "
        "FROM releases r LEFT JOIN plays p ON p.release_id = r.release_id "
        "WHERE r.in_collection = 1 GROUP BY r.release_id").fetchall()
    if not rows:
        return None
    rng = random.Random()
    dusty_cut = now - cfg["needs"]["curiosity"]["dusty_days"] * 86400
    # Skip recent cravings; in a small collection where that leaves nothing, at least
    # never re-pick the one it has only just finished with.
    avoid = list(avoid)
    fresh = ([r for r in rows if r["release_id"] not in set(avoid)]
             or [r for r in rows if not avoid or r["release_id"] != avoid[-1]] or list(rows))

    def tags(r):
        return json.loads(r["styles"] or "[]") or json.loads(r["genres"] or "[]")

    pool, reason = [], ""
    if need == "curiosity":
        pool = [r for r in fresh if r["last_played"] is None] or [r for r in fresh if r["last_played"] < dusty_cut]
        reason = "a record that hasn't been played in ages"
    elif need == "variety":
        recent = {t.lower() for t in recent_tags}
        scored = [(sum(1 for t in tags(r) if t.lower() not in recent), r) for r in fresh]
        best = max((sc for sc, _ in scored), default=0)
        pool = [r for sc, r in scored if sc == best and sc > 0]
        reason = "something with a different sound"
    elif need == "taste" and favs:
        pool = sorted((r for r in fresh if matches_any(tags(r), favs)), key=lambda r: r["last_played"] or 0)[:6]
        reason = "one of its favorite styles"
    if not pool:
        # Nothing need-specific: the records that have waited longest for a spin.
        pool = sorted(fresh, key=lambda r: r["last_played"] or 0)[:10]
        reason = reason if need == "fuel" else "a record it hasn't heard in a while"
        if need == "fuel":
            reason = "anything at all, it's hungry"
    r = rng.choice(pool)
    return {"release_id": r["release_id"], "title": r["title"], "artist": r["artist"],
            "cover": f"/covers/{r['cover_file']}" if r["cover_file"] else None, "reason": reason}


def meta_get(db, key, default=None):
    row = db.execute("SELECT value FROM meta WHERE key = ?", (key,)).fetchone()
    if not row:
        return default
    try:
        return json.loads(row[0])
    except (TypeError, ValueError):
        return default


def meta_set(db, key, value) -> None:
    with db:
        if value is None:
            db.execute("DELETE FROM meta WHERE key = ?", (key,))
        else:
            db.execute("INSERT INTO meta (key, value) VALUES (?, ?) "
                       "ON CONFLICT(key) DO UPDATE SET value = excluded.value", (key, json.dumps(value)))


SPIN_GAP = 30 * 60   # same rule as the web app: tracks this close together are one spin


def savored_seconds(db, release_id: int, since: float) -> int:
    """How long a record has been on the turntable since a moment, counted by whole spins:
    from the first track heard to the last, including tracks filled in from the tracklist
    and a side flip in between, so untagged stretches still count."""
    rows = db.execute(
        "SELECT started_at, COALESCE(ended_at, last_seen_at) AS e FROM plays "
        "WHERE release_id = ? AND COALESCE(ended_at, last_seen_at) > ? ORDER BY started_at",
        (release_id, since)).fetchall()
    total, start, end = 0, None, None
    for s_, e in rows:
        s_ = max(s_, since)
        if start is not None and s_ - end <= SPIN_GAP:
            end = max(end, e)
        else:
            if start is not None:
                total += end - start
            start, end = s_, e
    if start is not None:
        total += end - start
    return int(total)


def _remember(db, held: dict, outcome: str, now: float, heard: int) -> None:
    hist = meta_get(db, "pet_craving_history", [])[-19:]
    hist.append({"release_id": held["release_id"], "title": held["title"], "artist": held["artist"],
                 "reason": held.get("reason", ""), "since": held["since"], "at": int(now),
                 "outcome": outcome, "savored_minutes": round(heard / 60, 1)})
    meta_set(db, "pet_craving_history", hist)


def manage_craving(db, cfg, needs, lowest, recent_tags, favs, now, playing_release=None):
    """One craving at a time, kept until it's savored or gives up, then a rest, then the next.

    Returns (craving or None, satisfied craving or None, expired craving or None).
    The craving lives in the meta table, so it survives restarts and doesn't change
    just because a need moved."""
    c = cfg["craving"]
    held = meta_get(db, "pet_craving")
    satisfied = expired = None

    # Rest is stored as an end time. (v1.1 stored when the last craving was satisfied.)
    rest_until = meta_get(db, "pet_craving_rest_until")
    if rest_until is None and meta_get(db, "pet_craving_rest") is not None:
        rest_until = meta_get(db, "pet_craving_rest") + c["cooldown_hours"] * 3600

    if held:
        exists = db.execute("SELECT 1 FROM releases WHERE release_id = ? AND in_collection = 1",
                            (held["release_id"],)).fetchone()
        heard = savored_seconds(db, held["release_id"], held["since"]) if exists else 0
        on_now = playing_release == held["release_id"]
        if not exists:
            meta_set(db, "pet_craving", None)           # the record left the collection
            held = None
        elif heard >= c["satisfy_minutes"] * 60:
            satisfied = dict(held, heard_minutes=round(heard / 60, 1))
            meta_set(db, "pet_satisfied", meta_get(db, "pet_satisfied", [])[-9:] + [{"at": int(now), "title": held["title"]}])
            _remember(db, held, "satisfied", now, heard)
            rest_until = now + c["cooldown_hours"] * 3600
            meta_set(db, "pet_craving", None)
            held = None
        elif not on_now and now - held["since"] > c["expire_hours"] * 3600:
            expired = dict(held, heard_minutes=round(heard / 60, 1))
            _remember(db, held, "expired", now, heard)
            rest_until = now + c["expired_rest_hours"] * 3600
            meta_set(db, "pet_craving", None)
            held = None
        else:
            held = dict(held, heard_minutes=round(heard / 60, 1))
    meta_set(db, "pet_craving_rest_until", int(rest_until) if rest_until else None)
    meta_set(db, "pet_craving_rest", None)

    if held is None:
        rested = rest_until is None or now >= rest_until
        wants = c["always"] or needs[lowest] < max(cfg["mood"]["low"] * 2, 50)
        if rested and wants:
            recent = [h["release_id"] for h in meta_get(db, "pet_craving_history", [])][-int(c["avoid_recent"]):]
            for gone in (satisfied, expired):
                if gone:
                    recent.append(gone["release_id"])
            pick = craving(db, cfg, lowest, recent_tags, favs, now, avoid=recent)
            if pick:
                held = dict(pick, need=lowest, since=int(now))
                meta_set(db, "pet_craving", held)
                held = dict(held, heard_minutes=0.0)

    if held:
        held["satisfy_minutes"] = c["satisfy_minutes"]
        held["expires_at"] = int(held["since"] + c["expire_hours"] * 3600)
    return held, satisfied, expired


def afterglow(db, cfg, now) -> float:
    """Mood points still left over from recently satisfied cravings."""
    c = cfg["craving"]
    half = max(0.1, c["bonus_half_life_hours"]) * 3600
    total = sum(c["bonus"] * 0.5 ** ((now - e["at"]) / half) for e in meta_get(db, "pet_satisfied", []))
    return min(30.0, total)


def pick_dusty(db, now):
    """A record that's never been played, or not for the longest time. Changes hourly."""
    if db.execute("SELECT 1 FROM sqlite_master WHERE name = 'releases'").fetchone() is None:
        return None
    rows = db.execute(
        "SELECT r.release_id, r.title, r.artist, MAX(p.started_at) AS last_played "
        "FROM releases r LEFT JOIN plays p ON p.release_id = r.release_id "
        "WHERE r.in_collection = 1 GROUP BY r.release_id "
        "ORDER BY last_played IS NOT NULL, last_played LIMIT 12").fetchall()
    if not rows:
        return None
    r = random.Random(int(now // 3600)).choice(rows)
    return {"title": r["title"], "artist": r["artist"], "last_played": r["last_played"]}


# --- Progression ---------------------------------------------------------------------

def _level(table, value):
    """Index of the highest threshold reached."""
    return max((i for i, t in enumerate(table) if value >= t[0]), default=0)


def orbit_state(cfg, plays_all, born, now):
    """Altitude (in listening hours) with gentle decay.

    Listening raises altitude. After `grace_days` without a record it slips by
    `decay_hours_per_day`, but never more than `max_drop` orbits below its best and never
    back to the pad once hatched. Lost altitude is regained `recovery_multiplier` times faster.
    """
    o = cfg["orbit"]
    stages = o["stages"]
    grace = max(0.0, o["grace_days"]) * 86400
    rate = max(0.0, o["decay_hours_per_day"]) / 86400
    boost = max(1.0, float(o["recovery_multiplier"]))

    def floor_for(peak):
        idx = _level(stages, peak)
        lowest = 1 if (len(stages) > 1 and peak >= stages[1][0]) else 0
        return stages[max(lowest, idx - max(0, int(o["max_drop"])))][0]

    def drift(alt, peak, gap):
        if gap <= grace:
            return alt
        return max(min(alt, floor_for(peak)), alt - rate * (gap - grace)) if alt > floor_for(peak) else alt

    alt = peak = 0.0
    last = born
    for p in plays_all:
        s, e = max(p["start"], born, last), p["end"]
        if e <= s:
            continue
        alt = drift(alt, peak, s - last)
        hours = (e - s) / 3600
        if alt < peak:                       # catching back up to its best, faster
            fast = min(hours * boost, peak - alt)
            alt += fast
            hours -= fast / boost
        alt += hours
        peak = max(peak, alt)
        last = e
    idle = max(0.0, now - last)
    alt = drift(alt, peak, idle)

    idx = _level(stages, alt)
    cur, nxt = stages[idx], (stages[idx + 1] if idx + 1 < len(stages) else None)
    return {
        "id": cur[1] if cur[1] in ORBIT_IDS else "leo",
        "label": cur[2],
        "index": idx,
        "count": len(stages),
        "altitude": round(alt, 1),
        "peak": round(peak, 1),
        "lost": round(max(0.0, peak - alt), 1),
        "idle_days": round(idle / 86400, 1),
        "grace_days": o["grace_days"],
        "drifting": idle > grace and peak > 0,
        "next_label": nxt[2] if nxt else None,
        "next_hours": nxt[0] if nxt else None,
        "floor_label": stages[_level(stages, floor_for(peak))][2],
    }


def flight_orbit(cfg, fl: dict, plays_all: list, now: float) -> dict:
    """The orbit as the page and the pet see it, from the flight simulation."""
    stages = cfg["orbit"]["stages"]
    alt = fl["altitude"]
    idx = _level(stages, alt)
    if fl["launched"] and idx == 0:
        idx = 1              # falling (or down): it was in low orbit, not back on the pad
    cur, nxt = stages[idx], (stages[idx + 1] if idx + 1 < len(stages) else None)
    last = max((p["end"] for p in plays_all), default=now)
    return {
        "id": cur[1] if cur[1] in ORBIT_IDS else "leo",
        "label": "Re-entered" if fl["reentered_at"] is not None else cur[2],
        "index": idx,
        "count": len(stages),
        "altitude": round(alt, 1),
        "peak": round(fl["peak"], 1),
        "lost": round(max(0.0, fl["peak"] - alt), 1),
        "idle_days": round(max(0.0, now - last) / 86400, 1),
        "drifting": fl["slipping"],          # the name the mind and milestones already use
        "slipping": fl["slipping"],
        "holding": fl["holding"],
        "launched": fl["launched"],
        "eta_days": fl["eta_days"],
        "burns": fl["burns"],
        "last_burn": fl["last_burn"],
        "ceiling": fl["ceiling"],
        "reentry_altitude": fl["reentry_altitude"],
        "hold_level": fl["hold_level"],
        "reserve": fl["reserve"],
        "launch_altitude": stages[1][0] if len(stages) > 1 else 1,
        # for the page's fuel tank and altimeter
        "stages": [[st[0], st[1] if st[1] in ORBIT_IDS else "leo", st[2]] for st in stages],
        "burn_line": round(fl["reserve"] + cfg["orbit"]["burn_cost"], 1),     # fuel needed for a burn to fire
        "climbing": bool(fl["last_burn"] and now - fl["last_burn"] < 1800),
        "next_label": nxt[2] if nxt else None,
        "next_hours": nxt[0] if nxt else None,
    }


def stylus_state(db, cfg, now: float) -> dict:
    """Hours the stylus has played since it was installed (or since the first play logged)."""
    c = cfg.get("stylus", {})
    since, installed = 0, (c.get("installed") or "").strip()
    if installed:
        try:
            since = datetime.datetime.strptime(installed, "%Y-%m-%d").timestamp()
        except ValueError:
            installed = ""
    row = db.execute("SELECT COALESCE(SUM(MIN(COALESCE(ended_at, last_seen_at), ?) - started_at), 0), MIN(started_at) "
                     "FROM plays WHERE started_at >= ?", (now, since)).fetchone()
    hours = max(0.0, (row[0] or 0) / 3600)
    rated = max(1, int(c.get("rated_hours", 1000) or 1000))
    return {"hours": round(hours, 1), "rated": rated, "pct": round(100 * hours / rated, 1),
            "installed": installed, "since": int(since or row[1] or now)}


def legacy_signal_index(cfg, plays_all) -> int:
    """The tier as it was before v1.16 (distinct styles ever heard), to carry an existing probe across."""
    seconds = {}
    for p in plays_all:
        for tag in tags_of(p):
            seconds[tag.lower()] = seconds.get(tag.lower(), 0) + (p["end"] - p["start"])
    heard = sum(1 for v in seconds.values() if v >= cfg["signal"].get("min_minutes", 5) * 60)
    old = [[0, "beacon"], [5, "relay"], [12, "explorer"], [25, "surveyor"], [45, "observatory"]]
    return _level(old, heard)


def signal_state(cfg, plays_all, born: float, now: float, floor: int = 0):
    """The link with Earth: how regularly you keep in touch. Its strength is how many of the last
    14 days had any listening; the equipment tier comes from total days of contact in this
    probe's life (and never goes below `floor`, the tier a probe carried over from before)."""
    c = cfg["signal"]
    days = set()
    for p in plays_all:
        if p["end"] >= born:
            days.add(datetime.date.fromtimestamp(p["start"]).toordinal())
    today = datetime.date.fromtimestamp(now).toordinal()
    recent = [today - 13 + i in days for i in range(14)]      # oldest first, today last
    n = sum(recent)
    strength = 0 if n == 0 else 1 if n <= 2 else 2 if n <= 5 else 3 if n <= 8 else 4 if n <= 11 else 5
    tiers = c["tiers"]
    idx = max(_level(tiers, len(days)), min(floor, len(tiers) - 1))
    cur, nxt = tiers[idx], (tiers[idx + 1] if idx + 1 < len(tiers) else None)
    tier_index = TIER_IDS.index(cur[1]) if cur[1] in TIER_IDS else 0
    return {"id": cur[1] if cur[1] in TIER_IDS else "beacon", "label": cur[2], "index": idx, "count": len(tiers),
            "draw_tier": tier_index, "contact_days": len(days), "recent": recent, "recent_days": n,
            "strength": strength, "next_label": nxt[2] if nxt else None, "next_days": nxt[0] if nxt else None}


def trait_for(genre: str):
    g = genre.lower()
    for trait, words in TRAIT_RULES:
        if any(w in g for w in words):
            return trait
    return None


def traits_state(cfg, plays_all, now):
    """Up to `max` visual traits, one per constellation with a big enough share of recent listening.
    A play's constellation comes from sectors.py, as on the logbook; for records you don't own,
    Shazam's genre stands in for the styles (so a Shazam "Soundtrack" counts as one)."""
    c = cfg["traits"]
    since = now - 31 * 86400
    total, by = 0, {}
    for p in plays_all:
        if p["end"] < since:
            continue
        dur = p["end"] - max(p["start"], since)
        total += dur
        genres = p["genres"] or ([p["genre"]] if p["genre"] else [])
        styles = p["styles"] or ([p["genre"]] if p["genre"] and not p["genres"] else [])
        t = sectors.sector_of(genres, styles)
        if t in TRAIT_LABELS:
            by[t] = by.get(t, 0) + dur
    if total <= 0:
        return []
    out = []
    for t, secs in sorted(by.items(), key=lambda kv: -kv[1]):
        share = secs / total
        if share >= c["min_share"] and len(out) < max(0, int(c["max"])):
            out.append({"id": t, "label": TRAIT_LABELS[t], "genre": sectors.SECTORS[t], "share": round(share, 2)})
    return out


# --- Snapshot -------------------------------------------------------------------------

def snapshot(db, cfg: dict, now: float | None = None) -> dict:
    now = now or time.time()
    life = cfg["life"]
    st_row = db.execute("SELECT state FROM station WHERE id = 1").fetchone()
    playing_now = bool(st_row and st_row["state"] in ("playing", "unknown"))
    # Lost in space: stay dark until you next play a record (or `lost_hours` pass), then launch
    # the next probe as an egg with a fresh birthday.
    lost = meta_get(db, "pet_lost") if life["enabled"] else None
    reborn_event = None
    if lost and (playing_now or now - lost["at"] >= life["lost_hours"] * 3600):
        gen_new = lifecycle.reborn(db, cfg, now, meta_get, meta_set)
        reborn_event = {"generation": gen_new, "name": lifecycle.display_name(cfg["pet"].get("base_name") or cfg["pet"]["name"], gen_new),
                        "previous": lost.get("name", "")}
        lost = None
    gen = lifecycle.generation(db, meta_get)
    name = lifecycle.display_name(cfg["pet"].get("base_name") or cfg["pet"]["name"], gen)
    born = born_ts(cfg, db)
    # Needs look at recent listening regardless of age; only growth counts from the birthday.
    start = int(now - HISTORY_DAYS * 86400)
    plays_all = load_plays(db, 0)
    plays = [p for p in plays_all if p["end"] >= start]

    # Fuel and altitude come from one flight simulation, replayed from the probe's seed: its launch,
    # or, for a probe that was already flying before v1.13, the altitude and fuel it had then.
    seed = meta_get(db, "pet_flight_seed")
    if seed is None:
        if gen > 1 and now - born < 600:
            seed = {"t": born, "alt": 0.0, "fuel": float(life["launch_fuel"]), "launched": False}
        else:
            old = orbit_state(cfg, plays_all, born, now)
            old_fuel = need_fuel(cfg, [p for p in plays_all if p["end"] >= born], born, now, life["launch_fuel"]) if gen > 1 \
                else need_fuel(cfg, plays, start, now)
            seed = {"t": int(now), "alt": float(old["altitude"]), "fuel": float(old_fuel), "launched": old["index"] >= 1}
        meta_set(db, "pet_flight_seed", seed)
    sides = {}
    for rid, pos, side in db.execute("SELECT release_id, position, side FROM tracks ORDER BY release_id, seq"):
        if side:
            sides.setdefault(rid, {}).setdefault(side, []).append(pos)
    fl = flight.simulate(cfg, [p for p in plays_all if p["end"] > seed["t"]], sides, seed["t"], now,
                         seed["alt"], seed["fuel"], seed["launched"])
    fuel = fl["fuel"]
    variety, distinct, streak, variety_detail = need_variety(cfg, plays, now)
    discoveries, disc_streak = curiosity_mod.analyse(cfg, plays_all, now, HISTORY_DAYS)
    if lost:
        mission, mission_rest, mission_event = None, 0, None
    else:
        mission, mission_rest, mission_event = curiosity_mod.manage_mission(db, cfg, plays_all, discoveries, now,
                                                                            meta_get, meta_set)
    curiosity = curiosity_mod.meter(cfg, discoveries + curiosity_mod.reward_events(db, meta_get), now)
    events = [({"id": e["play_id"]}, e["kind"], e["boost"]) for e in discoveries
              if e["kind"] in ("never_played", "dusty")]
    favs, learned = favorites(cfg, plays, now)
    dis, dis_learned = dislikes(cfg, db, plays_all, favs, now)
    cfg = {**cfg, "taste": {**cfg["taste"], "dislikes": dis}}      # learned dislikes count everywhere dislikes do
    taste = need_taste(cfg, plays, favs, now)
    needs = {"fuel": fuel, "variety": variety, "curiosity": curiosity, "taste": taste}

    # The craving is settled before mood, so a just-satisfied craving lifts the mood right away.
    low = cfg["mood"]["low"]
    lowest = min(NEEDS, key=lambda n: needs[n])
    recent_tags = [t for p in plays if p["end"] >= now - 7 * 86400 for t in tags_of(p)]
    cur_rel = db.execute("SELECT p.release_id FROM station s JOIN plays p ON p.id = s.play_id "
                         "WHERE s.id = 1 AND s.state = 'playing'").fetchone()
    if lost:
        crave, satisfied, expired = None, None, None
    else:
        crave, satisfied, expired = manage_craving(db, cfg, needs, lowest, recent_tags, favs, now,
                                                   cur_rel[0] if cur_rel else None)

    weights = cfg["mood"]["weights"]
    wsum = sum(max(0.0, float(weights.get(n, 0))) for n in NEEDS) or 1.0
    score = sum(needs[n] * max(0.0, float(weights.get(n, 0))) for n in NEEDS) / wsum
    glow = afterglow(db, cfg, now)
    score = min(100.0, score + glow)
    mood = next((name for floor, name in cfg["mood"]["bands"] if score >= floor), cfg["mood"]["bands"][-1][1])

    orbit = flight_orbit(cfg, fl, plays_all, now)
    # The tier a probe can't fall below, remembered by id (v1.18 inserted tiers, which would shift
    # a remembered position). Converted once from the position stored before.
    floor_id = meta_get(db, "pet_signal_floor_id")
    if floor_id is None:
        old = meta_get(db, "pet_signal_floor")
        if old is None:    # first run since v1.16: keep the equipment an existing probe already earned
            old = legacy_signal_index(cfg, [p for p in plays_all if p["end"] >= born]) if gen == 1 else 0
        floor_id = OLD_TIER_IDS[max(0, min(int(old), len(OLD_TIER_IDS) - 1))]
        meta_set(db, "pet_signal_floor_id", floor_id)
    tier_ids = [t[1] for t in cfg["signal"]["tiers"]]
    floor = tier_ids.index(floor_id) if floor_id in tier_ids else 0
    signal = signal_state(cfg, plays_all, born, now, floor)
    traits = traits_state(cfg, plays_all, now)

    station = db.execute("SELECT state FROM station WHERE id = 1").fetchone()
    playing = bool(station and station["state"] in ("playing", "unknown"))
    activity = "listening" if playing else ("sleeping" if in_quiet_hours(cfg, now) else "idle")

    if crave and playing:
        cur = db.execute("SELECT p.release_id FROM station s JOIN plays p ON p.id = s.play_id WHERE s.id = 1").fetchone()
        crave["savoring"] = bool(cur and cur[0] == crave["release_id"])

    # Life and death: the best it has done, distress when fuel runs low, and the end at zero.
    died, danger = None, False
    if life["enabled"] and not lost:
        peak = lifecycle.track_peak(db, meta_get, meta_set, fuel, orbit, signal, traits)
        eta = orbit.get("eta_days")
        danger = bool(orbit["launched"] and orbit["slipping"] and eta is not None and eta <= life["distress_days"])
        if fl["reentered_at"] is not None:
            # it fell below the re-entry line: burned up, splashed down at Point Nemo
            stats = lifecycle.life_stats(db, plays_all, born, now, cfg, curiosity_mod)
            stats["peak_altitude"] = fl["peak"]
            died = lifecycle.die(db, cfg, name, gen, born, fl["reentered_at"], orbit, peak, stats, meta_get, meta_set)
            lost = meta_get(db, "pet_lost")
            mission, crave = None, None
            for k in ("pet_craving", "pet_mission"):
                meta_set(db, k, None)
    if lost:
        needs = {n: 0 for n in NEEDS}
        score, mood, activity = 0, "lost", "sleeping"

    return {
        "name": name,
        "generation": gen,
        "updated_at": int(now),
        "born": born,
        "orbit": orbit,
        "signal": signal,
        "traits": traits,
        "hatched": orbit["id"] != "pad",
        "needs": {n: round(v) for n, v in needs.items()},
        "low": [] if lost else [n for n in NEEDS if needs[n] < low],
        "lowest": lowest,
        "mood": mood,
        "mood_score": round(score),
        "activity": activity,
        "favorites": favs,
        "favorites_learned": learned,
        "dislikes": dis, "dislikes_learned": dis_learned,
        "distinct_styles": distinct,
        "artist_streak": streak,
        "craving": crave,
        "afterglow": round(glow),
        "craving_satisfied": satisfied,
        "craving_expired": expired,
        "curiosity_events": [{"play_id": p["id"], "kind": k} for p, k, _ in events],
        "curiosity": {"streak": disc_streak, "charted": curiosity_mod.charted(discoveries, now),
                      "detail": insights.curiosity_detail(discoveries + curiosity_mod.reward_events(db, meta_get), now),
                      "recent": [{"kind": e["kind"], "title": e["title"]} for e in discoveries
                                 if e["kind"] not in ("familiar",) and now - e["t"] <= 12 * 3600][-6:],
                      "familiar_now": any(e["kind"] == "familiar" and now - e["t"] <= 900 for e in discoveries)},
        "variety": variety_detail,
        "stylus": stylus_state(db, cfg, now),
        "taste_detail": insights.taste_detail(db, cfg, plays_all, favs, learned, now, tags_of, matches_any),
        "mood_detail": ({"pulls": [], "flavor": None, "phrase": "Lost in space", "effect": "", "afterglow": 0} if lost
                        else insights.mood_detail(cfg, needs, score, mood, glow)),
        "mood_history": insights.mood_history(db, now),
        # earned looks: mission patches (this probe's completed missions, up to 3), age, generation
        "cosmetics": {"patches": min(3, int(meta_get(db, "pet_missions_done", 0) or 0)),
                      "age_days": round(max(0, now - born) / 86400, 1), "generation": gen},
        "mission": mission,
        "mission_rest": mission_rest,
        "mission_reward": cfg["survey"]["reward"] if cfg["survey"]["enabled"] else None,
        "mission_event": {"kind": mission_event[0], "mission": mission_event[1]} if mission_event else None,
        "danger": danger,
        "lost": ({"at": lost["at"], "name": lost.get("name", name), "memorial_id": lost.get("memorial_id"),
                  "relaunch_at": lost["at"] + life["lost_hours"] * 3600} if lost else None),
        "died": died,                 # one-off: the memorial, the tick it happens
        "reborn": reborn_event,       # one-off: the next probe launched
    }
