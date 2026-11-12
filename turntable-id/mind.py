"""The companion's mind: one thing on its mind at a time, and a short memory.

Instead of picking idle topics at random, the pet holds a single preoccupation, chosen
by priority, and its lines stay on it until it's resolved:

  savoring    the record it craved is playing
  listening   some other record is playing
  craving     it wants a particular record (escalates the longer it waits)
  afterglow   it recently savored a craving and is still content
  drifting    no records for days, and its orbit is slipping
  need        a need is low while it isn't craving anything
  musing      nothing in particular

Each preoccupation remembers when it started and how often the pet has mentioned it,
so lines can build on each other ("Still no Kind of Blue...") instead of repeating.
The memory gives every prompt a few lines of recent context: what's been played
lately, how past cravings ended, and recent milestones.
"""
import json
import re
import time

STAGES = ["hint", "nudge", "plead", "despair"]

STAGE_TEXT = {
    "hint": "You've only just started wanting it. Drop a light hint.",
    "nudge": "You've wanted it for a while now. Nudge a bit more pointedly.",
    "plead": "You've been waiting a long time. Make it clear how much you want it.",
    "despair": "You've nearly given up hope. Be theatrical about how long you've waited.",
}


def meta_get(db, key, default=None):
    row = db.execute("SELECT value FROM meta WHERE key = ?", (key,)).fetchone()
    try:
        return json.loads(row[0]) if row else default
    except (TypeError, ValueError):
        return default


def meta_set(db, key, value) -> None:
    with db:
        if value is None:
            db.execute("DELETE FROM meta WHERE key = ?", (key,))
        else:
            db.execute("INSERT INTO meta (key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                       (key, json.dumps(value)))


EDITION = re.compile(r"\s*[\(\[][^\)\]]*\b(edition|remaster(ed)?|deluxe|anniversary|expanded|version|mono|stereo)\b[^\)\]]*[\)\]]", re.I)


def speakable(title: str) -> str:
    """How a person would say a title: without "(Deluxe Edition)", "(Remastered)" and the like."""
    return EDITION.sub("", title or "").strip() or (title or "")


def ago(seconds: float) -> str:
    h = seconds / 3600
    if h < 1:
        m = max(1, round(seconds / 60))
        return f"{m} minute{'s' if m != 1 else ''}"
    if h < 36:
        return f"{round(h)} hour{'s' if round(h) != 1 else ''}"
    return f"{round(h / 24)} days"


# --- what's on its mind -------------------------------------------------------------

def choose(db, cfg: dict, state: dict, now: float) -> dict:
    """The highest-priority preoccupation right now (not yet persisted)."""
    crave = state.get("craving")
    play = None
    if state["activity"] == "listening":
        row = db.execute("SELECT p.release_id, p.title, p.artist, r.title AS album FROM station s "
                         "JOIN plays p ON p.id = s.play_id LEFT JOIN releases r ON r.release_id = p.release_id "
                         "WHERE s.id = 1 AND s.state = 'playing'").fetchone()
        play = dict(row) if row else None

    if crave and crave.get("savoring"):
        return {"kind": "savoring", "subject": speakable(crave["title"]), "artist": crave["artist"], "key": f"r{crave['release_id']}"}
    if play:
        album = speakable(play["album"] or play["title"])
        return {"kind": "listening", "subject": album, "artist": play["artist"],
                "key": f"r{play['release_id']}" if play["release_id"] else f"t{album}"}
    if crave:
        waited = now - crave["since"]
        patience = max(1.0, crave["expires_at"] - crave["since"])
        return {"kind": "craving", "subject": speakable(crave["title"]), "artist": crave["artist"], "reason": crave.get("reason", ""),
                "key": f"r{crave['release_id']}", "waited": waited, "patience_used": waited / patience}
    hist = meta_get(db, "pet_craving_history", [])
    if hist and hist[-1]["outcome"] == "satisfied" and now - hist[-1]["at"] < 2 * 3600:
        return {"kind": "afterglow", "subject": speakable(hist[-1]["title"]), "artist": hist[-1]["artist"], "key": f"a{hist[-1]['at']}"}
    if state["orbit"]["drifting"] and state.get("hatched"):
        return {"kind": "drifting", "subject": state["orbit"]["label"], "key": "drift",
                "idle_days": state["orbit"]["idle_days"], "lost": state["orbit"]["lost"]}
    lowest = state["lowest"]
    if state["needs"][lowest] < cfg["mood"]["low"]:
        return {"kind": "need", "subject": lowest, "key": f"n{lowest}"}
    return {"kind": "musing", "subject": "", "key": "musing"}


def update(db, cfg: dict, state: dict, now: float) -> dict:
    """Keep the preoccupation, or switch to a new one (resetting its mention count)."""
    new = choose(db, cfg, state, now)
    old = meta_get(db, "pet_focus") or {}
    if old.get("key") == new["key"]:
        focus = dict(new, since=old["since"], mentions=old.get("mentions", 0))
    else:
        focus = dict(new, since=int(now), mentions=0)
    if focus["kind"] == "craving":
        # Escalate with time waited, and a step further once it has asked a few times already.
        # (clamped: after a reboot the Pi's clock can briefly run behind a craving's start time)
        i = max(0, min(3, int(focus["patience_used"] * 4)))
        if focus["mentions"] >= 4:
            i = min(3, i + 1)
        focus["stage"] = STAGES[i]
    meta_set(db, "pet_focus", {k: focus[k] for k in ("kind", "subject", "key", "since", "mentions")})
    return focus


def mentioned(db) -> None:
    """The pet just spoke about what's on its mind."""
    f = meta_get(db, "pet_focus")
    if f:
        f["mentions"] = f.get("mentions", 0) + 1
        meta_set(db, "pet_focus", f)


def describe(focus: dict, now: float) -> str:
    """A short phrase for the web page: what it's thinking about."""
    k, s = focus["kind"], focus.get("subject", "")
    if k == "savoring":
        return f"savoring {s}"
    if k == "listening":
        return f"listening to {s}"
    if k == "craving":
        return f"wanting {s} ({ago(focus.get('waited', now - focus['since']))} so far)"
    if k == "afterglow":
        return f"still glowing after {s}"
    if k == "drifting":
        return "its slipping orbit"
    if k == "need":
        return f"its low {s}"
    return "nothing in particular"


def focus_line(focus: dict, now: float, need_hints: dict) -> str:
    """One sentence for the prompt: what is on its mind."""
    k, s, a = focus["kind"], focus.get("subject", ""), focus.get("artist", "")
    if k == "savoring":
        return f'What is on your mind: you are savoring "{s}" by {a}, the record you were craving.'
    if k == "listening":
        return f'What is on your mind: the record playing right now, "{s}" by {a}.'
    if k == "craving":
        why = f" ({focus['reason']})" if focus.get("reason") else ""
        return (f'What is on your mind: you want to hear "{s}" by {a} from the cargo hold{why}, and you have wanted it for '
                f"{ago(focus.get('waited', now - focus['since']))}. {STAGE_TEXT[focus.get('stage', 'hint')]}")
    if k == "afterglow":
        return f'What is on your mind: you are still content after finally savoring "{s}" by {a}.'
    if k == "drifting":
        return (f"What is on your mind: nobody has played a record in {round(focus.get('idle_days', 0))} days and your "
                f"orbit is slipping ({focus.get('lost', 0)} hours of altitude lost so far).")
    if k == "need":
        return f"What is on your mind: your {s} is running low. {need_hints.get(s, '')}"
    return "Nothing in particular is on your mind; you're just drifting, waiting for the next record."


# --- short-term memory ------------------------------------------------------------------

def recent_spins(db, since: float) -> list:
    """Records played since a moment, as spins: title, artist, tracks heard and tracklist size."""
    rows = db.execute(
        "SELECT p.release_id, p.started_at, COALESCE(p.ended_at, p.last_seen_at) AS e, p.track_position, "
        "COALESCE(r.title, p.album, p.title) AS title, COALESCE(r.artist, p.artist) AS artist "
        "FROM plays p LEFT JOIN releases r ON r.release_id = p.release_id "
        "WHERE p.started_at >= ? ORDER BY p.started_at", (since,)).fetchall()
    spins, cur = [], None
    for r in rows:
        key = r["release_id"] or r["title"]
        if cur and cur["key"] == key and r["started_at"] - cur["end"] <= 1800:
            cur["end"] = max(cur["end"], r["e"])
            cur["positions"].add(r["track_position"])
        else:
            cur = {"key": key, "release_id": r["release_id"], "title": r["title"], "artist": r["artist"],
                   "start": r["started_at"], "end": r["e"], "positions": {r["track_position"]}}
            spins.append(cur)
    for sp in spins:
        total = db.execute("SELECT COUNT(*) FROM tracks WHERE release_id = ?", (sp["release_id"],)).fetchone()[0] \
            if sp["release_id"] else 0
        heard = len([p for p in sp["positions"] if p])
        sp["how"] = ("the whole record" if total and heard >= 0.8 * total
                     else f"side {sorted(p[0] for p in sp['positions'] if p)[0]}" if total and heard >= 0.35 * total
                     else "a few tracks")
    return spins


def memory(db, state: dict, focus: dict, now: float) -> list:
    """A few lines of recent context for the prompt."""
    out = []
    spins = recent_spins(db, now - 12 * 3600)
    if spins:
        listed = "; ".join(f'"{speakable(s["title"])}" by {s["artist"]} ({s["how"]})' for s in spins[-4:])
        out.append(f"Played in the last 12 hours: {listed}.")
    else:
        last = db.execute("SELECT COALESCE(ended_at, last_seen_at), COALESCE(album, title), artist FROM plays "
                          "ORDER BY started_at DESC LIMIT 1").fetchone()
        if last:
            out.append(f'Nothing has been played for {ago(now - last[0])}. The last record was "{last[1]}" by {last[2]}.')

    hist = meta_get(db, "pet_craving_history", [])
    week = [h for h in hist if now - h["at"] < 7 * 86400]
    if week:
        gave_up = {}
        for h in week:
            if h["outcome"] == "expired":
                gave_up[speakable(h["title"])] = gave_up.get(speakable(h["title"]), 0) + 1
        sat = [h for h in week if h["outcome"] == "satisfied"]
        bits = []
        if sat:
            bits.append(f'last satisfied craving: "{speakable(sat[-1]["title"])}", {ago(now - sat[-1]["at"])} ago')
        if gave_up:
            worst = max(gave_up.items(), key=lambda kv: kv[1])
            bits.append(f'you gave up waiting for "{worst[0]}"' + (f" {worst[1]} times" if worst[1] > 1 else ""))
        out.append("This week: " + "; ".join(bits) + ".")

    ms = db.execute("SELECT kind, value, at FROM pet_milestones WHERE at > ? ORDER BY at DESC LIMIT 2",
                    (max(now - 86400, state.get("born", 0)),)).fetchall() if db.execute(
        "SELECT 1 FROM sqlite_master WHERE name = 'pet_milestones'").fetchone() else []
    words = {"hatch": "you hatched", "orbit_up": "you climbed to {}", "orbit_down": "you slipped to {}",
             "tier_up": "you were upgraded to {} class", "trait_gained": "you grew {}", "trait_lost": "your {} faded"}
    if ms:
        out.append("Recently: " + "; ".join(words.get(m[0], m[0]).format(m[1]) + f" ({ago(now - m[2])} ago)" for m in ms) + ".")

    if focus["kind"] in ("craving", "drifting", "need") and focus.get("mentions"):
        out.append(f"You've already brought this up {focus['mentions']} time{'s' if focus['mentions'] != 1 else ''}, "
                   "so build on it rather than repeating yourself.")
    return out
