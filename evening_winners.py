import hashlib
import os
from datetime import datetime, timezone
from typing import Dict, List, Optional

import requests

from discord_api import DiscordClient, DiscordMessageNotFoundError
from state_utils import load_json_object, save_json_object_atomic

DISCORD_DAILY_POSTS_FILE = "discord_daily_posts.json"
THUMBS_UP_EMOJI = "👍"

DISCORD_BOT_TOKEN = os.getenv("DISCORD_BOT_TOKEN")
DISCORD_WINNERS_CHANNEL_ID = os.getenv("DISCORD_WINNERS_CHANNEL_ID")
WINNERS_DATE_OVERRIDE_ENV = "WINNERS_DATE_UTC"

SECTION_CONFIG = {
    "free": "Free Picks",
    "paid": "Paid Under $20",
    "instagram": "Instagram Creator Picks",
}
SECTION_ORDER = ["free", "paid", "instagram"]


def load_discord_daily_posts() -> Dict[str, dict]:
    return load_json_object(DISCORD_DAILY_POSTS_FILE, log=print)


def save_discord_daily_posts(data: Dict[str, dict]) -> None:
    save_json_object_atomic(DISCORD_DAILY_POSTS_FILE, data)


def get_target_day_key() -> str:
    manual_day = (os.getenv(WINNERS_DATE_OVERRIDE_ENV, "") or "").strip()
    if not manual_day:
        return datetime.now(timezone.utc).date().isoformat()
    datetime.fromisoformat(manual_day)
    return manual_day


def get_thumbsup_count(message_payload: dict) -> int:
    for reaction in message_payload.get("reactions", []):
        emoji = reaction.get("emoji", {})
        if emoji.get("name") == THUMBS_UP_EMOJI:
            return int(reaction.get("count", 0))
    return 0


def build_winners_message(winners_by_section: Dict[str, List[dict]]) -> str:
    lines = ["🏆 Daily Game Picks — Winners", ""]
    has_any_winners = False

    for section in SECTION_ORDER:
        items = winners_by_section.get(section, [])
        if not items:
            continue

        has_any_winners = True
        lines.append(SECTION_CONFIG[section])
        for item in items:
            lines.append(item["title"])
            lines.append(item["url"])
            vote_word = "vote" if item["human_votes"] == 1 else "votes"
            lines.append(f"👍 {item['human_votes']} {vote_word}")
            lines.append("")

    if not has_any_winners:
        lines.append("_No votes yet today._")

    return "\n".join(lines).strip()


def post_winners_message(client: DiscordClient, channel_id: str, message: str) -> str:
    payload = client.post_message(channel_id, message, context="post winners message")
    message_id = str(payload.get("id", ""))
    if not message_id:
        raise RuntimeError("Discord response missing winners message id")
    return message_id


def main() -> None:
    if not DISCORD_BOT_TOKEN:
        raise RuntimeError("DISCORD_BOT_TOKEN is not set.")
    if not DISCORD_WINNERS_CHANNEL_ID:
        raise RuntimeError("DISCORD_WINNERS_CHANNEL_ID is not set.")

    day_key = get_target_day_key()
    print(f"Starting evening winners for day={day_key}")

    daily_posts = load_discord_daily_posts()
    today_entry = daily_posts.get(day_key, {})
    if not isinstance(today_entry, dict):
        today_entry = {}
        daily_posts[day_key] = today_entry
    items = today_entry.get("items", []) if isinstance(today_entry, dict) else []

    winners_by_section: Dict[str, List[dict]] = {key: [] for key in SECTION_ORDER}

    with requests.Session() as session:
        session.headers.update(
            {
                "Authorization": f"Bot {DISCORD_BOT_TOKEN}",
                "Content-Type": "application/json",
            }
        )
        client = DiscordClient(session)

        for item in items:
            section = item.get("section")
            channel_id = item.get("channel_id")
            message_id = item.get("message_id")

            if section not in SECTION_CONFIG:
                continue
            if not channel_id or not message_id:
                continue

            try:
                message_payload = client.get_message(
                    channel_id=str(channel_id),
                    message_id=str(message_id),
                    context=f"fetch votes for {item.get('title', 'item')}",
                )
            except DiscordMessageNotFoundError:
                print(f"RECOVER: stale/deleted daily item message skipped (message_id={message_id})")
                continue

            raw_thumbsup_count = get_thumbsup_count(message_payload)
            human_votes = raw_thumbsup_count - 1

            if human_votes < 1:
                continue

            winners_by_section[section].append(
                {
                    "title": item.get("title", "Untitled"),
                    "url": item.get("url", ""),
                    "human_votes": human_votes,
                }
            )

        message = build_winners_message(winners_by_section)
        winners_state = today_entry.get("winners_state")
        if not isinstance(winners_state, dict):
            winners_state = {}
            today_entry["winners_state"] = winners_state

        content_hash = hashlib.sha256(message.encode("utf-8")).hexdigest()
        previous_message_id = winners_state.get("message_id")
        previous_content_hash = winners_state.get("content_hash")

        if isinstance(previous_message_id, str) and previous_message_id:
            try:
                client.get_message(
                    DISCORD_WINNERS_CHANNEL_ID,
                    previous_message_id,
                    context=f"verify winners message for {day_key}",
                )
                if previous_content_hash == content_hash:
                    print(f"SKIP: winners already posted and unchanged for {day_key}")
                    return
                client.edit_message(
                    DISCORD_WINNERS_CHANNEL_ID,
                    previous_message_id,
                    message,
                    context=f"edit winners message for {day_key}",
                )
                winners_state["content_hash"] = content_hash
                winners_state["last_action"] = "edit"
                winners_state["updated_at_utc"] = datetime.now(timezone.utc).isoformat()
                save_discord_daily_posts(daily_posts)
                print(f"EDIT: updated winners message for {day_key} (message_id={previous_message_id})")
                return
            except DiscordMessageNotFoundError:
                print(
                    f"RECOVER: stale/deleted winners message for {day_key} "
                    f"(message_id={previous_message_id}); posting replacement"
                )

        new_message_id = post_winners_message(client, DISCORD_WINNERS_CHANNEL_ID, message)
        winners_state["message_id"] = new_message_id
        winners_state["content_hash"] = content_hash
        winners_state["last_action"] = "create"
        winners_state["posted_at_utc"] = datetime.now(timezone.utc).isoformat()
        save_discord_daily_posts(daily_posts)
        print(f"CREATE: posted winners message for {day_key} (message_id={new_message_id})")


if __name__ == "__main__":
    main()
