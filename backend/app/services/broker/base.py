"""Abstract broker interface — extend for each broker (IB, Schwab, Fidelity, etc.)."""

from abc import ABC, abstractmethod
from dataclasses import dataclass, field


@dataclass
class BrokerAuthStatus:
    connected: bool
    authenticated: bool
    account_id: str | None = None
    server_name: str | None = None
    error: str | None = None


@dataclass
class ContractInfo:
    conid: int
    symbol: str
    sec_type: str
    exchange: str
    expiry: str | None = None
    strike: float | None = None
    right: str | None = None  # "C" or "P"
    description: str | None = None


@dataclass
class OrderRequest:
    conid: int
    side: str         # "BUY" or "SELL"
    quantity: int
    order_type: str   # "LMT" or "MKT"
    price: float | None = None
    tif: str = "DAY"


@dataclass
class OrderResult:
    success: bool
    order_id: str | None = None
    message: str | None = None
    error: str | None = None


@dataclass
class StrategyOrderResult:
    overall_success: bool
    legs: list[OrderResult] = field(default_factory=list)
    order_record_id: int | None = None


class BrokerService(ABC):
    """Abstract broker interface. Each broker implements this."""

    @abstractmethod
    async def check_connection(self) -> BrokerAuthStatus:
        """Check if the broker connection is alive and authenticated."""
        ...

    @abstractmethod
    async def get_accounts(self) -> list[dict]:
        """Get list of brokerage accounts."""
        ...

    @abstractmethod
    async def search_contract(
        self,
        symbol: str,
        sec_type: str = "OPT",
        expiry: str | None = None,
        strike: float | None = None,
        right: str | None = None,
    ) -> list[ContractInfo]:
        """Search for a contract and return matching results."""
        ...

    @abstractmethod
    async def place_order(
        self,
        account_id: str,
        order: OrderRequest,
    ) -> OrderResult:
        """Place a single order. Returns result with order ID or error."""
        ...

    @abstractmethod
    async def get_order_status(self, order_id: str) -> dict:
        """Get the current status of an order."""
        ...
