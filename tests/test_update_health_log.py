"""Tests for scripts/update_health_log.py"""

import json
from datetime import datetime, timedelta, timezone

import pytest

from scripts import update_health_log as uhl


def _utc_iso(dt: datetime) -> str:
    return dt.replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _write_log(tmp_path, data: dict) -> None:
    path = tmp_path / "health_monitor_log.json"
    path.write_text(json.dumps(data), encoding="utf-8")
    return path


@pytest.fixture(autouse=True)
def patch_log_path(tmp_path, monkeypatch):
    """Redirect HEALTH_LOG_FILE to a temp dir for every test."""
    fake_path = tmp_path / "health_monitor_log.json"
    monkeypatch.setattr(uhl, "HEALTH_LOG_FILE", fake_path)
    return fake_path


def test_failure_logged_correctly(patch_log_path):
    """log_failure writes a correct record to the log file."""
    result = uhl.log_failure(
        workflow="Steam Free Games",
        issue_type="discord_verification_failed",
        issue_description="Footer missing",
        run_id="12345",
    )

    assert "failure_id" in result
    assert result["recurrence_count"] == 0
    assert result["is_recurring"] is False

    data = json.loads(patch_log_path.read_text())
    assert len(data["failures"]) == 1
    record = data["failures"][0]
    assert record["workflow"] == "Steam Free Games"
    assert record["issue_type"] == "discord_verification_failed"
    assert record["issue_description"] == "Footer missing"
    assert record["fix_outcome"] is None


def test_recurrence_detected_after_two_same_issue_in_7_days(patch_log_path):
    """After 2 occurrences of the same issue in 7 days, is_recurring becomes True."""
    now = datetime.now(timezone.utc)

    # Pre-seed one failure 3 days ago
    existing = {
        "failures": [
            {
                "id": "aaa111",
                "timestamp": _utc_iso(now - timedelta(days=3)),
                "workflow": "Steam Free Games",
                "issue_type": "discord_verification_failed",
                "issue_description": "Footer missing",
                "is_recurring": False,
                "lifetime_count": 0,
            }
        ],
        "patterns": [],
        "last_updated": "",
    }
    patch_log_path.write_text(json.dumps(existing), encoding="utf-8")

    # Second occurrence
    result = uhl.log_failure(
        workflow="Steam Free Games",
        issue_type="discord_verification_failed",
        issue_description="Footer missing again",
        run_id="99999",
    )

    assert result["is_recurring"] is True
    assert result["recurrence_count"] == 1  # 1 prior in window


def test_recurrence_not_triggered_for_old_failure(patch_log_path):
    """Failures older than 7 days do not count toward recurrence."""
    now = datetime.now(timezone.utc)

    existing = {
        "failures": [
            {
                "id": "bbb222",
                "timestamp": _utc_iso(now - timedelta(days=10)),
                "workflow": "Steam Free Games",
                "issue_type": "discord_verification_failed",
                "issue_description": "Old failure",
                "is_recurring": False,
                "lifetime_count": 0,
            }
        ],
        "patterns": [],
        "last_updated": "",
    }
    patch_log_path.write_text(json.dumps(existing), encoding="utf-8")

    result = uhl.log_failure(
        workflow="Steam Free Games",
        issue_type="discord_verification_failed",
        issue_description="New failure",
        run_id="11111",
    )

    assert result["is_recurring"] is False
    assert result["recurrence_count"] == 0


def test_update_outcome_marks_success(patch_log_path):
    """update_outcome sets fix_outcome and resolved_at on success."""
    # First log a failure
    result = uhl.log_failure(
        workflow="Gaming Library Daily Reminder",
        issue_type="gaming_library_verification_failed",
        issue_description="Missing footer",
        run_id="55555",
    )
    fid = result["failure_id"]

    uhl.update_outcome(
        failure_id=fid,
        outcome="success",
        branch="fix/auto-fix-55555-1",
        attempt=1,
        pr_number="231",
    )

    data = json.loads(patch_log_path.read_text())
    record = next(f for f in data["failures"] if f["id"] == fid)
    assert record["fix_outcome"] == "success"
    assert record["fix_branch"] == "fix/auto-fix-55555-1"
    assert record["resolved_at"] is not None
    assert record["pr_number"] == "231"


def test_update_outcome_failed_does_not_set_resolved_at(patch_log_path):
    """update_outcome with outcome=failed does NOT set resolved_at."""
    result = uhl.log_failure(
        workflow="Steam Free Games",
        issue_type="discord_verification_failed",
        issue_description="Footer issue",
        run_id="77777",
    )
    fid = result["failure_id"]

    uhl.update_outcome(
        failure_id=fid,
        outcome="failed",
        branch="fix/auto-fix-77777-1",
        attempt=1,
    )

    data = json.loads(patch_log_path.read_text())
    record = next(f for f in data["failures"] if f["id"] == fid)
    assert record["fix_outcome"] == "failed"
    assert record["resolved_at"] is None


def test_daily_summary_counts_today_failures(patch_log_path):
    """get_summary returns correct failure and fix counts for the last 24h."""
    now = datetime.now(timezone.utc)
    existing = {
        "failures": [
            {
                "id": "t1",
                "timestamp": _utc_iso(now - timedelta(hours=2)),
                "workflow": "Steam Free Games",
                "issue_type": "discord_verification_failed",
                "issue_description": "Footer missing",
                "fix_outcome": "success",
                "is_recurring": False,
            },
            {
                "id": "t2",
                "timestamp": _utc_iso(now - timedelta(hours=5)),
                "workflow": "Evening Winners",
                "issue_type": "discord_verification_failed",
                "issue_description": "Missing intro",
                "fix_outcome": None,
                "is_recurring": False,
            },
            {
                "id": "t3",
                "timestamp": _utc_iso(now - timedelta(hours=30)),  # yesterday
                "workflow": "Steam Free Games",
                "issue_type": "workflow_execution_failed",
                "issue_description": "Old failure",
                "fix_outcome": None,
                "is_recurring": False,
            },
        ],
        "patterns": [{"id": "p1"}, {"id": "p2"}],
        "last_updated": "",
    }
    patch_log_path.write_text(json.dumps(existing), encoding="utf-8")

    summary = uhl.get_summary()
    assert summary["failures_today"] == 2
    assert summary["auto_fixes_today"] == 1
    assert summary["patterns_flagged"] == 2


def test_log_file_created_if_missing(patch_log_path):
    """log_failure creates the file from scratch if it does not exist (autouse already points to a fresh path)."""
    # patch_log_path is a fresh empty path per-test (set by the autouse fixture)
    assert not patch_log_path.exists()

    uhl.log_failure(
        workflow="Weekly Scheduling Bot",
        issue_type="workflow_execution_failed",
        issue_description="crashed",
        run_id="00001",
    )

    assert patch_log_path.exists()
    data = json.loads(patch_log_path.read_text())
    assert len(data["failures"]) == 1
