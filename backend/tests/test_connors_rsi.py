"""Tests for the Larry Connors 2-Period RSI mean-reversion engine (synthetic frames)."""
import json

import numpy as np
import pandas as pd

from app.services.connors_rsi_service import compute_connors_setups, _rsi_series, _vix_read


def _daily(prices, wig=0.01, start="2024-06-01"):
    n = len(prices)
    idx = pd.date_range(start, periods=n, freq="B")
    c = np.asarray(prices, float)
    o = np.concatenate([[c[0]], c[:-1]])
    return pd.DataFrame({"Open": o, "High": c * (1 + wig), "Low": c * (1 - wig), "Close": c,
                         "Volume": np.full(n, 1e6)}, index=idx)


def _vix(level, base=15.0, n=30):
    p = list(np.full(n - 1, base)) + [level]
    idx = pd.date_range("2024-06-01", periods=n, freq="B")
    return pd.DataFrame({"Close": p}, index=idx)


class _Fake:
    def __init__(self, d1):
        self._d1 = d1

    def history(self, period=None, interval=None, **kw):
        return self._d1 if interval == "1d" else None


class TestSignal:
    def test_long_fires_above_200sma_when_rsi2_oversold(self):
        prices = list(np.linspace(80, 120, 246)) + [118, 115, 112, 110]   # uptrend + sharp dip
        out = compute_connors_setups(_Fake(_daily(prices)), vix_df=_vix(16))
        sig = out["signal"]
        assert out["indicators"]["rsi2"] < 10 and out["price"] > out["indicators"]["sma200"]
        assert sig["tone"] == "buy" and sig["armed"]
        longs = [s for s in out["setups"] if s["direction"] == "long"]
        assert longs, "an oversold pullback above the 200-SMA must arm a long"
        s = longs[0]
        assert s["style"] == "connors_rsi2"
        assert s["stop"]["level"] < s["entry"]["level"] < s["targets"][0]["level"]   # long geometry
        assert s["entry_style"]["type"] == "market_on_close"                          # close-based entry
        assert {c["key"] for c in sig["checks"]} == {"trend_200", "rsi2", "pullback_5sma", "cum_rsi", "vix_fear"}

    def test_short_fires_below_200sma_when_rsi2_overbought(self):
        prices = list(np.linspace(140, 90, 246)) + [92, 95, 98, 100]      # downtrend + bounce
        out = compute_connors_setups(_Fake(_daily(prices)), vix_df=_vix(16))
        assert out["indicators"]["rsi2"] > 90 and out["price"] < out["indicators"]["sma200"]
        shorts = [s for s in out["setups"] if s["direction"] == "short"]
        assert shorts and shorts[0]["style"] == "connors_rsi2"
        s = shorts[0]
        assert s["targets"][0]["level"] < s["entry"]["level"] < s["stop"]["level"]   # short geometry

    def test_no_signal_when_not_oversold(self):
        # uptrend that is NOT oversold (RSI-2 not < 10) above the 200-SMA → no actionable Connors trade
        prices = list(np.linspace(100, 130, 250)) + [129, 130, 129.5, 130.5]
        out = compute_connors_setups(_Fake(_daily(prices)), vix_df=_vix(15))
        assert out["indicators"]["rsi2"] >= 10            # not in the buy zone
        assert not out["setups"]                          # ⇒ no setup fires
        assert out["signal"]["tone"] in ("flat", "watch") and not out["signal"]["armed"]


class TestVix:
    def test_flat_vix_is_not_a_fear_spike(self):
        # regression: a perfectly flat VIX must read RSI(2)=50 and fear_spike=False (not 100/True)
        v = _vix_read(_vix(15))
        assert v is not None and v["rsi2"] == 50.0 and v["fear_spike"] is False

    def test_stretched_vix_is_a_fear_spike_and_arms_a_panic_buy(self):
        v = _vix_read(_vix(34))
        assert v["fear_spike"] is True and v["pct_above_sma10"] > 8
        # a shallower dip (RSI2 in 10–25) + a VIX fear spike arms a relaxed "panic" long
        prices = list(np.linspace(80, 120, 247)) + [118, 116.5, 115.5]
        out = compute_connors_setups(_Fake(_daily(prices)), vix_df=_vix(34))
        assert (out["vix"] or {})["fear_spike"] is True
        assert any(s["direction"] == "long" for s in out["setups"])


class TestBacktestAndExecution:
    def test_backtest_reports_trades_on_an_oscillating_series(self):
        prices = list(120 + 10 * np.sin(np.linspace(0, 40, 320)))   # oscillates → repeated RSI-2 signals
        out = compute_connors_setups(_Fake(_daily(prices)), vix_df=_vix(15))
        bt = out["backtest"]
        assert bt["trades"] >= 1
        assert bt["win_rate_pct"] is not None and 0 <= bt["win_rate_pct"] <= 100

    def test_execution_block_is_side_aware(self):
        long_out = compute_connors_setups(_Fake(_daily(list(np.linspace(80, 120, 246)) + [118, 115, 112, 110])), vix_df=_vix(15))
        assert "≤" in long_out["execution"]["recommended_order"]
        short_out = compute_connors_setups(_Fake(_daily(list(np.linspace(140, 90, 246)) + [92, 95, 98, 100])), vix_df=_vix(15))
        assert "≥" in short_out["execution"]["recommended_order"]


class TestRobustness:
    def test_rsi_series_edge_cases(self):
        assert _rsi_series(pd.Series([10.0] * 10), 2).iloc[-1] == 50.0       # flat → neutral
        assert _rsi_series(pd.Series(np.linspace(10, 20, 10)), 2).iloc[-1] == 100.0   # all gains → 100

    def test_payload_json_serializable(self):
        out = compute_connors_setups(_Fake(_daily(list(np.linspace(80, 120, 246)) + [118, 115, 112, 110])), vix_df=_vix(20))
        json.dumps(out)

    def test_too_little_history_returns_none(self):
        assert compute_connors_setups(_Fake(_daily(list(np.linspace(10, 12, 40)))), vix_df=None) is None
