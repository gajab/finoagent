"""Manage Book: (1) covered calls net their shares' P&L in the stress scenarios, with the downside losses
optional; (2) a fix is priced from the LIVE quotes captured at Refresh, never a model price."""
import asyncio
from types import SimpleNamespace

import numpy as np
import pytest

import app.services.book_tail_risk as bt
from app.services.book_tail_risk import (
    reprice_scenario, book_mc, _covered_shares, _pay_price, _quote_dict, _remediate_position,
    _short_extrinsic, _CRASH,
)
from app.services.lifecycle_service import higher_order_greeks

R = 0.045


def _leg(strike, right="C", sign=-1, qty=1, iv=0.40, dte=30, exp="2026-11-20", bid=None, ask=None, mid=None):
    return {"strike": strike, "right": right, "sign": sign, "qty": qty, "iv": iv, "dte_years": dte / 365.0,
            "exp": exp, "dte_days": dte, "bid": bid, "ask": ask, "mid": mid}


def _pos(legs, spot=60.0, shares=0.0, covered=False, structure="options", ticker="XYZ", beta=1.0, quotes=None, strikes=None, tid=1):
    g = higher_order_greeks(legs, spot, r=R, stock_shares=0.0)
    p = {"ticker": ticker, "name": ticker, "trade_id": tid, "spot": spot, "iv": 0.40, "beta": beta, "n_short": sum(1 for l in legs if l["sign"] < 0),
         "capital": 6000.0, "legs": legs, "shares": shares, "has_stock": bool(shares), "covered": covered, "structure": structure,
         "strikes_by_exp": strikes or {}, "quotes_by_exp": quotes or {}, **g}
    p["cov_shares"] = _covered_shares(p)
    return p


# ── covered shares ────────────────────────────────────────────────────────────
class TestCoveredShares:
    def test_real_shares_cover_up_to_the_calls_size(self):
        assert _covered_shares(_pos([_leg(65)], shares=100)) == 100
        assert _covered_shares(_pos([_leg(65)], shares=500)) == 100          # extra shares aren't behind THIS call
        assert _covered_shares(_pos([_leg(65, qty=3)], shares=100)) == 100   # 3 calls, 1 lot of shares → 1 covered

    def test_naked_or_short_stock_has_no_cover(self):
        assert _covered_shares(_pos([_leg(65)], shares=0)) == 0
        assert _covered_shares(_pos([_leg(65)], shares=-100)) == 0
        assert _covered_shares(_pos([_leg(55, right="P")], shares=100)) == 0  # a short PUT is not covered by shares

    def test_the_covered_mark_or_structure_covers_every_short_call_without_recorded_shares(self):
        assert _covered_shares(_pos([_leg(65, qty=2)], covered=True)) == 200
        assert _covered_shares(_pos([_leg(65, qty=2)], structure="covered_call")) == 200


class TestScenarioNetting:
    def test_a_covered_call_is_not_a_loss_in_a_squeeze(self):
        cc = [_pos([_leg(65)], shares=100, structure="covered_call")]
        naked = [_pos([_leg(65)])]
        for label, mv, vs in _CRASH:
            if mv <= 0:
                continue
            assert reprice_scenario(naked, mv, vs, R) < 0                    # a naked call loses
            assert reprice_scenario(cc, mv, vs, R) > 0, label               # the covered call does not
        # exactly the shares' gain on top of the overlay
        ov = reprice_scenario(naked, 0.35, 8.0, R)
        assert reprice_scenario(cc, 0.35, 8.0, R) == pytest.approx(ov + 100 * 60.0 * 0.35, abs=1e-6)

    def test_the_downside_excludes_the_shares_unless_asked(self):
        cc = _pos([_leg(65)], shares=100, structure="covered_call")
        base = reprice_scenario([cc], -0.20, 15.0, R)
        with_stock = reprice_scenario([{**cc, "down_stock": True}], -0.20, 15.0, R)
        assert with_stock == pytest.approx(base + 100 * (60.0 * 0.8 - 60.0), abs=1e-6)   # −$1,200 of share loss
        assert base == reprice_scenario([_pos([_leg(65)])], -0.20, 15.0, R)               # = the bare overlay

    def test_beta_scales_the_share_move_like_the_option_move(self):
        cc = _pos([_leg(65)], shares=100, structure="covered_call", beta=2.0)
        up = reprice_scenario([cc], 0.10, -3.0, R)
        naked = reprice_scenario([_pos([_leg(65)], beta=2.0)], 0.10, -3.0, R)
        assert up == pytest.approx(naked + 100 * 60.0 * 0.20, abs=1e-6)

    def test_naked_and_spread_positions_are_unchanged(self):
        pos = _pos([_leg(65), _leg(75, sign=1)])                 # call credit spread, no shares
        assert pos["cov_shares"] == 0
        assert reprice_scenario([pos], 0.20, -6.0, R) == reprice_scenario([{**pos, "down_stock": True}], 0.20, -6.0, R)

    def test_monte_carlo_follows_the_same_rule(self):
        cc = _pos([_leg(65)], shares=100, structure="covered_call")
        overlay = book_mc([_pos([_leg(65)])], R, 0.18, 5000.0)["pnl"]
        default = book_mc([cc], R, 0.18, 5000.0)["pnl"]
        with_stock = book_mc([{**cc, "down_stock": True}], R, 0.18, 5000.0)["pnl"]
        assert np.all(default >= overlay - 1e-9)                 # shares' GAIN only ever helps
        assert np.all(with_stock <= default + 1e-9)              # their LOSS only ever hurts
        assert default.max() > overlay.max() + 100               # …and it does help in the up-tail
        assert np.percentile(-with_stock, 95) > np.percentile(-default, 95)   # CVaR is larger with the share losses


# ── pricing a fix from live quotes ────────────────────────────────────────────
def _q(bid, ask, iv, mid=None):
    return {"bid": bid, "ask": ask, "mid": mid if mid is not None else ((bid + ask) / 2 if bid is not None and ask is not None else None), "iv": iv}


class TestPayPrice:
    def test_pays_the_ask_then_the_mid_then_falls_back_to_a_model(self):
        assert _pay_price(_q(0.50, 0.60, 0.5)) == (0.60, "live ask")
        assert _pay_price({"bid": 0.0, "ask": 0.0, "mid": 0.4, "iv": 0.5}) == (0.4, "live mid")
        assert _pay_price({"bid": 0.0, "ask": 0.0, "mid": 0.0, "iv": 0.5}) == (None, "model")
        assert _pay_price(None) == (None, "model")

    def test_a_provider_quote_becomes_plain_floats_and_nan_becomes_none(self):
        q = SimpleNamespace(bid=1.0, ask="1.2", mid=float("nan"), iv=None)
        assert _quote_dict(q) == {"bid": 1.0, "ask": 1.2, "mid": None, "last": None, "iv": None, "oi": None, "volume": None}
        assert _quote_dict(None) is None


class TestRemediationPricing:
    def _naked_call(self, wing_quote, short_ask=3.10, short_mid=3.00):
        short = _leg(400, qty=4, iv=0.30, bid=2.9, ask=short_ask, mid=short_mid, exp="2026-10-16")
        quotes = {"2026-10-16": {"C": {440.0: wing_quote}}}
        return _pos([short], spot=380.0, ticker="GOOG", quotes=quotes, strikes={"2026-10-16": {"C": [400.0, 440.0], "P": []}})

    def test_the_wing_costs_its_live_ask_not_a_model_price(self):
        rem = _remediate_position(self._naked_call(_q(0.10, 0.14, 0.35)), "up", R)
        cap = next(o for o in [rem["recommended"], rem["alt"]] if o and o["action"] == "cap")
        assert cap["cost"] == pytest.approx(0.14 * 100 * 4)        # 4 contracts × $0.14 ask
        assert cap["pricing"] == "live ask" and cap["unit_price"] == 0.14
        assert cap["cost"] > 0                                      # never the "$0" a model gives for a far-OTM wing

    def test_it_falls_back_to_a_model_price_only_when_there_is_no_quote_and_says_so(self):
        rem = _remediate_position(self._naked_call(None), "up", R)
        cap = next(o for o in [rem["recommended"], rem["alt"]] if o and o["action"] == "cap")
        assert cap["pricing"] == "model"

    def test_the_wing_is_repriced_with_its_own_iv(self):
        from app.services.stock_service import bs_price
        def consistent(iv):             # a quote whose IV reprices to its own price (what a healthy chain looks like)
            px = bs_price(380.0, 440.0, 30 / 365.0, R, iv, "call")
            return _q(px * 0.95, px * 1.05, iv)
        cheap = _remediate_position(self._naked_call(consistent(0.20)), "up", R)
        rich = _remediate_position(self._naked_call(consistent(0.90)), "up", R)
        pick = lambda r: next(o for o in [r["recommended"], r["alt"]] if o and o["action"] == "cap")["tail_after"]
        assert pick(rich) > pick(cheap)                            # a richer wing pays more in the squeeze

    def test_a_junk_wing_iv_is_replaced_by_the_vol_its_own_price_implies(self):
        from app.services.stock_service import bs_price
        px = bs_price(380.0, 440.0, 30 / 365.0, R, 0.45, "call")
        junk = _q(px * 0.95, px * 1.05, 1e-5)                       # after-hours yfinance IV
        good = _q(px * 0.95, px * 1.05, 0.45)
        pick = lambda r: next(o for o in [r["recommended"], r["alt"]] if o and o["action"] == "cap")["tail_after"]
        assert pick(_remediate_position(self._naked_call(junk), "up", R)) == pytest.approx(pick(_remediate_position(self._naked_call(good), "up", R)), rel=0.03)

    def test_closing_costs_the_shorts_live_ask(self):
        rem = _remediate_position(self._naked_call(_q(0.10, 0.14, 0.35), short_ask=3.10), "up", R)
        close = next(o for o in [rem["recommended"], rem["alt"]] if o and o["action"] == "close")
        assert close["cost"] == pytest.approx(3.10 * 100 * 4) and close["pricing"] == "live ask"

    def test_premium_left_uses_the_live_mid(self):
        lg = _leg(400, iv=0.30, mid=3.00)
        assert _short_extrinsic(380.0, lg, R) == pytest.approx(300.0)             # OTM: all mid is extrinsic
        lg_itm = _leg(370, iv=0.30, mid=14.0)
        assert _short_extrinsic(380.0, lg_itm, R) == pytest.approx((14.0 - 10.0) * 100)

    def test_the_stress_behind_risk_now_is_disclosed(self):
        rem = _remediate_position(_pos([_leg(65, qty=1, exp="2026-10-16")], beta=1.9,
                                       strikes={"2026-10-16": {"C": [65.0, 72.0], "P": []}}), "up", R)
        assert rem["stress"]["market_move_pct"] == 30.0 and rem["stress"]["beta"] == 1.9
        assert rem["stress"]["name_move_pct"] == pytest.approx(57.0) and rem["stress"]["vol_shock_pts"] == pytest.approx(-9.0)


# ── the whole book, both downside policies ────────────────────────────────────
class _Provider:
    async def get_underlying_price(self, t):
        return SimpleNamespace(price={"^GSPC": 5200.0, "^VIX": 18.0}.get(t, 60.0))


@pytest.fixture
def book(monkeypatch):
    calls = {"puts": 0, "vcalls": 0, "factor": 0}
    cc = _pos([_leg(65, iv=0.40, bid=1.0, ask=1.2, mid=1.1)], shares=100, structure="covered_call", ticker="CCX", tid=1)
    naked = _pos([_leg(70, iv=0.40, bid=0.8, ask=1.0, mid=0.9)], ticker="NKD", tid=2)
    by_id = {1: cc, 2: naked}

    async def fake_pos(strategy, *a, **k): return dict(by_id[strategy.id])
    async def fake_puts(*a, **k): calls["puts"] += 1; return [], None
    async def fake_vcalls(*a, **k): calls["vcalls"] += 1; return [], None
    async def fake_factor(*a, **k): calls["factor"] += 1; return None
    async def fake_sofr(): return (R, None)

    import app.services.derivative_income_service as dis
    import app.services.quote_providers as qp
    monkeypatch.setattr(bt, "_position_greeks", fake_pos)
    monkeypatch.setattr(bt, "_compute_betas", lambda tickers: {t: 1.0 for t in tickers})
    monkeypatch.setattr(bt, "_index_puts", fake_puts)
    monkeypatch.setattr(bt, "_vix_calls", fake_vcalls)
    monkeypatch.setattr(bt, "_vix_levels", lambda: (18.0, 19.0))
    monkeypatch.setattr(bt, "_book_factor_exposure", fake_factor)
    monkeypatch.setattr(dis, "_get_sofr", fake_sofr)
    monkeypatch.setattr(qp, "get_provider", lambda *a, **k: _Provider())
    return calls


def _run(strategies):
    return asyncio.run(bt.compute_book_tail_risk(strategies, "yfinance", SimpleNamespace(id=1), None))


def test_a_book_with_covered_calls_returns_both_downside_variants_and_fetches_the_market_once(book):
    out = _run([SimpleNamespace(id=1), SimpleNamespace(id=2)])
    assert out["has_covered_stock"] is True and "with_stock_downside" in out
    alt = out["with_stock_downside"]
    by = lambda scs: {c["label"]: c["pnl"] for c in scs}
    base, withs = by(out["crash_scenarios"]), by(alt["crash_scenarios"])
    for label in ("Melt-up +10%", "Melt-up +20%", "Squeeze +35%"):
        assert base[label] == withs[label]                      # rallies: identical (the shares' gain is always counted)
    for label in ("−20%", "−10%", "COVID −34%", "GFC −50%"):
        assert withs[label] < base[label]                       # downside: the shares' loss is added
    assert (alt["cvar_95"] or 0) > (out["cvar_95"] or 0)
    assert set(alt) <= set(bt._DOWNSIDE_KEYS) and "concentration" not in alt   # only the policy-dependent fields
    assert book["factor"] == 1   # the factor lookup is shared by both passes, not repeated


def test_the_covered_call_no_longer_shows_a_melt_up_loss_but_the_naked_call_does(book):
    out = _run([SimpleNamespace(id=1), SimpleNamespace(id=2)])
    names = {n["ticker"]: n["pnl"] for n in out["loss_by_name"]}      # −20% month: overlay only, by name
    assert "CCX" in names
    # up-side fix targets: only the NAKED call is offered a fix; the covered call has no squeeze loss to fix
    fixed = {t["ticker"] for c in out["risk_scorecard"]["checks"] if c.get("fix") for t in (c["fix"].get("targets") or [])}
    assert "CCX" not in fixed


def test_a_book_without_covered_shares_has_no_toggle(book):
    out = _run([SimpleNamespace(id=2)])
    assert out["has_covered_stock"] is False and "with_stock_downside" not in out


# ── committed capital counts contracts ─────────────────────────────────────────
def test_committed_capital_is_strike_times_100_times_contracts():
    import json
    from datetime import date, timedelta
    exp = (date.today() + timedelta(days=30)).isoformat()

    class Prov:
        async def get_underlying_price(self, t): return SimpleNamespace(price=380.0)
        async def get_option_chain(self, t, e): return None

    def strat(legs):
        return SimpleNamespace(id=1, ticker="GOOG", name="g", strategy_type="options", legs_data=json.dumps(legs), parameters="{}")

    async def cap(legs):
        return (await bt._position_greeks(strat(legs), Prov(), R, date.today(), {}, {}))["capital"]

    one = asyncio.run(cap([{"action": "SELL", "type": "call", "strike": 400, "qty": 1, "expiration": exp, "iv": 30}]))
    four = asyncio.run(cap([{"action": "SELL", "type": "call", "strike": 400, "qty": 4, "expiration": exp, "iv": 30}]))
    assert one == 40000 and four == 160000          # a 4-lot ties up 4× the capital (it used to read as 1×)
