"""Plumbing tests for phase 2 of the Defend desk — POST /{id}/defend/refine.

The endpoint takes the phase-1 payload, runs the live credit-only roll search, and MERGES its candidates into the
same ranked menu (one recommendation, not two). Network/DB are faked; the optimizer's result is canned in its real
output schema so what is under test is the wiring: parse → tested leg → build → merge → re-rank → serialize."""
import asyncio
import json
from types import SimpleNamespace

import pytest

import app.routers.saved_strategy_router as router
from app.services import quote_providers, roll_optimizer_service
from defend_fixtures import ENTRY, K0, SPOT, amd_candidates, amd_menu, buyback_mark
from app.services import trade_repair_service as tr


class _Res:
    def __init__(self, obj):
        self._o = obj

    def scalar_one_or_none(self):
        return self._o


class _DB:
    def __init__(self, strategy):
        self._s = strategy

    async def execute(self, *_a, **_k):
        return _Res(self._s)


CALL_LEG = {"type": "call", "action": "SELL", "strike": 700, "qty": 1, "expiration": "2026-10-16"}


def _strategy(legs, prices):
    return SimpleNamespace(id=1, user_id=1, ticker="AMD", legs_data=json.dumps(legs),
                           entry_prices=json.dumps([{"price": p} for p in prices]))


def _phase1() -> dict:
    """What the client holds after GET /repair-menu: ranked, JSON-native, roll search pending."""
    menu = amd_menu()
    menu["roll_search"] = "pending"
    tr.rank_defenses(menu)
    return json.loads(json.dumps(router._to_native(menu)))


def _optimizer_result(cands=None):
    cands = amd_candidates() if cands is None else cands
    return {"tested": {"right": "C", "strike": K0, "qty": 1, "entry": ENTRY, "mark": round(buyback_mark(), 4), "dte": 22,
                       "covered": False},
            "spot": SPOT,
            "structure": {"support": 604.58, "resistance": 623.84, "poc": 613.89, "gamma_flip": 528.98, "gamma_wall": 630.0,
                          "gamma_regime": "long", "recent_5d": {"support": 608.33, "resistance": 626.13, "poc": 616.05},
                          "next_earnings": "2026-11-03"},
            "weights": {"probability": .4, "structure": .3, "credit": .15, "cushion": .15},
            "considered": len(cands), "candidates": cands, "note": "Cash-secured short — every roll shown is a NET CREDIT."}


@pytest.fixture
def wire(monkeypatch):
    """Fake the provider + optimizer; capture what the endpoint hands the optimizer."""
    seen = {}
    monkeypatch.setattr(quote_providers, "get_provider", lambda *a, **k: object())

    async def fake_optimize(**kw):
        seen.update(kw)
        return seen.get("_result", _optimizer_result())

    monkeypatch.setattr(roll_optimizer_service, "optimize_roll", fake_optimize)
    return seen


def _refine(strategy, payload, seen=None, result=None):
    if seen is not None and result is not None:
        seen["_result"] = result
    return asyncio.run(router.refine_defend_with_rolls(
        strategy_id=1, body=router.DefendPayloadIn(defend=payload), quote_source="yfinance",
        user=SimpleNamespace(id=1), db=_DB(strategy)))


def test_merges_the_search_into_one_ranked_menu(wire):
    out = _refine(_strategy([CALL_LEG], [ENTRY]), _phase1())
    assert out["roll_search"] == "done" and out["roll_note"]
    rolls = [a for a in out["alternatives"] if a.get("roll_meta")]
    assert len(rolls) == 6 and not any(a.get("plain_roll") for a in out["alternatives"])
    assert out["desk_recommendation"]["desk_score"] is not None
    assert any("Best credit roll" in r for r in out["desk_recommendation"]["reasons"])
    assert out["roll_structure"]["gamma_wall"] == 630.0
    json.dumps(out)                                                       # fully serializable (no numpy leaks)


def test_hands_the_optimizer_the_tested_leg_from_the_saved_trade(wire):
    _refine(_strategy([CALL_LEG], [ENTRY]), _phase1())
    assert (wire["tested_right"], wire["tested_strike"], wire["tested_qty"], wire["tested_entry"]) == ("C", 700.0, 1, ENTRY)
    assert wire["current_dte"] == 22 and wire["spot"] == SPOT and wire["covered"] is False and wire["ticker"] == "AMD"


def test_rerunning_is_idempotent(wire):
    st = _strategy([CALL_LEG], [ENTRY])
    once = json.loads(json.dumps(_refine(st, _phase1())))
    twice = _refine(st, once)
    assert len([a for a in twice["alternatives"] if a.get("roll_meta")]) == 6          # not 12
    assert twice["desk_recommendation"]["name"] == once["desk_recommendation"]["name"]


def test_search_failure_keeps_the_phase1_ranking_and_fallback_rolls(wire):
    out = _refine(_strategy([CALL_LEG], [ENTRY]), _phase1(), wire, {"error": "No later expirations available"})
    assert out["roll_search"] == "failed" and "expirations" in out["roll_note"]
    assert out["desk_recommendation"]["name"]                                      # still a recommendation
    assert any(a.get("plain_roll") for a in out["alternatives"])                   # the fixed-horizon rolls stay as a fallback
    assert not any(a.get("roll_meta") for a in out["alternatives"])


def test_optimizer_exception_is_reported_not_raised(monkeypatch):
    monkeypatch.setattr(quote_providers, "get_provider", lambda *a, **k: object())

    async def boom(**kw):
        raise RuntimeError("chain provider down")

    monkeypatch.setattr(roll_optimizer_service, "optimize_roll", boom)
    out = _refine(_strategy([CALL_LEG], [ENTRY]), _phase1())
    assert out["roll_search"] == "failed" and "chain provider down" in out["roll_note"]


def test_no_credit_roll_clears_the_bar_drops_the_stray_rolls_and_says_so(wire):
    out = _refine(_strategy([CALL_LEG], [ENTRY]), _phase1(), wire, _optimizer_result(cands=[]))
    assert out["roll_search"] == "done" and "No net-credit roll" in out["roll_note"]
    assert not any(a["category"] == "roll" for a in out["alternatives"])           # one source of truth: none


def test_a_spread_is_left_alone(wire):
    legs = [{"type": "put", "action": "SELL", "strike": 100, "qty": 1, "expiration": "2026-10-16"},
            {"type": "put", "action": "BUY", "strike": 95, "qty": 1, "expiration": "2026-10-16"}]
    out = _refine(_strategy(legs, [2.0, 0.8]), _phase1())
    assert out["roll_search"] == "skipped" and not any(a.get("roll_meta") for a in out["alternatives"])
    assert "tested_right" not in wire                                              # the optimizer was never called


def test_missing_payload_and_missing_trade_are_clean_errors(wire):
    assert "error" in _refine(_strategy([CALL_LEG], [ENTRY]), {})
    with pytest.raises(Exception) as e:
        _refine(None, _phase1())
    assert "404" in str(e.value) or "not found" in str(e.value).lower()


def test_covered_call_rolls_stay_covered(wire):
    legs = [CALL_LEG, {"type": "stock", "action": "BUY", "qty": 100, "price": 610.0}]
    out = _refine(_strategy(legs, [ENTRY, 610.0]), _phase1())
    assert wire["covered"] is True
    rolls = [a for a in out["alternatives"] if a.get("roll_meta")]
    assert rolls and all(a["defined_risk"] for a in rolls)                         # the shares define the risk


class TestParseDefendPosition:
    def test_csp(self):
        legs, near, shares, basis = router._parse_defend_position(
            _strategy([{"type": "put", "action": "SELL", "strike": 105, "qty": 2, "expiration": "2026-10-16"}], [2.10]))
        assert legs == [{"strike": 105.0, "right": "P", "sign": -1, "qty": 2, "entry": 2.10}]
        assert near == "2026-10-16" and shares == 0.0 and basis is None

    def test_covered_call_keeps_the_shares_and_their_basis(self):
        legs, near, shares, basis = router._parse_defend_position(_strategy(
            [CALL_LEG, {"type": "stock", "action": "BUY", "qty": 100}], [0.90, 610.0]))
        assert len(legs) == 1 and legs[0]["sign"] == -1 and shares == 100.0 and basis == 610.0

    def test_empty(self):
        assert router._parse_defend_position(SimpleNamespace(legs_data=None, entry_prices=None)) == ([], None, 0.0, None)
