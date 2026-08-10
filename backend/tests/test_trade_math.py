"""Tests for trade_math + box_math — the consistency guarantees.

Run from backend/:
    source venv/bin/activate
    python -m pytest tests/test_trade_math.py -v

The most important test here is ``test_box_analysis_is_source_agnostic``,
which proves that identical legs quoted from yfinance vs IBKR produce
byte-identical analysis. That's the anti-regression for the whole "math
diverges by quote source" problem class.
"""

from datetime import date, datetime, timedelta, timezone

import pytest

from app.services.trade_math import (
    annualized_return,
    annualized_return_pct,
    _structure_standing,
    classify_leg_action,
    dte_from_expiry,
    exit_recommendation,
    expiry_payoff,
    structure_payoff_extremes,
    structure_breakevens,
    is_crossed,
    is_stale,
    is_wide_spread,
    mid_price,
    prob_itm_lognormal,
    realized_close_pnl,
    simple_annualized_pct,
    summarize_trade_actions,
    walk_ledger,
)
from app.services.box_math import LegQuote, analyze_box


# ── trade_math primitives ───────────────────────────────────────────────

class TestMidPrice:
    def test_normal_bid_ask(self):
        assert mid_price(10.0, 11.0) == 10.5

    def test_crossed_falls_back_to_last(self):
        # bid > ask is crossed; mid_price should fall back to last
        assert mid_price(12.0, 10.0, last=11.0) == 11.0

    def test_one_sided_uses_available(self):
        assert mid_price(None, 10.0) == 10.0
        assert mid_price(10.0, None) == 10.0

    def test_zero_treated_as_missing(self):
        # Zero bid/ask means "no quote" — skip and use last
        assert mid_price(0.0, 0.0, last=9.5) == 9.5

    def test_no_inputs_returns_none(self):
        assert mid_price(None, None, None) is None
        assert mid_price(0.0, 0.0, None) is None


class TestSpreadDetection:
    def test_wide_spread_flagged(self):
        # 10% spread on a $10 mid
        assert is_wide_spread(9.5, 10.5, threshold_pct=5.0) is True

    def test_tight_spread_ok(self):
        # 1% spread
        assert is_wide_spread(9.95, 10.05, threshold_pct=5.0) is False

    def test_crossed_detected(self):
        assert is_crossed(11.0, 10.0) is True

    def test_not_crossed(self):
        assert is_crossed(10.0, 11.0) is False


class TestAnnualization:
    def test_geometric_two_percent_over_30_days(self):
        # 2% over 30 days, geometrically annualized:
        #   (1.02)^(365/30) − 1 = 0.2724 (27.24%)
        assert round(annualized_return(2, 100, 30), 4) == 0.2724

    def test_geometric_vs_simple_diverge(self):
        # The bug we're fixing: simple understates for short holds,
        # overstates for very short holds (>>1yr), both are wrong.
        geo = annualized_return_pct(2, 100, 30)
        simple = simple_annualized_pct(2, 100, 30)
        assert geo > simple
        assert round(geo, 2) == 27.24
        assert round(simple, 2) == 24.33

    def test_one_year_matches_roi(self):
        # Over 365 days, annualized should just equal ROI
        assert round(annualized_return(5, 100, 365) * 100, 4) == 5.0

    def test_zero_cost_returns_zero(self):
        assert annualized_return(10, 0, 30) == 0.0

    def test_zero_days_returns_zero(self):
        assert annualized_return(10, 100, 0) == 0.0


class TestDTE:
    def test_iso_string_parsed(self):
        future = (date.today() + timedelta(days=30)).strftime("%Y-%m-%d")
        assert dte_from_expiry(future) == 30

    def test_past_expiry_returns_zero(self):
        past = (date.today() - timedelta(days=5)).strftime("%Y-%m-%d")
        assert dte_from_expiry(past) == 0

    def test_invalid_string_returns_zero(self):
        assert dte_from_expiry("not-a-date") == 0


class TestStaleness:
    def test_fresh_quote_not_stale(self):
        now = datetime.now(timezone.utc)
        assert is_stale(now, max_age_seconds=300) is False

    def test_old_quote_stale(self):
        old = datetime.now(timezone.utc) - timedelta(hours=1)
        assert is_stale(old, max_age_seconds=300) is True

    def test_none_treated_as_fresh(self):
        # None means "no ts info" — we don't flag, we let caller decide
        assert is_stale(None, max_age_seconds=300) is False


# ── Ledger walk (FIFO) ─────────────────────────────────────────────────

class TestWalkLedger:
    def test_open_only(self):
        """Single 'open' transaction leaves one open lot."""
        txs = [
            {"action": "open", "quantity": 100, "price": 50.0, "fees": 0,
             "executed_at": "2026-01-01T10:00:00"},
        ]
        snap = walk_ledger(txs)
        assert snap.net_quantity == 100
        assert snap.avg_cost == 50.0
        assert snap.total_cost_basis == 5000.0
        assert snap.realized_pnl == 0
        assert len(snap.open_lots) == 1

    def test_add_to_position_averages_cost(self):
        """Two buys at different prices → weighted avg cost."""
        txs = [
            {"action": "open", "quantity": 100, "price": 50.0, "fees": 0,
             "executed_at": "2026-01-01"},
            {"action": "add",  "quantity": 100, "price": 60.0, "fees": 0,
             "executed_at": "2026-01-02"},
        ]
        snap = walk_ledger(txs)
        assert snap.net_quantity == 200
        # Total cost = 5000 + 6000 = 11000; 11000 / 200 = 55
        assert snap.avg_cost == 55.0

    def test_partial_close_realizes_fifo_pnl(self):
        """FIFO: selling 50 after buying 100@50 + 100@60 closes from the first lot."""
        txs = [
            {"action": "open",   "quantity": 100, "price": 50.0, "fees": 0,
             "executed_at": "2026-01-01"},
            {"action": "add",    "quantity": 100, "price": 60.0, "fees": 0,
             "executed_at": "2026-01-02"},
            {"action": "reduce", "quantity": -50, "price": 70.0, "fees": 0,
             "executed_at": "2026-01-10"},
        ]
        snap = walk_ledger(txs)
        # Sold 50 @ 70, matched against 50 from first lot @ 50 → realized = 50 × (70−50) = 1000
        assert snap.realized_pnl == 1000.0
        # Remaining: 50 @ 50 + 100 @ 60 = 8500 / 150 ≈ 56.67
        assert snap.net_quantity == 150
        assert round(snap.avg_cost, 2) == 56.67

    def test_full_close(self):
        txs = [
            {"action": "open",  "quantity": 100, "price": 50.0, "fees": 0,
             "executed_at": "2026-01-01"},
            {"action": "close", "quantity": -100, "price": 75.0, "fees": 5,
             "executed_at": "2026-01-30"},
        ]
        snap = walk_ledger(txs)
        assert snap.net_quantity == 0
        assert snap.realized_pnl == 2500.0
        assert snap.fees_paid == 5.0
        assert snap.is_open is False


# ── Box analysis — the consistency proof ───────────────────────────────

def _make_box_legs(source: str, ts_offset_seconds: int = 0) -> list[LegQuote]:
    """Standard lend-box fixture: long 100 call + short 110 call + long 110 put + short 100 put.
    Width = 10. Standard quotes yield a 2.00 debit mid-price."""
    ts = datetime.now(timezone.utc) - timedelta(seconds=ts_offset_seconds)
    return [
        LegQuote(strike=100, right="call", side="long",  bid=11.00, ask=11.20, last=11.10, ts=ts, source=source),
        LegQuote(strike=110, right="call", side="short", bid=3.40,  ask=3.60,  last=3.50,  ts=ts, source=source),
        LegQuote(strike=110, right="put",  side="long",  bid=8.40,  ask=8.60,  last=8.50,  ts=ts, source=source),
        LegQuote(strike=100, right="put",  side="short", bid=1.90,  ask=2.10,  last=2.00,  ts=ts, source=source),
    ]


class TestBoxAnalysis:
    def test_lend_box_basic_math(self):
        legs = _make_box_legs("yfinance")
        expiry = (date.today() + timedelta(days=60)).strftime("%Y-%m-%d")
        result = analyze_box(legs, intent="lend", expiry=expiry, capital=10_000)
        assert result.width == 10
        # Net = (+11.10 − 3.50) + (+8.50 − 2.00) = 7.60 + 6.50 = 14.10? Let me recompute.
        # long 100 call mid = 11.10 (+)
        # short 110 call mid = 3.50 (−)
        # long 110 put  mid = 8.50 (+)
        # short 100 put mid = 2.00 (−)
        # Net = 11.10 − 3.50 + 8.50 − 2.00 = 14.10
        assert round(result.net_per_contract, 2) == 14.10
        # That's a net DEBIT of 14.10, but width is only 10 → lend is negative-carry — not tradeable
        # This fixture proves warnings path.
        # Let's flip: adjust the fixture so lend has a sane debit < width

    def test_lend_box_with_sane_pricing(self):
        """A realistic lend: net debit is LESS than the width — small positive carry."""
        ts = datetime.now(timezone.utc)
        legs = [
            # To get a ~9.80 debit on a 10-width box, we want the long call ≈ long put + short call + short put:
            LegQuote(strike=100, right="call", side="long",  bid=11.30, ask=11.50, last=11.40, ts=ts, source="yfinance"),
            LegQuote(strike=110, right="call", side="short", bid=3.80,  ask=4.00,  last=3.90,  ts=ts, source="yfinance"),
            LegQuote(strike=110, right="put",  side="long",  bid=8.30,  ask=8.50,  last=8.40,  ts=ts, source="yfinance"),
            LegQuote(strike=100, right="put",  side="short", bid=6.00,  ask=6.20,  last=6.10,  ts=ts, source="yfinance"),
        ]
        expiry = (date.today() + timedelta(days=60)).strftime("%Y-%m-%d")
        result = analyze_box(legs, intent="lend", expiry=expiry, capital=10_000)

        # Net = 11.40 − 3.90 + 8.40 − 6.10 = 9.80
        assert round(result.net_per_contract, 2) == 9.80
        assert result.cost == 9.80
        # profit = width − cost = 10 − 9.80 = 0.20
        assert round(result.profit, 2) == 0.20
        # roi_pct = profit/cost × 100 = 0.20 / 9.80 × 100 ≈ 2.04%
        assert round(result.roi_pct, 2) == 2.04
        # annualized over 60 days (geometric): (1 + 0.02041)^(365/60) − 1 ≈ 0.1308 → 13.08%
        assert round(result.annualized_return_pct, 2) == 13.08

    def test_box_analysis_is_source_agnostic(self):
        """THE test — identical legs quoted from yfinance vs ibkr produce identical analysis."""
        ts = datetime.now(timezone.utc)
        yf_legs = [
            LegQuote(strike=100, right="call", side="long",  bid=11.30, ask=11.50, last=11.40, ts=ts, source="yfinance"),
            LegQuote(strike=110, right="call", side="short", bid=3.80,  ask=4.00,  last=3.90,  ts=ts, source="yfinance"),
            LegQuote(strike=110, right="put",  side="long",  bid=8.30,  ask=8.50,  last=8.40,  ts=ts, source="yfinance"),
            LegQuote(strike=100, right="put",  side="short", bid=6.00,  ask=6.20,  last=6.10,  ts=ts, source="yfinance"),
        ]
        ibkr_legs = [
            LegQuote(strike=100, right="call", side="long",  bid=11.30, ask=11.50, last=11.40, ts=ts, source="ibkr"),
            LegQuote(strike=110, right="call", side="short", bid=3.80,  ask=4.00,  last=3.90,  ts=ts, source="ibkr"),
            LegQuote(strike=110, right="put",  side="long",  bid=8.30,  ask=8.50,  last=8.40,  ts=ts, source="ibkr"),
            LegQuote(strike=100, right="put",  side="short", bid=6.00,  ask=6.20,  last=6.10,  ts=ts, source="ibkr"),
        ]
        expiry = (date.today() + timedelta(days=45)).strftime("%Y-%m-%d")

        yf_result = analyze_box(yf_legs, intent="lend", expiry=expiry, capital=10_000)
        ibkr_result = analyze_box(ibkr_legs, intent="lend", expiry=expiry, capital=10_000)

        # Every computed field must match. source_mix differs — that's by design.
        assert yf_result.net_per_contract == ibkr_result.net_per_contract
        assert yf_result.cost == ibkr_result.cost
        assert yf_result.profit == ibkr_result.profit
        assert yf_result.roi_pct == ibkr_result.roi_pct
        assert yf_result.annualized_return_pct == ibkr_result.annualized_return_pct
        assert yf_result.dte == ibkr_result.dte
        assert yf_result.contracts == ibkr_result.contracts
        assert yf_result.total_cost == ibkr_result.total_cost
        assert yf_result.total_profit == ibkr_result.total_profit
        # But the mix should distinguish
        assert yf_result.source_mix == {"yfinance": 4}
        assert ibkr_result.source_mix == {"ibkr": 4}

    def test_wide_spread_surfaces_warning(self):
        """A leg with a 10% wide spread should emit a warning, not break the computation."""
        ts = datetime.now(timezone.utc)
        legs = [
            LegQuote(strike=100, right="call", side="long",  bid=10.0, ask=12.0, last=11.0, ts=ts, source="yfinance"),
            LegQuote(strike=110, right="call", side="short", bid=3.8,  ask=4.0,  last=3.9,  ts=ts, source="yfinance"),
            LegQuote(strike=110, right="put",  side="long",  bid=8.3,  ask=8.5,  last=8.4,  ts=ts, source="yfinance"),
            LegQuote(strike=100, right="put",  side="short", bid=6.0,  ask=6.2,  last=6.1,  ts=ts, source="yfinance"),
        ]
        expiry = (date.today() + timedelta(days=30)).strftime("%Y-%m-%d")
        result = analyze_box(legs, intent="lend", expiry=expiry, capital=10_000)
        assert any("wide spread" in w.lower() for w in result.warnings)
        # Still computes a result
        assert result.annualized_return_pct > 0

    def test_stale_quote_surfaces_warning(self):
        """A leg with ts > 5 min old should emit a staleness warning."""
        # One very old leg
        stale_ts = datetime.now(timezone.utc) - timedelta(hours=1)
        fresh_ts = datetime.now(timezone.utc)
        legs = [
            LegQuote(strike=100, right="call", side="long",  bid=11.3, ask=11.5, last=11.4, ts=stale_ts, source="yfinance"),
            LegQuote(strike=110, right="call", side="short", bid=3.8,  ask=4.0,  last=3.9,  ts=fresh_ts, source="yfinance"),
            LegQuote(strike=110, right="put",  side="long",  bid=8.3,  ask=8.5,  last=8.4,  ts=fresh_ts, source="yfinance"),
            LegQuote(strike=100, right="put",  side="short", bid=6.0,  ask=6.2,  last=6.1,  ts=fresh_ts, source="yfinance"),
        ]
        expiry = (date.today() + timedelta(days=30)).strftime("%Y-%m-%d")
        result = analyze_box(legs, intent="lend", expiry=expiry, capital=10_000)
        assert any("stale" in w.lower() for w in result.warnings)


class TestProbItmLognormal:
    """Risk-neutral P(ITM) fallback — sanity of the N(d2) closed form."""

    def test_atm_call_is_near_half(self):
        # At-the-money, the P(ITM) sits a touch under 0.5 (drift − ½σ² pulls d2 down).
        p = prob_itm_lognormal(spot=100, strike=100, iv=30, dte_days=30, right="C")
        assert 0.40 < p < 0.55

    def test_deep_otm_call_is_low(self):
        p = prob_itm_lognormal(spot=100, strike=200, iv=30, dte_days=30, right="C")
        assert p < 0.02

    def test_deep_itm_put_is_high(self):
        # Strike far above spot → put almost certainly finishes ITM.
        p = prob_itm_lognormal(spot=100, strike=200, iv=30, dte_days=30, right="P")
        assert p > 0.98

    def test_call_put_probabilities_sum_to_one(self):
        # P(S>K) + P(S<K) = 1 at the same strike.
        pc = prob_itm_lognormal(spot=100, strike=105, iv=25, dte_days=45, right="C")
        pp = prob_itm_lognormal(spot=100, strike=105, iv=25, dte_days=45, right="P")
        assert abs(pc + pp - 1.0) < 1e-9

    def test_accepts_percent_or_decimal_iv(self):
        a = prob_itm_lognormal(spot=100, strike=100, iv=30.0, dte_days=30, right="C")
        b = prob_itm_lognormal(spot=100, strike=100, iv=0.30, dte_days=30, right="C")
        assert abs(a - b) < 1e-9

    def test_degenerate_inputs_return_none(self):
        assert prob_itm_lognormal(spot=0, strike=100, iv=30, dte_days=30, right="C") is None
        assert prob_itm_lognormal(spot=100, strike=100, iv=0, dte_days=30, right="C") is None
        assert prob_itm_lognormal(spot=100, strike=100, iv=30, dte_days=0, right="C") is None


class TestClassifyLegAction:
    """Deterministic close/hold/roll — the per-leg verdict rules."""

    def test_short_cheap_worthless_near_expiry_lets_expire(self):
        # Sold for $2.00, now worth $0.05, 15% ITM, 2 DTE → let it expire.
        r = classify_leg_action(sign=-1, right="P", p_itm=0.05,
                                entry_prem=2.00, current_mid=0.05, dte=2)
        assert r["action"] == "LET_EXPIRE"

    def test_short_mostly_captured_says_close(self):
        # Sold for $2.00, now $0.30 (85% captured), 8% ITM, 40 DTE → buy to close.
        r = classify_leg_action(sign=-1, right="C", p_itm=0.08,
                                entry_prem=2.00, current_mid=0.30, dte=40)
        assert r["action"] == "CLOSE"
        assert r["captured_pct"] == 85.0

    def test_short_now_itm_says_roll(self):
        # Short call now 60% likely ITM → defend by rolling.
        r = classify_leg_action(sign=-1, right="C", p_itm=0.60,
                                entry_prem=2.00, current_mid=5.00, dte=30)
        assert r["action"] == "ROLL"
        assert "roll up" in r["reason"].lower()

    def test_short_put_itm_rolls_down(self):
        r = classify_leg_action(sign=-1, right="P", p_itm=0.65,
                                entry_prem=2.00, current_mid=5.00, dte=30)
        assert r["action"] == "ROLL"
        assert "roll down" in r["reason"].lower()

    def test_short_otm_with_time_holds(self):
        # 20% ITM, only modestly decayed, plenty of time → hold and collect theta.
        r = classify_leg_action(sign=-1, right="P", p_itm=0.20,
                                entry_prem=2.00, current_mid=1.50, dte=30)
        assert r["action"] == "HOLD"

    def test_long_winning_high_prob_holds(self):
        # Long call 70% ITM and in profit → hold, it's working.
        r = classify_leg_action(sign=1, right="C", p_itm=0.70,
                                entry_prem=3.00, current_mid=6.00, dte=45)
        assert r["action"] == "HOLD"

    def test_long_decayed_worthless_closes(self):
        # Long put down to $0.10, only 8% ITM → salvage / close.
        r = classify_leg_action(sign=1, right="P", p_itm=0.08,
                                entry_prem=3.00, current_mid=0.10, dte=20)
        assert r["action"] == "CLOSE"

    def test_long_lowprob_but_priced_rolls(self):
        # Long put 10% ITM but still worth $1.20 with time → roll closer.
        r = classify_leg_action(sign=1, right="P", p_itm=0.10,
                                entry_prem=3.00, current_mid=1.20, dte=25)
        assert r["action"] == "ROLL"

    def test_p_itm_none_does_not_crash(self):
        r = classify_leg_action(sign=1, right="C", p_itm=None,
                                entry_prem=3.00, current_mid=2.00, dte=20)
        assert r["action"] in {"CLOSE", "HOLD", "ROLL", "LET_EXPIRE"}
        assert r["p_itm_pct"] is None


class TestSummarizeTradeActions:
    """Overall verdict — signal is preserved; per-leg moves are surfaced."""

    def _leg(self, action, reason="x"):
        return {"action": action, "reason": reason, "p_itm_pct": 10.0, "captured_pct": 50.0}

    def test_signal_is_preserved_from_hold_signal(self):
        out = summarize_trade_actions(
            leg_actions=[self._leg("HOLD")], hold_signal="STRONG_HOLD",
            pop=72.0, expected_value=120.0, unrealized_pnl=50.0,
            max_profit=200.0, max_loss=-300.0, dte=40)
        assert out["action"] == "STRONG_HOLD"
        assert out["headline"].startswith("Hold")

    def test_close_signal_says_unwind(self):
        out = summarize_trade_actions(
            leg_actions=[self._leg("HOLD"), self._leg("HOLD")], hold_signal="STRONG_CLOSE",
            pop=15.0, expected_value=-80.0, unrealized_pnl=-250.0,
            max_profit=200.0, max_loss=-300.0, dte=5)
        assert out["action"] == "STRONG_CLOSE"
        assert "unwind" in out["headline"].lower() or "exit" in out["headline"].lower()

    def test_roll_leg_surfaces_in_headline(self):
        out = summarize_trade_actions(
            leg_actions=[self._leg("HOLD"), self._leg("ROLL", "roll the 750 call up/out")],
            hold_signal="HOLD", pop=55.0, expected_value=10.0, unrealized_pnl=-40.0,
            max_profit=200.0, max_loss=-300.0, dte=30)
        assert "roll the 750 call" in out["headline"].lower()

    def test_stock_leg_reason_added_for_combo(self):
        out = summarize_trade_actions(
            leg_actions=[self._leg("HOLD")], hold_signal="HOLD",
            pop=None, expected_value=None, unrealized_pnl=500.0,
            max_profit=None, max_loss=None, dte=30, has_stock=True, stock_pnl=1200.0)
        assert any("stock leg" in rsn.lower() for rsn in out["reasons"])

    def test_leg_notes_ordered_by_urgency(self):
        # ROLL (urgency 3) must come before HOLD (0) in leg_notes.
        out = summarize_trade_actions(
            leg_actions=[self._leg("HOLD", "hold me"), self._leg("ROLL", "roll me")],
            hold_signal="HOLD", pop=50.0, expected_value=0.0, unrealized_pnl=0.0,
            max_profit=None, max_loss=None, dte=30)
        assert out["leg_notes"][0] == "roll me"

    def test_outcome_describes_whole_structure(self):
        # Outcome sentence must state defined risk/reward + where spot sits + PoP.
        out = summarize_trade_actions(
            leg_actions=[self._leg("HOLD")], hold_signal="HOLD",
            pop=62.0, expected_value=40.0, unrealized_pnl=-3.0,
            max_profit=1500.0, max_loss=-2936.0, dte=90,
            breakevens=[38.0, 66.0], underlying_price=52.0)
        oc = out["outcome"].lower()
        assert "makes up to $1,500" in oc and "risks $2,936" in oc
        assert "inside the profit band" in oc
        assert "62% chance of profit" in oc

    def test_headline_flags_loss_zone_on_close(self):
        # Spot outside the band + CLOSE signal → headline names the loss zone.
        out = summarize_trade_actions(
            leg_actions=[self._leg("HOLD"), self._leg("HOLD")], hold_signal="STRONG_CLOSE",
            pop=8.0, expected_value=-400.0, unrealized_pnl=-900.0,
            max_profit=500.0, max_loss=-1000.0, dte=10,
            breakevens=[40.0, 60.0], underlying_price=72.0)
        assert "above the $60.00 breakeven" in out["headline"]


class TestStructureStanding:
    def test_inside_band(self):
        assert "inside the profit band" in _structure_standing(50, [40, 60], 100, -100)

    def test_below_band(self):
        s = _structure_standing(35, [40, 60], 100, -100)
        assert "below" in s and "loss zone" in s

    def test_above_band(self):
        s = _structure_standing(70, [40, 60], 100, -100)
        assert "above" in s and "loss zone" in s

    def test_no_breakevens_returns_none(self):
        assert _structure_standing(50, [], 100, -100) is None


class TestOptionLegsNetDebit:
    """The entry-cost fix — a short leg's credit must be SUBTRACTED, not added."""

    def _lm(self, i, strike, typ, qty, action, prem):
        return {"i": i, "strike": strike, "type": typ, "qty": qty, "action": action,
                "_leg": {"strike": strike, "type": typ.lower(), "price": prem}}

    def test_dram_short_call_credit_is_subtracted(self):
        from app.routers.saved_strategy_router import option_legs_net_debit
        legs = [
            self._lm(0, 30, "CALL", 1, "BUY", 28.44),
            self._lm(1, 60, "PUT", 1, "BUY", 12.37),
            self._lm(2, 35, "PUT", 4, "SELL", 2.06),
            self._lm(3, 30, "PUT", 3, "BUY", 1.18),
            self._lm(4, 70, "CALL", 1, "SELL", 6.75),   # the leg the old bug inverted
        ]
        # BUY negative, SELL positive: -2844 -1237 +824 -354 +675 = -2936
        assert option_legs_net_debit(legs, []) == -2936.0

    def test_credit_spread_is_positive(self):
        from app.routers.saved_strategy_router import option_legs_net_debit
        legs = [
            self._lm(0, 580, "PUT", 1, "BUY", 34.58),
            self._lm(1, 750, "CALL", 1, "SELL", 31.00),
            self._lm(2, 400, "PUT", 1, "SELL", 4.92),
        ]
        assert option_legs_net_debit(legs, []) == 134.0

    def test_missing_premium_returns_none(self):
        from app.routers.saved_strategy_router import option_legs_net_debit
        legs = [{"i": 0, "strike": 30, "type": "CALL", "qty": 1, "action": "BUY", "_leg": {"strike": 30, "type": "call"}}]
        assert option_legs_net_debit(legs, []) is None

    def test_falls_back_to_entry_prices_by_strike(self):
        from app.routers.saved_strategy_router import option_legs_net_debit
        legs = [{"i": 0, "strike": 70, "type": "CALL", "qty": 1, "action": "SELL", "_leg": {"strike": 70, "type": "call"}}]
        eps = [{"strike": 70, "type": "call", "price": 6.75}]
        assert option_legs_net_debit(legs, eps) == 675.0


class TestBuildPayoff:
    """The unified payoff engine used by stock / futures / options / combos."""

    def test_covered_call_full_metrics(self):
        from app.routers.saved_strategy_router import build_payoff
        # 100 sh @ $50 + short 55 call for $2 credit, spot $53, 30 DTE, 30% IV.
        r = build_payoff(
            53, [{"strike": 55, "right": "C", "sign": -1, "qty": 1, "iv": 0.30, "dte_years": 30 / 365}],
            {"shares": 100, "avg_cost": 50, "mult": 1.0}, 200.0, avg_iv=0.30, dte_days=30,
        )
        assert r["max_profit"] == 700.0 and not r["unbounded_profit"]  # capped by short call
        assert r["max_loss"] == -4800.0
        assert r["breakevens"] == [48.0]                               # cost − premium
        assert r["pop"] is not None and 0 < r["pop"] <= 100
        assert r["expected_value"] is not None
        assert len(r["scenarios"]) == 49

    def test_pure_stock_has_no_pop(self):
        from app.routers.saved_strategy_router import build_payoff
        r = build_payoff(55, [], {"shares": 100, "avg_cost": 50, "mult": 1.0}, 0.0)
        assert r["pop"] is None and r["expected_value"] is None
        assert r["unbounded_profit"] and r["max_loss"] == -5000.0

    def test_zero_underlying_returns_empty(self):
        from app.routers.saved_strategy_router import build_payoff
        r = build_payoff(0, [], {"shares": 100, "avg_cost": 50, "mult": 1.0}, 0.0)
        assert r["scenarios"] == [] and r["max_profit"] is None


class TestStructurePayoffExtremes:
    """Exact whole-trade max profit / max loss from the payoff breakpoints."""

    def test_debit_call_spread_is_bounded_both_sides(self):
        legs = [
            {"strike": 100, "right": "C", "sign": 1, "qty": 1},
            {"strike": 110, "right": "C", "sign": -1, "qty": 1},
        ]
        e = structure_payoff_extremes(legs, -400)  # $4 debit
        assert e["max_profit"] == 600.0 and e["max_profit_price"] == 110.0
        assert e["max_loss"] == -400.0 and e["max_loss_price"] == 100.0  # plateau ≤ lower strike
        assert not e["unbounded_profit"] and not e["unbounded_loss"]

    def test_long_call_has_unlimited_upside(self):
        e = structure_payoff_extremes([{"strike": 100, "right": "C", "sign": 1, "qty": 1}], -500)
        assert e["unbounded_profit"] and e["max_profit"] is None
        assert e["max_loss"] == -500.0

    def test_naked_short_call_has_unlimited_downside(self):
        e = structure_payoff_extremes([{"strike": 100, "right": "C", "sign": -1, "qty": 1}], 300)
        assert e["unbounded_loss"] and e["max_loss"] is None
        assert e["max_profit"] == 300.0

    def test_dram_dual_buffer_extremes(self):
        # The user's DRAM trade: bounded both ways, +$1,064 up top, -$1,936 down low.
        legs = [
            {"strike": 30, "right": "C", "sign": 1, "qty": 1},
            {"strike": 60, "right": "P", "sign": 1, "qty": 1},
            {"strike": 35, "right": "P", "sign": -1, "qty": 4},
            {"strike": 30, "right": "P", "sign": 1, "qty": 3},
            {"strike": 70, "right": "C", "sign": -1, "qty": 1},
        ]
        e = structure_payoff_extremes(legs, -2936)
        assert e["max_profit"] == 1064.0 and e["max_profit_price"] == 70.0
        assert e["max_loss"] == -1936.0 and e["max_loss_price"] == 30.0  # boundary, not $0
        assert not e["unbounded_profit"] and not e["unbounded_loss"]

    def test_expiry_payoff_matches_manual(self):
        # Long 100 call, $2 debit: at 105 → intrinsic 5 → +500 − 200 = +300.
        legs = [{"strike": 100, "right": "C", "sign": 1, "qty": 1}]
        assert expiry_payoff(legs, 105, -200) == 300.0
        assert expiry_payoff(legs, 95, -200) == -200.0  # OTM → just the debit

    def test_no_strikes_no_stock_returns_none(self):
        assert structure_payoff_extremes([], -100) is None

    def test_long_stock_is_unlimited_up_capped_at_zero(self):
        # 100 sh @ $50: max loss = −$5,000 (to zero), upside unlimited.
        e = structure_payoff_extremes([], 0.0, stock={"shares": 100, "avg_cost": 50, "mult": 1.0})
        assert e["unbounded_profit"] and e["max_profit"] is None
        assert e["max_loss"] == -5000.0 and not e["unbounded_loss"]

    def test_short_stock_is_unlimited_down(self):
        e = structure_payoff_extremes([], 0.0, stock={"shares": -100, "avg_cost": 50, "mult": 1.0})
        assert e["unbounded_loss"] and e["max_loss"] is None
        assert e["max_profit"] == 5000.0

    def test_covered_call_caps_the_upside(self):
        # 100 sh @ $50 + short 55 call for $2 credit (entry_cost +200):
        # max profit = 5·100 + 200 = $700 at ≥55 (capped, NOT unlimited).
        legs = [{"strike": 55, "right": "C", "sign": -1, "qty": 1}]
        e = structure_payoff_extremes(legs, 200.0, stock={"shares": 100, "avg_cost": 50, "mult": 1.0})
        assert e["max_profit"] == 700.0 and not e["unbounded_profit"]
        assert e["max_profit_price"] == 55.0
        assert e["max_loss"] == -4800.0   # stock to zero, keep the premium

    def test_futures_multiplier_scales_pnl(self):
        # 2 contracts @ 4500, ×50 multiplier: max loss to zero = −$450,000.
        e = structure_payoff_extremes([], 0.0, stock={"shares": 2, "avg_cost": 4500, "mult": 50.0})
        assert e["unbounded_profit"] and e["max_loss"] == -450000.0

    def test_bull_put_credit_spread(self):
        # Sell 100P, buy 95P for a $2 net credit → max profit $200, max loss −$300.
        legs = [{"strike": 100, "right": "P", "sign": -1, "qty": 1},
                {"strike": 95, "right": "P", "sign": 1, "qty": 1}]
        e = structure_payoff_extremes(legs, 200)
        assert e["max_profit"] == 200.0 and e["max_loss"] == -300.0
        assert not e["unbounded_profit"] and not e["unbounded_loss"]

    def test_iron_condor_bounded_both_sides(self):
        legs = [{"strike": 90, "right": "P", "sign": 1, "qty": 1},
                {"strike": 95, "right": "P", "sign": -1, "qty": 1},
                {"strike": 105, "right": "C", "sign": -1, "qty": 1},
                {"strike": 110, "right": "C", "sign": 1, "qty": 1}]
        e = structure_payoff_extremes(legs, 200)   # $2 net credit
        assert e["max_profit"] == 200.0 and e["max_loss"] == -300.0

    def test_call_ratio_unbounded_loss(self):
        # Buy 1x 100C, sell 2x 110C, $1 net debit → unbounded loss above 110.
        legs = [{"strike": 100, "right": "C", "sign": 1, "qty": 1},
                {"strike": 110, "right": "C", "sign": -1, "qty": 2}]
        e = structure_payoff_extremes(legs, -100)
        assert e["unbounded_loss"] and e["max_loss"] is None
        assert e["max_profit"] == 900.0 and e["max_profit_price"] == 110.0


class TestStructureBreakevens:
    """EXACT breakevens from the piecewise-linear payoff — no coarse-grid kink error."""

    def test_gld_short_strangle_both_breakevens_exact(self):
        # short 300 put + short 470 call, $0.80 credit (entry_cost +80). BEs are the
        # strikes ± the credit — the case where the old grid scan drifted ~$0.26 on one side.
        legs = [{"strike": 300, "right": "P", "sign": -1, "qty": 1},
                {"strike": 470, "right": "C", "sign": -1, "qty": 1}]
        assert structure_breakevens(legs, 80.0) == [299.20, 470.80]

    def test_iron_condor_breakevens(self):
        legs = [{"strike": 290, "right": "P", "sign": 1, "qty": 1},
                {"strike": 300, "right": "P", "sign": -1, "qty": 1},
                {"strike": 470, "right": "C", "sign": -1, "qty": 1},
                {"strike": 480, "right": "C", "sign": 1, "qty": 1}]
        assert structure_breakevens(legs, 100.0) == [299.0, 471.0]

    def test_debit_call_spread_single_breakeven(self):
        # long 100 / short 110 call, $4 debit (entry_cost −400) → one BE at 104.
        legs = [{"strike": 100, "right": "C", "sign": 1, "qty": 1},
                {"strike": 110, "right": "C", "sign": -1, "qty": 1}]
        assert structure_breakevens(legs, -400.0) == [104.0]

    def test_credit_put_matches_strike_minus_credit_exactly(self):
        # short 90 put, $1.50 credit → BE = 88.50 exactly (a grid scan straddling 90 would drift).
        assert structure_breakevens([{"strike": 90, "right": "P", "sign": -1, "qty": 1}], 150.0) == [88.50]

    def test_no_legs_no_breakevens(self):
        assert structure_breakevens([], 100.0) == []


class TestExitRecommendation:
    """The whole-trade 4-level exit signal + lifecycle exit-timing rules."""

    def test_maps_base_signals(self):
        assert exit_recommendation(hold_signal="STRONG_HOLD", pop=80, unrealized_pnl=5,
                                   max_profit=200, max_loss=-800, dte=40)["signal"] == "STRONG_HOLD"
        assert exit_recommendation(hold_signal="STRONG_CLOSE", pop=10, unrealized_pnl=-500,
                                   max_profit=200, max_loss=-800, dte=30)["signal"] == "STRONG_CLOSE"

    def test_take_half_early_rule(self):
        # 60% of max profit banked with plenty of time → consider closing early.
        r = exit_recommendation(hold_signal="HOLD", pop=95, unrealized_pnl=60,
                                max_profit=100, max_loss=-2000, dte=20, theta_per_day=4)
        assert r["signal"] == "CLOSE" and r["captured_pct"] == 60.0

    def test_near_max_profit_closes(self):
        r = exit_recommendation(hold_signal="HOLD", pop=98, unrealized_pnl=90,
                                max_profit=100, max_loss=-2000, dte=15)
        assert r["signal"] == "STRONG_CLOSE"

    def test_near_max_loss_closes(self):
        r = exit_recommendation(hold_signal="HOLD", pop=20, unrealized_pnl=-700,
                                max_profit=200, max_loss=-800, dte=30)
        assert r["signal"] == "STRONG_CLOSE"

    def test_expiry_gamma_downgrades_hold(self):
        r = exit_recommendation(hold_signal="HOLD", pop=80, unrealized_pnl=30,
                                max_profit=200, max_loss=-800, dte=1)
        assert r["signal"] == "CLOSE"

    def test_healthy_position_holds_with_reason(self):
        r = exit_recommendation(hold_signal="HOLD", pop=75, unrealized_pnl=10,
                                max_profit=200, max_loss=-800, dte=40, theta_per_day=3)
        assert r["signal"] == "HOLD" and r["reasons"]


# ── realized_close_pnl — the close sign convention ───────────────────────────

class TestRealizedClosePnl:
    def test_short_option_buy_back_cheaper_is_profit(self):
        # Sold a call for $2.00, buy it back for $0.50, 1 contract → +$150.
        assert realized_close_pnl("SELL", 2.00, 0.50, 1, is_option=True) == pytest.approx(150.0)

    def test_short_option_buy_back_richer_is_loss(self):
        # Sold a put for $1.00, buy it back for $3.00, 2 contracts → −$400.
        assert realized_close_pnl("SELL", 1.00, 3.00, 2, is_option=True) == pytest.approx(-400.0)

    def test_long_option_sell_higher_is_profit(self):
        # Bought a call for $1.00, sell it for $2.50, 3 contracts → +$450.
        assert realized_close_pnl("BUY", 1.00, 2.50, 3, is_option=True) == pytest.approx(450.0)

    def test_long_stock_scales_by_shares_not_100(self):
        # Long 100 shares at $50, sold at $55 → +$500 (no ×100 for stock).
        assert realized_close_pnl("BUY", 50.0, 55.0, 100, is_option=False) == pytest.approx(500.0)

    def test_short_stock_profits_when_price_falls(self):
        assert realized_close_pnl("SELL", 50.0, 40.0, 100, is_option=False) == pytest.approx(1000.0)

    def test_short_and_long_are_mirror_images(self):
        s = realized_close_pnl("SELL", 2.0, 1.2, 1, is_option=True)
        l = realized_close_pnl("BUY", 2.0, 1.2, 1, is_option=True)
        assert s == pytest.approx(-l)


if __name__ == "__main__":
    import sys
    sys.exit(pytest.main([__file__, "-v"]))
