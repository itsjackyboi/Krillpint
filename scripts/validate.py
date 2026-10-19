"""Validate the category sources against the lore.

Errors (exit 1):
  - the same normalized name or alias on two answers in one category (duplicate)
  - an answer that isn't inferred but has zero mentions in the lore (no evidence)
  - an inferred answer with no quote, or a quote that isn't in the lore
  - a gem that isn't one of the category's answers
  - a count pattern that doesn't compile
Warnings:
  - near-duplicate answers in one category (similar names or aliases)
  - an explicit count pattern that never matches
  - a category under the minimum answer count
A category can list intentionally similar answers under "distinct": [["A", "B"]].

Usage: python3 scripts/validate.py
"""

import re
import sys
from itertools import combinations

from lib import compile_pattern, count_patterns, load_lore, load_sources, loose, mention_spans, normalize, similarity

NEAR_DUPLICATE = 0.85
MIN_ANSWERS = 18  # approved floor for smaller questions; 25 is the goal


def main():
    lore = load_lore()
    corpus = " ".join(loose(t) for t in lore.values())
    errors, warnings = [], []

    for cat in load_sources():
        where = cat["_path"]
        answers = cat["answers"]
        names = [a["name"] for a in answers]

        if cat.get("gem") not in names:
            errors.append(f"{where}: gem {cat.get('gem')!r} is not an answer")
        if len(answers) < MIN_ANSWERS:
            warnings.append(f"{where}: only {len(answers)} answers (minimum {MIN_ANSWERS})")

        keys = []  # (normalized key, answer name)
        for a in answers:
            for k in {normalize(x) for x in [a["name"], *a.get("aliases", [])]}:
                keys.append((k, a["name"]))
        owners = {}
        for k, name in keys:
            owners.setdefault(k, set()).add(name)
        for k, who in owners.items():
            if len(who) > 1:
                errors.append(f"{where}: {k!r} is shared by {sorted(who)}")

        distinct = {frozenset(pair) for pair in cat.get("distinct", [])}
        for a, b in combinations(answers, 2):
            if frozenset((a["name"], b["name"])) in distinct:
                continue
            ka = {normalize(x) for x in [a["name"], *a.get("aliases", [])]}
            kb = {normalize(x) for x in [b["name"], *b.get("aliases", [])]}
            best = max((similarity(x, y), x, y) for x in ka for y in kb)
            if best[0] >= NEAR_DUPLICATE and best[1] != best[2]:
                warnings.append(f"{where}: near-duplicate {a['name']!r} / {b['name']!r} ({best[1]!r} ~ {best[2]!r})")

        for a in answers:
            for p in a.get("count", []):
                try:
                    rx = compile_pattern(p)
                except re.error as exc:
                    errors.append(f"{where}: {a['name']!r} pattern {p!r} does not compile: {exc}")
                    continue
                if not any(rx.search(t) for t in lore.values()) and not a.get("inferred"):
                    warnings.append(f"{where}: {a['name']!r} pattern {p!r} never matches")
            mentions = sum(len(mention_spans(a, t)) for t in lore.values())
            if a.get("inferred"):
                quote = a.get("quote", "")
                if not quote:
                    errors.append(f"{where}: inferred {a['name']!r} has no evidence quote")
                elif loose(quote) not in corpus:
                    errors.append(f"{where}: inferred {a['name']!r} quote not found in lore: {quote!r}")
                if "obscurity" not in a:
                    errors.append(f"{where}: inferred {a['name']!r} needs an obscurity rating")
            elif mentions == 0:
                errors.append(f"{where}: {a['name']!r} has zero mentions in the lore (no evidence)")
            if not 1 <= a.get("obscurity", 3) <= 5:
                errors.append(f"{where}: {a['name']!r} obscurity must be 1-5")

    for w in warnings:
        print("warn ", w)
    for e in errors:
        print("ERROR", e)
    print(f"{len(errors)} errors, {len(warnings)} warnings")
    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main())
