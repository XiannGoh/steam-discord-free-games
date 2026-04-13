"""Build and post the end-of-day system health briefing to Discord.

Reads:
  --run-data-json <path>  JSON file written by the GitHub Actions collect step.
    Structure:
      generated_at: ISO timestamp
      cutoff: ISO timestamp (24h ago)
      operational_workflows: list of {file, name, channel, conclusion, html_url, created_at}
      auto_fix_runs: list of {run_id, conclusion, html_url, created_at}
      fix_prs: list of {number, title, mergedAt, headRefName}
      watchdog_runs: list of {run_id, conclusion, created_at}

Reads from filesystem (if present, downloaded as artifacts):
  daily_verification.json
  discord_verification.json
  gaming_library_verification.json
  weekly_schedule_verification.json
  state_sanity.json

Posts ONE structured briefing message to DISCORD_HEALTH_MONITOR_WEBHOOK_URL,
split into chunks if needed using split_discord_content from discord_api.py.

Exit codes:
  0 — posted successfully
  1 — fatal error (missing webhook, JSON parse failure, Discord POST failure)
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from datetime import datetime, timezone
from typing import Any
from zoneinfo import ZoneInfo

from discord_api import split_discord_content

NEW_YORK_TZ = ZoneInfo("America/New_York")

# Maps (channel_slug, workflow_file) for the four pipeline channels.
CHANNEL_WORKFLOWS = [
    ("step-1-vote-on-games-to-test",   "daily.yml"),
    ("step-2-test-then-vote-to-keep",  "evening-winners.yml"),
    ("step-3-review-existing-games",   "gaming-library-daily.yml"),
    ("update-weekly-schedule-here",    "weekly-scheduling-bot.yml"),
]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _load_json_file(path: str) -> dict[str, Any] | None:
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else None
    except Exception:
        return None


def _conclusion_emoji(conclusion: str | None, *, ran: bool) -> str:
    if not ran:
        return "⚪"
    if conclusion == "success":
        return "🟢"
    if conclusion == "failure":
        return "🔴"
    if conclusion in ("cancelled", "skipped", "timed_out"):
        return "🟡"
    return "🟡"


def _ny_date_str(now_utc: datetime) -> str:
    return now_utc.astimezone(NEW_YORK_TZ).strftime("%Y-%m-%d")


# ---------------------------------------------------------------------------
# Section renderers
# ---------------------------------------------------------------------------

def render_channel_lines(
    run_data: dict[str, Any],
    verifications: dict[str, dict[str, Any] | None],
) -> list[str]:
    wf_by_file = {
        wf.get("file"): wf
        for wf in (run_data.get("operational_workflows") or [])
    }
    lines: list[str] = []
    for channel, wf_file in CHANNEL_WORKFLOWS:
        wf = wf_by_file.get(wf_file)
        ran = wf is not None and wf.get("conclusion") is not None
        conclusion = (wf.get("conclusion") if wf else None)
        icon = _conclusion_emoji(conclusion, ran=ran)
        status_text = conclusion if ran else "no run in past 24h"

        ver = verifications.get(channel)
        ver_note = ""
        if ver is not None:
            passed = ver.get("pass")
            if passed is True:
                ver_note = " · verify ✅"
            elif passed is False:
                err_count = len(ver.get("errors") or [])
                ver_note = f" · verify ❌ ({err_count} error{'s' if err_count != 1 else ''})"
                if icon == "🟢":
                    icon = "🟡"  # workflow ok but verification failed

        lines.append(f"{icon} {channel} — {status_text}{ver_note}")
    return lines


def render_auto_fix_section(run_data: dict[str, Any]) -> list[str]:
    auto_fix_runs = run_data.get("auto_fix_runs") or []
    fix_prs = run_data.get("fix_prs") or []
    lines: list[str] = []

    pr_count = len(fix_prs)
    run_count = len(auto_fix_runs)

    if pr_count == 0 and run_count == 0:
        lines.append("🔧 Auto-fixes: none in past 24h")
        return lines

    lines.append(f"🔧 Auto-fixes: {pr_count} PR{'s' if pr_count != 1 else ''} merged in past 24h")

    if fix_prs:
        for pr in fix_prs:
            number = pr.get("number", "?")
            title = pr.get("title") or "(no title)"
            branch = pr.get("headRefName") or ""
            lines.append(f"  #{number} — {title}")
            if branch:
                lines.append(f"    Branch: {branch}")
    elif run_count > 0:
        # Runs happened but no PRs merged yet (or PRs still open)
        successes = sum(1 for r in auto_fix_runs if r.get("conclusion") == "success")
        failures = sum(1 for r in auto_fix_runs if r.get("conclusion") == "failure")
        lines.append(
            f"  auto-fix.yml ran {run_count}× — "
            f"{successes} succeeded, {failures} failed"
        )

    return lines


def render_watchdog_section(run_data: dict[str, Any]) -> list[str]:
    watchdog_runs = run_data.get("watchdog_runs") or []

    if not watchdog_runs:
        return ["⚠️ Watchdog: no runs recorded in past 24h (may not be deployed yet)"]

    failures = [r for r in watchdog_runs if r.get("conclusion") == "failure"]
    if failures:
        return [
            f"⚠️ Watchdog: {len(watchdog_runs)} run(s) in past 24h — "
            f"{len(failures)} unexpected job failure(s)"
        ]
    return [
        f"⚠️ Watchdog: {len(watchdog_runs)} run(s) in past 24h, all clean "
        "(real-time Discord alerts are posted when a workflow is triggered)"
    ]


def render_verification_section(
    verifications: dict[str, dict[str, Any] | None],
) -> list[str]:
    labels = {
        "step-1-vote-on-games-to-test":  "step-1",
        "step-2-test-then-vote-to-keep": "step-2",
        "step-3-review-existing-games":  "step-3",
        "update-weekly-schedule-here":   "weekly-schedule",
    }
    lines = ["📊 Verification:"]
    for channel, label in labels.items():
        ver = verifications.get(channel)
        if ver is None:
            lines.append(f"  {label}: not available")
        elif ver.get("pass") is True:
            lines.append(f"  {label}: ✅ pass")
        else:
            errors = ver.get("errors") or []
            err_note = (
                f" ({len(errors)} error{'s' if len(errors) != 1 else ''})"
                if errors else ""
            )
            lines.append(f"  {label}: ❌ fail{err_note}")
    return lines


def render_state_sanity_section(sanity: dict[str, Any] | None) -> list[str]:
    if sanity is None:
        return ["🧹 State sanity: not available"]
    errors = sanity.get("errors") or []
    warnings = sanity.get("warnings") or []
    if not errors and not warnings:
        return ["🧹 State sanity: clean ✅"]
    parts: list[str] = []
    if errors:
        parts.append(f"{len(errors)} error{'s' if len(errors) != 1 else ''}")
    if warnings:
        parts.append(f"{len(warnings)} warning{'s' if len(warnings) != 1 else ''}")
    status = "❌" if errors else "🟡"
    return [f"🧹 State sanity: {', '.join(parts)} {status}"]


def _overall_status(
    channel_lines: list[str],
    auto_fix_runs: list[dict[str, Any]],
    verifications: dict[str, dict[str, Any] | None],
    sanity: dict[str, Any] | None,
) -> str:
    has_red = any(line.startswith("🔴") for line in channel_lines)
    has_yellow = any(line.startswith("🟡") for line in channel_lines)
    has_no_run = any("no run in past 24h" in line for line in channel_lines)
    ver_fails = sum(
        1 for v in verifications.values()
        if v is not None and v.get("pass") is False
    )
    sanity_errors = len((sanity or {}).get("errors") or [])

    # Escalation: workflow failure with no successful auto-fix PR to show for it
    fix_pr_count = 0  # computed outside, but we can check auto_fix_runs
    successful_fixes = sum(
        1 for r in auto_fix_runs if r.get("conclusion") == "success"
    )
    escalation = has_red and successful_fixes == 0

    if escalation or sanity_errors > 0:
        return "🔴 Escalation needed"
    if has_red or ver_fails > 0 or has_no_run:
        return "⚠️ Issues detected — see above"
    if has_yellow:
        return "⚠️ Issues detected — see above"
    return "🟢 All systems healthy"


# ---------------------------------------------------------------------------
# Report assembly
# ---------------------------------------------------------------------------

def build_report(
    run_data: dict[str, Any],
    verifications: dict[str, dict[str, Any] | None],
    sanity: dict[str, Any] | None,
    now_utc: datetime,
) -> str:
    date_str = _ny_date_str(now_utc)
    parts: list[str] = [f"📋 Daily System Report — {date_str} (New York time)", ""]

    channel_lines = render_channel_lines(run_data, verifications)
    parts.extend(channel_lines)
    parts.append("")

    parts.extend(render_auto_fix_section(run_data))
    parts.append("")

    parts.extend(render_watchdog_section(run_data))
    parts.append("")

    parts.extend(render_verification_section(verifications))
    parts.append("")

    parts.extend(render_state_sanity_section(sanity))
    parts.append("")

    auto_fix_runs = run_data.get("auto_fix_runs") or []
    parts.append(_overall_status(channel_lines, auto_fix_runs, verifications, sanity))

    return "\n".join(parts).strip()


# ---------------------------------------------------------------------------
# Discord posting
# ---------------------------------------------------------------------------

def post_to_discord(content: str, webhook_url: str) -> None:
    chunks = split_discord_content(content)
    total = len(chunks)
    for idx, chunk in enumerate(chunks, start=1):
        if total > 1:
            prefix = f"📋 Daily Briefing ({idx}/{total})\n"
            chunk = prefix + chunk
        payload = json.dumps({"content": chunk})
        result = subprocess.run(
            [
                "curl", "--silent", "--show-error",
                "--output", "/tmp/briefing-discord-response.txt",
                "--write-out", "%{http_code}",
                "--header", "Content-Type: application/json",
                "--header", "User-Agent: steam-discord-free-games-daily-briefing/1.0",
                "--data", payload,
                "--request", "POST",
                webhook_url,
            ],
            check=False,
            text=True,
            capture_output=True,
        )
        http_status = (result.stdout or "").strip()
        if http_status.startswith("2"):
            print(f"Discord briefing chunk {idx}/{total} posted: HTTP {http_status}")
        else:
            print(
                f"Discord briefing chunk {idx}/{total} failed: HTTP {http_status}",
                file=sys.stderr,
            )
            try:
                with open("/tmp/briefing-discord-response.txt", encoding="utf-8") as f:
                    body = f.read()
                if body.strip():
                    print(body, file=sys.stderr)
            except OSError:
                pass
            sys.exit(1)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--run-data-json",
        required=True,
        help="Path to run_data.json written by the collect step",
    )
    args = parser.parse_args()

    webhook = os.environ.get("DISCORD_HEALTH_MONITOR_WEBHOOK_URL", "")
    if not webhook:
        print("ERROR: DISCORD_HEALTH_MONITOR_WEBHOOK_URL not set", file=sys.stderr)
        sys.exit(1)

    try:
        with open(args.run_data_json, encoding="utf-8") as f:
            run_data = json.load(f)
    except Exception as e:
        print(f"ERROR: failed to load {args.run_data_json}: {e}", file=sys.stderr)
        sys.exit(1)

    # Load verification artifacts (downloaded as workflow artifacts; absent = None).
    daily_ver = _load_json_file("daily_verification.json")
    discord_ver = _load_json_file("discord_verification.json")
    gaming_ver = _load_json_file("gaming_library_verification.json")
    weekly_ver = _load_json_file("weekly_schedule_verification.json")
    sanity = _load_json_file("state_sanity.json")

    # discord_verification.json carries both step-1 and step-2 channel results
    # under a top-level "channels" dict. Extract step-2 specifically; fall back
    # to the top-level object for step-1 (which also has a top-level "pass").
    step2_ver: dict[str, Any] | None = None
    if discord_ver is not None:
        channels = discord_ver.get("channels") or {}
        step2_ver = channels.get("step-2-test-then-vote-to-keep") or discord_ver

    verifications: dict[str, dict[str, Any] | None] = {
        "step-1-vote-on-games-to-test":  daily_ver,
        "step-2-test-then-vote-to-keep": step2_ver,
        "step-3-review-existing-games":  gaming_ver,
        "update-weekly-schedule-here":   weekly_ver,
    }

    now_utc = datetime.now(timezone.utc)
    report = build_report(run_data, verifications, sanity, now_utc)

    print("=== Daily Briefing ===")
    print(report)
    print("======================")

    post_to_discord(report, webhook)


if __name__ == "__main__":
    main()
