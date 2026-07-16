"""API metrics and observability endpoints."""

import datetime
from sqlalchemy import select, func, and_, case
from sqlalchemy.ext.asyncio import AsyncSession
from fastapi import APIRouter, Depends, Query, HTTPException

from ..database import get_db
from ..models import ApiMetric
from ..auth import get_current_user, User

router = APIRouter(prefix="/api/metrics", tags=["metrics"])

@router.get("/summary")
async def get_metrics_summary(
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user)
):
    """Get high level API metric summary (e.g. RPS, total calls)."""
    now = datetime.datetime.now(datetime.timezone.utc)
    
    # Over the last 24h by provider
    yesterday = now - datetime.timedelta(days=1)
    
    query = (
        select(ApiMetric.provider, func.count(), func.avg(ApiMetric.latency_ms))
        .where(ApiMetric.created_at >= yesterday)
        .group_by(ApiMetric.provider)
    )
    result = await db.execute(query)
    provider_data = []
    
    total_calls = 0
    for row in result:
        provider, count, avg_lat = row
        total_calls += count
        provider_data.append({
            "provider": provider,
            "total_24h": count,
            "avg_latency_ms": round(avg_lat, 2) if avg_lat else 0,
            "rpm_24h": round(count / (24 * 60), 2)
        })

    # Get error count
    error_query = (
        select(ApiMetric.provider, func.count())
        .where(and_(ApiMetric.created_at >= yesterday, ApiMetric.status_code >= 400))
        .group_by(ApiMetric.provider)
    )
    error_result = await db.execute(error_query)
    errors = {row[0]: row[1] for row in error_result}
    
    for pd in provider_data:
        pd["error_count_24h"] = errors.get(pd["provider"], 0)
        pd["error_rate_24h"] = round(pd["error_count_24h"] / pd["total_24h"] * 100, 2) if pd["total_24h"] > 0 else 0

    return {
        "providers": provider_data,
        "total_requests": total_calls
    }

@router.get("/timeseries")
async def get_metrics_timeseries(
    provider: str = Query("all"),
    endpoint_name: str | None = Query(None),
    days: int = Query(7),
    interval: str = Query("day"),
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user)
):
    """Get timeseries data for charting request volume and latency."""
    now = datetime.datetime.now(datetime.timezone.utc)
    start_date = now - datetime.timedelta(days=days)
    
    from ..config import settings
    sqlite_formats = {
        "second": "%Y-%m-%d %H:%M:%S",
        "minute": "%Y-%m-%d %H:%M:00",
        "hour": "%Y-%m-%d %H:00:00",
        "day": "%Y-%m-%d"
    }
    pg_formats = {
        "second": "YYYY-MM-DD HH24:MI:SS",
        "minute": "YYYY-MM-DD HH24:MI:00",
        "hour": "YYYY-MM-DD HH24:00:00",
        "day": "YYYY-MM-DD"
    }
    
    if "sqlite" in settings.DATABASE_URL:
        # SQLite
        fmt = sqlite_formats.get(interval, sqlite_formats["day"])
        date_trunc = func.strftime(fmt, ApiMetric.created_at)
    else:
        # PostgreSQL
        fmt = pg_formats.get(interval, pg_formats["day"])
        date_trunc = func.to_char(ApiMetric.created_at, fmt)
    
    base_query = select(
        date_trunc.label('time_bucket'),
        ApiMetric.provider,
        func.count().label('requests'),
        func.avg(ApiMetric.latency_ms).label('avg_lat'),
        func.sum(case((ApiMetric.status_code >= 400, 1), else_=0)).label('errors')
    ).where(ApiMetric.created_at >= start_date)
    
    if provider != "all":
        base_query = base_query.where(ApiMetric.provider == provider)
    
    if endpoint_name:
        base_query = base_query.where(ApiMetric.endpoint == endpoint_name)
        
    query = base_query.group_by('time_bucket', ApiMetric.provider).order_by('time_bucket')
    
    result = await db.execute(query)
    
    timeseries = []
    for row in result:
        time_bucket, prov, reqs, lat, errs = row
        timeseries.append({
            "timestamp": time_bucket,
            "provider": prov,
            "requests": reqs,
            "avg_latency_ms": round(lat, 2) if lat else 0,
            "errors": errs or 0
        })
        
    return {"timeseries": timeseries}


@router.get("/endpoints")
async def get_endpoints_summary(
    provider: str = Query("all"),
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user)
):
    """Get request distribution by endpoint destination."""
    now = datetime.datetime.now(datetime.timezone.utc)
    start_date = now - datetime.timedelta(days=7)
    
    base_query = select(
        ApiMetric.endpoint,
        ApiMetric.provider,
        func.count().label('requests'),
        func.avg(ApiMetric.latency_ms).label('latency'),
        func.sum(case((ApiMetric.status_code >= 400, 1), else_=0)).label('errors')
    ).where(ApiMetric.created_at >= start_date)
    
    if provider != "all":
        base_query = base_query.where(ApiMetric.provider == provider)
        
    query = base_query.group_by(ApiMetric.endpoint, ApiMetric.provider).order_by(func.count().desc())
    
    result = await db.execute(query)
    endpoints = []
    
    for row in result:
        ep, prov, count, lat, err = row
        endpoints.append({
            "endpoint": ep,
            "provider": prov,
            "requests": count,
            "avg_latency_ms": round(lat, 2) if lat else 0,
            "errors": err or 0
        })
        
    return {"endpoints": endpoints}
