from daily_section_config import (
    DAILY_SECTION_CONFIG,
    DAILY_SECTION_DISPLAY_LABELS,
    DAILY_SECTION_ORDER,
)


def test_daily_section_config_canonical_order_and_labels():
    assert DAILY_SECTION_ORDER == ["demo_playtest", "free", "paid", "instagram"]

    keys_from_config = [entry["key"] for entry in DAILY_SECTION_CONFIG]
    assert keys_from_config == DAILY_SECTION_ORDER

    assert set(DAILY_SECTION_DISPLAY_LABELS.keys()) == set(DAILY_SECTION_ORDER)
    assert all(DAILY_SECTION_DISPLAY_LABELS[section].strip() for section in DAILY_SECTION_ORDER)
