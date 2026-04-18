"""Tests for scripts/ensure_pinned_messages.py"""

import json

import pytest

from scripts import ensure_pinned_messages as epm
from discord_api import DiscordMessageNotFoundError, DiscordPermissionError


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
    assert content == epm.get_pinned_content("step-1")
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
    assert fake.edits[0][2] == epm.get_pinned_content("step-2")
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


def test_step3_all_variants_include_commands_list():
    """All Step 3 rolling variants include both how-it-works and the commands list."""
    for variant in epm.ROLLING_CONTENT["step-3"]:
        assert "!addgame" in variant
        assert "!add" in variant
        assert "!remove" in variant
        assert "!unassign" in variant
        assert "!rename" in variant
        assert "!archive" in variant


class FakeClientWithPermError(FakeClient):
    """FakeClient whose pin_message raises DiscordPermissionError."""

    def pin_message(self, channel_id, message_id, *, context):
        raise DiscordPermissionError(f"403 Forbidden pinning {message_id}")


def test_pin_permission_error_warns_and_continues(monkeypatch, tmp_path, capsys):
    """When pin_message raises DiscordPermissionError, the script warns and does not crash."""
    saved, fake = _run(
        monkeypatch, tmp_path,
        channel_envs={"DISCORD_STEP1_CHANNEL_ID": "chan-step1"},
        fake_client=FakeClientWithPermError(),
    )

    # Message was still created despite the pin failure
    assert len(fake.created_messages) == 1
    assert "step-1" in saved

    captured = capsys.readouterr()
    assert "WARN" in captured.out
    assert "Manage Messages" in captured.out


def test_pin_permission_error_does_not_prevent_other_channels(monkeypatch, tmp_path, capsys):
    """A permission error on one channel does not prevent other channels from being processed."""
    saved, fake = _run(
        monkeypatch, tmp_path,
        channel_envs={
            "DISCORD_STEP1_CHANNEL_ID": "chan-step1",
            "DISCORD_WINNERS_CHANNEL_ID": "chan-step2",
        },
        fake_client=FakeClientWithPermError(),
    )

    # Both channels should have messages created
    assert len(fake.created_messages) == 2
    assert "step-1" in saved
    assert "step-2" in saved


# ---------------------------------------------------------------------------
# Rolling explainer message tests
# ---------------------------------------------------------------------------

def test_rolling_content_steps_123_have_multiple_variants():
    """Steps 1, 2, and 3 each define at least 2 rolling variants."""
    for slug in ("step-1", "step-2", "step-3"):
        assert slug in epm.ROLLING_CONTENT
        assert len(epm.ROLLING_CONTENT[slug]) >= 2, f"{slug} must have at least 2 variants"


def test_rolling_content_steps_45_are_not_rolling():
    """Steps 4 and 5 are not in ROLLING_CONTENT — they use static PINNED_CONTENT."""
    assert "step-4" not in epm.ROLLING_CONTENT
    assert "step-5" not in epm.ROLLING_CONTENT
    assert "step-4" in epm.PINNED_CONTENT
    assert "step-5" in epm.PINNED_CONTENT


def test_get_pinned_content_returns_string_for_all_slugs():
    """get_pinned_content returns a non-empty string for every configured slug."""
    for slug in epm.CHANNEL_ENV_MAP:
        content = epm.get_pinned_content(slug)
        assert isinstance(content, str)
        assert len(content) > 0


def test_get_pinned_content_steps_45_match_pinned_content():
    """Steps 4 and 5 always return their static PINNED_CONTENT string."""
    assert epm.get_pinned_content("step-4") == epm.PINNED_CONTENT["step-4"]
    assert epm.get_pinned_content("step-5") == epm.PINNED_CONTENT["step-5"]


def test_rolling_variants_are_distinct():
    """Each rolling step has distinct variant texts (no accidental duplicates)."""
    for slug in ("step-1", "step-2", "step-3"):
        variants = epm.ROLLING_CONTENT[slug]
        assert len(set(variants)) == len(variants), f"{slug} has duplicate variants"


def test_all_rolling_variants_include_channel_slug(monkeypatch):
    """Every rolling variant references its own channel name so users know where they are."""
    channel_names = {
        "step-1": "step-1-vote-on-games-to-test",
        "step-2": "step-2-test-then-vote-to-keep",
        "step-3": "step-3-review-existing-games",
    }
    for slug, channel_name in channel_names.items():
        for i, variant in enumerate(epm.ROLLING_CONTENT[slug]):
            assert channel_name in variant, (
                f"{slug} variant {i} does not mention #{channel_name}"
            )


def test_get_pinned_content_rotates_by_week(monkeypatch):
    """get_pinned_content returns a different variant when the week changes."""
    import datetime

    n_variants = len(epm.ROLLING_CONTENT["step-1"])
    # Find two weeks that would yield different variant indices
    base_ordinal = datetime.date(2026, 1, 5).toordinal()  # week 0 of 2026
    results = set()
    for offset in range(n_variants * 7):
        day = datetime.date.fromordinal(base_ordinal + offset)
        monkeypatch.setattr(datetime, "date", type("_D", (), {
            "today": staticmethod(lambda d=day: d),
            "fromordinal": staticmethod(datetime.date.fromordinal),
        }))
        results.add(epm.get_pinned_content("step-1"))

    assert len(results) > 1, "expected at least 2 distinct variants across weeks"
