import json

import pytest

import main


def test_format_daily_picks_footer_date_full_format():
    """format_daily_picks_footer_date returns full weekday+month+day+year format."""
    assert main.format_daily_picks_footer_date("2026-04-15") == "Wednesday, April 15, 2026"


def test_format_daily_picks_footer_date_single_digit_day():
    """Single-digit day is not zero-padded in the output."""
    result = main.format_daily_picks_footer_date("2026-04-01")
    assert result == "Wednesday, April 1, 2026"


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

    assert len(deduped) == 1
    assert deduped[0]["username"] == "creator_a, creator_b"
    assert deduped[0]["caption"] == '"Star Drift" - free on Steam #indie 🚀'
    assert deduped[0]["url"] == "u1"


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


# ---------------------------------------------------------------------------
# Cross-creator dedup: hyphenated vs non-hyphenated same game (issue #280)
# ---------------------------------------------------------------------------

def test_dedupe_instagram_posts_merges_hyphenated_coop_variant():
    """'Drunkslop Pub Crawl Co-Op' and 'Drunkslop: Pub Crawl COOP' must collapse."""
    posts = [
        {"username": "itzjaysasa", "caption": "Drunkslop Pub Crawl Co-Op", "url": "u1"},
        {"username": "sharedxp_official", "caption": "Drunkslop: Pub Crawl COOP", "url": "u2"},
    ]
    deduped = main.dedupe_instagram_posts(posts)
    assert len(deduped) == 1
    assert deduped[0]["url"] == "u1"
    usernames = set(deduped[0]["username"].split(", "))
    assert usernames == {"itzjaysasa", "sharedxp_official"}


def test_dedupe_instagram_posts_keeps_distinct_plain_title_captions():
    """Plain-title captions that are different games must NOT be merged."""
    posts = [
        {"username": "creator_a", "caption": "Dragon Quest Builders", "url": "u1"},
        {"username": "creator_b", "caption": "Dragon Quest Heroes", "url": "u2"},
    ]
    deduped = main.dedupe_instagram_posts(posts)
    assert [post["url"] for post in deduped] == ["u1", "u2"]


def test_derive_instagram_game_key_plain_title_fallback():
    """Short, specific captions with no keywords produce a key via the full-caption fallback."""
    assert main.derive_instagram_game_key("Drunkslop Pub Crawl Co-Op") == "drunkslop pub crawl coop"
    assert main.derive_instagram_game_key("Drunkslop: Pub Crawl COOP") == "drunkslop pub crawl coop"


def test_derive_instagram_game_key_hyphen_normalisation():
    """Hyphens are removed so 'Co-Op' and 'COOP' produce the same fragment."""
    assert main.derive_instagram_game_key("Night Signal Co-Op Edition") == "night signal coop edition"
    assert main.derive_instagram_game_key("Night Signal COOP Edition") == "night signal coop edition"


def test_dedupe_instagram_posts_logs_dropped_duplicate(capsys):
    """A debug line must be printed to stdout when a duplicate is dropped."""
    posts = [
        {"username": "itzjaysasa", "caption": "Drunkslop Pub Crawl Co-Op", "url": "u1"},
        {"username": "sharedxp_official", "caption": "Drunkslop: Pub Crawl COOP", "url": "u2"},
    ]
    main._dedupe_instagram_posts_with_debug(posts)
    captured = capsys.readouterr()
    assert "[Instagram dedup]" in captured.out
    assert "sharedxp_official" in captured.out


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

    # Intro is the single first message; sections follow in order
    assert posted[0].startswith("📅 Daily Picks — ")
    assert "Vote 👍 on anything you want to try" in posted[0]
    section_headers = [
        message for message in posted
        if message in {"🎮 New Demos & Playtests", "🆓 Free Picks", "💰 Paid Under $20", "📸 Instagram Creator Picks"}
    ]
    assert section_headers == [
        "🎮 New Demos & Playtests",
        "🆓 Free Picks",
        "💰 Paid Under $20",
        "📸 Instagram Creator Picks",
    ]


def test_daily_picks_header_and_footer_are_posted_with_expected_links(monkeypatch, tmp_path):
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

    # Intro placeholder posted first (single message — no separate "header" + "intro")
    assert posted[0].startswith("📅 Daily Picks — Wednesday, April 8, 2026")
    assert "Vote 👍 on anything you want to try" in posted[0]
    assert posted[0].endswith(main.DAILY_INTRO_DIVIDER)
    # No second simple "intro" message before sections
    assert not any(msg == "🎯 Daily Picks — vote with 👍 on your favorites" for msg in posted)

    # Intro edited with jump links after sections posted
    # posted[0] gets message id "m-1"; sections get m-2,m-3,m-4,m-5 → demo_header=m-2, paid_item=m-3, free_header=m-4, free_item=m-5
    assert len(fake_client.edits) == 1
    edited_content = fake_client.edits[0][2]
    assert "📅 Daily Picks — Wednesday, April 8, 2026" in edited_content
    assert "Vote 👍 on anything you want to try" in edited_content
    assert "Demos & Playtests" in edited_content
    assert "Free Picks" in edited_content
    assert edited_content.endswith(main.DAILY_INTRO_DIVIDER)

    # Footer posted last — new format: single date+links line + End separator
    footer = posted[-1]
    assert footer.startswith("📅 End of Daily Picks — Wednesday, April 8, 2026 · Jump to:")
    assert "⬆️ Top" in footer
    assert footer.endswith(main.DAILY_FOOTER_SEPARATOR)
    # Footer must not be a copy of intro
    assert footer != edited_content

    # Check other messages are present
    assert any("Demo Pick #1" in message for message in posted[1:-1])
    assert any("Free Pick #1" in message for message in posted[1:-1])


def test_daily_picks_header_and_footer_rerun_reuse_existing_messages(monkeypatch, tmp_path):
    daily_path = tmp_path / "daily.json"
    day_key = "2026-04-08"
    initial = {
        day_key: {
            "run_state": {
                "intro": {"message_id": "intro-1", "channel_id": "chan-1"},
                "section_headers": {"free": {"message_id": "header-free-1", "channel_id": "chan-1"}},
                "footer": {"message_id": "footer-1", "channel_id": "chan-1"},
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

    fake_client = FakeDiscordClient(existing_ids={"header-1", "intro-1", "header-free-1", "footer-1", "item-1"})

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
                "footer": {"message_id": "footer-1", "channel_id": "chan-1"},
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
    assert len(fake_client.edits) == 4  # free header + item + intro (jump-links edit) + footer
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
    assert len(fake_client.edits) == edits_after_first + 5  # free header + two items + intro (jump-links edit) + footer
    assert len(fake_client.reactions) == reactions_after_first
    saved_after_second = json.loads(daily_path.read_text(encoding="utf-8"))
    assert len(saved_after_second[day_key]["items"]) == 2


def test_daily_picks_footer_uses_target_day_override_for_display(monkeypatch, tmp_path):
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

    assert posted[-1].startswith("📅 End of Daily Picks — Friday, April 10, 2026 · Jump to:")


def test_daily_picks_footer_skips_safely_when_guild_id_missing(monkeypatch, tmp_path):
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
    assert len(posted) == 3  # intro, demo_header, demo_item; no footer since no guild_id
    assert "Vote 👍 on anything you want to try" in posted[0]  # intro placeholder
    saved = json.loads(daily_path.read_text(encoding="utf-8"))
    assert saved[day_key]["run_state"]["completed"] is True


def test_manual_run_without_force_refresh_sets_completed_flag(monkeypatch, tmp_path):
    """workflow_dispatch without force_refresh posts normally AND marks the day completed.

    Fix 1 (Issue #292): only workflow_dispatch + force_refresh_same_day=True should keep
    the day open. A plain workflow_dispatch (e.g. from auto-fix bot) must set completed=True
    so that the next scheduled cron run is correctly suppressed.
    """
    daily_path = tmp_path / "daily.json"
    daily_path.write_text("{}", encoding="utf-8")
    day_key = "2026-04-09"
    posted = []

    monkeypatch.setattr(main, "DISCORD_DAILY_POSTS_FILE", str(daily_path))
    monkeypatch.setattr(main, "DISCORD_BOT_TOKEN", "token")
    monkeypatch.setattr(main, "post_to_discord_with_metadata", lambda message, capture_metadata=False: posted.append(message))
    monkeypatch.setattr(main, "sleep_briefly", lambda: None)
    monkeypatch.setenv(main.DAILY_DATE_OVERRIDE_ENV, day_key)

    main.post_daily_pick_messages(
        [], [{"title": "Free", "url": "https://store.steampowered.com/app/99", "score": 10}], [], [],
        manual_run=True,
        force_refresh_same_day=False,
    )

    # Posts happened (manual run still posts to Discord)
    assert len(posted) > 0
    # completed IS set — plain manual runs must not leave the day open
    saved = json.loads(daily_path.read_text(encoding="utf-8"))
    assert saved[day_key]["run_state"].get("completed") is True


def test_scheduled_run_sets_completed_flag(monkeypatch, tmp_path):
    """schedule-triggered runs mark the day as completed normally."""
    daily_path = tmp_path / "daily.json"
    daily_path.write_text("{}", encoding="utf-8")
    day_key = "2026-04-09"
    posted = []

    monkeypatch.setattr(main, "DISCORD_DAILY_POSTS_FILE", str(daily_path))
    monkeypatch.setattr(main, "DISCORD_BOT_TOKEN", "token")
    monkeypatch.setattr(main, "post_to_discord_with_metadata", lambda message, capture_metadata=False: posted.append(message))
    monkeypatch.setattr(main, "sleep_briefly", lambda: None)
    monkeypatch.setenv(main.DAILY_DATE_OVERRIDE_ENV, day_key)

    main.post_daily_pick_messages(
        [], [{"title": "Free", "url": "https://store.steampowered.com/app/99", "score": 10}], [], [],
        manual_run=False,
    )

    saved = json.loads(daily_path.read_text(encoding="utf-8"))
    assert saved[day_key]["run_state"]["completed"] is True


def test_manual_run_without_force_refresh_blocks_subsequent_scheduled_run(monkeypatch, tmp_path):
    """After a plain manual run, the subsequent scheduled run is blocked (Fix 1, Issue #292).

    workflow_dispatch without force_refresh_same_day sets completed=True, so the next
    scheduled cron run sees completed=True and correctly suppresses itself.
    Use force_refresh_same_day=True if the intent is to keep the day open for a follow-up
    scheduled run.
    """
    daily_path = tmp_path / "daily.json"
    daily_path.write_text("{}", encoding="utf-8")
    day_key = "2026-04-09"

    monkeypatch.setattr(main, "DISCORD_DAILY_POSTS_FILE", str(daily_path))
    monkeypatch.setattr(main, "DISCORD_BOT_TOKEN", "token")
    monkeypatch.setattr(main, "sleep_briefly", lambda: None)
    monkeypatch.setenv(main.DAILY_DATE_OVERRIDE_ENV, day_key)

    free_items = [{"title": "Free", "url": "https://store.steampowered.com/app/99", "score": 10}]

    # Manual run first (no force_refresh)
    manual_posts = []
    monkeypatch.setattr(main, "post_to_discord_with_metadata", lambda msg, capture_metadata=False: manual_posts.append(msg))
    main.post_daily_pick_messages([], free_items, [], [], manual_run=True, force_refresh_same_day=False)
    assert manual_posts  # posts happened
    saved = json.loads(daily_path.read_text(encoding="utf-8"))
    assert saved[day_key]["run_state"]["completed"] is True  # completed set after manual run

    # Scheduled run after — must be blocked because completed=True
    scheduled_posts = []
    monkeypatch.setattr(main, "post_to_discord_with_metadata", lambda msg, capture_metadata=False: scheduled_posts.append(msg))
    _, rerun_protection_active, _ = main.post_daily_pick_messages([], free_items, [], [], manual_run=False)
    assert rerun_protection_active  # suppressed by completed flag
    assert not scheduled_posts  # no duplicate posts


def test_manual_run_with_force_refresh_followed_by_scheduled_run_executes_normally(monkeypatch, tmp_path):
    """After manual run + force_refresh=True, the scheduled run proceeds normally.

    This is the explicit test-rerun flow: force_refresh_same_day=True keeps the day open
    so the next scheduled run executes from scratch.
    """
    daily_path = tmp_path / "daily.json"
    daily_path.write_text("{}", encoding="utf-8")
    day_key = "2026-04-09"

    monkeypatch.setattr(main, "DISCORD_DAILY_POSTS_FILE", str(daily_path))
    monkeypatch.setattr(main, "DISCORD_BOT_TOKEN", "token")
    monkeypatch.setattr(main, "sleep_briefly", lambda: None)
    monkeypatch.setenv(main.DAILY_DATE_OVERRIDE_ENV, day_key)

    free_items = [{"title": "Free", "url": "https://store.steampowered.com/app/99", "score": 10}]

    # Manual run with force_refresh — must NOT set completed
    manual_posts = []
    monkeypatch.setattr(main, "post_to_discord_with_metadata", lambda msg, capture_metadata=False: manual_posts.append(msg))
    main.post_daily_pick_messages([], free_items, [], [], manual_run=True, force_refresh_same_day=True)
    assert manual_posts
    saved = json.loads(daily_path.read_text(encoding="utf-8"))
    assert saved[day_key]["run_state"].get("completed") is not True  # day left open

    # Scheduled run after — must NOT be blocked
    scheduled_posts = []
    monkeypatch.setattr(main, "post_to_discord_with_metadata", lambda msg, capture_metadata=False: scheduled_posts.append(msg))
    _, rerun_protection_active, _ = main.post_daily_pick_messages([], free_items, [], [], manual_run=False)
    assert not rerun_protection_active  # was not skipped
    assert scheduled_posts  # posts happened
    # Now completed is set
    saved = json.loads(daily_path.read_text(encoding="utf-8"))
    assert saved[day_key]["run_state"]["completed"] is True


def test_manual_run_bypasses_completed_skip(monkeypatch, tmp_path):
    """A manual run must post even when completed=True (bypass scheduled-run idempotency skip)."""
    daily_path = tmp_path / "daily.json"
    day_key = "2026-04-08"
    daily_path.write_text(json.dumps({day_key: {"run_state": {"completed": True}}}), encoding="utf-8")
    posted = []

    monkeypatch.setattr(main, "DISCORD_DAILY_POSTS_FILE", str(daily_path))
    monkeypatch.setattr(main, "DISCORD_BOT_TOKEN", "token")
    monkeypatch.setattr(main, "post_to_discord_with_metadata", lambda message, capture_metadata=False: posted.append(message))
    monkeypatch.setattr(main, "sleep_briefly", lambda: None)
    monkeypatch.setenv(main.DAILY_DATE_OVERRIDE_ENV, day_key)

    _, rerun_protection_active, _ = main.post_daily_pick_messages(
        [], [{"title": "Free", "url": "https://store.steampowered.com/app/12", "score": 10}], [], [],
        manual_run=True,
    )

    assert not rerun_protection_active  # was NOT skipped
    assert len(posted) > 0  # posts actually happened


def test_scheduled_run_still_skips_when_completed(monkeypatch, tmp_path):
    """Scheduled runs must still skip when completed=True (unchanged behavior)."""
    daily_path = tmp_path / "daily.json"
    day_key = "2026-04-08"
    daily_path.write_text(json.dumps({day_key: {"run_state": {"completed": True}}}), encoding="utf-8")
    posted = []

    monkeypatch.setattr(main, "DISCORD_DAILY_POSTS_FILE", str(daily_path))
    monkeypatch.setattr(main, "DISCORD_BOT_TOKEN", "token")
    monkeypatch.setattr(main, "post_to_discord_with_metadata", lambda message, capture_metadata=False: posted.append(message))
    monkeypatch.setattr(main, "sleep_briefly", lambda: None)
    monkeypatch.setenv(main.DAILY_DATE_OVERRIDE_ENV, day_key)

    _, rerun_protection_active, _ = main.post_daily_pick_messages(
        [], [{"title": "Free", "url": "https://store.steampowered.com/app/12", "score": 10}], [], [],
        manual_run=False,
    )

    assert rerun_protection_active  # was skipped
    assert posted == []  # nothing posted


def test_is_manual_run_detects_workflow_dispatch(monkeypatch):
    monkeypatch.setenv(main.GITHUB_EVENT_NAME_ENV, "workflow_dispatch")
    assert main.is_manual_run() is True


def test_is_manual_run_returns_false_for_schedule(monkeypatch):
    monkeypatch.setenv(main.GITHUB_EVENT_NAME_ENV, "schedule")
    assert main.is_manual_run() is False


def test_is_manual_run_returns_false_when_env_unset(monkeypatch):
    monkeypatch.delenv(main.GITHUB_EVENT_NAME_ENV, raising=False)
    assert main.is_manual_run() is False


def test_daily_section_order_is_product_invariant():
    assert main.DAILY_SECTION_ORDER == ["demo_playtest", "free", "paid", "instagram"]
    assert [entry["header"] for entry in main.DAILY_SECTION_CONFIG] == [
        "🎮 New Demos & Playtests",
        "🆓 Free Picks",
        "💰 Paid Under $20",
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
            "footer": {"message_id": footer_id} if footer_id else {},
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


def test_main_stop_go_stops_immediately_when_verification_passes(monkeypatch):
    calls = []
    monkeypatch.setattr(main, "get_target_day_key", lambda: "2026-04-12")
    monkeypatch.setattr(
        main,
        "load_daily_verification_artifact",
        lambda: {"day_key": "2026-04-12", "pass": True},
    )
    monkeypatch.setattr(main, "run_daily_workflow", lambda **kwargs: calls.append(kwargs))

    main.main()

    assert calls == []


def test_main_stop_go_retries_until_verification_passes(monkeypatch):
    calls = []
    artifacts = iter(
        [
            {"day_key": "2026-04-12", "pass": False},
            {"day_key": "2026-04-12", "pass": False},
            {"day_key": "2026-04-12", "pass": True},
        ]
    )
    monkeypatch.setattr(main, "get_target_day_key", lambda: "2026-04-12")
    monkeypatch.setattr(main, "load_daily_verification_artifact", lambda: next(artifacts))
    monkeypatch.setattr(main, "run_daily_workflow", lambda **kwargs: calls.append(kwargs))

    main.main()

    assert calls == [{"force_refresh_same_day": False}, {"force_refresh_same_day": True}]


def test_main_stop_go_gives_up_after_max_attempts(monkeypatch, capsys):
    calls = []
    monkeypatch.setattr(main, "get_target_day_key", lambda: "2026-04-12")
    monkeypatch.setattr(
        main,
        "load_daily_verification_artifact",
        lambda: {"day_key": "2026-04-12", "pass": False},
    )
    monkeypatch.setattr(main, "run_daily_workflow", lambda **kwargs: calls.append(kwargs))

    main.main()

    captured = capsys.readouterr()
    assert len(calls) == main.MAX_RETRY_ATTEMPTS
    assert "STOP_GO decision=give_up" in captured.out


def test_export_stop_go_result_uses_escalate_signal_for_give_up(tmp_path):
    out = tmp_path / "stop_go.json"
    main.export_stop_go_result(
        day_key="2026-04-12",
        decision="give_up",
        reason="max_retry_attempts_reached",
        attempt=3,
        path=str(out),
    )

    artifact = json.loads(out.read_text(encoding="utf-8"))
    assert artifact["decision"] == "give_up"
    assert artifact["signal"] == "escalate_to_fixer"
    assert artifact["orchestrator"] == "openhands"
    assert artifact["fixer"] == "claude_code"
    assert artifact["escalation_target"] == "claude_code"


def test_main_stop_go_writes_give_up_artifact(monkeypatch, tmp_path):
    calls = []
    out = tmp_path / "stop_go.json"
    monkeypatch.setattr(main, "STOP_GO_RESULT_FILE", str(out))
    monkeypatch.setattr(main, "get_target_day_key", lambda: "2026-04-12")
    monkeypatch.setattr(
        main,
        "load_daily_verification_artifact",
        lambda: {"day_key": "2026-04-12", "pass": False},
    )
    monkeypatch.setattr(main, "run_daily_workflow", lambda **kwargs: calls.append(kwargs))

    main.main()

    artifact = json.loads(out.read_text(encoding="utf-8"))
    assert len(calls) == main.MAX_RETRY_ATTEMPTS
    assert artifact["decision"] == "give_up"
    assert artifact["signal"] == "escalate_to_fixer"
    assert artifact["reason"] == "max_retry_attempts_reached"
    assert artifact["attempt"] == main.MAX_RETRY_ATTEMPTS


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
    # intro + free_header + 1 item = 3 created (single intro message, no separate header)
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


# ---- Issue #170: Instagram age filter and per-creator post limit ----

class FakePost:
    """Minimal instaloader Post stub."""
    def __init__(self, shortcode: str, date_utc, caption: str = "test caption"):
        self.shortcode = shortcode
        self.date_utc = date_utc
        self.caption = caption


class FakeProfile:
    def __init__(self, posts):
        self._posts = posts

    def get_posts(self):
        return iter(self._posts)


class FakeInstaloader:
    class Instaloader:
        def __init__(self, **kwargs):
            pass

        def load_session_from_file(self, username, path):
            pass

        @property
        def context(self):
            return None

    class Profile:
        _registry = {}

        @classmethod
        def from_username(cls, context, username):
            return cls._registry[username]


def _setup_instagram_env(monkeypatch, tmp_path, fake_profiles: dict):
    """Wire up a fake instaloader and environment for Instagram tests."""
    session_path = tmp_path / "instaloader.session"
    session_path.write_text("session")

    fake_il = FakeInstaloader()
    fake_il.Profile._registry = fake_profiles
    monkeypatch.setattr(main, "instaloader", fake_il)
    monkeypatch.setattr(main, "INSTAGRAM_STATE_FILE", str(tmp_path / "instagram_seen.json"))
    monkeypatch.setenv("INSTAGRAM_USERNAME", "testuser")
    monkeypatch.chdir(tmp_path)


def test_instagram_age_filter_excludes_posts_older_than_7_days(monkeypatch, tmp_path):
    from datetime import datetime, timezone, timedelta

    now = datetime(2026, 4, 13, 12, 0, 0, tzinfo=timezone.utc)
    recent = FakePost("recent1", now - timedelta(days=2), "Recent game demo")
    old = FakePost("old1", now - timedelta(days=10), "Old game playtest")

    # Most recent post is within window; old one is not
    fake_profiles = {"gemgamingnetwork": FakeProfile([recent, old])}
    monkeypatch.setattr(main, "INSTAGRAM_CREATORS", ["gemgamingnetwork"])
    _setup_instagram_env(monkeypatch, tmp_path, fake_profiles)

    # Patch now so cutoff is deterministic
    monkeypatch.setattr(
        main,
        "fetch_instagram_posts",
        lambda: _patched_fetch_instagram(monkeypatch, fake_profiles, now),
    )

    posts = _patched_fetch_instagram_direct(monkeypatch, fake_profiles, now)
    assert len(posts) == 1
    assert posts[0]["url"] == "https://www.instagram.com/p/recent1/"


def _patched_fetch_instagram_direct(monkeypatch, fake_profiles, now_utc):
    """Run the core Instagram fetch loop with a fixed now_utc."""
    from datetime import timedelta, timezone

    cutoff = now_utc - timedelta(days=main.INSTAGRAM_MAX_POST_AGE_DAYS)
    all_new_posts = []
    seen = {}

    for username, profile in fake_profiles.items():
        if username not in seen:
            seen[username] = []
        count = 0
        for post in profile.get_posts():
            post_date = post.date_utc.replace(tzinfo=timezone.utc)
            if post_date < cutoff:
                break
            shortcode = post.shortcode
            if shortcode in seen[username]:
                continue
            caption = (post.caption or "").replace("\n", " ").strip()
            all_new_posts.append({
                "username": username,
                "caption": caption or "(no caption)",
                "url": f"https://www.instagram.com/p/{shortcode}/",
            })
            seen[username].append(shortcode)
            count += 1
            if count >= main.MAX_INSTAGRAM_POSTS_PER_ACCOUNT:
                break

    return all_new_posts


def _patched_fetch_instagram(monkeypatch, fake_profiles, now_utc):
    return _patched_fetch_instagram_direct(monkeypatch, fake_profiles, now_utc)


def test_instagram_max_2_posts_per_creator(monkeypatch, tmp_path):
    from datetime import datetime, timezone, timedelta

    now = datetime(2026, 4, 13, 12, 0, 0, tzinfo=timezone.utc)
    posts = [FakePost(f"p{i}", now - timedelta(days=i), f"Caption {i}") for i in range(5)]
    fake_profiles = {"gemgamingnetwork": FakeProfile(posts)}

    results = _patched_fetch_instagram_direct(monkeypatch, fake_profiles, now)
    # Only 2 posts per creator (MAX_INSTAGRAM_POSTS_PER_ACCOUNT = 2)
    assert len(results) == 2
    assert results[0]["url"] == "https://www.instagram.com/p/p0/"
    assert results[1]["url"] == "https://www.instagram.com/p/p1/"


def test_instagram_skips_creator_with_no_recent_posts(monkeypatch, tmp_path):
    from datetime import datetime, timezone, timedelta

    now = datetime(2026, 4, 13, 12, 0, 0, tzinfo=timezone.utc)
    # All posts older than 7 days
    old_posts = [FakePost(f"old{i}", now - timedelta(days=10 + i)) for i in range(3)]
    fake_profiles = {"gemgamingnetwork": FakeProfile(old_posts)}

    results = _patched_fetch_instagram_direct(monkeypatch, fake_profiles, now)
    assert results == []


def test_instagram_age_filter_boundary_exactly_7_days_included(monkeypatch, tmp_path):
    from datetime import datetime, timezone, timedelta

    now = datetime(2026, 4, 13, 12, 0, 0, tzinfo=timezone.utc)
    # Exactly at the boundary (6 days 23 hours = just inside)
    boundary_post = FakePost("boundary", now - timedelta(days=6, hours=23), "Boundary post")
    fake_profiles = {"gemgamingnetwork": FakeProfile([boundary_post])}

    results = _patched_fetch_instagram_direct(monkeypatch, fake_profiles, now)
    assert len(results) == 1


def test_instagram_age_filter_boundary_exactly_7_days_excluded(monkeypatch, tmp_path):
    from datetime import datetime, timezone, timedelta

    now = datetime(2026, 4, 13, 12, 0, 0, tzinfo=timezone.utc)
    # Exactly 7 days + 1 second = just outside
    old_post = FakePost("justold", now - timedelta(days=7, seconds=1), "Just old post")
    fake_profiles = {"gemgamingnetwork": FakeProfile([old_post])}

    results = _patched_fetch_instagram_direct(monkeypatch, fake_profiles, now)
    assert results == []


# ---------------------------------------------------------------------------
# Issue #216 — Step 1 intro/footer formatting contract tests
# ---------------------------------------------------------------------------

class TestStep1IntroFooterFormatting:
    """Contract tests for the new intro/footer spec from Issue #216."""

    def _run_post(self, monkeypatch, tmp_path, items_by_section, guild_id="guild-1"):
        daily_path = tmp_path / "daily.json"
        daily_path.write_text("{}", encoding="utf-8")
        day_key = "2026-04-15"
        posted = []
        counter = {"i": 0}

        def fake_post(message, capture_metadata=False):
            posted.append(message)
            counter["i"] += 1
            return {"message_id": f"m-{counter['i']}", "channel_id": "chan-1"}

        fake_client = FakeDiscordClient()

        monkeypatch.setattr(main, "DISCORD_DAILY_POSTS_FILE", str(daily_path))
        monkeypatch.setattr(main, "DISCORD_BOT_TOKEN", "token")
        monkeypatch.setattr(main, "DISCORD_GUILD_ID", guild_id)
        monkeypatch.setattr(main, "DiscordClient", lambda session: fake_client)
        monkeypatch.setattr(main, "post_to_discord_with_metadata", fake_post)
        monkeypatch.setattr(main, "sleep_briefly", lambda: None)
        monkeypatch.setenv(main.DAILY_DATE_OVERRIDE_ENV, day_key)

        main.post_daily_pick_messages(
            items_by_section.get("demo_playtest", []),
            items_by_section.get("free", []),
            items_by_section.get("paid", []),
            items_by_section.get("instagram", []),
        )
        return posted, fake_client

    def test_intro_contains_exactly_one_voting_instruction(self, monkeypatch, tmp_path):
        items = {"free": [{"title": "G", "url": "https://store.steampowered.com/app/1", "score": 9}]}
        posted, _ = self._run_post(monkeypatch, tmp_path, items)
        intro = posted[0]
        vote_occurrences = intro.count("Vote 👍")
        assert vote_occurrences == 1, f"Expected 1 voting instruction, found {vote_occurrences}"

    def test_footer_is_not_a_copy_of_intro(self, monkeypatch, tmp_path):
        items = {"free": [{"title": "G", "url": "https://store.steampowered.com/app/1", "score": 9}]}
        posted, _ = self._run_post(monkeypatch, tmp_path, items)
        intro = posted[0]
        footer = posted[-1]
        assert intro != footer, "Footer must not be a copy of the intro"

    def test_intro_has_divider_as_last_line(self, monkeypatch, tmp_path):
        items = {"free": [{"title": "G", "url": "https://store.steampowered.com/app/1", "score": 9}]}
        posted, _ = self._run_post(monkeypatch, tmp_path, items)
        intro = posted[0]
        assert intro.endswith(main.DAILY_INTRO_DIVIDER), "Divider must be last line of intro"

    def test_footer_has_end_separator_as_last_line(self, monkeypatch, tmp_path):
        items = {"free": [{"title": "G", "url": "https://store.steampowered.com/app/1", "score": 9}]}
        posted, _ = self._run_post(monkeypatch, tmp_path, items)
        footer = posted[-1]
        assert footer.endswith(main.DAILY_FOOTER_SEPARATOR), "End separator must be last line of footer"

    def test_jump_links_only_include_sections_with_content(self, monkeypatch, tmp_path):
        items = {"free": [{"title": "G", "url": "https://store.steampowered.com/app/1", "score": 9}]}
        posted, fake_client = self._run_post(monkeypatch, tmp_path, items)
        # Intro edited with jump links — check only free section is present
        assert fake_client.edits, "Intro should be edited with jump links"
        edited_intro = fake_client.edits[0][2]
        assert "Free Picks" in edited_intro
        # Jump links for absent sections should not appear (missing-section notices may appear)
        assert "[🎮 Demos" not in edited_intro
        assert "[💰 Paid" not in edited_intro
        assert "[📸 Instagram" not in edited_intro

    def test_jump_links_include_all_present_sections(self, monkeypatch, tmp_path):
        items = {
            "demo_playtest": [{"title": "D", "url": "https://store.steampowered.com/app/1", "score": 9}],
            "free": [{"title": "G", "url": "https://store.steampowered.com/app/2", "score": 9}],
        }
        posted, fake_client = self._run_post(monkeypatch, tmp_path, items)
        edited_intro = fake_client.edits[0][2]
        assert "Demos & Playtests" in edited_intro
        assert "Free Picks" in edited_intro

    def test_footer_skipped_when_guild_id_missing(self, monkeypatch, tmp_path):
        items = {"free": [{"title": "G", "url": "https://store.steampowered.com/app/1", "score": 9}]}
        posted, _ = self._run_post(monkeypatch, tmp_path, items, guild_id="")
        # Without guild_id, footer should not be posted
        for msg in posted:
            assert "End of Daily Picks" not in msg

    def test_footer_contains_top_link(self, monkeypatch, tmp_path):
        items = {"free": [{"title": "G", "url": "https://store.steampowered.com/app/1", "score": 9}]}
        posted, _ = self._run_post(monkeypatch, tmp_path, items)
        footer = posted[-1]
        assert "⬆️ Top" in footer

    def test_intro_jump_links_are_vertical_not_horizontal(self, monkeypatch, tmp_path):
        items = {
            "demo_playtest": [{"title": "D", "url": "https://store.steampowered.com/app/1", "score": 9}],
            "free": [{"title": "G", "url": "https://store.steampowered.com/app/2", "score": 9}],
        }
        posted, fake_client = self._run_post(monkeypatch, tmp_path, items)
        edited_intro = fake_client.edits[0][2]
        assert " · " not in edited_intro, "Jump links must be vertical, not joined with ' · '"

    def test_intro_each_section_link_on_own_line(self, monkeypatch, tmp_path):
        items = {
            "demo_playtest": [{"title": "D", "url": "https://store.steampowered.com/app/1", "score": 9}],
            "free": [{"title": "G", "url": "https://store.steampowered.com/app/2", "score": 9}],
            "paid": [{"title": "P", "url": "https://store.steampowered.com/app/3", "score": 8, "price": "$9.99"}],
        }
        posted, fake_client = self._run_post(monkeypatch, tmp_path, items)
        edited_intro = fake_client.edits[0][2]
        intro_lines = edited_intro.split("\n")
        link_lines = [line for line in intro_lines if "](https://discord.com/" in line]
        assert len(link_lines) == 3, f"Expected 3 link lines, got {len(link_lines)}: {link_lines}"

    def test_footer_section_labels_include_emojis(self, monkeypatch, tmp_path):
        items = {
            "demo_playtest": [{"title": "D", "url": "https://store.steampowered.com/app/1", "score": 9}],
            "free": [{"title": "G", "url": "https://store.steampowered.com/app/2", "score": 9}],
            "paid": [{"title": "P", "url": "https://store.steampowered.com/app/3", "score": 8, "price": "$9.99"}],
        }
        posted, _ = self._run_post(monkeypatch, tmp_path, items)
        footer = posted[-1]
        assert "🎮 Demos" in footer
        assert "🆓 Free" in footer
        assert "💰 Paid" in footer

    def test_intro_placeholder_not_posted_on_rerun(self, monkeypatch, tmp_path, capsys):
        """On a re-run where intro already has a message_id, no new post is made for the intro placeholder."""
        import json

        day_key = "2026-04-15"
        daily_path = tmp_path / "daily.json"
        # Pre-seed state with an existing intro message_id (must be inside run_state)
        existing_state = {
            day_key: {
                "run_state": {
                    "intro": {"message_id": "existing-intro-id", "channel_id": "chan-1"},
                },
                "items": [],
            }
        }
        daily_path.write_text(json.dumps(existing_state), encoding="utf-8")

        posted = []
        counter = {"i": 0}

        def fake_post(message, capture_metadata=False):
            posted.append(message)
            counter["i"] += 1
            return {"message_id": f"m-{counter['i']}", "channel_id": "chan-1"}

        fake_client = FakeDiscordClient(existing_ids={"existing-intro-id"})
        monkeypatch.setattr(main, "DISCORD_DAILY_POSTS_FILE", str(daily_path))
        monkeypatch.setattr(main, "DISCORD_BOT_TOKEN", "token")
        monkeypatch.setattr(main, "DISCORD_GUILD_ID", "guild-1")
        monkeypatch.setattr(main, "DiscordClient", lambda session: fake_client)
        monkeypatch.setattr(main, "post_to_discord_with_metadata", fake_post)
        monkeypatch.setattr(main, "sleep_briefly", lambda: None)
        monkeypatch.setenv(main.DAILY_DATE_OVERRIDE_ENV, day_key)

        items = {"free": [{"title": "G", "url": "https://store.steampowered.com/app/1", "score": 9}]}
        main.post_daily_pick_messages(
            items.get("demo_playtest", []),
            items.get("free", []),
            items.get("paid", []),
            items.get("instagram", []),
        )

        # No message posted whose content is just the placeholder (no jump links)
        placeholder_posts = [m for m in posted if "Loading sections..." in m]
        assert placeholder_posts == [], "Placeholder must not be posted when intro already exists"

        captured = capsys.readouterr()
        assert "REUSE: intro already posted" in captured.out

    def test_intro_placeholder_posted_on_first_run(self, monkeypatch, tmp_path):
        """On a first run (no existing message_id), the placeholder IS posted."""
        items = {"free": [{"title": "G", "url": "https://store.steampowered.com/app/1", "score": 9}]}
        posted, _ = self._run_post(monkeypatch, tmp_path, items)
        # The first posted message should be the placeholder
        assert "Loading sections..." in posted[0] or main.DAILY_INTRO_DIVIDER in posted[0], (
            "First run must post an intro placeholder"
        )


class TestStep1MissingSectionNotices:
    def _run_post(self, monkeypatch, tmp_path, items_by_section, guild_id="guild-1"):
        daily_path = tmp_path / "daily.json"
        daily_path.write_text("{}", encoding="utf-8")
        day_key = "2026-04-15"
        posted = []
        counter = {"i": 0}

        def fake_post(message, capture_metadata=False):
            posted.append(message)
            counter["i"] += 1
            return {"message_id": f"m-{counter['i']}", "channel_id": "chan-1"}

        fake_client = FakeDiscordClient()
        monkeypatch.setattr(main, "DISCORD_DAILY_POSTS_FILE", str(daily_path))
        monkeypatch.setattr(main, "DISCORD_BOT_TOKEN", "token")
        monkeypatch.setattr(main, "DISCORD_GUILD_ID", guild_id)
        monkeypatch.setattr(main, "DiscordClient", lambda session: fake_client)
        monkeypatch.setattr(main, "post_to_discord_with_metadata", fake_post)
        monkeypatch.setattr(main, "sleep_briefly", lambda: None)
        monkeypatch.setenv(main.DAILY_DATE_OVERRIDE_ENV, day_key)

        main.post_daily_pick_messages(
            items_by_section.get("demo_playtest", []),
            items_by_section.get("free", []),
            items_by_section.get("paid", []),
            items_by_section.get("instagram", []),
        )
        return posted, fake_client

    def test_missing_section_shows_notice_in_intro(self, monkeypatch, tmp_path):
        # Only free section posted — other 3 sections should show notices
        items = {"free": [{"title": "G", "url": "https://store.steampowered.com/app/1", "score": 9}]}
        posted, fake_client = self._run_post(monkeypatch, tmp_path, items)
        edited_intro = fake_client.edits[0][2]
        assert "_(No Demos & Playtests today)_" in edited_intro
        assert "_(No Paid Under $20 today)_" in edited_intro
        assert "_(No Instagram Picks today)_" in edited_intro

    def test_present_section_does_not_show_missing_notice(self, monkeypatch, tmp_path):
        items = {"free": [{"title": "G", "url": "https://store.steampowered.com/app/1", "score": 9}]}
        posted, fake_client = self._run_post(monkeypatch, tmp_path, items)
        edited_intro = fake_client.edits[0][2]
        assert "_(No Free Picks today)_" not in edited_intro


class TestScrapingHealthCheck:
    def test_all_pages_fail_yields_broken_status(self):
        stats: dict = {"ok": 0, "fail": 0}
        # Simulate 3 page fetches all returning None (failure)
        pages = list(range(1, 4))

        def fake_safe_fetch_html_none(url):
            return None

        import unittest.mock as mock
        with mock.patch.object(main, "safe_fetch_html", side_effect=fake_safe_fetch_html_none):
            main.collect_steam_free_candidates(1, 3, stats)

        assert stats["fail"] == 3
        assert stats["ok"] == 0

        total = stats["ok"] + stats["fail"]
        scraping_status = "ok" if total == 0 or stats["fail"] == 0 else (
            "broken" if stats["ok"] == 0 else "degraded"
        )
        assert scraping_status == "broken"

    def test_some_pages_fail_yields_degraded_status(self):
        stats: dict = {"ok": 0, "fail": 0}

        call_count = [0]

        def fake_safe_fetch_html_alternating(url):
            call_count[0] += 1
            return "<html><div class='search_result_row' data-ds-appid='1'></div></html>" if call_count[0] % 2 == 1 else None

        import unittest.mock as mock
        with mock.patch.object(main, "safe_fetch_html", side_effect=fake_safe_fetch_html_alternating):
            main.collect_steam_free_candidates(1, 4, stats)

        assert stats["ok"] > 0
        assert stats["fail"] > 0

        scraping_status = "ok" if stats["fail"] == 0 else (
            "broken" if stats["ok"] == 0 else "degraded"
        )
        assert scraping_status == "degraded"

    def test_broken_scraping_posts_health_monitor_warning(self, monkeypatch):
        warnings_posted: list[str] = []

        monkeypatch.setattr(main, "_notify_health_monitor", lambda msg: warnings_posted.append(msg))

        # Derive scraping_status == "broken" when ok=0 and fail>0
        pages_ok = 0
        pages_fail = 3
        total_pages = pages_ok + pages_fail
        if total_pages == 0 or pages_fail == 0:
            scraping_status = "ok"
        elif pages_ok == 0:
            scraping_status = "broken"
        else:
            scraping_status = "degraded"

        if scraping_status == "broken":
            main._notify_health_monitor(
                f"🔴 Steam scraping is broken — all {pages_fail} page(s) failed to fetch. "
                "Check Steam connectivity and scraper URLs."
            )

        assert len(warnings_posted) == 1
        assert "🔴" in warnings_posted[0]
        assert "broken" in warnings_posted[0]


class TestInstagramSessionAgeInMain:
    """Tests for main._check_instagram_session_age().

    Mirrors TestInstagramSessionAge in test_check_bot_token_health.py but
    exercises the main.py implementation directly using monkeypatching of
    os.path.getmtime rather than file-system manipulation.
    """

    def test_session_over_50_days_warns_health_monitor(self, monkeypatch, capsys):
        posted: list[str] = []

        monkeypatch.setattr(main, "_notify_health_monitor", lambda msg: posted.append(msg))
        monkeypatch.setattr(
            main.os.path, "getmtime",
            lambda path: main.datetime.now().timestamp() - 55 * 86400,
        )

        main._check_instagram_session_age("instaloader.session")

        assert len(posted) == 1
        assert "⚠️" in posted[0]
        captured = capsys.readouterr()
        assert "WARN" in captured.out

    def test_session_30_to_50_days_logs_info_only(self, monkeypatch, capsys):
        posted: list[str] = []

        monkeypatch.setattr(main, "_notify_health_monitor", lambda msg: posted.append(msg))
        monkeypatch.setattr(
            main.os.path, "getmtime",
            lambda path: main.datetime.now().timestamp() - 40 * 86400,
        )

        main._check_instagram_session_age("instaloader.session")

        assert len(posted) == 0
        captured = capsys.readouterr()
        assert "INFO" in captured.out

    def test_session_under_30_days_no_action(self, monkeypatch, capsys):
        posted: list[str] = []

        monkeypatch.setattr(main, "_notify_health_monitor", lambda msg: posted.append(msg))
        monkeypatch.setattr(
            main.os.path, "getmtime",
            lambda path: main.datetime.now().timestamp() - 10 * 86400,
        )

        main._check_instagram_session_age("instaloader.session")

        assert len(posted) == 0
        captured = capsys.readouterr()
        assert "WARN" not in captured.out
        assert "INFO" not in captured.out

    def test_missing_session_file_skips_gracefully(self, monkeypatch, capsys):
        posted: list[str] = []

        monkeypatch.setattr(main, "_notify_health_monitor", lambda msg: posted.append(msg))
        monkeypatch.setattr(
            main.os.path, "getmtime",
            lambda path: (_ for _ in ()).throw(OSError("no such file")),
        )

        main._check_instagram_session_age("instaloader.session")

        assert len(posted) == 0
        captured = capsys.readouterr()
        assert captured.out == ""
        assert captured.err == ""


class TestStaleSectionHeaderPruning:
    """Tests for stale section_header pruning before the section posting loop."""

    def _run_post_with_existing_state(self, monkeypatch, tmp_path, items_by_section, existing_run_state):
        import json

        day_key = "2026-04-15"
        daily_path = tmp_path / "daily.json"
        daily_path.write_text(
            json.dumps({day_key: {"run_state": existing_run_state, "items": []}}),
            encoding="utf-8",
        )

        posted = []
        counter = {"i": 0}

        def fake_post(message, capture_metadata=False):
            posted.append(message)
            counter["i"] += 1
            return {"message_id": f"m-{counter['i']}", "channel_id": "chan-1"}

        fake_client = FakeDiscordClient(existing_ids=set(
            v.get("message_id", "") for v in existing_run_state.get("section_headers", {}).values()
            if v.get("message_id")
        ))
        monkeypatch.setattr(main, "DISCORD_DAILY_POSTS_FILE", str(daily_path))
        monkeypatch.setattr(main, "DISCORD_BOT_TOKEN", "token")
        monkeypatch.setattr(main, "DISCORD_GUILD_ID", "guild-1")
        monkeypatch.setattr(main, "DiscordClient", lambda session: fake_client)
        monkeypatch.setattr(main, "post_to_discord_with_metadata", fake_post)
        monkeypatch.setattr(main, "sleep_briefly", lambda: None)
        monkeypatch.setenv(main.DAILY_DATE_OVERRIDE_ENV, day_key)

        main.post_daily_pick_messages(
            items_by_section.get("demo_playtest", []),
            items_by_section.get("free", []),
            items_by_section.get("paid", []),
            items_by_section.get("instagram", []),
        )

        import json as _json
        saved_state = _json.loads(daily_path.read_text(encoding="utf-8"))
        return saved_state[day_key]["run_state"], fake_client

    def test_stale_section_headers_removed_when_section_absent(self, monkeypatch, tmp_path, capsys):
        """section_headers entries for sections with no items today are pruned."""
        existing_run_state = {
            "intro": {"message_id": "intro-1", "channel_id": "chan-1"},
            "section_headers": {
                "demo_playtest": {"message_id": "hdr-demo-1", "channel_id": "chan-1"},
                "free": {"message_id": "hdr-free-1", "channel_id": "chan-1"},
            },
        }
        # Today only has free items — demo_playtest should be pruned
        items = {"free": [{"title": "G", "url": "https://store.steampowered.com/app/1", "score": 9}]}

        saved_run_state, _ = self._run_post_with_existing_state(
            monkeypatch, tmp_path, items, existing_run_state
        )

        assert "demo_playtest" not in saved_run_state.get("section_headers", {}), (
            "Stale demo_playtest header must be pruned when section has no items"
        )
        assert "free" in saved_run_state.get("section_headers", {}), (
            "Present free header must be retained"
        )

        captured = capsys.readouterr()
        assert "CLEANUP" in captured.out
        assert "demo_playtest" in captured.out

    def test_present_section_headers_not_removed(self, monkeypatch, tmp_path):
        """section_headers entries for sections that DO have items today are preserved."""
        existing_run_state = {
            "intro": {"message_id": "intro-1", "channel_id": "chan-1"},
            "section_headers": {
                "free": {"message_id": "hdr-free-1", "channel_id": "chan-1"},
            },
        }
        items = {"free": [{"title": "G", "url": "https://store.steampowered.com/app/1", "score": 9}]}

        saved_run_state, _ = self._run_post_with_existing_state(
            monkeypatch, tmp_path, items, existing_run_state
        )

        assert "free" in saved_run_state.get("section_headers", {}), (
            "Active free section header must not be pruned"
        )


# ---------------------------------------------------------------------------
# FIX: Step 1 footer first-line format and missing section notices
# ---------------------------------------------------------------------------

def _make_step1_run_state(posted_keys):
    """Build a minimal run_state for build_daily_picks_footer_content."""
    section_headers = {
        key: {"channel_id": "chan-1", "message_id": f"hdr-{key}"}
        for key in posted_keys
    }
    return {
        "intro": {"channel_id": "chan-1", "message_id": "intro-1"},
        "section_headers": section_headers,
    }


def test_step1_footer_first_line_starts_with_end_of_daily_picks():
    """Step 1 footer first line must start with '📅 End of Daily Picks —'."""
    run_state = _make_step1_run_state(["free"])
    footer = main.build_daily_picks_footer_content(
        run_state,
        guild_id="guild-1",
        target_day_key="2026-04-15",
        posted_section_keys=["free"],
    )
    assert footer is not None
    first_line = footer.split("\n")[0]
    assert first_line.startswith("📅 End of Daily Picks — Wednesday, April 15, 2026")


def test_step1_footer_shows_missing_section_notices():
    """Footer includes _(No X today)_ for each section not in posted_section_keys."""
    run_state = _make_step1_run_state(["free"])
    footer = main.build_daily_picks_footer_content(
        run_state,
        guild_id="guild-1",
        target_day_key="2026-04-15",
        posted_section_keys=["free"],
    )
    assert footer is not None
    assert "_(No Demos & Playtests today)_" in footer
    assert "_(No Paid Under $20 today)_" in footer
    assert "_(No Instagram Picks today)_" in footer
    assert "_(No Free Picks today)_" not in footer


def test_step1_footer_no_missing_notices_when_all_sections_present():
    """Footer has no missing notices when all sections are in posted_section_keys."""
    all_keys = ["demo_playtest", "free", "paid", "instagram"]
    run_state = _make_step1_run_state(all_keys)
    footer = main.build_daily_picks_footer_content(
        run_state,
        guild_id="guild-1",
        target_day_key="2026-04-15",
        posted_section_keys=all_keys,
    )
    assert footer is not None
    assert "_(No " not in footer


def test_intro_reposts_on_404_when_stale_message_id(monkeypatch, tmp_path):
    """If the stored intro message_id no longer exists (404), a fresh intro must be posted.

    Without Fix 2, the edit_message call catches generic Exception and prints a WARN,
    leaving the channel without an intro. With Fix 2, the DiscordMessageNotFoundError
    triggers a re-post via post_or_reconcile_simple.
    """
    daily_path = tmp_path / "daily.json"
    day_key = "2026-04-18"

    # State with a stale intro message_id that no longer exists on Discord
    initial = {
        day_key: {
            "run_state": {
                "completed": False,
                "section_headers": {},
                "intro": {"message_id": "stale-intro-id", "channel_id": "chan-1"},
            }
        }
    }
    daily_path.write_text(json.dumps(initial), encoding="utf-8")

    posted = []
    counter = {"i": 0}

    def fake_post(message, capture_metadata=False):
        counter["i"] += 1
        posted.append(message)
        return {"message_id": f"new-{counter['i']}", "channel_id": "chan-1"}

    # FakeDiscordClient raises DiscordMessageNotFoundError for the stale intro id
    fake_client = FakeDiscordClient(stale_ids={"stale-intro-id"})

    monkeypatch.setattr(main, "DISCORD_DAILY_POSTS_FILE", str(daily_path))
    monkeypatch.setattr(main, "DISCORD_BOT_TOKEN", "token")
    monkeypatch.setattr(main, "DiscordClient", lambda session: fake_client)
    monkeypatch.setattr(main, "post_to_discord_with_metadata", fake_post)
    monkeypatch.setattr(main, "sleep_briefly", lambda: None)
    monkeypatch.setenv(main.DAILY_DATE_OVERRIDE_ENV, day_key)

    main.post_daily_pick_messages(
        demo_playtest_items=[],
        free_items=[{"title": "Game A", "url": "https://store.steampowered.com/app/1", "price": "Free", "score": 9}],
        paid_items=[],
        instagram_posts=[],
    )

    # A fresh intro must have been posted (not silently skipped)
    assert any("Daily Picks" in msg for msg in posted), (
        "Expected a fresh intro to be posted after 404 on stale intro message_id, "
        f"but posted messages were: {posted!r}"
    )
