import json
import os
import re
import time
from datetime import datetime, timedelta, timezone
from typing import Dict, List, Optional, Tuple

import requests
from bs4 import BeautifulSoup

WEBHOOK_URL = os.getenv("DISCORD_WEBHOOK_URL")
STATE_FILE = "seen_ids.json"

HEADERS = {
    "User-Agent": "Mozilla/5.0"
}

# ---------- CONFIG ----------
MAX_FREE_POSTS = 10
MAX_PAID_POSTS = 10

STEAM_FREE_PAGES = 20
STEAM_DEMO_PAGES = 20
STEAM_PAID_PAGES = 20

REQUEST_DELAY_SECONDS = 0.5
REPOST_COOLDOWN_DAYS = 45
MIN_SCORE_TO_POST_FREE = 8
MIN_SCORE_TO_POST_PAID = 4

STEAM_FREE_SEARCH_URL = "https://store.steampowered.com/search/?maxprice=free&page={}"
STEAM_DEMO_SEARCH_URL = "https://store.steampowered.com/search/?category1=10&page={}"
STEAM_PAID_SEARCH_URL = "https://store.steampowered.com/search/?maxprice=20&page={}"
STEAMDB_FREE_PROMO_URL = "https://steamdb.info/upcoming/free/"

MULTIPLAYER_TERMS = {
    "Massively Multiplayer": 6,
    "MMO": 6,
    "Online Co-Op": 5,
    "Online Co-op": 5,
    "Co-op": 4,
    "Co-Op": 4,
    "Multiplayer": 3,
    "Multi-player": 3,
    "Online PvP": 3,
    "PvP": 2,
    "Squad": 2,
    "Team-based": 2,
}

GOOD_GENRE_TERMS = {
    "Survival": 2,
    "Shooter": 2,
    "Action": 1,
    "Action RPG": 2,
    "RPG": 1,
    "Party": 2,
    "Roguelike": 2,
    "Roguelite": 2,
    "Extraction": 2,
    "Dungeon": 1,
    "Crafting": 1,
    "Base-building": 1,
    "Hack and slash": 1,
    "Adventure": 1,
}

GOOD_DESCRIPTION_TERMS = {
    "team up": 2,
    "friends": 1,
    "squad": 2,
    "party": 2,
    "raid": 1,
    "cooperate": 2,
    "cooperative": 2,
    "online co-op": 3,
    "multiplayer": 2,
    "co-op": 2,
    "pvp": 1,
}

BAD_TERMS = {
    "Soundtrack": -10,
    "soundtrack": -10,
    "DLC": -8,
    "benchmark": -6,
    "playtest": -4,
    "test server": -5,
    "dedicated server": -5,
    "server tools": -6,
    "wallpaper": -8,
    "art book": -8,
    "prologue": -3,
    "character creator": -4,
}

PLAYER_COUNT_PATTERNS = [
    (r"\b1\s*-\s*4\b", 4),
    (r"\b1\s*-\s*6\b", 5),
    (r"\b1\s*-\s*8\b", 6),
    (r"\b2\s*-\s*4\b", 4),
    (r"\b2\s*-\s*6\b", 5),
    (r"\b2\s*-\s*8\b", 6),
    (r"\b3\s*\+\b", 5),
    (r"\b3\s*-\s*4\b", 4),
    (r"\b3\s*-\s*6\b", 5),
    (r"\b3\s*-\s*8\b", 6),
    (r"\b4\s*player\b", 4),
    (r"\b4\s*players\b", 4),
    (r"\bup to 4 players\b", 4),
    (r"\bup to 6 players\b", 5),
    (r"\bup to 8 players\b", 6),
]

REJECT_PATTERNS = [
    r"\b1\s*-\s*2\b",
    r"\b2\s*player\b",
    r"\b2\s*players\b",
    r"\b2-player\b",
    r"\btwo-player only\b",
    r"\bfor 2 players\b",
]


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def parse_iso(s: str) -> datetime:
    return datetime.fromisoformat(s)


def load_state() -> Dict[str, dict]:
    if not os.path.exists(STATE_FILE):
        return {}

    try:
        with open(STATE_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)

        if isinstance(data, list):
            converted = {}
            now = utc_now_iso()
            for app_id in data:
                converted[str(app_id)] = {
                    "last_posted": now,
                    "last_type": "unknown"
                }
            return converted

        if isinstance(data, dict):
            return data

        return {}
    except Exception:
        return {}


def save_state(state: Dict[str, dict]) -> None:
    with open(STATE_FILE, "w", encoding="utf-8") as f:
        json.dump(state, f, indent=2, sort_keys=True)


def can_repost(app_id: str, item_type: str, state: Dict[str, dict]) -> bool:
    if app_id not in state:
        return True

    entry = state[app_id]
    last_posted = entry.get("last_posted")
    last_type = entry.get("last_type", "unknown")

    if last_type != item_type:
        return True

    if not last_posted:
        return True

    try:
        dt = parse_iso(last_posted)
    except Exception:
        return True

    cooldown_cutoff = datetime.now(timezone.utc) - timedelta(days=REPOST_COOLDOWN_DAYS)
    return dt < cooldown_cutoff


def update_state_for_post(app_id: str, item_type: str, state: Dict[str, dict]) -> None:
    state[app_id] = {
        "last_posted": utc_now_iso(),
        "last_type": item_type
    }


def fetch_html(url: str) -> str:
    response = requests.get(url, headers=HEADERS, timeout=30)
    response.raise_for_status()
    return response.text


def safe_fetch_html(url: str) -> Optional[str]:
    try:
        return fetch_html(url)
    except Exception:
        return None


def sleep_briefly():
    time.sleep(REQUEST_DELAY_SECONDS)


def extract_appids_from_html(html: str) -> List[str]:
    ids = re.findall(r"/app/(\d+)", html)
    seen = set()
    result = []
    for app_id in ids:
        if app_id not in seen:
            seen.add(app_id)
            result.append(app_id)
    return result


def collect_steam_free_candidates() -> List[Tuple[str, str]]:
    results = []
    seen = set()

    for page in range(1, STEAM_FREE_PAGES + 1):
        html = safe_fetch_html(STEAM_FREE_SEARCH_URL.format(page))
        if not html:
            continue

        for app_id in extract_appids_from_html(html):
            key = ("steam_free", app_id)
            if key not in seen:
                seen.add(key)
                results.append(("steam_free", app_id))

        sleep_briefly()

    return results


def collect_steam_demo_candidates() -> List[Tuple[str, str]]:
    results = []
    seen = set()

    for page in range(1, STEAM_DEMO_PAGES + 1):
        html = safe_fetch_html(STEAM_DEMO_SEARCH_URL.format(page))
        if not html:
            continue

        for app_id in extract_appids_from_html(html):
            key = ("steam_demo", app_id)
            if key not in seen:
                seen.add(key)
                results.append(("steam_demo", app_id))

        sleep_briefly()

    return results


def collect_paid_candidates() -> List[Tuple[str, str]]:
    results = []
    seen = set()

    for page in range(1, STEAM_PAID_PAGES + 1):
        html = safe_fetch_html(STEAM_PAID_SEARCH_URL.format(page))
        if not html:
            continue

        for app_id in extract_appids_from_html(html):
            key = ("paid_under_20", app_id)
            if key not in seen:
                seen.add(key)
                results.append(("paid_under_20", app_id))

        sleep_briefly()

    return results


def collect_steamdb_promo_candidates() -> List[Tuple[str, str]]:
    html = safe_fetch_html(STEAMDB_FREE_PROMO_URL)
    if not html:
        return []

    ids = extract_appids_from_html(html)
    return [("steamdb_promo", app_id) for app_id in ids]


def collect_all_candidates() -> List[Tuple[str, str]]:
    combined = []
    seen_app_ids = set()

    sources = (
        collect_steam_free_candidates() +
        collect_steam_demo_candidates() +
        collect_steamdb_promo_candidates() +
        collect_paid_candidates()
    )

    for source, app_id in sources:
        if app_id not in seen_app_ids:
            seen_app_ids.add(app_id)
            combined.append((source, app_id))

    return combined


def clean_text(s: str) -> str:
    return " ".join(s.split())


def parse_title(soup: BeautifulSoup) -> Optional[str]:
    title_el = soup.select_one("#appHubAppName")
    if title_el:
        return clean_text(title_el.get_text(" ", strip=True))

    meta_title = soup.find("meta", property="og:title")
    if meta_title and meta_title.get("content"):
        return clean_text(meta_title["content"])

    return None


def parse_description(soup: BeautifulSoup) -> str:
    desc_el = soup.select_one(".game_description_snippet")
    if desc_el:
        return clean_text(desc_el.get_text(" ", strip=True))

    meta_desc = soup.find("meta", attrs={"name": "Description"})
    if meta_desc and meta_desc.get("content"):
        return clean_text(meta_desc["content"])

    return ""


def detect_item_type(source: str, title: str, text: str) -> str:
    lower_title = title.lower()
    lower_text = text.lower()

    if source == "paid_under_20":
        return "paid_under_20"

    if source == "steamdb_promo" or "free to keep" in lower_text or "100% off" in lower_text:
        return "temporarily_free"

    if source == "steam_demo" or "demo" in lower_title or "demo" in lower_text:
        return "demo"

    return "free_game"


def score_multiplayer(text: str) -> Tuple[int, List[str]]:
    score = 0
    hits = []

    for term, points in MULTIPLAYER_TERMS.items():
        if term.lower() in text.lower():
            score += points
            hits.append(term)

    return score, hits


def score_player_count(text: str) -> Tuple[int, List[str], bool]:
    score = 0
    hits = []
    rejected = False

    lower_text = text.lower()

    if "massively multiplayer" in lower_text or re.search(r"\bmmo\b", lower_text):
        score += 6
        hits.append("MMO/Massively Multiplayer")
        return score, hits, False

    for pattern, points in PLAYER_COUNT_PATTERNS:
        if re.search(pattern, text, re.IGNORECASE):
            score += points
            hits.append(pattern)

    for pattern in REJECT_PATTERNS:
        if re.search(pattern, text, re.IGNORECASE):
            rejected = True

    return score, hits, rejected


def score_genres_and_description(title: str, description: str, text: str) -> Tuple[int, List[str]]:
    score = 0
    hits = []

    combined = f"{title} {description} {text}"

    for term, points in GOOD_GENRE_TERMS.items():
        if term.lower() in combined.lower():
            score += points
            hits.append(term)

    for term, points in GOOD_DESCRIPTION_TERMS.items():
        if term.lower() in combined.lower():
            score += points
            hits.append(term)

    for term, points in BAD_TERMS.items():
        if term.lower() in combined.lower():
            score += points
            hits.append(term)

    return score, hits


def extract_review_score(soup: BeautifulSoup) -> int:
    review_element = soup.select_one(".user_reviews_summary_row")
    if not review_element:
        return 0

    tooltip = review_element.get("data-tooltip-html", "")
    text = BeautifulSoup(tooltip, "html.parser").get_text(" ", strip=True)

    if "Overwhelmingly Positive" in text:
        return 6
    if "Very Positive" in text:
        return 5
    if "Positive" in text:
        return 4
    if "Mostly Positive" in text:
        return 3
    if "Mixed" in text:
        return 0
    if "Mostly Negative" in text:
        return -3
    if "Negative" in text:
        return -4
    if "Very Negative" in text:
        return -5
    if "Overwhelmingly Negative" in text:
        return -6

    return 0


def inspect_game(source: str, app_id: str) -> Optional[dict]:
    url = f"https://store.steampowered.com/app/{app_id}/"
    html = safe_fetch_html(url)
    if not html:
        return None

    soup = BeautifulSoup(html, "html.parser")
    title = parse_title(soup)
    if not title:
        return None

    description = parse_description(soup)
    page_text = clean_text(soup.get_text(" ", strip=True))

    item_type = detect_item_type(source, title, page_text)

    multiplayer_score, multiplayer_hits = score_multiplayer(page_text)
    player_score, player_hits, rejected = score_player_count(page_text)
    flavor_score, flavor_hits = score_genres_and_description(title, description, page_text)
    review_score = extract_review_score(soup)

    total_score = multiplayer_score + player_score + flavor_score + review_score

    has_multiplayer_signal = multiplayer_score > 0
    has_3plus_signal = player_score > 0

    if item_type == "paid_under_20":
        keep = (
            has_multiplayer_signal and
            not rejected and
            total_score >= MIN_SCORE_TO_POST_PAID
        )
    else:
        keep = (
            has_multiplayer_signal and
            has_3plus_signal and
            not rejected and
            total_score >= MIN_SCORE_TO_POST_FREE
        )

    return {
        "id": app_id,
        "title": title,
        "url": url,
        "description": description,
        "type": item_type,
        "source": source,
        "score": total_score,
        "keep": keep,
        "rejected": rejected,
        "multiplayer_hits": multiplayer_hits,
        "player_hits": player_hits,
        "flavor_hits": flavor_hits,
        "review_score": review_score,
    }


def type_label(item_type: str) -> str:
    if item_type == "demo":
        return "Demo"
    if item_type == "temporarily_free":
        return "Temporarily Free"
    if item_type == "paid_under_20":
        return "Paid Under $20"
    return "Free Game"


def build_free_message(free_items: List[dict]) -> str:
    lines = []
    lines.append("🎮 **Best Free 3+ Player Multiplayer / Co-op Games & Demos Today**")
    lines.append("")

    for idx, item in enumerate(free_items, start=1):
        lines.append(f"**{idx}. {item['title']}**")
        lines.append(f"Type: {type_label(item['type'])}")
        lines.append(f"Score: {item['score']}")
        if item["description"]:
            lines.append(item["description"][:180])
        lines.append(item["url"])
        lines.append("")

    return "\n".join(lines)[:1900]


def build_paid_message(paid_items: List[dict]) -> str:
    lines = []
    lines.append("💸 **Best Multiplayer / Co-op Games Under $20 Today**")
    lines.append("")

    for idx, item in enumerate(paid_items, start=1):
        lines.append(f"**{idx}. {item['title']}**")
        lines.append("Type: Paid Under $20")
        lines.append(f"Score: {item['score']}")
        if item["description"]:
            lines.append(item["description"][:180])
        lines.append(item["url"])
        lines.append("")

    return "\n".join(lines)[:1900]


def post_to_discord(message: str) -> None:
    if not WEBHOOK_URL:
        raise RuntimeError("DISCORD_WEBHOOK_URL is not set.")

    response = requests.post(
        WEBHOOK_URL,
        json={"content": message},
        headers={"Content-Type": "application/json"},
        timeout=30,
    )
    response.raise_for_status()


def main():
    state = load_state()
    candidates = collect_all_candidates()

    total_candidates = len(candidates)
    paid_candidates = sum(1 for source, _ in candidates if source == "paid_under_20")
    print(f"Total candidates collected: {total_candidates}")
    print(f"Paid candidates collected: {paid_candidates}")

    inspected_items = []

    for source, app_id in candidates:
        item = inspect_game(source, app_id)
        sleep_briefly()

        if not item:
            continue

        if not item["keep"]:
            continue

        if not can_repost(app_id, item["type"], state):
            continue

        inspected_items.append(item)

    if not inspected_items:
        print("No new qualifying games found. Nothing will be posted to Discord.")
        return

    inspected_items.sort(
        key=lambda x: (
            x["score"],
            x.get("review_score", 0),
            1 if x["type"] == "temporarily_free" else 0,
            1 if x["type"] == "demo" else 0,
        ),
        reverse=True
    )

    free_items = [
        item for item in inspected_items
        if item["type"] in ["free_game", "demo", "temporarily_free"]
    ][:MAX_FREE_POSTS]

    paid_items = [
        item for item in inspected_items
        if item["type"] == "paid_under_20"
    ][:MAX_PAID_POSTS]

    if not free_items and not paid_items:
        print("No qualifying games found.")
        return

    if free_items:
        free_message = build_free_message(free_items)
        post_to_discord(free_message)

    if paid_items:
        paid_message = build_paid_message(paid_items)
        post_to_discord(paid_message)

    for item in free_items + paid_items:
        update_state_for_post(item["id"], item["type"], state)

    save_state(state)

    total = len(free_items) + len(paid_items)
    print(f"Posted {total} item(s) to Discord.")
    print(f"Free items selected: {len(free_items)}")
    print(f"Paid items selected: {len(paid_items)}")

    for item in free_items:
        print(
            f"FREE: {item['title']} ({item['type']}) "
            f"score={item['score']} review_score={item.get('review_score', 0)}"
        )

    for item in paid_items:
        print(
            f"PAID: {item['title']} ({item['type']}) "
            f"score={item['score']} review_score={item.get('review_score', 0)}"
        )


if __name__ == "__main__":
    main()
