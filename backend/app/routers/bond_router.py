"""Bond Desk router — ``/api/bonds``.

Everything fixed income: the user's bond book (individual Treasuries / TIPS / munis /
corporates / agencies / CDs + bond ETFs and mutual funds), analytics (marks, yields,
duration, DV01, key-rate risk, scenarios), cash-flow and tax projections, deterministic
recommendations, ladders (nominal / best-after-tax / defined-maturity ETF / TIPS) and
goal funding, plus live market data (curves, TIPS catalogue, spreads, auctions) and a
bond calculator.

Market payloads are shared across users and DB-cached briefly (keys versioned ``:v1`` —
bump on shape change); per-user analytics are computed on request from memoised market
inputs (pure-python math — light on the 512 MiB instance). The optional AI advisor runs
only on explicit request with the user's own key and is grounded on the computed payload.
"""
from __future__ import annotations

import datetime as dt
import hashlib
import json
import logging

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..auth import get_current_user, get_user_api_key
from ..database import get_db
from ..models import BondHolding, BondLadder, BondProfile, User
from ..services import bond_buy_service as buy_svc
from ..services import bond_gap_service as gap_svc
from ..services import bond_rebalance_service as rebal_svc
from ..services import bond_ladder_service as ladders
from ..services import bond_market_service as mkt
from ..services import bond_portfolio_service as ps
from ..services.cache_service import get_cached, set_cached
from ..services.llm_service import call_llm

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/bonds", tags=["bonds"])

KINDS = {"treasury", "tips", "muni", "corporate", "agency", "cd", "etf", "mutual_fund"}
STATUSES = {"held", "watch", "matured", "sold"}
ACCOUNTS = {"taxable", "ira", "roth", "401k", "403b", "hsa", "529"}

_TTL_MARKET = 1800
_TTL_CATALOGUE = 3 * 3600


# ---------------------------------------------------------------------------
# Pydantic I/O
# ---------------------------------------------------------------------------
class HoldingIn(BaseModel):
    kind: str
    status: str = "held"
    label: str | None = None
    issuer: str | None = None
    cusip: str | None = None
    ticker: str | None = None
    face_value: float | None = None
    quantity: float | None = None
    coupon_rate: float | None = None
    coupon_freq: int = 2
    day_count: str | None = None
    issue_date: dt.date | None = None
    maturity_date: dt.date | None = None
    purchase_date: dt.date | None = None
    purchase_price: float | None = None
    cost_basis: float | None = None
    current_price: float | None = None
    price_as_of: dt.date | None = None
    call_date: dt.date | None = None
    call_price: float | None = None
    rating: str | None = None
    state: str | None = None
    federal_taxable: bool | None = None
    state_taxable: bool | None = None
    amt: bool = False
    tips_ref_cpi: float | None = None
    account_type: str = "taxable"
    account_name: str | None = None
    ladder_id: int | None = None
    notes: str | None = None


class ProfileIn(BaseModel):
    federal_rate: float = 24.0
    state: str | None = None
    state_rate: float = 5.0
    niit: bool = False
    ltcg_rate: float = 15.0
    filing_status: str | None = None
    inflation_assumption: float | None = None
    horizon_years: float | None = None
    goals: list[dict] = Field(default_factory=list)
    settings: dict = Field(default_factory=dict)


class LadderSaveIn(BaseModel):
    name: str = "My ladder"
    ladder_type: str = "nominal"
    params: dict = Field(default_factory=dict)
    plan: dict = Field(default_factory=dict)
    status: str = "plan"
    notes: str | None = None


class AdoptIn(BaseModel):
    status: str = "held"          # held (bought) | watch (plan to buy)
    rung_indexes: list[int] | None = None


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------
def _loads(s, default):
    try:
        return json.loads(s) if s else default
    except Exception:  # noqa: BLE001
        return default


def _holding_dict(h: BondHolding) -> dict:
    out = {c.name: getattr(h, c.name) for c in BondHolding.__table__.columns}
    for k, v in list(out.items()):
        if isinstance(v, (dt.date, dt.datetime)):
            out[k] = v.isoformat()
    out.pop("user_id", None)
    return out


def _profile_dict(p: BondProfile | None) -> dict:
    if p is None:
        return ps.default_profile()
    return {"federal_rate": p.federal_rate, "state": p.state, "state_rate": p.state_rate, "niit": p.niit,
            "ltcg_rate": p.ltcg_rate, "filing_status": p.filing_status, "inflation_assumption": p.inflation_assumption,
            "horizon_years": p.horizon_years, "goals": _loads(p.goals, []), "settings": _loads(p.settings, {})}


def _ladder_dict(lad: BondLadder, with_plan: bool = True) -> dict:
    out = {"id": lad.id, "name": lad.name, "ladder_type": lad.ladder_type, "status": lad.status, "notes": lad.notes,
           "params": _loads(lad.params, {}),
           "created_at": lad.created_at.isoformat() if lad.created_at else None,
           "updated_at": lad.updated_at.isoformat() if lad.updated_at else None}
    plan = _loads(lad.plan, {})
    out["summary"] = plan.get("summary")
    if with_plan:
        out["plan"] = plan
    return out


def _validate(body: HoldingIn) -> dict:
    d = body.model_dump()
    d["kind"] = (d["kind"] or "").lower()
    if d["kind"] not in KINDS:
        raise HTTPException(400, f"kind must be one of {sorted(KINDS)}")
    d["status"] = (d["status"] or "held").lower()
    if d["status"] not in STATUSES:
        raise HTTPException(400, f"status must be one of {sorted(STATUSES)}")
    d["account_type"] = (d["account_type"] or "taxable").lower()
    if d["account_type"] not in ACCOUNTS:
        raise HTTPException(400, f"account_type must be one of {sorted(ACCOUNTS)}")
    if d["kind"] in ("etf", "mutual_fund"):
        if not d.get("ticker"):
            raise HTTPException(400, "Ticker is required for bond ETFs and mutual funds.")
        d["ticker"] = d["ticker"].upper().strip()
        if not d.get("quantity"):
            raise HTTPException(400, "Number of shares is required.")
        # funds: coupon_freq = how it pays (0 accumulates · 1 auto · 12 distributes), coupon_rate = your yield %
        if d.get("coupon_freq") not in (0, 1, 12):
            d["coupon_freq"] = 1
        if d.get("coupon_rate") is not None and not (-5.0 <= d["coupon_rate"] <= 30.0):
            raise HTTPException(400, "Fund yield must be a percent between -5 and 30.")
    else:
        if not d.get("maturity_date"):
            raise HTTPException(400, "Maturity date is required.")
        if not d.get("face_value") or d["face_value"] <= 0:
            raise HTTPException(400, "Face (par) amount is required.")
        if d.get("coupon_freq") not in (0, 1, 2, 4, 12):
            raise HTTPException(400, "coupon_freq must be 0 (zero / at maturity), 1, 2, 4 or 12.")
    if d.get("cusip"):
        d["cusip"] = d["cusip"].upper().strip()
    if d.get("state"):
        d["state"] = d["state"].upper().strip()[:2]
    return d


async def _load_holdings(db: AsyncSession, user_id: int) -> list[dict]:
    rows = (await db.execute(select(BondHolding).where(BondHolding.user_id == user_id)
                             .order_by(BondHolding.maturity_date.asc().nulls_last(), BondHolding.id))).scalars().all()
    return [_holding_dict(h) for h in rows]


def filter_holdings(holdings: list[dict], kind: list[str] | None = None,
                    account_type: list[str] | None = None) -> list[dict]:
    """Holdings matching every given filter (a missing/empty filter = all)."""
    # handlers are also called directly (tests): an unset Query(...) default is not a list → no filter
    as_list = lambda v: v if isinstance(v, (list, tuple)) else []  # noqa: E731
    kinds = {k.lower() for k in as_list(kind) if k}
    accts = {a.lower() for a in as_list(account_type) if a}

    def ok(h: dict) -> bool:
        if kinds and (h.get("kind") or "").lower() not in kinds:
            return False
        if accts and (h.get("account_type") or "taxable").lower() not in accts:
            return False
        return True
    return [h for h in holdings if ok(h)]


async def _load_profile(db: AsyncSession, user_id: int) -> dict:
    p = (await db.execute(select(BondProfile).where(BondProfile.user_id == user_id))).scalar_one_or_none()
    return _profile_dict(p)


async def _cached(db: AsyncSession, key: str, ttl: int, producer):
    hit = await get_cached(db, key)
    if hit is not None:
        return hit
    val = await producer()
    if val:
        await set_cached(db, key, val, ttl_seconds=ttl)
    return val


# ---------------------------------------------------------------------------
# Market data
# ---------------------------------------------------------------------------
@router.get("/market")
async def market(user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    """Curves (now / 1m / 1y), real curve, breakevens, spreads, deposit rates, yield menu, auctions."""
    return await _cached(db, "bonds:market:v1", _TTL_MARKET, mkt.market_snapshot)


@router.get("/treasuries")
async def treasuries(user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    """Every marketable Treasury with its FedInvest EOD price and computed yield."""
    out = await _cached(db, "bonds:treasuries:v1", _TTL_CATALOGUE, mkt.treasury_catalogue)
    if not out:
        raise HTTPException(503, "Treasury prices unavailable right now (TreasuryDirect).")
    return out


@router.get("/tips")
async def tips(user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    """TIPS catalogue: real yields, index ratios, breakevens, maturity gaps."""
    out = await _cached(db, "bonds:tips:v1", _TTL_CATALOGUE, mkt.tips_catalogue)
    if not out:
        raise HTTPException(503, "TIPS data unavailable right now (TreasuryDirect).")
    return out


@router.get("/lookup/{query}")
async def lookup(query: str, user: User = Depends(get_current_user)):
    """Auto-fill: a 9-char CUSIP → bond terms (Treasury catalogue, else OpenFIGI); else a fund ticker."""
    q = query.strip().upper()
    if mkt.looks_like_cusip(q):
        res = await mkt.lookup_cusip(q)
        if not res:
            raise HTTPException(404, "CUSIP not found — enter the terms manually.")
        return {"type": "bond", **res}
    fp = await mkt.fund_profile_full(q)
    if not fp:
        raise HTTPException(404, f"No fund found for {q}.")
    kind = "mutual_fund" if (fp.get("quote_type") or "").upper() == "MUTUALFUND" else "etf"
    return {"type": "fund", "kind": kind, **fp}


@router.get("/fund/{ticker}")
async def fund(ticker: str, user: User = Depends(get_current_user)):
    fp = await mkt.fund_profile_full(ticker)
    if not fp:
        raise HTTPException(404, f"No fund data for {ticker.upper()}.")
    return fp


@router.get("/etf-rungs/{family}")
async def etf_rungs(family: str, user: User = Depends(get_current_user)):
    if family not in mkt.DEFINED_MATURITY:
        raise HTTPException(400, f"family must be one of {sorted(mkt.DEFINED_MATURITY)}")
    return {"family": family, "rungs": await mkt.etf_rungs(family)}


class YieldMenuIn(BaseModel):
    tenors: list[float] = Field(default_factory=lambda: [0.5, 1, 2, 3, 5, 7, 10, 20, 30])
    account_type: str = "taxable"


class BuyPlanIn(BaseModel):
    amount: float = 50000.0
    account_type: str = "taxable"
    preset: str = "balanced"                  # safety | balanced | income
    target_duration: float | None = None      # None = keep the book's current duration
    duration_tolerance: float | None = None
    min_credit: str | None = None             # govt | AA | A | BBB
    tips_min_pct: float | None = None
    corp_max_pct: float | None = None
    bbb_max_pct: float | None = None
    max_line_pct: float | None = None
    max_years: float | None = None
    kinds: list[str] | None = None


@router.post("/buy-plan")
async def buy_plan(body: BuyPlanIn, user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    """What to buy next: allocate new money across Treasuries/TIPS/CDs/agencies/munis/corporates so the WHOLE book
    hits your duration, credit, inflation and tax targets at the best after-tax yield (one LP)."""
    if body.amount < 0 or body.amount > 1e9:
        raise HTTPException(400, "Amount must be between 0 and 1,000,000,000.")
    if body.account_type.lower() not in ACCOUNTS:
        raise HTTPException(400, f"account_type must be one of {sorted(ACCOUNTS)}")
    holdings = await _load_holdings(db, user.id)
    profile = await _load_profile(db, user.id)
    try:
        return await buy_svc.buy_plan(holdings, profile, body.model_dump())
    except Exception as exc:  # noqa: BLE001
        logger.exception("bond buy plan failed")
        raise HTTPException(502, f"Could not build a buy plan: {exc}")


class GapPlanIn(BaseModel):
    budget: float | None = None               # None = whatever it takes to close every gap
    account_type: str = "taxable"
    min_credit: str = "AA"                    # govt | AA | A | BBB
    low_inflation: float = -1.0               # % a year in the low world (negative = deflation)
    high_inflation: float = 5.0
    debase_inflation: float = 6.0
    after_tax: bool = True
    use_funds: bool = True
    reinvest: bool = True
    withdrawal_mode: str | None = None


@router.post("/gap-plan")
async def gap_plan(body: GapPlanIn, user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    """Purchases sized to the plan's shortfall years (TIPS vs nominal, best after-tax instrument per year), each
    strategy re-run through the planner under low / expected / high inflation and dollar debasement."""
    if body.account_type.lower() not in ACCOUNTS:
        raise HTTPException(400, f"account_type must be one of {sorted(ACCOUNTS)}")
    for v in (body.low_inflation, body.high_inflation, body.debase_inflation):
        if not (-2.0 <= v <= 25.0):
            raise HTTPException(400, "Scenario inflation must be between -2% and 25%.")
    if body.withdrawal_mode not in (None, "age", "before", "after"):
        raise HTTPException(400, "withdrawal_mode must be age, before or after.")
    holdings = await _load_holdings(db, user.id)
    profile = await _load_profile(db, user.id)
    # ~20 full plan runs → cache per (book, profile, inputs, day): any edit to a holding, goal or setting misses
    sig = hashlib.md5(json.dumps([holdings, profile, body.model_dump(), mkt.us_today().isoformat()],
                                 sort_keys=True, default=str).encode()).hexdigest()
    key = f"bonds:gap:v2:{user.id}:{sig}"
    hit = await get_cached(db, key)
    if hit is not None:
        return hit
    try:
        out = await gap_svc.gap_plan(holdings, profile, body.model_dump())
    except Exception as exc:  # noqa: BLE001
        logger.exception("bond gap plan failed")
        raise HTTPException(502, f"Could not build the gap plan: {exc}")
    await set_cached(db, key, out, ttl_seconds=1800)
    return out


class RebalanceIn(BaseModel):
    new_money: float = 0.0
    new_money_account: str = "taxable"
    max_turnover_pct: float = 25.0            # at most this share of the book may be sold
    robustness: str = "both"                  # expected | both (deflation + high) | high | all — worlds it must hold up in
    tips_max_pct: float = 50.0                # at most this share of what's BOUGHT is inflation-linked
    type_max_pct: float = 35.0                # … and this share in any one of CDs / agencies / munis / corporates
    intl_pct: float = 0.0                     # optional non-dollar sleeve, share of the book
    bond_turnover_pct: float = 25.0           # funds first: individual bonds may use this share of the change limit
    min_credit: str = "AA"
    corp_max_pct: float = 25.0
    stickiness_pct: float = 0.5               # "leave it alone" charge on everything sold
    keep_ids: list[int] = Field(default_factory=list)   # holdings you won't sell
    low_inflation: float = -1.0
    high_inflation: float = 5.0
    debase_inflation: float = 6.0
    after_tax: bool = True
    withdrawal_mode: str | None = None


@router.post("/rebalance")
async def rebalance(body: RebalanceIn, user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    """Balance the whole book: keep / sell / swap every holding within its account (+ new money) so the plan is
    funded at the best after-tax outcome with limited turnover — one LP, then verified by the planner."""
    if body.new_money < 0 or body.new_money > 1e9:
        raise HTTPException(400, "New money must be between 0 and 1,000,000,000.")
    if body.new_money_account.lower() not in ACCOUNTS:
        raise HTTPException(400, f"new_money_account must be one of {sorted(ACCOUNTS)}")
    if body.robustness not in ("expected", "both", "high", "all"):
        raise HTTPException(400, "robustness must be expected, both, high or all.")
    if not (0 <= body.tips_max_pct <= 100 and 0 <= body.type_max_pct <= 100 and 0 <= body.intl_pct <= 30):
        raise HTTPException(400, "tips_max_pct / type_max_pct must be 0–100 and intl_pct 0–30.")
    if not (0 <= body.max_turnover_pct <= 100):
        raise HTTPException(400, "max_turnover_pct must be between 0 and 100.")
    holdings = await _load_holdings(db, user.id)
    profile = await _load_profile(db, user.id)
    sig = hashlib.md5(json.dumps([holdings, profile, body.model_dump(), mkt.us_today().isoformat()],
                                 sort_keys=True, default=str).encode()).hexdigest()
    key = f"bonds:rebal:v2:{user.id}:{sig}"
    hit = await get_cached(db, key)
    if hit is not None:
        return hit
    try:
        out = await rebal_svc.rebalance(holdings, profile, body.model_dump())
    except Exception as exc:  # noqa: BLE001
        logger.exception("bond rebalance failed")
        raise HTTPException(502, f"Could not rebalance the book: {exc}")
    await set_cached(db, key, out, ttl_seconds=1800)
    return out


@router.post("/yield-menu")
async def yield_menu(body: YieldMenuIn, user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    """Per tenor, every instrument's pre-tax / after-tax / TEY for THIS user's tax profile."""
    profile = await _load_profile(db, user.id)
    mi = await mkt.market_inputs()
    rc = await mkt.rate_context()
    infl, _ = ps.inflation_assumption(profile, rc)
    return {"account_type": body.account_type, **ps.inflation_fields(infl),
            "rows": ps.after_tax_menu(mi, profile, body.tenors[:15], account=body.account_type.lower(), inflation=infl)}


@router.post("/calc")
async def calc(terms: dict, user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    """Bond calculator: price ⇄ yield, YTW, duration, convexity, DV01, after-tax, scenarios, cash flows."""
    profile = await _load_profile(db, user.id)
    try:
        out = await ps.calculate_bond(terms, profile)
    except Exception as exc:  # noqa: BLE001
        logger.exception("bond calc failed")
        raise HTTPException(400, f"Could not calculate: {exc}")
    if out.get("error"):
        raise HTTPException(400, out["error"])
    return out


# ---------------------------------------------------------------------------
# Profile
# ---------------------------------------------------------------------------
@router.get("/profile")
async def get_profile(user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    return await _load_profile(db, user.id)


@router.put("/profile")
async def put_profile(body: ProfileIn, user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    p = (await db.execute(select(BondProfile).where(BondProfile.user_id == user.id))).scalar_one_or_none()
    if p is None:
        p = BondProfile(user_id=user.id)
        db.add(p)
    d = body.model_dump()
    for k in ("federal_rate", "state_rate", "niit", "ltcg_rate", "filing_status", "inflation_assumption", "horizon_years"):
        setattr(p, k, d[k])
    p.state = (d["state"] or "").upper()[:2] or None
    p.goals = json.dumps(d["goals"][:50])
    # Settings are MERGED key by key (null clears a key): several screens each own a few keys (planner: birth year,
    # withdrawal mode, Social Security, income sources; tax profile: inflation, retirement rates) and may hold a
    # stale copy of the rest — a save from one must never wipe what another saved.
    merged = {**_loads(p.settings, {}), **(d["settings"] or {})}
    p.settings = json.dumps({k: v for k, v in merged.items() if v is not None})
    await db.commit()
    return _profile_dict(p)


# ---------------------------------------------------------------------------
# Holdings CRUD + analytics
# ---------------------------------------------------------------------------
@router.get("/holdings")
async def list_holdings(user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    return await _load_holdings(db, user.id)


async def _own_holding(db: AsyncSession, user_id: int, hid: int) -> BondHolding:
    h = (await db.execute(select(BondHolding).where(BondHolding.id == hid, BondHolding.user_id == user_id))).scalar_one_or_none()
    if h is None:
        raise HTTPException(404, "Holding not found")
    return h


async def _check_ladder(db: AsyncSession, user_id: int, ladder_id: int | None) -> None:
    if ladder_id is None:
        return
    lad = (await db.execute(select(BondLadder).where(BondLadder.id == ladder_id, BondLadder.user_id == user_id))).scalar_one_or_none()
    if lad is None:
        raise HTTPException(400, "Ladder not found")


def _coerce_dates(d: dict) -> dict:
    for k in ("issue_date", "maturity_date", "purchase_date", "price_as_of", "call_date"):
        if isinstance(d.get(k), str):
            d[k] = dt.date.fromisoformat(d[k][:10]) if d[k] else None
    return d


@router.post("/holdings")
async def create_holding(body: HoldingIn, user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    d = _validate(body)
    await _check_ladder(db, user.id, d.get("ladder_id"))
    h = BondHolding(user_id=user.id, **d)
    db.add(h)
    await db.commit()
    await db.refresh(h)
    return _holding_dict(h)


class PreviewIn(BaseModel):
    holding: dict
    broker_value: float | None = None    # the "Current value" on the user's statement (excl. accrued)


@router.post("/holdings/preview")
async def preview_holding(body: PreviewIn, user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    """Value a DRAFT holding exactly as the book will (nothing saved) and reconcile it with the broker.

    Brokers report bonds as Quantity (face/par; TIPS = original face) + cost-basis total + current value
    (clean, excluding accrued). We compare our clean value to theirs and back out the price per 100 their
    value implies — so the user can check the mapping before saving.
    """
    try:
        d = _validate(HoldingIn(**body.holding))
    except HTTPException as exc:
        return {"ready": False, "missing": exc.detail}
    except Exception as exc:  # noqa: BLE001 — pydantic errors on half-typed fields
        return {"ready": False, "missing": str(exc).splitlines()[0]}
    d.update(status="held", id=0)
    profile = await _load_profile(db, user.id)
    try:
        out = await ps.analyze_portfolio([d], profile)
    except Exception as exc:  # noqa: BLE001
        logger.exception("bond preview failed")
        return {"ready": False, "missing": f"Could not value it: {exc}"}
    row = (out.get("holdings") or [None])[0]
    rec = None
    bv = body.broker_value
    if row and bv:
        ours = row.get("clean_value") if row.get("clean_value") is not None else row.get("market_value")
        ir = ((row.get("tips") or {}).get("index_ratio")) or 1.0
        face = row.get("face")
        rec = {
            "broker_value": round(bv, 2), "our_value": round(ours or 0.0, 2),
            "diff": round((ours or 0.0) - bv, 2), "diff_pct": round(100 * ((ours or 0.0) - bv) / bv, 3),
            # the price per 100 of face the broker's value implies (REAL price for TIPS)
            "implied_price": round(bv / (face * ir) * 100.0, 4) if face else None,
            "basis": "clean value (excludes accrued interest, like broker statements)",
        }
    return {"ready": True, "row": row, "reconciliation": rec}


@router.put("/holdings/{hid}")
async def update_holding(hid: int, body: HoldingIn, user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    h = await _own_holding(db, user.id, hid)
    d = _validate(body)
    await _check_ladder(db, user.id, d.get("ladder_id"))
    for k, v in d.items():
        setattr(h, k, v)
    await db.commit()
    await db.refresh(h)
    return _holding_dict(h)


class StatusIn(BaseModel):
    status: str


@router.patch("/holdings/{hid}/status")
async def set_status(hid: int, body: StatusIn, user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    if body.status not in STATUSES:
        raise HTTPException(400, f"status must be one of {sorted(STATUSES)}")
    h = await _own_holding(db, user.id, hid)
    h.status = body.status
    await db.commit()
    return {"ok": True, "id": hid, "status": body.status}


@router.delete("/holdings/{hid}")
async def delete_holding(hid: int, user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    h = await _own_holding(db, user.id, hid)
    await db.delete(h)
    await db.commit()
    return {"ok": True}


@router.get("/portfolio")
async def portfolio(assume_calls: bool = False, kind: list[str] | None = Query(None),
                    account_type: list[str] | None = Query(None),
                    user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    """Full book analytics: summary, holdings, allocation, key-rate DV01, scenarios, cash flow, tax, recommendations."""
    holdings = filter_holdings(await _load_holdings(db, user.id), kind, account_type)
    profile = await _load_profile(db, user.id)
    try:
        return await ps.analyze_portfolio(holdings, profile, assume_calls=assume_calls)
    except Exception as exc:  # noqa: BLE001
        logger.exception("bond portfolio failed")
        raise HTTPException(502, f"Could not analyze the bond book: {exc}")


@router.get("/cashflow")
async def cashflow(years: int = Query(30, ge=1, le=40), fund_years: int = Query(10, ge=1, le=40),
                   assume_calls: bool = False, include_watch: bool = False,
                   kind: list[str] | None = Query(None), account_type: list[str] | None = Query(None),
                   user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    """Cash-flow projection for the whole book — or the holdings matching the filters."""
    holdings = filter_holdings(await _load_holdings(db, user.id), kind, account_type)
    profile = await _load_profile(db, user.id)
    return await ps.cash_flow_projection(holdings, profile, years=years, fund_years=fund_years,
                                         assume_calls=assume_calls, include_watch=include_watch)


# ---------------------------------------------------------------------------
# Ladders
# ---------------------------------------------------------------------------
@router.post("/ladders/build")
async def build(params: dict, user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    """Preview a nominal / best-after-tax / ETF ladder (nothing is saved)."""
    profile = await _load_profile(db, user.id)
    out = await ladders.build_ladder(params, profile)
    if out.get("error"):
        raise HTTPException(400, out["error"])
    return out


@router.post("/tips-ladder")
async def tips_ladder(params: dict, user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    """Preview a TIPS real-income ladder from the live TIPS catalogue."""
    profile = await _load_profile(db, user.id)
    out = await ladders.build_tips_ladder(params, profile)
    if out.get("error"):
        raise HTTPException(400, out["error"])
    return out


class SimulateIn(BaseModel):
    ladder: dict
    years: int = 15


@router.post("/ladders/simulate")
async def simulate(body: SimulateIn, user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    """Rolling-ladder income under flat / ±100bp / market-forward curves."""
    profile = await _load_profile(db, user.id)
    return await ladders.simulate_ladder(body.ladder, profile, years=max(1, min(body.years, 30)))


@router.get("/ladders")
async def list_ladders(user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    rows = (await db.execute(select(BondLadder).where(BondLadder.user_id == user.id)
                             .order_by(BondLadder.created_at.desc()))).scalars().all()
    return [_ladder_dict(r, with_plan=False) for r in rows]


@router.post("/ladders")
async def save_ladder(body: LadderSaveIn, user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    lad = BondLadder(user_id=user.id, name=body.name[:120] or "My ladder", ladder_type=body.ladder_type,
                     params=json.dumps(body.params), plan=json.dumps(body.plan), status=body.status, notes=body.notes)
    db.add(lad)
    await db.commit()
    await db.refresh(lad)
    return _ladder_dict(lad)


async def _own_ladder(db: AsyncSession, user_id: int, lid: int) -> BondLadder:
    lad = (await db.execute(select(BondLadder).where(BondLadder.id == lid, BondLadder.user_id == user_id))).scalar_one_or_none()
    if lad is None:
        raise HTTPException(404, "Ladder not found")
    return lad


@router.get("/ladders/{lid}")
async def get_ladder(lid: int, user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    """Saved ladder + live status of each rung vs the holdings linked to it (+ their analytics)."""
    lad = await _own_ladder(db, user.id, lid)
    out = _ladder_dict(lad)
    holdings = [h for h in await _load_holdings(db, user.id) if h.get("ladder_id") == lid]
    profile = await _load_profile(db, user.id)
    actual = None
    if holdings:
        try:
            actual = await ps.analyze_portfolio(holdings, profile)
        except Exception:  # noqa: BLE001
            logger.exception("ladder analytics failed")
    rows = (actual or {}).get("holdings", []) + (actual or {}).get("watchlist", [])
    out["status_detail"] = ladders.ladder_status(out["plan"], rows)
    out["actual"] = {k: (actual or {}).get(k) for k in ("summary", "cash_flow", "recommendations")} if actual else None
    out["linked_holdings"] = rows
    return out


@router.put("/ladders/{lid}")
async def update_ladder(lid: int, body: LadderSaveIn, user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    lad = await _own_ladder(db, user.id, lid)
    lad.name = body.name[:120] or lad.name
    lad.status = body.status
    lad.notes = body.notes
    if body.plan:
        lad.plan = json.dumps(body.plan)
        lad.params = json.dumps(body.params)
    await db.commit()
    await db.refresh(lad)
    return _ladder_dict(lad)


@router.delete("/ladders/{lid}")
async def delete_ladder(lid: int, delete_planned: bool = False, user: User = Depends(get_current_user),
                        db: AsyncSession = Depends(get_db)):
    """Delete a ladder. Linked holdings are unlinked (kept); ``delete_planned`` also removes its watchlist rungs."""
    lad = await _own_ladder(db, user.id, lid)
    linked = (await db.execute(select(BondHolding).where(BondHolding.user_id == user.id,
                                                         BondHolding.ladder_id == lid))).scalars().all()
    removed = 0
    for h in linked:
        if delete_planned and h.status == "watch":
            await db.delete(h)
            removed += 1
        else:
            h.ladder_id = None   # explicit: SQLite dev DBs don't enforce ON DELETE SET NULL
    await db.delete(lad)
    await db.commit()
    return {"ok": True, "unlinked": len(linked) - removed, "deleted_planned": removed}


@router.post("/ladders/{lid}/adopt")
async def adopt_ladder(lid: int, body: AdoptIn, user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    """Turn a saved plan's rungs into holdings linked to the ladder (``held`` = bought, ``watch`` = to buy)."""
    lad = await _own_ladder(db, user.id, lid)
    plan = _loads(lad.plan, {})
    status = body.status if body.status in ("held", "watch") else "held"
    created = []
    for r in plan.get("rungs") or []:
        if body.rung_indexes and r.get("index") not in body.rung_indexes and r.get("year") not in body.rung_indexes:
            continue
        hd = dict(r.get("holding") or {})
        if not hd:
            continue
        hd = {k: v for k, v in hd.items() if not k.startswith("_") and k in BondHolding.__table__.columns.keys()}
        hd.pop("id", None)
        hd["status"] = status
        hd["ladder_id"] = lad.id
        h = BondHolding(user_id=user.id, **_coerce_dates(hd))
        db.add(h)
        created.append(h)
    lad.status = "active"
    await db.commit()
    return {"ok": True, "created": len(created), "ladder_id": lad.id}


# ---------------------------------------------------------------------------
# Planning
# ---------------------------------------------------------------------------
@router.get("/plan")
async def plan(after_tax: bool = True, use_funds: bool = True, reinvest: bool = True,
               withdrawal_mode: str | None = None, kind: list[str] | None = Query(None),
               account_type: list[str] | None = Query(None),
               user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    """Goals vs your bonds' cash flows year by year.

    * ``use_funds`` — sell bond ETFs/mutual funds down to fill shortfall years (which fund, which year, how much);
    * ``reinvest`` — put surplus years' extra cash into Treasuries maturing in later gap years;
    * ``withdrawal_mode`` — retirement-account withdrawals: "age" (profile birth year), "before" / "after" 59½;
    * filters restrict which holdings fund the goals (e.g. only taxable accounts).
    """
    if withdrawal_mode is not None and not isinstance(withdrawal_mode, str):
        withdrawal_mode = None                         # direct calls: unset default
    if withdrawal_mode not in (None, "age", "before", "after"):
        raise HTTPException(400, "withdrawal_mode must be age, before or after")
    holdings = filter_holdings(await _load_holdings(db, user.id), kind, account_type)
    profile = await _load_profile(db, user.id)
    return await ladders.plan_goals(holdings, profile, after_tax=after_tax, use_funds=use_funds,
                                    reinvest=reinvest, withdrawal_mode=withdrawal_mode)


# ---------------------------------------------------------------------------
# AI advisor (explicit, grounded)
# ---------------------------------------------------------------------------
async def _resolve_llm(db: AsyncSession, user: User) -> tuple[str | None, str]:
    try:
        key = await get_user_api_key(db, user.id, "openai_api_key")
    except Exception:  # noqa: BLE001
        key = None
    try:
        model = await get_user_api_key(db, user.id, "openai_model")
    except Exception:  # noqa: BLE001
        model = None
    return key, (model or "gpt-4o")


def advisor_context(book: dict, snap: dict | None) -> dict:
    """The ONLY facts the LLM may use — computed numbers, compacted."""
    hold = [{k: h.get(k) for k in ("label", "kind", "account_type", "market_value", "ytw_pct", "eff_duration",
                                   "maturity", "rating_group", "price_source")}
            | {"after_tax_yield_pct": (h.get("tax") or {}).get("after_tax_yield_pct")} for h in book.get("holdings", [])[:40]]
    return {
        "summary": book.get("summary"),
        "allocation": {k: book.get("allocation", {}).get(k) for k in ("by_kind", "by_credit", "by_maturity", "by_tax")},
        "scenarios": {"parallel": book.get("scenarios", {}).get("parallel"), "twists": book.get("scenarios", {}).get("twists")},
        "recommendations": [{k: r.get(k) for k in ("severity", "title", "detail", "impact_usd")} for r in book.get("recommendations", [])[:12]],
        "cash_flow_next_12m": {k: v for k, v in (book.get("cash_flow", {}).get("next_12m") or {}).items() if k != "events"},
        "holdings": hold,
        "profile": {k: book.get("profile", {}).get(k) for k in ("federal_rate", "state", "state_rate", "niit", "horizon_years")},
        "market": ({"curve": snap.get("nominal_curve"), "real_curve": snap.get("real_curve"),
                    "curve_shape": snap.get("curve_shape"), "breakevens": snap.get("breakevens"),
                    "rate_context": snap.get("rate_context")} if snap else None),
    }


@router.post("/advisor")
async def advisor(user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    """Plain-English review of the bond book — strictly grounded on the computed analytics."""
    key, model = await _resolve_llm(db, user)
    if not key:
        raise HTTPException(400, "Add your OpenAI or Gemini API key in Settings to use the AI advisor.")
    holdings = await _load_holdings(db, user.id)
    if not holdings:
        raise HTTPException(400, "Add some bonds first.")
    profile = await _load_profile(db, user.id)
    book = await ps.analyze_portfolio(holdings, profile)
    snap = await _cached(db, "bonds:market:v1", _TTL_MARKET, mkt.market_snapshot)
    facts = advisor_context(book, snap)
    messages = [
        {"role": "system", "content": (
            "You are a fixed-income portfolio advisor reviewing a client's bond book. Use ONLY the numbers in the JSON "
            "facts provided — never invent yields, prices, ratings, correlations or relationships, and never cite a figure "
            "that is not in the facts. If something is unknown, say so. Be concise and concrete (markdown). Sections: "
            "**The book in one paragraph**, **What's working**, **Top 3 actions** (tie each to a recommendation and its $ "
            "impact when given), **Risks to watch** (rates, credit, reinvestment, call, inflation — use the scenario numbers), "
            "**Questions to consider**. This is education, not individualized investment advice.")},
        {"role": "user", "content": "FACTS:\n" + json.dumps(facts, default=str)[:24000]},
    ]
    try:
        text = await call_llm(key, model, messages, max_tokens=1400, temperature=0.3)
    except Exception as exc:  # noqa: BLE001
        logger.exception("bond advisor failed")
        raise HTTPException(502, f"AI advisor failed: {exc}")
    return {"markdown": text, "model": model}
