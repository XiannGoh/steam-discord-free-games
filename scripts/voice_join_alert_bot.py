"""Live Discord bot for voice-channel join alerts with per-user cooldowns."""

import asyncio
import importlib
import os
import sys
import time
from dataclasses import dataclass
from typing import Any

from state_utils import load_json_object, save_json_object_atomic

TARGET_VOICE_CHANNEL_ID = "1491560965567938692"
COOLDOWN_SECONDS = 300
ROSTER_FILE = "data/scheduling/expected_schedule_roster.json"
COOLDOWN_STATE_FILE = "data/scheduling/voice_join_alert_cooldowns.json"
EXCLUDED_USER_IDS = {
    "162382481369071617",  # Malphax
    "161248274970443776",  # lilwartz
}


def fail(message: str) -> None:
    print(f"ERROR: {message}", file=sys.stderr)
    sys.exit(1)


def require_env(name: str) -> str:
    value = os.getenv(name)
    if not value:
        fail(f"Missing required environment variable: {name}")
    return value


def load_active_roster_user_ids(roster_file: str = ROSTER_FILE) -> set[str]:
    payload = load_json_object(roster_file, default={}, log=print)
    users = payload.get("users")
    if not isinstance(users, dict):
        print(f"WARN: roster file has unexpected shape ({roster_file}); using empty active roster")
        return set()

    active_ids: set[str] = set()
    for user_id, user_info in users.items():
        if not isinstance(user_id, str) or not isinstance(user_info, dict):
            continue
        if user_info.get("is_active") is True:
            active_ids.add(user_id)
    return active_ids


def build_ping_user_ids(active_user_ids: set[str], joiner_id: str) -> list[str]:
    filtered = [user_id for user_id in sorted(active_user_ids) if user_id not in EXCLUDED_USER_IDS and user_id != joiner_id]
    return filtered


def format_alert_message(joiner_id: str, ping_user_ids: list[str]) -> str:
    ping_mentions = " ".join(f"<@{user_id}>" for user_id in ping_user_ids)
    if ping_mentions:
        second_line = f"Heads up – don’t leave them hanging! {ping_mentions}"
    else:
        second_line = "Heads up – don’t leave them hanging!"
    return f"📣 <@{joiner_id}> just joined, bitches.\n{second_line}"


@dataclass
class CooldownStore:
    path: str = COOLDOWN_STATE_FILE
    cooldown_seconds: int = COOLDOWN_SECONDS

    def load(self) -> dict[str, float]:
        payload = load_json_object(self.path, default={}, log=print)
        last_ping_by_user: dict[str, float] = {}
        for user_id, last_ping in payload.items():
            if not isinstance(user_id, str):
                continue
            if isinstance(last_ping, (int, float)):
                last_ping_by_user[user_id] = float(last_ping)
        return last_ping_by_user

    def save(self, last_ping_by_user: dict[str, float]) -> None:
        save_json_object_atomic(self.path, {user_id: timestamp for user_id, timestamp in last_ping_by_user.items()})

    def should_alert(self, joiner_id: str, now_epoch: float, last_ping_by_user: dict[str, float]) -> bool:
        last_ping = last_ping_by_user.get(joiner_id)
        if last_ping is None:
            return True
        return (now_epoch - last_ping) >= float(self.cooldown_seconds)


def create_voice_join_alert_bot(cooldown_store: CooldownStore) -> Any:
    discord = importlib.import_module("discord")
    intents = discord.Intents.default()
    intents.voice_states = True
    intents.guilds = True

    class VoiceJoinAlertBot(discord.Client):
        def __init__(self):
            super().__init__(intents=intents)
            self.cooldown_store = cooldown_store
            self.last_ping_by_user = cooldown_store.load()
            self.active_roster_user_ids = load_active_roster_user_ids()

        async def on_ready(self) -> None:
            print(f"VOICE ALERT BOT READY: user={self.user} active_roster_size={len(self.active_roster_user_ids)}")

        async def on_voice_state_update(self, member: Any, before: Any, after: Any) -> None:
            if member.bot:
                return

            before_channel_id = str(before.channel.id) if before.channel else None
            after_channel_id = str(after.channel.id) if after.channel else None

            joined_target = after_channel_id == TARGET_VOICE_CHANNEL_ID and before_channel_id != TARGET_VOICE_CHANNEL_ID
            if not joined_target:
                return

            joiner_id = str(member.id)
            now_epoch = time.time()
            if not self.cooldown_store.should_alert(joiner_id, now_epoch, self.last_ping_by_user):
                print(f"COOLDOWN: skip alert for user_id={joiner_id}")
                return

            target_channel = after.channel
            if target_channel is None:
                print("WARN: target voice channel missing from voice state update; skipping")
                return

            ping_user_ids = build_ping_user_ids(self.active_roster_user_ids, joiner_id)
            message_content = format_alert_message(joiner_id, ping_user_ids)

            try:
                await target_channel.send(message_content)
                print(
                    f"ALERT SENT: joiner_id={joiner_id} target_channel_id={TARGET_VOICE_CHANNEL_ID} "
                    f"ping_count={len(ping_user_ids)}"
                )
                self.last_ping_by_user[joiner_id] = now_epoch
                self.cooldown_store.save(self.last_ping_by_user)
            except discord.DiscordException as error:
                print(f"ERROR: failed to send voice join alert for user_id={joiner_id}: {error}")

    return VoiceJoinAlertBot()


async def run_bot() -> None:
    token = require_env("DISCORD_VOICE_ALERT_BOT_TOKEN")
    bot = create_voice_join_alert_bot(cooldown_store=CooldownStore())
    await bot.start(token)


def main() -> None:
    try:
        asyncio.run(run_bot())
    except KeyboardInterrupt:
        print("VOICE ALERT BOT STOPPED")


if __name__ == "__main__":
    main()
