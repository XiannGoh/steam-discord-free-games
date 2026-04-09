import os
from datetime import datetime, timedelta, timezone
from typing import Dict, List, Optional
from urllib.parse import quote

import requests

from discord_api import DiscordClient, DiscordMessageNotFoundError
from state_utils import load_json_object, save_json_object_atomic

DISCORD_DAILY_POSTS_FILE = "discord_daily_posts.json"
THUMBS_UP_EMOJI = "👍"
THUMBS_UP_EMOJI_ENCODED = quote(THUMBS_UP_EMOJI, safe="")

DISCORD_BOT_TOKEN = os.getenv("DISCORD_BOT_TOKEN")
DISCORD_WINNERS_CHANNEL_ID = os.getenv("DISCORD_WINNERS_CHANNEL_ID")
DISCORD_DAILY_PICKS_CHANNEL_ID = os.getenv("DISCORD_DAILY_PICKS_CHANNEL_ID")
WINNERS_DATE_OVERRIDE_ENV = "WINNERS_DATE_UTC"
WINNERS_LOOKBACK_DAYS = 10
MAX_VOTERS_SHOWN_PER_GAME = 6
DISCORD_MESSAGE_CHAR_LIMIT = 2000

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


def get_lookback_day_keys(target_day_key: str, lookback_days: int = WINNERS_LOOKBACK_DAYS) -> List[str]:
    target_date = datetime.fromisoformat(target_day_key).date()
    return [
        (target_date - timedelta(days=offset)).isoformat()
        for offset in range(lookback_days)
    ]


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
            lines.append(f"Voters — {format_voter_names_for_message(item['voter_names'])}")
            lines.append("")

    if not has_any_winners:
        lines.append("_No votes yet today._")

    return "\n".join(lines).strip()


def build_winners_message_compact(winners_by_section: Dict[str, List[dict]]) -> str:
    lines = ["🏆 Daily Game Picks — Winners", ""]
    has_any_winners = False

    for section in SECTION_ORDER:
        items = winners_by_section.get(section, [])
        if not items:
            continue

        has_any_winners = True
        lines.append(SECTION_CONFIG[section])
        for item in items:
            vote_word = "vote" if item["human_votes"] == 1 else "votes"
            lines.append(f"- {item['title']} ({item['human_votes']} {vote_word})")
            lines.append(f"  {item['url']}")
        lines.append("")

    if not has_any_winners:
        lines.append("_No votes yet today._")

    return "\n".join(lines).strip()


def resolve_display_name(user: dict) -> str:
    if not isinstance(user, dict):
        return "Unknown User"
    global_name = (user.get("global_name") or "").strip()
    if global_name:
        return global_name
    username = (user.get("username") or "").strip()
    if username:
        return username
    user_id = str(user.get("id") or "").strip()
    if user_id:
        return f"User {user_id}"
    return "Unknown User"


def format_voter_names_for_message(names: List[str]) -> str:
    if not names:
        return "Unknown voters"
    visible = names[:MAX_VOTERS_SHOWN_PER_GAME]
    hidden_count = len(names) - len(visible)
    joined = ", ".join(visible)
    if hidden_count > 0:
        return f"{joined}, +{hidden_count} more"
    return joined


def pick_winners_channel_id(items: List[dict]) -> Optional[str]:
    return DISCORD_WINNERS_CHANNEL_ID


def fetch_human_voter_names(
    client: DiscordClient,
    *,
    channel_id: str,
    message_id: str,
    bot_user_id: Optional[str],
    context: str,
) -> List[str]:
    names: List[str] = []
    seen_user_ids: set[str] = set()
    after: Optional[str] = None

    while True:
        users = client.get_reaction_users(
            channel_id,
            message_id,
            THUMBS_UP_EMOJI_ENCODED,
            context=context,
            limit=100,
            after=after,
        )
        if not users:
            break
        for user in users:
            user_id = str(user.get("id") or "").strip()
            if not user_id or user_id in seen_user_ids:
                continue
            seen_user_ids.add(user_id)
            if bot_user_id and user_id == bot_user_id:
                continue
            names.append(resolve_display_name(user))
        if len(users) < 100:
            break
        last_user_id = str(users[-1].get("id") or "").strip()
        if not last_user_id:
            break
        after = last_user_id

    return names


def post_winners_message(client: DiscordClient, channel_id: str, message: str) -> str:
    payload = client.post_message(channel_id, message, context="post winners message")
    message_id = str(payload.get("id", ""))
    if not message_id:
        raise RuntimeError("Discord response missing winners message id")
    return message_id


def main() -> None:
    if not DISCORD_BOT_TOKEN:
        raise RuntimeError("DISCORD_BOT_TOKEN is not set.")
    day_key = get_target_day_key()
    print(f"Starting evening winners for day={day_key}")

    daily_posts = load_discord_daily_posts()
    today_entry = daily_posts.get(day_key, {})
    if not isinstance(today_entry, dict):
        today_entry = {}
        daily_posts[day_key] = today_entry
    lookback_day_keys = get_lookback_day_keys(day_key)
    items: List[dict] = []
    for bucket_key in lookback_day_keys:
        bucket = daily_posts.get(bucket_key, {})
        if not isinstance(bucket, dict):
            continue
        bucket_items = bucket.get("items", [])
        if isinstance(bucket_items, list):
            items.extend(bucket_items)

    winners_by_section: Dict[str, List[dict]] = {key: [] for key in SECTION_ORDER}
    winners_channel_id = pick_winners_channel_id(items)
    if not winners_channel_id:
        raise RuntimeError(
            "Winners destination channel id is not set. "
            "Set DISCORD_WINNERS_CHANNEL_ID."
        )

    with requests.Session() as session:
        session.headers.update(
            {
                "Authorization": f"Bot {DISCORD_BOT_TOKEN}",
                "Content-Type": "application/json",
            }
        )
        client = DiscordClient(session)
        bot_user = client.get_current_user(context="fetch bot user for winners vote filtering")
        bot_user_id = str(bot_user.get("id") or "").strip() or None

        deduped_winners: Dict[str, dict] = {}
        for item in items:
            section = item.get("section")
            channel_id = item.get("channel_id")
            message_id = item.get("message_id")
            dedupe_key = str(item.get("url") or "").strip()
            if not dedupe_key:
                dedupe_key = str(item.get("item_key") or "").strip()
            if not dedupe_key:
                dedupe_key = f"{channel_id}:{message_id}"

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
            voter_names = fetch_human_voter_names(
                client,
                channel_id=str(channel_id),
                message_id=str(message_id),
                bot_user_id=bot_user_id,
                context=f"fetch voters for {item.get('title', 'item')}",
            )

            existing = deduped_winners.get(dedupe_key)
            candidate = {
                "section": section,
                "title": item.get("title", "Untitled"),
                "url": item.get("url", ""),
                "human_votes": human_votes,
                "voter_names": voter_names,
            }
            if existing is None or candidate["human_votes"] > existing["human_votes"]:
                deduped_winners[dedupe_key] = candidate

        for winner in deduped_winners.values():
            section = winner["section"]
            winners_by_section[section].append(
                {
                    "title": winner["title"],
                    "url": winner["url"],
                    "human_votes": winner["human_votes"],
                    "voter_names": winner["voter_names"],
                }
            )

        message = build_winners_message(winners_by_section)
        if len(message) > DISCORD_MESSAGE_CHAR_LIMIT:
            message = build_winners_message_compact(winners_by_section)
        winners_state = today_entry.get("winners_state")
        if not isinstance(winners_state, dict):
            winners_state = {}
            today_entry["winners_state"] = winners_state

        current_winner_keys = sorted(deduped_winners.keys())
        previous_message_id = winners_state.get("message_id")
        previous_winner_keys = winners_state.get("winner_keys")
        if not isinstance(previous_winner_keys, list):
            previous_winner_keys = []

        if not current_winner_keys and not (isinstance(previous_message_id, str) and previous_message_id):
            print(f"SKIP: no eligible winners in last {WINNERS_LOOKBACK_DAYS} days for {day_key}")
            return

        if sorted(str(key) for key in previous_winner_keys) == current_winner_keys:
            print(f"SKIP: no newly eligible winners for {day_key}")
            return

        if isinstance(previous_message_id, str) and previous_message_id:
            try:
                client.get_message(
                    winners_channel_id,
                    previous_message_id,
                    context=f"verify winners message for {day_key}",
                )
                client.edit_message(
                    winners_channel_id,
                    previous_message_id,
                    message,
                    context=f"edit winners message for {day_key}",
                )
                winners_state["winner_keys"] = current_winner_keys
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

        new_message_id = post_winners_message(client, winners_channel_id, message)
        winners_state["message_id"] = new_message_id
        winners_state["winner_keys"] = current_winner_keys
        winners_state["last_action"] = "create"
        winners_state["posted_at_utc"] = datetime.now(timezone.utc).isoformat()
        save_discord_daily_posts(daily_posts)
        print(f"CREATE: posted winners message for {day_key} (message_id={new_message_id})")


if __name__ == "__main__":
    main()
