"""Live P&L must never invent a number for a leg it could not price.

The bug (observed live, 2026-10-05): a 4-leg SPXW box (sell P7700 / buy C7700 / buy P8000 / sell C8000,
net debit −$29,645) read **−$29,645 / −100%** on My Trades. The yfinance provider had no alias for the
weekly root `SPXW` (→ `^SPX`), so every leg came back "no quote"; the mark then summed ONLY the priced
legs — none — i.e. valued the whole structure at $0, and `unrealized = 0 + entry_cost`. That phantom loss
flowed straight into the book headline.

Two independent guards, both tested here:
  1. SPXW/NDXP/RUTW/VIXW resolve to their cash index in the quote providers (the cause).
  2. `leg_mark_to_market` refuses to value a structure unless EVERY leg priced (the safety net — any
     future symbol the vendor can't resolve degrades to "no quote", not to a fake total loss).
"""
import asyncio
import datetime as dt
import json
import math
from types import SimpleNamespace

import pytest

import app.routers.saved_strategy_router as router
from app.services import quote_providers
from app.services.quote_providers.base import OptionQuote, INDEX_ROOT_ALIASES, canonical_index_root
from app.services.quote_providers.yfinance_provider import _normalize_ticker
from app.services.quote_providers.ibkr_provider import _normalize_symbol
from app.services.trade_math import ANNUALIZED_CLIP, clipped_annualized_pct, leg_mark_to_market

# ── the real trade-148 legs + the live yfinance mids observed for them (2026-10-05) ──────────────
BOX = [
    {"i": 0, "action": "sell", "type": "put",  "strike": 7700, "qty": 1, "premium": 243.77},
    {"i": 1, "action": "buy",  "type": "call", "strike": 7700, "qty": 1, "premium": 195.72},
    {"i": 2, "action": "buy",  "type": "put",  "strike": 8000, "qty": 1, "premium": 414.90},
    {"i": 3, "action": "sell", "type": "call", "strike": 8000, "qty": 1, "premium": 70.40},
]
ENTRY = -29645.0                                       # BUY negative / SELL positive, ×100
MIDS = {0: 142.2, 1: 270.9, 2: 274.25, 3: 105.55}
# Σ buys − Σ sells at market = (270.9 + 274.25 − 142.2 − 105.55) × 100
MARK = 29740.0
PNL = 95.0                                             # 29,740 − 29,645


def _quotes(mids=MIDS):
    return [{"leg": i, "mid": m} for i, m in mids.items()]


# ── 1 · provider symbol aliasing (the cause) ─────────────────────────────────────────────────────

@pytest.mark.parametrize("raw", ["SPXW", "spxw", ".SPXW", "^SPXW", " SPXW "])
def test_spxw_resolves_to_the_spx_index_on_yfinance_and_ibkr(raw):
    assert _normalize_ticker(raw) == "^SPX"
    assert _normalize_symbol(raw) == "SPX"


@pytest.mark.parametrize("raw,yf,ib", [("NDXP", "^NDX", "NDX"), ("RUTW", "^RUT", "RUT"), ("VIXW", "^VIX", "VIX")])
def test_other_weekly_roots_fold_onto_their_cash_index(raw, yf, ib):
    assert _normalize_ticker(raw) == yf
    assert _normalize_symbol(raw) == ib


@pytest.mark.parametrize("raw,yf", [("SPX", "^SPX"), (".SPX", "^SPX"), ("XSP", "^XSP"), ("AAPL", "AAPL"),
                                    ("/ES", "ES=F"), ("^GSPC", "^GSPC")])
def test_existing_normalisation_is_unchanged(raw, yf):
    assert _normalize_ticker(raw) == yf


def test_canonical_index_root_is_idempotent_and_leaves_equities_alone():
    for k, v in INDEX_ROOT_ALIASES.items():
        assert canonical_index_root(k) == v
        assert canonical_index_root(v) == v            # already canonical
    assert canonical_index_root(".spxw") == "SPX"
    assert canonical_index_root("TSLA") == "TSLA"


# ── 2 · mark-to-market refuses a partial/unpriced structure (the safety net) ─────────────────────

def test_fully_priced_box_marks_to_the_real_value():
    m = leg_mark_to_market(BOX, _quotes(), ENTRY)
    assert m["complete"] and m["unpriced_legs"] == []
    assert m["current_net"] == MARK
    assert m["unrealized_pnl"] == PNL                  # ≈ +$95 — NOT −$29,645
    assert m["priced_legs"] == [0, 1, 2, 3]


def test_unpriced_debit_structure_is_unknown_not_a_100pct_loss():
    """The original bug: no quotes ⇒ value 0 ⇒ pnl = entry_cost = −29,645."""
    errs = [{"leg": i, "error": "Cannot fetch current price for SPXW"} for i in range(4)]
    m = leg_mark_to_market(BOX, errs, ENTRY)
    assert not m["complete"]
    assert m["unrealized_pnl"] is None and m["current_net"] is None
    assert m["unpriced_legs"] == [0, 1, 2, 3]
    assert all("SPXW" in r for r in m["reasons"].values())


def test_unpriced_credit_structure_is_not_a_phantom_full_credit_gain():
    """Mirror image: a credit spread with no quotes used to read +credit (kept it all)."""
    legs = [{"i": 0, "action": "SELL", "qty": 1}, {"i": 1, "action": "BUY", "qty": 1}]
    m = leg_mark_to_market(legs, [{"leg": 0, "error": "no quote"}, {"leg": 1, "error": "no quote"}], 150.0)
    assert m["unrealized_pnl"] is None


def test_a_spread_with_one_unpriced_leg_is_not_half_valued():
    q = _quotes({0: 142.2, 1: 270.9, 2: 274.25})        # C8000 missing entirely
    m = leg_mark_to_market(BOX, q, ENTRY)
    assert not m["complete"] and m["unpriced_legs"] == [3]
    assert m["unrealized_pnl"] is None
    assert m["reasons"] == {3: "no quote"}


@pytest.mark.parametrize("bad", [None, float("nan"), float("inf"), "n/a", True])
def test_a_non_numeric_mid_counts_as_unpriced(bad):
    q = _quotes()
    q[2] = {"leg": 2, "mid": bad}
    assert leg_mark_to_market(BOX, q, ENTRY)["unpriced_legs"] == [2]


def test_a_zero_mid_is_a_price_not_a_missing_quote():
    """A worthless far-OTM wing legitimately marks at 0 — don't flag it as unpriced."""
    legs = [{"i": 0, "action": "SELL", "qty": 1}, {"i": 1, "action": "BUY", "qty": 1}]
    m = leg_mark_to_market(legs, [{"leg": 0, "mid": 1.50}, {"leg": 1, "mid": 0.0}], 150.0)
    assert m["complete"] and m["current_net"] == -150.0 and m["unrealized_pnl"] == 0.0


def test_a_retry_that_priced_the_leg_beats_the_earlier_error_row():
    q = [{"leg": i, "error": "warmup"} for i in range(4)] + _quotes()
    assert leg_mark_to_market(BOX, q, ENTRY)["complete"]
    q = _quotes() + [{"leg": 1, "error": "late failure"}]   # …and a later error must not clobber a good price
    assert leg_mark_to_market(BOX, q, ENTRY)["complete"]


def test_contracts_scale_the_mark_linearly():
    legs = [{"i": 0, "action": "BUY", "qty": 3}]
    m = leg_mark_to_market(legs, [{"leg": 0, "mid": 2.0}], -600.0)
    assert m["current_net"] == 600.0 and m["unrealized_pnl"] == 0.0


def test_no_legs_keeps_the_legacy_entry_cost_only_behaviour():
    m = leg_mark_to_market([], [], 123.0)
    assert m["complete"] and m["current_net"] == 0.0 and m["unrealized_pnl"] == 123.0


# ── annualised-return clip ───────────────────────────────────────────────────────────────────────

def test_the_999pct_rail_is_reported_as_unknown_not_999():
    assert clipped_annualized_pct(ANNUALIZED_CLIP) is None
    assert clipped_annualized_pct(-ANNUALIZED_CLIP) is None
    assert clipped_annualized_pct(250.0) is None          # an extrapolated 25,000% (was clipped to 999.0)
    assert clipped_annualized_pct(0.3071) == 30.7
    assert clipped_annualized_pct(-0.25) == -25.0
    assert clipped_annualized_pct(9.98) == 998.0          # just under the rail is still a real number


def test_non_finite_or_complex_annualisations_are_unknown():
    assert clipped_annualized_pct(float("nan")) is None
    assert clipped_annualized_pct(float("inf")) is None
    assert clipped_annualized_pct((-0.5) ** 0.3) is None   # negative base ** fractional → complex in py3
    assert clipped_annualized_pct(None) is None


# ── 3 · the real handler end to end ──────────────────────────────────────────────────────────────

EXP = "2026-12-18"
SPOT = 7773.95


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


def _box_strategy(**over):
    legs = [{"action": l["action"], "type": l["type"], "strike": l["strike"], "expiration": EXP,
             "qty": 1, "premium": l["premium"]} for l in BOX]
    s = dict(id=148, user_id=1, ticker="SPXW", strategy_type="options_spread", trade_status="active",
             legs_data=json.dumps(legs), entry_prices=json.dumps([{"price": l["premium"]} for l in BOX]),
             entry_net_debit=ENTRY, parameters=json.dumps({"expiration": EXP}), result_snapshot=None,
             entry_date=dt.datetime(2026, 9, 1, tzinfo=dt.timezone.utc))
    s.update(over)
    return SimpleNamespace(**s)


def _oq(right, strike, mid):
    return OptionQuote(strike=float(strike), right=right, expiration=EXP, bid=mid - .5, ask=mid + .5,
                       last=mid, mid=mid, iv=0.15, oi=500, volume=50)


_REAL = [_oq("P", 7700, 142.2), _oq("C", 7700, 270.9), _oq("P", 8000, 274.25), _oq("C", 8000, 105.55)]


class _Provider:
    """`down` = the vendor can't resolve the symbol (what SPXW did). Otherwise serves `quotes`."""
    def __init__(self, quotes=None, down=False, spot=SPOT):
        self.quotes, self.down, self.spot = quotes or [], down, spot

    async def get_underlying_price(self, sym):
        if self.down:
            raise ValueError(f"Cannot fetch current price for {sym}")
        return SimpleNamespace(price=self.spot)

    async def get_option_chain(self, sym, exp):
        if self.down:
            raise ValueError(f"Cannot fetch current price for {sym}")
        return SimpleNamespace(quotes=self.quotes, underlying_price=self.spot)


def _live(monkeypatch, strategy, provider):
    monkeypatch.setattr(quote_providers, "get_provider", lambda *a, **k: provider)
    return asyncio.run(router.get_live_pnl(strategy_id=strategy.id, quote_source="yfinance", margin_mode="reg_t",
                                           user=SimpleNamespace(id=1), db=_DB(strategy)))


def test_handler_unpriced_box_reports_no_pnl_instead_of_minus_100pct(monkeypatch):
    r = _live(monkeypatch, _box_strategy(), _Provider(down=True))
    assert r["pricing_complete"] is False
    assert r["unrealized_pnl"] is None and r["pnl_pct"] is None and r["current_value"] is None
    assert r["unpriced_legs"] == [0, 1, 2, 3]
    assert "4 of 4" in r["pricing_warning"] and "SPXW" in r["pricing_warning"]
    assert r["analysis"]["hold_vs_close_reasons"][0] == r["pricing_warning"]   # warning leads the signal reasons
    assert r["analysis"]["captured_pct"] is None
    json.dumps(r, default=str)                                                  # still serialisable for the snapshot
    # nothing in the payload may carry the phantom loss
    assert -29645.0 not in [r["unrealized_pnl"], r["current_value"]]


def test_handler_partially_priced_box_is_also_unknown(monkeypatch):
    r = _live(monkeypatch, _box_strategy(), _Provider(_REAL[:3]))               # C8000 absent from the chain
    assert r["pricing_complete"] is False and r["unpriced_legs"] == [3]
    assert r["unrealized_pnl"] is None


def test_handler_priced_box_marks_to_plus_95(monkeypatch):
    r = _live(monkeypatch, _box_strategy(), _Provider(_REAL))
    assert r["pricing_complete"] is True and r["unpriced_legs"] == [] and r["pricing_warning"] is None
    assert r["current_value"] == MARK
    assert r["unrealized_pnl"] == PNL
    assert r["pnl_pct"] == round(PNL / abs(r["total_capital"]) * 100, 2)
    assert r["unrealized_pnl"] == round(r["current_value"] + r["entry_cost"], 2)  # reconciles with its own legs


def test_handler_combo_with_an_unpriced_option_leg_keeps_stock_pnl_and_drops_option_pnl(monkeypatch):
    legs = [{"action": "sell", "type": "call", "strike": 8000, "expiration": EXP, "qty": 1, "premium": 70.4}]
    s = _box_strategy(id=7, ticker="SPXW", strategy_type="covered_call", legs_data=json.dumps(legs),
                      entry_prices=json.dumps([{"price": 7000.0}, {"price": 70.4}]), entry_net_debit=-692960.0,
                      parameters=json.dumps({"shares": 100, "avg_cost": 7000.0, "expiration": EXP,
                                             "options_net_debit": 7040.0}))
    down = _live(monkeypatch, s, _Provider(down=True))
    assert down["pricing_complete"] is False and down["unpriced_legs"] == [0]
    assert down["unrealized_pnl"] is None and down["options_pnl"] is None
    assert down["options_breakdown"]["options_pnl"] is None
    assert isinstance(down["stock_pnl"], float)                                  # stock leg is independent of the chain
    up = _live(monkeypatch, s, _Provider([_oq("C", 8000, 105.55)]))
    assert up["pricing_complete"] is True
    assert up["options_pnl"] == round(-105.55 * 100 + 7040.0, 2)                 # short call: credit − buyback
    assert up["unrealized_pnl"] == round(up["stock_pnl"] + up["options_pnl"], 2)


def test_every_priced_payload_still_reports_pricing_complete(monkeypatch):
    """The new fields are always present so the frontend never has to guess a legacy payload."""
    r = _live(monkeypatch, _box_strategy(), _Provider(_REAL))
    assert {"pricing_complete", "unpriced_legs", "pricing_warning"} <= r.keys()
    assert math.isfinite(r["unrealized_pnl"])
