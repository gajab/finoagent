"""A self-contained AAOI-style WORLD for the spread-defense tests: a put credit spread 80/70, spot $81, an option chain priced by
Black-Scholes with an earnings bump in every expiry that spans the print, and the REAL phase-1 / phase-2 handlers wired to it.
Only the network-bound pieces (yfinance structure levels, dealer gamma, the earnings lookup) are stubbed; `optimize_roll` itself
runs for real, so what is tested is the search — strikes, widths, credit/even filter, expiry selection — not a canned result.

The clock is pinned to Thu 2026-10-15 (a real calendar: Oct 16 / Nov 20 / Dec 18 are monthlies) so expiry selection is deterministic."""
import asyncio
import datetime as dt
import json
import sys
import types
from types import SimpleNamespace

import app.routers.saved_strategy_router as router
from app.services import correlated_assets_service as cas
from app.services import dealer_positioning_service as dps
from app.services import quote_providers, roll_optimizer_service as ros
from app.services.stock_service import bs_price

TODAY = dt.date(2026, 10, 15)
SPOT = 81.0
DTES = [1, 8, 15, 22, 29, 36, 64, 92]          # Oct16 Oct23 Oct30 Nov6 Nov13 Nov20 Dec18 Jan15


class _FixedDate(dt.date):
    @classmethod
    def today(cls):
        return cls(2026, 10, 15)


class _DtShim:                                  # what roll_optimizer_service sees as `datetime`
    date = _FixedDate
    timedelta = dt.timedelta
    datetime = dt.datetime


def iso(n: int) -> str:
    return (TODAY + dt.timedelta(days=n)).isoformat()


def term_ivs(earn_days: int) -> dict:
    """Pre-print expiries 80%; every expiry that spans the print carries event variance (decaying with maturity)."""
    return {n: (0.80 if n < earn_days else 0.80 + 0.12 * (1 - min(1.0, (n - earn_days) / 120))) for n in DTES}


class _Res:
    def __init__(self, o):
        self.o = o

    def scalar_one_or_none(self):
        return self.o


def install(monkeypatch, *, earn_days=28, cur_dte=8, entry=(2.60, 2.26), spot=SPOT, posture="marginal", p_touch=62.0,
            vrp=-6.0, earnings=True, legs=None, sym="AAOI", ivs=None, trend=-35.0):
    """Wire the world; returns (db, strategy). `legs` overrides the default 80/70 put spread (list of (type, action, strike))."""
    ivs = ivs or term_ivs(earn_days)

    def chain(n):
        qs = []
        for k in range(40, 125, 5):
            for right, kind in (("C", "call"), ("P", "put")):
                qs.append(SimpleNamespace(right=right, strike=float(k), oi=500, volume=50, iv=ivs[n],
                                          mid=round(max(bs_price(spot, float(k), n / 365, 0.045, ivs[n], kind), 0.05), 2)))
        return SimpleNamespace(quotes=qs)

    class Prov:
        async def get_underlying_price(self, _t):
            return SimpleNamespace(price=spot)

        async def get_option_expirations(self, _t):
            return [iso(n) for n in DTES]

        async def get_option_chain(self, _t, exp):
            return chain((dt.date.fromisoformat(str(exp)[:10]) - TODAY).days)

    spec = legs or [("put", "SELL", 80), ("put", "BUY", 70)]
    raw = [{"type": t, "action": a, "strike": k, "qty": 1, "expiration": iso(cur_dte)} for t, a, k in spec]
    strategy = SimpleNamespace(id=1, user_id=1, ticker=sym, legs_data=json.dumps(raw),
                               entry_prices=json.dumps([{"price": p} for p in entry]))

    class DB:
        async def execute(self, *a, **k):
            return _Res(strategy)

    monkeypatch.setattr(quote_providers, "get_provider", lambda *a, **k: Prov())

    def ta(*_a, **_k):
        risk = {"posture": posture, "p_touch": p_touch, "vrp_pct": vrp, "trend_pct": trend,
                "iv_pct": ivs[min(ivs, key=lambda n: abs(n - cur_dte))] * 100, "hv_pct": 85.0, "read": "x"}
        ctx = {"risk": risk}
        if earnings:
            ctx["earnings"] = {"days": earn_days, "date": iso(earn_days), "before_expiry": earn_days <= cur_dte, "note": "n"}
        return [], {"tilt": "balanced", "note": "n/a"}, ctx

    monkeypatch.setattr(router, "_defend_ta_context", ta)
    yf = types.ModuleType("yfinance")

    class _T:
        def __init__(self, *_):
            pass

        def history(self, *a, **k):
            raise RuntimeError("offline")

    yf.Ticker = _T
    monkeypatch.setitem(sys.modules, "yfinance", yf)
    monkeypatch.setattr(dps, "compute_dealer_positioning", lambda tk: None)
    monkeypatch.setattr(cas, "_next_earnings_sync", lambda t: (iso(earn_days) if earnings else None))
    monkeypatch.setattr(ros, "dt", _DtShim)
    return DB(), strategy


def _patch_router_today(monkeypatch):
    """`get_repair_menu` does `import datetime as dt` inside the handler and calls dt.date.today(): pin it via the stdlib class."""
    real = dt.date

    class Pinned(real):
        @classmethod
        def today(cls):
            return cls(2026, 10, 15)

    monkeypatch.setattr(dt, "date", Pinned)


async def _phase1(db):
    p1 = await router.get_repair_menu(strategy_id=1, quote_source="yfinance", user=SimpleNamespace(id=1), db=db)
    return json.loads(json.dumps(router._to_native(p1)))


def run_flow(monkeypatch, **kw):
    """Phase 1 then (when pending) phase 2 — exactly the two requests the panel makes. Returns (phase1, final)."""
    _patch_router_today(monkeypatch)
    db, _ = install(monkeypatch, **kw)

    async def go():
        p1 = await _phase1(db)
        out = p1
        if p1.get("roll_search") == "pending":
            out = await router.refine_defend_with_rolls(strategy_id=1, body=router.DefendPayloadIn(defend=p1), quote_source="yfinance",
                                                        user=SimpleNamespace(id=1), db=db)
        return p1, out

    return asyncio.run(go())
