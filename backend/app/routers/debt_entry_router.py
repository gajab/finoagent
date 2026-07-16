"""Debt Radar router — ``/api/debt``.

Real-time entry-timing tracker for debt instruments (CLO/loan/IG/HY/Treasury/
MBS/muni/EM-debt ETFs).  ``GET /entry/{ticker}`` returns the classification,
live NAV premium/discount, the seven signal families and the composite Entry
Score.  ``POST /entry/{ticker}/explain`` generates an on-demand LLM read using
the user's own OpenAI key (never auto-called).

Caching: the composed payload is DB-cached briefly so price/NAV stay fresh,
while the (shared) FRED macro series are memoised in-process for ~6h inside
``rates_service`` — so a cache miss here doesn't re-hit FRED.
"""

import logging

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from ..auth import get_premium_user, get_user_api_key
from ..database import get_db
from ..models import User
from ..services.cache_service import get_cached, set_cached
from ..services.debt_entry_service import get_debt_entry, explain_instrument, get_price_history

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/debt", tags=["debt"])

_TTL_ENTRY = 120     # 2 min — keep price / NAV premium fresh ("real-time")
_TTL_REJECT = 600    # 10 min — cache non-debt rejects to absorb typos
_TTL_HISTORY = 900   # 15 min — price-history chart / dividends


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


@router.get("/entry/{ticker}")
async def debt_entry(
    ticker: str,
    user: User = Depends(get_premium_user),
    db: AsyncSession = Depends(get_db),
):
    """Classification + live premium/discount + 7 signal families + composite score."""
    ticker = ticker.upper().strip()
    cache_key = f"debt:entry:{ticker}:v1"
    cached = await get_cached(db, cache_key)
    if cached:
        return cached

    try:
        result = await get_debt_entry(ticker)
    except Exception as exc:  # noqa: BLE001 — surface a clean 502 to the client
        logger.exception("debt entry failed for %s", ticker)
        raise HTTPException(status_code=502, detail=f"Could not analyze {ticker}: {exc}")

    ttl = _TTL_ENTRY if result.get("is_debt") else _TTL_REJECT
    await set_cached(db, cache_key, result, ttl_seconds=ttl)
    return result


@router.get("/history/{ticker}")
async def debt_history(
    ticker: str,
    user: User = Depends(get_premium_user),
    db: AsyncSession = Depends(get_db),
):
    """Multi-range price series (1D/1M/3M/YTD/1Y/5Y/10Y) + dividend history + next ex-date."""
    ticker = ticker.upper().strip()
    cache_key = f"debt:history:{ticker}:v1"
    cached = await get_cached(db, cache_key)
    if cached:
        return cached

    try:
        result = await get_price_history(ticker)
    except Exception as exc:  # noqa: BLE001
        logger.exception("debt history failed for %s", ticker)
        raise HTTPException(status_code=502, detail=f"Could not load history for {ticker}: {exc}")

    await set_cached(db, cache_key, result, ttl_seconds=_TTL_HISTORY)
    return result


@router.post("/entry/{ticker}/explain")
async def debt_entry_explain(
    ticker: str,
    user: User = Depends(get_premium_user),
    db: AsyncSession = Depends(get_db),
):
    """On-demand plain-English read of holdings + signal setup (uses the user's key)."""
    ticker = ticker.upper().strip()
    key, model = await _resolve_llm(db, user)
    if not key:
        raise HTTPException(status_code=400,
                            detail="Add your OpenAI API key in Settings to use AI explanations.")

    cache_key = f"debt:entry:{ticker}:v1"
    payload = await get_cached(db, cache_key)
    if payload is None:
        payload = await get_debt_entry(ticker)
        await set_cached(db, cache_key, payload,
                         ttl_seconds=_TTL_ENTRY if payload.get("is_debt") else _TTL_REJECT)
    if not payload.get("is_debt"):
        raise HTTPException(status_code=400, detail=payload.get("reject_reason") or "Not a debt instrument.")

    try:
        explanation = await explain_instrument(payload, key, model)
    except Exception as exc:  # noqa: BLE001
        logger.exception("debt explain failed for %s", ticker)
        raise HTTPException(status_code=502, detail=f"AI explanation failed: {exc}")
    return {"ticker": ticker, "explanation": explanation}
