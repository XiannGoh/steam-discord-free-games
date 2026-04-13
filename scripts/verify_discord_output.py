"""Verify that today's daily Discord picks and evening winners messages were
successfully posted.

Reads channel_specs.json to determine pass criteria for each channel, then
reads discord_daily_posts.json to get message IDs and channel IDs, fetches
each message via the Discord API, and writes discord_verification.json with
a structured per-channel pass/fail report.

Channels verified:
  step-1-vote-on-games-to-test   — daily picks (run_state in discord_daily_posts.json)
  step-2-test-then-vote-to-keep  — evening winners (winners_state in same file)
    Step-2 is skipped (not a failure) when winners_state is absent for today,
    which is expected when this script runs before evening_winners.py.

Usage:
    PYTHONPATH=. DISCORD_BOT_TOKEN=<token> python scripts/verify_discord_output.py

Exit codes:
    0 — all checked channels passed
    1 — one or more checked channels failed, or DISCORD_BOT_TOKEN not set
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

CHANNEL_SPECS_FILE = "channel_specs.json"
DISCORD_DAILY_POSTS_FILE = "discord_daily_posts.json"
DISCORD_VERIFICATION_FILE = "discord_verification.json"

CHANNEL_STEP1 = "step-1-vote-on-games-to-test"
CHANNEL_STEP2 = "step-2-test-then-vote-to-keep"

THUMBS_UP_EMOJI = "\U0001f44d"   # 👍
BOOKMARK_EMOJI = "\U0001f516"    # 🔖

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


def load_channel_specs() -> Dict[str, Any]:
    """Load channel_specs.json. Returns empty dict if missing or invalid."""
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
    """Return the required criteria for a channel, with safe defaults."""
    spec = specs.get(channel_name, {})
    required = spec.get("required", {})
    return {
        "intro_required": required.get("intro", True),
        "footer_required": required.get("footer", False),
        "min_items": required.get("min_items", 1),
        "no_duplicates": required.get("no_duplicates", True),
        "reactions": required.get("reactions", []),
    }


def write_verification(result: Dict[str, Any]) -> None:
    with open(DISCORD_VERIFICATION_FILE, "w", encoding="utf-8") as f:
        json.dump(result, f, indent=2, ensure_ascii=False)


def reaction_count_from_message(msg: Dict[str, Any], emoji: str) -> int:
    """Extract reaction count for a given emoji from a fetched message payload."""
    for reaction in msg.get("reactions", []):
        if reaction.get("emoji", {}).get("name") == emoji:
            return int(reaction.get("count", 0))
    return 0


def check_message(
    client: DiscordClient,
    channel_id: str,
    message_id: str,
    label: str,
    ch_result: Dict[str, Any],
    *,
    check_emoji: Optional[str] = None,
) -> Optional[Dict[str, Any]]:
    """Fetch a single message and record pass/fail into ch_result. Returns the
    payload or None. check_emoji names an emoji whose reaction count to log."""
    try:
        msg = client.get_message(channel_id, message_id, context=f"verify {label}")
        ch_result["messages_checked"] += 1
        if check_emoji:
            count = reaction_count_from_message(msg, check_emoji)
            print(f"  OK  {label} (message_id={message_id}, {check_emoji}={count})")
        else:
            print(f"  OK  {label} (message_id={message_id})")
        return msg
    except DiscordMessageNotFoundError:
        ch_result["messages_missing"].append({"label": label, "message_id": message_id})
        ch_result["errors"].append(f"{label}: message {message_id} not found (deleted or wrong ID)")
        print(f"  MISSING  {label} (message_id={message_id})")
        return None
    except DiscordApiError as e:
        ch_result["errors"].append(f"{label}: API error — {e}")
        print(f"  ERROR  {label}: {e}")
        return None


def _empty_channel_result() -> Dict[str, Any]:
    return {
        "pass": False,
        "checked": False,
        "messages_checked": 0,
        "messages_missing": [],
        "intro_found": False,
        "footer_found": False,
        "sections_found": [],
        "errors": [],
    }


# ---------------------------------------------------------------------------
# Step-1 verifier: step-1-vote-on-games-to-test
# ---------------------------------------------------------------------------

def verify_step1(
    client: DiscordClient,
    day_entry: Dict[str, Any],
    spec_required: Dict[str, Any],
    day_key: str,
) -> Dict[str, Any]:
    """Verify daily picks messages for step-1-vote-on-games-to-test."""
    ch = _empty_channel_result()
    ch["checked"] = True
    ch["spec_criteria"] = spec_required

    run_state = day_entry.get("run_state") or {}
    items: List[Dict[str, Any]] = day_entry.get("items") or []

    reaction_emoji = spec_required["reactions"][0] if spec_required["reactions"] else None

    # --- Intro ---
    print("\n--- Step-1 intro ---")
    intro_state = run_state.get("intro") or {}
    intro_message_id = intro_state.get("message_id")
    intro_channel_id = intro_state.get("channel_id")

    if intro_message_id and intro_channel_id:
        msg = check_message(client, intro_channel_id, intro_message_id, "intro", ch)
        ch["intro_found"] = msg is not None and bool(msg.get("content"))
    else:
        ch["errors"].append("Intro message_id or channel_id missing from run_state.")
        print("  MISSING  intro (no message_id in state)")

    # --- Section headers ---
    print("\n--- Step-1 section headers ---")
    section_headers = run_state.get("section_headers") or {}
    for section_key, section_state in section_headers.items():
        if not isinstance(section_state, dict):
            continue
        msg_id = section_state.get("message_id")
        ch_id = section_state.get("channel_id")
        if not (msg_id and ch_id):
            ch["errors"].append(f"Section header '{section_key}' missing message_id or channel_id.")
            print(f"  MISSING  {section_key} header (no message_id in state)")
            continue
        msg = check_message(client, ch_id, msg_id, f"{section_key} header", ch)
        if msg is not None and msg.get("content"):
            ch["sections_found"].append(section_key)

    # --- Navigation footer (conditional on GUILD_ID) ---
    print("\n--- Step-1 navigation footer ---")
    footer_state = run_state.get("navigation_footer") or {}
    footer_message_id = footer_state.get("message_id")
    footer_channel_id = footer_state.get("channel_id")

    if footer_message_id and footer_channel_id:
        msg = check_message(client, footer_channel_id, footer_message_id, "navigation_footer", ch)
        ch["footer_found"] = msg is not None and bool(msg.get("content"))
    else:
        print("  SKIPPED  navigation_footer (no message_id in state — DISCORD_GUILD_ID may not be set)")
        ch["footer_found"] = False

    # --- Item messages ---
    print(f"\n--- Step-1 item messages ({len(items)} total) ---")
    seen_message_ids: List[str] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        msg_id = item.get("message_id")
        ch_id = item.get("channel_id")
        title = item.get("title") or "(untitled)"
        section = item.get("section") or "unknown"
        if not (msg_id and ch_id):
            ch["errors"].append(f"Item '{title}' ({section}) missing message_id or channel_id.")
            print(f"  MISSING  [{section}] {title} (no message_id in state)")
            continue
        if msg_id in seen_message_ids:
            ch["errors"].append(f"Duplicate message_id {msg_id} for item '{title}'.")
        seen_message_ids.append(msg_id)
        check_message(client, ch_id, msg_id, f"[{section}] {title}", ch, check_emoji=reaction_emoji)

    # Duplicate check
    duplicates_found = len(seen_message_ids) != len(set(seen_message_ids))

    # --- Pass logic driven by spec ---
    intro_ok = not spec_required["intro_required"] or ch["intro_found"]
    footer_ok = not spec_required["footer_required"] or ch["footer_found"]
    items_ok = ch["messages_checked"] >= max(spec_required["min_items"], 1) if spec_required["min_items"] > 0 else True
    no_missing = len(ch["messages_missing"]) == 0
    no_dupes = not spec_required["no_duplicates"] or not duplicates_found

    ch["pass"] = intro_ok and footer_ok and items_ok and no_missing and no_dupes

    return ch


# ---------------------------------------------------------------------------
# Step-2 verifier: step-2-test-then-vote-to-keep
# ---------------------------------------------------------------------------

def verify_step2(
    client: DiscordClient,
    day_entry: Dict[str, Any],
    spec_required: Dict[str, Any],
    day_key: str,
) -> Dict[str, Any]:
    """Verify evening winners messages for step-2-test-then-vote-to-keep.

    Returns a result with checked=False and pass=True when winners_state is
    absent — that means evening winners haven't run yet today, which is
    expected when this script runs at 13:00 UTC before the 23:00 UTC winners
    job. An absent winners_state is only an error if this script is run after
    winners should have posted (e.g., via DAILY_DATE_UTC pointing at a past day).
    """
    ch = _empty_channel_result()
    ch["spec_criteria"] = spec_required

    winners_state = day_entry.get("winners_state")
    if not isinstance(winners_state, dict):
        ch["checked"] = False
        ch["pass"] = True  # not posted yet — not a failure
        ch["skipped_reason"] = f"No winners_state in discord_daily_posts.json for {day_key}."
        print(f"\n--- Step-2 skipped: no winners_state for {day_key} ---")
        return ch

    ch["checked"] = True

    reaction_emoji = spec_required["reactions"][0] if spec_required["reactions"] else None

    # --- Intro ---
    print("\n--- Step-2 intro ---")
    intro_state = winners_state.get("intro") or {}
    intro_message_id = str(intro_state.get("message_id") or "").strip()
    intro_channel_id = str(intro_state.get("channel_id") or "").strip()

    if intro_message_id and intro_channel_id:
        msg = check_message(client, intro_channel_id, intro_message_id, "winners intro", ch)
        ch["intro_found"] = msg is not None and bool(msg.get("content"))
    else:
        ch["errors"].append("Winners intro message_id or channel_id missing from winners_state.")
        print("  MISSING  winners intro (no message_id in state)")

    # --- Section headers ---
    print("\n--- Step-2 section headers ---")
    section_headers = winners_state.get("section_headers") or {}
    for section_key, section_state in section_headers.items():
        if not isinstance(section_state, dict):
            continue
        msg_id = str(section_state.get("message_id") or "").strip()
        ch_id = str(section_state.get("channel_id") or "").strip()
        if not (msg_id and ch_id):
            ch["errors"].append(f"Winners section header '{section_key}' missing message_id or channel_id.")
            print(f"  MISSING  {section_key} header (no message_id in state)")
            continue
        msg = check_message(client, ch_id, msg_id, f"winners {section_key} header", ch)
        if msg is not None and msg.get("content"):
            ch["sections_found"].append(section_key)

    # --- Footer (conditional on GUILD_ID having been set at post time) ---
    print("\n--- Step-2 navigation footer ---")
    footer_state = winners_state.get("footer") or {}
    footer_message_id = str(footer_state.get("message_id") or "").strip()
    footer_channel_id = str(footer_state.get("channel_id") or "").strip()

    if footer_message_id and footer_channel_id:
        msg = check_message(client, footer_channel_id, footer_message_id, "winners footer", ch)
        ch["footer_found"] = msg is not None and bool(msg.get("content"))
    else:
        print("  SKIPPED  winners footer (no message_id in state — DISCORD_GUILD_ID may not have been set at post time)")
        ch["footer_found"] = False

    # --- Winner item messages ---
    winner_messages = winners_state.get("winner_messages") or {}
    if not isinstance(winner_messages, dict):
        winner_messages = {}

    print(f"\n--- Step-2 winner messages ({len(winner_messages)} total) ---")
    seen_message_ids: List[str] = []
    for url, msg_state in winner_messages.items():
        if not isinstance(msg_state, dict):
            continue
        msg_id = str(msg_state.get("message_id") or "").strip()
        ch_id = str(msg_state.get("channel_id") or "").strip()
        label = f"winner [{url[:60]}{'…' if len(url) > 60 else ''}]"
        if not (msg_id and ch_id):
            ch["errors"].append(f"Winner message for '{url}' missing message_id or channel_id.")
            print(f"  MISSING  {label} (no message_id in state)")
            continue
        if msg_id in seen_message_ids:
            ch["errors"].append(f"Duplicate message_id {msg_id} for winner '{url}'.")
        seen_message_ids.append(msg_id)
        check_message(client, ch_id, msg_id, label, ch, check_emoji=reaction_emoji)

    duplicates_found = len(seen_message_ids) != len(set(seen_message_ids))

    # --- Pass logic driven by spec ---
    intro_ok = not spec_required["intro_required"] or ch["intro_found"]
    footer_ok = not spec_required["footer_required"] or ch["footer_found"]
    items_ok = ch["messages_checked"] >= spec_required["min_items"] if spec_required["min_items"] > 0 else True
    no_missing = len(ch["messages_missing"]) == 0
    no_dupes = not spec_required["no_duplicates"] or not duplicates_found

    ch["pass"] = intro_ok and footer_ok and items_ok and no_missing and no_dupes

    return ch


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

    specs = load_channel_specs()
    if specs:
        print(f"Loaded {CHANNEL_SPECS_FILE} ({len(specs)} channel specs)")
    else:
        print(f"WARN: No channel specs loaded — using defaults")

    daily_posts = load_daily_posts()
    day_entry = daily_posts.get(day_key)

    result: Dict[str, Any] = {
        "date": day_key,
        "timestamp": utc_now_iso(),
        "pass": False,
        "channels": {},
    }

    if not isinstance(day_entry, dict):
        result["channels"][CHANNEL_STEP1] = {
            "pass": False,
            "checked": True,
            "errors": [
                f"No entry for {day_key} in {DISCORD_DAILY_POSTS_FILE}. "
                "Daily picks may not have run yet."
            ],
        }
        result["channels"][CHANNEL_STEP2] = {
            "pass": True,
            "checked": False,
            "skipped_reason": f"No entry for {day_key} in {DISCORD_DAILY_POSTS_FILE}.",
        }
        print(f"FAIL: no entry for {day_key} in {DISCORD_DAILY_POSTS_FILE}")
        write_verification(result)
        sys.exit(1)

    session = requests.Session()
    session.headers.update({
        "Authorization": f"Bot {token}",
        "Content-Type": "application/json",
    })
    client = DiscordClient(session)

    # Verify step-1
    step1_required = get_spec_required(specs, CHANNEL_STEP1)
    print(f"\n{'='*60}")
    print(f"Verifying {CHANNEL_STEP1}")
    print(f"  Spec criteria: {step1_required}")
    result["channels"][CHANNEL_STEP1] = verify_step1(client, day_entry, step1_required, day_key)

    # Verify step-2
    step2_required = get_spec_required(specs, CHANNEL_STEP2)
    print(f"\n{'='*60}")
    print(f"Verifying {CHANNEL_STEP2}")
    print(f"  Spec criteria: {step2_required}")
    result["channels"][CHANNEL_STEP2] = verify_step2(client, day_entry, step2_required, day_key)

    # Overall pass: any checked channel that fails → overall fail
    checked_channels = {
        name: ch for name, ch in result["channels"].items() if ch.get("checked", True)
    }
    result["pass"] = all(ch.get("pass", False) for ch in checked_channels.values())

    # --- Print summary ---
    write_verification(result)

    print(f"\n{'='*60}")
    print(f"Verification summary for {day_key}")
    print(f"  Overall pass: {result['pass']}")
    for channel_name, ch in result["channels"].items():
        checked = ch.get("checked", True)
        status = "PASS" if ch.get("pass") else ("SKIP" if not checked else "FAIL")
        print(f"\n  [{status}] {channel_name}")
        if not checked:
            print(f"    skipped: {ch.get('skipped_reason', '')}")
            continue
        print(f"    messages_checked: {ch.get('messages_checked', 0)}")
        print(f"    messages_missing: {len(ch.get('messages_missing', []))}")
        print(f"    intro_found:      {ch.get('intro_found', False)}")
        print(f"    footer_found:     {ch.get('footer_found', False)}")
        print(f"    sections_found:   {ch.get('sections_found', [])}")
        if ch.get("errors"):
            for err in ch["errors"]:
                print(f"    ERROR: {err}")

    print(f"\nWrote {DISCORD_VERIFICATION_FILE}")

    if not result["pass"]:
        sys.exit(1)


if __name__ == "__main__":
    main()
