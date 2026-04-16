"""Read Discord channel messages and save snapshots to data/.

Fetches the last 50 messages from up to 5 channels and writes each to a
structured JSON file under data/:
  - data/snapshot_step1.json
  - data/snapshot_step2.json
  - data/snapshot_step3.json
  - data/snapshot_schedule.json
  - data/snapshot_health.json

Channel IDs are resolved from environment variables:
  DISCORD_BOT_TOKEN            — required for all reads
  DISCORD_STEP1_CHANNEL_ID     — step-1 channel; falls back to webhook lookup
  DISCORD_WEBHOOK_URL          — used to look up step-1 channel if DISCORD_STEP1_CHANNEL_ID unset
  DISCORD_WINNERS_CHANNEL_ID   — step-2
  DISCORD_GAMING_LIBRARY_CHANNEL_ID — step-3
  DISCORD_SCHEDULING_CHANNEL_ID — schedule channel
  DISCORD_HEALTH_MONITOR_CHANNEL_ID — health monitor channel; falls back to webhook lookup
  DISCORD_HEALTH_MONITOR_WEBHOOK_URL — used if DISCORD_HEALTH_MONITOR_CHANNEL_ID unset

Usage:
    # Fetch all 5 channels
    PYTHONPATH=. DISCORD_BOT_TOKEN=<token> python scripts/read_discord_channel.py

    # Fetch a specific channel by name
    PYTHONPATH=. DISCORD_BOT_TOKEN=<token> python scripts/read_discord_channel.py --channel step1

    # Fetch with custom message limit
    PYTHONPATH=. DISCORD_BOT_TOKEN=<token> python scripts/read_discord_channel.py --limit 20

Exit codes:
    0 — all requested channels fetched (or gracefully skipped)
    1 — DISCORD_BOT_TOKEN not set
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import requests

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from discord_api import DISCORD_API_BASE, DiscordApiError, DiscordClient  # noqa: E402

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

DEFAULT_MESSAGE_LIMIT = 50

CHANNEL_STEP1 = "step-1-vote-on-games-to-test"
CHANNEL_STEP2 = "step-2-test-then-vote-to-keep"
CHANNEL_STEP3 = "step-3-review-existing-games"
CHANNEL_SCHEDULE = "update-weekly-schedule-here"
CHANNEL_HEALTH = "xiann-gpt-bot-health-monitor"

DATA_DIR = ROOT / "data"

SNAPSHOT_FILES = {
    "step1": DATA_DIR / "snapshot_step1.json",
    "step2": DATA_DIR / "snapshot_step2.json",
    "step3": DATA_DIR / "snapshot_step3.json",
    "schedule": DATA_DIR / "snapshot_schedule.json",
    "health": DATA_DIR / "snapshot_health.json",
}

CHANNEL_NAMES = {
    "step1": CHANNEL_STEP1,
    "step2": CHANNEL_STEP2,
    "step3": CHANNEL_STEP3,
    "schedule": CHANNEL_SCHEDULE,
    "health": CHANNEL_HEALTH,
}


# ---------------------------------------------------------------------------
# Channel ID resolution
# ---------------------------------------------------------------------------

def _resolve_webhook_channel_id(webhook_url: str, client: DiscordClient) -> str | None:
    """Resolve the channel_id for a Discord webhook by calling the webhook URL.

    The Discord API returns the webhook object (including channel_id) when
    you GET the webhook URL with no auth header required.
    """
    if not webhook_url:
        return None
    try:
        response = client.request("GET", webhook_url, context="resolve webhook channel_id")
        payload = response.json()
        if isinstance(payload, dict):
            channel_id = payload.get("channel_id")
            if channel_id:
                return str(channel_id)
    except (DiscordApiError, Exception) as exc:
        print(f"WARN: could not resolve channel_id from webhook URL: {exc}")
    return None


def resolve_channel_ids(client: DiscordClient) -> dict[str, str | None]:
    """Return a dict mapping channel key → channel_id (or None if unresolvable)."""
    ids: dict[str, str | None] = {}

    # Step 1 — prefer explicit env var; fall back to webhook lookup
    step1_channel = (os.getenv("DISCORD_STEP1_CHANNEL_ID") or "").strip() or None
    if not step1_channel:
        webhook_url = (os.getenv("DISCORD_WEBHOOK_URL") or "").strip()
        if webhook_url:
            step1_channel = _resolve_webhook_channel_id(webhook_url, client)
            if step1_channel:
                print(f"INFO: resolved step-1 channel_id={step1_channel} from DISCORD_WEBHOOK_URL")
            else:
                print("WARN: DISCORD_STEP1_CHANNEL_ID not set and webhook lookup failed — step-1 will be skipped")
        else:
            print("WARN: DISCORD_STEP1_CHANNEL_ID and DISCORD_WEBHOOK_URL not set — step-1 will be skipped")
    ids["step1"] = step1_channel

    ids["step2"] = (os.getenv("DISCORD_WINNERS_CHANNEL_ID") or "").strip() or None
    if not ids["step2"]:
        print("WARN: DISCORD_WINNERS_CHANNEL_ID not set — step-2 will be skipped")

    ids["step3"] = (os.getenv("DISCORD_GAMING_LIBRARY_CHANNEL_ID") or "").strip() or None
    if not ids["step3"]:
        print("WARN: DISCORD_GAMING_LIBRARY_CHANNEL_ID not set — step-3 will be skipped")

    ids["schedule"] = (os.getenv("DISCORD_SCHEDULING_CHANNEL_ID") or "").strip() or None
    if not ids["schedule"]:
        print("WARN: DISCORD_SCHEDULING_CHANNEL_ID not set — schedule will be skipped")

    # Health monitor — prefer explicit channel ID; fall back to webhook lookup
    health_channel = (os.getenv("DISCORD_HEALTH_MONITOR_CHANNEL_ID") or "").strip() or None
    if not health_channel:
        health_webhook_url = (os.getenv("DISCORD_HEALTH_MONITOR_WEBHOOK_URL") or "").strip()
        if health_webhook_url:
            health_channel = _resolve_webhook_channel_id(health_webhook_url, client)
            if health_channel:
                print(f"INFO: resolved health-monitor channel_id={health_channel} from DISCORD_HEALTH_MONITOR_WEBHOOK_URL")
            else:
                print("WARN: DISCORD_HEALTH_MONITOR_CHANNEL_ID not set and webhook lookup failed — health will be skipped")
        else:
            print("WARN: DISCORD_HEALTH_MONITOR_CHANNEL_ID and DISCORD_HEALTH_MONITOR_WEBHOOK_URL not set — health will be skipped")
    ids["health"] = health_channel

    return ids


# ---------------------------------------------------------------------------
# Snapshot building
# ---------------------------------------------------------------------------

def _format_reaction(reaction: dict[str, Any]) -> dict[str, Any]:
    emoji = reaction.get("emoji", {})
    emoji_name = emoji.get("name", "")
    emoji_id = emoji.get("id")
    count = int(reaction.get("count", 0))
    return {"emoji": emoji_name, "count": count, **({"emoji_id": emoji_id} if emoji_id else {})}


def _format_message(msg: dict[str, Any]) -> dict[str, Any]:
    author = msg.get("author", {})
    author_name = author.get("global_name") or author.get("username") or ""
    reactions = [_format_reaction(r) for r in msg.get("reactions", [])]
    return {
        "id": str(msg.get("id", "")),
        "author": author_name,
        "content": msg.get("content", ""),
        "timestamp": msg.get("timestamp", ""),
        "reactions": reactions,
    }


def fetch_channel_snapshot(
    client: DiscordClient,
    channel_key: str,
    channel_id: str,
    *,
    limit: int = DEFAULT_MESSAGE_LIMIT,
) -> dict[str, Any]:
    """Fetch up to `limit` messages from a channel and return a snapshot dict."""
    channel_name = CHANNEL_NAMES[channel_key]
    fetched_at = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    print(f"  Fetching {channel_name} (channel_id={channel_id}, limit={limit})...")

    messages = client.get_channel_messages(
        channel_id,
        context=f"read_discord_channel {channel_name}",
        limit=limit,
    )

    formatted = [_format_message(m) for m in messages]
    print(f"  → fetched {len(formatted)} messages")

    return {
        "channel_id": channel_id,
        "channel_name": channel_name,
        "fetched_at": fetched_at,
        "messages": formatted,
    }


# ---------------------------------------------------------------------------
# I/O helpers
# ---------------------------------------------------------------------------

def write_snapshot(channel_key: str, snapshot: dict[str, Any]) -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    out_path = SNAPSHOT_FILES[channel_key]
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(snapshot, f, indent=2, ensure_ascii=False)
    print(f"  → wrote {out_path}")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Fetch Discord channel messages and save snapshots.")
    parser.add_argument(
        "--channel",
        choices=list(SNAPSHOT_FILES.keys()),
        default=None,
        help="Fetch only this channel. Omit to fetch all 5 channels.",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=DEFAULT_MESSAGE_LIMIT,
        help=f"Maximum messages to fetch per channel (default: {DEFAULT_MESSAGE_LIMIT}).",
    )
    return parser


def main() -> None:
    args = build_arg_parser().parse_args()

    token = os.getenv("DISCORD_BOT_TOKEN", "").strip()
    if not token:
        print("ERROR: DISCORD_BOT_TOKEN is not set.")
        sys.exit(1)

    session = requests.Session()
    session.headers.update({
        "Authorization": f"Bot {token}",
        "Content-Type": "application/json",
    })
    client = DiscordClient(session)

    # Determine which channels to fetch
    if args.channel:
        channels_to_fetch = [args.channel]
    else:
        channels_to_fetch = list(SNAPSHOT_FILES.keys())

    print("Resolving channel IDs...")
    channel_ids = resolve_channel_ids(client)

    errors: list[str] = []

    for key in channels_to_fetch:
        channel_id = channel_ids.get(key)
        if not channel_id:
            print(f"SKIP: {key} — no channel_id available")
            continue

        print(f"\nFetching {key}...")
        try:
            snapshot = fetch_channel_snapshot(client, key, channel_id, limit=args.limit)
            write_snapshot(key, snapshot)
        except DiscordApiError as exc:
            msg = f"ERROR fetching {key} (channel_id={channel_id}): {exc}"
            print(msg)
            errors.append(msg)
        except Exception as exc:
            msg = f"ERROR fetching {key} (channel_id={channel_id}): unexpected error: {exc}"
            print(msg)
            errors.append(msg)

    if errors:
        print(f"\nCompleted with {len(errors)} error(s):")
        for err in errors:
            print(f"  {err}")
    else:
        print("\nAll channels fetched successfully.")


if __name__ == "__main__":
    main()
