"""ONE source of truth for the quant hold/close score.

Observed on one card: Quant Analysis "53/100 STRONG CLOSE" (override: ≥90% captured) next to the Trade Manager's
"Quant desk (hold read): STRONG CLOSE 4/100". 53 sits in the 45–68 HOLD band, so the label contradicted its own number;
and the Trade Manager was handed the *light* read (with the −50 profit cliff) because the UI never passed it the deep one.

Rules pinned here:
  1. A hard override that demotes the signal also caps the SCORE at the top of that signal's band — the number never
     contradicts the label (same ceilings the Trade Manager's quant hard-stop already used: one definition).
  2. The pre-override points are kept (`raw_score`) so the cap is auditable.
  3. A position with an unpriced option leg is never judged on a $0 mark: the Trade Manager says so, drops the quant
     lens (rather than scoring a made-up value) and cannot be "high" confidence."""
import pytest

from app.services import trade_manager_service as TM
from app.services.lifecycle_service import (
    SIGNAL_SCORE_CEILING, clamp_score_to_signal, lifecycle_overlay, management_desk_score, management_exit,
)

# band cut-points shared by the quant desk and the Trade Manager
CUTS = (68, 45, 28)


def _band(score: float) -> str:
    return ("STRONG_HOLD" if score >= CUTS[0] else "HOLD" if score >= CUTS[1] else "CLOSE" if score >= CUTS[2] else "STRONG_CLOSE")


def _desk(captured, **kw):
    base = dict(keep_drift_pct=88.0, keep_standard_pct=88.0, subscores=None, grade_adjustments=[], ta_factors=[],
                captured_pct=captured, dte_days=46, unrealized_pnl=29.0, max_profit=65.0, max_loss=-5935.0, cushion_pct=38.0,
                structure="covered_call", omega=1.4, sortino=0.9, cvar95=300.0, capital=6000.0, covered=True)
    base.update(kw)
    return management_desk_score(**base)


# ── 1/2 · the number agrees with the label ──────────────────────────────────────────────────────

def test_ceilings_are_the_top_of_each_band():
    assert SIGNAL_SCORE_CEILING == {"HOLD": CUTS[0] - 1, "CLOSE": CUTS[1] - 1, "STRONG_CLOSE": CUTS[2] - 1}
    assert [clamp_score_to_signal(53, s) for s in ("STRONG_HOLD", "HOLD", "CLOSE", "STRONG_CLOSE")] == [53, 53, 44, 27]
    assert clamp_score_to_signal(90, "STRONG_HOLD") == 90 and clamp_score_to_signal(80, "HOLD") == 67
    assert clamp_score_to_signal(10, "STRONG_CLOSE") == 10                       # never raises a score


def test_deep_desk_override_no_longer_shows_a_hold_band_score_with_a_close_label():
    r = _desk(captured=100.0)
    assert r["signal"] == "STRONG_CLOSE" and r["overrides"]
    assert r["raw_score"] >= 45, "the unclamped points were in the HOLD band — the exact 53/100 STRONG CLOSE case"
    assert r["score"] <= 27 and _band(r["score"]) == r["signal"]


def test_no_override_leaves_the_score_untouched():
    r = _desk(captured=44.6)                                                       # the card's REAL capture
    assert not r["overrides"] and r["score"] == r["raw_score"] and _band(r["score"]) == r["signal"]


def test_light_overlay_obeys_the_same_rule():
    o = lifecycle_overlay(80.0, 95.0, 46, 60.0, -1000.0)                           # ≥85% captured → STRONG_CLOSE override
    assert o["signal"] == "STRONG_CLOSE" and _band(o["score"]) == "STRONG_CLOSE" and o["raw_score"] > o["score"]
    q = management_exit(pop_pct=92.0, captured_pct=95.0, dte_days=46, unrealized_pnl=60.0, max_profit=65.0, max_loss=-5935.0)
    assert _band(q["score"]) == q["signal"]


@pytest.mark.parametrize("captured", [None, -50, 0, 20, 45, 60, 76, 90, 100])
@pytest.mark.parametrize("dte", [1, 5, 30, 90])
@pytest.mark.parametrize("cushion", [-1.0, 3.0, 38.0])
def test_score_and_signal_never_disagree_across_the_input_space(captured, dte, cushion):
    r = _desk(captured=captured, dte_days=dte, cushion_pct=cushion, unrealized_pnl=captured or 0.0)
    assert _band(r["score"]) == r["signal"], (captured, dte, cushion, r["score"], r["raw_score"], r["signal"], r["overrides"])
    o = lifecycle_overlay(float(r["raw_score"]), captured, dte, captured or 0.0, -1000.0)
    assert _band(o["score"]) == o["signal"]


def test_trade_manager_hard_stop_uses_the_same_ceilings():
    # the quant lens's STRONG_CLOSE floors the blended verdict at the shared ceiling (27), CLOSE at 44
    src = open(TM.__file__).read()
    assert "SIGNAL_SCORE_CEILING" in src and "{2: 44.0, 3: 27.0}" not in src


# ── 3 · the Trade Manager and an unpriced leg ───────────────────────────────────────────────────

PCS = [{"action": "SELL", "type": "put", "strike": 90.0, "expiration": "2099-01-01", "qty": 1},
       {"action": "BUY", "type": "put", "strike": 85.0, "expiration": "2099-01-01", "qty": 1}]


def _strategy():
    return {"ticker": "TEST", "name": "t", "strategy_type": "put_credit_spread", "legs_data": PCS, "parameters": {}, "notes": ""}


def _gap_pnl(spot):
    """What live-pnl now returns when a leg is unpriced: null P&L, pricing_complete False, no quant_exit."""
    return {"underlying_price": spot, "unrealized_pnl": None, "pnl_pct": None, "entry_cost": -300.0, "current_value": None,
            "pricing_complete": False, "unpriced_legs": [0], "pricing_warning": "No live quote for 1 of 2 leg(s) — P&L is not computed",
            "net_greeks": {"delta": 20.0, "gamma": -0.5, "theta": 6.0, "vega": -9.0}, "max_profit": 300.0, "max_loss": -700.0,
            "breakevens": [spot * 0.9], "days_held": 12,
            "analysis": {"dte_remaining": 24, "captured_pct": None, "quant_exit": None, "exit_signal": None, "verdict_withheld": True}}


def _ev(spot=100.0):
    from test_trade_manager import _ev as base
    return base("up")


def test_trade_manager_flags_the_gap_and_drops_the_quant_lens():
    ev = _ev()
    prof = TM.build_profile(_strategy(), _gap_pnl(ev["spot"]))
    assert prof["quote_gap"] and prof["quote_gap"]["unpriced_legs"] == [0]
    d = TM.decide(prof, ev)
    assert d["quote_gap"] and d["lenses"]["quant"]["available"] is False and d["weights"]["quant"] == 0.0
    assert d["lenses"]["quant"]["source"] == "fallback"
    assert any("no live quote" in n.lower() and "never valued at $0" in n for n in d["lenses"]["quant"]["notes"])
    assert any("provisional" in c for c in d["conflicts"])
    assert d["confidence"] == "low" and d["conviction"] <= 40


def test_a_priced_position_is_unaffected():
    from test_trade_manager import _pnl
    ev = _ev()
    prof = TM.build_profile(_strategy(), _pnl(ev["spot"]))
    d = TM.decide(prof, ev)
    assert prof["quote_gap"] is None and d["quote_gap"] is None and d["lenses"]["quant"]["available"] is True
    assert not any("provisional" in c for c in d["conflicts"])
