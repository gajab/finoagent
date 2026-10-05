"""What to buy next — put new money (or maturing cash) to work so the WHOLE book is balanced.

Candidates are today's yields for Treasuries, TIPS, brokered CDs, agencies, in-state munis and AA/A/BBB
corporates at each maturity (the same model as the after-tax yield menu), taxed for the account you buy in.
ONE linear program chooses the dollars:

    max  Σ after-tax yield × $              (return you keep)
    s.t. book duration after buying within the target band       (interest-rate / duration risk)
         TIPS ≥ target share of the book                          (inflation risk)
         corporates ≤ cap, BBB & below ≤ cap, minimum credit       (credit risk)
         ≤ max share per line and per maturity                    (concentration / reinvestment risk)

Duration and TIPS targets are soft (a big penalty) so a small amount still gets the closest feasible answer;
credit caps are hard. Every number is computed — Treasury/TIPS lines are mapped to real CUSIPs (FedInvest).
"""
from __future__ import annotations

import logging
from datetime import date, timedelta

from . import bond_market_service as mkt
from . import bond_math as bm
from . import bond_portfolio_service as ps
from .bond_ladder_service import pick_treasury

logger = logging.getLogger(__name__)

TENORS = [0.5, 1.0, 2.0, 3.0, 5.0, 7.0, 10.0, 20.0, 30.0]
KINDS = ("treasury", "tips", "cd", "agency", "muni", "corporate")
# credit level of each candidate: 0 = government / FDIC, 1 = AA-ish (agency, muni, AA corp), 2 = A, 3 = BBB
_CREDIT_LEVEL = {("treasury", None): 0, ("tips", None): 0, ("cd", None): 0, ("agency", None): 1, ("muni", None): 1,
                 ("corporate", "AA"): 1, ("corporate", "A"): 2, ("corporate", "BBB"): 3}
MIN_CREDIT = {"govt": 0, "AA": 1, "A": 2, "BBB": 3}
PRESETS = {
    "safety":   {"min_credit": "govt", "tips_min_pct": 20.0, "corp_max_pct": 0.0, "bbb_max_pct": 0.0,
                 "max_line_pct": 35.0, "max_years": 10.0, "duration_tolerance": 0.5, "empty_book_duration": 3.0},
    "balanced": {"min_credit": "A", "tips_min_pct": 15.0, "corp_max_pct": 25.0, "bbb_max_pct": 0.0,
                 "max_line_pct": 30.0, "max_years": 20.0, "duration_tolerance": 0.75, "empty_book_duration": 5.0},
    "income":   {"min_credit": "BBB", "tips_min_pct": 5.0, "corp_max_pct": 40.0, "bbb_max_pct": 10.0,
                 "max_line_pct": 30.0, "max_years": 30.0, "duration_tolerance": 1.0, "empty_book_duration": 6.0},
}
MAX_TENOR_PCT = 40.0            # no single maturity takes more than this share of the purchase
MIN_LINE_PCT = 2.0              # lines smaller than this share of the purchase are dropped and the plan re-solved
TIPS_MIN_TENOR = 2.0            # very short TIPS: CPI lag / seasonality dominate the real yield
DUR_PENALTY = 0.02              # per $ per year outside the duration band (≫ any yield pickup)
TIPS_PENALTY = 0.01             # per $ short of the TIPS target


def par_duration(y: float, t: float) -> float:
    """Modified duration of a par bond (semiannual) — what a newly bought bond adds."""
    if t <= 0:
        return 0.0
    if t <= 1.0:                                  # bills / short CDs: ~ time to maturity
        return t / (1 + y * t)
    if abs(y) < 1e-9:
        return t
    return (1 - (1 + y / 2) ** (-2 * t)) / y


def _book_exposures(rows: list[dict], agg: dict) -> dict:
    """Current book in the planner's buckets (the same credit split the Overview uses: funds by credit mix)."""
    live = [r for r in rows if r["status"] == "held" and not r.get("matured")]
    mv = sum(r.get("market_value") or 0.0 for r in live)
    credit = {c["key"]: c["value"] for c in agg["allocation"]["by_credit"]}
    tips = corp = muni = 0.0
    for r in live:
        v = r.get("market_value") or 0.0
        f = r.get("fund") or {}
        name = f"{r.get('name') or ''} {r.get('label') or ''} {f.get('category') or ''}".lower()
        if r["kind"] == "tips" or (r["kind"] in ps.FUNDS and ("tips" in name or "inflation" in name)):
            tips += v
        if r["kind"] == "muni" or (r["kind"] in ps.FUNDS and f.get("tax_class") == "muni"):
            muni += v
        if r["kind"] == "corporate":
            corp += v
        elif r["kind"] in ps.FUNDS and f.get("tax_class") == "taxable" and not f.get("cash_like"):
            mix = f.get("credit_mix") or {}
            gov = min(mix.get("us_government") or 0.0, 1.0)
            if any(x for k, x in mix.items() if k != "us_government"):   # unknown mix ≠ corporate
                corp += v * (1 - gov)
    s = agg["summary"]
    return {
        "mv": mv, "dv01": s.get("dv01") or 0.0, "tips": tips, "corp": corp, "muni": muni,
        "govt": credit.get("GOVT", 0.0),
        "below_a": sum(credit.get(k, 0.0) for k in ("BBB", "BB", "B", "CCC")),
        "pre_income": mv * (s.get("total_yield_pct") or 0.0) / 100.0,
        "at_income": mv * (s.get("after_tax_yield_pct") or 0.0) / 100.0,
        "krd": {k["tenor"]: k["dv01"] for k in agg["key_rate_dv01"]},
        "by_kind": {a["key"]: a["value"] for a in agg["allocation"]["by_kind"]},
    }


def _metrics(mv: float, dv01: float, pre_income: float, at_income: float, tips: float, corp: float,
             below_a: float, govt: float, muni: float) -> dict:
    dur = dv01 / (mv * bm.BP) if mv else 0.0
    return {
        "market_value": round(mv, 2), "duration": round(dur, 2), "dv01": round(dv01, 2),
        "rate_shock_1pct": round(-dur * mv * 0.01, 2),
        "total_yield_pct": ps._pct(pre_income / mv) if mv else None,
        "after_tax_yield_pct": ps._pct(at_income / mv) if mv else None,
        "after_tax_income": round(at_income, 2), "tax_drag": round(pre_income - at_income, 2),
        "tips_pct": round(100 * tips / mv, 1) if mv else 0.0,
        "corporate_pct": round(100 * corp / mv, 1) if mv else 0.0,
        "below_a_pct": round(100 * below_a / mv, 1) if mv else 0.0,
        "government_pct": round(100 * govt / mv, 1) if mv else 0.0,
        "muni_pct": round(100 * muni / mv, 1) if mv else 0.0,
    }


def _look_for(c: dict, state: str | None) -> str:
    t = c["tenor_label"]
    return {
        "cd": f"Brokered CD maturing in ~{t}, non-callable; keep ≤ $250k per bank (FDIC) — buy only at ≥ {c['pre_tax_pct']:.2f}%",
        "agency": f"Non-callable FHLB / FFCB bullet, ~{t} (callables pay more but get called when rates fall) — FHLB/FFCB interest is state-tax-free",
        "muni": f"{state or 'In-state'} GO or essential-service revenue bond, AA or better, ~{t}; non-callable or call ≥ 8 years out; yield ≥ {c['pre_tax_pct']:.2f}% (TEY {c['tey_pct']:.2f}%)",
        "corporate": f"{c['rating']}-rated or better, non-callable, ~{t}; no single issuer above ~3% of the book; yield ≥ {c['pre_tax_pct']:.2f}%",
    }.get(c["kind"], "")


def _security(c: dict, cat_rows: list[dict], settle: date) -> dict | None:
    """The real Treasury / TIPS (FedInvest) closest to the candidate's maturity."""
    target = settle + timedelta(days=round(365.25 * c["tenor"]))
    if c["kind"] == "treasury":
        r = pick_treasury(cat_rows, target)
        if not r:
            return None
        return {"cusip": r["cusip"], "type": r["type"], "coupon_pct": r["coupon_pct"], "maturity": r["maturity"],
                "price": r["price"], "yield_pct": r.get("ytm_pct"), "real": False}
    if c["kind"] == "tips":
        tips = [r for r in cat_rows if r["type"] == "tips" and r.get("real_yield_pct") is not None]
        if not tips:
            return None
        r = min(tips, key=lambda r: abs((date.fromisoformat(r["maturity"]) - target).days))
        if abs((date.fromisoformat(r["maturity"]) - target).days) > 550:
            return None
        return {"cusip": r["cusip"], "type": "tips", "coupon_pct": r["coupon_pct"], "maturity": r["maturity"],
                "price": r["price"], "yield_pct": r.get("real_yield_pct"), "real": True,
                "index_ratio": r.get("index_ratio")}
    return None


def resolve_params(params: dict, book_duration: float, profile: dict) -> dict:
    preset = (params.get("preset") or "balanced").lower()
    base = dict(PRESETS.get(preset, PRESETS["balanced"]))
    out = {**base, **{k: v for k, v in params.items() if v is not None}, "preset": preset if preset in PRESETS else "balanced"}
    out["amount"] = max(0.0, float(out.get("amount") or 0.0))
    out["account_type"] = (out.get("account_type") or "taxable").lower()
    out["kinds"] = [k for k in (out.get("kinds") or list(KINDS)) if k in KINDS]
    if out.get("target_duration") is None:
        out["target_duration"] = round(book_duration, 2) if book_duration > 0 else base["empty_book_duration"]
        out["target_duration_source"] = "your current book" if book_duration > 0 else f"{preset} default"
    else:
        out["target_duration_source"] = "yours"
    if out["min_credit"] not in MIN_CREDIT:
        out["min_credit"] = base["min_credit"]
    return out


def solve(cands: list[dict], A: float, book: dict, p: dict) -> tuple[list[float], dict] | None:
    """The LP. Returns ($ per candidate, diagnostics) or None (SciPy unavailable / infeasible)."""
    try:
        import numpy as np
        from scipy.optimize import linprog
    except Exception:  # noqa: BLE001
        return None
    n = len(cands)
    M1 = book["mv"] + A
    # variables: x_0..x_{n-1}, s_dur_lo, s_dur_hi, s_tips
    N = n + 3
    c = np.zeros(N)
    for i, cd in enumerate(cands):
        c[i] = -cd["after_tax"] + 1e-5 * cd["credit_level"]          # tie-break: safer credit at equal yield
    c[n], c[n + 1], c[n + 2] = DUR_PENALTY, DUR_PENALTY, TIPS_PENALTY
    A_ub, b_ub = [], []
    d0 = book["dv01"] / bm.BP                                           # $·years already in the book
    lo = (p["target_duration"] - p["duration_tolerance"]) * M1 - d0
    hi = (p["target_duration"] + p["duration_tolerance"]) * M1 - d0
    row = np.zeros(N); row[:n] = [cd["duration"] for cd in cands]; row[n + 1] = -1.0
    A_ub.append(row); b_ub.append(hi)                                   # Σ D x − s_hi ≤ hi
    row = np.zeros(N); row[:n] = [-cd["duration"] for cd in cands]; row[n] = -1.0
    A_ub.append(row); b_ub.append(-lo)                                  # Σ D x + s_lo ≥ lo
    tips_need = p["tips_min_pct"] / 100 * M1 - book["tips"]
    if tips_need > 0:
        row = np.zeros(N); row[:n] = [-1.0 if cd["kind"] == "tips" else 0.0 for cd in cands]; row[n + 2] = -1.0
        A_ub.append(row); b_ub.append(-min(tips_need, A))
    corp_room = max(0.0, p["corp_max_pct"] / 100 * M1 - book["corp"])
    row = np.zeros(N); row[:n] = [1.0 if cd["kind"] == "corporate" else 0.0 for cd in cands]
    A_ub.append(row); b_ub.append(corp_room)
    bbb_room = max(0.0, p["bbb_max_pct"] / 100 * M1 - book["below_a"])
    row = np.zeros(N); row[:n] = [1.0 if cd["credit_level"] >= 3 else 0.0 for cd in cands]
    A_ub.append(row); b_ub.append(bbb_room)
    for t in sorted({cd["tenor"] for cd in cands}):
        row = np.zeros(N); row[:n] = [1.0 if cd["tenor"] == t else 0.0 for cd in cands]
        A_ub.append(row); b_ub.append(MAX_TENOR_PCT / 100 * A)
    A_eq = np.zeros((1, N)); A_eq[0, :n] = 1.0
    cap = max(p["max_line_pct"], 100.0 / max(1, n)) / 100 * A
    bounds = [(0.0, cap)] * n + [(0.0, None)] * 3
    try:
        res = linprog(c, A_ub=np.asarray(A_ub), b_ub=np.asarray(b_ub), A_eq=A_eq, b_eq=[A], bounds=bounds, method="highs")
    except Exception:  # noqa: BLE001
        return None
    if not res.success:
        return None
    x = list(res.x[:n])
    return x, {"duration_short_years": float(res.x[n]) / M1 if M1 else 0.0,
               "duration_over_years": float(res.x[n + 1]) / M1 if M1 else 0.0,
               "tips_short": float(res.x[n + 2]), "corp_room": corp_room, "bbb_room": bbb_room}


def _round_lines(x: list[float], A: float, step: float = 1000.0) -> list[float]:
    """Whole $1,000 lots (bonds trade in $1k face); the rounding leftover goes to the biggest line."""
    if A < step * 2:
        return x
    r = [step * round(v / step) if v >= step / 2 else 0.0 for v in x]
    if any(r):
        big = max(range(len(r)), key=lambda i: r[i])
        r[big] += step * round((A - sum(r)) / step)
        r[big] = max(0.0, r[big])
    return r


async def buy_plan(holdings: list[dict], profile: dict, params: dict) -> dict:
    ctx = await ps.load_context(holdings, profile)
    rows, internals = ps.analyze_rows(holdings, profile, ctx)
    agg = ps.aggregate(rows, internals)
    book = _book_exposures(rows, agg)
    d0 = book["dv01"] / (book["mv"] * bm.BP) if book["mv"] else 0.0
    p = resolve_params(params, d0, profile)
    A = p["amount"]
    acct = p["account_type"]
    taxable_acct = acct not in ps.TAX_ADVANTAGED
    menu = ps.after_tax_menu(ctx.mi, profile, [t for t in TENORS if t <= p["max_years"] + 1e-9],
                             account=acct, inflation=ctx.inflation, kinds=set(p["kinds"]))
    npts = (ctx.mi.get("nominal") or {}).get("points") or []
    cands: list[dict] = []
    for row in menu:
        t = row["tenor"]
        tsy_at = next((c["after_tax_pct"] for c in row["candidates"] if c["kind"] == "treasury"), None)
        for c in row["candidates"]:
            lvl = _CREDIT_LEVEL.get((c["kind"], c["rating"]))
            if lvl is None or lvl > MIN_CREDIT[p["min_credit"]]:
                continue
            if c["kind"] == "muni" and not taxable_acct:
                continue                         # tax-exempt income wasted inside an IRA/Roth
            if c["kind"] == "tips" and t < TIPS_MIN_TENOR:
                continue
            if c["after_tax_pct"] is None or c["pre_tax_pct"] is None:
                continue
            y_for_dur = (c["real_yield_pct"] if c["kind"] == "tips" else c["pre_tax_pct"]) / 100.0
            tsy_pre = bm.interp(npts, t)
            cands.append({**c, "tenor": t, "tenor_label": mkt._years_label(t) if hasattr(mkt, "_years_label") else f"{t:g}y",
                          "after_tax": c["after_tax_pct"] / 100.0, "credit_level": lvl,
                          "duration": par_duration(y_for_dur, t),
                          "vs_treasury_after_tax_pct": round(c["after_tax_pct"] - tsy_at, 3) if tsy_at is not None else None,
                          "spread_bp": round((c["pre_tax_pct"] / 100 - tsy_pre) * 1e4) if (tsy_pre is not None and c["kind"] in ("corporate", "agency", "cd")) else None})
    before = _metrics(book["mv"], book["dv01"], book["pre_income"], book["at_income"], book["tips"], book["corp"],
                      book["below_a"], book["govt"], book["muni"])
    from .bond_gap_service import resilience          # noqa: PLC0415 — gap service imports this module
    base = {"params": {k: v for k, v in p.items() if k != "empty_book_duration"}, "before": before,
            "resilience": {"before": resilience(rows)},
            "book_duration": round(d0, 2), "as_of": ctx.settle.isoformat(), **ps.inflation_fields(ctx.inflation)}
    if A <= 0 or not cands:
        return {**base, "lines": [], "after": before, "notes": ["Enter an amount to invest." if A <= 0
                                                                 else "No instrument matches these limits — loosen the credit or maturity limits."]}
    solved = solve(cands, A, book, p)
    if solved is not None:                     # drop crumbs (< 2% of the money) and re-solve without them
        crumbs = {i for i, v in enumerate(solved[0]) if 0.5 < v < max(1000.0, MIN_LINE_PCT / 100 * A)}
        if crumbs:
            kept = [c for i, c in enumerate(cands) if i not in crumbs]
            again = solve(kept, A, book, p) if kept else None
            if again is not None:
                cands, solved = kept, again
    if solved is None:
        return {**base, "lines": [], "after": before, "notes": ["The optimizer couldn't find a plan with these limits."]}
    x, diag = solved
    x = _round_lines(x, A)
    cat = await mkt.treasury_catalogue()
    cat_rows = (cat or {}).get("rows") or []
    best_at = {}
    for cd in cands:
        best_at[cd["tenor"]] = max(best_at.get(cd["tenor"], -1.0), cd["after_tax"])
    line_cap = max(p["max_line_pct"], 100.0 / max(1, len(cands))) / 100 * A
    at_cap = {(cd["tenor"], cd["label"]) for cd, v in zip(cands, x) if v >= line_cap - 500}
    lines, add = [], {"dv01": 0.0, "pre": 0.0, "at": 0.0, "tips": 0.0, "corp": 0.0, "below_a": 0.0, "govt": 0.0, "muni": 0.0}
    krd_add: dict[float, float] = {}
    for cd, amt in zip(cands, x):
        if amt < 1.0:
            continue
        dv01 = cd["duration"] * amt * bm.BP
        add["dv01"] += dv01
        add["pre"] += amt * cd["pre_tax_pct"] / 100
        add["at"] += amt * cd["after_tax"]
        add["tips"] += amt if cd["kind"] == "tips" else 0.0
        add["corp"] += amt if cd["kind"] == "corporate" else 0.0
        add["below_a"] += amt if cd["credit_level"] >= 3 else 0.0
        add["govt"] += amt if cd["credit_level"] == 0 else 0.0
        add["muni"] += amt if cd["kind"] == "muni" else 0.0
        for k, w in bm._tenor_weights(cd["tenor"]).items():
            krd_add[k] = krd_add.get(k, 0.0) + dv01 * w
        tags = []
        if cd["kind"] in ("treasury", "tips"):
            tags.append("no credit risk · state-tax-free")
        elif cd["kind"] == "cd":
            tags.append("FDIC-insured ≤ $250k per bank")
        elif cd["kind"] == "muni":
            tags.append("federal + in-state tax-free")
        elif cd["kind"] == "agency":
            tags.append("government-sponsored · AA+")
        else:
            tags.append(f"{cd['rating']} credit" + (f" · +{cd['spread_bp']}bp over Treasury" if cd.get("spread_bp") else ""))
        if cd["kind"] == "tips":
            tags.append("inflation-protected")
        why = []
        if cd["kind"] == "tips" and p["tips_min_pct"] > 0 and before["tips_pct"] < p["tips_min_pct"]:
            why.append(f"inflation protection toward your {p['tips_min_pct']:.0f}% TIPS target")
        if cd["after_tax"] >= best_at[cd["tenor"]] - 1e-9:
            why.append(f"highest after-tax yield at {cd['tenor_label']} within your limits")
        else:
            better = [o for o in cands if o["tenor"] == cd["tenor"] and o["after_tax"] > cd["after_tax"] + 1e-9]
            if better and all((o["tenor"], o["label"]) in at_cap for o in better):
                why.append(f"the better {better[0]['label']} at {cd['tenor_label']} is at your {p['max_line_pct']:.0f}% per-line limit")
            elif cd.get("vs_treasury_after_tax_pct") is not None and cd["kind"] != "treasury":
                why.append(f"{cd['vs_treasury_after_tax_pct']:+.2f}% after tax vs a Treasury at {cd['tenor_label']}")
            else:
                why.append("no credit risk; spreads the money across maturities (≤ 40% in any one)")
        lines.append({
            "kind": cd["kind"], "label": cd["label"], "rating": cd["rating"], "tenor": cd["tenor"], "tenor_label": cd["tenor_label"],
            "amount": round(amt, 2), "share_pct": round(100 * amt / A, 1),
            "pre_tax_pct": cd["pre_tax_pct"], "real_yield_pct": cd.get("real_yield_pct"), "after_tax_pct": cd["after_tax_pct"],
            "tey_pct": cd["tey_pct"], "tax_rate_pct": cd["tax_rate_pct"], "duration": round(cd["duration"], 2),
            "dv01": round(dv01, 2), "annual_after_tax": round(amt * cd["after_tax"], 2), "basis": cd["basis"],
            "tags": tags, "why": "; ".join(why), "look_for": _look_for(cd, (profile or {}).get("state")),
            "security": _security(cd, cat_rows, ctx.settle),
        })
    lines.sort(key=lambda l: (l["tenor"], l["kind"]))
    M1 = book["mv"] + A
    after = _metrics(M1, book["dv01"] + add["dv01"], book["pre_income"] + add["pre"], book["at_income"] + add["at"],
                     book["tips"] + add["tips"], book["corp"] + add["corp"], book["below_a"] + add["below_a"],
                     book["govt"] + add["govt"], book["muni"] + add["muni"])
    kinds_after = dict(book["by_kind"])
    for l in lines:
        key = mkt.KIND_LABELS.get(l["kind"], l["kind"])
        kinds_after[key] = kinds_after.get(key, 0.0) + l["amount"]
    notes = []
    if diag["duration_short_years"] > 0.05 or diag["duration_over_years"] > 0.05:
        notes.append(f"With ${A:,.0f} the book can only reach {after['duration']:.2f}y duration (target "
                     f"{p['target_duration']:.2f} ± {p['duration_tolerance']:.2f}) — a bigger amount, or selling, is needed to move it further.")
    if after["tips_pct"] < p["tips_min_pct"] - 0.1:
        gap = p["tips_min_pct"] / 100 * M1 - (book["tips"] + add["tips"])
        notes.append(f"TIPS reach {after['tips_pct']:.1f}% of the book — about ${gap:,.0f} more TIPS would hit your "
                     f"{p['tips_min_pct']:.0f}% target" + (" (this whole amount already went to TIPS)." if add["tips"] >= A - 1 else "."))
    if p["corp_max_pct"] > 0 and diag["corp_room"] < 1:
        notes.append(f"No room for corporates: they're already {before['corporate_pct']:.1f}% of the book (cap {p['corp_max_pct']:.0f}%).")
    notes += [
        "Yields are today's curve estimates (Treasury par curve; TIPS real curve + your inflation path; CDs/agencies/munis/corporates "
        "curve + typical spreads) — check the live quote; Treasury and TIPS lines show a real CUSIP priced at FedInvest's last close.",
        f"After-tax yields are for a {acct.upper() if not taxable_acct else 'taxable'} account at your rates; "
        + ("munis are excluded inside a tax-advantaged account." if not taxable_acct else
           "Treasury/TIPS interest is state-tax-free; in-state munis are federal + state tax-free."),
        "Duration = rate risk: each 1% rise in rates costs about duration × 1% of the book's value (and each fall adds it).",
    ]
    return {**base, "lines": lines, "after": after, "amount": A,
            "allocation": {"before": [{"key": k, "value": round(v, 2)} for k, v in sorted(book["by_kind"].items(), key=lambda kv: -kv[1])],
                           "after": [{"key": k, "value": round(v, 2)} for k, v in sorted(kinds_after.items(), key=lambda kv: -kv[1])]},
            "krd": [{"tenor": t, "before": round(book["krd"].get(t, 0.0), 2), "after": round(book["krd"].get(t, 0.0) + krd_add.get(t, 0.0), 2)}
                    for t in bm.KEY_TENORS],
            "candidates_considered": len(cands), "notes": notes}
