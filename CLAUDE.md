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

## System Architecture

This repo runs a 5-channel Discord pipeline for multiplayer game discovery. Each channel represents a stage in the pipeline:

- `step-1-vote-on-games-to-test` — morning candidate games posted for 👍 voting
- `step-2-test-then-vote-to-keep` — evening winners posted for 🔖 bookmarking
- `step-3-review-existing-games` — persistent playable backlog with ✅/⏸️/❌ reactions
- `update-weekly-schedule-here` — session scheduling and availability coordination
- `xiann-gpt-bot-health-monitor` — workflow health, verification results, and escalation alerts

`channel_specs.json` at the repo root defines the correct output spec for each channel (required fields, reactions, failure conditions). Always read it before diagnosing or fixing any Discord-related issue.

## Automation Loop

`auto-fix.yml` triggers automatically whenever the daily workflow completes. It fires a fix attempt when either `daily_verification.json` or `discord_verification.json` reports `pass: false`.

- Claude Code is the fixer — it reads both verification artifacts and `channel_specs.json` to diagnose the root cause before making any change
- Fix branches are named `fix/auto-fix-{workflow-run-id}-{attempt}` (e.g. `fix/auto-fix-12345678-1`)
- PRs are auto-merged when checks pass
- Maximum 3 attempts; if all fail, an escalation alert is posted to `xiann-gpt-bot-health-monitor`

## Key Files

| File | Purpose |
|------|---------|
| `channel_specs.json` | Per-channel correctness spec — required fields, reactions, and failure conditions |
| `daily_verification.json` | Runtime artifact from `main.py` — structural checks on what was posted |
| `discord_verification.json` | Runtime artifact from `scripts/verify_discord_output.py` — live Discord read-back checks |
| `discord_daily_posts.json` | Message ID state store — maps each day's posts to their Discord message and channel IDs |
| `data/gaming_library.json` | Library state — the persistent backlog of games tracked in `step-3-review-existing-games` |
