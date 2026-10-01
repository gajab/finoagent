"""Tests for paper_trade_service — the cost-basis + short-premium mark-to-market math.

Run from backend/:
    source venv/bin/activate
    python -m pytest tests/test_paper_trade.py -v

The user-trust guarantee here is the P&L sign + captured_pct convention: a short-premium
income trade is SOLD for the entry net credit and bought back at the current net value, so
``unrealized = entry_credit − current_value`` (positive when premium has DECAYED in your
favour), and ``captured_pct`` is the PERCENT of max profit banked (negative when the premium
moved against you). ``max_loss`` must be handed to the management overlay as a NEGATIVE number.
"""
from datetime import date, timedelta
from types import SimpleNamespace

from app.services.paper_trade_service import (
    build_paper_trade_fields,
    _pnl_block,
    dte_remaining,
    CONTRACT_MULTIPLIER,
)

FUTURE = (date.today() + timedelta(days=30)).isoformat()


def _opp(**over):
    base = {
        "structure": "cash_secured_put",
        "expiration": FUTURE,
        "label": "CSP $95",
        "short_strike": 95.0,
        "premium_per_share": 2.50,
        "desk_score": 78,
        "algo_grade": "A-",
        "legs": [{"action": "SELL", "type": "PUT", "strike": 95.0, "expiration": FUTURE}],
        "max_loss": 9250.0,   # positive magnitude, as the scan reports it
    }
    base.update(over)
    return base


def _pt(**over):
    d = {
        "contracts": 1,
        "entry_premium_per_share": 2.50,
        "entry_credit": 250.0,
        "expiration": FUTURE,
        "entry_spot": 100.0,
    }
    d.update(over)
    return SimpleNamespace(**d)


def test_build_fields_cost_basis_is_net_credit():
    f = build_paper_trade_fields(_opp(), contracts=1, spot=100.0)
    assert f["structure"] == "cash_secured_put"
    assert f["short_strike"] == 95.0
    assert f["entry_premium_per_share"] == 2.50
    # cost basis = premium/share × 100 × contracts
    assert f["entry_credit"] == 2.50 * CONTRACT_MULTIPLIER * 1
    assert f["placed_desk_score"] == 78 and f["placed_algo_grade"] == "A-"


def test_build_fields_backs_out_per_share_from_premium():
    # No premium_per_share → back it out of the per-contract premium.
    f = build_paper_trade_fields(_opp(premium_per_share=None, premium=250.0), contracts=1)
    assert f["entry_premium_per_share"] == 2.50
    assert f["entry_credit"] == 250.0


def test_pnl_profit_when_premium_decays():
    # Sold for 2.50, now worth 1.00 → +150 profit, 60% of max profit captured.
    pnl = _pnl_block(_pt(), {"premium_per_share": 1.00, "max_loss": 9250.0}, spot=102.0)
    assert pnl["cost_basis"] == 250.0
    assert pnl["current_value"] == 100.0
    assert pnl["unrealized_pnl"] == 150.0
    assert pnl["captured_pct"] == 60.0
    assert pnl["unrealized_pct"] == 60.0
    assert pnl["spot_change_pct"] == 2.0
    # max_loss handed to the overlay is NEGATIVE.
    assert pnl["max_loss"] == -9250.0
    assert pnl["max_profit"] == 250.0


def test_pnl_loss_when_premium_expands():
    # Sold for 2.50, now worth 4.00 → −150 loss, captured goes negative (moved against you).
    pnl = _pnl_block(_pt(), {"premium_per_share": 4.00, "max_loss": 9250.0}, spot=90.0)
    assert pnl["current_value"] == 400.0
    assert pnl["unrealized_pnl"] == -150.0
    assert pnl["captured_pct"] == -60.0
    assert pnl["spot_change_pct"] == -10.0


def test_pnl_scales_by_contracts():
    pnl = _pnl_block(_pt(contracts=3, entry_credit=750.0),
                     {"premium_per_share": 1.00, "max_loss": 9250.0}, spot=100.0)
    assert pnl["current_value"] == 300.0          # 1.00 × 100 × 3
    assert pnl["unrealized_pnl"] == 450.0          # 750 − 300
    assert pnl["max_loss"] == -9250.0 * 3


def test_dte_remaining_parses_and_floors():
    assert dte_remaining(FUTURE) == 30
    assert dte_remaining((date.today() - timedelta(days=5)).isoformat()) == 1   # min 1
    assert dte_remaining(None) is None
    assert dte_remaining("not-a-date") is None


# ---------------------------------------------------------------------------
# Router flow — create → list → get → refresh → close → delete against a real
# (temp SQLite) DB, with the heavy recompute stubbed so no network is touched.
# The key guarantee: create/list/get NEVER call recompute (the laziness contract);
# only /refresh and /close do.
# ---------------------------------------------------------------------------

import asyncio
import json
import os
import tempfile

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.database import Base
from app.models import User
from app.routers import paper_trade_router as R
from app.services import paper_trade_service


def _opp_full():
    return {
        **_opp(),
        "desk_metrics": {"quant": {"subscores": {"pop": 80, "carry": 60, "edge": 55, "tail": 70, "sortino": 50}}},
    }


async def _flow():
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    engine = create_async_engine(f"sqlite+aiosqlite:///{path}")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    Session = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    # seed a user
    async with Session() as s:
        u = User(google_id="g-paper", email="paper@example.com", name="Paper")
        s.add(u); await s.commit(); await s.refresh(u)
        uid = u.id

    calls: list[int] = []
    orig_recompute = paper_trade_service.recompute

    async def fake_recompute(pt, user, db, quote_source="yfinance"):
        calls.append(pt.id)
        pt.last_pnl = 150.0; pt.last_desk_score = 70.0; pt.last_algo_grade = "B"
        pt.last_spot = 101.0; pt.last_value_per_share = 1.0
        pt.last_eval = json.dumps({"matched": True, "desk_score": 70.0})
        import datetime as dt
        pt.last_eval_at = dt.datetime.now(dt.timezone.utc)
        if pt.entry_spot is None:
            pt.entry_spot = 100.0
        await db.commit(); await db.refresh(pt)
        return {"matched": True, "desk_score": 70.0, "algo_grade": "B",
                "pnl": {"unrealized_pnl": 150.0, "current_value": 100.0, "current_spot": 101.0}}

    paper_trade_service.recompute = fake_recompute
    try:
        # CREATE — no compute
        async with Session() as s:
            u = await s.get(User, uid)
            created = await R.create_paper_trade(R.PaperTradeIn(ticker="tsla", opp=_opp_full(), spot=100.0), u, s)
        assert created["ticker"] == "TSLA"
        assert created["cost_basis"] == 250.0
        assert created["placed_desk_score"] == 78
        assert created["last_pnl"] is None          # nothing priced yet
        pid = created["id"]
        assert calls == []                           # ← laziness: create did NOT reprice

        # LIST — no compute
        async with Session() as s:
            u = await s.get(User, uid)
            listed = await R.list_paper_trades("all", u, s)
        assert listed["counts"]["open"] == 1 and listed["counts"]["total"] == 1
        assert listed["items"][0]["id"] == pid
        assert calls == []                           # ← laziness: list did NOT reprice

        # GET — returns the placed snapshot, no compute
        async with Session() as s:
            u = await s.get(User, uid)
            detail = await R.get_paper_trade(pid, u, s)
        assert detail["placed_snapshot"]["structure"] == "cash_secured_put"
        assert detail["last_eval"] is None
        assert calls == []                           # ← laziness: get did NOT reprice

        # REFRESH — the ONLY heavy path; caches the current read
        async with Session() as s:
            u = await s.get(User, uid)
            refreshed = await R.refresh_paper_trade(pid, "yfinance", u, s)
        assert refreshed["matched"] is True and refreshed["desk_score"] == 70.0
        assert calls == [pid]                        # recompute ran exactly once

        # the cache is now persisted → GET/LIST show it with no further compute
        async with Session() as s:
            u = await s.get(User, uid)
            detail2 = await R.get_paper_trade(pid, u, s)
        assert detail2["last_pnl"] == 150.0 and detail2["last_desk_score"] == 70.0
        assert detail2["last_eval"] is not None
        assert calls == [pid]

        # CLOSE — banks P&L, archives
        async with Session() as s:
            u = await s.get(User, uid)
            closed = await R.close_paper_trade(pid, R.CloseIn(), u, s)
        assert closed["status"] == "closed"
        assert closed["close_pnl"] == 150.0

        # DELETE
        async with Session() as s:
            u = await s.get(User, uid)
            await R.delete_paper_trade(pid, u, s)
        async with Session() as s:
            u = await s.get(User, uid)
            after = await R.list_paper_trades("all", u, s)
        assert after["counts"]["total"] == 0
    finally:
        paper_trade_service.recompute = orig_recompute
        await engine.dispose()
        os.unlink(path)


def test_paper_trade_router_flow_and_laziness():
    asyncio.run(_flow())


# ---------------------------------------------------------------------------
# Expiry settlement — intrinsic value at expiry. OTM ⇒ keep the whole premium;
# ITM ⇒ premium − intrinsic. Generalizes to spreads/condors (short − long, capped).
# ---------------------------------------------------------------------------

from app.services.paper_trade_service import intrinsic_settlement, is_expired, settle_expiry

PAST = (date.today() - timedelta(days=3)).isoformat()


def test_intrinsic_otm_keeps_full_premium():
    # short put K=95, close 100 → OTM → nothing owed
    st = intrinsic_settlement([{"action": "SELL", "type": "PUT", "strike": 95.0}], 1, 100.0)
    assert st["settlement_cost"] == 0.0 and st["itm"] is False


def test_intrinsic_itm_short_put():
    # short put K=95, close 90 → intrinsic 5/sh → owe $500
    st = intrinsic_settlement([{"action": "SELL", "type": "PUT", "strike": 95.0}], 1, 90.0)
    assert st["settlement_cost"] == 500.0 and st["itm"] is True


def test_intrinsic_itm_short_call():
    # covered/short call K=105, close 110 → intrinsic 5/sh → owe $500
    st = intrinsic_settlement([{"action": "SELL", "type": "CALL", "strike": 105.0}], 1, 110.0)
    assert st["settlement_cost"] == 500.0 and st["itm"] is True


def test_intrinsic_credit_spread_nets_and_caps():
    legs = [{"action": "SELL", "type": "PUT", "strike": 95.0},
            {"action": "BUY", "type": "PUT", "strike": 90.0}]
    # close 92 → short 3, long 0 → net 3/sh → $300
    assert intrinsic_settlement(legs, 1, 92.0)["settlement_cost"] == 300.0
    # close 85 → short 10, long 5 → net 5/sh (capped at width) → $500
    assert intrinsic_settlement(legs, 1, 85.0)["settlement_cost"] == 500.0
    # close 100 → both OTM → 0
    assert intrinsic_settlement(legs, 1, 100.0)["settlement_cost"] == 0.0


def test_is_expired():
    class PT:
        expiration = PAST
    assert is_expired(PT()) is True
    PT.expiration = FUTURE
    assert is_expired(PT()) is False
    PT.expiration = date.today().isoformat()   # expiry day itself → still live
    assert is_expired(PT()) is False


async def _settle(pt, close_price):
    import app.services.paper_trade_service as m
    orig = m._fetch_expiry_close
    async def fake(_t, _e): return close_price
    m._fetch_expiry_close = fake
    try:
        return await settle_expiry(pt, db=None, commit=False)
    finally:
        m._fetch_expiry_close = orig


def _settle_pt():
    return SimpleNamespace(
        id=1, ticker="TSLA", expiration=PAST,
        legs=json.dumps([{"action": "SELL", "type": "PUT", "strike": 95.0}]),
        contracts=1, entry_premium_per_share=2.50, entry_credit=250.0, entry_spot=100.0,
    )


def test_settle_expiry_itm_realizes_premium_minus_intrinsic():
    pt = _settle_pt()
    summary = asyncio.run(_settle(pt, 90.0))   # ITM put: intrinsic 5 → cost 500
    assert summary["itm"] is True and summary["realized_pnl"] == -250.0   # 250 credit − 500
    assert pt.status == "expired" and pt.close_pnl == -250.0
    assert "in-the-money" in pt.close_note


def test_settle_expiry_otm_realizes_full_premium():
    pt = _settle_pt()
    summary = asyncio.run(_settle(pt, 100.0))  # OTM put → keep it all
    assert summary["itm"] is False and summary["realized_pnl"] == 250.0
    assert pt.status == "expired" and pt.close_pnl == 250.0
    assert "out-of-the-money" in pt.close_note


async def _expiry_list_flow():
    """A trade placed with a PAST expiration auto-settles on the next list load."""
    import app.services.paper_trade_service as m
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    engine = create_async_engine(f"sqlite+aiosqlite:///{path}")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    Session = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with Session() as s:
        u = User(google_id="g-exp", email="exp@example.com", name="Exp")
        s.add(u); await s.commit(); await s.refresh(u)
        uid = u.id

    orig = m._fetch_expiry_close
    async def fake(_t, _e): return 90.0   # ITM for a 95 put
    m._fetch_expiry_close = fake
    try:
        opp = {**_opp_full(), "expiration": PAST}
        async with Session() as s:
            u = await s.get(User, uid)
            created = await R.create_paper_trade(R.PaperTradeIn(ticker="tsla", opp=opp, spot=100.0), u, s)
        assert created["status"] == "open"

        # LIST auto-settles the expired trade
        async with Session() as s:
            u = await s.get(User, uid)
            listed = await R.list_paper_trades("all", u, s)
        row = listed["items"][0]
        assert row["status"] == "expired"
        assert row["expiry_outcome"] == "itm"
        assert row["close_pnl"] == -250.0          # 250 credit − 500 intrinsic
        # terminal → shows under Closed, not Open
        assert listed["counts"]["open"] == 0 and listed["counts"]["closed"] == 1
        async with Session() as s:
            u = await s.get(User, uid)
            openonly = await R.list_paper_trades("open", u, s)
        assert len(openonly["items"]) == 0
    finally:
        m._fetch_expiry_close = orig
        await engine.dispose()
        os.unlink(path)


def test_expiry_auto_settles_on_list():
    asyncio.run(_expiry_list_flow())
