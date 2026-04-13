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
    monkeypatch.setattr(main, "get_price_info", lambda app_id: (9.99, False, 0, "game"))

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
    assert player_score == 4


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


def test_review_count_confidence_bonus_applies_to_mostly_positive(monkeypatch):
    low_count = build_html(
        "Low Count Mostly Positive",
        "team up with friends",
        "Multiplayer Online Co-Op up to 6 players Mostly Positive 90 reviews",
        "Mostly Positive",
    )
    high_count = build_html(
        "High Count Mostly Positive",
        "team up with friends",
        "Multiplayer Online Co-Op up to 6 players Mostly Positive 15,000 reviews",
        "Mostly Positive",
    )
    stub_app_pages(monkeypatch, {"503": low_count, "504": high_count})

    low_item = main.inspect_game("steam_free", "503")
    high_item = main.inspect_game("steam_free", "504")
    assert low_item is not None and high_item is not None
    assert high_item["score"] >= low_item["score"] + 2


def test_paid_rejects_unknown_and_mixed(monkeypatch):
    unknown = build_html("Paid Unknown", "friends", "Multiplayer Online Co-Op up to 6 players")
    mixed = build_html("Paid Mixed", "friends", "Multiplayer Online Co-Op up to 6 players", "Mixed")
    stub_app_pages(monkeypatch, {"601": unknown, "602": mixed})
    monkeypatch.setattr(main, "get_price_info", lambda app_id: (15.0, False, 0, "game"))

    unknown_item = main.inspect_game("paid_candidate", "601")
    mixed_item = main.inspect_game("paid_candidate", "602")
    assert unknown_item is not None and mixed_item is not None
    assert unknown_item["review_gate_failed"] is True
    assert mixed_item["review_gate_failed"] is True


def test_demo_pick_scoring_tracks_friend_group_signals(monkeypatch):
    shared = "Multiplayer Online Co-Op up to 6 players party game Very Positive Download Demo"
    full_html = build_html("Full Game", "friends", shared, "Very Positive")
    demo_html = build_html("Demo Game", "friends", shared, "Very Positive")
    stub_app_pages(monkeypatch, {"701": full_html, "702": demo_html})

    full_item = main.inspect_game("steam_free", "701")
    demo_item = main.inspect_game("steam_demo", "702")
    assert full_item is not None and demo_item is not None
    assert demo_item["demo_friend_signal_score"] >= main.DEMO_PLAYTEST_MIN_FRIEND_SIGNAL
    assert demo_item["keep"] is True


def test_demo_and_playtest_detected_and_routed(monkeypatch):
    demo_html = build_html("Demo Game", "friends", "Multiplayer Online Co-Op up to 6 players", "Mostly Positive")
    playtest_html = build_html("Squad Rush Playtest", "team up", "Multiplayer squad up to 6 players", "")
    stub_app_pages(monkeypatch, {"730": demo_html, "731": playtest_html})

    demo_item = main.inspect_game("steam_demo", "730")
    playtest_item = main.inspect_game("steam_demo", "731")

    assert demo_item is not None and playtest_item is not None
    assert demo_item["type"] == "demo"
    assert playtest_item["type"] == "playtest"


def test_demo_group_play_fit_beats_solo_demo(monkeypatch):
    group_html = build_html(
        "Group Demo",
        "team up with friends",
        "Multiplayer Online Co-Op up to 6 players squad loot runs progression Download Demo",
        "",
    )
    solo_html = build_html(
        "Solo Story Demo",
        "single-player narrative",
        "single-player only story-rich demo",
        "",
    )
    stub_app_pages(monkeypatch, {"740": group_html, "741": solo_html})

    group_item = main.inspect_game("steam_demo", "740")
    solo_item = main.inspect_game("steam_demo", "741")
    assert group_item is not None and solo_item is not None
    assert group_item["keep"] is True
    assert group_item["demo_friend_signal_score"] > solo_item["demo_friend_signal_score"]
    assert solo_item["keep"] is False


def test_demo_legit_playable_cues_help_score(monkeypatch):
    with_cues = build_html(
        "Cue Demo",
        "team up with friends",
        "Multiplayer Online Co-Op up to 6 players demo available request access play now",
        "",
    )
    without_cues = build_html(
        "Base Demo",
        "team up with friends",
        "Multiplayer Online Co-Op up to 6 players",
        "",
    )
    stub_app_pages(monkeypatch, {"742": with_cues, "743": without_cues})

    cue_item = main.inspect_game("steam_demo", "742")
    base_item = main.inspect_game("steam_demo", "743")
    assert cue_item is not None and base_item is not None
    assert cue_item["score"] > base_item["score"]
    assert any(hit.startswith("playable-cue:") for hit in cue_item["demo_hits"])


def test_demo_missing_reviews_more_tolerant_than_free_game(monkeypatch):
    demo_text = "Multiplayer Online Co-Op up to 6 players party game loot runs Demo Available"
    free_text = "Multiplayer Online Co-Op up to 6 players party game loot runs"
    demo_html = build_html("Co-op Demo", "friends", demo_text, "")
    free_html = build_html("Co-op Full", "friends", free_text, "")
    stub_app_pages(monkeypatch, {"750": demo_html, "751": free_html})

    demo_item = main.inspect_game("steam_demo", "750")
    free_item = main.inspect_game("steam_free", "751")

    assert demo_item is not None and free_item is not None
    assert demo_item["keep"] is True
    assert free_item["keep"] is False


def test_demo_newness_bonus_is_small_and_optional(monkeypatch):
    recent_text = "Release Date: Apr 01, 2026 Multiplayer Online Co-Op up to 6 players Request Access"
    older_text = "Release Date: Jan 01, 2024 Multiplayer Online Co-Op up to 6 players squad loot runs progression Join Playtest"
    recent_html = build_html("Recent Demo", "friends", recent_text, "")
    older_html = build_html("Older Strong Demo", "team up with friends", older_text, "")
    stub_app_pages(monkeypatch, {"760": recent_html, "761": older_html})

    recent_item = main.inspect_game("steam_demo", "760")
    older_item = main.inspect_game("steam_demo", "761")

    assert recent_item is not None and older_item is not None
    assert recent_item["demo_freshness_bonus"] >= 1
    assert older_item["demo_freshness_bonus"] == 0
    assert older_item["keep"] is True


def test_paid_game_with_download_demo_signal_allowed_in_demo_playtest(monkeypatch):
    html = build_html(
        "Paid Game Demo",
        "team up with friends",
        "Multiplayer Online Co-Op up to 6 players Download Demo",
        "Mostly Positive",
    )
    stub_app_pages(monkeypatch, {"900": html})

    item = main.inspect_game("steam_demo", "900")
    assert item is not None
    assert item["type"] == "demo"
    assert item["demo_has_free_to_try_signal"] is True
    assert item["keep"] is True


def test_vr_game_detected_from_tags_is_excluded(monkeypatch):
    html = """
    <html>
      <head><meta property="og:title" content="VR Party" /></head>
      <body>
        <div id="appHubAppName">VR Party</div>
        <div class="game_description_snippet">Team up with friends</div>
        <div class="glance_tags popular_tags">
          <a class="app_tag">Virtual Reality</a>
          <a class="app_tag">Multiplayer</a>
        </div>
      </body>
    </html>
    """
    stub_app_pages(monkeypatch, {"910": html})

    item = main.inspect_game("steam_demo", "910")
    assert item is None


def test_vr_game_detected_from_description_is_excluded(monkeypatch):
    html = build_html(
        "VR Party",
        "Team up in virtual reality",
        "Multiplayer Online Co-Op up to 6 players Download Demo",
        "Very Positive",
    )
    stub_app_pages(monkeypatch, {"911": html})

    item = main.inspect_game("steam_demo", "911")
    assert item is None


def test_playtest_request_access_or_join_playtest_allowed(monkeypatch):
    request_access = build_html(
        "Squad Test Playtest",
        "team up",
        "Multiplayer squad up to 6 players Request Access",
        "",
    )
    join_playtest = build_html(
        "Squad Test Playtest 2",
        "team up",
        "Multiplayer squad up to 6 players Join Playtest",
        "",
    )
    stub_app_pages(monkeypatch, {"901": request_access, "902": join_playtest})

    request_item = main.inspect_game("steam_demo", "901")
    join_item = main.inspect_game("steam_demo", "902")

    assert request_item is not None and join_item is not None
    assert request_item["type"] == "playtest"
    assert join_item["type"] == "playtest"
    assert request_item["demo_has_free_to_try_signal"] is True
    assert join_item["demo_has_free_to_try_signal"] is True
    assert request_item["keep"] is True
    assert join_item["keep"] is True


def test_paid_game_with_demo_wording_but_without_access_signal_excluded(monkeypatch):
    html = build_html(
        "Upcoming Paid Demo",
        "wishlist now",
        "Multiplayer Online Co-Op up to 6 players demo coming soon",
        "Mostly Positive",
    )
    stub_app_pages(monkeypatch, {"903": html})

    item = main.inspect_game("steam_demo", "903")
    assert item is not None
    assert item["type"] == "demo"
    assert item["demo_has_free_to_try_signal"] is False
    assert item["keep"] is False


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


def test_vertical_slice_and_proof_of_concept_are_penalized(monkeypatch):
    junk = build_html(
        "Prototype Arena",
        "proof of concept build",
        "Multiplayer Online Co-Op up to 6 players vertical slice placeholder",
        "Mostly Positive",
    )
    clean = build_html(
        "Team Arena",
        "friends game",
        "Multiplayer Online Co-Op up to 6 players",
        "Mostly Positive",
    )
    stub_app_pages(monkeypatch, {"1003": junk, "1004": clean})

    junk_item = main.inspect_game("steam_free", "1003")
    clean_item = main.inspect_game("steam_free", "1004")
    assert junk_item is not None and clean_item is not None
    assert junk_item["score"] < clean_item["score"]


def test_demo_replayability_terms_help_when_friend_fit_exists(monkeypatch):
    replayable = build_html(
        "Replay Demo",
        "team up with friends",
        "Multiplayer Online Co-Op up to 6 players runs loot progression procedural replayable",
        "",
    )
    basic = build_html(
        "Basic Demo",
        "team up with friends",
        "Multiplayer Online Co-Op up to 6 players",
        "",
    )
    stub_app_pages(monkeypatch, {"1005": replayable, "1006": basic})

    replay_item = main.inspect_game("steam_demo", "1005")
    basic_item = main.inspect_game("steam_demo", "1006")
    assert replay_item is not None and basic_item is not None
    assert replay_item["score"] > basic_item["score"]


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


def test_keyword_stuffing_penalty_for_mixed_reviews(monkeypatch):
    text = "Multiplayer Online Co-Op Co-op squad team-based online pvp up to 6 players"
    mixed_score, mixed_hits = main.score_quality_refinements(
        title="Stuffed Keywords",
        description="friends game",
        text=text,
        review_sentiment="Mixed",
        review_count=200,
        multiplayer_score=8,
        player_score=4,
    )
    positive_score, _ = main.score_quality_refinements(
        title="Stuffed Keywords",
        description="friends game",
        text=text,
        review_sentiment="Mostly Positive",
        review_count=200,
        multiplayer_score=8,
        player_score=4,
    )

    assert "keyword-stuffing-weak-review" in mixed_hits
    assert mixed_score <= positive_score - 2


def test_discount_scoring_boosts_paid_games(monkeypatch):
    html = build_html(
        "Discounted Party",
        "friends game",
        "Multiplayer Online Co-Op up to 6 players",
        "Very Positive",
    )
    stub_app_pages(monkeypatch, {"1301": html, "1302": html})

    # 50% discount -> +3
    monkeypatch.setattr(main, "get_price_info", lambda app_id: (10.0, False, 50, "game") if app_id == "1301" else (10.0, False, 0, "game"))

    discounted_item = main.inspect_game("paid_candidate", "1301")
    regular_item = main.inspect_game("paid_candidate", "1302")

    assert discounted_item is not None and regular_item is not None
    assert discounted_item["score"] == regular_item["score"] + 3


def test_dlc_hard_excluded_from_paid_section(monkeypatch):
    html = build_html(
        "DLC Pack",
        "expansion content",
        "Multiplayer Online Co-Op up to 6 players",
        "Very Positive",
    )
    stub_app_pages(monkeypatch, {"1401": html})

    # DLC type should be excluded
    monkeypatch.setattr(main, "get_price_info", lambda app_id: (15.0, False, 0, "dlc"))

    item = main.inspect_game("paid_candidate", "1401")
    assert item is None  # DLC should be excluded
