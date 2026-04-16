"""Build a unified once-daily bot health report for Discord."""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from state_utils import format_et_timestamp as _format_et_timestamp_util  # noqa: E402
WEEKLY_PATHS = {
    "messages": ROOT / "data/scheduling/weekly_schedule_messages.json",
    "responses": ROOT / "data/scheduling/weekly_schedule_responses.json",
    "summary": ROOT / "data/scheduling/weekly_schedule_summary.json",
    "outputs": ROOT / "data/scheduling/weekly_schedule_bot_outputs.json",
    "roster": ROOT / "data/scheduling/expected_schedule_roster.json",
}
DAILY_POSTS_PATH = ROOT / "discord_daily_posts.json"
INSTAGRAM_FETCH_SUMMARY_PATH = ROOT / "instagram_fetch_summary.json"
INSTAGRAM_TOTAL_CREATORS = 8

NEW_YORK_TZ = ZoneInfo("America/New_York")
SCHEDULE_EXPECTATIONS: dict[str, dict[str, Any]] = {
    "Weekly Scheduling Bot": {
        "kind": "weekly",
        "cron": "0 13 * * 6",
        "cadence": "weekly Saturday 13:00 UTC",
        "hour": 13,
        "minute": 0,
        "weekday": 5,
        "window_before_minutes": 90,
    },
    "Weekly Scheduling Responses Sync": {
        "kind": "interval",
        "cron": "0 */3 * * *",
        "cadence": "every 3 hours (UTC)",
        "interval_hours": 3,
        "minute": 0,
        "window_before_minutes": 90,
    },
    "Daily Steam Picks": {
        "kind": "daily",
        "cron": "0 13 * * *",
        "cadence": "daily 13:00 UTC",
        "hour": 13,
        "minute": 0,
        "window_before_minutes": 90,
    },
    "Evening Winners": {
        "kind": "daily",
        "cron": "0 23 * * *",
        "cadence": "daily 23:00 UTC",
        "hour": 23,
        "minute": 0,
        "window_before_minutes": 90,
    },
    "Gaming Library Daily Reminder": {
        "kind": "daily",
        "cron": "0 14 * * *",
        "cadence": "daily 14:00 UTC",
        "hour": 14,
        "minute": 0,
        "window_before_minutes": 90,
    },
    "Gaming Library Sync": {
        "kind": "interval",
        "cron": "15 * * * *",
        "cadence": "every hour at :15 UTC",
        "interval_hours": 1,
        "minute": 15,
        "window_before_minutes": 30,
    },
    "Missed-Run Watchdog": {
        "kind": "interval",
        "cron": "0 * * * *",
        "cadence": "every hour",
        "interval_hours": 1,
        "minute": 0,
        "window_before_minutes": 90,
    },
}


@dataclass(frozen=True)
class Issue:
    code: str
    severity: str  # "warning" or "error"
    title: str
    context: str
    file_path: str | None = None
    week_key: str | None = None
    day_key: str | None = None
    disposition: str | None = None
    next_step: str | None = None
    extra: dict[str, str] = field(default_factory=dict)


@dataclass(frozen=True)
class OverallHealthSummary:
    icon: str
    headline: str
    detail: str


@dataclass(frozen=True)
class WorkflowScheduleDiagnostics:
    code: str
    message: str
    expected_cadence: str
    expected_schedule_time_utc: datetime | None
    expected_window_start_utc: datetime | None
    found_scheduled_run_in_window: bool
    latest_run_is_manual_recovery: bool
    latest_run_event: str | None
    latest_run_created_at: datetime | None
    latest_run_updated_at: datetime | None
    latest_run_id: int | None
    latest_run_conclusion: str | None
    scheduled_run_in_window_id: int | None
    scheduled_run_in_window_created_at: datetime | None


def _load_json(path: Path) -> Any | None:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def _format_age(hours: float) -> str:
    if hours < 1:
        return "<1h ago"
    if hours < 48:
        return f"{round(hours)}h ago"
    return f"{round(hours / 24)}d ago"


def _parse_iso_utc(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value.strip():
        return None
    text = value.strip().replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _format_ny_timestamp(value: Any) -> str | None:
    return _format_et_timestamp_util(value)


def evaluate_workflow_status(
    run: dict[str, Any] | None,
    stale_hours: int,
    *,
    now_utc: datetime | None = None,
) -> tuple[str, str, bool, str]:
    if not run:
        return "🟡", "no recent run found", False, "no_recent_run"

    updated_at = run.get("updated_at") or run.get("created_at")
    if not isinstance(updated_at, str):
        return "🟡", "run timestamp missing", True, "timestamp_missing"

    effective_now = now_utc or datetime.now(timezone.utc)
    run_time = datetime.fromisoformat(updated_at.replace("Z", "+00:00"))
    age_hours = (effective_now - run_time).total_seconds() / 3600
    recency = _format_age(age_hours)
    conclusion = str(run.get("conclusion") or run.get("status") or "unknown")

    if age_hours > stale_hours:
        return "🔴", f"{conclusion} ({recency}) — stale", True, "stale"
    if conclusion == "success":
        return "🟢", f"{conclusion} ({recency})", False, "success"
    if conclusion in {"failure", "timed_out", "startup_failure", "action_required"}:
        return "🔴", f"{conclusion} ({recency})", True, "failed"
    return "🟡", f"{conclusion} ({recency})", True, "non_success"


def _latest_expected_schedule_time(now_utc: datetime, expectation: dict[str, Any]) -> datetime | None:
    kind = expectation.get("kind")
    if kind == "daily":
        candidate = now_utc.replace(
            hour=int(expectation["hour"]),
            minute=int(expectation.get("minute", 0)),
            second=0,
            microsecond=0,
        )
        if candidate > now_utc:
            candidate -= timedelta(days=1)
        return candidate
    if kind == "weekly":
        candidate = now_utc.replace(
            hour=int(expectation["hour"]),
            minute=int(expectation.get("minute", 0)),
            second=0,
            microsecond=0,
        )
        target_weekday = int(expectation["weekday"])
        days_back = (candidate.weekday() - target_weekday) % 7
        candidate -= timedelta(days=days_back)
        if candidate > now_utc:
            candidate -= timedelta(days=7)
        return candidate
    if kind == "interval":
        interval_hours = int(expectation["interval_hours"])
        minute = int(expectation.get("minute", 0))
        aligned = now_utc.replace(minute=minute, second=0, microsecond=0)
        aligned = aligned.replace(hour=(aligned.hour // interval_hours) * interval_hours)
        if aligned > now_utc:
            aligned -= timedelta(hours=interval_hours)
        return aligned
    return None


def build_schedule_diagnostics(
    workflow_name: str,
    *,
    latest_run: dict[str, Any] | None,
    recent_runs: list[dict[str, Any]],
    now_utc: datetime,
) -> WorkflowScheduleDiagnostics | None:
    expectation = SCHEDULE_EXPECTATIONS.get(workflow_name)
    if not expectation:
        return None
    expected_time = _latest_expected_schedule_time(now_utc, expectation)
    if expected_time is None:
        return None
    window_start = expected_time - timedelta(minutes=int(expectation.get("window_before_minutes", 60)))

    scheduled_in_window = None
    for run in recent_runs:
        if run.get("event") != "schedule":
            continue
        created_at = _parse_iso_utc(run.get("created_at") or run.get("run_started_at") or run.get("updated_at"))
        if created_at is None:
            continue
        if created_at >= window_start:
            scheduled_in_window = run
            break

    latest_event = latest_run.get("event") if isinstance(latest_run, dict) and isinstance(latest_run.get("event"), str) else None
    found_scheduled = scheduled_in_window is not None
    latest_manual_recovery = bool(latest_event and latest_event != "schedule" and found_scheduled)

    if found_scheduled and latest_event == "schedule":
        code = "workflow.scheduled_window_satisfied"
        message = "Scheduled run found in expected window."
    elif found_scheduled and latest_event != "schedule":
        code = "workflow.latest_manual_run"
        message = "Latest run was manual/non-scheduled; expected scheduled run was still found in window."
    elif latest_event and latest_event != "schedule":
        code = "workflow.expected_scheduled_run_missing"
        message = "Latest run was manual/non-scheduled and no scheduled run was found in the expected window."
    elif latest_event == "schedule":
        code = "workflow.latest_scheduled_but_outside_expected_window"
        message = "Latest run is scheduled but appears older than the most recent expected schedule window."
    else:
        code = "workflow.expected_scheduled_run_missing"
        message = "No scheduled run found in the most recent expected window."

    return WorkflowScheduleDiagnostics(
        code=code,
        message=message,
        expected_cadence=f"{expectation['cadence']} (cron: {expectation['cron']})",
        expected_schedule_time_utc=expected_time,
        expected_window_start_utc=window_start,
        found_scheduled_run_in_window=found_scheduled,
        latest_run_is_manual_recovery=latest_manual_recovery,
        latest_run_event=latest_event,
        latest_run_created_at=_parse_iso_utc(latest_run.get("created_at")) if isinstance(latest_run, dict) else None,
        latest_run_updated_at=_parse_iso_utc(latest_run.get("updated_at")) if isinstance(latest_run, dict) else None,
        latest_run_id=latest_run.get("id") if isinstance(latest_run, dict) and isinstance(latest_run.get("id"), int) else None,
        latest_run_conclusion=str(latest_run.get("conclusion") or latest_run.get("status")) if isinstance(latest_run, dict) else None,
        scheduled_run_in_window_id=scheduled_in_window.get("id")
        if isinstance(scheduled_in_window, dict) and isinstance(scheduled_in_window.get("id"), int)
        else None,
        scheduled_run_in_window_created_at=_parse_iso_utc(scheduled_in_window.get("created_at"))
        if isinstance(scheduled_in_window, dict)
        else None,
    )


def _week_keys(payload: Any) -> set[str]:
    if isinstance(payload, dict):
        return {str(k) for k in payload.keys()}
    return set()


def _expected_winners_day(now_utc: datetime) -> str:
    if now_utc.hour >= 23:
        return now_utc.date().isoformat()
    return (now_utc.date() - timedelta(days=1)).isoformat()


def _to_week_key(monday_date: datetime.date) -> str:
    end = monday_date + timedelta(days=6)
    return f"{monday_date.isoformat()}_to_{end.isoformat()}"


def _current_week_monday(now_utc: datetime) -> datetime.date:
    today = now_utc.date()
    return today - timedelta(days=today.weekday())


def _extract_week_entry(payload: Any, week_key: str) -> dict[str, Any] | None:
    if not isinstance(payload, dict):
        return None
    entry = payload.get(week_key)
    if isinstance(entry, dict):
        return entry
    return None


def _new_issue(
    code: str,
    severity: str,
    title: str,
    context: str,
    *,
    file_path: str | None = None,
    week_key: str | None = None,
    day_key: str | None = None,
    disposition: str | None = None,
    next_step: str | None = None,
    extra: dict[str, str] | None = None,
) -> Issue:
    guidance_disposition, guidance_next_step = _state_issue_guidance(code, severity, file_path=file_path)
    return Issue(
        code=code,
        severity=severity,
        title=title,
        context=context,
        file_path=file_path,
        week_key=week_key,
        day_key=day_key,
        disposition=disposition or guidance_disposition,
        next_step=next_step or guidance_next_step,
        extra=extra or {},
    )


def _state_issue_guidance(code: str, severity: str, *, file_path: str | None = None) -> tuple[str, str]:
    code_map: dict[str, tuple[str, str]] = {
        "weekly.summary_freshness_missing": (
            "No action needed",
            "None. This is usually legacy output missing summary_last_synced_at_utc; monitor for future writes.",
        ),
        "winners.state_missing": (
            "No action needed",
            "None unless the expected winners post is missing in Discord for that day.",
        ),
        "daily.today_missing": (
            "Monitor only",
            "Wait for the next daily-picks run, then confirm today's entry appears in discord_daily_posts.json.",
        ),
        "weekly.expected_post_missing": (
            "Action recommended",
            "Re-run weekly-scheduling-bot.yml to post/repair the expected weekly schedule messages for the current or next week.",
        ),
    }
    if code in code_map:
        return code_map[code]
    if severity == "error":
        target = file_path or "state files"
        return "Action required", f"Inspect {target} and fix malformed/inconsistent fields before the next run."
    return "Action recommended", "Re-run the related workflow and verify state files update as expected."


def _workflow_guidance(
    *,
    status_reason: str,
    icon: str,
    workflow_name: str,
    run: dict[str, Any] | None,
) -> tuple[str, str] | None:
    if icon == "🟢":
        return None
    run_url = run.get("html_url") if isinstance(run, dict) and isinstance(run.get("html_url"), str) else None
    if status_reason == "schedule_missed":
        return (
            "Action recommended",
            f"Verify the scheduled trigger for {workflow_name} is still active; manually trigger if needed.",
        )
    if status_reason == "stale":
        return (
            "Action required",
            f"Re-run {workflow_name} if no run is expected soon; otherwise monitor the next scheduled run.",
        )
    if status_reason in {"no_recent_run", "timestamp_missing"}:
        return (
            "Action recommended",
            f"Trigger {workflow_name} manually and verify run metadata is recorded correctly.",
        )
    if status_reason == "failed":
        url_note = f"Open run details: {run_url}. " if run_url else ""
        return ("Action required", f"{url_note}Fix the failure cause and re-run the workflow.")
    return ("Monitor only", "Watch the next run and intervene only if this status repeats.")


def compute_state_issues(*, now_utc: datetime | None = None) -> list[Issue]:
    now_utc = now_utc or datetime.now(timezone.utc)
    issues: dict[str, Issue] = {}

    weekly_messages = _load_json(WEEKLY_PATHS["messages"])
    weekly_responses = _load_json(WEEKLY_PATHS["responses"])
    weekly_summary = _load_json(WEEKLY_PATHS["summary"])
    weekly_outputs = _load_json(WEEKLY_PATHS["outputs"])
    roster = _load_json(WEEKLY_PATHS["roster"])
    daily_posts = _load_json(DAILY_POSTS_PATH)

    all_week_keys = (
        _week_keys(weekly_messages)
        | _week_keys(weekly_responses)
        | _week_keys(weekly_summary)
        | _week_keys(weekly_outputs)
    )
    current_week_key = sorted(all_week_keys)[-1] if all_week_keys else None

    current_monday = _current_week_monday(now_utc)
    expected_weeks = {
        _to_week_key(current_monday),
        _to_week_key(current_monday + timedelta(days=7)),
    }
    message_keys = _week_keys(weekly_messages)
    if not message_keys.intersection(expected_weeks):
        issues["missing_expected_weekly_post"] = _new_issue(
            code="weekly.expected_post_missing",
            severity="warning",
            title="Expected weekly schedule post missing",
            context="No current/next expected weekly schedule message entry found.",
            file_path="data/scheduling/weekly_schedule_messages.json",
            extra={"Expected weeks": ", ".join(sorted(expected_weeks))},
        )

    if current_week_key:
        messages_entry = _extract_week_entry(weekly_messages, current_week_key)
        responses_entry = _extract_week_entry(weekly_responses, current_week_key)
        summary_entry = _extract_week_entry(weekly_summary, current_week_key)
        outputs_entry = _extract_week_entry(weekly_outputs, current_week_key)

        if not isinstance(messages_entry, dict):
            issues["missing_weekly_messages"] = _new_issue(
                "weekly.messages_missing",
                "warning",
                "Weekly schedule messages missing",
                "Current target week message state is missing.",
                file_path="data/scheduling/weekly_schedule_messages.json",
                week_key=current_week_key,
            )

        has_responses = isinstance(responses_entry, dict) and bool(responses_entry.get("users"))
        if has_responses and not isinstance(summary_entry, dict):
            issues["missing_weekly_summary"] = _new_issue(
                "weekly.summary_missing",
                "warning",
                "Weekly summary missing",
                "Weekly responses exist but summary entry is absent.",
                file_path="data/scheduling/weekly_schedule_summary.json",
                week_key=current_week_key,
            )

        if not isinstance(outputs_entry, dict):
            issues["missing_weekly_outputs"] = _new_issue(
                "weekly.outputs_missing",
                "warning",
                "Weekly schedule outputs missing",
                "Weekly outputs entry is absent for current target week.",
                file_path="data/scheduling/weekly_schedule_bot_outputs.json",
                week_key=current_week_key,
            )
        else:
            has_summary_message_id = isinstance(outputs_entry.get("summary_message_id"), str) and bool(
                outputs_entry.get("summary_message_id")
            )
            has_summary_content = isinstance(outputs_entry.get("summary_message_content"), str) and bool(
                outputs_entry.get("summary_message_content")
            )
            has_signature = isinstance(outputs_entry.get("summary_data_signature"), str) and bool(
                outputs_entry.get("summary_data_signature")
            )
            if len({has_summary_message_id, has_summary_content, has_signature}) > 1:
                issues["inconsistent_summary_fields"] = _new_issue(
                    "weekly.summary_fields_inconsistent",
                    "error",
                    "Weekly summary state inconsistent",
                    "Summary message/signature fields are partially missing.",
                    file_path="data/scheduling/weekly_schedule_bot_outputs.json",
                    week_key=current_week_key,
                )

            if isinstance(summary_entry, dict):
                summary_last_synced = _parse_iso_utc(outputs_entry.get("summary_last_synced_at_utc"))
                if summary_last_synced is None:
                    issues["missing_summary_freshness_fields"] = _new_issue(
                        "weekly.summary_freshness_missing",
                        "warning",
                        "Weekly summary freshness fields missing",
                        "Summary exists but outputs are missing summary_last_synced_at_utc.",
                        file_path="data/scheduling/weekly_schedule_bot_outputs.json",
                        week_key=current_week_key,
                    )
                else:
                    responses_mtime = WEEKLY_PATHS["responses"].stat().st_mtime if WEEKLY_PATHS["responses"].exists() else None
                    outputs_mtime = WEEKLY_PATHS["outputs"].stat().st_mtime if WEEKLY_PATHS["outputs"].exists() else None
                    newest_source_mtime = max(
                        value for value in [responses_mtime, outputs_mtime] if value is not None
                    ) if any(value is not None for value in [responses_mtime, outputs_mtime]) else None
                    if newest_source_mtime is not None:
                        newest_source_time = datetime.fromtimestamp(newest_source_mtime, tz=timezone.utc)
                        if newest_source_time - summary_last_synced > timedelta(minutes=20):
                            issues["stale_weekly_summary"] = _new_issue(
                                "weekly.summary_stale",
                                "warning",
                                "Weekly summary appears stale",
                                "Summary freshness timestamp is behind latest responses/outputs state.",
                                file_path="data/scheduling/weekly_schedule_bot_outputs.json",
                                week_key=current_week_key,
                            )

    users = roster.get("users") if isinstance(roster, dict) else None
    if not isinstance(users, dict):
        issues["malformed_roster"] = _new_issue(
            "roster.malformed",
            "error",
            "Active roster malformed",
            "Expected roster users object is missing or invalid.",
            file_path="data/scheduling/expected_schedule_roster.json",
        )
    elif not users:
        issues["empty_roster"] = _new_issue(
            "roster.empty",
            "error",
            "Active roster empty",
            "No active users are configured.",
            file_path="data/scheduling/expected_schedule_roster.json",
        )

    today_key = now_utc.date().isoformat()
    winners_day = _expected_winners_day(now_utc)
    if not isinstance(daily_posts, dict):
        issues["missing_daily_posts"] = _new_issue(
            "daily.posts_missing",
            "warning",
            "Daily picks state missing",
            "Daily picks JSON is missing or unreadable.",
            file_path="discord_daily_posts.json",
        )
    else:
        today_entry = daily_posts.get(today_key)
        if not isinstance(today_entry, dict):
            issues["missing_today_entry"] = _new_issue(
                "daily.today_missing",
                "warning",
                "Daily picks missing current-day entry",
                "Current day is not present in daily picks state.",
                file_path="discord_daily_posts.json",
                day_key=today_key,
            )

        winners_entry = daily_posts.get(winners_day)
        if isinstance(winners_entry, dict):
            picks = winners_entry.get("items")
            winners_state = winners_entry.get("winners_state")
            has_picks = isinstance(picks, list) and len(picks) > 0
            if has_picks and not isinstance(winners_state, dict):
                issues["missing_winners_state"] = _new_issue(
                    "winners.state_missing",
                    "warning",
                    "Winners state missing",
                    "Daily picks exist but winners state has not been recorded.",
                    file_path="discord_daily_posts.json",
                    day_key=winners_day,
                )
            if isinstance(winners_state, dict):
                message_id = winners_state.get("message_id")
                winner_keys = winners_state.get("winner_keys")
                if not isinstance(message_id, str) or not message_id.strip():
                    issues["missing_winners_message_id"] = _new_issue(
                        "winners.message_id_missing",
                        "error",
                        "Winners state inconsistent",
                        "message_id is missing from winners state.",
                        file_path="discord_daily_posts.json",
                        day_key=winners_day,
                    )
                if not isinstance(winner_keys, list):
                    issues["malformed_winner_keys"] = _new_issue(
                        "winners.keys_malformed",
                        "error",
                        "Winners state inconsistent",
                        "winner_keys field is malformed.",
                        file_path="discord_daily_posts.json",
                        day_key=winners_day,
                    )
                elif not winner_keys:
                    issues["empty_winner_keys"] = _new_issue(
                        "winners.keys_empty",
                        "warning",
                        "Winners state empty",
                        "Winners state exists but winner_keys is empty.",
                        file_path="discord_daily_posts.json",
                        day_key=winners_day,
                    )

                freshness_ts = _parse_iso_utc(
                    winners_state.get("updated_at_utc") or winners_state.get("posted_at_utc")
                )
                if freshness_ts is None and isinstance(message_id, str) and message_id.strip():
                    issues["winners_missing_freshness"] = _new_issue(
                        "winners.freshness_missing",
                        "warning",
                        "Winners freshness marker missing",
                        "Winners output exists but no updated_at_utc/posted_at_utc marker is present.",
                        file_path="discord_daily_posts.json",
                        day_key=winners_day,
                    )

                if isinstance(picks, list) and picks and freshness_ts is not None:
                    latest_pick_posted = max(
                        (
                            _parse_iso_utc(item.get("posted_at"))
                            for item in picks
                            if isinstance(item, dict)
                        ),
                        default=None,
                    )
                    if latest_pick_posted is not None and freshness_ts + timedelta(minutes=5) < latest_pick_posted:
                        issues["stale_winners_vs_picks"] = _new_issue(
                            "winners.stale_vs_picks",
                            "warning",
                            "Winners state appears stale",
                            "Winners freshness timestamp is older than latest daily picks updates.",
                            file_path="discord_daily_posts.json",
                            day_key=winners_day,
                        )

    return list(issues.values())


def _render_state_issue(issue: Issue) -> list[str]:
    icon = "🔴" if issue.severity == "error" else "🟡"
    lines = [f"{icon} {issue.title}", f"Code: {issue.code}"]
    if issue.week_key:
        lines.append(f"Week: {issue.week_key}")
    if issue.day_key:
        lines.append(f"Day: {issue.day_key}")
    if issue.file_path:
        lines.append(f"File: {issue.file_path}")
    for key, value in issue.extra.items():
        lines.append(f"{key}: {value}")
    lines.append(f"Context: {issue.context}")
    disposition = issue.disposition
    next_step = issue.next_step
    if not disposition or not next_step:
        default_disposition, default_next_step = _state_issue_guidance(
            issue.code,
            issue.severity,
            file_path=issue.file_path,
        )
        disposition = disposition or default_disposition
        next_step = next_step or default_next_step
    lines.append(f"Disposition: {disposition}")
    lines.append(f"Next step: {next_step}")
    return lines


def summarize_overall_health(*, workflow_status_lines: list[str], state_issues: list[Issue]) -> OverallHealthSummary:
    dispositions: list[str] = []
    for line in workflow_status_lines:
        if line.startswith("Disposition: "):
            dispositions.append(line.removeprefix("Disposition: ").strip())

    for issue in state_issues:
        disposition = issue.disposition
        if not disposition:
            disposition, _ = _state_issue_guidance(issue.code, issue.severity, file_path=issue.file_path)
        dispositions.append(disposition)

    action_required_count = sum(1 for disposition in dispositions if disposition == "Action required")
    monitor_or_follow_up_count = sum(
        1 for disposition in dispositions if disposition in {"Monitor only", "Action recommended"}
    )
    no_action_needed_count = sum(1 for disposition in dispositions if disposition == "No action needed")
    error_issue_count = sum(1 for issue in state_issues if issue.severity == "error")

    if action_required_count > 0 or error_issue_count > 0:
        urgent_count = max(action_required_count, error_issue_count)
        noun = "item requires" if urgent_count == 1 else "items require"
        return OverallHealthSummary(
            icon="🔴",
            headline="Action needed",
            detail=f"{urgent_count} {noun} immediate attention.",
        )

    if monitor_or_follow_up_count > 0:
        noun = "item should" if monitor_or_follow_up_count == 1 else "items should"
        return OverallHealthSummary(
            icon="🟡",
            headline="Follow-up recommended",
            detail=f"{monitor_or_follow_up_count} {noun} be monitored or reviewed soon.",
        )

    if no_action_needed_count > 0:
        noun = "warning" if no_action_needed_count == 1 else "warnings"
        return OverallHealthSummary(
            icon="🟢",
            headline="Healthy with informational warnings",
            detail=f"{no_action_needed_count} low-priority {noun} detected. No action needed.",
        )

    return OverallHealthSummary(
        icon="🟢",
        headline="Healthy",
        detail="No action needed.",
    )


def render_overall_summary(summary: OverallHealthSummary) -> list[str]:
    return [
        f"{summary.icon} Overall: {summary.headline}",
        summary.detail,
    ]


def build_actions_minutes_lines(billing_data: dict[str, Any] | None) -> list[str]:
    """Return a one-line summary of GitHub Actions minutes consumption.

    Thresholds: >95% → 🔴, >80% → ⚠️, missing data → ℹ️, otherwise 🟢.
    """
    if not billing_data:
        return ["ℹ️ Actions minutes data unavailable for this account type"]

    total = billing_data.get("total_minutes_used")
    included = billing_data.get("included_minutes")

    if total is None or included is None:
        return ["ℹ️ Actions minutes data unavailable for this account type"]

    if included == 0:
        return [f"ℹ️ GitHub Actions: {total:,} minutes used (unlimited plan)"]

    pct = total / included * 100

    if pct > 95:
        icon = "🔴"
        label = "critical"
    elif pct > 80:
        icon = "⚠️"
        label = "warning"
    else:
        icon = "🟢"
        label = "ok"

    return [f"{icon} GitHub Actions minutes: {total:,} / {included:,} ({pct:.0f}%) — {label}"]


def render_report(
    *,
    workflow_status_lines: list[str],
    state_issues: list[Issue],
    report_date: str,
    state_check_label: str = "Bot Data Health Check (consolidated)",
    actions_minutes_lines: list[str] | None = None,
) -> str:
    lines = [f"🚦 XiannGPT Bot Daily Health — {report_date}"]
    summary = summarize_overall_health(workflow_status_lines=workflow_status_lines, state_issues=state_issues)
    lines.extend(["", *render_overall_summary(summary)])
    lines.extend(_render_section("Workflow Status", workflow_status_lines))

    if state_issues:
        if any(issue.severity == "error" for issue in state_issues):
            icon, status = "🔴", "issues found"
        else:
            icon, status = "🟡", "warnings found"
        lines.extend(
            [
                f"{icon} {state_check_label}",
                f"Last run: {status} (see State / Artifact Health)",
            ]
        )
    else:
        lines.extend(
            [
                f"🟢 {state_check_label}",
                "Last run: healthy (see State / Artifact Health)",
            ]
        )

    state_lines: list[str] = []
    if not state_issues:
        state_lines.append("🟢 No state inconsistencies detected")
    else:
        sorted_issues = sorted(state_issues, key=lambda item: (item.severity != "error", item.title, item.code))
        for index, issue in enumerate(sorted_issues):
            state_lines.extend(_render_state_issue(issue))
            if index < len(sorted_issues) - 1:
                state_lines.append("")

    lines.extend(_render_section("State / Artifact Health", state_lines))
    lines.extend(_render_section("Instagram Fetch", build_instagram_summary_lines()))
    if actions_minutes_lines is not None:
        lines.extend(_render_section("GitHub Actions Minutes", actions_minutes_lines))

    return "\n".join(lines).strip()


def _render_section(title: str, content_lines: list[str]) -> list[str]:
    section = ["", f"## {title}", ""]
    section.extend(content_lines)
    return section


def _serialize_schedule_diagnostics(
    workflow_name: str,
    stale_hours: int,
    diagnostics: WorkflowScheduleDiagnostics | None,
) -> dict[str, Any] | None:
    if diagnostics is None:
        return None
    payload = asdict(diagnostics)
    for key in (
        "expected_schedule_time_utc",
        "expected_window_start_utc",
        "latest_run_created_at",
        "latest_run_updated_at",
        "scheduled_run_in_window_created_at",
    ):
        value = payload.get(key)
        if isinstance(value, datetime):
            payload[key] = value.isoformat()
    payload["workflow_name"] = workflow_name
    payload["stale_hours"] = stale_hours
    return payload


def build_workflow_status_lines(
    workflow_runs: list[dict[str, Any]], *, now_utc: datetime | None = None
) -> tuple[list[str], list[dict[str, Any]]]:
    now_utc = now_utc or datetime.now(timezone.utc)
    lines: list[str] = []
    diagnostics_payload: list[dict[str, Any]] = []
    for workflow in workflow_runs:
        stale_hours = int(workflow["staleHours"])
        recent_runs = workflow.get("recentRuns")
        run = workflow.get("run")
        if not isinstance(run, dict):
            run = recent_runs[0] if isinstance(recent_runs, list) and recent_runs and isinstance(recent_runs[0], dict) else None
        icon, status_text, include_details, status_reason = evaluate_workflow_status(run, stale_hours, now_utc=now_utc)
        schedule_diagnostics = build_schedule_diagnostics(
            workflow["name"],
            latest_run=run if isinstance(run, dict) else None,
            recent_runs=recent_runs if isinstance(recent_runs, list) else ([run] if isinstance(run, dict) else []),
            now_utc=now_utc,
        )
        # Override icon to yellow for schedule-missed cases even when the run itself succeeded.
        # Only override when we positively know the latest run was manual/non-scheduled
        # (latest_run_event is set and not "schedule"). Unknown-event runs are not overridden.
        if schedule_diagnostics and icon == "🟢":
            known_manual = (
                schedule_diagnostics.latest_run_event is not None
                and schedule_diagnostics.latest_run_event != "schedule"
            )
            if (
                known_manual
                and schedule_diagnostics.code == "workflow.expected_scheduled_run_missing"
            ):
                icon = "🟡"
                status_text = f"{status_text} — scheduled run missed, recovered manually"
                status_reason = "schedule_missed"
                include_details = True
            elif (
                known_manual
                and schedule_diagnostics.code == "workflow.latest_manual_run"
                and not schedule_diagnostics.found_scheduled_run_in_window
            ):
                icon = "🟡"
                status_text = f"{status_text} — scheduled run missed, recovered manually"
                status_reason = "schedule_missed"
                include_details = True
        serialized = _serialize_schedule_diagnostics(workflow["name"], stale_hours, schedule_diagnostics)
        if serialized:
            diagnostics_payload.append(serialized)
        lines.append(f"{icon} {workflow['name']}")
        lines.append(f"Last run: {status_text}")

        if include_details and isinstance(run, dict):
            lines.append(f"Expected freshness: ≤{stale_hours}h")
            trigger = run.get("event")
            if isinstance(trigger, str) and trigger:
                lines.append(f"Trigger: {trigger}")
            timestamp_text = _format_ny_timestamp(run.get("updated_at") or run.get("created_at"))
            if timestamp_text:
                lines.append(f"Last run time: {timestamp_text}")
            if run.get("html_url"):
                lines.append(f"Run: {run['html_url']}")
        if schedule_diagnostics:
            lines.append(f"Expected cadence: {schedule_diagnostics.expected_cadence}")
            expected_time_text = _format_ny_timestamp(schedule_diagnostics.expected_schedule_time_utc.isoformat())
            if expected_time_text:
                lines.append(f"Expected latest schedule (ET): {expected_time_text}")
            lines.append(
                f"Schedule check: {schedule_diagnostics.message} [{schedule_diagnostics.code}]"
            )
            if schedule_diagnostics.latest_run_is_manual_recovery:
                lines.append("Latest context: manual recovery run detected after scheduled execution.")
            elif schedule_diagnostics.latest_run_event and schedule_diagnostics.latest_run_event != "schedule":
                lines.append("Latest context: latest run trigger is manual/non-scheduled.")
            if not schedule_diagnostics.found_scheduled_run_in_window:
                lines.append("Scheduled window evidence: no scheduled run found in expected window.")
        guidance = _workflow_guidance(
            status_reason=status_reason,
            icon=icon,
            workflow_name=workflow["name"],
            run=run if isinstance(run, dict) else None,
        )
        if guidance:
            disposition, next_step = guidance
            lines.append(f"Disposition: {disposition}")
            lines.append(f"Next step: {next_step}")
        lines.append("")
    return lines, diagnostics_payload


def build_instagram_summary_lines() -> list[str]:
    """Return lines for the Instagram Fetch section of the daily health report."""
    data = _load_json(INSTAGRAM_FETCH_SUMMARY_PATH)
    if not isinstance(data, dict):
        return ["_No Instagram fetch summary available for today_"]

    total = data.get("total_creators", INSTAGRAM_TOTAL_CREATORS)
    with_posts = data.get("creators_with_posts", 0)
    collected = data.get("total_posts_collected", 0)
    skipped_seen = data.get("total_skipped_seen", 0)
    failed = data.get("failed_creators", [])
    run_at = data.get("run_at", "")

    lines: list[str] = []
    icon = "🟢" if not failed else "🔴"
    lines.append(f"{icon} {with_posts} of {total} creators fetched new posts")
    lines.append(f"Posts collected: {collected}  |  Skipped (already seen): {skipped_seen}")
    if failed:
        lines.append(f"⚠️ Failed creators: {', '.join('@' + u for u in failed)}")
    if run_at:
        lines.append(f"Last fetch: {run_at}")
    return lines


def _report_date_new_york(now_utc: datetime) -> str:
    ny_time = now_utc.astimezone(NEW_YORK_TZ)
    return f"{ny_time.strftime('%b')} {ny_time.day}, {ny_time.year}"


def load_state_sanity_issues(path: Path) -> list[Issue]:
    """Read state_sanity.json and convert errors/warnings to Issue objects."""
    if not path.exists():
        return []
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return []
    if not isinstance(data, dict):
        return []

    issues: list[Issue] = []
    for msg in data.get("errors") or []:
        issues.append(Issue(
            code="STATE_SANITY_ERROR",
            severity="error",
            title="State sanity check error",
            context=str(msg),
            file_path=None,
        ))
    for msg in data.get("warnings") or []:
        issues.append(Issue(
            code="STATE_SANITY_WARNING",
            severity="warning",
            title="State sanity check warning",
            context=str(msg),
            file_path=None,
        ))
    return issues


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workflow-runs-json", required=True, help="Path to workflow run metadata JSON")
    parser.add_argument(
        "--schedule-diagnostics-out",
        default="",
        help="Optional path to write compact workflow schedule diagnostics JSON.",
    )
    parser.add_argument(
        "--state-sanity-json",
        default="",
        help="Optional path to state_sanity.json; errors/warnings are surfaced in the report.",
    )
    parser.add_argument(
        "--actions-billing-json",
        default="",
        help="Optional path to GitHub Actions billing JSON; used to show minutes consumption.",
    )
    args = parser.parse_args()

    payload = _load_json(Path(args.workflow_runs_json))
    workflow_runs = payload if isinstance(payload, list) else []

    now_utc = datetime.now(timezone.utc)
    workflow_lines, diagnostics_payload = build_workflow_status_lines(workflow_runs, now_utc=now_utc)
    state_issues = compute_state_issues(now_utc=now_utc)
    if args.state_sanity_json:
        state_issues.extend(load_state_sanity_issues(Path(args.state_sanity_json)))
    elif Path("state_sanity.json").exists():
        state_issues.extend(load_state_sanity_issues(Path("state_sanity.json")))

    billing_data: dict[str, Any] | None = None
    if args.actions_billing_json and Path(args.actions_billing_json).exists():
        raw = _load_json(Path(args.actions_billing_json))
        billing_data = raw if isinstance(raw, dict) else None
    actions_minutes = build_actions_minutes_lines(billing_data)

    report = render_report(
        workflow_status_lines=workflow_lines,
        state_issues=state_issues,
        report_date=_report_date_new_york(now_utc),
        actions_minutes_lines=actions_minutes,
    )
    if args.schedule_diagnostics_out:
        Path(args.schedule_diagnostics_out).write_text(
            json.dumps({"generated_at_utc": now_utc.isoformat(), "workflows": diagnostics_payload}, indent=2),
            encoding="utf-8",
        )
    print(report)


if __name__ == "__main__":
    main()
