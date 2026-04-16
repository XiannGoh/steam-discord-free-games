"""Tests for the updated detect_broken_if in scripts/verify_discord_output.py."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.verify_discord_output import detect_broken_if


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

    def test_command_reference_not_pinned_triggered(self) -> None:
        ch = _empty_result(command_reference_not_pinned=True)
        result = detect_broken_if(["command reference not pinned"], ch)
        assert result["command reference not pinned"] == "triggered"

    def test_day_entries_missing_dates_triggered(self) -> None:
        ch = _empty_result(day_entries_missing_dates=True)
        result = detect_broken_if(["day entries missing dates"], ch)
        assert result["day entries missing dates"] == "triggered"

    def test_missing_members_not_mentioned_triggered(self) -> None:
        ch = _empty_result(missing_members_not_mentioned=True)
        result = detect_broken_if(["missing members not @mentioned"], ch)
        assert result["missing members not @mentioned"] == "triggered"

    def test_current_week_not_pinned_triggered(self) -> None:
        ch = _empty_result(current_week_not_pinned=True)
        result = detect_broken_if(["current week post not pinned"], ch)
        assert result["current week post not pinned"] == "triggered"

    def test_previous_week_still_pinned_triggered(self) -> None:
        ch = _empty_result(previous_week_still_pinned=True)
        result = detect_broken_if(["previous week still pinned when new week exists"], ch)
        assert result["previous week still pinned when new week exists"] == "triggered"

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
            "command reference not pinned",
            "day entries missing dates",
            "missing members not @mentioned",
            "current week post not pinned",
            "previous week still pinned when new week exists",
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
