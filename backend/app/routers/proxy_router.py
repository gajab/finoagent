"""Proxy router — ``/api/proxy``.

Forwards requests to external APIs (OpenAI, SerpAPI) using the user's
stored API keys.
"""

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from ..auth import get_current_user, get_user_api_key
from ..database import get_db
from ..models import User
from ..services.llm_service import call_llm
from ..services.search_service import web_search

router = APIRouter(prefix="/api/proxy", tags=["proxy"])


# ---------------------------------------------------------------------------
# Request bodies
# ---------------------------------------------------------------------------

class LLMRequest(BaseModel):
    model: str = "gpt-4o-mini"
    messages: list[dict]
    max_tokens: int = 1000
    expect_json: bool = False


class SearchRequest(BaseModel):
    query: str


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------

@router.post("/llm")
async def proxy_llm(
    body: LLMRequest,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Proxy a chat completion request to the LLM provider using the user's API key."""
    model_pref = await get_user_api_key(db, user.id, "openai_model")
    is_gemini = (body.model and body.model.startswith("gemini-")) or (model_pref and model_pref.startswith("gemini-"))

    api_key = await get_user_api_key(db, user.id, "openai_api_key")
    if not api_key:
        provider_name = "Gemini" if is_gemini else "OpenAI"
        raise HTTPException(
            status_code=400,
            detail=f"{provider_name} API key not configured. Please add it in Settings.",
        )

    try:
        content = await call_llm(
            api_key=api_key,
            model=body.model,
            messages=body.messages,
            max_tokens=body.max_tokens,
            expect_json=body.expect_json,
        )
        return {"content": content}
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"LLM request failed: {exc}")


@router.post("/search")
async def proxy_search(
    body: SearchRequest,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Proxy a web search request to SerpAPI using the user's API key."""
    api_key = await get_user_api_key(db, user.id, "search_api_key")
    if not api_key:
        raise HTTPException(
            status_code=400,
            detail="Search API key (SerpAPI) not configured. Please add it in Settings.",
        )

    try:
        results = await web_search(api_key=api_key, query=body.query)
        return {"results": results}
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"Search request failed: {exc}")
