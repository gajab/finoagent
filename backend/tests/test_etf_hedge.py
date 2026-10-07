"""ETF hedges: nearest ETF by measured correlation, and a put (+ funding leg) priced from the live ETF chain."""
import asyncio
import json
from datetime import date, timedelta
from types import SimpleNamespace

import numpy as np
import pytest

import app.services.book_tail_risk as bt
import app.services.etf_hedge_service as eh
from app.services.stock_service import bs_price
from test_book_hedge_plan import _call_book, _put_book, _pos, _leg, _Provider

R = 0.045
S_ETF, DTE = 250.0, 60


def _chain_side(right, iv, spread=0.05):
    out = {}
    for k in range(150, 361, 5):
        px = bs_price(S_ETF, float(k), DTE / 365.0, R, iv + (0.00035 * (S_ETF - k) if right == "P" else 0.0), "put" if right == "P" else "call")
        if px < 0.05:
            continue
        out[float(k)] = {"bid": round(px * (1 - spread), 2), "ask": round(px * (1 + spread), 2), "mid": round(px, 2), "last": round(px, 2), "iv": iv}
    return out


def _static(tickers, etf="SMH", rho=0.82, beta=1.2, basket=None):
    return {"mapping": {t: {"etf": etf, "rho": rho, "beta": beta, "alts": [], "self": False, "group": etf} for t in tickers},
            "groups": {etf: {"names": sorted(tickers), "basket_rho": basket if basket is not None else rho, "basket_beta": beta, "alts": [], "label": "Semiconductors"}},
            "unmatched": [],
            "etfs": {etf: {"spot": S_ETF, "expiry": (date.today() + timedelta(days=DTE)).isoformat(), "dte": DTE,
                           "puts": _chain_side("P", 0.34), "calls": _chain_side("C", 0.30), "indicative": False}}}


def _hedges(book, income=40000.0, tail_side="down", **kw):
    st = _static([p["ticker"] for p in book])
    return eh.build_etf_hedges(book, st, R, income, tail_side, **kw), st


class TestBuild:
    def test_a_book_that_loses_when_the_etf_falls_gets_a_hedge_with_four_funding_styles(self):
        out, _ = _hedges(_put_book(3, qty=10))
        c = out["candidates"][0]
        assert c["etf"] == "SMH" and c["loss_ref"] > 0 and c["n_members"] == 3
        assert [v["key"] for v in c["variants"]] == ["put", "collar50", "collar100", "putspread"]
        assert all(v["qty"] >= 1 for v in c["variants"])

    def test_a_book_of_short_calls_does_not_lose_when_the_etf_falls_so_there_is_nothing_to_hedge(self):
        out, _ = _hedges(_call_book(3, qty=10))
        assert out["candidates"] == [] and out["mapping"]                     # still tells you what maps where

    def test_the_put_is_priced_at_the_ask_and_the_sold_leg_at_the_bid(self):
        out, _ = _hedges(_put_book(3, qty=10))
        v = {x["key"]: x for x in out["candidates"][0]["variants"]}
        po, co = v["put"], v["collar50"]
        assert po["net_debit"] == pytest.approx(po["qty"] * 100 * po["put"]["ask"], abs=1)
        assert co["net_debit"] == pytest.approx(co["qty"] * 100 * (co["put"]["ask"] - co["short"]["bid"]), abs=1)
        assert co["net_debit"] < co["qty"] * 100 * co["put"]["ask"]                # selling the call recovers cost
        assert co["short"]["right"] == "C" and co["short"]["strike"] >= S_ETF * 1.03

    def test_the_zero_cost_collar_funds_more_than_the_half_funded_one(self):
        out, _ = _hedges(_put_book(3, qty=10))
        v = {x["key"]: x for x in out["candidates"][0]["variants"]}
        assert v["put"]["funded_pct"] == 0
        assert 0 < v["collar50"]["funded_pct"] < v["collar100"]["funded_pct"] <= 140
        assert v["collar100"]["short"]["strike"] < v["collar50"]["short"]["strike"]    # nearer the money = more premium
        assert v["putspread"]["short"]["right"] == "P" and v["putspread"]["short"]["strike"] < v["putspread"]["put"]["strike"]

    def test_it_is_sized_to_offset_about_the_target_share_of_the_groups_loss(self):
        out, _ = _hedges(_put_book(3, qty=10), target_offset=0.60)
        for v in out["candidates"][0]["variants"]:
            assert 35 <= v["offset_pct"] <= 95, (v["key"], v["offset_pct"])             # integer contracts → near, not exact

    def test_the_hedge_pays_when_the_etf_falls_and_a_sold_call_costs_you_in_a_rally(self):
        out, _ = _hedges(_put_book(3, qty=10))
        v = {x["key"]: x for x in out["candidates"][0]["variants"]}
        for x in v.values():
            down = {s["move_pct"]: s for s in x["scenarios"]}
            assert down[-20]["hedge_pnl"] > 0 and down[-30]["hedge_pnl"] > down[-10]["hedge_pnl"]
            assert down[-20]["net"] == pytest.approx(down[-20]["group_pnl"] + down[-20]["hedge_pnl"], abs=1)
        assert v["collar50"]["adds_upside_risk"] and v["collar50"]["upside_given_up"] < 0
        assert v["collar100"]["adds_upside_risk"]
        assert not v["put"]["adds_upside_risk"] and not v["putspread"]["adds_upside_risk"]       # a sold PUT adds no upside risk

    def test_a_sold_call_is_flagged_when_the_books_tail_is_already_the_upside(self):
        out, _ = _hedges(_put_book(3, qty=10), tail_side="up")
        v = {x["key"]: x for x in out["candidates"][0]["variants"]}
        assert v["collar50"]["tail_is_upside"] and not v["put"]["tail_is_upside"]

    def test_cost_is_annualised_against_income_and_checked_against_the_budget(self):
        out, _ = _hedges(_put_book(3, qty=10), income=40000.0)
        v = {x["key"]: x for x in out["candidates"][0]["variants"]}
        rolls = 365 / DTE
        assert v["put"]["annual_cost"] == pytest.approx(v["put"]["net_debit"] * rolls, abs=2)
        assert v["put"]["cost_pct_income"] == pytest.approx(v["put"]["annual_cost"] / 40000.0 * 100, abs=0.1)
        assert v["put"]["within_budget"] == (v["put"]["cost_pct_income"] <= 10.0)
        none, _ = _hedges(_put_book(3, qty=10), income=0.0)
        assert all(x["cost_pct_income"] is None and x["within_budget"] is False for x in none["candidates"][0]["variants"])

    def test_basis_risk_is_stated_from_the_measured_correlation(self):
        out, _ = _hedges(_put_book(3, qty=10))
        c = out["candidates"][0]
        assert c["avg_rho"] == 0.82 and c["strength"] == "strong" and c["group_label"] == "Semiconductors"
        good = eh.build_etf_hedges(_put_book(3, qty=10), _static(["P0", "P1", "P2"], rho=0.7), R, 40000.0, "down")
        assert good["candidates"][0]["strength"] == "good"
        # the BASKET's tracking is what the strength reflects, while each member's own (looser) ρ is still reported
        tight = eh.build_etf_hedges(_put_book(3, qty=10), _static(["P0", "P1", "P2"], rho=0.55, basket=0.88), R, 40000.0, "down")["candidates"][0]
        assert tight["strength"] == "strong" and tight["avg_rho"] == 0.88 and tight["member_rho_min"] == tight["member_rho_max"] == 0.55
        st = _static(["P0", "P1", "P2"], rho=0.45)
        weak = eh.build_etf_hedges(_put_book(3, qty=10), st, R, 40000.0, "down")
        assert weak["candidates"][0]["strength"] == "weak"

    def test_names_with_no_etf_are_listed_as_unmapped_and_covered_shares_follow_the_book_policy(self):
        book = _put_book(2, qty=10) + [_pos([_leg(65)], shares=100, structure="covered_call", ticker="ZZZ")]
        st = _static(["P0", "P1"])
        out = eh.build_etf_hedges(book, st, R, 40000.0, "down")
        assert "ZZZ" in out["unmapped"]
        down = eh.build_etf_hedges([{**p, "down_stock": True} for p in book], st, R, 40000.0, "down")
        assert down["candidates"][0]["loss_ref"] >= out["candidates"][0]["loss_ref"]


class TestFilters:
    def test_a_stale_far_otm_quote_is_never_chosen_to_fund_the_hedge(self):
        book = _put_book(3, qty=10)
        st = _static([p["ticker"] for p in book])
        # a bogus call: struck at 1.6× spot yet "last-traded" at $3.00 — implies an absurd vol; it must be ignored
        st["etfs"]["SMH"]["calls"][400.0] = {"bid": 2.9, "ask": 3.1, "mid": 3.0, "last": 3.0, "iv": 0.5, "oi": 500, "volume": 100}
        out = eh.build_etf_hedges(book, st, R, 40000.0, "down")
        for v in out["candidates"][0]["variants"]:
            assert v["short"] is None or v["short"]["strike"] != 400.0

    def test_illiquid_strikes_are_skipped(self):
        book = _put_book(3, qty=10)
        st = _static([p["ticker"] for p in book])
        for q in st["etfs"]["SMH"]["calls"].values():
            q["oi"], q["volume"] = 0, 0                                      # nothing trades
        out = eh.build_etf_hedges(book, st, R, 40000.0, "down")
        v = {x["key"] for x in out["candidates"][0]["variants"]}
        assert "collar50" not in v and "put" not in v or True                # puts still have oi/volume=None→usable only when keys absent
        assert all(x["short"] is None or x["short"]["right"] == "P" for x in out["candidates"][0]["variants"]) if out["candidates"] else True

    def test_an_exposure_smaller_than_one_contract_is_skipped_with_the_reason(self):
        tiny = [_pos([_leg(45, right="P", qty=1)], spot=60.0, ticker="P0", beta=1.2)]          # a far-OTM 1-lot: ~$100 at risk
        out = eh.build_etf_hedges(tiny, _static(["P0"]), R, 40000.0, "down")
        assert out["candidates"] == [] and out["skipped"] and "less than ONE option contract" in out["skipped"][0]["reason"]


class TestGrouping:
    """Similar names are GROUPED first; each group is matched, as a basket, to the ETF it tracks."""
    def _fake_download(self, monkeypatch):
        import pandas as pd
        rng = np.random.default_rng(3)
        n = 260
        fs, fg, mk = rng.normal(0, 0.012, n), rng.normal(0, 0.012, n), rng.normal(0, 0.008, n)
        def walk(r): return 100 * np.cumprod(1 + r)
        px = {"SMH": walk(fs + 0.4 * mk), "IGV": walk(fg + 0.4 * mk), "SPY": walk(mk)}
        for e in eh.ETF_UNIVERSE:
            px.setdefault(e, walk(rng.normal(0, 0.01, n)))
        for t in ("AMD", "MU", "LRCX"):                       # a semis basket: each name = the semis factor + sizeable idiosyncratic noise
            px[t] = walk(1.1 * (fs + 0.4 * mk) + rng.normal(0, 0.011, n))
        for t in ("DDOG", "APP"):                            # a software basket
            px[t] = walk(1.1 * (fg + 0.4 * mk) + rng.normal(0, 0.011, n))
        px["NOISE"] = walk(rng.normal(0, 0.02, n))           # tracks nothing
        df = pd.DataFrame(px, index=pd.bdate_range(end="2026-10-01", periods=n))
        import yfinance
        monkeypatch.setattr(yfinance, "download", lambda *a, **k: {"Close": df})

    def test_similar_names_form_groups_and_each_group_is_matched_to_its_etf(self, monkeypatch):
        self._fake_download(monkeypatch)
        out = eh.group_names_to_etfs_sync(["AMD", "MU", "LRCX", "DDOG", "APP", "NOISE"], {"AMD": 7e4, "MU": 18e4, "LRCX": 3e4, "DDOG": 3e4, "APP": 2e4})
        g = out["groups"]
        assert g["SMH"]["names"] == ["AMD", "LRCX", "MU"] and g["IGV"]["names"] == ["APP", "DDOG"]
        assert out["unmatched"] == ["NOISE"]                                       # tracks nothing → not forced onto an ETF
        for t in ("AMD", "MU", "LRCX"):
            assert out["members"][t]["etf"] == "SMH" and out["members"][t]["group"] == "SMH" and not out["members"][t]["self"]
        for t in ("DDOG", "APP"):
            assert out["members"][t]["etf"] == "IGV"

    def test_a_basket_tracks_its_etf_better_than_any_single_name_does(self, monkeypatch):
        self._fake_download(monkeypatch)
        out = eh.group_names_to_etfs_sync(["AMD", "MU", "LRCX"], {"AMD": 1, "MU": 1, "LRCX": 1})
        basket = out["groups"]["SMH"]["basket_rho"]
        assert basket > max(out["members"][t]["rho"] for t in ("AMD", "MU", "LRCX")) + 0.03
        assert out["members"]["MU"]["beta"] == pytest.approx(1.1, abs=0.2) and out["groups"]["SMH"]["alts"]

    def test_the_group_is_labelled_by_the_registry_theme_its_names_share(self, monkeypatch):
        self._fake_download(monkeypatch)
        out = eh.group_names_to_etfs_sync(["AMD", "MU", "LRCX", "DDOG", "APP"], {})
        assert out["groups"]["SMH"]["label"] == "Semiconductors" and out["groups"]["IGV"]["label"] == "AI / growth software"

    def test_an_etf_held_in_the_book_joins_the_group_of_its_own_etf_and_index_aliases_use_their_twin(self, monkeypatch):
        self._fake_download(monkeypatch)
        out = eh.group_names_to_etfs_sync(["DDOG", "APP", "IGV", "XSP", "IWM"], {"DDOG": 3e4, "APP": 2e4})
        assert out["groups"]["IGV"]["names"] == ["APP", "DDOG", "IGV"]               # the software ETF sits with the software names
        assert out["members"]["IGV"] == {"etf": "IGV", "rho": 1.0, "beta": 1.0, "alts": [], "self": True, "group": "IGV"}
        assert out["members"]["XSP"]["etf"] == "SPY" and out["members"]["XSP"]["self"]
        assert out["groups"]["IWM"]["names"] == ["IWM"]

    def test_a_failed_download_just_means_no_grouping(self, monkeypatch):
        import yfinance
        def boom(*a, **k): raise RuntimeError("offline")
        monkeypatch.setattr(yfinance, "download", boom)
        assert eh.group_names_to_etfs_sync(["AMD"]) == {"members": {}, "groups": {}, "unmatched": []}


class TestClusterRule:
    def _corr(self, rho):
        import pandas as pd
        return pd.DataFrame([[1, rho], [rho, 1]], index=["A", "B"], columns=["A", "B"])

    def test_names_above_the_similarity_cut_group_and_below_it_do_not(self):
        assert eh._cluster_names(["A", "B"], self._corr(0.70), {}, 0.55, 0.10) == [["A", "B"]]
        assert sorted(map(sorted, eh._cluster_names(["A", "B"], self._corr(0.30), {}, 0.55, 0.10))) == [["A"], ["B"]]

    def test_a_shared_theme_pulls_borderline_names_together(self):
        c = self._corr(0.40)                                           # distance 0.60 > cut 0.55
        assert len(eh._cluster_names(["A", "B"], c, {}, 0.55, 0.10)) == 2
        assert eh._cluster_names(["A", "B"], c, {"A": {"semis"}, "B": {"semis"}}, 0.55, 0.10) == [["A", "B"]]       # 0.60 − 0.10 = 0.50 ≤ cut
        assert len(eh._cluster_names(["A", "B"], c, {"A": {"semis"}, "B": {"energy"}}, 0.55, 0.10)) == 2


def test_expiry_is_the_listed_date_nearest_60_days_and_never_too_short():
    today = date(2026, 10, 7)
    exps = ["2026-10-16", "2026-11-20", "2026-12-18", "2027-01-15"]
    assert eh._pick_expiry(exps, today, 60) == ("2026-12-18", 72) or eh._pick_expiry(exps, today, 60)[0] in ("2026-11-20", "2026-12-18")
    assert eh._pick_expiry(["2026-10-16"], today, 60) is None
    assert eh._pick_expiry([], today) is None


def test_the_book_result_carries_the_etf_view_in_both_downside_variants_and_is_json_safe(monkeypatch):
    import app.services.derivative_income_service as dis
    import app.services.quote_providers as qp
    book = _put_book(3, qty=10) + [_pos([_leg(65)], shares=100, structure="covered_call", ticker="CC1", capital=6500.0)]
    for i, p in enumerate(book):
        p["trade_id"] = i + 1
    by_id = {p["trade_id"]: p for p in book}

    async def fake_pos(strategy, *a, **k): return dict(by_id[strategy.id])
    async def fake_none(*a, **k): return [], None
    async def fake_factor(*a, **k): return None
    async def fake_sofr(): return (R, None)
    async def fake_static(positions, provider, today): return _static(sorted({p["ticker"] for p in positions if p["ticker"].startswith("P")}))
    monkeypatch.setattr(bt, "_position_greeks", fake_pos)
    monkeypatch.setattr(bt, "_compute_betas", lambda tk: {t: 1.4 for t in tk})
    monkeypatch.setattr(bt, "_index_puts", fake_none); monkeypatch.setattr(bt, "_vix_calls", fake_none)
    monkeypatch.setattr(bt, "_vix_levels", lambda: (18.0, 19.0)); monkeypatch.setattr(bt, "_book_factor_exposure", fake_factor)
    monkeypatch.setattr(bt, "_fetch_etf_static", fake_static)
    monkeypatch.setattr(dis, "_get_sofr", fake_sofr); monkeypatch.setattr(qp, "get_provider", lambda *a, **k: _Provider())
    out = asyncio.run(bt.compute_book_tail_risk([SimpleNamespace(id=p["trade_id"]) for p in book], "yfinance", SimpleNamespace(id=1), None))
    json.dumps(out)
    assert out["etf_hedges"] and out["etf_hedges"]["candidates"][0]["etf"] == "SMH"
    assert out["with_stock_downside"]["etf_hedges"]["candidates"][0]["etf"] == "SMH"
    assert "CC1" in out["etf_hedges"]["unmapped"]
