import main


class FakeResponse:
    def __init__(self, app_id, text):
        self.url = f"https://store.steampowered.com/app/{app_id}/"
        self.text = text

    def raise_for_status(self):
        return None


def build_html(title: str, description: str, review_sentiment: str, feature_text: str) -> str:
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


def test_bad_review_free_games_are_rejected(monkeypatch):
    html = build_html(
        title="Noisy Co-op Game",
        description="Play with friends online.",
        review_sentiment="Mostly Negative",
        feature_text="Multiplayer Online Co-Op Up to 8 players",
    )
    stub_app_pages(monkeypatch, {"101": html})

    item = main.inspect_game("steam_free", "101")

    assert item is not None
    assert item["review_sentiment"] == "Mostly Negative"
    assert item["review_gate_failed"] is True
    assert item["keep"] is False


def test_paid_games_below_mostly_positive_are_rejected(monkeypatch):
    html = build_html(
        title="Paid Mixed Game",
        description="Online squad game.",
        review_sentiment="Mixed",
        feature_text="Multiplayer Online Co-Op Up to 8 players",
    )
    stub_app_pages(monkeypatch, {"202": html})
    monkeypatch.setattr(main, "get_price_info", lambda app_id: (14.99, False))

    item = main.inspect_game("paid_candidate", "202")

    assert item is not None
    assert item["type"] == "paid_under_20"
    assert item["review_sentiment"] == "Mixed"
    assert item["review_gate_failed"] is True
    assert item["keep"] is False


def test_strong_reviews_rank_above_weaker_reviews(monkeypatch):
    shared_features = "Multiplayer Online Co-Op Up to 8 players friends party action"
    html_good = build_html("Good Game", "Team up with friends", "Very Positive", shared_features)
    html_weak = build_html("Weak Game", "Team up with friends", "Mixed", shared_features)
    stub_app_pages(monkeypatch, {"301": html_good, "302": html_weak})

    good_item = main.inspect_game("steam_free", "301")
    weak_item = main.inspect_game("steam_free", "302")

    assert good_item is not None and weak_item is not None
    assert good_item["score"] > weak_item["score"]

    ranked = sorted(
        [good_item, weak_item],
        key=lambda x: (x["score"], x.get("review_score", 0)),
        reverse=True,
    )
    assert ranked[0]["id"] == "301"


def test_mixed_reviews_get_meaningful_penalty(monkeypatch):
    shared_features = "Multiplayer Online Co-Op Up to 8 players friends party action"
    html_mostly_positive = build_html(
        "Mostly Positive Game",
        "Play with friends online",
        "Mostly Positive",
        shared_features,
    )
    html_mixed = build_html(
        "Mixed Game",
        "Play with friends online",
        "Mixed",
        shared_features,
    )
    stub_app_pages(monkeypatch, {"401": html_mostly_positive, "402": html_mixed})

    mostly_positive = main.inspect_game("steam_free", "401")
    mixed = main.inspect_game("steam_free", "402")

    assert mostly_positive is not None and mixed is not None
    assert mostly_positive["review_score"] - mixed["review_score"] >= 8
    assert mostly_positive["score"] - mixed["score"] >= 8


def test_demo_does_not_outrank_equivalent_full_game(monkeypatch):
    shared_features = "Multiplayer Online Co-Op Up to 8 players friends party action"
    html_full = build_html("Full Game", "Play with friends online", "Very Positive", shared_features)
    html_demo = build_html("Demo Version", "Play with friends online", "Very Positive", shared_features)
    stub_app_pages(monkeypatch, {"501": html_full, "502": html_demo})

    full_game = main.inspect_game("steam_free", "501")
    demo_game = main.inspect_game("steam_demo", "502")

    assert full_game is not None and demo_game is not None
    assert full_game["type"] == "free_game"
    assert demo_game["type"] == "demo"
    assert full_game["score"] > demo_game["score"]


def test_temporarily_free_gets_only_modest_preference(monkeypatch):
    shared_features = "Multiplayer Online Co-Op Up to 8 players friends party action"
    html_regular = build_html("Regular Free", "Play with friends online", "Very Positive", shared_features)
    html_temp = build_html("Temp Free", "100% off play with friends online", "Very Positive", shared_features)
    stub_app_pages(monkeypatch, {"601": html_regular, "602": html_temp})

    regular = main.inspect_game("steam_free", "601")
    temporary = main.inspect_game("steamdb_promo", "602")

    assert regular is not None and temporary is not None
    assert regular["type"] == "free_game"
    assert temporary["type"] == "temporarily_free"
    assert temporary["score"] == regular["score"] + main.TEMPORARILY_FREE_SCORE_BONUS
