"""Bond Desk ladders & planning — build, size, simulate and monitor bond ladders.

* ``build_ladder`` — nominal ladders of Treasuries (real FedInvest CUSIPs + prices),
  CDs, munis, agencies, corporates, defined-maturity ETFs, or ``best_after_tax``
  (per rung, whichever instrument nets the most for THIS investor's tax profile).
  Sizing is ``equal`` per rung or ``level_income`` (backward induction so every
  period's principal + coupons are equal — the retirement-income ladder).
* ``build_tips_ladder`` — a real-income TIPS ladder from the live TIPS catalogue
  (tipsladder-style backward induction in today's dollars), with duration-matched
  bracket holdings covering maturity gap years (2037–2039).
* ``simulate_ladder`` — a rolling ladder over N years under flat / ±100bp /
  market-forward curves, so reinvestment risk is visible.
* ``ladder_status`` — funded / partial / missing per planned rung vs linked holdings.
* ``plan_goals`` — liability matching: holdings' cash flows vs goals by year, with the
  cost to close every shortfall with Treasuries (nominal goals) or TIPS (real goals).

Rungs are emitted as ``holdings`` dicts in the exact ``BondHolding`` shape and run
through the SAME portfolio engine, so a ladder's yield/duration/cash flows reconcile
with what the user sees after adopting it.
"""

from __future__ import annotations

import math
from datetime import date, timedelta

from . import bond_math as bm
from . import bond_market_service as mkt
from . import bond_income_service as inc
from . import bond_portfolio_service as ps

SPACING_MONTHS = {"annual": 12, "semiannual": 6, "quarterly": 3, "monthly": 1}
FACE_INCREMENT = {"treasury": 100.0, "tips": 100.0, "cd": 1000.0, "muni": 5000.0, "corporate": 1000.0, "agency": 1000.0}
ETF_FAMILY = {"etf_treasury": "treasury", "etf_corporate": "corporate", "etf_muni": "muni", "etf_tips": "tips",
              "etf_high_yield": "high_yield"}
INSTRUMENT_LABEL = {"treasury": "Treasury", "cd": "Brokered CD", "muni": "Muni", "corporate": "Corporate",
                    "agency": "Agency", "best_after_tax": "Best after-tax", "etf_treasury": "iBonds Treasury ETFs",
                    "etf_corporate": "iBonds Corporate ETFs", "etf_muni": "iBonds Muni ETFs",
                    "etf_tips": "iBonds TIPS ETFs", "etf_high_yield": "BulletShares High-Yield ETFs"}
MAX_RUNGS = 120


def _floor_to(x: float, inc: float) -> float:
    return math.floor(x / inc + 1e-9) * inc if inc > 0 else x


# ---------------------------------------------------------------------------
# Rung schedule + instrument selection
# ---------------------------------------------------------------------------
def rung_dates(today: date, start_years: float, end_years: float, frequency: str) -> list[date]:
    step = SPACING_MONTHS.get(frequency, 12)
    first = bm.add_months(today, max(1, int(round(start_years * 12))))
    last = bm.add_months(today, max(1, int(round(end_years * 12))))
    out, k = [], 0
    while True:
        d = bm.add_months(first, step * k)
        if d > last or len(out) >= MAX_RUNGS:
            break
        out.append(d)
        k += 1
    return out


def pick_treasury(catalogue_rows: list[dict], target: date, *, allow_bills: bool = True) -> dict | None:
    """Outstanding Treasury maturing closest to (preferably on/before) ``target``."""
    best, best_score = None, None
    for r in catalogue_rows:
        if r["type"] not in ("note", "bond") and not (allow_bills and r["type"] == "bill"):
            continue
        if r.get("ytm_pct") is None:
            continue
        m = date.fromisoformat(r["maturity"])
        delta = (m - target).days
        if delta < -150 or delta > 45:
            continue
        score = abs(delta) + (20 if delta > 0 else 0)   # prefer cash on/before the target
        if best_score is None or score < best_score:
            best, best_score = r, score
    return best


def _rung_holding(kind: str, *, target: date, today: date, cost: float, y: float, account: str,
                  label: str, tsy: dict | None = None, etf: dict | None = None, rating: str | None = None) -> dict:
    """A BondHolding-shaped dict for one rung (buy ``cost`` dollars)."""
    if etf:
        price = etf.get("price") or 0.0
        qty = math.floor(cost / price) if price else 0
        return {"kind": "etf", "ticker": etf["ticker"], "label": label, "quantity": qty, "purchase_price": price,
                "current_price": price, "purchase_date": today.isoformat(), "account_type": account,
                "_cost": qty * price}
    if tsy:
        mat = date.fromisoformat(tsy["maturity"])
        cpn = tsy["coupon_pct"] or 0.0
        freq = 0 if tsy["type"] == "bill" else 2
        spec = bm.BondSpec(maturity=mat, coupon=cpn / 100 if freq else 0.0, freq=freq or 0, day_count="ACT/ACT",
                           issue=date.fromisoformat(tsy["dated_date"]) if tsy.get("dated_date") else None)
        ai = bm.accrued_interest(spec, today)
        dirty = tsy["price"] + ai
        face = _floor_to(cost / (dirty / 100.0), FACE_INCREMENT["treasury"])
        return {"kind": "treasury", "cusip": tsy["cusip"], "issuer": "U.S. Treasury", "label": label,
                "face_value": face, "coupon_rate": cpn, "coupon_freq": freq, "day_count": "ACT/ACT",
                "issue_date": tsy.get("dated_date"), "maturity_date": tsy["maturity"],
                "purchase_date": today.isoformat(), "purchase_price": tsy["price"], "current_price": tsy["price"],
                "account_type": account, "rating": "AA+", "_cost": face * dirty / 100.0}
    years = bm.year_frac(today, target)
    inc = FACE_INCREMENT.get(kind, 1000.0)
    if kind in ("treasury",) and years <= 1.0:
        # bill: zero coupon bought at a discount
        spec = bm.BondSpec(maturity=target, coupon=0.0, freq=0)
        price = bm.clean_from_yield(spec, today, y)
        face = _floor_to(cost / (price / 100.0), inc)
        return {"kind": "treasury", "issuer": "U.S. Treasury", "label": label, "face_value": face, "coupon_rate": 0.0,
                "coupon_freq": 0, "maturity_date": target.isoformat(), "purchase_date": today.isoformat(),
                "purchase_price": round(price, 6), "current_price": round(price, 6), "account_type": account,
                "rating": "AA+", "_cost": face * price / 100.0}
    if kind == "cd" and years <= 1.0:
        face = _floor_to(cost, inc)
        return {"kind": "cd", "issuer": "Brokered CD (any FDIC bank)", "label": label, "face_value": face,
                "coupon_rate": round(y * 100, 4), "coupon_freq": 0, "issue_date": today.isoformat(),
                "maturity_date": target.isoformat(), "purchase_date": today.isoformat(), "account_type": account,
                "_cost": face}
    # new issue at par: coupon == yield
    face = _floor_to(cost, inc)
    return {"kind": kind, "label": label, "issuer": {"cd": "Brokered CD (any FDIC bank)", "muni": "Muni (AA, in-state)",
                                                     "agency": "Agency (FHLB/FFCB/FNMA)", "corporate": f"Corporate ({rating or 'A'})",
                                                     "treasury": "U.S. Treasury"}.get(kind, kind),
            "face_value": face, "coupon_rate": round(y * 100, 4), "coupon_freq": 12 if kind == "cd" else 2,
            "maturity_date": target.isoformat(), "purchase_date": today.isoformat(), "purchase_price": 100.0,
            "current_price": 100.0, "account_type": account, "rating": rating if kind == "corporate" else ("AA" if kind == "muni" else None),
            "_cost": face}


def _coupon_and_price(sp: dict, today: date) -> tuple[float, float]:
    """(annual coupon rate, dirty price per 1 of face) a rung will actually be bought at."""
    tsy = sp.get("tsy")
    if tsy:
        mat = date.fromisoformat(tsy["maturity"])
        freq = 0 if tsy["type"] == "bill" else 2
        spec = bm.BondSpec(maturity=mat, coupon=(tsy["coupon_pct"] or 0) / 100 if freq else 0.0, freq=freq,
                           day_count="ACT/ACT",
                           issue=date.fromisoformat(tsy["dated_date"]) if tsy.get("dated_date") else None)
        return (spec.coupon, (tsy["price"] + bm.accrued_interest(spec, today)) / 100.0)
    if sp["kind"] == "treasury" and sp["years"] <= 1.0:
        spec = bm.BondSpec(maturity=sp["target"], coupon=0.0, freq=0)
        return (0.0, bm.clean_from_yield(spec, today, sp["y"]) / 100.0)
    return (sp["y"], 1.0)


# ---------------------------------------------------------------------------
# Nominal ladder
# ---------------------------------------------------------------------------
async def build_ladder(params: dict, profile: dict, *, today: date | None = None) -> dict:
    today = today or mkt.us_today()
    p = {"amount": 100000.0, "start_years": 1.0, "end_years": 10.0, "frequency": "annual",
         "instrument": "treasury", "weighting": "equal", "account_type": "taxable",
         "allow_credit": False, "corporate_rating": "A", **(params or {})}
    amount = float(p["amount"] or 0)
    instrument = p["instrument"]
    account = (p["account_type"] or "taxable").lower()
    dates = rung_dates(today, float(p["start_years"]), float(p["end_years"]), p["frequency"])
    if not dates or amount <= 0:
        return {"error": "Choose an amount and a start/end so at least one rung exists.", "rungs": []}
    mi = await mkt.market_inputs()
    cat = await mkt.treasury_catalogue()
    cat_rows = (cat or {}).get("rows", [])
    rc = await mkt.rate_context()
    infl, _ = ps.inflation_assumption(profile, rc)
    etfs = {}
    if instrument in ETF_FAMILY:
        for e in await mkt.etf_rungs(ETF_FAMILY[instrument]):
            if e.get("price"):
                etfs[e["year"]] = e
        if p["frequency"] != "annual":
            p["frequency"] = "annual"
    rating = p.get("corporate_rating") or "A"

    # 1) choose the instrument + yield for each rung
    specs = []
    used_cusips: set[str] = set()
    for d in dates:
        t = bm.year_frac(today, d)
        choice: dict = {"target": d, "years": t, "alternatives": []}
        if instrument in ETF_FAMILY:
            e = etfs.get(d.year)
            base_kind = {"etf_treasury": "treasury", "etf_corporate": "corporate", "etf_muni": "muni",
                         "etf_tips": "tips", "etf_high_yield": "corporate"}[instrument]
            y_model, basis = mkt.model_yield(base_kind, t, mi, "BB" if instrument == "etf_high_yield" else rating)
            if e:
                choice.update(kind="etf", etf=e, y=y_model, basis=f"{e['ticker']} matures Dec {d.year}; est. YTM from {basis}",
                              label=f"{e['ticker']} · {d.year}", dist_yield=e.get("distribution_yield_pct"))
            else:
                y, b2 = mkt.model_yield("treasury", t, mi)
                tsy = pick_treasury(cat_rows, d)
                choice.update(kind="treasury", y=(tsy["ytm_pct"] / 100) if tsy else y, tsy=tsy,
                              basis=f"no {INSTRUMENT_LABEL[instrument]} for {d.year} — Treasury instead",
                              label=f"Treasury {tsy['maturity'] if tsy else d.isoformat()}")
        else:
            kind = instrument
            if instrument == "best_after_tax":
                kinds = {"treasury", "cd", "muni", "agency"} | ({"corporate"} if p.get("allow_credit") else set())
                menu = ps.after_tax_menu(mi, profile, [t], account=account, inflation=infl, kinds=kinds)
                cands = [c for c in menu[0]["candidates"] if c["kind"] != "corporate" or c.get("rating") == rating] if menu else []
                best = cands[0] if cands else None
                kind = best["kind"] if best else "treasury"
                choice["alternatives"] = cands[:4]
            tsy = pick_treasury(cat_rows, d) if kind == "treasury" else None
            if tsy and tsy["cusip"] in used_cusips:
                tsy = None
            if tsy:
                used_cusips.add(tsy["cusip"])
                choice.update(kind="treasury", tsy=tsy, y=tsy["ytm_pct"] / 100,
                              basis=f"FedInvest EOD {cat.get('as_of') if cat else ''} · CUSIP {tsy['cusip']}",
                              label=f"UST {tsy['coupon_pct']:.3f}% {tsy['maturity']}")
            else:
                y, basis = mkt.model_yield(kind, t, mi, rating if kind == "corporate" else None)
                choice.update(kind=kind, y=y, basis=basis,
                              label=f"{INSTRUMENT_LABEL.get(kind, kind)} {d.isoformat()}")
        if choice.get("y") is None:
            return {"error": "Market curve unavailable right now — try again shortly.", "rungs": []}
        specs.append(choice)

    # 2) size the rungs
    n = len(specs)
    delta = SPACING_MONTHS.get(p["frequency"], 12) / 12.0
    if p["weighting"] == "level_income" and n > 1:
        # Backward induction on FACE with each rung's ACTUAL coupon, then cost = face × dirty price.
        # (Sizing on yield at par drifts: a 3.375% Treasury bought at 97 pays less coupon per dollar.)
        cps = [_coupon_and_price(sp, today) for sp in specs]
        faces = [0.0] * n
        for k in range(n - 1, -1, -1):
            later_cpn = sum(faces[j] * cps[j][0] * delta for j in range(k + 1, n))
            faces[k] = max(0.0, (1.0 - later_cpn) / (1 + cps[k][0] * delta))
        costs = [f * cp[1] for f, cp in zip(faces, cps)]
        tot = sum(costs)
        weights = [c / tot for c in costs] if tot else [1.0 / n] * n
    else:
        weights = [1.0 / n] * n
    holdings = []
    for i, (sp, w) in enumerate(zip(specs, weights)):
        h = _rung_holding(sp["kind"], target=sp["target"], today=today, cost=amount * w, y=sp["y"], account=account,
                          label=sp["label"], tsy=sp.get("tsy"), etf=sp.get("etf"),
                          rating=rating if sp["kind"] == "corporate" else None)
        h["id"] = i + 1
        h["status"] = "held"
        holdings.append(h)

    # 3) run the rungs through the SAME portfolio engine
    ctx = await ps.load_context(holdings, profile, today)
    rows, internals = ps.analyze_rows(holdings, profile, ctx)
    agg = ps.aggregate(rows, internals)
    cash = ps.project_cash_flows(rows, internals, today, years=max(5, int(math.ceil(float(p["end_years"]))) + 1),
                                 fund_years=int(math.ceil(float(p["end_years"]))) + 1, inflation=ctx.inflation)
    rungs = []
    spent = 0.0
    for sp, h, r in zip(specs, holdings, rows):
        cost = h.pop("_cost", 0.0)
        spent += cost
        rungs.append({
            "index": h["id"], "target_date": sp["target"].isoformat(), "maturity": r.get("maturity") or (f"{sp['target'].year}-12-15" if sp["kind"] == "etf" else None),
            "years": round(sp["years"], 2), "kind": sp["kind"], "label": sp["label"], "basis": sp["basis"],
            "cusip": (sp.get("tsy") or {}).get("cusip"), "ticker": (sp.get("etf") or {}).get("ticker"),
            "yield_pct": r.get("ytw_pct") if sp["kind"] != "etf" else round(sp["y"] * 100, 3),
            "distribution_yield_pct": sp.get("dist_yield"),
            "after_tax_pct": (r.get("tax") or {}).get("after_tax_yield_pct") if sp["kind"] != "etf" else
                             round(sp["y"] * 100 * (1 - ((r.get("tax") or {}).get("rate_pct") or 0) / 100), 3),
            "tey_pct": (r.get("tax") or {}).get("tey_pct"),
            "cost": round(cost, 2), "face": h.get("face_value"), "quantity": h.get("quantity"),
            "coupon_pct": h.get("coupon_rate"), "annual_income": r.get("annual_income"),
            "eff_duration": r.get("eff_duration"), "alternatives": sp.get("alternatives") or [],
            "holding": h,
        })
    w_at = ps._wavg_vals([(r["after_tax_pct"], r["cost"]) for r in rungs])
    w_y = ps._wavg_vals([(r["yield_pct"], r["cost"]) for r in rungs])
    summary = {
        "amount": round(amount, 2), "invested": round(spent, 2), "cash_left": round(amount - spent, 2),
        "rungs": len(rungs), "yield_pct": ps._r(w_y, 3), "after_tax_yield_pct": ps._r(w_at, 3),
        "tey_pct": ps._r(bm.tax_equivalent_yield((w_at or 0) / 100, ps.tax_rates(profile)) * 100, 3) if w_at is not None else None,
        "eff_duration": agg["summary"]["eff_duration"], "annual_income": agg["summary"]["annual_income"],
        "annual_income_after_tax": agg["summary"]["annual_income_after_tax"], "dv01": agg["summary"]["dv01"],
        "avg_years": ps._r(ps._wavg_vals([(r["years"], r["cost"]) for r in rungs]), 2),
        "first_maturity": rungs[0]["maturity"] if rungs else None, "last_maturity": rungs[-1]["maturity"] if rungs else None,
    }
    notes = []
    if any(r["kind"] == "cd" for r in rungs):
        notes.append("Keep each bank's CDs ≤ $250k (principal + interest) for full FDIC coverage.")
    if any(r["kind"] == "muni" for r in rungs):
        notes.append("Muni yields are estimates (AA, in-state). Buy in $5k lots; check call features — a callable muni can end your rung early.")
    if any(r["kind"] == "corporate" for r in rungs):
        notes.append("Corporate rungs add credit risk; diversify issuers (≤5% each) and prefer non-callable bullets.")
    if instrument in ETF_FAMILY:
        notes.append("Defined-maturity ETFs liquidate in their maturity year and return NAV (not a guaranteed par amount).")
    if any(r["kind"] == "treasury" and r["cusip"] for r in rungs):
        notes.append("Treasury rungs are real outstanding CUSIPs priced at TreasuryDirect FedInvest end-of-day; buy at your broker's ask.")
    return {"params": p, "summary": summary, "rungs": rungs, "cash_flow": {k: v for k, v in cash.items() if k != "events"},
            "allocation": agg["allocation"], "key_rate_dv01": agg["key_rate_dv01"], "notes": notes,
            "as_of": today.isoformat(), **ps.inflation_fields(ctx.inflation)}


# ---------------------------------------------------------------------------
# TIPS ladder (real income floor)
# ---------------------------------------------------------------------------
def _coupons_in_maturity_year(maturity: date) -> int:
    """Semiannual coupons paid in the maturity's calendar year, on/before maturity."""
    return 2 if maturity.month >= 7 else 1


def tips_ladder_math(tips_rows: list[dict], first_year: int, last_year: int, income: float | dict,
                     *, pick: str = "earliest") -> dict:
    """Pure backward induction in TODAY's dollars (real). ``income``: flat annual amount or {year: amount}.

    Each TIPS holding's real principal P pays P×(1+c·n/2) in its maturity year (n = coupons that
    calendar year) and P×c in every earlier year. Gap years (no TIPS maturing) are pre-funded
    with extra of the bracketing TIPS, split by maturity-time weights (duration match) at their PV.
    """
    by_year: dict[int, list[dict]] = {}
    for r in tips_rows:
        y = int(r["maturity"][:4])
        by_year.setdefault(y, []).append(r)
    chosen: dict[int, dict] = {}
    for y, rs in by_year.items():
        rs = sorted(rs, key=lambda r: r["maturity"])
        chosen[y] = rs[0] if pick == "earliest" else rs[-1]
    need_of = (lambda yr: float(income.get(yr, 0.0))) if isinstance(income, dict) else (lambda yr: float(income))
    years = list(range(first_year, last_year + 1))
    principal: dict[int, float] = {}
    extra: dict[int, float] = {}
    gaps: list[dict] = []
    today = mkt.us_today()
    for yr in reversed(years):
        # coupons from every LATER holding — income rungs AND gap-hedge extras — fund this year
        later_cpn = sum((principal.get(j, 0.0) + extra.get(j, 0.0)) * chosen[j]["coupon_pct"] / 100.0
                        for j in set(principal) | set(extra) if j > yr)
        need = need_of(yr) - later_cpn
        if yr in chosen:
            r = chosen[yr]
            mat = date.fromisoformat(r["maturity"])
            n = _coupons_in_maturity_year(mat)
            principal[yr] = max(0.0, need / (1 + r["coupon_pct"] / 100.0 * n / 2))
        else:
            lo = max((y for y in chosen if y < yr), default=None)
            hi = min((y for y in chosen if y > yr), default=None)
            g = {"year": yr, "need": max(0.0, need), "lower": lo, "upper": hi}
            gaps.append(g)
            _fund_gap(g, chosen, extra, today)
    return _tips_result(chosen, principal, extra, gaps, years)


def _fund_gap(g: dict, chosen: dict, extra: dict, today: date) -> None:
    """Duration-matched bracket funding for one gap year (PV at the bracket bonds' real yields)."""
    lo, hi = g["lower"], g["upper"]
    tg = g["year"] + 0.04 - today.year - (today.timetuple().tm_yday / 365.25)
    if lo is None and hi is None:
        return
    if lo is None or hi is None:
        b = lo if lo is not None else hi
        wts = {b: 1.0}
    else:
        t_lo = bm.year_frac(today, date.fromisoformat(chosen[lo]["maturity"]))
        t_hi = bm.year_frac(today, date.fromisoformat(chosen[hi]["maturity"]))
        w_hi = min(1.0, max(0.0, (tg - t_lo) / (t_hi - t_lo))) if t_hi > t_lo else 0.5
        wts = {lo: 1 - w_hi, hi: w_hi}
    y_g = bm.interp([(bm.year_frac(today, date.fromisoformat(chosen[b]["maturity"])), (chosen[b].get("real_yield_pct") or 0) / 100)
                     for b in wts], tg) or 0.0
    pv = g["need"] / (1 + y_g) ** max(0.0, tg)
    g["pv"] = pv
    g["split"] = {}
    for b, w in wts.items():
        px = (chosen[b]["price"] + (chosen[b].get("accrued") or 0.0)) / 100.0
        add = pv * w / px if px else 0.0
        extra[b] = extra.get(b, 0.0) + add
        g["split"][b] = round(w, 4)


def _tips_result(chosen: dict, principal: dict, extra: dict, gaps: list, years: list[int]) -> dict:
    rungs = []
    for yr in years:
        if yr not in chosen:
            continue
        r = chosen[yr]
        p_ = principal.get(yr, 0.0) + extra.get(yr, 0.0)
        if p_ <= 1e-9:
            continue
        px = (r["price"] + (r.get("accrued") or 0.0)) / 100.0
        rungs.append({"year": yr, "cusip": r["cusip"], "maturity": r["maturity"], "coupon_pct": r["coupon_pct"],
                      "real_yield_pct": r.get("real_yield_pct"), "price": r["price"], "index_ratio": r.get("index_ratio"),
                      "real_principal": principal.get(yr, 0.0), "gap_hedge_principal": extra.get(yr, 0.0),
                      "cost": (principal.get(yr, 0.0) + extra.get(yr, 0.0)) * px})
    return {"rungs": rungs, "gaps": gaps, "chosen_years": sorted(chosen)}


async def build_tips_ladder(params: dict, profile: dict) -> dict:
    today = mkt.us_today()
    p = {"mode": "income", "annual_income": 40000.0, "amount": None, "first_year": today.year + 1,
         "last_year": today.year + 20, "account_type": "ira", "pick": "earliest", **(params or {})}
    cat = await mkt.tips_catalogue()
    if not cat:
        return {"error": "TIPS catalogue unavailable (TreasuryDirect) — try again shortly.", "rungs": []}
    rows = [r for r in cat["rows"] if r.get("real_yield_pct") is not None and r.get("index_ratio")]
    fy, ly = int(p["first_year"]), int(p["last_year"])
    avail = sorted({int(r["maturity"][:4]) for r in rows})
    if not avail:
        return {"error": "No priced TIPS available.", "rungs": []}
    fy = max(fy, avail[0])
    ly = min(ly, avail[-1])
    if fy > ly:
        return {"error": f"TIPS mature between {avail[0]} and {avail[-1]} — pick years in that range.", "rungs": []}
    unit = tips_ladder_math(rows, fy, ly, 1.0, pick=p["pick"])
    unit_cost = sum(r["cost"] for r in unit["rungs"])
    if p["mode"] == "budget" and p.get("amount"):
        income = float(p["amount"]) / unit_cost if unit_cost else 0.0
    else:
        income = float(p["annual_income"] or 0)
    res = tips_ladder_math(rows, fy, ly, income, pick=p["pick"])
    rc = await mkt.rate_context()
    infl, infl_src = ps.inflation_assumption(profile, rc)
    rungs = []
    total = 0.0
    for r in res["rungs"]:
        ir = r["index_ratio"] or 1.0
        real_p = r["real_principal"] + r["gap_hedge_principal"]
        face = _floor_to(real_p / ir, 100.0)            # original-principal face to order
        px = (r["price"] + (next((x.get("accrued") for x in rows if x["cusip"] == r["cusip"]), 0.0) or 0.0)) / 100.0
        cost = face * ir * px
        total += cost
        src = next((x for x in rows if x["cusip"] == r["cusip"]), {})
        rungs.append({**{k: r[k] for k in ("year", "cusip", "maturity", "coupon_pct", "real_yield_pct", "price", "index_ratio")},
                      "face_to_buy": face, "adjusted_principal": round(face * ir, 2), "cost": round(cost, 2),
                      "income_principal": round(r["real_principal"], 2), "gap_hedge": round(r["gap_hedge_principal"], 2),
                      "holding": {"kind": "tips", "cusip": r["cusip"], "issuer": "U.S. Treasury",
                                  "label": f"TIPS {r['coupon_pct']:.3f}% {r['maturity']}", "face_value": face,
                                  "coupon_rate": r["coupon_pct"], "coupon_freq": 2, "day_count": "ACT/ACT",
                                  "issue_date": src.get("dated_date"), "maturity_date": r["maturity"],
                                  "purchase_date": today.isoformat(), "purchase_price": r["price"],
                                  "tips_ref_cpi": src.get("ref_cpi"), "rating": "AA+",
                                  "account_type": (p.get("account_type") or "ira").lower()}})
    # real income by year (today's $) produced by the purchased faces — the check that the ladder works
    cash_real: dict[int, float] = {y: 0.0 for y in range(fy, ly + 1)}
    for r in rungs:
        mat = date.fromisoformat(r["maturity"])
        adj = r["adjusted_principal"]
        c = r["coupon_pct"] / 100.0
        # the gap-hedge slice is swapped out before maturity, so only the income slice pays out at maturity
        denom = r["income_principal"] + r["gap_hedge"]
        adj_income = adj * (r["income_principal"] / denom) if denom else 0.0
        for y in range(today.year + 1, mat.year + 1):
            if y not in cash_real:
                continue
            if y < mat.year:
                cash_real[y] += adj * c
            else:
                cash_real[y] += adj_income * (1 + c * _coupons_in_maturity_year(mat) / 2)
    gap_years = {g["year"] for g in res["gaps"]}
    yearly = []
    for y in range(fy, ly + 1):
        real_amt = cash_real.get(y, 0.0)
        if y in gap_years:
            real_amt = income  # funded by the bracket hedge (sold/swapped when gap TIPS are issued)
        nominal = real_amt * infl.factor(y - today.year)
        yearly.append({"year": y, "real_income": round(real_amt, 2), "nominal_income": round(nominal, 2),
                       "gap_year": y in gap_years})
    ryw = ps._wavg_vals([(r["real_yield_pct"], r["cost"]) for r in rungs])
    summary = {"rungs": len(rungs), "invested": round(total, 2), "annual_real_income": round(income, 2),
               "real_yield_pct": ps._r(ryw, 3), "first_maturity": rungs[0]["maturity"] if rungs else None,
               "last_maturity": rungs[-1]["maturity"] if rungs else None}
    return {"summary": summary,
        "params": p, "as_of": cat["as_of"], "annual_real_income": round(income, 2), "total_cost": round(total, 2),
        "first_year": fy, "last_year": ly, "real_yield_pct": ps._r(ryw, 3), **ps.inflation_fields(infl, infl_src), "rungs": rungs, "yearly": yearly,
        "gaps": [{"year": g["year"], "lower": g["lower"], "upper": g["upper"], "split": g.get("split")} for g in res["gaps"]],
        "notes": [
            "Income is in TODAY's dollars: each year's payout rises with CPI-U (index ratio).",
            "Gap years have no TIPS maturing; they're pre-funded with extra of the bracketing TIPS (maturity-weighted). "
            "Swap that excess into new 10-year TIPS as they're auctioned (Jan/Jul) to close the gap.",
            "Hold TIPS in an IRA if you can — in taxable accounts the inflation accretion is taxed every year.",
            "Prices are TreasuryDirect FedInvest end-of-day real clean prices; your broker's ask will be slightly higher.",
        ],
    }


# ---------------------------------------------------------------------------
# Rolling-ladder reinvestment simulation
# ---------------------------------------------------------------------------
def simulate_rolling(rungs: list[dict], nominal_points: list[tuple[float, float]], *, years: int = 15,
                     ladder_length: float | None = None, spreads: dict[float, float] | None = None,
                     tax_rate: float = 0.0) -> dict:
    """Income path of a rolling ladder under 4 curve scenarios.

    ``rungs``: [{face, coupon (decimal), t (years to maturity)}]. When a rung matures its
    principal buys a new rung at the far end (``ladder_length``) at that scenario's yield.
    ``spreads``: {tenor: spread over Treasury} for the rung type (held constant).
    """
    L = ladder_length or max((r["t"] for r in rungs), default=10.0)
    zeros = bm.bootstrap_zero_curve(nominal_points) if nominal_points else []
    sp = spreads or {}

    def y_at(scn: str, T: float, tenor: float) -> float:
        base_today = bm.interp(nominal_points, tenor) or 0.04
        s = bm.interp(sorted(sp.items()), tenor) if sp else 0.0
        if scn == "flat":
            b = base_today
        elif scn == "up100":
            b = base_today + 0.01
        elif scn == "down100":
            b = max(0.0, base_today - 0.01)
        else:  # forwards
            b = bm.forward_rate(zeros, min(T, 29.0 - tenor) if tenor < 29 else 0.0, tenor) or base_today
        return b + (s or 0.0)

    out = {}
    for scn in ("flat", "up100", "down100", "forwards"):
        book = [dict(r) for r in rungs]
        path = []
        for yr in range(1, years + 1):
            income = 0.0
            matured = []
            for r in book:
                if r["t"] <= yr - 1:
                    continue
                if r["t"] <= yr:
                    income += r["face"] * r["coupon"] * max(0.0, r["t"] - (yr - 1))
                    matured.append(r)
                else:
                    income += r["face"] * r["coupon"]
            for r in matured:
                y = y_at(scn, r["t"], L)
                book.append({"face": r["face"], "coupon": y, "t": r["t"] + L})
                r["t"] = -1
            book = [r for r in book if r["t"] > yr - 1 or r["t"] == -1]
            book = [r for r in book if r["t"] != -1]
            live = [r for r in book if r["t"] > yr]
            tot_face = sum(r["face"] for r in live)
            avg_y = sum(r["face"] * r["coupon"] for r in live) / tot_face if tot_face else 0.0
            path.append({"year": yr, "income": round(income, 2), "income_after_tax": round(income * (1 - tax_rate), 2),
                         "ladder_yield_pct": round(avg_y * 100, 3)})
        out[scn] = path
    return {"scenarios": out, "ladder_length_years": round(L, 2),
            "labels": {"flat": "Curve unchanged", "up100": "Rates +1%", "down100": "Rates −1%",
                       "forwards": "Market-implied forwards"}}


async def simulate_ladder(ladder: dict, profile: dict, *, years: int = 15) -> dict:
    """Simulate a built ladder payload (``build_ladder`` output)."""
    today = mkt.us_today()
    mi = await mkt.market_inputs()
    npts = (mi.get("nominal") or {}).get("points") or []
    rungs = []
    kinds = set()
    for r in ladder.get("rungs", []):
        mat = r.get("maturity")
        if not mat:
            continue
        t = bm.year_frac(today, date.fromisoformat(mat))
        face = r.get("face") or r.get("cost") or 0.0
        cpn = (r.get("coupon_pct") if r.get("coupon_pct") is not None else r.get("yield_pct") or 0.0) / 100.0
        if r.get("kind") == "etf":
            cpn = (r.get("yield_pct") or 0) / 100.0
            face = r.get("cost") or 0.0
        rungs.append({"face": face, "coupon": cpn, "t": t})
        kinds.add(r.get("kind"))
    kind = next(iter(kinds)) if len(kinds) == 1 else "treasury"
    spreads = {}
    if kind not in ("treasury", "etf"):
        for tn in (1, 2, 3, 5, 7, 10, 20, 30):
            y, _ = mkt.model_yield(kind, tn, mi, "A")
            b = bm.interp(npts, tn)
            if y is not None and b is not None:
                spreads[float(tn)] = y - b
    L = float(ladder.get("params", {}).get("end_years") or 10)
    rates = ps.tax_rates(profile)
    acct = (ladder.get("params", {}).get("account_type") or "taxable").lower()
    tr, _ = ps.tax_treatment({"kind": kind if kind != "etf" else "treasury", "account_type": acct,
                              "state": (profile or {}).get("state")}, profile)
    return simulate_rolling(rungs, npts, years=years, ladder_length=L, spreads=spreads,
                            tax_rate=bm.interest_tax_rate(rates, tr))


# ---------------------------------------------------------------------------
# Saved-ladder status vs linked holdings
# ---------------------------------------------------------------------------
def ladder_status(plan: dict, linked_rows: list[dict], *, today: date | None = None) -> dict:
    """Per planned rung: funded (bought ≥90%) / partial / planned (only on the watchlist) / missing / matured."""
    today = today or mkt.us_today()
    rungs = plan.get("rungs") or []
    status = []
    used: set = set()
    for r in rungs:
        mat = r.get("maturity")
        y = int(mat[:4]) if mat else None
        target = r.get("face") or r.get("face_to_buy") or r.get("cost") or 0.0
        match = [h for h in linked_rows if h.get("id") not in used and (
            (r.get("cusip") and h.get("cusip") == r.get("cusip")) or
            (r.get("ticker") and (h.get("ticker") or "").upper() == (r.get("ticker") or "").upper()) or
            (not r.get("cusip") and not r.get("ticker") and y and h.get("maturity_year") == y))]
        held = planned = 0.0
        for h in match:
            used.add(h.get("id"))
            amt = h.get("face") or h.get("market_value") or 0.0
            if h.get("status") == "watch":
                planned += amt
            else:
                held += amt
        pct = held / target if target else 0.0
        matured = bool(mat) and date.fromisoformat(mat) <= today
        if matured:
            st = "matured"
        elif pct >= 0.9:
            st = "funded"
        elif pct > 0:
            st = "partial"
        elif planned > 0:
            st = "planned"
        else:
            st = "missing"
        status.append({"index": r.get("index", r.get("year")), "maturity": mat, "label": r.get("label") or r.get("cusip"),
                       "planned": target, "held": round(held, 2), "on_watchlist": round(planned, 2),
                       "funded_pct": round(100 * pct, 1), "status": st, "holding_ids": [h.get("id") for h in match],
                       "maturing_soon": bool(mat) and not matured and (date.fromisoformat(mat) - today).days <= 90})
    target_tot = sum(s["planned"] for s in status)
    held_tot = sum(min(s["held"], s["planned"]) for s in status)
    return {"rungs": status, "funded_pct": round(100 * held_tot / target_tot, 1) if target_tot else 0.0,
            "missing": sum(1 for s in status if s["status"] in ("missing", "planned")),
            "maturing_soon": [s for s in status if s["maturing_soon"]],
            "unmatched_holdings": [h.get("id") for h in linked_rows if h.get("id") not in used]}


# ---------------------------------------------------------------------------
# Goals / liability matching
# ---------------------------------------------------------------------------
def _goal_needs(goals: list[dict], infl, today: date, horizon_end: int) -> dict[int, list[dict]]:
    needs: dict[int, list[dict]] = {}
    for g in goals or []:
        try:
            y0 = int(g.get("year"))
        except (TypeError, ValueError):
            continue
        y1 = int(g.get("end_year") or y0)
        amt = float(g.get("amount") or 0)
        real = bool(g.get("inflation_adjusted"))
        for y in range(y0, min(y1, horizon_end) + 1):
            nominal = amt * bm.as_path(infl).factor(max(0, y - today.year)) if real else amt
            needs.setdefault(y, []).append({"goal": g.get("name") or "Goal", "nominal": nominal, "real": real})
    return needs


TRADITIONAL = {"ira", "401k", "403b"}      # withdrawals taxed as ordinary income
SHELTERED = {"ira", "401k", "403b", "roth", "hsa", "529"}
RATE_RISK_PER_YEAR = 0.01                   # 1 year of duration mismatch ≈ 1% of the sale at risk per 1% rate move
CASH_LIKE_DURATION = 0.5                    # box / T-bill / floating-rate funds: same rate exposure as cash in T-bills


def _years_to(today: date, year: int) -> float:
    """Time from today to the middle of ``year`` (the current year: middle of what's left)."""
    if year <= today.year:
        return max(0.05, (date(today.year, 12, 31) - today).days / 365.25 / 2)
    return (date(year, 7, 1) - today).days / 365.25


EARLY_PENALTY = 0.10        # retirement-account withdrawals before 59½ (IRA/401k/403b; Roth EARNINGS)
EARLY_AGE_YEAR = 60         # 59½ falls in the year you turn 59 or 60 → penalty-free from the year you turn 60


def early_until_year(mode: str, birth_year: int | None, years: list[int]) -> int | None:
    """Last calendar year whose retirement-account withdrawals count as EARLY (before 59½), or None.
    ``mode``: "age" (use ``birth_year``), "before" (treat every year as early), "after" (never early)."""
    if mode == "before":
        return years[-1] if years else None
    if mode == "age" and birth_year:
        return int(birth_year) + EARLY_AGE_YEAR - 1
    return None


def _fund_tax_fields(acct: str, gain: float, rates: bm.TaxRates, tr: bm.TaxTreatment) -> dict:
    ordinary = rates.fed + rates.state
    cg = rates.ltcg + rates.niit + rates.state
    early_sale = early_dist = 0.0
    if acct in TRADITIONAL:
        sale_tax, tax_now, dist_tax = ordinary, 0.0, ordinary
        early_sale = early_dist = EARLY_PENALTY
    elif acct == "roth":
        sale_tax, tax_now, dist_tax = 0.0, 0.0, 0.0
        early_sale = max(0.0, gain) * (ordinary + EARLY_PENALTY)
        early_dist = ordinary + EARLY_PENALTY
    elif acct in SHELTERED:
        sale_tax, tax_now, dist_tax = 0.0, 0.0, 0.0
    else:
        sale_tax = tax_now = max(-0.4, min(0.6, gain * cg))
        dist_tax = bm.interest_tax_rate(rates, tr)
    return {"sale_tax": sale_tax, "tax_now": tax_now, "dist_tax": dist_tax,
            "early_sale_extra": early_sale, "early_dist_extra": early_dist,
            "cg": cg if acct not in SHELTERED else 0.0,
            "shelter_rate": (rates.fed + rates.niit + rates.state) if acct in SHELTERED else 0.0}


def fund_sale_profile(row: dict, profile: dict) -> dict:
    """How selling one open-ended bond fund behaves: duration, yield, and what tax does to each $.

    * ``yield`` = its total yield; ``cash_yield`` = what it pays out; ``accrual`` = the rest, which builds
      up in the share price (all of it for an accumulating fund like a box-spread ETF) and is only
      realized — as a capital gain — when sold.
    * ``sale_tax``    — fraction of a GROSS sale lost to tax: taxable = gain share × (LTCG+NIIT+state)
      (negative for a loss = tax saved; the gain share GROWS as the price accrues); traditional IRA/401k =
      ordinary rate (every withdrawal is income); Roth/HSA/529 = 0.
    * ``tax_now``     — the part of that tax that depends on WHEN you sell (only taxable gains — IRA tax is
      owed whenever you withdraw, so it can't be avoided by choosing a different year).
    * ``dist_tax``    — tax on its distributions when spent (taxable: its interest rate; traditional: ordinary).
    * ``early_sale_extra`` / ``early_dist_extra`` — EXTRA cost of a withdrawal before 59½: traditional = 10%
      penalty; Roth = income tax + 10% on the EARNINGS share (contributions come out free; we treat the
      position's gain as earnings, and distributions as earnings). HSA/529 assumed used for qualified expenses.
    * ``retired`` — the same tax fields at your after-retirement rates, used from ``retired_from``.
    """
    sched = ps.tax_schedule(profile)
    acct = (row.get("account_type") or "taxable").lower()
    mv = row.get("market_value") or 0.0
    cost = row.get("cost_basis")
    gain = ((mv - cost) / mv) if (cost and mv) else 0.0
    tx = row.get("tax") or {}
    tr = bm.TaxTreatment(fed_taxable=tx.get("fed_taxable", True), state_taxable=tx.get("state_taxable", True),
                         taxable_account=acct not in SHELTERED)
    now = _fund_tax_fields(acct, gain, sched.now, tr)
    if "rate_pct" in tx and acct not in SHELTERED:
        now["dist_tax"] = (tx.get("rate_pct") or 0.0) / 100.0        # the holding's own rate (as analyzed)
    fund = row.get("fund") or {}
    y = (row.get("ytw_pct") or 0.0) / 100.0
    cash = fund.get("cash_yield_pct")
    cash = y if cash is None else cash / 100.0
    dur = row.get("eff_duration")
    return {
        "id": row.get("id"), "label": row.get("label"), "ticker": row.get("ticker"), "account_type": acct,
        "value": mv, "yield": y, "cash_yield": cash, "accrual": y - cash,
        "accumulates": fund.get("payout") == "accumulates", "non_usd": bool(fund.get("non_usd")),
        "duration": dur if dur is not None else 5.0,         # 0 is real (box / T-bill / floating-rate funds)
        "duration_estimated": dur is None or fund.get("duration_confidence") in (None, "low", "unverified"),
        "duration_source": fund.get("duration_source"),
        "gain_pct": round(gain * 100, 2), "cost_known": bool(cost),
        "cost_ratio": (cost / mv) if (cost and mv) else None,
        **now,
        "retired": _fund_tax_fields(acct, gain, sched.retired, tr) if sched.retired else None,
        "retired_from": sched.from_year,
        "expense_ratio_pct": fund.get("expense_ratio_pct"),
    }


def _tx(f: dict, y: int) -> dict:
    """The fund's tax fields for year ``y`` (after-retirement rates from the retirement year)."""
    if f.get("retired") and f.get("retired_from") is not None and y >= f["retired_from"]:
        return f["retired"]
    return f


def _gain_tax(f: dict, y: int, g: float = 1.0) -> float:
    """Tax per GROSS $ sold that depends on the gain — for a taxable fund whose price has grown by ``g``
    since today the gain share is 1 − cost/(value·g)."""
    tx = _tx(f, y)
    if f.get("cost_ratio") is None or not tx.get("cg") or f["account_type"] in SHELTERED:
        return tx["tax_now"]
    return max(-0.4, min(0.6, (1 - f["cost_ratio"] / max(g, 1e-9)) * tx["cg"]))


def _is_early(y: int, early_until: int | None) -> bool:
    return early_until is not None and y <= early_until


def _is_retirement(f_or_acct) -> bool:
    acct = f_or_acct if isinstance(f_or_acct, str) else f_or_acct.get("account_type")
    return acct in TRADITIONAL or acct == "roth"


def _locked(f: dict, y: int, early_until: int | None, lock_early: bool) -> bool:
    """'By my age': retirement-account money stays IN the account (distributions reinvested, no sales)
    until the penalty-free year."""
    return lock_early and _is_retirement(f) and _is_early(y, early_until)


def _value_growth(f: dict, years: list[int], first_frac: float, early_until: int | None, lock_early: bool) -> list[float]:
    """Value multiplier of $1 held today at the START of each year: the price accrual (accumulating funds,
    NAV pull-to-par) every year, plus reinvested distributions while locked inside a retirement account."""
    out, g = [], 1.0
    for t, y in enumerate(years):
        out.append(g)
        rate = f["yield"] if _locked(f, y, early_until, lock_early) else f.get("accrual", 0.0)
        g *= 1 + rate * (first_frac if t == 0 else 1.0)
    return out


_growth_while_locked = _value_growth          # old name


def _keep(f: dict, y: int, after_tax: bool, early_until: int | None, g: float = 1.0) -> float:
    """Net $ received per GROSS $ sold of fund ``f`` in year ``y`` (``g`` = price growth since today)."""
    if not after_tax:
        return 1.0
    tx = _tx(f, y)
    base = _gain_tax(f, y, g) if (f["account_type"] not in SHELTERED) else tx["sale_tax"]
    return 1.0 - base - (tx["early_sale_extra"] if _is_early(y, early_until) else 0.0)


def _dist_net(f: dict, y: int, after_tax: bool, early_until: int | None) -> float:
    if not after_tax:
        return 1.0
    tx = _tx(f, y)
    return 1.0 - tx["dist_tax"] - (tx["early_dist_extra"] if _is_early(y, early_until) else 0.0)


def sale_cost(f: dict, t: float, plan_end_t: float, y: int | None = None, g: float = 1.0) -> float:
    """% of a GROSS sale of fund ``f`` at ``t`` years out (calendar year ``y``) that is lost or put at risk:
    rate risk (1% per year of duration mismatch) + tax triggered by selling now + sheltered growth given up."""
    left = max(0.0, plan_end_t - t)
    yy = y if y is not None else 0
    # a cash-like fund held until the need carries exactly the rate exposure of the T-bills the cash would
    # otherwise sit in → no mismatch charge (else the plan sells it early only to park the cash in taxable bills)
    mismatch = 0.0 if f["duration"] < CASH_LIKE_DURATION else abs(t - f["duration"])
    tx = _tx(f, yy)
    tax_now = _gain_tax(f, yy, g) if f["account_type"] not in SHELTERED else tx["tax_now"]
    return RATE_RISK_PER_YEAR * mismatch + tax_now + tx["shelter_rate"] * f["yield"] * left


def reinvest_factor(rate: float, tenor: int) -> float:
    """Growth of $1 set aside in a Treasury maturing ``tenor`` years later (semiannual compounding)."""
    return (1 + rate / 2) ** (2 * tenor)


def solve_cash_plan(years: list[int], today: date, bond_inflow: dict[int, float], need: dict[int, float],
                    funds: list[dict], *, after_tax: bool, carry_rate: float, plan_end: int,
                    sale_years: set[int] | None = None, early_until: int | None = None,
                    reinvest_rate=None, reinvest_targets: set[int] | None = None,
                    reinvest_sources_exclude: set[int] | None = None,
                    lock_early: bool = False) -> dict | None:
    """Optimal cash plan as ONE linear program over all years: which funds to SELL in which gap years, and
    how much SURPLUS (years with more bond cash than needed) to put into Treasuries maturing in a later
    gap year instead of rolling T-bills — solved TOGETHER so a Treasury arriving in a gap year replaces fund
    sales there instead of piling on top of them.

    min  Σ (1 + sale_cost + early-withdrawal cost) × gross sold  +  50·1.25^(years-earlier) × shortfall
         − 0.001 × cash left at the end            (tie-break: prefer the higher-earning path)
    s.t. each year: bond cash + distributions on what's still held + carry×(1+T-bill) + net sales
                    + reinvestments maturing − reinvestments made + shortfall − carry_out = need
         Σ sold/G ≤ fund value (G = the fund's price growth: accumulating funds / reinvested distributions);
         fund sales + reinvestment arriving in a year ≤ that year's need (no year is double-funded);
         reinvestment arriving ≤ what that year's OWN bond cash doesn't cover (the year spends its own coupons
         and maturities first — the Treasury fills the rest);
         never reinvest out of a year in which funds may be sold (no sell-and-buy churn).

    ``reinvest_rate(tenor)`` → (after-tax) yield of a Treasury of that tenor (today's curve, rates unchanged).
    Returns {"sales": {(fund_id, year): gross}, "reinvest": {(from_year, to_year): amount}} or None (no SciPy).
    """
    try:
        import numpy as np
        from scipy.optimize import linprog
    except Exception:  # noqa: BLE001
        return None
    F, T = len(funds), len(years)
    targets = reinvest_targets or set()
    no_src = reinvest_sources_exclude or set()
    pairs = [(i, j) for i in range(T) for j in range(i + 1, T)
             if reinvest_rate is not None and years[j] in targets and need.get(years[j], 0) > 0
             and years[i] not in no_src]
    if not F and not pairs:
        return {"sales": {}, "reinvest": {}}
    nx, nz = F * T, len(pairs)
    N = nx + nz + 2 * T
    ix = lambda f, t: f * T + t          # noqa: E731
    iz = lambda k: nx + k                # noqa: E731
    ic = lambda t: nx + nz + t           # noqa: E731
    js = lambda t: nx + nz + T + t       # noqa: E731
    first_frac = max(0.0, (date(today.year, 12, 31) - today).days / 365.25)
    G = [_value_growth(f, years, first_frac, early_until, lock_early) for f in funds]
    keep = [[_keep(f, y, after_tax, early_until, G[i][t]) for t, y in enumerate(years)] for i, f in enumerate(funds)]
    # locked retirement funds pay no cash before 59½ (distributions reinvested → the fund grows)
    dist = [[0.0 if _locked(f, y, early_until, lock_early)
             else f["cash_yield"] * (first_frac if t == 0 else 1.0) * _dist_net(f, y, after_tax, early_until)
             for t, y in enumerate(years)] for f in funds]
    R = [reinvest_factor(reinvest_rate(years[j] - years[i]), years[j] - years[i]) for i, j in pairs]
    plan_end_t = plan_end + 0.5 - today.year
    cvec = np.zeros(N)
    for f in range(F):
        for t, y in enumerate(years):
            early = (_tx(funds[f], y)["early_sale_extra"] if (after_tax and _is_early(y, early_until)) else 0.0)
            cvec[ix(f, t)] = 1.0 + sale_cost(funds[f], _years_to(today, y), plan_end_t, y, G[f][t]) + early
    for t in range(T):
        # Goals are met IN TIME ORDER: a shortfall one year earlier costs 25% more — faster than any bond can
        # compound — so money is never moved (sold early / reinvested) to a later goal at a nearer goal's expense,
        # and an unavoidable gap lands as late as possible (most time to fix it).
        cvec[js(t)] = 50.0 * 1.25 ** (T - 1 - t)
    cvec[ic(T - 1)] = -0.001
    A_eq = np.zeros((T, N))
    b_eq = np.zeros(T)
    for t, y in enumerate(years):
        for f in range(F):
            A_eq[t, ix(f, t)] += keep[f][t]
            for tau in range(t):          # $1 sold in tau would have grown to G_t/G_tau by now
                A_eq[t, ix(f, tau)] -= dist[f][t] * G[f][t] / G[f][tau]
        for k, (i, j) in enumerate(pairs):
            if i == t:
                A_eq[t, iz(k)] -= 1.0
            if j == t:
                A_eq[t, iz(k)] += R[k]
        if t > 0:
            A_eq[t, ic(t - 1)] = 1 + carry_rate
        A_eq[t, ic(t)] = -1.0
        A_eq[t, js(t)] = 1.0
        b_eq[t] = need.get(y, 0.0) - bond_inflow.get(y, 0.0) - sum(dist[f][t] * funds[f]["value"] * G[f][t] for f in range(F))
    ub_rows, ub_b = [], []
    for f in range(F):
        row = np.zeros(N)
        for t in range(T):
            row[ix(f, t)] = 1.0 / G[f][t]
        ub_rows.append(row)
        ub_b.append(funds[f]["value"])
    for t, y in enumerate(years):
        row = np.zeros(N)
        for f in range(F):
            row[ix(f, t)] = keep[f][t]
        for k, (i, j) in enumerate(pairs):
            if j == t:
                row[iz(k)] = R[k]
        ub_rows.append(row)
        ub_b.append(need.get(y, 0.0))
        if any(j == t for _, j in pairs):
            row2 = np.zeros(N)
            for k, (i, j) in enumerate(pairs):
                if j == t:
                    row2[iz(k)] = R[k]
            ub_rows.append(row2)
            ub_b.append(max(0.0, need.get(y, 0.0) - bond_inflow.get(y, 0.0)))
    bounds = ([(0.0, 0.0 if (keep[f][t] <= 0 or (sale_years is not None and years[t] not in sale_years)
                              or _locked(funds[f], years[t], early_until, lock_early)) else None)
               for f in range(F) for t in range(T)]
              + [(0.0, None)] * nz + [(0.0, None)] * (2 * T))
    try:
        res = linprog(cvec, A_ub=np.asarray(ub_rows) if ub_rows else None, b_ub=np.asarray(ub_b) if ub_b else None,
                      A_eq=A_eq, b_eq=b_eq, bounds=bounds, method="highs")
    except Exception:  # noqa: BLE001
        return None
    if not res.success:
        return None
    return {
        "sales": {(funds[f]["id"], years[t]): float(res.x[ix(f, t)])
                  for f in range(F) for t in range(T) if res.x[ix(f, t)] > 0.5},
        "reinvest": {(years[i], years[j]): float(res.x[iz(k)]) for k, (i, j) in enumerate(pairs) if res.x[iz(k)] > 0.5},
    }


def solve_fund_sales(years: list[int], today: date, bond_inflow: dict[int, float], need: dict[int, float],
                     funds: list[dict], *, after_tax: bool, carry_rate: float, plan_end: int,
                     allowed_years: set[int] | None = None) -> dict | None:
    """Fund-sale part of :func:`solve_cash_plan` (no reinvestment) → {(fund_id, year): gross}."""
    out = solve_cash_plan(years, today, bond_inflow, need, funds, after_tax=after_tax, carry_rate=carry_rate,
                          plan_end=plan_end, sale_years=allowed_years)
    return None if out is None else out["sales"]


def simulate_goal_funding(years: list[int], today: date, bond_inflow: dict[int, float], need: dict[int, float],
                          funds: list[dict], *, use_funds: bool, after_tax: bool, carry_rate: float,
                          plan_end: int, plan: dict | None = None, reinvest_plan: dict | None = None,
                          reinvest_rate=None, early_until: int | None = None, lock_early: bool = False) -> dict:
    """Year-by-year cash plan. Bonds pay what they pay; open-ended bond funds pay distributions on what's
    left (NAV assumed flat — total return ≈ yield) and, when ``use_funds``, are SOLD only to cover a
    shortfall. ``plan`` (fund sales) and ``reinvest_plan`` (surplus → Treasury maturing in a gap year) come
    from :func:`solve_cash_plan`; without a plan, the cheapest fund is sold first:

        cost = 1% × |years to the need − fund duration|   (rate risk: a fund held ~its duration locks in its yield)
             + tax you pay because you sold NOW            (taxable gains; a loss makes this negative = tax saved)
             + sheltered growth given up                   (IRA/Roth: tax rate × yield × years left in the plan)
             + early-withdrawal cost before 59½            (10% penalty; Roth: tax + penalty on earnings)
    """
    rem = {f["id"]: f["value"] for f in funds}
    carry = 0.0
    rows, schedule, reinvests = [], [], []
    pending: dict[int, float] = {}
    first_frac = max(0.0, (date(today.year, 12, 31) - today).days / 365.25)
    plan_end_t = plan_end + 0.5 - today.year
    G = {f["id"]: _value_growth(f, years, first_frac, early_until, lock_early) for f in funds}
    for i, y in enumerate(years):
        frac = first_frac if i == 0 else 1.0
        dist = 0.0
        early_cost = 0.0
        fund_tax = 0.0                                        # tax (+ early penalties) on distributions and sales
        for f in funds:
            if _locked(f, y, early_until, lock_early):
                continue                                      # stays in the IRA/Roth, reinvested (growth below)
            gross_d = rem[f["id"]] * f["cash_yield"] * frac
            dist += gross_d * _dist_net(f, y, after_tax, early_until)
            fund_tax += gross_d * (1 - _dist_net(f, y, after_tax, early_until))
            if after_tax and _is_early(y, early_until):
                early_cost += gross_d * _tx(f, y)["early_dist_extra"]
        bonds = bond_inflow.get(y, 0.0)
        n = need.get(y, 0.0)
        reinv_in = pending.pop(y, 0.0)
        avail = bonds + dist + carry + reinv_in
        sold_net = 0.0
        t = _years_to(today, y)
        early_now = _is_early(y, early_until) and after_tax

        def cost(f):
            return sale_cost(f, t, plan_end_t, y, G[f["id"]][i]) + (_tx(f, y)["early_sale_extra"] if early_now else 0.0)
        orders: list[tuple[dict, float | None]] = []
        if use_funds and plan is not None:
            orders += [(f, plan.get((f["id"], y), 0.0)) for f in funds if plan.get((f["id"], y), 0.0) > 0.5]
        if use_funds:
            orders += [(f, None) for f in sorted(funds, key=lambda f: (cost(f), -(f["expense_ratio_pct"] or 0)))]
        short = n - avail
        for f, planned in orders:
            g_f = G[f["id"]][i]
            keep = _keep(f, y, after_tax, early_until, g_f)
            if keep <= 0 or rem[f["id"]] <= 0.005 or _locked(f, y, early_until, lock_early):
                continue
            if planned is not None:
                gross = min(rem[f["id"]], planned)
            else:
                if short <= 0.005:
                    break
                gross = min(rem[f["id"]], short / keep)
            if gross <= 0.005:
                continue
            net = gross * keep
            fund_tax += gross - net
            rem[f["id"]] -= gross
            short -= net
            sold_net += net
            tx = _tx(f, y)
            pen = gross * tx["early_sale_extra"] if early_now else 0.0
            early_cost += pen
            base_tax = _gain_tax(f, y, g_f) if f["account_type"] not in SHELTERED else tx["sale_tax"]
            gain_now = ((1 - f["cost_ratio"] / g_f) * 100) if f.get("cost_ratio") is not None else f["gain_pct"]
            cash_like = f["duration"] < CASH_LIKE_DURATION
            why = ([f"cash-like (duration {f['duration']:.1f}y) — grows at short-term rates until needed"] if cash_like else
                   [f"duration {f['duration']:.1f}y{' (est.)' if f['duration_estimated'] else ''} vs need in {t:.1f}y"])
            if f["account_type"] in TRADITIONAL:
                why.append("IRA/401k withdrawal taxed as income" + (" + 10% early-withdrawal penalty" if pen else ""))
            elif f["account_type"] == "roth":
                why.append("Roth withdrawal" + (" before 59½ — earnings taxed + 10%" if pen else " — tax-free"))
            elif f["account_type"] in SHELTERED:
                why.append("tax-free withdrawal")
            elif base_tax < 0:
                why.append(f"sells at a loss ({gain_now:+.1f}%) → tax saved")
            elif base_tax > 0:
                why.append(f"gain {gain_now:+.1f}%{' (built up in the price)' if f.get('accumulates') else ''} → {base_tax * 100:.1f}% tax")
            if f.get("retired") and f.get("retired_from") is not None and y >= f["retired_from"] and f["account_type"] != "roth":
                why.append("after-retirement tax rates")
            schedule.append({
                "year": y, "holding_id": f["id"], "label": f["label"], "ticker": f["ticker"],
                "account_type": f["account_type"], "gross": round(gross, 2),
                "tax": round(gross * (base_tax + (tx["early_sale_extra"] if early_now else 0.0)), 2),
                "early_penalty": round(pen, 2),
                "net": round(net, 2), "duration": round(f["duration"], 2), "years_to_need": round(t, 2),
                "mismatch_years": round(abs(t - f["duration"]), 2),
                # value change of what you'll sell if rates jump +1% today (held until the need)
                "rate_impact_1pct": 0.0 if cash_like else round(gross * (t - f["duration"]) * 0.01, 2),
                "cost_pct": round(100 * cost(f), 2), "reason": " · ".join(why),
            })
        avail += sold_net
        covered = min(avail, n)
        shortfall = max(0.0, n - avail)
        surplus = avail - covered
        reinv_out = 0.0
        for (s_y, g_y), amt in sorted((reinvest_plan or {}).items()):
            if s_y != y or surplus - reinv_out <= 0.5:
                continue
            a = min(amt, surplus - reinv_out)
            rate = reinvest_rate(g_y - s_y) if reinvest_rate else 0.0
            fv = a * reinvest_factor(rate, g_y - s_y)
            pending[g_y] = pending.get(g_y, 0.0) + fv
            reinv_out += a
            reinvests.append({"from_year": s_y, "to_year": g_y, "tenor": g_y - s_y, "amount": round(a, 2),
                              "rate_pct": round(rate * 100, 3), "value_at_maturity": round(fv, 2),
                              "earned": round(fv - a, 2),
                              "vs_tbills": round(fv - a * (1 + carry_rate) ** (g_y - s_y), 2)})
        carry = (surplus - reinv_out) * (1 + carry_rate)
        for f in funds:                                       # what's still held grows (accrual / reinvested dist.)
            if i + 1 < len(years):
                rem[f["id"]] *= G[f["id"]][i + 1] / G[f["id"]][i]
        rows.append({"year": y, "need": round(n, 2), "bond_inflow": round(bonds, 2), "fund_distributions": round(dist, 2),
                     "fund_sales": round(sold_net, 2), "reinvest_in": round(reinv_in, 2), "reinvest_out": round(reinv_out, 2),
                     "inflow": round(bonds + dist, 2), "covered": round(covered, 2), "shortfall": round(shortfall, 2),
                     "surplus_carried": round(carry, 2), "early_cost": round(early_cost, 2), "fund_tax": round(fund_tax, 2)})
    merged: dict[tuple, dict] = {}                       # one row per (year, fund)
    for e in schedule:
        k = (e["year"], e["holding_id"])
        if k in merged:
            m = merged[k]
            for fld in ("gross", "tax", "net", "rate_impact_1pct", "early_penalty"):
                m[fld] = round(m[fld] + e[fld], 2)
        else:
            merged[k] = dict(e)
    return {"rows": rows, "schedule": list(merged.values()), "reinvestments": reinvests,
            "remaining": {fid: round(v, 2) for fid, v in rem.items()},
            "remaining_total": round(sum(rem.values()), 2)}


async def plan_goals(holdings: list[dict], profile: dict, *, after_tax: bool = True, use_funds: bool = True,
                     reinvest: bool = True, withdrawal_mode: str | None = None, light: bool = False,
                     rate_shift: float = 0.0, ingredients: bool = False, fx_drift: float = 0.0) -> dict:
    """Goals vs cash, year by year. Individual bonds pay what they pay; bond ETFs/mutual funds pay
    distributions and (``use_funds``) are sold down in gap years; surplus years (``reinvest``) buy Treasuries
    maturing in later gap years; retirement-account withdrawals follow the 59½ rule (``withdrawal_mode``:
    "age" with the profile's birth year, "before", or "after").

    Scenario hooks (stress tests): the inflation path comes from ``profile`` (so pass a modified one), and
    ``rate_shift`` moves interest rates with it — cash and future reinvestments earn that much more/less, and
    open-ended funds take the price hit (−duration × shift) today and then yield the shift more. Bonds held
    to maturity are untouched: that is exactly what "locking in" a rate means. ``fx_drift`` is how much faster
    (or slower) the dollar loses value than expected: non-dollar bond funds gain that much a year in dollars
    (purchasing-power parity) and don't feel US rate moves. ``light`` skips the
    lever-comparison and claiming-age extras (for running many scenarios). ``ingredients`` returns the
    plan's inputs (needs, income, each holding's after-tax cash by year, fund profiles, rates) instead of
    solving — the whole-book rebalancer optimizes over exactly these."""
    today = mkt.us_today()
    goals = (profile or {}).get("goals") or []
    settings = (profile or {}).get("settings") or {}
    birth_year = settings.get("birth_year")
    try:
        birth_year = int(birth_year) if birth_year else None
    except (TypeError, ValueError):
        birth_year = None
    mode = withdrawal_mode or settings.get("withdrawal_mode") or ("age" if birth_year else "after")
    sched = ps.tax_schedule(profile)
    rates = sched.now
    rc = await mkt.rate_context()
    infl, infl_src = ps.inflation_assumption(profile, rc)
    last_goal = max([int(g.get("end_year") or g.get("year") or today.year) for g in goals] or [today.year + 10])
    n_years = max(5, last_goal - today.year + 1)
    ctx = await ps.load_context(holdings, profile)
    rows_h, internals = ps.analyze_rows(holdings, profile, ctx)
    cash = ps.project_cash_flows(rows_h, internals, ctx.settle, years=n_years, fund_years=n_years, inflation=ctx.inflation,
                                 tax=sched)
    mi = ctx.mi
    npts = (mi.get("nominal") or {}).get("points") or []
    tips_pts = sorted(mi.get("tips_points") or [])
    by_id = {r["id"]: r for r in rows_h}
    years = list(range(today.year, today.year + n_years + 1))
    early_until = early_until_year(mode, birth_year, years)
    # "age": retirement money stays in the account until 59½ (no penalties, grows inside);
    # "before": you withdraw early anyway → 10% penalty (Roth: tax + 10% on earnings)
    lock_early = mode == "age" and early_until is not None

    # open-ended funds (never mature) are modelled explicitly; defined-maturity ETFs stay in the bond flows
    open_funds = [r for r in rows_h if r["status"] == "held" and r["kind"] in ps.FUNDS
                  and not r.get("defined_maturity_year") and (r.get("market_value") or 0) > 0]
    open_ids = {r["id"] for r in open_funds}
    book_value = sum(r.get("market_value") or 0.0 for r in rows_h if r["status"] == "held" and not r.get("matured"))
    ordinary_in = lambda yr: sched.at(yr).fed + sched.at(yr).state   # noqa: E731 — lower once retired
    r_bill = max(0.0, (bm.interp(npts, 1.0) or 0.03) + rate_shift)
    bond_inflow: dict[int, float] = {}
    bond_tax: dict[int, float] = {}
    bond_early: dict[int, float] = {}
    held_back = {"traditional": 0.0, "roth": 0.0}      # retirement cash kept in the account until 59½ ("age")
    by_h: dict[int, dict[int, float]] = {}             # each holding's after-tax cash by year (same amounts)
    held_h: dict[int, dict[str, float]] = {}
    prov: dict[int, float] = {}                         # portfolio income that counts toward Social Security's
    for e in sorted(cash.get("events", []), key=lambda e: e["date"]):      # "provisional income" test
        acct = (by_id.get(e["holding_id"], {}).get("account_type") or "taxable").lower()
        y = int(e["date"][:4])
        if acct not in SHELTERED and e["type"] in ("coupon", "distribution"):
            prov[y] = prov.get(y, 0.0) + e["amount"]                # taxable AND tax-exempt interest both count
        if e["holding_id"] in open_ids:
            continue
        if lock_early and _is_retirement(acct) and _is_early(y, early_until) and e["type"] != "tax":
            # stays inside the IRA/Roth (pre-tax) until the penalty-free year — rolled into a Treasury maturing then
            # (what you'd actually do when a bond matures in an account you can't tap yet), not left in T-bills
            wait = max(0.0, early_until + 1 - y - 0.5)
            grow = (1 + max(0.0, (bm.interp(npts, max(0.25, wait)) or r_bill) + rate_shift)) ** wait
            held_back["traditional" if acct in TRADITIONAL else "roth"] += e["amount"] * grow
            hb = held_h.setdefault(e["holding_id"], {"traditional": 0.0, "roth": 0.0})
            hb["traditional" if acct in TRADITIONAL else "roth"] += e["amount"] * grow
            continue
        early = after_tax and _is_early(y, early_until)
        ordinary = ordinary_in(y)
        extra = 0.0
        if acct in TRADITIONAL and e["type"] != "tax":
            prov[y] = prov.get(y, 0.0) + e["amount"]                # IRA/401k cash spent = a taxable withdrawal
        if not after_tax:
            amt = e["amount"]
        elif acct in TRADITIONAL:
            # spending IRA/401k cash = a taxable withdrawal (+10% before 59½)
            extra = e["amount"] * EARLY_PENALTY if early else 0.0
            amt = e["amount"] * (1 - ordinary) - extra
        elif acct == "roth":
            # Roth: tax-free after 59½; before, EARNINGS (interest, gains) are taxed + 10%
            if early:
                earnings = e["amount"] if e["type"] in ("coupon", "distribution") else (e.get("gain") or 0.0)
                extra = earnings * (ordinary + EARLY_PENALTY)
            amt = e["amount"] - extra
        elif acct in SHELTERED:
            amt = e["amount"]
        else:
            amt = e["after_tax"]
        bond_inflow[y] = bond_inflow.get(y, 0.0) + amt
        hy = by_h.setdefault(e["holding_id"], {})
        hy[y] = hy.get(y, 0.0) + amt
        bond_tax[y] = bond_tax.get(y, 0.0) + (e["amount"] - amt)       # interest/gain/phantom tax, IRA income tax, penalties
        if extra:
            bond_early[y] = bond_early.get(y, 0.0) + extra
    released_year = (early_until + 1) if lock_early else None
    released = 0.0
    if lock_early and (held_back["traditional"] or held_back["roth"]) and released_year <= years[-1]:
        released = held_back["traditional"] * ((1 - ordinary_in(released_year)) if after_tax else 1.0) + held_back["roth"]
        bond_inflow[released_year] = bond_inflow.get(released_year, 0.0) + released
        prov[released_year] = prov.get(released_year, 0.0) + held_back["traditional"]
        for hid, hb in held_h.items():
            hy = by_h.setdefault(hid, {})
            hy[released_year] = hy.get(released_year, 0.0) + hb["traditional"] * ((1 - ordinary_in(released_year)) if after_tax else 1.0) + hb["roth"]
        if after_tax:
            bond_tax[released_year] = bond_tax.get(released_year, 0.0) + held_back["traditional"] * ordinary_in(released_year)
    needs = _goal_needs(goals, infl, today, today.year + n_years)
    need = {y: sum(x["nominal"] for x in v) for y, v in needs.items()}
    # Social Security (you + spouse) and other income: net of tax, added to each year's cash
    def income_for(claim_age: float | None = None) -> dict:
        return inc.household_income(settings, years, today, infl, tax_at=sched.at, after_tax=after_tax,
                                    other_taxable_income=prov, filing_status=(profile or {}).get("filing_status"),
                                    claim_age_override=claim_age)

    def cash_with(income_: dict) -> dict[int, float]:
        return {y: bond_inflow.get(y, 0.0) + income_["net"].get(y, 0.0) for y in set(bond_inflow) | set(income_["net"])}

    income = income_for()
    cash_in = cash_with(income)

    funds = [fund_sale_profile(r, profile) for r in open_funds]
    if rate_shift:
        for f in funds:
            # a rate move reprices a fund today (−duration × shift) and it then earns the shift more; TIPS funds
            # follow REAL rates, which the inflation scenarios leave unchanged → no price hit, payouts follow CPI
            text = f"{f.get('label') or ''} {f.get('ticker') or ''}".lower()
            hit = 1.0 if ("tips" in text or "inflation" in text or f.get("non_usd")) else max(0.5, 1 - f["duration"] * rate_shift)
            if f.get("non_usd"):
                continue                                  # foreign rates, not US ones — handled by fx_drift below
            f["value"] *= hit
            if f.get("cost_ratio") is not None:
                f["cost_ratio"] /= hit
            f["yield"] = max(0.0, f["yield"] + rate_shift)
            if f["accumulates"]:
                f["accrual"] = f["yield"]
            else:
                f["cash_yield"] = max(0.0, f["cash_yield"] + rate_shift)
    if fx_drift:
        for f in funds:
            if f.get("non_usd"):                          # a weaker dollar lifts their dollar value year after year
                f["yield"] = max(-0.5, f["yield"] + fx_drift)
                f["accrual"] = f["yield"] - f["cash_yield"]
    tax_t = (rates.fed + rates.niit) if after_tax else 0.0          # Treasuries: federal only (state-exempt)
    r_carry = r_bill * (1 - tax_t)
    reinvest_rate = lambda tenor: max(0.0, (bm.interp(npts, float(tenor)) or r_bill) + rate_shift) * (1 - tax_t)  # noqa: E731
    plan_end = years[-1]
    common = dict(after_tax=after_tax, carry_rate=r_carry, plan_end=plan_end, early_until=early_until,
                  lock_early=lock_early)
    if ingredients:
        return {"today": today, "years": years, "need": need, "needs": needs, "income_net": income["net"],
                "inflow_by_holding": by_h, "bond_inflow": bond_inflow, "funds": funds, "r_carry": r_carry, "r_bill": r_bill,
                "early_until": early_until, "lock_early": lock_early, "released_year": released_year,
                "after_tax": after_tax, "plan_end": plan_end, "rows": rows_h, "open_ids": open_ids, "sched": sched,
                "infl": infl, "npts": npts, "mi": mi, "mode": mode, "tax_t": tax_t}

    def levers(inflow: dict[int, float]):
        """The plan's levers for one stream of outside cash (bonds + Social Security + other income):
        returns (baseline simulation, run(sales_on, reinvest_on))."""
        base = simulate_goal_funding(years, today, inflow, need, funds, use_funds=False, **common)
        gap_years = {r["year"] for r in base["rows"] if r["shortfall"] > 0.5}
        # surplus is invested in Treasuries maturing in EVERY later year that will spend it — a ladder instead of
        # rolling T-bills — not only in years that are short without it (those are usually paid by the same cash)
        need_years = {y for y in years[1:] if need.get(y, 0.0) > 0.5}

        cache: dict[tuple[bool, bool], tuple[dict, bool]] = {}

        def run(sales_on: bool, reinv_on: bool) -> tuple[dict, bool]:
            """Simulate with the chosen levers. Fund sales (gap years only) and surplus reinvestment (into gap
            years, never out of a year that sells funds) are optimized TOGETHER, so a Treasury arriving in a gap
            year takes the place of fund sales there — the plan never double-funds a year or sells-and-buys.
            Returns (simulation, optimizer_used); cached per lever combination."""
            key = (sales_on and bool(funds), reinv_on and bool(need_years))
            if key == (False, False):
                return base, False
            if key in cache:
                return cache[key]
            r_kw = dict(reinvest_rate=reinvest_rate, reinvest_targets=need_years) if key[1] else {}
            if not key[0]:
                pa = solve_cash_plan(years, today, inflow, need, funds, after_tax=after_tax, carry_rate=r_carry,
                                     plan_end=plan_end, sale_years=set(), early_until=early_until,
                                     lock_early=lock_early, **r_kw)
                sim = simulate_goal_funding(years, today, inflow, need, funds, use_funds=False,
                                            reinvest_plan=(pa or {}).get("reinvest", {}), reinvest_rate=reinvest_rate, **common)
                cache[key] = (sim, pa is not None)
                return cache[key]
            used, sim = False, None
            allowed = set(gap_years)
            for _ in range(6):
                pb = solve_cash_plan(years, today, inflow, need, funds, after_tax=after_tax, carry_rate=r_carry,
                                     plan_end=plan_end, sale_years=allowed, early_until=early_until,
                                     reinvest_sources_exclude=allowed, lock_early=lock_early, **r_kw)
                used = used or pb is not None
                sim = simulate_goal_funding(years, today, inflow, need, funds, use_funds=True,
                                            plan=(pb or {}).get("sales"), reinvest_plan=(pb or {}).get("reinvest", {}),
                                            reinvest_rate=reinvest_rate, **common)
                new_gaps = {s_["year"] for s_ in sim["schedule"]} - allowed
                if not new_gaps or pb is None:
                    break
                allowed |= new_gaps
            cache[key] = (sim, used)
            return cache[key]
        return base, run

    base, run = levers(cash_in)
    chosen, used_opt = run(use_funds, reinvest)
    # The IRA/401(k) fund withdrawals and realized gains the plan itself chooses are income too, so they count in
    # Social Security's provisional-income test. That changes the benefit tax, which changes the plan — iterate
    # to a fixed point (the sales stop moving), always from the portfolio's own income + the CURRENT plan's sales.
    if after_tax and income["people"] and income["ss_taxable_override"] is None and funds and use_funds:
        prov_base = dict(prov)
        fund_by_id = {f["id"]: f for f in funds}

        def plan_income(sim: dict) -> dict[int, float]:
            add: dict[int, float] = {}
            for s_ in sim["schedule"]:
                f = fund_by_id.get(s_["holding_id"])
                if not f:
                    continue
                if f["account_type"] in TRADITIONAL:
                    v = s_["gross"]
                elif f["account_type"] not in SHELTERED:
                    cg = _tx(f, s_["year"]).get("cg") or 0.0
                    v = max(0.0, (s_["tax"] - s_.get("early_penalty", 0.0)) / cg) if cg > 0 else 0.0
                else:
                    v = 0.0
                add[s_["year"]] = add.get(s_["year"], 0.0) + v
            return add

        seen = plan_income(chosen)
        for _ in range(5):
            prov.clear()
            prov.update({y: prov_base.get(y, 0.0) + seen.get(y, 0.0) for y in set(prov_base) | set(seen)})
            income = income_for()
            cash_in = cash_with(income)
            base, run = levers(cash_in)
            chosen, used_opt = run(use_funds, reinvest)
            nxt = plan_income(chosen)
            moved = max((abs(nxt.get(y, 0.0) - seen.get(y, 0.0)) for y in set(nxt) | set(seen)), default=0.0)
            seen = nxt
            if moved < 250.0:
                break
    for y, t in income["tax"].items():
        if t:
            bond_tax[y] = bond_tax.get(y, 0.0) + t
    if light:
        funds_before = funds_after = reinv_before = reinv_after = chosen
    else:
        funds_before, _ = run(False, reinvest)
        funds_after, _ = run(True, reinvest) if funds else (funds_before, False)
        reinv_before, _ = run(use_funds, False)
        reinv_after, _ = run(use_funds, True)

    def _disc(y: int) -> float:
        t = _years_to(today, y)
        return (1 + (bm.interp(npts, max(t, 0.1)) or 0.04) / 2) ** (-2 * t)

    def _grow(y_annual: float, t: float, taxed: bool) -> float:
        """$1 in a Treasury/TIPS held ``t`` years. ``taxed``: bought in a TAXABLE account, so its income (coupons,
        zero-coupon OID, TIPS inflation accretion) is taxed federally every year at that year's rate (state-exempt)."""
        if not taxed:
            return (1 + y_annual) ** t
        g, k = 1.0, 0
        while k < t - 1e-9:
            rk = sched.at(today.year + k)
            g *= (1 + y_annual * (1 - rk.fed - rk.niit)) ** min(1.0, t - k)
            k += 1
        return g

    def metrics(sim: dict) -> dict:
        pv_n = pv_c = pv_tax = tax_total = 0.0
        cost = {"treasury": 0.0, "tips": 0.0}
        cost_pre = {"treasury": 0.0, "tips": 0.0}
        for r in sim["rows"]:
            y, d = r["year"], _disc(r["year"])
            pv_n += r["need"] * d
            pv_c += r["covered"] * d
            tx = bond_tax.get(y, 0.0) + r.get("fund_tax", 0.0)
            tax_total += tx
            pv_tax += tx * d
            items = needs.get(y, [])
            real_share = (sum(x["nominal"] for x in items if x["real"]) / r["need"]) if r["need"] else 0.0
            if r["shortfall"] > 0:
                # today's cost of a zero-coupon Treasury (nominal goals) / TIPS (inflation-adjusted goals) paying the gap
                t = _years_to(today, y)
                yn = bm.interp(npts, max(t, 0.1)) or 0.04
                rr = bm.interp(tips_pts, max(t, 0.5)) if tips_pts else None
                for key, part, y_ann in (("treasury", 1 - real_share, (1 + yn / 2) ** 2 - 1),
                                         ("tips", real_share, (1 + (rr if rr is not None else 0.02)) * (1 + infl.avg(t)) - 1)):
                    if part > 0:
                        amt = r["shortfall"] * part
                        cost_pre[key] += amt / _grow(y_ann, t, False)
                        cost[key] += amt / _grow(y_ann, t, after_tax)
        c_basis = ("bought in a taxable account — Treasury/TIPS income (incl. inflation accretion) taxed federally each year"
                   if after_tax else "pre-tax")
        return {"funded_ratio_pct": round(100 * pv_c / pv_n, 1) if pv_n else None, "pv_needs": round(pv_n, 2),
                "pv_covered": round(pv_c, 2), "taxes_total": round(tax_total, 2), "taxes_pv": round(pv_tax, 2),
                "need_total": round(sum(r["need"] for r in sim["rows"]), 2),
                "covered_total": round(sum(r["covered"] for r in sim["rows"]), 2),
                "shortfall_total": round(sum(r["shortfall"] for r in sim["rows"]), 2),
                "cost_to_fund_shortfalls": {"treasury": round(cost["treasury"], 2), "tips": round(cost["tips"], 2),
                                            "total": round(cost["treasury"] + cost["tips"], 2), "basis": c_basis,
                                            "pre_tax_total": round(cost_pre["treasury"] + cost_pre["tips"], 2)},
                "shortfall_years": [r["year"] for r in sim["rows"] if r["shortfall"] > 0]}

    m = metrics(chosen)
    rows = []
    for r in chosen["rows"]:
        r = {**r, "goals": [x["goal"] for x in needs.get(r["year"], [])], "early_withdrawal": _is_early(r["year"], early_until)}
        r["early_cost"] = round(r["early_cost"] + bond_early.get(r["year"], 0.0), 2)
        items_y = needs.get(r["year"], [])
        r["real_share"] = round(sum(x["nominal"] for x in items_y if x["real"]) / r["need"], 4) if r["need"] else 0.0
        # split the outside cash into bonds vs Social Security / other income (both already net of tax)
        other = income["net"].get(r["year"], 0.0)
        r["other_income"] = round(other, 2)
        r["ss_income"] = round(income["ss"].get(r["year"], 0.0), 2)
        r["bond_inflow"] = round(r["bond_inflow"] - other, 2)
        rows.append(r)

    # Social Security: what claiming earlier / later would do to THIS plan (everything else unchanged)
    claim_cmp = []
    me = income["people"].get("you")
    if me and me["pia_monthly"] > 0 and goals and not light:
        for age in sorted({62.0, round(me["fra_age"], 2), 70.0, me["claim_age"]}):
            is_chosen = abs(age - me["claim_age"]) < 1e-6
            if is_chosen:
                inc_a, sim_a = income, chosen
            else:
                inc_a = income_for(age)
                sim_a = levers(cash_with(inc_a))[1](use_funds, reinvest)[0]
            ma, pa = metrics(sim_a), inc_a["people"]["you"]
            claim_cmp.append({
                "claim_age": age, "chosen": is_chosen, "start_year": pa["start_year"], "monthly_today": pa["own_monthly"],
                "pct_of_fra": pa["pct_of_fra"],
                "household_lifetime_today_dollars": round(sum(x["lifetime_today_dollars"] for x in inc_a["people"].values()), 2),
                "funded_ratio_pct": ma["funded_ratio_pct"], "shortfall_total": ma["shortfall_total"],
                "shortfall_years": len(ma["shortfall_years"]),
                "first_shortfall": ma["shortfall_years"][0] if ma["shortfall_years"] else None})
    inc_rows = [r for r in income["by_year"] if r["gross"] > 0]
    ss_until = min((x["through_year"] for x in income["people"].values()), default=None)
    horizon_short = bool(claim_cmp) and ss_until is not None and last_goal < ss_until - 1
    income_summary = {
        "configured": income["configured"], "people": income["people"], "sources": income["sources"],
        "filing": income["filing"], "ss_taxable_override": income["ss_taxable_override"], "state_taxed": income["state_taxed"],
        "by_year": inc_rows,
        "total_gross": round(sum(r["gross"] for r in inc_rows), 2), "total_tax": round(sum(r["tax"] for r in inc_rows), 2),
        "total_net": round(sum(r["net"] for r in inc_rows), 2),
        "pv_net": round(sum(r["net"] * _disc(r["year"]) for r in inc_rows), 2),
        "claim_comparison": claim_cmp, "comparison_horizon_short": horizon_short,
        "goals_end_year": last_goal, "benefits_through_year": ss_until,
        "notes": ([
            *([f"Your goals stop in {last_goal} but Social Security runs to {ss_until}: the funded ratio only sees the years with "
               "goals, which flatters claiming early. Extend your income goal to your plan-through age to compare claiming ages fairly."]
              if horizon_short else []),
            "Social Security is entered in today's dollars (as on your SSA statement) and rises with your inflation path (COLA).",
            ("Federal tax on benefits follows the IRS provisional-income test using this plan's interest, IRA/401(k) withdrawals "
             "and other income" + (" — joint thresholds." if income["filing"] == "joint" else " — single thresholds.")
             if income["ss_taxable_override"] is None else
             f"You set {income['ss_taxable_override']:.0f}% of benefits as federally taxable.")
            + ("" if income["state_taxed"] else " No state tax on benefits (true in most states, incl. CA)."),
            "Not modelled: the earnings test if you work while claiming before full retirement age, WEP/GPO, the family maximum.",
        ] if income["people"] else []),
    }

    # fund drawdown summary (holding the reinvestment choice fixed)
    draw = funds_after
    sales_by_year: dict[int, float] = {}
    for s_ in draw["schedule"]:
        sales_by_year[s_["year"]] = sales_by_year.get(s_["year"], 0.0) + s_["net"]
    lock_pv = sum(v * _disc(y) for y, v in sales_by_year.items())
    rate_impact = sum(s_["rate_impact_1pct"] for s_ in draw["schedule"])
    fund_summary = {
        "available": [{k: f[k] for k in ("id", "label", "ticker", "account_type", "value", "duration", "duration_estimated",
                                         "duration_source", "gain_pct", "cost_known", "expense_ratio_pct")}
                      | {"yield_pct": round(f["yield"] * 100, 3), "cash_yield_pct": round(f["cash_yield"] * 100, 3),
                         "accumulates": f["accumulates"], "sale_tax_pct": round(f["sale_tax"] * 100, 2),
                         "early_extra_pct": round(f["early_sale_extra"] * 100, 2),
                         "remaining_end": draw["remaining"].get(f["id"])} for f in funds],
        "enabled": use_funds,
        "method": ("optimal across all years (linear program)" if used_opt or not funds
                   else "year-by-year cheapest fund (optimizer unavailable)"),
        "schedule": draw["schedule"],
        "total_sold": round(sum(s_["gross"] for s_ in draw["schedule"]), 2),
        "tax_paid": round(sum(s_["tax"] for s_ in draw["schedule"]), 2),
        "net_raised": round(sum(s_["net"] for s_ in draw["schedule"]), 2),
        "remaining_value_end": draw["remaining_total"],
        "rate_impact_1pct": round(rate_impact, 2),
        "years_filled": sorted(sales_by_year),
        "before": metrics(funds_before), "after": metrics(funds_after),
        "lock_in": ({"years": sorted(sales_by_year), "treasury_cost_today": round(lock_pv, 2),
                     "note": (f"Instead of selling funds later (±${abs(rate_impact):,.0f} per 1% rate move), you could sell about "
                              f"${lock_pv:,.0f} of them now and buy Treasuries maturing in {', '.join(map(str, sorted(sales_by_year)))} — "
                              "that locks these years in with no rate risk.")} if sales_by_year else None),
    }
    ri = reinv_after["reinvestments"]
    reinvest_summary = {
        "enabled": reinvest,
        "plan": ri,
        "total_set_aside": round(sum(x["amount"] for x in ri), 2),
        "total_arriving": round(sum(x["value_at_maturity"] for x in ri), 2),
        "extra_vs_tbills": round(sum(x["vs_tbills"] for x in ri), 2),
        "before": metrics(reinv_before), "after": metrics(reinv_after),
        "rate_basis": ("today's Treasury curve at each tenor (rates unchanged)" + (", after federal tax" if after_tax else "")),
        "tbill_rate_pct": round(r_carry * 100, 3),
    }
    early_total = round(sum(r["early_cost"] for r in rows), 2)
    has_retirement = any((by_id[h]["account_type"] in TRADITIONAL or by_id[h]["account_type"] == "roth")
                         for h in by_id if by_id[h]["status"] == "held")
    return {
        "as_of": today.isoformat(), **ps.inflation_fields(infl, infl_src),
        "after_tax": after_tax, "use_funds": use_funds, "reinvest_surplus": reinvest, "years": rows, **m,
        "book_value": round(book_value, 2),
        "income": income_summary,
        "funds": fund_summary if funds else None,
        "reinvest": reinvest_summary,
        "retired_tax": ps.tax_schedule_fields(sched),
        "withdrawal": {"mode": mode, "birth_year": birth_year, "early_until": early_until,
                       "penalty_free_from": (early_until + 1) if early_until is not None else None,
                       "applies": has_retirement, "early_cost_total": early_total, "locked_until_59": lock_early,
                       "held_back_released": round(released, 2), "released_year": released_year,
                       "early_years": [r["year"] for r in rows if r["early_withdrawal"]]},
        "notes": ["Individual bonds and defined-maturity ETFs pay their coupons and principal on schedule"
                  + (" (after tax)." if after_tax else " (pre-tax)."),
                  *(["Social Security and your other income arrive every year" + (" (after tax)" if after_tax else "")
                     + " and are spent first; the bonds and funds cover what's left."] if income["configured"] else []),
                  "Open-ended bond funds never mature: they pay distributions on what you still hold (price assumed flat) and"
                  + (" are sold down only when a year falls short — cheapest fund first (rate risk + tax + sheltered growth given up)."
                     if use_funds else " are NOT sold in this view."),
                  ("Surplus cash buys Treasuries maturing in the later years that will spend it (today's curve, rates unchanged) "
                   "instead of rolling T-bills; each year spends its own coupons and maturities first."
                   if reinvest else "Surplus waits in T-bills (1-year Treasury, rolled)."),
                  ("IRA/401k cash is taxed as ordinary income when withdrawn (+10% before 59½); Roth is tax-free after 59½ "
                   "(before, earnings are taxed + 10%); HSA/529 assumed used for qualified expenses."
                   if after_tax else "Pre-tax view: withdrawals and sales are not taxed."),
                  *([f"From {sched.from_year} your after-retirement rates apply ({sched.retired.fed * 100:.0f}% federal, "
                     f"{sched.retired.state * 100:.1f}% state, {sched.retired.ltcg * 100:.0f}% LTCG)."]
                    if (after_tax and sched.retired) else []),
                  *(["By your age: IRA/401k/Roth money stays in the account (reinvested, no penalties) until "
                     f"{released_year}, then becomes available — switch to 'Before 59½' to see the cost of tapping it early."]
                    if lock_early else []),
                  "Cost to fund = today's price of zero-coupon Treasuries (nominal goals) / TIPS (inflation-adjusted goals) "
                  "maturing in each shortfall year" + (", bought in a taxable account (their income is taxed every year, so it "
                  "takes more than the pre-tax price)" if after_tax else "") + " — build it with the ladder builder."],
    }
