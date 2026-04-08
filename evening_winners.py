import json
import os
import time
from datetime import datetime, timezone
from typing import Dict, List, Optional

import requests

DISCORD_DAILY_POSTS_FILE = "discord_daily_posts.json"
DISCORD_API_BASE = "https://discord.com/api/v10"
THUMBS_UP_EMOJI = "👍"
MAX_RETRIES = 5
BASE_BACKOFF_SECONDS = 2
REQUEST_TIMEOUT_SECONDS = 30

DISCORD_BOT_TOKEN = os.getenv("DISCORD_BOT_TOKEN")
DISCORD_WINNERS_CHANNEL_ID = os.getenv("DISCORD_WINNERS_CHANNEL_ID")


SECTION_CONFIG = {
    "free": "Free Picks",
    "paid": "Paid Under $20",
    "instagram": "Instagram Creator Picks",
}
SECTION_ORDER = ["free", "paid", "instagram"]


def load_discord_daily_posts() -> Dict[str, dict]:
    if not os.path.exists(DISCORD_DAILY_POSTS_FILE):
        return {}

    with open(DISCORD_DAILY_POSTS_FILE, "r", encoding="utf-8") as f:
        data = json.load(f)

    return data if isinstance(data, dict) else {}


def request_with_retry(method: str, url: str, headers: Dict[str, str], json_payload: Optional[dict] = None) -> requests.Response:
    last_exc: Optional[Exception] = None

    for attempt in range(1, MAX_RETRIES + 1):
        try:
            response = requests.request(
                method=method,
                url=url,
                headers=headers,
                json=json_payload,
                timeout=REQUEST_TIMEOUT_SECONDS,
            )

            if response.status_code in (429, 500, 502, 503, 504):
                retry_after = response.headers.get("Retry-After")
                if retry_after:
                    try:
                        sleep_seconds = max(float(retry_after), 0.0)
                    except ValueError:
                        sleep_seconds = BASE_BACKOFF_SECONDS * attempt
                else:
                    sleep_seconds = BASE_BACKOFF_SECONDS * attempt

                if attempt == MAX_RETRIES:
                    response.raise_for_status()

                print(
                    f"RETRYABLE DISCORD ERROR: status={response.status_code} attempt={attempt}/{MAX_RETRIES}; sleeping {sleep_seconds:.1f}s"
                )
                time.sleep(sleep_seconds)
                continue

            response.raise_for_status()
            return response
        except requests.RequestException as exc:
            last_exc = exc
            if attempt == MAX_RETRIES:
                raise

            sleep_seconds = BASE_BACKOFF_SECONDS * attempt
            print(
                f"REQUEST EXCEPTION: attempt={attempt}/{MAX_RETRIES}; sleeping {sleep_seconds:.1f}s | error={exc}"
            )
            time.sleep(sleep_seconds)

    if last_exc:
        raise last_exc
    raise RuntimeError("request_with_retry exhausted retries unexpectedly")


def fetch_message(channel_id: str, message_id: str, headers: Dict[str, str]) -> dict:
    url = f"{DISCORD_API_BASE}/channels/{channel_id}/messages/{message_id}"
    response = request_with_retry("GET", url, headers)
    return response.json()


def get_thumbsup_count(message_payload: dict) -> int:
    for reaction in message_payload.get("reactions", []):
        emoji = reaction.get("emoji", {})
        if emoji.get("name") == THUMBS_UP_EMOJI:
            return int(reaction.get("count", 0))
    return 0


def build_winners_message(winners_by_section: Dict[str, List[dict]]) -> str:
    lines = ["🏆 Daily Game Picks — Winners", ""]
    has_any_winners = False

    for section in SECTION_ORDER:
        items = winners_by_section.get(section, [])
        if not items:
            continue

        has_any_winners = True
        lines.append(SECTION_CONFIG[section])
        for item in items:
            lines.append(item["title"])
            lines.append(item["url"])
            vote_word = "vote" if item["human_votes"] == 1 else "votes"
            lines.append(f"👍 {item['human_votes']} {vote_word}")
            lines.append("")

    if not has_any_winners:
        lines.append("_No votes yet today._")

    return "\n".join(lines).strip()


def post_winners_message(channel_id: str, headers: Dict[str, str], message: str) -> None:
    url = f"{DISCORD_API_BASE}/channels/{channel_id}/messages"
    request_with_retry("POST", url, headers, json_payload={"content": message})


def main() -> None:
    if not DISCORD_BOT_TOKEN:
        raise RuntimeError("DISCORD_BOT_TOKEN is not set.")
    if not DISCORD_WINNERS_CHANNEL_ID:
        raise RuntimeError("DISCORD_WINNERS_CHANNEL_ID is not set.")

    daily_posts = load_discord_daily_posts()
    day_key = datetime.now(timezone.utc).date().isoformat()
    today_entry = daily_posts.get(day_key, {})
    items = today_entry.get("items", []) if isinstance(today_entry, dict) else []

    headers = {
        "Authorization": f"Bot {DISCORD_BOT_TOKEN}",
        "Content-Type": "application/json",
    }

    winners_by_section: Dict[str, List[dict]] = {key: [] for key in SECTION_ORDER}

    for item in items:
        section = item.get("section")
        channel_id = item.get("channel_id")
        message_id = item.get("message_id")

        if section not in SECTION_CONFIG:
            continue
        if not channel_id or not message_id:
            continue

        message_payload = fetch_message(channel_id=str(channel_id), message_id=str(message_id), headers=headers)
        raw_thumbsup_count = get_thumbsup_count(message_payload)
        human_votes = raw_thumbsup_count - 1

        if human_votes < 1:
            continue

        winners_by_section[section].append(
            {
                "title": item.get("title", "Untitled"),
                "url": item.get("url", ""),
                "human_votes": human_votes,
            }
        )

    message = build_winners_message(winners_by_section)
    post_winners_message(DISCORD_WINNERS_CHANNEL_ID, headers, message)


if __name__ == "__main__":
    main()
