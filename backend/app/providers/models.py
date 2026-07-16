from typing import List, Optional, Dict
from datetime import datetime
from pydantic import BaseModel


class StockQuote(BaseModel):
    """Real-time or delayed stock price quote."""
    ticker: str
    price: float
    change_dollar: float
    change_percent: float
    volume: Optional[int] = None
    timestamp: datetime


class CompanyInfo(BaseModel):
    """Basic company details and metrics."""
    ticker: str
    name: str
    sector: Optional[str] = None
    industry: Optional[str] = None
    market_cap: Optional[float] = None
    beta: Optional[float] = None
    pe_ratio: Optional[float] = None
    forward_pe: Optional[float] = None
    dividend_yield: Optional[float] = None
    shares_outstanding: Optional[int] = None
    description: Optional[str] = None


class HistoricalBar(BaseModel):
    """A single OHLCV bar for a given timeframe."""
    timestamp: datetime
    open: float
    high: float
    low: float
    close: float
    volume: int
    vwap: Optional[float] = None


class OptionStrike(BaseModel):
    """Data for a single option contract strike."""
    strike: float
    type: str  # 'call' or 'put'
    bid: float
    ask: float
    last_price: Optional[float] = None
    volume: int
    open_interest: int
    implied_volatility: Optional[float] = None
    delta: Optional[float] = None
    gamma: Optional[float] = None
    theta: Optional[float] = None
    vega: Optional[float] = None


class OptionsExpiration(BaseModel):
    """All available strikes for a given expiration date."""
    date: str  # YYYY-MM-DD
    days_to_expiration: int
    puts: List[OptionStrike]
    calls: List[OptionStrike]


class OptionsChain(BaseModel):
    """The full options chain for a given ticker."""
    ticker: str
    current_price: float
    expirations: List[OptionsExpiration]
