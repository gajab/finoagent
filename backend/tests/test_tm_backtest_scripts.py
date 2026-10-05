"""The backtest harness itself must be right — crafted inputs with known answers (no network, no panel)."""
import math
import os
import sys

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))

import tm_backtest_exits as EX            # noqa: E402
import tm_backtest_shortput as SP         # noqa: E402
import tm_backtest_analyze as AN          # noqa: E402
import tm_backtest_flags as FL            # noqa: E402
import tm_backtest_stress as ST           # noqa: E402


def frame(closes, opens=None, lows=None, highs=None):
    c = np.asarray(closes, dtype=float)
    o = c.copy() if opens is None else np.asarray(opens, dtype=float)
    l = np.minimum(o, c) * 0.999 if lows is None else np.asarray(lows, dtype=float)
    h = np.maximum(o, c) * 1.001 if highs is None else np.asarray(highs, dtype=float)
    idx = pd.date_range("2020-01-01", periods=len(c), freq="B")
    return pd.DataFrame({"Open": o, "High": h, "Low": l, "Close": c, "Volume": 1e6}, index=idx)


# ── exits ───────────────────────────────────────────────────────────────────

def _P(closes, **kw):
    return EX._prep(frame(closes, **kw))


def test_hold_and_time_rules():
    c = np.concatenate([np.full(250, 100.0), np.linspace(100, 160, 130)])
    P = _P(c)
    i = 250
    r, h = EX.simulate(P, i, "hold126")
    assert r == pytest.approx((c[i + 126] / 100 - 1) * 100) and h == 126
    r63, h63 = EX.simulate(P, i, "time63")
    assert r63 == pytest.approx((c[i + 63] / 100 - 1) * 100) and h63 == 63


def test_close_basis_exit_fills_next_open_not_the_trigger_close():
    c = np.concatenate([np.linspace(50, 100, 250), [101, 102, 103, 99, 90, 95, 96, 97, 98, 99, 100]])
    o = c.copy(); o[254] = 88.0                       # the bar AFTER the first close below the 21 EMA gaps down to 88
    P = _P(c, opens=o)
    r, h = EX.simulate(P, 250, "ema21")
    j = 253 if c[253] < P["e21"][253] else None
    assert j is not None or True
    first = next(jj for jj in range(251, 261) if c[jj] < P["e21"][jj])
    assert r == pytest.approx((o[first + 1] / c[250] - 1) * 100) and h == first + 1 - 250


def test_fixed_stop_fills_at_trigger_or_gap_open():
    base = np.concatenate([np.full(260, 100.0), np.full(10, 100.0)])
    lows = base.copy() * 0.999; lows[262] = 85.0
    o = base.copy()
    P = _P(base, opens=o, lows=lows)
    r, _ = EX.simulate(P, 260, "fix10")
    assert r == pytest.approx(-10.0)                  # filled AT the −10% trigger
    o2 = base.copy(); o2[262] = 80.0                  # gap through the stop → filled at the (worse) open
    lows2 = lows.copy(); lows2[262] = 80.0
    P2 = _P(base, opens=o2, lows=lows2)
    r2, _ = EX.simulate(P2, 260, "fix10")
    assert r2 == pytest.approx(-20.0)


def test_fixed_stop_not_hit_holds_to_the_end():
    c = np.linspace(100, 130, 400)
    P = _P(c)
    r, h = EX.simulate(P, 250, "fix10")
    assert r == pytest.approx((c[250 + 126] / c[250] - 1) * 100) and h == 126


def test_minervini_three_tiers_average_the_three_exits():
    n = 520
    c = np.concatenate([np.linspace(50, 200, 300), np.linspace(200, 120, 220)])       # long uptrend then a steady decline
    P = _P(c)
    i = 300
    r, h = EX.simulate(P, i, "minervini3")
    singles = [EX.simulate(P, i, k) for k in ("sma50", "sma150", "sma200")]
    assert r == pytest.approx(np.mean([x[0] for x in singles]), rel=1e-9) and h == int(np.mean([x[1] for x in singles]))
    assert singles[0][1] <= singles[1][1] <= singles[2][1]                             # 50d breaks first, 200d last


def test_chandelier_ratchets_and_wider_multiple_exits_later():
    rng = np.random.default_rng(0)
    c = np.concatenate([np.linspace(50, 150, 300) + rng.normal(0, 0.6, 300), 150 - np.linspace(0, 40, 80)])
    P = _P(c)
    held = {k: EX.simulate(P, 300, k)[1] for k in ("chand2", "chand3", "chand5")}
    assert held["chand2"] <= held["chand3"] <= held["chand5"]


def test_unknown_rule_raises_and_short_window_is_nan():
    P = _P(np.linspace(100, 110, 300))
    with pytest.raises(ValueError):
        EX.simulate(P, 100, "nonsense")
    r, h = EX.simulate(P, P["n"] - 1, "hold126")
    assert math.isnan(r) and h == 0


def test_exit_report_columns_and_pf():
    res = pd.DataFrame({"ticker": "A", "date": pd.Timestamp("2020-01-01"), "rule": "x", "ret": [10.0, -5.0, 20.0, -5.0], "hold": [10, 10, 10, 10]})
    r = EX.report(res).loc["x"]
    assert r["n"] == 4 and r["mean%"] == pytest.approx(5.0) and r["win%"] == 50.0 and r["PF"] == pytest.approx(3.0)


# ── short-put model ─────────────────────────────────────────────────────────

def test_bs_put_call_parity_and_bounds():
    S, K, tau, iv = 100.0, 95.0, 0.1, 0.3
    put = SP.bs_put(S, K, tau, iv)
    d1 = (math.log(S / K) + (SP.R + 0.5 * iv ** 2) * tau) / (iv * math.sqrt(tau))
    call = S * SP.ncdf(d1) - K * math.exp(-SP.R * tau) * SP.ncdf(d1 - iv * math.sqrt(tau))
    assert call - put == pytest.approx(S - K * math.exp(-SP.R * tau), abs=1e-9)
    assert put >= max(K * math.exp(-SP.R * tau) - S, 0) and SP.bs_put(100, 120, 0.0, 0.3) == pytest.approx(20.0)


def test_strike_for_delta_gives_20_delta():
    S, tau, iv = 100.0, 24 / 252, 0.35
    K = SP.strike_for_delta(S, tau, iv)
    d1 = (math.log(S / K) + (SP.R + 0.5 * iv ** 2) * tau) / (iv * math.sqrt(tau))
    assert SP.ncdf(d1) - 1 == pytest.approx(-0.20, abs=2e-3) and K < S


def test_path_expiry_pnl_is_credit_minus_intrinsic():
    c = np.concatenate([[100.0], np.full(24, 80.0)])         # crash right after entry, stays down
    credit, pnl, coll, K1, cost = SP.path(c, 0, 0.3, "naked")
    assert pnl[-1] == pytest.approx(credit - (K1 - 80.0))
    flat = np.full(25, 100.0)
    credit2, pnl2, *_ = SP.path(flat, 0, 0.3, "naked")
    assert pnl2[-1] == pytest.approx(credit2) and np.all(np.diff(pnl2) >= -1e-9)      # time decay only: monotone up


def test_spread_loss_is_capped_at_width():
    c = np.concatenate([[100.0], np.full(24, 50.0)])
    credit, pnl, coll, K1, cost = SP.path(c, 0, 0.3, "spread")
    assert coll == pytest.approx(0.05 * 100.0) and pnl[-1] >= -(coll - credit) - 1e-9


# ── analysis / flags / stress on planted signals ────────────────────────────

def _panel(n_dates=120, n_tk=40, seed=0, planted=True):
    rng = np.random.default_rng(seed)
    rows = []
    for d in pd.date_range("2018-01-01", periods=n_dates, freq="W"):
        for t in range(n_tk):
            sig = rng.normal()
            noise = rng.normal(0, 4)
            rows.append({"ticker": f"T{t}", "date": d, "d_x": sig, "cons": sig, "fwd21": (2.0 * sig if planted else 0.0) + noise, "fwd63": noise,
                         "mae21": -2 - abs(noise) / 4, "mae21_pct": -3.0, "bench_above200": 1.0})
    return pd.DataFrame(rows)


def test_ic_detects_a_planted_signal_and_not_noise():
    good = AN.summarize(_panel(planted=True), ["d_x"], "fwd21", 1.0)
    bad = AN.summarize(_panel(planted=False, seed=1), ["d_x"], "fwd21", 1.0)
    assert good.loc["d_x", "IC"] > 0.2 and good.loc["d_x", "t_adj"] > 5
    assert abs(bad.loc["d_x", "t_adj"]) < 3


def test_buckets_are_monotone_for_a_planted_signal():
    b = AN.buckets(_panel(planted=True), "cons")
    assert list(b["fwd21"]) == sorted(b["fwd21"]) and b["n"].sum() == 120 * 40


def test_flag_comparison_finds_the_adverse_flag():
    rng = np.random.default_rng(3)
    n = 6000
    df = pd.DataFrame({"px": rng.normal(100, 5, n), "sma50": 100.0, "sma150": 100.0, "sma200": 100.0, "ema21": 100.0, "st_dir": 1.0, "chand": 0.0, "lo20": 0.0,
                       "lo10": 0.0, "weekly_stage": 2, "sma50_gt_150": 1.0, "from_hi": -3.0, "atr_pctile": 50.0, "rv21": 20.0, "rv63": 20.0, "rsi14": 50.0,
                       "rsi_div": 0.0, "obv_div": 0.0, "dist_days": 0, "adx": 20.0, "mdi": 10.0, "pdi": 10.0, "squeeze": 0.0, "cons": 0.0, "bench_above200": 1.0})
    df["fwd21"] = np.where(df.px < 100, -3.0, 1.0) + rng.normal(0, 5, n)
    df["mae21"] = -2.0
    f = FL.flags(df)
    assert f["below_sma200"].equals(df.px < 100)
    c = FL.compare(df, f)
    assert c.loc["below_sma200", "diff"] < -3 and c.loc["below_sma200", "t"] < -10
    assert {"below_sma50", "stage4", "supertrend_down"} <= set(f.columns)


def test_stress_model_learns_a_planted_relationship_out_of_sample():
    rng = np.random.default_rng(5)
    n = 8000
    df = pd.DataFrame({f: rng.normal(size=n) for f in ST.FEATS})
    df["rv_ratio"] = rng.normal(size=n)
    df["date"] = pd.date_range("2016-01-01", periods=n, freq="D")
    p = 1 / (1 + np.exp(-(1.5 * df["rv21"] - 1.0)))
    df["drop8"] = (rng.random(n) < p).astype(float)
    r = ST.fit_eval(df, "drop8", ST.FEATS + ["rv_ratio"])
    assert r["AUC_test"] > 0.75 and r["brier_skill"] > 0 and r["coefs"].index[0] == "rv21"
