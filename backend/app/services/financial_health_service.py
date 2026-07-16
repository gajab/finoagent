"""Financial Health analysis service.

Computes comprehensive financial health metrics from yfinance data:
- Revenue & margin trends (gross, operating, net)
- Earnings quality & consistency
- Cash flow growth & stability
- Debt levels, interest burden
- Liquidity (current ratio, quick ratio)
- Leverage (debt-to-assets, debt-to-EBITDA)
"""

import asyncio
import math
import logging
from typing import Any
import datetime

import numpy as np
import yfinance as yf

logger = logging.getLogger(__name__)


def _safe_float(val: Any) -> float | None:
    """Safely convert a value to float, returning None for NaN/None."""
    if val is None:
        return None
    try:
        f = float(val)
        return None if math.isnan(f) else f
    except (TypeError, ValueError):
        return None


def _extract_row(df, keys: list[str], col) -> float | None:
    """Try multiple row keys on a DataFrame column, returning the first valid value."""
    if df is None or df.empty:
        return None
    for key in keys:
        if key in df.index:
            val = _safe_float(df.loc[key, col])
            if val is not None:
                return val
    return None


def _pct(numerator: float | None, denominator: float | None) -> float | None:
    """Safe percentage calculation."""
    if numerator is None or denominator is None or denominator == 0:
        return None
    return round((numerator / denominator) * 100, 2)


def _growth(current: float | None, previous: float | None) -> float | None:
    """Calculate YoY growth as percentage."""
    if current is None or previous is None or previous == 0:
        return None
    return round(((current - previous) / abs(previous)) * 100, 2)


def _sum_last_n(df, keys: list[str], cols: list) -> float | None:
    """Sum a row (by first matching key) across the given columns.

    Returns None unless *every* column has a value — a partial sum would
    understate a trailing-twelve-months figure, so we'd rather show N/A.
    """
    if df is None or df.empty:
        return None
    for key in keys:
        if key in df.index:
            vals = [_safe_float(df.loc[key, c]) for c in cols if c in df.columns]
            vals = [v for v in vals if v is not None]
            return sum(vals) if len(vals) == len(cols) else None
    return None


def _compute_ttm(stock) -> dict | None:
    """Trailing-twelve-months snapshot from the last 4 quarterly statements.

    yfinance already fetches ``quarterly_income_stmt`` for the earnings block, so
    re-reading it here is cheap (the Ticker object caches it). Needs a full 4
    quarters — anything less isn't a real TTM.
    """
    try:
        qi = stock.quarterly_income_stmt
        qc = stock.quarterly_cashflow
    except Exception:
        return None
    if qi is None or qi.empty:
        return None
    cols = list(qi.columns)[:4]
    if len(cols) < 4:
        return None

    revenue = _sum_last_n(qi, ["Total Revenue", "TotalRevenue"], cols)
    gross_profit = _sum_last_n(qi, ["Gross Profit", "GrossProfit"], cols)
    operating_income = _sum_last_n(qi, ["Operating Income", "OperatingIncome", "EBIT", "Ebit"], cols)
    net_income = _sum_last_n(qi, ["Net Income", "NetIncome"], cols)
    ebitda = _sum_last_n(qi, ["EBITDA", "Ebitda"], cols)
    eps = _sum_last_n(qi, ["Diluted EPS", "DilutedEPS", "Basic EPS", "BasicEPS"], cols)

    qc_cols = list(qc.columns)[:4] if (qc is not None and not qc.empty) else []
    op_cf = None
    fcf = None
    if len(qc_cols) == 4:
        op_cf = _sum_last_n(qc, [
            "Operating Cash Flow", "OperatingCashFlow",
            "Cash Flow From Continuing Operating Activities",
        ], qc_cols)
        fcf = _sum_last_n(qc, ["Free Cash Flow", "FreeCashFlow"], qc_cols)
        if fcf is None:
            capex = _sum_last_n(qc, ["Capital Expenditure", "CapitalExpenditure", "Capital Expenditures"], qc_cols)
            if op_cf is not None and capex is not None:
                fcf = op_cf + capex  # capex is negative

    if revenue is None and net_income is None and eps is None:
        return None

    latest_q = cols[0]
    try:
        through = latest_q.strftime("%b %Y")
    except Exception:
        through = str(latest_q)[:7]

    return {
        "period": "TTM",
        "through": through,
        "revenue": revenue,
        "gross_profit": gross_profit,
        "operating_income": operating_income,
        "net_income": net_income,
        "ebitda": ebitda,
        "eps": round(eps, 2) if eps is not None else None,
        "operating_cf": op_cf,
        "free_cash_flow": fcf,
        "gross_margin": _pct(gross_profit, revenue),
        "operating_margin": _pct(operating_income, revenue),
        "net_margin": _pct(net_income, revenue),
        "ebitda_margin": _pct(ebitda, revenue),
    }


def _match_col_by_year(df, year: str):
    """Find the DataFrame column whose year matches ``year`` (statements from
    different reports don't always share identical period-end dates)."""
    if df is None or df.empty:
        return None
    for col in df.columns:
        cy = str(col.year) if hasattr(col, "year") else str(col)[:4]
        if cy == year:
            return col
    return None


def _ttm_returns(stock) -> dict:
    """ROE / ROA / ROIC on a trailing-twelve-months basis.

    Numerator = last 4 quarters (income); denominator = the most-recent-quarter
    balance sheet — so both sides cover the same window (period-matched), unlike
    a trailing income over a stale year-end balance sheet. Same formulas as the
    fiscal-year method, so the columns stay comparable.
    """
    try:
        qi = stock.quarterly_income_stmt
        qbs = stock.quarterly_balance_sheet
    except Exception:
        return {}
    if qi is None or qi.empty or qbs is None or qbs.empty:
        return {}
    cols = list(qi.columns)[:4]
    if len(cols) < 4:
        return {}

    ni = _sum_last_n(qi, ["Net Income", "NetIncome"], cols)
    op_inc = _sum_last_n(qi, ["Operating Income", "OperatingIncome", "EBIT", "Ebit"], cols)
    pretax = _sum_last_n(qi, ["Pretax Income", "PretaxIncome", "Income Before Tax"], cols)
    tax = _sum_last_n(qi, ["Tax Provision", "TaxProvision", "Income Tax Expense"], cols)

    bcol = list(qbs.columns)[0]  # most recent quarter, matched to the TTM window end
    equity = _extract_row(qbs, [
        "Stockholders Equity", "StockholdersEquity", "Total Equity Gross Minority Interest",
    ], bcol)
    assets = _extract_row(qbs, ["Total Assets", "TotalAssets"], bcol)
    debt = _extract_row(qbs, ["Total Debt", "TotalDebt"], bcol)
    cash = _extract_row(qbs, [
        "Cash And Cash Equivalents", "CashAndCashEquivalents",
        "Cash Cash Equivalents And Short Term Investments",
    ], bcol)

    roic = None
    if op_inc is not None and equity is not None:
        tax_rate = 0.21
        if pretax and tax is not None and pretax != 0:
            tr = tax / pretax
            if 0.0 <= tr <= 0.6:
                tax_rate = tr
        invested = equity + (debt or 0) - (cash or 0)
        if invested and invested > 0:
            roic = round((op_inc * (1 - tax_rate) / invested) * 100, 2)

    return {"roe": _pct(ni, equity), "roa": _pct(ni, assets), "roic": roic}


def _compute_returns_on_capital(income_stmt, balance_sheet, stock) -> dict | None:
    """ROE / ROA / ROIC per fiscal year, plus a TTM / Latest-FY / 1Y / 3Y / 5Y summary.

    * ROE  = Net Income / Shareholders' Equity
    * ROA  = Net Income / Total Assets
    * ROIC = NOPAT / Invested Capital, where NOPAT = Operating Income × (1 − tax
      rate) and Invested Capital = Debt + Equity − Cash. Tax rate is derived from
      the filing (Tax Provision ÷ Pretax Income), falling back to 21%.

    The FY columns come from audited annual statements (fully reproducible); the
    TTM column uses ``_ttm_returns`` for an up-to-the-quarter read.
    """
    if income_stmt is None or income_stmt.empty or balance_sheet is None or balance_sheet.empty:
        return None

    series: list[dict] = []
    for col in list(income_stmt.columns)[:6]:
        year = str(col.year) if hasattr(col, "year") else str(col)[:4]
        ni = _extract_row(income_stmt, ["Net Income", "NetIncome"], col)
        op_inc = _extract_row(income_stmt, ["Operating Income", "OperatingIncome", "EBIT", "Ebit"], col)
        pretax = _extract_row(income_stmt, ["Pretax Income", "PretaxIncome", "Income Before Tax"], col)
        tax = _extract_row(income_stmt, ["Tax Provision", "TaxProvision", "Income Tax Expense"], col)

        bcol = _match_col_by_year(balance_sheet, year)
        equity = _extract_row(balance_sheet, [
            "Stockholders Equity", "StockholdersEquity", "Total Equity Gross Minority Interest",
        ], bcol) if bcol is not None else None
        assets = _extract_row(balance_sheet, ["Total Assets", "TotalAssets"], bcol) if bcol is not None else None
        debt = _extract_row(balance_sheet, ["Total Debt", "TotalDebt"], bcol) if bcol is not None else None
        cash = _extract_row(balance_sheet, [
            "Cash And Cash Equivalents", "CashAndCashEquivalents",
            "Cash Cash Equivalents And Short Term Investments",
        ], bcol) if bcol is not None else None

        roic = None
        if op_inc is not None and equity is not None:
            tax_rate = 0.21
            if pretax and tax is not None and pretax != 0:
                tr = tax / pretax
                if 0.0 <= tr <= 0.6:
                    tax_rate = tr
            invested = equity + (debt or 0) - (cash or 0)
            if invested and invested > 0:
                roic = round((op_inc * (1 - tax_rate) / invested) * 100, 2)

        series.append({"year": year, "roe": _pct(ni, equity), "roa": _pct(ni, assets), "roic": roic})

    series.reverse()  # chronological: oldest → newest
    if not series:
        return None

    def _pick(back: int) -> dict:
        idx = len(series) - 1 - back
        return series[idx] if 0 <= idx < len(series) else {}

    ttm_ret = _ttm_returns(stock)

    summary = {
        m: {
            "ttm": ttm_ret.get(m),
            "current": series[-1].get(m),
            "y1": _pick(1).get(m),
            "y3": _pick(3).get(m),
            "y5": _pick(5).get(m),
        }
        for m in ("roe", "roa", "roic")
    }

    return {"series": series, "summary": summary}


def _run_sync(ticker: str) -> dict:
    """Build comprehensive financial health analysis."""
    stock = yf.Ticker(ticker)
    info = stock.info or {}
    income_stmt = stock.income_stmt
    balance_sheet = stock.balance_sheet
    cashflow = stock.cashflow

    result: dict = {
        "ticker": ticker.upper(),
        "company_name": info.get("shortName") or info.get("longName") or ticker.upper(),
        "sector": info.get("sector", "Unknown"),
        "industry": info.get("industry", "Unknown"),
        "currency": info.get("currency", "USD"),
    }

    # ================================================================
    # 1. Revenue & Margin Trends
    # ================================================================
    revenue_trend: list[dict] = []
    if income_stmt is not None and not income_stmt.empty:
        cols = list(income_stmt.columns)[:4]  # up to 4 years
        for col in cols:
            year = str(col.year) if hasattr(col, "year") else str(col)
            total_rev = _extract_row(income_stmt, ["Total Revenue", "TotalRevenue"], col)
            cogs = _extract_row(income_stmt, [
                "Cost Of Revenue", "CostOfRevenue",
                "Cost Of Goods Sold", "CostOfGoodsSold",
            ], col)
            gross_profit = _extract_row(income_stmt, ["Gross Profit", "GrossProfit"], col)
            operating_income = _extract_row(income_stmt, [
                "Operating Income", "OperatingIncome",
                "EBIT", "Ebit",
            ], col)
            net_income = _extract_row(income_stmt, ["Net Income", "NetIncome"], col)
            ebitda = _extract_row(income_stmt, ["EBITDA", "Ebitda"], col)
            interest_expense = _extract_row(income_stmt, [
                "Interest Expense", "InterestExpense",
                "Interest Expense Non Operating", "InterestExpenseNonOperating",
            ], col)

            # Calculate margins
            gross_margin = _pct(gross_profit, total_rev) if gross_profit else (
                _pct(total_rev - cogs, total_rev) if total_rev and cogs else None
            )
            operating_margin = _pct(operating_income, total_rev)
            net_margin = _pct(net_income, total_rev)
            ebitda_margin = _pct(ebitda, total_rev)

            revenue_trend.append({
                "year": year,
                "revenue": total_rev,
                "gross_profit": gross_profit,
                "operating_income": operating_income,
                "net_income": net_income,
                "ebitda": ebitda,
                "interest_expense": interest_expense,
                "gross_margin": gross_margin,
                "operating_margin": operating_margin,
                "net_margin": net_margin,
                "ebitda_margin": ebitda_margin,
            })

    # Reverse to chronological order (oldest → newest)
    revenue_trend.reverse()
    result["revenue_trend"] = revenue_trend

    # Revenue growth rates
    rev_growths = []
    for i in range(1, len(revenue_trend)):
        g = _growth(revenue_trend[i].get("revenue"), revenue_trend[i - 1].get("revenue"))
        rev_growths.append(g)
    result["revenue_growth_rates"] = rev_growths

    # ================================================================
    # 2. Earnings Quality & Consistency
    # ================================================================
    eps_history: list[dict] = []
    if income_stmt is not None and not income_stmt.empty:
        cols = list(income_stmt.columns)[:4]
        for col in reversed(cols):
            year = str(col.year) if hasattr(col, "year") else str(col)
            ni = _extract_row(income_stmt, ["Net Income", "NetIncome"], col)
            basic_eps = _extract_row(income_stmt, [
                "Basic EPS", "BasicEPS",
                "Diluted EPS", "DilutedEPS",
            ], col)
            eps_history.append({"year": year, "net_income": ni, "eps": basic_eps})

    result["eps_history"] = eps_history

    # EPS consistency: count how many years EPS was positive
    eps_vals = [e["eps"] for e in eps_history if e["eps"] is not None]
    result["eps_positive_years"] = sum(1 for e in eps_vals if e > 0)
    result["eps_total_years"] = len(eps_vals)

    # Earnings growth
    ni_growths = []
    for i in range(1, len(eps_history)):
        g = _growth(eps_history[i].get("net_income"), eps_history[i - 1].get("net_income"))
        ni_growths.append(g)
    result["earnings_growth_rates"] = ni_growths

    # ================================================================
    # 3. Cash Flow Growth & Stability
    # ================================================================
    cf_trend: list[dict] = []
    if cashflow is not None and not cashflow.empty:
        cols = list(cashflow.columns)[:4]
        for col in reversed(cols):
            year = str(col.year) if hasattr(col, "year") else str(col)
            op_cf = _extract_row(cashflow, [
                "Operating Cash Flow", "OperatingCashFlow",
                "Cash Flow From Continuing Operating Activities",
                "CashFlowFromContinuingOperatingActivities",
            ], col)
            capex = _extract_row(cashflow, [
                "Capital Expenditure", "CapitalExpenditure",
                "Capital Expenditures", "CapitalExpenditures",
            ], col)
            fcf = _extract_row(cashflow, ["Free Cash Flow", "FreeCashFlow"], col)
            if fcf is None and op_cf is not None and capex is not None:
                fcf = op_cf + capex  # capex is typically negative

            cf_trend.append({
                "year": year,
                "operating_cf": op_cf,
                "capex": capex,
                "free_cash_flow": fcf,
            })

    result["cash_flow_trend"] = cf_trend

    # FCF growth
    fcf_growths = []
    for i in range(1, len(cf_trend)):
        g = _growth(cf_trend[i].get("free_cash_flow"), cf_trend[i - 1].get("free_cash_flow"))
        fcf_growths.append(g)
    result["fcf_growth_rates"] = fcf_growths

    # FCF stability: standard deviation relative to mean
    fcf_vals = [c["free_cash_flow"] for c in cf_trend if c["free_cash_flow"] is not None]
    if len(fcf_vals) >= 2:
        fcf_mean = float(np.mean(fcf_vals))
        fcf_std = float(np.std(fcf_vals))
        result["fcf_stability"] = {
            "mean": round(fcf_mean, 0),
            "std": round(fcf_std, 0),
            "cv": round(fcf_std / abs(fcf_mean), 2) if fcf_mean != 0 else None,  # coefficient of variation
        }
    else:
        result["fcf_stability"] = None

    # ================================================================
    # 4. Debt Levels & Interest Burden
    # ================================================================
    debt_data: list[dict] = []
    if balance_sheet is not None and not balance_sheet.empty:
        cols = list(balance_sheet.columns)[:4]
        for col in reversed(cols):
            year = str(col.year) if hasattr(col, "year") else str(col)
            total_debt = _extract_row(balance_sheet, [
                "Total Debt", "TotalDebt",
            ], col)
            long_term_debt = _extract_row(balance_sheet, [
                "Long Term Debt", "LongTermDebt",
                "Long Term Debt And Capital Lease Obligation",
            ], col)
            short_term_debt = _extract_row(balance_sheet, [
                "Current Debt", "CurrentDebt",
                "Current Debt And Capital Lease Obligation",
                "Short Long Term Debt", "ShortLongTermDebt",
            ], col)
            total_assets = _extract_row(balance_sheet, ["Total Assets", "TotalAssets"], col)
            total_equity = _extract_row(balance_sheet, [
                "Stockholders Equity", "StockholdersEquity",
                "Total Equity Gross Minority Interest",
            ], col)
            cash = _extract_row(balance_sheet, [
                "Cash And Cash Equivalents", "CashAndCashEquivalents",
                "Cash Cash Equivalents And Short Term Investments",
            ], col)

            # Derive total debt if not directly available
            if total_debt is None and long_term_debt is not None:
                total_debt = long_term_debt + (short_term_debt or 0)

            net_debt = (total_debt or 0) - (cash or 0)

            # Find matching year interest expense from revenue_trend
            interest_exp = None
            for rt in revenue_trend:
                if rt["year"] == year:
                    interest_exp = rt.get("interest_expense")
                    break

            # Find matching EBITDA
            ebitda_val = None
            for rt in revenue_trend:
                if rt["year"] == year:
                    ebitda_val = rt.get("ebitda")
                    break

            # Ratios
            debt_to_assets = _pct(total_debt, total_assets) if total_debt and total_assets else None
            debt_to_equity = round(total_debt / total_equity, 2) if total_debt and total_equity and total_equity != 0 else None
            debt_to_ebitda = round(total_debt / ebitda_val, 2) if total_debt and ebitda_val and ebitda_val > 0 else None
            interest_coverage = round(ebitda_val / abs(interest_exp), 2) if ebitda_val and interest_exp and interest_exp != 0 else None

            debt_data.append({
                "year": year,
                "total_debt": total_debt,
                "long_term_debt": long_term_debt,
                "short_term_debt": short_term_debt,
                "cash": cash,
                "net_debt": round(net_debt, 0),
                "total_assets": total_assets,
                "total_equity": total_equity,
                "debt_to_assets_pct": debt_to_assets,
                "debt_to_equity": debt_to_equity,
                "debt_to_ebitda": debt_to_ebitda,
                "interest_coverage": interest_coverage,
                "interest_expense": interest_exp,
            })

    result["debt_data"] = debt_data

    # ================================================================
    # 5. Liquidity Metrics
    # ================================================================
    liquidity: dict | None = None
    if balance_sheet is not None and not balance_sheet.empty:
        latest = balance_sheet.columns[0]
        year = str(latest.year) if hasattr(latest, "year") else str(latest)

        current_assets = _extract_row(balance_sheet, ["Current Assets", "CurrentAssets"], latest)
        current_liabilities = _extract_row(balance_sheet, [
            "Current Liabilities", "CurrentLiabilities",
        ], latest)
        inventory = _extract_row(balance_sheet, ["Inventory", "Inventories"], latest)
        cash_val = _extract_row(balance_sheet, [
            "Cash And Cash Equivalents", "CashAndCashEquivalents",
        ], latest)
        short_term_inv = _extract_row(balance_sheet, [
            "Other Short Term Investments", "OtherShortTermInvestments",
            "Available For Sale Securities", "AvailableForSaleSecurities",
        ], latest)
        receivables = _extract_row(balance_sheet, [
            "Net Receivables", "NetReceivables",
            "Accounts Receivable", "AccountsReceivable",
        ], latest)

        current_ratio = round(current_assets / current_liabilities, 2) if current_assets and current_liabilities and current_liabilities != 0 else None
        quick_ratio = None
        if current_assets is not None and current_liabilities is not None and current_liabilities != 0:
            quick_assets = current_assets - (inventory or 0)
            quick_ratio = round(quick_assets / current_liabilities, 2)

        cash_ratio = round((cash_val or 0) / current_liabilities, 2) if current_liabilities and current_liabilities != 0 else None

        liquidity = {
            "year": year,
            "current_assets": current_assets,
            "current_liabilities": current_liabilities,
            "inventory": inventory,
            "cash": cash_val,
            "receivables": receivables,
            "current_ratio": current_ratio,
            "quick_ratio": quick_ratio,
            "cash_ratio": cash_ratio,
        }

    result["liquidity"] = liquidity

    # ================================================================
    # 5.5 Universal Dividend Metrics
    # ================================================================
    universal_dividend_metrics = None
    div_yield = info.get("dividendYield")
    
    # Calculate 3-year trailing annual payouts
    trailing_payouts = []
    try:
        divs = stock.dividends
        if divs is not None and not divs.empty:
            yearly_divs = divs.groupby(divs.index.year).sum()
            current_year = datetime.datetime.now().year
            for y in range(current_year - 3, current_year):
                val = yearly_divs.get(y, 0.0)
                if val > 0:
                    trailing_payouts.append({"year": y, "total_payout": round(float(val), 2)})
    except Exception:
        pass

    if div_yield is not None or trailing_payouts:
        payout_ratio = info.get("payoutRatio")
        
        calculated_yield = 0.0
        current_price = info.get("currentPrice", info.get("regularMarketPrice"))
        div_rate = info.get("dividendRate")
        
        if div_rate and current_price and current_price > 0:
             calculated_yield = round((div_rate / current_price) * 100, 2)
        elif div_yield is not None:
             calculated_yield = round(div_yield * 100, 2) if div_yield < 1 else round(div_yield, 2)

        universal_dividend_metrics = {
            "dividend_yield": calculated_yield,
            "payout_ratio": round(payout_ratio * 100, 2) if payout_ratio else None,
            "trailing_payouts": trailing_payouts
        }
    result["universal_dividend_metrics"] = universal_dividend_metrics

    # ================================================================
    # 5.6 Industry-Specific Metrics
    # ================================================================
    industry_metrics = None
    sector = info.get("sector", "")
    industry = info.get("industry", "")
    
    latest_ni = eps_history[0].get("net_income") if eps_history else None
    latest_rev = revenue_trend[-1].get("revenue") if revenue_trend else None
    latest_cogs = None
    if income_stmt is not None and not income_stmt.empty:
         latest_col = income_stmt.columns[0]
         latest_cogs = _extract_row(income_stmt, ["Cost Of Revenue", "CostOfRevenue", "Cost Of Goods Sold"], latest_col)
         latest_rnd = _extract_row(income_stmt, ["Research And Development", "ResearchAndDevelopment"], latest_col)
         latest_nii = _extract_row(income_stmt, ["Net Interest Income", "NetInterestIncome"], latest_col)

    latest_cf = cf_trend[-1] if cf_trend else {}
    latest_da = None
    if cashflow is not None and not cashflow.empty:
         latest_da = _extract_row(cashflow, ["Depreciation And Amortization", "Depreciation Amortization Depletion"], cashflow.columns[0])
    
    latest_bs = debt_data[-1] if debt_data else {}
    latest_assets = latest_bs.get("total_assets")
    
    if sector == "Real Estate":
        # REITs
        ffo_history = []
        p_ffo_history = []
        ffo_payout_history = []
        
        price = info.get("currentPrice", info.get("regularMarketPrice", 0))
        shares = info.get("sharesOutstanding", 0)
        market_cap = price * shares if price and shares else None
        
        for rt in eps_history:
            y = rt.get("year")
            ni = rt.get("net_income")
            # find matching da
            da = None
            if cashflow is not None and not cashflow.empty:
                for col in cashflow.columns:
                    if str(col.year) == y or str(col) == y:
                        da = _extract_row(cashflow, ["Depreciation And Amortization", "Depreciation Amortization Depletion"], col)
                        break
            
            if ni is not None and da is not None:
                ffo = ni + da
                ffo_history.append({"year": y, "value": f"${ffo/1e9:.2f}B"})
                if market_cap:
                    p_ffo_history.append({"year": y, "value": f"{round(market_cap/ffo, 2)}x" if ffo > 0 else "N/A"})
                
                div_paid = None
                if cashflow is not None and not cashflow.empty:
                    for col in cashflow.columns:
                        if str(col.year) == y or str(col) == y:
                            div_paid = abs(_extract_row(cashflow, ["Cash Dividends Paid", "Common Stock Dividend Paid"], col) or 0)
                            break
                if div_paid and ffo > 0:
                    ffo_payout_history.append({"year": y, "value": f"{round((div_paid/ffo)*100, 2)}%"})

        industry_metrics = {
            "type": "REIT",
            "metrics": [
                {"name": "Estimated FFO", "value": ffo_history[-1]["value"] if ffo_history else "N/A", "history": ffo_history, "desc": "Funds From Operations (NI + D&A)", "target": "Consistent Y/Y Growth"},
                {"name": "Price / FFO", "value": p_ffo_history[-1]["value"] if p_ffo_history else "N/A", "history": p_ffo_history, "desc": "Valuation relative to FFO", "target": "12x - 18x"},
                {"name": "FFO Payout Ratio", "value": ffo_payout_history[-1]["value"] if ffo_payout_history else "N/A", "history": ffo_payout_history, "desc": "Dividends normalized against FFO", "target": "65% - 85%"}
            ]
        }
    elif industry == "Oil & Gas Midstream":
        # MLPs
        dcf_history = []
        coverage_history = []
        
        for cft in cf_trend:
            y = cft.get("year")
            capex = cft.get("capex", 0) or 0
            
            # Find matching NI & DA
            ni = next((item.get("net_income") for item in eps_history if item["year"] == y), None)
            da = None
            if cashflow is not None and not cashflow.empty:
                for col in cashflow.columns:
                    if str(col.year) == y or str(col) == y:
                        da = _extract_row(cashflow, ["Depreciation And Amortization", "Depreciation Amortization Depletion"], col)
                        break
                        
            if ni is not None and da is not None:
                 dcf = ni + da + capex
                 dcf_history.append({"year": y, "value": f"${dcf/1e9:.2f}B"})
                 
                 div_paid = None
                 if cashflow is not None and not cashflow.empty:
                     for col in cashflow.columns:
                         if str(col.year) == y or str(col) == y:
                             div_paid = abs(_extract_row(cashflow, ["Cash Dividends Paid", "Common Stock Dividend Paid"], col) or 0)
                             break
                 if div_paid and div_paid > 0:
                     coverage_history.append({"year": y, "value": f"{round(dcf/div_paid, 2)}x"})
             
        industry_metrics = {
            "type": "MLP / Midstream",
            "metrics": [
                {"name": "Distributable Cash Flow (DCF)", "value": dcf_history[-1]["value"] if dcf_history else "N/A", "history": dcf_history, "desc": "NI + D&A - Maintenance CapEx", "target": "Consistent Distribution Growth"},
                {"name": "Distribution Coverage", "value": coverage_history[-1]["value"] if coverage_history else "N/A", "history": coverage_history, "desc": "DCF / Distributions Paid", "target": "> 1.2x (Safe)"}
            ]
        }
    elif sector == "Financial Services" and "Banks" in industry:
        # Banks
        nim_history = []
        equity_asset_history = []
        
        if income_stmt is not None and balance_sheet is not None:
             cols = list(reversed(income_stmt.columns[:5]))
             for col in cols:
                 y = str(col.year) if hasattr(col, "year") else str(col)
                 nii = _extract_row(income_stmt, ["Net Interest Income", "NetInterestIncome"], col)
                 # find matching assets
                 assets = None
                 equity = None
                 for bcol in balance_sheet.columns:
                     if str(bcol.year) == y or str(bcol) == y:
                         assets = _extract_row(balance_sheet, ["Total Assets", "TotalAssets"], bcol)
                         equity = _extract_row(balance_sheet, ["Stockholders Equity", "StockholdersEquity", "Total Equity Gross Minority Interest"], bcol)
                         break
                         
                 if nii and assets:
                      nim_history.append({"year": y, "value": f"{round((nii/assets)*100, 2)}%"})
                 if equity and assets:
                      equity_asset_history.append({"year": y, "value": f"{round((equity/assets)*100, 2)}%"})

        industry_metrics = {
            "type": "Banking",
            "metrics": [
                {"name": "Net Interest Margin (Proxy)", "value": nim_history[-1]["value"] if nim_history else "N/A", "history": nim_history, "desc": "Net Interest Income / Total Assets", "target": "2.5% - 3.5%"},
                {"name": "Equity-to-Asset Ratio", "value": equity_asset_history[-1]["value"] if equity_asset_history else "N/A", "history": equity_asset_history, "desc": "Core capital safety threshold", "target": "8% - 12%"}
            ]
        }
    elif sector == "Technology" and "Software" in industry:
        # SaaS
        rule40_history = []
        rnd_history = []
        
        for i, rt in enumerate(revenue_trend):
             y = rt.get("year")
             rev = rt.get("revenue")
             # find growth
             g = 0
             if i > 0 and rev and revenue_trend[i-1].get("revenue"):
                 prev_rev = revenue_trend[i-1].get("revenue")
                 g = ((rev - prev_rev) / abs(prev_rev)) * 100
             # find fcf
             fcf = next((c.get("free_cash_flow") for c in cf_trend if c["year"] == y), 0) or 0
             if rev and rev > 0:
                  fcf_m = (fcf / rev) * 100
                  rule40_history.append({"year": y, "value": f"{round(g + fcf_m, 1)}%"})
                  
                  rnd = None
                  if income_stmt is not None:
                      for col in income_stmt.columns:
                           if str(col.year) == y or str(col) == y:
                                rnd = _extract_row(income_stmt, ["Research And Development", "ResearchAndDevelopment"], col)
                                break
                  if rnd:
                       rnd_history.append({"year": y, "value": f"{round((rnd/rev)*100, 1)}%"})

        industry_metrics = {
            "type": "SaaS / Software",
            "metrics": [
                {"name": "Rule of 40 Score", "value": rule40_history[-1]["value"] if rule40_history else "N/A", "history": rule40_history, "desc": "Rev Growth + FCF Margin", "target": "> 40%"},
                {"name": "R&D Intensity", "value": rnd_history[-1]["value"] if rnd_history else "N/A", "history": rnd_history, "desc": "R&D Spend / Total Revenue", "target": "15% - 25%"}
            ]
        }
    elif sector == "Healthcare" or "Biotech" in industry:
        # Pharma/Biotech
        rnd_history = []
        efficiency_history = []
        
        for rt in revenue_trend:
             y = rt.get("year")
             rev = rt.get("revenue")
             gm = rt.get("gross_margin")
             om = rt.get("operating_margin")
             
             if gm and om:
                 efficiency_history.append({"year": y, "value": f"{round(gm - om, 1)}% dt"})
                 
             if rev and rev > 0:
                  rnd = None
                  if income_stmt is not None:
                      for col in income_stmt.columns:
                           if str(col.year) == y or str(col) == y:
                                rnd = _extract_row(income_stmt, ["Research And Development", "ResearchAndDevelopment"], col)
                                break
                  if rnd:
                       rnd_history.append({"year": y, "value": f"{round((rnd/rev)*100, 1)}%"})

        # Static patent cliff dictionary for major pharma
        patent_cliffs = {
            "PFE": "Eliquis (2026), Ibrance (2027), Prevnar 13 (2026)",
            "ABBV": "Humira (Biosimilars Active), Skyrizi (2033)",
            "MRK": "Keytruda (2028), Januvia (2026)",
            "BMY": "Eliquis (2026), Opdivo (2028), Revlimid (Active)",
            "LLY": "Trulicity (2027), Verzenio (2029)",
            "JNJ": "Stelara (2025), Darzalex (2029)"
        }
        cliff_data = patent_cliffs.get(ticker.upper(), "Not tracked (Requires Premium Data)")

        industry_metrics = {
            "type": "Pharma / Biotech",
            "metrics": [
                {"name": "R&D Intensity", "value": rnd_history[-1]["value"] if rnd_history else "N/A", "history": rnd_history, "desc": "Pipeline reinvestment rate (R&D/Rev)", "target": "15% - 25%"},
                {"name": "Patent Expiry / Exclusivity Loss", "value": cliff_data, "history": [], "desc": "Tracking upcoming revenue cliffs from generic competition", "target": "Monitoring 5yr+ Horizon"},
                {"name": "Commercialization Efficiency", "value": efficiency_history[-1]["value"] if efficiency_history else "N/A", "history": efficiency_history, "desc": "Delta between Gross and Operating margins", "target": "Minimizing Delta"}
            ]
        }
    elif sector == "Utilities":
        # Utilities
        capex_da_history = []
        safe_score_history = []
        for cft, cflow_col in zip(cf_trend, reversed(cashflow.columns[:5]) if cashflow is not None else []):
            y = cft.get("year")
            cx = abs(cft.get("capex", 0) or 0)
            da = _extract_row(cashflow, ["Depreciation And Amortization", "Depreciation Amortization Depletion"], cflow_col)
            div = abs(_extract_row(cashflow, ["Cash Dividends Paid"], cflow_col) or 0)
            fcf = cft.get("free_cash_flow", 0) or 0
            
            if da and da > 0:
                 capex_da_history.append({"year": y, "value": f"{round(cx/da, 2)}x"})
            if fcf > 0 and div:
                 safe_score_history.append({"year": y, "value": f"{round(div/fcf, 2)}x (Payout)"})

        industry_metrics = {
            "type": "Utilities / Infrastructure",
            "metrics": [
                {"name": "CapEx to D&A Ratio", "value": capex_da_history[-1]["value"] if capex_da_history else "N/A", "history": capex_da_history, "desc": "Grid maintenance and expansion indicator", "target": "1.0x - 1.5x"},
                {"name": "FCF Dividend Safety", "value": safe_score_history[-1]["value"] if safe_score_history else "N/A", "history": safe_score_history, "desc": "Dividends Paid / FCF", "target": "< 0.8x Payout"}
            ]
        }
    elif sector == "Consumer Defensive" or "Retail" in industry:
        # Consumer Staples / Retail
        inv_turnover_history = []
        if balance_sheet is not None and income_stmt is not None:
            # align years roughly
            for i, col in enumerate(reversed(balance_sheet.columns[:5])):
                y = str(col.year) if hasattr(col, "year") else str(col)
                inv = _extract_row(balance_sheet, ["Inventory", "Inventories"], col)
                if i < len(income_stmt.columns):
                     icols = list(reversed(income_stmt.columns[:5]))
                     if i < len(icols):
                          cogs = _extract_row(income_stmt, ["Cost Of Revenue", "CostOfRevenue", "Cost Of Goods Sold"], icols[i])
                          if cogs and inv and inv > 0:
                               inv_turnover_history.append({"year": y, "value": f"{round(cogs/inv, 2)}x"})

        industry_metrics = {
            "type": "Consumer Staples / Retail",
            "metrics": [
                {"name": "Inventory Turnover", "value": inv_turnover_history[-1]["value"] if inv_turnover_history else "N/A", "history": inv_turnover_history, "desc": "COGS / Average Inventory (Supply Chain Velocity)", "target": "> 5.0x (Higher is better)"}
            ]
        }
    elif sector == "Energy":
        # E&P (Oil & Gas)
        capex_da_history = []
        cf_debt_history = []
        
        for i, cft in enumerate(cf_trend):
            y = cft.get("year")
            cx = abs(cft.get("capex", 0) or 0)
            da = None
            if cashflow is not None and i < len(cashflow.columns):
                 da = _extract_row(cashflow, ["Depreciation And Amortization", "Depreciation Amortization Depletion"], list(reversed(cashflow.columns[:4]))[i])
            if da and da > 0:
                 capex_da_history.append({"year": y, "value": f"{round(cx/da, 2)}x"})
                 
            op_cf = cft.get("operating_cf", 0) or 0
            if i < len(debt_data):
                dt = debt_data[i].get("total_debt", 0) or 0
                if dt > 0:
                     cf_debt_history.append({"year": y, "value": f"{round((op_cf/dt)*100, 2)}%"})

        industry_metrics = {
            "type": "Energy E&P",
            "metrics": [
                {"name": "CapEx to D&A Ratio", "value": capex_da_history[-1]["value"] if capex_da_history else "N/A", "history": capex_da_history, "desc": "Is CapEx replenishing depleting wells?", "target": "1.0x - 1.3x"},
                {"name": "OpCF / Total Debt", "value": cf_debt_history[-1]["value"] if cf_debt_history else "N/A", "history": cf_debt_history, "desc": "Cash coverage against cyclical debt", "target": "> 40%"}
            ]
        }
        
    result["industry_metrics"] = industry_metrics

    # ================================================================
    # 6. Quick-reference from info dict
    # ================================================================
    result["quick_stats"] = {
        "return_on_equity": _pct_from_info(info, "returnOnEquity"),
        "return_on_assets": _pct_from_info(info, "returnOnAssets"),
        "profit_margin": _pct_from_info(info, "profitMargins"),
        "operating_margin": _pct_from_info(info, "operatingMargins"),
        "gross_margin": _pct_from_info(info, "grossMargins"),
        "revenue_per_share": _safe_float(info.get("revenuePerShare")),
        "book_value": _safe_float(info.get("bookValue")),
        "price_to_book": _safe_float(info.get("priceToBook")),
        "dividend_yield": round(info.get("dividendYield", 0) * 100, 2) if info.get("dividendYield") else 0,
        "payout_ratio": round(info.get("payoutRatio", 0) * 100, 1) if info.get("payoutRatio") else None,
        "trailing_pe": _safe_float(info.get("trailingPE")),
        "forward_pe": _safe_float(info.get("forwardPE")),
        "peg_ratio": _safe_float(info.get("pegRatio")),
        "ev_to_ebitda": _safe_float(info.get("enterpriseToEbitda")),
        "ev_to_revenue": _safe_float(info.get("enterpriseToRevenue")),
    }

    # ================================================================
    # 6.5 Trailing-Twelve-Months snapshot + Returns on Capital
    # ================================================================
    result["ttm"] = _compute_ttm(stock)
    result["returns_on_capital"] = _compute_returns_on_capital(income_stmt, balance_sheet, stock)

    # ================================================================
    # 7. Overall Health Score (simple heuristic)
    # ================================================================
    score_parts: list[tuple[str, float, str]] = []

    # Margins trending positive?
    if len(revenue_trend) >= 2:
        latest_nm = revenue_trend[-1].get("net_margin")
        prev_nm = revenue_trend[-2].get("net_margin")
        if latest_nm is not None and prev_nm is not None:
            if latest_nm > prev_nm:
                score_parts.append(("Margin Trend", 1.0, "Improving"))
            elif latest_nm > 0:
                score_parts.append(("Margin Trend", 0.6, "Positive but declining"))
            else:
                score_parts.append(("Margin Trend", 0.2, "Negative margins"))

    # Current ratio
    if liquidity and liquidity.get("current_ratio") is not None:
        cr = liquidity["current_ratio"]
        if cr >= 2.0:
            score_parts.append(("Liquidity", 1.0, f"Strong (CR: {cr})"))
        elif cr >= 1.5:
            score_parts.append(("Liquidity", 0.8, f"Good (CR: {cr})"))
        elif cr >= 1.0:
            score_parts.append(("Liquidity", 0.5, f"Adequate (CR: {cr})"))
        else:
            score_parts.append(("Liquidity", 0.2, f"Weak (CR: {cr})"))

    # Debt-to-EBITDA
    if debt_data:
        latest_dte = debt_data[-1].get("debt_to_ebitda")
        if latest_dte is not None:
            if latest_dte < 1:
                score_parts.append(("Leverage", 1.0, f"Low debt (D/EBITDA: {latest_dte})"))
            elif latest_dte < 3:
                score_parts.append(("Leverage", 0.7, f"Moderate (D/EBITDA: {latest_dte})"))
            elif latest_dte < 5:
                score_parts.append(("Leverage", 0.4, f"High (D/EBITDA: {latest_dte})"))
            else:
                score_parts.append(("Leverage", 0.1, f"Very high (D/EBITDA: {latest_dte})"))

    # FCF positive
    if fcf_vals:
        latest_fcf = fcf_vals[-1]
        if latest_fcf > 0:
            score_parts.append(("Cash Flow", 1.0, "Positive FCF"))
        else:
            score_parts.append(("Cash Flow", 0.2, "Negative FCF"))

    # Earnings consistency
    if result["eps_total_years"] > 0:
        consistency = result["eps_positive_years"] / result["eps_total_years"]
        score_parts.append(("Earnings", consistency, f"{result['eps_positive_years']}/{result['eps_total_years']} years profitable"))

    # Interest coverage
    if debt_data:
        ic = debt_data[-1].get("interest_coverage")
        if ic is not None:
            if ic > 10:
                score_parts.append(("Interest Coverage", 1.0, f"Strong ({ic}x)"))
            elif ic > 5:
                score_parts.append(("Interest Coverage", 0.8, f"Good ({ic}x)"))
            elif ic > 2:
                score_parts.append(("Interest Coverage", 0.5, f"Adequate ({ic}x)"))
            else:
                score_parts.append(("Interest Coverage", 0.2, f"Weak ({ic}x)"))

    avg_score = round(float(np.mean([s[1] for s in score_parts])) * 100, 0) if score_parts else None
    result["health_score"] = {
        "overall": avg_score,
        "components": [
            {"name": name, "score": round(score * 100, 0), "detail": detail}
            for name, score, detail in score_parts
        ],
    }

    return result


def _pct_from_info(info: dict, key: str) -> float | None:
    """Extract a ratio from yfinance info and convert to percentage."""
    val = info.get(key)
    if val is None:
        return None
    try:
        return round(float(val) * 100, 2)
    except (TypeError, ValueError):
        return None


async def run_financial_health(ticker: str) -> dict:
    """Async wrapper."""
    return await asyncio.to_thread(_run_sync, ticker)
