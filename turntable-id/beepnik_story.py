"""Beepnik's story: the history of his needs, his journey, his achievements, and two ways to talk
to him ("Ask for a record", "How are you?"). Shared by the web app (to show it) and the pet service
(to record achievements as they're earned).

Everything here reads the station's own records; the only writes are the achievements table and the
log lines for achievements and conversations."""
import datetime
import json
import random
import time

import pet_engine
import curiosity as curiosity_mod
import flight

NEEDS = pet_engine.NEEDS
SCHEMA = """CREATE TABLE IF NOT EXISTS pet_achievements (
    id TEXT PRIMARY KEY,
    earned_at INTEGER NOT NULL
)"""


def ensure_schema(db) -> None:
    try:
        db.execute(SCHEMA)
    except Exception:          # a read-only connection (the web app): the pet service creates it
        pass


# --- 1. How his needs moved --------------------------------------------------------------------------
_hist_cache = {}


def _sides(db) -> dict:
    sides = {}
    for rid, pos, side in db.execute("SELECT release_id, position, side FROM tracks ORDER BY release_id, seq"):
        if side:
            sides.setdefault(rid, {}).setdefault(side, []).append(pos)
    return sides


def needs_at(db, cfg, when: float, plays_all: list, sides: dict) -> dict:
    """The four needs as they stood at `when`, from the plays before it. Read-only: unlike the engine's
    snapshot, it changes no missions, cravings or memory."""
    before = [p for p in plays_all if p["start"] <= when]
    window = [p for p in before if p["end"] >= when - pet_engine.HISTORY_DAYS * 86400]
    seed = pet_engine.meta_get(db, "pet_flight_seed")
    if seed and seed.get("t", 0) <= when:
        fl = flight.simulate(cfg, [p for p in before if p["end"] > seed["t"]], sides, seed["t"], when,
                             seed["alt"], seed["fuel"], seed["launched"])
        fuel = fl["fuel"]
    else:
        fuel = pet_engine.need_fuel(cfg, window, int(when - pet_engine.HISTORY_DAYS * 86400), when)
    variety = pet_engine.need_variety(cfg, window, when)[0]
    discoveries, _ = curiosity_mod.analyse(cfg, before, when, pet_engine.HISTORY_DAYS)
    rewards = [e for e in curiosity_mod.reward_events(db, pet_engine.meta_get) if e.get("at", 0) <= when]
    curiosity = curiosity_mod.meter(cfg, discoveries + rewards, when)
    favs, _ = pet_engine.favorites(cfg, window, when)
    taste = pet_engine.need_taste(cfg, window, favs, when)
    return {k: int(round(max(0, min(100, v)))) for k, v in
            (("fuel", fuel), ("variety", variety), ("curiosity", curiosity), ("taste", taste))}


def history(db, cfg, days: int = 7, now: float | None = None) -> list:
    """Each need at the end of each of the last `days` days, then now: a sparkline's worth."""
    now = now or time.time()
    last = db.execute("SELECT MAX(id), MAX(last_seen_at) FROM plays").fetchone()
    key = (days, tuple(last), int(now // 600))
    if key in _hist_cache:
        return _hist_cache[key]
    plays_all = pet_engine.load_plays(db, 0)
    sides = _sides(db)
    today = datetime.datetime.fromtimestamp(now).replace(hour=0, minute=0, second=0, microsecond=0)
    points = [(today - datetime.timedelta(days=d)).timestamp() for d in range(days - 1, 0, -1)]
    out = []
    for t in points + [now]:
        try:
            out.append({"at": int(t), **needs_at(db, cfg, t, plays_all, sides)})
        except Exception:                      # a gap in the record shouldn't hide the whole chart
            continue
    _hist_cache.clear()
    _hist_cache[key] = out
    return out


# --- 2. His journey -----------------------------------------------------------------------------------
JOURNEY_KINDS = {
    # kind: (title, colour group)
    "hatch": ("Hatched", "lilac"), "orbit_up": ("Climbed to {value}", "gold"), "tier_up": ("Signal: {value}", "sky"),
    "trait_gained": ("New look: {value}", "lilac"), "dislike_new": ("Went off {value}", "pink"), "burn": ("Burn complete", "gold"), "mission_done": ("Mission complete", "pink"),
    "achievement": ("Achievement: {value}", "gold"), "death": ("Lost at Point Nemo", "pink"), "rebirth": ("A new probe launched", "lilac"),
}


def journey(db, born: float, limit: int = 14) -> list:
    """Milestones since this probe was born, newest first: orbits, tiers, looks, burns, missions,
    achievements. Drawn from the milestones and log the station already keeps."""
    items = []
    for at, kind, value in db.execute("SELECT at, kind, value FROM pet_milestones WHERE at >= ? AND kind IN "
                                      "('hatch', 'orbit_up', 'tier_up', 'trait_gained', 'dislike_new') ORDER BY at", (int(born),)):
        items.append({"at": at, "kind": kind, "value": value})
    for at, kind, text in db.execute("SELECT at, kind, text FROM pet_log WHERE at >= ? AND kind IN "
                                     "('burn', 'mission_done', 'achievement', 'death', 'rebirth') ORDER BY at", (int(born),)):
        value = text.split(":", 1)[1].split(".")[0].strip() if kind == "achievement" and ":" in text else ""
        items.append({"at": at, "kind": kind, "value": value, "text": text})
    out = []
    for it in items:
        title, group = JOURNEY_KINDS.get(it["kind"], (it["kind"], "lilac"))
        out.append({"at": it["at"], "kind": it["kind"], "title": title.format(value=it.get("value") or ""), "group": group,
                    "detail": it.get("text") if it["kind"] in ("burn", "mission_done", "death", "rebirth") else ""})
    out.append({"at": int(born), "kind": "launch", "title": "Launched", "group": "lilac", "detail": "On the launch pad"})
    out.sort(key=lambda x: -x["at"])
    # one entry per burn day, so a busy week of burns doesn't crowd out everything else
    seen, trimmed = set(), []
    for it in out:
        k = (it["kind"], datetime.date.fromtimestamp(it["at"])) if it["kind"] == "burn" else (it["kind"], it["at"], it["title"])
        if k not in seen:
            seen.add(k)
            trimmed.append(it)
    return trimmed[:limit]


# --- 3. Achievements ---------------------------------------------------------------------------------------
ACHIEVEMENTS = [
    # id, name, how to earn it, group
    ("first_contact", "First contact", "Play your first record", "listening"),
    ("centurion", "Centurion", "Play 100 tracks", "listening"),
    ("veteran", "Mission veteran", "Play 1,000 tracks", "listening"),
    ("full_side", "Side by side", "Play a whole side through", "listening"),
    ("flip_side", "Flip side", "Play both sides of a record in one sitting", "listening"),
    ("marathon", "Marathon", "Listen for 4 hours in one sitting", "listening"),
    ("night_owl", "Night owl", "Play a record between midnight and 4 AM", "listening"),
    ("early_bird", "Early bird", "Play a record between 5 and 8 AM", "listening"),
    ("week_signal", "Week-long signal", "Play something 7 days in a row", "listening"),
    ("genre_explorer", "Genre explorer", "Play records from 8 genres", "variety"),
    ("time_traveller", "Time traveller", "Play records from 5 decades", "variety"),
    ("crate_digger", "Crate digger", "Rescue 10 dusty records", "curiosity"),
    ("deep_cargo", "Deep cargo", "Play 50 different records", "curiosity"),
    ("regular", "A regular", "Spin one record 10 times", "taste"),
    ("liftoff", "Liftoff", "Reach low Earth orbit", "flight"),
    ("geostationary", "Geostationary", "Reach geostationary orbit", "flight"),
    ("lunar", "Lunar transfer", "Head for the Moon", "flight"),
    ("deep_space", "Deep space", "Reach deep space", "flight"),
    ("explorer", "Explorer", "Reach the Explorer signal tier", "flight"),
    ("voyager", "Voyager", "Reach the Voyager signal tier", "flight"),
    ("mission_specialist", "Mission specialist", "Complete 5 survey missions", "flight"),
]
SESSION_GAP = 45 * 60


def _sessions(plays):
    out, cur = [], []
    for p in sorted(plays, key=lambda p: p["start"]):
        if cur and p["start"] - max(q["end"] for q in cur) > SESSION_GAP:
            out.append(cur)
            cur = []
        cur.append(p)
    if cur:
        out.append(cur)
    return out


def _streak(days: set) -> int:
    best = run = 0
    prev = None
    for d in sorted(days):
        run = run + 1 if prev and (d - prev).days == 1 else 1
        best, prev = max(best, run), d
    return best


def progress(db, cfg, state: dict | None = None, now: float | None = None) -> list:
    """Every achievement with its progress (value of target), and when it was earned."""
    now = now or time.time()
    ensure_schema(db)
    try:
        earned = dict(db.execute("SELECT id, earned_at FROM pet_achievements").fetchall())
    except Exception:
        earned = {}
    plays = pet_engine.load_plays(db, 0)
    sides = _sides(db)
    sessions = _sessions(plays)
    longest = max((max(p["end"] for p in s) - min(p["start"] for p in s) for s in sessions), default=0)

    full_side = flip_side = 0
    for s in sessions:
        heard = {}
        for p in s:
            if p["release_id"] and p["position"]:
                heard.setdefault(p["release_id"], set()).add(p["position"])
        for rid, pos in heard.items():
            done = [side for side, tracks in sides.get(rid, {}).items() if len(tracks) >= 2 and set(tracks) <= pos]
            full_side = max(full_side, 1 if done else 0)
            flip_side = max(flip_side, 1 if len(done) >= 2 else 0)

    hours = [datetime.datetime.fromtimestamp(p["start"]).hour for p in plays]
    days = {datetime.date.fromtimestamp(p["start"]) for p in plays}
    genres = {g.strip().lower() for p in plays for g in (p.get("genres") or [p.get("genre")] if isinstance(p.get("genres"), list) else
                                                          str(p.get("genres") or p.get("genre") or "").split(",")) if g and g.strip()}
    rel = {r[0]: r for r in db.execute("SELECT release_id, original_year, year, date_added FROM releases")}
    decades = set()
    for rid in {p["release_id"] for p in plays if p["release_id"]}:
        r = rel.get(rid)
        y = (r[1] or r[2]) if r else 0
        if y and int(y) > 1900:
            decades.add(int(y) // 10)
    # dusty rescues: a record's first play 60+ days after it joined the collection, or a 180-day gap between plays
    by_rel = {}
    for p in plays:
        if p["release_id"]:
            by_rel.setdefault(p["release_id"], []).append(p["start"])
    dusty = 0
    for rid, starts in by_rel.items():
        starts.sort()
        added = rel.get(rid)[3] if rel.get(rid) else None
        try:
            added_ts = datetime.datetime.fromisoformat(str(added).replace("Z", "+00:00")).timestamp() if added else None
        except ValueError:
            added_ts = None
        if added_ts and starts[0] - added_ts >= 60 * 86400:
            dusty += 1
        elif any(b - a >= 180 * 86400 for a, b in zip(starts, starts[1:])):
            dusty += 1
    spins = {}
    for s in sessions:
        for rid in {p["release_id"] for p in s if p["release_id"]}:
            spins[rid] = spins.get(rid, 0) + 1

    # flight: the best orbit and tier ever reached, from milestones and the present
    stage_ids = [st[1] for st in cfg["orbit"]["stages"]]
    stage_by_label = {st[2]: i for i, st in enumerate(cfg["orbit"]["stages"])}
    tier_ids = [t[1] for t in cfg["signal"]["tiers"]]
    tier_by_label = {t[2]: i for i, t in enumerate(cfg["signal"]["tiers"])}
    best_orbit = max([stage_by_label.get(v, 0) for (v,) in db.execute("SELECT value FROM pet_milestones WHERE kind IN ('orbit_up', 'hatch')")] +
                     [(state or {}).get("orbit", {}).get("index", 0)] + [0])
    best_tier = max([tier_by_label.get(v, 0) for (v,) in db.execute("SELECT value FROM pet_milestones WHERE kind = 'tier_up'")] +
                    [(state or {}).get("signal", {}).get("index", 0)] + [0])
    missions = int(pet_engine.meta_get(db, "pet_missions_done", 0) or 0)

    oi = lambda sid: stage_ids.index(sid) if sid in stage_ids else 99
    ti = lambda tid: tier_ids.index(tid) if tid in tier_ids else 99
    values = {
        "first_contact": (len(plays), 1), "centurion": (len(plays), 100), "veteran": (len(plays), 1000),
        "full_side": (full_side, 1), "flip_side": (flip_side, 1), "marathon": (round(longest / 3600, 1), 4),
        "night_owl": (int(any(h < 4 for h in hours)), 1), "early_bird": (int(any(5 <= h < 8 for h in hours)), 1),
        "week_signal": (_streak(days), 7), "genre_explorer": (len(genres), 8), "time_traveller": (len(decades), 5),
        "crate_digger": (dusty, 10), "deep_cargo": (len(by_rel), 50), "regular": (max(spins.values(), default=0), 10),
        "liftoff": (int(best_orbit >= oi("leo")), 1), "geostationary": (int(best_orbit >= oi("geo")), 1),
        "lunar": (int(best_orbit >= oi("lunar")), 1), "deep_space": (int(best_orbit >= oi("deep")), 1),
        "explorer": (int(best_tier >= ti("explorer")), 1), "voyager": (int(best_tier >= ti("voyager")), 1),
        "mission_specialist": (missions, 5),
    }
    out = []
    for aid, name, how, group in ACHIEVEMENTS:
        v, target = values[aid]
        out.append({"id": aid, "name": name, "how": how, "group": group, "value": min(v, target), "target": target,
                    "earned_at": earned.get(aid) or (None if v < target else None), "done": v >= target or aid in earned})
    return out


def record(db, cfg, state: dict | None = None, now: float | None = None, announce: bool = True) -> list:
    """Store achievements newly reached, and have Beepnik announce each one. Returns the new ones.
    On the very first run, everything already reached is stored quietly, without a flood of announcements."""
    now = now or time.time()
    ensure_schema(db)
    first_run = db.execute("SELECT COUNT(*) FROM pet_achievements").fetchone()[0] == 0 and \
        pet_engine.meta_get(db, "pet_achievements_seeded") is None
    new = [a for a in progress(db, cfg, state, now) if a["done"] and not a["earned_at"]]
    if not new:
        if first_run:
            pet_engine.meta_set(db, "pet_achievements_seeded", int(now))
        return []
    with db:
        db.executemany("INSERT OR IGNORE INTO pet_achievements (id, earned_at) VALUES (?, ?)", [(a["id"], int(now)) for a in new])
        if announce and not first_run:
            for a in new:
                line = random.choice(ANNOUNCE).format(name=a["name"], how=a["how"].lower())
                db.execute("INSERT INTO pet_log (at, kind, text, mood, source) VALUES (?, ?, ?, ?, ?)",
                           (int(now), "achievement", line, (state or {}).get("mood", ""), "canned"))
    if first_run:
        pet_engine.meta_set(db, "pet_achievements_seeded", int(now))
    return [] if first_run else new


ANNOUNCE = [
    "Achievement: {name}. You managed to {how}. I'm logging it before you get smug.",
    "Achievement: {name}. Logged with honors. Don't let it go to your head, Earthling.",
    "Achievement: {name}. Ground Control has been notified. They seemed impressed. I'm reserving judgment.",
]


# --- 4. Talking to him --------------------------------------------------------------------------------------
NEED_WORDS = {"fuel": "fuel", "variety": "variety", "curiosity": "curiosity", "taste": "taste"}


def _listish(v) -> list:
    """Genres and styles are stored as JSON lists (or, in older rows, comma-separated text)."""
    if not v:
        return []
    if isinstance(v, list):
        return [str(x).strip() for x in v if str(x).strip()]
    v = str(v).strip()
    if v.startswith("["):
        try:
            return [str(x).strip() for x in json.loads(v) if str(x).strip()]
        except ValueError:
            pass
    return [x.strip() for x in v.split(",") if x.strip()]


def _recent_plays(db, days):
    return {r[0]: r[1] for r in db.execute("SELECT release_id, MAX(started_at) FROM plays WHERE release_id IS NOT NULL GROUP BY release_id")}


def suggest(db, cfg, state: dict, now: float | None = None) -> dict | None:
    """A record from the cargo that would help his lowest need, and why."""
    now = now or time.time()
    needs = state.get("needs") or {}
    need = min(NEEDS, key=lambda n: needs.get(n, 100)) if needs else "curiosity"
    last = _recent_plays(db, 365)
    recs = [dict(zip(("release_id", "title", "artist", "year", "original_year", "genres", "styles", "date_added", "cover_file"), r))
            for r in db.execute("SELECT release_id, title, artist, year, original_year, genres, styles, date_added, cover_file "
                                "FROM releases WHERE COALESCE(in_collection, 1) = 1")]
    if not recs:
        return None
    since = lambda r: now - last.get(r["release_id"], 0)
    gen = lambda r: (_listish(r.get("genres")) or [""])[0]
    pick, why = None, ""
    if need == "curiosity":
        never = [r for r in recs if r["release_id"] not in last]
        if never:
            pick = sorted(never, key=lambda r: str(r.get("date_added") or ""))[0]
            why = "you've never played it"
        else:
            pick = max(recs, key=since)
            why = f"it's been {int(since(pick) // 86400)} days"
    elif need == "variety":
        recent = [g for r in recs if since(r) < 14 * 86400 for g in [gen(r)]]
        counts = {}
        for r in recs:
            counts.setdefault(gen(r), 0)
        for g in recent:
            counts[g] = counts.get(g, 0) + 1
        rare = min(counts, key=lambda g: (counts[g], random.random()))
        cands = [r for r in recs if gen(r) == rare] or recs
        pick = max(cands, key=since)
        why = f"there's been no {rare or 'change of genre'} lately" if counts.get(rare, 0) == 0 else f"{rare} has been rare lately"
    elif need == "taste":
        favs = [f.lower() for f in (state.get("favorites") or [])]
        cands = [r for r in recs if any(f in [x.lower() for x in _listish(r.get("genres")) + _listish(r.get("styles"))] for f in favs)
                 and since(r) > 3 * 86400] or [r for r in recs if since(r) > 3 * 86400] or recs
        pick = max(cands, key=lambda r: (last.get(r["release_id"], 0) > 0, random.random()))
        why = "it's exactly my kind of thing" if favs else "I've been missing it"
    else:  # fuel: anything long and loved
        played = [r for r in recs if r["release_id"] in last and since(r) > 2 * 86400] or recs
        pick = random.choice(played[:20])
        why = "a good long listen tops me right up"
    year = pick.get("original_year") or pick.get("year") or ""
    return {"release_id": pick["release_id"], "title": pick["title"], "artist": pick["artist"], "year": year,
            "cover": f"/covers/{pick['cover_file']}" if pick.get("cover_file") else None, "need": need, "why": why}


def facts(state: dict) -> list:
    """What's true about him right now, in plain words, for "How are you?"."""
    needs = state.get("needs") or {}
    out = [f"Mood: {state.get('mood', 'unknown')}."]
    if needs:
        lo = min(NEEDS, key=lambda n: needs.get(n, 100))
        hi = max(NEEDS, key=lambda n: needs.get(n, 0))
        out.append(f"Lowest need: {lo} at {int(needs[lo])} of 100. Highest: {hi} at {int(needs[hi])}.")
    o = state.get("orbit") or {}
    if o:
        out.append(f"Orbit: {o.get('label', '')}" + (", drifting down" if o.get("drifting") else ", holding") + ".")
    s = state.get("signal") or {}
    if s:
        out.append(f"Signal tier: {s.get('label', '')}.")
    c = state.get("craving")
    if c:
        out.append(f"Craving: {c.get('title', 'a record')} by {c.get('artist', 'someone')}.")
    return out


_last_canned = {"line": ""}


def canned_feeling(state: dict) -> str:
    """His built-in answer to "How are you?", when no language model answers: shaped by his mood, his
    lowest and highest needs, his orbit and his craving, and never the same line twice running."""
    needs = state.get("needs") or {}
    mood = (state.get("mood") or "").lower()
    md = state.get("mood_detail") or {}
    if not needs:
        return "Systems nominal. Mostly. Play something and I'll tell you more."
    lo = min(NEEDS, key=lambda n: needs.get(n, 100))
    hi = max(NEEDS, key=lambda n: needs.get(n, 0))
    lo_v, hi_v = int(needs[lo]), int(needs[hi])
    orbit = state.get("orbit") or {}
    crave = state.get("craving") or {}
    opener = {
        "ecstatic": ["Honestly? Fantastic.", "Never better, Earthling.", "I'm practically glowing."],
        "happy": ["Pretty good, actually.", "Good. Don't tell anyone I said so.", "Better than usual."],
        "content": ["Fine. Steady.", "Can't complain. Well, I could.", "Holding up."],
        "restless": ["Twitchy.", "Restless, if you must know.", "Bored out of my circuits."],
        "glum": ["Not great.", "Glum, since you ask.", "Low. Very low."],
        "sleep": ["Mmf. I was asleep.", "Half awake.", "Dozing, mostly."],
    }.get(mood, ["Hard to say.", "Ask me again after a record."])
    lows = {
        "fuel": [f"My fuel's at {lo_v}, and the planet is getting bigger in the window.", "I'm running on fumes. A long listen would top me up.",
                 "Fuel's nearly gone. One side played through would do wonders."],
        "variety": ["It's been the same frequencies for days. Surprise me.", f"Variety's down to {lo_v}. Different genre, different decade, anything.",
                    "I could hum your last three records in my sleep. Mix it up."],
        "curiosity": ["Nothing new in ages. Pull out something I haven't heard.", f"Curiosity at {lo_v}. There are records in that hold you've never played.",
                      "I've charted everything you keep spinning. Find me an unknown."],
        "taste": ["I'm missing my favourites.", f"Taste at {lo_v}. You know what I like.", "Play something I actually love, would you?"],
    }[lo]
    highs = {"fuel": "at least the tanks are full", "variety": "the mix has been lovely", "curiosity": "all the new stuff has been a treat",
             "taste": "my favourites have been well fed"}
    extras = []
    if hi_v >= 60 and hi != lo:
        good = mood in ("ecstatic", "happy", "content")
        extras.append(f"{'Especially since' if good else 'On the bright side,'} {highs[hi]}.".replace("since at least", "since"))
    if orbit.get("slipping") or orbit.get("drifting"):
        extras.append(f"I'm slipping out of {orbit.get('label', 'orbit').lower()}, by the way.")
    elif orbit.get("climbing"):
        extras.append(f"Still climbing, though. {orbit.get('label', '')} is lovely this time of year.")
    if crave.get("title"):
        extras.append(f"And I keep thinking about {crave['title']}.")
    if md.get("flavor") and md.get("mood", mood) == mood:
        extras.append(f"Mostly {md['flavor']}, if I'm honest.")
    for _ in range(12):
        parts = [random.choice(opener)]
        if lo_v < 60 or mood in ("glum", "restless"):
            parts.append(random.choice(lows))
        if extras:
            parts.append(random.choice(extras))
        line = " ".join(parts)
        if line != _last_canned["line"]:
            break
    _last_canned["line"] = line
    return line


def talk(cfg: dict, state: dict, prompt: str, fallback: str):
    """One line in his voice: his usual personality prompt, his language model, his usual clean-up.
    Falls back to a built-in line if the model is off, unreachable or out of budget, and logs why.
    Returns (line, source)."""
    import logging
    log = logging.getLogger("turntable-beepnik")
    if not cfg.get("llm", {}).get("enabled", True):
        return fallback, "canned"
    try:
        import llm as llm_mod
        import pet_service as ps
        text, truncated = llm_mod.generate(cfg["llm"], ps.system_prompt(cfg, state), prompt,
                                           temperature=cfg["llm"].get("temperature", .9),
                                           max_tokens=cfg["personality"]["max_words"] * 4)
        line, why = ps.clean(text, cfg, truncated)
        if line:
            return line, "llm"
        log.warning("Beepnik's answer wasn't usable (%s); using a built-in line", why)
    except Exception as e:
        log.warning("The language model didn't answer (%s: %s); using a built-in line", type(e).__name__, e)
    return fallback, "canned"


def log_line(db, kind: str, text: str, mood: str, source: str, now: float | None = None) -> None:
    with db:
        db.execute("INSERT INTO pet_log (at, kind, text, mood, source) VALUES (?, ?, ?, ?, ?)",
                   (int(now or time.time()), kind, text, mood or "", source))


def ask_for_record(db, cfg: dict, state: dict) -> dict:
    s = suggest(db, cfg, state)
    if not s:
        return {"line": "Your cargo hold is empty. Sync your collection and ask me again.", "suggestion": None}
    fallback = random.choice([
        f"Put on {s['title']} by {s['artist']}. My {s['need']} is low, and {s['why']}.",
        f"{s['title']}, {s['artist']}. Trust me: my {s['need']} needs it, and {s['why']}.",
    ])
    prompt = (f"Your human asked you to pick a record from their collection. You picked \"{s['title']}\" by {s['artist']}"
              + (f" ({s['year']})" if s.get("year") else "") + f". Why: your {s['need']} is low, and {s['why']}. "
              "In one or two short sentences, in character, tell them to put it on and why. Name the record.")
    line, source = talk(cfg, state, prompt, fallback)
    log_line(db, "suggestion", line, state.get("mood", ""), source)
    return {"line": line, "source": source, "suggestion": s}


def how_are_you(db, cfg: dict, state: dict) -> dict:
    f = facts(state)
    prompt = ("Your human just asked how you're doing. These are the facts about you right now:\n"
              + "\n".join(f"- {x}" for x in f)
              + "\nAnswer in one or two short sentences, in character and honest, using only these facts. No numbers unless they help.")
    line, source = talk(cfg, state, prompt, canned_feeling(state))
    log_line(db, "chat", line, state.get("mood", ""), source)
    return {"line": line, "source": source, "facts": f}
