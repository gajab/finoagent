"""Phase-1 gating in GET /{id}/repair-menu: WHEN does the live roll search apply (`roll_search`), and what
happens to the fixed-horizon fallback rolls. This is the decision that makes the frontend run phase 2 (or not),
so it is tested against the real handler with a fake provider and a controlled risk read."""
import asyncio
import datetime as dt
import json
from types import SimpleNamespace

import pytest

import app.routers.saved_strategy_router as router
from app.services import quote_providers
from app.services.stock_service import bs_price

SPOT = 629.5


def _chain_for(spot, expiry, dte, iv=0.54):
    quotes = []
    for k in range(int(spot * 0.6), int(spot * 1.5), 5):
        for right, kind in (("C", "call"), ("P", "put")):
            quotes.append(SimpleNamespace(right=right, strike=float(k), oi=100, volume=10, iv=iv,
                                          mid=round(bs_price(spot, float(k), dte / 365, 0.045, iv, kind), 2)))
    return SimpleNamespace(quotes=quotes)


class _Provider:
    def __init__(self, spot, dtes=(22, 67), ivs=None):
        self.spot = spot
        self.ivs = ivs or {}                       # {dte: iv} — a chosen TERM STRUCTURE (default flat 54%)
        today = dt.date.today()
        self.exps = [(today + dt.timedelta(days=d)).isoformat() for d in dtes]

    async def get_underlying_price(self, _t):
        return SimpleNamespace(price=self.spot)

    async def get_option_expirations(self, _t):
        return self.exps

    async def get_option_chain(self, _t, exp):
        dte = (dt.date.fromisoformat(str(exp)[:10]) - dt.date.today()).days
        return _chain_for(self.spot, exp, dte, self.ivs.get(dte, 0.54))


class _Res:
    def __init__(self, o):
        self._o = o

    def scalar_one_or_none(self):
        return self._o


class _DB:
    def __init__(self, s):
        self._s = s

    async def execute(self, *_a, **_k):
        return _Res(self._s)


def _strategy(legs, prices):
    return SimpleNamespace(id=1, user_id=1, ticker="AMD", legs_data=json.dumps(legs),
                           entry_prices=json.dumps([{"price": p} for p in prices]))


def _leg(action, typ, strike, qty=1):
    exp = (dt.date.today() + dt.timedelta(days=22)).isoformat()
    return {"type": typ, "action": action, "strike": strike, "qty": qty, "expiration": exp}


def _menu(monkeypatch, legs, prices, *, posture, p_touch=45.0, spot=SPOT, dtes=(22, 67), ivs=None, earnings=None):
    monkeypatch.setattr(quote_providers, "get_provider", lambda *a, **k: _Provider(spot, dtes, ivs))

    def fake_ta(*_a, **_k):          # controlled risk read — no network
        risk = {"posture": posture, "p_touch": p_touch, "vrp_pct": -19.4 if posture != "healthy" else 5.0,
                "trend_pct": 723.4 if posture != "healthy" else 3.0, "iv_pct": 54.2, "hv_pct": 67.2, "read": "test read"}
        ctx = {"risk": risk}
        if earnings:
            ctx["earnings"] = earnings
        return [], {"tilt": "balanced", "note": "n/a"}, ctx

    monkeypatch.setattr(router, "_defend_ta_context", fake_ta)
    return asyncio.run(router.get_repair_menu(strategy_id=1, quote_source="yfinance",
                                              user=SimpleNamespace(id=1), db=_DB(_strategy(legs, prices))))


def test_marginal_lone_call_defers_to_the_live_roll_search(monkeypatch):
    m = _menu(monkeypatch, [_leg("SELL", "call", 700)], [0.90], posture="marginal")
    assert m["roll_search"] == "pending"
    assert m["structure"] == "short_call"
    assert m["desk_recommendation"]["name"]                       # a provisional ranking exists (the fallback)
    # the fixed-horizon fallback rolls stay (tagged) until phase 2 speaks — they are what the panel shows if the search fails
    assert any(a.get("plain_roll") for a in m["alternatives"])
    assert all(a["net_cash"] >= 0 for a in m["alternatives"] if a["name"].startswith("Roll"))   # credit-only, even the fallback


def test_healthy_lone_call_skips_the_search_and_drops_the_stray_rolls(monkeypatch):
    m = _menu(monkeypatch, [_leg("SELL", "call", 700)], [0.90], posture="healthy", p_touch=8.0)
    assert m["roll_search"] == "skipped"
    assert not any(a["category"] == "roll" for a in m["alternatives"])
    assert m["desk_recommendation"]["category"] == "hold"


def test_a_credit_spread_is_not_a_roll_search_candidate(monkeypatch):
    legs = [_leg("SELL", "put", 600), _leg("BUY", "put", 580)]
    m = _menu(monkeypatch, legs, [5.0, 2.0], posture="marginal")
    assert m["roll_search"] == "skipped"                          # rolling one leg would break the structure
    assert any(a["name"].startswith("Roll the whole structure") for a in m["alternatives"])   # multi-leg rolls untouched


def test_tested_csp_is_searched(monkeypatch):
    m = _menu(monkeypatch, [_leg("SELL", "put", 640)], [4.0], posture="tested", p_touch=80.0)
    assert m["roll_search"] == "pending" and m["structure"] == "cash_secured_put"


def test_covered_call_is_searched_and_stays_covered(monkeypatch):
    legs = [_leg("SELL", "call", 700), {"type": "stock", "action": "BUY", "qty": 100, "price": 610.0}]
    m = _menu(monkeypatch, legs, [0.90, 610.0], posture="marginal")
    assert m["roll_search"] == "pending" and m["structure"] == "covered_call" and m["covered"] is True
    assert not any(a["name"].startswith("Cap the tail") for a in m["alternatives"])   # the shares already define the risk


def test_payload_is_json_serializable(monkeypatch):
    m = _menu(monkeypatch, [_leg("SELL", "call", 700)], [0.90], posture="marginal")
    json.dumps(m)


def _in_days(n):
    return (dt.date.today() + dt.timedelta(days=n)).isoformat()


def _calendar(m):
    return next(a for a in m["alternatives"] if a["name"].startswith("Calendarised"))


def test_the_far_leg_uses_the_real_far_expiry_the_chain_resolved_to(monkeypatch):
    # the far chain is the listed expiry NEAREST +45d (=67d). With listings at 22d and 56d that is the 56d one —
    # the leg must be built and marked at 56d (it used to be labelled ~66d and valued with 10 extra days of time value)
    m = _menu(monkeypatch, [_leg("SELL", "call", 700)], [0.90], posture="marginal", dtes=(22, 56))
    cal = _calendar(m)
    far = next(lg for lg in cal["legs"] if lg["action"] == "BUY")
    assert far["dte_days"] == 56 and far["expiry"] == _in_days(56) and "~56d" in cal["name"]


def test_earnings_between_the_expiries_is_flagged_on_the_iv_structure_and_deducted_from_the_calendar(monkeypatch):
    m = _menu(monkeypatch, [_leg("SELL", "call", 700)], [0.90], posture="marginal", dtes=(22, 56),
              ivs={22: 0.66, 56: 0.50}, earnings={"days": 35, "date": _in_days(35), "before_expiry": False})
    ivs = m["iv_structure"]
    assert ivs["far_spans_earnings"] is True and "spans earnings" in ivs["note"] and "not a clean term-structure read" in ivs["note"]
    cal = _calendar(m)
    assert cal["event_risk"]["points"] == 10 and "earnings" in cal["event_risk"]["note"]
    assert not m["desk_recommendation"]["category"] == "calendar"


@pytest.mark.parametrize("days", [70, 10])
def test_earnings_not_between_the_expiries_leaves_the_term_structure_read_alone(monkeypatch, days):
    # after BOTH expiries → irrelevant to the legs; before BOTH → both months carry the event, so it cancels
    m = _menu(monkeypatch, [_leg("SELL", "call", 700)], [0.90], posture="marginal", dtes=(22, 56),
              earnings={"days": days, "date": _in_days(days), "before_expiry": days <= 22})
    assert "far_spans_earnings" not in (m.get("iv_structure") or {})
    assert "Caveat" not in ((m.get("iv_structure") or {}).get("note") or "")


def test_earnings_inside_the_current_expiry_is_charged_to_holding(monkeypatch):
    m = _menu(monkeypatch, [_leg("SELL", "call", 700)], [0.90], posture="marginal", dtes=(22, 56),
              earnings={"days": 10, "date": _in_days(10), "before_expiry": True})
    hold = next(a for a in m["alternatives"] if a["category"] == "hold")
    assert hold["event_risk"]["points"] == 15
