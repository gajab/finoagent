"""Shared correlated-assets registry — DB-cache-backed, lazily built, refreshed with recent data.

One place that knows, for any ticker: its sector/industry, the market THEMES it belongs to (crypto,
gold complex, AI-data-center, semis, energy, rates-sensitive financials, …), the macro factors it
loads on (rates / oil / gold / USD / crypto / AI-capex), any dependency chain (GDX & GDXJ track GLD;
COIN & MSTR track Bitcoin), and its correlated peer universe. Used by BOTH Book Exposure and
Tax-Loss Harvesting so the two surfaces agree.

Design (as requested):
  • LAZY — the first time a ticker is asked for, we discover its structural profile (sector via
    yfinance, theme membership from the curated map, peer candidates via the TLH sector-peer maps)
    and STORE it in the DB cache.
  • REFRESH — the measured correlation matrix is recomputed from RECENT price data on read (short TTL),
    over the stored peer universe, so ρ never goes stale even though the structural profile is cached long.
  • DETERMINISTIC — no LLM. A curated theme/peer map supplies CANDIDATES; measured ρ CONFIRMS them.
"""
from __future__ import annotations

import hashlib
import json
import logging
import math
from typing import Any, Optional

from sqlalchemy.ext.asyncio import AsyncSession

from .cache_service import get_cached, set_cached

logger = logging.getLogger(__name__)

_PROFILE_TTL = 30 * 86400   # structural profile — sector/themes/peers change slowly
_CORR_TTL = 2 * 86400       # measured ρ — refreshed from recent data every couple of days

# ─────────────────────────────────────────────────────────────────────────────
# Themes — structural co-movement groups with their driver, bellwether, macro factors and (where it
# applies) a dependency chain. Membership is deliberately conservative and CORRECT (a name only sits
# in a theme when it genuinely rides that driver); measured ρ still has the final say downstream.
# ─────────────────────────────────────────────────────────────────────────────
_THEMES: dict[str, dict[str, Any]] = {
    "crypto": {
        "label": "Crypto / Bitcoin beta", "driver": "Bitcoin & crypto prices", "bellwether": "COIN",
        "etf": "BITO", "macro": ["crypto", "risk_on"],
        "note": "COIN, MSTR, HOOD, miners all track Bitcoin — one crypto move hits them together.",
        "tickers": {"COIN", "HOOD", "MSTR", "MSTU", "MARA", "RIOT", "CLSK", "BITF", "WULF", "CIFR",
                    "HUT", "BTBT", "BITO", "IBIT", "GBTC", "ETHE", "SQ", "XYZ"},
    },
    "gold_complex": {
        "label": "Gold complex", "driver": "Gold spot (GLD)", "bellwether": "GLD", "etf": "GDX",
        "macro": ["gold", "real_rates", "usd_inverse"], "driver_ticker": "GLD",
        "note": "GDX / GDXJ and the miners are levered to GLD; GLD itself moves on real rates & the Fed.",
        "tickers": {"GLD", "IAU", "GDX", "GDXJ", "NEM", "AEM", "GOLD", "FNV", "WPM", "KGC", "AU",
                    "PAAS", "AG", "HL", "RGLD", "SIL", "SILJ", "SLV"},
    },
    "ai_datacenter": {
        "label": "AI data-center buildout", "driver": "AI / data-center capex", "bellwether": "NVDA",
        "etf": "SMH", "macro": ["ai_capex", "risk_on"],
        "note": "Chips, networking, power & cooling for AI data centers — all keyed to hyperscaler capex; NVDA is the tell.",
        "tickers": {"NVDA", "AMD", "AVGO", "MRVL", "TSM", "MU", "SMCI", "DELL", "VRT", "ANET", "CRDO",
                    "ALAB", "CDNS", "SNPS", "ARM", "CRWV", "NBIS", "SMH", "SOXX", "ETN", "POWL", "GEV"},
    },
    "semis": {
        "label": "Semiconductors", "driver": "The semiconductor cycle", "bellwether": "NVDA",
        "etf": "SMH", "macro": ["ai_capex", "cyclical", "risk_on"],
        "tickers": {"NVDA", "AMD", "AVGO", "MU", "TSM", "QCOM", "INTC", "TXN", "ADI", "NXPI", "ON",
                    "MCHP", "MRVL", "LRCX", "AMAT", "KLAC", "ASML", "ARM", "SMH", "SOXX", "SOXL"},
    },
    "megacap_tech": {
        "label": "Mega-cap tech", "driver": "Nasdaq-100 beta", "bellwether": "AAPL", "etf": "QQQ",
        "macro": ["risk_on", "rates"],
        "tickers": {"AAPL", "MSFT", "GOOGL", "GOOG", "AMZN", "META", "NFLX", "QQQ", "XLK", "VGT"},
    },
    "ai_software": {
        "label": "AI / growth software", "driver": "Software multiples & AI adoption", "bellwether": "CRM",
        "etf": "IGV", "macro": ["risk_on", "rates"],
        "tickers": {"PLTR", "SNOW", "CRM", "NOW", "MDB", "DDOG", "NET", "CRWD", "PANW", "ZS", "OKTA",
                    "S", "APP", "SHOP", "ORCL", "ADBE", "IGV"},
    },
    "energy": {
        "label": "Energy / oil & gas", "driver": "Crude oil (WTI/Brent)", "bellwether": "XOM", "etf": "XLE",
        "macro": ["oil", "inflation", "cyclical"],
        "tickers": {"XOM", "CVX", "COP", "OXY", "EOG", "DVN", "FANG", "HES", "SLB", "HAL", "MPC",
                    "PSX", "VLO", "WMB", "KMI", "OKE", "USO", "XLE", "XOP", "OIH"},
    },
    "banks_rates": {
        "label": "Banks / rate-sensitive financials", "driver": "Yield curve & credit", "bellwether": "JPM",
        "etf": "XLF", "macro": ["rates", "cyclical", "credit"],
        "tickers": {"JPM", "BAC", "WFC", "C", "GS", "MS", "USB", "PNC", "TFC", "SCHW", "COF", "AXP",
                    "XLF", "KRE", "KBE"},
    },
    "rate_duration": {
        "label": "Long-duration rates (bonds / utilities / REITs)", "driver": "Long-end Treasury yields",
        "bellwether": "TLT", "etf": "TLT", "macro": ["rates", "real_rates"], "driver_ticker": "TLT",
        "tickers": {"TLT", "IEF", "ZROZ", "EDV", "TMF", "VNQ", "XLU", "AMT", "PLD", "O", "NEE", "DUK", "SO"},
    },
    "china": {
        "label": "China / EM tech", "driver": "China policy & growth", "bellwether": "BABA", "etf": "FXI",
        "macro": ["china", "risk_on"],
        "tickers": {"BABA", "PDD", "JD", "BIDU", "NIO", "LI", "XPEV", "KWEB", "FXI", "MCHI", "YINN"},
    },
    "consumer_retail": {
        "label": "Consumer / retail", "driver": "US consumer spending", "bellwether": "WMT", "etf": "XRT",
        "macro": ["consumer", "cyclical", "inflation"],
        "tickers": {"WMT", "TGT", "COST", "HD", "LOW", "NKE", "SBUX", "MCD", "DIS", "TJX", "LULU", "XRT"},
    },
}

# Reverse index ticker → theme keys.
_TICKER_THEMES: dict[str, list[str]] = {}
for _k, _v in _THEMES.items():
    for _t in _v["tickers"]:
        _TICKER_THEMES.setdefault(_t, []).append(_k)

# Macro proxies — a factor's price ETF, so we can MEASURE a name's loading on it (no hand-waving).
_MACRO_PROXIES: list[dict[str, str]] = [
    {"factor": "rates", "label": "Interest rates", "proxy": "TLT", "sign": "-1"},   # bonds fall as rates rise
    {"factor": "oil", "label": "Oil", "proxy": "USO", "sign": "+1"},
    {"factor": "gold", "label": "Gold", "proxy": "GLD", "sign": "+1"},
    {"factor": "usd", "label": "US dollar", "proxy": "UUP", "sign": "+1"},
    {"factor": "crypto", "label": "Crypto", "proxy": "BITO", "sign": "+1"},
    {"factor": "ai_capex", "label": "AI / semis", "proxy": "SMH", "sign": "+1"},
    {"factor": "market", "label": "Market (SPY)", "proxy": "SPY", "sign": "+1"},
]
_MACRO_PROXY_TICKERS = [m["proxy"] for m in _MACRO_PROXIES]
_FACTOR_LABEL = {m["factor"]: m["label"] for m in _MACRO_PROXIES}
_FACTOR_LABEL.update({"risk_on": "broad risk appetite", "real_rates": "real interest rates",
                      "usd_inverse": "a weaker US dollar", "cyclical": "the economic cycle",
                      "inflation": "inflation", "credit": "credit spreads", "china": "China",
                      "consumer": "the US consumer"})


def _norm(t: Any) -> str:
    return str(t or "").strip().upper()


def themes_for(ticker: str) -> list[str]:
    return _TICKER_THEMES.get(_norm(ticker), [])


def theme_peers(ticker: str, max_peers: int = 40) -> list[str]:
    """Theme-based peer candidates (crypto / gold complex / AI data-center / semis / …) — the SHARED
    curated knowledge both Book Exposure and Tax-Loss Harvesting consult. Pure: no yfinance, no DB."""
    tk = _norm(ticker)
    out: list[str] = []
    for k in themes_for(tk):
        out.extend(p for p in _THEMES[k]["tickers"] if _norm(p) != tk)
    return list(dict.fromkeys(_norm(p) for p in out))[:max_peers]


# ─────────────────────────────────────────────────────────────────────────────
# Correlation primitives (shared).
# ─────────────────────────────────────────────────────────────────────────────
def pearson(a: list[float], b: list[float], min_n: int = 40) -> Optional[float]:
    n = min(len(a), len(b))
    if n < min_n:
        return None
    a, b = a[-n:], b[-n:]
    ma, mb = sum(a) / n, sum(b) / n
    va = sum((x - ma) ** 2 for x in a)
    vb = sum((x - mb) ** 2 for x in b)
    if va <= 0 or vb <= 0:
        return None
    cov = sum((a[i] - ma) * (b[i] - mb) for i in range(n))
    return max(-1.0, min(1.0, cov / math.sqrt(va * vb)))


def _fetch_returns_sync(tickers: list[str], period: str = "1y") -> dict[str, list[float]]:
    """Trailing daily simple returns per ticker (one batched download). Missing names drop out."""
    out: dict[str, list[float]] = {}
    uniq = [t for t in dict.fromkeys(_norm(t) for t in tickers) if t]
    if not uniq:
        return out
    try:
        import yfinance as yf
        data = yf.download(uniq, period=period, interval="1d", auto_adjust=True, progress=False)["Close"]
        if data is None or getattr(data, "empty", True):
            return out
        multi = hasattr(data, "columns")
        for t in uniq:
            try:
                s = data[t] if (multi and t in getattr(data, "columns", [])) else (data if len(uniq) == 1 else None)
                if s is None:
                    continue
                s = s.dropna()
                if len(s) < 40:
                    continue
                vals = [float(x) for x in s.tolist()]
                out[t] = [(vals[i] / vals[i - 1] - 1.0) for i in range(1, len(vals)) if vals[i - 1] > 0]
            except Exception:
                continue
    except Exception as exc:  # noqa: BLE001 — best-effort
        logger.info("corr-assets: return fetch failed: %s", exc)
    return out


async def fetch_returns(tickers: list[str], db: AsyncSession, period: str = "1y") -> dict[str, list[float]]:
    """Batched daily returns for a set of tickers, cached briefly so repeated reads in a day reuse it.
    THIS is the 'recompute the correlation matrix with recent data' primitive both surfaces call."""
    import asyncio
    uniq = sorted({_norm(t) for t in tickers if _norm(t)})
    if not uniq:
        return {}
    key = f"corrassets:returns:{period}:{hashlib.md5(','.join(uniq).encode()).hexdigest()[:16]}:v1"
    cached = await get_cached(db, key)
    if isinstance(cached, dict) and cached:
        return cached
    out = await asyncio.to_thread(_fetch_returns_sync, uniq, period)
    if out:
        await set_cached(db, key, out, ttl_seconds=_CORR_TTL)
    return out


# ─────────────────────────────────────────────────────────────────────────────
# The registry — structural profile per ticker, lazily discovered and cached long.
# ─────────────────────────────────────────────────────────────────────────────
def _discover_profile_sync(ticker: str) -> dict:
    """Structural profile (no correlation yet): sector/industry + theme membership + peer candidates."""
    tk = _norm(ticker)
    # Reuse the TLH sector/industry lookup + curated peer maps (single source of the peer universe).
    from .tax_loss_harvesting_service import _get_ticker_info, _get_sector_peers
    info = {}
    try:
        info = _get_ticker_info(tk) or {}
    except Exception:
        info = {}
    sector = (info.get("sector") or "").strip()
    industry = (info.get("industry") or "").strip()

    theme_keys = themes_for(tk)
    themes = [{"key": k, "label": _THEMES[k]["label"], "driver": _THEMES[k]["driver"],
               "bellwether": _THEMES[k]["bellwether"], "note": _THEMES[k].get("note")}
              for k in theme_keys]
    macro_factors = sorted({f for k in theme_keys for f in _THEMES[k]["macro"]})

    # Dependency chains this name participates in (e.g. GDX depends on GLD).
    depends_on = []
    for k in theme_keys:
        dt = _THEMES[k].get("driver_ticker")
        if dt and _norm(dt) != tk:
            depends_on.append({"driver": dt, "theme": _THEMES[k]["label"]})

    # Peer candidates: curated theme peers + TLH sector/industry peers (measured ρ confirms later).
    peers: list[str] = list(theme_peers(tk))
    try:
        peers.extend(p for p in _get_sector_peers(tk) if _norm(p) != tk)
    except Exception:
        pass
    peer_candidates = list(dict.fromkeys(_norm(p) for p in peers))[:60]

    return {
        "ticker": tk, "sector": sector, "industry": industry,
        "themes": themes, "theme_keys": theme_keys,
        "macro_factors": macro_factors, "depends_on": depends_on,
        "peer_candidates": peer_candidates,
    }


async def get_asset_profile(ticker: str, db: AsyncSession) -> dict:
    """Lazily-built, DB-cached structural profile for a ticker (sector, themes, macro factors, peers)."""
    import asyncio
    tk = _norm(ticker)
    if not tk:
        return {"ticker": tk, "sector": "", "industry": "", "themes": [], "theme_keys": [],
                "macro_factors": [], "depends_on": [], "peer_candidates": []}
    key = f"corrassets:profile:{tk}:v1"
    cached = await get_cached(db, key)
    if isinstance(cached, dict) and cached.get("ticker"):
        return cached
    profile = await asyncio.to_thread(_discover_profile_sync, tk)
    await set_cached(db, key, profile, ttl_seconds=_PROFILE_TTL)
    return profile


async def get_profiles(tickers: list[str], db: AsyncSession) -> dict[str, dict]:
    """Profiles for many tickers (each individually cached).

    DB access is SEQUENTIAL on the shared session. An ``AsyncSession`` is NOT safe for
    concurrent use, so fanning out ``get_asset_profile(t, db)`` with ``asyncio.gather`` on
    one session collides its flush/commit ("Session.add() within the execution stage of the
    flush", "commit() can't be called here", "this transaction is closed") — every miss then
    fails to cache and can poison the session for the rest of the request. Only the CPU/
    network-bound discovery is parallelised (in threads); cache reads/writes happen one at a time.
    """
    import asyncio
    uniq = list(dict.fromkeys(_norm(t) for t in tickers if _norm(t)))
    out: dict[str, dict] = {}
    misses: list[str] = []
    # 1) Sequential cache reads on the shared session.
    for t in uniq:
        cached = await get_cached(db, f"corrassets:profile:{t}:v1")
        if isinstance(cached, dict) and cached.get("ticker"):
            out[t] = cached
        else:
            misses.append(t)
    # 2) Parallel discovery (no DB touched) for the misses only.
    if misses:
        discovered = await asyncio.gather(
            *[asyncio.to_thread(_discover_profile_sync, t) for t in misses],
            return_exceptions=True,
        )
        # 3) Sequential cache writes on the shared session.
        for t, prof in zip(misses, discovered):
            if isinstance(prof, dict) and prof.get("ticker"):
                out[t] = prof
                await set_cached(db, f"corrassets:profile:{t}:v1", prof, ttl_seconds=_PROFILE_TTL)
    return out


async def sector_peers(ticker: str, db: AsyncSession) -> list[str]:
    """Peer candidate universe for a ticker — the shared entry TLH uses (theme peers + sector peers)."""
    prof = await get_asset_profile(ticker, db)
    return prof.get("peer_candidates", [])


# ─────────────────────────────────────────────────────────────────────────────
# Related-company earnings inside a trade window — a REAL catalyst (from yfinance), e.g. NVDA prints
# → every AI-data-center / semis name can gap together. Only the bellwether of a theme the candidate
# (or a book name) belongs to is checked, so it's targeted, not noise.
# ─────────────────────────────────────────────────────────────────────────────
def _next_earnings_sync(ticker: str) -> Optional[str]:
    try:
        import yfinance as yf
        cal = yf.Ticker(ticker).calendar
        dt = None
        if isinstance(cal, dict):
            ev = cal.get("Earnings Date")
            dt = (ev[0] if isinstance(ev, (list, tuple)) and ev else ev)
        if dt is None:
            return None
        if hasattr(dt, "date"):
            dt = dt.date()
        return str(dt)
    except Exception:
        return None


async def related_earnings_in_window(theme_keys_present: set[str], candidate_tk: str,
                                     horizon_days: int, db: AsyncSession) -> list[dict]:
    """For each theme present in the book+candidate, if its bellwether reports within `horizon_days`,
    flag it. Cached per (bellwether) so we don't refetch. Excludes the candidate's own ticker."""
    import asyncio
    import datetime as _dt
    bells = []
    for k in theme_keys_present:
        b = _THEMES.get(k, {}).get("bellwether")
        if b and _norm(b) != _norm(candidate_tk):
            bells.append((_norm(b), k))
    bells = list(dict.fromkeys(bells))
    if not bells:
        return []
    today = _dt.date.today()
    out: list[dict] = []
    for bell, theme_key in bells:
        key = f"corrassets:earn:{bell}:v1"
        ds = await get_cached(db, key)
        if not isinstance(ds, dict):
            date_str = await asyncio.to_thread(_next_earnings_sync, bell)
            ds = {"date": date_str}
            await set_cached(db, key, ds, ttl_seconds=_CORR_TTL)
        date_str = ds.get("date")
        if not date_str:
            continue
        try:
            d = _dt.date.fromisoformat(date_str[:10])
        except Exception:
            continue
        days = (d - today).days
        if 0 <= days <= horizon_days:
            out.append({"bellwether": bell, "theme": _THEMES[theme_key]["label"],
                        "date": date_str[:10], "days_out": days,
                        "driver": _THEMES[theme_key]["driver"]})
    out.sort(key=lambda x: x["days_out"])
    return out
