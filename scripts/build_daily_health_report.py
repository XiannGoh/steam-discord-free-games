"""Build a unified once-daily bot health report for Discord."""

from __future__ import annotations

import argparse
import json
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parent.parent
WEEKLY_PATHS = {
    "messages": ROOT / "data/scheduling/weekly_schedule_messages.json",
    "responses": ROOT / "data/scheduling/weekly_schedule_responses.json",
    "summary": ROOT / "data/scheduling/weekly_schedule_summary.json",
    "outputs": ROOT / "data/scheduling/weekly_schedule_bot_outputs.json",
    "roster": ROOT / "data/scheduling/expected_schedule_roster.json",
}
DAILY_POSTS_PATH = ROOT / "discord_daily_posts.json"

NEW_YORK_TZ = ZoneInfo("America/New_York")


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
    parsed = _parse_iso_utc(value)
    if parsed is None:
        return None
    ny_time = parsed.astimezone(NEW_YORK_TZ)
    hour_12 = ((ny_time.hour - 1) % 12) + 1
    return f"{ny_time.strftime('%b')} {ny_time.day}, {hour_12}:{ny_time.minute:02d} {ny_time.strftime('%p')} ET"


def evaluate_workflow_status(run: dict[str, Any] | None, stale_hours: int) -> tuple[str, str, bool, str]:
    if not run:
        return "🟡", "no recent run found", False, "no_recent_run"

    updated_at = run.get("updated_at") or run.get("created_at")
    if not isinstance(updated_at, str):
        return "🟡", "run timestamp missing", True, "timestamp_missing"

    run_time = datetime.fromisoformat(updated_at.replace("Z", "+00:00"))
    age_hours = (datetime.now(timezone.utc) - run_time).total_seconds() / 3600
    recency = _format_age(age_hours)
    conclusion = str(run.get("conclusion") or run.get("status") or "unknown")

    if age_hours > stale_hours:
        return "🟡", f"{conclusion} ({recency}) — stale", True, "stale"
    if conclusion == "success":
        return "🟢", f"{conclusion} ({recency})", False, "success"
    if conclusion in {"failure", "timed_out", "startup_failure", "action_required"}:
        return "🔴", f"{conclusion} ({recency})", True, "failed"
    return "🟡", f"{conclusion} ({recency})", True, "non_success"


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
    if status_reason == "stale":
        return (
            "Action recommended",
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


def render_report(
    *,
    workflow_status_lines: list[str],
    state_issues: list[Issue],
    report_date: str,
    state_check_label: str = "Bot Data Health Check (consolidated)",
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

    return "\n".join(lines).strip()


def _render_section(title: str, content_lines: list[str]) -> list[str]:
    section = ["", f"## {title}", ""]
    section.extend(content_lines)
    return section


def build_workflow_status_lines(workflow_runs: list[dict[str, Any]]) -> list[str]:
    lines: list[str] = []
    for workflow in workflow_runs:
        stale_hours = int(workflow["staleHours"])
        run = workflow.get("run")
        icon, status_text, include_details, status_reason = evaluate_workflow_status(run, stale_hours)
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
    return lines


def _report_date_new_york(now_utc: datetime) -> str:
    ny_time = now_utc.astimezone(NEW_YORK_TZ)
    return f"{ny_time.strftime('%b')} {ny_time.day}, {ny_time.year}"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workflow-runs-json", required=True, help="Path to workflow run metadata JSON")
    args = parser.parse_args()

    payload = _load_json(Path(args.workflow_runs_json))
    workflow_runs = payload if isinstance(payload, list) else []

    now_utc = datetime.now(timezone.utc)
    workflow_lines = build_workflow_status_lines(workflow_runs)
    state_issues = compute_state_issues(now_utc=now_utc)
    report = render_report(
        workflow_status_lines=workflow_lines,
        state_issues=state_issues,
        report_date=_report_date_new_york(now_utc),
    )
    print(report)


if __name__ == "__main__":
    main()
