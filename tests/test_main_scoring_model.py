import main


class FakeResponse:
    def __init__(self, app_id, text):
        self.url = f"https://store.steampowered.com/app/{app_id}/"
        self.text = text

    def raise_for_status(self):
        return None


def build_html(
    title: str,
    description: str,
    feature_text: str,
    review_sentiment: str = "",
) -> str:
    return f"""
    <html>
      <head><meta property="og:title" content="{title}" /></head>
      <body>
        <div id="appHubAppName">{title}</div>
        <div class="game_description_snippet">{description}</div>
        <div>{review_sentiment}</div>
        <div>{feature_text}</div>
      </body>
    </html>
    """


def stub_app_pages(monkeypatch, html_by_app_id):
    def fake_get(url, headers=None, timeout=30):
        app_id = url.rstrip("/").split("/")[-1]
        return FakeResponse(app_id, html_by_app_id[app_id])

    monkeypatch.setattr(main.requests, "get", fake_get)


def test_unknown_review_sentiment_penalized_and_blocked_for_paid(monkeypatch):
    base_text = "Multiplayer Online Co-Op up to 6 players party game"
    free_html = build_html("Unknown Free", "friends game", base_text, review_sentiment="")
    paid_html = build_html("Unknown Paid", "friends game", base_text, review_sentiment="")
    stub_app_pages(monkeypatch, {"101": free_html, "102": paid_html})
    monkeypatch.setattr(main, "get_price_info", lambda app_id: (9.99, False))

    free_item = main.inspect_game("steam_free", "101")
    paid_item = main.inspect_game("paid_candidate", "102")

    assert free_item is not None and paid_item is not None
    assert free_item["review_sentiment"] is None
    assert free_item["review_score"] == -2
    assert paid_item["review_sentiment"] is None
    assert paid_item["review_gate_failed"] is True


def test_mixed_reviews_are_harder_to_qualify(monkeypatch):
    shared = "Multiplayer Online Co-Op up to 6 players party game"
    html_mostly_positive = build_html("Mostly Positive Game", "friends game", shared, "Mostly Positive")
    html_mixed = build_html("Mixed Game", "friends game", shared, "Mixed")
    stub_app_pages(monkeypatch, {"201": html_mostly_positive, "202": html_mixed})

    mostly_positive = main.inspect_game("steam_free", "201")
    mixed = main.inspect_game("steam_free", "202")

    assert mostly_positive is not None and mixed is not None
    assert mixed["review_score"] == -6
    assert mostly_positive["score"] > mixed["score"]


def test_mmo_player_bonus_is_reduced(monkeypatch):
    html = build_html("MMO Game", "friends game", "Massively Multiplayer MMO Online Co-Op")
    stub_app_pages(monkeypatch, {"301": html})

    item = main.inspect_game("steam_free", "301")
    assert item is not None
    player_score, _, _ = main.score_player_count("Massively Multiplayer MMO Online Co-Op")
    assert player_score == 5


def test_very_positive_group_game_ranks_above_mixed(monkeypatch):
    shared = "Multiplayer Online Co-Op up to 6 players party game"
    strong = build_html("Strong Co-op", "team up with friends", shared, "Very Positive")
    weak = build_html("Weak Co-op", "team up with friends", shared, "Mixed")
    stub_app_pages(monkeypatch, {"401": strong, "402": weak})

    strong_item = main.inspect_game("steam_free", "401")
    weak_item = main.inspect_game("steam_free", "402")
    assert strong_item is not None and weak_item is not None
    assert strong_item["score"] > weak_item["score"]


def test_review_count_confidence_bonus(monkeypatch):
    low_count = build_html(
        "Low Count",
        "team up with friends",
        "Multiplayer Online Co-Op up to 6 players Very Positive 80 reviews",
        "Very Positive",
    )
    high_count = build_html(
        "High Count",
        "team up with friends",
        "Multiplayer Online Co-Op up to 6 players Very Positive 12,000 reviews",
        "Very Positive",
    )
    stub_app_pages(monkeypatch, {"501": low_count, "502": high_count})

    low_item = main.inspect_game("steam_free", "501")
    high_item = main.inspect_game("steam_free", "502")
    assert low_item is not None and high_item is not None
    assert high_item["review_count"] == 12000
    assert high_item["score"] >= low_item["score"] + 2


def test_paid_rejects_unknown_and_mixed(monkeypatch):
    unknown = build_html("Paid Unknown", "friends", "Multiplayer Online Co-Op up to 6 players")
    mixed = build_html("Paid Mixed", "friends", "Multiplayer Online Co-Op up to 6 players", "Mixed")
    stub_app_pages(monkeypatch, {"601": unknown, "602": mixed})
    monkeypatch.setattr(main, "get_price_info", lambda app_id: (15.0, False))

    unknown_item = main.inspect_game("paid_candidate", "601")
    mixed_item = main.inspect_game("paid_candidate", "602")
    assert unknown_item is not None and mixed_item is not None
    assert unknown_item["review_gate_failed"] is True
    assert mixed_item["review_gate_failed"] is True


def test_demo_penalty_keeps_full_game_above_demo(monkeypatch):
    shared = "Multiplayer Online Co-Op up to 6 players party game Very Positive"
    full_html = build_html("Full Game", "friends", shared, "Very Positive")
    demo_html = build_html("Demo Game", "friends", shared, "Very Positive")
    stub_app_pages(monkeypatch, {"701": full_html, "702": demo_html})

    full_item = main.inspect_game("steam_free", "701")
    demo_item = main.inspect_game("steam_demo", "702")
    assert full_item is not None and demo_item is not None
    assert full_item["score"] > demo_item["score"]


def test_temporarily_free_bonus_requires_positive_or_better(monkeypatch):
    pos = build_html("Promo Positive", "friends", "100% off Multiplayer up to 6 players", "Positive")
    mixed = build_html("Promo Mixed", "friends", "100% off Multiplayer up to 6 players", "Mixed")
    stub_app_pages(monkeypatch, {"801": pos, "802": mixed})

    pos_item = main.inspect_game("steamdb_promo", "801")
    mixed_item = main.inspect_game("steamdb_promo", "802")
    assert pos_item is not None and mixed_item is not None
    assert pos_item["score"] >= mixed_item["score"] + main.TEMPORARILY_FREE_SCORE_BONUS


def test_single_player_weak_multiplayer_gets_penalty(monkeypatch):
    weak = build_html("Solo Leaning", "single-player narrative", "single-player Multiplayer", "Mostly Positive")
    strong = build_html("Strong MP", "friends game", "single-player Multiplayer Online Co-Op up to 6 players", "Mostly Positive")
    stub_app_pages(monkeypatch, {"901": weak, "902": strong})

    weak_item = main.inspect_game("steam_free", "901")
    strong_item = main.inspect_game("steam_free", "902")
    assert weak_item is not None and strong_item is not None
    assert weak_item["score"] < strong_item["score"]


def test_junk_keywords_and_titles_are_penalized(monkeypatch):
    junk = build_html(
        "Prototype Clicker Simulator Test",
        "idle meme game",
        "Multiplayer up to 6 players clicker idle prototype",
        "Mostly Positive",
    )
    clean = build_html("Team Party Ops", "friends game", "Multiplayer Online Co-Op up to 6 players", "Mostly Positive")
    stub_app_pages(monkeypatch, {"1001": junk, "1002": clean})

    junk_item = main.inspect_game("steam_free", "1001")
    clean_item = main.inspect_game("steam_free", "1002")
    assert junk_item is not None and clean_item is not None
    assert junk_item["score"] < clean_item["score"]


def test_coop_preferred_over_pvp_only(monkeypatch):
    coop = build_html("Coop Game", "friends", "Multiplayer Online Co-Op up to 6 players", "Mostly Positive")
    pvp = build_html("PvP Arena", "friends", "Multiplayer Online PvP up to 6 players", "Mostly Positive")
    stub_app_pages(monkeypatch, {"1101": coop, "1102": pvp})

    coop_item = main.inspect_game("steam_free", "1101")
    pvp_item = main.inspect_game("steam_free", "1102")
    assert coop_item is not None and pvp_item is not None
    assert coop_item["score"] > pvp_item["score"]


def test_trusted_profile_bonus_lifts_best_fit_games(monkeypatch):
    trusted = build_html(
        "Survival Squad",
        "team up with friends",
        "Very Positive Multiplayer Online Co-Op up to 6 players survival progression 20,000 reviews",
        "Very Positive",
    )
    weaker = build_html(
        "Generic Multiplayer",
        "team up with friends",
        "Mostly Positive Multiplayer Online PvP up to 4 players 20,000 reviews",
        "Mostly Positive",
    )
    stub_app_pages(monkeypatch, {"1201": trusted, "1202": weaker})

    trusted_item = main.inspect_game("steam_free", "1201")
    weaker_item = main.inspect_game("steam_free", "1202")
    assert trusted_item is not None and weaker_item is not None
    assert trusted_item["score"] > weaker_item["score"]
