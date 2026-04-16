"""Ensure each Discord channel has a current pinned how-it-works message.

For each channel ID that is configured via environment variables, the script:
  - Checks if an existing pinned message ID is stored in data/pinned_messages.json
  - If the message still exists on Discord → edits it in place with the latest content
  - If the message is missing (deleted or never posted) → posts and pins a new one
  - Saves the updated message ID back to data/pinned_messages.json

State file: data/pinned_messages.json (keyed by channel slug, e.g. "step-1")
"""

import os
import sys

import requests

from discord_api import DiscordClient, DiscordMessageNotFoundError
from state_utils import load_json_object, save_json_object_atomic

PINNED_MESSAGES_FILE = "data/pinned_messages.json"
USER_AGENT = "steam-discord-free-games/ensure-pinned-messages"

# ---------------------------------------------------------------------------
# Pinned message content (one entry per channel slug)
# ---------------------------------------------------------------------------

PINNED_CONTENT: dict[str, str] = {
    "step-1": """\
📌 How This Works — #step-1-vote-on-games-to-test

Every morning the bot posts fresh game picks for the group to vote on.

👍 Vote on any game you want to try tonight
Top voted games move to #step-2-test-then-vote-to-keep in the evening

New picks are posted every morning""",

    "step-2": """\
📌 How This Works — #step-2-test-then-vote-to-keep

Every evening the bot posts the day's winners from #step-1-vote-on-games-to-test.

🔖 Bookmark any game you want to keep permanently
Bookmarked games move to #step-3-review-existing-games

Winners are posted every evening""",

    "step-3": """\
📌 How This Works — #step-3-review-existing-games

This is your group's permanent gaming library.

✅ Active — you want to play this
⏸️ Paused — taking a break
❌ Dropped — no longer interested

Use commands below to manage the library:
!addgame GameName SteamURL @user1 @user2
!add @user GameName
!remove @user GameName
!unassign @user
!rename GameName NewName
!archive GameName

Commands are processed periodically. Bot reacts ✅ when done.""",

    "step-4": """\
📌 How This Works — #update-weekly-schedule-here

Every weekend the bot posts the week's availability schedule.

Fill in when you're free to play each day
The bot identifies overlapping availability and suggests session times

Update your availability anytime — syncs automatically""",

    "step-5": """\
📌 About This Channel — #xiann-gpt-bot-health-monitor

This channel is for bot diagnostics only.

🟢 Green = all systems running normally
🔴 Red = a workflow failed and needs attention
🔧 Auto-fix attempts are logged here

Daily health report posted each evening
You only need to check this if something looks wrong in the other channels""",
}

# Map of (channel_slug → env_var_name_for_channel_id)
CHANNEL_ENV_MAP: dict[str, str] = {
    "step-1": "DISCORD_STEP1_CHANNEL_ID",
    "step-2": "DISCORD_WINNERS_CHANNEL_ID",
    "step-3": "DISCORD_GAMING_LIBRARY_CHANNEL_ID",
    "step-4": "DISCORD_SCHEDULING_CHANNEL_ID",
    "step-5": "DISCORD_HEALTH_MONITOR_CHANNEL_ID",
}


def _require_bot_token() -> str:
    token = os.getenv("DISCORD_BOT_TOKEN", "").strip()
    if not token:
        print("ERROR: DISCORD_BOT_TOKEN is not set", file=sys.stderr)
        sys.exit(1)
    return token


def _try_get_message(client: DiscordClient, channel_id: str, message_id: str, slug: str) -> bool:
    try:
        client.get_message(channel_id, message_id, context=f"verify pinned {slug}")
        return True
    except DiscordMessageNotFoundError:
        print(f"RECOVER: pinned message for {slug} is gone (message_id={message_id})")
        return False
    except Exception as error:
        print(f"WARN: could not verify pinned message for {slug}: {error}")
        return False


def ensure_pinned_messages(client: DiscordClient, state: dict) -> dict:
    """Process all configured channels. Returns updated state dict."""
    for slug, env_var in CHANNEL_ENV_MAP.items():
        channel_id = os.getenv(env_var, "").strip()
        if not channel_id:
            continue

        content = PINNED_CONTENT[slug]
        existing_id = state.get(slug, {}).get("message_id", "")

        if existing_id and _try_get_message(client, channel_id, existing_id, slug):
            client.edit_message(channel_id, existing_id, content, context=f"edit pinned {slug}")
            print(f"EDIT: pinned message for {slug} (message_id={existing_id})")
            # message_id unchanged — just update channel_id in case it changed
            state[slug] = {"channel_id": channel_id, "message_id": existing_id}
        else:
            payload = client.post_message(channel_id, content, context=f"post pinned {slug}")
            new_id = str(payload.get("id", ""))
            if not new_id:
                print(f"ERROR: Discord did not return message ID for {slug}", file=sys.stderr)
                continue
            client.pin_message(channel_id, new_id, context=f"pin {slug}")
            print(f"CREATE+PIN: pinned message for {slug} (message_id={new_id})")
            state[slug] = {"channel_id": channel_id, "message_id": new_id}

    return state


def main() -> None:
    token = _require_bot_token()

    configured = [slug for slug, ev in CHANNEL_ENV_MAP.items() if os.getenv(ev, "").strip()]
    if not configured:
        print("INFO: no channel IDs configured — nothing to pin")
        return

    print(f"ensure_pinned_messages: channels={configured}")

    state = load_json_object(PINNED_MESSAGES_FILE, log=print)

    with requests.Session() as session:
        session.headers.update(
            {
                "Authorization": f"Bot {token}",
                "Content-Type": "application/json",
                "User-Agent": USER_AGENT,
            }
        )
        client = DiscordClient(session)
        state = ensure_pinned_messages(client, state)

    save_json_object_atomic(PINNED_MESSAGES_FILE, state)
    print(f"Saved pinned message state to {PINNED_MESSAGES_FILE}")


if __name__ == "__main__":
    main()
