"""Unit tests for the shared correlated-assets registry (pure functions — no network)."""
import app.services.correlated_assets_service as cas


def test_theme_membership():
    assert "crypto" in cas.themes_for("COIN")
    assert "gold_complex" in cas.themes_for("GDX")
    nvda = cas.themes_for("NVDA")
    assert "ai_datacenter" in nvda and "semis" in nvda
    assert cas.themes_for("ZZZZ_NOT_A_TICKER") == []


def test_theme_peers_shared_knowledge():
    coin = cas.theme_peers("COIN")
    assert "MSTR" in coin and "HOOD" in coin and "COIN" not in coin      # excludes self
    gdx = cas.theme_peers("GDX")
    assert "GDXJ" in gdx and "GLD" in gdx                                 # gold complex incl. the driver


def test_gold_complex_dependency_chain():
    # GDX / GDXJ track GLD — the theme records GLD as the driver ticker.
    assert cas._THEMES["gold_complex"]["driver_ticker"] == "GLD"


def test_themes_are_well_formed():
    for key, t in cas._THEMES.items():
        assert t["tickers"] and t["label"] and t["driver"] and t["bellwether"], key
        assert t["macro"], key
        # bellwether should be a real member of its own theme (so related-earnings resolves)
        assert cas._norm(t["bellwether"]) in {cas._norm(x) for x in t["tickers"]}, key


def test_macro_proxies_cover_the_asked_factors():
    factors = {m["factor"] for m in cas._MACRO_PROXIES}
    # the user's named macro axes: rates, oil, gold, USD — plus crypto / AI-capex / market
    assert {"rates", "oil", "gold", "usd", "crypto", "ai_capex", "market"} <= factors
    for m in cas._MACRO_PROXIES:
        assert m["proxy"] and m["label"]


def test_pearson_matches_expectations():
    a = [0.01, -0.02, 0.03, -0.01, 0.02, 0.0, -0.015, 0.025] * 6
    assert cas.pearson(a, a) == 1.0
    assert cas.pearson(a, [-x for x in a]) == -1.0
    assert cas.pearson(a[:10], a[:10]) is None      # below the 40-point window
