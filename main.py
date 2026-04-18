import json
import os
import re
import time
import hashlib
from datetime import datetime, timedelta, timezone
from typing import Dict, List, Optional, Tuple, TypedDict
from urllib.parse import urlparse

import requests
from bs4 import BeautifulSoup
try:
    import instaloader
except ImportError:
    instaloader = None
from discord_api import DiscordClient, DiscordMessageNotFoundError
from daily_section_config import DAILY_SECTION_CONFIG, DAILY_SECTION_ORDER
from rolling_explainer import post_or_edit_rolling_explainer
from state_utils import (
    is_today_verified,
    load_json_object,
    prune_latest_iso_dates,
    save_json_object_atomic,
)

WEBHOOK_URL = os.getenv("DISCORD_WEBHOOK_URL")
DISCORD_BOT_TOKEN = os.getenv("DISCORD_BOT_TOKEN")
DISCORD_STEP1_CHANNEL_ID = os.getenv("DISCORD_STEP1_CHANNEL_ID")
DISCORD_GUILD_ID = os.getenv("DISCORD_GUILD_ID")
DISCORD_DEBUG_CHANNEL_ID = os.getenv("DISCORD_DEBUG_CHANNEL_ID")
DISCORD_HEALTH_MONITOR_WEBHOOK_URL = os.getenv("DISCORD_HEALTH_MONITOR_WEBHOOK_URL")
STATE_FILE = "seen_ids.json"
PAGE_STATE_FILE = "page_state.json"
INSTAGRAM_STATE_FILE = "instagram_seen.json"
INSTAGRAM_FETCH_SUMMARY_FILE = "instagram_fetch_summary.json"
DISCORD_DAILY_POSTS_FILE = "discord_daily_posts.json"
DISCORD_DAILY_POSTS_RETENTION_DAYS = 30
DAILY_DATE_OVERRIDE_ENV = "DAILY_DATE_UTC"
FORCE_REFRESH_SAME_DAY_ENV = "FORCE_REFRESH_SAME_DAY"
GITHUB_EVENT_NAME_ENV = "GITHUB_EVENT_NAME"
DAILY_DEBUG_SUMMARY_FILE = "daily_debug_summary.json"
DAILY_VERIFICATION_FILE = "daily_verification.json"
STOP_GO_RESULT_FILE = "daily_stop_go_result.json"
MAX_RETRY_ATTEMPTS = 3

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
INSTAGRAM_MAX_POST_AGE_DAYS = 7
INSTAGRAM_SEEN_RETENTION_PER_CREATOR = 50
INSTAGRAM_GAME_KEY_BOILERPLATE_PATTERNS = [
    r"\bdemo\b",
    r"\bplaytest\b",
    r"\bfree\b",
    r"\bsteam\b",
    r"\bwishlist\b",
    r"\bout\s+now\b",
    r"\blink\s+in\s+bio\b",
]
INSTAGRAM_GAME_KEY_FALLBACK_BOUNDARY_PATTERNS = [
    r"\bdemo\b",
    r"\bplaytest\b",
    r"\bfree\b",
    r"\bsteam\b",
    r"\bwishlist\b",
    r"\bout\s+now\b",
    r"\blink\s+in\s+bio\b",
]
INSTAGRAM_GAME_KEY_GENERIC_PREFIX_TOKENS = {
    "check",
    "this",
    "that",
    "out",
    "pick",
    "picks",
    "today",
    "game",
    "games",
    "new",
    "my",
    "our",
    "you",
}

HEADERS = {
    "User-Agent": "Mozilla/5.0"
}

# ---------- CONFIG ----------
# Edit-safely note: tune thresholds only after observing real production runs;
# avoid speculative changes that are not feedback-driven.
# Daily routing intent (do not redesign without explicit product change):
# - demo / playtest -> Demo & Playtest section (discovery lane for promising friend-group titles).
# - free_game / temporarily_free -> Free Picks (higher-confidence full free or temporary-free full games).
# - paid_under_20 -> Paid Under $20 (strictest paid recommendation lane).
# - Instagram creator picks remain a separate unchanged section.
# Threshold philosophy:
# - Demo/Playtest tolerates thinner review history, but demands stronger friend-group fit.
# - Free is stricter on review confidence.
# - Paid is strictest overall.
# Section caps are intentionally independent: Demo/Playtest picks are curated separately
# from Free Picks so one noisy pool cannot crowd out the other.
MAX_FREE_POSTS = 5
MAX_DEMO_PLAYTEST_POSTS = 5
MAX_PAID_POSTS = 5

PAGE_WINDOW_SIZE = 10
MAX_PAGE_LIMIT = 50

REQUEST_DELAY_SECONDS = 1.2
REPOST_COOLDOWN_DAYS = 30
# Threshold philosophy guardrails (quality-over-cap is intentional, especially for demos/playtests):
# - Demo/Playtest has lower review strictness but stronger friend-group gating.
# - Free raises review-confidence expectations.
# - Paid is strictest overall.
# These thresholds are intentionally conservative; do not tune further without
# first observing real Discord output over multiple runs.
MIN_SCORE_TO_POST_FREE = 11
MIN_SCORE_TO_POST_DEMO_PLAYTEST = 6
MIN_SCORE_TO_POST_PAID = 8

MAX_FETCH_RETRIES = 5
BACKOFF_SECONDS = 4

STEAM_FREE_SEARCH_URL = "https://store.steampowered.com/search/?maxprice=free&page={}"
STEAM_DEMO_SEARCH_URL = "https://store.steampowered.com/search/?category1=10&page={}"
STEAM_TOPSELLERS_URL = "https://store.steampowered.com/search/?filter=topsellers&page={}"
STEAMDB_FREE_PROMO_URL = "https://steamdb.info/upcoming/free/"

DISCORD_CHAR_LIMIT = 1900

FILTER_REASON_WEAK_REVIEW = "weak_review"
FILTER_REASON_WEAK_GROUP_FIT = "weak_group_fit"
FILTER_REASON_LOW_SIGNAL_JUNK = "low_signal_junk"
FILTER_REASON_REPOST_COOLDOWN = "repost_cooldown"
FILTER_REASON_BELOW_THRESHOLD = "below_threshold"
FILTER_REASON_QUALIFIED = "qualified"

MULTIPLAYER_TERMS = {
    "Massively Multiplayer": 6,
    "MMO": 4,
    "Online Co-Op": 3,
    "Online Co-op": 3,
    "Co-op": 2,
    "Co-Op": 2,
    "Multiplayer": 2,
    "Multi-player": 2,
    "Online PvP": 2,
    "PvP": 1,
    "Squad": 1,
    "Team-based": 1,
}

GOOD_GENRE_TERMS = {
    "Survival": 2,
    "Shooter": 2,
    "Action RPG": 2,
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
    "test server": -5,
    "dedicated server": -5,
    "server tools": -6,
    "wallpaper": -8,
    "art book": -8,
    "prologue": -3,
    "character creator": -4,
}

FRIEND_GROUP_PHRASE_SCORES = {
    "4-player co-op": 2,
    "4 player co-op": 2,
    "4-player online co-op": 2,
    "drop-in co-op": 1,
    "couch co-op": 2,
    "party game": 2,
    "cross-platform multiplayer": 1,
    "up to 6 players": 2,
    "up to 8 players": 2,
}

REPLAYABILITY_PHRASE_SCORES = {
    "procedurally generated": 1,
    "procedural": 1,
    "roguelite co-op": 1,
    "endless replayability": 1,
    "replayable": 1,
    "loot": 1,
    "runs": 1,
    "randomized": 1,
    "randomly generated": 1,
    "progression": 1,
}

DEMO_PLAYTEST_FRIEND_SIGNALS = {
    "online co-op": 4,
    "co-op": 3,
    "coop": 3,
    "multiplayer": 3,
    "party game": 3,
    "party": 2,
    "squad": 2,
    "team up": 2,
    "drop-in co-op": 2,
    "couch co-op": 2,
    "4-player co-op": 3,
    "4 player co-op": 3,
    "up to 4 players": 3,
    "up to 5 players": 3,
    "up to 6 players": 4,
    "up to 8 players": 4,
    "replayability": 1,
    "replayable": 1,
    "procedural": 1,
    "procedurally generated": 1,
    "randomly generated": 1,
    "loot": 1,
    "runs": 1,
    "progression": 1,
    "randomized": 1,
}

DEMO_PLAYTEST_SOLO_SIGNALS = {
    "single-player only": -6,
    "single player only": -6,
    "single-player": -3,
    "single player": -3,
    "solo": -2,
    "story-rich": -2,
    "narrative": -2,
    "visual novel": -4,
}

DEMO_PLAYTEST_MIN_FRIEND_SIGNAL = 5
# Quality-over-cap for Demo/Playtest: we prefer fewer stronger items over filling
# the entire section with borderline picks.
DEMO_PLAYTEST_QUALITY_FLOOR_SCORE = MIN_SCORE_TO_POST_DEMO_PLAYTEST + 1
# Diversity rerank is intentionally weak (light penalty only after a couple repeats)
# so quality remains primary and variety is just a soft tie-breaker.
LIGHT_DIVERSITY_PER_EXTRA_DUPLICATE = 1
LIGHT_DIVERSITY_DUPLICATE_FREE_SLOTS = 2

RELEASE_DATE_FORMATS = [
    "%b %d, %Y",
    "%B %d, %Y",
    "%d %b, %Y",
    "%d %B, %Y",
]

# Heavier penalties here are intentional to suppress low-signal/junk entries from
# leaking into daily picks despite keyword stuffing elsewhere on the page.
LOW_SIGNAL_KEYWORD_SCORES = {
    "clicker": -3,
    "idle": -2,
    "hentai": -4,
    "nsfw": -4,
    "prototype": -2,
    "vertical slice": -2,
    "proof of concept": -2,
    "test": -2,
    "placeholder": -2,
    "ai-generated": -2,
    "meme": -2,
    "asset flip": -4,
    "unity asset": -3,
}

DEMO_PLAYTEST_LEGIT_PLAYABLE_CUE_SCORES = {
    "download demo": 1,
    "demo available": 1,
    "playtest available": 1,
    "request access": 1,
    "join playtest": 1,
    "play now": 1,
}

VR_INDICATOR_PHRASES = [
    "virtual reality",
    "vr headset",
    "requires vr",
    "requires a vr",
    "requires virtual reality",
    "supports vr",
    "play in vr",
    "vr only",
    "vr supported",
    "oculus rift",
    "oculus quest",
    "htc vive",
    "valve index",
    "steam vr",
    "steamvr",
    "vr game",
    "vr experience",
    "mixed reality",
    "windows mixed reality",
]

VR_TAG_EXACT = {"vr", "virtual reality", "steamvr", "room-scale vr", "vr only", "vr supported"}

TITLE_LOW_SIGNAL_KEYWORD_SCORES = {
    "simulator": -1,
    "clicker": -2,
    "idle": -2,
    "prototype": -2,
    "test": -1,
}

PLAYER_COUNT_PATTERNS = [
    (r"\b1\s*-\s*4\b", 3),
    (r"\b1\s*-\s*6\b", 4),
    (r"\b1\s*-\s*8\b", 5),
    (r"\b2\s*-\s*4\b", 3),
    (r"\b2\s*-\s*6\b", 4),
    (r"\b2\s*-\s*8\b", 5),
    (r"\b3\s*\+\b", 4),
    (r"\b3\s*-\s*4\b", 3),
    (r"\b3\s*-\s*6\b", 4),
    (r"\b3\s*-\s*8\b", 5),
    (r"\b4\s*player\b", 3),
    (r"\b4\s*players\b", 3),
    (r"\bup to 4 players\b", 3),
    (r"\bup to 6 players\b", 4),
    (r"\bup to 8 players\b", 5),
]

REVIEW_SENTIMENT_SCORES = {
    "Overwhelmingly Positive": 6,
    "Very Positive": 5,
    "Positive": 4,
    "Mostly Positive": -1,
    "Mixed": -3,
    "Mostly Negative": -8,
    "Negative": -8,
    "Very Negative": -10,
    "Overwhelmingly Negative": -12,
}

# Hard-excluded from all sections regardless of other scores.
# Exception: demos/playtests with no reviews are handled separately (review_sentiment is None).
HARD_EXCLUDE_REVIEW_SENTIMENTS = {
    "Mostly Negative",
    "Very Negative",
    "Overwhelmingly Negative",
    "Negative",
}

REVIEW_SENTIMENT_PATTERNS = [
    "Overwhelmingly Positive",
    "Very Positive",
    "Mostly Positive",
    "Positive",
    "Mixed",
    "Overwhelmingly Negative",
    "Very Negative",
    "Mostly Negative",
    "Negative",
]

FREE_REVIEW_BLOCKLIST = {
    "Mostly Positive",
    "Mostly Negative",
    "Negative",
    "Very Negative",
    "Overwhelmingly Negative",
}

PAID_MINIMUM_REVIEW_SENTIMENTS = {
    "Positive",
    "Very Positive",
    "Overwhelmingly Positive",
}

TEMPORARILY_FREE_SCORE_BONUS = 1
DEMO_SCORE_PENALTY = 2

UNKNOWN_REVIEW_SCORE_BY_TYPE = {
    "free_game": -2,
    "demo": -1,
    "playtest": 0,
    "temporarily_free": -2,
    "paid_under_20": -6,
}

STRONG_REVIEW_SENTIMENTS = {"Very Positive", "Overwhelmingly Positive"}
POSITIVE_OR_BETTER_REVIEW_SENTIMENTS = {
    "Positive",
    "Mostly Positive",
    "Very Positive",
    "Overwhelmingly Positive",
}
REVIEW_CONFIDENCE_BASELINE_SENTIMENTS = {
    "Positive",
    "Mostly Positive",
    "Very Positive",
    "Overwhelmingly Positive",
}
REVIEW_CONFIDENCE_STRONG_SENTIMENTS = {"Very Positive", "Overwhelmingly Positive"}

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


def _notify_health_monitor(message: str) -> None:
    """Post a warning to the Discord health monitor webhook (best-effort, never raises)."""
    url = DISCORD_HEALTH_MONITOR_WEBHOOK_URL
    if not url:
        return
    try:
        requests.post(url, json={"content": message}, timeout=10)
    except Exception:
        pass


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


def collect_steam_free_candidates(start_page: int, end_page: int, scraping_stats: Optional[dict] = None) -> List[Tuple[str, str]]:
    results = []
    seen = set()

    for page in range(start_page, end_page + 1):
        url = STEAM_FREE_SEARCH_URL.format(page)
        html = safe_fetch_html(url)
        if not html:
            if scraping_stats is not None:
                scraping_stats["fail"] = scraping_stats.get("fail", 0) + 1
            continue
        if scraping_stats is not None:
            scraping_stats["ok"] = scraping_stats.get("ok", 0) + 1

        page_ids = extract_appids_from_html(html, from_search_results=True)
        print(f"FREE PAGE {page}: extracted {len(page_ids)} app ids")

        for app_id in page_ids:
            key = ("steam_free", app_id)
            if key not in seen:
                seen.add(key)
                results.append(("steam_free", app_id))

        sleep_briefly()

    return results


def collect_steam_demo_candidates(start_page: int, end_page: int, scraping_stats: Optional[dict] = None) -> List[Tuple[str, str]]:
    results = []
    seen = set()

    for page in range(start_page, end_page + 1):
        url = STEAM_DEMO_SEARCH_URL.format(page)
        html = safe_fetch_html(url)
        if not html:
            if scraping_stats is not None:
                scraping_stats["fail"] = scraping_stats.get("fail", 0) + 1
            continue
        if scraping_stats is not None:
            scraping_stats["ok"] = scraping_stats.get("ok", 0) + 1

        page_ids = extract_appids_from_html(html, from_search_results=True)
        print(f"DEMO PAGE {page}: extracted {len(page_ids)} app ids")

        for app_id in page_ids:
            key = ("steam_demo", app_id)
            if key not in seen:
                seen.add(key)
                results.append(("steam_demo", app_id))

        sleep_briefly()

    return results


def collect_paid_candidates(start_page: int, end_page: int, scraping_stats: Optional[dict] = None) -> List[Tuple[str, str]]:
    results = []
    seen = set()

    for page in range(start_page, end_page + 1):
        url = STEAM_TOPSELLERS_URL.format(page)
        html = safe_fetch_html(url)
        if not html:
            if scraping_stats is not None:
                scraping_stats["fail"] = scraping_stats.get("fail", 0) + 1
            continue
        if scraping_stats is not None:
            scraping_stats["ok"] = scraping_stats.get("ok", 0) + 1

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


def extract_steam_tags(soup: BeautifulSoup) -> List[str]:
    tags = []
    for tag_el in soup.select(".glance_tags.popular_tags a.app_tag"):
        text = clean_text(tag_el.get_text(" ", strip=True))
        if text:
            tags.append(text.lower())
    return tags


def is_vr_content(title: str, description: str, text: str, tags: List[str]) -> bool:
    lower_combined = normalize_text_lower(title, description, text)
    if any(phrase in lower_combined for phrase in VR_INDICATOR_PHRASES):
        return True

    for tag in tags:
        if tag in VR_TAG_EXACT:
            return True
        if any(phrase in tag for phrase in VR_INDICATOR_PHRASES):
            return True
        if tag == "vr" or tag.startswith("vr ") or tag.endswith(" vr"):
            return True
    return False


def get_price_info(app_id: str) -> Tuple[Optional[float], bool, Optional[int], Optional[str]]:
    try:
        url = (
            f"https://store.steampowered.com/api/appdetails"
            f"?appids={app_id}&cc=us"
        )
        response = requests.get(url, headers=HEADERS, timeout=30)
        response.raise_for_status()
        data = response.json()

        app_data = data.get(str(app_id), {})
        if not app_data.get("success"):
            return None, False, None, None

        info = app_data.get("data", {})

        if info.get("is_free") is True:
            return 0.0, True, None, info.get("type")

        price_info = info.get("price_overview")
        if not price_info:
            return None, False, None, info.get("type")

        final_price_cents = price_info.get("final")
        if final_price_cents is None:
            return None, False, None, info.get("type")

        discount_percent = price_info.get("discount_percent", 0)
        return round(final_price_cents / 100.0, 2), False, discount_percent, info.get("type")
    except Exception as e:
        print(f"PRICE API FAILED: app_id={app_id} | error={e}")
        return None, False, None, None


def detect_item_type(source: str, app_id: str, title: str, text: str) -> str:
    lower_title = title.lower()
    lower_text = text.lower()

    if source == "steamdb_promo" or "free to keep" in lower_text or "100% off" in lower_text:
        return "temporarily_free"

    # Paid candidates are only ever evaluated as paid_under_20 — skip demo/playtest paths.
    if source != "paid_candidate":
        if "playtest" in lower_title or "playtest" in lower_text:
            # Verify the app is actually free — paid games sometimes mention
            # "playtest" in their page text without offering a free trial.
            price, is_free, _, _ = get_price_info(app_id)
            if price is not None and price > 0 and not is_free:
                print(f"DEMO/PLAYTEST PAID SKIP: app_id={app_id} | price={price} | classified as playtest but is paid")
                return "ignore"
            return "playtest"

        if source == "steam_demo" or "demo" in lower_title or "demo" in lower_text:
            # For non-steam_demo sources, verify the app is actually free before
            # classifying as demo — paid games can mention "demo" anywhere in page text.
            if source != "steam_demo":
                price, is_free, _, _ = get_price_info(app_id)
                if price is not None and price > 0 and not is_free:
                    print(f"DEMO/PLAYTEST PAID SKIP: app_id={app_id} | price={price} | classified as demo but is paid")
                    return "ignore"
            return "demo"

    if source == "paid_candidate":
        price, is_free, _, item_type = get_price_info(app_id)

        if item_type == "dlc":
            return "ignore"

        if is_free:
            return "ignore"

        if price is not None and 0 < price <= 20:
            return "paid_under_20"

        return "ignore"

    return "free_game"


def normalize_text_lower(*parts: str) -> str:
    return " ".join(part.strip().lower() for part in parts if isinstance(part, str) and part.strip())


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
        score += 4
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

    lower_combined = normalize_text_lower(title, description, text)

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


def has_4plus_player_signal(text: str) -> bool:
    patterns = [
        r"\b4\s*player\b",
        r"\b4\s*players\b",
        r"\b5\s*player\b",
        r"\b5\s*players\b",
        r"\b6\s*player\b",
        r"\b6\s*players\b",
        r"\bup to 4 players\b",
        r"\bup to 5 players\b",
        r"\bup to 6 players\b",
        r"\bup to 8 players\b",
        r"\b1\s*-\s*4\b",
        r"\b1\s*-\s*6\b",
        r"\b2\s*-\s*4\b",
        r"\b2\s*-\s*6\b",
        r"\b3\s*-\s*4\b",
        r"\b3\s*-\s*6\b",
        r"\b4\s*\+\b",
    ]
    return any(re.search(pattern, text, re.IGNORECASE) for pattern in patterns)


def extract_review_count(page_text: str) -> int:
    match = re.search(r"([\d,]+)\s+(?:user\s+)?reviews?\b", page_text, re.IGNORECASE)
    if not match:
        return 0
    try:
        return int(match.group(1).replace(",", ""))
    except ValueError:
        return 0


def extract_release_date(page_text: str) -> Optional[datetime]:
    match = re.search(
        r"Release Date:\s*([A-Za-z]{3,9}\s+\d{1,2},\s+\d{4})",
        page_text,
        re.IGNORECASE,
    )
    if not match:
        return None
    raw = clean_text(match.group(1))
    for fmt in RELEASE_DATE_FORMATS:
        try:
            return datetime.strptime(raw, fmt).replace(tzinfo=timezone.utc)
        except ValueError:
            continue
    return None


# Recency bonus tiers for free and paid games.
RECENCY_BONUS_TIERS = [
    (7, 6),
    (30, 4),
    (90, 2),
    (180, 1),
]

# Ramped recency bonus tiers for demos and playtests (higher than standard).
DEMO_PLAYTEST_RECENCY_BONUS_TIERS = [
    (7, 10),
    (30, 7),
    (90, 3),
    (180, 1),
]


def score_recency_bonus(page_text: str, tiers=None) -> Tuple[int, List[str]]:
    """Return a recency bonus based on release date age, using the provided tier set."""
    if tiers is None:
        tiers = RECENCY_BONUS_TIERS
    release_date = extract_release_date(page_text)
    if release_date is None:
        return 0, []
    age_days = (datetime.now(timezone.utc) - release_date).days
    for threshold, bonus in tiers:
        if age_days <= threshold:
            return bonus, [f"recency:{bonus}(age={age_days}d)"]
    return 0, []


def score_demo_playtest_friend_group_fit(
    title: str,
    description: str,
    text: str,
    review_sentiment: Optional[str],
    review_count: int,
) -> Tuple[int, int, int, List[str]]:
    score = 0
    friend_signal_score = 0
    freshness_bonus = 0
    hits: List[str] = []

    combined = f"{title} {description} {text}".lower()
    for phrase, points in DEMO_PLAYTEST_FRIEND_SIGNALS.items():
        if phrase in combined:
            score += points
            friend_signal_score += points
            hits.append(f"friend:{phrase}")

    for phrase, points in DEMO_PLAYTEST_SOLO_SIGNALS.items():
        if phrase in combined:
            score += points
            hits.append(f"solo:{phrase}")

    for phrase, points in DEMO_PLAYTEST_LEGIT_PLAYABLE_CUE_SCORES.items():
        if phrase in combined:
            score += points
            hits.append(f"playable-cue:{phrase}")

    has_coop_or_mp = (
        "co-op" in combined
        or "coop" in combined
        or "multiplayer" in combined
        or "massively multiplayer" in combined
    )
    if has_coop_or_mp:
        friend_signal_score += 1
        score += 1
        hits.append("friend:coop-or-mp")

    if has_4plus_player_signal(text):
        friend_signal_score += 2
        score += 2
        hits.append("friend:4plus")

    if review_sentiment in FREE_REVIEW_BLOCKLIST:
        score -= 4
        hits.append("review:negative")
    elif review_sentiment in POSITIVE_OR_BETTER_REVIEW_SENTIMENTS and review_count >= 100:
        score += 1
        hits.append("review:positive")

    release_date = extract_release_date(text)
    if release_date:
        age_days = (datetime.now(timezone.utc) - release_date).days
        if age_days <= 14:
            freshness_bonus = 2
        elif age_days <= 45:
            freshness_bonus = 1
        if freshness_bonus > 0:
            score += freshness_bonus
            hits.append(f"freshness:{freshness_bonus}")

    return score, friend_signal_score, freshness_bonus, hits


def has_demo_playtest_free_to_try_signal(title: str, description: str, text: str) -> bool:
    combined = f"{title} {description} {text}".lower()
    return any(phrase in combined for phrase in DEMO_PLAYTEST_LEGIT_PLAYABLE_CUE_SCORES)


# Phrases in Steam page text that indicate a demo/playtest is not yet playable.
DEMO_NOT_YET_AVAILABLE_PHRASES = [
    "coming soon",
    "not yet available",
    "available soon",
    "wishlist now",
    "notify me when available",
]


def is_demo_not_yet_available(page_text: str, soup: "BeautifulSoup") -> tuple[bool, str]:
    """Return (True, reason) if a demo/playtest page indicates it is not yet playable.

    Checks (in order):
    1. Future release date
    2. Steam's dedicated coming-soon HTML elements (#game_area_comingsoon, .coming_soon)
    3. 'Coming Soon' or similar phrases in the purchase/action block only
    """
    # 1. Future release date
    release_date = extract_release_date(page_text)
    if release_date is not None and release_date > datetime.now(timezone.utc):
        days_until = (release_date - datetime.now(timezone.utc)).days
        return True, f"release_date={release_date.date().isoformat()} is in the future ({days_until}d)"

    # 2. Steam's dedicated coming-soon elements (unambiguous structural signals)
    for selector in ("#game_area_comingsoon", ".coming_soon", "#coming_soon_text"):
        if soup.select_one(selector):
            return True, f"Steam coming-soon element '{selector}' present"

    # 3. Purchase/action block text (targeted — not a broad page-text scan)
    for selector in (".game_purchase_action", ".game_area_purchase_game"):
        block = soup.select_one(selector)
        if block:
            block_text = block.get_text(" ", strip=True).lower()
            for phrase in DEMO_NOT_YET_AVAILABLE_PHRASES:
                if phrase in block_text:
                    return True, f"purchase_block contains '{phrase}'"

    return False, ""


def extract_diversity_tags(title: str, description: str, text: str) -> List[str]:
    combined = f"{title} {description} {text}".lower()
    tag_terms = (
        "survival",
        "crafting",
        "shooter",
        "roguelike",
        "roguelite",
        "party",
        "extraction",
    )
    return [term for term in tag_terms if term in combined]


def apply_light_diversity_rerank(items: List[dict]) -> List[dict]:
    # Diversity rerank is intentionally weaker than base quality scoring:
    # it only nudges away from near-identical same-day picks and should never
    # overpower stronger underlying scores.
    if len(items) <= 2:
        return items

    remaining = list(items)
    selected: List[dict] = []
    tag_counts: Dict[str, int] = {}

    while remaining:
        best_item = None
        best_adjusted_score = None
        for item in remaining:
            penalty = 0
            for tag in item.get("diversity_tags", []):
                extra = max(0, tag_counts.get(tag, 0) - LIGHT_DIVERSITY_DUPLICATE_FREE_SLOTS + 1)
                penalty += extra * LIGHT_DIVERSITY_PER_EXTRA_DUPLICATE
            adjusted_score = item["score"] - penalty
            if best_adjusted_score is None or adjusted_score > best_adjusted_score:
                best_item = item
                best_adjusted_score = adjusted_score

        assert best_item is not None
        picked = dict(best_item)
        picked["diversity_penalty"] = picked["score"] - int(best_adjusted_score)
        selected.append(picked)
        for tag in best_item.get("diversity_tags", []):
            tag_counts[tag] = tag_counts.get(tag, 0) + 1
        remaining.remove(best_item)

    return selected


def select_demo_playtest_items(qualified_demo_playtest: List[dict], cap: int) -> List[dict]:
    # Explicit quality-over-cap behavior:
    # for demo/playtest we intentionally prefer posting fewer strong picks over
    # filling the cap with weak borderline items.
    strong_items = [item for item in qualified_demo_playtest if item["score"] >= DEMO_PLAYTEST_QUALITY_FLOOR_SCORE]
    if strong_items:
        return strong_items[:cap]
    return qualified_demo_playtest[:cap]


def build_keep_debug_context(item: dict) -> str:
    keep_parts = [
        f"keep={item['keep']}",
        f"rejected={item.get('rejected', False)}",
        f"review_gate_failed={item.get('review_gate_failed', False)}",
    ]
    if item["type"] in {"demo", "playtest"}:
        keep_parts.append(f"friend_signal={item.get('demo_friend_signal_score', 0)}")
    return ", ".join(keep_parts)


def log_candidate_decision(item: dict, phase: str) -> None:
    if item["type"] in {"demo", "playtest"}:
        print(
            f"DEMO_CANDIDATE[{phase}]: {item['title']} | type={item['type']} "
            f"| score={item['score']} | sentiment={item.get('review_sentiment')} "
            f"| reviews={item.get('review_count', 0)} | friend_signal={item.get('demo_friend_signal_score', 0)} "
            f"| freshness={item.get('demo_freshness_bonus', 0)} | refinement_hits={item.get('refinement_hits', [])[:4]} "
            f"| demo_hits={item.get('demo_hits', [])[:4]} | {build_keep_debug_context(item)}"
        )
        return

    print(
        f"CANDIDATE[{phase}]: {item['title']} | type={item['type']} | score={item['score']} "
        f"| sentiment={item.get('review_sentiment')} | reviews={item.get('review_count', 0)} "
        f"| {build_keep_debug_context(item)}"
    )


def _is_filtered_for_weak_group_fit(item: dict) -> bool:
    item_type = item.get("type")
    if item.get("rejected"):
        return True
    if item_type in {"free_game", "temporarily_free"}:
        return not item.get("multiplayer_hits") or not item.get("player_hits")
    if item_type in {"demo", "playtest"}:
        return item.get("demo_friend_signal_score", 0) < DEMO_PLAYTEST_MIN_FRIEND_SIGNAL
    if item_type == "paid_under_20":
        return not item.get("multiplayer_hits")
    return False


def _is_filtered_as_low_signal_junk(item: dict) -> bool:
    for hit in item.get("refinement_hits", []):
        if hit.startswith("low-signal:") or hit.startswith("title-low-signal:"):
            return True
    return False


class DebugRecord(TypedDict):
    title: str
    type: str
    final_score: int
    review_sentiment: Optional[str]
    friend_group_signal: int
    keep: bool
    reason_list: List[str]


def build_filter_reason_list(item: dict) -> List[str]:
    reasons: List[str] = []
    if item.get("review_gate_failed"):
        reasons.append(FILTER_REASON_WEAK_REVIEW)
    if _is_filtered_for_weak_group_fit(item):
        reasons.append(FILTER_REASON_WEAK_GROUP_FIT)
    if _is_filtered_as_low_signal_junk(item):
        reasons.append(FILTER_REASON_LOW_SIGNAL_JUNK)
    return reasons


ITEM_TYPE_TO_DAILY_SECTION = {
    "demo": "demo_playtest",
    "playtest": "demo_playtest",
    "free_game": "free",
    "temporarily_free": "free",
    "paid_under_20": "paid",
}


def route_item_to_daily_section(item_type: str) -> Optional[str]:
    return ITEM_TYPE_TO_DAILY_SECTION.get(item_type)


def build_run_summary(
    *,
    steam_candidates_scanned: int,
    demo_playtest_candidates_qualified: int,
    free_candidates_qualified: int,
    paid_candidates_qualified: int,
    demo_playtest_posted: int,
    free_posted: int,
    paid_posted: int,
    filtered_weak_reviews: int,
    filtered_weak_group_fit: int,
    filtered_low_signal_junk: int,
    filtered_repost_cooldown: int,
    top_filter_reasons: Optional[List[Tuple[str, int]]] = None,
    selected_title_samples: Optional[Dict[str, List[str]]] = None,
) -> List[str]:
    lines = [
        "RUN SUMMARY",
        f"- Steam candidates scanned: {steam_candidates_scanned}",
        f"- Demo/playtest candidates qualified: {demo_playtest_candidates_qualified}",
        f"- Free candidates qualified: {free_candidates_qualified}",
        f"- Paid candidates qualified: {paid_candidates_qualified}",
        f"- Demo/playtest posted: {demo_playtest_posted}",
        f"- Free posted: {free_posted}",
        f"- Paid posted: {paid_posted}",
        f"- Filtered for weak reviews: {filtered_weak_reviews}",
        f"- Filtered for weak multiplayer/friend-group fit: {filtered_weak_group_fit}",
        f"- Filtered as junk/prototype/low-signal: {filtered_low_signal_junk}",
        f"- Filtered by repost cooldown: {filtered_repost_cooldown}",
    ]
    for index, (reason, count) in enumerate(top_filter_reasons or []):
        rank = ["Top", "Second", "Third"][index] if index < 3 else f"#{index + 1}"
        lines.append(f"- {rank} filter reason: {reason} ({count})")
    if selected_title_samples:
        lines.append("- Selected by section (sample):")
        for section in ("demo_playtest", "free", "paid"):
            titles = selected_title_samples.get(section, [])
            label = next((cfg["message_label"] for cfg in DAILY_SECTION_CONFIG if cfg["key"] == section), section)
            display = ", ".join(titles) if titles else "none"
            lines.append(f"  - {label}: {display}")
    return lines


def export_daily_debug_summary(
    records: List[dict],
    run_summary_lines: List[str],
    path: str = DAILY_DEBUG_SUMMARY_FILE,
    target_day_key: Optional[str] = None,
    instagram_debug: Optional[Dict[str, object]] = None,
) -> None:
    # Intentionally ephemeral troubleshooting artifact: overwritten each run.
    payload = {
        "generated_at_utc": utc_now_iso(),
        "target_day_key": target_day_key or get_target_day_key(),
        "section_order": DAILY_SECTION_ORDER,
        "run_summary": run_summary_lines,
        "instagram_debug": instagram_debug or {},
        "records": records,
    }
    try:
        with open(path, "w", encoding="utf-8") as f:
            json.dump(payload, f, indent=2, ensure_ascii=False)
        print(f"DEBUG EXPORT: wrote {path} ({len(records)} records)")
    except Exception as e:
        print(f"WARN: failed to write debug summary ({path}): {e}")


def export_verification_artifact(
    day_key: str,
    run_counts: Dict[str, int],
    rerun_protection_active: bool,
    verification_state: Optional[dict] = None,
    path: str = DAILY_VERIFICATION_FILE,
    pages_fetched_successfully: int = 0,
    pages_failed: int = 0,
    scraping_status: str = "ok",
) -> None:
    """Write a JSON verification artifact summarizing the daily workflow run outcome."""
    vs = verification_state or {}
    run_state = vs.get("run_state") if isinstance(vs.get("run_state"), dict) else {}
    items = vs.get("items") if isinstance(vs.get("items"), list) else []
    posted_section_keys = vs.get("posted_section_keys") if isinstance(vs.get("posted_section_keys"), list) else []

    intro_state = run_state.get("intro") if isinstance(run_state.get("intro"), dict) else {}
    intro_present = bool(intro_state.get("message_id"))
    intro_count = 1 if intro_present else 0

    section_headers_state = run_state.get("section_headers") if isinstance(run_state.get("section_headers"), dict) else {}
    section_header_counts = {
        sk: (1 if isinstance(v, dict) and v.get("message_id") else 0)
        for sk, v in section_headers_state.items()
    }

    item_count = len(items)
    key_seq = [item.get("item_key") for item in items if isinstance(item, dict) and item.get("item_key")]
    seen_keys: set = set()
    dup_keys: List[str] = []
    for k in key_seq:
        if k in seen_keys and k not in dup_keys:
            dup_keys.append(k)
        seen_keys.add(k)
    duplicate_item_keys = dup_keys

    footer_state = run_state.get("footer") if isinstance(run_state.get("footer"), dict) else {}
    footer_present = bool(footer_state.get("message_id"))

    skipped = run_counts.get("skipped", 0)

    if rerun_protection_active:
        # Run was intentionally skipped; structural checks belong to the completing run.
        passes = skipped == 0
    else:
        footer_expected = bool(posted_section_keys) and bool(DISCORD_GUILD_ID)
        footer_ok = footer_present if footer_expected else True
        headers_ok = all(
            sk in posted_section_keys
            for sk, count in section_header_counts.items()
            if count > 0
        )
        passes = (
            intro_count == 1
            and not duplicate_item_keys
            and footer_ok
            and headers_ok
            and skipped == 0
        )

    artifact = {
        "day_key": day_key,
        "created": run_counts.get("created", 0),
        "updated": run_counts.get("updated", 0),
        "reused": run_counts.get("reused", 0),
        "skipped": skipped,
        "rerun_protection_active": rerun_protection_active,
        "intro_present": intro_present,
        "intro_count": intro_count,
        "section_header_counts": section_header_counts,
        "item_count": item_count,
        "duplicate_item_keys": duplicate_item_keys,
        "footer_present": footer_present,
        "posted_section_keys": posted_section_keys,
        "pass": passes,
        "generated_at_utc": utc_now_iso(),
        "pages_fetched_successfully": pages_fetched_successfully,
        "pages_failed": pages_failed,
        "scraping_status": scraping_status,
    }
    try:
        with open(path, "w", encoding="utf-8") as f:
            json.dump(artifact, f, indent=2, ensure_ascii=False)
        print(f"VERIFICATION: wrote {path}")
    except Exception as e:
        print(f"WARN: failed to write verification artifact ({path}): {e}")


def post_discord_debug_summary(
    day_key: str,
    run_counts: Dict[str, int],
    rerun_protection_active: bool,
    force_refresh_same_day: bool,
) -> None:
    """Post a compact debug summary message to DISCORD_DEBUG_CHANNEL_ID after a daily workflow run."""
    if not DISCORD_DEBUG_CHANNEL_ID or not DISCORD_BOT_TOKEN:
        return
    try:
        day_label = datetime.strptime(day_key, "%Y-%m-%d").strftime("%Y-%m-%d (%a)")
    except ValueError:
        day_label = day_key
    created = run_counts.get("created", 0)
    updated = run_counts.get("updated", 0)
    reused = run_counts.get("reused", 0)
    skipped = run_counts.get("skipped", 0)
    lines = [
        f"🛠 Debug | Daily Picks | {day_label}",
        f"Created: {created} | Updated: {updated} | Reused: {reused} | Skipped: {skipped}",
        f"Rerun protection: {'active' if rerun_protection_active else 'inactive'}"
        + (" | Force refresh: on" if force_refresh_same_day else ""),
    ]
    content = "\n".join(lines)
    try:
        session = requests.Session()
        session.headers.update({
            "Authorization": f"Bot {DISCORD_BOT_TOKEN}",
            "Content-Type": "application/json",
        })
        client = DiscordClient(session)
        client.post_message(DISCORD_DEBUG_CHANNEL_ID, content, context="daily picks debug summary")
        print(f"DEBUG SUMMARY: posted to channel {DISCORD_DEBUG_CHANNEL_ID}")
    except Exception as e:
        print(f"WARN: failed to post debug summary to Discord: {e}")


def score_quality_refinements(
    title: str,
    description: str,
    text: str,
    review_sentiment: Optional[str],
    review_count: int,
    multiplayer_score: int,
    player_score: int,
) -> Tuple[int, List[str]]:
    score = 0
    hits = []
    lower_title = title.lower()
    lower_text = text.lower()
    combined = normalize_text_lower(title, description, text)

    strong_review = review_sentiment in STRONG_REVIEW_SENTIMENTS
    positive_or_better = review_sentiment in POSITIVE_OR_BETTER_REVIEW_SENTIMENTS
    has_4plus = has_4plus_player_signal(text)
    has_coop = "co-op" in lower_text or "co op" in lower_text or "online co-op" in lower_text
    has_pvp = "pvp" in lower_text or "online pvp" in lower_text
    strong_multiplayer = multiplayer_score >= 4 or player_score >= 3

    if strong_review and has_4plus:
        score += 2
        hits.append("strong-review-4plus-combo")

    if review_count >= 10000 and review_sentiment in REVIEW_CONFIDENCE_BASELINE_SENTIMENTS:
        score += 2
        hits.append("review-count-10k")
    elif review_count >= 1000 and review_sentiment in REVIEW_CONFIDENCE_BASELINE_SENTIMENTS:
        score += 1
        hits.append("review-count-1k")

    for phrase, points in FRIEND_GROUP_PHRASE_SCORES.items():
        if phrase in combined:
            score += points
            hits.append(f"group:{phrase}")

    for phrase, points in REPLAYABILITY_PHRASE_SCORES.items():
        if phrase in combined:
            score += points
            hits.append(f"replay:{phrase}")

    if "single-player" in combined and multiplayer_score <= 2:
        score -= 2
        hits.append("single-player-with-weak-mp")

    for keyword, points in LOW_SIGNAL_KEYWORD_SCORES.items():
        if keyword in combined:
            score += points
            hits.append(f"low-signal:{keyword}")

    for keyword, points in TITLE_LOW_SIGNAL_KEYWORD_SCORES.items():
        if keyword in lower_title:
            score += points
            hits.append(f"title-low-signal:{keyword}")

    story_terms = ["visual novel", "dating sim", "interactive fiction", "story-rich", "narrative"]
    if not strong_multiplayer:
        for term in story_terms:
            if term in combined:
                score -= 2
                hits.append(f"story:{term}")

    if "early access" in combined and not strong_review:
        score -= 2
        hits.append("early-access")

    if has_coop:
        score += 1
        hits.append("coop-preference")
    if has_pvp and not has_coop:
        score -= 1
        hits.append("pvp-only-penalty")

    if has_4plus and any(token in combined for token in ["4 players", "4-player", "up to 4 players", "up to 5 players", "up to 6 players", "5 players", "6 players"]):
        score += 1
        hits.append("4to6-player-preference")

    if multiplayer_score >= 6 and review_sentiment == "Mixed":
        score -= 2
        hits.append("keyword-stuffing-weak-review")

    trusted_genres = ["survival", "shooter", "roguelike", "roguelite", "party", "extraction"]
    if strong_review and has_4plus and has_coop and any(term in combined for term in trusted_genres):
        score += 2
        hits.append("trusted-profile")

    return score, hits


def extract_review_sentiment(soup: BeautifulSoup) -> Optional[str]:
    page_text = soup.get_text(" ", strip=True)

    for sentiment in REVIEW_SENTIMENT_PATTERNS:
        if sentiment in page_text:
            return sentiment

    return None


def extract_review_score(soup: BeautifulSoup) -> int:
    sentiment = extract_review_sentiment(soup)
    if sentiment is None:
        return 0

    return REVIEW_SENTIMENT_SCORES[sentiment]


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
    steam_tags = extract_steam_tags(soup)

    if is_vr_content(title, description, page_text, steam_tags):
        print(f"VR GAME DETECTED: {title}")
        return None

    item_type = detect_item_type(source, app_id, title, page_text)

    if source == "paid_candidate":
        price, is_free, discount_percent, api_type = get_price_info(app_id)
        print(f"PAID CHECK: {title} | price={price} | is_free={is_free} | discount={discount_percent}% | api_type={api_type} | item_type={item_type}")

    if item_type == "ignore":
        return None

    # Exclude demos/playtests that are not yet available to play.
    if item_type in {"demo", "playtest"}:
        not_available, reason = is_demo_not_yet_available(page_text, soup)
        if not_available:
            print(f"DEMO NOT YET AVAILABLE: {title} | excluded: demo not yet available to play | reason={reason}")
            return None
        # Hard age cutoff: demos/playtests older than 180 days are excluded.
        _release_date = extract_release_date(page_text)
        if _release_date is None or (datetime.now(timezone.utc) - _release_date).days > 180:
            print(f"EXCLUDE: {title} — demo/playtest older than 180 days (or no release date)")
            return None

    review_sentiment = extract_review_sentiment(soup)

    if item_type in {"demo", "playtest"}:
        if review_sentiment in HARD_EXCLUDE_REVIEW_SENTIMENTS:
            print(f"EXCLUDE: {title} — blocked review: {review_sentiment}")
            return None

    multiplayer_score, multiplayer_hits = score_multiplayer(page_text)
    player_score, player_hits, rejected = score_player_count(page_text)
    flavor_score, flavor_hits = score_genres_and_description(title, description, page_text)
    review_count = extract_review_count(page_text)
    review_score = REVIEW_SENTIMENT_SCORES.get(review_sentiment, UNKNOWN_REVIEW_SCORE_BY_TYPE.get(item_type, 0))

    # Hard exclude: Mostly Negative / Very Negative / Overwhelmingly Negative → skip entirely.
    # Demos/playtests with no reviews (review_sentiment is None) are exempt.
    if review_sentiment in HARD_EXCLUDE_REVIEW_SENTIMENTS:
        print(f"HARD EXCLUDED (negative reviews): {title} | sentiment={review_sentiment}")
        return None

    review_gate_failed = False
    if item_type in ["free_game", "temporarily_free"]:
        review_gate_failed = review_sentiment is None or review_sentiment in FREE_REVIEW_BLOCKLIST
    elif item_type in ["demo", "playtest"]:
        review_gate_failed = review_sentiment in {"Very Negative", "Overwhelmingly Negative"}
    elif item_type == "paid_under_20":
        review_gate_failed = review_sentiment not in PAID_MINIMUM_REVIEW_SENTIMENTS

    refinement_score, refinement_hits = score_quality_refinements(
        title=title,
        description=description,
        text=page_text,
        review_sentiment=review_sentiment,
        review_count=review_count,
        multiplayer_score=multiplayer_score,
        player_score=player_score,
    )

    type_adjustment = 0
    if item_type == "temporarily_free" and review_sentiment in POSITIVE_OR_BETTER_REVIEW_SENTIMENTS:
        type_adjustment += TEMPORARILY_FREE_SCORE_BONUS
    if item_type in {"demo", "playtest"}:
        type_adjustment -= DEMO_SCORE_PENALTY

    demo_section_score = 0
    demo_friend_signal_score = 0
    demo_freshness_bonus = 0
    demo_hits: List[str] = []
    demo_has_free_to_try_signal = True
    if item_type in {"demo", "playtest"}:
        demo_section_score, demo_friend_signal_score, demo_freshness_bonus, demo_hits = score_demo_playtest_friend_group_fit(
            title=title,
            description=description,
            text=page_text,
            review_sentiment=review_sentiment,
            review_count=review_count,
        )
        demo_has_free_to_try_signal = has_demo_playtest_free_to_try_signal(title, description, page_text)

    discount_score = 0
    if item_type == "paid_under_20" and discount_percent is not None:
        if discount_percent >= 50:
            discount_score = 3
        elif discount_percent >= 25:
            discount_score = 2
        elif discount_percent >= 10:
            discount_score = 1

    # Recency bonus applies to all item types; demos/playtests use a ramped tier set.
    recency_tiers = DEMO_PLAYTEST_RECENCY_BONUS_TIERS if item_type in {"demo", "playtest"} else RECENCY_BONUS_TIERS
    recency_score, recency_hits = score_recency_bonus(page_text, tiers=recency_tiers)

    total_score = (
        multiplayer_score
        + player_score
        + flavor_score
        + review_score
        + refinement_score
        + type_adjustment
        + demo_section_score
        + discount_score
        + recency_score
    )

    has_strong_multiplayer = multiplayer_score >= 2
    has_3plus_signal = player_score > 0

    if item_type == "paid_under_20":
        keep = (
            has_strong_multiplayer and
            not rejected and
            not review_gate_failed and
            total_score >= MIN_SCORE_TO_POST_PAID
        )
    elif item_type in ["free_game", "temporarily_free"]:
        keep = (
            has_strong_multiplayer and
            has_3plus_signal and
            not rejected and
            not review_gate_failed and
            total_score >= MIN_SCORE_TO_POST_FREE
        )
    elif item_type in {"demo", "playtest"}:
        # Demo/Playtest has a stricter friend-group gate than full free games:
        # we only post tests that already show multiplayer viability for group nights.
        keep = (
            demo_has_free_to_try_signal and
            has_strong_multiplayer and
            not rejected and
            not review_gate_failed and
            demo_friend_signal_score >= DEMO_PLAYTEST_MIN_FRIEND_SIGNAL and
            total_score >= MIN_SCORE_TO_POST_DEMO_PLAYTEST
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
        "refinement_hits": refinement_hits,
        "review_sentiment": review_sentiment,
        "review_score": review_score,
        "review_count": review_count,
        "review_gate_failed": review_gate_failed,
        "recency_score": recency_score,
        "recency_hits": recency_hits,
        "demo_friend_signal_score": demo_friend_signal_score,
        "demo_freshness_bonus": demo_freshness_bonus,
        "demo_has_free_to_try_signal": demo_has_free_to_try_signal,
        "demo_hits": demo_hits,
        "diversity_tags": extract_diversity_tags(title, description, page_text),
    }


def type_label(item_type: str) -> str:
    if item_type == "demo":
        return "Demo"
    if item_type == "playtest":
        return "Playtest"
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


def format_steam_item_message(item: dict, idx: int, paid: bool = False, demo_playtest: bool = False) -> str:
    emoji = "💸" if paid else ("🧪" if demo_playtest else "🎮")
    if paid:
        label = "Paid Pick"
    elif demo_playtest:
        label = f"{type_label(item.get('type', 'demo'))} Pick"
    else:
        label = "Free Pick"
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
    return load_json_object(DISCORD_DAILY_POSTS_FILE, log=print)


def save_discord_daily_posts(data: Dict[str, dict]) -> None:
    data = prune_discord_daily_posts(data)
    save_json_object_atomic(DISCORD_DAILY_POSTS_FILE, data)


def prune_discord_daily_posts(data: Dict[str, dict]) -> Dict[str, dict]:
    pruned = prune_latest_iso_dates(data, DISCORD_DAILY_POSTS_RETENTION_DAYS, log=print)
    if len(pruned) < len(data):
        print(f"RETENTION: pruned daily posts state from {len(data)} to {len(pruned)} day keys")
    return pruned


def get_target_day_key() -> str:
    manual_day = (os.getenv(DAILY_DATE_OVERRIDE_ENV, "") or "").strip()
    if not manual_day:
        return datetime.now(timezone.utc).date().isoformat()
    try:
        datetime.fromisoformat(manual_day)
    except ValueError:
        raise RuntimeError(f"{DAILY_DATE_OVERRIDE_ENV} must be YYYY-MM-DD")
    return manual_day


def get_force_refresh_same_day() -> bool:
    raw = (os.getenv(FORCE_REFRESH_SAME_DAY_ENV, "") or "").strip().lower()
    if not raw:
        return False
    if raw in {"1", "true", "yes", "on"}:
        return True
    if raw in {"0", "false", "no", "off"}:
        return False
    raise RuntimeError(
        f"{FORCE_REFRESH_SAME_DAY_ENV} must be one of: true/false, 1/0, yes/no, on/off"
    )


def is_manual_run() -> bool:
    """Return True when triggered by workflow_dispatch (manual/test run).

    Manual runs post to Discord but do NOT mark the day as completed so that
    the subsequent scheduled run always executes cleanly.
    """
    event = (os.getenv(GITHUB_EVENT_NAME_ENV, "") or "").strip().lower()
    return event == "workflow_dispatch"


def format_daily_picks_footer_date(target_day_key: str) -> str:
    target_day = datetime.fromisoformat(target_day_key).date()
    return f"{target_day:%A, %B} {target_day.day}, {target_day:%Y}"


def add_thumbs_up_reaction(client: DiscordClient, channel_id: str, message_id: str) -> None:
    if not DISCORD_BOT_TOKEN:
        raise RuntimeError("DISCORD_BOT_TOKEN is not set.")
    client.put_reaction(
        channel_id,
        message_id,
        "%F0%9F%91%8D",
        context=f"add thumbs-up reaction channel={channel_id} message={message_id}",
    )


def message_exists(client: DiscordClient, channel_id: str, message_id: str, context: str) -> bool:
    try:
        client.get_message(channel_id, message_id, context=context)
        return True
    except DiscordMessageNotFoundError:
        return False
    except Exception as error:
        print(f"WARN: failed to verify existing message ({context}): {error}")
        return False


def record_posted_item(
    daily_posts: Dict[str, dict],
    day_key: str,
    section: str,
    title: str,
    url: str,
    source_type: str,
    item_key: str,
    message_id: str,
    channel_id: Optional[str],
    description: Optional[str] = None,
) -> None:
    try:
        day_entry = daily_posts.setdefault(day_key, {"items": []})
        items = day_entry.get("items")
        if not isinstance(items, list):
            day_entry["items"] = []
            items = day_entry["items"]

        item_record = {
            "item_key": item_key,
            "section": section,
            "title": title,
            "url": url,
            "message_id": message_id,
            "channel_id": channel_id,
            "source_type": source_type,
            "posted_at": utc_now_iso(),
        }
        if isinstance(description, str):
            item_record["description"] = description
        existing_index = next(
            (index for index, existing in enumerate(items) if isinstance(existing, dict) and existing.get("item_key") == item_key),
            None,
        )
        if existing_index is None:
            items.append(item_record)
        else:
            items[existing_index] = item_record
        save_discord_daily_posts(daily_posts)
    except Exception as e:
        print(f"RECORD DAILY DISCORD ITEM FAILED: title={title} | error={e}")


def post_message_chunks(chunks: List[str]) -> None:
    for chunk in chunks:
        post_to_discord(chunk)
        sleep_briefly()


def build_discord_message_link(guild_id: str, channel_id: str, message_id: str) -> str:
    return f"https://discord.com/channels/{guild_id}/{channel_id}/{message_id}"


def build_daily_navigation_footer(
    run_state: dict,
    guild_id: Optional[str],
    target_day_key: str,
    posted_section_keys: List[str],
) -> Optional[str]:
    if not isinstance(guild_id, str) or not guild_id.strip():
        print("WARN: DISCORD_GUILD_ID missing; skipping daily navigation footer.")
        return None

    intro_state = run_state.get("intro")
    if not isinstance(intro_state, dict):
        print("WARN: intro state missing; skipping daily navigation footer.")
        return None

    intro_channel_id = intro_state.get("channel_id")
    intro_message_id = intro_state.get("message_id")
    if not (isinstance(intro_channel_id, str) and isinstance(intro_message_id, str)):
        print("WARN: intro message metadata missing; skipping daily navigation footer.")
        return None

    section_labels = {
        "demo_playtest": "🧪 Demo & Playtest Picks",
        "free": "🎮 Free Picks",
        "paid": "💸 Paid Picks",
        "instagram": "📸 Creator Picks",
    }
    section_headers = run_state.get("section_headers", {})
    lines = [
        f"🗓️ Daily Picks for {format_daily_picks_footer_date(target_day_key)}",
        "",
        f"🎯 Intro / Top of Post → [Jump]({build_discord_message_link(guild_id, intro_channel_id, intro_message_id)})",
    ]

    for section_key in DAILY_SECTION_ORDER:
        if section_key not in posted_section_keys:
            continue
        section_state = section_headers.get(section_key) if isinstance(section_headers, dict) else None
        if not isinstance(section_state, dict):
            print(f"WARN: {section_key} header state missing; skipping daily navigation footer.")
            return None
        channel_id = section_state.get("channel_id")
        message_id = section_state.get("message_id")
        if not (isinstance(channel_id, str) and isinstance(message_id, str)):
            print(f"WARN: {section_key} header metadata missing; skipping daily navigation footer.")
            return None
        section_label = section_labels.get(section_key)
        if not section_label:
            continue
        lines.append(f"{section_label} → [Jump]({build_discord_message_link(guild_id, channel_id, message_id)})")

    return "\n".join(lines)


def build_daily_picks_navigation_content(
    run_state: dict,
    guild_id: Optional[str],
    target_day_key: str,
    posted_section_keys: List[str],
) -> Optional[str]:
    if not isinstance(guild_id, str) or not guild_id.strip():
        return None

    lines = [
        f"📅 Daily Picks - {format_daily_picks_footer_date(target_day_key)}",
        "Vote 👍 on any game you want to try. Every vote advances to Step 2.",
    ]

    section_labels = {
        "demo_playtest": "🧪 Demo & Playtest Picks",
        "free": "🎮 Free Picks",
        "paid": "💸 Paid Picks",
        "instagram": "📸 Creator Picks",
    }
    section_headers = run_state.get("section_headers", {})

    for section_key in DAILY_SECTION_ORDER:
        if section_key not in posted_section_keys:
            continue
        section_state = section_headers.get(section_key) if isinstance(section_headers, dict) else None
        if not isinstance(section_state, dict):
            continue
        channel_id = section_state.get("channel_id")
        message_id = section_state.get("message_id")
        if not (isinstance(channel_id, str) and isinstance(message_id, str)):
            continue
        section_label = section_labels.get(section_key)
        if not section_label:
            continue
        lines.append(f"{section_label} ⟹ [Jump]({build_discord_message_link(guild_id, channel_id, message_id)})")

    return "\n".join(lines)


DAILY_INTRO_DIVIDER = "─────────────────────────────────────────"
DAILY_FOOTER_SEPARATOR = "─────────────────── End of Daily Picks ───────────────────"

_DAILY_INTRO_SECTION_LABELS = {
    "demo_playtest": ("🎮", "Demos & Playtests"),
    "free": ("🆓", "Free Picks"),
    "paid": ("💰", "Paid Under $20"),
    "instagram": ("📸", "Instagram Picks"),
}
_DAILY_FOOTER_SECTION_LABELS = {
    "demo_playtest": "🎮 Demos",
    "free": "🆓 Free",
    "paid": "💰 Paid",
    "instagram": "📸 Instagram",
}
_DAILY_MISSING_SECTION_LABELS = {
    "demo_playtest": "Demos & Playtests",
    "free": "Free Picks",
    "paid": "Paid Under $20",
    "instagram": "Instagram Picks",
}


def build_daily_picks_intro_content(
    run_state: dict,
    guild_id: Optional[str],
    target_day_key: str,
    posted_section_keys: List[str],
) -> str:
    """Build the Step 1 intro message with optional jump links and trailing divider."""
    date_str = format_daily_picks_footer_date(target_day_key)
    lines = [
        f"📅 Daily Picks — {date_str}",
        "",
        "Vote 👍 on anything you want to try. All voted games move to Step 2.",
    ]

    if isinstance(guild_id, str) and guild_id.strip() and posted_section_keys:
        section_headers = run_state.get("section_headers", {})
        parts = []
        for section_key in DAILY_SECTION_ORDER:
            if section_key not in posted_section_keys:
                continue
            section_state = section_headers.get(section_key) if isinstance(section_headers, dict) else None
            if not isinstance(section_state, dict):
                continue
            channel_id = section_state.get("channel_id")
            message_id = section_state.get("message_id")
            if not (isinstance(channel_id, str) and isinstance(message_id, str)):
                continue
            emoji, label = _DAILY_INTRO_SECTION_LABELS.get(section_key, ("", section_key))
            link = build_discord_message_link(guild_id, channel_id, message_id)
            parts.append(f"{emoji} [{label}]({link})")
        if parts:
            lines.append("")
            for part in parts:
                lines.append(part)

        all_section_keys = list(DAILY_SECTION_ORDER)
        missing_sections = [
            key for key in all_section_keys
            if key not in posted_section_keys
        ]
        if missing_sections:
            lines.append("")
            for key in missing_sections:
                label = _DAILY_MISSING_SECTION_LABELS.get(key, key)
                lines.append(f"_(No {label} today)_")

    lines.append("")
    lines.append(DAILY_INTRO_DIVIDER)
    return "\n".join(lines)


def build_daily_picks_footer_content(
    run_state: dict,
    guild_id: Optional[str],
    target_day_key: str,
    posted_section_keys: List[str],
) -> Optional[str]:
    """Build the Step 1 footer: single date+jump line followed by End separator."""
    if not isinstance(guild_id, str) or not guild_id.strip():
        return None

    intro_state = run_state.get("intro")
    if not isinstance(intro_state, dict):
        return None
    intro_channel_id = str(intro_state.get("channel_id") or "").strip()
    intro_message_id = str(intro_state.get("message_id") or "").strip()
    if not intro_channel_id or not intro_message_id:
        return None

    date_str = format_daily_picks_footer_date(target_day_key)
    section_headers = run_state.get("section_headers", {})
    link_parts: List[str] = []
    for section_key in DAILY_SECTION_ORDER:
        if section_key not in posted_section_keys:
            continue
        section_state = section_headers.get(section_key) if isinstance(section_headers, dict) else None
        if not isinstance(section_state, dict):
            continue
        channel_id = section_state.get("channel_id")
        message_id = section_state.get("message_id")
        if not (isinstance(channel_id, str) and isinstance(message_id, str)):
            continue
        label = _DAILY_FOOTER_SECTION_LABELS.get(section_key, section_key)
        link_parts.append(f"[{label}]({build_discord_message_link(guild_id, channel_id, message_id)})")

    top_link = build_discord_message_link(guild_id, intro_channel_id, intro_message_id)
    link_parts.append(f"[⬆️ Top]({top_link})")

    all_section_keys = list(DAILY_SECTION_ORDER)
    missing_sections = [k for k in all_section_keys if k not in posted_section_keys]
    missing_lines = []
    for key in missing_sections:
        label = _DAILY_MISSING_SECTION_LABELS.get(key, key)
        missing_lines.append(f"_(No {label} today)_")

    first_line = f"📅 End of Daily Picks — {date_str} · Jump to: {' · '.join(link_parts)}"
    if missing_lines:
        missing_block = "\n".join(missing_lines)
        return f"{first_line}\n{missing_block}\n{DAILY_FOOTER_SEPARATOR}"
    return f"{first_line}\n{DAILY_FOOTER_SEPARATOR}"


def post_daily_pick_messages(
    demo_playtest_items: List[dict],
    free_items: List[dict],
    paid_items: List[dict],
    instagram_posts: List[dict],
    *,
    force_refresh_same_day: bool = False,
    manual_run: bool = False,
) -> Tuple[Dict[str, int], bool]:
    """Post (or reconcile) daily pick messages. Returns (run_counts, rerun_protection_active, verification_state).

    When manual_run is True (workflow_dispatch trigger), the run completes normally
    but does NOT mark the day as completed in state. This ensures the next scheduled
    run always executes cleanly regardless of prior manual runs.
    """
    run_counts: Dict[str, int] = {"created": 0, "updated": 0, "reused": 0, "skipped": 0}
    if not (demo_playtest_items or free_items or paid_items or instagram_posts):
        return run_counts, False, {}

    token_available = bool(DISCORD_BOT_TOKEN)
    daily_posts = load_discord_daily_posts() if token_available else {}
    day_key = get_target_day_key()
    day_entry = daily_posts.setdefault(day_key, {})
    if not isinstance(day_entry, dict):
        day_entry = {}
        daily_posts[day_key] = day_entry

    if not token_available:
        print("DISCORD_BOT_TOKEN missing; skipping auto-reactions and message-ID tracking.")
    else:
        print(f"Daily picks target day={day_key}")

    run_state = day_entry.get("run_state")
    if not isinstance(run_state, dict):
        run_state = {}
        day_entry["run_state"] = run_state
    run_state.setdefault("section_headers", {})
    run_state["last_attempt_at_utc"] = utc_now_iso()

    # Rule 1 & 5: If already completed AND discord_verification.json shows pass=True
    # for today, suppress all re-triggers (watchdog, force_refresh, manual) — nothing to do.
    if bool(run_state.get("completed")) and is_today_verified(day_key):
        print(f"Run already completed and verified — watchdog re-trigger suppressed for {day_key}")
        save_discord_daily_posts(daily_posts)
        return run_counts, True, {}

    if bool(run_state.get("completed")) and not force_refresh_same_day and not manual_run:
        print(f"SKIP: daily picks already completed for {day_key}; rerun protection active (force_refresh_same_day=false)")
        save_discord_daily_posts(daily_posts)
        return run_counts, True, {}
    if bool(run_state.get("completed")) and (force_refresh_same_day or manual_run):
        label = "force_refresh_same_day=true" if force_refresh_same_day else "manual_run=true"
        print(f"REFRESH: daily picks already completed for {day_key}; {label} so reconciling posts")

    discord_client: Optional[DiscordClient] = None
    if token_available:
        session = requests.Session()
        session.headers.update(
            {
                "Authorization": f"Bot {DISCORD_BOT_TOKEN}",
                "Content-Type": "application/json",
            }
        )
        discord_client = DiscordClient(session)

    def post_or_reconcile_simple(message: str, state_key: str, state_obj: dict) -> None:
        existing_message_id = state_obj.get("message_id")
        existing_channel_id = state_obj.get("channel_id")
        should_attempt_edit = bool(force_refresh_same_day) or bool(manual_run)
        if (
            token_available
            and discord_client
            and isinstance(existing_message_id, str)
            and isinstance(existing_channel_id, str)
        ):
            if should_attempt_edit:
                try:
                    payload = discord_client.edit_message(
                        existing_channel_id,
                        existing_message_id,
                        message,
                        context=f"edit {state_key} for {day_key}",
                    )
                    state_obj["message_id"] = str(payload.get("id") or existing_message_id)
                    state_obj["channel_id"] = str(payload.get("channel_id") or existing_channel_id)
                    state_obj["posted_at_utc"] = utc_now_iso()
                    print(f"REFRESH: updated {state_key} for {day_key} (message_id={state_obj['message_id']})")
                    run_counts["updated"] += 1
                    save_discord_daily_posts(daily_posts)
                    return
                except DiscordMessageNotFoundError:
                    print(f"RECOVER: stale/deleted {state_key} for {day_key}; posting replacement")
                except Exception as error:
                    print(f"WARN: failed to edit {state_key} for {day_key}: {error}; falling back to create")
            elif message_exists(
                discord_client,
                existing_channel_id,
                existing_message_id,
                context=f"verify {state_key} for {day_key}",
            ):
                print(f"REUSE: {state_key} for {day_key} (message_id={existing_message_id})")
                run_counts["reused"] += 1
                return

        metadata = post_to_discord_with_metadata(message, capture_metadata=token_available)
        if metadata and metadata.get("message_id"):
            state_obj["message_id"] = metadata["message_id"]
            state_obj["channel_id"] = metadata.get("channel_id")
            state_obj["posted_at_utc"] = utc_now_iso()
            print(f"CREATE: posted {state_key} for {day_key} (message_id={metadata['message_id']})")
            run_counts["created"] += 1
        else:
            print(f"WARN: missing metadata for {state_key} on {day_key}")
            run_counts["skipped"] += 1
        save_discord_daily_posts(daily_posts)

    intro_state = run_state.setdefault("intro", {})
    if not intro_state.get("message_id"):
        intro_placeholder = "\n".join([
            f"📅 Daily Picks — {format_daily_picks_footer_date(day_key)}",
            "",
            "Vote 👍 on anything you want to try. All voted games move to Step 2.",
            "",
            "Loading sections...",
            "",
            DAILY_INTRO_DIVIDER,
        ])
        post_or_reconcile_simple(intro_placeholder, "intro", intro_state)
        sleep_briefly()
    else:
        print(f"REUSE: intro already posted for {day_key} — skipping placeholder re-edit")

    section_items_by_key = {
        "demo_playtest": demo_playtest_items,
        "free": free_items,
        "paid": paid_items,
        "instagram": instagram_posts,
    }
    posted_section_keys = [section_key for section_key in DAILY_SECTION_ORDER if section_items_by_key.get(section_key)]
    section_paid_flags = {"demo_playtest": False, "free": False, "paid": True, "instagram": False}

    # Prune stale section_header entries from previous runs that no longer have items today
    section_headers = run_state.get("section_headers", {})
    stale_keys = [k for k in list(section_headers.keys()) if k not in posted_section_keys]
    for stale_key in stale_keys:
        del section_headers[stale_key]
        print(f"CLEANUP: removed stale section header '{stale_key}' from run_state")

    for section in DAILY_SECTION_CONFIG:
        section_key = section["key"]
        header_message = section["header"]
        section_items = section_items_by_key.get(section_key, [])
        is_paid = section_paid_flags.get(section_key, False)
        source_type = section["source_type"]
        if not section_items:
            continue

        section_headers = run_state.setdefault("section_headers", {})
        section_state = section_headers.setdefault(section_key, {})
        post_or_reconcile_simple(header_message, f"{section_key}_header", section_state)
        sleep_briefly()

        existing_items = day_entry.get("items")
        if not isinstance(existing_items, list):
            existing_items = []
            day_entry["items"] = existing_items

        for idx, item in enumerate(section_items, start=1):
            title = item["title"] if section_key != "instagram" else f"@{item['username']}"
            url = item["url"]
            item_key = hashlib.sha256(f"{section_key}|{source_type}|{url}".encode("utf-8")).hexdigest()[:16]
            existing_record = next(
                (entry for entry in existing_items if isinstance(entry, dict) and entry.get("item_key") == item_key),
                None,
            )

            content = (
                format_instagram_item_message(item, idx)
                if section_key == "instagram"
                else format_steam_item_message(item, idx, paid=is_paid, demo_playtest=(section_key == "demo_playtest"))
            )
            metadata = None
            is_new_message = False
            if (
                token_available
                and discord_client
                and isinstance(existing_record, dict)
                and isinstance(existing_record.get("channel_id"), str)
                and isinstance(existing_record.get("message_id"), str)
            ):
                existing_channel_id = str(existing_record["channel_id"])
                existing_message_id = str(existing_record["message_id"])
                if force_refresh_same_day or manual_run:
                    try:
                        payload = discord_client.edit_message(
                            existing_channel_id,
                            existing_message_id,
                            content,
                            context=f"edit {section_key} item {title} for {day_key}",
                        )
                        metadata = {
                            "message_id": str(payload.get("id") or existing_message_id),
                            "channel_id": str(payload.get("channel_id") or existing_channel_id),
                        }
                        print(f"REFRESH: updated {section_key} item {title} for {day_key}")
                        run_counts["updated"] += 1
                        # Update the existing record
                        existing_record["description"] = item.get("caption") if section_key == "instagram" else item.get("description")
                        existing_record["posted_at"] = utc_now_iso()
                    except DiscordMessageNotFoundError:
                        print(f"RECOVER: stale/deleted {section_key} item {title} for {day_key}; posting replacement")
                    except Exception as error:
                        print(
                            f"WARN: failed to edit {section_key} item {title} for {day_key}: {error}; "
                            "falling back to create"
                        )
                elif message_exists(
                    discord_client,
                    existing_channel_id,
                    existing_message_id,
                    context=f"verify {section_key} item for {day_key}",
                ):
                    metadata = {"message_id": existing_message_id, "channel_id": existing_channel_id}
                    print(f"REUSE: {section_key} item {title} for {day_key}")
                    run_counts["reused"] += 1
                    # Update the existing record
                    existing_record["description"] = item.get("caption") if section_key == "instagram" else item.get("description")
                    existing_record["posted_at"] = utc_now_iso()

            if metadata is None:
                metadata = post_to_discord_with_metadata(content, capture_metadata=token_available)
                is_new_message = True
            if token_available and metadata and metadata.get("message_id") and metadata.get("channel_id"):
                try:
                    if discord_client and is_new_message:
                        add_thumbs_up_reaction(discord_client, metadata["channel_id"], metadata["message_id"])
                except Exception as e:
                    print(f"ADD REACTION FAILED: title={title} | error={e}")
                if is_new_message:
                    record_posted_item(
                        daily_posts=daily_posts,
                        day_key=day_key,
                        section=section_key,
                        title=title,
                        url=url,
                        source_type=source_type,
                        item_key=item_key,
                        message_id=metadata["message_id"],
                        channel_id=metadata["channel_id"],
                        description=item.get("caption") if section_key == "instagram" else item.get("description"),
                    )
                    print(f"CREATE: posted {section_key} item {title} for {day_key}")
                    run_counts["created"] += 1
                else:
                    print(f"REUSE: persisted existing {section_key} item {title} for {day_key}")
            else:
                print(f"WARN: missing metadata for {section_key} item title={title}")
                run_counts["skipped"] += 1
            sleep_briefly()

    # Edit intro with jump links now that sections are posted
    if token_available and discord_client:
        intro_content = build_daily_picks_intro_content(run_state, DISCORD_GUILD_ID, day_key, posted_section_keys)
        existing_message_id = intro_state.get("message_id")
        existing_channel_id = intro_state.get("channel_id")
        if existing_message_id and existing_channel_id:
            try:
                discord_client.edit_message(existing_channel_id, existing_message_id, intro_content, context=f"edit intro for {day_key}")
                intro_state["posted_at_utc"] = utc_now_iso()
                print(f"EDIT: updated intro for {day_key}")
            except Exception as e:
                print(f"WARN: failed to edit intro for {day_key}: {e}")

    footer_state = run_state.setdefault("footer", {})
    footer_content = build_daily_picks_footer_content(run_state, DISCORD_GUILD_ID, day_key, posted_section_keys)
    if footer_content:
        post_or_reconcile_simple(footer_content, "footer", footer_state)
        sleep_briefly()

    if manual_run:
        print(f"MANUAL RUN: daily picks done for {day_key}; skipping completed=True to preserve scheduled run eligibility")
    else:
        run_state["completed"] = True
        run_state["completed_at_utc"] = utc_now_iso()
        print(f"COMPLETE: daily picks state marked completed for {day_key}")
    save_discord_daily_posts(daily_posts)
    return run_counts, False, {
        "run_state": run_state,
        "items": day_entry.get("items") or [],
        "posted_section_keys": posted_section_keys,
    }


def dedupe_by_app_id(items: List[Tuple[str, str]]) -> List[Tuple[str, str]]:
    seen = set()
    output = []
    for source, app_id in items:
        if app_id not in seen:
            seen.add(app_id)
            output.append((source, app_id))
    return output


def _normalize_instagram_game_key_fragment(text: str) -> str:
    normalized = text.lower()
    normalized = re.sub(r"#\w+", " ", normalized)
    for pattern in INSTAGRAM_GAME_KEY_BOILERPLATE_PATTERNS:
        normalized = re.sub(pattern, " ", normalized)
    # Remove hyphens without inserting a space so "Co-Op" → "coop", matching "COOP".
    normalized = re.sub(r"-", "", normalized)
    normalized = re.sub(r"[^a-z0-9\s]", " ", normalized)
    normalized = re.sub(r"\s+", " ", normalized).strip()
    return normalized


def _is_low_confidence_instagram_title_candidate(key: str) -> bool:
    tokens = key.split()
    if len(tokens) > 6:
        return True
    return any(token in INSTAGRAM_GAME_KEY_GENERIC_PREFIX_TOKENS for token in tokens)


def derive_instagram_game_key(caption: str) -> Optional[str]:
    if not caption:
        return None

    raw_caption = caption.strip()
    if not raw_caption or raw_caption == "(no caption)":
        return None

    quoted_or_bracketed_patterns = [
        r"[\"“”]([^\"“”]{2,80})[\"“”]",
        r"\[([^\[\]]{2,80})\]",
        r"\(([^\(\)]{2,80})\)",
    ]
    for pattern in quoted_or_bracketed_patterns:
        match = re.search(pattern, raw_caption)
        if match:
            key = _normalize_instagram_game_key_fragment(match.group(1))
            if key:
                return key

    separator_match = re.search(r"\s[-|:]\s", raw_caption)
    if separator_match:
        candidate = raw_caption[:separator_match.start()].strip()
        key = _normalize_instagram_game_key_fragment(candidate)
        if key and len(key) >= 3 and re.search(r"[a-z]", key):
            return key

    boundary_start = None
    for pattern in INSTAGRAM_GAME_KEY_FALLBACK_BOUNDARY_PATTERNS:
        match = re.search(pattern, raw_caption, flags=re.IGNORECASE)
        if not match:
            continue
        if boundary_start is None or match.start() < boundary_start:
            boundary_start = match.start()

    if boundary_start is not None:
        candidate = raw_caption[:boundary_start].strip(" -|:–—•")
        key = _normalize_instagram_game_key_fragment(candidate)
        if key and len(key) >= 3 and re.search(r"[a-z]", key):
            if not _is_low_confidence_instagram_title_candidate(key):
                return key

    # Last resort: treat the whole caption as the game title if it is short and specific.
    # Handles plain-title captions like "Drunkslop Pub Crawl Co-Op" with no separators or
    # boilerplate keywords that the earlier paths can latch onto.
    key = _normalize_instagram_game_key_fragment(raw_caption)
    if key and len(key) >= 3 and re.search(r"[a-z]", key):
        if not _is_low_confidence_instagram_title_candidate(key):
            return key

    return None


def dedupe_instagram_posts(posts: List[dict]) -> List[dict]:
    deduped_posts, _ = _dedupe_instagram_posts_with_debug(posts)
    return deduped_posts


def _dedupe_instagram_posts_with_debug(posts: List[dict]) -> Tuple[List[dict], Dict[str, object]]:
    game_posts: Dict[str, dict] = {}
    no_key_posts: List[dict] = []
    removed_keys: List[str] = []

    for post in posts:
        key = derive_instagram_game_key(post.get("caption", ""))
        if key is None:
            no_key_posts.append(post)
            continue

        if key not in game_posts:
            game_posts[key] = {
                "usernames": [post["username"]],
                "caption": post["caption"],
                "url": post["url"],  # Keep the first URL
            }
        else:
            # Merge usernames
            if post["username"] not in game_posts[key]["usernames"]:
                game_posts[key]["usernames"].append(post["username"])
            first_username = game_posts[key]["usernames"][0]
            print(
                f"[Instagram dedup] Dropped duplicate key='{key}' "
                f"from @{post['username']} (already kept from @{first_username})"
            )
            if len(removed_keys) < 3:
                removed_keys.append(key)

    deduped_posts = []
    for key, data in game_posts.items():
        deduped_posts.append({
            "username": ", ".join(sorted(data["usernames"])),  # Combine usernames
            "caption": data["caption"],
            "url": data["url"],
        })

    # Add posts without keys as-is
    deduped_posts.extend(no_key_posts)

    debug: Dict[str, object] = {
        "fetched_count": len(posts),
        "deduped_count": len(deduped_posts),
        "removed_count": len(posts) - len(deduped_posts),
    }
    return deduped_posts, debug


def prune_instagram_seen_state(data: object) -> Dict[str, List[str]]:
    if not isinstance(data, dict):
        return {}
    cleaned: Dict[str, List[str]] = {}
    for username, shortcodes in data.items():
        if not isinstance(username, str) or not isinstance(shortcodes, list):
            continue
        valid_shortcodes = [code for code in shortcodes if isinstance(code, str) and code]
        cleaned[username] = valid_shortcodes[-INSTAGRAM_SEEN_RETENTION_PER_CREATOR:]
    return cleaned

def load_instagram_seen():
    if not os.path.exists(INSTAGRAM_STATE_FILE):
        return {}

    try:
        with open(INSTAGRAM_STATE_FILE, "r", encoding="utf-8") as f:
            return prune_instagram_seen_state(json.load(f))
    except Exception:
        return {}


def save_instagram_seen(data):
    with open(INSTAGRAM_STATE_FILE, "w", encoding="utf-8") as f:
        json.dump(prune_instagram_seen_state(data), f, indent=2)


def _check_instagram_session_age(session_file: str) -> None:
    """Warn if the Instagram session file is old (proxy for session expiry).

    >50 days: print WARN and post to health monitor
    >30 days: print INFO only
    <30 days: no action
    Missing file: skip gracefully
    """
    try:
        age_days = (datetime.now().timestamp() - os.path.getmtime(session_file)) / 86400
    except OSError:
        return
    if age_days > 50:
        print(f"WARN: Instagram session file is {age_days:.0f} days old — session may have expired", flush=True)
        _notify_health_monitor(
            f"⚠️ Instagram session file is {age_days:.0f} days old.\n"
            "The session may have expired — re-authenticate and update the INSTAGRAM_SESSION secret."
        )
    elif age_days > 30:
        print(f"INFO: Instagram session file is {age_days:.0f} days old — consider refreshing soon", flush=True)


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

    _check_instagram_session_age(session_file)

    try:
        loader.load_session_from_file(instagram_username, session_file)
        print(f"Loaded Instagram session for {instagram_username}")
    except Exception as e:
        print(f"Instagram session load failed: {e}")
        _notify_health_monitor(
            f"⚠️ Instagram session load failed for {instagram_username}: {e}\n"
            f"Instagram session may have expired — re-authenticate and update INSTAGRAM_SESSION secret."
        )
        return []

    # Session auth check — make a real authenticated API call to verify the session.
    try:
        authed_user = loader.test_login()
        if not authed_user:
            print(f"WARN: Instagram session auth check failed — test_login() returned None for {instagram_username}")
            _notify_health_monitor(
                f"⚠️ Instagram session auth check failed for {instagram_username}.\n"
                f"test_login() returned None — session may have expired. "
                f"Re-authenticate and update the INSTAGRAM_SESSION secret."
            )
            return []
        print(f"Instagram session valid: logged in as @{authed_user}")
    except Exception as e:
        print(f"WARN: Instagram session auth check raised an exception: {e}")
        _notify_health_monitor(
            f"⚠️ Instagram session auth check raised an exception for {instagram_username}: {e}\n"
            f"Re-authenticate and update the INSTAGRAM_SESSION secret."
        )
        return []

    cutoff_utc = datetime.now(timezone.utc) - timedelta(days=INSTAGRAM_MAX_POST_AGE_DAYS)

    creator_stats: Dict[str, Dict] = {}

    for username in INSTAGRAM_CREATORS:
        creator_stats[username] = {"collected": 0, "skipped_seen": 0, "skipped_age": 0, "failed": False, "failure_reason": None}
        try:
            if username not in seen:
                seen[username] = []

            profile = instaloader.Profile.from_username(loader.context, username)
            count = 0
            skipped_age = 0
            skipped_seen = 0

            for post in profile.get_posts():
                # Posts are returned newest-first; stop as soon as we pass the age cutoff.
                post_date = post.date_utc.replace(tzinfo=timezone.utc)
                if post_date < cutoff_utc:
                    age_days = (datetime.now(timezone.utc) - post_date).days
                    print(
                        f"INSTAGRAM AGE FILTER: @{username} post {post.shortcode} "
                        f"date={post_date.date().isoformat()} age={age_days}d "
                        f"(cutoff={INSTAGRAM_MAX_POST_AGE_DAYS}d); stopping iteration"
                    )
                    skipped_age += 1
                    break

                shortcode = post.shortcode

                if shortcode in seen[username]:
                    print(f"INSTAGRAM SEEN: @{username} post {shortcode} already seen; skipping")
                    skipped_seen += 1
                    continue

                caption = (post.caption or "").replace("\n", " ").strip()
                if len(caption) > 120:
                    caption = caption[:117] + "..."

                blocked_caption_phrases = [
                    "coming soon",
                    "not yet available",
                    "wishlist now",
                ]
                blocked_caption_patterns = [
                    r"coming\s+202[5-9]",
                    r"coming\s+203[0-9]",
                ]
                caption_lower = caption.lower()
                if any(phrase in caption_lower for phrase in blocked_caption_phrases):
                    print(f"INSTAGRAM SKIP: @{username} — unavailable caption")
                    continue
                if any(re.search(p, caption_lower) for p in blocked_caption_patterns):
                    print(f"INSTAGRAM SKIP: @{username} — future release caption")
                    continue

                all_new_posts.append({
                    "username": username,
                    "caption": caption or "(no caption)",
                    "url": f"https://www.instagram.com/p/{shortcode}/",
                })

                seen[username].append(shortcode)
                count += 1

                if count >= MAX_INSTAGRAM_POSTS_PER_ACCOUNT:
                    break

            seen[username] = seen[username][-INSTAGRAM_SEEN_RETENTION_PER_CREATOR:]

            creator_stats[username]["collected"] = count
            creator_stats[username]["skipped_seen"] = skipped_seen
            creator_stats[username]["skipped_age"] = skipped_age

            if count == 0:
                print(
                    f"INSTAGRAM ZERO POSTS: @{username} returned 0 new posts "
                    f"(skipped_seen={skipped_seen}, skipped_age={skipped_age})"
                )
            else:
                print(f"INSTAGRAM: @{username} collected {count} new post(s) (skipped_seen={skipped_seen}, skipped_age={skipped_age})")

        except Exception as e:
            print(f"Instagram scrape failed for {username}: {e}")
            creator_stats[username]["failed"] = True
            creator_stats[username]["failure_reason"] = str(e)
            continue

    save_instagram_seen(seen)
    total_posts = len(all_new_posts)
    total_skipped_seen = sum(s["skipped_seen"] for s in creator_stats.values())
    creators_with_posts = sum(1 for s in creator_stats.values() if s["collected"] > 0)
    failed_creators = [u for u, s in creator_stats.items() if s["failed"]]
    print(f"Instagram posts found this run: {total_posts}")

    fetch_summary = {
        "run_at": datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z"),
        "total_creators": len(INSTAGRAM_CREATORS),
        "creators_with_posts": creators_with_posts,
        "total_posts_collected": total_posts,
        "total_skipped_seen": total_skipped_seen,
        "failed_creators": failed_creators,
        "creators": creator_stats,
    }
    try:
        save_json_object_atomic(INSTAGRAM_FETCH_SUMMARY_FILE, fetch_summary)
    except Exception as e:
        print(f"WARN: could not save instagram_fetch_summary.json: {e}")

    return all_new_posts
    
def load_daily_verification_artifact(path: str = DAILY_VERIFICATION_FILE) -> dict:
    return load_json_object(path, default={})


def verification_passed_for_day(day_key: str, artifact: dict) -> bool:
    if not isinstance(artifact, dict):
        return False
    return artifact.get("day_key") == day_key and bool(artifact.get("pass"))


def export_stop_go_result(
    *,
    day_key: str,
    decision: str,
    reason: str,
    attempt: Optional[int] = None,
    max_attempts: int = MAX_RETRY_ATTEMPTS,
    verification_file: str = DAILY_VERIFICATION_FILE,
    path: Optional[str] = None,
) -> None:
    """Write a machine-readable stop/go decision artifact for external orchestration."""
    signal = decision
    escalation_target = None
    if decision == "give_up":
        signal = "escalate_to_fixer"
        escalation_target = "claude_code"

    result = {
        "day_key": day_key,
        "decision": decision,
        "signal": signal,
        "reason": reason,
        "attempt": attempt,
        "max_attempts": max_attempts,
        "verification_file": verification_file,
        "orchestrator": "openhands",
        "fixer": "claude_code",
        "escalation_target": escalation_target,
        "generated_at_utc": utc_now_iso(),
    }
    result_path = path or STOP_GO_RESULT_FILE
    save_json_object_atomic(result_path, result)
    print(
        "STOP_GO_RESULT "
        f"file={result_path} day_key={day_key} decision={decision} signal={signal} "
        f"attempt={attempt if attempt is not None else 'na'}/{max_attempts} "
        f"reason={reason}"
    )


def run_daily_workflow(*, force_refresh_same_day: bool = False, manual_run: bool = False) -> None:
    state = load_state()
    print(f"Daily run target date (UTC): {get_target_day_key()}")

    start_page, end_page = get_page_window()
    print(f"Current rotating page window: {start_page}-{end_page}")

    scraping_stats: dict = {"ok": 0, "fail": 0}
    free_candidates = (
        collect_steam_free_candidates(start_page, end_page, scraping_stats) +
        collect_steam_demo_candidates(start_page, end_page, scraping_stats) +
        collect_steamdb_promo_candidates()
    )
    free_candidates = dedupe_by_app_id(free_candidates)

    paid_candidates = collect_paid_candidates(start_page, end_page, scraping_stats)
    paid_candidates = dedupe_by_app_id(paid_candidates)

    pages_ok = scraping_stats.get("ok", 0)
    pages_fail = scraping_stats.get("fail", 0)
    total_pages = pages_ok + pages_fail
    if total_pages == 0 or pages_fail == 0:
        scraping_status = "ok"
    elif pages_ok == 0:
        scraping_status = "broken"
    else:
        scraping_status = "degraded"

    if scraping_status == "broken":
        _notify_health_monitor(
            f"🔴 Steam scraping is broken — all {pages_fail} page(s) failed to fetch. "
            "Check Steam connectivity and scraper URLs."
        )
    elif scraping_status == "degraded":
        _notify_health_monitor(
            f"⚠️ Steam scraping is degraded — {pages_fail} of {total_pages} page(s) failed. "
            "Some candidates may be missing."
        )

    print(f"Free candidates collected: {len(free_candidates)}")
    print(f"Paid candidates collected: {len(paid_candidates)}")
    steam_candidates_scanned = len(free_candidates) + len(paid_candidates)

    qualified_demo_playtest = []
    qualified_free = []
    qualified_paid = []
    filtered_weak_reviews = 0
    filtered_weak_group_fit = 0
    filtered_low_signal_junk = 0
    filtered_repost_cooldown = 0
    debug_records: List[DebugRecord] = []

    for source, app_id in free_candidates:
        item = inspect_game(source, app_id)
        sleep_briefly()

        if not item:
            continue
        log_candidate_decision(item, phase="evaluated")
        record: DebugRecord = {
            "title": item["title"],
            "type": item["type"],
            "final_score": item["score"],
            "review_sentiment": item.get("review_sentiment"),
            "friend_group_signal": item.get("demo_friend_signal_score", 0),
            "keep": bool(item["keep"]),
            "reason_list": [],
        }
        if not item["keep"]:
            reason_list = build_filter_reason_list(item)
            if FILTER_REASON_WEAK_REVIEW in reason_list:
                filtered_weak_reviews += 1
            if FILTER_REASON_WEAK_GROUP_FIT in reason_list:
                filtered_weak_group_fit += 1
            if FILTER_REASON_LOW_SIGNAL_JUNK in reason_list:
                filtered_low_signal_junk += 1
            record["reason_list"] = reason_list
            log_candidate_decision(item, phase="filtered_not_kept")
            debug_records.append(record)
            continue
        if not can_repost(app_id, item["type"], state):
            filtered_repost_cooldown += 1
            record["reason_list"] = [FILTER_REASON_REPOST_COOLDOWN]
            log_candidate_decision(item, phase="filtered_repost_cooldown")
            debug_records.append(record)
            continue

        record["reason_list"] = [FILTER_REASON_QUALIFIED]
        debug_records.append(record)
        section_key = route_item_to_daily_section(item["type"])
        if section_key == "demo_playtest":
            qualified_demo_playtest.append(item)
        elif section_key == "free":
            qualified_free.append(item)

    for source, app_id in paid_candidates:
        item = inspect_game(source, app_id)
        sleep_briefly()

        if not item:
            continue
        log_candidate_decision(item, phase="evaluated")
        record: DebugRecord = {
            "title": item["title"],
            "type": item["type"],
            "final_score": item["score"],
            "review_sentiment": item.get("review_sentiment"),
            "friend_group_signal": item.get("demo_friend_signal_score", 0),
            "keep": bool(item["keep"]),
            "reason_list": [],
        }
        if item["type"] != "paid_under_20":
            record["reason_list"] = [FILTER_REASON_BELOW_THRESHOLD]
            debug_records.append(record)
            continue
        if not item["keep"]:
            reason_list = build_filter_reason_list(item)
            if FILTER_REASON_WEAK_REVIEW in reason_list:
                filtered_weak_reviews += 1
            if FILTER_REASON_WEAK_GROUP_FIT in reason_list:
                filtered_weak_group_fit += 1
            if FILTER_REASON_LOW_SIGNAL_JUNK in reason_list:
                filtered_low_signal_junk += 1
            record["reason_list"] = reason_list
            log_candidate_decision(item, phase="filtered_not_kept")
            debug_records.append(record)
            continue
        if not can_repost(app_id, item["type"], state):
            filtered_repost_cooldown += 1
            record["reason_list"] = [FILTER_REASON_REPOST_COOLDOWN]
            log_candidate_decision(item, phase="filtered_repost_cooldown")
            debug_records.append(record)
            continue

        record["reason_list"] = [FILTER_REASON_QUALIFIED]
        debug_records.append(record)
        if route_item_to_daily_section(item["type"]) == "paid":
            qualified_paid.append(item)

    qualified_free.sort(
        key=lambda x: (
            x["score"],
            x.get("review_score", 0),
            1 if x["type"] == "temporarily_free" else 0,
        ),
        reverse=True
    )

    qualified_demo_playtest.sort(
        key=lambda x: (
            x["score"],
            x.get("demo_friend_signal_score", 0),
            x.get("demo_freshness_bonus", 0),
            x.get("review_score", 0),
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

    qualified_demo_playtest = apply_light_diversity_rerank(qualified_demo_playtest)
    qualified_free = apply_light_diversity_rerank(qualified_free)
    qualified_paid = apply_light_diversity_rerank(qualified_paid)

    demo_playtest_items = select_demo_playtest_items(qualified_demo_playtest, MAX_DEMO_PLAYTEST_POSTS)
    free_items = qualified_free[:MAX_FREE_POSTS]
    paid_items = qualified_paid[:MAX_PAID_POSTS]

    print(f"Qualified demo/playtest items before cap: {len(qualified_demo_playtest)}")
    print(f"Qualified free items before cap: {len(qualified_free)}")
    print(f"Qualified paid items before cap: {len(qualified_paid)}")
    print(
        f"Demo/playtest quality floor for final section: score>={DEMO_PLAYTEST_QUALITY_FLOOR_SCORE} "
        f"(cap={MAX_DEMO_PLAYTEST_POSTS})"
    )

    fetched_instagram_posts = fetch_instagram_posts()
    instagram_posts, instagram_debug = _dedupe_instagram_posts_with_debug(fetched_instagram_posts)
    print(
        "Instagram dedupe: "
        f"fetched={instagram_debug['fetched_count']} "
        f"kept={instagram_debug['deduped_count']} "
        f"removed={instagram_debug['removed_count']}"
    )
    force_refresh_same_day = force_refresh_same_day or get_force_refresh_same_day()
    if force_refresh_same_day:
        print("Daily picks run configured with FORCE_REFRESH_SAME_DAY=true")
    manual_run = manual_run or is_manual_run()
    if manual_run:
        print("Daily picks run is a manual (workflow_dispatch) run — completed flag will not be set")
    run_counts, rerun_protection_active, verification_state = post_daily_pick_messages(
        demo_playtest_items,
        free_items,
        paid_items,
        instagram_posts,
        force_refresh_same_day=force_refresh_same_day,
        manual_run=manual_run,
    )

    if not demo_playtest_items and not free_items and not paid_items:
        print("No qualifying games found from Steam.")

    for item in demo_playtest_items + free_items + paid_items:
        update_state_for_post(item["id"], item["type"], state)

    save_state(state)

    total = len(demo_playtest_items) + len(free_items) + len(paid_items)
    print(f"Posted {total} Steam item(s) to Discord.")
    print(f"Demo/playtest items selected: {len(demo_playtest_items)}")
    print(f"Free items selected: {len(free_items)}")
    print(f"Paid items selected: {len(paid_items)}")

    for item in demo_playtest_items:
        log_candidate_decision(item, phase="selected")
        print(
            f"DEMO/PLAYTEST: {item['title']} ({item['type']}) "
            f"score={item['score']} friend_signal={item.get('demo_friend_signal_score', 0)} "
            f"freshness_bonus={item.get('demo_freshness_bonus', 0)} "
            f"diversity_penalty={item.get('diversity_penalty', 0)}"
        )

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

    reason_counts: Dict[str, int] = {}
    for record in debug_records:
        for reason in record["reason_list"]:
            if reason == FILTER_REASON_QUALIFIED:
                continue
            reason_counts[reason] = reason_counts.get(reason, 0) + 1
    top_filter_reasons = sorted(reason_counts.items(), key=lambda kv: kv[1], reverse=True)[:3]
    selected_title_samples = {
        "demo_playtest": [item["title"] for item in demo_playtest_items[:3]],
        "free": [item["title"] for item in free_items[:3]],
        "paid": [item["title"] for item in paid_items[:3]],
    }

    run_summary_lines = build_run_summary(
        steam_candidates_scanned=steam_candidates_scanned,
        demo_playtest_candidates_qualified=len(qualified_demo_playtest),
        free_candidates_qualified=len(qualified_free),
        paid_candidates_qualified=len(qualified_paid),
        demo_playtest_posted=len(demo_playtest_items),
        free_posted=len(free_items),
        paid_posted=len(paid_items),
        filtered_weak_reviews=filtered_weak_reviews,
        filtered_weak_group_fit=filtered_weak_group_fit,
        filtered_low_signal_junk=filtered_low_signal_junk,
        filtered_repost_cooldown=filtered_repost_cooldown,
        top_filter_reasons=top_filter_reasons,
        selected_title_samples=selected_title_samples,
    )
    for line in run_summary_lines:
        print(line)
    export_daily_debug_summary(
        debug_records,
        run_summary_lines,
        target_day_key=get_target_day_key(),
        instagram_debug=instagram_debug,
    )
    post_discord_debug_summary(
        day_key=get_target_day_key(),
        run_counts=run_counts,
        rerun_protection_active=rerun_protection_active,
        force_refresh_same_day=force_refresh_same_day,
    )
    export_verification_artifact(
        day_key=get_target_day_key(),
        run_counts=run_counts,
        rerun_protection_active=rerun_protection_active,
        verification_state=verification_state,
        pages_fetched_successfully=pages_ok,
        pages_failed=pages_fail,
        scraping_status=scraping_status,
    )

    next_start_page = get_next_start_page(start_page)
    save_page_state(next_start_page)
    next_end_page = min(next_start_page + PAGE_WINDOW_SIZE - 1, MAX_PAGE_LIMIT)
    print(f"Next rotating page window saved: {next_start_page}-{next_end_page}")

    step1_channel_id = (DISCORD_STEP1_CHANNEL_ID or "").strip()
    if step1_channel_id and DISCORD_BOT_TOKEN:
        with requests.Session() as _expl_session:
            _expl_session.headers.update({
                "Authorization": f"Bot {DISCORD_BOT_TOKEN}",
                "Content-Type": "application/json",
            })
            post_or_edit_rolling_explainer(DiscordClient(_expl_session), step1_channel_id, "step-1")


def main():
    day_key = get_target_day_key()
    artifact = load_daily_verification_artifact()
    if verification_passed_for_day(day_key, artifact):
        export_stop_go_result(
            day_key=day_key,
            decision="stop",
            reason="verification_pass",
            attempt=0,
        )
        print(
            "STOP_GO decision=stop "
            f"reason=verification_pass day_key={day_key} verification_file={DAILY_VERIFICATION_FILE}"
        )
        return

    for attempt in range(1, MAX_RETRY_ATTEMPTS + 1):
        if attempt > 1:
            export_stop_go_result(
                day_key=day_key,
                decision="retry",
                reason="verification_failed",
                attempt=attempt,
            )
            print(
                "STOP_GO decision=retry "
                f"reason=verification_failed attempt={attempt}/{MAX_RETRY_ATTEMPTS}"
            )
        run_daily_workflow(force_refresh_same_day=(attempt > 1))
        artifact = load_daily_verification_artifact()
        if verification_passed_for_day(day_key, artifact):
            export_stop_go_result(
                day_key=day_key,
                decision="stop",
                reason="verification_pass",
                attempt=attempt,
            )
            print(
                "STOP_GO decision=stop "
                f"reason=verification_pass attempt={attempt}/{MAX_RETRY_ATTEMPTS}"
            )
            return

    export_stop_go_result(
        day_key=day_key,
        decision="give_up",
        reason="max_retry_attempts_reached",
        attempt=MAX_RETRY_ATTEMPTS,
    )
    print(
        "STOP_GO decision=give_up "
        f"reason=max_retry_attempts_reached attempts={MAX_RETRY_ATTEMPTS}/{MAX_RETRY_ATTEMPTS}"
    )

if __name__ == "__main__":
    main()
