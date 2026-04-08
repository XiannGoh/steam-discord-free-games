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


def _setup_daily(tmp_path):
    day_key = "2026-04-08"
    path = tmp_path / "daily.json"
    data = {
        day_key: {
            "items": [
                {"section": "free", "title": "One", "url": "u1", "channel_id": "c", "message_id": "m1"},
                {"section": "free", "title": "Two", "url": "u2", "channel_id": "c", "message_id": "m2"},
                {"section": "paid", "title": "Three", "url": "u3", "channel_id": "c", "message_id": "m3"},
            ]
        }
    }
    path.write_text(json.dumps(data), encoding="utf-8")
    return day_key, path


def test_winner_vote_rules_and_message_content(monkeypatch, tmp_path):
    day_key, path = _setup_daily(tmp_path)
    payloads = {
        "m1": {"reactions": [{"emoji": {"name": "👍"}, "count": 1}]},
        "m2": {"reactions": [{"emoji": {"name": "👍"}, "count": 2}]},
        "m3": {"reactions": [{"emoji": {"name": "👍"}, "count": 3}]},
    }
    reaction_users = {
        "m2": [
            {"id": "bot-1", "global_name": "Bot Name", "username": "bot-user"},
            {"id": "u1", "global_name": "Jan", "username": "jan123"},
        ],
        "m3": [
            {"id": "bot-1", "global_name": "Bot Name", "username": "bot-user"},
            {"id": "u2", "global_name": "Jerry", "username": "jerry1"},
            {"id": "u3", "global_name": None, "username": "akhil_user"},
            {"id": "u3", "global_name": None, "username": "akhil_user"},
            {"id": "u4"},
        ],
    }
    fake = FakeDiscordClient(payloads, reaction_users)

    monkeypatch.setattr(winners, "DISCORD_DAILY_POSTS_FILE", str(path))
    monkeypatch.setattr(winners.requests, "Session", FakeSession)
    monkeypatch.setattr(winners, "DiscordClient", lambda session: fake)
    monkeypatch.setattr(winners, "DISCORD_BOT_TOKEN", "x")
    monkeypatch.setattr(winners, "DISCORD_DAILY_PICKS_CHANNEL_ID", None)
    monkeypatch.setattr(winners, "DISCORD_WINNERS_CHANNEL_ID", None)
    monkeypatch.setenv(winners.WINNERS_DATE_OVERRIDE_ENV, day_key)

    winners.main()

    assert len(fake.posts) == 1
    assert fake.posts[0][0] == "c"
    content = fake.posts[0][1]
    assert "One" not in content
    assert "Two" in content and "👍 1 vote" in content
    assert "Three" in content and "👍 2 votes" in content
    assert "Voters — Jan" in content
    assert "Voters — Jerry, akhil_user, User u4" in content
    assert "Bot Name" not in content


def test_winners_rerun_skip_edit_and_recovery(monkeypatch, tmp_path):
    day_key, path = _setup_daily(tmp_path)
    data = json.loads(path.read_text(encoding="utf-8"))
    same_message = winners.build_winners_message({"free": [], "paid": [], "instagram": []})
    same_hash = winners.hashlib.sha256(same_message.encode("utf-8")).hexdigest()
    data[day_key]["winners_state"] = {"message_id": "w-old", "content_hash": same_hash}
    data[day_key]["items"] = []
    path.write_text(json.dumps(data), encoding="utf-8")

    fake_skip = FakeDiscordClient({})
    monkeypatch.setattr(winners, "DISCORD_DAILY_POSTS_FILE", str(path))
    monkeypatch.setattr(winners.requests, "Session", FakeSession)
    monkeypatch.setattr(winners, "DiscordClient", lambda session: fake_skip)
    monkeypatch.setattr(winners, "DISCORD_BOT_TOKEN", "x")
    monkeypatch.setattr(winners, "DISCORD_DAILY_PICKS_CHANNEL_ID", "daily-picks")
    monkeypatch.setattr(winners, "DISCORD_WINNERS_CHANNEL_ID", "wchan")
    monkeypatch.setenv(winners.WINNERS_DATE_OVERRIDE_ENV, day_key)
    winners.main()
    assert fake_skip.posts == [] and fake_skip.edits == []

    data = json.loads(path.read_text(encoding="utf-8"))
    data[day_key]["winners_state"]["content_hash"] = "different"
    path.write_text(json.dumps(data), encoding="utf-8")
    fake_edit = FakeDiscordClient({})
    monkeypatch.setattr(winners, "DiscordClient", lambda session: fake_edit)
    winners.main()
    assert len(fake_edit.edits) == 1
    assert fake_edit.edits[0][0] == "daily-picks"

    fake_recover = FakeDiscordClient({}, stale_winner_id="w-old")
    monkeypatch.setattr(winners, "DiscordClient", lambda session: fake_recover)
    winners.main()
    assert len(fake_recover.posts) == 1
    assert fake_recover.posts[0][0] == "daily-picks"


def test_stale_daily_item_message_is_skipped(monkeypatch, tmp_path):
    day_key, path = _setup_daily(tmp_path)
    payloads = {"m2": {"reactions": [{"emoji": {"name": "👍"}, "count": 2}]}}

    class ItemStaleClient(FakeDiscordClient):
        def get_message(self, channel_id, message_id, *, context):
            if message_id == "m1":
                raise winners.DiscordMessageNotFoundError("missing item")
            return super().get_message(channel_id, message_id, context=context)

    reaction_users = {
        "m2": [
            {"id": "bot-1", "username": "bot"},
            {"id": "u9", "global_name": "Thomas", "username": "thomas"},
        ]
    }
    fake = ItemStaleClient(payloads, reaction_users)
    monkeypatch.setattr(winners, "DISCORD_DAILY_POSTS_FILE", str(path))
    monkeypatch.setattr(winners.requests, "Session", FakeSession)
    monkeypatch.setattr(winners, "DiscordClient", lambda session: fake)
    monkeypatch.setattr(winners, "DISCORD_BOT_TOKEN", "x")
    monkeypatch.setattr(winners, "DISCORD_DAILY_PICKS_CHANNEL_ID", "daily-picks")
    monkeypatch.setattr(winners, "DISCORD_WINNERS_CHANNEL_ID", "wchan")
    monkeypatch.setenv(winners.WINNERS_DATE_OVERRIDE_ENV, day_key)

    winners.main()

    assert len(fake.posts) == 1
    assert "Two" in fake.posts[0][1]
    assert "Voters — Thomas" in fake.posts[0][1]


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
