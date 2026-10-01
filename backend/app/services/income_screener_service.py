"""Income Screener — find WHERE option-income opportunities are today, then grade them.

Three stages, each deliberately small so the single 512 MiB instance never holds a big peak:

1. ``screen_universe``  — ONE Yahoo equity-screener query (server-side filters: price range,
   market cap, liquidity, beta, sector, today's move) → up to ``max_results`` optionable US names
   with price / today % / 52W range / next earnings straight off the screener quote. No per-name
   calls, so the table appears in ~1–2 s.
2. ``vol_metrics``      — ATM IV30 + HV30 for a BATCH (≤ 25) of tickers. HV30 from ONE batched
   ``yf.download``; IV30 = ATM IV (call+put nearest spot) on the two expiries bracketing 30 DTE,
   total-variance interpolated to exactly 30 calendar days. Cached per ticker. The frontend calls
   this in batches so the IV columns fill in progressively (and IV>HV filters apply as they land).
3. ``evaluate_ticker``  — the Income Desk engine (``rank_desk``) on ONE ticker for naked calls +
   cash-secured puts at ONE expiry inside the user's DTE window (earnings include/exclude), then
   keeps only A/B grades and at most 4 trades: per side, the short strike NEAREST spot and the one
   FARTHEST from spot. Compact payload (the full desk result is huge). ``rank_desk`` already runs
   under the desk scan semaphore, so concurrent evaluations queue instead of stacking memory.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import math
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timezone
from typing import Optional

logger = logging.getLogger(__name__)

SCREEN_TTL = 900            # screener snapshot (prices move; 15 min like _TTL_PRICE)
VOL_TTL = 1800              # per-ticker IV30/HV30
EVAL_TTL = 900              # per-ticker graded trades
MAX_SCREEN_RESULTS = 250    # one Yahoo screener page
MAX_VOL_BATCH = 25
_VOL_WORKERS = 6            # bounded chain fetches per batch (Yahoo rate limits + memory)
_US_EXCHANGES = ["NMS", "NYQ", "NGM", "NCM", "ASE"]   # listed US venues — excludes OTC/pink sheets
IV_TENOR_DAYS = 30

CALL_STRUCTURES = ("naked_call", "covered_call")
PUT_STRUCTURES = ("cash_secured_put",)


def _key(prefix: str, payload: dict) -> str:
    h = hashlib.sha1(json.dumps(payload, sort_keys=True, default=str).encode()).hexdigest()[:16]
    return f"{prefix}:{h}"


# ───────────────────────── Stage 1 — universe screen ─────────────────────────

def _next_earnings_iso(q: dict) -> Optional[str]:
    """Earliest earnings timestamp on the quote that is today-or-later (the screener carries the
    LAST print in ``earningsTimestamp`` and the upcoming window in ``…Start``/``…End``)."""
    today = date.today()
    best: Optional[date] = None
    for k in ("earningsTimestampStart", "earningsTimestamp", "earningsTimestampEnd"):
        ts = q.get(k)
        if not ts:
            continue
        try:
            d = datetime.fromtimestamp(int(ts), tz=timezone.utc).date()
        except (TypeError, ValueError, OSError):
            continue
        if d >= today and (best is None or d < best):
            best = d
    return best.isoformat() if best else None


def _screen_sync(p: dict) -> dict:
    import yfinance as yf
    from yfinance import EquityQuery as Q

    clauses = [Q("eq", ["region", "us"]), Q("is-in", ["exchange", *_US_EXCHANGES])]
    pmin, pmax = p.get("price_min"), p.get("price_max")
    if pmin is not None and pmax is not None:
        clauses.append(Q("btwn", ["intradayprice", float(pmin), float(pmax)]))
    elif pmin is not None:
        clauses.append(Q("gte", ["intradayprice", float(pmin)]))
    elif pmax is not None:
        clauses.append(Q("lte", ["intradayprice", float(pmax)]))
    if p.get("market_cap_min_b"):
        clauses.append(Q("gte", ["intradaymarketcap", float(p["market_cap_min_b"]) * 1e9]))
    if p.get("avg_volume_min"):
        clauses.append(Q("gte", ["avgdailyvol3m", float(p["avg_volume_min"])]))
    if p.get("beta_min") is not None:
        clauses.append(Q("gte", ["beta", float(p["beta_min"])]))
    if p.get("beta_max") is not None:
        clauses.append(Q("lte", ["beta", float(p["beta_max"])]))
    if p.get("change_min") is not None:
        clauses.append(Q("gte", ["percentchange", float(p["change_min"])]))
    if p.get("change_max") is not None:
        clauses.append(Q("lte", ["percentchange", float(p["change_max"])]))
    sectors = [s for s in (p.get("sectors") or []) if s]
    if sectors:
        clauses.append(Q("is-in", ["sector", *sectors]) if len(sectors) > 1 else Q("eq", ["sector", sectors[0]]))

    size = max(1, min(int(p.get("max_results") or 100), MAX_SCREEN_RESULTS))
    # Most-liquid first: average share volume is the best free proxy for a tight, deep options book.
    res = yf.screen(Q("and", clauses), sortField="avgdailyvol3m", sortAsc=False, size=size, offset=0)
    quotes = (res or {}).get("quotes") or []

    today = date.today()
    excl_days = p.get("exclude_earnings_within_days")
    pos_min, pos_max = p.get("week52_pos_min"), p.get("week52_pos_max")
    rows: list[dict] = []
    for q in quotes:
        sym = q.get("symbol")
        price = q.get("regularMarketPrice")
        if not sym or not price:
            continue
        hi, lo = q.get("fiftyTwoWeekHigh"), q.get("fiftyTwoWeekLow")
        pos = None
        if hi and lo and hi > lo:
            pos = round((price - lo) / (hi - lo) * 100.0, 1)
        if pos_min is not None and (pos is None or pos < pos_min):
            continue
        if pos_max is not None and (pos is None or pos > pos_max):
            continue
        ne = _next_earnings_iso(q)
        days_to_er = (date.fromisoformat(ne) - today).days if ne else None
        if excl_days and days_to_er is not None and days_to_er <= int(excl_days):
            continue
        rows.append({
            "ticker": sym,
            "name": q.get("shortName") or q.get("longName"),
            "current_price": round(float(price), 2),
            "today_pct": round(float(q.get("regularMarketChangePercent") or 0.0), 2),
            "week52_low": round(float(lo), 2) if lo else None,
            "week52_high": round(float(hi), 2) if hi else None,
            "week52_pos_pct": pos,
            "market_cap": q.get("marketCap"),
            "avg_volume": q.get("averageDailyVolume3Month"),
            "next_earnings": ne,
            "days_to_earnings": days_to_er,
        })
    return {"total_matches": (res or {}).get("total"), "rows": rows}


async def screen_universe(params: dict, db=None) -> dict:
    from .cache_service import get_cached, set_cached

    ck = _key("iscreen:universe:v1", params)
    if db is not None:
        cached = await get_cached(db, ck)
        if cached is not None:
            return cached
    out = await asyncio.to_thread(_screen_sync, params)
    out["as_of"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
    out["universe_note"] = ("Yahoo equity screener · US-listed common stocks, most liquid first. "
                            "ETFs/indices aren't in the screener — add them manually.")
    if db is not None:
        await set_cached(db, ck, out, ttl_seconds=SCREEN_TTL)
    return out


# ───────────────────────── Stage 2 — IV30 / HV30 ─────────────────────────

def _atm_iv(tk, exp: str, spot: float) -> Optional[float]:
    """ATM implied vol for one expiry: mean of the call and put IV at the strike nearest spot.
    Rejects yfinance's garbage near-zero / absurd IVs (off-hours quotes)."""
    try:
        ch = tk.option_chain(exp)
    except Exception:  # noqa: BLE001
        return None
    ivs: list[float] = []
    for df in (ch.calls, ch.puts):
        if df is None or df.empty or "impliedVolatility" not in df:
            continue
        idx = (df["strike"] - spot).abs().idxmin()
        iv = df.loc[idx, "impliedVolatility"]
        try:
            iv = float(iv)
        except (TypeError, ValueError):
            continue
        if 0.03 < iv < 5.0:
            ivs.append(iv)
    return sum(ivs) / len(ivs) if ivs else None


def _iv30_sync(ticker: str, spot: float) -> dict:
    """ATM IV at a constant 30-day tenor: total-variance interpolation between the expiries that
    bracket 30 DTE (σ²T is linear in T); the single nearest expiry when only one side exists."""
    import yfinance as yf

    out = {"atm_iv": None, "iv_tenor": None, "optionable": False}
    try:
        tk = yf.Ticker(ticker)
        exps = list(tk.options or [])
    except Exception:  # noqa: BLE001
        return out
    if not exps:
        return out
    out["optionable"] = True
    today = date.today()
    parsed = []
    for s in exps:
        try:
            dte = (datetime.strptime(s, "%Y-%m-%d").date() - today).days
        except ValueError:
            continue
        if dte >= 3:                      # skip expiring-this-week noise
            parsed.append((s, dte))
    if not parsed:
        return out
    below = [x for x in parsed if x[1] <= IV_TENOR_DAYS]
    above = [x for x in parsed if x[1] >= IV_TENOR_DAYS]
    e1 = max(below, key=lambda x: x[1]) if below else None
    e2 = min(above, key=lambda x: x[1]) if above else None
    if e1 and e2 and e1[0] != e2[0]:
        iv1, iv2 = _atm_iv(tk, e1[0], spot), _atm_iv(tk, e2[0], spot)
        if iv1 and iv2:
            t1, t2, t = e1[1] / 365.0, e2[1] / 365.0, IV_TENOR_DAYS / 365.0
            var = iv1 * iv1 * t1 + (t - t1) / (t2 - t1) * (iv2 * iv2 * t2 - iv1 * iv1 * t1)
            if var > 0:
                out["atm_iv"] = round(math.sqrt(var / t) * 100.0, 1)
                out["iv_tenor"] = f"30d interp ({e1[1]}d/{e2[1]}d)"
                return out
        iv = iv1 or iv2
        if iv:
            out["atm_iv"] = round(iv * 100.0, 1)
            out["iv_tenor"] = f"{(e1 if iv1 else e2)[1]}d"
        return out
    e = e1 or e2 or min(parsed, key=lambda x: abs(x[1] - IV_TENOR_DAYS))
    iv = _atm_iv(tk, e[0], spot)
    if iv:
        out["atm_iv"] = round(iv * 100.0, 1)
        out["iv_tenor"] = f"{e[1]}d"
    return out


def _hv30_batch_sync(tickers: list[str]) -> dict[str, dict]:
    """HV30 (sample std of 30 daily log returns × √252 — the SAME definition as the desk's
    ``_context_sync``) + last/prev close + 52W range for manual adds, from ONE batched download."""
    import numpy as np
    import yfinance as yf

    out: dict[str, dict] = {}
    try:
        df = yf.download(tickers, period="1y", interval="1d", auto_adjust=True,
                         progress=False, threads=False, group_by="column")
    except Exception as exc:  # noqa: BLE001
        logger.warning("HV batch download failed: %s", exc)
        return out
    if df is None or df.empty:
        return out
    close = df["Close"]
    if not hasattr(close, "columns"):                  # single ticker → Series
        close = close.to_frame(tickers[0])
    for t in tickers:
        if t not in close.columns:
            continue
        s = close[t].dropna()
        if len(s) < 31:
            continue
        lr = np.log(s / s.shift(1)).dropna()
        out[t] = {
            "hv30": round(float(lr.tail(30).std() * math.sqrt(252)) * 100.0, 1),
            "last_close": float(s.iloc[-1]),
            "prev_close": float(s.iloc[-2]),
            "week52_high": round(float(s.max()), 2),
            "week52_low": round(float(s.min()), 2),
        }
    return out


async def vol_metrics(items: list[dict], db=None) -> list[dict]:
    """IV30/HV30 for a batch. ``items`` = [{ticker, price?}]. Cache reads/writes are SEQUENTIAL on
    the one AsyncSession (never gather DB ops on a shared session); only the yfinance work is parallel."""
    from .cache_service import get_cached, set_cached

    items = items[:MAX_VOL_BATCH]
    result: dict[str, dict] = {}
    missing: list[dict] = []
    for it in items:
        t = it["ticker"].upper().strip()
        cached = await get_cached(db, f"iscreen:vol:{t}:v1") if db is not None else None
        if cached is not None:
            result[t] = cached
        else:
            missing.append({"ticker": t, "price": it.get("price")})

    if missing:
        def _work() -> list[dict]:
            hv = _hv30_batch_sync([m["ticker"] for m in missing])
            rows: list[dict] = []

            def one(m: dict) -> dict:
                t = m["ticker"]
                h = hv.get(t) or {}
                spot = m.get("price") or h.get("last_close")
                row = {"ticker": t, "hv30": h.get("hv30"), "atm_iv": None, "iv_tenor": None,
                       "optionable": False}
                if h.get("last_close") and h.get("prev_close"):
                    row["current_price"] = round(h["last_close"], 2)
                    row["today_pct"] = round((h["last_close"] / h["prev_close"] - 1) * 100.0, 2)
                    row["week52_high"] = h.get("week52_high")
                    row["week52_low"] = h.get("week52_low")
                if spot:
                    row.update(_iv30_sync(t, float(spot)))
                if row["atm_iv"] and row["hv30"]:
                    row["iv_hv_ratio"] = round(row["atm_iv"] / row["hv30"], 2)
                return row

            with ThreadPoolExecutor(max_workers=_VOL_WORKERS) as ex:
                rows = list(ex.map(one, missing))
            return rows

        computed = await asyncio.to_thread(_work)
        for r in computed:
            result[r["ticker"]] = r
            if db is not None and (r.get("atm_iv") is not None or r.get("hv30") is not None):
                await set_cached(db, f"iscreen:vol:{r['ticker']}:v1", r, ttl_seconds=VOL_TTL)

    return [result[it["ticker"].upper().strip()] for it in items if it["ticker"].upper().strip() in result]


# ───────────────────────── Stage 3 — grade A/B trades ─────────────────────────

def _pick_expiry(exps: list[str], min_dte: int, max_dte: int,
                 earnings_before: Optional[date]) -> tuple[Optional[tuple[str, int]], Optional[str]]:
    """ONE expiry inside [min_dte, max_dte] (memory: one chain per name). Standard monthlies are
    preferred (deepest books), then the listing nearest the window's midpoint. With
    ``earnings_before`` set the expiry must settle BEFORE the print."""
    from .derivative_income_service import _is_monthly_expiry

    today = date.today()
    cands = []
    for s in exps:
        try:
            d = datetime.strptime(s, "%Y-%m-%d").date()
        except ValueError:
            continue
        dte = (d - today).days
        if min_dte <= dte <= max_dte:
            cands.append((s, dte, d))
    if not cands:
        return None, f"No listed expiry between {min_dte}–{max_dte} DTE"
    if earnings_before:
        cands = [c for c in cands if c[2] < earnings_before]
        if not cands:
            return None, f"Every expiry in the window straddles earnings ({earnings_before.isoformat()})"
    mid = (min_dte + max_dte) / 2.0
    cands.sort(key=lambda c: (not _is_monthly_expiry(c[2]), abs(c[1] - mid)))
    s, dte, _ = cands[0]
    return (s, dte), None


def _compact(o: dict, pick: str) -> dict:
    qp = o.get("qp") or {}
    return {
        "pick": pick,                                    # nearest | farthest
        "side": "call" if o.get("structure") in CALL_STRUCTURES else "put",
        "structure": o.get("structure"),
        "label": o.get("label"),
        "expiration": o.get("expiration"),
        "dte": o.get("dte"),
        "short_strike": o.get("short_strike"),
        "short_strike_pct": o.get("short_strike_pct"),
        "short_delta": o.get("short_delta"),
        "premium": o.get("premium"),
        "premium_per_share": o.get("premium_per_share"),
        "prob_keep_pct": o.get("prob_keep_pct"),
        "prob_touch_pct": o.get("prob_touch_pct"),
        "premium_annualized_pct": o.get("premium_annualized_pct"),
        "collateral": o.get("collateral"),
        "capital_basis": o.get("capital_basis"),
        "breakeven": o.get("breakeven"),
        "cushion_pct": o.get("cushion_pct"),
        "theta_per_day": o.get("theta_per_day"),
        "expected_pnl": o.get("expected_pnl"),
        "implied_vol_pct": qp.get("implied_vol_pct"),
        "iv_hv_ratio": qp.get("iv_hv_ratio"),
        "earnings_gap_pct": o.get("earnings_gap_pct"),
        "desk_score": o.get("desk_score"),
        "algo_grade": o.get("algo_grade"),
        "approval_odds": o.get("approval_odds"),
        "grade_merits": (o.get("grade_merits") or [])[:3],
        "grade_demerits": (o.get("grade_demerits") or [])[:3],
        "timing_hold": bool(o.get("grade_timing_hold")),
    }


def select_trades(ranked: list[dict], spot: float, grades: set[str], per_side: int = 2) -> list[dict]:
    """≤ 4 trades: per side (naked call / CSP), the A/B-graded short strike NEAREST spot and the one
    FARTHEST from spot (one trade when a side has a single qualifier)."""
    picks: list[dict] = []
    for structs in (CALL_STRUCTURES, PUT_STRUCTURES):
        side = [o for o in ranked
                if o.get("structure") in structs and (o.get("algo_grade") or "").upper() in grades
                and o.get("short_strike")]
        if not side:
            continue
        side.sort(key=lambda o: (abs(float(o["short_strike"]) - spot), -(o.get("desk_score") or 0)))
        picks.append(_compact(side[0], "nearest"))
        if per_side > 1 and len(side) > 1:
            far = max(side, key=lambda o: (abs(float(o["short_strike"]) - spot), o.get("desk_score") or 0))
            if far["short_strike"] != side[0]["short_strike"]:
                picks.append(_compact(far, "farthest"))
    return picks


async def evaluate_ticker(ticker: str, p: dict, user=None, db=None) -> dict:
    from .cache_service import get_cached, set_cached
    from .derivative_income_service import _context_sync, _norm_ticker
    from .desk_review_service import rank_desk
    from .quote_providers import get_provider

    ticker = _norm_ticker(ticker)
    ck = _key(f"iscreen:eval:{ticker}:v1", p)
    if db is not None:
        cached = await get_cached(db, ck)
        if cached is not None:
            return cached

    base = {"ticker": ticker, "trades": [], "expiration": None, "dte": None, "spot": None,
            "next_earnings": None, "n_candidates": 0, "n_qualified": 0, "skipped": None}

    # Earnings: the screener hint when present, else the desk's own read (one history pull).
    ne: Optional[str] = p.get("next_earnings")
    if not ne:
        try:
            ne = (await asyncio.to_thread(_context_sync, ticker)).get("next_earnings")
        except Exception:  # noqa: BLE001
            ne = None
    base["next_earnings"] = ne
    earn_before = None
    if p.get("earnings") == "exclude" and ne:
        try:
            earn_before = date.fromisoformat(ne[:10])
        except ValueError:
            earn_before = None

    provider = get_provider(p.get("quote_source") or "yfinance", user=user, db=db)
    try:
        exps = await provider.get_option_expirations(ticker)
    except Exception as exc:  # noqa: BLE001
        base["skipped"] = f"No options chain: {exc}"
        return base
    chosen, why = _pick_expiry(exps or [], int(p["min_dte"]), int(p["max_dte"]), earn_before)
    if not chosen:
        base["skipped"] = why
        return base
    base["expiration"], base["dte"] = chosen

    res = await rank_desk(
        ticker, min_prob=float(p["min_prob"]), min_income=float(p["min_income"]),
        structures=["covered_call", "cash_secured_put"],     # not owned → builds NAKED calls
        quote_source=p.get("quote_source") or "yfinance", user=user, db=db,
        target_expiration=chosen[0], owns_underlying=False,
        earnings_aware=bool(p.get("earnings_aware", True)),
    )
    if res.get("error"):
        base["skipped"] = res["error"]
        return base
    ranked = res.get("ranked") or []
    spot = float(res.get("spot") or 0.0)
    grades = {g.upper() for g in (p.get("grades") or ["A", "B"])}
    base.update({
        "spot": round(spot, 2) if spot else None,
        "n_candidates": len(ranked),
        "n_qualified": sum(1 for o in ranked if (o.get("algo_grade") or "").upper() in grades),
        "trades": select_trades(ranked, spot, grades) if spot else [],
    })
    if not base["trades"]:
        base["skipped"] = (f"No {'/'.join(sorted(grades))}-grade trades" if ranked
                           else "No executable trades met the probability / premium filters")
    if db is not None:
        await set_cached(db, ck, base, ttl_seconds=EVAL_TTL)
    return base
