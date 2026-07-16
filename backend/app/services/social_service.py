"""Social-media signal for Pick & Shovel v2 deep dives.

StockTwits' public symbol stream is free and key-less (verified), so it's the
primary source. Reddit now requires OAuth and Twitter/X requires a paid plan, so
those are intentionally left as optional future adds. All best-effort: failures
return empty, never raise.
"""

from __future__ import annotations

import logging

from .cache_service import get_cached, set_cached

logger = logging.getLogger(__name__)

_TTL = 3600  # 1 h — chatter moves but we don't need to hammer

# StockTwits sits behind Cloudflare and TLS-fingerprints clients, so plain httpx
# gets a 403. curl_cffi impersonates a real Chrome TLS handshake and gets through.


async def stocktwits_messages(db, ticker: str, limit: int = 30) -> dict:
    """Recent StockTwits messages for ``ticker``.

    Returns ``{ok, source, messages: [str], url}`` (best-effort, cached 1h).
    """
    ticker = (ticker or "").strip().upper()
    out = {"ok": False, "source": "StockTwits", "messages": [],
           "url": f"https://stocktwits.com/symbol/{ticker}" if ticker else ""}
    if not ticker:
        return out

    cache_key = f"social:stocktwits:{ticker}"
    cached = await get_cached(db, cache_key) if db is not None else None
    if cached and isinstance(cached, dict):
        return cached

    url = f"https://api.stocktwits.com/api/2/streams/symbol/{ticker}.json"
    try:
        from curl_cffi.requests import AsyncSession
        async with AsyncSession() as session:
            resp = await session.get(url, impersonate="chrome", timeout=15)
        if resp.status_code != 200:
            raise RuntimeError(f"status {resp.status_code}")
        data = resp.json()
        msgs = [(m.get("body") or "").strip() for m in (data.get("messages") or [])]
        msgs = [m for m in msgs if m][:limit]
        out = {"ok": bool(msgs), "source": "StockTwits", "messages": msgs,
               "url": f"https://stocktwits.com/symbol/{ticker}"}
    except Exception as exc:
        logger.info("StockTwits fetch failed for %s: %s", ticker, exc)

    if db is not None and out["ok"]:
        await set_cached(db, cache_key, out, ttl_seconds=_TTL)
    return out
