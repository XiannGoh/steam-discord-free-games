"""Read/write data/health_monitor_log.json — failure tracking and recurrence detection.

CLI usage (called from auto-fix.yml bash steps):

  # Log a new failure and print JSON with: failure_id, recurrence_count, is_recurring
  python scripts/update_health_log.py log-failure \\
    --workflow "Steam Free Games" \\
    --issue-type "discord_verification_failed" \\
    --issue-description "Footer missing from Step 1 post" \\
    --run-id "12345678"

  # Update an existing failure record with fix outcome
  python scripts/update_health_log.py update-outcome \\
    --failure-id "abc123" \\
    --outcome "success" \\
    --branch "fix/auto-fix-12345678-1" \\
    --attempt 1 \\
    [--pr-number "230"]

  # Print a JSON summary for the daily health report
  python scripts/update_health_log.py summary
"""

import argparse
import json
import sys
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
HEALTH_LOG_FILE = ROOT / "data" / "health_monitor_log.json"

_EMPTY_LOG: dict = {
    "failures": [],
    "patterns": [],
    "last_updated": "",
}

RECURRENCE_WINDOW_DAYS = 7
RECURRENCE_THRESHOLD = 2   # how many occurrences in window → "recurring"
MANUAL_REVIEW_THRESHOLD = 10   # lifetime_count above this → recommend manual review


# ---------------------------------------------------------------------------
# I/O helpers
# ---------------------------------------------------------------------------

def _load() -> dict:
    if HEALTH_LOG_FILE.exists():
        try:
            data = json.loads(HEALTH_LOG_FILE.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                data.setdefault("failures", [])
                data.setdefault("patterns", [])
                data.setdefault("last_updated", "")
                return data
        except (json.JSONDecodeError, OSError):
            pass
    return {"failures": [], "patterns": [], "last_updated": ""}


def _save(data: dict) -> None:
    data["last_updated"] = _now_iso()
    HEALTH_LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
    HEALTH_LOG_FILE.write_text(json.dumps(data, indent=2), encoding="utf-8")


def _now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


# ---------------------------------------------------------------------------
# Core operations
# ---------------------------------------------------------------------------

def log_failure(
    workflow: str,
    issue_type: str,
    issue_description: str,
    run_id: str,
) -> dict:
    """Append a new failure record. Returns the created record plus recurrence info."""
    data = _load()
    failures: list[dict] = data["failures"]

    # Recurrence: same issue_type in the last RECURRENCE_WINDOW_DAYS days
    cutoff = datetime.now(timezone.utc) - timedelta(days=RECURRENCE_WINDOW_DAYS)
    recent_same = [
        f for f in failures
        if f.get("issue_type") == issue_type
        and f.get("workflow") == workflow
        and _parse_ts(f.get("timestamp", "")) >= cutoff
    ]
    recurrence_count = len(recent_same)
    is_recurring = recurrence_count >= RECURRENCE_THRESHOLD - 1  # this event is the Nth

    # Lifetime count across all time
    lifetime_count = sum(
        1 for f in failures
        if f.get("issue_type") == issue_type and f.get("workflow") == workflow
    )

    failure_id = str(uuid.uuid4())[:8]
    record: dict = {
        "id": failure_id,
        "timestamp": _now_iso(),
        "workflow": workflow,
        "run_id": run_id,
        "issue_type": issue_type,
        "issue_description": issue_description,
        "fix_attempted": False,
        "fix_branch": None,
        "fix_outcome": None,
        "attempt_number": None,
        "max_attempts": 3,
        "resolved_at": None,
        "recurrence_count": recurrence_count,
        "lifetime_count": lifetime_count,
        "is_recurring": is_recurring,
        "pattern_id": None,
    }
    failures.append(record)
    _save(data)

    return {
        "failure_id": failure_id,
        "recurrence_count": recurrence_count,
        "lifetime_count": lifetime_count,
        "is_recurring": is_recurring,
        "manual_review_recommended": lifetime_count >= MANUAL_REVIEW_THRESHOLD,
    }


def update_outcome(
    failure_id: str,
    outcome: str,
    branch: str,
    attempt: int,
    pr_number: str = "",
) -> dict:
    """Update an existing failure record with fix attempt outcome."""
    data = _load()
    failures: list[dict] = data["failures"]

    record = next((f for f in failures if f.get("id") == failure_id), None)
    if record is None:
        # Record not found — create a minimal one so callers don't crash
        record = {"id": failure_id}
        failures.append(record)

    record["fix_attempted"] = True
    record["fix_branch"] = branch
    record["fix_outcome"] = outcome
    record["attempt_number"] = attempt
    if pr_number:
        record["pr_number"] = pr_number
    if outcome == "success":
        record["resolved_at"] = _now_iso()

    _save(data)
    return record


def get_summary() -> dict:
    """Return a condensed summary for inclusion in daily health reports."""
    data = _load()
    failures: list[dict] = data["failures"]

    cutoff_24h = datetime.now(timezone.utc) - timedelta(hours=24)
    cutoff_7d = datetime.now(timezone.utc) - timedelta(days=RECURRENCE_WINDOW_DAYS)

    today_failures = [f for f in failures if _parse_ts(f.get("timestamp", "")) >= cutoff_24h]
    today_fixed = [f for f in today_failures if f.get("fix_outcome") == "success"]
    recurring = [f for f in failures if f.get("is_recurring") and _parse_ts(f.get("timestamp", "")) >= cutoff_7d]

    return {
        "failures_today": len(today_failures),
        "auto_fixes_today": len(today_fixed),
        "recurring_issues_7d": len(set(f.get("issue_type", "") for f in recurring)),
        "patterns_flagged": len(data.get("patterns", [])),
        "last_updated": data.get("last_updated", ""),
    }


def _parse_ts(ts: str) -> datetime:
    """Parse an ISO timestamp string. Returns epoch on failure."""
    try:
        if ts.endswith("Z"):
            ts = ts[:-1] + "+00:00"
        return datetime.fromisoformat(ts)
    except (ValueError, TypeError):
        return datetime(1970, 1, 1, tzinfo=timezone.utc)


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(description="Update the bot health monitor log.")
    sub = parser.add_subparsers(dest="command", required=True)

    p_log = sub.add_parser("log-failure", help="Log a new failure record")
    p_log.add_argument("--workflow", required=True)
    p_log.add_argument("--issue-type", required=True)
    p_log.add_argument("--issue-description", required=True)
    p_log.add_argument("--run-id", default="")

    p_upd = sub.add_parser("update-outcome", help="Update a failure record with fix outcome")
    p_upd.add_argument("--failure-id", required=True)
    p_upd.add_argument("--outcome", required=True, choices=["success", "failed"])
    p_upd.add_argument("--branch", default="")
    p_upd.add_argument("--attempt", type=int, default=1)
    p_upd.add_argument("--pr-number", default="")

    sub.add_parser("summary", help="Print a JSON summary for the daily health report")

    args = parser.parse_args()

    if args.command == "log-failure":
        result = log_failure(
            workflow=args.workflow,
            issue_type=args.issue_type,
            issue_description=args.issue_description,
            run_id=args.run_id,
        )
        print(json.dumps(result))

    elif args.command == "update-outcome":
        record = update_outcome(
            failure_id=args.failure_id,
            outcome=args.outcome,
            branch=args.branch,
            attempt=args.attempt,
            pr_number=args.pr_number,
        )
        print(json.dumps({"updated": True, "id": record.get("id", args.failure_id)}))

    elif args.command == "summary":
        print(json.dumps(get_summary()))


if __name__ == "__main__":
    main()
