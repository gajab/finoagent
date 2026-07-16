"""RUPEE framework service.

Assembles the "Bahi-Khata" (ledger) of hard fundamentals that the RUPEE
analysis needs — Rokda (cash), Udhaari (debt), Price, Excellence (quality) and
Earnings (growth) — pulling directly from yfinance ``info`` so the metrics are
the same ones a value investor would open the books to.

The LLM prompt and persistence live in ``stock_router``; this module only
fetches and shapes the numbers.
"""

import asyncio

import yfinance as yf


def _pct(value) -> str:
    """Format a yfinance fraction (e.g. 0.557) as a percentage string."""
    if value is None:
        return "N/A"
    try:
        return f"{float(value) * 100:.1f}%"
    except (TypeError, ValueError):
        return "N/A"


def _money(value) -> str:
    """Human-readable large-dollar formatting."""
    if value is None:
        return "N/A"
    try:
        v = float(value)
    except (TypeError, ValueError):
        return "N/A"
    a = abs(v)
    sign = "-" if v < 0 else ""
    if a >= 1e12:
        return f"{sign}${a / 1e12:.2f}T"
    if a >= 1e9:
        return f"{sign}${a / 1e9:.2f}B"
    if a >= 1e6:
        return f"{sign}${a / 1e6:.0f}M"
    return f"{sign}${a:,.0f}"


def _num(value, suffix: str = "") -> str:
    if value is None:
        return "N/A"
    try:
        return f"{float(value):.2f}{suffix}"
    except (TypeError, ValueError):
        return "N/A"


def _get_rupee_metrics_sync(ticker: str) -> dict:
    info = yf.Ticker(ticker).info or {}

    fcf = info.get("freeCashflow")
    revenue = info.get("totalRevenue")
    fcf_margin = None
    if fcf is not None and revenue:
        try:
            fcf_margin = float(fcf) / float(revenue)
        except (TypeError, ValueError, ZeroDivisionError):
            fcf_margin = None

    return {
        "name": info.get("longName") or info.get("shortName") or ticker,
        "sector": info.get("sector") or "N/A",
        "industry": info.get("industry") or "N/A",
        # R — Rokda (cash generation)
        "freeCashflow": fcf,
        "operatingCashflow": info.get("operatingCashflow"),
        "totalRevenue": revenue,
        "fcfMargin": fcf_margin,
        # U — Udhaari (debt & leverage)
        "totalDebt": info.get("totalDebt"),
        "totalCash": info.get("totalCash"),
        "debtToEquity": info.get("debtToEquity"),  # % form, e.g. 23.8 ≈ 0.24x
        "currentRatio": info.get("currentRatio"),
        "quickRatio": info.get("quickRatio"),
        "ebitda": info.get("ebitda"),
        # P — Price (valuation)
        "trailingPE": info.get("trailingPE"),
        "forwardPE": info.get("forwardPE"),
        "priceToBook": info.get("priceToBook"),
        "priceToSales": info.get("priceToSalesTrailing12Months"),
        "marketCap": info.get("marketCap"),
        "currentPrice": info.get("currentPrice") or info.get("regularMarketPrice"),
        "fiftyTwoWeekHigh": info.get("fiftyTwoWeekHigh"),
        "fiftyTwoWeekLow": info.get("fiftyTwoWeekLow"),
        # E — Excellence (moat / quality / margins)
        "returnOnEquity": info.get("returnOnEquity"),
        "returnOnAssets": info.get("returnOnAssets"),
        "grossMargins": info.get("grossMargins"),
        "operatingMargins": info.get("operatingMargins"),
        "profitMargins": info.get("profitMargins"),
        # E — Earnings (growth & trajectory)
        "trailingEps": info.get("trailingEps"),
        "epsCurrentYear": info.get("epsCurrentYear"),
        "forwardEps": info.get("forwardEps"),
        "revenueGrowth": info.get("revenueGrowth"),
        "earningsGrowth": info.get("earningsGrowth"),
    }


async def get_rupee_metrics(ticker: str) -> dict:
    """Async wrapper — yfinance is blocking, so run it in a thread."""
    return await asyncio.to_thread(_get_rupee_metrics_sync, ticker)


def format_rupee_context(m: dict) -> str:
    """Render the metrics as a plain-text ledger for the LLM prompt."""
    cur = m.get("currentPrice")
    lo, hi = m.get("fiftyTwoWeekLow"), m.get("fiftyTwoWeekHigh")
    range_str = f"${_num(lo)}–${_num(hi)}" if lo and hi else "N/A"
    d2e = m.get("debtToEquity")
    d2e_str = f"{float(d2e) / 100:.2f}x (debt/equity)" if isinstance(d2e, (int, float)) else "N/A"

    return "\n".join([
        "── Bahi-Khata (the ledger) ──",
        f"Business: {m['name']} | Sector: {m['sector']} | Industry: {m['industry']}",
        "",
        "R — Rokda (Cash Generation):",
        f"  Free Cash Flow (TTM): {_money(m.get('freeCashflow'))}",
        f"  Operating Cash Flow (TTM): {_money(m.get('operatingCashflow'))}",
        f"  Revenue (TTM): {_money(m.get('totalRevenue'))}",
        f"  FCF Margin: {_pct(m.get('fcfMargin'))}",
        "",
        "U — Udhaari (Debt & Leverage):",
        f"  Total Debt: {_money(m.get('totalDebt'))} | Total Cash: {_money(m.get('totalCash'))}",
        f"  Debt-to-Equity: {d2e_str}",
        f"  EBITDA (TTM): {_money(m.get('ebitda'))}",
        f"  Current Ratio: {_num(m.get('currentRatio'))} | Quick Ratio: {_num(m.get('quickRatio'))}",
        "",
        "P — Price (Valuation):",
        f"  Trailing P/E: {_num(m.get('trailingPE'))} | Forward P/E: {_num(m.get('forwardPE'))}",
        f"  Price-to-Book: {_num(m.get('priceToBook'))} | Price-to-Sales: {_num(m.get('priceToSales'))}",
        f"  Market Cap: {_money(m.get('marketCap'))}",
        f"  Current Price: ${_num(cur)} | 52-week range: {range_str}",
        "",
        "E — Excellence (Moat / Quality / Margins):",
        f"  Return on Equity: {_pct(m.get('returnOnEquity'))} | Return on Assets: {_pct(m.get('returnOnAssets'))}",
        f"  Gross Margin: {_pct(m.get('grossMargins'))} | Operating Margin: {_pct(m.get('operatingMargins'))} | Net Margin: {_pct(m.get('profitMargins'))}",
        "",
        "E — Earnings (Growth & Trajectory):",
        f"  Trailing EPS (TTM, GAAP): {_num(m.get('trailingEps'))}",
        f"  Current-FY EPS estimate (analyst): {_num(m.get('epsCurrentYear'))}",
        f"  Next-FY EPS estimate (analyst): {_num(m.get('forwardEps'))}",
        f"  Revenue Growth (latest qtr, YoY): {_pct(m.get('revenueGrowth'))}",
        f"  Earnings Growth (latest qtr, YoY): {_pct(m.get('earningsGrowth'))}",
        "",
        "NOTE: Forward EPS figures are ANALYST consensus estimates (often on an "
        "adjusted/non-GAAP basis), not company guidance. Trailing EPS is GAAP and "
        "may be depressed by one-time items — weigh the difference like a skeptic.",
    ])
