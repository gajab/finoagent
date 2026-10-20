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

Earnings plays (sell the event vol before the crush): an ``earnings_window`` (today after the close ·
tomorrow · within 2–5 trading sessions) swaps the universe source to Yahoo's earnings CALENDAR — every
name reporting in the window, with its BMO/AMC slot — intersected with the same screener filters. Stage 2
then also prices the event expiry (first listed expiry after the print: its ATM IV and the isolated
implied earnings move, the SAME ``_earnings_implied_move_pct`` the desk grades with), and stage 3's
``earnings="play"`` trades that expiry so the short premium spans the print.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import math
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, time as dt_time, timedelta, timezone
from types import SimpleNamespace
from typing import Optional
from zoneinfo import ZoneInfo

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

_ET = ZoneInfo("America/New_York")
_CLOSE_ET = dt_time(16, 0)
# Earnings windows as (first, last) trading-session offsets: 0 = today's session, 1 = the next session, …
EARNINGS_WINDOWS: dict[str, tuple[int, int]] = {
    "today": (0, 0), "tomorrow": (1, 1),
    "within_2": (0, 2), "within_3": (0, 3), "within_4": (0, 4), "within_5": (0, 5),
}
_CAL_PAGE = 100             # Yahoo caps a calendar page at 100 events
_CAL_MAX_PAGES = 8          # ≤ 800 events — market-cap DESC, so a truncation only drops the smallest names
_TICKER_CHUNK = 200         # ticker-scoped screener queries (the calendar's own most-active filter uses 200)
EVENT_MAX_DTE = 45          # don't price an "event expiry" further out than the desk's default horizon


def _key(prefix: str, payload: dict) -> str:
    h = hashlib.sha1(json.dumps(payload, sort_keys=True, default=str).encode()).hexdigest()[:16]
    return f"{prefix}:{h}"


# ───────────────────────── Earnings timing (pure date math) ─────────────────────────

def _is_session(d: date) -> bool:
    from .selloff_monitor_service import is_session
    return is_session(d)


def _next_session(d: date) -> date:
    d += timedelta(days=1)
    while not _is_session(d):
        d += timedelta(days=1)
    return d


def _prev_session(d: date) -> date:
    d -= timedelta(days=1)
    while not _is_session(d):
        d -= timedelta(days=1)
    return d


def _now_et() -> datetime:
    return datetime.now(_ET)


def _window_dates(window: str, today: date) -> Optional[tuple[date, date]]:
    """Calendar dates spanned by an earnings window. Offset 0 is today only when today is a session
    (a weekend has no "today after the close"); offset N is the N-th session after today."""
    lo_off, hi_off = EARNINGS_WINDOWS[window]

    def at(off: int) -> Optional[date]:
        if off == 0:
            return today if _is_session(today) else None
        d = today
        for _ in range(off):
            d = _next_session(d)
        return d

    lo, hi = at(lo_off), at(hi_off)
    if hi is None:
        return None
    return (lo or today), hi


def _last_session_before_print(d: date, slot: Optional[str]) -> date:
    """The last regular session in which premium can still be sold ahead of the print. BMO reports before
    d's open → the session before d; AMC (and an unknown time, treated as AMC) → d itself."""
    if slot == "bmo":
        return _prev_session(d)
    return d if _is_session(d) else _prev_session(d)


def _sellable(d: date, slot: Optional[str], now: datetime) -> bool:
    """True while there is still a regular session to sell into before the print (options don't trade
    pre-/post-market, so today's BMO — or tonight's AMC after 4 pm ET — is already too late)."""
    last = _last_session_before_print(d, slot)
    today = now.date()
    if last > today:
        return True
    return last == today and now.time() < _CLOSE_ET


def _reaction_session(d: date, slot: Optional[str]) -> date:
    """The session whose OPEN prices the print (where the IV crush lands): BMO → d; AMC → the next session.
    An unknown time is treated as AMC — conservative, because an expiry on/after the next session spans the
    print whichever slot it turns out to be."""
    if slot == "bmo":
        return d if _is_session(d) else _next_session(d)
    return _next_session(d)


def _cal_slot(timing: Optional[str], ts) -> Optional[str]:
    """Yahoo calendar timing → 'bmo' | 'amc' | None. TAS (time as stated) carries a real time; TNS (time not
    supplied) carries a 20:00 UTC placeholder, so it stays unknown."""
    from .derivative_income_service import _amc_bmo

    t = (timing or "").upper()
    if t in ("BMO", "AMC"):
        return t.lower()
    if t == "TAS":
        return _amc_bmo(ts)
    return None


def _pick_events(records: list[tuple], lo: date, hi: date, now: datetime) -> dict[str, dict]:
    """Calendar records ``(symbol, timestamp, timing)`` → each symbol's EARLIEST print in [lo, hi] (ET dates),
    kept only while it is still sellable. Earliest first, THEN sellability: a later duplicate listing must
    never stand in for a print that has already happened (the vol would already be crushed)."""
    import pandas as pd

    first: dict[str, tuple[date, Optional[str]]] = {}
    for sym, ts, timing in records:
        if not sym or ts is None:
            continue
        try:
            t = pd.Timestamp(ts)
            t = t.tz_localize("UTC") if t.tzinfo is None else t
            d = t.tz_convert(_ET).date()
        except Exception:  # noqa: BLE001
            continue
        if lo <= d <= hi and (sym not in first or d < first[sym][0]):
            first[sym] = (d, _cal_slot(timing, t))
    return {sym: {"date": d.isoformat(), "timing": slot, "reaction": _reaction_session(d, slot).isoformat()}
            for sym, (d, slot) in first.items() if _sellable(d, slot, now)}


def _earnings_calendar_sync(lo: date, hi: date, mcap_min: Optional[float]) -> list[tuple]:
    """Every US earnings event in [lo, hi] (≥ mcap_min), market-cap DESC, paginated."""
    import yfinance as yf

    cal = yf.Calendars()
    recs: list[tuple] = []
    for page in range(_CAL_MAX_PAGES):
        df = cal.get_earnings_calendar(
            market_cap=mcap_min, filter_most_active=False, start=lo.isoformat(),
            end=(hi + timedelta(days=1)).isoformat(),          # lte <date> is midnight UTC → include all of hi
            limit=_CAL_PAGE, offset=page * _CAL_PAGE, force=True)
        if df is None or df.empty:
            break
        for sym, row in df.iterrows():
            recs.append((str(sym), row.get("Event Start Date"), row.get("Timing")))
        if len(df) < _CAL_PAGE:
            break
    return recs


# ───────────────────────── Stage 1 — universe screen ─────────────────────────

def _next_earnings_iso(q: dict, today: Optional[date] = None) -> Optional[str]:
    """Earliest earnings timestamp on the quote that is today-or-later (the screener carries the
    LAST print in ``earningsTimestamp`` and the upcoming window in ``…Start``/``…End``)."""
    today = today or date.today()
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
    now = _now_et()
    today = now.date()
    window = p.get("earnings_window")
    events: dict[str, dict] = {}
    window_meta: Optional[dict] = None
    if window:
        # Earnings play: the universe is every name REPORTING in the window (Yahoo earnings calendar, with
        # its BMO/AMC slot), intersected with the same filters via a ticker-scoped screener query.
        span = _window_dates(window, today)
        window_meta = {"window": window, "from": span[0].isoformat() if span else None,
                       "to": span[1].isoformat() if span else None, "n_reporting": 0}
        if span is None:
            return {"total_matches": 0, "rows": [], "earnings_window": window_meta}
        mcap = float(p["market_cap_min_b"]) * 1e9 if p.get("market_cap_min_b") else None
        events = _pick_events(_earnings_calendar_sync(span[0], span[1], mcap), span[0], span[1], now)
        window_meta["n_reporting"] = len(events)
        class _TickerScoped(Q):             # "ticker" isn't in EquityQuery's validated field list, but the
            def __init__(self, d):          # screener accepts it — pass the raw body through untouched
                self._d = d

            def to_dict(self):
                return self._d

        base = Q("and", clauses).to_dict()
        syms = sorted(events)
        quotes: list[dict] = []
        total = 0
        for i in range(0, len(syms), _TICKER_CHUNK):
            body = {**base, "operands": [*base["operands"], {
                "operator": "or",
                "operands": [{"operator": "eq", "operands": ["ticker", s]} for s in syms[i:i + _TICKER_CHUNK]]}]}
            res = yf.screen(_TickerScoped(body), sortField="avgdailyvol3m", sortAsc=False,
                            size=MAX_SCREEN_RESULTS, offset=0)
            quotes += (res or {}).get("quotes") or []
            total += int((res or {}).get("total") or 0)
        quotes.sort(key=lambda q: -(q.get("averageDailyVolume3Month") or 0))
        quotes = quotes[:size]
        res = {"total": total}
    else:
        # Most-liquid first: average share volume is the best free proxy for a tight, deep options book.
        res = yf.screen(Q("and", clauses), sortField="avgdailyvol3m", sortAsc=False, size=size, offset=0)
        quotes = (res or {}).get("quotes") or []

    excl_days = None if window else p.get("exclude_earnings_within_days")
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
        ev = events.get(sym)
        if window and ev is None:
            continue
        ne = ev["date"] if ev else _next_earnings_iso(q, today)
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
            # Calendar slot (window mode only — the plain quote's time is a placeholder for many names).
            "earnings_timing": ev["timing"] if ev else None,
            "earnings_reaction": ev["reaction"] if ev else None,
            "earnings_estimate": bool(q.get("isEarningsDateEstimate")) if ne else None,
        })
    out = {"total_matches": (res or {}).get("total"), "rows": rows}
    if window_meta:
        out["earnings_window"] = window_meta
    return out


async def screen_universe(params: dict, db=None) -> dict:
    from .cache_service import get_cached, set_cached

    key_payload = dict(params)
    if params.get("earnings_window"):
        # The window is relative to NOW: a print stops being sellable at the 4 pm ET close.
        now = _now_et()
        key_payload["_asof"] = [now.date().isoformat(), now.time() < _CLOSE_ET]
    ck = _key("iscreen:universe:v2", key_payload)
    if db is not None:
        cached = await get_cached(db, ck)
        if cached is not None:
            return cached
    out = await asyncio.to_thread(_screen_sync, params)
    out["as_of"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
    ew = out.get("earnings_window")
    if ew:
        out["universe_note"] = (
            f"Yahoo earnings calendar · {ew['n_reporting']} US names report {ew['from']} → {ew['to']} "
            "with a session left to sell into, intersected with your filters, most liquid first."
            if ew.get("to") else "No trading session today — pick tomorrow or a wider window.")
    else:
        out["universe_note"] = ("Yahoo equity screener · US-listed common stocks, most liquid first. "
                                "ETFs/indices aren't in the screener — add them manually.")
    if db is not None:
        await set_cached(db, ck, out, ttl_seconds=SCREEN_TTL)
    return out


# ───────────────────────── Stage 2 — IV30 / HV30 ─────────────────────────

def _chain(tk, exp: str, chains: Optional[dict] = None):
    """One expiry's chain, memoised per ticker call so IV30 and the event expiry never fetch it twice."""
    if chains is not None and exp in chains:
        return chains[exp]
    try:
        ch = tk.option_chain(exp)
    except Exception:  # noqa: BLE001
        ch = None
    if chains is not None:
        chains[exp] = ch
    return ch


def _atm_iv(tk, exp: str, spot: float, chains: Optional[dict] = None) -> Optional[float]:
    """ATM implied vol for one expiry: mean of the call and put IV at the strike nearest spot.
    Rejects yfinance's garbage near-zero / absurd IVs (off-hours quotes)."""
    ch = _chain(tk, exp, chains)
    if ch is None:
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


def _iv30_of(tk, exps: list[str], spot: float, today: date, chains: dict) -> tuple[Optional[float], Optional[str]]:
    """ATM IV (%) at a constant 30-day tenor: total-variance interpolation between the expiries that
    bracket 30 DTE (σ²T is linear in T); the single nearest expiry when only one side exists."""
    parsed = []
    for s in exps:
        try:
            dte = (datetime.strptime(s, "%Y-%m-%d").date() - today).days
        except ValueError:
            continue
        if dte >= 3:                      # skip expiring-this-week noise
            parsed.append((s, dte))
    if not parsed:
        return None, None
    below = [x for x in parsed if x[1] <= IV_TENOR_DAYS]
    above = [x for x in parsed if x[1] >= IV_TENOR_DAYS]
    e1 = max(below, key=lambda x: x[1]) if below else None
    e2 = min(above, key=lambda x: x[1]) if above else None
    if e1 and e2 and e1[0] != e2[0]:
        iv1, iv2 = _atm_iv(tk, e1[0], spot, chains), _atm_iv(tk, e2[0], spot, chains)
        if iv1 and iv2:
            t1, t2, t = e1[1] / 365.0, e2[1] / 365.0, IV_TENOR_DAYS / 365.0
            var = iv1 * iv1 * t1 + (t - t1) / (t2 - t1) * (iv2 * iv2 * t2 - iv1 * iv1 * t1)
            if var > 0:
                return round(math.sqrt(var / t) * 100.0, 1), f"30d interp ({e1[1]}d/{e2[1]}d)"
        iv = iv1 or iv2
        return (round(iv * 100.0, 1), f"{(e1 if iv1 else e2)[1]}d") if iv else (None, None)
    e = e1 or e2 or min(parsed, key=lambda x: abs(x[1] - IV_TENOR_DAYS))
    iv = _atm_iv(tk, e[0], spot, chains)
    return (round(iv * 100.0, 1), f"{e[1]}d") if iv else (None, None)


def _num(v) -> float:
    try:
        f = float(v)
    except (TypeError, ValueError):
        return 0.0
    return f if math.isfinite(f) else 0.0


def _mid_map(df) -> dict:
    """strike → quote with ``.mid`` — the SAME mid rule as the yfinance quote provider (bid/ask midpoint when
    both sides are live, else last), so the event move reconciles with the desk's ``earnings_gap_pct``."""
    out: dict = {}
    if df is None or df.empty:
        return out
    for k, b, a, last in df[["strike", "bid", "ask", "lastPrice"]].itertuples(index=False):
        b, a, last = _num(b), _num(a), _num(last)
        out[float(k)] = SimpleNamespace(mid=round((b + a) / 2, 4) if b > 0 and a > 0 else last)
    return out


def _event_metrics(tk, exps: list[str], spot: float, hv_pct: Optional[float], reaction: date,
                   today: date, chains: dict) -> dict:
    """The EVENT expiry — first listed expiry on/after the reaction session, i.e. the one the print lands in —
    with its ATM IV and the isolated implied earnings move (straddle minus normal diffusion, the desk's own
    ``_earnings_implied_move_pct`` with the same HV30 baseline)."""
    from .derivative_income_service import _earnings_implied_move_pct

    out = {"event_expiry": None, "event_dte": None, "event_iv": None, "event_move_pct": None}
    cands = []
    for s in exps:
        try:
            d = datetime.strptime(s, "%Y-%m-%d").date()
        except ValueError:
            continue
        dte = (d - today).days
        if d >= reaction and 1 <= dte <= EVENT_MAX_DTE:
            cands.append((d, s, dte))
    if not cands:
        return out
    _, s, dte = min(cands)
    out["event_expiry"], out["event_dte"] = s, dte
    iv = _atm_iv(tk, s, spot, chains)
    out["event_iv"] = round(iv * 100.0, 1) if iv else None
    ch = chains.get(s)
    if ch is not None:
        gap = _earnings_implied_move_pct(_mid_map(ch.calls), _mid_map(ch.puts), spot, dte,
                                         hv_pct / 100.0 if hv_pct else None)
        out["event_move_pct"] = round(gap * 100.0, 1) if gap is not None else None
    return out


def _iv30_sync(ticker: str, spot: float, reaction: Optional[date] = None,
               hv_pct: Optional[float] = None) -> dict:
    """IV30 for the screener table, plus — when the name has a print coming (``reaction`` = the session that
    prices it) — the event-expiry metrics. Chains are fetched once and shared."""
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
    today = date.today()                  # the desk's DTE clock, so event_move_pct reconciles with it
    chains: dict = {}
    out["atm_iv"], out["iv_tenor"] = _iv30_of(tk, exps, spot, today, chains)
    if reaction is not None:
        out.update(_event_metrics(tk, exps, spot, hv_pct, reaction, today, chains))
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


def _item_reaction(it: dict) -> Optional[date]:
    """The reaction session for an item's earnings hint (``next_earnings`` + ``earnings_timing``), or None
    when there is no upcoming print to price."""
    ne = it.get("next_earnings")
    if not ne:
        return None
    try:
        d = date.fromisoformat(str(ne)[:10])
    except ValueError:
        return None
    timing = it.get("earnings_timing") if it.get("earnings_timing") in ("bmo", "amc") else None
    return _reaction_session(d, timing) if d >= _now_et().date() else None


def _vol_key(t: str, reaction: Optional[date]) -> str:
    return f"iscreen:vol:{t}:{reaction.isoformat() if reaction else '-'}:v2"


async def vol_metrics(items: list[dict], db=None) -> list[dict]:
    """IV30/HV30 for a batch. ``items`` = [{ticker, price?, next_earnings?, earnings_timing?}] — an
    earnings hint also prices the event expiry. Cache reads/writes are SEQUENTIAL on the one AsyncSession
    (never gather DB ops on a shared session); only the yfinance work is parallel."""
    from .cache_service import get_cached, set_cached

    items = items[:MAX_VOL_BATCH]
    result: dict[str, dict] = {}
    missing: list[dict] = []
    for it in items:
        t = it["ticker"].upper().strip()
        reaction = _item_reaction(it)
        cached = await get_cached(db, _vol_key(t, reaction)) if db is not None else None
        if cached is not None:
            result[t] = cached
        else:
            missing.append({"ticker": t, "price": it.get("price"), "reaction": reaction})

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
                    row.update(_iv30_sync(t, float(spot), m.get("reaction"), h.get("hv30")))
                if row["atm_iv"] and row["hv30"]:
                    row["iv_hv_ratio"] = round(row["atm_iv"] / row["hv30"], 2)
                return row

            with ThreadPoolExecutor(max_workers=_VOL_WORKERS) as ex:
                rows = list(ex.map(one, missing))
            return rows

        computed = await asyncio.to_thread(_work)
        reactions = {m["ticker"]: m.get("reaction") for m in missing}
        for r in computed:
            result[r["ticker"]] = r
            if db is not None and (r.get("atm_iv") is not None or r.get("hv30") is not None):
                await set_cached(db, _vol_key(r["ticker"], reactions.get(r["ticker"])), r, ttl_seconds=VOL_TTL)

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


def _pick_event_expiry(exps: list[str], reaction: date, max_dte: int,
                       today: Optional[date] = None) -> tuple[Optional[tuple[str, int]], Optional[str]]:
    """Sell-the-print expiry: the FIRST listed expiry on/after the reaction session — the shortest option that
    spans the print, so the most event vol (and the full crush) sits in the premium. ``max_dte`` caps it;
    the DTE floor doesn't apply (the whole point is a short-dated event expiry)."""
    today = today or date.today()
    cands = []
    for s in exps:
        try:
            d = datetime.strptime(s, "%Y-%m-%d").date()
        except ValueError:
            continue
        dte = (d - today).days
        if d >= reaction and 1 <= dte:
            cands.append((d, s, dte))
    if not cands:
        return None, f"No listed expiry after the print (reacts {reaction.isoformat()})"
    _, s, dte = min(cands)
    if dte > max_dte:
        return None, f"First expiry after the print is {s} ({dte}d) — beyond the {max_dte}-day max"
    return (s, dte), None


def _compact(o: dict, pick: str) -> dict:
    qp = o.get("qp") or {}
    gap, pct = o.get("earnings_gap_pct"), o.get("short_strike_pct")
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
        "premium_fill": o.get("premium_fill"),           # what you collect after the expected fill + commissions
        "entry_cost": o.get("entry_cost"),
        "limit_only": bool(o.get("limit_only")),          # wide market — work a limit order
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
        "earnings_gap_pct": gap,
        # Short-strike distance in multiples of the implied earnings move (≥ 1.5× is the desk's comfort line).
        "move_multiple": round(abs(float(pct)) / float(gap), 2) if gap and pct is not None else None,
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
    mode = p.get("earnings") or "exclude"          # exclude | include | play (sell the print)
    play = mode == "play"
    key_payload = dict(p)
    if play:                                        # sellability flips at the 4 pm ET close
        now = _now_et()
        key_payload["_asof"] = [now.date().isoformat(), now.time() < _CLOSE_ET]
    # v7 = earnings plays (sell the print) + the desk upgrade (exact payoff law, fills + carry-to-risk,
    # market-implied law, earnings split, limit-only legs) — past both earlier keys (v2 and v6)
    ck = _key(f"iscreen:eval:{ticker}:v7", key_payload)
    if db is not None:
        cached = await get_cached(db, ck)
        if cached is not None:
            return cached

    base = {"ticker": ticker, "trades": [], "expiration": None, "dte": None, "spot": None,
            "next_earnings": None, "earnings_timing": None, "earnings_mode": mode, "event_move_pct": None,
            "n_candidates": 0, "n_qualified": 0, "skipped": None}

    # Earnings: the screener hint (calendar date + BMO/AMC slot) when present, else the desk's own read.
    ne: Optional[str] = p.get("next_earnings")
    timing: Optional[str] = p.get("earnings_timing")
    if not ne:
        try:
            ctx = await asyncio.to_thread(_context_sync, ticker)
            ne, timing = ctx.get("next_earnings"), timing or ctx.get("next_earnings_amc")
        except Exception:  # noqa: BLE001
            ne = None
    timing = timing if timing in ("bmo", "amc") else None
    base["next_earnings"], base["earnings_timing"] = ne, timing
    ne_date: Optional[date] = None
    if ne:
        try:
            ne_date = date.fromisoformat(ne[:10])
        except ValueError:
            ne_date = None
    earn_before = ne_date if mode == "exclude" else None
    reaction: Optional[date] = None
    if play:
        if ne_date is None:
            base["skipped"] = "No upcoming earnings date — nothing to sell into"
            return base
        if not _sellable(ne_date, timing, _now_et()):
            base["skipped"] = (f"The {ne_date.isoformat()} {(timing or '').upper() or 'print'} is already out — "
                               "no session left to sell before it")
            return base
        reaction = _reaction_session(ne_date, timing)

    provider = get_provider(p.get("quote_source") or "yfinance", user=user, db=db)
    try:
        exps = await provider.get_option_expirations(ticker)
    except Exception as exc:  # noqa: BLE001
        base["skipped"] = f"No options chain: {exc}"
        return base
    if play:
        chosen, why = _pick_event_expiry(exps or [], reaction, int(p["max_dte"]))
    else:
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
        # Selling INTO a print without gap-aware grading would pass strikes inside the expected move → forced on.
        earnings_aware=True if play else bool(p.get("earnings_aware", True)),
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
        # The desk's isolated implied earnings move on this expiry (same number every opp carries).
        "event_move_pct": next((o["earnings_gap_pct"] for o in ranked if o.get("earnings_gap_pct") is not None), None),
    })
    if not base["trades"]:
        base["skipped"] = (f"No {'/'.join(sorted(grades))}-grade trades" if ranked
                           else "No executable trades met the probability / premium filters")
    if db is not None:
        from .derivative_income_service import scan_cache_ttl
        await set_cached(db, ck, base, ttl_seconds=scan_cache_ttl(bool(base["trades"]), EVAL_TTL))
    return base
