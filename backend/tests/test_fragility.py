"""Trend-vs-Fragility overlay: calibration curve, study-identical definitions, sizing math, fragile-state rules.

No network: synthetic daily bars; yfinance.Ticker is monkeypatched for the Momentum engine's internal SPY fetch.
"""
import numpy as np
import pandas as pd
import pytest
import yfinance as yf

from app.services import fragility_service as F
from app.services.fragility_service import apply_risk_overlay, compute_fragility, gap_overshoot
from app.services.trade_setup_service import _RISK_BUDGET, _equity_plan


def _frame(c: np.ndarray, seed: int = 0, wig: float = 0.004) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    o = np.concatenate([[c[0]], c[:-1]])
    w = np.abs(rng.normal(0, wig, len(c)))
    idx = pd.date_range(end="2025-06-30", periods=len(c), freq="B", tz="America/New_York")
    return pd.DataFrame({"Open": o, "High": np.maximum(o, c) * (1 + w), "Low": np.minimum(o, c) * (1 - w),
                         "Close": c, "Volume": rng.integers(2e6, 4e6, len(c)).astype(float)}, index=idx)


def _calm(n=420, seed=1):
    rng = np.random.default_rng(seed)
    c = 50.0 * np.cumprod(1 + 0.0015 + rng.normal(0, 0.008, n))
    return _frame(c, seed)


def _blowoff(n=420, seed=2):
    """A long orderly uptrend, then a violent parabolic final leg (LITE-like)."""
    rng = np.random.default_rng(seed)
    r = list(0.0025 + rng.normal(0, 0.015, n - 25))
    r += list(0.012 + rng.normal(0, 0.045, 25))
    r[-15] = 0.09                                   # a lottery-like one-day pop inside the last 21 sessions
    r[-9:] = [0.03, 0.025, 0.02, 0.035, 0.015, 0.03, 0.02, 0.025, 0.03]   # a hot 10-day run into "now"
    c = 50.0 * np.cumprod(1 + np.array(r))
    return _frame(c, seed, wig=0.01)


# ---------------------------------------------------------------- calibration curve

def test_gap_overshoot_matches_measured_points_and_is_monotone():
    for k, v in F.OVERSHOOT:
        assert gap_overshoot(k) == pytest.approx(v)
    ks = np.linspace(0.05, 4.0, 200)
    vals = [gap_overshoot(k) for k in ks]
    assert all(a >= b - 1e-12 for a, b in zip(vals, vals[1:]))          # tighter stop ⇒ never less slippage
    assert gap_overshoot(0.1) > F.OVERSHOOT[0][1] and gap_overshoot(0.001) <= 3.0
    assert gap_overshoot(10) == pytest.approx(F.OVERSHOOT[-1][1])
    assert gap_overshoot(None) == F.OVERSHOOT[0][1] and gap_overshoot(0) == F.OVERSHOOT[0][1]


def test_size_caps_are_derived_from_crash_odds():
    """cap ≈ base ÷ state odds, so crash-weighted exposure returns to about the base rate."""
    for flags in (3, 4):
        exact = F.BASE_CRASH / F.CRASH_ODDS[flags]
        assert abs(F.SIZE_CAP[flags] - exact) < 0.1
        assert F.SIZE_CAP[flags] * F.CRASH_ODDS[flags] <= F.BASE_CRASH * 1.2
    assert all(F.SIZE_CAP[k] == 1.0 for k in (0, 1, 2))
    assert [F.CRASH_ODDS[k] for k in range(5)] == sorted(F.CRASH_ODDS[k] for k in range(5))


# ---------------------------------------------------------------- definitions == the calibration study

def test_component_values_match_study_formulas():
    df = _blowoff()
    fr = compute_fragility(df)
    C, H, L = df.Close, df.High, df.Low
    ret = C.pct_change()
    study = {"vol": ret.rolling(20).std().iloc[-1] * np.sqrt(252),
             "above_200": (C / C.rolling(200).mean() - 1).iloc[-1],
             "max_day": ret.rolling(21).max().iloc[-1],
             "run_10": (C / C.shift(10) - 1).iloc[-1]}
    got = {c["key"]: c["value"] for c in fr["components"]}
    for k, v in study.items():
        assert got[k] == pytest.approx(v, abs=1e-4), k
    pc = C.shift()
    atr = pd.concat([H - L, (H - pc).abs(), (L - pc).abs()], axis=1).max(axis=1).rolling(14).mean().iloc[-1]
    assert fr["atr14"] == pytest.approx(atr, rel=1e-4)
    assert fr["ema20"] == pytest.approx(C.ewm(span=20, adjust=False).mean().iloc[-1], abs=0.01)


# ---------------------------------------------------------------- states

def test_calm_trend_is_not_fragile():
    fr = compute_fragility(_calm())
    assert fr["flags"] <= 1 and fr["size_cap"] == 1.0 and fr["exit_policy"]["mode"] == "standard"
    assert fr["crash_odds_pct"] == F.CRASH_ODDS[fr["flags"]]
    assert all(c["detail"] and c["label"] for c in fr["components"])   # every factor is described


def test_blowoff_is_extreme_with_fast_exit():
    fr = compute_fragility(_blowoff())
    assert fr["flags"] == 4 and fr["level"] == "extreme"
    assert fr["size_cap_label"] == "⅓" and fr["crash_odds_pct"] == 24.8
    assert fr["exit_policy"]["mode"] == "fast" and fr["exit_policy"]["level"] == fr["ema20"]
    assert "20-day EMA" in fr["exit_policy"]["rule"]


# ---------------------------------------------------------------- sizing + rules

def _setup(kind, direction, entry, stop, t1, score=8.0, conf="high"):
    tg = [{"level": t1, "label": "t1"}]
    return {"type": kind, "direction": direction, "score": score, "confidence": conf,
            "entry": {"level": entry}, "stop": {"level": stop}, "targets": tg,
            "equity_plan": _equity_plan(direction, entry, stop, tg, entry), "what_to_watch": ["original first line"]}


def test_fragile_long_breakout_is_capped_gap_adjusted_derated_and_fast_exit():
    fr = compute_fragility(_blowoff())
    atr = fr["atr14"]
    entry = 100.0
    stop = entry - 0.5 * atr
    s = apply_risk_overlay(_setup("momentum_breakout", "long", entry, stop, entry + 3 * atr), fr)
    eq, ro, sa = s["equity_plan"], s["risk_overlay"], s["equity_plan"]["size_adjustment"]
    risk = entry - stop
    over = gap_overshoot(0.5)
    expect = _RISK_BUDGET / (risk * over) / 3.0
    assert sa["gap_overshoot"] == pytest.approx(over, abs=1e-3)
    assert eq["suggested_shares"] == pytest.approx(expect, abs=1.0 if expect >= 20 else 0.011)
    assert eq["dollar_risk"] == pytest.approx(eq["suggested_shares"] * risk, abs=1.0)        # reconciles
    assert eq["expected_loss_if_stopped"] == pytest.approx(eq["suggested_shares"] * risk * over, abs=1.0)
    assert eq["expected_loss_if_stopped"] <= _RISK_BUDGET / 3.0 + 1.0                        # expected loss ≈ budget × cap
    assert sa["binding"] == "fragility" and ro["reduced_fresh_entry"] is True
    assert s["what_to_watch"][0].startswith("FRAGILE 4/4") and "20-EMA" in s["what_to_watch"][0]
    assert s["what_to_watch"][-1] == "original first line"
    assert s["score"] == pytest.approx(5.0) and s["confidence"] == "medium"
    assert ro["exit_policy"]["mode"] == "fast"


def test_fragile_pullback_is_capped_but_not_derated():
    fr = compute_fragility(_blowoff())
    s = apply_risk_overlay(_setup("momentum_pullback", "long", 100.0, 95.0, 115.0), fr)
    assert s["risk_overlay"]["size_cap_label"] == "⅓" and s["risk_overlay"]["reduced_fresh_entry"] is False
    assert s["score"] == 8.0 and s["confidence"] == "high"


def test_calm_long_keeps_full_size_and_standard_exit():
    fr = compute_fragility(_calm())
    s = apply_risk_overlay(_setup("qm_breakout", "long", 100.0, 92.0, 124.0), fr)
    sa = s["equity_plan"]["size_adjustment"]
    assert sa["fragility_cap"] == 1.0 and sa["final_shares"] <= sa["base_shares"]
    assert not any(w.startswith("FRAGILE") for w in s["what_to_watch"])
    assert s["score"] == 8.0 and s["risk_overlay"]["exit_policy"]["mode"] == "standard"


def test_short_is_never_fragility_capped():
    fr = compute_fragility(_blowoff())
    assert fr["flags"] == 4
    s = apply_risk_overlay(_setup("qm_parabolic_short", "short", 100.0, 106.0, 90.0), fr)
    sa = s["equity_plan"]["size_adjustment"]
    assert sa["fragility_cap"] == 1.0 and s["risk_overlay"]["exit_policy"]["mode"] == "standard"
    assert not any(w.startswith("FRAGILE") for w in s["what_to_watch"])


def test_cap_is_honored_on_high_priced_stocks():
    """Regression: on a $1,100 stock a ⅓ cap used to round back up to 1 whole share (cap silently defeated)."""
    fr = compute_fragility(_blowoff())
    entry = 1100.0
    stop = entry - 100.0          # base ≈ 2.5 sh → ⅓ ≈ 0.8 sh; whole-share rounding used to force 1 sh (over-risking the cap)
    s = apply_risk_overlay(_setup("qm_breakout", "long", entry, stop, entry + 300.0), fr)
    sa, eq = s["equity_plan"]["size_adjustment"], s["equity_plan"]
    assert isinstance(sa["final_shares"], float) and sa["final_shares"] < 1.0
    assert sa["final_shares"] == pytest.approx(sa["gap_adjusted_shares"] / 3.0, abs=0.011)
    assert eq["dollar_risk"] <= _RISK_BUDGET / 3.0 + 1.0
    assert "fractional" in eq["sizing_basis"]


# ---------------------------------------------------------------- engines end-to-end

class _Fake:
    def __init__(self, df):
        self.df, self.calendar = df, {}

    def history(self, period=None, interval=None, **_):
        return pd.DataFrame() if (interval and interval[-1] in "mh") else self.df.copy()


@pytest.fixture
def _spy(monkeypatch):
    flat = _frame(np.full(420, 100.0) * (1 + np.linspace(0, 0.02, 420)), seed=3)
    monkeypatch.setattr(yf, "Ticker", lambda *a, **k: _Fake(flat))


@pytest.mark.parametrize("engine", ["momentum", "qullamaggie"])
def test_engines_show_both_axes_and_overlay_every_setup(engine, _spy):
    if engine == "momentum":
        from app.services.momentum_service import compute_momentum_setup
        r = compute_momentum_setup(_Fake(_blowoff()), deep=False)
    else:
        from app.services.qullamaggie_service import compute_qullamaggie_setups
        r = compute_qullamaggie_setups(_Fake(_blowoff()))
    q = r["qualification"]
    assert q["fragility"]["flags"] == 4
    assert any(s["direction"] == "long" for s in r["setups"]), "fixture must yield a live long setup (else the loop below is vacuous)"
    if engine == "momentum":   # a fresh breakout into a fragile tape must rank BELOW the pullback entry
        types = [s["type"] for s in r["setups"]]
        assert "momentum_pullback" in types and "momentum_breakout" in types
        assert types.index("momentum_pullback") < types.index("momentum_breakout")
    assert q["trend_grade"] == q["grade"] and q["trend_score"] == q["score"]
    assert q["summary"].startswith(f"Trend {q['grade']} ({q['score']}/100) · Fragility 4/4")
    for s in r["setups"]:
        ro = s["risk_overlay"]
        assert ro["fragility_flags"] == 4 and "size_adjustment" in s["equity_plan"]
        if s["direction"] == "long":
            assert ro["exit_policy"]["mode"] == "fast" and ro["size_cap_label"] == "⅓"
        else:
            assert ro["size_cap"] == 1.0
    assert [s["rank"] for s in r["setups"]] == list(range(1, len(r["setups"]) + 1))
    scores = [s["score"] for s in r["setups"]]
    assert scores == sorted(scores, reverse=True)                     # ranking re-sorted AFTER the de-rank
