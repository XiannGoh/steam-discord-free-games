import json

from scripts import post_weekly_availability as weekly
from scripts import scheduling_labels


class FakeSession:
    def __init__(self):
        self.headers = {}

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False


class FakeClient:
    def __init__(self, existing_message_ids=None, stale_ids=None, pinned_messages=None):
        self.existing = set(existing_message_ids or [])
        self.stale = set(stale_ids or [])
        self.created_messages = []
        self.reaction_calls = []
        self.pin_calls = []
        self.unpin_calls = []
        self._pinned_messages = pinned_messages or []

    def get_message(self, channel_id, message_id, *, context):
        if message_id in self.stale:
            raise weekly.DiscordMessageNotFoundError("gone")
        if message_id in self.existing:
            return {"id": message_id}
        raise RuntimeError("not verifiable")

    def post_message(self, channel_id, content, *, context):
        new_id = f"new-{len(self.created_messages) + 1}"
        self.created_messages.append((context, content, new_id))
        return {"id": new_id}

    def put_reaction(self, channel_id, message_id, encoded_emoji, *, context):
        self.reaction_calls.append((message_id, encoded_emoji))

    def pin_message(self, channel_id, message_id, *, context):
        self.pin_calls.append(message_id)

    def unpin_message(self, channel_id, message_id, *, context):
        self.unpin_calls.append(message_id)

    def get_pinned_messages(self, channel_id, *, context):
        return list(self._pinned_messages)


def _run_main(monkeypatch, tmp_path, *, existing_state, fake_client):
    path = tmp_path / "weekly_messages.json"
    path.write_text(json.dumps(existing_state), encoding="utf-8")

    monkeypatch.setattr(weekly, "WEEKLY_SCHEDULE_MESSAGES_FILE", str(path))
    monkeypatch.setattr(weekly.requests, "Session", FakeSession)
    monkeypatch.setattr(weekly, "DiscordClient", lambda session: fake_client)
    monkeypatch.setenv("DISCORD_SCHEDULING_BOT_TOKEN", "x")
    monkeypatch.setenv("DISCORD_SCHEDULING_CHANNEL_ID", "chan-1")
    monkeypatch.setenv("SCHEDULE_WEEK_START", "2026-04-13")

    weekly.main()
    return json.loads(path.read_text(encoding="utf-8"))


def test_rerun_reuses_intro_and_days_without_duplicates(monkeypatch, tmp_path):
    week_key = "2026-04-13_to_2026-04-19"
    existing = {
        week_key: {
            "channel_id": "chan-1",
            "date_range": "Apr 13–19, 2026",
            "created_at_utc": "2026-04-01T00:00:00Z",
            "intro_message_id": "intro-1",
            "days": {day: f"id-{day}" for day, _, _ in weekly.DAY_MESSAGE_TEMPLATES},
        }
    }
    fake = FakeClient(existing_message_ids={"intro-1", *[f"id-{day}" for day, _, _ in weekly.DAY_MESSAGE_TEMPLATES]})

    saved = _run_main(monkeypatch, tmp_path, existing_state=existing, fake_client=fake)

    assert fake.created_messages == []
    assert saved[week_key]["intro_message_id"] == "intro-1"
    assert saved[week_key]["post_completed"] is True


def test_partial_recovery_only_creates_missing_or_stale_posts(monkeypatch, tmp_path):
    week_key = "2026-04-13_to_2026-04-19"
    existing = {
        week_key: {
            "channel_id": "chan-1",
            "date_range": "Apr 13–19, 2026",
            "intro_message_id": "intro-1",
            "days": {day: f"id-{day}" for day, _, _ in weekly.DAY_MESSAGE_TEMPLATES},
        }
    }
    fake = FakeClient(
        existing_message_ids={"id-Monday", "id-Tuesday", "id-Thursday", "id-Friday", "id-Saturday", "id-Sunday"},
        stale_ids={"intro-1", "id-Wednesday"},
    )

    saved = _run_main(monkeypatch, tmp_path, existing_state=existing, fake_client=fake)

    assert len(fake.created_messages) == 2  # intro + Wednesday only
    assert saved[week_key]["intro_message_id"].startswith("new-")
    assert saved[week_key]["days"]["Wednesday"].startswith("new-")
    assert saved[week_key]["days"]["Monday"] == "id-Monday"
    assert len(fake.reaction_calls) == len(weekly.AVAILABILITY_REACTIONS)


def test_day_message_includes_week_dates_and_compact_format(monkeypatch, tmp_path):
    fake = FakeClient()

    _run_main(monkeypatch, tmp_path, existing_state={}, fake_client=fake)

    day_contents = [
        content
        for context, content, _ in fake.created_messages
        if context.startswith("post ") and "intro" not in context
    ]
    assert day_contents == [
        "🇲 Monday — 4/13",
        "🇹 Tuesday — 4/14",
        "🇼 Wednesday — 4/15",
        "🇷 Thursday — 4/16",
        "🇫 Friday — 4/17",
        "🇸 Saturday — 4/18",
        "🇺 Sunday — 4/19",
    ]


def test_format_day_message_uses_no_leading_zeros():
    assert (
        weekly.format_day_message("Monday", "🇲", weekly.date(2026, 4, 3))
        == scheduling_labels.format_day_label("Monday", weekly.date(2026, 4, 3), include_emoji=True)
        == "🇲 Monday — 4/3"
    )


def test_pin_message_called_after_weekly_post(monkeypatch, tmp_path):
    """A freshly posted intro message should be pinned."""
    fake = FakeClient()

    _run_main(monkeypatch, tmp_path, existing_state={}, fake_client=fake)

    assert len(fake.pin_calls) == 1
    assert fake.pin_calls[0].startswith("new-")


def test_previous_week_unpinned_when_new_week_posted(monkeypatch, tmp_path):
    """When a new week is posted, any previously pinned weekly availability message is unpinned."""
    old_pinned_id = "old-intro-99"
    pinned = [{"id": old_pinned_id, "content": "🗓️ Weekly Availability — react below for next week\nWeek of Apr 6–12, 2026"}]
    fake = FakeClient(pinned_messages=pinned)

    _run_main(monkeypatch, tmp_path, existing_state={}, fake_client=fake)

    assert old_pinned_id in fake.unpin_calls
    assert len(fake.pin_calls) == 1  # new intro pinned


def test_already_pinned_intro_not_pinned_again(monkeypatch, tmp_path):
    """If the current week's intro is already pinned, pin_message is not called again."""
    week_key = "2026-04-13_to_2026-04-19"
    intro_id = "intro-1"
    pinned = [{"id": intro_id, "content": "🗓️ Weekly Availability — react below for next week\nWeek of Apr 13–19, 2026"}]
    existing = {
        week_key: {
            "channel_id": "chan-1",
            "date_range": "Apr 13–19, 2026",
            "created_at_utc": "2026-04-13T00:00:00Z",
            "intro_message_id": intro_id,
            "days": {day: f"id-{day}" for day, _, _ in weekly.DAY_MESSAGE_TEMPLATES},
        }
    }
    fake = FakeClient(
        existing_message_ids={intro_id, *[f"id-{day}" for day, _, _ in weekly.DAY_MESSAGE_TEMPLATES]},
        pinned_messages=pinned,
    )

    _run_main(monkeypatch, tmp_path, existing_state=existing, fake_client=fake)

    assert fake.pin_calls == []
    assert fake.unpin_calls == []


def test_legacy_state_shape_loads_and_upgrades(monkeypatch, tmp_path, load_fixture_json):
    existing = load_fixture_json("weekly_messages_legacy.json")
    fake = FakeClient(existing_message_ids={"intro-old", "m-1", "t-1", "w-1", "r-1", "f-1", "s-1", "u-1"})

    saved = _run_main(monkeypatch, tmp_path, existing_state=existing, fake_client=fake)
    week_key = "2026-04-13_to_2026-04-19"

    assert saved[week_key]["channel_id"] == "chan-1"
    assert "updated_at_utc" in saved[week_key]
    assert saved[week_key]["post_completed"] is True
