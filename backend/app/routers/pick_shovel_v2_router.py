"""Pick & Shovel v2 router — ``/api/research/v2``.

Drives the structured, evidence-grounded research wizard. One endpoint per stage;
all premium-gated and dependent on the user's OpenAI key.  No SerpAPI — deep
research is grounded in SEC EDGAR + company investor materials.
"""

import logging

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from ..auth import get_premium_user, get_user_api_key
from ..database import get_db
from ..models import User
from ..services import pick_shovel_v2_service as v2
from ..services.cache_service import get_cached, set_cached

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/research/v2", tags=["research-v2"])

_TTL = 86400  # 24 h for cacheable stages


async def _resolve_llm(db: AsyncSession, user: User) -> tuple[str | None, str]:
    """Fetch the user's OpenAI key + preferred model (mirrors market_router)."""
    try:
        key = await get_user_api_key(db, user.id, "openai_api_key")
    except Exception:
        key = None
    try:
        model = await get_user_api_key(db, user.id, "openai_model")
    except Exception:
        model = None
    return key, (model or "gpt-4o")


def _require_key(key: str | None) -> str:
    if not key:
        raise HTTPException(status_code=400, detail="OpenAI API key is not configured. Add one in Settings.")
    return key


def _slug(s: str, n: int = 60) -> str:
    return s.strip().lower().replace(" ", "_")[:n]


# ---------------------------------------------------------------------------
# Request models
# ---------------------------------------------------------------------------

class DecomposeIn(BaseModel):
    theme: str = Field(min_length=3, max_length=300)
    extra_context: str = Field(default="", max_length=4000)


class RefineComponentsIn(BaseModel):
    theme: str = Field(min_length=3, max_length=300)
    components: list[dict] = Field(default_factory=list)
    user_input: str = Field(default="", max_length=2000)


class EtfsIn(BaseModel):
    theme: str = Field(min_length=3, max_length=300)
    components: list[dict] = Field(default_factory=list)


class ComponentCompaniesIn(BaseModel):
    theme: str = Field(min_length=3, max_length=300)
    component: str = Field(min_length=1, max_length=200)
    description: str = Field(default="", max_length=600)
    existing: list[str] = Field(default_factory=list)


class EtfHoldingsIn(BaseModel):
    ticker: str = Field(min_length=1, max_length=12)


class IngestIn(BaseModel):
    theme: str = Field(min_length=3, max_length=300)
    inputs: list[dict] = Field(default_factory=list)  # [{type:'text'|'link'|'image', value}]


class ValidateIn(BaseModel):
    tickers: list[str] = Field(default_factory=list)


class MatchIn(BaseModel):
    theme: str = Field(min_length=3, max_length=300)
    components: list[dict] = Field(default_factory=list)
    companies: list[dict] = Field(default_factory=list)
    deep_dives: list[dict] = Field(default_factory=list)


class DeepDiveIn(BaseModel):
    theme: str = Field(min_length=3, max_length=300)
    ticker: str = Field(min_length=1, max_length=12)
    name: str = Field(default="", max_length=200)
    website: str | None = None


class SummaryIn(BaseModel):
    theme: str = Field(min_length=3, max_length=300)
    deep_dives: list[dict] = Field(default_factory=list)
    components: list[dict] = Field(default_factory=list)
    holdings: list[dict] = Field(default_factory=list)
    companies: list[dict] = Field(default_factory=list)
    emphasis: list[str] = Field(default_factory=list)


class FinalPickIn(BaseModel):
    theme: str = Field(min_length=3, max_length=300)
    deep_dives: list[dict] = Field(default_factory=list)
    summary: dict = Field(default_factory=dict)
    emphasis: list[str] = Field(default_factory=list)
    components: list[dict] = Field(default_factory=list)
    holdings: list[dict] = Field(default_factory=list)
    companies: list[dict] = Field(default_factory=list)
    matches: list[dict] = Field(default_factory=list)


class ComponentPicksIn(BaseModel):
    theme: str = Field(min_length=3, max_length=300)
    component: str = Field(min_length=1, max_length=200)
    anchors: list[dict] = Field(default_factory=list)
    deep_dives: list[dict] = Field(default_factory=list)
    emphasis: list[str] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------

@router.post("/decompose")
async def decompose(body: DecomposeIn, user: User = Depends(get_premium_user), db: AsyncSession = Depends(get_db)):
    key, model = await _resolve_llm(db, user)
    _require_key(key)
    cache_key = f"psv2:decompose:{user.id}:{_slug(body.theme)}"
    if not body.extra_context:
        cached = await get_cached(db, cache_key)
        if cached:
            cached["from_cache"] = True
            return cached
    try:
        result = await v2.decompose_theme(body.theme, key, model, body.extra_context)
        if not body.extra_context:
            await set_cached(db, cache_key, result, ttl_seconds=_TTL)
        return result
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        logger.exception("v2 decompose failed: %s", exc)
        raise HTTPException(status_code=500, detail=f"Decompose failed: {exc}") from exc


@router.post("/components/companies")
async def components_companies(body: ComponentCompaniesIn, user: User = Depends(get_premium_user), db: AsyncSession = Depends(get_db)):
    """Find listed public companies specific to ONE component's space (no ETF needed)."""
    key, model = await _resolve_llm(db, user)
    _require_key(key)
    try:
        return await v2.find_component_companies(body.theme, body.component, body.description, body.existing, key, model)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        logger.exception("v2 component companies failed: %s", exc)
        raise HTTPException(status_code=500, detail=f"Find companies failed: {exc}") from exc


@router.post("/components/refine")
async def components_refine(body: RefineComponentsIn, user: User = Depends(get_premium_user), db: AsyncSession = Depends(get_db)):
    key, model = await _resolve_llm(db, user)
    _require_key(key)
    try:
        return await v2.refine_components(body.theme, body.components, body.user_input, key, model)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        logger.exception("v2 refine_components failed: %s", exc)
        raise HTTPException(status_code=500, detail=f"Refine failed: {exc}") from exc


@router.post("/etfs")
async def etfs(body: EtfsIn, user: User = Depends(get_premium_user), db: AsyncSession = Depends(get_db)):
    key, model = await _resolve_llm(db, user)
    _require_key(key)
    cache_key = f"psv2:etfs:{user.id}:{_slug(body.theme)}"
    cached = await get_cached(db, cache_key)
    if cached:
        cached["from_cache"] = True
        return cached
    try:
        result = await v2.discover_etfs(body.theme, body.components, key, model)
        await set_cached(db, cache_key, result, ttl_seconds=_TTL)
        return result
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        logger.exception("v2 etfs failed: %s", exc)
        raise HTTPException(status_code=500, detail=f"ETF discovery failed: {exc}") from exc


@router.post("/etfs/holdings")
async def etfs_holdings(body: EtfHoldingsIn, user: User = Depends(get_premium_user), db: AsyncSession = Depends(get_db)):
    try:
        return await v2.etf_holdings(body.ticker)
    except Exception as exc:
        logger.exception("v2 etf_holdings failed: %s", exc)
        raise HTTPException(status_code=500, detail=f"Holdings fetch failed: {exc}") from exc


@router.post("/inputs/ingest")
async def inputs_ingest(body: IngestIn, user: User = Depends(get_premium_user), db: AsyncSession = Depends(get_db)):
    key, model = await _resolve_llm(db, user)
    _require_key(key)
    try:
        return await v2.ingest_user_inputs(db, body.theme, body.inputs, key, model)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        logger.exception("v2 ingest failed: %s", exc)
        raise HTTPException(status_code=500, detail=f"Ingest failed: {exc}") from exc


@router.post("/companies/validate")
async def companies_validate(body: ValidateIn, user: User = Depends(get_premium_user), db: AsyncSession = Depends(get_db)):
    try:
        return await v2.enrich_companies(body.tickers)
    except Exception as exc:
        logger.exception("v2 validate failed: %s", exc)
        raise HTTPException(status_code=500, detail=f"Validation failed: {exc}") from exc


@router.post("/match")
async def match(body: MatchIn, user: User = Depends(get_premium_user), db: AsyncSession = Depends(get_db)):
    key, model = await _resolve_llm(db, user)
    _require_key(key)
    try:
        return await v2.match_components(body.theme, body.components, body.companies, key, model, deep_dives=body.deep_dives)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        logger.exception("v2 match failed: %s", exc)
        raise HTTPException(status_code=500, detail=f"Match failed: {exc}") from exc


@router.post("/deep-dive")
async def deep_dive(body: DeepDiveIn, user: User = Depends(get_premium_user), db: AsyncSession = Depends(get_db)):
    key, model = await _resolve_llm(db, user)
    _require_key(key)
    cache_key = f"psv2:deepdive:{user.id}:{body.ticker.strip().upper()}:{_slug(body.theme, 40)}"
    cached = await get_cached(db, cache_key)
    if cached:
        cached["from_cache"] = True
        return cached
    try:
        result = await v2.deep_dive_company(db, body.ticker, body.name, body.theme, key, model, body.website)
        await set_cached(db, cache_key, result, ttl_seconds=_TTL)
        return result
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        logger.exception("v2 deep_dive failed: %s", exc)
        raise HTTPException(status_code=500, detail=f"Deep dive failed: {exc}") from exc


@router.post("/summary")
async def summary(body: SummaryIn, user: User = Depends(get_premium_user), db: AsyncSession = Depends(get_db)):
    key, model = await _resolve_llm(db, user)
    _require_key(key)
    try:
        return await v2.summarize_research(
            body.theme, body.deep_dives, key, model,
            components=body.components, holdings=body.holdings, companies=body.companies,
            emphasis=body.emphasis,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        logger.exception("v2 summary failed: %s", exc)
        raise HTTPException(status_code=500, detail=f"Summary failed: {exc}") from exc


@router.post("/pick-shovel")
async def pick_shovel(body: FinalPickIn, user: User = Depends(get_premium_user), db: AsyncSession = Depends(get_db)):
    key, model = await _resolve_llm(db, user)
    _require_key(key)
    try:
        return await v2.synthesize_pick_shovel(
            body.theme, key, model,
            components=body.components, holdings=body.holdings, companies=body.companies,
            matches=body.matches, deep_dives=body.deep_dives, summary=body.summary,
            emphasis=body.emphasis,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        logger.exception("v2 pick_shovel failed: %s", exc)
        raise HTTPException(status_code=500, detail=f"Pick & shovel failed: {exc}") from exc


@router.post("/component-picks")
async def component_picks(body: ComponentPicksIn, user: User = Depends(get_premium_user), db: AsyncSession = Depends(get_db)):
    """Find pick-&-shovels for ONE component's matched anchor companies only."""
    key, model = await _resolve_llm(db, user)
    _require_key(key)
    try:
        return await v2.component_picks(
            body.theme, body.component, body.anchors, body.deep_dives, key, model,
            emphasis=body.emphasis,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        logger.exception("v2 component_picks failed: %s", exc)
        raise HTTPException(status_code=500, detail=f"Component picks failed: {exc}") from exc
