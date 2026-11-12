#!/usr/bin/env python3
"""Turntable-1's container entrypoint. One image, four roles:

  icecast   the stream server (and, on first start, the stream's passwords)
  capture   reads the turntable and sends it to Icecast (ffmpeg)
  core      identification, Beepnik, recording, telemetry, the album archive, the Discogs sync
  web       the site, the API and the TV view

  migrate   build the config from an existing Turntable-1 install's files, mounted at /legacy
"""
import os
import secrets as pysecrets
import socket
import subprocess
import sys
import time
from urllib.parse import urlparse

sys.path.insert(0, os.environ.get("TURNTABLE_CORE", "/app/core"))
import turntable_config as tc

HERE = os.path.dirname(os.path.abspath(__file__))


def log(msg):
    print(f"[{sys.argv[1] if len(sys.argv) > 1 else 'entrypoint'}] {msg}", flush=True)


def writable(path):
    try:
        probe = os.path.join(path, ".turntable-write-test")
        with open(probe, "w") as f:
            f.write("ok")
        os.unlink(probe)
        return True
    except OSError:
        return False


def prepare(cfg):
    """The folders everything expects, under the data and config volumes."""
    for vol in (tc.CONFIG_DIR, cfg["paths"]["data"]):
        if os.path.isdir(vol) and not writable(vol):
            log(f"Can't write to {vol}. On the host, the folder mounted there must belong to user 1000: "
                f"sudo chown -R 1000:1000 config data archive (in the Turntable-1 folder), then start again.")
            sys.exit(1)
    os.makedirs(tc.CONFIG_DIR, exist_ok=True)
    p = cfg["paths"]
    for d in (p["data"], p["covers"], p["recordings"], p["spool"], os.path.dirname(p["telemetry"])):
        try:
            os.makedirs(d, exist_ok=True)
        except OSError as e:
            log(f"Can't create {d}: {e}")


def ensure_icecast_secrets():
    """First start: random passwords for the stream server (only the icecast role does this, so
    containers starting together can't each invent different ones)."""
    sec = tc._read(tc.SECRETS_FILE)
    ice = sec.setdefault("icecast", {})
    changed = False
    for k in ("source_password", "admin_password"):
        if not ice.get(k):
            ice[k] = pysecrets.token_urlsafe(18)
            changed = True
    if changed:
        tc.write_atomic(tc.SECRETS_FILE, tc.dumps({s: t for s, t in sec.items() if t},
                                                  "# Turntable-1 secrets: passwords and tokens. Keep this file private."), 0o600)
        log("Generated the stream server's passwords (in secrets.toml)")


def wait_for_secrets(timeout=120):
    end = time.time() + timeout
    while time.time() < end:
        cfg = tc.load()
        if cfg["secrets"]["icecast"]["source_password"]:
            return cfg
        time.sleep(2)
    log("The stream server's passwords never appeared; is the icecast container running?")
    sys.exit(1)


def wait_for_port(url, timeout=180):
    u = urlparse(url)
    host, port = u.hostname, u.port or 80
    end = time.time() + timeout
    while time.time() < end:
        try:
            with socket.create_connection((host, port), timeout=3):
                return True
        except OSError:
            time.sleep(2)
    return False


def run_icecast():
    ensure_icecast_secrets()
    cfg = tc.load()
    prepare(cfg)
    s, ice = cfg["station"], cfg["secrets"]["icecast"]
    port = urlparse(s["icecast_url"]).port or 8000
    tmpl = open(os.path.join(HERE, "icecast.xml.tmpl")).read()
    xml = (tmpl.replace("{{SOURCE_PASSWORD}}", ice["source_password"]).replace("{{ADMIN_PASSWORD}}", ice["admin_password"])
              .replace("{{PORT}}", str(port)).replace("{{MOUNT}}", s["mount"]).replace("{{NAME}}", s["name"]).replace("{{LOGDIR}}", os.environ.get("ICECAST_LOGDIR", "/tmp")))
    path = os.path.join(os.environ.get("ICECAST_CONFIG_DIR", "/tmp"), "icecast.xml")
    tc.write_atomic(path, xml, 0o600)
    log(f"Starting the stream server on port {port}")
    os.execvp("icecast2", ["icecast2", "-c", path])


def find_usb_audio(cards_file="/proc/asound/cards"):
    """The first USB sound card, as an ALSA device name (plughw:CARD=CODEC,DEV=0), or None.
    /proc/asound/cards lists each card as ' 1 [CODEC          ]: USB-Audio - USB AUDIO  CODEC'."""
    import re as _re
    try:
        text = open(os.environ.get("ASOUND_CARDS", cards_file)).read()
    except OSError:
        return None
    for m in _re.finditer(r"^\s*\d+\s+\[(\S+)\s*\]:\s*(\S+)\s*-\s*(.*)$", text, _re.M):
        card, driver, desc = m.groups()
        if "usb" in driver.lower():
            return f"plughw:CARD={card},DEV=0"
    return None


def resolve_device(s):
    if s["input_format"] != "alsa" or s["device"] != "auto":
        return s["device"]
    found = find_usb_audio()
    if found:
        log(f"Found a USB audio device: {found}")
        return found
    log("No USB audio device found; using the default device. Is the turntable plugged in and switched on?")
    return "default"


def capture_command(cfg):
    s, pw = cfg["station"], cfg["secrets"]["icecast"]["source_password"]
    s = {**s, "device": resolve_device(s)}
    u = urlparse(s["icecast_url"])
    if s["input_format"] == "alsa":
        source = ["-f", "alsa", "-ac", str(s["channels"]), "-ar", str(s["sample_rate"]), "-thread_queue_size", "1024", "-i", s["device"]]
    else:                                    # e.g. lavfi with a test tone, for trying the station without a turntable
        source = ["-re", "-f", s["input_format"], "-i", s["device"], "-ac", str(s["channels"]), "-ar", str(s["sample_rate"])]
    return (["ffmpeg", "-hide_banner", "-loglevel", "warning"] + source +
            ["-c:a", "libmp3lame", "-b:a", f"{s['bitrate']}k", "-content_type", "audio/mpeg", "-ice_name", s["name"],
             "-f", "mp3", f"icecast://source:{pw}@{u.hostname}:{u.port or 8000}{s['mount']}"])


def run_capture():
    cfg = wait_for_secrets()
    prepare(cfg)
    while True:
        if not wait_for_port(cfg["station"]["icecast_url"]):
            log("Waiting for the stream server...")
            continue
        log(f"Streaming {cfg['station']['device']} ({cfg['station']['input_format']}) at {cfg['station']['bitrate']} kbps")
        code = subprocess.call(capture_command(cfg))
        log(f"ffmpeg stopped (exit {code}); restarting in 5 seconds")
        time.sleep(5)
        cfg = tc.load()                      # pick up a changed device or bitrate


def run_core():
    cfg = wait_for_secrets()
    prepare(cfg)
    os.execvp(sys.executable, [sys.executable, os.path.join(HERE, "supervise.py")])


def run_web():
    cfg = tc.load()
    prepare(cfg)
    w = cfg["web"]
    proxies = ",".join(w["trusted_proxies"]) or "127.0.0.1"
    args = [sys.executable, "-m", "uvicorn", "app:app", "--app-dir", os.environ.get("TURNTABLE_WEB", "/app/web"),
            "--host", "0.0.0.0", "--port", str(w["port"]), "--proxy-headers", "--forwarded-allow-ips", proxies, "--no-server-header"]
    log(f"Starting the site on port {w['port']}")
    os.execvp(args[0], args)


def run_migrate():
    import glob
    legacy = os.environ.get("LEGACY_DIR", "/legacy")
    unified = os.path.join(legacy, "turntable")
    if os.path.isfile(os.path.join(unified, "turntable.toml")):
        # an install already on the unified config (v1.29 and later): bring it across as it is,
        # less what belonged to the old machine (its paths, its Icecast, its archive folder)
        if os.path.exists(tc.CONFIG_FILE):
            log(f"{tc.CONFIG_FILE} already exists; leaving it as it is")
            return
        cfg = tc._read(os.path.join(unified, "turntable.toml"))
        cfg.pop("paths", None)
        cfg.get("station", {}).pop("icecast_url", None)
        cfg.get("archive", {}).pop("library", None)
        os.makedirs(tc.CONFIG_DIR, exist_ok=True)
        tc.write_atomic(tc.CONFIG_FILE, tc.dumps(cfg, "# Turntable-1 settings, brought across from the previous install."))
        sec = tc._read(os.path.join(unified, "secrets.toml"))
        sec.get("icecast", {}).pop("source_password", None)      # the new stream server makes its own
        tc.write_atomic(tc.SECRETS_FILE, tc.dumps({s: t for s, t in sec.items() if t},
                                                  "# Turntable-1 secrets: passwords and tokens. Keep this file private."), 0o600)
        log(f"Brought your settings across from the previous install into {tc.CONFIG_DIR}")
        return
    changed, secrets = tc.migrate(os.path.join(legacy, "turntable-pet.toml"), os.path.join(legacy, "turntable-id.env"),
                                  extra_env=tc.read_unit_env(sorted(glob.glob(os.path.join(legacy, "*.service")))),
                                  station=tc.read_stream_unit(sorted(glob.glob(os.path.join(legacy, "*.service")))))
    log(f"Wrote {tc.CONFIG_FILE} and {tc.SECRETS_FILE} ({sum(len(t) for t in changed.values())} settings)")


ROLES = {"icecast": run_icecast, "capture": run_capture, "core": run_core, "web": run_web, "migrate": run_migrate}

if __name__ == "__main__":
    role = sys.argv[1] if len(sys.argv) > 1 else "web"
    if role not in ROLES:
        print(__doc__)
        sys.exit(2)
    ROLES[role]()
