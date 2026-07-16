import re

with open("backend/app/services/exit_analysis_service.py", "r") as f:
    content = f.read()

# Remove Position P&L and holding days block
content = re.sub(
    r'    # Position P&L.*?    position = \{.*?\n    \}',
    '',
    content,
    flags=re.DOTALL
)

# Remove `purchase_date` from _compute_price_chart call
content = content.replace(
    'price_chart = _compute_price_chart(stock, info, purchase_date, hist_1y, current_price)',
    'price_chart = _compute_price_chart(stock, info, "", hist_1y, current_price)'
)

# Remove `position` from the return dict
content = content.replace(
    '        "position": position,\n',
    ''
)

# Remove `p6 = _compute_portfolio...` which I missed earlier? Let's check if it's there.
content = re.sub(
    r'    p6 = _compute_portfolio\(current_price, shares, cost_basis, purchase_date, hist_1y,[\s]*total_portfolio_value=total_portfolio_value\)',
    '    p6 = _compute_sentiment(info, current_price)',
    content
)

with open("backend/app/services/exit_analysis_service.py", "w") as f:
    f.write(content)
