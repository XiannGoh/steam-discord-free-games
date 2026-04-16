"""Tests for scripts/ensure_pinned_messages.py"""

import json

import pytest

from scripts import ensure_pinned_messages as epm
from discord_api import DiscordMessageNotFoundError


class FakeClient:
    def __init__(self, existing_message_ids=None, stale_ids=None):
        self.existing = set(existing_message_ids or [])
        self.stale = set(stale_ids or [])
        self.created_messages = []  # list of (context, content, new_id)
        self.edits = []             # list of (channel_id, message_id, content)
        self.pin_calls = []         # list of message_id

    def get_message(self, channel_id, message_id, *, context):
        if message_id in self.stale:
            raise DiscordMessageNotFoundError("gone")
        if message_id in self.existing:
            return {"id": message_id}
        raise RuntimeError("not found")

    def post_message(self, channel_id, content, *, context):
        new_id = f"new-{len(self.created_messages) + 1}"
        self.created_messages.append((context, content, new_id))
        return {"id": new_id}

    def edit_message(self, channel_id, message_id, content, *, context):
        self.edits.append((channel_id, message_id, content))
        return {"id": message_id}

    def pin_message(self, channel_id, message_id, *, context):
        self.pin_calls.append(message_id)


def _run(monkeypatch, tmp_path, *, existing_state=None, channel_envs=None, fake_client=None):
    """Helper: set up env and run ensure_pinned_messages with a FakeClient."""
    path = tmp_path / "pinned_messages.json"
    path.write_text(json.dumps(existing_state or {}), encoding="utf-8")
    monkeypatch.setattr(epm, "PINNED_MESSAGES_FILE", str(path))

    monkeypatch.setenv("DISCORD_BOT_TOKEN", "test-token")
    # Clear all channel env vars first
    for ev in epm.CHANNEL_ENV_MAP.values():
        monkeypatch.delenv(ev, raising=False)
    # Set only the ones requested
    for key, val in (channel_envs or {}).items():
        monkeypatch.setenv(key, val)

    if fake_client is None:
        fake_client = FakeClient()

    import requests as requests_module
    monkeypatch.setattr(requests_module, "Session", lambda: _FakeSession())
    monkeypatch.setattr(epm, "DiscordClient", lambda session: fake_client)

    epm.main()
    saved = json.loads(path.read_text(encoding="utf-8"))
    return saved, fake_client


class _FakeSession:
    headers = {}

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def update(self, *a, **kw):
        pass


def test_pinned_message_posted_and_pinned_when_not_exists(monkeypatch, tmp_path):
    """When no existing message ID is stored, the script posts and pins a new message."""
    saved, fake = _run(
        monkeypatch, tmp_path,
        channel_envs={"DISCORD_STEP1_CHANNEL_ID": "chan-step1"},
    )

    assert len(fake.created_messages) == 1
    ctx, content, new_id = fake.created_messages[0]
    assert "step-1" in ctx
    assert content == epm.PINNED_CONTENT["step-1"]
    assert new_id in fake.pin_calls
    assert saved["step-1"]["message_id"] == new_id


def test_pinned_message_edited_in_place_when_exists(monkeypatch, tmp_path):
    """When an existing message ID is stored and still valid, it is edited in place."""
    existing_id = "msg-step2-existing"
    saved, fake = _run(
        monkeypatch, tmp_path,
        existing_state={"step-2": {"channel_id": "chan-step2", "message_id": existing_id}},
        channel_envs={"DISCORD_WINNERS_CHANNEL_ID": "chan-step2"},
        fake_client=FakeClient(existing_message_ids={existing_id}),
    )

    assert fake.created_messages == []   # no new post
    assert fake.pin_calls == []          # no new pin
    assert len(fake.edits) == 1
    assert fake.edits[0][1] == existing_id
    assert fake.edits[0][2] == epm.PINNED_CONTENT["step-2"]
    assert saved["step-2"]["message_id"] == existing_id


def test_no_duplicate_posted_when_already_exists(monkeypatch, tmp_path):
    """Running the script twice with a valid message ID produces no duplicate posts."""
    existing_id = "msg-step3-existing"
    fake = FakeClient(existing_message_ids={existing_id})

    # First run — sets up state
    saved, _ = _run(
        monkeypatch, tmp_path,
        existing_state={"step-3": {"channel_id": "chan-step3", "message_id": existing_id}},
        channel_envs={"DISCORD_GAMING_LIBRARY_CHANNEL_ID": "chan-step3"},
        fake_client=fake,
    )

    # Second run — same state, same fake client
    saved2, _ = _run(
        monkeypatch, tmp_path,
        existing_state=saved,
        channel_envs={"DISCORD_GAMING_LIBRARY_CHANNEL_ID": "chan-step3"},
        fake_client=fake,
    )

    assert len(fake.created_messages) == 0  # still zero posts after two runs
    assert saved2["step-3"]["message_id"] == existing_id


def test_stale_message_replaced_with_new_post_and_pin(monkeypatch, tmp_path):
    """When the stored message ID is stale (deleted), a new message is posted and pinned."""
    stale_id = "msg-stale-99"
    saved, fake = _run(
        monkeypatch, tmp_path,
        existing_state={"step-4": {"channel_id": "chan-step4", "message_id": stale_id}},
        channel_envs={"DISCORD_SCHEDULING_CHANNEL_ID": "chan-step4"},
        fake_client=FakeClient(stale_ids={stale_id}),
    )

    assert len(fake.created_messages) == 1
    new_id = fake.created_messages[0][2]
    assert new_id in fake.pin_calls
    assert saved["step-4"]["message_id"] == new_id
    assert saved["step-4"]["message_id"] != stale_id


def test_unconfigured_channels_skipped(monkeypatch, tmp_path):
    """Channels whose env vars are not set are silently skipped."""
    saved, fake = _run(
        monkeypatch, tmp_path,
        channel_envs={"DISCORD_STEP1_CHANNEL_ID": "chan-step1"},
    )

    # Only step-1 should be in state; others not set
    assert "step-1" in saved
    assert "step-2" not in saved
    assert "step-3" not in saved


def test_all_five_channels_processed_when_all_configured(monkeypatch, tmp_path):
    """All 5 channels are posted when all channel IDs are configured."""
    saved, fake = _run(
        monkeypatch, tmp_path,
        channel_envs={
            "DISCORD_STEP1_CHANNEL_ID": "chan-1",
            "DISCORD_WINNERS_CHANNEL_ID": "chan-2",
            "DISCORD_GAMING_LIBRARY_CHANNEL_ID": "chan-3",
            "DISCORD_SCHEDULING_CHANNEL_ID": "chan-4",
            "DISCORD_HEALTH_MONITOR_CHANNEL_ID": "chan-5",
        },
    )

    assert len(fake.created_messages) == 5
    assert len(fake.pin_calls) == 5
    for slug in ["step-1", "step-2", "step-3", "step-4", "step-5"]:
        assert slug in saved
        assert saved[slug]["message_id"].startswith("new-")


def test_step3_content_includes_commands_list(monkeypatch, tmp_path):
    """Step 3 pinned message includes both how-it-works and the commands list."""
    content = epm.PINNED_CONTENT["step-3"]
    assert "!addgame" in content
    assert "!add" in content
    assert "!remove" in content
    assert "!unassign" in content
    assert "!rename" in content
    assert "!archive" in content
    assert "permanent gaming library" in content
