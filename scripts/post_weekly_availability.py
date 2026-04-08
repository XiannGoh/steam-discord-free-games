"""Post weekly availability prompts to a Discord scheduling thread and add reactions."""

import json
import os
import sys
import time
import urllib.parse
from datetime import date, datetime, timedelta, timezone
from typing import Any

import requests


DISCORD_API_BASE = "https://discord.com/api/v10"
REQUEST_TIMEOUT_SECONDS = 30
USER_AGENT = "steam-discord-free-games/weekly-scheduling-bot"
WEEKLY_SCHEDULE_MESSAGES_FILE = "data/scheduling/weekly_schedule_messages.json"

INTRO_MESSAGE_TEMPLATE = """🗓️ Weekly Availability — react below for next week
Week of {date_range}

React on each day message with your availability.
Use 📝 only if you want to reply with a custom time.

Availability:
- ✅ Free all day (alpha chad)
- 🌅 Morning
- ☀️ Afternoon
- 🌙 Evening
- ❌ Not free (I'm a gayboi)
- 📝 Other / custom time

If needed, reply under a day message for custom availability, for example:
Tue 7–9 PM
Wed after 6
Sat 1–4 PM"""

DAY_MESSAGES: list[tuple[str, str]] = [
    ("Monday", "🇲 Monday"),
    ("Tuesday", "🇹 Tuesday"),
    ("Wednesday", "🇼 Wednesday"),
    ("Thursday", "🇷 Thursday"),
    ("Friday", "🇫 Friday"),
    ("Saturday", "🇸 Saturday"),
    ("Sunday", "🇺 Sunday"),
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


def build_message_url(thread_id: str) -> str:
    """Build the Discord API URL for creating a message in a thread."""
    return f"{DISCORD_API_BASE}/channels/{thread_id}/messages"


def build_reaction_url(thread_id: str, message_id: str, emoji: str) -> str:
    """Build the Discord API URL for adding a reaction to a message."""
    encoded_emoji = urllib.parse.quote(emoji, safe="")
    return (
        f"{DISCORD_API_BASE}/channels/{thread_id}/messages/"
        f"{message_id}/reactions/{encoded_emoji}/@me"
    )


def get_next_week_bounds(today: date | None = None) -> tuple[date, date]:
    """Return next week's Monday and Sunday dates."""
    current_date = today or date.today()

    days_until_next_monday = (7 - current_date.weekday()) % 7
    if days_until_next_monday == 0:
        days_until_next_monday = 7

    next_monday = current_date + timedelta(days=days_until_next_monday)
    next_sunday = next_monday + timedelta(days=6)
    return next_monday, next_sunday


def get_next_week_date_range(today: date | None = None) -> str:
    """Return next week's Monday-Sunday range, formatted for the intro message."""
    next_monday, next_sunday = get_next_week_bounds(today=today)
    return format_week_date_range(next_monday, next_sunday)


def format_week_date_range(start_date: date, end_date: date) -> str:
    """Return a Monday-Sunday range formatted for the intro message."""
    next_monday = start_date
    next_sunday = end_date

    if next_monday.year == next_sunday.year:
        if next_monday.month == next_sunday.month:
            return f"{next_monday:%b} {next_monday.day}–{next_sunday.day}, {next_monday.year}"
        return f"{next_monday:%b} {next_monday.day}–{next_sunday:%b} {next_sunday.day}, {next_monday.year}"

    return (
        f"{next_monday:%b} {next_monday.day}, {next_monday.year}"
        f"–{next_sunday:%b} {next_sunday.day}, {next_sunday.year}"
    )


def get_week_key(start_date: date, end_date: date) -> str:
    """Return the stable week key for persisted message IDs."""
    return f"{start_date.isoformat()}_to_{end_date.isoformat()}"


def ensure_parent_dir(path: str) -> None:
    """Create the parent directory for a file path if needed."""
    parent_dir = os.path.dirname(path)
    if parent_dir:
        try:
            os.makedirs(parent_dir, exist_ok=True)
        except OSError as error:
            fail(f"Failed to create directory {parent_dir}: {error}")


def get_current_utc_timestamp() -> str:
    """Return the current UTC timestamp in ISO-like format with Z suffix."""
    return datetime.now(timezone.utc).replace(microsecond=0).strftime("%Y-%m-%dT%H:%M:%SZ")


def prune_weeks(data: dict[str, Any], keep_last: int = 12) -> dict[str, Any]:
    """Keep only the latest N week entries ordered by week key."""
    week_keys = sorted(data.keys())
    if len(week_keys) <= keep_last:
        return data

    keys_to_keep = set(week_keys[-keep_last:])
    return {week_key: data[week_key] for week_key in week_keys if week_key in keys_to_keep}


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


def post_message(session: requests.Session, thread_id: str, content: str) -> str:
    """Post a message and return the created Discord message ID."""
    url = build_message_url(thread_id)
    response = session.post(url, json={"content": content}, timeout=REQUEST_TIMEOUT_SECONDS)
    check_response(response, "Failed to post Discord message")

    try:
        payload: dict[str, Any] = response.json()
    except ValueError:
        fail("Discord response was not valid JSON when posting message")

    message_id = payload.get("id")
    if not message_id:
        fail("Discord response JSON did not include message id")

    return str(message_id)


def add_reaction(
    session: requests.Session, thread_id: str, message_id: str, emoji: str
) -> None:
    """Add a single emoji reaction to a posted Discord message."""
    url = build_reaction_url(thread_id, message_id, emoji)

    for attempt in range(1, 4):
        response = session.put(url, timeout=REQUEST_TIMEOUT_SECONDS)
        if response.status_code != 429:
            check_response(response, f"Failed to add reaction: {emoji}")
            return

        retry_after_seconds = 1.0
        try:
            payload: dict[str, Any] = response.json()
            retry_after_value = payload.get("retry_after")
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
                f"Rate limited adding reaction {emoji}, "
                f"sleeping {retry_after_seconds} seconds before retry"
            )
            time.sleep(retry_after_seconds)

    check_response(response, f"Failed to add reaction: {emoji}")


def main() -> None:
    """Run the weekly availability post flow and seed day-specific reactions."""
    token = require_env("DISCORD_SCHEDULING_BOT_TOKEN")
    thread_id = require_env("DISCORD_SCHEDULING_THREAD_ID")

    print(f"Starting weekly availability post (thread_id={thread_id})")

    with requests.Session() as session:
        session.headers.update(
            {
                "Authorization": f"Bot {token}",
                "Content-Type": "application/json",
                "User-Agent": USER_AGENT,
            }
        )

        week_start, week_end = get_next_week_bounds()
        week_key = get_week_key(week_start, week_end)
        date_range = format_week_date_range(week_start, week_end)
        intro_message = INTRO_MESSAGE_TEMPLATE.format(date_range=date_range)

        intro_message_id = post_message(session, thread_id, intro_message)
        print(f"Posted intro message (message_id={intro_message_id})")

        day_message_ids: dict[str, str] = {}
        for day_name, day_message in DAY_MESSAGES:
            day_message_id = post_message(session, thread_id, day_message)
            print(f"Posted day message: {day_name} (message_id={day_message_id})")
            day_message_ids[day_name] = day_message_id

            for reaction in AVAILABILITY_REACTIONS:
                add_reaction(session, thread_id, day_message_id, reaction)
                print(f"Added reaction {reaction} to {day_name}")

    weekly_messages = load_json_file(WEEKLY_SCHEDULE_MESSAGES_FILE)
    weekly_messages[week_key] = {
        "thread_id": thread_id,
        "date_range": date_range,
        "created_at_utc": get_current_utc_timestamp(),
        "intro_message_id": intro_message_id,
        "days": day_message_ids,
    }
    pruned_weekly_messages = prune_weeks(weekly_messages, keep_last=12)
    if len(pruned_weekly_messages) < len(weekly_messages):
        print("Pruned weekly schedule history to last 12 weeks")

    save_json_file(WEEKLY_SCHEDULE_MESSAGES_FILE, pruned_weekly_messages)
    print(
        f"Saved weekly schedule message IDs for {week_key} "
        f"to {WEEKLY_SCHEDULE_MESSAGES_FILE}"
    )

    print("Finished successfully")


if __name__ == "__main__":
    main()
