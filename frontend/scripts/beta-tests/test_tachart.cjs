// Tests for the Beta chart's level model, fed by the REAL classic layer builders (taLayers.ts).
const path = require('path');
const C = require(path.join(process.env.BUILD_DIR, 'betaTaChartModel.cjs'));
const L = require(path.join(process.env.BUILD_DIR, 'taLayers.cjs'));
const assert = require('assert');
let n = 0; const ok = (name, fn) => { fn(); n++; console.log('  ok -', name); };
console.log('betaTaChartModel');

const tf = (poc, vah, val) => ({ label: 'Macro', poc, vah, val, lvns: [{ price: poc - 9 }] });
const micro = { timeframe_profiles: { daily: tf(210.86, 225, 198), h4: tf(231, 236, 226), h1: tf(236, 239, 233) },
  naked_pocs: [{ price: 188.5 }], avwap: { ytd: { label: 'YTD AVWAP', value: 214.2 }, earnings: null, high_52w: null, low_52w: null } };
const tfBlock = (swH, swL, ob, fvg, pools) => ({ trend: 'up', structure: { last_event: { type: 'BOS', level: swL + 3 }, recent_swing_high: swH, recent_swing_low: swL },
  order_blocks: ob, fair_value_gaps: fvg, liquidity_pools: pools });
const ms = { timeframes: {
  daily: tfBlock(246, 221, [{ type: 'bearish', top: 228.2, bottom: 226, mitigated: false }, { type: 'bullish', top: 223.2, bottom: 221.2, mitigated: true }], [{ type: 'bullish', top: 215, bottom: 212, filled: false }], [{ type: 'BSL', price: 247 }]),
  h4: null, h1: tfBlock(240, 235, [{ type: 'bullish', top: 236.5, bottom: 235.2, mitigated: false }], [], [{ type: 'SSL', price: 234.1 }]) },
  confluence: [{ bias: 'bullish', zone: [226, 229] }] };
const dealer = { net_gex: { value_millions: 120, sign: 'long', label: 'Long gamma' }, gamma_flip: { level: 222.31 },
  gamma_levels: { call_resistance: { strike: 260 }, put_support: { strike: 215 }, hvl: { strike: 230 } }, walls: {},
  expected_move: { em_30d: { upper: 255, lower: 223, move_pct: 7 }, em_45d: { upper: 262, lower: 218, move_pct: 9 } } };
const regime = { timeframes: {}, zscore: { vwap: 228.4, z: 0.8, state: 'ok' } };
const layers = [...L.vpLayers(micro), ...L.structureLayers(ms), ...L.liquidityLayers(ms), ...L.gammaLayers(dealer), ...L.regimeLayers(regime)];

ok('the tier follows the candle resolution: daily/weekly -> daily, hourly and 15m -> hourly', () => {
  assert.equal(C.tierForInterval('1d'), 'daily'); assert.equal(C.tierForInterval('1wk'), 'daily');
  assert.equal(C.tierForInterval('1h'), 'h1'); assert.equal(C.tierForInterval('15m'), 'h1');
});

ok('rgb() colours become hex; hex passes through', () => {
  assert.equal(C.toHex('rgb(34,197,94)'), '#22c55e'); assert.equal(C.toHex('rgba(239, 68, 68, 0.5)'), '#ef4444'); assert.equal(C.toHex('#abc123'), '#abc123');
});

ok('plan layer: entry, stop, T1, T2 at the exact prices, with the right colours; an entry ZONE becomes a band', () => {
  const lv = C.levelsFromPlan({ direction: 'long', entry: 233.67, entryLow: 232.5, entryHigh: 234.8, stop: 229.87, t1: 259.31, t2: 261.98 });
  const by = Object.fromEntries(lv.map(x => [x.id, x]));
  assert.equal(by['plan-entry'].price, 233.67); assert.equal(by['plan-stop'].price, 229.87); assert.equal(by['plan-t1'].price, 259.31); assert.equal(by['plan-t2'].price, 261.98);
  assert.equal(by['plan-stop'].color, C.COLOR.down); assert.equal(by['plan-t1'].color, C.COLOR.up);
  assert.equal(by['plan-zone'].kind, 'band'); assert.equal(by['plan-zone'].top, 234.8); assert.equal(by['plan-zone'].bottom, 232.5);
  assert.equal(by['plan-entry'].label, 'Entry 233.67');
  assert.equal(C.levelsFromPlan(null).length, 0);
  assert.ok(!C.levelsFromPlan({ direction: 'long', entry: 100, entryLow: 100, entryHigh: 100, stop: 95, t1: 110, t2: null }).some(x => x.id === 'plan-zone'), 'no zero-width zone');
});

ok('confluence zones: bands above spot are resistance-coloured, below are support-coloured, far ones are dropped, capped', () => {
  const zones = [{ center: 245, low: 243, high: 247, kind: 'resistance', score: 5, n_sources: 3, sources: [], distance_pct: 2.5 },
    { center: 222, low: 220, high: 224, kind: 'support', score: 6, n_sources: 4, sources: [], distance_pct: -7 },
    { center: 300, low: 298, high: 302, kind: 'resistance', score: 6, n_sources: 3, sources: [], distance_pct: 25 }];
  const lv = C.levelsFromZones(zones, 238.9);
  assert.equal(lv.length, 2); assert.equal(lv[0].color, C.COLOR.rose); assert.equal(lv[1].color, C.COLOR.up);
  assert.equal(C.levelsFromZones(zones, null).length, 0); assert.equal(C.levelsFromZones(undefined, 238.9).length, 0);
});

ok('dealer layer: flip, walls, HVL and the 30-day band at the engine prices; the 45-day band and the Net-GEX badge are NOT drawn', () => {
  const lv = C.levelsFromLayers(layers, 'dealer', 'daily');
  const prices = lv.filter(x => x.kind === 'line').map(x => x.price).sort((a, b) => a - b);
  assert.deepEqual(prices, [215, 222.31, 230, 260]);
  const band = lv.find(x => x.kind === 'band'); assert.equal(band.top, 255); assert.equal(band.bottom, 223);
  assert.equal(lv.filter(x => x.kind === 'band').length, 1);
});

ok('value layer: POC + value area + naked POC + anchored VWAP + regime VWAP for the matching tier only', () => {
  const d = C.levelsFromLayers(layers, 'value', 'daily');
  assert.ok(d.some(x => x.kind === 'line' && x.price === 210.86), 'daily POC');
  assert.ok(d.some(x => x.kind === 'band' && x.top === 225 && x.bottom === 198), 'daily VA');
  assert.ok(d.some(x => x.price === 188.5) && d.some(x => x.price === 214.2) && d.some(x => x.price === 228.4));
  assert.ok(!d.some(x => x.price === 236), 'the hourly POC is not shown on a daily chart');
  const h = C.levelsFromLayers(layers, 'value', 'h1'); assert.ok(h.some(x => x.price === 236) && !h.some(x => x.price === 210.86));
});

ok('blocks / structure / liquidity: unmitigated blocks and unfilled gaps only, swings+BOS, pools and MTF confluence — for the tier on screen', () => {
  const b = C.levelsFromLayers(layers, 'blocks', 'daily');
  assert.equal(b.filter(x => x.kind === 'band').length, 2, 'one supply OB + one FVG; the mitigated demand block is excluded');
  assert.ok(b.some(x => x.top === 228.2 && x.bottom === 226));
  const s = C.levelsFromLayers(layers, 'structure', 'daily').map(x => x.price).sort((a, c) => a - c);
  assert.deepEqual(s, [221, 224, 246]);    // swing low, last BOS level (swing low + 3), swing high
  const q = C.levelsFromLayers(layers, 'liquidity', 'daily');
  assert.ok(q.some(x => x.kind === 'line' && x.price === 247) && q.some(x => x.kind === 'band' && x.top === 229 && x.bottom === 226));
  assert.ok(!C.levelsFromLayers(layers, 'liquidity', 'h1').some(x => x.price === 247));
});

ok('a layer that has not loaded yet draws nothing; unknown layers draw nothing; non-finite prices never reach the chart', () => {
  assert.deepEqual(C.levelsFromLayers(undefined, 'dealer', 'daily'), []); assert.deepEqual(C.levelsFromLayers(layers, 'plan', 'daily'), []);
  const bad = [{ id: 'gamma_flip', group: 'k', label: 'x', tone: '', color: 'rgb(1,2,3)', lines: [{ price: NaN, label: 'x' }, { price: 10, label: 'ok' }], json: {} }];
  assert.equal(C.levelsFromLayers(bad, 'dealer', 'daily').length, 1);
});

ok('y-range: covers every candle, pulls in levels within 35% of the range, ignores a far-off level, never inverts', () => {
  const candles = [{ h: 250, l: 200 }, { h: 240, l: 210 }];
  const near = { id: 'a', layer: 'plan', kind: 'line', price: 259, label: '', color: '', dashed: false, priority: 1 };
  const far = { id: 'b', layer: 'plan', kind: 'line', price: 400, label: '', color: '', dashed: false, priority: 1 };
  const r = C.yRange(candles, [near, far]);
  assert.ok(r.min < 200 && r.max > 259 && r.max < 300, JSON.stringify(r));
  const noLv = C.yRange(candles, []); assert.ok(noLv.min < 200 && noLv.max > 250 && noLv.max < 260);
  assert.equal(C.yRange([], []), null); assert.equal(C.yRange([{ h: null, l: null }], []), null);
  const flat = C.yRange([{ h: 100, l: 100 }], []); assert.ok(flat.max > flat.min);
  const band = { id: 'c', layer: 'blocks', kind: 'band', price: 195, top: 197, bottom: 193, label: '', color: '', dashed: false, priority: 1 };
  assert.ok(C.yRange(candles, [band]).min < 193);
});

ok('inRange: a line must be inside; a band is drawn if any part of it is (it is clipped); its label needs the midpoint on screen', () => {
  const r = { min: 100, max: 200 };
  assert.ok(C.inRange({ kind: 'line', price: 150 }, r) && !C.inRange({ kind: 'line', price: 201 }, r));
  assert.ok(C.inRange({ kind: 'band', price: 150, top: 160, bottom: 140 }, r));
  assert.ok(C.inRange({ kind: 'band', price: 205, top: 210, bottom: 190 }, r), 'straddling the top edge is still drawn');
  assert.ok(!C.inRange({ kind: 'band', price: 230, top: 240, bottom: 220 }, r) && !C.inRange({ kind: 'band', price: 50, top: 60, bottom: 40 }, r));
  assert.ok(C.labelVisible({ kind: 'band', price: 150 }, r) && !C.labelVisible({ kind: 'band', price: 205 }, r));
});
ok('gutter text is short and ends in the price: no $, no timeframe prefix, no parenthetical; the price is added only when missing', () => {
  assert.equal(C.shortLabel('Call Resistance $260', 260), 'Call Resistance 260');
  assert.equal(C.shortLabel('γ-flip $222.31', 222.31), 'γ-flip 222.31');
  assert.equal(C.shortLabel('YTD AVWAP', 214.2), 'YTD AVWAP 214.20');
  assert.equal(C.shortLabel('50d VWAP (z 0.8)', 228.4), '50d VWAP 228.40');
  assert.equal(C.shortLabel('Daily swing H', 246), 'swing H 246.00');
  assert.equal(C.rangeText(226, 228.2), '226.0–228.2'); assert.equal(C.rangeText(219.3, 258.5), '219–259');
});

ok('layer labels through the real builders: bands show their range, lines show their price, and the full name stays as the hover title', () => {
  const b = C.levelsFromLayers(layers, 'blocks', 'daily').find(x => x.kind === 'band' && x.top === 228.2);
  assert.equal(b.label, 'Supply OB 226.0–228.2'); assert.equal(b.title, 'Daily Supply OB');
  const d = C.levelsFromLayers(layers, 'dealer', 'daily');
  assert.equal(d.find(x => x.kind === 'band').label, '30d ±1σ 223–255');
  assert.ok(d.some(x => x.label === 'Call Resistance 260') && d.some(x => x.label === 'γ-flip 222.31'));
  const v = C.levelsFromLayers(layers, 'value', 'daily');
  assert.ok(v.some(x => x.label === 'YTD AVWAP 214.20') && v.some(x => x.label === '50d VWAP 228.40'));
  for (const l of [...b ? [b] : [], ...d, ...v]) assert.ok(l.label.length <= 24, `too long for the gutter: ${l.label}`);
  const z = C.levelsFromPlan({ direction: 'long', entry: 233.67, entryLow: 232.5, entryHigh: 234.8, stop: 229.87, t1: 259.31, t2: null }).find(x => x.id === 'plan-zone');
  assert.equal(z.label, 'Zone 232.5–234.8');
});

ok('dedupe: a line on top of a higher-priority level is dropped, a different price is kept, bands are never dropped', () => {
  const mk = (id, kind, price, priority, extra = {}) => ({ id, layer: 'levels', kind, price, label: id, color: '', dashed: false, priority, ...extra });
  const out = C.dedupeLevels([mk('zone', 'band', 214.96, 6, { top: 216, bottom: 213.5 }), mk('swing', 'line', 214.96, 5), mk('other', 'line', 230, 5), mk('twin-a', 'line', 100, 5), mk('twin-b', 'line', 100.05, 5)]);
  const ids = out.map(x => x.id);
  assert.ok(ids.includes('zone') && ids.includes('other') && !ids.includes('swing'));
  assert.ok(ids.includes('twin-a') && !ids.includes('twin-b'), 'equal priority keeps the first only');
  assert.equal(C.dedupeLevels([mk('a', 'band', 1, 1, { top: 2, bottom: 0.5 }), mk('b', 'band', 1, 1, { top: 2, bottom: 0.5 })]).length, 2);
});
console.log(`\n${n} passed`);
