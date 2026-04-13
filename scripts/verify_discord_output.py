"""Verify that today's daily Discord picks messages were successfully posted.

Reads discord_daily_posts.json to get today's message IDs and channel IDs,
fetches each message via the Discord API, and writes discord_verification.json
with a structured pass/fail report.

Usage:
    PYTHONPATH=. DISCORD_BOT_TOKEN=<token> python scripts/verify_discord_output.py

Exit codes:
    0 — all checks passed
    1 — one or more checks failed, or DISCORD_BOT_TOKEN not set
"""

import json
import os
import sys
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

import requests

from discord_api import DiscordApiError, DiscordClient, DiscordMessageNotFoundError

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

DISCORD_DAILY_POSTS_FILE = "discord_daily_posts.json"
DISCORD_VERIFICATION_FILE = "discord_verification.json"

# URL-encoded 👍 — matches the encoding used in main.py's add_thumbs_up_reaction.
THUMBS_UP_EMOJI = "\U0001f44d"
THUMBS_UP_EMOJI_ENCODED = "%F0%9F%91%8D"

# Honour the same date-override env var that main.py uses so this script can
# be pointed at a specific day during manual reruns.
DAILY_DATE_OVERRIDE_ENV = "DAILY_DATE_UTC"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def get_target_day_key() -> str:
    manual_day = (os.getenv(DAILY_DATE_OVERRIDE_ENV, "") or "").strip()
    if manual_day:
        try:
            datetime.fromisoformat(manual_day)
        except ValueError:
            print(f"ERROR: {DAILY_DATE_OVERRIDE_ENV} must be YYYY-MM-DD, got: {manual_day!r}")
            sys.exit(1)
        return manual_day
    return datetime.now(timezone.utc).date().isoformat()


def load_daily_posts() -> Dict[str, Any]:
    if not os.path.exists(DISCORD_DAILY_POSTS_FILE):
        return {}
    try:
        with open(DISCORD_DAILY_POSTS_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except Exception as e:
        print(f"WARN: failed to load {DISCORD_DAILY_POSTS_FILE}: {e}")
        return {}


def write_verification(result: Dict[str, Any]) -> None:
    with open(DISCORD_VERIFICATION_FILE, "w", encoding="utf-8") as f:
        json.dump(result, f, indent=2, ensure_ascii=False)


def thumbs_up_count_from_message(msg: Dict[str, Any]) -> int:
    """Extract 👍 reaction count directly from a fetched message payload."""
    for reaction in msg.get("reactions", []):
        emoji = reaction.get("emoji", {})
        if emoji.get("name") == THUMBS_UP_EMOJI:
            return int(reaction.get("count", 0))
    return 0


def check_message(
    client: DiscordClient,
    channel_id: str,
    message_id: str,
    label: str,
    result: Dict[str, Any],
    *,
    check_thumbs_up: bool = False,
) -> Optional[Dict[str, Any]]:
    """Fetch a single message and record pass/fail into result. Returns the payload or None."""
    try:
        msg = client.get_message(channel_id, message_id, context=f"verify {label}")
        result["messages_checked"] += 1
        if check_thumbs_up:
            count = thumbs_up_count_from_message(msg)
            print(f"  OK  {label} (message_id={message_id}, 👍={count})")
        else:
            print(f"  OK  {label} (message_id={message_id})")
        return msg
    except DiscordMessageNotFoundError:
        result["messages_missing"].append({"label": label, "message_id": message_id})
        result["errors"].append(f"{label}: message {message_id} not found (deleted or wrong ID)")
        print(f"  MISSING  {label} (message_id={message_id})")
        return None
    except DiscordApiError as e:
        result["errors"].append(f"{label}: API error — {e}")
        print(f"  ERROR  {label}: {e}")
        return None


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    token = os.getenv("DISCORD_BOT_TOKEN")
    if not token:
        print("ERROR: DISCORD_BOT_TOKEN is not set.")
        sys.exit(1)

    day_key = get_target_day_key()
    print(f"Verifying Discord output for date: {day_key}")

    daily_posts = load_daily_posts()
    day_entry = daily_posts.get(day_key)

    result: Dict[str, Any] = {
        "date": day_key,
        "timestamp": utc_now_iso(),
        "pass": False,
        "messages_checked": 0,
        "messages_missing": [],
        "intro_found": False,
        "footer_found": False,
        "sections_found": [],
        "errors": [],
    }

    if not isinstance(day_entry, dict):
        result["errors"].append(
            f"No entry for {day_key} in {DISCORD_DAILY_POSTS_FILE}. "
            "Daily picks may not have run yet."
        )
        print(f"FAIL: no entry for {day_key} in {DISCORD_DAILY_POSTS_FILE}")
        write_verification(result)
        sys.exit(1)

    run_state = day_entry.get("run_state") or {}
    items: List[Dict[str, Any]] = day_entry.get("items") or []

    # Build DiscordClient — same pattern as other scripts in this repo.
    session = requests.Session()
    session.headers.update({
        "Authorization": f"Bot {token}",
        "Content-Type": "application/json",
    })
    client = DiscordClient(session)

    # -------------------------------------------------------------------
    # 1. Intro message
    # -------------------------------------------------------------------
    print("\n--- Intro ---")
    intro_state = run_state.get("intro") or {}
    intro_message_id = intro_state.get("message_id")
    intro_channel_id = intro_state.get("channel_id")

    if intro_message_id and intro_channel_id:
        msg = check_message(client, intro_channel_id, intro_message_id, "intro")
        result["intro_found"] = msg is not None and bool(msg.get("content"))
    else:
        result["errors"].append("Intro message_id or channel_id missing from run_state.")
        print("  MISSING  intro (no message_id in state)")

    # -------------------------------------------------------------------
    # 2. Section headers
    # -------------------------------------------------------------------
    print("\n--- Section headers ---")
    section_headers = run_state.get("section_headers") or {}
    for section_key, section_state in section_headers.items():
        if not isinstance(section_state, dict):
            continue
        msg_id = section_state.get("message_id")
        ch_id = section_state.get("channel_id")
        if not (msg_id and ch_id):
            result["errors"].append(f"Section header '{section_key}' missing message_id or channel_id.")
            print(f"  MISSING  {section_key} header (no message_id in state)")
            continue
        msg = check_message(client, ch_id, msg_id, f"{section_key} header")
        if msg is not None and msg.get("content"):
            result["sections_found"].append(section_key)

    # -------------------------------------------------------------------
    # 3. Navigation footer
    #    Footer is conditional on DISCORD_GUILD_ID being set at post time.
    #    An empty state ({}) means it was skipped — report but don't fail.
    # -------------------------------------------------------------------
    print("\n--- Navigation footer ---")
    footer_state = run_state.get("navigation_footer") or {}
    footer_message_id = footer_state.get("message_id")
    footer_channel_id = footer_state.get("channel_id")

    if footer_message_id and footer_channel_id:
        msg = check_message(client, footer_channel_id, footer_message_id, "navigation_footer")
        result["footer_found"] = msg is not None and bool(msg.get("content"))
    else:
        print("  SKIPPED  navigation_footer (no message_id in state — DISCORD_GUILD_ID may not have been set)")
        result["footer_found"] = False

    # -------------------------------------------------------------------
    # 4. Item messages (one per game / creator post)
    #    Also checks 👍 reaction is readable from the message payload.
    # -------------------------------------------------------------------
    print(f"\n--- Item messages ({len(items)} total) ---")
    for item in items:
        if not isinstance(item, dict):
            continue
        msg_id = item.get("message_id")
        ch_id = item.get("channel_id")
        title = item.get("title") or "(untitled)"
        section = item.get("section") or "unknown"
        if not (msg_id and ch_id):
            result["errors"].append(f"Item '{title}' ({section}) missing message_id or channel_id.")
            print(f"  MISSING  [{section}] {title} (no message_id in state)")
            continue
        check_message(
            client, ch_id, msg_id,
            f"[{section}] {title}",
            result,
            check_thumbs_up=True,
        )

    # -------------------------------------------------------------------
    # 5. Determine overall pass
    #    Footer is intentionally excluded — it's conditional on GUILD_ID.
    # -------------------------------------------------------------------
    result["pass"] = (
        result["intro_found"]
        and len(result["sections_found"]) >= 1
        and len(result["messages_missing"]) == 0
        and result["messages_checked"] > 0
    )

    # -------------------------------------------------------------------
    # 6. Write output and print summary
    # -------------------------------------------------------------------
    write_verification(result)

    print(f"\n=== Verification result for {day_key} ===")
    print(f"  pass:             {result['pass']}")
    print(f"  messages_checked: {result['messages_checked']}")
    print(f"  messages_missing: {len(result['messages_missing'])}")
    print(f"  intro_found:      {result['intro_found']}")
    print(f"  footer_found:     {result['footer_found']}")
    print(f"  sections_found:   {result['sections_found']}")
    if result["errors"]:
        print(f"  errors ({len(result['errors'])}):")
        for err in result["errors"]:
            print(f"    - {err}")
    print(f"\nWrote {DISCORD_VERIFICATION_FILE}")

    if not result["pass"]:
        sys.exit(1)


if __name__ == "__main__":
    main()
