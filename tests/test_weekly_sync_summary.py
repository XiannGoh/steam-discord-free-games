import json

from scripts import sync_weekly_schedule_responses as sync


class FakeSession:
    def __init__(self):
        self.headers = {}

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False


class FakeDiscordClient:
    def __init__(self, *, stale_summary_id=None):
        self.posts = []
        self.edits = []
        self.stale_summary_id = stale_summary_id

    def post_message(self, channel_id, content, *, context):
        mid = f"summary-{len(self.posts)+1}"
        self.posts.append((channel_id, content, context, mid))
        return {"id": mid}

    def edit_message(self, channel_id, message_id, content, *, context):
        if self.stale_summary_id and message_id == self.stale_summary_id:
            raise sync.DiscordMessageNotFoundError("missing")
        self.edits.append((channel_id, message_id, content, context))
        return {"id": message_id}


def _write(path, data):
    path.write_text(json.dumps(data), encoding="utf-8")


def _setup_files(tmp_path, weekly_responses):
    week_key = "2026-04-13_to_2026-04-19"
    messages = {
        week_key: {
            "channel_id": "chan-1",
            "date_range": "Apr 13–19, 2026",
            "days": {d: f"id-{d}" for d in sync.DAY_NAMES},
        }
    }
    roster = {"users": {"100": {"is_active": True}, "200": {"is_active": True}}}

    messages_p = tmp_path / "messages.json"
    responses_p = tmp_path / "responses.json"
    summary_p = tmp_path / "summary.json"
    roster_p = tmp_path / "roster.json"
    outputs_p = tmp_path / "outputs.json"

    _write(messages_p, messages)
    _write(responses_p, weekly_responses)
    _write(summary_p, {})
    _write(roster_p, roster)
    _write(outputs_p, {})

    return week_key, messages_p, responses_p, summary_p, roster_p, outputs_p


def test_summary_format_includes_dates_voter_names_and_truncation():
    voters = [{"display_name": f"User{i}"} for i in range(1, 30)]
    week_summary = {
        "week_key": "2026-04-13_to_2026-04-19",
        "date_range": "Apr 13–19, 2026",
        "summary": {
            "day_counts": {d: (2 if d == "Monday" else 0) for d in sync.DAY_NAMES},
            "slot_counts": {
                d: {slot: (len(voters) if d == "Monday" and slot == "✅" else 0) for slot in sync.SUMMARY_DISPLAY_ORDER}
                for d in sync.DAY_NAMES
            },
            "slot_voters": {
                d: {slot: (voters + [{"display_name": "User1"}] if d == "Monday" and slot == "✅" else []) for slot in sync.SUMMARY_SLOT_ORDER}
                for d in sync.DAY_NAMES
            },
            "best_overlap": {"day": "Monday", "slot": "✅", "count": len(voters)},
        },
    }

    msg = sync.format_summary_message("Apr 13–19, 2026", week_summary)

    assert "Monday 4/13" in msg
    assert "Best overlap:" in msg
    assert "✅" in msg
    assert "+" in msg and "more" in msg
    assert "User1, User1" not in msg


def test_main_rebuild_only_edits_or_recovers_summary(monkeypatch, tmp_path, load_fixture_json):
    weekly_responses = load_fixture_json("weekly_responses_named_users.json")
    week_key, messages_p, responses_p, summary_p, roster_p, outputs_p = _setup_files(tmp_path, weekly_responses)

    existing_output = {
        week_key: {
            "summary_message_id": "sum-old",
            "summary_message_content": "old-content",
        },
        "2026-04-06_to_2026-04-12": {"summary_message_id": "preserve-me"},
    }
    _write(outputs_p, existing_output)

    fake_client = FakeDiscordClient(stale_summary_id="sum-old")
    monkeypatch.setattr(sync.requests, "Session", FakeSession)
    monkeypatch.setattr(sync, "DiscordClient", lambda session: fake_client)
    monkeypatch.setattr(sync, "post_channel_message", lambda session, channel_id, content: "rem-1")
    monkeypatch.setattr(sync, "WEEKLY_SCHEDULE_MESSAGES_FILE", str(messages_p))
    monkeypatch.setattr(sync, "WEEKLY_SCHEDULE_RESPONSES_FILE", str(responses_p))
    monkeypatch.setattr(sync, "WEEKLY_SCHEDULE_SUMMARY_FILE", str(summary_p))
    monkeypatch.setattr(sync, "EXPECTED_SCHEDULE_ROSTER_FILE", str(roster_p))
    monkeypatch.setattr(sync, "WEEKLY_SCHEDULE_BOT_OUTPUTS_FILE", str(outputs_p))

    monkeypatch.setenv("DISCORD_SCHEDULING_BOT_TOKEN", "x")
    monkeypatch.setenv("REBUILD_SUMMARY_ONLY", "true")
    monkeypatch.setenv("TARGET_WEEK_KEY", week_key)
    monkeypatch.delenv("DRY_RUN", raising=False)

    sync.main()

    outputs = json.loads(outputs_p.read_text(encoding="utf-8"))
    assert fake_client.posts, "stale summary should trigger replacement post"
    assert outputs[week_key]["summary_message_id"].startswith("summary-")
    assert outputs["2026-04-06_to_2026-04-12"]["summary_message_id"] == "preserve-me"


def test_main_dry_run_does_not_mutate_summary_message(monkeypatch, tmp_path, load_fixture_json):
    weekly_responses = load_fixture_json("weekly_responses_named_users.json")
    week_key, messages_p, responses_p, summary_p, roster_p, outputs_p = _setup_files(tmp_path, weekly_responses)

    _write(outputs_p, {week_key: {"summary_message_id": "sum-old", "summary_message_content": "unchanged"}})

    fake_client = FakeDiscordClient()
    monkeypatch.setattr(sync.requests, "Session", FakeSession)
    monkeypatch.setattr(sync, "DiscordClient", lambda session: fake_client)
    monkeypatch.setattr(sync, "WEEKLY_SCHEDULE_MESSAGES_FILE", str(messages_p))
    monkeypatch.setattr(sync, "WEEKLY_SCHEDULE_RESPONSES_FILE", str(responses_p))
    monkeypatch.setattr(sync, "WEEKLY_SCHEDULE_SUMMARY_FILE", str(summary_p))
    monkeypatch.setattr(sync, "EXPECTED_SCHEDULE_ROSTER_FILE", str(roster_p))
    monkeypatch.setattr(sync, "WEEKLY_SCHEDULE_BOT_OUTPUTS_FILE", str(outputs_p))

    monkeypatch.setenv("DISCORD_SCHEDULING_BOT_TOKEN", "x")
    monkeypatch.setenv("REBUILD_SUMMARY_ONLY", "true")
    monkeypatch.setenv("TARGET_WEEK_KEY", week_key)
    monkeypatch.setenv("DRY_RUN", "true")

    sync.main()

    assert fake_client.posts == []
    assert fake_client.edits == []
