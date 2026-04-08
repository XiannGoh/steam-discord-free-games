"""Sync weekly scheduling reaction responses from Discord into durable JSON state."""

import json
import os
import sys
import time
import urllib.parse
from datetime import datetime, timezone
from typing import Any

import requests


DISCORD_API_BASE = "https://discord.com/api/v10"
REQUEST_TIMEOUT_SECONDS = 30
USER_AGENT = "steam-discord-free-games/weekly-scheduling-bot"
WEEKLY_SCHEDULE_MESSAGES_FILE = "data/scheduling/weekly_schedule_messages.json"
WEEKLY_SCHEDULE_RESPONSES_FILE = "data/scheduling/weekly_schedule_responses.json"

DAY_NAMES: list[str] = [
    "Monday",
    "Tuesday",
    "Wednesday",
    "Thursday",
    "Friday",
    "Saturday",
    "Sunday",
]

AVAILABILITY_REACTIONS: list[str] = [
    "✅",
    "🌅",
    "☀️",
    "🌙",
    "❌",
    "📝",
]


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


def get_current_utc_timestamp() -> str:
    """Return the current UTC timestamp in ISO-like format with Z suffix."""
    return datetime.now(timezone.utc).replace(microsecond=0).strftime("%Y-%m-%dT%H:%M:%SZ")


def get_latest_week_key(data: dict[str, Any]) -> str:
    """Return the latest week key by chronological (ISO string) order."""
    if not data:
        fail(f"No weekly message state found in {WEEKLY_SCHEDULE_MESSAGES_FILE}")

    return sorted(data.keys())[-1]


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


def get_bot_user(session: requests.Session) -> dict[str, Any]:
    """Fetch and return the current bot user object."""
    response = session.get(build_current_user_url(), timeout=REQUEST_TIMEOUT_SECONDS)
    check_response(response, "Failed to fetch current bot user")

    try:
        payload: dict[str, Any] = response.json()
    except ValueError:
        fail("Discord response was not valid JSON when fetching current bot user")

    user_id = payload.get("id")
    if not user_id:
        fail("Discord response JSON did not include bot user id")

    return payload


def normalize_user(user: dict[str, Any]) -> dict[str, Any]:
    """Normalize a Discord user object for stored response data."""
    user_id = user.get("id")
    username = user.get("username")

    if not user_id or not username:
        fail("Discord reaction user object missing required id/username fields")

    return {
        "user_id": str(user_id),
        "username": str(username),
        "global_name": user.get("global_name"),
    }


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


def main() -> None:
    """Fetch and persist reaction responses for the latest scheduled week."""
    token = require_env("DISCORD_SCHEDULING_BOT_TOKEN")

    weekly_messages = load_json_file(WEEKLY_SCHEDULE_MESSAGES_FILE)
    week_key = get_latest_week_key(weekly_messages)

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

    print(f"Starting weekly schedule response sync (thread_id={thread_id}, week_key={week_key})")

    with requests.Session() as session:
        session.headers.update(
            {
                "Authorization": f"Bot {token}",
                "Content-Type": "application/json",
                "User-Agent": USER_AGENT,
            }
        )

        bot_user = get_bot_user(session)
        bot_user_id = str(bot_user["id"])
        print(f"Fetched bot user (user_id={bot_user_id})")

        responses_by_day: dict[str, dict[str, list[dict[str, Any]]]] = {}

        for day_name in DAY_NAMES:
            day_message_id = days.get(day_name)
            if not day_message_id:
                fail(
                    f"Missing message ID for {day_name} in week {week_key} "
                    f"from {WEEKLY_SCHEDULE_MESSAGES_FILE}"
                )

            responses_by_day[day_name] = {}
            for reaction in AVAILABILITY_REACTIONS:
                reaction_users = fetch_reaction_users(session, thread_id, str(day_message_id), reaction)
                filtered_users = [
                    normalize_user(user)
                    for user in reaction_users
                    if str(user.get("id", "")) != bot_user_id
                ]
                responses_by_day[day_name][reaction] = filtered_users
                print(
                    f"Fetched {len(filtered_users)} human users for "
                    f"{day_name} reaction {reaction}"
                )

    weekly_responses = load_json_file(WEEKLY_SCHEDULE_RESPONSES_FILE)
    weekly_responses[week_key] = {
        "thread_id": thread_id,
        "date_range": date_range,
        "created_at_utc": get_current_utc_timestamp(),
        "days": responses_by_day,
    }

    pruned_weekly_responses = prune_weeks(weekly_responses, keep_last=12)
    if len(pruned_weekly_responses) < len(weekly_responses):
        print("Pruned weekly schedule history to last 12 weeks")

    save_json_file(WEEKLY_SCHEDULE_RESPONSES_FILE, pruned_weekly_responses)
    print(
        f"Saved weekly schedule responses for {week_key} "
        f"to {WEEKLY_SCHEDULE_RESPONSES_FILE}"
    )
    print("Finished successfully")


if __name__ == "__main__":
    main()
