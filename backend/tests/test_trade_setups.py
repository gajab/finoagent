"""Tests for the trade-setup fusion engine (pure functions)."""
import pandas as pd

from app.services.trade_setup_service import (
    _collect_levels, _cluster_zones, _make_zone, _rr, _derive_bias, _build_setups,
    _snap, _option_play, compute_trade_setups,
    _equity_plan, _options_plan, _second_target, _edge, _event_risk, _candidate_plans,
)

# a realistic (whole + half dollar) strike board near spot 100
_STRIKES = [80, 85, 90, 92, 94, 95, 96, 97, 98, 99, 100, 101, 102, 104, 105, 108, 110, 112, 115, 120]


def _zone(center, kind, score=5.0, spot=100.0):
    return {"center": center, "low": center - 1, "high": center + 1, "kind": kind,
            "score": score, "n_sources": 3, "has_magnet": False,
            "sources": [{"label": "src", "price": center, "weight": score}],
            "distance_pct": round((center - spot) / spot * 100, 2)}


class TestCollectLevels:
    def test_dealer_levels_tagged_by_side(self):
        dealer = {"gamma_flip": {"level": 100}, "walls": {"call_wall": {"strike": 110}, "put_wall": {"strike": 90}},
                  "expected_move": {"em_30d": {"upper": 105, "lower": 95}}}
        levels = _collect_levels(None, None, None, dealer, spot=100.0)
        by = {l["label"]: l for l in levels}
        assert by["Call Resistance"]["kind"] == "resistance" and by["Put Support"]["kind"] == "support"
        assert "Gamma flip" in by and by["Gamma flip"]["kind"] == "magnet"

    def test_micro_poc_is_magnet(self):
        micro = {"timeframe_profiles": {"macro": {"poc": 101, "vah": 108, "val": 92, "lvns": []}},
                 "naked_pocs": [], "avwap": {}}
        levels = _collect_levels(micro, None, None, None, spot=100.0)
        labels = {l["label"]: l["kind"] for l in levels}
        assert labels.get("Macro POC") == "magnet"
        assert labels.get("Macro VAH") == "resistance" and labels.get("Macro VAL") == "support"


class TestClusterZones:
    def test_width_capped_no_runaway_chain(self):
        levels = [{"price": p, "kind": "support", "source": f"s{i}", "label": "x", "weight": 1.0}
                  for i, p in enumerate([100.0, 100.3, 100.6, 103.0, 103.2])]
        zones = _cluster_zones(levels, atr=1.0, spot=100.0)   # tol = 0.4
        centers = sorted(z["center"] for z in zones)
        assert len(zones) >= 2                                 # not one mega-zone
        assert any(abs(c - 100) < 1 for c in centers) and any(abs(c - 103) < 1 for c in centers)

    def test_position_tagging(self):
        assert _make_zone([{"price": 95, "kind": "support", "source": "a", "label": "x", "weight": 1}], 100.0, 3.0)["kind"] == "support"
        assert _make_zone([{"price": 110, "kind": "support", "source": "a", "label": "x", "weight": 1}], 100.0, 3.0)["kind"] == "resistance"
        assert _make_zone([{"price": 100.1, "kind": "support", "source": "a", "label": "x", "weight": 1}], 100.0, 3.0)["kind"] == "pivot"


class TestRR:
    def test_math(self):
        assert _rr(100, 95, 110) == 2.0
        assert _rr(100, 100, 110) is None       # zero risk


class TestDeriveBias:
    def test_directions(self):
        assert _derive_bias({"bias": {"overall": "bullish"}, "timeframes": {"daily": {"trend": "up"}}}, None, None)["direction"] == "bullish"
        assert _derive_bias({"bias": {"overall": "bearish"}, "timeframes": {"daily": {"trend": "down"}}}, None, None)["direction"] == "bearish"


class TestBuildSetups:
    def _regime(self, overall, z=0.2, vwap=100):
        return {"regime": {"overall": overall, "hurst_daily": 0.6, "playbook": "x", "favored": []},
                "zscore": {"z": z, "vwap": vwap}}

    def _dealer(self):
        return {"net_gex": {"sign": "long"}, "walls": {}, "expected_move": {"em_30d": {"move_pct": 5}}}

    def test_trend_continuation_long(self):
        bias = {"direction": "bullish", "strength": "strong", "regime": "trending", "rationale": "x"}
        zones = [_zone(95, "support", 6), _zone(108, "resistance", 5)]
        setups = _build_setups(bias, zones, 100.0, 3.0, self._dealer(), self._regime("trending"), 5, 100.0, _STRIKES)
        s = next(x for x in setups if x["type"] == "trend_continuation")
        assert s["direction"] == "long" and s["entry"]["level"] == 95
        assert s["stop"]["level"] < 95 and s["targets"][0]["level"] == 108 and s["risk_reward"] > 0

    def test_transitional_bias_still_yields_setup(self):
        bias = {"direction": "bullish", "strength": "strong", "regime": "transitional", "rationale": "x"}
        zones = [_zone(95, "support", 6), _zone(108, "resistance", 5)]
        setups = _build_setups(bias, zones, 100.0, 3.0, self._dealer(), self._regime("transitional"), 5, 100.0, _STRIKES)
        assert any(s["type"] == "trend_continuation" and s["direction"] == "long" for s in setups)

    def test_stretched_high_gives_fade_short(self):
        bias = {"direction": "neutral", "strength": "weak", "regime": "mean_reverting", "rationale": "x"}
        zones = [_zone(95, "support", 5), _zone(112, "resistance", 5)]
        setups = _build_setups(bias, zones, 100.0, 3.0, self._dealer(), self._regime("mean_reverting", z=2.2, vwap=101), 5, 101.0, _STRIKES)
        assert any(s["type"] == "mean_reversion_fade" and s["direction"] == "short" for s in setups)

    def test_neutral_transitional_falls_back_to_range(self):
        bias = {"direction": "neutral", "strength": "weak", "regime": "transitional", "rationale": "x"}
        zones = [_zone(95, "support", 5), _zone(108, "resistance", 5)]
        setups = _build_setups(bias, zones, 100.0, 3.0, self._dealer(), self._regime("transitional"), 5, 100.0, _STRIKES)
        assert setups and setups[0]["type"] == "range_bracket"


class TestOptionPlay:
    def test_snap_to_real_strikes_never_fractional(self):
        assert _snap(387.22, [385, 386, 387, 388, 389]) == 387        # nearest real strike (not 387.22)
        assert _snap(389.57, [388, 389, 390], "below") == 389         # long call just below spot
        assert _snap(392.87, [391, 393, 395], "above") == 393
        v = _snap(387.22, None)                                        # no chain → increment grid, never fractional
        assert v == round(v, 2) and (v * 2) % 1 == 0

    def test_bull_call_real_strikes_and_fortress_csp(self):
        # spot 100, target 108, the strongest support is the fortress at 90 (well below spot)
        dealer = {"walls": {"call_wall": {"strike": 110}, "put_wall": {"strike": 88}}}
        play = _option_play("trend_continuation", "long", 100.0, 108.0,
                            strong_support=90.0, strong_resistance=108.0, dealer=dealer, strikes=_STRIKES)
        st = play["strikes"]
        assert st["long_call"] in _STRIKES and st["short_call"] in _STRIKES and st["csp_put"] in _STRIKES
        assert st["long_call"] <= 100 and st["short_call"] > st["long_call"]
        assert st["csp_put"] <= 92                                     # CSP anchored at the fortress, not under spot


class TestEquityPlan:
    def test_sizing_and_rr(self):
        eq = _equity_plan("long", entry=100.0, stop=95.0, targets=[{"level": 110.0}], spot=100.0)
        assert eq["risk_per_share"] == 5.0 and eq["risk_reward"] == 2.0
        assert eq["suggested_shares"] == 50 and eq["dollar_risk"] == 250 and eq["dollar_reward_t1"] == 500
        assert eq["side"].startswith("Buy")

    def test_zero_risk_is_none(self):
        assert _equity_plan("long", 100.0, 100.0, [{"level": 110.0}], 100.0) is None


class TestOptionsPayoff:
    def test_bull_call_spread_payoff(self):
        op = {"structure": "Bull Call Spread / Cash-Secured Put", "strikes": {"long_call": 100.0, "short_call": 105.0}}
        quotes = {100.0: {"C": {"mid": 3.0, "iv": 0.30}}, 105.0: {"C": {"mid": 1.0, "iv": 0.30}}}
        plan = _options_plan(op, {"date": "2026-09-19", "dte": 30}, quotes, spot=100.0, dealer=None)
        assert plan["available"] and plan["net_cost_label"] == "debit"
        assert abs(plan["net_cost"] - 200) < 1                       # $2.00 debit × 100
        assert abs(plan["max_loss"] + 200) < 1 and abs(plan["max_profit"] - 300) < 1
        assert any(abs(b - 102.0) < 0.6 for b in plan["breakevens"])  # BE = long + debit


class TestEdge:
    def test_pop_ev_kelly(self):
        setup = {"direction": "long", "entry": {"level": 100}, "stop": {"level": 95}, "targets": [{"level": 110}],
                 "options_plan": {"available": True, "structure": "Bull Call Spread",
                                  "legs": [{"action": "Buy", "right": "Call"}], "breakevens": [102],
                                  "max_profit": 300, "max_loss": -200}}
        e = _edge(setup, 100.0, 0.30, 30)
        assert 0 <= e["equity"]["pop_pct"] <= 100 and "ev_per_share" in e["equity"]
        assert e["equity"]["kelly_pct"] is not None and e["equity"]["half_kelly_risk_pct"] <= 2.0
        assert 0 <= e["options"]["pop_pct"] <= 100 and "ev" in e["options"]

    def test_earnings_guard_flags_in_window(self):
        import datetime as dt
        soon = (dt.date.today() + dt.timedelta(days=10)).isoformat()
        exp = {"date": (dt.date.today() + dt.timedelta(days=30)).isoformat(), "dte": 30}
        er = _event_risk(soon, exp)
        assert er and er["type"] == "earnings" and er["in_days"] == 10
        # earnings AFTER expiry → no flag
        assert _event_risk((dt.date.today() + dt.timedelta(days=60)).isoformat(), exp) is None


class TestCandidatePlans:
    def test_debit_and_credit_offered_and_structurally_ranked(self):
        strikes = [285, 290, 295, 300, 305, 310, 315, 320, 325]
        quotes = {float(s): {"C": {"mid": max(0.2, 320 - s + 2), "iv": 0.30},
                             "P": {"mid": max(0.2, s - 290 + 2), "iv": 0.30}} for s in strikes}
        exp = {"date": "2026-09-19", "dte": 30}
        # a fade/pullback short → the CREDIT spread (sell premium at resistance) is primary
        fade = _candidate_plans("short", "trend_continuation", 305.0, 310.0, 295.0, 295.0, 310.0,
                                None, strikes, quotes, exp, 0.30)
        assert {c["structure"] for c in fade} == {"Bear Put Spread", "Call Credit Spread"}   # BOTH offered
        assert all(c["legs"] and c["pop_pct"] is not None for c in fade)
        assert fade[0]["kind"] == "credit"                                     # structural preference
        # a breakout short → the DEBIT spread's convexity is primary
        brk = _candidate_plans("short", "breakout", 305.0, 310.0, 295.0, 295.0, 310.0,
                               None, strikes, quotes, exp, 0.30)
        assert brk[0]["kind"] == "debit"


class TestFadeGuard:
    def test_trending_suppresses_stretch_fade(self):
        bias = {"direction": "neutral", "strength": "weak", "regime": "trending", "rationale": "x"}
        zones = [_zone(95, "support", 5), _zone(112, "resistance", 5)]
        dealer = {"net_gex": {"sign": "short"}, "walls": {}, "expected_move": {"em_30d": {"move_pct": 5}}}
        regime = {"regime": {"overall": "trending"}, "zscore": {"z": -2.5, "vwap": 101}}
        setups = _build_setups(bias, zones, 100.0, 3.0, dealer, regime, 5, 101.0, _STRIKES)
        assert not any(s["type"] == "mean_reversion_fade" for s in setups)     # don't fade a trend


class TestSecondTarget:
    def test_next_zone_beyond_t1(self):
        zones = [{"kind": "resistance", "center": 108.0}, {"kind": "resistance", "center": 115.0}]
        assert _second_target(zones, 100.0, "long", 108.0) == 115.0

    def test_measured_move_fallback(self):
        assert _second_target([], 100.0, "long", 108.0) == 116.0      # t1 + (t1-spot)


class TestIndicatorFoldIn:
    def test_indicators_add_confirmations(self):
        structure = {"bias": {"overall": "bullish"}, "timeframes": {"daily": {"trend": "up"}}}
        ind = {"currentRSI": 62, "macd": {"crossover": "bullish_crossover"},
               "movingAverages": {"priceVsSma50": "above", "goldenDeathCross": "golden_cross"}}
        b = _derive_bias(structure, None, None, ind)
        assert b["direction"] == "bullish" and b["strength"] in ("moderate", "strong")
        tags = {c["signal"] for c in b["confirmations"]}
        assert {"rsi", "macd", "sma_cross"} <= tags


class _NullStock:
    def history(self, period=None, interval=None, start=None, end=None):
        return pd.DataFrame()

    @property
    def options(self):
        return []

    @property
    def earnings_dates(self):
        raise RuntimeError("none")

    @property
    def earnings_history(self):
        raise RuntimeError("none")


class TestComputeTradeSetups:
    def test_no_data_returns_none(self):
        assert compute_trade_setups(_NullStock()) is None
