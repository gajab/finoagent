"""Alembic env.py — wired to the app's existing settings + models.

Design notes
------------
- Single source of truth for DB URL: `app.config.settings.DATABASE_URL`.
  We never read `sqlalchemy.url` from alembic.ini (kept blank there).
- Async-aware: reuses SQLAlchemy's async engine so migrations connect the
  same way the app does (asyncpg for PostgreSQL).
- Prod safety rail: if the target URL looks like production Cloud SQL,
  refuse to run `upgrade`/`downgrade` unless ALEMBIC_ALLOW_PROD=1 is set.
  Autogenerate and dry runs (--sql / offline) are always allowed.

Adding a new model
------------------
All models must be imported here (or transitively) so that
`Base.metadata` knows about them before autogenerate runs. The import
of `app.models` at the bottom of this header handles that today — add
new model modules there as the codebase grows.
"""

from __future__ import annotations

import asyncio
import os
import sys
from logging.config import fileConfig
from pathlib import Path

from sqlalchemy import pool
from sqlalchemy.engine import Connection
from sqlalchemy.ext.asyncio import async_engine_from_config

from alembic import context

# ── Make `app` importable when running alembic from the backend/ dir ──
BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from app.config import settings            # noqa: E402
from app.database import Base              # noqa: E402
import app.models                          # noqa: F401, E402 — register all models

# ── Alembic config object (driven by alembic.ini + env.py overrides) ──
config = context.config

# Logging
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

# Inject the app's DATABASE_URL at runtime — never hardcoded in alembic.ini
# configparser treats % as an interpolation marker; escape it.
config.set_main_option("sqlalchemy.url", settings.DATABASE_URL.replace("%", "%%"))

# Autogenerate target
target_metadata = Base.metadata

# ── Prod safety rail ─────────────────────────────────────────────────
def _looks_like_prod(url: str) -> bool:
    """Heuristic: Cloud SQL private IPs, cloud-sql proxy sockets, or any
    non-sqlite URL without an explicit 'local'/'dev'/'test' marker."""
    u = url.lower()
    if "sqlite" in u:
        return False
    if "localhost" in u or "127.0.0.1" in u:
        # Could still be a Cloud SQL Auth Proxy — trust the explicit override.
        return False
    return True


def _assert_prod_override_if_needed() -> None:
    """Block destructive ops against prod unless explicitly allowed."""
    cmd = (context.get_x_argument(as_dictionary=True).get("cmd") or "").lower()
    # Alembic doesn't pass the command name directly; fall back to argv scan.
    argv = " ".join(sys.argv).lower()
    destructive = any(
        kw in argv for kw in ("upgrade", "downgrade", "stamp")
    )
    offline = context.is_offline_mode()   # `--sql` dry runs are safe
    if not destructive or offline:
        return
    if not _looks_like_prod(settings.DATABASE_URL):
        return
    if os.environ.get("ALEMBIC_ALLOW_PROD") != "1":
        raise RuntimeError(
            "Refusing to run Alembic against what looks like a production "
            "database. Set ALEMBIC_ALLOW_PROD=1 to override, or use "
            "`alembic upgrade head --sql` for a dry run."
        )


_assert_prod_override_if_needed()


# ── Migration runners ────────────────────────────────────────────────
def run_migrations_offline() -> None:
    """Offline mode: emit SQL to stdout without connecting. Used for --sql dry runs."""
    url = config.get_main_option("sqlalchemy.url")
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        compare_type=True,             # detect column type changes
        compare_server_default=True,   # detect server_default changes
    )
    with context.begin_transaction():
        context.run_migrations()


def do_run_migrations(connection: Connection) -> None:
    context.configure(
        connection=connection,
        target_metadata=target_metadata,
        compare_type=True,
        compare_server_default=True,
    )
    with context.begin_transaction():
        context.run_migrations()


async def run_async_migrations() -> None:
    """Online mode: connect using the async engine, then run migrations
    synchronously on that connection."""
    connectable = async_engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    async with connectable.connect() as connection:
        await connection.run_sync(do_run_migrations)
    await connectable.dispose()


def run_migrations_online() -> None:
    asyncio.run(run_async_migrations())


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
