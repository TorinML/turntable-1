#!/usr/bin/env python3
"""Identify what's playing on the turntable stream, publish it as ICY metadata,
and record every play in the local SQLite log for the web UI."""
import asyncio
import logging
import os
import subprocess
import tempfile
import time
import wave

import numpy as np
import requests
from shazamio import Shazam

import edits
import infer
from store import Store
import turntable_config
CFG = turntable_config.load()

# --- Settings -------------------------------------------------------------
STREAM         = turntable_config.stream_url(CFG)
ICECAST_ADMIN  = CFG["station"]["icecast_url"].rstrip("/") + "/admin/metadata"
MOUNT          = CFG["station"]["mount"]
ADMIN_USER     = "admin"
ADMIN_PASS     = CFG["secrets"]["icecast"]["admin_password"]
HA_WEBHOOK     = CFG["secrets"]["home_assistant"]["webhook"] if CFG["home_assistant"]["enabled"] else None

SAMPLE_SECS    = CFG["identify"]["sample_seconds"]     # length of each sample sent to Shazam
INTERVAL       = CFG["identify"]["interval"]    # seconds from one check's start to the next; keep >= 20
SILENCE_RMS    = CFG["identify"]["silence_rms"]   # int16 RMS below this counts as silence; tune from logs
SILENT_LIMIT   = CFG["identify"]["silent_limit"]     # silent checks in a row before showing Idle
MISSES_LIMIT   = CFG["identify"]["misses_limit"]     # failed IDs in a row before showing the generic title
REASSERT_EVERY = CFG["identify"]["reassert_every"]    # re-send the current title every N checks (~5 min)
CARGO_ONLY     = CFG["identify"].get("cargo_only", True)   # a track not on any record you own counts as a miss
CONFIRM_HITS   = CFG["identify"]["confirm_hits"]     # checks that must agree before an unexpected track replaces the current one
RECONCILE_HOURS = CFG["identify"]["reconcile_hours"]    # how far back to re-examine after a track change or silence
IDLE_TITLE     = CFG["identify"]["idle_title"]
VINYL_TITLE    = CFG["identify"]["vinyl_title"]

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("turntable-id")


def grab_sample(path: str) -> None:
    """Record SAMPLE_SECS of the live stream to a mono WAV file."""
    subprocess.run(
        ["ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
         "-i", STREAM, "-t", str(SAMPLE_SECS), "-ac", "1", "-ar", "44100", path],
        check=True, timeout=SAMPLE_SECS + 20,
    )


def rms(path: str) -> float:
    """Loudness of the sample, used to tell silence from music."""
    with wave.open(path) as w:
        data = np.frombuffer(w.readframes(w.getnframes()), dtype=np.int16)
    if data.size == 0:
        return 0.0
    return float(np.sqrt(np.mean(data.astype(np.float64) ** 2)))


def parse(result: dict):
    """Pull the useful fields out of a Shazam response."""
    track = result.get("track")
    if not track:
        return None
    meta = {}
    for section in track.get("sections", []):
        for item in section.get("metadata", []) or []:
            if item.get("title") and item.get("text"):
                meta.setdefault(item["title"], item["text"])
    images = track.get("images", {})
    return {
        "title": track.get("title", ""),
        "artist": track.get("subtitle", ""),
        "album": meta.get("Album", ""),
        "label": meta.get("Label", ""),
        "released": meta.get("Released", ""),
        "genre": track.get("genres", {}).get("primary", ""),
        "key": str(track.get("key", "")),
        "isrc": track.get("isrc", ""),
        "cover": images.get("coverarthq") or images.get("coverart", ""),
    }


def set_icy(text: str) -> None:
    """Update the StreamTitle that Music Assistant and players display."""
    r = requests.get(
        ICECAST_ADMIN, auth=(ADMIN_USER, ADMIN_PASS), timeout=5,
        params={"mount": MOUNT, "mode": "updinfo", "song": text, "charset": "UTF-8"},
    )
    r.raise_for_status()
    log.info("StreamTitle -> %s", text)


def push_ha(payload: dict) -> None:
    """Send now-playing details to Home Assistant, if a webhook is configured."""
    if not HA_WEBHOOK:
        return
    try:
        requests.post(HA_WEBHOOK, json=payload, timeout=5)
    except requests.RequestException as e:
        log.warning("HA webhook failed: %s", e)


def safe(fn, *args):
    """Never let a play-log problem stop identification."""
    try:
        return fn(*args)
    except Exception as e:
        log.warning("Play log error: %s", e)


EMPTY = {"title": "", "artist": "", "album": "", "cover": ""}


def same_track(cur: dict, info: dict, match) -> bool:
    if match and cur.get("release_id") and match[0] == cur["release_id"] and match[1] and match[1] == cur["track_position"]:
        return True
    return (cur["title"] or "").strip().lower() == info["title"].strip().lower() and \
           (cur["artist"] or "").strip().lower() == info["artist"].strip().lower()


def expected(store: Store, cur, match) -> bool:
    """Accept on the first hit: the first owned record after a pause, or the record carrying on as it should."""
    if not match:
        return False
    if cur is None:
        return True
    return bool(cur.get("release_id") == match[0] and cur.get("track_position") and match[1]
                and infer.follows(store.db, match[0], cur["track_position"], match[1]))


class Station:
    """Decides what is playing from each check, publishes it, and keeps the record straight."""

    def __init__(self, store: Store):
        self.store = store
        self.icy = None
        self.pending = None     # an unexpected identification waiting for a second check
        self.misses = 0
        self.silent = 0

    def publish(self, title: str, info: dict = None, state: str = "playing") -> None:
        if title != self.icy:
            self.icy = title
            set_icy(title)
            push_ha(({k: info.get(k, "") for k in ("title", "artist", "album", "cover")} if info else EMPTY)
                    | {"state": state})

    def reconcile(self) -> None:
        try:
            s = infer.reconcile(self.store.db, time.time() - RECONCILE_HOURS * 3600)
            if s["inferred"] or s["quarantined"]:
                log.info("Reconciled: %d tracks inferred, %d blips quarantined", len(s["inferred"]), len(s["quarantined"]))
        except Exception as e:
            log.warning("Reconcile failed: %s", e)

    def begin(self, info: dict, match, started_at: float) -> None:
        # A gap heard just before the first check that caught this track is where it really began.
        cur = self.store.current()
        if cur:
            g = infer.gap_near(self.store.db, started_at, 40, 2, not_before=cur["started_at"] + 30)
            if g and g <= started_at + 2:
                started_at = g
        self.store.start_play(info, match=match or (None, "", ""), started_at=started_at)
        self.publish(f'{info["artist"]} - {info["title"]}', info)
        self.pending = None
        self.misses = 0
        self.reconcile()

    def on_silence(self, level: float, at: float) -> None:
        self.silent += 1
        self.misses = 0
        self.pending = None
        self.store.log_check("silence", level, at)
        log.info("Silence (%d), rms=%.0f", self.silent, level)
        if self.silent >= SILENT_LIMIT and self.icy != IDLE_TITLE:
            safe(self.store.stop, "idle")
            self.publish(IDLE_TITLE, None, "idle")
            self.reconcile()

    def on_match(self, info: dict, level: float, at: float) -> None:
        self.silent = 0
        cur = self.store.current()
        match = self.store.identify(info)
        if CARGO_ONLY and match is None and self.store.has_cargo():
            # not on any record you own: a false positive (another song, the TV, a similar track); treat it as a miss
            log.info("Heard %s - %s, which isn't in your cargo: ignored", info["artist"], info["title"])
            self.pending = None
            self.on_miss(level, at)
            return
        if cur and same_track(cur, info, match):
            if cur.get("inferred"):
                safe(self.store.confirm_inferred)
                log.info("Confirmed the inferred track: %s", info["title"])
            else:
                safe(self.store.still_playing)
            self.misses, self.pending = 0, None
            self.store.log_check("match", level, at)
            log.info("Still playing: %s (rms=%.0f)", info["title"], level)
            return

        key = (info["artist"].lower(), info["title"].lower())
        if expected(self.store, cur, match):
            self.store.log_check("match", level, at)
            self.begin(info, match, at)
            return
        if self.pending and self.pending["key"] == key:
            self.pending["hits"] += 1
        else:
            self.pending = {"key": key, "info": info, "match": match, "first_at": at, "hits": 1}
        if self.pending["hits"] >= CONFIRM_HITS:
            self.store.log_check("match", level, at)
            log.info("Confirmed on check %d: %s", self.pending["hits"], info["title"])
            self.begin(self.pending["info"], self.pending["match"], self.pending["first_at"])
        else:
            self.store.log_check("pending", level, at)
            log.info("Heard %s - %s once; waiting for a second check before switching", info["artist"], info["title"])

    def on_miss(self, level: float, at: float) -> None:
        self.silent = 0
        self.misses += 1
        self.store.log_check("nomatch", level, at)
        log.info("No match (%d), rms=%.0f", self.misses, level)
        cur = self.store.current()

        # Live forward inference: the music carries on past the track's length, so the next one is playing.
        if cur and cur.get("release_id") and cur.get("track_position"):
            tl = infer.tracklist(self.store.db, cur["release_id"])
            i = infer.index_of(tl, cur["track_position"])
            secs = tl[i]["secs"] if i is not None else None
            nxt = infer.next_track(self.store.db, cur["release_id"], cur["track_position"])
            # A gap between tracks near where the listed length runs out: the next track began there.
            # (Right away, and exactly; no waiting out the slack.)
            if secs and nxt:
                expected = cur["started_at"] + secs
                g = infer.gap_near(self.store.db, expected, max(20, secs * .12), max(20, secs * .12),
                                   not_before=cur["started_at"] + 30)
                if g and g <= time.time():
                    safe(self.store.start_inferred, cur["release_id"], nxt, g)
                    log.info("Inferred at a gap between tracks: %s %s", nxt["position"], nxt["title"])
                    self.publish(f'{cur["artist"]} - {nxt["title"]}', {"title": nxt["title"], "artist": cur["artist"],
                                                                        "album": cur.get("album", ""), "cover": ""})
                    self.misses = 0
                    return
            if secs and nxt and time.time() >= cur["started_at"] + secs + infer.SLACK:
                safe(self.store.start_inferred, cur["release_id"], nxt, cur["started_at"] + secs)
                log.info("Inferred from the tracklist: %s %s", nxt["position"], nxt["title"])
                rel_artist = cur["artist"]
                self.publish(f'{rel_artist} - {nxt["title"]}', {"title": nxt["title"], "artist": rel_artist,
                                                                  "album": cur.get("album", ""), "cover": ""})
                self.misses = 0
                return
            # Still within the track's length: keep it (the audio is clearly still going, so
            # it counts as heard), and don't give up on the record yet.
            # (No length listed on Discogs: give it about three minutes of misses.)
            if (secs is None and self.misses < MISSES_LIMIT * 3) or \
               (secs is not None and time.time() < cur["started_at"] + secs + infer.SLACK):
                safe(self.store.still_playing)
                return

        if self.misses >= MISSES_LIMIT and self.icy != VINYL_TITLE:
            safe(self.store.stop, "unknown")
            self.publish(VINYL_TITLE, None, "unknown")
            self.reconcile()


async def main() -> None:
    store = Store()
    try:                                         # plays belong to the album: fold any onto its anchor pressing
        import albums
        n = albums.fold_plays(store.db)
        if n:
            log.info("Moved %d plays onto their album's first pressing", n)
    except Exception as e:
        log.warning("Folding plays onto albums: %s", e)
    station = Station(store)
    shazam = Shazam()
    # Newer shazamio versions call it recognize(); older ones recognize_song().
    recognize = getattr(shazam, "recognize", None) or shazam.recognize_song
    station.reconcile()   # tidy anything left from before a restart

    cycles = 0
    with tempfile.TemporaryDirectory() as tmp:
        sample = os.path.join(tmp, "sample.wav")
        while True:
            started = time.monotonic()
            at = time.time()
            try:
                grab_sample(sample)
                level = rms(sample)
                if level < SILENCE_RMS:
                    station.on_silence(level, at)
                else:
                    info = parse(await recognize(sample))
                    if info:
                        station.on_match(info, level, at)
                    else:
                        station.on_miss(level, at)

                # Re-send periodically so a title set elsewhere (or lost) gets corrected.
                cycles += 1
                if station.icy and cycles % REASSERT_EVERY == 0:
                    set_icy(station.icy)
                if cycles % 360 == 0:          # about every 2 hours, pick up Discogs edits
                    infer.forget_tracklists()
            except Exception as e:
                log.warning("Cycle error: %s", e)

            # Sleep only what's left, so checks start every INTERVAL seconds, applying any
            # edits from the web page each second so they land almost immediately.
            while True:
                try:
                    edits.apply_pending(store.db, station)
                except Exception as e:
                    log.warning("Edit queue error: %s", e)
                left = INTERVAL - (time.monotonic() - started)
                if left <= 0:
                    break
                await asyncio.sleep(min(1.0, left))


if __name__ == "__main__":
    asyncio.run(main())
