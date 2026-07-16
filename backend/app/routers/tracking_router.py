"""Tracked Companies router — /api/tracked-companies.

Lets users save Pick & Shovel companies for ongoing monitoring.
Each tracked company stores:
  • Full LLM context (thesis, catalysts, hidden link, etc.)
  • Refreshable live financial data (price, P/E, 52W range)
  • User notes (editable)
  • Source theme (with a ≤50-char summarized slug)

The router also provides:
  • Price refresh (yfinance, cached 15 min)
  • Agent pre-population endpoint
"""
from __future__ import annotations

import asyncio
import json
import logging
import math
from datetime import datetime, timedelta, timezone

import yfinance as yf
from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..auth import get_current_user, get_user_api_key
from ..database import get_db
from ..models import Agent, TrackedCompany, User
from ..services.llm_service import call_llm

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/tracked-companies", tags=["tracking"])


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _safe_float(v, default=None):
    try:
        f = float(v)
        return default if math.isnan(f) or math.isinf(f) else f
    except Exception:
        return default


def _fmt_cap(v) -> str | None:
    if v is None:
        return None
    if v >= 1e12:
        return f"${v / 1e12:.2f}T"
    if v >= 1e9:
        return f"${v / 1e9:.1f}B"
    if v >= 1e6:
        return f"${v / 1e6:.0f}M"
    return f"${v:,.0f}"


def _fetch_price_sync(ticker: str) -> dict:
    try:
        info = yf.Ticker(ticker).info or {}
        curr = _safe_float(info.get("currentPrice") or info.get("regularMarketPrice"))
        prev = _safe_float(info.get("previousClose") or info.get("regularMarketPreviousClose"))
        pct  = ((curr - prev) / prev * 100) if curr and prev and prev != 0 else None
        return {
            "price":         round(curr, 2) if curr else None,
            "price_chg_1d":  round(pct, 2) if pct is not None else None,
            "pe_ratio":      round(_safe_float(info.get("trailingPE")), 1) if _safe_float(info.get("trailingPE")) else None,
            "forward_pe":    round(_safe_float(info.get("forwardPE")), 1) if _safe_float(info.get("forwardPE")) else None,
            "market_cap":    _fmt_cap(_safe_float(info.get("marketCap"))),
            "week52_high":   round(_safe_float(info.get("fiftyTwoWeekHigh")), 2) if _safe_float(info.get("fiftyTwoWeekHigh")) else None,
            "week52_low":    round(_safe_float(info.get("fiftyTwoWeekLow")), 2) if _safe_float(info.get("fiftyTwoWeekLow")) else None,
            "sector":        info.get("sector"),
            "refreshed_at":  datetime.now(timezone.utc).isoformat(),
        }
    except Exception as exc:
        logger.debug("Price fetch failed for %s: %s", ticker, exc)
        return {}


def _row_to_dict(row: TrackedCompany, agent_counts: dict | None = None) -> dict:
    fin = {}
    try:
        fin = json.loads(row.financial_data or "{}")
    except Exception:
        pass
    llm = {}
    try:
        llm = json.loads(row.llm_data or "{}")
    except Exception:
        pass
    counts = agent_counts or {}
    return {
        "id":                   row.id,
        "ticker":               row.ticker,
        "name":                 row.name,
        "theme_raw":            row.theme_raw,
        "theme_slug":           row.theme_slug,
        "theme_summary":        row.theme_summary,
        "exchange":             row.exchange,
        "sector":               row.sector or fin.get("sector"),
        "source_tier":          row.source_tier,
        "depth_level":          row.depth_level,
        "status":               row.status,
        "user_notes":           row.user_notes,
        "llm_data":             llm,
        "financial_data":       fin,
        "last_price_refresh":   row.last_price_refresh.isoformat() if row.last_price_refresh else None,
        "agent_count":          counts.get("total", 0),
        "active_agent_count":   counts.get("active", 0),
        "created_at":           row.created_at.isoformat(),
        "updated_at":           row.updated_at.isoformat(),
    }


async def _resolve_llm(db: AsyncSession, user: User) -> tuple[str | None, str]:
    try:
        key = await get_user_api_key(db, user.id, "openai_api_key")
    except Exception:
        key = None
    try:
        model = await get_user_api_key(db, user.id, "openai_model")
    except Exception:
        model = None
    return key, (model or "gpt-4o")


async def _summarize_theme(theme: str, openai_key: str | None, model: str) -> str:
    """Return a ≤50-char theme label. Uses LLM if theme is long.

    Always uses gpt-4o-mini regardless of the user's preferred model —
    this is a trivial summarisation task that costs a fraction of a cent.
    """
    if len(theme) <= 50:
        return theme
    if not openai_key:
        return theme[:47] + "…"
    try:
        resp = await call_llm(
            api_key=openai_key,
            model="gpt-3.5-turbo",        # cheapest model — plain text summary only
            messages=[
                {"role": "system", "content": "You summarize investment themes into ≤50-character labels. Output ONLY the label — no quotes, no punctuation at the end."},
                {"role": "user", "content": f"Summarize this theme in ≤50 characters: {theme}"},
            ],
            max_tokens=30,
            temperature=0.1,
        )
        slug = (resp or "").strip().strip('"').strip("'")[:50]
        return slug if slug else theme[:47] + "…"
    except Exception:
        return theme[:47] + "…"


# ---------------------------------------------------------------------------
# Pydantic models
# ---------------------------------------------------------------------------

class TrackCompanyIn(BaseModel):
    ticker: str
    name: str
    theme_raw: str
    theme_slug: str | None = None         # if already short; else backend generates
    theme_summary: str | None = None
    exchange: str | None = None
    sector: str | None = None
    source_tier: str | None = None        # direct | enabler | deep | deeper
    depth_level: int | None = None
    llm_data: dict = Field(default_factory=dict)
    financial_data: dict = Field(default_factory=dict)
    user_notes: str | None = None


class UpdateNotesIn(BaseModel):
    user_notes: str | None = None         # None = clear notes


class UpdateStatusIn(BaseModel):
    status: str   # 'active' | 'on_hold'


class AgentFromTrackingIn(BaseModel):
    company_id: int
    extra_instruction: str | None = None
    schedule_type: str = "manual"
    schedule_cron: str | None = None
    mini_agent_type: str | None = None  # 'technical' | 'fundamental' | 'developments' | 'earnings' | 'insider' | 'options_sentiment'


class UntrackIn(BaseModel):
    deactivate_agents: bool = False   # if True, pause all linked agents


# ---------------------------------------------------------------------------
# Mini-agent template definitions
# ---------------------------------------------------------------------------

MINI_AGENT_TEMPLATES: dict[str, dict] = {
    "technical": {
        "label": "Technical Analysis",
        "description": "Daily quantitative technical analysis — price action, momentum indicators, volume patterns, and support/resistance levels.",
        "schedule_type": "recurring",
        "schedule_cron": "0 9 * * 1-5",   # weekdays 9am
        "instruction_template": """You are a quantitative technical analyst monitoring {ticker} — {name}.

COMPANY CONTEXT
Theme: {theme_slug}
Sector: {sector}
Baseline Price: ${price}  |  52W High: ${week52_high}  |  52W Low: ${week52_low}

YOUR DAILY TECHNICAL ANALYSIS TASKS
1. Fetch the latest price, volume, and recent OHLC data for {ticker}.
2. Calculate and interpret:
   • RSI (14) — overbought/oversold levels
   • MACD — bullish/bearish crossovers, histogram momentum
   • Bollinger Bands — squeeze setups, breakouts, mean-reversion signals
   • 20/50/200-day SMAs — golden/death cross, price vs key MAs
   • Volume analysis — accumulation vs distribution days, unusual spikes
3. Identify key support and resistance levels.
4. Note any chart patterns (cup & handle, head & shoulders, wedges, flags).
5. Rate the technical setup: STRONG BUY / BUY / NEUTRAL / SELL / STRONG SELL.
6. Compare current position in 52W range: ${week52_low}–${week52_high}.

Format output as:
PRICE ACTION | MOMENTUM (RSI/MACD) | TREND (MAs) | VOLUME | PATTERNS | VERDICT + LEVELS""",
    },
    "fundamental": {
        "label": "Fundamental Deep Dive",
        "description": "Weekly deep fundamental analysis — earnings, valuation, competitive moat, management quality, and growth prospects.",
        "schedule_type": "recurring",
        "schedule_cron": "0 8 * * 1",    # Mondays 8am
        "instruction_template": """You are a fundamental equity analyst conducting a weekly deep dive on {ticker} — {name}.

INVESTMENT CONTEXT
Theme: {theme_slug} | Sector: {sector}
Thesis: {thesis}
Baseline Financials: Price ${price} | P/E {pe_ratio} | Fwd P/E {forward_pe} | Market Cap {market_cap}

YOUR WEEKLY FUNDAMENTAL ANALYSIS TASKS
1. Review the most recent quarterly earnings:
   • EPS and revenue vs consensus estimates (beat/miss/meet)
   • Revenue growth rate YoY and QoQ
   • Gross and operating margin trends
   • Free cash flow and balance sheet health
2. Valuation assessment:
   • P/E, EV/EBITDA, P/S vs sector peers and 5Y historical averages
   • DCF sanity check — does current price imply reasonable growth assumptions?
3. Competitive moat evaluation:
   • Market share trajectory
   • Pricing power signals
   • Key competitive threats or new entrants
4. Management quality signals:
   • Guidance accuracy (beat/meet pattern)
   • Capital allocation (buybacks, M&A, capex efficiency)
   • Insider ownership trends
5. Growth catalysts: check if any items from the thesis are progressing — {catalysts}
6. Final verdict: STRONG VALUE / FAIR VALUE / OVERVALUED + one-paragraph investment case.

USER NOTES: {user_notes}

Format: EARNINGS QUALITY | VALUATION | MOAT | MANAGEMENT | CATALYSTS | VERDICT""",
    },
    "developments": {
        "label": "Recent Developments",
        "description": "Daily scan of news, competitor moves, industry shifts, and macro factors affecting this company.",
        "schedule_type": "recurring",
        "schedule_cron": "0 7 * * 1-5",  # weekdays 7am
        "instruction_template": """You are a market intelligence analyst providing a daily developments briefing for {ticker} — {name}.

COMPANY CONTEXT
Theme: {theme_slug} | Sector: {sector}
Key risks to monitor: {risk}
Hidden connection: {hidden_link}

YOUR DAILY SCAN (last 24-48 hours)
1. Company news: earnings releases, press releases, analyst upgrades/downgrades, price target changes.
2. Competitor moves: any significant announcements from direct competitors that affect {ticker}'s market position.
3. Industry developments: regulatory changes, supply chain disruptions, technology shifts affecting the sector.
4. Macro factors: interest rate changes, commodity price moves, FX shifts, geopolitical events relevant to {theme_slug}.
5. Social and alternative signals: unusual executive commentary, product reviews, patent filings, job postings signalling strategic intent.
6. Catalyst progress: check if any of these known catalysts have advanced — {catalysts}

Rate overall news sentiment: VERY POSITIVE / POSITIVE / NEUTRAL / NEGATIVE / VERY NEGATIVE

Format: COMPANY NEWS | COMPETITOR MOVES | INDUSTRY & MACRO | CATALYST PROGRESS | SENTIMENT RATING""",
    },
    "earnings": {
        "label": "Earnings Intelligence",
        "description": "Pre-earnings prep: consensus estimates, historical reaction patterns, implied options move, and key metrics to watch.",
        "schedule_type": "manual",
        "schedule_cron": None,
        "instruction_template": """You are an earnings intelligence analyst preparing a pre-earnings brief for {ticker} — {name}.

COMPANY CONTEXT
Theme: {theme_slug} | Sector: {sector}
Investment Thesis: {thesis}
Current: Price ${price} | P/E {pe_ratio} | Market Cap {market_cap}

YOUR EARNINGS PREPARATION BRIEF
1. Next earnings date: when is the next scheduled earnings release?
2. Consensus estimates:
   • EPS estimate and range (high/low)
   • Revenue estimate and range
   • YoY growth implied by consensus
3. Historical earnings pattern:
   • Last 4 quarters: beat/miss EPS, beat/miss revenue
   • Average stock reaction on earnings day (magnitude + direction)
   • Typical post-earnings drift
4. Key metrics to watch this quarter:
   • The 2-3 numbers that matter most to the investment thesis
   • Guidance language (is management known for conservative/aggressive guidance?)
5. Options-implied move:
   • What is the current IV vs historical IV?
   • What magnitude move does the ATM straddle price imply?
6. Bull vs Bear case:
   • Bull: what would make this a 5%+ upside surprise?
   • Bear: what would cause a sharp selloff?

USER NOTES: {user_notes}

Format: EARNINGS DATE | ESTIMATES | HISTORY | KEY METRICS | OPTIONS MOVE | BULL vs BEAR""",
    },
    "insider": {
        "label": "Insider & Institutional Flow",
        "description": "Weekly monitoring of insider transactions, 13F/13D filings, institutional accumulation, and short interest changes.",
        "schedule_type": "recurring",
        "schedule_cron": "0 8 * * 3",    # Wednesdays 8am (SEC filings typically processed mid-week)
        "instruction_template": """You are an institutional flow analyst monitoring smart-money activity in {ticker} — {name}.

COMPANY CONTEXT
Theme: {theme_slug} | Sector: {sector}
Thesis: {thesis}

YOUR WEEKLY FLOW MONITORING TASKS
1. Recent insider transactions (Form 4 filings, last 30 days):
   • CEO/CFO/Director buys vs sells
   • Volume and dollar value of insider activity
   • Pattern: are insiders net buyers or sellers over the last 6 months?
2. Institutional ownership changes (13F filings, latest quarter):
   • Top 10 holders — did they add, trim, or hold?
   • Any new significant positions (13D/13G events)?
   • Aggregate institutional ownership % trend
3. Short interest:
   • Current short interest as % of float
   • Short interest ratio (days to cover)
   • Change vs prior month — increasing or decreasing?
4. Options market positioning:
   • Large block options trades (dark pool prints)
   • Significant put/call open interest changes
5. Summary signal: STRONG ACCUMULATION / ACCUMULATION / NEUTRAL / DISTRIBUTION / STRONG DISTRIBUTION

Format: INSIDER ACTIVITY | INSTITUTIONAL CHANGES | SHORT INTEREST | OPTIONS POSITIONING | SIGNAL""",
    },
    "options_sentiment": {
        "label": "Options Flow & Sentiment",
        "description": "Daily options flow analysis, put/call ratio, implied volatility, and social/news sentiment.",
        "schedule_type": "recurring",
        "schedule_cron": "0 9 * * 1-5",  # weekdays 9am
        "instruction_template": """You are an options and sentiment analyst monitoring {ticker} — {name}.

COMPANY CONTEXT
Theme: {theme_slug} | Sector: {sector}
Current: Price ${price} | 52W High: ${week52_high} | 52W Low: ${week52_low}

YOUR DAILY OPTIONS & SENTIMENT TASKS
1. Options flow analysis:
   • Put/call ratio (total volume and open interest)
   • Unusual options activity — any large sweeps or blocks?
   • Largest open interest strikes — where is the market positioned?
2. Implied volatility:
   • Current IV vs 30-day historical volatility (IV premium or discount)
   • IV percentile rank (0–100) — is the market pricing high or low uncertainty?
   • IV skew — are puts or calls more expensive (fear vs greed)?
3. News sentiment (last 48 hours):
   • Positive/negative/neutral article count
   • Analyst commentary tone
   • Social media sentiment (Reddit WSB, Twitter/X financial community)
4. Retail vs institutional signal divergence:
   • Is retail sentiment aligned or diverging from options positioning?
5. Sentiment verdict: EXTREME GREED / GREED / NEUTRAL / FEAR / EXTREME FEAR

Format: OPTIONS FLOW | IMPLIED VOLATILITY | NEWS SENTIMENT | RETAIL SIGNAL | VERDICT""",
    },
}


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

@router.post("", status_code=201)
async def track_company(
    body: TrackCompanyIn,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Add a company to the user's tracking list."""
    # Avoid duplicates: if same ticker+theme already tracked, return existing
    existing = (await db.execute(
        select(TrackedCompany).where(
            TrackedCompany.user_id == user.id,
            TrackedCompany.ticker == body.ticker.upper(),
            TrackedCompany.theme_raw == body.theme_raw,
        )
    )).scalar_one_or_none()
    if existing:
        return {"already_tracked": True, **_row_to_dict(existing)}

    key, model = await _resolve_llm(db, user)

    # Generate short theme slug if needed
    theme_slug = body.theme_slug
    if not theme_slug or len(theme_slug) > 50:
        theme_slug = await _summarize_theme(body.theme_raw, key, model)

    row = TrackedCompany(
        user_id=user.id,
        ticker=body.ticker.upper(),
        name=body.name,
        theme_raw=body.theme_raw,
        theme_slug=theme_slug,
        theme_summary=body.theme_summary,
        exchange=body.exchange,
        sector=body.sector,
        source_tier=body.source_tier,
        depth_level=body.depth_level,
        llm_data=json.dumps(body.llm_data),
        financial_data=json.dumps(body.financial_data) if body.financial_data else "{}",
        user_notes=body.user_notes,
        last_price_refresh=datetime.now(timezone.utc) if body.financial_data else None,
    )
    db.add(row)
    await db.commit()
    await db.refresh(row)
    return {"already_tracked": False, **_row_to_dict(row)}


@router.get("")
async def list_tracked(
    status: str = Query(default="active", description="Filter by status: active | on_hold | all"),
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Return tracked companies grouped by theme_slug.

    Pass ``status=on_hold`` for the archive view, ``status=all`` to get both.
    Each company includes ``agent_count`` and ``active_agent_count`` derived from
    linked agents (agents with ``tracked_company_id = company.id``).
    """
    q = select(TrackedCompany).where(TrackedCompany.user_id == user.id)
    if status in ("active", "on_hold"):
        q = q.where(TrackedCompany.status == status)
    rows = (await db.execute(q.order_by(TrackedCompany.theme_slug, TrackedCompany.created_at))).scalars().all()

    # Batch-fetch agent counts for all company ids in one query
    company_ids = [r.id for r in rows]
    agent_counts_by_id: dict[int, dict] = {}
    if company_ids:
        agent_rows = (await db.execute(
            select(Agent.tracked_company_id, Agent.status)
            .where(Agent.tracked_company_id.in_(company_ids))
        )).all()
        for cid, astatus in agent_rows:
            if cid not in agent_counts_by_id:
                agent_counts_by_id[cid] = {"total": 0, "active": 0}
            agent_counts_by_id[cid]["total"] += 1
            if astatus == "active":
                agent_counts_by_id[cid]["active"] += 1

    # Group by theme_slug
    groups: dict[str, dict] = {}
    for row in rows:
        slug = row.theme_slug
        if slug not in groups:
            groups[slug] = {
                "theme_slug":    slug,
                "theme_raw":     row.theme_raw,
                "theme_summary": row.theme_summary,
                "companies":     [],
            }
        groups[slug]["companies"].append(_row_to_dict(row, agent_counts_by_id.get(row.id)))

    return {"groups": list(groups.values()), "total": len(rows)}


@router.delete("/{company_id}", status_code=204)
async def untrack_company(
    company_id: int,
    deactivate_agents: bool = Query(default=False, description="If true, pause all linked active agents before deleting."),
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Permanently remove a tracked company. Pass deactivate_agents=true to pause
    all linked agents before deletion (the agents survive with tracked_company_id=NULL
    because the FK is SET NULL on delete, and their status becomes 'stopped')."""
    row = (await db.execute(
        select(TrackedCompany).where(
            TrackedCompany.id == company_id,
            TrackedCompany.user_id == user.id,
        )
    )).scalar_one_or_none()
    if not row:
        raise HTTPException(404, "Not found")

    if deactivate_agents:
        linked = (await db.execute(
            select(Agent).where(
                Agent.tracked_company_id == company_id,
                Agent.status == "active",
            )
        )).scalars().all()
        for agent in linked:
            agent.status = "stopped"
            agent.next_run_at = None

    await db.delete(row)
    await db.commit()


@router.patch("/{company_id}/status")
async def update_status(
    company_id: int,
    body: UpdateStatusIn,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Archive (on_hold) or restore (active) a tracked company.

    Moving to on_hold does NOT delete the company or its agents —
    it simply removes it from the active focus list.
    """
    if body.status not in ("active", "on_hold"):
        raise HTTPException(400, "status must be 'active' or 'on_hold'")
    row = (await db.execute(
        select(TrackedCompany).where(
            TrackedCompany.id == company_id,
            TrackedCompany.user_id == user.id,
        )
    )).scalar_one_or_none()
    if not row:
        raise HTTPException(404, "Not found")
    row.status = body.status
    await db.commit()
    await db.refresh(row)
    return _row_to_dict(row)


@router.get("/{company_id}/agents")
async def list_company_agents(
    company_id: int,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Return all agents linked to this tracked company (any status)."""
    # Verify ownership
    row = (await db.execute(
        select(TrackedCompany).where(
            TrackedCompany.id == company_id,
            TrackedCompany.user_id == user.id,
        )
    )).scalar_one_or_none()
    if not row:
        raise HTTPException(404, "Not found")

    agents = (await db.execute(
        select(Agent)
        .where(Agent.tracked_company_id == company_id, Agent.user_id == user.id)
        .order_by(Agent.created_at.desc())
    )).scalars().all()

    return [_agent_to_dict(a) for a in agents]


def _agent_to_dict(a: Agent) -> dict:
    return {
        "id":                   a.id,
        "name":                 a.name,
        "description":          a.description,
        "status":               a.status,
        "schedule_type":        a.schedule_type,
        "schedule_cron":        a.schedule_cron,
        "last_run_at":          a.last_run_at.isoformat() if a.last_run_at else None,
        "next_run_at":          a.next_run_at.isoformat() if a.next_run_at else None,
        "tracked_company_id":   a.tracked_company_id,
        "created_at":           a.created_at.isoformat(),
    }


@router.get("/mini-agent-templates")
async def get_mini_agent_templates():
    """Return the list of predefined mini-agent templates."""
    return [
        {
            "key": k,
            "label": v["label"],
            "description": v["description"],
            "schedule_type": v["schedule_type"],
            "schedule_cron": v["schedule_cron"],
        }
        for k, v in MINI_AGENT_TEMPLATES.items()
    ]


@router.patch("/{company_id}/notes")
async def update_notes(
    company_id: int,
    body: UpdateNotesIn,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    row = (await db.execute(
        select(TrackedCompany).where(
            TrackedCompany.id == company_id,
            TrackedCompany.user_id == user.id,
        )
    )).scalar_one_or_none()
    if not row:
        raise HTTPException(404, "Not found")
    row.user_notes = body.user_notes
    await db.commit()
    await db.refresh(row)
    return _row_to_dict(row)


@router.post("/{company_id}/refresh-price")
async def refresh_price(
    company_id: int,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Refresh live price data for a tracked company (max once per 15 min)."""
    row = (await db.execute(
        select(TrackedCompany).where(
            TrackedCompany.id == company_id,
            TrackedCompany.user_id == user.id,
        )
    )).scalar_one_or_none()
    if not row:
        raise HTTPException(404, "Not found")

    # Rate-limit: don't re-fetch more than once per 15 min
    if row.last_price_refresh:
        age = datetime.now(timezone.utc) - row.last_price_refresh.replace(tzinfo=timezone.utc)
        if age < timedelta(minutes=15):
            return _row_to_dict(row)

    fin = await asyncio.to_thread(_fetch_price_sync, row.ticker)
    row.financial_data = json.dumps(fin)
    row.last_price_refresh = datetime.now(timezone.utc)
    if fin.get("sector") and not row.sector:
        row.sector = fin["sector"]
    await db.commit()
    await db.refresh(row)
    return _row_to_dict(row)


@router.post("/{company_id}/create-agent")
async def create_agent_from_tracking(
    company_id: int,
    body: AgentFromTrackingIn,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Build a pre-populated agent scaffold from a tracked company.

    Returns the agent-create payload (name, description, instruction) ready for
    the frontend to open the agent creation modal — does NOT actually create
    the agent (that's the user's decision after review).
    """
    row = (await db.execute(
        select(TrackedCompany).where(
            TrackedCompany.id == company_id,
            TrackedCompany.user_id == user.id,
        )
    )).scalar_one_or_none()
    if not row:
        raise HTTPException(404, "Not found")

    llm = {}
    try:
        llm = json.loads(row.llm_data or "{}")
    except Exception:
        pass
    fin = {}
    try:
        fin = json.loads(row.financial_data or "{}")
    except Exception:
        pass

    # Compose a rich agent instruction
    catalysts = "; ".join(llm.get("catalysts") or [])
    instruction = f"""You are a financial monitoring agent for {row.ticker} — {row.name}.

TRACKING CONTEXT
Theme: {row.theme_slug} ({row.theme_raw[:200]})
Sector: {row.sector or 'Unknown'}
Source: {row.source_tier or 'pick-and-shovel'} research

INVESTMENT THESIS
{llm.get('thesis', 'No thesis recorded.')}

KEY CATALYSTS TO MONITOR
{catalysts or 'See thesis above.'}
"""
    if llm.get("hidden_link"):
        instruction += f"\nHIDDEN CONNECTION\n{llm['hidden_link']}\n"
    if llm.get("supply_chain_role"):
        instruction += f"\nSUPPLY CHAIN ROLE\n{llm['supply_chain_role']}\n"
    if llm.get("risk"):
        instruction += f"\nKEY RISK TO WATCH\n{llm['risk']}\n"
    if fin:
        instruction += f"""
BASELINE FINANCIAL SNAPSHOT (captured {fin.get('refreshed_at', 'unknown')})
Price: ${fin.get('price', '—')}  |  P/E: {fin.get('pe_ratio', '—')}  |  Fwd P/E: {fin.get('forward_pe', '—')}
52W High: ${fin.get('week52_high', '—')}  |  52W Low: ${fin.get('week52_low', '—')}
Market Cap: {fin.get('market_cap', '—')}
"""
    if row.user_notes:
        instruction += f"\nUSER RESEARCH NOTES\n{row.user_notes}\n"
    if body.extra_instruction:
        instruction += f"\nADDITIONAL MONITORING INSTRUCTIONS\n{body.extra_instruction}\n"

    # If a mini_agent_type is specified, use the predefined template instead
    if body.mini_agent_type and body.mini_agent_type in MINI_AGENT_TEMPLATES:
        tpl = MINI_AGENT_TEMPLATES[body.mini_agent_type]
        fin_data = fin if fin else {}
        instruction = tpl["instruction_template"].format(
            ticker=row.ticker,
            name=row.name,
            theme_slug=row.theme_slug,
            sector=row.sector or "Unknown",
            price=fin_data.get("price", "N/A"),
            pe_ratio=fin_data.get("pe_ratio", "N/A"),
            forward_pe=fin_data.get("forward_pe", "N/A"),
            market_cap=fin_data.get("market_cap", "N/A"),
            week52_high=fin_data.get("week52_high", "N/A"),
            week52_low=fin_data.get("week52_low", "N/A"),
            thesis=llm.get("thesis", "No thesis recorded."),
            catalysts="; ".join(llm.get("catalysts") or []) or "See thesis.",
            risk=llm.get("risk", "General market risk."),
            hidden_link=llm.get("hidden_link", ""),
            user_notes=row.user_notes or "None.",
        )
        if body.extra_instruction:
            instruction += f"\n\nADDITIONAL INSTRUCTIONS\n{body.extra_instruction}"
        return {
            "agent_scaffold": {
                "name": f"{tpl['label']}: {row.ticker}",
                "description": f"{tpl['description']} ({row.name} · {row.theme_slug})",
                "instruction": instruction.strip(),
                "schedule_type": tpl["schedule_type"],
                "schedule_cron": tpl["schedule_cron"],
                "mini_agent_type": body.mini_agent_type,
                "tracked_company_id": row.id,
            },
            "company": _row_to_dict(row),
        }

    instruction += f"""
YOUR MONITORING TASKS
1. Fetch the latest price for {row.ticker} and compare to the baseline above.
2. Check for recent news about {row.ticker} in the last 7 days — earnings, analyst upgrades/downgrades, M&A, regulatory news.
3. Check if any of the key catalysts listed above have fired or progressed.
4. Flag any significant move in the 52-week range (approaching high or low).
5. Summarize: BUY / HOLD / WATCH / AVOID — give one-paragraph reasoning.
6. Highlight any developments related to the theme: {row.theme_slug}

Format your output as a concise monitoring report with sections for:
Price Update | News Highlights | Catalyst Progress | Risk Watch | Summary Verdict
"""

    return {
        "agent_scaffold": {
            "name": f"Monitor {row.ticker} — {row.theme_slug[:30]}",
            "description": f"Tracks {row.name} ({row.ticker}) in the context of the '{row.theme_slug}' theme.",
            "instruction": instruction.strip(),
            "schedule_type": body.schedule_type,
            "schedule_cron": body.schedule_cron,
            "tracked_company_id": row.id,
        },
        "company": _row_to_dict(row),
    }
