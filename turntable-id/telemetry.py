#!/usr/bin/env python3
"""Listen to the stream and describe the music as it plays: level, bass/mid/treble, tempo, beats.

ffmpeg decodes the Icecast stream to 11 kHz mono; numpy analyses it about 43 times a second.
Every ~100 ms the latest readings go to a small JSON file in memory (/run/turntable), which the
web app streams to the page and the pet reads; nothing is written to the flash drive for that.
Each play's average tempo and energy are saved to the database (play_audio) every 30 s or so.

Readings (telemetry.json):
  level        0..1, loudness over the last quarter second
  db           the same in dBFS
  bass/mid/treble  0..1, each band's share against its own recent peak
  bpm          tempo estimate, or null until it's confident
  confidence   0..1, how clear the beat is
  period       seconds per beat
  last_beat    epoch time of the most recent beat (predict the next ones from period)
  music        true while there's audio above the silence floor
  t            when this reading was taken

The stream runs a second or two behind the speakers in the room; the page shifts beats by a
configurable offset to line up with what you hear.
"""
import json
import logging
import os
import sqlite3
import subprocess
import sys
import time
from collections import deque

import numpy as np
import turntable_config
CFG = turntable_config.load()

log = logging.getLogger("turntable-telemetry")

STREAM = turntable_config.stream_url(CFG)
OUT = CFG["paths"]["telemetry"]
DB_PATH = CFG["paths"]["db"]

SR = 11025                 # samples a second after decoding
N_FFT = 1024
HOP = 256                  # ~23 ms per analysis frame
FPS = SR / HOP             # ~43 frames a second
ENV_SECS = 8.0             # onset history used for tempo
BPM_MIN, BPM_MAX = 60, 190
SILENCE_DB = -50.0
WRITE_EVERY = 0.1          # seconds between readings written
SAVE_EVERY = 30            # seconds between per-play saves

FREQS = np.fft.rfftfreq(N_FFT, 1 / SR)
BANDS = {"bass": (20, 250), "mid": (250, 2000), "treble": (2000, 5500)}
BAND_IDX = {k: (FREQS >= lo) & (FREQS < hi) for k, (lo, hi) in BANDS.items()}
FLUX_IDX = FREQS < 4000
LOW_IDX = FREQS < 200          # kick and bass: where the beat usually lives
MID_IDX = (FREQS >= 300) & (FREQS < 4000)   # a snare's crack: little kick, little hi-hat
WINDOW = np.hanning(N_FFT).astype(np.float32)


class Analyser:
    def __init__(self):
        self.buf = np.zeros(N_FFT, dtype=np.float32)
        self.prev_mag = None
        self.env = deque(maxlen=int(ENV_SECS * FPS))       # onset strength per frame, all frequencies
        self.env_low = deque(maxlen=int(ENV_SECS * FPS))   # onset strength in the kick and bass
        self.env_mid = deque(maxlen=int(ENV_SECS * FPS))   # onset strength in the mids (snares)
        self.env_t = deque(maxlen=int(ENV_SECS * FPS))     # its timestamps
        self.rms = deque(maxlen=int(0.25 * FPS))
        self.peaks = {k: 1e-6 for k in BANDS}
        self.bands = {k: 0.0 for k in BANDS}
        self.bpm_hist = deque(maxlen=6)
        self.tempo = {"bpm": None, "confidence": 0.0, "period": None, "last_beat": None}
        self.frames = 0

    def reset(self):
        self.env.clear(); self.env_low.clear(); self.env_mid.clear(); self.env_t.clear(); self.bpm_hist.clear(); self.prev_mag = None
        self.tempo = {"bpm": None, "confidence": 0.0, "period": None, "last_beat": None}

    def frame(self, hop: np.ndarray, t: float):
        """Take HOP new samples (float -1..1), analyse one frame."""
        self.buf = np.concatenate([self.buf[HOP:], hop])
        x = self.buf * WINDOW
        mag = np.abs(np.fft.rfft(x))
        self.rms.append(float(np.sqrt(np.mean(hop ** 2)) + 1e-9))
        logmag = np.log1p(mag * 10)
        if self.prev_mag is not None:
            rise = np.maximum(0.0, logmag - self.prev_mag)
            self.env.append(float(np.sum(rise[FLUX_IDX])))
            self.env_low.append(float(np.sum(rise[LOW_IDX])))
            self.env_mid.append(float(np.sum(rise[MID_IDX])))
            self.env_t.append(t)
        self.prev_mag = logmag
        power = mag ** 2
        for k, idx in BAND_IDX.items():
            e = float(np.sum(power[idx]))
            self.peaks[k] = max(e, self.peaks[k] * 0.9995)        # a slowly falling peak: automatic gain
            self.bands[k] = 0.8 * self.bands[k] + 0.2 * min(1.0, (e / self.peaks[k]) ** 0.5)
        self.frames += 1
        if self.frames % int(FPS / 2) == 0:      # tempo twice a second
            self.estimate_tempo()

    @staticmethod
    def _pulses(env: np.ndarray) -> np.ndarray:
        """Onsets with the slow trend removed, scaled to unit variance."""
        k = int(FPS * 0.25)
        e = np.maximum(0.0, env - np.convolve(env, np.ones(k) / k, mode="same"))
        sd = e.std()
        return (e - e.mean()) / sd if sd > 1e-9 else np.zeros_like(e)

    @staticmethod
    def _phase(e: np.ndarray, p: float) -> float:
        """Which offset within one period (in frames) lines up best with the recent onsets."""
        n = len(e)
        best, best_phase = -1e9, 0.0
        for phase in np.arange(0, p, 0.5):
            idx = (n - 1 - phase - np.arange(0, 8) * p).round().astype(int)
            idx = idx[idx >= 0]
            sc = float(e[idx].sum())
            if sc > best:
                best, best_phase = sc, phase
        return best_phase

    def _halfway_hits(self, e: np.ndarray, p: float) -> bool:
        n = len(e)
        phase = self._phase(e, p)
        lows, mids = self._pulses(np.array(self.env_low)), self._pulses(np.array(self.env_mid))
        on = (n - 1 - phase - np.arange(0, 8) * p).round().astype(int)
        off = (n - 1 - phase - (np.arange(0, 8) + 0.5) * p).round().astype(int)
        on, off = on[on >= 1], off[off >= 1]

        def hits(idx):
            if not len(idx):
                return 0.0
            return max(float(np.mean([env[max(0, i - 1):i + 2].max() for i in idx])) for env in (lows, mids))

        a, b = hits(on), hits(off)
        return b > 0.8 and b >= 0.5 * a

    def estimate_tempo(self):
        if len(self.env) < FPS * 4:
            return
        # Two views of the onsets: kick and bass lead (that's where the beat usually is), the full
        # range supports, so dense hi-hats can back up the beat but can't impersonate it.
        low, full = self._pulses(np.array(self.env_low)), self._pulses(np.array(self.env))
        e = low + 0.5 * full
        if not e.any():
            return
        n = len(e)
        ac = np.correlate(e, e, mode="full")[n - 1:] / n
        grid = np.arange(len(ac))
        lo, hi = int(FPS * 60 / BPM_MAX), int(FPS * 60 / BPM_MIN)
        lags = np.arange(lo, min(hi, n - 1) + 1)
        if len(lags) < 3:
            return
        bpms = 60 * FPS / lags
        prior = np.exp(-0.5 * (np.log2(bpms / 115.0) / 0.9) ** 2)     # gently favour 80-160 BPM
        comb = sum(np.interp(lags * m, grid, ac, right=0.0) for m in (1, 2, 3)) / 3
        score = comb * prior
        i = int(np.argmax(score))
        lag = float(lags[i])
        if 0 < i < len(lags) - 1:                # parabolic refinement between lag steps
            a, b, c = score[i - 1], score[i], score[i + 1]
            d = (a - 2 * b + c)
            if d != 0:
                lag += 0.5 * (a - c) / d
        # The 3:2 trap: "one and a half beats" can win on busy records. If two-thirds of the winning
        # period (one real beat, in that case) correlates nearly as well, take the faster tempo.
        # A true tempo has nothing there: two-thirds of a beat falls between eighth notes.
        alt = lag * 2 / 3
        if alt >= lo and np.interp(alt, grid, ac) >= 0.8 * np.interp(lag, grid, ac):
            near = np.arange(max(lo, int(alt - 2)), int(alt + 3))
            lag = float(near[int(np.argmax(ac[near]))])
        conf = float(max(0.0, min(1.0, np.interp(lag, grid, ac) / (ac[0] + 1e-9))))
        bpm = 60 * FPS / lag
        # Half-tempo check, on this estimate alone: with a kick only on 1 and 3 the beat can read at
        # half speed. Strong hits exactly halfway between the beats found (a kick in the lows or a
        # snare in the mids) mean those are beats too. Hi-hats barely reach either band.
        if bpm < 100 and 2 * bpm <= BPM_MAX and self._halfway_hits(e, 60 * FPS / bpm):
            bpm *= 2
        # Each estimate is corrected on its own, then the reported tempo is the median of the last
        # few, so one odd estimate (a fill, a breakdown) can't take over.
        self.bpm_hist.append(bpm)
        steady = float(np.median(self.bpm_hist))
        period = 60.0 / steady
        p = period * FPS
        best_phase = self._phase(e, p)
        last_beat = self.env_t[-1] - best_phase / FPS
        self.tempo = {"bpm": round(steady, 1) if conf >= 0.15 else None, "confidence": round(conf, 2),
                      "period": round(period, 4), "last_beat": round(last_beat, 3)}

    def reading(self, t: float) -> dict:
        rms = float(np.sqrt(np.mean(np.square(self.rms)))) if self.rms else 0.0
        db = 20 * np.log10(max(rms, 1e-9))
        music = db > SILENCE_DB
        return {"t": round(t, 3), "music": bool(music), "db": round(float(db), 1),
                "level": round(float(min(1.0, max(0.0, (db - SILENCE_DB) / 45.0))), 3),
                **{k: round(v if music else 0.0, 3) for k, v in self.bands.items()},
                **(self.tempo if music else {"bpm": None, "confidence": 0.0, "period": None, "last_beat": None})}


def write(reading: dict):
    tmp = OUT + ".part"
    with open(tmp, "w") as f:
        json.dump(reading, f)
    os.replace(tmp, OUT)


class GapDetector:
    """The short silences between tracks: a drop to groove noise, well below the music's recent
    level, lasting half a second to 8 seconds, between stretches of music. Longer silences (a side
    ending, a flip) aren't gaps. A pop or click doesn't end a gap: the music must be back for a
    quarter of a second. Gaps are saved for the identifier to place track boundaries with."""
    MIN_GAP, MAX_GAP = 0.5, 8.0
    MIN_MUSIC_BEFORE = 20.0      # seconds of music before a gap counts (not a fade-in or side start)
    RESUME = 0.25

    def __init__(self):
        self.level = None        # the music's recent level, dB
        self.music_since = None
        self.quiet_since = None
        self.resume_at = None
        self.found = []

    def frame(self, hop: np.ndarray, t: float):
        db = 20 * np.log10(float(np.sqrt(np.mean(hop ** 2))) + 1e-9)
        thr = min(-38.0, (self.level if self.level is not None else -18.0) - 22.0)
        if db >= thr:
            if db > SILENCE_DB:
                self.level = db if self.level is None else self.level + (db - self.level) * 0.002
            if self.quiet_since is not None:
                if self.resume_at is None:
                    self.resume_at = t
                elif t - self.resume_at >= self.RESUME:              # the music is really back
                    dur = self.resume_at - self.quiet_since
                    long_enough = self.music_since is not None and self.quiet_since - self.music_since >= self.MIN_MUSIC_BEFORE
                    if self.MIN_GAP <= dur <= self.MAX_GAP and long_enough:
                        self.found.append((self.quiet_since, self.resume_at))
                    self.music_since = self.resume_at            # the next gap needs its own stretch of music
                    self.quiet_since, self.resume_at = None, None
            elif self.music_since is None:
                self.music_since = t
        else:
            self.resume_at = None                                  # a blip of sound inside the gap
            if self.quiet_since is None and self.music_since is not None:
                self.quiet_since = t
            elif self.quiet_since is not None and t - self.quiet_since > self.MAX_GAP:
                self.music_since = None                            # a real stop: start over

    def take(self) -> list:
        out, self.found = self.found, []
        return out


class PlaySaver:
    """Average tempo and energy for the play on the turntable, saved now and then."""

    def __init__(self):
        self.db = None
        self.play = None
        self.bpm, self.energy, self.n = [], 0.0, 0
        self.saved = 0.0

    def connect(self):
        if self.db is None and os.path.exists(DB_PATH):
            self.db = sqlite3.connect(DB_PATH, timeout=5)
            self.db.execute("CREATE TABLE IF NOT EXISTS play_audio (play_id INTEGER PRIMARY KEY, bpm REAL, "
                            "energy REAL, seconds INTEGER NOT NULL DEFAULT 0, updated_at INTEGER NOT NULL)")
            self.db.commit()
        return self.db

    def add(self, r: dict, now: float, analyser: "Analyser"):
        db = self.connect()
        if db is None:
            return
        try:
            row = db.execute("SELECT play_id FROM station WHERE id = 1 AND state = 'playing'").fetchone()
        except sqlite3.Error:
            return
        pid = row[0] if row else None
        if pid != self.play:
            self.flush(now)
            self.play, self.bpm, self.energy, self.n = pid, [], 0.0, 0
            analyser.reset()                     # a new track: start the tempo estimate afresh
        if pid and r["music"]:
            self.n += 1
            self.energy += r["level"]
            if r["bpm"]:
                self.bpm.append(r["bpm"])
        if now - self.saved > SAVE_EVERY:
            self.flush(now)

    def save_gaps(self, gaps: list):
        db = self.connect()
        if not (db and gaps):
            return
        try:
            with db:
                db.execute("CREATE TABLE IF NOT EXISTS audio_gaps (id INTEGER PRIMARY KEY, start REAL NOT NULL, end REAL NOT NULL)")
                db.execute("CREATE INDEX IF NOT EXISTS audio_gaps_end ON audio_gaps(end)")
                db.executemany("INSERT INTO audio_gaps (start, end) VALUES (?, ?)", [(round(a, 2), round(b, 2)) for a, b in gaps])
                db.execute("DELETE FROM audio_gaps WHERE end < ?", (time.time() - 14 * 86400,))
            for a, b in gaps:
                log.info("Gap between tracks: %.1f s", b - a)
        except sqlite3.Error as e:
            log.warning("Saving gaps: %s", e)

    def flush(self, now: float):
        if not (self.db and self.play and self.n):
            self.saved = now
            return
        bpm = float(np.median(self.bpm)) if len(self.bpm) >= 5 else None
        try:
            with self.db:
                self.db.execute("INSERT INTO play_audio (play_id, bpm, energy, seconds, updated_at) VALUES (?, ?, ?, ?, ?) "
                                "ON CONFLICT(play_id) DO UPDATE SET bpm = excluded.bpm, energy = excluded.energy, "
                                "seconds = excluded.seconds, updated_at = excluded.updated_at",
                                (self.play, round(bpm, 1) if bpm else None, round(self.energy / self.n, 3),
                                 int(self.n * WRITE_EVERY), int(now)))
        except sqlite3.Error as e:
            log.warning("Saving play audio: %s", e)
        self.saved = now


def main():
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    analyser, saver, gaps = Analyser(), PlaySaver(), GapDetector()
    while True:
        cmd = ["ffmpeg", "-nostdin", "-hide_banner", "-loglevel", "error", "-reconnect", "1", "-reconnect_streamed", "1",
               "-reconnect_delay_max", "5", "-i", STREAM, "-ac", "1", "-ar", str(SR), "-f", "s16le", "-"]
        proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
        log.info("Listening to %s", STREAM)
        t0 = time.time()
        frames = 0
        next_write = 0.0
        try:
            while True:
                raw = proc.stdout.read(HOP * 2)
                if len(raw) < HOP * 2:
                    break
                hop = np.frombuffer(raw, dtype="<i2").astype(np.float32) / 32768.0
                frames += 1
                # Time each frame by the audio itself (steady, unlike the bursty pipe), re-anchored to
                # the clock: pulled back if Icecast's connect burst runs it ahead, forward after a stall.
                t = t0 + frames / FPS
                wall = time.time()
                if t > wall:
                    t0 -= t - wall
                    t = wall
                elif wall - t > 1.0:
                    t0 += wall - t - 0.1
                    t = wall - 0.1
                analyser.frame(hop, t)
                gaps.frame(hop, t)
                now = time.time()
                if now >= next_write:
                    r = analyser.reading(now)
                    write(r)
                    saver.add(r, now, analyser)
                    saver.save_gaps(gaps.take())
                    next_write = now + WRITE_EVERY
        except Exception as e:
            log.warning("Analysis stopped: %s", e)
        finally:
            proc.kill()
        write({"t": round(time.time(), 3), "music": False, "level": 0.0, "db": -90.0, "bass": 0, "mid": 0, "treble": 0,
               "bpm": None, "confidence": 0.0, "period": None, "last_beat": None})
        log.warning("Stream ended; reconnecting in 5 s")
        time.sleep(5)


if __name__ == "__main__":
    sys.exit(main())
