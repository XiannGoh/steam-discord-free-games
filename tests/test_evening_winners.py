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
    def __init__(self, message_payloads, *, stale_winner_id=None, unchanged_hash=None):
        self.message_payloads = message_payloads
        self.stale_winner_id = stale_winner_id
        self.unchanged_hash = unchanged_hash
        self.posts = []
        self.edits = []

    def get_message(self, channel_id, message_id, *, context):
        if self.stale_winner_id and message_id == self.stale_winner_id:
            raise winners.DiscordMessageNotFoundError("missing")
        if message_id in self.message_payloads:
            return self.message_payloads[message_id]
        return {"id": message_id}

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
    fake = FakeDiscordClient(payloads)

    monkeypatch.setattr(winners, "DISCORD_DAILY_POSTS_FILE", str(path))
    monkeypatch.setattr(winners.requests, "Session", FakeSession)
    monkeypatch.setattr(winners, "DiscordClient", lambda session: fake)
    monkeypatch.setattr(winners, "DISCORD_BOT_TOKEN", "x")
    monkeypatch.setattr(winners, "DISCORD_WINNERS_CHANNEL_ID", "wchan")
    monkeypatch.setenv(winners.WINNERS_DATE_OVERRIDE_ENV, day_key)

    winners.main()

    assert len(fake.posts) == 1
    content = fake.posts[0][1]
    assert "One" not in content
    assert "Two" in content and "👍 1 vote" in content
    assert "Three" in content and "👍 2 votes" in content


def test_winners_rerun_skip_edit_and_recovery(monkeypatch, tmp_path):
    day_key, path = _setup_daily(tmp_path)
    data = json.loads(path.read_text(encoding="utf-8"))
    same_message = winners.build_winners_message({"free": [], "paid": [], "instagram": []})
    same_hash = winners.hashlib.sha256(same_message.encode("utf-8")).hexdigest()
    data[day_key]["winners_state"] = {"message_id": "w-old", "content_hash": same_hash}
    data[day_key]["items"] = []
    path.write_text(json.dumps(data), encoding="utf-8")

    fake_skip = FakeDiscordClient({}, unchanged_hash=same_hash)
    monkeypatch.setattr(winners, "DISCORD_DAILY_POSTS_FILE", str(path))
    monkeypatch.setattr(winners.requests, "Session", FakeSession)
    monkeypatch.setattr(winners, "DiscordClient", lambda session: fake_skip)
    monkeypatch.setattr(winners, "DISCORD_BOT_TOKEN", "x")
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

    fake_recover = FakeDiscordClient({}, stale_winner_id="w-old")
    monkeypatch.setattr(winners, "DiscordClient", lambda session: fake_recover)
    winners.main()
    assert len(fake_recover.posts) == 1


def test_stale_daily_item_message_is_skipped(monkeypatch, tmp_path):
    day_key, path = _setup_daily(tmp_path)
    payloads = {"m2": {"reactions": [{"emoji": {"name": "👍"}, "count": 2}]}}

    class ItemStaleClient(FakeDiscordClient):
        def get_message(self, channel_id, message_id, *, context):
            if message_id == "m1":
                raise winners.DiscordMessageNotFoundError("missing item")
            return super().get_message(channel_id, message_id, context=context)

    fake = ItemStaleClient(payloads)
    monkeypatch.setattr(winners, "DISCORD_DAILY_POSTS_FILE", str(path))
    monkeypatch.setattr(winners.requests, "Session", FakeSession)
    monkeypatch.setattr(winners, "DiscordClient", lambda session: fake)
    monkeypatch.setattr(winners, "DISCORD_BOT_TOKEN", "x")
    monkeypatch.setattr(winners, "DISCORD_WINNERS_CHANNEL_ID", "wchan")
    monkeypatch.setenv(winners.WINNERS_DATE_OVERRIDE_ENV, day_key)

    winners.main()

    assert len(fake.posts) == 1
    assert "Two" in fake.posts[0][1]
