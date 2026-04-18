# steam-discord-free-games

[![Steam Free Games](https://github.com/XiannGoh/steam-discord-free-games/actions/workflows/daily.yml/badge.svg)](https://github.com/XiannGoh/steam-discord-free-games/actions/workflows/daily.yml)
[![Daily Game Picks Winners](https://github.com/XiannGoh/steam-discord-free-games/actions/workflows/evening-winners.yml/badge.svg)](https://github.com/XiannGoh/steam-discord-free-games/actions/workflows/evening-winners.yml)
[![Weekly Scheduling Bot](https://github.com/XiannGoh/steam-discord-free-games/actions/workflows/weekly-scheduling-bot.yml/badge.svg)](https://github.com/XiannGoh/steam-discord-free-games/actions/workflows/weekly-scheduling-bot.yml)
[![Weekly Scheduling Responses Sync](https://github.com/XiannGoh/steam-discord-free-games/actions/workflows/weekly-scheduling-responses-sync.yml/badge.svg)](https://github.com/XiannGoh/steam-discord-free-games/actions/workflows/weekly-scheduling-responses-sync.yml)
[![Bot Health Report](https://github.com/XiannGoh/steam-discord-free-games/actions/workflows/bot-health-report.yml/badge.svg)](https://github.com/XiannGoh/steam-discord-free-games/actions/workflows/bot-health-report.yml)

A fully automated, self-healing Discord bot pipeline for multiplayer Steam game discovery. Runs daily, fixes itself when things break, and posts picks to a 5-channel Discord server.

## What it does

**Step 1 — Daily Picks** (`step-1-vote-on-games-to-test`)
Scrapes Steam daily for free games, demos, playtests, and paid games under $20. Scores and filters them for multiplayer fit. Posts picks every morning at 9AM ET with 👍 voting. All voted games move to Step 2.

**Step 2 — Daily Winners** (`step-2-test-then-vote-to-keep`)
Every evening, posts the day's Step 1 winners. Members 🔖 bookmark games they want to keep permanently. Bookmarked games move to Step 3.

**Step 3 — Gaming Library** (`step-3-review-existing-games`)
A persistent backlog of bookmarked games. Updated daily with delta summaries showing what changed. Members react with ✅ active · ⏸️ paused · ❌ dropped.

**Weekly Scheduling** (`update-weekly-schedule-here`)
Every Saturday, posts an availability prompt for the coming week. Members select their available time slots. The bot syncs responses every 3 hours and posts a summary with @mentions for missing members.

**Health Monitor** (`xiann-gpt-bot-health-monitor`)
Every workflow posts its status here. Failures trigger auto-fix. Daily health report summarizes everything.

## How it works

- **Scoring model** — games are scored on review sentiment, multiplayer tags, recency, and friend signal. Only games above a minimum score threshold are posted.
- **Validator** — after every post, a validator checks Discord output against a spec. If anything is wrong it triggers auto-fix.
- **Auto-fix loop** — Claude Code automatically diagnoses failures, opens PRs, and merges fixes. Up to 3 attempts per failure with rollback if a fix makes things worse.
- **Watchdog** — runs hourly. If any scheduled workflow missed its run, the watchdog re-triggers it. Capped at 3 re-triggers per workflow per day.
- **Pattern analysis** — runs every Sunday. Reviews failure patterns from the week and suggests improvements.
- **State backup** — all state files backed up daily to a separate branch.

## Workflows

| Workflow | Schedule | Purpose |
|----------|----------|---------|
| `daily.yml` | 9AM ET daily | Steam scraping + Step 1 picks |
| `evening-winners.yml` | 7PM ET daily | Step 2 winners |
| `gaming-library-daily.yml` | 8PM ET daily | Step 3 library update |
| `gaming-library-sync.yml` | Every 3 hours | Sync library reactions |
| `weekly-scheduling-bot.yml` | Saturday 9AM ET | Weekly availability prompt |
| `weekly-scheduling-responses-sync.yml` | Every 3 hours | Sync availability responses |
| `bot-health-report.yml` | 11PM ET daily | Daily health summary |
| `watchdog.yml` | Every hour | Re-trigger missed workflows |
| `auto-fix.yml` | On failure | Self-healing fix loop |
| `pattern-analysis.yml` | Sunday midnight | Weekly pattern review |
| `state-backup.yml` | 11:45PM ET daily | Back up state files |

## Key files

- `main.py` — Steam scraping, scoring, Step 1 posting
- `evening_winners.py` — Step 2 winners
- `gaming_library.py` — Step 3 library
- `discord_api.py` — Discord REST client
- `channel_specs.json` — per-channel validation spec
- `CLAUDE.md` — Claude Code constitution
- `data/health_monitor_log.json` — failure tracking
- `daily_section_config.py` — section config and order

## Required secrets

| Secret | Purpose |
|--------|---------|
| `DISCORD_BOT_TOKEN` | Main bot token |
| `DISCORD_SCHEDULING_BOT_TOKEN` | Scheduling bot token |
| `DISCORD_WEBHOOK_URL` | Step 1 webhook |
| `DISCORD_GUILD_ID` | Server ID |
| `DISCORD_STEP1_CHANNEL_ID` | Step 1 channel |
| `DISCORD_WINNERS_CHANNEL_ID` | Step 2 channel |
| `DISCORD_GAMING_LIBRARY_CHANNEL_ID` | Step 3 channel |
| `DISCORD_SCHEDULING_CHANNEL_ID` | Scheduling channel |
| `DISCORD_HEALTH_MONITOR_CHANNEL_ID` | Health monitor channel |
| `DISCORD_HEALTH_MONITOR_WEBHOOK_URL` | Health monitor webhook |
| `DISCORD_DEBUG_CHANNEL_ID` | Debug channel |
| `INSTAGRAM_USERNAME` | Instagram username |
| `INSTAGRAM_SESSION_B64` | Instagram session |
| `CLAUDE_CODE_OAUTH_TOKEN` | Claude Code token |
