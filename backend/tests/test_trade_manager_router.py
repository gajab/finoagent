"""Router layer for the Trade Manager: ownership/404, missing key → 400, no-data → 404, 502 on engine failure."""
import asyncio
import json
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from app.routers import saved_strategy_router as R
from app.services import trade_manager_service as TM


class _Res:
    def __init__(self, row):
        self._row = row

    def scalar_one_or_none(self):
        return self._row


class _DB:
    def __init__(self, row):
        self._row = row

    async def execute(self, *_a, **_k):
        return _Res(self._row)


USER = SimpleNamespace(id=7)
import datetime as _dt
ROW = SimpleNamespace(id=1, ticker="AAPL", name="n", strategy_type="put_credit_spread", notes="why", trade_status="active",
                      legs_data=json.dumps([{"action": "SELL", "type": "put", "strike": 90, "expiration": "2099-01-01", "qty": 1}]),
                      parameters=json.dumps({"covered": False, "closed_legs": [{"realized": 120.0}, {"realized": 30.0, "roll": True}]}),
                      entry_date=_dt.datetime(2026, 9, 14, tzinfo=_dt.timezone.utc), entry_prices=json.dumps([{"price": 1.25}]), entry_net_debit=-125.0)
BODY = R.TradeManagerRequest(pnl_snapshot={"underlying_price": 100})


def test_inputs_parses_the_saved_trade():
    out = asyncio.run(R._trade_manager_inputs(1, USER, _DB(ROW)))
    assert out["ticker"] == "AAPL" and out["legs_data"][0]["strike"] == 90 and out["parameters"]["covered"] is False and out["notes"] == "why"
    assert out["trade_status"] == "active" and out["entry_date"].startswith("2026-09-14") and out["entry_prices"] == [{"price": 1.25}] and out["entry_net_debit"] == -125.0
    assert out["realized_banked"] == 120.0                                       # only genuine closes — roll realized is the cost-basis adjustment


def test_inputs_trade_not_found_is_404():
    with pytest.raises(HTTPException) as e:
        asyncio.run(R._trade_manager_inputs(1, USER, _DB(None)))
    assert e.value.status_code == 404


def test_endpoint_passes_snapshot_and_desk_to_the_engine(monkeypatch):
    seen = {}

    async def fake(db, strategy, pnl, desk):
        seen.update(strategy=strategy, pnl=pnl, desk=desk)
        return {"ok": True}
    monkeypatch.setattr(TM, "run_trade_manager", fake)
    body = R.TradeManagerRequest(pnl_snapshot={"underlying_price": 100}, desk={"signal": "HOLD"})
    out = asyncio.run(R.run_trade_manager_endpoint(1, body, user=USER, db=_DB(ROW)))
    assert out == {"ok": True} and seen["pnl"]["underlying_price"] == 100 and seen["desk"] == {"signal": "HOLD"} and seen["strategy"]["ticker"] == "AAPL"


def test_endpoint_no_market_data_is_404(monkeypatch):
    async def boom(*a, **k):
        raise TM.NoMarketData("no history for AAPL")
    monkeypatch.setattr(TM, "run_trade_manager", boom)
    with pytest.raises(HTTPException) as e:
        asyncio.run(R.run_trade_manager_endpoint(1, BODY, user=USER, db=_DB(ROW)))
    assert e.value.status_code == 404 and "no history" in e.value.detail


def test_endpoint_engine_failure_is_502(monkeypatch):
    async def boom(*a, **k):
        raise RuntimeError("yfinance exploded")
    monkeypatch.setattr(TM, "run_trade_manager", boom)
    with pytest.raises(HTTPException) as e:
        asyncio.run(R.run_trade_manager_endpoint(1, BODY, user=USER, db=_DB(ROW)))
    assert e.value.status_code == 502


def test_ai_endpoint_requires_an_api_key(monkeypatch):
    async def nokey(*a, **k):
        return None
    monkeypatch.setattr(R, "get_user_api_key", nokey)
    with pytest.raises(HTTPException) as e:
        asyncio.run(R.run_trade_manager_ai_endpoint(1, BODY, user=USER, db=_DB(ROW)))
    assert e.value.status_code == 400 and "OpenAI" in e.value.detail


def test_ai_endpoint_happy_path_and_errors(monkeypatch):
    async def key(*a, **k):
        return "sk-test"
    monkeypatch.setattr(R, "get_user_api_key", key)

    async def ok(db, strategy, pnl, api_key):
        assert api_key == "sk-test"
        return {"verdict": "HOLD"}
    monkeypatch.setattr(TM, "run_trade_manager_ai", ok)
    assert asyncio.run(R.run_trade_manager_ai_endpoint(1, BODY, user=USER, db=_DB(ROW)))["verdict"] == "HOLD"

    async def nodata(*a, **k):
        raise TM.NoMarketData("x")
    monkeypatch.setattr(TM, "run_trade_manager_ai", nodata)
    with pytest.raises(HTTPException) as e:
        asyncio.run(R.run_trade_manager_ai_endpoint(1, BODY, user=USER, db=_DB(ROW)))
    assert e.value.status_code == 404

    async def llm_down(*a, **k):
        raise RuntimeError("429")
    monkeypatch.setattr(TM, "run_trade_manager_ai", llm_down)
    with pytest.raises(HTTPException) as e:
        asyncio.run(R.run_trade_manager_ai_endpoint(1, BODY, user=USER, db=_DB(ROW)))
    assert e.value.status_code == 502


def test_routes_are_registered_post():
    paths = {(tuple(sorted(r.methods)), r.path) for r in R.router.routes if "trade-manager" in r.path}
    assert (("POST",), "/api/saved-strategies/{strategy_id}/trade-manager") in paths
    assert (("POST",), "/api/saved-strategies/{strategy_id}/trade-manager/ai") in paths


def test_research_strategy_that_was_never_entered_is_refused():
    row = SimpleNamespace(**{**ROW.__dict__, "trade_status": None})
    with pytest.raises(HTTPException) as e:
        asyncio.run(R._trade_manager_inputs(1, USER, _DB(row)))
    assert e.value.status_code == 409 and "OPEN trades" in e.value.detail and "not entered yet" in e.value.detail


def test_closed_trade_is_refused_on_both_endpoints(monkeypatch):
    row = SimpleNamespace(**{**ROW.__dict__, "trade_status": "closed"})
    with pytest.raises(HTTPException) as e:
        asyncio.run(R.run_trade_manager_endpoint(1, BODY, user=USER, db=_DB(row)))
    assert e.value.status_code == 409 and "already closed" in e.value.detail

    async def key(*a, **k):
        return "sk-test"
    monkeypatch.setattr(R, "get_user_api_key", key)
    with pytest.raises(HTTPException) as e2:
        asyncio.run(R.run_trade_manager_ai_endpoint(1, BODY, user=USER, db=_DB(row)))
    assert e2.value.status_code == 409


def test_active_trade_passes_entry_facts_to_the_engine(monkeypatch):
    from app.services import trade_manager_service as TM
    seen = {}

    async def fake(db, strategy, pnl, desk):
        seen.update(strategy)
        return {"ok": True}
    monkeypatch.setattr(TM, "run_trade_manager", fake)
    asyncio.run(R.run_trade_manager_endpoint(1, BODY, user=USER, db=_DB(ROW)))
    assert seen["trade_status"] == "active" and seen["entry_date"].startswith("2026-09-14") and seen["entry_prices"] and seen["realized_banked"] == 120.0
    assert seen["roll"] is not None and seen["roll"]["count"] >= 0
