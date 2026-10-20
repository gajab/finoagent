"""Selloff Monitor — calendar, options cone, analogs, stress grid, tells, end-to-end payload.

No network: synthetic index history (GBM with injected selloffs), synthetic cross-asset closes,
straddles built from a known σ, hourly futures bars. The router handler is exercised with a fake
cache so the cold-cache path (dealer read → build → write) runs for real.
"""
import asyncio
import datetime as dt
import json
import math
from types import SimpleNamespace

import numpy as np
import pandas as pd
import pytest

from app.services import selloff_monitor_service as sm


# ---------------------------------------------------------------------------
# fixtures
# ---------------------------------------------------------------------------

def _sessions(start: str, n: int) -> pd.DatetimeIndex:
    days, d = [], dt.date.fromisoformat(start)
    while len(days) < n:
        if sm.is_session(d):
            days.append(d)
        d += dt.timedelta(days=1)
    return pd.DatetimeIndex(days)


def _index_history(n: int = 3000, seed: int = 7, start: str = "2010-01-04") -> tuple[pd.DataFrame, pd.Series]:
    """GBM with a selloff (−1.2%, +0.3%, −1.2%) every ~150 sessions and on the LAST 3 bars."""
    rng = np.random.default_rng(seed)
    idx = _sessions(start, n)
    r = rng.normal(0.0003, 0.009, n)
    for k in range(300, n - 30, 150):
        r[k - 2:k + 1] = [-0.012, 0.003, -0.012]
    r[-3:] = [-0.012, 0.003, -0.012]
    c = 100 * np.exp(np.cumsum(r))
    o = np.concatenate([[c[0]], c[:-1]])
    wig = np.abs(rng.normal(0, 0.003, n))
    df = pd.DataFrame({"Open": o, "High": np.maximum(o, c) * (1 + wig),
                       "Low": np.minimum(o, c) * (1 - wig), "Close": c}, index=idx)
    vix = pd.Series(15 + 3 * np.sin(np.arange(n) / 40) + rng.normal(0, 0.5, n), index=idx)
    return df, vix


def _straddle_chain(spot: float, sigma_total: float, strikes=None, src="mid") -> tuple[list, list]:
    """Call/put rows whose ATM straddle == spot·sigma_total/√(π/2) at EVERY strike (flat), so the
    interpolated straddle at spot recovers sigma_total exactly."""
    strad = spot * sigma_total / sm._SQRT_HALF_PI
    strikes = strikes or [round(spot) - 2, round(spot) - 1, round(spot), round(spot) + 1, round(spot) + 2]
    calls, puts = [], []
    for k in strikes:
        half = strad / 2
        q = {"bid": half - 0.01, "ask": half + 0.01, "lastPrice": half} if src == "mid" else \
            {"bid": 0.0, "ask": 0.0, "lastPrice": half}
        calls.append({"strike": float(k), **q, "impliedVolatility": 0.2})
        puts.append({"strike": float(k), **q, "impliedVolatility": 0.2})
    return calls, puts


def _grid_closes(sessions: pd.DatetimeIndex, seed: int = 3) -> dict[str, pd.Series]:
    rng = np.random.default_rng(seed)
    out = {}
    n = len(sessions)
    for sym, _l, _g, kind, sign in sm.STRESS_ASSETS:
        base = 5.0 if kind == "bp" else 100.0
        steps = rng.normal(0, 0.01 if kind != "bp" else 0.03, n)
        out[sym] = pd.Series(base * np.exp(np.cumsum(steps)) if kind != "bp" else base + np.cumsum(steps),
                             index=sessions)
    out["^VIX3M"] = out["^VIX"] * 1.1
    return out


def _data_dict(n: int = 3000) -> dict:
    index, vix = _index_history(n)
    spy = index.copy() * 0.1                                   # SPY ≈ index / 10
    closes = _grid_closes(index.index[-504:])
    closes["SPY"] = spy["Close"].iloc[-504:]
    return {"index": index, "vix_hist": vix, "spy_ohlc": spy.iloc[-504:], "closes": closes,
            "futures": {}, "option_chains": []}


# ---------------------------------------------------------------------------
# calendar
# ---------------------------------------------------------------------------

def test_nyse_holidays_and_next_sessions():
    hol = sm.nyse_holidays(2026)
    assert dt.date(2026, 4, 3) in hol                     # Good Friday
    assert dt.date(2026, 11, 26) in hol                   # Thanksgiving
    assert dt.date(2026, 7, 3) in hol                     # July 4 (Sat) observed Fri
    assert dt.date(2026, 10, 12) not in hol               # Columbus Day — stocks trade
    nxt = sm.next_sessions(dt.date(2026, 10, 16), 3)
    assert nxt == [dt.date(2026, 10, 19), dt.date(2026, 10, 20), dt.date(2026, 10, 21)]
    around_tg = sm.next_sessions(dt.date(2026, 11, 25), 2)
    assert around_tg == [dt.date(2026, 11, 27), dt.date(2026, 11, 30)]


def test_market_events_mid_october_2026():
    ev = sm.market_events(dt.date(2026, 10, 18))
    by = {e["date"]: e["label"] for e in ev}
    assert "just passed" in by["2026-10-16"]
    assert by["2026-10-21"].startswith("VIX")             # 30 days before Fri Nov 20
    assert by["2026-11-20"] == "Monthly options expiry"
    assert [e["date"] for e in ev] == sorted(e["date"] for e in ev)


# ---------------------------------------------------------------------------
# options cone
# ---------------------------------------------------------------------------

def test_select_cone_expiries_dedupes_and_targets():
    exps = ["2026-10-19", "2026-10-20", "2026-10-23", "2026-10-30", "2026-11-13", "2026-11-20"]
    got = sm.select_cone_expiries(exps, dt.date(2026, 10, 16))
    assert got == ["2026-10-19", "2026-10-23", "2026-10-30", "2026-11-13"]
    assert sm.select_cone_expiries(["2026-10-16"], dt.date(2026, 10, 16)) == []   # same-day excluded


def test_atm_straddle_interpolates_between_strikes_and_flags_source():
    calls = [{"strike": 100.0, "bid": 2.9, "ask": 3.1, "lastPrice": 3.0},
             {"strike": 101.0, "bid": 2.4, "ask": 2.6, "lastPrice": 2.5}]
    puts = [{"strike": 100.0, "bid": 1.9, "ask": 2.1, "lastPrice": 2.0},
            {"strike": 101.0, "bid": 2.4, "ask": 2.6, "lastPrice": 2.5}]
    v, src = sm.atm_straddle(calls, puts, 100.25)
    assert v == pytest.approx(5.0 + 0.25 * (5.0 - 5.0)) and src == "mid"
    puts[1] = {"strike": 101.0, "bid": 0.0, "ask": 0.0, "lastPrice": 3.0}       # weekend: last only
    v2, src2 = sm.atm_straddle(calls, puts, 100.5)
    assert v2 == pytest.approx(5.0 + 0.5 * (5.5 - 5.0)) and src2 == "last"


def test_options_cone_recovers_sigma_and_is_lognormal():
    spot, anchor = 700.0, dt.date(2026, 10, 16)
    sig_ann = 0.18
    chains = []
    for exp in ("2026-10-19", "2026-10-23", "2026-10-30", "2026-11-13"):
        T = (dt.date.fromisoformat(exp) - anchor).days
        c, p = _straddle_chain(spot, sig_ann * math.sqrt(T / 365))
        chains.append({"expiration": exp, "calls": c, "puts": p})
    sess = sm.next_sessions(anchor, 20)
    cone = sm.options_cone(chains, spot, anchor, sess)
    assert cone["available"]
    for pt in cone["points"]:
        assert pt["sigma_annual_pct"] == pytest.approx(18.0, abs=0.1)
    # flat vol term structure → total σ at each session = σ_ann·√(calendar days/365)
    for f, d in zip(cone["fan_pct"], sess):
        T = (d - anchor).days / 365
        assert f["sigma_pct"] == pytest.approx(sig_ann * math.sqrt(T) * 100, abs=0.01)
        assert f["p10"] < f["p25"] < f["p50"] < f["p75"] < f["p90"]
        # symmetric in LOG space around the −σ²/2 median
        lo, hi = math.log(1 + f["p10"] / 100), math.log(1 + f["p90"] / 100)
        med = math.log(1 + f["p50"] / 100)
        assert (hi - med) == pytest.approx(med - lo, abs=1e-4)
        w = (f["sigma_pct"] / 100) ** 2                        # lognormal median sits at −σ²/2
        assert f["p50"] == pytest.approx((math.exp(-w / 2) - 1) * 100, abs=2e-3)
    widths = [f["p90"] - f["p10"] for f in cone["fan_pct"]]
    assert widths == sorted(widths)


def test_options_cone_interpolates_from_origin_before_first_expiry():
    """Sessions before the first listed expiry scale total variance from w(0)=0 (∝ calendar days)."""
    spot, anchor, sig = 700.0, dt.date(2026, 10, 16), 0.20
    T = (dt.date(2026, 10, 23) - anchor).days
    c, p = _straddle_chain(spot, sig * math.sqrt(T / 365))
    sess = sm.next_sessions(anchor, 3)                         # Mon–Wed: inside (0, 7] days
    cone = sm.options_cone([{"expiration": "2026-10-23", "calls": c, "puts": p}], spot, anchor, sess)
    for f, d in zip(cone["fan_pct"], sess):
        assert f["sigma_pct"] == pytest.approx(sig * math.sqrt((d - anchor).days / 365) * 100, abs=0.01)


def test_compare_to_baseline_significance():
    st = sm._horizon_stats(np.array([1.0] * 34 + [-1.0] * 6))   # 85% up, CI ≈ 71–93%
    sm.compare_to_baseline(st, {"p_up": 55.0, "median_pct": 0.1})
    assert st["significant"] and st["edge_pts"] == pytest.approx(30.0)
    st2 = sm._horizon_stats(np.array([1.0] * 24 + [-1.0] * 16))  # 60% up, CI ≈ 45–74%
    sm.compare_to_baseline(st2, {"p_up": 55.0, "median_pct": 0.1})
    assert not st2["significant"] and "not distinguishable" in sm._lean(st2)


def test_options_cone_without_chains_is_unavailable():
    cone = sm.options_cone([], 700.0, dt.date(2026, 10, 16), sm.next_sessions(dt.date(2026, 10, 16), 5))
    assert cone["available"] is False and cone["reason"]


# ---------------------------------------------------------------------------
# analogs
# ---------------------------------------------------------------------------

def test_index_features_have_no_lookahead():
    index, vix = _index_history(1200)
    full = sm.index_features(index, vix)
    cut = sm.index_features(index.iloc[:900], vix.iloc[:900])
    cols = list(sm._FEATURES)
    pd.testing.assert_frame_equal(full[cols].iloc[:900], cut[cols])


def test_find_analogs_invariants():
    index, vix = _index_history()
    feat = sm.index_features(index, vix)
    a = sm.find_analogs(feat)
    assert a["available"] and 10 <= a["n"] <= sm.K_ANALOGS
    picked = sorted(pd.Timestamp(m["date"]) for m in a["matches"])
    pos = [feat.index.get_loc(p) for p in picked]
    assert all(abs(i - j) > sm.H for k, i in enumerate(pos) for j in pos[k + 1:])   # non-overlapping
    assert all(p <= pd.Timestamp(a["through"]) for p in picked)                     # full forward window
    for f in a["fan_pct"]:
        assert f["p10"] <= f["p25"] <= f["p50"] <= f["p75"] <= f["p90"]
    for key in ("h1", "h5", f"h{sm.H}"):
        st = a["stats"][key]
        assert st["n"] == a["n"]
        lo, hi = st["p_up_ci"]
        assert lo <= st["p_up"] <= hi
        assert st["wilson_significant"] == (not (lo <= st["baseline_p_up"] <= hi))
        assert st["significant"] == (st["p_holm"] < 0.05)              # episode-robust z, Holm-adjusted
        elo, ehi = st["p_up_ci_episode"]
        assert elo <= st["p_up"] <= ehi
        assert st["edge_pts"] == pytest.approx(st["p_up"] - st["baseline_p_up"], abs=0.11)
    fp = a["path"]["first_passage"]
    assert fp["up_first"] + fp["down_first"] + fp["neither"] == pytest.approx(100, abs=0.2)
    for k in ("baseline_p_new_low_5d", "baseline_p_reclaim_pre_move_20d", "baseline_worst_close_20d_median_pct"):
        assert a["path"][k] is not None
    # the injected selloffs are the closest matches (the engine finds the pattern it was shown)
    assert all(m["ret3_pct"] < -1.5 for m in a["matches"][:5])


def test_find_analogs_needs_history():
    index, vix = _index_history(250)
    assert sm.find_analogs(sm.index_features(index, vix))["available"] is False


def test_wilson_and_first_passage():
    lo, hi = sm._wilson(20, 40)
    assert lo < 0.5 < hi and hi - lo == pytest.approx(0.30, abs=0.01)
    closes = np.array([100, 101, 102.5, 99])
    assert sm._first_passage(closes, 0, 0.02, 5) == "up"
    assert sm._first_passage(np.array([100, 99, 97.9, 103]), 0, 0.02, 5) == "down"
    assert sm._first_passage(np.array([100, 100.5, 99.5]), 0, 0.02, 5) == "neither"


def test_lean_wording_is_honest():
    assert "no edge" in sm._lean({"edge_pts": 2.0, "significant": False})
    assert "not distinguishable" in sm._lean({"edge_pts": 9.0, "significant": False})
    assert "further losses" in sm._lean({"edge_pts": -12.0, "significant": True})
    # one voice: an in-sample 'significant' tilt is not called meaningful when the walk-forward shows no skill
    assert "does not confirm" in sm._lean({"edge_pts": 16.0, "significant": True}, "no_skill")
    assert "statistically meaningful" in sm._lean({"edge_pts": 16.0, "significant": True}, "skill")


# ---------------------------------------------------------------------------
# stress grid
# ---------------------------------------------------------------------------

def test_stress_grid_weekend_steps_and_holiday_gaps():
    sessions = _sessions("2026-06-01", 120)
    last5 = list(sessions[-5:])
    closes = {s: pd.Series(100.0, index=sessions) for s, *_ in sm.STRESS_ASSETS}
    # SPY: noisy history, then −2% on the last session
    rng = np.random.default_rng(1)
    spy = 100 * np.exp(np.cumsum(rng.normal(0, 0.01, len(sessions))))
    spy[-1] = spy[-2] * 0.98
    closes["SPY"] = pd.Series(spy, index=sessions)
    # BTC trades every calendar day; flat except a +3% move over the weekend before last5[0]
    cal = pd.date_range(sessions[0], sessions[-1] + pd.Timedelta(days=2), freq="D")
    btc = pd.Series(100 * np.exp(np.cumsum(rng.normal(0, 0.01, len(cal)))), index=cal)
    closes["BTC-USD"] = btc
    # MOVE: missing print on one grid session (bond holiday)
    mv = pd.Series(100 * np.exp(np.cumsum(rng.normal(0, 0.02, len(sessions)))), index=sessions)
    closes["^MOVE"] = mv.drop(last5[2])
    g = sm.stress_grid(closes, last5, None)
    row = sm._row(g, "SPY")
    assert row["cells"][-1]["change"] == pytest.approx(-2.0, abs=0.01)
    assert row["cells"][-1]["z"] < -1 and row["cells"][-1]["stress"] == -row["cells"][-1]["z"]
    mrow = sm._row(g, "^MOVE")
    assert mrow["cells"][2]["change"] is None and mrow["cells"][2]["z"] is None   # no print ≠ 0
    assert mrow["cells"][3]["change"] is not None
    # BTC: Monday cell spans 3 own observations → z uses σ·√3
    b = sm._row(g, "BTC-USD")
    mondays = [i for i, d in enumerate(last5) if d.weekday() == 0 and i > 0]
    assert mondays, "fixture must put a Monday inside the grid window"
    i = mondays[0]
    raw = math.log(btc.loc[last5[i]] / btc.loc[last5[i - 1]])
    sd = float(np.log(btc / btc.shift(1)).dropna()[lambda s: s.index < last5[-sm.WINDOW]].tail(sm.STRESS_LOOKBACK).std())
    assert b["cells"][i]["z"] == pytest.approx(raw / (sd * math.sqrt(3)), abs=0.01)
    assert b["cells"][i + 1]["z"] == pytest.approx(
        math.log(btc.loc[last5[i + 1]] / btc.loc[last5[i]]) / sd, abs=0.01)
    assert b["now"] is not None                                    # weekend prints after the last session
    assert sm._row(g, "LQD")["cells"][-1]["stress"] is None        # context row: no stress sign


def test_stress_grid_fred_lag_and_vix_ratio():
    sessions = _sessions("2026-06-01", 120)
    last5 = list(sessions[-5:])
    closes = {s: pd.Series(100.0, index=sessions) for s, *_ in sm.STRESS_ASSETS}
    closes["^VIX"] = pd.Series(20.0, index=sessions)
    closes["^VIX3M"] = pd.Series(19.0, index=sessions)
    oas = pd.Series(np.linspace(3.0, 3.3, len(sessions) - 1), index=sessions[:-1])   # lags a day
    g = sm.stress_grid(closes, last5, oas)
    o = sm._row(g, "HY OAS")
    assert o["cells"][-1]["change"] is None and o["as_of"] == sessions[-2].date().isoformat()
    assert o["cells"][-2]["change"] == pytest.approx((oas.iloc[-1] - oas.iloc[-2]) * 100, abs=0.05)
    r = sm._row(g, "VIX/VIX3M")
    assert r["cells"][-1]["value"] == pytest.approx(20 / 19, abs=1e-3)


def _grid_with(spy_z=-2.0, oas_bp=None, hyg_z=None, ratio=None, tlt=0.0, jpy=0.0):
    rows = [{"symbol": "SPY", "window": {"change": -2.0, "z": spy_z}, "cells": []},
            {"symbol": "TLT", "window": {"change": tlt, "z": None}, "cells": []},
            {"symbol": "JPY=X", "window": {"change": jpy, "z": None}, "cells": []}]
    if oas_bp is not None:
        rows.append({"symbol": "HY OAS", "window": {"change": oas_bp, "z": None}, "cells": []})
    if hyg_z is not None:
        rows.append({"symbol": "HYG", "window": {"change": -1.0, "z": hyg_z}, "cells": []})
    if ratio is not None:
        rows.append({"symbol": "VIX/VIX3M", "window": {}, "cells": [{"value": ratio}]})
    return {"groups": [{"group": "x", "rows": rows}]}


@pytest.mark.parametrize("kw,label", [
    (dict(spy_z=-0.5, oas_bp=30, ratio=1.1), "No significant equity stress"),
    (dict(oas_bp=25, ratio=1.05), "Broad risk-off"),
    (dict(oas_bp=25, ratio=0.85), "Stress building"),           # credit alone isn't broad risk-off
    (dict(oas_bp=2, ratio=1.05), "Stress building"),            # VIX curve alone isn't either
    (dict(hyg_z=-2.0, ratio=1.05), "Broad risk-off"),           # HYG fallback when OAS is missing
    (dict(oas_bp=10, ratio=0.85), "Stress building"),
    (dict(oas_bp=2, ratio=0.95), "Stress building"),
    (dict(oas_bp=2, ratio=0.85), "Equity-only de-risking"),
])
def test_diagnosis_rules(kw, label):
    assert sm.diagnose_stress(_grid_with(**kw))["label"] == label


def test_diagnosis_uses_index_headline_numbers():
    d = sm.diagnose_stress(_grid_with(spy_z=-0.5, oas_bp=2, ratio=0.85), {"move3_sigma": -1.8, "ret3_pct": -1.87})
    assert d["label"] == "Equity-only de-risking"
    assert "-1.87%" in d["points"][0]["value"] and "-1.8σ" in d["points"][0]["value"]


# ---------------------------------------------------------------------------
# overnight futures + tells
# ---------------------------------------------------------------------------

def _hourly(day_vals: dict[str, list[tuple[int, float]]]) -> pd.Series:
    idx, vals = [], []
    for day, pts in day_vals.items():
        for hour, v in pts:
            idx.append(pd.Timestamp(f"{day} {hour:02d}:00", tz="America/New_York"))
            vals.append(v)
    return pd.Series(vals, index=pd.DatetimeIndex(idx))


def test_overnight_futures_vs_cash_close():
    fri = dt.date(2026, 10, 16)
    es = _hourly({"2026-10-16": [(14, 7700.0), (15, 7704.0), (16, 7706.0)], "2026-10-18": [(18, 7735.0), (19, 7742.52)]})
    # the 15:00 bar (closing at the 4 pm cash close) is the reference, not the 16:00 after-hours bar
    o = sm.overnight_futures({"ES=F": es}, fri, 763.25)
    assert o["available"] and o["es"]["ref"] == 7704.0
    assert o["es"]["change_pct"] == pytest.approx((7742.52 / 7704 - 1) * 100, abs=0.01)
    assert o["spy_implied"] == pytest.approx(763.25 * (1 + o["es"]["change_pct"] / 100), abs=0.01)
    closed = sm.overnight_futures({"ES=F": _hourly({"2026-10-16": [(14, 7700.0), (15, 7704.0)]})}, fri, 763.25)
    assert closed["available"] is False and closed["es"]["trading"] is False


def _levels(close=763.25):
    return {"close": close, "session_high": 773.25, "session_low": 760.64, "pre_move_close": 778.0,
            "sma50": 767.0, "sma200": 722.0, "prev_high": 775.0}


def test_tells_with_and_without_futures():
    grid = _grid_with(oas_bp=7, ratio=0.975, jpy=-0.24)
    grid["groups"][0]["rows"].append({"symbol": "HY OAS", "as_of": "2026-10-15",
                                      "window": {"change": 7}, "cells": [{"change": 5.0}, {"change": 7.0}]})
    dealer = {"gamma_flip": {"level": 774.6}, "gamma_levels": {"put_support": {"strike": 760.0}}}
    no_fut = sm.build_tells(_levels(), grid, dealer, {"available": False})
    by = {t["id"]: t for t in no_fut}
    assert by["break_low"]["status"] == "watch" and by["break_low"]["basis"] == "Friday close"
    assert "gamma" not in by and "put_support" not in by          # dealer gamma is a vol regime, not a direction tell
    assert all(t["evidence"]["grade"] for t in no_fut)
    assert by["vix_curve"]["status"] == "not_met" and by["vix_calm"]["status"] == "not_met"
    assert by["trend"]["label"].endswith("200-day average")    # already below the 50-day
    fut = sm.build_tells(_levels(), grid, dealer, {"available": True, "spy_implied": 758.0})
    by2 = {t["id"]: t for t in fut}
    assert by2["break_low"]["status"] == "met"
    assert by2["retake_high"]["status"] == "not_met"
    assert by2["breadth"]["status"] == "watch"
    assert {t["side"] for t in fut} == {"continuation", "recovery"}


# ---------------------------------------------------------------------------
# end to end
# ---------------------------------------------------------------------------

def test_build_selloff_monitor_end_to_end_json_clean():
    data = _data_dict()
    spot = float(data["spy_ohlc"]["Close"].iloc[-1])
    anchor = data["spy_ohlc"].index[-1].date()
    chains = []
    for exp in sm.next_sessions(anchor, 12)[::4]:
        T = (exp - anchor).days
        c, p = _straddle_chain(spot, 0.16 * math.sqrt(T / 365), strikes=[round(spot, 1) - 0.5, round(spot, 1) + 0.5])
        chains.append({"expiration": exp.isoformat(), "calls": c, "puts": p})
    data["option_chains"] = chains
    dealer = {"net_gex": {"sign": "short", "value_millions": -50.0}, "gamma_flip": {"level": spot * 1.01},
              "gamma_levels": {"put_support": {"strike": spot * 0.99}, "call_resistance": {"strike": spot * 1.03},
                               "hvl": {"strike": spot}}, "as_of": "x"}
    res = sm.build_selloff_monitor(data, dealer, None, today=anchor + dt.timedelta(days=2))
    json.dumps(res, allow_nan=False)
    assert res["available"] and res["analogs"]["available"] and res["options_cone"]["available"]
    ch = res["chart"]
    assert len(ch["forward_dates"]) == sm.H and len(ch["history_fan"]) == sm.H == len(ch["options_fan"])
    # the price fans are the pct fans converted ONCE on the backend
    for pct, px in zip(res["analogs"]["fan_pct"], ch["history_fan"]):
        assert px["p50"] == pytest.approx(spot * (1 + pct["p50"] / 100), abs=0.011)
    assert {lv["id"] for lv in ch["levels"]} >= {"session_low", "pre_move", "flip", "put_support"}
    assert res["state"]["dealer"]["sign"] == "short"
    assert res["read"] and any("closest historical setups" in line for line in res["read"])
    assert any(t["side"] == "recovery" for t in res["tells"])


def test_build_selloff_monitor_degrades_gracefully():
    data = _data_dict()
    data["closes"] = {}
    res = sm.build_selloff_monitor(data, None, None)
    json.dumps(res, allow_nan=False)
    assert res["available"] and res["options_cone"]["available"] is False
    assert res["chart"]["options_fan"] is None and res["state"]["dealer"] is None
    assert res["stress"]["groups"] == []
    empty = sm.build_selloff_monitor({"index": pd.DataFrame(), "spy_ohlc": pd.DataFrame()})
    assert empty["available"] is False


# ---------------------------------------------------------------------------
# router: cold cache builds once, reuses the shared SPY dealer entry, versioned key
# ---------------------------------------------------------------------------

def test_router_cold_cache_path(monkeypatch):
    from app.routers import market_router as mr

    store: dict = {"dealergex:SPY:v1": {"gamma_flip": {"level": 1.0}}}
    calls = {"build": 0}

    async def fake_get(db, key):
        return store.get(key)

    async def fake_set(db, key, value, ttl_seconds=0):
        store[key] = value

    async def fake_build(dealer):
        calls["build"] += 1
        assert dealer == {"gamma_flip": {"level": 1.0}}         # cached dealer entry reused
        return {"available": True, "x": 1}

    monkeypatch.setattr(mr, "get_cached", fake_get)
    monkeypatch.setattr(mr, "set_cached", fake_set)
    monkeypatch.setattr(mr, "get_selloff_monitor", fake_build)
    user = SimpleNamespace(id=1)
    r1 = asyncio.run(mr.selloff_monitor(refresh=False, user=user, db=None))
    r2 = asyncio.run(mr.selloff_monitor(refresh=False, user=user, db=None))
    assert r1 == r2 == {"available": True, "x": 1} and calls["build"] == 1
    assert mr._SELLOFF_CACHE_KEY in store and mr._SELLOFF_CACHE_KEY.startswith("market:selloff:")
    asyncio.run(mr.selloff_monitor(refresh=True, user=user, db=None))
    assert calls["build"] == 2


# ---------------------------------------------------------------------------
# research-driven upgrades: credit lead-lag, flight to safety, carry, dealer context, panic state
# ---------------------------------------------------------------------------

def test_credit_excess_removes_the_equity_implied_part():
    idx = _sessions("2025-01-02", 320)
    rng = np.random.default_rng(4)
    r = rng.normal(0, 1.0, len(idx))                               # SPY daily % returns
    d = -3.0 * r + rng.normal(0, 1.0, len(idx))                    # OAS bp change: −3 bp per +1% SPY
    r[-3:] = [-2.0, 0.0, -2.0]
    d[-3:] = [6.0, 0.0, 6.0]                                       # exactly what β·r implies → no excess
    spy = pd.Series(100 * np.cumprod(1 + r / 100), index=idx)
    oas = pd.Series(3.0 + np.cumsum(d) / 100, index=idx)
    cm = sm.credit_excess(spy, oas, list(idx[-5:]))
    assert cm["beta_bp_per_pct"] == pytest.approx(-3.0, abs=0.3)
    assert cm["actual_bp"] == pytest.approx(12.0, abs=0.1) and abs(cm["excess_z"]) < 1.0
    d2 = d.copy()
    d2[-3:] = [16.0, 4.0, 16.0]                                    # 24 bp MORE than equity implies
    cm2 = sm.credit_excess(spy, pd.Series(3.0 + np.cumsum(d2) / 100, index=idx), list(idx[-5:]))
    assert cm2["excess_bp"] > 20 and cm2["excess_z"] > 1.5
    g = {"groups": [{"group": "x", "rows": [{"symbol": "SPY", "window": {"change": -4, "z": -2}, "cells": []}]}],
         "credit_model": cm2}
    assert sm.diagnose_stress(g)["points"][1]["lens"] == "Credit"
    assert "beyond" in {t["id"]: t for t in sm.build_tells(_levels(), g, None, {"available": False})}["credit_wide"]["label"]


def test_flight_to_safety_joint_tail():
    idx = _sessions("2025-01-02", 300)
    rng = np.random.default_rng(9)
    z = rng.multivariate_normal([0, 0], [[1, -0.3], [-0.3, 1]], len(idx))
    s_r, b_r = z[:, 0] * 1.0, z[:, 1] * 0.8
    s_r[-1], b_r[-1] = -3.5, 2.8                                   # jointly extreme: stocks down hard, bonds up hard
    s_r[-2], b_r[-2] = -0.5, 0.4                                   # mild — not a flight to safety
    s_r[-3], b_r[-3] = 3.5, -2.8                                   # jointly extreme the WRONG way (risk-on) — not FTS
    spy = pd.Series(100 * np.cumprod(1 + s_r / 100), index=idx)
    tlt = pd.Series(100 * np.cumprod(1 + b_r / 100), index=idx)
    f = sm.flight_to_safety(spy, tlt, list(idx[-5:]))
    assert f["days"] == [idx[-1].date().isoformat()]
    assert f["rho_1y"] == pytest.approx(-0.3, abs=0.12) and f["q_threshold"] == pytest.approx(2 * 1.26 ** 2 / (1 - f["rho_1y"]), abs=0.02)


def test_yen_carry_needs_vix_confirmation():
    base = _grid_with(spy_z=-2.0, oas_bp=2, ratio=0.85, jpy=-2.0)
    base["groups"][0]["rows"].append({"symbol": "^VIX", "window": {"change": 1.0, "z": 0.3}, "cells": []})
    pts = {p["lens"]: p for p in sm.diagnose_stress(base)["points"]}
    assert "safe-haven" in pts["Yen"]["value"] and pts["Yen"]["tone"] == "watch"
    base["groups"][0]["rows"][-1]["window"]["z"] = 2.0
    pts2 = {p["lens"]: p for p in sm.diagnose_stress(base)["points"]}
    assert "carry-unwind" in pts2["Yen"]["value"] and pts2["Yen"]["tone"] == "stress"
    t = {x["id"]: x for x in sm.build_tells(_levels(), base, None, {"available": False})}
    assert t["yen"]["status"] == "met"


def test_dealer_context_is_volatility_only():
    notes = sm.dealer_context({"net_gex": {"sign": "short"}, "gamma_flip": {"level": 774.6}}, 763.25)
    assert len(notes) == 1 and "not a direction call" in notes[0]["detail"] and "sign" in notes[0]["detail"]
    assert notes[0]["flip_distance_pct"] == pytest.approx((774.6 / 763.25 - 1) * 100, abs=0.01)
    assert sm.dealer_context(None, 100) == []


def test_panic_state_and_context_in_payload():
    data = _data_dict()
    res = sm.build_selloff_monitor(data, {"net_gex": {"sign": "long"}, "gamma_flip": {"level": 1.0}}, None)
    ps = res["state"]["momentum_panic_state"]
    assert set(ps) >= {"on", "ret_24m_pct", "var126_pct_rank", "note"}
    assert ps["on"] == bool(ps["ret_24m_pct"] < 0 and ps["var126_pct_rank"] >= 70)
    assert res["context_notes"][0]["id"] == "dealer_gamma"
    a = res["analogs"]
    assert a["calibration"] is not None and "kth_sorted" not in a["calibration"]
    json.dumps(res, allow_nan=False)
