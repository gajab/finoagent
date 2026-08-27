"""
Quantitative Exit Analysis Service
Computes data-driven metrics for all 7 institutional exit pillars,
plus technical signals, options protection, and risk metrics.
No LLM required — purely yfinance + numpy computations.

Scoring Philosophy:
  - Each pillar scores 0–100.
  - 0–20 = very healthy / strong hold signal
  - 20–35 = healthy, low concern
  - 35–50 = moderate concern / mixed signals
  - 50–65 = elevated concern / consider trimming
  - 65–80 = high concern / strong exit signal
  - 80–100 = alarming / urgent exit

Score Calibration:
  - A "typical healthy large-cap" should score ~20–30 on most pillars.
  - A healthy growth stock near 52w high should be ~25–35 overall ("Hold").
  - Only stocks with genuine deterioration should cross 50+ ("Caution").
  - Sector, beta, and valuation context must be sector-relative to avoid
    structural bias against growth stocks in regulated sectors.
"""

import math
import asyncio
import numpy as np
import pandas as pd
from datetime import datetime, timedelta
import yfinance as yf
from .stock_service import safe_float

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

SECTOR_CYCLICALITY = {
    "Technology": "Mixed",
    "Financial Services": "Cyclical",
    "Healthcare": "Defensive",
    "Consumer Cyclical": "Cyclical",
    "Consumer Defensive": "Defensive",
    "Energy": "Cyclical",
    "Utilities": "Defensive",
    "Industrials": "Cyclical",
    "Basic Materials": "Cyclical",
    "Real Estate": "Mixed",
    "Communication Services": "Mixed",
}

SECTOR_REGULATORY_RISK = {
    "Technology": "High",
    "Financial Services": "High",
    "Healthcare": "High",
    "Consumer Cyclical": "Medium",
    "Consumer Defensive": "Low",
    "Energy": "High",
    "Utilities": "Medium",
    "Industrials": "Medium",
    "Basic Materials": "Medium",
    "Real Estate": "Low",
    "Communication Services": "High",
}

REGULATORY_KEYWORDS = [
    "regulation", "regulatory", "antitrust", "lawsuit", "SEC", "FDA",
    "tariff", "ban", "fine", "penalty", "probe", "investigation",
    "sanctions", "compliance", "congress", "legislation", "court",
    "subpoena", "injunction", "settlement",
]

NEGATIVE_KEYWORDS = [
    "decline", "drop", "fall", "loss", "crash", "risk", "warn",
    "cut", "layoff", "miss", "weak", "negative", "concern",
    "down", "bear", "sell", "fraud", "scandal", "failure",
]

SECTOR_ETF_MAP = {
    "Technology": "XLK",
    "Financial Services": "XLF",
    "Healthcare": "XLV",
    "Consumer Cyclical": "XLY",
    "Consumer Defensive": "XLP",
    "Energy": "XLE",
    "Utilities": "XLU",
    "Industrials": "XLI",
    "Basic Materials": "XLB",
    "Real Estate": "XLRE",
    "Communication Services": "XLC",
}

# Sector-relative PE thresholds — median PE for each sector
# Used to normalize valuation scoring so growth sectors aren't over-penalized
SECTOR_PE_MEDIAN = {
    "Technology": 32,
    "Financial Services": 14,
    "Healthcare": 28,
    "Consumer Cyclical": 22,
    "Consumer Defensive": 20,
    "Energy": 12,
    "Utilities": 18,
    "Industrials": 20,
    "Basic Materials": 15,
    "Real Estate": 35,
    "Communication Services": 25,
}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _safe(val, default=None):
    """Convert NaN/Inf/None to ``default``.

    NOTE: ``safe_float`` defaults to 0.0, so it would turn NaN into 0.0 *before*
    we could detect it — silently masking missing data as zero (e.g. an unreported
    EPS reading as a −100% surprise). Pass ``default=None`` so NaN/Inf/parse-errors
    come back as None and are correctly treated as missing here.
    """
    if val is None:
        return default
    f = safe_float(val, default=None)
    if f is None or math.isnan(f) or math.isinf(f):
        return default
    return f


def _direction(values: list[float]) -> str:
    """Determine trend direction from a list of values (oldest first)."""
    if len(values) < 2:
        return "stable"
    # Use average of recent half vs older half for robustness
    mid = len(values) // 2
    if mid == 0:
        mid = 1
    older_avg = sum(values[:mid]) / mid
    recent_avg = sum(values[mid:]) / max(len(values) - mid, 1)
    if older_avg == 0:
        return "stable"
    pct_change = (recent_avg - older_avg) / abs(older_avg)
    if pct_change > 0.05:
        return "improving"
    elif pct_change < -0.05:
        return "declining"
    return "stable"


def _clamp(val: float, lo: float = 0, hi: float = 100) -> int:
    return int(max(lo, min(hi, val)))


def _find_closest_expiration(expirations: list[str], target_days: int):
    today = datetime.now().date()
    target_date = today + timedelta(days=target_days)
    best_exp, min_diff, actual_dte = None, 99999, 0
    for exp_str in expirations:
        try:
            exp_date = datetime.strptime(exp_str, "%Y-%m-%d").date()
            diff = abs((exp_date - target_date).days)
            if diff < min_diff:
                min_diff = diff
                best_exp = exp_str
                actual_dte = (exp_date - today).days
        except Exception:
            pass
    return best_exp, actual_dte


def _find_closest_option(df, target_strike):
    if df.empty:
        return None
    df_copy = df.copy()
    df_copy["diff"] = abs(df_copy["strike"] - target_strike)
    closest = df_copy.sort_values("diff").iloc[0]
    ask = safe_float(closest.get("ask", 0.0))
    bid = safe_float(closest.get("bid", 0.0))
    mid = round((bid + ask) / 2, 2) if bid > 0 and ask > 0 else safe_float(closest.get("lastPrice", 0.0))
    return {
        "strike": safe_float(closest.get("strike", 0)),
        "bid": bid, "ask": ask, "mid": mid,
        "iv": safe_float(closest.get("impliedVolatility", 0)),
        "oi": int(safe_float(closest.get("openInterest", 0))),
        "volume": int(safe_float(closest.get("volume", 0))),
    }


# ---------------------------------------------------------------------------
# Pillar Computations  — each starts at a BASELINE and adjusts up/down
# ---------------------------------------------------------------------------

def _compute_fundamental(stock, info: dict) -> dict:
    """Pillar 1: Fundamental Deterioration.
    Baseline 22 — healthy company stays 15–28; deteriorating pushes 55+."""
    score = 22  # baseline
    data = {}
    chart_data = {"years": [], "revenue": [], "gross_margin": [], "operating_margin": [],
                  "net_margin": [], "fcf": []}

    # Income statement
    try:
        inc = stock.income_stmt
        if inc is not None and not inc.empty:
            cols = sorted(inc.columns)  # oldest first
            years = [c.strftime("%Y") if hasattr(c, "strftime") else str(c) for c in cols]
            chart_data["years"] = years

            revenues = [_safe(inc.at["Total Revenue", c], 0) if "Total Revenue" in inc.index else 0 for c in cols]
            chart_data["revenue"] = revenues

            # Growth rates
            growth_rates = []
            for i in range(1, len(revenues)):
                if revenues[i - 1] and revenues[i - 1] != 0:
                    growth_rates.append(round((revenues[i] - revenues[i - 1]) / abs(revenues[i - 1]) * 100, 1))
                else:
                    growth_rates.append(0)
            data["revenue_growth_rates"] = growth_rates
            data["revenue_direction"] = _direction(revenues)

            # Revenue scoring — declining is bad, strong growth is good
            if data["revenue_direction"] == "declining":
                score += 20
            elif data["revenue_direction"] == "stable":
                score += 5
            else:  # improving
                latest_gr = growth_rates[-1] if growth_rates else 0
                if latest_gr > 15:
                    score -= 10
                elif latest_gr > 5:
                    score -= 5

            # Margins
            for margin_name, num_row, denom_row in [
                ("gross_margin", "Gross Profit", "Total Revenue"),
                ("operating_margin", "Operating Income", "Total Revenue"),
                ("net_margin", "Net Income", "Total Revenue"),
            ]:
                vals = []
                for c in cols:
                    num = _safe(inc.at[num_row, c], 0) if num_row in inc.index else 0
                    den = _safe(inc.at[denom_row, c], 0) if denom_row in inc.index else 0
                    vals.append(round(num / den * 100, 1) if den else 0)
                chart_data[margin_name] = vals
                data[f"{margin_name}_trend"] = vals

            data["margin_direction"] = _direction(chart_data["operating_margin"])
            if data["margin_direction"] == "declining":
                score += 18
            elif data["margin_direction"] == "improving":
                score -= 5

            # EPS
            eps_vals = []
            for c in cols:
                ni = _safe(inc.at["Net Income", c], 0) if "Net Income" in inc.index else 0
                for row_name in ["Basic Average Shares", "Diluted Average Shares"]:
                    if row_name in inc.index:
                        sh = _safe(inc.at[row_name, c], 0)
                        if sh and sh > 0:
                            eps_vals.append({"year": c.strftime("%Y") if hasattr(c, "strftime") else str(c),
                                             "value": round(ni / sh, 2)})
                            break
                else:
                    eps_vals.append({"year": c.strftime("%Y") if hasattr(c, "strftime") else str(c), "value": 0})
            data["eps_trend"] = eps_vals
            profitable = sum(1 for e in eps_vals if e["value"] > 0)
            data["eps_consistency"] = round(profitable / max(len(eps_vals), 1) * 100, 0)

            # EPS consistency scoring
            if data["eps_consistency"] < 50:
                score += 12
            elif data["eps_consistency"] < 75:
                score += 5
    except Exception:
        data["revenue_growth_rates"] = []
        data["revenue_direction"] = "unknown"
        data["margin_direction"] = "unknown"
        data["eps_trend"] = []
        data["eps_consistency"] = 0
        score += 10  # penalty for no data

    # Cash flow
    try:
        cf = stock.cash_flow
        if cf is not None and not cf.empty:
            cols = sorted(cf.columns)
            fcf_vals = []
            for c in cols:
                ocf = _safe(cf.at["Operating Cash Flow", c], 0) if "Operating Cash Flow" in cf.index else 0
                capex_row = "Capital Expenditure" if "Capital Expenditure" in cf.index else "Capital Expenditures"
                capex = abs(_safe(cf.at[capex_row, c], 0)) if capex_row in cf.index else 0
                fcf_vals.append({"year": c.strftime("%Y") if hasattr(c, "strftime") else str(c),
                                 "value": round(ocf - capex, 0)})
            data["fcf_trend"] = fcf_vals
            chart_data["fcf"] = [f["value"] for f in fcf_vals]
            data["fcf_direction"] = _direction([f["value"] for f in fcf_vals])

            if data["fcf_direction"] == "declining":
                score += 15
            elif fcf_vals and fcf_vals[-1]["value"] < 0:
                score += 20  # negative FCF is worse than declining
            elif data["fcf_direction"] == "improving":
                score -= 5
    except Exception:
        data["fcf_trend"] = []
        data["fcf_direction"] = "unknown"

    # Balance sheet & ratios
    de = _safe(info.get("debtToEquity"), None)
    data["debt_to_equity"] = de
    if de is not None:
        if de > 300:
            score += 12
        elif de > 200:
            score += 8
        elif de > 100:
            score += 3
        elif de < 30:
            score -= 3

    cr = _safe(info.get("currentRatio"), None)
    data["current_ratio"] = cr
    if cr is not None:
        if cr < 0.8:
            score += 10
        elif cr < 1.0:
            score += 6
        elif cr < 1.2:
            score += 2
        elif cr > 2.5:
            score -= 3

    # Interest coverage — use actual interest expense from income statement
    ic = None
    try:
        inc_temp = stock.income_stmt
        if inc_temp is not None and not inc_temp.empty:
            latest_col = sorted(inc_temp.columns)[-1]
            ebit_val = None
            for row_name in ["EBIT", "Operating Income"]:
                if row_name in inc_temp.index:
                    ebit_val = _safe(inc_temp.at[row_name, latest_col], None)
                    if ebit_val:
                        break
            ie_val = None
            for row_name in ["Interest Expense", "Interest Expense Non Operating"]:
                if row_name in inc_temp.index:
                    ie_val = abs(_safe(inc_temp.at[row_name, latest_col], 0))
                    if ie_val:
                        break
            if ebit_val and ie_val and ie_val > 0:
                ic = round(ebit_val / ie_val, 1)
    except Exception:
        pass
    # Fallback to estimated interest
    if ic is None:
        ebitda_val = _safe(info.get("ebitda"), 0)
        total_debt = _safe(info.get("totalDebt"), 0)
        est_interest = total_debt * 0.05
        if ebitda_val and est_interest > 0:
            ic = round(ebitda_val / est_interest, 1)
    data["interest_coverage"] = ic
    if ic:
        if ic < 1.5:
            score += 15
        elif ic < 2:
            score += 12
        elif ic < 3:
            score += 8
        elif ic < 5:
            score += 3
        elif ic > 10:
            score -= 3

    # Earnings surprises — try multiple yfinance attributes.
    # earnings_dates is sorted newest-first and INCLUDES upcoming (unreported)
    # dates; those have no Reported EPS (NaN, or a 0.0 placeholder that would read
    # as a spurious −100% surprise). Skip any date that is in the future or has no
    # reported actual, and keep the 4 most recent *reported* quarters.
    data["earnings_surprises"] = []
    try:
        ed = getattr(stock, "earnings_dates", None)
        if ed is not None and not ed.empty:
            for idx, row in ed.iterrows():
                # Skip not-yet-reported (future) earnings dates.
                try:
                    idx_ts = idx if isinstance(idx, pd.Timestamp) else pd.Timestamp(idx)
                    now_cmp = pd.Timestamp.now(tz=idx_ts.tzinfo) if idx_ts.tzinfo is not None else pd.Timestamp.now()
                    if idx_ts > now_cmp:
                        continue
                except Exception:
                    pass
                actual = _safe(row.get("Reported EPS"), None)
                if actual is None:
                    continue  # reported date passed but EPS not populated — skip
                estimate = _safe(row.get("EPS Estimate"), None)
                if estimate is not None and estimate != 0:
                    surprise_pct = round((actual - estimate) / abs(estimate) * 100, 1)
                else:
                    surprise_pct = 0
                quarter_str = idx.strftime("%Y-%m-%d") if hasattr(idx, "strftime") else str(idx)
                data["earnings_surprises"].append({
                    "quarter": quarter_str[:10],
                    "surprise_pct": surprise_pct,
                })
                if len(data["earnings_surprises"]) >= 4:
                    break
        # Fallback to earnings_history
        if not data["earnings_surprises"]:
            eh = getattr(stock, "earnings_history", None)
            if eh is not None and not eh.empty:
                for idx, row in eh.tail(4).iterrows():
                    surprise = _safe(row.get("surprisePercent"), None)
                    if surprise is not None:
                        data["earnings_surprises"].append({
                            "quarter": str(idx)[:10] if hasattr(idx, "strftime") else str(idx),
                            "surprise_pct": round(surprise * 100, 1),
                        })
    except Exception:
        pass

    misses = sum(1 for s in data["earnings_surprises"] if s.get("surprise_pct", 0) < -5)
    mild_misses = sum(1 for s in data["earnings_surprises"] if -5 <= s.get("surprise_pct", 0) < 0)
    if misses >= 2:
        score += 15
    elif misses == 1:
        score += 8
    if mild_misses >= 2:
        score += 5

    return {"score": _clamp(score), "data": data, "chart_data": chart_data}


def _compute_macro(info: dict, hist_6m: pd.DataFrame) -> dict:
    """Pillar 2: Macroeconomic & Policy Shifts.
    Baseline 22 — only truly high-beta cyclical stocks push 55+."""
    score = 22
    beta = _safe(info.get("beta"), None)
    sector = info.get("sector", "")
    cyclicality = SECTOR_CYCLICALITY.get(sector, "Mixed")
    div_yield = _safe(info.get("dividendYield"), 0)

    # Beta — market-range (0.8-1.3) is normal, no penalty
    beta_interp = "Unknown"
    if beta is not None:
        if beta < 0.6:
            beta_interp = "Very Defensive"
            score -= 5
        elif beta < 0.8:
            beta_interp = "Defensive"
            score -= 3
        elif beta < 1.3:
            beta_interp = "Market"
            # no adjustment — this is the normal range
        elif beta < 1.6:
            beta_interp = "Aggressive"
            score += 10
        elif beta < 2.0:
            beta_interp = "High Beta"
            score += 20
        else:
            beta_interp = "Very High Beta"
            score += 28

    # Cyclicality — reduced impact
    if cyclicality == "Cyclical":
        score += 8
    elif cyclicality == "Mixed":
        score += 3
    elif cyclicality == "Defensive":
        score -= 3

    # Historical volatility
    hv30, hv60 = None, None
    if hist_6m is not None and not hist_6m.empty and len(hist_6m) > 30:
        returns = hist_6m["Close"].pct_change().dropna()
        if len(returns) > 21:
            hv30 = round(float(returns.tail(21).std() * np.sqrt(252) * 100), 1)
        if len(returns) > 42:
            hv60 = round(float(returns.tail(42).std() * np.sqrt(252) * 100), 1)

    vol_regime = "Normal"
    if hv30:
        if hv30 > 60:
            vol_regime = "High"
            score += 20
        elif hv30 > 40:
            vol_regime = "Elevated"
            score += 12
        elif hv30 > 25:
            vol_regime = "Above Average"
            score += 5
        elif hv30 < 12:
            vol_regime = "Low"
            score -= 3

    # Dividend context
    div_vs_rates = "No Dividend"
    if div_yield and div_yield > 0:
        if div_yield * 100 > 4.5:
            div_vs_rates = "Above"
            score -= 3
        elif div_yield * 100 > 2.0:
            div_vs_rates = "Moderate"
        else:
            div_vs_rates = "Below"
            score += 3
    else:
        score += 3  # no dividend income = slight concern

    data = {
        "beta": round(beta, 2) if beta else None,
        "beta_interpretation": beta_interp,
        "sector": sector,
        "sector_cyclicality": cyclicality,
        "dividend_yield": round(div_yield * 100, 2) if div_yield else 0,
        "dividend_yield_vs_rates": div_vs_rates,
        "hv30": hv30,
        "hv60": hv60,
        "vol_regime": vol_regime,
    }
    return {"score": _clamp(score), "data": data}


def _compute_structural(stock, info: dict) -> dict:
    """Pillar 3: Structural Obsolescence.
    Baseline 20 — declining R&D or slow growth push toward 55+."""
    score = 20
    data = {}
    chart_data = {"years": [], "rd_spend": [], "capex": [], "revenue_growth": []}

    industry = info.get("industry", "")
    data["industry"] = industry

    try:
        inc = stock.income_stmt
        cf = stock.cash_flow
        if inc is not None and not inc.empty:
            cols = sorted(inc.columns)
            years = [c.strftime("%Y") if hasattr(c, "strftime") else str(c) for c in cols]
            chart_data["years"] = years

            # R&D
            rd_row = None
            for name in ["Research And Development", "ResearchAndDevelopment", "Research Development"]:
                if name in inc.index:
                    rd_row = name
                    break

            revenues = [_safe(inc.at["Total Revenue", c], 0) if "Total Revenue" in inc.index else 0 for c in cols]

            if rd_row:
                rd_vals = [_safe(inc.at[rd_row, c], 0) for c in cols]
                chart_data["rd_spend"] = rd_vals
                data["rd_trend"] = [{"year": y, "value": v} for y, v in zip(years, rd_vals)]
                data["rd_direction"] = _direction(rd_vals)
                latest_rev = revenues[-1] if revenues else 0
                data["rd_as_pct_revenue"] = round(rd_vals[-1] / latest_rev * 100, 1) if latest_rev > 0 and rd_vals else None

                if data["rd_direction"] == "declining":
                    score += 22
                elif data["rd_direction"] == "stable":
                    score += 5
                elif data["rd_direction"] == "improving":
                    score -= 5
            else:
                data["rd_trend"] = None
                data["rd_direction"] = None
                data["rd_as_pct_revenue"] = None
                score += 8  # no R&D data = slight concern

            # Revenue growth for CAGR
            rev_growth = []
            for i in range(1, len(revenues)):
                if revenues[i - 1] and revenues[i - 1] != 0:
                    rev_growth.append(round((revenues[i] - revenues[i - 1]) / abs(revenues[i - 1]) * 100, 1))
                else:
                    rev_growth.append(0)
            chart_data["revenue_growth"] = rev_growth

            # 3Y CAGR
            if len(revenues) >= 2 and revenues[0] > 0:
                n_years = len(revenues) - 1
                cagr = ((revenues[-1] / revenues[0]) ** (1 / n_years) - 1) * 100
                data["revenue_growth_3y_cagr"] = round(cagr, 1)
            else:
                data["revenue_growth_3y_cagr"] = 0

            # CAGR scoring — more granular
            cagr_val = data["revenue_growth_3y_cagr"]
            if cagr_val < -5:
                score += 28
                data["industry_growth_signal"] = "Declining"
            elif cagr_val < 0:
                score += 18
                data["industry_growth_signal"] = "Declining"
            elif cagr_val < 3:
                score += 10
                data["industry_growth_signal"] = "Stagnant"
            elif cagr_val < 8:
                score += 3
                data["industry_growth_signal"] = "Mature"
            elif cagr_val < 15:
                score -= 3
                data["industry_growth_signal"] = "Growing"
            else:
                score -= 8
                data["industry_growth_signal"] = "High Growth"

            # CapEx
            if cf is not None and not cf.empty:
                cf_cols = sorted(cf.columns)
                capex_row = "Capital Expenditure" if "Capital Expenditure" in cf.index else "Capital Expenditures"
                if capex_row in cf.index:
                    capex_vals = [abs(_safe(cf.at[capex_row, c], 0)) for c in cf_cols]
                    chart_data["capex"] = capex_vals
                    data["capex_trend"] = [{"year": c.strftime("%Y") if hasattr(c, "strftime") else str(c), "value": v}
                                           for c, v in zip(cf_cols, capex_vals)]
                    latest_rev = revenues[-1] if revenues else 0
                    data["capex_to_revenue_pct"] = round(capex_vals[-1] / latest_rev * 100, 1) if latest_rev > 0 and capex_vals else 0

                    capex_dir = _direction(capex_vals)
                    if capex_dir == "declining":
                        score += 8  # declining investment
                else:
                    data["capex_trend"] = []
                    data["capex_to_revenue_pct"] = 0

    except Exception:
        data["rd_trend"] = None
        data["rd_direction"] = None
        data["rd_as_pct_revenue"] = None
        data["revenue_growth_3y_cagr"] = 0
        data["industry_growth_signal"] = "Unknown"
        data["capex_trend"] = []
        data["capex_to_revenue_pct"] = 0
        score += 10

    return {"score": _clamp(score), "data": data, "chart_data": chart_data}


def _compute_geopolitical(stock, info: dict) -> dict:
    """Pillar 4: Geopolitical & Regulatory Risks.
    Baseline 20 — only active negative events + regulatory actions push 55+.
    Being in a regulated sector alone shouldn't raise alarms."""
    score = 20
    sector = info.get("sector", "")
    sector_risk = SECTOR_REGULATORY_RISK.get(sector, "Medium")

    # Sector structural risk — reduced: being in tech/healthcare is baseline, not a risk
    if sector_risk == "High":
        score += 8
    elif sector_risk == "Medium":
        score += 4
    elif sector_risk == "Low":
        score -= 3

    # News analysis
    headlines = []
    negative_count = 0
    positive_count = 0
    regulatory_themes = set()
    total_news = 0

    try:
        news_items = stock.news or []
        ticker_name = info.get("shortName", "") or info.get("longName", "") or ""
        ticker_sym = (info.get("symbol", "") or "").upper()
        # Build filter keywords: ticker symbol + company name words (3+ chars)
        filter_words = set()
        if ticker_sym:
            filter_words.add(ticker_sym.lower())
        if ticker_name:
            for word in ticker_name.split():
                w = word.strip(",.()'-").lower()
                if len(w) >= 3 and w not in {"inc", "corp", "ltd", "the", "and", "llc", "plc", "co.", "group"}:
                    filter_words.add(w)

        for item in news_items[:30]:
            # yfinance >=0.2.31 nests news under item["content"]
            content = item.get("content", item) if isinstance(item, dict) else item
            if isinstance(content, dict):
                title = content.get("title", "") or ""
                summary = content.get("summary", "") or ""
                publisher = ""
                link = ""
                pub_date = ""
                provider = content.get("provider", {})
                if isinstance(provider, dict):
                    publisher = provider.get("displayName", "")
                click_url = content.get("clickThroughUrl", {})
                if isinstance(click_url, dict):
                    link = click_url.get("url", "")
                pub_date = content.get("pubDate", "") or ""
            else:
                title = str(content) if content else ""
                summary = ""
                publisher = ""
                link = ""
                pub_date = ""

            # Filter: only include news mentioning the ticker or company name
            combined_text = (title + " " + summary).lower()
            if filter_words and not any(fw in combined_text for fw in filter_words):
                continue  # skip non-ticker-related news

            total_news += 1
            title_lower = title.lower()

            sentiment = "Neutral"
            if any(kw in title_lower for kw in NEGATIVE_KEYWORDS):
                sentiment = "Negative"
                negative_count += 1
            elif any(kw in title_lower for kw in ["rise", "gain", "beat", "strong", "up", "bull", "buy", "upgrade", "growth", "profit", "record"]):
                sentiment = "Positive"
                positive_count += 1

            for kw in REGULATORY_KEYWORDS:
                if kw.lower() in title_lower:
                    regulatory_themes.add(kw.capitalize())

            if len(headlines) < 10:
                headlines.append({
                    "title": title[:160],
                    "sentiment": sentiment,
                    "publisher": publisher[:60],
                    "link": link,
                    "published": pub_date[:25],
                })
    except Exception:
        pass

    if total_news > 0:
        neg_ratio = negative_count / total_news
        pos_ratio = positive_count / total_news
        if neg_ratio > 0.5:
            score += 22
            news_sentiment = "Negative"
        elif neg_ratio > 0.3:
            score += 12
            news_sentiment = "Mixed"
        elif neg_ratio > 0.15:
            score += 5
            news_sentiment = "Slightly Negative"
        elif pos_ratio > 0.5:
            score -= 5
            news_sentiment = "Positive"
        elif negative_count == 0:
            score -= 3
            news_sentiment = "Positive"
        else:
            news_sentiment = "Neutral"
    else:
        news_sentiment = "Neutral"
        score += 5  # no news = slight uncertainty

    if len(regulatory_themes) >= 3:
        score += 18
    elif len(regulatory_themes) >= 2:
        score += 12
    elif len(regulatory_themes) >= 1:
        score += 5

    data = {
        "news_sentiment": news_sentiment,
        "negative_news_count": negative_count,
        "total_news_count": total_news,
        "regulatory_themes_detected": sorted(regulatory_themes),
        "sector_regulatory_risk": sector_risk,
        "recent_headlines": headlines,
    }
    return {"score": _clamp(score), "data": data}


def _compute_valuation(info: dict, current_price: float) -> dict:
    """Pillar 5: Valuation Extremes.
    Baseline 22 — uses sector-relative PE thresholds. Genuinely overvalued stocks push 60+."""
    score = 22
    sector = info.get("sector", "")

    trailing_pe = _safe(info.get("trailingPE"), None)
    forward_pe = _safe(info.get("forwardPE"), None)
    peg = _safe(info.get("pegRatio"), None)
    ev_ebitda = _safe(info.get("enterpriseToEbitda"), None)
    price_to_book = _safe(info.get("priceToBook"), None)
    hi52 = _safe(info.get("fiftyTwoWeekHigh"), None)
    lo52 = _safe(info.get("fiftyTwoWeekLow"), None)

    pe_expansion = None
    if trailing_pe and forward_pe and forward_pe > 0:
        pe_expansion = round((trailing_pe / forward_pe - 1) * 100, 1)

    price_vs_52w = None
    if hi52 and lo52 and hi52 != lo52:
        price_vs_52w = round((current_price - lo52) / (hi52 - lo52) * 100, 1)

    target_mean = _safe(info.get("targetMeanPrice"), None)
    target_low = _safe(info.get("targetLowPrice"), None)
    target_high = _safe(info.get("targetHighPrice"), None)
    num_analysts = int(_safe(info.get("numberOfAnalystOpinions"), 0))
    rec_key = info.get("recommendationKey")
    rec_score = _safe(info.get("recommendationMean"), None)

    target_upside = None
    if target_mean and current_price > 0:
        target_upside = round((target_mean - current_price) / current_price * 100, 1)

    # --- Scoring: PE (sector-relative) ---
    sector_median_pe = SECTOR_PE_MEDIAN.get(sector, 20)
    if trailing_pe and trailing_pe > 0:
        pe_ratio = trailing_pe / sector_median_pe  # how many × sector median
        if pe_ratio > 3.0:
            score += 20  # 3× sector median = extreme
        elif pe_ratio > 2.0:
            score += 12
        elif pe_ratio > 1.5:
            score += 6
        elif pe_ratio > 1.0:
            pass  # at or slightly above sector median = normal
        elif pe_ratio < 0.5:
            score -= 5  # deep value

    # --- PEG ---
    if peg and peg > 0:
        if peg > 3:
            score += 12
        elif peg > 2:
            score += 8
        elif peg > 1.5:
            score += 3
        elif peg < 0.8:
            score -= 5

    # --- EV/EBITDA ---
    if ev_ebitda and ev_ebitda > 0:
        if ev_ebitda > 35:
            score += 10
        elif ev_ebitda > 25:
            score += 5
        elif ev_ebitda < 8:
            score -= 3

    # --- 52-week position (reduced impact — being near highs is not inherently bad) ---
    if price_vs_52w is not None:
        if price_vs_52w > 97:
            score += 10  # at extreme highs, minor concern
        elif price_vs_52w > 90:
            score += 5
        elif price_vs_52w < 15:
            score -= 5  # near 52w low = cheap
        elif price_vs_52w < 30:
            score -= 2

    # --- Analyst targets (strong signal) ---
    if target_upside is not None:
        if target_upside < -20:
            score += 22  # analysts see major downside
        elif target_upside < -10:
            score += 15
        elif target_upside < -5:
            score += 10
        elif target_upside < 0:
            score += 5
        elif target_upside > 30:
            score -= 5

    # --- Recommendation ---
    if rec_score:
        if rec_score > 4.0:  # sell territory
            score += 18
        elif rec_score > 3.5:
            score += 12
        elif rec_score > 3.0:
            score += 5
        elif rec_score < 1.8:
            score -= 5  # strong buy

    data = {
        "trailing_pe": round(trailing_pe, 1) if trailing_pe else None,
        "forward_pe": round(forward_pe, 1) if forward_pe else None,
        "pe_expansion": pe_expansion,
        "peg_ratio": round(peg, 2) if peg else None,
        "ev_to_ebitda": round(ev_ebitda, 1) if ev_ebitda else None,
        "price_to_book": round(price_to_book, 2) if price_to_book else None,
        "fifty_two_week_high": round(hi52, 2) if hi52 else None,
        "fifty_two_week_low": round(lo52, 2) if lo52 else None,
        "price_vs_52w_pct": price_vs_52w,
        "analyst_target_mean": round(target_mean, 2) if target_mean else None,
        "analyst_target_low": round(target_low, 2) if target_low else None,
        "analyst_target_high": round(target_high, 2) if target_high else None,
        "target_upside_pct": target_upside,
        "num_analysts": num_analysts,
        "recommendation": rec_key,
        "recommendation_score": round(rec_score, 1) if rec_score else None,
    }
    return {"score": _clamp(score), "data": data}


def _compute_sentiment(info: dict, current_price: float) -> dict:
    """Pillar 6: Market Sentiment & Momentum.

    Price-based positioning — where the stock trades in its 52-week range, distance
    from the high, trend vs the 50/200-day moving averages, and relative strength vs
    the S&P 500. Short interest & ownership now live in the Ownership & Flow pillar;
    analyst consensus & targets in Catalyst & Revisions — so this pillar no longer
    double-counts them."""
    score = 25
    data: dict = {}

    hi = _safe(info.get("fiftyTwoWeekHigh"))
    lo = _safe(info.get("fiftyTwoWeekLow"))
    ma50 = _safe(info.get("fiftyDayAverage"))
    ma200 = _safe(info.get("twoHundredDayAverage"))
    cp = current_price or _safe(info.get("currentPrice")) or _safe(info.get("regularMarketPrice"))

    # 52-week range position
    range_pos = None
    if hi is not None and lo is not None and hi > lo and cp:
        range_pos = round((cp - lo) / (hi - lo) * 100, 1)
        if range_pos < 20:
            score += 15
        elif range_pos < 40:
            score += 6
        elif range_pos > 80:
            score -= 8
        elif range_pos > 60:
            score -= 3
    data["range_position_pct"] = range_pos

    # Distance from the 52-week high (negative = below the high)
    pct_from_high = None
    if hi and cp and hi > 0:
        pct_from_high = round((cp - hi) / hi * 100, 1)
        if pct_from_high <= -30:
            score += 10
        elif pct_from_high <= -20:
            score += 5
        elif pct_from_high >= -3:
            score -= 5
    data["pct_from_52w_high"] = pct_from_high

    # Trend vs moving averages
    above_50 = (cp > ma50) if (cp and ma50) else None
    above_200 = (cp > ma200) if (cp and ma200) else None
    data["above_50dma"] = above_50
    data["above_200dma"] = above_200
    if above_200 is True:
        score -= 5
    elif above_200 is False:
        score += 12
    if above_50 is True:
        score -= 3
    elif above_50 is False:
        score += 6

    # Relative strength vs the S&P 500 (trailing 1 year)
    one_yr = _safe(info.get("52WeekChange"))
    sp_yr = _safe(info.get("SandP52WeekChange"))
    data["one_year_return_pct"] = round(one_yr * 100, 1) if one_yr is not None else None
    rel = None
    if one_yr is not None and sp_yr is not None:
        rel = round((one_yr - sp_yr) * 100, 1)
        if rel <= -15:
            score += 12
        elif rel < 0:
            score += 5
        elif rel >= 15:
            score -= 8
        elif rel > 0:
            score -= 3
    data["relative_to_sp_pct"] = rel

    if score >= 55:
        sentiment_label = "Weak / Downtrend"
    elif score >= 40:
        sentiment_label = "Soft"
    elif score >= 25:
        sentiment_label = "Neutral"
    elif score >= 15:
        sentiment_label = "Constructive"
    else:
        sentiment_label = "Strong / Uptrend"
    data["sentiment_label"] = sentiment_label

    return {"score": _clamp(score), "data": data}


# ---------------------------------------------------------------------------
# Pillar 7: Sector Rotation
# ---------------------------------------------------------------------------

def _compute_sector_rotation(info: dict, hist_1y: pd.DataFrame) -> dict:
    """Pillar 7: Sector Rotation — are funds flowing into or out of this sector?
    Baseline 22 — neutral rotation. Outflows push 55+, strong inflows pull <15."""
    score = 22
    sector = info.get("sector", "")
    sector_etf_ticker = SECTOR_ETF_MAP.get(sector)
    data = {
        "sector": sector,
        "sector_etf": sector_etf_ticker or "N/A",
        "sector_vs_spy_1m": None,
        "sector_vs_spy_3m": None,
        "sector_vs_spy_6m": None,
        "sector_return_1m": None,
        "sector_return_3m": None,
        "sector_return_6m": None,
        "spy_return_1m": None,
        "spy_return_3m": None,
        "spy_return_6m": None,
        "sector_volume_trend": "Unknown",
        "relative_strength_signal": "Neutral",
        "rotation_signal": "Neutral",
        "sector_rs_series": [],
        "sector_etf_prices": [],
        "spy_prices": [],
        "price_timestamps": [],
    }

    if not sector_etf_ticker:
        data["rotation_signal"] = "Unknown Sector"
        return {"score": _clamp(score), "data": data}

    try:
        # Fetch sector ETF and SPY data
        tickers_str = f"{sector_etf_ticker} SPY"
        raw = yf.download(tickers_str, period="1y", interval="1d", progress=False, auto_adjust=True)

        if raw is None or raw.empty:
            return {"score": _clamp(score), "data": data}

        # Extract close prices
        close = raw["Close"]
        if isinstance(close, pd.Series):
            # Only one ticker returned
            return {"score": _clamp(score), "data": data}

        sector_close = close[sector_etf_ticker].dropna()
        spy_close = close["SPY"].dropna()

        if sector_close.empty or spy_close.empty or len(sector_close) < 30:
            return {"score": _clamp(score), "data": data}

        # Align both series on common dates
        common_idx = sector_close.index.intersection(spy_close.index)
        sector_close = sector_close.loc[common_idx]
        spy_close = spy_close.loc[common_idx]

        # Compute returns for different windows
        def _ret(series, days):
            if len(series) < days:
                return None
            return round(float((series.iloc[-1] / series.iloc[-days] - 1) * 100), 2)

        s_1m = _ret(sector_close, 21)
        s_3m = _ret(sector_close, 63)
        s_6m = _ret(sector_close, 126)
        spy_1m = _ret(spy_close, 21)
        spy_3m = _ret(spy_close, 63)
        spy_6m = _ret(spy_close, 126)

        data["sector_return_1m"] = s_1m
        data["sector_return_3m"] = s_3m
        data["sector_return_6m"] = s_6m
        data["spy_return_1m"] = spy_1m
        data["spy_return_3m"] = spy_3m
        data["spy_return_6m"] = spy_6m

        # Relative performance (sector - SPY)
        vs_1m = round(s_1m - spy_1m, 2) if s_1m is not None and spy_1m is not None else None
        vs_3m = round(s_3m - spy_3m, 2) if s_3m is not None and spy_3m is not None else None
        vs_6m = round(s_6m - spy_6m, 2) if s_6m is not None and spy_6m is not None else None

        data["sector_vs_spy_1m"] = vs_1m
        data["sector_vs_spy_3m"] = vs_3m
        data["sector_vs_spy_6m"] = vs_6m

        # Relative strength series (sector/SPY ratio normalized)
        rs_ratio = (sector_close / spy_close)
        rs_norm = (rs_ratio / rs_ratio.iloc[0] * 100).round(2)
        # Downsample to weekly for chart
        rs_weekly = rs_norm.resample("W").last().dropna()
        data["sector_rs_series"] = [float(v) for v in rs_weekly.values[-52:]]

        # Price series for chart (weekly)
        sector_weekly = sector_close.resample("W").last().dropna()
        spy_weekly = spy_close.resample("W").last().dropna()
        common_weekly = sector_weekly.index.intersection(spy_weekly.index)

        data["sector_etf_prices"] = [round(float(v), 2) for v in sector_weekly.loc[common_weekly].values[-52:]]
        data["spy_prices"] = [round(float(v), 2) for v in spy_weekly.loc[common_weekly].values[-52:]]
        data["price_timestamps"] = [d.strftime("%Y-%m-%d") for d in common_weekly[-52:]]

        # Volume trend (sector ETF)
        vol = raw["Volume"][sector_etf_ticker].dropna() if "Volume" in raw.columns else pd.Series(dtype=float)
        if len(vol) > 40:
            recent_vol = vol.tail(20).mean()
            older_vol = vol.iloc[-40:-20].mean()
            if older_vol > 0:
                vol_change = (recent_vol - older_vol) / older_vol
                if vol_change > 0.20:
                    data["sector_volume_trend"] = "Surging"
                elif vol_change > 0.05:
                    data["sector_volume_trend"] = "Increasing"
                elif vol_change < -0.15:
                    data["sector_volume_trend"] = "Declining"
                elif vol_change < -0.05:
                    data["sector_volume_trend"] = "Decreasing"
                else:
                    data["sector_volume_trend"] = "Stable"

        # Relative strength signal based on RS slope
        if len(rs_norm) > 21:
            rs_recent = float(rs_norm.iloc[-1])
            rs_1m_ago = float(rs_norm.iloc[-21])
            rs_change = rs_recent - rs_1m_ago
            if rs_change > 3:
                data["relative_strength_signal"] = "Strong Inflow"
            elif rs_change > 1:
                data["relative_strength_signal"] = "Moderate Inflow"
            elif rs_change < -3:
                data["relative_strength_signal"] = "Strong Outflow"
            elif rs_change < -1:
                data["relative_strength_signal"] = "Moderate Outflow"
            else:
                data["relative_strength_signal"] = "Neutral"

        # ---- Scoring ----
        # 1. Relative performance scoring (most weight)
        rel_perf_scores = []
        for vs, weight in [(vs_1m, 0.5), (vs_3m, 0.3), (vs_6m, 0.2)]:
            if vs is not None:
                if vs < -8:
                    rel_perf_scores.append((25, weight))
                elif vs < -4:
                    rel_perf_scores.append((18, weight))
                elif vs < -1:
                    rel_perf_scores.append((8, weight))
                elif vs > 8:
                    rel_perf_scores.append((-12, weight))
                elif vs > 4:
                    rel_perf_scores.append((-8, weight))
                elif vs > 1:
                    rel_perf_scores.append((-3, weight))
                else:
                    rel_perf_scores.append((0, weight))

        if rel_perf_scores:
            total_w = sum(w for _, w in rel_perf_scores)
            weighted_adj = sum(s * w for s, w in rel_perf_scores) / total_w if total_w > 0 else 0
            score += weighted_adj

        # 2. Volume trend scoring
        vt = data["sector_volume_trend"]
        if vt == "Declining":
            score += 12
        elif vt == "Decreasing":
            score += 5
        elif vt == "Surging":
            score -= 8
        elif vt == "Increasing":
            score -= 3

        # 3. RS momentum scoring
        rs_sig = data["relative_strength_signal"]
        if rs_sig == "Strong Outflow":
            score += 15
        elif rs_sig == "Moderate Outflow":
            score += 8
        elif rs_sig == "Strong Inflow":
            score -= 10
        elif rs_sig == "Moderate Inflow":
            score -= 5

        # Determine rotation signal
        if score >= 55:
            data["rotation_signal"] = "Funds Leaving Sector"
        elif score >= 40:
            data["rotation_signal"] = "Sector Weakening"
        elif score <= 15:
            data["rotation_signal"] = "Strong Inflows"
        elif score <= 25:
            data["rotation_signal"] = "Sector Strengthening"
        else:
            data["rotation_signal"] = "Neutral"

    except Exception:
        data["rotation_signal"] = "Data Unavailable"

    return {"score": _clamp(score), "data": data}


# ---------------------------------------------------------------------------
# Pillar 8 — Ownership & Flow
# ---------------------------------------------------------------------------

def _compute_ownership_flow(stock, info: dict) -> dict:
    """Pillar 8: Ownership & Flow.

    Short interest (level, days-to-cover, month-over-month change), institutional
    and insider ownership, and net insider activity. Baseline 25 — rising short
    interest and heavy insider selling push exit; falling shorts / insider buying
    pull toward hold. Semantics match the other pillars (higher = more exit pressure)."""
    score = 25
    data: dict = {}

    inst_pct = _safe(info.get("heldPercentInstitutions"))
    insider_pct = _safe(info.get("heldPercentInsiders"))
    short_pct = _safe(info.get("shortPercentOfFloat"))
    short_ratio = _safe(info.get("shortRatio"))  # days-to-cover
    shares_short = _safe(info.get("sharesShort"))
    shares_short_prior = _safe(info.get("sharesShortPriorMonth"))

    data["institution_pct"] = round(inst_pct * 100, 1) if inst_pct is not None else None
    data["insider_pct"] = round(insider_pct * 100, 1) if insider_pct is not None else None
    data["short_pct_float"] = round(short_pct * 100, 1) if short_pct is not None else None
    data["days_to_cover"] = round(short_ratio, 1) if short_ratio is not None else None

    # Short interest level
    if short_pct is not None:
        sp = short_pct * 100
        if sp >= 20:
            score += 20
        elif sp >= 10:
            score += 12
        elif sp >= 5:
            score += 5
        elif sp < 2:
            score -= 5
    # Days to cover
    if short_ratio is not None:
        if short_ratio >= 8:
            score += 8
        elif short_ratio >= 5:
            score += 4

    # Short interest change month-over-month
    short_change = None
    if shares_short is not None and shares_short_prior and shares_short_prior > 0:
        short_change = round((shares_short - shares_short_prior) / shares_short_prior * 100, 1)
        if short_change >= 20:
            score += 10
        elif short_change >= 5:
            score += 5
        elif short_change <= -20:
            score -= 8
        elif short_change <= -5:
            score -= 4
    data["short_change_pct"] = short_change

    # Net insider activity — prefer the info summary, best-effort fall back to the feed.
    insider_net_pct = None
    nspa = info.get("netSharePurchaseActivity")
    if isinstance(nspa, dict):
        insider_net_pct = _safe(nspa.get("netPercentInsiderShares"))
        if insider_net_pct is not None:
            insider_net_pct = round(insider_net_pct * 100, 2)
    insider_signal = "N/A"
    if insider_net_pct is not None:
        if insider_net_pct <= -1.0:
            score += 10; insider_signal = "Net selling"
        elif insider_net_pct < 0:
            score += 4; insider_signal = "Mild selling"
        elif insider_net_pct >= 1.0:
            score -= 8; insider_signal = "Net buying"
        elif insider_net_pct > 0:
            score -= 3; insider_signal = "Mild buying"
        else:
            insider_signal = "Flat"
    data["insider_net_pct"] = insider_net_pct
    data["insider_signal"] = insider_signal

    if inst_pct is not None and inst_pct > 0.95:
        score += 3  # very crowded ownership

    return {"score": _clamp(score), "data": data}


# ---------------------------------------------------------------------------
# Pillar 9 — Catalyst & Analyst Revisions
# ---------------------------------------------------------------------------

def _compute_catalyst_revisions(stock, info: dict) -> dict:
    """Pillar 9: Catalyst & Analyst Revisions.

    Analyst consensus & momentum, price-target upside, earnings growth & proximity,
    and (best-effort) the EPS-estimate revision trend. Cut estimates / downgrades /
    downside targets push exit; raised estimates and upside pull toward hold/entry."""
    score = 25
    data: dict = {}

    rec_mean = _safe(info.get("recommendationMean"))  # 1=Strong Buy .. 5=Sell
    data["recommendation_mean"] = round(rec_mean, 2) if rec_mean is not None else None
    data["recommendation_key"] = info.get("recommendationKey")
    data["num_analysts"] = _safe(info.get("numberOfAnalystOpinions"))
    if rec_mean is not None:
        if rec_mean >= 3.5:
            score += 15
        elif rec_mean >= 3.0:
            score += 8
        elif rec_mean <= 2.0:
            score -= 8
        elif rec_mean <= 2.5:
            score -= 3

    tmp = _safe(info.get("targetMeanPrice"))
    cp = _safe(info.get("currentPrice")) or _safe(info.get("regularMarketPrice"))
    upside = None
    if tmp and cp and cp > 0:
        upside = round((tmp - cp) / cp * 100, 1)
        if upside <= -10:
            score += 15
        elif upside < 0:
            score += 8
        elif upside >= 25:
            score -= 10
        elif upside >= 10:
            score -= 5
    data["target_upside_pct"] = upside

    eqg = _safe(info.get("earningsQuarterlyGrowth"))
    data["earnings_qtr_growth_pct"] = round(eqg * 100, 1) if eqg is not None else None
    if eqg is not None:
        if eqg <= -0.20:
            score += 10
        elif eqg < 0:
            score += 5
        elif eqg >= 0.25:
            score -= 5

    # Next-earnings proximity (event risk, not directional)
    next_days = None
    try:
        ts = info.get("earningsTimestampStart") or info.get("earningsTimestamp")
        if ts:
            dt = datetime.utcfromtimestamp(int(ts))
            next_days = (dt.date() - datetime.utcnow().date()).days
            if next_days is not None and next_days < 0:
                next_days = None
    except Exception:
        next_days = None
    data["days_to_earnings"] = next_days
    if next_days is not None and 0 <= next_days <= 7:
        score += 3  # binary event imminent

    # EPS estimate revisions — best-effort (needs scraping in prod)
    revision_trend = None
    revision_net = None
    try:
        er = getattr(stock, "eps_revisions", None)
        if er is not None and not er.empty:
            def _cell(row_key, col):
                try:
                    if row_key in er.index and col in er.columns:
                        return _safe(er.loc[row_key, col])
                except Exception:
                    return None
                return None
            up = (_cell("0q", "upLast30days") or 0) + (_cell("+1q", "upLast30days") or 0)
            down = (_cell("0q", "downLast30days") or 0) + (_cell("+1q", "downLast30days") or 0)
            revision_net = int(up - down)
            if revision_net <= -2:
                score += 12; revision_trend = "cutting"
            elif revision_net < 0:
                score += 5; revision_trend = "mildly cutting"
            elif revision_net >= 2:
                score -= 10; revision_trend = "raising"
            elif revision_net > 0:
                score -= 4; revision_trend = "mildly raising"
            else:
                revision_trend = "flat"
    except Exception:
        pass
    data["eps_revision_trend"] = revision_trend
    data["eps_revision_net"] = revision_net

    return {"score": _clamp(score), "data": data}


# ---------------------------------------------------------------------------
# Pillar 10 — Quality & Capital Allocation
# ---------------------------------------------------------------------------

def _shares_trend(stock) -> tuple:
    """Share-count trend from the balance sheet: falling = buybacks, rising = dilution.
    Returns (label, annualized_buyback_yield_pct) — positive yield = net buyback."""
    try:
        bs = stock.balance_sheet
        if bs is None or bs.empty:
            return None, None
        row = None
        for key in ["Ordinary Shares Number", "Share Issued", "Common Stock Shares Outstanding"]:
            if key in bs.index:
                row = bs.loc[key]
                break
        if row is None:
            return None, None
        vals = [v for v in (_safe(x) for x in row.tolist()) if v is not None and v > 0]
        if len(vals) < 2:
            return None, None
        latest, oldest = vals[0], vals[-1]  # balance-sheet columns are newest-first
        n = len(vals) - 1
        yld = round((1 - (latest / oldest) ** (1 / n)) * 100, 2)  # +ve = buyback
        chg = (latest - oldest) / oldest
        if chg <= -0.02:
            return "buyback", yld
        if chg >= 0.02:
            return "dilution", yld
        return "stable", yld
    except Exception:
        return None, None


def _compute_quality_capital(stock, info: dict) -> dict:
    """Pillar 10: Quality & Capital Allocation.

    Return on capital (ROE/ROA), free-cash-flow conversion, gross-margin level, and
    share-count trend (buybacks vs dilution). Durable, cash-generative, shareholder-
    friendly businesses score LOW (hold/entry); weak returns, poor cash conversion and
    dilution score high. A 'reward compounders' lens the deterioration pillars miss."""
    score = 25
    data: dict = {}

    roe = _safe(info.get("returnOnEquity"))
    roa = _safe(info.get("returnOnAssets"))
    data["roe"] = round(roe * 100, 1) if roe is not None else None
    data["roa"] = round(roa * 100, 1) if roa is not None else None
    if roe is not None:
        if roe >= 0.20:
            score -= 12
        elif roe >= 0.12:
            score -= 6
        elif roe < 0:
            score += 15
        elif roe < 0.05:
            score += 6

    # ROIC = NOPAT / (Debt + Equity − Cash) — the sharpest quality read. From the
    # audited statements (operating income × (1 − effective tax rate)).
    roic = None
    try:
        inc = stock.income_stmt
        bs = stock.balance_sheet
        if inc is not None and not inc.empty and bs is not None and not bs.empty:
            ic_col, bc_col = inc.columns[0], bs.columns[0]

            def _row(df, col, keys):
                for k in keys:
                    if k in df.index:
                        v = _safe(df.at[k, col])
                        if v is not None:
                            return v
                return None

            op = _row(inc, ic_col, ["Operating Income", "OperatingIncome", "EBIT", "Ebit"])
            pretax = _row(inc, ic_col, ["Pretax Income", "PretaxIncome", "Income Before Tax"])
            tax = _row(inc, ic_col, ["Tax Provision", "TaxProvision", "Income Tax Expense"])
            eq = _row(bs, bc_col, ["Stockholders Equity", "StockholdersEquity", "Total Equity Gross Minority Interest"])
            debt = _row(bs, bc_col, ["Total Debt", "TotalDebt"])
            cash = _row(bs, bc_col, ["Cash And Cash Equivalents", "CashAndCashEquivalents", "Cash Cash Equivalents And Short Term Investments"])
            if op is not None and eq is not None:
                tr = 0.21
                if pretax and tax is not None and pretax != 0:
                    t = tax / pretax
                    if 0.0 <= t <= 0.6:
                        tr = t
                invested = eq + (debt or 0) - (cash or 0)
                if invested and invested > 0:
                    roic = round(op * (1 - tr) / invested * 100, 1)
    except Exception:
        pass
    data["roic"] = roic
    if roic is not None:
        if roic >= 15:
            score -= 10
        elif roic >= 10:
            score -= 5
        elif roic < 0:
            score += 12
        elif roic < 5:
            score += 5

    # FCF conversion = Free Cash Flow / Net Income. Prefer the cash-flow statement —
    # yfinance's info["freeCashflow"] is often a partial/levered figure and unreliable.
    conv = None
    ni_stmt = None
    try:
        cf = stock.cashflow
        inc = stock.income_stmt
        fcf_s = None
        if cf is not None and not cf.empty:
            c = cf.columns[0]
            for k in ["Free Cash Flow", "FreeCashFlow"]:
                if k in cf.index:
                    fcf_s = _safe(cf.loc[k, c]); break
            if fcf_s is None:
                ocf = capex = None
                for k in ["Operating Cash Flow", "OperatingCashFlow", "Cash Flow From Continuing Operating Activities"]:
                    if k in cf.index:
                        ocf = _safe(cf.loc[k, c]); break
                for k in ["Capital Expenditure", "CapitalExpenditure"]:
                    if k in cf.index:
                        capex = _safe(cf.loc[k, c]); break
                if ocf is not None and capex is not None:
                    fcf_s = ocf + capex  # capex is negative
        if inc is not None and not inc.empty:
            c = inc.columns[0]
            for k in ["Net Income", "NetIncome"]:
                if k in inc.index:
                    ni_stmt = _safe(inc.loc[k, c]); break
        if fcf_s is not None and ni_stmt and ni_stmt > 0:
            conv = round(fcf_s / ni_stmt, 2)
    except Exception:
        pass
    if conv is None:  # fall back to info fields
        fcf_i = _safe(info.get("freeCashflow"))
        ni_i = _safe(info.get("netIncomeToCommon"))
        if fcf_i is not None and ni_i and ni_i > 0:
            conv = round(fcf_i / ni_i, 2)
        if ni_stmt is None:
            ni_stmt = ni_i
    if conv is not None:
        if conv >= 1.0:
            score -= 8
        elif conv >= 0.7:
            score -= 3
        elif conv < 0.4:
            score += 10
        elif conv < 0.7:
            score += 4
    elif ni_stmt is not None and ni_stmt < 0:
        score += 10  # unprofitable
    data["fcf_conversion"] = conv

    gm = _safe(info.get("grossMargins"))
    data["gross_margin"] = round(gm * 100, 1) if gm is not None else None
    if gm is not None:
        if gm >= 0.5:
            score -= 5
        elif gm < 0.2:
            score += 5

    shares_trend, buyback_yield = _shares_trend(stock)
    data["shares_trend"] = shares_trend
    data["buyback_yield_pct"] = buyback_yield
    if shares_trend == "dilution":
        score += 8
    elif shares_trend == "buyback":
        score -= 6

    return {"score": _clamp(score), "data": data}


# ---------------------------------------------------------------------------
# Modern Technical — the trade-setup engine (bias / regime / structure / setups)
# ---------------------------------------------------------------------------

def _compute_modern_technical(stock) -> dict:
    """Modern TA via the shared trade-setup engine — the same bias / regime / market
    structure / ranked-setup reads used on the Strategies page — plus derived entry and
    exit timing scores. Degrades to ``{"available": False}`` if the engine can't run."""
    try:
        from .trade_setup_service import compute_trade_setups
        ts = compute_trade_setups(stock)
    except Exception:
        ts = None
    if not ts:
        return {"available": False}

    dossier = ts.get("dossier") or {}
    bias = dossier.get("bias") or {}
    direction = bias.get("direction", "neutral")
    strength = bias.get("strength", "weak")
    bscore = _safe(bias.get("score"), 0) or 0
    regime = bias.get("regime") or dossier.get("regime") or "transitional"
    setups = ts.get("setups") or []

    def _best_rr(want_dir: str):
        best = None
        for s in setups:
            if s.get("direction") != want_dir:
                continue
            rrs = [t.get("rr") for t in (s.get("targets") or []) if t.get("rr") is not None]
            rr = max(rrs) if rrs else s.get("risk_reward")
            if rr is not None and (best is None or rr > best):
                best = rr
        return round(best, 2) if best is not None else None

    best_long_rr = _best_rr("long")
    best_short_rr = _best_rr("short")
    regime_l = str(regime).lower()

    # Entry timing (higher = better entry window)
    entry = 50 + bscore * 8
    if any(k in regime_l for k in ("trend", "up")):
        entry += 8
    if best_long_rr is not None and best_long_rr >= 2:
        entry += 12
    elif best_long_rr is not None and best_long_rr >= 1.5:
        entry += 6
    entry_timing = _clamp(entry)

    # Exit pressure (higher = exit): mirror of the bias, plus a clean short setup
    ex = 50 - bscore * 8
    if best_short_rr is not None and best_short_rr >= 2:
        ex += 10
    if any(k in regime_l for k in ("down", "distribut")):
        ex += 6
    exit_pressure = _clamp(ex)

    return {
        "available": True,
        "bias": {"direction": direction, "strength": strength, "score": round(float(bscore), 2),
                 "rationale": bias.get("rationale")},
        "regime": regime,
        "best_long_rr": best_long_rr,
        "best_short_rr": best_short_rr,
        "entry_timing_score": entry_timing,
        "exit_pressure_score": exit_pressure,
        "setups": setups[:5],
        "confluence_zones": (ts.get("confluence_zones") or [])[:6],
        "price": ts.get("price"),
        "context": ts.get("context"),
    }


# ---------------------------------------------------------------------------
# Entry rating — the buy-side complement to the exit score
# ---------------------------------------------------------------------------

def _entry_label(score: int) -> str:
    if score >= 72:
        return "Strong Buy"
    if score >= 58:
        return "Accumulate"
    if score >= 45:
        return "Watch"
    if score >= 32:
        return "Cautious"
    return "Avoid"


def _compute_entry_rating(pillar_scores_by_name: dict, modern_tech: dict, hold_health: int) -> dict:
    """Entry Attractiveness (0-100, higher = better entry).

    Anchored to the position's overall HOLD health (65%) so the two ratings stay
    coherent — a strong hold can't read as a weak buy, and vice-versa — then tilted
    (35%) by the entry-specific factors that genuinely differ between buying now vs
    already holding: technical entry-timing, valuation cheapness and catalyst momentum.
    Pillar scores arrive as exit-pressure (higher=worse), so they are inverted here."""
    def health(name: str) -> float:
        return 100 - float(pillar_scores_by_name.get(name, 50))

    val_health = health("Valuation")
    cat_health = health("Catalyst & Revisions")
    timing = float(modern_tech.get("entry_timing_score", 50)) if modern_tech.get("available") else 50.0

    entry_specific = 0.40 * timing + 0.35 * val_health + 0.25 * cat_health
    entry_score = _clamp(0.65 * float(hold_health) + 0.35 * entry_specific)

    contribs = [
        ("Hold-health anchor", float(hold_health)),
        ("Technical timing", timing),
        ("Valuation", val_health),
        ("Catalyst momentum", cat_health),
    ]
    supports = sorted([c for c in contribs if c[1] >= 60], key=lambda x: -x[1])[:3]
    headwinds = sorted([c for c in contribs if c[1] <= 42], key=lambda x: x[1])[:3]

    return {
        "score": entry_score,
        "label": _entry_label(entry_score),
        "components": {
            "hold_anchor": round(float(hold_health)),
            "technical_timing": round(timing),
            "valuation_attractiveness": round(val_health),
            "catalyst": round(cat_health),
        },
        "supports": [{"factor": f, "score": round(s)} for f, s in supports],
        "headwinds": [{"factor": f, "score": round(s)} for f, s in headwinds],
    }


# ---------------------------------------------------------------------------
# Technical Signals
# ---------------------------------------------------------------------------

def _compute_technical_signals(stock, info: dict, hist_6m: pd.DataFrame) -> dict:
    """Compute swing trader and long-term investor technical exit signals.
    Scoring: 0–100 per indicator. Composite ~35 for neutral market."""
    swing = {}
    longterm = {}

    try:
        hist_daily = stock.history(period="6mo", interval="1d")
    except Exception:
        hist_daily = hist_6m if hist_6m is not None else pd.DataFrame()

    if hist_daily is None or hist_daily.empty or len(hist_daily) < 30:
        swing = {
            "rsi_score": 30, "rsi_value": 50, "rsi_signal": "Insufficient Data",
            "macd_score": 30, "macd_value": 0, "macd_signal": "Insufficient Data", "macd_histogram": 0,
            "bollinger_score": 30, "bollinger_pct_b": 0.5, "bollinger_position": "Insufficient Data",
            "ema_crossover_score": 30, "ema_signal": "Insufficient Data",
            "volume_divergence_score": 30, "volume_phase": "Insufficient Data",
            "composite_score": 30,
        }
        longterm = {
            "sma_cross_score": 30, "sma_cross_label": "Insufficient Data",
            "sma50": None, "sma200": None,
            "fundamental_health_score": 30, "valuation_score": 30,
            "analyst_consensus_score": 30, "analyst_recommendation": "N/A",
            "composite_score": 30,
        }
        return {"swing": swing, "longterm": longterm}

    close = hist_daily["Close"]
    volume = hist_daily["Volume"] if "Volume" in hist_daily.columns else pd.Series(dtype=float)

    # --- RSI ---
    delta = close.diff()
    gain = delta.where(delta > 0, 0).rolling(14).mean()
    loss = (-delta.where(delta < 0, 0)).rolling(14).mean()
    rs = gain / loss.replace(0, np.nan)
    rsi_series = 100 - (100 / (1 + rs))
    current_rsi = float(rsi_series.iloc[-1]) if not rsi_series.empty else 50

    if current_rsi > 80:
        rsi_score = 90
        rsi_signal = "Extremely Overbought"
    elif current_rsi > 70:
        rsi_score = 75
        rsi_signal = "Overbought"
    elif current_rsi > 60:
        rsi_score = 50
        rsi_signal = "Elevated"
    elif current_rsi > 45:
        rsi_score = 30
        rsi_signal = "Neutral"
    elif current_rsi > 30:
        rsi_score = 20
        rsi_signal = "Approaching Oversold"
    else:
        rsi_score = 10
        rsi_signal = "Oversold"

    swing["rsi_score"] = rsi_score
    swing["rsi_value"] = round(current_rsi, 1)
    swing["rsi_signal"] = rsi_signal

    # --- MACD ---
    ema12 = close.ewm(span=12).mean()
    ema26 = close.ewm(span=26).mean()
    macd_line = ema12 - ema26
    signal_line = macd_line.ewm(span=9).mean()
    histogram = macd_line - signal_line

    macd_val = float(macd_line.iloc[-1])
    hist_val = float(histogram.iloc[-1])
    prev_hist = float(histogram.iloc[-2]) if len(histogram) > 1 else 0

    if hist_val < 0 and prev_hist > 0:
        macd_score = 85
        macd_signal_str = "Bearish Crossover"
    elif hist_val < 0 and abs(hist_val) > abs(prev_hist):
        macd_score = 70
        macd_signal_str = "Accelerating Bearish"
    elif hist_val < 0:
        macd_score = 55
        macd_signal_str = "Bearish"
    elif hist_val > 0 and prev_hist < 0:
        macd_score = 10
        macd_signal_str = "Bullish Crossover"
    elif hist_val > 0 and hist_val < prev_hist:
        macd_score = 40
        macd_signal_str = "Decelerating Bullish"
    else:
        macd_score = 20
        macd_signal_str = "Bullish"

    swing["macd_score"] = macd_score
    swing["macd_value"] = round(macd_val, 2)
    swing["macd_signal"] = macd_signal_str
    swing["macd_histogram"] = round(hist_val, 2)

    # --- Bollinger Bands ---
    sma20 = close.rolling(20).mean()
    std20 = close.rolling(20).std()
    bb_upper = sma20 + 2 * std20
    bb_lower = sma20 - 2 * std20
    bb_width = bb_upper.iloc[-1] - bb_lower.iloc[-1]
    pct_b = (close.iloc[-1] - bb_lower.iloc[-1]) / bb_width if bb_width > 0 else 0.5

    if pct_b > 1.1:
        bb_score = 85
        bb_position = "Well Above Upper Band"
    elif pct_b > 1.0:
        bb_score = 72
        bb_position = "Above Upper Band"
    elif pct_b > 0.8:
        bb_score = 55
        bb_position = "Near Upper Band"
    elif pct_b > 0.5:
        bb_score = 35
        bb_position = "Upper Middle"
    elif pct_b > 0.2:
        bb_score = 25
        bb_position = "Lower Middle"
    elif pct_b > 0:
        bb_score = 15
        bb_position = "Near Lower Band"
    else:
        bb_score = 10
        bb_position = "Below Lower Band"

    swing["bollinger_score"] = bb_score
    swing["bollinger_pct_b"] = round(float(pct_b), 2)
    swing["bollinger_position"] = bb_position

    # --- EMA Crossover ---
    ema12_val = float(ema12.iloc[-1])
    ema26_val = float(ema26.iloc[-1])
    ema_spread = (ema12_val - ema26_val) / ema26_val * 100 if ema26_val > 0 else 0
    if ema12_val < ema26_val and ema_spread < -2:
        ema_score = 75
        ema_signal = "Strong Bearish (EMA12 << EMA26)"
    elif ema12_val < ema26_val:
        ema_score = 60
        ema_signal = "Bearish (EMA12 < EMA26)"
    elif ema_spread < 1:
        ema_score = 45
        ema_signal = "Converging (near crossover)"
    else:
        ema_score = 20
        ema_signal = "Bullish (EMA12 > EMA26)"

    swing["ema_crossover_score"] = ema_score
    swing["ema_signal"] = ema_signal

    # --- Volume Analysis ---
    if not volume.empty and len(volume) > 20:
        avg_vol_20 = float(volume.tail(20).mean())
        recent_vol = float(volume.tail(5).mean())
        price_change_5d = float((close.iloc[-1] - close.iloc[-5]) / close.iloc[-5]) if len(close) > 5 else 0

        if recent_vol > avg_vol_20 * 1.5 and price_change_5d < -0.02:
            vol_score = 90
            vol_phase = "Heavy Distribution"
        elif recent_vol > avg_vol_20 * 1.2 and price_change_5d < 0:
            vol_score = 75
            vol_phase = "Distribution (High Vol + Price Down)"
        elif recent_vol > avg_vol_20 * 1.2 and price_change_5d > 0:
            vol_score = 20
            vol_phase = "Accumulation (High Vol + Price Up)"
        elif recent_vol < avg_vol_20 * 0.7 and price_change_5d > 0:
            vol_score = 60
            vol_phase = "Weak Rally (Low Volume)"
        elif recent_vol < avg_vol_20 * 0.8:
            vol_score = 45
            vol_phase = "Low Volume"
        else:
            vol_score = 35
            vol_phase = "Normal"
    else:
        vol_score = 40
        vol_phase = "Insufficient Data"

    swing["volume_divergence_score"] = vol_score
    swing["volume_phase"] = vol_phase

    swing["composite_score"] = _clamp(
        rsi_score * 0.20 + macd_score * 0.25 + bb_score * 0.15 +
        ema_score * 0.15 + vol_score * 0.25
    )

    # === Long-Term Signals ===
    sma50 = close.rolling(50).mean()
    sma200 = close.rolling(200).mean() if len(close) > 200 else pd.Series(dtype=float)

    sma50_val = float(sma50.iloc[-1]) if not sma50.empty and not np.isnan(sma50.iloc[-1]) else None
    sma200_val = float(sma200.iloc[-1]) if not sma200.empty and len(sma200) > 0 and not np.isnan(sma200.iloc[-1]) else None

    if sma50_val and sma200_val:
        if sma50_val < sma200_val * 0.97:
            sma_score = 85
            sma_label = "Strong Death Cross"
        elif sma50_val < sma200_val:
            sma_score = 70
            sma_label = "Death Cross (SMA50 < SMA200)"
        elif close.iloc[-1] < sma200_val:
            sma_score = 60
            sma_label = "Below SMA200"
        elif close.iloc[-1] < sma50_val:
            sma_score = 45
            sma_label = "Below SMA50"
        elif sma50_val > sma200_val * 1.03:
            sma_score = 15
            sma_label = "Strong Golden Cross"
        else:
            sma_score = 25
            sma_label = "Golden Cross / Above SMAs"
    elif sma50_val:
        if close.iloc[-1] < sma50_val * 0.95:
            sma_score = 60
            sma_label = "Well Below SMA50"
        elif close.iloc[-1] < sma50_val:
            sma_score = 45
            sma_label = "Below SMA50"
        else:
            sma_score = 25
            sma_label = "Above SMA50"
    else:
        sma_score = 35
        sma_label = "Insufficient Data"

    longterm["sma_cross_score"] = sma_score
    longterm["sma_cross_label"] = sma_label
    longterm["sma50"] = round(sma50_val, 2) if sma50_val else None
    longterm["sma200"] = round(sma200_val, 2) if sma200_val else None

    # Analyst consensus
    rec_mean = _safe(info.get("recommendationMean"), None)
    if rec_mean:
        # 1=strong buy → score 10, 3=hold → score 50, 5=strong sell → score 90
        analyst_score = _clamp(10 + (rec_mean - 1) * 20)
    else:
        analyst_score = 45

    longterm["analyst_consensus_score"] = analyst_score
    longterm["analyst_recommendation"] = info.get("recommendationKey", "N/A")

    # Composite filled by caller (needs pillar scores)
    longterm["composite_score"] = _clamp(sma_score * 0.35 + analyst_score * 0.25 + 40 * 0.40)

    return {"swing": swing, "longterm": longterm}


# ---------------------------------------------------------------------------
# Options Protection
# ---------------------------------------------------------------------------

def _compute_options_protection(stock, current_price: float, shares: float = 100) -> dict:
    """Compute protective put, zero-cost collar, and covered call pricing."""
    result = {"available": False}

    try:
        expirations = list(stock.options)
    except Exception:
        return result
    if not expirations:
        return result

    exp_str, dte = _find_closest_expiration(expirations, 45)
    if not exp_str or dte < 7:
        return result

    try:
        chain = stock.option_chain(exp_str)
    except Exception:
        return result

    calls = chain.calls
    puts = chain.puts
    if calls.empty or puts.empty:
        return result

    contracts = math.ceil(shares / 100)
    result["available"] = True
    result["current_price"] = round(current_price, 2)
    result["contracts_needed"] = contracts

    # Protective Put: ~5% OTM
    put_target = current_price * 0.95
    put_opt = _find_closest_option(puts, put_target)
    if put_opt:
        total_cost = round(put_opt["mid"] * 100 * contracts, 2)
        ann_cost_pct = round(put_opt["mid"] / current_price * (365 / max(dte, 1)) * 100, 1)
        result["protective_put"] = {
            "strike": put_opt["strike"], "expiration": exp_str, "dte": dte,
            "mid_price": put_opt["mid"], "total_cost": total_cost,
            "protection_floor": put_opt["strike"],
            "max_loss_per_share": round(current_price - put_opt["strike"] + put_opt["mid"], 2),
            "annualized_cost_pct": ann_cost_pct,
        }

    # Zero-Cost Collar
    collar_put_target = current_price * 0.92
    collar_call_target = current_price * 1.08
    collar_put = _find_closest_option(puts, collar_put_target)
    collar_call = _find_closest_option(calls, collar_call_target)
    if collar_put and collar_call:
        net = round((collar_call["mid"] - collar_put["mid"]) * 100 * contracts, 2)
        result["zero_cost_collar"] = {
            "put_strike": collar_put["strike"], "call_strike": collar_call["strike"],
            "expiration": exp_str, "dte": dte,
            "put_mid": collar_put["mid"], "call_mid": collar_call["mid"],
            "net_credit_debit": net,
            "protection_floor": collar_put["strike"],
            "upside_cap": collar_call["strike"],
        }

    # Covered Call
    cc_target = current_price * 1.05
    cc_opt = _find_closest_option(calls, cc_target)
    if cc_opt:
        total_premium = round(cc_opt["mid"] * 100 * contracts, 2)
        ann_return = round(cc_opt["mid"] / current_price * (365 / max(dte, 1)) * 100, 1)
        result["covered_call"] = {
            "strike": cc_opt["strike"], "expiration": exp_str, "dte": dte,
            "mid_price": cc_opt["mid"], "total_premium": total_premium,
            "annualized_return_pct": ann_return,
            "upside_cap_pct": round((cc_opt["strike"] / current_price - 1) * 100, 1),
        }

    return result


# ---------------------------------------------------------------------------
# Risk Metrics
# ---------------------------------------------------------------------------

def _compute_risk(info: dict, hist_1y: pd.DataFrame, current_price: float, shares: float = 100) -> dict:
    """Compute advanced portfolio risk metrics including Sortino, CVaR, Cornish-Fisher VaR, tail risk."""
    data = {}

    beta = _safe(info.get("beta"), None)
    data["beta"] = round(beta, 2) if beta else None
    if beta:
        if beta < 0.5:
            data["beta_label"] = "Very Defensive"
        elif beta < 0.8:
            data["beta_label"] = "Defensive"
        elif beta < 1.2:
            data["beta_label"] = "Market"
        elif beta < 1.5:
            data["beta_label"] = "Aggressive"
        else:
            data["beta_label"] = "High Beta"
    else:
        data["beta_label"] = "Unknown"

    null_risk = {
        "annualized_volatility": None, "max_drawdown_1y": None,
        "sharpe_ratio_1y": None, "sortino_ratio_1y": None,
        "var_95_daily": None, "var_95_monthly": None,
        "cvar_95_daily": None, "cvar_95_monthly": None,
        "position_var_95_daily": None, "position_var_95_monthly": None,
        "position_cvar_95_daily": None, "position_cvar_95_monthly": None,
        "upside_volatility": None, "downside_volatility": None,
        "skewness": None, "kurtosis": None,
        "calmar_ratio": None, "max_drawdown_duration_days": None,
        "ulcer_index": None, "tail_risk_ratio": None,
        "win_rate": None, "avg_win_pct": None, "avg_loss_pct": None,
        "gain_to_pain_ratio": None,
    }

    if hist_1y is None or hist_1y.empty or len(hist_1y) < 30:
        data.update(null_risk)
        return data

    close = hist_1y["Close"]
    if isinstance(close, pd.DataFrame):
        close = close.iloc[:, 0]
    returns = close.pct_change().dropna()

    if len(returns) < 30:
        data.update(null_risk)
        return data

    daily_vol = float(returns.std())
    ann_vol = round(daily_vol * np.sqrt(252), 4)
    mean_return = float(returns.mean())

    # Sharpe (risk-free ~4.5%)
    rf_daily = 0.045 / 252
    sharpe = round(((mean_return - rf_daily) * 252) / (daily_vol * np.sqrt(252)), 2) if daily_vol > 0 else 0

    # Sortino (downside deviation only)
    neg_returns = returns[returns < 0]
    downside_dev = float(neg_returns.std()) if len(neg_returns) > 5 else daily_vol
    sortino = round(((mean_return - rf_daily) * 252) / (downside_dev * np.sqrt(252)), 2) if downside_dev > 0 else 0
    downside_vol_ann = round(downside_dev * np.sqrt(252), 4)
    upside_returns = returns[returns > 0]
    upside_dev = float(upside_returns.std()) if len(upside_returns) > 5 else daily_vol
    upside_vol_ann = round(upside_dev * np.sqrt(252), 4)

    # Drawdown analysis
    cummax = close.cummax()
    drawdown = (close / cummax - 1)
    max_dd = round(float(drawdown.min()), 4)

    # Max drawdown duration
    dd_duration = 0
    max_dd_duration = 0
    for val in drawdown.values:
        if val < 0:
            dd_duration += 1
            max_dd_duration = max(max_dd_duration, dd_duration)
        else:
            dd_duration = 0

    # Calmar ratio (annualized return / max drawdown)
    ann_return = mean_return * 252
    calmar = round(ann_return / abs(max_dd), 2) if max_dd != 0 else 0

    # Ulcer Index (RMS of drawdowns — better measure of downside risk)
    ulcer = round(float(np.sqrt((drawdown ** 2).mean())) * 100, 2)

    # Parametric VaR (95%)
    var_daily = round(1.645 * daily_vol, 4)
    var_monthly = round(var_daily * np.sqrt(21), 4)

    # Conditional VaR / Expected Shortfall (average loss beyond VaR)
    sorted_returns = np.sort(returns.values)
    cutoff_idx = max(1, int(len(sorted_returns) * 0.05))
    tail_losses = sorted_returns[:cutoff_idx]
    cvar_daily = round(float(abs(tail_losses.mean())), 4) if len(tail_losses) > 0 else var_daily
    cvar_monthly = round(cvar_daily * np.sqrt(21), 4)

    # Position VaR/CVaR
    pos_value = current_price * shares
    pos_var_daily = round(pos_value * var_daily, 2)
    pos_var_monthly = round(pos_value * var_monthly, 2)
    pos_cvar_daily = round(pos_value * cvar_daily, 2)
    pos_cvar_monthly = round(pos_value * cvar_monthly, 2)

    # Higher moments
    skew = round(float(returns.skew()), 3)
    kurt = round(float(returns.kurtosis()), 3)

    # Tail risk ratio (CVaR / VaR — how fat the tail is; >1.5 = fat tails)
    tail_risk = round(cvar_daily / var_daily, 2) if var_daily > 0 else None

    # Win rate & payoff
    wins = returns[returns > 0]
    losses = returns[returns < 0]
    win_rate = round(len(wins) / len(returns) * 100, 1) if len(returns) > 0 else None
    avg_win = round(float(wins.mean()) * 100, 3) if len(wins) > 0 else None
    avg_loss = round(float(losses.mean()) * 100, 3) if len(losses) > 0 else None

    # Gain-to-pain ratio (sum of returns / sum of absolute losses)
    total_gains = float(wins.sum()) if len(wins) > 0 else 0
    total_losses = float(abs(losses.sum())) if len(losses) > 0 else 0
    gtp = round(total_gains / total_losses, 2) if total_losses > 0 else None

    data.update({
        "annualized_volatility": ann_vol,
        "max_drawdown_1y": max_dd,
        "sharpe_ratio_1y": sharpe,
        "sortino_ratio_1y": sortino,
        "var_95_daily": var_daily,
        "var_95_monthly": var_monthly,
        "cvar_95_daily": cvar_daily,
        "cvar_95_monthly": cvar_monthly,
        "position_var_95_daily": pos_var_daily,
        "position_var_95_monthly": pos_var_monthly,
        "position_cvar_95_daily": pos_cvar_daily,
        "position_cvar_95_monthly": pos_cvar_monthly,
        "upside_volatility": upside_vol_ann,
        "downside_volatility": downside_vol_ann,
        "skewness": skew,
        "kurtosis": kurt,
        "calmar_ratio": calmar,
        "max_drawdown_duration_days": max_dd_duration,
        "ulcer_index": ulcer,
        "tail_risk_ratio": tail_risk,
        "win_rate": win_rate,
        "avg_win_pct": avg_win,
        "avg_loss_pct": avg_loss,
        "gain_to_pain_ratio": gtp,
    })

    return data


def _compute_risk_score(risk: dict, liquidity: dict) -> int:
    """Convert raw risk metrics into a composite 0–100 exit-risk score.
    Low risk → low score (hold), high risk → high score (exit pressure).
    Baseline 20 for a normal-risk stock."""
    score = 20

    # --- Sharpe ratio (strongest signal) ---
    sharpe = risk.get("sharpe_ratio_1y")
    if sharpe is not None:
        if sharpe < -0.5:
            score += 30       # deeply negative risk-adjusted returns
        elif sharpe < 0:
            score += 22        # losing money risk-adjusted
        elif sharpe < 0.3:
            score += 12        # poor returns for risk taken
        elif sharpe < 0.8:
            score += 3
        elif sharpe > 2.0:
            score -= 8         # excellent
        elif sharpe > 1.2:
            score -= 5

    # --- Sortino (downside-only) ---
    sortino = risk.get("sortino_ratio_1y")
    if sortino is not None:
        if sortino < 0:
            score += 12
        elif sortino < 0.5:
            score += 5
        elif sortino > 2.5:
            score -= 5

    # --- Max drawdown severity ---
    dd = risk.get("max_drawdown_1y")
    if dd is not None:
        dd_pct = abs(dd)
        if dd_pct > 0.40:
            score += 22
        elif dd_pct > 0.30:
            score += 15
        elif dd_pct > 0.20:
            score += 8
        elif dd_pct > 0.12:
            score += 3
        elif dd_pct < 0.06:
            score -= 3

    # --- Volatility ---
    vol = risk.get("annualized_volatility")
    if vol is not None:
        if vol > 0.60:
            score += 15
        elif vol > 0.45:
            score += 10
        elif vol > 0.30:
            score += 5
        elif vol < 0.12:
            score -= 3

    # --- Tail risk (skewness + kurtosis) ---
    skew = risk.get("skewness")
    kurt = risk.get("kurtosis")
    if skew is not None and skew < -1.0:
        score += 8  # heavy negative skew
    elif skew is not None and skew < -0.5:
        score += 3
    if kurt is not None and kurt > 5:
        score += 8  # fat tails — extreme moves likely
    elif kurt is not None and kurt > 3:
        score += 3

    # --- CVaR tail ratio ---
    tail_ratio = risk.get("tail_risk_ratio")
    if tail_ratio is not None and tail_ratio > 1.8:
        score += 5

    # --- Win rate / gain-to-pain ---
    win_rate = risk.get("win_rate")
    gtp = risk.get("gain_to_pain_ratio")
    if win_rate is not None and win_rate < 45:
        score += 8
    elif win_rate is not None and win_rate > 55:
        score -= 3
    if gtp is not None and gtp < 0.5:
        score += 5
    elif gtp is not None and gtp > 1.5:
        score -= 3

    # --- Liquidity risk ---
    liq_rating = liquidity.get("liquidity_rating", "")
    days_liq = liquidity.get("days_to_liquidate")
    if liq_rating == "Low":
        score += 10
    elif liq_rating == "Fair":
        score += 3
    if days_liq is not None and days_liq > 10:
        score += 8
    elif days_liq is not None and days_liq > 5:
        score += 3

    return _clamp(score)


def _score_to_label(score: int) -> str:
    """Convert a 0-100 PRESSURE score (higher = more exit pressure) to a label."""
    if score >= 70:
        return "Strong Exit"
    elif score >= 55:
        return "Consider Exit"
    elif score >= 40:
        return "Caution"
    elif score >= 22:
        return "Hold"
    return "Strong Hold"


def _health_label(health: int) -> str:
    """Convert a 0-100 HEALTH score (higher = healthier / stronger hold) to a label.
    This is the user-facing convention: every displayed score reads higher = better.
    Thresholds are aligned with ``_entry_label`` so Hold and Entry read consistently."""
    if health >= 72:
        return "Strong Hold"
    elif health >= 58:
        return "Hold"
    elif health >= 45:
        return "Caution"
    elif health >= 32:
        return "Consider Exit"
    return "Strong Exit"


def _compute_liquidity(hist_1y: pd.DataFrame, current_price: float, shares: float = 100) -> dict:
    """Volume and liquidity assessment."""
    if hist_1y is None or hist_1y.empty:
        return {"avg_daily_volume_20d": None, "avg_daily_dollar_volume": None,
                "days_to_liquidate": None, "liquidity_rating": "Unknown"}

    volume = hist_1y["Volume"] if "Volume" in hist_1y.columns else pd.Series(dtype=float)
    if volume.empty:
        return {"avg_daily_volume_20d": None, "avg_daily_dollar_volume": None,
                "days_to_liquidate": None, "liquidity_rating": "Unknown"}

    avg_vol = int(volume.tail(20).mean())
    avg_dollar_vol = round(avg_vol * current_price, 0)
    days_to_liq = round(shares / (avg_vol * 0.10), 1) if avg_vol > 0 else 999

    if avg_dollar_vol > 100_000_000:
        rating = "Excellent"
    elif avg_dollar_vol > 10_000_000:
        rating = "Good"
    elif avg_dollar_vol > 1_000_000:
        rating = "Fair"
    else:
        rating = "Low"

    return {
        "avg_daily_volume_20d": avg_vol,
        "avg_daily_dollar_volume": avg_dollar_vol,
        "days_to_liquidate": days_to_liq,
        "liquidity_rating": rating,
    }


# ---------------------------------------------------------------------------
# Price Chart with Events & Trend Analysis
# ---------------------------------------------------------------------------

def _compute_price_chart(stock, info: dict, purchase_date: str, hist_1y: pd.DataFrame,
                         current_price: float) -> dict:
    """Compute price/volume chart data with 30/60/90 day trends and major events."""
    result = {
        "timestamps": [], "prices": [], "volumes": [],
        "cost_basis_line": None,
        "trend_30d": None, "trend_60d": None, "trend_90d": None,
        "events": [],
        "sma50": [], "sma200": [],
    }

    try:
        # Fetch historical data covering the holding period (up to 2Y)
        pd_date = datetime.strptime(purchase_date, "%Y-%m-%d").date()
        days_since = (datetime.now().date() - pd_date).days
        if days_since > 700:
            period = "5y"
        elif days_since > 350:
            period = "2y"
        else:
            period = "1y"

        hist = stock.history(period=period, interval="1d")
        if hist is None or hist.empty or len(hist) < 10:
            return result

        close = hist["Close"]
        if isinstance(close, pd.DataFrame):
            close = close.iloc[:, 0]
        vol = hist["Volume"] if "Volume" in hist.columns else pd.Series(dtype=float)

        # Downsample if too many points (>500)
        if len(close) > 500:
            # Use every Nth point
            step = max(1, len(close) // 400)
            indices = list(range(0, len(close), step))
            if indices[-1] != len(close) - 1:
                indices.append(len(close) - 1)
        else:
            indices = list(range(len(close)))

        result["timestamps"] = [close.index[i].strftime("%Y-%m-%d") for i in indices]
        result["prices"] = [round(float(close.iloc[i]), 2) for i in indices]
        result["volumes"] = [int(vol.iloc[i]) if not vol.empty and i < len(vol) else 0 for i in indices]

        # SMA overlays
        sma50 = close.rolling(50).mean()
        sma200 = close.rolling(200).mean()
        result["sma50"] = [round(float(sma50.iloc[i]), 2) if not np.isnan(sma50.iloc[i]) else None for i in indices]
        result["sma200"] = [round(float(sma200.iloc[i]), 2) if len(sma200) > i and not np.isnan(sma200.iloc[i]) else None for i in indices]

        # 30/60/90 day trend analysis
        for days, key in [(30, "trend_30d"), (60, "trend_60d"), (90, "trend_90d")]:
            if len(close) >= days:
                segment = close.iloc[-days:]
                start_p = float(segment.iloc[0])
                end_p = float(segment.iloc[-1])
                change_pct = round((end_p - start_p) / start_p * 100, 2)
                high_p = round(float(segment.max()), 2)
                low_p = round(float(segment.min()), 2)
                ret = segment.pct_change().dropna()
                vol_ann = round(float(ret.std()) * np.sqrt(252) * 100, 1) if len(ret) > 5 else None
                # Trend direction using linear regression slope
                x = np.arange(len(segment))
                y = segment.values
                if len(x) > 5:
                    slope = np.polyfit(x, y, 1)[0]
                    trend = "Uptrend" if slope > 0.05 * start_p / len(x) else ("Downtrend" if slope < -0.05 * start_p / len(x) else "Sideways")
                else:
                    trend = "Sideways"
                result[key] = {
                    "change_pct": change_pct,
                    "high": high_p,
                    "low": low_p,
                    "volatility": vol_ann,
                    "direction": trend,
                }

        # Major events detection
        events = []

        # 1. Large daily moves (>3% in a day)
        daily_returns = close.pct_change()
        for i in range(1, len(daily_returns)):
            ret = float(daily_returns.iloc[i])
            if abs(ret) >= 0.03:
                date_str = daily_returns.index[i].strftime("%Y-%m-%d")
                price_val = round(float(close.iloc[i]), 2)
                events.append({
                    "date": date_str,
                    "price": price_val,
                    "type": "big_move",
                    "label": f"{'▲' if ret > 0 else '▼'} {abs(ret)*100:.1f}%",
                    "description": f"{'Surge' if ret > 0 else 'Drop'} of {abs(ret)*100:.1f}% on {date_str}",
                })

        # 2. 52-week high/low touches
        hi52_date = close.idxmax()
        lo52_date = close.idxmin()
        events.append({
            "date": hi52_date.strftime("%Y-%m-%d"),
            "price": round(float(close.loc[hi52_date]), 2),
            "type": "52w_high",
            "label": "52W High",
            "description": f"52-week high of ${float(close.loc[hi52_date]):.2f}",
        })
        events.append({
            "date": lo52_date.strftime("%Y-%m-%d"),
            "price": round(float(close.loc[lo52_date]), 2),
            "type": "52w_low",
            "label": "52W Low",
            "description": f"52-week low of ${float(close.loc[lo52_date]):.2f}",
        })

        # 3. SMA crossovers (Golden/Death cross)
        if len(sma50) > 200 and len(sma200) > 200:
            for i in range(201, len(sma50)):
                if np.isnan(sma50.iloc[i]) or np.isnan(sma200.iloc[i]) or np.isnan(sma50.iloc[i-1]) or np.isnan(sma200.iloc[i-1]):
                    continue
                prev_diff = sma50.iloc[i-1] - sma200.iloc[i-1]
                curr_diff = sma50.iloc[i] - sma200.iloc[i]
                if prev_diff <= 0 and curr_diff > 0:
                    events.append({
                        "date": sma50.index[i].strftime("%Y-%m-%d"),
                        "price": round(float(close.iloc[i]), 2),
                        "type": "golden_cross",
                        "label": "Golden Cross",
                        "description": "SMA50 crossed above SMA200 — bullish signal",
                    })
                elif prev_diff >= 0 and curr_diff < 0:
                    events.append({
                        "date": sma50.index[i].strftime("%Y-%m-%d"),
                        "price": round(float(close.iloc[i]), 2),
                        "type": "death_cross",
                        "label": "Death Cross",
                        "description": "SMA50 crossed below SMA200 — bearish signal",
                    })

        # 4. Volume spikes (>2.5x average)
        if not vol.empty and len(vol) > 20:
            vol_avg = vol.rolling(20).mean()
            for i in range(21, len(vol)):
                if vol_avg.iloc[i] > 0 and vol.iloc[i] > vol_avg.iloc[i] * 2.5:
                    date_str = vol.index[i].strftime("%Y-%m-%d")
                    mult = vol.iloc[i] / vol_avg.iloc[i]
                    events.append({
                        "date": date_str,
                        "price": round(float(close.iloc[i]), 2),
                        "type": "volume_spike",
                        "label": f"Vol {mult:.1f}x",
                        "description": f"Volume spike ({mult:.1f}x avg) on {date_str}",
                    })

        # 5. Earnings dates from news / earnings_dates
        try:
            ed = getattr(stock, "earnings_dates", None)
            if ed is not None and not ed.empty:
                for idx in ed.index[:8]:
                    edate = idx.strftime("%Y-%m-%d") if hasattr(idx, "strftime") else str(idx)[:10]
                    # Find price on that date
                    ep = None
                    try:
                        nearest = close.index.get_indexer([idx], method="nearest")[0]
                        ep = round(float(close.iloc[nearest]), 2)
                    except Exception:
                        pass
                    events.append({
                        "date": edate,
                        "price": ep,
                        "type": "earnings",
                        "label": "Earnings",
                        "description": f"Earnings report on {edate}",
                    })
        except Exception:
            pass

        # Deduplicate events and limit (keep unique date+type combos, max 20)
        seen = set()
        unique_events = []
        for ev in sorted(events, key=lambda e: e["date"], reverse=True):
            key = (ev["date"], ev["type"])
            if key not in seen:
                seen.add(key)
                unique_events.append(ev)
        result["events"] = unique_events[:25]

    except Exception:
        pass

    return result


# ---------------------------------------------------------------------------
# Main Orchestrator
# ---------------------------------------------------------------------------

def _run_exit_analysis_sync(
    ticker: str,
) -> dict:
    ticker = ticker.upper()
    stock = yf.Ticker(ticker)
    info = stock.info or {}

    # Current price
    current_price = _safe(info.get("currentPrice")) or _safe(info.get("regularMarketPrice")) or 0
    if not current_price:
        try:
            hist = stock.history(period="1d")
            if not hist.empty:
                current_price = float(hist["Close"].iloc[-1])
        except Exception:
            pass
    if not current_price:
        return {"error": f"Could not fetch current price for {ticker}"}

    # Historical data
    try:
        hist_1y = stock.history(period="1y", interval="1d")
    except Exception:
        hist_1y = pd.DataFrame()

    try:
        hist_6m = stock.history(period="6mo", interval="1d")
    except Exception:
        hist_6m = pd.DataFrame()



    # Compute all pillars
    p1 = _compute_fundamental(stock, info)
    p2 = _compute_macro(info, hist_6m)
    p3 = _compute_structural(stock, info)
    p4 = _compute_geopolitical(stock, info)
    p5 = _compute_valuation(info, current_price)
    p6 = _compute_sentiment(info, current_price)
    p7 = _compute_sector_rotation(info, hist_1y)

    # Pillars 8-10 (Ownership & Flow, Catalyst & Revisions, Quality & Capital)
    p8 = _compute_ownership_flow(stock, info)
    p9 = _compute_catalyst_revisions(stock, info)
    p10 = _compute_quality_capital(stock, info)

    # Technical signals + price chart data
    tech = _compute_technical_signals(stock, info, hist_6m)

    # Modern technical — the shared trade-setup engine (bias / regime / structure / ranked setups)
    modern_tech = _compute_modern_technical(stock)

    # Price/Volume chart data for the holding period
    price_chart = _compute_price_chart(stock, info, "", hist_1y, current_price)

    # Update long-term composite with actual pillar scores
    lt = tech["longterm"]
    lt["fundamental_health_score"] = p1["score"]
    lt["valuation_score"] = p5["score"]
    lt["composite_score"] = _clamp(
        lt.get("sma_cross_score", 40) * 0.25 +
        p1["score"] * 0.25 +
        p5["score"] * 0.30 +
        lt.get("analyst_consensus_score", 45) * 0.20
    )

    # Options protection
    options_protection = _compute_options_protection(stock, current_price, 100)

    # Risk
    risk = _compute_risk(info, hist_1y, current_price, 100)

    # Liquidity
    liquidity = _compute_liquidity(hist_1y, current_price, 100)

    # Risk composite score
    risk_score = _compute_risk_score(risk, liquidity)

    # Overall EXIT score — 10 pillars + technical + risk
    pillar_names = ["Fundamental", "Macro", "Structural", "Geopolitical",
                    "Valuation", "Sentiment", "Sector Rotation",
                    "Ownership & Flow", "Catalyst & Revisions", "Quality & Capital"]
    pillar_objs = [p1, p2, p3, p4, p5, p6, p7, p8, p9, p10]
    pillar_scores = [p["score"] for p in pillar_objs]
    # Weighted average — the core business / valuation / technical-flow pillars matter
    # more to a hold decision than the softer contextual pillars (macro, geopolitical,
    # sector), so the overall Hold score isn't dragged around by context. Order matches
    # pillar_names: Fundamental, Macro, Structural, Geopolitical, Valuation, Sentiment,
    # Sector Rotation, Ownership & Flow, Catalyst & Revisions, Quality & Capital.
    _PILLAR_W = [0.16, 0.05, 0.08, 0.05, 0.14, 0.08, 0.06, 0.10, 0.12, 0.16]
    pillar_avg = sum(w * s for w, s in zip(_PILLAR_W, pillar_scores))

    # Technical dimension — blend the modern engine's exit-pressure read with the legacy
    # swing/long-term composite so the score reflects the new TA model.
    legacy_tech_avg = (tech["swing"]["composite_score"] + tech["longterm"]["composite_score"]) / 2
    if modern_tech.get("available"):
        tech_avg = legacy_tech_avg * 0.5 + modern_tech["exit_pressure_score"] * 0.5
    else:
        tech_avg = legacy_tech_avg

    # Weighted: Pillars 50%, Technical 25%, Risk 25%
    overall_score = _clamp(pillar_avg * 0.50 + tech_avg * 0.25 + risk_score * 0.25)
    overall_label = _score_to_label(overall_score)

    # Per-dimension labels
    pillar_label = _score_to_label(_clamp(pillar_avg))
    tech_label = _score_to_label(_clamp(tech_avg))
    risk_label = _score_to_label(risk_score)

    # Entry Attractiveness — anchored to the overall HOLD health so the two ratings
    # stay coherent (overall_score is exit-pressure here; hold health is its inverse).
    pillar_scores_by_name = {n: s for n, s in zip(pillar_names, pillar_scores)}
    entry_rating = _compute_entry_rating(pillar_scores_by_name, modern_tech, _clamp(100 - overall_score))

    # Find top concern pillars (sorted desc by score)
    ranked = sorted(zip(pillar_names, pillar_objs, pillar_scores),
                    key=lambda x: x[2], reverse=True)

    # Build key drivers — include pillars, technical, and risk
    swing_composite = tech["swing"]["composite_score"]
    lt_composite = tech["longterm"]["composite_score"]
    key_drivers = []
    for name, pobj, pscore in ranked[:3]:
        if pscore > 35:
            key_drivers.append({"pillar": name, "score": pscore, "severity": "high"})
        elif pscore > 25:
            key_drivers.append({"pillar": name, "score": pscore, "severity": "moderate"})

    # Technical as a driver
    if swing_composite >= 55:
        key_drivers.append({"pillar": "Swing Technical", "score": swing_composite, "severity": "high"})
    elif swing_composite >= 40:
        key_drivers.append({"pillar": "Swing Technical", "score": swing_composite, "severity": "moderate"})
    if lt_composite >= 55:
        key_drivers.append({"pillar": "Long-term Technical", "score": lt_composite, "severity": "high"})
    elif lt_composite >= 40:
        key_drivers.append({"pillar": "Long-term Technical", "score": lt_composite, "severity": "moderate"})

    # Risk as a driver
    risk_sharpe = risk.get("sharpe_ratio_1y")
    risk_dd = risk.get("max_drawdown_1y")
    risk_ann_vol = risk.get("annualized_volatility")
    risk_skew = risk.get("skewness")
    risk_kurt = risk.get("kurtosis")
    risk_sortino = risk.get("sortino_ratio_1y")
    if risk_sharpe is not None and risk_sharpe < 0:
        key_drivers.append({"pillar": "Risk (Sharpe < 0)", "score": 65, "severity": "high"})
    elif risk_dd is not None and abs(risk_dd) > 0.25:
        key_drivers.append({"pillar": "Risk (Deep Drawdown)", "score": 55, "severity": "high"})
    elif risk_ann_vol is not None and risk_ann_vol > 0.45:
        key_drivers.append({"pillar": "Risk (High Volatility)", "score": 50, "severity": "moderate"})

    # Sort key drivers by score descending, keep top 5
    key_drivers.sort(key=lambda x: x["score"], reverse=True)
    key_drivers = key_drivers[:5]

    # Build strengths — include pillars, technical, and risk
    strengths = []
    for name, pobj, pscore in ranked:
        if pscore < 20:
            strengths.append({"pillar": name, "score": pscore})
    if swing_composite < 25:
        strengths.append({"pillar": "Swing Technical", "score": swing_composite})
    if lt_composite < 25:
        strengths.append({"pillar": "Long-term Technical", "score": lt_composite})
    if risk_sharpe is not None and risk_sharpe > 1.5:
        strengths.append({"pillar": "Risk (Strong Sharpe)", "score": 10})
    elif risk_sortino is not None and risk_sortino > 2.0:
        strengths.append({"pillar": "Risk (Strong Sortino)", "score": 10})

    # Build quantitative breakdown
    quant_summary = {
        "pillar_avg": round(pillar_avg, 1),
        "pillar_label": pillar_label,
        "tech_avg": round(tech_avg, 1),
        "tech_label": tech_label,
        "risk_score": risk_score,
        "risk_label": risk_label,
        "pillar_weight": 0.50,
        "tech_weight": 0.25,
        "risk_weight": 0.25,
        "swing_score": swing_composite,
        "longterm_score": lt_composite,
        "highest_pillar": {"name": ranked[0][0], "score": ranked[0][2]},
        "lowest_pillar": {"name": ranked[-1][0], "score": ranked[-1][2]},
        "pillar_scores": {name: sc for name, _, sc in zip(pillar_names, pillar_objs, pillar_scores)},
    }

    # Build qualitative narrative — ALL dimensions
    narratives = []
    p1_data = p1.get("data", {})
    p2_data = p2.get("data", {})
    p4_data = p4.get("data", {})
    p5_data = p5.get("data", {})
    p6_data = p6.get("data", {})
    p7_data = p7.get("data", {})

    # === PILLAR NARRATIVES ===

    # Fundamental narrative
    if p1["score"] >= 40:
        parts = []
        if p1_data.get("revenue_direction") == "declining":
            parts.append("declining revenue")
        if p1_data.get("margin_direction") == "declining":
            parts.append("compressing margins")
        if p1_data.get("fcf_direction") == "declining":
            parts.append("weakening cash flow")
        ic_val = p1_data.get("interest_coverage")
        if ic_val and ic_val < 3:
            parts.append(f"low interest coverage ({ic_val}x)")
        if parts:
            narratives.append(f"Fundamental concerns: {', '.join(parts)}.")
    elif p1["score"] <= 18:
        narratives.append("Fundamentals are strong with healthy revenue growth and solid margins.")

    # Valuation narrative
    if p5["score"] >= 40:
        pe = p5_data.get("trailing_pe")
        target = p5_data.get("target_upside_pct")
        parts = []
        if pe:
            parts.append(f"PE of {pe:.0f}x")
        if target is not None and target < 0:
            parts.append(f"analyst target implies {target:.0f}% downside")
        if parts:
            narratives.append(f"Valuation elevated: {', '.join(parts)}.")
    elif p5["score"] <= 18:
        narratives.append("Valuation appears reasonable relative to sector peers.")

    # Portfolio narrative
    if p6["score"] >= 40:
        opp = p6_data.get("opportunity_cost")
        if opp is not None and opp < -5:
            narratives.append(f"Underperforming S&P 500 by {abs(opp):.0f}% over the past year.")
        gl_pct = p6_data.get("unrealized_gain_loss_pct", 0)
        if gl_pct > 100:
            narratives.append(f"Large unrealized gain of {gl_pct:.0f}% — consider rebalancing.")
        elif gl_pct < -20:
            narratives.append(f"Significant loss of {gl_pct:.0f}% — review thesis.")

    # Sector Rotation narrative
    if p7["score"] >= 40:
        rotation = p7_data.get("rotation_signal", "")
        if "Outflow" in rotation or "Leaving" in rotation:
            narratives.append(f"Sector rotation signals funds moving out ({rotation}).")

    # Geopolitical narrative
    if p4["score"] >= 40:
        sentiment = p4_data.get("news_sentiment", "")
        themes = p4_data.get("regulatory_themes_detected", [])
        if sentiment in ("Negative", "Mixed"):
            narratives.append(f"News sentiment is {sentiment.lower()} with regulatory attention.")
        if themes:
            narratives.append(f"Regulatory themes detected: {', '.join(themes[:3])}.")

    # Ownership & Flow narrative
    p8_data = p8.get("data", {})
    if p8["score"] >= 40:
        parts = []
        if (p8_data.get("short_pct_float") or 0) >= 10:
            parts.append(f"{p8_data['short_pct_float']:.0f}% short float")
        if (p8_data.get("short_change_pct") or 0) >= 5:
            parts.append("rising short interest")
        if p8_data.get("insider_signal") in ("Net selling", "Mild selling"):
            parts.append("insider selling")
        if parts:
            narratives.append(f"Ownership & flow: {', '.join(parts)}.")
    elif p8["score"] <= 18:
        narratives.append("Ownership is stable with light short interest.")

    # Catalyst & Revisions narrative
    p9_data = p9.get("data", {})
    if p9["score"] >= 40:
        parts = []
        if p9_data.get("eps_revision_trend") in ("cutting", "mildly cutting"):
            parts.append("analysts cutting EPS estimates")
        up9 = p9_data.get("target_upside_pct")
        if up9 is not None and up9 < 0:
            parts.append(f"mean target {up9:.0f}% below price")
        rm = p9_data.get("recommendation_mean")
        if rm is not None and rm >= 3:
            parts.append("soft analyst consensus")
        if parts:
            narratives.append(f"Catalysts weak: {', '.join(parts)}.")
    elif p9["score"] <= 18:
        parts = []
        if p9_data.get("eps_revision_trend") in ("raising", "mildly raising"):
            parts.append("estimates being raised")
        up9 = p9_data.get("target_upside_pct")
        if up9 is not None and up9 >= 10:
            parts.append(f"{up9:.0f}% upside to mean target")
        narratives.append("Catalysts supportive" + (f": {', '.join(parts)}." if parts else "."))

    # Quality & Capital narrative
    p10_data = p10.get("data", {})
    if p10["score"] >= 40:
        parts = []
        if (p10_data.get("roe") is not None) and p10_data["roe"] < 5:
            parts.append("low return on equity")
        conv = p10_data.get("fcf_conversion")
        if conv is not None and conv < 0.5:
            parts.append("weak FCF conversion")
        if p10_data.get("shares_trend") == "dilution":
            parts.append("share dilution")
        if parts:
            narratives.append(f"Quality concerns: {', '.join(parts)}.")
    elif p10["score"] <= 18:
        parts = []
        roe10 = p10_data.get("roe")
        if roe10 is not None and roe10 >= 15:
            parts.append(f"{roe10:.0f}% ROE")
        if p10_data.get("shares_trend") == "buyback":
            parts.append("net buybacks")
        narratives.append("High-quality capital allocation" + (f": {', '.join(parts)}." if parts else "."))

    # Modern technical narrative
    if modern_tech.get("available"):
        mb = modern_tech.get("bias", {})
        d = mb.get("direction", "neutral")
        if d in ("bullish", "bearish"):
            rr = modern_tech.get("best_long_rr") if d == "bullish" else modern_tech.get("best_short_rr")
            rr_txt = f", best setup {rr:.1f}:1 R:R" if rr else ""
            narratives.append(
                f"Trade-setup engine reads {mb.get('strength', '')} {d} in a {modern_tech.get('regime')} regime{rr_txt}."
            )

    # === TECHNICAL NARRATIVES ===
    # Swing technical: granular per-indicator
    rsi_val = tech["swing"].get("rsi_value", 50)
    rsi_sig = tech["swing"].get("rsi_signal", "")
    macd_sig = tech["swing"].get("macd_signal", "")
    bb_pos = tech["swing"].get("bollinger_position", "")
    vol_phase = tech["swing"].get("volume_phase", "")
    ema_sig = tech["swing"].get("ema_signal", "")

    tech_parts = []
    if rsi_val > 70:
        tech_parts.append(f"RSI overbought at {rsi_val:.0f}")
    elif rsi_val < 30:
        tech_parts.append(f"RSI oversold at {rsi_val:.0f}")
    if "Bearish" in macd_sig:
        tech_parts.append(f"MACD {macd_sig.lower()}")
    elif "Bullish" in macd_sig:
        tech_parts.append(f"MACD {macd_sig.lower()}")
    if "Above" in bb_pos or "Well Above" in bb_pos:
        tech_parts.append(f"price {bb_pos.lower()}")
    elif "Below" in bb_pos:
        tech_parts.append(f"price {bb_pos.lower()}")
    if "Distribution" in vol_phase:
        tech_parts.append(f"volume in {vol_phase.lower()}")
    elif "Accumulation" in vol_phase:
        tech_parts.append(f"volume shows {vol_phase.lower()}")

    if swing_composite >= 55:
        narratives.append(f"Swing technicals bearish ({swing_composite}/100): {', '.join(tech_parts[:3])}." if tech_parts else f"Swing technical score elevated at {swing_composite}/100.")
    elif swing_composite <= 25:
        narratives.append(f"Swing technicals bullish ({swing_composite}/100): {', '.join(tech_parts[:3])}." if tech_parts else f"Swing technical score strong at {swing_composite}/100.")
    else:
        # Neutral range — still mention key indicators
        if tech_parts:
            narratives.append(f"Swing technicals mixed ({swing_composite}/100): {', '.join(tech_parts[:2])}.")

    # Long-term technical
    sma_label = tech["longterm"].get("sma_cross_label", "")
    analyst_rec = tech["longterm"].get("analyst_recommendation", "N/A")
    if lt_composite >= 55:
        lt_parts = []
        if "Death" in sma_label or "Below" in sma_label:
            lt_parts.append(sma_label)
        if analyst_rec not in ("N/A", "buy", "strong_buy"):
            lt_parts.append(f"analyst consensus: {analyst_rec}")
        narratives.append(f"Long-term trend bearish ({lt_composite}/100): {', '.join(lt_parts)}." if lt_parts else f"Long-term trend score elevated at {lt_composite}/100.")
    elif lt_composite <= 25:
        narratives.append(f"Long-term trend confirms uptrend ({lt_composite}/100): {sma_label}.")

    # === RISK NARRATIVES ===
    risk_parts = []
    if risk_sharpe is not None:
        if risk_sharpe < 0:
            risk_parts.append(f"Sharpe ratio negative ({risk_sharpe:.2f}) — losing money on a risk-adjusted basis")
        elif risk_sharpe < 0.5:
            risk_parts.append(f"weak risk-adjusted returns (Sharpe {risk_sharpe:.2f})")
        elif risk_sharpe > 2.0:
            risk_parts.append(f"excellent risk-adjusted returns (Sharpe {risk_sharpe:.2f})")

    if risk_dd is not None:
        dd_pct = abs(risk_dd) * 100
        if dd_pct > 30:
            risk_parts.append(f"severe max drawdown of {dd_pct:.0f}%")
        elif dd_pct > 20:
            risk_parts.append(f"significant max drawdown of {dd_pct:.0f}%")
        elif dd_pct < 8:
            risk_parts.append(f"contained max drawdown of only {dd_pct:.0f}%")

    if risk_ann_vol is not None:
        vol_pct = risk_ann_vol * 100
        if vol_pct > 50:
            risk_parts.append(f"very high volatility ({vol_pct:.0f}%)")
        elif vol_pct > 35:
            risk_parts.append(f"elevated volatility ({vol_pct:.0f}%)")
        elif vol_pct < 15:
            risk_parts.append(f"low volatility ({vol_pct:.0f}%)")

    if risk_skew is not None and risk_skew < -1.0:
        risk_parts.append("negatively skewed returns (larger downside moves)")
    if risk_kurt is not None and risk_kurt > 5:
        risk_parts.append("heavy tail risk (extreme moves more likely)")

    if risk_parts:
        # Group by severity
        concern_parts = [p for p in risk_parts if any(w in p for w in ["negative", "severe", "very high", "weak", "heavy", "negatively"])]
        positive_parts = [p for p in risk_parts if any(w in p for w in ["excellent", "contained", "low"])]
        neutral_parts = [p for p in risk_parts if p not in concern_parts and p not in positive_parts]

        if concern_parts:
            narratives.append(f"Risk profile concerning: {', '.join(concern_parts[:3])}.")
        if positive_parts:
            narratives.append(f"Risk profile favorable: {', '.join(positive_parts[:3])}.")
        if neutral_parts and not concern_parts and not positive_parts:
            narratives.append(f"Risk metrics: {', '.join(neutral_parts[:2])}.")

    # === OPTIONS/HEDGING NARRATIVE ===
    if options_protection.get("available"):
        pp = options_protection.get("protective_put", {})
        cc = options_protection.get("covered_call", {})
        opts_parts = []
        if pp.get("annualized_cost_pct"):
            cost_pct = pp["annualized_cost_pct"]
            floor = pp.get("protection_floor", 0)
            opts_parts.append(f"protective put available at ${floor:.0f} floor ({cost_pct:.1f}% annualized cost)")
        if cc.get("annualized_return_pct"):
            cc_return = cc["annualized_return_pct"]
            cc_cap = cc.get("upside_cap_pct", 0)
            opts_parts.append(f"covered call yields {cc_return:.1f}% annualized with {cc_cap:.0f}% upside cap")
        if opts_parts:
            narratives.append(f"Hedging: {'; '.join(opts_parts)}.")
    else:
        narratives.append("Options data not available for hedging analysis.")

    # === LIQUIDITY NARRATIVE ===
    liq_rating = liquidity.get("liquidity_rating", "")
    days_liq = liquidity.get("days_to_liquidate")
    if liq_rating == "Low" or (days_liq is not None and days_liq > 5):
        narratives.append(f"Liquidity concern: {liq_rating} rating, ~{days_liq} days to fully liquidate position.")

    # Overall summary sentence — incorporating all dimensions
    concern_count = len([d for d in key_drivers if d["severity"] == "high"])
    if overall_score >= 55:
        summary_line = f"Multiple signals ({concern_count} high-severity) suggest elevated risk — {overall_label.lower()} is recommended."
    elif overall_score >= 40:
        summary_line = f"Mixed signals detected across fundamentals, technicals and risk — monitor closely for further deterioration."
    elif overall_score >= 22:
        summary_line = f"Position appears stable across pillars, technicals and risk metrics — no immediate exit triggers."
    else:
        summary_line = f"Strong fundamentals, positive technicals and favorable risk profile support continued holding."

    # Entry-side narrative
    _e_sup = ", ".join(s["factor"].lower() for s in entry_rating["supports"]) or None
    _e_head = ", ".join(h["factor"].lower() for h in entry_rating["headwinds"]) or None
    if entry_rating["score"] >= 58:
        entry_rating["narrative"] = f"Entry looks attractive ({entry_rating['label']})" + (f" — supported by {_e_sup}." if _e_sup else ".")
    elif entry_rating["score"] >= 45:
        entry_rating["narrative"] = f"A watch-list entry ({entry_rating['label']})" + (f" — held back by {_e_head}." if _e_head else ".")
    else:
        entry_rating["narrative"] = f"Poor entry point here ({entry_rating['label']})" + (f" — {_e_head}." if _e_head else ".")

    # ==== HEALTH convention: flip every displayed score so higher = better ====
    # The pillar / technical / risk math above is "pressure" (higher = worse); the
    # user-facing contract is the opposite (higher = healthier / stronger hold),
    # matching the Entry rating. Entry was already computed on pressure above, so the
    # flip happens only now, at the display boundary.
    overall_health = _clamp(100 - overall_score)
    overall_health_label = _health_label(overall_health)
    pillar_health = _clamp(100 - pillar_avg)
    tech_health = _clamp(100 - tech_avg)
    risk_health = _clamp(100 - risk_score)

    for _p in pillar_objs:
        _p["score"] = _clamp(100 - _p["score"])
    for _d in key_drivers:
        _d["score"] = _clamp(100 - _d["score"])
    for _s in strengths:
        _s["score"] = _clamp(100 - _s["score"])

    quant_summary["pillar_avg"] = pillar_health
    quant_summary["pillar_label"] = _health_label(pillar_health)
    quant_summary["tech_avg"] = tech_health
    quant_summary["tech_label"] = _health_label(tech_health)
    quant_summary["risk_score"] = risk_health
    quant_summary["risk_label"] = _health_label(risk_health)
    quant_summary["swing_score"] = _clamp(100 - swing_composite)
    quant_summary["longterm_score"] = _clamp(100 - lt_composite)
    quant_summary["highest_pillar"] = {"name": quant_summary["highest_pillar"]["name"],
                                       "score": _clamp(100 - quant_summary["highest_pillar"]["score"])}
    quant_summary["lowest_pillar"] = {"name": quant_summary["lowest_pillar"]["name"],
                                      "score": _clamp(100 - quant_summary["lowest_pillar"]["score"])}
    quant_summary["pillar_scores"] = {n: _clamp(100 - v) for n, v in quant_summary["pillar_scores"].items()}

    signal_summary = {
        "overall_narrative": summary_line,
        "key_drivers": key_drivers,
        "strengths": strengths,
        "detail_narratives": narratives,
        "quantitative": quant_summary,
    }

    return {
        "success": True,
        "ticker": ticker,
        "overall_score": overall_health,
        "overall_label": overall_health_label,
        "score_convention": "health",  # higher = healthier / stronger hold
        "entry": entry_rating,
        "dimension_scores": {
            "pillars": {"score": pillar_health, "label": _health_label(pillar_health), "weight": 0.50},
            "technical": {"score": tech_health, "label": _health_label(tech_health), "weight": 0.25},
            "risk": {"score": risk_health, "label": _health_label(risk_health), "weight": 0.25},
        },
        "signal_summary": signal_summary,
        "pillars": {
            "fundamental": p1,
            "macro": p2,
            "structural": p3,
            "geopolitical": p4,
            "valuation": p5,
            "sentiment": p6,
            "sector_rotation": p7,
            "ownership_flow": p8,
            "catalyst_revisions": p9,
            "quality_capital": p10,
        },
        "technical_signals": tech,
        "technical_modern": modern_tech,
        "price_chart": price_chart,
        "options_protection": options_protection,
        "risk": risk,
        "liquidity": liquidity,
    }


async def run_exit_analysis(
    ticker: str
) -> dict:
    """Async wrapper — runs blocking yfinance calls in thread."""
    return await asyncio.to_thread(
        _run_exit_analysis_sync, ticker
    )
