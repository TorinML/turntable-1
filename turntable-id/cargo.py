"""Keep Beepnik talking about records in the cargo hold, not ones it imagines.

Two halves:
  options(state)  a few real records it may mention, for the prompt: its craving, what played
                  last, and some dusty or never-played ones from the collection
  check(line)     record names in an answer that aren't in the collection (quoted titles,
                  "Title by Artist", and suggestions like "try X" or "something like X")

What counts as known: every record title, track title and artist in the collection, plus
whatever played in the last 12 hours (a track playing now may be from a record you don't own,
and Beepnik may talk about it).
"""
import random
import re
import time
from difflib import SequenceMatcher

import mind

REFRESH = 600              # seconds between reloads of the collection's names

STOP = {"side", "track", "tracks", "it", "that", "this", "the", "something", "anything", "me", "you", "your",
        "my", "a", "an", "one", "another", "more", "some", "music", "records", "record", "vinyl", "everything",
        "nothing", "beep", "orbit", "space", "cargo", "hold", "monday", "tuesday", "wednesday", "thursday",
        "friday", "saturday", "sunday", "today", "tonight", "now", "again", "please", "already", "anyway",
        # capitalized mid-sentence for good reason in Beepnik's world
        "i", "i m", "i ve", "i d", "i ll", "ok", "okay", "lp", "lps", "ep", "dj", "bpm", "earth", "moon", "mars", "sun",
        "jupiter", "saturn", "venus", "mercury", "neptune", "uranus", "pluto", "milky way", "sputnik", "nasa", "houston",
        "mission control", "ground control", "apollo", "side a", "side b", "side c", "side d", "a side", "b side",
        "january", "february", "march", "april", "may", "june", "july", "august", "september", "october", "november",
        "december", "god", "lord", "mr", "mrs", "dr"}

WORD = r"[A-Z0-9][\w'\u2019$.&!?:\-]*"
SMALL = r"(?:of|the|and|in|on|a|to|for|with|de|la|le|el|y|vs|n)"
TITLE = rf"{WORD}(?:\s+(?:{WORD}|{SMALL})){{0,6}}"
BY_RE = re.compile(rf"({TITLE})\s+by\s+({WORD}(?:\s+(?:{WORD}|the|and|&)){{0,4}})")
QUOTE_RE = re.compile(r"[\"\u201c\u201d]([^\"\u201c\u201d]{2,60})[\"\u201c\u201d]")
# A whole lowercase word (optionally ending in , ; or :), then a run of capitalized words: a name
# in the middle of a sentence. The capitalized run must start right after that word, never midway.
NAME_RUN = re.compile(r"\b[a-z][a-z'\u2019]*[,;:]?\s+((?:[A-Z][\w'\u2019$.&\-]*)(?:\s+(?:[A-Z][\w'\u2019$.&\-]*|of|the|and)){0,4})")
SUGGEST_RE = re.compile(rf"\b(?:like|try|spin|play|put on|throw on|some|cue up)\s+({WORD}(?:\s+{WORD}){{0,4}})")


def norm(text: str) -> str:
    t = mind.speakable(text or "").lower()
    t = re.sub(r"['\u2019]s\b", "", t)            # possessives: Flume's -> flume
    t = re.sub(r"[^a-z0-9]+", " ", t)
    t = re.sub(r"^the ", "", t.strip())
    return re.sub(r"\s+", " ", t).strip()


class Cargo:
    def __init__(self, db):
        self.db = db
        self.loaded = 0
        self.titles, self.artists = set(), set()

    def refresh(self, now: float) -> None:
        if now - self.loaded < REFRESH:
            return
        titles, artists = set(), set()
        try:
            for title, artist in self.db.execute("SELECT title, artist FROM releases WHERE in_collection = 1"):
                titles.add(norm(title))
                for a in re.split(r"\s*(?:,|&|\band\b|\bfeat\.?|\bwith\b)\s*", artist or ""):
                    artists.add(norm(a))
                artists.add(norm(artist))
            for (title,) in self.db.execute("SELECT t.title FROM tracks t JOIN releases r USING (release_id) WHERE r.in_collection = 1"):
                titles.add(norm(title))
        except Exception:
            pass                       # no catalog yet: only recent plays count
        self.titles, self.artists = titles - {""}, artists - {""}
        self.loaded = now

    def recent(self, now: float):
        rows = self.db.execute("SELECT title, album, artist FROM plays WHERE started_at > ?", (now - 12 * 3600,)).fetchall()
        titles = {norm(t) for r in rows for t in (r[0], r[1]) if t}
        artists = {norm(r[2]) for r in rows if r[2]}
        return titles, artists

    @staticmethod
    def _matches(cand: str, pool) -> bool:
        if not cand or len(cand) < 3:
            return True                # too short to judge
        if cand in pool:
            return True
        for k in pool:
            if len(k) >= 5 and len(cand) >= 5 and (cand in k or k in cand):
                return True
            if abs(len(k) - len(cand)) <= 6 and SequenceMatcher(None, cand, k).ratio() >= 0.85:
                return True
        return False

    def check(self, line: str, extra_ok=(), now: float = None):
        """The first record name in `line` that isn't in the collection or recent plays, or None."""
        now = now or time.time()
        self.refresh(now)
        rt, ra = self.recent(now)
        titles, artists = self.titles | rt, self.artists | ra
        ok = {norm(x) for x in extra_ok}

        def known_title(t):
            n = norm(t)
            return n in ok or self._matches(n, titles)

        def known_any(t):
            n = norm(t)
            return n in ok or self._matches(n, titles) or self._matches(n, artists)

        for title, artist in BY_RE.findall(line):
            title = re.sub(r"^(?:Spin|Play|Try|Put on|Throw on|Cue up|Some|Maybe|Like)\s+", "", title.strip())
            if norm(title) in STOP:
                continue
            if not known_title(title):
                return f"{title.strip()} by {artist.strip()}"
        for q in QUOTE_RE.findall(line):
            q = q.strip(" .,!?")
            if not q or not (q[0].isupper() or q[0].isdigit()) or len(q.split()) > 6:
                continue               # a catchphrase or speech, not a title
            if not known_any(q):
                return q
        # Names in the middle of a sentence (after a lowercase word or a comma), like "kill for Radiohead".
        for run in NAME_RUN.findall(line):
            run = re.sub(r"(?:\s+(?:of|the|and))+$", "", run).strip(" .,!?")
            letters = re.sub(r"[^A-Za-z]", "", run)
            if not run or norm(run) in STOP or (letters.isupper() and letters.isalpha() and " " not in run
                                                and not any(ch.isdigit() for ch in run)):
                continue               # an everyday capitalized word, or ALL-CAPS emphasis
            if all(norm(w) in STOP for w in run.split()):
                continue
            if not known_any(run):
                return run
        for s in SUGGEST_RE.findall(line):
            words = [w for w in s.split() if norm(w) not in STOP]
            if not words:
                continue
            if not known_any(s) and not known_any(" ".join(words)):
                return s.strip()
        return None

    def options(self, state: dict, now: float, limit: int = 5) -> list:
        """A few real records it may mention: (title, artist, why)."""
        out, seen = [], set()

        def add(title, artist, why):
            k = norm(title)
            if title and k not in seen and len(out) < limit:
                seen.add(k)
                out.append((mind.speakable(title), artist, why))

        c = state.get("craving")
        if c:
            add(c["title"], c["artist"], "the record you're craving")
        row = self.db.execute("SELECT COALESCE(r.title, p.album), COALESCE(r.artist, p.artist) FROM plays p "
                              "LEFT JOIN releases r ON r.release_id = p.release_id ORDER BY p.started_at DESC LIMIT 1").fetchone()
        if row and row[0]:
            add(row[0], row[1], "played most recently")
        try:
            rows = self.db.execute(
                "SELECT r.title, r.artist, MAX(p.started_at) AS last FROM releases r LEFT JOIN plays p USING (release_id) "
                "WHERE r.in_collection = 1 GROUP BY r.release_id ORDER BY last IS NOT NULL, last LIMIT 15").fetchall()
        except Exception:
            rows = []
        rng = random.Random(int(now // 600))          # a steady pick for ten minutes, then a fresh one
        for title, artist, last in rng.sample(rows, min(len(rows), 6)):
            add(title, artist, "never played here" if last is None else "gathering dust")
        return out
