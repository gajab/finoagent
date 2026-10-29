"""LEAP Protection: chain forward/IV recovery, jump-model pricing + calibration, the simulation repricing the chain,
the evaluator's payoff + tax rules, ranking gates, and an end-to-end compute on a synthetic market."""
import json
import math
from datetime import date

import numpy as np
import pytest

from app.services.leap_protection_service import (
    Book, Env, JumpModel, Leg, Strategy, black, build_candidates, calibrate_jumps, compute, cs_read, diffusion_rho,
    evaluate, forward_vol, how_it_works, implied_vol, jump_price, ledger, outcome_metrics, payoff_shape, scenario_paths,
    simulate, time_grid, _roll, _leg, dividend_rate, dividend_schedule, holder_exercises,
)

R = 0.04
LT, ST = 0.238, 0.408
TAX = {"lt_rate_pct": LT * 100, "st_rate_pct": ST * 100}
STOCK_JM = JumpModel(sigma=0.22, lam=0.3, m=-0.2, s=0.12)
INDEX_JM = JumpModel(sigma=0.12, lam=0.12, m=-0.35, s=0.15)


def _rows(S, T, q, jm, *, lo=0.4, hi=1.8, step=None, half=0.01):
    """Synthetic chain priced by a jump model: OTM-consistent prices on both sides, ±`half` around the mid."""
    F = S * math.exp((R - q) * T)
    step = step or max(0.5, round(S * 0.025, 1))
    rows, k = [], round(S * lo / step) * step
    while k <= S * hi:
        for right in ("C", "P"):
            mid = float(jump_price(F, k, T, R, jm, right))
            if mid < 0.02:
                continue
            rows.append([float(k), right, round(mid * (1 - half), 4), round(mid * (1 + half), 4), 500, 20])
        k = round(k + step, 6)
    return rows


def _book(S=100.0, T=2.0, q=0.01, jm=STOCK_JM, expiry="2028-12-15"):
    return Book(S, T, R, _rows(S, T, q, jm), expiry)


def _env(**kw):
    base = dict(S0=100.0, I0=500.0, B=20.0, T=2.0, r=R, r_b=R + 0.015, q_s=0.0, q_i=0.0, lt=LT, st=ST, idx1256=True,
                lens="defer")
    base.update(kw)
    return Env(**base)


# ---------------------------------------------------------------------------- chain
def test_black_put_call_parity_and_iv_roundtrip():
    F, K, T = 105.0, 90.0, 1.5
    c, p = float(black(F, K, T, R, 0.3, "C")), float(black(F, K, T, R, 0.3, "P"))
    assert c - p == pytest.approx(math.exp(-R * T) * (F - K), abs=1e-9)
    assert implied_vol(p, F, K, T, R, "P") == pytest.approx(0.3, abs=1e-5)
    assert implied_vol(0.0, F, K, T, R, "P") is None


def test_book_recovers_forward_and_smile():
    b = _book(q=0.012)
    assert b.F == pytest.approx(100 * math.exp((R - 0.012) * 2.0), rel=2e-3)
    assert b.q == pytest.approx(0.012, abs=1e-3)
    # the smile is the jump model's: downside IV above upside IV (skew)
    assert b.iv(70.0) > b.atm_iv > b.iv(140.0)
    # IV read back from OTM mids reprices the mid
    K = 80.0
    assert float(black(b.F, K, b.T, R, b.iv(K), "P")) == pytest.approx(b.puts[K].mid, rel=2e-3)


def test_zero_cost_call_and_strike_pickers():
    b = _book()
    kp = b.pick("P", 85.0, "below")
    assert kp <= 85.0
    kc = b.zero_cost_call(b.puts[kp].ask, 103.0)
    assert kc >= 103.0 and b.calls[kc].bid >= b.puts[kp].ask
    nxt = [k for k in b.strikes("C") if k > kc]
    assert not nxt or b.calls[nxt[0]].bid < b.puts[kp].ask          # it is the HIGHEST such strike
    k20 = b.for_delta("C", 0.20)
    assert abs(b.delta(k20, "C") - 0.20) < 0.05


# ---------------------------------------------------------------------------- jump model
def test_jump_price_reduces_to_black_and_keeps_parity():
    F, T = 100.0, 1.0
    flat = JumpModel(sigma=0.25)
    assert float(jump_price(F, 90.0, T, R, flat, "P")) == pytest.approx(float(black(F, 90.0, T, R, 0.25, "P")), rel=1e-12)
    c, p = jump_price(F, 95.0, T, R, STOCK_JM, "C"), jump_price(F, 95.0, T, R, STOCK_JM, "P")
    assert float(c - p) == pytest.approx(math.exp(-R * T) * (F - 95.0), abs=1e-6)
    common = JumpModel(sigma=0.2, lam=0.2, m=-0.1, s=0.1, lam_c=0.1, m_c=-0.3, s_c=0.1, beta_j=1.2)
    c, p = jump_price(F, 110.0, T, R, common, "C"), jump_price(F, 110.0, T, R, common, "P")
    assert float(c - p) == pytest.approx(math.exp(-R * T) * (F - 110.0), abs=1e-6)


def test_calibration_reprices_the_smile():
    b = _book()
    jm = calibrate_jumps(b)
    assert jm is not None and jm.rmse_vol_pts < 0.5
    for K in (70.0, 90.0, 100.0, 120.0):
        right = "P" if K < b.F else "C"
        model = float(jump_price(b.F, K, b.T, R, jm, right))
        assert model == pytest.approx((b.puts if right == "P" else b.calls)[K].mid, rel=0.03)


def test_simulation_reprices_the_chain_under_risk_neutral_drift():
    """The accuracy anchor: run the paths with μ = r (risk-neutral) and the calibrated jumps — Monte-Carlo option values must
    equal the market mids the model was fitted to."""
    q = 0.01
    sb = _book(q=q)
    ib = Book(500.0, 2.0, R, _rows(500.0, 2.0, 0.013, INDEX_JM, step=10.0), "2028-12-15")
    jm_i = calibrate_jumps(ib)
    jm_s = calibrate_jumps(sb, common=jm_i, beta=1.1)
    times = np.array([0.0, 2.0])
    S, I = simulate(S0=100.0, I0=500.0, mu_s=R, mu_i=R, q_s=sb.q, q_i=ib.q, sig_s=jm_s.sigma, sig_i=jm_i.sigma,
                    rho=0.5, times=times, n_paths=200_000, jm_s=jm_s, jm_i=jm_i)
    df = math.exp(-R * 2.0)
    assert float(S[:, -1].mean()) == pytest.approx(sb.F, rel=0.01)
    assert float(I[:, -1].mean()) == pytest.approx(ib.F, rel=0.01)
    for K in (70.0, 85.0, 100.0):
        mc = df * float(np.maximum(K - S[:, -1], 0).mean())
        assert mc == pytest.approx(sb.puts[K].mid, rel=0.06), K
    for K in (400.0, 500.0):
        mc = df * float(np.maximum(K - I[:, -1], 0).mean())
        assert mc == pytest.approx(ib.puts[K].mid, rel=0.06), K


def test_diffusion_rho_hits_the_measured_total_correlation():
    jm_i = INDEX_JM
    jm_s = JumpModel(sigma=0.22, lam=0.2, m=-0.15, s=0.1, lam_c=jm_i.lam, m_c=jm_i.m, s_c=jm_i.s, beta_j=1.0)
    rd = diffusion_rho(0.6, jm_s, jm_i)
    times = np.linspace(0, 2.0, 25)
    S, I = simulate(S0=100, I0=500, mu_s=R, mu_i=R, q_s=0, q_i=0, sig_s=jm_s.sigma, sig_i=jm_i.sigma, rho=rd, times=times,
                    n_paths=60_000, jm_s=jm_s, jm_i=jm_i)
    rs, ri = np.diff(np.log(S), axis=1).ravel(), np.diff(np.log(I), axis=1).ravel()
    assert float(np.corrcoef(rs, ri)[0, 1]) == pytest.approx(0.6, abs=0.03)


def test_forward_vol_preserves_total_variance():
    fv = forward_vol(0.25, 0.15, 0.30, 2.0)
    assert 0.25 ** 2 * 0.15 + fv ** 2 * (2.0 - 0.15) == pytest.approx(0.30 ** 2 * 2.0, rel=1e-9)


# ---------------------------------------------------------------------------- evaluator
def _paths(st_vals, it_vals, T=2.0):
    times = np.array([0.0, T])
    S = np.array([[100.0, v] for v in st_vals])
    I = np.array([[500.0, v] for v in it_vals])
    return S, I, times, np.array([False])


def test_hold_and_sell_now_are_exact():
    env = _env(q_s=0.01, q_i=0.015, dest="index")
    S, I, t, c = _paths([60.0, 100.0, 150.0], [500.0, 500.0, 600.0])
    hold = evaluate(Strategy("hold", "reference", "h"), S, I, t, c, env)
    # no dividend schedule → the yield is paid as cash at the step end, taxed qualified, and sent to the destination
    assert np.allclose(hold["W"], S[:, -1] + 0.01 * 100.0 * 2.0 * (1 - LT))
    assert np.allclose(hold["divs"], 0.01 * 100.0 * 2.0 * (1 - LT))
    assert np.allclose(hold["tax"], 0.0)
    sell = evaluate(Strategy("sell_now", "reference", "s", sell_now=True), S, I, t, c, env)
    units = (100.0 - LT * 80.0) / 500.0
    assert np.allclose(sell["W"], units * I[:, -1] * math.exp(0.015 * (1 - LT) * 2.0))
    assert sell["tax_upfront"] == pytest.approx(LT * 80.0)


def test_collar_payoff_and_straddle_tax():
    """Long 85 put @5 (ask), short 120 call @4 (bid). Below the floor the put gain is short-term TAXED (straddle stops the
    holding period); above the cap the call loss is DEFERRED into basis (no current benefit under the defer lens)."""
    env = _env()
    p = Leg("S", "P", 85.0, 1, 5.0, bid=4.8, ask=5.0, mid=4.9, iv=0.3, delta=-0.3, T_leg=2.0)
    cl = Leg("S", "C", 120.0, -1, 4.0, bid=4.0, ask=4.2, mid=4.1, iv=0.25, delta=0.3, T_leg=2.0)
    st = Strategy("collar", "collars", "c", [p, cl])
    S, I, t, c = _paths([60.0, 100.0, 150.0], [500.0] * 3)
    o = evaluate(st, S, I, t, c, env)
    cash0 = -1.0 * math.exp(env.r_b * 2.0)                 # net debit 1.00, financed at the borrow rate
    # S=60: put pays 25; option P&L = 25 − 5 + 4 = 24 → taxed short-term
    assert o["W"][0] == pytest.approx(60 + cash0 + 25 - ST * 24.0, rel=1e-9)
    # S=100: both expire; P&L = −1 → deferred, no tax
    assert o["W"][1] == pytest.approx(100 + cash0, rel=1e-9)
    assert o["deferred"][1] == pytest.approx(1.0)
    # S=150: buy back the call for 30; P&L = −31 → deferred into basis
    assert o["W"][2] == pytest.approx(150 + cash0 - 30, rel=1e-9)
    assert o["deferred"][2] == pytest.approx(31.0)
    # liquidate lens: the deferred loss comes back as basis
    o2 = evaluate(st, S, I, t, c, _env(lens="liquidate"))
    assert o2["W"][2] == pytest.approx(150 + cash0 - 30 - LT * (150 - (20 + 31)), rel=1e-9)


def test_qualified_covered_call_loss_is_recognised_and_index_is_1256():
    env = _env()
    cc = Leg("S", "C", 120.0, -1, 4.0, bid=4.0, ask=4.2, mid=4.1, iv=0.25, delta=0.3, T_leg=2.0)
    S, I, t, c = _paths([150.0], [500.0])
    o = evaluate(Strategy("cc", "income", "cc", [cc]), S, I, t, c, env)
    assert o["tax"][0] == pytest.approx(ST * (4.0 - 30.0))          # OTM covered call, no put → QCC → loss recognised
    assert o["deferred"][0] == 0.0
    ip = Leg("I", "P", 450.0, 0.2, 20.0, bid=19.5, ask=20.0, mid=19.75, iv=0.2, delta=-0.3, T_leg=2.0)
    S, I, t, c = _paths([100.0, 100.0], [350.0, 600.0])
    o = evaluate(Strategy("ip", "index", "ip", [ip]), S, I, t, c, env)
    b1256 = 0.6 * LT + 0.4 * ST
    assert o["tax"][0] == pytest.approx(b1256 * 0.2 * (100.0 - 20.0))
    assert o["tax"][1] == pytest.approx(b1256 * 0.2 * (0.0 - 20.0))  # §1256 loss recognised (negative tax)


def test_deep_itm_call_is_a_straddle_and_flagged():
    env = _env(dest="index")
    deep = Leg("S", "C", 60.0, -1, 42.0, bid=42.0, ask=42.5, mid=42.25, iv=0.3, delta=0.93, T_leg=2.0)
    st = Strategy("ditm", "diversify", "d", [deep])
    S, I, t, c = _paths([150.0], [500.0])
    o = evaluate(st, S, I, t, c, env)
    assert o["units"] == pytest.approx(42.0 / 500.0)
    assert o["deferred"][0] == pytest.approx(90.0 - 42.0)         # ITM call → not QCC → loss deferred
    assert cs_read(st, env)["risk"] == "high"


def test_fixed_strike_calendar_is_not_resold_in_the_money():
    xb = _book(T=45 / 365)
    ro = _roll(xb, "P", -1, atm_fwd=xb.atm_iv, K_abs=90.0)
    Sj = np.array([100.0, 85.0])
    assert list(ro.active(Sj)) == [1.0, 0.0]
    assert float(ro.premium(Sj, 0)[0]) > 0


def test_rolling_cycles_receive_premium_and_pay_intrinsic():
    xb = _book(T=45 / 365)
    ro = _roll(xb, "C", -1, atm_fwd=xb.atm_iv, delta=0.2)
    times, cycle = time_grid(2.0, xb.T)
    full = cycle >= 0
    assert full[:-1].all() and len(times) >= 16 and (cycle[full] == np.arange(len(cycle))[full] + 1).all()
    flat = np.full((1, len(times)), 100.0)
    st = Strategy("roll", "calendar", "r", [], [ro])
    o = evaluate(st, flat, np.full((1, len(times)), 500.0), times, cycle, _env())
    prem = float(ro.premium(np.array([100.0]), 0)[0])
    kept = prem * full.sum() * (1 - ST)                            # every cycle's premium kept, short-term tax paid
    assert 100.0 + kept < o["W"][0] < 100.0 + kept + 1.0              # … plus a little interest on the cash
    assert o["tax"][0] == pytest.approx(ST * prem * full.sum(), rel=0.05)


# ---------------------------------------------------------------------------- metrics + end-to-end
def test_outcome_metrics_basic():
    W = np.array([50.0, 100.0, 110.0, 120.0] * 25)
    m = outcome_metrics(W, np.linspace(400, 600, 100), 100.0, 1.0)
    assert m["p_loss"] == pytest.approx(0.25)
    assert m["cvar"] == pytest.approx(-0.5)
    assert m["mean_ann"] == pytest.approx(-0.05)


def _snapshot():
    T_days, short_days = 730, 45
    return {
        "ticker": "TEST", "index": "SPY", "spot": 100.0, "index_spot": 500.0,
        "expiries": [{"expiry": "2028-10-20", "dte": T_days}],
        "leap": {"expiry": "2028-10-20", "dte": T_days, "rows": _rows(100.0, T_days / 365, 0.01, STOCK_JM)},
        "short": {"expiry": "2026-12-18", "dte": short_days, "rows": _rows(100.0, short_days / 365, 0.01, STOCK_JM, lo=0.6, hi=1.4, step=1.0)},
        "index_leap": {"expiry": "2028-10-20", "dte": T_days, "rows": _rows(500.0, T_days / 365, 0.013, INDEX_JM, step=10.0)},
        "hist": {"hv_s": 0.30, "hv_i": 0.17, "rho": 0.6, "n_obs": 500, "div_s": 0.01, "div_i": 0.013},
    }


def _run(**kw):
    args = dict(shares=1000, cost_basis=20.0, tax=TAX, tax_lens="defer", erp_pct=5.0, alpha_pct=0.0, vol_view="implied",
                objective="cagr", max_loss_pct=25.0, max_single_stock_pct=50.0, borrow_spread_pct=1.5, loan_ltv_pct=90.0,
                allow_constructive_sale=False, r=R, r_label="test", today=date(2026, 10, 26), n_paths=6000)
    args.update(kw)
    return compute(_snapshot(), **args)


def test_compute_end_to_end_is_complete_and_json_safe():
    out = _run()
    assert out["success"]
    json.dumps(out)
    ids = {x["id"] for x in out["strategies"]}
    assert {"hold", "sell_now", "collar_15", "put_taxadj", "diag_10", "put_cal", "idx_put_10", "idx_short_fut",
            "collar_loan_15", "ditm_70", "ditm_pair", "synthetic_sale", "long_straddle", "put_fly"} <= ids
    assert len(out["strategies"]) >= 35
    best = next(x for x in out["strategies"] if x["id"] == out["best_id"])
    assert best["family"] != "reference" and best["recommended"]
    if out["best_note"] is None:
        assert best["eligible"] and best["cvar5_pct"] >= -25.0 and best["single_stock_pct"] <= 50.0
    # ranked by the objective
    cagrs = [x["cagr_pct"] for x in out["strategies"]]
    assert cagrs == sorted(cagrs, reverse=True)
    syn = next(x for x in out["strategies"] if x["id"] == "synthetic_sale")
    assert syn["constructive_sale"]["risk"] == "high" and not syn["eligible"]
    assert {s["id"] for s in out["scenarios"]} >= {"market_crash", "stock_crash", "flat"}
    for sc in out["scenarios"]:
        assert sc["move"] in out["grid"]["moves"] and sc["mode"] in ("market", "stock_only")
    assert out["market"]["jumps"]["stock_fit_vol_pts"] < 1.0


def _at(row, mode, out, move):
    return sum(row["payout"][mode]["values"][out["grid"]["moves"].index(move)])


def test_flat_point_reconciles_with_hold_and_lens_base():
    out = _run()
    hold = next(x for x in out["strategies"] if x["id"] == "hold")
    T = 730 / 365
    # no ex-dividend history → the 1% yield paid quarterly from 1.5 months; after tax it buys SPY (flat here)
    ex = np.arange(0.125, T, 0.25)
    divs = sum(0.25 * (1 - LT) * math.exp(0.013 * (1 - LT) * (T - t)) for t in ex)
    assert _at(hold, "market", out, 0) == pytest.approx(1000 * (100 + divs), abs=2)
    assert hold["dividends"]["payments"] == 8 and hold["dividends"]["qualified"]
    liq = _run(tax_lens="liquidate")
    assert liq["starting_wealth"] == pytest.approx(1000 * (100 - LT * 80), abs=1)
    sell = next(x for x in liq["strategies"] if x["id"] == "sell_now")
    assert _at(sell, "market", liq, 0) == pytest.approx(liq["starting_wealth"] * math.exp(0.013 * (1 - LT) * T), abs=3)


def test_allowing_constructive_sale_changes_eligibility_only():
    a = _run()
    b = _run(allow_constructive_sale=True)
    sa = next(x for x in a["strategies"] if x["id"] == "synthetic_sale")
    sb = next(x for x in b["strategies"] if x["id"] == "synthetic_sale")
    assert sa["cagr_pct"] == sb["cagr_pct"]
    assert "could be taxed as a sale (§1259)" in sa["reasons"] and "could be taxed as a sale (§1259)" not in sb["reasons"]


def test_realized_view_runs_without_jumps():
    out = _run(vol_view="realized")
    assert out["market"]["jumps"] is None and out["market"]["sigma_used_pct"] == 30.0


def test_scenario_paths_hit_the_targets():
    times = np.array([0.0, 0.5, 2.0])
    S, I = scenario_paths(100.0, 500.0, [(-40.0, 0.0), (60.0, 10.0)], times)
    assert S[0, -1] == pytest.approx(60.0) and I[1, -1] == pytest.approx(550.0) and S[1, 0] == pytest.approx(100.0)


def test_candidates_need_no_index_book():
    env = _env(T=2.0)
    sb = _book()
    cands = build_candidates(env, sb, None, None, beta=1.0, ltv=90.0, index="SPY")
    fams = {c.family for c in cands}
    assert "index" not in fams and "calendar" not in fams and {"puts", "collars", "income"} <= fams


# ---------------------------------------------------------------------------- proceeds destination + payout ledger
def _ledger_sum(o):
    legs = np.sum(o["legs"], axis=0) if o["legs"] else 0.0
    return (o["stock_val"] + o["divs"] + legs + o["rolls"] + o["cash_other"] + o["idx_val"] + o["bond_val"] - o["debt"]
            - o["tax"] - o["liq_tax"])


@pytest.mark.parametrize("dest", ["index", "bond", "cash"])
@pytest.mark.parametrize("lens", ["defer", "liquidate"])
def test_ledger_sums_exactly_to_the_outcome(dest, lens):
    """Every line shown at expiry comes from evaluate and they add up to the after-tax value — with legs, rolls, a loan."""
    env = _env(dest=dest, bond_y=0.05, lens=lens, q_s=0.01, q_i=0.012)
    xb = _book(T=45 / 365)
    p = Leg("S", "P", 85.0, 1, 5.0, bid=4.8, ask=5.0, mid=4.9, iv=0.3, delta=-0.3, T_leg=2.0)
    c = Leg("S", "C", 125.0, -1, 7.0, bid=7.0, ask=7.2, mid=7.1, iv=0.25, delta=0.3, T_leg=2.0)
    ro = _roll(xb, "C", -1, atm_fwd=xb.atm_iv, delta=0.2)
    st = Strategy("x", "collars", "x", [p, c], [ro], loan_ps=60.0)
    times, cycle = time_grid(2.0, xb.T)
    S, I = scenario_paths(100.0, 500.0, [(-40.0, -30.0), (0.0, 0.0), (50.0, 20.0)], times)
    o = evaluate(st, S, I, times, cycle, env)
    assert np.allclose(_ledger_sum(o), o["W"], atol=1e-9)
    led = ledger(st, o, env, shares=1000, ticker="TEST", index="SPY")
    for row, w in zip(led["values"], o["W"]):
        assert sum(row) == pytest.approx(w * 1000, abs=len(row))      # dollars, rounded per line


def test_bond_and_cash_destinations_are_after_tax():
    cc = Leg("S", "C", 130.0, -1, 6.0, bid=6.0, ask=6.2, mid=6.1, iv=0.25, delta=0.25, T_leg=2.0)
    st = Strategy("cc", "income", "cc", [cc])
    S, I, t, c = _paths([100.0], [500.0])
    ob = evaluate(st, S, I, t, c, _env(dest="bond", bond_y=0.05))
    assert ob["bond_val"] == pytest.approx(6.0 * (1 + 0.05 * (1 - ST)) ** 2.0)
    assert ob["cash_other"][0] == pytest.approx(0.0)
    oc = evaluate(st, S, I, t, c, _env(dest="cash"))
    assert oc["cash_other"][0] == pytest.approx(6.0 * math.exp(R * (1 - ST) * 2.0))      # T-bill interest taxed as income
    oi = evaluate(st, S, I, t, c, _env(dest="index"))
    assert oi["units"] == pytest.approx(6.0 / 500.0) and oi["idx_val"][0] == pytest.approx(6.0)
    keep = evaluate(Strategy("k", "diversify", "k", [cc], keep_cash=True), S, I, t, c, _env(dest="index"))
    assert keep["units"] == 0.0 and keep["cash_other"][0] > 6.0


def test_payoff_shape_reads_floor_and_cap():
    env = _env()
    p = Leg("S", "P", 85.0, 1.69, 5.0, bid=4.8, ask=5.0, mid=4.9, iv=0.3, delta=-0.3, T_leg=2.0)
    c = Leg("S", "C", 120.0, -1, 8.4, bid=8.4, ask=8.6, mid=8.5, iv=0.25, delta=0.3, T_leg=2.0)
    collar = Strategy("c", "collars", "c", [p, c])
    times = np.array([0.0, 2.0])
    moves = list(range(-60, 85, 5))
    Sm, Im = scenario_paths(100.0, 500.0, [(m, m) for m in moves], times)
    Ss, Is = scenario_paths(100.0, 500.0, [(m, 0) for m in moves], times)
    W = lambda st, S_, I_: evaluate(st, S_, I_, times, np.array([False]), env)["W"] * 1000
    sh = payoff_shape(W(collar, Sm, Im), W(collar, Ss, Is), 100_000, collar, env)
    assert sh["floored"] and sh["capped"] and sh["protect_below"] == 85.0 and sh["cap_above"] == 120.0
    hold = Strategy("hold", "reference", "h")
    sh = payoff_shape(W(hold, Sm, Im), W(hold, Ss, Is), 100_000, hold, env)
    assert not sh["floored"] and not sh["capped"] and sh["worst_pct"] == pytest.approx(-60, abs=0.5)


def test_how_it_works_speaks_in_contracts_and_dollars():
    env = _env(dest="bond", bond_y=0.045)
    p = Leg("S", "P", 85.0, 1, 5.0, bid=4.8, ask=5.0, mid=4.9, iv=0.3, delta=-0.3, T_leg=2.0, expiry="2028-12-15")
    c = Leg("S", "C", 120.0, -1, 6.0, bid=6.0, ask=6.2, mid=6.1, iv=0.25, delta=0.3, T_leg=2.0, expiry="2028-12-15")
    lines = how_it_works(Strategy("c", "collars", "c", [p, c]), env, shares=1000, ticker="TEST", index="SPY")
    assert lines[0].startswith("Buy 10 × $85 puts (Dec 2028) for $5,000")
    assert lines[1].startswith("Sell 10 × $120 calls (Dec 2028) for $6,000")
    assert lines[-1] == "Net: you collect $1,000 today → a 4.50% bond."


def test_destination_changes_sell_now_and_names():
    a, b = _run(proceeds_to="index"), _run(proceeds_to="bond", bond_rate_pct=6.0)
    sa = next(x for x in a["strategies"] if x["id"] == "sell_now")
    sb = next(x for x in b["strategies"] if x["id"] == "sell_now")
    assert sa["name"].endswith("SPY") and sb["name"].endswith("6.00% bond")
    assert b["destination"] == {"kind": "bond", "label": "a 6.00% bond", "rate_pct": 6.0}
    la = _run(proceeds_to="index", tax_lens="liquidate")
    lb = _run(proceeds_to="bond", bond_rate_pct=6.0, tax_lens="liquidate")
    pa = next(x for x in la["strategies"] if x["id"] == "sell_now")["p_loss_pct"]
    pb = next(x for x in lb["strategies"] if x["id"] == "sell_now")["p_loss_pct"]
    assert pb == 0.0 < pa                                            # measured from after-tax value, a bond can't lose
    ids_b = {x["id"] for x in b["strategies"]}
    assert "ditm_protected" not in ids_b and "collar_loan_protected" not in ids_b


# ---------------------------------------------------------------------------- best/worst per year, ATM collar, own view
def test_best_and_worst_case_per_year_and_own_view():
    out = _run()
    T = 730 / 365
    by = {x["id"]: x for x in out["strategies"]}
    assert "collar_0" in by and "ATM" in by["collar_0"]["name"]
    for x in out["strategies"]:
        sh = x["shape"]
        W0N = out["starting_wealth"]
        assert sh["best_ann_pct"] == pytest.approx(((sh["best_value"] / W0N) ** (1 / T) - 1) * 100, abs=0.06)
        assert sh["worst_ann_pct"] <= sh["best_ann_pct"]
        if sh["capped"]:
            assert sh["cap_ann_pct"] is not None
        if sh["cap_above"] is not None:                              # odds only where an OTM call strike sets the cap
            assert sh["capped"] and 0 <= sh["p_cap_pct"] <= 100
    # a 1-for-1 collar floors the PRE-tax value; under 'never sell' the taxed put payout makes the after-tax value slide
    c = by["collar_15"]["shape"]
    assert c["pretax_floored"] and not c["floored"] and c["protect_below"] is not None
    t = by["collar_taxadj"]["shape"]
    assert t["floored"] and t["p_floor_pct"] is not None
    # your own return view replaces CAPM: higher drift → more paths reach the cap, same cap
    hi = _run(stock_return_pct=20.0)
    assert hi["market"]["stock_drift_pct"] == pytest.approx(20.0) and hi["market"]["capm_stock_pct"] == out["market"]["capm_stock_pct"]
    h = {x["id"]: x for x in hi["strategies"]}
    assert h["collar_15"]["shape"]["cap_value"] == by["collar_15"]["shape"]["cap_value"]
    assert h["collar_15"]["shape"]["p_cap_pct"] > by["collar_15"]["shape"]["p_cap_pct"]
    assert h["hold"]["cagr_pct"] > by["hold"]["cagr_pct"]


def test_premium_routing_growth_and_margin_note():
    """Collected premium follows the user's choice (bond / T-bills) and its growth is reported and counted; only the INDEX
    choice keeps a keep_cash trade's premium in T-bills (it already holds the index). Short index options carry a margin note."""
    from app.services.leap_protection_service import premium_block, margin_warnings
    c = Leg("S", "C", 60.0, -1, 42.0, bid=42.0, ask=42.5, mid=42.25, iv=0.3, delta=0.93, T_leg=2.0)
    ip = Leg("I", "P", 650.0, -0.2, 160.0, bid=160.0, ask=162.0, mid=161.0, iv=0.15, delta=-0.8, T_leg=2.0)
    st = Strategy("pair", "diversify", "p", [c, ip], keep_cash=True)
    S, I, t, cyc = _paths([100.0], [500.0])
    credit = 42.0 + 0.2 * 160.0
    ob = evaluate(st, S, I, t, cyc, _env(dest="bond", bond_y=0.05))
    assert ob["sleeve0"] == pytest.approx(credit) and ob["bond_val"] == pytest.approx(credit * (1 + 0.05 * (1 - ST)) ** 2.0)
    mb = premium_block(st, _env(dest="bond", bond_y=0.05), ob, shares=1000, index="SPY")
    assert mb["today_total"] == pytest.approx(credit * 1000) and mb["at_expiry_total"] == pytest.approx(ob["bond_val"] * 1000, abs=1)
    oi = evaluate(st, S, I, t, cyc, _env(dest="index"))
    assert oi["units"] == 0.0 and oi["cash_other"][0] > credit                     # kept in T-bills, earning interest
    mi = premium_block(st, _env(dest="index"), oi, shares=1000, index="SPY")
    assert mi["kept_as_collateral"] and mi["where"] == "T-bills (collateral)"
    w = margin_warnings(st, _env(), shares=1000, index="SPY")
    per = max(160.0 + 0.15 * 500.0, 160.0 + 0.10 * 650.0)
    assert len(w) == 1 and f"${per * 0.2 * 1000:,.0f}" in w[0]
    assert premium_block(Strategy("hold", "reference", "h"), _env(), oi, shares=1000, index="SPY") is None


# ---------------------------------------------------------------------------- early assignment, index twin, dividends
def test_index_legs_trade_at_the_european_twin_not_the_american_etf_quote():
    ib = Book(500.0, 2.0, R, _rows(500.0, 2.0, 0.013, INDEX_JM, step=10.0), "2028-12-15")
    K = 650.0
    q = ib.puts[K]
    q.bid, q.ask = q.bid + 40.0, q.ask + 40.0                       # an American ETF quote: early-exercise right priced in
    american = _leg(ib, "I", "P", K, -1)
    assert american.px == pytest.approx(q.bid)
    ib.european = True
    twin = _leg(ib, "I", "P", K, -1)
    euro = float(black(ib.F, K, ib.T, R, ib.iv(K), "P"))
    assert twin.european and twin.px == pytest.approx(euro * (1 - twin.half_spread))
    assert twin.px < q.bid - 30                                      # the deep-ITM premium is NOT credited
    otm = _leg(ib, "I", "P", 450.0, 1)                               # out of the money the twin and the quote agree
    assert otm.px == pytest.approx(ib.puts[450.0].ask, rel=0.02)


def test_holder_exercise_rules():
    U = np.array([100.0])
    # call: only when the dividend beats the interest on the strike (+ put value)
    assert holder_exercises("C", U, 60.0, 0.5, R, 0.3, 0.0, 5.0)[0]
    assert not holder_exercises("C", U, 60.0, 0.5, R, 0.3, 0.0, 0.2)[0]
    assert not holder_exercises("C", U, 60.0, 0.5, R, 0.3, 0.0, 0.0)[0]
    assert not holder_exercises("C", np.array([55.0]), 60.0, 0.5, R, 0.3, 0.0, 5.0)[0]        # out of the money
    # put: deep in the money with time left → the interest on the strike wins; near the money it doesn't
    assert holder_exercises("P", np.array([50.0]), 100.0, 1.5, R, 0.3, 0.0, 0.0)[0]
    assert not holder_exercises("P", np.array([95.0]), 100.0, 1.5, R, 0.3, 0.0, 0.0)[0]


def _ex_paths(path, times):
    S = np.array([path])
    return S, np.full_like(S, 500.0)


def test_short_call_is_closed_before_a_big_dividend_and_the_ledger_still_sums():
    env = _env(div_t=(1.5,), div_amt=(5.0,), q_s=0.05, dest="cash")
    times, cycle = time_grid(2.0, 0.0, extra=env.div_t)
    assert list(times) == [0.0, 1.5, 2.0]
    S, I = _ex_paths([100.0, 120.0, 130.0], times)
    c = Leg("S", "C", 60.0, -1, 42.0, bid=42.0, ask=42.5, mid=42.2, iv=0.3, delta=0.95, T_leg=2.0)
    st = Strategy("d", "diversify", "d", [c])
    o = evaluate(st, S, I, times, cycle, env)
    assert o["early"][0][0] and o["early_t"][0][0] == pytest.approx(1.5)
    assert o["legs"][0][0] == pytest.approx(-(120.0 - 60.0))        # bought back at intrinsic before the ex-date
    assert o["divs"][0] > 0                                          # … so the shares (and the dividend) are kept
    led = ledger(st, o, env, shares=1000, ticker="TEST", index="SPY")
    assert sum(led["values"][0]) == pytest.approx(o["W"][0] * 1000, abs=len(led["values"][0]))
    leg_line = next(l for l in led["lines"] if l["kind"] == "leg")
    assert leg_line["early"] == [1]
    small = evaluate(st, S, I, times, cycle, _env(div_t=(1.5,), div_amt=(0.25,), q_s=0.01, dest="cash"))
    assert not small["early"][0][0] and small["legs"][0][0] == pytest.approx(-(130.0 - 60.0))


def test_deep_itm_short_put_is_assigned_early():
    times = np.array([0.0, 0.5, 2.0])
    S, I = _ex_paths([100.0, 50.0, 45.0], times)
    p = Leg("S", "P", 100.0, -1, 12.0, bid=12.0, ask=12.4, mid=12.2, iv=0.3, delta=-0.4, T_leg=2.0)
    o = evaluate(Strategy("sp", "income", "sp", [p]), S, I, times, np.array([-1, -1]), _env())
    assert o["early"][0][0] and o["legs"][0][0] == pytest.approx(-50.0)


def test_dividend_tax_status_follows_the_hedge():
    env = _env()
    put = Leg("S", "P", 85.0, 1, 5.0, iv=0.3, T_leg=2.0)
    otm_call = Leg("S", "C", 125.0, -1, 7.0, iv=0.25, T_leg=2.0)
    itm_call = Leg("S", "C", 80.0, -1, 24.0, iv=0.25, T_leg=2.0)
    assert dividend_rate(Strategy("h", "reference", "h"), env) == LT
    assert dividend_rate(Strategy("cc", "income", "cc", [otm_call]), env) == LT          # qualified covered call
    assert dividend_rate(Strategy("col", "collars", "c", [put, otm_call]), env) == ST    # a put stops the holding clock
    assert dividend_rate(Strategy("itm", "income", "i", [itm_call]), env) == ST          # not a QCC


def test_dividend_schedule_projects_the_usual_spacing():
    hist = {"div_s_per_year": 4, "div_s_last_amt": 0.26, "div_s_last_date": "2026-08-11", "div_s": 0.003}
    ts, amts = dividend_schedule(hist, 340.0, date(2026, 10, 26), 2.0)
    assert len(ts) == 8 and all(a == 0.26 for a in amts)
    assert ts[0] == pytest.approx((date(2026, 11, 10) - date(2026, 10, 26)).days / 365, abs=0.01)
    assert np.allclose(np.diff(ts), 0.25, atol=0.01)
    assert dividend_schedule({"div_s": 0.0}, 100.0, date(2026, 10, 26), 2.0) == ((), ())


def test_compute_reports_dividends_and_assignment_per_plan():
    out = _run()
    rows = {x["id"]: x for x in out["strategies"]}
    assert rows["sell_now"]["dividends"] is None
    collar = next(x for x in out["strategies"] if x["family"] == "collars" and x["dividends"])
    assert not collar["dividends"]["qualified"] and collar["dividends"]["rate_pct"] == pytest.approx(ST * 100)
    for x in out["strategies"]:
        shorts = [l for l in x["legs"] if l["action"] == "SELL" and l["type"] != "futures"]
        assert len(x["assignment"]) == len(shorts) + sum(1 for r in x["rolls"] if r["action"] == "SELL")
        for a in x["assignment"]:
            assert 0.0 <= a["p_pct"] <= 100.0 and a["level"] in ("none", "low", "medium", "high")
    if "ditm_pair" in rows:
        idx = next(a for a in rows["ditm_pair"]["assignment"] if not a["american"])
        assert idx["p_pct"] == 0.0 and idx["twin"]
    json.dumps(out, allow_nan=False)
