"""Debt Radar — entry-timing engine for debt instruments.

Given a debt-instrument ticker (CLO/bank-loan/IG/HY/Treasury/MBS/muni/EM-debt
ETF, fully generalisable), this service:

1. **Classifies** the instrument from yfinance metadata (``classify_instrument``).
2. Pulls a **live block** (price, NAV premium/discount, bid/ask, distribution
   yield, AUM) and a **macro block** (FRED rates/spreads + MOVE/VIX stress).
3. Scores entry timing across **seven signal families** (A–G), each 0–100, then
   blends them with an instrument-class-specific weight profile into a single
   composite **Entry Score** + verdict.

Every signal degrades gracefully: missing data ⇒ ``score=None`` and the family
is dropped from the (renormalised) composite, with a confidence flag surfaced.

Data sources are 100% free / no-API-key: yfinance + the keyless FRED CSV endpoint
(see ``rates_service``).  True CLO OAS is not freely available, so valuation uses
a documented ETF-implied **carry-spread proxy** (distribution yield − SOFR).
"""

from __future__ import annotations

import asyncio
import logging
import re
from datetime import datetime, timezone, timedelta, date

import httpx
import pandas as pd
import yfinance as yf

from . import rates_service as rates
from .stock_service import _san
from .llm_service import call_llm

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Instrument classes & weight profiles (over families A,B,C,D,E,F,G)
# ---------------------------------------------------------------------------
# A Valuation/RelVal · B Carry/Roll · C Macro/RateRegime · D CreditCycle ·
# E Momentum/Technical · F Volatility/Stress · G Liquidity/Flows
FAMILY_KEYS = ["valuation", "carry", "macro", "credit_cycle", "momentum", "volatility", "liquidity"]

_PROFILES: dict[str, dict[str, float]] = {
    #                     val   carry macro credit mom   vol   liq
    "clo_floating":  dict(valuation=.20, carry=.20, macro=.15, credit_cycle=.15, momentum=.05, volatility=.15, liquidity=.10),
    "bank_loan":     dict(valuation=.20, carry=.20, macro=.15, credit_cycle=.15, momentum=.07, volatility=.13, liquidity=.10),
    "ig_corp":       dict(valuation=.22, carry=.15, macro=.15, credit_cycle=.18, momentum=.12, volatility=.08, liquidity=.10),
    "hy_corp":       dict(valuation=.20, carry=.12, macro=.12, credit_cycle=.25, momentum=.15, volatility=.08, liquidity=.08),
    "treasury":      dict(valuation=.10, carry=.12, macro=.30, credit_cycle=.03, momentum=.25, volatility=.12, liquidity=.08),
    "mbs_agency":    dict(valuation=.20, carry=.15, macro=.20, credit_cycle=.10, momentum=.12, volatility=.13, liquidity=.10),
    "muni":          dict(valuation=.20, carry=.15, macro=.20, credit_cycle=.12, momentum=.13, volatility=.10, liquidity=.10),
    "em_debt":       dict(valuation=.20, carry=.15, macro=.15, credit_cycle=.20, momentum=.12, volatility=.10, liquidity=.08),
    "generic_debt":  dict(valuation=.18, carry=.15, macro=.18, credit_cycle=.15, momentum=.14, volatility=.10, liquidity=.10),
}

_CLASS_LABELS = {
    "clo_floating": "CLO ETF (Floating-Rate)",
    "bank_loan": "Senior / Bank-Loan ETF (Floating-Rate)",
    "ig_corp": "Investment-Grade Corporate",
    "hy_corp": "High-Yield Corporate",
    "treasury": "U.S. Treasury / Government",
    "mbs_agency": "Agency MBS",
    "muni": "Municipal",
    "em_debt": "Emerging-Market Debt",
    "generic_debt": "Bond / Fixed-Income Fund",
}
_FLOATING = {"clo_floating", "bank_loan"}

FAMILY_LABELS = {
    "valuation": "Valuation / Relative Value",
    "carry": "Carry & Roll",
    "macro": "Macro / Rate Regime",
    "credit_cycle": "Credit Cycle / Fundamentals",
    "momentum": "Momentum / Technical",
    "volatility": "Volatility / Stress",
    "liquidity": "Liquidity & Flows",
}


# ---------------------------------------------------------------------------
# Small scoring helpers
# ---------------------------------------------------------------------------
def _clamp(x: float, lo: float = 0.0, hi: float = 100.0) -> float:
    return max(lo, min(hi, x))


def _band(x, lo, hi):
    """Linear map x∈[lo,hi] → [0,100], clamped. (favorable as x rises)."""
    if x is None:
        return None
    if hi == lo:
        return 50.0
    return _clamp((x - lo) / (hi - lo) * 100.0)


def _band_inv(x, lo, hi):
    """Linear map where favorable as x falls (x=lo→100, x=hi→0)."""
    s = _band(x, lo, hi)
    return None if s is None else 100.0 - s


def _z_to_score(z):
    """Z-score → 0–100. z=+1.5→90, 0→50, −1.5→10 (favorable as z rises)."""
    if z is None:
        return None
    return _clamp(50.0 + z * (40.0 / 1.5))


def _avg(vals):
    vals = [v for v in vals if v is not None]
    return sum(vals) / len(vals) if vals else None


def _verdict(score):
    if score is None:
        return "N/A"
    if score >= 70:
        return "Favorable"
    if score >= 45:
        return "Neutral"
    return "Unfavorable"


def _pct_rank(values: list[float], x: float) -> float | None:
    vals = [v for v in values if v is not None]
    if len(vals) < 8 or x is None:
        return None
    return round(100.0 * sum(1 for v in vals if v <= x) / len(vals), 1)


def _fmt_aum(v):
    if not v:
        return None
    v = float(v)
    if v >= 1e12:
        return f"${v/1e12:.2f}T"
    if v >= 1e9:
        return f"${v/1e9:.2f}B"
    if v >= 1e6:
        return f"${v/1e6:.0f}M"
    return f"${v:,.0f}"


# ---------------------------------------------------------------------------
# Classification
# ---------------------------------------------------------------------------
_REJECT_MSG = ("This doesn't look like a debt instrument. Enter a bond / credit ETF "
               "(e.g. PAAA, ICLO, JAAA, JBBB, HYG, LQD, TLT, AGG, MBB, MUB).")


def classify_instrument(info: dict, summary: str, asset_classes: dict) -> dict:
    """Return classification dict, including ``is_debt`` + ``reject_reason``."""
    text = f"{summary} {info.get('category','')} {info.get('longName','')} {info.get('shortName','')}".lower()
    quote_type = (info.get("quoteType") or "").upper()
    bond_pos = float(asset_classes.get("bondPosition") or 0)

    def has(*words):
        # word-boundary match so short tokens (clo, mbs, muni) don't substring-hit
        # unrelated words ("clo" in "iCloud", "mbs" in "thumbs").
        return any(re.search(rf"\b{re.escape(w)}\b", text) for w in words)

    is_clo = has("clo", "clos") or "collateralized loan" in text

    # Single stocks are never debt instruments; gate funds on bond exposure/keywords.
    if quote_type == "EQUITY":
        return {"is_debt": False, "reject_reason": _REJECT_MSG}
    debt_like = (
        bond_pos >= 0.5 or is_clo
        or has("bond", "treasury", "municipal", "muni", "securitized", "sovereign")
        or has("senior loan", "bank loan", "leveraged loan", "floating rate", "floating-rate")
        or has("mortgage-backed", "agency mbs", "mbs")
        or "fixed income" in text or "fixed-income" in text or "high yield" in text
        or "high-yield" in text or "government bond" in text or "corporate bond" in text
    )
    if not debt_like:
        return {"is_debt": False, "reject_reason": _REJECT_MSG}

    # Determine class (order matters — most specific first)
    if is_clo:
        cls = "clo_floating"
    elif has("senior loan", "bank loan", "leveraged loan") or "floating rate" in text or "floating-rate" in text:
        cls = "bank_loan"
    elif has("municipal", "muni") or "tax-exempt" in text or "tax exempt" in text:
        cls = "muni"
    elif "mortgage-backed" in text or "agency mbs" in text or has("mbs"):
        cls = "mbs_agency"
    elif (has("sovereign") or "emerging market" in text or "emerging-market" in text) and has("bond", "debt"):
        cls = "em_debt"
    elif "high yield" in text or "high-yield" in text or has("junk"):
        cls = "hy_corp"
    elif has("treasury") or "government bond" in text or "u.s. government" in text or "us government" in text:
        cls = "treasury"
    elif "investment grade" in text or "investment-grade" in text or "corporate bond" in text:
        cls = "ig_corp"
    else:
        cls = "generic_debt"

    # Tier hint for tranched products (CLO/bank-loan)
    tier = None
    if cls in ("clo_floating", "bank_loan"):
        if has("aaa"):
            tier = "AAA (senior)"
        elif has("bbb", "bb ", "mezzanine", "below investment"):
            tier = "Mezzanine (BBB/BB)"
        else:
            tier = "Mixed / Senior"

    return {
        "is_debt": True,
        "reject_reason": None,
        "instrument_class": cls,
        "class_label": _CLASS_LABELS[cls],
        "rate_type": "floating" if cls in _FLOATING else "fixed",
        "tier": tier,
    }


# ---------------------------------------------------------------------------
# Synchronous yfinance fetchers (wrapped in to_thread by the orchestrator)
# ---------------------------------------------------------------------------
def _fetch_live_sync(ticker: str) -> dict:
    tk = yf.Ticker(ticker)
    info = tk.info or {}

    asset_classes = {}
    top_holdings = []
    try:
        fd = tk.funds_data
        ac = fd.asset_classes
        if ac:
            asset_classes = {k: round(float(v) * 100, 2) for k, v in ac.items() if v}
        th = fd.top_holdings
        if th is not None and not th.empty:
            for sym, row in th.iterrows():
                top_holdings.append({
                    "ticker": str(sym),
                    "name": str(row.get("Name", sym)),
                    "weight_pct": round(float(row.get("Holding Percent", 0)) * 100, 2),
                })
    except Exception:
        pass

    price = info.get("regularMarketPrice") or info.get("previousClose")
    nav = info.get("navPrice")
    bid = info.get("bid")
    ask = info.get("ask")
    yld = info.get("yield") or info.get("trailingAnnualDividendYield")

    premium_discount = None
    if price and nav:
        premium_discount = round((float(price) - float(nav)) / float(nav) * 100, 3)
    bid_ask_bps = None
    if bid and ask and ask > 0 and bid > 0:
        mid = (float(bid) + float(ask)) / 2
        bid_ask_bps = round((float(ask) - float(bid)) / mid * 10000, 1)

    return {
        "info": {k: info.get(k) for k in (
            "quoteType", "legalType", "category", "fundFamily", "longName",
            "shortName", "longBusinessSummary")},
        "asset_classes": asset_classes,
        "top_holdings": top_holdings,
        "live": {
            "price": _f(price), "nav": _f(nav),
            "premium_discount_pct": premium_discount,
            "bid": _f(bid), "ask": _f(ask), "bid_ask_bps": bid_ask_bps,
            "distribution_yield_pct": round(float(yld) * 100, 2) if yld else None,
            "aum": info.get("totalAssets") or info.get("netAssets"),
            "aum_fmt": _fmt_aum(info.get("totalAssets") or info.get("netAssets")),
            "expense_ratio_pct": round(float(info.get("annualReportExpenseRatio")) * 100, 3)
                if info.get("annualReportExpenseRatio") else None,
        },
    }


def _f(v):
    try:
        return float(v) if v is not None else None
    except (TypeError, ValueError):
        return None


def _fetch_tech_and_spread_sync(ticker: str, sofr_series: list[dict]) -> dict:
    """Technicals (50/200-DMA, 12-1 momentum, RSI) + reconstructed implied-spread
    history for the valuation z-score. Single price-history pull."""
    tk = yf.Ticker(ticker)
    out = {"tech": {}, "spread_hist": []}
    try:
        hist = tk.history(period="3y", auto_adjust=True)
    except Exception:
        hist = None

    if hist is not None and not hist.empty and "Close" in hist:
        close = hist["Close"].dropna()
        try:
            close.index = close.index.tz_localize(None)
        except (TypeError, AttributeError):
            pass
        px = float(close.iloc[-1])
        sma50 = float(close.rolling(50).mean().iloc[-1]) if len(close) >= 50 else None
        sma200 = float(close.rolling(200).mean().iloc[-1]) if len(close) >= 200 else None
        mom_12_1 = None
        if len(close) >= 252:
            try:
                mom_12_1 = round((float(close.iloc[-21]) / float(close.iloc[-252]) - 1) * 100, 2)
            except Exception:
                mom_12_1 = None
        rsi14 = None
        if len(close) >= 15:
            delta = close.diff()
            gain = delta.clip(lower=0).rolling(14).mean()
            loss = (-delta.clip(upper=0)).rolling(14).mean()
            rs = gain / loss
            rsi_series = 100 - 100 / (1 + rs)
            if pd.notna(rsi_series.iloc[-1]):
                rsi14 = round(float(rsi_series.iloc[-1]), 1)
        out["tech"] = {
            "price": round(px, 2),
            "sma50": round(sma50, 2) if sma50 else None,
            "sma200": round(sma200, 2) if sma200 else None,
            "pct_above_200dma": round((px / sma200 - 1) * 100, 2) if sma200 else None,
            "golden_cross": (sma50 is not None and sma200 is not None and sma50 >= sma200),
            "mom_12_1_pct": mom_12_1,
            "rsi14": rsi14,
        }

        # ── reconstruct implied carry-spread history (monthly) ──────────────
        try:
            divs = tk.dividends
            if divs is not None and not divs.empty:
                try:
                    divs.index = divs.index.tz_localize(None)
                except (TypeError, AttributeError):
                    pass
                m_close = close.groupby(close.index.to_period("M")).last()
                m_div = divs.groupby(divs.index.to_period("M")).sum()
                ttm = m_div.rolling(12).sum()
                sofr_lookup = _SofrLookup(sofr_series)
                series = []
                for period, ttm_val in ttm.items():
                    if pd.isna(ttm_val) or period not in m_close.index:
                        continue
                    pxm = float(m_close.loc[period])
                    if pxm <= 0:
                        continue
                    ttm_yield = float(ttm_val) / pxm  # fraction
                    month_end = period.to_timestamp(how="end").date().isoformat()
                    sofr_v = sofr_lookup.at(month_end)
                    if sofr_v is None:
                        continue
                    spread_bps = ttm_yield * 10000 - sofr_v * 100
                    series.append({"date": month_end, "value": round(spread_bps, 1)})
                out["spread_hist"] = series[-36:]  # cap ~3y
        except Exception as exc:
            logger.debug("spread reconstruction failed for %s: %s", ticker, exc)

    return out


class _SofrLookup:
    """As-of (last value on/before date) lookup over a FRED series."""
    def __init__(self, series: list[dict]):
        self._dates = [p["date"] for p in series]
        self._vals = [p["value"] for p in series]

    def at(self, iso_date: str):
        import bisect
        i = bisect.bisect_right(self._dates, iso_date)
        return self._vals[i - 1] if i > 0 else None


def _fetch_stress_sync() -> dict:
    """MOVE (rate vol) + VIX (equity vol): latest + ~1y percentile."""
    out = {}
    for label, sym in (("move", "^MOVE"), ("vix", "^VIX")):
        try:
            h = yf.Ticker(sym).history(period="1y")["Close"].dropna()
            if not h.empty:
                last = float(h.iloc[-1])
                out[label] = round(last, 2)
                out[f"{label}_pct"] = _pct_rank([float(x) for x in h.values], last)
        except Exception:
            out[label] = None
            out[f"{label}_pct"] = None
    return out


# ---------------------------------------------------------------------------
# Signal families
# ---------------------------------------------------------------------------
def _metric(label, value, hint=None):
    return {"label": label, "value": value, "hint": hint}


def _signal(key, score, headline, metrics, confidence="medium"):
    return {
        "key": key,
        "family": FAMILY_LABELS[key],
        "score": None if score is None else round(score, 1),
        "verdict": _verdict(score),
        "headline": headline,
        "metrics": metrics,
        "confidence": confidence,
    }


def _sig_valuation(cls, live, fred, spread_hist):
    """A — implied carry-spread z-score + cross-sector premium + real-yield + credit cheapness."""
    yld = live.get("distribution_yield_pct")
    sofr = rates.latest(fred["SOFR"])
    oas_aaa = rates.latest(fred["BAMLC0A1CAAA"])
    oas_hy = fred["BAMLH0A0HYM2"]
    real10 = fred["DFII10"]

    metrics, scores = [], []
    confidence = "medium"

    # implied carry spread (floaters) / yield-vs-AAA (others)
    carry_spread = None
    if yld is not None and sofr is not None:
        carry_spread = round((yld - sofr) * 100, 0)  # both in %, → bps
        metrics.append(_metric("Implied carry spread", f"{carry_spread:.0f} bps",
                               "distribution yield − SOFR (proxy; true CLO OAS not public)"))
        if oas_aaa is not None:
            prem = carry_spread - oas_aaa * 100
            metrics.append(_metric("Premium vs AAA corp OAS", f"{prem:.0f} bps",
                                   "complexity/liquidity premium over fixed-rate AAA"))

    # z-score of the reconstructed implied-spread series vs its own history
    z = rates.zscore(spread_hist) if spread_hist else None
    if z is not None:
        scores.append(_z_to_score(z))
        metrics.append(_metric("Spread vs own history", f"{z:+.2f}σ",
                               f"{rates.observations(spread_hist)} mo · entry target > +1.5σ"))
        confidence = "high" if rates.observations(spread_hist) >= 24 else "low"
    else:
        confidence = "low"

    # credit cheapness regime (HY OAS percentile, clean 3y data) — wide = cheap
    hy_pct = rates.percentile(oas_hy, 1100)
    if hy_pct is not None:
        scores.append(hy_pct)  # high percentile (wide spreads) = cheap = favorable
        metrics.append(_metric("Credit-spread regime", f"HY OAS {rates.latest(oas_hy)*100:.0f} bps · {hy_pct:.0f}th pct",
                               "credit cheap (high pct) vs 3y"))

    # real yield level (higher real yield = better entry)
    rp = rates.percentile(real10, 1100)
    if rp is not None:
        scores.append(rp)
        metrics.append(_metric("Real 10y yield", f"{rates.latest(real10):.2f}% · {rp:.0f}th pct", None))

    score = _avg(scores)
    head = (f"Carry ~{carry_spread:.0f} bps over SOFR; " if carry_spread is not None else "") + \
           (f"spread {z:+.2f}σ vs history" if z is not None else "valuation via credit regime")
    return _signal("valuation", score, head, metrics, confidence)


def _sig_carry(cls, live, fred):
    """B — net carry over funding + curve roll (for duration)."""
    yld = live.get("distribution_yield_pct")
    sofr = rates.latest(fred["SOFR"])
    tbill = rates.latest(fred["DGS3MO"])
    curve = rates.latest(fred["T10Y2Y"])
    floating = cls in _FLOATING
    funding = sofr if floating else (tbill or sofr)

    metrics, scores = [], []
    net_carry = None
    if yld is not None and funding is not None:
        net_carry = (yld - funding) * 100  # both in %, → bps
        scores.append(_band(net_carry, 0, 300))  # 0bps→0, 300bps→100
        metrics.append(_metric("Net carry", f"{net_carry:.0f} bps",
                               f"yield − {'SOFR' if floating else '3m T-bill'} funding"))
    if not floating and curve is not None:
        # positive curve slope ⇒ positive roll-down for duration holders
        scores.append(_band(curve, -1.0, 1.5))
        metrics.append(_metric("Curve roll (2s10s)", f"{curve:+.2f}%", "positive = roll tailwind"))
    elif floating:
        metrics.append(_metric("Roll-down", "~0 (floating)", "no duration ⇒ carry dominates"))

    score = _avg(scores)
    head = f"{net_carry:.0f} bps net carry" if net_carry is not None else "carry n/a"
    return _signal("carry", score, head, metrics)


def _sig_macro(cls, fred):
    """C — curve, rate path (1y1y fwd vs SOFR), financial conditions, recession."""
    floating = cls in _FLOATING
    curve_3m = rates.latest(fred["T10Y3M"])
    curve_2y = rates.latest(fred["T10Y2Y"])
    sofr = rates.latest(fred["SOFR"])
    t1 = rates.latest(fred["DGS1"])
    t2 = rates.latest(fred["DGS2"])
    nfci = rates.latest(fred["NFCI"])
    sahm = rates.latest(fred["SAHMREALTIME"])

    metrics, scores = [], []

    if curve_3m is not None:
        scores.append(_band(curve_3m, -1.0, 1.5))
        metrics.append(_metric("Yield curve (10y-3m)", f"{curve_3m:+.2f}%",
                               "inverted (<0) flags recession risk"))

    # 1y1y forward vs spot SOFR — rate-path
    fwd = None
    if t1 is not None and t2 is not None:
        fwd = ((1 + t2 / 100) ** 2 / (1 + t1 / 100) - 1) * 100
        slope = fwd - (sofr if sofr is not None else t1)
        if floating:
            # cuts priced (slope<<0) ⇒ floating coupon decays ⇒ unfavorable
            scores.append(_band(slope, -1.5, 0.25))
            hint = "cuts priced ⇒ floating-coupon decay risk"
        else:
            # cuts priced ⇒ duration price gains ⇒ favorable (sign flipped)
            scores.append(_band_inv(slope, -1.5, 0.5))
            hint = "cuts priced ⇒ duration tailwind"
        metrics.append(_metric("1y1y fwd vs SOFR", f"{fwd:.2f}% vs {sofr:.2f}% ({slope:+.2f})", hint))

    if nfci is not None:
        scores.append(_band_inv(nfci, -0.6, 0.6))  # loose (negative) = favorable
        metrics.append(_metric("Financial conditions (NFCI)", f"{nfci:+.2f}",
                               "negative = loose/accommodative"))
    if sahm is not None:
        scores.append(_band_inv(sahm, 0.1, 0.5))  # <0.5 healthy, 0.5 = recession trigger
        metrics.append(_metric("Sahm recession gauge", f"{sahm:.2f}",
                               "≥0.50 triggers recession signal"))

    score = _avg(scores)
    head = (f"Curve {curve_3m:+.2f}%" if curve_3m is not None else "macro") + \
           (f", fwd path {fwd-sofr:+.2f}" if (fwd is not None and sofr is not None) else "")
    return _signal("macro", score, head, metrics)


def _sig_credit_cycle(cls, fred):
    """D — credit-cycle health: spread trend, quality spread, delinquency trend."""
    hy = fred["BAMLH0A0HYM2"]
    bbb = fred["BAMLC0A4CBBB"]
    deln = fred["DRBLACBS"]

    metrics, scores = [], []
    hy_last = rates.latest(hy)

    # spread momentum: tightening (negative change) = improving cycle = favorable
    chg_3m = rates.change(hy, 95)
    if chg_3m is not None and hy_last is not None:
        scores.append(_band_inv(chg_3m, -0.5, 0.75))  # tightening→100, widening→0
        metrics.append(_metric("HY OAS 3m change", f"{chg_3m*100:+.0f} bps",
                               "tightening = healing credit; widening = stress"))
        metrics.append(_metric("HY OAS level", f"{hy_last*100:.0f} bps", None))

    # quality spread HY − BBB: widening = dispersion/stress
    if hy_last is not None and rates.latest(bbb) is not None:
        qs = (hy_last - rates.latest(bbb)) * 100
        metrics.append(_metric("Quality spread (HY−BBB)", f"{qs:.0f} bps",
                               "compression = risk appetite"))

    # delinquency trend (quarterly): falling = healthy
    deln_chg = rates.change(deln, 200)
    if deln_chg is not None:
        scores.append(_band_inv(deln_chg, -0.2, 0.4))
        metrics.append(_metric("Business-loan delinquency", f"{rates.latest(deln):.2f}% ({deln_chg:+.2f} YoY)",
                               "rising defaults = late cycle"))

    score = _avg(scores)
    head = (f"HY OAS {hy_last*100:.0f} bps" if hy_last is not None else "credit cycle") + \
           (f", {chg_3m*100:+.0f} bps 3m" if chg_3m is not None else "")
    conf = "high" if rates.observations(hy) > 200 else "low"
    return _signal("credit_cycle", score, head, metrics, conf)


def _sig_momentum(cls, tech):
    """E — trend (200-DMA + golden cross), 12-1 momentum, RSI."""
    if not tech:
        return _signal("momentum", None, "price history unavailable", [], "low")
    metrics, scores = [], []

    pa = tech.get("pct_above_200dma")
    if pa is not None:
        scores.append(_band(pa, -8, 8))
        gc = tech.get("golden_cross")
        metrics.append(_metric("Trend vs 200-DMA", f"{pa:+.1f}%",
                               ("golden cross (50>200)" if gc else "below 50-DMA")))
    mom = tech.get("mom_12_1_pct")
    if mom is not None:
        scores.append(_band(mom, -6, 10))
        metrics.append(_metric("12-1 momentum", f"{mom:+.1f}%", "price return, skip last month"))
    rsi = tech.get("rsi14")
    if rsi is not None:
        # favorable in healthy-uptrend zone ~45–68; penalise overbought >75
        rsi_score = 100 - abs(rsi - 57) * 2.2
        scores.append(_clamp(rsi_score))
        metrics.append(_metric("RSI(14)", f"{rsi:.0f}",
                               "overbought >70 / oversold <30"))

    score = _avg(scores)
    head = (f"{pa:+.1f}% vs 200-DMA" if pa is not None else "trend n/a")
    conf = "medium" if tech.get("sma200") else "low"
    return _signal("momentum", score, head, metrics, conf)


def _sig_volatility(cls, live, stress):
    """F — calm carry environment OR stress-spike × NAV-discount mean-revert entry."""
    metrics, scores = [], []
    move = stress.get("move")
    move_pct = stress.get("move_pct")
    vix = stress.get("vix")
    vix_pct = stress.get("vix_pct")
    pd_disc = live.get("premium_discount_pct")

    # base: calm = favorable for stable carry (low percentile = calm)
    base = None
    if move_pct is not None:
        base = 100 - move_pct  # calm (low pct) → high score
        metrics.append(_metric("MOVE (rate vol)", f"{move:.0f} · {move_pct:.0f}th pct",
                               "bond-market volatility, 1y percentile"))
    elif vix_pct is not None:
        base = 100 - vix_pct
    if vix is not None:
        metrics.append(_metric("VIX (equity vol)", f"{vix:.1f}" +
                               (f" · {vix_pct:.0f}th pct" if vix_pct is not None else ""), None))

    # opportunity overlay: high stress + trading at a discount ⇒ forced-selling entry
    opp = None
    stress_pct = move_pct if move_pct is not None else vix_pct
    if stress_pct is not None and pd_disc is not None and stress_pct >= 70 and pd_disc < -0.1:
        opp = 90.0
        metrics.append(_metric("Stress dislocation", f"vol {stress_pct:.0f}th pct + {pd_disc:.2f}% discount",
                               "forced-selling entry (arb breaks → buy below NAV)"))

    score = max([s for s in (base, opp) if s is not None], default=None)
    head = (f"MOVE {move_pct:.0f}th pct" if move_pct is not None else "vol n/a") + \
           (" · stress dislocation" if opp else "")
    return _signal("volatility", score, head, metrics)


def _sig_liquidity(cls, live):
    """G — NAV premium/discount, bid/ask, AUM adequacy."""
    metrics, scores = [], []
    pd_disc = live.get("premium_discount_pct")
    if pd_disc is not None:
        # at/below NAV favorable; rich premium unfavorable. -0.5%→100, +1.0%→0
        scores.append(_band_inv(pd_disc, -0.5, 1.0))
        metrics.append(_metric("Premium / discount to NAV", f"{pd_disc:+.2f}%",
                               "buy at/below NAV = yield enhancer"))
    ba = live.get("bid_ask_bps")
    if ba is not None:
        scores.append(_band_inv(ba, 5, 60))  # tight→100, wide→0
        metrics.append(_metric("Bid/ask spread", f"{ba:.0f} bps",
                               "execution cost / liquidity"))
    aum = live.get("aum")
    if aum:
        scores.append(_band(aum / 1e9, 0.1, 3.0))  # >$3B → ample
        metrics.append(_metric("AUM", live.get("aum_fmt"), "scale / liquidity depth"))

    score = _avg(scores)
    head = (f"{pd_disc:+.2f}% vs NAV" if pd_disc is not None else "liquidity")
    return _signal("liquidity", score, head, metrics)


# ---------------------------------------------------------------------------
# Orchestrator
# ---------------------------------------------------------------------------
_FRED_IDS = [
    "SOFR", "DGS1", "DGS2", "DGS3MO", "T10Y2Y", "T10Y3M", "DFII10", "T10YIE",
    "BAMLC0A1CAAA", "BAMLC0A4CBBB", "BAMLH0A0HYM2", "NFCI", "SAHMREALTIME", "DRBLACBS",
]


# ---------------------------------------------------------------------------
# CUSIP → ticker resolution (keyless OpenFIGI)
# ---------------------------------------------------------------------------
_OPENFIGI_URL = "https://api.openfigi.com/v3/mapping"
_BOND_SECTORS = {"CORP", "GOVT", "MUNI", "MTGE", "PFD"}
_figi_memo: dict[str, dict] = {}


def _looks_like_cusip(s: str) -> bool:
    """CUSIP = 9 alphanumeric chars containing at least one digit (tickers don't)."""
    s = s.strip().upper()
    return bool(len(s) == 9 and re.fullmatch(r"[0-9A-Z]{9}", s) and any(c.isdigit() for c in s))


def _is_fundlike(m: dict) -> bool:
    """True for a priceable fund/ETF/equity listing; False for individual bonds."""
    tick = m.get("ticker") or ""
    if not tick or " " in tick or len(tick) > 6:  # bond identifiers look like 'AAPL 2.4 05/03/23'
        return False
    st = f"{m.get('securityType', '')} {m.get('securityType2', '')}".upper()
    sec = (m.get("marketSector") or "").upper()
    if sec in _BOND_SECTORS or "GOVERNMENT" in st or "BOND" in st or "NOTE" in st:
        return False
    return "ETP" in st or "FUND" in st or "COMMON STOCK" in st or "REIT" in st or sec == "EQUITY"


async def resolve_to_ticker(query: str) -> dict:
    """Map a CUSIP to a US-listed ticker via OpenFIGI; pass tickers through unchanged.

    Returns ``{ticker, resolved_from, reject_reason}``.  Individual bonds (Corp/Govt/
    Muni CUSIPs) resolve to a non-priceable identifier → returned as a clean reject.
    """
    q = query.strip().upper()
    if not _looks_like_cusip(q):
        return {"ticker": q, "resolved_from": None, "reject_reason": None}
    if q in _figi_memo:
        return _figi_memo[q]

    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.post(_OPENFIGI_URL,
                                     headers={"Content-Type": "application/json"},
                                     json=[{"idType": "ID_CUSIP", "idValue": q}])
            resp.raise_for_status()
            data = resp.json()
        matches = (data[0].get("data") if data and isinstance(data, list) else None) or []
    except Exception as exc:
        logger.warning("OpenFIGI lookup failed for %s: %s", q, exc)
        return {"ticker": None, "resolved_from": q,
                "reject_reason": f"Couldn't look up CUSIP {q} right now — try the fund's ticker."}

    if not matches:
        res = {"ticker": None, "resolved_from": q,
               "reject_reason": f"No security found for CUSIP {q}. Check the digits or use the ticker."}
        _figi_memo[q] = res
        return res

    # Prefer the US-composite listing; fall back to any fund-like match.
    us = [m for m in matches if m.get("exchCode") == "US"]
    pick = next((m for m in us if _is_fundlike(m)), None) or next((m for m in matches if _is_fundlike(m)), None)
    if not pick:
        res = {"ticker": None, "resolved_from": q,
               "reject_reason": (f"CUSIP {q} maps to an individual bond/security, not a fund. "
                                 "Debt Radar currently covers bond & credit ETFs and funds — "
                                 "enter a fund ticker or an ETF/fund CUSIP.")}
        _figi_memo[q] = res
        return res

    res = {"ticker": pick["ticker"].upper(), "resolved_from": q, "reject_reason": None}
    _figi_memo[q] = res
    return res


async def get_debt_entry(query: str) -> dict:
    resolved = await resolve_to_ticker(query)
    if resolved["reject_reason"]:
        return _san({
            "ticker": query.upper().strip(),
            "as_of": datetime.now(timezone.utc).isoformat(),
            "is_debt": False,
            "reject_reason": resolved["reject_reason"],
            "resolved_from": resolved["resolved_from"],
            "classification": {"name": query.upper().strip()},
        })
    ticker = resolved["ticker"]
    resolved_from = resolved["resolved_from"]

    fred, live_raw, stress = await asyncio.gather(
        rates.fred_many(_FRED_IDS),
        asyncio.to_thread(_fetch_live_sync, ticker),
        asyncio.to_thread(_fetch_stress_sync),
    )

    info = live_raw["info"]
    summary = info.get("longBusinessSummary") or ""
    cls_info = classify_instrument(info, summary, live_raw["asset_classes"])

    if not cls_info["is_debt"]:
        return _san({
            "ticker": ticker,
            "as_of": datetime.now(timezone.utc).isoformat(),
            "is_debt": False,
            "reject_reason": cls_info["reject_reason"],
            "resolved_from": resolved_from,
            "classification": {"name": info.get("longName") or info.get("shortName") or ticker},
        })

    cls = cls_info["instrument_class"]
    live = live_raw["live"]
    live["move"] = stress.get("move")
    live["move_pct"] = stress.get("move_pct")
    live["vix"] = stress.get("vix")
    live["vix_pct"] = stress.get("vix_pct")

    ts = await asyncio.to_thread(_fetch_tech_and_spread_sync, ticker, fred["SOFR"])
    tech, spread_hist = ts["tech"], ts["spread_hist"]

    signals = [
        _sig_valuation(cls, live, fred, spread_hist),
        _sig_carry(cls, live, fred),
        _sig_macro(cls, fred),
        _sig_credit_cycle(cls, fred),
        _sig_momentum(cls, tech),
        _sig_volatility(cls, live, stress),
        _sig_liquidity(cls, live),
    ]

    # composite — renormalise profile weights over families that produced a score
    profile = _PROFILES[cls]
    by_key = {s["key"]: s for s in signals}
    avail = {k: profile[k] for k in FAMILY_KEYS if by_key[k]["score"] is not None}
    wsum = sum(avail.values()) or 1.0
    composite = sum(by_key[k]["score"] * w for k, w in avail.items()) / wsum if avail else None
    for s in signals:
        s["weight"] = round(profile[s["key"]], 3)

    macro_snapshot = {
        "sofr": rates.latest(fred["SOFR"]),
        "t1y": rates.latest(fred["DGS1"]),
        "t2y": rates.latest(fred["DGS2"]),
        "curve_10y2y": rates.latest(fred["T10Y2Y"]),
        "curve_10y3m": rates.latest(fred["T10Y3M"]),
        "real_10y": rates.latest(fred["DFII10"]),
        "breakeven_10y": rates.latest(fred["T10YIE"]),
        "oas_aaa_bps": round((rates.latest(fred["BAMLC0A1CAAA"]) or 0) * 100, 0) or None,
        "oas_bbb_bps": round((rates.latest(fred["BAMLC0A4CBBB"]) or 0) * 100, 0) or None,
        "oas_hy_bps": round((rates.latest(fred["BAMLH0A0HYM2"]) or 0) * 100, 0) or None,
        "nfci": rates.latest(fred["NFCI"]),
        "sahm": rates.latest(fred["SAHMREALTIME"]),
    }

    return _san({
        "ticker": ticker,
        "as_of": datetime.now(timezone.utc).isoformat(),
        "is_debt": True,
        "reject_reason": None,
        "resolved_from": resolved_from,
        "classification": {
            "instrument_class": cls,
            "class_label": cls_info["class_label"],
            "rate_type": cls_info["rate_type"],
            "tier": cls_info["tier"],
            "name": info.get("longName") or info.get("shortName") or ticker,
            "category": info.get("category"),
            "fund_family": info.get("fundFamily"),
            "summary": summary,
            "asset_classes": live_raw["asset_classes"],
            "top_holdings": live_raw["top_holdings"],
            "holdings_note": (
                "Tranche-level holdings aren't exposed by the data provider for this "
                "fund; asset-class mix and strategy are shown instead."
                if not live_raw["top_holdings"] else None),
        },
        "live": live,
        "technical": tech,
        "spread_history": spread_hist,
        "composite": {
            "score": None if composite is None else round(composite, 1),
            "verdict": _verdict(composite),
        },
        "signals": signals,
        "macro_snapshot": macro_snapshot,
    })


# ---------------------------------------------------------------------------
# Optional on-demand LLM explainer
# ---------------------------------------------------------------------------
async def explain_instrument(payload: dict, api_key: str, model: str) -> str:
    """Plain-English read of what the instrument holds and how the signals line
    up for a long entry. Uses the user's key; only called on explicit request."""
    cls = payload.get("classification", {})
    comp = payload.get("composite", {})
    sig_lines = "\n".join(
        f"- {s['family']}: score {s['score']} ({s['verdict']}) — {s['headline']}"
        for s in payload.get("signals", []) if s.get("score") is not None
    )
    macro = payload.get("macro_snapshot", {})
    messages = [
        {"role": "system", "content":
            "You are a fixed-income strategist. Be concise, concrete and balanced. "
            "Explain (1) what this instrument actually holds and its key risks, and "
            "(2) how the current signals line up for timing a LONG entry — including "
            "what would make you wait. Use the numbers provided. 180 words max. "
            "Note that the CLO spread figure is an ETF-implied carry proxy, not a true OAS."},
        {"role": "user", "content":
            f"Instrument: {cls.get('name')} ({payload.get('ticker')}) — "
            f"{cls.get('class_label')}, tier {cls.get('tier')}.\n"
            f"Strategy summary: {(cls.get('summary') or '')[:600]}\n\n"
            f"Composite Entry Score: {comp.get('score')} ({comp.get('verdict')}).\n"
            f"Signals:\n{sig_lines}\n\n"
            f"Macro: {macro}\n"
            f"Live: premium/discount {payload.get('live',{}).get('premium_discount_pct')}%, "
            f"yield {payload.get('live',{}).get('distribution_yield_pct')}%."},
    ]
    return await call_llm(api_key, model, messages, max_tokens=420, temperature=0.5)


# ---------------------------------------------------------------------------
# Price history (multi-range) + dividend history + estimated next ex-date
# ---------------------------------------------------------------------------
def _freq_label(median_gap_days: int | None) -> str | None:
    if not median_gap_days:
        return None
    g = median_gap_days
    if g <= 10:
        return "Weekly"
    if g <= 45:
        return "Monthly"
    if g <= 135:
        return "Quarterly"
    if g <= 250:
        return "Semi-annual"
    return "Annual"


def _range_block(close, cutoff: date | None, max_points: int) -> dict | None:
    """Build a downsampled {series, change_pct, change_abs} for a date window."""
    if close is None or len(close) < 2:
        return None
    sub = close[close.index.date >= cutoff] if cutoff else close
    if sub is None or len(sub) < 2:
        return None
    first = float(sub.iloc[0])
    last = float(sub.iloc[-1])
    step = max(1, len(sub) // max_points)
    strided = sub.iloc[::step]
    series = [{"t": i.strftime("%Y-%m-%d"), "c": round(float(v), 4)} for i, v in strided.items()]
    last_label = sub.index[-1].strftime("%Y-%m-%d")
    if not series or series[-1]["t"] != last_label:
        series.append({"t": last_label, "c": round(last, 4)})
    # partial = data starts well after the requested window (fund younger than range)
    partial = bool(cutoff and (sub.index[0].date() - cutoff).days > 20)
    return {
        "series": series,
        "change_abs": round(last - first, 4),
        "change_pct": round((last / first - 1) * 100, 2) if first else None,
        "start_date": sub.index[0].strftime("%Y-%m-%d"),
        "partial": partial,
    }


def _fetch_history_sync(ticker: str) -> dict:
    tk = yf.Ticker(ticker)
    try:
        hist = tk.history(period="max", interval="1d")
    except Exception:
        hist = None
    try:
        intraday = tk.history(period="1d", interval="5m")
    except Exception:
        intraday = None

    close = None
    if hist is not None and not hist.empty and "Close" in hist:
        close = hist["Close"].dropna()
    intra_close = None
    if intraday is not None and not intraday.empty and "Close" in intraday:
        intra_close = intraday["Close"].dropna()

    current_price = None
    if intra_close is not None and len(intra_close):
        current_price = round(float(intra_close.iloc[-1]), 4)
    elif close is not None and len(close):
        current_price = round(float(close.iloc[-1]), 4)

    today = date.today()
    ranges: list[dict] = []

    # 1D — intraday path; change vs prior daily close
    if intra_close is not None and len(intra_close) >= 2:
        prev_close = float(close.iloc[-2]) if (close is not None and len(close) >= 2) else float(intra_close.iloc[0])
        last = float(intra_close.iloc[-1])
        series = [{"t": i.strftime("%H:%M"), "c": round(float(v), 4)} for i, v in intra_close.items()]
        ranges.append({"key": "1D", "label": "1D", "series": series,
                       "change_abs": round(last - prev_close, 4),
                       "change_pct": round((last / prev_close - 1) * 100, 2) if prev_close else None})

    _RANGES = [
        ("1M", "1M", today - timedelta(days=31), 240),
        ("3M", "3M", today - timedelta(days=93), 240),
        ("YTD", "YTD", date(today.year, 1, 1), 240),
        ("1Y", "1Y", today - timedelta(days=366), 240),
        ("5Y", "5Y", today - timedelta(days=1827), 220),
        ("10Y", "10Y", today - timedelta(days=3653), 200),
    ]
    for key, label, cutoff, mp in _RANGES:
        block = _range_block(close, cutoff, mp)
        if block:
            ranges.append({"key": key, "label": label, **block})

    # ── dividends ───────────────────────────────────────────────────────────
    dividends: dict = {"history": [], "ttm_total": None, "ttm_yield_pct": None,
                       "frequency": None, "last_amount": None, "last_date": None,
                       "next_ex_date": None, "next_estimated": False}
    try:
        div = tk.dividends
        if div is not None and len(div):
            div = div[div > 0]
            items = [(idx.date(), float(v)) for idx, v in div.items()]
            if items:
                last_date, last_amt = items[-1]
                cutoff = today - timedelta(days=365)
                ttm = round(sum(a for d, a in items if d >= cutoff), 4)
                recent = [d for d, _ in items][-9:]
                gaps = sorted((recent[i + 1] - recent[i]).days for i in range(len(recent) - 1))
                med = gaps[len(gaps) // 2] if gaps else None
                next_ex = None
                if med:
                    nxt = last_date + timedelta(days=med)
                    while nxt < today:  # roll stale estimate forward (like nextEarningsDate)
                        nxt = nxt + timedelta(days=med)
                    next_ex = nxt.isoformat()
                dividends = {
                    "history": [{"date": d.isoformat(), "amount": round(a, 4)} for d, a in items[-60:]],
                    "ttm_total": ttm,
                    "ttm_yield_pct": round(ttm / current_price * 100, 2) if current_price else None,
                    "frequency": _freq_label(med),
                    "last_amount": round(last_amt, 4),
                    "last_date": last_date.isoformat(),
                    "next_ex_date": next_ex,
                    "next_estimated": next_ex is not None,
                }
    except Exception as exc:
        logger.debug("dividend fetch failed for %s: %s", ticker, exc)

    info = {}
    try:
        info = tk.fast_info or {}
    except Exception:
        pass

    return {
        "ticker": ticker,
        "currency": (info.get("currency") if isinstance(info, dict) else None) or "USD",
        "current_price": current_price,
        "ranges": ranges,
        "dividends": dividends,
    }


async def get_price_history(query: str) -> dict:
    resolved = await resolve_to_ticker(query)
    ticker = resolved["ticker"]
    if not ticker:  # unresolvable / individual-bond CUSIP — entry endpoint shows the reason
        return _san({
            "ticker": query.upper().strip(), "currency": "USD", "current_price": None,
            "ranges": [], "error": resolved["reject_reason"],
            "dividends": {"history": [], "ttm_total": None, "ttm_yield_pct": None,
                          "frequency": None, "last_amount": None, "last_date": None,
                          "next_ex_date": None, "next_estimated": False},
        })
    return _san(await asyncio.to_thread(_fetch_history_sync, ticker))
