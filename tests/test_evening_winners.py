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
        return self.reaction_users.get(message_id, [])

    def edit_message(self, channel_id, message_id, content, *, context):
        self.edits.append((channel_id, message_id, content))
        return {"id": message_id}

    def post_message(self, channel_id, content, *, context):
        mid = f"w-{len(self.posts)+1}"
        self.posts.append((channel_id, content, mid))
        return {"id": mid}


def _patch_common(monkeypatch, path, fake, day_key):
    monkeypatch.setattr(winners, "DISCORD_DAILY_POSTS_FILE", str(path))
    monkeypatch.setattr(winners.requests, "Session", FakeSession)
    monkeypatch.setattr(winners, "DiscordClient", lambda session: fake)
    monkeypatch.setattr(winners, "DISCORD_BOT_TOKEN", "x")
    monkeypatch.setattr(winners, "DISCORD_DAILY_PICKS_CHANNEL_ID", "daily-picks")
    monkeypatch.setattr(winners, "DISCORD_WINNERS_CHANNEL_ID", "wchan")
    monkeypatch.setenv(winners.WINNERS_DATE_OVERRIDE_ENV, day_key)


def _setup_daily(tmp_path):
    day_key = "2026-04-08"
    path = tmp_path / "daily.json"
    data = {
        "2026-03-29": {
            "items": [
                {"section": "free", "title": "Old Outside Window", "url": "old-out", "channel_id": "c", "message_id": "m-old-out"}
            ]
        },
        "2026-03-30": {
            "items": [
                {"section": "free", "title": "Old Inside Window", "url": "old-in", "channel_id": "c", "message_id": "m-old-in"}
            ]
        },
        "2026-04-07": {
            "items": [
                {"section": "free", "title": "Late Voted Earlier Day", "url": "shared-dupe", "channel_id": "c", "message_id": "m-late"},
            ]
        },
        day_key: {
            "items": [
                {"section": "free", "title": "Same Game Repost", "url": "shared-dupe", "channel_id": "c", "message_id": "m-dupe"},
                {"section": "free", "title": "Only Bot Vote", "url": "bot-only", "channel_id": "c", "message_id": "m-bot-only"},
                {"section": "paid", "title": "Current Paid Winner", "url": "paid-win", "channel_id": "c", "message_id": "m-paid"},
            ]
        }
    }
    path.write_text(json.dumps(data), encoding="utf-8")
    return day_key, path


def test_winner_vote_rules_and_message_content(monkeypatch, tmp_path):
    day_key, path = _setup_daily(tmp_path)
    payloads = {
        "m-old-out": {"reactions": [{"emoji": {"name": "👍"}, "count": 2}]},
        "m-old-in": {"reactions": [{"emoji": {"name": "👍"}, "count": 1}]},
        "m-late": {"reactions": [{"emoji": {"name": "👍"}, "count": 3}]},
        "m-dupe": {"reactions": [{"emoji": {"name": "👍"}, "count": 2}]},
        "m-bot-only": {"reactions": [{"emoji": {"name": "👍"}, "count": 1}]},
        "m-paid": {"reactions": [{"emoji": {"name": "👍"}, "count": 2}]},
    }
    reaction_users = {
        "m-late": [
            {"id": "bot-1", "global_name": "Bot Name", "username": "bot-user"},
            {"id": "u1", "global_name": "Jan", "username": "jan123"},
            {"id": "u5", "global_name": "LateFan", "username": "latefan"},
        ],
        "m-paid": [
            {"id": "bot-1", "global_name": "Bot Name", "username": "bot-user"},
            {"id": "u2", "global_name": "Jerry", "username": "jerry1"},
            {"id": "u3", "global_name": None, "username": "akhil_user"},
        ],
    }
    fake = FakeDiscordClient(payloads, reaction_users)

    _patch_common(monkeypatch, path, fake, day_key)

    winners.main()

    assert len(fake.posts) == 1
    assert fake.posts[0][0] == "wchan"
    content = fake.posts[0][1]
    assert "Late Voted Earlier Day" in content
    assert "Old Inside Window" not in content  # only bot's default reaction
    assert "Old Outside Window" not in content  # outside 10-day lookback
    assert "Same Game Repost" not in content  # deduped against late-voted earlier post by url
    assert "Only Bot Vote" not in content
    assert "Current Paid Winner" in content and "👍 1 vote" in content
    assert "Voters — Jan, LateFan" in content
    assert "Voters — Jerry, akhil_user" in content
    assert "Bot Name" not in content


def test_winners_rerun_skip_then_edit_for_newly_eligible_winner(monkeypatch, tmp_path):
    day_key, path = _setup_daily(tmp_path)
    data = json.loads(path.read_text(encoding="utf-8"))
    data[day_key]["winners_state"] = {"message_id": "w-old", "winner_keys": ["paid-win", "shared-dupe"]}
    path.write_text(json.dumps(data), encoding="utf-8")

    payloads = {
        "m-late": {"reactions": [{"emoji": {"name": "👍"}, "count": 2}]},
        "m-dupe": {"reactions": [{"emoji": {"name": "👍"}, "count": 2}]},
        "m-paid": {"reactions": [{"emoji": {"name": "👍"}, "count": 2}]},
        "m-old-in": {"reactions": [{"emoji": {"name": "👍"}, "count": 1}]},
    }
    reaction_users = {
        "m-late": [{"id": "bot-1"}, {"id": "u1", "username": "u1"}],
        "m-paid": [{"id": "bot-1"}, {"id": "u2", "username": "u2"}],
    }
    fake_skip = FakeDiscordClient(payloads, reaction_users)
    _patch_common(monkeypatch, path, fake_skip, day_key)
    winners.main()
    assert fake_skip.posts == [] and fake_skip.edits == []

    data = json.loads(path.read_text(encoding="utf-8"))
    data["2026-04-07"]["items"].append(
        {"section": "free", "title": "Brand New", "url": "new-url", "channel_id": "c", "message_id": "m-new"}
    )
    path.write_text(json.dumps(data), encoding="utf-8")
    payloads["m-new"] = {"reactions": [{"emoji": {"name": "👍"}, "count": 2}]}
    reaction_users["m-new"] = [{"id": "bot-1"}, {"id": "u9", "username": "newvoter"}]

    fake_edit = FakeDiscordClient(payloads, reaction_users)
    monkeypatch.setattr(winners, "DiscordClient", lambda session: fake_edit)
    winners.main()
    assert len(fake_edit.edits) == 1
    assert fake_edit.edits[0][0] == "wchan"
    assert "Brand New" in fake_edit.edits[0][2]


def test_no_eligible_winners_and_no_existing_message_is_noop(monkeypatch, tmp_path):
    day_key = "2026-04-08"
    path = tmp_path / "daily.json"
    path.write_text(json.dumps({day_key: {"items": []}}), encoding="utf-8")
    fake = FakeDiscordClient({})
    _patch_common(monkeypatch, path, fake, day_key)
    winners.main()
    assert fake.posts == []
    assert fake.edits == []


def test_stale_daily_item_message_is_skipped(monkeypatch, tmp_path):
    day_key, path = _setup_daily(tmp_path)
    payloads = {"m-paid": {"reactions": [{"emoji": {"name": "👍"}, "count": 2}]}}

    class ItemStaleClient(FakeDiscordClient):
        def get_message(self, channel_id, message_id, *, context):
            if message_id == "m-late":
                raise winners.DiscordMessageNotFoundError("missing item")
            return super().get_message(channel_id, message_id, context=context)

    reaction_users = {"m-paid": [{"id": "bot-1", "username": "bot"}, {"id": "u9", "global_name": "Thomas", "username": "thomas"}]}
    fake = ItemStaleClient(payloads, reaction_users)
    _patch_common(monkeypatch, path, fake, day_key)
    winners.main()
    assert len(fake.posts) == 1
    assert "Current Paid Winner" in fake.posts[0][1]
    assert "Voters — Thomas" in fake.posts[0][1]


def test_dedupe_only_within_rolling_window_not_forever(monkeypatch, tmp_path):
    # Window for 2026-04-08 includes 2026-03-30..2026-04-08. It does not include 2026-03-29.
    day_key, path = _setup_daily(tmp_path)
    data = json.loads(path.read_text(encoding="utf-8"))
    data[day_key]["items"] = []
    data["2026-03-29"]["items"] = [
        {"section": "free", "title": "Old Return Winner", "url": "return-url", "channel_id": "c", "message_id": "m-return"},
    ]
    data[day_key]["items"].append(
        {"section": "free", "title": "Old Return Winner Reappears", "url": "return-url", "channel_id": "c", "message_id": "m-return-new"}
    )
    path.write_text(json.dumps(data), encoding="utf-8")

    payloads = {
        "m-return": {"reactions": [{"emoji": {"name": "👍"}, "count": 2}]},
        "m-return-new": {"reactions": [{"emoji": {"name": "👍"}, "count": 2}]},
    }
    reaction_users = {
        "m-return-new": [{"id": "bot-1"}, {"id": "u2", "username": "u2"}],
    }
    fake = FakeDiscordClient(payloads, reaction_users)
    _patch_common(monkeypatch, path, fake, day_key)
    winners.main()
    content = fake.posts[0][1]
    assert "Old Return Winner Reappears" in content


def test_build_winners_message_voter_truncation():
    winners_by_section = {
        "free": [
            {
                "title": "Game A",
                "url": "u-a",
                "human_votes": 9,
                "voter_names": ["Jan", "Jerry", "Akhil", "Thomas", "Charlie", "Kevin", "Raymond", "Rishabh", "Malphax"],
            }
        ],
        "paid": [],
        "instagram": [],
    }
    content = winners.build_winners_message(winners_by_section)
    assert "Voters — Jan, Jerry, Akhil, Thomas, Charlie, Kevin, +3 more" in content


def test_build_winners_message_compact_fallback_when_too_long(monkeypatch):
    winners_by_section = {
        "free": [
            {
                "title": f"Game {idx}",
                "url": f"https://example.com/{idx}",
                "human_votes": 2,
                "voter_names": [f"User {i}" for i in range(10)],
            }
            for idx in range(50)
        ],
        "paid": [],
        "instagram": [],
    }
    message = winners.build_winners_message(winners_by_section)
    assert len(message) > winners.DISCORD_MESSAGE_CHAR_LIMIT
    compact = winners.build_winners_message_compact(winners_by_section)
    assert "Voters —" not in compact
    assert "- Game 0 (2 votes)" in compact
