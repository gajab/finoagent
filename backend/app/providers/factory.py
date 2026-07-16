import os
from typing import Optional
from .base import MarketDataProvider
from .alpaca_provider import AlpacaProvider
from .fmp_provider import FMPProvider

# We can import YFinanceProvider here later when we build it.

class ProviderRegistry:
    """
    Factory class to instantiate and cache Market Data Providers.
    """
    def __init__(self):
        self._providers: dict[str, MarketDataProvider] = {}

    def get_provider(self, name: str) -> MarketDataProvider:
        """
        Get or initialize a provider by name ('alpaca', 'fmp', 'yfinance', etc.)
        """
        name = name.lower()
        if name in self._providers:
            return self._providers[name]

        if name == "alpaca":
            provider = AlpacaProvider()
            self._providers[name] = provider
            return provider
            
        if name == "fmp":
            provider = FMPProvider()
            self._providers[name] = provider
            return provider
            
        # if name == "yfinance":
        #    provider = YFinanceProvider()
        #    return provider

        raise ValueError(f"Unknown MarketDataProvider: {name}")

# Global registry instance
registry = ProviderRegistry()

def get_market_data_provider(name: Optional[str] = None) -> MarketDataProvider:
    """
    Returns the configured market data provider.
    Defaults to Alpaca if nothing is specified (for now, based on env).
    """
    provider_name = name or os.getenv("DATA_PROVIDER", "alpaca")
    return registry.get_provider(provider_name)
