"""Manage Book hedge plan: tail attribution, budget-sized hedges that must actually cut CVaR, the risk→hedge
exposure map, and the concentration view."""
import asyncio
from types import SimpleNamespace

import numpy as np
import pytest

import app.services.book_tail_risk as bt
from app.services.book_tail_risk import (
    book_mc, reprice_scenario, _tail_attribution, _hedge_plan, _hedge_candidates, _hedge_call_candidates,
    _exposure_map, _concentration_view, _cvar95_of, _covered_shares, _HEDGE_BUDGETS,
)
from app.services.lifecycle_service import higher_order_greeks

R, IDX, VOL = 0.045, 5200.0, 0.18


def _leg(strike, right="C", sign=-1, qty=1, iv=0.40, dte=30):
    return {"strike": strike, "right": right, "sign": sign, "qty": qty, "iv": iv, "dte_years": dte / 365.0, "exp": "2026-11-20", "dte_days": dte}


def _pos(legs, spot=60.0, ticker="XYZ", beta=1.0, shares=0.0, structure="options", capital=6000.0):
    g = higher_order_greeks(legs, spot, r=R, stock_shares=0.0)
    p = {"ticker": ticker, "name": ticker, "trade_id": 1, "spot": spot, "iv": 0.40, "beta": beta, "n_short": 1, "capital": capital,
         "legs": legs, "shares": shares, "has_stock": bool(shares), "covered": False, "structure": structure,
         "strikes_by_exp": {}, "quotes_by_exp": {}, **g}
    p["cov_shares"] = _covered_shares(p)
    return p


def _call_book(n=6, qty=40):    # short OTM calls on several names → an UPSIDE-driven tail (sized so the income can pay for insurance)
    return [_pos([_leg(66 + i, qty=qty)], spot=60.0, ticker=f"C{i}", beta=1.4, capital=(66 + i) * 100.0 * qty) for i in range(n)]


def _put_book(n=6, qty=40):     # short OTM puts → a DOWNSIDE-driven tail
    return [_pos([_leg(54 - i, right="P", qty=qty)], spot=60.0, ticker=f"P{i}", beta=1.4, capital=(54 - i) * 100.0 * qty) for i in range(n)]


def _plan(positions, income=None):
    mc = book_mc(positions, R, VOL, IDX)
    cvar0 = mc["cvar_95"]
    income = (sum(p["net_theta"] for p in positions) * 365.0) if income is None else income
    T, rolls = 90 / 365.0, 365.0 / 90
    book_loss20 = -reprice_scenario(positions, -0.20, 15.0, R)
    items = (_hedge_candidates(IDX, VOL, T, R, [], mc["pnl"], mc["idx_sim"], cvar0, max(book_loss20, 1.0), 1000.0, 0.05,
                               sum(p["capital"] for p in positions), income, rolls, horizon_years=mc["horizon_years"])
             + _hedge_call_candidates(IDX, VOL, T, R, mc["pnl"], mc["idx_sim"], rolls, horizon_years=mc["horizon_years"]))
    tail = _tail_attribution(mc)
    plan = _hedge_plan([dict(c) for c in items], mc=mc, cvar0=cvar0, annual_income=income,
                       book_capital=sum(p["capital"] for p in positions), tail=tail)
    return plan, mc, tail


class TestTailAttribution:
    def test_a_short_call_book_has_a_rally_driven_tail(self):
        _, mc, tail = _plan(_call_book())
        assert tail["side"] == "up" and tail["up_pct"] >= 60
        assert tail["avg_tail_pnl"] < 0

    def test_a_short_put_book_has_a_selloff_driven_tail(self):
        _, mc, tail = _plan(_put_book())
        assert tail["side"] == "down" and tail["down_pct"] >= 60

    def test_the_parts_add_up_to_the_tail_loss(self):
        _, mc, tail = _plan(_call_book())
        parts = tail["delta"] + tail["gamma"] + tail["vega"] + tail["theta"] + tail["other"]
        assert parts == pytest.approx(tail["avg_tail_pnl"], abs=6)            # rounding only
        assert tail["delta"] < 0 and tail["gamma"] < 0 and tail["theta"] > 0   # short calls: directional + convexity losses, theta pays


class TestHedgePlan:
    def test_puts_and_vix_do_not_help_a_rally_tail_but_call_spreads_do(self):
        plan, _, _ = _plan(_call_book())
        by = {c["label"]: c for c in plan["candidates"]}
        puts = [c for c in plan["candidates"] if c["kind"] in ("put", "put spread")]
        calls = [c for c in plan["candidates"] if c["kind"] == "call spread"]
        assert puts and calls
        assert all(not c["helps"] for c in puts)
        assert all("rallies" in (c["reason"] or "") or "costs more" in (c["reason"] or "") for c in puts)
        assert any(c["helps"] for c in calls)
        rec = next(c for c in plan["candidates"] if c["recommended"])
        assert rec["kind"] == "call spread"

    def test_puts_help_a_selloff_tail_and_call_spreads_do_not(self):
        plan, _, _ = _plan(_put_book())
        assert any(c["helps"] for c in plan["candidates"] if c["kind"] in ("put", "put spread"))
        assert all(not c["helps"] for c in plan["candidates"] if c["kind"] == "call spread")
        assert next(c for c in plan["candidates"] if c["recommended"])["kind"] in ("put", "put spread")

    def test_every_plan_stays_inside_its_income_budget_and_bigger_budgets_buy_more(self):
        plan, _, _ = _plan(_call_book())
        inc = plan["annual_income"]
        for c in plan["candidates"]:
            prev = 0
            for pl in c["plans"]:
                if pl["units"] >= 1:
                    assert pl["annual_cost"] <= inc * pl["budget_pct"] / 100.0 + 1.0
                    assert pl["cost_pct_income"] <= pl["budget_pct"] + 0.1
                assert pl["units"] >= prev
                prev = pl["units"]

    def test_cvar_after_is_the_book_cvar_with_the_hedge_on_the_same_sims(self):
        positions = _call_book()
        plan, mc, _ = _plan(positions)
        c = next(x for x in plan["candidates"] if x["recommended"])
        pl = next(p for p in c["plans"] if p["units"] >= 1)
        assert pl["cvar_after"] == pytest.approx(plan["cvar_now"] - pl["cvar_reduction"], abs=2)
        assert pl["cvar_reduction"] > 0 and pl["per_dollar"] > 0

    def test_a_hedge_that_cuts_nothing_is_never_recommended(self):
        plan, _, _ = _plan(_call_book())
        assert all(not c["recommended"] for c in plan["candidates"] if not c["helps"])

    def test_no_income_means_no_insurance_budget(self):
        plan, _, _ = _plan(_call_book(), income=0.0)
        assert plan["income_ok"] is False
        assert all(pl["units"] == 0 for c in plan["candidates"] for pl in c["plans"])
        assert all(c["reason"] == "no income to fund insurance" for c in plan["candidates"])

    def test_the_gap_is_measured_to_the_cvar_guardrail(self):
        plan, _, _ = _plan(_call_book())
        assert plan["gap"] == max(0, plan["cvar_now"] - plan["limit_warn"])
        assert plan["limit_breach"] > plan["limit_warn"] > 0


class TestExposureMap:
    def _rows(self, positions):
        bw = sum(p["net_delta"] * p["beta"] * p["spot"] for p in positions)
        spy = IDX / 10.0
        rows = _exposure_map(positions, R, bw_delta_notional=bw, beta_delta_spy=round(bw / spy), spy_price=spy,
                             net_vega=sum(p["net_vega"] for p in positions), book_capital=sum(p["capital"] for p in positions),
                             mkt_vol=VOL, hedge_dte_days=90)
        return {r["key"]: r for r in rows}, bw

    def test_vega_impact_is_the_exact_reprice_of_a_pure_vol_move(self):
        positions = _call_book()
        rows, _ = self._rows(positions)
        opt = [{**p, "cov_shares": 0.0, "down_stock": False} for p in positions]
        assert rows["vega"]["impact_up"] == round(reprice_scenario(opt, 0.0, 10.0, R), 0)
        assert rows["vega"]["impact_up"] < 0                                   # short vega loses on a vol spike

    def test_delta_impact_is_linear_and_the_delta_hedge_flattens_it(self):
        positions = _call_book()
        rows, bw = self._rows(positions)
        assert rows["delta"]["impact_down"] == round(bw * -0.10, 0) and rows["delta"]["impact_up"] == round(bw * 0.10, 0)
        h = rows["delta"]["hedge"]
        assert h["instrument"] == "SPY shares" and h["action"].startswith("BUY") and h["annual_cost"] == 0.0   # short-delta book → buy

    def test_gamma_is_the_convexity_beyond_the_delta_line(self):
        positions = _call_book()
        rows, bw = self._rows(positions)
        opt = [{**p, "cov_shares": 0.0, "down_stock": False} for p in positions]
        assert rows["gamma"]["impact_up"] == round(reprice_scenario(opt, 0.10, 0.0, R) - bw * 0.10, 0)
        assert rows["gamma"]["impact_up"] < 0                                  # short gamma loses beyond the line
        assert "Fixes" in rows["gamma"]["hedge"]["instrument"]

    def test_a_tiny_vega_is_reported_as_below_one_option(self):
        tiny = [_pos([_leg(66, qty=1, dte=10)], spot=60.0)]
        rows, _ = self._rows(tiny)
        h = rows["vega"]["hedge"]
        assert h is None or h["action"] == "below one SPY option" or h["units"] < 1

    def test_covered_shares_are_not_part_of_the_greek_exposures(self):
        cc = [_pos([_leg(65)], shares=100, structure="covered_call")]
        rows, _ = self._rows(cc)
        naked = [_pos([_leg(65)])]
        rows_n, _ = self._rows(naked)
        assert rows["gamma"]["impact_up"] == rows_n["gamma"]["impact_up"]


class TestConcentrationView:
    def _view(self, positions, clusters=None, sectors=None):
        fe = {"clusters": clusters or [], "sectors": sectors or []}
        return _concentration_view(positions, R, sum(p["capital"] for p in positions), fe)

    def test_shares_of_capital_tail_and_income_each_sum_to_100(self):
        v = self._view(_call_book(4))
        assert sum(n["capital_pct"] for n in v["names"]) == pytest.approx(100, abs=0.6)
        assert sum(n["tail_pct"] for n in v["names"]) == pytest.approx(100, abs=0.6)
        assert sum(n["theta_pct"] for n in v["names"]) == pytest.approx(100, abs=0.6)
        assert v["side"] == "up"

    def test_the_name_with_the_biggest_tail_share_sorts_first_and_payback_is_loss_over_theta(self):
        pos = _call_book(3)
        pos.append(_pos([_leg(61, qty=400)], ticker="BIG", beta=1.8))     # a near-the-money 400-lot
        v = self._view(pos)
        assert v["names"][0]["ticker"] == "BIG"
        n = v["names"][0]
        assert n["payback_days"] == pytest.approx(n["tail_loss"] / n["theta"], rel=0.02)

    def test_a_correlated_cluster_is_one_bet_so_effective_bets_fall(self):
        pos = _call_book(4)
        alone = self._view(pos)
        merged = self._view(pos, clusters=[{"tickers": ["C0", "C1", "C2", "C3"], "capital": 1, "net_directional": 1, "avg_rho": 0.9}])
        assert merged["effective_bets"] < alone["effective_bets"] <= 4.0 + 1e-9
        assert merged["effective_bets"] == pytest.approx(1.0, abs=0.01)

    def test_a_group_shock_reprices_only_that_group_and_the_down_move_helps_short_calls(self):
        pos = _call_book(4)
        v = self._view(pos, clusters=[{"tickers": ["C0", "C1"], "capital": 1, "net_directional": 1, "avg_rho": 0.8}])
        g = next(x for x in v["groups"] if x["kind"] == "cluster")
        members = [{**p, "beta": 1.0} for p in pos if p["ticker"] in ("C0", "C1")]
        assert g["shocks"]["+20"] == round(reprice_scenario(members, 0.20, bt._vol_shock_for(0.20), R), 0)
        assert g["shocks"]["+20"] < 0 and g["shocks"]["-20"] > 0               # short calls: a rally hurts, a selloff pays
        assert g["worst_move"] == "+30" and g["worst"] == min(g["shocks"].values())
        book = v["groups"][0]
        assert book["kind"] == "book" and len(book["tickers"]) == 4 and book["worst"] <= g["worst"]

    def test_covered_calls_do_not_show_a_group_rally_loss(self):
        cc = [_pos([_leg(65)], shares=100, structure="covered_call", ticker="A"), _pos([_leg(65)], shares=100, structure="covered_call", ticker="B")]
        v = self._view(cc, clusters=[{"tickers": ["A", "B"], "capital": 1, "net_directional": 1, "avg_rho": 0.9}])
        g = next(x for x in v["groups"] if x["kind"] == "cluster")
        assert g["shocks"]["+30"] > 0


# ── the whole book: the CVaR guardrail's fix acts on the tail's own side ─────────
class _Provider:
    async def get_underlying_price(self, t):
        return SimpleNamespace(price={"^GSPC": IDX, "^VIX": 18.0}.get(t, 60.0))


@pytest.fixture
def wired(monkeypatch):
    import app.services.derivative_income_service as dis
    import app.services.quote_providers as qp
    holder = {}

    async def fake_pos(strategy, *a, **k): return dict(holder["by_id"][strategy.id])
    async def fake_none(*a, **k): return [], None
    async def fake_factor(*a, **k): return None
    async def fake_sofr(): return (R, None)
    monkeypatch.setattr(bt, "_position_greeks", fake_pos)
    monkeypatch.setattr(bt, "_compute_betas", lambda tickers: {t: 1.4 for t in tickers})
    monkeypatch.setattr(bt, "_index_puts", fake_none)
    monkeypatch.setattr(bt, "_vix_calls", fake_none)
    monkeypatch.setattr(bt, "_vix_levels", lambda: (18.0, 19.0))
    monkeypatch.setattr(bt, "_book_factor_exposure", fake_factor)
    monkeypatch.setattr(dis, "_get_sofr", fake_sofr)
    monkeypatch.setattr(qp, "get_provider", lambda *a, **k: _Provider())
    return holder


def test_the_book_returns_a_plan_and_its_cvar_fix_follows_the_tail_side(wired):
    positions = _call_book(6)
    for i, p in enumerate(positions):
        p["trade_id"] = i + 1
    wired["by_id"] = {p["trade_id"]: p for p in positions}
    out = asyncio.run(bt.compute_book_tail_risk([SimpleNamespace(id=p["trade_id"]) for p in positions], "yfinance", SimpleNamespace(id=1), None))
    import json
    json.dumps(out)                                                                   # the whole payload must be JSON-serialisable (no numpy arrays)
    hp = out["hedge_plan"]
    assert hp and hp["tail"]["side"] == "up" and hp["exposures"] and hp["candidates"]
    assert {r["key"] for r in hp["exposures"]} == {"delta", "vega", "gamma"}
    assert out["concentration_view"]["names"] and out["concentration_view"]["side"] == "up"
    assert not any(k.startswith("_") for c in hp["candidates"] for k in c)            # no private arrays leak into the payload
    assert not any(k.startswith("_") for c in (out["hedge_menu"] or []) for k in c)
    cv = next((c for c in out["risk_scorecard"]["checks"] if c["key"] == "cvar"), None)
    if cv and cv["status"] != "pass":
        assert cv["fix"] is not None
        assert "put spread" not in cv["fix"]["headline"].lower() and "VIX" not in cv["fix"]["headline"]
        for t in cv["fix"].get("targets") or []:
            assert t["stress"]["market_move_pct"] > 0                                  # up-side fixes for an up-side tail


def test_a_rally_driven_book_is_not_told_there_is_no_hedge_when_the_downside_note_fires(wired):
    positions = _call_book(3)
    for i, p in enumerate(positions):
        p["trade_id"] = i + 1
    wired["by_id"] = {p["trade_id"]: p for p in positions}
    out = asyncio.run(bt.compute_book_tail_risk([SimpleNamespace(id=p["trade_id"]) for p in positions], "yfinance", SimpleNamespace(id=1), None))
    assert out["hedge_menu"] == [] and "budget plan" in (out["hedge_note"] or "")
    assert any(c["kind"] == "call spread" and c["helps"] for c in out["hedge_plan"]["candidates"])


# ── the standby VIXY sleeve is a rule, not "the hedge to add" ──────────────────
class TestStandbySleeve:
    def _menu(self):
        mc = book_mc(_put_book(), R, VOL, IDX)
        spx = _hedge_candidates(IDX, VOL, 90 / 365.0, R, [], mc["pnl"], mc["idx_sim"], mc["cvar_95"], 20000.0, 12000.0, 0.05,
                                600000.0, 60000.0, 365.0 / 90, horizon_years=mc["horizon_years"])
        vixy = bt._vixy_dynamic_candidate(18.0, 19.0, IDX, mc["idx_sim"], mc["pnl"], mc["cvar_95"], 20000.0, 12000.0, 0.05, 600000.0, 60000.0)
        bt._strip_private(spx + vixy)
        return spx, vixy

    def test_the_sleeve_is_flagged_standby_and_never_recommended(self):
        spx, vixy = self._menu()
        assert vixy and vixy[0]["standby"] is True
        ranked = bt._rank_hedges(spx + vixy)
        rec = [c for c in ranked if c["recommended"]]
        assert len(rec) == 1 and rec[0]["instrument"] != "VIXY"
        assert next(c for c in ranked if c["instrument"] == "VIXY")["recommended"] is False

    def test_when_only_the_sleeve_exists_nothing_is_recommended(self):
        _, vixy = self._menu()
        assert [c["recommended"] for c in bt._rank_hedges(list(vixy))] == [False]

    def test_the_verdict_never_prints_none_and_says_to_wait_for_the_signal(self):
        spx, vixy = self._menu()
        menu = bt._rank_hedges(spx + vixy)
        scen = [{"label": "−20%", "move_pct": -0.2, "pnl": -9000, "pct_of_capital": -9.0}]
        v = bt._verdict_and_actions(scenarios=scen, short_vol=True, concentration=[], hedge_menu=menu, cvar_pct=3.0, beta_delta_spy=0)
        text = " ".join(v["actions"])
        assert "None" not in text
        assert "backwardation" in text and "Standby" in text and "nothing to buy" in text
        assert "VIXY dynamic sleeve" not in text.split("Standby")[0]          # the "Add the recommended hedge" line is an actionable one


# ── the stress tables must reconcile with each other ─────────────────────────
class TestStressTablesReconcile:
    def _book(self):
        return _call_book(3, qty=5) + _put_book(3, qty=5) + [_pos([_leg(65)], shares=100, structure="covered_call", ticker="CC", beta=1.2)]

    def test_the_by_name_waterfall_sums_to_the_minus_20_tile(self):
        book = self._book()
        tile = reprice_scenario(book, -0.20, 15.0, R)
        by_name = bt._loss_by_name(book, R, -0.20, 15.0)
        assert sum(r["pnl"] for r in by_name) == pytest.approx(tile, abs=0.5 * len(by_name) + 1)      # per-name rounding only

    def test_a_grid_cell_is_the_same_reprice_as_the_tile_at_that_vol(self):
        book = self._book()
        grid = bt._scenario_grid(book, R, 100000.0)
        row = next(r for r in grid["rows"] if r["move_pct"] == -20)
        for vp, cell in zip(grid["vol_shocks"], row["cells"]):
            assert cell["pnl"] == pytest.approx(reprice_scenario(book, -0.20, vp, R), abs=1.0)

    def test_the_minus_20_tile_at_plus_15_vol_sits_between_the_plus_10_and_plus_20_cells(self):
        book = self._book()
        lo, mid, hi = (reprice_scenario(book, -0.20, v, R) for v in (10.0, 15.0, 20.0))
        assert min(lo, hi) <= mid <= max(lo, hi)
