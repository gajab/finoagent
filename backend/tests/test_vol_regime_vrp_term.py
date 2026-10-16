"""Income Desk scoring — three evidence-driven changes:

  #1 DIRECTION is information, not score. Trend drift / MACD momentum / systemic-beta market direction are shown
     but carry 0 points, the breach (touch) odds are drift-free, and the MACD "WAIT" hold is retired — the 2016–26
     point-in-time backtest found no directional edge. TURBULENCE is priced as dispersion instead: one calibrated
     physical-vol forecast (trailing gap-aware vol × 1.05, × 1.20 in a compressed calm near the highs).
  #2 TERM STRUCTURE sign by what the slope means: single stock inverted → front rich (+), contango → (−);
     index / ETF inverted → stress regime (−); an earnings print inside the curve → 0.
  #3 The VOLATILITY RISK PREMIUM in dollars: the payoff law is the PHYSICAL forecast in both directions (rich IV
     → +EV, cheap IV → −EV), tails never thinner than the market prices them, an earnings print priced at its
     implied size (no fake edge), and no separate VRP grade points (the crushed-vol hard veto stays).
"""

import math
from datetime import date, timedelta

import pytest

import app.services.lifecycle_service as L
from app.services.desk_review_service import (
    _VOL_K_BASE,
    _VOL_K_COMPRESSED_CALM,
    _algo_grade,
    _physical_vol_forecast,
    _ta_alignment,
    _term_structure_points,
)
from app.services.stock_service import bs_price

S, R, DTE = 100.0, 0.05, 30
T = DTE / 365.0


# ── #1 direction is information; turbulence is dispersion ────────────────────────────────────────────────────
class TestPhysicalVolForecast:
    def test_normal_state_is_trailing_times_base(self):
        f, k, st = _physical_vol_forecast(0.30, 0.30, 0.30, 90.0, 100.0)       # 10% off the high, no compression
        assert (k, st) == (_VOL_K_BASE, "normal") and f == pytest.approx(0.30 * _VOL_K_BASE)

    def test_compressed_calm_near_highs_expands(self):
        f, k, st = _physical_vol_forecast(0.20, 0.15, 0.22, 98.0, 100.0)       # 21/63 = 0.68 and 2% off the high
        assert st == "compressed_calm" and f == pytest.approx(0.20 * _VOL_K_COMPRESSED_CALM)

    def test_drawdown_adds_nothing_on_top_of_trailing_vol(self):
        # Turbulence persists (GARCH) — the trailing vol already carries it; the calibration found no extra
        # widening in drawdowns (RMS 1.04× at >35% off the high), so a deep drawdown stays at the base multiplier.
        f, k, _ = _physical_vol_forecast(0.55, 0.60, 0.50, 60.0, 100.0)
        assert k == _VOL_K_BASE and f == pytest.approx(0.55 * _VOL_K_BASE)

    def test_post_earnings_uses_the_ex_event_diffusion_vol(self):
        f, _, st = _physical_vol_forecast(0.60, 0.70, 0.40, 95.0, 100.0, post_earnings_diffusion=0.28)
        assert st == "post_earnings" and f == pytest.approx(0.28 * _VOL_K_BASE)

    def test_missing_vol(self):
        assert _physical_vol_forecast(None, None, None, 100.0, 100.0) == (None, None, None)


def _csp_opp(K=90.0, iv=30.0):
    prem = bs_price(S, K, T, R, iv / 100, "put")
    return {"structure": "cash_secured_put", "dte": DTE, "atm_iv_pct": iv, "max_loss": -(K - prem) * 100,
            "short_strike": K, "legs": [{"action": "SELL", "type": "PUT", "strike": K, "mid": prem, "iv": iv,
                                         "delta": -0.12, "bid_ask_spread_pct": 3.0}]}


def _ta(mu, hist=None):
    return {"_drift_mu": mu, "institutional": {"volume_profile": {}},
            "macd": {"histogramValues": hist or [0.0, 0.0, 0.0, 0.0, 0.0]}}


class TestDirectionIsInfoOnly:
    def test_trend_shown_but_not_scored_and_breach_is_drift_free(self):
        up = _ta_alignment(_csp_opp(), _ta(+0.50), None, S, hv=0.30)
        down = _ta_alignment(_csp_opp(), _ta(-0.50, [0, 0, 1.0, 0.5, -1.5]), None, S, hv=0.30)
        for bonus, _note, factors in (up, down):
            trend = next(f for f in factors if f["label"] == "Trend drift")
            assert trend["points"] == 0 and trend["detail"].startswith("info only")
        assert up[0] == down[0]                                                # same score either way
        breach = lambda fs: next(f for f in fs if f["label"] == "Breach risk")
        assert breach(up[2])["points"] == breach(down[2])["points"]

    def test_market_direction_is_info_only(self):
        _, _, fs = _ta_alignment(_csp_opp(), _ta(0.0), None, S, hv=0.30, beta=2.0, mkt_drift=-0.25)
        sysb = next(f for f in fs if f["label"] == "Systemic beta")
        assert sysb["points"] == 0 and "info only" in sysb["detail"]

    def test_momentum_never_holds_a_trade(self):
        g = _algo_grade(_csp_opp(), {"pm": {}, "risk": {}}, S, 4.3, 30.0, None, None, hv=0.30,
                        macd={"accel_norm": -0.02, "histogram": -2.0})            # accelerating DOWN at a short put
        assert g["timing_hold"] == []


# ── #2 term structure, signed by what the slope means ────────────────────────────────────────────────────────
class TestTermStructureSign:
    today = date(2026, 10, 16)

    def test_single_stock(self):
        inv, _ = _term_structure_points("backwardation", -3.0, 60, False, None, self.today)
        con, _ = _term_structure_points("contango", +3.0, 60, False, None, self.today)
        assert inv > 0 > con                                    # front rich when inverted, cheap in contango

    def test_index_or_etf(self):
        inv, note = _term_structure_points("backwardation", -3.0, 60, True, None, self.today)
        con, _ = _term_structure_points("contango", +3.0, 60, True, None, self.today)
        assert con > 0 > inv and "STRESS" in note

    def test_earnings_inside_the_curve_is_neutral(self):
        ne = (self.today + timedelta(days=20)).isoformat()
        pts, note = _term_structure_points("backwardation", -6.0, 60, False, ne, self.today)
        assert pts == 0 and "earnings-driven" in note
        later = (self.today + timedelta(days=90)).isoformat()                 # print beyond the back expiry
        assert _term_structure_points("backwardation", -6.0, 60, False, later, self.today)[0] > 0

    def test_flat_or_missing(self):
        assert _term_structure_points("flat", 0.2, 60, False, None, self.today) == (0, None)
        assert _term_structure_points(None, None, None, False, None, self.today) == (0, None)


# ── #3 the volatility risk premium in dollars ────────────────────────────────────────────────────────────────
def _put_metrics(phys, iv=0.25, K=92.0, event_var=0.0, atm=None):
    prem = bs_price(S, K, T, R, iv, "put")
    legs = [{"strike": K, "right": "P", "sign": -1, "qty": 1, "price": prem, "iv": iv * 100, "dte_years": T}]
    return L.compute_pretrade_metrics(legs, S, L.terminal_payoff_curve(legs, 0.0, S), K * 20, -(K - prem) * 100,
                                      prem * 100, iv, DTE, r=R, realized_vol=phys, atm_iv=atm or iv,
                                      event_var=event_var)


class TestVrpInDollars:
    def test_rich_implied_is_positive_ev(self):
        fair, rich = _put_metrics(0.25), _put_metrics(0.18)
        assert abs(fair["pm"]["expected_value"]) < 0.5
        assert rich["pm"]["expected_value"] > 10                 # the seller's edge now shows up as dollars
        assert rich["pm"]["pop"] > fair["pm"]["pop"]
        assert rich["distribution"]["measure"] == "physical_narrower"

    def test_tails_never_thinner_than_the_market(self):
        # Narrowing (rich IV) moves expectation, NOT the tail: CVaR stays the market's.
        fair, rich = _put_metrics(0.25), _put_metrics(0.18)
        assert rich["risk"]["cvar_99"] == pytest.approx(fair["risk"]["cvar_99"], rel=1e-6)

    def test_cheap_implied_is_negative_ev_and_a_wider_tail(self):
        fair, cheap = _put_metrics(0.25), _put_metrics(0.35)
        assert cheap["pm"]["expected_value"] < -20
        assert cheap["risk"]["cvar_99"] > fair["risk"]["cvar_99"]
        assert cheap["distribution"]["measure"] == "physical_wider"

    def test_an_earnings_print_is_not_mistaken_for_edge(self):
        # IV 40% carries an earnings event; diffusion vol is 25%. Without the event the law would read the event
        # premium as a huge edge; with the event priced at its implied size, it is (about) fair.
        ev = (0.40 ** 2 - 0.25 ** 2) * T
        fake = _put_metrics(0.25, iv=0.40)
        priced = _put_metrics(0.25, iv=0.40, event_var=ev)
        assert fake["pm"]["expected_value"] > 30
        assert abs(priced["pm"]["expected_value"]) < 1.0

    def test_grade_has_no_vrp_points_but_keeps_the_crushed_vol_veto(self):
        rich = _algo_grade(_csp_opp(iv=30.0), {"pm": {}, "risk": {}}, S, 4.3, 30.0, 80.0, None, hv=0.20)
        assert rich["components"]["vrp"] == 0
        assert any("priced into the expected value" in m for m in rich["merits"])
        crushed = _algo_grade(_csp_opp(iv=30.0), {"pm": {}, "risk": {}}, S, 4.3, 30.0, 10.0, None, hv=0.50)
        assert crushed["components"]["vrp"] == 0
        assert any("crushed vol" in b for b in crushed["blocking"])
