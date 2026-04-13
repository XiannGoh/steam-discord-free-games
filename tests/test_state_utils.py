import json

from state_utils import format_et_timestamp, load_json_object, prune_latest_iso_dates, prune_latest_keys, save_json_object_atomic


def test_load_json_object_returns_default_for_invalid_json(tmp_path):
    path = tmp_path / "bad.json"
    path.write_text("{oops", encoding="utf-8")

    assert load_json_object(str(path), default={"ok": 1}) == {"ok": 1}


def test_load_json_object_returns_default_for_non_dict(tmp_path):
    path = tmp_path / "arr.json"
    path.write_text("[1,2,3]", encoding="utf-8")

    assert load_json_object(str(path), default={"ok": True}) == {"ok": True}


def test_save_json_object_atomic_writes_expected_json(tmp_path):
    path = tmp_path / "state" / "x.json"
    save_json_object_atomic(str(path), {"b": 2, "a": 1})

    saved = json.loads(path.read_text(encoding="utf-8"))
    assert saved == {"b": 2, "a": 1}


def test_prune_latest_keys_keeps_newest_keys():
    data = {f"week-{i:02d}": i for i in range(1, 16)}
    pruned = prune_latest_keys(data, keep_last=12)

    assert len(pruned) == 12
    assert "week-01" not in pruned
    assert "week-15" in pruned


def test_format_et_timestamp_basic_utc_noon():
    # 2024-12-15T17:00:00Z is noon ET (UTC-5 in December)
    result = format_et_timestamp("2024-12-15T17:00:00Z")
    assert result == "Dec 15, 2024 at 12:00 PM ET"


def test_format_et_timestamp_z_suffix_handled():
    result = format_et_timestamp("2026-04-10T13:10:00Z")
    # UTC-4 in April (EDT), so 13:10 UTC = 9:10 AM ET
    assert result == "Apr 10, 2026 at 9:10 AM ET"


def test_format_et_timestamp_returns_none_for_empty_string():
    assert format_et_timestamp("") is None


def test_format_et_timestamp_returns_none_for_non_string():
    assert format_et_timestamp(None) is None
    assert format_et_timestamp(12345) is None


def test_format_et_timestamp_returns_none_for_invalid_iso():
    assert format_et_timestamp("not-a-date") is None


def test_format_et_timestamp_midnight_utc():
    # 2026-01-01T00:00:00Z is Dec 31, 2025 at 7:00 PM ET (UTC-5 in January)
    result = format_et_timestamp("2026-01-01T00:00:00Z")
    assert result == "Dec 31, 2025 at 7:00 PM ET"


def test_prune_latest_iso_dates_handles_mixed_keys_safely():
    data = {
        "2026-01-01": 1,
        "not-a-date": 2,
        "2026-03-01": 3,
        "2026-02-01": 4,
    }
    pruned = prune_latest_iso_dates(data, keep_last=2, log=lambda _: None)

    assert set(pruned.keys()) == {"2026-02-01", "2026-03-01"}
