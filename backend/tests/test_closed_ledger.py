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


# ── Rolls — a rolled trade is ONE continuing campaign, hidden from Closed by default ──

def _roll_leg(realized, *, entry=2.0, exit=3.0, strike=100, action="sell", type="put", qty=1,
              closed_at="2026-09-10T00:00:00+00:00", roll_id="r1", seq=1):
    return {"type": type, "action": action, "qty": qty, "strike": strike,
            "entry_price": entry, "exit_price": exit, "realized": realized,
            "closed_at": closed_at, "roll": True, "roll_id": roll_id, "roll_seq": seq}


def _genuine_leg(realized, *, entry=2.0, exit=0.5, strike=100, action="sell", type="put", qty=1,
                 closed_at="2026-09-11T00:00:00+00:00"):
    return {"type": type, "action": action, "qty": qty, "strike": strike,
            "entry_price": entry, "exit_price": exit, "realized": realized, "closed_at": closed_at}


class TestRollRealized:
    def test_sums_tagged_roll_legs(self):
        params = {"closed_legs": [_roll_leg(-150), _roll_leg(50, roll_id="r2", seq=2)]}
        assert R._roll_realized(params) == -100.0

    def test_falls_back_to_scalar_when_untagged(self):
        assert R._roll_realized({"roll_realized_pnl": -80.0}) == -80.0

    def test_zero_when_no_roll_info(self):
        assert R._roll_realized({"realized_pnl": 300.0}) == 0.0


class TestRollAwareClosedRealized:
    def test_active_excludes_roll_by_default(self):
        s = _mk(1, "active", "2026-09", -100.0, legs=[_roll_leg(-100)])
        assert R._closed_realized(s, R._safe_params(s), include_rolls=False) == 0.0
        assert R._closed_realized(s, R._safe_params(s), include_rolls=True) == -100.0

    def test_active_keeps_genuine_partial_drops_roll(self):
        # realized_pnl 70 = genuine +170 and roll −100
        s = _mk(1, "active", "2026-09", 70.0, legs=[_genuine_leg(170.0), _roll_leg(-100.0)])
        assert R._closed_realized(s, R._safe_params(s), include_rolls=False) == 170.0
        assert R._closed_realized(s, R._safe_params(s), include_rolls=True) == 70.0

    def test_closed_always_full_regardless_of_flag(self):
        s = _mk(1, "closed", "2026-09", 70.0, legs=[_genuine_leg(170.0), _roll_leg(-100.0)])
        assert R._closed_realized(s, R._safe_params(s), include_rolls=False) == 70.0
        assert R._closed_realized(s, R._safe_params(s), include_rolls=True) == 70.0


class TestRollAwareCostProceeds:
    def test_skips_roll_legs_when_excluded(self):
        params = {"closed_legs": [_genuine_leg(150.0, entry=2.0, exit=0.5),
                                  _roll_leg(-100.0, entry=2.0, exit=3.0)]}
        # Genuine short put only: proceeds 200, cost 50.
        assert R._closed_cost_proceeds(params, include_roll_legs=False) == (50.0, 200.0)
        # Both legs: + the roll leg (sell 2.0 → buy 3.0): proceeds +200, cost +300.
        assert R._closed_cost_proceeds(params, include_roll_legs=True) == (350.0, 400.0)


class TestHasShownRealized:
    def test_roll_only_active_hidden_by_default(self):
        s = _mk(1, "active", "2026-09", -100.0, legs=[_roll_leg(-100)])
        assert R._has_shown_realized(s, include_rolls=False) is False
        assert R._has_shown_realized(s, include_rolls=True) is True

    def test_genuine_partial_always_shown(self):
        s = _mk(1, "active", "2026-09", 170.0, legs=[_genuine_leg(170.0)])
        assert R._has_shown_realized(s, include_rolls=False) is True

    def test_mixed_active_shown_because_genuine_present(self):
        s = _mk(1, "active", "2026-09", 70.0, legs=[_genuine_leg(170.0), _roll_leg(-100.0)])
        assert R._has_shown_realized(s, include_rolls=False) is True

    def test_closed_always_shown(self):
        s = _mk(1, "closed", "2026-09", -100.0, legs=[_roll_leg(-100)])
        assert R._has_shown_realized(s, include_rolls=False) is True


class TestRollLedgerIntegration:
    def test_roll_only_active_total_excluded_by_default(self):
        # One genuine closed (+55) and one roll-only active (−100, this month).
        rows = [_mk(1, "closed", "2026-09", 55.0),
                _mk(2, "active", "2026-09", -100.0, legs=[_roll_leg(-100)])]
        comp = R._compute_closed_ledger(rows, NOW, include_rolls=False)
        resp = R._closed_ledger_response(NOW, comp["frozen"], comp["current_rows"],
                                         stored=False, include_rolls=False)
        # The active roll trade contributes 0 by default → total is just the genuine close.
        assert resp["total_realized"] == 55.0

    def test_roll_included_when_opted_in(self):
        rows = [_mk(1, "closed", "2026-09", 55.0),
                _mk(2, "active", "2026-09", -100.0, legs=[_roll_leg(-100)])]
        comp = R._compute_closed_ledger(rows, NOW, include_rolls=True)
        resp = R._closed_ledger_response(NOW, comp["frozen"], comp["current_rows"],
                                         stored=False, include_rolls=True)
        assert resp["total_realized"] == -45.0   # 55 + (−100)


class TestRollSummary:
    """_roll_summary — the cost-basis overlay: effective breakeven folds roll-realized into
    the entry cost, reconciling with structure_breakevens (see test_trade_math)."""

    def _rolled_csp(self):
        params = {"rolls": [{"roll_id": "r1", "seq": 1, "realized": -150.0}],
                  "roll_realized_pnl": -150.0,
                  "closed_legs": [_roll_leg(-150.0, strike=100, entry=2.0, exit=3.5)]}
        now = dt.datetime(2026, 9, 20, tzinfo=dt.timezone.utc)
        return SimpleNamespace(
            id=1, trade_status="active", strategy_type="cash_secured_put",
            parameters=json.dumps(params),
            legs_data=json.dumps([{"type": "put", "action": "sell", "strike": 95, "qty": 1,
                                   "premium": 2.5, "expiration": "2026-12-19"}]),
            entry_prices=json.dumps([{"price": 2.5, "strike": 95, "type": "put"}]),
            result_snapshot="{}", exit_prices=None, name="t1", ticker="XYZ", notes=None,
            order_source="manual", entry_net_debit=None, entry_date=None, exit_date=None,
            exit_net=None, created_at=now, updated_at=now)

    def test_effective_breakeven_absorbs_roll_loss(self):
        roll = R._roll_summary(self._rolled_csp())
        assert roll is not None
        assert roll["count"] == 1
        assert roll["roll_realized_pnl"] == -150.0
        assert roll["raw_net_credit"] == 250.0
        assert roll["effective_net_credit"] == 100.0
        assert roll["raw_breakevens"] == [92.5]        # 95 − 2.50
        assert roll["effective_breakevens"] == [94.0]  # 95 − 1.00 (credit shrunk by the roll loss)

    def test_none_when_never_rolled(self):
        s = _mk(1, "active", "2026-09", 0.0)
        assert R._roll_summary(s) is None
