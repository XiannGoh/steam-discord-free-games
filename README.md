# steam-discord-free-games

[![Weekly Scheduling Bot](https://github.com/XiannGoh/steam-discord-free-games/actions/workflows/weekly-scheduling-bot.yml/badge.svg)](https://github.com/XiannGoh/steam-discord-free-games/actions/workflows/weekly-scheduling-bot.yml)
[![Weekly Scheduling Responses Sync](https://github.com/XiannGoh/steam-discord-free-games/actions/workflows/weekly-scheduling-responses-sync.yml/badge.svg)](https://github.com/XiannGoh/steam-discord-free-games/actions/workflows/weekly-scheduling-responses-sync.yml)
[![Steam Free Games](https://github.com/XiannGoh/steam-discord-free-games/actions/workflows/daily.yml/badge.svg)](https://github.com/XiannGoh/steam-discord-free-games/actions/workflows/daily.yml)
[![Daily Game Picks Winners](https://github.com/XiannGoh/steam-discord-free-games/actions/workflows/evening-winners.yml/badge.svg)](https://github.com/XiannGoh/steam-discord-free-games/actions/workflows/evening-winners.yml)

Discord automation for two production workflows:

1. **Weekly scheduling** (availability prompts + ongoing sync/summary/reminders)
2. **Steam daily picks + evening winners**

Additionally, this repo contains a **live voice-channel join alert bot script** (`scripts/voice_join_alert_bot.py`) that is intended to run continuously with a dedicated Discord bot token (not on a cron workflow).

This repository is now **channel-based** (no thread-based posting).

## Current Discord Channels

- Weekly scheduling channel: `update-weekly-schedule-here` (`1491294381418741870`)
- Daily picks channel: `daily-game-picks` (`1491294533751799809`)
- Winners destination channel: `daily-game-picks` (uses `DISCORD_DAILY_PICKS_CHANNEL_ID` when set; otherwise falls back to daily item channel/state and then `DISCORD_WINNERS_CHANNEL_ID`)
- Health monitor channel: `xiann-gpt-bot-health-monitor` (`1491520649917628536`)

---

## Operator Health / Status Quick Reference

Healthy at a glance:
- The four production workflows above show green badges.
- Recent runs in **Actions** are succeeding on schedule (daily/3-hourly/weekly cadence).
- New state commits continue landing for weekly and daily flows.

If something fails:
1. Open **GitHub Actions** → failed run → inspect the first failing step/log.
2. Confirm required secrets are still set and non-empty.
3. Re-run with `workflow_dispatch` and a specific date/week input when needed.
4. If `DISCORD_HEALTH_MONITOR_WEBHOOK_URL` is configured, a concise failure ping is sent to the health monitor Discord channel (failure only; no success spam).

### Health monitor operator dashboard

Operational alerts are now centralized in `xiann-gpt-bot-health-monitor`.

- **Immediate failure pings:** key production workflows send a compact `🔴 XiannGPT Bot Failure` message with workflow, job, context, and run URL.
- **Daily health report:** `bot-health-report.yml` posts one summary per day with signal lights:
  - 🟢 healthy (latest run succeeded)
  - 🟡 warning (stale, skipped, cancelled, or otherwise non-success)
  - 🔴 failed (latest run failed)
- **Operator default path:** this channel is now the primary dashboard for bot operations; email is no longer the preferred alerting path.

Manual rerun quick commands (GitHub CLI):

### Weekly Scheduling Bot (`weekly-scheduling-bot.yml`)
- **What it does:** posts/repairs weekly intro + weekday availability prompts.
- **Manual input:** `schedule_week_start` (optional `YYYY-MM-DD`, Monday only).
- **When to rerun:** missed Saturday post, stale/deleted scheduling messages, or a backfill week.
- **Examples:**
  - Default target logic: `gh workflow run weekly-scheduling-bot.yml`
  - Specific week: `gh workflow run weekly-scheduling-bot.yml -f schedule_week_start=2026-04-13`

### Weekly Scheduling Responses Sync (`weekly-scheduling-responses-sync.yml`)
- **What it does:** pulls reactions, updates state, and posts/repairs weekly summary/reminders.
- **Manual inputs:**
  - `target_week_key` (optional, e.g. `2026-04-13_to_2026-04-19`)
  - `rebuild_summary_only` (`true`/`false`)
  - `dry_run` (`true`/`false`)
- **When to rerun:** summary drift, missed reminder, or post-reaction sync repair.
- **Examples:**
  - Standard sync: `gh workflow run weekly-scheduling-responses-sync.yml`
  - Rebuild only for one week: `gh workflow run weekly-scheduling-responses-sync.yml -f target_week_key=2026-04-13_to_2026-04-19 -f rebuild_summary_only=true`
  - Preview only: `gh workflow run weekly-scheduling-responses-sync.yml -f dry_run=true`

### Daily Picks (`daily.yml`)
- **What it does:** posts daily free/paid/creator picks and updates `discord_daily_posts.json`.
- **Manual input:** `daily_date_utc` (optional `YYYY-MM-DD`).
- **When to rerun:** partial morning run, stale/deleted daily messages, or date-targeted state repair.
- **Examples:**
  - Default run date: `gh workflow run daily.yml`
  - Target date: `gh workflow run daily.yml -f daily_date_utc=2026-04-08`

### Evening Winners (`evening-winners.yml`)
- **What it does:** computes winners from same-day 👍 reactions and posts/edits winners message.
- **Manual input:** `winners_date_utc` (optional `YYYY-MM-DD`).
- **When to rerun:** missed evening summary or reaction recount/state repair for a specific day.
- **Examples:**
  - Default run date: `gh workflow run evening-winners.yml`
  - Target date: `gh workflow run evening-winners.yml -f winners_date_utc=2026-04-08`

### State sanity check (`state-sanity-check.yml`)
- **What it does:** read-only parse/shape checks for key JSON state files.
- **Schedule:** Mondays at 12:00 UTC.
- **Manual rerun:** `gh workflow run state-sanity-check.yml`
- **Local run:** `python scripts/check_state_sanity.py`

### Bot health report (`bot-health-report.yml`)
- **What it does:** posts a once-daily Discord health summary across core workflows.
- **Schedule:** `10 3 * * *` (03:10 UTC daily; ~11:10 PM New York during EDT).
- **Manual rerun:** `gh workflow run bot-health-report.yml`

---

## Workflow Overview

### 1) Weekly Scheduling Bot

- **Workflow:** `.github/workflows/weekly-scheduling-bot.yml`
- **Schedule:** `0 13 * * 6` (Saturday 13:00 UTC, ~9:00 AM New York during EDT)
- **Script:** `scripts/post_weekly_availability.py`

Behavior:
- Posts one weekly intro message plus 7 weekday messages (Mon-Sun)
- Seeds each day message with default reactions:
  - ✅ 🌅 ☀️ 🌙 ❌ 📝
- Stores message IDs/state in `data/scheduling/weekly_schedule_messages.json`
- Is rerun-safe for the target week (reuses existing messages when possible)
- Keeps only latest 12 weeks of weekly-message state

Manual operator control:
- `workflow_dispatch` input `schedule_week_start` (`YYYY-MM-DD`, must be Monday)

### 2) Weekly Scheduling Responses Sync

- **Workflow:** `.github/workflows/weekly-scheduling-responses-sync.yml`
- **Schedule:** `0 */3 * * *` (every 3 hours)
- **Concurrency:** `weekly-scheduling-responses-sync` (non-overlapping runs)
- **Script:** `scripts/sync_weekly_schedule_responses.py`

What it does:
- Pulls reactions from weekly day messages
- Treats a user as "responded" if they have any valid reaction on any day
- Tracks optional custom replies for 📝
- Builds and stores derived summary data
- Posts/edits weekly summary message in the scheduling channel
- Posts reminder mentions for missing users when needed

Weekly state files (12-week retention):
- `data/scheduling/weekly_schedule_messages.json`
- `data/scheduling/weekly_schedule_responses.json`
- `data/scheduling/weekly_schedule_summary.json`
- `data/scheduling/weekly_schedule_bot_outputs.json`
- `data/scheduling/expected_schedule_roster.json`

Current expected roster (`expected_schedule_roster.json`):
- Jan
- Jerry
- Akhil
- TCFS100
- Raymond Monkey King Martinez
- Charlie
- Kevin Lam
- Malphax
- lilwartz
- Rishabh
- Thomas

Summary behavior:
- One summary per week (`summary_message_id` per week)
- Existing summary is edited in-place when content changes
- Prior weeks are preserved (not overwritten)
- Includes calendar dates next to weekdays
- Includes voter names grouped by emoji bucket
- Uses Discord-friendly multiline formatting
- Applies truncation/fallback when needed to fit message limits
- No separate “Best overlap” section in posted summary

Reminder behavior:
- Mentions users using `<@USER_ID>`
- Posts only if missing-user list changed vs prior reminder state
- Daily cap: at most one reminder per New York local calendar day
- Reminder cap state stored via `last_reminder_local_date` in `weekly_schedule_bot_outputs.json`

Operator controls (`workflow_dispatch` / env passthrough):
- `TARGET_WEEK_KEY` (target a specific week key)
- `REBUILD_SUMMARY_ONLY` (skip reaction fetch, rebuild/repair summary only)
- `DRY_RUN` (print preview and skip Discord mutations)

---

### 3) Daily Steam Picks (Morning)

- **Workflow:** `.github/workflows/daily.yml`
- **Schedule:** `0 13 * * *` (13:00 UTC daily, ~9:00 AM New York during EDT)
- **Script:** `main.py`

Posting behavior in `daily-game-picks`:
- Intro message
- Section headers:
  - Free Picks
  - Paid Under $20
  - Instagram Creator Picks
- One Discord message per item
- Adds a default 👍 reaction to each posted item

State + reliability:
- Tracks daily post metadata in `discord_daily_posts.json`
- Retains latest 30 date keys
- Same-day reruns are idempotent:
  - Reuses existing intro/header/item messages when they still exist
  - Recovers from partial runs without duplicating everything
- Includes stale/deleted message recovery checks before reusing state
- Steam title parsing is cleaned so titles do not include trailing `on Steam`

Manual operator control:
- `workflow_dispatch` input `daily_date_utc` (`YYYY-MM-DD`) for manual rerun targeting

### 4) Evening Winners (Daily)

- **Workflow:** `.github/workflows/evening-winners.yml`
- **Schedule:** `0 23 * * *` (23:00 UTC daily, ~7:00 PM New York during EDT)
- **Script:** `evening_winners.py`

Behavior:
- Reads same-day items from `discord_daily_posts.json`
- Fetches 👍 counts from item messages
- Fetches 👍 voter identities and displays human voter names per winner
- Subtracts bot default 👍 to compute human votes
- Inclusion rules:
  - raw 👍 = 1 → excluded (0 human votes)
  - raw 👍 = 2 → included (1 human vote)
  - raw 👍 = 3 → included (2 human votes)
- Posts winners to `daily-game-picks` (`DISCORD_DAILY_PICKS_CHANNEL_ID` preferred; backward-compatible fallback to `DISCORD_WINNERS_CHANNEL_ID`)

Rerun/idempotency behavior:
- Same-day reruns do not duplicate winners posts
- If content unchanged, run skips
- If changed, existing winners message is edited
- If stored winners message was deleted/stale, script posts a replacement and repairs state

---

## Shared Infrastructure

The repository now centralizes common behavior in:

- `discord_api.py`
  - Discord request helper
  - retry handling (rate limits/transient errors)
  - explicit stale/deleted message signal (`DiscordMessageNotFoundError`)
- `state_utils.py`
  - JSON load helpers
  - atomic JSON writes
  - retention helpers (`prune_latest_keys`, `prune_latest_iso_dates`)

This shared layer is used by weekly + daily flows for consistency and safer reruns.

---

## Environment / Secrets (GitHub Actions)

- Weekly scheduling bot:
  - `DISCORD_SCHEDULING_BOT_TOKEN`
  - `DISCORD_SCHEDULING_CHANNEL_ID`
- Weekly responses sync:
  - `DISCORD_SCHEDULING_BOT_TOKEN`
- Daily picks:
  - `DISCORD_WEBHOOK_URL`
  - `DISCORD_BOT_TOKEN`
  - `INSTAGRAM_USERNAME`
  - `INSTAGRAM_SESSION_B64`
- Evening winners:
  - `DISCORD_BOT_TOKEN`
  - `DISCORD_DAILY_PICKS_CHANNEL_ID` (recommended)
  - `DISCORD_WINNERS_CHANNEL_ID`
- Health monitor notifications (failure pings + daily report):
  - `DISCORD_HEALTH_MONITOR_WEBHOOK_URL`

## Voice-channel join alert bot (live process)

- **Script:** `scripts/voice_join_alert_bot.py`
- **Run model:** long-running Discord Gateway client process (required for live voice-state events)
- **Target voice channel ID:** `1491560965567938692`
- **Cooldown:** 5 minutes per joiner (`300` seconds)
- **Roster source:** `data/scheduling/expected_schedule_roster.json` (`is_active: true` users only)
- **Hard exclusions:** `162382481369071617` (Malphax), `161248274970443776` (lilwartz), and the current joiner
- **Message destination:** only the same voice channel's attached chat surface (no fallback destination channel)

Required environment variable:

- `DISCORD_VOICE_ALERT_BOT_TOKEN`

Start command:

```bash
python scripts/voice_join_alert_bot.py
```

## Local Testing

- Tests run with `pytest` (see repository test suite).
- For workflow-accurate behavior, prefer testing via `workflow_dispatch` inputs in GitHub Actions.
