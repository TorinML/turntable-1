"""Move Turntable-1's stylesheets and scripts from the old navy palette to Nebula.

Each colour is mapped by its hue family, keeping its lightness so contrast and hierarchy survive:
navy -> indigo, steel -> lilac grey, gold -> starlight, red -> comet pink, cyan -> sky, green -> mint.
Black, white and creams are left alone. Run: python3 nebula_remap.py [--dry] FILE..."""
import colorsys
import re
import sys

KEEP = {"#000", "#000000", "#fff", "#ffffff"}


def transform(r, g, b):
    h, l, s = colorsys.rgb_to_hls(r / 255, g / 255, b / 255)
    H, L, S = h * 360, l * 100, s * 100
    if S < 6 and (L < 8 or L > 97):                        # black and white
        return r, g, b
    if 170 <= H <= 240 and L < 18:                         # near-black navies and teals -> deep indigo
        H, S = 248, min(70, S * .9 + 10)
    elif 190 <= H <= 240 and S < 60 and L < 40:            # navy backgrounds and panels -> indigo
        H, S = 248, min(70, S * 1.05 + 6)
    elif 190 <= H <= 240 and S < 45:                       # steel greys and near-whites -> lilac
        H = 254
        S = min(32, S * 1.2 + 6) if L < 60 else min(48, S * 1.4 + 8)
        if L > 85:
            S = 80
    elif 170 <= H < 200 and S >= 45 and L >= 40:           # cyan -> sky
        H, S, L = 222, 100, max(L, 70)
    elif 170 <= H < 200 and S >= 30:                       # dark teals -> indigo
        H, S = 248, min(60, S)
    elif 200 <= H <= 240 and S >= 45:                      # bright blues -> sky (soft below mid lightness)
        if L >= 60:
            H, S = 222, 100
        else:
            H, S = 232, min(S, 60)
    elif 90 <= H < 160 and S > 30:                         # greens -> mint
        H, S = 156, min(S, 62)
    elif (H < 16 or H >= 335) and S > 45:                  # signal red -> comet pink
        H, S, L = 346, 100, max(L, 68)
    elif 300 <= H < 335 and S > 40:                        # pinks -> comet pink
        H, S = 346, 100
    elif 16 <= H < 30 and S > 45 and L > 40:               # oranges -> peach
        H, S, L = 24, 100, max(L, 70)
    elif 30 <= H <= 52 and L < 34:                         # dark ambers (old radar lines) -> indigo lines
        H, S, L = 248, 42, L + 6
    elif 30 <= H <= 52 and S > 55 and L < 90:              # gold -> starlight
        H, S, L = 41, 100, min(86, L + (100 - L) * 0.28)
    elif 250 <= H < 300 and S > 40:                        # purples -> lilac
        H, S = 252, min(S, 66)
    else:
        return r, g, b                                     # creams and anything unclassified
    rr, gg, bb = colorsys.hls_to_rgb((H % 360) / 360, L / 100, S / 100)
    return round(rr * 255), round(gg * 255), round(bb * 255)


def hexmap(m):
    h = m.group(0)
    if h.lower() in KEEP:
        return h
    s = h[1:]
    if len(s) == 3:
        s = "".join(c * 2 for c in s)
    r, g, b = (int(s[i:i + 2], 16) for i in (0, 2, 4))
    return "#%02x%02x%02x" % transform(r, g, b)


def rgbmap(m):
    r, g, b = (int(x) for x in m.group(2, 3, 4))
    if (r, g, b) in ((0, 0, 0), (255, 255, 255)):
        return m.group(0)
    nr, ng, nb = transform(r, g, b)
    return f"{m.group(1)}({nr}, {ng}, {nb}"


HEX = re.compile(r"#[0-9a-fA-F]{6}\b|#[0-9a-fA-F]{3}\b")
RGB = re.compile(r"(rgba?)\( *(\d+) *, *(\d+) *, *(\d+)")

if __name__ == "__main__":
    dry = "--dry" in sys.argv
    for path in [a for a in sys.argv[1:] if not a.startswith("--")]:
        text = open(path).read()
        if dry:
            for h in sorted({m.group(0).lower() for m in HEX.finditer(text)}):
                new = HEX.sub(hexmap, h)
                if new != h:
                    print(f"  {h} -> {new}")
            continue
        out = RGB.sub(rgbmap, HEX.sub(hexmap, text))
        open(path, "w").write(out)
        print(f"{path}: remapped")
