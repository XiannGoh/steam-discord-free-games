"""Check Discord bot token health by calling GET /users/@me for each token.

Exits with code 0 even if tokens are invalid — warnings are posted to the
health monitor webhook so the health report workflow continues regardless.
"""

from __future__ import annotations

import os
import sys

import requests

DISCORD_HEALTH_MONITOR_WEBHOOK_URL = os.getenv("DISCORD_HEALTH_MONITOR_WEBHOOK_URL", "")


def _post_health_monitor_warning(message: str) -> None:
    """Post a warning to the Discord health monitor webhook (best-effort, never raises)."""
    url = DISCORD_HEALTH_MONITOR_WEBHOOK_URL
    if not url:
        return
    try:
        requests.post(url, json={"content": message}, timeout=10)
    except Exception:
        pass


def check_token(token: str, label: str) -> bool:
    """Return True if the token is valid, False otherwise.

    On 401, prints a warning and posts to the health monitor webhook.
    """
    session = requests.Session()
    session.headers.update(
        {
            "Authorization": f"Bot {token}",
            "Content-Type": "application/json",
        }
    )
    try:
        response = session.get(
            "https://discord.com/api/v10/users/@me",
            timeout=10,
        )
    except requests.RequestException as exc:
        print(f"WARN: {label} — request failed: {exc}", file=sys.stderr)
        _post_health_monitor_warning(
            f"⚠️ Discord bot token health check failed for {label}\n"
            f"Request error: {exc}"
        )
        return False

    if response.status_code == 401:
        print(f"WARN: {label} — token is invalid (401 Unauthorized)", file=sys.stderr)
        _post_health_monitor_warning(
            f"🔴 Discord bot token is invalid for {label}\n"
            "The token returned 401 Unauthorized. "
            "Update the secret with a valid bot token."
        )
        return False

    if not response.ok:
        print(
            f"WARN: {label} — unexpected status {response.status_code}",
            file=sys.stderr,
        )
        return False

    data = response.json()
    username = data.get("username", "unknown")
    print(f"Bot token OK: @{username} ({label})")
    return True


def main() -> None:
    tokens: list[tuple[str, str]] = []

    bot_token = os.getenv("DISCORD_BOT_TOKEN", "")
    if bot_token:
        tokens.append((bot_token, "DISCORD_BOT_TOKEN"))
    else:
        print("WARN: DISCORD_BOT_TOKEN is not set", file=sys.stderr)
        _post_health_monitor_warning(
            "⚠️ DISCORD_BOT_TOKEN is not set\n"
            "The bot token secret is missing from this environment. "
            "Daily picks and library posts will fail without it."
        )

    scheduling_token = os.getenv("DISCORD_SCHEDULING_BOT_TOKEN", "")
    if scheduling_token:
        tokens.append((scheduling_token, "DISCORD_SCHEDULING_BOT_TOKEN"))
    else:
        print("WARN: DISCORD_SCHEDULING_BOT_TOKEN is not set", file=sys.stderr)
        _post_health_monitor_warning(
            "⚠️ DISCORD_SCHEDULING_BOT_TOKEN is not set\n"
            "The scheduling bot token secret is missing from this environment. "
            "Weekly scheduling posts will fail without it."
        )

    for token, label in tokens:
        check_token(token, label)


if __name__ == "__main__":
    main()
