import os

import requests

from discord_api import DiscordClient
from gaming_library import run_daily_post
from rolling_explainer import post_or_edit_rolling_explainer

if __name__ == "__main__":
    posted = run_daily_post()
    print(f"daily gaming library posted={posted}")

    # PR #324 follow-up: if run_daily_post() returned False, the day was already
    # complete (or suppression fired) and we MUST NOT touch the rolling
    # explainer either — it's an existing message older than 1 hour, and Discord
    # rate-limits edits to old messages (HTTP 429 code 30046). The script would
    # exit 1 from rolling_explainer's crash even though run_daily_post() succeeded
    # cleanly. continue-on-error: true in the workflow swallows the failure but
    # the annotation "Process completed with exit code 1" still appears on the
    # workflow run page, which is noise.
    channel_id = os.getenv("DISCORD_GAMING_LIBRARY_CHANNEL_ID", "").strip()
    token = os.getenv("DISCORD_BOT_TOKEN", "").strip()
    if posted and channel_id and token:
        with requests.Session() as _expl_session:
            _expl_session.headers.update({
                "Authorization": f"Bot {token}",
                "Content-Type": "application/json",
            })
            post_or_edit_rolling_explainer(DiscordClient(_expl_session), channel_id, "step-3")
