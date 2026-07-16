"""Lightweight DB-backed cache for yfinance / market data.

Usage::

    from .cache_service import get_cached, set_cached

    data = await get_cached(db, "market:overview")
    if data is None:
        data = expensive_fetch()
        await set_cached(db, "market:overview", data, ttl_seconds=900)

``ttl_seconds`` guidelines
--------------------------
* Prices / returns  : 900 s  (15 min)  — stale but acceptable for most use
* Sector analytics  : 900 s
* Static info       : 86400 s (24 h) — sector names, market caps rarely change
* Pick-shovel result: 3600 s (1 h) per (theme, user) key
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timedelta, timezone

from sqlalchemy import delete, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from ..models import DataCache

logger = logging.getLogger(__name__)


async def get_cached(db: AsyncSession, key: str) -> dict | list | None:
    """Return cached payload for ``key`` if it exists and hasn't expired."""
    now = datetime.now(timezone.utc)
    try:
        row = (
            await db.execute(
                select(DataCache).where(DataCache.cache_key == key)
            )
        ).scalar_one_or_none()
        if row is None:
            return None
        if row.expires_at.replace(tzinfo=timezone.utc) <= now:
            # Expired — delete lazily
            await db.execute(delete(DataCache).where(DataCache.cache_key == key))
            await db.commit()
            return None
        return json.loads(row.payload)
    except Exception as exc:
        logger.debug("Cache read failed for key %r: %s", key, exc)
        return None


async def set_cached(
    db: AsyncSession,
    key: str,
    data: dict | list,
    ttl_seconds: int = 900,
) -> None:
    """Upsert ``data`` into the cache with the given TTL."""
    expires = datetime.now(timezone.utc) + timedelta(seconds=ttl_seconds)
    payload = json.dumps(data, default=str)
    try:
        existing = (
            await db.execute(
                select(DataCache).where(DataCache.cache_key == key)
            )
        ).scalar_one_or_none()
        if existing:
            await db.execute(
                update(DataCache)
                .where(DataCache.cache_key == key)
                .values(payload=payload, expires_at=expires)
            )
        else:
            db.add(DataCache(cache_key=key, payload=payload, expires_at=expires))
        await db.commit()
    except Exception as exc:
        logger.warning("Cache write failed for key %r: %s", key, exc)
        try:
            await db.rollback()
        except Exception:
            pass


async def invalidate(db: AsyncSession, key: str) -> None:
    """Delete a cache entry."""
    try:
        await db.execute(delete(DataCache).where(DataCache.cache_key == key))
        await db.commit()
    except Exception as exc:
        logger.debug("Cache invalidate failed for %r: %s", key, exc)
        await db.rollback()


async def purge_expired(db: AsyncSession) -> int:
    """Delete all expired rows. Returns count deleted."""
    now = datetime.now(timezone.utc)
    try:
        result = await db.execute(
            delete(DataCache).where(DataCache.expires_at <= now)
        )
        await db.commit()
        return result.rowcount or 0
    except Exception as exc:
        logger.warning("Cache purge failed: %s", exc)
        await db.rollback()
        return 0
