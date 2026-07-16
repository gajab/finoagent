"""YFinance-backed quote provider.

Free delayed data — no authentication required.
"""

from __future__ import annotations

import asyncio
import math
import logging
from datetime import datetime

import yfinance as yf

from .base import QuoteProvider, OptionQuote, UnderlyingQuote, OptionChain

logger = logging.getLogger(__name__)

# Index tickers that need "^" prefix for yfinance
_INDEX_TICKERS = {"SPX", "XSP", "NDX", "RUT", "VIX", "DJX", "OEX", "GSPC"}


def _safe_float(v, default: float = 0.0) -> float:
    try:
        return float(v) if v is not None and not (isinstance(v, float) and math.isnan(v)) else default
    except (TypeError, ValueError):
        return default


def _normalize_ticker(ticker: str) -> str:
    """Normalize ticker for yfinance: .SPX → ^SPX, /ES → ES=F, etc."""
    t = ticker.strip().upper()
    if t.startswith("/"):
        t = t[1:]
        if not ("=" in t or "." in t):
            t = t + "=F"
    if t.startswith("."):
        t = "^" + t[1:]
    if not t.startswith("^") and t in _INDEX_TICKERS:
        t = "^" + t
    return t


class YFinanceProvider(QuoteProvider):
    """Fetches option quotes via the free yfinance library."""

    source_name = "yfinance"

    async def _run_fetch_with_metrics(self, endpoint: str, symbol: str, fetch_func):
        import time
        from ...database import async_session
        from ...models import ApiMetric
        start = time.monotonic()
        status_code = 200
        try:
            result = await asyncio.to_thread(fetch_func)
            return result
        except Exception as e:
            status_code = getattr(e, "status_code", 500)
            raise e
        finally:
            elapsed_ms = (time.monotonic() - start) * 1000
            
            async def _log_metric():
                try:
                    async with async_session() as session:
                        metric = ApiMetric(
                            provider="yfinance",
                            endpoint=endpoint,
                            method="API",
                            status_code=status_code,
                            latency_ms=elapsed_ms
                        )
                        session.add(metric)
                        await session.commit()
                except Exception as e:
                    logger.error(f"Failed to save yfinance metric: {e}")
                    
            asyncio.create_task(_log_metric())

    def __init__(self) -> None:
        self._ticker_cache: dict[str, yf.Ticker] = {}

    def _get_ticker(self, symbol: str) -> yf.Ticker:
        norm = _normalize_ticker(symbol)
        if norm not in self._ticker_cache:
            self._ticker_cache[norm] = yf.Ticker(norm)
        return self._ticker_cache[norm]

    # ------------------------------------------------------------------
    # QuoteProvider interface
    # ------------------------------------------------------------------

    async def get_underlying_price(self, symbol: str) -> UnderlyingQuote:
        def _fetch():
            stock = self._get_ticker(symbol)
            info = stock.info or {}
            price = info.get("currentPrice") or info.get("regularMarketPrice")
            bid = info.get("bid")
            ask = info.get("ask")
            vol = info.get("volume")
            return price, bid, ask, vol

        price, bid, ask, vol = await self._run_fetch_with_metrics("info", symbol, _fetch)
        if not price:
            raise ValueError(f"Cannot fetch current price for {symbol}")
        return UnderlyingQuote(
            symbol=_normalize_ticker(symbol),
            price=float(price),
            source="yfinance",
            bid=float(bid) if bid else None,
            ask=float(ask) if ask else None,
            volume=int(vol) if vol else None,
        )

    async def get_option_expirations(self, symbol: str) -> list[str]:
        def _fetch():
            stock = self._get_ticker(symbol)
            return list(stock.options)

        return await self._run_fetch_with_metrics("options", symbol, _fetch)

    async def get_option_chain(
        self, symbol: str, expiration: str
    ) -> OptionChain:
        underlying = await self.get_underlying_price(symbol)

        def _fetch():
            stock = self._get_ticker(symbol)
            return stock.option_chain(expiration)

        chain = await self._run_fetch_with_metrics("option_chain", symbol, _fetch)
        is_index = _normalize_ticker(symbol).startswith("^")

        quotes: list[OptionQuote] = []
        for right_label, df in [("C", chain.calls), ("P", chain.puts)]:
            for _, row in df.iterrows():
                strike = float(row["strike"])
                bid = _safe_float(row.get("bid"))
                ask = _safe_float(row.get("ask"))
                last = _safe_float(row.get("lastPrice"))
                mid = round((bid + ask) / 2, 4) if bid > 0 and ask > 0 else last

                # For index options with stale bid/ask, synthesize from lastPrice
                if is_index and bid == 0 and ask == 0 and last > 0:
                    bid = round(last * 0.97, 4)
                    ask = round(last * 1.03, 4)
                    mid = last

                iv = _safe_float(row.get("impliedVolatility"))
                oi = int(_safe_float(row.get("openInterest")))
                vol = int(_safe_float(row.get("volume")))

                quotes.append(OptionQuote(
                    strike=strike,
                    right=right_label,
                    expiration=expiration,
                    bid=bid,
                    ask=ask,
                    last=last,
                    mid=mid,
                    iv=iv,
                    oi=oi,
                    volume=vol,
                ))

        return OptionChain(
            symbol=underlying.symbol,
            expiration=expiration,
            underlying_price=underlying.price,
            quotes=quotes,
            source="yfinance",
        )
