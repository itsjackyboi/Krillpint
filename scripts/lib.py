"""Shared helpers for the Deep Pour data pipeline.

Canon is limited to the two files in /lore. Everything here is deterministic:
the same lore and source files always produce the same output.
"""

import functools
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


@functools.lru_cache(maxsize=None)
def compile_pattern(p):
    """'re:<regex>' is used as-is. Plain text matches case-insensitively as whole words,
    with flexible whitespace and optional apostrophes."""
    if p.startswith("re:"):
        return re.compile(p[3:])
    parts = [re.escape(tok).replace("'", "'?") for tok in p.split()]
    return re.compile(r"(?<![A-Za-z0-9])" + r"\s+".join(parts) + r"(?![A-Za-z0-9])", re.IGNORECASE)


def count_patterns(entry):
    return entry.get("count") or [entry["name"], *entry.get("aliases", [])]


_span_cache = {}


def mention_spans(entry, text):
    """All alias matches in text, with overlapping matches merged into one mention."""
    key = (tuple(count_patterns(entry)), id(text))
    if key not in _span_cache:
        _span_cache[key] = _mention_spans(key[0], text)
    return [list(s) for s in _span_cache[key]]


def _mention_spans(patterns, text):
    spans = []
    for p in patterns:
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


def load_sources(lore=None):
    """Load every question in scripts/source and expand its answers.

    Two shortcuts keep the hand-written files short and consistent:
      {"ref": "Gideon Drake"}  copies the answer of that name from wherever it is first
                               defined (aliases, count patterns, evidence); other keys
                               in the ref override the copy.
      "derive": {...}          builds the answer list from the other questions instead
                               of listing it. See derive_answers().
    Unresolved refs are kept as {"name": ref, "_error": ...} so validation can report them.
    """
    cats = []
    for path in sorted(SOURCE_DIR.glob("*.json")):
        data = json.loads(path.read_text(encoding="utf-8"))
        data["_path"] = path.relative_to(ROOT).as_posix()
        cats.append(data)

    registry = {}
    for cat in cats:
        for a in cat.get("answers", []):
            if "ref" not in a:
                registry.setdefault(normalize(a["name"]), a)
    for cat in cats:
        resolved = []
        for a in cat.get("answers", []):
            if "ref" in a:
                base = registry.get(normalize(a["ref"]))
                if base is None:
                    resolved.append({"name": a["ref"], "_error": f"ref {a['ref']!r} matches no answer"})
                    continue
                extra = {k: v for k, v in a.items() if k != "ref"}
                resolved.append({**base, **extra})
            else:
                resolved.append(a)
        cat["answers"] = resolved

    derived = [c for c in cats if "derive" in c]
    if derived:
        lore = lore or load_lore()
        authored = [c for c in cats if "derive" not in c]
        for cat in derived:
            cat["answers"] = derive_answers(cat["derive"], authored, lore)
    return cats


def derive_answers(rule, authored, lore):
    """Answer list for a rule-based question, pooled from the hand-written questions.

    rule keys:
      scope       list of question ids to pool from (default: all hand-written questions)
      letter      keep answers whose name starts with this letter (ignoring "The"/"A");
                  aliases that start with another letter are dropped
      compendium  exact mention count required in the Master Lore Compendium
      records_min minimum mention count in the Hall of Records
      total       exact mention count required across both docs
      exclude     names to leave out (e.g. groups or monsters in a "character" question)
      rank_by     "spread": rank by how many questions an answer appears in instead of
                  by mentions (for pools where every answer has the same mention count)
    """
    scope = set(rule.get("scope") or [c["id"] for c in authored])
    pool = {}  # first name seen -> merged answer
    owner = {}  # any normalized name or alias -> pool key
    for cat in authored:
        if cat["id"] not in scope:
            continue
        for a in cat["answers"]:
            if a.get("_error"):
                continue
            keys = [normalize(x) for x in [a["name"], *a.get("aliases", [])]]
            hits = list(dict.fromkeys(owner[k] for k in keys if k in owner))  # same thing under other names
            if not hits:
                hit = keys[0]
                pool[hit] = {**a, "aliases": list(a.get("aliases", [])), "count": count_patterns(a), "_spread": set()}
            else:
                hit = hits[0]
                for other in hits[1:]:  # this answer bridges two pooled ones: fold them together
                    gone = pool.pop(other)
                    pool[hit]["aliases"] += [gone["name"], *gone["aliases"]]
                    pool[hit]["count"] += gone["count"]
                    pool[hit]["_spread"] |= gone["_spread"]
                    for k, v in owner.items():
                        if v == other:
                            owner[k] = hit
                pool[hit]["aliases"] += [a["name"], *a.get("aliases", [])]
                pool[hit]["count"] += count_patterns(a)
            merged = pool[hit]
            merged["count"] = list(dict.fromkeys(merged["count"]))
            merged["aliases"] = [x for x in dict.fromkeys(merged["aliases"]) if normalize(x) != normalize(merged["name"])]
            merged["_spread"].add(cat["id"])
            for k in keys:
                owner.setdefault(k, hit)
    excluded = {normalize(x) for x in rule.get("exclude", [])}
    out = []
    for key, a in sorted(pool.items()):
        if key in excluded:
            continue
        if "letter" in rule:
            letter = rule["letter"].lower()
            if not key.startswith(letter):
                continue
            # Off-letter aliases can't be typed for this question but still count as mentions.
            a = {**a, "aliases": [x for x in a["aliases"] if normalize(x).startswith(letter)]}
        if any(k in rule for k in ("compendium", "records_min", "total")):
            per_doc = {k: len(mention_spans(a, text)) for k, text in lore.items()}
            if "compendium" in rule and per_doc["compendium"] != rule["compendium"]:
                continue
            if "records_min" in rule and per_doc["records"] < rule["records_min"]:
                continue
            if "total" in rule and sum(per_doc.values()) != rule["total"]:
                continue
        a = {**a, "_spread": len(a["_spread"])}
        out.append(a)
    return out


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
