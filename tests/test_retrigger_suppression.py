"""Tests for re-trigger suppression (Issue #215).

Covers the Rule 1/5 pre-skip gates added to:
  - state_utils.is_today_verified()
  - main.post_daily_pick_messages() — Step 1
  - evening_winners.main() — Step 2
  - gaming_library.run_daily_post() — Step 3
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from state_utils import is_today_verified


# ---------------------------------------------------------------------------
# is_today_verified
# ---------------------------------------------------------------------------

class TestIsTodayVerified:
    def test_pass_true_same_day(self, tmp_path: Path) -> None:
        vf = tmp_path / "discord_verification.json"
        vf.write_text(json.dumps({"date": "2026-04-15", "pass": True}), encoding="utf-8")
        assert is_today_verified("2026-04-15", verification_file=str(vf)) is True

    def test_pass_false_same_day(self, tmp_path: Path) -> None:
        vf = tmp_path / "discord_verification.json"
        vf.write_text(json.dumps({"date": "2026-04-15", "pass": False}), encoding="utf-8")
        assert is_today_verified("2026-04-15", verification_file=str(vf)) is False

    def test_wrong_date_returns_false(self, tmp_path: Path) -> None:
        vf = tmp_path / "discord_verification.json"
        vf.write_text(json.dumps({"date": "2026-04-14", "pass": True}), encoding="utf-8")
        assert is_today_verified("2026-04-15", verification_file=str(vf)) is False

    def test_missing_file_returns_false(self, tmp_path: Path) -> None:
        vf = tmp_path / "nonexistent.json"
        assert is_today_verified("2026-04-15", verification_file=str(vf)) is False

    def test_invalid_json_returns_false(self, tmp_path: Path) -> None:
        vf = tmp_path / "discord_verification.json"
        vf.write_text("NOT JSON", encoding="utf-8")
        assert is_today_verified("2026-04-15", verification_file=str(vf)) is False

    def test_empty_file_returns_false(self, tmp_path: Path) -> None:
        vf = tmp_path / "discord_verification.json"
        vf.write_text("{}", encoding="utf-8")
        assert is_today_verified("2026-04-15", verification_file=str(vf)) is False


# ---------------------------------------------------------------------------
# Step 1 — post_daily_pick_messages() suppression
# ---------------------------------------------------------------------------

class TestStep1RetriggerSuppression:
    """Verify that post_daily_pick_messages() exits cleanly when completed+verified.

    post_daily_pick_messages() loads daily_posts internally via load_discord_daily_posts()
    (only when DISCORD_BOT_TOKEN is set). We mock both that function and is_today_verified.
    """

    def _make_daily_posts(self, day_key: str, *, completed: bool) -> dict:
        return {day_key: {"run_state": {"completed": completed, "section_headers": {}}}}

    def test_completed_and_verified_suppresses_rerun(self) -> None:
        from main import post_daily_pick_messages

        day_key = "2026-04-15"
        daily_posts = self._make_daily_posts(day_key, completed=True)

        with patch("main.is_today_verified", return_value=True), \
             patch("main.load_discord_daily_posts", return_value=daily_posts), \
             patch("main.get_target_day_key", return_value=day_key), \
             patch("main.DISCORD_BOT_TOKEN", "fake_token"), \
             patch("main.save_discord_daily_posts"):
            run_counts, skipped, _ = post_daily_pick_messages(
                demo_playtest_items=[{"title": "Demo A", "url": "u", "score": 1, "section": "demo_playtest"}],
                free_items=[],
                paid_items=[],
                instagram_posts=[],
                force_refresh_same_day=True,  # would normally trigger refresh
                manual_run=True,              # would normally trigger refresh
            )

        assert skipped is True

    def test_completed_but_not_verified_uses_existing_skip_gate(self) -> None:
        """If verified=False and completed=True, existing gate still skips (not force_refresh)."""
        from main import post_daily_pick_messages

        day_key = "2026-04-15"
        daily_posts = self._make_daily_posts(day_key, completed=True)

        with patch("main.is_today_verified", return_value=False), \
             patch("main.load_discord_daily_posts", return_value=daily_posts), \
             patch("main.get_target_day_key", return_value=day_key), \
             patch("main.DISCORD_BOT_TOKEN", "fake_token"), \
             patch("main.save_discord_daily_posts"):
            run_counts, skipped, _ = post_daily_pick_messages(
                demo_playtest_items=[{"title": "Demo A", "url": "u", "score": 1, "section": "demo_playtest"}],
                free_items=[],
                paid_items=[],
                instagram_posts=[],
                force_refresh_same_day=False,
                manual_run=False,
            )

        # Original completed + not force_refresh gate fires
        assert skipped is True

    def test_not_completed_runs_normally(self) -> None:
        """If completed=False, verified check must not suppress — run proceeds past skip gates."""
        from main import post_daily_pick_messages

        day_key = "2026-04-15"
        daily_posts = self._make_daily_posts(day_key, completed=False)

        # Patch post_to_discord_with_metadata to avoid needing WEBHOOK_URL
        mock_meta = {"id": "msg1", "channel_id": "ch1"}
        with patch("main.is_today_verified", return_value=True), \
             patch("main.load_discord_daily_posts", return_value=daily_posts), \
             patch("main.get_target_day_key", return_value=day_key), \
             patch("main.DISCORD_BOT_TOKEN", None), \
             patch("main.post_to_discord_with_metadata", return_value=mock_meta), \
             patch("main.save_discord_daily_posts"):
            run_counts, skipped, _ = post_daily_pick_messages(
                demo_playtest_items=[{"title": "Demo A", "url": "u", "score": 1, "section": "demo_playtest"}],
                free_items=[],
                paid_items=[],
                instagram_posts=[],
                force_refresh_same_day=False,
                manual_run=False,
            )

        assert skipped is False

    def test_manual_run_without_force_refresh_sets_completed_so_next_run_skips(self) -> None:
        """workflow_dispatch without force_refresh must set completed=True (Issue #292).

        A plain workflow_dispatch (e.g. triggered by auto-fix bot) must NOT keep
        the day open. Only workflow_dispatch + force_refresh_same_day=True should
        leave completed=False so the next scheduled run re-runs from scratch.

        After Fix 1, this verifies that:
        - Run 1: manual_run=True, force_refresh=False → completed=True is set
        - Run 2: manual_run=False, force_refresh=False → skipped (idempotency gate fires)
        """
        from main import post_daily_pick_messages

        day_key = "2026-04-18"
        daily_posts = self._make_daily_posts(day_key, completed=False)
        mock_meta = {"message_id": "msg1", "channel_id": "ch1"}

        # Run 1: workflow_dispatch (manual), no force_refresh.
        # DISCORD_BOT_TOKEN must be truthy so load_discord_daily_posts() is called and
        # returns our mutable daily_posts dict, allowing us to inspect the in-memory state.
        with patch("main.is_today_verified", return_value=False), \
             patch("main.load_discord_daily_posts", return_value=daily_posts), \
             patch("main.get_target_day_key", return_value=day_key), \
             patch("main.DISCORD_BOT_TOKEN", "fake_token"), \
             patch("main.DiscordClient"), \
             patch("main.post_to_discord_with_metadata", return_value=mock_meta), \
             patch("main.sleep_briefly"), \
             patch("main.save_discord_daily_posts"):
            _, skipped1, _ = post_daily_pick_messages(
                demo_playtest_items=[{"title": "Demo A", "url": "u", "score": 1, "section": "demo_playtest"}],
                free_items=[],
                paid_items=[],
                instagram_posts=[],
                force_refresh_same_day=False,
                manual_run=True,
            )

        assert skipped1 is False
        assert daily_posts[day_key]["run_state"].get("completed") is True, (
            "manual_run without force_refresh must set completed=True"
        )

        # Run 2: scheduled cron — must be suppressed because completed=True
        with patch("main.is_today_verified", return_value=False), \
             patch("main.load_discord_daily_posts", return_value=daily_posts), \
             patch("main.get_target_day_key", return_value=day_key), \
             patch("main.DISCORD_BOT_TOKEN", "fake_token"), \
             patch("main.save_discord_daily_posts"):
            _, skipped2, _ = post_daily_pick_messages(
                demo_playtest_items=[{"title": "Demo A", "url": "u", "score": 1, "section": "demo_playtest"}],
                free_items=[],
                paid_items=[],
                instagram_posts=[],
                force_refresh_same_day=False,
                manual_run=False,
            )

        assert skipped2 is True, "Scheduled run after completed manual run must be suppressed"

    def test_manual_run_with_force_refresh_does_not_set_completed(self) -> None:
        """workflow_dispatch + force_refresh_same_day=True must NOT set completed=True.

        This allows the next scheduled run to re-execute, which is the intended
        behaviour for explicit test reruns.
        """
        from main import post_daily_pick_messages

        day_key = "2026-04-18"
        daily_posts = self._make_daily_posts(day_key, completed=False)
        mock_meta = {"message_id": "msg1", "channel_id": "ch1"}

        with patch("main.is_today_verified", return_value=False), \
             patch("main.load_discord_daily_posts", return_value=daily_posts), \
             patch("main.get_target_day_key", return_value=day_key), \
             patch("main.DISCORD_BOT_TOKEN", "fake_token"), \
             patch("main.DiscordClient"), \
             patch("main.post_to_discord_with_metadata", return_value=mock_meta), \
             patch("main.sleep_briefly"), \
             patch("main.save_discord_daily_posts"):
            _, skipped, _ = post_daily_pick_messages(
                demo_playtest_items=[{"title": "Demo A", "url": "u", "score": 1, "section": "demo_playtest"}],
                free_items=[],
                paid_items=[],
                instagram_posts=[],
                force_refresh_same_day=True,
                manual_run=True,
            )

        assert skipped is False
        assert daily_posts[day_key]["run_state"].get("completed") is not True, (
            "manual_run + force_refresh must NOT set completed=True"
        )


# ---------------------------------------------------------------------------
# Step 3 — run_daily_post() suppression
# ---------------------------------------------------------------------------

class TestStep3RetriggerSuppression:
    """Verify that run_daily_post() exits cleanly when completed+verified."""

    def _make_state(self, day_key: str, *, completed: bool) -> dict:
        return {
            "games": {},
            "daily_posts": {
                day_key: {"completed": completed}
            },
        }

    def test_completed_and_verified_suppresses_rerun(self) -> None:
        from gaming_library import run_daily_post

        day_key = "2026-04-15"
        state = self._make_state(day_key, completed=True)

        with patch("gaming_library.load_gaming_library", return_value=state), \
             patch("gaming_library.get_target_day_key", return_value=day_key), \
             patch("gaming_library.DISCORD_GAMING_LIBRARY_CHANNEL_ID", "ch123"), \
             patch("gaming_library.DISCORD_BOT_TOKEN", "tok"), \
             patch("gaming_library.is_today_verified", return_value=True), \
             patch("gaming_library.save_gaming_library") as mock_save:

            result = run_daily_post()

        assert result is False
        mock_save.assert_not_called()

    def test_completed_but_not_verified_continues(self) -> None:
        """completed=True but verified=False → should NOT suppress (allow reconcile)."""
        from gaming_library import run_daily_post

        day_key = "2026-04-15"
        state = self._make_state(day_key, completed=True)
        state["previous_day_games"] = {}

        with patch("gaming_library.load_gaming_library", return_value=state), \
             patch("gaming_library.get_target_day_key", return_value=day_key), \
             patch("gaming_library.DISCORD_GAMING_LIBRARY_CHANNEL_ID", "ch123"), \
             patch("gaming_library.DISCORD_BOT_TOKEN", "tok"), \
             patch("gaming_library.is_today_verified", return_value=False), \
             patch("gaming_library.is_manual_run", return_value=False), \
             patch("gaming_library.DISCORD_GUILD_ID", None), \
             patch("gaming_library.post_daily_library_reminder", return_value=True) as mock_post, \
             patch("gaming_library.save_gaming_library"), \
             patch("requests.Session"):

            result = run_daily_post()

        # Should have reached post_daily_library_reminder, not returned early
        mock_post.assert_called_once()

    def test_not_completed_does_not_suppress(self) -> None:
        from gaming_library import run_daily_post

        day_key = "2026-04-15"
        state = self._make_state(day_key, completed=False)

        with patch("gaming_library.load_gaming_library", return_value=state), \
             patch("gaming_library.get_target_day_key", return_value=day_key), \
             patch("gaming_library.DISCORD_GAMING_LIBRARY_CHANNEL_ID", "ch123"), \
             patch("gaming_library.DISCORD_BOT_TOKEN", "tok"), \
             patch("gaming_library.is_today_verified", return_value=True), \
             patch("gaming_library.is_manual_run", return_value=False), \
             patch("gaming_library.DISCORD_GUILD_ID", None), \
             patch("gaming_library.post_daily_library_reminder", return_value=True) as mock_post, \
             patch("gaming_library.save_gaming_library"), \
             patch("requests.Session"):

            result = run_daily_post()

        mock_post.assert_called_once()
