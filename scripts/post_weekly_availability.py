"""Post weekly availability prompts to a Discord scheduling thread and add reactions."""

import os
import sys
import time
import urllib.parse
from datetime import date, timedelta
from typing import Any

import requests


DISCORD_API_BASE = "https://discord.com/api/v10"
REQUEST_TIMEOUT_SECONDS = 30
USER_AGENT = "steam-discord-free-games/weekly-scheduling-bot"

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


def get_next_week_date_range(today: date | None = None) -> str:
    """Return next week's Monday-Sunday range, formatted for the intro message."""
    current_date = today or date.today()

    days_until_next_monday = (7 - current_date.weekday()) % 7
    if days_until_next_monday == 0:
        days_until_next_monday = 7

    next_monday = current_date + timedelta(days=days_until_next_monday)
    next_sunday = next_monday + timedelta(days=6)

    if next_monday.year == next_sunday.year:
        if next_monday.month == next_sunday.month:
            return f"{next_monday:%b} {next_monday.day}–{next_sunday:%b} {next_sunday.day}, {next_monday.year}"
        return f"{next_monday:%b} {next_monday.day}–{next_sunday:%b} {next_sunday.day}, {next_monday.year}"

    return (
        f"{next_monday:%b} {next_monday.day}, {next_monday.year}"
        f"–{next_sunday:%b} {next_sunday.day}, {next_sunday.year}"
    )


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

        date_range = get_next_week_date_range()
        intro_message = INTRO_MESSAGE_TEMPLATE.format(date_range=date_range)

        intro_message_id = post_message(session, thread_id, intro_message)
        print(f"Posted intro message (message_id={intro_message_id})")

        for day_name, day_message in DAY_MESSAGES:
            day_message_id = post_message(session, thread_id, day_message)
            print(f"Posted day message: {day_name} (message_id={day_message_id})")

            for reaction in AVAILABILITY_REACTIONS:
                add_reaction(session, thread_id, day_message_id, reaction)
                print(f"Added reaction {reaction} to {day_name}")

    print("Finished successfully")


if __name__ == "__main__":
    main()
