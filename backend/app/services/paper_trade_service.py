"""Paper Trader — place an Income-Desk opportunity as a paper trade and re-price it on demand.

The user "places" any scanned/evaluated opportunity in one click; we snapshot the full
opportunity (a ``DeskRankedTrade``, incl. its Quant Analysis) at placement, then on each Refresh
re-price the EXACT legs through the same desk engine and compare placed-vs-now.

Laziness contract: the ONLY heavy path is :func:`recompute` (yfinance + ``rank_desk``). The
list/detail routes read stored/cached columns only — no network, no scan.

P&L is short-premium mark-to-market: you SELL the structure for the entry net credit and would
buy it back at the current net value, so ``unrealized = entry_credit − current_value`` (positive
when the premium has decayed in your favour). Cost basis = the entry net credit received.
"""
from __future__ import annotations

import asyncio
import datetime as dt
import json
import logging
from typing import Optional

from .desk_review_service import reprice_desk_focus, score_desk_management

logger = logging.getLogger(__name__)

CONTRACT_MULTIPLIER = 100   # shares per option contract


def expiry_date(expiration: Optional[str]) -> Optional[dt.date]:
    """Parse the trade's expiration (YYYY-MM-DD…) to a date. None if unparseable."""
    if not expiration:
        return None
    try:
        return dt.date.fromisoformat(str(expiration)[:10])
    except (ValueError, TypeError):
        return None


def dte_remaining(expiration: Optional[str]) -> Optional[int]:
    """Calendar days from today to the expiration (min 1). None if unparseable."""
    e = expiry_date(expiration)
    return None if e is None else max(1, (e - dt.date.today()).days)


def is_expired(pt) -> bool:
    """True once the expiration date is strictly in the past — so the expiry-date CLOSE is final
    (settled), not an intraday print. On the expiry date itself the trade stays open (live MTM)."""
    e = expiry_date(pt.expiration)
    return e is not None and dt.date.today() > e


def _per_share_credit(opp_or_row: dict) -> Optional[float]:
    """Net credit PER SHARE for the structure. Prefers ``premium_per_share``; else backs it out
    of the per-contract ``premium`` (÷100)."""
    pps = opp_or_row.get("premium_per_share")
    if pps is None and opp_or_row.get("premium") is not None:
        try:
            pps = float(opp_or_row["premium"]) / CONTRACT_MULTIPLIER
        except (TypeError, ValueError):
            pps = None
    return None if pps is None else float(pps)


def build_paper_trade_fields(opp: dict, contracts: int = 1, spot: Optional[float] = None) -> dict:
    """Derive the stored columns for a NEW paper trade from the placed opportunity
    (a ``DeskRankedTrade``). Pure — no network. Cost basis = net credit received
    (``entry_premium_per_share × 100 × contracts``)."""
    pps = _per_share_credit(opp)
    entry_credit = (pps * CONTRACT_MULTIPLIER * contracts) if pps is not None else None
    return {
        "structure": opp.get("structure"),
        "expiration": opp.get("expiration"),
        "label": opp.get("label"),
        "short_strike": opp.get("short_strike") or opp.get("put_short") or opp.get("call_short"),
        "contracts": contracts,
        "legs": json.dumps(opp.get("legs") or []),
        "entry_spot": spot,
        "entry_premium_per_share": pps,
        "entry_credit": entry_credit,
        "placed_snapshot": json.dumps(opp),
        "placed_desk_score": opp.get("desk_score"),
        "placed_algo_grade": opp.get("algo_grade"),
    }


def _pnl_block(pt, row: dict, spot: Optional[float]) -> dict:
    """Compute the displayed P&L + the ``pnl_snapshot`` the management overlay consumes, from the
    repriced ``row`` and the trade's stored entry basis. Returns ``(display, snapshot)`` merged
    into one dict; ``snapshot`` keys are consumed by :func:`score_desk_management`."""
    contracts = pt.contracts or 1
    cur_pps = _per_share_credit(row)
    entry_pps = pt.entry_premium_per_share
    entry_credit = pt.entry_credit
    cur_value = (cur_pps * CONTRACT_MULTIPLIER * contracts) if cur_pps is not None else None
    unrealized = (entry_credit - cur_value) if (entry_credit is not None and cur_value is not None) else None
    # captured_pct — PERCENT of max profit banked (0-100; NEGATIVE if the premium moved against
    # you). max_profit == entry_credit for these short-premium structures, so this equals
    # (1 − current/entry) × 100 whether measured in per-share or dollar terms.
    captured = None
    if entry_pps and cur_pps is not None and abs(entry_pps) > 1e-9:
        captured = round((1.0 - (cur_pps / entry_pps)) * 100.0, 1)
    # max_loss must be NEGATIVE for the near-max-loss override (lifecycle_overlay). The opp
    # reports a positive magnitude → negate and scale by contracts.
    ml = row.get("max_loss")
    max_loss = (-abs(float(ml)) * contracts) if ml is not None else None
    unrealized_pct = (round(unrealized / abs(entry_credit) * 100.0, 2)
                      if (unrealized is not None and entry_credit) else None)
    spot_change_pct = (round((spot - pt.entry_spot) / pt.entry_spot * 100.0, 2)
                       if (spot and pt.entry_spot) else None)
    dte = dte_remaining(pt.expiration)
    return {
        # displayed
        "cost_basis": entry_credit,
        "current_value": cur_value,
        "unrealized_pnl": unrealized,
        "unrealized_pct": unrealized_pct,
        "captured_pct": captured,
        "current_spot": spot,
        "entry_spot": pt.entry_spot,
        "spot_change_pct": spot_change_pct,
        "dte_remaining": dte,
        "contracts": contracts,
        "entry_premium_per_share": entry_pps,
        "current_premium_per_share": cur_pps,
        # for the management overlay (score_desk_management → management_desk_score)
        "max_profit": entry_credit,
        "max_loss": max_loss,
    }


async def recompute(pt, user, db, quote_source: str = "yfinance") -> dict:
    """The one heavy path: re-price the exact legs off a fresh chain, compute current P&L, run the
    holder management overlay, and CACHE the result (``last_eval`` + denormalized ``last_*``) so
    the list view stays compute-free. Returns the full result dict + a ``pnl`` block, or
    ``{matched: False, error, pnl: None}`` when the trade can't be priced.

    Commits ``pt`` (cache columns + first-refresh ``entry_spot`` backfill)."""
    legs = json.loads(pt.legs) if pt.legs else []
    dte = dte_remaining(pt.expiration)
    row, desk = await reprice_desk_focus(
        ticker=pt.ticker, structure=pt.structure, expiration=pt.expiration,
        short_strike=pt.short_strike, legs=legs, target_dte=dte,
        user=user, db=db, quote_source=quote_source,
    )
    if row is None:
        # desk is the error dict; leave the cache untouched so the last good read still shows.
        return {**desk, "pnl": None}

    spot = desk.get("spot") or (desk.get("context") or {}).get("spot")
    pnl = _pnl_block(pt, row, spot)
    pnl_snapshot = {
        "analysis": {"dte_remaining": pnl["dte_remaining"], "captured_pct": pnl["captured_pct"]},
        "unrealized_pnl": pnl["unrealized_pnl"],
        "max_profit": pnl["max_profit"],
        "max_loss": pnl["max_loss"],
    }
    result = score_desk_management(row, desk, pnl_snapshot=pnl_snapshot, structure=pt.structure)
    result["pnl"] = pnl

    # Cache — the list route renders these with zero compute.
    pt.last_eval = json.dumps(result, default=str)
    pt.last_eval_at = dt.datetime.now(dt.timezone.utc)
    pt.last_spot = spot
    pt.last_value_per_share = pnl["current_premium_per_share"]
    pt.last_pnl = pnl["unrealized_pnl"]
    pt.last_desk_score = result.get("desk_score")
    pt.last_algo_grade = result.get("algo_grade")
    if pt.entry_spot is None and spot is not None:
        pt.entry_spot = spot   # backfill so the spot-move column works from the next refresh on
    await db.commit()
    await db.refresh(pt)
    return result


# ── Expiry settlement ──────────────────────────────────────────────────────────
# At expiry a short-premium income trade settles to INTRINSIC value: each short leg that
# finished in-the-money is a cash liability (its intrinsic), each long leg in-the-money is a
# cash asset. Realized P&L = entry credit − net intrinsic liability. If everything expired OTM
# the intrinsic is 0 → the full premium collected is the profit. (User's rule, generalized to
# spreads / condors so the short and long wings net correctly, capped by width.)

def intrinsic_settlement(legs: list[dict], contracts: int, close_price: float) -> dict:
    """PURE — the net intrinsic liability of the option structure at expiry, given the closing
    stock price. `net_liability_per_share` > 0 means the structure finished against you (ITM);
    `settlement_cost` is that liability in dollars (× 100 × contracts)."""
    S = float(close_price)
    mult = CONTRACT_MULTIPLIER * (contracts or 1)
    net_liab_ps = 0.0   # per share: + = you owe (short ITM), − = you're owed (long ITM)
    detail: list[dict] = []
    for l in legs or []:
        strike = l.get("strike")
        typ = str(l.get("type", "")).upper()
        act = str(l.get("action", "")).upper()
        if strike is None or not (typ.startswith("C") or typ.startswith("P")):
            continue   # skip non-option / stock legs — they don't expire to intrinsic
        K = float(strike)
        is_call = typ.startswith("C")
        intrinsic = max(0.0, S - K) if is_call else max(0.0, K - S)
        is_short = "SELL" in act
        net_liab_ps += intrinsic if is_short else -intrinsic
        detail.append({
            "strike": K, "right": "C" if is_call else "P",
            "action": "SELL" if is_short else "BUY",
            "intrinsic_per_share": round(intrinsic, 4),
            "itm": intrinsic > 1e-9,
        })
    return {
        "close_price": round(S, 4),
        "net_liability_per_share": round(net_liab_ps, 4),
        "settlement_cost": round(net_liab_ps * mult, 2),   # $ it costs to settle (≈ what you owe)
        "itm": net_liab_ps > 1e-9,                          # structure finished against you
        "legs": detail,
    }


async def _fetch_expiry_close(ticker: str, exp: dt.date) -> Optional[float]:
    """The daily CLOSE on the expiration date (the settlement print). Falls back to the last close
    on/just before expiry if the exact day is missing. Runs off-thread; None on any failure."""
    def _fetch():
        import yfinance as yf
        start = (exp - dt.timedelta(days=6)).isoformat()
        end = (exp + dt.timedelta(days=2)).isoformat()
        h = yf.Ticker(ticker).history(start=start, end=end, interval="1d")
        if h is None or h.empty:
            return None
        h = h[[d <= exp for d in h.index.date]]   # up to and including expiry
        if h.empty:
            return None
        return float(h["Close"].iloc[-1])
    try:
        return await asyncio.to_thread(_fetch)
    except Exception as exc:  # noqa: BLE001
        logger.info("expiry close fetch failed for %s @ %s: %s", ticker, exp, exc)
        return None


async def settle_expiry(pt, db, close_memo: Optional[dict] = None, commit: bool = True) -> dict:
    """Settle an EXPIRED paper trade to intrinsic value → status 'expired', realized P&L banked.
    Fetches the expiry-date close (memoized per ticker+expiry across a batch). Raises if the close
    can't be fetched (caller leaves the trade open to retry). Returns the settlement summary."""
    exp = expiry_date(pt.expiration)
    if exp is None:
        raise RuntimeError(f"paper trade {pt.id} has no parseable expiration")
    key = (pt.ticker, pt.expiration)
    S: Optional[float]
    if close_memo is not None and key in close_memo:
        S = close_memo[key]
    else:
        S = await _fetch_expiry_close(pt.ticker, exp)
        if close_memo is not None:
            close_memo[key] = S
    if S is None:
        raise RuntimeError(f"no expiry close for {pt.ticker} on {pt.expiration}")

    legs = json.loads(pt.legs) if pt.legs else []
    st = intrinsic_settlement(legs, pt.contracts or 1, S)
    entry_credit = pt.entry_credit or 0.0
    realized = round(entry_credit - st["settlement_cost"], 2)
    itm = st["itm"]
    now = dt.datetime.now(dt.timezone.utc)

    summary = {
        "matched": False,          # no live desk score for an expired chain
        "settled": True, "expired": True, "status": "expired",
        "itm": itm, "close_price": st["close_price"],
        "settlement_cost": st["settlement_cost"], "realized_pnl": realized,
        "legs_settlement": st["legs"],
        "pnl": {
            "cost_basis": entry_credit, "current_value": st["settlement_cost"],
            "unrealized_pnl": realized, "realized_pnl": realized,
            "unrealized_pct": (round(realized / abs(entry_credit) * 100.0, 2) if entry_credit else None),
            "current_spot": st["close_price"], "entry_spot": pt.entry_spot,
            "spot_change_pct": (round((st["close_price"] - pt.entry_spot) / pt.entry_spot * 100.0, 2)
                                if pt.entry_spot else None),
            "dte_remaining": 0, "contracts": pt.contracts or 1,
        },
    }

    pt.status = "expired"
    pt.closed_at = now
    pt.close_pnl = realized
    pt.close_note = (
        f"Expired {'in-the-money' if itm else 'out-of-the-money'} — settled at ${st['close_price']:.2f} close"
        + (f"; intrinsic ${st['net_liability_per_share']:.2f}/sh → −${st['settlement_cost']:,.0f} vs "
           f"${entry_credit:,.0f} credit" if itm else "; full premium kept")
    )
    pt.last_pnl = realized
    pt.last_spot = st["close_price"]
    pt.last_value_per_share = st["net_liability_per_share"]
    pt.last_desk_score = None
    pt.last_algo_grade = None
    pt.last_eval = json.dumps(summary, default=str)
    pt.last_eval_at = now
    if commit:
        await db.commit()
        await db.refresh(pt)
    return summary
