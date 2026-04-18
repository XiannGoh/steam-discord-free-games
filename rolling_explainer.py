"""Post or edit-in-place a rolling explainer as the last message in each channel.

Called at the end of each posting script (main.py for Step 1, evening_winners.py
for Step 2, post_daily_gaming_library.py for Step 3). Same-day reruns detect the
prefix and edit instead of posting a new message, preventing duplicates.
"""

from datetime import date

from discord_api import DiscordClient

ROLLING_EXPLAINER_PREFIX = "📌 How This Works"

# Three weekly-rotating variants per step. Rotate by ISO week number so the
# variant is stable for the whole week and changes predictably.
ROLLING_CONTENT: dict[str, list[str]] = {
    "step-1": [
        """\
📌 How This Works — #step-1-vote-on-games-to-test

Every morning the bot posts fresh game picks for the group to vote on.
Vote on any game you want to try tonight. All voted games move to Step 2 in the evening.

New picks are posted every morning.""",

        """\
📌 How This Works — #step-1-vote-on-games-to-test

Fresh picks land here every morning — free games, demos, and paid-under-$20 finds.
React 👍 on anything that looks fun. All voted games move to Step 2 in the evening.

Check back each morning for a new batch.""",

        """\
📌 How This Works — #step-1-vote-on-games-to-test

The bot scans Steam every morning and posts the best picks here.
👍 Vote on what interests you — all voted games move to Step 2 in the evening.

New picks arrive every morning. No account needed to browse.""",
    ],

    "step-2": [
        """\
📌 How This Works — #step-2-test-then-vote-to-keep

Every evening the bot posts the day's winners from #step-1-vote-on-games-to-test.
🔖 Bookmark any game you want to keep in the permanent library.
Bookmarked games move to #step-3-review-existing-games.

Winners are posted every evening.""",

        """\
📌 How This Works — #step-2-test-then-vote-to-keep

Games that earned votes in Step 1 appear here each evening.
React 🔖 on anything you want to keep. Bookmarked games move to #step-3-review-existing-games.

Check back each evening for the day's top picks.""",

        """\
📌 How This Works — #step-2-test-then-vote-to-keep

Each evening the bot promotes voted picks here from Step 1.
🔖 Bookmark the games you want to test. Bookmarked games move to #step-3-review-existing-games.

Miss today's votes? The last 10 days of picks are always available.""",
    ],

    "step-3": [
        """\
📌 How This Works — #step-3-review-existing-games

This is your group's permanent gaming library.
✅ Active — you want to play this
⏸️ Paused — taking a break
❌ Dropped — no longer interested

Manage the library with commands (the bot reacts ✅ when done):
!addgame GameName SteamURL @user1 @user2
!add @user GameName · !remove @user GameName
!unassign @user · !rename GameName NewName · !archive GameName""",

        """\
📌 How This Works — #step-3-review-existing-games

Your permanent backlog of group-approved games. React to update your status:
✅ Active · ⏸️ Paused · ❌ Dropped

Bot commands (processed periodically, bot reacts ✅):
!add @user GameName · !remove @user GameName
!addgame GameName SteamURL @user1 @user2
!archive GameName · !rename GameName NewName · !unassign @user""",

        """\
📌 How This Works — #step-3-review-existing-games

The living record of games your group has tested and kept.
React on each game: ✅ active · ⏸️ paused · ❌ dropped

Commands to edit the library (bot reacts ✅ when processed):
!add @user GameName · !remove @user GameName · !unassign @user
!addgame GameName SteamURL @user1 @user2
!rename GameName NewName · !archive GameName""",
    ],
}


def get_rolling_content(slug: str, *, _today: date | None = None) -> str:
    """Return the weekly-rotating variant for the given channel slug."""
    variants = ROLLING_CONTENT.get(slug)
    if not variants:
        raise KeyError(f"No rolling content defined for slug: {slug!r}")
    today = _today or date.today()
    idx = (today.toordinal() // 7) % len(variants)
    return variants[idx]


def post_or_edit_rolling_explainer(
    client: DiscordClient,
    channel_id: str,
    slug: str,
) -> None:
    """Post the rolling explainer as the last message, or edit it if already last."""
    content = get_rolling_content(slug)
    messages = client.get_channel_messages(channel_id, context=f"rolling explainer fetch {slug}", limit=1)
    last = messages[0] if messages else None
    if last and str(last.get("content", "")).startswith(ROLLING_EXPLAINER_PREFIX):
        client.edit_message(channel_id, str(last["id"]), content, context=f"edit rolling explainer {slug}")
        print(f"EDIT: rolling explainer for {slug} (message_id={last['id']})")
    else:
        msg = client.post_message(channel_id, content, context=f"post rolling explainer {slug}")
        print(f"CREATE: rolling explainer for {slug} (message_id={msg.get('id')})")
