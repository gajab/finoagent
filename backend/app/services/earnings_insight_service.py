"""Earnings-report insight — an LLM summary grounded in the latest SEC filing.

Replaces the old web-search-then-summarize path with the same primary-source
dossier the multi-agent debate uses: the most recent quarterly filing (10-Q, or
a 6-K / 8-K EX-99 earnings release for foreign filers) from ``edgar_service`` plus
the reported earnings numbers from ``stock_service``. One structured LLM pass
turns that into a readable quarter recap, cached per ticker so re-opens are free.
"""

from __future__ import annotations

import datetime
import logging

from .edgar_service import gather_company_filings
from .stock_service import fetch_stock_data
from .llm_service import call_llm
from .cache_service import get_cached, set_cached

logger = logging.getLogger(__name__)

# A quarter's filing doesn't change once filed; refresh weekly so a fresh report
# supersedes the prior quarter within a week (users can also force-regenerate).
_TTL = 7 * 86400
_MAX_DOCS = 3
_EXCERPT_CHARS = 9000


def _cache_key(ticker: str) -> str:
    return f"earnings_insight:{ticker.upper()}:v1"


def _is_quarterly(form: str | None) -> bool:
    """10-Q / interim 6-K / any EX-99 earnings release carry the quarter's results;
    a 10-K annual report does not (it's a full-year document)."""
    fu = (form or "").upper()
    return fu.startswith(("10-Q", "6-K")) or "EX-99" in fu


def _earnings_context(ticker: str, sd: dict) -> str:
    """Compact, factual numbers block so the model anchors to reported figures."""
    if not sd:
        return f"Ticker: {ticker} (no market data available)"
    earn = (sd or {}).get("earnings", {}) or {}
    g = earn.get("guidance", {}) or {}
    lines = [
        f"Company: {sd.get('companyName', ticker)} ({ticker})",
        f"Sector/Industry: {sd.get('sector', 'N/A')} / {sd.get('industry', 'N/A')}",
        f"Most recent reported quarter: {earn.get('lastQuarter', 'N/A')}",
    ]
    if earn.get("available"):
        lines.append(
            f"Reported EPS {earn.get('reportedEPS', 'N/A')} vs est {earn.get('estimatedEPS', 'N/A')} "
            f"(surprise {earn.get('epsSurprisePct', 'N/A')}%); Revenue {earn.get('revenueFormatted', 'N/A')}; "
            f"Net income {earn.get('netIncomeFormatted', 'N/A')}"
        )
        lines.append(
            f"Valuation — Trailing P/E {earn.get('trailingPE', 'N/A')}, Forward P/E {earn.get('forwardPE', 'N/A')}, "
            f"PEG {earn.get('pegRatio', 'N/A')}"
        )
    if g:
        lines.append(
            f"Analyst outlook — forward EPS {g.get('forwardEps', 'N/A')}, mean target "
            f"${g.get('targetMeanPrice', 'N/A')} (n={g.get('numberOfAnalysts', 'N/A')}), "
            f"rev growth {g.get('revenueGrowthPct', 'N/A')}%, earnings growth {g.get('earningsGrowthPct', 'N/A')}%"
        )
        if g.get("nextEarningsDate"):
            lines.append(f"Next earnings date: {g.get('nextEarningsDate')}")
    return "\n".join(lines)


_SYSTEM_PROMPT = """You are a senior equity research analyst writing a concise recap of a company's \
MOST RECENT quarterly earnings report. You are given (a) the reported headline numbers and (b) excerpts \
from the company's latest SEC filing (10-Q / earnings release). Ground every claim in that material — do \
NOT invent figures, guidance, or events that are not supported by the excerpts or the reported numbers.

Write in markdown using EXACTLY these section headers (omit a section only if there is genuinely nothing \
to say):

### Headline
One or two sentences: did they beat/miss, and the single most important takeaway.

### Results vs Expectations
Revenue and EPS vs consensus; how the print compares to the prior year/quarter.

### Revenue & Segment Drivers
What drove the top line — segments, products, geographies, volume vs price — per management's discussion.

### Margins & Profitability
Gross/operating/net margin direction and why (mix, costs, one-offs).

### Cash Flow & Balance Sheet
Operating cash flow / FCF, buybacks or dividends, debt or liquidity changes if mentioned.

### Guidance & Outlook
Any forward guidance or management outlook from the filing; note if none was given.

### Management Tone & Strategy
The posture management struck (confident, cautious) and strategic priorities emphasized.

### Risks & Watch Items
Concrete risks or watch items surfaced in the filing (from MD&A / Risk Factors).

Keep it tight — roughly 350-550 words total. Prefer specifics from the filing over generic commentary."""


async def generate_earnings_insight(db, ticker: str, openai_key: str, model: str) -> dict:
    """Fetch the latest quarterly filing + numbers and LLM-summarize the quarter."""
    ticker = ticker.upper()

    try:
        sd = await fetch_stock_data(ticker)
    except Exception as exc:
        logger.info("earnings insight: stock data fetch failed for %s: %s", ticker, exc)
        sd = {}
    quarter = ((sd or {}).get("earnings", {}) or {}).get("lastQuarter") or ""

    bundle = await gather_company_filings(db, ticker)
    docs = bundle.get("docs", []) or []
    quarterly = [d for d in docs if _is_quarterly(d.get("form"))]
    chosen = (quarterly or docs)[:_MAX_DOCS]

    context = _earnings_context(ticker, sd)

    if chosen:
        excerpt_text = "\n\n".join(
            f"[{d.get('form', 'FILING')} filed {d.get('date', 'n/a')}]\n{(d.get('excerpt') or '')[:_EXCERPT_CHARS]}"
            for d in chosen
        )
        filing_note = ""
    else:
        # No EDGAR primary documents (foreign filer without SEC docs, or lookup
        # miss). Summarize from the reported numbers alone and flag the gap.
        excerpt_text = "(No SEC filing excerpts were available for this company.)"
        filing_note = ("\n\n_Note: no SEC filing was found for this ticker, so this recap is based only on "
                       "the reported headline numbers — treat it as directional._")

    messages = [
        {"role": "system", "content": _SYSTEM_PROMPT},
        {
            "role": "user",
            "content": (
                f"REPORTED NUMBERS\n{context}\n\n"
                f"SEC FILING EXCERPTS\n{excerpt_text}\n\n"
                f"Write the quarterly earnings recap for {ticker}."
            ),
        },
    ]

    answer = await call_llm(api_key=openai_key, model=model, messages=messages, max_tokens=1300)
    if filing_note:
        answer = (answer or "").rstrip() + filing_note

    result = {
        "ticker": ticker,
        "quarter": quarter,
        "summary": answer,
        "sources": [
            {"form": d.get("form"), "date": d.get("date"), "url": d.get("url")}
            for d in chosen
        ],
        "generated_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
    }

    try:
        if db is not None:
            await set_cached(db, _cache_key(ticker), result, ttl_seconds=_TTL)
    except Exception as exc:
        logger.debug("earnings insight cache write failed for %s: %s", ticker, exc)

    return result


async def get_cached_earnings_insight(db, ticker: str) -> dict | None:
    """Return a previously generated insight without spending an LLM call."""
    if db is None:
        return None
    try:
        return await get_cached(db, _cache_key(ticker))
    except Exception:
        return None
