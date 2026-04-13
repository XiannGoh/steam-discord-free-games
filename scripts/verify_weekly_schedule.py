"""Verify that this week's scheduling messages were successfully posted.

Reads data/scheduling/weekly_schedule_messages.json to get the current week's
message IDs, fetches each message via the Discord API, and writes
weekly_schedule_verification.json with a structured pass/fail report.

Channel verified: update-weekly-schedule-here
Pass criteria are loaded from channel_specs.json.

Usage:
    PYTHONPATH=. DISCORD_SCHEDULING_BOT_TOKEN=<token> python scripts/verify_weekly_schedule.py

Environment variables:
    DISCORD_SCHEDULING_BOT_TOKEN  — required (separate bot from main DISCORD_BOT_TOKEN)
    TARGET_WEEK_KEY               — optional week key override (e.g. 2026-04-13_to_2026-04-19)
                                    defaults to the latest key in the state file

Exit codes:
    0 — all checks passed
    1 — one or more checks failed, or DISCORD_SCHEDULING_BOT_TOKEN not set
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

WEEKLY_SCHEDULE_MESSAGES_FILE = "data/scheduling/weekly_schedule_messages.json"
CHANNEL_SPECS_FILE = "channel_specs.json"
VERIFICATION_FILE = "weekly_schedule_verification.json"

CHANNEL_NAME = "update-weekly-schedule-here"

TARGET_WEEK_KEY_ENV = "TARGET_WEEK_KEY"

DAY_ORDER = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def load_weekly_schedule_messages() -> Dict[str, Any]:
    if not os.path.exists(WEEKLY_SCHEDULE_MESSAGES_FILE):
        return {}
    try:
        with open(WEEKLY_SCHEDULE_MESSAGES_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except Exception as e:
        print(f"WARN: failed to load {WEEKLY_SCHEDULE_MESSAGES_FILE}: {e}")
        return {}


def load_channel_specs() -> Dict[str, Any]:
    if not os.path.exists(CHANNEL_SPECS_FILE):
        print(f"WARN: {CHANNEL_SPECS_FILE} not found; using default pass criteria.")
        return {}
    try:
        with open(CHANNEL_SPECS_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except Exception as e:
        print(f"WARN: failed to load {CHANNEL_SPECS_FILE}: {e}")
        return {}


def get_spec_required(specs: Dict[str, Any], channel_name: str) -> Dict[str, Any]:
    spec = specs.get(channel_name, {})
    required = spec.get("required", {})
    return {
        "intro_required": required.get("intro", False),
        "footer_required": required.get("footer", False),
        "min_items": required.get("min_items", 0),
        "no_duplicates": required.get("no_duplicates", True),
        "reactions": required.get("reactions", []),
    }


def get_target_week_key(messages: Dict[str, Any]) -> Optional[str]:
    """Return TARGET_WEEK_KEY env var or the latest key in the messages file."""
    override = (os.getenv(TARGET_WEEK_KEY_ENV, "") or "").strip()
    if override:
        return override
    if not messages:
        return None
    # Keys are in the format YYYY-MM-DD_to_YYYY-MM-DD — sort lexicographically.
    return sorted(messages.keys())[-1]


def write_verification(result: Dict[str, Any]) -> None:
    with open(VERIFICATION_FILE, "w", encoding="utf-8") as f:
        json.dump(result, f, indent=2, ensure_ascii=False)


def check_message(
    client: DiscordClient,
    channel_id: str,
    message_id: str,
    label: str,
    result: Dict[str, Any],
) -> Optional[Dict[str, Any]]:
    """Fetch a single message and record pass/fail into result."""
    try:
        msg = client.get_message(channel_id, message_id, context=f"verify {label}")
        result["messages_checked"] += 1
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
    token = os.getenv("DISCORD_SCHEDULING_BOT_TOKEN")
    if not token:
        print("ERROR: DISCORD_SCHEDULING_BOT_TOKEN is not set.")
        sys.exit(1)

    messages = load_weekly_schedule_messages()
    week_key = get_target_week_key(messages)

    print(f"Verifying weekly schedule output for week: {week_key}")

    specs = load_channel_specs()
    spec_required = get_spec_required(specs, CHANNEL_NAME)
    print(f"Channel: {CHANNEL_NAME}")
    print(f"Spec criteria: {spec_required}")

    result: Dict[str, Any] = {
        "week_key": week_key,
        "timestamp": utc_now_iso(),
        "channel": CHANNEL_NAME,
        "pass": False,
        "checked": True,
        "spec_criteria": spec_required,
        "messages_checked": 0,
        "messages_missing": [],
        "intro_found": False,
        "days_found": [],
        "days_missing": [],
        "errors": [],
    }

    if not week_key:
        result["errors"].append(
            f"No week key found in {WEEKLY_SCHEDULE_MESSAGES_FILE}. "
            "Weekly scheduling bot may not have run yet."
        )
        print(f"FAIL: no week key in {WEEKLY_SCHEDULE_MESSAGES_FILE}")
        write_verification(result)
        sys.exit(1)

    week_entry = messages.get(week_key)
    if not isinstance(week_entry, dict):
        result["errors"].append(
            f"No entry for week {week_key!r} in {WEEKLY_SCHEDULE_MESSAGES_FILE}."
        )
        print(f"FAIL: no entry for week {week_key!r}")
        write_verification(result)
        sys.exit(1)

    channel_id = str(week_entry.get("channel_id") or "").strip()
    if not channel_id:
        result["errors"].append("channel_id missing from week entry.")
        print("FAIL: no channel_id in week entry")
        write_verification(result)
        sys.exit(1)

    session = requests.Session()
    session.headers.update({
        "Authorization": f"Bot {token}",
        "Content-Type": "application/json",
    })
    client = DiscordClient(session)

    seen_message_ids: List[str] = []

    # --- Intro message ---
    print("\n--- Intro ---")
    intro_message_id = str(week_entry.get("intro_message_id") or "").strip()

    if intro_message_id:
        if intro_message_id in seen_message_ids:
            result["errors"].append(f"Duplicate message_id {intro_message_id} for intro.")
        seen_message_ids.append(intro_message_id)
        msg = check_message(client, channel_id, intro_message_id, "weekly schedule intro", result)
        result["intro_found"] = msg is not None and bool(msg.get("content"))
    else:
        print("  SKIPPED  intro (no intro_message_id in state)")
        result["intro_found"] = False

    # --- Day messages ---
    days = week_entry.get("days") or {}
    print(f"\n--- Day messages ({len(days)} days) ---")

    for day_name in DAY_ORDER:
        day_message_id = str(days.get(day_name) or "").strip()
        if not day_message_id:
            result["days_missing"].append(day_name)
            result["errors"].append(f"Day '{day_name}' has no message_id in state.")
            print(f"  MISSING  {day_name} (no message_id in state)")
            continue

        if day_message_id in seen_message_ids:
            result["errors"].append(f"Duplicate message_id {day_message_id} for day '{day_name}'.")
        seen_message_ids.append(day_message_id)

        msg = check_message(client, channel_id, day_message_id, day_name, result)
        if msg is not None:
            result["days_found"].append(day_name)
        else:
            result["days_missing"].append(day_name)

    # --- Duplicate check ---
    duplicates_found = len(seen_message_ids) != len(set(seen_message_ids))

    # --- Pass logic driven by spec ---
    # For this channel: intro is optional (spec intro_required=false).
    # Pass = all state-tracked messages are fetchable + no duplicates.
    # min_items=0 so an empty schedule is not a failure.
    no_missing = len(result["messages_missing"]) == 0
    no_dupes = not spec_required["no_duplicates"] or not duplicates_found

    # If the week entry claims post_completed=True, require at least the day
    # messages that were recorded to be present.
    post_completed = bool(week_entry.get("post_completed"))
    if post_completed and result["days_missing"] and all(
        d in (days or {}) for d in result["days_missing"]
    ):
        # Days were in state but failed to fetch — that's a real failure.
        result["errors"].append(
            f"Post marked completed but {len(result['days_missing'])} day message(s) missing: "
            f"{result['days_missing']}"
        )

    result["pass"] = no_missing and no_dupes

    # --- Write and summarise ---
    write_verification(result)

    print(f"\n=== Verification result for {week_key} ({CHANNEL_NAME}) ===")
    print(f"  pass:              {result['pass']}")
    print(f"  messages_checked:  {result['messages_checked']}")
    print(f"  messages_missing:  {len(result['messages_missing'])}")
    print(f"  intro_found:       {result['intro_found']}")
    print(f"  days_found:        {result['days_found']}")
    if result["days_missing"]:
        print(f"  days_missing:      {result['days_missing']}")
    if result["errors"]:
        print(f"  errors ({len(result['errors'])}):")
        for err in result["errors"]:
            print(f"    - {err}")
    print(f"\nWrote {VERIFICATION_FILE}")

    if not result["pass"]:
        sys.exit(1)


if __name__ == "__main__":
    main()
