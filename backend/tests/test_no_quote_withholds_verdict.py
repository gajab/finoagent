"""A leg with no live quote must not feed an exit / close verdict.

The DRAM covered call (100 sh @ $60 + short $85 call sold @ $0.65, exp 46d out). Its option quote never came back —
the yfinance combo wrapper dropped it — so the call was valued at $0: "100% of max profit captured — close to free
capital", badge STRONG CLOSE, a Trade Manager quant lens of 4/100, while the leg row said HOLD and the headline said
"Hold the structure". Five verdicts, none of them backed by a price. The real mark was ~$0.36 (≈45% captured).

`leg_mark_to_market` already makes the P&L *unknown* (None) in that case; these tests pin the other half — every
VERDICT resting on the option mark is withheld with it, in one voice — through the real `get_live_pnl` handler."""
import asyncio
import datetime as dt
import json
from types import SimpleNamespace

import pytest

import app.routers.saved_strategy_router as router
from app.services import quote_providers
from app.services.quote_providers.base import OptionQuote
from app.services.trade_math import withheld_verdict

DTE = 46
EXP = (dt.date.today() + dt.timedelta(days=DTE)).isoformat()
SPOT = 61.67
ENTRY_PREM, MARK = 0.65, 0.36

CALL = {"action": "sell", "type": "call", "strike": 85, "expiration": EXP, "qty": 1, "premium": ENTRY_PREM}


def _oq(strike, mid, right="C"):
    return OptionQuote(strike=float(strike), right=right, expiration=EXP, bid=round(mid - 0.02, 2), ask=round(mid + 0.02, 2),
                       last=mid, mid=mid, iv=0.62, oi=500, volume=50)


class _Provider:
    def __init__(self, quotes):
        self.quotes = quotes

    async def get_underlying_price(self, _t):
        return SimpleNamespace(price=SPOT)

    async def get_option_chain(self, _t, _exp):
        return SimpleNamespace(quotes=self.quotes, underlying_price=SPOT)


class _Res:
    def __init__(self, o):
        self._o = o

    def scalar_one_or_none(self):
        return self._o


class _DB:
    def __init__(self, s):
        self._s = s

    async def execute(self, *_a, **_k):
        return _Res(self._s)


def _covered():
    return SimpleNamespace(
        id=93, user_id=1, ticker="DRAM", name="DRAM Covered Call", strategy_type="covered_call", trade_status="active",
        legs_data=json.dumps([CALL]), entry_prices=json.dumps([{"ticker": "DRAM", "price": 60}, {"price": ENTRY_PREM}]),
        entry_net_debit=301.0, result_snapshot=None, entry_date=dt.datetime(2026, 8, 1, tzinfo=dt.timezone.utc),
        parameters=json.dumps({"purpose": "income", "contracts": 1, "shares": 100, "avg_cost": 60}))


def _lone():
    return SimpleNamespace(
        id=94, user_id=1, ticker="DRAM", name="DRAM short call", strategy_type="options", trade_status="active",
        legs_data=json.dumps([CALL]), entry_prices=json.dumps([{"price": ENTRY_PREM}]),
        entry_net_debit=65.0, result_snapshot=None, entry_date=dt.datetime(2026, 8, 1, tzinfo=dt.timezone.utc),
        parameters=json.dumps({"purpose": "income", "contracts": 1}))


def _live(monkeypatch, strategy, quotes):
    monkeypatch.setattr(quote_providers, "get_provider", lambda *a, **k: _Provider(quotes))
    return asyncio.run(router.get_live_pnl(strategy_id=strategy.id, quote_source="yfinance", margin_mode="reg_t",
                                           user=SimpleNamespace(id=1), db=_DB(strategy)))


NO_CALL_QUOTE = [_oq(60, 4.1), _oq(65, 2.2)]            # the chain is up, but the $85 call isn't in it
WITH_CALL_QUOTE = NO_CALL_QUOTE + [_oq(85, MARK)]


def _assert_every_verdict_withheld(r):
    a = r["analysis"]
    assert r["pricing_complete"] is False and r["unpriced_legs"] == [0]
    assert a["verdict_withheld"] is True
    assert a["exit_signal"] is None and a["quant_exit"] is None and a["captured_pct"] is None
    assert a["hold_vs_close"] == "NO_QUOTE"
    assert a["recommendation"]["action"] == "NO_QUOTE" and "No live quote" in a["recommendation"]["headline"]
    assert a["recommendation"]["leg_notes"] == []
    assert "No live quote" in a["exit_reasons"][0] and a["hold_vs_close_reasons"] == [r["pricing_warning"]] or "No live quote" in a["hold_vs_close_reasons"][0]
    blob = json.dumps(a, default=str)
    assert "close to free capital" not in blob and "Hold the structure" not in blob and "STRONG_CLOSE" not in blob
    assert r["leg_analysis"][0]["action"] == "NO_QUOTE" and r["leg_analysis"][0]["captured_pct"] is None   # not "HOLD", not a close call
    json.dumps(r, default=str)                                                                              # still serialisable


def test_covered_call_with_no_call_quote_withholds_every_verdict(monkeypatch):
    r = _live(monkeypatch, _covered(), NO_CALL_QUOTE)
    _assert_every_verdict_withheld(r)
    # the option P&L is UNKNOWN (not +$65 = "all of it captured"); the shares' P&L is real and unaffected
    assert r["options_pnl"] is None and r["unrealized_pnl"] is None
    assert r["stock_pnl"] == pytest.approx(100 * (SPOT - 60), abs=0.01)
    assert r["options_breakdown"]["current_value"] is None


def test_lone_short_call_with_no_quote_withholds_every_verdict(monkeypatch):
    r = _live(monkeypatch, _lone(), NO_CALL_QUOTE)
    _assert_every_verdict_withheld(r)
    assert r["unrealized_pnl"] is None and r["current_value"] is None


def test_quoted_control_still_gets_a_real_verdict(monkeypatch):
    """The same trade once the call prices: ≈45% captured, a signal, a quant read — nothing withheld."""
    r = _live(monkeypatch, _covered(), WITH_CALL_QUOTE)
    a = r["analysis"]
    assert r["pricing_complete"] is True and r["unpriced_legs"] == [] and not a.get("verdict_withheld")
    assert r["options_pnl"] == pytest.approx((ENTRY_PREM - MARK) * 100, abs=0.01)             # +$29 — not +$65
    assert a["captured_pct"] == pytest.approx((ENTRY_PREM - MARK) / ENTRY_PREM * 100, abs=0.5)  # ≈44.6%, not 100%
    assert a["exit_signal"] in ("STRONG_HOLD", "HOLD", "CLOSE", "STRONG_CLOSE")
    assert a["exit_signal"] != "STRONG_CLOSE"
    assert a["hold_vs_close"] != "NO_QUOTE" and a["recommendation"]["action"] != "NO_QUOTE"
    assert r["leg_analysis"][0]["action"] != "NO_QUOTE"


def test_a_zero_priced_call_is_not_mistaken_for_a_gap(monkeypatch):
    """A leg that genuinely quotes ~nothing is a PRICE (the whole credit really is captured), unlike a missing row."""
    r = _live(monkeypatch, _covered(), NO_CALL_QUOTE + [_oq(85, 0.01)])
    assert r["pricing_complete"] is True and not r["analysis"].get("verdict_withheld")
    assert r["analysis"]["captured_pct"] == pytest.approx((ENTRY_PREM - 0.01) / ENTRY_PREM * 100, abs=0.5)


def test_withheld_verdict_payload_is_self_contained():
    w = withheld_verdict("No live quote for 1 of 1 option leg(s)")
    assert w["exit_signal"] is None and w["quant_exit"] is None and w["captured_pct"] is None
    assert w["recommendation"]["headline"] == w["hold_vs_close_reasons"][0] == w["exit_reasons"][0]
    assert w["verdict_withheld"] is True
