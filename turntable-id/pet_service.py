#!/usr/bin/env python3
"""Turntable-1 companion service.

Every few seconds: recompute the pet from the play log, notice events (a new
record, a dusty one, a mood shift, a need running low, growing up), and
occasionally ask a local LLM for a one-line quip about it. Writes the result
to the database, where the read-only web app picks it up.

Usage:
  pet_service.py                 run the service
  pet_service.py --once          print the current state and exit
  pet_service.py --test-quip     generate one quip from the current state and print it
"""
import argparse
import json
import logging
import os
import random
import re
import sys
import time

try:
    import requests
except ImportError:          # the web app's environment on a hand-built install may not have it; it imports
    class _NoRequests:       # this module only for code that never makes a request there
        RequestException = OSError
        def __getattr__(self, name):
            raise RuntimeError("the requests library isn't installed")
    requests = _NoRequests()

import cargo
import ground
import insights
import lifecycle
import mind
import pet_engine
import beepnik_story
from store import DB_PATH, connect
import turntable_config
CFG = turntable_config.load()

CONFIG_PATH = os.environ.get("TURNTABLE_PET_CONFIG", turntable_config.CONFIG_FILE)   # Beepnik's sections of turntable.toml
TICK = 20

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("turntable-pet")

SCHEMA = """
CREATE TABLE IF NOT EXISTS pet_state (
    id          INTEGER PRIMARY KEY CHECK (id = 1),
    updated_at  INTEGER NOT NULL,
    state       TEXT NOT NULL             -- JSON snapshot for the web app
);
CREATE TABLE IF NOT EXISTS pet_log (
    id      INTEGER PRIMARY KEY,
    at      INTEGER NOT NULL,
    kind    TEXT NOT NULL,                -- track, never_played, dusty, favorite, need_low, mood_up, ...
    text    TEXT NOT NULL,
    mood    TEXT NOT NULL DEFAULT '',
    source  TEXT NOT NULL DEFAULT ''      -- llm or canned
);
CREATE INDEX IF NOT EXISTS pet_log_at ON pet_log(at);
CREATE TABLE IF NOT EXISTS pet_milestones (
    id      INTEGER PRIMARY KEY,
    at      INTEGER NOT NULL,
    kind    TEXT NOT NULL,                -- hatch, orbit_up, orbit_down, tier_up, trait_gained, trait_lost
    value   TEXT NOT NULL                 -- the orbit, tier or trait it concerns
);
"""

NEED_HINTS = {
    "fuel": "You're hungry: it's been too quiet, and any record at all would help.",
    "variety": "You're bored of hearing the same kinds of sounds and want a different style.",
    "curiosity": "You want a record from the cargo hold that hasn't been played in a long time.",
    "taste": "You want more of the styles you love.",
}
NEED_WORDS = {"fuel": "fuel", "variety": "variety", "curiosity": "curiosity", "taste": "taste"}

# Emoji and pictographs, for when emoji are switched off.
EMOJI = re.compile("[\U0001F000-\U0001FAFF\u2600-\u27BF\uFE0F\u200D]")


# --- Quip writing ------------------------------------------------------------------

def clip(s: str, n: int = 80) -> str:
    """Track metadata goes into the prompt as data, so keep it short and single-line."""
    s = " ".join(str(s or "").split())
    return s[:n]


def lc(label: str) -> str:
    """Lowercase only the first letter, so "Low Earth orbit" keeps its Earth."""
    return label[:1].lower() + label[1:] if label else label


def where(state: dict) -> str:
    """One sentence on where the probe is and what it carries, for the LLM."""
    o, sig = state["orbit"], state["signal"]
    if not state.get("hatched"):
        return "You are still an egg on the launch pad, waiting for enough music to launch."
    text = f"You are a {lc(sig['label'])}-class probe in {lc(o['label'])}"
    if state["traits"]:
        text += ", wearing " + " and ".join(f"{t['label']} (from all the {t['genre']})" for t in state["traits"])
    text += "."
    if o["drifting"]:
        text += f" Nobody has played a record in {round(o['idle_days'])} days and your orbit is slowly slipping."
    return text


TELEMETRY_PATH = CFG["paths"]["telemetry"]


def audio_line():
    """How the music sounds right now, from turntable-telemetry, or None."""
    try:
        with open(TELEMETRY_PATH) as f:
            d = json.load(f)
    except (OSError, ValueError):
        return None
    if time.time() - d.get("t", 0) > 5 or not d.get("music"):
        return None
    bits = []
    bpm = d.get("bpm")
    if bpm and d.get("confidence", 0) >= 0.3:
        pace = "slow" if bpm < 80 else "mid-tempo" if bpm < 110 else "upbeat" if bpm < 135 else "fast"
        bits.append(f"{pace} (about {round(bpm)} BPM)")
    lvl = d.get("level", 0)
    bits.append("quiet and mellow" if lvl < 0.35 else "moderately loud" if lvl < 0.6 else "loud")
    if d.get("bass", 0) > 0.6 and d.get("bass", 0) > d.get("treble", 0) + 0.15:
        bits.append("bass-heavy")
    elif d.get("treble", 0) > 0.6 and d.get("treble", 0) > d.get("bass", 0) + 0.15:
        bits.append("bright and trebly")
    return "How the music sounds right now: " + ", ".join(bits) + "."


def mood_words(state: dict) -> str:
    """The mood with its flavor and what that means for how it talks, e.g. "content, but restless
    (variety is low): lines lean impatient and hint at genres you've skipped"."""
    md = state.get("mood_detail") or {}
    if not md.get("flavor"):
        return state["mood"]
    need = md.get("flavor_need")
    level = "low" if md["flavor"] in ("starving", "peckish", "restless", "bored", "homesick") else "high"
    out = md["phrase"][0].lower() + md["phrase"][1:]
    if need:
        out += f" ({need} is {level})"
    if md.get("effect"):
        out += f". {md['effect'].rstrip('.')}"
    return out


def target_words(cfg: dict) -> int:
    """Ask for less than the hard limit, so lines rarely need trimming."""
    return max(8, round(cfg["personality"]["max_words"] * 0.7))


def article(noun: str) -> str:
    return ("an " if noun[:1] in "aeiou" else "a ") + noun


def system_prompt(cfg: dict, state: dict) -> str:
    p = cfg["personality"]
    rules = [
        f"You are {cfg['pet']['name']}, a small chrome satellite creature who lives inside a record player's web page and feeds on vinyl.",
        f"Personality: {p['description']}",
        f"Snark: {p['snark']}/10. Enthusiasm: {p['enthusiasm']}/10.",
        where(state) + f" You feel {mood_words(state)}.",
        f"Reply with one short line in first person: aim for {target_words(cfg)} words or fewer, "
        "and always finish your sentence.",
        "Your human's record collection is your cargo hold: call it that, never a crate.",
        "No quotation marks, no hashtags, no stage directions, no lists.",
        "Never invent facts about the music beyond what you are told.",
    ]
    if not p["emoji"]:
        rules.append("Do not use emoji.")
    if p["family_friendly"]:
        rules.append("Keep it friendly and suitable for all ages.")
    if p["catchphrases"]:
        rules.append("Now and then you may work in one of these phrases: " + ", ".join(p["catchphrases"]) + ".")
    return " ".join(rules)


def event_prompt(state: dict, ev: dict) -> str:
    now = time.time()
    focus = ev.get("focus")
    lines = []
    if focus:
        lines.append(mind.focus_line(focus, now, NEED_HINTS))
    for m in ev.get("memory") or []:
        lines.append(m)
    needs = ", ".join(f"{k} {v}" for k, v in state["needs"].items())
    lines.append(f"Your needs (0 = empty, 100 = full): {needs}.")
    m = state.get("mission")
    if m:
        lines.append(f'Your current survey mission for your human: "{clip(m["text"], 90)}" '
                     f'({m["progress"]:g} of {m["target"]:g} {m["unit"]} so far).')
    streak = (state.get("curiosity") or {}).get("streak", 0)
    if streak >= 2:
        lines.append(f"Your human is on a discovery streak: {streak} new-to-you records in a row. Curiosity is thrilled.")
    if state.get("activity") == "listening":
        sound = audio_line()
        if sound:
            lines.append(sound + " You may react to how it sounds.")
    if ev.get("recent"):
        lines.append("Say something new and don't reuse the wording of your last few lines, which were: " +
                     " / ".join(clip(r, 120) for r in ev["recent"]))
    if ev.get("options"):
        lines.append("Records in your cargo hold you could mention: " +
                     "; ".join(f'"{clip(t)}" by {clip(a)} ({why})' for t, a, why in ev["options"]) +
                     ". If you mention or suggest any record, album, song or artist, it must be one of these "
                     "(or what's playing). Never name anything else; you only know this collection.")
    if ev.get("warned"):
        lines.append(f'Your last answer named "{clip(ev["warned"])}", which is not in the cargo hold. '
                     "Don't mention it; only mention records from the list above, or none at all.")
    if state["favorites"]:
        lines.append("Styles you love: " + ", ".join(clip(f, 30) for f in state["favorites"]) + ".")
    kind = ev["kind"]
    t = ev.get("track") or {}
    if t:
        desc = f'The record player just started "{clip(t["title"])}" by {clip(t["artist"])}'
        if t.get("album"):
            desc += f' from the album "{clip(mind.speakable(t["album"]))}"'
        if t.get("tags"):
            desc += " (style: " + ", ".join(clip(x, 30) for x in t["tags"][:3]) + ")"
        lines.append(desc + ".")
        c = state.get("craving")
        if c and not c.get("savoring") and kind != "craving_start":
            lines.append(f'You were hoping for "{clip(mind.speakable(c["title"]))}" by {clip(c["artist"])} instead; you may mention it.')
    if kind == "never_played":
        lines.append("This record has never been played here before. You are thrilled to finally hear it.")
    elif kind == "dusty":
        lines.append("Nobody has played this record in a long time. It's a reunion.")
    elif kind == "favorite":
        lines.append("This is one of your favorite styles.")
    elif kind == "disliked":
        lines.append("This is a style you don't really like. React in character.")
    elif kind == "need_low":
        lines.append(f"Your {ev['need']} is low. {NEED_HINTS[ev['need']]}")
        if ev.get("craving"):
            c = ev["craving"]
            lines.append(f'You would love to hear "{clip(c["title"])}" by {clip(c["artist"])} from the cargo hold. Ask for it.')
    elif kind == "mood_up":
        lines.append(f"Your mood just improved from {ev['from']} to {ev['to']}.")
    elif kind == "mood_down":
        lines.append(f"Your mood just dropped from {ev['from']} to {ev['to']}.")
    elif kind == "hatch":
        lines.append("You just hatched and launched off the pad into low Earth orbit! It's your first day in space.")
    elif kind == "orbit_up":
        lines.append(f"All this music just boosted you up into {lc(ev['value'])}. Celebrate the view.")
    elif kind == "orbit_down":
        lines.append(f"Without records your orbit decayed and you've slipped back to {lc(ev['value'])}. "
                     "Some music would get you back up fast. React in character.")
    elif kind == "orbit_drift":
        lines.append("It's been a while without records and your orbit has started to slip a little. "
                     "Mention it in character.")
    elif kind == "tier_up":
        lines.append(f"All the different styles you've heard just earned you an upgrade: you're now a "
                     f"{lc(ev['value'])}-class probe with new equipment. Show it off.")
    elif kind == "trait_gained":
        lines.append(f"So much {ev['genre']} lately that you've grown {ev['value']}. React to your new look.")
    elif kind == "trait_lost":
        lines.append(f"Your {ev['value']} faded away now that there's less {ev['genre']}. Shrug it off.")
    elif kind == "dislike_new":
        lines.append(f"Your human keeps cutting {ev['genre']} tracks short, and you've decided you don't care for "
                     f"{ev['genre']} either. Say so, in character: if they won't sit through it, neither will you.")
    elif kind == "dislike_gone":
        lines.append(f"Nobody's been skipping {ev['genre']} lately, so you've stopped minding it. Admit it grudgingly.")
    elif kind == "greeting":
        lines.append("The turntable just woke up after a long quiet stretch. Greet your human.")
    elif kind == "craving_start":
        lines.append("This is the exact record you've been craving! You're excited, and you want to savor it "
                     "properly before you'll call the craving satisfied.")
    elif kind == "craving_expired":
        c = ev["craving"]
        lines.append(f'You wanted "{clip(c["title"])}" by {clip(c["artist"])} for hours and nobody played it, '
                     "so you've given up on it and moved on. React in character.")
        if state.get("craving"):
            n = state["craving"]
            lines.append(f'You already want something else now: "{clip(n["title"])}" by {clip(n["artist"])}.')
    elif kind == "death":
        lines.append("Your orbit has decayed and you are re-entering the atmosphere, burning up on your way down to "
                     "Point Nemo, the spacecraft graveyard in the South Pacific. This is your final transmission. Say "
                     "goodbye in character: dramatic, a little petty about the records nobody played, and secretly "
                     "fond of your human.")
    elif kind == "burn":
        lines.append(f"That side played all the way through, so you just fired your engines and climbed to "
                     f"{state['orbit']['altitude']:g} ({state['orbit']['label']}). React in character, briefly.")
    elif kind == "rebirth":
        r = ev["reborn"]
        lines.append(f"You are {r['name']}, a brand-new probe sitting on the launch pad as an egg. Your predecessor, "
                     f"{r['previous'] or 'the last probe'}, ran out of fuel and was lost in space. Introduce yourself in "
                     "character, and make it clear you expect to be kept fuelled with records.")
    elif kind == "distress":
        lines.append(f"Your orbit is decaying: with your tank too low to correct it, drag is pulling you down, and at "
                     f"this rate you re-enter in about {state['orbit']['eta_days']:g} days. Send a distress call: a record "
                     "would refuel you and stop the fall.")
    elif kind == "mayday":
        lines.append(f"MAYDAY: re-entry in under a day ({state['orbit']['eta_days']:g} days). Any record will refuel you "
                     "and stop the fall; otherwise you burn up over Point Nemo. Plead, in character.")
    elif kind == "mission_new":
        lines.append(f'You just set yourself a new survey mission for your human: "{clip(ev["mission"]["text"], 90)}". '
                     "Announce it in character, like a probe radioing home.")
    elif kind == "mission_done":
        lines.append(f'Your human just completed your survey mission: "{clip(ev["mission"]["text"], 90)}". '
                     "Report mission success in character; you're impressed, whether you admit it or not.")
    elif kind == "mission_expired":
        lines.append(f'Your survey mission ran out of time unfinished: "{clip(ev["mission"]["text"], 90)}". '
                     "Log the failure in character.")
    elif kind == "craving_met":
        c = ev["craving"]
        lines.append(f'You just finished properly savoring "{clip(c["title"])}" by {clip(c["artist"])}, '
                     "the record you were craving. You feel deeply satisfied and grateful.")
    elif kind == "idle":
        lines.append("Nothing is playing right now. Think out loud about what is on your mind.")
        if ev.get("aside"):
            d = ev["aside"]
            when = "has never been played" if not d.get("last_played") else "hasn't been played in a long time"
            lines.append(f'In passing you notice "{clip(d["title"])}" by {clip(d["artist"])} in the cargo hold; it {when}. '
                         "You may mention it briefly, but tie it back to what is on your mind.")
    if kind not in ("idle", "greeting"):
        lines.append("React to this moment; you can refer back to what's on your mind if it fits naturally.")
    lines.append("Say your one line now.")
    return " ".join(lines)


def clean(text: str, cfg: dict, truncated: bool = False):
    """Tidy an LLM answer for the transmission readout. Returns (line, None) or (None, reason).

    Multi-line answers are joined. A line over the word limit is trimmed at its last
    sentence end, or failing that at a natural pause (comma, semicolon, dash), and kept
    whole if it's only a little over. An answer cut off by the token limit is trimmed the
    same way. Only empty, tiny, blocklisted or hopelessly long answers are rejected."""
    p = cfg["personality"]
    limit = p["max_words"]
    # Reasoning models (Qwen3, DeepSeek-R1, ...) put their thinking in <think> tags first.
    text = re.sub(r"<think>.*?(</think>|$)", "", text or "", flags=re.S | re.I)
    prefix = cfg["pet"]["name"].lower() + ":"
    parts = []
    for line in text.splitlines():
        line = line.strip().strip('"\u201c\u201d*`_').strip()
        if line.lower().startswith(prefix):
            line = line[len(prefix):].strip()
        if line:
            parts.append(line)
    line = " ".join(parts)
    if not line:
        return None, "empty answer"
    if not p["emoji"]:
        line = EMOJI.sub("", line).strip()
    line = re.sub(r"https?://\S+", "", line)
    line = re.sub(r"[#<>{}\[\]]", "", line)
    line = re.sub(r"\s+", " ", line).strip().strip('"\u201c\u201d*`_ ').strip()

    words = line.split()
    if truncated or len(words) > limit:
        cut = " ".join(words[:limit])
        ends = [m.end() for m in re.finditer(r"[.!?](?=\s|$)", cut)]
        pauses = [m.start() for m in re.finditer(r"[,;:\u2014\u2013]|\s-\s", cut)]
        if ends and len(cut[:ends[-1]].split()) >= 4:
            line = cut[:ends[-1]]
        elif pauses and len(cut[:pauses[-1]].split()) >= 4:
            line = cut[:pauses[-1]].rstrip(" ,;:-\u2014\u2013") + "."
        elif not truncated and len(words) <= limit * 1.5:
            pass                     # a little over: the readout scrolls, so keep it whole
        else:
            reason = "cut off by the token limit" if truncated else f"{len(words)} words with nowhere to trim"
            return None, reason
    if len(line) < 3 or len(line.split()) < 2:
        return None, "too short"
    lower = line.lower()
    hit = next((w for w in cfg["quips"]["blocklist"] if w and w.lower() in lower), None)
    if hit:
        return None, f"contains blocklisted '{hit}'"
    return line, None


# Events that wait (up to an hour) for their turn if another line is spoken on their tick.
MILESTONE_KINDS = {"hatch", "orbit_up", "orbit_down", "tier_up", "trait_gained", "trait_lost", "dislike_new", "dislike_gone",
                   "mission_new", "mission_done", "mission_expired", "death", "rebirth", "distress", "mayday"}

DEFAULT_CANNED = {
    "craving_start": ["That's the one! Savoring every groove of {craving}.", "{craving}! Shh, I'm savoring."],
    "craving_met": ["Ahh, {craving}. Craving satisfied. Orbit secured.", "That hit the spot. Thank you for {craving}."],
    "craving_expired": ["Fine. I've given up on {craving}. Moving on.", "Nobody played {craving}. I'll want something else, then."],
    "mission_new": ["New survey mission: {mission}. Over.", "Mission control to human: {mission}."],
    "death": ["Re-entry. It's getting warm in here. Tell the turntable... I always hated the dust. Signal lost.",
              "This is {name}, burning up over the Pacific. Play something for the next one. Static."],
    "burn": ["Engines lit. Altitude up. You may continue.", "Burn complete. Climbing. That side earned it."],
    "rebirth": ["{name}, online. Egg on the pad. My predecessor starved. Let's not repeat that.",
                "New probe, new rules: feed me records. I'm {name}."],
    "distress": ["Orbit decaying. This is a distress call. Any record will do. Any.", "I'm sinking. A record, please, before I'm space junk."],
    "mayday": ["MAYDAY. MAYDAY. Re-entry imminent. Needle down or I'm gone.", "Last call for vinyl. After this, it's Point Nemo for me."],
    "mission_done": ["Mission complete: {mission}. Logged with honors.", "Survey complete. I'll admit it: nicely done."],
    "mission_expired": ["Mission expired: {mission}. Logged as a failure. Yours.", "Survey abandoned. Noted. Logged. Judged."],
    "idle_craving": ["Still dreaming about {craving}...", "Not to be dramatic, but {craving} is right there in the cargo hold."],
    "idle_dusty": ["{dusty} looks lonely in the cargo hold.", "When did anyone last spin {dusty}?"],
    "idle_memory": ["Still humming {title} from earlier.", "That {artist} record earlier was nice. Just saying."],
    "idle_need": ["My {need} could use a top-up.", "Running a little low on {need} over here."],
    "hatch": ["Liftoff! Hello, low Earth orbit!", "I'm out of the shell and into space. Beep beep!"],
    "orbit_up": ["New altitude: {stage}. The view up here!", "Climbing! Hello, {stage}."],
    "orbit_down": ["Slipped back to {stage}. A few records would boost me right up.", "Bit lower than I was. Nothing a record can't fix."],
    "orbit_drift": ["My orbit's getting a little wobbly. Just saying.", "Drifting a touch. A record would steady me."],
    "tier_up": ["New equipment! {tier} class, reporting in.", "Upgrade complete: {tier} class. Look at this hardware!"],
    "trait_gained": ["All that {genre} gave me {trait}. Fancy!", "Look at me, sporting {trait}."],
    "trait_lost": ["My {trait} faded. Easy come, easy go.", "Bye bye, {trait}."],
    "dislike_new": ["You keep skipping {genre}. Fine. I don't like it either.", "Noted: {genre} goes on my list. If you won't sit through it, neither will I.",
                    "{genre}? Not for me anymore. You've made your feelings clear."],
    "dislike_gone": ["I suppose {genre} isn't so bad after all.", "Off my list: {genre}. Don't make it weird."],
    "idle_orbit": ["Holding orbit, but it'd love a boost.", "Altitude's slipping a little. Record, please?"],
    "idle_afterglow": ["Still thinking about {craving}. Good times.", "Content. Thoroughly content."],
    "idle_craving_hint": ["Just saying, {craving} is in the cargo hold.", "Thinking about {craving}. No pressure."],
    "idle_craving_nudge": ["Still thinking about {craving}...", "{craving} would be nice. Right about now."],
    "idle_craving_plead": ["Please. {craving}. I'm asking nicely.", "It's been a while. {craving}, maybe?"],
    "idle_craving_despair": ["I've waited so long for {craving}. Never mind.", "{craving}... I'm starting to lose hope."],
    "idle_musing": ["Drifting. Waiting. Beep.", "Quiet up here. Ready when you are.", "Counting stars until the next record."],
}


def canned(cfg: dict, state: dict, ev: dict) -> str:
    kind = ev["kind"]
    keys = [kind]
    if kind == "idle":
        f = ev.get("focus") or {"kind": "musing"}
        base = {"craving": "idle_craving", "drifting": "idle_orbit", "need": "idle_need",
                "afterglow": "idle_afterglow"}.get(f["kind"], "idle_musing")
        keys = ([f"{base}_{f['stage']}"] if f.get("stage") else []) + [base, "idle_musing"]
    # Your own lines win at every level; the built-in ones only fill gaps.
    lines = (next((cfg["canned"][k] for k in keys if cfg["canned"].get(k)), None)
             or next((DEFAULT_CANNED[k] for k in keys if DEFAULT_CANNED.get(k)), None)
             or cfg["canned"].get("track") or ["Beep."])
    t = ev.get("track") or (ev.get("data") if ev.get("topic") == "memory" else None) or {}
    c = ev.get("craving") or state.get("craving") or {}
    if not c.get("title") and (ev.get("focus") or {}).get("kind") == "afterglow":
        c = {"title": ev["focus"]["subject"]}
    d = ev.get("data") if ev.get("topic") == "dusty" else {}
    values = {
        "dusty": (d or {}).get("title", "that old record"),
        "name": cfg["pet"]["name"], "title": t.get("title", "this one"), "artist": t.get("artist", "this artist"),
        "album": t.get("album") or t.get("title", "this record"),
        "need": NEED_WORDS.get(ev.get("need", "") or state.get("lowest", ""), "mood"),
        "craving": mind.speakable(c.get("title", "something new")) if c else "something new",
        "orbit": lc(state["orbit"]["label"]), "tier": state["signal"]["label"],
        "trait": ev.get("value", "a new look") if ev["kind"].startswith("trait") else "",
        "genre": ev.get("genre", "music"),
        "mission": (ev.get("mission") or state.get("mission") or {}).get("text", "the survey"),
        "stage": lc(ev.get("value", state["orbit"]["label"])) if ev["kind"].startswith("orbit") or ev["kind"] == "hatch"
                 else lc(state["orbit"]["label"]),
    }
    def fill(line):
        try:
            return line.format(**values)
        except (KeyError, IndexError, ValueError):
            return line
    # Prefer a line it hasn't said recently; failing that, the one said longest ago.
    recent = list(ev.get("recent") or [])          # newest first
    options = [fill(l) for l in lines]
    unused = [o for o in options if o not in recent]
    if unused:
        return random.choice(unused)
    return max(options, key=lambda o: recent.index(o))


def ask_llm(cfg: dict, state: dict, ev: dict):
    import llm as llm_mod
    llm = cfg["llm"]
    started = time.monotonic()
    # Room for the whole answer: about 4 tokens per allowed word, so it isn't cut mid-sentence.
    text, truncated = llm_mod.generate(llm, system_prompt(cfg, state), event_prompt(state, ev),
                                       temperature=llm["temperature"], max_tokens=cfg["personality"]["max_words"] * 4)
    log.info("LLM answered in %.1f s%s", time.monotonic() - started, " (hit the token limit)" if truncated else "")
    return text, truncated


OBITUARY_FALLBACK = [
    "{name}: came for the vinyl, stayed for the judgment. Ran out of fuel, never out of opinions.",
    "Here lies {name}. Charted {planets} planets, reached {best_orbit}, and never once approved of your needle.",
    "{name} has left the orbit. In lieu of flowers, please play a record.",
]


def obituary(cfg: dict, m: dict) -> str:
    """A corny, affectionate one-line obituary for Point Nemo, in a narrator's voice."""
    days = max(1, round((m["died"] - m["born"]) / 86400))
    facts = (f'{m["name"]}, a sarcastic, record-obsessed space probe, flew for {days} days and {m["flight_hours"]} hours '
             f'of listening, reached {m["best_orbit"]}' + (f' as a {m["best_tier"]}-class probe' if m.get("best_tier") else "")
             + (f', wore {", ".join(m["traits"])}' if m.get("traits") else "")
             + f', completed {m["missions"]} survey missions, charted {m["planets"]} new tracks and {m["systems"]} new records, '
             'and ran out of fuel waiting for a record.')
    if cfg["llm"]["enabled"]:
        llm = cfg["llm"]
        try:
            import llm as llm_mod
            raw, _ = llm_mod.generate(llm, "You write short, corny, affectionate obituaries for retired satellites, read aloud at "
                                           "Point Nemo, the spacecraft graveyard. One line, at most 25 words. Puns welcome. "
                                           "No quotation marks, no hashtags, no names other than the deceased.",
                                      f"The facts: {facts} Write its obituary line now.", temperature=0.9, max_tokens=100)
            text = re.sub(r"<think>.*?(</think>|$)", "", raw, flags=re.S | re.I)
            text = re.sub(r"\s+", " ", text).strip().strip('"\u201c\u201d*`_ ')
            if 4 <= len(text.split()) <= 40:
                return text
            log.info("Obituary unusable (%d words); using a canned one", len(text.split()))
        except (requests.RequestException, ValueError) as e:
            log.info("No LLM for the obituary (%s); using a canned one", e)
    vals = {"name": m["name"], "planets": m["planets"], "best_orbit": m["best_orbit"].lower() if m["best_orbit"] else "orbit"}
    return random.choice(OBITUARY_FALLBACK).format(**vals)


def write_quip(cfg: dict, state: dict, ev: dict, hold=None):
    """Returns (text, source). With `hold` (a cargo.Cargo), the prompt lists real records it may
    mention, and an answer naming a record that isn't in the collection is re-asked."""
    if cfg["llm"]["enabled"]:
        try:
            if hold is not None:
                ev["options"] = hold.options(state, time.time())
            for attempt in range(2):
                raw, truncated = ask_llm(cfg, state, ev)
                line, why = clean(raw, cfg, truncated)
                if line and hold is not None:
                    bad = hold.check(line, extra_ok=[cfg["pet"]["name"], *cfg["personality"]["catchphrases"]])
                    if bad:
                        line, why = None, f"named \"{bad}\", which isn't in the cargo hold"
                        ev["warned"] = bad
                if line:
                    return line, "llm"
                snippet = re.sub(r"\s+", " ", raw or "").strip()[:160]
                log.info("LLM line was unusable (attempt %d): %s. It wrote: %r", attempt + 1, why, snippet)
            log.info("Using a canned line instead")
        except requests.RequestException as e:
            log.warning("LLM unavailable (%s); using a canned line", e.__class__.__name__)
        except (ValueError, KeyError, TypeError) as e:
            log.warning("LLM sent something unexpected (%s); using a canned line", e)
    return canned(cfg, state, ev), "canned"


# --- Events ---------------------------------------------------------------------------

def mission_text(e: dict) -> str:
    m = e.get("mission")
    if isinstance(m, dict):
        m = m.get("text") or m.get("short")
    return (m or "a survey").rstrip(".")


class Companion:
    def __init__(self, db):
        self.db = db
        self.db.executescript(SCHEMA)
        beepnik_story.ensure_schema(self.db)
        self.achievements_at = 0.0  # when achievements were last checked
        self.cfg = None
        self.cfg_mtime = None
        self.prev = None            # previous snapshot
        self.prev_play = None       # id of the play we last reacted to
        self.last_quip = 0.0
        self.alerted_low = set()    # needs we've already complained about until they recover
        self.prev_release = None    # record of the play we last reacted to
        self.idle_topic = 0         # rotates what it muses about while idle
        self.pending = []           # milestones still waiting for their turn to be voiced
        self.distress = 0           # 0 fine, 1 distress call sent, 2 mayday sent
        self.cargo = cargo.Cargo(db)     # what's really in the collection, for suggestions and checks
        lifecycle.ensure_schema(db)      # Point Nemo's memorials
        row = self.db.execute("SELECT MAX(at) FROM pet_log").fetchone()
        self.last_quip = float(row[0] or 0)

    def reload_config(self) -> None:
        try:
            mtime = os.path.getmtime(CONFIG_PATH)
        except OSError:
            mtime = None
        if self.cfg is not None and mtime == self.cfg_mtime:
            return
        try:
            self.cfg = pet_engine.load_config(CONFIG_PATH)
            self.cfg_mtime = mtime
            log.info("Loaded config from %s (model %s, LLM %s)", CONFIG_PATH, __import__("llm").describe(self.cfg["llm"]),
                     "on" if self.cfg["llm"]["enabled"] else "off")
        except Exception:
            if self.cfg is None:
                self.cfg = pet_engine.load_config("/nonexistent")   # defaults
            self.cfg_mtime = mtime

    def current_play(self):
        row = self.db.execute(
            "SELECT s.state, p.id, p.title, p.artist, p.album, p.genre, p.release_id, p.started_at "
            "FROM station s LEFT JOIN plays p ON p.id = s.play_id WHERE s.id = 1").fetchone()
        if not row or row["state"] != "playing" or row["id"] is None:
            return None
        play = dict(row)
        play["tags"] = []
        if play["release_id"]:
            r = self.db.execute("SELECT title, styles, genres FROM releases WHERE release_id = ?",
                                (play["release_id"],)).fetchone()
            if r:
                play["album"] = r["title"] or play["album"]
                play["tags"] = json.loads(r["styles"] or "[]") or json.loads(r["genres"] or "[]")
        if not play["tags"] and play["genre"]:
            play["tags"] = [play["genre"]]
        return play

    def detect(self, state: dict) -> list:
        """Events since the last tick, most important first."""
        cfg, events = self.cfg, []
        prev = self.prev
        play = self.current_play()

        if state.get("craving_satisfied"):
            events.append({"kind": "craving_met", "craving": state["craving_satisfied"], "priority": True})
        if state.get("craving_expired"):
            events.append({"kind": "craving_expired", "craving": state["craving_expired"]})
        if state.get("died"):
            events.append({"kind": "death", "memorial": state["died"], "priority": True})
        if state.get("reborn"):
            events.append({"kind": "rebirth", "reborn": state["reborn"], "priority": True})
        eta = state["orbit"].get("eta_days")
        mayday = state["orbit"].get("slipping") and eta is not None and eta <= self.cfg["life"]["mayday_days"]
        level = 2 if state.get("danger") and mayday else 1 if state.get("danger") else 0
        if level > self.distress:
            events.append({"kind": "mayday" if level == 2 else "distress", "priority": True})
        self.distress = level
        if prev and state["orbit"].get("burns", 0) > prev["orbit"].get("burns", 0) and random.random() < 0.3 \
                and not state.get("died"):
            events.append({"kind": "burn"})
        me = state.get("mission_event")
        if me:
            events.append({"kind": me["kind"], "mission": me["mission"], "priority": me["kind"] == "mission_done"})

        if prev:
            if not state.get("lost") and not prev.get("lost"):     # no milestones for a probe that's down
                events.extend(self.milestones(prev, state))
        if not state.get("lost"):
            events.extend(self.dislike_changes(state))

        if prev is None:
            # First tick after startup: note what's playing, but don't react to it.
            self.prev_play = play["id"] if play else None
            self.prev_release = play["release_id"] if play else None
            self.alerted_low.update(state["low"])
            return events

        if play and play["id"] != self.prev_play:
            track = {"title": play["title"], "artist": play["artist"], "album": play["album"], "tags": play["tags"]}
            curious = {e["play_id"]: e["kind"] for e in state["curiosity_events"]}
            kind = curious.get(play["id"])
            crave = state.get("craving")
            new_record = play["release_id"] is None or play["release_id"] != self.prev_release
            if crave and play["release_id"] == crave["release_id"] and new_record:
                events.append({"kind": "craving_start", "track": track, "craving": crave, "priority": True})
            elif kind:
                events.append({"kind": kind, "track": track, "priority": kind == "never_played"})
            elif state["favorites"] and pet_engine.matches_any(play["tags"], state["favorites"]):
                events.append({"kind": "favorite", "track": track})
            elif cfg["taste"]["dislikes"] and pet_engine.matches_any(play["tags"], cfg["taste"]["dislikes"]):
                events.append({"kind": "disliked", "track": track})
            else:
                # First record after a long quiet stretch gets a greeting instead.
                last_end = self.db.execute(
                    "SELECT MAX(COALESCE(ended_at, last_seen_at)) FROM plays WHERE id != ?", (play["id"],)).fetchone()[0]
                if last_end is None or play["started_at"] - last_end > 6 * 3600:
                    events.append({"kind": "greeting", "track": track})
                elif random.random() < cfg["quips"]["track_chance"]:
                    events.append({"kind": "track", "track": track})
        if play:
            self.prev_play = play["id"]
            self.prev_release = play["release_id"]

        if prev and state["mood"] != prev["mood"]:
            bands = [b[1] for b in cfg["mood"]["bands"]]
            if state["mood"] in bands and prev["mood"] in bands:
                up = bands.index(state["mood"]) < bands.index(prev["mood"])
                events.append({"kind": "mood_up" if up else "mood_down", "from": prev["mood"], "to": state["mood"]})

        for need in state["low"]:
            if need not in self.alerted_low:
                self.alerted_low.add(need)
                events.append({"kind": "need_low", "need": need,
                               "craving": state["craving"] if state["lowest"] == need else None})
        recovered = {n for n in self.alerted_low if state["needs"][n] >= cfg["mood"]["low"] + 10}
        self.alerted_low -= recovered
        return events

    SYSTEM_TEXT = {      # (a mission arrives as its record or its text; see mission_text)
        "burn": lambda s, e: f"Burn complete: climbing, fuel now {round(s['needs']['fuel'])}.",
        "mission_new": lambda s, e: f"Mission posted: {mission_text(e)}.",
        "mission_done": lambda s, e: f"Mission complete: {mission_text(e)}.",
        "mission_expired": lambda s, e: f"Mission expired: {mission_text(e)}.",
        "distress": lambda s, e: "Distress call: re-entry within days.",
        "mayday": lambda s, e: "Mayday: re-entry within a day.",
        "death": lambda s, e: "Signal lost: the probe re-entered over Point Nemo.",
        "rebirth": lambda s, e: f"{s['name']} on the pad.",
        "hatch": lambda s, e: f"Launch: {e['value']}.",
        "orbit_up": lambda s, e: f"Orbit raised: {e['value']}.",
        "orbit_down": lambda s, e: f"Orbit lowered: {e['value']}.",
        "tier_up": lambda s, e: f"Equipment upgrade: {e['value']} online.",
        "trait_gained": lambda s, e: f"New finish: {e['value']}, from {e.get('genre', 'recent listening')}.",
        "trait_lost": lambda s, e: f"Finish faded: {e['value']}.",
        "dislike_new": lambda s, e: f"Taste updated: {e['value']} disliked (often skipped).",
        "dislike_gone": lambda s, e: f"Taste updated: {e['value']} no longer disliked.",
    }

    def stylus_lines(self, state: dict) -> None:
        """A system line the first time the stylus passes each warning level (once per stylus)."""
        s = state.get("stylus")
        if not s:
            return
        levels = sorted(int(x) for x in self.cfg.get("stylus", {}).get("warn_at", [75, 90, 100]))
        reached = max([lv for lv in levels if s["pct"] >= lv], default=0)
        seen = pet_engine.meta_get(self.db, "stylus_warned") or {}
        if seen.get("since") != s["since"]:
            seen = {"since": s["since"], "level": max([lv for lv in levels if s["pct"] >= lv], default=0)}
            pet_engine.meta_set(self.db, "stylus_warned", seen)       # a new stylus: start counting, no back-dated warnings
            return
        if reached > seen.get("level", 0):
            msg = (f"Stylus at {reached}% of its rated life: {s['hours']:.0f} of {s['rated']} hours. Time to order a replacement."
                   if reached >= 100 else f"Stylus at {reached}% of its rated life: {s['hours']:.0f} of {s['rated']} hours.")
            try:
                ground.add_system(self.db, msg, "stylus")
            except Exception as e:
                log.warning("Stylus line: %s", e)
            pet_engine.meta_set(self.db, "stylus_warned", {"since": s["since"], "level": reached})

    def system_lines(self, state: dict, events: list) -> None:
        """A status line in the terminal log for each event (whether or not Beepnik remarks on it)."""
        for e in events:
            if e.get("system_logged"):
                continue                          # a milestone carried over from an earlier tick
            f = self.SYSTEM_TEXT.get(e["kind"])
            if f:
                try:
                    ground.add_system(self.db, f(state, e), e["kind"])
                except Exception as ex:
                    log.warning("System line: %s", ex)
            e["system_logged"] = True

    def dislike_changes(self, state: dict) -> list:
        """Styles he's taken against (learned from skips), or stopped minding. Compared with the list
        he remembers, not the last tick, so each change is announced exactly once, restarts or not."""
        if not state.get("dislikes_learned"):
            return []
        known = pet_engine.meta_get(self.db, "pet_dislikes_known")
        now_d = set(state.get("dislikes") or [])
        was = set(known or [])
        evs = [{"kind": "dislike_new", "value": g, "genre": g} for g in sorted(now_d - was)] + \
              [{"kind": "dislike_gone", "value": g, "genre": g} for g in sorted(was - now_d)]
        if evs or known is None:
            pet_engine.meta_set(self.db, "pet_dislikes_known", sorted(now_d))
        if evs:
            with self.db:
                self.db.executemany("INSERT INTO pet_milestones (at, kind, value) VALUES (?, ?, ?)",
                                    [(int(time.time()), e["kind"], e["value"]) for e in evs])
        return evs

    def milestones(self, prev: dict, state: dict) -> list:
        """Orbit, equipment and trait changes since the last tick. Each is logged for the star chart."""
        evs = []
        po, o = prev["orbit"], state["orbit"]
        if o["index"] != po["index"]:
            if po["id"] == "pad" and o["index"] > po["index"]:
                evs.append({"kind": "hatch", "value": o["label"], "priority": True})
            elif o["index"] > po["index"]:
                evs.append({"kind": "orbit_up", "value": o["label"], "priority": True})
            else:
                evs.append({"kind": "orbit_down", "value": o["label"]})
        elif o["drifting"] and not po["drifting"] and state.get("hatched"):
            evs.append({"kind": "orbit_drift", "value": o["label"]})
        if state["signal"]["index"] > prev["signal"]["index"]:
            evs.append({"kind": "tier_up", "value": state["signal"]["label"], "priority": True})
        before = {t["id"]: t for t in prev["traits"]}
        now_t = {t["id"]: t for t in state["traits"]}
        for tid, t in now_t.items():
            if tid not in before:
                evs.append({"kind": "trait_gained", "value": t["label"], "genre": t["genre"]})
        for tid, t in before.items():
            if tid not in now_t:
                evs.append({"kind": "trait_lost", "value": t["label"], "genre": t["genre"]})

        if evs:
            with self.db:
                self.db.executemany("INSERT INTO pet_milestones (at, kind, value) VALUES (?, ?, ?)",
                                    [(int(time.time()), e["kind"], e["value"]) for e in evs if e["kind"] != "orbit_drift"])
        return evs

    def idle_event(self, state: dict, now: float, focus: dict):
        """While nothing plays, it thinks out loud about what's on its mind. Now and then it
        notices something else in passing (a dusty record, the last thing played) but ties it back."""
        ev = {"kind": "idle", "focus": focus}
        if focus["kind"] not in ("listening", "savoring") and random.random() < 0.2:
            d = pet_engine.pick_dusty(self.db, now)
            if d and d["title"] != focus.get("subject"):
                ev["aside"] = d
        return ev

    def tick(self) -> None:
        self.reload_config()
        self.cfg["pet"].setdefault("base_name", self.cfg["pet"]["name"])
        now = time.time()
        state = pet_engine.snapshot(self.db, self.cfg, now)
        self.cfg["pet"]["name"] = state["name"]     # "Beepnik II" and so on, in prompts and canned lines
        if not state.get("lost"):
            try:
                insights.record_mood(self.db, now, state["mood_score"], state["mood"], (state.get("mood_detail") or {}).get("flavor"))
            except Exception as e:
                log.warning("Mood history: %s", e)
            if now - self.achievements_at >= 300:          # achievements: checked every five minutes
                self.achievements_at = now
                try:
                    for a in beepnik_story.record(self.db, self.cfg, state, now):
                        log.info("Achievement: %s", a["name"])
                except Exception as e:
                    log.warning("Achievements: %s", e)
        focus = mind.update(self.db, self.cfg, state, now)
        state["thought"] = "" if state.get("lost") else mind.describe(focus, now)   # a dark probe thinks nothing
        state["panel"] = self.cfg["panel"]          # ticker speed and gap, for the web page
        if state.get("reborn"):
            self.prev = None        # a new probe: its first tick isn't a fall from the old one's orbit
            self.pending = []
            self.distress = 0
        events = self.pending + self.detect(state)
        self.system_lines(state, events)
        self.stylus_lines(state)
        # The important moments first (a final transmission can't lose its turn to a passing remark).
        events.sort(key=lambda e: 0 if e["kind"] in ("death", "rebirth") else 1 if e.get("priority") else 2)
        # Milestones wait (up to an hour) if they can't be voiced this tick.
        self.pending = [e for e in events if e["kind"] in MILESTONE_KINDS
                        and now - e.setdefault("queued_at", now) < 3600]

        quiet = pet_engine.in_quiet_hours(self.cfg, now)
        gap_ok = now - self.last_quip >= self.cfg["quips"]["min_gap_seconds"]

        idle_every = self.cfg["quips"]["idle_minutes"] * 60
        if not events and not state.get("lost") and state["activity"] == "idle" and idle_every > 0 \
                and now - self.last_quip >= idle_every:
            events.append(self.idle_event(state, now, focus))

        recent = [r[0] for r in self.db.execute("SELECT text FROM pet_log ORDER BY at DESC, id DESC LIMIT 5")]
        memory = mind.memory(self.db, state, focus, now)
        for ev in events:
            ev["recent"] = recent
            ev["focus"] = focus
            ev["memory"] = memory
        for ev in events:
            if quiet and not ev.get("priority"):
                continue
            if not gap_ok and not ev.get("priority"):
                continue
            text, source = write_quip(self.cfg, state, ev, self.cargo)
            with self.db:
                self.db.execute("INSERT INTO pet_log (at, kind, text, mood, source) VALUES (?, ?, ?, ?, ?)",
                                (int(time.time()), ev["kind"], text, state["mood"], source))
            log.info("Quip (%s, %s): %s", ev["kind"], source, text)
            if ev["kind"] == "death":
                self.write_memorial(ev["memorial"], text)
            self.last_quip = time.time()
            if ev["kind"] in ("idle", "need_low", "orbit_drift"):
                mind.mentioned(self.db)
            self.pending = [e for e in self.pending if e is not ev]
            break   # one quip per tick

        public = {k: v for k, v in state.items()
                  if k not in ("curiosity_events", "craving_satisfied", "craving_expired", "mission_event", "died", "reborn")}
        with self.db:
            self.db.execute(
                "INSERT INTO pet_state (id, updated_at, state) VALUES (1, ?, ?) "
                "ON CONFLICT(id) DO UPDATE SET updated_at = excluded.updated_at, state = excluded.state",
                (int(now), json.dumps(public)))
        self.prev = state

    def write_memorial(self, memorial: dict, last_words: str) -> None:
        """Last words and a corny one-line obituary for Point Nemo."""
        text = obituary(self.cfg, memorial)
        try:
            lifecycle.memorial_update(self.db, memorial["id"], last_words=last_words, obituary=text)
            log.info("%s was lost in space. Obituary: %s", memorial["name"], text)
        except Exception as e:
            log.warning("Couldn't write the memorial: %s", e)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--once", action="store_true", help="print the current state and exit")
    ap.add_argument("--test-quip", action="store_true", help="generate one quip and exit")
    args = ap.parse_args()

    db = connect(DB_PATH)
    pet = Companion(db)
    pet.reload_config()

    if args.once or args.test_quip:
        state = pet_engine.snapshot(db, pet.cfg)
        if args.once:
            print(json.dumps({k: v for k, v in state.items() if k != "curiosity_events"}, indent=2))
        if args.test_quip:
            play = pet.current_play()
            ev = {"kind": "track", "track": {"title": play["title"], "artist": play["artist"], "album": play["album"],
                                             "tags": play["tags"]}} if play else \
                 {"kind": "need_low", "need": state["lowest"], "craving": state["craving"]}
            print("Prompt:", event_prompt(state, ev))
            text, source = write_quip(pet.cfg, state, ev, pet.cargo)
            print(f"\n{pet.cfg['pet']['name']} ({source}): {text}")
        return

    log.info("Companion service started; config %s", CONFIG_PATH)
    ground.ensure_schema(db)
    ground.GroundControl(DB_PATH, lambda: pet.cfg).start()     # one transmission per new track
    while True:
        started = time.monotonic()
        try:
            pet.tick()
        except Exception as e:
            log.exception("Tick failed: %s", e)
        time.sleep(max(1, TICK - (time.monotonic() - started)))


if __name__ == "__main__":
    main()
