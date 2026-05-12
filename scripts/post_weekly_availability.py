"""Post weekly availability prompts to a Discord scheduling channel and add reactions."""

import os
import sys
import urllib.parse
from datetime import date, datetime, timedelta, timezone
from typing import Any, Optional

import requests

from discord_api import DiscordClient, DiscordMessageNotFoundError
from rolling_explainer import post_or_edit_rolling_explainer
from scripts.scheduling_labels import DAY_MESSAGE_TEMPLATES, format_day_label
from state_utils import load_json_object, prune_latest_keys, save_json_object_atomic

USER_AGENT = "steam-discord-free-games/weekly-scheduling-bot"
WEEKLY_SCHEDULE_MESSAGES_FILE = "data/scheduling/weekly_schedule_messages.json"
DISCORD_HEALTH_MONITOR_WEBHOOK_URL = os.getenv("DISCORD_HEALTH_MONITOR_WEBHOOK_URL", "")

INTRO_MESSAGE_TEMPLATE = """🗓️ Weekly Availability — react below for next week
Week of {date_range}

React on each day message with your availability.
Use 📝 only if you want to reply with a custom time.

Availability:

✅ Free all day (Giga Chad)
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


def _notify_health_monitor(message: str) -> None:
    """Post a warning to the Discord health monitor webhook (best-effort, never raises)."""
    url = DISCORD_HEALTH_MONITOR_WEBHOOK_URL
    if not url:
        return
    try:
        requests.post(url, json={"content": message}, timeout=10)
    except Exception:
        pass


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


def find_recent_intro_in_channel(
    client: DiscordClient,
    channel_id: str,
    date_range: str,
    *,
    scan_limit: int = 50,
) -> Optional[str]:
    """Scan recent channel messages for an existing intro matching this week.

    The state file (`weekly_schedule_messages.json`) is the primary source of
    truth for whether this week was already posted. But if two scheduled runs
    fire in parallel (the concurrency group is best-effort), both can read
    the state file BEFORE either has committed, and both will CREATE
    duplicate intro+day messages (this happened the week of May 2 2026).
    This helper provides a second-line idempotency check by scanning the
    channel for a recent message containing the unique "Week of {date_range}"
    marker. If found, the caller should treat it as the canonical intro and
    skip the CREATE step.

    Returns the message id of the matching intro, or None if no match.
    """
    marker = f"Week of {date_range}"
    try:
        recent = client.get_channel_messages(
            channel_id,
            context=f"scan-for-existing-intro {date_range}",
            limit=scan_limit,
        )
    except Exception as error:
        print(f"WARN: channel scan for existing intro failed: {error}")
        return None
    for msg in recent:
        content = str(msg.get("content") or "")
        if marker in content and "Weekly Availability" in content:
            return str(msg.get("id") or "")
    return None


def find_recent_day_messages_in_channel(
    client: DiscordClient,
    channel_id: str,
    intro_message_id: str,
    week_start: date,
    *,
    scan_limit: int = 100,
) -> dict[str, str]:
    """Scan messages posted AFTER the intro for day-message matches.

    Companion to `find_recent_intro_in_channel`. When a parallel run has
    already posted intro+days for this week, but our checkout's state file
    is stale, we need to recover both the intro id AND each day-message id
    to avoid creating duplicate day posts (Codex review on PR #309 caught
    that adopting only the intro is not enough — the day-message loop would
    still post fresh messages for every weekday).

    Each day message has a unique format like "🇲 Monday — 5/4" generated by
    format_day_label. We match on `day_name` + the formatted m/d suffix.

    Returns a dict mapping day_name -> message_id for each day found.
    Days not found in the scan are simply absent from the dict; the caller
    will then CREATE those (preserving original behaviour for the no-race
    case where nothing matches).
    """
    found: dict[str, str] = {}
    try:
        recent = client.get_channel_messages(
            channel_id,
            context=f"scan-for-existing-days after={intro_message_id}",
            limit=scan_limit,
            after=intro_message_id,
        )
    except Exception as error:
        print(f"WARN: channel scan for existing day messages failed: {error}")
        return found
    # Build expected day-name -> suffix map from DAY_MESSAGE_TEMPLATES
    expected: dict[str, str] = {}
    for day_name, _emoji, day_offset in DAY_MESSAGE_TEMPLATES:
        day_date = week_start + timedelta(days=day_offset)
        # Use the same format the post-time code uses, then extract the
        # date-suffix substring as a stable disambiguator.
        full_label = format_day_label(day_name, day_date, include_emoji=True)
        expected[day_name] = full_label
    for msg in recent:
        content = str(msg.get("content") or "")
        for day_name, label in expected.items():
            if day_name in found:
                continue
            if label in content:
                found[day_name] = str(msg.get("id") or "")
                break
    return found


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
        # Pre-CREATE channel scan: if the state file does not have an intro
        # message id, but the channel already has one matching this week
        # (e.g. a parallel run beat us to the post), adopt it instead of
        # creating a duplicate. This narrows the parallel-run race window
        # from ~90s (state-file-commit time) to ~2s (channel POST round-
        # trip). Combined with the workflow concurrency group, this makes
        # duplicate posts very unlikely in practice.
        if not (isinstance(intro_message_id, str) and intro_message_id):
            scanned_intro_id = find_recent_intro_in_channel(client, channel_id, date_range)
            if scanned_intro_id:
                intro_message_id = scanned_intro_id
                print(f"ADOPT: intro found via channel scan for {week_key} (message_id={intro_message_id})")
                # Also scan for existing day messages so we do not duplicate
                # the per-day voting posts (Codex review on PR #309).
                scanned_days = find_recent_day_messages_in_channel(
                    client, channel_id, scanned_intro_id, week_start
                )
                if scanned_days:
                    existing_week_state = dict(existing_week_state)
                    existing_days_carry = existing_week_state.get("days") or {}
                    if not isinstance(existing_days_carry, dict):
                        existing_days_carry = {}
                    merged = dict(existing_days_carry)
                    merged.update(scanned_days)
                    existing_week_state["days"] = merged
                    print(
                        f"ADOPT: {len(scanned_days)} day message(s) found via channel "
                        f"scan for {week_key}: {sorted(scanned_days.keys())}"
                    )
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

        post_or_edit_rolling_explainer(client, channel_id, "weekly-scheduling")

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
