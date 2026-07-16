import os
import httpx
from datetime import datetime
from typing import List, Optional, Dict
from dateutil import parser

from .base import MarketDataProvider
from .models import StockQuote, CompanyInfo, HistoricalBar, OptionsChain

# Alpaca API URLs
ALPACA_DATA_URL = "https://data.alpaca.markets/v2"
ALPACA_TRADING_URL = "https://paper-api.alpaca.markets/v2" # Using paper for general endpoints

class AlpacaProvider(MarketDataProvider):
    """
    Implementation of MarketDataProvider using Alpaca's raw REST API.
    Requires ALPACA_API_KEY and ALPACA_API_SECRET in the environment.
    """
    
    def __init__(self, api_key: str = None, api_secret: str = None):
        self.api_key = api_key or os.getenv("ALPACA_API_KEY")
        self.api_secret = api_secret or os.getenv("ALPACA_API_SECRET")
        
        if not self.api_key or not self.api_secret:
            raise ValueError("Alpaca API credentials missing. Set ALPACA_API_KEY and ALPACA_API_SECRET.")
            
        self.headers = {
            "APCA-API-KEY-ID": self.api_key,
            "APCA-API-SECRET-KEY": self.api_secret,
            "Accept": "application/json"
        }

    @property
    def provider_name(self) -> str:
        return "alpaca"
        
    async def _get(self, url: str, params: dict = None) -> dict:
        """Helper to make async GET requests to Alpaca."""
        async with httpx.AsyncClient() as client:
            response = await client.get(url, headers=self.headers, params=params, timeout=10.0)
            response.raise_for_status()
            return response.json()

    async def _get_previous_close(self, ticker: str) -> float:
        """Helper to get the previous day's close for change calculation."""
        url = f"{ALPACA_DATA_URL}/stocks/{ticker}/bars"
        # Request the last 2 days of daily bars
        params = {"timeframe": "1Day", "limit": 2}
        try:
            data = await self._get(url, params)
            bars = data.get("bars", [])
            if len(bars) >= 2:
                # previous day is the second to last bar
                return float(bars[-2]["c"])
            elif len(bars) == 1:
                # Only 1 bar returned, use its open as a fallback
                return float(bars[0]["o"])
        except Exception:
            pass
        return 0.0

    async def get_quote(self, ticker: str) -> StockQuote:
        # Get latest quote/trade (Alpaca provides latest trade which is best for "current price")
        url = f"{ALPACA_DATA_URL}/stocks/{ticker}/trades/latest"
        data = await self._get(url)
        trade = data.get("trade", {})
        price = float(trade.get("p", 0.0))
        
        # To get daily change, we need yesterday's close
        prev_close = await self._get_previous_close(ticker)
        
        change_dollar = price - prev_close if prev_close else 0.0
        change_percent = (change_dollar / prev_close * 100) if prev_close > 0 else 0.0
        
        return StockQuote(
            ticker=ticker,
            price=price,
            change_dollar=round(change_dollar, 2),
            change_percent=round(change_percent, 2),
            volume=trade.get("s", 0),  # size of the latest trade, not daily total.
            timestamp=parser.parse(trade.get("t", datetime.utcnow().isoformat()))
        )

    async def get_company_info(self, ticker: str) -> CompanyInfo:
        # Alpaca's basic Asset API provides limited fundamental info. 
        # Typically companies use specialized data providers for deep fundamentals.
        url = f"{ALPACA_TRADING_URL}/assets/{ticker}"
        try:
            data = await self._get(url)
            return CompanyInfo(
                ticker=data.get("symbol", ticker),
                name=data.get("name", ticker),
                sector=None, # Alpaca asset doesn't return sector/industry out of the box
                industry=None,
                market_cap=None,
                description=f"Alpaca Asset ID: {data.get('id')} - {data.get('class', 'us_equity')} / Exchange: {data.get('exchange')}"
            )
        except httpx.HTTPError:
            return CompanyInfo(ticker=ticker, name=ticker)

    async def get_historical_bars(self, ticker: str, timeframe: str, limit: int = 100) -> List[HistoricalBar]:
        """
        timeframe examples: '1Min', '15Min', '1Hour', '1Day'
        """
        url = f"{ALPACA_DATA_URL}/stocks/{ticker}/bars"
        params = {
            "timeframe": timeframe,
            "limit": limit
        }
        data = await self._get(url, params)
        bars = data.get("bars", [])
        
        result = []
        for b in bars:
            result.append(HistoricalBar(
                timestamp=parser.parse(b["t"]),
                open=float(b["o"]),
                high=float(b["h"]),
                low=float(b["l"]),
                close=float(b["c"]),
                volume=int(b["v"]),
                vwap=float(b.get("vw", 0.0))
            ))
        return result

    async def get_options_chain(self, ticker: str) -> Optional[OptionsChain]:
        """
        Note: Alpaca's options data API requires a separate data subscription.
        If the user does not have it, this will raise or return None.
        """
        # Placeholder for options chain logic when the user has Alpaca Options Data enabled.
        # Currently, Alpaca requires specific endpoints for options data under 'https://data.alpaca.markets/v1beta1/options/...'.
        return None
