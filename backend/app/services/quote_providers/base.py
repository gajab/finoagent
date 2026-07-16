"""Abstract base for option quote providers.

Every quote source (yfinance, IBKR, Schwab, Polygon, …) implements this
interface so the box-spread engine is data-source agnostic.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------

@dataclass
class OptionQuote:
    """Single option contract quote."""
    strike: float
    right: str              # "C" or "P"
    expiration: str         # YYYY-MM-DD
    bid: float
    ask: float
    last: float
    mid: float
    iv: float | None = None
    oi: int = 0
    volume: int = 0
    # Greeks — populated by providers that supply them (e.g. IBKR)
    delta: float | None = None
    gamma: float | None = None
    theta: float | None = None
    vega: float | None = None
    # Provider metadata
    conid: int | None = None  # IBKR contract id (used for order placement)


@dataclass
class UnderlyingQuote:
    """Spot / underlying price snapshot."""
    symbol: str
    price: float
    source: str             # "yfinance" | "ibkr" | …
    bid: float | None = None
    ask: float | None = None
    volume: int | None = None


@dataclass
class OptionChain:
    """Full option chain for one expiration."""
    symbol: str
    expiration: str
    underlying_price: float
    quotes: list[OptionQuote] = field(default_factory=list)
    source: str = ""


# ---------------------------------------------------------------------------
# Abstract provider
# ---------------------------------------------------------------------------

class QuoteProvider(ABC):
    """Interface every quote data source must implement."""

    source_name: str = "unknown"

    @abstractmethod
    async def get_underlying_price(self, symbol: str) -> UnderlyingQuote:
        """Return the current price of the underlying."""
        ...

    @abstractmethod
    async def get_option_expirations(self, symbol: str) -> list[str]:
        """Return available option expiration dates (YYYY-MM-DD strings)."""
        ...

    @abstractmethod
    async def get_option_chain(
        self, symbol: str, expiration: str
    ) -> OptionChain:
        """Return all option quotes for *symbol* at *expiration*."""
        ...
