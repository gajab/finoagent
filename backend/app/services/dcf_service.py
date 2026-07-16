"""DCF (Discounted Cash Flow) analysis service.

Fetches historical financial data from yfinance and calculates
intelligent defaults for a user-interactive DCF model.
"""

import asyncio
import math
import logging
from datetime import datetime

import numpy as np
import yfinance as yf

logger = logging.getLogger(__name__)

# Industry average WACCs (approximate)
INDUSTRY_WACC = {
    "Technology": 10.0,
    "Healthcare": 9.0,
    "Financial Services": 8.5,
    "Communication Services": 9.5,
    "Consumer Cyclical": 9.5,
    "Consumer Defensive": 7.5,
    "Energy": 10.5,
    "Industrials": 9.0,
    "Basic Materials": 10.0,
    "Real Estate": 7.0,
    "Utilities": 6.0,
}

RISK_FREE_RATE = 4.25  # ~10yr Treasury yield proxy


def _fmt_num(val: float) -> str:
    """Format large numbers for human-readable reasoning text."""
    a = abs(val)
    sign = "-" if val < 0 else ""
    if a >= 1e12:
        return f"{sign}${a / 1e12:.2f}T"
    if a >= 1e9:
        return f"{sign}${a / 1e9:.1f}B"
    if a >= 1e6:
        return f"{sign}${a / 1e6:.0f}M"
    return f"{sign}${a:,.0f}"


def _compute_peg_pegy(info: dict, revenue_growth_rates: list, fcf_growth_rates: list) -> dict:
    """Compute PEG and PEGY ratios with multiple growth rate methods.

    PEG  = (P/E) / Earnings Growth Rate
    PEGY = (P/E) / (Earnings Growth Rate + Dividend Yield)

    A PEG < 1 is often considered undervalued relative to growth.
    PEGY adjusts for dividend-paying stocks (Peter Lynch's preferred metric).
    """
    trailing_pe = info.get("trailingPE")
    forward_pe = info.get("forwardPE")
    trailing_eps = info.get("trailingEps")
    forward_eps = info.get("forwardEps")
    eps_current_year = info.get("epsCurrentYear")  # current-FY analyst estimate
    dividend_yield = info.get("dividendYield")  # e.g. 0.0065 for 0.65%
    yfinance_peg = info.get("pegRatio") or info.get("trailingPegRatio")

    # Convert dividend yield to percentage
    div_yield_pct = round(dividend_yield * 100, 2) if dividend_yield else 0.0

    # ---- Calculate EPS growth rate (forward vs trailing) ----
    eps_growth_rate = None
    if trailing_eps and forward_eps and trailing_eps > 0:
        eps_growth_rate = round(((forward_eps - trailing_eps) / abs(trailing_eps)) * 100, 2)

    # ---- Compute PEG from different growth sources ----
    peg_variants: list[dict] = []

    # Method 1: Trailing P/E with EPS growth (forward vs trailing)
    if trailing_pe and eps_growth_rate and eps_growth_rate > 0:
        peg_val = round(trailing_pe / eps_growth_rate, 2)
        peg_variants.append({
            "method": "Trailing P/E ÷ EPS Growth",
            "pe_used": round(trailing_pe, 2),
            "growth_used": eps_growth_rate,
            "growth_source": "Forward (FY+1) vs trailing TTM EPS — spans ~2 FYs",
            "peg": peg_val,
            "interpretation": _peg_interpretation(peg_val),
        })

    # Method 2: Forward P/E with EPS growth
    if forward_pe and eps_growth_rate and eps_growth_rate > 0:
        peg_val = round(forward_pe / eps_growth_rate, 2)
        peg_variants.append({
            "method": "Forward P/E ÷ EPS Growth",
            "pe_used": round(forward_pe, 2),
            "growth_used": eps_growth_rate,
            "growth_source": "Forward (FY+1) vs trailing TTM EPS — spans ~2 FYs",
            "peg": peg_val,
            "interpretation": _peg_interpretation(peg_val),
        })

    # Method 3: Trailing P/E with historical revenue growth
    avg_rev_growth = float(np.mean(revenue_growth_rates)) if revenue_growth_rates else None
    if trailing_pe and avg_rev_growth and avg_rev_growth > 0:
        peg_val = round(trailing_pe / avg_rev_growth, 2)
        peg_variants.append({
            "method": "Trailing P/E ÷ Revenue Growth",
            "pe_used": round(trailing_pe, 2),
            "growth_used": round(avg_rev_growth, 2),
            "growth_source": f"Avg revenue growth ({len(revenue_growth_rates)}yr)",
            "peg": peg_val,
            "interpretation": _peg_interpretation(peg_val),
        })

    # Method 4: Forward P/E with per-annum forward EPS growth (current-FY → next-FY).
    # This avoids the trailing-TTM → forward jump that conflates a GAAP trailing
    # base with an adjusted forward estimate over ~2 fiscal years.
    fwd_annual_growth = None
    if eps_current_year and forward_eps and eps_current_year > 0:
        fwd_annual_growth = round(((forward_eps - eps_current_year) / abs(eps_current_year)) * 100, 2)
    if forward_pe and fwd_annual_growth and fwd_annual_growth > 0:
        peg_val = round(forward_pe / fwd_annual_growth, 2)
        peg_variants.append({
            "method": "Forward P/E ÷ Next-Year EPS Growth",
            "pe_used": round(forward_pe, 2),
            "growth_used": fwd_annual_growth,
            "growth_source": "Current-FY → next-FY estimate (analyst, per-year)",
            "peg": peg_val,
            "interpretation": _peg_interpretation(peg_val),
        })

    # Method 5: Consensus PEG (Yahoo / Morningstar basis) — uses a long-term
    # (~5yr) expected growth rate, which is what most data providers report.
    # It is the most consistent with external sources, so it anchors the
    # headline number rather than the distortion-prone 1-year variants.
    if yfinance_peg and yfinance_peg > 0:
        implied_growth = round(trailing_pe / yfinance_peg, 2) if trailing_pe else 0
        peg_variants.append({
            "method": "Consensus PEG (Yahoo / Morningstar basis)",
            "pe_used": round(trailing_pe, 2) if trailing_pe else 0,
            "growth_used": implied_growth,
            "growth_source": "Long-term (~5yr) expected EPS growth",
            "peg": round(yfinance_peg, 2),
            "interpretation": _peg_interpretation(yfinance_peg),
        })

    # ---- Primary PEG (headline) ----
    # Prefer the consensus / long-term-growth PEG (matches Morningstar & Yahoo),
    # then the per-annum forward variant.  The trailing-vs-forward variants can
    # read far too cheap when trailing GAAP EPS is depressed by one-time items —
    # that inflates the implied growth rate and makes a naive PEG look like a
    # screaming bargain when it isn't.
    def _find_variant(method_substr: str):
        return next((v for v in peg_variants if method_substr in v["method"]), None)

    primary = (
        _find_variant("Consensus PEG")
        or _find_variant("Next-Year EPS Growth")
        or (peg_variants[0] if peg_variants else None)
    )
    primary_peg = primary["peg"] if primary else None
    primary_peg_source = primary["method"] if primary else None

    # Keep the headline variant first so the UI's gauge value and the caption it
    # reads from peg_variants[0] stay in sync.
    if primary and peg_variants and peg_variants[0] is not primary:
        peg_variants.remove(primary)
        peg_variants.insert(0, primary)

    # Flag when the naive forward-vs-trailing EPS growth is implausibly high
    # relative to the per-year analyst growth — a sign trailing EPS is depressed
    # (one-time items / GAAP-vs-adjusted basis) and a simple PEG reads too cheap.
    eps_growth_caveat = bool(
        eps_growth_rate and fwd_annual_growth and eps_growth_rate > (fwd_annual_growth * 1.5 + 10)
    )

    # ---- PEGY Ratio ----
    # PEGY = P/E ÷ (EPS Growth + Dividend Yield)
    pegy = None
    pegy_details = None
    growth_for_pegy = eps_growth_rate if eps_growth_rate and eps_growth_rate > 0 else (
        round(avg_rev_growth, 2) if avg_rev_growth and avg_rev_growth > 0 else None
    )
    pe_for_pegy = trailing_pe

    if pe_for_pegy and growth_for_pegy and (growth_for_pegy + div_yield_pct) > 0:
        pegy_val = round(pe_for_pegy / (growth_for_pegy + div_yield_pct), 2)
        pegy = pegy_val
        pegy_details = {
            "pe_used": round(pe_for_pegy, 2),
            "growth_used": growth_for_pegy,
            "dividend_yield_pct": div_yield_pct,
            "denominator": round(growth_for_pegy + div_yield_pct, 2),
            "pegy": pegy_val,
            "interpretation": _pegy_interpretation(pegy_val),
        }

    return {
        "trailing_pe": round(trailing_pe, 2) if trailing_pe else None,
        "forward_pe": round(forward_pe, 2) if forward_pe else None,
        "trailing_eps": round(trailing_eps, 2) if trailing_eps else None,
        "forward_eps": round(forward_eps, 2) if forward_eps else None,
        "eps_growth_rate": eps_growth_rate,
        "dividend_yield_pct": div_yield_pct,
        "yfinance_peg": round(yfinance_peg, 2) if yfinance_peg else None,
        "primary_peg": primary_peg,
        "primary_peg_source": primary_peg_source,
        "eps_growth_caveat": eps_growth_caveat,
        "peg_variants": peg_variants,
        "pegy": pegy,
        "pegy_details": pegy_details,
    }


def _peg_interpretation(peg: float) -> str:
    """Human-readable PEG interpretation."""
    if peg < 0:
        return "Negative — earnings declining or negative P/E"
    if peg < 0.5:
        return "Deeply undervalued relative to growth"
    if peg < 1.0:
        return "Potentially undervalued — growth exceeds valuation premium"
    if peg < 1.5:
        return "Fairly valued relative to growth"
    if peg < 2.0:
        return "Slightly overvalued — premium above growth rate"
    return "Significantly overvalued relative to earnings growth"


def _pegy_interpretation(pegy: float) -> str:
    """Human-readable PEGY interpretation (Peter Lynch's dividend-adjusted PEG)."""
    if pegy < 0:
        return "Negative — earnings declining"
    if pegy < 0.5:
        return "Highly attractive — growth + yield far exceed valuation"
    if pegy < 1.0:
        return "Attractive — growth plus dividends justify the price"
    if pegy < 1.5:
        return "Fair value when accounting for dividends"
    if pegy < 2.0:
        return "Moderately expensive even with dividend adjustment"
    return "Expensive — valuation not justified by growth + yield"


def _run_dcf_sync(ticker: str, risk_free: float | None = None) -> dict:
    """Build a DCF analysis with smart defaults for the given ticker."""
    rf = float(risk_free) if risk_free else RISK_FREE_RATE
    stock = yf.Ticker(ticker)
    info = stock.info or {}

    result: dict = {"ticker": ticker.upper()}

    # ---- Basic info ----
    company_name = info.get("shortName") or info.get("longName") or ticker.upper()
    sector = info.get("sector", "Unknown")
    industry = info.get("industry", "Unknown")
    current_price = info.get("currentPrice") or info.get("regularMarketPrice") or 0
    shares_outstanding = info.get("sharesOutstanding") or 0
    market_cap = info.get("marketCap") or (current_price * shares_outstanding)
    beta = info.get("beta") or 1.0
    trailing_pe = info.get("trailingPE")
    forward_pe = info.get("forwardPE")

    result["company_name"] = company_name
    result["sector"] = sector
    result["industry"] = industry
    result["current_price"] = round(float(current_price), 2)
    result["shares_outstanding"] = int(shares_outstanding)
    result["market_cap"] = round(float(market_cap), 2)
    result["beta"] = round(float(beta), 2)
    result["trailing_pe"] = round(float(trailing_pe), 2) if trailing_pe else None
    result["forward_pe"] = round(float(forward_pe), 2) if forward_pe else None

    # ---- Fetch historical financials ----
    cashflow = stock.cashflow
    income_stmt = stock.income_stmt
    balance_sheet = stock.balance_sheet

    # Extract FCF history (up to 4 years)
    fcf_history: list[dict] = []
    if cashflow is not None and not cashflow.empty:
        for col in list(cashflow.columns)[:4]:
            year = str(col.year) if hasattr(col, "year") else str(col)
            fcf_val = None
            for key in ["Free Cash Flow", "FreeCashFlow"]:
                if key in cashflow.index:
                    val = cashflow.loc[key, col]
                    if val is not None and not (isinstance(val, float) and math.isnan(val)):
                        fcf_val = float(val)
                        break
            fcf_history.append({"year": year, "value": fcf_val})

    result["fcf_history"] = fcf_history

    # Extract revenue history for growth rate calculation
    revenue_history: list[dict] = []
    if income_stmt is not None and not income_stmt.empty:
        for col in list(income_stmt.columns)[:4]:
            year = str(col.year) if hasattr(col, "year") else str(col)
            rev_val = None
            for key in ["Total Revenue", "TotalRevenue"]:
                if key in income_stmt.index:
                    val = income_stmt.loc[key, col]
                    if val is not None and not (isinstance(val, float) and math.isnan(val)):
                        rev_val = float(val)
                        break
            revenue_history.append({"year": year, "value": rev_val})

    result["revenue_history"] = revenue_history

    # Net income history
    net_income_history: list[dict] = []
    if income_stmt is not None and not income_stmt.empty:
        for col in list(income_stmt.columns)[:4]:
            year = str(col.year) if hasattr(col, "year") else str(col)
            ni_val = None
            for key in ["Net Income", "NetIncome"]:
                if key in income_stmt.index:
                    val = income_stmt.loc[key, col]
                    if val is not None and not (isinstance(val, float) and math.isnan(val)):
                        ni_val = float(val)
                        break
            net_income_history.append({"year": year, "value": ni_val})

    result["net_income_history"] = net_income_history

    # ---- Extract balance-sheet items for net debt ----
    total_debt = 0.0
    cash_and_equivalents = 0.0

    if balance_sheet is not None and not balance_sheet.empty:
        latest_bs = balance_sheet.columns[0]  # most recent period

        # Total Debt
        for key in ["Total Debt", "TotalDebt", "Long Term Debt", "LongTermDebt"]:
            if key in balance_sheet.index:
                val = balance_sheet.loc[key, latest_bs]
                if val is not None and not (isinstance(val, float) and math.isnan(val)):
                    total_debt = float(val)
                    break

        # If we only found long-term, try adding short-term / current debt
        if total_debt > 0:
            for key in ["Current Debt", "CurrentDebt", "Short Long Term Debt",
                        "ShortLongTermDebt", "Current Portion Of Long Term Debt"]:
                if key in balance_sheet.index:
                    val = balance_sheet.loc[key, latest_bs]
                    if val is not None and not (isinstance(val, float) and math.isnan(val)):
                        # Only add if we used LongTermDebt as base (avoid double-counting)
                        if "Long" in [k for k in ["Long Term Debt", "LongTermDebt"] if k in balance_sheet.index]:
                            total_debt += float(val)
                        break

        # Cash & Short-Term Investments
        for key in ["Cash And Cash Equivalents", "CashAndCashEquivalents",
                     "Cash Cash Equivalents And Short Term Investments",
                     "CashCashEquivalentsAndShortTermInvestments"]:
            if key in balance_sheet.index:
                val = balance_sheet.loc[key, latest_bs]
                if val is not None and not (isinstance(val, float) and math.isnan(val)):
                    cash_and_equivalents = float(val)
                    break

        # Fallback: try just "Cash" if nothing found
        if cash_and_equivalents == 0:
            for key in ["Cash", "Cash And Short Term Investments"]:
                if key in balance_sheet.index:
                    val = balance_sheet.loc[key, latest_bs]
                    if val is not None and not (isinstance(val, float) and math.isnan(val)):
                        cash_and_equivalents = float(val)
                        break

    # Also try info dict as fallback
    if total_debt == 0:
        total_debt = float(info.get("totalDebt", 0) or 0)
    if cash_and_equivalents == 0:
        cash_and_equivalents = float(info.get("totalCash", 0) or 0)

    net_debt = total_debt - cash_and_equivalents

    result["total_debt"] = round(total_debt, 0)
    result["cash_and_equivalents"] = round(cash_and_equivalents, 0)
    result["net_debt"] = round(net_debt, 0)

    # ---- Calculate intelligent defaults ----

    # 1. FCF growth rate — based on historical FCF trend
    fcf_values = [f["value"] for f in fcf_history if f["value"] is not None and f["value"] > 0]
    fcf_values.reverse()  # oldest first
    rev_values = [r["value"] for r in revenue_history if r["value"] is not None and r["value"] > 0]
    rev_values.reverse()

    revenue_growth_rates = []
    if len(rev_values) >= 2:
        for i in range(1, len(rev_values)):
            if rev_values[i - 1] > 0:
                g = (rev_values[i] - rev_values[i - 1]) / rev_values[i - 1] * 100
                revenue_growth_rates.append(g)

    fcf_growth_rates = []
    if len(fcf_values) >= 2:
        for i in range(1, len(fcf_values)):
            if fcf_values[i - 1] > 0:
                g = (fcf_values[i] - fcf_values[i - 1]) / fcf_values[i - 1] * 100
                fcf_growth_rates.append(g)

    # Default growth rate: weighted average of FCF growth and revenue growth
    avg_rev_growth = np.mean(revenue_growth_rates) if revenue_growth_rates else 0
    avg_fcf_growth = np.mean(fcf_growth_rates) if fcf_growth_rates else avg_rev_growth

    # Clamp to reasonable range and use a conservative blend
    default_growth = float(np.clip(avg_fcf_growth * 0.6 + avg_rev_growth * 0.4, -10, 40))
    default_growth = round(default_growth, 1)

    # 2. Discount rate (WACC proxy)
    # CAPM: WACC ≈ Rf + β(Rm-Rf), with Rm-Rf ≈ 5.5% equity premium
    capm_cost_equity = rf + beta * 5.5
    industry_wacc = INDUSTRY_WACC.get(sector, 9.0)
    # Blend CAPM and industry average
    default_discount_rate = round((capm_cost_equity * 0.6 + industry_wacc * 0.4), 1)
    default_discount_rate = float(np.clip(default_discount_rate, 5.0, 18.0))

    # 3. Terminal growth rate (GDP-like long-term growth)
    default_terminal_growth = 2.5  # Conservative perpetuity growth rate

    # 4. Projection years
    default_projection_years = 5

    # 5. Starting FCF (most recent)
    latest_fcf = fcf_values[-1] if fcf_values else 0

    # ---- Two-stage / CAP engine inputs (fraction units) ----
    terminal_frac = default_terminal_growth / 100.0
    wacc_frac = default_discount_rate / 100.0
    rev_cagr = _cagr(rev_values)
    fcf_cagr = _cagr(fcf_values)
    _fwd = info.get("revenueGrowth")
    fwd_growth = float(_fwd) if isinstance(_fwd, (int, float)) else None
    yoy_std = (float(np.std(revenue_growth_rates)) / 100.0) if len(revenue_growth_rates) >= 2 else None
    latest_rev = rev_values[-1] if rev_values else None
    # Normalized base: mean of last up-to-3 positive FCF years smooths a one-off dip.
    _recent_fcf = [f for f in fcf_values[-3:] if f and f > 0]
    base_fcf = float(np.mean(_recent_fcf)) if _recent_fcf else float(latest_fcf)
    fcf_margin = (base_fcf / latest_rev) if (latest_rev and latest_rev > 0) else None
    stage1_frac = _stage1_growth(rev_cagr, fcf_cagr, fwd_growth, terminal_frac)
    cap_years = _cap_years(latest_rev, rev_cagr, yoy_std, fcf_margin)
    exit_mult = _exit_multiple(fcf_margin, rev_cagr)

    # ---- Build reasoning ----
    reasoning: list[str] = []

    # Growth rate reasoning
    if revenue_growth_rates:
        avg_rg = round(float(np.mean(revenue_growth_rates)), 1)
        reasoning.append(
            f"Revenue Growth: {avg_rg}% avg over {len(revenue_growth_rates)} years → "
            f"suggests company is {'growing well' if avg_rg > 10 else 'growing steadily' if avg_rg > 0 else 'declining'}."
        )
    if fcf_growth_rates:
        avg_fg = round(float(np.mean(fcf_growth_rates)), 1)
        reasoning.append(
            f"FCF Growth: {avg_fg}% avg → "
            f"{'strong cash generation growth' if avg_fg > 15 else 'moderate cash flow growth' if avg_fg > 0 else 'declining cash flows, using conservative estimate'}."
        )
    reasoning.append(
        f"Default growth rate set to {default_growth}% (blended 60% FCF + 40% revenue growth, clamped)."
    )

    # Discount rate reasoning
    reasoning.append(
        f"WACC estimate: CAPM gives {round(capm_cost_equity, 1)}% (β={beta}, Rf={round(rf,2)}% live 10-Y, ERP=5.5%). "
        f"Industry avg ({sector}): {industry_wacc}%. Blended: {default_discount_rate}%."
    )

    # Terminal growth reasoning
    reasoning.append(
        f"Terminal growth: {default_terminal_growth}% (conservative, near long-term GDP growth)."
    )

    # Net debt reasoning
    if net_debt > 0:
        reasoning.append(
            f"Net Debt: {_fmt_num(total_debt)} total debt − {_fmt_num(cash_and_equivalents)} cash = "
            f"{_fmt_num(net_debt)} net debt (subtracted from enterprise value to get equity value)."
        )
    elif net_debt < 0:
        reasoning.append(
            f"Net Cash Position: {_fmt_num(cash_and_equivalents)} cash − {_fmt_num(total_debt)} debt = "
            f"{_fmt_num(abs(net_debt))} excess cash (added to enterprise value — company has more cash than debt)."
        )
    else:
        reasoning.append("Net Debt: ~$0 (debt roughly equals cash holdings).")

    # Valuation context
    if trailing_pe:
        reasoning.append(f"Trailing P/E: {round(trailing_pe, 1)}x — {'premium valuation' if trailing_pe > 30 else 'moderate valuation' if trailing_pe > 15 else 'value territory'}.")
    if forward_pe:
        reasoning.append(f"Forward P/E: {round(forward_pe, 1)}x — market expects {'growth' if forward_pe < (trailing_pe or 999) else 'slowdown'}.")

    result["defaults"] = {
        "fcf_growth_rate": round(stage1_frac * 100, 1),
        "discount_rate": default_discount_rate,
        "terminal_growth_rate": default_terminal_growth,
        "projection_years": cap_years,
        "starting_fcf": round(base_fcf, 0) if base_fcf else 0,
        "net_debt": round(net_debt, 0),
    }

    avg_hist_fcf_str = f"{round(float(np.mean(fcf_growth_rates)), 1)}%" if fcf_growth_rates else "Unknown"
    result["suggestions"] = {
        "fcf_growth_rate": f"Company's historic FCF growth is {avg_hist_fcf_str}. Mature firms typically maintain 5-10%, while hyper-growth tech can sustain 15-25%. Be conservative.",
        "discount_rate": f"Industry baseline for {sector} is {round(industry_wacc, 1)}%. Given the {RISK_FREE_RATE}% risk-free rate proxy in this inflation environment, a standard sensitivity range to test is {max(6.0, round(industry_wacc - 1.0, 1))}% - {round(industry_wacc + 2.0, 1)}%.",
        "terminal_growth_rate": "Should mirror long-term macroeconomic GDP and inflation targets. A sustainable range is 2.0% - 3.0%. Anything higher structurally implies the business will outgrow the economy forever."
    }

    result["reasoning"] = reasoning

    # ---- PEG & PEGY Ratio Analysis ----
    peg_analysis = _compute_peg_pegy(info, revenue_growth_rates, fcf_growth_rates)
    result["peg_analysis"] = peg_analysis

    # ---- Compute headline DCF valuation (two-stage / CAP + range + reverse-DCF) ----
    result["valuation"] = _compute_dcf_2stage(
        base_fcf=base_fcf,
        stage1=stage1_frac,
        terminal=terminal_frac,
        cap=cap_years,
        wacc=wacc_frac,
        exit_mult=exit_mult,
        shares=shares_outstanding,
        net_debt=net_debt,
        current_price=float(current_price),
    )

    return result


# ===========================================================================
# Two-stage / CAP DCF engine (headline valuation)
# ---------------------------------------------------------------------------
# Everything below works in FRACTIONS (0.08 = 8%). It fixes the naive single-
# stage model that extrapolated a one-year FCF dip into a perpetual negative
# growth rate. Key ideas: normalized FCF base, forward-looking stage-1 growth
# floored at terminal, a data-driven competitive-advantage period (CAP) that
# fades growth to terminal, terminal value cross-checked two ways (Gordon +
# exit multiple), a reverse-DCF market-implied growth, and a fair-value range.
# ===========================================================================

def _cagr(vals: list[float]) -> float | None:
    v = [x for x in vals if x and x > 0]
    if len(v) < 2:
        return None
    return (v[-1] / v[0]) ** (1 / (len(v) - 1)) - 1


def _stage1_growth(rev_cagr, fcf_cagr, fwd_growth, terminal: float) -> float:
    """Forward-looking near-term growth, floored at terminal — a profitable
    franchise must not be projected to shrink forever off one weak year."""
    g, w = 0.0, 0.0
    if fwd_growth is not None:
        g += 0.45 * fwd_growth; w += 0.45
    if rev_cagr is not None:
        g += 0.40 * rev_cagr; w += 0.40
    if fcf_cagr is not None:
        g += 0.15 * fcf_cagr; w += 0.15
    if w == 0:
        return max(terminal, 0.05)
    return float(np.clip(g / w, terminal, 0.40))


def _cap_years(revenue, rev_cagr, yoy_std, fcf_margin) -> int:
    """Competitive-advantage period: how long above-terminal growth persists.
    Bigger company → shorter (law of large numbers); steadier high growth and
    fatter margins (moat proxy) → longer."""
    yrs = 5.0
    if revenue and revenue > 500e9:
        yrs -= 3
    elif revenue and revenue > 100e9:
        yrs -= 2
    elif revenue and revenue > 20e9:
        yrs -= 1
    if rev_cagr is not None and rev_cagr > 0.15 and (yoy_std is None or yoy_std < 0.15):
        yrs += 2
    elif rev_cagr is not None and rev_cagr > 0.08:
        yrs += 1
    if fcf_margin is not None and fcf_margin > 0.20:
        yrs += 1
    return int(np.clip(yrs, 3, 12))


def _exit_multiple(fcf_margin, rev_cagr) -> float:
    """Terminal EV/FCF — an INDEPENDENT anchor (not derived from WACC−g) so it
    genuinely cross-checks the Gordon terminal. Base = mature-market ~14x, lifted
    for durable high-margin/high-growth franchises."""
    m = 14.0
    if fcf_margin is not None and fcf_margin > 0.20:
        m += 3
    if rev_cagr is not None and rev_cagr > 0.15:
        m += 3
    return float(np.clip(m, 10, 24))


_FADE_YEARS = 5   # explicit fade window AFTER the competitive-advantage period


def _project_fcfs(base_fcf, stage1, terminal, cap) -> list[float]:
    """Two-stage: constant stage-1 growth for `cap` years, then a linear fade to
    terminal over the next _FADE_YEARS. Growth stays up through the moat window
    and only mean-reverts afterward (not instantly)."""
    fcfs, fcf = [], base_fcf
    for t in range(1, cap + _FADE_YEARS + 1):
        g = stage1 if t <= cap else stage1 + (terminal - stage1) * ((t - cap) / _FADE_YEARS)
        fcf *= (1 + g)
        fcfs.append(fcf)
    return fcfs


def _dcf_equity(base_fcf, stage1, terminal, cap, wacc, exit_mult, shares, net_debt) -> dict:
    fcfs = _project_fcfs(base_fcf, stage1, terminal, cap)
    n = len(fcfs)
    pvs = [f / ((1 + wacc) ** (t + 1)) for t, f in enumerate(fcfs)]
    total_pv = sum(pvs)
    gordon_tv = (fcfs[-1] * (1 + terminal) / (wacc - terminal)) if wacc > terminal else 0.0
    exit_tv = fcfs[-1] * exit_mult
    tv = 0.5 * gordon_tv + 0.5 * exit_tv          # blend the two terminal methods
    pv_tv = tv / ((1 + wacc) ** n)
    ev = total_pv + pv_tv
    equity = ev - net_debt
    return {
        "fcfs": fcfs, "pvs": pvs, "total_pv": total_pv,
        "gordon_tv": gordon_tv, "exit_tv": exit_tv, "tv": tv, "pv_tv": pv_tv,
        "ev": ev, "equity": equity,
        "fair_value": (equity / shares) if shares else 0.0,
        "terminal_pct": (pv_tv / ev) if ev else None,
    }


def _implied_growth(base_fcf, terminal, wacc, exit_mult, shares, net_debt,
                    target_price, years: int = 10) -> float | None:
    """Reverse-DCF: the CONSTANT annual FCF growth over `years` that reprices the
    equity to today's market price. The 'expectations' number the debate argues."""
    if not (base_fcf and base_fcf > 0 and shares and target_price and target_price > 0 and wacc > terminal):
        return None

    def fv(g: float) -> float:
        fcf, pv = base_fcf, 0.0
        for t in range(1, years + 1):
            fcf *= (1 + g)
            pv += fcf / ((1 + wacc) ** t)
        tv = 0.5 * (fcf * (1 + terminal) / (wacc - terminal)) + 0.5 * (fcf * exit_mult)
        pv += tv / ((1 + wacc) ** years)
        return (pv - net_debt) / shares

    lo, hi = -0.10, 0.50
    if target_price <= fv(lo):
        return -0.10
    if target_price >= fv(hi):
        return 0.50
    for _ in range(50):
        mid = (lo + hi) / 2
        if fv(mid) < target_price:
            lo = mid
        else:
            hi = mid
    return (lo + hi) / 2


def _compute_dcf_2stage(base_fcf, stage1, terminal, cap, wacc, exit_mult,
                        shares, net_debt, current_price) -> dict:
    if base_fcf <= 0 or shares <= 0 or wacc <= terminal:
        return {
            "projected_fcfs": [], "present_values": [], "total_pv_fcf": 0,
            "terminal_value": 0, "pv_terminal_value": 0, "enterprise_value": 0,
            "net_debt": round(net_debt, 0), "equity_value": 0, "fair_value_per_share": 0,
            "error": "Cannot compute DCF — negative/zero FCF base or invalid rates.",
            "flags": ["DCF not computable (negative FCF base or WACC ≤ terminal growth)"],
        }

    base = _dcf_equity(base_fcf, stage1, terminal, cap, wacc, exit_mult, shares, net_debt)

    # Fair-value range: bear = lower growth / higher WACC / shorter CAP; bull = opposite.
    fv_bear = _dcf_equity(base_fcf, terminal + 0.5 * (stage1 - terminal), terminal,
                          max(3, cap - 2), wacc + 0.015, exit_mult, shares, net_debt)["fair_value"]
    fv_bull = _dcf_equity(base_fcf, float(np.clip(stage1 * 1.4 if stage1 > 0 else stage1 + 0.03, terminal, 0.45)),
                          terminal, min(12, cap + 2), max(terminal + 0.005, wacc - 0.015),
                          exit_mult, shares, net_debt)["fair_value"]
    low, high = sorted((fv_bear, fv_bull))

    implied = _implied_growth(base_fcf, terminal, wacc, exit_mult, shares, net_debt, current_price)

    flags: list[str] = []
    if stage1 <= terminal + 0.005:
        flags.append("near-zero excess growth — value is essentially terminal only")
    if base["terminal_pct"] and base["terminal_pct"] > 0.85:
        flags.append(f"terminal value is {base['terminal_pct']*100:.0f}% of EV — highly assumption-dependent")
    if base["gordon_tv"] and base["exit_tv"]:
        div = abs(base["gordon_tv"] - base["exit_tv"]) / max(base["gordon_tv"], base["exit_tv"])
        if div > 0.40:
            flags.append(f"Gordon vs exit-multiple terminal disagree by {div*100:.0f}%")
    # Reframe the market-vs-model gap as the reverse-DCF expectation (the debatable number),
    # not a noisy "diverges X%" that fires on every quality name.
    if implied is not None and implied > 0.20:
        flags.append(f"market prices in ~{implied*100:.0f}% FCF growth for 10y — priced for exceptional execution")
    elif implied is not None and (stage1 - implied) > 0.05:
        flags.append(f"market implies only ~{implied*100:.0f}% FCF growth vs the model's {stage1*100:.0f}% — possible value setup")

    return {
        # ---- DCFValuation fields the DCF page already renders ----
        "projected_fcfs": [round(f, 0) for f in base["fcfs"]],
        "present_values": [round(p, 0) for p in base["pvs"]],
        "total_pv_fcf": round(base["total_pv"], 0),
        "terminal_value": round(base["tv"], 0),
        "pv_terminal_value": round(base["pv_tv"], 0),
        "enterprise_value": round(base["ev"], 0),
        "net_debt": round(net_debt, 0),
        "equity_value": round(base["equity"], 0),
        "fair_value_per_share": round(base["fair_value"], 2),
        # ---- New two-stage / robustness fields ----
        "fair_value_low": round(low, 2),
        "fair_value_high": round(high, 2),
        "market_implied_growth": round(implied * 100, 1) if implied is not None else None,
        "stage1_growth": round(stage1 * 100, 1),
        "cap_years": cap,
        "terminal_growth": round(terminal * 100, 1),
        "wacc": round(wacc * 100, 1),
        "exit_multiple": round(exit_mult, 1),
        "terminal_pct": round(base["terminal_pct"] * 100, 1) if base["terminal_pct"] else None,
        "gordon_terminal": round(base["gordon_tv"], 0),
        "exit_terminal": round(base["exit_tv"], 0),
        "base_fcf": round(base_fcf, 0),
        "upside_pct": round((base["fair_value"] / current_price - 1) * 100, 1) if current_price else None,
        "flags": flags,
    }


def _compute_dcf(
    starting_fcf: float,
    growth_rate: float,
    discount_rate: float,
    terminal_growth: float,
    projection_years: int,
    shares_outstanding: int,
    net_debt: float = 0.0,
) -> dict:
    """Compute DCF fair value given parameters.

    Equity Value = Enterprise Value − Net Debt
    Fair Value / Share = Equity Value / Shares Outstanding

    net_debt > 0 means company owes more than its cash (reduces equity value).
    net_debt < 0 means company has more cash than debt (increases equity value).
    """
    if starting_fcf <= 0 or shares_outstanding <= 0 or discount_rate <= terminal_growth:
        return {
            "projected_fcfs": [],
            "present_values": [],
            "total_pv_fcf": 0,
            "terminal_value": 0,
            "pv_terminal_value": 0,
            "enterprise_value": 0,
            "net_debt": round(net_debt, 0),
            "equity_value": 0,
            "fair_value_per_share": 0,
            "error": "Cannot compute DCF — negative/zero FCF or invalid rates.",
        }

    projected_fcfs: list[float] = []
    present_values: list[float] = []

    for year in range(1, projection_years + 1):
        fcf = starting_fcf * ((1 + growth_rate) ** year)
        pv = fcf / ((1 + discount_rate) ** year)
        projected_fcfs.append(round(fcf, 0))
        present_values.append(round(pv, 0))

    total_pv_fcf = sum(present_values)

    # Terminal value (Gordon Growth Model)
    final_fcf = starting_fcf * ((1 + growth_rate) ** projection_years)
    terminal_fcf = final_fcf * (1 + terminal_growth)
    terminal_value = terminal_fcf / (discount_rate - terminal_growth)
    pv_terminal = terminal_value / ((1 + discount_rate) ** projection_years)

    enterprise_value = total_pv_fcf + pv_terminal

    # Equity Value = Enterprise Value − Net Debt
    equity_value = enterprise_value - net_debt
    fair_value = equity_value / shares_outstanding if shares_outstanding > 0 else 0

    return {
        "projected_fcfs": projected_fcfs,
        "present_values": present_values,
        "total_pv_fcf": round(total_pv_fcf, 0),
        "terminal_value": round(terminal_value, 0),
        "pv_terminal_value": round(pv_terminal, 0),
        "enterprise_value": round(enterprise_value, 0),
        "net_debt": round(net_debt, 0),
        "equity_value": round(equity_value, 0),
        "fair_value_per_share": round(fair_value, 2),
    }


async def _live_risk_free() -> float | None:
    """Live 10-Y Treasury yield (%) from FRED via rates_service; None on failure."""
    try:
        from . import rates_service
        return rates_service.latest(await rates_service.fred_series("DGS10"))
    except Exception:
        return None


async def run_dcf_analysis(ticker: str) -> dict:
    """Async wrapper. Uses the live 10-Y risk-free rate in the WACC when available."""
    rf = await _live_risk_free()
    return await asyncio.to_thread(_run_dcf_sync, ticker, rf)


async def compute_dcf_scenario(
    starting_fcf: float,
    growth_rate: float,
    discount_rate: float,
    terminal_growth: float,
    projection_years: int,
    shares_outstanding: int,
    net_debt: float = 0.0,
) -> dict:
    """Compute a user-customized SINGLE-stage DCF scenario (legacy, kept for compat)."""
    return _compute_dcf(
        starting_fcf=starting_fcf,
        growth_rate=growth_rate / 100,
        discount_rate=discount_rate / 100,
        terminal_growth=terminal_growth / 100,
        projection_years=projection_years,
        shares_outstanding=shares_outstanding,
        net_debt=net_debt,
    )


def dcf_from_override(p: dict) -> dict:
    """Build a full DCF result (``{valuation, market_cap}``) from user-tweaked
    TWO-STAGE params — the exact engine behind the headline. Shared by the DCF
    page's interactive scenario AND the debate (the user's DCF overrides the
    auto-computed anchor). Percentages in, fractions handled internally."""
    shares = int(p.get("shares_outstanding") or 0)
    price = float(p.get("current_price") or 0)
    val = _compute_dcf_2stage(
        base_fcf=float(p.get("base_fcf") or 0),
        stage1=float(p.get("stage1_growth") or 0) / 100,
        terminal=float(p.get("terminal_growth") if p.get("terminal_growth") is not None else 2.5) / 100,
        cap=int(p.get("cap_years") or 4),
        wacc=float(p.get("discount_rate") or 10) / 100,
        exit_mult=float(p.get("exit_multiple") or 16.0),
        shares=shares,
        net_debt=float(p.get("net_debt") or 0),
        current_price=price,
    )
    return {"valuation": val, "market_cap": (price * shares) if (price and shares) else None}
