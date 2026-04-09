import json

import main


class FakeDiscordClient:
    def __init__(self, existing_ids=None, stale_ids=None):
        self.existing_ids = set(existing_ids or [])
        self.stale_ids = set(stale_ids or [])
        self.reactions = []

    def get_message(self, channel_id, message_id, *, context):
        if message_id in self.stale_ids:
            raise main.DiscordMessageNotFoundError("gone")
        if message_id in self.existing_ids:
            return {"id": message_id}
        raise RuntimeError("missing")

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
