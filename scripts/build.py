"""Build /data/prompts.json, /data/answers/*.json and /data/lexicon.json from the lore.

Rarity is hybrid:
  1. Mechanical: count alias-aware mentions of each answer across both lore docs.
     Fewer mentions = rarer. This is the base.
  2. Judged: an obscurity rating (1-5, default 3) nudges the base up or down by at
     most OBSCURITY_WEIGHT. Inferred answers (implied or passing mentions) always
     carry a rating and an evidence quote.
  3. The adjusted value is ranked within the category to give a 0-100 percentile,
     and the percentile is curved into a 0-100 depth score so obvious answers barely count.

Daily lineups are precomputed into /data/schedule.json. Each day takes one easy, one
medium and one hard question plus two more, always preferring the questions that ran
longest ago, so no question repeats within REPEAT_GAP days once there are enough of
them (5 x REPEAT_GAP). Days up to and including today (US Eastern) are kept from the
existing schedule, so adding questions never changes a lineup people already played.

Usage: python3 scripts/build.py
"""

import datetime as dt
import hashlib
import json
import random
import sys
from zoneinfo import ZoneInfo

from lib import DATA_DIR, LORE_FILES, ROOT, load_lore, load_sources, mention_spans, normalize, snippet

OBSCURITY_WEIGHT = 0.15  # max shift of the 0-1 mechanical rarity from a 1 or 5 rating
SCORE_CURVE = 1.5  # score = 100 * percentile^curve
EPOCH = "2026-10-18"  # Pour #1
TIMEZONE = "America/New_York"
REQUIRED_TIERS = ["easy", "medium", "hard"]  # plus two of any difficulty
PER_DAY = 5
REPEAT_GAP = 30  # target: days before a question may run again
HORIZON_DAYS = 730  # schedule this far past today
TIER_ORDER = {"easy": 0, "medium": 1, "hard": 2}
MIN_ANSWERS = 18  # approved floor for smaller questions; 25 is the goal


def rarity_table(entries):
    n = len(entries)
    for e in entries:
        m = e["mentions"]["total"]
        higher = sum(1 for o in entries if o["mentions"]["total"] > m)
        equal = sum(1 for o in entries if o["mentions"]["total"] == m) - 1
        mech = (higher + 0.5 * equal) / (n - 1) if n > 1 else 1.0
        e["_adj"] = mech + OBSCURITY_WEIGHT * (e["obscurity"] - 3) / 2
    for e in entries:
        lower = sum(1 for o in entries if o["_adj"] < e["_adj"] - 1e-9)
        equal = sum(1 for o in entries if abs(o["_adj"] - e["_adj"]) <= 1e-9) - 1
        pct = (lower + 0.5 * equal) / (n - 1) if n > 1 else 1.0
        e["rank"] = lower + 1  # 1 = most common
        e["percentile"] = round(pct * 100, 1)
        e["score"] = round(100 * pct ** SCORE_CURVE)
    entries.sort(key=lambda e: (e["_adj"], -e["mentions"]["total"], e["name"].lower()))
    for e in entries:
        del e["_adj"]


def build_category(cat, lore):
    entries = []
    for src in cat["answers"]:
        per_doc = {}
        first = None
        for key, text in lore.items():
            spans = mention_spans(src, text)
            per_doc[key] = len(spans)
            if spans and first is None:
                first = (key, snippet(text, *spans[0]))
        e = {
            "name": src["name"],
            "aliases": src.get("aliases", []),
            "mentions": {"total": sum(per_doc.values()), **per_doc},
            "obscurity": src.get("obscurity", 3),
            "inferred": bool(src.get("inferred", False)),
        }
        if e["inferred"]:
            e["evidence"] = {"source": "inferred", "text": src.get("quote", ""), "note": src.get("note", "")}
        elif first:
            e["evidence"] = {"source": LORE_FILES[first[0]][0], "text": first[1]}
        entries.append(e)
    rarity_table(entries)
    return {
        "id": cat["id"],
        "prompt": cat["prompt"],
        "blurb": cat.get("blurb", ""),
        "difficulty": cat["difficulty"],
        "gem": cat["gem"],
        "answerCount": len(entries),
        "answers": entries,
    }


def build_schedule(index, existing, today):
    ids = [c["id"] for c in index]
    tier = {c["id"]: c["difficulty"] for c in index}
    epoch = dt.date.fromisoformat(EPOCH)
    keep_until = (dt.date.fromisoformat(today) - epoch).days
    days = []
    if existing.get("epoch") == EPOCH:
        for d, picks in enumerate(existing.get("days", [])[: keep_until + 1]):
            if len(picks) != PER_DAY or not all(p in tier for p in picks):
                break
            days.append(picks)
    last_used = {}
    for d, picks in enumerate(days):
        for p in picks:
            last_used[p] = d
    for d in range(len(days), keep_until + HORIZON_DAYS + 1):
        rng = random.Random(f"deeppint|{(epoch + dt.timedelta(days=d)).isoformat()}")
        order = sorted(ids)
        rng.shuffle(order)
        order.sort(key=lambda i: last_used.get(i, -10**9))  # stable: oldest first, random among ties
        picks = []
        for t in REQUIRED_TIERS:
            pick = next((i for i in order if tier[i] == t and i not in picks), None)
            if pick:
                picks.append(pick)
        picks += [i for i in order if i not in picks][: PER_DAY - len(picks)]
        picks.sort(key=lambda i: (TIER_ORDER[tier[i]], order.index(i)))
        for p in picks:
            last_used[p] = d
        days.append(picks)
    gaps = []
    seen = {}
    for d, picks in enumerate(days):
        for p in picks:
            if p in seen:
                gaps.append(d - seen[p])
            seen[p] = d
    return {"epoch": EPOCH, "days": days}, (min(gaps) if gaps else None), len(days) - keep_until - 1


def main():
    lore = load_lore()
    lore_hash = hashlib.sha256("".join(lore[k] for k in sorted(lore)).encode()).hexdigest()[:12]
    cats = load_sources()
    answers_dir = DATA_DIR / "answers"
    answers_dir.mkdir(parents=True, exist_ok=True)
    for old in answers_dir.glob("*.json"):
        old.unlink()

    index, lexicon = [], {}
    for cat in cats:
        built = build_category(cat, lore)
        (answers_dir / f"{cat['id']}.json").write_text(json.dumps(built, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
        index.append({k: built[k] for k in ("id", "prompt", "blurb", "difficulty", "gem", "answerCount")})
        for e in built["answers"]:
            for alias in [e["name"], *e["aliases"]]:
                ids = lexicon.setdefault(normalize(alias), [])
                if cat["id"] not in ids:
                    ids.append(cat["id"])

    today = dt.datetime.now(ZoneInfo(TIMEZONE)).date().isoformat()
    sched_path = DATA_DIR / "schedule.json"
    existing = json.loads(sched_path.read_text(encoding="utf-8")) if sched_path.exists() else {}
    schedule, min_gap, ahead = build_schedule(index, existing, today)
    sched_path.write_text(json.dumps(schedule, separators=(",", ":")) + "\n", encoding="utf-8")
    prompts = {
        "version": 1,
        "loreHash": lore_hash,
        "epoch": EPOCH,
        "timezone": TIMEZONE,
        "repeatGap": REPEAT_GAP,
        "minAnswers": MIN_ANSWERS,
        "categories": index,
    }
    (DATA_DIR / "prompts.json").write_text(json.dumps(prompts, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
    (DATA_DIR / "lexicon.json").write_text(json.dumps(dict(sorted(lexicon.items())), ensure_ascii=False) + "\n", encoding="utf-8")

    total = sum(c["answerCount"] for c in index)
    print(f"Built {len(index)} categories, {total} answers (lore {lore_hash}) -> {DATA_DIR.relative_to(ROOT)}/")
    for c in index:
        flag = "  < min" if c["answerCount"] < MIN_ANSWERS else ""
        print(f"  {c['difficulty']:<6} {c['answerCount']:>3}  {c['id']}{flag}")
    gap_note = "" if min_gap is None or min_gap >= REPEAT_GAP else f"  < {REPEAT_GAP} target: add questions (need {PER_DAY * REPEAT_GAP})"
    print(f"  schedule: {len(schedule['days'])} days, {ahead} ahead of {today}; shortest repeat gap {min_gap} days{gap_note}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
