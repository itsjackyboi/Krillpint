"""Post today's DeepPint link to a Discord channel through a webhook.

Run by .github/workflows/discord-daily.yml, which a Claude scheduled task starts at
8:00 AM US Eastern every morning.

Env:
  DISCORD_WEBHOOK_URL  the channel webhook (a GitHub Actions secret)
  SITE_URL             the game URL (optional; defaults to the GitHub Pages address)

Usage: python3 scripts/discord_post.py [--dry-run]
"""

import datetime as dt
import json
import os
import sys
import urllib.request
from zoneinfo import ZoneInfo

from lib import DATA_DIR

DEFAULT_URL = "https://itsjackyboi.github.io/Krillpint/"

LINES = [
    "is poured. Five questions, 25 seconds each. How deep can you go?",
    "is on tap. Dig past the obvious answers.",
    "is ready. The rarer your answer, the deeper you sink.",
    "is poured. Find today's gems before the clock runs dry.",
    "is up. Sackbeard's Tavern won't save you today.",
]


def main():
    dry_run = "--dry-run" in sys.argv
    prompts = json.loads((DATA_DIR / "prompts.json").read_text(encoding="utf-8"))
    tz = ZoneInfo(prompts["timezone"])
    epoch = dt.date.fromisoformat(prompts["epoch"])
    now = dt.datetime.now(tz)

    day = (now.date() - epoch).days
    url = os.environ.get("SITE_URL", "").strip() or DEFAULT_URL
    content = f"🍺 **DeepPint #{day + 1}** {LINES[day % len(LINES)]}\n{url}"
    if dry_run:
        print(content)
        return 0

    webhook = os.environ.get("DISCORD_WEBHOOK_URL", "").strip()
    if not webhook:
        print("DISCORD_WEBHOOK_URL is not set. Add it as a repository secret.", file=sys.stderr)
        return 1
    body = json.dumps({"content": content, "username": "DeepPint", "allowed_mentions": {"parse": []}}).encode()
    req = urllib.request.Request(
        webhook,
        data=body,
        headers={"Content-Type": "application/json", "User-Agent": "DeepPint (github.com/itsjackyboi/Krillpint, 1.0)"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=20) as res:
        print(f"Posted DeepPint #{day + 1} (HTTP {res.status}).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
