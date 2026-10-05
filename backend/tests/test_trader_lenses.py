"""Indicator math + trader-lens rules (pure numpy; no network). Crafted series with KNOWN answers."""
import json
import math

import numpy as np
import pandas as pd
import pytest

from app.services import trader_lenses as TL


# ── helpers ─────────────────────────────────────────────────────────────────

def ohlcv(closes, spread=0.01, vol=1_000_000.0, vols=None, seed=0):
    c = np.asarray(closes, dtype=float)
    n = len(c)
    o = np.concatenate([[c[0]], c[:-1]])
    h = np.maximum(o, c) * (1 + spread)
    l = np.minimum(o, c) * (1 - spread)
    v = np.full(n, vol) if vols is None else np.asarray(vols, dtype=float)
    idx = pd.date_range(end="2026-09-30", periods=n, freq="B")
    return o, h, l, c, v, idx


def trend(n=420, drift=0.003, sig=0.004, seed=1, start=100.0):
    rng = np.random.default_rng(seed)
    return start * np.exp(np.cumsum(rng.normal(drift, sig, n)))


# ── primitives ──────────────────────────────────────────────────────────────

def test_sma_known_values():
    a = TL.sma(np.arange(1, 11), 3)
    assert np.isnan(a[0]) and np.isnan(a[1])
    assert a[2] == pytest.approx(2.0) and a[-1] == pytest.approx(9.0)


def test_sma_window_longer_than_series_is_all_nan():
    assert np.all(np.isnan(TL.sma([1, 2, 3], 5)))


def test_ema_matches_pandas_ewm():
    x = np.random.default_rng(3).normal(100, 5, 200)
    ours = TL.ema(x, 21)
    ref = pd.Series(x).ewm(span=21, adjust=False).mean().values
    assert np.allclose(ours, ref)


def test_ema_empty():
    assert TL.ema([], 5).size == 0


def test_rsi_all_up_is_100_and_all_down_is_0():
    up = TL.rsi(np.arange(1, 60, dtype=float), 14)
    dn = TL.rsi(np.arange(60, 1, -1, dtype=float), 14)
    assert up[-1] == pytest.approx(100.0)
    assert dn[-1] == pytest.approx(0.0, abs=1e-9)


def test_rsi_close_to_wilder_reference():
    x = np.cumsum(np.random.default_rng(5).normal(0, 1, 400)) + 100
    ours = TL.rsi(x, 14)
    d = pd.Series(x).diff()
    up, dn = d.clip(lower=0), (-d).clip(lower=0)
    ru = up.ewm(alpha=1 / 14, adjust=False).mean()
    rd = dn.ewm(alpha=1 / 14, adjust=False).mean()
    ref = (100 - 100 / (1 + ru / rd)).values
    assert abs(ours[-1] - ref[-1]) < 0.5          # same Wilder smoothing (seed differs only at the start)


def test_rsi_bounds_and_short_series():
    x = np.random.default_rng(6).normal(0, 1, 300).cumsum() + 50
    r = TL.rsi(x, 2)
    assert np.nanmin(r) >= 0 and np.nanmax(r) <= 100
    assert np.all(np.isnan(TL.rsi([1, 2, 3], 14)))


def test_true_range_uses_gap_to_previous_close():
    h, l, c = [10, 12, 11], [9, 11.5, 10], [9.5, 12, 10.5]
    tr = TL.true_range(h, l, c)
    assert tr[1] == pytest.approx(max(12 - 11.5, abs(12 - 9.5), abs(11.5 - 9.5)))   # gap up → 2.5 not 0.5


def test_atr_constant_range_equals_range():
    n = 60
    h, l, c = np.full(n, 101.0), np.full(n, 99.0), np.full(n, 100.0)
    assert TL.atr(h, l, c, 14)[-1] == pytest.approx(2.0)


def test_adx_high_in_clean_trend_low_in_chop():
    o, h, l, c, v, _ = ohlcv(trend(300, 0.004, 0.002))
    adx_t, pdi, mdi = TL.adx_di(h, l, c)
    assert adx_t[-1] > 40 and pdi[-1] > mdi[-1]
    rng = np.random.default_rng(2)
    chop = 100 + np.sin(np.arange(300) / 3.0) * 2 + rng.normal(0, 0.2, 300)
    _, h2, l2, c2, _, _ = ohlcv(chop)
    adx_c, _, _ = TL.adx_di(h2, l2, c2)
    assert adx_c[-1] < 25


def test_adx_short_series_all_nan():
    a, p, m = TL.adx_di([1, 2, 3], [1, 2, 3], [1, 2, 3])
    assert np.all(np.isnan(a))


def test_supertrend_up_in_uptrend_flips_on_crash():
    c = np.concatenate([trend(200, 0.003, 0.003), trend(1, 0)[:0]])
    o, h, l, c, v, _ = ohlcv(c)
    d, line = TL.supertrend(h, l, c, 10, 3.0)
    assert d[-1] > 0 and line[-1] < c[-1]
    crash = np.concatenate([c, c[-1] * np.cumprod(np.full(15, 0.96))])
    o, h, l, cc, v, _ = ohlcv(crash)
    d2, line2 = TL.supertrend(h, l, cc, 10, 3.0)
    assert d2[-1] < 0 and line2[-1] > cc[-1]


def test_swing_points_zigzag():
    c = np.array([1, 2, 3, 4, 3, 2, 1, 2, 3, 4, 5, 4, 3, 2, 3, 4], dtype=float)
    sh, sl = TL.swing_points(c, c, 2, 2)
    assert (4, 3.0) not in sh                      # sanity: index/price are consistent pairs
    assert any(p == 5.0 for _, p in sh) and any(p == 1.0 for _, p in sl)


def test_divergence_bearish_and_bullish():
    # price makes a higher high while the oscillator makes a lower high
    n = 60
    price = np.concatenate([np.linspace(100, 120, 20), np.linspace(120, 110, 10), np.linspace(110, 125, 20), np.linspace(125, 118, 10)])
    osc = np.concatenate([np.linspace(40, 80, 20), np.linspace(80, 50, 10), np.linspace(50, 70, 20), np.linspace(70, 55, 10)])
    assert TL._divergence(price, osc, lookback=50, order=3) == "bearish"
    assert TL._divergence(-price, -osc, lookback=50, order=3) == "bullish"
    assert TL._divergence(price, price, lookback=50, order=3) is None


# ── indicator suite ─────────────────────────────────────────────────────────

def test_suite_requires_60_bars():
    assert TL.indicator_suite(*ohlcv(trend(40))[:5]) == {}


def test_suite_json_roundtrip_keeps_string_keys():
    """REGRESSION: int dict keys ('roc', 'realized_vol') silently broke every lens that read them by string
    after the cache's JSON round-trip — the suite must already be JSON-stable."""
    o, h, l, c, v, idx = ohlcv(trend(420))
    bench = trend(420, 0.001, 0.005, seed=9)
    s = TL.indicator_suite(o, h, l, c, v, idx, bench)
    assert json.loads(json.dumps(s)) == s


def test_suite_values_are_sane():
    o, h, l, c, v, idx = ohlcv(trend(420))
    s = TL.indicator_suite(o, h, l, c, v, idx, trend(420, 0.001, 0.005, seed=9))
    assert 0 <= s["rsi14"] <= 100 and 0 <= s["rsi2"] <= 100
    assert s["supertrend"]["direction"] == "up"
    assert s["range"]["pct_from_52w_high"] <= 0 <= s["range"]["pct_above_52w_low"]
    assert s["range"]["hi_252"] >= s["price"] >= s["range"]["lo_252"]
    assert s["pct_vs_sma"]["200"] > 0
    assert s["weekly"]["stage"] == 2
    assert s["roc"]["63"] is not None and s["realized_vol"]["21"] is not None
    assert s["relative_strength"]["excess_return_pct"]["63"] is not None
    assert s["bollinger"]["upper"] > s["bollinger"]["lower"]
    assert s["ichimoku"]["price_vs_cloud"] == "above"


def test_suite_weekly_stage_4_in_downtrend():
    o, h, l, c, v, idx = ohlcv(trend(420, -0.003, 0.004))
    assert TL.indicator_suite(o, h, l, c, v, idx)["weekly"]["stage"] == 4


def test_suite_without_benchmark_or_index_still_works():
    s = TL.indicator_suite(*ohlcv(trend(300))[:5])
    assert "relative_strength" not in s and "weekly" not in s and s["rsi14"] is not None


def test_distribution_days_counted():
    c = np.concatenate([np.full(300, 100.0)])
    c = 100 + np.arange(300) * 0.0
    vols = np.full(300, 1e6)
    # last 25 bars: alternate down days on rising volume
    for k in range(20, 0, -2):
        c[-k:] = c[-k:] * 0.99
        vols[-k] = 2e6 + k * 1e5
    s = TL.indicator_suite(*ohlcv(c, vols=vols)[:5])
    assert s["distribution_days_25d"] >= 3


def test_vsa_upthrust_and_spring_flags():
    base = np.full(300, 100.0)
    # upthrust: spike above the 20-bar high then close back inside
    c = base.copy(); o, h, l, cc, v, idx = ohlcv(c, spread=0.002)
    h = h.copy(); h[-1] = 105.0
    s = TL.indicator_suite(o, h, l, cc, v, idx)
    assert s["vsa"]["upthrust"] is True
    # spring: undercut then reclaim
    o, h, l, cc, v, idx = ohlcv(c, spread=0.002)
    l = l.copy(); l[-2] = 95.0
    s2 = TL.indicator_suite(o, h, l, cc, v, idx)
    assert s2["vsa"]["spring"] is True


def test_squeeze_detected_after_volatility_collapse():
    rng = np.random.default_rng(4)
    wild = 100 + rng.normal(0, 2.5, 250).cumsum() * 0.2
    calm = wild[-1] + rng.normal(0, 0.05, 60).cumsum() * 0.02
    c = np.concatenate([wild, calm])
    o, h, l, cc, v, idx = ohlcv(c, spread=0.0015)
    s = TL.indicator_suite(o, h, l, cc, v, idx)
    assert s["squeeze_on"] is True
    assert s["bollinger"]["bandwidth_percentile_1y"] < 20


# ── lenses ──────────────────────────────────────────────────────────────────

def _lens_map(c, bench=None):
    o, h, l, cc, v, idx = ohlcv(c)
    s = TL.indicator_suite(o, h, l, cc, v, idx, bench)
    return s, {x["key"]: x for x in TL.trader_lenses(s)}


def test_all_fifteen_lenses_present_with_unique_rule_ids():
    s, m = _lens_map(trend(420), trend(420, 0.001, 0.005, seed=9))
    assert len(m) == 15
    for k, L in m.items():
        ids = [r["id"] for r in L["rules"]]
        assert len(ids) == len(set(ids)), k
        assert L["exit_rule"] and -1 <= L["d"] <= 1 and L["stance"] in ("bullish", "bearish", "neutral")
        assert 0 <= L["met"] <= L["total"] <= len(L["rules"])


def test_every_lens_rule_is_scoreable_given_full_inputs():
    """REGRESSION (key-type bug): with full history + benchmark, momentum/RS rules must produce True/False, not None."""
    s, m = _lens_map(trend(420), trend(420, 0.001, 0.005, seed=9))
    unscoreable = {(k, r["id"]) for k, L in m.items() for r in L["rules"] if r["met"] is None}
    assert unscoreable <= {("livermore", "no_failure")}       # the one deliberately-unscored placeholder rule


def test_minervini_8_of_8_in_leader_uptrend():
    s, m = _lens_map(trend(420, 0.004, 0.004), trend(420, 0.0005, 0.004, seed=9))
    L = m["minervini"]
    assert sum(1 for r in L["rules"][:8] if r["met"]) == 8
    assert L["stance"] == "bullish" and L["exit_level"] == pytest.approx(s["sma"]["50"])


def test_minervini_fails_in_downtrend_and_flags_climax():
    s, m = _lens_map(trend(420, -0.003, 0.004))
    assert sum(1 for r in m["minervini"]["rules"][:8] if r["met"]) <= 2
    assert m["minervini"]["stance"] in ("bearish", "neutral")
    # climactic extension (> 25% above the 50d) flips the 9th rule
    c = np.concatenate([np.full(300, 100.0), np.linspace(100, 160, 20)])
    s2, m2 = _lens_map(c)
    rule = next(r for r in m2["minervini"]["rules"] if r["id"] == "not_climactic")
    assert rule["met"] is False


def test_weinstein_stage_maps_to_direction():
    _, up = _lens_map(trend(420, 0.003, 0.004))
    _, dn = _lens_map(trend(420, -0.003, 0.004))
    assert up["weinstein"]["d"] > 0.5 and dn["weinstein"]["d"] < -0.5
    assert up["weinstein"]["note"] == "Stage 2" and dn["weinstein"]["note"] == "Stage 4"


def test_turtles_exit_levels_are_prior_channel_lows():
    s, m = _lens_map(trend(420))
    assert m["turtles"]["exit_level"] == pytest.approx(s["prior_range"]["lo_10"])


def test_connors_rsi2_oversold_in_uptrend_is_bullish_entry():
    c = trend(420, 0.003, 0.002)
    c = np.concatenate([c, c[-1] * np.cumprod(np.full(3, 0.985))])     # sharp 3-day dip inside an uptrend
    s, m = _lens_map(c)
    assert s["rsi2"] < 10
    assert m["connors"]["d"] > 0


def test_connors_overbought_in_uptrend_is_take_profit():
    c = trend(420, 0.002, 0.002)
    c = np.concatenate([c, c[-1] * np.cumprod(np.full(4, 1.03))])
    s, m = _lens_map(c)
    assert s["rsi2"] > 90 and m["connors"]["d"] < 0


def test_ptj_200d_rule():
    _, up = _lens_map(trend(420, 0.003, 0.004))
    _, dn = _lens_map(trend(420, -0.003, 0.004))
    assert up["ptj"]["rules"][0]["met"] is True and dn["ptj"]["rules"][0]["met"] is False


def test_qullamaggie_needs_adr_and_ema_surf():
    s, m = _lens_map(trend(420, 0.004, 0.004))
    r = {x["id"]: x for x in m["qullamaggie"]["rules"]}
    assert r["above_10_20"]["met"] is True and r["ema10_gt_20"]["met"] is True
    assert r["mom_63"]["met"] == (s["roc"]["63"] > 25)


def test_ichimoku_above_cloud_bullish():
    _, m = _lens_map(trend(420, 0.003, 0.003))
    assert m["ichimoku"]["d"] > 0.5 and m["ichimoku"]["rules"][0]["met"] is True


def test_wyckoff_upthrust_and_buying_climax_are_bearish():
    base = np.full(300, 100.0)
    o, h, l, c, v, idx = ohlcv(base, spread=0.002)
    h = h.copy(); h[-1] = 105.0
    s = TL.indicator_suite(o, h, l, c, v, idx)
    L = {x["key"]: x for x in TL.trader_lenses(s)}["wyckoff"]
    assert L["d"] < 0


def test_lens_consensus_counts_and_empty():
    assert TL.lens_consensus([]) == {"d": 0.0, "bull": 0, "bear": 0, "neutral": 0, "n": 0}
    _, m = _lens_map(trend(420, 0.004, 0.003), trend(420, 0.0005, 0.004, seed=9))
    c = TL.lens_consensus(list(m.values()))
    assert c["n"] == 15 and c["bull"] + c["bear"] + c["neutral"] == 15 and c["d"] > 0.2


def test_uptrend_consensus_beats_downtrend_consensus():
    up = TL.lens_consensus(TL.trader_lenses(TL.indicator_suite(*ohlcv(trend(420, 0.003, 0.004)))))
    dn = TL.lens_consensus(TL.trader_lenses(TL.indicator_suite(*ohlcv(trend(420, -0.003, 0.004)))))
    assert up["d"] > 0.2 > -0.2 > dn["d"]


def test_trader_lenses_empty_suite():
    assert TL.trader_lenses({}) == []
