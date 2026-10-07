"""Removing / deleting / closing the STOCK of an income trade must never remove its option income.

A covered call carries the stock (`parameters.shares`, entry_prices[0]) AND the option income on one row:
open short call(s) = ONGOING, `closed_legs` = REALIZED / PARTIAL (rolls tagged `roll`). Three things used to
take that income down with the stock:

  1. Active card "Delete" → DELETE /{id} hard-deleted the whole row (income gone from Closed + Active).
  2. Closed-ledger "delete" on a single-part row of a still-ACTIVE trade → also DELETE /{id}: the live
     shares and open call vanished although the dialog said "from your closed journal".
  3. Selling the last share through the stock ledger flipped a trade to `closed` even with option legs open.

Fixtures are the real NOK / DRAM covered-call rows (2026-10-05): stock is NOT a legs_data leg; it sits at
entry_prices[0] with option leg i at [i+1].
"""
import asyncio
import copy
import datetime as dt
import json
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

import app.routers.saved_strategy_router as R


def _call(strike, exp="2026-11-20", prem=0.65):
    return {"action": "sell", "type": "call", "qty": 1, "strike": strike, "expiration": exp, "premium": prem, "label": "Leg 1"}


def _closed_opt(realized, strike=60, roll=False, when="2026-08-28T01:13:14+00:00"):
    d = {"leg_index": 0, "action": "sell", "type": "call", "strike": strike, "qty": 1, "entry_price": 1.0,
         "exit_price": 0.5, "realized": realized, "closed_at": when}
    if roll:
        d["roll"] = True
    return d


def _closed_stock(realized):
    return {"leg_index": None, "type": "stock", "qty": 100, "entry_price": 60, "exit_price": 55,
            "realized": realized, "closed_at": "2026-09-01T00:00:00+00:00"}


# DRAM: 100 sh, one OPEN call, $164 realized from 2 closed option legs
def _dram_parts():
    legs = [_call(85)]
    ep = [{"ticker": "DRAM", "price": 60}, {"price": 0.65}]
    params = {"purpose": "income", "contracts": 1, "shares": 100, "avg_cost": 60, "realized_pnl": 164,
              "closed_legs": [_closed_opt(119), _closed_opt(45, strike=80)], "last_pnl": {"unrealized_pnl": 5}, "last_pnl_at": "x"}
    return legs, ep, params


# NOK: 700 sh, NO open call, $20 realized from one closed option leg
def _nok_parts():
    return [], [{"ticker": "NOK", "price": 13}, {"price": 0.16}], {
        "purpose": "income", "shares": 700, "avg_cost": 13, "realized_pnl": 20, "closed_legs": [_closed_opt(20, strike=14)]}


# ── pure helper ───────────────────────────────────────────────────────────────────────────────────

def test_ongoing_call_and_realized_income_survive_removing_the_stock():
    legs, ep, params = _dram_parts()
    out = R._strip_stock_component(legs, ep, params)
    assert out["params"]["shares"] == 0 and "avg_cost" not in out["params"]
    assert out["legs"] == legs                                           # the open short call is untouched
    assert out["entry_prices"] == [{"price": 0.65}]                      # stock slot dropped → option entry stays aligned
    assert out["params"]["realized_pnl"] == 164                          # option income intact
    assert [l["realized"] for l in out["params"]["closed_legs"]] == [119, 45]
    assert out["has_open_options"] and out["has_option_income"]
    assert out["stock_realized_removed"] == 0
    assert "last_pnl" not in out["params"] and "last_pnl_at" not in out["params"]   # stale snapshot included the stock


def test_banked_income_with_no_open_call_still_counts_as_income():
    legs, ep, params = _nok_parts()
    out = R._strip_stock_component(legs, ep, params)
    assert not out["has_open_options"] and out["has_option_income"]
    assert out["params"]["realized_pnl"] == 20


def test_a_banked_stock_close_is_dropped_but_option_realized_is_not():
    legs, ep, params = _dram_parts()
    params["closed_legs"].append(_closed_stock(-50.0))
    params["realized_pnl"] = 114.0                                       # 164 option income − 50 stock loss
    out = R._strip_stock_component(legs, ep, params)
    assert all((l.get("type") or "") != "stock" for l in out["params"]["closed_legs"])
    assert out["params"]["realized_pnl"] == 164.0                        # exactly the option income
    assert out["stock_realized_removed"] == -50.0


def test_realized_that_predates_closed_legs_bookkeeping_survives():
    out = R._strip_stock_component([], [{"price": 10}, {"price": 0.2}], {"shares": 100, "realized_pnl": 100.0})
    assert out["params"]["realized_pnl"] == 100.0 and out["has_option_income"]


def test_stock_as_a_legs_data_leg_drops_its_positional_entry_price():
    legs = [{"action": "buy", "type": "stock", "qty": 100}, _call(85)]
    out = R._strip_stock_component(legs, [{"price": 60}, {"price": 0.65}], {"shares": 100, "realized_pnl": 10, "closed_legs": [_closed_opt(10)]})
    assert out["legs"] == [_call(85)] and out["entry_prices"] == [{"price": 0.65}]


def test_nothing_to_remove_and_nothing_to_keep():
    assert R._strip_stock_component([_call(85)], [{"price": 0.65}], {"shares": 0}) is None       # no stock at all
    out = R._strip_stock_component([], [{"price": 60}], {"shares": 100})                          # stock only
    assert out["has_option_income"] is False


def test_it_never_mutates_its_inputs():
    legs, ep, params = _dram_parts()
    snap = copy.deepcopy((legs, ep, params))
    R._strip_stock_component(legs, ep, params)
    assert (legs, ep, params) == snap


# ── fakes for the handlers ────────────────────────────────────────────────────────────────────────

class _Res:
    def __init__(self, o):
        self._o = o

    def scalar_one_or_none(self):
        return self._o

    def scalars(self):
        return SimpleNamespace(all=lambda: self._o)


class _DB:
    def __init__(self, obj):
        self.obj, self.deleted, self.commits = obj, [], 0

    async def execute(self, *_a, **_k):
        return _Res(self.obj)

    async def commit(self):
        self.commits += 1

    async def refresh(self, _o):
        pass

    async def delete(self, o):
        self.deleted.append(o)


def _row(parts, *, type_="covered_call", status="active", id=1, ticker="NOK", entry_net_debit=32.0):
    legs, ep, params = parts
    now = dt.datetime(2026, 10, 5, tzinfo=dt.timezone.utc)
    return SimpleNamespace(
        id=id, user_id=1, trade_status=status, strategy_type=type_, name=ticker, ticker=ticker,
        legs_data=json.dumps(legs), entry_prices=json.dumps(ep), parameters=json.dumps(params),
        result_snapshot="{}", notes=None, order_source="manual", entry_net_debit=entry_net_debit,
        entry_date=now, exit_date=None, exit_prices=None, exit_net=None, created_at=now, updated_at=now)


def _run(coro):
    return asyncio.run(coro)


def _remove(row):
    db = _DB(row)
    return _run(R.remove_stock(strategy_id=row.id, user=SimpleNamespace(id=1), db=db)), db


# ── remove-stock handler ──────────────────────────────────────────────────────────────────────────

def test_remove_stock_keeps_an_ongoing_call_active_with_its_income():
    row = _row(_dram_parts(), ticker="DRAM", entry_net_debit=301.0)
    out, db = _remove(row)
    assert db.deleted == []                                                # nothing is hard-deleted
    assert out.trade_status == "active"                                    # the open call is ongoing
    assert out.parameters["shares"] == 0 and out.parameters["realized_pnl"] == 164
    assert len(out.parameters["closed_legs"]) == 2
    assert out.legs_data == [_call(85)]
    assert out.entry_prices == [{"price": 0.65}]
    assert out.entry_net_debit == 65.0                                      # +$65 credit on the option alone (was 301 incl. stock)
    # no longer holds shares → must NOT keep reading as a covered call in the risk math
    assert out.strategy_type == "options_spread"


def test_remove_stock_with_no_open_call_moves_the_banked_income_to_closed():
    row = _row(_nok_parts())
    out, db = _remove(row)
    assert db.deleted == []
    assert out.trade_status == "closed" and out.exit_net == 20.0 and out.exit_date
    assert out.parameters["realized_pnl"] == 20 and out.parameters["shares"] == 0
    assert out.strategy_type == "covered_call"                              # a closed trade keeps its label
    # …and it is visible in the Closed tab with that income
    assert R._has_shown_realized(row) is True
    assert R._closed_realized(row, json.loads(row.parameters)) == 20.0


def test_remove_stock_keeps_option_income_even_when_the_stock_also_had_a_realized_loss():
    parts = _nok_parts()
    parts[2]["closed_legs"].append(_closed_stock(-300.0))
    parts[2]["realized_pnl"] = -280.0                                       # 20 option income − 300 stock loss
    out, _ = _remove(_row(parts))
    assert out.exit_net == 20.0 and out.parameters["realized_pnl"] == 20.0


def test_remove_stock_refuses_when_there_is_no_option_income_to_keep():
    row = _row(([], [{"ticker": "X", "price": 10}], {"shares": 100, "avg_cost": 10}), type_="stock_long")
    with pytest.raises(HTTPException) as e:
        _remove(row)
    assert e.value.status_code == 400 and "delete" in e.value.detail.lower()


def test_remove_stock_refuses_a_trade_with_no_stock():
    row = _row(([_call(85)], [{"price": 0.65}], {"shares": 0, "realized_pnl": 5, "closed_legs": [_closed_opt(5)]}), type_="options_spread")
    with pytest.raises(HTTPException) as e:
        _remove(row)
    assert e.value.status_code == 400 and "no stock" in e.value.detail.lower()


def test_remove_stock_only_acts_on_active_trades():
    with pytest.raises(HTTPException) as e:
        _run(R.remove_stock(strategy_id=1, user=SimpleNamespace(id=1), db=_DB(None)))
    assert e.value.status_code == 404


# ── closed-ledger delete of a single-part row must not destroy a LIVE trade ───────────────────────

def _delete_part(row, part):
    db = _DB(row)
    out = _run(R.delete_closed_part(strategy_id=row.id, body=R.DeleteClosedPartIn(part=part), user=SimpleNamespace(id=1), db=db))
    return out, db


def test_deleting_the_journal_row_of_an_active_trade_keeps_the_live_position():
    row = _row(_dram_parts(), ticker="DRAM")
    out, db = _delete_part(row, "all")
    assert db.deleted == []                                                # NOT hard-deleted
    assert out.trade_status == "active"
    assert out.parameters["shares"] == 100                                 # shares still held
    assert out.legs_data == [_call(85)]                                    # open call still open
    assert out.parameters.get("realized_pnl") == 0                         # only the banked chunk went
    assert "closed_legs" not in out.parameters or out.parameters["closed_legs"] == []


def test_deleting_an_active_trades_journal_row_keeps_roll_legs_cost_basis():
    legs, ep, params = _dram_parts()
    params["closed_legs"].append(_closed_opt(-30, strike=70, roll=True))
    params["realized_pnl"] = 134
    out, _ = _delete_part(_row((legs, ep, params)), "all")
    assert [l.get("roll") for l in out.parameters["closed_legs"]] == [True]
    assert out.parameters["realized_pnl"] == -30                           # roll cost-basis adjustment survives


def test_part_all_is_refused_on_a_closed_trade():
    row = _row(_nok_parts(), status="closed")
    with pytest.raises(HTTPException) as e:
        _delete_part(row, "all")
    assert e.value.status_code == 400


def test_existing_per_part_deletes_still_work():
    parts = _nok_parts()
    parts[2]["closed_legs"].append(_closed_stock(-5.0))
    parts[2]["realized_pnl"] = 15.0
    row = _row(parts, status="closed")
    out, db = _delete_part(row, "stock")                                   # drop the stock row → option income stays
    assert db.deleted == [] and out.parameters["realized_pnl"] == 20.0
    # …and deleting the LAST part of a fully-closed trade still removes it
    row2 = _row(_nok_parts(), status="closed")
    out2, db2 = _delete_part(row2, "options")
    assert out2 is None and db2.deleted == [row2]                          # nothing left AND fully closed → trade dropped
    with pytest.raises(HTTPException):
        _delete_part(_row(_nok_parts(), status="closed"), "stock")         # no stock legs to delete → 400


# ── stock-ledger sync must not close a trade that still has option legs open ──────────────────────

def _ledger_row(legs):
    row = _row((legs, [{"price": 60}], {"shares": 100, "avg_cost": 60}), type_="stock_long", ticker="LITE")
    row.exit_date = None
    return row


def _txns():
    t0 = dt.datetime(2026, 9, 1, tzinfo=dt.timezone.utc)
    mk = lambda i, a, q, d: SimpleNamespace(id=i, action=a, quantity=q, price=60.0, fees=0.0, executed_at=t0 + dt.timedelta(days=d), created_at=t0)
    return [mk(1, "open", 100, 0), mk(2, "close", -100, 5)]


def _sync(row):
    db = _DB(_txns())
    _run(R._sync_stock_ledger(db, row))
    return row


def test_selling_the_last_share_keeps_a_trade_with_an_open_call_active():
    row = _sync(_ledger_row([_call(85)]))
    assert row.trade_status == "active" and row.exit_date is None          # ongoing option income stays on the book
    assert json.loads(row.parameters)["shares"] == 0


def test_selling_the_last_share_still_closes_a_stock_only_trade():
    row = _sync(_ledger_row([]))
    assert row.trade_status == "closed" and row.exit_date is not None
