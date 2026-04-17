import gaming_library as lib
from scripts.manage_gaming_library import _parse_users


class FakeDiscordClient:
    def __init__(self, reactions=None):
        self.reactions = reactions or {}
        self.posts = []
        self.edits = []
        self.put_reactions = []
        self.not_found_edits = set()

    def get_reaction_users(self, channel_id, message_id, encoded_emoji, *, context, limit=100, after=None):
        return self.reactions.get((channel_id, message_id, encoded_emoji), [])

    def post_message(self, channel_id, content, *, context):
        message_id = f"m-{len(self.posts)+1}"
        self.posts.append((channel_id, content, context))
        return {"id": message_id, "channel_id": channel_id}

    def put_reaction(self, channel_id, message_id, encoded_emoji, *, context):
        self.put_reactions.append((channel_id, message_id, encoded_emoji, context))

    def edit_message(self, channel_id, message_id, content, *, context):
        if (channel_id, message_id) in self.not_found_edits:
            raise lib.DiscordMessageNotFoundError("not found")
        self.edits.append((channel_id, message_id, content, context))
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
                ],
                "winner_messages": {
                    "https://store.steampowered.com/app/12345/Star_Crew/": {
                        "channel_id": "winners-1",
                        "message_id": "winner-message-1",
                    }
                },
            },
        }
    }


def test_promote_winner_via_bookmark_auto_assigns_user():
    state = lib.load_gaming_library(path="/tmp/does-not-exist.json")
    daily_posts = _seed_daily_posts_with_winner()
    client = FakeDiscordClient(
        reactions={
            ("winners-1", "winner-message-1", lib.BOOKMARK_EMOJI_ENCODED): [
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
        reactions={("winners-1", "winner-message-1", lib.BOOKMARK_EMOJI_ENCODED): [{"id": "u-1"}]}
    )

    first_promotions = lib.sync_promotions_from_winners(state, daily_posts, client, bot_user_id=None)
    second_promotions = lib.sync_promotions_from_winners(state, daily_posts, client, bot_user_id=None)

    assert list(state["games"].keys()) == ["steam:12345"]
    assert first_promotions == 1
    assert second_promotions == 0


def test_promotions_prefer_winners_channel_message_over_daily_message():
    state = lib.load_gaming_library(path="/tmp/does-not-exist.json")
    daily_posts = _seed_daily_posts_with_winner()
    client = FakeDiscordClient(
        reactions={
            ("daily-1", "item-1", lib.BOOKMARK_EMOJI_ENCODED): [{"id": "u-daily"}],
            ("winners-1", "winner-message-1", lib.BOOKMARK_EMOJI_ENCODED): [{"id": "u-winner"}],
        }
    )
    lib.sync_promotions_from_winners(state, daily_posts, client, bot_user_id=None)
    assignments = state["games"]["steam:12345"]["assignments"]
    assert "u-winner" in assignments
    assert "u-daily" not in assignments


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
    assert len(messages_after) == 1  # header only (delta embedded in header)
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

    # Game is now m-3 (header=m-1, section_header=m-2, game=m-3, footer=m-4)
    client = FakeDiscordClient(
        reactions={
            ("lib-chan", "m-3", lib.quote("✅", safe="")): [{"id": "u1"}],
            ("lib-chan", "m-3", lib.quote("❌", safe="")): [{"id": "u2"}],
        }
    )
    posted = lib.post_daily_library_reminder(state, day_key="2026-04-10", channel_id="lib-chan", client=client)

    assert posted is True
    assert state["daily_posts"]["2026-04-10"]["completed"] is True
    # header, section_header, game, 4 empty_sections, footer = 8 posts (delta embedded in header)
    assert len(client.posts) == 8
    assert client.put_reactions == [
        ("lib-chan", "m-3", lib.quote("✅", safe=""), "add gaming library status reaction ✅ for 2026-04-10"),
        ("lib-chan", "m-3", lib.quote("⏸️", safe=""), "add gaming library status reaction ⏸️ for 2026-04-10"),
        ("lib-chan", "m-3", lib.quote("❌", safe=""), "add gaming library status reaction ❌ for 2026-04-10"),
    ]

    updates = lib.sync_statuses_from_library_posts(state, client, bot_user_id=None)
    assert updates >= 2
    assert game["assignments"]["u1"]["status"] == lib.STATUS_ACTIVE
    assert game["assignments"]["u2"]["status"] == lib.STATUS_DROPPED


def test_daily_library_rerun_after_promotions_reuses_header_and_adds_missing_games():
    state = lib.load_gaming_library(path="/tmp/does-not-exist.json")
    client = FakeDiscordClient()

    first_posted = lib.post_daily_library_reminder(state, day_key="2026-04-10", channel_id="lib-chan", client=client)

    # First run with no games: header, footer = 2 posts (delta embedded in header)
    assert first_posted is True
    assert len(client.posts) == 2
    assert "No active library games for today" in client.posts[0][1]

    game = lib.ensure_game_entry(state, canonical_name="Core Keeper", url="https://store.steampowered.com/app/1621690/")
    lib.assign_user(game, "u1", lib.STATUS_ACTIVE)

    second_posted = lib.post_daily_library_reminder(state, day_key="2026-04-10", channel_id="lib-chan", client=client)

    assert second_posted is True
    # Second run: new section_header (m-3) + new game (m-4) + 4 empty sections (m-5..m-8); header/footer are edits
    assert len(client.posts) == 8
    # Edits: footer + header jump links = 2 (header placeholder skipped since message_id exists)
    assert len(client.edits) == 2
    # Header jump-links edit is the last edit; message_id must be preserved
    assert state["daily_posts"]["2026-04-10"]["messages"]["header"]["message_id"] == "m-1"
    assert "steam:1621690" in state["daily_posts"]["2026-04-10"]["messages"]
    assert client.put_reactions == [
        ("lib-chan", "m-4", lib.quote("✅", safe=""), "add gaming library status reaction ✅ for 2026-04-10"),
        ("lib-chan", "m-4", lib.quote("⏸️", safe=""), "add gaming library status reaction ⏸️ for 2026-04-10"),
        ("lib-chan", "m-4", lib.quote("❌", safe=""), "add gaming library status reaction ❌ for 2026-04-10"),
    ]


def test_daily_library_rerun_updates_existing_game_message_without_duplicate_post():
    state = lib.load_gaming_library(path="/tmp/does-not-exist.json")
    game = lib.ensure_game_entry(state, canonical_name="Status Game", url="https://store.steampowered.com/app/200/status/")
    lib.assign_user(game, "u1", lib.STATUS_ACTIVE)
    client = FakeDiscordClient()

    lib.post_daily_library_reminder(state, day_key="2026-04-10", channel_id="lib-chan", client=client)
    # header=m-1, section_header=m-2, game=m-3, 4 empty_sections=m-4..m-7, footer=m-8 + 1 edit (header jump links)
    assert len(client.posts) == 8
    assert len(client.put_reactions) == 3

    lib.set_user_status(game, "u1", lib.STATUS_PAUSED)
    posted_again = lib.post_daily_library_reminder(state, day_key="2026-04-10", channel_id="lib-chan", client=client)

    assert posted_again is True
    assert len(client.posts) == 8  # no new posts
    assert len(client.put_reactions) == 3  # no new reactions
    # Second run edits: section_header, game, 4 empty_sections, footer, header jump links = 8 (header placeholder skipped)
    assert len(client.edits) == 9  # 1 from first run + 8 from second run
    game_edit = next(e for e in client.edits if e[1] == "m-3")
    assert "(paused)" in game_edit[2]


def test_daily_library_reruns_converge_without_duplicate_headers_or_games():
    state = lib.load_gaming_library(path="/tmp/does-not-exist.json")
    game = lib.ensure_game_entry(state, canonical_name="Converge", url="https://store.steampowered.com/app/300/converge/")
    lib.assign_user(game, "u1", lib.STATUS_ACTIVE)
    client = FakeDiscordClient()

    lib.post_daily_library_reminder(state, day_key="2026-04-10", channel_id="lib-chan", client=client)
    lib.post_daily_library_reminder(state, day_key="2026-04-10", channel_id="lib-chan", client=client)
    lib.post_daily_library_reminder(state, day_key="2026-04-10", channel_id="lib-chan", client=client)

    # 8 posts on first run (header, section:other, game, 4 empty_sections, footer); no new posts on reruns
    assert len(client.posts) == 8
    # Run 1: 1 edit (header jump links)
    # Run 2: 8 edits (section:other, game, 4 empty_sections, footer, header-jumps) — header placeholder skipped
    # Run 3: same 8 edits
    assert len(client.edits) == 17
    messages = state["daily_posts"]["2026-04-10"]["messages"]
    assert sorted(messages.keys()) == [
        "empty:creator_picks", "empty:demo_playtest", "empty:free_picks", "empty:paid_picks",
        "footer", "header", "section:other", "steam:300",
    ]
    assert messages["header"]["message_id"] == "m-1"
    assert messages["section:other"]["message_id"] == "m-2"
    assert messages["steam:300"]["message_id"] == "m-3"


def test_manage_library_normalizes_mention_user_ids(tmp_path):
    state = {"games": {}, "daily_posts": {}, "version": 1}
    path = tmp_path / "gaming_library.json"
    lib.save_gaming_library(state, str(path))
    lib.manage_library(
        operation="add",
        canonical_name="Mention Parse",
        url="https://store.steampowered.com/app/777/mention/",
        user_ids=["<@123>", "<@!456>", "789"],
        state_path=str(path),
    )
    updated = lib.load_gaming_library(str(path))
    assignments = updated["games"]["steam:777"]["assignments"]
    assert sorted(assignments.keys()) == ["123", "456", "789"]


def test_manage_script_user_parser_accepts_discord_mentions():
    assert _parse_users("123,<@456>,<@!789>") == ["123", "456", "789"]


# --- Enhancement 2: Category grouping ---

def test_games_grouped_by_category_in_library_messages():
    state = lib.load_gaming_library(path="/tmp/does-not-exist.json")
    free_game = lib.ensure_game_entry(state, canonical_name="Free Thing", url="https://store.steampowered.com/app/1/free", source_type="steam_free")
    demo_game = lib.ensure_game_entry(state, canonical_name="Demo Game", url="https://store.steampowered.com/app/2/demo", source_type="steam_demo")
    lib.assign_user(free_game, "u1", lib.STATUS_ACTIVE)
    lib.assign_user(demo_game, "u1", lib.STATUS_ACTIVE)

    messages = lib.build_daily_library_messages(state, "2026-04-10")
    contents = [m["content"] for m in messages]
    full_text = "\n".join(contents)

    assert "🎮 Demo & Playtest" in full_text
    assert "🆓 Free Picks" in full_text
    # Demo section appears before Free Picks — verify by CATEGORY_ORDER position
    demo_idx = lib.CATEGORY_ORDER.index(lib.CATEGORY_DEMO_PLAYTEST)
    free_idx = lib.CATEGORY_ORDER.index(lib.CATEGORY_FREE_PICKS)
    assert demo_idx < free_idx


# --- Enhancement 4: Players label ---

def test_game_message_uses_players_label_not_assigned():
    state = lib.load_gaming_library(path="/tmp/does-not-exist.json")
    game = lib.ensure_game_entry(state, canonical_name="Label Test", url="https://store.steampowered.com/app/10/label")
    lib.assign_user(game, "u1", lib.STATUS_ACTIVE)

    messages = lib.build_daily_library_messages(state, "2026-04-10")
    game_msg = next(m for m in messages if m.get("type") == "game")
    assert "Players:" in game_msg["content"]
    assert "Assigned:" not in game_msg["content"]


# --- Enhancement 12: Steam URL embed suppression ---

def test_steam_urls_wrapped_in_angle_brackets():
    state = lib.load_gaming_library(path="/tmp/does-not-exist.json")
    game = lib.ensure_game_entry(state, canonical_name="Embed Test", url="https://store.steampowered.com/app/42/embed_test/")
    lib.assign_user(game, "u1", lib.STATUS_ACTIVE)

    messages = lib.build_daily_library_messages(state, "2026-04-10")
    game_msg = next(m for m in messages if m.get("type") == "game")
    assert "<https://store.steampowered.com/app/42/embed_test/>" in game_msg["content"]


def test_non_steam_urls_not_wrapped():
    assert lib._suppress_steam_url("https://example.com/game") == "https://example.com/game"
    assert lib._suppress_steam_url("https://store.steampowered.com/app/1/x") == "<https://store.steampowered.com/app/1/x>"


# --- Enhancement 9: Conflict resolution ---

def test_conflicting_status_reactions_reset_to_active():
    state = lib.load_gaming_library(path="/tmp/does-not-exist.json")
    game = lib.ensure_game_entry(state, canonical_name="Conflict Game", url="https://store.steampowered.com/app/99/conflict/")
    lib.assign_user(game, "u1", lib.STATUS_PAUSED)

    # First post to get message IDs in state
    client = FakeDiscordClient(
        reactions={
            # u1 reacted with both ✅ and ❌ (conflict) on m-3 (the game)
            ("lib-chan", "m-3", lib.quote("✅", safe="")): [{"id": "u1"}],
            ("lib-chan", "m-3", lib.quote("❌", safe="")): [{"id": "u1"}],
        }
    )
    lib.post_daily_library_reminder(state, day_key="2026-04-10", channel_id="lib-chan", client=client)
    lib.sync_statuses_from_library_posts(state, client, bot_user_id=None)

    assert game["assignments"]["u1"]["status"] == lib.STATUS_ACTIVE
    assert "u1" in game.get("conflicting_users", [])


def test_single_reaction_sets_status_normally():
    state = lib.load_gaming_library(path="/tmp/does-not-exist.json")
    game = lib.ensure_game_entry(state, canonical_name="Single React", url="https://store.steampowered.com/app/88/single/")
    lib.assign_user(game, "u1", lib.STATUS_ACTIVE)

    client = FakeDiscordClient(
        reactions={
            ("lib-chan", "m-3", lib.quote("⏸️", safe="")): [{"id": "u1"}],
        }
    )
    lib.post_daily_library_reminder(state, day_key="2026-04-10", channel_id="lib-chan", client=client)
    lib.sync_statuses_from_library_posts(state, client, bot_user_id=None)

    assert game["assignments"]["u1"]["status"] == lib.STATUS_PAUSED
    assert game.get("conflicting_users", []) == []


# --- Enhancement 6: Instagram game name extraction ---

def test_instagram_game_name_extracted_from_steam_url():
    state = lib.load_gaming_library(path="/tmp/does-not-exist.json")
    daily_posts = {
        "2026-04-09": {
            "items": [{
                "section": "instagram",
                "source_type": "instagram",
                "title": "coolcreator",
                "url": "https://store.steampowered.com/app/555/Night_Signal/",
                "channel_id": "daily-ch",
                "message_id": "item-ig",
                "description": "check this out!",
            }],
            "winners_state": {
                "winner_entries": [{
                    "winner_key": "https://store.steampowered.com/app/555/Night_Signal/",
                    "title": "coolcreator",
                    "url": "https://store.steampowered.com/app/555/Night_Signal/",
                }],
                "winner_messages": {
                    "https://store.steampowered.com/app/555/Night_Signal/": {
                        "channel_id": "winners-ch",
                        "message_id": "winner-ig",
                    }
                },
            },
        }
    }
    client = FakeDiscordClient(
        reactions={
            ("winners-ch", "winner-ig", lib.BOOKMARK_EMOJI_ENCODED): [{"id": "u1"}],
        }
    )
    lib.sync_promotions_from_winners(state, daily_posts, client, bot_user_id=None)

    game = state["games"]["steam:555"]
    # Should extract "Night Signal" from Steam URL slug
    assert game["canonical_name"] == "Night Signal"


def test_instagram_game_name_flagged_when_not_extractable():
    state = lib.load_gaming_library(path="/tmp/does-not-exist.json")
    daily_posts = {
        "2026-04-09": {
            "items": [{
                "section": "instagram",
                "source_type": "instagram",
                "title": "somecreator",
                "url": "https://www.instagram.com/p/abc/",
                "channel_id": "daily-ch",
                "message_id": "item-ig2",
                "description": "",
            }],
            "winners_state": {
                "winner_entries": [{
                    "winner_key": "https://www.instagram.com/p/abc/",
                    "title": "somecreator",
                    "url": "https://www.instagram.com/p/abc/",
                }],
                "winner_messages": {
                    "https://www.instagram.com/p/abc/": {
                        "channel_id": "winners-ch",
                        "message_id": "winner-ig2",
                    }
                },
            },
        }
    }
    client = FakeDiscordClient(
        reactions={
            ("winners-ch", "winner-ig2", lib.BOOKMARK_EMOJI_ENCODED): [{"id": "u1"}],
        }
    )
    lib.sync_promotions_from_winners(state, daily_posts, client, bot_user_id=None)

    url_key = "url:https://www.instagram.com/p/abc/"
    game = state["games"][url_key]
    assert "⚠️ Name needed" in game["canonical_name"]


# --- Enhancement 7: Discord commands ---

def test_command_add_assigns_user_to_existing_game():
    state = lib.load_gaming_library(path="/tmp/does-not-exist.json")
    game = lib.ensure_game_entry(state, canonical_name="Star Crew", url="https://store.steampowered.com/app/12345/Star_Crew/")

    class CommandFakeClient(FakeDiscordClient):
        def get_channel_messages(self, channel_id, *, context, limit=100, before=None, after=None):
            return [{"id": "msg-1", "author": {"id": "111"}, "content": "!add <@9900> Star Crew"}]

    count = lib.process_library_commands(state, CommandFakeClient(), "lib-chan", bot_user_id="bot-1")

    assert count == 1
    assert "9900" in game["assignments"]
    assert "msg-1" in state["processed_command_ids"]


def test_command_rename_game():
    state = lib.load_gaming_library(path="/tmp/does-not-exist.json")
    lib.ensure_game_entry(state, canonical_name="Old Name", url="https://store.steampowered.com/app/77/old/")

    class CommandFakeClient(FakeDiscordClient):
        def get_channel_messages(self, channel_id, *, context, limit=100, before=None, after=None):
            return [{"id": "msg-2", "author": {"id": "admin"}, "content": "!rename Old Name New Name"}]

    count = lib.process_library_commands(state, CommandFakeClient(), "lib-chan", bot_user_id=None)
    assert count == 1
    game = state["games"]["steam:77"]
    assert game["canonical_name"] == "New Name"


def test_command_archive_game():
    state = lib.load_gaming_library(path="/tmp/does-not-exist.json")
    lib.ensure_game_entry(state, canonical_name="Archive Me", url="https://store.steampowered.com/app/55/archive/")

    class CommandFakeClient(FakeDiscordClient):
        def get_channel_messages(self, channel_id, *, context, limit=100, before=None, after=None):
            return [{"id": "msg-3", "author": {"id": "admin"}, "content": "!archive Archive Me"}]

    lib.process_library_commands(state, CommandFakeClient(), "lib-chan", bot_user_id=None)
    assert state["games"]["steam:55"]["archived"] is True


def test_commands_not_reprocessed_on_second_sync():
    state = lib.load_gaming_library(path="/tmp/does-not-exist.json")
    lib.ensure_game_entry(state, canonical_name="Dup Game", url="https://store.steampowered.com/app/66/dup/")

    class CommandFakeClient(FakeDiscordClient):
        def get_channel_messages(self, channel_id, *, context, limit=100, before=None, after=None):
            return [{"id": "msg-4", "author": {"id": "admin"}, "content": "!archive Dup Game"}]

    lib.process_library_commands(state, CommandFakeClient(), "lib-chan", bot_user_id=None)
    count2 = lib.process_library_commands(state, CommandFakeClient(), "lib-chan", bot_user_id=None)
    assert count2 == 0


# --- Enhancement 3: Header with jump links ---

def test_header_edited_with_jump_links_after_sections_posted(monkeypatch):
    monkeypatch.setenv("DISCORD_GUILD_ID", "guild-1")
    import importlib
    import gaming_library as _lib_mod
    importlib.reload(_lib_mod)

    state = _lib_mod.load_gaming_library(path="/tmp/does-not-exist.json")
    game = _lib_mod.ensure_game_entry(state, canonical_name="Jump Test", url="https://store.steampowered.com/app/111/jump/", source_type="steam_free")
    _lib_mod.assign_user(game, "u1", _lib_mod.STATUS_ACTIVE)
    client = FakeDiscordClient()

    _lib_mod.post_daily_library_reminder(state, day_key="2026-04-10", channel_id="lib-chan", client=client)

    # Header should have been edited at least once with a jump link (new badge format)
    jump_edits = [e for e in client.edits if "discord.com/channels/guild-1" in e[2] and e[1] == "m-1"]
    assert len(jump_edits) >= 1, "Header was not edited with jump links"

    monkeypatch.delenv("DISCORD_GUILD_ID", raising=False)
    importlib.reload(_lib_mod)


# --- Issue #184: Per-player counts and pending reactions in daily summary ---

def _make_state_with_games(game_specs):
    """Build a minimal library state from a list of (name, url, assignments) tuples.
    assignments is a dict of {user_id: {status, updated_at_utc}} or None for no assignments.
    Sets previous_day_games to an empty dict so all games are treated as new.
    """
    state = lib.load_gaming_library(path="/tmp/does-not-exist.json")
    for name, url, assignments in game_specs:
        game = lib.ensure_game_entry(state, canonical_name=name, url=url)
        if assignments:
            for user_id, ass_data in assignments.items():
                game.setdefault("assignments", {})[user_id] = ass_data
    state["previous_day_games"] = {}
    return state


def test_new_games_reported_in_delta():
    state = _make_state_with_games([
        ("Alpha", "https://store.steampowered.com/app/1/alpha/", {"u1": {"status": "active", "updated_at_utc": "t1"}, "u2": {"status": "active", "updated_at_utc": "t1"}}),
        ("Beta", "https://store.steampowered.com/app/2/beta/", {"u1": {"status": "active", "updated_at_utc": "t1"}}),
    ])
    result = lib.compute_daily_delta(state)
    assert "🆕 **Alpha**" in result
    assert "🆕 **Beta**" in result


def test_game_with_no_assignments_still_reported_as_new():
    state = _make_state_with_games([
        ("No Players Game", "https://store.steampowered.com/app/3/noplayers/", None),
    ])
    result = lib.compute_daily_delta(state)
    assert "🆕 **No Players Game**" in result
    assert "👥 Players:" not in result


def test_archived_games_excluded_from_new_game_delta():
    state = _make_state_with_games([
        ("Active Game", "https://store.steampowered.com/app/4/active/", {"u1": {"status": "active", "updated_at_utc": "t1"}}),
        ("Archived Game", "https://store.steampowered.com/app/5/archived/", {"u1": {"status": "active", "updated_at_utc": "t1"}}),
    ])
    state["games"]["steam:5"]["archived"] = True
    result = lib.compute_daily_delta(state)
    assert "🆕 **Active Game**" in result
    assert "Archived Game" not in result


def test_pending_reaction_flags_specific_user_and_game():
    """A player with status=active and updated_at_utc == game's created_at_utc is flagged as pending."""
    state = lib.load_gaming_library(path="/tmp/does-not-exist.json")
    game = lib.ensure_game_entry(state, canonical_name="Pending Game", url="https://store.steampowered.com/app/6/pending/")
    created_ts = game["created_at_utc"]
    # Simulate assignment with updated_at == game.created_at (no reaction yet)
    game["assignments"]["u99"] = {"status": lib.STATUS_ACTIVE, "updated_at_utc": created_ts}
    state["previous_day_games"] = {list(state["games"].keys())[0]: game.copy()}
    result = lib.compute_daily_delta(state)
    assert "⏳ <@u99> has not reacted on Pending Game" in result


def test_pending_not_flagged_when_status_updated():
    """A player whose assignment was updated after game creation is NOT flagged as pending."""
    state = lib.load_gaming_library(path="/tmp/does-not-exist.json")
    game = lib.ensure_game_entry(state, canonical_name="Updated Game", url="https://store.steampowered.com/app/7/updated/")
    game["assignments"]["u88"] = {"status": lib.STATUS_ACTIVE, "updated_at_utc": "2099-01-01T00:00:00+00:00"}
    state["previous_day_games"] = {list(state["games"].keys())[0]: game.copy()}
    result = lib.compute_daily_delta(state)
    assert "⏳" not in result


def test_new_game_with_assignment_reported_in_delta():
    state = _make_state_with_games([
        ("New Addition", "https://store.steampowered.com/app/8/new/", {"u1": {"status": "active", "updated_at_utc": "t1"}}),
    ])
    result = lib.compute_daily_delta(state)
    assert "🆕 **New Addition**" in result


def test_no_changes_message_shown_when_empty_library():
    state = lib.load_gaming_library(path="/tmp/does-not-exist.json")
    state["previous_day_games"] = {}
    result = lib.compute_daily_delta(state)
    assert "No changes since yesterday" in result


# --- Issue #186: Default new library games to Active status on first post ---

def test_assigned_player_with_no_status_defaults_to_active_on_sync():
    """Players assigned without a status should be defaulted to active during sync."""
    state = lib.load_gaming_library(path="/tmp/does-not-exist.json")
    game = lib.ensure_game_entry(state, canonical_name="No Status Game", url="https://store.steampowered.com/app/200/nostatus/")
    # Manually inject an assignment with no status (simulating legacy or edge-case data)
    game.setdefault("assignments", {})["u1"] = {"updated_at_utc": "2026-01-01T00:00:00+00:00"}

    client = FakeDiscordClient(reactions={})  # No reactions
    lib.post_daily_library_reminder(state, day_key="2026-04-10", channel_id="lib-chan", client=client)
    lib.sync_statuses_from_library_posts(state, client, bot_user_id=None)

    assert game["assignments"]["u1"]["status"] == lib.STATUS_ACTIVE


def test_assigned_player_with_none_status_defaults_to_active_on_sync():
    """Players with status=None should be defaulted to active during sync."""
    state = lib.load_gaming_library(path="/tmp/does-not-exist.json")
    game = lib.ensure_game_entry(state, canonical_name="None Status Game", url="https://store.steampowered.com/app/201/nonestatus/")
    game.setdefault("assignments", {})["u2"] = {"status": None, "updated_at_utc": "2026-01-01T00:00:00+00:00"}

    client = FakeDiscordClient(reactions={})
    lib.post_daily_library_reminder(state, day_key="2026-04-10", channel_id="lib-chan", client=client)
    lib.sync_statuses_from_library_posts(state, client, bot_user_id=None)

    assert game["assignments"]["u2"]["status"] == lib.STATUS_ACTIVE


def test_existing_status_not_overridden_by_default():
    """Players with a valid status should not be changed by the default logic."""
    state = lib.load_gaming_library(path="/tmp/does-not-exist.json")
    game = lib.ensure_game_entry(state, canonical_name="Has Status Game", url="https://store.steampowered.com/app/202/hasstatus/")
    lib.assign_user(game, "u3", lib.STATUS_PAUSED)

    # No reactions — but status already set to paused
    client = FakeDiscordClient(reactions={})
    lib.post_daily_library_reminder(state, day_key="2026-04-10", channel_id="lib-chan", client=client)
    lib.sync_statuses_from_library_posts(state, client, bot_user_id=None)

    # Should remain paused, not reset to active
    assert game["assignments"]["u3"]["status"] == lib.STATUS_PAUSED


def test_player_with_no_status_appears_in_library_post_after_sync():
    """A player defaulted to active should appear in the library post's Players section."""
    state = lib.load_gaming_library(path="/tmp/does-not-exist.json")
    game = lib.ensure_game_entry(state, canonical_name="Appear Game", url="https://store.steampowered.com/app/203/appear/")
    game.setdefault("assignments", {})["u4"] = {"updated_at_utc": "2026-01-01T00:00:00+00:00"}

    client = FakeDiscordClient(reactions={})
    lib.post_daily_library_reminder(state, day_key="2026-04-10", channel_id="lib-chan", client=client)
    lib.sync_statuses_from_library_posts(state, client, bot_user_id=None)

    # Re-run the daily reminder post to verify the player shows up
    client2 = FakeDiscordClient(reactions={})
    lib.post_daily_library_reminder(state, day_key="2026-04-11", channel_id="lib-chan", client=client2)

    game_posts = [p[1] for p in client2.posts if "Appear Game" in p[1] and "Players:" in p[1]]
    assert any("<@u4>" in msg for msg in game_posts), "User u4 should appear in library post after status default"


# --- Manual run vs scheduled run completed flag ---

def test_gaming_library_scheduled_run_sets_completed_flag():
    """Scheduled runs mark the day_entry as completed."""
    state = lib.load_gaming_library(path="/tmp/does-not-exist.json")
    client = FakeDiscordClient()
    lib.post_daily_library_reminder(state, day_key="2026-04-10", channel_id="lib-chan", client=client)
    assert state["daily_posts"]["2026-04-10"]["completed"] is True


def test_gaming_library_manual_run_does_not_set_completed_flag():
    """workflow_dispatch runs post content but do not mark the day as completed."""
    state = lib.load_gaming_library(path="/tmp/does-not-exist.json")
    client = FakeDiscordClient()
    lib.post_daily_library_reminder(state, day_key="2026-04-10", channel_id="lib-chan", client=client, manual_run=True)
    assert state["daily_posts"]["2026-04-10"].get("completed") is not True
    assert len(client.posts) > 0  # content was still posted


def test_gaming_library_manual_run_followed_by_scheduled_run_posts_normally():
    """After a manual run, the scheduled run still produces output (not blocked by completed flag)."""
    state = lib.load_gaming_library(path="/tmp/does-not-exist.json")

    # Manual run — completed flag NOT set
    client1 = FakeDiscordClient()
    lib.post_daily_library_reminder(state, day_key="2026-04-10", channel_id="lib-chan", client=client1, manual_run=True)
    assert state["daily_posts"]["2026-04-10"].get("completed") is not True
    assert len(client1.posts) > 0

    # Scheduled run — completed flag IS set, and it still posts (edits existing messages)
    client2 = FakeDiscordClient()
    lib.post_daily_library_reminder(state, day_key="2026-04-10", channel_id="lib-chan", client=client2, manual_run=False)
    assert state["daily_posts"]["2026-04-10"]["completed"] is True
    assert len(client2.posts) + len(client2.edits) > 0  # produced Discord activity


def test_is_manual_run_detects_workflow_dispatch(monkeypatch):
    monkeypatch.setenv(lib.GITHUB_EVENT_NAME_ENV, "workflow_dispatch")
    assert lib.is_manual_run() is True


def test_is_manual_run_returns_false_for_schedule(monkeypatch):
    monkeypatch.setenv(lib.GITHUB_EVENT_NAME_ENV, "schedule")
    assert lib.is_manual_run() is False


def test_is_manual_run_returns_false_when_env_unset(monkeypatch):
    monkeypatch.delenv(lib.GITHUB_EVENT_NAME_ENV, raising=False)
    assert lib.is_manual_run() is False



# --- FIX 6: Step 3 footer separator and section label contract tests ---

def test_library_footer_ends_with_exact_separator_string():
    footer = lib.build_library_footer(
        day_key="2026-04-15",
        header_channel_id="",
        header_message_id="",
        guild_id=None,
    )
    assert footer.endswith("─────────────────── End of Gaming Library ───────────────────")


def test_library_footer_section_labels_include_emojis():
    assert lib._LIBRARY_FOOTER_SECTION_LABELS[lib.CATEGORY_DEMO_PLAYTEST] == "🎮 Demo & Playtest"
    assert lib._LIBRARY_FOOTER_SECTION_LABELS[lib.CATEGORY_PAID_PICKS] == "💰 Paid"
    assert lib._LIBRARY_FOOTER_SECTION_LABELS[lib.CATEGORY_CREATOR_PICKS] == "📸 Creator"


# ---------------------------------------------------------------------------
# Emoji standardization contract tests (FIX 4)
# ---------------------------------------------------------------------------

def test_category_display_uses_standard_emojis():
    """CATEGORY_DISPLAY uses the agreed emoji standard for all three key categories."""
    assert lib.CATEGORY_DISPLAY[lib.CATEGORY_DEMO_PLAYTEST].startswith("🎮"), (
        "Demo & Playtest must use 🎮, not 🧪"
    )
    assert lib.CATEGORY_DISPLAY[lib.CATEGORY_FREE_PICKS].startswith("🆓"), (
        "Free Picks must use 🆓, not 🎮"
    )
    assert lib.CATEGORY_DISPLAY[lib.CATEGORY_PAID_PICKS].startswith("💰"), (
        "Paid Picks must use 💰, not 💸"
    )


def test_free_picks_uses_box_emoji_not_gamepad():
    """Free Picks uses 🆓, never 🎮 (which belongs to Demos)."""
    assert "🆓" in lib.CATEGORY_DISPLAY[lib.CATEGORY_FREE_PICKS]
    assert "🎮" not in lib.CATEGORY_DISPLAY[lib.CATEGORY_FREE_PICKS]


def test_demos_uses_gamepad_emoji_not_test_tube():
    """Demo & Playtest uses 🎮, never 🧪."""
    assert "🎮" in lib.CATEGORY_DISPLAY[lib.CATEGORY_DEMO_PLAYTEST]
    assert "🧪" not in lib.CATEGORY_DISPLAY[lib.CATEGORY_DEMO_PLAYTEST]


def test_paid_uses_money_bag_not_flying_money():
    """Paid Picks uses 💰, never 💸."""
    assert "💰" in lib.CATEGORY_DISPLAY[lib.CATEGORY_PAID_PICKS]
    assert "💸" not in lib.CATEGORY_DISPLAY[lib.CATEGORY_PAID_PICKS]


class TestStep3EmptyCategoryPlaceholders:
    def _make_state_with_games(self, games):
        """Build minimal gaming_library state with the given game dicts."""
        return {
            "games": {g["identity_key"]: g for g in games},
            "archived_game_keys": [],
        }

    def test_missing_category_shows_placeholder(self):
        # Only a free_picks game — other categories should produce empty_section messages
        state = self._make_state_with_games([
            {
                "identity_key": "free|app1",
                "canonical_name": "Game A",
                "status": "active",
                "source_type": "steam_free",
                "url": "",
                "assignments": {},
                "conflicting_users": [],
            }
        ])
        messages = lib.build_daily_library_messages(state, "2026-04-15")
        empty_msgs = [m for m in messages if m.get("type") == "empty_section"]
        empty_keys = [m["identity_key"] for m in empty_msgs]
        assert f"empty:{lib.CATEGORY_DEMO_PLAYTEST}" in empty_keys
        assert f"empty:{lib.CATEGORY_PAID_PICKS}" in empty_keys
        assert f"empty:{lib.CATEGORY_CREATOR_PICKS}" in empty_keys

    def test_present_category_does_not_produce_empty_placeholder(self):
        state = self._make_state_with_games([
            {
                "identity_key": "free|app1",
                "canonical_name": "Game A",
                "status": "active",
                "source_type": "steam_free",
                "url": "",
                "assignments": {},
                "conflicting_users": [],
            }
        ])
        messages = lib.build_daily_library_messages(state, "2026-04-15")
        empty_keys = [m["identity_key"] for m in messages if m.get("type") == "empty_section"]
        assert f"empty:{lib.CATEGORY_FREE_PICKS}" not in empty_keys


# ---------------------------------------------------------------------------
# FIX 5 — Library header placeholder skip on rerun
# ---------------------------------------------------------------------------

def test_gaming_library_intro_contains_delta_after_posting():
    """The header message posted by post_daily_library_reminder always includes 📊 Today's Changes."""
    state = lib.load_gaming_library(path="/tmp/does-not-exist.json")
    game = lib.ensure_game_entry(state, canonical_name="Delta Game", url="https://store.steampowered.com/app/1/delta")
    lib.assign_user(game, "u1", lib.STATUS_ACTIVE)
    client = FakeDiscordClient()

    lib.post_daily_library_reminder(state, day_key="2026-04-10", channel_id="lib-chan", client=client)

    # The header is the first posted message (before the nav_header edit)
    header_posted_content = client.posts[0][1]
    assert "📊 Today's Changes" in header_posted_content, (
        "Intro must include 📊 Today's Changes on first post"
    )

    # After the nav_header edit, the final header also has delta
    header_edits = [e for e in client.edits if e[1] == "m-1"]
    assert header_edits, "Header must be edited with jump links"
    final_header = header_edits[-1][2]
    assert "📊 Today's Changes" in final_header, (
        "Intro must include 📊 Today's Changes in final nav_header edit"
    )


def test_gaming_library_header_placeholder_not_re_edited_on_rerun(capsys):
    """On a re-run where the header already has a message_id, the placeholder is not re-edited.
    The nav_header edit still fires and updates the header with delta + jump links."""
    state = lib.load_gaming_library(path="/tmp/does-not-exist.json")
    game = lib.ensure_game_entry(state, canonical_name="RerunGame", url="https://store.steampowered.com/app/2/rerun")
    lib.assign_user(game, "u1", lib.STATUS_ACTIVE)
    client = FakeDiscordClient()

    # First run
    lib.post_daily_library_reminder(state, day_key="2026-04-10", channel_id="lib-chan", client=client)
    edits_after_first = list(client.edits)

    # Second run (header already has message_id m-1)
    lib.post_daily_library_reminder(state, day_key="2026-04-10", channel_id="lib-chan", client=client)

    captured = capsys.readouterr()
    assert "REUSE: gaming library header already posted" in captured.out

    # The nav_header edit must still fire on the second run
    second_run_edits = client.edits[len(edits_after_first):]
    header_jump_edits = [e for e in second_run_edits if e[1] == "m-1"]
    assert len(header_jump_edits) == 1, "nav_header edit must still fire on rerun"

    final_content = header_jump_edits[0][2]
    assert "📊 Today's Changes" in final_content, (
        "nav_header edit on rerun must still include delta"
    )
