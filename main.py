import json
import os
import re
import time
import requests
from bs4 import BeautifulSoup

WEBHOOK_URL = os.getenv("DISCORD_WEBHOOK_URL")

SEEN_FILE = "seen_ids.json"

HEADERS = {
    "User-Agent": "Mozilla/5.0"
}

FREE_SEARCH_URL = "https://store.steampowered.com/search/?maxprice=free&page={}"

MULTIPLAYER_TERMS = [
    "Multiplayer",
    "Multi-player",
    "Co-op",
    "Online Co-Op",
    "Online PvP",
    "MMO",
    "Massively Multiplayer"
]

PLAYER_COUNT_PATTERNS = [
    r"1-4",
    r"1-6",
    r"1-8",
    r"2-4",
    r"2-6",
    r"2-8",
    r"3\+",
    r"3-",
    r"4-",
    r"up to 4",
    r"up to 6",
    r"up to 8",
]

REJECT_PATTERNS = [
    r"1-2",
    r"2 player",
    r"2-player",
    r"two-player only"
]


def load_seen():
    try:
        with open(SEEN_FILE, "r") as f:
            return set(json.load(f))
    except:
        return set()


def save_seen(seen):
    with open(SEEN_FILE, "w") as f:
        json.dump(list(seen), f, indent=2)


def get_candidate_games():
    candidates = []

    for page in range(1, 4):
        url = FREE_SEARCH_URL.format(page)
        response = requests.get(url, headers=HEADERS)
        html = response.text

        ids = re.findall(r"/app/(\d+)", html)

        for app_id in ids:
            if app_id not in candidates:
                candidates.append(app_id)

        time.sleep(1)

    return candidates


def inspect_game(app_id):
    url = f"https://store.steampowered.com/app/{app_id}/"
    response = requests.get(url, headers=HEADERS)
    html = response.text

    soup = BeautifulSoup(html, "html.parser")
    page_text = soup.get_text(" ", strip=True)

    title_element = soup.select_one("#appHubAppName")
    if not title_element:
        return None

    title = title_element.get_text(strip=True)

    is_multiplayer = any(term.lower() in page_text.lower() for term in MULTIPLAYER_TERMS)

    has_3plus = any(re.search(pattern, page_text, re.IGNORECASE) for pattern in PLAYER_COUNT_PATTERNS)

    rejected = any(re.search(pattern, page_text, re.IGNORECASE) for pattern in REJECT_PATTERNS)

    is_demo = "demo" in title.lower() or "demo" in page_text.lower()

    is_temp_free = "free to keep" in page_text.lower() or "100% off" in page_text.lower()

    if is_multiplayer and has_3plus and not rejected:
        return {
            "id": app_id,
            "title": title,
            "url": url,
            "type": (
                "Demo" if is_demo
                else "Temporarily Free" if is_temp_free
                else "Free Game"
            )
        }

    return None


def send_to_discord(games):
    if not games:
        return

    message = "🎮 **Today's Free 3+ Player Multiplayer / Co-op Steam Finds**\n\n"

    for i, game in enumerate(games, start=1):
        message += f"**{i}. {game['title']}**\n"
        message += f"Type: {game['type']}\n"
        message += f"{game['url']}\n\n"

    requests.post(WEBHOOK_URL, json={"content": message[:1900]})


seen = load_seen()
candidates = get_candidate_games()

new_games = []

for app_id in candidates:
    if app_id in seen:
        continue

    try:
        game = inspect_game(app_id)
    except Exception:
        continue

    if game:
        new_games.append(game)
        seen.add(app_id)

    if len(new_games) >= 20:
        break

save_seen(seen)
send_to_discord(new_games)
