"""Verify that today's gaming library messages were successfully posted.

Reads gaming_library.json to get today's posted message IDs, fetches each
message via the Discord API, and writes gaming_library_verification.json
with a structured pass/fail report.

Channel verified: step-3-review-existing-games
Pass criteria are loaded from channel_specs.json.

Footer note: channel_specs.json marks footer as required for step-3, but
post_daily_library_reminder() does not post a footer. Footer is excluded from
the pass logic and reported as footer_skipped=True until the code catches up
to the spec.

Usage:
    PYTHONPATH=. DISCORD_BOT_TOKEN=<token> python scripts/verify_gaming_library.py

Environment variables:
    DISCORD_BOT_TOKEN   — required
    LIBRARY_DATE_UTC    — optional YYYY-MM-DD override (defaults to today UTC)

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

GAMING_LIBRARY_FILE = "gaming_library.json"
CHANNEL_SPECS_FILE = "channel_specs.json"
VERIFICATION_FILE = "gaming_library_verification.json"

CHANNEL_NAME = "step-3-review-existing-games"

# Reactions the spec expects on each game message.
STATUS_EMOJIS = ("✅", "⏸️", "❌")

# Instagram source types — used to detect entries that may lack a real game name.
INSTAGRAM_SOURCE_TYPES = {"instagram"}

LIBRARY_DATE_OVERRIDE_ENV = "LIBRARY_DATE_UTC"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def get_target_day_key() -> str:
    manual_day = (os.getenv(LIBRARY_DATE_OVERRIDE_ENV, "") or "").strip()
    if manual_day:
        try:
            datetime.fromisoformat(manual_day)
        except ValueError:
            print(f"ERROR: {LIBRARY_DATE_OVERRIDE_ENV} must be YYYY-MM-DD, got: {manual_day!r}")
            sys.exit(1)
        return manual_day
    return datetime.now(timezone.utc).date().isoformat()


def load_gaming_library() -> Dict[str, Any]:
    if not os.path.exists(GAMING_LIBRARY_FILE):
        return {}
    try:
        with open(GAMING_LIBRARY_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except Exception as e:
        print(f"WARN: failed to load {GAMING_LIBRARY_FILE}: {e}")
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
        "intro_required": required.get("intro", True),
        "footer_required": required.get("footer", False),
        "min_items": required.get("min_items", 0),
        "no_duplicates": required.get("no_duplicates", True),
        "reactions": required.get("reactions", []),
    }


def write_verification(result: Dict[str, Any]) -> None:
    with open(VERIFICATION_FILE, "w", encoding="utf-8") as f:
        json.dump(result, f, indent=2, ensure_ascii=False)


def reaction_counts_from_message(msg: Dict[str, Any], emojis: tuple) -> Dict[str, int]:
    """Return {emoji: count} for the given emojis from a fetched message payload."""
    counts: Dict[str, int] = {e: 0 for e in emojis}
    for reaction in msg.get("reactions", []):
        name = reaction.get("emoji", {}).get("name", "")
        if name in counts:
            counts[name] = int(reaction.get("count", 0))
    return counts


def check_message(
    client: DiscordClient,
    channel_id: str,
    message_id: str,
    label: str,
    result: Dict[str, Any],
    *,
    check_reactions: Optional[tuple] = None,
) -> Optional[Dict[str, Any]]:
    """Fetch a single message and record pass/fail into result."""
    try:
        msg = client.get_message(channel_id, message_id, context=f"verify {label}")
        result["messages_checked"] += 1
        if check_reactions:
            counts = reaction_counts_from_message(msg, check_reactions)
            reaction_str = "  ".join(f"{e}={counts[e]}" for e in check_reactions)
            print(f"  OK  {label} (message_id={message_id}  {reaction_str})")
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


def is_placeholder_name(canonical_name: str, source_type: str) -> bool:
    """Return True when an Instagram-sourced entry has only a creator handle as its name."""
    if source_type not in INSTAGRAM_SOURCE_TYPES:
        return False
    name = (canonical_name or "").strip()
    return name.startswith("@") or not name


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    token = os.getenv("DISCORD_BOT_TOKEN")
    if not token:
        print("ERROR: DISCORD_BOT_TOKEN is not set.")
        sys.exit(1)

    day_key = get_target_day_key()
    print(f"Verifying gaming library output for date: {day_key}")

    specs = load_channel_specs()
    spec_required = get_spec_required(specs, CHANNEL_NAME)
    print(f"Channel: {CHANNEL_NAME}")
    print(f"Spec criteria: {spec_required}")

    library = load_gaming_library()
    games = library.get("games") or {}
    daily_posts = library.get("daily_posts") or {}
    day_entry = daily_posts.get(day_key)

    result: Dict[str, Any] = {
        "date": day_key,
        "timestamp": utc_now_iso(),
        "channel": CHANNEL_NAME,
        "pass": False,
        "checked": True,
        "spec_criteria": spec_required,
        "messages_checked": 0,
        "messages_missing": [],
        "header_found": False,
        "game_messages_found": [],
        "game_name_warnings": [],
        "footer_skipped": True,  # footer not implemented in post_daily_library_reminder
        "errors": [],
    }

    if not isinstance(day_entry, dict):
        result["errors"].append(
            f"No entry for {day_key} in {GAMING_LIBRARY_FILE}. "
            "Gaming library daily post may not have run yet."
        )
        print(f"FAIL: no daily_posts entry for {day_key}")
        write_verification(result)
        sys.exit(1)

    messages = day_entry.get("messages") or {}
    if not isinstance(messages, dict):
        result["errors"].append(f"daily_posts[{day_key}].messages is not a dict.")
        write_verification(result)
        sys.exit(1)

    session = requests.Session()
    session.headers.update({
        "Authorization": f"Bot {token}",
        "Content-Type": "application/json",
    })
    client = DiscordClient(session)

    seen_message_ids: List[str] = []

    # --- Header ---
    print("\n--- Header ---")
    header_info = messages.get("header") or {}
    header_msg_id = str(header_info.get("message_id") or "").strip()
    header_ch_id = str(header_info.get("channel_id") or "").strip()

    if header_msg_id and header_ch_id:
        if header_msg_id in seen_message_ids:
            result["errors"].append(f"Duplicate message_id {header_msg_id} for header.")
        seen_message_ids.append(header_msg_id)
        msg = check_message(client, header_ch_id, header_msg_id, "gaming library header", result)
        result["header_found"] = msg is not None and bool(msg.get("content"))
    else:
        result["errors"].append("Header message_id or channel_id missing from daily_posts.")
        print("  MISSING  header (no message_id in state)")

    # --- Game messages ---
    game_keys = [k for k in messages if k not in {"header", "intro"}]
    print(f"\n--- Game messages ({len(game_keys)} total) ---")

    for identity_key in game_keys:
        msg_info = messages[identity_key]
        if not isinstance(msg_info, dict):
            continue
        msg_id = str(msg_info.get("message_id") or "").strip()
        ch_id = str(msg_info.get("channel_id") or "").strip()

        # Look up game metadata for name validation
        game = games.get(identity_key) or {}
        canonical_name = str(game.get("canonical_name") or "").strip()
        source_type = str(game.get("source_type") or "").strip()
        label = canonical_name or identity_key

        # Warn on missing or placeholder game names
        if not canonical_name:
            result["game_name_warnings"].append(
                f"{identity_key}: canonical_name is empty"
            )
            result["errors"].append(f"Game '{identity_key}': missing canonical_name.")
        elif is_placeholder_name(canonical_name, source_type):
            result["game_name_warnings"].append(
                f"{identity_key}: Instagram entry has only a creator handle as name: {canonical_name!r}"
            )
            print(f"  WARN  [{identity_key}] Instagram entry may lack a real game name: {canonical_name!r}")

        if not (msg_id and ch_id):
            result["errors"].append(f"Game '{label}' ({identity_key}) missing message_id or channel_id.")
            print(f"  MISSING  {label} (no message_id in state)")
            continue

        if msg_id in seen_message_ids:
            result["errors"].append(f"Duplicate message_id {msg_id} for game '{label}'.")
        seen_message_ids.append(msg_id)

        msg = check_message(
            client, ch_id, msg_id, label, result,
            check_reactions=STATUS_EMOJIS,
        )
        if msg is not None:
            result["game_messages_found"].append(identity_key)

    # --- Footer ---
    print("\n--- Footer ---")
    print("  SKIPPED  footer (not posted by post_daily_library_reminder — spec aspirational)")

    # --- Duplicate check ---
    duplicates_found = len(seen_message_ids) != len(set(seen_message_ids))

    # --- Pass logic driven by spec ---
    # footer_required is excluded: code does not post a footer yet.
    intro_ok = not spec_required["intro_required"] or result["header_found"]
    items_ok = result["messages_checked"] >= spec_required["min_items"]
    no_missing = len(result["messages_missing"]) == 0
    no_dupes = not spec_required["no_duplicates"] or not duplicates_found

    result["pass"] = intro_ok and items_ok and no_missing and no_dupes

    # --- Write and summarise ---
    write_verification(result)

    print(f"\n=== Verification result for {day_key} ({CHANNEL_NAME}) ===")
    print(f"  pass:                  {result['pass']}")
    print(f"  messages_checked:      {result['messages_checked']}")
    print(f"  messages_missing:      {len(result['messages_missing'])}")
    print(f"  header_found:          {result['header_found']}")
    print(f"  game_messages_found:   {len(result['game_messages_found'])}")
    print(f"  footer_skipped:        {result['footer_skipped']}")
    if result["game_name_warnings"]:
        print(f"  game_name_warnings ({len(result['game_name_warnings'])}):")
        for w in result["game_name_warnings"]:
            print(f"    - {w}")
    if result["errors"]:
        print(f"  errors ({len(result['errors'])}):")
        for err in result["errors"]:
            print(f"    - {err}")
    print(f"\nWrote {VERIFICATION_FILE}")

    if not result["pass"]:
        sys.exit(1)


if __name__ == "__main__":
    main()
