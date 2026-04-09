import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

from scripts import build_daily_health_report as report


def _write_json(path: Path, payload):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload), encoding="utf-8")


def _configure_paths(monkeypatch, root: Path):
    monkeypatch.setattr(
        report,
        "WEEKLY_PATHS",
        {
            "messages": root / "data/scheduling/weekly_schedule_messages.json",
            "responses": root / "data/scheduling/weekly_schedule_responses.json",
            "summary": root / "data/scheduling/weekly_schedule_summary.json",
            "outputs": root / "data/scheduling/weekly_schedule_bot_outputs.json",
            "roster": root / "data/scheduling/expected_schedule_roster.json",
        },
    )
    monkeypatch.setattr(report, "DAILY_POSTS_PATH", root / "discord_daily_posts.json")


def _seed_healthy_state(root: Path, *, now_utc: datetime):
    week = "2026-04-13_to_2026-04-19"
    _write_json(root / "data/scheduling/weekly_schedule_messages.json", {week: {"intro_message_id": "123"}})
    _write_json(root / "data/scheduling/weekly_schedule_responses.json", {week: {"users": {"1": {"days": {}}}}})
    _write_json(root / "data/scheduling/weekly_schedule_summary.json", {week: {"summary": {"day_counts": {}}}})
    _write_json(
        root / "data/scheduling/weekly_schedule_bot_outputs.json",
        {
            week: {
                "summary_message_id": "123",
                "summary_message_content": "hello",
                "summary_data_signature": "sig",
            }
        },
    )
    _write_json(root / "data/scheduling/expected_schedule_roster.json", {"users": {"1": {"is_active": True}}})

    today = now_utc.date().isoformat()
    winners_day = (now_utc.date() - timedelta(days=1)).isoformat()
    _write_json(
        root / "discord_daily_posts.json",
        {
            today: {"items": []},
            winners_day: {
                "items": [{"title": "Game", "message_id": "1", "channel_id": "1"}],
                "winners_state": {
                    "message_id": "22",
                    "winner_keys": ["abc"],
                    "winner_vote_counts": {"abc": 2},
                },
            },
        },
    )


def test_healthy_state_has_all_clear_message(tmp_path, monkeypatch):
    now_utc = datetime(2026, 4, 10, 3, 10, tzinfo=timezone.utc)
    _configure_paths(monkeypatch, tmp_path)
    _seed_healthy_state(tmp_path, now_utc=now_utc)

    issues = report.compute_state_issues(now_utc=now_utc)
    assert issues == []

    rendered = report.render_report(
        workflow_status_lines=["🟢 Daily Steam Picks", "Last run: success (1h ago)", ""],
        state_issues=issues,
        report_date="Apr 9, 2026",
    )
    assert "🟢 No state inconsistencies detected" in rendered
    assert "🔴" not in rendered


def test_stale_workflow_is_warning_yellow():
    run = {
        "updated_at": (datetime.now(timezone.utc) - timedelta(hours=40)).isoformat(),
        "conclusion": "success",
        "html_url": "https://example.test/run/1",
    }
    icon, status, include_run = report.evaluate_workflow_status(run, stale_hours=30)
    assert icon == "🟡"
    assert "stale" in status
    assert include_run is True


def test_missing_summary_and_winners_state_reported_once(tmp_path, monkeypatch):
    now_utc = datetime(2026, 4, 10, 3, 10, tzinfo=timezone.utc)
    _configure_paths(monkeypatch, tmp_path)

    week = "2026-04-13_to_2026-04-19"
    _write_json(tmp_path / "data/scheduling/weekly_schedule_messages.json", {week: {"intro_message_id": "123"}})
    _write_json(tmp_path / "data/scheduling/weekly_schedule_responses.json", {week: {"users": {"1": {}}}})
    _write_json(tmp_path / "data/scheduling/weekly_schedule_summary.json", {})
    _write_json(tmp_path / "data/scheduling/weekly_schedule_bot_outputs.json", {week: {}})
    _write_json(tmp_path / "data/scheduling/expected_schedule_roster.json", {"users": {"1": {"is_active": True}}})

    today = now_utc.date().isoformat()
    winners_day = (now_utc.date() - timedelta(days=1)).isoformat()
    _write_json(
        tmp_path / "discord_daily_posts.json",
        {
            today: {"items": []},
            winners_day: {"items": [{"title": "Game", "message_id": "1", "channel_id": "1"}]},
        },
    )

    issues = report.compute_state_issues(now_utc=now_utc)
    messages = [issue.message for issue in issues]

    assert any("no weekly summary exists" in message for message in messages)
    assert sum("no weekly summary exists" in message for message in messages) == 1
    assert any("winners state is missing" in message for message in messages)


def test_malformed_roster_is_red_issue(tmp_path, monkeypatch):
    now_utc = datetime(2026, 4, 10, 3, 10, tzinfo=timezone.utc)
    _configure_paths(monkeypatch, tmp_path)
    _seed_healthy_state(tmp_path, now_utc=now_utc)
    _write_json(tmp_path / "data/scheduling/expected_schedule_roster.json", {"users": []})

    issues = report.compute_state_issues(now_utc=now_utc)
    roster_issue = next(issue for issue in issues if "roster" in issue.message.lower())
    assert roster_issue.severity == "error"


def test_signal_light_formatting_uses_expected_icons():
    rendered = report.render_report(
        workflow_status_lines=["🟡 Evening Winners", "Last run: success (36h ago) — stale", ""],
        state_issues=[
            report.Issue(code="warn", severity="warning", message="State warning"),
            report.Issue(code="err", severity="error", message="State error"),
        ],
        report_date="Apr 10, 2026",
    )

    assert "🟡 Evening Winners" in rendered
    assert "🔴 State error" in rendered
    assert "🟡 State warning" in rendered


def test_final_report_snapshot_shape_with_mixed_signals():
    workflow_lines = [
        "🟢 Daily Steam Picks",
        "Last run: success (2h ago)",
        "",
        "🟡 Evening Winners",
        "Last run: success (40h ago) — stale",
        "Run: https://example.test/run/42",
        "",
        "🟢 Weekly Schedule Summary",
        "Last run: success (4h ago)",
        "",
    ]
    state_issues = [
        report.Issue(code="state_warning", severity="warning", message="Daily picks entry missing expected field"),
        report.Issue(code="state_error", severity="error", message="Winners state message id is missing"),
    ]

    rendered = report.render_report(
        workflow_status_lines=workflow_lines,
        state_issues=state_issues,
        report_date="Apr 9, 2026",
    )

    expected = (
        "🚦 XiannGPT Bot Daily Health — Apr 9, 2026\n"
        "\n"
        "## Workflow Status\n"
        "\n"
        "🟢 Daily Steam Picks\n"
        "Last run: success (2h ago)\n"
        "\n"
        "🟡 Evening Winners\n"
        "Last run: success (40h ago) — stale\n"
        "Run: https://example.test/run/42\n"
        "\n"
        "🟢 Weekly Schedule Summary\n"
        "Last run: success (4h ago)\n"
        "\n"
        "🔴 Bot Data Health Check (consolidated)\n"
        "Last run: issues found (see State / Artifact Health)\n"
        "\n"
        "## State / Artifact Health\n"
        "\n"
        "🔴 Winners state message id is missing\n"
        "🟡 Daily picks entry missing expected field"
    )
    assert rendered == expected
    assert "\n\n## State / Artifact Health\n" in rendered
    assert "\n\n\n## State / Artifact Health\n" not in rendered

    assert "## Workflow Status" in rendered
    assert "## State / Artifact Health" in rendered
    assert "🔴 Bot Data Health Check (consolidated)" in rendered
    assert "🟢 Daily Steam Picks" in rendered
    assert "🟡 Evening Winners" in rendered
    assert "🔴 Winners state message id is missing" in rendered


def test_report_date_new_york_format_is_portable_and_unpadded_day():
    now_utc = datetime(2026, 4, 9, 16, 0, tzinfo=timezone.utc)
    assert report._report_date_new_york(now_utc) == "Apr 9, 2026"
