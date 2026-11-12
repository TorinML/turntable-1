#!/usr/bin/env python3
"""Turntable-1's settings, in one place, for every service and the settings page.

Settings come from, in order (later wins):
  1. the built-in defaults below (and Beepnik's, in pet_engine.DEFAULTS), so a fresh install runs;
  2. turntable.toml in the config folder;
  3. secrets.toml beside it: passwords and tokens, kept apart so the settings page never shows them;
  4. environment variables: the original names (TURNTABLE_DB, RECORD_KEEP, DISCOGS_TOKEN...) and,
     for anything else, TT_SECTION__KEY (TT_RECORDING__KEEP_SESSIONS=10).

Values are converted to the type of their default, so RECORD_KEEP=10 is the number 10.

  turntable_config.py --show                       the settings in effect (secrets hidden)
  turntable_config.py --migrate [--pet FILE] [--env FILE]
                                                   build turntable.toml and secrets.toml from a
                                                   Turntable-1 install's turntable-pet.toml and
                                                   turntable-id.env (it won't overwrite either)
"""
import copy
import json
import os
import re
import sys
import tomllib

CONFIG_DIR = os.environ.get("TURNTABLE_CONFIG_DIR", "/config")
CONFIG_FILE = os.path.join(CONFIG_DIR, "turntable.toml")
SECRETS_FILE = os.path.join(CONFIG_DIR, "secrets.toml")

DEFAULTS = {
    "station": {
        "name": "Turntable-1",
        "input_format": "alsa",           # how ffmpeg reads the device (alsa; lavfi for a test tone)
        "device": "auto",                 # the ALSA capture device; "auto" picks the first USB audio device
                                          # (a USB turntable is often plughw:CARD=CODEC,DEV=0)
        "sample_rate": 48000,
        "channels": 2,
        "bitrate": 320,                   # kbps, the MP3 stream
        "icecast_url": "http://icecast:8000",
        "mount": "/turntable",
    },
    "identify": {
        "sample_seconds": 8,              # each sample sent for identification
        "interval": 20,                   # seconds from one check's start to the next (keep 20 or more)
        "silence_rms": 300,               # int16 RMS below this counts as silence
        "silent_limit": 2,                # silent checks in a row before the station is idle
        "misses_limit": 3,                # failed checks in a row before the generic title
        "confirm_hits": 2,                # checks that must agree before an unexpected track takes over
        "cargo_only": True,               # only accept tracks found on a record in your collection
        "reassert_every": 15,             # re-send the current title every N checks
        "reconcile_hours": 6,
        "idle_title": "Turntable - Idle",
        "vinyl_title": "Turntable - Vinyl",
        "musicbrainz_contact": "",        # an email or URL MusicBrainz can reach you at (their request)
    },
    "collection": {
        "group_pressings": True,          # several pressings of one album show as one, in Cargo and the star chart
    },
    "discogs": {
        "user": "",
        "sync_time": "04:30",             # the nightly collection sync, local time
        "details_per_night": 80,
        "years_per_night": 60,
    },
    "web": {
        "port": 8080,
        "public_url": "",                 # e.g. https://turntable.example.com, for Cast and links
        "trusted_proxies": [],            # addresses of reverse proxies whose X-Forwarded headers to trust
        "require_sign_in_to_edit": True,  # tagging and fixing plays need the admin sign-in
    },
    "recording": {
        "enabled": True,
        "keep_sessions": 5,
        "kbps": 320,
        "preroll": 60,
        "stop_after": 600,
        "min_seconds": 90,                # shorter sessions aren't kept
        "tail": 20,                       # seconds kept after the music stops, so a fade-out isn't clipped
        "min_free_gb": 2,
    },
    "archive": {
        "enabled": False,                 # album tracks, cut from the session recordings
        "library": "/archive",            # where they're filed: Artist/Album (Year)/NN - Title.mp3
    },
    "home_assistant": {
        "enabled": False,                 # the webhook URL itself is a secret
    },
    "cast": {
        "app_id": "",                     # your Google Cast receiver app id, if you set one up
    },
    "paths": {
        "data": "/data",
        "db": "/data/turntable.db",
        "covers": "/data/covers",
        "recordings": "/data/recordings",
        "spool": "/data/archive-spool",
        "telemetry": "/run/turntable/telemetry.json",
    },
}

# secrets.toml: never shown by the settings page, only replaced
SECRET_DEFAULTS = {
    "discogs": {"token": ""},
    "icecast": {"source_password": "", "admin_password": ""},
    "home_assistant": {"webhook": ""},
    "llm": {"api_key": ""},
    "web": {"admin_password_hash": ""},
}

# the original environment variables keep working
LEGACY_ENV = {
    "TURNTABLE_DB": ("paths", "db"), "TURNTABLE_COVERS": ("paths", "covers"), "TELEMETRY_PATH": ("paths", "telemetry"),
    "RECORD_DIR": ("paths", "recordings"), "ARCHIVE_SPOOL": ("paths", "spool"), 
    "ARCHIVE_DIR": ("archive", "library"), "RECORD_KEEP": ("recording", "keep_sessions"), "RECORD_KBPS": ("recording", "kbps"),
    "RECORD_PREROLL": ("recording", "preroll"), "RECORD_STOP_AFTER": ("recording", "stop_after"),
    "RECORD_MIN_SECONDS": ("recording", "min_seconds"), "RECORD_MIN_FREE_GB": ("recording", "min_free_gb"),
    "RECORD_TAIL": ("recording", "tail"),
    "DISCOGS_USER": ("discogs", "user"), "MB_CONTACT": ("identify", "musicbrainz_contact"),
}
LEGACY_SECRET_ENV = {
    "DISCOGS_TOKEN": ("discogs", "token"), "ICECAST_ADMIN_PASS": ("icecast", "admin_password"),
    "ICECAST_SOURCE_PASS": ("icecast", "source_password"), "HA_WEBHOOK": ("home_assistant", "webhook"),
}


def _pet_defaults() -> dict:
    try:
        sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
        import pet_engine
        return copy.deepcopy(pet_engine.DEFAULTS)
    except Exception:
        return {}


def all_defaults() -> dict:
    d = _pet_defaults()
    for k, v in DEFAULTS.items():
        d[k] = {**d.get(k, {}), **copy.deepcopy(v)} if isinstance(v, dict) else v
    return d


def _merge(base: dict, over: dict) -> dict:
    for k, v in over.items():
        if isinstance(v, dict) and isinstance(base.get(k), dict):
            _merge(base[k], v)
        else:
            base[k] = v
    return base


def coerce(value, like):
    """A value (often a string from the environment) as the type of its default."""
    if isinstance(like, bool):
        return value if isinstance(value, bool) else str(value).strip().lower() in ("1", "true", "yes", "on")
    if isinstance(like, int) and not isinstance(like, bool):
        return int(float(value)) if not isinstance(value, int) else value
    if isinstance(like, float):
        return float(value)
    if isinstance(like, list) and isinstance(value, str):
        v = value.strip()
        return json.loads(v) if v.startswith("[") else [x.strip() for x in v.split(",") if x.strip()]
    return value


def _read(path: str) -> dict:
    try:
        with open(path, "rb") as f:
            return tomllib.load(f)
    except FileNotFoundError:
        return {}


def _apply_env(cfg: dict, mapping: dict, env: dict):
    for name, (sec, key) in mapping.items():
        if env.get(name, "").strip():
            like = cfg.get(sec, {}).get(key, "")
            cfg.setdefault(sec, {})[key] = coerce(env[name].strip(), like)
    for name, value in env.items():                        # TT_SECTION__KEY
        m = re.fullmatch(r"TT_([A-Z0-9_]+?)__([A-Z0-9_]+)", name)
        if m:
            sec, key = m.group(1).lower(), m.group(2).lower()
            if sec in cfg and key in cfg[sec]:
                cfg[sec][key] = coerce(value, cfg[sec][key])


def load(config_file: str = None, secrets_file: str = None, env: dict = None) -> dict:
    """Every setting in effect: defaults, then the files, then the environment."""
    env = os.environ if env is None else env
    cfg = _merge(all_defaults(), _read(config_file or CONFIG_FILE))
    _apply_env(cfg, LEGACY_ENV, env)
    sec = _merge(copy.deepcopy(SECRET_DEFAULTS), _read(secrets_file or SECRETS_FILE))
    _apply_env(sec, LEGACY_SECRET_ENV, env)
    cfg["secrets"] = sec
    return cfg


def stream_url(cfg: dict) -> str:
    """The station's own stream, as the services inside reach it."""
    return cfg["station"]["icecast_url"].rstrip("/") + cfg["station"]["mount"]


def redacted(cfg: dict) -> dict:
    out = copy.deepcopy(cfg)
    for sec in out.get("secrets", {}).values():
        for k, v in sec.items():
            sec[k] = "(set)" if v else ""
    return out


# --- writing TOML (Python reads it natively, but doesn't write it) ------------------------------------
def _tv(v) -> str:
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, (int, float)):
        return repr(v)
    if isinstance(v, str):
        return json.dumps(v, ensure_ascii=False)
    if isinstance(v, (list, tuple)):
        return "[" + ", ".join(_tv(x) for x in v) + "]"
    if isinstance(v, dict):
        return "{ " + ", ".join(f"{_key(k)} = {_tv(x)}" for k, x in v.items()) + " }"
    raise TypeError(f"can't write {type(v).__name__} to TOML")


def _key(k: str) -> str:
    return k if re.fullmatch(r"[A-Za-z0-9_-]+", k) else json.dumps(k)


def dumps(data: dict, header: str = "") -> str:
    lines = [header.rstrip(), ""] if header else []
    def section(prefix, table):
        plain = {k: v for k, v in table.items() if not isinstance(v, dict)}
        subs = {k: v for k, v in table.items() if isinstance(v, dict)}
        if prefix and plain:
            lines.append(f"[{prefix}]")
        for k, v in plain.items():
            lines.append(f"{_key(k)} = {_tv(v)}")
        if prefix and plain:
            lines.append("")
        for k, v in subs.items():
            section(f"{prefix}.{_key(k)}" if prefix else _key(k), v)
    section("", data)
    return "\n".join(lines).rstrip() + "\n"


def write_atomic(path: str, text: str, mode: int = 0o644):
    tmp = f"{path}.tmp"
    with open(tmp, "w") as f:
        f.write(text)
    os.chmod(tmp, mode)
    os.replace(tmp, path)


# --- migrating an existing install -----------------------------------------------------------------
def read_unit_env(paths: list) -> dict:
    """Environment= lines from systemd service files (the recorder's settings lived there)."""
    out = {}
    for path in paths:
        try:
            for raw in open(path):
                line = raw.strip()
                if line.startswith("Environment="):
                    for part in re.findall(r'"[^"]*"|\S+', line[len("Environment="):]):
                        part = part.strip('"')
                        if "=" in part:
                            k, v = part.split("=", 1)
                            out[k] = v
        except OSError:
            pass
    return out


def read_stream_unit(paths: list) -> dict:
    """The [station] settings, from an install's ffmpeg stream command (turntable-stream.service)."""
    for path in paths:
        try:
            text = open(path).read()
        except OSError:
            continue
        if "ffmpeg" not in text or "icecast://" not in text:
            continue
        cmd = re.sub(r"\\\n\s*", " ", text)
        found = {}
        for key, pattern, cast in (("device", r"-i\s+(\S+)", str), ("sample_rate", r"-ar\s+(\d+)", int),
                                   ("channels", r"-ac\s+(\d+)", int), ("bitrate", r"-b:a\s+(\d+)k", int),
                                   ("name", r'-ice_name\s+"([^"]+)"', str), ("mount", r"icecast://\S+?:\d+(/\S+)", str)):
            m = re.search(pattern, cmd)
            if m:
                found[key] = cast(m.group(1))
        return found
    return {}


def read_env_file(path: str) -> dict:
    out = {}
    try:
        for raw in open(path):
            line = raw.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            out[k.strip()] = v.strip().strip('"').strip("'")
    except FileNotFoundError:
        pass
    return out


def migrate(pet_file: str, env_file: str, extra_env: dict = None, config_file: str = None, secrets_file: str = None,
            station: dict = None) -> tuple:
    """Build turntable.toml and secrets.toml from an existing install's files. Only settings that
    differ from the defaults are written, so the file stays short and future defaults still apply."""
    config_file, secrets_file = config_file or CONFIG_FILE, secrets_file or SECRETS_FILE
    if os.path.exists(config_file) or os.path.exists(secrets_file):
        raise FileExistsError("turntable.toml or secrets.toml already exists; move it aside to migrate again")
    env = {**read_env_file(env_file), **(extra_env or {})}
    cfg = _merge(all_defaults(), _read(pet_file))
    _apply_env(cfg, LEGACY_ENV, env)
    cfg["station"].update(station or {})
    sec = copy.deepcopy(SECRET_DEFAULTS)
    _apply_env(sec, LEGACY_SECRET_ENV, env)
    if sec["home_assistant"]["webhook"]:
        cfg["home_assistant"]["enabled"] = True
    # Where things live is up to the new install (in Docker, everything is under /data), so the old
    # install's paths aren't carried over. An archive the old install kept is switched on here; its
    # folder is whatever gets mounted at the new install's archive path.
    if env.get("ARCHIVE_DIR", "").strip():
        cfg["archive"]["enabled"] = True
    cfg["paths"] = copy.deepcopy(DEFAULTS["paths"])
    cfg["archive"]["library"] = DEFAULTS["archive"]["library"]
    defaults = all_defaults()
    changed = {}
    for s, table in cfg.items():
        if not isinstance(table, dict):
            continue
        diff = {k: v for k, v in table.items() if defaults.get(s, {}).get(k) != v}
        if diff:
            changed[s] = diff
    os.makedirs(os.path.dirname(config_file) or ".", exist_ok=True)
    write_atomic(config_file, dumps(changed, "# Turntable-1 settings. Only what differs from the defaults is listed;\n"
                                             "# the settings page shows them all. Migrated from turntable-pet.toml and turntable-id.env."))
    write_atomic(secrets_file, dumps({s: {k: v for k, v in t.items() if v} for s, t in sec.items() if any(t.values())},
                                     "# Turntable-1 secrets: passwords and tokens. Keep this file private."), 0o600)
    return changed, redacted({"secrets": sec})["secrets"]


def main():
    import argparse
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--show", action="store_true")
    ap.add_argument("--migrate", action="store_true")
    ap.add_argument("--pet", default="/etc/turntable-pet.toml")
    ap.add_argument("--env", default="/etc/turntable-id.env")
    ap.add_argument("--units", default="/etc/systemd/system/turntable-*.service", help="service files to read Environment= lines from")
    args = ap.parse_args()
    if args.migrate:
        import glob
        unit_env = read_unit_env(sorted(glob.glob(args.units)))
        changed, secrets = migrate(args.pet, args.env, extra_env={**unit_env, **{k: v for k, v in os.environ.items() if k in LEGACY_ENV or k in LEGACY_SECRET_ENV}},
                                   station=read_stream_unit(sorted(glob.glob(args.units))))
        print(f"Wrote {CONFIG_FILE} ({sum(len(t) for t in changed.values())} settings that differ from the defaults)")
        print(f"Wrote {SECRETS_FILE}: " + ", ".join(f"{s}.{k}" for s, t in secrets.items() for k, v in t.items() if v))
    else:
        print(dumps(redacted(load())))


if __name__ == "__main__":
    main()
