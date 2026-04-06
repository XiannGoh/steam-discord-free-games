import json
import os
import re
import time
from datetime import datetime, timedelta, timezone
from typing import Dict, List, Optional, Tuple

import requests
from bs4 import BeautifulSoup

try:
    import instaloader
except ImportError:
    instaloader = None

WEBHOOK_URL = os.getenv("DISCORD_WEBHOOK_URL")
STATE_FILE = "seen_ids.json"
PAGE_STATE_FILE = "page_state.json"
INSTAGRAM_STATE_FILE = "instagram_seen.json"

HEADERS = {
    "User-Agent": "Mozilla/5.0"
}

MAX_FREE_POSTS = 10
MAX_PAID_POSTS = 10
PAGE_WINDOW_SIZE = 10
MAX_PAGE_LIMIT = 50
REQUEST_DELAY_SECONDS = 1.2
REPOST_COOLDOWN_DAYS = 30
DISCORD_CHAR_LIMIT = 1900
MAX_INSTAGRAM_POSTS_PER_ACCOUNT = 2

INSTAGRAM_CREATORS = [
    "gemgamingnetwork",
    "cloudual",
    "mildsoss_official",
    "sharedxp_official",
    "wilfratgaming",
    "indiegamespotlights",
]

STEAM_FREE_SEARCH_URL = "https://store.steampowered.com/search/?maxprice=free&page={}"
STEAM_TOPSELLERS_URL = "https://store.steampowered.com/search/?filter=topsellers&page={}"


def utc_now_iso():
    return datetime.now(timezone.utc).isoformat()


def load_state(path):
    if not os.path.exists(path):
        return {}
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def save_state(path, data):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)


def load_page_state():
    data = load_state(PAGE_STATE_FILE)
    start_page = int(data.get("start_page", 1))
    if start_page > MAX_PAGE_LIMIT:
        return 1
    return start_page


def save_page_state(start_page):
    save_state(PAGE_STATE_FILE, {
        "start_page": start_page,
        "updated_at": utc_now_iso()
    })


def next_page_window(current_start):
    next_start = current_start + PAGE_WINDOW_SIZE
    if next_start > MAX_PAGE_LIMIT:
        return 1
    return next_start


def fetch_html(url):
    response = requests.get(url, headers=HEADERS, timeout=30)
    response.raise_for_status()
    return response.text


def extract_app_ids(html):
    ids = re.findall(r"/app/(\d+)", html)
    seen = set()
    results = []
    for app_id in ids:
        if app_id not in seen:
            seen.add(app_id)
            results.append(app_id)
    return results


def load_instagram_seen():
    return load_state(INSTAGRAM_STATE_FILE)


def save_instagram_seen(data):
    save_state(INSTAGRAM_STATE_FILE, data)


def fetch_instagram_posts():
    if instaloader is None:
        print("instaloader not installed; skipping Instagram section")
        return []

    seen = load_instagram_seen()
    all_new_posts = []

    loader = instaloader.Instaloader(
        download_pictures=False,
        download_videos=False,
        download_video_thumbnails=False,
        download_geotags=False,
        download_comments=False,
        save_metadata=False,
        quiet=True,
    )

    for username in INSTAGRAM_CREATORS:
        try:
            if username not in seen:
                seen[username] = []

            profile = instaloader.Profile.from_username(loader.context, username)
            count = 0

            for post in profile.get_posts():
                shortcode = post.shortcode

                if shortcode in seen[username]:
                    continue

                caption = (post.caption or "").replace("\n", " ").strip()
                if len(caption) > 120:
                    caption = caption[:117] + "..."

                all_new_posts.append({
                    "username": username,
                    "caption": caption or "(no caption)",
                    "url": f"https://www.instagram.com/p/{shortcode}/",
                })

                seen[username].append(shortcode)
                count += 1

                if count >= MAX_INSTAGRAM_POSTS_PER_ACCOUNT:
                    break

            seen[username] = seen[username][-50:]

        except Exception as e:
            print(f"Instagram scrape failed for {username}: {e}")
            continue

    save_instagram_seen(seen)
    return all_new_posts


def send_discord_message(message):
    if not WEBHOOK_URL:
        raise ValueError("DISCORD_WEBHOOK_URL missing")

    response = requests.post(
        WEBHOOK_URL,
        json={"content": message},
        timeout=30,
    )
    response.raise_for_status()


def main():
    seen_ids = load_state(STATE_FILE)

    start_page = load_page_state()
    end_page = min(start_page + PAGE_WINDOW_SIZE - 1, MAX_PAGE_LIMIT)

    print(f"Scanning Steam pages {start_page}-{end_page}")

    free_lines = ["🎮 Best Free Multiplayer Games", ""]
    paid_lines = ["💸 Best Multiplayer Games Under $20", ""]

    free_count = 0
    paid_count = 0

    for page in range(start_page, end_page + 1):
        try:
            free_html = fetch_html(STEAM_FREE_SEARCH_URL.format(page))
            free_ids = extract_app_ids(free_html)

            for app_id in free_ids:
                if app_id in seen_ids:
                    continue

                free_lines.append(f"{free_count + 1}. https://store.steampowered.com/app/{app_id}/")
                free_lines.append("")
                seen_ids[app_id] = utc_now_iso()
                free_count += 1

                if free_count >= MAX_FREE_POSTS:
                    break

            paid_html = fetch_html(STEAM_TOPSELLERS_URL.format(page))
            paid_ids = extract_app_ids(paid_html)

            for app_id in paid_ids:
                paid_key = f"paid_{app_id}"
                if paid_key in seen_ids:
                    continue

                paid_lines.append(f"{paid_count + 1}. https://store.steampowered.com/app/{app_id}/")
                paid_lines.append("")
                seen_ids[paid_key] = utc_now_iso()
                paid_count += 1

                if paid_count >= MAX_PAID_POSTS:
                    break

            if free_count >= MAX_FREE_POSTS and paid_count >= MAX_PAID_POSTS:
                break

        except Exception as e:
            print(f"Steam scan failed on page {page}: {e}")

        time.sleep(REQUEST_DELAY_SECONDS)

    message_parts = []

    if free_count:
        message_parts.append("\n".join(free_lines))

    if paid_count:
        message_parts.append("\n".join(paid_lines))

    instagram_posts = fetch_instagram_posts()

    if instagram_posts:
        instagram_lines = ["📸 New Instagram Creator Picks", ""]

        for idx, post in enumerate(instagram_posts, start=1):
            instagram_lines.append(
                f"{idx}. @{post['username']} — {post['caption']}"
            )
            instagram_lines.append(post["url"])
            instagram_lines.append("")

        message_parts.append("\n".join(instagram_lines))

    if message_parts:
        send_discord_message("\n\n".join(message_parts)[:DISCORD_CHAR_LIMIT])

    save_state(STATE_FILE, seen_ids)
    save_page_state(next_page_window(start_page))


if __name__ == "__main__":
    main()
