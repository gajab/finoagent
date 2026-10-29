"""Wide markets are offered as LIMIT-ORDER ONLY, and an empty scan says EVERY reason it found nothing.

Seen live (MCO 2026-10-20, earnings the next day): every put failed the IV-scaled bid-ask gate (410P 1.00/2.90,
97% of mid) and the scan showed "No structure cleared 90%… Try a lower probability…" — blaming the filters when
the real blocker was liquidity.
"""

import asyncio
from datetime import date, datetime, timedelta
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import pytest

import app.services.derivative_income_service as dis
from app.services.derivative_income_service import (
    LIMIT_ONLY_MAX_SPREAD,
    _exec_status,
    _limit_only_ok,
    _market_open_now,
    _no_trade_reasons,
    _single_leg_income,
)
from app.services.quote_providers import OptionChain, OptionQuote
from app.services.stock_service import bs_price

S, DTE, IV = 100.0, 31, 0.35
EXP = "2026-11-20"


def _q(k, bid, ask, right="P", oi=150, vol=20, iv=IV):
    return OptionQuote(strike=k, right=right, expiration=EXP, bid=bid, ask=ask, last=(bid + ask) / 2,
                       mid=(bid + ask) / 2, iv=iv, oi=oi, volume=vol)


def _build(q, min_prob=0.85, min_income=20.0, diag=None):
    return _single_leg_income("cash_secured_put", "Cash-Secured Put", q, spot=S, dte=DTE, exp=EXP, rnd=None,
                              r=0.045, sofr_pct=4.3, atm_iv=IV, iv_hv_ratio=1.2, richness="rich",
                              european=False, earnings_before=None, macro=[], min_prob=min_prob,
                              min_income=min_income, ticker="TEST", diag=diag)


class TestGates:
    def test_status_codes(self):
        assert _exec_status(_q(85, 1.00, 1.05))[0] == "ok"
        assert _exec_status(_q(85, 1.00, 2.90))[0] == "wide"
        assert _exec_status(_q(85, 0.00, 1.00))[0] == "no_market"
        assert _exec_status(_q(85, 1.00, 1.05, oi=3, vol=2))[0] == "thin"

    def test_limit_only_needs_a_real_market_that_clears_the_floor_at_the_bid(self):
        assert _limit_only_ok(_q(85, 1.00, 2.90), 20.0)                       # 97% wide, bid pays $100
        assert not _limit_only_ok(_q(85, 0.05, 3.80), 20.0)                   # 195% wide — a mirage mid
        assert not _limit_only_ok(_q(85, 0.15, 0.30), 20.0)                   # bid alone pays only $15
        assert not _limit_only_ok(_q(85, 1.00, 2.90, oi=3, vol=2), 20.0)      # dead book
        assert LIMIT_ONLY_MAX_SPREAD == 1.50                                  # bid 1.00 / ask 7.00 = 150% of mid
        assert _limit_only_ok(_q(85, 1.00, 7.00), 20.0)
        assert not _limit_only_ok(_q(85, 1.00, 7.10), 20.0)


class TestSingleLeg:
    def test_wide_but_real_market_is_kept_as_limit_only(self):
        o = _build(_q(85.0, 1.00, 2.90))
        assert o is not None and o["limit_only"] is True
        assert o["flags"][0]["level"] == "warn" and "LIMIT ORDER ONLY" in o["flags"][0]["text"]

    def test_tight_market_is_not_flagged(self):
        o = _build(_q(85.0, 1.90, 2.00))
        assert o is not None and o["limit_only"] is False

    def test_rejections_are_recorded_by_their_binding_reason(self):
        diag = {}
        assert _build(_q(85.0, 0.05, 3.80), diag=diag) is None                # qualifies but too wide → liquidity
        assert _build(_q(84.0, 1.00, 1.05, oi=2, vol=1), diag=diag) is None   # qualifies but dead book
        assert _build(_q(99.0, 3.00, 3.10), diag=diag) is None                # near money → keep too low
        assert _build(_q(60.0, 0.05, 0.10), diag=diag) is None                # far OTM → premium too low
        d = diag["put"]
        assert d["_seen"]["n"] == 4
        assert {k: v["n"] for k, v in d.items() if k != "_seen"} == {"wide": 1, "thin": 1, "prob": 1, "premium": 1}
        assert d["prob"]["ex"]["strike"] == 99.0 and d["premium"]["ex"]["strike"] == 60.0

    def test_economic_failure_wins_over_liquidity(self):
        # A dead quote that would ALSO fail the keep test is reported as "keep too low", not as illiquid.
        diag = {}
        assert _build(_q(99.0, 0.0, 3.10), diag=diag) is None
        assert set(diag["put"]) == {"_seen", "prob"} and diag["put"]["prob"]["ex"]["liquid"] is False


class TestReasons:
    TODAY = date(2026, 10, 20)

    def _diag(self):
        diag = {}
        _build(_q(85.0, 0.05, 3.80), diag=diag)
        _build(_q(99.0, 3.00, 3.10), diag=diag)
        _build(_q(60.0, 0.05, 0.10), diag=diag)
        return diag

    def test_lists_every_reason(self):
        out = _no_trade_reasons(self._diag(), [(EXP, DTE)], ["2026-12-18"], ["cash_secured_put", "iron_condor"],
                                0.85, 20.0, False, "2026-10-21", self.TODAY, "auto", market_open=False)
        text = "\n".join(out)
        assert out[0].startswith(f"Scanned {EXP} ({DTE}d)") and "auto mode" in out[0]
        assert "unavailable for 2026-12-18" in text
        assert "1 clear 85% keep and $20 but can't be filled" in text and "85P bid $0.05 / ask $3.80" in text
        assert "1 keep < 85%" in text and "99P" in text
        assert "1 keep ≥ 85% but pay < $20" in text and "60P" in text
        assert "Multi-leg (iron condor)" in text
        assert "Earnings 2026-10-21" in text
        assert "market is closed" in text
        assert out[-1].startswith("Try ") and "lower keep chance" in out[-1] and "more liquid" in out[-1]

    def test_quiet_context_lines_are_omitted(self):
        out = _no_trade_reasons(self._diag(), [(EXP, DTE)], [], ["cash_secured_put"], 0.85, 20.0, False,
                                "2026-12-30", self.TODAY, "exact", market_open=True)
        text = "\n".join(out)
        assert "the expiry you picked" in out[0]
        assert "Earnings" not in text and "closed" not in text and "Multi-leg" not in text
        assert "specific later expiry" not in out[-1]

    def test_no_strikes_quoted(self):
        out = _no_trade_reasons({}, [(EXP, DTE)], [], ["covered_call"], 0.85, 20.0, True, None, self.TODAY,
                                "auto", market_open=True)
        assert any(l.startswith("Covered calls: no out-of-the-money strikes") for l in out)

    @pytest.mark.parametrize("ts,open_", [("2026-10-20 10:00", True), ("2026-10-20 09:29", False),
                                          ("2026-10-20 16:00", False), ("2026-10-24 12:00", False)])
    def test_market_hours(self, ts, open_):
        t = datetime.strptime(ts, "%Y-%m-%d %H:%M").replace(tzinfo=ZoneInfo("America/New_York"))
        assert _market_open_now(t) is open_


# ── end to end through the scan ──────────────────────────────────────────────────────────────────────────────
def _third_friday_ahead(days):
    d = date.today() + timedelta(days=days)
    while not dis._is_monthly_expiry(d):
        d += timedelta(days=1)
    return d


@pytest.mark.parametrize("half_spread,expect", [(0.35, "limit"), (0.90, "empty")])
def test_scan(monkeypatch, half_spread, expect):
    exp = _third_friday_ahead(20).isoformat()
    T = (date.fromisoformat(exp) - date.today()).days / 365
    quotes = []
    for k in range(70, 131, 2):
        for right in ("P", "C"):
            px = bs_price(S, k, T, 0.045, IV, "put" if right == "P" else "call")
            if px > 0.05:
                bid, ask = round(px * (1 - half_spread), 2), round(px * (1 + half_spread), 2)
                quotes.append(OptionQuote(strike=float(k), right=right, expiration=exp, bid=max(bid, 0.0), ask=ask,
                                          last=px, mid=(max(bid, 0.0) + ask) / 2, iv=IV, oi=400, volume=50))

    class FakeProvider:
        async def get_underlying_price(self, t):
            return SimpleNamespace(price=S)

        async def get_option_expirations(self, t):
            return [exp]

        async def get_option_chain(self, t, e):
            return OptionChain(symbol=t, expiration=e, underlying_price=S, quotes=list(quotes))

    async def fake_sofr():
        return 0.043, "test"

    monkeypatch.setattr(dis, "get_provider", lambda *a, **k: FakeProvider())
    monkeypatch.setattr(dis, "_get_sofr", fake_sofr)
    monkeypatch.setattr(dis, "_context_sync", lambda t: {"hv30": IV, "hv20": IV})
    r = asyncio.run(dis.run_derivative_income("TEST", min_prob=0.85, min_income=20.0,
                                              structures=["cash_secured_put"], db=None))
    if expect == "limit":                                   # 70% of mid wide: every strike limit-only, none hidden
        assert r["n_opportunities"] > 0 and all(o["limit_only"] for o in r["opportunities"])
        assert "note" not in r
    else:                                                   # 180% wide: nothing tradable, and the note says why
        assert r["n_opportunities"] == 0
        assert r["note"].startswith("No trade cleared 85% keep-probability AND $20 premium")
        assert any("can't be filled" in l and "too wide even for a limit order" in l for l in r["note_reasons"])


class TestUntradedWideMarket:
    """A wide quote nobody traded (open interest only) is a placeholder, not a market a limit order fills in."""

    def test_limit_only_needs_a_trade_in_the_latest_session(self):
        assert not _limit_only_ok(_q(85, 1.00, 2.90, oi=500, vol=0), 20.0)
        assert _limit_only_ok(_q(85, 1.00, 2.90, oi=500, vol=0), 20.0, need_volume=False)
        assert _limit_only_ok(_q(85, 1.00, 2.90, oi=500, vol=1), 20.0)

    def test_untraded_wide_strike_is_dropped_and_explained(self):
        diag = {}
        assert _build(_q(85, 1.00, 2.90, oi=500, vol=0), diag=diag) is None
        assert diag["put"]["untraded"]["n"] == 1
        lines = _no_trade_reasons(diag, [(EXP, DTE)], [], ["cash_secured_put"], 0.85, 20.0, False, None,
                                  date.today(), "target", True)
        assert any("NO contracts traded in the latest session" in l for l in lines)

    def test_traded_wide_strike_is_still_offered(self):
        o = _build(_q(85, 1.00, 2.90, oi=500, vol=3))
        assert o is not None and o["limit_only"] is True
