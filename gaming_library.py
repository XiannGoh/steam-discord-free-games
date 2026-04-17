import os
import re
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional
from urllib.parse import quote

import requests

from discord_api import (
    DiscordClient,
    DiscordMessageNotFoundError,
    DiscordPermissionError,
    PERM_ADD_REACTIONS,
    PERM_MANAGE_MESSAGES,
    PERM_READ_MESSAGE_HISTORY,
    PERM_SEND_MESSAGES,
)
from state_utils import is_today_verified, load_json_object, save_json_object_atomic

GAMING_LIBRARY_FILE = "gaming_library.json"
DISCORD_DAILY_POSTS_FILE = "discord_daily_posts.json"
DISCORD_BOT_TOKEN = os.getenv("DISCORD_BOT_TOKEN")
DISCORD_GAMING_LIBRARY_CHANNEL_ID = os.getenv("DISCORD_GAMING_LIBRARY_CHANNEL_ID")
DISCORD_GUILD_ID = os.getenv("DISCORD_GUILD_ID")
DISCORD_HEALTH_MONITOR_WEBHOOK_URL = os.getenv("DISCORD_HEALTH_MONITOR_WEBHOOK_URL")
GITHUB_EVENT_NAME_ENV = "GITHUB_EVENT_NAME"
LIBRARY_DATE_OVERRIDE_ENV = "LIBRARY_DATE_UTC"

STATUS_ACTIVE = "active"
STATUS_PAUSED = "paused"
STATUS_DROPPED = "dropped"
ALLOWED_STATUSES = {STATUS_ACTIVE, STATUS_PAUSED, STATUS_DROPPED}

BOOKMARK_EMOJI = "🔖"
BOOKMARK_EMOJI_ENCODED = quote(BOOKMARK_EMOJI, safe="")
STATUS_TO_EMOJI = {
    STATUS_ACTIVE: "✅",
    STATUS_PAUSED: "⏸️",
    STATUS_DROPPED: "❌",
}
EMOJI_TO_STATUS = {emoji: status for status, emoji in STATUS_TO_EMOJI.items()}
EMOJI_TO_STATUS_ENCODED = {quote(emoji, safe=""): status for emoji, status in EMOJI_TO_STATUS.items()}
MENTION_USER_ID_PATTERN = re.compile(r"^<@!?(\d+)>$")

# Category constants for grouping games in library posts
CATEGORY_DEMO_PLAYTEST = "demo_playtest"
CATEGORY_FREE_PICKS = "free_picks"
CATEGORY_PAID_PICKS = "paid_picks"
CATEGORY_CREATOR_PICKS = "creator_picks"
CATEGORY_OTHER = "other"

CATEGORY_DISPLAY = {
    CATEGORY_DEMO_PLAYTEST: "🎮 Demo & Playtest",
    CATEGORY_FREE_PICKS: "🆓 Free Picks",
    CATEGORY_PAID_PICKS: "💰 Paid Picks",
    CATEGORY_CREATOR_PICKS: "📸 Creator Picks",
    CATEGORY_OTHER: "🎮 Other",
}

# Ordered display sequence
CATEGORY_ORDER = [
    CATEGORY_DEMO_PLAYTEST,
    CATEGORY_FREE_PICKS,
    CATEGORY_PAID_PICKS,
    CATEGORY_CREATOR_PICKS,
    CATEGORY_OTHER,
]
_LIBRARY_MISSING_CATEGORY_LABELS = {
    CATEGORY_DEMO_PLAYTEST: "Demo & Playtest",
    CATEGORY_FREE_PICKS: "Free Picks",
    CATEGORY_PAID_PICKS: "Paid Picks",
    CATEGORY_CREATOR_PICKS: "Creator Picks",
    CATEGORY_OTHER: "Other",
}

LIBRARY_INTRO_DIVIDER = "─────────────────────────────────────────"
LIBRARY_FOOTER_SEPARATOR = "─────────────────── End of Gaming Library ───────────────────"

_LIBRARY_INTRO_SECTION_LABELS = {
    CATEGORY_DEMO_PLAYTEST: ("🎮", "Demo & Playtest"),
    CATEGORY_FREE_PICKS: ("🆓", "Free Picks"),
    CATEGORY_PAID_PICKS: ("💰", "Paid Picks"),
    CATEGORY_CREATOR_PICKS: ("📸", "Creator Picks"),
    CATEGORY_OTHER: ("🎮", "Other"),
}
_LIBRARY_FOOTER_SECTION_LABELS = {
    CATEGORY_DEMO_PLAYTEST: "🎮 Demo & Playtest",
    CATEGORY_FREE_PICKS: "🆓 Free Picks",
    CATEGORY_PAID_PICKS: "💰 Paid",
    CATEGORY_CREATOR_PICKS: "📸 Creator",
    CATEGORY_OTHER: "🎮 Other",
}

COMMAND_REFERENCE_MESSAGE = """\
📋 Bot Commands — step-3-review-existing-games
`!add @user GameName` — assign a player to a game
`!remove @user GameName` — unassign a player from a game
`!rename GameName NewName` — rename a game
`!unassign @user` — remove a player from all games
`!archive GameName` — manually archive a game
`!addgame GameName SteamURL @user1 @user2` — add game directly to library

React on any game message to update your status:
✅ Playing · ⏸️ Paused · ❌ Dropped\
"""


def is_manual_run() -> bool:
    """Return True when triggered by workflow_dispatch (manual/test run).

    Manual runs post to Discord but do NOT mark the day as completed so that
    the subsequent scheduled run always executes cleanly.
    """
    event = (os.getenv(GITHUB_EVENT_NAME_ENV, "") or "").strip().lower()
    return event == "workflow_dispatch"


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def classify_game_category(game: Dict[str, Any]) -> str:
    """Return the display category for a game based on its source type/section."""
    source_type = str(game.get("source_type") or "").lower()
    source_section = str(game.get("source_section") or "").lower()
    if source_type in ("instagram", "creator") or source_section in ("instagram", "creator"):
        return CATEGORY_CREATOR_PICKS
    if source_type in ("steam_demo", "demo") or source_section in ("demo", "playtest") or "demo" in source_type or "playtest" in source_type:
        return CATEGORY_DEMO_PLAYTEST
    if source_type in ("paid", "paid_candidate") or source_section in ("paid",):
        return CATEGORY_PAID_PICKS
    if source_type in ("steam_free", "free", "winner_promotion") or source_section in ("free", "free_picks"):
        return CATEGORY_FREE_PICKS
    return CATEGORY_OTHER


def _suppress_steam_url(url: str) -> str:
    """Wrap Steam store URLs in <> to suppress Discord embed cards."""
    url = url.strip()
    if url.startswith("https://store.steampowered.com") or url.startswith("http://store.steampowered.com"):
        return f"<{url}>"
    return url


def _extract_game_name_from_caption(caption: str, steam_url: str) -> Optional[str]:
    """Try to extract an actual game name from an Instagram post caption or Steam URL.

    Returns the extracted name, or None if one cannot be determined.
    """
    if steam_url:
        match = re.search(r"store\.steampowered\.com/app/\d+/([^/?#]+)", steam_url)
        if match:
            raw = match.group(1).replace("_", " ").strip()
            if raw and raw.lower() not in ("", "app"):
                return raw
    if caption:
        # Look for patterns like "Game Name — check it out" or "New game: Game Name"
        # Just take first non-empty line up to any separator
        first_line = caption.strip().splitlines()[0] if caption.strip() else ""
        # Remove common Instagram fluff from start
        for prefix in ("new game:", "game:", "check out", "check this out", "play"):
            if first_line.lower().startswith(prefix):
                first_line = first_line[len(prefix):].strip()
                break
        # Trim at common separators
        for sep in (" — ", " - ", " | ", "!", "?", "#"):
            if sep in first_line:
                first_line = first_line[:first_line.index(sep)].strip()
        if first_line and len(first_line) >= 3:
            return first_line
    return None


def format_library_date(target_day_key: str) -> str:
    d = datetime.fromisoformat(target_day_key).date()
    return f"{d:%A, %B} {d.day}, {d:%Y}"


def get_target_day_key() -> str:
    manual_day = (os.getenv(LIBRARY_DATE_OVERRIDE_ENV, "") or "").strip()
    if manual_day:
        datetime.fromisoformat(manual_day)
        return manual_day
    return datetime.now(timezone.utc).date().isoformat()


def load_gaming_library(path: str = GAMING_LIBRARY_FILE) -> Dict[str, Any]:
    state = load_json_object(path, log=print)
    if not isinstance(state.get("games"), dict):
        state["games"] = {}
    if not isinstance(state.get("daily_posts"), dict):
        state["daily_posts"] = {}
    if not isinstance(state.get("version"), int):
        state["version"] = 1
    return state


def save_gaming_library(state: Dict[str, Any], path: str = GAMING_LIBRARY_FILE) -> None:
    save_json_object_atomic(path, state)


def load_discord_daily_posts(path: str = DISCORD_DAILY_POSTS_FILE) -> Dict[str, Any]:
    return load_json_object(path, log=print)


def compute_daily_delta(state: Dict[str, Any]) -> str:
    """Return formatted delta lines for embedding in the Step 3 intro message."""
    current_games = state.get("games", {})
    previous_games = state.get("previous_day_games", {})

    lines: List[str] = []

    for key, game in current_games.items():
        if not isinstance(game, dict):
            continue
        game_name = game.get("canonical_name", "Untitled")
        curr_assignments = game.get("assignments", {})
        if not isinstance(curr_assignments, dict):
            curr_assignments = {}

        if key not in previous_games:
            if not game.get("archived"):
                category = classify_game_category(game)
                section_label = CATEGORY_DISPLAY.get(category, "library")
                section_clean = section_label.lstrip("🧪🎮💸📸 ")
                lines.append(f"🆕 **{game_name}** added to {section_clean}")
        else:
            prev_game = previous_games[key]
            if not isinstance(prev_game, dict):
                prev_game = {}
            prev_assignments = prev_game.get("assignments", {})
            if not isinstance(prev_assignments, dict):
                prev_assignments = {}

            # Check if newly archived
            if game.get("archived") and not prev_game.get("archived"):
                lines.append(f"🗄️ **{game_name}** archived")
                continue

            if game.get("archived"):
                continue

            # Check if all active/paused players are now dropped
            non_dropped_before = {
                uid for uid, ass in prev_assignments.items()
                if isinstance(ass, dict) and ass.get("status") in (STATUS_ACTIVE, STATUS_PAUSED)
            }
            all_now_dropped = bool(curr_assignments) and all(
                isinstance(ass, dict) and ass.get("status") == STATUS_DROPPED
                for ass in curr_assignments.values()
            )
            if non_dropped_before and all_now_dropped:
                lines.append(f"❌ **{game_name}** dropped by all players")
                continue

            # Per-user status changes and new assignments
            for user_id, curr_ass in curr_assignments.items():
                if not isinstance(curr_ass, dict):
                    continue
                prev_ass = prev_assignments.get(user_id)
                if prev_ass is None:
                    lines.append(f"👤 <@{user_id}> added to **{game_name}**")
                elif isinstance(prev_ass, dict):
                    prev_status = str(prev_ass.get("status") or "")
                    curr_status = str(curr_ass.get("status") or "")
                    if curr_status != prev_status:
                        if curr_status == STATUS_ACTIVE:
                            lines.append(f"✅ **{game_name}** marked active by <@{user_id}>")
                        elif curr_status == STATUS_PAUSED:
                            lines.append(f"⏸️ **{game_name}** paused by <@{user_id}>")
                        elif curr_status == STATUS_DROPPED:
                            lines.append(f"❌ **{game_name}** dropped by <@{user_id}>")
                    # Pending: active but never reacted
                    if curr_status == STATUS_ACTIVE:
                        updated = curr_ass.get("updated_at_utc")
                        created = game.get("created_at_utc")
                        if updated == created:
                            lines.append(f"⏳ <@{user_id}> has not reacted on {game_name}")

            # Check for users removed
            for user_id in prev_assignments:
                if user_id not in curr_assignments:
                    lines.append(f"👤 <@{user_id}> removed from **{game_name}**")

    if not lines:
        return "• No changes since yesterday"
    return "\n".join(f"- {line}" for line in lines[:15])


def _extract_steam_app_id(url: str) -> Optional[str]:
    match = re.search(r"store\.steampowered\.com/app/(\d+)", url or "")
    if match:
        return match.group(1)
    return None


def _slugify(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", name.lower().strip()).strip("-")


def build_identity_key(canonical_name: str, url: str) -> str:
    app_id = _extract_steam_app_id(url)
    if app_id:
        return f"steam:{app_id}"
    if url:
        return f"url:{url.strip().lower()}"
    return f"name:{_slugify(canonical_name)}"


def ensure_game_entry(
    state: Dict[str, Any],
    *,
    canonical_name: str,
    url: str,
    source_type: Optional[str] = None,
    source_section: Optional[str] = None,
    source_metadata: Optional[Dict[str, Any]] = None,
    archived: bool = False,
) -> Dict[str, Any]:
    identity_key = build_identity_key(canonical_name, url)
    games = state.setdefault("games", {})
    game = games.get(identity_key)
    if not isinstance(game, dict):
        game = {
            "identity_key": identity_key,
            "canonical_name": canonical_name,
            "url": url,
            "source_type": source_type,
            "source_section": source_section,
            "source_metadata": source_metadata or {},
            "assignments": {},
            "archived": archived,
            "created_at_utc": utc_now_iso(),
            "updated_at_utc": utc_now_iso(),
            "last_activity_date": utc_now_iso()[:10],
        }
        games[identity_key] = game
    else:
        if canonical_name:
            game["canonical_name"] = canonical_name
        if url:
            game["url"] = url
        if source_type:
            game["source_type"] = source_type
        if source_section:
            game["source_section"] = source_section
        if source_metadata:
            existing_meta = game.get("source_metadata")
            if not isinstance(existing_meta, dict):
                existing_meta = {}
            existing_meta.update(source_metadata)
            game["source_metadata"] = existing_meta
        game["updated_at_utc"] = utc_now_iso()
    return game


def assign_user(game: Dict[str, Any], user_id: str, status: str = STATUS_ACTIVE) -> None:
    if status not in ALLOWED_STATUSES:
        raise RuntimeError(f"Invalid status: {status}")
    assignments = game.setdefault("assignments", {})
    assignments[str(user_id)] = {"status": status, "updated_at_utc": utc_now_iso()}
    game["updated_at_utc"] = utc_now_iso()
    game["last_activity_date"] = utc_now_iso()[:10]


def assign_user_if_changed(game: Dict[str, Any], user_id: str, status: str = STATUS_ACTIVE) -> bool:
    assignments = game.setdefault("assignments", {})
    existing = assignments.get(str(user_id))
    if isinstance(existing, dict) and str(existing.get("status") or "") == status:
        return False
    assign_user(game, user_id, status)
    return True


def unassign_user(game: Dict[str, Any], user_id: str) -> None:
    assignments = game.setdefault("assignments", {})
    assignments.pop(str(user_id), None)
    game["updated_at_utc"] = utc_now_iso()
    game["last_activity_date"] = utc_now_iso()[:10]


def set_user_status(game: Dict[str, Any], user_id: str, status: str) -> None:
    if status not in ALLOWED_STATUSES:
        raise RuntimeError(f"Invalid status: {status}")
    assignments = game.setdefault("assignments", {})
    current = assignments.get(str(user_id), {})
    if not isinstance(current, dict):
        current = {}
    current["status"] = status
    current["updated_at_utc"] = utc_now_iso()
    assignments[str(user_id)] = current
    game["updated_at_utc"] = utc_now_iso()
    game["last_activity_date"] = utc_now_iso()[:10]


def refresh_archive_state(game: Dict[str, Any]) -> None:
    assignments = game.get("assignments", {})
    if not isinstance(assignments, dict) or not assignments:
        return
    statuses = []
    for assignment in assignments.values():
        if isinstance(assignment, dict):
            statuses.append(str(assignment.get("status") or STATUS_ACTIVE))
    if statuses and all(status == STATUS_DROPPED for status in statuses):
        game["archived"] = True
    game["updated_at_utc"] = utc_now_iso()


def list_visible_games_for_reminder(state: Dict[str, Any]) -> List[Dict[str, Any]]:
    visible = []
    for game in state.get("games", {}).values():
        if not isinstance(game, dict):
            continue
        assignments = game.get("assignments", {})
        if not isinstance(assignments, dict):
            assignments = {}
        visible_assignments = {
            uid: assignment
            for uid, assignment in assignments.items()
            if isinstance(assignment, dict) and assignment.get("status") in {STATUS_ACTIVE, STATUS_PAUSED}
        }
        archived = bool(game.get("archived"))
        if archived and not visible_assignments:
            continue
        game_copy = dict(game)
        game_copy["visible_assignments"] = visible_assignments
        visible.append(game_copy)

    visible.sort(
        key=lambda g: (
            -len(g.get("visible_assignments", {})),
            str(g.get("canonical_name") or "").lower(),
        )
    )
    return visible


def _discord_message_link(guild_id: str, channel_id: str, message_id: str) -> str:
    return f"https://discord.com/channels/{guild_id}/{channel_id}/{message_id}"


def build_library_footer(
    *,
    day_key: str,
    header_channel_id: str,
    header_message_id: str,
    guild_id: Optional[str],
    channel_id: str = "",
    posted_section_keys: Optional[Dict[str, str]] = None,
) -> str:
    """Build the Step 3 footer: date+jump line followed by End separator."""
    date_str = format_library_date(day_key)
    if not isinstance(guild_id, str) or not guild_id.strip() or not header_message_id:
        return f"📅 {date_str}\n{LIBRARY_FOOTER_SEPARATOR}"

    link_parts: List[str] = []
    if posted_section_keys and channel_id:
        for category in CATEGORY_ORDER:
            section_key = f"section:{category}"
            msg_id = posted_section_keys.get(section_key)
            if not msg_id:
                continue
            label = _LIBRARY_FOOTER_SECTION_LABELS.get(category, category)
            link_parts.append(f"[{label}]({_discord_message_link(guild_id, channel_id, msg_id)})")

    top_link = _discord_message_link(guild_id, header_channel_id, header_message_id)
    link_parts.append(f"[⬆️ Top]({top_link})")

    first_line = f"📅 {date_str} · Jump to: {' · '.join(link_parts)}"
    return f"{first_line}\n{LIBRARY_FOOTER_SEPARATOR}"


def build_library_header_placeholder(target_day_key: str, state: Optional[Dict[str, Any]] = None) -> str:
    date_str = format_library_date(target_day_key)
    lines = [
        f"📚 Gaming Library — {date_str}",
        "",
        "React to each game: ✅ active · ⏸️ paused · ❌ dropped",
    ]
    delta_content = compute_daily_delta(state) if state is not None else "• No changes since yesterday"
    lines += [
        "",
        LIBRARY_INTRO_DIVIDER,
        "📊 Today's Changes",
        delta_content,
        LIBRARY_INTRO_DIVIDER,
    ]
    return "\n".join(lines)


def build_library_navigation_header(
    target_day_key: str,
    *,
    guild_id: Optional[str],
    channel_id: str,
    posted_section_keys: Dict[str, str],
    state: Optional[Dict[str, Any]] = None,
) -> str:
    """Build header with jump links, delta summary, and dividers."""
    date_str = format_library_date(target_day_key)
    lines = [
        f"📚 Gaming Library — {date_str}",
        "",
        "React to each game: ✅ active · ⏸️ paused · ❌ dropped",
    ]

    if isinstance(guild_id, str) and guild_id.strip() and posted_section_keys:
        parts = []
        for category in CATEGORY_ORDER:
            section_key = f"section:{category}"
            msg_id = posted_section_keys.get(section_key)
            if not msg_id:
                continue
            emoji, label = _LIBRARY_INTRO_SECTION_LABELS.get(category, ("🎮", category))
            link = _discord_message_link(guild_id, channel_id, msg_id)
            parts.append(f"{emoji} [{label}]({link})")
        if parts:
            lines.append("")
            lines.append(" · ".join(parts))

    delta_content = compute_daily_delta(state) if state is not None else "• No changes since yesterday"
    lines += [
        "",
        LIBRARY_INTRO_DIVIDER,
        "📊 Today's Changes",
        delta_content,
        LIBRARY_INTRO_DIVIDER,
    ]
    return "\n".join(lines)


def build_daily_library_messages(state: Dict[str, Any], target_day_key: str) -> List[Dict[str, str]]:
    visible_games = list_visible_games_for_reminder(state)

    # Group by category
    by_category: Dict[str, List[Dict[str, Any]]] = {}
    for game in visible_games:
        cat = classify_game_category(game)
        by_category.setdefault(cat, []).append(game)

    active_categories = [c for c in CATEGORY_ORDER if c in by_category]

    messages: List[Dict[str, str]] = []

    # Header placeholder (will be edited after sections are posted)
    messages.append({"type": "header", "content": build_library_header_placeholder(target_day_key, state)})

    if not visible_games:
        delta_content = compute_daily_delta(state)
        messages[0]["content"] = "\n".join([
            f"📚 Gaming Library — {format_library_date(target_day_key)}",
            "",
            "_No active library games for today._",
            "",
            LIBRARY_INTRO_DIVIDER,
            "📊 Today's Changes",
            delta_content,
            LIBRARY_INTRO_DIVIDER,
        ])
    else:
        for category in active_categories:
            section_label = CATEGORY_DISPLAY[category]
            messages.append({
                "type": "section_header",
                "section_key": f"section:{category}",
                "identity_key": f"section:{category}",
                "content": f"**{section_label}**",
            })
            for game in by_category[category]:
                name = game.get("canonical_name", "Untitled")
                lines = [f"🎮 {name}"]
                url = str(game.get("url") or "").strip()
                if url:
                    lines.append(_suppress_steam_url(url))
                last_act = game.get("last_activity_date")
                if last_act:
                    try:
                        d = datetime.fromisoformat(last_act).date()
                        lines.append(f"Last activity: {d:%b} {d.day}, {d:%Y}")
                    except (ValueError, AttributeError):
                        pass
                lines.append("Players:")
                visible_assignments = game.get("visible_assignments", {})
                conflict_users = game.get("conflicting_users", [])
                if visible_assignments:
                    for user_id, assignment in visible_assignments.items():
                        status = str(assignment.get("status") or STATUS_ACTIVE)
                        emoji = STATUS_TO_EMOJI.get(status, "✅")
                        lines.append(f"- <@{user_id}> {emoji} ({status})")
                else:
                    lines.append("- _No active players_")
                for user_id in conflict_users:
                    lines.append(f"- ⚠️ <@{user_id}> — conflicting reactions, defaulted to Active")
                messages.append({"type": "game", "identity_key": game["identity_key"], "content": "\n".join(lines)})

        for category in CATEGORY_ORDER:
            if category in active_categories:
                continue
            label = _LIBRARY_MISSING_CATEGORY_LABELS.get(category, category)
            messages.append({
                "type": "empty_section",
                "identity_key": f"empty:{category}",
                "content": f"**{label}**\n_No games in this category_",
            })

    return messages


def _post_or_edit_message(
    client: DiscordClient,
    channel_id: str,
    content: str,
    *,
    context: str,
    existing_info: Optional[Dict[str, str]],
) -> tuple[Dict[str, Any], bool]:
    """Post or edit a message. Returns (payload, is_new_message)."""
    existing_channel_id = str((existing_info or {}).get("channel_id") or channel_id).strip()
    existing_message_id = str((existing_info or {}).get("message_id") or "").strip()
    if existing_message_id:
        try:
            payload = client.edit_message(existing_channel_id, existing_message_id, content, context=context)
            return payload, False
        except DiscordMessageNotFoundError:
            pass
    payload = client.post_message(channel_id, content, context=context)
    return payload, True


def post_daily_library_reminder(
    state: Dict[str, Any],
    *,
    day_key: str,
    channel_id: str,
    client: DiscordClient,
    manual_run: bool = False,
) -> bool:
    daily_posts = state.setdefault("daily_posts", {})
    day_entry = daily_posts.setdefault(day_key, {})
    existing_messages = day_entry.get("messages")
    if not isinstance(existing_messages, dict):
        existing_messages = {}

    messages = build_daily_library_messages(state, day_key)
    reconciled_messages: Dict[str, Dict[str, str]] = {}
    changed = False

    for message in messages:
        message_key = str(message.get("identity_key", message["type"]))
        existing_info = existing_messages.get(message_key)
        payload, is_new_message = _post_or_edit_message(
            client,
            channel_id,
            message["content"],
            context=f"post/edit gaming library {message['type']} for {day_key}",
            existing_info=existing_info,
        )
        changed = True

        message_id = str(payload.get("id") or "")
        if not message_id:
            raise RuntimeError("Discord response missing id for gaming library message")
        if message.get("type") == "game" and is_new_message:
            for emoji in ("✅", "⏸️", "❌"):
                client.put_reaction(
                    str(payload.get("channel_id") or channel_id),
                    message_id,
                    quote(emoji, safe=""),
                    context=f"add gaming library status reaction {emoji} for {day_key}",
                )
        reconciled_messages[message_key] = {"message_id": message_id, "channel_id": str(payload.get("channel_id") or channel_id)}

    # --- Edit header with jump links after all sections are posted ---
    header_info = reconciled_messages.get("header", {})
    header_ch_id = str(header_info.get("channel_id") or channel_id).strip()
    header_msg_id = str(header_info.get("message_id") or "").strip()
    posted_section_keys = {key: info["message_id"] for key, info in reconciled_messages.items() if key.startswith("section:")}
    if posted_section_keys and header_msg_id:
        nav_header = build_library_navigation_header(
            day_key,
            guild_id=DISCORD_GUILD_ID,
            channel_id=header_ch_id,
            posted_section_keys=posted_section_keys,
            state=state,
        )
        client.edit_message(header_ch_id, header_msg_id, nav_header, context=f"update gaming library header with jump links for {day_key}")

    # --- Footer ---
    footer_content = build_library_footer(
        day_key=day_key,
        header_channel_id=header_ch_id,
        header_message_id=header_msg_id,
        guild_id=DISCORD_GUILD_ID,
        channel_id=channel_id,
        posted_section_keys=posted_section_keys,
    )
    footer_payload, _ = _post_or_edit_message(
        client,
        channel_id,
        footer_content,
        context=f"post/edit gaming library footer for {day_key}",
        existing_info=existing_messages.get("footer"),
    )
    footer_msg_id = str(footer_payload.get("id") or "")
    if not footer_msg_id:
        raise RuntimeError("Discord response missing id for gaming library footer")
    reconciled_messages["footer"] = {
        "message_id": footer_msg_id,
        "channel_id": str(footer_payload.get("channel_id") or channel_id),
    }

    day_entry["messages"] = reconciled_messages
    if manual_run:
        print(f"MANUAL RUN: gaming library daily post done for {day_key}; skipping completed=True to preserve scheduled run eligibility")
    else:
        day_entry["completed"] = True
        day_entry["completed_at_utc"] = utc_now_iso()
    return changed


def _build_winner_identity_key(item: Dict[str, Any]) -> str:
    dedupe_key = str(item.get("url") or "").strip()
    if dedupe_key:
        return dedupe_key
    dedupe_key = str(item.get("item_key") or "").strip()
    if dedupe_key:
        return dedupe_key
    return f"{item.get('channel_id')}:{item.get('message_id')}"


def sync_promotions_from_winners(state: Dict[str, Any], daily_posts: Dict[str, Any], client: DiscordClient, bot_user_id: Optional[str]) -> int:
    promoted_count = 0
    items_index: Dict[str, Dict[str, Any]] = {}
    for day_entry in daily_posts.values():
        if not isinstance(day_entry, dict):
            continue
        items = day_entry.get("items", [])
        if not isinstance(items, list):
            continue
        for item in items:
            if isinstance(item, dict):
                items_index[_build_winner_identity_key(item)] = item

    for day_entry in daily_posts.values():
        if not isinstance(day_entry, dict):
            continue
        winners_state = day_entry.get("winners_state")
        if not isinstance(winners_state, dict):
            continue
        winner_entries = winners_state.get("winner_entries")
        if not isinstance(winner_entries, list):
            continue

        for winner in winner_entries:
            if not isinstance(winner, dict):
                continue
            winner_key = str(winner.get("winner_key") or "").strip()
            if not winner_key:
                continue
            winner_messages = winners_state.get("winner_messages")
            winner_message = winner_messages.get(winner_key) if isinstance(winner_messages, dict) else None
            channel_id = str((winner_message or {}).get("channel_id") or "").strip()
            message_id = str((winner_message or {}).get("message_id") or "").strip()
            source_item = items_index.get(winner_key)
            if (not channel_id or not message_id) and isinstance(source_item, dict):
                channel_id = str(source_item.get("channel_id") or "").strip()
                message_id = str(source_item.get("message_id") or "").strip()
            if not channel_id or not message_id:
                continue

            users = client.get_reaction_users(
                channel_id,
                message_id,
                BOOKMARK_EMOJI_ENCODED,
                context=f"library bookmark users {winner_key}",
                limit=100,
            )
            human_user_ids = [str(user.get("id") or "").strip() for user in users if str(user.get("id") or "").strip() and str(user.get("id") or "").strip() != bot_user_id]
            if not human_user_ids:
                continue

            raw_title = str(winner.get("title") or (source_item or {}).get("title") or "").strip()
            url = str(winner.get("url") or (source_item or {}).get("url") or "").strip()
            # Enhancement 6: for Instagram sources, try to extract a better game name
            item_source_type = str((source_item or {}).get("source_type") or "").lower()
            if item_source_type in ("instagram", "creator"):
                caption = str((source_item or {}).get("description") or "").strip()
                extracted = _extract_game_name_from_caption(caption, url)
                if extracted:
                    canonical_name = extracted
                else:
                    # raw_title is typically the creator's username, not a game name
                    canonical_name = "⚠️ Name needed — use !rename command"
            else:
                canonical_name = raw_title or "Untitled"
            source_metadata = {
                "winner_key": winner_key,
                "description": winner.get("description") or (source_item or {}).get("description"),
                "daily_section": (source_item or {}).get("section"),
            }
            identity_key = build_identity_key(canonical_name, url)
            game_preexisted = identity_key in state.setdefault("games", {})
            game = ensure_game_entry(
                state,
                canonical_name=canonical_name,
                url=url,
                source_type=str((source_item or {}).get("source_type") or "winner_promotion"),
                source_section=str((source_item or {}).get("section") or ""),
                source_metadata=source_metadata,
            )
            assignment_changed = False
            for user_id in human_user_ids:
                if assign_user_if_changed(game, user_id, STATUS_ACTIVE):
                    assignment_changed = True
            game["archived"] = False
            refresh_archive_state(game)
            if (not game_preexisted) or assignment_changed:
                promoted_count += 1
    return promoted_count


def normalize_user_id_token(user_id_like: str) -> str:
    token = str(user_id_like or "").strip()
    if not token:
        return ""
    if token.isdigit():
        return token
    mention_match = MENTION_USER_ID_PATTERN.fullmatch(token)
    if mention_match:
        return mention_match.group(1)
    return ""


def sync_statuses_from_library_posts(state: Dict[str, Any], client: DiscordClient, bot_user_id: Optional[str]) -> int:
    updates = 0
    daily_posts = state.get("daily_posts", {})
    if not isinstance(daily_posts, dict):
        return 0

    games = state.get("games", {})
    if not isinstance(games, dict):
        return 0

    for day_entry in daily_posts.values():
        if not isinstance(day_entry, dict):
            continue
        messages = day_entry.get("messages")
        if not isinstance(messages, dict):
            continue
        for identity_key, message_info in messages.items():
            if identity_key in {"header", "intro", "footer", "delta"} or identity_key.startswith("section:"):
                continue
            if not isinstance(message_info, dict):
                continue
            channel_id = str(message_info.get("channel_id") or "").strip()
            message_id = str(message_info.get("message_id") or "").strip()
            if not channel_id or not message_id:
                continue
            game = games.get(identity_key)
            if not isinstance(game, dict):
                continue

            # Collect all status reactions per user
            user_reactions: Dict[str, List[str]] = {}
            for encoded_emoji, status in EMOJI_TO_STATUS_ENCODED.items():
                users = client.get_reaction_users(
                    channel_id,
                    message_id,
                    encoded_emoji,
                    context=f"library status users {identity_key} {status}",
                    limit=100,
                )
                for user in users:
                    user_id = str(user.get("id") or "").strip()
                    if not user_id or user_id == bot_user_id:
                        continue
                    user_reactions.setdefault(user_id, []).append(status)

            # Enhancement 9: detect conflicts (multiple status reactions)
            conflict_users: List[str] = []
            for user_id, statuses in user_reactions.items():
                if len(statuses) > 1:
                    # Conflicting reactions — reset to active
                    conflict_users.append(user_id)
                    set_user_status(game, user_id, STATUS_ACTIVE)
                    updates += 1
                else:
                    set_user_status(game, user_id, statuses[0])
                    updates += 1
            game["conflicting_users"] = conflict_users

            # E5: Default any assigned player with no valid status to Active.
            # This covers players assigned before they react, and any legacy data
            # where a status field is missing or empty.
            assignments = game.get("assignments", {})
            if isinstance(assignments, dict):
                for user_id, assignment in assignments.items():
                    if not isinstance(assignment, dict):
                        continue
                    if assignment.get("status") not in ALLOWED_STATUSES:
                        set_user_status(game, user_id, STATUS_ACTIVE)
                        updates += 1

            refresh_archive_state(game)
    return updates


COMMAND_CHECKMARK_ENCODED = quote("✅", safe="")


def _parse_command_line(text: str) -> Optional[tuple[str, List[str]]]:
    """Parse a bot command from a message line. Returns (command, args) or None."""
    text = text.strip()
    if not text.startswith("!"):
        return None
    parts = text.split()
    if not parts:
        return None
    return parts[0].lower(), parts[1:]


def process_library_commands(
    state: Dict[str, Any],
    client: DiscordClient,
    channel_id: str,
    bot_user_id: Optional[str],
) -> int:
    """Read unprocessed !command messages from the channel and apply them to state.

    Returns the number of commands successfully processed.
    """
    processed_ids: List[str] = state.setdefault("processed_command_ids", [])
    processed_set = set(processed_ids)
    processed_count = 0

    messages = client.get_channel_messages(channel_id, context="library command scan", limit=100)
    # Messages are returned newest-first; process oldest-first
    for msg in reversed(messages):
        msg_id = str(msg.get("id") or "").strip()
        if not msg_id or msg_id in processed_set:
            continue
        author = msg.get("author") or {}
        author_id = str(author.get("id") or "").strip()
        if author_id == bot_user_id:
            continue
        content = str(msg.get("content") or "").strip()
        parsed = _parse_command_line(content)
        if not parsed:
            continue
        command, args = parsed
        ok = False
        try:
            ok = _apply_library_command(state, command, args)
        except Exception:
            pass
        if ok:
            processed_ids.append(msg_id)
            processed_set.add(msg_id)
            processed_count += 1
            try:
                client.put_reaction(channel_id, msg_id, COMMAND_CHECKMARK_ENCODED, context=f"ack command {msg_id}")
            except Exception:
                pass

    return processed_count


def _apply_library_command(state: Dict[str, Any], command: str, args: List[str]) -> bool:
    """Apply a single parsed library command to state. Returns True if applied."""
    games = state.setdefault("games", {})

    if command == "!add":
        # !add @user GameName...
        if len(args) < 2:
            return False
        user_token = args[0]
        user_id = normalize_user_id_token(user_token)
        if not user_id:
            return False
        game_name = " ".join(args[1:])
        game = _find_game_by_name(games, game_name)
        if game is None:
            game = ensure_game_entry(state, canonical_name=game_name, url="", source_type="manual", source_section="manual")
        assign_user(game, user_id, STATUS_ACTIVE)
        game["archived"] = False
        return True

    elif command == "!remove":
        # !remove @user GameName...
        if len(args) < 2:
            return False
        user_token = args[0]
        user_id = normalize_user_id_token(user_token)
        if not user_id:
            return False
        game_name = " ".join(args[1:])
        game = _find_game_by_name(games, game_name)
        if game is None:
            return False
        unassign_user(game, user_id)
        refresh_archive_state(game)
        return True

    elif command == "!rename":
        # !rename OldName NewName (split at last space as new name)
        if len(args) < 2:
            return False
        # Try to match the longest prefix as existing game name
        game = None
        new_name = ""
        for split_at in range(len(args) - 1, 0, -1):
            candidate_name = " ".join(args[:split_at])
            candidate_new = " ".join(args[split_at:])
            found = _find_game_by_name(games, candidate_name)
            if found is not None:
                game = found
                new_name = candidate_new
                break
        if game is None or not new_name:
            return False
        game["canonical_name"] = new_name
        game["updated_at_utc"] = utc_now_iso()
        return True

    elif command == "!unassign":
        # !unassign @user
        if not args:
            return False
        user_id = normalize_user_id_token(args[0])
        if not user_id:
            return False
        for game in games.values():
            if isinstance(game, dict):
                unassign_user(game, user_id)
                refresh_archive_state(game)
        return True

    elif command == "!archive":
        # !archive GameName...
        if not args:
            return False
        game_name = " ".join(args)
        game = _find_game_by_name(games, game_name)
        if game is None:
            return False
        game["archived"] = True
        game["updated_at_utc"] = utc_now_iso()
        return True

    elif command == "!addgame":
        # !addgame GameName SteamURL @user1 @user2...
        # First arg that looks like a URL is the steam URL boundary
        if not args:
            return False
        url_idx = None
        for i, arg in enumerate(args):
            if arg.startswith("http"):
                url_idx = i
                break
        if url_idx is None or url_idx == 0:
            return False
        game_name = " ".join(args[:url_idx])
        url = args[url_idx]
        user_tokens = args[url_idx + 1:]
        user_ids = [normalize_user_id_token(t) for t in user_tokens]
        user_ids = [u for u in user_ids if u]
        game = ensure_game_entry(state, canonical_name=game_name, url=url, source_type="manual", source_section="manual")
        for user_id in user_ids:
            assign_user(game, user_id, STATUS_ACTIVE)
        game["archived"] = False
        refresh_archive_state(game)
        return True

    return False


def _find_game_by_name(games: Dict[str, Any], name: str) -> Optional[Dict[str, Any]]:
    """Find a game by canonical name (case-insensitive). Returns the game dict or None."""
    name_lower = name.lower().strip()
    for game in games.values():
        if isinstance(game, dict) and game.get("canonical_name", "").lower() == name_lower:
            return game
    return None


def _notify_health_monitor(message: str) -> None:
    """Post a warning to the Discord health monitor webhook (best-effort, never raises)."""
    url = DISCORD_HEALTH_MONITOR_WEBHOOK_URL
    if not url:
        return
    try:
        requests.post(url, json={"content": message}, timeout=10)
    except Exception:
        pass


def _check_channel_permissions(
    client: DiscordClient,
    channel_id: str,
    guild_id: str,
    context: str,
    *,
    bot_user_id: str = "",
) -> None:
    """Preflight check: warn if the bot is missing required permissions in a channel.

    Logs a warning and notifies the health monitor if any required permission is
    missing.  Always returns — the caller continues regardless.
    """
    required = {
        "Send Messages": PERM_SEND_MESSAGES,
        "Add Reactions": PERM_ADD_REACTIONS,
        "Read Message History": PERM_READ_MESSAGE_HISTORY,
        "Manage Messages": PERM_MANAGE_MESSAGES,
    }
    try:
        effective = client.check_bot_permissions(channel_id, guild_id, bot_user_id=bot_user_id)
    except Exception as exc:
        print(f"WARN: {context} — could not check bot permissions: {exc}")
        return

    if effective == 0:
        print(f"WARN: {context} — could not determine bot permissions for channel {channel_id}")
        return

    missing = [name for name, flag in required.items() if not (effective & flag)]
    if not missing:
        return

    warning = (
        f"⚠️ {context} — Bot is missing Discord permissions in <#{channel_id}>: "
        + ", ".join(missing)
        + ". The bot will attempt to continue but errors may occur."
    )
    print(f"WARN: {warning}")
    _notify_health_monitor(
        f"⚠️ Gaming Library — Missing Discord Permissions\n\n"
        f"Context: {context}\n"
        f"Channel: <#{channel_id}>\n"
        f"Missing: {', '.join(missing)}\n\n"
        f"The bot will attempt to continue but errors may occur. "
        f"Please grant the required permissions to the bot in that channel."
    )


def ensure_command_reference_pinned(
    state: Dict[str, Any],
    client: DiscordClient,
    channel_id: str,
) -> None:
    """Post the command reference message and pin it if not already done."""
    pinned_info = state.get("command_reference_message")
    existing_msg_id = str((pinned_info or {}).get("message_id") or "").strip()
    if existing_msg_id:
        # Already pinned — edit to keep current
        try:
            client.edit_message(channel_id, existing_msg_id, COMMAND_REFERENCE_MESSAGE, context="update command reference")
        except DiscordMessageNotFoundError:
            existing_msg_id = ""
    if not existing_msg_id:
        payload = client.post_message(channel_id, COMMAND_REFERENCE_MESSAGE, context="post command reference")
        msg_id = str(payload.get("id") or "").strip()
        if msg_id:
            try:
                client.pin_message(channel_id, msg_id, context="pin command reference")
            except DiscordPermissionError as exc:
                warning = (
                    f"⚠️ Bot is missing permission to pin messages in <#{channel_id}>. "
                    f"The command reference was posted (message ID {msg_id}) but could not be pinned. "
                    f"Please grant the bot 'Manage Messages' permission in this channel."
                )
                print(f"WARN: pin command reference — {exc}")
                try:
                    client.post_message(channel_id, warning, context="post pin permission warning")
                except Exception:
                    pass
                _notify_health_monitor(
                    f"⚠️ Gaming Library — Missing Pin Permission\n\n"
                    f"Channel: <#{channel_id}>\n"
                    f"The bot posted the command reference (message ID {msg_id}) but cannot pin it. "
                    f"Please grant 'Manage Messages' permission to the bot in that channel."
                )
            except Exception:
                pass
            state["command_reference_message"] = {"message_id": msg_id, "channel_id": channel_id}


def run_discord_sync(state_path: str = GAMING_LIBRARY_FILE, daily_posts_path: str = DISCORD_DAILY_POSTS_FILE) -> Dict[str, int]:
    if not DISCORD_BOT_TOKEN:
        raise RuntimeError("DISCORD_BOT_TOKEN is not set")

    channel_id = DISCORD_GAMING_LIBRARY_CHANNEL_ID
    state = load_gaming_library(state_path)
    daily_posts = load_discord_daily_posts(daily_posts_path)

    with requests.Session() as session:
        session.headers.update({
            "Authorization": f"Bot {DISCORD_BOT_TOKEN}",
            "Content-Type": "application/json",
        })
        client = DiscordClient(session)
        bot_user = client.get_current_user(context="fetch bot user for gaming library")
        bot_user_id = str(bot_user.get("id") or "").strip() or None

        if channel_id and DISCORD_GUILD_ID:
            _check_channel_permissions(
                client, channel_id, DISCORD_GUILD_ID, "gaming library sync",
                bot_user_id=bot_user_id or "",
            )

        promotions = sync_promotions_from_winners(state, daily_posts, client, bot_user_id)
        status_updates = sync_statuses_from_library_posts(state, client, bot_user_id)
        commands_processed = 0
        if channel_id:
            commands_processed = process_library_commands(state, client, channel_id, bot_user_id)
            ensure_command_reference_pinned(state, client, channel_id)

    save_gaming_library(state, state_path)
    return {"promotions": promotions, "status_updates": status_updates, "commands_processed": commands_processed}


def run_daily_post(state_path: str = GAMING_LIBRARY_FILE) -> bool:
    if not DISCORD_BOT_TOKEN:
        raise RuntimeError("DISCORD_BOT_TOKEN is not set")

    state = load_gaming_library(state_path)
    day_key = get_target_day_key()
    channel_id = DISCORD_GAMING_LIBRARY_CHANNEL_ID
    if not channel_id:
        raise RuntimeError("DISCORD_GAMING_LIBRARY_CHANNEL_ID is not set")

    # Rule 1 & 5: If already completed AND discord_verification.json shows pass=True
    # for today, suppress all re-triggers (watchdog, manual) — nothing to do.
    _day_entry_check = state.get("daily_posts", {}).get(day_key, {})
    if isinstance(_day_entry_check, dict) and bool(_day_entry_check.get("completed")):
        if is_today_verified(day_key):
            print(f"Run already completed and verified — watchdog re-trigger suppressed for {day_key}")
            return False

    manual_run = is_manual_run()
    if manual_run:
        print("Gaming library daily post is a manual (workflow_dispatch) run — completed flag will not be set")

    # Save previous day's games for delta comparison
    state["previous_day_games"] = dict(state.get("games", {}))

    with requests.Session() as session:
        session.headers.update({
            "Authorization": f"Bot {DISCORD_BOT_TOKEN}",
            "Content-Type": "application/json",
        })
        client = DiscordClient(session)
        if DISCORD_GUILD_ID:
            _check_channel_permissions(
                client, channel_id, DISCORD_GUILD_ID, "gaming library daily post",
            )
        posted = post_daily_library_reminder(state, day_key=day_key, channel_id=channel_id, client=client, manual_run=manual_run)

    save_gaming_library(state, state_path)
    return posted


def manage_library(
    *,
    operation: str,
    canonical_name: str = "",
    url: str = "",
    source_type: str = "",
    source_section: str = "",
    source_caption: str = "",
    identity_key: str = "",
    user_ids: Optional[List[str]] = None,
    status: str = STATUS_ACTIVE,
    archive: Optional[bool] = None,
    state_path: str = GAMING_LIBRARY_FILE,
) -> bool:
    state = load_gaming_library(state_path)
    normalized_user_ids: List[str] = []
    for user_id_like in user_ids or []:
        normalized = normalize_user_id_token(str(user_id_like))
        if normalized:
            normalized_user_ids.append(normalized)
    user_ids = normalized_user_ids

    if operation == "add":
        game = ensure_game_entry(
            state,
            canonical_name=canonical_name,
            url=url,
            source_type=source_type or "manual",
            source_section=source_section or "manual",
            source_metadata={"original_caption": source_caption} if source_caption else {},
        )
        for user_id in user_ids:
            assign_user(game, user_id, status)
        if archive is not None:
            game["archived"] = bool(archive)
        refresh_archive_state(game)
    else:
        target_key = identity_key or build_identity_key(canonical_name, url)
        game = state.get("games", {}).get(target_key)
        if not isinstance(game, dict):
            raise RuntimeError(f"Game not found for key={target_key}")

        if operation == "rename":
            if not canonical_name:
                raise RuntimeError("canonical_name is required for rename")
            game["canonical_name"] = canonical_name
            game["updated_at_utc"] = utc_now_iso()
        elif operation == "assign":
            for user_id in user_ids:
                assign_user(game, user_id, status)
            game["archived"] = False
        elif operation == "unassign":
            for user_id in user_ids:
                unassign_user(game, user_id)
        elif operation == "set_status":
            for user_id in user_ids:
                set_user_status(game, user_id, status)
        elif operation == "archive":
            game["archived"] = True
        elif operation == "unarchive":
            game["archived"] = False
        else:
            raise RuntimeError(f"Unsupported operation: {operation}")
        refresh_archive_state(game)

    save_gaming_library(state, state_path)
    return True
