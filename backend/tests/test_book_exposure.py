"""Unit tests for the DETERMINISTIC Book-Exposure engine.

Network paths (yfinance download, live option chains) aren't exercised; these lock the pure math the
user must be able to trust: candidate-leg normalization, correlation, β-weighted directional delta,
the full-reprice scenario table (book vs book+trade), and the rule-based findings — especially that a
SAME-NAME position is flagged loudly and that correlations are only asserted, never invented.
"""
import json

import numpy as np

import app.services.book_exposure_service as be
import app.services.correlated_assets_service as cas


# ── Serialization guard: numpy scalars (from the reused book_tail_risk math) must be converted to
# native Python or FastAPI raises "'numpy.bool' object is not iterable". ──────
def test_native_strips_numpy_for_json():
    payload = be._native({
        "adds_to_direction": (np.float64(500) * np.float64(800) > 0 and abs(np.float64(800)) > abs(np.float64(500))),
        "delta": np.float64(123.4), "count": np.int64(3),
        "scenarios": [{"with_pnl": np.float64(-1000.0)}],
    })
    assert isinstance(payload["adds_to_direction"], bool)          # was numpy.bool_
    assert isinstance(payload["delta"], float) and isinstance(payload["count"], int)
    json.dumps(payload)                                            # must not raise


# ── Candidate legs ──────────────────────────────────────────────────────────
def test_candidate_legs_from_explicit_legs():
    legs = be._candidate_legs({
        "ticker": "AAPL", "structure": "naked_call", "contracts": 2,
        "legs": [{"action": "SELL", "type": "CALL", "strike": 250, "expiration": "2026-10-16", "iv": 0.25}],
    })
    assert legs == [{"type": "call", "action": "SELL", "strike": 250.0,
                     "expiration": "2026-10-16", "qty": 2.0, "iv": 0.25}]


def test_candidate_legs_fallback_from_structure():
    # No legs passed → reconstruct a short call from short_strike + structure.
    legs = be._candidate_legs({"ticker": "AAPL", "structure": "naked_call",
                               "short_strike": 250, "expiration": "2026-10-16", "contracts": 1})
    assert len(legs) == 1 and legs[0]["type"] == "call" and legs[0]["action"] == "SELL"
    # Vertical: short + long
    spread = be._candidate_legs({"ticker": "AAPL", "structure": "call_credit_spread",
                                 "short_strike": 250, "long_strike": 260, "expiration": "2026-10-16"})
    assert len(spread) == 2 and {l["strike"] for l in spread} == {250.0, 260.0}


# ── Correlation ─────────────────────────────────────────────────────────────
def test_pearson_perfect_inverse_and_min_window():
    a = [0.01, -0.02, 0.03, -0.01, 0.02, 0.0, -0.015, 0.025] * 6   # 48 pts
    assert cas.pearson(a, a) == 1.0
    assert cas.pearson(a, [-x for x in a]) == -1.0
    assert cas.pearson(a[:20], a[:20]) is None      # under the 40-point 1y floor


# ── Directional delta ($/1% SPY) ────────────────────────────────────────────
def test_spy_delta_sign_and_scale():
    # net long 50 deltas on a $100 name, β1 → +$50 per 1% move.
    pos = [{"ticker": "X", "net_delta": 50.0, "beta": 1.0, "spot": 100.0}]
    assert be._spy_delta_per_pct(pos) == 50.0
    # short call (negative delta) → negative $/1%.
    short = [{"ticker": "Y", "net_delta": -30.0, "beta": 1.2, "spot": 200.0}]
    assert be._spy_delta_per_pct(short) < 0


# ── Scenario table: full reprice, book vs book+trade ────────────────────────
def _short_call_pos(spot=100.0, strike=110.0):
    return {"ticker": "Z", "spot": spot, "beta": 1.0,
            "net_delta": -40.0, "net_gamma": 0.0, "net_vega": 0.0,
            "legs": [{"right": "C", "strike": strike, "sign": -1, "qty": 1.0,
                      "iv": 0.30, "dte_years": 30 / 365.0}]}


def test_scenario_table_short_call_directionality():
    cand = _short_call_pos()
    rows = be._scenario_table([], [cand], r=0.045)     # empty book → delta == candidate P&L
    by_move = {row["move_pct"]: row for row in rows}
    # short call GAINS on a big drop, LOSES on a big rally.
    assert by_move[-20]["with_pnl"] > 0
    assert by_move[20]["with_pnl"] < 0
    # empty book: book_pnl 0, delta == with.
    assert all(row["book_pnl"] == 0 for row in rows)
    assert all(row["delta_pnl"] == row["with_pnl"] for row in rows)


def test_scenario_delta_is_marginal_contribution():
    book = [_short_call_pos(strike=90.0)]
    cand = _short_call_pos(strike=110.0)
    rows = be._scenario_table(book, book + [cand], r=0.045)
    for row in rows:                                    # Δ == with − book, exactly
        assert row["delta_pnl"] == round((row["with_pnl"]) - (row["book_pnl"]))


# ── Book driver returns (macro weighting) ───────────────────────────────────
def test_book_driver_returns_weighted():
    base = [0.01, -0.02, 0.03, -0.01, 0.02, 0.0, -0.015, 0.025] * 6
    positions = [{"ticker": "A", "net_delta": 100.0, "beta": 1.0, "spot": 100.0},
                 {"ticker": "B", "net_delta": 10.0, "beta": 1.0, "spot": 100.0}]
    returns = {"A": base, "B": [-x for x in base]}
    agg = be._book_driver_returns(positions, returns)
    # A dominates the weight → aggregate correlates positively with A.
    assert agg is not None and cas.pearson(agg, base) > 0.5


# ── Same-name posture: a short put + short call is a STRANGLE, never a summed obligation ─────
def test_same_name_strangle_not_summed():
    same = [{"ticker": "AAPL", "structure": "cash_secured_put", "n_short": 1, "capital": 26000,
             "has_stock": False, "legs": [{"right": "P", "strike": 260, "sign": -1, "qty": 1, "dte_years": 45 / 365}]}]
    cand = {"ticker": "AAPL", "structure": "naked_call", "has_stock": False,
            "legs": [{"right": "C", "strike": 365, "sign": -1, "qty": 1, "dte_years": 45 / 365}]}
    a = be._same_name_analysis(same, cand, "AAPL")
    assert a["posture"] == "short_strangle"
    assert a["downside_outlay"] == 26000        # put side only — NOT 26000 + call notional
    assert a["upside_unbounded"] is True and a["lowest_naked_call"] == 365
    assert a["same_expiry"] is True


def test_same_name_stacked_calls():
    same = [{"ticker": "NVDA", "structure": "naked_call", "n_short": 1, "capital": 0, "has_stock": False,
             "legs": [{"right": "C", "strike": 140, "sign": -1, "qty": 1, "dte_years": 30 / 365}]}]
    cand = {"ticker": "NVDA", "structure": "naked_call", "has_stock": False,
            "legs": [{"right": "C", "strike": 150, "sign": -1, "qty": 1, "dte_years": 30 / 365}]}
    a = be._same_name_analysis(same, cand, "NVDA")
    assert a["posture"] == "stacked_calls" and a["downside_outlay"] is None


# ── Direction read straight off the reprise ±20% signs (plain, no jargon) ────
def test_direction_from_scenarios():
    assert be._direction_from_scenarios([{"move_pct": 20, "with_pnl": -100}, {"move_pct": -20, "with_pnl": 200}])[0] == "short"
    assert be._direction_from_scenarios([{"move_pct": 20, "with_pnl": 200}, {"move_pct": -20, "with_pnl": -100}])[0] == "long"
    assert be._direction_from_scenarios([{"move_pct": 20, "with_pnl": -50}, {"move_pct": -20, "with_pnl": -80}])[0] == "short_vol"


# ── _assess: verdict, plain key points, correlation labelling ───────────────
def test_assess_same_name_concentrates():
    a = be._same_name_analysis(
        [{"ticker": "AAPL", "structure": "cash_secured_put", "n_short": 1, "capital": 26000, "has_stock": False,
          "legs": [{"right": "P", "strike": 260, "sign": -1, "qty": 1, "dte_years": 45 / 365}]}],
        {"ticker": "AAPL", "structure": "naked_call", "has_stock": False,
         "legs": [{"right": "C", "strike": 365, "sign": -1, "qty": 1, "dte_years": 45 / 365}]}, "AAPL")
    scen = [{"move_pct": 20, "with_pnl": -3000}, {"move_pct": -20, "with_pnl": -1500}]
    v, h, kp, rec = be._assess({"ticker": "AAPL", "structure": "naked_call"}, {}, a, scen, "short_vol",
                               0.0, 0.0, 0.0, [], None, [], [], [])
    assert v == "concentrates" and "AAPL" in h
    assert any("short strangle" in k["text"] and "either way" in k["text"].lower() for k in kp)
    assert "26,000" in kp[0]["text"] and "62" not in kp[0]["text"]     # no summed 62.5k
    assert "defined-risk" in rec or "different" in rec


def test_assess_offset_diversifies():
    scen = [{"move_pct": 20, "with_pnl": 200}, {"move_pct": -20, "with_pnl": -100}]
    v, _, kp, _ = be._assess({"ticker": "X", "structure": "naked_call"}, {}, None, scen, "long",
                             500.0, 200.0, -300.0, [], None, [], [], [])   # book long, trade short → offset
    assert v == "diversifies" and any("OPPOSITE" in k["text"] for k in kp)


def test_assess_correlation_labeled():
    scen = [{"move_pct": 20, "with_pnl": 0}, {"move_pct": -20, "with_pnl": 0}]
    corr = [{"ticker": "HOOD", "rho": 0.78, "cluster": "Crypto / Bitcoin beta"}]
    v, _, kp, _ = be._assess({"ticker": "COIN", "structure": "cash_secured_put"}, {}, None, scen, "short_vol",
                             0.0, 0.0, 0.0, corr, None, [], [], [])
    assert any("HOOD" in k["text"] for k in kp)


def test_assess_theme_overlap_concentrates():
    # candidate shares the AI-data-center theme with two book names → theme key point + verdict.
    scen = [{"move_pct": 20, "with_pnl": 0}, {"move_pct": -20, "with_pnl": 0}]
    theme = [{"key": "ai_datacenter", "theme": "AI data-center buildout", "driver": "AI / data-center capex",
              "bellwether": "NVDA", "note": None, "book_tickers": ["AVGO", "NVDA"]}]
    v, h, kp, rec = be._assess({"ticker": "SMCI", "structure": "naked_call"}, {}, None, scen, "short_vol",
                               0.0, 0.0, 0.0, [], None, theme, [], [])
    assert v == "concentrates" and "AI data-center" in h
    assert any("AI data-center" in k["text"] and "AVGO" in k["text"] for k in kp)
    assert "AI data-center" in rec


def test_assess_related_earnings():
    scen = [{"move_pct": 20, "with_pnl": 0}, {"move_pct": -20, "with_pnl": 0}]
    earn = [{"bellwether": "NVDA", "theme": "AI data-center buildout", "date": "2026-09-10", "days_out": 9,
             "driver": "AI / data-center capex"}]
    v, _, kp, _ = be._assess({"ticker": "AVGO", "structure": "naked_call"}, {}, None, scen, "short_vol",
                             0.0, 0.0, 0.0, [], None, [], [], earn)
    assert any("NVDA reports in 9 days" in k["text"] for k in kp)


def test_macro_only_meaningful_and_aligned():
    # candidate & book BOTH strongly load the same factor, same sign → surfaced with a plain sentence.
    base = [0.01, -0.02, 0.03, -0.01, 0.02, 0.0, -0.015, 0.025] * 6
    positions = [{"ticker": "A", "net_delta": 100.0, "beta": 1.0, "spot": 100.0}]
    returns = {"COIN": base, "A": base, "TLT": base}   # only Rates proxy present → others skipped
    out = be._macro_overlap("COIN", positions, returns)
    assert {m["factor"] for m in out} == {"Interest rates"}
    assert out[0]["candidate_rho"] == 1.0 and out[0]["book_rho"] == 1.0 and "interest rates" in out[0]["plain"]


def test_macro_drops_opposite_sign():
    # candidate +corr, book −corr to the SAME proxy → not a shared exposure → dropped (c*b ≤ 0).
    base = [0.01, -0.02, 0.03, -0.01, 0.02, 0.0, -0.015, 0.025] * 6
    positions = [{"ticker": "A", "net_delta": -100.0, "beta": 1.0, "spot": 100.0}]  # short → book_driver = −base
    returns = {"COIN": base, "A": base, "TLT": base}
    assert be._macro_overlap("COIN", positions, returns) == []
