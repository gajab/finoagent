import re

with open("backend/app/services/exit_analysis_service.py", "r") as f:
    content = f.read()

# 1. Replace _compute_portfolio with _compute_sentiment
portfolio_regex = re.compile(r'def _compute_portfolio\(.*?\).*?return \{"score": _clamp\(score\), "data": data\}', re.DOTALL)

sentiment_code = '''def _compute_sentiment(info: dict, current_price: float) -> dict:
    """Pillar 6: Market Sentiment & Flow.
    Evaluates short interest, insider activity, and analyst consensus."""
    score = 25

    # Short interest
    short_pct = info.get("shortPercentOfFloat")
    if short_pct is not None:
        short_pct = short_pct * 100  # Convert to percentage if it's a decimal
        if short_pct > 20:
            score += 25
        elif short_pct > 10:
            score += 15
        elif short_pct > 5:
            score += 5
        elif short_pct < 2:
            score -= 5

    short_ratio = info.get("shortRatio")  # Days to cover
    if short_ratio is not None:
        if short_ratio > 10:
            score += 15
        elif short_ratio > 5:
            score += 8

    # Insider & Institutional
    insider_pct = info.get("heldPercentInsiders")
    if insider_pct is not None:
        insider_pct = insider_pct * 100
        if insider_pct < 1:
            score += 5
        elif insider_pct > 10:
            score -= 5

    inst_pct = info.get("heldPercentInstitutions")
    if inst_pct is not None:
        inst_pct = inst_pct * 100
        if inst_pct < 20:
            score += 10
        elif inst_pct > 80:
            score -= 5

    # Analyst consensus
    consensus = info.get("recommendationKey", "none").lower()
    if consensus in ["strong_sell", "sell"]:
        score += 25
    elif consensus == "underperform":
        score += 15
    elif consensus in ["hold", "none"]:
        score += 5
    elif consensus in ["buy", "strong_buy"]:
        score -= 5

    # Target upside
    target = info.get("targetMeanPrice")
    upside = None
    if target and current_price and current_price > 0:
        upside = ((target - current_price) / current_price) * 100
        if upside < -10:
            score += 20
        elif upside < 0:
            score += 10
        elif upside > 20:
            score -= 10

    if score >= 60:
        sentiment_label = "Highly Bearish"
    elif score >= 40:
        sentiment_label = "Bearish"
    elif score >= 25:
        sentiment_label = "Neutral"
    elif score >= 15:
        sentiment_label = "Bullish"
    else:
        sentiment_label = "Highly Bullish"

    data = {
        "short_pct_of_float": short_pct,
        "short_ratio": short_ratio,
        "insider_ownership_pct": insider_pct,
        "institutional_ownership_pct": inst_pct,
        "analyst_consensus": consensus.replace("_", " ").title(),
        "target_upside_pct": upside,
        "sentiment_label": sentiment_label,
    }
    return {"score": _clamp(score), "data": data}'''

content = portfolio_regex.sub(sentiment_code, content)

# 2. Modify signatures for _compute_options_protection, _compute_risk, _compute_liquidity
content = content.replace(
    'def _compute_options_protection(stock, current_price: float, shares: float) -> dict:',
    'def _compute_options_protection(stock, current_price: float, shares: float = 100) -> dict:'
)
content = content.replace(
    'def _compute_risk(info: dict, hist_1y: pd.DataFrame, current_price: float, shares: float) -> dict:',
    'def _compute_risk(info: dict, hist_1y: pd.DataFrame, current_price: float, shares: float = 100) -> dict:'
)
content = content.replace(
    'def _compute_liquidity(hist_1y: pd.DataFrame, current_price: float, shares: float) -> dict:',
    'def _compute_liquidity(hist_1y: pd.DataFrame, current_price: float, shares: float = 100) -> dict:'
)

# 3. Modify _run_exit_analysis_sync
content = content.replace(
    'def _run_exit_analysis_sync(\n    ticker: str, shares: float, cost_basis: float, purchase_date_str: str,\n    total_portfolio_value: float | None = None,\n) -> dict:',
    'def _run_exit_analysis_sync(\n    ticker: str\n) -> dict:'
)
content = content.replace(
    '    if purchase_date_str:\n        try:\n            pd_date = datetime.strptime(purchase_date_str, "%Y-%m-%d").date()\n            holding_days = (datetime.now().date() - pd_date).days\n        except:\n            holding_days = 0\n    else:\n        holding_days = 0',
    ''
)
content = content.replace(
    'p6 = _compute_portfolio(current_price, shares, cost_basis, purchase_date_str, hist_1y, total_portfolio_value)',
    'p6 = _compute_sentiment(info, current_price)'
)

# remove "portfolio": p6,
content = content.replace('"portfolio": p6,', '"sentiment": p6,')
# _compute_options_protection
content = content.replace('_compute_options_protection(stock, current_price, shares)', '_compute_options_protection(stock, current_price, 100)')
# _compute_risk
content = content.replace('_compute_risk(info, hist_1y, current_price, shares)', '_compute_risk(info, hist_1y, current_price, 100)')
# _compute_liquidity
content = content.replace('_compute_liquidity(hist_1y, current_price, shares)', '_compute_liquidity(hist_1y, current_price, 100)')

# _compute_price_chart
content = content.replace('def _compute_price_chart(stock, info: dict, purchase_date: str, hist_1y: pd.DataFrame,\n                         current_price: float) -> dict:', 'def _compute_price_chart(stock, info: dict, purchase_date: str, hist_1y: pd.DataFrame,\n                         current_price: float) -> dict:')
content = content.replace('_compute_price_chart(stock, info, purchase_date_str, hist_1y, current_price)', '_compute_price_chart(stock, info, "", hist_1y, current_price)')

# run_exit_analysis wrapper
content = content.replace(
    'async def run_exit_analysis(\n    ticker: str, shares: float, cost_basis: float, purchase_date: str,\n    total_portfolio_value: float | None = None,\n) -> dict:',
    'async def run_exit_analysis(\n    ticker: str\n) -> dict:'
)
content = content.replace(
    '        _run_exit_analysis_sync, ticker, shares, cost_basis, purchase_date,\n        total_portfolio_value,',
    '        _run_exit_analysis_sync, ticker'
)

# In ExitPosition dict
content = content.replace(
    '        "shares": shares,\n        "cost_basis": cost_basis,\n        "purchase_date": purchase_date_str,',
    '        "shares": 100,\n        "cost_basis": current_price,\n        "purchase_date": "",'
)

with open("backend/app/services/exit_analysis_service.py", "w") as f:
    f.write(content)
