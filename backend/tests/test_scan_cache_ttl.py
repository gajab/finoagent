"""An EMPTY scan must not be cached for the full 15 minutes.

Seen live (CB, 2026-10-20): the auto-expiry scan got one bad yfinance response at 10:17:37, found 0 trades, and
that empty result was cached for 15 min under the auto key — every retry showed "No structure cleared 90%…"
while the explicit-date key (computed fresh) found 3 trades 82 s later.
"""

import asyncio
from datetime import date, timedelta
from types import SimpleNamespace

import pytest

import app.services.derivative_income_service as dis
from app.services.derivative_income_service import TTL_ANALYSIS, TTL_EMPTY, scan_cache_ttl
from app.services.quote_providers import OptionChain, OptionQuote


def test_ttl_rule():
    assert scan_cache_ttl(True) == TTL_ANALYSIS
    assert scan_cache_ttl(False) == TTL_EMPTY < TTL_ANALYSIS
    assert scan_cache_ttl(False, 30) == 30          # never LONGER than the caller's own TTL
    assert scan_cache_ttl(True, 300) == 300


def _third_friday_ahead(days: int) -> date:
    d = date.today() + timedelta(days=days)
    while not dis._is_monthly_expiry(d):
        d += timedelta(days=1)
    return d


@pytest.mark.parametrize("with_quotes,expect_ttl", [(False, TTL_EMPTY), (True, TTL_ANALYSIS)])
def test_scan_caches_empty_results_briefly(monkeypatch, with_quotes, expect_ttl):
    exp = _third_friday_ahead(20).isoformat()
    spot = 100.0

    def q(k, right, bid, ask, iv):
        return OptionQuote(strike=k, right=right, expiration=exp, bid=bid, ask=ask, last=(bid + ask) / 2,
                           mid=(bid + ask) / 2, iv=iv, oi=900, volume=300)

    quotes = []
    if with_quotes:                                   # a liquid put wing → at least one cash-secured put clears
        from app.services.stock_service import bs_price
        T = (date.fromisoformat(exp) - date.today()).days / 365
        for k in range(70, 131, 2):
            for right in ("P", "C"):
                px = bs_price(spot, k, T, 0.045, 0.40, "put" if right == "P" else "call")
                if px > 0.05:
                    quotes.append(q(float(k), right, round(px * 0.98, 2), round(px * 1.02, 2), 0.40))

    class FakeProvider:
        async def get_underlying_price(self, t):
            return SimpleNamespace(price=spot)

        async def get_option_expirations(self, t):
            return [exp]

        async def get_option_chain(self, t, e):
            return OptionChain(symbol=t, expiration=e, underlying_price=spot, quotes=list(quotes))

    writes = []

    async def fake_set(db, key, payload, ttl_seconds):
        writes.append((key, ttl_seconds, payload.get("n_opportunities")))

    async def fake_get(db, key):
        return None

    async def fake_sofr():
        return 0.043, "test"

    monkeypatch.setattr(dis, "get_provider", lambda *a, **k: FakeProvider())
    monkeypatch.setattr(dis, "set_cached", fake_set)
    monkeypatch.setattr(dis, "get_cached", fake_get)
    monkeypatch.setattr(dis, "_get_sofr", fake_sofr)
    monkeypatch.setattr(dis, "_context_sync", lambda t: {"hv30": 0.35, "hv20": 0.35})

    r = asyncio.run(dis.run_derivative_income("TEST", min_prob=0.85, min_income=5.0,
                                              structures=["cash_secured_put"], db=object()))
    assert ("note" in r) is (not with_quotes)
    assert len(writes) == 1
    key, ttl, n = writes[0]
    assert ttl == expect_ttl, (n, ttl)
    assert (n > 0) is with_quotes
