"""Lost in space: when the companion runs out of fuel, and the probe that replaces it.

  danger    fuel below `danger_below` (after it has been fuelled at all): distress calls
  death     fuel at 0, nothing playing, and it was fuelled to `alive_after` or more at some
            point in its life (so a brand-new egg can't die before you've ever played it).
            Its memorial goes to Point Nemo (pet_memorials), and it drifts, powered down.
  rebirth   when you next play a record, or after `lost_hours`, a new probe launches as an
            egg: the next generation (Beepnik II, III ...), with a fresh birthday.

Only the pet starts over. Plays, stats, the logbook and recordings are never touched; the
old probe's lines and milestones stay in the database, and the page simply shows the current
probe's from its birthday on.
"""
import json
import time

SCHEMA = """
CREATE TABLE IF NOT EXISTS pet_memorials (
    id            INTEGER PRIMARY KEY,
    generation    INTEGER NOT NULL,
    name          TEXT NOT NULL,
    born          INTEGER NOT NULL,
    died          INTEGER NOT NULL,
    flight_hours  REAL NOT NULL DEFAULT 0,
    best_orbit    TEXT NOT NULL DEFAULT '',
    best_tier     TEXT NOT NULL DEFAULT '',
    traits        TEXT NOT NULL DEFAULT '[]',     -- JSON list of traits worn in its life
    missions      INTEGER NOT NULL DEFAULT 0,
    planets       INTEGER NOT NULL DEFAULT 0,
    systems       INTEGER NOT NULL DEFAULT 0,
    records       INTEGER NOT NULL DEFAULT 0,
    last_words    TEXT NOT NULL DEFAULT '',
    obituary      TEXT NOT NULL DEFAULT ''
);
"""

# Meta keys belonging to one probe's life, cleared when the next one launches.
LIFE_KEYS = ("pet_craving", "pet_craving_rest", "pet_craving_rest_until", "pet_craving_history", "pet_satisfied",
             "pet_focus", "pet_mission", "pet_mission_rest_until", "pet_mission_history", "pet_mission_rewards",
             "pet_missions_done", "pet_peak", "pet_flight_seed")
# (pet_signal_floor is set to 0 for a new probe in reborn(), not cleared: clearing it would re-seed it)

ROMAN = [(10, "X"), (9, "IX"), (5, "V"), (4, "IV"), (1, "I")]


def roman(n: int) -> str:
    out = ""
    for v, s in ROMAN:
        while n >= v:
            out += s
            n -= v
    return out


def display_name(base: str, generation: int) -> str:
    return base if generation <= 1 else f"{base} {roman(generation)}"


def ensure_schema(db) -> None:
    with db:
        db.executescript(SCHEMA)


def generation(db, meta_get) -> int:
    return int(meta_get(db, "pet_generation", 1) or 1)


def track_peak(db, meta_get, meta_set, fuel: float, orbit: dict, signal: dict, traits: list) -> dict:
    """The best this probe has done in its life: highest fuel, furthest orbit, best equipment, traits worn."""
    peak = meta_get(db, "pet_peak", None) or {"fuel": 0, "orbit_index": -1, "orbit": "", "tier_index": -1, "tier": "",
                                                 "traits": []}
    changed = False
    if fuel > peak["fuel"]:
        peak["fuel"] = round(fuel, 1)
        changed = True
    if orbit["index"] > peak["orbit_index"]:
        peak["orbit_index"], peak["orbit"] = orbit["index"], orbit["label"]
        changed = True
    if signal["index"] > peak["tier_index"]:
        peak["tier_index"], peak["tier"] = signal["index"], signal["label"]
        changed = True
    for t in traits:
        if t["label"] not in peak["traits"]:
            peak["traits"].append(t["label"])
            changed = True
    if changed:
        meta_set(db, "pet_peak", peak)
    return peak


def life_stats(db, plays_all: list, born: int, now: float, cfg: dict, curiosity_mod) -> dict:
    """What it did in its life, for the memorial."""
    days = max(1, int((now - born) / 86400) + 2)
    events, _ = curiosity_mod.analyse(cfg, plays_all, now, days)
    mine = [e for e in events if e["t"] >= born]
    records = {p["release_id"] for p in plays_all if p["start"] >= born and p.get("release_id")}
    return {"planets": sum(1 for e in mine if e["kind"] == "track_new"),
            "systems": sum(1 for e in mine if e["kind"] == "never_played"),
            "records": len(records)}


def die(db, cfg, name: str, gen: int, born: int, now: float, orbit: dict, peak: dict, stats: dict,
        meta_get, meta_set) -> dict:
    ensure_schema(db)
    missions = int(meta_get(db, "pet_missions_done", 0) or 0)
    with db:
        cur = db.execute(
            "INSERT INTO pet_memorials (generation, name, born, died, flight_hours, best_orbit, best_tier, traits, "
            "missions, planets, systems, records) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (gen, name, born, int(now), stats.get("peak_altitude", orbit.get("altitude", 0)), peak.get("orbit") or orbit["label"],
             peak.get("tier", ""), json.dumps(peak.get("traits", [])), missions, stats["planets"], stats["systems"],
             stats["records"]))
    stats = {k: v for k, v in stats.items() if k != "peak_altitude"}
    memorial = {"id": cur.lastrowid, "generation": gen, "name": name, "born": born, "died": int(now),
                "flight_hours": stats.get("peak_altitude", orbit.get("altitude", 0)), "best_orbit": peak.get("orbit") or orbit["label"],
                "best_tier": peak.get("tier", ""), "traits": peak.get("traits", []), "missions": missions, **stats}
    meta_set(db, "pet_lost", {"at": int(now), "memorial_id": memorial["id"], "name": name})
    return memorial


def reborn(db, cfg, now: float, meta_get, meta_set) -> int:
    gen = generation(db, meta_get) + 1
    meta_set(db, "pet_generation", gen)
    meta_set(db, "pet_lost", None)
    for k in LIFE_KEYS:
        meta_set(db, k, None)
    meta_set(db, "pet_signal_floor", 0)       # a new probe earns its equipment from scratch
    meta_set(db, "pet_signal_floor_id", "beacon")
    with db:
        db.execute("INSERT INTO meta (key, value) VALUES ('pet_born', ?) "
                   "ON CONFLICT(key) DO UPDATE SET value = excluded.value", (str(int(now)),))
    return gen


def memorial_update(db, memorial_id: int, **fields) -> None:
    sets = ", ".join(f"{k} = ?" for k in fields)
    with db:
        db.execute(f"UPDATE pet_memorials SET {sets} WHERE id = ?", (*fields.values(), memorial_id))
