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

    msg = sync.format_summary_message(
        "Apr 13–19, 2026",
        week_summary,
        responded_count=1,
        active_user_count=2,
        synced_at_utc=sync.datetime(2026, 4, 9, 0, 0, tzinfo=sync.timezone.utc),
    )

    assert "Monday 4/13" in msg
    assert "Best overlap:" not in msg
    assert "All days ranked:" not in msg
    assert "✅" in msg
    assert "+" in msg and "more" in msg
    assert "User1, User1" not in msg


def test_summary_format_is_monday_to_sunday_chronological():
    week_summary = {
        "week_key": "2026-04-13_to_2026-04-19",
        "date_range": "Apr 13–19, 2026",
        "summary": {
            "day_counts": {d: 0 for d in sync.DAY_NAMES},
            "slot_counts": {
                d: {slot: 0 for slot in sync.SUMMARY_DISPLAY_ORDER}
                for d in sync.DAY_NAMES
            },
            "slot_voters": {
                d: {slot: [] for slot in sync.SUMMARY_SLOT_ORDER}
                for d in sync.DAY_NAMES
            },
            "best_overlap": {"day": "Monday", "slot": "✅", "count": 0},
        },
    }

    msg = sync.format_summary_message(
        "Apr 13–19, 2026",
        week_summary,
        responded_count=1,
        active_user_count=2,
        synced_at_utc=sync.datetime(2026, 4, 9, 0, 0, tzinfo=sync.timezone.utc),
    )

    day_positions = [msg.index(f"**{day} 4/{13 + index}**") for index, day in enumerate(sync.DAY_NAMES)]
    assert day_positions == sorted(day_positions)


def test_shared_date_label_helper_matches_summary_and_day_posts():
    week_summary = {
        "week_key": "2026-04-13_to_2026-04-19",
        "date_range": "Apr 13–19, 2026",
        "summary": {
            "day_counts": {d: 0 for d in sync.DAY_NAMES},
            "slot_counts": {
                d: {slot: 0 for slot in sync.SUMMARY_DISPLAY_ORDER}
                for d in sync.DAY_NAMES
            },
            "slot_voters": {
                d: {slot: [] for slot in sync.SUMMARY_SLOT_ORDER}
                for d in sync.DAY_NAMES
            },
            "best_overlap": {"day": "Monday", "slot": "✅", "count": 0},
        },
    }

    msg = sync.format_summary_message(
        "Apr 13–19, 2026",
        week_summary,
        responded_count=1,
        active_user_count=2,
        synced_at_utc=sync.datetime(2026, 4, 9, 0, 0, tzinfo=sync.timezone.utc),
    )
    assert "**Monday 4/13**" in msg


def test_summary_includes_status_timestamp_spacing_and_slot_order():
    week_summary = {
        "week_key": "2026-04-13_to_2026-04-19",
        "date_range": "Apr 13–19, 2026",
        "summary": {
            "day_counts": {d: 0 for d in sync.DAY_NAMES},
            "slot_counts": {
                "Monday": {"✅": 3, "🌅": 1, "☀️": 2, "🌙": 1, "📝": 1, "❌": 4},
                "Tuesday": {"✅": 0, "🌅": 0, "☀️": 1, "🌙": 0, "📝": 0, "❌": 0},
                **{
                    day: {slot: 0 for slot in sync.SUMMARY_DISPLAY_ORDER}
                    for day in sync.DAY_NAMES
                    if day not in {"Monday", "Tuesday"}
                },
            },
            "slot_voters": {
                "Monday": {
                    "✅": [{"display_name": "Alice"}, {"display_name": "Bob"}, {"display_name": "Charlie"}],
                    "🌅": [{"display_name": "Dawn"}],
                    "☀️": [{"display_name": "Sun1"}, {"display_name": "Sun2"}],
                    "🌙": [{"display_name": "Moon"}],
                    "📝": [{"display_name": "Note"}],
                },
                "Tuesday": {"✅": [], "🌅": [], "☀️": [{"display_name": "Erin"}], "🌙": [], "📝": []},
                **{
                    day: {slot: [] for slot in sync.SUMMARY_SLOT_ORDER}
                    for day in sync.DAY_NAMES
                    if day not in {"Monday", "Tuesday"}
                },
            },
            "best_overlap": {"day": "Monday", "slot": "✅", "count": 3},
        },
    }

    msg = sync.format_summary_message(
        "Apr 13–19, 2026",
        week_summary,
        responded_count=12,
        active_user_count=14,
        synced_at_utc=sync.datetime(2026, 4, 10, 0, 0, tzinfo=sync.timezone.utc),
    )

    assert "*12 of 14 people responded • 2 still missing*" in msg
    assert "*Last updated: Apr 9, 8:00 PM ET*" in msg
    assert "\n\n**Tuesday 4/14**" in msg
    assert "\n\n\n**Tuesday 4/14**" not in msg
    assert "\n**Monday 4/13**\n✅ 3 — Alice, Bob, Charlie\n🌅 1 — Dawn\n☀️ 2 — Sun1, Sun2\n🌙 1 — Moon\n📝 1 — Note\n❌ 4 — (names unavailable)\n\n**Tuesday 4/14**\n☀️ 1 — Erin\n\n**Wednesday 4/15**\nNo responses" in msg


def test_main_rebuild_only_edits_or_recovers_summary(monkeypatch, tmp_path, load_fixture_json):
    weekly_responses = load_fixture_json("weekly_responses_named_users.json")
    week_key, messages_p, responses_p, summary_p, roster_p, outputs_p = _setup_files(tmp_path, weekly_responses)

    existing_output = {
        week_key: {
            "summary_message_id": "sum-old",
            "summary_message_content": "old-content",
            "summary_data_signature": "stale-signature",
            "summary_last_synced_at_utc": "2026-04-01T00:00:00+00:00",
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
    monkeypatch.setattr(sync, "post_channel_message", lambda session, channel_id, content: "rem-1")
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


def test_main_posts_only_one_reminder_per_new_york_local_day(monkeypatch, tmp_path, load_fixture_json):
    week_key, messages_p, responses_p, summary_p, roster_p, outputs_p = _setup_files(
        tmp_path,
        {
            "2026-04-13_to_2026-04-19": {
                "date_range": "Apr 13–19, 2026",
                "users": {},
            }
        },
    )

    reminder_posts = []

    def fake_post_channel_message(session, channel_id, content):
        reminder_posts.append((channel_id, content))
        return f"rem-{len(reminder_posts)}"

    fake_client = FakeDiscordClient()
    monkeypatch.setattr(sync.requests, "Session", FakeSession)
    monkeypatch.setattr(sync, "DiscordClient", lambda session: fake_client)
    monkeypatch.setattr(sync, "post_channel_message", fake_post_channel_message)
    monkeypatch.setattr(sync, "WEEKLY_SCHEDULE_MESSAGES_FILE", str(messages_p))
    monkeypatch.setattr(sync, "WEEKLY_SCHEDULE_RESPONSES_FILE", str(responses_p))
    monkeypatch.setattr(sync, "WEEKLY_SCHEDULE_SUMMARY_FILE", str(summary_p))
    monkeypatch.setattr(sync, "EXPECTED_SCHEDULE_ROSTER_FILE", str(roster_p))
    monkeypatch.setattr(sync, "WEEKLY_SCHEDULE_BOT_OUTPUTS_FILE", str(outputs_p))

    monkeypatch.setenv("DISCORD_SCHEDULING_BOT_TOKEN", "x")
    monkeypatch.setenv("REBUILD_SUMMARY_ONLY", "true")
    monkeypatch.setenv("TARGET_WEEK_KEY", week_key)
    monkeypatch.delenv("DRY_RUN", raising=False)

    monkeypatch.setattr(sync, "current_new_york_local_date", lambda: "2026-04-18")
    sync.main()
    sync.main()

    outputs_after_same_day = json.loads(outputs_p.read_text(encoding="utf-8"))
    assert len(reminder_posts) == 1
    assert outputs_after_same_day[week_key]["last_reminder_local_date"] == "2026-04-18"

    # New day + changed missing list should allow a new reminder.
    _write(
        responses_p,
        {
            week_key: {
                "date_range": "Apr 13–19, 2026",
                "users": {
                    "100": {
                        "username": "jan",
                        "global_name": "Jan",
                        "days": {
                            day: {"reactions": ["✅"], "custom_reply": None}
                            for day in sync.DAY_NAMES
                        },
                    }
                },
            }
        },
    )

    monkeypatch.setattr(sync, "current_new_york_local_date", lambda: "2026-04-19")
    sync.main()

    outputs_after_next_day = json.loads(outputs_p.read_text(encoding="utf-8"))
    assert len(reminder_posts) == 2
    assert outputs_after_next_day[week_key]["last_reminder_local_date"] == "2026-04-19"


def test_main_saturday_new_week_requires_change_and_allows_single_daily_reminder(
    monkeypatch, tmp_path
):
    week_key, messages_p, responses_p, summary_p, roster_p, outputs_p = _setup_files(
        tmp_path,
        {
            "2026-04-13_to_2026-04-19": {
                "date_range": "Apr 13–19, 2026",
                "users": {},
            }
        },
    )
    reminder_posts = []

    def fake_post_channel_message(session, channel_id, content):
        reminder_posts.append((channel_id, content))
        return f"rem-{len(reminder_posts)}"

    fake_client = FakeDiscordClient()
    monkeypatch.setattr(sync.requests, "Session", FakeSession)
    monkeypatch.setattr(sync, "DiscordClient", lambda session: fake_client)
    monkeypatch.setattr(sync, "post_channel_message", fake_post_channel_message)
    monkeypatch.setattr(sync, "WEEKLY_SCHEDULE_MESSAGES_FILE", str(messages_p))
    monkeypatch.setattr(sync, "WEEKLY_SCHEDULE_RESPONSES_FILE", str(responses_p))
    monkeypatch.setattr(sync, "WEEKLY_SCHEDULE_SUMMARY_FILE", str(summary_p))
    monkeypatch.setattr(sync, "EXPECTED_SCHEDULE_ROSTER_FILE", str(roster_p))
    monkeypatch.setattr(sync, "WEEKLY_SCHEDULE_BOT_OUTPUTS_FILE", str(outputs_p))
    monkeypatch.setattr(sync, "current_new_york_local_date", lambda: "2026-04-18")

    monkeypatch.setenv("DISCORD_SCHEDULING_BOT_TOKEN", "x")
    monkeypatch.setenv("REBUILD_SUMMARY_ONLY", "true")
    monkeypatch.setenv("TARGET_WEEK_KEY", week_key)
    monkeypatch.delenv("DRY_RUN", raising=False)

    # First Saturday sync for newly created week posts summary + first reminder.
    sync.main()
    assert len(fake_client.posts) == 1
    assert len(reminder_posts) == 1

    # Later same-day Saturday syncs cannot post another reminder.
    sync.main()
    assert len(reminder_posts) == 1


def test_main_unchanged_summary_signature_skips_edit_and_keeps_timestamp(
    monkeypatch, tmp_path
):
    week_key, messages_p, responses_p, summary_p, roster_p, outputs_p = _setup_files(
        tmp_path,
        {
            "2026-04-13_to_2026-04-19": {
                "date_range": "Apr 13–19, 2026",
                "users": {},
            }
        },
    )

    weekly_responses = json.loads(responses_p.read_text(encoding="utf-8"))
    roster = json.loads(roster_p.read_text(encoding="utf-8"))
    weekly_summary = sync.build_weekly_summary(weekly_responses)
    posting_week_summary = weekly_summary[week_key]
    missing_user_ids = sync.compute_missing_user_ids_for_week(weekly_responses[week_key], roster)
    active_user_count = sync.count_active_roster_users(roster)
    responded_count = max(active_user_count - len(missing_user_ids), 0)
    old_synced_at = sync.datetime(2026, 4, 1, 0, 0, tzinfo=sync.timezone.utc)
    prior_summary_message = sync.format_summary_message(
        "Apr 13–19, 2026",
        posting_week_summary,
        responded_count=responded_count,
        active_user_count=active_user_count,
        synced_at_utc=old_synced_at,
    )
    prior_signature = sync.compute_summary_data_signature(
        posting_week_summary, responded_count, active_user_count, missing_user_ids
    )
    _write(
        outputs_p,
        {
            week_key: {
                "summary_message_id": "sum-old",
                "summary_message_content": prior_summary_message,
                "summary_data_signature": prior_signature,
                "summary_last_synced_at_utc": old_synced_at.isoformat(),
            }
        },
    )

    fake_client = FakeDiscordClient()
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
    assert fake_client.posts == []
    assert fake_client.edits == []
    assert outputs[week_key]["summary_last_synced_at_utc"] == old_synced_at.isoformat()
    assert outputs[week_key]["summary_message_content"] == prior_summary_message


def test_main_changed_summary_data_updates_timestamp_and_edits_message(monkeypatch, tmp_path):
    week_key, messages_p, responses_p, summary_p, roster_p, outputs_p = _setup_files(
        tmp_path,
        {
            "2026-04-13_to_2026-04-19": {
                "date_range": "Apr 13–19, 2026",
                "users": {},
            }
        },
    )
    baseline_weekly_responses = json.loads(responses_p.read_text(encoding="utf-8"))
    roster = json.loads(roster_p.read_text(encoding="utf-8"))
    baseline_summary = sync.build_weekly_summary(baseline_weekly_responses)[week_key]
    baseline_missing = sync.compute_missing_user_ids_for_week(
        baseline_weekly_responses[week_key], roster
    )
    active_user_count = sync.count_active_roster_users(roster)
    baseline_responded = max(active_user_count - len(baseline_missing), 0)
    old_synced_at = sync.datetime(2026, 4, 1, 0, 0, tzinfo=sync.timezone.utc)
    baseline_content = sync.format_summary_message(
        "Apr 13–19, 2026",
        baseline_summary,
        responded_count=baseline_responded,
        active_user_count=active_user_count,
        synced_at_utc=old_synced_at,
    )
    baseline_signature = sync.compute_summary_data_signature(
        baseline_summary, baseline_responded, active_user_count, baseline_missing
    )
    _write(
        outputs_p,
        {
            week_key: {
                "summary_message_id": "sum-old",
                "summary_message_content": baseline_content,
                "summary_data_signature": baseline_signature,
                "summary_last_synced_at_utc": old_synced_at.isoformat(),
            }
        },
    )

    _write(
        responses_p,
        {
            week_key: {
                "date_range": "Apr 13–19, 2026",
                "users": {
                    "100": {
                        "username": "jan",
                        "global_name": "Jan",
                        "days": {
                            day: {"reactions": ["✅"], "custom_reply": None}
                            for day in sync.DAY_NAMES
                        },
                    }
                },
            }
        },
    )

    fake_client = FakeDiscordClient()
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
    assert fake_client.posts == []
    assert len(fake_client.edits) == 1
    assert outputs[week_key]["summary_message_content"] != baseline_content
    assert outputs[week_key]["summary_data_signature"] != baseline_signature
    assert outputs[week_key]["summary_last_synced_at_utc"] != old_synced_at.isoformat()


def test_main_legacy_summary_backfills_signature_without_noisy_edit(monkeypatch, tmp_path):
    week_key, messages_p, responses_p, summary_p, roster_p, outputs_p = _setup_files(
        tmp_path,
        {
            "2026-04-13_to_2026-04-19": {
                "date_range": "Apr 13–19, 2026",
                "users": {},
            }
        },
    )
    _write(
        outputs_p,
        {
            week_key: {
                "summary_message_id": "sum-old",
                "summary_message_content": "legacy-summary-content",
            }
        },
    )

    fake_client = FakeDiscordClient()
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
    assert fake_client.posts == []
    assert fake_client.edits == []
    assert outputs[week_key]["summary_message_content"] == "legacy-summary-content"
    assert isinstance(outputs[week_key].get("summary_data_signature"), str)
    assert "summary_last_synced_at_utc" not in outputs[week_key]
