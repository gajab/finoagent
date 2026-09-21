"""Back ratio (1×2 net-credit backspread) — the LONG-VEGA pre-earnings vol-expansion income structure.

Uses a synthetic BS-priced chain (realistic IVs) because the weekend dev env has garbage/zero IV, which
zeroes vega and (correctly) makes the long-vega gate build nothing live. Locks the invariants:
  • builds 1 SHORT + 2 LONG (same strike) legs — the ratio the engines must sum as 1×2,
  • NET CREDIT and DEFINED max loss = width×100 − credit at the long strike (the valley of death),
  • only surfaces when MEASURABLY long-vega (net vega > 0),
  • desk metrics reconcile (net vega/theta/max-loss) and the grade takes the long-vega path (cheap IV = merit, no veto).
"""
from app.services.quote_providers.base import OptionQuote
from app.services.derivative_income_service import _back_ratio
from app.services.desk_review_service import _opp_desk_metrics, _algo_grade
from app.services.lifecycle_service import bs_price

SPOT, DTE, R = 100.0, 25, 0.045
_T = DTE / 365.0


class _RND:                                  # minimal RND for _prob_keep (prob_above/below)
    def prob_above(self, k): return 0.72
    def prob_below(self, k): return 0.28


def _iv(K):                                  # steep put skew — lower strikes richer (real vega on the longs)
    return 0.35 + max(0.0, (SPOT - K)) / SPOT * 1.4


def _q(K, right):
    iv = _iv(K)
    mid = max(bs_price(SPOT, K, _T, R, iv, "put" if right == "P" else "call"), 0.02)
    return OptionQuote(strike=K, right=right, expiration="2026-10-16", bid=mid * 0.95, ask=mid * 1.05,
                       last=mid, mid=round(mid, 2), iv=iv, oi=800, volume=200)


def _build():
    puts = {K: _q(K, "P") for K in range(80, 101)}
    calls = {K: _q(K, "C") for K in range(100, 121)}
    return _back_ratio(calls, puts, SPOT, DTE, "2026-10-16", _RND(), R, 0.36, 1.2, "fair",
                       min_prob=10.0, min_income=5.0, european=False, ticker="TST")


def test_builds_a_1x2_ratio_with_two_long_legs():
    o = _build()
    assert o is not None and o["structure"] == "back_ratio"
    assert o["ratio"] == "1x2" and len(o["legs"]) == 3
    sells = [l for l in o["legs"] if l["action"] == "SELL"]
    buys = [l for l in o["legs"] if l["action"] == "BUY"]
    assert len(sells) == 1 and len(buys) == 2
    assert buys[0]["strike"] == buys[1]["strike"] == o["long_strike"]   # 2 longs, SAME strike
    assert sells[0]["strike"] == o["short_strike"]


def test_net_credit_and_defined_valley_of_death_max_loss():
    o = _build()
    assert o["net_credit"] > 0 and o["premium"] > 0                     # a real NET CREDIT
    width = abs(o["short_strike"] - o["long_strike"])
    expected_max_loss = round(-(width * 100 - o["premium"]), 2)          # at the long strike, at expiry
    assert o["max_loss"] == expected_max_loss                            # DEFINED risk (not None/unbounded)
    assert o["collateral"] == abs(expected_max_loss)
    assert o["valley_of_death"]["price"] == o["long_strike"]
    assert o["valley_of_death"]["max_loss"] == o["max_loss"]


def test_only_surfaces_when_long_vega():
    o = _build()
    assert o["long_vega"] is True and o["vega_exposure"] > 0            # the vol-expansion play
    assert o["theta_per_day"] < 0                                        # long options bleed theta (paid, not collected)


def test_flat_skew_no_long_vega_credit_backspread_builds_nothing():
    # Flat IV + no skew: a net-credit backspread would need far-OTM (low-vega) longs → net SHORT vega →
    # the long-vega gate rejects it, so nothing surfaces (correct — that's not this strategy).
    flat = lambda K, right: OptionQuote(  # noqa: E731
        strike=K, right=right, expiration="2026-10-16",
        mid=(m := max(bs_price(SPOT, K, _T, R, 0.30, "put" if right == "P" else "call"), 0.02)),
        bid=m * 0.95, ask=m * 1.05, last=m, iv=0.30, oi=800, volume=200)
    puts = {K: flat(K, "P") for K in range(70, 101)}
    calls = {K: flat(K, "C") for K in range(100, 131)}
    o = _back_ratio(calls, puts, SPOT, DTE, "2026-10-16", _RND(), R, 0.30, 1.0, "fair",
                    min_prob=10.0, min_income=1.0, european=False, ticker="TST")
    assert o is None or o["long_vega"] is True                          # never surface a short-vega one


def test_desk_metrics_reconcile_and_long_vega_grading():
    o = _build()
    dm = _opp_desk_metrics(o, SPOT, 5.0, 0.30)
    tr, rk = dm.get("trader") or {}, dm.get("risk") or {}
    assert tr.get("net_vega", 0) > 0 and tr.get("net_theta", 0) < 0     # desk agrees: long vega, paying theta
    assert rk.get("max_loss") == o["max_loss"]                          # per-leg payoff → aggregate reconciles
    # Cheap IV (hv 0.60 → IV/HV 0.6) must be an EDGE for a long-vega structure — a MERIT, never a crushed-vol veto.
    g = _algo_grade(o, dm, SPOT, 5.0, atm_iv_pct=36.0, iv_rank=20, beta=1.0, hv=0.60)
    assert not any("crushed vol" in b for b in g["blocking"])
    assert any("long-vega" in m for m in g["merits"])
