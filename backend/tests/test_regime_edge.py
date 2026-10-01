"""Regime-conditional edge engine — structural invariants + the trend-vs-chop contrast.

No network: a fake yfinance ``Ticker`` serves a deterministic synthetic daily series that alternates
trending runs and choppy ranges, so the ER-tercile buckets are populated and the contrast is real.
"""
import numpy as np
import pandas as pd

from app.services.regime_edge_service import compute_regime_edge, _REGIMES, _MIN_BUCKET_N


def _synthetic_daily(n_seg: int = 12, seg_len: int = 60, seed: int = 42) -> pd.DataFrame:
    """Alternating regime blocks: even = clean uptrend (high ER), odd = mean-reverting range (low ER)."""
    rng = np.random.default_rng(seed)
    closes: list[float] = []
    price = 100.0
    for k in range(n_seg):
        if k % 2 == 0:                                   # trending up, low noise
            for _ in range(seg_len):
                price *= (1.0 + 0.006 + rng.normal(0, 0.004))
                closes.append(price)
        else:                                            # choppy: revert around the block's base
            base = price
            for _ in range(seg_len):
                price = base * (1.0 + rng.normal(0, 0.013))
                closes.append(price)
    c = np.asarray(closes, dtype=float)
    n = len(c)
    o = np.concatenate([[c[0]], c[:-1]])
    wig = np.abs(rng.normal(0, 0.004, n))
    h = np.maximum(o, c) * (1.0 + wig)
    l = np.minimum(o, c) * (1.0 - wig)
    v = (rng.integers(1_000_000, 5_000_000, n)).astype(float)
    idx = pd.date_range(end="2024-12-31", periods=n, freq="B", tz="America/New_York")
    return pd.DataFrame({"Open": o, "High": h, "Low": l, "Close": c, "Volume": v}, index=idx)


class _FakeTicker:
    def __init__(self, daily: pd.DataFrame):
        self._daily = daily

    def history(self, period=None, interval=None, **_):
        if interval == "60m":                            # append_live_daily_bar's intraday probe
            return pd.DataFrame()                          # empty → no live-bar append, df unchanged
        return self._daily.copy()


def _result():
    return compute_regime_edge(_FakeTicker(_synthetic_daily()), history="max", horizon=10)


def test_shape_and_signals():
    r = _result()
    assert r is not None
    assert {s["key"] for s in r["signals"]} == {"breakout", "breakdown", "mean_rev_dip", "trend_pullback"}
    assert r["current_regime"]["regime"] in _REGIMES
    assert r["barrier"]["target_atr"] == 2.0 and r["barrier"]["stop_atr"] == 1.0
    assert r["read"] and isinstance(r["read"][0], str)


def test_reconciliation_per_regime_n_sums_to_overall():
    """math-accuracy invariant: any per-bucket count must sum to its aggregate."""
    r = _result()
    for s in r["signals"]:
        if not s["overall"]:
            continue
        tot = sum((s["by_regime"][rg]["n"] if s["by_regime"][rg] else 0) for rg in _REGIMES)
        assert tot == s["overall"]["n"], (s["key"], tot, s["overall"]["n"])


def test_buckets_are_balanced_by_terciles():
    """ER terciles must actually populate all three regimes (the fixed-Hurst bug left 'chop' empty)."""
    r = _result()
    totals = {rg: 0 for rg in _REGIMES}
    for s in r["signals"]:
        for rg in _REGIMES:
            st = s["by_regime"][rg]
            if st:
                totals[rg] += st["n"]
    for rg in _REGIMES:
        assert totals[rg] > 0, (rg, totals)


def test_no_lookahead_boundary():
    """Only occurrences with the full forward horizon are scored."""
    r = _result()
    n = r["meta"]["bars"]
    assert r["meta"]["evaluated_through"] == n - 1 - r["horizon"]


def test_stats_are_json_clean_and_sane():
    r = _result()
    for s in r["signals"]:
        for rg in _REGIMES:
            st = s["by_regime"][rg]
            if not st:
                continue
            assert 0 <= st["win_rate"] <= 100
            assert st["expectancy"] is None or np.isfinite(st["expectancy"])
            assert st["profit_factor"] is None or st["profit_factor"] >= 0
            assert isinstance(st["low_confidence"], bool)
            assert (st["n"] < _MIN_BUCKET_N) == st["low_confidence"]


def test_determinism():
    a, b = _result(), _result()
    assert a["signals"] == b["signals"] and a["current_regime"] == b["current_regime"]


def test_bucket_labels_are_honest():
    """The 'Trending' bucket must genuinely carry higher trend efficiency than 'Choppy' — otherwise
    the labels are cosmetic. Guaranteed by the ER-tercile construction; assert it holds end-to-end."""
    r = _result()
    checked = 0
    for s in r["signals"]:
        tr, ch = s["by_regime"]["trending"], s["by_regime"]["choppy"]
        if tr and ch and tr["avg_er"] is not None and ch["avg_er"] is not None:
            assert tr["avg_er"] > ch["avg_er"], (s["key"], tr["avg_er"], ch["avg_er"])
            checked += 1
    assert checked >= 1


def test_breakout_works_in_trending_and_edge_is_regime_dependent():
    """Momentum breakouts are profitable in the trending bucket of a trending series, and at least
    one signal shows a materially different edge across the regimes it fired in (the whole feature)."""
    r = _result()
    bo = next(s for s in r["signals"] if s["key"] == "breakout")
    tr = bo["by_regime"]["trending"]
    assert tr and tr["n"] >= _MIN_BUCKET_N
    assert tr["expectancy"] > 0, tr["expectancy"]

    # some signal's expectancy spans a real gap between the regimes it actually fired in (n>=5)
    max_spread = 0.0
    for s in r["signals"]:
        exps = [s["by_regime"][rg]["expectancy"] for rg in _REGIMES
                if s["by_regime"][rg] and s["by_regime"][rg]["n"] >= 5 and s["by_regime"][rg]["expectancy"] is not None]
        if len(exps) >= 2:
            max_spread = max(max_spread, max(exps) - min(exps))
    assert max_spread >= 0.3, max_spread
