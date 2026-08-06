"""Tests for the trade-setup fusion engine (pure functions)."""
import pandas as pd

from app.services.trade_setup_service import (
    _collect_levels, _cluster_zones, _make_zone, _rr, _derive_bias, _build_setups,
    _snap, _option_play, compute_trade_setups,
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
        assert by["Call wall"]["kind"] == "resistance" and by["Put wall"]["kind"] == "support"
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
