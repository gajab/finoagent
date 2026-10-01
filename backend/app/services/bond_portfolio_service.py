"""Bond Desk portfolio engine — marks, analytics, risk, cash flows, tax, recommendations.

Input is a list of plain holding dicts (the ``BondHolding`` columns) plus the user's
``BondProfile`` dict; output is one JSON-ready payload. All math goes through
``bond_math`` (one pricer per bond) and all market inputs through
``bond_market_service`` — nothing here invents a number.

**Mark hierarchy** (surfaced per holding as ``price_source``):
1. the user's manual mark;
2. TreasuryDirect FedInvest end-of-day price (Treasuries/TIPS by CUSIP);
3. an *estimated* mark — today's curve + the bond's own spread at purchase, held
   constant (munis: the muni/Treasury ratio at purchase is held instead);
4. an *estimated* generic mark — today's curve + typical spread for the type/rating.

**Reconciliation invariants** (tested): portfolio DV01 == Σ position DV01 ==
Σ key-rate DV01; weighted averages use market value; yearly cash-flow totals == Σ
events; after-tax figures come from the same treatment everywhere.
"""

from __future__ import annotations

import asyncio
import math
import re
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta

from . import bond_math as bm
from . import bond_market_service as mkt

INDIVIDUAL = {"treasury", "tips", "muni", "corporate", "agency", "cd"}
FUNDS = {"etf", "mutual_fund"}
TAX_ADVANTAGED = {"ira", "roth", "401k", "403b", "hsa", "529"}
GOVERNMENT = {"treasury", "tips"}
FDIC_LIMIT = 250_000.0
SHOCKS_BP = [-300, -200, -100, -50, 50, 100, 200, 300]
MATURITY_BUCKETS = [(0, 1, "0–1y"), (1, 3, "1–3y"), (3, 5, "3–5y"), (5, 10, "5–10y"), (10, 20, "10–20y"), (20, 200, "20y+")]
RATING_ORDER = ["GOVT", "AAA", "AA", "A", "BBB", "BB", "B", "CCC", "NR"]


# ---------------------------------------------------------------------------
# Small helpers
# ---------------------------------------------------------------------------
def _date(x) -> date | None:
    if x is None or x == "":
        return None
    if isinstance(x, datetime):
        return x.date()
    if isinstance(x, date):
        return x
    try:
        return date.fromisoformat(str(x)[:10])
    except ValueError:
        return None


def _num(x) -> float | None:
    try:
        if x is None or x == "":
            return None
        v = float(x)
        return v if math.isfinite(v) else None
    except (TypeError, ValueError):
        return None


def _pct(x: float | None, nd: int = 3) -> float | None:
    return None if x is None else round(x * 100.0, nd)


def _r(x: float | None, nd: int = 2) -> float | None:
    return None if x is None else round(x, nd)


def default_profile() -> dict:
    return {"federal_rate": 24.0, "state": None, "state_rate": 5.0, "niit": False, "ltcg_rate": 15.0,
            "filing_status": None, "inflation_assumption": None, "horizon_years": None,
            "goals": [], "settings": {}}


def tax_rates(profile: dict) -> bm.TaxRates:
    p = {**default_profile(), **(profile or {})}
    return bm.TaxRates(fed=(_num(p["federal_rate"]) or 0) / 100, state=(_num(p["state_rate"]) or 0) / 100,
                       niit=0.038 if p.get("niit") else 0.0, ltcg=(_num(p["ltcg_rate"]) or 0) / 100)


@dataclass(frozen=True)
class TaxSchedule:
    """Your marginal rates now, and (optionally) the different rates from the year you retire."""
    now: bm.TaxRates
    retired: bm.TaxRates | None = None
    from_year: int | None = None

    def at(self, year: int) -> bm.TaxRates:
        if self.retired is not None and self.from_year is not None and year >= self.from_year:
            return self.retired
        return self.now


def retirement_year(profile: dict) -> int | None:
    """When the 'after retirement' rates start: your setting, else the first year of a goal named
    'retire…' (e.g. the Retirement income goal)."""
    st = (profile or {}).get("settings") or {}
    try:
        if st.get("retire_year"):
            return int(st["retire_year"])
    except (TypeError, ValueError):
        pass
    years = [int(g["year"]) for g in (profile or {}).get("goals") or []
             if "retire" in str(g.get("name") or "").lower() and str(g.get("year") or "").isdigit()]
    return min(years) if years else None


def tax_schedule(profile: dict) -> TaxSchedule:
    now = tax_rates(profile)
    st = (profile or {}).get("settings") or {}
    fed, state, ltcg = (_num(st.get(k)) for k in ("retired_federal_rate", "retired_state_rate", "retired_ltcg_rate"))
    yr = retirement_year(profile)
    if yr is None or (fed is None and state is None and ltcg is None):
        return TaxSchedule(now)
    return TaxSchedule(now, bm.TaxRates(fed=fed / 100 if fed is not None else now.fed,
                                        state=state / 100 if state is not None else now.state,
                                        niit=now.niit, ltcg=ltcg / 100 if ltcg is not None else now.ltcg), yr)


def as_schedule(rates) -> TaxSchedule:
    return rates if isinstance(rates, TaxSchedule) else TaxSchedule(rates)


def tax_schedule_fields(sched: TaxSchedule) -> dict | None:
    if sched.retired is None:
        return None
    r = sched.retired
    return {"from_year": sched.from_year, "federal_pct": round(r.fed * 100, 2), "state_pct": round(r.state * 100, 2),
            "ltcg_pct": round(r.ltcg * 100, 2), "niit_pct": round(r.niit * 100, 2)}


def fund_tax_class(fund: dict | None, h: dict) -> str:
    text = " ".join(str(x or "") for x in ((fund or {}).get("category"), (fund or {}).get("name"), h.get("label"))).lower()
    # yfinance abbreviates mutual-fund names ("Vanguard Interm-Term Tx-Ex Adm"), so match the short forms too.
    if any(k in text for k in ("muni", "municipal", "tax-exempt", "tax exempt", "tx-ex", "tx ex", "tax-free", "tax free")):
        return "muni"
    # NOT "government": GNMA/agency funds are state-taxable — only Treasury/TIPS funds get the state exemption.
    if any(k in text for k in ("treasury", "treas", "tsy", "tips", "inflation-protected", "infl-prot", "t-bill")):
        return "treasury"
    return "taxable"


def tax_treatment(h: dict, profile: dict, fund: dict | None = None) -> tuple[bm.TaxTreatment, list[str]]:
    """Default US treatment by kind, overridable per holding (``federal_taxable`` / ``state_taxable``)."""
    kind = h.get("kind")
    notes: list[str] = []
    acct = (h.get("account_type") or "taxable").lower()
    fed, st = True, True
    if kind in GOVERNMENT:
        st = False
    elif kind == "muni":
        fed = False
        my, theirs = (profile or {}).get("state"), h.get("state")
        if my and theirs and my.upper() != theirs.upper():
            st = True
            notes.append(f"Out-of-state muni ({theirs}) — taxable by {my}.")
        else:
            st = False
            if not (my and theirs):
                notes.append("Assumed in-state muni (state-tax free) — set your state and the bond's state to confirm.")
    elif kind in FUNDS:
        cls = fund_tax_class(fund, h)
        if cls == "muni":
            fed = False
            notes.append("Muni fund: federal-tax-free; state tax applies except on your own state's share.")
        elif cls == "treasury":
            st = False
            notes.append("Treasury fund: most states exempt the Treasury-interest share.")
    if h.get("federal_taxable") is not None:
        fed = bool(h["federal_taxable"])
    if h.get("state_taxable") is not None:
        st = bool(h["state_taxable"])
    if kind == "muni" and fed:
        notes.append("Federally taxable muni.")
    if h.get("amt") and kind == "muni":
        notes.append("Private-activity muni: interest counts for the AMT.")
    zero = kind in INDIVIDUAL and int(h.get("coupon_freq") or 0) == 0 and kind != "cd"
    if acct in TAX_ADVANTAGED:
        notes = [f"Held in a tax-advantaged account ({acct.upper()}): no current tax on interest."]
        if kind == "muni" or (kind in FUNDS and fund_tax_class(fund, h) == "muni"):
            notes.append("A muni's tax exemption is wasted here — taxable bonds usually yield more inside an IRA.")
    return (bm.TaxTreatment(fed_taxable=fed, state_taxable=st, taxable_account=acct not in TAX_ADVANTAGED,
                            muni=(kind == "muni" or (kind in FUNDS and fund_tax_class(fund, h) == "muni")),
                            tips=(kind == "tips"), zero_coupon=zero), notes)


def bond_spec(h: dict) -> bm.BondSpec | None:
    mat = _date(h.get("maturity_date"))
    if not mat:
        return None
    freq = int(h.get("coupon_freq") or 0)
    freq = freq if freq in (1, 2, 4, 12) else 0
    cpn = (_num(h.get("coupon_rate")) or 0.0) / 100.0
    calls = []
    cd = _date(h.get("call_date"))
    if cd:
        calls.append((cd, _num(h.get("call_price")) or 100.0))
    return bm.BondSpec(maturity=mat, coupon=cpn if freq else 0.0, freq=freq,
                       day_count=h.get("day_count") or bm.default_day_count(h.get("kind") or ""),
                       issue=_date(h.get("issue_date")), calls=calls)


def maturity_bucket(years: float | None) -> str:
    if years is None:
        return "n/a"
    for lo, hi, lbl in MATURITY_BUCKETS:
        if lo <= years < hi:
            return lbl
    return "20y+"


def _rating_group(h: dict) -> str:
    if h.get("kind") in GOVERNMENT or h.get("kind") == "cd":
        return "GOVT"  # CDs: FDIC-insured up to the limit (flagged separately)
    return mkt.rating_bucket(h.get("rating")) or "NR"


_FUND_RATING_MAP = {"us_government": "GOVT", "aaa": "AAA", "aa": "AA", "a": "A", "bbb": "BBB", "bb": "BB",
                    "b": "B", "below_b": "CCC", "other": "NR"}


# ---------------------------------------------------------------------------
# Market context bundle
# ---------------------------------------------------------------------------
@dataclass
class Ctx:
    settle: date
    mi: dict
    catalogue: dict[str, dict]
    funds: dict[str, dict | None]
    purchase_curves: dict[tuple[str, date], list]
    cpi: dict
    inflation: bm.InflationPath
    rate_context: dict
    catalogue_as_of: str | None = None
    etf_maturity: dict[str, int] = field(default_factory=dict)
    reference: dict[str, dict] = field(default_factory=dict)   # FiscalData terms by CUSIP (no FedInvest needed)


def inflation_assumption(profile: dict, rate_ctx: dict) -> tuple[bm.InflationPath, str]:
    """Expected inflation as a PATH: next 3 years (profile ``inflation_assumption``, else the market 5y
    breakeven) then a long-run average (``settings.inflation_long``, else the market 5y5y forward implied
    by the 5y and 10y breakevens)."""
    prof = profile or {}
    s_user = _num(prof.get("inflation_assumption"))
    l_user = _num((prof.get("settings") or {}).get("inflation_long"))
    rc = rate_ctx or {}
    be5 = (rc.get("be_5y") or {}).get("value_pct")
    be10 = (rc.get("be_10y") or {}).get("value_pct")
    m_short = be5 / 100.0 if be5 is not None else None
    if be5 is not None and be10 is not None:
        m_long, m_long_src = ((1 + be10 / 100) ** 10 / (1 + be5 / 100) ** 5) ** 0.2 - 1, "market 5y5y forward"
    elif be10 is not None:
        m_long, m_long_src = be10 / 100.0, "market 10y breakeven"
    else:
        m_long, m_long_src = m_short, "market 5y breakeven"
    short = s_user / 100.0 if s_user is not None else (m_short if m_short is not None else 0.025)
    long = l_user / 100.0 if l_user is not None else (m_long if m_long is not None else short)
    src_s = "yours" if s_user is not None else ("market 5y breakeven" if m_short is not None else "default")
    src_l = "yours" if l_user is not None else (m_long_src if m_long is not None else "same as short-term")
    path = bm.InflationPath(short, long)
    if abs(short - long) < 5e-5 and src_s == src_l:
        return path, f"{short * 100:.2f}% ({src_s})"
    return path, f"{short * 100:.2f}% next 3y ({src_s}), then {long * 100:.2f}% ({src_l})"


def inflation_fields(path, source: str | None = None) -> dict:
    """The inflation path as it appears in every API payload."""
    path = bm.as_path(path)
    out = {"inflation_pct": round(path.short * 100, 3), "inflation_long_pct": round(path.long * 100, 3),
           "inflation_10y_avg_pct": round(path.avg(10.0) * 100, 3)}
    if source is not None:
        out["inflation_source"] = source
    return out


async def load_context(holdings: list[dict], profile: dict, settle: date | None = None) -> Ctx:
    settle = settle or mkt.us_today()
    mi, rctx, cat = await asyncio.gather(mkt.market_inputs(), mkt.rate_context(), mkt.treasury_catalogue())
    tickers = sorted({(h.get("ticker") or "").upper() for h in holdings if h.get("kind") in FUNDS and h.get("ticker")})
    fund_res = await asyncio.gather(*(mkt.fund_profile_full(t) for t in tickers)) if tickers else []
    funds = dict(zip(tickers, fund_res))
    catalogue = {r["cusip"]: r for r in (cat or {}).get("rows", [])}
    # purchase-date curves only for bonds that need an estimated mark
    need: set[tuple[str, date]] = set()
    for h in holdings:
        if h.get("kind") not in INDIVIDUAL or _num(h.get("current_price")) is not None:
            continue
        if (h.get("cusip") or "").upper() in catalogue:
            continue
        pd_ = _date(h.get("purchase_date"))
        if pd_ and (_num(h.get("purchase_price")) or _num(h.get("cost_basis"))) and pd_ < settle:
            need.add(("real" if h.get("kind") == "tips" else "nominal", pd_))
    need_l = sorted(need)
    curves = await asyncio.gather(*(mkt.curve_on(d, k) for k, d in need_l)) if need_l else []
    purchase_curves = {key: (c or {}).get("points") or [] for key, c in zip(need_l, curves)}
    cpi = await mkt.cpi_monthly() if any(h.get("kind") == "tips" for h in holdings) else {}
    # Treasury/TIPS terms (reference CPI, dated date) must not depend on FedInvest being up.
    need_ref = any(h.get("kind") in GOVERNMENT and h.get("cusip") and h["cusip"].upper() not in catalogue for h in holdings)
    reference = (await mkt.treasury_reference() or {}) if need_ref else {}
    infl, _ = inflation_assumption(profile, rctx)
    etf_maturity = {t: y for fam in mkt.DEFINED_MATURITY.values() for y, t in fam.items()}
    return Ctx(settle=settle, mi=mi, catalogue=catalogue, funds=funds, purchase_curves=purchase_curves,
               cpi=cpi, inflation=infl, rate_context=rctx or {},
               catalogue_as_of=(cat or {}).get("as_of"), etf_maturity=etf_maturity, reference=reference)


# ---------------------------------------------------------------------------
# Per-holding analytics
# ---------------------------------------------------------------------------
@dataclass
class _Internal:
    spec: bm.BondSpec | None = None
    ytw: float | None = None
    workout: bm.Workout | None = None
    face: float = 0.0
    ir: float = 1.0                  # TIPS index ratio (1 for nominal)
    bank_cd_value_at_maturity: float | None = None
    fund_yield: float | None = None       # CASH yield a fund pays out (0 for an accumulating fund)
    fund_accrual: float = 0.0             # yield that builds up in the price instead (NAV accrual / pull-to-par)
    fund_duration: float | None = None
    t_int: float = 0.0
    krd_share: dict = field(default_factory=dict)
    # tax facts for splitting principal into cost-returned + gain and taxing it (cash-flow projection)
    tr: bm.TaxTreatment | None = None
    rates: bm.TaxRates | None = None
    cost: float | None = None             # $ cost basis of the whole position (None = unknown)
    pp: float | None = None               # clean price paid per 100 face (REAL price for TIPS)
    pd: date | None = None                # purchase date
    ir_buy: float | None = None           # TIPS index ratio on the purchase date


def _base_row(h: dict) -> dict:
    mat, pd_ = _date(h.get("maturity_date")), _date(h.get("purchase_date"))
    return {
        "id": h.get("id"), "kind": h.get("kind"), "kind_label": mkt.KIND_LABELS.get(h.get("kind"), h.get("kind")),
        "label": h.get("label") or h.get("issuer") or h.get("ticker") or h.get("cusip") or "Bond",
        "issuer": h.get("issuer"), "cusip": h.get("cusip"), "ticker": h.get("ticker"),
        "status": h.get("status") or "held", "account_type": (h.get("account_type") or "taxable").lower(),
        "account_name": h.get("account_name"), "ladder_id": h.get("ladder_id"),
        "rating": h.get("rating"), "rating_group": _rating_group(h), "state": h.get("state"),
        "face": _num(h.get("face_value")), "quantity": _num(h.get("quantity")),
        "coupon_pct": _num(h.get("coupon_rate")), "coupon_freq": int(h.get("coupon_freq") or 0),
        "maturity": mat.isoformat() if mat else None,
        "purchase_date": pd_.isoformat() if pd_ else None,
        "purchase_price": _num(h.get("purchase_price")),
        "warnings": [],
    }


def _base_yield(kind: str, points: list, t: float, mi: dict) -> float | None:
    """The reference curve a bond's spread is measured against: Treasury (real curve for
    TIPS); munis use Treasury × today's muni/Treasury ratio so the spread is vs the MUNI
    curve (holding a raw ratio explodes after a big Treasury move)."""
    b = bm.interp(points, t)
    if b is None:
        return None
    if kind == "muni":
        return b * (bm.interp((mi.get("muni_ratio") or {}).get("points") or [], t) or 0.7)
    return b


def _estimated_yield(h: dict, spec: bm.BondSpec, ctx: Ctx) -> tuple[float | None, str]:
    """Today's model yield for a bond without a quote: hold its spread at purchase over the
    reference curve, measured to the bond's WORKOUT date (a premium callable yields to its call)."""
    kind = h.get("kind")
    curve_kind = "real" if kind == "tips" else "nominal"
    today_pts = (ctx.mi.get(curve_kind) or {}).get("points") or []
    pd_, pp = _date(h.get("purchase_date")), _num(h.get("purchase_price"))
    if pd_ and pp and pd_ < ctx.settle and (curve_kind, pd_) in ctx.purchase_curves:
        c0 = ctx.purchase_curves[(curve_kind, pd_)]
        try:
            a0 = bm.analytics(spec, pd_, clean=pp)
        except Exception:  # noqa: BLE001
            a0 = None
        if a0 and a0.get("ytw") is not None:
            wk_date = a0["ytw_date"] if a0["ytw_date"] > ctx.settle else spec.maturity
            base0 = _base_yield(kind, c0, bm.year_frac(pd_, wk_date), ctx.mi)
            base = _base_yield(kind, today_pts, bm.year_frac(ctx.settle, wk_date), ctx.mi)
            if base0 is not None and base is not None:
                spread = a0["ytw"] - base0
                ref = "muni curve" if kind == "muni" else ("real curve" if kind == "tips" else "Treasury curve")
                return base + spread, f"estimated — today's {ref} + your purchase spread ({spread * 1e4:+.0f}bp)"
    t = bm.year_frac(ctx.settle, spec.maturity)
    y, basis = mkt.model_yield(kind, t, ctx.mi, h.get("rating"))
    return y, f"estimated — {basis}"


def _tax_block(tr: bm.TaxTreatment, t_int: float, ate: float | None, rates: bm.TaxRates, notes: list[str],
               after_tax_real: float | None = None) -> dict:
    return {"fed_taxable": tr.fed_taxable, "state_taxable": tr.state_taxable, "taxable_account": tr.taxable_account,
            "rate_pct": _pct(t_int, 2), "after_tax_yield_pct": _pct(ate),
            "tey_pct": _pct(bm.tax_equivalent_yield(ate, rates)) if ate is not None else None,
            "after_tax_real_pct": _pct(after_tax_real), "notes": notes}


# ---------------------------------------------------------------------------
# Bond funds: what they really yield and how they pay it
# ---------------------------------------------------------------------------
# For a fund, ``coupon_freq`` is how it pays — 0 = accumulates (no cash until you sell / it matures),
# 12 = distributes, 1 = auto-detect — and ``coupon_rate`` is YOUR yield (%). Rows saved before funds had
# these settings carry the bond default (2): auto-detect, and any stray coupon value is ignored.
FUND_ACCUMULATES, FUND_AUTO, FUND_DISTRIBUTES = 0, 1, 12
_FUND_SETTINGS = (FUND_ACCUMULATES, FUND_AUTO, FUND_DISTRIBUTES)
# Typical CLO tranche spreads over T-bills (CLOs are floating rate) — an estimate; the fund's SEC yield is better.
_CLO_SPREAD = {"AAA": 0.0125, "AA": 0.0165, "A": 0.0195, "BBB": 0.0290, "BB": 0.0560}
_BILL_HINTS = ("t-bill", "treasury bill", "0-3 month", "0-1 year treasury", "ultra short treasury",
               "floating rate treasury", "treasury floating")


def _fund_text(h: dict, fp: dict | None) -> str:
    return " ".join(str(x or "") for x in ((fp or {}).get("name"), (fp or {}).get("category"), h.get("label"))).lower()


def _is_box(text: str) -> bool:
    return bool(re.search(r"\bbox\b", text))


def _fund_setting(h: dict) -> int | None:
    try:
        f = int(h.get("coupon_freq")) if h.get("coupon_freq") is not None else None
    except (TypeError, ValueError):
        return None
    return f if f in _FUND_SETTINGS else None


def fund_user_yield(h: dict) -> float | None:
    """Your yield (%) for a fund — only when saved with the fund settings (legacy coupon values ignored)."""
    return _num(h.get("coupon_rate")) if _fund_setting(h) is not None else None


def fund_payout(h: dict, fp: dict | None) -> tuple[str, str]:
    """("distributes" | "accumulates", where that came from). Box-spread ETFs (BOXX…) never distribute —
    the return builds up in the share price and is a capital gain when you sell."""
    f = _fund_setting(h)
    if f == FUND_ACCUMULATES:
        return "accumulates", "your setting"
    if f == FUND_DISTRIBUTES:
        return "distributes", "your setting"
    if _is_box(_fund_text(h, fp)):
        return "accumulates", "auto — box-spread ETFs pay no distributions; the return builds up in the price"
    return "distributes", "auto"


def fund_yield_estimate(h: dict, fp: dict | None, ctx: Ctx) -> dict | None:
    """Estimated yield to maturity of what a fund holds, NET of its expense ratio — what it earns if rates
    don't move. The trailing distribution yield lags it after rates move, and box / T-bill ETFs that
    accumulate show ~0%. Built from today's curves at the fund's MEASURED duration:

    * box-spread & T-bill funds → 3-month T-bill;  CLO funds → T-bill + typical tranche spread (floating rate)
    * floating-rate loan funds → T-bill + their credit mix's spread
    * TIPS funds → real yield + expected inflation;  muni funds → Treasury × muni/Treasury ratio
    * everything else → Treasury + its credit mix's OAS (government share at the Treasury rate)

    Not estimated (→ distribution yield): closed-end funds (leverage + discounts), emerging-market /
    global / foreign-currency funds (their yields don't live on the US curve).
    """
    if not fp:
        return None
    text = _fund_text(h, fp)
    if (fp.get("quote_type") or "").upper() not in ("ETF", "MUTUALFUND", ""):
        return None                                   # closed-end fund: leverage + discount → not modelled
    if any(k in text for k in ("emerging", "global", "international", "world", "local currency", "foreign", "ex-us", "ex-u.s")):
        return None
    npts = (ctx.mi.get("nominal") or {}).get("points") or []
    bill = bm.interp(npts, 0.25)
    if bill is None:
        return None
    er = (fp.get("expense_ratio_pct") or 0.0) / 100.0
    less = f" − {er * 100:.2f}% expenses" if er else ""
    mix = fp.get("credit_mix") or {}
    if _is_box(text) or any(k in text for k in _BILL_HINTS):
        return {"yield": bill - er, "inflation": 0.0,
                "basis": f"3-month T-bill {bill * 100:.2f}%{less} (earns short-term Treasury rates)"}
    if re.search(r"\bclo\b", text):
        if "aaa" in text:
            rb = "AAA"
        else:
            graded = [(_FUND_RATING_MAP.get(k), v) for k, v in mix.items() if _FUND_RATING_MAP.get(k) in _CLO_SPREAD]
            rb = max(graded, key=lambda kv: kv[1])[0] if graded else "AA"
        sp = _CLO_SPREAD[rb]
        return {"yield": bill + sp - er, "inflation": 0.0,
                "basis": f"floating rate: 3-month T-bill {bill * 100:.2f}% + typical {rb} CLO spread ~{sp * 1e4:.0f}bp{less} "
                         "— enter the fund's SEC yield to be exact"}
    dur = fp.get("duration")
    floating = any(k in text for k in ("floating", "float rate", "bank loan", "senior loan", "leveraged loan"))
    if (not dur or dur <= 0) and not floating:
        return None
    D = max(0.25, float(dur or 0.0))
    if any(k in text for k in ("tips", "inflation")):
        tpts = sorted(ctx.mi.get("tips_points") or [])
        r = bm.interp(tpts, D) if tpts else bm.interp((ctx.mi.get("real") or {}).get("points") or [], D)
        if r is None:
            return None
        pi = bm.as_path(ctx.inflation).avg(D)
        return {"yield": (1 + r) * (1 + pi) - 1 - er, "inflation": pi,
                "basis": f"real yield {r * 100:.2f}% at its {D:.1f}y duration + {pi * 100:.2f}% expected inflation{less}"}
    tsy = bm.interp(npts, D)
    if tsy is None:
        return None
    if fund_tax_class(fp, h) == "muni":                # handles yfinance abbreviations ("Interm-Term Tx-Ex Adm")
        ratio = bm.interp((ctx.mi.get("muni_ratio") or {}).get("points") or [], D) or 0.7
        return {"yield": tsy * ratio - er, "inflation": 0.0,
                "basis": f"Treasury {tsy * 100:.2f}% × muni ratio {ratio:.2f} at its {D:.1f}y duration{less} (tax-exempt)"}
    base = bill if floating else tsy
    govt_fund = (any(k in text for k in ("treasury", "government", "govt", "gov't"))
                 and not any(k in text for k in ("corporate", "credit", "mortgage")))
    sp = 0.0
    gov = min(mix.get("us_government") or 0.0, 1.0)
    rest = {k: v for k, v in mix.items() if k != "us_government" and v}
    tot = sum(rest.values())
    spreads = ctx.mi.get("spreads")
    if tot and spreads and not govt_fund:
        scale = (1 - gov) / tot
        for k, v in rest.items():
            rb = _FUND_RATING_MAP.get(k, "NR")
            # floaters reset to the bill rate but carry the credit spread of ~3–5y loans
            sp += v * scale * (mkt.spread_for(spreads, "BBB" if rb == "NR" else rb, 4.0 if floating else max(D, 1.0)) or 0.0)
    where = "3-month T-bill (floating rate)" if floating else f"Treasury at its {D:.1f}y duration"
    return {"yield": base + sp - er, "inflation": 0.0,
            "basis": f"{where} {base * 100:.2f}%" + (f" + credit-mix spread {sp * 1e4:.0f}bp" if sp else "") + less}


def yield_parts(total: float | None, income: float | None, inflation: float = 0.0) -> dict | None:
    """Split a NOMINAL total yield into cash income + price gain to maturity (pull-to-par / NAV accrual;
    negative = premium you'll lose) + inflation accretion (TIPS). The parts always sum to the total."""
    if total is None:
        return None
    inc = income or 0.0
    return {"total_pct": _pct(total), "income_pct": _pct(inc), "price_gain_pct": _pct(total - inc - inflation),
            "inflation_pct": _pct(inflation)}


def analyze_holding(h: dict, profile: dict, rates: bm.TaxRates, ctx: Ctx) -> tuple[dict, _Internal]:
    row = _base_row(h)
    it = _Internal()
    kind = h.get("kind")
    settle = ctx.settle

    # ---------------- funds ----------------
    if kind in FUNDS:
        t = (h.get("ticker") or "").upper()
        fp = ctx.funds.get(t)
        qty = _num(h.get("quantity")) or 0.0
        price = _num(h.get("current_price")) or (fp or {}).get("price")
        if not fp:
            row["warnings"].append(f"No market data for {t} — add a manual price.")
        mv = qty * price if price else 0.0
        dist = ((fp or {}).get("distribution_yield_pct") or 0.0) / 100.0
        payout, payout_src = fund_payout(h, fp)
        user_y = fund_user_yield(h)
        est = fund_yield_estimate(h, fp, ctx)
        if user_y is not None:
            y, basis, infl_part = user_y / 100.0, "your yield", 0.0
        elif est is not None:
            y, basis, infl_part = est["yield"], f"estimated yield to maturity — {est['basis']}", est["inflation"]
        else:
            y, basis, infl_part = dist, "distribution yield (TTM)", 0.0
        # cash it pays vs what builds up in the price (sold → capital gain)
        if payout == "accumulates":
            cash_y = 0.0
        elif user_y is not None or dist <= 0:
            cash_y = y
        else:
            cash_y = dist
        accrual = y - cash_y
        dur = (fp or {}).get("duration")
        tr, notes = tax_treatment(h, profile, fp)
        t_int = bm.interest_tax_rate(rates, tr)
        t_gain = (rates.ltcg + rates.niit + rates.state) if tr.taxable_account else 0.0
        if payout == "accumulates":
            notes = notes + ["Pays no distributions: the return builds up in the share price and is taxed as a capital "
                             "gain only when you sell (long-term after a year)" + (" — no yearly tax." if t_gain else ".")]
        pp = _num(h.get("purchase_price"))
        cost = _num(h.get("cost_basis")) or (qty * pp if pp else None)
        dm_year = ctx.etf_maturity.get(t)
        years = dur if dur else None
        if dm_year:
            years = max(0.0, bm.year_frac(settle, date(dm_year, 12, 15)))
        it.fund_yield, it.fund_accrual, it.fund_duration, it.t_int = cash_y, accrual, dur, t_int
        it.tr, it.rates, it.cost = tr, rates, cost
        it.krd_share = dict(bm._tenor_weights(dur)) if dur else {}
        ate = y * (1 - (t_gain if payout == "accumulates" else t_int))
        row.update({
            "name": (fp or {}).get("name"), "price": _r(price, 4),
            "price_source": "manual" if _num(h.get("current_price")) else ("market (yfinance)" if fp else "missing"),
            "market_value": round(mv, 2), "cost_basis": _r(cost), "unrealized_pnl": _r(mv - cost) if cost else None,
            "unrealized_pnl_pct": _r((mv / cost - 1) * 100) if cost else None,
            "ytm_pct": _pct(y), "ytw_pct": _pct(y), "total_yield_pct": _pct(y), "yield_basis": basis,
            "yield_parts": yield_parts(y, cash_y, infl_part),
            "current_yield_pct": _pct(cash_y), "eff_duration": _r(dur, 2), "mod_duration": _r(dur, 2),
            "convexity": _r((dur * dur + dur) / 100, 3) if dur else None,
            "dv01": round((dur or 0.0) * mv * bm.BP, 2), "annual_income": round(mv * cash_y, 2),
            "annual_accrual": round(mv * accrual, 2),
            "years_to_maturity": _r(years, 2), "maturity_bucket": maturity_bucket(years),
            "maturity_year": dm_year, "defined_maturity_year": dm_year,
            "fund": {"distribution_yield_pct": _pct(dist) if dist else None, "expense_ratio_pct": (fp or {}).get("expense_ratio_pct"),
                     "est_ytm_pct": _pct(est["yield"]) if est else None, "est_basis": est["basis"] if est else None,
                     "user_yield_pct": user_y, "payout": payout, "payout_source": payout_src,
                     "cash_yield_pct": _pct(cash_y), "accrual_pct": _pct(accrual),
                     "duration_source": (fp or {}).get("duration_source"), "avg_maturity": (fp or {}).get("avg_maturity"),
                     "duration_confidence": (fp or {}).get("duration_confidence"), "duration_r2": (fp or {}).get("duration_r2"),
                     "category": (fp or {}).get("category"), "family": (fp or {}).get("family"),
                     "credit_mix": (fp or {}).get("credit_mix") or {}, "tax_class": fund_tax_class(fp, h)},
            "tax": _tax_block(tr, t_int, ate, rates, notes),
            "krd": {str(k): round(v * (dur or 0), 4) for k, v in it.krd_share.items()},
        })
        if not dur:
            row["warnings"].append("Fund duration unavailable — rate risk understated.")
        return row, it

    # ---------------- individual bonds ----------------
    ref_terms = ctx.reference.get((h.get("cusip") or "").upper()) if kind in GOVERNMENT else None
    if ref_terms and not h.get("issue_date") and ref_terms.get("dated_date"):
        h = {**h, "issue_date": ref_terms["dated_date"]}      # accrual starts at the dated date
    spec = bond_spec(h)
    face = _num(h.get("face_value")) or 0.0
    it.face = face
    if spec is None:
        row["warnings"].append("Missing maturity date.")
        row.update(market_value=0.0, dv01=0.0, annual_income=0.0)
        return row, it
    if spec.maturity <= settle:
        row.update(matured=True, market_value=0.0, dv01=0.0, annual_income=0.0, years_to_maturity=0.0,
                   maturity_bucket="matured", maturity_year=spec.maturity.year)
        row["warnings"].append("Matured — principal should have been paid; mark it matured or reinvest.")
        return row, it
    it.spec = spec
    tr, notes = tax_treatment(h, profile)
    t_int = bm.interest_tax_rate(rates, tr)
    it.t_int = t_int
    years = bm.year_frac(settle, spec.maturity)
    row.update(years_to_maturity=round(years, 2), maturity_bucket=maturity_bucket(years),
               maturity_year=spec.maturity.year)
    nominal_pts = (ctx.mi.get("nominal") or {}).get("points") or []
    tsy_here = bm.interp(nominal_pts, years)

    # ---- cost basis the way brokers report it ----
    # Statements show Quantity (= face/par — for TIPS the ORIGINAL, un-indexed face) and a cost-basis
    # TOTAL. A TIPS total already includes the index ratio on the purchase date, so the real clean
    # price paid is total / (face × IR_at_purchase); for nominal bonds it is simply total / face.
    cu = (h.get("cusip") or "").upper()
    cat = ctx.catalogue.get(cu)
    pd_ = _date(h.get("purchase_date"))
    refc = (_num(h.get("tips_ref_cpi")) or (cat or {}).get("ref_cpi") or (ref_terms or {}).get("ref_cpi")) if kind == "tips" else None
    ir_buy: float | None = 1.0
    if kind == "tips":
        ir_buy = bm.index_ratio(pd_, refc, ctx.cpi) if (refc and pd_) else None
    cb = _num(h.get("cost_basis"))
    pp_derived = False
    if _num(h.get("purchase_price")) is None and cb and face and ir_buy:
        h = {**h, "purchase_price": cb / (face * ir_buy) * 100.0}
        pp_derived = True

    # ---- bank CD: accrues to maturity, no mark-to-market ----
    if kind == "cd" and spec.freq == 0:
        apy = (_num(h.get("coupon_rate")) or 0.0) / 100.0
        start = _date(h.get("issue_date")) or _date(h.get("purchase_date")) or settle
        term = max(0.0, bm.year_frac(start, spec.maturity))
        elapsed = max(0.0, bm.year_frac(start, settle))
        value = face * (1 + apy) ** elapsed
        at_mat = face * (1 + apy) ** term
        it.bank_cd_value_at_maturity = at_mat
        cost = _num(h.get("cost_basis")) or face
        it.tr, it.rates, it.cost = tr, rates, cost
        row.update({
            "price": None, "price_source": "accrual (bank CD — principal + interest)", "market_value": round(value, 2),
            "cost_basis": round(cost, 2), "unrealized_pnl": round(value - cost, 2),
            "ytm_pct": _pct(apy), "ytw_pct": _pct(apy), "total_yield_pct": _pct(apy), "yield_parts": yield_parts(apy, apy),
            "yield_basis": "APY", "current_yield_pct": _pct(apy),
            "book_yield_pct": _pct(apy), "eff_duration": 0.0, "mod_duration": 0.0, "cash_flow_duration": round(years, 2),
            "dv01": 0.0, "annual_income": round(value * apy, 2), "value_at_maturity": round(at_mat, 2),
            "spread_bp": round((apy - tsy_here) * 1e4) if tsy_here is not None else None,
            "tax": _tax_block(tr, t_int, apy * (1 - t_int), rates,
                              notes + ["CD interest is taxable every year even when paid at maturity."]),
            "krd": {}, "no_mark_to_market": True,
        })
        row["warnings"].append("Bank CD: early withdrawal usually costs months of interest.")
        return row, it

    # ---- price discovery ----
    cu = (h.get("cusip") or "").upper()
    cat = ctx.catalogue.get(cu)
    manual = _num(h.get("current_price"))
    y_est = None
    if manual is not None:
        pa = _date(h.get("price_as_of"))
        clean, source = manual, "manual" + (f" · {pa.isoformat()}" if pa else "")
    elif cat and cat.get("price") and kind in GOVERNMENT:
        clean, source = cat["price"], f"TreasuryDirect FedInvest EOD {ctx.catalogue_as_of}"
    else:
        y_est, source = _estimated_yield(h, spec, ctx)
        clean = None
        if y_est is None:
            row["warnings"].append("No price and no curve — cannot value this bond.")
            row.update(market_value=0.0, dv01=0.0, annual_income=round(face * spec.coupon, 2))
            return row, it
    try:
        a = bm.analytics(spec, settle, clean=clean) if clean is not None else bm.analytics(spec, settle, y=y_est)
    except Exception as exc:  # noqa: BLE001
        a = None
        row["warnings"].append(f"Analytics failed: {exc}")
    if not a or a.get("ytw") is None:
        row.update(market_value=0.0, dv01=0.0, annual_income=round(face * spec.coupon, 2))
        row["warnings"].append("Could not solve a yield at this price — check the price/terms.")
        return row, it
    if clean is None:
        row["estimated_mark"] = True

    ir = 1.0
    tips_block = None
    if kind == "tips":
        projected = True
        if refc:
            ir_v, projected = bm.latest_index_ratio(settle, refc, ctx.cpi, ctx.inflation)
            ir = ir_v or 1.0
        else:
            row["warnings"].append(
                "Couldn't find this TIPS's reference CPI — check the CUSIP (TIPS CUSIPs start with 912), or enter "
                "'Ref CPI (dated date)' from TreasuryDirect. Until then the index ratio is assumed 1.0 and the value is too low.")
        tips_block = {"index_ratio": round(ir, 5), "ref_cpi": refc, "index_ratio_projected": projected,
                      "index_ratio_at_purchase": round(ir_buy, 5) if ir_buy else None,
                      "adjusted_principal": round(face * ir, 2), "real_yield_pct": _pct(a["ytw"]),
                      "breakeven_pct": _pct((tsy_here - a["ytw"]) if tsy_here is not None else None)}
    it.ir = ir
    it.ytw = a["ytw"]
    wk = next((w for w in bm.workouts(spec, settle) if w.date == a["ytw_date"]),
              bm.Workout(spec.maturity, spec.redemption, "maturity"))
    it.workout = wk
    mv = face * ir * a["dirty"] / 100.0
    dv01 = (a["eff_duration"] or 0.0) * mv * bm.BP
    pp = _num(h.get("purchase_price"))
    if cb:
        cost = cb                                   # the broker's total is authoritative for P&L
    elif pp and ir_buy:
        cost = face * pp / 100.0 * ir_buy           # TIPS: index ratio on the PURCHASE date, not today's
    else:
        cost = None
    if kind == "tips" and pp and not cb and not ir_buy:
        row["warnings"].append("Add the purchase date (or your broker's cost-basis total) so the TIPS cost "
                               "includes the index ratio you paid.")
    clean_mv = face * ir * a["clean"] / 100.0
    # NOMINAL total yield = coupons + pull-to-par (+ inflation accretion for TIPS, whose YTW is REAL)
    income_y = (face * ir * spec.coupon / mv) if mv else 0.0
    infl_y = 0.0
    total_y = a["ytw"]
    if kind == "tips":
        infl_y = bm.as_path(ctx.inflation).avg(bm.year_frac(settle, a["ytw_date"]))
        total_y = (1 + a["ytw"]) * (1 + infl_y) - 1
        infl_y = total_y - a["ytw"]                 # includes the real×inflation cross term
    it.tr, it.rates, it.cost, it.pp, it.pd = tr, rates, cost, pp, pd_
    it.ir_buy = ir_buy if kind == "tips" else 1.0
    book = None
    if pd_ and pp and pd_ < spec.maturity:
        try:
            b = bm.analytics(spec, pd_, clean=pp)
            book = b["ytw"] if b else None
        except Exception:  # noqa: BLE001
            book = None
    try:
        at = bm.after_tax_yield(spec, settle, a["clean"], rates, tr, workout=wk, inflation=ctx.inflation)
    except Exception:  # noqa: BLE001
        at = None
    _, nxt, _ = bm.coupon_schedule(spec, settle)
    krd = bm.key_rate_durations(spec, settle, a["ytw"], a["eff_duration"] or 0.0, wk)
    it.krd_share = {k: (v / a["eff_duration"]) if a["eff_duration"] else 0.0 for k, v in krd.items()}
    if pd_ and pp and kind == "muni":
        dm = bm.de_minimis_price(pd_, spec.maturity)
        if pp < dm:
            notes = notes + [f"Bought at {pp:.3f} < de-minimis {dm:.3f}: the discount is ordinary income at maturity."]
    row.update({
        "price": round(a["clean"], 4), "price_source": source, "accrued": round(a["accrued"], 5),
        "accrued_usd": round(face * ir * a["accrued"] / 100.0, 2),
        "purchase_price": _r(pp, 4) if pp else None, "purchase_price_derived": pp_derived,
        "dirty_price": round(a["dirty"], 4), "market_value": round(mv, 2), "clean_value": round(clean_mv, 2),
        "cost_basis": _r(cost), "unrealized_pnl": _r(clean_mv - cost) if cost else None,
        "unrealized_pnl_pct": _r((clean_mv / cost - 1) * 100) if cost else None,
        "ytm_pct": _pct(a["ytm"]), "ytw_pct": _pct(a["ytw"]), "ytw_date": a["ytw_date"].isoformat(),
        "ytw_kind": a["ytw_kind"], "ytc_pct": _pct(a["ytc"]),
        "yield_basis": "real yield to worst" if kind == "tips" else "yield to worst",
        "total_yield_pct": _pct(total_y), "yield_parts": yield_parts(total_y, income_y, infl_y),
        "current_yield_pct": _pct(a["current_yield"]), "book_yield_pct": _pct(book),
        "mac_duration": _r(a["mac_duration"], 3), "mod_duration": _r(a["mod_duration"], 3),
        "eff_duration": _r(a["eff_duration"], 3), "convexity": _r(a["eff_convexity"], 2),
        "dv01": round(dv01, 2), "annual_income": round(face * ir * spec.coupon, 2),
        "next_coupon_date": nxt.isoformat() if spec.coupon_per_period else None,
        "next_coupon_amount": round(face * ir * spec.coupon_per_period / 100.0, 2) if spec.coupon_per_period else None,
        "callable": a["callable"], "likely_called": a["likely_called"],
        "call_date": spec.calls[0][0].isoformat() if spec.calls else None,
        "call_price": spec.calls[0][1] if spec.calls else None,
        "spread_bp": round((a["ytw"] - tsy_here) * 1e4) if (tsy_here is not None and kind not in GOVERNMENT) else None,
        "tax": _tax_block(tr, t_int, (at or {}).get("after_tax"), rates,
                          notes + list((at or {}).get("notes") or []), (at or {}).get("after_tax_real")),
        "tips": tips_block,
        "krd": {str(k): round(v, 4) for k, v in krd.items() if abs(v) > 1e-6},
    })
    return row, it


# ---------------------------------------------------------------------------
# Aggregation, KRD, scenarios
# ---------------------------------------------------------------------------
def _wavg_vals(pairs: list[tuple[float | None, float]]) -> float | None:
    num = den = 0.0
    for v, w in pairs:
        if v is None or not w or w <= 0:
            continue
        num += v * w
        den += w
    return num / den if den else None


def _live(rows: list[dict]) -> list[dict]:
    return [r for r in rows if r["status"] == "held" and not r.get("matured")]


def aggregate(rows: list[dict], internals: dict) -> dict:
    live = _live(rows)
    mv = sum(r.get("market_value") or 0.0 for r in live)
    cost = sum(r.get("cost_basis") or 0.0 for r in live if r.get("cost_basis"))
    pnl = sum(r.get("unrealized_pnl") or 0.0 for r in live if r.get("unrealized_pnl") is not None)
    dv01 = sum(r.get("dv01") or 0.0 for r in live)
    income = sum(r.get("annual_income") or 0.0 for r in live)
    after_tax_income = sum((r.get("annual_income") or 0.0) * (1 - (((r.get("tax") or {}).get("rate_pct") or 0) / 100))
                           for r in live)
    indiv = [r for r in live if r["kind"] in INDIVIDUAL]
    w = lambda key: _wavg_vals([(r.get(key), r.get("market_value")) for r in live])  # noqa: E731
    wt = lambda key: _wavg_vals([((r.get("tax") or {}).get(key), r.get("market_value")) for r in live])  # noqa: E731
    wp = lambda key: _wavg_vals([((r.get("yield_parts") or {}).get(key), r.get("market_value"))  # noqa: E731
                                 for r in live if r.get("yield_parts")])
    total_y = w("total_yield_pct")
    parts = {k: _r(wp(k), 3) for k in ("income_pct", "price_gain_pct", "inflation_pct")}
    if total_y is not None and all(v is not None for v in parts.values()):
        parts["price_gain_pct"] = _r(total_y - parts["income_pct"] - parts["inflation_pct"], 3)   # sums exactly
    accrual = sum(r.get("annual_accrual") or 0.0 for r in live)
    summary = {
        "market_value": round(mv, 2), "cost_basis": round(cost, 2), "unrealized_pnl": round(pnl, 2),
        "annual_income": round(income, 2), "annual_income_after_tax": round(after_tax_income, 2),
        "ytw_pct": _r(w("ytw_pct"), 3), "after_tax_yield_pct": _r(wt("after_tax_yield_pct"), 3),
        # nominal TOTAL yield: coupons/distributions + pull-to-par / fund accrual + TIPS inflation
        "total_yield_pct": _r(total_y, 3), "yield_parts": {**parts, "total_pct": _r(total_y, 3)},
        "annual_total_return": round(sum((r.get("market_value") or 0.0) * (r.get("total_yield_pct") or 0.0) / 100 for r in live), 2),
        "annual_accrual": round(accrual, 2),
        "tey_pct": _r(wt("tey_pct"), 3),
        "eff_duration": _r(dv01 / (mv * bm.BP), 3) if mv else None,
        "convexity": _r(w("convexity"), 2),
        "years_to_maturity": _r(_wavg_vals([(r.get("years_to_maturity"), r.get("market_value")) for r in indiv]), 2),
        "current_yield_pct": _r(income / mv * 100, 3) if mv else None,
        "dv01": round(dv01, 2), "positions": len(live),
        "watchlist": sum(1 for r in rows if r["status"] == "watch"),
        "estimated_marks_pct": _r(100 * sum(r.get("market_value") or 0 for r in live if r.get("estimated_mark")) / mv, 1) if mv else 0.0,
    }

    def group(keyfn):
        g: dict[str, float] = defaultdict(float)
        for r in live:
            k = keyfn(r)
            if k is not None:
                g[k] += r.get("market_value") or 0.0
        return [{"key": k, "value": round(v, 2), "pct": round(100 * v / mv, 2) if mv else 0.0}
                for k, v in sorted(g.items(), key=lambda kv: -kv[1])]

    # credit: funds split by their credit mix (yfinance's us_government overlaps AAA/AA → carve it out first)
    credit: dict[str, float] = defaultdict(float)
    for r in live:
        v = r.get("market_value") or 0.0
        mix = (r.get("fund") or {}).get("credit_mix") or {}
        if r["kind"] in FUNDS and mix:
            gov = min(mix.get("us_government") or 0.0, 1.0)
            rest = {k: x for k, x in mix.items() if k != "us_government" and x}
            tot_rest = sum(rest.values())
            credit["GOVT"] += v * gov
            scale = (1 - gov) / tot_rest if tot_rest else 0.0
            for k, x in rest.items():
                credit[_FUND_RATING_MAP.get(k, "NR")] += v * x * scale
            if not tot_rest:
                credit["NR"] += v * (1 - gov)
        else:
            credit[r.get("rating_group") or "NR"] += v
    credit_l = [{"key": k, "value": round(credit[k], 2), "pct": round(100 * credit[k] / mv, 2) if mv else 0.0}
                for k in RATING_ORDER if credit.get(k)]
    order = [b[2] for b in MATURITY_BUCKETS]

    def _tax_key(r):
        tx = r.get("tax") or {}
        if not tx.get("taxable_account", True):
            return "tax-advantaged account"
        if not tx.get("fed_taxable", True):
            return "federal tax-exempt"
        if not tx.get("state_taxable", True):
            return "state tax-exempt"
        return "fully taxable"

    allocation = {
        "by_kind": group(lambda r: r["kind_label"]),
        "by_credit": credit_l,
        "by_maturity": sorted(group(lambda r: r.get("maturity_bucket") or "n/a"),
                              key=lambda x: order.index(x["key"]) if x["key"] in order else 99),
        "by_account": group(lambda r: r["account_type"]),
        "by_tax": group(_tax_key),
        "by_issuer": group(lambda r: (r.get("issuer") or r.get("label")) if r["kind"] in ("corporate", "muni", "agency", "cd") else None)[:10],
        "by_state": group(lambda r: r.get("state") or "—") if any(r["kind"] == "muni" for r in live) else [],
    }

    # key-rate DV01 ($ per bp per tenor) — reconciles to total DV01
    krd_dv01: dict[float, float] = {k: 0.0 for k in bm.KEY_TENORS}
    for r in live:
        it = internals.get(r["id"])
        if not it or not r.get("dv01"):
            continue
        for k, share in it.krd_share.items():
            krd_dv01[k] += (r["dv01"] or 0.0) * share
    krd = [{"tenor": k, "dv01": round(v, 2), "pct": round(100 * v / dv01, 1) if dv01 else 0.0} for k, v in krd_dv01.items()]
    return {"summary": summary, "allocation": allocation, "key_rate_dv01": krd}


def _twist(tenor: float, short_bp: float, long_bp: float) -> float:
    """Linear-in-log-tenor shift between the 3m (short) and 30y (long) ends."""
    a, b = math.log(0.25), math.log(30.0)
    x = min(max(math.log(max(tenor, 0.25)), a), b)
    return short_bp + (long_bp - short_bp) * (x - a) / (b - a)


def scenarios(rows: list[dict], internals: dict, settle: date, key_rate_dv01: list[dict]) -> dict:
    live = _live(rows)
    base = sum(r.get("market_value") or 0.0 for r in live)
    parallel = []
    for bp in SHOCKS_BP:
        d = bp * bm.BP
        pnl = 0.0
        for r in live:
            it = internals.get(r["id"])
            mv = r.get("market_value") or 0.0
            if not it or not mv or r.get("no_mark_to_market"):
                continue
            if it.spec is not None and it.ytw is not None:
                p1 = bm.scenario_price(it.spec, settle, it.ytw, d)
                pnl += it.face * it.ir * p1 / 100.0 - mv
            elif it.fund_duration:
                dur = it.fund_duration
                cvx = dur * dur + dur
                pnl += mv * (-dur * d + 0.5 * cvx * d * d)
        parallel.append({"shift_bp": bp, "pnl": round(pnl, 2), "pnl_pct": round(100 * pnl / base, 2) if base else 0.0,
                         "value": round(base + pnl, 2)})
    twists = []
    for name, s, l, desc in (("Bull steepener", -100, -25, "front end −100bp, long end −25bp (Fed cutting)"),
                             ("Bear steepener", 25, 100, "front end +25bp, long end +100bp (term premium rising)"),
                             ("Bull flattener", -25, -100, "front end −25bp, long end −100bp (flight to quality)"),
                             ("Bear flattener", 100, 25, "front end +100bp, long end +25bp (Fed hiking)")):
        pnl = -sum(k["dv01"] * _twist(k["tenor"], s, l) for k in key_rate_dv01)
        twists.append({"name": name, "description": desc, "pnl": round(pnl, 2),
                       "pnl_pct": round(100 * pnl / base, 2) if base else 0.0})
    credit = []
    for name, ig_bp, hy_bp in (("Credit stress", 100, 300), ("Severe credit stress", 200, 600)):
        pnl = 0.0
        for r in live:
            mv = r.get("market_value") or 0.0
            dur = r.get("eff_duration") or 0.0
            if r["kind"] in FUNDS:
                mix = (r.get("fund") or {}).get("credit_mix") or {}
                gov = min(mix.get("us_government") or 0.0, 1.0)
                hy = sum(mix.get(k) or 0 for k in ("bb", "b", "below_b"))
                ig = max(0.0, 1 - gov - hy)
                if (r.get("fund") or {}).get("tax_class") == "muni":
                    ig, hy = ig * 0.5, hy * 0.5
                pnl -= mv * dur * (ig * ig_bp + hy * hy_bp) * bm.BP
            elif r["kind"] in ("corporate", "agency", "muni"):
                grp = r.get("rating_group") or "NR"
                widen = hy_bp if grp in ("BB", "B", "CCC") else ig_bp
                if r["kind"] == "muni":
                    widen *= 0.5
                elif r["kind"] == "agency":
                    widen *= 0.25
                pnl -= mv * dur * widen * bm.BP
        credit.append({"name": name, "description": f"IG spreads +{ig_bp}bp, high-yield +{hy_bp}bp (munis ½, agencies ¼)",
                       "pnl": round(pnl, 2), "pnl_pct": round(100 * pnl / base, 2) if base else 0.0})
    return {"parallel": parallel, "twists": twists, "credit": credit,
            "note": "Parallel shocks fully reprice every bond (to worst, so calls are honoured); funds use duration + "
                    "convexity; twists use key-rate DV01; TIPS real yields are shocked 1:1. Bank CDs are not marked to market."}


# ---------------------------------------------------------------------------
# Cash-flow projection
# ---------------------------------------------------------------------------
def phantom_income_by_year(r: dict, it: _Internal, settle: date, inflation, years: int) -> dict[int, float]:
    """Income that is TAXED EACH YEAR but not paid in cash (taxable accounts, federally-taxable interest):
    TIPS inflation accretion and zero-coupon OID (> 1y). Shared by the cash-flow and tax projections."""
    out: dict[int, float] = {}
    tr = it.tr
    path = bm.as_path(inflation)
    if not it.spec or not tr or not tr.taxable_account or not tr.fed_taxable:
        return out
    is_tips = r["kind"] == "tips"
    is_oid = (not is_tips and it.spec.coupon_per_period == 0 and r["kind"] != "cd"
              and (it.spec.maturity - settle).days > 366)
    if not (is_tips or is_oid):
        return out
    for k in range(years):
        yr = settle.year + k
        if date(yr, 1, 1) > it.spec.maturity:
            break
        frac = 1.0 if k else max(0.0, (date(yr, 12, 31) - settle).days / 365.25)
        if yr == it.spec.maturity.year:
            frac *= max(0.0, (it.spec.maturity - date(yr, 1, 1)).days / 365.25)
        acc = (it.face * it.ir * path.factor(k) * path.rate_at(k) * frac) if is_tips \
            else (r.get("market_value") or 0.0) * (it.ytw or 0.0) * frac
        if acc > 0:
            out[yr] = acc
    return out


def _redemption_split(r: dict, it: _Internal, prin: float, settle: date, rates: bm.TaxRates | None = None) -> dict:
    """Split a principal payment into YOUR COST RETURNED + GAIN (never double-counted: capital + gain == prin),
    and the tax due on that gain at redemption (bond_math helpers — same rules as the after-tax yield).
    ``rates`` = the rates in the redemption year (after retirement they can differ from today's)."""
    cost, tr = it.cost, it.tr
    rates = rates or it.rates
    t_int = bm.interest_tax_rate(rates, tr) if (rates and tr) else it.t_int
    if cost is None or cost <= 0:
        return {"capital": prin, "gain": 0.0, "premium_loss": 0.0, "tax_on_gain": 0.0, "gain_known": False}
    gain = prin - cost
    tax = 0.0
    if tr and rates and tr.taxable_account and gain > 0 and it.face:
        kind = r["kind"]
        held_from = it.pd or settle
        maturity = it.spec.maturity if it.spec else settle
        full_years = int(max(0.0, bm.year_frac(held_from, maturity)))
        if kind in FUNDS:                                     # defined-maturity ETF winds up → capital gain
            tax = gain * (rates.ltcg + rates.niit + rates.state)
        elif kind == "tips":
            # inflation accretion was taxed yearly (phantom); only a REAL-price discount is left to tax
            if it.pp is not None and it.ir_buy:
                t, _ = bm.redemption_gain_tax(100.0 - it.pp, full_years, rates, tr)
                tax = t * it.face * it.ir_buy / 100.0
        elif it.spec is not None and it.spec.coupon_per_period == 0 and kind != "cd":
            if bm.year_frac(held_from, maturity) <= 1.0:      # T-bill-style discount: interest at maturity
                tax = gain * t_int
            # longer zeros: OID was taxed every year (phantom) → nothing left at maturity
        elif kind != "cd":
            t, _ = bm.redemption_gain_tax(gain / it.face * 100.0, full_years, rates, tr)
            tax = t * it.face / 100.0
    return {"capital": min(prin, cost), "gain": max(0.0, gain), "premium_loss": max(0.0, -gain),
            "tax_on_gain": tax, "gain_known": True}


def project_cash_flows(rows: list[dict], internals: dict, settle: date, *, years: int = 30,
                       fund_years: int = 10, inflation=0.025, assume_calls: bool = False,
                       include_watch: bool = False, tax: TaxSchedule | None = None) -> dict:
    """Every coupon / principal / distribution from today forward, plus yearly and monthly rollups.

    Principal is split into **your cost returned + gain** (discount bonds) — the gain is PART of the
    principal, never an extra flow, so ``principal == capital_returned + gain`` in every event and bucket.
    After-tax amounts use the same rules as the after-tax yield: premium amortized against coupons (tax
    shield), market discount taxed at redemption (ordinary / de-minimis capital gain), TIPS accretion and
    zero-coupon OID taxed yearly as phantom income (cash-less ``tax`` events). With a ``tax`` schedule,
    every event is taxed at the rates of ITS year (e.g. lower rates once you retire).
    """
    events: list[dict] = []
    path = bm.as_path(inflation)
    end = date(settle.year + years, 12, 31)
    statuses = ("held", "watch") if include_watch else ("held",)
    for r in rows:
        if r.get("matured") or r["status"] not in statuses:
            continue
        it = internals.get(r["id"])
        if not it:
            continue
        t_int = it.t_int
        base = {"holding_id": r["id"], "label": r["label"], "kind": r["kind"]}
        retired = (tax is not None and tax.retired is not None and it.tr is not None)
        t_ret = bm.interest_tax_rate(tax.retired, it.tr) if retired else t_int

        def R(d: date, it=it):                       # noqa: E306 — the holder's rates in that year
            return tax.at(d.year) if retired else it.rates

        def T(d: date, t_int=t_int, t_ret=t_ret):     # interest tax rate in that year
            return t_ret if (retired and d.year >= tax.from_year) else t_int

        def principal_event(d, prin, typ="principal", projected=False):
            sp = _redemption_split(r, it, prin, settle, R(d))
            ev = {**base, "date": d, "type": typ, "amount": prin, "after_tax": prin - sp["tax_on_gain"], **sp}
            if projected:
                ev["projected"] = True
            events.append(ev)

        if it.bank_cd_value_at_maturity is not None and it.spec:
            d = it.spec.maturity
            interest = it.bank_cd_value_at_maturity - it.face
            events.append({**base, "date": d, "type": "coupon", "amount": interest, "after_tax": interest * (1 - T(d))})
            principal_event(d, it.face)
            continue
        if it.spec is not None and it.ytw is not None:
            wk = it.workout if (assume_calls and it.workout) else bm.Workout(it.spec.maturity, it.spec.redemption, "maturity")
            flows = bm.cash_flows(it.spec, settle, wk)
            is_tips = r["kind"] == "tips"
            # premium still to amortize against the remaining coupons (tax shield) — nominal coupon bonds
            shield = 0.0
            if not is_tips and it.pp is not None and it.tr and it.spec.coupon_per_period > 0:
                prem = it.pp - wk.price
                if prem > 0 and it.pd and it.pd < settle:
                    life = bm.year_frac(it.pd, wk.date)
                    prem *= (bm.year_frac(settle, wk.date) / life) if life > 0 else 0.0
                n_cpn = sum(1 for _ in flows)
                shield = bm.premium_amortization_shield(prem, n_cpn, t_int, it.tr) * it.face / 100.0
            for i, (d, amt, _) in enumerate(flows):
                last = i == len(flows) - 1
                g = path.factor(bm.year_frac(settle, d)) if is_tips else 1.0
                cpn = (amt - (wk.price if last else 0.0)) * it.face / 100.0 * it.ir * g
                if cpn > 1e-9:
                    # the premium shield is worth your rate that year
                    sh = shield * (T(d) / t_int) if t_int else 0.0
                    events.append({**base, "date": d, "type": "coupon", "amount": cpn,
                                   "after_tax": cpn * (1 - T(d)) + sh})
                if last:
                    prin = wk.price * it.face / 100.0 * (max(it.ir * g, 1.0) if is_tips else 1.0)
                    principal_event(d, prin, "call" if wk.kind == "call" else "principal")
            for yr, inc in phantom_income_by_year(r, it, settle, inflation, years + 1).items():
                ry = R(date(yr, 12, 31))
                ptax = inc * (ry.fed + ry.niit) if ry else 0.0
                events.append({**base, "date": date(yr, 12, 31), "type": "tax", "amount": 0.0, "after_tax": -ptax,
                               "phantom_income": inc})
            continue
        if it.fund_yield is not None:
            mv = r.get("market_value") or 0.0
            # cash distributions on a NAV that grows at the accrual rate (accumulating funds: no cash at all —
            # the whole return arrives when sold, or at a defined-maturity ETF's wind-up)
            g = it.fund_accrual or 0.0
            dm_year = r.get("defined_maturity_year")
            stop = date(dm_year, 12, 15) if dm_year else None
            if it.fund_yield > 0:
                for k in range(1, fund_years * 12 + 1):
                    d = bm.add_months(settle, k)
                    if stop and d > stop:
                        break
                    monthly = mv * (1 + g) ** (k / 12) * it.fund_yield / 12.0
                    events.append({**base, "date": d, "type": "distribution", "amount": monthly,
                                   "after_tax": monthly * (1 - T(d)), "projected": True})
            if stop and settle < stop <= end:
                principal_event(stop, mv * (1 + g) ** bm.year_frac(settle, stop), projected=True)
    events = [e for e in events if settle < e["date"] <= end]
    events.sort(key=lambda e: e["date"])

    def _bucket(key, **extra):
        return {**extra, "coupon": 0.0, "principal": 0.0, "distribution": 0.0, "total": 0.0, "after_tax": 0.0,
                "capital_returned": 0.0, "gain": 0.0, "premium_loss": 0.0, "tax_on_gains": 0.0, "phantom_tax": 0.0}

    by_year: dict[int, dict] = {}
    for y in range(settle.year, end.year + 1):
        by_year[y] = {**_bucket(y, year=y), "real_total": 0.0, "by_kind": defaultdict(float)}
    months: dict[str, dict] = {}
    m_end = bm.add_months(settle, 24)

    def _add(b, e):
        if e["type"] == "tax":
            b["phantom_tax"] += -e["after_tax"]
            b["after_tax"] += e["after_tax"]
            return
        typ = "principal" if e["type"] in ("principal", "call") else e["type"]
        b[typ] += e["amount"]
        b["total"] += e["amount"]
        b["after_tax"] += e["after_tax"]
        if typ == "principal":
            b["capital_returned"] += e.get("capital", e["amount"])
            b["gain"] += e.get("gain", 0.0)
            b["premium_loss"] += e.get("premium_loss", 0.0)
            b["tax_on_gains"] += e.get("tax_on_gain", 0.0)

    for e in events:
        b = by_year[e["date"].year]
        _add(b, e)
        if e["type"] != "tax":
            b["real_total"] += e["amount"] / path.factor(bm.year_frac(settle, e["date"]))
            b["by_kind"][e["kind"]] += e["amount"]
        if e["date"] <= m_end:
            mk = e["date"].strftime("%Y-%m")
            _add(months.setdefault(mk, _bucket(mk, month=mk)), e)

    def _round_bucket(b):
        out = {k: (round(v, 2) if isinstance(v, float) else v) for k, v in b.items() if k != "by_kind"}
        if "by_kind" in b:
            out["by_kind"] = {k: round(v, 2) for k, v in b["by_kind"].items()}
        return out

    yearly = [_round_bucket(b) for b in by_year.values()]
    while len(yearly) > 5 and yearly[-1]["total"] == 0 and yearly[-1]["phantom_tax"] == 0:
        yearly.pop()
    one_year = settle + timedelta(days=365)
    next12 = [e for e in events if e["date"] <= one_year]
    ser = []
    for e in events:
        x = {**e, "date": e["date"].isoformat(), "amount": round(e["amount"], 2), "after_tax": round(e["after_tax"], 2)}
        for k in ("capital", "gain", "premium_loss", "tax_on_gain", "phantom_income"):
            if k in x:
                x[k] = round(x[k], 2)
        ser.append(x)
    prin_events = [e for e in events if e["type"] in ("principal", "call")]
    accumulating = sorted({r["label"] for r in rows if r["status"] in statuses and not r.get("defined_maturity_year")
                           and (r.get("fund") or {}).get("payout") == "accumulates"})
    return {
        "as_of": settle.isoformat(), **inflation_fields(path), "assume_calls": assume_calls,
        "yearly": yearly, "monthly": [_round_bucket(m) for m in sorted(months.values(), key=lambda m: m["month"])],
        "next_12m": {
            "income": round(sum(e["amount"] for e in next12 if e["type"] in ("coupon", "distribution")), 2),
            "income_after_tax": round(sum(e["after_tax"] for e in next12 if e["type"] in ("coupon", "distribution")), 2),
            "principal": round(sum(e["amount"] for e in next12 if e["type"] in ("principal", "call")), 2),
            "gain": round(sum(e.get("gain", 0.0) for e in next12 if e["type"] in ("principal", "call")), 2),
            "events": [x for x in ser if x["date"] <= one_year.isoformat() and x["type"] != "tax"],
        },
        "gains": {
            "total_gain": round(sum(e.get("gain", 0.0) for e in prin_events), 2),
            "total_premium_loss": round(sum(e.get("premium_loss", 0.0) for e in prin_events), 2),
            "tax_on_gains": round(sum(e.get("tax_on_gain", 0.0) for e in prin_events), 2),
            "phantom_tax": round(sum(-e["after_tax"] for e in events if e["type"] == "tax"), 2),
            "unknown_cost": sorted({e["label"] for e in prin_events if not e.get("gain_known", True)}),
        },
        "events_count": len(ser),
        "events": ser,
        "notes": ["Principal = your cost returned + gain at maturity (bought at a discount) — the gain is part of the principal, "
                  "not extra cash. A premium you paid above par isn't returned; it's written off against the coupons for tax.",
                  "Fund distributions are projected at their cash yield (yours if you entered one); funds never mature, "
                  "except defined-maturity ETFs.",
                  *([f"{', '.join(accumulating)} pay no distributions — their return builds up in the price and only becomes "
                     "cash when you sell (the Planner sells them in gap years)."] if accumulating else []),
                  "TIPS coupons and principal are projected along your inflation path; principal has a deflation floor at par.",
                  *([f"Taxed at your after-retirement rates from {tax.from_year}."] if (tax and tax.retired) else [])],
    }


# ---------------------------------------------------------------------------
# Tax projection
# ---------------------------------------------------------------------------
def _tax_year(yr: int) -> dict:
    return {"year": yr, "fed_taxable_interest": 0.0, "state_taxable_interest": 0.0, "tax_exempt_interest": 0.0,
            "sheltered_interest": 0.0, "phantom_income": 0.0, "tax_on_gains": 0.0, "fed_tax": 0.0, "state_tax": 0.0}


def tax_projection(rows: list[dict], internals: dict, cash: dict, rates, settle: date,
                   inflation, years: int = 5) -> dict:
    sched = as_schedule(rates)
    rates = sched.now
    per_year: dict[int, dict] = {}
    by_id = {r["id"]: r for r in rows}
    for e in cash["events"]:
        d = _date(e["date"])
        if not d or d.year >= settle.year + years or e["type"] not in ("coupon", "distribution"):
            continue
        tx = (by_id.get(e["holding_id"]) or {}).get("tax") or {}
        y = per_year.setdefault(d.year, _tax_year(d.year))
        amt = e["amount"]
        if not tx.get("taxable_account", True):
            y["sheltered_interest"] += amt
            continue
        if tx.get("fed_taxable", True):
            y["fed_taxable_interest"] += amt
        else:
            y["tax_exempt_interest"] += amt
        if tx.get("state_taxable", True):
            y["state_taxable_interest"] += amt
    # phantom income: TIPS inflation accretion + OID on zeros (same helper as the cash-flow projection)
    for r in _live(rows):
        it = internals.get(r["id"])
        if not it:
            continue
        for yr, inc in phantom_income_by_year(r, it, settle, inflation, years).items():
            per_year.setdefault(yr, _tax_year(yr))["phantom_income"] += inc
    # gains realized at redemption (market discount / defined-maturity ETF wind-up) — taxed in that year
    for e in cash["events"]:
        d = _date(e["date"])
        if d and d.year < settle.year + years and e.get("tax_on_gain"):
            per_year.setdefault(d.year, _tax_year(d.year))["tax_on_gains"] += e["tax_on_gain"]
    out = []
    for yr in sorted(per_year):
        y = per_year[yr]
        ry = sched.at(yr)
        y["fed_tax"] = (y["fed_taxable_interest"] + y["phantom_income"]) * (ry.fed + ry.niit)
        y["state_tax"] = y["state_taxable_interest"] * ry.state
        y["retired_rates"] = ry is not sched.now
        y["total_tax"] = y["fed_tax"] + y["state_tax"] + y["tax_on_gains"]
        gross = y["fed_taxable_interest"] + y["tax_exempt_interest"] + y["phantom_income"]
        y["effective_rate_pct"] = round(100 * y["total_tax"] / gross, 2) if gross else 0.0
        out.append({k: (round(v, 2) if isinstance(v, float) else v) for k, v in y.items()})
    return {"years": out, "rates": {"federal_pct": round(rates.fed * 100, 2), "state_pct": round(rates.state * 100, 2),
                                    "niit_pct": round(rates.niit * 100, 2), "ltcg_pct": round(rates.ltcg * 100, 2)},
            "retired_rates": tax_schedule_fields(sched),
            "notes": ["Estimates at your marginal rates; ignores SALT-deduction interplay, AMT and state-specific quirks.",
                      "Phantom income = TIPS inflation accretion and zero-coupon OID taxed before you receive the cash."]}


# ---------------------------------------------------------------------------
# After-tax yield menu (best instrument per tenor for THIS investor)
# ---------------------------------------------------------------------------
CANDIDATES = [("treasury", None, "Treasury"), ("cd", None, "Brokered CD"), ("agency", None, "Agency"),
              ("muni", None, "Muni (in-state)"), ("corporate", "AA", "Corporate AA"),
              ("corporate", "A", "Corporate A"), ("corporate", "BBB", "Corporate BBB"), ("tips", None, "TIPS")]


def after_tax_menu(mi: dict, profile: dict, tenors: list[float], *, account: str = "taxable",
                   inflation=0.025, kinds: set[str] | None = None) -> list[dict]:
    rates = tax_rates(profile)
    taxable_acct = account not in TAX_ADVANTAGED
    out = []
    for t in tenors:
        cands = []
        for kind, rating, label in CANDIDATES:
            if kinds and kind not in kinds:
                continue
            y, basis = mkt.model_yield(kind, t, mi, rating)
            if y is None:
                continue
            tr, _ = tax_treatment({"kind": kind, "account_type": account, "state": (profile or {}).get("state")}, profile)
            if kind == "tips":
                pre = (1 + y) * (1 + bm.as_path(inflation).avg(t)) - 1
                t_rate = (rates.fed + rates.niit) if taxable_acct else 0.0
                ate = pre * (1 - t_rate)
            else:
                pre = y
                t_rate = bm.interest_tax_rate(rates, tr)
                ate = bm.simple_after_tax_yield(y, rates, tr)
            cands.append({"kind": kind, "rating": rating, "label": label, "pre_tax_pct": _pct(pre),
                          "real_yield_pct": _pct(y) if kind == "tips" else None,
                          "after_tax_pct": _pct(ate), "tey_pct": _pct(bm.tax_equivalent_yield(ate, rates)),
                          "tax_rate_pct": _pct(t_rate, 2), "basis": basis, "credit_risk": kind == "corporate"})
        cands.sort(key=lambda c: -(c["after_tax_pct"] if c["after_tax_pct"] is not None else -99))
        out.append({"tenor": t, "candidates": cands, "best": cands[0] if cands else None,
                    "best_no_credit": next((c for c in cands if not c["credit_risk"] and c["kind"] != "tips"), None)})
    return out


# ---------------------------------------------------------------------------
# Recommendations (deterministic rules — every number is computed, none invented)
# ---------------------------------------------------------------------------
def _rec(rid, severity, category, title, detail, *, impact=None, ids=None, action=None) -> dict:
    return {"id": rid, "severity": severity, "category": category, "title": title, "detail": detail,
            "impact_usd": _r(impact), "holding_ids": ids or [], "action": action}


def recommendations(rows: list[dict], agg: dict, profile: dict, ctx: Ctx, rates: bm.TaxRates) -> list[dict]:
    recs: list[dict] = []
    live = _live(rows)
    mv = agg["summary"]["market_value"] or 0.0
    settle = ctx.settle
    infl = bm.as_path(ctx.inflation)

    # 1) matured but still marked held
    for r in rows:
        if r.get("matured") and r["status"] == "held":
            recs.append(_rec(f"matured-{r['id']}", "high", "data", f"{r['label']} has matured",
                             "Principal should be back in your account. Mark it matured and put the cash to work "
                             "(reinvestment menu on the Ladders tab).", ids=[r["id"]], action="mark_matured"))

    # 2) munis in tax-advantaged accounts
    for r in live:
        if r["kind"] == "muni" and r["account_type"] in TAX_ADVANTAGED:
            t = r.get("years_to_maturity") or 5
            alt, _ = mkt.model_yield("treasury", t, ctx.mi)
            own = (r.get("ytw_pct") or 0) / 100
            gain = ((alt if alt is not None else own) - own) * (r.get("market_value") or 0)
            if gain <= 0:
                continue
            recs.append(_rec(f"muni-ira-{r['id']}", "high" if gain > 200 else "medium", "tax",
                             f"Tax-free muni inside a {r['account_type'].upper()}",
                             f"{r['label']} yields {own * 100:.2f}% tax-free, but the account already shelters interest. "
                             f"A {t:.0f}y Treasury yields ~{(alt or 0) * 100:.2f}% there — you're paying for a tax break you can't use.",
                             impact=gain, ids=[r["id"]], action="swap"))

    # 3) phantom income in taxable accounts (TIPS / zero-coupon OID)
    for r in live:
        if not (r.get("tax") or {}).get("taxable_account"):
            continue
        if r["kind"] == "tips":
            phantom = ((r.get("tips") or {}).get("adjusted_principal") or 0) * infl.short
            tax = phantom * (rates.fed + rates.niit)
            recs.append(_rec(f"tips-taxable-{r['id']}", "medium", "tax", f"TIPS in a taxable account: {r['label']}",
                             f"Inflation accretion (~${phantom:,.0f}/yr at {infl.short * 100:.1f}%) is taxed every year though the cash "
                             f"only arrives at maturity (~${tax:,.0f}/yr federal). TIPS are best held in an IRA/401k.",
                             impact=tax, ids=[r["id"]], action="relocate"))
        elif r["kind"] in ("treasury", "corporate", "agency") and r.get("coupon_freq") == 0 and (r.get("years_to_maturity") or 0) > 1:
            oid = (r.get("market_value") or 0) * (r.get("ytw_pct") or 0) / 100
            recs.append(_rec(f"oid-taxable-{r['id']}", "low", "tax", f"Zero-coupon in taxable: {r['label']}",
                             f"OID of ~${oid:,.0f}/yr is taxed annually with no cash paid — zeros belong in tax-deferred accounts.",
                             impact=oid * (rates.fed + rates.niit), ids=[r["id"]], action="relocate"))

    # 4) tax-aware relative value vs the best same-maturity no-credit-risk alternative
    for r in live:
        if r["kind"] not in INDIVIDUAL or r["kind"] == "tips" or not r.get("years_to_maturity"):
            continue
        own_at = (r.get("tax") or {}).get("after_tax_yield_pct")
        if own_at is None:
            continue
        menu = after_tax_menu(ctx.mi, profile, [max(0.25, r["years_to_maturity"])], account=r["account_type"],
                              inflation=infl, kinds={"treasury", "muni", "cd", "agency"})
        best = menu[0]["best"] if menu else None
        if best and best["after_tax_pct"] is not None and best["kind"] != r["kind"] and best["after_tax_pct"] - own_at >= 0.30:
            diff = (best["after_tax_pct"] - own_at) / 100
            recs.append(_rec(f"rv-{r['id']}", "medium", "income", f"{r['label']}: a {best['label']} nets more after tax",
                             f"Yours nets {own_at:.2f}% after tax; a same-maturity {best['label']} nets ~{best['after_tax_pct']:.2f}% "
                             f"for you ({best['basis']}). Consider swapping — mind bid/ask and any gain/loss you'd realize.",
                             impact=diff * (r.get("market_value") or 0), ids=[r["id"]], action="swap"))

    # 5) FDIC coverage on CDs (per issuing bank)
    banks: dict[str, list[dict]] = defaultdict(list)
    for r in live:
        if r["kind"] == "cd":
            banks[(r.get("issuer") or r["label"] or "").strip().lower()].append(r)
    for bank, rs in banks.items():
        tot = sum(max(x.get("face") or 0, x.get("market_value") or 0) for x in rs)
        if tot > FDIC_LIMIT:
            recs.append(_rec(f"fdic-{bank}", "high", "risk",
                             f"${tot - FDIC_LIMIT:,.0f} above FDIC insurance at {rs[0].get('issuer') or rs[0]['label']}",
                             f"FDIC covers ${FDIC_LIMIT:,.0f} per depositor, per bank, per ownership category (principal + accrued). "
                             "Split across banks or ownership categories, or use Treasury bills (no limit).",
                             impact=tot - FDIC_LIMIT, ids=[x["id"] for x in rs], action="diversify"))

    # 6) call risk
    for r in live:
        if r.get("likely_called") and r.get("ytw_date"):
            d = _date(r["ytw_date"])
            yrs = bm.year_frac(settle, d) if d else 0
            if yrs <= 3:
                t_left = max(0.5, (r.get("years_to_maturity") or 1) - yrs)
                re_y, _ = mkt.model_yield(r["kind"], t_left, ctx.mi, r.get("rating"))
                drop = ((r.get("coupon_pct") or 0) / 100 - (re_y or 0)) * (r.get("face") or 0)
                recs.append(_rec(f"call-{r['id']}", "medium", "risk", f"{r['label']} is likely to be called on {r['ytw_date']}",
                                 f"It trades above its call price, so your yield-to-worst is the call yield ({r.get('ytw_pct')}%). "
                                 f"If called you'd reinvest ~${r.get('face') or 0:,.0f} at ~{(re_y or 0) * 100:.2f}%"
                                 + (f" — about ${drop:,.0f}/yr less income." if drop > 0 else "."),
                                 impact=max(0.0, drop), ids=[r["id"]], action="plan_reinvestment"))

    # 7) maturing within 90 days → reinvestment menu
    soon = [r for r in live if r["kind"] in INDIVIDUAL and r.get("years_to_maturity") is not None and r["years_to_maturity"] <= 0.25]
    if soon:
        amt = sum((r.get("face") or 0) for r in soon)
        menu = after_tax_menu(ctx.mi, profile, [1, 2, 5, 10], inflation=infl, kinds={"treasury", "muni", "cd"})
        best = "; ".join(f"{m['tenor']:.0f}y {m['best']['label']} {m['best']['after_tax_pct']:.2f}% after tax" for m in menu if m["best"])
        recs.append(_rec("maturing-soon", "medium", "ladder", f"${amt:,.0f} matures in the next 90 days",
                         f"{len(soon)} holding(s) mature soon. Best after-tax options for you right now: {best}. "
                         "Extending to the far end of your ladder keeps it rolling.", ids=[r["id"] for r in soon], action="reinvest"))

    # 8) concentration — single issuer / credit quality
    issuers: dict[str, float] = defaultdict(float)
    for r in live:
        if r["kind"] in ("corporate", "muni", "agency"):
            issuers[(r.get("issuer") or r["label"])] += r.get("market_value") or 0
    for name, v in issuers.items():
        if mv and v / mv > 0.10 and v > 10000:
            recs.append(_rec(f"conc-{name}", "medium", "risk", f"{v / mv * 100:.0f}% of the book in one issuer: {name}",
                             "Single-name credit risk: a downgrade or default hits hard. Institutional guidelines usually cap one "
                             "non-government issuer near 5%.", action="diversify"))
    credit = {x["key"]: x["pct"] for x in agg["allocation"]["by_credit"]}
    hy = sum(credit.get(k, 0) for k in ("BB", "B", "CCC"))
    if hy > 20:
        recs.append(_rec("hy-share", "medium", "risk", f"{hy:.0f}% below investment grade",
                         "High yield behaves more like equity in a downturn; it won't cushion a stock sell-off the way "
                         "Treasuries do.", action="review"))

    # 9) maturity wall + ladder gaps (individual bonds + defined-maturity ETFs)
    by_year: dict[int, float] = defaultdict(float)
    for r in live:
        if r.get("maturity_year") and (r["kind"] in INDIVIDUAL or r.get("defined_maturity_year")):
            by_year[r["maturity_year"]] += r.get("market_value") or 0
    tot_dated = sum(by_year.values())
    if by_year and tot_dated:
        yr, v = max(by_year.items(), key=lambda kv: kv[1])
        if v / tot_dated > 0.35 and sum(1 for r in live if r.get("maturity_year")) >= 3:
            recs.append(_rec("maturity-wall", "medium", "ladder", f"{v / tot_dated * 100:.0f}% of dated bonds mature in {yr}",
                             "A maturity wall concentrates reinvestment risk: if rates are low that year, a big slice of your "
                             "income resets lower at once. Spread maturities (a ladder) to average it out.", action="build_ladder"))
        if len(by_year) >= 3:
            yrs = sorted(by_year)
            gaps = [y for y in range(yrs[0], yrs[-1] + 1) if y not in by_year]
            if gaps:
                recs.append(_rec("ladder-gaps", "low", "ladder", f"Ladder gaps: nothing matures in {', '.join(map(str, gaps[:6]))}",
                                 "A year with no maturities means no principal to reinvest or spend that year. The ladder builder "
                                 "can fill gaps with Treasuries/CDs.", action="fill_gaps"))

    # 10) duration vs horizon
    hz = _num((profile or {}).get("horizon_years"))
    dur = agg["summary"].get("eff_duration")
    if hz and dur is not None and mv:
        if dur > hz * 1.5 and dur - hz > 2:
            recs.append(_rec("dur-long", "medium", "risk", f"Duration {dur:.1f}y is well beyond your {hz:.0f}y horizon",
                             f"If you need the money in ~{hz:.0f}y, a +1% rate rise costs ~{dur:.1f}% today and you may have to sell "
                             "at a loss. Matching duration to horizon (immunization) locks in today's yield.", action="shorten"))
        elif dur < hz * 0.5 and hz - dur > 2:
            recs.append(_rec("dur-short", "low", "income", f"Duration {dur:.1f}y is much shorter than your {hz:.0f}y horizon",
                             "You're exposed to reinvestment risk: if rates fall you'll roll into lower yields. Extending "
                             "locks in current rates for longer.", action="extend"))

    # 11) fund expense ratios
    for r in live:
        er = (r.get("fund") or {}).get("expense_ratio_pct")
        if r["kind"] in FUNDS and er is not None and er > 0.25:
            drag = (er - 0.05) / 100 * (r.get("market_value") or 0)
            recs.append(_rec(f"er-{r['id']}", "medium" if er > 0.5 else "low", "cost", f"{r['label']} costs {er:.2f}%/yr",
                             f"Broad bond index funds cost ~0.03–0.05%. That's ~${drag:,.0f}/yr of yield lost, every year — "
                             "or build the exposure with individual Treasuries/CDs at zero ongoing cost.",
                             impact=drag, ids=[r["id"]], action="swap"))

    # 12) estimated marks dominate
    if agg["summary"].get("estimated_marks_pct", 0) > 50:
        recs.append(_rec("marks", "low", "data", f"{agg['summary']['estimated_marks_pct']:.0f}% of value uses estimated prices",
                         "Corporates, munis and CDs have no free live quotes. Values use today's curve + each bond's spread at "
                         "purchase. Enter the price from your broker statement for exact marks.", action="edit_prices"))

    # 13) market context (grounded in live data)
    real10 = (ctx.rate_context or {}).get("real_10y") or {}
    if real10.get("percentile_20y") and real10["percentile_20y"] >= 80:
        recs.append(_rec("real-yields", "info", "market",
                         f"10y real yield {real10['value_pct']:.2f}% — higher than {min(real10['percentile_20y'], 99.0):.0f}% of days in the last 20 years",
                         f"TIPS lock in inflation-proof income far above the 20y average ({real10.get('avg_20y_pct')}%). "
                         "A TIPS ladder can fund a real-dollar income floor (TIPS tab).", action="tips_ladder"))
    npts = (ctx.mi.get("nominal") or {}).get("points") or []
    if npts:
        y3m, y10 = bm.interp(npts, 0.25), bm.interp(npts, 10)
        if y3m and y10 and y3m > y10:
            recs.append(_rec("curve-inverted", "info", "market", "Yield curve inverted (3m > 10y)",
                             f"Bills pay {y3m * 100:.2f}% vs {y10 * 100:.2f}% at 10y — but bills carry reinvestment risk if the Fed "
                             "cuts. A ladder or barbell keeps some lock-in."))
        elif y3m and y10 and (y10 - y3m) > 0.01:
            recs.append(_rec("curve-steep", "info", "market", f"Curve is upward-sloping (+{(y10 - y3m) * 1e4:.0f}bp 3m→10y)",
                             "Extending maturity is paid: longer rungs yield more, and bonds 'roll down' the curve as they age."))

    sev = {"high": 0, "medium": 1, "low": 2, "info": 3}
    recs.sort(key=lambda r: (sev.get(r["severity"], 9), -(r["impact_usd"] or 0)))
    return recs


# ---------------------------------------------------------------------------
# Top-level
# ---------------------------------------------------------------------------
def analyze_rows(holdings: list[dict], profile: dict, ctx: Ctx) -> tuple[list[dict], dict]:
    rates = tax_rates(profile)
    rows, internals = [], {}
    for i, h in enumerate(holdings):
        h = {**h, "id": h.get("id") if h.get("id") is not None else i + 1}
        row, it = analyze_holding(h, profile, rates, ctx)
        rows.append(row)
        internals[row["id"]] = it
    return rows, internals


def _analyze_sync(holdings: list[dict], profile: dict, ctx: Ctx, cash_years: int, fund_years: int,
                  assume_calls: bool) -> dict:
    rates = tax_rates(profile)
    rows, internals = analyze_rows(holdings, profile, ctx)
    agg = aggregate(rows, internals)
    scen = scenarios(rows, internals, ctx.settle, agg["key_rate_dv01"])
    sched = tax_schedule(profile)
    cash = project_cash_flows(rows, internals, ctx.settle, years=cash_years, fund_years=fund_years,
                              inflation=ctx.inflation, assume_calls=assume_calls, tax=sched)
    tax = tax_projection(rows, internals, cash, sched, ctx.settle, ctx.inflation)
    recs = recommendations(rows, agg, profile, ctx, rates)
    return {
        "as_of": ctx.settle.isoformat(), "fedinvest_as_of": ctx.catalogue_as_of,
        **inflation_fields(ctx.inflation, inflation_assumption(profile, ctx.rate_context)[1]),
        "summary": agg["summary"], "allocation": agg["allocation"], "key_rate_dv01": agg["key_rate_dv01"],
        "scenarios": scen, "holdings": [r for r in rows if r["status"] != "watch"],
        "watchlist": [r for r in rows if r["status"] == "watch"],
        "cash_flow": {k: v for k, v in cash.items() if k != "events"}, "tax": tax, "recommendations": recs,
        "profile": {**default_profile(), **(profile or {})},
    }


async def analyze_portfolio(holdings: list[dict], profile: dict, *, settle: date | None = None,
                            cash_years: int = 30, fund_years: int = 10, assume_calls: bool = False) -> dict:
    ctx = await load_context(holdings, profile, settle)
    return await asyncio.to_thread(_analyze_sync, holdings, profile, ctx, cash_years, fund_years, assume_calls)


async def cash_flow_projection(holdings: list[dict], profile: dict, *, years: int = 30, fund_years: int = 10,
                               assume_calls: bool = False, include_watch: bool = False) -> dict:
    ctx = await load_context(holdings, profile)

    def _run():
        rows, internals = analyze_rows(holdings, profile, ctx)
        return project_cash_flows(rows, internals, ctx.settle, years=years, fund_years=fund_years,
                                  inflation=ctx.inflation, assume_calls=assume_calls, include_watch=include_watch,
                                  tax=tax_schedule(profile))
    return await asyncio.to_thread(_run)


# ---------------------------------------------------------------------------
# Bond calculator (one bond, any terms) — same engine as holdings
# ---------------------------------------------------------------------------
async def calculate_bond(terms: dict, profile: dict) -> dict:
    """Price ⇄ yield, risk, after-tax, scenarios and the cash-flow schedule for arbitrary terms.

    ``terms``: kind, coupon_rate (%), coupon_freq, maturity_date, [issue_date, day_count,
    call_date, call_price, settle, face_value, account_type, state, tips_ref_cpi] and either
    ``price`` (clean per 100) or ``yield_pct``.
    """
    settle = _date(terms.get("settle")) or mkt.us_today()
    h = {**terms, "id": 1, "status": "held", "face_value": _num(terms.get("face_value")) or 10000.0}
    spec = bond_spec(h)
    if spec is None:
        return {"error": "Maturity date is required."}
    if spec.maturity <= settle:
        return {"error": "The bond has already matured."}
    price, ytm_in = _num(terms.get("price")), _num(terms.get("yield_pct"))
    if price is None and ytm_in is None:
        return {"error": "Enter a price or a yield."}
    a = bm.analytics(spec, settle, clean=price) if price is not None else bm.analytics(spec, settle, y=ytm_in / 100.0)
    if not a or a.get("ytw") is None:
        return {"error": "Could not solve — check the price/terms."}
    ctx = await load_context([h], profile, settle)
    rates = tax_rates(profile)
    tr, notes = tax_treatment(h, profile)
    wk = next((w for w in bm.workouts(spec, settle) if w.date == a["ytw_date"]),
              bm.Workout(spec.maturity, spec.redemption, "maturity"))
    at = bm.after_tax_yield(spec, settle, a["clean"], rates, tr, workout=wk, inflation=ctx.inflation)
    face = h["face_value"]
    per = face / 100.0
    npts = (ctx.mi.get("nominal") or {}).get("points") or []
    years = bm.year_frac(settle, spec.maturity)
    tsy = bm.interp(npts, years)
    shocks = []
    for bp in (-200, -100, -50, -25, 25, 50, 100, 200):
        p1 = bm.scenario_price(spec, settle, a["ytw"], bp * bm.BP)
        shocks.append({"shift_bp": bp, "price": round(p1 - a["accrued"], 4), "pnl": round((p1 - a["dirty"]) * per, 2),
                       "pnl_pct": round(100 * (p1 / a["dirty"] - 1), 3),
                       "duration_estimate_pct": round(-100 * (a["eff_duration"] or 0) * bp * bm.BP
                                                      + 50 * (a["eff_convexity"] or 0) * (bp * bm.BP) ** 2, 3)})
    horizon = []
    for bp in (-100, 0, 100):
        hr = bm.horizon_return(spec, settle, a["ytw"], 1.0, shift=bp * bm.BP)
        if hr:
            horizon.append({"shift_bp": bp, "total_return_pct": round(hr["total_return"] * 100, 3)})
    flows = [{"date": d.isoformat(), "coupon": round((amt - (wk.price if i == len(fl) - 1 else 0)) * per, 2),
              "principal": round((wk.price if i == len(fl) - 1 else 0) * per, 2)}
             for fl in [bm.cash_flows(spec, settle, wk)] for i, (d, amt, _) in enumerate(fl)]
    krd = bm.key_rate_durations(spec, settle, a["ytw"], a["eff_duration"] or 0.0, wk)
    dm = bm.de_minimis_price(settle, spec.maturity)
    return {
        "settle": settle.isoformat(), "face_value": face,
        "clean_price": round(a["clean"], 6), "dirty_price": round(a["dirty"], 6), "accrued": round(a["accrued"], 6),
        "accrued_usd": round(a["accrued"] * per, 2), "cost_usd": round(a["dirty"] * per, 2),
        "ytm_pct": _pct(a["ytm"], 4), "ytw_pct": _pct(a["ytw"], 4), "ytw_date": a["ytw_date"].isoformat(),
        "ytw_kind": a["ytw_kind"], "ytc_pct": _pct(a["ytc"], 4), "current_yield_pct": _pct(a["current_yield"], 4),
        "mac_duration": _r(a["mac_duration"], 4), "mod_duration": _r(a["mod_duration"], 4),
        "eff_duration": _r(a["eff_duration"], 4), "convexity": _r(a["convexity"], 3), "eff_convexity": _r(a["eff_convexity"], 3),
        "dv01_per_100": round(a["dv01"], 5), "dv01_usd": round(a["dv01"] * per, 2),
        "years_to_maturity": round(years, 3), "callable": a["callable"], "likely_called": a["likely_called"],
        "spread_to_treasury_bp": round((a["ytw"] - tsy) * 1e4) if (tsy is not None and h.get("kind") not in GOVERNMENT) else None,
        "treasury_at_maturity_pct": _pct(tsy),
        "de_minimis_price": round(dm, 3) if h.get("kind") == "muni" else None,
        "tax": _tax_block(tr, bm.interest_tax_rate(rates, tr), (at or {}).get("after_tax"), rates,
                          notes + list((at or {}).get("notes") or []), (at or {}).get("after_tax_real")),
        "scenarios": shocks, "horizon_1y": horizon,
        "krd": [{"tenor": k, "duration": round(v, 4)} for k, v in krd.items() if abs(v) > 1e-6],
        "cash_flows": flows, "annual_income_usd": round(face * spec.coupon, 2),
        **inflation_fields(ctx.inflation),
    }
