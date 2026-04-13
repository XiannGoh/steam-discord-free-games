import json

import main


class FakeDiscordClient:
    def __init__(self, existing_ids=None, stale_ids=None):
        self.existing_ids = set(existing_ids or [])
        self.stale_ids = set(stale_ids or [])
        self.reactions = []
        self.edits = []

    def get_message(self, channel_id, message_id, *, context):
        if message_id in self.stale_ids:
            raise main.DiscordMessageNotFoundError("gone")
        if message_id in self.existing_ids:
            return {"id": message_id}
        raise RuntimeError("missing")

    def edit_message(self, channel_id, message_id, content, *, context):
        if message_id in self.stale_ids:
            raise main.DiscordMessageNotFoundError("gone")
        self.existing_ids.add(message_id)
        self.edits.append((channel_id, message_id, content, context))
        return {"id": message_id, "channel_id": channel_id}

    def put_reaction(self, channel_id, message_id, encoded_emoji, *, context):
        self.reactions.append((channel_id, message_id, encoded_emoji))


def test_prune_daily_posts_retains_latest_30():
    data = {f"2026-03-{day:02d}": {"items": []} for day in range(1, 32)}
    pruned = main.prune_discord_daily_posts(data)

    assert len(pruned) == 30
    assert "2026-03-01" not in pruned
    assert "2026-03-31" in pruned


def test_daily_pick_rerun_and_partial_recovery(monkeypatch, tmp_path, load_fixture_json):
    daily_path = tmp_path / "daily.json"
    initial = load_fixture_json("discord_daily_posts_legacy.json")
    day_key = "2026-04-08"
    initial[day_key]["items"][0]["title"] = "Game A"
    initial[day_key]["items"][0]["url"] = "https://store.steampowered.com/app/1"
    initial[day_key]["items"][0]["item_key"] = main.hashlib.sha256(
        "free|steam_free|https://store.steampowered.com/app/1".encode("utf-8")
    ).hexdigest()[:16]
    initial[day_key]["run_state"] = {
        "intro": {"message_id": "intro-1", "channel_id": "chan-1"},
        "section_headers": {"free": {"message_id": "header-1", "channel_id": "chan-1"}},
        "completed": False,
    }
    daily_path.write_text(json.dumps(initial), encoding="utf-8")

    posted = []
    counter = {"i": 0}

    def fake_post(message, capture_metadata=False):
        counter["i"] += 1
        posted.append(message)
        return {"message_id": f"new-{counter['i']}", "channel_id": "chan-1"}

    fake_client = FakeDiscordClient(existing_ids={"intro-1", "header-1", "10"})

    monkeypatch.setattr(main, "DISCORD_DAILY_POSTS_FILE", str(daily_path))
    monkeypatch.setattr(main, "DISCORD_BOT_TOKEN", "token")
    monkeypatch.setattr(main, "DiscordClient", lambda session: fake_client)
    monkeypatch.setattr(main, "post_to_discord_with_metadata", fake_post)
    monkeypatch.setattr(main, "sleep_briefly", lambda: None)
    monkeypatch.setenv(main.DAILY_DATE_OVERRIDE_ENV, day_key)

    demo_items = [
        {"title": "Demo A", "url": "https://store.steampowered.com/app/9", "price": "Free", "score": 10},
    ]
    free_items = [
        {"title": "Game A", "url": "https://store.steampowered.com/app/1", "price": "Free", "score": 9},
        {"title": "Game B", "url": "https://store.steampowered.com/app/2", "price": "Free", "score": 8},
    ]
    main.post_daily_pick_messages(demo_items, free_items, [], [])

    saved = json.loads(daily_path.read_text(encoding="utf-8"))
    assert any("Game B" in msg for msg in posted)
    assert saved[day_key]["run_state"]["completed"] is True
    assert len(fake_client.reactions) == 2  # demo + newly posted free item get default 👍

    posted_before = len(posted)
    main.post_daily_pick_messages(demo_items, free_items, [], [])
    assert len(posted) == posted_before


def test_load_discord_daily_posts_handles_old_shapes(monkeypatch, tmp_path):
    path = tmp_path / "daily.json"
    path.write_text("[1, 2, 3]", encoding="utf-8")
    monkeypatch.setattr(main, "DISCORD_DAILY_POSTS_FILE", str(path))

    assert main.load_discord_daily_posts() == {}


def test_daily_item_persistence_stores_descriptions(monkeypatch, tmp_path):
    daily_path = tmp_path / "daily.json"
    daily_path.write_text("{}", encoding="utf-8")
    day_key = "2026-04-08"

    counter = {"i": 0}

    def fake_post(message, capture_metadata=False):
        counter["i"] += 1
        return {"message_id": f"new-{counter['i']}", "channel_id": "chan-1"}

    fake_client = FakeDiscordClient()

    monkeypatch.setattr(main, "DISCORD_DAILY_POSTS_FILE", str(daily_path))
    monkeypatch.setattr(main, "DISCORD_BOT_TOKEN", "token")
    monkeypatch.setattr(main, "DiscordClient", lambda session: fake_client)
    monkeypatch.setattr(main, "post_to_discord_with_metadata", fake_post)
    monkeypatch.setattr(main, "sleep_briefly", lambda: None)
    monkeypatch.setenv(main.DAILY_DATE_OVERRIDE_ENV, day_key)

    free_items = [
        {
            "title": "Game A",
            "url": "https://store.steampowered.com/app/1",
            "description": "Steam short description",
            "price": "Free",
            "score": 9,
        }
    ]
    instagram_posts = [
        {
            "username": "creator",
            "caption": "Creator caption text",
            "url": "https://www.instagram.com/p/abc/",
        }
    ]
    main.post_daily_pick_messages([], free_items, [], instagram_posts)

    saved = json.loads(daily_path.read_text(encoding="utf-8"))
    items = saved[day_key]["items"]
    steam_item = next(item for item in items if item["section"] == "free")
    instagram_item = next(item for item in items if item["section"] == "instagram")

    assert steam_item["description"] == "Steam short description"
    assert instagram_item["description"] == "Creator caption text"


def test_dedupe_instagram_posts_merges_same_game_across_creators():
    posts = [
        {"username": "creator_a", "caption": '"Star Drift" - free on Steam #indie 🚀', "url": "u1"},
        {"username": "creator_b", "caption": '"STAR DRIFT" | wishlist now #Steam ✨', "url": "u2"},
    ]

    deduped = main.dedupe_instagram_posts(posts)

    assert [post["url"] for post in deduped] == ["u1"]


def test_dedupe_instagram_posts_keeps_different_games():
    posts = [
        {"username": "creator_a", "caption": '"Star Drift" - out now', "url": "u1"},
        {"username": "creator_b", "caption": '"Moon Harbor" - out now', "url": "u2"},
    ]

    deduped = main.dedupe_instagram_posts(posts)

    assert [post["url"] for post in deduped] == ["u1", "u2"]


def test_dedupe_instagram_posts_keeps_low_confidence_captions():
    posts = [
        {"username": "creator_a", "caption": "Go check this out link in bio", "url": "u1"},
        {"username": "creator_b", "caption": "Super fun pick today 🔥", "url": "u2"},
    ]

    deduped = main.dedupe_instagram_posts(posts)

    assert [post["url"] for post in deduped] == ["u1", "u2"]


def test_dedupe_instagram_posts_preserves_surviving_order():
    posts = [
        {"username": "creator_a", "caption": "[Night Signal]: out now", "url": "u1"},
        {"username": "creator_b", "caption": '"NIGHT SIGNAL" - demo', "url": "u2"},
        {"username": "creator_c", "caption": '"Orbit Tail" - wishlist', "url": "u3"},
    ]

    deduped = main.dedupe_instagram_posts(posts)

    assert [post["url"] for post in deduped] == ["u1", "u3"]


def test_dedupe_instagram_posts_handles_separator_variants():
    posts = [
        {"username": "creator_a", "caption": "Sky Relay - out now", "url": "u1"},
        {"username": "creator_b", "caption": "SKY RELAY | wishlist now", "url": "u2"},
        {"username": "creator_c", "caption": "Sky Relay : free on Steam", "url": "u3"},
    ]

    deduped = main.dedupe_instagram_posts(posts)

    assert [post["url"] for post in deduped] == ["u1"]


def test_dedupe_instagram_posts_handles_quoted_and_bracketed_titles():
    posts = [
        {"username": "creator_a", "caption": '[Echo Vale] - demo on Steam', "url": "u1"},
        {"username": "creator_b", "caption": '"ECHO VALE" - link in bio', "url": "u2"},
    ]

    deduped = main.dedupe_instagram_posts(posts)

    assert [post["url"] for post in deduped] == ["u1"]


def test_dedupe_instagram_posts_keeps_low_confidence_boilerplate_only_captions():
    posts = [
        {"username": "creator_a", "caption": "Demo - Steam - wishlist - link in bio", "url": "u1"},
        {"username": "creator_b", "caption": "playtest : out now on steam", "url": "u2"},
    ]

    deduped = main.dedupe_instagram_posts(posts)

    assert [post["url"] for post in deduped] == ["u1", "u2"]


def test_dedupe_instagram_posts_merges_same_game_without_separator_when_boilerplate_differs():
    posts = [
        {"username": "creator_a", "caption": "Star Drift free on Steam now", "url": "u1"},
        {"username": "creator_b", "caption": "STAR DRIFT wishlist link in bio", "url": "u2"},
    ]

    deduped = main.dedupe_instagram_posts(posts)

    assert [post["url"] for post in deduped] == ["u1"]


def test_dedupe_instagram_posts_keeps_low_confidence_non_title_prefixes_conservative():
    posts = [
        {"username": "creator_a", "caption": "Check this out link in bio", "url": "u1"},
        {"username": "creator_b", "caption": "Check this out on Steam", "url": "u2"},
    ]

    deduped = main.dedupe_instagram_posts(posts)

    assert [post["url"] for post in deduped] == ["u1", "u2"]


def test_derive_instagram_game_key_extracts_prefix_before_boilerplate_boundary():
    assert main.derive_instagram_game_key("Neon Harbor wishlist now on steam") == "neon harbor"


def test_derive_instagram_game_key_rejects_generic_prefix_candidates():
    assert main.derive_instagram_game_key("Check this out link in bio") is None


def test_instagram_dedupe_debug_summary_counts_and_samples():
    posts = [
        {"username": "creator_a", "caption": '"Nova Hex" - out now', "url": "u1"},
        {"username": "creator_b", "caption": '"NOVA HEX" | wishlist', "url": "u2"},
        {"username": "creator_c", "caption": '"Moon Harbor" - out now', "url": "u3"},
    ]

    deduped, debug = main._dedupe_instagram_posts_with_debug(posts)

    assert [post["url"] for post in deduped] == ["u1", "u3"]
    assert debug["fetched_count"] == 3
    assert debug["deduped_count"] == 2
    assert debug["removed_count"] == 1
    assert debug["removed_key_samples"] == ["nova hex"]


def test_daily_sections_post_in_new_order(monkeypatch, tmp_path):
    daily_path = tmp_path / "daily.json"
    daily_path.write_text("{}", encoding="utf-8")
    day_key = "2026-04-08"
    posted = []
    counter = {"i": 0}

    def fake_post(message, capture_metadata=False):
        posted.append(message)
        counter["i"] += 1
        return {"message_id": f"m-{counter['i']}", "channel_id": "chan-1"}

    fake_client = FakeDiscordClient()

    monkeypatch.setattr(main, "DISCORD_DAILY_POSTS_FILE", str(daily_path))
    monkeypatch.setattr(main, "DISCORD_BOT_TOKEN", "token")
    monkeypatch.setattr(main, "DiscordClient", lambda session: fake_client)
    monkeypatch.setattr(main, "post_to_discord_with_metadata", fake_post)
    monkeypatch.setattr(main, "sleep_briefly", lambda: None)
    monkeypatch.setenv(main.DAILY_DATE_OVERRIDE_ENV, day_key)

    main.post_daily_pick_messages(
        [{"title": "Demo", "url": "https://store.steampowered.com/app/11", "score": 10}],
        [{"title": "Free", "url": "https://store.steampowered.com/app/12", "score": 10}],
        [{"title": "Paid", "url": "https://store.steampowered.com/app/13", "score": 10}],
        [{"username": "creator", "caption": "caption", "url": "https://www.instagram.com/p/a/"}],
    )

    headers = [
        message for message in posted
        if message in {
            "🎯 Daily Picks — vote with 👍 on your favorites",
            "🧪 New Demos & Playtests",
            "🎮 Free Picks",
            "💸 Paid Under $20",
        }
    ]
    assert headers[:4] == [
        "🎯 Daily Picks — vote with 👍 on your favorites",
        "🧪 New Demos & Playtests",
        "🎮 Free Picks",
        "💸 Paid Under $20",
    ]


def test_daily_navigation_footer_is_posted_last_with_expected_links(monkeypatch, tmp_path):
    daily_path = tmp_path / "daily.json"
    daily_path.write_text("{}", encoding="utf-8")
    day_key = "2026-04-08"
    posted = []
    counter = {"i": 0}

    def fake_post(message, capture_metadata=False):
        posted.append(message)
        counter["i"] += 1
        return {"message_id": f"m-{counter['i']}", "channel_id": "chan-1"}

    fake_client = FakeDiscordClient()

    monkeypatch.setattr(main, "DISCORD_DAILY_POSTS_FILE", str(daily_path))
    monkeypatch.setattr(main, "DISCORD_BOT_TOKEN", "token")
    monkeypatch.setattr(main, "DISCORD_GUILD_ID", "guild-1")
    monkeypatch.setattr(main, "DiscordClient", lambda session: fake_client)
    monkeypatch.setattr(main, "post_to_discord_with_metadata", fake_post)
    monkeypatch.setattr(main, "sleep_briefly", lambda: None)
    monkeypatch.setenv(main.DAILY_DATE_OVERRIDE_ENV, day_key)

    main.post_daily_pick_messages(
        [{"title": "Demo", "url": "https://store.steampowered.com/app/11", "score": 10}],
        [{"title": "Free", "url": "https://store.steampowered.com/app/12", "score": 10}],
        [],
        [],
    )

    expected_footer = "\n".join(
        [
            "🗓️ Daily Picks for Wednesday, April 8, 2026",
            "",
            "🎯 Intro / Top of Post → [Jump](https://discord.com/channels/guild-1/chan-1/m-1)",
            "🧪 Demo & Playtest Picks → [Jump](https://discord.com/channels/guild-1/chan-1/m-2)",
            "🎮 Free Picks → [Jump](https://discord.com/channels/guild-1/chan-1/m-4)",
        ]
    )
    assert posted[-1] == expected_footer
    assert any("Demo Pick #1" in message for message in posted[:-1])
    assert any("Free Pick #1" in message for message in posted[:-1])


def test_daily_navigation_footer_rerun_reuses_existing_message(monkeypatch, tmp_path):
    daily_path = tmp_path / "daily.json"
    day_key = "2026-04-08"
    initial = {
        day_key: {
            "run_state": {
                "intro": {"message_id": "intro-1", "channel_id": "chan-1"},
                "section_headers": {"free": {"message_id": "header-free-1", "channel_id": "chan-1"}},
                "navigation_footer": {"message_id": "footer-1", "channel_id": "chan-1"},
                "completed": False,
            },
            "items": [
                {
                    "item_key": main.hashlib.sha256(
                        "free|steam_free|https://store.steampowered.com/app/12".encode("utf-8")
                    ).hexdigest()[:16],
                    "section": "free",
                    "title": "Free",
                    "url": "https://store.steampowered.com/app/12",
                    "message_id": "item-1",
                    "channel_id": "chan-1",
                    "source_type": "steam_free",
                    "posted_at": "2026-04-08T00:00:00+00:00",
                }
            ],
        }
    }
    daily_path.write_text(json.dumps(initial), encoding="utf-8")
    posted = []

    def fake_post(message, capture_metadata=False):
        posted.append(message)
        return {"message_id": "new-message", "channel_id": "chan-1"}

    fake_client = FakeDiscordClient(existing_ids={"intro-1", "header-free-1", "footer-1", "item-1"})

    monkeypatch.setattr(main, "DISCORD_DAILY_POSTS_FILE", str(daily_path))
    monkeypatch.setattr(main, "DISCORD_BOT_TOKEN", "token")
    monkeypatch.setattr(main, "DISCORD_GUILD_ID", "guild-1")
    monkeypatch.setattr(main, "DiscordClient", lambda session: fake_client)
    monkeypatch.setattr(main, "post_to_discord_with_metadata", fake_post)
    monkeypatch.setattr(main, "sleep_briefly", lambda: None)
    monkeypatch.setenv(main.DAILY_DATE_OVERRIDE_ENV, day_key)

    main.post_daily_pick_messages([], [{"title": "Free", "url": "https://store.steampowered.com/app/12", "score": 10}], [], [])

    assert posted == []


def test_daily_completed_run_skips_without_force_refresh(monkeypatch, tmp_path):
    daily_path = tmp_path / "daily.json"
    day_key = "2026-04-08"
    daily_path.write_text(json.dumps({day_key: {"run_state": {"completed": True}}}), encoding="utf-8")
    posted = []

    monkeypatch.setattr(main, "DISCORD_DAILY_POSTS_FILE", str(daily_path))
    monkeypatch.setattr(main, "DISCORD_BOT_TOKEN", "token")
    monkeypatch.setattr(main, "post_to_discord_with_metadata", lambda message, capture_metadata=False: posted.append(message))
    monkeypatch.setattr(main, "sleep_briefly", lambda: None)
    monkeypatch.setenv(main.DAILY_DATE_OVERRIDE_ENV, day_key)

    main.post_daily_pick_messages([], [{"title": "Free", "url": "https://store.steampowered.com/app/12", "score": 10}], [], [])

    assert posted == []


def test_daily_force_refresh_reconciles_same_day_without_duplicates(monkeypatch, tmp_path):
    daily_path = tmp_path / "daily.json"
    day_key = "2026-04-08"
    free_item_key = main.hashlib.sha256("free|steam_free|https://store.steampowered.com/app/12".encode("utf-8")).hexdigest()[:16]
    initial = {
        day_key: {
            "run_state": {
                "intro": {"message_id": "intro-1", "channel_id": "chan-1"},
                "section_headers": {"free": {"message_id": "header-free-1", "channel_id": "chan-1"}},
                "navigation_footer": {"message_id": "footer-1", "channel_id": "chan-1"},
                "completed": True,
            },
            "items": [
                {
                    "item_key": free_item_key,
                    "section": "free",
                    "title": "Free",
                    "url": "https://store.steampowered.com/app/12",
                    "description": "old description",
                    "message_id": "item-1",
                    "channel_id": "chan-1",
                    "source_type": "steam_free",
                    "posted_at": "2026-04-08T00:00:00+00:00",
                }
            ],
        }
    }
    daily_path.write_text(json.dumps(initial), encoding="utf-8")
    posted = []
    counter = {"i": 0}

    def fake_post(message, capture_metadata=False):
        posted.append(message)
        counter["i"] += 1
        return {"message_id": f"new-{counter['i']}", "channel_id": "chan-1"}

    fake_client = FakeDiscordClient(existing_ids={"intro-1", "header-free-1", "footer-1", "item-1"})

    monkeypatch.setattr(main, "DISCORD_DAILY_POSTS_FILE", str(daily_path))
    monkeypatch.setattr(main, "DISCORD_BOT_TOKEN", "token")
    monkeypatch.setattr(main, "DISCORD_GUILD_ID", "guild-1")
    monkeypatch.setattr(main, "DiscordClient", lambda session: fake_client)
    monkeypatch.setattr(main, "post_to_discord_with_metadata", fake_post)
    monkeypatch.setattr(main, "sleep_briefly", lambda: None)
    monkeypatch.setenv(main.DAILY_DATE_OVERRIDE_ENV, day_key)

    free_items = [
        {
            "title": "Free",
            "url": "https://store.steampowered.com/app/12",
            "description": "new description",
            "score": 10,
        },
        {
            "title": "Free New",
            "url": "https://store.steampowered.com/app/13",
            "description": "new item",
            "score": 9,
        },
    ]
    main.post_daily_pick_messages([], free_items, [], [], force_refresh_same_day=True)

    assert len(posted) == 1
    assert len(fake_client.edits) == 4  # intro + header + existing free item + footer
    assert len(fake_client.reactions) == 1  # only brand-new item gets 👍

    saved = json.loads(daily_path.read_text(encoding="utf-8"))
    items = saved[day_key]["items"]
    assert len(items) == 2
    updated_existing = next(item for item in items if item["item_key"] == free_item_key)
    assert updated_existing["description"] == "new description"

    edits_after_first = len(fake_client.edits)
    reactions_after_first = len(fake_client.reactions)
    main.post_daily_pick_messages([], free_items, [], [], force_refresh_same_day=True)

    assert len(posted) == 1
    assert len(fake_client.edits) == edits_after_first + 5  # intro + header + two items + footer
    assert len(fake_client.reactions) == reactions_after_first
    saved_after_second = json.loads(daily_path.read_text(encoding="utf-8"))
    assert len(saved_after_second[day_key]["items"]) == 2


def test_daily_navigation_footer_uses_target_day_override_for_display(monkeypatch, tmp_path):
    daily_path = tmp_path / "daily.json"
    daily_path.write_text("{}", encoding="utf-8")
    day_key = "2026-04-10"
    posted = []
    counter = {"i": 0}

    def fake_post(message, capture_metadata=False):
        posted.append(message)
        counter["i"] += 1
        return {"message_id": f"m-{counter['i']}", "channel_id": "chan-1"}

    fake_client = FakeDiscordClient()

    monkeypatch.setattr(main, "DISCORD_DAILY_POSTS_FILE", str(daily_path))
    monkeypatch.setattr(main, "DISCORD_BOT_TOKEN", "token")
    monkeypatch.setattr(main, "DISCORD_GUILD_ID", "guild-1")
    monkeypatch.setattr(main, "DiscordClient", lambda session: fake_client)
    monkeypatch.setattr(main, "post_to_discord_with_metadata", fake_post)
    monkeypatch.setattr(main, "sleep_briefly", lambda: None)
    monkeypatch.setenv(main.DAILY_DATE_OVERRIDE_ENV, day_key)

    main.post_daily_pick_messages([], [{"title": "Free", "url": "https://store.steampowered.com/app/12", "score": 10}], [], [])

    assert posted[-1].splitlines()[0] == "🗓️ Daily Picks for Friday, April 10, 2026"


def test_daily_navigation_footer_skips_safely_when_guild_or_metadata_missing(monkeypatch, tmp_path):
    daily_path = tmp_path / "daily.json"
    daily_path.write_text("{}", encoding="utf-8")
    day_key = "2026-04-08"
    posted = []
    counter = {"i": 0}

    def fake_post(message, capture_metadata=False):
        posted.append(message)
        counter["i"] += 1
        if counter["i"] == 2:
            return {"message_id": f"m-{counter['i']}", "channel_id": None}
        return {"message_id": f"m-{counter['i']}", "channel_id": "chan-1"}

    fake_client = FakeDiscordClient()

    monkeypatch.setattr(main, "DISCORD_DAILY_POSTS_FILE", str(daily_path))
    monkeypatch.setattr(main, "DISCORD_BOT_TOKEN", "token")
    monkeypatch.setattr(main, "DISCORD_GUILD_ID", "")
    monkeypatch.setattr(main, "DiscordClient", lambda session: fake_client)
    monkeypatch.setattr(main, "post_to_discord_with_metadata", fake_post)
    monkeypatch.setattr(main, "sleep_briefly", lambda: None)
    monkeypatch.setenv(main.DAILY_DATE_OVERRIDE_ENV, day_key)

    main.post_daily_pick_messages([{"title": "Demo", "url": "https://store.steampowered.com/app/11", "score": 10}], [], [], [])

    assert any("Demo Pick #1" in message for message in posted)
    assert not any("Intro / Top of Post" in message for message in posted)
    saved = json.loads(daily_path.read_text(encoding="utf-8"))
    assert saved[day_key]["run_state"]["completed"] is True


def test_daily_section_order_is_product_invariant():
    assert main.DAILY_SECTION_ORDER == ["demo_playtest", "free", "paid", "instagram"]
    assert [entry["header"] for entry in main.DAILY_SECTION_CONFIG] == [
        "🧪 New Demos & Playtests",
        "🎮 Free Picks",
        "💸 Paid Under $20",
        "📸 Instagram Creator Picks",
    ]


def test_routing_exclusivity_invariants():
    assert main.route_item_to_daily_section("demo") == "demo_playtest"
    assert main.route_item_to_daily_section("playtest") == "demo_playtest"
    assert main.route_item_to_daily_section("free_game") == "free"
    assert main.route_item_to_daily_section("temporarily_free") == "free"
    assert main.route_item_to_daily_section("paid_under_20") == "paid"

    demo_like = {"demo", "playtest"}
    free_like = {"free_game", "temporarily_free"}
    paid_like = {"paid_under_20"}
    all_types = demo_like | free_like | paid_like
    sections = [main.route_item_to_daily_section(item_type) for item_type in all_types]
    assert all(section in {"demo_playtest", "free", "paid"} for section in sections)
    assert len(sections) == len(set((item_type, section) for item_type, section in zip(all_types, sections)))

    assert all(main.route_item_to_daily_section(item_type) != "free" for item_type in demo_like)
    assert all(main.route_item_to_daily_section(item_type) != "demo_playtest" for item_type in free_like)
    assert all(main.route_item_to_daily_section(item_type) not in {"demo_playtest", "free"} for item_type in paid_like)


def test_each_item_type_has_exactly_one_section_owner():
    item_types = ["demo", "playtest", "free_game", "temporarily_free", "paid_under_20"]
    owned_sections = [main.route_item_to_daily_section(item_type) for item_type in item_types]

    assert all(section is not None for section in owned_sections)
    assert owned_sections.count("demo_playtest") == 2
    assert owned_sections.count("free") == 2
    assert owned_sections.count("paid") == 1


def test_demo_playtest_label_uses_specific_type():
    demo_message = main.format_steam_item_message(
        {"title": "Demo A", "url": "https://store.steampowered.com/app/1", "score": 9, "type": "demo"},
        1,
        demo_playtest=True,
    )
    playtest_message = main.format_steam_item_message(
        {"title": "Playtest B", "url": "https://store.steampowered.com/app/2", "score": 10, "type": "playtest"},
        2,
        demo_playtest=True,
    )

    assert "🧪 Demo Pick #1" in demo_message
    assert "🧪 Playtest Pick #2" in playtest_message


def test_light_diversity_rerank_penalizes_excess_duplicate_tags():
    items = [
        {"title": "Survival A", "score": 12, "diversity_tags": ["survival"]},
        {"title": "Survival B", "score": 11, "diversity_tags": ["survival"]},
        {"title": "Survival C", "score": 10, "diversity_tags": ["survival"]},
        {"title": "Party D", "score": 10, "diversity_tags": ["party"]},
    ]

    reranked = main.apply_light_diversity_rerank(items)
    assert [item["title"] for item in reranked[:3]] == ["Survival A", "Survival B", "Party D"]
    assert reranked[2]["diversity_penalty"] == 0
    assert reranked[3]["diversity_penalty"] >= 1


def test_demo_selection_prefers_quality_over_filling_cap():
    qualified = [
        {"title": "Strong", "score": 8},
        {"title": "Borderline", "score": main.MIN_SCORE_TO_POST_DEMO_PLAYTEST},
    ]
    selected = main.select_demo_playtest_items(qualified, cap=10)

    assert [item["title"] for item in selected] == ["Strong"]


def test_demo_selection_can_intentionally_post_below_cap_when_only_weak_remain():
    qualified = [
        {"title": "Strong A", "score": main.DEMO_PLAYTEST_QUALITY_FLOOR_SCORE},
        {"title": "Weak B", "score": main.MIN_SCORE_TO_POST_DEMO_PLAYTEST},
        {"title": "Weak C", "score": main.MIN_SCORE_TO_POST_DEMO_PLAYTEST},
    ]
    selected = main.select_demo_playtest_items(qualified, cap=10)
    assert [item["title"] for item in selected] == ["Strong A"]


def test_run_summary_aggregation_lines():
    lines = main.build_run_summary(
        steam_candidates_scanned=40,
        demo_playtest_candidates_qualified=5,
        free_candidates_qualified=9,
        paid_candidates_qualified=3,
        demo_playtest_posted=4,
        free_posted=8,
        paid_posted=2,
        filtered_weak_reviews=7,
        filtered_weak_group_fit=6,
        filtered_low_signal_junk=4,
        filtered_repost_cooldown=3,
        top_filter_reasons=[("weak_group_fit", 6), ("low_signal_junk", 4), ("repost_cooldown", 3)],
        selected_title_samples={
            "demo_playtest": ["Demo A"],
            "free": ["Free A", "Free B"],
            "paid": ["Paid A"],
        },
    )

    assert lines[0] == "RUN SUMMARY"
    assert "- Steam candidates scanned: 40" in lines
    assert "- Demo/playtest posted: 4" in lines
    assert "- Filtered by repost cooldown: 3" in lines
    assert "- Top filter reason: weak_group_fit (6)" in lines
    assert "  - Demo/Playtest: Demo A" in lines


def test_debug_export_writes_expected_structure(tmp_path):
    output_path = tmp_path / "daily_debug_summary.json"
    records = [
        {
            "title": "Demo A",
            "type": "demo",
            "final_score": 11,
            "review_sentiment": "Very Positive",
            "friend_group_signal": 7,
            "keep": True,
            "reason_list": ["qualified"],
        }
    ]
    summary = ["RUN SUMMARY", "- Steam candidates scanned: 1"]

    instagram_debug = {"fetched_count": 4, "deduped_count": 3, "removed_count": 1}

    main.export_daily_debug_summary(records, summary, path=str(output_path), instagram_debug=instagram_debug)
    saved = json.loads(output_path.read_text(encoding="utf-8"))

    assert saved["run_summary"] == summary
    assert saved["generated_at_utc"]
    assert saved["target_day_key"]
    assert saved["section_order"] == ["demo_playtest", "free", "paid", "instagram"]
    assert saved["instagram_debug"] == instagram_debug
    assert saved["records"][0]["title"] == "Demo A"
    assert saved["records"][0]["reason_list"] == ["qualified"]


def test_prune_instagram_seen_state_enforces_retention_and_shape():
    oversized = [f"code-{idx}" for idx in range(main.INSTAGRAM_SEEN_RETENTION_PER_CREATOR + 5)]
    pruned = main.prune_instagram_seen_state(
        {
            "creator": oversized,
            "bad_creator": "not-a-list",
            123: ["ignored"],
            "mixed": ["ok", "", None, "ok-2"],
        }
    )

    assert pruned["creator"] == oversized[-main.INSTAGRAM_SEEN_RETENTION_PER_CREATOR:]
    assert "bad_creator" not in pruned
    assert "123" not in pruned
    assert pruned["mixed"] == ["ok", "ok-2"]


def test_debug_export_fails_gracefully(monkeypatch, capsys):
    def fake_open(*args, **kwargs):
        raise OSError("disk full")

    monkeypatch.setattr("builtins.open", fake_open)
    main.export_daily_debug_summary([], ["RUN SUMMARY"], path="ignored.json")
    captured = capsys.readouterr()
    assert "WARN: failed to write debug summary" in captured.out


class FakeDebugClient:
    def __init__(self):
        self.posts = []

    def post_message(self, channel_id, content, *, context):
        self.posts.append((channel_id, content, context))
        return {"id": "debug-1", "channel_id": channel_id}


def _make_vs(*, intro_id="intro-1", footer_id="footer-1", section_headers=None, items=None, posted_section_keys=None):
    """Build a minimal verification_state for tests."""
    return {
        "run_state": {
            "intro": {"message_id": intro_id} if intro_id else {},
            "navigation_footer": {"message_id": footer_id} if footer_id else {},
            "section_headers": section_headers or {},
        },
        "items": items or [],
        "posted_section_keys": posted_section_keys or [],
    }


def test_export_verification_artifact_pass_when_no_skipped(tmp_path):
    out = tmp_path / "verification.json"
    run_counts = {"created": 4, "updated": 1, "reused": 2, "skipped": 0}
    main.export_verification_artifact(
        day_key="2026-04-12",
        run_counts=run_counts,
        rerun_protection_active=False,
        verification_state=_make_vs(),
        path=str(out),
    )
    artifact = json.loads(out.read_text(encoding="utf-8"))
    assert artifact["day_key"] == "2026-04-12"
    assert artifact["created"] == 4
    assert artifact["updated"] == 1
    assert artifact["reused"] == 2
    assert artifact["skipped"] == 0
    assert artifact["rerun_protection_active"] is False
    assert artifact["pass"] is True
    assert artifact["generated_at_utc"]
    # New structural fields present
    assert artifact["intro_present"] is True
    assert artifact["intro_count"] == 1
    assert artifact["item_count"] == 0
    assert artifact["duplicate_item_keys"] == []
    assert artifact["footer_present"] is True
    assert artifact["posted_section_keys"] == []


def test_export_verification_artifact_fail_when_skipped(tmp_path):
    out = tmp_path / "verification.json"
    run_counts = {"created": 2, "updated": 0, "reused": 1, "skipped": 1}
    main.export_verification_artifact(
        day_key="2026-04-12",
        run_counts=run_counts,
        rerun_protection_active=False,
        verification_state=_make_vs(),
        path=str(out),
    )
    artifact = json.loads(out.read_text(encoding="utf-8"))
    assert artifact["pass"] is False
    assert artifact["skipped"] == 1


def test_export_verification_artifact_pass_when_rerun_protection_active(tmp_path):
    out = tmp_path / "verification.json"
    run_counts = {"created": 0, "updated": 0, "reused": 0, "skipped": 0}
    main.export_verification_artifact(
        day_key="2026-04-12",
        run_counts=run_counts,
        rerun_protection_active=True,
        path=str(out),
    )
    artifact = json.loads(out.read_text(encoding="utf-8"))
    assert artifact["rerun_protection_active"] is True
    assert artifact["pass"] is True


def test_export_verification_artifact_fails_gracefully(monkeypatch, capsys):
    def fake_open(*args, **kwargs):
        raise OSError("disk full")

    monkeypatch.setattr("builtins.open", fake_open)
    main.export_verification_artifact(
        day_key="2026-04-12",
        run_counts={"created": 1, "updated": 0, "reused": 0, "skipped": 0},
        rerun_protection_active=False,
        path="ignored.json",
    )
    captured = capsys.readouterr()
    assert "WARN: failed to write verification artifact" in captured.out


def test_export_verification_artifact_fail_when_no_intro(tmp_path):
    out = tmp_path / "verification.json"
    main.export_verification_artifact(
        day_key="2026-04-12",
        run_counts={"created": 1, "updated": 0, "reused": 0, "skipped": 0},
        rerun_protection_active=False,
        verification_state=_make_vs(intro_id=None),
        path=str(out),
    )
    artifact = json.loads(out.read_text(encoding="utf-8"))
    assert artifact["intro_present"] is False
    assert artifact["intro_count"] == 0
    assert artifact["pass"] is False


def test_export_verification_artifact_fail_when_duplicate_item_keys(tmp_path):
    out = tmp_path / "verification.json"
    items = [
        {"item_key": "abc123", "title": "Game A"},
        {"item_key": "abc123", "title": "Game A duplicate"},
        {"item_key": "def456", "title": "Game B"},
    ]
    main.export_verification_artifact(
        day_key="2026-04-12",
        run_counts={"created": 3, "updated": 0, "reused": 0, "skipped": 0},
        rerun_protection_active=False,
        verification_state=_make_vs(items=items),
        path=str(out),
    )
    artifact = json.loads(out.read_text(encoding="utf-8"))
    assert artifact["duplicate_item_keys"] == ["abc123"]
    assert artifact["item_count"] == 3
    assert artifact["pass"] is False


def test_export_verification_artifact_footer_required_when_guild_id_set(tmp_path, monkeypatch):
    out = tmp_path / "verification.json"
    monkeypatch.setattr(main, "DISCORD_GUILD_ID", "guild-123")
    main.export_verification_artifact(
        day_key="2026-04-12",
        run_counts={"created": 2, "updated": 0, "reused": 0, "skipped": 0},
        rerun_protection_active=False,
        verification_state=_make_vs(footer_id=None, posted_section_keys=["free"]),
        path=str(out),
    )
    artifact = json.loads(out.read_text(encoding="utf-8"))
    assert artifact["footer_present"] is False
    assert artifact["pass"] is False


def test_export_verification_artifact_footer_not_required_without_guild_id(tmp_path, monkeypatch):
    out = tmp_path / "verification.json"
    monkeypatch.setattr(main, "DISCORD_GUILD_ID", "")
    main.export_verification_artifact(
        day_key="2026-04-12",
        run_counts={"created": 2, "updated": 0, "reused": 0, "skipped": 0},
        rerun_protection_active=False,
        verification_state=_make_vs(footer_id=None, posted_section_keys=["free"]),
        path=str(out),
    )
    artifact = json.loads(out.read_text(encoding="utf-8"))
    assert artifact["footer_present"] is False
    assert artifact["pass"] is True


def test_export_verification_artifact_fail_when_orphan_section_header(tmp_path):
    # A header exists for "paid" but "paid" was not in posted_section_keys
    out = tmp_path / "verification.json"
    vs = _make_vs(
        section_headers={"paid": {"message_id": "hdr-paid"}},
        posted_section_keys=["free"],
    )
    main.export_verification_artifact(
        day_key="2026-04-12",
        run_counts={"created": 2, "updated": 0, "reused": 0, "skipped": 0},
        rerun_protection_active=False,
        verification_state=vs,
        path=str(out),
    )
    artifact = json.loads(out.read_text(encoding="utf-8"))
    assert artifact["section_header_counts"] == {"paid": 1}
    assert artifact["pass"] is False


def test_export_verification_artifact_section_header_counts_by_key(tmp_path):
    out = tmp_path / "verification.json"
    vs = _make_vs(
        section_headers={
            "free": {"message_id": "hdr-free"},
            "paid": {},  # no message_id → count 0
        },
        posted_section_keys=["free"],
    )
    main.export_verification_artifact(
        day_key="2026-04-12",
        run_counts={"created": 2, "updated": 0, "reused": 0, "skipped": 0},
        rerun_protection_active=False,
        verification_state=vs,
        path=str(out),
    )
    artifact = json.loads(out.read_text(encoding="utf-8"))
    assert artifact["section_header_counts"] == {"free": 1, "paid": 0}
    # paid header has count 0 so headers_ok is True (only count>0 headers are checked)
    assert artifact["pass"] is True


def test_post_discord_debug_summary_posts_compact_message(monkeypatch):
    fake_client = FakeDebugClient()
    monkeypatch.setattr(main, "DISCORD_DEBUG_CHANNEL_ID", "debug-chan-1")
    monkeypatch.setattr(main, "DISCORD_BOT_TOKEN", "token")
    monkeypatch.setattr(main, "DiscordClient", lambda session: fake_client)

    run_counts = {"created": 3, "updated": 1, "reused": 2, "skipped": 0}
    main.post_discord_debug_summary(
        day_key="2026-04-12",
        run_counts=run_counts,
        rerun_protection_active=False,
        force_refresh_same_day=False,
    )

    assert len(fake_client.posts) == 1
    channel_id, content, _ctx = fake_client.posts[0]
    assert channel_id == "debug-chan-1"
    assert "2026-04-12" in content
    assert "Created: 3" in content
    assert "Updated: 1" in content
    assert "Reused: 2" in content
    assert "Skipped: 0" in content
    assert "inactive" in content


def test_post_discord_debug_summary_rerun_protection_active(monkeypatch):
    fake_client = FakeDebugClient()
    monkeypatch.setattr(main, "DISCORD_DEBUG_CHANNEL_ID", "debug-chan-1")
    monkeypatch.setattr(main, "DISCORD_BOT_TOKEN", "token")
    monkeypatch.setattr(main, "DiscordClient", lambda session: fake_client)

    run_counts = {"created": 0, "updated": 0, "reused": 0, "skipped": 0}
    main.post_discord_debug_summary(
        day_key="2026-04-12",
        run_counts=run_counts,
        rerun_protection_active=True,
        force_refresh_same_day=False,
    )

    assert len(fake_client.posts) == 1
    _, content, _ = fake_client.posts[0]
    assert "active" in content


def test_post_discord_debug_summary_skips_when_no_channel(monkeypatch):
    fake_client = FakeDebugClient()
    monkeypatch.setattr(main, "DISCORD_DEBUG_CHANNEL_ID", "")
    monkeypatch.setattr(main, "DISCORD_BOT_TOKEN", "token")
    monkeypatch.setattr(main, "DiscordClient", lambda session: fake_client)

    main.post_discord_debug_summary(
        day_key="2026-04-12",
        run_counts={"created": 1, "updated": 0, "reused": 0, "skipped": 0},
        rerun_protection_active=False,
        force_refresh_same_day=False,
    )

    assert len(fake_client.posts) == 0


def test_post_discord_debug_summary_skips_when_no_token(monkeypatch):
    fake_client = FakeDebugClient()
    monkeypatch.setattr(main, "DISCORD_DEBUG_CHANNEL_ID", "debug-chan-1")
    monkeypatch.setattr(main, "DISCORD_BOT_TOKEN", "")
    monkeypatch.setattr(main, "DiscordClient", lambda session: fake_client)

    main.post_discord_debug_summary(
        day_key="2026-04-12",
        run_counts={"created": 1, "updated": 0, "reused": 0, "skipped": 0},
        rerun_protection_active=False,
        force_refresh_same_day=False,
    )

    assert len(fake_client.posts) == 0


def test_post_daily_pick_messages_returns_counts(monkeypatch, tmp_path):
    daily_path = tmp_path / "daily.json"
    daily_path.write_text("{}", encoding="utf-8")
    day_key = "2026-04-12"

    counter = {"i": 0}

    def fake_post(message, capture_metadata=False):
        counter["i"] += 1
        return {"message_id": f"new-{counter['i']}", "channel_id": "chan-1"}

    fake_client = FakeDiscordClient()

    monkeypatch.setattr(main, "DISCORD_DAILY_POSTS_FILE", str(daily_path))
    monkeypatch.setattr(main, "DISCORD_BOT_TOKEN", "token")
    monkeypatch.setattr(main, "DiscordClient", lambda session: fake_client)
    monkeypatch.setattr(main, "post_to_discord_with_metadata", fake_post)
    monkeypatch.setattr(main, "sleep_briefly", lambda: None)
    monkeypatch.setenv(main.DAILY_DATE_OVERRIDE_ENV, day_key)

    free_items = [{"title": "Game A", "url": "https://store.steampowered.com/app/1", "price": "Free", "score": 9}]
    run_counts, rerun_protection_active, _ = main.post_daily_pick_messages([], free_items, [], [])

    assert rerun_protection_active is False
    # intro + free_header + 1 item = 3 created
    assert run_counts["created"] == 3
    assert run_counts["updated"] == 0
    assert run_counts["reused"] == 0


def test_post_daily_pick_messages_rerun_protection_returns_flag(monkeypatch, tmp_path):
    daily_path = tmp_path / "daily.json"
    day_key = "2026-04-12"
    daily_path.write_text(
        __import__("json").dumps({day_key: {"run_state": {"completed": True}, "items": []}}),
        encoding="utf-8",
    )

    fake_client = FakeDiscordClient()
    monkeypatch.setattr(main, "DISCORD_DAILY_POSTS_FILE", str(daily_path))
    monkeypatch.setattr(main, "DISCORD_BOT_TOKEN", "token")
    monkeypatch.setattr(main, "DiscordClient", lambda session: fake_client)
    monkeypatch.setattr(main, "sleep_briefly", lambda: None)
    monkeypatch.setenv(main.DAILY_DATE_OVERRIDE_ENV, day_key)

    free_items = [{"title": "Game A", "url": "https://store.steampowered.com/app/1", "price": "Free", "score": 9}]
    run_counts, rerun_protection_active, _ = main.post_daily_pick_messages([], free_items, [], [])

    assert rerun_protection_active is True
    assert run_counts["created"] == 0
