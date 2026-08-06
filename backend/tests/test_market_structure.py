"""Tests for the multi-timeframe market-structure / liquidity pure math."""
import numpy as np
import pandas as pd

from app.services.market_structure_service import (
    _pivots, _structure, _liquidity_pools, _confluence, _mtf_bias,
    compute_market_structure,
)


class _FakeStock:
    def __init__(self, df):
        self._df = df

    def history(self, period=None, interval=None, start=None, end=None):
        return self._df.copy()


class _EmptyStock:
    def history(self, period=None, interval=None, start=None, end=None):
        return pd.DataFrame()


def _synth_hourly(n=600):
    idx = pd.date_range(end=pd.Timestamp.today().normalize(), periods=n, freq="h")
    t = np.arange(n)
    base = 100 + 8 * np.sin(t / 9.0) + t * 0.02
    return pd.DataFrame({
        "Open": base + np.where(t % 2 == 0, 0.2, -0.2),
        "High": base + 1.0, "Low": base - 1.0, "Close": base,
        "Volume": 1000 + np.abs(np.cos(t / 5.0)) * 4000,
    }, index=idx)


class TestPivots:
    def test_finds_local_extrema(self):
        # a sine has clear alternating peaks and troughs
        t = np.arange(120)
        wave = 100 + 5 * np.sin(t / 4.0)
        sh, sl = _pivots(wave + 0.5, wave - 0.5, order=2)
        assert len(sh) > 3 and len(sl) > 3

    def test_too_short_is_empty(self):
        assert _pivots([1, 2, 3], [1, 2, 3], order=5) == ([], [])


class TestStructure:
    def test_choch_marks_a_reversal(self):
        # rise + break a high (bull), then break the swing low → bearish CHOCH last
        closes = [100, 105, 110, 115, 118, 116, 112, 114, 120, 124, 122, 119, 116, 112, 108, 110, 107, 103, 100, 98]
        sh, sl = [(4, 118.0), (9, 124.0)], [(14, 108.0)]
        st = _structure(closes, sh, sl, order=1)
        assert st["last_event"]["type"] == "CHOCH" and st["last_event"]["direction"] == "bearish"
        assert any(e["direction"] == "bullish" for e in st["events"])   # the earlier upside break

    def test_bos_marks_continuation(self):
        # trend already up, then a close breaks a HIGHER high → BOS bullish
        closes = [100, 105, 110, 115, 118, 116, 119, 122, 120, 126, 124]
        sh, sl = [(4, 118.0), (7, 122.0)], [(5, 116.0)]
        st = _structure(closes, sh, sl, order=1)
        assert st["trend"] == "up"
        assert any(e["type"] == "BOS" and e["direction"] == "bullish" for e in st["events"])


class TestLiquidityPools:
    def test_unswept_bsl_swept_excluded_and_equal_highs_strong(self):
        # 118 high gets exceeded by a later close (swept → dropped); ~124 twin highs never
        # exceeded (unswept + engineered-equal → strong).
        closes = [120, 121, 119, 120]
        sh = [(0, 118.0), (1, 124.0), (2, 124.05)]
        sl = [(3, 110.0)]
        pools = _liquidity_pools(sh, sl, closes, atr=2.0, price=120.0)
        prices = [p["price"] for p in pools]
        assert all(abs(p - 118.0) > 0.5 for p in prices)          # swept BSL excluded
        bsl = next(p for p in pools if p["type"] == "BSL")
        assert bsl["swept"] is False and bsl["side"] == "above" and bsl["strength"] == "strong"
        assert any(p["type"] == "SSL" and p["side"] == "below" for p in pools)

    def test_no_price_is_safe(self):
        assert _liquidity_pools([(0, 10.0)], [(1, 5.0)], [7, 8], atr=1.0, price=0) == []


class TestConfluence:
    def test_one_zone_per_timeframe(self):
        zones = [
            {"tf": "daily", "tf_w": 3, "kind": "OB", "type": "bullish", "top": 105, "bottom": 100},
            {"tf": "h1", "tf_w": 1, "kind": "FVG", "type": "bullish", "top": 103, "bottom": 101},
            {"tf": "h1", "tf_w": 1, "kind": "FVG", "type": "bullish", "top": 104, "bottom": 102},
        ]
        cf = _confluence(zones)
        assert len(cf) == 1
        assert cf[0]["timeframes"] == ["daily", "h1"]     # sorted by weight desc
        assert cf[0]["score"] == 4                        # 3 + one h1 (NOT 5) — same-tf collapsed

    def test_non_overlapping_zones_yield_nothing(self):
        zones = [
            {"tf": "daily", "tf_w": 3, "kind": "OB", "type": "bullish", "top": 105, "bottom": 100},
            {"tf": "h1", "tf_w": 1, "kind": "FVG", "type": "bullish", "top": 90, "bottom": 88},
        ]
        assert _confluence(zones) == []


class TestMtfBias:
    def test_all_up_is_aligned_bullish(self):
        tfs = {k: {"trend": "up"} for k in ("daily", "h4", "h1")}
        b = _mtf_bias(tfs)
        assert b["overall"] == "bullish" and b["aligned"] is True and b["score"] == 6

    def test_conflict_is_mixed(self):
        tfs = {"daily": {"trend": "up"}, "h4": {"trend": "down"}, "h1": {"trend": "down"}}
        b = _mtf_bias(tfs)
        assert b["aligned"] is False


class TestComputeMarketStructure:
    def test_end_to_end_shape(self):
        out = compute_market_structure(_FakeStock(_synth_hourly()))
        assert out is not None
        assert out["price"] is not None
        assert out["bias"]["overall"] in ("bullish", "bearish", "mixed")
        assert set(out["timeframes"]) == {"daily", "h4", "h1"}
        assert isinstance(out["confluence"], list)
        assert out["price_series"] and len(out["price_series"]["closes"]) > 20

    def test_no_data_returns_none(self):
        assert compute_market_structure(_EmptyStock()) is None
