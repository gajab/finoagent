"""Selloff Monitor Phase 2 — market internals (breadth + anatomy). No network: synthetic
constituent panels with hand-checkable answers, synthetic ETF closes, fake router cache."""
import asyncio
import datetime as dt
import json
import math
from types import SimpleNamespace

import numpy as np
import pandas as pd
import pytest

from app.services import market_internals_service as mi
from app.services import selloff_monitor_service as sm


def _dates(n: int, start: str = "2025-06-02") -> pd.DatetimeIndex:
    out, d = [], dt.date.fromisoformat(start)
    while len(out) < n:
        if sm.is_session(d):
            out.append(d)
        d += dt.timedelta(days=1)
    return pd.DatetimeIndex(out)


def _panel(T: int = 300, N: int = 60, seed: int = 0) -> tuple[pd.DataFrame, pd.DataFrame, list[dict]]:
    rng = np.random.default_rng(seed)
    idx = _dates(T)
    c = 100 * np.exp(np.cumsum(rng.normal(0.0002, 0.015, (T, N)), axis=0))
    v = rng.uniform(1e5, 1e6, (T, N))
    syms = [f"S{i:03d}" for i in range(N)]
    gics = [g for _, _, g in mi.SECTOR_ETFS]
    cons = [{"symbol": s, "name": f"Name {s}", "sector": gics[i % len(gics)]} for i, s in enumerate(syms)]
    return (pd.DataFrame(c, index=idx, columns=syms).astype("float32"),
            pd.DataFrame(v, index=idx, columns=syms).astype("float32"), cons)


def _etfs(idx: pd.DatetimeIndex, seed: int = 1, drift: dict | None = None) -> dict[str, pd.Series]:
    rng = np.random.default_rng(seed)
    out = {}
    for s in mi.ETF_SYMBOLS:
        r = rng.normal(0, 0.01, len(idx))
        if drift and s in drift:
            r[-sm.WINDOW:] += drift[s]
        out[s] = pd.Series(100 * np.exp(np.cumsum(r)), index=idx)
    return out


# ---------------------------------------------------------------------------
# constituents
# ---------------------------------------------------------------------------

def test_parse_constituents_normalizes_yahoo_symbols():
    csv = "Symbol,Security,GICS Sector\nBRK.B,Berkshire,Financials\nAAPL,Apple,Information Technology\n"
    out = mi.parse_constituents(csv)
    assert [c["symbol"] for c in out] == ["BRK-B", "AAPL"] and out[0]["sector"] == "Financials"
    with pytest.raises(ValueError):
        mi.parse_constituents("Ticker,Name\nA,B\n")


# ---------------------------------------------------------------------------
# breadth
# ---------------------------------------------------------------------------

def test_breadth_hand_checkable_last_session():
    c, v, _ = _panel(T=300, N=60)
    # last session: first 40 names up 1%, last 20 down 1%; volume 100 vs 300 per name
    c.iloc[-1] = c.iloc[-2]
    c.iloc[-1, :40] = c.iloc[-2, :40] * 1.01
    c.iloc[-1, 40:] = c.iloc[-2, 40:] * 0.99
    v.iloc[-1, :40], v.iloc[-1, 40:] = 100.0, 300.0
    # S000 makes a new 252-session closing high, S059 a new closing low
    c.iloc[-1, 0] = float(c.iloc[-252:, 0].max()) * 1.05
    c.iloc[-1, 59] = float(c.iloc[-252:, 59].min()) * 0.95
    b = mi.build_breadth(c, v)
    L = b["last"]
    assert (L["adv"], L["dec"]) == (40, 20)
    assert L["up_vol_pct"] == pytest.approx(40 * 100 / (40 * 100 + 20 * 300) * 100, abs=0.1)
    assert L["nh"] >= 1 and L["nl"] >= 1
    cc = c.astype("float64")
    exp50 = float(((cc.iloc[-1] > cc.iloc[-50:].mean())).mean() * 100)
    assert L["pct50"] == pytest.approx(exp50, abs=0.1)
    exp50_before = float(((cc.iloc[-1 - sm.WINDOW] > cc.iloc[-50 - sm.WINDOW:-sm.WINDOW].mean())).mean() * 100)
    assert L["pct50_before"] == pytest.approx(exp50_before, abs=0.1)
    w = (cc.iloc[-1] / cc.iloc[-1 - sm.WINDOW] - 1) * 100
    assert b["window"]["pct_down"] == pytest.approx(float((w < 0).mean() * 100), abs=0.1)
    assert b["window"]["median_pct"] == pytest.approx(float(w.median()), abs=0.01)
    assert len(b["series"]) == mi.SERIES_LEN and b["series"][-1]["date"] == c.index[-1].date().isoformat()


def test_breadth_ninety_percent_down_day_and_tell():
    c, v, _ = _panel()
    c.iloc[-1] = c.iloc[-2] * 0.98                               # everything down
    b = mi.build_breadth(c, v)
    assert b["last"]["up_vol_pct"] == 0.0
    assert b["ninety_pct_days"][-1] == {"date": c.index[-1].date().isoformat(), "kind": "down", "up_vol_pct": 0.0}
    tells = {t["id"]: t for t in mi.build_internal_tells(b)}
    assert tells["down90"]["status"] == "met" and tells["up90"]["status"] == "watch"
    assert tells["zweig"]["evidence"]["grade"] == "folklore" and all(t["evidence"]["note"] for t in tells.values())


def test_breadth_new_highs_lows_are_closing_basis():
    c, v, _ = _panel(N=60)
    T = len(c)
    c.iloc[:, 0] = np.linspace(50, 150, T).astype("float32")                      # monotone up → new high every day
    c.iloc[:, 1] = np.linspace(150, 50, T).astype("float32")                      # monotone down → new low every day
    b = mi.build_breadth(c, v)
    assert all(s["nh"] >= 1 and s["nl"] >= 1 for s in b["series"])
    tells = {t["id"]: t for t in mi.build_internal_tells(b)}
    assert tells["nl_gt_nh"]["status"] == ("met" if b["last"]["nl"] > b["last"]["nh"] else "not_met")


def _with_ratio(c: pd.DataFrame, ratios: list[float]) -> pd.DataFrame:
    """Overwrite the last len(ratios) sessions so exactly round(r·N) names advance each day."""
    c = c.copy()
    N = c.shape[1]
    for k, r in enumerate(ratios):
        i = len(c) - len(ratios) + k
        n_up = int(round(r * N))
        c.iloc[i] = c.iloc[i - 1] * 0.99
        c.iloc[i, :n_up] = c.iloc[i - 1, :n_up] * 1.01
    return c


def test_zweig_thrust_fires_only_when_fast():
    c, v, _ = _panel(N=100)
    fast = _with_ratio(c, [0.1] * 12 + [0.95] * 8)                # deep washout, then a surge in < 10 sessions
    b = mi.build_breadth(fast, v)
    assert b["zweig_thrust_on"] is not None
    assert {t["id"]: t for t in mi.build_internal_tells(b)}["zweig"]["status"] == "met"
    slow = _with_ratio(c, [0.1] * 4 + list(np.linspace(0.35, 0.7, 30)))   # takes far longer than 10 sessions
    assert mi.build_breadth(slow, v)["zweig_thrust_on"] is None


def test_breadth_needs_history():
    c, v, _ = _panel(T=200)
    assert mi.build_breadth(c, v)["available"] is False


# ---------------------------------------------------------------------------
# anatomy
# ---------------------------------------------------------------------------

def test_window_and_spread_stats_use_pre_window_sigma():
    idx = _dates(120)
    rng = np.random.default_rng(3)
    r = rng.normal(0, 0.01, len(idx))
    r[-sm.WINDOW:] = [-0.06, 0.0, -0.06]          # big enough that a σ leaking the window would show
    s = pd.Series(100 * np.exp(np.cumsum(r)), index=idx)
    st = mi._window_stats(s, idx)
    raw = float(np.sum(r[-sm.WINDOW:]))
    sd = float(np.std(r[-sm.WINDOW - sm.VOL_LOOKBACK:-sm.WINDOW], ddof=1))
    assert st["ret_pct"] == pytest.approx((math.exp(raw) - 1) * 100, abs=0.01)
    assert st["z"] == pytest.approx(raw / (sd * math.sqrt(sm.WINDOW)), abs=0.01)
    flat = pd.Series(100.0, index=idx)
    sp = mi._spread_stats({"A": s, "B": flat * np.exp(np.cumsum(rng.normal(0, 0.001, len(idx))))}, ["A"], ["B"], idx)
    assert sp["spread_pts"] is not None and sp["z"] < -1


def test_anatomy_sectors_concentration_movers():
    c, v, cons = _panel(N=66)
    idx = c.index
    etf = _etfs(idx)
    holdings = [{"symbol": "S000", "name": "Big A", "weight": 0.07}, {"symbol": "S001", "name": "Big B", "weight": 0.05},
                {"symbol": "ZZZZ", "name": "Not in panel", "weight": 0.04}]
    a = mi.build_anatomy(c, cons, etf, holdings)
    assert a["available"] and len(a["sectors"]) == 11
    rets = [s["ret_pct"] for s in a["sectors"]]
    assert rets == sorted(rets)                                  # worst sector first
    cc = c.astype("float64")
    w = (cc.iloc[-1] / cc.iloc[-1 - sm.WINDOW] - 1) * 100
    tech = [x["symbol"] for x in cons if x["sector"] == "Information Technology"]
    t = next(s for s in a["sectors"] if s["symbol"] == "XLK")
    assert t["members"] == len(tech) and t["pct_down"] == pytest.approx(float((w[tech] < 0).mean() * 100), abs=0.1)
    conc = a["concentration"]
    assert conc["contribution_pts"] == pytest.approx(0.07 * w["S000"] + 0.05 * w["S001"], abs=0.01)
    assert conc["weight_pct"] == pytest.approx(12.0) and len(conc["holdings"]) == 2   # unknown symbol skipped
    assert conc["rest_pts"] == pytest.approx(a["spy"]["ret_pct"] - conc["contribution_pts"], abs=0.02)
    assert a["movers"]["losers"][0]["ret_pct"] == pytest.approx(float(w.min()), abs=0.01)
    assert a["movers"]["gainers"][0]["ret_pct"] == pytest.approx(float(w.max()), abs=0.01)


def _f(**z):
    return [{"id": k, "label": k, "z": v[0], "spread_pts": v[1]} for k, v in z.items()]


def _secs(n_down: int):
    return [{"ret_pct": -1.0 if i < n_down else 1.0} for i in range(11)]


@pytest.mark.parametrize("factors,sectors,frac_down,spy,conc,primary", [
    (_f(mom_lowvol=(-2.0, -5.0), equal_cap=(2.0, 1.5)), _secs(3), 0.3, -1.9, None, "crowded_long_selling"),
    (_f(mom_lowvol=(-2.0, -5.0), equal_cap=(2.0, 1.5)), _secs(3), 0.3, +1.9, None, "short_squeeze"),
    (_f(mom_lowvol=(-2.0, -5.0), equal_cap=(-1.0, -0.5)), _secs(10), 0.8, -3.0, None, "broad_liquidation"),
    (_f(cyc_def=(-1.8, -2.0)), _secs(5), 0.5, -1.0, None, "growth_scare"),
    (_f(small_large=(-2.0, -2.5)), _secs(6), 0.6, -1.0, None, "small_caps"),
    (_f(), _secs(6), 0.6, -1.0, {"contribution_pts": -0.8, "weight_pct": 35.0, "holdings": [1] * 10}, "megacap"),
    (_f(mom_lowvol=(-1.0, -1.0)), _secs(5), 0.5, -1.0, None, "mixed"),
])
def test_classification_rules(factors, sectors, frac_down, spy, conc, primary):
    n = 100
    w = pd.Series([-1.0] * int(frac_down * n) + [1.0] * (n - int(frac_down * n)))
    cl = mi.classify_selloff(sectors, factors, w, {"ret_pct": spy}, conc)
    assert cl["primary"]["id"] == primary


def test_crowded_unwind_needs_equal_weight_outperformance():
    f = _f(mom_lowvol=(-2.5, -6.0), equal_cap=(-0.5, -0.4))
    cl = mi.classify_selloff(_secs(4), f, pd.Series([1.0] * 70 + [-1.0] * 30), {"ret_pct": -1.5}, None)
    assert cl["primary"]["id"] != "crowded_long_selling"


# ---------------------------------------------------------------------------
# end to end + degraded inputs
# ---------------------------------------------------------------------------

def test_build_market_internals_end_to_end_and_degraded():
    c, v, cons = _panel(N=66)
    etf = _etfs(c.index, drift={"XLK": -0.02, "SMH": -0.025, "MTUM": -0.02})
    res = mi.build_market_internals({"closes": c, "volumes": v, "etf": etf, "holdings": []}, cons)
    json.dumps(res, allow_nan=False)
    assert res["available"] and res["breadth"]["available"] and res["anatomy"]["available"]
    assert res["read"] and res["read"][0].startswith("Participation:")
    assert {t["side"] for t in res["tells"]} == {"continuation", "recovery"}
    no_etf = mi.build_market_internals({"closes": c, "volumes": v, "etf": {}, "holdings": []}, cons)
    assert no_etf["available"] and not no_etf["anatomy"]["available"] and no_etf["breadth"]["available"]
    no_px = mi.build_market_internals({"closes": pd.DataFrame(), "volumes": pd.DataFrame(), "etf": etf, "holdings": []}, cons)
    assert no_px["available"] and not no_px["breadth"]["available"] and no_px["tells"] == []
    nothing = mi.build_market_internals({"closes": pd.DataFrame(), "volumes": pd.DataFrame(), "etf": {}}, cons)
    assert nothing["available"] is False


# ---------------------------------------------------------------------------
# router
# ---------------------------------------------------------------------------

def test_internals_ttl_market_hours():
    from zoneinfo import ZoneInfo
    from app.routers import market_router as mr
    et = ZoneInfo("America/New_York")
    assert mr._internals_ttl(dt.datetime(2026, 10, 19, 11, 0, tzinfo=et)) == 1800     # Monday session
    assert mr._internals_ttl(dt.datetime(2026, 10, 18, 11, 0, tzinfo=et)) == 21600    # Sunday
    assert mr._internals_ttl(dt.datetime(2026, 11, 26, 11, 0, tzinfo=et)) == 21600    # Thanksgiving
    assert mr._internals_ttl(dt.datetime(2026, 10, 19, 20, 0, tzinfo=et)) == 21600    # after the close


def test_internals_router_cache_refresh_floor_and_failures(monkeypatch):
    from app.routers import market_router as mr
    store: dict = {}
    calls = {"scan": 0, "cons": 0}

    async def fake_get(db, key):
        return store.get(key)

    async def fake_set(db, key, value, ttl_seconds=0):
        store[key] = value

    async def fake_cons():
        calls["cons"] += 1
        return [{"symbol": "AAA", "name": "A", "sector": "Financials"}]

    async def fake_scan(cons):
        calls["scan"] += 1
        return {"available": True, "as_of": dt.datetime.now().strftime("%Y-%m-%d %H:%M"), "n": calls["scan"]}

    monkeypatch.setattr(mr, "get_cached", fake_get)
    monkeypatch.setattr(mr, "set_cached", fake_set)
    monkeypatch.setattr(mr, "fetch_constituents", fake_cons)
    monkeypatch.setattr(mr, "get_market_internals", fake_scan)
    u = SimpleNamespace(id=1)
    run = lambda refresh=False: asyncio.run(mr.selloff_internals(refresh=refresh, user=u, db=None))  # noqa: E731
    assert run()["n"] == 1 and run()["n"] == 1                    # cached
    assert run(refresh=True)["n"] == 1                            # refresh inside the 5-min floor → cached
    store["market:selloff:internals:v1"]["as_of"] = "2020-01-01 00:00"
    assert run(refresh=True)["n"] == 2                            # stale → real re-scan
    assert calls["cons"] == 1                                     # member list cached across scans

    store.clear()

    async def boom():
        raise RuntimeError("github down")
    monkeypatch.setattr(mr, "fetch_constituents", boom)
    from fastapi import HTTPException
    with pytest.raises(HTTPException) as ei:
        run()
    assert ei.value.status_code == 503
