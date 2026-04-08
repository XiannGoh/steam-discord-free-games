# steam-discord-free-games

Discord automation for two production workflows:

1. **Weekly scheduling** (availability prompts + ongoing sync/summary/reminders)
2. **Steam daily picks + evening winners**

This repository is now **channel-based** (no thread-based posting).

## Current Discord Channels

- Weekly scheduling channel: `update-weekly-schedule-here` (`1491294381418741870`)
- Daily picks channel: `daily-game-picks` (`1491294533751799809`)
- Winners destination channel: `daily-game-picks` (uses `DISCORD_DAILY_PICKS_CHANNEL_ID` when set; otherwise falls back to daily item channel/state and then `DISCORD_WINNERS_CHANNEL_ID`)

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

## Local Testing

- Tests run with `pytest` (see repository test suite).
- For workflow-accurate behavior, prefer testing via `workflow_dispatch` inputs in GitHub Actions.
