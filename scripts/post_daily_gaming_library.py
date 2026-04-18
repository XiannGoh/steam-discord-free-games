import os

import requests

from discord_api import DiscordClient
from gaming_library import run_daily_post
from rolling_explainer import post_or_edit_rolling_explainer

if __name__ == "__main__":
    posted = run_daily_post()
    print(f"daily gaming library posted={posted}")

    channel_id = os.getenv("DISCORD_GAMING_LIBRARY_CHANNEL_ID", "").strip()
    token = os.getenv("DISCORD_BOT_TOKEN", "").strip()
    if channel_id and token:
        with requests.Session() as _expl_session:
            _expl_session.headers.update({
                "Authorization": f"Bot {token}",
                "Content-Type": "application/json",
            })
            post_or_edit_rolling_explainer(DiscordClient(_expl_session), channel_id, "step-3")
