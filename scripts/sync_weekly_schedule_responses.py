"""Sync weekly scheduling reactions from Discord into durable JSON state."""

import json
import os
import sys
import time
import urllib.parse
from typing import Any

import requests

from discord_api import DiscordClient, DiscordMessageNotFoundError
from state_utils import load_json_object, prune_latest_keys, save_json_object_atomic

DISCORD_API_BASE = "https://discord.com/api/v10"
REQUEST_TIMEOUT_SECONDS = 30
USER_AGENT = "steam-discord-free-games/weekly-scheduling-bot"
WEEKLY_SCHEDULE_MESSAGES_FILE = "data/scheduling/weekly_schedule_messages.json"
WEEKLY_SCHEDULE_RESPONSES_FILE = "data/scheduling/weekly_schedule_responses.json"
WEEKLY_SCHEDULE_SUMMARY_FILE = "data/scheduling/weekly_schedule_summary.json"
EXPECTED_SCHEDULE_ROSTER_FILE = "data/scheduling/expected_schedule_roster.json"
WEEKLY_SCHEDULE_BOT_OUTPUTS_FILE = "data/scheduling/weekly_schedule_bot_outputs.json"

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
SUMMARY_DISPLAY_ORDER: list[str] = ["✅", "🌅", "☀️", "🌙", "📝", "❌"]


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


def load_json_file(path: str) -> dict[str, Any]:
    """Load a JSON object from disk."""
    return load_json_object(path, log=print)


def save_json_file(path: str, data: dict[str, Any]) -> None:
    """Write a JSON object to disk using atomic persistence."""
    try:
        save_json_object_atomic(path, data)
    except OSError as error:
        fail(f"Failed to write {path}: {error}")


def prune_weeks(data: dict[str, Any], keep_last: int = 12) -> dict[str, Any]:
    """Keep only the latest N week entries ordered by week key."""
    return prune_latest_keys(data, keep_last=keep_last)


def build_current_user_url() -> str:
    """Build the Discord API URL for fetching the current bot user."""
    return f"{DISCORD_API_BASE}/users/@me"


def build_reaction_users_url(channel_id: str, message_id: str, emoji: str) -> str:
    """Build the Discord API URL for listing users of a specific message reaction."""
    encoded_emoji = urllib.parse.quote(emoji, safe="")
    return (
        f"{DISCORD_API_BASE}/channels/{channel_id}/messages/"
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
    session: requests.Session, channel_id: str, message_id: str, emoji: str
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
                build_reaction_users_url(channel_id, message_id, emoji),
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


def build_channel_messages_url(channel_id: str) -> str:
    """Build the Discord API URL for listing messages in a channel."""
    return f"{DISCORD_API_BASE}/channels/{channel_id}/messages"


def build_create_message_url(channel_id: str) -> str:
    """Build the Discord API URL for posting a message in a channel."""
    return f"{DISCORD_API_BASE}/channels/{channel_id}/messages"


def build_edit_message_url(channel_id: str, message_id: str) -> str:
    """Build the Discord API URL for editing an existing channel message."""
    return f"{DISCORD_API_BASE}/channels/{channel_id}/messages/{message_id}"


def fetch_channel_messages(session: requests.Session, channel_id: str) -> list[dict[str, Any]]:
    """Fetch all messages in a channel, handling pagination."""
    all_messages: list[dict[str, Any]] = []
    before_message_id: str | None = None

    while True:
        params: dict[str, Any] = {"limit": 100}
        if before_message_id:
            params["before"] = before_message_id

        response = session.get(
            build_channel_messages_url(channel_id),
            params=params,
            timeout=REQUEST_TIMEOUT_SECONDS,
        )
        check_response(response, f"Failed to fetch channel messages for channel_id={channel_id}")

        try:
            payload = response.json()
        except ValueError:
            fail(f"Discord response was not valid JSON when fetching channel {channel_id} messages")

        if not isinstance(payload, list):
            fail(f"Discord messages response was not a list for channel_id={channel_id}")
        if not payload:
            break

        page_messages: list[dict[str, Any]] = []
        for raw_message in payload:
            if not isinstance(raw_message, dict):
                fail(f"Discord messages payload included a non-object for channel_id={channel_id}")
            page_messages.append(raw_message)

        all_messages.extend(page_messages)
        before_message_id = str(page_messages[-1].get("id", ""))
        if not before_message_id or len(page_messages) < 100:
            break

    return all_messages


def collect_latest_custom_replies_by_day(
    channel_messages: list[dict[str, Any]], day_message_ids: set[str]
) -> dict[str, dict[str, str]]:
    """Collect latest non-empty reply text per (day_message_id, user_id)."""
    latest_reply_ids: dict[str, dict[str, int]] = {}
    latest_reply_texts: dict[str, dict[str, str]] = {}

    for message in channel_messages:
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


def get_week_channel_id(week_data: dict[str, Any], week_key: str) -> str:
    """Return channel_id from weekly state with a fallback for legacy thread_id data."""
    channel_id = week_data.get("channel_id")
    if isinstance(channel_id, str) and channel_id:
        return channel_id

    legacy_thread_id = week_data.get("thread_id")
    if isinstance(legacy_thread_id, str) and legacy_thread_id:
        print(
            f"Legacy thread_id detected for {week_key}; "
            "using it as channel_id for backward compatibility"
        )
        return legacy_thread_id

    fail(f"Missing or invalid channel_id for {week_key} in {WEEKLY_SCHEDULE_MESSAGES_FILE}")


def compute_missing_user_ids_for_week(
    week_responses: dict[str, Any], roster: dict[str, Any]
) -> list[str]:
    """Return sorted active roster user IDs with no reactions on every day."""
    users = roster.get("users")
    if not isinstance(users, dict):
        fail(f"Missing or invalid users object in {EXPECTED_SCHEDULE_ROSTER_FILE}")

    week_users = week_responses.get("users")
    if not isinstance(week_users, dict):
        fail("Missing or invalid users object in current week responses")

    missing_user_ids: list[str] = []
    for user_id in sorted(users, key=lambda value: int(value)):
        roster_user = users.get(user_id)
        if not isinstance(roster_user, dict):
            continue
        if roster_user.get("is_active") is not True:
            continue

        response_user = week_users.get(user_id)
        has_any_reaction = False
        if isinstance(response_user, dict):
            days = response_user.get("days")
            if isinstance(days, dict):
                for day_name in DAY_NAMES:
                    day_entry = days.get(day_name)
                    if not isinstance(day_entry, dict):
                        continue
                    reactions = day_entry.get("reactions")
                    if isinstance(reactions, list) and any(
                        isinstance(reaction, str) and reaction in AVAILABILITY_REACTIONS
                        for reaction in reactions
                    ):
                        has_any_reaction = True
                        break

        if not has_any_reaction:
            missing_user_ids.append(user_id)

    return missing_user_ids


def format_reminder_message(date_range: str, missing_user_ids: list[str]) -> str:
    """Build a deterministic reminder message for users missing availability reactions."""
    display_lines = [f"- <@{user_id}>" for user_id in missing_user_ids]

    lines = [
        f"⏰ Still waiting on availability for {date_range}",
        "",
        "Please react on the day messages if you have not responded yet:",
        "",
        *display_lines,
    ]
    return "\n".join(lines)


def format_summary_message(date_range: str, week_summary: dict[str, Any]) -> str:
    """Build a concise deterministic summary message from weekly summary data."""
    summary = week_summary.get("summary")
    if not isinstance(summary, dict):
        fail("Missing or invalid summary object for current week")

    day_counts = summary.get("day_counts")
    best_overlap = summary.get("best_overlap")
    slot_counts = summary.get("slot_counts")

    if not isinstance(day_counts, dict):
        fail("Missing or invalid day_counts in current week summary")
    if not isinstance(best_overlap, dict):
        fail("Missing or invalid best_overlap in current week summary")
    if not isinstance(slot_counts, dict):
        fail("Missing or invalid slot_counts in current week summary")

    best_day = best_overlap.get("day")
    if not isinstance(best_day, str):
        fail("Missing or invalid best_overlap fields in current week summary")

    ranked_days = sorted(
        DAY_NAMES,
        key=lambda day_name: (-int(day_counts.get(day_name, 0)), DAY_NAMES.index(day_name)),
    )

    def format_day_slot_counts(day_name: str) -> str:
        raw_day_slot_counts = slot_counts.get(day_name)
        if not isinstance(raw_day_slot_counts, dict):
            raw_day_slot_counts = {}

        ordered_parts: list[str] = []
        for slot in SUMMARY_DISPLAY_ORDER:
            slot_count = int(raw_day_slot_counts.get(slot, 0))
            if slot_count > 0:
                ordered_parts.append(f"{slot} {slot_count}")
        if ordered_parts:
            return ", ".join(ordered_parts)
        return "No responses"

    ranked_day_lines = [
        f"{index + 1}. {day_name} — {format_day_slot_counts(day_name)}"
        for index, day_name in enumerate(ranked_days)
    ]

    lines = [
        f"📊 Availability Summary — {date_range}",
        "",
        "Best overlap:",
        f"{best_day} — {format_day_slot_counts(best_day)}",
        "",
        "All days ranked:",
        *ranked_day_lines,
    ]

    return "\n".join(lines)


def post_channel_message(session: requests.Session, channel_id: str, content: str) -> str:
    """Post a message to a Discord channel and return the new message id."""
    response = session.post(
        build_create_message_url(channel_id),
        json={"content": content},
        timeout=REQUEST_TIMEOUT_SECONDS,
    )
    check_response(response, f"Failed to post channel message for channel_id={channel_id}")

    try:
        payload: dict[str, Any] = response.json()
    except ValueError:
        fail("Discord response was not valid JSON when posting channel message")

    message_id = payload.get("id")
    if not message_id:
        fail("Discord response JSON did not include posted message id")

    return str(message_id)


def edit_channel_message(
    session: requests.Session, channel_id: str, message_id: str, content: str
) -> None:
    """Edit an existing Discord channel message in place."""
    response = session.patch(
        build_edit_message_url(channel_id, message_id),
        json={"content": content},
        timeout=REQUEST_TIMEOUT_SECONDS,
    )
    check_response(
        response,
        f"Failed to edit channel message for channel_id={channel_id}, message_id={message_id}",
    )


def main() -> None:
    """Fetch and persist reaction responses for recorded scheduled weeks."""
    token = require_env("DISCORD_SCHEDULING_BOT_TOKEN")

    weekly_messages = load_json_file(WEEKLY_SCHEDULE_MESSAGES_FILE)
    roster = load_json_file(EXPECTED_SCHEDULE_ROSTER_FILE)
    weekly_bot_outputs = load_json_file(WEEKLY_SCHEDULE_BOT_OUTPUTS_FILE)
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

            channel_id = get_week_channel_id(latest_week_data, week_key)
            date_range = latest_week_data.get("date_range")
            days = latest_week_data.get("days")

            if not date_range or not isinstance(date_range, str):
                fail(f"Missing or invalid date_range for {week_key} in {WEEKLY_SCHEDULE_MESSAGES_FILE}")
            if not isinstance(days, dict):
                fail(f"Missing or invalid days mapping for {week_key} in {WEEKLY_SCHEDULE_MESSAGES_FILE}")

            users_map: dict[str, dict[str, Any]] = {}
            day_message_ids = {str(day_id) for day_id in days.values() if day_id}
            channel_messages = fetch_channel_messages(session, channel_id)
            latest_custom_replies = collect_latest_custom_replies_by_day(
                channel_messages, day_message_ids
            )
            for day_name in DAY_NAMES:
                day_message_id = days.get(day_name)
                if not day_message_id:
                    fail(
                        f"Missing message ID for {day_name} in week {week_key} "
                        f"from {WEEKLY_SCHEDULE_MESSAGES_FILE}"
                    )

                for reaction in AVAILABILITY_REACTIONS:
                    reaction_users = fetch_reaction_users(
                        session, channel_id, str(day_message_id), reaction
                    )
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

    latest_week_key = sorted(pruned_weekly_responses.keys())[-1]
    latest_week_messages = weekly_messages.get(latest_week_key)
    latest_week_responses = pruned_weekly_responses.get(latest_week_key)
    latest_week_summary = weekly_summary.get(latest_week_key)
    if not isinstance(latest_week_messages, dict):
        fail(f"Missing week payload for {latest_week_key} in {WEEKLY_SCHEDULE_MESSAGES_FILE}")
    if not isinstance(latest_week_responses, dict):
        fail(f"Missing week payload for {latest_week_key} in {WEEKLY_SCHEDULE_RESPONSES_FILE}")
    if not isinstance(latest_week_summary, dict):
        fail(f"Missing week payload for {latest_week_key} in {WEEKLY_SCHEDULE_SUMMARY_FILE}")

    channel_id = get_week_channel_id(latest_week_messages, latest_week_key)
    date_range = latest_week_messages.get("date_range")
    if not isinstance(date_range, str) or not date_range:
        fail(f"Missing or invalid date_range for {latest_week_key}")

    missing_user_ids = compute_missing_user_ids_for_week(latest_week_responses, roster)
    week_outputs = weekly_bot_outputs.get(latest_week_key)
    if not isinstance(week_outputs, dict):
        week_outputs = {}

    with requests.Session() as posting_session:
        posting_session.headers.update(
            {
                "Authorization": f"Bot {token}",
                "Content-Type": "application/json",
                "User-Agent": USER_AGENT,
            }
        )
        discord_client = DiscordClient(posting_session)

        summary_message = format_summary_message(date_range, latest_week_summary)
        previous_summary_message_id = week_outputs.get("summary_message_id")
        summary_message_id = (
            str(previous_summary_message_id)
            if isinstance(previous_summary_message_id, str) and previous_summary_message_id
            else None
        )
        previous_summary_message_content = week_outputs.get("summary_message_content")

        if summary_message_id is None:
            payload = discord_client.post_message(
                channel_id, summary_message, context=f"post summary for {latest_week_key}"
            )
            summary_message_id = str(payload.get("id", ""))
            week_outputs["summary_posted"] = True
            week_outputs["summary_message_id"] = summary_message_id
            week_outputs["summary_message_content"] = summary_message
            print(f"CREATE: posted summary for week {latest_week_key} (message_id={summary_message_id})")
        elif previous_summary_message_content != summary_message:
            try:
                discord_client.edit_message(
                    channel_id,
                    summary_message_id,
                    summary_message,
                    context=f"edit summary for {latest_week_key}",
                )
                week_outputs["summary_posted"] = True
                week_outputs["summary_message_content"] = summary_message
                print(
                    f"EDIT: updated summary for week {latest_week_key} "
                    f"(message_id={summary_message_id})"
                )
            except DiscordMessageNotFoundError:
                print(
                    f"RECOVER: stale/deleted summary message for week {latest_week_key} "
                    f"(message_id={summary_message_id}); posting replacement"
                )
                payload = discord_client.post_message(
                    channel_id,
                    summary_message,
                    context=f"recover summary for {latest_week_key}",
                )
                summary_message_id = str(payload.get("id", ""))
                week_outputs["summary_posted"] = True
                week_outputs["summary_message_id"] = summary_message_id
                week_outputs["summary_message_content"] = summary_message
                print(
                    f"RECOVER: posted replacement summary for week {latest_week_key} "
                    f"(message_id={summary_message_id})"
                )
        else:
            print(f"SKIP: summary unchanged for week {latest_week_key}; skipping summary edit")

        previous_missing_users = week_outputs.get("reminder_missing_users")
        if not isinstance(previous_missing_users, list):
            previous_missing_users = []
        previous_missing_users = [str(user_id) for user_id in previous_missing_users]

        if missing_user_ids:
            if missing_user_ids != previous_missing_users:
                reminder_message = format_reminder_message(date_range, missing_user_ids)
                reminder_message_id = post_channel_message(
                    posting_session, channel_id, reminder_message
                )
                week_outputs["reminder_message_id"] = reminder_message_id
                week_outputs["reminder_missing_users"] = missing_user_ids
                print(
                    f"CREATE: posted reminder for week {latest_week_key} "
                    f"(message_id={reminder_message_id}, missing={len(missing_user_ids)})"
                )
            else:
                print(f"SKIP: reminder unchanged for week {latest_week_key}; skipping reminder post")
        else:
            week_outputs["reminder_missing_users"] = []
            print(f"SKIP: no missing users for week {latest_week_key}; skipping reminder post")

    weekly_bot_outputs[latest_week_key] = week_outputs
    pruned_bot_outputs = prune_weeks(weekly_bot_outputs, keep_last=12)
    save_json_file(WEEKLY_SCHEDULE_BOT_OUTPUTS_FILE, pruned_bot_outputs)
    print(
        f"Saved weekly schedule responses for {len(pruned_weekly_responses)} week(s) "
        f"to {WEEKLY_SCHEDULE_RESPONSES_FILE}"
    )
    print(
        f"Saved weekly schedule summary for {len(weekly_summary)} week(s) "
        f"to {WEEKLY_SCHEDULE_SUMMARY_FILE}"
    )
    print(
        f"Saved weekly schedule bot outputs for {len(pruned_bot_outputs)} week(s) "
        f"to {WEEKLY_SCHEDULE_BOT_OUTPUTS_FILE}"
    )
    print("Finished successfully")


if __name__ == "__main__":
    main()
