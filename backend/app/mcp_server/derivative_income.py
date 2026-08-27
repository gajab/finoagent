"""Derivative Income — MCP tool definitions.

Exposes the platform's "Derivative Income" desk analysis as a Model-Context-Protocol
tool so an external agent (Gemini CLI, Claude Desktop, …) can scan option-premium income
trades for a ticker and rank them across a large universe.

Reuses the exact backend engine that powers the web UI:

    rank_desk()  (desk_review_service)  →  full grade + risk-adjusted desk metrics
    run_derivative_income()             →  the underlying scan (used by mode="scan")

Both run against the keyless yfinance provider with ``user=None, db=None`` — no auth, no
OpenAI key, no database. The LLM Quant→Risk→PM cascade shown in the UI is a *separate*,
on-demand endpoint and is intentionally NOT invoked here (the grade + risk-adjusted quality
returned below are fully algorithmic).

This module only DEFINES the ``MCPServer`` (``mcp``) and its tools. Serving it:
  * HTTP (prod): mounted into the FastAPI app — see ``.http_mount`` / ``app.main``.
  * stdio (local dev): ``mcp_servers/derivative_income/server.py``.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any, Optional

from mcp.server import MCPServer

from ..services.desk_review_service import rank_desk
from ..services.derivative_income_service import run_derivative_income

log = logging.getLogger("app.mcp.derivative_income")

# Scannable structures (fresh-scan builders). "collar" is only built for a user-supplied
# trade, so it is a harmless no-op here and is omitted from the default.
VALID_STRUCTURES = (
    "covered_call",
    "cash_secured_put",
    "short_strangle",
    "credit_spread",
    "iron_condor",
    "jade_lizard",
    "calendar",
)
DEFAULT_TIMEOUT_S = 150.0

mcp = MCPServer(
    name="finoagent-derivative-income",
    version="1.0.0",
    instructions=(
        "Tools for OPTION-PREMIUM INCOME ('derivative income') analysis on US stocks & ETFs. "
        "Use `analyze_derivative_income` whenever the user wants to SELL options for income, "
        "or asks about: covered calls, cash-secured puts, selling puts/calls, credit spreads, "
        "iron condors, jade lizards, strangles, calendars, 'the wheel', theta/premium income, "
        "'high-probability' or 'income' option trades, or 'which of these tickers should I sell "
        "options on'. It returns the full desk read for ONE ticker (grade, win%, premium, "
        "greeks, IV/HV/HAR vol stats, VaR/CVaR, risk-adjusted quality, quant analysis, legs) — "
        "the same data the FinoAgent web UI shows. For a list of tickers, call it once per "
        "ticker (results come back ranked best-first) and aggregate to pick the best trades by "
        "any metric. Do NOT use it for directional stock picks, for BUYING options, or for "
        "non-US symbols."
    ),
)


# ---------------------------------------------------------------------------
# Field extraction — flatten the engine payload into a compact, cross-ticker
# -rankable shape while preserving every metric shown in the UI.
# ---------------------------------------------------------------------------
def _underlying_block(res: dict) -> dict:
    """Ticker-level context: price, vol surface (ATM IV / HV / HAR fwd RV / ranks /
    skew / term structure), rates, earnings, regime read."""
    vs = res.get("vol_stats") or {}
    ctx = res.get("context") or {}
    pf = res.get("portfolio_fit") or {}
    ts = vs.get("term_structure") or {}
    return {
        "ticker": res.get("ticker"),
        "spot": res.get("spot"),
        "as_of": res.get("as_of"),
        "sofr_pct": res.get("sofr_pct"),
        "next_earnings": ctx.get("next_earnings"),
        "exercise_style": ctx.get("exercise_style"),
        "week52": ctx.get("week52"),
        "beta_1y_spx": pf.get("beta_1y_spx"),
        # --- volatility surface ---
        "atm_iv_pct": vs.get("iv_atm_pct"),
        "hv30_pct": vs.get("hv30_pct"),
        "hv20_pct": vs.get("hv20_pct"),
        "hv10_pct": vs.get("hv10_pct"),
        "forward_rv_har_pct": vs.get("har_rv_pct"),      # Fwd RV · HAR (forecast)
        "iv_vs_har_pts": vs.get("iv_vs_har_pts"),        # + = implied rich to forecast (seller edge)
        "iv_rank": vs.get("iv_rank"),
        "iv_percentile": vs.get("iv_percentile"),
        "vol_rank": vs.get("vol_rank"),
        "vol_percentile": vs.get("vol_percentile"),
        "skew_pts": vs.get("skew_pts"),
        "skew_direction": vs.get("skew_direction"),
        "term_structure": {
            "state": ts.get("state"),
            "back_minus_front_pts": ts.get("back_minus_front_pts"),
            "front_iv_pct": ts.get("front_iv_pct"),
            "back_iv_pct": ts.get("back_iv_pct"),
        } if ts else None,
        # --- context ---
        "corporate_actions": res.get("corporate_actions"),
        "technical_summary": res.get("ta_summary"),
        "gex_regime": (res.get("gex") or {}).get("regime") if res.get("gex") else None,
    }


def _trade_block(t: dict, verbose: bool = False) -> dict:
    """One ranked opportunity → flat dict with grade, win%, premium suite, capital/risk,
    greeks, risk-adjusted quality and quant analysis. ``verbose`` adds chrome
    (ta factors, risk-trigger ladder, flags, full desk_metrics)."""
    dm = t.get("desk_metrics") or {}
    risk = dm.get("risk") or {}
    pm = dm.get("pm") or {}
    quant = dm.get("quant") or {}
    vrp = dm.get("vrp") or {}

    out: dict[str, Any] = {
        # --- identity ---
        "structure": t.get("structure"),
        "label": t.get("label"),
        "expiration": t.get("expiration"),
        "dte": t.get("dte"),
        "exercise_style": t.get("exercise_style"),

        # --- grading (algorithmic desk) ---
        "algo_grade": t.get("algo_grade"),
        "desk_score": t.get("desk_score"),
        "approval_odds": t.get("approval_odds"),
        "base_quality": t.get("base_quality"),
        "grade_blocking": t.get("grade_blocking"),

        # --- win probability ---
        "win_pct": t.get("prob_keep_pct"),               # P(not assigned / stays OTM)
        "prob_method": t.get("prob_method"),
        "prob_assign_pct": t.get("prob_assign_pct"),
        "prob_touch_pct": t.get("prob_touch_pct"),
        "short_delta": t.get("short_delta"),

        # --- strikes ---
        "short_strike": t.get("short_strike"),
        "short_strike_pct_from_spot": t.get("short_strike_pct"),
        "breakeven": t.get("breakeven"),
        "cushion_pct": t.get("cushion_pct"),

        # --- premium / income ---
        "premium": t.get("premium"),                     # $ per contract
        "premium_per_share": t.get("premium_per_share"),
        "premium_annualized_pct": t.get("premium_annualized_pct"),
        "total_annualized_pct": t.get("total_annualized_pct"),
        "static_return_pct": t.get("static_return_pct"),
        "sofr_excess_pct": t.get("sofr_excess_pct"),
        "beats_sofr": t.get("beats_sofr"),
        "if_assigned_return_pct": t.get("if_assigned_return_pct"),
        "event_adjusted_yield_pct": t.get("event_adjusted_yield_pct"),

        # --- capital & risk ---
        "capital_at_risk": t.get("collateral"),          # yield denominator (BPR / width / stock notional)
        "capital_basis": t.get("capital_basis"),
        "notional_capital": t.get("notional_capital"),
        "max_loss": t.get("max_loss"),
        "max_profit": t.get("max_profit"),
        "var_95": risk.get("var_95"),
        "cvar_95": risk.get("cvar_95"),
        "var_99": risk.get("var_99"),
        "cvar_99": risk.get("cvar_99"),

        # --- implied vol / edge on this trade ---
        "atm_iv_pct": t.get("atm_iv_pct"),
        "iv_hv_ratio": t.get("iv_hv_ratio"),
        "premium_richness": t.get("premium_richness"),
        "iv_edge_vp": t.get("iv_edge_vp"),

        # --- dynamic greeks (per contract) ---
        "greeks": t.get("greeks"),                       # delta/gamma/theta/vega
        "theta_per_day": t.get("theta_per_day"),
        "vega_exposure": t.get("vega_exposure"),

        # --- risk-adjusted quality ---
        "risk_adjusted": {
            "quant_score": quant.get("score"),
            "quant_verdict": quant.get("verdict"),
            "quant_reasons": quant.get("reasons"),
            "quant_subscores": quant.get("subscores"),
            "pop_pct": pm.get("pop"),
            "omega": pm.get("omega"),
            "sortino": pm.get("sortino"),
            "calmar": pm.get("calmar"),
            "kelly_fraction": pm.get("kelly_fraction"),
            "expected_value": pm.get("expected_value"),
            "expected_return_pct": pm.get("expected_return_pct"),
            "downside_dev_pct": pm.get("downside_dev_pct"),
        },

        # --- quant analysis (Q-vs-P boundary read + VRP) ---
        "quant_analysis": {
            **(t.get("qp") or {}),
            "vrp": vrp or None,
        },

        # --- confidence (fill + model trust) ---
        "confidence": t.get("confidence"),

        # --- liquidity ---
        "liquidity": t.get("liquidity"),

        # --- trade legs (full) ---
        "legs": t.get("legs"),

        # --- concise grade rationale ---
        "grade_merits": t.get("grade_merits"),
        "grade_demerits": t.get("grade_demerits"),
        "ta_note": t.get("ta_note"),
    }

    if verbose:
        out["grade_adjustments"] = t.get("grade_adjustments")
        out["ta_factors"] = t.get("ta_factors")
        out["risk_triggers"] = t.get("risk_triggers")
        out["flags"] = t.get("flags")
        out["desk_metrics_full"] = dm
    return out


def _shape_desk(res: dict, top_n: int, verbose: bool, include_raw: bool) -> dict:
    """Full desk payload (mode='desk') → compact, ranked, cross-ticker-rankable dict."""
    ranked = res.get("ranked") or []
    top = ranked[: max(top_n, 0)] if top_n else ranked
    top_pick = res.get("algo_top_pick")
    payload = {
        "ok": True,
        "mode": "desk",
        "underlying": _underlying_block(res),
        "top_pick": _trade_block(top_pick, verbose) if top_pick else None,
        "trades": [_trade_block(t, verbose) for t in top],
        "n_trades_returned": len(top),
        "n_trades_total": res.get("n_trades", len(ranked)),
        "events": res.get("events"),
        "flag_events": res.get("flag_events"),
        "note": res.get("note"),
        "data_source_note": res.get("data_source_note"),
    }
    if include_raw:
        payload["raw"] = res
    return payload


def _shape_scan(res: dict, top_n: int, verbose: bool, include_raw: bool) -> dict:
    """Fast scan payload (mode='scan') — no algorithmic grade / desk risk metrics.
    Those fields come back null; use mode='desk' for the full UI-equivalent read."""
    opps = res.get("opportunities") or []
    top = opps[: max(top_n, 0)] if top_n else opps
    # `run_derivative_income` puts vol_stats under context; normalise to the desk shape.
    ctx = res.get("context") or {}
    norm = {**res, "vol_stats": ctx.get("vol_stats") or {}, "ta_summary": None,
            "portfolio_fit": None, "gex": None, "corporate_actions": None}
    payload = {
        "ok": True,
        "mode": "scan",
        "underlying": _underlying_block(norm),
        "top_pick": _trade_block(top[0], verbose) if top else None,
        "trades": [_trade_block(t, verbose) for t in top],
        "n_trades_returned": len(top),
        "n_trades_total": res.get("n_opportunities", len(opps)),
        "events": res.get("events"),
        "note": res.get("note"),
        "data_source_note": res.get("data_source_note"),
    }
    if include_raw:
        payload["raw"] = res
    return payload


# ---------------------------------------------------------------------------
# Tools
# ---------------------------------------------------------------------------
@mcp.tool(title="Analyze Derivative Income", structured_output=True)
async def analyze_derivative_income(
    ticker: str,
    target_dte: Optional[int] = None,
    target_expiration: Optional[str] = None,
    min_probability: float = 0.90,
    min_premium: float = 20.0,
    structures: Optional[list[str]] = None,
    already_own_stock: bool = False,
    top_n: int = 5,
    mode: str = "desk",
    verbose: bool = False,
    include_raw: bool = False,
) -> dict[str, Any]:
    """Analyze option-premium INCOME trades for one US stock/ETF and return the full desk
    analysis the FinoAgent web UI shows.

    USE THIS WHEN the user wants to sell option premium / generate options income on a name —
    or screen a list of names for income trades. Trigger phrases: "covered call", "cash-
    secured put", "sell puts/calls", "credit spread", "put/call spread", "iron condor", "jade
    lizard", "strangle", "calendar", "the wheel", "theta income", "premium selling", "high-
    probability options", "which tickers should I sell options on". Covers these structures:
    covered_call, cash_secured_put, short_strangle, credit_spread, iron_condor, jade_lizard,
    calendar. Do NOT use for directional stock picks, for BUYING options, or non-US symbols.

    Designed to be called once per ticker across a large universe; `trades` come back
    ranked best-first (by algorithmic desk score) so you can aggregate results across
    many tickers and then select the top trades by any returned metric.

    Args:
        ticker: Underlying symbol, e.g. "AAPL". Index options: use a "." prefix (".SPX").
        target_dte: Target days-to-expiry (±10d window). Omit for the default =
            monthly expiries ≤45 DTE (the UI "Auto Monthlies <=45 days" default).
        target_expiration: Exact expiry "YYYY-MM-DD"; overrides target_dte.
        min_probability: Min probability of NOT being assigned (0.5–0.99). Default 0.90.
        min_premium: Min premium in $/contract to surface a trade. Default 20.
        structures: Subset of trade structures to scan. Omit = all. Valid:
            covered_call, cash_secured_put, short_strangle, credit_spread,
            iron_condor, jade_lizard, calendar.
        already_own_stock: True = you already hold the shares, so a short call is scored
            as a COVERED income overlay (not a naked call). Default False.
        top_n: Max ranked trades to return (best-first). Default 5. Use 0 for all found.
        mode: "desk" (default) = full grade + risk-adjusted desk metrics (slower; extra
            technical/GEX reads). "scan" = faster, premium/greeks/vol only, no grade.
        verbose: Add chrome per trade (TA factors, risk-trigger ladder, flags, full
            desk_metrics). Default False.
        include_raw: Attach the complete engine payload under `raw` for deep dives on a
            single ticker. Default False (keep responses compact for bulk scans).

    Returns:
        {ok, mode, underlying{...vol surface, ranks, skew, HAR, term structure...},
         top_pick{...}, trades[{grade, win_pct, premium..., greeks, capital/risk,
         risk_adjusted, quant_analysis, legs}], n_trades_total, events, ...}
        On failure: {ok: False, error, ticker}.
    """
    sym = (ticker or "").strip().upper()
    if not sym:
        return {"ok": False, "error": "ticker is required", "ticker": ticker}
    if sym.startswith("."):                       # ".SPX" → "^SPX" (index convention)
        sym = "^" + sym[1:]

    mode = (mode or "desk").strip().lower()
    if mode not in ("desk", "scan"):
        return {"ok": False, "error": f"mode must be 'desk' or 'scan', got {mode!r}", "ticker": sym}

    if structures:
        bad = [s for s in structures if s not in VALID_STRUCTURES]
        if bad:
            return {"ok": False, "ticker": sym,
                    "error": f"Unknown structure(s) {bad}. Valid: {list(VALID_STRUCTURES)}"}

    min_prob = min(max(float(min_probability), 0.5), 0.99)

    try:
        async def _run() -> dict:
            if mode == "desk":
                res = await rank_desk(
                    ticker=sym, target_dte=target_dte, target_expiration=target_expiration,
                    min_prob=min_prob, min_income=float(min_premium),
                    structures=structures, quote_source="yfinance",
                    owns_underlying=bool(already_own_stock), user=None, db=None,
                )
                if res.get("error"):
                    return {"ok": False, "error": res["error"], "ticker": sym}
                return _shape_desk(res, top_n, verbose, include_raw)
            res = await run_derivative_income(
                ticker=sym, target_dte=target_dte, target_expiration=target_expiration,
                min_prob=min_prob, min_income=float(min_premium),
                structures=structures, quote_source="yfinance",
                owns_underlying=bool(already_own_stock), user=None, db=None,
            )
            if res.get("error"):
                return {"ok": False, "error": res["error"], "ticker": sym}
            return _shape_scan(res, top_n, verbose, include_raw)

        return await asyncio.wait_for(_run(), timeout=DEFAULT_TIMEOUT_S)
    except asyncio.TimeoutError:
        return {"ok": False, "ticker": sym,
                "error": f"timed out after {DEFAULT_TIMEOUT_S:.0f}s (yfinance slow/rate-limited); retry"}
    except Exception as exc:  # noqa: BLE001 — never crash the loop over a universe of tickers
        log.exception("analyze_derivative_income failed for %s", sym)
        return {"ok": False, "ticker": sym, "error": f"{type(exc).__name__}: {exc}"}


@mcp.tool(title="List Trade Structures")
def list_trade_structures() -> dict[str, Any]:
    """The trade structures analyze_derivative_income can scan, with one-line descriptions."""
    return {
        "structures": {
            "covered_call": "Sell an OTM call (covered if you hold shares, else naked Reg-T margin).",
            "cash_secured_put": "Sell an OTM put; income + willingness to buy lower.",
            "short_strangle": "Neutral, undefined-risk: sell an OTM put AND call.",
            "credit_spread": "Defined-risk vertical: sell near / buy far (put or call side).",
            "iron_condor": "Neutral, defined-risk both sides (put spread + call spread).",
            "jade_lizard": "Short put + call spread sized so net credit ≥ call width → no upside risk.",
            "calendar": "Long-vega: sell near / buy far at the same strike (net debit = max loss).",
        },
        "default": "all of the above (pass structures=null)",
    }
