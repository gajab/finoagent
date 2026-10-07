// MOCK data for the Beta Technical visual preview — synthetic, deterministic, layout-check only (no network, no real tickers).
// Shapes follow src/types.ts so the real components, layer builders and odds code run unchanged.
const D60: number[][] = [[194.2,196.9,192,195.2,.55],[195.2,197.1,192,193.7,.73],[193.7,196.6,191.6,195.2,.96],[195.2,196.6,190.1,191.9,.49],[191.9,195,190.1,193.2,1],[193.2,195.2,190.5,191.7,.6],[191.7,193.5,188.9,189.6,.65],[189.6,191.7,188.7,190.8,.39],[190.8,192.8,187.1,188,.64],[188,192.5,185.8,191.2,.5],[191.2,192.6,189.5,191.1,.92],[191.1,194.8,189,192.7,.53],[192.7,197,191.5,195.7,1],[195.7,201.3,194.8,198.9,.58],[198.9,200,196.3,197.3,.67],[197.3,200.9,196.3,199.3,.35],[199.3,203.9,198,202.6,.9],[202.6,207.8,200.7,205.5,.86],[205.5,207.2,203.5,205.3,.39],[205.3,207.6,203.3,205.3,.92],[205.3,210.7,204,208.7,.76],[208.7,209.5,203.7,205.4,.49],[205.4,210.5,204.4,209.8,.57],[209.8,211,207.4,208.1,.35],[208.1,209.1,207.3,208.2,.59],[208.2,209.6,206.1,208.9,.75],[208.9,211.4,207.9,210.6,.58],[210.6,214.8,209.7,213.6,1],[213.6,216,210.2,211.6,.66],[211.6,215,210.8,214.3,.72],[214.3,216.5,212.2,215.4,.45],[215.4,216.1,212.8,215.1,.69],[215.1,217.7,213.6,216.8,.37],[216.8,218.4,213.2,215.6,.91],[215.6,218.3,214.5,216.5,.59],[216.5,218.9,214.5,218,.7],[218,223,216.8,221,.62],[221,223.1,218.4,220.8,.9],[220.8,223.2,218.7,221.2,.83],[221.2,222.9,219.6,221.9,.58],[221.9,222.5,220.1,220.8,.53],[220.8,221.9,217.8,219.6,.97],[219.6,222.7,217.3,221.3,.99],[221.3,223.6,219.1,220.4,.49],[220.4,221.4,216.9,217.9,.6],[217.9,220.6,215.6,218.8,.9],[218.8,220.3,216.3,218.1,.87],[218.1,221.9,216.3,221.2,1],[221.2,224,219.2,222,.66],[222,222.9,219.5,221.6,.57],[221.6,228.2,219.2,226.1,.76],[226.1,227.4,221.5,223.8,.82],[223.8,227.5,223,226.6,.56],[226.6,231.4,224.6,229.1,.45],[229.1,231.2,225.1,227.5,.78],[227.5,231.2,225.9,229.9,.44],[229.9,230.6,226.6,229,.77],[229,235.9,226.7,234.4,.79],[234.4,236.5,231.9,233.9,.49],[233.9,240,232.8,238.9,.63]];

function rng(seed: number) { let a = seed; return () => { a |= 0; a = (a + 0x6d2b79f5) | 0; let t = Math.imul(a ^ (a >>> 15), 1 | a); t = (t + Math.imul(t ^ (t >>> 7), 61 | t)) ^ t; return ((t ^ (t >>> 14)) >>> 0) / 4294967296; }; }
const pad = (n: number) => String(n).padStart(2, '0');

function walk(n: number, endPrice: number, vol: number, seed: number, label: (i: number) => string, volBase: number) {
  const r = rng(seed); const out: { t: string; o: number; h: number; l: number; c: number; v: number }[] = [];
  let p = 100;
  for (let i = 0; i < n; i++) {
    const o = p, c = p * (1 + (r() - 0.48) * vol), h = Math.max(o, c) * (1 + r() * vol * 0.5), l = Math.min(o, c) * (1 - r() * vol * 0.5);
    out.push({ t: label(i), o, h, l, c, v: Math.round(volBase * (0.6 + r() * 0.8)) }); p = c;
  }
  const k = endPrice / out[n - 1].c;
  return out.map(x => ({ ...x, o: +(x.o * k).toFixed(2), h: +(x.h * k).toFixed(2), l: +(x.l * k).toFixed(2), c: +(x.c * k).toFixed(2) }));
}
function dailyLabel(i: number, n: number) { const d = new Date(Date.UTC(2026, 9, 5)); let back = n - 1 - i; while (back > 0) { d.setUTCDate(d.getUTCDate() - 1); if (d.getUTCDay() !== 0 && d.getUTCDay() !== 6) back--; } return `${d.getUTCFullYear()}-${pad(d.getUTCMonth() + 1)}-${pad(d.getUTCDate())}`; }

export function candles(interval: string) {
  const SPOT = 238.9;
  let cs;
  if (interval === '1d') {
    const n = 260, hist = walk(n - 60, 194.2, 0.018, 7, i => dailyLabel(i, n), 40_000_000);
    cs = [...hist, ...D60.map(([o, h, l, c, v], i) => ({ t: dailyLabel(n - 60 + i, n), o, h, l, c, v: Math.round(v * 60_000_000) }))];
  } else if (interval === '1wk') {
    cs = walk(110, SPOT, 0.04, 11, i => { const d = new Date(Date.UTC(2026, 9, 5)); d.setUTCDate(d.getUTCDate() - 7 * (109 - i)); return `${d.getUTCFullYear()}-${pad(d.getUTCMonth() + 1)}-${pad(d.getUTCDate())}`; }, 200_000_000);
  } else {
    const n = 420, step = interval === '1h' ? 60 : 15;
    cs = walk(n, SPOT, interval === '1h' ? 0.006 : 0.003, 13, i => { const m = (n - 1 - i) * step; const d = new Date(Date.UTC(2026, 9, 5, 20, 0) - m * 60_000); return `${d.getUTCFullYear()}-${pad(d.getUTCMonth() + 1)}-${pad(d.getUTCDate())} ${pad(d.getUTCHours())}:${pad(d.getUTCMinutes())}`; }, 5_000_000);
  }
  const lows = cs.map(c => c.l), highs = cs.map(c => c.h);
  return { ticker: 'NVDA', candles: { interval, candles: cs, count: cs.length, spot: cs[cs.length - 1].c, as_of: '2026-10-05', range: { low: Math.min(...lows), high: Math.max(...highs) } } };
}

const setup = (o: any) => ({
  rank: 1, type: 'trend_continuation', direction: 'long', regime_fit: 'aligned', confidence: 'medium', score: 7,
  entry: { low: 232.5, high: 234.8, level: 233.67, label: 'Pullback zone' }, stop: { level: 229.87, label: 'Below the demand block' },
  targets: [{ level: 259.31, label: 'T1', rr: 6.75 }, { level: 261.98, label: 'extended (T2)', rr: 7.4 }], risk_reward: 6.75,
  sizing: { risk_per_share: 3.8, target_move_pct: 8.5, within_expected_move: true, note: null },
  options: { structure: 'Bull call spread', detail: 'Buy the 240 call, sell the 260 call', bias: 'bullish', expiry: { date: '2026-11-06', dte: 32 } },
  equity_plan: null, options_plan: { available: false, structure: 'Bull call spread', note: 'Mock data — no option chain' },
  what_to_watch: ['A daily close back above the 20-day average keeps the pullback shallow.', 'Gamma flip near 222 is the line dealers defend.'],
  entry_style: { type: 'pullback', label: 'Buy the pullback', note: 'Wait for price to come to the demand zone.', distance_pct: -2.2 },
  horizon: { label: 'Swing', style: 'swing', est_days: 9, atrs_to_t1: 3.6, note: '' }, from_current: { to_entry_pct: -2.2, to_t1_pct: 8.5 },
  thesis: 'Trending up with dealers long gamma: buy the dip into the demand block, with the stop beneath it.', evidence: ['market structure bullish', 'daily trend up', 'above 50-day MA'], style: 'swing', ...o,
});

export const tradeSetups = {
  ticker: 'NVDA', cached: true,
  trade_setups: {
    price: 238.9, as_of: '2026-10-05 15:58',
    context: {
      bias: { direction: 'bullish', strength: 'moderate', score: 2.6, rationale: 'Market structure is bullish.', regime: 'trending', confirmations: [
        { signal: 'market_structure', reads: 'bullish', detail: 'market structure is bullish' }, { signal: 'daily_trend', reads: 'bullish', detail: 'daily trend is up' },
        { signal: 'gamma', reads: 'bullish', detail: 'spot is above the gamma flip' }, { signal: 'rsi', reads: 'bullish', detail: 'RSI 66 — bullish momentum' },
        { signal: 'macd', reads: 'bullish', detail: 'MACD above signal' }, { signal: 'sma50', reads: 'bullish', detail: 'price above the 50-day MA' },
        { signal: 'bollinger', reads: 'bullish', detail: 'upper half of the Bollinger band' }] },
      regime: { label: 'trending', confidence: 'medium', hurst: 0.58, note: 'Buy dips', favored: ['trend_continuation'] },
      expected_move: { pct_30d: 8.2, upper: 258.5, lower: 219.3, iv: 31.5 },
      dealer: { gamma: 'long', flip: 222.31, note: 'Long gamma' },
      trend_alignment: { daily: 'up', h4: 'up', h1: 'down' },
    },
    confluence_zones: [
      { center: 227.1, low: 226, high: 228.2, kind: 'support', score: 6.5, n_sources: 3, has_magnet: false, sources: [], distance_pct: -4.9 },
      { center: 222.2, low: 221.2, high: 223.2, kind: 'support', score: 8, n_sources: 4, sources: [], distance_pct: -7 },
      { center: 214.96, low: 213.5, high: 216.2, kind: 'support', score: 7, n_sources: 3, sources: [], distance_pct: -10 },
      { center: 249.5, low: 248.5, high: 250.6, kind: 'resistance', score: 5, n_sources: 3, sources: [], distance_pct: 4.4 },
      { center: 255.2, low: 254.4, high: 256.1, kind: 'resistance', score: 4.5, n_sources: 2, sources: [], distance_pct: 6.8 },
    ],
    setups: [setup({}), setup({ rank: 2, type: 'breakout', entry: { low: null, high: null, level: 241.2, label: 'Break of 241' }, stop: { level: 236.4, label: 'Back inside range' },
      targets: [{ level: 253.9, label: 'T1', rr: 2.65 }], risk_reward: 2.65, entry_style: { type: 'breakout', label: 'Buy the break', note: 'Enter on a close above 241.', distance_pct: 1 }, from_current: { to_entry_pct: 1, to_t1_pct: 6.3 } }),
      setup({ rank: 1, type: 'position_accumulate', style: 'position', entry: { low: 214, high: 218, level: 216, label: 'Value zone' }, stop: { level: 198, label: 'Weekly structure' }, targets: [{ level: 276, label: 'T1', rr: 3.3 }], risk_reward: 3.3,
        horizon: { label: 'Position', style: 'position', est_days: 60, note: '' } })],
    price_series: null, meta: { sources_ok: {} },
  },
};
export const dayTradeSetups = { ticker: 'NVDA', day_trade_setups: { price: 238.9, as_of: 'x', atr: 3.1, setups: [setup({ type: 'vwap_pullback_long', style: 'day', entry: { low: 237.9, high: 238.4, level: 238.2, label: 'VWAP' }, stop: { level: 236.9, label: 'Below VWAP' }, targets: [{ level: 241.4, label: 'T1', rr: 2.5 }], risk_reward: 2.5, horizon: { label: 'Day trade', style: 'day', note: '' }, entry_style: { type: 'pullback', label: 'Buy the VWAP hold', note: '', distance_pct: -0.3 }, from_current: { to_entry_pct: -0.3, to_t1_pct: 1.0 } })] } };

const tf = (poc: number, vah: number, val: number) => ({ label: 'Profile', poc, vah, val, lvns: [{ price: +(poc - 9).toFixed(2) }], bins: [] });
export const microstructure = { ticker: 'NVDA', microstructure: { timeframe_profiles: { daily: tf(210.86, 225, 198), h4: tf(231.4, 236, 226), h1: tf(236.2, 239, 233.5) }, naked_pocs: [{ price: 188.5 }], avwap: { ytd: { label: 'YTD AVWAP', value: 214.2 }, earnings: { label: 'Earnings AVWAP', value: 229.7 }, high_52w: null, low_52w: null } } };
const block = (swH: number, swL: number, ob: any[], fvg: any[], pools: any[]) => ({ trend: 'up', structure: { last_event: { type: 'BOS', level: swL + 3 }, recent_swing_high: swH, recent_swing_low: swL }, order_blocks: ob, fair_value_gaps: fvg, liquidity_pools: pools });
export const marketStructure = { ticker: 'NVDA', market_structure: { timeframes: {
  daily: block(240, 221, [{ type: 'bearish', top: 228.2, bottom: 226, mitigated: false }, { type: 'bullish', top: 223.2, bottom: 221.2, mitigated: false }], [{ type: 'bullish', top: 215, bottom: 212, filled: false }], [{ type: 'BSL', price: 241.5 }, { type: 'SSL', price: 219.8 }]),
  h4: null, h1: block(240, 235.4, [{ type: 'bullish', top: 236.5, bottom: 235.2, mitigated: false }], [], [{ type: 'SSL', price: 234.1 }]) }, confluence: [{ bias: 'bullish', zone: [226, 229] }] } };
export const regime = { ticker: 'NVDA', regime: { timeframes: { daily: { regime: 'trending', hurst: 0.58, efficiency_ratio: 0.41, confidence: 'medium' }, h4: null }, zscore: { vwap: 228.4, z: 0.8, state: 'ok' } } };
export const dealer = { ticker: 'NVDA', dealer_positioning: { price: 238.9, as_of: 'x', net_gex: { value: 1, value_millions: 120, sign: 'long', label: 'Long gamma' }, gamma_flip: { level: 222.31, distance_pct: -7, side: 'below', note: '' },
  gamma_levels: { call_resistance: { strike: 260 }, put_support: { strike: 215 }, hvl: { strike: 230 }, gamma_flip: null }, walls: { call_wall: { strike: 260 }, put_wall: { strike: 215 }, by_strike: [] },
  expected_move: { em_30d: { upper: 258.5, lower: 219.3, move_pct: 8.2 }, em_45d: { upper: 262, lower: 216, move_pct: 9.6 } }, expirations_used: [], price_series: null } };

export const technical = {
  timeframe: 'medium_term', timestamps: ['x'], prices: [236.1, 237.4, 238.9], volumes: [1, 2, 3], highs: [], lows: [], rsiValues: [], rsiTimestamps: [],
  currentRSI: 66, rsiSignal: 'Bullish momentum', supportLevel: 214.96, resistanceLevel: 262, analysisSummary: '',
  macd: { macdLine: 3.1, signalLine: 2.6, histogram: 0.5, signal: 'bullish', crossover: 'none', macdValues: [], signalValues: [], histogramValues: [], timestamps: [] },
  bollingerBands: { upper: 246, middle: 226, lower: 206, bandwidthPct: 17.6, percentB: 0.82, position: 'neutral' },
  movingAverages: { sma50: 221.4, priceVsSma50: 'above', sma200: 198.7, priceVsSma200: 'above', goldenDeathCross: 'golden_cross' },
  emaCrossover: { ema12: 233.4, ema26: 226.8, signal: 'bullish' },
  volumeAnalysis: { phase: 'accumulation', priceTrend: 'rising', priceChangePct: 6.2, volumeTrend: 'increasing', volumeChangePct: 14, avgRecentVolume: 1, avgOlderVolume: 1, bigMoneyAnalysis: 'Accumulation' },
};
