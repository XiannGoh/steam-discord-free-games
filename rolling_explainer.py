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

    "weekly-scheduling": [
        """\
📌 How This Works — #update-weekly-schedule-here

Every week the bot posts everyone's availability summary for game nights.

✍️ React with the time buckets that work for you each day
🔄 The summary updates as reactions change

Current week's post is at the top; scroll up to see everyone's availability.""",

        """\
📌 How This Works — #update-weekly-schedule-here

Every week the bot posts everyone's availability summary for game nights.

✍️ React with the time buckets that work for you each day
🔄 The summary updates as reactions change

Current week's post is at the top; scroll up to see everyone's availability.""",

        """\
📌 How This Works — #update-weekly-schedule-here

Every week the bot posts everyone's availability summary for game nights.

✍️ React with the time buckets that work for you each day
🔄 The summary updates as reactions change

Current week's post is at the top; scroll up to see everyone's availability.""",
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


# How many pages of channel history to scan when looking for a stale explainer.
# Each page is up to 100 messages, so this caps the search at ~500 messages.
# A typical Step 3 daily run posts on the order of `1 header + N section
# headers + M game messages + 1 footer + 1 explainer`. Five pages is plenty
# for libraries with hundreds of games while still bounding the worst-case
# Discord API cost to a handful of GETs.
ROLLING_EXPLAINER_MAX_SCAN_PAGES = 5
ROLLING_EXPLAINER_PAGE_SIZE = 100


def _find_stale_explainer_id(
    client: DiscordClient,
    channel_id: str,
    slug: str,
    *,
    after_message_id: str,
) -> str | None:
    """Page back through channel history looking for the most recent rolling
    explainer older than `after_message_id`.

    Returns the message id of the first explainer found, or None if no
    explainer was found within `ROLLING_EXPLAINER_MAX_SCAN_PAGES` pages.
    Stopping bounded means a hopelessly polluted channel doesn't cause an
    unbounded scan; if a stale explainer is buried deeper than the cap, the
    next workflow run still gets another chance.
    """
    cursor = after_message_id
    for page in range(ROLLING_EXPLAINER_MAX_SCAN_PAGES):
        batch = client.get_channel_messages(
            channel_id,
            context=f"rolling explainer scan page {page} for {slug}",
            limit=ROLLING_EXPLAINER_PAGE_SIZE,
            before=cursor,
        )
        if not batch:
            return None
        for m in batch:
            if str(m.get("content", "")).startswith(ROLLING_EXPLAINER_PREFIX):
                return str(m["id"])
        cursor = str(batch[-1]["id"])
    return None


def post_or_edit_rolling_explainer(
    client: DiscordClient,
    channel_id: str,
    slug: str,
) -> None:
    """Post the rolling explainer as the last message, or edit it if already last.

    If a previous rolling explainer exists earlier in the channel (e.g. from a
    previous workflow run that posted other messages after it), it is deleted
    before the new explainer is posted. This prevents accumulating one stale
    "How This Works" message per workflow run while still satisfying the
    verifier's expectation that the explainer is the literal last message.

    The cleanup pages back through channel history until it finds an explainer
    or hits `ROLLING_EXPLAINER_MAX_SCAN_PAGES`. This handles channels where
    today's run posts more than one page of messages (e.g. Step 3 with a large
    gaming library) before the explainer is posted, which limit=20 alone could
    not reach.
    """
    content = get_rolling_content(slug)
    first_page = client.get_channel_messages(
        channel_id,
        context=f"rolling explainer fetch {slug}",
        limit=ROLLING_EXPLAINER_PAGE_SIZE,
    )
    last = first_page[0] if first_page else None
    if last and str(last.get("content", "")).startswith(ROLLING_EXPLAINER_PREFIX):
        client.edit_message(channel_id, str(last["id"]), content, context=f"edit rolling explainer {slug}")
        print(f"EDIT: rolling explainer for {slug} (message_id={last['id']})")
        return
    # Last message isn't an explainer. Look for a stale explainer to clean up
    # before posting a fresh one.
    stale_id: str | None = None
    # First check the current page (cheap — no extra API call).
    for m in first_page[1:]:
        if str(m.get("content", "")).startswith(ROLLING_EXPLAINER_PREFIX):
            stale_id = str(m["id"])
            break
    # If not found on page 1 and there are more messages to scan, page back.
    if stale_id is None and first_page:
        stale_id = _find_stale_explainer_id(
            client,
            channel_id,
            slug,
            after_message_id=str(first_page[-1]["id"]),
        )
    if stale_id is not None:
        try:
            client.delete_message(channel_id, stale_id, context=f"delete stale rolling explainer {slug}")
            print(f"DELETE: stale rolling explainer for {slug} (message_id={stale_id})")
        except Exception as e:
            print(f"WARN: could not delete stale rolling explainer for {slug} (message_id={stale_id}): {e}")
    msg = client.post_message(channel_id, content, context=f"post rolling explainer {slug}")
    print(f"CREATE: rolling explainer for {slug} (message_id={msg.get('id')})")
