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

## Tiny Repo File Map

- `main.py` → scoring, routing, daily pick selection, posting.
- `evening_winners.py` → winners selection and rendering.
- `daily_section_config.py` → shared daily section order/labels/routing ownership.
- `daily_debug_summary.json` → ephemeral operator debug output for the current run.

### Maintainer note

Section order and labels are intentionally centralized, threshold comments are intentional operator guidance, and any future tuning should happen only after observing real-world runs.

## Current Discord Channels

- Weekly scheduling channel: `update-weekly-schedule-here` (`1491294381418741870`)
- Daily picks channel: `step-1-vote-on-games-to-test` (`1491294533751799809`)
- Winners destination channel: configured by `DISCORD_WINNERS_CHANNEL_ID` (currently `step-2-test-then-vote-to-keep`)
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
- **Daily health report:** `bot-health-report.yml` posts one summary per day with:
  - A top-level **Overall** block (`🟢/🟡/🔴`) that tells operators if action is needed.
  - Workflow and state sections that include `Disposition` and `Next step` on yellow/red items.
  - Workflow schedule observability fields for monitored cron workflows:
    - latest trigger/event (`schedule`, `workflow_dispatch`, etc.)
    - expected cadence + expected latest scheduled window
    - schedule diagnosis code/message (for example `workflow.latest_manual_run`, `workflow.expected_scheduled_run_missing`)
    - manual-recovery context when a dispatch run appears to have been used as a repair path
  - Informational warnings that can explicitly be `No action needed`.
  - Action meaning:
    - 🟢 = no action needed
    - 🟡 = monitor / follow-up (non-urgent warnings)
    - 🔴 = action needed
    - Missed workflow freshness windows (`stale`) are treated as 🔴 action needed.
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
- **What it does:** posts daily demos/playtests + free/paid/creator picks and updates `discord_daily_posts.json`.
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

### Bot health report (`bot-health-report.yml`)
- **What it does:** posts a once-daily Discord health summary across core workflows.
- **Schedule:** `10 3 * * *` (03:10 UTC daily; ~11:10 PM New York during EDT).
- **Workflow freshness thresholds:** Weekly Scheduling Bot `168h`, Weekly Scheduling Responses Sync `6h`, Daily Steam Picks `20h`, Evening Winners `12h`.
- **Scheduling diagnostics artifact:** uploads `workflow-schedule-diagnostics.json` each run with compact expected-vs-actual schedule evidence (latest run metadata, expected window classification, and diagnosis code per monitored workflow).
- **Manual rerun:** `gh workflow run bot-health-report.yml`

## Operator Runbook (Consolidated)

Use this section as the fast triage guide when a workflow or state file drifts.

### Workflow purpose map

- `daily.yml`: posts daily game picks and persists item message metadata in `discord_daily_posts.json`.
- `evening-winners.yml`: reads daily picks + reactions, computes winners, posts/edits winners message, and stores winners state back into `discord_daily_posts.json`.
- `weekly-scheduling-bot.yml`: posts weekly intro/day prompts and writes week message IDs into `data/scheduling/weekly_schedule_messages.json`.
- `weekly-scheduling-responses-sync.yml`: syncs weekly reactions, rebuilds weekly summary, evaluates reminder decisions, posts/edits summary/reminders, and updates weekly response/summary/output state files.
- `bot-health-report.yml`: compiles workflow freshness + state/artifact checks and posts one daily health report to the health monitor channel/webhook.

### State files and what they are for

- `discord_daily_posts.json`: daily picks item registry and winners output state (`winners_state`) by day.
- `data/scheduling/weekly_schedule_messages.json`: canonical weekly intro/day message IDs and date-range context.
- `data/scheduling/weekly_schedule_responses.json`: synced per-user weekly availability reactions/custom replies.
- `data/scheduling/weekly_schedule_summary.json`: derived weekly summary structure from synced responses.
- `data/scheduling/weekly_schedule_bot_outputs.json`: operational outputs/metadata (summary message ID/content/signature, summary freshness timestamp, reminder state).
- `data/scheduling/expected_schedule_roster.json`: expected active roster used for reminder missing-user logic.

### What the daily health report checks

- Workflow freshness/status (stale, failure, missing recent run).
- Workflow schedule diagnostics (scheduled-window evidence, latest trigger context, manual-recovery detection, and explicit diagnosis codes for missing/late expected scheduled runs).
- State/artifact consistency (missing or malformed files, missing summary/output links, winners-state coherence).
- Roster/config integrity (missing/malformed/empty expected roster).
- Winners + weekly scheduling sanity (expected week/day entries, summary freshness, picks↔winners coherence).
- Top-level operator signal derived from workflow + state dispositions:
  - 🟢 no action needed
  - 🟡 monitor / follow-up (informational/non-urgent only)
  - 🔴 action needed
  - Note: stale workflow freshness is classified as 🔴 with `Disposition: Action required`.
  - Long reports are now automatically split into multiple Discord messages (readable chunk boundaries, hard-capped below Discord's 2000-char content limit).

### Manual recovery / triage playbook

- **Weekly Scheduling Responses Sync is stale**
  1. Re-run `weekly-scheduling-responses-sync.yml`.
  2. If summary drift only: rerun with `rebuild_summary_only=true`.
  3. Confirm `weekly_schedule_summary.json` and `weekly_schedule_bot_outputs.json` update for the target week.
- **Weekly summary missing**
  1. Confirm the week exists in `weekly_schedule_messages.json` and `weekly_schedule_responses.json`.
  2. Run sync with `target_week_key=<week>` and `rebuild_summary_only=true`.
- **Expected weekly schedule post missing**
  1. Re-run `weekly-scheduling-bot.yml` (not responses sync) to post/repair weekly intro/day messages.
  2. Confirm `data/scheduling/weekly_schedule_messages.json` includes the current/next expected week key.
- **Winners state missing**
  1. Confirm picks exist for expected winners day in `discord_daily_posts.json`.
  2. Re-run `evening-winners.yml` with `winners_date_utc=<day>`.
- **Malformed roster**
  1. Repair `data/scheduling/expected_schedule_roster.json` to a valid `users` object with active user entries.
  2. Re-run `weekly-scheduling-responses-sync.yml`.
  3. Confirm the next `bot-health-report.yml` run clears the warning.
- **Daily picks / winners inconsistency**
  1. Verify `discord_daily_posts.json` day entry has valid `items` message IDs.
  2. Re-run `daily.yml` (if items missing/stale) then `evening-winners.yml` for the same day.

### Required secrets / env vars by area (high-level)

- Weekly scheduling post/sync: `DISCORD_SCHEDULING_BOT_TOKEN`, `DISCORD_SCHEDULING_CHANNEL_ID`.
- Daily picks: `DISCORD_WEBHOOK_URL`, `DISCORD_BOT_TOKEN`, `INSTAGRAM_USERNAME`, `INSTAGRAM_SESSION_B64`.
- Evening winners: `DISCORD_BOT_TOKEN`, winners destination channel env (`DISCORD_WINNERS_CHANNEL_ID`).
- Health reporting: `DISCORD_HEALTH_MONITOR_WEBHOOK_URL`.

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
- Summary/reminder outputs now auto-chunk when content grows, with backward-compatible `*_message_id` + `*_message_ids` tracking for rerun-safe edits.

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

Posting behavior in `step-1-vote-on-games-to-test`:
- Intro message
- Section headers:
  - New Demos & Playtests
  - Free Picks
  - Paid Under $20
  - Instagram Creator Picks
- One Discord message per item
- Adds a default 👍 reaction to each posted item
- Instagram creator picks are conservatively deduplicated using a caption-derived normalized game key; if a reliable key cannot be derived from caption text, the post is kept.
- Instagram seen-state is intentionally bounded by `INSTAGRAM_SEEN_RETENTION_PER_CREATOR` (default `50`): only the most recent 50 shortcodes per creator are retained in `instagram_seen.json`, preventing unbounded state growth over time.
- Selection intent (low-risk/quality-first):
  - **New Demos & Playtests** sits above Free Picks and prioritizes friend-group fit (co-op/player-count cues), freshness, and basic legitimacy cues (`demo available`, `request access`, `playtest available`) over random catalog fill.
  - **Free Picks** remain focused on full free and temporarily free full games.
  - The bot prefers quality over maxing section caps (especially for demos/playtests), so some days intentionally post fewer than the section maximum.

Daily routing reference (operator-facing):

| Item Type | Daily Section | Main Selection Philosophy |
| --- | --- | --- |
| `demo` | New Demos & Playtests | Friend-group discovery lane |
| `playtest` | New Demos & Playtests | Friend-group discovery lane |
| `free_game` | Free Picks | Higher-confidence free games |
| `temporarily_free` | Free Picks | Higher-confidence free games |
| `paid_under_20` | Paid Under $20 | Strictest quality gate |

Routing glossary (quick scan):
- **`demo` / `playtest`** → **Demo & Playtest** (`demo_playtest`): friend-group discovery lane.
- **`free_game` / `temporarily_free`** → **Free Picks** (`free`): higher-confidence free/temporarily-free titles.
- **`paid_under_20`** → **Paid Under $20** (`paid`): strictest quality lane.

Daily picks troubleshooting (operator quick guide):
- If **New Demos & Playtests** is unexpectedly empty, check run logs for:
  - `DEMO_PLAYTEST_MIN_FRIEND_SIGNAL` gate misses,
  - low total score vs `MIN_SCORE_TO_POST_DEMO_PLAYTEST`,
  - repost cooldown filtering.
- If the section becomes too noisy, inspect `daily_debug_summary.json` first, then tighten:
  - `DEMO_PLAYTEST_MIN_FRIEND_SIGNAL` (friend-group strictness),
  - `MIN_SCORE_TO_POST_DEMO_PLAYTEST` (overall quality floor).
- If variety feels repetitive, tune only the light diversity knobs (`LIGHT_DIVERSITY_PER_EXTRA_DUPLICATE`, `LIGHT_DIVERSITY_DUPLICATE_FREE_SLOTS`) — this rerank is intentionally weak and should stay secondary to score quality.
- For quality signal tuning, review replayability and legitimacy cue hits in logs/debug JSON before adjusting weights/thresholds.
- At end of each daily run, start with the `RUN SUMMARY` block in logs, then inspect `daily_debug_summary.json` for per-item keep/filtered reasons.
  - `daily_debug_summary.json` is ephemeral: it is overwritten every run and is intended for current-run debugging only (not historical analytics).

`daily_debug_summary.json` mini glossary:
- `final_score`: final scoring output for the candidate.
- `review_sentiment`: parsed Steam review sentiment used in scoring.
- `friend_group_signal`: friend-group fit signal (especially important for demo/playtest).
- `reason_list`: compact keep/filter reasons (for example `weak_review`, `repost_cooldown`).
- `section_order`: canonical section order used for this run.
- `generated_at_utc`: export timestamp.
- `target_day_key`: UTC day key the run targeted.

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
- Posts winners to the channel configured by `DISCORD_WINNERS_CHANNEL_ID` (currently `step-2-test-then-vote-to-keep`)
- Winners intentionally inherit the same section order as daily picks (`demo_playtest`, `free`, `paid`, `instagram`).

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
  - `DISCORD_SCHEDULING_BOT_TOKEN` — required bot token used to post/repair weekly intro/day prompts.
  - `DISCORD_SCHEDULING_CHANNEL_ID` — required channel ID where weekly scheduling prompts are posted.
  - `SCHEDULE_WEEK_START` (optional, `YYYY-MM-DD` Monday) — manual week override for backfill/repair runs.
- Weekly responses sync:
  - `DISCORD_SCHEDULING_BOT_TOKEN` — required bot token used to fetch reactions and post/edit weekly summary/reminders.
  - `TARGET_WEEK_KEY` (optional; workflow input `target_week_key`) — limit sync/rebuild to a specific stored week key.
  - `REBUILD_SUMMARY_ONLY` (optional; workflow input `rebuild_summary_only`) — skip reaction fetch and only rebuild/repair summary output.
  - `DRY_RUN` (optional; workflow input `dry_run`) — preview behavior without mutating Discord messages.
- Daily picks:
  - `DISCORD_WEBHOOK_URL` — required webhook URL used to post daily intro/section/item content.
  - `DISCORD_BOT_TOKEN` — required bot token for reactions, message checks, and daily state linkage.
  - `INSTAGRAM_USERNAME` — required Instagram account username used for session-authenticated scraping.
  - `INSTAGRAM_SESSION_B64` — required base64-encoded Instaloader session content (written to `instaloader.session` by workflow).
- Evening winners:
  - `DISCORD_BOT_TOKEN` — required bot token used to fetch reactions/voters and post winners output.
  - `DISCORD_WINNERS_CHANNEL_ID` — required destination channel ID for winners posts.
- Health monitor notifications (failure pings + daily report):
  - `DISCORD_HEALTH_MONITOR_WEBHOOK_URL` — webhook URL used by workflow failure handlers and `bot-health-report.yml`.

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
