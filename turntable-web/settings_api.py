"""Turntable-1's settings page backend: the admin sign-in, and reading and saving every setting.

  GET  /api/admin/session          {configured, signed_in}
  POST /api/admin/setup            create the admin password (only while none is set)
  POST /api/admin/login            sign in; POST /api/admin/logout signs out
  POST /api/admin/password         change the password (signed in)
  GET  /api/settings               every setting: its value, default, type, limits, help and when it applies
  POST /api/settings               save changes {values: {"section.key": value}, secrets: {"section.key": value}}

Settings are saved to turntable.toml as just the differences from the defaults; secrets go to
secrets.toml and are never sent back, only reported as set or not.
"""
import base64
import copy
import hashlib
import hmac
import json
import os
import re
import secrets as pysecrets
import time

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse

import turntable_config as tc

router = APIRouter()
COOKIE = "tt_admin"
SESSION_DAYS = 30
_fails = {}                                   # address -> [failures, locked until]

# --- what each setting is ----------------------------------------------------------------------------
SECTIONS = [
    # (section id, title, when it applies, description)
    ("station", "Station", "restart", "The turntable's audio and the stream."),
    ("identify", "Identification", "restart", "How tracks are recognized."),
    ("discogs", "Discogs", "restart", "Your collection, synced nightly."),
    ("recording", "Recording", "restart", "Session recordings, for playback in the Flight log."),
    ("archive", "Album archive", "restart", "Albums saved track by track from the recordings."),
    ("web", "Web and sign-in", "restart", "The site, reverse proxies and editing."),
    ("cast", "Google Cast", "live", "The Cast button, if you've registered a receiver."),
    ("home_assistant", "Home Assistant", "restart", "Send each track to Home Assistant."),
    ("pet", "Beepnik", "live", "Your companion: name and personality."),
    ("personality", "Personality", "live", ""),
    ("quips", "Chatter", "live", "When and how often Beepnik speaks."),
    ("llm", "Language model", "live", "What writes Beepnik's lines and Ground Control's: a local Ollama server, or a free online "
                                      "service. Gemini (aistudio.google.com) is free without a card; Groq is free and very fast. "
                                      "With an online service, what's playing and Beepnik's state are sent to it; Ollama keeps "
                                      "everything at home. Without either, Beepnik uses built-in lines."),
    ("needs", "Needs", "live", "How fuel, variety, curiosity and taste rise and fall."),
    ("orbit", "Orbit", "live", "Burns, holding orbit and falling."),
    ("signal", "Signal", "live", "Contact days and equipment tiers."),
    ("stylus", "Stylus", "live", "Wear tracking for your stylus."),
    ("craving", "Cravings", "live", ""), ("mood", "Mood", "live", ""), ("taste", "Taste", "live", ""),
    ("life", "Life and loss", "live", ""), ("survey", "Survey missions", "live", ""), ("ground", "Ground Control", "live", ""),
    ("traits", "Traits", "live", ""), ("panel", "Session player", "live", ""),
    ("canned", "Fallback lines", "live", "Lines used when the language model is off or unreachable."),
]
HIDDEN = {"paths", "device"}                  # paths are up to the install (Docker volumes); device is retired
OBSOLETE = {"device"}
HIDDEN_PATHS = {"panel.collapse_idle_minutes"}   # retired settings, kept in old files but no longer shown                         # sections no longer used: dropped from the file on the next save

META = {
    "station.name": ("Station name", "Shown on the stream and the page.", {}),
    "station.device": ("Audio device", "The ALSA capture device. A USB turntable is often plughw:CARD=CODEC,DEV=0.", {}),
    "station.sample_rate": ("Sample rate", "", {"choices": [44100, 48000]}),
    "station.channels": ("Channels", "", {"choices": [1, 2]}),
    "station.bitrate": ("Stream bitrate (kbps)", "", {"choices": [128, 192, 256, 320]}),
    "station.icecast_url": ("Icecast address", "Where the stream server is, from inside the station.", {"advanced": True}),
    "station.mount": ("Stream mount", "", {"advanced": True}),
    "identify.interval": ("Seconds between checks", "Keep 20 or more: the identification service limits how often it's asked.", {"min": 20, "max": 120}),
    "identify.sample_seconds": ("Sample length (seconds)", "", {"min": 4, "max": 15}),
    "identify.silence_rms": ("Silence threshold", "Below this level a check counts as silence.", {"min": 0, "max": 5000}),
    "identify.musicbrainz_contact": ("Contact for MusicBrainz", "An email or URL; MusicBrainz asks that lookups include one.", {}),
    "discogs.user": ("Discogs username", "", {}),
    "discogs.sync_time": ("Nightly sync time", "24-hour time, local.", {"pattern": r"^([01]\d|2[0-3]):[0-5]\d$"}),
    "web.public_url": ("Public address", "Your site's address from outside, e.g. https://turntable.example.com.", {}),
    "web.trusted_proxies": ("Trusted reverse proxies", "Addresses whose X-Forwarded headers are believed.", {}),
    "web.require_sign_in_to_edit": ("Require sign-in to edit", "Tagging and fixing plays need the admin sign-in.", {}),
    "recording.keep_sessions": ("Sessions to keep", "", {"min": 1, "max": 200}),
    "recording.min_free_gb": ("Minimum free space (GB)", "Recording pauses below this.", {"min": 0}),
    "archive.library": ("Archive folder", "Where albums are filed (mount your NAS here).", {"advanced": True}),
    "cast.app_id": ("Cast app ID", "From the Google Cast developer console.", {"pattern": r"^([A-Za-z0-9]{6,12})?$"}),
    "home_assistant.enabled": ("Send tracks to Home Assistant", "The webhook address is set below, as a secret.", {}),
    "pet.name": ("Name", "", {}),
    "quips.quiet_start": ("Quiet hours start", "", {"pattern": r"^([01]\d|2[0-3]):[0-5]\d$"}),
    "quips.quiet_end": ("Quiet hours end", "", {"pattern": r"^([01]\d|2[0-3]):[0-5]\d$"}),
    "quips.idle_minutes": ("Minutes between lines in silence", "", {"min": 1}),
    "llm.provider": ("Provider", "ollama: your own server. gemini, groq, openrouter: free online tiers (set the API key below). "
                                 "openai: any other OpenAI-compatible service, at the API address.",
                     {"choices": ["ollama", "gemini", "groq", "openrouter", "openai"]}),
    "llm.url": ("Ollama address", "For the ollama provider, e.g. http://192.168.0.50:11434 (not localhost, from Docker).", {}),
    "llm.model": ("Ollama model", "For the ollama provider, e.g. llama3.2:3b.", {}),
    "llm.api_model": ("Online model", "Blank uses the provider's recommended model (gemini-flash-lite-latest, llama-3.1-8b-instant, "
                                      "or a free OpenRouter model). Free tiers change: set a current model if one stops working.", {}),
    "llm.daily_limit": ("Daily request limit", "For an online provider: at most this many requests a day (retries count). Then "
                                         "built-in lines until it resets; Gemini's day resets at midnight Pacific. Set it a little under "
                                         "your plan's limit, e.g. 450 for 500. 0: no limit.", {"min": 0}),
    "llm.reasoning": ("Reasoning", "For thinking models (like Gemini Flash): none or low makes replies quick, and keeps the thinking "
                                   "from using up the reply. Blank leaves the provider's default. Flash-Lite models rarely need it.",
                      {"choices": ["", "none", "low", "medium"]}),
    "llm.timeout_seconds": ("Timeout seconds", "How long to wait for a reply before using a built-in line. 45 suits slower online models.", {"min": 5, "max": 120}),
    "llm.api_url": ("API address", "Only for the openai provider: the service's OpenAI-compatible base address, ending in /v1.", {}),
    "stylus.installed": ("Stylus installed", "The date, as YYYY-MM-DD.", {"pattern": r"^(\d{4}-\d{2}-\d{2})?$"}),
    "stylus.rated_hours": ("Rated life (hours)", "", {"min": 1}),
}
SECRET_META = {
    "discogs.token": ("Discogs token", "A personal access token, from Discogs settings > Developers."),
    "icecast.source_password": ("Icecast source password", "Generated on first start; change only if you know why."),
    "icecast.admin_password": ("Icecast admin password", "Used to set the stream's track titles."),
    "home_assistant.webhook": ("Home Assistant webhook URL", ""),
    "llm.api_key": ("API key", "For an online provider: from aistudio.google.com (Gemini), console.groq.com (Groq) or openrouter.ai."),
}
SECRET_SECTION = {"discogs.token": "discogs", "icecast.source_password": "station", "icecast.admin_password": "station",
                  "home_assistant.webhook": "home_assistant", "llm.api_key": "llm"}


def _flatten(d: dict, prefix: str = "") -> dict:
    out = {}
    for k, v in d.items():
        path = f"{prefix}.{k}" if prefix else k
        if isinstance(v, dict) and v and not _is_leaf_table(path):
            out.update(_flatten(v, path))
        else:
            out[path] = v
    return out


def _is_leaf_table(path: str) -> bool:
    return False


def _kind(v) -> str:
    if isinstance(v, bool): return "bool"
    if isinstance(v, int): return "int"
    if isinstance(v, float): return "float"
    if isinstance(v, str): return "str"
    if isinstance(v, list) and all(isinstance(x, (str, int, float)) and not isinstance(x, bool) for x in v): return "list"
    return "json"


def _label(path: str) -> str:
    last = path.split(".")[-1].replace("_", " ")
    return last[:1].upper() + last[1:]


# --- the layout: categories, pages, and groups within them ---------------------------------------------
# Each page lists the settings people change most, in groups; everything else from its sections goes
# under Advanced, grouped by sub-table. A setting nobody placed still appears (in its page's Advanced,
# or on "Other"), so new settings can't go missing.
LABELS = {
    "station.input_format": ("Input format", "alsa for a turntable; lavfi plays a test tone instead."),
    "recording.enabled": ("Record sessions", "Keep recordings of each listening session, for playback in the Flight log.", ),
    "recording.kbps": ("Recording quality (kbps)", ""), "recording.preroll": ("Seconds kept before the music", ""),
    "recording.stop_after": ("Stop after (seconds of silence)", ""), "recording.min_seconds": ("Shortest session kept (seconds)", ""),
    "recording.tail": ("Seconds kept after the music", ""),
    "archive.enabled": ("Archive albums", "Cut sides that play through into tagged MP3 tracks, filed as Artist/Album (Year)."),
    "stylus.warn_at": ("Warn at (% of rated life)", "A warning at each of these points."),
    "identify.cargo_only": ("Only accept tracks from your cargo", "A track that isn't on any record in your collection counts as "
                            "unrecognized, which cuts out false matches (another song, the TV, a similar track). Turn this off "
                            "to log anything Shazam hears; records bought since the last Discogs sync count once they've synced."),
    "collection.group_pressings": ("One sleeve per album", "Pressings of the same album (the same Discogs master, or the same artist and "
                                   "title) show as one record, with their history combined. Each pressing stays on the record's page."),
    "discogs.details_per_night": ("Record details fetched per night", ""), "discogs.years_per_night": ("Original years looked up per night", ""),
    "pet.born": ("Born", "YYYY-MM-DD. Blank: when he was first switched on."),
    "personality.description": ("Description", "Who he is, in a sentence; it shapes every line."),
    "personality.snark": ("Snark", "0 to 1."), "personality.enthusiasm": ("Enthusiasm", "0 to 1."),
    "personality.max_words": ("Longest line (words)", ""), "personality.emoji": ("Use emoji", ""),
    "personality.family_friendly": ("Family friendly", "Keeps his language clean."), "personality.catchphrases": ("Catchphrases", "Used now and then."),
    "llm.enabled": ("Use a language model", "Off: Beepnik uses built-in lines."),
    "llm.temperature": ("Creativity", "Higher is more surprising; 0.9 suits him."), "llm.keep_alive": ("Ollama keep-alive", "How long Ollama keeps the model loaded."),
    "quips.track_chance": ("Chance of a line per track", "0 to 1."), "quips.min_gap_seconds": ("Seconds between lines, at least", ""),
    "quips.blocklist": ("Words he never uses", "Comma-separated."),
    "needs.fuel.full_after_minutes": ("Fills after (minutes of listening)", ""), "needs.fuel.empty_after_hours": ("Empties after (hours of silence)", ""),
    "taste.favorites": ("Favorite genres", "Blank: he learns them from your listening."), "taste.dislikes": ("Genres he dislikes", ""),
    "taste.learn": ("Learn favorites from listening", ""),
    "taste.learn_dislikes": ("Learn dislikes from skips", "Styles you often cut short become his dislikes (\"if you won't sit through it, "
                             "neither will I\"), and fade once you stop. Used only while Genres he dislikes is empty."), "mood.low": ("Mood counts as low below", ""),
    "orbit.burn_cost": ("Fuel per burn", "Spent when a side plays through above the burn line."), "orbit.burn_gain": ("Climb per burn", ""),
    "orbit.burn_minutes": ("Minutes of a side for a burn", ""), "orbit.hold_level": ("Falls below (fuel)", "Below this he can't hold his orbit."),
    "orbit.reserve": ("Reserve kept after a burn", "The burn line is this plus the fuel per burn."),
    "orbit.station_keeping_per_day": ("Station keeping (fuel per day)", ""),
    "life.enabled": ("He can be lost", "Off: he falls, but never reaches Point Nemo."),
    "life.distress_days": ("Distress after (days without music)", ""), "life.mayday_days": ("Mayday after (days)", ""),
    "life.lost_hours": ("Lost after (hours)", ""),
    "signal.tiers": ("Tiers", "Each: days of contact, an id, a name. Tiers never go down."),
    "traits.min_share": ("Share of listening for a trait", "0 to 1: how much of a genre earns its look."), "traits.max": ("Most traits at once", ""),
    "traits.window_days": ("Days of listening considered", ""),
    "survey.enabled": ("Survey missions", ""), "survey.reward": ("Curiosity reward", ""), "survey.expire_hours": ("Hours to complete one", ""),
    "survey.cooldown_hours": ("Hours between missions", ""),
    "ground.enabled": ("Ground Control", "A short report on each new track."), "ground.use_llm": ("Rephrase with the language model", "It may only use the facts it's given."),
    "ground.track_facts": ("Facts about the track", ""), "ground.record_facts": ("Facts about the record", ""),
    "panel.ticker_speed": ("Ticker speed", ""), "panel.ticker_gap_seconds": ("Pause between ticker lines (seconds)", ""),
    "panel.beat_offset_ms": ("Beat offset (ms)", "Lines the animations up with what you hear."),
    "panel.collapse_idle_minutes": ("Collapse the player after (minutes idle)", ""),
    "web.port": ("Port", "Inside the container; change HTTP_PORT in .env instead."),
}
SECTION_NAMES = {"needs": "", "mood": "Mood", "craving": "Cravings", "taste": "Taste", "orbit": "Orbit", "life": "Life", "quips": "Chatter",
                 "canned": "Fallback lines", "personality": "Personality", "signal": "Signal", "traits": "Traits"}

PAGES = [
    # category, id, title, sections, groups [(title, [paths])], about
    ("Station", "station", "Turntable and stream", ["station"], [("The turntable", ["station.device", "station.sample_rate", "station.channels"]),
     ("The stream", ["station.name", "station.bitrate"])], "Applies under Docker; a hand-built install keeps its own stream command."),
    ("Station", "identify", "Identification", ["identify"], [("Matching", ["identify.cargo_only"]), ("Checking", ["identify.interval", "identify.sample_seconds", "identify.silence_rms"]),
     ("Stream titles", ["identify.idle_title", "identify.vinyl_title"]), ("MusicBrainz", ["identify.musicbrainz_contact"])], "How tracks are recognized."),
    ("Station", "recording", "Recording", ["recording"], [("", ["recording.enabled", "recording.keep_sessions", "recording.min_free_gb"])],
     "Session recordings, for playback in the Flight log."),
    ("Station", "archive", "Album archive", ["archive"], [("", ["archive.enabled", "archive.library"])], "Albums saved track by track from the recordings."),
    ("Station", "stylus", "Stylus", ["stylus"], [("", ["stylus.installed", "stylus.rated_hours", "stylus.warn_at"])], "Wear tracking for your stylus."),
    ("Collection", "discogs", "Discogs", ["discogs", "collection"], [("Your account", ["discogs.user", "secret:discogs.token"]), ("Nightly sync", ["discogs.sync_time"]),
     ("The cargo hold", ["collection.group_pressings"])],
     "Your collection: covers, tracklists, pressings."),
    ("Beepnik", "profile", "Profile", ["pet", "personality"], [("Who he is", ["pet.name", "pet.born"]),
     ("Personality", ["personality.description", "personality.snark", "personality.enthusiasm", "personality.max_words",
                      "personality.family_friendly", "personality.emoji", "personality.catchphrases"])], ""),
    ("Beepnik", "voice", "Voice", ["llm"], [("Provider", ["llm.enabled", "llm.provider"]),
     ("Online service", ["llm.api_model", "secret:llm.api_key", "llm.daily_limit", "llm.reasoning", "llm.api_url"]), ("Ollama", ["llm.url", "llm.model"]),
     ("Style", ["llm.temperature", "llm.timeout_seconds"])], None),
    ("Beepnik", "chatter", "Chatter", ["quips", "canned"], [("When he speaks", ["quips.track_chance", "quips.idle_minutes", "quips.min_gap_seconds"]),
     ("Quiet hours", ["quips.quiet_start", "quips.quiet_end"]), ("Words to avoid", ["quips.blocklist"])], "When and how often Beepnik speaks."),
    ("Beepnik", "needs", "Needs and mood", ["needs", "mood", "taste", "craving"], [("Fuel", ["needs.fuel.full_after_minutes", "needs.fuel.empty_after_hours"]),
     ("Taste", ["taste.favorites", "taste.dislikes", "taste.learn", "taste.learn_dislikes"]), ("Mood", ["mood.low"])], "How his needs rise and fall."),
    ("Beepnik", "orbit", "Orbit and life", ["orbit", "life"], [("Burns", ["orbit.burn_cost", "orbit.burn_gain", "orbit.burn_minutes"]),
     ("Holding orbit", ["orbit.hold_level", "orbit.reserve", "orbit.station_keeping_per_day"]),
     ("Life and loss", ["life.enabled", "life.distress_days", "life.mayday_days", "life.lost_hours"])], "Climbing, holding, falling."),
    ("Beepnik", "signal", "Signal and traits", ["signal", "traits"], [("Signal", ["signal.tiers"]),
     ("Traits", ["traits.min_share", "traits.max", "traits.window_days"])], "Equipment for the days you play, and looks for what you play."),
    ("Beepnik", "missions", "Missions", ["survey"], [("", ["survey.enabled", "survey.reward", "survey.expire_hours", "survey.cooldown_hours"])], ""),
    ("Beepnik", "ground", "Ground Control", ["ground"], [("", ["ground.enabled", "ground.use_llm", "ground.track_facts", "ground.record_facts"])], ""),
    ("Connections", "home_assistant", "Home Assistant", ["home_assistant"], [("", ["home_assistant.enabled", "secret:home_assistant.webhook"])],
     "Send each track to Home Assistant."),
    ("Connections", "cast", "Google Cast", ["cast"], [("", ["cast.app_id"])], "The Cast button, if you've registered a receiver."),
    ("System", "web", "Site and sign-in", ["web"], [("Sign-in", ["web.require_sign_in_to_edit"]), ("Address", ["web.public_url", "web.trusted_proxies"])], ""),
    ("System", "panel", "Session player", ["panel"], [("", ["panel.ticker_speed", "panel.ticker_gap_seconds", "panel.beat_offset_ms"])], ""),
]
CATEGORIES = ["Station", "Collection", "Beepnik", "Connections", "System"]
EXTRA_SECRETS = {"station": ["secret:icecast.source_password", "secret:icecast.admin_password"]}   # into the page's Advanced


def _field(path, current, defaults, cfg):
    if path.startswith("secret:"):
        spath = path[7:]
        label, help_ = SECRET_META.get(spath, (_label(spath), ""))
        sec, key = spath.split(".")
        return {"path": path, "label": label, "help": help_, "kind": "secret", "set": bool(cfg["secrets"].get(sec, {}).get(key))}
    value, default = current[path], defaults.get(path)
    label, help_, opts = META.get(path, (None, "", {}))
    if label is None:
        label, help_ = LABELS.get(path, (_label(path), ""))
    return {"path": path, "label": label, "help": help_, "kind": _kind(default if default is not None else value),
            "value": value, "default": default, "custom": path not in defaults, **opts}


def _sub_title(path, multi):
    parts = path.split(".")[:-1] or [path]       # a whole section as one setting (like canned)
    sec = parts[0]
    name = " ".join(x.replace("_", " ") for x in parts[1:]).strip()
    base = SECTION_NAMES.get(sec, sec.replace("_", " ").title()) if multi else ""
    title = f"{base} {name}".strip() if base else name
    return title[:1].upper() + title[1:] if title else ""


def schema(cfg: dict) -> list:
    """The settings page: pages, in categories, each with groups of settings and an Advanced part."""
    defaults = _flatten(tc.all_defaults())
    current = _flatten({k: v for k, v in cfg.items() if k != "secrets"})
    applies_live = set(tc._pet_defaults())
    placed, pages = set(), []
    for cat, pid, title, sections, groups, about in PAGES:
        if about is None:
            about = next((t[3] for t in SECTIONS if t[0] == sections[0]), "")
        page_groups = []
        for gtitle, paths in groups:
            fields = [_field(p, current, defaults, cfg) for p in paths if p.startswith("secret:") or p in current]
            placed.update(paths)
            if fields:
                page_groups.append({"title": gtitle, "fields": fields})
        rest = [p for p in sorted(current) if p.split(".")[0] in sections and p not in placed and p.split(".")[0] not in HIDDEN
                and p not in HIDDEN_PATHS]
        placed.update(rest)
        adv = {}
        for p in rest:
            adv.setdefault(_sub_title(p, len(sections) > 1), []).append(_field(p, current, defaults, cfg))
        for sp in EXTRA_SECRETS.get(pid, []):
            adv.setdefault("Stream server", []).append(_field(sp, current, defaults, cfg))
        pages.append({"id": pid, "category": cat, "title": title, "about": about,
                      "applies": "live" if all(s in applies_live or s == "cast" for s in sections) else "restart",
                      "groups": page_groups, "advanced": [{"title": t, "fields": f} for t, f in adv.items()]})
    leftovers = [p for p in sorted(current) if p not in placed and p.split(".")[0] not in HIDDEN | {"secrets"} and p not in HIDDEN_PATHS]
    if leftovers:
        pages.append({"id": "other", "category": "System", "title": "Other", "about": "Settings from your file that aren't on another page.",
                      "applies": "restart", "groups": [{"title": "", "fields": [_field(p, current, defaults, cfg) for p in leftovers]}], "advanced": []})
    return pages


# --- validation -----------------------------------------------------------------------------------------
def validate(path: str, raw, like, opts: dict):
    """The value to store, or raises ValueError with a sentence for the page."""
    kind = _kind(like)
    if kind == "bool":
        if not isinstance(raw, bool): raise ValueError("should be on or off")
        v = raw
    elif kind in ("int", "float"):
        try:
            v = int(raw) if kind == "int" else float(raw)
        except (TypeError, ValueError):
            raise ValueError("should be a number" if kind == "float" else "should be a whole number")
        if "min" in opts and v < opts["min"]: raise ValueError(f"should be at least {opts['min']}")
        if "max" in opts and v > opts["max"]: raise ValueError(f"should be at most {opts['max']}")
    elif kind == "str":
        v = str(raw)
        if len(v) > 2000: raise ValueError("is too long")
        if "pattern" in opts and not re.match(opts["pattern"], v): raise ValueError("isn't in the expected format")
    elif kind == "list":
        if isinstance(raw, str): raw = [x.strip() for x in raw.split(",") if x.strip()]
        if not isinstance(raw, list): raise ValueError("should be a list")
        elem = like[0] if like else ""
        try:
            v = [type(elem)(x) if not isinstance(elem, str) else str(x) for x in raw]
        except (TypeError, ValueError):
            raise ValueError("has an item of the wrong type")
    else:
        if isinstance(raw, str):
            try:
                raw = json.loads(raw)
            except json.JSONDecodeError as e:
                raise ValueError(f"isn't valid JSON ({e.msg})")
        if type(raw) is not type(like): raise ValueError(f"should be a {type(like).__name__} like the default")
        v = raw
    if "choices" in opts and v not in opts["choices"]:
        raise ValueError(f"should be one of {', '.join(map(str, opts['choices']))}")
    return v


def _set(d: dict, path: str, value):
    parts = path.split(".")
    for p in parts[:-1]:
        d = d.setdefault(p, {})
    d[parts[-1]] = value


def _diff(cur: dict, base: dict) -> dict:
    out = {}
    for k, v in cur.items():
        if isinstance(v, dict) and isinstance(base.get(k), dict):
            sub = _diff(v, base[k])
            if sub: out[k] = sub
        elif base.get(k) != v or k not in base:
            out[k] = v
    return out


def save(values: dict, new_secrets: dict) -> dict:
    """Validate and save. Returns {saved, errors, restart}."""
    cfg = tc.load()
    defaults = tc.all_defaults()
    flat_def, flat_cur = _flatten(defaults), _flatten({k: v for k, v in cfg.items() if k != "secrets"})
    errors, stored, restart = {}, {}, set()
    titles = {s[0]: s for s in SECTIONS}
    for path, raw in (values or {}).items():
        sec = path.split(".")[0]
        if sec in HIDDEN or sec == "secrets" or path not in flat_cur:
            errors[path] = "isn't a setting"
            continue
        like = flat_def.get(path, flat_cur[path])
        try:
            stored[path] = validate(path, raw, like, META.get(path, ("", "", {}))[2])
        except ValueError as e:
            errors[path] = str(e)
    for spath, raw in (new_secrets or {}).items():
        if spath not in SECRET_META:
            errors[f"secret:{spath}"] = "isn't a secret setting"
        elif not isinstance(raw, str) or len(raw) > 2000:
            errors[f"secret:{spath}"] = "should be text"
    if errors:
        return {"saved": False, "errors": errors}
    # the file keeps only what differs from the defaults (and anything not in the defaults)
    file_now = tc._read(tc.CONFIG_FILE)
    merged = tc._merge(copy.deepcopy(defaults), file_now)
    for path, v in stored.items():
        _set(merged, path, v)
        sec = path.split(".")[0]
        if titles.get(sec, ("", "", "live" if sec in tc._pet_defaults() else "restart"))[2] == "restart":
            restart.add(titles.get(sec, (sec, sec))[1])
    out = _diff(merged, defaults)
    for sec in OBSOLETE:
        out.pop(sec, None)
    out.pop("paths", None) if "paths" not in file_now else None
    if "paths" in file_now:
        out["paths"] = file_now["paths"]
    os.makedirs(os.path.dirname(tc.CONFIG_FILE) or ".", exist_ok=True)
    tc.write_atomic(tc.CONFIG_FILE, tc.dumps(out, "# Turntable-1 settings, saved by the settings page. Only what differs from the\n"
                                                    "# defaults is listed. You can edit this by hand too."))
    if new_secrets:
        sec = tc._read(tc.SECRETS_FILE)
        for spath, raw in new_secrets.items():
            s, k = spath.split(".")
            sec.setdefault(s, {})[k] = raw.strip()
            restart.add(titles.get(SECRET_SECTION[spath], (spath, spath))[1])
        _write_secrets(sec)
    return {"saved": True, "count": len(stored) + len(new_secrets or {}), "restart": sorted(restart)}


def _write_secrets(sec: dict):
    os.makedirs(os.path.dirname(tc.SECRETS_FILE) or ".", exist_ok=True)
    tc.write_atomic(tc.SECRETS_FILE, tc.dumps({s: t for s, t in sec.items() if t},
                                              "# Turntable-1 secrets: passwords and tokens. Keep this file private."), 0o600)


# --- the admin password and sessions ---------------------------------------------------------------------
def _hash(password: str, salt: bytes = None) -> str:
    salt = salt or os.urandom(16)
    h = hashlib.scrypt(password.encode(), salt=salt, n=2 ** 14, r=8, p=1, dklen=32)
    return "scrypt$" + base64.b64encode(salt).decode() + "$" + base64.b64encode(h).decode()


def _check(password: str, stored: str) -> bool:
    try:
        _, salt, h = stored.split("$")
        return hmac.compare_digest(_hash(password, base64.b64decode(salt)).split("$")[2], h)
    except Exception:
        return False


def _secrets() -> dict:
    return tc._read(tc.SECRETS_FILE)


def _session_key() -> bytes:
    sec = _secrets()
    key = sec.get("web", {}).get("session_key")
    if not key:
        key = pysecrets.token_hex(32)
        sec.setdefault("web", {})["session_key"] = key
        _write_secrets(sec)
    return key.encode()


def _token(expires: int) -> str:
    sig = hmac.new(_session_key(), str(expires).encode(), hashlib.sha256).hexdigest()
    return f"{expires}.{sig}"


def signed_in(request: Request) -> bool:
    t = request.cookies.get(COOKIE, "")
    if "." not in t:
        return False
    exp, sig = t.split(".", 1)
    if not exp.isdigit() or int(exp) < time.time():
        return False
    return hmac.compare_digest(_token(int(exp)).split(".", 1)[1], sig)


def configured() -> bool:
    return bool(_secrets().get("web", {}).get("admin_password_hash"))


def _signed_in_response(request: Request, body: dict) -> JSONResponse:
    exp = int(time.time()) + SESSION_DAYS * 86400
    r = JSONResponse(body)
    secure = request.url.scheme == "https" or request.headers.get("x-forwarded-proto") == "https"
    r.set_cookie(COOKIE, _token(exp), max_age=SESSION_DAYS * 86400, httponly=True, samesite="strict", secure=secure, path="/")
    return r


def _require(request: Request):
    if not signed_in(request):
        raise HTTPException(401, "Sign in first.")


def _client(request: Request) -> str:
    return request.client.host if request.client else "?"


@router.get("/api/admin/session")
def session(request: Request):
    return {"configured": configured(), "signed_in": signed_in(request), "version": os.environ.get("TURNTABLE_VERSION", "")}


@router.post("/api/admin/setup")
async def setup(request: Request):
    if configured():
        raise HTTPException(409, "An admin password is already set.")
    body = await request.json()
    pw = str(body.get("password", ""))
    if len(pw) < 8:
        raise HTTPException(400, "Use at least 8 characters.")
    sec = _secrets()
    sec.setdefault("web", {})["admin_password_hash"] = _hash(pw)
    _write_secrets(sec)
    return _signed_in_response(request, {"ok": True})


@router.post("/api/admin/login")
async def login(request: Request):
    who, now = _client(request), time.time()
    fails, until = _fails.get(who, [0, 0])
    if until > now:
        raise HTTPException(429, f"Too many tries. Wait {int(until - now) + 1} seconds.")
    body = await request.json()
    stored = _secrets().get("web", {}).get("admin_password_hash", "")
    if not stored or not _check(str(body.get("password", "")), stored):
        fails += 1
        _fails[who] = [fails, now + 300 if fails >= 5 else 0]
        raise HTTPException(401, "That password isn't right.")
    _fails.pop(who, None)
    return _signed_in_response(request, {"ok": True})


@router.post("/api/admin/logout")
def logout():
    r = JSONResponse({"ok": True})
    r.delete_cookie(COOKIE, path="/")
    return r


@router.post("/api/admin/password")
async def change_password(request: Request):
    _require(request)
    body = await request.json()
    stored = _secrets().get("web", {}).get("admin_password_hash", "")
    if not _check(str(body.get("current", "")), stored):
        raise HTTPException(401, "The current password isn't right.")
    new = str(body.get("password", ""))
    if len(new) < 8:
        raise HTTPException(400, "Use at least 8 characters.")
    sec = _secrets()
    sec["web"]["admin_password_hash"] = _hash(new)
    sec["web"]["session_key"] = pysecrets.token_hex(32)          # signs every other session out
    _write_secrets(sec)
    return _signed_in_response(request, {"ok": True})


@router.get("/api/settings")
def get_settings(request: Request):
    _require(request)
    return {"categories": CATEGORIES, "pages": schema(tc.load())}


on_saved = []                                   # the web app's own settings reload, registered by app.py


@router.post("/api/settings")
async def post_settings(request: Request):
    _require(request)
    body = await request.json()
    result = save(body.get("values") or {}, body.get("secrets") or {})
    if not result["saved"]:
        return JSONResponse(result, status_code=400)
    for fn in on_saved:
        fn()
    return result
