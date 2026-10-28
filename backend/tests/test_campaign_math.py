"""All-in option CAMPAIGN math — cost basis / break-even / close-out price after partial closes AND rolls.

The case that exposed it (AAOI, 2026-10-23, trade id 180): an iron condor was opened, its call spread closed
early (partial close), its put spread closed, and the tested short put ROLLED into a new Nov-20 $75 put.

    Oct-02  open:  sell P80  0.59 · buy P70 0.25 · sell C155 2.34 · buy C160 1.85      net credit +$83
    Oct-15  close: buy back C155 @0.12 · sell C160 @0.12                               (partial close)
    Oct-23  close: sell P70 @3.80                                                      (partial close)
    Oct-23  roll:  buy back P80 @12.10  →  sell NEW P75 Nov-20 @11.60                  (roll)

Banked so far: C160 −173, C155 +222, P70 +355 (partials = +404) and P80 −1151 (the roll) = **−747**.
Open: short P75 for a +1,160 credit. ALL-IN cost basis = 1,160 − 747 = **+413** → break-even at expiry 75 − 4.13
= **$70.87**, and buying the put back for ≤ **$4.13** leaves the campaign ahead.

The app used to fold in ONLY the roll-tagged −1151: net credit +9 → break-even $74.91, close-out $0.09 — a
$4 error on the number the user trades off. The History journal showed "Realized +$2.47 · Position Closed".
"""
import asyncio
import datetime as dt
import json
from types import SimpleNamespace

import pytest

import app.routers.saved_strategy_router as R
from app.services.campaign_math import build_campaign_ledger, close_out_breakeven, describe_structure, split_option_realized


# ── replay the real sequence through the REAL handlers ────────────────────────────────────────────

class _Res:
    def __init__(self, o):
        self._o = o

    def scalar_one_or_none(self):
        return self._o


class _DB:
    def __init__(self, row):
        self.row, self.added = row, []

    async def execute(self, *_a, **_k):
        return _Res(self.row)

    def add(self, o):
        self.added.append(o)

    async def commit(self):
        pass

    async def refresh(self, _o):
        pass

    async def delete(self, _o):
        raise AssertionError("a close/roll must never delete the trade")


def _condor(prices):
    """The four legs in the stored order (idx 0..3) with the given per-share entry fills."""
    p80, p70, c155, c160 = prices
    legs = [
        {"action": "sell", "type": "put", "strike": 80, "qty": 1, "expiration": "2026-10-30", "premium": p80},
        {"action": "buy", "type": "put", "strike": 70, "qty": 1, "expiration": "2026-10-30", "premium": p70},
        {"action": "buy", "type": "call", "strike": 160, "qty": 1, "expiration": "2026-10-30", "premium": c160},
        {"action": "sell", "type": "call", "strike": 155, "qty": 1, "expiration": "2026-10-30", "premium": c155},
    ]
    now = dt.datetime(2026, 10, 2, 20, 1, tzinfo=dt.timezone.utc)
    return SimpleNamespace(
        id=180, user_id=1, trade_status="active", strategy_type="options_spread", name="AAOI", ticker="AAOI",
        legs_data=json.dumps(legs),
        entry_prices=json.dumps([{"price": l["premium"], "strike": l["strike"], "type": l["type"]} for l in legs]),
        parameters=json.dumps({"purpose": "income", "contracts": 1}), result_snapshot="{}", notes=None,
        order_source="manual", entry_net_debit=round((p80 + c155 - p70 - c160) * 100, 2),
        entry_date=now, exit_date=None, exit_prices=None, exit_net=None, created_at=now, updated_at=now)


def _close(row, legs, when):
    body = R.ClosePositionIn(legs=[R.ClosePositionLegIn(leg_index=i, exit_price=px) for i, px in legs], executed_at=when)
    return asyncio.run(R.close_position(strategy_id=row.id, body=body, user=SimpleNamespace(id=1), db=_DB(row)))


def _roll(row, out, into, when):
    body = R.RollPositionIn(close_legs=[R.ClosePositionLegIn(leg_index=i, exit_price=px) for i, px in out],
                            open_legs=[R.RollLegIn(**into)], executed_at=when)
    return asyncio.run(R.roll_position(strategy_id=row.id, body=body, user=SimpleNamespace(id=1), db=_DB(row)))


def _replay(entry, call_close, put70_close, put80_buyback, new_put_premium):
    row = _condor(entry)
    _close(row, [(2, call_close[1]), (3, call_close[0])], "2026-10-15T16:10:38+00:00")     # C160 sold, C155 bought back
    _close(row, [(1, put70_close)], "2026-10-23T18:00:00+00:00")                            # P70 sold (idx re-based to 1)
    _roll(row, [(0, put80_buyback)],
          {"action": "sell", "type": "put", "strike": 75, "qty": 1, "expiration": "2026-11-20", "premium": new_put_premium},
          "2026-10-23T20:04:41+00:00")
    return row


@pytest.fixture
def aaoi():
    return _replay(entry=(0.59, 0.25, 2.34, 1.85), call_close=(0.12, 0.12), put70_close=3.80,
                   put80_buyback=12.10, new_put_premium=11.60)


def test_replay_banks_exactly_what_the_app_recorded(aaoi):
    p = json.loads(aaoi.parameters)
    assert p["realized_pnl"] == -747 and p["roll_realized_pnl"] == -1151
    assert sorted(l["realized"] for l in p["closed_legs"]) == [-1151, -173, 222, 355]
    assert [l["leg_index"] for l in p["closed_legs"] if not l.get("roll")] == [2, 3, 1]
    assert aaoi.trade_status == "active" and len(json.loads(aaoi.legs_data)) == 1   # one open leg: short P75


def test_all_in_cost_basis_counts_partial_closes_and_the_roll(aaoi):
    s = R._roll_summary(aaoi)
    assert s["raw_net_credit"] == 1160.0                       # the open P75 on its own
    assert s["roll_realized_pnl"] == -1151.0
    assert s["partial_realized_pnl"] == 404.0                  # −173 + 222 + 355 — used to be ignored
    assert s["campaign_realized_pnl"] == -747.0
    assert s["effective_net_credit"] == 413.0                  # 1160 − 747   (was 9.0)
    assert s["raw_breakevens"] == [63.4]
    assert s["effective_breakevens"] == [70.87]                # 75 − 4.13    (was 74.91)


def test_close_out_price_for_an_overall_profit(aaoi):
    ce = R._roll_summary(aaoi)["close_even"]
    assert ce == {"debit": 413.0, "per_share": 4.13, "qty": 1.0}
    # Prove it: buy the put back at exactly that price → campaign P&L is zero; below it → profit.
    realized = json.loads(aaoi.parameters)["realized_pnl"]
    for buyback, expect in ((4.13, 0.0), (2.00, 213.0), (6.00, -187.0)):
        assert round(1160.0 - buyback * 100 + realized, 2) == expect


def test_the_old_roll_only_basis_was_the_bug(aaoi):
    """Documents the regression: counting only the roll-tagged realized gives the wrong, optimistic-looking basis."""
    s = R._roll_summary(aaoi)
    old_effective = s["raw_net_credit"] + s["roll_realized_pnl"]
    assert old_effective == 9.0 and old_effective != s["effective_net_credit"]
    assert round(75 - old_effective / 100, 2) == 74.91          # what the card used to claim


def test_broker_cash_amounts_reproduce_your_numbers_exactly():
    """Your broker statement (fees included): the same campaign priced to 4 dp reproduces the user's own
    arithmetic — net cash +$407.73, break-even $70.92, close-out $4.08/share."""
    row = _replay(entry=(0.5834, 0.2566, 2.3334, 1.8566), call_close=(0.1201, 0.1139), put70_close=3.7834,
                  put80_buyback=12.0966, new_put_premium=11.5931)
    p = json.loads(row.parameters)
    assert p["realized_pnl"] == -751.58                          # 80.36 − 0.62 − 831.32
    s = R._roll_summary(row)
    assert s["raw_net_credit"] == 1159.31
    assert s["effective_net_credit"] == 407.73
    assert s["effective_breakevens"] == [70.92]
    assert s["close_even"]["per_share"] == pytest.approx(4.0773, abs=1e-4)


def test_the_journal_ledger_reconciles_to_the_cent(aaoi):
    legs = json.loads(aaoi.legs_data)
    p = json.loads(aaoi.parameters)
    led = build_campaign_ledger(legs, p["closed_legs"], p["rolls"], aaoi.entry_date.isoformat())
    assert led["realized"] == -747.0
    assert led["open_cash"] == 1160.0
    assert led["net_cash"] == 413.0                                # open cash + realized
    assert led["rows"][-1]["running_cash"] == 413.0                # the running column ends on the same number
    assert round(sum(r["cash"] for r in led["rows"]), 2) == 413.0
    # 4 closed legs → open+close rows, plus the open put
    assert [r["kind"] for r in led["rows"]].count("open") == 5 and [r["kind"] for r in led["rows"]].count("close") == 4
    # the flat rows follow the order the events tell the story in
    assert led["rows"] == [r for ev in led["events"] for r in ev["rows"]]


def test_ledger_rows_have_signed_cash_and_dates(aaoi):
    legs = json.loads(aaoi.legs_data)
    p = json.loads(aaoi.parameters)
    rows = build_campaign_ledger(legs, p["closed_legs"], p["rolls"], aaoi.entry_date.isoformat())["rows"]
    by = {(r["kind"], r["side"], r["type"], r["strike"]): r for r in rows}
    assert by[("open", "sell", "put", 80.0)]["cash"] == 59.0
    assert by[("open", "buy", "put", 70.0)]["cash"] == -25.0
    assert by[("close", "buy", "put", 80.0)]["cash"] == -1210.0 and by[("close", "buy", "put", 80.0)]["roll"] is True
    assert by[("close", "sell", "put", 70.0)]["cash"] == 380.0
    assert by[("close", "buy", "call", 155.0)]["cash"] == -12.0 and by[("close", "sell", "call", 160.0)]["cash"] == 12.0
    new_put = by[("open", "sell", "put", 75.0)]
    assert new_put["cash"] == 1160.0 and new_put["date"].startswith("2026-10-23")   # dated by the ROLL, not the original entry
    assert by[("open", "sell", "put", 80.0)]["date"].startswith("2026-10-02")


def test_the_campaign_endpoint_returns_ledger_and_summary(aaoi):
    out = asyncio.run(R.get_campaign_ledger(strategy_id=180, user=SimpleNamespace(id=1), db=_DB(aaoi)))
    assert out["ledger"]["net_cash"] == out["summary"]["effective_net_credit"] == 413.0
    assert out["trade_status"] == "active"


# ── partial-close-only (never rolled): the basis must still exist ─────────────────────────────────

def _covered_call(closed_legs, *, shares=100, avg=60.0, premium=0.65, stock_closed=()):
    legs = [{"action": "sell", "type": "call", "strike": 85, "qty": 1, "expiration": "2026-11-20", "premium": premium}]
    params = {"shares": shares, "avg_cost": avg, "realized_pnl": sum(l["realized"] for l in closed_legs) + sum(l["realized"] for l in stock_closed),
              "closed_legs": list(closed_legs) + list(stock_closed)}
    now = dt.datetime(2026, 10, 5, tzinfo=dt.timezone.utc)
    return SimpleNamespace(
        id=93, user_id=1, trade_status="active", strategy_type="covered_call", name="DRAM", ticker="DRAM",
        legs_data=json.dumps(legs), entry_prices=json.dumps([{"price": avg}, {"price": premium}]),
        parameters=json.dumps(params), result_snapshot="{}", notes=None, order_source="manual", entry_net_debit=65.0,
        entry_date=now, exit_date=None, exit_prices=None, exit_net=None, created_at=now, updated_at=now)


def _opt_closed(realized, strike=60, roll=False):
    d = {"leg_index": 0, "action": "sell", "type": "call", "strike": strike, "qty": 1, "entry_price": 1.0,
         "exit_price": 0.5, "realized": realized, "closed_at": "2026-09-14T00:00:00+00:00"}
    if roll:
        d["roll"] = True
    return d


def test_a_covered_call_with_only_partial_closes_still_gets_an_adjusted_cost_basis():
    row = _covered_call([_opt_closed(119), _opt_closed(45, strike=80)])        # DRAM: $164 banked, one open call
    s = R._roll_summary(row)
    assert s is not None and s["has_rolls"] is False and s["count"] == 0       # never rolled — yet a campaign summary exists
    assert s["partial_realized_pnl"] == 164.0 and s["roll_realized_pnl"] == 0.0
    assert s["effective_net_credit"] == 229.0                                  # open call +65 + 164 banked
    assert s["effective_breakevens"] == [57.71]                                # 100 sh @ $60 less $2.29/sh of option income
    assert s["close_even"]["per_share"] == 2.29                                # buy the call back below this → ahead overall


def test_stock_realized_never_shifts_the_option_basis():
    stock = {"leg_index": None, "type": "stock", "qty": 100, "entry_price": 60, "exit_price": 50, "realized": -1000.0,
             "closed_at": "2026-10-01T00:00:00+00:00"}
    with_stock = R._roll_summary(_covered_call([_opt_closed(164)], stock_closed=[stock]))
    without = R._roll_summary(_covered_call([_opt_closed(164)]))
    assert with_stock["campaign_realized_pnl"] == without["campaign_realized_pnl"] == 164.0
    assert with_stock["effective_net_credit"] == without["effective_net_credit"] == 229.0


def test_a_never_touched_trade_has_no_campaign():
    assert R._roll_summary(_covered_call([])) is None


# ── pure pieces ───────────────────────────────────────────────────────────────────────────────────

def test_split_option_realized_buckets():
    legs = [_opt_closed(100), _opt_closed(-30, roll=True), _opt_closed(-20, roll=True),
            {"type": "stock", "realized": -500.0}]
    assert split_option_realized(legs) == {"roll": -50.0, "partial": 100.0, "total": 50.0, "stock": -500.0}


def test_split_option_realized_legacy_scalars_without_closed_legs():
    assert split_option_realized([], roll_scalar=40.0, realized_scalar=100.0) == {"roll": 40.0, "partial": 60.0, "total": 100.0, "stock": 0.0}
    assert split_option_realized([]) == {"roll": 0.0, "partial": 0.0, "total": 0.0, "stock": 0.0}


def test_close_out_when_the_campaign_is_under_water_needs_a_net_credit():
    ce = close_out_breakeven(-250.0, [{"qty": 2}])
    assert ce == {"debit": -250.0, "per_share": -1.25, "qty": 2.0}             # must CLOSE FOR a $250 credit to get even
    assert close_out_breakeven(None, [{"qty": 1}]) is None and close_out_breakeven(100.0, []) is None
    assert close_out_breakeven(300.0, [{"qty": 1}, {"qty": 2}])["per_share"] is None     # mixed sizes → no single per-share price


def test_ledger_handles_a_trade_that_was_rolled_twice():
    closed = [
        {"type": "put", "action": "sell", "strike": 80, "qty": 1, "entry_price": 1.0, "exit_price": 2.0, "realized": -100.0,
         "closed_at": "2026-10-10T00:00:00+00:00", "roll": True, "leg": {"action": "sell", "type": "put", "strike": 80, "expiration": "2026-10-16"}},
        {"type": "put", "action": "sell", "strike": 75, "qty": 1, "entry_price": 2.0, "exit_price": 1.0, "realized": 100.0,
         "closed_at": "2026-10-20T00:00:00+00:00", "roll": True, "leg": {"action": "sell", "type": "put", "strike": 75, "expiration": "2026-10-23"}},
    ]
    rolls = [{"date": "2026-10-10T00:00:00+00:00", "roll_id": "a", "to": ["sell put $75 exp 2026-10-23"]},
             {"date": "2026-10-20T00:00:00+00:00", "roll_id": "b", "to": ["sell put $70 exp 2026-11-20"]}]
    open_legs = [{"action": "sell", "type": "put", "strike": 70, "qty": 1, "expiration": "2026-11-20", "premium": 3.0}]
    led = build_campaign_ledger(open_legs, closed, rolls, "2026-10-01T00:00:00+00:00")
    assert led["realized"] == 0.0 and led["open_cash"] == 300.0 and led["net_cash"] == 300.0
    dates = {(r["kind"], r["strike"]): r["date"][:10] for r in led["rows"]}
    assert dates[("open", 80.0)] == "2026-10-01"       # original entry
    assert dates[("open", 75.0)] == "2026-10-10"       # opened by roll #1
    assert dates[("open", 70.0)] == "2026-10-20"       # opened by roll #2
    assert led["rows"][-1]["running_cash"] == 300.0


# ── the journal as a STORY: named events with every leg labelled ──────────────────────────────────

def _events(row):
    legs = json.loads(row.legs_data)
    p = json.loads(row.parameters)
    return build_campaign_ledger(legs, p["closed_legs"], p["rolls"], row.entry_date.isoformat())["events"]


def test_the_original_opening_is_in_the_journal_all_four_legs(aaoi):
    """The classic ledger used to start at 'Opened: sell put $80' — the 80/70 put spread (and the call side)
    were missing. Every original leg must appear, each labelled."""
    ev = _events(aaoi)[0]
    assert ev["kind"] == "open" and ev["date"].startswith("2026-10-02")
    assert ev["title"] == "Opened iron condor — puts $70/$80 · calls $155/$160"
    assert ev["subtitle"] == "exp 2026-10-30" and ev["legs"] == 4
    got = {(r["side"], r["type"], r["strike"]): (r["price"], r["cash"]) for r in ev["rows"]}
    assert got == {("sell", "put", 80): (0.59, 59.0), ("buy", "put", 70): (0.25, -25.0),
                   ("sell", "call", 155): (2.34, 234.0), ("buy", "call", 160): (1.85, -185.0)}
    assert ev["cash"] == 83.0 and ev["realized"] is None and ev["running_cash"] == 83.0   # the +$83 opening net credit
    assert all(r["kind"] == "open" for r in ev["rows"])


def test_events_are_opened_then_each_close_then_the_roll(aaoi):
    evs = _events(aaoi)
    assert [e["kind"] for e in evs] == ["open", "close", "close", "roll"]
    assert [e["title"] for e in evs] == [
        "Opened iron condor — puts $70/$80 · calls $155/$160",
        "Closed call credit spread $155/$160",          # Oct 15: the call side taken off
        "Closed long put $70",                          # Oct 23: the long put sold
        "Rolled short put $80 → short put $75",
    ]
    assert [e["cash"] for e in evs] == [83.0, 0.0, 380.0, -50.0]
    assert [e["realized"] for e in evs] == [None, 49.0, 355.0, -1151.0]
    assert [e["running_cash"] for e in evs] == [83.0, 83.0, 463.0, 413.0]     # story ends on the all-in net cash
    assert evs[-1]["running_cash"] == 413.0


def test_the_roll_event_holds_the_buyback_and_the_new_leg_together(aaoi):
    roll = _events(aaoi)[-1]
    assert roll["subtitle"] == "2026-10-30 → 2026-11-20"
    assert [(r["kind"], r["side"], r["strike"]) for r in roll["rows"]] == [("close", "buy", 80), ("open", "sell", 75)]
    close, new = roll["rows"]
    assert (close["price"], close["entry_price"], close["cash"], close["realized"]) == (12.1, 0.59, -1210.0, -1151.0)
    assert new["still_open"] is True and new["cash"] == 1160.0
    assert roll["cash"] == -50.0 and roll["realized"] == -1151.0                  # net cash of the roll vs P&L on the buy-back


def test_close_rows_carry_the_entry_price_so_the_pnl_is_traceable(aaoi):
    closes = [r for ev in _events(aaoi) for r in ev["rows"] if r["kind"] == "close"]
    by = {(r["side"], r["type"], r["strike"]): (r["entry_price"], r["price"], r["realized"]) for r in closes}
    assert by[("sell", "call", 160)] == (1.85, 0.12, -173.0)
    assert by[("buy", "call", 155)] == (2.34, 0.12, 222.0)
    assert by[("sell", "put", 70)] == (0.25, 3.8, 355.0)
    assert by[("buy", "put", 80)] == (0.59, 12.1, -1151.0)


def test_a_legacy_roll_leg_without_a_roll_id_still_joins_its_roll_event():
    closed = [{"type": "put", "action": "sell", "strike": 80, "qty": 1, "entry_price": 1.0, "exit_price": 2.0, "realized": -100.0,
               "closed_at": "2026-10-10T00:00:00+00:00", "roll": True,
               "leg": {"action": "sell", "type": "put", "strike": 80, "expiration": "2026-10-16"}}]
    rolls = [{"date": "2026-10-10T00:00:00+00:00", "roll_id": "r1", "to": ["sell put $75 exp 2026-10-23"]}]
    opn = [{"action": "sell", "type": "put", "strike": 75, "qty": 1, "expiration": "2026-10-23", "premium": 3.0}]
    evs = build_campaign_ledger(opn, closed, rolls, "2026-10-01T00:00:00+00:00")["events"]
    assert [e["kind"] for e in evs] == ["open", "roll"]
    assert evs[0]["legs"] == 1 and evs[1]["legs"] == 2                   # the roll event pairs the buy-back with the new leg


@pytest.mark.parametrize("legs,expect", [
    ([("sell", "put", 80)], "short put $80"),
    ([("buy", "call", 160)], "long call $160"),
    ([("sell", "put", 80), ("buy", "put", 70)], "put credit spread $80/$70"),
    ([("buy", "put", 80), ("sell", "put", 70)], "put debit spread $70/$80"),
    ([("sell", "call", 155), ("buy", "call", 160)], "call credit spread $155/$160"),
    ([("buy", "call", 155), ("sell", "call", 160)], "call debit spread $160/$155"),
    ([("sell", "put", 80), ("sell", "call", 155)], "short strangle $80/$155"),
    ([("buy", "put", 80), ("buy", "call", 80)], "long straddle $80"),
    ([("sell", "put", 80), ("buy", "put", 70), ("buy", "call", 160), ("sell", "call", 155)], "iron condor — puts $70/$80 · calls $155/$160"),
    ([("sell", "put", 80), ("buy", "put", 70), ("buy", "call", 90), ("sell", "call", 80)], "iron butterfly — puts $70/$80 · calls $80/$90"),
    ([("buy", "call", 75), ("sell", "put", 75), ("sell", "call", 80), ("buy", "put", 80)], "box spread $75/$80"),
    ([("sell", "put", 80), ("buy", "put", 80), ("buy", "call", 1)], "3-leg options"),
])
def test_describe_structure_names_what_a_trader_would_call_it(legs, expect):
    assert describe_structure([{"action": a, "type": t, "strike": k, "qty": 1} for a, t, k in legs]) == expect


def test_covered_call_journal_names_the_option_side_only():
    row = _covered_call([_opt_closed(119), _opt_closed(45, strike=80)])
    legs, p = json.loads(row.legs_data), json.loads(row.parameters)
    evs = build_campaign_ledger(legs, p["closed_legs"], [], row.entry_date.isoformat())["events"]
    assert evs[-1]["running_cash"] == 229.0 and all(r["type"] == "call" for e in evs for r in e["rows"])
