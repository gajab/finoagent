"""Tests for the intraday day-trade engine (synthetic single-session frames)."""
import numpy as np
import pandas as pd

from app.services.day_trade_service import compute_day_trade_setups


def _intraday(prices, day="2026-09-14 09:30", freq="5min", wig=0.05):
    n = len(prices)
    idx = pd.date_range(day, periods=n, freq=freq)
    c = np.asarray(prices, float)
    return pd.DataFrame({"Open": c, "High": c + wig, "Low": c - wig, "Close": c,
                         "Volume": np.full(n, 1000.0)}, index=idx)


class _FakeStock:
    """Serves synthetic frames by interval so compute_day_trade_setups runs offline."""
    def __init__(self, d5, d15, daily):
        self._by = {"5m": d5, "15m": d15, "60m": d15, "1d": daily}

    def history(self, period=None, interval=None, start=None, end=None, **kw):
        return self._by.get(interval, self._by["5m"])


def _rising_session():
    # one RTH session grinding 100 → 106 (ends well above its VWAP ≈ 103)
    p5 = list(np.linspace(100, 106, 78))
    p15 = list(np.linspace(100, 106, 26))
    daily = _intraday([98, 100], day="2026-09-11", freq="1D", wig=1.0)   # prior-day H/L context
    return _FakeStock(_intraday(p5), _intraday(p15), daily)


class TestDayTrade:
    def test_uptrend_above_vwap_yields_a_long_not_empty(self):
        # the GLD bug: a clean intraday uptrend above a rising VWAP must produce a LONG,
        # not nothing (old logic blocked it on a multi-day 15m 'down' trend).
        out = compute_day_trade_setups(_rising_session())
        assert out and out["setups"], "an intraday uptrend above VWAP must yield at least one setup"
        longs = [s for s in out["setups"] if s["direction"] == "long"]
        assert longs, "expected a long-side day trade in an uptrend"
        s = longs[0]
        assert s["style"] == "day"
        assert s["entry"]["level"] <= out["price"]            # buy at/at-pullback below the extended price
        assert s["targets"][0]["level"] > s["entry"]["level"]  # target above entry
        assert s["risk_reward"] and s["risk_reward"] >= 1.0

    def test_extended_move_is_a_pullback_not_a_chase(self):
        # price ends ~3% above VWAP → engine should say buy the PULLBACK toward VWAP, not chase spot
        out = compute_day_trade_setups(_rising_session())
        s = next((x for x in out["setups"] if x["direction"] == "long"), None)
        assert s and s["entry"]["level"] < out["price"]        # entry is below the current (extended) price
