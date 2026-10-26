"""Post today's DeepPint and Pintland Tap links to a Discord channel through a webhook.

One message with two embeds: DeepPint first, then Pintland Tap (the daily map game on the
server's Dynmap), each with its own title, link and colour bar.

Run by .github/workflows/discord-daily.yml, which a Claude scheduled task starts at
8:00 AM US Eastern every morning.

Env:
  DISCORD_WEBHOOK_URL  the channel webhook (a GitHub Actions secret)
  SITE_URL             the DeepPint URL (optional; defaults to the GitHub Pages address)
  TAP_URL              the Pintland Tap URL (optional; defaults to the server's Dynmap address)

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

# Pintland Tap lives in the itsjackyboi/base repo; puzzle #1 was its epoch, in the same time zone.
TAP_DEFAULT_URL = "http://51.161.71.220:8128/pintland/index.html"
TAP_EPOCH = dt.date(2026, 10, 26)
TAP_LINES = [
    "is up. Five places, 10 seconds each. How well do you know the Isles?",
    "is live. Block-perfect or bust.",
    "is ready. Ten seconds a guess, no second chances.",
    "is out. Find it before the clock runs out.",
    "is up. The places get harder every round.",
]
DEEP_COLOR = 0xC9A84C   # amber
TAP_COLOR = 0x2E7D5B    # map green

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
    embeds = [{
        "title": f"🍺 DeepPint #{day + 1}",
        "url": url,
        "description": f"Today's puzzle {LINES[day % len(LINES)]}\n[Play DeepPint]({url})",
        "color": DEEP_COLOR,
    }]

    tap_day = (now.date() - TAP_EPOCH).days
    if tap_day >= 0:   # nothing to post before puzzle #1
        tap_url = os.environ.get("TAP_URL", "").strip() or TAP_DEFAULT_URL
        embeds.append({
            "title": f"🗺️ Pintland Tap #{tap_day + 1}",
            "url": tap_url,
            "description": f"Today's map {TAP_LINES[tap_day % len(TAP_LINES)]}\n"
                           f"[Play Pintland Tap]({tap_url}) (log in to Dynmap first)",
            "color": TAP_COLOR,
        })

    header = f"**Pintland dailies · {now.strftime('%A, %B')} {now.day}**"
    if dry_run:
        print(json.dumps({"content": header, "embeds": embeds}, indent=2, ensure_ascii=False))
        return 0

    webhook = os.environ.get("DISCORD_WEBHOOK_URL", "").strip()
    if not webhook:
        print("DISCORD_WEBHOOK_URL is not set. Add it as a repository secret.", file=sys.stderr)
        return 1
    body = json.dumps({"username": "Pintland Daily", "content": header, "embeds": embeds, "allowed_mentions": {"parse": []}}).encode()
    req = urllib.request.Request(
        webhook,
        data=body,
        headers={"Content-Type": "application/json", "User-Agent": "DeepPint (github.com/itsjackyboi/Krillpint, 1.0)"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=20) as res:
        print(f"Posted DeepPint #{day + 1}" + (f" and Pintland Tap #{tap_day + 1}" if tap_day >= 0 else "") + f" (HTTP {res.status}).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
