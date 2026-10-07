// Payload shapes mirror what the live panels returned for the DRAM covered call (values read off the real screens).
const lens = (o) => o;
const baseTM = () => ({
  ticker: 'DRAM', as_of: '2026-10-05 22:03 UTC', cached_evidence: true, headline: 'x',
  since_entry: { entry_date: '2026-08-12', days_held: 54, underlying_entry: 54.8, underlying_entry_source: 'close on the entry date', underlying_now: 61.67, move_pct: 12.5, vs_you: 'with you', high_since_pct: 16.1, low_since_pct: -0.9, rolls: 1, roll_realized_pnl: 164, realized_banked: 164, effective_breakevens: [59.35] },
  profile: { kind: 'covered_call', label: 'Covered call', dte: 46, pnl: { unrealized: 232, pct: 3.91 }, notes: 'Imported from pasted broker order', short_premium: true },
  decision: {
    signal: 'STRONG_EXIT', score: 22, raw_score: 22, blend: 24, event_adj: -2, confidence: 'medium', conviction: 60, weights: {}, coverage: 1,
    overrides: ['Captured 100% of max profit - almost nothing left to earn; close to free capital'], conflicts: ['Technical (51) and Quant (4) disagree by 47 pts'],
    lenses: {
      quant: { score: 4, weight: 0.81, label: 'Quant', notes: ['Quant desk (hold read): STRONG CLOSE 4/100'], source: 'desk', detail: { desk: { signal: 'STRONG_CLOSE', score: 4, hold_base: 50, adjustments: [{ label: 'Cushion', pts: -3, note: 'n' }], holder_factors: [{ label: 'Vol decay', favorable: true, note: 'n' }], overrides: [], subscores: { edge: 50, pop: 60, sortino: 40, tail: 30, carry: 20 } }, groups: { Risk: [{ key: 'k', label: 'Cushion', value: 37.8, fmt: 'pct', tone: 'good', note: 'n' }] } } },
      technical: { score: 51, weight: 0.39, notes: ['Bullish position, technical neutral'], scope: 'scope text', signals: [{ name: 'a', d: 0.27, note: 'n' }] },
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
  trader_lenses: [], technical: { suite: {}, zones: [], patterns: [] }, fundamental: {}, pillars: {}, events: {}, market: {}, sources_ok: {}, evidence_json: { a: 1 },
});
const stockTM = () => { const t = baseTM(); t.profile = { kind: 'stock', label: 'Stock', pnl: { unrealized: -1949, pct: -21.4 } }; t.since_entry = undefined; t.exit_plan.hold_odds = null; t.exit_plan.vol = undefined; t.exit_plan.risk_reward = null; t.exit_plan.atr = null; t.exit_plan.items = []; t.monitor = { up: [], down: [], indicators: [], fundamental_events: [] }; t.decision.signal = 'HOLD'; t.decision.overrides = []; t.decision.conflicts = []; t.decision.score = 55; t.decision.raw_score = 55; return t; };

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
const remed = (legs, after, cost, kept, action = 'cap', pre) => ({ action, legs, cost, tail_after: after, premium_kept: kept, ...(pre ? { prefill: pre } : {}) });
const bt = (ticker, id, structure, before, rec, left, short, alt = null) => ({ ticker, name: ticker, trade_id: id, structure, risk: Math.abs(before), premium_left: left, why: 'w', tail_before: before, recommended: rec, alt, short });
const pf = (ticker, strike, type) => ({ ticker, legs: [{ action: 'BUY', type, strike, expiration: '2026-11-20', qty: 1 }] });
const bookTailRisk = () => {
  const AMD = () => bt('AMD', 21, 'options', -49872, remed('BUY 1x 770C (vs your short 700C)', -6760, 11, 232, 'cap', pf('AMD', 770, 'CALL')), 242, { strike: 700, right: 'C', qty: 1 }, remed('SELL 1x 700C (close)', -1000, 900, 0, 'close'));
  const SMH = () => bt('SMH', 22, 'options_spread', -33212, remed('BUY 1x 825C (vs your short 750C)', -7081, 233, 601, 'cap', pf('SMH', 825, 'CALL')), 834, { strike: 750, right: 'C', qty: 1 });
  const APP = () => bt('APP', 11, 'cash_secured_put', -6829, remed('BUY 1x 205P (vs your short 230P)', -2272, 16, 112, 'cap', pf('APP', 205, 'PUT')), 126, { strike: 230, right: 'P', qty: 1 });
  const fix = (headline, targets, extra = {}) => ({ headline, effect: 'cuts the stress loss', cost: 'each target priced below', targets, ...extra });
  const chk = (key, label, status, value_str, limit_str, f, note) => ({ key, label, status, value_str, limit_str, note, fix: f });
  return {
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
      { label: 'VIX 30/45 call spread', long_put: null, short_put: null, contracts: 3, instrument: 'VIX', long_strike: 30, short_strike: 45, cost_per_spread: 200, total_cost: 600, annual_bleed: 2400, crash_payoff_20: 3000, offsets_pct: 32, cvar_reduction: 2200, efficiency: 0.6, cagr_lift_pct: -0.1, cost_effective: false, dte_days: 60, vix_at_minus20: 42 },
    ],
    hedge_note: null,
    verdict: { level: 'Elevated', summary: 'Short-vol book with an upside tail 2.4× the downside.', actions: ['Cap the AMD and SMH short calls first.', 'Keep the SPX put spread as the crash backstop.'] },
    assumptions: { beta: 'Ticker betas vs SPY', mkt_vol_pct: 18, tail: 'fat-tailed Student-t', crash_prob_annual_pct: 5, hedge_rolls_per_year: 6 },
  };
};
module.exports = { baseTM, stockTM, defendMenu, defendHealthyMinimal, bookTailRisk };
