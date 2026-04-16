"""Post weekly availability prompts to a Discord scheduling channel and add reactions."""

import os
import sys
import urllib.parse
from datetime import date, datetime, timedelta, timezone
from typing import Any

import requests

from discord_api import DiscordClient, DiscordMessageNotFoundError
from scripts.scheduling_labels import DAY_MESSAGE_TEMPLATES, format_day_label
from state_utils import load_json_object, prune_latest_keys, save_json_object_atomic

USER_AGENT = "steam-discord-free-games/weekly-scheduling-bot"
WEEKLY_SCHEDULE_MESSAGES_FILE = "data/scheduling/weekly_schedule_messages.json"

INTRO_MESSAGE_TEMPLATE = """🗓️ Weekly Availability — react below for next week
Week of {date_range}

React on each day message with your availability.
Use 📝 only if you want to reply with a custom time.

Availability:

✅ Free all day (alpha chad)
🌅 Morning
☀️ Afternoon
🌙 Evening
❌ Not free (I'm a gayboi)
📝 Other / custom time

If needed, reply under a day message for custom availability, for example:
Tue 7–9 PM
Wed after 6
Sat 1–4 PM"""

AVAILABILITY_REACTIONS: list[str] = ["✅", "🌅", "☀️", "🌙", "❌", "📝"]


def fail(message: str) -> None:
    print(f"ERROR: {message}", file=sys.stderr)
    sys.exit(1)


def require_env(name: str) -> str:
    value = os.getenv(name)
    if not value:
        fail(f"Missing required environment variable: {name}")
    return value


def get_week_bounds(today: date | None = None, manual_week_start: str | None = None) -> tuple[date, date]:
    if manual_week_start:
        try:
            monday = date.fromisoformat(manual_week_start)
        except ValueError:
            fail("SCHEDULE_WEEK_START must be in YYYY-MM-DD format")
        if monday.weekday() != 0:
            fail("SCHEDULE_WEEK_START must be a Monday")
        return monday, monday + timedelta(days=6)

    current_date = today or date.today()
    days_until_next_monday = (7 - current_date.weekday()) % 7
    if days_until_next_monday == 0:
        days_until_next_monday = 7
    monday = current_date + timedelta(days=days_until_next_monday)
    return monday, monday + timedelta(days=6)


def format_week_date_range(start_date: date, end_date: date) -> str:
    if start_date.year == end_date.year:
        if start_date.month == end_date.month:
            return f"{start_date:%b} {start_date.day}–{end_date.day}, {start_date.year}"
        return f"{start_date:%b} {start_date.day}–{end_date:%b} {end_date.day}, {start_date.year}"
    return f"{start_date:%b} {start_date.day}, {start_date.year}–{end_date:%b} {end_date.day}, {end_date.year}"


def get_week_key(start_date: date, end_date: date) -> str:
    return f"{start_date.isoformat()}_to_{end_date.isoformat()}"


def utc_timestamp() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).strftime("%Y-%m-%dT%H:%M:%SZ")


def try_get_message(client: DiscordClient, channel_id: str, message_id: str, context: str) -> bool:
    try:
        client.get_message(channel_id, message_id, context=context)
        return True
    except DiscordMessageNotFoundError:
        print(f"RECOVER: stale/deleted Discord message detected ({context}, message_id={message_id})")
        return False
    except Exception as error:
        print(f"WARN: could not verify message ({context}, message_id={message_id}): {error}")
        return False


def format_day_message(day_name: str, emoji: str, day_date: date) -> str:
    _ = emoji
    return format_day_label(day_name, day_date, include_emoji=True)


def ensure_day_reactions(client: DiscordClient, channel_id: str, day_name: str, message_id: str) -> None:
    for reaction in AVAILABILITY_REACTIONS:
        encoded = urllib.parse.quote(reaction, safe="")
        client.put_reaction(
            channel_id,
            message_id,
            encoded,
            context=f"seed reaction {reaction} for {day_name}",
        )


def main() -> None:
    token = require_env("DISCORD_SCHEDULING_BOT_TOKEN")
    channel_id = require_env("DISCORD_SCHEDULING_CHANNEL_ID")
    manual_week_start = os.getenv("SCHEDULE_WEEK_START", "").strip() or None

    week_start, week_end = get_week_bounds(manual_week_start=manual_week_start)
    week_key = get_week_key(week_start, week_end)
    date_range = format_week_date_range(week_start, week_end)
    intro_message = INTRO_MESSAGE_TEMPLATE.format(date_range=date_range)

    print(
        f"Starting weekly availability post (channel_id={channel_id}, week_key={week_key}, manual={bool(manual_week_start)})"
    )

    weekly_messages = load_json_object(WEEKLY_SCHEDULE_MESSAGES_FILE, log=print)
    existing_week_state = weekly_messages.get(week_key)
    if not isinstance(existing_week_state, dict):
        existing_week_state = {}

    with requests.Session() as session:
        session.headers.update(
            {
                "Authorization": f"Bot {token}",
                "Content-Type": "application/json",
                "User-Agent": USER_AGENT,
            }
        )
        client = DiscordClient(session)

        intro_message_id = existing_week_state.get("intro_message_id")
        if isinstance(intro_message_id, str) and intro_message_id and try_get_message(
            client, channel_id, intro_message_id, f"verify intro for {week_key}"
        ):
            print(f"REUSE: intro message for {week_key} (message_id={intro_message_id})")
        else:
            payload = client.post_message(channel_id, intro_message, context=f"post intro for {week_key}")
            intro_message_id = str(payload.get("id", ""))
            if not intro_message_id:
                fail("Discord response JSON did not include intro message id")
            print(f"CREATE: intro message for {week_key} (message_id={intro_message_id})")

        # Pin the intro message; unpin any stale weekly availability posts.
        pinned_messages = client.get_pinned_messages(channel_id, context=f"fetch pinned for {week_key}")
        already_pinned_ids = {str(p.get("id") or "") for p in pinned_messages}
        for pinned in pinned_messages:
            pinned_id = str(pinned.get("id") or "")
            pinned_content = str(pinned.get("content") or "")
            if pinned_id and pinned_id != intro_message_id and pinned_content.startswith("🗓️ Weekly Availability"):
                client.unpin_message(channel_id, pinned_id, context=f"unpin old weekly availability {pinned_id}")
                print(f"UNPIN: old weekly availability message {pinned_id}")
        if intro_message_id not in already_pinned_ids:
            client.pin_message(channel_id, intro_message_id, context=f"pin intro for {week_key}")
            print(f"PIN: intro message for {week_key} (message_id={intro_message_id})")

        existing_days = existing_week_state.get("days")
        if not isinstance(existing_days, dict):
            existing_days = {}

        day_message_ids: dict[str, str] = {}
        created_days = 0
        for day_name, emoji, day_offset in DAY_MESSAGE_TEMPLATES:
            day_date = week_start + timedelta(days=day_offset)
            day_message = format_day_message(day_name, emoji, day_date)
            existing_day_id = existing_days.get(day_name)
            if isinstance(existing_day_id, str) and existing_day_id and try_get_message(
                client,
                channel_id,
                existing_day_id,
                context=f"verify {day_name} for {week_key}",
            ):
                day_message_ids[day_name] = existing_day_id
                print(f"REUSE: day message {day_name} (message_id={existing_day_id})")
                continue

            payload = client.post_message(channel_id, day_message, context=f"post {day_name} for {week_key}")
            day_message_id = str(payload.get("id", ""))
            if not day_message_id:
                fail(f"Discord response JSON did not include {day_name} message id")

            ensure_day_reactions(client, channel_id, day_name, day_message_id)
            day_message_ids[day_name] = day_message_id
            created_days += 1
            print(f"CREATE: day message {day_name} (message_id={day_message_id})")

    weekly_messages[week_key] = {
        "channel_id": channel_id,
        "date_range": date_range,
        "created_at_utc": existing_week_state.get("created_at_utc") or utc_timestamp(),
        "updated_at_utc": utc_timestamp(),
        "intro_message_id": intro_message_id,
        "days": day_message_ids,
        "post_completed": len(day_message_ids) == len(DAY_MESSAGE_TEMPLATES),
    }

    pruned = prune_latest_keys(weekly_messages, keep_last=12)
    if len(pruned) < len(weekly_messages):
        print(f"RETENTION: pruned weekly schedule history from {len(weekly_messages)} to {len(pruned)} weeks")

    save_json_object_atomic(WEEKLY_SCHEDULE_MESSAGES_FILE, pruned)
    if created_days == 0 and existing_week_state:
        print(f"SKIP: week {week_key} already posted and reused")
    else:
        print(f"Saved weekly schedule message IDs for {week_key} (created_days={created_days})")


if __name__ == "__main__":
    main()
