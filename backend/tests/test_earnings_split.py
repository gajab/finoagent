"""An earnings print inside the window is priced ONCE, at the market's own size.

Seen live (UAL 120C 2026-11-20, print 2026-10-20 AMC): the physical law added the diffusion forecast σ_P²T (50.5%)
AND the scan's isolated event move on top, while the market's 49.4% ATM vol already contained the print — the
event was counted twice and the trade read EV −$23.5 instead of ≈ −$11. Three fixes:

  1. The event comes from the TERM STRUCTURE — two expiries that both contain the print split the market's
     variance into diffusion + event (σd² = (IV₂²T₂ − IV₁²T₁)/(T₂ − T₁), e² = IV₁²T₁ − σd²T₁) — so the physical
     law is σ_P²T + e²: the desk's diffusion view against the market's diffusion, the event at the market's size.
  2. When the market's variance can't be split, the physical variance is max(market total, σ_P²T): no edge is
     claimed on an event we can't separate, but a diffusion forecast above everything priced is still −VRP.
  3. The straddle-implied move is in consistent σ units: a straddle prices E|ΔS| = √(2/π)·σ√T, not σ√T.
"""

import math
from datetime import date, timedelta
from types import SimpleNamespace

import pytest

import app.services.lifecycle_service as L
from app.services.derivative_income_service import _EARN_GAP_MIN_FRAC, _earnings_implied_move_pct, _straddle_iv
from app.services.desk_review_service import (
    _TS_SPLIT_MIN_GAP_DAYS,
    _earnings_in_window,
    _event_split_from_term,
    _opp_desk_metrics,
)
from app.services.stock_service import bs_price

S, R, DTE = 100.0, 0.05, 31
T = DTE / 365.0
TODAY = date(2026, 10, 20)


# ── 3. straddle units ────────────────────────────────────────────────────────────────────────────────────────
class TestStraddleUnits:
    @pytest.mark.parametrize("sd_diff,e_sd", [(0.40, 0.08), (0.25, 0.05), (0.60, 0.12)])
    def test_recovers_the_event_move_from_a_straddle(self, sd_diff, e_sd):
        # A straddle priced at total variance σd²T + e², with the diffusion vol known: the isolated event comes back
        # as its expected ABSOLUTE move (√(2/π)·e) — not the ~half-size number the 0.8σ-vs-1σ subtraction gave.
        tot = math.sqrt(sd_diff ** 2 + e_sd ** 2 / T)
        c, p = bs_price(S, S, T, 0.0, tot, "call"), bs_price(S, S, T, 0.0, tot, "put")
        quotes = {S: SimpleNamespace(mid=c)}, {S: SimpleNamespace(mid=p)}
        got = _earnings_implied_move_pct(*quotes, S, DTE, sd_diff)
        assert got == pytest.approx(math.sqrt(2 / math.pi) * e_sd, rel=0.03)

    def test_no_event_hits_the_floor_not_zero(self):
        c, p = bs_price(S, S, T, 0.0, 0.30, "call"), bs_price(S, S, T, 0.0, 0.30, "put")
        got = _earnings_implied_move_pct({S: SimpleNamespace(mid=c)}, {S: SimpleNamespace(mid=p)}, S, DTE, 0.30)
        assert got == pytest.approx(_EARN_GAP_MIN_FRAC * (c + p) / S, rel=1e-6)


# ── 1. the market's own diffusion / event split ──────────────────────────────────────────────────────────────
def _ts(sd, e, f=31, b=59):
    iv = lambda d: math.sqrt(sd ** 2 + e ** 2 / (d / 365.0)) * 100
    return {"front_dte": f, "back_dte": b, "front_straddle_iv_pct": iv(f), "back_straddle_iv_pct": iv(b)}


def _chain(sigma, dte, ks=(95.0, 100.0, 105.0), r=0.045, half=0.03):
    T = dte / 365.0
    q = lambda px: SimpleNamespace(mid=px, bid=px * (1 - half), ask=px * (1 + half))
    return ({k: q(bs_price(S, k, T, r, sigma, "call")) for k in ks},
            {k: q(bs_price(S, k, T, r, sigma, "put")) for k in ks})


class TestStraddleIv:
    @pytest.mark.parametrize("sigma,dte", [(0.30, 31), (0.55, 59), (0.18, 10)])
    def test_inverts_the_straddle_mid(self, sigma, dte):
        assert _straddle_iv(*_chain(sigma, dte), S, dte, 0.045) == pytest.approx(sigma, abs=1e-4)

    def test_skips_one_sided_and_garbage_quotes(self):
        calls, puts = _chain(0.30, 31)
        puts[100.0].bid = 0.0                                  # nearest strike one-sided → next strikes used
        assert _straddle_iv(calls, puts, S, 31, 0.045) == pytest.approx(0.30, abs=1e-4)
        dead = {k: SimpleNamespace(mid=0.001, bid=0.001, ask=0.001) for k in calls}
        assert _straddle_iv(dead, dead, S, 31, 0.045) is None   # below intrinsic → no vol fits


class TestTermStructureSplit:
    @pytest.mark.parametrize("sd,e", [(0.40, 0.06), (0.25, 0.10), (0.55, 0.03)])
    def test_recovers_known_diffusion_and_event(self, sd, e):
        r = _event_split_from_term(_ts(sd, e), "2026-10-21", TODAY)
        assert r["diffusion_vol"] == pytest.approx(sd, rel=1e-9)
        assert math.sqrt(r["event_var"]) == pytest.approx(e, rel=1e-9)

    def test_flat_curve_means_no_event_premium(self):
        ts = {"front_dte": 31, "back_dte": 59, "front_straddle_iv_pct": 40.0, "back_straddle_iv_pct": 40.0}
        r = _event_split_from_term(ts, "2026-10-21", TODAY)
        assert r["event_var"] == 0.0 and r["diffusion_vol"] == pytest.approx(0.40)

    def test_cannot_split(self):
        ok = _ts(0.40, 0.06)
        assert _event_split_from_term(ok, None, TODAY) is None                              # no print known
        assert _event_split_from_term(ok, "2026-10-19", TODAY) is None                      # print already past
        assert _event_split_from_term(ok, (TODAY + timedelta(days=40)).isoformat(), TODAY) is None   # after front
        close = _ts(0.40, 0.06, f=31, b=31 + _TS_SPLIT_MIN_GAP_DAYS - 1)
        assert _event_split_from_term(close, "2026-10-21", TODAY) is None                   # expiries too close
        steep = {"front_dte": 31, "back_dte": 59, "front_straddle_iv_pct": 80.0, "back_straddle_iv_pct": 30.0}
        assert _event_split_from_term(steep, "2026-10-21", TODAY) is None                   # no diffusion fits
        assert _event_split_from_term({"state": None}, "2026-10-21", TODAY) is None         # one expiry only
        mixed = {"front_dte": 31, "back_dte": 59, "front_iv_pct": 45.0, "back_iv_pct": 41.0}
        assert _event_split_from_term(mixed, "2026-10-21", TODAY) is None   # never from mixed ATM-IV estimators

    def test_print_in_window(self):
        opp = {"expiration": "2026-11-20", "legs": [{"expiration": "2026-11-20"}]}
        assert _earnings_in_window(opp, "2026-10-20", TODAY)                     # tonight counts
        assert _earnings_in_window(opp, "2026-11-20", TODAY)
        assert not _earnings_in_window(opp, "2026-11-21", TODAY)
        assert not _earnings_in_window(opp, "2026-10-19", TODAY)
        assert not _earnings_in_window(opp, None, TODAY)
        cal = {"expiration": "2026-12-18", "legs": [{"expiration": "2026-11-20"}, {"expiration": "2026-12-18"}]}
        assert not _earnings_in_window(cal, "2026-12-01", TODAY)                 # the NEAREST leg's expiry


# ── 2. the physical law: event at the market's size, or max(market, diffusion) ───────────────────────────────
def _put(phys, iv, K=90.0, **kw):
    prem = bs_price(S, K, T, R, iv, "put")
    legs = [{"strike": K, "right": "P", "sign": -1, "qty": 1, "price": prem, "iv": iv * 100, "dte_years": T}]
    return L.compute_pretrade_metrics(legs, S, L.terminal_payoff_curve(legs, 0.0, S), K * 20, -(K - prem) * 100,
                                      prem * 100, iv, DTE, r=R, realized_vol=phys, atm_iv=iv, **kw)


class TestPhysicalLawWithAPrint:
    def test_fallback_claims_no_edge_on_an_unsplit_event(self):
        # IV 45% carries a print; the diffusion forecast is 30%. Diffusion-only would read the event premium as a
        # big edge; the fallback holds the law at the market's (fair at mid ⇒ EV ≈ 0).
        fake = _put(0.30, 0.45)
        held = _put(0.30, 0.45, event_fallback=True)
        assert fake["pm"]["expected_value"] > 20
        assert abs(held["pm"]["expected_value"]) < 1.0
        assert held["distribution"]["measure"] == "market"

    def test_fallback_still_sees_a_diffusion_forecast_above_everything_priced(self):
        wide = _put(0.55, 0.45, event_fallback=True)
        assert wide["distribution"]["measure"] == "physical_wider"
        assert wide["pm"]["expected_value"] == pytest.approx(_put(0.55, 0.45)["pm"]["expected_value"], abs=1e-6)
        assert wide["pm"]["expected_value"] < -10

    def test_split_event_prices_the_diffusion_view_only(self):
        # Market: diffusion 40% + a 6% event. Desk forecast = the market's diffusion → fair; a richer forecast →
        # negative EV from the DIFFUSION gap alone (the event is not counted twice).
        sd, e = 0.40, 0.06
        iv = math.sqrt(sd ** 2 + e ** 2 / T)
        fair = _put(sd, iv, event_var=e ** 2)
        assert abs(fair["pm"]["expected_value"]) < 1.0
        double = _put(sd, iv, event_var=e ** 2 + (math.sqrt(2 / math.pi) * e) ** 2)      # the old double count
        assert double["pm"]["expected_value"] < fair["pm"]["expected_value"] - 5


# ── wiring: the desk uses the split / fallback, never the scan's straddle move ───────────────────────────────
def _opp(iv, gap_pct, K=90.0):
    prem = bs_price(S, K, T, R, iv, "put")
    return {"structure": "cash_secured_put", "dte": DTE, "atm_iv_pct": iv * 100, "expiration": "2026-11-20",
            "max_loss": -(K - prem) * 100, "max_profit": prem * 100, "collateral": K * 100, "short_strike": K,
            "earnings_gap_pct": gap_pct,
            "legs": [{"action": "SELL", "type": "PUT", "strike": K, "mid": prem, "iv": iv * 100,
                      "expiration": "2026-11-20", "delta": -0.12, "bid_ask_spread_pct": 3.0}]}


class TestDeskWiring:
    sd, e = 0.40, 0.06
    iv = math.sqrt(sd ** 2 + e ** 2 / T)
    split = {"event_var": e ** 2, "diffusion_vol": sd}

    def _ev(self, opp, **kw):
        return _opp_desk_metrics(opp, S, 4.3, self.sd, **kw)["pm"]["expected_value"]

    def test_split_ignores_the_scan_straddle_move(self):
        a = self._ev(_opp(self.iv, 9.0), earnings_in_window=True, event_split=self.split)
        b = self._ev(_opp(self.iv, 2.0), earnings_in_window=True, event_split=self.split)
        assert a == pytest.approx(b, abs=1e-9) and abs(a) < 1.0

    def test_unsplit_print_falls_back_to_the_market(self):
        assert abs(self._ev(_opp(self.iv, 9.0), earnings_in_window=True, event_split=None)) < 1.0

    def test_print_outside_the_window_adds_nothing(self):
        # No print in the window: the desk's 40% diffusion vs the 41%+ market reads as a (diffusion) edge.
        out = self._ev(_opp(self.iv, 9.0), earnings_in_window=False, event_split=self.split)
        assert out > 2.0

    def test_market_variance_is_centred_on_the_straddle_vol(self):
        # The scan's ATM IV sits 3 vol points under the mids (COF 2026-10: 32.8% vs straddle 35.3%). Centred there,
        # a fair trade reads as a wider physical law (fake −EV); centred on the straddle vol it is fair again.
        opp = _opp(0.35, None)
        opp["atm_iv_pct"] = 32.0
        off = _opp_desk_metrics(opp, S, 4.3, 0.35)
        on = _opp_desk_metrics(opp, S, 4.3, 0.35, atm_iv=0.35)
        assert off["distribution"]["measure"] == "physical_wider" and off["pm"]["expected_value"] < -2.0
        assert abs(on["pm"]["expected_value"]) < 1.0
