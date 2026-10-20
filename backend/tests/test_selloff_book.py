"""Selloff Monitor Phase 3 — the user's book replayed through the monitor's scenarios. No network:
synthetic positions (book_tail_risk position dicts) on a monitor payload built by the REAL engine from
synthetic data; independent Black-Scholes checks for the horizon repricing."""
import asyncio
import datetime as dt
import json
import math
from types import SimpleNamespace

import numpy as np
import pytest

from app.services import selloff_book_service as sb
from app.services import selloff_monitor_service as sm
from app.services.book_tail_risk import reprice_scenario
from app.services.stock_service import bs_price

from test_selloff_monitor import _data_dict, _straddle_chain

R = 0.04


def _leg(right, strike, sign, iv=0.3, dte=30, qty=1):
    return {"right": right, "strike": float(strike), "sign": sign, "qty": qty, "iv": iv, "dte_years": dte / 365.0,
            "exp": "2026-11-20"}


def _pos(tid=1, ticker="AAA", spot=100.0, beta=1.2, legs=None, **kw):
    p = {"ticker": ticker, "name": f"{ticker} trade", "trade_id": tid, "spot": spot, "iv": 0.3, "beta": beta,
         "capital": 9000.0, "shares": 0.0, "legs": legs or [_leg("P", 90, -1)],
         "net_delta": 20.0, "net_gamma": -1.5, "net_vega": -12.0, "net_theta": 6.0}
    p.update(kw)
    return p


@pytest.fixture(scope="module")
def monitor():
    data = _data_dict()
    spot = float(data["spy_ohlc"]["Close"].iloc[-1])
    anchor = data["spy_ohlc"].index[-1].date()
    chains = []
    for exp in sm.next_sessions(anchor, 25)[::5]:
        T = (exp - anchor).days
        c, p = _straddle_chain(spot, 0.16 * math.sqrt(T / 365), strikes=[round(spot, 1) - 0.5, round(spot, 1) + 0.5])
        chains.append({"expiration": exp.isoformat(), "calls": c, "puts": p})
    data["option_chains"] = chains
    res = sm.build_selloff_monitor(data, None, None, today=anchor + dt.timedelta(days=2))
    assert res["analogs"]["available"] and res["options_cone"]["available"]
    return json.loads(json.dumps(res))          # exactly what the cache would hand the book view


# ---------------------------------------------------------------------------
# shared repricer: horizon + proportional vol (Manage Book defaults unchanged)
# ---------------------------------------------------------------------------

def test_reprice_defaults_unchanged_and_horizon_decay():
    p = _pos(legs=[_leg("P", 90, -1, iv=0.3, dte=30)], beta=1.0)
    inst = reprice_scenario([p], -0.05, 10.0, R)
    v0 = bs_price(100, 90, 30 / 365, R, 0.3, "put")
    v1 = bs_price(95, 90, 30 / 365, R, 0.4, "put")
    assert inst == pytest.approx(-100 * (v1 - v0), abs=1e-6)
    # flat market, same vol, 5 days later → the short put banks exactly its 5 days of decay
    dec = reprice_scenario([p], 0.0, 0.0, R, days=5, iv_scale=1.0)
    assert dec == pytest.approx(-100 * (bs_price(100, 90, 25 / 365, R, 0.3, "put") - v0), abs=1e-6) and dec > 0
    # proportional vol: iv × 1.5 == +15 vol points at iv 0.30
    assert reprice_scenario([p], -0.03, 0.0, R, days=5, iv_scale=1.5) == pytest.approx(
        reprice_scenario([p], -0.03, 15.0, R, days=5), abs=1e-6)


def test_leg_expiring_inside_horizon_settles_at_intrinsic():
    p = _pos(legs=[_leg("P", 100, -1, iv=0.25, dte=3)], beta=1.0)
    pnl = reprice_scenario([p], -0.04, 0.0, R, days=5, iv_scale=1.2)
    v0 = bs_price(100, 100, 3 / 365, R, 0.25, "put")
    assert pnl == pytest.approx(-100 * (4.0 - v0), abs=1e-6)       # 96 vs 100 strike → $4 intrinsic


# ---------------------------------------------------------------------------
# vol rule
# ---------------------------------------------------------------------------

def test_fit_vol_rule_recovers_known_relation():
    rng = np.random.default_rng(0)
    outs = []
    for m in rng.normal(0, 3, 40):
        outs.append({"ret_pct": float(m), "vix_ratio": float(math.exp(-0.05 - 4.0 * math.log(1 + m / 100)))})
    rule = sb.fit_vol_rule(outs)
    assert rule["a"] == pytest.approx(-0.05, abs=1e-3) and rule["b"] == pytest.approx(-4.0, abs=1e-2)
    assert sb.vol_scale(rule, -5.0) == pytest.approx(math.exp(-0.05 - 4.0 * math.log(0.95)), rel=1e-3)
    assert sb.vol_scale(rule, -60.0) == sb.VOL_SCALE_BOUNDS[1]               # clipped
    assert sb.fit_vol_rule(outs[:5]) is None and sb.vol_scale(None, -3) == 1.0


# ---------------------------------------------------------------------------
# book replay
# ---------------------------------------------------------------------------

def test_book_scenarios_history_replays_each_analog(monitor):
    p = _pos()
    res = sb.book_scenarios([p], R, monitor)
    assert res["available"] and {h["h"] for h in res["horizons"]} == set(sb.HORIZONS)
    h5 = next(h for h in res["horizons"] if h["h"] == 5)
    outs = [o for o in monitor["analogs"]["outcomes"]["h5"] if o["vix_ratio"]]
    assert h5["history"]["n"] == len(outs) == len(h5["history"]["points"])
    days = h5["days"]
    for pt, o in list(zip(h5["history"]["points"], outs))[:5]:          # each point = THAT analog's move + VIX path
        exp = reprice_scenario([p], o["ret_pct"] / 100, 0.0, R, days=days, iv_scale=o["vix_ratio"])
        assert pt["pnl"] == pytest.approx(exp, abs=1.0)
    pnl = np.array([pt["pnl"] for pt in h5["history"]["points"]])
    assert h5["history"]["p_loss"] == pytest.approx(float((pnl < 0).mean() * 100), abs=0.1)
    assert h5["history"]["worst"] == pnl.min() and h5["history"]["p10"] <= h5["history"]["p50"] <= h5["history"]["p90"]
    # options rows use the rule fitted on the same analogs
    rule = h5["vol_rule"]
    o10 = next(o for o in h5["options"] if o["q"] == "p10")
    assert o10["iv_scale"] == pytest.approx(sb.vol_scale(rule, o10["mkt_pct"]), abs=1e-3)
    assert o10["iv_scale"] != pytest.approx(1.0, abs=1e-3)              # the rule really moves vol here
    assert o10["pnl"] == pytest.approx(reprice_scenario([p], o10["mkt_pct"] / 100, 0.0, R, days=days,
                                                        iv_scale=sb.vol_scale(rule, o10["mkt_pct"])), abs=1.0)
    json.dumps(res, allow_nan=False)


def test_short_put_curve_is_concave_and_levels_present(monitor):
    res = sb.book_scenarios([_pos(beta=1.0)], R, monitor)
    pts = res["curve"]["points"]
    pnl = np.array([p["pnl"] for p in pts])
    assert pnl[0] < pnl[len(pnl) // 2]                                  # big drop hurts a short put
    assert max(pnl) - pnl[-1] < pnl[len(pnl) // 2] - pnl[0]             # upside is capped
    ids = {lv["id"] for lv in res["levels"]}
    assert {"break_low", "reclaim", "repeat"} <= ids


def test_breached_nearest_short_and_trades(monitor):
    p = _pos(spot=100.0, beta=2.0, legs=[_leg("P", 97, -1, iv=0.2), _leg("C", 110, -1, iv=0.2)])
    assert sb.breached(p, -2.0) == ["97P"]                              # β 2 × −2% → 96 < 97
    assert sb.breached(p, 6.0) == ["110C"]                              # β 2 × +6% → 112 > 110
    ns = sb.nearest_short(p, 7)
    assert ns["strike"] == 97 and ns["right"] == "P"
    assert ns["z"] == pytest.approx(3 / (100 * 0.2 * math.sqrt(7 / 365)), abs=0.01)
    res = sb.book_scenarios([p, _pos(tid=2, ticker="BBB", beta=0.5)], R, monitor)
    assert [t["pnl_bad"] for t in res["trades"]] == sorted(t["pnl_bad"] for t in res["trades"])   # worst first
    assert sum(res["by_purpose_bad"].values()) == pytest.approx(sum(t["pnl_bad"] for t in res["trades"]), abs=1)


def test_exposures_match_manage_book_definitions(monitor):
    a = _pos(tid=1, spot=100.0, beta=1.5, net_delta=30.0, net_gamma=-2.0, net_theta=5.0, net_vega=-10.0)
    b = _pos(tid=2, ticker="BBB", spot=50.0, beta=0.8, net_delta=-10.0, net_gamma=-1.0, net_theta=3.0, net_vega=-4.0)
    ex = sb.book_scenarios([a, b], R, monitor)["exposures"]
    bw = 30 * 1.5 * 100 + (-10) * 0.8 * 50
    assert ex["beta_delta_spy"] == pytest.approx(round(bw / monitor["state"]["spy_close"]), abs=1)
    assert ex["dollar_delta_per_1pct"] == pytest.approx(bw * 0.01, abs=1)
    assert ex["theta_per_day"] == 8 and ex["vega_per_pt"] == -14


def test_covered_stock_variant(monitor):
    cc = _pos(legs=[_leg("C", 105, -1)], shares=100.0, cov_shares=100.0, structure="covered_call", beta=1.0)
    res = sb.book_scenarios([cc], R, monitor)
    assert res["has_covered_stock"]
    for h in res["horizons"]:
        assert h["history_with_stock"]["p10"] < h["history"]["p10"]    # the shares' downside is really counted
    assert "history_with_stock" not in sb.book_scenarios([_pos()], R, monitor)["horizons"][0]


def test_book_read_and_unavailable(monitor):
    res = sb.book_scenarios([_pos()], R, monitor)
    lines = sb.book_read(res)
    assert lines[0].startswith("Your book across the") and any("Exposure:" in x for x in lines)
    assert sb.book_scenarios([_pos()], R, {"analogs": {"available": False}})["available"] is False


# ---------------------------------------------------------------------------
# router
# ---------------------------------------------------------------------------

class _Rows:
    def __init__(self, rows):
        self.rows = rows

    def scalars(self):
        return self

    def all(self):
        return self.rows


class _DB:
    def __init__(self, rows):
        self.rows = rows

    async def execute(self, _q):
        return _Rows(self.rows)


def test_book_router_snapshot_refresh_and_errors(monkeypatch, monitor):
    from fastapi import HTTPException
    from app.routers import market_router as mr
    from app.services import selloff_book_service as svc
    store: dict = {}

    async def fake_get(db, key):
        return store.get(key)

    async def fake_set(db, key, value, ttl_seconds=0):
        store[key] = value

    async def fake_monitor(db, refresh=False):
        return monitor

    seen = {}

    async def fake_compute(rows, purpose_of, qs, user, db, mon):
        seen.update(purpose_of=purpose_of, qs=qs, mon=mon)
        return {"available": True, "x": 1}

    monkeypatch.setattr(mr, "get_cached", fake_get)
    monkeypatch.setattr(mr, "set_cached", fake_set)
    monkeypatch.setattr(mr, "_monitor_payload", fake_monitor)
    monkeypatch.setattr(svc, "compute_book_under_tape", fake_compute)
    u = SimpleNamespace(id=7)
    row = SimpleNamespace(id=11, parameters=json.dumps({"purpose": "hedge"}))
    call = lambda refresh, qs="yfinance", rows=(row,): asyncio.run(  # noqa: E731
        mr.selloff_book(quote_source=qs, refresh=refresh, user=u, db=_DB(list(rows))))
    assert call(False) == {"available": False, "stored": False}         # nothing stored → explicit action needed
    r1 = call(True)
    assert r1["available"] and r1["stored"] is False and seen["purpose_of"] == {11: "hedge"} and seen["mon"] is monitor
    assert call(False)["stored"] is True                                # snapshot served on page load
    assert "No open trades" in call(True, rows=())["reason"]
    with pytest.raises(HTTPException) as ei:
        call(True, qs="bloomberg")
    assert ei.value.status_code == 400
