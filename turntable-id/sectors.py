"""Which constellation a record belongs to, from its Discogs genres and styles.

Shared by the web page (the Cosmic logbook) and the pet (survey missions). The same file lives
in turntable-id/ and turntable-web/; keep the two copies identical.

Styles are more specific than genres, so they count most. A few kinds of record get their own
constellation whatever their musical genre: comedy, spoken word and stories, holiday records,
and soundtracks.
"""

# id -> label, in the order the page lists them
SECTORS = {
    "rock": "Rock", "pop": "Pop", "jazz": "Jazz", "classical": "Classical", "funk": "Funk / Soul",
    "hiphop": "Hip Hop", "electronic": "Electronic", "folk": "Folk, World & Country", "blues": "Blues",
    "screen": "Soundtracks & Scores", "comedy": "Comedy", "stories": "Stories & Spoken Word",
    "holiday": "Holiday", "other": "Uncharted reaches",
}

# Words in a style or genre that point to a constellation. Matched as substrings, lower case.
KEYWORDS = [
    ("hiphop", ("hip hop", "hip-hop", "hiphop", "rap", "trap", "boom bap", "conscious", "gangsta", "g-funk",
                "horrorcore", "grime", "cut-up", "turntablism", "bass music")),
    ("electronic", ("electronic", "synth", "downtempo", "trip hop", "darkwave", "industrial", "lo-fi", "abstract",
                    "breakbeat", "leftfield", "noise", "house", "techno", "ambient", "idm", "electro", "drum n bass")),
    ("jazz", ("jazz", "swing", "big band", "bop", "easy listening", "exotica", "space-age", "lounge")),
    ("funk", ("r&b", "rhythm & blues", "soul", "funk", "disco", "boogie", "gospel", "new jack")),
    ("rock", ("rock", "punk", "metal", "new wave", "surf", "aor", "beat", "grunge", "shoegaze", "emo", "alternative")),
    ("pop", ("pop", "ballad", "vocal", "chanson")),
    ("folk", ("folk", "country", "acoustic", "bluegrass", "world", "reggae", "dancehall", "ragga", "latin", "afro")),
    ("blues", ("blues",)),
    ("classical", ("classical", "opera", "baroque", "romantic", "contemporary classical", "choral")),
]
SCREEN = ("soundtrack", "score", "theme", "musical", "video game music", "stage & screen")
COMEDY = ("comedy", "parody", "stand-up", "satire")
STORIES = ("story", "spoken word", "audiobook", "poetry", "interview", "children's", "education")


def _has(items, words):
    return any(w in i for i in items for w in words)


def sector_of(genres: list, styles: list | None = None) -> str:
    g = [x.lower() for x in (genres or [])]
    s = [x.lower() for x in (styles or [])]
    if _has(s, COMEDY):
        return "comedy"
    if _has(s, STORIES) or "children's" in g:
        return "stories"
    if _has(s, ("holiday", "christmas")):
        return "holiday"
    if "non-music" in g and not s:
        return "stories"
    if _has(s, SCREEN) or (g == ["stage & screen"]) or ("stage & screen" in g and not s):
        return "screen"
    # Everything else: styles score 1 each, genres 0.6 (and 0.2 more for the first listed).
    score = {}
    for i, item in enumerate(s + g):
        is_style = i < len(s)
        for sector, words in KEYWORDS:
            if any(w in item for w in words):
                score[sector] = score.get(sector, 0) + (1.0 if is_style else 0.6 + (0.2 if i == len(s) else 0))
                break                     # an item counts once, for its best-fitting constellation
    if not score:
        return "other"
    return max(score.items(), key=lambda kv: kv[1])[0]


def sector_label(genres: list, styles: list | None = None) -> str:
    return SECTORS[sector_of(genres, styles)]
