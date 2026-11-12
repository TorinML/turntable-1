"""The detail behind the gauges: what fed Curiosity, what Taste loves and whether it's getting it,
and what's pulling on the mood (plus an hourly mood history for the week's sparkline).

Nothing here changes a need's value; it explains it.
"""
import json
import time

DAY = 86400

# --- Curiosity ------------------------------------------------------------------------------

def curiosity_detail(discoveries: list, now: float, days: int = 3) -> dict:
    """The last few days of discoveries, with the points each gave, and what drained it.
    New tracks and artists are rolled up per day; drains are rolled up per record."""
    since = now - days * DAY
    recent = [e for e in discoveries if e["t"] >= since]
    rows, drains = [], {}
    tracks, artists, sides = {}, {}, {}
    for e in recent:
        k = e["kind"]
        if k == "familiar":
            d = drains.setdefault(e.get("title") or "a familiar record", {"title": e.get("title") or "a familiar record", "points": 0.0})
            d["points"] += e["boost"]
        elif k == "track_new":
            day = int(e["t"] // DAY)
            tracks.setdefault(day, [0, 0.0, e["t"]])
            tracks[day][0] += 1
            tracks[day][1] += e["boost"]
        elif k == "artist_new":
            artists.setdefault(int(e["t"] // DAY), []).append(e)
        elif k == "side_new":
            sides.setdefault(int(e["t"] // DAY), []).append(e)
        else:
            text = {"never_played": f"{e['title']}: a record you'd never played",
                    "dusty": f"{e['title']}: back after a long while",
                    "side_new": f"{e['title'][:1].upper()}{e['title'][1:]}",
                    "mission": "A survey mission completed"}.get(k, e.get("title", k))
            if k in ("never_played", "dusty") and e.get("streak", 1) > 1:
                text += f" (streak x{min(2.5, 1 + 0.5 * (e['streak'] - 1)):g})"
            rows.append({"kind": k, "text": text, "points": round(e["boost"]), "t": int(e["t"])})
    for day, (n, pts, t) in tracks.items():
        rows.append({"kind": "track_new", "text": f"{n} new {'track' if n == 1 else 'tracks'} (planets) charted",
                     "points": round(pts), "t": int(t)})
    for day, evs in sides.items():
        text = evs[0]["title"][:1].upper() + evs[0]["title"][1:] if len(evs) == 1 else f"{len(evs)} sides heard for the first time"
        rows.append({"kind": "side_new", "text": text, "points": round(sum(e["boost"] for e in evs)), "t": int(evs[-1]["t"])})
    for day, evs in artists.items():
        names = [e["title"] for e in evs if e.get("title")]
        text = (f"{names[0]}, new here" if len(names) == 1 else f"{len(names)} artists new here or back after a while")
        rows.append({"kind": "artist_new", "text": text, "points": round(sum(e["boost"] for e in evs)), "t": int(evs[-1]["t"])})
    # the biggest discoveries first: records, then missions, tracks, sides and artists; newest first within each
    order = {"never_played": 0, "dusty": 0, "mission": 1, "track_new": 2, "side_new": 3, "artist_new": 4}
    rows.sort(key=lambda r: (order.get(r["kind"], 5), -r["t"]))
    today = [e for e in discoveries if e["t"] >= now - DAY]
    return {
        "rows": rows[:8],
        "drains": sorted(({"title": d["title"], "points": round(d["points"])} for d in drains.values() if d["points"] <= -1),
                         key=lambda d: d["points"])[:3],
        "today": {"tracks": sum(1 for e in today if e["kind"] == "track_new"),
                  "sides": sum(1 for e in today if e["kind"] == "side_new"),
                  "records": sum(1 for e in today if e["kind"] in ("never_played", "dusty"))},
    }


# --- Taste ------------------------------------------------------------------------------------

def taste_detail(db, cfg, plays: list, favs: list, learned: bool, now: float, tags_of, matches_any) -> dict:
    """Each favorite's share of the last few days' listening, any dislikes, and a record from the
    cargo hold that would please it (a favorite style it hasn't heard in a while)."""
    half = max(0.1, cfg["needs"]["taste"].get("half_life_days", 2)) * DAY
    total, by = 0.0, {f: 0.0 for f in favs}
    for p in plays:
        if p["end"] < now - 14 * DAY:
            continue
        w = max(0, p["end"] - p["start"]) * 0.5 ** (max(0, now - p["end"]) / half)
        total += w
        tags = tags_of(p)
        for f in favs:
            if matches_any(tags, [f]):
                by[f] += w
    shares = [{"name": f, "share": round(by[f] / total, 3) if total else 0.0} for f in favs]
    fed = sum(s["share"] for s in shares)
    suggestion = None
    if favs:
        try:
            rows = db.execute(
                "SELECT r.release_id, r.title, r.artist, r.styles, r.genres, MAX(p.started_at) AS last "
                "FROM releases r LEFT JOIN plays p ON p.release_id = r.release_id WHERE r.in_collection = 1 "
                "GROUP BY r.release_id").fetchall()
            best = None
            for rid, title, artist, styles, genres, last in rows:
                tags = (json.loads(styles) if styles else []) + (json.loads(genres) if genres else [])
                hits = [f for f in favs if matches_any(tags, [f])]
                if not hits or (last and now - last < 7 * DAY):
                    continue
                key = (len(hits), -(last or 0))         # more of its favorites, then the longest unplayed
                if best is None or key > best[0]:
                    best = (key, {"release_id": rid, "title": title, "artist": artist, "styles": hits[:2],
                                  "last_played": last})
            suggestion = best[1] if best else None
        except Exception:
            suggestion = None
    return {"favorites": shares, "fed": round(fed, 3), "learned": learned,
            "dislikes": list(cfg["taste"].get("dislikes") or []), "suggestion": suggestion}


# --- Mood ------------------------------------------------------------------------------------

FLAVORS = {   # need: (very low, low, high)
    "fuel": ("starving", "peckish", "well fed"),
    "variety": ("restless", "restless", "worldly"),
    "curiosity": ("bored", "bored", "starstruck"),
    "taste": ("homesick", "homesick", "smitten"),
}
EFFECTS = {
    "starving": "Lines get pleading: any record at all would do.",
    "peckish": "Lines hint that it's been a while since anything played.",
    "restless": "Lines lean impatient and hint at genres you've skipped; cravings lean toward something different.",
    "bored": "Lines push the dusty and never-played records; cravings lean that way too.",
    "homesick": "Lines pine for its favorite styles; cravings lean toward them.",
    "well fed": "Generous and chatty.",
    "worldly": "Shows off its range.",
    "starstruck": "Giddy about recent discoveries.",
    "smitten": "Gushes about its favorites.",
}
POSITIVE_BANDS = ("ecstatic", "happy", "content")


def mood_detail(cfg, needs: dict, score: float, mood: str, glow: float) -> dict:
    """Each need's pull on the mood (its weighted distance from a neutral 50; the pulls plus 50
    make the score, before any afterglow), and a flavor from the need that stands out most."""
    weights = cfg["mood"]["weights"]
    wsum = sum(max(0.0, float(weights.get(n, 0))) for n in needs) or 1.0
    pulls = [{"need": n, "value": round(v), "pull": round(max(0.0, float(weights.get(n, 0))) / wsum * (v - 50), 1)}
             for n, v in needs.items()]
    pulls.sort(key=lambda p: p["pull"])
    low = cfg["mood"].get("low", 25)
    # the flavor: the need furthest from comfortable, low first (a need in trouble matters more)
    flavor, flavor_need = None, None
    lows = sorted((v, n) for n, v in needs.items() if v < max(35, low + 10) and n in FLAVORS)
    highs = sorted(((v, n) for n, v in needs.items() if v >= 80 and n in FLAVORS), reverse=True)
    if lows:
        v, flavor_need = lows[0]
        flavor = FLAVORS[flavor_need][0 if v < 15 else 1]
    elif highs:
        v, flavor_need = highs[0]
        flavor = FLAVORS[flavor_need][2]
    joiner = ""
    if flavor:
        is_low = bool(lows)
        joiner = "but" if (mood in POSITIVE_BANDS and is_low) or (mood not in POSITIVE_BANDS and not is_low) else "and"
    return {"pulls": pulls, "flavor": flavor, "flavor_need": flavor_need, "joiner": joiner,
            "phrase": mood.capitalize() + ((", but " if joiner == "but" else " and ") + flavor if flavor else ""),
            "effect": EFFECTS.get(flavor, ""), "afterglow": round(glow, 1)}


# --- Mood history -----------------------------------------------------------------------------

HISTORY_SQL = """
CREATE TABLE IF NOT EXISTS pet_mood_history (
    at     INTEGER PRIMARY KEY,
    score  INTEGER NOT NULL,
    mood   TEXT NOT NULL,
    flavor TEXT
);
"""


def record_mood(db, now: float, score: float, mood: str, flavor, every: int = 3600, keep_days: int = 30) -> None:
    """A sample an hour (the sparkline's points), kept for a month."""
    with db:
        db.executescript(HISTORY_SQL)
        last = db.execute("SELECT MAX(at) FROM pet_mood_history").fetchone()[0]
        if last and now - last < every:
            return
        db.execute("INSERT OR REPLACE INTO pet_mood_history (at, score, mood, flavor) VALUES (?, ?, ?, ?)",
                   (int(now), int(round(score)), mood, flavor))
        db.execute("DELETE FROM pet_mood_history WHERE at < ?", (int(now - keep_days * DAY),))


def mood_history(db, now: float, days: int = 7) -> list:
    try:
        return [[r[0], r[1]] for r in db.execute("SELECT at, score FROM pet_mood_history WHERE at >= ? ORDER BY at",
                                                 (int(now - days * DAY),))]
    except Exception:
        return []
