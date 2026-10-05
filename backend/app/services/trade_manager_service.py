"""Trade Manager — the hold / exit desk for a PLACED trade (My Trades).

One question: *given what I own, should I STRONG-HOLD, HOLD, EXIT or STRONG-EXIT — where exactly do I get
out, why, and what do I watch next?*

Pipeline (all deterministic until the explicit LLM action):

1. ``gather_market_evidence``  — ticker-level evidence, cached 10 min, built lazily on user action:
   institutional TA (MTF structure · volume profile · AVWAP · regime · dealer gamma · patterns via the
   shared trade-setup dossier) + the NEW institutional indicator suite and 15 famous-trader rule lenses
   (``trader_lenses``) + fundamentals (the 10 exit-analysis pillars + analyst/estimate/insider/dividend
   facts) + events (earnings, ex-div, 8-K filings, headlines + flags) + market tape / FRED macro / peers.
2. ``build_profile``           — what the user actually holds (thesis, premium stance, strikes, cushion).
3. ``decide``                  — four lenses (Quant · Technical · Fundamental · Event) aligned to the
   POSITION (a falling stock is good for a short call), weighted by the trade's remaining DTE, then
   discipline overrides → STRONG_HOLD / HOLD / EXIT / STRONG_EXIT (same 68/45/28 cut-points as the quant
   desk, which this reconciles with rather than competes against).
4. ``build_exit_plan`` / ``build_monitor`` — concrete exit levels with the WHY (confluence, trader exit
   rules, options-management rules) and what to watch up / down / elsewhere.
5. ``llm_packet`` + ``run_trade_manager_ai`` — the raw evidence (facts only; every algorithm decision —
   verdict, scores, stances — is stripped) handed to the LLM on explicit user action.
"""
from __future__ import annotations

import asyncio
import datetime as dt
import json
import logging
import math
import re
from typing import Any, Optional

import numpy as np

from . import trader_lenses as TL

logger = logging.getLogger(__name__)

SEV = {"STRONG_HOLD": 0, "HOLD": 1, "EXIT": 2, "STRONG_EXIT": 3}
_BY_SEV = {v: k for k, v in SEV.items()}
CUT_STRONG_HOLD, CUT_HOLD, CUT_EXIT = 68, 45, 28          # = the quant desk's management cut-points
INVAL_MIN_ATR = 2.0                                        # min distance of the structural invalidation (see _levels)
STOCK_STOP_PCT = 10.0                                      # discipline stop for share positions (see decide())
_EV_TTL = 600
_EV_VERSION = "v3"
_EV_SEM = asyncio.Semaphore(1)                              # one heavy evidence build at a time (512 MiB box)


# ── backtest evidence (scripts/tm_backtest_*.py; reproduce with `PYTHONPATH=. venv/bin/python scripts/…`) ─────────────────
# Universe: 99 liquid US stocks/ETFs (incl. laggards to damp survivorship), daily 2016-01 → 2026-10, POINT-IN-TIME (each
# sample sees only the data up to that bar). Exits fill at the NEXT open after a close-basis trigger (conservative).
BACKTEST = {
    "universe": "99 liquid US stocks/ETFs · 2016-2026 · 52.6k point-in-time samples (every 5th bar)",
    "direction": {
        "finding": "None of the 15 trader rule-sets (nor their consensus) predicts the next 21 / 63-day return: cross-sectional IC "
                   "−0.02…+0.01, |t| < 1.2, both halves of the sample. Adverse flags (below the 50/150/200d, supertrend down, stage 4) "
                   "are followed by HIGHER mean 21d returns (1-month reversal) but a fatter left tail. After normalising by realized "
                   "vol, logistic models on 22 technical features score out-of-sample AUC 0.51-0.55.",
        "so": "Trader lenses are shown as structure/context and to derive levels; they carry a small weight in the verdict.",
    },
    "tail_state": {"P(21d ≤ −8%)": {"≥10% off 52w-high": 13.9, "otherwise": 7.5, "≥20% off high": 17.5},
                   "note": "Dispersion, not direction: vol ratio of forward returns 1.5-1.7× in drawdown states."},
    "vol_ratio": "21d/63d realized-vol ratio is the one technical input that still predicts forward dispersion after vol normalisation "
                 "(mean |z| of the next 21d: 0.73 → 0.92 from the lowest to the highest ratio quartile).",
    # 22,674 bullish-lens entries, max hold 126 bars
    "exit_rules": {
        "hold126":    {"label": "hold 126 days (no exit)",              "mean": 7.8, "win": 65, "p5": -20.3, "worst": -74.2, "hold": 124},
        "fix8":       {"label": "hard stop −8% from entry",              "mean": 4.2, "win": 45, "p5": -8.9,  "worst": -26.9, "hold": 82},
        "fix10":      {"label": "hard stop −10% from entry",             "mean": 4.7, "win": 51, "p5": -10.8, "worst": -28.8, "hold": 91},
        "sma200":     {"label": "exit on a close below the 200d SMA",    "mean": 5.0, "win": 46, "p5": -15.4, "worst": -56.3, "hold": 84},
        "sma150":     {"label": "exit on a close below the 150d SMA",    "mean": 4.4, "win": 42, "p5": -13.8, "worst": -55.1, "hold": 75},
        "minervini3": {"label": "Minervini ⅓ at 50d / ⅓ at 150d / ⅓ at 200d", "mean": 3.7, "win": 47, "p5": -11.5, "worst": -51.7, "hold": 64},
        "chand5":     {"label": "Chandelier 5×ATR trail",                "mean": 2.8, "win": 45, "p5": -11.1, "worst": -33.8, "hold": 52},
        "sma50":      {"label": "exit on a close below the 50d SMA",     "mean": 1.8, "win": 39, "p5": -8.7,  "worst": -44.4, "hold": 34},
        "chand3":     {"label": "Chandelier 3×ATR trail",                "mean": 1.2, "win": 40, "p5": -7.2,  "worst": -30.1, "hold": 24},
        "ema21":      {"label": "exit on a close below the 21 EMA",      "mean": 0.8, "win": 38, "p5": -6.0,  "worst": -30.1, "hold": 16},
        "ema10":      {"label": "exit on a close below the 10 EMA",      "mean": 0.5, "win": 40, "p5": -4.5,  "worst": -30.1, "hold": 9},
    },
    # SYNTHETIC: Black-Scholes-priced 24-trading-day 20Δ cash-secured puts (IV = 1.15 × blended realized vol, vol-spike on drops,
    # 2bp/leg costs), 26,115 entries 2016-26 — % of collateral per trade. Relative comparisons only (no historical chains).
    "short_put": {
        "universe": "26.1k synthetic 20Δ ~35-DTE short puts, 99 names, 2016-26 (BS-priced; relative comparison only)",
        "rows": {
            "hold":       {"label": "hold to expiry",                         "mean": 0.50, "win": 88.4, "cvar5": -10.9, "worst": -70.1, "mean_sd": 0.142},
            "tp50":       {"label": "close at 50% of max profit",             "mean": 0.29, "win": 93.6, "cvar5": -8.6,  "worst": -70.1, "mean_sd": 0.098},
            "stop2x":     {"label": "50% take + stop at −2× credit",          "mean": 0.19, "win": 85.9, "cvar5": -5.6,  "worst": -40.9, "mean_sd": 0.103},
            "stop1x":     {"label": "50% take + stop at −1× credit",          "mean": 0.11, "win": 76.2, "cvar5": -4.3,  "worst": -36.4, "mean_sd": 0.068},
            "sma200":     {"label": "50% take + exit on a close < 200d SMA",  "mean": 0.08, "win": 72.9, "cvar5": -4.6,  "worst": -34.4, "mean_sd": 0.056},
            "tech":       {"label": "50% take + weekly technical-consensus exit (< −0.1)", "mean": 0.06, "win": 74.4, "cvar5": -5.1, "worst": -34.4, "mean_sd": 0.038},
        },
        "takeaway": "Exiting short puts on technical weakness or a stop buys tail protection at a steep price: CVaR(5%) halves (−10.9% → −4.6…−5.6%) "
                    "and the worst case roughly halves, but 60-90% of the expected return is surrendered and risk-adjusted return (mean/sd) is WORSE "
                    "than simply holding (0.04-0.10 vs 0.14). Closing at 50% of max profit raised the win rate (88% → 94%) at a modest return cost. "
                    "So: take 50%, and exit on real strike/structure pressure — not on a chart signal or a P&L multiple alone. "
                    "Entry note: in this model, selling puts after drawdowns (below the 200d) did not do worse (mean/sd 0.17 vs 0.12) because the "
                    "premium is richer; real chains may price that differently.",
    },
    # Hold-vs-CLOSE for an OPEN short put (synthetic, same model): at weekly check-points of 42.6k open positions,
    # Δ = P&L if held to expiry − P&L if closed now (in % of collateral). Δ>0 means holding was better. Rows = state at the check-point.
    "hold_state": {
        "universe": "42.6k open synthetic short-put check-points (k = 5/10/15/20 of 24 days), 99 names, 2016-26",
        "all": {"n": 42628, "mean_delta": 0.62, "p_close_better": 18.1, "worst5": -5.0, "p_finish_lt_neg2": 16.7},
        "p_touch": [
            {"lo": 0, "hi": 0.2, "label": "P(touch) 0–20%", "n": 1705, "mean_delta": 0.46, "p_close_better": 5.6, "worst5": -0.2, "p_finish_lt_neg2": 2.3},
            {"lo": 0.2, "hi": 0.35, "label": "P(touch) 20–35%", "n": 10025, "mean_delta": 0.47, "p_close_better": 9.4, "worst5": -2.3, "p_finish_lt_neg2": 4.9},
            {"lo": 0.35, "hi": 0.5, "label": "P(touch) 35–50%", "n": 8545, "mean_delta": 0.61, "p_close_better": 13.3, "worst5": -3.6, "p_finish_lt_neg2": 7.5},
            {"lo": 0.5, "hi": 0.75, "label": "P(touch) 50–75%", "n": 8362, "mean_delta": 0.82, "p_close_better": 16.4, "worst5": -4.6, "p_finish_lt_neg2": 10.5},
            {"lo": 0.75, "hi": 1.0, "label": "P(touch) 75–100%", "n": 13991, "mean_delta": 0.64, "p_close_better": 29.9, "worst5": -7.6, "p_finish_lt_neg2": 36.2},
        ],
        "vol_ratio": [
            {"lo": 0, "hi": 0.75, "label": "21d/63d RV ratio < 0.75 (compressing)", "n": 6626, "mean_delta": 0.77, "p_close_better": 12.8, "worst5": -3.4, "p_finish_lt_neg2": 8.8},
            {"lo": 0.75, "hi": 1.4, "label": "ratio 0.75–1.4 (stable)", "n": 33086, "mean_delta": 0.63, "p_close_better": 18.5, "worst5": -4.7, "p_finish_lt_neg2": 16.4},
            {"lo": 1.4, "hi": 99, "label": "ratio ≥ 1.4 (expanding)", "n": 2916, "mean_delta": 0.26, "p_close_better": 26.7, "worst5": -13.8, "p_finish_lt_neg2": 38.3},
        ],
        "pnl_vs_credit": [
            {"lo": -10000.0, "hi": -100, "label": "P&L worse than −1× credit", "n": 11072, "mean_delta": 0.63, "p_close_better": 31.4, "worst5": -8.7, "p_finish_lt_neg2": 41.7},
            {"lo": -100, "hi": 0, "label": "P&L between −1× credit and 0", "n": 13229, "mean_delta": 0.81, "p_close_better": 17.5, "worst5": -4.7, "p_finish_lt_neg2": 11.6},
            {"lo": 0, "hi": 50, "label": "P&L 0–50% of credit", "n": 18327, "mean_delta": 0.49, "p_close_better": 10.6, "worst5": -2.5, "p_finish_lt_neg2": 5.2},
        ],
        # the ONE ex-ante condition where closing beat holding on average (Δ<0):
        "close_trigger": {
            "rule": "short strike TESTED (P(touch) ≥ 50% or in the money) AND volatility EXPANDING (21d RV ≥ 1.4× the 63d)",
            "stats": {"n": 2162, "mean_delta": -0.18, "p_close_better": 34.8, "worst5": -17.3, "p_finish_lt_neg2": 50.8},
            "plus_deep_loss": {"rule": "…and P&L already worse than −2× credit", "n": 1324, "mean_delta": -1.02, "p_close_better": 44.7, "worst5": -20.6, "p_finish_lt_neg2": 73.5},
            "regime_note": "2020 (crash): tested puts averaged Δ = −1.3% of collateral (closing better 35% of the time, worst-5% −25%); the Feb-Apr 2020 window "
                           "Δ = −5.2%, closing better 55% of the time. In every other year holding a tested put had positive Δ.",
        },
        "takeaway": "Holding an open short put had POSITIVE average value vs closing in almost every state (theta + 1-month mean reversion), "
                    "but the left tail fattens fast: when P(touch) > 75% the odds that closing would have been better rise from ~6% to 30%, and "
                    "the worst-5% outcome goes from ≈0 to −7.6% of collateral. The exception — where closing wins on average — is a tested strike "
                    "in an EXPANDING-volatility regime. Treat EXIT on a short put as tail-risk control, sized to what you can afford to lose.",
    },
    "exit_takeaway": "Tight trails (10/21 EMA, Chandelier ≤3×ATR, 10/20-day lows) whipsaw: ~40% win rate and an average hold of 9-24 days "
                     "turn +7.8% into +0.5-1.2%. Wide exits (200d / 150d / 5×ATR) keep most of the trend; a hard −8…−10% stop cuts the "
                     "worst-5% outcome by ~45% and the worst case by ~60% for the best return per unit of tail risk.",
}


def _evid_sp(rule: str) -> str:
    rows = BACKTEST["short_put"]["rows"]
    r, h = rows.get(rule), rows["hold"]
    if not r:
        return ""
    return (f"Synthetic backtest ({BACKTEST['short_put']['universe'].split(',')[0]}): {r['label']} → win {r['win']}% · CVaR5 {r['cvar5']}% · "
            f"mean/sd {r['mean_sd']} (vs hold-to-expiry: win {h['win']}% · CVaR5 {h['cvar5']}% · mean/sd {h['mean_sd']})")


def _evid(rule: str) -> str:
    r = BACKTEST["exit_rules"].get(rule)
    if not r:
        return ""
    return (f"Backtest ({BACKTEST['universe'].split(' · ')[0]}, bullish entries): {r['label']} → avg {r['mean']:+.1f}% · win {r['win']}% · "
            f"worst-5% {r['p5']:.0f}% · avg hold {r['hold']}d (vs hold-to-126d: {BACKTEST['exit_rules']['hold126']['mean']:+.1f}% / worst-5% "
            f"{BACKTEST['exit_rules']['hold126']['p5']:.0f}%)")


# ── helpers ──────────────────────────────────────────────────────────────────

def _clean(o: Any) -> Any:
    """JSON-safe: numpy → python, NaN/inf → None, dates → iso. Recursive."""
    if isinstance(o, dict):
        return {str(k): _clean(v) for k, v in o.items()}
    if isinstance(o, (list, tuple, set)):
        return [_clean(v) for v in o]
    if isinstance(o, (np.floating, float)):
        f = float(o)
        return None if (math.isnan(f) or math.isinf(f)) else f
    if isinstance(o, (np.integer,)):
        return int(o)
    if isinstance(o, (np.bool_,)):
        return bool(o)
    if isinstance(o, (dt.datetime, dt.date)):
        return o.isoformat()
    if hasattr(o, "isoformat"):
        try:
            return o.isoformat()
        except Exception:  # noqa: BLE001
            return str(o)
    return o


def _num(x) -> Optional[float]:
    try:
        if x is None or isinstance(x, bool):
            return None
        f = float(x)
        return None if (math.isnan(f) or math.isinf(f)) else f
    except (TypeError, ValueError):
        return None


def _clamp(x: float, lo: float = 0.0, hi: float = 100.0) -> float:
    return max(lo, min(hi, x))


def _r(x, nd: int = 2):
    v = _num(x)
    return None if v is None else round(v, nd)


# ── 1. evidence (ticker-level, cached) ───────────────────────────────────────

_RISK_WORDS = ("downgrade", "lawsuit", "probe", "investigation", "sec ", "subpoena", "recall", "guidance cut",
               "cuts guidance", "lowers guidance", "misses", "miss ", "plunge", "tumble", "slump", "bankrupt",
               "fraud", "halt", "tariff", "ban ", "export curb", "restriction", "layoff", "short report",
               "short seller", "delist", "warning", "default", "downturn", "resigns", "steps down", "outage")
_POS_WORDS = ("upgrade", "beats", "beat ", "raises guidance", "raised guidance", "record", "approval", "approved",
              "wins", "contract", "buyback", "repurchase", "partnership", "surge", "soar", "initiates", "price target raised")


def _headline_flags(news: list[dict]) -> dict:
    risk, pos = [], []
    for n in news:
        t = (n.get("title") or "").lower()
        if any(w in t for w in _RISK_WORDS):
            risk.append(n.get("title"))
        if any(w in t for w in _POS_WORDS):
            pos.append(n.get("title"))
    return {"risk_headlines": risk[:5], "positive_headlines": pos[:5],
            "n_risk": len(risk), "n_positive": len(pos), "n_total": len(news),
            "note": "keyword scan of headlines — a heuristic prompt for the reader, not a sentiment model"}



def _rss_news(query: str, n: int = 6) -> list[dict]:
    """Public Google-News RSS headlines (no key) — the fallback / supplement for yfinance's thin news feed and
    the only source for industry / macro / geopolitical context. Best-effort; [] on any failure."""
    try:
        import httpx
        import xml.etree.ElementTree as ET
        r = httpx.get("https://news.google.com/rss/search", params={"q": query, "hl": "en-US", "gl": "US", "ceid": "US:en"},
                      timeout=8.0, headers={"User-Agent": "Mozilla/5.0"}, follow_redirects=True)
        if r.status_code != 200:
            return []
        out, seen = [], set()
        for it in ET.fromstring(r.text).findall(".//item"):
            title = (it.findtext("title") or "").strip()
            key = re.sub(r"\W+", " ", title.lower()).strip()[:60]
            if not title or key in seen:
                continue
            seen.add(key)
            src = it.find("source")
            out.append({"title": title, "publisher": (src.text if src is not None else "") or "", "date": it.findtext("pubDate") or "",
                        "summary": "", "link": it.findtext("link") or ""})
            if len(out) >= n:
                break
        return out
    except Exception:  # noqa: BLE001
        return []


def _news_for(sym: str, n: int, name: Optional[str] = None) -> list[dict]:
    from .market_sentiment_service import _fetch_news_headlines
    items = _fetch_news_headlines(sym, n) or []
    if len(items) < min(4, n):
        have = {re.sub(r"\W+", " ", (x.get("title") or "").lower()).strip()[:60] for x in items}
        for x in _rss_news(f"{name or sym} {sym} stock when:14d", n):
            if re.sub(r"\W+", " ", x["title"].lower()).strip()[:60] not in have:
                items.append(x)
    return items[:n]


def _pillar(fn, *args) -> dict:
    try:
        r = fn(*args) or {}
        return {"score": r.get("score"), "data": r.get("data") or {}}
    except Exception as exc:  # noqa: BLE001 — one failing pillar must not sink the read
        return {"score": None, "data": {}, "error": str(exc)[:120]}


def _df_records(df, cols: list[str], n: int = 4) -> list[dict]:
    try:
        if df is None or getattr(df, "empty", True):
            return []
        return _clean(df.head(n).reset_index().to_dict("records"))
    except Exception:  # noqa: BLE001
        return []


def _analyst_block(stock, info: dict) -> dict:
    out: dict = {
        "recommendation_key": info.get("recommendationKey"), "recommendation_mean": _r(info.get("recommendationMean")),
        "n_analysts": info.get("numberOfAnalystOpinions"),
        "target_mean": _r(info.get("targetMeanPrice")), "target_high": _r(info.get("targetHighPrice")),
        "target_low": _r(info.get("targetLowPrice")), "target_median": _r(info.get("targetMedianPrice")),
    }
    try:
        apt = getattr(stock, "analyst_price_targets", None)
        if isinstance(apt, dict):
            out["price_targets"] = _clean(apt)
    except Exception:  # noqa: BLE001
        pass
    try:                                              # last ~90d rating actions
        ud = stock.upgrades_downgrades
        if ud is not None and not ud.empty:
            cut = (dt.datetime.now() - dt.timedelta(days=90))
            rows = []
            for ts, r in ud.head(40).iterrows():
                try:
                    when = ts.to_pydatetime().replace(tzinfo=None)
                except Exception:  # noqa: BLE001
                    when = None
                if when and when < cut:
                    continue
                rows.append({"date": when.date().isoformat() if when else None, "firm": r.get("Firm"),
                             "from": r.get("FromGrade"), "to": r.get("ToGrade"), "action": r.get("Action"),
                             "price_target_action": r.get("priceTargetAction"),
                             "price_target": _r(r.get("currentPriceTarget"))})
            out["rating_changes_90d"] = rows[:12]
            acts = [str(x.get("action") or "").lower() for x in rows]
            out["upgrades_90d"] = sum(1 for a in acts if a.startswith("up"))
            out["downgrades_90d"] = sum(1 for a in acts if a.startswith("down"))
            out["reiterated_90d"] = sum(1 for a in acts if a.startswith(("main", "reit")))
    except Exception:  # noqa: BLE001
        pass
    return out


def _estimates_block(stock) -> dict:
    out: dict = {}
    for name, attr in (("eps_estimate", "earnings_estimate"), ("revenue_estimate", "revenue_estimate"),
                       ("eps_trend", "eps_trend"), ("eps_revisions", "eps_revisions"),
                       ("growth_estimates", "growth_estimates")):
        try:
            df = getattr(stock, attr, None)
            if df is not None and not getattr(df, "empty", True):
                out[name] = _clean(df.reset_index().to_dict("records"))[:5]
        except Exception:  # noqa: BLE001
            continue
    try:
        eh = stock.earnings_history
        if eh is not None and not eh.empty:
            out["earnings_surprises"] = _clean(eh.reset_index().tail(4).to_dict("records"))
    except Exception:  # noqa: BLE001
        pass
    return out


def _insider_block(stock) -> dict:
    try:
        tx = stock.insider_transactions
        if tx is None or tx.empty:
            return {}
        buys = sells = 0
        bsh = ssh = 0.0
        for _, r in tx.head(40).iterrows():
            txt = (str(r.get("Text") or "") + " " + str(r.get("Transaction") or "")).lower()
            sh = abs(_num(r.get("Shares")) or 0.0)
            if "purchase" in txt or "buy" in txt:
                buys += 1; bsh += sh
            elif "sale" in txt or "sold" in txt:
                sells += 1; ssh += sh
        return {"recent_buys": buys, "recent_sells": sells, "shares_bought": int(bsh), "shares_sold": int(ssh)}
    except Exception:  # noqa: BLE001
        return {}


def _trim_ta_block(ta: dict) -> dict:
    keep = ("timeframeLabel", "currentRSI", "rsiSignal", "supportLevel", "resistanceLevel", "volumeAnalysis",
            "institutional", "macd", "bollingerBands", "movingAverages", "emaCrossover", "_structure_tf", "_trend_tf",
            "_trend_regime", "_drift_mu", "_atr_pct", "_last_div", "_next_exdiv", "_div_cadence_days")
    out = {k: ta.get(k) for k in keep if k in ta}
    inst = out.get("institutional")
    if isinstance(inst, dict):                         # drop bulky per-bin arrays; keep the named levels
        vp = inst.get("volume_profile")
        if isinstance(vp, dict) and "bins" in vp:
            inst = {**inst, "volume_profile": {k: v for k, v in vp.items() if k != "bins"}}
            out["institutional"] = inst
    return out


def _collect_sync(ticker: str, dte: Optional[int]) -> dict:
    """Blocking: price history → indicator suite + trader lenses; fundamentals; events; market tape."""
    import yfinance as yf
    from .desk_review_service import _ta_sync, _norm_ticker
    from . import exit_analysis_service as EX
    from .thematic_impact_service import _fetch_market_snapshot_sync

    sym = _norm_ticker(ticker)
    stock = yf.Ticker(sym)
    ev: dict = {"ticker": sym, "sources_ok": {}}

    # — price history → suite + lenses
    hist = None
    try:
        hist = stock.history(period="2y", interval="1d").dropna(subset=["Close"])
    except Exception as exc:  # noqa: BLE001
        logger.info("trade-manager history failed %s: %s", sym, exc)
    suite, lenses = {}, []
    if hist is not None and len(hist) >= 60:
        bench = None
        try:
            b = yf.Ticker("SPY").history(period="2y", interval="1d")["Close"].dropna()
            bench = b.reindex(hist.index, method="ffill").dropna().values
        except Exception:  # noqa: BLE001
            bench = None
        suite = TL.indicator_suite(hist["Open"].values, hist["High"].values, hist["Low"].values,
                                   hist["Close"].values, hist["Volume"].values, hist.index, bench)
        lenses = TL.trader_lenses(suite)
    ev["sources_ok"]["suite"] = bool(suite)
    spot = float(hist["Close"].values[-1]) if hist is not None and len(hist) else None
    ev["spot"] = _r(spot)
    if hist is not None and len(hist):                 # compact daily closes — lets us show what the underlying did SINCE YOU ENTERED
        h_ = hist.tail(520)
        ev["history"] = {"dates": [t.strftime("%Y-%m-%d") for t in h_.index], "closes": [round(float(x), 2) for x in h_["Close"].values]}

    ta_block, volume = {}, {}
    try:
        ta_block = _trim_ta_block(_ta_sync(sym, dte) or {})
    except Exception:  # noqa: BLE001
        pass
    ev["sources_ok"]["ta_block"] = bool(ta_block)
    try:
        from .volume_service import compute_volume_analysis
        v = compute_volume_analysis(stock, "1d") or {}
        volume = {"metrics": v.get("metrics"), "read": v.get("read"), "climax_bars": (v.get("climax_bars") or [])[-3:]}
    except Exception:  # noqa: BLE001
        pass
    ev["technical"] = {"suite": suite, "lenses": lenses, "consensus": TL.lens_consensus(lenses),
                       "ta_block": ta_block, "volume": volume}

    # — fundamentals
    info: dict = {}
    try:
        info = stock.info or {}
    except Exception:  # noqa: BLE001
        info = {}
    px = spot or _num(info.get("currentPrice")) or _num(info.get("regularMarketPrice")) or 0.0
    is_fund = str(info.get("quoteType") or "").upper() in ("ETF", "MUTUALFUND", "INDEX", "CRYPTOCURRENCY", "FUTURE")
    h6 = hist.tail(126) if hist is not None else None
    h1 = hist.tail(252) if hist is not None else None
    pillars: dict = {}
    if info and not is_fund:
        pillars = {
            "fundamental": _pillar(EX._compute_fundamental, stock, info),
            "valuation": _pillar(EX._compute_valuation, info, px),
            "sentiment_targets": _pillar(EX._compute_sentiment, info, px),
            "catalyst_revisions": _pillar(EX._compute_catalyst_revisions, stock, info),
            "quality_capital": _pillar(EX._compute_quality_capital, stock, info),
            "ownership_flow": _pillar(EX._compute_ownership_flow, stock, info),
            "structural": _pillar(EX._compute_structural, stock, info),
            "macro_sensitivity": _pillar(EX._compute_macro, info, h6),
            "geopolitical": _pillar(EX._compute_geopolitical, stock, info),
            "sector_rotation": _pillar(EX._compute_sector_rotation, info, h1),
        }
    ev["fundamental"] = {
        "is_fund": is_fund, "name": info.get("shortName") or info.get("longName"), "sector": info.get("sector"),
        "industry": info.get("industry"), "market_cap": info.get("marketCap"),
        "valuation_multiples": {k: _r(info.get(k)) for k in (
            "trailingPE", "forwardPE", "pegRatio", "priceToBook", "priceToSalesTrailing12Months",
            "enterpriseToEbitda", "enterpriseToRevenue")},
        "profitability": {k: _r(info.get(k), 4) for k in (
            "grossMargins", "operatingMargins", "profitMargins", "returnOnEquity", "returnOnAssets",
            "revenueGrowth", "earningsGrowth", "earningsQuarterlyGrowth")},
        "balance_sheet": {k: _r(info.get(k), 3) for k in ("debtToEquity", "currentRatio", "quickRatio",
                                                         "totalDebt", "totalCash", "freeCashflow", "operatingCashflow")},
        "dividend": {"yield": _r(info.get("dividendYield"), 4), "rate": _r(info.get("dividendRate")),
                     "payout_ratio": _r(info.get("payoutRatio"), 3), "last_ex_dividend_date": _epoch_to_date(info.get("exDividendDate")),
                     "five_year_avg_yield": _r(info.get("fiveYearAvgDividendYield"))},
        "shares": {"short_pct_float": _r(info.get("shortPercentOfFloat"), 4), "beta": _r(info.get("beta")),
                   "held_by_institutions": _r(info.get("heldPercentInstitutions"), 3),
                   "held_by_insiders": _r(info.get("heldPercentInsiders"), 3)},
        "pillars": pillars,
        "analyst": _analyst_block(stock, info) if not is_fund else {},
        "estimates": _estimates_block(stock) if not is_fund else {},
        "insiders": _insider_block(stock) if not is_fund else {},
    }
    ev["sources_ok"]["fundamental"] = bool(pillars)

    # — events: earnings / ex-div / headlines
    cat = ((pillars.get("catalyst_revisions") or {}).get("data") or {})
    ex_div = _epoch_to_date(info.get("exDividendDate"))
    earn_date = None
    try:
        ts = info.get("earningsTimestampStart") or info.get("earningsTimestamp")
        if ts:
            d_ = _epoch_to_date(ts)
            if d_ and dt.date.fromisoformat(d_) >= dt.date.today():
                earn_date = d_
    except Exception:  # noqa: BLE001
        pass
    news: list[dict] = []
    try:
        news = _news_for(sym, 12, info.get("shortName") or info.get("longName"))
    except Exception:  # noqa: BLE001
        news = []
    industry = info.get("industry") or info.get("category")
    ind_news = _rss_news(f"{industry} industry outlook when:14d", 5) if industry else []
    macro_news = _rss_news("stock market Federal Reserve inflation tariffs geopolitical when:7d", 6)
    last_ex_div = None
    if ex_div and _days_to(ex_div) is not None and _days_to(ex_div) < 0:      # yfinance returns the LAST ex-date — only an upcoming one is a catalyst
        last_ex_div, ex_div = ex_div, None
    ev["events"] = {"days_to_earnings": cat.get("days_to_earnings"), "earnings_date": earn_date,
                    "ex_dividend_date": ex_div, "last_ex_dividend_date": last_ex_div, "news": news, "industry_news": ind_news, "macro_geopolitical_news": macro_news,
                    "headline_flags": _headline_flags(news + ind_news)}
    ev["sources_ok"]["news"] = bool(news)

    try:
        ev.setdefault("market", {})["tape"] = _fetch_market_snapshot_sync()
    except Exception:  # noqa: BLE001
        ev.setdefault("market", {})["tape"] = {}
    ev["sources_ok"]["tape"] = bool(ev["market"].get("tape"))
    return _clean(ev)


def _setup_digest(ts: dict) -> dict:
    """The consolidated trade-setup read, minus the entry setups (irrelevant to managing) and bulky arrays."""
    d = dict(ts.get("dossier") or {})
    d.pop("setups", None)
    vp = {}
    for k, v in (d.get("volume_profile") or {}).items():
        if isinstance(v, dict):
            vp[k] = {kk: vv for kk, vv in v.items() if kk not in ("bins",)}
    d["volume_profile"] = vp
    gl = d.get("dealer_gamma") or {}
    if isinstance(gl.get("gamma_levels"), dict):
        gl = {**gl, "gamma_levels": {k: v for k, v in gl["gamma_levels"].items() if k != "by_strike"}}
        d["dealer_gamma"] = gl
    pats = []
    for p in (ts.get("chart_patterns") or [])[:6]:
        pats.append({k: p.get(k) for k in ("name", "category", "direction", "status", "confidence", "breakout", "target", "stop")})
    return {"context": ts.get("context"), "zones": (ts.get("confluence_zones") or [])[:10],
            "patterns": pats, "dossier": d, "meta": ts.get("meta")}


def _cum_ret(rets: list[float], k: int) -> Optional[float]:
    if not rets or len(rets) < k:
        return None
    return float(np.prod(1.0 + np.asarray(rets[-k:], dtype=float)) - 1.0) * 100.0


async def gather_market_evidence(db, ticker: str, dte: Optional[int]) -> dict:
    """Ticker-level evidence (cached). Sequential on the shared session; heavy compute off the event loop
    behind a global semaphore (the prod box is one 512 MiB instance)."""
    from .cache_service import get_cached, set_cached
    from .desk_review_service import _tf_band, _norm_ticker, _release_memory
    sym = _norm_ticker(ticker)
    key = f"trademgr:ev:{sym}:{_tf_band(dte)}:{_EV_VERSION}"
    cached = await get_cached(db, key)
    if cached is not None:
        cached["_cached"] = True
        return cached

    async with _EV_SEM:
        cached = await get_cached(db, key)             # another request may have built it while we waited
        if cached is not None:
            cached["_cached"] = True
            return cached
        ev = await asyncio.to_thread(_collect_sync, sym, dte)

        # trade-setup dossier (MTF structure · VP · AVWAP · regime · dealer gamma · patterns) — shared cache
        try:
            import yfinance as yf
            from .trade_setup_service import compute_trade_setups
            ts = await get_cached(db, f"setups:{sym}:v3")
            if ts is None:
                ts = await asyncio.to_thread(lambda: compute_trade_setups(yf.Ticker(sym)))
                if ts:
                    await set_cached(db, f"setups:{sym}:v3", _clean(ts), ttl_seconds=900)
            ev["structure"] = _clean(_setup_digest(ts)) if ts else {}
        except Exception as exc:  # noqa: BLE001
            logger.info("trade-manager setups failed %s: %s", sym, exc)
            ev["structure"] = {}
        ev["sources_ok"]["structure"] = bool(ev["structure"])

        # regime-conditional edge (historical expectancy of the canonical signals in THIS regime)
        try:
            import yfinance as yf
            from .regime_edge_service import compute_regime_edge
            re_ = await get_cached(db, f"regime-edge:{sym}:h10:v1")
            if re_ is None:
                re_ = await asyncio.to_thread(lambda: compute_regime_edge(yf.Ticker(sym), horizon=10))
                if re_:
                    await set_cached(db, f"regime-edge:{sym}:h10:v1", _clean(re_), ttl_seconds=3600)
            ev["regime_edge"] = _clean(re_) if re_ else None
        except Exception:  # noqa: BLE001
            ev["regime_edge"] = None

        # macro (FRED) + peers + recent filings — all best-effort, sequential on the session
        mk = ev.setdefault("market", {})
        try:
            from .debate_service import _macro_snapshot, _MACRO_SERIES
            snap, _ = await _macro_snapshot()
            mk["fred"] = {_MACRO_SERIES.get(k, k): v for k, v in snap.items()}      # labelled — raw series ids mean nothing to a reader/LLM
        except Exception:  # noqa: BLE001
            mk["fred"] = {}
        try:
            from . import correlated_assets_service as cas
            peers = [p for p in (await cas.sector_peers(sym, db)) if p != sym][:6]
            peer_rows, peer_news = [], []
            if peers:
                rets = await cas.fetch_returns([sym] + peers, db, "1y")
                me21, me63 = _cum_ret(rets.get(sym) or [], 21), _cum_ret(rets.get(sym) or [], 63)
                for p in peers:
                    r21, r63 = _cum_ret(rets.get(p) or [], 21), _cum_ret(rets.get(p) or [], 63)
                    if r21 is None and r63 is None:
                        continue
                    peer_rows.append({"ticker": p, "ret_21d_pct": _r(r21, 1), "ret_63d_pct": _r(r63, 1)})
                med21 = float(np.median([x["ret_21d_pct"] for x in peer_rows if x["ret_21d_pct"] is not None] or [0]))
                med63 = float(np.median([x["ret_63d_pct"] for x in peer_rows if x["ret_63d_pct"] is not None] or [0]))
                mk["peers"] = {"rows": peer_rows, "median_21d_pct": _r(med21, 1), "median_63d_pct": _r(med63, 1),
                               "self_21d_pct": _r(me21, 1), "self_63d_pct": _r(me63, 1),
                               "self_vs_peers_21d_pct": _r(me21 - med21, 1) if me21 is not None else None,
                               "self_vs_peers_63d_pct": _r(me63 - med63, 1) if me63 is not None else None}
                for p in [x["ticker"] for x in peer_rows][:3]:
                    try:
                        for n in (await asyncio.to_thread(_news_for, p, 3)):
                            peer_news.append({"peer": p, "title": n.get("title"), "date": n.get("date"),
                                              "publisher": n.get("publisher")})
                    except Exception:  # noqa: BLE001
                        continue
                mk["peer_news"] = peer_news
        except Exception as exc:  # noqa: BLE001
            logger.info("trade-manager peers failed %s: %s", sym, exc)
        ev["sources_ok"]["peers"] = bool((mk.get("peers") or {}).get("rows"))
        try:
            from .edgar_service import gather_company_filings
            fil = await gather_company_filings(db, sym, max_8k=2)
            ev["events"]["filings"] = [{"form": d.get("form"), "date": d.get("date"), "url": d.get("url"),
                                        "excerpt": (d.get("excerpt") or "")[:1200] if str(d.get("form", "")).startswith(("8-K", "6-K")) else None}
                                       for d in (fil.get("docs") or [])]
        except Exception:  # noqa: BLE001
            ev["events"]["filings"] = []
        ev["sources_ok"]["filings"] = bool(ev["events"].get("filings"))
        ev["as_of"] = dt.datetime.utcnow().strftime("%Y-%m-%d %H:%M UTC")
        ev = _clean(ev)
        _release_memory()
    await set_cached(db, key, ev, ttl_seconds=_EV_TTL)
    return ev


# ── 2. the position ──────────────────────────────────────────────────────────

def _epoch_to_date(x) -> Optional[str]:
    """yfinance epoch seconds (or an ISO string) → 'YYYY-MM-DD'; None when unparseable."""
    try:
        if x is None or x == "":
            return None
        if isinstance(x, (int, float, np.integer, np.floating)):
            return dt.datetime.fromtimestamp(int(x), dt.timezone.utc).date().isoformat()
        return dt.date.fromisoformat(str(x)[:10]).isoformat()
    except (TypeError, ValueError, OSError, OverflowError):
        return None


def _days_to(exp: Optional[str]) -> Optional[int]:
    try:
        return (dt.date.fromisoformat(str(exp)[:10]) - dt.date.today()).days
    except (TypeError, ValueError):
        return None


def build_profile(strategy: dict, pnl: dict) -> dict:
    """What the user actually holds. ``strategy`` = {ticker, name, strategy_type, legs_data, parameters, notes}."""
    legs = strategy.get("legs_data") or []
    params = strategy.get("parameters") or {}
    a = pnl.get("analysis") or {}
    ng = pnl.get("net_greeks") or {}
    spot = _num(pnl.get("underlying_price"))
    opts, stock_sh = [], 0.0
    for l in legs:
        typ = str(l.get("type", "")).lower()
        act = str(l.get("action", "")).upper()
        sgn = -1 if "SELL" in act or "SHORT" in act else 1
        q = int(l.get("qty") or l.get("contracts") or 1)
        if l.get("strike") and ("call" in typ or "put" in typ):
            opts.append({"right": "P" if "put" in typ else "C", "side": sgn, "strike": float(l["strike"]), "qty": q,
                         "exp": str(l.get("expiration") or l.get("expiry") or "")[:10] or None})
        elif "stock" in typ or "share" in typ or "equity" in typ:
            stock_sh += sgn * abs(float(l.get("qty") or l.get("shares") or 0.0))
    # normalise by the larger single-RIGHT contract count (a vertical spread's two legs are ONE unit of
    # exposure, not two) so a 1-lot put credit spread's ~+0.15Δ reads as 0.15, not 0.07
    def _unit(right: str) -> int:
        lg = sum(o["qty"] for o in opts if o["right"] == right and o["side"] > 0)
        sh = sum(o["qty"] for o in opts if o["right"] == right and o["side"] < 0)
        return max(lg, sh)
    # Pure stock / futures trades keep their size in parameters.shares (signed by "short" in the strategy type) and an EMPTY
    # legs_data — reading only the legs saw ZERO shares and classified every share trade as direction-neutral.
    stype = str(strategy.get("strategy_type") or "").lower()
    if not stock_sh:                                  # also covers hybrids (covered call: option legs + parameters.shares)
        psh = _num(params.get("shares")) or 0.0
        if not psh and (stype == "futures" or stype.startswith("stock") or not opts):
            psh = _num(params.get("contracts")) or _num(params.get("quantity")) or 0.0
        if psh:
            stock_sh = -abs(psh) if "short" in stype else abs(psh)
    gross = max(_unit("C"), _unit("P")) * 100 + abs(stock_sh)
    net_delta = _num(ng.get("delta"))
    if net_delta is None:
        net_delta = stock_sh
    if not opts and not stock_sh and net_delta:       # last resort: the live position delta IS the share count
        stock_sh = net_delta
        gross = abs(net_delta)
    dir_raw = (net_delta / gross) if gross else 0.0
    theta, vega, gamma = _num(ng.get("theta")), _num(ng.get("vega")), _num(ng.get("gamma"))
    short_prem = bool(theta is not None and theta > 0 and (vega is None or vega <= 0))
    long_prem = bool(theta is not None and theta < 0 and (vega is None or vega >= 0))
    direction = "bullish" if dir_raw >= 0.12 else "bearish" if dir_raw <= -0.12 else "neutral"
    pos_sign = 1 if direction == "bullish" else -1 if direction == "bearish" else 0
    range_play = bool(direction == "neutral" and short_prem)
    long_vol = bool(direction == "neutral" and long_prem)
    short_calls = sorted(o["strike"] for o in opts if o["right"] == "C" and o["side"] < 0)
    short_puts = sorted((o["strike"] for o in opts if o["right"] == "P" and o["side"] < 0), reverse=True)
    long_calls = sorted(o["strike"] for o in opts if o["right"] == "C" and o["side"] > 0)
    long_puts = sorted((o["strike"] for o in opts if o["right"] == "P" and o["side"] > 0), reverse=True)
    n_short_c = sum(o["qty"] for o in opts if o["right"] == "C" and o["side"] < 0)
    covered = bool(params.get("covered")) or (stock_sh >= 100 * n_short_c > 0)
    nc, np_ = (short_calls[0] if short_calls else None), (short_puts[0] if short_puts else None)
    exps = [d for d in (_days_to(o["exp"]) for o in opts) if d is not None]
    dte = a.get("dte_remaining") if a.get("dte_remaining") is not None else (min(exps) if exps else None)
    q = a.get("quant_exit") or {}
    roll = strategy.get("roll") or {}
    entry = {
        "status": strategy.get("trade_status") or "active",
        "entry_date": (strategy.get("entry_date") or "")[:10] or None,
        "days_held": pnl.get("days_held"),
        "entry_prices": [{"leg": i_, "price": _num((e_ or {}).get("price"))} for i_, e_ in enumerate(strategy.get("entry_prices") or []) if isinstance(e_, dict)] or None,
        "entry_net": _num(strategy.get("entry_net_debit")),
        "avg_cost": _num(params.get("avg_cost")),
        "rolls": roll.get("count") or 0, "roll_realized_pnl": _num(roll.get("roll_realized_pnl")),
        "effective_breakevens": roll.get("effective_breakevens") or None,
        "realized_banked": _num(strategy.get("realized_banked")) or 0.0,
    }
    kind = ("stock" if not opts else "short_premium" if short_prem else "long_premium" if long_prem else "mixed")
    label = {"bullish": "Bullish", "bearish": "Bearish", "neutral": "Neutral / range"}[direction]
    label += {"stock": " · shares", "short_premium": " · short premium", "long_premium": " · long premium",
              "mixed": " · mixed"}[kind]
    return {
        "ticker": strategy.get("ticker"), "name": strategy.get("name"), "structure": strategy.get("strategy_type"),
        "notes": strategy.get("notes") or "", "label": label, "entry": entry,
        "legs": [{"right": o["right"], "side": "SHORT" if o["side"] < 0 else "LONG", "strike": o["strike"],
                  "qty": o["qty"], "exp": o["exp"]} for o in opts],
        "stock_shares": stock_sh, "covered": covered, "avg_cost": _num(params.get("avg_cost")),
        "spot": spot, "dte": dte, "direction": direction, "pos_sign": pos_sign, "dir_raw": round(dir_raw, 3),
        "kind": kind, "short_premium": short_prem, "long_premium": long_prem, "range_play": range_play,
        "long_vol": long_vol, "undefined_risk": bool(pnl.get("unbounded_loss")) and not covered,
        "short_call_strike": nc, "short_put_strike": np_, "long_call_strikes": long_calls, "long_put_strikes": long_puts,
        "cushion_up_pct": _r((nc - spot) / spot * 100, 2) if (nc and spot) else None,
        "cushion_down_pct": _r((spot - np_) / spot * 100, 2) if (np_ and spot) else None,
        "pnl": {"unrealized": _num(pnl.get("unrealized_pnl")), "pct": _num(pnl.get("pnl_pct")),
                "entry_cost": _num(pnl.get("entry_cost")), "current_value": _num(pnl.get("current_value")),
                "days_held": pnl.get("days_held"), "captured_pct": _num(a.get("captured_pct")),
                "max_profit": _num(pnl.get("max_profit")), "max_loss": _num(pnl.get("max_loss")),
                "max_profit_price": _num(pnl.get("max_profit_price")), "max_loss_price": _num(pnl.get("max_loss_price")),
                "unbounded_profit": bool(pnl.get("unbounded_profit")), "unbounded_loss": bool(pnl.get("unbounded_loss")),
                "breakevens": [b for b in (pnl.get("breakevens") or []) if _num(b) is not None]},
        "greeks": {"delta": net_delta, "gamma": gamma, "theta_per_day": theta, "vega": vega},
        "edge": {"pop_pct": _num(a.get("probability_of_profit")), "pop_method": a.get("pop_method"),
                 "expected_value": _num(a.get("expected_value")), "kelly": _num(a.get("kelly_fraction")),
                 "risk_reward": _num(a.get("risk_reward_ratio")), "theta_burn_pct": _num(a.get("theta_burn_rate_pct"))},
        "lifecycle": pnl.get("lifecycle") or {},
        "avg_iv_pct": _num((pnl.get("lifecycle") or {}).get("avg_iv_pct")),
        # the quant desk's own hold/close read — an INPUT to the Quant lens, never shown to the LLM
        "_quant": {"signal": q.get("signal") or ({"HOLD": "HOLD", "CLOSE": "CLOSE"}.get(a.get("exit_signal"), a.get("exit_signal"))),
                   "score": _num(q.get("score")), "overrides": q.get("overrides") or [],
                   "reasons": q.get("reasons") or a.get("exit_reasons") or [],
                   "adjustments": q.get("adjustments") or [], "factors": q.get("factors") or [],
                   "base_quality": _num(q.get("base_quality")), "hold_base": _num(q.get("hold_base")),
                   "subscores": q.get("subscores") or {},
                   "hold_vs_close": a.get("hold_vs_close"), "hold_vs_close_reasons": a.get("hold_vs_close_reasons") or [],
                   "theta_burn_pct": _num(a.get("theta_burn_rate_pct")), "days_to_theta_breakeven": _num(a.get("days_to_theta_breakeven"))},
    }


# ── 3. decision ──────────────────────────────────────────────────────────────

def since_entry(profile: dict, ev: dict) -> dict:
    """TRACKING: what has happened since you entered — days held, where the underlying was, how far it has moved WITH or AGAINST
    you, and the best / worst it got. Facts only; the position's own P&L comes from the live snapshot."""
    ent = profile.get("entry") or {}
    ps = profile.get("pos_sign", 0)
    spot = profile.get("spot") or ev.get("spot")
    out: dict = {"entry_date": ent.get("entry_date"), "days_held": ent.get("days_held"), "rolls": ent.get("rolls") or 0,
                 "roll_realized_pnl": ent.get("roll_realized_pnl"), "realized_banked": ent.get("realized_banked") or 0.0,
                 "effective_breakevens": ent.get("effective_breakevens")}
    h = ev.get("history") or {}
    dates, closes = h.get("dates") or [], h.get("closes") or []
    entry_px, src = None, None
    if profile.get("kind") == "stock" and ent.get("avg_cost"):
        entry_px, src = ent["avg_cost"], "your cost"
    idx = None
    if ent.get("entry_date") and dates:
        for i_, d_ in enumerate(dates):
            if d_ >= ent["entry_date"]:
                idx = i_
                break
        if idx is not None and idx == 0 and dates[0] > ent["entry_date"]:
            idx = None                                           # entered before our history window — can't place it
    if entry_px is None and idx is not None:
        entry_px, src = closes[idx], "close on the entry date"
    if entry_px and spot:
        mv = (spot / entry_px - 1) * 100
        out.update(underlying_entry=_r(entry_px), underlying_entry_source=src, underlying_now=_r(spot), move_pct=_r(mv, 1))
        if ps != 0:
            with_you = mv * (1 if ps > 0 else -1)
            out["vs_you"] = "with you" if with_you > 0.5 else "against you" if with_you < -0.5 else "flat"
    if idx is not None and entry_px:
        seg = closes[idx:]
        out["high_since_pct"] = _r((max(seg) / entry_px - 1) * 100, 1)
        out["low_since_pct"] = _r((min(seg) / entry_px - 1) * 100, 1)
        out["bars_since_entry"] = len(seg) - 1
    return out


def _atr_of(ev: dict, spot: float) -> float:
    s = ((ev.get("technical") or {}).get("suite") or {})
    return _num(s.get("atr14")) or (spot * 0.02 if spot else 0.0)


def _tech_signals(ev: dict, spot: float) -> list[dict]:
    """Directional (bull +1 / bear −1) technical readings — evidence → algorithm judgement."""
    t = ev.get("technical") or {}
    suite = t.get("suite") or {}
    cons = t.get("consensus") or {}
    st = ev.get("structure") or {}
    dos = st.get("dossier") or {}
    sig: list[dict] = []

    def add(fam, name, d, w, note):
        if d is not None:
            sig.append({"family": fam, "name": name, "d": round(_clamp(d, -1, 1), 3), "w": w, "note": note})

    if cons.get("n"):
        add("traders", "Famous-trader rulesets", cons["d"], 4.0,
            f"{cons.get('bull')} bullish · {cons.get('bear')} bearish · {cons.get('neutral')} neutral of {cons['n']}")
    ms = dos.get("market_structure") or {}
    bias = (ms.get("bias") or {}).get("overall")
    if bias:
        add("structure", "Multi-timeframe market structure", {"bullish": 0.7, "bearish": -0.7}.get(bias, 0.0), 2.0,
            f"{bias} · D/4H/1H = " + "/".join(str((ms.get('trend_alignment') or {}).get(k) or '–') for k in ('daily', 'h4', 'h1')))
    vp = (dos.get("volume_profile") or {}).get("daily") or (dos.get("volume_profile") or {}).get("macro") or {}
    if vp.get("poc") and spot:
        vah, val_, poc = vp.get("vah"), vp.get("val"), vp["poc"]
        d = 0.6 if (vah and spot > vah) else -0.6 if (val_ and spot < val_) else _clamp((spot - poc) / (poc * 0.05), -0.4, 0.4)
        add("volume_profile", "Price vs value area (daily VP)", d, 1.5, f"POC {poc} · VA {val_}–{vah}")
    dg = dos.get("dealer_gamma") or {}
    flip = ((dg.get("gamma_flip") or {}).get("level"))
    if flip and spot:
        add("dealer", "Dealer gamma flip", 0.3 if spot >= flip else -0.3, 1.0, f"spot {'above' if spot >= flip else 'below'} flip {flip}")
    pats = st.get("patterns") or []
    pd_ = [(1 if p.get("direction") == "bullish" else -1 if p.get("direction") == "bearish" else 0) * (p.get("confidence") or 0.5)
           for p in pats if p.get("status") in ("confirmed", "forming", "active", "breakout", "retest")]
    if pd_:
        add("patterns", "Chart patterns", float(np.mean(pd_)), 1.0, f"{len(pd_)} active")
    div = 0.0
    if suite.get("rsi_divergence") == "bearish": div -= 0.5
    if suite.get("rsi_divergence") == "bullish": div += 0.5
    if suite.get("obv_divergence") == "bearish": div -= 0.4
    if suite.get("obv_divergence") == "bullish": div += 0.4
    if suite.get("macd_divergence") == "bearish": div -= 0.3
    if suite.get("macd_divergence") == "bullish": div += 0.3
    add("momentum", "Momentum divergences (RSI/OBV/MACD)", div, 1.5,
        f"rsi {suite.get('rsi_divergence') or 'none'} · obv {suite.get('obv_divergence') or 'none'} · macd {suite.get('macd_divergence') or 'none'}")
    if suite.get("cmf20") is not None:
        add("flow", "Money flow (CMF20 / up-down volume)", _clamp(suite["cmf20"] * 4, -1, 1) * 0.6 +
            (0.4 if (suite.get("up_down_volume_ratio_20d") or 1) > 1.15 else -0.4 if (suite.get("up_down_volume_ratio_20d") or 1) < 0.87 else 0.0),
            1.0, f"CMF {suite['cmf20']} · up/down vol {suite.get('up_down_volume_ratio_20d')}")
    rs63 = (((suite.get("relative_strength") or {}).get("excess_return_pct") or {}).get("63"))
    if rs63 is not None:
        add("relative_strength", "Relative strength vs market (63d)", _clamp(rs63 / 15.0, -1, 1), 1.0, f"{rs63:+.1f}% vs SPY")
    return sig



def _norm_cdf(x: float) -> float:
    return 0.5 * math.erfc(-x / math.sqrt(2.0))


def _vol_context(profile: dict, ev: dict) -> dict:
    """Forecast σ over the remaining DTE and each short strike's distance in σ.

    Backtested (2016-26, 99 names, vol-normalised): technical features add no information on the SIZE or DIRECTION of
    the next move beyond the volatility level — except the short/long realized-vol ratio. So breach risk is measured
    in σ (market IV when we have it, else the blended RV), the way an options desk does, not in chart terms."""
    spot = profile["spot"] or ev.get("spot") or 0.0
    vf = (((ev.get("technical") or {}).get("suite") or {}).get("vol_forecast")) or {}
    blend = _num(vf.get("blend_ann_pct"))
    iv = _num(profile.get("avg_iv_pct"))
    cands = [x for x in (iv, blend) if x]
    sigma = max(cands) if cands else None                          # conservative: the higher of implied and realized
    dte = profile.get("dte") if profile.get("dte") else 30
    out = {"sigma_ann_pct": _r(sigma, 1), "iv_pct": _r(iv, 1), "rv_blend_pct": _r(blend, 1), "ratio_21_63": vf.get("ratio_21_63"),
           "regime": vf.get("regime"), "dte": dte, "strikes": [],
           "horizon": "the next month (21 trading days)" if profile.get("kind") == "stock" else f"expiry ({dte} days)",
           "horizon_short": "next month" if profile.get("kind") == "stock" else "expiry"}
    if not (sigma and spot):
        return out
    s_dte = sigma / 100.0 * math.sqrt(max(dte, 1) / 365.0)
    out["sigma_dte_pct"] = _r(s_dte * 100, 2)
    for k, side in ((profile.get("short_call_strike"), "call"), (profile.get("short_put_strike"), "put")):
        if not k:
            continue
        z = math.log(k / spot) / s_dte if s_dte > 0 else None
        breached = (spot >= k) if side == "call" else (spot <= k)
        p_touch = 1.0 if breached else (min(1.0, 2.0 * (1.0 - _norm_cdf(abs(z)))) if z is not None else None)
        out["strikes"].append({"side": side, "strike": k, "z_sigma": _r(abs(z), 2) if z is not None else None,
                               "p_touch": _r(p_touch, 3), "breached": bool(breached)})
    return out


def _technical_score(profile: dict, ev: dict, sig: list[dict]) -> dict:
    """Technical lens — rebuilt after the backtest (2016-26, 52k point-in-time samples, 99 tickers):

    * Trend / trader-rule alignment has ~0 out-of-sample directional edge at 21-63d (IC ≈ 0; adverse flags are even
      followed by HIGHER mean returns — 1-month reversal — but by a fatter left tail). It is kept as CONTEXT with a
      small gain (±11 pts), and discounted when oversold/overbought (reversals are common).
    * What IS validated: the vol regime (short/long realized-vol ratio), drawdown depth (P(≤−8% in 21d) 14-17%
      when ≥10-20% off the high vs 7-8%), and — for short premium — the strike's distance in forecast σ.
    """
    spot = profile["spot"] or ev.get("spot") or 0.0
    suite = (ev.get("technical") or {}).get("suite") or {}
    tech_dir = (sum(s["d"] * s["w"] for s in sig) / sum(s["w"] for s in sig)) if sig else 0.0
    adxv = ((suite.get("adx") or {}).get("adx"))
    adx_s = 0.5 if adxv is None else _clamp((adxv - 18.0) / 22.0, 0, 1)
    dos = ((ev.get("structure") or {}).get("dossier") or {})
    reg_lbl = str((((dos.get("regime") or {}).get("overall")) or "")).lower()
    vc = _vol_context(profile, ev)
    notes: list[str] = []
    ps = profile["pos_sign"]
    rsi = _num(suite.get("rsi14"))
    oversold, overbought = (rsi is not None and rsi < 35), (rsi is not None and rsi > 70)
    downside = bool(profile["short_put_strike"]) or (profile["kind"] == "stock" and ps > 0) or ps > 0
    upside = bool(profile["short_call_strike"]) or (profile["kind"] == "stock" and ps < 0) or ps < 0

    # 1) structure alignment — CONTEXT only
    if profile["kind"] == "stock" or ps != 0:
        s = ps * tech_dir
        gain = 22.0
        if s < 0 and ((ps > 0 and oversold) or (ps < 0 and overbought)):
            gain *= 0.5
            notes.append(f"RSI {rsi:.0f} — adverse structure discounted: oversold/overbought names mean-revert within a month more often than they continue")
        score = 50 + gain * _clamp(1.15 * s, -1, 1)
        notes.append(f"{'bullish' if ps > 0 else 'bearish'} position vs technical consensus {tech_dir:+.2f} → alignment {s:+.2f} (context: ≈0 backtested directional edge, so low gain)")
    elif profile["range_play"]:
        trend_strength = _clamp(0.55 * abs(tech_dir) + 0.45 * adx_s, 0, 1)
        score = 64 - 14 * trend_strength
        notes.append(f"range structure: trend strength {trend_strength:.2f} (|consensus| {abs(tech_dir):.2f}, ADX {adxv})")
        if "mean" in reg_lbl or "range" in reg_lbl or "revert" in reg_lbl:
            score += 4; notes.append("regime favours mean-reversion / range (+4)")
        elif "trend" in reg_lbl:
            score -= 3; notes.append("regime is trending — adverse for a range structure (−3)")
        if (((dos.get("dealer_gamma") or {}).get("net_gex") or {}).get("sign")) == "long":
            score += 3; notes.append("dealers long gamma → pinning (+3)")
        if suite.get("squeeze_on"):
            score -= 4; notes.append("volatility squeeze ON — expansion due (−4)")
    elif profile["long_vol"]:
        score = 50 + (6 if suite.get("squeeze_on") else 0)
        notes.append("long-volatility position: direction-free; a squeeze (+6) means expansion is loading" if suite.get("squeeze_on") else "long-volatility position: direction-free")
    else:
        score = 50.0

    # 2) vol regime — validated (for shares this lives in the Position-risk lens instead)
    ratio = _num(vc.get("ratio_21_63"))
    if ratio is not None and profile["kind"] != "stock":
        if ratio >= 1.4 and (profile["short_premium"] or profile["kind"] == "stock"):
            score -= 7; notes.append(f"volatility EXPANDING (21d RV is {ratio:.2f}× the 63d) — wider moves ahead (−7)")
        elif ratio >= 1.4 and profile["long_premium"]:
            score += 3; notes.append(f"volatility expanding ({ratio:.2f}×) — helps long premium (+3)")
        elif ratio <= 0.75 and profile["short_premium"]:
            score += 2; notes.append(f"volatility compressing ({ratio:.2f}×) — calmer tape (+2)")

    # 3) drawdown depth — validated left-tail state
    fh = _num((suite.get("range") or {}).get("pct_from_52w_high"))
    if fh is not None and downside and profile["kind"] != "stock":
        if fh <= -20:
            score -= 6; notes.append(f"{abs(fh):.0f}% below the 52-wk high — left-tail state (P(≤−8% in 21d) ≈ 17% vs 8% base) (−6)")
        elif fh <= -10:
            score -= 3; notes.append(f"{abs(fh):.0f}% below the 52-wk high — fatter left tail (−3)")

    # 4) short-strike breach risk in forecast σ (shares the quant desk's P(touch) idea — kept moderate to avoid double counting)
    if profile["short_premium"] and vc["strikes"]:
        worst = max(vc["strikes"], key=lambda x: x["p_touch"] or 0)
        pt = worst["p_touch"] or 0.0
        pen = 22 if worst["breached"] else 18 if pt >= 0.5 else 12 if pt >= 0.35 else 6 if pt >= 0.2 else 2 if pt >= 0.12 else 0
        if pen:
            score -= pen
            notes.append(f"short {worst['side']} ${worst['strike']:g} is {worst['z_sigma']}σ away ({vc['sigma_dte_pct']}% 1σ to expiry) → P(touch) ≈ {pt * 100:.0f}% (−{pen})")
    return {"score": round(_clamp(score), 1), "dir": round(tech_dir, 3), "notes": notes, "signals": sig, "vol": vc, "scope": _lens_scope(profile)["technical"]}


def _fundamental_score(profile: dict, ev: dict) -> dict:
    f = ev.get("fundamental") or {}
    pill = f.get("pillars") or {}
    if f.get("is_fund") or not pill:
        return {"score": 55.0, "dir": 0.0, "available": False,
                "notes": ["fund / index / no fundamentals available — neutral, low weight"], "factors": []}
    W = {"fundamental": .22, "valuation": .14, "sentiment_targets": .08, "catalyst_revisions": .16, "quality_capital": .14,
         "ownership_flow": .06, "structural": .08, "macro_sensitivity": .06, "geopolitical": .03, "sector_rotation": .03}
    num = den = 0.0
    factors = []
    for k, w in W.items():
        sc = (pill.get(k) or {}).get("score")
        if sc is None:
            continue
        num += sc * w; den += w
        factors.append({"pillar": k, "exit_pressure": sc})
    pressure = num / den if den else 30.0
    d = _clamp((30.0 - pressure) / 30.0, -1, 1)
    notes = [f"10-pillar exit pressure {pressure:.0f} (neutral ≈ 30; lower = healthier)"]
    an = f.get("analyst") or {}
    if (an.get("downgrades_90d") or 0) >= 2 and (an.get("upgrades_90d") or 0) == 0:
        d -= 0.25; notes.append(f"{an['downgrades_90d']} analyst downgrades / 0 upgrades in 90d (−0.25)")
    elif (an.get("upgrades_90d") or 0) >= 2 and (an.get("downgrades_90d") or 0) == 0:
        d += 0.2; notes.append(f"{an['upgrades_90d']} upgrades / 0 downgrades in 90d (+0.2)")
    tm, px = an.get("target_mean"), ev.get("spot")
    if tm and px:
        up = (tm / px - 1) * 100
        if up < -5: d -= 0.15; notes.append(f"price is {abs(up):.0f}% ABOVE the mean analyst target (−0.15)")
        elif up > 20: d += 0.1; notes.append(f"mean analyst target {up:.0f}% above price (+0.1)")
    sur = (f.get("estimates") or {}).get("earnings_surprises") or []
    miss = sum(1 for s in sur if (_num(s.get("surprisePercent")) or 0) < 0)
    if len(sur) >= 3 and miss >= 3:
        d -= 0.15; notes.append(f"{miss} of last {len(sur)} EPS prints missed (−0.15)")
    d = _clamp(d, -1, 1)
    ps = profile["pos_sign"]
    if profile["kind"] == "stock" or ps != 0:
        score = 50 + 45 * ps * d
    elif profile["range_play"]:
        score = 62 + 28 * min(d, 0.0) + 6 * max(d, 0.0)          # fundamentals only matter as TAIL risk
        notes.append("range structure: fundamentals count as tail-risk only")
    else:
        score = 55.0
    return {"score": round(_clamp(score), 1), "dir": round(d, 3), "available": True, "notes": notes, "factors": factors}


def _event_score(profile: dict, ev: dict) -> dict:
    e = ev.get("events") or {}
    mk = ev.get("market") or {}
    dte = profile["dte"]
    score, notes, items = 72.0, [], []
    sp, lp = profile["short_premium"], profile["long_premium"]
    d_earn = e.get("days_to_earnings")
    if d_earn is not None and profile["kind"] == "stock":
        # shares have no expiry window — only a NEAR print matters (the gap can jump your stop)
        pen = 10 if d_earn <= 7 else 6 if d_earn <= 14 else 3 if d_earn <= 30 else 0
        if d_earn <= 1:
            pen += 6
        if pen:
            score -= pen
            notes.append(f"earnings in {d_earn}d — the gap can jump your levels (−{pen:.0f})")
            items.append({"event": "earnings", "in_days": d_earn, "date": e.get("earnings_date")})
    elif d_earn is not None and (dte is None or d_earn <= dte):
        pen = 0.0
        if sp:
            pen = 25 if profile["undefined_risk"] else 18
        elif lp:
            pen = 4
        else:
            pen = 10
        if d_earn <= 1:
            pen += 8
        score -= pen
        notes.append(f"earnings in {d_earn}d, inside the trade window (−{pen:.0f})")
        items.append({"event": "earnings", "in_days": d_earn, "date": e.get("earnings_date")})
    xd = e.get("ex_dividend_date")
    dd = _days_to(xd) if xd else None
    if dd is not None and 0 <= dd and (dte is None or dd <= dte) and profile["short_call_strike"]:
        cu = profile.get("cushion_up_pct")
        if cu is not None and cu < 4:
            score -= 14
            notes.append(f"ex-dividend in {dd}d with the short call only {cu:.1f}% OTM — early-assignment risk (−14)")
        items.append({"event": "ex_dividend", "in_days": dd, "date": xd})
    tape = mk.get("tape") or {}
    vix = (tape.get("^VIX") or {})
    if vix.get("last"):
        if vix["last"] >= 30 and sp:
            score -= 8; notes.append(f"VIX {vix['last']:.0f} ≥ 30 — stressed tape for short premium (−8)")
        elif vix["last"] >= 30 and lp:
            score += 4
        if (vix.get("change_pct_5d") or 0) >= 25 and sp:
            score -= 6; notes.append(f"VIX +{vix['change_pct_5d']:.0f}% in 5d — vol spike (−6)")
    hf = e.get("headline_flags") or {}
    if hf.get("n_risk"):
        pen = min(12, 4 * hf["n_risk"])
        score -= pen
        notes.append(f"{hf['n_risk']} risk-keyword headline(s) (−{pen})")
    if hf.get("n_positive") and profile["pos_sign"] > 0:
        score += min(4, 2 * hf["n_positive"])
    fil = [d for d in (e.get("filings") or []) if str(d.get("form", "")).startswith(("8-K", "6-K"))]
    def _age(d: dict) -> Optional[int]:
        try:
            return (dt.date.today() - dt.date.fromisoformat(str(d.get("date"))[:10])).days
        except (TypeError, ValueError):
            return None
    recent = [d for d in fil if (_age(d) is not None and _age(d) <= 10)]
    if recent:
        score -= 3 * len(recent)
        notes.append(f"{len(recent)} 8-K/6-K filed in the last 10 days — a fresh corporate event to read (−{3 * len(recent)})")
    peers = mk.get("peers") or {}
    if (peers.get("self_vs_peers_21d_pct") is not None) and profile["pos_sign"] != 0:
        rel = peers["self_vs_peers_21d_pct"]
        score += _clamp(rel / 10.0, -1, 1) * 4 * profile["pos_sign"]
    pts = round(72.0 - score, 1)                       # >0 = risk points, <0 = a small tailwind (headlines / peers)
    return {"score": round(_clamp(score), 1), "pts": pts, "notes": notes, "items": items, "clear": pts <= 0 and not items}



def _fm(x, nd=2):
    v = _num(x)
    return None if v is None else round(v, nd)


def _lens_scope(profile: dict) -> dict:
    """What the Quant and Technical lenses MEAN for THIS kind of position (the two are easy to confuse)."""
    if profile.get("kind") == "stock":
        return {
            "quant": "For shares there is no option payoff to price (no odds-of-profit, theta or greeks), so Quant is a POSITION-RISK read in volatility terms: "
                     "how big a normal move is, how far you are from the −10% discipline stop, drawdown from the high, beta. It carries a small weight; "
                     "the verdict leans on the Technical, Fundamental and Event lenses.",
            "technical": "For shares the chart is the main timing evidence — trend, support/resistance, the 50/150/200d ladder, momentum. Backtests found ~0 "
                         "directional edge in these signals, so Technical supplies LEVELS and risk state (volatility regime, drawdown) rather than a forecast.",
        }
    return {
        "quant": "For an options position Quant asks whether THIS trade's own math still works — odds of profit, edge left (EV, Omega), theta/gamma/vega, "
                 "how far the strikes/breakevens are in σ, IV vs realized, tail loss, P&L vs the max. It is independent of the chart and leads the verdict, "
                 "because the backtests found this (strike distance in σ, vol regime, event gaps) is where the information is.",
        "technical": "For an options position the chart only matters through how likely price is to REACH your strikes/breakevens and where it would break: "
                     "trend & structure for the levels, volatility regime and drawdown depth for the tails. Backtests found ~0 directional edge, so it carries a small weight.",
    }


def _quant_detail(profile: dict, ev: dict) -> dict:
    """The POSITION's own math — independent of the chart. Facts + a tone per metric (good / warn / bad) so the Quant tab
    can show WHAT it measures: edge & odds, risk, greeks & time, breakevens, and the quant desk's factor list."""
    q = profile.get("_quant") or {}
    e, pn, gr = profile.get("edge") or {}, profile.get("pnl") or {}, profile.get("greeks") or {}
    lc = profile.get("lifecycle") or {}
    pm, risk, tr = lc.get("pm") or {}, lc.get("risk") or {}, lc.get("trader") or {}
    vc = _vol_context(profile, ev)
    spot = profile.get("spot") or 0.0
    groups: dict[str, list[dict]] = {"Edge & odds": [], "Risk": [], "Greeks & time": [], "Breakevens": []}

    stock_kind = profile.get("kind") == "stock"
    OPTION_ONLY = {"Greeks & time", "Breakevens"}

    def add(group, key, label, value, fmt, tone=None, note=None):
        if value is None or (stock_kind and (group in OPTION_ONLY or key in ("pop", "ev", "kelly", "rr", "omega", "sortino", "captured", "iv_rv", "cvar", "var", "max_loss"))):
            return                                           # option-only metric on a share position — don't show it
        groups[group].append({"key": key, "label": label, "value": value, "fmt": fmt, "tone": tone, "note": note})

    pop = _fm(e.get("pop_pct"), 0)
    add("Edge & odds", "pop", "Probability of profit", pop, "pct", None if pop is None else "good" if pop >= 65 else "warn" if pop >= 45 else "bad",
        f"{e.get('pop_method') or 'model'} — chance the position finishes in profit at expiry")
    ev_ = _fm(e.get("expected_value"), 0)
    add("Edge & odds", "ev", "Expected value", ev_, "usd", None if ev_ is None else "good" if ev_ > 0 else "bad", "probability-weighted P&L at expiry")
    kel = _fm(e.get("kelly"), 2)
    add("Edge & odds", "kelly", "Kelly fraction", kel, "num", None if kel is None else "good" if kel > 0.05 else "warn" if kel > 0 else "bad", "≤ 0 means no sizing edge")
    mp, ml = pn.get("max_profit"), pn.get("max_loss")
    if mp and ml and not pn.get("unbounded_profit") and not pn.get("unbounded_loss") and ml != 0:
        rr = abs(mp / ml)
        add("Edge & odds", "rr", "Max reward : max risk", round(rr, 2), "ratio", "good" if rr >= 1 else "warn" if rr >= 0.3 else "bad", f"+${mp:,.0f} vs −${abs(ml):,.0f}")
    add("Edge & odds", "omega", "Omega", _fm(pm.get("omega"), 2), "num", None, "gain-weighted ÷ loss-weighted payoff (> 1 = positive skew of outcomes)")
    add("Edge & odds", "sortino", "Sortino", _fm(pm.get("sortino"), 2), "num", None, "return per unit of downside deviation")
    cap = _fm(pn.get("captured_pct"), 0)
    if profile.get("short_premium"):
        add("Edge & odds", "captured", "Profit captured", cap, "pct", None if cap is None else "good" if cap >= 50 else "warn" if cap >= 0 else "bad", "of max profit — the playbook banks 50%")
    iv, blend = _fm(vc.get("iv_pct"), 1), _fm(vc.get("rv_blend_pct"), 1)
    if iv and blend:
        ratio = iv / blend
        lp = profile.get("long_premium")
        add("Edge & odds", "iv_rv", "Implied ÷ realized vol", round(ratio, 2), "mult",
            ("bad" if ratio >= 1.3 else "warn" if ratio >= 1.1 else "good") if lp else ("good" if ratio >= 1.1 else "warn" if ratio >= 0.9 else "bad"),
            f"IV {iv}% vs RV {blend}% — " + ("you PAID for rich vol" if lp and ratio >= 1.1 else "premium is rich → favours sellers" if ratio >= 1.1 else "options are cheap vs what the stock actually does" if lp else "premium is thin vs realized"))
    # risk
    cvar, capital = _num(risk.get("cvar_95")), _num(risk.get("capital"))
    if cvar is not None and capital:
        add("Risk", "cvar", "CVaR 95% (expected tail loss)", round(abs(cvar) / capital * 100, 1), "pct_cap", "good" if abs(cvar) / capital < 0.1 else "warn" if abs(cvar) / capital < 0.25 else "bad", f"${abs(cvar):,.0f} on ${capital:,.0f} at risk")
    var = _num(risk.get("var_95"))
    if var is not None:
        add("Risk", "var", "VaR 95%", round(abs(var)), "usd", None, "loss not exceeded in 95% of outcomes")
    add("Risk", "max_loss", "Max loss", None if (ml is None or pn.get("unbounded_loss")) else round(ml), "usd", None, "UNBOUNDED" if pn.get("unbounded_loss") else None)
    if pn.get("unbounded_loss") and not stock_kind:
        groups["Risk"].append({"key": "max_loss", "label": "Max loss", "value": "unbounded", "fmt": "text", "tone": "bad", "note": "undefined-risk structure — size and hedge accordingly"})
    add("Risk", "unrealized", "Unrealized P&L", _fm(pn.get("unrealized"), 0), "usd", None if pn.get("unrealized") is None else "good" if pn["unrealized"] >= 0 else "warn",
        None if pn.get("pct") is None else f"{pn['pct']:+.1f}% on the trade")
    for stt in vc.get("strikes") or []:
        pt = stt.get("p_touch")
        add("Risk", f"touch_{stt['side']}", f"Short {stt['side']} ${stt['strike']:g} — P(touch)", None if pt is None else round(pt * 100), "pct",
            None if pt is None else "bad" if pt >= 0.5 else "warn" if pt >= 0.25 else "good", None if stt.get("z_sigma") is None else f"{stt['z_sigma']}σ from price over the remaining life")
    # greeks & time — OPTIONS only (shares have no theta, gamma, vega, expiry)
    th = _num(gr.get("theta_per_day"))
    val = abs(_num(pn.get("current_value")) or 0)
    is_opt = profile.get("kind") != "stock"
    if is_opt and th is not None:
        pct_day = (abs(th) / val * 100) if val else None
        bad_theta = (th < 0 and pct_day is not None and pct_day >= 4)
        add("Greeks & time", "theta", "Theta / day", round(th, 2), "usd", "good" if th > 0 else ("bad" if bad_theta else "warn"),
            ("you collect this every day" if th > 0 else "you PAY this every day") + (f" ({pct_day:.1f}% of the position's value)" if pct_day else ""))
    dtb = _num(q.get("days_to_theta_breakeven"))
    if is_opt:
        add("Greeks & time", "theta_be", "Days of theta to break even", None if dtb is None else round(dtb), "num", None, "how long the decay takes to eat the edge")
    add("Greeks & time", "delta", "Delta (share-equiv.)", _fm(gr.get("delta"), 1), "num", None, "P&L per $1 move in the stock")
    add("Greeks & time", "gamma", "Gamma", _fm(gr.get("gamma"), 3), "num", None, "how fast delta changes — " + ("short gamma: big moves hurt" if (gr.get("gamma") or 0) < 0 else "long gamma: big moves help"))
    add("Greeks & time", "vega", "Vega", _fm(gr.get("vega"), 1), "num", None, ("short vega: an IV spike hurts" if (gr.get("vega") or 0) < 0 else "long vega: an IV rise helps, an IV crush hurts"))
    add("Greeks & time", "dte", "Days to expiry", profile.get("dte"), "num", "warn" if (profile.get("dte") is not None and profile["dte"] <= 7) else None, "gamma/pin risk rises inside ~7 days")
    # shares: no payoff model (no PoP / theta / greeks) → the quant read is POSITION RISK in volatility terms
    if profile.get("kind") == "stock":
        sig_ann = _num(vc.get("sigma_ann_pct"))
        suite_ = (ev.get("technical") or {}).get("suite") or {}
        if sig_ann:
            s21 = sig_ann * math.sqrt(21 / 252.0)
            add("Risk", "vol_ann", "Volatility (annualised)", round(sig_ann, 1), "pct", "good" if sig_ann < 25 else "warn" if sig_ann < 50 else "bad", "blend of 21d and 63d realized vol")
            add("Risk", "move_1s", "1σ move over 21 trading days", round(s21, 1), "pct", None, f"≈ ±${spot * s21 / 100:,.2f} per share" if spot else None)
            pnl_pct = pn.get("pct")
            if pnl_pct is not None and spot and (1 + pnl_pct / 100.0):
                cost = spot / (1 + pnl_pct / 100.0)
                lvl = cost * (1 - STOCK_STOP_PCT / 100.0)
                if spot <= lvl:
                    add("Risk", "stop_touch", f"−{STOCK_STOP_PCT:.0f}% discipline stop", "breached", "text", "bad", f"price is already below {lvl:,.2f}")
                else:
                    z = math.log(spot / lvl) / (s21 / 100.0)
                    pt = min(1.0, 2.0 * (1.0 - _norm_cdf(z)))
                    add("Risk", "stop_touch", f"P(touch −{STOCK_STOP_PCT:.0f}% stop in 21d)", round(pt * 100), "pct", "bad" if pt >= 0.35 else "warn" if pt >= 0.2 else "good",
                        f"stop {lvl:,.2f} is {z:.2f}σ below price")
        fh = _num((suite_.get("range") or {}).get("pct_from_52w_high"))
        add("Risk", "drawdown", "Below the 52-week high", None if fh is None else round(abs(fh), 1), "pct", None if fh is None else "bad" if fh <= -20 else "warn" if fh <= -10 else "good",
            "≥ 10-20% off the high = fatter left tail in the backtest")
        beta = _num(((ev.get("fundamental") or {}).get("shares") or {}).get("beta"))
        add("Risk", "beta", "Beta vs market", None if beta is None else round(beta, 2), "num", None if beta is None else "warn" if beta > 1.4 else None, "market sensitivity of the stock")
        add("Edge & odds", "tsmom", "12-1 month momentum", suite_.get("tsmom_12_1_pct"), "pct_signed", None, "academic time-series momentum (weak edge at 1-3 months)")
    # breakevens in σ — a model-free-ish cross-check of the desk's PoP
    bes = sorted(b for b in (pn.get("breakevens") or []) if _num(b))
    sd = (vc.get("sigma_dte_pct") or 0) / 100.0
    pop_sigma = None
    if bes and spot and sd and not stock_kind:
        zs = [(b, math.log(b / spot) / sd) for b in bes]
        for b, z in zs:
            add("Breakevens", f"be_{b}", f"Breakeven {b:,.2f}", round((b / spot - 1) * 100, 1), "pct_signed", None, f"{abs(z):.2f}σ {'above' if b > spot else 'below'} price")
        if len(zs) == 1:
            b, z = zs[0]
            beyond = 1 - _norm_cdf(z) if b > spot else _norm_cdf(z)
            profit_above = profile.get("long_premium") and profile.get("pos_sign", 0) >= 0 or (profile.get("short_premium") and b < spot)
            pop_sigma = (1 - _norm_cdf(z)) if (b > spot and profile.get("long_premium")) else (_norm_cdf(z) if (b < spot and profile.get("long_premium") and profile.get("pos_sign", 0) < 0) else
                         (1 - _norm_cdf(z)) if b < spot else _norm_cdf(z))
        elif len(zs) >= 2:
            lo, hi = zs[0][1], zs[-1][1]
            inside = _norm_cdf(hi) - _norm_cdf(lo)
            pop_sigma = inside if profile.get("short_premium") else 1 - inside
        if pop_sigma is not None:
            ps_ = round(max(0.0, min(1.0, pop_sigma)) * 100)
            diff = (ps_ - pop) if pop is not None else None
            add("Breakevens", "pop_sigma", "σ-model P(profit) cross-check", ps_, "pct",
                None if diff is None else "good" if abs(diff) <= 12 else "warn",
                "lognormal on forecast σ, zero drift" + (f" — {'agrees with' if abs(diff) <= 12 else 'differs from'} the desk's {pop:.0f}%" if diff is not None else ""))
    factors = []
    for a_ in (q.get("adjustments") or []):
        factors.append({"label": a_.get("name"), "pts": a_.get("pts"), "note": a_.get("note")})
    holder = [{"label": f_.get("label"), "favorable": f_.get("favorable"), "note": f_.get("note")} for f_ in (q.get("factors") or [])]
    subs = q.get("subscores") or {}
    return {
        "groups": {k: v for k, v in groups.items() if v},
        "desk": {"signal": q.get("signal"), "score": q.get("score"), "hold_base": q.get("hold_base"),
                 # ENTRY-flavoured 5-lens of the payoff (how attractive it is as a NEW trade) — shown as reference only; NOT used for the hold decision
                 "base_quality": q.get("base_quality"), "entry_reference": {"quality": q.get("base_quality"), "subscores": subs, "note": "entry-style quality of this payoff — reference only, not the hold decision"},
                 "subscores": subs, "adjustments": factors, "holder_factors": holder, "overrides": q.get("overrides") or [],
                 "reasons": q.get("reasons") or [], "hold_vs_close": q.get("hold_vs_close"), "hold_vs_close_reasons": q.get("hold_vs_close_reasons") or []},
        "how_it_counts": _lens_scope(profile)["quant"],
    }



def _position_risk(profile: dict, ev: dict) -> dict:
    """Shares have no option payoff, so the 'quant' lens is POSITION RISK in volatility terms (all validated states):
    how likely the −10% discipline stop is to be touched in a month, volatility regime, drawdown depth, beta."""
    spot = profile["spot"] or ev.get("spot") or 0.0
    suite = (ev.get("technical") or {}).get("suite") or {}
    vc = _vol_context(profile, ev)
    ps = profile["pos_sign"] or 1
    score, notes = 62.0, []
    sig = _num(vc.get("sigma_ann_pct"))
    pct = (profile["pnl"] or {}).get("pct")
    cost = profile.get("avg_cost") or (spot / (1 + pct / 100.0) if (pct is not None and (1 + pct / 100.0)) else None)
    if sig and spot and cost:
        s21 = sig / 100.0 * math.sqrt(21 / 252.0)
        lvl = cost * (1 - STOCK_STOP_PCT / 100.0) if ps > 0 else cost * (1 + STOCK_STOP_PCT / 100.0)
        crossed = (spot <= lvl) if ps > 0 else (spot >= lvl)
        if crossed:
            score -= 30; notes.append(f"price is already through the {STOCK_STOP_PCT:.0f}% discipline stop ({lvl:,.2f}) (−30)")
        else:
            z = abs(math.log(spot / lvl)) / s21
            pt = min(1.0, 2.0 * (1.0 - _norm_cdf(z)))
            pen = 18 if pt >= 0.35 else 9 if pt >= 0.2 else 0
            bonus = 4 if pt <= 0.08 else 0
            score += bonus - pen
            notes.append(f"{pt * 100:.0f}% chance of touching the {STOCK_STOP_PCT:.0f}% stop ({lvl:,.2f}) within a month — it is {z:.1f}σ away" + (f" (−{pen})" if pen else f" (+{bonus})" if bonus else ""))
    ratio = _num(vc.get("ratio_21_63"))
    if ratio is not None:
        if ratio >= 1.4:
            score -= 8; notes.append(f"volatility EXPANDING (21d is {ratio:.2f}× the 63d) — bigger swings ahead (−8)")
        elif ratio <= 0.75:
            score += 2; notes.append(f"volatility compressing ({ratio:.2f}×) (+2)")
    fh = _num((suite.get("range") or {}).get("pct_from_52w_high"))
    if fh is not None:
        if ps > 0 and fh <= -20:
            score -= 8; notes.append(f"{abs(fh):.0f}% below the 52-wk high — left-tail state (P(≤−8% in a month) ≈ 17% vs ~8%) (−8)")
        elif ps > 0 and fh <= -10:
            score -= 4; notes.append(f"{abs(fh):.0f}% below the 52-wk high — fatter left tail (−4)")
        elif ps < 0 and fh >= -3:
            score -= 4; notes.append("short at the 52-wk high — squeeze risk (−4)")
    beta = _num(((ev.get("fundamental") or {}).get("shares") or {}).get("beta"))
    if beta is not None and beta >= 1.6:
        score -= 3; notes.append(f"beta {beta:.1f} — amplifies market drops (−3)")
    if not notes:
        notes.append("no elevated position-risk signal — volatility, drawdown and stop distance are all normal")
    return {"score": round(_clamp(score), 1), "notes": notes}


def _quant_score(profile: dict, ev: Optional[dict] = None) -> dict:
    q = profile.get("_quant") or {}
    ev = ev or {}
    detail = _quant_detail(profile, ev)
    if profile.get("kind") == "stock":
        pr = _position_risk(profile, ev)
        return {"score": pr["score"], "signal": None, "source": "position_risk", "overrides": [], "notes": pr["notes"], "detail": detail, "label": "Position risk"}
    sc = q.get("score")
    if sc is None:
        # no quant-desk read yet → NOT scored: it drops out of the blend rather than contributing a made-up 50
        return {"score": 50.0, "signal": q.get("signal"), "source": "fallback", "overrides": [], "detail": detail, "label": "Quant",
                "notes": ["No quant-desk read for this position yet — open Quant Analysis (or refresh P&L) for the full factor breakdown. Until then this lens is left out of the score."]}
    notes = [f"Quant desk (hold read): {str(q.get('signal') or '').replace('_', ' ')} {sc:.0f}/100"
             + (f" · hold anchor {q['hold_base']:.0f} (neutral 50 + holder factors)" if q.get("hold_base") is not None else "")]
    notes += [f"{a_['label']} {a_['pts']:+d}: {a_['note']}" if isinstance(a_.get("pts"), (int, float)) and a_.get("note") else f"{a_['label']}: {a_.get('note') or ''}"
              for a_ in detail["desk"]["adjustments"][:5] if a_.get("label")]
    notes += [str(r_) for r_ in (q.get("reasons") or [])[:3]]
    return {"score": float(sc), "signal": q.get("signal"), "source": q.get("source") or "quant_desk", "overrides": q.get("overrides") or [],
            "notes": notes, "detail": detail, "label": "Quant"}


def _base_weights(profile: dict) -> dict:
    """Technical's weight was cut (35% → ~26%) after the backtest found ~zero directional edge in the trader rule-sets;
    the Quant desk (strike distance, edge, theta/gamma) and the Event lens (binary gaps) carry the verdict."""
    d = profile.get("dte")
    if profile["kind"] == "stock" or d is None:
        return {"quant": .30, "technical": .26, "fundamental": .28, "event": .16}
    if d <= 7:
        return {"quant": .52, "technical": .25, "fundamental": .03, "event": .20}
    if d <= 30:
        return {"quant": .46, "technical": .26, "fundamental": .08, "event": .20}
    if d <= 90:
        return {"quant": .40, "technical": .26, "fundamental": .15, "event": .19}
    return {"quant": .32, "technical": .26, "fundamental": .25, "event": .17}


def _lens_weights(profile: dict, available: dict) -> dict:
    """Weights over the SCORED lenses only (quant / technical / fundamental), renormalised over those that carry information.
    Event is not a lens — it is a transparent point adjustment applied after the blend."""
    base = _base_weights(profile)
    core = {k: base[k] for k in ("quant", "technical", "fundamental") if available.get(k)}
    tot = sum(core.values()) or 1.0
    return {k: (round(core[k] / tot, 4) if k in core else 0.0) for k in ("quant", "technical", "fundamental")}


def _signal_of(score: float) -> str:
    return ("STRONG_HOLD" if score >= CUT_STRONG_HOLD else "HOLD" if score >= CUT_HOLD
            else "EXIT" if score >= CUT_EXIT else "STRONG_EXIT")



def _close_trigger(profile: dict, vc: dict) -> Optional[dict]:
    """The one backtested ex-ante state where CLOSING an open short option beat holding on average: a TESTED strike
    (P(touch) ≥ 50% or already ITM) while volatility is EXPANDING (21d/63d RV ≥ 1.4). Validated on short puts."""
    if not profile.get("short_premium") or not vc.get("strikes"):
        return None
    tested = any((x.get("p_touch") or 0) >= 0.5 or x.get("breached") for x in vc["strikes"])
    if not (tested and vc.get("regime") == "expanding"):
        return None
    cap = (profile.get("pnl") or {}).get("captured_pct")
    deep = cap is not None and cap <= -200
    st = BACKTEST["hold_state"]["close_trigger"]
    r = st["plus_deep_loss"] if deep else st["stats"]
    return {"deep_loss": deep, "stats": r,
            "text": ("Close trigger: strike tested + volatility expanding" + (" + loss already > 2× the credit" if deep else "")
                     + f" — the one backtested state where closing beat holding (avg Δ {r['mean_delta']:+.2f}% of collateral; closing was better "
                       f"{r['p_close_better']:.0f}% of the time; worst-5% hold outcome {r['worst5']:.0f}%)")}


def _hold_odds(profile: dict, vc: dict) -> Optional[dict]:
    """Backtest odds for the user's CURRENT state (open short premium): how often closing now would have beaten holding."""
    if not profile.get("short_premium"):
        return None
    hs = BACKTEST["hold_state"]
    rows = []

    def pick(table, val):
        for r in table:
            if r["lo"] < val <= r["hi"] or (r["lo"] <= 0 and val <= r["hi"]):
                return r
        return None
    if vc.get("strikes"):
        w = max(vc["strikes"], key=lambda x: x.get("p_touch") or 0)
        r = pick(hs["p_touch"], 1.0 if w.get("breached") else (w.get("p_touch") or 0))
        if r:
            rows.append({"state": f"strike P(touch) ≈ {int(round((w.get('p_touch') or 0) * 100))}%", **r})
    if vc.get("ratio_21_63") is not None:
        r = pick(hs["vol_ratio"], vc["ratio_21_63"])
        if r:
            rows.append({"state": f"vol ratio {vc['ratio_21_63']}×", **r})
    cap = (profile.get("pnl") or {}).get("captured_pct")
    if cap is not None:
        r = pick(hs["pnl_vs_credit"], cap if cap < 50 else 49.9)
        if r:
            rows.append({"state": f"P&L {cap:+.0f}% of max profit", **r})
    return {"rows": rows, "baseline": hs["all"], "takeaway": hs["takeaway"]} if rows else None



EVENT_ADJ_FACTOR, EVENT_ADJ_MAX, EVENT_ADJ_BONUS = 0.4, 12.0, 2.0     # event risk points → verdict points (see decide)


def decide(profile: dict, ev: dict) -> dict:
    """Verdict = weighted blend of the SCORED lenses (quant / technical / fundamental) + a visible event-risk adjustment.

    * A lens that carries no information (no quant-desk read yet; a fund with no company fundamentals) is LEFT OUT and the
      weights renormalise — it no longer contributes a made-up 50.
    * Event & macro risk is not a lens: it is `−0.4 × risk points` (max −12; headlines/peers can add ≤ +2) applied after the
      blend, so "earnings in 5 days = −7" is something you can read, not an opaque 72."""
    spot = profile["spot"] or ev.get("spot") or 0.0
    sig = _tech_signals(ev, spot)
    tech = _technical_score(profile, ev, sig)
    fund = _fundamental_score(profile, ev)
    evt = _event_score(profile, ev)
    qnt = _quant_score(profile, ev)
    suite_ok = bool((ev.get("technical") or {}).get("suite"))
    avail = {"quant": qnt.get("source") != "fallback", "technical": suite_ok, "fundamental": bool(fund.get("available", True))}
    w = _lens_weights(profile, avail)
    lens = {"quant": qnt["score"], "technical": tech["score"], "fundamental": fund["score"]}
    blend = sum(lens[k] * w[k] for k in w)
    event_adj = -min(EVENT_ADJ_MAX, EVENT_ADJ_FACTOR * evt["pts"]) if evt["pts"] > 0 else min(EVENT_ADJ_BONUS, -EVENT_ADJ_FACTOR * evt["pts"])
    overall = blend + event_adj
    overrides: list[str] = []
    capped = overall

    # Discipline overrides — they only ever make the call MORE cautious, and only on POSITION-RISK facts
    # (the backtest found no technical flag that justifies an exit on its own: adverse TA is followed by higher
    # mean returns in a month, only a fatter tail — so TA reduces the score, it doesn't trigger an exit).
    q_sig = qnt.get("signal")
    q_sev = SEV.get({"CLOSE": "EXIT", "STRONG_CLOSE": "STRONG_EXIT"}.get(q_sig, q_sig), None)
    if qnt["overrides"] and q_sev is not None and q_sev >= 2:
        floor_score = {2: 44.0, 3: 27.0}[q_sev]
        if capped > floor_score:
            capped = floor_score
            overrides.append(f"Quant desk hard stop — {'; '.join(qnt['overrides'][:2])}")
    pct = (profile["pnl"] or {}).get("pct")
    if profile["kind"] == "stock" and pct is not None and pct <= -STOCK_STOP_PCT and capped > 40:
        capped = 40.0
        overrides.append(f"Down {abs(pct):.0f}% from cost — past the −{STOCK_STOP_PCT:.0f}% discipline stop (backtest: a ~8-10% stop cut the worst-5% outcome by ~45% and the worst case by ~60% at the best return per unit of tail risk)")
    vc = (tech.get("vol") or {})
    trig = _close_trigger(profile, vc)
    if trig:
        cap_to = 27.0 if trig["deep_loss"] else 40.0
        if capped > cap_to:
            capped = cap_to
        overrides.append(trig["text"])
    signal = _signal_of(capped)
    scored = {k: lens[k] for k in w if w[k] > 0}
    spread = (max(scored.values()) - min(scored.values())) if scored else 0.0
    cov = ev.get("sources_ok") or {}
    coverage = (sum(1 for v in cov.values() if v) / len(cov)) if cov else 0.0
    conviction = _clamp(100 - spread * 0.9, 0, 100) * (0.6 + 0.4 * coverage)
    if not avail["quant"]:
        conviction *= 0.85                       # no quant-desk read for this position → less to anchor on
    if len(scored) <= 1:
        conviction *= 0.8
    conf = "high" if conviction >= 68 and coverage >= 0.7 else "medium" if conviction >= 45 else "low"
    conflicts = []
    if not avail["quant"]:
        conflicts.append("No quant-desk read for this position yet — the score rests on Technical and Fundamental only (confidence reduced). Open Quant Analysis for the full read.")
    if avail["quant"] and abs(lens["quant"] - lens["technical"]) >= 25:
        hi, lo = (qnt["label"], "Technical") if lens["quant"] > lens["technical"] else ("Technical", qnt["label"])
        conflicts.append(f"{hi} ({max(lens['quant'], lens['technical']):.0f}) and {lo} ({min(lens['quant'], lens['technical']):.0f}) disagree by {abs(lens['quant'] - lens['technical']):.0f} pts"
                         + (" — technicals carry little backtested predictive weight, so the position-risk read leads" if lens["quant"] > lens["technical"] else ""))
    lenses = {
        "quant": {**qnt, "weight": w["quant"], "available": avail["quant"]},
        "technical": {**tech, "weight": w["technical"], "available": avail["technical"]},
        "fundamental": {**fund, "weight": w["fundamental"], "available": avail["fundamental"]},
        "event": {**evt, "weight": 0.0, "adjustment": round(event_adj, 1)},
    }
    return {
        "signal": signal, "score": round(capped, 1), "raw_score": round(overall, 1), "blend": round(blend, 1), "event_adj": round(event_adj, 1),
        "confidence": conf, "conviction": round(conviction, 0), "weights": {**w, "event": 0.0}, "overrides": overrides, "conflicts": conflicts,
        "lenses": lenses, "coverage": round(coverage, 2),
    }


# ── 4. exit plan & monitor ───────────────────────────────────────────────────

def _cands(profile: dict, ev: dict) -> list[dict]:
    """Every candidate level in the evidence, tagged with its source (the raw material of the exit plan)."""
    out: list[dict] = []
    spot = profile["spot"] or ev.get("spot") or 0.0
    t = ev.get("technical") or {}
    suite = t.get("suite") or {}
    st = ev.get("structure") or {}

    def add(price, label, kind, w=1.0):
        p = _num(price)
        if p and p > 0:
            out.append({"price": round(p, 2), "label": label, "kind": kind, "w": w})

    for z in (st.get("zones") or []):
        for s in (z.get("sources") or [])[:2]:
            add(s.get("price"), s.get("label") or "confluence", "structure", min(2.5, 0.5 + (s.get("weight") or 0.5)))
    for l in (t.get("lenses") or []):
        add(l.get("exit_level"), f"{l['trader']} exit", "trader", 1.0)
    for k, lab in (("50", "50d SMA"), ("150", "150d SMA"), ("200", "200d SMA")):
        add((suite.get("sma") or {}).get(k), lab, "ma", 1.2 if k != "150" else 0.8)
    for k, lab in (("10", "10 EMA"), ("21", "21 EMA")):
        add((suite.get("ema") or {}).get(k), lab, "ma", 0.8)
    stl = suite.get("supertrend") or {}
    add(stl.get("line"), f"Supertrend ({stl.get('direction')})", "trend", 1.2)
    ch = suite.get("chandelier") or {}
    add(ch.get("long_stop"), "Chandelier long-stop", "trail", 1.0)
    add(ch.get("short_stop"), "Chandelier short-stop", "trail", 1.0)
    ich = suite.get("ichimoku") or {}
    add(ich.get("kijun"), "Ichimoku Kijun", "trend", 0.8)
    add(ich.get("cloud_top"), "Ichimoku cloud top", "trend", 0.9)
    add(ich.get("cloud_bottom"), "Ichimoku cloud bottom", "trend", 0.9)
    rg = suite.get("range") or {}
    add(rg.get("hi_252"), "52-wk high", "range", 1.4)
    add(rg.get("lo_252"), "52-wk low", "range", 1.4)
    add(rg.get("hi_55"), "55-day high", "range", 1.0)
    add(rg.get("lo_55"), "55-day low", "range", 1.0)
    pv = suite.get("pivots") or {}
    for k in ("r1", "r2", "s1", "s2"):
        add(pv.get(k), f"Pivot {k.upper()}", "pivot", 0.5)
    for s in ((suite.get("swings") or {}).get("last_swing_highs") or []):
        add(s.get("price"), "Swing high", "swing", 1.1)
    for s in ((suite.get("swings") or {}).get("last_swing_lows") or []):
        add(s.get("price"), "Swing low", "swing", 1.1)
    dg = (st.get("dossier") or {}).get("dealer_gamma") or {}
    add(((dg.get("gamma_flip") or {}).get("level")), "Gamma flip", "dealer", 1.8)
    gl = dg.get("gamma_levels") if isinstance(dg.get("gamma_levels"), dict) else {}
    for nm, lab in (("call_resistance", "Dealer call resistance"), ("put_support", "Dealer put support"), ("hvl", "Gamma HVL (pin)")):
        w_ = gl.get(nm)
        if isinstance(w_, dict):
            add(w_.get("strike"), lab, "dealer", 1.6)
    em = (dg.get("expected_move") or {}).get("em_30d") or {}
    add(em.get("upper"), "30d expected-move upper", "expected_move", 1.0)
    add(em.get("lower"), "30d expected-move lower", "expected_move", 1.0)
    for p in (st.get("patterns") or []):
        add(p.get("target"), f"{p.get('name')} target", "pattern", 1.0)
        add(p.get("stop"), f"{p.get('name')} stop", "pattern", 0.9)
    for tf_key, tfp in ((st.get("dossier") or {}).get("volume_profile") or {}).items():
        if isinstance(tfp, dict):
            add(tfp.get("poc"), f"{tf_key} POC", "volume_profile", 1.2)
            add(tfp.get("vah"), f"{tf_key} VAH", "volume_profile", 0.9)
            add(tfp.get("val"), f"{tf_key} VAL", "volume_profile", 0.9)
    for n in ((st.get("dossier") or {}).get("naked_pocs") or []):
        if isinstance(n, dict):
            add(n.get("price") or n.get("poc"), "Naked POC", "volume_profile", 1.0)
    return [c for c in out if spot and 0.4 * spot < c["price"] < 2.5 * spot]


def _norm_label(label: str) -> str:
    l = label.lower().replace("dealer ", "").replace("wall", "resistance").strip()
    return "gamma flip" if "gamma flip" in l else l


def _confluence_levels(cands: list[dict], price: float, atr: float) -> list[dict]:
    """The reads that cluster at ``price`` WITH their own prices — 'Dealer put support 1,045' not just 'Dealer put support'.
    The candidate nearest the level always comes first (it IS the level); synonyms from different engines are collapsed."""
    tol = max(0.5 * atr, price * 0.004)
    near = [c for c in cands if abs(c["price"] - price) <= tol]
    if not near:
        return []
    anchor = min(near, key=lambda c: abs(c["price"] - price))
    ordered = [anchor] + sorted((c for c in near if c is not anchor), key=lambda c: -c["w"])
    out, seen = [], set()
    for c in ordered:
        key = (_norm_label(c["label"]), round(c["price"], 0))
        key2 = _norm_label(c["label"])
        if key in seen or key2 in {k[0] for k in seen if abs(k[1] - round(c["price"], 0)) <= max(1.0, 0.003 * price)}:
            continue
        seen.add(key)
        out.append({"label": c["label"], "price": round(c["price"], 2)})
    return out[:5]


def _confluence(cands: list[dict], price: float, atr: float) -> tuple[float, list[str]]:
    tol = max(0.5 * atr, price * 0.004)
    near = [c for c in cands if abs(c["price"] - price) <= tol]
    return sum(c["w"] for c in near), sorted({c["label"] for c in near})[:5]


def _levels(profile: dict, ev: dict) -> dict:
    """Pick the structural invalidation (adverse), profit targets, and range guards for the position.

    Invalidation must sit ≥ INVAL_MIN_ATR away: the exit-rule backtest (22.7k bullish entries, 2016-26) found trails/stops
    inside ~3 ATR whipsaw (≈40% win rate, average win barely above the average loss) while wide exits keep the trend."""
    spot = profile["spot"] or ev.get("spot") or 0.0
    atr = _atr_of(ev, spot)
    cands = _cands(profile, ev)
    res: dict = {"atr": _r(atr), "cands": cands}
    if not spot or not atr:
        return res
    ps = profile["pos_sign"]

    def pick_adverse(below: bool, min_atr: float):
        side = [c for c in cands if (c["price"] < spot - min_atr * atr if below else c["price"] > spot + min_atr * atr)
                and c["kind"] in ("structure", "swing", "trend", "ma", "volume_profile", "dealer", "trail", "range", "pattern")]
        if not side:
            return None
        scored = []
        for c in side:
            cw, labs = _confluence(side, c["price"], atr)
            dist = abs(c["price"] - spot) / atr
            scored.append((cw - 0.35 * max(0.0, dist - 5.0), c, cw, labs))      # prefer strong AND within ~2-5 ATR
        scored.sort(key=lambda x: -x[0])
        _, c, cw, labs = scored[0]
        return {"level": c["price"], "confluence": round(cw, 1), "sources": labs, "source_levels": _confluence_levels(side, c["price"], atr),
                "why": f"{len(labs)} independent reads cluster here ({', '.join(labs[:3])})"}

    if ps != 0:
        res["invalidation"] = pick_adverse(below=(ps > 0), min_atr=INVAL_MIN_ATR)
        fav = sorted([c for c in cands if (c["price"] > spot + 0.8 * atr if ps > 0 else c["price"] < spot - 0.8 * atr)
                      and c["kind"] in ("structure", "swing", "range", "volume_profile", "pattern", "expected_move", "dealer", "pivot")],
                     key=lambda c: abs(c["price"] - spot))
        tg, used = [], []
        for c in fav:
            if any(abs(c["price"] - u) <= 0.8 * atr for u in used):
                continue
            cw, labs = _confluence(fav, c["price"], atr)
            tg.append({"level": c["price"], "confluence": round(cw, 1), "sources": labs, "source_levels": _confluence_levels(fav, c["price"], atr)})
            used.append(c["price"])
            if len(tg) >= 3:
                break
        inv = (res.get("invalidation") or {}).get("level")
        for tgt in tg:
            if inv:
                risk = abs(spot - inv)
                tgt["r_multiple"] = _r(abs(tgt["level"] - spot) / risk, 2) if risk > 0 else None
        res["targets"] = tg
    else:
        res["upper_guard"] = pick_adverse(below=False, min_atr=1.0)
        res["lower_guard"] = pick_adverse(below=True, min_atr=1.0)
    return res



def _status_info(kind: str, status: Optional[str], level: float, spot: float, atr: float) -> tuple[Optional[str], Optional[str]]:
    """(plain-language status text, tone) for a price level. Real dollars, never bare jargon."""
    if status is None or status == "far":
        return None, None
    gap = abs(level - spot)
    near_txt = f"${gap:,.0f} away — about one average day's move (ATR ${atr:,.0f})"
    if kind == "target":
        return ("reached" if status == "hit" else near_txt), ("good" if status == "hit" else "warn")
    if kind == "level":
        return ("price is already past it" if status == "hit" else near_txt), "warn"
    return ("price is already through it" if status == "hit" else near_txt), ("bad" if status == "hit" else "warn")


def _status(level: Optional[float], spot: float, atr: float, adverse_below: Optional[bool]) -> Optional[str]:
    """hit / near (< 1 ATR) / far for a price level. adverse_below=True → exit level under price (hit when price is below it)."""
    if level is None or not spot:
        return None
    if adverse_below is None:
        return None
    crossed = (spot <= level) if adverse_below else (spot >= level)
    if crossed:
        return "hit"
    return "near" if (atr and abs(level - spot) <= atr) else "far"


def _be_meaning(profile: dict, idx: int, n: int) -> tuple[str, bool]:
    """(what lies beyond this breakeven, adverse_below flag for the status chip)."""
    sp = profile["short_premium"]
    ps = profile["pos_sign"]
    if n >= 2:
        below = (idx == 0)
        return (("loss" if sp else "profit") + (" below it" if below else " above it")), below
    if sp:                                  # one breakeven: a credit position loses beyond it, on the side it is exposed to
        below = ps >= 0
        return ("loss " + ("below it" if below else "above it")), below
    below = ps < 0                          # one breakeven: long premium profits beyond it in its direction
    return ("profit " + ("below it" if below else "above it")), below


def build_exit_plan(profile: dict, ev: dict, decision: dict) -> dict:
    """Concrete exit points — every item has a level (or a rule), a SHORT why, an optional detail, the prices of the reads that
    cluster there, a plain status ("$52 away — about one day's move") and, where we have it, the backtest.

    Groups: against (what hurts) · for (take profit) · rules (time / events) · levels (breakevens / your cost, key S/R).
    SHARES get share-language (your cost, a month's range, no expiry / theta / strikes); OPTIONS get option-language.
    Every position gets a plan — an empty plan is never useful."""
    spot = profile["spot"] or ev.get("spot") or 0.0
    atr = _atr_of(ev, spot)
    suite = (ev.get("technical") or {}).get("suite") or {}
    lv = _levels(profile, ev)
    vc = _vol_context(profile, ev)
    ps = profile["pos_sign"]
    is_stock = profile["kind"] == "stock"
    items: list[dict] = []

    def add(kind, level, basis, action, title, why, sources=None, extra=None, adverse_below=None, group="against"):
        d = {"kind": kind, "level": _r(level), "basis": basis, "action": action, "title": title, "why": why, "group": group, "sources": sources or []}
        if level is not None and spot:
            d["distance_pct"] = _r((level - spot) / spot * 100, 2)
            d["distance_usd"] = _r(level - spot, 2)
            d["distance_atr"] = _r(abs(level - spot) / atr, 1) if atr else None
            st = _status(level, spot, atr, adverse_below)
            if st:
                d["status"] = st
                txt, tone = _status_info(kind, st, level, spot, atr)
                if txt:
                    d["status_text"], d["status_tone"] = txt, tone
        if extra:
            d.update({k: v for k, v in extra.items() if v is not None})
        items.append(d)

    sma = suite.get("sma") or {}
    pnl = profile["pnl"]
    mp, ml, cap = pnl.get("max_profit"), pnl.get("max_loss"), pnl.get("captured_pct")
    dte = profile["dte"]
    ev_ = ev.get("events") or {}
    de = ev_.get("days_to_earnings")
    lost_ladder: list[tuple[str, float]] = []

    # ── directional positions (shares, spreads, long options) ────────────────────────────────────────────────
    if ps != 0:
        below = ps > 0                                    # exit levels sit under price for a bullish position
        word = "below" if below else "above"
        inv = lv.get("invalidation")
        if inv:
            hard = inv["level"] - (0.5 * atr if below else -0.5 * atr)
            add("stop", inv["level"], "daily close", "EXIT" if is_stock else "DEFEND_OR_EXIT", "Structural stop",
                f"A daily close {word} it means the {'bullish' if below else 'bearish'} structure has failed.",
                inv["sources"], {"source_levels": inv.get("source_levels"), "hard_stop": _r(hard),
                                 "detail": f"{len(inv['sources'])} independent reads cluster here. Sits ≥ {INVAL_MIN_ATR:g} ATR out so it doesn't whipsaw. Intraday hard stop {_r(hard)} (½ ATR beyond, to avoid wick noise)."}, adverse_below=below)
        if is_stock and pnl.get("pct") is not None:
            cost = profile.get("avg_cost") or (spot / (1 + pnl["pct"] / 100.0) if (1 + pnl["pct"] / 100.0) else None)
            if cost:
                lvl = cost * (1 - STOCK_STOP_PCT / 100.0) if below else cost * (1 + STOCK_STOP_PCT / 100.0)
                add("stop", lvl, "daily close", "EXIT", "Discipline stop",
                    f"Discipline stop {STOCK_STOP_PCT:.0f}% {word} your cost ({cost:,.2f}) — caps the loser.", ["−10% stop"], {"evidence": _evid("fix10")}, adverse_below=below)
        def _lost(lvl: float) -> bool:
            return (spot <= lvl) if below else (spot >= lvl)
        if is_stock:                                      # scale-out ladder (Minervini) — the backtest's best trend-keeping exit
            for k, frac, act in (("50", "⅓", "TRIM"), ("150", "⅓", "TRIM"), ("200", "the rest", "EXIT")):
                lvl = _num(sma.get(k))
                if lvl is None:
                    continue
                if _lost(lvl):                            # already triggered → not a FUTURE exit: it is a level to RECLAIM
                    lost_ladder.append((k, lvl))
                    add("level", lvl, "daily close", "WATCH", f"Reclaim · {k}d",
                        f"Price is already {'below' if below else 'above'} the {k}-day average — the trend is broken here. A daily close back {'above' if below else 'below'} it would repair it.",
                        [f"{k}d SMA"], {"tier": {"50": "first", "150": "second", "200": "final"}[k], "status": "hit",
                                        "status_text": f"price is {'below' if below else 'above'} it — trend broken", "status_tone": "warn"}, group="levels")
                    continue
                add("trail", lvl, "daily close", act, f"Scale out · {k}d" if k != "200" else "Exit the rest · 200d",
                    f"Sell {frac} on a close {word} the {k}-day average.", [f"{k}d SMA"],
                    {"tier": {"50": "first", "150": "second", "200": "final"}[k], "fraction": frac, "evidence": _evid("minervini3"),
                     "detail": "Minervini scale-out. The 50-day alone whipsaws (39% win rate), so it only trims ⅓; volatility above it is noise." if k == "50" else None},
                    adverse_below=below)
        else:                                             # an option position's P&L is driven by the strike; the 200d is only a regime flag
            lvl = _num(sma.get("200"))
            if lvl is not None and _lost(lvl):
                lost_ladder.append(("200", lvl))
                add("level", lvl, "daily close", "WATCH", "Reclaim · 200d",
                    f"Price is already {'below' if below else 'above'} the 200-day average — the long-term trend is broken. A close back {'above' if below else 'below'} it would repair it.",
                    ["200d SMA"], {"tier": "final", "status": "hit", "status_text": f"price is {'below' if below else 'above'} it — trend broken", "status_tone": "warn"}, group="levels")
            elif lvl is not None:
                add("trail", lvl, "daily close", "REVIEW", "Trend line · 200d", f"A close {word} the 200-day average is a regime break for this name — review the position.",
                    ["200d SMA"], {"tier": "final", "fraction": "the rest", "evidence": _evid("sma200")}, adverse_below=below)
        for tg in (lv.get("targets") or []):
            only_em = all("expected-move" in x.lower() for x in tg["sources"]) if tg["sources"] else False
            add("target", tg["level"], "touch / reject", "TAKE_PROFIT", "Profit zone",
                ("Top of this month's normal range — price rarely goes further without news, so it is a natural place to trim." if only_em else
                 "First area where sellers have stepped in before" if ps > 0 else "First area where buyers have stepped in before"),
                tg["sources"], {"r_multiple": tg.get("r_multiple"), "source_levels": tg.get("source_levels"),
                                "detail": f"{tg['r_multiple']}× the risk to the structural stop." if tg.get("r_multiple") else None}, adverse_below=(not below), group="for")

    # ── short-premium: guards either side + strike tests (σ-based) ───────────────────────────────────────────
    if profile["short_premium"] and ps == 0:
        for side, key, k, word in (("upper", "upper_guard", profile["short_call_strike"], "call"), ("lower", "lower_guard", profile["short_put_strike"], "put")):
            g = lv.get(key)
            if g and k:
                add("guard", g["level"], "daily close", "DEFEND_OR_EXIT", f"Range guard {'↑' if side == 'upper' else '↓'}",
                    f"A close through it carries price toward your short {word} ${k:g}.", g["sources"], {"source_levels": g.get("source_levels")}, adverse_below=(side == "lower"))
    for st_ in vc.get("strikes") or []:
        k, up = st_["strike"], st_["side"] == "call"
        if not atr:
            continue
        ptxt = f" · {st_['z_sigma']}σ away · P(touch) ≈ {st_['p_touch'] * 100:.0f}%" if st_.get("p_touch") is not None else ""
        add("strike", k - atr if up else k + atr, "touch", "DEFEND_OR_EXIT", f"Strike test · short {st_['side']} ${k:g}",
            f"Within 1 ATR of the strike{ptxt} — roll/defend or exit before gamma takes over.", ["short strike", "1 ATR buffer"],
            {"p_touch": st_.get("p_touch"), "z_sigma": st_.get("z_sigma")}, adverse_below=(not up))

    # ── position-level management rules (OPTIONS only — shares have no credit/debit, theta or expiry) ─────────
    ec = None
    if profile["long_premium"]:
        ec = abs(pnl["entry_cost"]) if pnl.get("entry_cost") else (abs(ml) if ml and not pnl.get("unbounded_loss") else None)   # debit paid
    if profile["short_premium"] and mp and mp > 0 and not pnl.get("unbounded_profit"):
        tp = round(0.5 * mp, 2)
        add("pnl", None, "P&L", "TAKE_PROFIT", "Bank 50% of max profit",
            f"Bank at +${tp:,.0f} (50% of max ${mp:,.0f})" + (f" — {cap:.0f}% captured so far." if cap is not None else "."), ["50% profit rule"],
            {"pnl_level": tp, "evidence": _evid_sp("tp50"),
             "detail": "tastylive's SPY study: closing at 50% lifted P&L/day ~77% vs 25% and raised the win rate (90% vs 82% at expiry) — the last half of the premium carries most of the gamma risk."}, group="for")
        review = -min(1.5 * mp, 0.6 * abs(ml)) if ml else -1.5 * mp
        add("pnl", None, "P&L", "REVIEW", "Loss review (not a stop)",
            f"Re-underwrite at −${abs(review):,.0f} (≈ 1.5× the credit). Exit only if the strike or structure is under real pressure too.", ["loss review (not a stop)"],
            {"pnl_level": round(review, 2), "evidence": _evid_sp("stop2x"),
             "detail": "Studies (tastylive SPY 2005+, FlashAlpha 2019-26) found fixed credit-multiple stops lower expectancy vs holding to expiry."})
    elif profile["long_premium"] and ec:
        add("pnl", None, "P&L", "EXIT", "Cut at 50% of debit", f"Cut at −${0.5 * ec:,.0f} (50% of the ${ec:,.0f} debit) — long premium bleeds theta; don't let it go to zero.", ["50% debit stop"], {"pnl_level": round(-0.5 * ec, 2)})
        add("pnl", None, "P&L", "TAKE_PROFIT", "Scale out at +100%", f"Scale out at +${ec:,.0f} (100% of the debit); trail the rest.", ["double-up rule"], {"pnl_level": round(ec, 2)}, group="for")
    if not is_stock and dte is not None and profile["kind"] in ("short_premium", "mixed"):
        if dte > 21:
            add("time", None, "date", "REVIEW", "21-DTE decision", f"In {dte - 21}d (21 DTE) gamma starts to dominate theta — decide roll / close then.", ["21-DTE rule"], {"in_days": dte - 21}, group="rules")
        elif dte > 2:
            add("time", None, "date", "EXIT", "Gamma zone", f"{dte} DTE — close or roll rather than carry pin/assignment risk to expiry.", ["gamma zone"], {"in_days": dte}, group="rules")
    if profile["long_premium"] and dte is not None and dte > 0:
        th = _num((profile.get("greeks") or {}).get("theta_per_day"))
        cost = f" — costs ≈${abs(th):,.0f}/day" + (f" ({abs(th) / ec * 100:.1f}% of the debit)" if ec else "") if th else ""
        if dte > 21:
            add("time", None, "date", "REVIEW", "Theta clock", f"In {dte - 21}d (21 DTE) decay accelerates{cost}. If it isn't working by then, sell or roll.", ["theta clock"], {"in_days": dte - 21}, group="rules")
        elif dte > 2:
            add("time", None, "date", "REVIEW", "Theta zone", f"{dte} DTE — time decay is at its steepest{cost}. Don't hold a losing long option into expiry.", ["theta clock"], {"in_days": dte}, group="rules")
    if de is not None and (dte is None or de <= dte):
        edate = ev_.get("earnings_date") or "date TBC"
        if is_stock:
            add("event", None, "date", "REVIEW", "Earnings", f"Earnings in {de}d ({edate}). Results can gap the stock through your levels — decide whether to hold through the print.", ["earnings"], {"in_days": de}, group="rules")
        elif profile["kind"] == "long_premium":
            add("event", None, "date", "REVIEW", "Earnings = your catalyst",
                f"Earnings in {de}d ({edate}). Implied vol collapses after the print — plan to sell the day after (or before, if you only want the run-up).", ["earnings"], {"in_days": de}, group="rules")
        else:
            add("event", None, "date", "DERISK", "Earnings", f"Earnings in {de}d ({edate}) — the gap can jump your levels. Exit or hedge BEFORE the print if you don't want the binary.", ["earnings"], {"in_days": de}, group="rules")
    xd = _days_to(ev_.get("ex_dividend_date")) if ev_.get("ex_dividend_date") else None
    if xd is not None and xd >= 0 and (dte is None or xd <= dte):
        if profile["short_call_strike"]:
            add("event", None, "date", "REVIEW", "Ex-dividend", f"Ex-dividend in {xd}d — a short call that is ITM/near-ITM is assigned early the evening before.", ["ex-div"], {"in_days": xd}, group="rules")
        elif is_stock:
            add("event", None, "date", "WATCH", "Ex-dividend", f"Ex-dividend in {xd}d — the price drops by about the dividend on that date; hold through it to receive the payment.", ["ex-div"], {"in_days": xd}, group="rules")

    # ── breakevens (options) / your cost (shares) ────────────────────────────────────────────────────────────
    sd_pct = vc.get("sigma_dte_pct")
    bes = sorted(b for b in (pnl.get("breakevens") or []) if _num(b))
    if is_stock:
        cost_ = profile.get("avg_cost") or (spot / (1 + pnl["pct"] / 100.0) if pnl.get("pct") is not None and (1 + pnl["pct"] / 100.0) else (bes[0] if bes else None))
        if cost_:
            gain = (spot / cost_ - 1) * 100 * (1 if ps >= 0 else -1)
            add("breakeven", cost_, "—", "WATCH", "Your cost",
                f"You're {abs(gain):.1f}% {'in profit' if gain >= 0 else 'under water'} on this position. A close back through your cost turns a winner into a loser.",
                ["your cost"], {"status": "hit" if gain >= 0 else "far", "status_text": f"{'in profit' if gain >= 0 else 'under water'} {gain:+.1f}%", "status_tone": "good" if gain >= 0 else "bad"}, group="levels")
    else:
        for i_, b in enumerate(bes):
            beyond, adv_below = _be_meaning(profile, i_, len(bes))
            profit_side = beyond.startswith("profit")
            on_beyond_side = (spot < b) if beyond.endswith("below it") else (spot > b)
            state = "you are on the profit side" if (profit_side == on_beyond_side) else "you are on the loss side"
            add("breakeven", b, "at expiry", "WATCH", "Breakeven" if len(bes) == 1 else f"Breakeven {'↓' if i_ == 0 else '↑'}",
                f"{abs((b / spot - 1) * 100):.1f}% {'below' if b < spot else 'above'} price — {beyond} at expiry" + (f" (1σ to expiry is ±{sd_pct}%)." if sd_pct else "."),
                ["breakeven"], {"status": "hit" if profit_side == on_beyond_side else "far", "status_text": state, "status_tone": "good" if profit_side == on_beyond_side else "bad"}, group="levels")

    # ── key support / resistance when nothing else anchors the plan ──────────────────────────────────────────
    if sum(1 for i in items if i["kind"] in ("stop", "trail", "target", "guard", "strike", "breakeven")) < 3 and atr:
        cands = [c for c in lv.get("cands", []) if c["kind"] in ("structure", "swing", "volume_profile", "dealer", "range", "ma", "pattern")]
        for below_ in (True, False):
            side = sorted([c for c in cands if (c["price"] < spot - 0.5 * atr if below_ else c["price"] > spot + 0.5 * atr)], key=lambda c: abs(c["price"] - spot))
            used = []
            for c in side:
                if any(abs(c["price"] - u) < 0.6 * atr for u in used):
                    continue
                cw, labs = _confluence(side, c["price"], atr)
                if cw < 1.6:
                    continue
                used.append(c["price"])
                add("level", c["price"], "close", "WATCH", "Support" if below_ else "Resistance", "A level several independent reads agree on.", labs,
                    {"source_levels": _confluence_levels(side, c["price"], atr)}, adverse_below=below_, group="levels")
                if len(used) >= 2:
                    break

    # ── reward : risk (Paul Tudor Jones asks for 5:1) ───────────────────────────────────────────────────────
    rr = None
    stop_i = next((i for i in items if i["kind"] == "stop" and i["title"] == "Structural stop"), None) or next((i for i in items if i["kind"] == "stop" and i.get("level") is not None), None)
    tgt_i = next((i for i in sorted([x for x in items if x["kind"] == "target"], key=lambda x: abs(x.get("distance_pct") or 0))), None)
    if stop_i and tgt_i and spot:
        risk, reward = abs(spot - stop_i["level"]), abs(tgt_i["level"] - spot)
        if risk > 0:
            rr = {"risk": _r(risk), "reward": _r(reward), "ratio": _r(reward / risk, 2),
                  "note": "FROM HERE (today's price, not your entry): reward to the first profit zone ÷ risk to the structural stop" + (" — below 1.5:1 the skew favours leaving" if reward / risk < 1.5 else "")}
    if not items:
        add("note", None, "—", "WATCH", "No mechanical exit", "No price-based exit applies to this structure and the price history is too thin for levels — manage it by P&L and the days left.", [], group="levels")
    items.sort(key=lambda i: (i.get("level") is None, abs(i.get("distance_pct") or 0)))          # nearest to price first

    # ── headline recommendation: WHERE and WHEN (prose + structured steps) ──────────────────────────────────
    sig = decision["signal"]
    steps: list[dict] = []
    if sig == "STRONG_EXIT":
        rec = {"when": "now", "level": _r(spot), "text": "Exit now — the evidence is against the position. Work a limit near the mid; don't wait for a bounce the evidence doesn't support."}
        steps.append({"tag": "EXIT", "text": "Exit now — work a limit near the mid"})
    elif sig == "EXIT":
        near_tgt = tgt_i if (ps > 0 and tgt_i and tgt_i.get("distance_atr") is not None and tgt_i["distance_atr"] <= 1.5) else None
        if near_tgt:
            rec = {"when": "into strength", "level": near_tgt["level"], "text": f"Exit into strength at ~{near_tgt['level']} ({', '.join(near_tgt['sources'][:2])}); if it fails to get there, exit on a close {'below' if ps > 0 else 'above'} {stop_i['level'] if stop_i else 'the structural stop'}."}
            steps.append({"tag": "EXIT", "text": f"Sell into strength near {near_tgt['level']:,.2f}"})
            if stop_i:
                steps.append({"tag": "EXIT", "text": f"…or on a close {'below' if ps > 0 else 'above'} {stop_i['level']:,.2f}"})
        else:
            rec = {"when": "soon", "level": _r(spot), "text": "Exit / reduce on the next liquid window — the balance of evidence no longer supports the position."}
            steps.append({"tag": "EXIT", "text": "Exit or reduce at the next liquid window"})
    else:
        parts = []
        if is_stock and ps != 0:
            lad = [i for i in items if i["kind"] == "trail" and i.get("fraction") and i.get("status") != "hit"]
            if lad:
                parts.append("scale out " + " · ".join(f"{i['fraction']} {'<' if ps > 0 else '>'} {i['level']}" for i in lad[:3]))
                steps.append({"tag": "TRIM", "text": "Scale out " + " · ".join(f"{i['fraction']} {'below' if ps > 0 else 'above'} {i['level']:,.2f}" for i in lad[:3])})
        disc_i = next((i for i in items if i["kind"] == "stop" and i["title"] == "Discipline stop"), None)
        w_ = "below" if ps > 0 else "above"
        if disc_i and stop_i:          # shares: lead with the nearer, cost-based stop; structure is the backstop
            near_i, far_i = sorted([disc_i, stop_i], key=lambda i: abs(i.get("distance_pct") or 0))
            parts.append(f"exit on a daily close {w_} {near_i['level']} ({near_i['title'].lower()}) — structure fails {w_} {far_i['level']}")
            steps.insert(0, {"tag": "EXIT", "text": f"Exit on a daily close {w_} {near_i['level']:,.2f} ({'your −10% stop' if near_i is disc_i else 'structure fails'}); "
                                                    f"{'structure fails' if near_i is disc_i else 'your −10% stop'} at {far_i['level']:,.2f}"})
        elif stop_i:
            parts.append(f"structural exit on a daily close {'below' if ps > 0 else 'above'} {stop_i['level']}")
            steps.insert(0, {"tag": "EXIT", "text": f"Exit on a daily close {'below' if ps > 0 else 'above'} {stop_i['level']:,.2f}"})
        if tgt_i:
            parts.append(f"take profit into {tgt_i['level']}")
            steps.append({"tag": "PROFIT", "text": f"Take profit into {tgt_i['level']:,.2f}"})
        if ps == 0:
            ups = [i for i in items if i["kind"] == "guard" and (i.get("distance_pct") or 0) > 0]
            dns = [i for i in items if i["kind"] == "guard" and (i.get("distance_pct") or 0) < 0]
            if ups:
                parts.append(f"defend/exit on a close above {ups[0]['level']}")
                steps.append({"tag": "EXIT", "text": f"Defend or exit on a close above {ups[0]['level']:,.2f}"})
            if dns:
                parts.append(f"defend/exit on a close below {dns[0]['level']}")
                steps.append({"tag": "EXIT", "text": f"Defend or exit on a close below {dns[0]['level']:,.2f}"})
            if not profile["short_premium"] and len(bes) >= 2:
                parts.append(f"profit needs a move beyond {bes[0]:,.2f} / {bes[-1]:,.2f}")
                steps.append({"tag": "INFO", "text": f"Profit needs a move beyond {bes[0]:,.2f} / {bes[-1]:,.2f}"})
            ev_i = next((i for i in items if i["kind"] == "event" and i["title"].startswith("Earnings")), None)
            th_i = next((i for i in items if i["kind"] == "time"), None)
            if profile["long_premium"] and ev_i:
                parts.append("sell around the earnings print (vol collapses after)")
                steps.append({"tag": "REVIEW", "text": "Sell around the earnings print — vol collapses after"})
            elif profile["long_premium"] and th_i:
                parts.append(f"decide by 21 DTE (in {th_i.get('in_days')}d)" if th_i.get("title") == "Theta clock" else "don't carry it into expiry")
                steps.append({"tag": "REVIEW", "text": f"Decide by 21 DTE (in {th_i.get('in_days')}d)" if th_i.get("title") == "Theta clock" else "Don't carry it into expiry"})
        if ps != 0 and lost_ladder:
            names = " and ".join(f"{k}-day ({lvl:,.2f})" for k, lvl in sorted(lost_ladder, key=lambda x: int(x[0])))
            top = max(lost_ladder, key=lambda x: int(x[0]))
            steps.append({"tag": "REVIEW", "text": f"Price is already {'below' if ps > 0 else 'above'} the {names} average{'s' if len(lost_ladder) > 1 else ''} — the long-term trend is broken. "
                                                    f"A daily close back {'above' if ps > 0 else 'below'} the {top[0]}-day ({top[1]:,.2f}) would repair it."})
            parts.append("trend already broken — hold only with the stop")
        for it in items:
            if it["kind"] == "pnl" and it["action"] == "TAKE_PROFIT":
                parts.append(f"bank at +${it['pnl_level']:,.0f}")
                steps.append({"tag": "PROFIT", "text": f"Bank at +${it['pnl_level']:,.0f}"}); break
        for it in items:                                   # the next dated catalyst
            if it["kind"] in ("event", "time") and it.get("in_days") is not None and it["action"] != "WATCH":
                steps.append({"tag": "REVIEW", "text": f"{it['title']} in {it['in_days']}d"}); break
        rec = {"when": "conditional", "level": stop_i["level"] if stop_i else None,
               "text": ("Hold — " + "; ".join(parts) + ".") if parts else "Hold — no structural exit has triggered; re-check on the next close."}
        if not steps:
            steps.append({"tag": "HOLD", "text": "No exit has triggered — re-check on the next close"})
    rec["steps"] = steps[:5]
    return {"recommendation": rec, "items": items, "atr": _r(atr), "atr_pct": _r(atr / spot * 100, 1) if spot else None, "risk_reward": rr, "vol": vc,
            "hold_odds": _hold_odds(profile, vc)}


def _level_actions(profile: dict, up: bool, level: float) -> tuple[str, str]:
    """(what a close through the level means, what a rejection means) — worded for THIS position."""
    ps = profile["pos_sign"]
    kc, kp = profile.get("short_call_strike"), profile.get("short_put_strike")
    L = f"{level:,.2f}"
    if profile.get("long_vol"):
        return (f"A close {'above' if up else 'below'} {L} means the move you paid for is under way — consider banking part of it.",
                "If it rejects here the move fades and theta keeps costing you.")
    if up and kc:
        return (f"A close above {L} on strong volume takes price toward your short call ${kc:g} — defend, roll or exit.", "If a rally stalls here your range is intact.")
    if (not up) and kp:
        return (f"A close below {L} on strong volume takes price toward your short put ${kp:g} — defend, roll or exit.", "If it holds on a retest, your short put stays comfortable.")
    if up and kp and not kc:
        return (f"A close above {L} adds cushion to your short put.", "Nothing to do — a pullback from here only returns to the cushion you had.")
    if (not up) and kc and not kp:
        return (f"A close below {L} adds cushion to your short call.", "Nothing to do — a bounce from here only returns to the cushion you had.")
    if ps > 0:
        return ((f"Breakout: a daily close above {L} on strong volume means the trend is extending — raise your targets.", "If it stalls here on a long upper wick, sellers are defending it — consider trimming.") if up else
                (f"A daily close below {L} on strong volume means support has failed — tighten up and watch your next level / stop.", "If it holds on a retest with a rejection wick, buyers are defending it — keep holding."))
    if ps < 0:
        return ((f"A daily close above {L} on strong volume threatens your bearish thesis — cover or tighten.", "If it stalls here on a long upper wick, sellers are defending it — thesis intact.") if up else
                (f"A daily close below {L} on strong volume confirms the downtrend — you can lower targets.", "If it holds on a retest, buyers are defending it — be careful."))
    return (f"A close {'above' if up else 'below'} {L} on strong volume would change the regime for this name.", "If it rejects, the range holds.")


def build_monitor(profile: dict, ev: dict, decision: dict) -> dict:
    """What to watch next — up / down / indicators / fundamentals & events — each with what it would MEAN."""
    spot = profile["spot"] or ev.get("spot") or 0.0
    suite = (ev.get("technical") or {}).get("suite") or {}
    atr = _atr_of(ev, spot)
    ps = profile["pos_sign"]
    lv = _levels(profile, ev)
    lvol = bool(profile.get("long_vol"))              # long straddle/strangle: a big move EITHER way is what you paid for
    sc_, sp_ = bool(profile["short_call_strike"]), bool(profile["short_put_strike"])
    put_only, call_only = (sp_ and not sc_), (sc_ and not sp_)           # one-sided short premium: the OTHER direction helps
    up_good = ps > 0 or lvol or (ps == 0 and put_only)
    up_bad = ps < 0 or sc_ or (profile["range_play"] and not put_only)
    down_good = ps < 0 or lvol or (ps == 0 and call_only)
    down_bad = ps > 0 or sp_ or (profile["range_play"] and not call_only)

    def eff(good, bad):
        return "good" if good and not bad else "bad" if bad and not good else "mixed"

    def eff_note(up: bool):
        g, b = (up_good, up_bad) if up else (down_good, down_bad)
        k = profile.get("short_call_strike") if up else profile.get("short_put_strike")
        if g and not b:
            return "helps your position" + (" — you need a move" if lvol else "")
        if b and not g:
            return ("threatens your short call" if up and k else "threatens your short put" if (not up) and k else
                    "threatens your range" if profile["range_play"] else "goes against your thesis")
        if g and b:
            return f"helps until your short {'call' if up else 'put'} ${k:g} caps it — then it hurts" if k else "cuts both ways for this position"
        return "no clear effect on this position — watch, don't act"

    up: list[dict] = []
    down: list[dict] = []
    res = sorted([c for c in lv.get("cands", []) if c["price"] > spot + 0.3 * atr], key=lambda c: c["price"])
    sup = sorted([c for c in lv.get("cands", []) if c["price"] < spot - 0.3 * atr], key=lambda c: -c["price"])
    seen: list[float] = []
    for c in res:
        if any(abs(c["price"] - s) < 0.6 * atr for s in seen):
            continue
        seen.append(c["price"])
        cw, labs = _confluence(res, c["price"], atr)
        if cw < 1.6:
            continue
        d_atr = abs(c["price"] - spot) / atr if atr else 0.0
        brk, rej = _level_actions(profile, True, c["price"])
        up.append({"level": c["price"], "what": ", ".join(labs[:3]), "distance_atr": round(d_atr, 1), "distance_usd": round(c["price"] - spot, 2),
                   "distance_pct": round((c["price"] / spot - 1) * 100, 2) if spot else None, "noise": d_atr < 1.0,
                   "source_levels": _confluence_levels(res, c["price"], atr), "if_break": brk, "if_reject": rej,
                   "effect_if_break": eff(up_good, up_bad), "effect_note": eff_note(True)})
        if len(up) >= 3:
            break
    seen = []
    for c in sup:
        if any(abs(c["price"] - s) < 0.6 * atr for s in seen):
            continue
        seen.append(c["price"])
        cw, labs = _confluence(sup, c["price"], atr)
        if cw < 1.6:
            continue
        d_atr = abs(c["price"] - spot) / atr if atr else 0.0
        brk, rej = _level_actions(profile, False, c["price"])
        down.append({"level": c["price"], "what": ", ".join(labs[:3]), "distance_atr": round(d_atr, 1), "distance_usd": round(c["price"] - spot, 2),
                     "distance_pct": round((c["price"] / spot - 1) * 100, 2) if spot else None, "noise": d_atr < 1.0,
                     "source_levels": _confluence_levels(sup, c["price"], atr), "if_break": brk, "if_hold": rej,
                     "effect_if_break": eff(down_good, down_bad), "effect_note": eff_note(False)})
        if len(down) >= 3:
            break

    inds: list[dict] = []

    def ind(metric, now, watch):
        if now is not None:
            inds.append({"metric": metric, "now": now, "watch": watch})

    ind("RSI(14)", suite.get("rsi14"), [f"> 70 and falling back under → momentum exhaustion", "< 40 and failing at 50 → downtrend momentum", "bearish divergence flagged: " + str(suite.get("rsi_divergence") or "none")])
    m = suite.get("macd") or {}
    ind("MACD histogram", m.get("hist"), ["zero-line / signal cross against you → momentum turning", f"3-day slope now {m.get('hist_slope_3d')}"])
    adx = suite.get("adx") or {}
    ind("ADX / DI", adx.get("adx"),
        (["ADX > 25 and rising → a real trend (bad for a range structure, good for a directional trade)", "ADX < 18 → chop (good for a range structure)"] if profile["kind"] != "stock"
         else ["ADX > 25 and rising → a real trend: let winners run", "ADX < 18 → chop: trends are unreliable, expect whipsaws"]) + [f"+DI {adx.get('plus_di')} / −DI {adx.get('minus_di')}"])
    vf = suite.get("vol_forecast") or {}
    ind("Volatility regime (21d ÷ 63d realized)", vf.get("ratio_21_63"),
        ["≥ 1.4 = expanding → wider moves ahead (the one technical input the backtest found predictive of forward dispersion)",
         "≤ 0.75 = compressing → calmer tape, helps short premium", f"forecast σ {vf.get('blend_ann_pct')}% annualised"])
    s50, s150 = (suite.get("sma") or {}).get("50"), (suite.get("sma") or {}).get("150")
    if s50 and s150:
        ind("50d vs 150d SMA", f"{s50} vs {s150}", ["50d crossing BELOW the 150d is Minervini's defensive warning (backtest: 12% vs 9% odds of a ≥8% 21d drop)",
                                                     f"currently {'above' if s50 > s150 else 'BELOW'}"])
    fh = (suite.get("range") or {}).get("pct_from_52w_high")
    if fh is not None:
        ind("Distance from 52-week high %", fh, ["≤ −10% / −20% = left-tail state (P(≥8% drop in 21d) 14% / 17% vs ~8% otherwise)", "a reclaim of the 50d is the first sign the damage is healing"])
    ind("12-1 month momentum % (academic TSMOM)", suite.get("tsmom_12_1_pct"), ["negative = the trend-following literature's 'don't be long' signal (weak at a 1-3 month horizon in large caps)"])
    bb = suite.get("bollinger") or {}
    ind("Bollinger bandwidth pct-rank", bb.get("bandwidth_percentile_1y"), ["< 15 → squeeze: a volatility expansion is loading", f"squeeze ON: {suite.get('squeeze_on')}", f"%B {bb.get('pct_b')}"])
    ind("Relative volume", suite.get("rvol"), ["≥ 1.5 on a break = real; < 0.8 on a break = suspect", f"distribution days (25d): {suite.get('distribution_days_25d')} — ≥ 5 = institutional selling"])
    ind("Money flow (CMF20)", suite.get("cmf20"), ["turning negative while price rises = distribution", f"OBV divergence: {suite.get('obv_divergence') or 'none'}"])
    stn = suite.get("supertrend") or {}
    ind("Supertrend(10,3)", stn.get("line"), [f"direction {stn.get('direction')} — a daily close through {stn.get('line')} flips it", f"flipped in the last 5 bars: {stn.get('flipped_recently')}"])
    wk = suite.get("weekly") or {}
    if wk:
        ind("Weekly stage (Weinstein)", wk.get("stage"), ["Stage 3/4 = distribution / decline — the big-picture exit signal", f"30-wk SMA {wk.get('sma30')}"])
    dg = ((ev.get("structure") or {}).get("dossier") or {}).get("dealer_gamma") or {}
    flip = (dg.get("gamma_flip") or {}).get("level")
    if flip:
        if profile["kind"] != "stock":
            ind("Dealer gamma flip", flip, [f"spot {'above' if spot >= flip else 'below'} — losing it flips dealers short gamma (moves amplify)"])
    vix = ((ev.get("market") or {}).get("tape") or {}).get("^VIX") or {}
    if vix:
        ind("VIX", vix.get("last"), ["> 30 or +25% in a week → stressed tape: short premium suffers, hedge", f"5d change {vix.get('change_pct_5d')}%"])

    fund: list[dict] = []
    e = ev.get("events") or {}
    if e.get("days_to_earnings") is not None:
        fund.append({"item": "Earnings", "when": e.get("earnings_date") or f"in {e['days_to_earnings']}d",
                     "watch": ("The print and guidance: a miss on guidance matters more than on EPS. Decide beforehand whether you hold through it." if profile["kind"] == "stock"
                               else "The print and guidance; options price the move — a miss on guide matters more than on EPS.")})
    if e.get("ex_dividend_date"):
        if profile["kind"] == "stock":
            fund.append({"item": "Ex-dividend", "when": e["ex_dividend_date"], "watch": "The price drops by about the dividend on this date; hold through it to receive the payment."})
        elif profile["short_call_strike"]:
            fund.append({"item": "Ex-dividend", "when": e["ex_dividend_date"], "watch": "Early-assignment window for ITM short calls."})
    an = (ev.get("fundamental") or {}).get("analyst") or {}
    if an:
        fund.append({"item": "Analyst actions", "when": "ongoing",
                     "watch": f"{an.get('upgrades_90d', 0)} up / {an.get('downgrades_90d', 0)} down in 90d; mean target {an.get('target_mean')}. A downgrade cluster or target cuts = deteriorating sponsorship."})
    est = (ev.get("fundamental") or {}).get("estimates") or {}
    if est.get("eps_trend"):
        fund.append({"item": "EPS estimate revisions", "when": "ongoing", "watch": "Falling current-/next-year EPS estimates into the print is the classic pre-miss tell."})
    pr = (ev.get("market") or {}).get("peers") or {}
    if pr.get("rows"):
        fund.append({"item": "Peers / competitors", "when": "ongoing",
                     "watch": f"Stock vs peer median: {pr.get('self_vs_peers_21d_pct')}% (21d), {pr.get('self_vs_peers_63d_pct')}% (63d). A peer's guide-down or a competitor win re-rates the group."})
    for d in [d for d in (e.get("filings") or []) if str(d.get("form", "")).startswith(("8-K", "6-K"))][:2]:
        fund.append({"item": f"Filing {d.get('form')} {d.get('date')}", "when": d.get("date"), "watch": "Read it — fresh disclosure from the company itself.", "url": d.get("url")})
    macro = (ev.get("market") or {}).get("fred") or {}
    if macro:
        fund.append({"item": "Macro", "when": "ongoing", "watch": "Rates / curve / credit spreads and the dollar: " + ", ".join(f"{k} {v}" for k, v in list(macro.items())[:6])})
    return {"up": up, "down": down, "indicators": inds, "fundamental_events": fund}


# ── 5. orchestration ─────────────────────────────────────────────────────────

def _lens_view(l: dict) -> dict:
    return {k: l[k] for k in ("key", "trader", "philosophy", "rules", "met", "total", "exit_level", "exit_rule", "stance", "d", "note")}


def _headline(profile: dict, dec: dict) -> str:
    sig = dec["signal"].replace("_", " ")
    lens = dec["lenses"]
    order = sorted(lens.items(), key=lambda kv: kv[1]["score"])
    worst, best = order[0], order[-1]
    return (f"{sig} — {profile['label']}. Strongest: {best[0]} ({best[1]['score']:.0f}); weakest: {worst[0]} ({worst[1]['score']:.0f})."
            + (f" Overrides: {dec['overrides'][0]}." if dec["overrides"] else ""))


class NoMarketData(ValueError):
    """The ticker has no usable price history (new listing, bad symbol, or the data source failed)."""


def _require_data(profile: dict, ev: dict) -> None:
    if not profile.get("spot"):
        profile["spot"] = ev.get("spot")
    if not ev.get("spot") or not ((ev.get("technical") or {}).get("suite")):
        raise NoMarketData(f"No usable price history for {profile.get('ticker')} (need ≥ 60 daily bars) — can't manage it.")


async def run_trade_manager(db, strategy: dict, pnl: dict, desk: Optional[dict] = None) -> dict:
    """The deterministic Trade Manager read (no LLM)."""
    profile = build_profile(strategy, pnl)
    if desk and desk.get("signal") and desk.get("lifecycle_score") is not None:       # the FULL desk score beats the light one
        ma = desk.get("management_analysis") or {}
        profile["_quant"] = {**profile["_quant"], "signal": desk["signal"], "score": desk["lifecycle_score"],
                             "overrides": desk.get("overrides") or [], "reasons": [],
                             "adjustments": [{"name": c.get("label"), "pts": c.get("pts"), "note": c.get("note")} for c in (ma.get("contributions") or [])] or profile["_quant"].get("adjustments") or [],
                             "hold_base": _num(ma.get("anchor")) if ma.get("anchor") is not None else profile["_quant"].get("hold_base"),
                             "source": "full_desk"}
    ev = await gather_market_evidence(db, profile["ticker"], profile["dte"])
    _require_data(profile, ev)
    dec = decide(profile, ev)
    plan = build_exit_plan(profile, ev, dec)
    mon = build_monitor(profile, ev, dec)
    packet = llm_packet(profile, ev)
    return _clean({
        "ticker": profile["ticker"], "as_of": ev.get("as_of"), "cached_evidence": bool(ev.get("_cached")),
        "headline": _headline(profile, dec),
        "profile": {k: v for k, v in profile.items() if not k.startswith("_") and k != "lifecycle"},
        "since_entry": since_entry(profile, ev),
        "decision": dec, "exit_plan": plan, "monitor": mon,
        "trader_lenses": [_lens_view(l) for l in (ev.get("technical") or {}).get("lenses") or []],
        "technical": {"suite": (ev.get("technical") or {}).get("suite"), "consensus": (ev.get("technical") or {}).get("consensus"),
                      "volume": (ev.get("technical") or {}).get("volume"), "structure_context": (ev.get("structure") or {}).get("context"),
                      "zones": (ev.get("structure") or {}).get("zones"), "patterns": (ev.get("structure") or {}).get("patterns"),
                      "regime_edge": ev.get("regime_edge") and {k: ev["regime_edge"].get(k) for k in ("current", "read", "signals") if k in ev["regime_edge"]}},
        "fundamental": {k: v for k, v in (ev.get("fundamental") or {}).items() if k != "pillars"},
        "pillars": {k: {"score": v.get("score"), "data": v.get("data")} for k, v in ((ev.get("fundamental") or {}).get("pillars") or {}).items()},
        "events": ev.get("events"), "market": ev.get("market"), "sources_ok": ev.get("sources_ok"),
        "backtest": BACKTEST,
        "evidence_json": packet,
    })


# ── 6. the LLM packet (facts only) ───────────────────────────────────────────

def llm_packet(profile: dict, ev: dict) -> dict:
    """Every input we collected — NO algorithm decision (no verdict, lens scores, trader stances, pillar
    scores, quant signal). The LLM forms its own view from the raw facts."""
    t = ev.get("technical") or {}
    lenses = [{"trader": l["trader"], "philosophy": l["philosophy"], "rules": l["rules"],
               "rules_met": f"{l['met']}/{l['total']}", "trader_exit_level": l["exit_level"], "trader_exit_rule": l["exit_rule"],
               "note": l.get("note")} for l in (t.get("lenses") or [])]
    f = ev.get("fundamental") or {}
    st = ev.get("structure") or {}
    dossier = json.loads(json.dumps(st.get("dossier") or {}, default=str))
    dossier.pop("bias", None)                                           # the setup engine's own bias call
    (dossier.get("market_structure") or {}).pop("bias", None)           # …and the MTF engine's (the per-TF trends stay)
    zones = [{("confluence_weight" if k == "score" else k): v for k, v in z.items()} for z in (st.get("zones") or [])]
    lv = _levels(profile, ev)
    q = profile.get("_quant") or {}
    qfacts = [{"factor": a.get("name"), "detail": a.get("note")} for a in (q.get("adjustments") or [])][:12]
    pos = {k: v for k, v in profile.items() if not k.startswith("_") and k not in ("lifecycle", "label", "range_play", "long_vol", "pos_sign")}
    pos["lifecycle_metrics"] = profile.get("lifecycle") or {}
    pos["since_entry"] = since_entry(profile, ev)                 # tracking facts: entry date, days held, move since entry, best/worst, rolls, banked P&L
    pos["thesis_notes_by_user"] = profile.get("notes")
    pos["direction_from_delta"] = profile["direction"]
    pkt = {
        "as_of": ev.get("as_of"), "ticker": profile["ticker"],
        "position": pos,
        "quant_facts": {"desk_factor_notes": qfacts,
                        "position_metrics": [{"metric": m_["label"], "value": m_["value"], "unit": m_["fmt"], "note": m_.get("note")}
                                             for g_ in _quant_detail(profile, ev)["groups"].values() for m_ in g_]},
        "technical": {
            "indicator_suite": t.get("suite"), "timeframe_block": t.get("ta_block"), "volume_read": t.get("volume"),
            "famous_trader_rule_checks": lenses,
            "market_structure_volume_profile_regime_dealer_patterns": {"context": st.get("context"), "dossier": dossier,
                                                                       "confluence_zones": zones, "chart_patterns": st.get("patterns")},
            "regime_conditional_edge_backtest": ev.get("regime_edge"),
            "candidate_levels": [{"price": c["price"], "label": c["label"]} for c in sorted(lv.get("cands", []), key=lambda c: c["price"])][:60],
        },
        "fundamental": {
            "profile": {k: f.get(k) for k in ("name", "sector", "industry", "market_cap", "is_fund")},
            "valuation_multiples": f.get("valuation_multiples"), "profitability": f.get("profitability"),
            "balance_sheet": f.get("balance_sheet"), "dividend": f.get("dividend"), "shares": f.get("shares"),
            "pillar_data": {k: v.get("data") for k, v in (f.get("pillars") or {}).items()},
            "analyst": f.get("analyst"), "estimates": f.get("estimates"), "insiders": f.get("insiders"),
        },
        "events_news_macro": {
            "days_to_earnings": (ev.get("events") or {}).get("days_to_earnings"),
            "earnings_date": (ev.get("events") or {}).get("earnings_date"),
            "ex_dividend_date": (ev.get("events") or {}).get("ex_dividend_date"),
            "company_headlines": [{"title": n.get("title"), "publisher": n.get("publisher"), "date": n.get("date"),
                                   "summary": (n.get("summary") or "")[:240]} for n in (ev.get("events") or {}).get("news", [])[:12]],
            "industry_headlines": [{"title": n.get("title"), "publisher": n.get("publisher"), "date": n.get("date")} for n in (ev.get("events") or {}).get("industry_news", [])[:6]],
            "macro_geopolitical_headlines": [{"title": n.get("title"), "publisher": n.get("publisher"), "date": n.get("date")} for n in (ev.get("events") or {}).get("macro_geopolitical_news", [])[:6]],
            "headline_keyword_scan": (ev.get("events") or {}).get("headline_flags"),
            "recent_sec_filings": (ev.get("events") or {}).get("filings"),
            "peers": (ev.get("market") or {}).get("peers"), "peer_headlines": (ev.get("market") or {}).get("peer_news"),
            "market_tape_5d": (ev.get("market") or {}).get("tape"), "fred_macro": (ev.get("market") or {}).get("fred"),
        },
        "coverage": ev.get("sources_ok"),
    }
    return _clean(pkt)


_AI_SYSTEM = (
    "You are the head of risk at a multi-strategy fund reviewing ONE position that is ALREADY OPEN — the user entered it on position.since_entry.entry_date "
    "at the prices given and is TRACKING and MANAGING it. Decide HOLD / trim / EXIT FROM HERE given what they own and paid; never evaluate it as a new "
    "entry, and treat sunk cost as irrelevant except for stops, risk limits and taxes. Use since_entry (days held, move since entry, best/worst, rolls, banked P&L). "
    "A panel of legendary traders is consulted. "
    "You are given a JSON evidence packet: the position, the quant facts, a very deep technical dossier (indicator suite, "
    "multi-timeframe structure, volume profile, regime, dealer gamma, patterns), the mechanical rule-checks of famous traders, "
    "fundamentals, analyst/estimate data, recent headlines/filings, peers and macro. NO algorithmic verdict is included — "
    "form your OWN judgement.\n"
    "RESEARCH PRIORS (from our own 10-year, 99-ticker point-in-time backtest + published studies — weigh the evidence accordingly): "
    "(a) trader/indicator rule-sets show ~NO out-of-sample directional edge at 1-3 months in large caps; adverse technicals precede "
    "fatter left tails and higher volatility, not lower mean returns, and oversold names often bounce within a month; "
    "(b) what IS informative: volatility level and the 21d/63d realized-vol ratio, the strike's distance in σ, event gaps (earnings), "
    "drawdown depth; (c) tight trailing stops (10/21 EMA, ≤3×ATR) whipsaw — wide exits (200d / 5×ATR / ~10% from cost) keep trends and "
    "cut tails; (d) for short premium, closing at ~50% of max profit is supported, but mechanical 1-2× credit stop-losses have tended to "
    "UNDERPERFORM holding to expiry — exit on real structural/strike pressure, not a P&L number alone.\n"
    "HARD RULES: (1) Use ONLY facts in the packet; quote the number. Never invent a price, date, headline or statistic. "
    "(2) If something you'd need is missing, put it in data_gaps as 'NOT IN PACKET: …' — do not guess. "
    "(3) Judge the trade FOR THIS POSITION: a falling stock helps a short call and hurts a short put; a squeeze release hurts a range structure. "
    "(4) Distinguish noise from signal in headlines — rumours stay rumours. (5) Give an actual exit point with a number and the reason; "
    "if you recommend holding, give the level that would make you wrong and the ones that would make you add/raise targets.\n"
    "Return STRICT JSON only:\n"
    '{"verdict":"STRONG_HOLD|HOLD|EXIT|STRONG_EXIT","conviction":0-100,"one_line":"the call in one sentence",'
    '"why":["3-6 bullets, each with a number from the packet"],'
    '"exit_plan":{"primary_exit":{"level":number|null,"basis":"close|touch|time|event","why":"…"},'
    '"stop":{"level":number|null,"why":"…"},"profit_targets":[{"level":number,"why":"…"}],"time_or_event_exit":"…|null"},'
    '"hold_case":"the strongest argument to stay","exit_case":"the strongest argument to leave",'
    '"watch":{"upside":[{"trigger":"…","means":"…"}],"downside":[{"trigger":"…","means":"…"}],'
    '"indicators":[{"metric":"…","trigger":"…","means":"…"}],"fundamental_events":[{"item":"…","why":"…"}]},'
    '"trader_views":[{"trader":"…","would":"hold|add|trim|exit","because":"…using their rule-checks","their_exit":"…"}],'
    '"fundamental_read":{"recent_changes":"…","competitor_industry":"…","macro_geopolitical":"…","analyst_street":"…","what_could_change_direction":["…"]},'
    '"risks_to_this_call":["…"],"data_gaps":["NOT IN PACKET: …"]}'
)


async def run_trade_manager_ai(db, strategy: dict, pnl: dict, api_key: str, model: str = "gpt-4o") -> dict:
    from .llm_service import call_llm, clean_json_text
    profile = build_profile(strategy, pnl)
    ev = await gather_market_evidence(db, profile["ticker"], profile["dte"])
    _require_data(profile, ev)
    pkt = llm_packet(profile, ev)
    text = json.dumps(pkt, separators=(",", ":"), default=str)
    if len(text) > 90000:                             # bound the prompt: drop the longest optional sections first
        for path in (("technical", "candidate_levels"), ("events_news_macro", "peer_headlines"),
                     ("technical", "regime_conditional_edge_backtest"), ("fundamental", "estimates")):
            pkt[path[0]].pop(path[1], None)
            text = json.dumps(pkt, separators=(",", ":"), default=str)
            if len(text) <= 90000:
                break
    messages = [{"role": "system", "content": _AI_SYSTEM},
                {"role": "user", "content": "EVIDENCE PACKET (JSON):\n" + text + "\n\nManage this position. JSON only."}]
    raw = await call_llm(api_key=api_key, model=model, messages=messages, max_tokens=3500, temperature=0.2, expect_json=True)
    try:
        out = json.loads(clean_json_text(raw))
    except Exception:  # noqa: BLE001
        out = {"parse_error": True, "raw": raw}
    v = str(out.get("verdict", "")).upper().replace(" ", "_")
    out["verdict"] = next((s for s in ("STRONG_HOLD", "STRONG_EXIT", "HOLD", "EXIT") if s == v), None) or \
        next((s for s in ("STRONG_HOLD", "STRONG_EXIT", "HOLD", "EXIT") if s in v), None)
    out["_meta"] = {"model": model, "packet_chars": len(text), "as_of": ev.get("as_of")}
    return _clean(out)
