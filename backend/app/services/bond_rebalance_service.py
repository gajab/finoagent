"""Balance the whole book — keep, sell or swap every holding, account by account, so the plan is funded
at the best after-tax outcome with as little change as that takes.

One linear program over the planner's own inputs (each holding's after-tax cash by year, the bond funds'
sale mechanics, Social Security and other income, the goal years), solved jointly across the inflation
worlds you choose to be robust to:

    today    sell a fraction of any bond / fund  →  proceeds stay in THAT account (taxable sales pay the
             capital-gains tax, or bank the loss)  →  plus any new money  →  buy bonds maturing in the
             years the plan spends (TIPS, or the best after-tax of Treasury / CD / agency / muni / corporate
             for that account and year)
    later    in every inflation world: coupons, maturities, income, fund distributions and fund sales pay
             each year's need; cash carries in T-bills

    min   Σ worlds  p · [ shortfall penalties (nearest years first)  +  rate-risk charge on future fund sales
                          −  after-tax wealth left at the end ]
          +  trading costs  +  a "leave it alone" charge on everything sold
    s.t.  proceeds can't leave their account;  turnover ≤ your limit;  credit caps;
          don't buy more for a year than the year needs.

So a holding is replaced only when the swap pays for its tax, its trading cost and the nuisance — FUNDS FIRST
(cheap to trade, never mature): a fund replaced by bonds maturing when you spend, a low-yielding cash fund by a
Treasury ladder. What's bought stays balanced — a cap on the inflation-linked share and on any one credit type —
and every world is valued in today's purchasing power, so the book has to work in a deflation as well as in high
inflation. An optional non-dollar sleeve (unhedged international bond funds) diversifies the currency. The recommendation is then APPLIED to
the book and re-run through the planner in all four worlds; if the planner doesn't agree it's better, the
answer is "hold".
"""
from __future__ import annotations

import logging
from datetime import date

from . import bond_gap_service as gap
from . import bond_ladder_service as lad
from . import bond_market_service as mkt
from . import bond_math as bm
from . import bond_portfolio_service as ps
from .bond_buy_service import MIN_CREDIT

logger = logging.getLogger(__name__)

# what it costs to trade $1 (bid-ask + friction); mutual funds trade at NAV
TXN_COST = {"treasury": 0.001, "tips": 0.002, "etf": 0.0005, "mutual_fund": 0.0, "cd": 0.005, "agency": 0.004,
            "muni": 0.01, "corporate": 0.0075}
ROBUST = {"expected": ["base"], "both": ["low", "base", "high"], "high": ["base", "high"],
          "all": ["low", "base", "high", "debase"]}
WEIGHT = {"low": 0.15, "base": 0.50, "high": 0.25, "debase": 0.10}
LINE_CAP = {"corporate": 0.05, "muni": 0.05, "agency": 0.10}      # share of the book in one non-government line
TICKET = 25.0                                                      # nuisance of trading one position at all ($)
BOND_STICKY, FUND_STICKY = 3.0, 0.2          # × the "leave it alone" charge: funds are the first thing to sell
TYPE_KEYS = ("cd", "agency", "muni", "corporate")                  # credit types capped as a share of what's bought
# unhedged international bond funds for an optional non-dollar sleeve (share of the sleeve)
INTL_FUNDS = [("IGOV", 0.7, "developed-market government bonds in their own currencies"),
              ("EMLC", 0.3, "emerging-market government bonds in local currencies")]
CD_CAP = 250000.0                                                  # FDIC limit per bank


def _sale_tax_rate(row: dict, rates: bm.TaxRates, today: date) -> float:
    """Tax per $ of market value if sold TODAY in a taxable account: gain share × (long- or short-term rate);
    negative for a loss (tax saved). Sales inside an IRA / Roth / HSA aren't taxed — the money stays inside."""
    acct = (row.get("account_type") or "taxable").lower()
    mv, pnl = row.get("market_value") or 0.0, row.get("unrealized_pnl")
    if acct in lad.SHELTERED or not mv or pnl is None:
        return 0.0
    held = row.get("purchase_date")
    long_term = (not held) or (today - date.fromisoformat(held)).days > 365
    rate = (rates.ltcg if long_term else rates.fed) + rates.niit + rates.state
    return max(-0.3, min(0.5, pnl / mv * rate))


async def rebalance(holdings: list[dict], profile: dict, params: dict) -> dict:
    try:
        import numpy as np
        from scipy.optimize import linprog
    except Exception:  # noqa: BLE001
        return {"verdict": "unavailable", "notes": ["The optimizer isn't available on this server."]}
    today = mkt.us_today()
    after_tax = params.get("after_tax", True)
    min_credit = params.get("min_credit") if params.get("min_credit") in MIN_CREDIT else "AA"
    robust = params.get("robustness") if params.get("robustness") in ROBUST else "both"
    tips_max = max(0.0, min(100.0, gap._pnum(params, "tips_max_pct", 50.0))) / 100       # share of what's BOUGHT
    type_max = max(0.0, min(100.0, gap._pnum(params, "type_max_pct", 35.0))) / 100       # any one credit type
    intl_pct = max(0.0, min(30.0, gap._pnum(params, "intl_pct", 0.0))) / 100             # non-dollar sleeve, share of the book
    # funds first: individual bonds may use only this share of the change limit (0 = sell funds only)
    bond_share = max(0.0, min(100.0, gap._pnum(params, "bond_turnover_pct", 25.0))) / 100
    new_money = max(0.0, float(params.get("new_money") or 0.0))
    new_acct = (params.get("new_money_account") or "taxable").lower()
    turnover_max = max(0.0, min(100.0, float(params.get("max_turnover_pct") if params.get("max_turnover_pct") is not None else 25.0))) / 100
    sticky = max(0.0, float(params.get("stickiness_pct") if params.get("stickiness_pct") is not None else 0.5)) / 100
    corp_max = max(0.0, float(params.get("corp_max_pct") if params.get("corp_max_pct") is not None else 25.0)) / 100
    kw = dict(after_tax=after_tax, use_funds=True, reinvest=True, withdrawal_mode=params.get("withdrawal_mode"))
    keep_ids = {int(i) for i in (params.get("keep_ids") or []) if str(i).lstrip("-").isdigit()}   # holdings you won't sell

    rc = await mkt.rate_context()
    base_path, infl_src = ps.inflation_assumption(profile, rc)
    scs = {s["key"]: s for s in gap.scenario_defs(base_path, gap._pnum(params, "low_inflation", -1.0),
                                                  gap._pnum(params, "high_inflation", 5.0), gap._pnum(params, "debase_inflation", 6.0))}
    use = ROBUST[robust]
    prob = {k: WEIGHT[k] / sum(WEIGHT[x] for x in use) for k in use}
    cat = await mkt.treasury_catalogue()
    cat_rows = (cat or {}).get("rows") or []

    async def plan(hs: list[dict], key: str, **extra) -> dict:
        sc = scs[key]
        prof = profile if key == "base" else gap._with_inflation(profile, sc)
        return await lad.plan_goals(hs, prof, rate_shift=sc["rate_shift"], fx_drift=sc["fx_drift"], **kw, **extra)

    ing = {k: await plan(holdings, k, ingredients=True) for k in use}
    b = ing["base"]
    years, sched, mi = b["years"], b["sched"], b["mi"]
    T = len(years)
    rows = {r["id"]: r for r in b["rows"]}
    live = [r for r in b["rows"] if r["status"] == "held" and not r.get("matured") and (r.get("market_value") or 0) > 0]
    M0 = sum(r["market_value"] for r in live)
    M1 = M0 + new_money
    out_base = {"as_of": today.isoformat(), "robustness": robust, "new_money": new_money, "new_money_account": new_acct,
                "max_turnover_pct": round(turnover_max * 100, 1), "min_credit": min_credit, "book_value": round(M0, 2),
                **ps.inflation_fields(base_path, infl_src), "has_goals": bool((profile or {}).get("goals")),
                "scenarios": [{k: (round(v * 100, 3) if k in ("short", "long", "rate_shift", "fx_drift") else v) for k, v in scs[key].items()}
                              for key in ("low", "base", "high", "debase")],
                "worlds_optimized": [scs[k]["label"] for k in use]}
    if not out_base["has_goals"] or not any(b["need"].get(y, 0) > 0 for y in years):
        return {**out_base, "verdict": "no_goals", "sells": [], "buys": [], "why": [],
                "notes": ["Add your goals in the Planner — the rebalancer builds the book that funds them."]}

    rates_now = sched.now
    funds0 = {f["id"]: f for f in b["funds"]}
    fund_ids = list(funds0)
    # individual holdings the plan can sell: everything that isn't an open-ended fund, pays inside the plan
    # horizon and has a market price (a bank CD can't be sold — it stays)
    released = b["released_year"] if b["lock_early"] else None
    roll = [r for r in live if r["id"] not in b["open_ids"] and released and lad._is_retirement(r["account_type"])
            and (r.get("maturity_year") or 9999) < released]
    roll_ids = {r["id"] for r in roll}
    H = [r for r in live if r["id"] not in b["open_ids"] and not r.get("no_mark_to_market") and r["id"] not in keep_ids
         and r["id"] not in roll_ids
         and (r.get("maturity_year") or r.get("defined_maturity_year") or 9999) <= b["plan_end"]
         and b["inflow_by_holding"].get(r["id"])]
    p_h = {r["id"]: 1 - (_sale_tax_rate(r, rates_now, today) if after_tax else 0.0) - TXN_COST.get(r["kind"], 0.005) for r in H}
    p_f = {fid: 1 - (_sale_tax_rate(rows[fid], rates_now, today) if after_tax else 0.0) - TXN_COST.get(rows[fid]["kind"], 0.0005) for fid in fund_ids}
    accounts = sorted({r["account_type"] for r in H} | {rows[fid]["account_type"] for fid in fund_ids} | ({new_acct} if new_money > 0 else set()))
    first_frac = max(0.0, (date(today.year, 12, 31) - today).days / 365.25)

    # ---- what can be bought: per account and spending year, TIPS + every nominal type within the credit limit ----
    cands = []
    for a in accounts:
        locked_from = b["released_year"] if (b["lock_early"] and lad._is_retirement(a)) else None
        for t, y in enumerate(years):
            if t == 0 or b["need"].get(y, 0.0) <= 0 or (locked_from and y < locked_from):
                continue
            Ty = max(0.25, bm.year_frac(today, date(y, 6, 30)))
            _, field = gap.best_nominal(Ty, y, a, profile, sched, mi, base_path, today, min_credit=min_credit, after_tax=after_tax)
            for nom in field:
                cands.append({"a": a, "t": t, "y": y, "leg": "nominal", "c": nom, "g": {k: nom["growth"] for k in use}})
            g = {}
            for k in use:
                d = gap.delivered_per_dollar("tips", None, Ty, y, a, profile, sched, mi, ing[k]["infl"], today, after_tax)
                if d:
                    g[k] = d
            if len(g) == len(use):
                cands.append({"a": a, "t": t, "y": y, "leg": "tips", "c": {"kind": "tips", "rating": None, "label": "TIPS", **g["base"]},
                              "g": {k: v["growth"] for k, v in g.items()}})
    # optional non-dollar sleeve: unhedged international bond funds, held as a diversifier to the end of the plan.
    # In each world their dollar value drifts with the dollar (fx_drift): up if the dollar weakens, down if it strengthens.
    res_before = gap.resilience(b["rows"])
    intl_target = max(0.0, intl_pct * M1 - res_before["non_usd_pct"] / 100 * M0)
    intl_info: dict[str, dict] = {}
    if intl_target >= 1000:
        got = [(tkr, w, what, await mkt.fund_profile_full(tkr)) for tkr, w, what in INTL_FUNDS]
        got = [x for x in got if x[3] and x[3].get("price") and x[3].get("distribution_yield_pct")]
        wsum = sum(x[1] for x in got)
        Tend = max(1.0, lad._years_to(today, years[-1]))
        for tkr, w, what, fp in got:
            y_ = fp["distribution_yield_pct"] / 100
            intl_info[tkr] = {"ticker": tkr, "name": fp.get("name") or tkr, "what": what, "share": w / wsum, "price": fp["price"],
                              "yield_pct": fp["distribution_yield_pct"], "duration": fp.get("duration"), "expense_ratio_pct": fp.get("expense_ratio_pct")}
            for a in accounts:
                tax = 0.0 if (a in lad.SHELTERED or not after_tax) else (rates_now.fed + rates_now.niit + rates_now.state)
                wd = (1 - sched.at(years[-1]).fed - sched.at(years[-1]).state) if (after_tax and a in lad.TRADITIONAL) else 1.0
                cands.append({"a": a, "t": T - 1, "y": years[-1], "leg": "intl", "ticker": tkr,
                              "c": {"kind": "etf", "rating": None, "label": fp.get("name") or tkr, "pre_tax_yield": y_,
                                    "after_tax_yield": y_ * (1 - tax), "real_yield": None, "growth": (1 + y_ * (1 - tax)) ** Tend * wd},
                              "g": {k: max(0.05, 1 + y_ * (1 - tax) + scs[k]["fx_drift"]) ** Tend * wd for k in use}})
        if not got:
            intl_target = 0.0

    # ---- variables ----
    nH, nF, nC, nA, S = len(H), len(fund_ids), len(cands), len(accounts), len(use)
    iu = lambda i: i                                                # noqa: E731 sell fraction of holding i
    iv = lambda f: nH + f                                           # noqa: E731 $ of fund f sold today
    iw = lambda c: nH + nF + c                                      # noqa: E731 $ bought of candidate c
    ik = lambda a: nH + nF + nC + a                                 # noqa: E731 cash left in account a
    base0 = nH + nF + nC + nA
    per = nF * T + 2 * T
    ix = lambda s, f, t: base0 + s * per + f * T + t                # noqa: E731 $ of fund f sold in year t (world s)
    ic = lambda s, t: base0 + s * per + nF * T + t                  # noqa: E731 carry
    ish = lambda s, t: base0 + s * per + nF * T + T + t             # noqa: E731 shortfall
    N = base0 + S * per
    cvec = np.zeros(N)
    A_eq, b_eq, A_ub, b_ub = [], [], [], []
    # cash: only NEW money may be left uninvested — a sale has to be for something better, not to sit in T-bills
    # (the plan already keeps its own surplus in T-bills year by year)
    bounds = ([(0.0, 1.0)] * nH + [(0.0, 0.0 if fid in keep_ids else funds0[fid]["value"]) for fid in fund_ids]
              + [(0.0, None)] * nC + [(0.0, new_money if a == new_acct else 0.0) for a in accounts])
    omega = (1 + b["r_carry"]) ** (-(T - 1))
    plan_end_t = b["plan_end"] + 0.5 - today.year
    for i, r in enumerate(H):                                # (tax and trading cost are already netted out of the proceeds)
        cvec[iu(i)] += BOND_STICKY * sticky * r["market_value"] + TICKET
    for f, fid in enumerate(fund_ids):                       # funds: cheap to trade and never mature → first to go
        cvec[iv(f)] += FUND_STICKY * sticky + (0.4 * TICKET / funds0[fid]["value"] if funds0[fid]["value"] else 0.0)
    for c in range(nC):
        cvec[iw(c)] += 1e-4                                                                     # no pointless purchases
    acct_idx = {a: j for j, a in enumerate(accounts)}
    for si, k in enumerate(use):
        d = ing[k]
        fs = {f["id"]: f for f in d["funds"]}
        G = {fid: lad._value_growth(fs[fid], years, first_frac, d["early_until"], d["lock_early"]) for fid in fund_ids}
        hit = {fid: (fs[fid]["value"] / funds0[fid]["value"]) if funds0[fid]["value"] else 1.0 for fid in fund_ids}
        # everything is valued in TODAY's purchasing power: a dollar short (or left over) in a high-inflation world is
        # worth less than one in a deflation — otherwise the objective itself would lean toward inflation-linked bonds
        real = [base_path.factor(max(0, y - today.year)) / d["infl"].factor(max(0, y - today.year)) for y in years]
        omega_s = omega * real[-1]
        for t, y in enumerate(years):
            row = np.zeros(N)
            const = d["bond_inflow"].get(y, 0.0) + d["income_net"].get(y, 0.0)
            for f, fid in enumerate(fund_ids):
                fd = fs[fid]
                locked = lad._locked(fd, y, d["early_until"], d["lock_early"])
                keep = lad._keep(fd, y, after_tax, d["early_until"], G[fid][t])
                dist = 0.0 if locked else fd["cash_yield"] * (first_frac if t == 0 else 1.0) * lad._dist_net(fd, y, after_tax, d["early_until"])
                row[ix(si, f, t)] += keep
                for tau in range(t):
                    row[ix(si, f, tau)] -= dist * G[fid][t] / G[fid][tau]
                row[iv(f)] -= dist * hit[fid] * G[fid][t]
                const += dist * fs[fid]["value"] * G[fid][t]
                # soft cost of a future sale: rate risk (a fund's price that year isn't known) + sheltered growth given up
                left = max(0.0, plan_end_t - lad._years_to(today, y))
                tt = lad._years_to(today, y)
                mism = 0.0 if fd["duration"] < lad.CASH_LIKE_DURATION else abs(tt - fd["duration"])
                soft = lad.RATE_RISK_PER_YEAR * mism + lad._tx(fd, y)["shelter_rate"] * fd["yield"] * left
                keep_T = lad._keep(fd, years[-1], after_tax, d["early_until"], G[fid][-1])
                cvec[ix(si, f, t)] += prob[k] * (soft * real[t] + omega_s * keep_T * G[fid][-1] / G[fid][t])
                if t == 0:
                    cvec[iv(f)] += prob[k] * omega_s * keep_T * hit[fid] * G[fid][-1]
            for i, r in enumerate(H):
                row[iu(i)] -= d["inflow_by_holding"].get(r["id"], {}).get(y, 0.0)
            for ci, c in enumerate(cands):
                if c["t"] == t:
                    row[iw(ci)] += c["g"][k]
            for a, j in acct_idx.items():
                if a not in lad.SHELTERED:
                    if t == 0:
                        row[ik(j)] += 1.0                           # taxable cash not reinvested: spendable now
                else:                                               # stays in the account in T-bills until it can come out
                    ta = max(1, years.index(d["released_year"]) if (d["lock_early"] and lad._is_retirement(a) and d["released_year"] in years) else 1)
                    if t == min(ta, T - 1):
                        wd = (1 - sched.at(y).fed - sched.at(y).state) if (after_tax and a in lad.TRADITIONAL) else 1.0
                        row[ik(j)] += (1 + d["r_bill"]) ** t * wd
            if t > 0:
                row[ic(si, t - 1)] = 1 + d["r_carry"]
            row[ic(si, t)] = -1.0
            row[ish(si, t)] = 1.0
            A_eq.append(row)
            b_eq.append(d["need"].get(y, 0.0) - const)
            cvec[ish(si, t)] += prob[k] * 50.0 * 1.25 ** (T - 1 - t) * real[t]
            cap = np.zeros(N)                                        # fund sales in a year ≤ that year's need
            for f, fid in enumerate(fund_ids):
                cap[ix(si, f, t)] = lad._keep(fs[fid], y, after_tax, d["early_until"], G[fid][t])
            A_ub.append(cap)
            b_ub.append(d["need"].get(y, 0.0))
        cvec[ic(si, T - 1)] -= prob[k] * omega_s
        for f, fid in enumerate(fund_ids):                           # can't sell more of a fund than there is
            row = np.zeros(N)
            for t in range(T):
                row[ix(si, f, t)] = 1.0 / G[fid][t]
            row[iv(f)] = hit[fid]
            A_ub.append(row)
            b_ub.append(fs[fid]["value"])
    # x / carry / shortfall bounds (locked or untaxable-negative years can't sell)
    for si, k in enumerate(use):
        d = ing[k]
        fs = {f["id"]: f for f in d["funds"]}
        xb = []
        for f, fid in enumerate(fund_ids):
            G_ = lad._value_growth(fs[fid], years, first_frac, d["early_until"], d["lock_early"])
            for t, y in enumerate(years):
                dead = lad._keep(fs[fid], y, after_tax, d["early_until"], G_[t]) <= 0 or lad._locked(fs[fid], y, d["early_until"], d["lock_early"])
                xb.append((0.0, 0.0) if dead else (0.0, None))
        bounds += xb + [(0.0, None)] * (2 * T)
    # account budgets: what's bought (+ cash left) = proceeds of what's sold there + new money put there
    for a, j in acct_idx.items():
        row = np.zeros(N)
        for ci, c in enumerate(cands):
            if c["a"] == a:
                row[iw(ci)] = 1.0
        row[ik(j)] = 1.0
        for i, r in enumerate(H):
            if r["account_type"] == a:
                row[iu(i)] = -r["market_value"] * p_h[r["id"]]
        for f, fid in enumerate(fund_ids):
            if rows[fid]["account_type"] == a:
                row[iv(f)] = -p_f[fid]
        A_eq.append(row)
        b_eq.append(new_money if a == new_acct else 0.0)
    bond_c = [ci for ci, c in enumerate(cands) if c["leg"] != "intl"]
    for y_ in sorted({cands[ci]["y"] for ci in bond_c}):             # don't buy more for a year than it needs
        row = np.zeros(N)
        for ci in bond_c:
            if cands[ci]["y"] == y_:
                row[iw(ci)] = cands[ci]["g"]["base"]
        A_ub.append(row)
        b_ub.append(b["need"].get(y_, 0.0))
    # balance of what's bought: inflation-linked ≤ your share; no single credit type dominates
    row = np.zeros(N)
    for ci in bond_c:
        row[iw(ci)] = (1 - tips_max) if cands[ci]["leg"] == "tips" else -tips_max
    A_ub.append(row)
    b_ub.append(0.0)
    for kind in TYPE_KEYS:
        if type_max < 1.0 and any(cands[ci]["c"]["kind"] == kind for ci in bond_c):
            row = np.zeros(N)
            for ci in bond_c:
                row[iw(ci)] = (1 - type_max) if cands[ci]["c"]["kind"] == kind else -type_max
            A_ub.append(row)
            b_ub.append(0.0)
    for tkr, info in intl_info.items():                              # the non-dollar sleeve you asked for: exactly its share
        row = np.zeros(N)
        for ci, c in enumerate(cands):
            if c["leg"] == "intl" and c["ticker"] == tkr:
                row[iw(ci)] = 1.0
        A_eq.append(row)
        b_eq.append(info["share"] * intl_target)
    row = np.zeros(N)                                                # turnover limit
    for i, r in enumerate(H):
        row[iu(i)] = r["market_value"]
    for f in range(nF):
        row[iv(f)] = 1.0
    A_ub.append(row)
    b_ub.append(turnover_max * M0)
    row = np.zeros(N)                                                # …of which individual bonds get only a part
    for i, r in enumerate(H):
        row[iu(i)] = r["market_value"]
    A_ub.append(row)
    b_ub.append(bond_share * turnover_max * M0)
    corp0 = sum(r["market_value"] for r in live if r["kind"] == "corporate")
    row = np.zeros(N)                                                # corporates ≤ cap of the book
    for ci, c in enumerate(cands):
        if c["c"]["kind"] == "corporate":
            row[iw(ci)] = 1.0
    for i, r in enumerate(H):
        if r["kind"] == "corporate":
            row[iu(i)] = -r["market_value"]
    A_ub.append(row)
    b_ub.append(max(0.0, corp_max * M1 - corp0))
    for ci, c in enumerate(cands):
        kind = c["c"]["kind"]
        if kind in LINE_CAP:
            bounds[iw(ci)] = (0.0, LINE_CAP[kind] * M1)
        elif kind == "cd":
            bounds[iw(ci)] = (0.0, CD_CAP)
    try:
        res = linprog(cvec, A_ub=np.asarray(A_ub), b_ub=np.asarray(b_ub), A_eq=np.asarray(A_eq), b_eq=np.asarray(b_eq),
                      bounds=bounds, method="highs")
    except Exception as exc:  # noqa: BLE001
        logger.warning("rebalance LP failed: %s", exc)
        res = None
    if (res is None or not res.success) and intl_info:               # the sleeve doesn't fit these limits → without it
        out = await rebalance(holdings, profile, {**params, "intl_pct": 0})
        out["notes"] = [f"The {intl_pct * 100:.0f}% non-dollar sleeve doesn't fit inside your change limit — raise the limit or add new money. "
                        "Shown without it."] + out.get("notes", [])
        return out
    if res is None or not res.success:
        return {**out_base, "verdict": "unavailable", "sells": [], "buys": [], "why": [],
                "notes": ["The optimizer couldn't find a plan with these limits — loosen the turnover or credit limit."]}
    x = res.x
    # ---- the trades, rounded to what you can actually execute ----
    sells, proceeds = [], {a: (new_money if a == new_acct else 0.0) for a in accounts}
    new_holdings = [dict(h) for h in holdings]
    by_id = {h.get("id"): h for h in new_holdings}
    for i, r in enumerate(H):
        u = float(x[iu(i)])
        face = r.get("face") or 0.0
        if u > 0.985:
            u = 1.0
        elif u < 0.05:
            u = 0.0
        elif face >= 2000:                                   # bonds trade in $1,000 lots (round DOWN: stay inside the turnover cap)
            u = 1000.0 * int(u * face / 1000.0) / face
        if u <= 0:
            continue
        amt = r["market_value"] * u
        rate = _sale_tax_rate(r, rates_now, today) if after_tax else 0.0
        proceeds[r["account_type"]] += amt * p_h[r["id"]]
        sells.append({"holding_id": r["id"], "label": r["label"], "kind": r["kind"], "ticker": r.get("ticker"), "cusip": r.get("cusip"),
                      "account_type": r["account_type"], "fraction_pct": round(u * 100, 1), "amount": round(amt, 2),
                      "proceeds": round(amt * p_h[r["id"]], 2), "gain": round((r.get("unrealized_pnl") or 0.0) * u, 2),
                      "tax_now": round(amt * rate, 2), "trading_cost": round(amt * TXN_COST.get(r["kind"], 0.005), 2),
                      "total_yield_pct": r.get("total_yield_pct"), "after_tax_yield_pct": (r.get("tax") or {}).get("after_tax_yield_pct"),
                      "duration": r.get("eff_duration"), "maturity": r.get("maturity"), "is_fund": False})
        h = by_id[r["id"]]
        if u >= 1.0:
            h["status"] = "sold"
        else:
            for fld in ("face_value", "cost_basis"):
                if h.get(fld):
                    h[fld] = h[fld] * (1 - u)
    for f, fid in enumerate(fund_ids):
        r, V = rows[fid], funds0[fid]["value"]
        v = float(x[iv(f)])
        v = V if v > 0.985 * V else (0.0 if v < max(1000.0, 0.03 * V) else v)
        if v <= 0:
            continue
        u = v / V
        rate = _sale_tax_rate(r, rates_now, today) if after_tax else 0.0
        proceeds[r["account_type"]] += v * p_f[fid]
        sells.append({"holding_id": fid, "label": r["label"], "kind": r["kind"], "ticker": r.get("ticker"), "cusip": None,
                      "account_type": r["account_type"], "fraction_pct": round(u * 100, 1), "amount": round(v, 2),
                      "proceeds": round(v * p_f[fid], 2), "gain": round((r.get("unrealized_pnl") or 0.0) * u, 2),
                      "tax_now": round(v * rate, 2), "trading_cost": round(v * TXN_COST.get(r["kind"], 0.0005), 2),
                      "total_yield_pct": r.get("total_yield_pct"), "after_tax_yield_pct": (r.get("tax") or {}).get("after_tax_yield_pct"),
                      "duration": r.get("eff_duration"), "maturity": None, "is_fund": True,
                      "accumulates": (r.get("fund") or {}).get("payout") == "accumulates"})
        h = by_id[fid]
        if u >= 0.999:
            h["status"] = "sold"
        else:
            for fld in ("quantity", "cost_basis"):
                if h.get(fld):
                    h[fld] = h[fld] * (1 - u)
    buys, hid = [], -1
    for a in accounts:
        want = [(ci, float(x[iw(ci)])) for ci, c in enumerate(cands) if c["a"] == a and x[iw(ci)] >= 1000.0]
        # rounding of the sells can leave a little less than planned: the sleeve you asked for is filled first,
        # the bond rungs share what's left
        want.sort(key=lambda cw: cands[cw[0]]["leg"] != "intl")
        intl_w = sum(w for ci, w in want if cands[ci]["leg"] == "intl")
        bond_w = sum(w for ci, w in want if cands[ci]["leg"] != "intl")
        intl_scale = min(1.0, proceeds[a] / intl_w) if intl_w > 0 else 0.0
        scale = min(1.0, max(0.0, proceeds[a] - intl_w * intl_scale) / bond_w) if bond_w > 0 else 0.0
        for ci, w in want:
            c = cands[ci]
            if c["leg"] == "intl":                                   # the non-dollar sleeve: a fund, not a rung
                info, amt = intl_info[c["ticker"]], w * intl_scale
                qty = amt / info["price"]
                new_holdings.append({"id": hid, "kind": "etf", "ticker": c["ticker"], "label": info["name"], "quantity": qty,
                                     "purchase_price": info["price"], "account_type": a, "status": "held", "coupon_freq": 1,
                                     "purchase_date": today.isoformat()})
                hid -= 1
                buys.append({"account_type": a, "year": c["y"], "leg": "intl", "kind": "etf", "label": info["name"], "rating": None,
                             "ticker": c["ticker"], "amount": round(amt, 2), "face": round(qty, 3), "pre_tax_pct": info["yield_pct"],
                             "real_yield_pct": None,
                             "after_tax_pct": ps._pct(c["c"]["pre_tax_yield"] if a in lad.SHELTERED else c["c"]["after_tax_yield"]),
                             "spendable": round(amt * c["c"]["growth"], 2), "security": None, "etf": c["ticker"], "what": info["what"],
                             "non_usd": True})
                continue
            made = gap._holding(hid, c["c"]["kind"], c["c"]["rating"], c["y"], w * scale, a, profile, cat_rows, mi, today)
            if not made or made[1]["cost"] < 1000:
                continue
            hid -= 1
            new_holdings.append(made[0])
            cc = c["c"]
            fam = mkt.DEFINED_MATURITY.get(gap._ETF_FAMILY.get(cc["kind"], ""), {})
            buys.append({"account_type": a, "year": c["y"], "leg": c["leg"], "kind": cc["kind"], "label": cc["label"], "rating": cc["rating"],
                         "amount": round(made[1]["cost"], 2), "face": made[1]["face"], "pre_tax_pct": ps._pct(cc["pre_tax_yield"]),
                         "real_yield_pct": ps._pct(cc["real_yield"]) if cc.get("real_yield") is not None else None,
                         # inside an IRA/Roth the yield compounds untaxed (a traditional IRA's tax comes at withdrawal, on
                         # whatever it holds) → compare like with like: the yield earned IN the account
                         "after_tax_pct": ps._pct(cc["pre_tax_yield"] if a in lad.SHELTERED else cc["after_tax_yield"]),
                         "spendable": round(made[1]["cost"] * cc["growth"], 2),
                         "security": made[1]["security"], "etf": fam.get(c["y"])})
    merged: dict[tuple, dict] = {}
    for l in buys:                         # two spending years can share one bond (no TIPS matures 2037–39) → one line
        key = ((l["account_type"], l["security"]["cusip"]) if l["security"]
               else (l["account_type"], l["kind"], l["rating"], l["year"], l.get("ticker")))
        if key in merged:
            m = merged[key]
            tot = m["amount"] + l["amount"]
            for fld in ("pre_tax_pct", "after_tax_pct", "real_yield_pct"):
                if m.get(fld) is not None and l.get(fld) is not None:
                    m[fld] = round((m[fld] * m["amount"] + l[fld] * l["amount"]) / tot, 3)
            m["amount"], m["face"], m["spendable"] = round(tot, 2), m["face"] + l["face"], round(m["spendable"] + l["spendable"], 2)
            m["years"] = sorted(set(m["years"]) | {l["year"]})
        else:
            merged[key] = {**l, "years": [l["year"]]}
    buys = sorted(merged.values(), key=lambda l: (l["account_type"], l["year"], l["leg"]))
    spent = {a: sum(l["amount"] for l in buys if l["account_type"] == a) for a in accounts}
    cash_left = [{"account_type": a, "amount": round(proceeds[a] - spent[a], 2)} for a in accounts if proceeds[a] - spent[a] > 500]
    for cl in cash_left:                                             # unspent money waits in a 1-year Treasury
        made = gap._holding(hid, "treasury", None, today.year + 1, cl["amount"], cl["account_type"], profile, cat_rows, mi, today)
        if made:
            hid -= 1
            new_holdings.append(made[0])

    # ---- the planner is the judge: before vs after in all four worlds ----
    order = ("low", "base", "high", "debase")
    before_p = {k: await plan(holdings, k, light=True) for k in order}
    after_p = {k: await plan(new_holdings, k, light=True) for k in order} if (sells or buys or cash_left) else before_p
    oc = lambda p, k: gap._outcome(p, scs[k], today, mi)             # noqa: E731
    before = {k: oc(before_p[k], k) for k in order}
    after = {k: oc(after_p[k], k) for k in order}

    ctx_b = await ps.load_context(holdings, profile)
    ctx_a = await ps.load_context(new_holdings, profile)
    rows_b, int_b = ps.analyze_rows(holdings, profile, ctx_b)
    rows_a, int_a = ps.analyze_rows(new_holdings, profile, ctx_a)
    agg_b, agg_a = ps.aggregate(rows_b, int_b), ps.aggregate(rows_a, int_a)
    sum_b, sum_a = agg_b["summary"], agg_a["summary"]
    pick = lambda s_: {k: s_.get(k) for k in ("market_value", "total_yield_pct", "after_tax_yield_pct", "eff_duration",  # noqa: E731
                                              "annual_income_after_tax", "positions")}
    # every fund against the best thing the same account could hold instead (same rate risk: at the fund's duration)
    fund_alts = []
    for fid in fund_ids:
        r = rows[fid]
        a, D = r["account_type"], max(1.0, min(10.0, r.get("eff_duration") or 1.0))
        nom, _ = gap.best_nominal(D, today.year + int(round(D)), a, profile, sched, mi, base_path, today, min_credit=min_credit, after_tax=after_tax)
        inside = a in lad.SHELTERED
        y_f = r.get("total_yield_pct") if inside else (r.get("tax") or {}).get("after_tax_yield_pct")
        y_alt = ps._pct(nom["pre_tax_yield"] if inside else nom["after_tax_yield"]) if nom else None
        fund_alts.append({
            "holding_id": fid, "label": r["label"], "ticker": r.get("ticker"), "account_type": a, "value": round(r["market_value"], 2),
            "yield_pct": y_f, "alt_label": nom["label"] if nom else None, "alt_years": round(D, 1), "alt_yield_pct": y_alt,
            "gap_pct": round(y_alt - y_f, 3) if (y_alt is not None and y_f is not None) else None,
            "duration": r.get("eff_duration"), "expense_ratio_pct": (r.get("fund") or {}).get("expense_ratio_pct"),
            "sold_pct": next((s_["fraction_pct"] for s_ in sells if s_["holding_id"] == fid), 0.0), "kept": fid in keep_ids,
            "basis": "inside the account" if inside else "after tax"})
    fund_alts.sort(key=lambda x: -(x["gap_pct"] if x["gap_pct"] is not None else -99))
    alt_by = {x["holding_id"]: x for x in fund_alts}
    for s_ in sells:
        bb = [l for l in buys if l["account_type"] == s_["account_type"]]
        amt_b = sum(l["amount"] for l in bb) or 1.0
        y_b = sum(l["amount"] * (l["after_tax_pct"] or 0) for l in bb) / amt_b
        tips_b = sum(l["amount"] for l in bb if l["kind"] == "tips") / amt_b
        mat_y = int(s_["maturity"][:4]) if s_.get("maturity") else None
        if s_["is_fund"]:
            fa = alt_by.get(s_["holding_id"]) or {}
            worse = (f"earns {fa['yield_pct']:.2f}% {fa['basis']} vs {fa['alt_yield_pct']:.2f}% in a {fa['alt_label']} of similar maturity; "
                     if (fa.get("gap_pct") or 0) > 0.1 else "")
            s_["reason"] = worse + (f"a fund never matures — its price in the year you spend isn't known (duration {s_['duration'] or 0:.1f}y)"
                                    if (s_["duration"] or 0) >= 0.5 else "cash-like fund — bonds locked to your spending years earn more")
        elif released and lad._is_retirement(s_["account_type"]) and mat_y and mat_y < released:
            s_["reason"] = f"matures {mat_y}, before this account can be tapped ({released}) — the cash would just wait in T-bills"
        elif tips_b > 0.5 and s_["kind"] != "tips":
            s_["reason"] = "fixed dollars → inflation-linked: the spending it pays for rises with CPI"
        elif (s_["after_tax_yield_pct"] or 0) + 0.05 < y_b:
            s_["reason"] = f"earns {s_['after_tax_yield_pct']:.2f}% vs {y_b:.2f}% in what replaces it"
        else:
            s_["reason"] = "moved to the years your plan is short"
    sold_total = sum(s_["amount"] for s_ in sells)
    tax_now = sum(s_["tax_now"] for s_ in sells)
    cost_now = sum(s_["trading_cost"] for s_ in sells)
    # verdict: worth doing only if the planner sees a better plan in the worlds you chose (or equal funding, more left)
    w_un = lambda o: sum(prob[k] * o[k]["unfunded_today_dollars"] for k in use)     # noqa: E731
    gain_funding = w_un(before) - w_un(after)
    end_b = before_p["base"]["years"][-1]["surplus_carried"] + ((before_p["base"].get("funds") or {}).get("remaining_value_end") or 0.0)
    end_a = after_p["base"]["years"][-1]["surplus_carried"] + ((after_p["base"].get("funds") or {}).get("remaining_value_end") or 0.0)
    better = (gain_funding > 500.0) or (abs(gain_funding) <= 500.0 and end_a - end_b > max(500.0, tax_now + cost_now))
    worse_base = after["base"]["unfunded_today_dollars"] > before["base"]["unfunded_today_dollars"] + 1000.0 and "base" in use and len(use) == 1
    verdict = "rebalance" if ((sells or buys) and better and not worse_base) else "hold"

    # ---- why, in plain terms (per account: what leaves vs what replaces it) ----
    why = []
    if verdict == "rebalance":
        for a in accounts:
            ss, bb = [s_ for s_ in sells if s_["account_type"] == a], [l for l in buys if l["account_type"] == a]
            if not bb:
                continue
            intl_b = [l for l in bb if l["leg"] == "intl"]
            bb = [l for l in bb if l["leg"] != "intl"]
            if intl_b:
                why.append(f"In your {'taxable account' if a == 'taxable' else a.upper()}: {gap._money(sum(l['amount'] for l in intl_b))} into non-dollar bond funds "
                           f"({', '.join(l['ticker'] for l in intl_b)}) — a hedge if the dollar weakens, a drag if it strengthens.")
            if not bb:
                continue
            amt_b = sum(l["amount"] for l in bb)
            y_b = sum(l["amount"] * (l["after_tax_pct"] or 0) for l in bb) / amt_b
            tips_share = sum(l["amount"] for l in bb if l["kind"] == "tips") / amt_b
            kinds_b = sorted({l["label"] for l in bb})
            label = "taxable account" if a == "taxable" else f"{a.upper()}"
            basis = "a year after tax" if a not in lad.SHELTERED else "a year inside the account"
            span = f"{bb[0]['years'][0]}–{bb[-1]['years'][-1]}" if bb[0]["years"][0] != bb[-1]["years"][-1] else str(bb[0]["years"][0])
            msg = f"In your {label}: buy {gap._money(amt_b)} of bonds for {span} earning {y_b:.2f}% {basis}"
            if ss:
                amt_s = sum(s_["amount"] for s_ in ss)
                y_s = sum(s_["amount"] * (s_["after_tax_yield_pct"] or 0) for s_ in ss) / amt_s
                funds_sold = [s_ for s_ in ss if s_["is_fund"]]
                msg += f", paid for by selling {gap._money(amt_s)} earning {y_s:.2f}%"
                if funds_sold:
                    msg += f" ({', '.join(sorted({s_['ticker'] or s_['label'] for s_ in funds_sold}))}: funds never mature, so their price in the year you spend isn't known — these bonds pay a known amount that year)"
            if a == new_acct and new_money > 0:
                msg += f"{' and' if ss else ', using'} your {gap._money(new_money)} of new money"
            if tips_share > 0.05:
                msg += f"; {tips_share * 100:.0f}% is TIPS (keeps its buying power), the rest {', '.join(k for k in kinds_b if k != 'TIPS') or '—'} (wins if inflation is low)"
            elif len(kinds_b) > 1:
                msg += f" across {', '.join(kinds_b)}"
            why.append(msg + ".")
        if tax_now > 50:
            why.append(f"Selling realizes {gap._money(sum(s_['gain'] for s_ in sells if s_['gain'] > 0))} of gains → about {gap._money(tax_now)} of tax now (already netted out of what's reinvested).")
        elif tax_now < -50:
            why.append(f"Selling at a loss banks about {gap._money(-tax_now)} of tax savings (netted into what's reinvested).")
        for k in order:
            d = before[k]["unfunded_today_dollars"] - after[k]["unfunded_today_dollars"]
            if abs(d) > 1000:
                why.append(f"{scs[k]['label']}: unfunded spending {'falls' if d > 0 else 'rises'} by {gap._money(abs(d))} "
                           f"({before[k]['funded_ratio_pct']}% → {after[k]['funded_ratio_pct']}% funded).")
    else:
        why.append("Your book already does what the plan needs about as well as any swap would after tax, trading costs and your "
                   "turnover limit — nothing is worth changing" + (" beyond investing the new money." if new_money > 0 else "."))
        if not (new_money > 0):
            sells, buys, cash_left, after, sum_a, rows_a = [], [], [], before, sum_b, rows_b
    return {
        **out_base, "verdict": verdict, "sells": sells, "buys": buys, "cash_left": cash_left, "why": why,
        "totals": {"sold": round(sum(s_["amount"] for s_ in sells), 2), "bought": round(sum(l["amount"] for l in buys), 2),
                   "turnover_pct": round(100 * sum(s_["amount"] for s_ in sells) / M0, 1) if M0 else 0.0,
                   "tax_now": round(sum(s_["tax_now"] for s_ in sells), 2), "trading_cost": round(sum(s_["trading_cost"] for s_ in sells), 2),
                   "positions_sold": len(sells), "lines_bought": len(buys)},
        "by_account": [{"account_type": a, "value": round(sum(r["market_value"] for r in live if r["account_type"] == a), 2),
                        "sold": round(sum(s_["amount"] for s_ in sells if s_["account_type"] == a), 2),
                        "bought": round(sum(l["amount"] for l in buys if l["account_type"] == a), 2),
                        "new_money": new_money if a == new_acct else 0.0} for a in accounts],
        "outcomes": {"before": before, "after": after},
        "book": {"before": pick(sum_b), "after": pick(sum_a)},
        "end_wealth": {"before": round(end_b, 2), "after": round(end_a if verdict == "rebalance" or new_money > 0 else end_b, 2)},
        "resilience": {"before": gap.resilience(rows_b), "after": gap.resilience(rows_a)},
        "considered": {"holdings": nH + nF, "kept_out": len(live) - nH - nF, "candidates": nC},
        "fund_alternatives": fund_alts,
        "roll_at_maturity": [{"holding_id": r["id"], "label": r["label"], "account_type": r["account_type"], "maturity": r.get("maturity"),
                              "value": round(r["market_value"], 2)} for r in sorted(roll, key=lambda r: r.get("maturity") or "")],
        "roll_until": released,
        "allocation": {"before": agg_b["allocation"]["by_kind"], "after": (agg_a if verdict == "rebalance" or new_money > 0 else agg_b)["allocation"]["by_kind"]},
        "limits": {"tips_max_pct": round(tips_max * 100), "type_max_pct": round(type_max * 100), "intl_pct": round(intl_pct * 100, 1),
                   "bond_turnover_pct": round(bond_share * 100),
                   "intl_target": round(intl_target, 2), "intl_funds": list(intl_info.values())},
        "keep_ids": sorted(keep_ids), "kept": [{"holding_id": i, "label": rows[i]["label"]} for i in sorted(keep_ids) if i in rows],
        "notes": [
            *(["Non-dollar bond funds (unhedged): their dollar value moves with exchange rates — modelled as gaining what the dollar loses "
               "beyond your expected inflation, and losing if the dollar strengthens. Their yields are the funds' trailing distributions."]
              if intl_target >= 1000 else []),
            f"Optimized for: {', '.join(scs[k]['label'] for k in use)}. Then checked by the planner in all four inflation worlds — those are the numbers shown.",
            "Money never moves between accounts: a sale in your IRA buys bonds in your IRA (no tax); a taxable sale pays capital-gains tax "
            "first (or banks the loss) and the rest is reinvested.",
            f"Funds are the first thing considered for sale (cheap to trade, never mature); an individual bond is replaced only if the swap "
            f"beats its tax, its trading cost (Treasuries ~0.1%, munis/corporates ~1%) and a {BOND_STICKY * sticky * 100:.1f}% 'leave it alone' "
            f"charge. At most {turnover_max * 100:.0f}% of the book can change, and individual bonds only "
            f"{bond_share * turnover_max * 100:.1f}% of it.",
            f"What's bought is kept balanced: at most {tips_max * 100:.0f}% inflation-linked and {type_max * 100:.0f}% in any one of CDs, agencies, "
            "munis or corporates — so it works if inflation is high AND if it is low or negative. Everything is valued in today's purchasing power.",
            "Corporates and munis: the defined-maturity ETF for that year (shown where one exists) spreads the money over hundreds of issuers.",
            "Bonds in a retirement account that mature before you can tap it aren't sold: roll each one, when it matures, into a bond "
            "maturing in the years that account's money is needed (the plan assumes a Treasury to the year the account opens).",
            "Kept out of the trade list: bank CDs (can't be sold), anything maturing after your last goal year, and holdings you mark 'keep'.",
            "Yields for CDs, agencies, munis and corporates are curve estimates — check live quotes; wash-sale rules and lot selection "
            "aren't modelled.",
        ],
    }
