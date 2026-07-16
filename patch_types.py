import re

with open("frontend/src/types.ts", "r") as f:
    content = f.read()

# Replace ExitPortfolioData
portfolio_data = '''export interface ExitPortfolioData {
  position_pct_of_portfolio: number | null;
  concentration_risk: string;
  tax_type: string;
  holding_period_days: number;
  days_until_long_term: number | null;
  unrealized_gain_loss: number;
  unrealized_gain_loss_pct: number;
  tax_impact_if_sold: number;
  estimated_tax_rate: number;
  benchmark_return_1y: number | null;
  stock_return_1y: number | null;
  opportunity_cost: number | null;
  better_than_benchmark: boolean | null;
}'''

sentiment_data = '''export interface ExitSentimentData {
  short_pct_of_float: number | null;
  short_ratio: number | null;
  insider_ownership_pct: number | null;
  institutional_ownership_pct: number | null;
  analyst_consensus: string | null;
  target_upside_pct: number | null;
  sentiment_label: string;
}'''

content = content.replace(portfolio_data, sentiment_data)

# Replace in ExitAnalysisData pillars
content = content.replace(
    'portfolio: { score: number; data: ExitPortfolioData };',
    'sentiment: { score: number; data: ExitSentimentData };'
)

with open("frontend/src/types.ts", "w") as f:
    f.write(content)
