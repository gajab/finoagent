"""Background scheduler — checks for agents whose next_run_at has passed and triggers them."""

import asyncio
import datetime
import logging
import time

from croniter import croniter
from sqlalchemy import select, distinct
from sqlalchemy.exc import DBAPIError, OperationalError
from sqlalchemy.ext.asyncio import AsyncSession

from ..database import async_session
from ..models import Agent, AgentRun, PortfolioHolding
from ..auth import get_user_api_key
from .agent_service import execute_agent
from .portfolio_enrichment_service import fetch_prices_cached, fetch_enriched_view
from .rate_limit_service import breaker_open

logger = logging.getLogger(__name__)

CHECK_INTERVAL_SECONDS = 30  # how often the loop checks for due agents
_MAX_TICK_RETRIES = 2        # retries on transient DB connection errors

# ── Portfolio cache warmer ────────────────────────────────────────────────────
# Keeps the price + enrichment caches hot for the union of every user's holdings
# so a 300-holding page load reads warm cache instead of firing fetches on the
# request path. Prices (cheap, batched) refresh every cycle; the per-ticker
# `.info` enrichment views rotate one-per-cycle to spread their cost. Everything
# goes through the same rate-limiter/breaker, so warming can never bombard Yahoo.
_WARM_INTERVAL_SECONDS = 600          # warm at most once every 10 min
_WARM_STARTUP_DELAY = 120             # wait 2 min after boot before first warm
_WARM_VIEWS = ("technical", "dividends", "fundamentals")
_WARM_MAX_TICKERS = 1000              # safety cap on universe size per cycle
_last_warm_at = time.monotonic() - (_WARM_INTERVAL_SECONDS - _WARM_STARTUP_DELAY)  # first warm after startup delay
_warm_view_index = 0

# Known ticker aliases: some brokers/users store tickers that yfinance doesn't accept.
_TICKER_ALIASES: dict[str, str] = {
    "BRKB":  "BRK-B",
    "BRK.B": "BRK-B",
    "BRKA":  "BRK-A",
    "BRK.A": "BRK-A",
}


def _normalize_ticker(ticker: str) -> str:
    """Map broker-style tickers to yfinance-compatible symbols."""
    return _TICKER_ALIASES.get(ticker.upper(), ticker)


async def _distinct_holdings_tickers(db: AsyncSession) -> list[str]:
    rows = await db.execute(select(distinct(PortfolioHolding.ticker)))
    tickers = [_normalize_ticker(t) for (t,) in rows.all() if t]
    # Deduplicate after normalization (e.g. BRKB and BRK-B both map to BRK-B)
    seen: set[str] = set()
    result = []
    for t in tickers:
        if t not in seen:
            seen.add(t)
            result.append(t)
    return result[:_WARM_MAX_TICKERS]


async def _warm_portfolio_universe() -> None:
    """Refresh cache-cold prices + one enrichment view for the holdings universe."""
    global _warm_view_index
    if breaker_open():
        logger.info("Warmer: skipping — yfinance breaker open")
        return
    try:
        async with async_session() as db:
            tickers = await _distinct_holdings_tickers(db)
            if not tickers:
                return
            # Prices: batched + cheap, keep them warm every cycle.
            await fetch_prices_cached(db, tickers)
            # Enrichment: rotate one view per cycle so we never do 3×N `.info`.
            view = _WARM_VIEWS[_warm_view_index % len(_WARM_VIEWS)]
            _warm_view_index += 1
            await fetch_enriched_view(db, tickers, view)
            logger.info("Warmer: refreshed %d tickers (prices + %s)", len(tickers), view)
    except Exception:
        logger.exception("Portfolio warm failed")


async def _tick():
    """Single scheduler tick: find due agents and execute them."""
    now = datetime.datetime.now(datetime.timezone.utc)

    # ---------------------------------------------------------
    # System Cleanup Tasks
    # ---------------------------------------------------------
    async def _cleanup_metrics():
        try:
            async with async_session() as db_session:
                from sqlalchemy import delete
                from ..models import ApiMetric
                seven_days_ago = now - datetime.timedelta(days=7)
                await db_session.execute(delete(ApiMetric).where(ApiMetric.created_at < seven_days_ago))
                await db_session.commit()
        except Exception as e:
            logger.error(f"Failed to cleanup old metrics: {e}")

    asyncio.create_task(_cleanup_metrics())

    # ---------------------------------------------------------
    # Portfolio cache warmer (slow cadence, fire-and-forget)
    # First run is delayed _WARM_STARTUP_DELAY seconds after boot
    # so the server isn't hitting external APIs the moment it starts.
    # ---------------------------------------------------------
    global _last_warm_at
    if time.monotonic() - _last_warm_at >= _WARM_INTERVAL_SECONDS:
        _last_warm_at = time.monotonic()
        asyncio.create_task(_warm_portfolio_universe())

    # ---------------------------------------------------------
    # Agent Executions  (with retry on transient DB errors)
    # ---------------------------------------------------------
    await _tick_agents(now)


async def _tick_agents(now: datetime.datetime, _attempt: int = 0):
    """Query due agents and trigger them.  Retries on transient DB connection errors."""
    try:
        async with async_session() as db:
            result = await db.execute(
                select(Agent).where(
                    Agent.status == "active",
                    Agent.next_run_at.isnot(None),
                    Agent.next_run_at <= now,
                )
            )
            due_agents = result.scalars().all()

            for agent in due_agents:
                logger.info(f"Scheduler: triggering agent {agent.id} ({agent.name})")
                try:
                    # Check for already-running
                    running_check = await db.execute(
                        select(AgentRun).where(
                            AgentRun.agent_id == agent.id,
                            AgentRun.status == "running",
                        )
                    )
                    if running_check.scalar_one_or_none():
                        logger.info(f"Agent {agent.id} is already running — skipping")
                        continue

                    # Create a run record
                    run = AgentRun(agent_id=agent.id, status="running", started_at=now)
                    db.add(run)
                    await db.commit()
                    await db.refresh(run)

                    # Get API keys
                    openai_key = await get_user_api_key(db, agent.user_id, "openai_api_key")
                    if not openai_key:
                        run.status = "failed"
                        run.error = "OpenAI API key not configured."
                        run.completed_at = datetime.datetime.now(datetime.timezone.utc)
                        await db.commit()
                        continue

                    search_key = await get_user_api_key(db, agent.user_id, "search_api_key")

                    # Execute (don't await — fire and forget so we can process other agents)
                    asyncio.create_task(
                        _execute_in_own_session(agent.id, agent.user_id, run.id)
                    )

                    # Update next_run_at
                    if agent.schedule_type == "recurring" and agent.schedule_cron:
                        try:
                            cron = croniter(agent.schedule_cron, now)
                            agent.next_run_at = cron.get_next(datetime.datetime)
                        except (ValueError, KeyError):
                            agent.next_run_at = None
                    elif agent.schedule_type == "one_time":
                        agent.next_run_at = None

                    await db.commit()

                except (DBAPIError, OperationalError):
                    raise  # bubble up to outer retry handler
                except Exception:
                    logger.exception(f"Scheduler error for agent {agent.id}")

    except (DBAPIError, OperationalError) as exc:
        if _attempt < _MAX_TICK_RETRIES:
            logger.warning(
                "Scheduler DB connection error (attempt %d/%d): %s — retrying in 2s",
                _attempt + 1, _MAX_TICK_RETRIES, exc,
            )
            await asyncio.sleep(2)
            await _tick_agents(now, _attempt + 1)
        else:
            logger.exception("Scheduler DB connection error — exhausted retries")


async def _execute_in_own_session(agent_id: int, user_id: int, run_id: int):
    """Execute an agent in its own DB session (for use in asyncio.create_task)."""
    try:
        async with async_session() as db:
            openai_key = await get_user_api_key(db, user_id, "openai_api_key")
            if not openai_key:
                result = await db.execute(select(AgentRun).where(AgentRun.id == run_id))
                run = result.scalar_one_or_none()
                if run:
                    run.status = "failed"
                    run.error = "OpenAI API key not configured."
                    run.completed_at = datetime.datetime.now(datetime.timezone.utc)
                    await db.commit()
                return

            model_pref = await get_user_api_key(db, user_id, "openai_model")
            model = model_pref or "gpt-4o-mini"

            search_key = await get_user_api_key(db, user_id, "search_api_key")
            await execute_agent(agent_id, db, openai_key, search_key, run_id=run_id, model=model)
    except Exception:
        logger.exception(f"Background agent execution failed: agent {agent_id}")


async def scheduler_loop():
    """Long-running loop that checks for due agents every CHECK_INTERVAL_SECONDS."""
    logger.info("Agent scheduler started")
    while True:
        try:
            await _tick()
        except Exception:
            logger.exception("Scheduler tick error")
        await asyncio.sleep(CHECK_INTERVAL_SECONDS)
