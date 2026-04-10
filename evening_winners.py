import os
import re
from datetime import datetime, timedelta, timezone
from typing import Dict, List, Optional
from urllib.parse import quote

import requests

from daily_section_config import DAILY_SECTION_DISPLAY_LABELS, DAILY_SECTION_ORDER
from discord_api import DiscordClient, DiscordMessageNotFoundError
from state_utils import load_json_object, save_json_object_atomic

DISCORD_DAILY_POSTS_FILE = "discord_daily_posts.json"
THUMBS_UP_EMOJI = "👍"
THUMBS_UP_EMOJI_ENCODED = quote(THUMBS_UP_EMOJI, safe="")

DISCORD_BOT_TOKEN = os.getenv("DISCORD_BOT_TOKEN")
DISCORD_WINNERS_CHANNEL_ID = os.getenv("DISCORD_WINNERS_CHANNEL_ID")
WINNERS_DATE_OVERRIDE_ENV = "WINNERS_DATE_UTC"
WINNERS_LOOKBACK_DAYS = 10
MAX_VOTERS_SHOWN_PER_GAME = 6
DISCORD_MESSAGE_CHAR_LIMIT = 2000
WINNERS_MESSAGE_TARGET_MAX = 1900

# Winners intentionally mirror daily section ordering as product behavior,
# not an incidental implementation detail.
SECTION_CONFIG = DAILY_SECTION_DISPLAY_LABELS
SECTION_ORDER = DAILY_SECTION_ORDER


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
            description = resolve_winner_description_for_message(item, section=section)
            if description:
                lines.append(description)
            lines.append(item["url"])
            vote_word = "vote" if item["human_votes"] == 1 else "votes"
            lines.append(f"👍 {item['human_votes']} {vote_word}")
            lines.append(f"Voters — {format_voter_names_for_message(item['voter_names'])}")
            lines.append("")

    if not has_any_winners:
        lines.append("_No votes yet today._")

    return "\n".join(lines).strip()


def _build_winner_item_lines(item: dict, *, section: str) -> List[str]:
    lines = [item["title"]]
    description = resolve_winner_description_for_message(item, section=section)
    if description:
        lines.append(description)
    lines.append(item["url"])
    vote_word = "vote" if item["human_votes"] == 1 else "votes"
    lines.append(f"👍 {item['human_votes']} {vote_word}")
    lines.append(f"Voters — {format_voter_names_for_message(item['voter_names'])}")
    lines.append("")
    return lines


def build_winners_message_chunks(
    winners_by_section: Dict[str, List[dict]],
    *,
    target_max: int = WINNERS_MESSAGE_TARGET_MAX,
    hard_limit: int = DISCORD_MESSAGE_CHAR_LIMIT,
) -> List[str]:
    full_message = build_winners_message(winners_by_section)
    if len(full_message) <= hard_limit:
        return [full_message]

    header = "🏆 Daily Game Picks — Winners"
    chunks: List[str] = []
    current_lines: List[str] = [header, ""]
    has_any_winners = any(isinstance(winners_by_section.get(section), list) and winners_by_section.get(section) for section in SECTION_ORDER)

    def finalize_current_chunk() -> None:
        if current_lines:
            rendered = "\n".join(current_lines).strip()
            if rendered:
                chunks.append(rendered)

    def can_fit(lines: List[str], extra_lines: List[str]) -> bool:
        rendered = "\n".join(lines + extra_lines).strip()
        return len(rendered) <= target_max

    if not has_any_winners:
        return [full_message]

    for section in SECTION_ORDER:
        items = winners_by_section.get(section, [])
        if not items:
            continue

        section_header_lines = [SECTION_CONFIG[section]]
        section_full_lines = section_header_lines[:]
        for item in items:
            section_full_lines.extend(_build_winner_item_lines(item, section=section))

        if can_fit(current_lines, section_full_lines):
            current_lines.extend(section_full_lines)
            continue

        if len("\n".join(current_lines).strip()) > len(header):
            finalize_current_chunk()
            current_lines = []

        current_lines.extend(section_header_lines)
        for item in items:
            item_lines = _build_winner_item_lines(item, section=section)
            if not can_fit(current_lines, item_lines):
                finalize_current_chunk()
                current_lines = [SECTION_CONFIG[section]]
            current_lines.extend(item_lines)

    finalize_current_chunk()
    for chunk in chunks:
        if len(chunk) > hard_limit:
            raise RuntimeError("Winners chunk exceeded Discord character limit")
    return chunks


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


def normalize_winner_description_for_message(
    description: Optional[str], max_length: int = 110
) -> str:
    if not isinstance(description, str):
        return ""
    cleaned = " ".join(description.split()).strip()
    if not cleaned:
        return ""
    if len(cleaned) <= max_length:
        return cleaned
    return f"{cleaned[:max_length - 3].rstrip()}..."


def build_instagram_legacy_description_fallback(item: dict) -> str:
    title = str(item.get("title") or "").strip()
    if title and not re.fullmatch(r"@[A-Za-z0-9._]+", title):
        return title

    creator = title if title else str(item.get("username") or "").strip()
    url = str(item.get("url") or "").strip()
    shortcode_match = re.search(r"instagram\.com/p/([^/?#]+)/?", url)
    shortcode = shortcode_match.group(1) if shortcode_match else ""
    if creator:
        if shortcode:
            return f"Instagram post from {creator} · post {shortcode} (caption unavailable in legacy state)"
        return f"Instagram post from {creator} (caption unavailable in legacy state)"
    return ""


def resolve_winner_description_for_message(item: dict, *, section: Optional[str] = None) -> str:
    normalized_description = normalize_winner_description_for_message(item.get("description"))
    if normalized_description:
        return normalized_description

    resolved_section = section or str(item.get("section") or "").strip()
    if resolved_section != "instagram":
        return ""

    fallback = build_instagram_legacy_description_fallback(item)
    return normalize_winner_description_for_message(fallback)


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




def build_winner_identity_key(item: dict) -> str:
    dedupe_key = str(item.get("url") or "").strip()
    if dedupe_key:
        return dedupe_key

    dedupe_key = str(item.get("item_key") or "").strip()
    if dedupe_key:
        return dedupe_key

    channel_id = str(item.get("channel_id") or "").strip()
    message_id = str(item.get("message_id") or "").strip()
    return f"{channel_id}:{message_id}"


def collect_recent_announced_winner_keys(
    daily_posts: Dict[str, dict],
    *,
    target_day_key: str,
    lookback_days: int = WINNERS_LOOKBACK_DAYS,
) -> set[str]:
    announced_keys: set[str] = set()
    for bucket_key in get_lookback_day_keys(target_day_key, lookback_days)[1:]:
        bucket = daily_posts.get(bucket_key, {})
        if not isinstance(bucket, dict):
            continue
        winners_state = bucket.get("winners_state")
        if not isinstance(winners_state, dict):
            continue
        winner_keys = winners_state.get("winner_keys")
        if not isinstance(winner_keys, list):
            continue
        for winner_key in winner_keys:
            normalized = str(winner_key).strip()
            if normalized:
                announced_keys.add(normalized)
    return announced_keys


def post_winners_message(client: DiscordClient, channel_id: str, message: str) -> str:
    payload = client.post_message(channel_id, message, context="post winners message")
    message_id = str(payload.get("id", ""))
    if not message_id:
        raise RuntimeError("Discord response missing winners message id")
    return message_id


def normalize_winners_message_ids(winners_state: dict) -> List[str]:
    message_ids = winners_state.get("message_ids")
    if isinstance(message_ids, list):
        normalized = [str(message_id).strip() for message_id in message_ids if str(message_id).strip()]
        if normalized:
            return normalized
    message_id = winners_state.get("message_id")
    if isinstance(message_id, str) and message_id.strip():
        return [message_id.strip()]
    return []


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
            dedupe_key = build_winner_identity_key(item)

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
                "description": item.get("description"),
                "human_votes": human_votes,
                "voter_names": voter_names,
            }
            if existing is None or candidate["human_votes"] > existing["human_votes"]:
                deduped_winners[dedupe_key] = candidate

        previously_announced_winner_keys = collect_recent_announced_winner_keys(
            daily_posts,
            target_day_key=day_key,
        )
        deduped_winners = {
            key: winner
            for key, winner in deduped_winners.items()
            if key not in previously_announced_winner_keys
        }

        for winner in deduped_winners.values():
            section = winner["section"]
            winners_by_section[section].append(
                {
                    "title": winner["title"],
                    "url": winner["url"],
                    "description": winner.get("description"),
                    "human_votes": winner["human_votes"],
                    "voter_names": winner["voter_names"],
                }
            )

        messages = build_winners_message_chunks(winners_by_section)
        winners_state = today_entry.get("winners_state")
        if not isinstance(winners_state, dict):
            winners_state = {}
            today_entry["winners_state"] = winners_state

        current_winner_keys = sorted(deduped_winners.keys())
        current_winner_vote_counts = {
            key: int(deduped_winners[key]["human_votes"])
            for key in current_winner_keys
        }
        previous_message_ids = normalize_winners_message_ids(winners_state)
        previous_winner_keys = winners_state.get("winner_keys")
        if not isinstance(previous_winner_keys, list):
            previous_winner_keys = []
        previous_winner_vote_counts = winners_state.get("winner_vote_counts")
        had_previous_vote_snapshot = isinstance(previous_winner_vote_counts, dict)
        if not had_previous_vote_snapshot:
            previous_winner_vote_counts = {}
        normalized_previous_vote_counts = {
            str(key): int(value)
            for key, value in previous_winner_vote_counts.items()
            if str(key)
        }

        if not current_winner_keys and not previous_message_ids:
            print(f"SKIP: no eligible winners in last {WINNERS_LOOKBACK_DAYS} days for {day_key}")
            return

        keys_unchanged = sorted(str(key) for key in previous_winner_keys) == current_winner_keys
        vote_counts_unchanged = normalized_previous_vote_counts == current_winner_vote_counts

        if keys_unchanged and not had_previous_vote_snapshot:
            winners_state["winner_vote_counts"] = current_winner_vote_counts
            winners_state["updated_at_utc"] = datetime.now(timezone.utc).isoformat()
            save_discord_daily_posts(daily_posts)
            print(f"SKIP: no newly eligible winners for {day_key} (backfilled vote snapshot)")
            return

        if keys_unchanged and vote_counts_unchanged:
            print(f"SKIP: no newly eligible winners for {day_key}")
            return

        if previous_message_ids:
            try:
                for index, previous_message_id in enumerate(previous_message_ids):
                    client.get_message(
                        winners_channel_id,
                        previous_message_id,
                        context=f"verify winners message {index + 1}/{len(previous_message_ids)} for {day_key}",
                    )
            except DiscordMessageNotFoundError:
                previous_message_ids = []
                print(f"RECOVER: stale/deleted winners message for {day_key}; posting replacement")

        resulting_message_ids: List[str] = []
        if previous_message_ids:
            for index, message in enumerate(messages):
                if index < len(previous_message_ids):
                    message_id = previous_message_ids[index]
                    client.edit_message(
                        winners_channel_id,
                        message_id,
                        message,
                        context=f"edit winners message chunk {index + 1}/{len(messages)} for {day_key}",
                    )
                    resulting_message_ids.append(message_id)
                else:
                    resulting_message_ids.append(post_winners_message(client, winners_channel_id, message))
            for stale_index in range(len(messages), len(previous_message_ids)):
                stale_message_id = previous_message_ids[stale_index]
                client.edit_message(
                    winners_channel_id,
                    stale_message_id,
                    "_(Winners content moved to earlier message chunks.)_",
                    context=f"clear stale winners chunk {stale_index + 1}/{len(previous_message_ids)} for {day_key}",
                )
            winners_state["last_action"] = "edit"
            winners_state["updated_at_utc"] = datetime.now(timezone.utc).isoformat()
            print(f"EDIT: updated winners messages for {day_key} (message_ids={resulting_message_ids})")
        else:
            resulting_message_ids = [post_winners_message(client, winners_channel_id, message) for message in messages]
            winners_state["last_action"] = "create"
            winners_state["posted_at_utc"] = datetime.now(timezone.utc).isoformat()
            print(f"CREATE: posted winners messages for {day_key} (message_ids={resulting_message_ids})")

        winners_state["message_id"] = resulting_message_ids[0]
        winners_state["message_ids"] = resulting_message_ids
        winners_state["winner_keys"] = current_winner_keys
        winners_state["winner_vote_counts"] = current_winner_vote_counts
        save_discord_daily_posts(daily_posts)


if __name__ == "__main__":
    main()
