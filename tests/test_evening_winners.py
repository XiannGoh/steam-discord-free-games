import json

import evening_winners as winners


class FakeSession:
    def __init__(self):
        self.headers = {}

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False


class FakeDiscordClient:
    def __init__(self, message_payloads, reaction_users=None, *, stale_winner_id=None):
        self.message_payloads = message_payloads
        self.reaction_users = reaction_users or {}
        self.stale_winner_id = stale_winner_id
        self.posts = []
        self.edits = []
        self.reaction_calls = []
        self.put_reactions = []

    def get_message(self, channel_id, message_id, *, context):
        if self.stale_winner_id and message_id == self.stale_winner_id:
            raise winners.DiscordMessageNotFoundError("missing")
        if message_id in self.message_payloads:
            return self.message_payloads[message_id]
        return {"id": message_id}

    def get_current_user(self, *, context):
        return {"id": "bot-1", "username": "bot-user"}

    def get_reaction_users(self, channel_id, message_id, encoded_emoji, *, context, limit=100, after=None):
        self.reaction_calls.append((channel_id, message_id, encoded_emoji, limit, after))
        return self.reaction_users.get(message_id, []) or self.reaction_users.get((channel_id, message_id, encoded_emoji), [])

    def edit_message(self, channel_id, message_id, content, *, context):
        self.edits.append((channel_id, message_id, content, context))
        return {"id": message_id}

    def post_message(self, channel_id, content, *, context):
        mid = f"w-{len(self.posts)+1}"
        self.posts.append((channel_id, content, mid, context))
        return {"id": mid, "channel_id": channel_id}

    def put_reaction(self, channel_id, message_id, encoded_emoji, *, context):
        self.put_reactions.append((channel_id, message_id, encoded_emoji, context))


def _patch_common(monkeypatch, path, fake, day_key):
    monkeypatch.setattr(winners, "DISCORD_DAILY_POSTS_FILE", str(path))
    monkeypatch.setattr(winners.requests, "Session", FakeSession)
    monkeypatch.setattr(winners, "DiscordClient", lambda session: fake)
    monkeypatch.setattr(winners, "DISCORD_BOT_TOKEN", "x")
    monkeypatch.setattr(winners, "DISCORD_WINNERS_CHANNEL_ID", "wchan")
    monkeypatch.setattr(winners, "DISCORD_GUILD_ID", "guild-1")
    monkeypatch.setenv(winners.WINNERS_DATE_OVERRIDE_ENV, day_key)


def _setup_daily(tmp_path):
    day_key = "2026-04-08"
    path = tmp_path / "daily.json"
    data = {
        "2026-04-07": {
            "items": [
                {"section": "free", "title": "Late Voted Earlier Day", "url": "shared-dupe", "channel_id": "c", "message_id": "m-late"},
            ]
        },
        day_key: {
            "items": [
                {"section": "demo_playtest", "title": "Demo Winner", "url": "demo-win", "channel_id": "c", "message_id": "m-demo"},
                {"section": "free", "title": "Same Game Repost", "url": "shared-dupe", "channel_id": "c", "message_id": "m-dupe"},
                {"section": "paid", "title": "Current Paid Winner", "url": "paid-win", "channel_id": "c", "message_id": "m-paid"},
            ]
        },
    }
    path.write_text(json.dumps(data), encoding="utf-8")
    return day_key, path


def test_winners_channel_posts_intro_sections_games_and_footer_in_order(monkeypatch, tmp_path):
    day_key, path = _setup_daily(tmp_path)
    payloads = {
        "m-demo": {"reactions": [{"emoji": {"name": "👍"}, "count": 2}]},
        "m-late": {"reactions": [{"emoji": {"name": "👍"}, "count": 3}]},
        "m-dupe": {"reactions": [{"emoji": {"name": "👍"}, "count": 2}]},
        "m-paid": {"reactions": [{"emoji": {"name": "👍"}, "count": 2}]},
    }
    reaction_users = {
        "m-demo": [{"id": "bot-1"}, {"id": "u-demo", "username": "demouser"}],
        "m-late": [{"id": "bot-1"}, {"id": "u1", "username": "jan"}, {"id": "u2", "username": "jerry"}],
        "m-paid": [{"id": "bot-1"}, {"id": "u3", "username": "thomas"}],
    }
    fake = FakeDiscordClient(payloads, reaction_users)
    _patch_common(monkeypatch, path, fake, day_key)

    winners.main()

    posted = [content for _, content, _, _ in fake.posts]
    assert posted[0].startswith("🏆 Daily Game Picks — Winners")
    assert posted[1] == "🧪 Demo & Playtest Winners"
    assert "Demo Winner" in posted[2]
    assert posted[3] == "🎮 Free Winners"
    assert "Late Voted Earlier Day" in posted[4]
    assert posted[5] == "💸 Paid Winners"
    assert "Current Paid Winner" in posted[6]
    assert posted[-1].startswith("🗓️ Daily Winners for")
    assert "Demo & Playtest Winners → [Jump](https://discord.com/channels/guild-1/wchan/w-2)" in posted[-1]
    assert "Free Winners → [Jump](https://discord.com/channels/guild-1/wchan/w-4)" in posted[-1]
    assert "Paid Winners → [Jump](https://discord.com/channels/guild-1/wchan/w-6)" in posted[-1]


def test_winners_footer_omits_missing_sections(monkeypatch, tmp_path):
    day_key = "2026-04-08"
    path = tmp_path / "daily.json"
    data = {day_key: {"items": [{"section": "free", "title": "Only Free", "url": "free-win", "channel_id": "c", "message_id": "m-free"}]}}
    path.write_text(json.dumps(data), encoding="utf-8")
    fake = FakeDiscordClient(
        {"m-free": {"reactions": [{"emoji": {"name": "👍"}, "count": 2}]}},
        {"m-free": [{"id": "bot-1"}, {"id": "u1", "username": "u1"}]},
    )
    _patch_common(monkeypatch, path, fake, day_key)
    winners.main()
    footer = fake.posts[-1][1]
    assert "Free Winners" in footer
    assert "Paid Winners" not in footer
    assert "Creator Winners" not in footer


def test_bookmark_added_to_winners_game_messages_not_daily_messages(monkeypatch, tmp_path):
    day_key, path = _setup_daily(tmp_path)
    fake = FakeDiscordClient(
        {
            "m-demo": {"reactions": [{"emoji": {"name": "👍"}, "count": 2}]},
            "m-late": {"reactions": [{"emoji": {"name": "👍"}, "count": 2}]},
            "m-paid": {"reactions": [{"emoji": {"name": "👍"}, "count": 2}]},
        },
        {
            "m-demo": [{"id": "bot-1"}, {"id": "u1", "username": "u1"}],
            "m-late": [{"id": "bot-1"}, {"id": "u2", "username": "u2"}],
            "m-paid": [{"id": "bot-1"}, {"id": "u3", "username": "u3"}],
        },
    )
    _patch_common(monkeypatch, path, fake, day_key)

    winners.main()

    reaction_targets = {(c, m) for c, m, e, _ in fake.put_reactions if e == winners.BOOKMARK_EMOJI_ENCODED}
    assert ("wchan", "w-3") in reaction_targets
    assert ("wchan", "w-5") in reaction_targets
    assert ("wchan", "w-7") in reaction_targets
    assert ("c", "m-demo") not in reaction_targets


def test_same_day_rerun_reuses_existing_intro_header_footer_and_game(monkeypatch, tmp_path):
    day_key, path = _setup_daily(tmp_path)
    data = json.loads(path.read_text(encoding="utf-8"))
    data[day_key]["winners_state"] = {
        "intro": {"channel_id": "wchan", "message_id": "intro-1"},
        "section_headers": {"free": {"channel_id": "wchan", "message_id": "header-free-1"}},
        "winner_messages": {"shared-dupe": {"channel_id": "wchan", "message_id": "winner-free-1"}},
        "footer": {"channel_id": "wchan", "message_id": "footer-1"},
        "winner_keys": ["shared-dupe"],
        "winner_vote_counts": {"shared-dupe": 1},
        "winner_entries": [
            {"winner_key": "shared-dupe", "section": "free", "title": "Late Voted Earlier Day", "url": "shared-dupe", "human_votes": 1, "voter_names": ["jan"]}
        ],
    }
    path.write_text(json.dumps(data), encoding="utf-8")
    fake = FakeDiscordClient(
        {
            "m-late": {"reactions": [{"emoji": {"name": "👍"}, "count": 2}]},
            "intro-1": {},
            "header-free-1": {},
            "winner-free-1": {},
            "footer-1": {},
        },
        {"m-late": [{"id": "bot-1"}, {"id": "u1", "username": "jan"}]},
    )
    _patch_common(monkeypatch, path, fake, day_key)
    winners.main()
    assert fake.posts == []
    assert fake.edits == []


def test_late_votes_edit_prior_day_individual_winner_message(monkeypatch, tmp_path):
    day_key = "2026-04-08"
    path = tmp_path / "daily.json"
    data = {
        "2026-04-07": {
            "items": [],
            "winners_state": {
                "winner_keys": ["shared-dupe"],
                "winner_vote_counts": {"shared-dupe": 2},
                "winner_entries": [
                    {"winner_key": "shared-dupe", "section": "free", "title": "Original Winner", "url": "shared-dupe", "human_votes": 2, "voter_names": ["jan", "jerry"]}
                ],
                "winner_messages": {"shared-dupe": {"channel_id": "wchan", "message_id": "winner-prev-1"}},
                "intro": {"channel_id": "wchan", "message_id": "intro-prev"},
                "section_headers": {"free": {"channel_id": "wchan", "message_id": "header-prev-free"}},
                "footer": {"channel_id": "wchan", "message_id": "footer-prev"},
            },
        },
        day_key: {"items": [{"section": "free", "title": "Same Game Repost", "url": "shared-dupe", "channel_id": "c", "message_id": "m-late"}]},
    }
    path.write_text(json.dumps(data), encoding="utf-8")
    fake = FakeDiscordClient(
        {"m-late": {"reactions": [{"emoji": {"name": "👍"}, "count": 4}]}, "winner-prev-1": {}, "intro-prev": {}, "header-prev-free": {}, "footer-prev": {}},
        {"m-late": [{"id": "bot-1"}, {"id": "u1", "username": "jan"}, {"id": "u2", "username": "jerry"}, {"id": "u3", "username": "akhil"}]},
    )
    _patch_common(monkeypatch, path, fake, day_key)
    winners.main()
    assert fake.posts == []
    assert any(entry[1] == "winner-prev-1" and "👍 3 votes" in entry[2] for entry in fake.edits)


def test_cross_day_duplicate_suppression_and_section_order(monkeypatch, tmp_path):
    day_key = "2026-04-08"
    path = tmp_path / "daily.json"
    data = {
        "2026-04-07": {"items": [], "winners_state": {"winner_keys": ["repeat-free"]}},
        day_key: {
            "items": [
                {"section": "free", "title": "Old Repeat", "url": "repeat-free", "channel_id": "c", "message_id": "m-repeat"},
                {"section": "demo_playtest", "title": "Demo Fresh", "url": "demo-fresh", "channel_id": "c", "message_id": "m-demo"},
                {"section": "paid", "title": "Paid Fresh", "url": "paid-fresh", "channel_id": "c", "message_id": "m-paid"},
            ]
        },
    }
    path.write_text(json.dumps(data), encoding="utf-8")
    fake = FakeDiscordClient(
        {
            "m-repeat": {"reactions": [{"emoji": {"name": "👍"}, "count": 2}]},
            "m-demo": {"reactions": [{"emoji": {"name": "👍"}, "count": 2}]},
            "m-paid": {"reactions": [{"emoji": {"name": "👍"}, "count": 2}]},
        },
        {
            "m-repeat": [{"id": "bot-1"}, {"id": "u1", "username": "u1"}],
            "m-demo": [{"id": "bot-1"}, {"id": "u2", "username": "u2"}],
            "m-paid": [{"id": "bot-1"}, {"id": "u3", "username": "u3"}],
        },
    )
    _patch_common(monkeypatch, path, fake, day_key)
    winners.main()

    body = "\n".join([p[1] for p in fake.posts])
    assert "Old Repeat" not in body
    assert body.index("🧪 Demo & Playtest Winners") < body.index("💸 Paid Winners")


def test_instagram_fallback_description_is_preserved():
    msg = winners.build_winner_game_message(
        {
            "title": "@creator",
            "url": "https://www.instagram.com/p/ABC123/",
            "human_votes": 2,
            "voter_names": ["Jan", "Jerry"],
        },
        section="instagram",
    )
    assert "Instagram post from @creator" in msg
    assert "caption unavailable in legacy state" in msg
