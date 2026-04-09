"""Sync weekly scheduling reactions from Discord into durable JSON state."""

import json
import hashlib
import os
import sys
import time
import urllib.parse
from datetime import date, datetime, timedelta, timezone
from typing import Any
from zoneinfo import ZoneInfo

import requests

from discord_api import DiscordClient, DiscordMessageNotFoundError
from scripts.scheduling_labels import DAY_NAMES, format_day_label
from state_utils import load_json_object, prune_latest_keys, save_json_object_atomic

DISCORD_API_BASE = "https://discord.com/api/v10"
REQUEST_TIMEOUT_SECONDS = 30
USER_AGENT = "steam-discord-free-games/weekly-scheduling-bot"
WEEKLY_SCHEDULE_MESSAGES_FILE = "data/scheduling/weekly_schedule_messages.json"
WEEKLY_SCHEDULE_RESPONSES_FILE = "data/scheduling/weekly_schedule_responses.json"
WEEKLY_SCHEDULE_SUMMARY_FILE = "data/scheduling/weekly_schedule_summary.json"
EXPECTED_SCHEDULE_ROSTER_FILE = "data/scheduling/expected_schedule_roster.json"
WEEKLY_SCHEDULE_BOT_OUTPUTS_FILE = "data/scheduling/weekly_schedule_bot_outputs.json"

AVAILABILITY_REACTIONS: list[str] = ["✅", "🌅", "☀️", "🌙", "❌", "📝"]
SUMMARY_SLOT_ORDER: list[str] = ["✅", "🌅", "☀️", "🌙", "📝"]
SUMMARY_DISPLAY_ORDER: list[str] = ["✅", "🌅", "☀️", "🌙", "📝", "❌"]
MAX_SUMMARY_LINE_LENGTH = 185
NEW_YORK_TIMEZONE = ZoneInfo("America/New_York")


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




def env_flag(name: str, default: bool = False) -> bool:
    """Read a boolean environment variable with common truthy values."""
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


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
    """Build derived weekly summary with day/slot overlap and voter details."""
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
        slot_voters: dict[str, dict[str, list[dict[str, str]]]] = {
            day: {slot: [] for slot in SUMMARY_SLOT_ORDER} for day in DAY_NAMES
        }

        for user_id, user_data in users.items():
            if not isinstance(user_data, dict):
                continue

            display_name = normalize_optional_text(user_data.get("global_name")) or normalize_optional_text(
                user_data.get("username")
            )
            if display_name is None:
                display_name = f"User {user_id}"

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
                        slot_voters[day_name][slot].append(
                            {
                                "user_id": str(user_id),
                                "display_name": display_name,
                            }
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
            "week_key": week_key,
            "date_range": week_data.get("date_range"),
            "summary": {
                "day_counts": day_counts,
                "slot_counts": slot_counts,
                "slot_voters": slot_voters,
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


def parse_week_start_date(week_key: str) -> date | None:
    """Parse a week key like YYYY-MM-DD_to_YYYY-MM-DD."""
    try:
        start_text, _, _ = week_key.partition("_to_")
        return datetime.strptime(start_text, "%Y-%m-%d").date()
    except ValueError:
        return None


def parse_start_date_from_date_range(date_range: str) -> date | None:
    """Parse date range text like 'Apr 13–19, 2026' and return its start date."""
    cleaned = date_range.replace("—", "–")
    try:
        month_day, _, year_text = cleaned.partition(",")
        month_text, _, day_range = month_day.strip().partition(" ")
        start_day_text, _, _ = day_range.partition("–")
        if not month_text or not start_day_text or not year_text.strip():
            return None
        return datetime.strptime(
            f"{month_text} {start_day_text.strip()} {year_text.strip()}",
            "%b %d %Y",
        ).date()
    except ValueError:
        return None


def compute_week_dates_from_summary(week_summary: dict[str, Any]) -> dict[str, date]:
    """Map weekday names to concrete dates using week key or date range context."""
    week_key = week_summary.get("week_key")
    if isinstance(week_key, str):
        start = parse_week_start_date(week_key)
        if start is not None:
            return {
                day_name: start + timedelta(days=index)
                for index, day_name in enumerate(DAY_NAMES)
            }

    date_range = week_summary.get("date_range")
    if isinstance(date_range, str):
        maybe_start = parse_start_date_from_date_range(date_range)
        if maybe_start is not None:
            return {
                day_name: maybe_start + timedelta(days=index)
                for index, day_name in enumerate(DAY_NAMES)
            }

    return {}


def count_active_roster_users(roster: dict[str, Any]) -> int:
    """Return the number of active users from the expected roster."""
    users = roster.get("users")
    if not isinstance(users, dict):
        fail(f"Missing or invalid users object in {EXPECTED_SCHEDULE_ROSTER_FILE}")
    return sum(
        1
        for user in users.values()
        if isinstance(user, dict) and user.get("is_active") is True
    )


def format_summary_last_updated_line(synced_at_utc: datetime) -> str:
    """Format a compact New York last-updated line for summary output."""
    ny_time = synced_at_utc.astimezone(NEW_YORK_TIMEZONE)
    month_day = f"{ny_time.strftime('%b')} {ny_time.day}"
    hour_12 = ((ny_time.hour - 1) % 12) + 1
    minute = f"{ny_time.minute:02d}"
    am_pm = ny_time.strftime("%p")
    return f"*Last updated: {month_day}, {hour_12}:{minute} {am_pm} ET*"


def normalize_summary_content_for_data_compare(content: Any) -> str | None:
    """Normalize summary content for legacy data comparison.

    Drops the volatile "Last updated" line so we can compare meaningful
    summary content across pre-signature and post-signature runs.
    """
    if not isinstance(content, str):
        return None

    normalized_lines = [
        line
        for line in content.splitlines()
        if not line.strip().startswith("*Last updated:")
    ]
    return "\n".join(normalized_lines).strip()


def format_summary_message(
    date_range: str,
    week_summary: dict[str, Any],
    *,
    responded_count: int,
    active_user_count: int,
    synced_at_utc: datetime,
) -> str:
    """Build a richer deterministic summary message from weekly summary data."""
    summary = week_summary.get("summary")
    if not isinstance(summary, dict):
        fail("Missing or invalid summary object for current week")

    day_counts = summary.get("day_counts")
    slot_counts = summary.get("slot_counts")
    slot_voters = summary.get("slot_voters")

    if not isinstance(day_counts, dict):
        fail("Missing or invalid day_counts in current week summary")
    if not isinstance(slot_counts, dict):
        fail("Missing or invalid slot_counts in current week summary")
    if not isinstance(slot_voters, dict):
        slot_voters = {}

    week_dates = compute_week_dates_from_summary(week_summary)

    def day_with_date_label(day_name: str) -> str:
        day_date = week_dates.get(day_name)
        if day_date is None:
            return day_name
        return format_day_label(day_name, day_date)

    def format_voter_line(day_name: str, slot: str, slot_count: int) -> str:
        raw_day_slot_voters = slot_voters.get(day_name)
        raw_voters = []
        if isinstance(raw_day_slot_voters, dict):
            raw_voters = raw_day_slot_voters.get(slot, [])

        voter_names: list[str] = []
        seen_names: set[str] = set()
        if isinstance(raw_voters, list):
            for raw_voter in raw_voters:
                if not isinstance(raw_voter, dict):
                    continue
                display_name = normalize_optional_text(raw_voter.get("display_name"))
                if display_name is None or display_name in seen_names:
                    continue
                seen_names.add(display_name)
                voter_names.append(display_name)

        base_prefix = f"{slot} {slot_count} — "
        if not voter_names:
            return f"{base_prefix}(names unavailable)"

        full_line = f"{base_prefix}{', '.join(voter_names)}"
        if len(full_line) <= MAX_SUMMARY_LINE_LENGTH:
            return full_line

        kept_names: list[str] = []
        for index, name in enumerate(voter_names):
            remaining_after = len(voter_names) - (index + 1)
            candidate_names = ", ".join([*kept_names, name])
            if remaining_after > 0:
                candidate_line = f"{base_prefix}{candidate_names}, +{remaining_after} more"
            else:
                candidate_line = f"{base_prefix}{candidate_names}"
            if len(candidate_line) <= MAX_SUMMARY_LINE_LENGTH:
                kept_names.append(name)
            else:
                break

        if not kept_names:
            return f"{base_prefix}+{len(voter_names)} more"

        remaining = len(voter_names) - len(kept_names)
        if remaining > 0:
            return f"{base_prefix}{', '.join(kept_names)}, +{remaining} more"
        return f"{base_prefix}{', '.join(kept_names)}"

    def build_day_slot_lines(day_name: str) -> list[str]:
        raw_day_slot_counts = slot_counts.get(day_name)
        if not isinstance(raw_day_slot_counts, dict):
            raw_day_slot_counts = {}

        ordered_lines: list[str] = []
        for slot in SUMMARY_DISPLAY_ORDER:
            slot_count = int(raw_day_slot_counts.get(slot, 0))
            if slot_count > 0:
                ordered_lines.append(format_voter_line(day_name, slot, slot_count))
        return ordered_lines

    chronological_day_lines: list[str] = []
    for index, day_name in enumerate(DAY_NAMES):
        chronological_day_lines.append(f"**{day_with_date_label(day_name)}**")
        day_slot_lines = build_day_slot_lines(day_name)
        if day_slot_lines:
            chronological_day_lines.extend(day_slot_lines)
        else:
            chronological_day_lines.append("No responses")
        if index < len(DAY_NAMES) - 1:
            chronological_day_lines.append("")

    missing_count = max(active_user_count - responded_count, 0)
    response_status_line = (
        f"*{responded_count} of {active_user_count} people responded • "
        f"{missing_count} still missing*"
    )
    last_updated_line = format_summary_last_updated_line(synced_at_utc)
    lines = [
        f"📊 Availability Summary — {date_range}",
        "",
        response_status_line,
        last_updated_line,
        "",
        *chronological_day_lines,
    ]

    message = "\n".join(lines)
    if len(message) <= 2000:
        return message

    fallback_lines = [
        f"📊 Availability Summary — {date_range}",
        "",
        response_status_line,
        last_updated_line,
        "",
        *(f"**{day_with_date_label(day_name)}**" for day_name in DAY_NAMES),
        "",
        "_Detailed voter names were truncated to fit Discord message limits._",
    ]
    return "\n".join(fallback_lines)


def compute_summary_data_signature(
    week_summary: dict[str, Any],
    responded_count: int,
    active_user_count: int,
    missing_user_ids: list[str],
) -> str:
    """Hash meaningful summary inputs to detect real summary-data changes."""
    signature_payload = {
        "week_summary": week_summary,
        "responded_count": responded_count,
        "active_user_count": active_user_count,
        "missing_user_ids": missing_user_ids,
    }
    stable_payload = json.dumps(signature_payload, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(stable_payload.encode("utf-8")).hexdigest()


def parse_iso_utc_datetime(value: Any) -> datetime | None:
    """Parse an ISO timestamp into an aware UTC datetime."""
    if not isinstance(value, str) or not value.strip():
        return None

    normalized = value.strip()
    if normalized.endswith("Z"):
        normalized = f"{normalized[:-1]}+00:00"

    try:
        parsed = datetime.fromisoformat(normalized)
    except ValueError:
        return None

    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


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


def current_new_york_local_date() -> str:
    """Return the current New York calendar date in ISO format."""
    return datetime.now(NEW_YORK_TIMEZONE).date().isoformat()


def main() -> None:
    """Fetch and persist reaction responses for recorded scheduled weeks."""
    token = require_env("DISCORD_SCHEDULING_BOT_TOKEN")

    weekly_messages = load_json_file(WEEKLY_SCHEDULE_MESSAGES_FILE)
    existing_weekly_responses = load_json_file(WEEKLY_SCHEDULE_RESPONSES_FILE)
    roster = load_json_file(EXPECTED_SCHEDULE_ROSTER_FILE)
    weekly_bot_outputs = load_json_file(WEEKLY_SCHEDULE_BOT_OUTPUTS_FILE)
    if not weekly_messages:
        print("No weekly message state found; nothing to sync")
        return

    target_week_key = normalize_optional_text(os.getenv("TARGET_WEEK_KEY"))
    rebuild_summary_only = env_flag("REBUILD_SUMMARY_ONLY", default=False)
    dry_run = env_flag("DRY_RUN", default=False)

    if target_week_key:
        if target_week_key not in weekly_messages:
            fail(
                f"TARGET_WEEK_KEY={target_week_key} not found in {WEEKLY_SCHEDULE_MESSAGES_FILE}"
            )
        week_keys = [target_week_key]
    else:
        week_keys = sorted(weekly_messages.keys())[-12:]

    weekly_responses: dict[str, Any] = {}
    for week_key, week_payload in existing_weekly_responses.items():
        if isinstance(week_payload, dict):
            weekly_responses[week_key] = week_payload

    if rebuild_summary_only:
        print(
            f"Rebuild-only mode enabled; skipping reaction fetch for {len(week_keys)} "
            f"week(s): {', '.join(week_keys)}"
        )
    else:
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

            for week_key in week_keys:
                latest_week_data = weekly_messages.get(week_key)
                if not isinstance(latest_week_data, dict):
                    fail(f"Invalid week payload for {week_key} in {WEEKLY_SCHEDULE_MESSAGES_FILE}")

                channel_id = get_week_channel_id(latest_week_data, week_key)
                date_range = latest_week_data.get("date_range")
                days = latest_week_data.get("days")

                if not date_range or not isinstance(date_range, str):
                    fail(
                        f"Missing or invalid date_range for {week_key} "
                        f"in {WEEKLY_SCHEDULE_MESSAGES_FILE}"
                    )
                if not isinstance(days, dict):
                    fail(
                        f"Missing or invalid days mapping for {week_key} "
                        f"in {WEEKLY_SCHEDULE_MESSAGES_FILE}"
                    )

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
                                custom_reply = latest_custom_replies.get(str(day_message_id), {}).get(
                                    user_id
                                )
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

    posting_week_key = target_week_key or sorted(pruned_weekly_responses.keys())[-1]
    posting_week_messages = weekly_messages.get(posting_week_key)
    posting_week_responses = pruned_weekly_responses.get(posting_week_key)
    posting_week_summary = weekly_summary.get(posting_week_key)
    if not isinstance(posting_week_messages, dict):
        fail(f"Missing week payload for {posting_week_key} in {WEEKLY_SCHEDULE_MESSAGES_FILE}")
    if not isinstance(posting_week_responses, dict):
        fail(f"Missing week payload for {posting_week_key} in {WEEKLY_SCHEDULE_RESPONSES_FILE}")
    if not isinstance(posting_week_summary, dict):
        fail(f"Missing week payload for {posting_week_key} in {WEEKLY_SCHEDULE_SUMMARY_FILE}")

    channel_id = get_week_channel_id(posting_week_messages, posting_week_key)
    date_range = posting_week_messages.get("date_range")
    if not isinstance(date_range, str) or not date_range:
        fail(f"Missing or invalid date_range for {posting_week_key}")

    missing_user_ids = compute_missing_user_ids_for_week(posting_week_responses, roster)
    active_user_count = count_active_roster_users(roster)
    # "Responded" means active roster users minus users still missing per reminder logic.
    responded_active_user_count = max(active_user_count - len(missing_user_ids), 0)
    summary_synced_at_utc = datetime.now(timezone.utc)
    week_outputs = weekly_bot_outputs.get(posting_week_key)
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

        previous_summary_message_id = week_outputs.get("summary_message_id")
        summary_message_id = (
            str(previous_summary_message_id)
            if isinstance(previous_summary_message_id, str) and previous_summary_message_id
            else None
        )
        previous_summary_message_content = week_outputs.get("summary_message_content")
        previous_summary_data_signature = normalize_optional_text(
            week_outputs.get("summary_data_signature")
        )
        previous_summary_last_synced_at_utc = parse_iso_utc_datetime(
            week_outputs.get("summary_last_synced_at_utc")
        )
        current_summary_data_signature = compute_summary_data_signature(
            posting_week_summary,
            responded_count=responded_active_user_count,
            active_user_count=active_user_count,
            missing_user_ids=missing_user_ids,
        )
        is_legacy_summary_without_signature = (
            previous_summary_data_signature is None
            and isinstance(previous_summary_message_content, str)
            and bool(previous_summary_message_content)
        )
        if previous_summary_data_signature is not None:
            summary_data_changed = previous_summary_data_signature != current_summary_data_signature
        elif is_legacy_summary_without_signature:
            # Legacy rows may have pre-signature content; compare normalized content
            # before backfilling a signature so changed summaries still trigger edits.
            regenerated_legacy_comparable_message = format_summary_message(
                date_range,
                posting_week_summary,
                responded_count=responded_active_user_count,
                active_user_count=active_user_count,
                synced_at_utc=summary_synced_at_utc,
            )
            previous_legacy_normalized_content = normalize_summary_content_for_data_compare(
                previous_summary_message_content
            )
            regenerated_legacy_normalized_content = normalize_summary_content_for_data_compare(
                regenerated_legacy_comparable_message
            )
            summary_data_changed = (
                previous_legacy_normalized_content
                != regenerated_legacy_normalized_content
            )
        else:
            summary_data_changed = True

        if summary_data_changed:
            effective_summary_synced_at_utc = summary_synced_at_utc
            week_outputs["summary_last_synced_at_utc"] = (
                effective_summary_synced_at_utc.isoformat()
            )
        else:
            effective_summary_synced_at_utc = previous_summary_last_synced_at_utc

        if (
            not summary_data_changed
            and isinstance(previous_summary_message_content, str)
            and previous_summary_message_content
        ):
            summary_message = previous_summary_message_content
        else:
            if effective_summary_synced_at_utc is None:
                effective_summary_synced_at_utc = summary_synced_at_utc
            summary_message = format_summary_message(
                date_range,
                posting_week_summary,
                responded_count=responded_active_user_count,
                active_user_count=active_user_count,
                synced_at_utc=effective_summary_synced_at_utc,
            )

        week_outputs["summary_data_signature"] = current_summary_data_signature

        if dry_run:
            print(f"DRY_RUN: summary preview for {posting_week_key}")
            print(summary_message)
            print(f"DRY_RUN: skipping Discord summary and reminder mutations for {posting_week_key}")
        elif summary_message_id is None:
            payload = discord_client.post_message(
                channel_id, summary_message, context=f"post summary for {posting_week_key}"
            )
            summary_message_id = str(payload.get("id", ""))
            week_outputs["summary_posted"] = True
            week_outputs["summary_message_id"] = summary_message_id
            week_outputs["summary_message_content"] = summary_message
            print(f"CREATE: posted summary for week {posting_week_key} (message_id={summary_message_id})")
        elif previous_summary_message_content != summary_message:
            try:
                discord_client.edit_message(
                    channel_id,
                    summary_message_id,
                    summary_message,
                    context=f"edit summary for {posting_week_key}",
                )
                week_outputs["summary_posted"] = True
                week_outputs["summary_message_content"] = summary_message
                print(
                    f"EDIT: updated summary for week {posting_week_key} "
                    f"(message_id={summary_message_id})"
                )
            except DiscordMessageNotFoundError:
                print(
                    f"RECOVER: stale/deleted summary message for week {posting_week_key} "
                    f"(message_id={summary_message_id}); posting replacement"
                )
                payload = discord_client.post_message(
                    channel_id,
                    summary_message,
                    context=f"recover summary for {posting_week_key}",
                )
                summary_message_id = str(payload.get("id", ""))
                week_outputs["summary_posted"] = True
                week_outputs["summary_message_id"] = summary_message_id
                week_outputs["summary_message_content"] = summary_message
                print(
                    f"RECOVER: posted replacement summary for week {posting_week_key} "
                    f"(message_id={summary_message_id})"
                )
        else:
            print(f"SKIP: summary unchanged for week {posting_week_key}; skipping summary edit")

        if not dry_run:
            previous_missing_users = week_outputs.get("reminder_missing_users")
            if not isinstance(previous_missing_users, list):
                previous_missing_users = []
            previous_missing_users = [str(user_id) for user_id in previous_missing_users]
            last_reminder_local_date = normalize_optional_text(
                week_outputs.get("last_reminder_local_date")
            )
            reminder_local_date = current_new_york_local_date()

            if missing_user_ids:
                if missing_user_ids != previous_missing_users:
                    if last_reminder_local_date == reminder_local_date:
                        print(
                            f"SKIP: reminder already posted on New York date "
                            f"{reminder_local_date} for week {posting_week_key}; skipping reminder post"
                        )
                    else:
                        reminder_message = format_reminder_message(date_range, missing_user_ids)
                        reminder_message_id = post_channel_message(
                            posting_session, channel_id, reminder_message
                        )
                        week_outputs["reminder_message_id"] = reminder_message_id
                        week_outputs["reminder_missing_users"] = missing_user_ids
                        week_outputs["last_reminder_local_date"] = reminder_local_date
                        print(
                            f"CREATE: posted reminder for week {posting_week_key} "
                            f"(message_id={reminder_message_id}, missing={len(missing_user_ids)}, "
                            f"ny_date={reminder_local_date})"
                        )
                else:
                    print(
                        f"SKIP: reminder unchanged for week {posting_week_key}; "
                        "skipping reminder post"
                    )
            else:
                week_outputs["reminder_missing_users"] = []
                print(f"SKIP: no missing users for week {posting_week_key}; skipping reminder post")

    weekly_bot_outputs[posting_week_key] = week_outputs
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
