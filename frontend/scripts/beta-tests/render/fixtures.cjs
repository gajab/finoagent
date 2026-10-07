// Payload shapes mirror what the live panels returned for the DRAM covered call (values read off the real screens).
const lens = (o) => o;

// ── Evidence data (Technical · Fundamental · News & events). Shapes and magnitudes copied from a live /trade-manager read of AMD.
const evidenceData = () => ({
  trader_lenses: [
    { key: 'minervini', trader: 'Mark Minervini', philosophy: 'Trade only Stage-2 leaders; sell when the 50d fails.', rules: [{ id: 'px', rule: 'Price > 150d & 200d SMA', met: true, value: '61.67 vs 50.1/44.2' }, { id: 'vcp', rule: 'Tight base', met: false, value: null }, { id: 'rs', rule: 'RS rank', met: null, value: '' }], met: 8, total: 9, exit_level: 55.35, exit_rule: 'Close below the 50d SMA on expanding volume', stance: 'bullish', d: 0.75 },
    { key: 'livermore', trader: 'Jesse Livermore', philosophy: 'Follow the line of least resistance.', rules: [{ id: 'hi', rule: 'Breaks a pivot high', met: false, value: 'no' }], met: 1, total: 4, exit_level: null, exit_rule: 'Exit on a failed pivot', stance: 'bearish', d: -0.4 },
  ],
  technical: {
    consensus: { bull: 11, neutral: 3, bear: 1, n: 15, d: 0.618 },
    suite: {
      price: 61.67, rsi14: 72.2, rsi5: 82, rsi2: 94, mfi14: 78.3, rvol: 1.08, cmf20: 0.327, up_down_volume_ratio_20d: 3.06, accumulation_days_25d: 12, distribution_days_25d: 3,
      atr14: 2.32, atr_pct: 3.69, atr_percentile_1y: 67, tsmom_12_1_pct: 190,
      adx: { adx: 26.9, plus_di: 40.5, minus_di: 18.3 }, macd: { hist: 3.564, hist_slope_3d: -1.243, line: 36.489, signal_line: 32.926 }, stoch: { percent_k: 91.8, percent_d: 89.3 },
      bollinger: { pct_b: 0.82, lower: 47.5, upper: 68.7, bandwidth_pct: 36.5, bandwidth_percentile_1y: 74 },
      pct_vs_sma: { '50': 25.17, '150': 50.09, '200': 71.31 }, pct_vs_ema: { '21': 10.9 },
      supertrend: { direction: 'up', line: 55.35, flipped_recently: false }, ichimoku: { price_vs_cloud: 'above', cloud_bottom: 47.9, cloud_top: 50.4, tk_bullish: true, chikou_above_price: true },
      weekly: { stage: 2, weekly_rsi14: 72.4, macd_hist_rising: true, price_above_sma30: true },
      range: { hi_252: 65.85, lo_252: 18.82, pct_from_52w_high: -1.38 }, roc: { '21': 36, '63': 25.5, '126': 193.2, '252': 294.4 },
      realized_vol: { '10': 32.1, '21': 52.3, '63': 63.4 }, vol_forecast: { blend_ann_pct: 59, regime: 'stable' }, squeeze_on: false, squeeze_momentum: { rising: false, value: 7.4 },
      chandelier: { long_stop: 58.36, long_stop_wide: 53.37 },
      relative_strength: { excess_return_pct: { '63': 20.7 }, rs_line_vs_50d: 'above', rs_line_new_high_90d: true },
      rsi_divergence: null, obv_divergence: null, macd_divergence: null,
    },
    structure_context: {
      bias: { direction: 'bullish', strength: 'strong', rationale: 'Market structure is bullish; daily trend up.' },
      regime: { label: 'trending', hurst: 0.589, note: 'Trending regime — favor momentum.' },
      trend_alignment: { daily: 'up', h4: 'up', h1: 'down' },
      dealer: { flip: 55.84, gamma: 'long', note: 'Dealers LONG gamma → volatility suppressed.' },
      expected_move: { iv: 54.5, lower: 54.8, upper: 75.1, pct_30d: 15.6 },
    },
    zones: [
      { center: 51.84, low: 51.5, high: 52.3, kind: 'support', distance_pct: -15.9, n_sources: 5, score: 10.6, sources: [{ label: 'Volume POC', price: 50.57 }, { label: 'Bull FVG', price: 52.1 }] },
      { center: 70, low: 69.5, high: 70.4, kind: 'resistance', distance_pct: 13.5, n_sources: 2, score: 4, sources: [{ label: 'Dealer call resistance', price: 70 }] },
    ],
    patterns: [{ name: 'Bull flag', direction: 'bullish', status: 'forming', confidence: 70, breakout: { level: 66 }, target: { price: 72, pct: 16.7 }, stop: 58 }],
    volume: {
      metrics: { rvol: 1.08, rvol_multi: { '10': 1.25, '20': 1.08, '50': 1.07 }, rvol_basis: 'the prior 20 bars', up_volume_pct: 71, obv_trend: 'flat', cvd_trend: 'flat', price_trend: 'flat', divergence: 'aligned', dryup_state: 'drying up', dryup_ratio: 0.78, volume_trend: 'rising' },
      climax_bars: [{ t: '2026-09-21', direction: 'up', ratio: 2.3, price: 61.55 }],
      read: ['RVOL 10/20/50 = 1.25× / 1.08× / 1.07×.', 'Volume is drying up on the pullback.'],
    },
    regime_edge: {
      read: ['Current regime: TRENDING (Hurst 0.575).', '⚡ 20-Day Breakout is FIRING right now — but its edge here is weak — size down.'],
      signals: [
        { key: 'breakout', label: '20-Day Breakout', thesis: 'Momentum — close breaks the prior 20-day high.', firing_now: true, current_edge: 'weak', current: { n: 14, win_rate: 36, expectancy: 0.07, low_confidence: false }, overall: { n: 36 } },
        { key: 'dip', label: 'Mean-Reversion Dip', thesis: 'Buy a pullback in an uptrend.', firing_now: false, current_edge: 'confirmed', current: { n: 42, win_rate: 62, expectancy: 0.79, low_confidence: false }, overall: { n: 80 } },
      ],
    },
  },
  events: {
    days_to_earnings: 5, earnings_date: '2026-11-03', ex_dividend_date: null, last_ex_dividend_date: '1995-04-27',
    filings: [{ form: '8-K', date: '2026-09-28', url: 'https://www.sec.gov/Archives/edgar/data/1/000001', excerpt: 'xbrl junk 0000002488 false' }],
    headline_flags: { n_total: 17, n_positive: 1, n_risk: 1, note: 'keyword scan of headlines — a heuristic prompt for the reader, not a sentiment model', positive_headlines: ['Memory ETF could surge on beat-and-raise'], risk_headlines: ['Regulator opens probe into chip exports'] },
    news: [
      { title: 'Memory ETF could surge on beat-and-raise', publisher: 'Yahoo Finance', date: 'Sun, 04 Oct 2026 00:42:00 GMT', link: 'https://news.example.com/a', summary: '' },
      { title: 'Regulator opens probe into chip exports', publisher: 'Reuters', date: 'Mon, 05 Oct 2026 10:00:00 GMT', link: 'https://news.example.com/b', summary: '' },
      { title: 'A headline with a hostile link', publisher: 'Spam', date: 'Mon, 05 Oct 2026 11:00:00 GMT', link: 'javascript:alert(1)', summary: '' },
    ],
    industry_news: [{ title: 'Semiconductor capex outlook', publisher: 'India Briefing', date: 'Wed, 30 Sep 2026 07:00:00 GMT', link: 'https://news.example.com/c', summary: '' }],
    macro_geopolitical_news: [{ title: 'AI investment keeps growth in gear', publisher: 'Deloitte', date: 'Wed, 30 Sep 2026 07:00:00 GMT', link: 'https://news.example.com/d', summary: '' }],
  },
  market: {
    fred: { VIX: 15.52, 'Fed Funds %': 3.75, '10Y Treasury %': 5.31 },
    peer_news: [{ peer: 'NBIS', title: 'Peer story about a fund raise', publisher: 'Yahoo Finance', date: 'Thu, 24 Sep 2026 07:00:00 GMT' }],
    peers: { median_21d_pct: 7.2, median_63d_pct: 13.4, rows: [{ ticker: 'NBIS', ret_21d_pct: 10.8, ret_63d_pct: 15.9 }], self_21d_pct: 37.1, self_63d_pct: 26.5, self_vs_peers_21d_pct: 29.9, self_vs_peers_63d_pct: 13.1 },
    tape: { SPY: { change_pct_5d: 1.47, label: 'S&P 500', last: 779.09 }, '^VIX': { change_pct_5d: -1.12, label: 'VIX (equity volatility)', last: 15.01 }, '^TNX': { change_pct_5d: 3.03, label: 'US 10Y Treasury yield', last: 5.269 } },
  },
  sources_ok: { filings: true, fundamental: true, news: true, peers: true, structure: true, suite: true, ta_block: true, tape: true },
  backtest: { universe: '99 tickers, 10y', exit_takeaway: 'Wide exits beat tight trails.', vol_ratio: 'Vol ratio predicts a little.', direction: { finding: 'No directional edge.', so: 'Use levels, not forecasts.' }, tail_state: {}, exit_rules: { wide: { label: 'Wide trail', mean: 4.2, win: 55, p5: -12, worst: -30, hold: 40 } } },
});
const richCompany = () => ({
  fundamental: {
    name: 'Advanced Micro Devices, Inc.', sector: 'Technology', industry: 'Semiconductors', is_fund: false, market_cap: 1060161912832,
    valuation_multiples: { trailingPE: 165.67, forwardPE: 41.31, pegRatio: 0.64, priceToSalesTrailing12Months: 25.67, priceToBook: 15.77, enterpriseToEbitda: 106.93 },
    profitability: { grossMargins: 0.5572, operatingMargins: 0.1725, profitMargins: 0.1558, returnOnEquity: 0.102, returnOnAssets: 0.0513, revenueGrowth: 0.501, earningsGrowth: 1.595, earningsQuarterlyGrowth: 1.634 },
    balance_sheet: { totalCash: 13111000064, totalDebt: 4276000000, freeCashflow: 8841499648, operatingCashflow: 10080000000, debtToEquity: 6.361, currentRatio: 2.609, quickRatio: 1.688 },
    dividend: { yield: 0, payout_ratio: 0, rate: null, five_year_avg_yield: null, last_ex_dividend_date: '1995-04-27' },
    shares: { beta: 2.45, held_by_insiders: 0.004, held_by_institutions: 0.753, short_pct_float: 0.0246 },
    analyst: { n_analysts: 50, recommendation_key: 'strong_buy', recommendation_mean: 1.49, upgrades_90d: 1, downgrades_90d: 0, reiterated_90d: 34, price_targets: { current: 649.42, high: 1250, low: 365, mean: 636.208, median: 635.5 },
      rating_changes_90d: [{ action: 'main', date: '2026-10-06', firm: 'Citigroup', from: 'Buy', to: 'Buy', price_target: 800, price_target_action: 'Raises' }, { action: 'down', date: '2026-10-01', firm: 'Acme Capital', from: 'Buy', to: 'Hold', price_target: 600, price_target_action: 'Lowers' }] },
    insiders: { recent_buys: 0, recent_sells: 14, shares_bought: 0, shares_sold: 361285 },
    estimates: {
      eps_estimate: [{ period: '0q', avg: 1.92939, growth: 0.6078, numberOfAnalysts: 40 }, { period: '+1q', avg: 2.1, growth: 0.45, numberOfAnalysts: 38 }],
      revenue_estimate: [{ period: '0q', avg: 13058084460 }, { period: '+1q', avg: 14000000000 }],
      eps_revisions: [{ period: '0q', upLast30days: 25, downLast30days: 3 }, { period: '+1q', upLast30days: 18, downLast30days: 2 }],
      eps_trend: [{ period: '0q', current: 1.92939, '90daysAgo': 1.81335 }],
      earnings_surprises: [{ quarter: '2025-09-30T00:00:00', epsActual: 1.2, epsEstimate: 1.171, surprisePercent: 0.0248 }, { quarter: '2025-06-30T00:00:00', epsActual: 0.9, epsEstimate: 1.0, surprisePercent: -0.1 }],
    },
  },
  pillars: {
    fundamental: { score: 0, data: { revenue_direction: 'improving', margin_direction: 'improving', current_ratio: 2.609 } },
    valuation: { score: 57, data: { forward_pe: 41.3, peg_ratio: 0.64, ev_to_ebitda: 106.9, target_upside_pct: -2, recommendation: 'strong_buy' } },
    sentiment_targets: { score: 0, data: { sentiment_label: 'Strong / Uptrend', one_year_return_pct: 175.7 } },
    catalyst_revisions: { score: 10, data: { eps_revision_trend: 'raising', days_to_earnings: 27 } },
    quality_capital: { score: 17, data: { roe: 10.2, roic: 4.8 } },
    ownership_flow: { score: 25, data: { institution_pct: 75.3, short_pct_float: 2.5 } },
    structural: { score: 25, data: { industry_growth_signal: 'Stagnant' } },
    macro_sensitivity: { score: 68, data: { beta: 2.45, beta_interpretation: 'Very High Beta', vol_regime: 'Elevated' } },
    geopolitical: { score: 33, data: { news_sentiment: 'Neutral', sector_regulatory_risk: 'High' } },
    sector_rotation: { score: 0, data: { rotation_signal: 'Strong Inflows' } },
  },
});
const baseTM = () => ({
  ticker: 'DRAM', as_of: '2026-10-05 22:03 UTC', cached_evidence: true, headline: 'x',
  since_entry: { entry_date: '2026-08-12', days_held: 54, underlying_entry: 54.8, underlying_entry_source: 'close on the entry date', underlying_now: 61.67, move_pct: 12.5, vs_you: 'with you', high_since_pct: 16.1, low_since_pct: -0.9, rolls: 1, roll_realized_pnl: 164, realized_banked: 164, effective_breakevens: [59.35] },
  profile: { kind: 'covered_call', label: 'Covered call', dte: 46, pnl: { unrealized: 232, pct: 3.91 }, notes: 'Imported from pasted broker order', short_premium: true },
  decision: {
    signal: 'STRONG_EXIT', score: 22, raw_score: 22, blend: 24, event_adj: -2, confidence: 'medium', conviction: 60, weights: {}, coverage: 1,
    overrides: ['Captured 100% of max profit - almost nothing left to earn; close to free capital'], conflicts: ['Technical (51) and Quant (4) disagree by 47 pts'],
    lenses: {
      quant: { score: 4, weight: 0.81, label: 'Quant', notes: ['Quant desk (hold read): STRONG CLOSE 4/100'], source: 'desk', detail: { desk: { signal: 'STRONG_CLOSE', score: 4, hold_base: 50, adjustments: [{ label: 'Cushion', pts: -3, note: 'n' }], holder_factors: [{ label: 'Vol decay', favorable: true, note: 'n' }], overrides: [], subscores: { edge: 50, pop: 60, sortino: 40, tail: 30, carry: 20 } }, groups: { Risk: [{ key: 'k', label: 'Cushion', value: 37.8, fmt: 'pct', tone: 'good', note: 'n' }] } } },
      technical: { score: 51, weight: 0.39, notes: ['Bullish position, technical neutral'], scope: 'scope text', build: { kind: 'alignment', dir: 0.434, aligned: 0.434, k: 1.15, gain: 22, base: 50, points: 11, lo: 28, hi: 72, weight_total: 5, adjust: -10 }, signals: [{ family: 'traders', name: 'Famous-trader rulesets', d: 0.618, note: '11 bullish · 1 bearish · 3 neutral of 15', w: 4 }, { family: 'dealer', name: 'Dealer gamma flip', d: -0.3, note: 'spot below flip', w: 1 }] },
      fundamental: { score: 0, weight: 0, available: false, notes: [] },
      event: { score: 50, weight: 0, clear: true, notes: [], adjustment: 0 },
    },
  },
  exit_plan: {
    recommendation: { when: 'now', level: null, text: 'Exit now - work a limit near the mid', steps: [{ tag: 'EXIT', text: 'Exit now - work a limit near the mid' }, { tag: 'INFO', text: 'Short call n/a ITM - theta is working for you' }] },
    items: [
      { kind: 'stop', level: 55.35, basis: 'x', action: 'DEFEND_OR_EXIT', why: 'A daily close below it means the bullish structure has failed.', sources: [], distance_pct: -10.25, distance_atr: 2.7, distance_usd: -6.32, title: 'Structural stop', group: 'against', source_levels: [{ label: 'Supertrend (up)', price: 55.35 }, { label: 'Put Support', price: 55 }], evidence: 'ev', detail: 'detail' },
      { kind: 'target', level: 63.82, basis: 'x', action: 'TAKE_PROFIT', why: 'First area where sellers stepped in before', sources: [], distance_pct: 3.49, distance_atr: 0.9, distance_usd: 2.15, title: 'Profit zone', group: 'for', source_levels: [{ label: 'BSL pool', price: 63.82 }], status_text: '$2 away', status_tone: 'warn' },
      { kind: 'target', level: 70, basis: 'x', action: 'TAKE_PROFIT', why: 'Dealer call resistance', sources: [], distance_pct: 13.5, distance_atr: 3.6, distance_usd: 8.33, title: 'Profit zone', group: 'for', source_levels: [{ label: 'Dealer call resistance', price: 70 }] },
      { kind: 'time', level: null, in_days: 25, basis: 'x', action: 'REVIEW', why: 'gamma starts to dominate theta', sources: [], title: '21-DTE decision', group: 'rules' },
      { kind: 'pnl', level: null, pnl_level: 150, basis: 'x', action: 'TRIM', why: 'bank some', sources: [], title: 'P&L trim', group: 'rules' },
    ],
    atr: 2.32, atr_pct: 3.8, risk_reward: { risk: 1, reward: 0.34, ratio: 0.34, note: 'rr note' },
    hold_odds: { rows: [{ state: 'deep OTM', n: 100, mean_delta: 1.2, p_close_better: 30, worst5: -3, p_finish_lt_neg2: 5 }], baseline: { n: 1000, mean_delta: 0.9, p_close_better: 15, worst5: -4, p_finish_lt_neg2: 6 }, takeaway: 't' },
    vol: { sigma_ann_pct: 50, sigma_dte_pct: 23.86, horizon_short: 'expiry', dte: 46, strikes: [{ side: 'call', strike: 85, z_sigma: 1.34, p_touch: 0.18, breached: false }] },
  },
  monitor: {
    up: [{ level: 63.82, what: 'BSL pool, Swing high', distance_usd: 2.15, distance_pct: 3.49, source_levels: [{ label: 'BSL pool', price: 63.82 }], if_break: 'breaks higher', if_reject: 'rejects', effect_if_break: 'good' }],
    down: [{ level: 55.35, what: 'Supertrend', distance_usd: -6.32, distance_pct: -10.2, if_break: 'breaks lower', if_hold: 'holds', effect_if_break: 'bad', noise: true }],
    indicators: [{ metric: 'MTF bias', now: { direction: 'bullish', strength: 'weak' }, watch: ['w1', 'w2'] }],
    fundamental_events: [{ item: 'Earnings', when: '2026-12-17', watch: 'a print', url: 'http://example.com' }],
  },
  ...evidenceData(), fundamental: { is_fund: true, name: 'Roundhill Memory ETF', sector: 'ETF', industry: '' }, pillars: {}, evidence_json: { a: 1 },
});
// A single-name company (not a fund): the fundamental lens is scored and the company data is present.
const richTM = () => { const t = baseTM(); Object.assign(t, richCompany()); t.decision.lenses.fundamental = { score: 35.8, weight: 0.1, available: true, notes: ['Valuation is stretched; business health is strong'] }; return t; };
const positionRiskLens = (over = {}) => ({
  score: 44, weight: 0.3571, label: 'Position risk', source: 'position_risk', available: true, signal: null, overrides: [],
  notes: ['55% chance of touching the 10% stop (975.01) within a month — it is 0.6σ away (−18)'],
  detail: {
    groups: {
      'Edge & odds': [{ key: 'tsmom', label: '12-1 month momentum', value: 438, fmt: 'pct_signed', tone: null, note: 'academic time-series momentum (weak edge at 1-3 months)' }],
      Risk: [
        { key: 'unrealized', label: 'Unrealized P&L', value: 250, fmt: 'usd', tone: 'good', note: '+4.6% on the trade' },
        { key: 'vol_ann', label: 'Volatility (annualised)', value: 87.2, fmt: 'pct', tone: 'bad', note: 'blend of 21d and 63d realized vol' },
        { key: 'move_1s', label: '1σ move over 21 trading days', value: 25.2, fmt: 'pct', tone: null, note: '≈ ±$285.30 per share' },
        { key: 'stop_touch', label: 'P(touch −10% stop in 21d)', value: 55, fmt: 'pct', tone: 'bad', note: 'stop 975.01 is 0.60σ below price' },
        { key: 'drawdown', label: 'Below the 52-week high', value: 0.3, fmt: 'pct', tone: 'good', note: '≥ 10-20% off the high = fatter left tail in the backtest' },
        { key: 'beta', label: 'Beta vs market', value: 1.54, fmt: 'num', tone: 'warn', note: 'market sensitivity of the stock' },
      ],
    },
    desk: {}, how_it_counts: 'For shares there is no option payoff to price, so Quant is a POSITION-RISK read in volatility terms. It is scored alongside Technical and Fundamental (each lens shows its weight); event risk is then added or subtracted as visible points.',
  },
  build: { kind: 'position_risk', base: 62, clamped: false, terms: [{ label: 'Stop distance', pts: -18, note: '55% chance of touching the 10% stop (975.01) within a month — it is 0.6σ away' }],
    stop: { cost: 1083.3, level: 975.01, pct: 10, crossed: false, z: 0.6, p_touch: 0.55, sigma21_pct: 25.2 } },
  ...over,
});
const stockTM = () => { const t = baseTM(); t.profile = { kind: 'stock', label: 'Stock', spot: 1133.4, pnl: { unrealized: 250, pct: 4.6 } };
  t.decision.lenses.quant = positionRiskLens(); t.technical.suite.price = 1133.4; t.technical.suite.range = { hi_252: 1136.9, lo_252: 90, pct_from_52w_high: -0.3 }; t.since_entry = undefined; t.exit_plan.hold_odds = null; t.exit_plan.vol = undefined; t.exit_plan.risk_reward = null; t.exit_plan.atr = null; t.exit_plan.items = []; t.monitor = { up: [], down: [], indicators: [], fundamental_events: [] }; t.decision.signal = 'HOLD'; t.decision.overrides = []; t.decision.conflicts = []; t.decision.score = 55; t.decision.raw_score = 55; return t; };

const alt = (o) => Object.assign({ name: 'x', category: 'roll', group: 'adjust', mechanics: 'mech', rationale: 'because', risk_note: 'rn', net_cash: 56, scenarios: [{ move_pct: -30, spot: 43, pnl: -1000 }, { move_pct: 0, spot: 61.67, pnl: 5000 }, { move_pct: 30, spot: 80, pnl: 8000 }], max_loss: null, max_gain: 6056, breakevens: [60], greeks: { delta: -13, gamma: 0, theta: 2, vega: 0 }, theta_day: 2, defined_risk: false, upside_risk_free: true, pop_pct: 100, ev: 5959, d_pop: 0, turns_profitable: true, legs: [{ action: 'SELL', right: 'C', strike: 86, qty: 1, dte_days: 74, expiry: '2026-12-18' }], desk_score: 63, score_breakdown: { edge: 60, risk: 40, recovery: 50, market_fit: 70 } }, o);
const defendMenu = (roll) => ({
  ticker: 'DRAM', tested: false, cushion_pct: 27.4, covered: false, short_right: 'C', short_strike: 85, spot: 61.67, dte_days: 46, contracts: 1, unrealized_pnl: 5964, pricing: 'Model-priced on the live chain', structure: 'naked_call', roll_search: roll,
  alternatives: [
    alt({ name: 'Cover it - buy 100 sh', category: 'cover', defined_risk: true, max_loss: -166, legs: [{ action: 'BUY', right: 'STK', strike: 0, qty: 100, dte_days: null }], desk_score: 66, event_risk: { points: 6, note: 'earnings print before expiry' } }),
    alt({ name: 'Roll to the $86 call - Dec 18', category: 'roll', roll_meta: { expiry: '2026-12-18', dte: 74, strike: 86, credit_total: 56, p_otm: 98.4, p_otm_source: 'RND', spans_earnings: true, structure: { 'clears support': true, 'clears resistance': true, levels_available: 6 }, scores: { cushion_sigma: 1.4 }, new_breakeven: null, new_capital: null } }),
    alt({ name: 'Roll to the $89 call', category: 'roll', desk_score: 62 }), alt({ name: 'Roll to the $87 call', category: 'roll', desk_score: 61 }),
    alt({ name: 'Calendarised hedge', category: 'calendar', desk_score: 55 }), alt({ name: 'Wheel', category: 'assignment', desk_score: 50 }),
    alt({ name: 'Close and redeploy: defined condor', category: 'defined_risk', group: 'replace', defined_risk: true, max_loss: -400, desk_score: 58 }),
    alt({ name: 'Hold as-is (do nothing)', category: 'hold', group: 'benchmark', desk_score: 59, legs: [] }),
    alt({ name: 'Close the trade', category: 'exit', group: 'benchmark', desk_score: 68, max_loss: -5964, legs: [], defined_risk: true }),
  ],
  recoverability: { recovery_score: 100, posture: 'marginal', breakeven: null, needed_move_pct: null, cushion_pct: 27.4, dist_to_be_sigma: null, expected_move: 12.41, tested_delta: 0.07, severity: 'fresh', p_touch: 12.8, vrp_pct: -26.8, trend_pct: 79.6, iv_pct: 56.7, hv_pct: 77.4, risk_read: 'a 13% chance the strike is breached before expiry', factors: [{ label: 'Cushion to breakeven', kind: 'prob', favorable: true, detail: 'well clear' }, { label: 'Implied vol', kind: 'prob', favorable: null, detail: 'sigma 57%' }, { label: 'Momentum (MACD)', kind: 'ta', favorable: false, detail: 'against the pullback' }], outlook: { tilt: 'adverse', note: 'Technicals lean adverse' } },
  assignment: { p_itm: 5, extrinsic: 0.36, intrinsic: 0, early_assignment_risk: true, early_reason: 'extrinsic below dividend', pin_ratio: 1.88, effective_basis: 145, assignment_capital: 8500, consequence: 'Assignment => 100 sh CALLED AWAY at $85' },
  cost_of_waiting: [{ in_trading_days: 5, dte_left: 41, recovery_pop: 100, expected_pnl: 5976 }, { in_trading_days: 20, dte_left: 26, recovery_pop: 100, expected_pnl: 5998 }],
  context: { loss_read: { time_value_recoverable: 36 }, technical: { support: 55.6, resistance: 62.8, note: 'support 55.6, resistance 62.8' }, pattern: { type: 'double bottom', direction: 'bullish', status: 'forming', target: 72, breakout: 63, confidence: 75, window: '3mo' }, range: { low: 55.6, high: 62.8, width_pct: 12, where: 'mid', note: 'Range 55-62' }, sector: { themes: [], peers: ['A', 'B'], note: 'No mapped sector theme' }, earnings: { before_expiry: false, date: '2026-12-17', days: 73, note: 'Earnings 2026-12-17 after expiry' }, vol_note: 'Realized vol cooling' },
  hold: { pop_pct: 100, expected_pnl: 5963, max_loss: null }, iv_structure: { near_iv_pct: 51.1, far_iv_pct: 54.3, term_ratio: 1.06, term_label: 'contango', skew_pts: 1, skew_label: 'flat', note: 'Front cheap vs back' },
  desk_recommendation: { name: 'Close the trade', category: 'exit', group: 'benchmark', desk_score: 68, score_breakdown: { edge: 50, risk: 100, recovery: 55, market_fit: 65 }, reasons: ['Locks in a gain with certainty', 'Removes the tail'], runner_up: { name: 'Roll to the $86 call', desk_score: 63, reasons: ['credit roll'], role: 'fix' }, avoid: [{ name: 'Widen to a strangle', why: 'adds a naked put' }] },
  roll_structure: { support: 60.26, resistance: 61.99, poc: 61.74, gamma_flip: 59.27, gamma_wall: 70, gamma_regime: 'long', recent_5d: { support: 61.4, resistance: 62.19, poc: null } },
  roll_note: 'Naked short call - every roll is a NET CREDIT',
});
const defendHealthyMinimal = () => ({ ticker: 'AAPL', short_right: 'P', short_strike: 305, spot: 332.89, dte_days: 11, cushion_pct: 8.4, unrealized_pnl: 12, roll_search: 'skipped', structure: 'cash_secured_put', alternatives: [], recoverability: { recovery_score: 98.7, posture: 'healthy', breakeven: 304.76, needed_move_pct: null, cushion_pct: 8.4, dist_to_be_sigma: 2.2, tested_delta: 0.03, severity: 'healthy', factors: [] } });

// ── Manage Book (BookTailRiskResult) — numbers read off the live book on 5 Oct: AMD breaches three guardrails with one fix.
const remed = (legs, after, cost, kept, action = 'cap', pre) => ({ action, legs, cost, tail_after: after, premium_kept: kept, unit_price: action === 'cap' ? 0.11 : 9.0, pricing: 'live ask', ...(pre ? { prefill: pre } : {}) });
const bt = (ticker, id, structure, before, rec, left, short, alt = null) => ({ ticker, name: ticker, trade_id: id, structure, risk: Math.abs(before), premium_left: left, why: 'w', tail_before: before, stress: { market_move_pct: 30, beta: 1.9, name_move_pct: 57, vol_shock_pts: -9 }, recommended: rec, alt, short });
const pf = (ticker, strike, type) => ({ ticker, legs: [{ action: 'BUY', type, strike, expiration: '2026-11-20', qty: 1 }] });
const bookTailRisk = () => {
  const AMD = () => bt('AMD', 21, 'options', -49872, remed('BUY 1x 770C (vs your short 700C)', -6760, 11, 232, 'cap', pf('AMD', 770, 'CALL')), 242, { strike: 700, right: 'C', qty: 1 }, remed('SELL 1x 700C (close)', -1000, 900, 0, 'close'));
  const SMH = () => bt('SMH', 22, 'options_spread', -33212, remed('BUY 1x 825C (vs your short 750C)', -7081, 233, 601, 'cap', pf('SMH', 825, 'CALL')), 834, { strike: 750, right: 'C', qty: 1 });
  const APP = () => bt('APP', 11, 'cash_secured_put', -6829, remed('BUY 1x 205P (vs your short 230P)', -2272, 16, 112, 'cap', pf('APP', 205, 'PUT')), 126, { strike: 230, right: 'P', qty: 1 });
  const fix = (headline, targets, extra = {}) => ({ headline, effect: 'cuts the stress loss', cost: 'each target priced below', targets, ...extra });
  const chk = (key, label, status, value_str, limit_str, f, note) => ({ key, label, status, value_str, limit_str, note, fix: f });
  return {
    scope: { purpose: 'income', included: 14, excluded: { hedge: 2, untagged: 2, trade: 1 }, excluded_total: 5 },
    coverage: { requested: 14, analysed: 12, skipped: [{ trade_id: 50, ticker: 'TOST', reason: 'no open option legs (stock / futures only, or the options are closed)' }, { trade_id: 64, ticker: 'EQT', reason: 'no open option legs (stock / futures only, or the options are closed)' }] },
    data_quality: { legs: 22, chain: 1, solved: 21, fallback: 0, indicative: true },
    etf_hedges: (() => {
      const mv = [-30, -20, -15, -10, -5, 10, 20];
      const group = { '-30': -62000, '-20': -50000, '-15': -40185, '-10': -29000, '-5': -15000, '10': 22000, '20': 61000 };
      const scen = (h) => mv.map((m, i) => ({ move_pct: m, group_pnl: group[String(m)], hedge_pnl: h[i], net: group[String(m)] + h[i] }));
      const v = (key, label, qty, shortLeg, net, annual, pct, funded, offset, up, h) => ({
        key, label, qty, put: { strike: 540, ask: 18.38, iv: 0.3 }, short: shortLeg, net_debit: net, is_credit: net < 0, annual_cost: annual, cost_pct_income: pct,
        within_budget: pct <= 10, n_within_budget: 1, funded_pct: funded, offset_pct: offset, net_at_ref: -40185 + h[2], scenarios: scen(h),
        adds_upside_risk: !!(shortLeg && shortLeg.right === 'C'), upside_given_up: up, tail_is_upside: false });
      return {
        target_offset_pct: 60, budget_pct: 10,
        candidates: [{
          etf: 'SOXX', label: 'Semiconductors (iShares)', group_label: 'AI data-center buildout', member_rho_min: 0.37, member_rho_max: 0.89, spot: 589.45, expiry: '2026-12-18', dte: 73, indicative: true, n_members: 3, avg_rho: 0.9, strength: 'strong', loss_ref: 40185, ref_move: -15, capital: 480000, theta: 113.1,
          members: [{ ticker: 'MU', rho: 0.82, beta: 1.43, loss_ref: 22366 }, { ticker: 'AMD', rho: 0.8, beta: 1.2, loss_ref: 11223 }, { ticker: 'LRCX', rho: 0.89, beta: 1.25, loss_ref: 6071 }],
          group_pnl: group,
          variants: [
            v('put', 'Put only', 5, null, 9188, 45938, 64.7, 0, 58, 0, [60000, 38000, 23300, 12000, 4000, -3000, -6000]),
            v('collar50', 'Put + sell a call (≈50% funded)', 5, { right: 'C', strike: 700, bid: 7.6 }, 5388, 26938, 37.9, 41, 64, -26787, [58000, 36000, 25700, 11000, 3000, -9000, -26787]),
            v('collar100', 'Put + sell a call (≈zero-cost collar)', 4, { right: 'C', strike: 660, bid: 18.35 }, 8, 42, 0.1, 100, 57, -28819, [50000, 30000, 22900, 9000, 2500, -12000, -28819]),
            v('putspread', 'Put spread (sell a lower put)', 12, { right: 'P', strike: 475, bid: 5.42 }, 15552, 77760, 109.5, 29, 58, 0, [55000, 35000, 23300, 12000, 4000, -3000, -9000]),
          ],
        }],
        skipped: [{ etf: 'XLK', label: 'Technology', loss_ref: 310, reason: 'the group only loses ~$310 at a −15% ETF move — less than ONE option contract\'s protection' }],
        groups: [{ etf: 'SOXX', label: 'AI data-center buildout', names: ['AMD', 'LRCX', 'MU'], basket_rho: 0.9, alts: [{ etf: 'SMH', rho: 0.89 }, { etf: 'XLK', rho: 0.85 }] },
                 { etf: 'IWM', label: 'Russell 2000 small caps', names: ['IWM'], basket_rho: 1.0, alts: [] }],
        mapping: [{ ticker: 'AMD', etf: 'SOXX', rho: 0.8, beta: 1.2, alts: [], self: false, group: 'SOXX' }, { ticker: 'IWM', etf: 'IWM', rho: 1.0, beta: 1.0, alts: [], self: true, group: 'IWM' }],
        unmapped: ['ZZZ'],
      };
    })(),
    has_covered_stock: true,
    with_stock_downside: {
      crash_scenarios: [
        { label: '−20%', move_pct: -0.2, pnl: -21500, pct_of_capital: -11.8 }, { label: '−10%', move_pct: -0.1, pnl: -6200, pct_of_capital: -3.4 },
        { label: '+10%', move_pct: 0.1, pnl: -14000, pct_of_capital: -7.7 }, { label: '+20%', move_pct: 0.2, pnl: -38000, pct_of_capital: -20.9 },
      ],
      loss_by_name: [{ ticker: 'AMD', pnl: -15500, beta: 1.8 }, { ticker: 'SMH', pnl: -6000, beta: 1.5 }, { ticker: 'KO', pnl: 800, beta: 0.6 }],
      cvar_95: -45000, cvar_capital_pct: 24.7,
    },
    concentration_view: {
      side: 'up', side_label: '+30% squeeze × β', n_names: 14, n_groups: 6, effective_bets: 4.1, effective_names: 9.8,
      shock_moves: [-30, -20, -10, 10, 20, 30],
      names: [
        { ticker: 'AMD', trades: 1, beta: 1.9, capital: 21800, capital_pct: 12, theta: 1.1, theta_pct: 9, down_pnl: 900, up_pnl: -53034, tail_loss: 53034, tail_pct: 38, gamma_pct: 31, risk_ratio: 3.17, payback_days: 1200 },
        { ticker: 'SMH', trades: 2, beta: 1.5, capital: 41000, capital_pct: 22, theta: 6.0, theta_pct: 28, down_pnl: 700, up_pnl: -33000, tail_loss: 33000, tail_pct: 24, gamma_pct: 21, risk_ratio: 1.09, payback_days: 400 },
        { ticker: 'KO', trades: 1, beta: 0.6, capital: 30000, capital_pct: 16, theta: 3.0, theta_pct: 18, down_pnl: -300, up_pnl: -1000, tail_loss: 1000, tail_pct: 1, gamma_pct: 3, risk_ratio: 0.06, payback_days: 12 },
      ],
      groups: [
        { kind: 'book', label: 'Whole book moves together', tickers: ['AMD', 'SMH', 'KO'], capital: 92800, capital_pct: 100, theta: 10.1, shocks: { '-30': 4000, '-20': 2500, '-10': 900, '+10': -9000, '+20': -33000, '+30': -90000 }, worst_move: '+30', worst: -90000, worst_pct_capital: -97, carry_years: 24.4 },
        { kind: 'cluster', label: 'AMD · SMH', tickers: ['AMD', 'SMH'], capital: 62800, capital_pct: 68, theta: 7.1, avg_rho: 0.82, shocks: { '-30': 3000, '-20': 2000, '-10': 800, '+10': -8000, '+20': -30000, '+30': -86000 }, worst_move: '+30', worst: -86000, worst_pct_capital: -137, carry_years: 33.2 },
        { kind: 'sector', label: 'Semiconductors', tickers: ['AMD', 'SMH'], capital: 62800, capital_pct: 68, theta: 7.1, shocks: { '-30': 3000, '-20': 2000, '-10': 800, '+10': -14200, '+20': -30000, '+30': -86000 }, worst_move: '+30', worst: -86000, worst_pct_capital: -137, carry_years: null },
      ],
    },
    hedge_plan: {
      budgets: [5, 10, 15, 25], default_budget: 10, income_ok: true, annual_income: 41000, cvar_now: 33100, cvar_pct_capital: 18.2,
      limit_warn: 3640, limit_breach: 5460, gap: 29460,
      tail: { avg_tail_pnl: -41000, n_sims: 1000, delta: -30000, gamma: -15000, vega: -1300, theta: 4000, other: 1300, up_pct: 84, down_pct: 16, side: 'up' },
      candidates: [
        { label: 'Call spread 5%/15%', instrument: 'SPX', kind: 'call spread', long_strike: 5460, short_strike: 5980, cost_per_spread: 700, pricing: 'model (BS · index vol)', dte_days: 90, unit: 'spread', unit_annual: 2800, targets: ['Upside tail (melt-up)'],
          plans: [{ budget_pct: 5, units: 0 }, { budget_pct: 10, units: 1, annual_cost: 2800, cost_pct_income: 6.8, cvar_after: 28000, cvar_reduction: 5100, reduction_pct: 15.4, closes_gap_pct: 17, per_dollar: 1.82 },
                  { budget_pct: 15, units: 2, annual_cost: 5600, cost_pct_income: 13.7, cvar_after: 23500, cvar_reduction: 9600, reduction_pct: 29, closes_gap_pct: 33, per_dollar: 1.71 }, { budget_pct: 25, units: 3, annual_cost: 8400, cost_pct_income: 20.5, cvar_after: 20000, cvar_reduction: 13100, reduction_pct: 39.6, closes_gap_pct: 44, per_dollar: 1.56 }],
          helps: true, reason: null, recommended: true, standby: false },
        { label: 'Dynamic VIXY sleeve', instrument: 'VIXY', kind: 'signal-based', signal: 'deploy when spot VIX > VIX3M (backwardation)', unit: '$1k sleeve', unit_annual: 15, targets: ['Vol spike', 'Crash tail'],
          plans: [{ budget_pct: 5, units: 100, annual_cost: 1500, cost_pct_income: 3.7, cvar_after: 32000, cvar_reduction: 1100, reduction_pct: 3.3, closes_gap_pct: 4, per_dollar: 0.73 }, { budget_pct: 10, units: 180, annual_cost: 2700, cost_pct_income: 6.6, cvar_after: 31500, cvar_reduction: 1600, reduction_pct: 4.8, closes_gap_pct: 5, per_dollar: 0.59 },
                  { budget_pct: 15, units: 180, annual_cost: 2700, cost_pct_income: 6.6, cvar_after: 31500, cvar_reduction: 1600, reduction_pct: 4.8, closes_gap_pct: 5, per_dollar: 0.59 }, { budget_pct: 25, units: 180, annual_cost: 2700, cost_pct_income: 6.6, cvar_after: 31500, cvar_reduction: 1600, reduction_pct: 4.8, closes_gap_pct: 5, per_dollar: 0.59 }],
          helps: true, reason: null, recommended: false, standby: true },
        { label: 'Put spread 15%/30%', instrument: 'SPX', kind: 'put spread', long_strike: 4420, short_strike: 3640, cost_per_spread: 900, pricing: 'live SPX chain', dte_days: 90, unit: 'spread', unit_annual: 3600, targets: ['Downside tail', 'Net direction'],
          plans: [{ budget_pct: 5, units: 0 }, { budget_pct: 10, units: 1, annual_cost: 3600, cost_pct_income: 8.8, cvar_after: 33250, cvar_reduction: -150, reduction_pct: -0.5, closes_gap_pct: 0, per_dollar: -0.04 }, { budget_pct: 15, units: 1, annual_cost: 3600, cost_pct_income: 8.8, cvar_after: 33250, cvar_reduction: -150, reduction_pct: -0.5, closes_gap_pct: 0, per_dollar: -0.04 }, { budget_pct: 25, units: 2, annual_cost: 7200, cost_pct_income: 17.6, cvar_after: 33400, cvar_reduction: -300, reduction_pct: -0.9, closes_gap_pct: 0, per_dollar: -0.04 }],
          helps: false, reason: 'pays when the market FALLS, but 84% of your worst months are rallies', recommended: false, standby: false },
      ],
      exposures: [
        { key: 'delta', label: 'Net direction (β-Δ)', value: '−120 SPY-equiv shares', shock: 'market ±10% (β-weighted)', impact_down: 6200, impact_up: -6200, worst: -6200, worst_pct_capital: -3.4, hedge: { instrument: 'SPY shares', action: 'BUY 120 SPY', units: 120, notional: 62400, annual_cost: 0, note: 'flattens β-Δ exactly — no premium' } },
        { key: 'vega', label: 'Net vega (short vol)', value: '-126 $/vol-pt', shock: 'vol +10 pts, spot unchanged', impact_down: null, impact_up: -1610, worst: -1610, worst_pct_capital: -0.88, hedge: { instrument: 'SPY ~90d ATM option', action: 'below one SPY option', units: 0.1, annual_cost: 120, vega_per_contract: 104, note: 'assumes your names IV moves 1:1 with index IV' } },
        { key: 'gamma', label: 'Short gamma (convexity)', value: '', shock: 'market ±10%, beyond the delta line', impact_down: -2200, impact_up: -24000, worst: -24000, worst_pct_capital: -13.2, hedge: { instrument: 'Cap the short strikes (Fixes tab)', action: 'buy a wing on the offending shorts', units: null, annual_cost: null, note: 'targeted gamma cover' } },
      ],
    },
    positions: 14, stored: true, computed_at: new Date(Date.now() - 3 * 3600e3).toISOString(),
    net_delta: -310, net_gamma: -4.2, net_vega: -1650, net_theta: 612, beta_delta_spy: -120, avg_beta: 1.4, short_vol: true,
    book_capital: 182000, annual_income: 41000, capital_basis: 'cash-secured notional', theta_net_liq_pct: 0.34, carry_yield_pct: 22.5, cvar_capital_pct: 18.2,
    cvar_95: -33100, var_95: -21000, horizon: '1 month', stock_notional: 120000,
    concentration: [
      { ticker: 'AMD', trades: 1, short_legs: 1, net_gamma: -1.2, net_vega: -300, net_delta: -50, beta: 1.8, gamma_share_pct: 38, laddered: false, flags: ['One name carries 38% of book gamma'] },
      { ticker: 'SMH', trades: 2, short_legs: 2, net_gamma: -0.8, net_vega: -250, net_delta: -40, beta: 1.5, gamma_share_pct: 21, laddered: true, flags: [] },
    ],
    loss_by_name: [{ ticker: 'AMD', pnl: -9500, beta: 1.8 }, { ticker: 'SMH', pnl: -4100, beta: 1.5 }, { ticker: 'KO', pnl: 800, beta: 0.6 }],
    scenario_grid: { spot_moves: [-10, 10], vol_shocks: [-5, 5], rows: [
      { move_pct: -10, cells: [{ pnl: 1200, pct: 0.7 }, { pnl: -2000, pct: -1.1 }] },
      { move_pct: 10, cells: [{ pnl: -14000, pct: -7.7 }, { pnl: -21000, pct: -11.5 }] },
    ] },
    risk_scorecard: {
      grade: 'At risk', n_breach: 3, n_warn: 1, n_checks: 6,
      checks: [
        chk('cvar', 'CVaR 95% (1-mo)', 'breach', '18.2% of capital', '≤ 15%', fix('Cap the two biggest tail contributors', [APP(), SMH()], { alt: 'or trim 20% across the book' }), 'Expected loss in the worst 5% of months.'),
        chk('var', 'VaR 95% (1-mo)', 'pass', '11.5%', '≤ 15%', undefined),
        chk('sym', 'Up/down tail symmetry', 'breach', 'up-tail 2.4× down', '≤ 1.5×', fix('Cap the call side where the squeeze loses', [AMD(), SMH()])),
        chk('cluster', 'Correlated-cluster conc.', 'breach', '61% in one cluster', '≤ 40%', fix('Cap the cluster', [AMD(), SMH()])),
        chk('sector', 'Sector concentration', 'warn', 'Semis 52%', '≤ 45%', fix('Reduce Semis', [AMD()])),
        chk('tail', 'Tail loss at −20%', 'pass', '−$9.5k', '≤ $15k', undefined),
      ],
    },
    factor_exposure: {
      sectors: [{ sector: 'Semiconductors', tickers: ['AMD', 'SMH', 'MU'], capital: 94000, net_directional: -1, n: 3 }, { sector: 'Staples', tickers: ['KO'], capital: 12000, net_directional: 0, n: 1 }],
      clusters: [{ tickers: ['AMD', 'SMH', 'MU'], capital: 94000, net_directional: -1, avg_rho: 0.82 }],
      macro: [{ factor: 'SPY', label: 'S&P 500', rho: 0.74 }, { factor: 'TLT', label: '20y Treasuries', rho: -0.2 }],
    },
    crash_scenarios: [
      { label: '−20%', move_pct: -0.2, pnl: -9500, pct_of_capital: -5.2 }, { label: '−10%', move_pct: -0.1, pnl: 1200, pct_of_capital: 0.7 },
      { label: '+10%', move_pct: 0.1, pnl: -14000, pct_of_capital: -7.7 }, { label: '+20%', move_pct: 0.2, pnl: -38000, pct_of_capital: -20.9 },
    ],
    assignment_ladder: [{ move_pct: -10, pnl: 1200, put_assignment_capital: 23000, call_cover_cost: 0, puts_itm: 1, calls_itm: 0 }, { move_pct: 10, pnl: -14000, put_assignment_capital: 0, call_cover_cost: 9000, puts_itm: 0, calls_itm: 2 }],
    naked_assignment: { put_capital: 23000, call_capital: 70000, total: 93000, n_naked_puts: 1, n_naked_calls: 2 },
    hedge_menu: [
      { label: 'SPX 5200/5000 put spread', long_put: 5200, short_put: 5000, contracts: 2, instrument: 'SPX', long_strike: 5200, short_strike: 5000, cost_per_spread: 900, total_cost: 1800, annual_bleed: 5400, crash_payoff_20: 6200, offsets_pct: 65, cvar_reduction: 4100, efficiency: 0.8, cagr_lift_pct: 0.4, cost_effective: true, recommended: true, dte_days: 60 },
      { label: 'Dynamic VIXY sleeve', long_put: null, short_put: null, contracts: null, instrument: 'VIXY', sleeve_capital: 90000, cost_per_spread: 0, total_cost: 90000, annual_bleed: 1350, crash_payoff_20: 5000, offsets_pct: 52, cvar_reduction: 1100, efficiency: 0.8, cagr_lift_pct: 0.2, cost_effective: true, standby: true },
      { label: 'VIX 30/45 call spread', long_put: null, short_put: null, contracts: 3, instrument: 'VIX', long_strike: 30, short_strike: 45, cost_per_spread: 200, total_cost: 600, annual_bleed: 2400, crash_payoff_20: 3000, offsets_pct: 32, cvar_reduction: 2200, efficiency: 0.6, cagr_lift_pct: -0.1, cost_effective: false, dte_days: 60, vix_at_minus20: 42 },
    ],
    hedge_note: null,
    verdict: { level: 'Elevated', summary: 'Short-vol book with an upside tail 2.4× the downside.', actions: ['Cap the AMD and SMH short calls first.', 'Keep the SPX put spread as the crash backstop.'] },
    assumptions: { beta: 'Ticker betas vs SPY', mkt_vol_pct: 18, tail: 'fat-tailed Student-t', crash_prob_annual_pct: 5, hedge_rolls_per_year: 6 },
  };
};
// The Quant tab's full desk score (POST /desk-score): what the Manage read must adopt so the quant number is the same in both places.
const deskScore = (score = 53, signal = 'HOLD') => ({
  matched: true, desk_score: 61, lifecycle_score: score, signal, overrides: ['Captured 90% of max profit'], qp: null, spot: 61.67,
  opp: { per_side: null },
  management_analysis: {
    anchor: 50, anchor_label: 'Hold quality (remaining risk vs reward)', base_lenses: [], factors_net: 3, overlay: [{ name: 'Time / gamma', pts: 0, note: 'n' }],
    score, signal, overrides: ['Captured 90% of max profit'],
    contributions: [{ label: 'Cushion to strike', pts: 5, favorable: true, note: '37.8% — plenty of room', dimension: 'Loss probability' }, { label: 'Vol decay', pts: -2, favorable: false, note: 'n', dimension: 'Vol edge' }],
  },
});
module.exports = { baseTM, richTM, deskScore, positionRiskLens, stockTM, defendMenu, defendHealthyMinimal, bookTailRisk };
