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
                "summary_last_synced_at_utc": now_utc.isoformat(),
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
                "items": [{"title": "Game", "message_id": "1", "channel_id": "1", "posted_at": now_utc.isoformat()}],
                "winners_state": {
                    "message_id": "22",
                    "winner_keys": ["abc"],
                    "winner_vote_counts": {"abc": 2},
                    "updated_at_utc": now_utc.isoformat(),
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


def test_stale_workflow_includes_triage_metadata():
    run = {
        "updated_at": "2026-01-01T00:10:00+00:00",
        "created_at": "2026-01-01T00:10:00+00:00",
        "conclusion": "success",
        "event": "schedule",
        "html_url": "https://example.test/run/1",
    }
    lines = report.build_workflow_status_lines(
        [{"name": "Weekly Scheduling Responses Sync", "staleHours": 2, "run": run}]
    )
    rendered = "\n".join(lines)

    assert "🔴 Weekly Scheduling Responses Sync" in rendered
    assert "Expected freshness: ≤2h" in rendered
    assert "Trigger: schedule" in rendered
    assert "Last run time:" in rendered
    assert "Run: https://example.test/run/1" in rendered
    assert "Disposition: Action required" in rendered
    assert "Next step: Re-run Weekly Scheduling Responses Sync" in rendered


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
    codes = [issue.code for issue in issues]

    assert "weekly.summary_missing" in codes
    assert codes.count("weekly.summary_missing") == 1
    assert "winners.state_missing" in codes


def test_malformed_roster_is_red_issue(tmp_path, monkeypatch):
    now_utc = datetime(2026, 4, 10, 3, 10, tzinfo=timezone.utc)
    _configure_paths(monkeypatch, tmp_path)
    _seed_healthy_state(tmp_path, now_utc=now_utc)
    _write_json(tmp_path / "data/scheduling/expected_schedule_roster.json", {"users": []})

    issues = report.compute_state_issues(now_utc=now_utc)
    roster_issue = next(issue for issue in issues if "roster" in issue.code)
    assert roster_issue.severity == "error"


def test_signal_light_formatting_uses_expected_icons_and_state_metadata():
    rendered = report.render_report(
        workflow_status_lines=["🟡 Evening Winners", "Last run: success (36h ago) — stale", ""],
        state_issues=[
            report.Issue(
                code="state.warning",
                severity="warning",
                title="Daily picks warning",
                file_path="discord_daily_posts.json",
                day_key="2026-04-09",
                context="Missing expected field",
            ),
            report.Issue(
                code="state.error",
                severity="error",
                title="Winners state error",
                file_path="discord_daily_posts.json",
                day_key="2026-04-09",
                context="Message id missing",
            ),
        ],
        report_date="Apr 10, 2026",
    )

    assert "🟡 Evening Winners" in rendered
    assert "🔴 Winners state error" in rendered
    assert "🟡 Daily picks warning" in rendered
    assert "Code: state.error" in rendered
    assert "File: discord_daily_posts.json" in rendered
    assert "Disposition: Action required" in rendered
    assert "Next step: Inspect discord_daily_posts.json" in rendered


def test_final_report_snapshot_shape_with_mixed_signals_and_triage_metadata():
    workflow_lines = [
        "🟢 Daily Steam Picks",
        "Last run: success (2h ago)",
        "",
        "🔴 Evening Winners",
        "Last run: success (40h ago) — stale",
        "Expected freshness: ≤30h",
        "Trigger: schedule",
        "Last run time: Apr 9, 8:10 PM ET",
        "Run: https://example.test/run/42",
        "",
        "🔴 Weekly Scheduling Responses Sync",
        "Last run: failure (1h ago)",
        "Expected freshness: ≤6h",
        "Trigger: schedule",
        "Last run time: Apr 9, 11:10 PM ET",
        "Run: https://example.test/run/43",
        "",
    ]
    state_issues = [
        report.Issue(
            code="weekly.summary_missing",
            severity="warning",
            title="Weekly summary missing",
            week_key="2026-04-13_to_2026-04-19",
            file_path="data/scheduling/weekly_schedule_summary.json",
            context="Weekly responses exist but summary entry is absent.",
        ),
        report.Issue(
            code="winners.message_id_missing",
            severity="error",
            title="Winners state inconsistent",
            day_key="2026-04-09",
            file_path="discord_daily_posts.json",
            context="message_id is missing from winners state.",
        ),
    ]

    rendered = report.render_report(
        workflow_status_lines=workflow_lines,
        state_issues=state_issues,
        report_date="Apr 9, 2026",
    )

    assert "## Workflow Status" in rendered
    assert "🟢 Daily Steam Picks" in rendered
    assert "🔴 Evening Winners" in rendered
    assert "🔴 Weekly Scheduling Responses Sync" in rendered
    assert "Expected freshness: ≤30h" in rendered
    assert "Trigger: schedule" in rendered
    assert "Run: https://example.test/run/42" in rendered
    assert "Disposition: Action required" in rendered
    assert "Disposition: Action required" in rendered

    assert "## State / Artifact Health" in rendered
    assert "🔴 Winners state inconsistent" in rendered
    assert "Code: winners.message_id_missing" in rendered
    assert "🟡 Weekly summary missing" in rendered
    assert "Week: 2026-04-13_to_2026-04-19" in rendered
    assert "Context: Weekly responses exist but summary entry is absent." in rendered
    assert "Next step: Re-run the related workflow" in rendered


def test_health_report_cleanup_removes_state_sanity_check_references_from_workflow_monitoring():
    workflow_file = Path(".github/workflows/bot-health-report.yml").read_text(encoding="utf-8")

    assert "state-sanity-check.yml" not in workflow_file
    assert "State Sanity Check" not in workflow_file

    rendered = report.render_report(
        workflow_status_lines=[
            "🟢 Weekly Scheduling Bot",
            "Last run: success (4h ago)",
            "",
            "🟢 Weekly Scheduling Responses Sync",
            "Last run: success (1h ago)",
            "",
            "🟢 Daily Steam Picks",
            "Last run: success (6h ago)",
            "",
            "🟢 Evening Winners",
            "Last run: success (8h ago)",
            "",
        ],
        state_issues=[],
        report_date="Apr 9, 2026",
    )

    assert "## Workflow Status" in rendered
    assert "## State / Artifact Health" in rendered
    assert "🟢 No state inconsistencies detected" in rendered
    assert "State Sanity Check" not in rendered


def test_report_date_new_york_format_is_portable_and_unpadded_day():
    now_utc = datetime(2026, 4, 9, 16, 0, tzinfo=timezone.utc)
    assert report._report_date_new_york(now_utc) == "Apr 9, 2026"


def test_weekly_and_winners_freshness_checks_are_high_signal_without_duplicates(tmp_path, monkeypatch):
    now_utc = datetime(2026, 4, 10, 3, 10, tzinfo=timezone.utc)
    _configure_paths(monkeypatch, tmp_path)

    week = "2026-04-13_to_2026-04-19"
    _write_json(tmp_path / "data/scheduling/weekly_schedule_messages.json", {week: {"intro_message_id": "123"}})
    _write_json(tmp_path / "data/scheduling/weekly_schedule_responses.json", {week: {"users": {"1": {"days": {}}}}})
    _write_json(tmp_path / "data/scheduling/weekly_schedule_summary.json", {week: {"summary": {"day_counts": {}}}})
    _write_json(
        tmp_path / "data/scheduling/weekly_schedule_bot_outputs.json",
        {week: {"summary_message_id": "sum-1", "summary_message_content": "summary", "summary_data_signature": "sig"}},
    )
    _write_json(tmp_path / "data/scheduling/expected_schedule_roster.json", {"users": {"1": {"is_active": True}}})

    today = now_utc.date().isoformat()
    winners_day = (now_utc.date() - timedelta(days=1)).isoformat()
    _write_json(
        tmp_path / "discord_daily_posts.json",
        {
            today: {"items": [{"posted_at": "2026-04-10T01:00:00+00:00"}]},
            winners_day: {
                "items": [{"posted_at": "2026-04-10T01:05:00+00:00"}],
                "winners_state": {
                    "message_id": "w1",
                    "winner_keys": ["a"],
                    "updated_at_utc": "2026-04-09T00:00:00+00:00",
                },
            },
        },
    )

    issues = report.compute_state_issues(now_utc=now_utc)
    codes = [issue.code for issue in issues]

    assert "weekly.summary_freshness_missing" in codes
    assert "weekly.summary_freshness_missing" in codes and codes.count("weekly.summary_freshness_missing") == 1
    assert "winners.stale_vs_picks" in codes
    assert "winners.freshness_missing" not in codes


def test_benign_warning_renders_no_action_needed_guidance():
    rendered = "\n".join(
        report._render_state_issue(
            report.Issue(
                code="weekly.summary_freshness_missing",
                severity="warning",
                title="Weekly summary freshness fields missing",
                context="Summary exists but outputs are missing summary_last_synced_at_utc.",
                file_path="data/scheduling/weekly_schedule_bot_outputs.json",
                week_key="2026-04-13_to_2026-04-19",
                disposition="No action needed",
                next_step="None. This is usually legacy output missing summary_last_synced_at_utc; monitor for future writes.",
            )
        )
    )
    assert "Disposition: No action needed" in rendered
    assert "Next step: None." in rendered


def test_monitor_only_case_for_daily_today_missing(tmp_path, monkeypatch):
    now_utc = datetime(2026, 4, 10, 3, 10, tzinfo=timezone.utc)
    _configure_paths(monkeypatch, tmp_path)
    _seed_healthy_state(tmp_path, now_utc=now_utc)
    _write_json(tmp_path / "discord_daily_posts.json", {})

    issues = report.compute_state_issues(now_utc=now_utc)
    today_issue = next(issue for issue in issues if issue.code == "daily.today_missing")
    assert today_issue.disposition == "Monitor only"
    assert "Wait for the next daily-picks run" in (today_issue.next_step or "")


def test_actionable_warning_has_specific_next_step(tmp_path, monkeypatch):
    now_utc = datetime(2026, 4, 10, 3, 10, tzinfo=timezone.utc)
    _configure_paths(monkeypatch, tmp_path)

    week = "2026-03-30_to_2026-04-05"
    _write_json(tmp_path / "data/scheduling/weekly_schedule_messages.json", {week: {"intro_message_id": "123"}})
    _write_json(tmp_path / "data/scheduling/weekly_schedule_responses.json", {})
    _write_json(tmp_path / "data/scheduling/weekly_schedule_summary.json", {})
    _write_json(tmp_path / "data/scheduling/weekly_schedule_bot_outputs.json", {})
    _write_json(tmp_path / "data/scheduling/expected_schedule_roster.json", {"users": {"1": {"is_active": True}}})
    _write_json(tmp_path / "discord_daily_posts.json", {})

    issues = report.compute_state_issues(now_utc=now_utc)
    weekly_issue = next(issue for issue in issues if issue.code == "weekly.expected_post_missing")
    assert weekly_issue.disposition == "Action recommended"
    assert "Re-run weekly-scheduling-bot.yml" in (weekly_issue.next_step or "")


def test_error_rendering_uses_action_required():
    lines = report._render_state_issue(
        report.Issue(
            code="winners.keys_malformed",
            severity="error",
            title="Winners state inconsistent",
            context="winner_keys field is malformed.",
            file_path="discord_daily_posts.json",
            day_key="2026-04-09",
            disposition="Action required",
            next_step="Inspect discord_daily_posts.json and fix malformed winners_state fields.",
        )
    )
    rendered = "\n".join(lines)
    assert "Disposition: Action required" in rendered
    assert "Next step: Inspect discord_daily_posts.json" in rendered


def test_green_workflow_output_has_no_guidance_lines():
    run = {
        "updated_at": datetime.now(timezone.utc).isoformat(),
        "created_at": datetime.now(timezone.utc).isoformat(),
        "conclusion": "success",
    }
    lines = report.build_workflow_status_lines([{"name": "Daily Steam Picks", "staleHours": 6, "run": run}])
    rendered = "\n".join(lines)
    assert "Disposition:" not in rendered
    assert "Next step:" not in rendered


def test_overall_summary_is_green_when_only_no_action_needed_warnings():
    rendered = report.render_report(
        workflow_status_lines=["🟢 Daily Steam Picks", "Last run: success (1h ago)", ""],
        state_issues=[
            report.Issue(
                code="weekly.summary_freshness_missing",
                severity="warning",
                title="Weekly summary freshness fields missing",
                context="Summary exists but outputs are missing summary_last_synced_at_utc.",
                disposition="No action needed",
                next_step="None.",
            )
        ],
        report_date="Apr 9, 2026",
    )

    assert "🟢 Overall: Healthy with informational warnings" in rendered
    assert "1 low-priority warning detected. No action needed." in rendered


def test_overall_summary_is_red_when_stale_workflow_is_action_required():
    rendered = report.render_report(
        workflow_status_lines=[
            "🔴 Evening Winners",
            "Last run: success (40h ago) — stale",
            "Disposition: Action required",
            "Next step: Re-run Evening Winners if no run is expected soon; otherwise monitor the next scheduled run.",
            "",
        ],
        state_issues=[
            report.Issue(
                code="weekly.expected_post_missing",
                severity="warning",
                title="Expected weekly schedule post missing",
                context="No current/next expected weekly schedule message entry found.",
                disposition="Action recommended",
                next_step="Re-run weekly-scheduling-bot.yml.",
            )
        ],
        report_date="Apr 9, 2026",
    )

    assert "🔴 Overall: Action needed" in rendered
    assert "1 item requires immediate attention." in rendered


def test_overall_summary_is_red_when_action_required_or_error_exists():
    rendered = report.render_report(
        workflow_status_lines=[
            "🔴 Weekly Scheduling Responses Sync",
            "Last run: failure (1h ago)",
            "Disposition: Action required",
            "Next step: Fix workflow failure.",
            "",
        ],
        state_issues=[
            report.Issue(
                code="winners.message_id_missing",
                severity="error",
                title="Winners state inconsistent",
                context="message_id is missing from winners state.",
            )
        ],
        report_date="Apr 9, 2026",
    )

    assert "🔴 Overall: Action needed" in rendered
    assert "items require immediate attention." in rendered


def test_overall_summary_is_green_when_fully_healthy():
    rendered = report.render_report(
        workflow_status_lines=["🟢 Daily Steam Picks", "Last run: success (1h ago)", ""],
        state_issues=[],
        report_date="Apr 9, 2026",
    )

    assert "🟢 Overall: Healthy" in rendered
    assert "No action needed." in rendered
