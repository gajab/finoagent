"""Fund the plan's gaps — what to buy, sized to YOUR shortfall years, and stress-tested for inflation.

The planner already knows, year by year, what your bonds, funds, Social Security and other income cover and
where they fall short. This module turns those gaps into a purchase list and answers "what if inflation
doesn't do what I expect?":

1. **Gap-shaped ladder.** For every shortfall year, buy bonds maturing that year, sized (with the planner
   itself, iteratively) so the year is covered after tax. Inflation-adjusted needs can be met with TIPS
   (buying power locked) or nominal bonds (dollars locked); the nominal instrument is whichever of
   Treasury / CD / agency / in-state muni / corporate delivers the most AFTER TAX for that year in the chosen
   account — taxed year by year at your pre- and post-retirement rates.
2. **Three strategies**: lock nominal rates · inflation-proof (TIPS) · half-and-half.
3. **Four worlds**, each a full re-run of the plan (existing holdings, funds, Social Security, goals):
   low inflation (rates fall with it), expected, high inflation (rates rise with it), and dollar debasement
   (inflation stays high for decades while rates lag behind it). Bonds held to maturity keep paying what they
   promised; cash, future reinvestments and bond funds move with rates; TIPS, Social Security and
   inflation-adjusted goals move with CPI.
4. **Recommendation** = the strategy with the smallest worst-case unfunded amount (in today's dollars) across
   the four worlds; ties go to the cheaper one. Deterministic — no forecasts, no LLM.
"""
from __future__ import annotations

import logging
from datetime import date

from . import bond_ladder_service as lad
from . import bond_market_service as mkt
from . import bond_math as bm
from . import bond_portfolio_service as ps
from .bond_buy_service import _CREDIT_LEVEL, MIN_CREDIT

logger = logging.getLogger(__name__)

NOMINAL_KINDS = [("treasury", None, "Treasury"), ("cd", None, "Brokered CD"), ("agency", None, "Agency"),
                 ("muni", None, "Muni (in-state)"), ("corporate", "AA", "Corporate AA"),
                 ("corporate", "A", "Corporate A"), ("corporate", "BBB", "Corporate BBB")]
STRATEGIES = {
    "nominal": {"label": "Lock today's rates (nominal bonds)", "tips_share": 0.0,
                "blurb": "Cheapest if inflation comes in at or below what you expect — dollars are fixed, buying power isn't."},
    "blend": {"label": "Half TIPS, half nominal", "tips_share": 0.5,
              "blurb": "Splits the bet: half the gap keeps its buying power, half locks today's nominal yields."},
    "tips": {"label": "Inflation-proof (TIPS)", "tips_share": 1.0,
             "blurb": "Each year's spending power is locked whatever CPI does — you give up the win if inflation falls."},
}
_ETF_FAMILY = {"treasury": "treasury", "tips": "tips", "muni": "muni", "corporate": "corporate"}
_RATING = {"agency": "AA+", "muni": "AA", "cd": None, "treasury": None, "tips": None}


def _pnum(params: dict, key: str, default: float) -> float:
    v = params.get(key)
    try:
        return float(v) if v not in (None, "") else default
    except (TypeError, ValueError):
        return default


def scenario_defs(base: bm.InflationPath, low_pct: float, high_pct: float, debase_pct: float) -> list[dict]:
    low, high, deb = low_pct / 100, high_pct / 100, debase_pct / 100
    # fx_drift = how much faster the dollar loses value than expected → non-dollar bonds gain that much a year
    return [
        {"key": "low", "label": (f"Deflation ({low_pct:g}% a year)" if low < 0 else f"Low inflation ({low_pct:g}%)"),
         "short": low, "long": low, "rate_shift": low - base.long, "fx_drift": low - base.long,
         "story": ("Prices fall and interest rates fall with them: fixed-dollar bonds gain buying power, TIPS only hold theirs "
                   "(principal can't go below par), Social Security isn't cut, cash and funds reinvest at almost nothing."
                   if low < 0 else
                   "Inflation fades and interest rates fall with it: locked-in nominal yields win, TIPS pay less in dollars, cash and funds reinvest lower.")},
        {"key": "base", "label": "As expected", "short": base.short, "long": base.long, "rate_shift": 0.0, "fx_drift": 0.0,
         "story": "Your inflation path; rates stay where they are."},
        {"key": "high", "label": f"High inflation ({high_pct:g}%)", "short": high, "long": high, "rate_shift": high - base.long,
         "fx_drift": high - base.long,
         "story": "Inflation runs hot and rates rise with it: fixed dollars buy less, TIPS and Social Security keep up, funds drop then earn more."},
        {"key": "debase", "label": f"Dollar debasement ({debase_pct:g}% for decades)", "short": deb, "long": deb,
         "rate_shift": 0.5 * (deb - base.long), "fx_drift": deb - base.long,
         "story": "The dollar steadily loses value while rates are held below inflation (they rise only half as much): the worst case for nominal bonds and cash; non-dollar bonds and TIPS hold up."},
    ]


def _with_inflation(profile: dict, sc: dict) -> dict:
    return {**profile, "inflation_assumption": sc["short"] * 100,
            "settings": {**((profile or {}).get("settings") or {}), "inflation_long": sc["long"] * 100}}


def _real_shortfall(plan: dict, path: bm.InflationPath, today: date) -> float:
    return sum(r["shortfall"] / path.factor(max(0, r["year"] - today.year)) for r in plan["years"] if r["shortfall"] > 0)


def _outcome(plan: dict, sc: dict, today: date, mi: dict | None = None) -> dict:
    path = bm.InflationPath(sc["short"], sc["long"])
    short = plan["shortfall_years"]
    # what closing THIS world's gap would cost today with TIPS: each short year's real amount at the real yield
    tips_cost = 0.0
    for r in plan["years"]:
        if r["shortfall"] > 0:
            T = max(0.25, bm.year_frac(today, date(r["year"], 6, 30)))
            rr = mkt.model_yield("tips", T, mi)[0] if mi else None
            tips_cost += r["shortfall"] / path.factor(max(0, r["year"] - today.year)) / (1 + (rr if rr is not None else 0.02)) ** T
    return {"funded_ratio_pct": plan["funded_ratio_pct"], "shortfall_years": len(short),
            "first_shortfall": short[0] if short else None, "last_shortfall": short[-1] if short else None,
            "unfunded_today_dollars": round(_real_shortfall(plan, path, today), 2),
            "unfunded_nominal": round(sum(r["shortfall"] for r in plan["years"]), 2),
            "tips_to_close": round(tips_cost, 2)}


def delivered_per_dollar(kind: str, rating: str | None, T: float, year: int, acct: str, profile: dict,
                         sched: ps.TaxSchedule, mi: dict, infl: bm.InflationPath, today: date, after_tax: bool) -> dict | None:
    """$ you can SPEND in ``year`` per $1 invested today in a bond of this kind maturing then: the yield
    compounds after each year's tax (your rates that year — lower once retired), and money in a traditional
    IRA/401(k) is taxed as income when it comes out."""
    y, basis = mkt.model_yield(kind, T, mi, rating)
    if y is None:
        return None
    tr, _ = ps.tax_treatment({"kind": kind, "account_type": acct, "state": (profile or {}).get("state")}, profile)
    g, k = 1.0, 0
    floored = kind == "tips" and infl.factor(T) < 1.0        # deflation: a TIPS still repays at least par
    while k < T - 1e-9:
        step = min(1.0, T - k)
        r = sched.at(today.year + k)
        if kind == "tips":
            y_nom = (1 + y) * (1 + (0.0 if floored else infl.rate_at(k))) - 1
            t = (r.fed + r.niit) if tr.taxable_account else 0.0
        else:
            y_nom = (1 + y / 2) ** 2 - 1
            t = bm.interest_tax_rate(r, tr)
        g *= (1 + y_nom * (1 - (t if after_tax else 0.0))) ** step
        k += 1
    if after_tax and acct in lad.TRADITIONAL:
        r = sched.at(year)
        g *= 1 - r.fed - r.state
    pre = ((1 + y) * (1 + infl.avg(T)) - 1) if kind == "tips" else y
    return {"growth": g, "after_tax_yield": g ** (1 / T) - 1 if T > 0 else 0.0, "pre_tax_yield": pre,
            "real_yield": y if kind == "tips" else None, "basis": basis}


def best_nominal(T: float, year: int, acct: str, profile: dict, sched, mi: dict, infl, today: date, *,
                 min_credit: str, after_tax: bool) -> tuple[dict | None, list[dict]]:
    """The nominal instrument that leaves the most to spend in ``year`` (within the credit limit), and the field."""
    taxable = acct not in ps.TAX_ADVANTAGED
    out = []
    for kind, rating, label in NOMINAL_KINDS:
        if _CREDIT_LEVEL[(kind, rating)] > MIN_CREDIT[min_credit]:
            continue
        if kind == "muni" and not taxable:
            continue
        if kind == "cd" and T > 10.5:
            continue                                       # brokered CDs rarely go past 10 years
        d = delivered_per_dollar(kind, rating, T, year, acct, profile, sched, mi, infl, today, after_tax)
        if d:
            out.append({"kind": kind, "rating": rating, "label": label, **d})
    out.sort(key=lambda c: (-c["growth"], _CREDIT_LEVEL[(c["kind"], c["rating"])]))
    return (out[0] if out else None), out


def _pick_tips(cat_rows: list[dict], year: int) -> dict | None:
    tips = [r for r in cat_rows if r["type"] == "tips" and r.get("real_yield_pct") is not None and r.get("index_ratio")]
    same = [r for r in tips if int(r["maturity"][:4]) == year]
    if same:
        return min(same, key=lambda r: abs((date.fromisoformat(r["maturity"]) - date(year, 6, 30)).days))
    earlier = [r for r in tips if year - 3 <= int(r["maturity"][:4]) < year]       # no TIPS matures that year
    return max(earlier, key=lambda r: r["maturity"]) if earlier else None


def _holding(hid: int, kind: str, rating: str | None, year: int, amount: float, acct: str, profile: dict,
             cat_rows: list[dict], mi: dict, today: date) -> tuple[dict, dict] | None:
    """A purchase as a holding the planner can value: real Treasuries / TIPS by CUSIP (FedInvest price), other
    kinds as a new par bond at today's model yield. Returns (holding, line facts)."""
    target = date(year, 6, 30)
    T = max(0.25, bm.year_frac(today, target))
    base = {"id": hid, "status": "held", "account_type": acct, "purchase_date": today.isoformat()}
    if kind == "treasury":
        r = lad.pick_treasury(cat_rows, target) if cat_rows else None
        if r:
            px = r["price"] + (r.get("accrued") or 0.0)
            face = max(1000.0, 1000.0 * round(amount / (px / 100) / 1000))
            h = {**base, "kind": "treasury", "cusip": r["cusip"], "label": f"UST {r['coupon_pct']}% {r['maturity']}",
                 "face_value": face, "coupon_rate": r["coupon_pct"], "coupon_freq": 0 if r["type"] == "bill" else 2,
                 "maturity_date": r["maturity"], "purchase_price": r["price"]}
            return h, {"cost": face * px / 100, "face": face, "security": {
                "cusip": r["cusip"], "coupon_pct": r["coupon_pct"], "maturity": r["maturity"], "price": r["price"],
                "yield_pct": r.get("ytm_pct"), "real": False, "type": r["type"]}}
    if kind == "tips":
        r = _pick_tips(cat_rows, year) if cat_rows else None
        if r:
            px = (r["price"] + (r.get("accrued") or 0.0)) * r["index_ratio"]
            face = max(1000.0, 1000.0 * round(amount / (px / 100) / 1000))
            h = {**base, "kind": "tips", "cusip": r["cusip"], "label": f"TIPS {r['coupon_pct']}% {r['maturity']}",
                 "face_value": face, "coupon_rate": r["coupon_pct"], "coupon_freq": 2, "maturity_date": r["maturity"],
                 "tips_ref_cpi": r.get("ref_cpi"), "purchase_price": r["price"]}
            return h, {"cost": face * px / 100, "face": face, "security": {
                "cusip": r["cusip"], "coupon_pct": r["coupon_pct"], "maturity": r["maturity"], "price": r["price"],
                "yield_pct": r.get("real_yield_pct"), "real": True, "type": "tips", "index_ratio": r["index_ratio"]},
                "off_year": int(r["maturity"][:4]) != year}
    y, _ = mkt.model_yield(kind, T, mi, rating)
    if y is None:
        return None
    face = max(1000.0, 1000.0 * round(amount / 1000))
    h = {**base, "kind": kind, "label": f"New {mkt.KIND_LABELS.get(kind, kind)} {year}", "face_value": face,
         "coupon_rate": round(y * 100, 3), "coupon_freq": 2, "maturity_date": target.isoformat(),
         "current_price": 100.0, "purchase_price": 100.0, "rating": rating or _RATING.get(kind),
         "state": (profile or {}).get("state") if kind == "muni" else None, "issuer": f"New {kind} {year}"}
    return h, {"cost": face, "face": face, "security": None}


def resilience(rows: list[dict]) -> dict:
    """How the book is spread across markets and what protects it if the dollar loses value."""
    live = [r for r in rows if r["status"] == "held" and not r.get("matured") and (r.get("market_value") or 0) > 0]
    mv = sum(r["market_value"] for r in live) or 1.0
    seg: dict[str, float] = {}
    infl = non_usd = floating = funds = 0.0
    by_name: dict[str, float] = {}
    issuers: dict[str, float] = {}
    for r in live:
        v = r["market_value"]
        f = r.get("fund") or {}
        text = f"{r.get('name') or ''} {r.get('label') or ''} {f.get('category') or ''}".lower()
        is_fund = r["kind"] in ps.FUNDS
        tips_like = r["kind"] == "tips" or (is_fund and ("tips" in text or "inflation" in text))
        foreign = is_fund and (f.get("non_usd") or any(k in text for k in ("international", "global", "world", "foreign", "ex-us", "ex us", "emerging")))
        if tips_like:
            infl += v
        if is_fund and (f.get("non_usd") or ps.is_non_usd_fund(text)):
            non_usd += v
        if (r.get("eff_duration") is not None and r["eff_duration"] < 1.0) or f.get("cash_like"):
            floating += v
        if is_fund:
            funds += v
        if r["kind"] in ("treasury", "tips", "agency", "cd") or f.get("cash_like") or (is_fund and f.get("tax_class") == "treasury"):
            key = "US government, agency & FDIC"
        elif r["kind"] == "muni" or f.get("tax_class") == "muni":
            key = "Municipal"
        elif is_fund and foreign:
            key = "International & emerging markets"
        elif is_fund and any(k in text for k in ("clo", "securitized", "mortgage", "asset-backed")):
            key = "Securitized (CLO / MBS)"
        elif is_fund and any(k in text for k in ("high yield", "floating rate", "bank loan", "senior loan")):
            key = "High yield & loans"
        else:
            key = "Corporate & broad funds"
        seg[key] = seg.get(key, 0.0) + v
        name = (r.get("ticker") or r.get("label") or "?") if is_fund else (r.get("label") or "?")
        by_name[name] = by_name.get(name, 0.0) + v
        if r["kind"] == "corporate":
            issuer = (r.get("issuer") or r.get("label") or "?").split()[0].upper()
            issuers[issuer] = issuers.get(issuer, 0.0) + v
    top = max(by_name.items(), key=lambda kv: kv[1]) if by_name else ("—", 0.0)
    top_issuer = max(issuers.items(), key=lambda kv: kv[1]) if issuers else None
    pct = lambda v: round(100 * v / mv, 1)  # noqa: E731
    flags = []
    if pct(infl) < 15:
        flags.append(f"Only {pct(infl):.0f}% of the book is inflation-linked — the rest pays fixed dollars that buy less if prices rise.")
    if pct(non_usd) < 1:
        flags.append("Every holding pays in US dollars. Inside a bond book, only TIPS (CPI) protect against a weaker dollar at home; "
                     "non-dollar bonds would add currency diversification — with currency swings of their own.")
    if pct(top[1]) > 20:
        flags.append(f"{top[0]} alone is {pct(top[1]):.0f}% of the book.")
    if top_issuer and pct(top_issuer[1]) > 5:
        flags.append(f"{top_issuer[0]} bonds add up to {pct(top_issuer[1]):.0f}% — a single company; diversified corporate exposure usually keeps one issuer under ~3%.")
    return {"market_value": round(mv, 2), "inflation_linked_pct": pct(infl), "non_usd_pct": pct(non_usd),
            "floating_or_short_pct": pct(floating), "funds_pct": pct(funds),
            "largest_position": {"name": top[0], "pct": pct(top[1])},
            "largest_corporate_issuer": {"name": top_issuer[0], "pct": pct(top_issuer[1])} if top_issuer else None,
            "segments": [{"key": k, "pct": pct(v), "value": round(v, 2)} for k, v in sorted(seg.items(), key=lambda kv: -kv[1])],
            "flags": flags}


async def gap_plan(holdings: list[dict], profile: dict, params: dict) -> dict:
    today = mkt.us_today()
    acct = (params.get("account_type") or "taxable").lower()
    min_credit = params.get("min_credit") if params.get("min_credit") in MIN_CREDIT else "AA"
    after_tax = params.get("after_tax", True)
    kw = dict(after_tax=after_tax, use_funds=params.get("use_funds", True), reinvest=params.get("reinvest", True),
              withdrawal_mode=params.get("withdrawal_mode"))
    budget = params.get("budget")
    budget = float(budget) if budget not in (None, "") and float(budget) > 0 else None
    rc = await mkt.rate_context()
    base_path, infl_src = ps.inflation_assumption(profile, rc)
    scs = scenario_defs(base_path, _pnum(params, "low_inflation", -1.0), _pnum(params, "high_inflation", 5.0),
                        _pnum(params, "debase_inflation", 6.0))
    sched = ps.tax_schedule(profile)
    mi = await mkt.market_inputs()
    cat = await mkt.treasury_catalogue()
    cat_rows = (cat or {}).get("rows") or []

    async def run(extra: list[dict], sc: dict) -> dict:
        prof = profile if sc["key"] == "base" else _with_inflation(profile, sc)
        return await lad.plan_goals(holdings + extra, prof, light=True, rate_shift=sc["rate_shift"], fx_drift=sc["fx_drift"], **kw)

    base_sc = next(s for s in scs if s["key"] == "base")
    current = await run([], base_sc)
    ctx = await ps.load_context(holdings, profile)
    rows_now, _ = ps.analyze_rows(holdings, profile, ctx)
    out = {
        "as_of": today.isoformat(), "account_type": acct, "min_credit": min_credit, "budget": budget,
        **ps.inflation_fields(base_path, infl_src),
        "scenarios": [{k: (round(v * 100, 3) if k in ("short", "long", "rate_shift", "fx_drift") else v) for k, v in s.items()} for s in scs],
        "has_goals": bool((profile or {}).get("goals")), "retired_tax": ps.tax_schedule_fields(sched),
        "resilience": {"before": resilience(rows_now)},
    }
    gaps = [{"year": r["year"], "shortfall": r["shortfall"], "need": r["need"], "real_share": r.get("real_share", 0.0)}
            for r in current["years"] if r["shortfall"] > 500]
    cur_out = {"base": _outcome(current, base_sc, today, mi)}
    for sc in scs:
        if sc["key"] != "base":
            cur_out[sc["key"]] = _outcome(await run([], sc), sc, today, mi)
    out["current"] = {"outcomes": cur_out, "cost_to_fund": current["cost_to_fund_shortfalls"]}
    out["gaps"] = [{**g, "shortfall": round(g["shortfall"], 2)} for g in gaps]
    if not gaps:
        out.update(strategies=[], recommended=None, notes=[
            "No shortfall at your expected inflation — nothing to buy to close gaps. The table shows how the plan "
            "holds up if inflation surprises." if out["has_goals"] else "Add goals in the Planner first — this plan funds their gaps."])
        return out

    # ---- per gap year: the instruments (after-tax, at your rates across the whole holding period) ----
    picks: dict[int, dict] = {}
    for g in gaps:
        T = max(0.25, bm.year_frac(today, date(g["year"], 6, 30)))
        nom, field = best_nominal(T, g["year"], acct, profile, sched, mi, base_path, today, min_credit=min_credit, after_tax=after_tax)
        tips = delivered_per_dollar("tips", None, T, g["year"], acct, profile, sched, mi, base_path, today, after_tax)
        picks[g["year"]] = {"T": T, "nominal": nom, "field": field, "tips": ({"kind": "tips", "rating": None, "label": "TIPS", **tips} if tips else None)}

    async def size(tips_share: float) -> tuple[dict, list[dict], dict]:
        """Dollars per (year, leg) so the planner shows the gap years covered: start from the after-tax
        arithmetic, then let the planner itself say what's still short and scale (2 corrections)."""
        alloc: dict[tuple[int, str], float] = {}
        for g in gaps:
            pk = picks[g["year"]]
            for leg, share in (("tips", tips_share), ("nominal", 1 - tips_share)):
                c = pk[leg] or pk["nominal"] or pk["tips"]
                if share > 0 and c:
                    alloc[(g["year"], leg)] = alloc.get((g["year"], leg), 0.0) + 0.94 * share * g["shortfall"] / c["growth"]
        target = {g["year"]: g["shortfall"] for g in gaps}
        plan, hyp, facts = current, [], {}
        for it in range(3):
            hyp, facts = build(alloc)
            plan = await run(hyp, base_sc)
            rem = {r["year"]: r["shortfall"] for r in plan["years"] if r["shortfall"] > 200}
            if it == 2 or sum(rem.values()) < 0.004 * sum(target.values()):
                break
            for y, s in rem.items():
                legs = [k for k in alloc if k[0] == y]
                if legs:
                    done = max(0.25 * target.get(y, s), target.get(y, s) - s)
                    f = min(1.8, target.get(y, s) / done) if y in target else 1.0
                    for k in legs:
                        alloc[k] *= f
                else:                                   # a year that only became short after re-optimizing
                    T = max(0.25, bm.year_frac(today, date(y, 6, 30)))
                    nom, _ = best_nominal(T, y, acct, profile, sched, mi, base_path, today, min_credit=min_credit, after_tax=after_tax)
                    if nom:
                        picks.setdefault(y, {"T": T, "nominal": nom, "field": [nom], "tips": None})
                        alloc[(y, "nominal")] = s / nom["growth"]
                        target[y] = s
        return alloc, hyp, {"plan": plan, "facts": facts}

    def build(alloc: dict[tuple[int, str], float]) -> tuple[list[dict], dict]:
        hyp, facts = [], {}
        for i, ((y, leg), amt) in enumerate(sorted(alloc.items())):
            pk = picks[y]
            c = pk[leg] or pk["nominal"] or pk["tips"]
            if not c or amt < 500:
                continue
            made = _holding(-(i + 1), c["kind"], c["rating"], y, amt, acct, profile, cat_rows, mi, today)
            if made:
                hyp.append(made[0])
                facts[(y, leg)] = {**made[1], "cand": c}
        return hyp, facts

    def trim(alloc: dict[tuple[int, str], float], facts: dict) -> dict:
        """Budget: fund the nearest gap years first; the first one that doesn't fit is filled in part."""
        if budget is None:
            return alloc
        left, out_ = budget, {}
        for k in sorted(alloc):
            cost = facts.get(k, {}).get("cost", alloc[k])
            if left <= 500:
                break
            take = min(1.0, left / cost) if cost > 0 else 0.0
            out_[k] = alloc[k] * take
            left -= cost * take
        return out_

    sized = {"nominal": await size(0.0), "tips": await size(1.0)}
    allocs = {"nominal": sized["nominal"][0], "tips": sized["tips"][0]}
    allocs["blend"] = {**{k: v * 0.5 for k, v in allocs["nominal"].items()}, **{k: v * 0.5 for k, v in allocs["tips"].items()}}
    strategies = []
    for key in ("nominal", "blend", "tips"):
        alloc = allocs[key]
        hyp, facts = build(alloc)
        if budget is not None and sum(f["cost"] for f in facts.values()) > budget + 500:
            alloc = trim(alloc, facts)
            hyp, facts = build(alloc)
            plan = await run(hyp, base_sc)
        elif key == "blend":
            plan = await run(hyp, base_sc)
        else:
            plan = sized[key][2]["plan"]
        outcomes = {"base": _outcome(plan, base_sc, today, mi)}
        for sc in scs:
            if sc["key"] != "base":
                outcomes[sc["key"]] = _outcome(await run(hyp, sc), sc, today, mi)
        lines = []
        for (y, leg), f in sorted(facts.items()):
            c, pk = f["cand"], picks[y]
            alt = [x for x in pk["field"] if x["label"] != c["label"]][:3] if leg == "nominal" else []
            fam = mkt.DEFINED_MATURITY.get(_ETF_FAMILY.get(c["kind"], ""), {})
            why = ("Keeps this year's spending power whatever inflation does (principal and coupons rise with CPI)"
                   + ("; no TIPS matures that year, so the nearest earlier one is used and the cash waits in T-bills" if f.get("off_year") else "")
                   if c["kind"] == "tips" else
                   f"Most to spend in {y} after tax in a {'taxable' if acct not in ps.TAX_ADVANTAGED else acct.upper()} account: "
                   f"{c['after_tax_yield'] * 100:.2f}% a year"
                   + (" vs " + ", ".join(f"{x['label']} {x['after_tax_yield'] * 100:.2f}%" for x in alt) if alt else ""))
            lines.append({
                "year": y, "leg": leg, "kind": c["kind"], "label": c["label"], "rating": c["rating"],
                "amount": round(f["cost"], 2), "face": f["face"], "pre_tax_pct": ps._pct(c["pre_tax_yield"]),
                "real_yield_pct": ps._pct(c["real_yield"]) if c.get("real_yield") is not None else None,
                "after_tax_pct": ps._pct(c["after_tax_yield"]), "spendable": round(f["cost"] * c["growth"], 2),
                "security": f["security"], "etf": fam.get(y), "why": why, "basis": c["basis"],
                "alternatives": [{"label": x["label"], "after_tax_pct": ps._pct(x["after_tax_yield"])} for x in alt],
            })
        cost = sum(l["amount"] for l in lines)
        worst = max(o["unfunded_today_dollars"] for o in outcomes.values())
        before = {r["year"]: r for r in current["years"]}
        strategies.append({
            "key": key, **{k: v for k, v in STRATEGIES[key].items() if k != "tips_share"}, "cost": round(cost, 2),
            "tips_cost": round(sum(l["amount"] for l in lines if l["kind"] == "tips"), 2), "lines": lines, "outcomes": outcomes,
            "worst_unfunded_today_dollars": round(worst, 2),
            "years": [{"year": r["year"], "need": r["need"], "shortfall_before": before.get(r["year"], {}).get("shortfall", 0.0),
                       "shortfall_after": r["shortfall"], "covered": r["covered"]} for r in plan["years"] if r["need"] > 0],
            "fund_sales": [{k: s_[k] for k in ("year", "ticker", "label", "account_type", "gross", "net", "tax", "reason")}
                           for s_ in ((plan.get("funds") or {}).get("schedule") or [])],
            "_hyp": hyp,
        })
    # ---- recommendation: smallest worst case across the four worlds; within 2% → the cheaper one ----
    floor = min(s["worst_unfunded_today_dollars"] for s in strategies)
    tol = max(2000.0, 0.02 * max(1.0, sum(g["shortfall"] for g in gaps)))
    safe = [s for s in strategies if s["worst_unfunded_today_dollars"] <= floor + tol]
    rec = min(safe, key=lambda s: s["cost"])
    cheapest = min(strategies, key=lambda s: s["cost"])
    why = [f"{rec['label']} leaves the least unfunded in its worst case: {_money(rec['worst_unfunded_today_dollars'])} "
           f"(today's dollars) across low, expected, high inflation and dollar debasement."]
    for s in strategies:
        if s["key"] != rec["key"]:
            d = s["cost"] - rec["cost"]
            why.append(f"{s['label']}: {'costs ' + _money(abs(d)) + (' more' if d > 0 else ' less')} today, worst case "
                       f"{_money(s['worst_unfunded_today_dollars'])} unfunded"
                       + (f" (in {_worst_key(s, scs)})" if s['worst_unfunded_today_dollars'] > 1000 else "") + ".")
    if cheapest["key"] != rec["key"]:
        why.append(f"The {_money(rec['cost'] - cheapest['cost'])} extra over the cheapest option is the price of that protection.")
    out["resilience"]["after"] = resilience(ps.analyze_rows(holdings + rec["_hyp"], profile,
                                                            await ps.load_context(holdings + rec["_hyp"], profile))[0])
    for s_ in strategies:
        s_.pop("_hyp", None)
    real_w = sum(g["shortfall"] * g["real_share"] for g in gaps) / max(1.0, sum(g["shortfall"] for g in gaps))
    be = _breakeven(mi, sum(picks[g["year"]]["T"] for g in gaps) / len(gaps))
    out.update(
        strategies=strategies, recommended=rec["key"], why=why, real_share_pct=round(100 * real_w, 1),
        breakeven_pct=ps._pct(be), placement=_placement(gaps, picks, profile, sched, mi, base_path, today, min_credit, after_tax),
        notes=[
            f"{real_w * 100:.0f}% of the gap is inflation-adjusted spending. TIPS break even against nominal Treasuries at about "
            f"{be * 100:.2f}% inflation over these maturities" + (f" — you expect {base_path.avg(10) * 100:.2f}%." if be is not None else "."),
            "Sizing is done by the planner itself: purchases are added to your book and scaled until the shortfall years are covered "
            "after tax, with your fund sales, Social Security and reinvestments re-optimized around them.",
            "Bonds held to maturity pay what they promised in every scenario; cash, future reinvestments and bond funds move with "
            "interest rates (funds: −duration × the rate move today, then they earn the difference); TIPS, Social Security and "
            "inflation-adjusted goals move with CPI.",
            "Dollar debasement: inside a bond book only CPI-linked bonds (TIPS, I Bonds) and Social Security keep pace; stocks, real "
            "estate, gold and non-dollar assets are the usual hedges outside it — they're not modelled here.",
            "Yields for CDs, agencies, munis and corporates are curve estimates — check the live quote. Treasuries and TIPS are real "
            "CUSIPs at FedInvest's last close.",
        ])
    return out


def _money(v: float) -> str:
    return f"${v:,.0f}"


def _worst_key(s: dict, scs: list[dict]) -> str:
    k = max(s["outcomes"], key=lambda k: s["outcomes"][k]["unfunded_today_dollars"])
    return next((x["label"] for x in scs if x["key"] == k), k).lower()


def _breakeven(mi: dict, T: float) -> float | None:
    n, _ = mkt.model_yield("treasury", T, mi)
    r, _ = mkt.model_yield("tips", T, mi)
    return ((1 + n) / (1 + r) - 1) if (n is not None and r is not None) else None


def _placement(gaps: list[dict], picks: dict, profile: dict, sched, mi: dict, infl, today: date, min_credit: str, after_tax: bool) -> dict:
    """The same rung bought in each kind of account: what $1 becomes, to spend, in a middle gap year."""
    g = gaps[len(gaps) // 2]
    T = picks[g["year"]]["T"]
    rows = []
    for acct, label in (("taxable", "Taxable"), ("ira", "IRA / 401(k)"), ("roth", "Roth")):
        nom, _ = best_nominal(T, g["year"], acct, profile, sched, mi, infl, today, min_credit=min_credit, after_tax=after_tax)
        tips = delivered_per_dollar("tips", None, T, g["year"], acct, profile, sched, mi, infl, today, after_tax)
        rows.append({"account": acct, "label": label,
                     "nominal": {"label": nom["label"], "spendable_per_dollar": round(nom["growth"], 4), "after_tax_pct": ps._pct(nom["after_tax_yield"])} if nom else None,
                     "tips": {"spendable_per_dollar": round(tips["growth"], 4), "after_tax_pct": ps._pct(tips["after_tax_yield"])} if tips else None})
    return {"year": g["year"], "rows": rows,
            "note": "Money already inside an IRA/401(k) is pre-tax (the tax comes out when you withdraw); a Roth dollar is after-tax "
                    "and grows tax-free. TIPS in a taxable account are taxed every year on inflation growth you haven't received."}
