"""
Institutional-grade What-If scenario evaluator.

Flow
----
1. Fetch a live market snapshot from yfinance (indices, volatility, rates, dollar,
   commodities, credit proxy) — gives the LLM concrete grounding instead of a blank slate.
2. If the user opts in, pull their active SavedStrategy tickers so the LLM can
   score individual positions.
3. Call LLM with:
     - the scenario text
     - time horizon (days / weeks / months / year)
     - market snapshot
     - optional portfolio context
4. Return a rich, institutional-style JSON:
     - executive_thesis, scenario_probability, time_horizon_estimate
     - historical_analogue, key_assumptions, key_catalysts, invalidation_signals
     - direct beneficiaries + casualties (sectors / stocks / etfs) with magnitude
     - second_order_effects
     - cross_asset_reactions (rates, dxy, oil, gold, vix, credit spreads)
     - portfolio_impacts — per-ticker scoring of the user's own positions
     - suggested_hedges — actionable trade ideas to neutralise unwanted exposure
"""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Optional

import yfinance as yf

from .llm_service import call_llm

logger = logging.getLogger(__name__)

# Tickers used for the "current market state" snapshot that grounds the LLM.
# Chosen to cover: equity indices, volatility, rates, USD, commodities, credit.
_SNAPSHOT_TICKERS: dict[str, str] = {
    "SPY":  "S&P 500",
    "QQQ":  "Nasdaq 100",
    "IWM":  "Russell 2000 (small caps)",
    "^VIX": "VIX (equity volatility)",
    "^TNX": "US 10Y Treasury yield",
    "UUP":  "US Dollar Index ETF",
    "USO":  "Crude Oil ETF",
    "GLD":  "Gold ETF",
    "HYG":  "High-yield credit ETF",
}


def _fetch_market_snapshot_sync() -> dict[str, dict]:
    """Blocking yfinance call — price + 5d change for each snapshot ticker.

    We keep this tight (~1 network round-trip via batched download) so it doesn't
    add meaningful latency to the scenario analysis. On failure we return an
    empty dict; the prompt handles the absence gracefully.
    """
    out: dict[str, dict] = {}
    try:
        tickers = list(_SNAPSHOT_TICKERS.keys())
        df = yf.download(
            tickers=" ".join(tickers),
            period="10d",
            interval="1d",
            auto_adjust=True,
            progress=False,
            group_by="ticker",
            threads=True,
        )
        if df is None or df.empty:
            return out
        for tk in tickers:
            try:
                # Batched yfinance frames are MultiIndex; fall through if schema differs.
                closes = df[tk]["Close"].dropna() if tk in df.columns.get_level_values(0) else df["Close"][tk].dropna()
                if len(closes) < 2:
                    continue
                last = float(closes.iloc[-1])
                prev = float(closes.iloc[0])
                change_pct = ((last - prev) / prev * 100.0) if prev else 0.0
                out[tk] = {
                    "label": _SNAPSHOT_TICKERS[tk],
                    "last": round(last, 4),
                    "change_pct_5d": round(change_pct, 2),
                }
            except Exception as inner:
                logger.debug(f"Snapshot skip {tk}: {inner}")
                continue
    except Exception as exc:
        logger.warning(f"Market snapshot fetch failed: {exc}")
    return out


def _render_snapshot(snapshot: dict[str, dict]) -> str:
    if not snapshot:
        return "(Live market snapshot unavailable — reason from general knowledge of current conditions.)"
    lines = ["CURRENT MARKET SNAPSHOT (last 5 trading days):"]
    for tk, info in snapshot.items():
        lines.append(
            f"  - {tk} ({info['label']}): {info['last']} · 5d {info['change_pct_5d']:+.2f}%"
        )
    return "\n".join(lines)


def _render_portfolio(portfolio: Optional[list[dict]]) -> str:
    if not portfolio:
        return ""
    lines = ["", "USER'S ACTIVE POSITIONS (score each one's exposure to this scenario):"]
    for p in portfolio[:40]:  # cap to keep prompt bounded
        ticker = p.get("ticker", "?")
        stype = p.get("strategy_type", "stock")
        qty = p.get("quantity")
        notes = p.get("notes") or ""
        qty_str = f" · qty={qty}" if qty else ""
        notes_str = f" · thesis: {notes[:120]}" if notes else ""
        lines.append(f"  - {ticker} [{stype}]{qty_str}{notes_str}")
    return "\n".join(lines)


def _build_system_prompt(include_portfolio: bool) -> str:
    # The schema below is what the frontend consumes. Keep keys stable.
    schema_portfolio = (
        ',\n  "portfolio_impacts": ['
        '{"ticker": "NVDA", "exposure": "high|moderate|low|neutral", '
        '"direction": "positive|negative|neutral", '
        '"estimated_impact_pct": -12.5, '
        '"reasoning": "1-2 sentence rationale specific to this position"}]'
        if include_portfolio else ""
    )

    return (
        "You are a senior institutional macro strategist at a multi-strategy hedge fund. "
        "A user has proposed a hypothetical scenario. Using the live market snapshot provided "
        "and your training knowledge, produce a rigorous, actionable scenario analysis — "
        "the kind you would circulate to a PM before a risk meeting.\n\n"
        "Your analysis must be:\n"
        "- **Grounded**: reference the current market snapshot where relevant (e.g. 'with VIX at 14 "
        "and HYG compressed, the initial reaction would…').\n"
        "- **Specific**: name concrete tickers (Yahoo Finance format), give numeric magnitudes where "
        "possible (e.g. 'SPY -8 to -12%', '10Y +60bps').\n"
        "- **Multi-order**: cover direct winners/losers AND second-order knock-on effects "
        "(supply chains, substitute goods, funding spillovers, FX pairs).\n"
        "- **Falsifiable**: state the key assumptions that must hold and invalidation signals that "
        "would disprove the thesis.\n"
        "- **Actionable**: suggest specific hedges (tickers + direction) to neutralize unwanted exposure.\n\n"
        "You MUST return ONLY valid JSON matching this exact schema. Do not include markdown fences.\n\n"
        "{\n"
        '  "executive_thesis": "3-5 sentence summary: what happens and why, in plain English.",\n'
        '  "scenario_probability_pct": 15,   // your subjective odds the scenario plays out in the stated horizon\n'
        '  "time_horizon_estimate": "e.g. 1-3 months / 6-12 months",\n'
        '  "historical_analogue": "Reference prior episode with similar dynamics (e.g. 2015 CHF unpeg, 1998 LTCM, Aug 2015 yuan devaluation). Explain what rhymes.",\n'
        '  "key_assumptions": ["Assumption 1 that must hold…", "Assumption 2…"],\n'
        '  "key_catalysts": ["Specific events/datapoints to watch that confirm the thesis is playing out"],\n'
        '  "invalidation_signals": ["If X happens, thesis is wrong"],\n'
        '  "sectors": [{"name": "Specific sub-sector (prefer granular, e.g. \'Oilfield Services\' not \'Energy\')", "sentiment": "positive|negative|neutral", "magnitude": "strong|moderate|mild", "reason": "Why this sector moves"}],  // 3-5 entries\n'
        '  "stocks": [{"ticker": "XOM", "sentiment": "positive|negative|neutral", "magnitude": "strong|moderate|mild", "estimated_move_pct": 8.5, "reason": "Concrete position-specific rationale"}],  // 6-10 entries\n'
        '  "etfs": [{"ticker": "XLE", "sentiment": "positive|negative|neutral", "magnitude": "strong|moderate|mild", "reason": "Why this ETF captures the theme"}],  // 3-6 entries\n'
        '  "second_order_effects": [{"area": "e.g. Auto OEMs via commodity pass-through", "description": "What happens and why it\'s downstream"}],\n'
        '  "cross_asset_reactions": {\n'
        '    "equities": "Expected SPY/QQQ direction + approximate magnitude",\n'
        '    "rates_10y": "Expected UST 10Y move in bps",\n'
        '    "usd": "DXY direction + magnitude",\n'
        '    "oil": "Brent/WTI direction + magnitude",\n'
        '    "gold": "Direction + magnitude",\n'
        '    "vix": "Expected VIX level",\n'
        '    "credit_spreads": "HY OAS direction (tighter/wider + bps)"\n'
        '  },\n'
        '  "suggested_hedges": [{"action": "buy|sell|long|short", "instrument": "VIX calls / TLT / SPY puts / etc.", "rationale": "Why this hedge fits"}]'
        f"{schema_portfolio}\n"
        "}"
    )


async def analyze_thematic_impact(
    scenario_text: str,
    openai_key: str,
    time_horizon: str = "1-3 months",
    portfolio: Optional[list[dict]] = None,
) -> dict:
    """Institutional-style scenario analysis.

    Parameters
    ----------
    scenario_text : str
        User-written hypothetical (e.g. "Fed cuts 200bps emergency").
    openai_key : str
        The caller's OpenAI API key.
    time_horizon : str
        Free-form horizon hint surfaced to the LLM ("1 week", "6 months", etc.).
    portfolio : list[dict], optional
        User's active positions. Each dict: {ticker, strategy_type, quantity, notes}.
        When provided, the LLM returns per-position impact scoring.
    """
    if not scenario_text or len(scenario_text.strip()) < 5:
        return {"error": "Please provide a valid scenario description (min 5 chars)."}

    # Fetch market snapshot off the event loop — yfinance is blocking.
    snapshot = await asyncio.to_thread(_fetch_market_snapshot_sync)

    include_portfolio = bool(portfolio)
    system_prompt = _build_system_prompt(include_portfolio=include_portfolio)

    user_prompt_parts = [
        f"HYPOTHETICAL SCENARIO:\n{scenario_text}",
        f"\nTIME HORIZON: {time_horizon}",
        "\n" + _render_snapshot(snapshot),
    ]
    if include_portfolio:
        user_prompt_parts.append(_render_portfolio(portfolio))
    user_prompt_parts.append(
        "\nProduce the scenario analysis JSON. Be concrete with numbers and named tickers. "
        "If the scenario is implausible given the market snapshot, still analyse it but mark "
        "scenario_probability_pct accordingly."
    )
    user_prompt = "\n".join(user_prompt_parts)

    try:
        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ]
        llm_response = await call_llm(
            api_key=openai_key,
            model="gpt-4o",
            messages=messages,
            max_tokens=3500,
            expect_json=True
        )
        clean = llm_response.strip()
        if clean.startswith("```json"):
            clean = clean[7:]
        if clean.startswith("```"):
            clean = clean[3:]
        if clean.endswith("```"):
            clean = clean[:-3]
        parsed = json.loads(clean.strip())
    except json.JSONDecodeError as exc:
        logger.error(f"What-If LLM returned invalid JSON: {exc}")
        return {"error": "LLM returned malformed JSON. Try rephrasing your scenario."}
    except Exception as exc:
        logger.error(f"What-If analysis failed: {exc}")
        return {"error": f"Failed to analyze scenario with LLM: {exc}. Please check your OpenAI API Key."}

    return {
        "success": True,
        "analysis": parsed,
        "market_snapshot": snapshot,
        "metadata": {
            "time_horizon": time_horizon,
            "portfolio_included": include_portfolio,
            "portfolio_size": len(portfolio) if portfolio else 0,
        },
    }
