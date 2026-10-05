"""Momentum Swing engine — gates, confluence scoring, setup sanity. No network.

A fake yfinance ``Ticker`` serves deterministic synthetic daily bars; ``yfinance.Ticker`` is
monkeypatched so the engine's internal SPY (relative-strength) fetch also hits the fake, and
``deep=False`` skips the best-effort dealer / regime-edge layers that would fetch options.
"""
import numpy as np
import pandas as pd
import pytest
import yfinance as yf

from app.services import momentum_service
from app.services.momentum_service import compute_momentum_setup


def _ohlc(closes: np.ndarray, vols: np.ndarray, seed: int = 1) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    n = len(closes)
    o = np.concatenate([[closes[0]], closes[:-1]])
    wig = np.abs(rng.normal(0, 0.003, n))
    h = np.maximum(o, closes) * (1.0 + wig)
    l = np.minimum(o, closes) * (1.0 - wig)
    idx = pd.date_range(end="2025-09-30", periods=n, freq="B", tz="America/New_York")
    return pd.DataFrame({"Open": o, "High": h, "Low": l, "Close": closes, "Volume": vols}, index=idx)


def _uptrend_leader(n: int = 520, seed: int = 7) -> pd.DataFrame:
    """A strong, sustained uptrend that ends in a tight low-volatility base near the highs."""
    rng = np.random.default_rng(seed)
    c = [10.0]
    for _ in range(n - 18):
        c.append(c[-1] * (1.0 + 0.004 + rng.normal(0, 0.009)))
    base = c[-1]
    for _ in range(17):                       # tight base (~17 bars) near the highs
        c.append(base * (1.0 + rng.normal(0, 0.004)))
    c = np.asarray(c, float)
    vols = np.concatenate([rng.integers(8_000_000, 12_000_000, n - 17).astype(float),
                           rng.integers(3_000_000, 5_000_000, 17).astype(float)])  # dry-up in the base
    return _ohlc(c, vols, seed)


def _downtrend(n: int = 520, seed: int = 9) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    c = [100.0]
    for _ in range(n - 1):
        c.append(max(1.0, c[-1] * (1.0 - 0.003 + rng.normal(0, 0.009))))
    c = np.asarray(c, float)
    vols = rng.integers(5_000_000, 9_000_000, n).astype(float)
    return _ohlc(c, vols, seed)


class _FakeTicker:
    def __init__(self, daily: pd.DataFrame):
        self._daily = daily

    def history(self, period=None, interval=None, **_):
        if interval and (interval.endswith("m") or interval.endswith("h")):
            return pd.DataFrame()                 # no intraday → _safe_history returns None
        return self._daily.copy()


@pytest.fixture(autouse=True)
def _patch_spy(monkeypatch):
    """Route the engine's internal yf.Ticker('SPY') to a flat benchmark so RS is measurable offline."""
    flat = _ohlc(np.full(400, 100.0) * (1.0 + np.linspace(0, 0.02, 400)),
                 np.full(400, 1_000_000.0), seed=3)
    monkeypatch.setattr(yf, "Ticker", lambda *_a, **_k: _FakeTicker(flat))


def _run(df):
    return compute_momentum_setup(_FakeTicker(df), deep=False)


def test_uptrend_leader_qualifies_with_live_setup():
    r = _run(_uptrend_leader())
    assert r is not None
    q = r["qualification"]
    assert q["gates"]["uptrend"] is True and q["gates"]["leadership"] is True
    assert q["score"] >= 40          # moderate+ confluence (deep=False drops the dealer pillar)
    assert r["setups"], "a trending leader in a tight base should produce a live setup"
    # a long momentum setup must have a coherent risk structure
    s = r["setups"][0]
    assert s["direction"] == "long" and s["style"] == "momentum"
    entry, stop = s["entry"]["level"], s["stop"]["level"]
    assert stop < entry
    levels = [t["level"] for t in s["targets"]]
    assert levels == sorted(levels) and all(t > entry for t in levels)
    assert s["risk_reward"] and s["risk_reward"] > 0


def test_pillars_within_bounds_and_score_normalized():
    r = _run(_uptrend_leader())
    for name, p in r["pillars"].items():
        assert 0.0 <= p["points"] <= p["max"], (name, p)
    assert 0 <= r["qualification"]["score"] <= 100


def test_checks_present_and_valid():
    r = _run(_uptrend_leader())
    checks = r["qualification"]["checks"]
    assert len(checks) >= 10
    for c in checks:
        assert c["status"] in ("pass", "warn", "fail")
        assert {"key", "label", "value", "ideal", "detail"} <= set(c)


def test_downtrend_rejected_by_uptrend_gate():
    r = _run(_downtrend())
    assert r is not None
    q = r["qualification"]
    assert q["gates"]["uptrend"] is False
    assert q["is_candidate"] is False
    assert "uptrend" in q["summary"].lower()
    # no long breakout/pullback setups when the uptrend gate fails
    assert all(s["type"] == "momentum_episodic_pivot" for s in r["setups"])


def test_rsi_is_continuation_aware():
    """A hot-but-trending name must not be failed on RSI alone (RSI>70 = continuation, not a sell)."""
    r = _run(_uptrend_leader())
    rsi_check = next(c for c in r["qualification"]["checks"] if c["key"] == "rsi")
    assert rsi_check["status"] in ("pass", "warn")   # never 'fail' for a strong uptrend


def test_determinism():
    df = _uptrend_leader()
    a, b = _run(df), _run(df)
    assert a["qualification"] == b["qualification"] and a["setups"] == b["setups"]
