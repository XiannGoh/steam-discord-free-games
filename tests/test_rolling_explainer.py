"""Tests for rolling_explainer.py"""

from datetime import date

import pytest

from rolling_explainer import (
    ROLLING_CONTENT,
    ROLLING_EXPLAINER_PREFIX,
    get_rolling_content,
    post_or_edit_rolling_explainer,
)


# ---------------------------------------------------------------------------
# Fake Discord client
# ---------------------------------------------------------------------------

class FakeClient:
    def __init__(self, last_message=None):
        self.last_message = last_message  # dict or None
        self.posted = []   # list of (channel_id, content)
        self.edited = []   # list of (channel_id, message_id, content)

    def get_channel_messages(self, channel_id, *, context, limit=100, before=None, after=None):
        if self.last_message is None:
            return []
        return [self.last_message]

    def post_message(self, channel_id, content, *, context):
        msg_id = f"new-{len(self.posted) + 1}"
        self.posted.append((channel_id, content))
        return {"id": msg_id}

    def edit_message(self, channel_id, message_id, content, *, context):
        self.edited.append((channel_id, message_id, content))
        return {"id": message_id}


# ---------------------------------------------------------------------------
# get_rolling_content
# ---------------------------------------------------------------------------

def test_get_rolling_content_returns_string_for_each_step():
    for slug in ("step-1", "step-2", "step-3"):
        result = get_rolling_content(slug)
        assert isinstance(result, str)
        assert len(result) > 0


def test_get_rolling_content_starts_with_prefix():
    for slug in ("step-1", "step-2", "step-3"):
        result = get_rolling_content(slug)
        assert result.startswith(ROLLING_EXPLAINER_PREFIX)


def test_get_rolling_content_invalid_slug_raises():
    with pytest.raises(KeyError):
        get_rolling_content("step-99")


def test_get_rolling_content_weekly_rotation_produces_variants():
    """Different weeks produce different variants for each step."""
    for slug in ("step-1", "step-2", "step-3"):
        seen = set()
        for week_offset in range(len(ROLLING_CONTENT[slug])):
            # ordinal 0 + week_offset * 7 lands on different weeks
            d = date.fromordinal(week_offset * 7 + 1)
            seen.add(get_rolling_content(slug, _today=d))
        assert len(seen) == len(ROLLING_CONTENT[slug]), f"{slug}: rotation did not produce all variants"


def test_get_rolling_content_stable_within_week():
    """Same ISO week always returns the same variant."""
    # Two dates 3 days apart that share the same week
    d1 = date(2026, 4, 13)  # Monday
    d2 = date(2026, 4, 15)  # Wednesday
    for slug in ("step-1", "step-2", "step-3"):
        assert get_rolling_content(slug, _today=d1) == get_rolling_content(slug, _today=d2)


def test_rolling_content_each_step_has_three_variants():
    for slug in ("step-1", "step-2", "step-3"):
        assert len(ROLLING_CONTENT[slug]) == 3


def test_rolling_content_variants_are_distinct():
    for slug in ("step-1", "step-2", "step-3"):
        variants = ROLLING_CONTENT[slug]
        assert len(set(variants)) == len(variants), f"{slug}: duplicate variants found"


def test_rolling_content_all_variants_contain_channel_slug():
    """Every variant for a step must reference the channel name."""
    slug_to_channel = {
        "step-1": "step-1-vote-on-games-to-test",
        "step-2": "step-2-test-then-vote-to-keep",
        "step-3": "step-3-review-existing-games",
    }
    for slug, channel in slug_to_channel.items():
        for i, variant in enumerate(ROLLING_CONTENT[slug]):
            assert channel in variant, f"{slug} variant {i} missing channel reference"


def test_step1_variants_mention_all_voted_games():
    """Step 1 variants must use 'All voted games' not 'Top picks'."""
    for i, variant in enumerate(ROLLING_CONTENT["step-1"]):
        assert "Top picks" not in variant, f"step-1 variant {i} uses obsolete 'Top picks' language"
        assert "voted" in variant.lower(), f"step-1 variant {i} missing 'voted' language"


def test_step3_variants_include_commands():
    """All Step 3 variants must include key bot commands."""
    for i, variant in enumerate(ROLLING_CONTENT["step-3"]):
        for cmd in ("!addgame", "!add", "!remove"):
            assert cmd in variant, f"step-3 variant {i} missing command {cmd!r}"


# ---------------------------------------------------------------------------
# post_or_edit_rolling_explainer
# ---------------------------------------------------------------------------

def test_posts_new_when_channel_empty():
    client = FakeClient(last_message=None)
    post_or_edit_rolling_explainer(client, "chan-1", "step-1")
    assert len(client.posted) == 1
    assert len(client.edited) == 0
    channel_id, content = client.posted[0]
    assert channel_id == "chan-1"
    assert content.startswith(ROLLING_EXPLAINER_PREFIX)


def test_posts_new_when_last_message_not_explainer():
    client = FakeClient(last_message={"id": "msg-99", "content": "Some other message"})
    post_or_edit_rolling_explainer(client, "chan-1", "step-1")
    assert len(client.posted) == 1
    assert len(client.edited) == 0


def test_edits_in_place_when_last_message_is_explainer():
    existing_content = f"{ROLLING_EXPLAINER_PREFIX} — old content here"
    client = FakeClient(last_message={"id": "msg-42", "content": existing_content})
    post_or_edit_rolling_explainer(client, "chan-1", "step-1")
    assert len(client.posted) == 0
    assert len(client.edited) == 1
    channel_id, message_id, content = client.edited[0]
    assert channel_id == "chan-1"
    assert message_id == "msg-42"
    assert content.startswith(ROLLING_EXPLAINER_PREFIX)


def test_no_duplicate_on_same_day_rerun():
    """Running twice with an existing explainer as last message edits, never re-posts."""
    existing_content = f"{ROLLING_EXPLAINER_PREFIX} — #step-2 content"
    client = FakeClient(last_message={"id": "msg-10", "content": existing_content})
    post_or_edit_rolling_explainer(client, "chan-2", "step-2")
    post_or_edit_rolling_explainer(client, "chan-2", "step-2")
    assert len(client.posted) == 0
    assert len(client.edited) == 2  # two edits, zero posts


def test_step2_posted_to_correct_channel():
    client = FakeClient(last_message=None)
    post_or_edit_rolling_explainer(client, "winners-chan", "step-2")
    assert client.posted[0][0] == "winners-chan"


def test_step3_posted_to_correct_channel():
    client = FakeClient(last_message=None)
    post_or_edit_rolling_explainer(client, "library-chan", "step-3")
    assert client.posted[0][0] == "library-chan"
