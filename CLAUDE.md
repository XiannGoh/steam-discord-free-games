# Repo Rules

- Always review recent merged PRs before making changes.
- Extra pre-flight steps in daily.yml (such as Restore Instagram session, Sanity-check state files) are intentional and must not be removed.
- Prefer patterns already used in the repo unless a newer explicit rule in this file supersedes an older pattern.
- Never create duplicate Discord messages if an existing message can be reused or edited.
- Preserve idempotency and same-day rerun safety.
- Re-triggers for the same day must edit or reuse existing messages. They must never post new intro, footer, or item messages alongside already-tracked messages for the same logical post.
- Run targeted tests before suggesting a change.
- Prefer editing existing messages over recreating them.
- For Discord workflow changes, always preserve:
  - exactly one intro message per day per channel post
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
- All save state steps in workflow files must use `git stash --include-untracked || true` before `git pull --rebase` and `git stash pop || true` after, to prevent unstaged changes from causing rebase failures.
- Future work should support:
  - debug summaries
  - verification JSON artifacts
  - autonomous rerun-safe behavior

## Content Rules

- **VR exclusion** â games flagged as VR (via `is_vr_content()` in `main.py`) are excluded from daily picks. Check both tag matches (`VR_TAG_EXACT`) and phrase matches (`VR_INDICATOR_PHRASES`) in title/description.
- **DLC hard exclusion** â items detected as DLC are always excluded, no exceptions.
- **Instagram 7-day filter** â posts older than 7 days (`INSTAGRAM_MAX_POST_AGE_DAYS = 7`) are skipped during Instagram fetch. Posts are newest-first; stop iterating when a post exceeds the cutoff.
- **Steam URL embed suppression** â all Steam store URLs in Discord messages must be wrapped in `<>` (e.g. `<https://store.steampowered.com/app/123/>`). Use `_suppress_steam_url()` from `gaming_library.py`.
- **ET time formatting** â all human-readable timestamps in Discord messages that include time must use `format_et_timestamp()` from `state_utils.py`. Format: `Dec 15, 2024 at 7:00 AM ET`.
- **Full-date header formatting** â daily intro and footer messages that show only a date must use a full date string such as `Wednesday, April 15, 2026`. Use a dedicated helper function for this â do not use `format_et_timestamp()` for date-only strings.
- **Demo/playtest source gating** â paid games that mention "demo" or "playtest" in title/text are excluded (`detect_item_type()` in `main.py`). Free items pass through.
- **Demo/playtest freshness cutoff** â demo and playtest items in Step 1 must be excluded if their release date is older than 180 days.
- **Demo/playtest review hard exclusion** â demo and playtest items must be excluded if their review sentiment is one of: Overwhelmingly Negative, Very Negative, Mostly Negative, Negative. Reuse `HARD_EXCLUDE_REVIEW_SENTIMENTS` where possible.
- **Instagram unavailable-title filtering** â Instagram picks must be excluded if the caption indicates the item is not yet available, including phrases such as: coming soon, not yet available, wishlist now, Coming 2025, Coming 2026, Coming 2027.

## Discord Message Format Rules

### Emoji Standard (mandatory â never change)
These emojis are standardized across all steps and must never be changed:
- Free Picks â ð
- Demos & Playtests â ð®
- Paid Under $20 â ð°
- Instagram/Creator Picks â ð¸

Must be consistent across:
- Section headers in daily_section_config.py
- build_winners_section_header() in evening_winners.py
- CATEGORY_DISPLAY in gaming_library.py
- All intro and footer section label dicts

### Step 1 â `step-1-vote-on-games-to-test`

- Step 1 must have a single unified intro message.
- The old separate Step 1 `header` + `intro` pattern is obsolete.
- The old `header` state key must be renamed to `intro`.
- The separate one-line intro message must be removed.
- The Step 1 intro must be posted once, then edited in place after section links are known.
- Same-day reruns must edit or reuse the existing Step 1 intro and footer instead of creating new ones.

Required Step 1 intro format:

```
ð Daily Picks â Wednesday, April 15, 2026

Vote ð on anything you want to try. All voted games move to Step 2.

ð [Free Picks](...)
ð® [Demos & Playtests](...)
ð° [Paid Under $20](...)
ð¸ [Instagram Picks](...)

âââââââââââââââââââââââââââââââââââââââââ
```

Placeholder version before all sections are posted:

```
ð Daily Picks â Wednesday, April 15, 2026

Vote ð on anything you want to try. All voted games move to Step 2.

Loading sections...

âââââââââââââââââââââââââââââââââââââââââ
```

Rules:
- Exactly one voting instruction line
- Jump links must be vertical, one per line
- Only include section links for sections that actually have content that day
- The divider line must be the last line of the intro
- The Step 1 intro content must not contain Steam item card content

Required Step 1 footer format:

```
ð End of Daily Picks â Wednesday, April 15, 2026 Â· Jump to: ð Free Â· ð® Demos Â· ð° Paid Â· ð¸ Instagram Â· â¬ï¸ Top
_(No Demos & Playtests today)_  â only shown if that category is missing
âââââââââââââââââââ End of Daily Picks âââââââââââââââââââ
```

Rules:
- Footer must be lean and must not duplicate the intro body
- Only include sections that actually exist that day
- Must include a Top jump link to the intro message
- Must end with End of Daily Picks
- First line must start with "ð End of Daily Picks â"
- Missing categories must appear as "_(No X today)_" between the jump links line and the separator
- Separator must always be the last line

### Intro placeholder rules (mandatory)
- The intro placeholder must NEVER be re-posted or re-edited if intro_state already has a message_id
- Only post the placeholder on the very first run when message_id is absent
- On re-runs, skip directly to the jump links edit
- This applies to both main.py Step 1 intro and gaming_library.py Step 3 intro

### Missing category notices (mandatory)
Both intros AND footers must show notices for categories that have no content that day:
- Intro format: "_(No {Category} today)_"
- Footer format Step 1: "_(No {Category} today)_"
- Footer format Step 2: "_(No {Category} Winners today)_"
- Footer format Step 3: "_(No {Category} in library)_"
- Notices are dynamic â computed at runtime from which sections actually have content
- Never hardcode which categories are missing

### Step 2 â `step-2-test-then-vote-to-keep`

- Step 2 footer must be lean and must not be a copy of the header or intro.
- Same-day reruns must edit or reuse the existing Step 2 intro and footer instead of creating new ones.

Required Step 2 footer format:

```
ð End of Daily Winners â Wednesday, April 15, 2026 Â· Jump to: ð Free Â· ð® Demo & Playtest Â· ð° Paid Â· ð¸ Creator Â· â¬ï¸ Top
_(No Demo & Playtest Winners today)_  â only shown if that category is missing
âââââââââââââââââââ End of Daily Winners âââââââââââââââââââ
```

Rules:
- First line contains date and jump links
- Second line (if any categories are missing) contains missing category notices
- Final line contains only the end separator
- Must include a Top jump link to the intro message
- Only include sections that actually have winners that day
- Must end with End of Daily Winners
- First line must start with "ð End of Daily Winners â"
- Missing winner categories must appear as "_(No X Winners today)_" between jump links and separator

### Step 3 â `step-3-review-existing-games`

- Step 3 intro must use a full date format.
- The daily delta summary must be inside the intro message, not as a separate message.
- Step 3 must have a footer.
- Same-day reruns must edit or reuse the existing Step 3 intro and footer instead of creating new ones.

Required Step 3 intro format:

```
ð Gaming Library â Wednesday, April 15, 2026
React on each game: â active Â· â¸ï¸ paused Â· â dropped
...jump links if present...

âââââââââââââââââââââââââââââââââââââââââ
ð Today's Changes
- ...
- ...
âââââââââââââââââââââââââââââââââââââââââ
```

If there are no changes, the delta block must contain:
```
â¢ No changes since yesterday
```

Required Step 3 footer format:

```
ð End of Gaming Library â Wednesday, April 15, 2026 Â· Jump to: ð° Paid Â· ð¸ Creator Â· â¬ï¸ Top
_(No Demo & Playtest in library)_  â only shown if that category is missing
âââââââââââââââââââ End of Gaming Library âââââââââââââââââââ
```

Rules:
- Footer must include Top jump link to the intro message
- Must end with End of Gaming Library
- First line must start with "ð End of Gaming Library â"
- Missing categories must appear as "_(No X in library)_" between jump links and separator
- Separator must always be the last line of the footer

## System Architecture

This repo runs a 5-channel Discord pipeline for multiplayer game discovery. Each channel represents a stage in the pipeline:

- `step-1-vote-on-games-to-test` â morning candidate games posted for ð voting
- `step-2-test-then-vote-to-keep` â evening winners posted for ð bookmarking
- `step-3-review-existing-games` â persistent playable backlog with â/â¸ï¸/â reactions and bot commands
- `update-weekly-schedule-here` â session scheduling and availability coordination
- `xiann-gpt-bot-health-monitor` â workflow health, verification results, and escalation alerts

`channel_specs.json` at the repo root defines the correct output spec for each channel (required fields, reactions, failure conditions). Always read it before diagnosing or fixing any Discord-related issue.

## Workflow Execution Order Rules

### `daily.yml`
Order must be:
1. install packages
2. `python main.py` (posts picks + rolling explainer at end)
3. `python scripts/read_discord_channel.py`
4. `python scripts/verify_discord_output.py`
5. upload artifacts
6. save state
7. health monitor failure notification

### `evening-winners.yml`
Order must be:
1. install packages
2. `python evening_winners.py` (posts winners + rolling explainer at end)
3. `python scripts/read_discord_channel.py`
4. `python scripts/verify_discord_output.py`
5. upload artifacts
6. save state
7. health monitor failure notification

### `gaming-library-daily.yml`
Order must be:
1. install packages
2. `python scripts/post_daily_gaming_library.py` (posts library + rolling explainer at end)
3. `python scripts/read_discord_channel.py`
4. `python scripts/verify_discord_output.py`
5. upload artifacts
6. save state
7. health monitor failure notification

### `weekly-scheduling-bot.yml`
Order must be:
1. install packages
2. `scripts/ensure_pinned_messages.py`
3. `python scripts/post_weekly_availability.py`
4. `python scripts/read_discord_channel.py`
5. `python scripts/verify_weekly_schedule.py`
6. upload artifacts
7. save state
8. health monitor failure notification

### Pinned-message workflow requirements
`scripts/ensure_pinned_messages.py` must run at the start of:
- `weekly-scheduling-bot.yml`

Rolling explainer (last-message how-it-works) is handled by each posting script directly:
- `main.py` posts the Step 1 rolling explainer at the end of `run_daily_workflow()`
- `evening_winners.py` posts the Step 2 rolling explainer at all exit points of `main()`
- `scripts/post_daily_gaming_library.py` posts the Step 3 rolling explainer after `run_daily_post()`

### Snapshot workflow requirements
`scripts/read_discord_channel.py` must run after every posting workflow and before verification. The following snapshot files must be uploaded as a single artifact named `discord-snapshots-{run_id}`:
- `data/snapshot_step1.json`
- `data/snapshot_step2.json`
- `data/snapshot_step3.json`
- `data/snapshot_schedule.json`
- `data/snapshot_health.json`

### Section header state cleanup (mandatory)
Before the section posting loop in post_daily_pick_messages(), always prune stale section_headers from run_state that are not in posted_section_keys for today's run.
This prevents ghost section headers from previous runs appearing in jump links.

## Step 3 Discord Commands

Players can type commands in `step-3-review-existing-games`. Commands are processed on the next sync run (`gaming-library-sync.yml`). The bot reacts with â on each processed command.

| Command | Effect |
|---------|--------|
| `!add @user GameName` | Assign player to a game |
| `!remove @user GameName` | Unassign player from a game |
| `!rename GameName NewName` | Rename a game |
| `!unassign @user` | Remove player from all games |
| `!archive GameName` | Archive a game |
| `!addgame GameName SteamURL @user1 @user2` | Add game directly to library |

Processed command message IDs are tracked in `gaming_library.json` under `processed_command_ids` to prevent reprocessing. The pinned command reference message is tracked under `command_reference_message`.

## Verification Rules

- `scripts/verify_discord_output.py` must verify Steps 1, 2, and 3.
- Step 1 verification must include:
  - intro exists
  - intro ends with the divider line âââââââââââââââââââââââââââââââââââââââââ
  - footer exists
  - footer ends with the exact line: âââââââââââââââââââ End of Daily Picks âââââââââââââââââââ
  - intro does not contain Steam URLs
  - demo/playtest items are not older than 180 days
  - rolling explainer (starting with "ð How This Works") is the last message in the channel
- Step 2 verification must include:
  - intro exists
  - footer exists
  - footer ends with the exact line: âââââââââââââââââââ End of Daily Winners âââââââââââââââââââ
  - intro does not contain Steam URLs
  - rolling explainer (starting with "ð How This Works") is the last message in the channel
- Step 3 verification must include:
  - intro exists
  - footer exists
  - footer ends with the exact line: âââââââââââââââââââ End of Gaming Library âââââââââââââââââââ
  - intro contains either ð Today's Changes or No changes since yesterday
  - delta summary is inside the intro message, not posted as a separate Discord message
  - game cards satisfy min_items rules from channel_specs.json
  - rolling explainer (starting with "ð How This Works") is the last message in the channel

## Automation Loop

`auto-fix.yml` triggers automatically when any of these workflows complete: Steam Free Games, Daily Game Picks Winners, Gaming Library Daily Reminder, Gaming Library Sync, Weekly Scheduling Responses Sync. It fires a fix attempt when any relevant verification artifact reports pass: false or when the triggering workflow itself fails.

- Claude Code is the fixer â it reads verification artifacts, Discord snapshot artifacts, and channel_specs.json to diagnose the root cause before making any change
- `auto-fix.yml` must download Discord snapshot artifacts (`discord-snapshots-*`) before invoking Claude Code so Claude Code can read the actual Discord output
- Fix branches are named fix/auto-fix-{workflow-run-id}-{attempt}
- PRs are auto-merged when checks pass
- Maximum 3 attempts; if all fail, an escalation alert is posted to xiann-gpt-bot-health-monitor
- Success notifications must be posted to the health monitor channel when an auto-fix succeeds
- If the bot encounters a 403 Forbidden error when attempting to pin a message, it must NOT treat this as a code bug. Instead it must:
  - Post a warning to `xiann-gpt-bot-health-monitor` explaining which channel is missing Manage Messages permission
  - Continue execution without failing the workflow
  - The auto-fix loop must NOT attempt to fix 403 permission errors via code changes

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
| `watchdog.yml` | Missed-Run Watchdog | Every hour |
| `auto-fix.yml` | Auto-Fix | Triggered by workflow_run |

Every workflow has a Notify Discord health monitor on failure step that posts to DISCORD_HEALTH_MONITOR_WEBHOOK_URL on failure.

## Claude Code Operating Rules

Rules for Claude Code when working on issues in this repo. These apply to every PR.

### Scope
- Fix only what the issue explicitly asks. If you notice a separate bug, open a new GitHub issue describing it — do not fix it in the same PR.
- Do not refactor, rename, or restructure code unrelated to the issue.
- Do not update CLAUDE.md, channel_specs.json, or verify_discord_output.py unless the issue explicitly requires it.
- If you cannot complete the full task (e.g. workflow file push blocked), commit what you can, open the PR, and leave a comment explaining exactly what remains and why.

### Code quality
- Remove all debug print statements and temporary investigation code before committing. Only keep logging that belongs in production.
- Every new module-level file must have a docstring explaining its purpose.
- Every new public function must have a docstring.
- Do not duplicate logic that already exists in state_utils.py, discord_api.py, or other shared helpers — reuse them.

### Environment variables
- If your Python change reads a new env var (os.getenv), check whether it is already exported in the relevant workflow .yml env block. If it is missing, note it explicitly in your PR comment — you cannot modify workflow files directly, but the gap must be flagged.
- Never hardcode secrets, tokens, or channel IDs. Always use os.getenv with the existing secret names.

### Testing
- Run `python -m pytest tests/ -x -q` before pushing. All tests must pass. Do not open a PR with failing tests.
- New behavior must have at least one test. Test the outcome, not that a function was called.
- If tests fail for a reason unrelated to your change, report it in a comment rather than fixing it silently.

### Push discipline
- Always use `git stash --include-untracked || true` before `git pull --rebase` and `git stash pop || true` after, to avoid rebase conflicts with concurrent state commits.
- If a push fails due to conflict, retry once after pulling. If it fails again, leave a comment on the issue explaining the conflict — do not force push.

### When NOT to act
- Do not attempt to fix issues that appear in the health monitor if a PR was merged in the last 24 hours that should resolve them. Wait for a real workflow run to validate first.
- Do not re-open or re-attempt work that a previous Claude Code run already completed and pushed. Check existing branches before starting.
- If the issue is ambiguous or contradicts rules in this file, post a clarifying comment on the issue rather than guessing.

## Key Files

| File | Purpose |
|------|---------|
| `main.py` | Step 1 â Steam scraping, scoring model, demo/playtest/VR detection, Instagram fetch |
| `evening_winners.py` | Step 2 â winners channel management with footer jump links |
| `gaming_library.py` | Step 3 â library state, daily post, Discord command processing, sync |
| `state_utils.py` | Shared helpers: atomic JSON writes, ET timestamp helpers, prune utilities |
| `discord_api.py` | Discord REST client â post, edit, react, pin, get channel messages |
| `daily_section_config.py` | Section definitions for Step 1 daily post categories |
| `channel_specs.json` | Per-channel correctness spec â required fields, reactions, and failure conditions |
| `daily_verification.json` | Runtime artifact from main.py â structural checks on what was posted |
| `discord_verification.json` | Runtime artifact from scripts/verify_discord_output.py â live Discord read-back checks |
| `discord_daily_posts.json` | Message ID state store â maps each day's posts to their Discord message and channel IDs |
| `gaming_library.json` | Library state â persistent backlog of games tracked in step-3-review-existing-games |
| `seen_ids.json` | Deduplication store for Step 1 Steam items |
| `instagram_seen.json` | Deduplication store for Instagram posts |
| `page_state.json` | Pagination state for Steam free games scraper |
| `state_sanity.json` | Output of scripts/check_state_sanity.py â cross-file state consistency checks |
| `data/pinned_messages.json` | Pinned-message state store by channel slug |
| `data/instagram_fetch_summary.json` | Instagram fetch summary state |
| `data/snapshot_step1.json` | Discord snapshot artifact for Step 1 |
| `data/snapshot_step2.json` | Discord snapshot artifact for Step 2 |
| `data/snapshot_step3.json` | Discord snapshot artifact for Step 3 |
| `data/snapshot_schedule.json` | Discord snapshot artifact for weekly scheduling |
| `data/snapshot_health.json` | Discord snapshot artifact for health monitor |
| `data/health_monitor_log.json` | Failure tracking database for recursive self-healing loop |

## Key Scripts

| Script | Purpose |
|--------|---------|
| `scripts/sync_gaming_library.py` | Syncs Step 2 bookmarks â Step 3 library, reads reactions, processes !commands |
| `scripts/post_daily_gaming_library.py` | Posts daily Step 3 library reminder with intro/footer and embedded delta summary |
| `scripts/build_daily_health_report.py` | Builds the bot health report with workflow schedule diagnostics |
| `scripts/verify_discord_output.py` | Live Discord read-back verification against channel_specs.json for Steps 1, 2, and 3 |
| `scripts/verify_gaming_library.py` | Structural verification of gaming_library.json |
| `scripts/check_state_sanity.py` | Cross-file state consistency checks |
| `scripts/manage_gaming_library.py` | CLI tool for manual library operations |
| `scripts/read_discord_channel.py` | Reads Discord channels and writes snapshot artifacts for all 5 channels |
| `scripts/ensure_pinned_messages.py` | Ensures pinned how-it-works messages exist and are updated in all channels |
| `scripts/update_health_log.py` | Reads and writes the failure tracking database for the health monitor loop |
