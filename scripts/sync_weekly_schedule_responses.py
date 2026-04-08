"""Sync weekly scheduling reactions from Discord into durable JSON state."""

import json
import os
import sys
import time
import urllib.parse
from typing import Any

import requests

DISCORD_API_BASE = "https://discord.com/api/v10"
REQUEST_TIMEOUT_SECONDS = 30
USER_AGENT = "steam-discord-free-games/weekly-scheduling-bot"
WEEKLY_SCHEDULE_MESSAGES_FILE = "data/scheduling/weekly_schedule_messages.json"
WEEKLY_SCHEDULE_RESPONSES_FILE = "data/scheduling/weekly_schedule_responses.json"
WEEKLY_SCHEDULE_SUMMARY_FILE = "data/scheduling/weekly_schedule_summary.json"

DAY_NAMES: list[str] = [
    "Monday",
    "Tuesday",
    "Wednesday",
    "Thursday",
    "Friday",
    "Saturday",
    "Sunday",
]

AVAILABILITY_REACTIONS: list[str] = ["✅", "🌅", "☀️", "🌙", "❌", "📝"]
SUMMARY_SLOT_ORDER: list[str] = ["✅", "🌅", "☀️", "🌙", "📝"]


def fail(message: str) -> None:
    """Print an error and exit with a non-zero status."""
    print(f"ERROR: {message}", file=sys.stderr)
    sys.exit(1)


def require_env(name: str) -> str:
    """Return a required environment variable or exit if it is missing."""
    value = os.getenv(name)
    if not value:
        fail(f"Missing required environment variable: {name}")
    return value


def check_response(response: requests.Response, context: str) -> None:
    """Exit with details when a Discord API response is not successful."""
    if not response.ok:
        print(f"ERROR: {context}", file=sys.stderr)
        print(f"HTTP status: {response.status_code}", file=sys.stderr)
        print(f"Response body: {response.text}", file=sys.stderr)
        sys.exit(1)


def ensure_parent_dir(path: str) -> None:
    """Create the parent directory for a file path if needed."""
    parent_dir = os.path.dirname(path)
    if parent_dir:
        try:
            os.makedirs(parent_dir, exist_ok=True)
        except OSError as error:
            fail(f"Failed to create directory {parent_dir}: {error}")


def load_json_file(path: str) -> dict[str, Any]:
    """Load a JSON object from disk, creating an empty one if missing."""
    if not os.path.exists(path):
        save_json_file(path, {})
        return {}

    try:
        with open(path, "r", encoding="utf-8") as file:
            loaded = json.load(file)
    except json.JSONDecodeError:
        fail(f"Invalid JSON in {path}")
    except OSError as error:
        fail(f"Failed to read {path}: {error}")

    if not isinstance(loaded, dict):
        fail(f"Expected top-level JSON object in {path}")

    return loaded


def save_json_file(path: str, data: dict[str, Any]) -> None:
    """Write a JSON object to disk using UTF-8 and pretty indentation."""
    ensure_parent_dir(path)

    try:
        with open(path, "w", encoding="utf-8") as file:
            json.dump(data, file, indent=2, ensure_ascii=False)
            file.write("\n")
    except OSError as error:
        fail(f"Failed to write {path}: {error}")


def prune_weeks(data: dict[str, Any], keep_last: int = 12) -> dict[str, Any]:
    """Keep only the latest N week entries ordered by week key."""
    week_keys = sorted(data.keys())
    if len(week_keys) <= keep_last:
        return data

    keys_to_keep = set(week_keys[-keep_last:])
    return {week_key: data[week_key] for week_key in week_keys if week_key in keys_to_keep}


def build_current_user_url() -> str:
    """Build the Discord API URL for fetching the current bot user."""
    return f"{DISCORD_API_BASE}/users/@me"


def build_reaction_users_url(thread_id: str, message_id: str, emoji: str) -> str:
    """Build the Discord API URL for listing users of a specific message reaction."""
    encoded_emoji = urllib.parse.quote(emoji, safe="")
    return (
        f"{DISCORD_API_BASE}/channels/{thread_id}/messages/"
        f"{message_id}/reactions/{encoded_emoji}"
    )


def get_bot_user_id(session: requests.Session) -> str:
    """Fetch and return the current bot user id."""
    response = session.get(build_current_user_url(), timeout=REQUEST_TIMEOUT_SECONDS)
    check_response(response, "Failed to fetch current bot user")

    try:
        payload: dict[str, Any] = response.json()
    except ValueError:
        fail("Discord response was not valid JSON when fetching current bot user")

    user_id = payload.get("id")
    if not user_id:
        fail("Discord response JSON did not include bot user id")

    return str(user_id)


def fetch_reaction_users(
    session: requests.Session, thread_id: str, message_id: str, emoji: str
) -> list[dict[str, Any]]:
    """Fetch all users who reacted with a specific emoji, handling pagination."""
    all_users: list[dict[str, Any]] = []
    after_user_id: str | None = None

    while True:
        params: dict[str, Any] = {"limit": 100}
        if after_user_id:
            params["after"] = after_user_id

        for attempt in range(1, 4):
            response = session.get(
                build_reaction_users_url(thread_id, message_id, emoji),
                params=params,
                timeout=REQUEST_TIMEOUT_SECONDS,
            )
            if response.status_code != 429:
                break

            retry_after_seconds = 1.0
            try:
                payload_429: dict[str, Any] = response.json()
                retry_after_value = payload_429.get("retry_after")
                if isinstance(retry_after_value, (int, float)):
                    retry_after_seconds = float(retry_after_value)
                elif isinstance(retry_after_value, str):
                    retry_after_seconds = float(retry_after_value)
            except (ValueError, TypeError):
                retry_after_seconds = 1.0

            if retry_after_seconds < 0:
                retry_after_seconds = 1.0

            if attempt < 3:
                print(
                    f"Rate limited fetching reaction users for emoji {emoji}, "
                    f"sleeping {retry_after_seconds} seconds before retry"
                )
                time.sleep(retry_after_seconds)

        check_response(response, f"Failed to fetch reaction users: {emoji}")

        try:
            payload = response.json()
        except ValueError:
            fail(f"Discord response was not valid JSON when fetching reaction users: {emoji}")

        if not isinstance(payload, list):
            fail(f"Discord reaction users response was not a list for emoji: {emoji}")

        if not payload:
            break

        page_users: list[dict[str, Any]] = []
        for raw_user in payload:
            if not isinstance(raw_user, dict):
                fail(f"Discord reaction users payload included a non-object for emoji: {emoji}")
            page_users.append(raw_user)

        all_users.extend(page_users)
        after_user_id = str(page_users[-1].get("id", ""))
        if not after_user_id or len(page_users) < 100:
            break

    return all_users


def build_channel_messages_url(thread_id: str) -> str:
    """Build the Discord API URL for listing messages in a thread."""
    return f"{DISCORD_API_BASE}/channels/{thread_id}/messages"


def fetch_thread_messages(session: requests.Session, thread_id: str) -> list[dict[str, Any]]:
    """Fetch all messages in a thread, handling pagination."""
    all_messages: list[dict[str, Any]] = []
    before_message_id: str | None = None

    while True:
        params: dict[str, Any] = {"limit": 100}
        if before_message_id:
            params["before"] = before_message_id

        response = session.get(
            build_channel_messages_url(thread_id),
            params=params,
            timeout=REQUEST_TIMEOUT_SECONDS,
        )
        check_response(response, f"Failed to fetch thread messages for thread_id={thread_id}")

        try:
            payload = response.json()
        except ValueError:
            fail(f"Discord response was not valid JSON when fetching thread {thread_id} messages")

        if not isinstance(payload, list):
            fail(f"Discord messages response was not a list for thread_id={thread_id}")
        if not payload:
            break

        page_messages: list[dict[str, Any]] = []
        for raw_message in payload:
            if not isinstance(raw_message, dict):
                fail(f"Discord messages payload included a non-object for thread_id={thread_id}")
            page_messages.append(raw_message)

        all_messages.extend(page_messages)
        before_message_id = str(page_messages[-1].get("id", ""))
        if not before_message_id or len(page_messages) < 100:
            break

    return all_messages


def collect_latest_custom_replies_by_day(
    thread_messages: list[dict[str, Any]], day_message_ids: set[str]
) -> dict[str, dict[str, str]]:
    """Collect latest non-empty reply text per (day_message_id, user_id)."""
    latest_reply_ids: dict[str, dict[str, int]] = {}
    latest_reply_texts: dict[str, dict[str, str]] = {}

    for message in thread_messages:
        author = message.get("author")
        if not isinstance(author, dict):
            continue
        if bool(author.get("bot")):
            continue

        user_id = str(author.get("id", ""))
        if not user_id:
            continue

        message_reference = message.get("message_reference")
        if not isinstance(message_reference, dict):
            continue

        referenced_message_id = str(message_reference.get("message_id", ""))
        if not referenced_message_id or referenced_message_id not in day_message_ids:
            continue

        content = normalize_optional_text(message.get("content"))
        if content is None:
            continue

        message_id = str(message.get("id", ""))
        try:
            message_snowflake = int(message_id)
        except (TypeError, ValueError):
            continue

        day_latest_reply_ids = latest_reply_ids.setdefault(referenced_message_id, {})
        day_latest_reply_texts = latest_reply_texts.setdefault(referenced_message_id, {})
        existing_id = day_latest_reply_ids.get(user_id)
        if existing_id is None or message_snowflake > existing_id:
            day_latest_reply_ids[user_id] = message_snowflake
            day_latest_reply_texts[user_id] = content

    return latest_reply_texts


def build_weekly_summary(weekly_responses: dict[str, Any]) -> dict[str, Any]:
    """Build derived weekly summary with day and slot overlap insights."""
    summary_by_week: dict[str, Any] = {}

    for week_key, week_data in weekly_responses.items():
        if not isinstance(week_data, dict):
            continue

        users = week_data.get("users")
        if not isinstance(users, dict):
            continue

        day_counts: dict[str, int] = {day: 0 for day in DAY_NAMES}
        slot_counts: dict[str, dict[str, int]] = {
            day: {slot: 0 for slot in SUMMARY_SLOT_ORDER} for day in DAY_NAMES
        }

        for user_data in users.values():
            if not isinstance(user_data, dict):
                continue
            days = user_data.get("days")
            if not isinstance(days, dict):
                continue

            for day_name in DAY_NAMES:
                day_entry = days.get(day_name, {})
                reactions: list[str] = []
                if isinstance(day_entry, dict):
                    raw_reactions = day_entry.get("reactions", [])
                    if isinstance(raw_reactions, list):
                        reactions = [reaction for reaction in raw_reactions if isinstance(reaction, str)]
                elif isinstance(day_entry, list):
                    reactions = [reaction for reaction in day_entry if isinstance(reaction, str)]

                reaction_set = set(reactions)
                if reaction_set.intersection(SUMMARY_SLOT_ORDER):
                    day_counts[day_name] += 1

                for slot in SUMMARY_SLOT_ORDER:
                    if slot in reaction_set:
                        slot_counts[day_name][slot] += 1

        most_available_day = max(
            DAY_NAMES,
            key=lambda day_name: (day_counts[day_name], -DAY_NAMES.index(day_name)),
        )
        best_overlap_day, best_overlap_slot = max(
            [(day_name, slot) for day_name in DAY_NAMES for slot in SUMMARY_SLOT_ORDER],
            key=lambda item: (
                slot_counts[item[0]][item[1]],
                -DAY_NAMES.index(item[0]),
                -SUMMARY_SLOT_ORDER.index(item[1]),
            ),
        )

        summary_by_week[week_key] = {
            "date_range": week_data.get("date_range"),
            "summary": {
                "day_counts": day_counts,
                "slot_counts": slot_counts,
                "most_available_day": most_available_day,
                "best_overlap": {
                    "day": best_overlap_day,
                    "slot": best_overlap_slot,
                    "count": slot_counts[best_overlap_day][best_overlap_slot],
                },
            },
        }

    return prune_weeks(summary_by_week, keep_last=12)


def normalize_optional_text(value: Any) -> str | None:
    """Return a stripped string value when present, otherwise None."""
    if isinstance(value, str):
        stripped = value.strip()
        if stripped:
            return stripped
    return None


def main() -> None:
    """Fetch and persist reaction responses for recorded scheduled weeks."""
    token = require_env("DISCORD_SCHEDULING_BOT_TOKEN")

    weekly_messages = load_json_file(WEEKLY_SCHEDULE_MESSAGES_FILE)
    if not weekly_messages:
        print("No weekly message state found; nothing to sync")
        return

    week_keys = sorted(weekly_messages.keys())[-12:]
    print(f"Starting weekly schedule response sync for {len(week_keys)} week(s)")

    with requests.Session() as session:
        session.headers.update(
            {
                "Authorization": f"Bot {token}",
                "Content-Type": "application/json",
                "User-Agent": USER_AGENT,
            }
        )

        bot_user_id = get_bot_user_id(session)
        print(f"Fetched bot user (user_id={bot_user_id})")

        weekly_responses: dict[str, Any] = {}

        for week_key in week_keys:
            latest_week_data = weekly_messages.get(week_key)
            if not isinstance(latest_week_data, dict):
                fail(f"Invalid week payload for {week_key} in {WEEKLY_SCHEDULE_MESSAGES_FILE}")

            thread_id = latest_week_data.get("thread_id")
            date_range = latest_week_data.get("date_range")
            days = latest_week_data.get("days")

            if not thread_id or not isinstance(thread_id, str):
                fail(f"Missing or invalid thread_id for {week_key} in {WEEKLY_SCHEDULE_MESSAGES_FILE}")
            if not date_range or not isinstance(date_range, str):
                fail(f"Missing or invalid date_range for {week_key} in {WEEKLY_SCHEDULE_MESSAGES_FILE}")
            if not isinstance(days, dict):
                fail(f"Missing or invalid days mapping for {week_key} in {WEEKLY_SCHEDULE_MESSAGES_FILE}")

            users_map: dict[str, dict[str, Any]] = {}
            day_message_ids = {str(day_id) for day_id in days.values() if day_id}
            thread_messages = fetch_thread_messages(session, thread_id)
            latest_custom_replies = collect_latest_custom_replies_by_day(
                thread_messages, day_message_ids
            )
            for day_name in DAY_NAMES:
                day_message_id = days.get(day_name)
                if not day_message_id:
                    fail(
                        f"Missing message ID for {day_name} in week {week_key} "
                        f"from {WEEKLY_SCHEDULE_MESSAGES_FILE}"
                    )

                for reaction in AVAILABILITY_REACTIONS:
                    reaction_users = fetch_reaction_users(session, thread_id, str(day_message_id), reaction)
                    for user in reaction_users:
                        user_id = str(user.get("id", ""))
                        if not user_id:
                            continue

                        if user_id == bot_user_id:
                            continue
                        if bool(user.get("bot")):
                            continue

                        if user_id not in users_map:
                            users_map[user_id] = {
                                "username": normalize_optional_text(user.get("username")),
                                "global_name": normalize_optional_text(user.get("global_name")),
                                "days": {
                                    day: {
                                        "reactions": [],
                                        "custom_reply": None,
                                    }
                                    for day in DAY_NAMES
                                },
                            }
                        else:
                            if users_map[user_id]["username"] is None:
                                users_map[user_id]["username"] = normalize_optional_text(
                                    user.get("username")
                                )
                            if users_map[user_id]["global_name"] is None:
                                users_map[user_id]["global_name"] = normalize_optional_text(
                                    user.get("global_name")
                                )

                        day_entry = users_map[user_id]["days"][day_name]
                        if reaction not in day_entry["reactions"]:
                            day_entry["reactions"].append(reaction)

                        if reaction == "📝":
                            custom_reply = latest_custom_replies.get(str(day_message_id), {}).get(user_id)
                            day_entry["custom_reply"] = custom_reply

            stable_users_map = {
                user_id: users_map[user_id]
                for user_id in sorted(users_map, key=lambda value: int(value))
            }
            weekly_responses[week_key] = {
                "date_range": date_range,
                "users": stable_users_map,
            }
            print(f"Synced week {week_key}")

    pruned_weekly_responses = prune_weeks(weekly_responses, keep_last=12)
    save_json_file(WEEKLY_SCHEDULE_RESPONSES_FILE, pruned_weekly_responses)
    weekly_summary = build_weekly_summary(pruned_weekly_responses)
    save_json_file(WEEKLY_SCHEDULE_SUMMARY_FILE, weekly_summary)
    print(
        f"Saved weekly schedule responses for {len(pruned_weekly_responses)} week(s) "
        f"to {WEEKLY_SCHEDULE_RESPONSES_FILE}"
    )
    print(
        f"Saved weekly schedule summary for {len(weekly_summary)} week(s) "
        f"to {WEEKLY_SCHEDULE_SUMMARY_FILE}"
    )
    print("Finished successfully")


if __name__ == "__main__":
    main()
