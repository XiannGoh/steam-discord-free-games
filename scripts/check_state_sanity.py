"""Lightweight sanity checks for JSON state files used by automation workflows."""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
WEEK_KEY_RE = re.compile(r"^\d{4}-\d{2}-\d{2}_to_\d{4}-\d{2}-\d{2}$")
DATE_KEY_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


class SanityReport:
    def __init__(self) -> None:
        self.errors: list[str] = []
        self.warnings: list[str] = []

    def error(self, message: str) -> None:
        self.errors.append(message)

    def warn(self, message: str) -> None:
        self.warnings.append(message)


def _load_json(path: Path, report: SanityReport, *, required: bool = True) -> Any | None:
    if not path.exists():
        message = f"missing file: {path.relative_to(ROOT)}"
        if required:
            report.error(message)
        else:
            report.warn(message)
        return None

    try:
        with path.open("r", encoding="utf-8") as file:
            return json.load(file)
    except json.JSONDecodeError as error:
        report.error(f"invalid JSON in {path.relative_to(ROOT)}: {error}")
    except OSError as error:
        report.error(f"failed to read {path.relative_to(ROOT)}: {error}")
    return None


def _expect_dict(payload: Any, relative_path: str, report: SanityReport) -> dict[str, Any]:
    if isinstance(payload, dict):
        return payload
    report.error(f"expected top-level object in {relative_path}, got {type(payload).__name__}")
    return {}


def check_weekly_mapping(path: str, report: SanityReport) -> None:
    payload = _load_json(ROOT / path, report)
    if payload is None:
        return

    mapping = _expect_dict(payload, path, report)
    for week_key, value in mapping.items():
        if not isinstance(week_key, str) or not WEEK_KEY_RE.match(week_key):
            report.warn(f"unexpected weekly key format in {path}: {week_key}")
        if not isinstance(value, dict):
            report.error(f"expected object for week {week_key} in {path}")


def check_expected_schedule_roster(report: SanityReport) -> None:
    path = "data/scheduling/expected_schedule_roster.json"
    payload = _load_json(ROOT / path, report)
    if payload is None:
        return

    mapping = _expect_dict(payload, path, report)
    users = mapping.get("users")
    if not isinstance(users, dict):
        report.error(f"expected 'users' object in {path}")
        return

    for user_id, details in users.items():
        if not isinstance(user_id, str):
            report.warn(f"non-string user id in {path}: {user_id}")
        if not isinstance(details, dict):
            report.error(f"expected object for user {user_id} in {path}")
            continue
        if "is_active" in details and not isinstance(details["is_active"], bool):
            report.error(f"expected boolean is_active for user {user_id} in {path}")


def check_daily_posts(report: SanityReport) -> None:
    path = "discord_daily_posts.json"
    payload = _load_json(ROOT / path, report, required=False)
    if payload is None:
        return

    mapping = _expect_dict(payload, path, report)
    for date_key, entry in mapping.items():
        if not isinstance(date_key, str) or not DATE_KEY_RE.match(date_key):
            report.warn(f"unexpected date key format in {path}: {date_key}")
        if not isinstance(entry, dict):
            report.error(f"expected object for date {date_key} in {path}")
            continue

        items = entry.get("items")
        if items is not None and not isinstance(items, list):
            report.error(f"expected 'items' list for date {date_key} in {path}")


def run_checks() -> int:
    report = SanityReport()

    check_weekly_mapping("data/scheduling/weekly_schedule_messages.json", report)
    check_weekly_mapping("data/scheduling/weekly_schedule_responses.json", report)
    check_weekly_mapping("data/scheduling/weekly_schedule_summary.json", report)
    check_weekly_mapping("data/scheduling/weekly_schedule_bot_outputs.json", report)
    check_expected_schedule_roster(report)
    check_daily_posts(report)

    if report.warnings:
        print("STATE SANITY WARNINGS:")
        for warning in report.warnings:
            print(f"- {warning}")

    if report.errors:
        print("STATE SANITY ERRORS:")
        for error in report.errors:
            print(f"- {error}")
        return 1

    print("State sanity check passed.")
    return 0


if __name__ == "__main__":
    sys.exit(run_checks())
