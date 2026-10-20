"""Market internals for the Selloff Monitor — breadth (who is participating) and anatomy
(what got sold). Phase 2 of the monitor; lazily loaded because it scans ~500 stocks.

Breadth (S&P 500 constituents, closing basis, equal-weighted counts)
  • % of stocks above their 20/50/200-day averages, now vs. before the move window
  • 52-week new highs vs. new lows (closing basis)
  • advancers/decliners and the up-volume share (Lowry 90% up/down-volume days)
  • Zweig breadth thrust (10-day EMA of advancers/(adv+dec) from <0.40 to >0.615 in ≤10 sessions)
  • how many stocks actually fell over the window, and the median stock vs. the index

Anatomy
  • 11 sector ETFs (cap-weighted move, σ vs own volatility, vs SPY) + % of each GICS
    sector's members that fell (equal-weighted participation)
  • factor spreads (momentum−low-vol, high-beta−low-vol, growth−value, small−large,
    equal−cap weight, cyclicals−defensives, semis−market) in σ of the spread's own volatility
  • concentration: what SPY's 10 largest holdings contributed to the move
  • biggest losers / gainers, and a rule-based label for the KIND of selloff

Thresholds marked "conventional" (e.g. ≤15% above the 20-day = washout) are widely used
practitioner levels, NOT measured here — 14 months of today's constituents can't give an
unbiased history (survivorship), so the engine doesn't pretend to.
"""

from __future__ import annotations

import datetime as _dt
import gc
import io
import logging
import math
from typing import Any

import numpy as np
import pandas as pd

from .selloff_monitor_service import VOL_LOOKBACK, WINDOW, _r

logger = logging.getLogger(__name__)

CONSTITUENTS_URL = "https://raw.githubusercontent.com/datasets/s-and-p-500-companies/main/data/constituents.csv"
HISTORY_DAYS = 440            # ≈ 300 sessions: 252-day highs/lows + a 40-session series
CHUNK = 50                    # yfinance batch size — measured: 50 keeps the peak ≈ +25 MB for 500 names
SERIES_LEN = 40
TOP_N_MOVERS = 8

SECTOR_ETFS: list[tuple[str, str, str]] = [   # (ETF, short name, GICS sector in the constituents file)
    ("XLK", "Technology", "Information Technology"),
    ("XLC", "Communication", "Communication Services"),
    ("XLY", "Cons. Discretionary", "Consumer Discretionary"),
    ("XLF", "Financials", "Financials"),
    ("XLI", "Industrials", "Industrials"),
    ("XLB", "Materials", "Materials"),
    ("XLE", "Energy", "Energy"),
    ("XLV", "Health Care", "Health Care"),
    ("XLP", "Cons. Staples", "Consumer Staples"),
    ("XLU", "Utilities", "Utilities"),
    ("XLRE", "Real Estate", "Real Estate"),
]
# (id, label, long leg(s), short leg(s), what a NEGATIVE spread means)
FACTOR_SPREADS: list[tuple[str, str, list[str], list[str], str]] = [
    ("mom_lowvol", "Momentum vs low-vol", ["MTUM"], ["USMV"], "recent winners sold hardest"),
    ("beta", "High beta vs low vol", ["SPHB"], ["SPLV"], "risk appetite falling"),
    ("growth_value", "Growth vs value", ["VUG"], ["VTV"], "growth/duration sold"),
    ("semis", "Semiconductors vs S&P", ["SMH"], ["SPY"], "AI/semis leadership unwinding"),
    ("small_large", "Small vs large caps", ["IWM"], ["SPY"], "small caps / credit-sensitive sold"),
    ("equal_cap", "Equal vs cap weight", ["RSP"], ["SPY"], "the average stock lagging the giants"),
    ("cyc_def", "Cyclicals vs defensives", ["XLY", "XLI", "XLF", "XLB"], ["XLP", "XLU", "XLV"], "growth scare (defensives bid)"),
]
ETF_SYMBOLS = sorted({"SPY"} | {s for s, *_ in SECTOR_ETFS} |
                     {x for _, _, a, b, _ in FACTOR_SPREADS for x in a + b})

WASHOUT_20D = 15.0            # conventional: ≤15% of stocks above their 20-day = washed out
OVERBOUGHT_20D = 85.0
ZWEIG_LOW, ZWEIG_HIGH, ZWEIG_DAYS = 0.40, 0.615, 10


def release_memory() -> None:
    """gc + glibc malloc_trim so the scan's freed arenas go back to the OS (512 MiB instance)."""
    gc.collect()
    try:
        import ctypes
        ctypes.CDLL("libc.so.6").malloc_trim(0)
    except Exception:  # noqa: BLE001 — non-glibc (macOS dev)
        pass


# ---------------------------------------------------------------------------
# Data
# ---------------------------------------------------------------------------

def parse_constituents(csv_text: str) -> list[dict]:
    df = pd.read_csv(io.StringIO(csv_text))
    cols = {c.lower(): c for c in df.columns}
    sym, name, sector = cols.get("symbol"), cols.get("security"), cols.get("gics sector")
    if not sym or not sector:
        raise ValueError("constituents CSV missing Symbol / GICS Sector")
    out = []
    for _, r in df.iterrows():
        s = str(r[sym]).strip().upper().replace(".", "-")        # BRK.B → BRK-B (Yahoo)
        if s and s != "NAN":
            out.append({"symbol": s, "name": str(r[name]) if name else s, "sector": str(r[sector])})
    return out


async def fetch_constituents() -> list[dict]:
    import httpx
    async with httpx.AsyncClient(timeout=20.0) as client:
        r = await client.get(CONSTITUENTS_URL, follow_redirects=True)
        r.raise_for_status()
    out = parse_constituents(r.text)
    if len(out) < 400:
        raise ValueError(f"constituents list looks truncated ({len(out)})")
    return out


def _frame(d: pd.DataFrame, field: str, syms: list[str]) -> pd.DataFrame:
    if d is None or d.empty:
        return pd.DataFrame()
    if isinstance(d.columns, pd.MultiIndex):
        f = d[field] if field in d.columns.get_level_values(0) else pd.DataFrame()
    else:
        f = pd.DataFrame({syms[0]: d[field]}) if field in d.columns else pd.DataFrame()
    if f.empty:
        return f
    idx = pd.DatetimeIndex(f.index)
    if idx.tz is not None:
        idx = idx.tz_localize(None)
    f.index = idx.normalize()
    return f


def fetch_internals_data(constituents: list[dict]) -> dict[str, Any]:
    """Blocking network reads (run in a thread): constituent closes/volumes in small chunks
    (peak-memory bound), sector/factor ETFs, SPY's top holdings."""
    import yfinance as yf

    start = (_dt.date.today() - _dt.timedelta(days=HISTORY_DAYS)).isoformat()
    syms = [c["symbol"] for c in constituents]
    close_parts, vol_parts = [], []
    for i in range(0, len(syms), CHUNK):
        ch = syms[i:i + CHUNK]
        try:
            d = yf.download(" ".join(ch), start=start, interval="1d", progress=False,
                            auto_adjust=True, group_by="column", threads=True)
        except Exception as exc:  # pragma: no cover — network
            logger.warning("internals: chunk %d failed: %s", i, exc)
            continue
        c, v = _frame(d, "Close", ch), _frame(d, "Volume", ch)
        if not c.empty:
            close_parts.append(c.astype("float32"))
            vol_parts.append(v.astype("float32") if not v.empty else c.astype("float32") * np.nan)
        del d, c, v
        gc.collect()
    closes = pd.concat(close_parts, axis=1).sort_index() if close_parts else pd.DataFrame()
    volumes = pd.concat(vol_parts, axis=1).sort_index() if vol_parts else pd.DataFrame()
    del close_parts, vol_parts

    etf: dict[str, pd.Series] = {}
    try:
        d = yf.download(" ".join(ETF_SYMBOLS), start=start, interval="1d", progress=False,
                        auto_adjust=True, group_by="column", threads=True)
        f = _frame(d, "Close", ETF_SYMBOLS)
        etf = {s: f[s].dropna().astype(float) for s in ETF_SYMBOLS if s in f}
    except Exception as exc:  # pragma: no cover
        logger.warning("internals: ETF download failed: %s", exc)

    holdings: list[dict] = []
    try:
        th = yf.Ticker("SPY").funds_data.top_holdings
        for sym, row in th.iterrows():
            w = float(row.get("Holding Percent") or 0)
            if w > 0:
                holdings.append({"symbol": str(sym).upper().replace(".", "-"),
                                 "name": str(row.get("Name") or sym), "weight": w})
    except Exception as exc:  # pragma: no cover
        logger.warning("internals: SPY top holdings failed: %s", exc)
    return {"closes": closes, "volumes": volumes, "etf": etf, "holdings": holdings}


# ---------------------------------------------------------------------------
# Breadth
# ---------------------------------------------------------------------------

def _pct_above(c: pd.DataFrame, n: int) -> pd.Series:
    sma = c.rolling(n, min_periods=n).mean()
    valid = sma.notna() & c.notna()
    above = (c > sma) & valid
    return above.sum(axis=1) / valid.sum(axis=1).replace(0, np.nan) * 100


def build_breadth(closes: pd.DataFrame, volumes: pd.DataFrame, series_len: int = SERIES_LEN) -> dict:
    """Equal-weighted participation stats over the constituent panel (rows = sessions)."""
    c = closes.astype("float64")
    if c.shape[0] < 260 or c.shape[1] < 50:
        return {"available": False, "reason": "Not enough constituent history for breadth."}
    v = volumes.reindex_like(c).astype("float64")
    prev = c.shift(1)
    both = c.notna() & prev.notna()
    adv = (c > prev) & both
    dec = (c < prev) & both
    n_adv, n_dec = adv.sum(axis=1), dec.sum(axis=1)
    up_vol = v.where(adv).sum(axis=1)
    dn_vol = v.where(dec).sum(axis=1)
    up_vol_pct = up_vol / (up_vol + dn_vol).replace(0, np.nan) * 100

    hi = c.rolling(252, min_periods=252).max()
    lo = c.rolling(252, min_periods=252).min()
    nh = ((c >= hi) & hi.notna()).sum(axis=1)
    nl = ((c <= lo) & lo.notna()).sum(axis=1)

    p20, p50, p200 = _pct_above(c, 20), _pct_above(c, 50), _pct_above(c, 200)
    ratio = n_adv / (n_adv + n_dec).replace(0, np.nan)
    zweig = ratio.ewm(span=ZWEIG_DAYS, adjust=False).mean()

    idx = c.index
    tail = range(len(idx) - series_len, len(idx))
    series = [{"date": idx[i].date().isoformat(), "pct20": _r(p20.iloc[i], 1), "pct50": _r(p50.iloc[i], 1),
               "pct200": _r(p200.iloc[i], 1), "nh": int(nh.iloc[i]), "nl": int(nl.iloc[i]),
               "adv": int(n_adv.iloc[i]), "dec": int(n_dec.iloc[i]), "up_vol_pct": _r(up_vol_pct.iloc[i], 1),
               "zweig": _r(zweig.iloc[i], 3)} for i in tail]

    # Zweig thrust: EMA was < 0.40 at some point in the 10 sessions up to a day it closed > 0.615
    thrust_on = None
    for i in range(len(idx) - ZWEIG_DAYS, len(idx)):
        window = zweig.iloc[max(0, i - ZWEIG_DAYS + 1): i + 1]
        if zweig.iloc[i] > ZWEIG_HIGH and window.min() < ZWEIG_LOW:
            thrust_on = idx[i].date().isoformat()
    ninety = [{"date": idx[i].date().isoformat(),
               "kind": "down" if up_vol_pct.iloc[i] <= 10 else "up", "up_vol_pct": _r(up_vol_pct.iloc[i], 1)}
              for i in range(len(idx) - 10, len(idx))
              if pd.notna(up_vol_pct.iloc[i]) and (up_vol_pct.iloc[i] <= 10 or up_vol_pct.iloc[i] >= 90)]

    # the move window: how many stocks fell, the median stock vs. the cap-weighted index
    w_ret = (c.iloc[-1] / c.iloc[-1 - WINDOW] - 1) * 100
    w_ret = w_ret.dropna()
    before = -1 - WINDOW
    last = {
        "date": idx[-1].date().isoformat(),
        "pct20": _r(p20.iloc[-1], 1), "pct50": _r(p50.iloc[-1], 1), "pct200": _r(p200.iloc[-1], 1),
        "pct20_before": _r(p20.iloc[before], 1), "pct50_before": _r(p50.iloc[before], 1),
        "pct200_before": _r(p200.iloc[before], 1),
        "nh": int(nh.iloc[-1]), "nl": int(nl.iloc[-1]),
        "adv": int(n_adv.iloc[-1]), "dec": int(n_dec.iloc[-1]), "up_vol_pct": _r(up_vol_pct.iloc[-1], 1),
        "zweig": _r(zweig.iloc[-1], 3),
    }
    window = {
        "sessions": WINDOW, "n": int(len(w_ret)),
        "pct_down": _r((w_ret < 0).mean() * 100, 1) if len(w_ret) else None,
        "median_pct": _r(w_ret.median(), 2) if len(w_ret) else None,
        "mean_pct": _r(w_ret.mean(), 2) if len(w_ret) else None,
        "nh_sum": int(nh.iloc[-WINDOW:].sum()), "nl_sum": int(nl.iloc[-WINDOW:].sum()),
    }
    state = ("washout" if (last["pct20"] or 100) <= WASHOUT_20D else
             "overbought" if (last["pct20"] or 0) >= OVERBOUGHT_20D else "neutral")
    return {"available": True, "n_stocks": int(c.shape[1]), "last": last, "window": window,
            "series": series, "ninety_pct_days": ninety, "zweig_thrust_on": thrust_on, "state": state,
            "thresholds": {"washout_20d": WASHOUT_20D, "overbought_20d": OVERBOUGHT_20D,
                           "zweig_low": ZWEIG_LOW, "zweig_high": ZWEIG_HIGH,
                           "note": "Conventional practitioner thresholds — not back-tested here."}}


# ---------------------------------------------------------------------------
# Anatomy
# ---------------------------------------------------------------------------

def _window_stats(s: pd.Series, sessions: pd.DatetimeIndex) -> dict:
    """Window return (%) and z = log-return / (σ·√W), σ from the VOL_LOOKBACK daily log
    returns that END before the window — the same convention as the rest of the monitor."""
    s = s.dropna()
    first = sessions[-WINDOW]
    pre = s[s.index < first]
    upto = s[s.index <= sessions[-1]]
    if len(pre) < 25 or upto.empty:
        return {"ret_pct": None, "z": None}
    lr = np.log(pre / pre.shift(1)).dropna().tail(VOL_LOOKBACK)
    sd = float(lr.std()) if len(lr) >= 20 else None
    raw = math.log(float(upto.iloc[-1]) / float(pre.iloc[-1]))
    return {"ret_pct": _r((math.exp(raw) - 1) * 100, 2),
            "z": _r(raw / (sd * math.sqrt(WINDOW)), 2) if sd else None}


def _basket_logret(etf: dict[str, pd.Series], legs: list[str]) -> pd.Series | None:
    parts = [np.log(etf[x] / etf[x].shift(1)) for x in legs if x in etf and not etf[x].empty]
    if len(parts) != len(legs):
        return None
    return pd.concat(parts, axis=1).mean(axis=1, skipna=False)


def _spread_stats(etf: dict[str, pd.Series], long: list[str], short: list[str],
                  sessions: pd.DatetimeIndex) -> dict:
    a, b = _basket_logret(etf, long), _basket_logret(etf, short)
    if a is None or b is None:
        return {"spread_pts": None, "z": None}
    sp = (a - b).dropna()
    first = sessions[-WINDOW]
    pre = sp[sp.index < first].tail(VOL_LOOKBACK)
    win = sp[(sp.index >= first) & (sp.index <= sessions[-1])]
    if len(pre) < 20 or win.empty:
        return {"spread_pts": None, "z": None}
    raw = float(win.sum())
    sd = float(pre.std())
    la, lb = a[(a.index >= first) & (a.index <= sessions[-1])].sum(), b[(b.index >= first) & (b.index <= sessions[-1])].sum()
    return {"spread_pts": _r((math.exp(la) - math.exp(lb)) * 100, 2),
            "z": _r(raw / (sd * math.sqrt(len(win))), 2) if sd else None}


def build_anatomy(closes: pd.DataFrame, constituents: list[dict], etf: dict[str, pd.Series],
                  holdings: list[dict]) -> dict:
    spy = etf.get("SPY")
    if spy is None or len(spy) < VOL_LOOKBACK + WINDOW + 5:
        return {"available": False, "reason": "Sector / factor ETF data unavailable."}
    sessions = pd.DatetimeIndex(spy.dropna().index)
    spy_w = _window_stats(spy, sessions)

    meta = {c["symbol"]: c for c in constituents}
    w_ret = pd.Series(dtype=float)
    if not closes.empty and closes.shape[0] > WINDOW:
        cc = closes.astype("float64")
        w_ret = ((cc.iloc[-1] / cc.iloc[-1 - WINDOW] - 1) * 100).dropna()

    sectors = []
    for sym, short, gics in SECTOR_ETFS:
        st = _window_stats(etf[sym], sessions) if sym in etf else {"ret_pct": None, "z": None}
        members = [s for s in w_ret.index if meta.get(s, {}).get("sector") == gics]
        mr = w_ret[members] if members else pd.Series(dtype=float)
        sectors.append({
            "symbol": sym, "name": short, "gics": gics, "ret_pct": st["ret_pct"], "z": st["z"],
            "vs_spy_pts": _r(st["ret_pct"] - spy_w["ret_pct"], 2)
            if st["ret_pct"] is not None and spy_w["ret_pct"] is not None else None,
            "members": len(mr), "pct_down": _r((mr < 0).mean() * 100, 1) if len(mr) else None,
            "median_member_pct": _r(mr.median(), 2) if len(mr) else None,
        })
    sectors.sort(key=lambda s: (s["ret_pct"] is None, s["ret_pct"] if s["ret_pct"] is not None else 0))

    factors = []
    for fid, label, long, short, meaning in FACTOR_SPREADS:
        st = _spread_stats(etf, long, short, sessions)
        factors.append({"id": fid, "label": label, "long": long, "short": short, "negative_means": meaning, **st})

    conc = None
    if holdings and len(w_ret):
        rows, contrib, wsum = [], 0.0, 0.0
        for h in holdings:
            r = w_ret.get(h["symbol"])
            if r is None or not np.isfinite(r):
                continue
            pts = h["weight"] * float(r)
            contrib += pts
            wsum += h["weight"]
            rows.append({"symbol": h["symbol"], "name": h["name"], "weight_pct": _r(h["weight"] * 100, 2),
                         "ret_pct": _r(r, 2), "contribution_pts": _r(pts, 3)})
        if rows and spy_w["ret_pct"] is not None:
            conc = {"holdings": sorted(rows, key=lambda x: x["contribution_pts"]),
                    "weight_pct": _r(wsum * 100, 1), "contribution_pts": _r(contrib, 2),
                    "index_ret_pct": spy_w["ret_pct"], "rest_pts": _r(spy_w["ret_pct"] - contrib, 2),
                    "note": "Weights from SPY's latest published top holdings; contribution = weight × window return."}

    movers = {"losers": [], "gainers": []}
    if len(w_ret):
        def _m(s):
            return {"symbol": s, "name": meta.get(s, {}).get("name", s), "sector": meta.get(s, {}).get("sector"),
                    "ret_pct": _r(w_ret[s], 2)}
        movers["losers"] = [_m(s) for s in w_ret.nsmallest(TOP_N_MOVERS).index]
        movers["gainers"] = [_m(s) for s in w_ret.nlargest(TOP_N_MOVERS).index]

    return {"available": True, "window_sessions": WINDOW, "spy": spy_w, "sectors": sectors, "factors": factors,
            "concentration": conc, "movers": movers,
            "classification": classify_selloff(sectors, factors, w_ret, spy_w, conc)}


def classify_selloff(sectors: list[dict], factors: list[dict], w_ret: pd.Series, spy_w: dict,
                     conc: dict | None) -> dict:
    """Rule-based label for the KIND of move. Every rule is listed with the data that fired."""
    fz = {f["id"]: f.get("z") for f in factors}
    fpts = {f["id"]: f.get("spread_pts") for f in factors}
    down_sectors = sum(1 for s in sectors if (s["ret_pct"] or 0) < 0)
    pct_down = float((w_ret < 0).mean() * 100) if len(w_ret) else None
    spy_ret = spy_w.get("ret_pct")
    hits: list[dict] = []

    def z(k):
        return fz.get(k)

    crowded = [k for k in ("mom_lowvol", "beta", "semis", "growth_value") if (z(k) or 0) <= -1.5]
    if spy_ret is not None and spy_ret < 0 and crowded and (fpts.get("equal_cap") or 0) > 0:
        hits.append({"id": "crowded_long_selling", "label": "Crowded-long selling",
                     "why": f"{', '.join(f['label'] for f in factors if f['id'] in crowded)} ≤ −1.5σ while the "
                            f"equal-weight index beat the cap-weighted one by {fpts['equal_cap']:+.2f} pts — "
                            "selling concentrated in recent winners, not the whole market."})
    if spy_ret is not None and spy_ret > 0 and (z("mom_lowvol") or 0) <= -1.5:
        hits.append({"id": "short_squeeze", "label": "Short squeeze / junk rally",
                     "why": f"The index rose while momentum lagged low-vol by {fpts['mom_lowvol']:+.2f} pts "
                            f"({z('mom_lowvol'):+.1f}σ) — past losers rallying hardest, the pattern of momentum crashes "
                            "(Daniel & Moskowitz 2016)."})
    if spy_ret is not None and spy_ret < 0 and pct_down is not None and pct_down >= 75 and down_sectors >= 9 \
            and (fpts.get("equal_cap") or 0) <= 0:
        hits.append({"id": "broad_liquidation", "label": "Broad liquidation",
                     "why": f"{pct_down:.0f}% of S&P 500 stocks fell and {down_sectors} of 11 sectors were down, "
                            "with the average stock doing no better than the index."})
    if (z("cyc_def") or 0) <= -1.5:
        hits.append({"id": "growth_scare", "label": "Defensive rotation",
                     "why": f"Cyclicals lagged defensives by {fpts['cyc_def']:+.2f} pts ({z('cyc_def'):+.1f}σ) — "
                            "money hiding in staples, utilities and health care."})
    if (z("small_large") or 0) <= -1.5:
        hits.append({"id": "small_caps", "label": "Small-cap / credit-sensitive selling",
                     "why": f"Russell 2000 lagged the S&P by {fpts['small_large']:+.2f} pts ({z('small_large'):+.1f}σ)."})
    if conc and spy_ret is not None and spy_ret < 0 and conc["contribution_pts"] is not None \
            and conc["contribution_pts"] / spy_ret >= 0.6:
        hits.append({"id": "megacap", "label": "Mega-cap driven",
                     "why": f"SPY's top {len(conc['holdings'])} holdings ({conc['weight_pct']:.0f}% of the index) "
                            f"delivered {conc['contribution_pts']:+.2f} of the {spy_ret:+.2f}% move."})
    if not hits:
        hits.append({"id": "mixed", "label": "Mixed / no dominant theme",
                     "why": "No factor spread moved more than 1.5σ and participation was not one-sided."})
    return {"primary": hits[0], "also": hits[1:],
            "context": {"sectors_down": down_sectors, "pct_stocks_down": _r(pct_down, 1)},
            "evidence": {"grade": "descriptive",
                         "note": ("These labels describe WHAT moved, not what comes next. Momentum crashes happen on index "
                                  "REBOUNDS after declines (Daniel & Moskowitz 2016); there is no peer-reviewed evidence on "
                                  "index returns 1–20 days after crowded-long selling days, and deleveraging unwinds have been "
                                  "short and partly reversed (Khandani & Lo 2007, one episode).")},
            "rules": ("Crowded-long selling = index down AND any of momentum/high-beta/semis/growth spreads ≤ −1.5σ AND equal-weight > cap-weight. "
                      "Short squeeze = index up AND momentum − low-vol ≤ −1.5σ. "
                      "Broad liquidation = ≥75% of stocks down AND ≥9/11 sectors down AND equal-weight ≤ cap-weight. "
                      "Defensive rotation = cyclicals−defensives ≤ −1.5σ. Mega-cap driven = top holdings ≥60% of the move.")}


# ---------------------------------------------------------------------------
# Tells + read (merged into the main monitor's panels by the frontend)
# ---------------------------------------------------------------------------

def build_internal_tells(breadth: dict) -> list[dict]:
    if not breadth.get("available"):
        return []
    L = breadth["last"]
    tells = [
        {"id": "nl_gt_nh", "side": "continuation", "label": "New lows outnumber new highs",
         "level": None, "value": None,
         "status": "met" if L["nl"] > L["nh"] else "not_met",
         "detail": f"Last session: {L['nh']} new 52-week highs vs {L['nl']} new lows (closing basis). Expanding new lows "
                   "mean the damage is spreading beyond a few names.", "basis": f"S&P 500 members, {L['date']}"},
        {"id": "down90", "side": "continuation", "label": "A 90% down-volume day",
         "level": None, "value": _r(100 - (L["up_vol_pct"] or 0), 1), "threshold": 90, "unit": "% down vol",
         "status": "met" if (L["up_vol_pct"] is not None and L["up_vol_pct"] <= 10) else "not_met",
         "detail": "When 90%+ of volume is in falling stocks, selling is indiscriminate (forced or panicked). "
                   "Value = last session's down-volume share.", "basis": f"S&P 500 members, {L['date']}"},
        {"id": "washout", "side": "recovery", "label": f"Breadth washout (≤{WASHOUT_20D:.0f}% above 20-day)",
         "level": None, "value": L["pct20"], "threshold": WASHOUT_20D, "unit": "%",
         "status": "met" if (L["pct20"] is not None and L["pct20"] <= WASHOUT_20D) else "not_met",
         "detail": "Selling has reached nearly every stock — exhaustive rather than selective. Conventional threshold.",
         "basis": f"S&P 500 members, {L['date']}"},
        {"id": "up90", "side": "recovery", "label": "A 90% up-volume day",
         "level": None, "value": L["up_vol_pct"], "threshold": 90, "unit": "% up vol",
         "status": "met" if (L["up_vol_pct"] is not None and L["up_vol_pct"] >= 90) else "watch",
         "detail": "Broad, urgent buying across the index — the classic sign a low has demand behind it.",
         "basis": "measure on the day"},
        {"id": "zweig", "side": "recovery", "label": "Zweig breadth thrust (practitioner)",
         "level": None, "value": L["zweig"], "threshold": ZWEIG_HIGH, "unit": "",
         "status": "met" if breadth.get("zweig_thrust_on") else "watch",
         "detail": f"10-day average of advancers/(adv+dec) jumping from below {ZWEIG_LOW} to above {ZWEIG_HIGH} within "
                   f"{ZWEIG_DAYS} sessions — rare; marks a market-wide surge in participation."
                   + (f" Fired {breadth['zweig_thrust_on']}." if breadth.get("zweig_thrust_on") else ""),
         "basis": "10-day EMA"},
    ]
    from .selloff_evidence import tell_evidence
    for t in tells:
        t["evidence"] = tell_evidence(t["id"])            # same registry as the evidence panel
    return tells


def build_internal_read(breadth: dict, anatomy: dict) -> list[str]:
    out: list[str] = []
    if breadth.get("available"):
        w, L = breadth["window"], breadth["last"]
        out.append(f"Participation: {w['pct_down']:.0f}% of S&P 500 stocks fell over the last {w['sessions']} sessions "
                   f"(median stock {w['median_pct']:+.2f}%); the share above their 50-day average went from "
                   f"{L['pct50_before']:.0f}% to {L['pct50']:.0f}%, and last session had {L['nh']} new highs vs "
                   f"{L['nl']} new lows.")
    if anatomy.get("available"):
        cl = anatomy["classification"]
        line = f"What got sold — {cl['primary']['label']}: {cl['primary']['why']}"
        if cl["also"]:
            line += " Also: " + "; ".join(h["label"] for h in cl["also"]) + "."
        out.append(line)
    return out


def build_market_internals(data: dict, constituents: list[dict]) -> dict:
    """Pure assembly (no network) — unit-testable with synthetic panels."""
    closes, volumes = data.get("closes", pd.DataFrame()), data.get("volumes", pd.DataFrame())
    breadth = build_breadth(closes, volumes) if not closes.empty else \
        {"available": False, "reason": "Constituent prices unavailable."}
    anatomy = build_anatomy(closes, constituents, data.get("etf") or {}, data.get("holdings") or [])
    if not breadth.get("available") and not anatomy.get("available"):
        return {"available": False, "reason": breadth.get("reason") or anatomy.get("reason")}
    return {
        "available": True,
        "as_of": _dt.datetime.now().strftime("%Y-%m-%d %H:%M"),
        "last_session": closes.index[-1].date().isoformat() if not closes.empty else None,
        "breadth": breadth, "anatomy": anatomy,
        "tells": build_internal_tells(breadth),
        "read": build_internal_read(breadth, anatomy),
    }


async def get_market_internals(constituents: list[dict]) -> dict:
    import asyncio
    try:
        data = await asyncio.to_thread(fetch_internals_data, constituents)
        result = await asyncio.to_thread(build_market_internals, data, constituents)
        del data
        return result
    finally:
        release_memory()
