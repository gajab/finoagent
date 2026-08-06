"""Tests for the market-regime pure math."""
import numpy as np
import pandas as pd

from app.services.regime_service import (
    _hurst, _efficiency_ratio, _zscore_vwap, _classify, compute_regime,
)


def _series(kind: str, n: int = 800, seed: int = 1):
    rng = np.random.default_rng(seed)
    e = rng.standard_normal(n) * 0.01
    if kind == "rw":                       # iid returns → random walk price
        r = e
    elif kind == "trend":                  # positive autocorrelation → persistent
        r = np.zeros(n)
        for t in range(1, n):
            r[t] = 0.45 * r[t - 1] + e[t]
    else:                                  # negative autocorrelation → mean-reverting
        r = np.zeros(n)
        for t in range(1, n):
            r[t] = -0.45 * r[t - 1] + e[t]
    return 100 * np.exp(np.cumsum(r))


class TestHurst:
    def test_regime_ordering(self):
        h_rw = _hurst(_series("rw"))
        h_tr = _hurst(_series("trend"))
        h_mr = _hurst(_series("mr"))
        assert 0.4 < h_rw < 0.6                 # random walk ≈ 0.5
        assert h_tr > 0.55                       # trending persistent
        assert h_mr < h_rw and h_mr < 0.5        # mean-reverting below random walk

    def test_short_series_is_none(self):
        assert _hurst([1.0, 2.0, 3.0]) is None


class TestEfficiencyRatio:
    def test_clean_trend_high_choppy_low(self):
        ramp = np.linspace(100, 120, 60)                      # perfectly efficient
        chop = 100 + np.tile([0.0, 1.0], 30)                  # all path, no net move
        er_ramp = _efficiency_ratio(ramp)
        er_chop = _efficiency_ratio(chop)
        assert er_ramp > 0.9 and 0.0 <= er_chop < 0.1

    def test_bounds(self):
        er = _efficiency_ratio(np.random.default_rng(0).standard_normal(100).cumsum() + 100)
        assert er is None or 0.0 <= er <= 1.0


class TestZScoreVwap:
    def test_stretch_above_vwap_is_positive_z(self):
        c = np.array([100.0] * 59 + [130.0])           # a sharp recent over-expansion
        h, l, v = c + 0.5, c - 0.5, np.full(len(c), 1000.0)
        z = _zscore_vwap(h, l, c, v, window=50)
        assert z is not None and z["z"] > 0 and "high" in z["state"]

    def test_short_is_none(self):
        assert _zscore_vwap([1, 2, 3], [1, 2, 3], [1, 2, 3], [1, 1, 1]) is None


class TestClassify:
    def test_labels(self):
        assert _classify(0.65, 0.45)["regime"] == "trending"
        assert _classify(0.42, 0.10)["regime"] == "mean_reverting"
        assert _classify(0.50, 0.25)["regime"] == "transitional"


class _FakeStock:
    def __init__(self, df):
        self._df = df

    def history(self, period=None, interval=None, start=None, end=None):
        return self._df.copy()


class _EmptyStock:
    def history(self, period=None, interval=None, start=None, end=None):
        return pd.DataFrame()


def _synth_daily(n=300):
    idx = pd.date_range(end=pd.Timestamp.today().normalize(), periods=n, freq="D")
    t = np.arange(n)
    base = 100 + 12 * np.sin(t / 11.0) + t * 0.03
    return pd.DataFrame({"Open": base, "High": base + 1, "Low": base - 1, "Close": base,
                         "Volume": 1000 + np.abs(np.cos(t / 6.0)) * 3000}, index=idx)


class TestComputeRegime:
    def test_end_to_end_shape(self):
        out = compute_regime(_FakeStock(_synth_daily()))
        assert out is not None and out["price"] is not None
        assert out["regime"]["overall"] in ("trending", "mean_reverting", "transitional")
        assert "daily" in out["timeframes"] and "h4" in out["timeframes"]
        assert out["price_series"] and len(out["price_series"]["closes"]) > 20

    def test_no_data_returns_none(self):
        assert compute_regime(_EmptyStock()) is None
