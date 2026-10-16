"""The desk's payoff DISTRIBUTION — PoP / EV / ratios / VaR / CVaR / Kelly all come from one exact,
skew-consistent law (`lifecycle_service._payoff_distribution`). Regression cases for two verified defects:

  1. a 2.5%-step scenario grid made the breakeven bucket all-or-nothing → PoP off by ~7 pts, EV off by ~10% of
     the premium, and neighbouring strikes swapped rank on grid alignment;
  2. every leg valued at ONE averaged IV → a fairly-priced 95/90 put spread read EV −$31.5 / Omega 0.52.
"""

import math
import time

import numpy as np
import pytest
from scipy.special import ndtr

from app.services.desk_review_service import _opp_desk_metrics
from app.services.lifecycle_service import (
    _payoff_distribution,
    _weighted_var_cvar,
    compute_pretrade_metrics,
    terminal_payoff_curve,
)
from app.services.stock_service import bs_price

S, R, DTE = 100.0, 0.05, 30
T = DTE / 365.0


def _put(K, iv):
    return bs_price(S, K, T, R, iv, "put")


def _call(K, iv):
    return bs_price(S, K, T, R, iv, "call")


def _leg(K, right, sign, price, iv):
    return {"strike": K, "right": right, "sign": sign, "qty": 1, "price": price, "iv": iv * 100, "dte_years": T}


def _metrics(legs, capital, max_loss, max_profit, avg_iv, realized=None, atm_iv=None, stock=0.0):
    return compute_pretrade_metrics(legs, S, terminal_payoff_curve(legs, stock, S), capital, max_loss, max_profit,
                                    avg_iv, DTE, stock_shares=stock, r=R, realized_vol=realized, atm_iv=atm_iv)


def _exact_short_put_pop(K, prem, iv):
    be = K - prem
    return ndtr((math.log(S / be) + (R - 0.5 * iv * iv) * T) / (iv * math.sqrt(T))) * 100


class TestGridAccuracy:
    """Defect 1 — exact buckets (every strike + breakeven is an edge) instead of a 2.5% grid."""

    def test_short_put_pop_and_ev_match_closed_form(self):
        # The review's case: 97 put at 15% IV read PoP 74.9% vs exact 81.7%, EV −$5.24 vs ≈ −$0.2.
        prem = _put(97, 0.15)
        m = _metrics([_leg(97, "P", -1, prem, 0.15)], 1940, -(97 - prem) * 100, prem * 100, 0.15)
        assert m["pm"]["pop"] == pytest.approx(_exact_short_put_pop(97, prem, 0.15), abs=0.15)
        carry = -prem * 100 * (math.exp(R * T) - 1)            # a fair short's only EV is the forward carry
        assert m["pm"]["expected_value"] == pytest.approx(carry, abs=0.05)
        assert m["distribution"]["method"] == "calibrated"

    @pytest.mark.parametrize("iv,kpct", [(0.15, 0.95), (0.25, 0.93), (0.40, 0.90), (0.20, 0.955)])
    def test_pop_exact_across_vols(self, iv, kpct):
        K = S * kpct
        prem = _put(K, iv)
        m = _metrics([_leg(K, "P", -1, prem, iv)], K * 20, -(K - prem) * 100, prem * 100, iv)
        assert m["pm"]["pop"] == pytest.approx(_exact_short_put_pop(K, prem, iv), abs=0.15)

    def test_strike_ladder_pop_is_monotone(self):
        # On the old grid 4% OTM read a LOWER PoP than 2% OTM and 8% lower than 6% — rank flips from grid
        # alignment. Deeper strikes must always be strictly safer.
        pops = []
        for otm in (0.02, 0.03, 0.04, 0.05, 0.06, 0.07, 0.08, 0.10, 0.12):
            K = S * (1 - otm)
            iv = 0.25 + 0.6 * otm
            prem = _put(K, iv)
            pops.append(_metrics([_leg(K, "P", -1, prem, iv)], K * 20, -(K - prem) * 100, prem * 100, iv)["pm"]["pop"])
        assert all(b > a for a, b in zip(pops, pops[1:])), pops


class TestPerLegVol:
    """Defect 2 — each leg priced at its OWN vol, so a fair trade of any structure has EV ≈ carry, Omega ≈ 1."""

    def test_fair_put_spread_on_a_skew_has_zero_edge(self):
        # The review's case: 95/90 put spread at 24%/29% read EV −$31.5 / Omega 0.52 on a $45 credit.
        ps, pl = _put(95, 0.24), _put(90, 0.29)
        legs = [_leg(95, "P", -1, ps, 0.24), _leg(90, "P", 1, pl, 0.29)]
        credit = (ps - pl) * 100
        m = _metrics(legs, 500 - credit, -(500 - credit), credit, 0.265)
        assert m["pm"]["expected_value"] == pytest.approx(-credit * (math.exp(R * T) - 1), abs=0.1)
        assert m["pm"]["omega"] == pytest.approx(1.0, abs=0.02)
        assert m["distribution"]["max_price_err"] < 1e-4         # every leg repriced at its own vol

    def test_fair_call_spread_is_not_flattered(self):
        # The flat average pushed call spreads UP (spurious positive edge) on a downward-sloping call wing.
        cs, cl = _call(105, 0.19), _call(110, 0.18)
        legs = [_leg(105, "C", -1, cs, 0.19), _leg(110, "C", 1, cl, 0.18)]
        credit = (cs - cl) * 100
        m = _metrics(legs, 500 - credit, -(500 - credit), credit, 0.185)
        assert m["pm"]["expected_value"] == pytest.approx(-credit * (math.exp(R * T) - 1), abs=0.1)
        assert m["pm"]["omega"] == pytest.approx(1.0, abs=0.02)

    def test_fair_iron_condor_has_zero_edge(self):
        q = [(88, "P", 1, 0.31), (93, "P", -1, 0.25), (106, "C", -1, 0.18), (111, "C", 1, 0.19)]
        legs = [_leg(K, rt, sg, (_put if rt == "P" else _call)(K, iv), iv) for K, rt, sg, iv in q]
        credit = -sum(l["sign"] * l["price"] for l in legs) * 100
        m = _metrics(legs, 500 - credit, -(500 - credit), credit, 0.2325, atm_iv=0.21)
        assert m["pm"]["expected_value"] == pytest.approx(-credit * (math.exp(R * T) - 1), abs=0.15)
        assert m["distribution"]["atm_anchor"] is True

    def test_matches_an_exact_skewed_market(self):
        # Ground truth: an arbitrage-free 2-lognormal mixture with a crash component (a real put skew). Legs are
        # priced exactly from it; the desk's PoP/EV for spreads must match the mixture's own, exactly computed.
        mix = [(0.85, (1 - 0.15 * 0.94) / 0.85, 0.16), (0.15, 0.94, 0.38)]
        F0 = S * math.exp(R * T)

        def fwd_put(F, K, v):
            d1 = (math.log(F / K) + 0.5 * v * v) / v
            return K * ndtr(-(d1 - v)) - F * ndtr(-d1)

        def mix_put(K):
            return math.exp(-R * T) * sum(w * fwd_put(F0 * k, K, s * math.sqrt(T)) for w, k, s in mix)

        grid = np.linspace(0.5, 400.0, 159801)
        mid = (grid[1:] + grid[:-1]) / 2
        dens = sum(w * np.diff(ndtr((np.log(grid) - (math.log(F0 * k) - 0.5 * s * s * T)) / (s * math.sqrt(T))))
                   for w, k, s in mix)
        for ks, kl in ((95, 90), (93, 88)):
            ps, pl = mix_put(ks), mix_put(kl)
            legs = [_leg(ks, "P", -1, ps, 0.25), _leg(kl, "P", 1, pl, 0.30)]   # quoted IVs deliberately rough
            credit = (ps - pl) * 100
            pnl = -100 * (np.maximum(ks - mid, 0) - ps) + 100 * (np.maximum(kl - mid, 0) - pl)
            ref_pop = float(dens[pnl > 0].sum() / dens.sum() * 100)
            ref_ev = float((dens * pnl).sum() / dens.sum())
            m = _metrics(legs, 500 - credit, -(500 - credit), credit, 0.275, atm_iv=0.20)
            assert m["pm"]["pop"] == pytest.approx(ref_pop, abs=0.6)
            assert m["pm"]["expected_value"] == pytest.approx(ref_ev, abs=0.1)

    def test_desk_path_fair_put_spread(self):
        ps, pl = _put(95, 0.24), _put(90, 0.29)
        opp = {"structure": "put_credit_spread", "dte": DTE, "contracts": 1, "atm_iv_pct": 21.0,
               "collateral": 500 - (ps - pl) * 100, "max_loss": -(500 - (ps - pl) * 100), "max_profit": (ps - pl) * 100,
               "legs": [{"action": "SELL", "type": "PUT", "strike": 95, "mid": ps, "iv": 24.0, "expiration": "2026-11-14"},
                        {"action": "BUY", "type": "PUT", "strike": 90, "mid": pl, "iv": 29.0, "expiration": "2026-11-14"}]}
        dm = _opp_desk_metrics(opp, S, 4.3, hv=0.21)              # physical = implied → a fair trade has no edge
        assert dm["distribution"]["method"] == "calibrated"
        assert abs(dm["pm"]["expected_value"]) < 0.5
        assert dm["pm"]["omega"] == pytest.approx(1.0, abs=0.03)


class TestPhysicalWideningAndFallbacks:
    def test_negative_vrp_single_leg_keeps_max_iv_hv(self):
        # A lone leg with no ATM anchor: realized above implied ⇒ exactly the old max(IV, HV) lognormal.
        prem = _put(92, 0.22)
        m = _metrics([_leg(92, "P", -1, prem, 0.22)], 1840, -(92 - prem) * 100, prem * 100, 0.22, realized=0.40)
        assert m["distribution"]["weight_vol_pct"] == pytest.approx(40.0, abs=0.05)
        assert m["pm"]["pop"] == pytest.approx(_exact_short_put_pop(92, prem, 0.40), abs=0.15)
        assert m["pm"]["expected_value"] < -50                  # cheap vol vs a stock that moves → real loss

    def test_dropped_leg_keeps_the_callers_curve(self):
        legs = [_leg(95, "P", -1, 1.2, 0.24), _leg(90, "P", 1, 0.5, 0.29)]
        curve = terminal_payoff_curve(legs, 0.0, S)
        m = compute_pretrade_metrics(legs[:1], S, curve, 430, -430, 70, 0.24, DTE)
        assert m["distribution"]["method"] == "scenario_grid"

    def test_no_legs_uses_the_scenario_grid(self):
        curve = terminal_payoff_curve([_leg(95, "P", -1, 1.2, 0.24)], 0.0, S)
        m = compute_pretrade_metrics([], S, curve, 430, -430, 120, 0.24, DTE)
        assert m["distribution"]["method"] == "scenario_grid"
        assert m["pm"]["pop"] is not None

    def test_arbitrageable_quotes_fall_back_without_error(self):
        # Credit (6.5) above the spread width (5) — no density can price that; keep the exact grid, flat law.
        legs = [_leg(95, "P", -1, 7.0, 0.24), _leg(90, "P", 1, 0.5, 0.29)]
        m = _metrics(legs, 100, -100, 650, 0.265)
        assert m["distribution"]["method"] == "lognormal_fallback"
        assert m["pm"]["pop"] == pytest.approx(100.0)

    def test_scales_linearly_with_contracts(self):
        ps, pl = _put(95, 0.24), _put(90, 0.29)
        one = [_leg(95, "P", -1, ps, 0.24), _leg(90, "P", 1, pl, 0.29)]
        ten = [dict(l, qty=10) for l in one]
        c = (ps - pl) * 100
        m1 = _metrics(one, 500 - c, -(500 - c), c, 0.265)
        m10 = _metrics(ten, (500 - c) * 10, -(500 - c) * 10, c * 10, 0.265)
        assert m10["pm"]["pop"] == pytest.approx(m1["pm"]["pop"], abs=0.01)
        assert m10["risk"]["cvar_95"] == pytest.approx(m1["risk"]["cvar_95"] * 10, rel=1e-3)


class TestTailAndCost:
    def test_expected_shortfall_counts_the_boundary_bucket_fractionally(self):
        # 3% mass at −$100, 97% at $0: the worst 5% = 3% at −100 + 2% at 0 ⇒ ES = $60 (the old code said $100).
        var, cvar = _weighted_var_cvar(np.array([-100.0, 0.0]), np.array([0.03, 0.97]), 0.05)
        assert cvar == pytest.approx(60.0)
        assert var == pytest.approx(0.0)

    def test_cheap_in_time_and_memory(self):
        q = [(88, "P", 1, 0.31), (93, "P", -1, 0.25), (106, "C", -1, 0.18), (111, "C", 1, 0.19)]
        legs = [_leg(K, rt, sg, (_put if rt == "P" else _call)(K, iv), iv) for K, rt, sg, iv in q]
        t0 = time.perf_counter()
        for _ in range(20):
            d = _payoff_distribution(legs, 0.0, S, DTE, R, 0.23, 0.18, atm_iv=0.21)
        per_call_ms = (time.perf_counter() - t0) / 20 * 1000
        assert len(d["w"]) < 400                                 # a few hundred buckets, never a big grid
        assert per_call_ms < 25                                  # ~2 ms locally; generous for slow CI
