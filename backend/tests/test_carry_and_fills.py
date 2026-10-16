"""Income Desk: expected-fill pricing (#4), the carry-to-risk Income lens (#3), and the market-implied payoff
law shared by every trade on an expiry.

Regression cases for verified defects:
  • premiums / yields / EV were all at MID while quotes up to 40–50% wide passed the executability gate;
  • the Income lens (premium yield vs SOFR) saturated at ~13%/yr → 30/30 for EVERY candidate (financing-
    dependent, and a covered call's capped stock gain counted as income);
  • the scan's SVI law (fitted to the provider's IV field) priced SPY near-money puts 15–25% below their mids,
    and per-trade calibration gave each trade a DIFFERENT tail (1% quantile 592–640 for one expiry).
"""

import math

import numpy as np
import pytest
from scipy.special import ndtr

import app.services.lifecycle_service as L
from app.services.derivative_income_service import (
    COMMISSION_PER_CONTRACT,
    _attach_fill_economics,
    _expected_fill,
    _rnd_grid,
)
from app.services.desk_review_service import _opp_desk_metrics, _opp_legs
from app.services.hedging_service import _build_rnd
from app.services.quote_providers import OptionQuote
from app.services.stock_service import bs_price

S, R, DTE = 100.0, 0.045, 30
T = DTE / 365.0
COMM = COMMISSION_PER_CONTRACT / 100.0


def _q(K, right, bid, ask, oi=500, vol=100, iv=0.25):
    return OptionQuote(strike=K, right=right, expiration="2026-11-14", bid=bid, ask=ask, last=(bid + ask) / 2,
                       mid=(bid + ask) / 2, iv=iv, oi=oi, volume=vol)


class TestExpectedFill:
    def test_sell_below_mid_buy_above_never_past_the_quote(self):
        q = _q(95, "P", 1.00, 1.10)
        sell, sc = _expected_fill(q, "SELL")
        buy, bc = _expected_fill(q, "BUY")
        assert 1.00 - COMM <= sell < q.mid
        assert q.mid < buy <= 1.10 + COMM
        assert sc == pytest.approx(q.mid - sell) and bc == pytest.approx(buy - q.mid)

    def test_give_up_grows_with_width_and_thin_books(self):
        tight = _expected_fill(_q(95, "P", 1.00, 1.02), "SELL")[1] - COMM
        wide = _expected_fill(_q(95, "P", 0.80, 1.22), "SELL")[1] - COMM
        assert tight / 0.01 == pytest.approx(0.25, abs=0.01)     # k = 0.25 of the half-spread on a tight book
        assert wide / 0.21 == pytest.approx(0.75, abs=0.01)      # k caps at 0.75 on a 42%-wide quote
        deep = _expected_fill(_q(95, "P", 1.00, 1.10, oi=500), "SELL")[1]
        thin = _expected_fill(_q(95, "P", 1.00, 1.10, oi=10, vol=0), "SELL")[1]
        assert thin > deep

    def test_opp_entry_cost_and_premium_at_fill(self):
        legs = [{"entry_cost_ps": 0.03}, {"entry_cost_ps": 0.02}]
        o = _attach_fill_economics({"legs": legs, "contracts": 3, "premium": 120.0, "collateral": 1500, "dte": 30})
        assert o["entry_cost"] == pytest.approx(0.05 * 100 * 3)
        assert o["premium_fill"] == pytest.approx(120.0 - 15.0)
        from app.services.trade_math import annualized_return_pct
        assert o["premium_annualized_fill_pct"] == pytest.approx(annualized_return_pct(105.0, 1500, 30), abs=0.01)
        assert o["premium_annualized_fill_pct"] < annualized_return_pct(120.0, 1500, 30)


def _fair_put_spread_opp(focus=False):
    ps, pl = bs_price(S, 95, T, R, 0.24, "put"), bs_price(S, 90, T, R, 0.29, "put")
    legs = []
    for K, act, p, iv in ((95, "SELL", ps, 24.0), (90, "BUY", pl, 29.0)):
        q = _q(K, "P", round(p - 0.03, 2), round(p + 0.03, 2))
        fill, cost = _expected_fill(q, act)
        legs.append({"action": act, "type": "PUT", "strike": K, "mid": q.mid, "fill": fill, "entry_cost_ps": cost,
                     "iv": iv, "expiration": "2026-11-14", "bid": q.bid, "ask": q.ask})
    credit = (legs[0]["mid"] - legs[1]["mid"]) * 100
    o = {"structure": "put_credit_spread", "dte": DTE, "contracts": 1, "atm_iv_pct": 21.0, "premium": credit,
         "collateral": 500 - credit, "max_loss": -(500 - credit), "max_profit": credit, "legs": legs}
    if focus:
        o["_is_focus"] = True
    return _attach_fill_economics(o)


class TestDeskAtFill:
    def test_new_trade_ev_is_minus_the_entry_cost(self):
        # The law is calibrated on MID (fair ⇒ EV ≈ carry); P&L is booked at the FILL ⇒ EV ≈ −entry cost.
        o = _fair_put_spread_opp()
        dm = _opp_desk_metrics(o, S, 4.3, hv=0.21)              # physical = implied (ATM 21%) → no VRP in play
        assert dm["pm"]["expected_value"] == pytest.approx(-o["entry_cost"], abs=0.6)
        assert dm["distribution"]["method"].startswith("calibrated")

    def test_held_trade_is_not_charged_entry(self):
        dm = _opp_desk_metrics(_fair_put_spread_opp(focus=True), S, 4.3, hv=0.21)
        assert abs(dm["pm"]["expected_value"]) < 0.6               # entry already paid — holding costs nothing
        assert all(l["price"] == l["mid"] for l in _opp_legs(_fair_put_spread_opp(focus=True)))

    def test_evaluate_flag_charges_a_focus_build(self):
        o = _fair_put_spread_opp(focus=True)
        o["_charge_entry"] = True
        assert all(l["price"] == l2["fill"] for l, l2 in zip(_opp_legs(o), o["legs"]))


def _csp_metrics(K, iv, capital):
    prem = bs_price(S, K, T, R, iv, "put")
    legs = [{"strike": K, "right": "P", "sign": -1, "qty": 1, "price": prem, "iv": iv * 100, "dte_years": T}]
    return L.compute_pretrade_metrics(legs, S, L.terminal_payoff_curve(legs, 0.0, S), capital, -(K - prem) * 100,
                                      prem * 100, iv, DTE, r=R, sofr_pct=4.3, atm_iv=0.20)


def _income(m):
    return next(l for l in m["quant"]["lenses"] if l["label"] == "Income")["contribution"]


class TestCarryToRisk:
    def test_financing_neutral(self):
        # Same put: Reg-T margin vs fully cash-secured collateral → the SAME Income lens (it used to differ).
        prem = bs_price(S, 94, T, R, 0.25, "put")
        bpr = max(0.2 * S - (S - 94) + prem, 0.1 * 94 + prem) * 100
        assert _income(_csp_metrics(94, 0.25, bpr)) == pytest.approx(_income(_csp_metrics(94, 0.25, 94 * 100)))

    def test_lens_discriminates_across_a_ladder(self):
        # Every strike 6%→14% OTM used to score Income 30.0. Carry-to-risk falls as the premium shrinks
        # faster than the tail — so the lens now ranks.
        inc = [_income(_csp_metrics(S * (1 - otm), 0.25 + 0.6 * otm, 2000)) for otm in (0.04, 0.06, 0.08, 0.10, 0.12, 0.14)]
        assert len(set(round(x, 1) for x in inc)) == len(inc)
        assert all(b < a for a, b in zip(inc, inc[1:])), inc
        assert max(inc) < 30.0                                      # not saturated

    def test_covered_call_income_is_premium_only(self):
        prem = bs_price(S, 105, T, R, 0.19, "call")
        legs = [{"strike": 105, "right": "C", "sign": -1, "qty": 1, "price": prem, "iv": 19.0, "dte_years": T}]
        m = L.compute_pretrade_metrics(legs, S, L.terminal_payoff_curve(legs, 0.0, S), S * 100, None,
                                       prem * 100 + 500, 0.19, DTE, r=R, atm_iv=0.20)
        assert m["carry"]["credit"] == pytest.approx(prem * 100, abs=0.01)   # never the +$500 stock gain to strike

    def test_sqrt_time_scaling(self):
        legs = [{"strike": 95, "right": "P", "sign": -1, "qty": 1, "price": 1.0, "dte_years": 7 / 365}]
        a = L._carry_to_risk(legs, 500.0, 7)
        b = L._carry_to_risk(legs, 500.0, 28)
        assert a["per_cycle"] == pytest.approx(b["per_cycle"])
        assert a["ctr"] / b["ctr"] == pytest.approx(2.0)            # √(28/7), not the linear 4×

    def test_debit_structure_has_no_carry(self):
        legs = [{"strike": 100, "right": "C", "sign": 1, "qty": 1, "price": 2.0, "dte_years": T}]
        assert L._carry_to_risk(legs, 200.0, DTE) is None

    def test_tail_exposure_is_financing_neutral(self):
        put = [{"strike": 94, "right": "P", "sign": -1, "qty": 2}]
        assert L._tail_exposure(put, S, None) == pytest.approx(94 * 100 * 2)
        assert L._tail_exposure(put, S, -455.0) == pytest.approx(455.0)
        call = [{"strike": 108, "right": "C", "sign": -1, "qty": 1}]
        assert L._tail_exposure(call, S, None) == pytest.approx(S * 100)


# ── The expiry's market-implied law: calibrated to the MIDS, shared by every trade ─────────────────────────
_MIX = [(0.85, (1 - 0.15 * 0.94) / 0.85, 0.16), (0.15, 0.94, 0.38)]       # arbitrage-free crash-skew market
F0 = S * math.exp(R * T)


def _mix_put(K):
    out = 0.0
    for w, k, s in _MIX:
        v = s * math.sqrt(T); F = F0 * k
        d1 = (math.log(F / K) + 0.5 * v * v) / v
        out += w * (K * ndtr(-(d1 - v)) - F * ndtr(-d1))
    return math.exp(-R * T) * out


def _mix_call(K):
    return _mix_put(K) + S - K * math.exp(-R * T)


@pytest.fixture(scope="module")
def chain():
    """A full chain priced from the mixture at 2¢-wide quotes — but with a deliberately WRONG flat provider IV
    (the yfinance failure mode: the IV field disagrees with the mids)."""
    calls, puts = {}, {}
    for K in np.arange(60.0, 151.0, 1.0):
        for right, px, book in (("C", _mix_call(K), calls), ("P", _mix_put(K), puts)):
            if px < 0.02:
                continue
            book[float(K)] = _q(float(K), right, round(px - 0.01, 2), round(px + 0.01, 2), iv=0.18)
    rnd = _build_rnd(calls, puts, sorted(set(calls) | set(puts)), S, DTE)
    return calls, puts, rnd


class TestMarketLaw:
    def test_grid_reprices_every_mid(self, chain):
        calls, puts, rnd = chain
        g = _rnd_grid(rnd, 0.20, calls, puts, S, DTE)
        assert g["basis"] == "mid_calibrated"
        K, cdf, pe = (np.array(g[k]) for k in ("K", "cdf", "pe"))
        mean = pe[0]
        for k in (80.0, 88.0, 94.0, 98.0):
            fwd_put = k * np.interp(k, K, cdf) - (mean - np.interp(k, K, pe))
            assert fwd_put * math.exp(-g["r"] * g["T"]) == pytest.approx(puts[k].mid, abs=0.03)

    def test_one_consistent_tail_for_every_trade(self, chain):
        # Each trade on the expiry must see the SAME market law: the 1% price quantile and the tail shape can't
        # depend on which strike is traded (per-trade 2-point calibration gave 592–640 on SPY).
        calls, puts, rnd = chain
        g = _rnd_grid(rnd, 0.20, calls, puts, S, DTE)
        q01, cvars = [], []
        for k in (98.0, 95.0, 92.0, 88.0, 84.0):
            mid = puts[k].mid
            legs = [{"strike": k, "right": "P", "sign": -1, "qty": 1, "price": mid, "mid": mid, "iv": 18.0, "dte_years": T}]
            d = L._payoff_distribution(legs, 0.0, S, DTE, 0.05, 0.18, None, atm_iv=0.20, rnd_grid=g)
            assert d["method"] in ("calibrated_rnd", "rnd")
            o = np.argsort(d["price"]); cw = np.cumsum(d["w"][o])
            q01.append(float(d["price"][o][np.searchsorted(cw, 0.01)]))
            # the exact mixture's CVaR99 for this short put
            grid = np.linspace(0.5, 400, 79801); midp = (grid[1:] + grid[:-1]) / 2
            dens = sum(w * np.diff(ndtr((np.log(grid) - (math.log(F0 * kk) - 0.5 * s * s * T)) / (s * math.sqrt(T))))
                       for w, kk, s in _MIX)
            ref = L._weighted_var_cvar(-100 * (np.maximum(k - midp, 0) - mid), dens / dens.sum(), 0.01)[1]
            cvars.append((L._weighted_var_cvar(d["pnl"], d["w"], 0.01)[1], ref))
        assert max(q01) - min(q01) < 1.5                              # one law, not one per strike
        for ours, ref in cvars:
            assert ours == pytest.approx(ref, rel=0.08)

    def test_no_chain_quotes_keeps_the_provider_smile(self, chain):
        _, _, rnd = chain
        g = _rnd_grid(rnd, 0.20)
        assert g["basis"] == "provider_iv"

    def test_a_garbage_wing_quote_cannot_collapse_the_law(self, chain):
        # JPM 2026-11: a stale 190 put at $1.08 (the 210 put was $0.07) dragged the calibrated law onto itself
        # (all mass at $185 → every put read PoP 0%, every call 100%). The no-arbitrage filter drops it.
        calls, puts, rnd = chain
        bad = dict(puts)
        bad[62.0] = _q(62.0, "P", 1.05, 1.11)                 # far-OTM put priced above the 70 put — impossible
        g = _rnd_grid(rnd, 0.20, calls, bad, S, DTE)
        assert g["basis"] == "mid_calibrated"
        K, cdf = np.array(g["K"]), np.array(g["cdf"])
        assert L.law_is_sane(K, cdf, g["forward"], g["T"], 0.20)
        assert np.interp(0.9 * g["forward"], K, cdf) < 0.2     # the body stays where the market put it

    def test_arb_clean_drops_the_stale_wing(self):
        from app.services.derivative_income_service import _arb_clean
        q = [(62.0, 1.08, 0.03), (70.0, 0.07, 0.01), (80.0, 0.35, 0.01), (90.0, 1.40, 0.01), (95.0, 2.6, 0.01)]
        kept = [k for k, _, _ in _arb_clean(q, 100.0)]
        assert 62.0 not in kept and kept == [70.0, 80.0, 90.0, 95.0]

    def test_insane_grid_is_rejected_by_the_desk(self):
        # A collapsed law (a point mass) must never reach the desk — it falls back to the lognormal prior.
        K = np.linspace(50, 200, 401)
        cdf = np.where(K < 85.0, 0.0, 1.0).astype(float)
        assert not L.law_is_sane(K, cdf, F0, T, 0.20)
        g = {"K": K.tolist(), "cdf": cdf.tolist(), "pe": np.where(K < 85, 85.0, 0.0).tolist(), "forward": F0, "T": T}
        assert L._usable_rnd_grid(g, T, 0.20) is None


class TestTailBasis:
    """Tail lens = CVaR ÷ the UNDERLYING NOTIONAL the shorts control (chosen after the live 4-variant comparison):
    financing-neutral like 'exposure', but it doesn't punish defined risk for having a small, fully-used tail."""

    def test_default_basis_is_underlying_notional(self):
        assert L._TAIL_BASIS == "notional"

    def test_notional_counts_the_larger_short_side(self):
        legs = [{"strike": 95, "right": "P", "sign": -1, "qty": 2}, {"strike": 90, "right": "P", "sign": 1, "qty": 2},
                {"strike": 105, "right": "C", "sign": -1, "qty": 1}]
        assert L._tail_notional(legs, S) == pytest.approx(S * 100 * 2)
        assert L._tail_notional([{"strike": 90, "right": "P", "sign": 1, "qty": 1}], S) is None

    def test_financing_neutral_and_reported(self):
        # Reg-T margin vs cash-secured capital no longer moves the Tail lens either.
        prem = bs_price(S, 94, T, R, 0.25, "put")
        bpr = max(0.2 * S - (S - 94) + prem, 0.1 * 94 + prem) * 100

        def tail(m):
            return next(l for l in m["quant"]["lenses"] if l["label"] == "Tail")["contribution"]
        a, b = _csp_metrics(94, 0.25, bpr), _csp_metrics(94, 0.25, 94 * 100)
        assert tail(a) == pytest.approx(tail(b))
        assert a["tail_basis"] == "notional"

    def test_defined_risk_is_not_punished_for_its_capped_tail(self):
        # A put spread's 1% tail is its whole (small) max loss; on the 'exposure' basis that read as a 100% tail
        # (Tail lens ≈ 0). On the notional basis it scores at least as well as the naked put's open tail.
        ps, pl = bs_price(S, 95, T, R, 0.24, "put"), bs_price(S, 90, T, R, 0.29, "put")
        spread = [{"strike": 95, "right": "P", "sign": -1, "qty": 1, "price": ps, "iv": 24.0, "dte_years": T},
                  {"strike": 90, "right": "P", "sign": 1, "qty": 1, "price": pl, "iv": 29.0, "dte_years": T}]
        c = (ps - pl) * 100
        ms = L.compute_pretrade_metrics(spread, S, L.terminal_payoff_curve(spread, 0.0, S), 500 - c, -(500 - c), c,
                                        0.265, DTE, r=R, atm_iv=0.21)
        mn = _csp_metrics(95, 0.24, 9500)

        def tail(m):
            return next(l for l in m["quant"]["lenses"] if l["label"] == "Tail")["contribution"]
        assert tail(ms) >= tail(mn)
