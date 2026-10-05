"""Income beyond the bond book: Social Security (you + spouse) and other sources (pension, annuity, rental…).

Social Security follows the SSA rules that matter for planning:

* **Full retirement age (FRA)** by birth year (66 → 67 for 1955–1960+).
* **Claiming age**: before FRA the benefit is cut 5/9 of 1% per month for the first 36 months and 5/12 of 1%
  for each month beyond; after FRA it earns delayed-retirement credits of 2/3 of 1% per month up to 70
  (62 → 70% of the FRA benefit, 70 → 124%, for FRA 67).
* **Spouse**: on top of their own benefit, a spouse gets the *excess* of 50% of the worker's FRA benefit over
  their own FRA benefit — once BOTH have claimed — reduced 25/36 of 1% per month (first 36) + 5/12 of 1%
  beyond if it starts before the spouse's FRA; no delayed credits on the spousal part.
* **Survivor**: when one dies the survivor keeps the larger of the two benefits (the deceased's actual benefit,
  floored at 82.5% of their FRA benefit), reduced only if the survivor is under FRA.
* **COLA**: amounts are entered in today's dollars (as on the SSA statement) and grow with your inflation path.
* **Federal tax**: 0 / up to 50% / up to 85% of benefits are taxable depending on "provisional income"
  (other income + tax-exempt interest + half of Social Security) vs fixed thresholds ($25k/$34k single,
  $32k/$44k joint — not indexed), taxed at your rate that year. Most states (incl. CA) don't tax it.

Not modelled: the earnings test (working before FRA while claiming), WEP/GPO, family maximum, divorced-spouse
and child benefits. Everything here is deterministic arithmetic on the user's own inputs.
"""
from __future__ import annotations

from datetime import date

from . import bond_math as bm

MIN_CLAIM, MAX_CLAIM = 62 * 12, 70 * 12
SS_THRESHOLDS = {"single": (25000.0, 34000.0), "joint": (32000.0, 44000.0)}


def fra_months(birth_year: int) -> int:
    """Full retirement age in months."""
    if birth_year <= 1937:
        return 65 * 12
    if birth_year <= 1942:
        return 65 * 12 + 2 * (birth_year - 1937)
    if birth_year <= 1954:
        return 66 * 12
    if birth_year <= 1959:
        return 66 * 12 + 2 * (birth_year - 1954)
    return 67 * 12


def retirement_factor(fra: int, claim: int) -> float:
    """Own benefit at ``claim`` (age in months) as a multiple of the FRA benefit (PIA)."""
    claim = max(MIN_CLAIM, min(MAX_CLAIM, claim))
    if claim < fra:
        early = fra - claim
        return 1 - (min(early, 36) * 5 / 9 + max(0, early - 36) * 5 / 12) / 100
    return 1 + (claim - fra) * (2 / 3) / 100


def spousal_factor(fra: int, start: int) -> float:
    """Reduction on the spousal part if it starts before the spouse's own FRA (no credits for waiting)."""
    if start >= fra:
        return 1.0
    early = fra - start
    return max(0.0, 1 - (min(early, 36) * 25 / 36 + max(0, early - 36) * 5 / 12) / 100)


def survivor_factor(fra: int, age: int) -> float:
    """Widow(er)'s benefit: full at FRA, down to 71.5% at 60 (linear), nothing before 60."""
    if age >= fra:
        return 1.0
    if age < 60 * 12:
        return 0.0
    return 1 - 0.285 * (fra - age) / (fra - 60 * 12)


def taxable_social_security(ss: float, other_income: float, filing: str = "single") -> float:
    """Federally taxable part of a year's benefits (IRS Pub. 915 worksheet). ``other_income`` = everything else
    in provisional income: taxable income + tax-exempt interest."""
    if ss <= 0:
        return 0.0
    b1, b2 = SS_THRESHOLDS["joint" if filing == "joint" else "single"]
    prov = other_income + 0.5 * ss
    if prov <= b1:
        return 0.0
    if prov <= b2:
        return min(0.5 * ss, 0.5 * (prov - b1))
    return min(0.85 * ss, 0.85 * (prov - b2) + min(0.5 * ss, 0.5 * (b2 - b1)))


def _f(x, default=None):
    try:
        return float(x) if x not in (None, "") else default
    except (TypeError, ValueError):
        return default


def parse_person(d: dict | None, fallback_birth_year: int | None = None) -> dict | None:
    """Normalise one person's Social Security inputs; None if there's no birth year."""
    d = d or {}
    by = _f(d.get("birth_year"), fallback_birth_year)
    if not by or by < 1900:
        return None
    by = int(by)
    monthly = max(0.0, _f(d.get("monthly_benefit"), 0.0))
    claim = int(round(max(62.0, min(70.0, _f(d.get("claim_age"), 67.0))) * 12))
    fra = fra_months(by)
    f = retirement_factor(fra, claim)
    basis = d.get("basis") if d.get("basis") in ("fra", "claim") else "fra"
    pia = monthly if basis == "fra" else (monthly / f if f else 0.0)
    return {"birth_year": by, "birth_month": int(max(1, min(12, _f(d.get("birth_month"), 6)))),
            "claim": claim, "fra": fra, "factor": f, "pia": pia, "basis": basis, "monthly_input": monthly,
            "through_age": int(max(62, min(110, _f(d.get("through_age"), 95))))}


def _age_m(p: dict, y: int, m: int) -> int:
    return (y - p["birth_year"]) * 12 + (m - p["birth_month"])


def _ym(p: dict, age_months: int) -> tuple[int, int]:
    """(year, month) when the person reaches ``age_months``."""
    t = p["birth_year"] * 12 + (p["birth_month"] - 1) + age_months
    return t // 12, t % 12 + 1


def social_security_streams(you: dict | None, spouse: dict | None, years: list[int], today: date,
                            infl) -> dict:
    """Month-by-month benefits → nominal $ per calendar year for each person, plus the facts behind them."""
    path = bm.as_path(infl)
    people = {"you": you, "spouse": spouse}
    out = {k: {y: 0.0 for y in years} for k in people}
    real_total = {k: 0.0 for k in people}
    info: dict[str, dict] = {}
    for k, p in people.items():
        if not p:
            continue
        o = people["spouse" if k == "you" else "you"]
        excess, sp_start, sp_factor = 0.0, None, 1.0
        if o:
            excess = max(0.0, 0.5 * o["pia"] - p["pia"])
            if excess > 0:
                # the spousal part starts when BOTH have claimed
                oy, om = _ym(o, o["claim"])
                sp_start = max(p["claim"], _age_m(p, oy, om))
                sp_factor = spousal_factor(p["fra"], sp_start)
        own = p["pia"] * p["factor"]
        sy, sm = _ym(p, p["claim"])
        info[k] = {
            "birth_year": p["birth_year"], "fra_age": round(p["fra"] / 12, 2),
            "fra_label": f"{p['fra'] // 12}" + (f"y {p['fra'] % 12}m" if p["fra"] % 12 else ""),
            "claim_age": round(p["claim"] / 12, 2), "start": f"{sy}-{sm:02d}", "start_year": sy,
            "pia_monthly": round(p["pia"], 2), "pct_of_fra": round(p["factor"] * 100, 1),
            "own_monthly": round(own, 2), "spousal_monthly": round(excess * sp_factor, 2),
            "spousal_start": (lambda t: f"{t[0]}-{t[1]:02d}")(_ym(p, sp_start)) if sp_start is not None else None,
            "monthly_at_claim": round(own + (excess * sp_factor if sp_start is not None and sp_start <= p["claim"] else 0.0), 2),
            "through_age": p["through_age"], "through_year": p["birth_year"] + p["through_age"],
        }
        p["_own"], p["_excess"], p["_sp_start"], p["_sp_factor"] = own, excess, sp_start, sp_factor
    # run to the last plan-through year so lifetime totals are complete; only plan years go into the streams
    last = max([years[-1]] + [p["birth_year"] + p["through_age"] + 1 for p in people.values() if p])
    in_plan = set(years)
    peak = 1.0
    for y in range(years[0], last + 1):
        peak = max(peak, path.factor(max(0, y - today.year)))
        cola = peak                                   # benefits rise with CPI but are never cut when prices fall
        for m in range(1, 13):
            if (y, m) <= (today.year, today.month):
                continue
            alive = {k: (p is not None and _age_m(p, y, m) < (p["through_age"] + 1) * 12) for k, p in people.items()}
            for k, p in people.items():
                if not p or not alive[k]:
                    continue
                ok = "spouse" if k == "you" else "you"
                o = people[ok]
                age = _age_m(p, y, m)
                amt = p["_own"] if age >= p["claim"] else 0.0
                if o and alive[ok] and p["_sp_start"] is not None and age >= p["_sp_start"]:
                    amt += p["_excess"] * p["_sp_factor"]
                if o and not alive[ok]:
                    # survivor: the larger of own and the deceased's benefit (their actual one, min 82.5% of PIA;
                    # if they died before claiming, what they had earned by then)
                    death_age = (o["through_age"] + 1) * 12
                    dead_ben = o["_own"] if death_age >= o["claim"] else o["pia"] * retirement_factor(o["fra"], max(o["fra"], min(death_age, MAX_CLAIM)))
                    dy, dm = _ym(o, death_age)
                    surv = max(dead_ben, 0.825 * o["pia"]) * survivor_factor(p["fra"], max(_age_m(p, dy, dm), 0))
                    amt = max(amt, surv)
                if y in in_plan:
                    out[k][y] += amt * cola
                real_total[k] += amt
    for k in info:
        info[k]["lifetime_today_dollars"] = round(real_total[k], 2)
    return {"by_person": out, "people": info}


def other_income_streams(sources: list[dict] | None, years: list[int], today: date, infl) -> list[dict]:
    """Pension / annuity / rental / work: {name, amount (per year), start_year, end_year, cola, taxable_pct}."""
    path = bm.as_path(infl)
    out = []
    for i, s in enumerate(sources or []):
        amt = _f(s.get("amount"), 0.0) or 0.0
        if amt <= 0:
            continue
        start = int(_f(s.get("start_year"), today.year) or today.year)
        end = int(_f(s.get("end_year"), years[-1]) or years[-1])
        cola = bool(s.get("cola", True))
        tax_pct = max(0.0, min(100.0, _f(s.get("taxable_pct"), 100.0)))
        first_frac = max(0.0, (date(today.year, 12, 31) - today).days / 365.25)
        by_year = {}
        for y in years:
            if y < start or y > end:
                continue
            frac = first_frac if y == today.year else 1.0
            by_year[y] = amt * frac * (path.factor(max(0, y - today.year)) if cola else 1.0)
        out.append({"name": s.get("name") or f"Income {i + 1}", "amount": amt, "start_year": start, "end_year": end,
                    "cola": cola, "taxable_pct": tax_pct, "by_year": by_year})
    return out


def household_income(settings: dict, years: list[int], today: date, infl, *, tax_at, after_tax: bool,
                     other_taxable_income: dict[int, float] | None = None, filing_status: str | None = None,
                     claim_age_override: float | None = None) -> dict:
    """Every non-portfolio income, per year: gross, tax and net.

    ``tax_at(year)`` → that year's ``TaxRates``; ``other_taxable_income`` = the portfolio's income that counts
    toward provisional income (taxable + tax-exempt interest, IRA withdrawals).
    """
    ss_cfg = (settings or {}).get("social_security") or {}
    you_cfg = dict(ss_cfg.get("you") or {})
    if claim_age_override is not None:
        you_cfg["claim_age"] = claim_age_override
        if you_cfg.get("basis") == "claim":            # keep the same earnings record when comparing ages
            base = parse_person(ss_cfg.get("you"), (settings or {}).get("birth_year"))
            if base:
                you_cfg["monthly_benefit"], you_cfg["basis"] = base["pia"], "fra"
    you = parse_person(you_cfg, (settings or {}).get("birth_year")) if (you_cfg.get("monthly_benefit") or ss_cfg.get("spouse")) else None
    if you and not you["pia"] and not ss_cfg.get("spouse"):
        you = None
    spouse = parse_person(ss_cfg.get("spouse")) if ss_cfg.get("spouse") else None
    ss = social_security_streams(you, spouse, years, today, infl) if (you or spouse) else {"by_person": {"you": {}, "spouse": {}}, "people": {}}
    sources = other_income_streams((settings or {}).get("income_sources"), years, today, infl)
    filing = "joint" if (spouse or str(filing_status or "").lower() in ("mfj", "joint", "married")) else "single"
    override = _f(ss_cfg.get("taxable_pct"))
    state_taxed = bool(ss_cfg.get("state_taxed"))
    prov = other_taxable_income or {}
    rows = []
    for y in years:
        ss_you = ss["by_person"]["you"].get(y, 0.0)
        ss_sp = ss["by_person"]["spouse"].get(y, 0.0)
        ss_y = ss_you + ss_sp
        other = sum(s["by_year"].get(y, 0.0) for s in sources)
        other_taxable = sum(s["by_year"].get(y, 0.0) * s["taxable_pct"] / 100 for s in sources)
        r = tax_at(y)
        if override is not None:
            taxable_ss = ss_y * max(0.0, min(85.0, override)) / 100
        else:
            taxable_ss = taxable_social_security(ss_y, prov.get(y, 0.0) + other_taxable, filing)
        tax = 0.0
        if after_tax:
            tax = taxable_ss * (r.fed + (r.state if state_taxed else 0.0)) + other_taxable * (r.fed + r.state)
        rows.append({"year": y, "social_security": round(ss_y, 2), "ss_you": round(ss_you, 2), "ss_spouse": round(ss_sp, 2),
                     "other": round(other, 2), "gross": round(ss_y + other, 2), "tax": round(tax, 2),
                     "net": round(ss_y + other - tax, 2),
                     "ss_taxable_pct": round(100 * taxable_ss / ss_y, 1) if ss_y > 0 else None})
    return {
        "by_year": rows, "net": {r["year"]: r["net"] for r in rows}, "tax": {r["year"]: r["tax"] for r in rows},
        "ss": {r["year"]: r["social_security"] for r in rows},
        "people": ss["people"], "filing": filing, "ss_taxable_override": override, "state_taxed": state_taxed,
        "sources": [{k: v for k, v in s.items() if k != "by_year"} | {"total": round(sum(s["by_year"].values()), 2)} for s in sources],
        "configured": bool(ss["people"]) or bool(sources),
    }
