"""Tests for scripts/read_discord_channel.py."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

ROOT = Path(__file__).resolve().parents[1]


# ---------------------------------------------------------------------------
# Helpers to import the module under test
# ---------------------------------------------------------------------------

import sys

if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.read_discord_channel import (
    CHANNEL_NAMES,
    SNAPSHOT_FILES,
    _format_message,
    _format_reaction,
    fetch_channel_snapshot,
    resolve_channel_ids,
    write_snapshot,
)
from discord_api import DiscordApiError, DiscordClient


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

def _make_client(messages: list[dict[str, Any]] | None = None, raise_error: bool = False) -> DiscordClient:
    """Return a mocked DiscordClient."""
    client = MagicMock(spec=DiscordClient)
    if raise_error:
        client.get_channel_messages.side_effect = DiscordApiError("test error")
    else:
        client.get_channel_messages.return_value = messages or []
    return client


def _raw_message(
    msg_id: str = "111",
    username: str = "BotUser",
    content: str = "hello",
    timestamp: str = "2026-04-15T09:00:00.000000+00:00",
    reactions: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    return {
        "id": msg_id,
        "author": {"username": username, "global_name": username},
        "content": content,
        "timestamp": timestamp,
        "reactions": reactions or [],
    }


# ---------------------------------------------------------------------------
# _format_reaction
# ---------------------------------------------------------------------------

class TestFormatReaction:
    def test_basic_unicode_emoji(self) -> None:
        raw = {"emoji": {"name": "👍", "id": None}, "count": 5}
        result = _format_reaction(raw)
        assert result["emoji"] == "👍"
        assert result["count"] == 5
        assert "emoji_id" not in result

    def test_custom_emoji_includes_id(self) -> None:
        raw = {"emoji": {"name": "pepe", "id": "999"}, "count": 2}
        result = _format_reaction(raw)
        assert result["emoji"] == "pepe"
        assert result["count"] == 2
        assert result["emoji_id"] == "999"

    def test_missing_fields_handled(self) -> None:
        result = _format_reaction({})
        assert result["emoji"] == ""
        assert result["count"] == 0


# ---------------------------------------------------------------------------
# _format_message
# ---------------------------------------------------------------------------

class TestFormatMessage:
    def test_basic_fields(self) -> None:
        raw = _raw_message(msg_id="123", username="Bot", content="ping")
        result = _format_message(raw)
        assert result["id"] == "123"
        assert result["author"] == "Bot"
        assert result["content"] == "ping"
        assert isinstance(result["reactions"], list)

    def test_global_name_preferred_over_username(self) -> None:
        raw = _raw_message()
        raw["author"] = {"username": "bot_internal", "global_name": "XiannGPT Bot"}
        result = _format_message(raw)
        assert result["author"] == "XiannGPT Bot"

    def test_reactions_formatted(self) -> None:
        raw = _raw_message(reactions=[{"emoji": {"name": "👍", "id": None}, "count": 3}])
        result = _format_message(raw)
        assert len(result["reactions"]) == 1
        assert result["reactions"][0]["emoji"] == "👍"
        assert result["reactions"][0]["count"] == 3


# ---------------------------------------------------------------------------
# fetch_channel_snapshot
# ---------------------------------------------------------------------------

class TestFetchChannelSnapshot:
    def test_returns_correct_structure(self) -> None:
        msgs = [_raw_message(msg_id=str(i)) for i in range(3)]
        client = _make_client(messages=msgs)
        snapshot = fetch_channel_snapshot(client, "step1", "12345", limit=50)
        assert snapshot["channel_id"] == "12345"
        assert snapshot["channel_name"] == CHANNEL_NAMES["step1"]
        assert "fetched_at" in snapshot
        assert len(snapshot["messages"]) == 3

    def test_messages_have_required_fields(self) -> None:
        msgs = [_raw_message(msg_id="42", username="Bot", content="test")]
        client = _make_client(messages=msgs)
        snapshot = fetch_channel_snapshot(client, "step2", "999", limit=10)
        msg = snapshot["messages"][0]
        assert "id" in msg
        assert "author" in msg
        assert "content" in msg
        assert "timestamp" in msg
        assert "reactions" in msg

    def test_empty_channel_returns_empty_messages(self) -> None:
        client = _make_client(messages=[])
        snapshot = fetch_channel_snapshot(client, "step3", "111", limit=50)
        assert snapshot["messages"] == []


# ---------------------------------------------------------------------------
# resolve_channel_ids — missing env vars handled gracefully
# ---------------------------------------------------------------------------

class TestResolveChannelIds:
    def test_all_env_vars_set(self) -> None:
        env = {
            "DISCORD_STEP1_CHANNEL_ID": "ch1",
            "DISCORD_WINNERS_CHANNEL_ID": "ch2",
            "DISCORD_GAMING_LIBRARY_CHANNEL_ID": "ch3",
            "DISCORD_SCHEDULING_CHANNEL_ID": "ch4",
            "DISCORD_HEALTH_MONITOR_CHANNEL_ID": "ch5",
        }
        client = MagicMock(spec=DiscordClient)
        with patch.dict("os.environ", env, clear=False):
            ids = resolve_channel_ids(client)
        assert ids["step1"] == "ch1"
        assert ids["step2"] == "ch2"
        assert ids["step3"] == "ch3"
        assert ids["schedule"] == "ch4"
        assert ids["health"] == "ch5"

    def test_missing_step2_returns_none(self) -> None:
        env = {
            "DISCORD_STEP1_CHANNEL_ID": "ch1",
            "DISCORD_GAMING_LIBRARY_CHANNEL_ID": "ch3",
            "DISCORD_SCHEDULING_CHANNEL_ID": "ch4",
            "DISCORD_HEALTH_MONITOR_CHANNEL_ID": "ch5",
        }
        client = MagicMock(spec=DiscordClient)
        with patch.dict("os.environ", env, clear=False):
            # Remove DISCORD_WINNERS_CHANNEL_ID from environment
            import os as _os
            orig = _os.environ.pop("DISCORD_WINNERS_CHANNEL_ID", None)
            try:
                ids = resolve_channel_ids(client)
            finally:
                if orig is not None:
                    _os.environ["DISCORD_WINNERS_CHANNEL_ID"] = orig
        assert ids["step2"] is None

    def test_all_env_vars_missing_returns_all_none(self) -> None:
        """When no channel IDs are set and no webhooks given, all resolve to None."""
        keys_to_clear = [
            "DISCORD_STEP1_CHANNEL_ID",
            "DISCORD_WINNERS_CHANNEL_ID",
            "DISCORD_GAMING_LIBRARY_CHANNEL_ID",
            "DISCORD_SCHEDULING_CHANNEL_ID",
            "DISCORD_HEALTH_MONITOR_CHANNEL_ID",
            "DISCORD_WEBHOOK_URL",
            "DISCORD_HEALTH_MONITOR_WEBHOOK_URL",
        ]
        import os as _os
        originals = {k: _os.environ.pop(k, None) for k in keys_to_clear}
        client = MagicMock(spec=DiscordClient)
        try:
            ids = resolve_channel_ids(client)
        finally:
            for k, v in originals.items():
                if v is not None:
                    _os.environ[k] = v

        for key in ("step1", "step2", "step3", "schedule", "health"):
            assert ids[key] is None, f"Expected {key} to be None, got {ids[key]}"

    def test_step1_falls_back_to_webhook_lookup(self) -> None:
        """When DISCORD_STEP1_CHANNEL_ID absent, resolves via webhook URL."""
        import os as _os
        orig_step1 = _os.environ.pop("DISCORD_STEP1_CHANNEL_ID", None)
        orig_webhook = _os.environ.get("DISCORD_WEBHOOK_URL")
        _os.environ["DISCORD_WEBHOOK_URL"] = "https://discord.com/api/webhooks/123/abc"
        client = MagicMock(spec=DiscordClient)
        mock_response = MagicMock()
        mock_response.json.return_value = {"channel_id": "from_webhook"}
        client.request.return_value = mock_response
        try:
            ids = resolve_channel_ids(client)
        finally:
            if orig_step1 is not None:
                _os.environ["DISCORD_STEP1_CHANNEL_ID"] = orig_step1
            elif "DISCORD_STEP1_CHANNEL_ID" in _os.environ:
                del _os.environ["DISCORD_STEP1_CHANNEL_ID"]
            if orig_webhook is not None:
                _os.environ["DISCORD_WEBHOOK_URL"] = orig_webhook
            elif "DISCORD_WEBHOOK_URL" in _os.environ:
                del _os.environ["DISCORD_WEBHOOK_URL"]

        assert ids["step1"] == "from_webhook"

    def test_webhook_lookup_failure_returns_none(self) -> None:
        """If webhook API call fails, channel resolves to None gracefully."""
        import os as _os
        orig_step1 = _os.environ.pop("DISCORD_STEP1_CHANNEL_ID", None)
        orig_webhook = _os.environ.get("DISCORD_WEBHOOK_URL")
        _os.environ["DISCORD_WEBHOOK_URL"] = "https://discord.com/api/webhooks/bad/url"
        client = MagicMock(spec=DiscordClient)
        client.request.side_effect = DiscordApiError("bad webhook")
        try:
            ids = resolve_channel_ids(client)
        finally:
            if orig_step1 is not None:
                _os.environ["DISCORD_STEP1_CHANNEL_ID"] = orig_step1
            elif "DISCORD_STEP1_CHANNEL_ID" in _os.environ:
                del _os.environ["DISCORD_STEP1_CHANNEL_ID"]
            if orig_webhook is not None:
                _os.environ["DISCORD_WEBHOOK_URL"] = orig_webhook
            elif "DISCORD_WEBHOOK_URL" in _os.environ:
                del _os.environ["DISCORD_WEBHOOK_URL"]

        assert ids["step1"] is None


# ---------------------------------------------------------------------------
# write_snapshot — output JSON structure
# ---------------------------------------------------------------------------

class TestWriteSnapshot:
    def test_writes_correct_json_structure(self, tmp_path: Path) -> None:
        snapshot = {
            "channel_id": "123",
            "channel_name": "step-1-vote-on-games-to-test",
            "fetched_at": "2026-04-15T09:00:00Z",
            "messages": [
                {
                    "id": "999",
                    "author": "XiannGPT Bot",
                    "content": "hello",
                    "timestamp": "2026-04-15T09:00:00.000000+00:00",
                    "reactions": [{"emoji": "👍", "count": 3}],
                }
            ],
        }
        # Patch the SNAPSHOT_FILES dict to write to tmp_path
        out_file = tmp_path / "snapshot_step1.json"
        with patch("scripts.read_discord_channel.SNAPSHOT_FILES", {"step1": out_file}), \
             patch("scripts.read_discord_channel.DATA_DIR", tmp_path):
            write_snapshot("step1", snapshot)

        assert out_file.exists()
        written = json.loads(out_file.read_text(encoding="utf-8"))
        assert written["channel_id"] == "123"
        assert written["channel_name"] == "step-1-vote-on-games-to-test"
        assert "fetched_at" in written
        assert len(written["messages"]) == 1
        msg = written["messages"][0]
        assert msg["id"] == "999"
        assert msg["author"] == "XiannGPT Bot"
        assert msg["reactions"][0]["emoji"] == "👍"
        assert msg["reactions"][0]["count"] == 3

    def test_creates_data_dir_if_missing(self, tmp_path: Path) -> None:
        nested = tmp_path / "nested" / "data"
        snapshot = {
            "channel_id": "1",
            "channel_name": "test",
            "fetched_at": "2026-04-15T09:00:00Z",
            "messages": [],
        }
        out_file = nested / "snapshot_step2.json"
        with patch("scripts.read_discord_channel.SNAPSHOT_FILES", {"step2": out_file}), \
             patch("scripts.read_discord_channel.DATA_DIR", nested):
            write_snapshot("step2", snapshot)
        assert out_file.exists()
