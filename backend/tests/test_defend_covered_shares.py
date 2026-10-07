"""A covered call whose SHARES LIVE IN `parameters` must be modelled COVERED by every Defend consumer.

Regression for the DRAM covered call (trade 93). Stored shape — legs_data holds ONLY the option leg, the shares are
`parameters.shares / avg_cost`, and `entry_prices = [stock row, *option rows]` (the stock FIRST):

    legs_data    = [{"action": "sell", "type": "call", "strike": 85, "premium": 0.65, ...}]
    entry_prices = [{"ticker": "DRAM", "price": 60}, {"price": 0.65}]
    parameters   = {"shares": 100, "avg_cost": 60}

`_parse_defend_position` used to (a) see ZERO shares (only stock legs inside legs_data counted) → the call was modelled
naked ("UNBOUNDED tail … NAKED", "Cover it — buy 100 sh") and (b) index `entry_prices[i]` by the option's position in
legs_data → the call's credit was the STOCK's $60 basis ("Close the trade … locks in a gain of $5,964", the arithmetic
being (60.00 − 0.36) × 100). Everything is exercised through the real handlers with a fake provider / db."""
import asyncio
import datetime as dt
import json
from types import SimpleNamespace

import pytest

import app.routers.saved_strategy_router as router
from app.services import quote_providers, roll_optimizer_service
from app.services.book_tail_risk import _position_greeks
from app.services.stock_service import bs_price
from app.services.trade_math import classify_leg_action, stock_position

SPOT = 61.67
DTE = 46
EXP = (dt.date.today() + dt.timedelta(days=DTE)).isoformat()
FAR = (dt.date.today() + dt.timedelta(days=DTE + 45)).isoformat()
CALL_MARK = 0.36          # what the live chain shows for the $85 call (the card's $0.65 credit → ~45% captured)

CALL_LEG = {"action": "sell", "type": "call", "qty": 1, "strike": 85, "expiration": EXP, "premium": 0.65, "label": "Leg 1"}
STOCK_ROW = {"ticker": "DRAM", "price": 60}


def _strategy(legs=None, entry_prices=None, params=None, stype="covered_call"):
    return SimpleNamespace(
        id=93, user_id=1, ticker="DRAM", name="DRAM Covered Call", strategy_type=stype,
        legs_data=json.dumps(legs if legs is not None else [CALL_LEG]),
        entry_prices=json.dumps(entry_prices if entry_prices is not None else [STOCK_ROW, {"price": 0.65}]),
        parameters=json.dumps(params if params is not None else {"purpose": "income", "contracts": 1, "shares": 100, "avg_cost": 60}))


def _naked():
    """The SAME call with no shares anywhere — the control that proves the covered assertions can fail."""
    return _strategy(entry_prices=[{"price": 0.65}], params={"purpose": "income", "contracts": 1}, stype="options")


def _chain(exp, iv=0.62):
    dte = (dt.date.fromisoformat(str(exp)[:10]) - dt.date.today()).days
    quotes = []
    for k in range(30, 130, 5):
        for right, kind in (("C", "call"), ("P", "put")):
            mid = round(bs_price(SPOT, float(k), dte / 365, 0.045, iv, kind), 2)
            if right == "C" and k == 85 and str(exp)[:10] == EXP:
                mid = CALL_MARK
            quotes.append(SimpleNamespace(right=right, strike=float(k), oi=100, volume=10, iv=iv, mid=mid))
    return SimpleNamespace(quotes=quotes)


class _Provider:
    async def get_underlying_price(self, _t):
        return SimpleNamespace(price=SPOT)

    async def get_option_expirations(self, _t):
        return [EXP, FAR]

    async def get_option_chain(self, _t, exp):
        return _chain(exp)


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


@pytest.fixture
def provider(monkeypatch):
    monkeypatch.setattr(quote_providers, "get_provider", lambda *a, **k: _Provider())

    def fake_ta(*_a, **_k):          # controlled, healthy risk read — no network
        return [], {"tilt": "balanced", "note": "n/a"}, {"risk": {
            "posture": "healthy", "p_touch": 3.0, "vrp_pct": 5.0, "trend_pct": 3.0, "iv_pct": 62.0, "hv_pct": 60.0, "read": "t"}}

    monkeypatch.setattr(router, "_defend_ta_context", fake_ta)


def _menu(strategy):
    return asyncio.run(router.get_repair_menu(strategy_id=93, quote_source="yfinance", user=SimpleNamespace(id=1), db=_DB(strategy)))


# ── the shared stock reader ──────────────────────────────────────────────────────────────────────

def test_stock_position_reads_parameters_first():
    p = stock_position({"shares": 100, "avg_cost": 60}, [CALL_LEG], [STOCK_ROW, {"price": 0.65}], "covered_call")
    assert p == {"shares": 100.0, "basis": 60.0, "source": "parameters"}


def test_stock_position_basis_falls_back_to_the_stock_row_at_index_zero():
    p = stock_position({"shares": 100}, [CALL_LEG], [STOCK_ROW, {"price": 0.65}], "covered_call")
    assert p["basis"] == 60.0 and p["source"] == "parameters"


def test_stock_position_never_double_counts_params_and_a_stock_leg():
    both = [CALL_LEG, {"type": "stock", "action": "BUY", "qty": 100, "price": 55.0}]
    assert stock_position({"shares": 100, "avg_cost": 60}, both, None, "covered_call")["shares"] == 100.0


def test_stock_position_falls_back_to_a_stock_leg_with_a_weighted_basis():
    legs = [CALL_LEG, {"type": "stock", "action": "BUY", "qty": 100, "price": 50.0}, {"type": "stock", "action": "BUY", "qty": 100, "price": 70.0}]
    p = stock_position({}, legs, [{"price": 0.65}, {"price": 50.0}, {"price": 70.0}], None)
    assert p["shares"] == 200.0 and p["basis"] == 60.0 and p["source"] == "legs"


def test_stock_position_signs_a_short_stock_trade_and_ignores_sold_stock_legs_as_cover():
    assert stock_position({"shares": 100}, [], None, "stock_short")["shares"] == -100.0
    assert stock_position({}, [{"type": "stock", "action": "SELL", "qty": 100}], None, None)["shares"] == -100.0
    assert stock_position({}, [CALL_LEG], None, "options") == {"shares": 0.0, "basis": None, "source": None}


# ── the Defend parser ────────────────────────────────────────────────────────────────────────────

def test_parser_sees_the_shares_and_the_calls_own_credit():
    legs, near, covered, basis = router._parse_defend_position(_strategy())
    assert covered == 100.0 and basis == 60.0
    assert legs == [{"strike": 85.0, "right": "C", "sign": -1, "qty": 1, "entry": 0.65}]
    assert near == EXP


def test_parser_does_not_hand_the_call_the_stocks_basis_as_its_credit():
    # the leg carries no premium of its own → it must resolve POSITIONALLY with the +1 stock-row offset (0.65), not [0] (=60)
    bare = {k: v for k, v in CALL_LEG.items() if k != "premium"}
    legs, _, covered, _ = router._parse_defend_position(_strategy(legs=[bare]))
    assert legs[0]["entry"] == 0.65 and covered == 100.0


def test_parser_without_shares_is_naked_and_keeps_positional_prices():
    bare = {k: v for k, v in CALL_LEG.items() if k != "premium"}
    legs, _, covered, basis = router._parse_defend_position(_strategy(legs=[bare], entry_prices=[{"price": 0.65}], params={}, stype="options"))
    assert covered == 0.0 and basis is None and legs[0]["entry"] == 0.65


def test_parser_still_reads_a_stock_leg_inside_legs_data():
    legs = [{"type": "call", "action": "SELL", "strike": 700, "qty": 1, "expiration": EXP}, {"type": "stock", "action": "BUY", "qty": 100, "price": 610.0}]
    st = SimpleNamespace(legs_data=json.dumps(legs), entry_prices=json.dumps([{"price": 0.9}, {"price": 610.0}]))   # no `parameters` at all
    out, _, covered, basis = router._parse_defend_position(st)
    assert covered == 100.0 and basis == 610.0 and out[0]["entry"] == 0.9


# ── GET /repair-menu: the whole Defend panel for this trade ──────────────────────────────────────

def test_menu_models_the_call_as_covered_not_naked(provider):
    m = _menu(_strategy())
    assert m["structure"] == "covered_call" and m["covered"] is True
    text = json.dumps(m)
    assert "NAKED" not in text and "UNBOUNDED" not in text
    assert "CAPPED outcome" in m["assignment"]["consequence"] and "$60.00" in m["assignment"]["consequence"]
    assert not any(a["name"].startswith(("Cover it", "Collar it")) for a in m["alternatives"])     # nothing to "cover" — you own the shares


def test_naked_control_still_reads_naked(provider):
    m = _menu(_naked())
    assert m["structure"] == "short_call" and m["covered"] is False
    assert "NAKED" in m["assignment"]["consequence"]
    assert any(a["name"].startswith("Cover it") for a in m["alternatives"])


def test_the_mark_is_the_options_pnl_not_the_stocks_basis_times_100(provider):
    m = _menu(_strategy())
    # sold at $0.65, now $0.36 → +$29 (45% of the credit) — NOT (60.00 − 0.36) × 100 = $5,964
    assert m["unrealized_pnl"] == pytest.approx((0.65 - CALL_MARK) * 100, abs=1.0)
    assert m["assignment"]["effective_basis"] == 60.0


def test_close_is_buy_back_keeping_the_shares_and_is_priced_like_hold(provider):
    m = _menu(_strategy())
    close = next(a for a in m["alternatives"] if a["category"] == "exit")
    hold = next(a for a in m["alternatives"] if a["category"] == "hold")
    assert close["keeps_shares"] is True and close["legs"] == [] and "shares stay" in close["mechanics"]
    assert close["max_loss"] is not None and close["max_loss"] < -5000        # the shares' own tail is still there (not a flat +$29)
    assert abs(close["ev"] - hold["ev"]) < 100                                # same shares, same law, same horizon → comparable
    assert close["ev"] < 1000                                                 # …and nowhere near the phantom +$5,964


def test_every_alternative_carries_the_shares(provider):
    m = _menu(_strategy())
    hold = next(a for a in m["alternatives"] if a["category"] == "hold")
    # holding 100 sh @ $60 can lose ~$5.9k; an alternative that "defines" risk at ~$29 has simply forgotten the stock
    for a in m["alternatives"]:
        assert a["max_loss"] is not None and a["max_loss"] < -500, a["name"]
    assert hold["max_loss"] == pytest.approx(-5934, abs=5)                    # = 100 × (0.01 − 60) + the $65 credit (the card shows −$5,935)


def test_no_naked_call_recovery_structures_on_a_covered_call(provider):
    m = _menu(_strategy())
    assert not any(a["category"] in ("calendar", "ratio", "butterfly") for a in m["alternatives"])
    assert m["desk_recommendation"]["category"] not in ("calendar", "ratio", "butterfly")


def test_menu_payload_is_json_serializable(provider):
    json.dumps(_menu(_strategy()))


# ── POST /defend/refine — the other consumer of the same parser ─────────────────────────────────

def test_roll_search_is_told_the_call_is_covered_and_its_real_entry(monkeypatch):
    seen = {}
    monkeypatch.setattr(quote_providers, "get_provider", lambda *a, **k: object())

    async def fake_optimize(**kw):
        seen.update(kw)
        return {"error": "stop here"}                       # we only care what the handler hands the optimizer

    monkeypatch.setattr(roll_optimizer_service, "optimize_roll", fake_optimize)
    payload = {"alternatives": [{"category": "hold"}], "spot": SPOT, "dte_days": DTE}
    asyncio.run(router.refine_defend_with_rolls(strategy_id=93, body=router.DefendPayloadIn(defend=payload),
                                                quote_source="yfinance", user=SimpleNamespace(id=1), db=_DB(_strategy())))
    assert seen["covered"] is True and seen["tested_entry"] == 0.65 and seen["tested_strike"] == 85.0


# ── book tail risk — the other "is it covered?" reader ─────────────────────────────────────────

def test_book_tail_risk_marks_the_params_shares_call_covered():
    from app.services.book_tail_risk import _is_covered
    prov = _Provider()
    pos = asyncio.run(_position_greeks(_strategy(), prov, 0.045, dt.date.today(), {}, {}))
    assert pos["shares"] == 100.0 and pos["has_stock"] is True and _is_covered(pos)
    naked = asyncio.run(_position_greeks(_naked(), prov, 0.045, dt.date.today(), {}, {}))
    assert naked["shares"] == 0.0 and not _is_covered(naked)


# ── per-leg advice with no mark ──────────────────────────────────────────────────────────────────

def test_a_leg_with_no_mark_gets_no_close_or_hold_call():
    r = classify_leg_action(sign=-1, right="C", p_itm=None, entry_prem=0.65, current_mid=None, dte=46)
    assert r["action"] == "NO_QUOTE" and r["captured_pct"] is None and r.get("no_quote") is True
    assert "never priced at $0" in r["reason"]


def test_a_marked_leg_is_unchanged():
    r = classify_leg_action(sign=-1, right="C", p_itm=0.08, entry_prem=0.65, current_mid=0.36, dte=46)
    assert r["action"] != "NO_QUOTE" and r["captured_pct"] == pytest.approx(44.6, abs=0.1)
