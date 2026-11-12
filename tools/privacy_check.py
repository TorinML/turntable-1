#!/usr/bin/env python3
"""Check a Turntable-1 checkout (and, with --history, every commit in its git history) for anything
that shouldn't be public: API keys and tokens, passwords, Wi-Fi settings, private keys, private IP
addresses, local hostnames, email addresses, home-directory paths, image metadata, and files that
don't belong in a public repository.

    python3 tools/privacy_check.py                       # the working tree
    python3 tools/privacy_check.py --history             # plus every version of every file ever committed
    python3 tools/privacy_check.py --also "my-ssid,192.168.7.20,mynas.local"   # your own values too

Exits with 1 if anything is found, so it can run in CI. Known, intended values (the repo's own
GitHub owner, documentation-only example addresses) are listed in ALLOWED below."""
import argparse
import os
import re
import subprocess
import sys

# --- what counts as a problem ----------------------------------------------------------------------------
PATTERNS = [
    # keys and tokens from the services this project touches, and common others
    ("Google API key (Gemini)", r"AIza[0-9A-Za-z_\-]{30,}"),
    ("Groq API key", r"gsk_[0-9A-Za-z]{20,}"),
    ("OpenRouter API key", r"sk-or-v1-[0-9a-f]{20,}"),
    ("OpenAI-style API key", r"\bsk-(?:proj-)?[0-9A-Za-z_\-]{30,}"),
    ("Anthropic API key", r"sk-ant-[0-9A-Za-z_\-]{20,}"),
    ("GitHub token", r"\b(?:ghp|gho|ghu|ghs|ghr|github_pat)_[0-9A-Za-z_]{20,}"),
    ("Slack token", r"\bxox[abprs]-[0-9A-Za-z\-]{10,}"),
    ("AWS access key", r"\bAKIA[0-9A-Z]{16}\b"),
    ("JSON web token (e.g. a Home Assistant long-lived token)", r"\beyJ[0-9A-Za-z_\-]{10,}\.eyJ[0-9A-Za-z_\-]{10,}\.[0-9A-Za-z_\-]{10,}"),
    ("Home Assistant webhook URL", r"/api/webhook/[0-9A-Za-z_\-]{6,}"),
    ("Private key", r"-----BEGIN (?:RSA |EC |OPENSSH |DSA |PGP )?PRIVATE KEY"),
    # assignments of secret-looking values
    ("Secret assigned a value", r"(?i)\b(?:password|passwd|pass|secret|token|api_?key|webhook|psk|wpa_passphrase|session_key|admin_password_hash)\b"
                                r"[ \t]*[=:][ \t]*[\"']?(?![\"'<\[{$%(]|your|my_|change|example|xxx|none|null|true|false|\*|\.\.\.)"
                                r"(?![A-Za-z_]+\.[A-Za-z_]+\b)(?![A-Z][A-Za-z]+\()[^\s\"',;()]{6,}(?![\w.]*\()"),
    ("Discogs-style token assignment", r"(?i)discogs[_ ]?token\s*[=:]\s*[\"']?[A-Za-z]{10,}[0-9][A-Za-z0-9]{10,}"),
    ("Wi-Fi settings", r"(?i)\b(?:ssid|wifi_?pass(?:word)?|wifi_?ssid)\b\s*[=:]\s*[\"'][^\"']{2,}[\"']"),
    # where things live
    ("Private IP address", r"\b(?:10\.\d{1,3}|172\.(?:1[6-9]|2\d|3[01])|192\.168)\.\d{1,3}\.\d{1,3}\b"),
    ("Local hostname", r"\b[a-z0-9][a-z0-9\-]{1,40}\.(?:local|lan|home|internal|localdomain|home\.arpa)\b"),
    ("Email address", r"\b[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}\b"),
    ("Home-directory path", r"(?:/home/[a-z][a-z0-9_\-]+|/Users/[A-Za-z][A-Za-z0-9_\-]+|C:\\\\?Users\\\\?[A-Za-z][A-Za-z0-9_\-]+)"),
    ("Windows SDK path (Android local.properties)", r"(?i)sdk\.dir\s*="),
]

# --- what's intended (documentation examples, the project's own public identity) -----------------
ALLOWED = [
    r"192\.168\.0\.\d{1,3}",                   # documentation examples use 192.168.0.x
    r"10\.0\.0\.\d{1,3}",                      # and 10.0.0.x
    r"\b(?:raspberrypi|turntable|homeassistant|ollama-box)\.local\b",   # generic example names
    r"you@example\.com|[a-z.]+@example\.(?:com|org)|noreply@github\.com|\d+\+[A-Za-z0-9\-]+@users\.noreply\.github\.com",
    r"\bgit@github\.com\b",
    r"/home/claude\b",                         # never present, but harmless if it were
    r"/api/webhook/(?:turntable|example|YOUR)",
    r"Pass the|password (?:from|you|is)",      # prose
    r"secret:[a-z_]+\.[a-z_]+",                # the settings layout naming a secret, not containing one
    r"astigma@astigmatic\.com",                # the Righteous font's designer, in its open-font license
]
BINARY_EXT = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".ico", ".ttf", ".woff", ".woff2", ".apk", ".jar", ".zip", ".gz", ".mp3", ".wav", ".flac"}
NEVER_PUBLIC = [
    (r"(^|/)\.env$", "a real .env file (only .env.example belongs in the repo)"),
    (r"(^|/)secrets\.toml$", "a secrets file"),
    (r"(^|/)turntable\.toml$", "a real settings file"),
    (r"(^|/)local\.properties$", "Android local.properties (has your SDK path)"),
    (r"\.(?:jks|keystore|p12|pem|key)$", "a signing key or certificate"),
    (r"(^|/)google-services\.json$", "Firebase config"),
    (r"(^|/)(?:turntable\.db|.*\.sqlite3?|.*\.db)$", "a database"),
    (r"(^|/)\.idea/|(^|/)\.gradle/|(^|/)build/|(^|/)__pycache__/|\.pyc$", "build output or editor files"),
    (r"(^|/)\.DS_Store$|(^|/)Thumbs\.db$", "OS metadata"),
]


def text_of(path_or_bytes):
    b = path_or_bytes if isinstance(path_or_bytes, bytes) else open(path_or_bytes, "rb").read()
    return b.decode("utf-8", "replace")


def scan_text(name, text, extra, findings):
    allowed = re.compile("|".join(ALLOWED))
    for label, pat in PATTERNS + [(f"Your value: {v}", re.escape(v)) for v in extra]:
        flags = re.I if label.startswith("Your value") else 0
        for m in re.finditer(pat, text, flags):
            hit = m.group(0)
            if allowed.search(hit) or allowed.search(text[max(0, m.start() - 12):m.end() + 12]) and not label.startswith("Your value"):
                continue
            line = text.count("\n", 0, m.start()) + 1
            ctx = text[text.rfind("\n", 0, m.start()) + 1:text.find("\n", m.end()) if text.find("\n", m.end()) != -1 else len(text)].strip()
            findings.append((name, line, label, ctx[:140]))


def image_metadata(name, data, findings):
    """GPS, device, software and author tags in images (screenshots and photos can carry them)."""
    try:
        from PIL import Image
        import io
        im = Image.open(io.BytesIO(data))
        exif = im.getexif() if hasattr(im, "getexif") else {}
        tags = {34853: "GPS location", 271: "camera make", 272: "camera model", 305: "software", 315: "author", 33432: "copyright", 40092: "comment"}
        found = [label for k, label in tags.items() if exif.get(k)]
        info = {k: v for k, v in (im.info or {}).items() if k.lower() in ("author", "comment", "description", "software", "xml:com.adobe.xmp", "exif")}
        if found or info:
            findings.append((name, 0, "Image metadata", ", ".join(found + sorted(info))[:140]))
    except Exception:
        pass


def scan_tree(root, extra):
    findings = []
    for base, dirs, files in os.walk(root):
        dirs[:] = [d for d in dirs if d != ".git"]
        for f in files:
            path = os.path.join(base, f)
            rel = os.path.relpath(path, root).replace(os.sep, "/")
            if rel.startswith("tools/privacy_check.py"):
                continue
            for pat, why in NEVER_PUBLIC:
                if re.search(pat, rel):
                    findings.append((rel, 0, "Shouldn't be public", why))
            data = open(path, "rb").read()
            ext = os.path.splitext(f)[1].lower()
            if ext in {".png", ".jpg", ".jpeg", ".webp"}:
                image_metadata(rel, data, findings)
            if ext in BINARY_EXT:
                # binaries: only look for your own values and keys, as raw bytes
                scan_text(rel + " (binary)", data.decode("latin-1"), extra, [x for x in []])
                raw = data.decode("latin-1")
                for v in extra:
                    if v.lower() in raw.lower():
                        findings.append((rel, 0, f"Your value: {v}", "inside a binary file"))
                continue
            scan_text(rel, text_of(data), extra, findings)
    return findings


def scan_history(root, extra):
    """Every version of every file in every commit on every branch and tag (deleted files included)."""
    findings, seen = [], set()
    revs = subprocess.run(["git", "-C", root, "rev-list", "--all"], capture_output=True, text=True).stdout.split()
    for rev in revs:
        listing = subprocess.run(["git", "-C", root, "ls-tree", "-r", rev], capture_output=True, text=True).stdout.splitlines()
        for row in listing:
            meta, path = row.split("\t", 1)
            blob = meta.split()[2]
            if blob in seen or path.startswith("tools/privacy_check.py"):
                continue
            seen.add(blob)
            for pat, why in NEVER_PUBLIC:
                if re.search(pat, path):
                    findings.append((f"{rev[:8]}:{path}", 0, "Shouldn't be public", why))
            data = subprocess.run(["git", "-C", root, "cat-file", "-p", blob], capture_output=True).stdout
            ext = os.path.splitext(path)[1].lower()
            if ext in {".png", ".jpg", ".jpeg", ".webp"}:
                image_metadata(f"{rev[:8]}:{path}", data, findings)
            if ext in BINARY_EXT:
                for v in extra:
                    if v.lower() in data.decode("latin-1").lower():
                        findings.append((f"{rev[:8]}:{path}", 0, f"Your value: {v}", "inside a binary file"))
                continue
            scan_text(f"{rev[:8]}:{path}", text_of(data), extra, findings)
    # commit messages and author emails
    log = subprocess.run(["git", "-C", root, "log", "--all", "--format=%H%x09%an <%ae>%x09%s%n%b"], capture_output=True, text=True).stdout
    scan_text("commit messages", log, extra, findings)
    return findings, len(revs), len(seen)


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("root", nargs="?", default=".")
    ap.add_argument("--history", action="store_true", help="also scan every commit in the git history")
    ap.add_argument("--also", default="", help="comma-separated values of your own to look for (IPs, names, SSIDs, domains)")
    a = ap.parse_args()
    extra = [v.strip() for v in a.also.split(",") if v.strip()]
    findings = scan_tree(a.root, extra)
    print(f"Working tree: {len(findings)} finding(s)")
    if a.history:
        hist, n_commits, n_blobs = scan_history(a.root, extra)
        print(f"History: {n_commits} commits, {n_blobs} file versions, {len(hist)} finding(s)")
        findings += hist
    seen = set()
    for name, line, label, ctx in findings:
        if label.startswith("Your value"):
            # never print your own values, not even in a match: CI logs on a public repo are public
            k = next((i for i, v in enumerate(extra) if label.endswith(v)), 0)
            for v in extra:
                ctx = re.sub(re.escape(v), "\u2022" * 6, ctx, flags=re.I)
            label = f"Your value #{k + 1}"
        key = (name.split(":", 1)[-1] if ":" in name else name, label, ctx)
        if key in seen:
            continue
        seen.add(key)
        print(f"  {name}{':' + str(line) if line else ''}  [{label}]  {ctx}")
    if not findings:
        print("Nothing found.")
    sys.exit(1 if findings else 0)


if __name__ == "__main__":
    main()
