from scripts import voice_join_alert_bot as voice_bot


def test_load_active_roster_user_ids_filters_only_active(tmp_path):
    roster_path = tmp_path / "expected_schedule_roster.json"
    roster_path.write_text(
        '{"users": {'
        '"111": {"is_active": true}, '
        '"222": {"is_active": false}, '
        '"333": {"is_active": true}'
        '}}',
        encoding="utf-8",
    )

    active = voice_bot.load_active_roster_user_ids(str(roster_path))

    assert active == {"111", "333"}


def test_build_ping_user_ids_excludes_blocked_and_joiner():
    active = {"162382481369071617", "161248274970443776", "100", "200"}

    ping_ids = voice_bot.build_ping_user_ids(active, joiner_id="100")

    assert ping_ids == ["200"]


def test_format_alert_message_uses_direct_discord_mentions():
    message = voice_bot.format_alert_message("999", ["123", "456"])

    assert message == (
        "📣 <@999> just joined, bitches.\n"
        "Heads up – don’t leave them hanging! <@123> <@456>"
    )


def test_format_alert_message_handles_empty_ping_audience():
    message = voice_bot.format_alert_message("999", [])

    assert message == "📣 <@999> just joined, bitches.\nHeads up – don’t leave them hanging!"


def test_cooldown_store_should_alert_per_joiner_threshold():
    store = voice_bot.CooldownStore(path="unused.json", cooldown_seconds=300)
    last_ping_by_user = {"123": 1_000.0, "999": 1_200.0}

    assert store.should_alert("123", now_epoch=1_299.0, last_ping_by_user=last_ping_by_user) is False
    assert store.should_alert("123", now_epoch=1_300.0, last_ping_by_user=last_ping_by_user) is True
    assert store.should_alert("456", now_epoch=1_050.0, last_ping_by_user=last_ping_by_user) is True
