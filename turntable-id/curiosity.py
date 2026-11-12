"""Curiosity: what feeds it, what drains it, and the survey missions.

Fed by discoveries, small and large:
  never_played  a record from the cargo hold spun for the first time        record_new
  dusty         a record not spun for `dusty_days`                          dusty_boost
  side_new      a side of a record heard for the first time                 side_new
  track_new     a track heard for the first time (a new planet charted)     track_new
  artist_new    an artist first heard, or back after `artist_gap_days`      artist_new
  mission       a survey mission completed                                  [survey] reward

Discovery streaks: new-to-you records in a row (the same session, nothing familiar in
between) multiply their record boosts: x1, x1.5, x2 ... up to `streak_max`.

Familiarity drains it: a record played on `familiar_spins` or more of the last `familiar_days`
days takes `drain_per_minute` points for every minute it plays.

The meter halves every `half_life_days`, and is capped as each event lands, so the drop
between sessions always shows.
"""
import datetime
import random
import time

import sectors

SESSION_GAP = 30 * 60


def _day(t: float) -> int:
    return datetime.date.fromtimestamp(t).toordinal()


def _side(p: dict) -> str:
    if p.get("side"):
        return p["side"]
    pos = p.get("position") or ""
    return pos[0].upper() if pos[:1].isalpha() else ""


def analyse(cfg: dict, plays_all: list, now: float, window_days: int = 31):
    """Every curiosity event in the last `window_days`, oldest first, and the current streak.

    Works through all plays ever, in order, so "first time" means first time here."""
    c = cfg["needs"]["curiosity"]
    window = now - window_days * 86400
    last_rel, seen_track, seen_side, artist_last, rel_days = {}, set(), set(), {}, {}
    events = []
    streak, last_end, spin_rel = 0, None, object()
    artist_day = set()
    for p in plays_all:
        rid = p.get("release_id")
        inside = p["end"] >= window
        if last_end is None or p["start"] - last_end > SESSION_GAP:
            streak, spin_rel = 0, object()           # a new session
        # A new spin: a different record (or the same one after a break).
        if rid != spin_rel:
            spin_rel = rid
            kind = None
            if rid:
                prev = last_rel.get(rid)
                if prev is None:
                    kind = "never_played"
                elif p["start"] - prev >= c["dusty_days"] * 86400:
                    kind = "dusty"
            streak = streak + 1 if kind else 0
            if kind and inside:
                mult = min(c["streak_max"], 1 + c["streak_step"] * (streak - 1))
                base = c["record_new"] if kind == "never_played" else c["dusty_boost"]
                events.append({"t": p["start"], "kind": kind, "boost": base * mult, "play_id": p["id"],
                               "streak": streak, "title": p.get("album") or p.get("title") or ""})
        # Sides and tracks of records you own: new planets on the star chart.
        pos = p.get("position") or ""
        if rid and pos:
            side = _side(p)
            if side and (rid, side) not in seen_side:
                seen_side.add((rid, side))
                if inside:
                    events.append({"t": p["start"], "kind": "side_new", "boost": c["side_new"], "play_id": p["id"],
                                   "title": f'side {side} of {p.get("album") or ""}'.strip()})
            if (rid, pos) not in seen_track:
                seen_track.add((rid, pos))
                if inside:
                    events.append({"t": p["start"], "kind": "track_new", "boost": c["track_new"], "play_id": p["id"],
                                   "title": p.get("title") or ""})
        # Artists: first time, or back after a long gap (once a day each).
        a = (p.get("artist") or "").strip().lower()
        if a:
            last = artist_last.get(a)
            if inside and (last is None or p["start"] - last >= c["artist_gap_days"] * 86400) \
                    and (a, _day(p["start"])) not in artist_day:
                artist_day.add((a, _day(p["start"])))
                events.append({"t": p["start"], "kind": "artist_new", "boost": c["artist_new"], "play_id": p["id"],
                               "title": p.get("artist") or ""})
            artist_last[a] = p["end"]
        # Familiarity: a record you've played on several recent days drains it while it plays.
        if rid:
            day = _day(p["start"])
            days = rel_days.setdefault(rid, set())
            recent = [d for d in days if day - c["familiar_days"] <= d < day]
            if inside and len(recent) >= c["familiar_spins"]:
                mins = max(0, p["end"] - p["start"]) / 60
                events.append({"t": p["end"], "kind": "familiar", "boost": -c["drain_per_minute"] * mins,
                               "play_id": p["id"], "title": p.get("album") or ""})
            days.add(day)
            last_rel[rid] = max(last_rel.get(rid, 0), p["start"])
        last_end = p["end"]
    if last_end is None or now - last_end > SESSION_GAP:
        streak = 0
    events.sort(key=lambda e: e["t"])
    return events, streak


def meter(cfg: dict, events: list, now: float) -> float:
    half = max(0.1, cfg["needs"]["curiosity"]["half_life_days"]) * 86400
    level, t = 0.0, None
    # In time order, always: the level decays by the time between events, so one out of order (a
    # mission reward added after the discoveries, say) would decay everything from its old time.
    for e in sorted(events, key=lambda e: e["t"]):
        if t is not None:
            level *= 0.5 ** (max(0, e["t"] - t) / half)
        level = min(100.0, max(0.0, level + e["boost"]))
        t = e["t"]
    if t is not None:
        level *= 0.5 ** (max(0, now - t) / half)
    return max(0.0, min(100.0, level))


def charted(events: list, now: float, hours: int = 24) -> dict:
    """What was discovered recently, for the page: planets, systems, sides."""
    recent = [e for e in events if now - e["t"] <= hours * 3600]
    return {"planets": sum(1 for e in recent if e["kind"] == "track_new"),
            "systems": sum(1 for e in recent if e["kind"] == "never_played"),
            "sides": sum(1 for e in recent if e["kind"] == "side_new"),
            "dusted": sum(1 for e in recent if e["kind"] == "dusty")}


# --- survey missions ------------------------------------------------------------------------

def _minutes_where(plays_all, since, test) -> float:
    return sum(max(0, p["end"] - max(p["start"], since)) for p in plays_all if p["end"] > since and test(p)) / 60


def progress(m: dict, plays_all: list, events: list, years: dict, added: dict = None, dusty_days: float = 60) -> float:
    since = m["since"]
    if m["kind"] == "chart":
        return sum(1 for e in events if e["kind"] == "track_new" and e["t"] >= since)
    if m["kind"] == "side":
        return sum(1 for e in events if e["kind"] == "side_new" and e["t"] >= since)
    if m["kind"] == "dust":
        # Untouched: back after the dusty gap, or never played at all, as long as it has been in the
        # collection that long (a record bought yesterday and played today isn't untouched).
        def untouched(e):
            if e["kind"] == "dusty":
                return True
            if e["kind"] != "never_played":
                return False
            rid = play_release.get(e.get("play_id"))
            when = (added or {}).get(rid)
            return when is None or e["t"] - when >= dusty_days * 86400
        play_release = {p["id"]: p.get("release_id") for p in plays_all if p.get("id") is not None}
        return sum(1 for e in events if e["t"] >= since and untouched(e))
    if m["kind"] == "constellation":
        # by constellation (sectors.py); a mission set before v1.17 named a Discogs genre instead
        g = m["param"].lower()
        return _minutes_where(plays_all, since, lambda p: sectors.sector_label(p.get("genres") or [], p.get("styles") or []).lower() == g
                              or any(x.lower() == g for x in (p.get("genres") or [])))
    if m["kind"] == "era":
        y = int(m["param"])
        return _minutes_where(plays_all, since, lambda p: 0 < years.get(p.get("release_id"), 0) < y)
    return 0


def new_mission(cfg: dict, db, plays_all: list, now: float, recent_kinds: list):
    s = cfg["survey"]
    rng = random.Random()
    options = []
    options.append({"kind": "chart", "target": rng.randint(s["chart_min"], s["chart_max"]), "param": "",
                    "unit": "planets"})
    options.append({"kind": "side", "target": 1, "param": "", "unit": "side"})
    options.append({"kind": "dust", "target": 1, "param": "", "unit": "record"})
    try:
        rows = db.execute("SELECT genres, year, styles FROM releases WHERE in_collection = 1").fetchall()
    except Exception:
        rows = []
    import json
    owned_genres = {}
    yrs = []
    for genres, year, styles in rows:
        # constellations as the logbook draws them (sectors.py)
        lab = sectors.sector_label(json.loads(genres) if genres else [], json.loads(styles) if styles else [])
        if lab != sectors.SECTORS["other"]:
            owned_genres[lab] = owned_genres.get(lab, 0) + 1
        if year:
            yrs.append(int(year))
    lately = {sectors.sector_label(p.get("genres") or [], p.get("styles") or []) for p in plays_all if p["end"] >= now - 14 * 86400}
    quiet = [g for g, n in owned_genres.items() if g not in lately]
    if quiet:
        options.append({"kind": "constellation", "target": s["minutes"], "param": rng.choice(quiet), "unit": "minutes"})
    if len(yrs) >= 8:
        yrs.sort()
        cut = yrs[len(yrs) // 4]
        cut = max(1950, (cut // 5) * 5 + 5)
        options.append({"kind": "era", "target": s["minutes"], "param": str(cut), "unit": "minutes"})
    fresh = [o for o in options if o["kind"] not in recent_kinds] or options
    m = rng.choice(fresh)
    m["text"] = {
        "chart": f'Chart {m["target"]} new planets: tracks you\'ve never heard',
        "side": "Hear a side you've never heard",
        "dust": f'Spin a record untouched for {cfg["needs"]["curiosity"]["dusty_days"]}+ days',
        "constellation": f'Visit the {m["param"]} constellation: {m["target"]} minutes of it',
        "era": f'Travel back: {m["target"]} minutes of a record from before {m["param"]}',
    }[m["kind"]]
    m["short"] = {                     # fits the status lamp on the page
        "chart": f'Chart {m["target"]} new planets',
        "side": "Hear an unheard side",
        "dust": "Dust off a record",
        "constellation": f'Visit {m["param"]}',
        "era": f'Travel back before {m["param"]}',
    }[m["kind"]]
    m["since"] = int(now)
    m["expires_at"] = int(now + s["expire_hours"] * 3600)
    m["progress"] = 0
    return m


def manage_mission(db, cfg: dict, plays_all: list, events: list, now: float, meta_get, meta_set):
    """The current mission (or None) and an event: ("mission_new" | "mission_done" | "mission_expired", mission)."""
    s = cfg["survey"]
    if not s["enabled"]:
        return None, None
    m = meta_get(db, "pet_mission")
    rest_until = meta_get(db, "pet_mission_rest_until", 0) or 0
    history = meta_get(db, "pet_mission_history", []) or []
    years, added = {}, {}
    try:
        for rid, year, date_added in db.execute("SELECT release_id, year, date_added FROM releases"):
            years[rid] = int(year or 0)
            try:
                added[rid] = datetime.datetime.fromisoformat((date_added or "").replace("Z", "+00:00")).timestamp()
            except ValueError:
                pass                                   # no date: treated as long owned
    except Exception:
        pass
    event = None
    if m:
        m["progress"] = round(min(progress(m, plays_all, events, years, added, cfg["needs"]["curiosity"]["dusty_days"]), m["target"]), 1)
        if m["progress"] >= m["target"]:
            rewards = (meta_get(db, "pet_mission_rewards", []) or [])[-19:] + [[int(now), s["reward"]]]
            meta_set(db, "pet_mission_rewards", rewards)
            meta_set(db, "pet_missions_done", int(meta_get(db, "pet_missions_done", 0) or 0) + 1)   # for the memorial
            event = ("mission_done", m)
        elif now > m["expires_at"]:
            event = ("mission_expired", m)
        if event:
            rest_until = int(now + s["cooldown_hours"] * 3600)
            meta_set(db, "pet_mission", None)
            meta_set(db, "pet_mission_rest_until", rest_until)
            meta_set(db, "pet_mission_history", (history + [m["kind"]])[-5:])
            m = None
        else:
            meta_set(db, "pet_mission", m)
    elif now >= rest_until:
        m = new_mission(cfg, db, plays_all, now, history[-2:])
        meta_set(db, "pet_mission", m)
        event = ("mission_new", m)
    rest = max(0, int(rest_until - now)) if m is None else 0
    return m, rest, event


def reward_events(db, meta_get, days: int = 31) -> list:
    since = time.time() - days * 86400
    return [{"t": t, "kind": "mission", "boost": b, "play_id": None, "title": "survey mission"}
            for t, b in (meta_get(db, "pet_mission_rewards", []) or []) if t >= since]
