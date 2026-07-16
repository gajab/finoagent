from abc import ABC, abstractmethod
from typing import List, Optional
from .models import StockQuote, CompanyInfo, HistoricalBar, OptionsChain


class MarketDataProvider(ABC):
    """
    Abstract interface for all financial market data providers.
    Implementations should wrap robust external APIs (REST endpoints)
    and strictly return the standardized Pydantic models defined above.
    """

    @property
    @abstractmethod
    def provider_name(self) -> str:
        """Name of the data provider (e.g. 'alpaca', 'fmp', 'polygon')"""
        pass

    @abstractmethod
    async def get_quote(self, ticker: str) -> StockQuote:
        """Fetch the latest price quote for a ticker."""
        pass

    @abstractmethod
    async def get_company_info(self, ticker: str) -> CompanyInfo:
        """Fetch basic company information and metrics."""
        pass

    @abstractmethod
    async def get_historical_bars(self, ticker: str, timeframe: str, limit: int = 100) -> List[HistoricalBar]:
        """
        Fetch historical OHLCV. 
        timeframe should be standardized, e.g. '1D', '1Min', '15Min'
        """
        pass

    @abstractmethod
    async def get_options_chain(self, ticker: str) -> Optional[OptionsChain]:
        """
        Fetch the options chain for a ticker. 
        Returns None if options data is unsupported by the provider or unavailable for the ticker.
        """
        pass
