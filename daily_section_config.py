"""Single shared config for section order, labels, and routing ownership.

Keeping this centralized ensures daily picks and evening winners stay aligned.
"""

from typing import Dict, List

# Centralized source of truth: keep these labels/order in sync for both pipelines.
DAILY_SECTION_CONFIG: List[dict] = [
    {
        "key": "demo_playtest",
        "header": "🧪 New Demos & Playtests",
        "source_type": "steam_demo_playtest",
        "message_label": "Demo/Playtest",
        "display_label": "New Demos & Playtests",
    },
    {
        "key": "free",
        "header": "🎮 Free Picks",
        "source_type": "steam_free",
        "message_label": "Free",
        "display_label": "Free Picks",
    },
    {
        "key": "paid",
        "header": "💸 Paid Under $20",
        "source_type": "paid_under_20",
        "message_label": "Paid",
        "display_label": "Paid Under $20",
    },
    {
        "key": "instagram",
        "header": "📸 Instagram Creator Picks",
        "source_type": "instagram",
        "message_label": "Instagram",
        "display_label": "Instagram Creator Picks",
    },
]

DAILY_SECTION_ORDER: List[str] = [entry["key"] for entry in DAILY_SECTION_CONFIG]
DAILY_SECTION_DISPLAY_LABELS: Dict[str, str] = {
    entry["key"]: entry["display_label"] for entry in DAILY_SECTION_CONFIG
}
