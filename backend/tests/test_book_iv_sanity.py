"""Manage Book: an option quote's IV must reprice to the quote's own price — after-hours yfinance IV (~1e-5) used to
collapse θ / vega / the stress P&Ls to ~0 while the book still looked fine."""
import asyncio
import json
from datetime import date, timedelta
from types import SimpleNamespace

import pytest

import app.services.book_tail_risk as bt
from app.services.book_tail_risk import _solve_iv, _sane_iv
from app.services.stock_service import bs_price

R, S = 0.045, 100.0


def _px(iv, K=105.0, T=30 / 365.0, right="C"):
    return bs_price(S, K, T, R, iv, "call" if right == "C" else "put")


class TestSolveIv:
    @pytest.mark.parametrize("iv", [0.08, 0.25, 0.40, 0.85, 1.6])
    def test_it_recovers_the_vol_that_made_the_price(self, iv):
        for K, right in ((90, "P"), (105, "C"), (120, "C")):
            if iv < 0.2 and K == 120:
                continue                       # a far-OTM call at 8% vol is worth ~0: the price carries no vol information
            T = 45 / 365.0
            px = bs_price(S, K, T, R, iv, "call" if right == "C" else "put")
            got = _solve_iv(px, S, K, T, R, "call" if right == "C" else "put")
            assert got is not None and got == pytest.approx(iv, abs=2e-3), (K, right, iv, got)

    def test_a_price_at_or_below_the_no_vol_floor_carries_no_information(self):
        assert _solve_iv(0.0, S, 105, 30 / 365.0, R, "call") is None
        assert _solve_iv(5.0, S, 80, 30 / 365.0, R, "call") is None             # deep ITM quoted at intrinsic-ish
        assert _solve_iv(-1.0, S, 105, 30 / 365.0, R, "call") is None


class TestSaneIv:
    T = 30 / 365.0

    def _q(self, iv_quote, iv_true=0.35, last_only=False):
        px = _px(iv_true)
        return {"bid": 0.0 if last_only else px * 0.97, "ask": 0.0 if last_only else px * 1.03,
                "mid": px, "last": px, "iv": iv_quote}

    def test_a_healthy_chain_iv_is_trusted(self):
        iv, src = _sane_iv(self._q(0.35), S, 105, self.T, R, "C", 0.30)
        assert src == "chain" and iv == pytest.approx(0.35)

    @pytest.mark.parametrize("junk", [1e-5, 0.0, None, 0.0004, 9.0])
    def test_a_junk_iv_is_replaced_by_the_vol_the_price_implies(self, junk):
        iv, src = _sane_iv(self._q(junk), S, 105, self.T, R, "C", 0.30)
        assert src == "solved" and iv == pytest.approx(0.35, abs=3e-3)

    def test_a_plausible_iv_that_does_not_reprice_to_the_quote_is_not_trusted(self):
        iv, src = _sane_iv(self._q(0.80, iv_true=0.30), S, 105, self.T, R, "C", 0.30)     # chain says 80%, the price says 30%
        assert src == "solved" and iv == pytest.approx(0.30, abs=3e-3)

    def test_with_no_price_at_all_a_plausible_iv_is_kept_and_a_junk_one_falls_back(self):
        assert _sane_iv({"bid": 0, "ask": 0, "mid": 0, "last": 0, "iv": 0.42}, S, 105, self.T, R, "C", 0.30) == (0.42, "chain")
        assert _sane_iv({"bid": 0, "ask": 0, "mid": 0, "last": 0, "iv": 1e-5}, S, 105, self.T, R, "C", 0.30) == (0.30, "fallback")
        assert _sane_iv(None, S, 105, self.T, R, "C", 0.55) == (0.55, "fallback")

    def test_puts_work_the_same_way(self):
        px = _px(0.28, K=92.0, right="P")
        iv, src = _sane_iv({"bid": 0, "ask": 0, "mid": px, "last": px, "iv": 1e-5}, S, 92, self.T, R, "P", 0.30)
        assert src == "solved" and iv == pytest.approx(0.28, abs=3e-3)


# ── the whole position: junk chain IV must not zero the book ─────────────────────
class _Chain:
    def __init__(self, quotes, source):
        self.quotes, self.source = quotes, source


def _chain(strike, right, iv_true, iv_quote, T, source="yfinance (indicative — market closed)"):
    px = bs_price(S, strike, T, R, iv_true, "call" if right == "C" else "put")
    return _Chain([SimpleNamespace(strike=strike, right=right, expiration="x", bid=0.0, ask=0.0, last=px, mid=px, iv=iv_quote, oi=100, volume=10)], source)


def _strategy(legs, tid=1):
    return SimpleNamespace(id=tid, ticker="XYZ", name="x", strategy_type="options", legs_data=json.dumps(legs), parameters="{}")


def _position(iv_quote, qty=3):
    exp = (date.today() + timedelta(days=30)).isoformat()
    T = 30 / 365.0
    chains = {("XYZ", exp): _chain(105.0, "C", 0.35, iv_quote, T)}

    class Prov:
        async def get_underlying_price(self, t): return SimpleNamespace(price=S)
        async def get_option_chain(self, t, e): return chains[(t, e)]
    legs = [{"action": "SELL", "type": "call", "strike": 105, "qty": qty, "expiration": exp, "iv": 30}]
    return asyncio.run(bt._position_greeks(_strategy(legs), Prov(), R, date.today(), {}, {}))


def test_a_junk_chain_iv_no_longer_zeroes_theta_and_vega():
    junk, healthy = _position(1e-5), _position(0.35)
    assert healthy["net_theta"] > 5 and healthy["net_vega"] < -20                    # a real short call earns θ and is short vega
    assert junk["net_theta"] == pytest.approx(healthy["net_theta"], rel=0.05)
    assert junk["net_vega"] == pytest.approx(healthy["net_vega"], rel=0.05)
    assert junk["net_gamma"] == pytest.approx(healthy["net_gamma"], rel=0.05)


def test_the_position_says_where_each_iv_came_from_and_that_the_chain_was_indicative():
    junk, healthy = _position(1e-5), _position(0.35)
    assert junk["iv_audit"] == {"chain": 0, "solved": 1, "fallback": 0} and junk["indicative"] is True
    assert healthy["iv_audit"] == {"chain": 1, "solved": 0, "fallback": 0}
    assert junk["legs"][0]["iv_src"] == "solved"


# ── coverage: say what was left out ─────────────────────────────────────────────
def test_the_book_reports_how_many_trades_it_actually_analysed_and_why_others_were_skipped(monkeypatch):
    import app.services.derivative_income_service as dis
    import app.services.quote_providers as qp
    pos = SimpleNamespace  # noqa
    from test_book_hedge_plan import _call_book, _Provider  # reuse the fixtures (same directory)
    book = _call_book(2)
    for i, p in enumerate(book):
        p["trade_id"] = i + 1; p["iv_audit"] = {"chain": 1, "solved": 0, "fallback": 0}; p["indicative"] = False
    by_id = {p["trade_id"]: p for p in book}

    async def fake_pos(strategy, *a, **k): return dict(by_id[strategy.id]) if strategy.id in by_id else None
    async def fake_none(*a, **k): return [], None
    async def fake_factor(*a, **k): return None
    async def fake_sofr(): return (R, None)
    monkeypatch.setattr(bt, "_position_greeks", fake_pos)
    monkeypatch.setattr(bt, "_compute_betas", lambda tk: {t: 1.4 for t in tk})
    monkeypatch.setattr(bt, "_index_puts", fake_none); monkeypatch.setattr(bt, "_vix_calls", fake_none)
    monkeypatch.setattr(bt, "_vix_levels", lambda: (18.0, 19.0)); monkeypatch.setattr(bt, "_book_factor_exposure", fake_factor)
    monkeypatch.setattr(dis, "_get_sofr", fake_sofr); monkeypatch.setattr(qp, "get_provider", lambda *a, **k: _Provider())
    stock_only = SimpleNamespace(id=99, ticker="TOST", legs_data="[]")
    out = asyncio.run(bt.compute_book_tail_risk([SimpleNamespace(id=1), SimpleNamespace(id=2), stock_only], "yfinance", SimpleNamespace(id=1), None))
    assert out["coverage"]["requested"] == 3 and out["coverage"]["analysed"] == 2
    assert out["coverage"]["skipped"] == [{"trade_id": 99, "ticker": "TOST", "reason": "no open option legs (stock / futures only, or the options are closed)"}]
    assert out["data_quality"] == {"legs": 2, "chain": 2, "solved": 0, "fallback": 0, "indicative": False}
