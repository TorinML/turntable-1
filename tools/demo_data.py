#!/usr/bin/env python3
"""A demo station for screenshots and trying Turntable-1 without a turntable: a made-up collection
(fictional artists, generated cover art), a month of listening, and a record playing now.

  demo_data.py DATA_DIR       creates DATA_DIR/turntable.db and DATA_DIR/covers/
"""
import json
import math
import os
import random
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "turntable-id"))
from PIL import Image, ImageDraw, ImageFilter

RECORDS = [
    # artist, title, year, label, genres, styles, palette, tracks
    ("Nebula Drive", "Low Orbit Lullabies", 2019, "Perigee Records", ["Electronic"], ["Ambient", "Downtempo"], ("#0b1b3a", "#3f6fb5", "#f2b544"),
     ["Countdown", "Low Orbit Lullaby", "Solar Wind", "Station Keeping", "Dark Side Radio", "Splashdown", "Re-entry", "Home Signal"]),
    ("The Velvet Static", "Signals From The Basement", 1978, "Grain Records", ["Rock"], ["Garage Rock", "Power Pop"], ("#2b1010", "#c43b3b", "#ffd28a"),
     ["Basement Tapes", "Static Heart", "Turn It Up", "Neon Alley", "Fuse Box", "Last Bus Home", "Feedback Loop", "Goodnight Amplifier"]),
    ("Mae Holloway Trio", "Blue Hour Sessions", 1961, "Lantern Jazz", ["Jazz"], ["Hard Bop", "Cool Jazz"], ("#0d2233", "#2e6f8f", "#e0c48c"),
     ["Blue Hour", "Two Lamps", "Rain On Fifth", "Holloway's Theme", "Night Ferry", "Soft Shoe", "Late Set"]),
    ("Kid Comet", "Gravity Is A Suggestion", 2021, "Orbital Sound", ["Hip Hop"], ["Boom Bap", "Conscious"], ("#1a1030", "#8a3ab9", "#ff8fc8"),
     ["Intro (Liftoff)", "Gravity", "Satellite Dreams", "No Atmosphere", "Moon Boots", "Escape Velocity", "Mission Control (Skit)", "Orbit", "Landing Lights"]),
    ("Juniper & The Pines", "Woodsmoke", 2014, "Hollow Tree", ["Folk, World, & Country"], ["Folk", "Indie Folk"], ("#1d2b1a", "#5a7a3a", "#e8d9a8"),
     ["Woodsmoke", "River Stones", "The Long Walk", "Cabin Light", "Northern Pines", "Kettle Song", "Winter Bones", "Lantern"]),
    ("Otis Fairweather", "Sunday Kind Of Soul", 1972, "Southbound", ["Funk / Soul"], ["Soul", "Southern Soul"], ("#3a1a0a", "#d9822b", "#ffe0a3"),
     ["Sunday Kind Of Love", "Sweet Talk", "Porch Light", "Hold On Brother", "Good Morning Rain", "Back To You", "Amen"]),
    ("Aurora Chamber Ensemble", "Nocturnes For The Night Shift", 2009, "Meridian Classics", ["Classical"], ["Contemporary", "Chamber Music"], ("#10131f", "#4a4f7a", "#c9cfe8"),
     ["Nocturne No. 1", "Nocturne No. 2", "Interlude For Strings", "Nocturne No. 3", "The Long Night", "Dawn Variations"]),
    ("Theo Vance", "The Lighthouse Keeper (Original Score)", 2017, "Silver Screen", ["Stage & Screen"], ["Soundtrack", "Score"], ("#0a1a24", "#2e8b9b", "#f0e6c8"),
     ["Main Title", "The Lamp", "Storm Over The Sound", "Letters", "The Keeper's Waltz", "Shipwreck", "Morning Light", "End Credits"]),
    ("Delta Rae Lowe", "Muddy Water Hymns", 1966, "Crossroads", ["Blues"], ["Delta Blues", "Electric Blues"], ("#20160a", "#8a5a2b", "#e9c27a"),
     ["Muddy Water", "Train Whistle Blues", "Hellhound Moon", "Sweet Delta", "Six Strings Down", "Levee Song", "Last Call"]),
    ("Pastel Arcade", "Cartridge Hearts", 2016, "Pixel Pop", ["Pop", "Electronic"], ["Synth-pop", "Electropop"], ("#2a0f2e", "#e8668a", "#7fe8ff"),
     ["Insert Coin", "Cartridge Hearts", "High Score", "Pastel Skies", "Two Player", "Glitch In The Love", "Continue?", "Game Over (Reprise)"]),
    ("Ironwood", "Thunder Over Basalt", 1984, "Granite", ["Rock"], ["Hard Rock", "Heavy Metal"], ("#141414", "#5a5a5a", "#e5432d"),
     ["Basalt", "Thunder Road Rider", "Iron Will", "Black Sky", "Avalanche", "Forge", "Into The Storm"]),
    ("Lena Sato", "Paper Lanterns", 2020, "Kite Records", ["Pop"], ["Indie Pop", "Dream Pop"], ("#1f1430", "#b48cff", "#ffe7a3"),
     ["Paper Lanterns", "Summer Static", "Tokyo Rain", "Daydream", "Fold Me Up", "Lantern Festival", "Night Train", "Goodbye, Kite"]),
    ("Cosmo Brass Collective", "Afterburner", 1975, "Galaxy Funk", ["Funk / Soul", "Jazz"], ["Funk", "Jazz-Funk"], ("#2a1a00", "#f2b544", "#e5432d"),
     ["Afterburner", "Get On The Good Foot", "Brass Planet", "Slow Burn", "Comet Tail", "Boogie Orbit"]),
    ("Glass Harbor", "Tidal", 2012, "Shoreline", ["Electronic"], ["Chillwave", "Downtempo"], ("#071a26", "#2e6f8f", "#9ad0ff"),
     ["Tidal", "Undertow", "Sea Glass", "Low Tide", "Harbor Lights", "Drift"]),
    ("The Midnight Choir", "Holiday Lights", 1964, "Evergreen", ["Pop"], ["Vocal", "Holiday"], ("#1a0a0a", "#a8323c", "#9fd4a8"),
     ["Deck The Halls", "Snowfall", "Silver Bells", "Fireside", "Winter Wonderland", "Auld Lang Syne"]),
    ("Raya Okafor", "Lagos To Lisbon", 2018, "Atlas World", ["Folk, World, & Country", "Jazz"], ["Afrobeat", "Fado"], ("#2a1408", "#c96a2b", "#5ac8d8"),
     ["Lagos", "Atlantic Crossing", "Saudade", "Market Day", "Lisbon Hills", "Two Homes", "Lullaby For The Ocean"]),
]


def hexrgb(h):
    h = h.lstrip("#")
    return tuple(int(h[i:i + 2], 16) for i in (0, 2, 4))


def cover(path, palette, seed):
    """Procedural cover art: a gradient, a sun or planet, and a horizon, different for each record."""
    rnd = random.Random(seed)
    W = 600
    a, b, c = (hexrgb(x) for x in palette)
    img = Image.new("RGB", (W, W))
    px = img.load()
    for y in range(W):
        t = y / W
        row = tuple(int(a[i] * (1 - t) + b[i] * t) for i in range(3))
        for x in range(W):
            px[x, y] = row
    d = ImageDraw.Draw(img, "RGBA")
    style = seed % 4
    cx, cy, r = rnd.randint(170, 430), rnd.randint(150, 330), rnd.randint(80, 150)
    if style == 0:                                   # a sun over layered hills
        d.ellipse((cx - r, cy - r, cx + r, cy + r), fill=c + (235,))
        for k in range(3):
            h = 380 + k * 60
            pts = [(x, h + 30 * math.sin(x / 90 + k + seed)) for x in range(0, W + 20, 20)] + [(W, W), (0, W)]
            d.polygon(pts, fill=tuple(int(v * (0.5 - k * 0.12)) for v in b) + (255,))
    elif style == 1:                                 # a ringed planet
        d.ellipse((cx - r, cy - r, cx + r, cy + r), fill=c + (240,))
        d.ellipse((cx - r * 1.9, cy - r * 0.35, cx + r * 1.9, cy + r * 0.35), outline=c + (200,), width=10)
        for _ in range(60):
            x, y = rnd.randint(0, W), rnd.randint(0, W)
            d.ellipse((x, y, x + 3, y + 3), fill=(255, 255, 255, rnd.randint(80, 200)))
    elif style == 2:                                 # concentric grooves
        for k in range(12, 0, -1):
            rr = k * 26
            col = c if k % 2 else b
            d.ellipse((W / 2 - rr, W / 2 - rr, W / 2 + rr, W / 2 + rr), fill=col + (200 if k % 2 else 255,))
    else:                                            # stripes and a moon
        for k in range(8):
            y = 300 + k * 38
            d.rectangle((0, y, W, y + 18), fill=c + (110 + k * 15,))
        d.ellipse((cx - r * 0.7, 80, cx + r * 0.7, 80 + r * 1.4), fill=(255, 245, 225, 230))
    img = img.filter(ImageFilter.GaussianBlur(0.6))
    img.save(path, quality=88)


def main(data):
    from store import Store
    os.makedirs(os.path.join(data, "covers"), exist_ok=True)
    st = Store(os.path.join(data, "turntable.db"), os.path.join(data, "covers"))
    db = st.db
    rnd = random.Random(42)
    now = int(time.time())
    lengths = {}
    with db:
        for i, (artist, title, year, label, genres, styles, pal, tracks) in enumerate(RECORDS):
            rid = 9100 + i
            cover(os.path.join(data, "covers", f"demo-{rid}.jpg"), pal, i)
            db.execute("INSERT OR REPLACE INTO releases (release_id, title, artist, year, label, catno, format, genres, styles, cover_file, date_added, "
                       "in_collection, tracks_synced, synced_at, original_year) VALUES (?, ?, ?, ?, ?, ?, 'Vinyl, LP', ?, ?, ?, ?, 1, 1, ?, ?)",
                       (rid, title, artist, year + rnd.choice([0, 0, 0, 20, 45]), label, f"DEMO-{100 + i}", json.dumps(genres), json.dumps(styles),
                        f"demo-{rid}.jpg", time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime(now - rnd.randint(60, 900) * 86400)), now, year))
            half = (len(tracks) + 1) // 2
            for k, t in enumerate(tracks):
                side = "A" if k < half else "B"
                secs = rnd.randint(150, 330)
                pos = f"{side}{(k if side == 'A' else k - half) + 1}"
                lengths[(rid, pos)] = (secs, t, side)
                db.execute("INSERT OR REPLACE INTO tracks (release_id, seq, position, side, title, duration) VALUES (?, ?, ?, ?, ?, ?)",
                           (rid, k + 1, pos, side, t, f"{secs // 60}:{secs % 60:02d}"))

        def play_side(rid, side, t0, upto=None):
            """Play one side through from t0; upto stops partway (the record playing now)."""
            artist, title = RECORDS[rid - 9100][0], RECORDS[rid - 9100][1]
            t = t0
            for (r, pos), (secs, name, s) in sorted(lengths.items(), key=lambda kv: (kv[0][0], kv[0][1])):
                if r != rid or s != side:
                    continue
                if upto is not None and t > upto:
                    break
                end = t + secs
                live = upto is not None and end > upto
                db.execute("INSERT INTO plays (started_at, last_seen_at, ended_at, artist, title, album, release_id, track_position, side, inferred, cover_file) "
                           "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                           (t, min(end, upto) if live else end, None if live else end, artist, name, title, rid, pos, side,
                            1 if rnd.random() < 0.12 and not live else 0, f"demo-{rid}.jpg"))
                if live:
                    return db.execute("SELECT MAX(id) FROM plays").fetchone()[0], t
                t = end + 2
            return None, t

        # a month of evenings, most of them with a record or three
        favorites = [9100, 9103, 9102, 9109, 9105, 9113, 9111]
        for day in range(29, 0, -1):
            if rnd.random() < 0.35:
                continue
            t = now - day * 86400
            t = t - (t % 86400) + rnd.choice([17, 18, 19, 20, 21]) * 3600 + rnd.randint(0, 3000) + time.timezone
            for _ in range(rnd.choice([1, 1, 2, 2, 3])):
                rid = rnd.choice(favorites if rnd.random() < 0.6 else list(range(9100, 9100 + len(RECORDS) - 3)))
                for side in ("A", "B") if rnd.random() < 0.7 else ("A",):
                    _, t = play_side(rid, side, t)
                    t += rnd.randint(40, 90)
                t += rnd.randint(120, 600)
        # tonight: one record played, and Low Orbit Lullabies spinning now (side A, track 3)
        _, t = play_side(9102, "A", now - 3600)
        a1 = lengths[(9100, "A1")][0] + lengths[(9100, "A2")][0]
        pid, started = play_side(9100, "A", now - a1 - 95, upto=now)
        db.execute("UPDATE station SET state = 'playing', play_id = ?, updated_at = ?", (pid, now))
    db.commit()
    return db, pid


if __name__ == "__main__":
    db, pid = main(sys.argv[1] if len(sys.argv) > 1 else "./demo-data")
    print("records:", db.execute("SELECT COUNT(*) FROM releases").fetchone()[0], "| plays:", db.execute("SELECT COUNT(*) FROM plays").fetchone()[0],
          "| playing now: play", pid)
