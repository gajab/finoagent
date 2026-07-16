"""Quote provider registry.

Usage:
    provider = get_provider("yfinance")
    chain = await provider.get_option_chain("SPY", "2026-04-17")
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from .base import QuoteProvider, OptionQuote, UnderlyingQuote, OptionChain  # noqa: F401

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession
    from ...models import User

# Registered source names
AVAILABLE_SOURCES = ("yfinance", "ibkr")


def get_provider(
    source: str,
    *,
    user: "User | None" = None,
    db: "AsyncSession | None" = None,
) -> QuoteProvider:
    """Factory — return the right provider for *source*.

    IBKR requires authenticated user + db session so we can load encrypted
    credentials.
    """
    source = source.strip().lower()
    if source == "yfinance":
        from .yfinance_provider import YFinanceProvider
        return YFinanceProvider()
    if source == "ibkr":
        if user is None or db is None:
            raise ValueError("IBKR provider requires an authenticated user and db session.")
        from .ibkr_provider import IBKRProvider
        return IBKRProvider(user=user, db=db)
    raise ValueError(
        f"Unknown quote source '{source}'. Available: {', '.join(AVAILABLE_SOURCES)}"
    )
