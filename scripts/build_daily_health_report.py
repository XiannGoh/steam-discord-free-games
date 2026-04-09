"""Build a unified once-daily bot health report for Discord."""

from __future__ import annotations

import argparse
import json
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
WEEKLY_PATHS = {
    "messages": ROOT / "data/scheduling/weekly_schedule_messages.json",
    "responses": ROOT / "data/scheduling/weekly_schedule_responses.json",
    "summary": ROOT / "data/scheduling/weekly_schedule_summary.json",
    "outputs": ROOT / "data/scheduling/weekly_schedule_bot_outputs.json",
    "roster": ROOT / "data/scheduling/expected_schedule_roster.json",
}
DAILY_POSTS_PATH = ROOT / "discord_daily_posts.json"


@dataclass(frozen=True)
class Issue:
    code: str
    severity: str  # "warning" or "error"
    message: str


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


def evaluate_workflow_status(run: dict[str, Any] | None, stale_hours: int) -> tuple[str, str, bool]:
    if not run:
        return "🟡", "no recent run found", False

    updated_at = run.get("updated_at") or run.get("created_at")
    if not isinstance(updated_at, str):
        return "🟡", "run timestamp missing", True

    run_time = datetime.fromisoformat(updated_at.replace("Z", "+00:00"))
    age_hours = (datetime.now(timezone.utc) - run_time).total_seconds() / 3600
    recency = _format_age(age_hours)
    conclusion = str(run.get("conclusion") or run.get("status") or "unknown")

    if age_hours > stale_hours:
        return "🟡", f"{conclusion} ({recency}) — stale", True
    if conclusion == "success":
        return "🟢", f"{conclusion} ({recency})", False
    if conclusion in {"failure", "timed_out", "startup_failure", "action_required"}:
        return "🔴", f"{conclusion} ({recency})", True
    return "🟡", f"{conclusion} ({recency})", True


def _week_keys(payload: Any) -> set[str]:
    if isinstance(payload, dict):
        return {str(k) for k in payload.keys()}
    return set()


def _expected_winners_day(now_utc: datetime) -> str:
    if now_utc.hour >= 23:
        return now_utc.date().isoformat()
    return (now_utc.date() - timedelta(days=1)).isoformat()


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

    if current_week_key:
        messages_entry = weekly_messages.get(current_week_key) if isinstance(weekly_messages, dict) else None
        responses_entry = weekly_responses.get(current_week_key) if isinstance(weekly_responses, dict) else None
        summary_entry = weekly_summary.get(current_week_key) if isinstance(weekly_summary, dict) else None
        outputs_entry = weekly_outputs.get(current_week_key) if isinstance(weekly_outputs, dict) else None

        if not isinstance(messages_entry, dict):
            issues["missing_weekly_messages"] = Issue(
                "missing_weekly_messages",
                "warning",
                f"Current weekly schedule message state missing for {current_week_key}",
            )

        has_responses = isinstance(responses_entry, dict) and bool(responses_entry.get("users"))
        if has_responses and not isinstance(summary_entry, dict):
            issues["missing_weekly_summary"] = Issue(
                "missing_weekly_summary",
                "warning",
                f"Weekly responses exist but no weekly summary exists for {current_week_key}",
            )

        if not isinstance(outputs_entry, dict):
            issues["missing_weekly_outputs"] = Issue(
                "missing_weekly_outputs",
                "warning",
                f"Weekly schedule outputs missing for current target week ({current_week_key})",
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
                issues["inconsistent_summary_fields"] = Issue(
                    "inconsistent_summary_fields",
                    "error",
                    "Weekly summary state is inconsistent (summary message/signature fields are partially missing)",
                )

    users = roster.get("users") if isinstance(roster, dict) else None
    if not isinstance(users, dict):
        issues["malformed_roster"] = Issue(
            "malformed_roster",
            "error",
            "Active roster file malformed (expected users object)",
        )
    elif not users:
        issues["empty_roster"] = Issue("empty_roster", "error", "Active roster is empty")

    today_key = now_utc.date().isoformat()
    winners_day = _expected_winners_day(now_utc)
    if not isinstance(daily_posts, dict):
        issues["missing_daily_posts"] = Issue(
            "missing_daily_posts",
            "warning",
            "Daily picks JSON is missing or unreadable",
        )
    else:
        today_entry = daily_posts.get(today_key)
        if not isinstance(today_entry, dict):
            issues["missing_today_entry"] = Issue(
                "missing_today_entry",
                "warning",
                f"Daily picks JSON missing expected current-day entry ({today_key})",
            )

        winners_entry = daily_posts.get(winners_day)
        if isinstance(winners_entry, dict):
            picks = winners_entry.get("items")
            winners_state = winners_entry.get("winners_state")
            has_picks = isinstance(picks, list) and len(picks) > 0
            if has_picks and not isinstance(winners_state, dict):
                issues["missing_winners_state"] = Issue(
                    "missing_winners_state",
                    "warning",
                    f"Daily picks exist for {winners_day} but winners state is missing",
                )
            if isinstance(winners_state, dict):
                message_id = winners_state.get("message_id")
                winner_keys = winners_state.get("winner_keys")
                if not isinstance(message_id, str) or not message_id.strip():
                    issues["missing_winners_message_id"] = Issue(
                        "missing_winners_message_id",
                        "error",
                        f"Winners state for {winners_day} is inconsistent (message id missing)",
                    )
                if not isinstance(winner_keys, list):
                    issues["malformed_winner_keys"] = Issue(
                        "malformed_winner_keys",
                        "error",
                        f"Winners state for {winners_day} is inconsistent (winner keys malformed)",
                    )
                elif not winner_keys:
                    issues["empty_winner_keys"] = Issue(
                        "empty_winner_keys",
                        "warning",
                        f"Winners state exists for {winners_day} but winner keys are empty",
                    )

    return list(issues.values())


def render_report(
    *,
    workflow_status_lines: list[str],
    state_issues: list[Issue],
    report_date: str,
    state_check_label: str = "Bot Data Health Check (consolidated)",
) -> str:
    lines = [f"🚦 XiannGPT Bot Daily Health — {report_date}"]
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
        for issue in sorted(state_issues, key=lambda item: (item.severity != "error", item.message)):
            icon = "🔴" if issue.severity == "error" else "🟡"
            state_lines.append(f"{icon} {issue.message}")

    lines.extend(_render_section("State / Artifact Health", state_lines))

    return "\n".join(lines).strip()


def _render_section(title: str, content_lines: list[str]) -> list[str]:
    section = ["", f"## {title}", ""]
    section.extend(content_lines)
    return section


def build_workflow_status_lines(workflow_runs: list[dict[str, Any]]) -> list[str]:
    lines: list[str] = []
    for workflow in workflow_runs:
        icon, status_text, include_run = evaluate_workflow_status(workflow.get("run"), int(workflow["staleHours"]))
        lines.append(f"{icon} {workflow['name']}")
        lines.append(f"Last run: {status_text}")
        run = workflow.get("run")
        if include_run and isinstance(run, dict) and run.get("html_url"):
            lines.append(f"Run: {run['html_url']}")
        lines.append("")
    return lines


def _report_date_new_york(now_utc: datetime) -> str:
    # Keep output date style aligned with previous report convention.
    from zoneinfo import ZoneInfo

    ny_time = now_utc.astimezone(ZoneInfo("America/New_York"))
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
