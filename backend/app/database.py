"""Async SQLAlchemy database setup.

Schema management
-----------------
Starting with the Alembic baseline migration, **Alembic is the source of
truth for schema changes**. See ``backend/MIGRATIONS.md`` for the workflow.

On startup this module does two things:

1. ``Base.metadata.create_all`` — a deliberate safety net for **fresh local
   developer databases** (e.g. empty SQLite file on a new clone). It is a
   no-op on any DB where the tables already exist, including production
   Cloud SQL Postgres. Do NOT rely on it for schema changes — add a proper
   Alembic migration instead.

2. ``_log_alembic_state`` — prints the current Alembic revision and
   whether it matches ``head``. This surfaces schema drift early without
   blocking boot.

Historical note: this file previously contained inline ``ALTER TABLE``
blocks (the "DIY migration" pattern). Those were removed when Alembic was
introduced — the columns they added already exist in production.
"""

import logging
from pathlib import Path

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase

from .config import settings

logger = logging.getLogger(__name__)

engine_kwargs = {"echo": False}
if "sqlite" not in settings.DATABASE_URL:
    engine_kwargs["pool_pre_ping"] = True
    engine_kwargs["pool_recycle"] = 300       # recycle connections every 5 min (avoid stale pg connections)
    engine_kwargs["pool_size"] = 5
    engine_kwargs["max_overflow"] = 10
    engine_kwargs["pool_timeout"] = 30

engine = create_async_engine(settings.DATABASE_URL, **engine_kwargs)
async_session = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)


class Base(DeclarativeBase):
    pass


async def _log_alembic_state() -> None:
    """Log current vs head Alembic revision. Non-blocking: any failure
    (including missing ``alembic_version`` table on fresh DBs) is logged
    at INFO and swallowed."""
    try:
        from alembic.config import Config                                # type: ignore
        from alembic.script import ScriptDirectory                       # type: ignore
        from sqlalchemy import text

        backend_dir = Path(__file__).resolve().parents[1]
        ini_path = backend_dir / "alembic.ini"
        if not ini_path.exists():
            return  # Alembic not installed in this checkout

        cfg = Config(str(ini_path))
        script = ScriptDirectory.from_config(cfg)
        head_rev = script.get_current_head()

        async with engine.connect() as conn:
            try:
                result = await conn.execute(text("SELECT version_num FROM alembic_version"))
                current_rev = result.scalar()
            except Exception:
                current_rev = None

        if current_rev is None:
            logger.info(
                "Alembic: no alembic_version row found. Run `alembic stamp head` "
                "if this is an existing DB, or `alembic upgrade head` for a fresh one. "
                "(head=%s)", head_rev,
            )
        elif current_rev != head_rev:
            logger.warning(
                "Alembic: DB is at revision %s but code expects %s. "
                "Run `alembic upgrade head` or downgrade the code.",
                current_rev, head_rev,
            )
        else:
            logger.info("Alembic: DB at head (%s).", current_rev)
    except Exception as exc:  # noqa: BLE001
        logger.info("Alembic state check skipped: %s", exc)


async def init_db() -> None:
    """Initialize the database on application startup.

    - Runs ``create_all`` (safety net for fresh local DBs only — no-op otherwise).
    - Logs Alembic revision state.

    **Schema changes must go through Alembic migrations**, not this function.
    """
    async with engine.begin() as conn:
        from . import models  # noqa: F401 — ensure models are registered
        await conn.run_sync(Base.metadata.create_all)

    await _log_alembic_state()


async def get_db() -> AsyncSession:  # type: ignore[misc]
    """FastAPI dependency that yields an async DB session."""
    async with async_session() as session:
        yield session
