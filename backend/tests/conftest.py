"""Test-wide safety: the Manage Book engine's ETF-hedge step normally fetches ~40 tickers of history and live ETF
option chains from the network. Unit tests must never do that — stub the hook; the ETF module's own tests call
`etf_hedge_service` directly (not through this hook) with synthetic data."""
import pytest


@pytest.fixture(autouse=True)
def _no_network_etf_hedges(monkeypatch):
    try:
        import app.services.book_tail_risk as bt
    except Exception:  # pragma: no cover
        return

    async def _none(*a, **k):
        return None
    monkeypatch.setattr(bt, "_fetch_etf_static", _none, raising=False)
