import gaming_library as lib


class FakeDiscordClient:
    def __init__(self, reactions=None):
        self.reactions = reactions or {}
        self.posts = []

    def get_reaction_users(self, channel_id, message_id, encoded_emoji, *, context, limit=100, after=None):
        return self.reactions.get((channel_id, message_id, encoded_emoji), [])

    def post_message(self, channel_id, content, *, context):
        message_id = f"m-{len(self.posts)+1}"
        self.posts.append((channel_id, content, context))
        return {"id": message_id, "channel_id": channel_id}


def _seed_daily_posts_with_winner():
    return {
        "2026-04-09": {
            "items": [
                {
                    "section": "free",
                    "source_type": "steam_free",
                    "title": "Star Crew",
                    "url": "https://store.steampowered.com/app/12345/Star_Crew/",
                    "channel_id": "daily-1",
                    "message_id": "item-1",
                }
            ],
            "winners_state": {
                "winner_entries": [
                    {
                        "winner_key": "https://store.steampowered.com/app/12345/Star_Crew/",
                        "title": "Star Crew",
                        "url": "https://store.steampowered.com/app/12345/Star_Crew/",
                        "description": "co-op space game",
                    }
                ]
            },
        }
    }


def test_promote_winner_via_bookmark_auto_assigns_user():
    state = lib.load_gaming_library(path="/tmp/does-not-exist.json")
    daily_posts = _seed_daily_posts_with_winner()
    client = FakeDiscordClient(
        reactions={
            ("daily-1", "item-1", lib.BOOKMARK_EMOJI_ENCODED): [
                {"id": "bot-1"},
                {"id": "u-1"},
            ]
        }
    )

    promotions = lib.sync_promotions_from_winners(state, daily_posts, client, bot_user_id="bot-1")

    assert promotions == 1
    game = state["games"]["steam:12345"]
    assert game["canonical_name"] == "Star Crew"
    assert game["assignments"]["u-1"]["status"] == lib.STATUS_ACTIVE


def test_promotions_do_not_duplicate_library_entries_for_same_game():
    state = lib.load_gaming_library(path="/tmp/does-not-exist.json")
    daily_posts = _seed_daily_posts_with_winner()
    client = FakeDiscordClient(
        reactions={("daily-1", "item-1", lib.BOOKMARK_EMOJI_ENCODED): [{"id": "u-1"}]}
    )

    lib.sync_promotions_from_winners(state, daily_posts, client, bot_user_id=None)
    lib.sync_promotions_from_winners(state, daily_posts, client, bot_user_id=None)

    assert list(state["games"].keys()) == ["steam:12345"]


def test_manual_add_with_canonical_name_and_instagram_metadata_preserved():
    state = lib.load_gaming_library(path="/tmp/does-not-exist.json")
    game = lib.ensure_game_entry(
        state,
        canonical_name="Night Signal",
        url="https://www.instagram.com/p/abc/",
        source_type="instagram",
        source_section="instagram",
        source_metadata={"original_caption": "new game check this out", "creator": "foo"},
    )

    assert game["canonical_name"] == "Night Signal"
    assert game["source_metadata"]["original_caption"] == "new game check this out"
    assert game["source_metadata"]["creator"] == "foo"


def test_dropped_users_hidden_in_daily_reminder_and_all_dropped_archives():
    state = lib.load_gaming_library(path="/tmp/does-not-exist.json")
    game = lib.ensure_game_entry(
        state,
        canonical_name="Orbit Ops",
        url="https://store.steampowered.com/app/444/Orbit_Ops/",
    )
    lib.assign_user(game, "u1", lib.STATUS_ACTIVE)
    lib.assign_user(game, "u2", lib.STATUS_DROPPED)

    messages = lib.build_daily_library_messages(state, "2026-04-10")
    game_message = next(msg for msg in messages if msg.get("type") == "game")
    assert "<@u1>" in game_message["content"]
    assert "<@u2>" not in game_message["content"]

    lib.set_user_status(game, "u1", lib.STATUS_DROPPED)
    lib.refresh_archive_state(game)
    assert game["archived"] is True

    messages_after = lib.build_daily_library_messages(state, "2026-04-11")
    assert len(messages_after) == 1
    assert "No active library games" in messages_after[0]["content"]


def test_ordering_by_non_dropped_assignee_count_then_name():
    state = lib.load_gaming_library(path="/tmp/does-not-exist.json")

    alpha = lib.ensure_game_entry(state, canonical_name="Alpha Game", url="https://store.steampowered.com/app/1/a")
    beta = lib.ensure_game_entry(state, canonical_name="Beta Game", url="https://store.steampowered.com/app/2/b")

    lib.assign_user(alpha, "u1", lib.STATUS_ACTIVE)
    lib.assign_user(beta, "u1", lib.STATUS_ACTIVE)
    lib.assign_user(beta, "u2", lib.STATUS_PAUSED)

    visible = lib.list_visible_games_for_reminder(state)
    assert [game["canonical_name"] for game in visible] == ["Beta Game", "Alpha Game"]


def test_daily_library_post_records_message_metadata_and_status_sync():
    state = lib.load_gaming_library(path="/tmp/does-not-exist.json")
    game = lib.ensure_game_entry(state, canonical_name="Sync Game", url="https://store.steampowered.com/app/9/sync")
    lib.assign_user(game, "u1", lib.STATUS_ACTIVE)

    client = FakeDiscordClient(
        reactions={
            ("lib-chan", "m-2", lib.quote("✅", safe="")): [{"id": "u1"}],
            ("lib-chan", "m-2", lib.quote("❌", safe="")): [{"id": "u2"}],
        }
    )
    posted = lib.post_daily_library_reminder(state, day_key="2026-04-10", channel_id="lib-chan", client=client)

    assert posted is True
    assert state["daily_posts"]["2026-04-10"]["completed"] is True
    assert len(client.posts) == 2

    updates = lib.sync_statuses_from_library_posts(state, client, bot_user_id=None)
    assert updates >= 2
    assert game["assignments"]["u1"]["status"] == lib.STATUS_ACTIVE
    assert game["assignments"]["u2"]["status"] == lib.STATUS_DROPPED
