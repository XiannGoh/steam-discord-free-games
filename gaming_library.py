import os
import re
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional
from urllib.parse import quote

import requests

from discord_api import DiscordClient, DiscordMessageNotFoundError
from state_utils import load_json_object, save_json_object_atomic

GAMING_LIBRARY_FILE = "gaming_library.json"
DISCORD_DAILY_POSTS_FILE = "discord_daily_posts.json"
DISCORD_BOT_TOKEN = os.getenv("DISCORD_BOT_TOKEN")
DISCORD_GAMING_LIBRARY_CHANNEL_ID = os.getenv("DISCORD_GAMING_LIBRARY_CHANNEL_ID", "1492038691248410676")
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


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


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


def _slugify(text: str) -> str:
    normalized = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")
    return normalized or "untitled"


def _extract_steam_app_id(url: str) -> Optional[str]:
    match = re.search(r"store\.steampowered\.com/app/(\d+)", url or "")
    if match:
        return match.group(1)
    return None


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


def unassign_user(game: Dict[str, Any], user_id: str) -> None:
    assignments = game.setdefault("assignments", {})
    assignments.pop(str(user_id), None)
    game["updated_at_utc"] = utc_now_iso()


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


def build_daily_library_messages(state: Dict[str, Any], target_day_key: str) -> List[Dict[str, str]]:
    visible_games = list_visible_games_for_reminder(state)
    header = f"📚 Gaming Library — {target_day_key}"
    if not visible_games:
        return [{"type": "header", "content": header + "\n\n_No active library games for today._"}]

    messages: List[Dict[str, str]] = [{"type": "header", "content": header + "\n\nReact on each game: ✅ active · ⏸️ paused · ❌ dropped"}]
    for game in visible_games:
        lines = [f"🎮 {game.get('canonical_name', 'Untitled')}"]
        url = str(game.get("url") or "").strip()
        if url:
            lines.append(url)
        lines.append("Assigned:")
        visible_assignments = game.get("visible_assignments", {})
        if visible_assignments:
            for user_id, assignment in visible_assignments.items():
                status = str(assignment.get("status") or STATUS_ACTIVE)
                emoji = STATUS_TO_EMOJI.get(status, "✅")
                lines.append(f"- <@{user_id}> {emoji} ({status})")
        else:
            lines.append("- _No non-dropped assignees_")
        messages.append({"type": "game", "identity_key": game["identity_key"], "content": "\n".join(lines)})
    return messages


def post_daily_library_reminder(
    state: Dict[str, Any],
    *,
    day_key: str,
    channel_id: str,
    client: DiscordClient,
) -> bool:
    daily_posts = state.setdefault("daily_posts", {})
    day_entry = daily_posts.setdefault(day_key, {})
    if bool(day_entry.get("completed")):
        print(f"SKIP: library daily reminder already completed for {day_key}")
        return False

    messages = build_daily_library_messages(state, day_key)
    posted: Dict[str, Dict[str, str]] = {}
    for message in messages:
        payload = client.post_message(channel_id, message["content"], context=f"post gaming library {message['type']} for {day_key}")
        message_id = str(payload.get("id") or "")
        if not message_id:
            raise RuntimeError("Discord response missing id for gaming library message")
        posted[message.get("identity_key", message["type"])] = {"message_id": message_id, "channel_id": str(payload.get("channel_id") or channel_id)}

    day_entry["messages"] = posted
    day_entry["completed"] = True
    day_entry["completed_at_utc"] = utc_now_iso()
    return True


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
            source_item = items_index.get(winner_key)
            if not source_item:
                continue
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

            canonical_name = str(winner.get("title") or source_item.get("title") or "Untitled").strip()
            url = str(winner.get("url") or source_item.get("url") or "").strip()
            source_metadata = {
                "winner_key": winner_key,
                "description": winner.get("description") or source_item.get("description"),
                "daily_section": source_item.get("section"),
            }
            game = ensure_game_entry(
                state,
                canonical_name=canonical_name,
                url=url,
                source_type=str(source_item.get("source_type") or "winner_promotion"),
                source_section=str(source_item.get("section") or ""),
                source_metadata=source_metadata,
            )
            for user_id in human_user_ids:
                assign_user(game, user_id, STATUS_ACTIVE)
            game["archived"] = False
            refresh_archive_state(game)
            promoted_count += 1
    return promoted_count


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
            if identity_key in {"header", "intro"}:
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

            user_status_map: Dict[str, str] = {}
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
                    user_status_map[user_id] = status

            for user_id, status in user_status_map.items():
                set_user_status(game, user_id, status)
                updates += 1
            refresh_archive_state(game)
    return updates


def run_discord_sync(state_path: str = GAMING_LIBRARY_FILE, daily_posts_path: str = DISCORD_DAILY_POSTS_FILE) -> Dict[str, int]:
    if not DISCORD_BOT_TOKEN:
        raise RuntimeError("DISCORD_BOT_TOKEN is not set")

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

        promotions = sync_promotions_from_winners(state, daily_posts, client, bot_user_id)
        status_updates = sync_statuses_from_library_posts(state, client, bot_user_id)

    save_gaming_library(state, state_path)
    return {"promotions": promotions, "status_updates": status_updates}


def run_daily_post(state_path: str = GAMING_LIBRARY_FILE) -> bool:
    if not DISCORD_BOT_TOKEN:
        raise RuntimeError("DISCORD_BOT_TOKEN is not set")

    state = load_gaming_library(state_path)
    day_key = get_target_day_key()
    channel_id = DISCORD_GAMING_LIBRARY_CHANNEL_ID
    if not channel_id:
        raise RuntimeError("DISCORD_GAMING_LIBRARY_CHANNEL_ID is not set")

    with requests.Session() as session:
        session.headers.update({
            "Authorization": f"Bot {DISCORD_BOT_TOKEN}",
            "Content-Type": "application/json",
        })
        client = DiscordClient(session)
        posted = post_daily_library_reminder(state, day_key=day_key, channel_id=channel_id, client=client)

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
    user_ids = [uid for uid in (user_ids or []) if uid]

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
