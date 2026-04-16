import os
import re
from datetime import datetime, timedelta, timezone
from typing import Dict, List, Optional
from urllib.parse import quote

import requests

from daily_section_config import DAILY_SECTION_DISPLAY_LABELS, DAILY_SECTION_ORDER
from discord_api import DiscordClient, DiscordMessageNotFoundError
from state_utils import is_today_verified, load_json_object, save_json_object_atomic

DISCORD_DAILY_POSTS_FILE = "discord_daily_posts.json"
THUMBS_UP_EMOJI = "👍"
THUMBS_UP_EMOJI_ENCODED = quote(THUMBS_UP_EMOJI, safe="")
BOOKMARK_EMOJI = "🔖"
BOOKMARK_EMOJI_ENCODED = quote(BOOKMARK_EMOJI, safe="")

DISCORD_BOT_TOKEN = os.getenv("DISCORD_BOT_TOKEN")
DISCORD_WINNERS_CHANNEL_ID = os.getenv("DISCORD_WINNERS_CHANNEL_ID")
DISCORD_GUILD_ID = os.getenv("DISCORD_GUILD_ID")
WINNERS_DATE_OVERRIDE_ENV = "WINNERS_DATE_UTC"
GITHUB_EVENT_NAME_ENV = "GITHUB_EVENT_NAME"
WINNERS_LOOKBACK_DAYS = 10
MAX_VOTERS_SHOWN_PER_GAME = 6
DISCORD_MESSAGE_CHAR_LIMIT = 2000
WINNERS_MESSAGE_TARGET_MAX = 1900

WINNERS_INTRO_DIVIDER = "─────────────────────────────────────────"
WINNERS_FOOTER_SEPARATOR = "─────────────────── End of Daily Winners ───────────────────"

_WINNERS_INTRO_SECTION_LABELS = {
    "demo_playtest": ("🎮", "Demo & Playtest Winners"),
    "free": ("🆓", "Free Winners"),
    "paid": ("💰", "Paid Winners"),
    "instagram": ("📸", "Creator Winners"),
}
_WINNERS_FOOTER_SECTION_LABELS = {
    "demo_playtest": "Demo & Playtest",
    "free": "Free",
    "paid": "Paid",
    "instagram": "Creator",
}

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


def is_manual_run() -> bool:
    """Return True when triggered by workflow_dispatch (manual/test run).

    Manual runs post to Discord but do NOT block subsequent scheduled runs —
    they always re-post fresh content even when winners are unchanged.
    """
    event = (os.getenv(GITHUB_EVENT_NAME_ENV, "") or "").strip().lower()
    return event == "workflow_dispatch"


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


def format_winners_footer_date(target_day_key: str) -> str:
    target_day = datetime.fromisoformat(target_day_key).date()
    return f"{target_day:%A, %B} {target_day.day}, {target_day:%Y}"


def build_discord_message_link(guild_id: str, channel_id: str, message_id: str) -> str:
    return f"https://discord.com/channels/{guild_id}/{channel_id}/{message_id}"


def build_winners_header_placeholder(target_day_key: str) -> str:
    date_str = format_winners_footer_date(target_day_key)
    return "\n".join(
        [
            f"🏆 Daily Winners — {date_str}",
            "",
            "These games won the Step 1 vote. Play them and 🔖 bookmark to keep permanently.",
            "",
            WINNERS_INTRO_DIVIDER,
        ]
    )


def build_winners_navigation_header(
    winners_state: dict,
    *,
    guild_id: Optional[str],
    target_day_key: str,
    posted_section_keys: List[str],
) -> str:
    date_str = format_winners_footer_date(target_day_key)
    lines = [
        f"🏆 Daily Winners — {date_str}",
        "",
        "These games won the Step 1 vote. Play them and 🔖 bookmark to keep permanently.",
    ]

    if isinstance(guild_id, str) and guild_id.strip() and posted_section_keys:
        section_headers = winners_state.get("section_headers", {})
        parts = []
        for section_key in SECTION_ORDER:
            if section_key not in posted_section_keys:
                continue
            section_state = section_headers.get(section_key) if isinstance(section_headers, dict) else None
            if not isinstance(section_state, dict):
                continue
            channel_id = str(section_state.get("channel_id") or "").strip()
            message_id = str(section_state.get("message_id") or "").strip()
            if not channel_id or not message_id:
                continue
            emoji, label = _WINNERS_INTRO_SECTION_LABELS.get(section_key, ("", section_key))
            link = build_discord_message_link(guild_id, channel_id, message_id)
            parts.append(f"{emoji} [{label}]({link})")
        if parts:
            lines.append("")
            lines.append(" · ".join(parts))

    lines.append("")
    lines.append(WINNERS_INTRO_DIVIDER)
    return "\n".join(lines)


def build_winners_section_header(section: str) -> str:
    header_map = {
        "demo_playtest": "🧪 Demo & Playtest Winners",
        "free": "🎮 Free Winners",
        "paid": "💸 Paid Winners",
        "instagram": "📸 Creator Winners",
    }
    return header_map.get(section, SECTION_CONFIG.get(section, section))


def build_winner_game_message(item: dict, *, section: str) -> str:
    lines = [item["title"]]
    description = resolve_winner_description_for_message(item, section=section)
    if description:
        lines.append(description)
    lines.append(item["url"])
    vote_word = "vote" if item["human_votes"] == 1 else "votes"
    lines.append(f"👍 {item['human_votes']} {vote_word}")
    lines.append(f"Voters — {format_voter_names_for_message(item['voter_names'])}")
    return "\n".join(lines)


def build_winners_navigation_footer(
    winners_state: dict,
    *,
    guild_id: Optional[str],
    target_day_key: str,
    posted_section_keys: List[str],
) -> Optional[str]:
    if not isinstance(guild_id, str) or not guild_id.strip():
        print("WARN: DISCORD_GUILD_ID missing; skipping winners navigation footer.")
        return None
    intro_state = winners_state.get("intro")
    if not isinstance(intro_state, dict):
        return None
    intro_channel_id = str(intro_state.get("channel_id") or "").strip()
    intro_message_id = str(intro_state.get("message_id") or "").strip()
    if not intro_channel_id or not intro_message_id:
        return None

    section_headers = winners_state.get("section_headers", {})
    date_str = format_winners_footer_date(target_day_key)
    link_parts: List[str] = []
    for section_key in SECTION_ORDER:
        if section_key not in posted_section_keys:
            continue
        section_state = section_headers.get(section_key) if isinstance(section_headers, dict) else None
        if not isinstance(section_state, dict):
            return None
        channel_id = str(section_state.get("channel_id") or "").strip()
        message_id = str(section_state.get("message_id") or "").strip()
        if not channel_id or not message_id:
            return None
        label = _WINNERS_FOOTER_SECTION_LABELS.get(section_key, section_key)
        link_parts.append(f"[{label}]({build_discord_message_link(guild_id, channel_id, message_id)})")

    top_link = build_discord_message_link(guild_id, intro_channel_id, intro_message_id)
    link_parts.append(f"[⬆️ Top]({top_link})")

    first_line = f"📅 {date_str} · Jump to: {' · '.join(link_parts)}"
    return f"{first_line}\n{WINNERS_FOOTER_SEPARATOR}"


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


def add_bookmark_reaction(
    client: DiscordClient,
    *,
    channel_id: str,
    message_id: str,
    context: str,
) -> None:
    client.put_reaction(
        channel_id,
        message_id,
        BOOKMARK_EMOJI_ENCODED,
        context=context,
    )


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


def _coerce_int(value: object, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _build_winners_by_section_from_entries(entries: List[dict]) -> Dict[str, List[dict]]:
    winners_by_section: Dict[str, List[dict]] = {key: [] for key in SECTION_ORDER}
    for entry in entries:
        section = str(entry.get("section") or "").strip()
        if section not in winners_by_section:
            continue
        winners_by_section[section].append(
            {
                "title": entry.get("title", "Untitled"),
                "url": entry.get("url", ""),
                "description": entry.get("description"),
                "human_votes": _coerce_int(entry.get("human_votes"), 0),
                "voter_names": entry.get("voter_names", []),
            }
        )
    return winners_by_section


def collect_recent_announced_winner_index(
    daily_posts: Dict[str, dict],
    *,
    target_day_key: str,
    lookback_days: int = WINNERS_LOOKBACK_DAYS,
) -> Dict[str, dict]:
    announced_index: Dict[str, dict] = {}
    for bucket_key in get_lookback_day_keys(target_day_key, lookback_days)[1:]:
        bucket = daily_posts.get(bucket_key, {})
        if not isinstance(bucket, dict):
            continue
        winners_state = bucket.get("winners_state")
        if not isinstance(winners_state, dict):
            continue
        winner_entries = winners_state.get("winner_entries")
        if isinstance(winner_entries, list):
            for entry in winner_entries:
                if not isinstance(entry, dict):
                    continue
                winner_key = str(entry.get("winner_key") or "").strip()
                if not winner_key or winner_key in announced_index:
                    continue
                announced_index[winner_key] = {
                    "day_key": bucket_key,
                    "human_votes": _coerce_int(entry.get("human_votes"), 0),
                    "can_update_state": True,
                }
            continue

        winner_keys = winners_state.get("winner_keys")
        if not isinstance(winner_keys, list):
            continue
        winner_vote_counts = winners_state.get("winner_vote_counts")
        normalized_vote_counts = winner_vote_counts if isinstance(winner_vote_counts, dict) else {}
        for winner_key in winner_keys:
            normalized = str(winner_key).strip()
            if not normalized or normalized in announced_index:
                continue
            announced_index[normalized] = {
                "day_key": bucket_key,
                "human_votes": _coerce_int(normalized_vote_counts.get(normalized), 0),
                "can_update_state": False,
            }
    return announced_index


def update_existing_winner_entry_if_needed(
    daily_posts: Dict[str, dict],
    *,
    key: str,
    candidate: dict,
    announced_info: dict,
) -> bool:
    if not announced_info.get("can_update_state"):
        return False
    if candidate["human_votes"] <= _coerce_int(announced_info.get("human_votes"), 0):
        return False

    day_key = str(announced_info.get("day_key") or "").strip()
    if not day_key:
        return False
    bucket = daily_posts.get(day_key, {})
    if not isinstance(bucket, dict):
        return False
    winners_state = bucket.get("winners_state")
    if not isinstance(winners_state, dict):
        return False
    winner_entries = winners_state.get("winner_entries")
    if not isinstance(winner_entries, list):
        return False

    updated = False
    for entry in winner_entries:
        if not isinstance(entry, dict):
            continue
        entry_key = str(entry.get("winner_key") or "").strip()
        if entry_key != key:
            continue
        entry["section"] = candidate["section"]
        entry["title"] = candidate["title"]
        entry["url"] = candidate["url"]
        entry["description"] = candidate.get("description")
        entry["human_votes"] = candidate["human_votes"]
        entry["voter_names"] = candidate["voter_names"]
        updated = True
        break

    if not updated:
        return False

    winners_state["winner_vote_counts"] = {
        str(entry.get("winner_key") or "").strip(): _coerce_int(entry.get("human_votes"), 0)
        for entry in winner_entries
        if isinstance(entry, dict) and str(entry.get("winner_key") or "").strip()
    }
    winners_state["updated_at_utc"] = datetime.now(timezone.utc).isoformat()
    return True


def upsert_winners_messages_for_day(
    client: DiscordClient,
    *,
    daily_posts: Dict[str, dict],
    day_key: str,
    winners_channel_id: str,
) -> bool:
    bucket = daily_posts.get(day_key, {})
    if not isinstance(bucket, dict):
        return False
    winners_state = bucket.get("winners_state")
    if not isinstance(winners_state, dict):
        return False
    winner_entries = winners_state.get("winner_entries")
    if not isinstance(winner_entries, list):
        return False

    winner_entries_by_key = {
        str(entry.get("winner_key") or "").strip(): entry
        for entry in winner_entries
        if isinstance(entry, dict) and str(entry.get("winner_key") or "").strip()
    }
    if not winner_entries_by_key:
        return False
    publish_winners_for_entries(
        client,
        winners_state=winners_state,
        winners_channel_id=winners_channel_id,
        day_key=day_key,
        winner_entries_by_key=winner_entries_by_key,
    )
    winners_state["winner_keys"] = sorted(
        str(entry.get("winner_key") or "").strip()
        for entry in winner_entries
        if isinstance(entry, dict) and str(entry.get("winner_key") or "").strip()
    )
    winners_state["winner_vote_counts"] = {
        str(entry.get("winner_key") or "").strip(): _coerce_int(entry.get("human_votes"), 0)
        for entry in winner_entries
        if isinstance(entry, dict) and str(entry.get("winner_key") or "").strip()
    }
    return True


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


def _ensure_post_or_edit_message(
    client: DiscordClient,
    *,
    channel_id: str,
    state_entry: dict,
    content: str,
    context_prefix: str,
) -> dict:
    existing_message_id = str(state_entry.get("message_id") or "").strip()
    existing_channel_id = str(state_entry.get("channel_id") or channel_id).strip() or channel_id
    if existing_message_id:
        try:
            client.get_message(existing_channel_id, existing_message_id, context=f"verify {context_prefix}")
            client.edit_message(existing_channel_id, existing_message_id, content, context=f"edit {context_prefix}")
            state_entry["message_id"] = existing_message_id
            state_entry["channel_id"] = existing_channel_id
            return state_entry
        except DiscordMessageNotFoundError:
            print(f"RECOVER: stale/deleted {context_prefix}; posting replacement")
    payload = client.post_message(channel_id, content, context=f"post {context_prefix}")
    message_id = str(payload.get("id") or "").strip()
    if not message_id:
        raise RuntimeError(f"Discord response missing message id for {context_prefix}")
    state_entry["message_id"] = message_id
    state_entry["channel_id"] = str(payload.get("channel_id") or channel_id)
    return state_entry


def publish_winners_for_entries(
    client: DiscordClient,
    *,
    winners_state: dict,
    winners_channel_id: str,
    day_key: str,
    winner_entries_by_key: Dict[str, dict],
) -> None:
    intro_state = winners_state.setdefault("intro", {})
    section_headers = winners_state.setdefault("section_headers", {})
    winner_messages = winners_state.setdefault("winner_messages", {})
    footer_state = winners_state.setdefault("footer", {})
    if not isinstance(section_headers, dict):
        section_headers = {}
        winners_state["section_headers"] = section_headers
    if not isinstance(winner_messages, dict):
        winner_messages = {}
        winners_state["winner_messages"] = winner_messages

    # Post header placeholder first (without jump links) — edited after sections post
    _ensure_post_or_edit_message(
        client,
        channel_id=winners_channel_id,
        state_entry=intro_state,
        content=build_winners_header_placeholder(day_key),
        context_prefix=f"winners intro for {day_key}",
    )

    posted_section_keys: List[str] = []
    ordered_keys: List[str] = []
    for section in SECTION_ORDER:
        section_entries = [
            (winner_key, entry)
            for winner_key, entry in winner_entries_by_key.items()
            if str(entry.get("section") or "").strip() == section
        ]
        if not section_entries:
            continue
        posted_section_keys.append(section)
        section_state = section_headers.setdefault(section, {})
        _ensure_post_or_edit_message(
            client,
            channel_id=winners_channel_id,
            state_entry=section_state,
            content=build_winners_section_header(section),
            context_prefix=f"winners section header {section} for {day_key}",
        )
        for winner_key, entry in section_entries:
            message_state = winner_messages.setdefault(winner_key, {})
            _ensure_post_or_edit_message(
                client,
                channel_id=winners_channel_id,
                state_entry=message_state,
                content=build_winner_game_message(entry, section=section),
                context_prefix=f"winner game {winner_key} for {day_key}",
            )
            add_bookmark_reaction(
                client,
                channel_id=str(message_state.get("channel_id") or winners_channel_id),
                message_id=str(message_state.get("message_id") or ""),
                context=f"add bookmark reaction for winner {winner_key}",
            )
            ordered_keys.append(winner_key)

    for stale_key in list(winner_messages.keys()):
        if stale_key in winner_entries_by_key:
            continue
        stale_state = winner_messages.get(stale_key)
        if isinstance(stale_state, dict):
            stale_channel_id = str(stale_state.get("channel_id") or winners_channel_id).strip()
            stale_message_id = str(stale_state.get("message_id") or "").strip()
            if stale_channel_id and stale_message_id:
                try:
                    client.edit_message(
                        stale_channel_id,
                        stale_message_id,
                        "_(Winner no longer active for this day due to late-vote reconciliation.)_",
                        context=f"clear stale winner game {stale_key} for {day_key}",
                    )
                except DiscordMessageNotFoundError:
                    pass

    # Edit header to add jump links now that all section messages exist
    header_content = build_winners_navigation_header(
        winners_state,
        guild_id=DISCORD_GUILD_ID,
        target_day_key=day_key,
        posted_section_keys=posted_section_keys,
    )
    intro_message_id = str(intro_state.get("message_id") or "").strip()
    intro_channel_id = str(intro_state.get("channel_id") or winners_channel_id).strip()
    if intro_message_id and intro_channel_id:
        try:
            client.edit_message(
                intro_channel_id,
                intro_message_id,
                header_content,
                context=f"edit winners header with jump links for {day_key}",
            )
        except DiscordMessageNotFoundError:
            print(f"WARN: winners header message missing; skip header jump-link edit for {day_key}")
        except Exception as e:
            print(f"WARN: failed to edit winners header for {day_key}: {e}")

    footer_content = build_winners_navigation_footer(
        winners_state,
        guild_id=DISCORD_GUILD_ID,
        target_day_key=day_key,
        posted_section_keys=posted_section_keys,
    )
    if footer_content:
        _ensure_post_or_edit_message(
            client,
            channel_id=winners_channel_id,
            state_entry=footer_state,
            content=footer_content,
            context_prefix=f"winners footer for {day_key}",
        )

    message_ids: List[str] = []
    intro_id = str(intro_state.get("message_id") or "").strip()
    if intro_id:
        message_ids.append(intro_id)
    for section in SECTION_ORDER:
        if section not in posted_section_keys:
            continue
        sid = str(section_headers.get(section, {}).get("message_id") or "").strip()
        if sid:
            message_ids.append(sid)
        for winner_key in ordered_keys:
            entry = winner_entries_by_key.get(winner_key, {})
            if str(entry.get("section") or "").strip() != section:
                continue
            mid = str(winner_messages.get(winner_key, {}).get("message_id") or "").strip()
            if mid:
                message_ids.append(mid)
    footer_id = str(footer_state.get("message_id") or "").strip()
    if footer_id:
        message_ids.append(footer_id)
    if message_ids:
        winners_state["message_id"] = message_ids[0]
        winners_state["message_ids"] = message_ids


def main() -> None:
    if not DISCORD_BOT_TOKEN:
        raise RuntimeError("DISCORD_BOT_TOKEN is not set.")
    day_key = get_target_day_key()
    manual_run = is_manual_run()
    if manual_run:
        print("Evening winners run is a manual (workflow_dispatch) run — idempotency skips bypassed")
    print(f"Starting evening winners for day={day_key}")

    daily_posts = load_discord_daily_posts()
    today_entry = daily_posts.get(day_key, {})
    if not isinstance(today_entry, dict):
        today_entry = {}
        daily_posts[day_key] = today_entry

    # Rule 1 & 5: If winners already posted AND discord_verification.json shows pass=True
    # for today, suppress all re-triggers (watchdog, manual) — nothing to do.
    _winners_state_check = today_entry.get("winners_state") or {}
    if isinstance(_winners_state_check, dict) and _winners_state_check.get("winner_messages"):
        if is_today_verified(day_key):
            print(f"Run already completed and verified — watchdog re-trigger suppressed for {day_key}")
            return

    lookback_day_keys = get_lookback_day_keys(day_key)
    items: List[dict] = []
    for bucket_key in lookback_day_keys:
        bucket = daily_posts.get(bucket_key, {})
        if not isinstance(bucket, dict):
            continue
        bucket_items = bucket.get("items", [])
        if isinstance(bucket_items, list):
            items.extend(bucket_items)

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
                "channel_id": str(channel_id),
                "message_id": str(message_id),
            }
            if existing is None or candidate["human_votes"] > existing["human_votes"]:
                deduped_winners[dedupe_key] = candidate

        recently_announced_winner_index = collect_recent_announced_winner_index(
            daily_posts,
            target_day_key=day_key,
        )
        prior_days_requiring_updates: set[str] = set()
        new_winners_by_key: Dict[str, dict] = {}
        for key, winner in deduped_winners.items():
            announced_info = recently_announced_winner_index.get(key)
            if not announced_info:
                new_winners_by_key[key] = winner
                continue
            updated = update_existing_winner_entry_if_needed(
                daily_posts,
                key=key,
                candidate=winner,
                announced_info=announced_info,
            )
            if updated:
                prior_days_requiring_updates.add(str(announced_info.get("day_key") or "").strip())
        deduped_winners = new_winners_by_key

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
        previous_winner_entries = winners_state.get("winner_entries")
        has_previous_winner_entries = isinstance(previous_winner_entries, list)

        if not current_winner_keys and not previous_message_ids:
            for prior_day in sorted(key for key in prior_days_requiring_updates if key and key != day_key):
                upsert_winners_messages_for_day(
                    client,
                    daily_posts=daily_posts,
                    day_key=prior_day,
                    winners_channel_id=winners_channel_id,
                )
            if prior_days_requiring_updates:
                save_discord_daily_posts(daily_posts)
            print(f"SKIP: no eligible winners in last {WINNERS_LOOKBACK_DAYS} days for {day_key}")
            return

        keys_unchanged = sorted(str(key) for key in previous_winner_keys) == current_winner_keys
        vote_counts_unchanged = normalized_previous_vote_counts == current_winner_vote_counts
        current_winner_entries = [
            {
                "winner_key": key,
                "section": deduped_winners[key]["section"],
                "title": deduped_winners[key]["title"],
                "url": deduped_winners[key]["url"],
                "description": deduped_winners[key].get("description"),
                "human_votes": deduped_winners[key]["human_votes"],
                "voter_names": deduped_winners[key]["voter_names"],
            }
            for key in current_winner_keys
        ]

        if keys_unchanged and not had_previous_vote_snapshot and not manual_run:
            winners_state["winner_vote_counts"] = current_winner_vote_counts
            winners_state["winner_entries"] = current_winner_entries
            winners_state["updated_at_utc"] = datetime.now(timezone.utc).isoformat()
            for prior_day in sorted(key for key in prior_days_requiring_updates if key and key != day_key):
                upsert_winners_messages_for_day(
                    client,
                    daily_posts=daily_posts,
                    day_key=prior_day,
                    winners_channel_id=winners_channel_id,
                )
            save_discord_daily_posts(daily_posts)
            print(f"SKIP: no newly eligible winners for {day_key} (backfilled vote snapshot)")
            return

        if keys_unchanged and vote_counts_unchanged and has_previous_winner_entries and not manual_run:
            for prior_day in sorted(key for key in prior_days_requiring_updates if key and key != day_key):
                upsert_winners_messages_for_day(
                    client,
                    daily_posts=daily_posts,
                    day_key=prior_day,
                    winners_channel_id=winners_channel_id,
                )
            if prior_days_requiring_updates:
                save_discord_daily_posts(daily_posts)
            print(f"SKIP: no newly eligible winners for {day_key}")
            return

        if keys_unchanged and vote_counts_unchanged and not manual_run:
            winners_state["winner_entries"] = current_winner_entries
            winners_state["updated_at_utc"] = datetime.now(timezone.utc).isoformat()
            for prior_day in sorted(key for key in prior_days_requiring_updates if key and key != day_key):
                upsert_winners_messages_for_day(
                    client,
                    daily_posts=daily_posts,
                    day_key=prior_day,
                    winners_channel_id=winners_channel_id,
                )
            save_discord_daily_posts(daily_posts)
            print(f"SKIP: no newly eligible winners for {day_key}")
            return

        publish_winners_for_entries(
            client,
            winners_state=winners_state,
            winners_channel_id=winners_channel_id,
            day_key=day_key,
            winner_entries_by_key={entry["winner_key"]: entry for entry in current_winner_entries},
        )
        winners_state["last_action"] = "edit" if previous_message_ids else "create"
        winners_state["updated_at_utc"] = datetime.now(timezone.utc).isoformat()
        if not previous_message_ids:
            winners_state["posted_at_utc"] = datetime.now(timezone.utc).isoformat()
        winners_state["winner_keys"] = current_winner_keys
        winners_state["winner_vote_counts"] = current_winner_vote_counts
        winners_state["winner_entries"] = current_winner_entries
        for prior_day in sorted(key for key in prior_days_requiring_updates if key and key != day_key):
            upsert_winners_messages_for_day(
                client,
                daily_posts=daily_posts,
                day_key=prior_day,
                winners_channel_id=winners_channel_id,
            )
        save_discord_daily_posts(daily_posts)


if __name__ == "__main__":
    main()
