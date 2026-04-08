import json
import os
import re
import time
from datetime import datetime, timedelta, timezone
from typing import Dict, List, Optional, Tuple
from urllib.parse import urlparse

import requests
from bs4 import BeautifulSoup
try:
    import instaloader
except ImportError:
    instaloader = None

WEBHOOK_URL = os.getenv("DISCORD_WEBHOOK_URL")
DISCORD_BOT_TOKEN = os.getenv("DISCORD_BOT_TOKEN")
STATE_FILE = "seen_ids.json"
PAGE_STATE_FILE = "page_state.json"
INSTAGRAM_STATE_FILE = "instagram_seen.json"
DISCORD_DAILY_POSTS_FILE = "discord_daily_posts.json"
DISCORD_DAILY_POSTS_RETENTION_DAYS = 30

INSTAGRAM_CREATORS = [
    "gemgamingnetwork",
    "cloudual",
    "mildsoss_official",
    "sharedxp_official",
    "wilfratgaming",
    "indiegamespotlights",
    "biffmatictv",
    "itzjaysasa",
]

MAX_INSTAGRAM_POSTS_PER_ACCOUNT = 2

HEADERS = {
    "User-Agent": "Mozilla/5.0"
}

# ---------- CONFIG ----------
MAX_FREE_POSTS = 10
MAX_PAID_POSTS = 10

PAGE_WINDOW_SIZE = 10
MAX_PAGE_LIMIT = 50

REQUEST_DELAY_SECONDS = 1.2
REPOST_COOLDOWN_DAYS = 30
MIN_SCORE_TO_POST_FREE = 8
MIN_SCORE_TO_POST_PAID = 4

MAX_FETCH_RETRIES = 5
BACKOFF_SECONDS = 4

STEAM_FREE_SEARCH_URL = "https://store.steampowered.com/search/?maxprice=free&page={}"
STEAM_DEMO_SEARCH_URL = "https://store.steampowered.com/search/?category1=10&page={}"
STEAM_TOPSELLERS_URL = "https://store.steampowered.com/search/?filter=topsellers&page={}"
STEAMDB_FREE_PROMO_URL = "https://steamdb.info/upcoming/free/"

DISCORD_CHAR_LIMIT = 1900

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


def load_page_state() -> int:
    if not os.path.exists(PAGE_STATE_FILE):
        return 1

    try:
        with open(PAGE_STATE_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)

        start_page = int(data.get("start_page", 1))
        valid_starts = set(range(1, MAX_PAGE_LIMIT + 1, PAGE_WINDOW_SIZE))

        if start_page not in valid_starts:
            return 1

        return start_page
    except Exception:
        return 1


def save_page_state(start_page: int) -> None:
    payload = {
        "start_page": start_page,
        "updated_at": utc_now_iso(),
    }
    with open(PAGE_STATE_FILE, "w", encoding="utf-8") as f:
        json.dump(payload, f, indent=2, sort_keys=True)


def get_page_window() -> Tuple[int, int]:
    start_page = load_page_state()
    end_page = min(start_page + PAGE_WINDOW_SIZE - 1, MAX_PAGE_LIMIT)
    return start_page, end_page


def get_next_start_page(current_start: int) -> int:
    next_start = current_start + PAGE_WINDOW_SIZE
    if next_start > MAX_PAGE_LIMIT:
        return 1
    return next_start


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
    for attempt in range(1, MAX_FETCH_RETRIES + 1):
        try:
            return fetch_html(url)
        except requests.HTTPError as e:
            status_code = e.response.status_code if e.response is not None else None
            if status_code == 429 and attempt < MAX_FETCH_RETRIES:
                wait_time = BACKOFF_SECONDS * attempt
                print(f"FETCH RETRY {attempt}/{MAX_FETCH_RETRIES}: {url} | 429 received, waiting {wait_time}s")
                time.sleep(wait_time)
                continue

            print(f"FETCH FAILED: {url} | error={e}")
            return None
        except Exception as e:
            print(f"FETCH FAILED: {url} | error={e}")
            return None


def sleep_briefly():
    time.sleep(REQUEST_DELAY_SECONDS)


def extract_appids_from_html(html: str, from_search_results: bool = False) -> List[str]:
    ids: List[str] = []
    if from_search_results:
        soup = BeautifulSoup(html, "html.parser")
        for anchor in soup.select("a.search_result_row[href*='/app/']"):
            href = anchor.get("href", "")
            match = re.search(r"/app/(\d+)", href)
            if match:
                ids.append(match.group(1))
    else:
        ids = re.findall(r"/app/(\d+)", html)

    seen = set()
    result = []
    for app_id in ids:
        if app_id not in seen:
            seen.add(app_id)
            result.append(app_id)
    return result


def collect_steam_free_candidates(start_page: int, end_page: int) -> List[Tuple[str, str]]:
    results = []
    seen = set()

    for page in range(start_page, end_page + 1):
        url = STEAM_FREE_SEARCH_URL.format(page)
        html = safe_fetch_html(url)
        if not html:
            continue

        page_ids = extract_appids_from_html(html, from_search_results=True)
        print(f"FREE PAGE {page}: extracted {len(page_ids)} app ids")

        for app_id in page_ids:
            key = ("steam_free", app_id)
            if key not in seen:
                seen.add(key)
                results.append(("steam_free", app_id))

        sleep_briefly()

    return results


def collect_steam_demo_candidates(start_page: int, end_page: int) -> List[Tuple[str, str]]:
    results = []
    seen = set()

    for page in range(start_page, end_page + 1):
        url = STEAM_DEMO_SEARCH_URL.format(page)
        html = safe_fetch_html(url)
        if not html:
            continue

        page_ids = extract_appids_from_html(html, from_search_results=True)
        print(f"DEMO PAGE {page}: extracted {len(page_ids)} app ids")

        for app_id in page_ids:
            key = ("steam_demo", app_id)
            if key not in seen:
                seen.add(key)
                results.append(("steam_demo", app_id))

        sleep_briefly()

    return results


def collect_paid_candidates(start_page: int, end_page: int) -> List[Tuple[str, str]]:
    results = []
    seen = set()

    for page in range(start_page, end_page + 1):
        url = STEAM_TOPSELLERS_URL.format(page)
        html = safe_fetch_html(url)
        if not html:
            continue

        page_ids = extract_appids_from_html(html, from_search_results=True)
        print(f"PAID PAGE {page}: extracted {len(page_ids)} app ids")

        for app_id in page_ids:
            key = ("paid_candidate", app_id)
            if key not in seen:
                seen.add(key)
                results.append(("paid_candidate", app_id))

        sleep_briefly()

    return results


def collect_steamdb_promo_candidates() -> List[Tuple[str, str]]:
    html = safe_fetch_html(STEAMDB_FREE_PROMO_URL)
    if not html:
        return []

    ids = extract_appids_from_html(html)
    print(f"STEAMDB PROMO: extracted {len(ids)} app ids")
    return [("steamdb_promo", app_id) for app_id in ids]


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


def get_price_info(app_id: str) -> Tuple[Optional[float], bool]:
    try:
        url = (
            f"https://store.steampowered.com/api/appdetails"
            f"?appids={app_id}&cc=us&filters=price_overview,is_free"
        )
        response = requests.get(url, headers=HEADERS, timeout=30)
        response.raise_for_status()
        data = response.json()

        app_data = data.get(str(app_id), {})
        if not app_data.get("success"):
            return None, False

        info = app_data.get("data", {})

        if info.get("is_free") is True:
            return 0.0, True

        price_info = info.get("price_overview")
        if not price_info:
            return None, False

        final_price_cents = price_info.get("final")
        if final_price_cents is None:
            return None, False

        return round(final_price_cents / 100.0, 2), False
    except Exception as e:
        print(f"PRICE API FAILED: app_id={app_id} | error={e}")
        return None, False


def detect_item_type(source: str, app_id: str, title: str, text: str) -> str:
    lower_title = title.lower()
    lower_text = text.lower()

    if source == "steamdb_promo" or "free to keep" in lower_text or "100% off" in lower_text:
        return "temporarily_free"

    if source == "steam_demo" or "demo" in lower_title or "demo" in lower_text:
        return "demo"

    if source == "paid_candidate":
        price, is_free = get_price_info(app_id)

        if is_free:
            return "ignore"

        if price is not None and 0 < price <= 20:
            return "paid_under_20"

        return "ignore"

    return "free_game"


def score_multiplayer(text: str) -> Tuple[int, List[str]]:
    score = 0
    hits = []

    lower_text = text.lower()
    for term, points in MULTIPLAYER_TERMS.items():
        if term.lower() in lower_text:
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
    lower_combined = combined.lower()

    for term, points in GOOD_GENRE_TERMS.items():
        if term.lower() in lower_combined:
            score += points
            hits.append(term)

    for term, points in GOOD_DESCRIPTION_TERMS.items():
        if term.lower() in lower_combined:
            score += points
            hits.append(term)

    for term, points in BAD_TERMS.items():
        if term.lower() in lower_combined:
            score += points
            hits.append(term)

    return score, hits


def extract_review_score(soup: BeautifulSoup) -> int:
    page_text = soup.get_text(" ", strip=True)

    if "Overwhelmingly Positive" in page_text:
        return 6
    if "Very Positive" in page_text:
        return 5
    if "Mostly Positive" in page_text:
        return 3
    if "Positive" in page_text:
        return 4
    if "Mixed" in page_text:
        return 0
    if "Mostly Negative" in page_text:
        return -3
    if "Very Negative" in page_text:
        return -5
    if "Overwhelmingly Negative" in page_text:
        return -6
    if "Negative" in page_text:
        return -4

    return 0


def inspect_game(source: str, app_id: str) -> Optional[dict]:
    url = f"https://store.steampowered.com/app/{app_id}/"
    try:
        response = requests.get(url, headers=HEADERS, timeout=30)
        response.raise_for_status()
    except Exception as e:
        print(f"FETCH FAILED: {url} | error={e}")
        return None

    final_url = response.url or url
    final_path = urlparse(final_url).path or ""
    if not re.search(r"/app/\d+", final_path):
        print(f"INVALID APP PAGE: app_id={app_id} | final_url={final_url}")
        return None

    html = response.text
    soup = BeautifulSoup(html, "html.parser")
    if not soup.select_one("#appHubAppName"):
        print(f"INVALID APP PAGE: app_id={app_id} | missing #appHubAppName")
        return None

    title = parse_title(soup)
    if not title:
        return None
    if title == "Steam Store":
        print(f"INVALID APP PAGE: app_id={app_id} | title={title}")
        return None

    description = parse_description(soup)
    page_text = clean_text(soup.get_text(" ", strip=True))

    item_type = detect_item_type(source, app_id, title, page_text)

    if source == "paid_candidate":
        price, is_free = get_price_info(app_id)
        print(f"PAID CHECK: {title} | price={price} | is_free={is_free} | type={item_type}")

    if item_type == "ignore":
        return None

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
    elif item_type in ["free_game", "demo", "temporarily_free"]:
        keep = (
            has_multiplayer_signal and
            has_3plus_signal and
            not rejected and
            total_score >= MIN_SCORE_TO_POST_FREE
        )
    else:
        keep = False

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


def format_item_block(item: dict, idx: int, paid: bool = False) -> str:
    lines = []
    lines.append(f"**{idx}. {item['title']}**")
    lines.append(f"Type: {'Paid Under $20' if paid else type_label(item['type'])}")
    lines.append(f"Score: {item['score']}")
    if item["description"]:
        lines.append(item["description"][:180])
    lines.append(item["url"])
    lines.append("")
    return "\n".join(lines)


def format_steam_item_message(item: dict, idx: int, paid: bool = False) -> str:
    emoji = "💸" if paid else "🎮"
    label = "Paid Pick" if paid else "Free Pick"
    lines = [
        f"{emoji} {label} #{idx}",
        item["title"],
        f"Score: {item['score']}",
    ]

    if item.get("description"):
        lines.append(item["description"][:180])

    lines.append(item["url"])
    return "\n".join(lines)


def format_instagram_item_message(post: dict, idx: int) -> str:
    return (
        f"📸 Creator Pick #{idx}\n"
        f"@{post['username']} — {post['caption']}\n"
        f"{post['url']}"
    )


def build_message_chunks(title_line: str, items: List[dict], paid: bool = False) -> List[str]:
    if not items:
        return []

    chunks = []
    current = f"{title_line}\n\n"

    for idx, item in enumerate(items, start=1):
        block = format_item_block(item, idx, paid=paid)

        if len(current) + len(block) > DISCORD_CHAR_LIMIT:
            chunks.append(current.rstrip())
            current = block
        else:
            current += block

    if current.strip():
        chunks.append(current.rstrip())

    return chunks
    
def build_instagram_chunks(posts: List[dict]) -> List[str]:
    title = "📸 **New Instagram Creator Picks**"
    chunks = []
    current = title + "\n\n"

    for idx, post in enumerate(posts, start=1):
        block = (
            f"{idx}. @{post['username']} — {post['caption']}\n"
            f"{post['url']}\n\n"
        )

        if len(current) + len(block) > DISCORD_CHAR_LIMIT:
            chunks.append(current.rstrip())
            current = title + "\n\n" + block
        else:
            current += block

    if current.strip():
        chunks.append(current.rstrip())

    return chunks

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


def post_to_discord_with_metadata(message: str, capture_metadata: bool = False) -> Optional[dict]:
    if not WEBHOOK_URL:
        raise RuntimeError("DISCORD_WEBHOOK_URL is not set.")

    webhook_url = WEBHOOK_URL
    if capture_metadata:
        separator = "&" if "?" in WEBHOOK_URL else "?"
        webhook_url = f"{WEBHOOK_URL}{separator}wait=true"

    response = requests.post(
        webhook_url,
        json={"content": message},
        headers={"Content-Type": "application/json"},
        timeout=30,
    )
    response.raise_for_status()

    if not capture_metadata:
        return None

    try:
        payload = response.json()
    except Exception as e:
        print(f"DISCORD METADATA PARSE FAILED: error={e}")
        return None

    message_id = payload.get("id")
    channel_id = payload.get("channel_id")
    if not message_id:
        return None

    return {
        "message_id": str(message_id),
        "channel_id": str(channel_id) if channel_id else None,
    }


def load_discord_daily_posts() -> Dict[str, dict]:
    if not os.path.exists(DISCORD_DAILY_POSTS_FILE):
        return {}

    try:
        with open(DISCORD_DAILY_POSTS_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except Exception as e:
        print(f"LOAD DISCORD DAILY POSTS FAILED: error={e}")
        return {}


def save_discord_daily_posts(data: Dict[str, dict]) -> None:
    data = prune_discord_daily_posts(data)
    with open(DISCORD_DAILY_POSTS_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, sort_keys=True)


def prune_discord_daily_posts(data: Dict[str, dict]) -> Dict[str, dict]:
    if len(data) <= DISCORD_DAILY_POSTS_RETENTION_DAYS:
        return data

    retained_keys = sorted(data.keys())[-DISCORD_DAILY_POSTS_RETENTION_DAYS:]
    retained_key_set = set(retained_keys)
    return {key: value for key, value in data.items() if key in retained_key_set}


def add_thumbs_up_reaction(channel_id: str, message_id: str) -> None:
    if not DISCORD_BOT_TOKEN:
        raise RuntimeError("DISCORD_BOT_TOKEN is not set.")

    reaction_url = (
        f"https://discord.com/api/v10/channels/{channel_id}/messages/{message_id}/"
        "reactions/%F0%9F%91%8D/@me"
    )
    response = requests.put(
        reaction_url,
        headers={
            "Authorization": f"Bot {DISCORD_BOT_TOKEN}",
            "Content-Type": "application/json",
        },
        timeout=30,
    )
    response.raise_for_status()


def record_posted_item(
    daily_posts: Dict[str, dict],
    day_key: str,
    section: str,
    title: str,
    url: str,
    source_type: str,
    message_id: str,
    channel_id: Optional[str],
) -> None:
    try:
        day_entry = daily_posts.setdefault(day_key, {"items": []})
        items = day_entry.get("items")
        if not isinstance(items, list):
            day_entry["items"] = []
            items = day_entry["items"]

        item_record = {
            "section": section,
            "title": title,
            "url": url,
            "message_id": message_id,
            "channel_id": channel_id,
            "source_type": source_type,
            "posted_at": utc_now_iso(),
        }
        items.append(item_record)
        save_discord_daily_posts(daily_posts)
    except Exception as e:
        print(f"RECORD DAILY DISCORD ITEM FAILED: title={title} | error={e}")


def post_message_chunks(chunks: List[str]) -> None:
    for chunk in chunks:
        post_to_discord(chunk)
        sleep_briefly()


def post_daily_pick_messages(free_items: List[dict], paid_items: List[dict], instagram_posts: List[dict]) -> None:
    if not (free_items or paid_items or instagram_posts):
        return

    token_available = bool(DISCORD_BOT_TOKEN)
    daily_posts = load_discord_daily_posts() if token_available else {}
    day_key = datetime.now(timezone.utc).date().isoformat()

    if not token_available:
        print("DISCORD_BOT_TOKEN missing; skipping auto-reactions and message-ID tracking.")

    post_to_discord("🎯 Daily Picks — vote with 👍 on your favorites")
    sleep_briefly()

    if free_items:
        post_to_discord("🎮 Free Picks")
        sleep_briefly()
        for idx, item in enumerate(free_items, start=1):
            metadata = post_to_discord_with_metadata(
                format_steam_item_message(item, idx, paid=False),
                capture_metadata=token_available,
            )
            if token_available:
                if metadata and metadata.get("message_id") and metadata.get("channel_id"):
                    try:
                        add_thumbs_up_reaction(metadata["channel_id"], metadata["message_id"])
                    except Exception as e:
                        print(f"ADD REACTION FAILED: title={item['title']} | error={e}")
                    record_posted_item(
                        daily_posts=daily_posts,
                        day_key=day_key,
                        section="free",
                        title=item["title"],
                        url=item["url"],
                        source_type="steam_free",
                        message_id=metadata["message_id"],
                        channel_id=metadata["channel_id"],
                    )
                else:
                    print(f"DISCORD MESSAGE METADATA MISSING: title={item['title']}")
            sleep_briefly()

    if paid_items:
        post_to_discord("💸 Paid Under $20")
        sleep_briefly()
        for idx, item in enumerate(paid_items, start=1):
            metadata = post_to_discord_with_metadata(
                format_steam_item_message(item, idx, paid=True),
                capture_metadata=token_available,
            )
            if token_available:
                if metadata and metadata.get("message_id") and metadata.get("channel_id"):
                    try:
                        add_thumbs_up_reaction(metadata["channel_id"], metadata["message_id"])
                    except Exception as e:
                        print(f"ADD REACTION FAILED: title={item['title']} | error={e}")
                    record_posted_item(
                        daily_posts=daily_posts,
                        day_key=day_key,
                        section="paid",
                        title=item["title"],
                        url=item["url"],
                        source_type="paid_under_20",
                        message_id=metadata["message_id"],
                        channel_id=metadata["channel_id"],
                    )
                else:
                    print(f"DISCORD MESSAGE METADATA MISSING: title={item['title']}")
            sleep_briefly()

    if instagram_posts:
        post_to_discord("📸 Instagram Creator Picks")
        sleep_briefly()
        for idx, post in enumerate(instagram_posts, start=1):
            metadata = post_to_discord_with_metadata(
                format_instagram_item_message(post, idx),
                capture_metadata=token_available,
            )
            if token_available:
                if metadata and metadata.get("message_id") and metadata.get("channel_id"):
                    try:
                        add_thumbs_up_reaction(metadata["channel_id"], metadata["message_id"])
                    except Exception as e:
                        print(f"ADD REACTION FAILED: username={post['username']} | error={e}")
                    record_posted_item(
                        daily_posts=daily_posts,
                        day_key=day_key,
                        section="instagram",
                        title=f"@{post['username']}",
                        url=post["url"],
                        source_type="instagram",
                        message_id=metadata["message_id"],
                        channel_id=metadata["channel_id"],
                    )
                else:
                    print(f"DISCORD MESSAGE METADATA MISSING: username={post['username']}")
            sleep_briefly()


def dedupe_by_app_id(items: List[Tuple[str, str]]) -> List[Tuple[str, str]]:
    seen = set()
    output = []
    for source, app_id in items:
        if app_id not in seen:
            seen.add(app_id)
            output.append((source, app_id))
    return output

def load_instagram_seen():
    if not os.path.exists(INSTAGRAM_STATE_FILE):
        return {}

    try:
        with open(INSTAGRAM_STATE_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def save_instagram_seen(data):
    with open(INSTAGRAM_STATE_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)


def fetch_instagram_posts():
    if instaloader is None:
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
    instagram_username = os.getenv("INSTAGRAM_USERNAME")
    session_file = "instaloader.session"

    if not instagram_username:
        print("INSTAGRAM_USERNAME missing; skipping Instagram")
        return []

    if not os.path.exists(session_file):
        print("instaloader.session missing; skipping Instagram")
        return []

    try:
        loader.load_session_from_file(instagram_username, session_file)
        print(f"Loaded Instagram session for {instagram_username}")
    except Exception as e:
        print(f"Instagram session load failed: {e}")
        return []

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
    print(f"Instagram posts found this run: {len(all_new_posts)}")
    return all_new_posts
    
def main():
    state = load_state()

    start_page, end_page = get_page_window()
    print(f"Current rotating page window: {start_page}-{end_page}")

    free_candidates = (
        collect_steam_free_candidates(start_page, end_page) +
        collect_steam_demo_candidates(start_page, end_page) +
        collect_steamdb_promo_candidates()
    )
    free_candidates = dedupe_by_app_id(free_candidates)

    paid_candidates = collect_paid_candidates(start_page, end_page)
    paid_candidates = dedupe_by_app_id(paid_candidates)

    print(f"Free candidates collected: {len(free_candidates)}")
    print(f"Paid candidates collected: {len(paid_candidates)}")

    qualified_free = []
    qualified_paid = []

    for source, app_id in free_candidates:
        item = inspect_game(source, app_id)
        sleep_briefly()

        if not item:
            continue
        if not item["keep"]:
            continue
        if not can_repost(app_id, item["type"], state):
            continue

        qualified_free.append(item)

    for source, app_id in paid_candidates:
        item = inspect_game(source, app_id)
        sleep_briefly()

        if not item:
            continue
        if item["type"] != "paid_under_20":
            continue
        if not item["keep"]:
            continue
        if not can_repost(app_id, item["type"], state):
            continue

        qualified_paid.append(item)

    qualified_free.sort(
        key=lambda x: (
            x["score"],
            x.get("review_score", 0),
            1 if x["type"] == "temporarily_free" else 0,
            1 if x["type"] == "demo" else 0,
        ),
        reverse=True
    )

    qualified_paid.sort(
        key=lambda x: (
            x["score"],
            x.get("review_score", 0),
        ),
        reverse=True
    )

    free_items = qualified_free[:MAX_FREE_POSTS]
    paid_items = qualified_paid[:MAX_PAID_POSTS]

    print(f"Qualified free items before cap: {len(qualified_free)}")
    print(f"Qualified paid items before cap: {len(qualified_paid)}")

    instagram_posts = fetch_instagram_posts()
    post_daily_pick_messages(free_items, paid_items, instagram_posts)

    if not free_items and not paid_items:
        print("No qualifying games found from Steam.")

    for item in free_items + paid_items:
        update_state_for_post(item["id"], item["type"], state)

    save_state(state)

    total = len(free_items) + len(paid_items)
    print(f"Posted {total} Steam item(s) to Discord.")
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

    next_start_page = get_next_start_page(start_page)
    save_page_state(next_start_page)
    next_end_page = min(next_start_page + PAGE_WINDOW_SIZE - 1, MAX_PAGE_LIMIT)
    print(f"Next rotating page window saved: {next_start_page}-{next_end_page}")

if __name__ == "__main__":
    main()
