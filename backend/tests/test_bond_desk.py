"""Bond Desk — portfolio engine, ladders, planning and router (offline: market inputs are faked)."""
import asyncio
import json
from datetime import date
from types import SimpleNamespace

import pytest

from app.services import bond_ladder_service as L
from app.services import bond_market_service as mkt
from app.services import bond_math as bm
from app.services import bond_portfolio_service as ps

SETTLE = date(2026, 9, 29)
NOM = [(1 / 12, 0.040), (0.25, 0.0428), (0.5, 0.0441), (1, 0.0459), (2, 0.0492), (3, 0.0501), (5, 0.0506),
       (7, 0.0515), (10, 0.0524), (20, 0.056), (30, 0.0556)]
REAL = [(5, 0.0273), (7, 0.028), (10, 0.029), (20, 0.0314), (30, 0.0328)]
SPREADS = {"by_rating": {k: {"oas_bp": v} for k, v in
                         {"AAA": 41, "AA": 59, "A": 69, "BBB": 99, "BB": 176, "B": 300, "CCC": 1128, "IG": 81, "HY": 293}.items()},
           "ig_buckets": [{"mid_years": 2.0, "oas_bp": 51}, {"mid_years": 6.0, "oas_bp": 84}, {"mid_years": 12.5, "oas_bp": 100}]}
MI = {"nominal": {"date": SETTLE, "points": NOM}, "real": {"date": SETTLE, "points": REAL}, "spreads": SPREADS,
      "muni_ratio": {"points": [(1.0, 0.656), (5.0, 0.643), (10.0, 0.688), (20.0, 0.823), (30.0, 0.888)]},
      "tips_points": []}
BND = {"ticker": "BND", "name": "Vanguard Total Bond Market", "price": 70.0, "distribution_yield_pct": 4.0,
       "duration": 6.0, "expense_ratio_pct": 0.03, "credit_mix": {"us_government": 0.5, "aaa": 0.03, "aa": 0.72, "a": 0.12, "bbb": 0.12}}
PROFILE = {"federal_rate": 32, "state": "NY", "state_rate": 6.85, "niit": True, "ltcg_rate": 15}


def _ctx(**kw):
    base = dict(settle=SETTLE, mi=MI, catalogue={}, funds={"BND": BND}, purchase_curves={}, cpi={},
                inflation=0.025, rate_context={}, catalogue_as_of=None, etf_maturity={"IBTJ": 2029})
    base.update(kw)
    return ps.Ctx(**base)


BOOK = [
    {"id": 1, "kind": "treasury", "label": "UST 4.375 2028", "face_value": 50000, "coupon_rate": 4.375, "coupon_freq": 2,
     "maturity_date": "2028-08-31", "current_price": 99.0, "purchase_price": 101.2, "purchase_date": "2024-01-10"},
    {"id": 2, "kind": "muni", "label": "NYC GO 5% c29", "issuer": "NYC", "state": "NY", "face_value": 25000, "coupon_rate": 5.0,
     "coupon_freq": 2, "maturity_date": "2034-08-01", "call_date": "2029-08-01", "call_price": 100, "current_price": 104.0},
    {"id": 3, "kind": "corporate", "label": "AAPL 4.1 2030", "issuer": "Apple", "face_value": 30000, "coupon_rate": 4.1,
     "coupon_freq": 2, "maturity_date": "2030-05-15", "current_price": 96.0, "rating": "AA+"},
    {"id": 4, "kind": "cd", "label": "Ally 12m", "issuer": "Ally Bank", "face_value": 200000, "coupon_rate": 4.2, "coupon_freq": 0,
     "issue_date": "2026-03-01", "maturity_date": "2027-03-01"},
    {"id": 5, "kind": "cd", "label": "Ally brokered", "issuer": "Ally Bank", "face_value": 100000, "coupon_rate": 4.5,
     "coupon_freq": 12, "maturity_date": "2028-06-30", "current_price": 99.0},
    {"id": 6, "kind": "etf", "ticker": "BND", "quantity": 500, "purchase_price": 72.0, "account_type": "ira"},
    {"id": 7, "kind": "muni", "label": "CA GO in IRA", "state": "CA", "face_value": 20000, "coupon_rate": 3.0, "coupon_freq": 2,
     "maturity_date": "2031-10-01", "current_price": 97.0, "account_type": "ira"},
    {"id": 8, "kind": "tips", "label": "TIPS 2036", "face_value": 40000, "coupon_rate": 2.375, "coupon_freq": 2,
     "maturity_date": "2036-07-15", "current_price": 96.0, "tips_ref_cpi": 333.96974},
    {"id": 9, "kind": "treasury", "label": "matured note", "face_value": 10000, "coupon_rate": 2.0, "coupon_freq": 2,
     "maturity_date": "2026-08-15"},
]


def _analyze(book=BOOK, profile=PROFILE, ctx=None):
    ctx = ctx or _ctx(cpi={(2026, 6): 334.0, (2026, 7): 334.5})
    rows, internals = ps.analyze_rows(book, profile, ctx)
    agg = ps.aggregate(rows, internals)
    return rows, internals, agg, ctx


# ---------------------------------------------------------------------------
# Tax treatment
# ---------------------------------------------------------------------------
def test_tax_treatment_defaults_and_overrides():
    tr, _ = ps.tax_treatment({"kind": "treasury"}, PROFILE)
    assert tr.fed_taxable and not tr.state_taxable
    tr, _ = ps.tax_treatment({"kind": "muni", "state": "NY"}, PROFILE)
    assert not tr.fed_taxable and not tr.state_taxable
    tr, notes = ps.tax_treatment({"kind": "muni", "state": "CA"}, PROFILE)
    assert not tr.fed_taxable and tr.state_taxable and "Out-of-state" in notes[0]
    tr, _ = ps.tax_treatment({"kind": "corporate", "account_type": "ira"}, PROFILE)
    assert not tr.taxable_account and bm.interest_tax_rate(ps.tax_rates(PROFILE), tr) == 0
    tr, _ = ps.tax_treatment({"kind": "agency", "state_taxable": False}, PROFILE)   # FHLB override
    assert tr.fed_taxable and not tr.state_taxable


def test_fund_tax_class_handles_yfinance_abbreviations():
    assert ps.fund_tax_class({"name": "Vanguard Interm-Term Tx-Ex Adm"}, {}) == "muni"
    assert ps.fund_tax_class({"name": "iShares 0-5 Year TIPS Bond ETF"}, {}) == "treasury"
    assert ps.fund_tax_class({"name": "iShares GNMA Bond ETF", "category": "Intermediate Government"}, {}) == "taxable"


# ---------------------------------------------------------------------------
# Portfolio engine
# ---------------------------------------------------------------------------
def test_portfolio_reconciles_dv01_value_and_cash_flows():
    rows, internals, agg, ctx = _analyze()
    live = [r for r in rows if r["status"] == "held" and not r.get("matured")]
    assert agg["summary"]["market_value"] == pytest.approx(sum(r["market_value"] for r in live), abs=0.05)
    assert agg["summary"]["dv01"] == pytest.approx(sum(r["dv01"] for r in live), abs=0.05)
    assert sum(k["dv01"] for k in agg["key_rate_dv01"]) == pytest.approx(agg["summary"]["dv01"], abs=0.05)
    cash = ps.project_cash_flows(rows, internals, SETTLE, years=15, inflation=ctx.inflation)
    assert sum(y["total"] for y in cash["yearly"]) == pytest.approx(sum(e["amount"] for e in cash["events"]), abs=0.5)
    # allocations each sum to the whole book
    for key in ("by_kind", "by_credit", "by_maturity", "by_tax"):
        assert sum(x["value"] for x in agg["allocation"][key]) == pytest.approx(agg["summary"]["market_value"], abs=1.0)


def test_scenarios_are_convex_and_signed():
    rows, internals, agg, _ = _analyze()
    sc = ps.scenarios(rows, internals, SETTLE, agg["key_rate_dv01"])
    by = {s["shift_bp"]: s["pnl"] for s in sc["parallel"]}
    assert by[-100] > 0 > by[100]
    assert by[-100] > -by[100]                    # positive convexity on the whole book
    assert abs(by[100] + agg["summary"]["dv01"] * 100) / abs(by[100]) < 0.1   # ≈ DV01 × 100


def test_callable_premium_muni_priced_to_call_and_bank_cd_accrues():
    rows, _, _, _ = _analyze()
    muni = next(r for r in rows if r["id"] == 2)
    assert muni["ytw_kind"] == "call" and muni["likely_called"]
    assert muni["tax"]["after_tax_yield_pct"] == pytest.approx(muni["ytw_pct"], abs=0.02)   # tax-free in-state
    cd = next(r for r in rows if r["id"] == 4)
    elapsed = bm.year_frac(date(2026, 3, 1), SETTLE)
    assert cd["market_value"] == pytest.approx(200000 * 1.042 ** elapsed, abs=0.05)
    assert cd["dv01"] == 0 and cd["no_mark_to_market"]


def test_tips_value_uses_index_ratio():
    rows, _, _, _ = _analyze()
    t = next(r for r in rows if r["id"] == 8)
    ir = t["tips"]["index_ratio"]
    assert ir > 1.0
    assert t["market_value"] == pytest.approx(40000 * ir * t["dirty_price"] / 100, rel=1e-6)


def test_recommendations_fire_on_real_problems():
    rows, internals, agg, ctx = _analyze()
    recs = ps.recommendations(rows, agg, PROFILE, ctx, ps.tax_rates(PROFILE))
    ids = {r["id"].split("-")[0] for r in recs}
    assert "fdic" in ids                       # 200k + 100k at Ally
    fdic = next(r for r in recs if r["id"].startswith("fdic"))
    assert fdic["impact_usd"] > 50000
    assert "matured" in ids                    # stale matured note
    assert "tips" in ids                       # TIPS in taxable → phantom income
    assert "muni" in ids                       # muni inside an IRA
    assert recs[0]["severity"] == "high"       # sorted by severity


def test_estimated_mark_holds_purchase_spread():
    """A corporate bought at +80bp over the curve keeps +80bp over TODAY's curve at its workout tenor."""
    pd_ = date(2025, 6, 30)
    old_curve = [(t, y - 0.01) for t, y in NOM]              # curve was 100bp lower at purchase
    spec = bm.BondSpec(maturity=date(2030, 6, 30), coupon=0.045, freq=2)
    y0 = bm.interp(old_curve, bm.year_frac(pd_, spec.maturity)) + 0.008
    pp = bm.clean_from_yield(spec, pd_, y0)
    h = {"id": 1, "kind": "corporate", "face_value": 10000, "coupon_rate": 4.5, "coupon_freq": 2,
         "maturity_date": "2030-06-30", "purchase_date": pd_.isoformat(), "purchase_price": pp}
    ctx = _ctx(purchase_curves={("nominal", pd_): old_curve})
    rows, _ = ps.analyze_rows([h], PROFILE, ctx)
    r = rows[0]
    expect = bm.interp(NOM, bm.year_frac(SETTLE, spec.maturity)) + 0.008
    assert r["estimated_mark"] and "purchase spread (+80bp)" in r["price_source"]
    assert r["ytw_pct"] == pytest.approx(expect * 100, abs=0.01)


def test_after_tax_menu_is_bracket_and_account_aware():
    hi = {"federal_rate": 37, "state": "NY", "state_rate": 10.9, "niit": True}
    menu = ps.after_tax_menu(MI, hi, [5.0], kinds={"treasury", "muni", "cd"})
    assert menu[0]["best"]["kind"] == "muni"
    ira = ps.after_tax_menu(MI, hi, [5.0], account="ira", kinds={"treasury", "muni", "cd"})
    assert ira[0]["best"]["kind"] != "muni"


# ---------------------------------------------------------------------------
# Market helpers
# ---------------------------------------------------------------------------
def test_parse_figi_ticker_and_rating_bucket():
    assert mkt.parse_figi_ticker("AAPL 2.4 05/03/23") == {"coupon_pct": 2.4, "maturity": "2023-05-03"}
    assert mkt.parse_figi_ticker("CA CAS 2.4 10/01/2024") == {"coupon_pct": 2.4, "maturity": "2024-10-01", "state": "CA"}
    assert mkt.parse_figi_ticker("XITII 2 3/8 07/15/36")["coupon_pct"] == 2.375
    assert mkt.rating_bucket("Aa2") == "AA" and mkt.rating_bucket("BBB-") == "BBB"
    assert mkt.rating_bucket("Baa1") == "BBB" and mkt.rating_bucket("NR") is None


def test_model_yield_by_kind():
    tsy, _ = mkt.model_yield("treasury", 5, MI)
    muni, _ = mkt.model_yield("muni", 5, MI)
    corp_a, _ = mkt.model_yield("corporate", 5, MI, "A")
    corp_bbb, _ = mkt.model_yield("corporate", 5, MI, "BBB")
    assert muni < tsy < corp_a < corp_bbb


# ---------------------------------------------------------------------------
# Ladders
# ---------------------------------------------------------------------------
def _tips_row(year, month, cpn, price=98.0, ry=2.8):
    return {"cusip": f"T{year}{month:02d}", "maturity": f"{year}-{month:02d}-15", "coupon_pct": cpn, "price": price,
            "accrued": 0.3, "real_yield_pct": ry, "index_ratio": 1.2, "type": "tips", "years": year - 2026}


def test_tips_ladder_math_level_real_income_and_gap_hedge():
    rows = [_tips_row(y, 1, 1.0 + 0.1 * (y - 2027)) for y in range(2027, 2037)] + \
           [_tips_row(y, 2, 2.0) for y in range(2040, 2046)]
    res = L.tips_ladder_math(rows, 2027, 2045, 10000.0)
    prin = {r["year"]: r["real_principal"] for r in res["rungs"]}
    extra = {r["year"]: r["gap_hedge_principal"] for r in res["rungs"]}
    cpn = {r["year"]: r["coupon_pct"] / 100 for r in res["rungs"]}
    for y in [y for y in range(2027, 2046) if y in prin]:
        later = sum((prin[j] + extra[j]) * cpn[j] for j in prin if j > y)
        own = prin[y] * (1 + cpn[y] * 1 / 2)                     # Jan/Feb maturities → one coupon that year
        assert own + later == pytest.approx(10000.0, rel=1e-9)
    gaps = {g["year"]: g for g in res["gaps"]}
    assert set(gaps) == {2037, 2038, 2039}
    assert all(sum(g["split"].values()) == pytest.approx(1.0) for g in gaps.values())
    assert gaps[2037]["split"][2036] > gaps[2039]["split"][2036]   # nearer the lower bracket → more of it


def test_rolling_simulation_flat_curve_is_flat():
    flat = [(t, 0.05) for t in (0.5, 1, 2, 5, 10, 30)]
    rungs = [{"face": 10000.0, "coupon": 0.05, "t": float(k)} for k in range(1, 6)]
    sim = L.simulate_rolling(rungs, flat, years=8, ladder_length=5.0)
    inc = [p["income"] for p in sim["scenarios"]["flat"]]
    assert max(inc) - min(inc) < 1.0
    up = [p["income"] for p in sim["scenarios"]["up100"]]
    assert up[-1] > inc[-1]


def test_ladder_status_separates_bought_from_planned():
    plan = {"rungs": [{"index": 1, "maturity": "2027-09-15", "cusip": "A", "face": 10000},
                      {"index": 2, "maturity": "2028-09-15", "cusip": "B", "face": 10000},
                      {"index": 3, "maturity": "2029-09-15", "cusip": "C", "face": 10000}]}
    rows = [{"id": 1, "cusip": "A", "face": 10000, "status": "held"},
            {"id": 2, "cusip": "B", "face": 10000, "status": "watch"}]
    st = L.ladder_status(plan, rows, today=SETTLE)
    assert [r["status"] for r in st["rungs"]] == ["funded", "planned", "missing"]
    assert st["funded_pct"] == pytest.approx(33.3, abs=0.1)


@pytest.fixture
def fake_market(monkeypatch):
    async def market_inputs():
        return MI

    async def none(*a, **k):
        return None

    async def rate_context():
        return {"be_5y": {"value_pct": 2.3}}

    async def fund_profile(t):
        return BND if t.upper() == "BND" else None

    async def etf_rungs(fam):
        return []

    async def cpi_monthly():
        return {}

    monkeypatch.setattr(mkt, "market_inputs", market_inputs)
    monkeypatch.setattr(mkt, "treasury_catalogue", none)
    monkeypatch.setattr(mkt, "tips_catalogue", none)
    monkeypatch.setattr(mkt, "rate_context", rate_context)
    monkeypatch.setattr(mkt, "fund_profile", fund_profile)
    monkeypatch.setattr(mkt, "fund_profile_full", fund_profile)
    monkeypatch.setattr(mkt, "etf_rungs", etf_rungs)
    monkeypatch.setattr(mkt, "cpi_monthly", cpi_monthly)
    monkeypatch.setattr(mkt, "curve_on", none)

    async def no_ref():
        return {}
    monkeypatch.setattr(mkt, "treasury_reference", no_ref)


def test_level_income_ladder_pays_level_cash(fake_market):
    lad = asyncio.run(L.build_ladder({"amount": 500000, "start_years": 2, "end_years": 10, "instrument": "agency",
                                      "weighting": "level_income"}, PROFILE, today=SETTLE))
    assert lad["summary"]["rungs"] == 9
    assert lad["summary"]["invested"] <= 500000 + 1e-6
    ys = {y["year"]: y["total"] for y in lad["cash_flow"]["yearly"]}
    mid = [ys[y] for y in range(2029, 2036)]          # full calendar years (first rung pays in 2028)
    assert (max(mid) - min(mid)) / max(mid) < 0.03


def test_best_after_tax_ladder_picks_per_rung(fake_market):
    lad = asyncio.run(L.build_ladder({"amount": 100000, "start_years": 1, "end_years": 5,
                                      "instrument": "best_after_tax"}, {**PROFILE, "federal_rate": 37}, today=SETTLE))
    assert all(r["alternatives"] for r in lad["rungs"])
    for r in lad["rungs"]:
        assert r["after_tax_pct"] >= max(a["after_tax_pct"] for a in r["alternatives"]) - 0.05


# ---------------------------------------------------------------------------
# Router (real handlers, SQLite, faked market)
# ---------------------------------------------------------------------------
def test_router_crud_ladder_adopt_and_unlink(fake_market, tmp_path):
    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

    from app import models
    from app.database import Base
    from app.routers import bond_router as R

    async def run():
        eng = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'b.db'}")
        async with eng.begin() as c:
            await c.run_sync(Base.metadata.create_all)
        S = async_sessionmaker(eng, class_=AsyncSession, expire_on_commit=False)
        user = SimpleNamespace(id=1)
        async with S() as db:
            db.add(models.User(id=1, google_id="g", email="a@b.c", name="A"))
            await db.commit()
            await R.put_profile(R.ProfileIn(federal_rate=32, state="NY", state_rate=6.85), user=user, db=db)
            h = await R.create_holding(R.HoldingIn(kind="etf", ticker="bnd", quantity=10), user=user, db=db)
            assert h["ticker"] == "BND"
            with pytest.raises(Exception):
                await R.create_holding(R.HoldingIn(kind="bogus"), user=user, db=db)
            lad = await R.build({"amount": 50000, "start_years": 1, "end_years": 3, "instrument": "cd"}, user=user, db=db)
            saved = await R.save_ladder(R.LadderSaveIn(name="CDs", params=lad["params"],
                                                       plan=json.loads(json.dumps(lad, default=str))), user=user, db=db)
            ad = await R.adopt_ladder(saved["id"], R.AdoptIn(status="watch"), user=user, db=db)
            assert ad["created"] == 3
            got = await R.get_ladder(saved["id"], user=user, db=db)
            assert [r["status"] for r in got["status_detail"]["rungs"]] == ["planned"] * 3
            port = await R.portfolio(user=user, db=db)
            assert port["summary"]["positions"] == 1 and port["summary"]["watchlist"] == 3
            out = await R.delete_ladder(saved["id"], delete_planned=True, user=user, db=db)
            assert out["deleted_planned"] == 3
            assert len(await R.list_holdings(user=user, db=db)) == 1
        await eng.dispose()

    asyncio.run(run())


# ---------------------------------------------------------------------------
# Broker-statement mapping (Quantity = face, cost-basis TOTAL, TIPS index ratio at purchase)
# ---------------------------------------------------------------------------
def _cpi(start=(2024, 1), months=34, base=310.0, step=0.7):
    out, (y, m) = {}, start
    for i in range(months):
        out[(y, m)] = base + step * i
        m += 1
        if m == 13:
            y, m = y + 1, 1
    return out


TIPS_ROW = {"id": 1, "kind": "tips", "face_value": 25000, "coupon_rate": 1.625, "coupon_freq": 2,
            "maturity_date": "2030-04-15", "tips_ref_cpi": 318.0, "current_price": 96.3125,
            "cost_basis": 26551.79}


def test_tips_cost_basis_total_is_authoritative_and_implies_real_price():
    ctx = _ctx(cpi=_cpi())
    rows, _ = ps.analyze_rows([{**TIPS_ROW, "purchase_date": "2025-06-02"}], PROFILE, ctx)
    r = rows[0]
    ir_buy = r["tips"]["index_ratio_at_purchase"]
    assert ir_buy == pytest.approx(bm.index_ratio(date(2025, 6, 2), 318.0, ctx.cpi))
    assert r["cost_basis"] == pytest.approx(26551.79)
    assert r["purchase_price_derived"] is True
    # the derived REAL price reconciles back to the broker's total through the index ratio AT PURCHASE
    assert 25000 * r["purchase_price"] / 100 * ir_buy == pytest.approx(26551.79, abs=0.05)
    # value = face × index ratio today × real price ÷ 100 (clean, like a statement); P&L vs the broker total
    assert r["clean_value"] == pytest.approx(25000 * r["tips"]["index_ratio"] * 96.3125 / 100, abs=0.01)
    assert r["unrealized_pnl"] == pytest.approx(r["clean_value"] - 26551.79, abs=0.01)


def test_tips_without_purchase_date_still_uses_broker_total():
    rows, _ = ps.analyze_rows([TIPS_ROW], PROFILE, _ctx(cpi=_cpi()))
    r = rows[0]
    assert r["cost_basis"] == pytest.approx(26551.79) and r["purchase_price"] is None
    assert r["unrealized_pnl"] == pytest.approx(r["clean_value"] - 26551.79, abs=0.01)


def test_tips_cost_from_real_price_uses_index_ratio_at_purchase_not_today():
    h = {**TIPS_ROW, "cost_basis": None, "purchase_price": 98.0, "purchase_date": "2024-09-03"}
    rows, _ = ps.analyze_rows([h], PROFILE, _ctx(cpi=_cpi()))
    r = rows[0]
    ir_buy, ir_now = r["tips"]["index_ratio_at_purchase"], r["tips"]["index_ratio"]
    assert ir_buy < ir_now
    assert r["cost_basis"] == pytest.approx(25000 * 0.98 * ir_buy, abs=0.05)


def test_nominal_bond_price_per_100_derived_from_total():
    h = {"id": 1, "kind": "corporate", "face_value": 30000, "coupon_rate": 4.1, "coupon_freq": 2,
         "maturity_date": "2030-05-15", "current_price": 96.0, "cost_basis": 28800.0}
    r = ps.analyze_rows([h], PROFILE, _ctx())[0][0]
    assert r["purchase_price"] == pytest.approx(96.0) and r["purchase_price_derived"]
    assert r["accrued_usd"] == pytest.approx(30000 * r["accrued"] / 100, abs=0.01)


def test_preview_endpoint_reconciles_with_broker_value(fake_market, monkeypatch):
    from app.routers import bond_router as R

    async def cpi():
        return _cpi()
    monkeypatch.setattr(mkt, "cpi_monthly", cpi)

    class _Res:
        def scalar_one_or_none(self):
            return None

    class _DB:
        async def execute(self, *a, **k):
            return _Res()

    user = SimpleNamespace(id=1)
    half = asyncio.run(R.preview_holding(R.PreviewIn(holding={"kind": "tips", "face_value": 25000}), user=user, db=_DB()))
    assert half["ready"] is False and "Maturity" in half["missing"]
    out = asyncio.run(R.preview_holding(R.PreviewIn(holding={k: v for k, v in TIPS_ROW.items() if k != "id"},
                                                    broker_value=25265.21), user=user, db=_DB()))
    rec, row = out["reconciliation"], out["row"]
    assert rec["diff"] == pytest.approx(row["clean_value"] - 25265.21, abs=0.01)
    assert rec["implied_price"] == pytest.approx(25265.21 / (25000 * row["tips"]["index_ratio"]) * 100, abs=1e-3)


# ---------------------------------------------------------------------------
# FedInvest date handling + Treasury lookup that doesn't depend on FedInvest
# (regression: container on UTC asked FedInvest for an unpublished date → HTTP 200 form → whole
#  catalogue failed → TIPS CUSIP looked up as "agency" and index ratio fell back to 1.0)
# ---------------------------------------------------------------------------
import httpx


def test_fedinvest_unpublished_date_is_empty_not_an_error(monkeypatch):
    def handler(req):
        if req.method == "GET":
            return httpx.Response(200, text='<input type="hidden" name="_csrf" value="tok123" />')
        return httpx.Response(200, text="<form>no prices for that date</form>")   # form re-shown, no 302
    real = httpx.Client
    monkeypatch.setattr(mkt.httpx, "Client", lambda **kw: real(transport=httpx.MockTransport(handler), **kw))
    assert mkt._fedinvest_sync(date(2026, 9, 30)) == []


def test_treasury_prices_walks_back_from_new_york_today(monkeypatch):
    seen = []

    def fake_sync(d):
        seen.append(d)
        return [{"cusip": "X"}] if d == date(2026, 9, 28) else []
    monkeypatch.setattr(mkt, "us_today", lambda: date(2026, 9, 30))
    monkeypatch.setattr(mkt, "_fedinvest_sync", fake_sync)
    mkt._memo.pop("fedinvest", None)
    mkt._neg.pop("fedinvest", None)
    out = asyncio.run(mkt.treasury_prices())
    mkt._memo.pop("fedinvest", None)
    assert out["as_of"] == "2026-09-28" and seen == [date(2026, 9, 30), date(2026, 9, 29), date(2026, 9, 28)]


def test_negative_cache_stops_hammering_a_down_source():
    calls = []

    async def boom():
        calls.append(1)
        raise RuntimeError("down")
    key = "test:neg"
    mkt._memo.pop(key, None)
    mkt._neg.pop(key, None)
    assert asyncio.run(mkt._memoized(key, 60, boom)) is None
    assert asyncio.run(mkt._memoized(key, 60, boom)) is None
    assert len(calls) == 1
    mkt._neg.pop(key, None)


def test_tips_cusip_lookup_uses_fiscaldata_when_prices_unavailable(monkeypatch):
    async def none(*a, **k):
        return None

    async def ref():
        return {"91282CNB3": {"cusip": "91282CNB3", "tips": True, "frn": False, "coupon_pct": 1.625,
                              "maturity": "2030-04-15", "dated_date": "2025-04-15", "ref_cpi": 318.0}}

    async def cpi():
        return _cpi(start=(2024, 1), months=40)

    async def be(*a, **k):
        return ("2026-09-28", 2.3)
    monkeypatch.setattr(mkt, "treasury_catalogue", none)
    monkeypatch.setattr(mkt, "treasury_reference", ref)
    monkeypatch.setattr(mkt, "cpi_monthly", cpi)
    monkeypatch.setattr(mkt, "fred_latest", be)
    monkeypatch.setattr(mkt, "us_today", lambda: date(2026, 9, 30))
    r = asyncio.run(mkt.lookup_cusip("91282cnb3"))
    assert r["kind"] == "tips" and r["tips_ref_cpi"] == 318.0 and r["index_ratio"] > 1.0
    assert r["coupon_pct"] == 1.625 and r["maturity"] == "2030-04-15" and "FiscalData" in r["source"]


@pytest.mark.parametrize("cusip,ticker,kind,freq", [
    ("912797VK0", "B 0 10/06/26", "treasury", 0),
    ("91282CZZ9", "TII 1 5/8 04/15/30", "tips", 2),
    ("3130AXYZ1", "FHLB 4.5 01/15/29", "agency", 2),
])
def test_openfigi_never_calls_a_treasury_an_agency(monkeypatch, cusip, ticker, kind, freq):
    async def none(*a, **k):
        return None

    async def empty():
        return {}

    def handler(req):
        return httpx.Response(200, json=[{"data": [{"marketSector": "Govt", "ticker": ticker, "name": "ISSUER"}]}])
    real = httpx.AsyncClient
    monkeypatch.setattr(mkt, "treasury_catalogue", none)
    monkeypatch.setattr(mkt, "treasury_reference", empty)
    monkeypatch.setattr(mkt.httpx, "AsyncClient", lambda **kw: real(transport=httpx.MockTransport(handler), **kw))
    r = asyncio.run(mkt.lookup_cusip(cusip))
    assert r["kind"] == kind and r["coupon_freq"] == freq


def test_tips_ref_cpi_comes_from_fiscaldata_when_not_priced():
    h = {k: v for k, v in TIPS_ROW.items() if k != "tips_ref_cpi"}
    h["cusip"] = "91282CNB3"
    ctx = _ctx(cpi=_cpi(), reference={"91282CNB3": {"ref_cpi": 318.0, "dated_date": "2025-04-15"}})
    r = ps.analyze_rows([h], PROFILE, ctx)[0][0]
    assert r["tips"]["ref_cpi"] == 318.0 and r["tips"]["index_ratio"] > 1.0
    assert not any("reference CPI" in w for w in r["warnings"])


# ---------------------------------------------------------------------------
# Goal planner: selling bond ETFs / mutual funds down to fill gap years
# ---------------------------------------------------------------------------
TODAY = date(2026, 9, 30)


def _fund(fid, dur, *, acct="taxable", gain=0.0, value=100000.0, y=0.04, dist_tax=0.0):
    row = {"id": fid, "label": f"F{fid}", "ticker": f"F{fid}", "account_type": acct, "market_value": value,
           "cost_basis": value / (1 + gain) if gain else value, "ytw_pct": y * 100, "eff_duration": dur,
           "tax": {"rate_pct": dist_tax * 100}, "fund": {"expense_ratio_pct": 0.05}}
    return L.fund_sale_profile(row, PROFILE)


def _sim(funds, need, *, bonds=None, after_tax=True, use=True, years=range(2026, 2041)):
    return L.simulate_goal_funding(list(years), TODAY, bonds or {}, need, funds, use_funds=use,
                                   after_tax=after_tax, carry_rate=0.0, plan_end=max(years))


def test_fund_sold_is_the_one_whose_duration_matches_the_gap_year():
    # zero-yield funds so accumulated distributions don't pre-fund the later gap (isolates the choice rule)
    short, long_ = _fund(1, 2.0, y=0.0), _fund(2, 12.0, y=0.0)
    out = _sim([short, long_], {2028: 60000, 2038: 60000}, after_tax=False)
    by_year = {}
    for s in out["schedule"]:
        by_year.setdefault(s["year"], set()).add(s["holding_id"])
    assert by_year[2028] == {1} and by_year[2038] == {2}


def test_loss_position_sold_before_gain_position_and_tax_nets_out():
    gainer, loser = _fund(1, 5.0, gain=0.30), _fund(2, 5.0, gain=-0.10)
    out = _sim([gainer, loser], {2027: 20000})
    first = out["schedule"][0]
    assert first["holding_id"] == 2 and first["tax"] < 0          # a loss → tax saved
    assert sum(s["net"] for s in out["schedule"] if s["year"] == 2027) + \
        next(r for r in out["rows"] if r["year"] == 2027)["fund_distributions"] + \
        next(r for r in out["rows"] if r["year"] == 2026)["surplus_carried"] == pytest.approx(20000, abs=0.05)


def test_ira_withdrawal_is_grossed_up_for_ordinary_income_tax():
    ira = _fund(1, 3.0, acct="ira")
    out = _sim([ira], {2027: 30000}, years=range(2027, 2030))       # no carry from 2026 distributions
    rates = ps.tax_rates(PROFILE)
    s = out["schedule"][0]
    assert s["net"] == pytest.approx(s["gross"] * (1 - rates.fed - rates.state), abs=0.05)


def test_taxable_money_spent_before_tax_free_roth_money():
    taxable, roth = _fund(1, 4.0), _fund(2, 4.0, acct="roth")
    out = _sim([roth, taxable], {2029: 30000})        # Roth listed FIRST: only the shelter cost can pick taxable
    assert out["schedule"][0]["holding_id"] == 1


def test_fund_capacity_spills_and_distributions_shrink_after_sales():
    small = _fund(1, 3.0, value=20000, y=0.05)
    out = _sim([small], {2027: 50000, 2028: 1}, after_tax=False, years=range(2027, 2030))
    r27, r28 = (next(r for r in out["rows"] if r["year"] == y) for y in (2027, 2028))
    assert out["remaining"][1] == pytest.approx(0.0, abs=0.01)
    assert r27["shortfall"] > 0 and r28["fund_distributions"] == pytest.approx(0.0, abs=0.01)


def test_each_year_reconciles_and_holding_funds_sells_nothing():
    f = [_fund(1, 2.0), _fund(2, 9.0, gain=0.2)]
    need = {2027: 40000, 2030: 40000, 2034: 40000}
    out = _sim(f, need, bonds={2027: 10000, 2031: 5000})
    carry = 0.0
    for r in out["rows"]:
        sold = sum(s["net"] for s in out["schedule"] if s["year"] == r["year"])
        assert r["fund_sales"] == pytest.approx(sold, abs=0.05)
        avail = r["bond_inflow"] + r["fund_distributions"] + carry + r["fund_sales"]
        assert r["covered"] == pytest.approx(min(avail, r["need"]), abs=0.05)
        carry = r["surplus_carried"]
    held = _sim(f, need, bonds={2027: 10000, 2031: 5000}, use=False)
    assert held["schedule"] == [] and sum(r["shortfall"] for r in held["rows"]) > sum(r["shortfall"] for r in out["rows"])


def test_plan_goals_uses_funds_to_close_gaps(fake_market):
    prof = {**PROFILE, "goals": [{"name": "College", "year": 2028, "end_year": 2031, "amount": 30000, "inflation_adjusted": False}]}
    book = [{"id": 1, "kind": "etf", "ticker": "BND", "quantity": 1500, "purchase_price": 72.0, "account_type": "taxable"},
            {"id": 2, "kind": "treasury", "face_value": 20000, "coupon_rate": 4.0, "coupon_freq": 2,
             "maturity_date": "2028-06-30", "current_price": 99.5}]
    with_f = asyncio.run(L.plan_goals(book, prof, use_funds=True))
    without = asyncio.run(L.plan_goals(book, prof, use_funds=False))
    assert with_f["funds"]["schedule"] and with_f["funded_ratio_pct"] > without["funded_ratio_pct"]
    assert with_f["funds"]["before"]["funded_ratio_pct"] == without["funded_ratio_pct"]
    assert with_f["funds"]["lock_in"]["treasury_cost_today"] > 0


def test_optimizer_matches_long_fund_to_late_gap_where_greedy_would_not():
    """Both gaps are beyond both durations. Greedy gives the first gap (8.8y out) the 15y fund because it's
    marginally closer (6.2 vs 6.8), leaving the 13.8y gap the 2y fund (11.8y mismatch). The LP must pair
    short→early and long→late (total mismatch 6.8 + 1.2 instead of 6.2 + 11.8)."""
    short, long_ = _fund(1, 2.0, y=0.0), _fund(2, 15.0, y=0.0)
    need = {2035: 100000, 2040: 100000}
    greedy = _sim([short, long_], need, after_tax=False)
    assert {s["year"]: s["holding_id"] for s in greedy["schedule"]}[2035] == 2           # the myopic choice
    plan = L.solve_fund_sales(list(range(2026, 2041)), TODAY, {}, need, [short, long_],
                              after_tax=False, carry_rate=0.0, plan_end=2040)
    out = L.simulate_goal_funding(list(range(2026, 2041)), TODAY, {}, need, [short, long_], use_funds=True,
                                  after_tax=False, carry_rate=0.0, plan_end=2040, plan=plan)
    got = {}
    for s in out["schedule"]:
        got.setdefault(s["year"], set()).add(s["holding_id"])
    assert got == {2035: {1}, 2040: {2}}
    assert sum(r["shortfall"] for r in out["rows"]) == pytest.approx(0.0, abs=1.0)
    assert sum(s["mismatch_years"] * s["gross"] for s in out["schedule"]) < \
        sum(s["mismatch_years"] * s["gross"] for s in greedy["schedule"])


def test_optimizer_plan_reconciles_with_simulation_and_sells_only_what_is_needed():
    f = [_fund(1, 2.0), _fund(2, 9.0, gain=0.2), _fund(3, 6.0, acct="roth")]
    need = {2027: 40000, 2030: 40000, 2034: 60000}
    bonds = {2027: 10000, 2031: 5000}
    plan = L.solve_fund_sales(list(range(2026, 2041)), TODAY, bonds, need, f, after_tax=True,
                              carry_rate=0.02, plan_end=2040)
    out = L.simulate_goal_funding(list(range(2026, 2041)), TODAY, bonds, need, f, use_funds=True,
                                  after_tax=True, carry_rate=0.02, plan_end=2040, plan=plan)
    assert sum(r["shortfall"] for r in out["rows"]) == pytest.approx(0.0, abs=1.0)
    total_need = sum(need.values())
    sold_net = sum(s["net"] for s in out["schedule"])
    assert sold_net < total_need                       # bonds + distributions cover part — no over-selling
    for r in out["rows"]:                              # a year that sells ends with ~no surplus: sold only the gap
        if r["fund_sales"] > 0:
            assert r["surplus_carried"] < 1.0


# ---------------------------------------------------------------------------
# Cash flow: principal = cost returned + gain (no double count), taxed like the after-tax yield
# ---------------------------------------------------------------------------
def _project(book, **kw):
    ctx = _ctx(cpi=_cpi(start=(2024, 1), months=40))
    rows, internals = ps.analyze_rows(book, PROFILE, ctx)
    return rows, internals, ps.project_cash_flows(rows, internals, SETTLE, years=15, inflation=ctx.inflation, **kw), ctx


def _bond(**kw):
    base = {"id": 1, "kind": "corporate", "face_value": 50000, "coupon_rate": 4.0, "coupon_freq": 2,
            "maturity_date": "2031-03-15", "current_price": 97.0, "rating": "A", "account_type": "taxable"}
    return {**base, **kw}


def test_principal_splits_into_cost_and_gain_without_double_counting():
    book = [_bond(purchase_date="2025-03-15", cost_basis=47000.0),                    # bought at 94 → discount
            _bond(id=2, purchase_date="2025-03-15", cost_basis=52500.0),              # bought at 105 → premium
            _bond(id=3, kind="treasury", face_value=20000, coupon_rate=0.0, coupon_freq=0,
                  maturity_date="2027-06-30", current_price=97.5, purchase_date="2026-09-01", cost_basis=19400.0)]
    _, _, cf, _ = _project(book)
    prin = [e for e in cf["events"] if e["type"] in ("principal", "call")]
    for e in prin:
        assert e["capital"] + e["gain"] == pytest.approx(e["amount"], abs=0.02)
    disc = next(e for e in prin if e["holding_id"] == 1)
    assert disc["gain"] == pytest.approx(3000.0, abs=0.01) and disc["capital"] == pytest.approx(47000.0)
    prem = next(e for e in prin if e["holding_id"] == 2)
    assert prem["gain"] == 0 and prem["premium_loss"] == pytest.approx(2500.0) and prem["capital"] == pytest.approx(50000.0)
    for y in cf["yearly"]:
        assert y["principal"] == pytest.approx(y["capital_returned"] + y["gain"], abs=0.05)
        assert y["total"] == pytest.approx(y["coupon"] + y["distribution"] + y["principal"], abs=0.05)
    assert cf["gains"]["total_gain"] == pytest.approx(3000.0 + 600.0, abs=0.05)


def test_market_discount_taxed_as_ordinary_and_de_minimis_as_capital_gain():
    rates = ps.tax_rates(PROFILE)
    _, _, cf, _ = _project([_bond(purchase_date="2025-03-15", cost_basis=47000.0),          # 6/100 > 0.25×5
                            _bond(id=2, purchase_date="2025-03-15", cost_basis=49800.0)])   # 0.4/100 < 1.25
    e1, e2 = (next(e for e in cf["events"] if e["type"] == "principal" and e["holding_id"] == i) for i in (1, 2))
    assert e1["tax_on_gain"] == pytest.approx(3000 * (rates.fed + rates.niit + rates.state), abs=0.05)
    assert e1["after_tax"] == pytest.approx(e1["amount"] - e1["tax_on_gain"], abs=0.05)
    assert e2["tax_on_gain"] == pytest.approx(200 * (rates.ltcg + rates.niit + rates.state), abs=0.05)


def test_muni_discount_is_federally_taxed_and_ira_gains_are_not():
    rates = ps.tax_rates(PROFILE)
    _, _, cf, _ = _project([_bond(kind="muni", state="NY", purchase_date="2025-03-15", cost_basis=47000.0),
                            _bond(id=2, account_type="ira", purchase_date="2025-03-15", cost_basis=47000.0)])
    muni, ira = (next(e for e in cf["events"] if e["type"] == "principal" and e["holding_id"] == i) for i in (1, 2))
    assert muni["tax_on_gain"] == pytest.approx(3000 * (rates.fed + rates.niit), abs=0.05)   # in-state: no state tax
    assert ira["gain"] == pytest.approx(3000.0) and ira["tax_on_gain"] == 0


def test_premium_bond_coupons_get_the_amortization_tax_shield():
    _, internals, cf, _ = _project([_bond(purchase_date="2026-09-29", purchase_price=105.0, current_price=105.0)])
    t_int = internals[1].t_int
    cpns = [e for e in cf["events"] if e["type"] == "coupon"]
    assert all(e["after_tax"] > e["amount"] * (1 - t_int) + 1 for e in cpns)


@pytest.mark.parametrize("price", [94.0, 100.0, 106.0])
def test_after_tax_cash_flows_reconcile_to_the_after_tax_yield(price):
    """Buy today at ``price``: the IRR of the projection's after-tax cash flows must equal bond_math's
    after-tax yield — same helpers (premium shield, redemption tax), so the two views can never disagree."""
    h = _bond(purchase_date=SETTLE.isoformat(), purchase_price=price, current_price=price)
    rows, internals, cf, ctx = _project([h])
    it = internals[1]
    flows = {d: t for d, _, t in bm.cash_flows(it.spec, SETTLE)}
    at_flows = [(flows[date.fromisoformat(e["date"])], e["after_tax"]) for e in cf["events"] if e["type"] != "tax"]
    by_t: dict[float, float] = {}
    for t, a in at_flows:
        by_t[t] = by_t.get(t, 0.0) + a
    dirty = 50000 * (price + bm.accrued_interest(it.spec, SETTLE)) / 100
    irr = bm._irr(sorted(by_t.items()), dirty, 2)
    want = bm.after_tax_yield(it.spec, SETTLE, price, ps.tax_rates(PROFILE), it.tr)["after_tax"]
    assert irr == pytest.approx(want, abs=2e-6)


def test_tips_phantom_tax_matches_tax_tab_and_only_real_discount_taxed_at_maturity():
    h = {k: v for k, v in TIPS_ROW.items()}
    h.update(purchase_date="2025-06-02", purchase_price=97.0, cost_basis=None)
    rows, internals, cf, ctx = _project([h])
    rates = ps.tax_rates(PROFILE)
    phantom_cf = {int(e["date"][:4]): e["phantom_income"] for e in cf["events"] if e["type"] == "tax"}
    tax = ps.tax_projection(rows, internals, {**cf}, rates, SETTLE, ctx.inflation, years=5)
    for y in tax["years"]:
        assert y["phantom_income"] == pytest.approx(phantom_cf.get(y["year"], 0.0), abs=0.05)
    prin = next(e for e in cf["events"] if e["type"] == "principal")
    it = internals[1]
    real_disc_tax = (100 - 97.0) * (rates.fed + rates.niit) * 25000 * it.ir_buy / 100   # ordinary, fed only
    assert prin["tax_on_gain"] == pytest.approx(real_disc_tax, abs=0.05)
    assert prin["capital"] + prin["gain"] == pytest.approx(prin["amount"], abs=0.02)


def test_filters_restrict_the_book():
    from app.routers.bond_router import filter_holdings
    book = [{"kind": "tips", "account_type": "ira"}, {"kind": "muni", "account_type": "taxable"},
            {"kind": "etf", "account_type": "taxable"}]
    assert len(filter_holdings(book, ["TIPS"], None)) == 1
    assert [h["kind"] for h in filter_holdings(book, None, ["taxable"])] == ["muni", "etf"]
    assert [h["kind"] for h in filter_holdings(book, ["muni", "tips"], ["taxable"])] == ["muni"]
    assert len(filter_holdings(book, None, None)) == 3


# ---------------------------------------------------------------------------
# Planner: reinvest surplus years into gap years; 59½ withdrawal rule
# ---------------------------------------------------------------------------
YEARS = list(range(2026, 2041))


def _plan(bonds, need, funds=(), *, rate=0.05, carry=0.03, early_until=None, sales=True, reinvest=True, after_tax=True):
    gaps = {y for y in need}
    plan = L.solve_cash_plan(YEARS, TODAY, bonds, need, list(funds) if sales else [], after_tax=after_tax,
                             carry_rate=carry, plan_end=YEARS[-1], sale_years=gaps, early_until=early_until,
                             reinvest_rate=(lambda tenor: rate) if reinvest else None, reinvest_targets=gaps)
    return L.simulate_goal_funding(YEARS, TODAY, bonds, need, list(funds), use_funds=sales, after_tax=after_tax,
                                   carry_rate=carry, plan_end=YEARS[-1], plan=plan["sales"], reinvest_plan=plan["reinvest"],
                                   reinvest_rate=lambda tenor: rate, early_until=early_until), plan


def test_surplus_is_reinvested_to_mature_in_the_gap_year_and_beats_tbills():
    # $100k in 2028: T-bills at 3% reach ~$123k by 2035 — short of a $130k gap; a 7y Treasury at 5% isn't
    bonds, need = {2028: 100000}, {2035: 130000}
    sim, plan = _plan(bonds, need, rate=0.05, carry=0.03)
    assert plan["reinvest"] and all(g == 2035 for (_, g) in plan["reinvest"])
    r = sim["reinvestments"][0]
    assert r["value_at_maturity"] == pytest.approx(r["amount"] * L.reinvest_factor(0.05, r["tenor"]), rel=1e-6)
    assert r["vs_tbills"] > 0
    held, _ = _plan(bonds, need, rate=0.05, carry=0.03, reinvest=False)
    short = lambda s: sum(x["shortfall"] for x in s["rows"])  # noqa: E731
    assert short(sim) < short(held)
    row35 = next(x for x in sim["rows"] if x["year"] == 2035)
    assert row35["reinvest_in"] <= 130000 + 0.5                       # never more than the gap needs
    assert sim["rows"][-1]["surplus_carried"] > held["rows"][-1]["surplus_carried"]


def test_reinvestment_never_starves_a_nearer_goal():
    sim, _ = _plan({2028: 100000}, {2030: 40000, 2036: 100000}, rate=0.05, carry=0.03)
    assert next(x for x in sim["rows"] if x["year"] == 2030)["shortfall"] == 0


def test_inverted_curve_keeps_surplus_in_tbills():
    sim, plan = _plan({2028: 100000}, {2035: 120000}, rate=0.02, carry=0.04)
    assert plan["reinvest"] == {} and sim["reinvestments"] == []


def test_early_withdrawal_rule_years():
    assert L.early_until_year("age", 1970, YEARS) == 2029          # turns 60 in 2030 → penalty-free from 2030
    assert L.early_until_year("before", 1970, YEARS) == YEARS[-1]
    assert L.early_until_year("after", 1970, YEARS) is None
    assert L.early_until_year("age", None, YEARS) is None


def test_ira_sale_before_59_and_a_half_pays_the_10pct_penalty():
    ira = _fund(1, 3.0, acct="ira", y=0.0)
    rates = ps.tax_rates(PROFILE)
    early, _ = _plan({}, {2028: 30000}, [ira], early_until=2029, reinvest=False)
    late, _ = _plan({}, {2028: 30000}, [ira], early_until=None, reinvest=False)
    e, l_ = early["schedule"][0], late["schedule"][0]
    assert e["net"] == pytest.approx(e["gross"] * (1 - rates.fed - rates.state - 0.10), abs=0.05)
    assert l_["net"] == pytest.approx(l_["gross"] * (1 - rates.fed - rates.state), abs=0.05)
    assert e["early_penalty"] == pytest.approx(e["gross"] * 0.10, abs=0.05) and l_["early_penalty"] == 0
    assert e["gross"] > l_["gross"]                                   # more must be sold to net the same


def test_roth_distributions_taxed_only_before_59_and_a_half():
    roth = _fund(1, 3.0, acct="roth", y=0.05, value=100000)
    early, _ = _plan({}, {}, [roth], early_until=2040, sales=False, reinvest=False)
    late, _ = _plan({}, {}, [roth], early_until=None, sales=False, reinvest=False)
    ed = next(x for x in early["rows"] if x["year"] == 2027)["fund_distributions"]
    ld = next(x for x in late["rows"] if x["year"] == 2027)["fund_distributions"]
    rates = ps.tax_rates(PROFILE)
    assert ld == pytest.approx(5000.0, abs=0.01)
    assert ed == pytest.approx(5000.0 * (1 - rates.fed - rates.state - 0.10), abs=0.01)


def test_plan_goals_reports_reinvestment_and_withdrawal_window(fake_market):
    prof = {**PROFILE, "settings": {"birth_year": 1968},
            "goals": [{"name": "Retirement", "year": 2032, "end_year": 2036, "amount": 30000, "inflation_adjusted": False}]}
    book = [{"id": 1, "kind": "treasury", "face_value": 80000, "coupon_rate": 4.0, "coupon_freq": 2,
             "maturity_date": "2028-06-30", "current_price": 99.5},
            {"id": 2, "kind": "corporate", "face_value": 30000, "coupon_rate": 4.0, "coupon_freq": 2,
             "maturity_date": "2030-06-30", "current_price": 99.0, "account_type": "ira"}]
    out = asyncio.run(L.plan_goals(book, prof, reinvest=True))
    w = out["withdrawal"]
    assert w["mode"] == "age" and w["early_until"] == 2027 and w["penalty_free_from"] == 2028 and w["applies"]
    ri = out["reinvest"]
    assert ri["plan"] and ri["after"]["funded_ratio_pct"] >= ri["before"]["funded_ratio_pct"]
    assert all(p["from_year"] < p["to_year"] for p in ri["plan"])
    no = asyncio.run(L.plan_goals(book, prof, reinvest=False))
    assert no["funded_ratio_pct"] == ri["before"]["funded_ratio_pct"]


def test_by_age_mode_keeps_ira_funds_in_the_account_until_59_and_a_half():
    ira = _fund(1, 3.0, acct="ira", y=0.05, value=100000)
    plan = L.solve_cash_plan(YEARS, TODAY, {}, {2028: 30000, 2032: 30000}, [ira], after_tax=True, carry_rate=0.03,
                             plan_end=YEARS[-1], sale_years={2028, 2032}, early_until=2029, lock_early=True)
    sim = L.simulate_goal_funding(YEARS, TODAY, {}, {2028: 30000, 2032: 30000}, [ira], use_funds=True, after_tax=True,
                                  carry_rate=0.03, plan_end=YEARS[-1], plan=plan["sales"], early_until=2029, lock_early=True)
    assert all(s["year"] >= 2030 for s in sim["schedule"])                       # nothing sold while locked
    assert all(r["fund_distributions"] == 0 for r in sim["rows"] if r["year"] <= 2029)
    assert next(r for r in sim["rows"] if r["year"] == 2028)["shortfall"] == pytest.approx(30000, abs=1)
    assert sum(r["early_cost"] for r in sim["rows"]) == 0                        # no penalties paid
    assert sum(s["gross"] for s in sim["schedule"]) + sim["remaining_total"] > 100000  # it grew while locked


def test_ira_bond_cash_is_held_back_and_released_at_59_and_a_half(fake_market):
    prof = {**PROFILE, "settings": {"birth_year": 1968},
            "goals": [{"name": "Spend", "year": 2027, "end_year": 2030, "amount": 10000, "inflation_adjusted": False}]}
    book = [{"id": 1, "kind": "treasury", "face_value": 40000, "coupon_rate": 4.0, "coupon_freq": 2,
             "maturity_date": "2027-06-30", "current_price": 99.9, "account_type": "ira"}]
    out = asyncio.run(L.plan_goals(book, prof, reinvest=False))
    w = out["withdrawal"]
    assert w["locked_until_59"] and w["released_year"] == 2028 and w["held_back_released"] > 0
    r27, r28 = (next(r for r in out["years"] if r["year"] == y) for y in (2027, 2028))
    assert r27["bond_inflow"] == 0 and r27["shortfall"] == pytest.approx(10000, abs=1)   # can't touch it before 59½
    rates = ps.tax_rates(PROFILE)
    assert r28["bond_inflow"] == pytest.approx(w["held_back_released"], abs=0.5)
    assert w["held_back_released"] < 41600 * (1 - rates.fed - rates.state) * 1.05      # taxed as income on release
    before = asyncio.run(L.plan_goals(book, prof, reinvest=False, withdrawal_mode="before"))
    assert before["withdrawal"]["early_cost_total"] > 0 and not before["withdrawal"]["locked_until_59"]


def test_plan_never_sells_funds_and_sets_cash_aside_in_the_same_year(fake_market):
    prof = {**PROFILE, "goals": [{"name": "Income", "year": 2029, "end_year": 2038, "amount": 40000, "inflation_adjusted": False}]}
    book = [{"id": 1, "kind": "treasury", "face_value": 150000, "coupon_rate": 4.0, "coupon_freq": 2,
             "maturity_date": "2028-06-30", "current_price": 99.5},
            {"id": 2, "kind": "etf", "ticker": "BND", "quantity": 2000, "purchase_price": 72.0, "account_type": "taxable"}]
    out = asyncio.run(L.plan_goals(book, prof, use_funds=True, reinvest=True))
    for r in out["years"]:
        assert not (r["fund_sales"] > 0 and r["reinvest_out"] > 0), r["year"]


# ---------------------------------------------------------------------------
# Total yield (income + price gain to maturity + inflation), fund yields & payout,
# inflation path, after-retirement tax, joint reinvest/sell plan
# ---------------------------------------------------------------------------
BOXX = {"ticker": "BOXX", "name": "Alpha Architect 1-3 Month Box ETF", "quote_type": "ETF", "price": 115.0,
        "distribution_yield_pct": None, "duration": 0.0, "expense_ratio_pct": 0.19, "credit_mix": {}}
CLO = {"ticker": "JAAA", "name": "Janus Henderson AAA CLO ETF", "quote_type": "ETF", "price": 50.0,
       "distribution_yield_pct": 5.3, "duration": 0.05, "expense_ratio_pct": 0.2, "credit_mix": {"aaa": 0.97, "aa": 0.03}}
EMF = {"ticker": "VWOB", "name": "Vanguard Emerging Markets Government Bond", "quote_type": "ETF", "price": 65.0,
       "distribution_yield_pct": 5.9, "duration": 6.4, "expense_ratio_pct": 0.15, "credit_mix": {"us_government": 0.85}}
CEF = {"ticker": "MUC", "name": "BlackRock MuniHoldings California", "quote_type": "EQUITY", "price": 11.0,
       "distribution_yield_pct": 6.6, "duration": 6.7, "expense_ratio_pct": None, "credit_mix": {}}
FUNDS_X = {"BND": BND, "BOXX": BOXX, "JAAA": CLO, "VWOB": EMF, "MUC": CEF}


def _fund_row(ticker, **kw):
    h = {"id": 100, "kind": "etf", "ticker": ticker, "quantity": 1000, "purchase_price": 100.0, "account_type": "taxable", **kw}
    rows, internals = ps.analyze_rows([h], PROFILE, _ctx(funds=FUNDS_X))
    return rows[0], internals[100]


def test_box_spread_etf_accumulates_at_the_bill_rate_and_is_taxed_as_a_capital_gain():
    r, it = _fund_row("BOXX")
    rates = ps.tax_rates(PROFILE)
    assert r["fund"]["payout"] == "accumulates" and r["annual_income"] == 0
    assert r["total_yield_pct"] == pytest.approx((0.0428 - 0.0019) * 100, abs=1e-3)       # 3-month bill − expenses
    assert r["yield_parts"]["income_pct"] == 0
    assert r["yield_parts"]["price_gain_pct"] == pytest.approx(r["total_yield_pct"], abs=1e-3)
    assert r["tax"]["after_tax_yield_pct"] == pytest.approx(
        r["total_yield_pct"] * (1 - rates.ltcg - rates.niit - rates.state), abs=1e-3)
    cash = ps.project_cash_flows([r], {100: it}, SETTLE, years=5, inflation=0.025)
    assert cash["events_count"] == 0 and any("pay no distributions" in n for n in cash["notes"])


def test_fund_settings_set_payout_and_yield_but_a_legacy_coupon_value_is_ignored():
    legacy, _ = _fund_row("BND", coupon_rate=11.75, coupon_freq=2)        # saved before funds had these settings
    assert legacy["fund"]["user_yield_pct"] is None and legacy["total_yield_pct"] != 11.75
    mine, _ = _fund_row("BND", coupon_rate=4.6, coupon_freq=ps.FUND_DISTRIBUTES)
    assert mine["total_yield_pct"] == pytest.approx(4.6) and mine["yield_basis"] == "your yield"
    assert mine["annual_income"] == pytest.approx(mine["market_value"] * 0.046, abs=0.01)
    acc, _ = _fund_row("JAAA", coupon_freq=ps.FUND_ACCUMULATES)
    assert acc["fund"]["payout"] == "accumulates" and acc["annual_income"] == 0
    auto, _ = _fund_row("JAAA", coupon_rate=5.1, coupon_freq=ps.FUND_AUTO)
    assert auto["fund"]["payout"] == "distributes" and auto["total_yield_pct"] == pytest.approx(5.1)


def test_fund_yield_estimates_by_fund_type():
    bnd, _ = _fund_row("BND")
    assert bnd["fund"]["est_ytm_pct"] > bm.interp(NOM, 6.0) * 100 - 0.03          # Treasury at 6y + credit − fees
    assert bnd["total_yield_pct"] == bnd["fund"]["est_ytm_pct"]
    assert bnd["yield_parts"]["income_pct"] == pytest.approx(4.0)                 # cash = its distributions
    clo, _ = _fund_row("JAAA")
    assert clo["fund"]["est_ytm_pct"] == pytest.approx((0.0428 + 0.0125 - 0.002) * 100, abs=1e-3)
    for t in ("VWOB", "MUC"):         # emerging-market / closed-end: not on the US curve → their distributions
        r, _ = _fund_row(t)
        assert r["fund"]["est_ytm_pct"] is None
        assert r["total_yield_pct"] == pytest.approx(FUNDS_X[t]["distribution_yield_pct"])


def test_total_yield_parts_sum_and_tips_total_is_real_plus_inflation():
    rows, internals, agg, ctx = _analyze()
    for r in rows:
        p = r.get("yield_parts")
        if p:
            assert p["income_pct"] + p["price_gain_pct"] + p["inflation_pct"] == pytest.approx(p["total_pct"], abs=2e-3)
    tips = next(r for r in rows if r["kind"] == "tips")
    assert tips["total_yield_pct"] == pytest.approx(((1 + tips["ytw_pct"] / 100) * 1.025 - 1) * 100, abs=2e-3)
    s = agg["summary"]
    assert sum(s["yield_parts"][k] for k in ("income_pct", "price_gain_pct", "inflation_pct")) == \
        pytest.approx(s["total_yield_pct"], abs=1e-3)
    assert s["total_yield_pct"] > s["ytw_pct"]                   # TIPS now counted nominal, not real
    assert next(r for r in rows if r["id"] == 3)["yield_parts"]["price_gain_pct"] > 0   # bought at 96 → pulls to 100
    assert next(r for r in rows if r["id"] == 2)["yield_parts"]["price_gain_pct"] < 0   # premium muni amortizes


def test_inflation_path_from_profile_and_market():
    rc = {"be_5y": {"value_pct": 2.4}, "be_10y": {"value_pct": 2.3}}
    p, _ = ps.inflation_assumption({}, rc)
    assert p.short == pytest.approx(0.024) and p.long == pytest.approx((1.023 ** 10 / 1.024 ** 5) ** 0.2 - 1)
    p, src = ps.inflation_assumption({"inflation_assumption": 4.0, "settings": {"inflation_long": 2.5}}, rc)
    assert p.short == pytest.approx(0.04) and p.long == pytest.approx(0.025) and "yours" in src


def test_tips_cash_flows_follow_the_inflation_path():
    rows, internals, _, _ = _analyze()
    flat = ps.project_cash_flows(rows, internals, SETTLE, years=12, inflation=bm.InflationPath(0.025, 0.025))
    hot = ps.project_cash_flows(rows, internals, SETTLE, years=12, inflation=bm.InflationPath(0.05, 0.025))
    prin = lambda cf: sum(e["amount"] for e in cf["events"] if e["holding_id"] == 8 and e["type"] == "principal")  # noqa: E731
    assert prin(hot) / prin(flat) == pytest.approx((1.05 / 1.025) ** 3, rel=1e-3)   # differs only in the first 3 years


def test_after_retirement_rates_apply_from_the_retirement_year():
    prof = {**PROFILE, "settings": {"retire_year": 2029, "retired_federal_rate": 12, "retired_state_rate": 3,
                                    "retired_ltcg_rate": 0}}
    sched = ps.tax_schedule(prof)
    assert sched.at(2028) == ps.tax_rates(PROFILE) and sched.at(2029).fed == pytest.approx(0.12)
    rows, internals, _, _ = _analyze(profile=prof)
    cf = ps.project_cash_flows(rows, internals, SETTLE, years=10, inflation=0.025, tax=sched)
    corp = [e for e in cf["events"] if e["holding_id"] == 3 and e["type"] == "coupon"]
    before = next(e for e in corp if e["date"] < "2029-01-01")
    after = next(e for e in corp if e["date"] >= "2029-01-01")
    full = bm.TaxTreatment()
    assert before["after_tax"] == pytest.approx(before["amount"] * (1 - bm.interest_tax_rate(sched.now, full)), abs=0.01)
    assert after["after_tax"] == pytest.approx(after["amount"] * (1 - bm.interest_tax_rate(sched.retired, full)), abs=0.01)
    tax = ps.tax_projection(rows, internals, cf, sched, SETTLE, 0.025, years=5)
    assert all(y["retired_rates"] == (y["year"] >= 2029) for y in tax["years"])
    assert tax["retired_rates"]["from_year"] == 2029
    # the retirement year defaults to a goal named "Retirement…"; no retired rates → one schedule
    assert ps.retirement_year({"goals": [{"name": "Retirement income", "year": 2031}], "settings": {}}) == 2031
    assert ps.tax_schedule(PROFILE).retired is None


def test_accumulating_fund_grows_until_sold_and_its_taxable_gain_grows_with_it():
    acc = {**_fund(1, 0.1, y=0.04, value=100000), "cash_yield": 0.0, "accrual": 0.04, "accumulates": True}
    out = _sim([acc], {2032: 50000}, years=range(2026, 2034))
    assert all(r["fund_distributions"] == 0 for r in out["rows"])
    s = out["schedule"][0]
    assert out["remaining_total"] + s["gross"] > 100000 * 1.04 ** 5        # it kept growing in price
    assert s["tax"] > 0                       # bought at today's value → the growth is a taxable gain when sold


def test_optimizer_can_sell_the_grown_value_of_an_accumulating_fund():
    acc = {**_fund(1, 0.1, y=0.05, value=100000), "cash_yield": 0.0, "accrual": 0.05, "accumulates": True}
    plan = L.solve_cash_plan(YEARS, TODAY, {}, {2034: 500000}, [acc], after_tax=False, carry_rate=0.0,
                             plan_end=YEARS[-1], sale_years={2034})
    first_frac = max(0.0, (date(TODAY.year, 12, 31) - TODAY).days / 365.25)
    g = L._value_growth(acc, YEARS, first_frac, None, False)[YEARS.index(2034)]
    assert sum(plan["sales"].values()) == pytest.approx(100000 * g, rel=1e-6)


def test_reinvested_treasury_replaces_fund_sales_in_its_gap_year(fake_market):
    prof = {**PROFILE, "goals": [{"name": "Income", "year": 2029, "end_year": 2038, "amount": 40000, "inflation_adjusted": False}]}
    book = [{"id": 1, "kind": "treasury", "face_value": 150000, "coupon_rate": 4.0, "coupon_freq": 2,
             "maturity_date": "2028-06-30", "current_price": 99.5},
            {"id": 2, "kind": "etf", "ticker": "BND", "quantity": 2000, "purchase_price": 72.0, "account_type": "taxable"}]
    out = asyncio.run(L.plan_goals(book, prof, use_funds=True, reinvest=True))
    assert out["reinvest"]["plan"]
    for r in out["years"]:          # a gap year is never funded twice (Treasury arriving + fund sales ≤ its need)
        assert r["reinvest_in"] + r["fund_sales"] <= r["need"] + 1, r["year"]


def test_router_normalizes_fund_settings():
    from app.routers import bond_router as br
    d = br._validate(br.HoldingIn(kind="etf", ticker="boxx", quantity=10, coupon_freq=2))
    assert d["coupon_freq"] == ps.FUND_AUTO and d["ticker"] == "BOXX"
    assert br._validate(br.HoldingIn(kind="etf", ticker="BOXX", quantity=10, coupon_freq=0))["coupon_freq"] == 0
    with pytest.raises(Exception):
        br._validate(br.HoldingIn(kind="etf", ticker="BOXX", quantity=10, coupon_freq=1, coupon_rate=45.0))


def test_surplus_is_laddered_into_every_later_year_that_spends_it(fake_market):
    """No year is short — the 2027 maturity's surplus pays 2028–2030. It should be invested in Treasuries
    maturing in those years (term yield > T-bills), each year spending its own bond cash first."""
    prof = {**PROFILE, "goals": [{"name": "Spend", "year": 2028, "end_year": 2030, "amount": 100000, "inflation_adjusted": False}]}
    book = [{"id": 1, "kind": "treasury", "face_value": 300000, "coupon_rate": 4.0, "coupon_freq": 2,
             "maturity_date": "2027-06-30", "current_price": 99.8},
            {"id": 2, "kind": "treasury", "face_value": 20000, "coupon_rate": 4.0, "coupon_freq": 2,
             "maturity_date": "2029-06-30", "current_price": 99.0}]
    on = asyncio.run(L.plan_goals(book, prof, reinvest=True))
    off = asyncio.run(L.plan_goals(book, prof, reinvest=False))
    plan = on["reinvest"]["plan"]
    assert plan and {p["to_year"] for p in plan} <= {2028, 2029, 2030} and all(p["vs_tbills"] > 0 for p in plan)
    for r in on["years"]:                     # the Treasury fills only what that year's own bonds don't
        assert r["reinvest_in"] <= max(0.0, r["need"] - r["bond_inflow"]) + 1, r["year"]
    assert sum(r["reinvest_in"] for r in on["years"]) > 0
    assert on["years"][-1]["surplus_carried"] >= off["years"][-1]["surplus_carried"] - 0.5
    b, a = on["reinvest"]["before"], on["reinvest"]["after"]
    assert b["shortfall_total"] == pytest.approx(sum(r["shortfall"] for r in off["years"]), abs=0.05)
    assert a["shortfall_total"] <= b["shortfall_total"] + 0.05 and a["need_total"] == pytest.approx(b["need_total"])


def test_plan_taxes_reconcile_and_shortfall_is_priced_after_tax(fake_market):
    """Taxes reported = pre-tax bond cash − after-tax bond cash; the cost to fill a gap is priced for a taxable
    account (Treasury/TIPS income taxed every year → more than the pre-tax price), and equals it pre-tax."""
    prof = {**PROFILE, "goals": [{"name": "Retirement", "year": 2027, "end_year": 2045, "amount": 30000, "inflation_adjusted": True}]}
    book = [{"id": 1, "kind": "treasury", "face_value": 100000, "coupon_rate": 4.0, "coupon_freq": 2,
             "maturity_date": "2031-06-30", "current_price": 99.0, "purchase_price": 97.0, "purchase_date": "2025-01-10"},
            {"id": 2, "kind": "corporate", "face_value": 60000, "coupon_rate": 5.0, "coupon_freq": 2,
             "maturity_date": "2034-06-30", "current_price": 100.0}]
    pre = asyncio.run(L.plan_goals(book, prof, after_tax=False, use_funds=False, reinvest=False))
    aft = asyncio.run(L.plan_goals(book, prof, after_tax=True, use_funds=False, reinvest=False))
    gross = sum(r["bond_inflow"] for r in pre["years"])
    net = sum(r["bond_inflow"] for r in aft["years"])
    assert aft["taxes_total"] == pytest.approx(gross - net, abs=1.0) and pre["taxes_total"] == 0
    assert aft["book_value"] == pytest.approx(pre["book_value"]) and aft["book_value"] > 0
    c = aft["cost_to_fund_shortfalls"]
    assert aft["shortfall_years"] and c["total"] > c["pre_tax_total"] > 0
    assert pre["cost_to_fund_shortfalls"]["total"] == pytest.approx(pre["cost_to_fund_shortfalls"]["pre_tax_total"])
    # the same gap priced with no income tax collapses to the pre-tax price
    zero = {**prof, "federal_rate": 0, "state_rate": 0, "niit": False}
    z = asyncio.run(L.plan_goals(book, zero, after_tax=True, use_funds=False, reinvest=False))["cost_to_fund_shortfalls"]
    assert z["total"] == pytest.approx(z["pre_tax_total"], rel=1e-9)
