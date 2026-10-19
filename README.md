# DeepPint

A daily Pintland Isles trivia game. Five questions a day, the same for everyone, 25 seconds each. The rarer your answer is in the lore, the deeper it scores.

Play: https://itsjackyboi.github.io/Krillpint/ (add `#test` to the URL for date controls)

## How it works

- **Canon** is only the two files in `lore/`.
- **Questions** are hand-written in `scripts/source/*.json`: answers, aliases, mention-count patterns, an obscurity rating (1–5) and, for answers the lore only implies, an evidence quote. Letter questions and a few others are rule-based (`"derive"`) and pool their answers from the hand-written ones.
- **Rarity** = mentions across both lore docs (fewer = rarer), nudged by the obscurity rating, ranked within the question and curved so obvious answers score near 0.
- **Gems**: each question has one hand-picked gem. Landing it multiplies that answer's score by 1.5.
- **Schedule**: one easy, one medium and one hard question a day plus two more, least recently used first. Days already played never change.

## Commands

```sh
python3 scripts/validate.py   # duplicates, near-duplicates, zero-evidence answers, bad refs
python3 scripts/build.py      # writes data/prompts.json, data/answers/*.json, data/schedule.json
```

Python 3.9+, no dependencies. Serve the repo root with any static server to play locally.

## Daily Discord post

`.github/workflows/discord-daily.yml` posts the link at 8:00 AM US Eastern. It needs a repository secret `DISCORD_WEBHOOK_URL`. Run it by hand from the Actions tab to test.

## Assumptions

- A repeat gap of 30 days needs 150 questions; there are 122, so each question currently sits out about 24 days before it repeats.
- Questions may have as few as 18 answers (25 is the goal).
- Real-world people only count in the "real-world figure" question.
- Crude canon names are kept as written.
