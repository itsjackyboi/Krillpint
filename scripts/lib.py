"""Shared helpers for the Deep Pour data pipeline.

Canon is limited to the two files in /lore. Everything here is deterministic:
the same lore and source files always produce the same output.
"""

import json
import re
import unicodedata
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
LORE_DIR = ROOT / "lore"
SOURCE_DIR = ROOT / "scripts" / "source"
DATA_DIR = ROOT / "data"

LORE_FILES = {
    "compendium": ("Master Lore Compendium", LORE_DIR / "MASTER_LORE_COMPENDIUM.md"),
    "records": ("Hoegaarden Hall of Records", LORE_DIR / "HOEGAARDEN_HALL_OF_RECORDS.md"),
}

ARTICLES = ("the ", "a ", "an ")


def clean_lore(text):
    """Strip Markdown export noise so names match as written in prose."""
    text = text.replace("’", "'").replace("‘", "'")
    text = text.replace("“", '"').replace("”", '"')
    text = re.sub(r"\\(.)", r"\1", text)  # "\!" -> "!"
    text = text.replace("*", "")
    return text


def load_lore():
    return {key: clean_lore(path.read_text(encoding="utf-8")) for key, (_, path) in LORE_FILES.items()}


def normalize(s):
    """Player-input normalization. Must stay in sync with normalize() in app.js."""
    s = unicodedata.normalize("NFKD", s)
    s = "".join(c for c in s if not unicodedata.combining(c))
    s = s.lower().replace("’", "'").replace("‘", "'").replace("`", "'")
    s = s.replace("'", "").replace("&", " and ")
    s = re.sub(r"[^a-z0-9]+", " ", s).strip()
    for article in ARTICLES:
        if s.startswith(article) and len(s) > len(article):
            s = s[len(article):]
            break
    return s


def loose(s):
    """Normalization for checking that an evidence quote appears in the lore."""
    s = clean_lore(s).lower()
    return re.sub(r"[^a-z0-9]+", " ", s).strip()


def compile_pattern(p):
    """'re:<regex>' is used as-is. Plain text matches case-insensitively as whole words,
    with flexible whitespace and optional apostrophes."""
    if p.startswith("re:"):
        return re.compile(p[3:])
    parts = [re.escape(tok).replace("'", "'?") for tok in p.split()]
    return re.compile(r"(?<![A-Za-z0-9])" + r"\s+".join(parts) + r"(?![A-Za-z0-9])", re.IGNORECASE)


def count_patterns(entry):
    return entry.get("count") or [entry["name"], *entry.get("aliases", [])]


def mention_spans(entry, text):
    """All alias matches in text, with overlapping matches merged into one mention."""
    spans = []
    for p in count_patterns(entry):
        spans.extend(m.span() for m in compile_pattern(p).finditer(text))
    spans.sort()
    merged = []
    for start, end in spans:
        if merged and start < merged[-1][1]:
            merged[-1][1] = max(merged[-1][1], end)
        else:
            merged.append([start, end])
    return merged


def snippet(text, start, end, radius=90):
    a = max(0, start - radius)
    b = min(len(text), end + radius)
    while a > 0 and not text[a - 1].isspace():
        a -= 1
    while b < len(text) and not text[b].isspace():
        b += 1
    s = re.sub(r"\s+", " ", text[a:b]).strip()
    return ("…" if a > 0 else "") + s + ("…" if b < len(text) else "")


def load_sources():
    cats = []
    for path in sorted(SOURCE_DIR.glob("*.json")):
        data = json.loads(path.read_text(encoding="utf-8"))
        data["_path"] = path.relative_to(ROOT).as_posix()
        cats.append(data)
    return cats


def levenshtein(a, b):
    if a == b:
        return 0
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        cur = [i]
        for j, cb in enumerate(b, 1):
            cur.append(min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (ca != cb)))
        prev = cur
    return prev[-1]


def similarity(a, b):
    if not a and not b:
        return 1.0
    return 1 - levenshtein(a, b) / max(len(a), len(b))
