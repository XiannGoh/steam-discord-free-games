import json

from scripts import check_state_sanity as sanity


def _write_json(path, payload):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload), encoding="utf-8")


def test_state_sanity_passes_for_minimal_valid_state(tmp_path, monkeypatch):
    root = tmp_path
    monkeypatch.setattr(sanity, "ROOT", root)

    _write_json(root / "data/scheduling/weekly_schedule_messages.json", {"2026-04-13_to_2026-04-19": {"ok": True}})
    _write_json(root / "data/scheduling/weekly_schedule_responses.json", {"2026-04-13_to_2026-04-19": {"ok": True}})
    _write_json(root / "data/scheduling/weekly_schedule_summary.json", {"2026-04-13_to_2026-04-19": {"ok": True}})
    _write_json(root / "data/scheduling/weekly_schedule_bot_outputs.json", {"2026-04-13_to_2026-04-19": {"ok": True}})
    _write_json(root / "data/scheduling/expected_schedule_roster.json", {"users": {"1": {"is_active": True}}})
    _write_json(root / "discord_daily_posts.json", {"2026-04-08": {"items": []}})

    assert sanity.run_checks() == 0


def test_state_sanity_fails_for_missing_required_or_wrong_shape(tmp_path, monkeypatch):
    root = tmp_path
    monkeypatch.setattr(sanity, "ROOT", root)

    _write_json(root / "data/scheduling/weekly_schedule_messages.json", [])
    _write_json(root / "data/scheduling/weekly_schedule_responses.json", {"bad": []})
    _write_json(root / "data/scheduling/weekly_schedule_summary.json", {})
    _write_json(root / "data/scheduling/weekly_schedule_bot_outputs.json", {})
    _write_json(root / "data/scheduling/expected_schedule_roster.json", {"users": []})

    assert sanity.run_checks() == 1
