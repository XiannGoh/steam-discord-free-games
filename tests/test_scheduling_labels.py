from datetime import date

from scripts import post_weekly_availability as weekly
from scripts import scheduling_labels


def test_format_day_label_no_leading_zeros():
    assert scheduling_labels.format_day_label("Monday", date(2026, 4, 3)) == "Monday 4/3"
    assert scheduling_labels.format_day_label("Monday", date(2026, 4, 3), include_emoji=True) == "🇲 Monday — 4/3"


def test_post_script_uses_shared_day_label_helper():
    assert (
        weekly.format_day_message("Tuesday", "🇹", date(2026, 4, 14))
        == scheduling_labels.format_day_label("Tuesday", date(2026, 4, 14), include_emoji=True)
    )
