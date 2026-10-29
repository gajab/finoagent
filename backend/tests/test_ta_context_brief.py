"""Direction/LVN are CONTEXT, not grade: info-only factors are tagged, LVN slip is 0 pts and honest about strikes
outside the volume profile, and the pre-trade TA brief is deterministic from the desk's own TA read."""
from app.services.desk_review_service import _lvn_check, _ta_brief

BINS = [{"price": 90.0 + i, "volume": 1, "pct": p} for i, p in enumerate([2, 6, 6, 6, 1, 6, 6, 6, 6, 6, 2])]  # 90..100
VP = {"poc": 95.0, "val": 92.0, "vah": 98.0, "bins": BINS}


def _opp(k):
    return {"legs": [{"action": "SELL", "strike": k}]}


def test_lvn_in_range_is_info_only():
    r = _lvn_check(_opp(94.0), VP, 97.0)                 # 94 sits in the 1% node
    assert r["points"] == 0 and "low-volume node" in r["note"] and r["note"].startswith("info only")


def test_lvn_strike_outside_profile_says_so_instead_of_reading_the_edge_bin():
    r = _lvn_check(_opp(80.0), VP, 97.0)                 # 10 below the traded range
    assert r["points"] == 0 and "beyond the recent traded range" in r["note"]


def test_lvn_heavy_node_no_note():
    assert _lvn_check(_opp(96.0), VP, 97.0) is None


TA = {
    "timeframeLabel": "Position (3mo / 1d)", "_trend_label": "Medium Term (6mo / 1d)", "_drift_mu": 0.6,
    "_trend_regime": {"state": "Trend-Following", "bias": "bullish"},
    "movingAverages": {"sma50": 90.0, "sma200": 80.0, "priceVsSma50": "above", "priceVsSma200": "above",
                       "goldenDeathCross": "golden_cross"},
    "emaCrossover": {"signal": "bullish"},
    "macd": {"histogram": -2.0, "histogramValues": [1.0, 0.5, -1.0, -2.0], "crossover": "bearish_crossover"},
    "currentRSI": 72.0,
    "institutional": {"volume_profile": VP, "market_structure": {"trend": "up", "recent_swing_low": 88.0,
                                                                 "recent_swing_high": 101.0}},
    "_patterns": [{"name": "Double Bottom", "direction": "bullish", "status": "forming", "breakout": 99.0,
                   "target": 108.0}],
    "_atr_pct": 2.5, "bollingerBands": {"percentB": 0.8, "bandwidthPct": 12},
}


def test_brief_covers_direction_momentum_profile_structure_patterns():
    b = _ta_brief(TA, 97.0, 30)
    by = {i["key"]: i for i in b["items"]}
    assert set(by) >= {"direction", "momentum", "volume_profile", "structure", "patterns", "tape"}
    assert by["direction"]["tone"] == "bull" and "+60%/yr ≈ +4.9% over 30d" in by["direction"]["text"]
    m = by["momentum"]["text"]
    assert "MACD below its signal line" in m and "bearish momentum building" in m and "fresh bearish crossover" in m
    assert "RSI 72 (overbought)" in m
    assert "inside the value area" in by["volume_profile"]["text"] and "$94 (-3.1%)" in by["volume_profile"]["text"]
    assert "Double Bottom" in by["patterns"]["text"] and "target $108" in by["patterns"]["text"]
    assert "none of this moves the grade" in b["note"]


def test_brief_survives_empty_ta():
    assert _ta_brief({}, None, None)["items"] == []
