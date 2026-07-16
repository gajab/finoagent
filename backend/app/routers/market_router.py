"""Market overview router — ``/api/market``.

Powers the Market dashboard page: major indices, sector rotation, top movers,
pick & shovel thematic research.

Caching strategy
----------------
* Sector / index yfinance data: 15 min DB cache (cheap repeated page loads).
* LLM narratives: **only generated on explicit user action** — the base
  /overview and /sectors endpoints never call the LLM.  Clients that want
  an AI briefing hit the /overview/narrative or /sectors/narrative endpoints.
"""

import logging

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from ..auth import get_current_user, get_premium_user, get_user_api_key
from ..database import get_db
from ..models import User
from ..services.market_overview_service import (
    get_market_overview,
    get_sector_dashboard,
    get_sector_calendar_returns,
    get_style_calendar_returns,
    get_top_movers,
    sector_chat,
    _llm_market_narrative,       # noqa: PLC2701 — private but needed here
    _llm_rotation_intelligence,  # noqa: PLC2701
)
from ..services.pick_shovel_service import (
    analyze_pick_shovel,
    dig_deeper_pick_shovel,
    interpret_thesis,
    refine_recommendations,
)
from ..services.liquidity_service import get_liquidity_dashboard, _llm_liquidity_narrative
from ..services.business_cycle_service import get_business_cycle
from ..services.cycle_sector_ta_service import get_cycle_sectors_ta
from ..services.cache_service import get_cached, set_cached

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/market", tags=["market"])

# TTLs (seconds)
_TTL_PRICE   = 900    # 15 min — prices / returns
_TTL_STATIC  = 86400  # 24 h  — names, market caps, static sector structure


async def _resolve_llm(db: AsyncSession, user: User) -> tuple[str | None, str]:
    """Fetch the user's OpenAI key + preferred model."""
    try:
        key = await get_user_api_key(db, user.id, "openai_api_key")
    except Exception:
        key = None
    try:
        model = await get_user_api_key(db, user.id, "openai_model")
    except Exception:
        model = None
    return key, (model or "gpt-4o")


# ---------------------------------------------------------------------------
# Market overview — no LLM on auto-load
# ---------------------------------------------------------------------------

@router.get("/overview")
async def overview(
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Major indices overview with multi-timeframe returns and sparklines.
    No LLM call — fast and cheap.  Use /overview/narrative for AI briefing."""
    cache_key = "market:overview:v2"
    cached = await get_cached(db, cache_key)
    if cached:
        return cached

    # yfinance fetch — runs in threadpool inside service
    result = await get_market_overview(openai_key=None, model="gpt-4o")
    # Strip narrative before caching (it's always None here anyway)
    result.pop("narrative", None)
    await set_cached(db, cache_key, result, ttl_seconds=_TTL_PRICE)
    return result


@router.post("/overview/narrative")
async def overview_narrative(
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Generate an AI market briefing — called only when user explicitly
    clicks 'Get AI Briefing'. Costs one LLM call."""
    key, model = await _resolve_llm(db, user)
    if not key:
        raise HTTPException(
            status_code=400,
            detail="OpenAI API key not configured. Add one in Settings.",
        )
    # Re-use cached index data so we don't hit yfinance again
    cache_key = "market:overview:v2"
    cached = await get_cached(db, cache_key)
    if cached:
        indices = cached.get("indices", [])
    else:
        result = await get_market_overview(openai_key=None)
        indices = result.get("indices", [])
        await set_cached(db, cache_key, result, ttl_seconds=_TTL_PRICE)

    narrative = await _llm_market_narrative(indices, key, model)
    return {"narrative": narrative}


@router.get("/index/{symbol}")
async def get_index_quote(
    symbol: str,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Fetch the latest price & returns for a single index directly from yfinance (bypassing cache) and update cache."""
    import asyncio
    from ..services.market_overview_service import _batch_history, _build_index_payload, INDICES
    
    idx_def = next((x for x in INDICES if x["symbol"].upper() == symbol.upper()), None)
    if not idx_def:
        raise HTTPException(status_code=404, detail="Index not found in definition")
        
    try:
        df = await asyncio.to_thread(_batch_history, [symbol], "2y")
        payload = _build_index_payload(idx_def, df, "2y")
        
        # Sync with cached market overview
        cache_key = "market:overview:v2"
        cached = await get_cached(db, cache_key)
        if cached and "indices" in cached:
            updated_indices = [
                payload if x["symbol"].upper() == symbol.upper() else x
                for x in cached["indices"]
            ]
            cached["indices"] = updated_indices
            await set_cached(db, cache_key, cached, ttl_seconds=_TTL_PRICE)
            
        return payload
    except Exception as exc:
        logger.exception("Failed to refresh index %s: %s", symbol, exc)
        raise HTTPException(status_code=500, detail=str(exc))


# ---------------------------------------------------------------------------
# Sector dashboard — no LLM on auto-load
# ---------------------------------------------------------------------------

@router.get("/sectors")
async def sectors(
    timeframe: str = Query("1m", description="Rotation timeframe: 1d, 7d, 15d, 1m, 3m, ytd, 1y, 3y, 5y"),
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Sector ETF dashboard — returns, analytics, rotation classification.
    No LLM call on this endpoint.  Use /sectors/narrative for AI diagnosis."""
    allowed = {"1d", "7d", "15d", "1m", "3m", "ytd", "1y", "3y", "5y"}
    if timeframe not in allowed:
        raise HTTPException(status_code=400, detail=f"Invalid timeframe. Allowed: {sorted(allowed)}")

    cache_key = f"market:sectors:{timeframe}"
    cached = await get_cached(db, cache_key)
    if cached:
        return cached

    try:
        data = await get_sector_dashboard(rotation_timeframe=timeframe, openai_key=None)
        data.pop("intelligence", None)  # no LLM on auto-load
        await set_cached(db, cache_key, data, ttl_seconds=_TTL_PRICE)
        return data
    except Exception as exc:
        logger.exception("Sector dashboard failed: %s", exc)
        raise HTTPException(status_code=500, detail=f"Sector dashboard failed: {exc}") from exc


@router.post("/sectors/narrative")
async def sectors_narrative(
    timeframe: str = Query("1m"),
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Generate AI sector rotation diagnosis — called on explicit user action."""
    allowed = {"1d", "7d", "15d", "1m", "3m", "ytd", "1y", "3y", "5y"}
    if timeframe not in allowed:
        raise HTTPException(status_code=400, detail=f"Invalid timeframe. Allowed: {sorted(allowed)}")
    key, model = await _resolve_llm(db, user)
    if not key:
        raise HTTPException(
            status_code=400,
            detail="OpenAI API key not configured. Add one in Settings.",
        )
    # Use cached sector data if available
    cache_key = f"market:sectors:{timeframe}"
    cached = await get_cached(db, cache_key)
    if not cached:
        cached = await get_sector_dashboard(rotation_timeframe=timeframe, openai_key=None)
        cached.pop("intelligence", None)
        await set_cached(db, cache_key, cached, ttl_seconds=_TTL_PRICE)

    intelligence = await _llm_rotation_intelligence(cached, timeframe, key, model)
    return {"intelligence": intelligence}


# ---------------------------------------------------------------------------
# Sector calendar-year heatmap
# ---------------------------------------------------------------------------

@router.get("/sectors/calendar-returns")
async def sector_calendar_returns(
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Annual sector returns ranked best→worst per calendar year (2006→present).
    Cached 24 h — data only changes once per trading day at most."""
    cache_key = "market:sectors:calendar-returns"
    cached = await get_cached(db, cache_key)
    if cached:
        return cached
    try:
        data = await get_sector_calendar_returns()
        result = {"years": data, "generated_at": __import__("datetime").datetime.utcnow().isoformat()}
        await set_cached(db, cache_key, result, ttl_seconds=_TTL_STATIC)
        return result
    except Exception as exc:
        logger.exception("Calendar returns failed: %s", exc)
        raise HTTPException(status_code=500, detail=f"Calendar returns failed: {exc}") from exc


# ---------------------------------------------------------------------------
# Investment style calendar-year heatmap
# ---------------------------------------------------------------------------

_VALID_STYLE_CATEGORIES = {"Factors", "Size & Indices", "Geography"}

@router.get("/styles/calendar-returns")
async def style_calendar_returns(
    category: str | None = Query(None, description="Filter: Factors | Size & Indices | Geography"),
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Annual returns for investment-style ETFs ranked best→worst per year.
    Pass ?category=Factors (etc.) to get one group; omit for all groups merged."""
    if category and category not in _VALID_STYLE_CATEGORIES:
        raise HTTPException(status_code=400, detail=f"Invalid category. Allowed: {sorted(_VALID_STYLE_CATEGORIES)}")

    cache_key = f"market:styles:calendar-returns:{category or 'all'}"
    cached = await get_cached(db, cache_key)
    if cached:
        return cached
    try:
        data = await get_style_calendar_returns(category=category)
        result = {
            "years": data,
            "category": category,
            "generated_at": __import__("datetime").datetime.utcnow().isoformat(),
        }
        await set_cached(db, cache_key, result, ttl_seconds=_TTL_STATIC)
        return result
    except Exception as exc:
        logger.exception("Style calendar returns failed: %s", exc)
        raise HTTPException(status_code=500, detail=f"Style calendar returns failed: {exc}") from exc


# ---------------------------------------------------------------------------
# Sector chat
# ---------------------------------------------------------------------------

class ChatTurn(BaseModel):
    role: str
    content: str


class SectorChatIn(BaseModel):
    question: str = Field(min_length=1, max_length=2000)
    timeframe: str = "1m"
    history: list[ChatTurn] | None = None


@router.post("/sectors/chat")
async def sectors_chat(
    body: SectorChatIn,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Ask questions about the live sector landscape — always user-initiated."""
    allowed = {"1d", "7d", "15d", "1m", "3m", "ytd", "1y", "3y", "5y"}
    if body.timeframe not in allowed:
        raise HTTPException(status_code=400, detail=f"Invalid timeframe.")
    key, model = await _resolve_llm(db, user)
    if not key:
        raise HTTPException(
            status_code=400,
            detail="OpenAI API key is not configured. Add one in Settings.",
        )
    try:
        return await sector_chat(
            question=body.question,
            timeframe=body.timeframe,
            history=[t.model_dump() for t in (body.history or [])],
            openai_key=key,
            model=model,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        logger.exception("Sector chat failed: %s", exc)
        raise HTTPException(status_code=500, detail=f"Sector chat failed: {exc}") from exc


# ---------------------------------------------------------------------------
# Pick & Shovel — always user-initiated
# ---------------------------------------------------------------------------

class PickShovelIn(BaseModel):
    theme: str = Field(min_length=3, max_length=500)
    # Optional research brief from the collaborative interpret/refine loop.
    brief: dict | None = None


class InterpretIn(BaseModel):
    thesis: str = Field(min_length=3, max_length=500)
    brief: dict | None = None
    user_reply: str | None = Field(default=None, max_length=1000)
    answers: list[dict] = Field(default_factory=list)


@router.post("/pick-shovel/interpret")
async def pick_shovel_interpret(
    body: InterpretIn,
    user: User = Depends(get_premium_user),
    db: AsyncSession = Depends(get_db),
):
    """Collaborative 'understand intent' pass: restate the thesis, surface the
    angles inferred from it, and ask thesis-specific clarifying questions ONLY when
    genuinely in doubt.  Not cached — interpretation must stay live as the user
    refines."""
    key, model = await _resolve_llm(db, user)
    if not key:
        raise HTTPException(
            status_code=400,
            detail="OpenAI API key is not configured. Add one in Settings.",
        )
    try:
        return await interpret_thesis(
            thesis=body.thesis,
            openai_key=key,
            model=model,
            brief=body.brief,
            user_reply=body.user_reply,
            answers=body.answers,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        logger.exception("Pick-shovel interpret failed: %s", exc)
        raise HTTPException(status_code=500, detail=f"Interpretation failed: {exc}") from exc


@router.post("/pick-shovel")
async def pick_shovel(
    body: PickShovelIn,
    user: User = Depends(get_premium_user),
    db: AsyncSession = Depends(get_db),
):
    """Three-tier pick-and-shovel analysis.  Always user-initiated (explicit
    search).  Results are cached per (user, theme, brief) for 1 hour to avoid
    duplicate LLM + yfinance calls on re-submission of the same theme."""
    key, model = await _resolve_llm(db, user)
    if not key:
        raise HTTPException(
            status_code=400,
            detail="OpenAI API key is not configured. Add one in Settings.",
        )
    # Per-user cache to prevent hammering LLM with identical requests. The brief
    # is folded into the key so a brief-tailored run is never served a stale
    # plain-theme blob (and vice versa).
    import hashlib, json as _json
    theme_slug = body.theme.strip().lower().replace(" ", "_")[:80]
    brief_hash = ""
    if body.brief:
        brief_hash = hashlib.md5(
            _json.dumps(body.brief, sort_keys=True, default=str).encode()
        ).hexdigest()[:8]
    cache_key = f"pick_shovel:{user.id}:{theme_slug}:{brief_hash}"
    cached = await get_cached(db, cache_key)
    if cached:
        cached["from_cache"] = True
        return cached

    try:
        result = await analyze_pick_shovel(
            theme=body.theme, openai_key=key, model=model, brief=body.brief,
        )
        await set_cached(db, cache_key, result, ttl_seconds=3600)
        return result
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        logger.exception("Pick-shovel analysis failed: %s", exc)
        raise HTTPException(status_code=500, detail=f"Analysis failed: {exc}") from exc


# ---------------------------------------------------------------------------
# Pick & Shovel — Dig Deeper (iterative discovery)
# ---------------------------------------------------------------------------

class AnchorCompany(BaseModel):
    ticker: str
    name: str
    supply_chain_role: str | None = None
    hidden_link: str | None = None
    thesis: str | None = None


class DigDeeperIn(BaseModel):
    theme: str = Field(min_length=3, max_length=500)
    already_shown: list[str] = Field(default_factory=list, description="All tickers shown so far")
    anchor_companies: list[AnchorCompany] = Field(default_factory=list)
    depth_level: int = Field(default=1, ge=1, le=10)


@router.post("/pick-shovel/deeper")
async def pick_shovel_deeper(
    body: DigDeeperIn,
    user: User = Depends(get_premium_user),
    db: AsyncSession = Depends(get_db),
):
    """Dig one level deeper into the supply chain — never repeating already-shown
    tickers.  Uses escalating investigation strategies per depth level."""
    key, model = await _resolve_llm(db, user)
    if not key:
        raise HTTPException(
            status_code=400,
            detail="OpenAI API key is not configured. Add one in Settings.",
        )

    # Cache key: per user + theme slug + depth + sorted shown tickers hash
    import hashlib
    shown_hash = hashlib.md5(",".join(sorted(body.already_shown)).encode()).hexdigest()[:8]
    theme_slug = body.theme.strip().lower().replace(" ", "_")[:60]
    cache_key = f"pick_shovel_deeper:{user.id}:{theme_slug}:d{body.depth_level}:{shown_hash}"
    cached = await get_cached(db, cache_key)
    if cached:
        cached["from_cache"] = True
        return cached

    try:
        result = await dig_deeper_pick_shovel(
            theme=body.theme,
            already_shown=body.already_shown,
            anchor_companies=[c.model_dump() for c in body.anchor_companies],
            depth_level=body.depth_level,
            openai_key=key,
            model=model,
        )
        await set_cached(db, cache_key, result, ttl_seconds=3600)
        return result
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        logger.exception("Dig deeper failed: %s", exc)
        raise HTTPException(status_code=500, detail=f"Dig deeper failed: {exc}") from exc


# ---------------------------------------------------------------------------
# Pick & Shovel — Refine (learn from kept/dropped, backfill dropped slots)
# ---------------------------------------------------------------------------

class RefineIn(BaseModel):
    brief: dict | None = None
    kept: list[dict] = Field(default_factory=list, description="Companies the user kept (positive signal)")
    dropped: list[dict] = Field(default_factory=list, description="[{ticker,name,tier,reason?}] dropped (negative signal)")
    already_shown: list[str] = Field(default_factory=list, description="All tickers shown so far — never repeat")


@router.post("/pick-shovel/refine")
async def pick_shovel_refine(
    body: RefineIn,
    user: User = Depends(get_premium_user),
    db: AsyncSession = Depends(get_db),
):
    """Learn from the user's keep/drop pattern and backfill ONLY the dropped slots
    with fresh, better-targeted picks — never repeating any shown ticker.  Not
    cached: the result depends on live session state (which cards were dropped)."""
    key, model = await _resolve_llm(db, user)
    if not key:
        raise HTTPException(
            status_code=400,
            detail="OpenAI API key is not configured. Add one in Settings.",
        )
    try:
        return await refine_recommendations(
            brief=body.brief,
            kept=body.kept,
            dropped=body.dropped,
            already_shown=body.already_shown,
            openai_key=key,
            model=model,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        logger.exception("Pick-shovel refine failed: %s", exc)
        raise HTTPException(status_code=500, detail=f"Refine failed: {exc}") from exc


# ---------------------------------------------------------------------------
# Macro Liquidity dashboard
# ---------------------------------------------------------------------------

_TTL_LIQUIDITY = 14400  # 4 h — FRED publishes weekly; DXY/SPY 15-min is fine
# Versioned cache key: bump the suffix whenever the dashboard payload shape
# changes so a stale older-shape blob is never served to a newer frontend.
_LIQUIDITY_CACHE_KEY = "market:liquidity:v3"  # v3 adds the Growth × Inflation regime


@router.get("/liquidity")
async def liquidity_dashboard(
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Macro liquidity snapshot: Net Fed Liquidity, DXY, HY Spread, SPY + OBV.
    Cached 4 h — FRED data is weekly so frequent refreshes add no value."""
    cache_key = _LIQUIDITY_CACHE_KEY
    cached = await get_cached(db, cache_key)
    if cached:
        return cached
    try:
        result = await get_liquidity_dashboard(months=24)
        await set_cached(db, cache_key, result, ttl_seconds=_TTL_LIQUIDITY)
        return result
    except Exception as exc:
        logger.exception("Liquidity dashboard failed: %s", exc)
        raise HTTPException(status_code=500, detail=f"Liquidity dashboard failed: {exc}") from exc


@router.post("/liquidity/narrative")
async def liquidity_narrative(
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """AI macro liquidity briefing — called only on explicit user action."""
    key, model = await _resolve_llm(db, user)
    if not key:
        raise HTTPException(
            status_code=400,
            detail="OpenAI API key not configured. Add one in Settings.",
        )
    cache_key = _LIQUIDITY_CACHE_KEY
    cached = await get_cached(db, cache_key)
    if not cached:
        cached = await get_liquidity_dashboard(months=24)
        await set_cached(db, cache_key, cached, ttl_seconds=_TTL_LIQUIDITY)
    try:
        narrative = await _llm_liquidity_narrative(cached, key, model)
        return {"narrative": narrative}
    except Exception as exc:
        logger.exception("Liquidity narrative failed: %s", exc)
        raise HTTPException(status_code=500, detail=f"Narrative failed: {exc}") from exc


# ---------------------------------------------------------------------------
# Top movers
# ---------------------------------------------------------------------------

@router.get("/movers")
async def movers(
    timeframe: str = Query("1d", description="Movers timeframe: 1d, 7d, 15d, 1m, 3m, ytd, 1y"),
    limit: int = Query(10, ge=3, le=25),
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Top gainers & losers — yfinance only, no auto LLM."""
    allowed = {"1d", "7d", "15d", "1m", "3m", "ytd", "1y"}
    if timeframe not in allowed:
        raise HTTPException(status_code=400, detail=f"Invalid timeframe. Allowed: {sorted(allowed)}")

    cache_key = f"market:movers:{timeframe}:{limit}"
    cached = await get_cached(db, cache_key)
    if cached:
        return cached

    try:
        result = await get_top_movers(timeframe=timeframe, limit=limit, openai_key=None)
        await set_cached(db, cache_key, result, ttl_seconds=_TTL_PRICE)
        return result
    except Exception as exc:
        logger.exception("Top movers failed: %s", exc)
        raise HTTPException(status_code=500, detail=f"Top movers failed: {exc}") from exc


# ---------------------------------------------------------------------------
# Business / Economic Cycle dashboard
# ---------------------------------------------------------------------------

_TTL_CYCLE = 21600  # 6 h — cycle phases don't change intraday


@router.get("/business-cycle")
async def business_cycle(
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Global business cycle classification + asset performance matrix.

    Phase classification uses OECD CLI (amplitude-adjusted, monthly).
    GDP data from IMF DataMapper (NGDP_RPCH).
    LLM adds key signals/outlook if the user has an OpenAI key configured.
    Cached 6 h — OECD CLI publishes monthly; intraday refresh adds no value."""
    key, model = await _resolve_llm(db, user)
    # v2 = OECD CLI + IMF GDP (replaces v1 ETF-based approach)
    cache_key = f"market:business_cycle:v3:{'llm' if key else 'data'}"
    cached = await get_cached(db, cache_key)
    if cached:
        return cached

    try:
        result = await get_business_cycle(openai_key=key, model=model)
        await set_cached(db, cache_key, result, ttl_seconds=_TTL_CYCLE)
        return result
    except Exception as exc:
        logger.exception("Business cycle failed: %s", exc)
        raise HTTPException(status_code=500, detail=f"Business cycle failed: {exc}") from exc


@router.post("/business-cycle/refresh")
async def business_cycle_refresh(
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Force-refresh the business cycle assessment (bypasses cache)."""
    key, model = await _resolve_llm(db, user)
    try:
        result = await get_business_cycle(openai_key=key, model=model)
        cache_key = f"market:business_cycle:v3:{'llm' if key else 'data'}"
        await set_cached(db, cache_key, result, ttl_seconds=_TTL_CYCLE)
        return result
    except Exception as exc:
        logger.exception("Business cycle refresh failed: %s", exc)
        raise HTTPException(status_code=500, detail=f"Business cycle refresh failed: {exc}") from exc


# ---------------------------------------------------------------------------
# US cycle sector technical analysis
# ---------------------------------------------------------------------------

_TTL_SECTOR_TA = 900  # 15 min — price data


@router.get("/cycle-sectors-ta")
async def cycle_sectors_ta(
    phase: str = "mid",
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Technical analysis for the top 3 sector ETFs favored in the given US cycle phase.

    Returns price chart (90d), SMA20/50/200, RSI, MACD, and actionable signals.
    Cached 15 min per phase."""
    allowed = {"early", "mid", "late", "recession"}
    if phase not in allowed:
        raise HTTPException(status_code=400, detail=f"phase must be one of {allowed}")

    cache_key = f"market:cycle_sectors_ta:{phase}"
    cached = await get_cached(db, cache_key)
    if cached:
        return cached

    try:
        result = await get_cycle_sectors_ta(phase=phase)
        await set_cached(db, cache_key, result, ttl_seconds=_TTL_SECTOR_TA)
        return result
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        logger.exception("Cycle sectors TA failed: %s", exc)
        raise HTTPException(status_code=500, detail=f"Sector TA failed: {exc}") from exc
