"""Shared day label helpers for weekly scheduling scripts."""

from datetime import date

DAY_MESSAGE_TEMPLATES: list[tuple[str, str, int]] = [
    ("Monday", "🇲", 0),
    ("Tuesday", "🇹", 1),
    ("Wednesday", "🇼", 2),
    ("Thursday", "🇷", 3),
    ("Friday", "🇫", 4),
    ("Saturday", "🇸", 5),
    ("Sunday", "🇺", 6),
]

DAY_NAMES: list[str] = [day_name for day_name, _, _ in DAY_MESSAGE_TEMPLATES]
DAY_EMOJIS: dict[str, str] = {day_name: emoji for day_name, emoji, _ in DAY_MESSAGE_TEMPLATES}


def format_day_label(day_name: str, day_date: date, *, include_emoji: bool = False) -> str:
    """Format a scheduling day label with a compact no-leading-zero date."""
    base_label = f"{day_name} {day_date.month}/{day_date.day}"
    if not include_emoji:
        return base_label

    emoji = DAY_EMOJIS.get(day_name)
    if not emoji:
        return base_label

    return f"{emoji} {day_name} — {day_date.month}/{day_date.day}"
