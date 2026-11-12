#!/usr/bin/env python3
"""Turntable-1's core container: runs the background workers and keeps them running.

  identify    turntable_id.py   what's playing, the play log, the stream's titles
  beepnik     pet_service.py    the companion, Ground Control
  telemetry   telemetry.py      live level, bands and tempo
  recorder    recorder.py       session recordings ([recording] enabled)
  archive     archive.py        album tracks from the recordings ([archive] enabled)

and the Discogs sync, nightly at [discogs] sync_time (and right away on a fresh install).
A worker that stops is restarted, waiting longer each time it keeps failing (up to a minute).
"""
import datetime
import os
import signal
import sqlite3
import subprocess
import sys
import threading
import time

CORE = os.environ.get("TURNTABLE_CORE", "/app/core")
sys.path.insert(0, CORE)
import turntable_config as tc

running = True
procs = {}


def log(name, msg):
    print(f"[{name}] {msg}", flush=True)


def pipe(name, stream):
    for line in iter(stream.readline, ""):
        print(f"[{name}] {line.rstrip()}", flush=True)


def start(name, script):
    p = subprocess.Popen([sys.executable, "-u", os.path.join(CORE, script)], cwd=CORE, stdout=subprocess.PIPE,
                         stderr=subprocess.STDOUT, text=True, bufsize=1)
    threading.Thread(target=pipe, args=(name, p.stdout), daemon=True).start()
    return p


def keep(name, script):
    """Run a worker for as long as the container runs."""
    delay = 5
    while running:
        began = time.time()
        p = start(name, script)
        procs[name] = p
        code = p.wait()
        if not running:
            break
        delay = 5 if time.time() - began > 300 else min(60, delay * 2)    # a long healthy run resets the wait
        log(name, f"stopped (exit {code}); restarting in {delay} s")
        time.sleep(delay)


def collection_empty(cfg) -> bool:
    try:
        db = sqlite3.connect(cfg["paths"]["db"], timeout=5)
        n = db.execute("SELECT COUNT(*) FROM releases").fetchone()[0]
        db.close()
        return n == 0
    except sqlite3.Error:
        return True


def discogs_schedule():
    """The nightly sync at [discogs] sync_time; right away if the collection hasn't been synced yet."""
    first = True
    while running:
        cfg = tc.load()
        ready = cfg["discogs"]["user"] and cfg["secrets"]["discogs"]["token"]
        if ready and first and collection_empty(cfg):
            log("discogs", "First sync of your collection")
            run_sync()
        first = False
        hh, mm = (int(x) for x in cfg["discogs"]["sync_time"].split(":"))
        now = datetime.datetime.now()
        nxt = now.replace(hour=hh, minute=mm, second=0, microsecond=0)
        if nxt <= now:
            nxt += datetime.timedelta(days=1)
        while running and datetime.datetime.now() < nxt:
            time.sleep(min(60, max(1, (nxt - datetime.datetime.now()).total_seconds())))
        if running and ready:
            run_sync()


def run_sync():
    p = start("discogs", "discogs_sync.py")
    procs["discogs"] = p
    p.wait()
    procs.pop("discogs", None)


def stop(*_):
    global running
    running = False
    for p in list(procs.values()):
        if p.poll() is None:
            p.terminate()
    end = time.time() + 15
    for p in list(procs.values()):
        try:
            p.wait(timeout=max(0.1, end - time.time()))
        except subprocess.TimeoutExpired:
            p.kill()
    sys.exit(0)


def main():
    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    cfg = tc.load()
    workers = [("identify", "turntable_id.py"), ("beepnik", "pet_service.py"), ("telemetry", "telemetry.py")]
    if cfg["recording"]["enabled"]:
        workers.append(("recorder", "recorder.py"))
    if cfg["archive"]["enabled"]:
        workers.append(("archive", "archive.py"))
    log("core", "Starting " + ", ".join(n for n, _ in workers))
    for name, script in workers:
        threading.Thread(target=keep, args=(name, script), daemon=True).start()
    threading.Thread(target=discogs_schedule, daemon=True).start()
    while running:
        time.sleep(1)


if __name__ == "__main__":
    main()
