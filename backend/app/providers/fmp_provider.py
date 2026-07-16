import os
import httpx
from datetime import datetime
from typing import List, Optional
from dateutil import parser
import math

from .base import MarketDataProvider
from .models import StockQuote, CompanyInfo, HistoricalBar, OptionsChain

FMP_BASE_URL = "https://financialmodelingprep.com/api/v3"

class FMPProvider(MarketDataProvider):
    """
    Implementation of MarketDataProvider using Financial Modeling Prep (FMP) REST API.
    Requires FMP_API_KEY in the environment.
    """
    
    def __init__(self, api_key: str = None):
        self.api_key = api_key or os.getenv("FMP_API_KEY")
        if not self.api_key:
            raise ValueError("FMP API credentials missing. Set FMP_API_KEY.")
            
    @property
    def provider_name(self) -> str:
        return "fmp"
        
    async def _get(self, endpoint: str, params: dict = None) -> list | dict:
        """Helper to make async GET requests to FMP."""
        url = f"{FMP_BASE_URL}{endpoint}"
        
        req_params = {"apikey": self.api_key}
        if params:
            req_params.update(params)
            
        async with httpx.AsyncClient() as client:
            response = await client.get(url, params=req_params, timeout=10.0)
            response.raise_for_status()
            return response.json()

    async def get_quote(self, ticker: str) -> StockQuote:
        data = await self._get(f"/quote/{ticker}")
        if not data:
            raise ValueError(f"No quote found for ticker {ticker}")
            
        item = data[0]
        timestamp = item.get("timestamp")
        dt = datetime.fromtimestamp(timestamp) if timestamp else datetime.utcnow()
        
        return StockQuote(
            ticker=ticker,
            price=float(item.get("price", 0.0)),
            change_dollar=float(item.get("change", 0.0)),
            change_percent=float(item.get("changesPercentage", 0.0)),
            volume=int(item.get("volume", 0)),
            timestamp=dt
        )

    async def get_company_info(self, ticker: str) -> CompanyInfo:
        # FMP provides rich fundamental profiles
        profiles = await self._get(f"/profile/{ticker}")
        if not profiles:
            return CompanyInfo(ticker=ticker, name=ticker)
            
        profile = profiles[0]
        
        return CompanyInfo(
            ticker=ticker,
            name=profile.get("companyName", ticker),
            sector=profile.get("sector"),
            industry=profile.get("industry"),
            market_cap=float(profile.get("mktCap", 0.0)) if profile.get("mktCap") else None,
            beta=float(profile.get("beta", 0.0)) if profile.get("beta") else None,
            pe_ratio=None, # Usually requires another endpoint or calculation for trailing/forward PE
            dividend_yield=None, 
            description=profile.get("description")
        )

    async def get_historical_bars(self, ticker: str, timeframe: str, limit: int = 100) -> List[HistoricalBar]:
        """
        timeframe examples: '1Min', '15Min', '1Hour', '1Day'
        """
        # Map our timeframe input to FMP's endpoints
        tf_lower = timeframe.lower()
        
        if tf_lower in ["1day", "1d", "daily"]:
            # Daily endpoint is different from intraday
            data = await self._get(f"/historical-price-full/{ticker}")
            historical = data.get("historical", [])[:limit]
            
            result = []
            for b in historical:
                # FMP historical daily sends date like '2023-10-09'
                dt = parser.parse(b["date"])
                result.append(HistoricalBar(
                    timestamp=dt,
                    open=float(b["open"]),
                    high=float(b["high"]),
                    low=float(b["low"]),
                    close=float(b["close"]),
                    volume=int(b["volume"]),
                    vwap=float(b.get("vwap", 0.0)) if b.get("vwap") else None
                ))
            # FMP returns newest first in historical-price-full, so maybe reverse depending on needs.
            # Assuming we return newest first to match typical limits.
            return result
        else:
            # Intraday (1min, 5min, 15min, 30min, 1hour, 4hour)
            # Map standard inputs to FMP specific
            fmp_tf = "1min"
            if "min" in tf_lower:
                mins = "".join(filter(str.isdigit, tf_lower))
                if mins in ["1", "5", "15", "30"]:
                    fmp_tf = f"{mins}min"
            elif "hour" in tf_lower or "h" in tf_lower:
                hours = "".join(filter(str.isdigit, tf_lower))
                if hours in ["1", "4"]:
                    fmp_tf = f"{hours}hour"
                    
            data = await self._get(f"/historical-chart/{fmp_tf}/{ticker}")
            # data is already list
            data = data[:limit]
            
            result = []
            for b in data:
                result.append(HistoricalBar(
                    timestamp=parser.parse(b["date"]),
                    open=float(b["open"]),
                    high=float(b["high"]),
                    low=float(b["low"]),
                    close=float(b["close"]),
                    volume=int(b["volume"])
                ))
            return result

    async def get_options_chain(self, ticker: str) -> Optional[OptionsChain]:
        """
        FMP requires a different endpoint for options chains or a premium subscription.
        Currently keeping it unsupported to mirror the generic base.
        """
        return None
