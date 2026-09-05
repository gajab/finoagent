"""Closed-tab monthly rollups — the pure partitioning/aggregation logic behind
GET /trades/closed-ledger.

A prior month is "frozen" (summary-only, stored, not re-shipped) iff it is strictly before
the current month AND no still-open partial close touches it. Everything else — current month,
undated, any prior month a partial banked into — ships as full expandable rows. These tests
lock the partitioning, the cost/proceeds sign conventions, and the reconciling totals without
needing a DB/HTTP harness (matches the pure-function style of the rest of the suite)."""
import datetime as dt
import json
from types import SimpleNamespace

from app.routers import saved_strategy_router as R


def _mk(id, status, month, realized, legs=None, partial_closed_at=None, shares=None, avg=None):
    """A stand-in SavedStrategy row carrying just the fields the ledger helpers read."""
    params = {}
    if realized is not None:
        params["realized_pnl"] = realized
    if legs:
        params["closed_legs"] = list(legs)
    if partial_closed_at:
        params.setdefault("closed_legs", []).append(
            {"type": "call", "action": "sell", "qty": 1, "strike": 100,
             "entry_price": 2.0, "exit_price": 0.5, "closed_at": partial_closed_at})
    if shares is not None:
        params["shares"] = shares
    if avg is not None:
        params["avg_cost"] = avg
    exit_date = (dt.datetime(int(month[:4]), int(month[5:7]), 15, tzinfo=dt.timezone.utc)
                 if status == "closed" and month != "undated" else None)
    now = dt.datetime(2026, 9, 20, tzinfo=dt.timezone.utc)
    return SimpleNamespace(
        id=id, trade_status=status, parameters=json.dumps(params), legs_data="[]",
        result_snapshot="{}", entry_prices=None, exit_prices=None, strategy_type="income",
        name=f"t{id}", ticker="XYZ", notes=None, order_source="manual", entry_net_debit=None,
        entry_date=None, exit_date=exit_date, exit_net=realized, created_at=now, updated_at=now)


NOW = "2026-09"


class TestCloseMonth:
    def test_full_close_buckets_by_exit_date(self):
        s = _mk(1, "closed", "2026-08", 10.0)
        assert R._close_month_of(s, R._safe_params(s)) == "2026-08"

    def test_partial_buckets_by_latest_closed_leg(self):
        s = _mk(1, "active", "2026-07", 10.0, partial_closed_at="2026-07-22T00:00:00+00:00")
        assert R._close_month_of(s, R._safe_params(s)) == "2026-07"

    def test_undated_when_nothing_dates_it(self):
        s = _mk(1, "active", "undated", 10.0)
        s.exit_date = None
        s.updated_at = None
        assert R._close_month_of(s, R._safe_params(s)) is None


class TestCostProceeds:
    def test_short_leg_sells_to_open_buys_to_close(self):
        # sell put entry 2.00 / exit 0.50, 1 contract → proceeds 200, cost 50
        params = {"closed_legs": [{"type": "put", "action": "sell", "qty": 1, "strike": 100,
                                   "entry_price": 2.0, "exit_price": 0.5}]}
        cost, proceeds = R._closed_cost_proceeds(params)
        assert (cost, proceeds) == (50.0, 200.0)

    def test_long_leg_buys_to_open_sells_to_close(self):
        params = {"closed_legs": [{"type": "call", "action": "buy", "qty": 2, "strike": 100,
                                   "entry_price": 1.0, "exit_price": 1.5}]}
        cost, proceeds = R._closed_cost_proceeds(params)
        assert (cost, proceeds) == (200.0, 300.0)

    def test_stock_leg_uses_1x_multiplier(self):
        params = {"closed_legs": [{"type": "stock", "action": "buy", "qty": 100,
                                   "entry_price": 50.0, "exit_price": 55.0}]}
        cost, proceeds = R._closed_cost_proceeds(params)
        assert (cost, proceeds) == (5000.0, 5500.0)

    def test_no_prices_returns_none(self):
        assert R._closed_cost_proceeds({}) == (None, None)


class TestRealized:
    def test_prefers_realized_pnl(self):
        s = _mk(1, "closed", "2026-08", 123.0)
        assert R._closed_realized(s, R._safe_params(s)) == 123.0

    def test_falls_back_to_exit_net(self):
        s = _mk(1, "closed", "2026-08", None)
        s.exit_net = -45.0
        assert R._closed_realized(s, R._safe_params(s)) == -45.0


class TestPartition:
    def _rows(self):
        return [
            # prior fully-closed months → FREEZE
            _mk(1, "closed", "2026-08", 120.0,
                legs=[{"type": "put", "action": "sell", "qty": 1, "strike": 100,
                       "entry_price": 2.0, "exit_price": 0.5}]),
            _mk(2, "closed", "2026-07", -40.0,
                legs=[{"type": "put", "action": "sell", "qty": 1, "strike": 90,
                       "entry_price": 1.0, "exit_price": 1.5}]),
            # current month → full row (not frozen)
            _mk(3, "closed", "2026-09", 55.0),
            # partial that banked into a PRIOR month → makes Aug impure
            _mk(4, "active", "2026-08", 30.0, partial_closed_at="2026-08-20T00:00:00+00:00"),
        ]

    def test_only_pure_prior_months_freeze(self):
        comp = R._compute_closed_ledger(self._rows(), NOW)
        frozen = {g["month"] for g in comp["frozen"]}
        # July is pure-prior → frozen. August has a partial → impure → NOT frozen.
        assert frozen == {"2026-07"}

    def test_impure_and_current_ship_loose(self):
        comp = R._compute_closed_ledger(self._rows(), NOW)
        current_ids = {s.id for s in comp["current_rows"]}
        assert current_ids == {1, 3, 4}          # Aug full-close, Sep, Aug partial
        assert set(comp["loose_ids"]) == {1, 4}  # the two prior-month rows shipped loose (Sep caught by month query)

    def test_frozen_rollup_aggregates(self):
        comp = R._compute_closed_ledger(self._rows(), NOW)
        july = comp["frozen"][0]
        assert july["count"] == 1 and july["scored"] == 1 and july["wins"] == 0
        assert july["realized"] == -40.0        # authoritative realized_pnl, not leg-derived
        assert july["cost"] == 150.0 and july["proceeds"] == 100.0  # sell 1.00→buy-back 1.50

    def test_total_realized_reconciles(self):
        comp = R._compute_closed_ledger(self._rows(), NOW)
        resp = R._closed_ledger_response(NOW, comp["frozen"], comp["current_rows"], stored=False)
        # frozen(-40) + current(120 + 55 + 30) = 165
        assert resp["total_realized"] == 165.0

    def test_returned_trades_carry_close_month_tag(self):
        comp = R._compute_closed_ledger(self._rows(), NOW)
        resp = R._closed_ledger_response(NOW, comp["frozen"], comp["current_rows"], stored=False)
        by_id = {t["id"]: t["close_month"] for t in resp["trades"]}
        assert by_id == {1: "2026-08", 3: "2026-09", 4: "2026-08"}

    def test_current_month_never_freezes_even_when_pure(self):
        rows = [_mk(1, "closed", "2026-09", 10.0)]
        comp = R._compute_closed_ledger(rows, NOW)
        assert comp["frozen"] == []
        assert {s.id for s in comp["current_rows"]} == {1}

    def test_undated_row_ships_loose_not_frozen(self):
        s = _mk(9, "active", "undated", 12.0)
        s.exit_date = None
        s.updated_at = None
        comp = R._compute_closed_ledger([s], NOW)
        assert comp["frozen"] == []
        assert 9 in comp["loose_ids"]

    def test_empty_book(self):
        comp = R._compute_closed_ledger([], NOW)
        assert comp["frozen"] == [] and comp["current_rows"] == [] and comp["loose_ids"] == []
