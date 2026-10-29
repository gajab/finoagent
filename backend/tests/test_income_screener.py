"""Income Screener — trade selection (≤4/stock: nearest + farthest per side, A/B only) and expiry gating."""
from datetime import date, timedelta

from app.services.income_screener_service import _pick_expiry, select_trades


def _o(structure, strike, grade, score=70):
    return {"structure": structure, "short_strike": strike, "algo_grade": grade, "desk_score": score}


def test_select_nearest_and_farthest_per_side_ab_only():
    spot = 100.0
    ranked = [
        _o("naked_call", 105, "A"), _o("naked_call", 110, "B"), _o("naked_call", 120, "B"),
        _o("naked_call", 103, "C"),                       # nearer but C → excluded
        _o("cash_secured_put", 95, "B"), _o("cash_secured_put", 85, "A"),
        _o("cash_secured_put", 80, "F"),                  # farther but F → excluded
        _o("iron_condor", 100, "A"),                      # other structures ignored
    ]
    picks = select_trades(ranked, spot, {"A", "B"})
    assert len(picks) == 4
    got = {(p["side"], p["pick"]): p["short_strike"] for p in picks}
    assert got == {("call", "nearest"): 105, ("call", "farthest"): 120,
                   ("put", "nearest"): 95, ("put", "farthest"): 85}


def test_single_qualifier_per_side_gives_one_trade():
    picks = select_trades([_o("naked_call", 110, "A"), _o("cash_secured_put", 90, "D")], 100.0, {"A", "B"})
    assert [(p["side"], p["pick"]) for p in picks] == [("call", "nearest")]


def test_pick_expiry_window_and_earnings():
    today = date.today()
    exps = [(today + timedelta(days=d)).isoformat() for d in (5, 12, 20, 33, 60)]
    (exp, dte), why = _pick_expiry(exps, 10, 40, None)
    assert 10 <= dte <= 40 and why is None
    # earnings in 15 days → only the 12-DTE expiry settles before the print
    (exp, dte), _ = _pick_expiry(exps, 10, 40, today + timedelta(days=15))
    assert dte == 12
    none, why = _pick_expiry(exps, 10, 40, today + timedelta(days=11))
    assert none is None and "earnings" in why
    none, why = _pick_expiry(exps, 70, 90, None)
    assert none is None and "No listed expiry" in why


# ───────────────────────── Earnings plays (sell the print) ─────────────────────────
import asyncio
from datetime import datetime
from types import SimpleNamespace

import pandas as pd

from app.services import income_screener_service as isc
from app.services.income_screener_service import (
    _ET, _cal_slot, _compact, _event_metrics, _pick_event_expiry, _pick_events, _reaction_session,
    _sellable, _window_dates,
)

MON, TUE, WED, THU, FRI, SAT = (date(2026, 10, d) for d in (19, 20, 21, 22, 23, 24))
NEXT_MON, NEXT_TUE = date(2026, 10, 26), date(2026, 10, 27)


def _et(d, hh, mm=0):
    return datetime(d.year, d.month, d.day, hh, mm, tzinfo=_ET)


def test_window_dates_count_trading_sessions_not_calendar_days():
    assert _window_dates("today", MON) == (MON, MON)
    assert _window_dates("tomorrow", MON) == (TUE, TUE)
    assert _window_dates("within_2", MON) == (MON, WED)
    assert _window_dates("within_5", MON) == (MON, NEXT_MON)          # skips the weekend
    # Weekend: no "today after the close"; tomorrow = Monday's session
    assert _window_dates("today", SAT) is None
    assert _window_dates("tomorrow", SAT) == (NEXT_MON, NEXT_MON)
    assert _window_dates("within_2", SAT) == (SAT, NEXT_TUE)
    # NYSE holiday (Thanksgiving Thu 2026-11-26) is skipped
    assert _window_dates("tomorrow", date(2026, 11, 25)) == (date(2026, 11, 27),) * 2


def test_sellable_needs_a_regular_session_before_the_print():
    assert _sellable(MON, "amc", _et(MON, 15, 30))            # tonight's AMC, before the close
    assert not _sellable(MON, "amc", _et(MON, 16, 5))         # after the close — too late
    assert not _sellable(MON, "bmo", _et(MON, 7, 0))          # this morning's BMO — no session before it
    assert _sellable(TUE, "bmo", _et(MON, 15, 59))            # tomorrow's BMO — sell into today's close
    assert not _sellable(TUE, "bmo", _et(MON, 16, 1))
    assert not _sellable(NEXT_MON, "bmo", _et(SAT, 12))       # Monday BMO seen on the weekend: Friday is gone
    assert _sellable(NEXT_MON, "amc", _et(SAT, 12))           # Monday AMC: Monday's session is still ahead
    assert _sellable(MON, None, _et(MON, 15))                 # unknown time → treated as AMC


def test_reaction_session_is_where_the_crush_lands():
    assert _reaction_session(FRI, "amc") == NEXT_MON
    assert _reaction_session(MON, "bmo") == MON
    assert _reaction_session(THU, None) == FRI                # unknown → conservative (next session)
    assert _reaction_session(date(2026, 11, 25), "amc") == date(2026, 11, 27)   # over Thanksgiving


def test_calendar_slot_mapping():
    ts = pd.Timestamp("2026-10-21 20:00", tz="UTC")
    assert _cal_slot("AMC", ts) == "amc" and _cal_slot("BMO", ts) == "bmo"
    assert _cal_slot("TNS", ts) is None                       # time-not-supplied placeholder stays unknown
    assert _cal_slot("TAS", pd.Timestamp("2026-10-21 21:11", tz="UTC")) == "amc"
    assert _cal_slot("TAS", pd.Timestamp("2026-10-21 11:00", tz="UTC")) == "bmo"


def test_pick_events_window_sellability_and_earliest_print():
    u = lambda d, hh, mm=0: pd.Timestamp(datetime(d.year, d.month, d.day, hh, mm), tz="UTC")
    recs = [
        ("AAA", u(MON, 20), "AMC"),        # tonight AMC → in
        ("BBB", u(MON, 12, 30), "BMO"),    # this morning → already out
        ("CCC", u(TUE, 12, 30), "BMO"),    # tomorrow BMO → in, reacts Tue
        ("DDD", u(NEXT_TUE, 20), "AMC"),   # outside the window
        ("EEE", u(WED, 20), "TNS"),        # unknown time → in, conservative reaction (Thu)
        ("CCC", u(THU, 20), "AMC"),        # a later duplicate never stands in for the earlier print
    ]
    ev = _pick_events(recs, MON, NEXT_MON, _et(MON, 14))
    assert set(ev) == {"AAA", "CCC", "EEE"}
    assert ev["AAA"] == {"date": "2026-10-19", "timing": "amc", "reaction": "2026-10-20"}
    assert ev["CCC"] == {"date": "2026-10-20", "timing": "bmo", "reaction": "2026-10-20"}
    assert ev["EEE"]["timing"] is None and ev["EEE"]["reaction"] == "2026-10-22"
    # After the close the same window drops tonight's AMC and tomorrow's BMO — and CCC's later duplicate
    # listing does NOT resurrect it (its real print is the one already out of reach)
    assert set(_pick_events(recs, MON, NEXT_MON, _et(MON, 16, 30))) == {"EEE"}


def test_pick_event_expiry_first_listing_spanning_the_print():
    exps = ["2026-10-20", "2026-10-23", "2026-10-30", "2026-11-20"]
    # Mon AMC / Tue BMO both react at Tue's open → Tue's expiry already spans the print
    assert _pick_event_expiry(exps, TUE, 45, today=MON) == (("2026-10-20", 1), None)
    assert _pick_event_expiry(exps, WED, 45, today=MON) == (("2026-10-23", 4), None)   # Tue AMC → Fri weekly
    none, why = _pick_event_expiry(exps, NEXT_MON, 7, today=MON)
    assert none is None and "beyond the 7-day max" in why
    none, why = _pick_event_expiry(exps, date(2026, 12, 1), 45, today=MON)
    assert none is None and "No listed expiry after the print" in why


def test_compact_move_multiple():
    o = {"structure": "naked_call", "short_strike": 112, "short_strike_pct": 12.0, "earnings_gap_pct": 6.0}
    assert _compact(o, "nearest")["move_multiple"] == 2.0
    assert _compact({**o, "earnings_gap_pct": None}, "nearest")["move_multiple"] is None


def _fake_chain(spot, call_mid, put_mid, iv=0.9):
    ks = [spot - 5, spot, spot + 5]
    def df(mid_atm):
        return pd.DataFrame({"strike": ks, "bid": [0.0, mid_atm - 0.1, 0.0], "ask": [0.0, mid_atm + 0.1, 0.0],
                             "lastPrice": [1.0, mid_atm, 1.0], "impliedVolatility": [iv, iv, iv]})
    return SimpleNamespace(calls=df(call_mid), puts=df(put_mid))


def test_event_metrics_reconcile_with_desk_gap_helper():
    from app.services.derivative_income_service import _earnings_implied_move_pct

    spot, today = 100.0, MON
    chain = _fake_chain(spot, 3.0, 2.8)
    tk = SimpleNamespace(option_chain=lambda exp: chain)
    out = _event_metrics(tk, ["2026-10-23", "2026-11-20"], spot, 40.0, TUE, today, {})
    assert out["event_expiry"] == "2026-10-23" and out["event_dte"] == 4
    assert out["event_iv"] == 90.0
    q = lambda m: SimpleNamespace(mid=m)
    want = _earnings_implied_move_pct({95.0: q(1.0), 100.0: q(3.0), 105.0: q(1.0)},
                                      {95.0: q(1.0), 100.0: q(2.8), 105.0: q(1.0)}, spot, 4, 0.40)
    assert out["event_move_pct"] == round(want * 100, 1)


def test_screen_window_mode_scopes_to_reporting_names(monkeypatch):
    import yfinance as yf

    monkeypatch.setattr(isc, "_now_et", lambda: _et(MON, 14))
    monkeypatch.setattr(isc, "_earnings_calendar_sync", lambda lo, hi, mcap: [
        ("AAA", pd.Timestamp("2026-10-19 20:00", tz="UTC"), "AMC"),
        ("BBB", pd.Timestamp("2026-10-20 12:30", tz="UTC"), "BMO")])
    bodies = []

    def fake_screen(q, **kw):
        bodies.append(q.to_dict())
        return {"total": 2, "quotes": [
            {"symbol": "BBB", "regularMarketPrice": 50.0, "averageDailyVolume3Month": 2e6},
            {"symbol": "AAA", "regularMarketPrice": 80.0, "averageDailyVolume3Month": 5e6,
             "isEarningsDateEstimate": False}]}
    monkeypatch.setattr(yf, "screen", fake_screen)
    out = isc._screen_sync({"earnings_window": "within_2", "exclude_earnings_within_days": 30,
                            "market_cap_min_b": 2, "max_results": 100})
    scope = bodies[0]["operands"][-1]
    assert scope["operator"] == "or" and {o["operands"][1] for o in scope["operands"]} == {"AAA", "BBB"}
    assert [r["ticker"] for r in out["rows"]] == ["AAA", "BBB"]          # most liquid first; exclusion ignored
    assert out["rows"][0]["earnings_timing"] == "amc" and out["rows"][0]["earnings_reaction"] == "2026-10-20"
    assert out["rows"][1]["earnings_timing"] == "bmo" and out["rows"][1]["days_to_earnings"] == 1
    assert out["earnings_window"] == {"window": "within_2", "from": "2026-10-19", "to": "2026-10-21",
                                      "n_reporting": 2}


def test_evaluate_play_mode_trades_the_event_expiry(monkeypatch):
    import app.services.desk_review_service as drs
    import app.services.quote_providers as qp

    monkeypatch.setattr(isc, "_now_et", lambda: _et(MON, 14))
    monkeypatch.setattr(isc, "date", type("D", (date,), {"today": classmethod(lambda cls: MON)}))

    class Prov:
        async def get_option_expirations(self, t):
            return ["2026-10-19", "2026-10-23", "2026-11-20"]
    monkeypatch.setattr(qp, "get_provider", lambda *a, **k: Prov())
    seen = {}

    async def fake_rank(ticker, **kw):
        seen.update(kw)
        return {"spot": 100.0, "ranked": [
            {"structure": "naked_call", "short_strike": 110, "short_strike_pct": 10.0, "algo_grade": "A",
             "desk_score": 80, "earnings_gap_pct": 6.2}]}
    monkeypatch.setattr(drs, "rank_desk", fake_rank)
    p = {"min_prob": 0.85, "min_income": 20, "min_dte": 14, "max_dte": 45, "earnings": "play",
         "earnings_aware": False, "grades": ["A", "B"], "next_earnings": "2026-10-19", "earnings_timing": "amc"}
    res = asyncio.run(isc.evaluate_ticker("XYZ", p))
    assert res["expiration"] == "2026-10-23" and res["dte"] == 4      # ignores min_dte; first after the print
    assert seen["target_expiration"] == "2026-10-23" and seen["earnings_aware"] is True   # forced on
    assert res["event_move_pct"] == 6.2 and res["trades"][0]["move_multiple"] == 1.61
    # Same print after the close: nothing left to sell into
    monkeypatch.setattr(isc, "_now_et", lambda: _et(MON, 16, 30))
    late = asyncio.run(isc.evaluate_ticker("XYZ", p))
    assert late["trades"] == [] and "already out" in late["skipped"]


def test_rank_desk_derives_dte_from_the_expiry_for_every_caller(monkeypatch):
    """The screener used to send no target_dte (→ the 35-day TA rung) while single-ticker sent the expiry's DTE, so
    the SAME 2-day trade was graded on different TA levels (VLO 407.5P scored 55 vs 52). rank_desk now derives it."""
    import app.services.desk_review_service as drs

    seen = []

    async def fake_ctx(ticker, dte=None):
        seen.append(("ctx", dte))
        return {}, {}, {}

    async def fake_scan(ticker, **kw):
        seen.append(("scan", kw.get("target_dte")))
        return {"error": "stop"}

    monkeypatch.setattr(drs, "_desk_context", fake_ctx)
    monkeypatch.setattr(drs, "run_derivative_income", fake_scan)
    monkeypatch.setattr(drs, "_structural_levels", lambda ta, gex: None)
    exp = (date.today() + timedelta(days=2)).isoformat()
    for client_dte in (None, 3, 40):                        # screener (none), a client clock a day off, a wrong hint
        seen.clear()
        asyncio.run(drs.rank_desk("XYZ", target_dte=client_dte, target_expiration=exp))
        assert seen == [("ctx", 2), ("scan", 2)]
