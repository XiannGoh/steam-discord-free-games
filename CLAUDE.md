# Repo Rules

- Always review recent merged PRs before making changes.
- Prefer patterns already used in the repo over inventing new ones.
- Never create duplicate Discord messages if an existing message can be reused or edited.
- Preserve idempotency and same-day rerun safety.
- Run targeted tests before suggesting a change.
- Prefer editing existing messages over recreating them.
- For Discord workflow changes, always preserve:
  - intro message
  - section headers
  - one message per item
  - footer
- Never remove reactions from existing messages unless explicitly required.
- Only add reactions when a message is newly created.
- Before making changes, summarize:
  - files to edit
  - likely risks
  - tests to run
- After making changes, summarize:
  - exactly what changed
  - what tests passed
  - any remaining risks
- Never merge directly to main.
- Always create a new branch and PR.
- Future work should support:
  - debug summaries
  - verification JSON artifacts
  - autonomous rerun-safe behavior

## Content Rules

- **VR exclusion** — games flagged as VR (via `is_vr_content()` in `main.py`) are excluded from daily picks. Check both tag matches (`VR_TAG_EXACT`) and phrase matches (`VR_INDICATOR_PHRASES`) in title/description.
- **DLC hard exclusion** — items detected as DLC are always excluded, no exceptions.
- **Instagram 7-day filter** — posts older than 7 days (`INSTAGRAM_MAX_POST_AGE_DAYS = 7`) are skipped during Instagram fetch. Posts are newest-first; stop iterating when a post exceeds the cutoff.
- **Steam URL embed suppression** — all Steam store URLs in Discord messages must be wrapped in `<>` (e.g. `<https://store.steampowered.com/app/123/>`). Use `_suppress_steam_url()` from `gaming_library.py`.
- **ET timestamps** — all human-readable timestamps in Discord messages must use `format_et_timestamp()` from `state_utils.py`. Format: `Dec 15, 2024 at 7:00 AM ET`.
- **Demo/playtest source gating** — paid games that mention "demo" or "playtest" in title/text are excluded (`detect_item_type()` in `main.py`). Free items pass through.

## System Architecture

This repo runs a 5-channel Discord pipeline for multiplayer game discovery. Each channel represents a stage in the pipeline:

- `step-1-vote-on-games-to-test` — morning candidate games posted for 👍 voting
- `step-2-test-then-vote-to-keep` — evening winners posted for 🔖 bookmarking
- `step-3-review-existing-games` — persistent playable backlog with ✅/⏸️/❌ reactions and bot commands
- `update-weekly-schedule-here` — session scheduling and availability coordination
- `xiann-gpt-bot-health-monitor` — workflow health, verification results, and escalation alerts

`channel_specs.json` at the repo root defines the correct output spec for each channel (required fields, reactions, failure conditions). Always read it before diagnosing or fixing any Discord-related issue.

## Step 3 Discord Commands

Players can type commands in `step-3-review-existing-games`. Commands are processed on the next sync run (`gaming-library-sync.yml`). The bot reacts with ✅ on each processed command.

| Command | Effect |
|---------|--------|
| `!add @user GameName` | Assign player to a game |
| `!remove @user GameName` | Unassign player from a game |
| `!rename GameName NewName` | Rename a game |
| `!unassign @user` | Remove player from all games |
| `!archive GameName` | Archive a game |
| `!addgame GameName SteamURL @user1 @user2` | Add game directly to library |

Processed command message IDs are tracked in `gaming_library.json` under `processed_command_ids` to prevent reprocessing. The pinned command reference message is tracked under `command_reference_message`.

## Automation Loop

`auto-fix.yml` triggers automatically when any of these workflows complete: **Steam Free Games**, **Daily Game Picks Winners**, **Gaming Library Daily Reminder**. It fires a fix attempt when either `daily_verification.json` or `discord_verification.json` reports `pass: false`.

- Claude Code is the fixer — it reads both verification artifacts and `channel_specs.json` to diagnose the root cause before making any change
- Fix branches are named `fix/auto-fix-{workflow-run-id}-{attempt}` (e.g. `fix/auto-fix-12345678-1`)
- PRs are auto-merged when checks pass
- Maximum 3 attempts; if all fail, an escalation alert is posted to `xiann-gpt-bot-health-monitor`

## Workflow Schedule (all times ET)

| Workflow file | Name | Schedule |
|---|---|---|
| `daily.yml` | Steam Free Games (Step 1) | 9:00 AM daily |
| `evening-winners.yml` | Daily Game Picks Winners (Step 2) | 7:00 PM daily |
| `gaming-library-sync.yml` | Gaming Library Sync | Every 3 hours |
| `gaming-library-daily.yml` | Gaming Library Daily Reminder (Step 3) | 8:00 PM daily |
| `weekly-scheduling-bot.yml` | Weekly Scheduling Bot | 9:00 AM Saturday |
| `weekly-scheduling-responses-sync.yml` | Weekly Scheduling Responses Sync | Every 3 hours |
| `bot-health-report.yml` | Bot Health Report | 11:00 PM daily |
| `daily-briefing.yml` | Daily System Briefing | 11:30 PM daily |
| `watchdog.yml` | Missed-Run Watchdog | Every hour |
| `auto-fix.yml` | Auto-Fix | Triggered by workflow_run |

Every workflow has a `Notify Discord health monitor on failure` step that posts to `DISCORD_HEALTH_MONITOR_WEBHOOK_URL` on failure.

## Key Files

| File | Purpose |
|------|---------|
| `main.py` | Step 1 — Steam scraping, scoring model, demo/playtest/VR detection, Instagram fetch |
| `evening_winners.py` | Step 2 — winners channel management with header/footer jump links |
| `gaming_library.py` | Step 3 — library state, daily post, Discord command processing, sync |
| `state_utils.py` | Shared helpers: atomic JSON writes, `format_et_timestamp()`, prune utilities |
| `discord_api.py` | Discord REST client — post, edit, react, pin, get channel messages |
| `daily_section_config.py` | Section definitions for Step 1 daily post categories |
| `channel_specs.json` | Per-channel correctness spec — required fields, reactions, and failure conditions |
| `daily_verification.json` | Runtime artifact from `main.py` — structural checks on what was posted |
| `discord_verification.json` | Runtime artifact from `scripts/verify_discord_output.py` — live Discord read-back checks |
| `discord_daily_posts.json` | Message ID state store — maps each day's posts to their Discord message and channel IDs |
| `gaming_library.json` | Library state — persistent backlog of games tracked in `step-3-review-existing-games` |
| `seen_ids.json` | Deduplication store for Step 1 Steam items |
| `instagram_seen.json` | Deduplication store for Instagram posts |
| `page_state.json` | Pagination state for Steam free games scraper |
| `state_sanity.json` | Output of `scripts/check_state_sanity.py` — cross-file consistency checks |

## Key Scripts

| Script | Purpose |
|--------|---------|
| `scripts/sync_gaming_library.py` | Syncs Step 2 bookmarks → Step 3 library, reads reactions, processes !commands |
| `scripts/post_daily_gaming_library.py` | Posts daily Step 3 library reminder with category grouping and jump links |
| `scripts/build_daily_health_report.py` | Builds the bot health report with workflow schedule diagnostics |
| `scripts/build_daily_briefing.py` | Builds the daily system briefing with 24h run summary |
| `scripts/verify_discord_output.py` | Live Discord read-back verification against `channel_specs.json` |
| `scripts/verify_gaming_library.py` | Structural verification of `gaming_library.json` |
| `scripts/check_state_sanity.py` | Cross-file state consistency checks |
| `scripts/manage_gaming_library.py` | CLI tool for manual library operations |
