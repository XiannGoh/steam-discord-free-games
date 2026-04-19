"""Tests for the updated detect_broken_if in scripts/verify_discord_output.py."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.verify_discord_output import (
    detect_broken_if,
    verify_step1,
    verify_step2,
    verify_step3,
    get_spec_required,
)
from discord_api import DiscordMessageNotFoundError


def _empty_result(**overrides) -> dict:
    base = {
        "messages_checked": 1,
        "errors": [],
        "intro_found": True,
        "footer_found": True,
    }
    base.update(overrides)
    return base


class TestDetectBrokenIfNewConditions:
    """Verify new broken_if conditions added in Issue #214 are correctly detected."""

    def test_footer_missing_separator_triggered(self) -> None:
        ch = _empty_result(footer_missing_separator=True)
        result = detect_broken_if(["footer is missing end separator"], ch)
        assert result["footer is missing end separator"] == "triggered"

    def test_footer_missing_separator_not_triggered(self) -> None:
        ch = _empty_result(footer_missing_separator=False)
        result = detect_broken_if(["footer is missing end separator"], ch)
        assert result["footer is missing end separator"] == "not_triggered"

    def test_footer_is_copy_of_intro_triggered(self) -> None:
        ch = _empty_result(footer_is_copy_of_intro=True)
        result = detect_broken_if(["footer is a copy of the intro"], ch)
        assert result["footer is a copy of the intro"] == "triggered"

    def test_footer_is_copy_of_intro_step2_variant(self) -> None:
        ch = _empty_result(footer_is_copy_of_intro=True)
        result = detect_broken_if(["footer is copy of intro"], ch)
        assert result["footer is copy of intro"] == "triggered"

    def test_intro_contains_game_content_triggered(self) -> None:
        ch = _empty_result(intro_contains_game_content=True)
        result = detect_broken_if(["intro contains game content"], ch)
        assert result["intro contains game content"] == "triggered"

    def test_delta_missing_from_intro_triggered(self) -> None:
        ch = _empty_result(delta_missing_from_intro=True)
        result = detect_broken_if(["delta summary missing from intro"], ch)
        assert result["delta summary missing from intro"] == "triggered"

    def test_delta_posted_separately_triggered(self) -> None:
        ch = _empty_result(delta_posted_separately=True)
        result = detect_broken_if(["delta summary posted as separate message instead of inside intro"], ch)
        assert result["delta summary posted as separate message instead of inside intro"] == "triggered"

    def test_game_card_missing_activity_date_triggered(self) -> None:
        ch = _empty_result(game_card_missing_activity_date=True)
        result = detect_broken_if(["game card missing last activity date"], ch)
        assert result["game card missing last activity date"] == "triggered"

    def test_day_entries_missing_dates_triggered(self) -> None:
        ch = _empty_result(day_entries_missing_dates=True)
        result = detect_broken_if(["day entries missing dates"], ch)
        assert result["day entries missing dates"] == "triggered"

    def test_missing_members_not_mentioned_triggered(self) -> None:
        ch = _empty_result(missing_members_not_mentioned=True)
        result = detect_broken_if(["missing members not @mentioned"], ch)
        assert result["missing members not @mentioned"] == "triggered"

    def test_delta_posted_when_nothing_changed_triggered(self) -> None:
        ch = _empty_result(delta_posted_when_nothing_changed=True)
        result = detect_broken_if(["delta posted when nothing changed"], ch)
        assert result["delta posted when nothing changed"] == "triggered"

    def test_failure_report_missing_attempt_count_triggered(self) -> None:
        ch = _empty_result(failure_report_missing_attempt_count=True)
        result = detect_broken_if(["failure report missing attempt count"], ch)
        assert result["failure report missing attempt count"] == "triggered"

    def test_failure_report_missing_occurrence_count_triggered(self) -> None:
        ch = _empty_result(failure_report_missing_occurrence_count=True)
        result = detect_broken_if(["failure report missing previous occurrence count"], ch)
        assert result["failure report missing previous occurrence count"] == "triggered"

    def test_no_daily_summary_triggered(self) -> None:
        ch = _empty_result(no_daily_summary=True)
        result = detect_broken_if(["no daily summary posted"], ch)
        assert result["no daily summary posted"] == "triggered"

    def test_new_messages_on_rerun_triggered(self) -> None:
        ch = _empty_result(new_messages_on_rerun=True)
        result = detect_broken_if(["second run posted new messages instead of editing existing ones"], ch)
        assert result["second run posted new messages instead of editing existing ones"] == "triggered"

    def test_demo_playtest_stale_game_triggered(self) -> None:
        ch = _empty_result(demo_playtest_stale_game=True)
        result = detect_broken_if(["demo_playtest contains game older than 180 days"], ch)
        assert result["demo_playtest contains game older than 180 days"] == "triggered"

    def test_section_content_in_intro_triggered(self) -> None:
        ch = _empty_result(section_content_in_intro=True)
        result = detect_broken_if(["section content bleeding into intro message"], ch)
        assert result["section content bleeding into intro message"] == "triggered"

    def test_all_new_conditions_not_triggered_by_default(self) -> None:
        """Clean result dict → none of the new conditions should trigger."""
        ch = _empty_result()
        new_conditions = [
            "footer is missing end separator",
            "footer is a copy of the intro",
            "intro contains game content",
            "delta summary missing from intro",
            "delta summary posted as separate message instead of inside intro",
            "game card missing last activity date",
            "day entries missing dates",
            "missing members not @mentioned",
            "delta posted when nothing changed",
            "failure report missing attempt count",
            "failure report missing previous occurrence count",
            "no daily summary posted",
            "second run posted new messages instead of editing existing ones",
            "demo_playtest contains game older than 180 days",
            "section content bleeding into intro message",
        ]
        detected = detect_broken_if(new_conditions, ch)
        for cond, state in detected.items():
            assert state == "not_triggered", f"Expected not_triggered for '{cond}', got {state}"

    def test_existing_conditions_still_work(self) -> None:
        """Regression: original broken_if conditions still work correctly."""
        # no games posted
        ch_empty = _empty_result(messages_checked=0)
        result = detect_broken_if(["no games posted"], ch_empty)
        assert result["no games posted"] == "triggered"

        # missing intro
        ch_no_intro = _empty_result(intro_found=False)
        result = detect_broken_if(["missing intro"], ch_no_intro)
        assert result["missing intro"] == "triggered"

        # duplicate game messages
        ch_dupe = _empty_result(errors=["Duplicate message_id 111 for item 'Game'"])
        result = detect_broken_if(["duplicate game messages"], ch_dupe)
        assert result["duplicate game messages"] == "triggered"


# ---------------------------------------------------------------------------
# Functional tests for verify_step1, verify_step2, verify_step3 new checks
# ---------------------------------------------------------------------------

class _FakeVerifyClient:
    """Minimal fake Discord client for verify_discord_output functional tests."""

    def __init__(self, messages: dict = None, *, missing_ids: set = None, last_message_content: str = "📌 How This Works — fake"):
        self._messages = messages or {}
        self._missing_ids = missing_ids or set()
        self._last_message_content = last_message_content

    def get_message(self, channel_id, message_id, *, context=""):
        if message_id in self._missing_ids:
            raise DiscordMessageNotFoundError(f"missing: {message_id}")
        if message_id in self._messages:
            return self._messages[message_id]
        return {"id": message_id, "content": "", "reactions": []}

    def get_channel_messages(self, channel_id, *, context="", limit=100, before=None, after=None):
        if not self._last_message_content:
            return []
        return [{"id": "last-msg", "content": self._last_message_content}]


def _step1_entry(intro_content="📅 Daily Picks\n─────", footer_content="📅 ⬆️ Top\n─────────────────── End of Daily Picks ───────────────────"):
    return {
        "run_state": {
            "intro": {"message_id": "intro-1", "channel_id": "chan-1"},
            "section_headers": {},
            "footer": {"message_id": "footer-1", "channel_id": "chan-1"},
        },
        "items": [],
    }, {
        "intro-1": {"id": "intro-1", "content": intro_content, "reactions": []},
        "footer-1": {"id": "footer-1", "content": footer_content, "reactions": []},
    }


_SPEC_REQUIRED = {
    "intro_required": True,
    "footer_required": True,
    "min_items": 0,
    "no_duplicates": True,
    "reactions": [],
}


class TestVerifyStep1NewChecks:
    def test_footer_missing_separator_when_wrong_text(self) -> None:
        """verify_step1 sets footer_missing_separator=True when footer ends with wrong text."""
        day_entry, msgs = _step1_entry(footer_content="📅 ⬆️ Top\n─── Wrong ───")
        client = _FakeVerifyClient(msgs)
        result = verify_step1(client, day_entry, _SPEC_REQUIRED, "2026-04-15")
        assert result.get("footer_missing_separator") is True

    def test_footer_correct_separator(self) -> None:
        """verify_step1 sets footer_missing_separator=False when footer ends with correct separator."""
        day_entry, msgs = _step1_entry(
            footer_content="📅 ⬆️ Top\n─────────────────── End of Daily Picks ───────────────────"
        )
        client = _FakeVerifyClient(msgs)
        result = verify_step1(client, day_entry, _SPEC_REQUIRED, "2026-04-15")
        assert result.get("footer_missing_separator") is False

    def test_intro_steam_url_sets_section_content_in_intro(self) -> None:
        """verify_step1 sets section_content_in_intro=True when intro contains Steam URL."""
        day_entry, msgs = _step1_entry(
            intro_content="📅 Daily\nhttps://store.steampowered.com/app/123/\n─────"
        )
        msgs["intro-1"]["content"] = "📅 Daily\nhttps://store.steampowered.com/app/123/\n─────"
        client = _FakeVerifyClient(msgs)
        result = verify_step1(client, day_entry, _SPEC_REQUIRED, "2026-04-15")
        assert result.get("section_content_in_intro") is True

    def test_intro_no_steam_url(self) -> None:
        """verify_step1 sets section_content_in_intro=False when intro has no Steam URL."""
        day_entry, msgs = _step1_entry(intro_content="📅 Daily Picks\nLoading...\n─────")
        client = _FakeVerifyClient(msgs)
        result = verify_step1(client, day_entry, _SPEC_REQUIRED, "2026-04-15")
        assert result.get("section_content_in_intro") is False


class TestVerifyStep2NewChecks:
    def _step2_entry(self, intro_content="📅 Winners\n─────", footer_content="📅 ⬆️ Top\n─────────────────── End of Daily Winners ───────────────────"):
        return {
            "winners_state": {
                "intro": {"message_id": "intro-2", "channel_id": "chan-2"},
                "section_headers": {},
                "footer": {"message_id": "footer-2", "channel_id": "chan-2"},
                "winner_messages": {},
            }
        }, {
            "intro-2": {"id": "intro-2", "content": intro_content, "reactions": []},
            "footer-2": {"id": "footer-2", "content": footer_content, "reactions": []},
        }

    def test_footer_missing_separator(self) -> None:
        """verify_step2 sets footer_missing_separator=True when footer ends with wrong text."""
        day_entry, msgs = self._step2_entry(footer_content="📅 ⬆️ Top\n─── Wrong ───")
        client = _FakeVerifyClient(msgs)
        result = verify_step2(client, day_entry, _SPEC_REQUIRED, "2026-04-15")
        assert result.get("footer_missing_separator") is True

    def test_footer_correct_separator(self) -> None:
        """verify_step2 sets footer_missing_separator=False when footer ends correctly."""
        day_entry, msgs = self._step2_entry(
            footer_content="📅 ⬆️ Top\n─────────────────── End of Daily Winners ───────────────────"
        )
        client = _FakeVerifyClient(msgs)
        result = verify_step2(client, day_entry, _SPEC_REQUIRED, "2026-04-15")
        assert result.get("footer_missing_separator") is False


class TestVerifyStep3:
    def _gl_state(self, intro_content="📚 Gaming Library\n─────\n📊 Today's Changes\n- Game added\n─────", footer_content="📅 ⬆️ Top\n─────────────────── End of Gaming Library ───────────────────", games=None, day_key="2026-04-15"):
        return {
            "games": games if games is not None else {"g1": {}, "g2": {}},
            "daily_posts": {
                day_key: {
                    "messages": {
                        "header": {"message_id": "intro-3", "channel_id": "chan-3"},
                        "footer": {"message_id": "footer-3", "channel_id": "chan-3"},
                    },
                    "completed": True,
                }
            },
        }, {
            "intro-3": {"id": "intro-3", "content": intro_content, "reactions": []},
            "footer-3": {"id": "footer-3", "content": footer_content, "reactions": []},
        }

    def test_pass_when_intro_footer_correct(self) -> None:
        """verify_step3 passes when intro has delta and footer has correct separator."""
        gl_state, msgs = self._gl_state()
        client = _FakeVerifyClient(msgs)
        result = verify_step3(client, gl_state, {}, "2026-04-15")
        assert result["pass"] is True
        assert result["intro_found"] is True
        assert result["footer_found"] is True
        assert result["delta_missing_from_intro"] is False
        assert result["footer_missing_separator"] is False

    def test_delta_missing_from_intro(self) -> None:
        """verify_step3 sets delta_missing_from_intro=True when intro has no delta content."""
        gl_state, msgs = self._gl_state(intro_content="📚 Gaming Library\n─────")
        client = _FakeVerifyClient(msgs)
        result = verify_step3(client, gl_state, {}, "2026-04-15")
        assert result["delta_missing_from_intro"] is True
        assert result["pass"] is False

    def test_no_changes_since_yesterday_satisfies_delta(self) -> None:
        """'No changes since yesterday' in intro satisfies delta_missing_from_intro=False."""
        gl_state, msgs = self._gl_state(intro_content="📚 Gaming Library\n─────\nNo changes since yesterday\n─────")
        client = _FakeVerifyClient(msgs)
        result = verify_step3(client, gl_state, {}, "2026-04-15")
        assert result["delta_missing_from_intro"] is False

    def test_footer_missing_separator(self) -> None:
        """verify_step3 sets footer_missing_separator=True when footer ends with wrong text."""
        gl_state, msgs = self._gl_state(footer_content="📅 ⬆️ Top\n─── Wrong ───")
        client = _FakeVerifyClient(msgs)
        result = verify_step3(client, gl_state, {}, "2026-04-15")
        assert result["footer_missing_separator"] is True
        assert result["pass"] is False

    def test_item_count_from_games(self) -> None:
        """verify_step3 reports item_count equal to number of games in library."""
        gl_state, msgs = self._gl_state(games={"a": {}, "b": {}, "c": {}})
        client = _FakeVerifyClient(msgs)
        result = verify_step3(client, gl_state, {}, "2026-04-15")
        assert result["item_count"] == 3

    def test_min_items_failure(self) -> None:
        """verify_step3 fails when item_count < min_items spec."""
        gl_state, msgs = self._gl_state(games={})
        client = _FakeVerifyClient(msgs)
        specs = {"step-3-review-existing-games": {"required": {"min_items": 5}}}
        result = verify_step3(client, gl_state, specs, "2026-04-15")
        assert result["item_count"] == 0
        assert result["pass"] is False

    def test_skipped_when_no_day_entry(self) -> None:
        """verify_step3 is skipped (pass=True, checked=False) when no entry for the day."""
        gl_state = {"games": {}, "daily_posts": {}}
        client = _FakeVerifyClient()
        result = verify_step3(client, gl_state, {}, "2026-04-15")
        assert result["checked"] is False
        assert result["pass"] is True
