// Tests for the Beta Technical model: verdict, lenses, disagreements, plan, level table, gutter label layout.
const path = require('path');
const M = require(path.join(process.env.BUILD_DIR, 'betaTaModel.cjs'));
const assert = require('assert');
let n = 0; const ok = (name, fn) => { fn(); n++; console.log('  ok -', name); };
console.log('betaTaModel');

const conf = (signal, reads, detail) => ({ signal, reads, detail });
const ctx = (direction, strength, confirmations, extra = {}) => ({
  bias: { direction, strength, score: 2, rationale: '', regime: 'trending', confirmations },
  regime: { label: 'trending', confidence: 'medium', hurst: 0.58, note: null, favored: [] },
  expected_move: { pct_30d: 8.2, upper: 255, lower: 223, iv: 31.5 },
  dealer: { gamma: 'long', flip: 222.31, note: 'Long gamma' },
  trend_alignment: { daily: 'up', h4: 'up', h1: 'down' }, ...extra,
});
const BULL = [
  conf('market_structure', 'bullish', 'market structure is bullish'), conf('daily_trend', 'bullish', 'daily trend is up'),
  conf('gamma', 'bullish', 'spot is above the gamma flip'), conf('rsi', 'bullish', 'RSI 61 — bullish momentum'),
  conf('macd', 'bullish', 'MACD above signal'), conf('sma50', 'bullish', 'price above the 50-day MA'),
];
const tech = (o = {}) => ({ currentRSI: 61, macd: { histogram: 0.4, signal: 'bullish', crossover: 'none' },
  bollingerBands: { percentB: 0.7, upper: 250, middle: 230, lower: 210, position: 'neutral' },
  movingAverages: { sma50: 225, priceVsSma50: 'above' }, supportLevel: 214.96, resistanceLevel: 262,
  volumeAnalysis: { phase: 'accumulation', priceTrend: 'rising', priceChangePct: 4, volumeTrend: 'increasing', volumeChangePct: 12, bigMoneyAnalysis: '' }, ...o });
const byKey = ls => Object.fromEntries(ls.map(l => [l.key, l]));

ok('verdict IS the engine bias: direction, conviction from strength, a headline from the regime', () => {
  const v = M.deriveVerdict(ctx('bullish', 'moderate', BULL));
  assert.equal(v.dir, 'long'); assert.equal(v.label, 'Lean long'); assert.equal(v.conviction, 'medium'); assert.equal(v.mixed, false);
  assert.match(v.headline, /pullbacks/i);
  assert.equal(M.deriveVerdict(ctx('bearish', 'strong', [])).conviction, 'high');
  assert.equal(M.deriveVerdict(ctx('bearish', 'weak', [])).conviction, 'low');
  assert.equal(M.deriveVerdict(null), null);
});

ok('neutral with reads on BOTH sides is "Mixed", neutral with none is "No clear edge" — never a made-up direction', () => {
  const mixed = M.deriveVerdict(ctx('neutral', 'weak', [conf('rsi', 'bullish', 'a'), conf('macd', 'bearish', 'b')]));
  assert.equal(mixed.mixed, true); assert.equal(mixed.label, 'Mixed — no clear edge'); assert.equal(mixed.conviction, null);
  const none = M.deriveVerdict(ctx('neutral', 'weak', [conf('rsi', 'bullish', 'a')]));
  assert.equal(none.mixed, false); assert.equal(none.label, 'No clear edge');
});

ok('lenses regroup the engine votes; all-aligned = supports, mixed inside a lens = caution, all-opposed = against', () => {
  const L = byKey(M.deriveLenses(ctx('bullish', 'moderate', BULL), tech(), 238.9));
  assert.equal(L.trend.state, 'supports'); assert.equal(L.momentum.state, 'supports'); assert.equal(L.dealer.state, 'supports');
  assert.ok(L.trend.counted && L.momentum.counted && L.dealer.counted);
  assert.ok(!L.stretch.counted && !L.volume.counted && !L.regime.counted);
  const mixedMom = byKey(M.deriveLenses(ctx('bullish', 'moderate', [conf('rsi', 'bullish', 'RSI 58'), conf('macd', 'bearish', 'MACD below signal')]), tech(), 238.9));
  assert.equal(mixedMom.momentum.state, 'caution'); assert.equal(mixedMom.momentum.lean, 'mixed');
  const against = byKey(M.deriveLenses(ctx('bullish', 'moderate', [...BULL.slice(0, 2), conf('rsi', 'bearish', 'RSI 40')]), tech(), 238.9));
  assert.equal(against.momentum.state, 'against');
});

ok('a lens the engine did not vote on says so honestly: "neutral" (nothing signalled) vs "not scored" (no data)', () => {
  const L = byKey(M.deriveLenses(ctx('bullish', 'moderate', [conf('market_structure', 'bullish', 'x')]), tech({ currentRSI: 50, macd: { histogram: 0.1, signal: 'bullish', crossover: 'none' } }), 238.9));
  assert.equal(L.momentum.state, 'neutral');
  const bare = byKey(M.deriveLenses({ bias: { direction: 'bullish', strength: 'weak', score: 1, rationale: '', regime: '', confirmations: [] }, regime: { label: null, confidence: null, hurst: null, note: null, favored: null }, expected_move: null, dealer: null, trend_alignment: null }, null, null));
  for (const k of ['trend', 'momentum', 'dealer', 'stretch', 'volume', 'regime']) assert.equal(bare[k].state, 'not_scored', k);
});

ok('stretch: overbought is a caution for a long and an argument for a short; oversold the reverse', () => {
  const lens = (dir, rsi) => byKey(M.deriveLenses(ctx(dir, 'moderate', []), tech({ currentRSI: rsi, bollingerBands: { percentB: 0.5 } }), 225)).stretch.state;
  assert.equal(lens('bullish', 76), 'caution'); assert.equal(lens('bearish', 76), 'supports');
  assert.equal(lens('bullish', 24), 'supports'); assert.equal(lens('bearish', 24), 'caution');
  assert.equal(lens('bullish', 55), 'neutral'); assert.equal(lens('neutral', 76), 'neutral');
  // stretch from distance to the 50-day alone
  const far = byKey(M.deriveLenses(ctx('bullish', 'moderate', []), tech({ currentRSI: 55, bollingerBands: { percentB: 0.5 }, movingAverages: { sma50: 200 } }), 240)).stretch;
  assert.equal(far.state, 'caution'); assert.match(far.detail, /\+20\.0% vs 50-day/);
});

ok('volume: accumulation supports a long, distribution fights it, a weak rally is a caution; mirrored for shorts', () => {
  const v = (dir, phase) => byKey(M.deriveLenses(ctx(dir, 'moderate', []), tech({ volumeAnalysis: { phase, volumeTrend: 'increasing', volumeChangePct: 5 } }), 225)).volume.state;
  assert.equal(v('bullish', 'accumulation'), 'supports'); assert.equal(v('bullish', 'distribution'), 'against'); assert.equal(v('bullish', 'weak_rally'), 'caution');
  assert.equal(v('bearish', 'distribution'), 'supports'); assert.equal(v('bearish', 'accumulation'), 'against'); assert.equal(v('bearish', 'weak_decline'), 'caution');
  assert.equal(v('bullish', 'neutral'), 'neutral');
});

ok('regime: a trend the daily structure agrees with supports; a counter-trend regime fights; mean-reverting cautions a directional call', () => {
  const r = (dir, label, daily) => byKey(M.deriveLenses(ctx(dir, 'moderate', [], { regime: { label, confidence: 'high', hurst: 0.6, note: null, favored: [] }, trend_alignment: { daily, h4: 'up', h1: 'up' } }), tech(), 225)).regime.state;
  assert.equal(r('bullish', 'trending', 'up'), 'supports'); assert.equal(r('bullish', 'trending', 'down'), 'against');
  assert.equal(r('bearish', 'trending', 'down'), 'supports'); assert.equal(r('bullish', 'mean_reverting', 'up'), 'caution');
  assert.equal(r('neutral', 'mean_reverting', 'up'), 'neutral'); assert.equal(r('bullish', 'transitional', 'up'), 'neutral');
});

ok('disagreements list only lenses that argue with the call, plus engine warnings and a below-break-even odds flag', () => {
  const c = ctx('bullish', 'moderate', BULL);
  const lenses = M.deriveLenses(c, tech({ currentRSI: 78 }), 238.9);
  const plan = { sizing: { within_expected_move: false, note: 'Target is ~13% away vs the ±8% expected move.' }, event_risk: { warning: 'Earnings in 6 days.' }, regime_fit: 'counter_regime' };
  const d = M.disagreements(lenses, M.deriveVerdict(c), plan, { ok: true, edgePts: -4.2 });
  const who = d.map(x => x.who);
  assert.ok(who.includes('Stretch') && who.includes('Target') && who.includes('Event') && who.includes('Regime') && who.includes('Odds'));
  assert.ok(!who.includes('Trend'));
  assert.equal(M.disagreements(lenses, M.deriveVerdict(ctx('neutral', 'weak', [])), null, null).length, 0);   // no verdict, nothing to argue with
  assert.ok(!M.disagreements(lenses, M.deriveVerdict(c), null, { ok: true, edgePts: -1 }).some(x => x.who === 'Odds'));
});

const plan = (o = {}) => ({ direction: 'long', style: 'swing', entry: { low: 232.5, high: 234.8, level: 233.67, label: '' }, stop: { level: 229.87, label: '' },
  targets: [{ level: 259.31, label: 'T1', rr: 6.75 }, { level: 261.98, label: 'T2', rr: 7.4 }], options: { expiry: { date: '2026-11-06', dte: 32 } }, ...o });

ok('setups split by style exactly like the classic tabs', () => {
  const data = { setups: [plan({ style: 'swing' }), plan({ style: 'position' }), plan({ style: undefined }), plan({ style: 'qullamaggie' })] };
  assert.equal(M.setupsForStyle(data, 'swing', null).length, 3); assert.equal(M.setupsForStyle(data, 'position', null).length, 1);
  assert.equal(M.setupsForStyle(data, 'day', [plan()]).length, 1); assert.equal(M.setupsForStyle(data, 'day', null).length, 0);
  assert.equal(M.setupsForStyle(null, 'swing', null).length, 0);
});

ok('plan levels: entry level, else the middle of the zone; refuse neutral or target-less plans', () => {
  const lv = M.planLevels(plan());
  assert.deepEqual([lv.entry, lv.stop, lv.t1, lv.t2, lv.direction], [233.67, 229.87, 259.31, 261.98, 'long']);
  assert.equal(M.planLevels(plan({ entry: { low: 100, high: 102, level: null } })).entry, 101);
  assert.equal(M.planLevels(plan({ direction: 'neutral' })), null); assert.equal(M.planLevels(plan({ targets: [] })), null);
  assert.equal(M.planLevels(plan({ stop: { level: null } })), null); assert.equal(M.planLevels(null), null);
});

ok('window: the engine expiry for a swing, at least 90 days for a position, one trading session for a day trade, 30 when the engine gave none', () => {
  assert.equal(M.planWindowDays(plan(), 'swing'), 32); assert.equal(M.planWindowDays(plan(), 'position'), 90);
  assert.equal(M.planWindowDays(plan({ options: { expiry: { dte: 120 } } }), 'position'), 120);
  assert.ok(Math.abs(M.planWindowDays(plan(), 'day') - 365 / 252) < 1e-12);
  assert.equal(M.planWindowDays(plan({ options: {} }), 'swing'), 30); assert.equal(M.planWindowDays(null, 'swing'), null);
  assert.equal(M.windowLabel(M.SESSION_DAYS, 'day'), '1 session'); assert.equal(M.windowLabel(32, 'swing'), '32 days'); assert.equal(M.windowLabel(89.6, 'position'), '90 days');
});

ok('odds use the engine implied vol (percent -> decimal) and refuse, with a reason, when it is missing', () => {
  const c = ctx('bullish', 'moderate', BULL);
  const o = M.planOdds(plan(), c, 238.9, 'swing');
  assert.ok(o.ok); assert.equal(o.iv, 0.315); assert.equal(o.days, 32);
  assert.deepEqual(M.planOdds(plan(), { ...c, expected_move: { pct_30d: 8, upper: 1, lower: 1, iv: null } }, 238.9, 'swing'), { ok: false, reason: 'no-iv' });
  assert.deepEqual(M.planOdds(plan(), { ...c, expected_move: null }, 238.9, 'swing'), { ok: false, reason: 'no-iv' });
  assert.equal(M.planOdds(plan({ direction: 'neutral' }), c, 238.9, 'swing'), null); assert.equal(M.planOdds(plan(), c, null, 'swing'), null);
});

ok('level table: plan levels always shown, the nearest few per side, deduped, sorted high to low, signed distances', () => {
  const zones = Array.from({ length: 12 }, (_, i) => ({ center: 200 + i * 5, low: 0, high: 0, kind: i < 6 ? 'support' : 'resistance', score: 6, n_sources: 3, sources: [], distance_pct: 0 }));
  const rows = M.levelRows(238.9, M.planLevels(plan()), zones, tech(), ctx('bullish', 'moderate', BULL));
  const ids = rows.map(r => r.id);
  for (const id of ['plan-entry', 'plan-stop', 'plan-t1', 'plan-t2']) assert.ok(ids.includes(id), id);
  assert.ok(rows.filter(r => r.kind !== 'plan' && r.price > 238.9).length <= 5 && rows.filter(r => r.kind !== 'plan' && r.price <= 238.9).length <= 5);
  for (let i = 1; i < rows.length; i++) assert.ok(rows[i - 1].price >= rows[i].price);
  const t1 = rows.find(r => r.id === 'plan-t1'); assert.ok(Math.abs(t1.distPct - ((259.31 - 238.9) / 238.9) * 100) < 1e-9);
  assert.equal(M.levelRows(null, null, [], null, null).length, 0);
});

// ── gutter layout ────────────────────────────────────────────────────────────────────────────────────────────────
const rng = (seed => () => (seed = (seed * 1664525 + 1013904223) % 4294967296) / 4294967296)(42);
ok('gutter: well-spaced labels are left exactly where they are', () => {
  const r = M.layoutGutter([{ id: 'a', y: 20, priority: 1 }, { id: 'b', y: 80, priority: 1 }, { id: 'c', y: 160, priority: 1 }], 0, 200, 14);
  assert.ok(r.placed.every(p => !p.displaced && p.at === p.y)); assert.equal(r.dropped.length, 0);
});
ok('gutter: crowded labels fan out — order kept, gap >= minGap, all inside the box, and nothing dropped when they fit', () => {
  for (let t = 0; t < 400; t++) {
    const k = 2 + Math.floor(rng() * 14), top = 4, bottom = 300, gap = 14;
    const items = Array.from({ length: k }, (_, i) => ({ id: 'i' + i, y: rng() * 340 - 20, priority: Math.floor(rng() * 9) }));
    const { placed, dropped } = M.layoutGutter(items, top, bottom, gap);
    assert.equal(placed.length + dropped.length, k);
    for (let i = 0; i < placed.length; i++) {
      assert.ok(placed[i].at >= top - 1e-9 && placed[i].at <= bottom + 1e-9, `bounds ${placed[i].at}`);
      if (i) { assert.ok(placed[i].at - placed[i - 1].at >= gap - 1e-9, `gap ${placed[i].at - placed[i - 1].at}`); assert.ok(placed[i].y >= placed[i - 1].y, 'order'); }
    }
    if (k <= Math.floor((bottom - top) / gap) + 1) assert.equal(dropped.length, 0);
  }
});
ok('gutter: when labels cannot fit, the lowest-priority ones are dropped — never overlapped', () => {
  const items = Array.from({ length: 30 }, (_, i) => ({ id: 'i' + i, y: 50 + i, priority: i === 7 ? 9 : 1 }));
  const { placed, dropped } = M.layoutGutter(items, 0, 100, 14);
  assert.ok(dropped.length > 0); assert.ok(placed.some(p => p.id === 'i7'), 'the high-priority label survives');
  for (let i = 1; i < placed.length; i++) assert.ok(placed[i].at - placed[i - 1].at >= 14 - 1e-9);
});
ok('gutter: non-finite pixels are ignored; an empty set is fine', () => {
  assert.deepEqual(M.layoutGutter([], 0, 100, 14), { placed: [], dropped: [] });
  assert.equal(M.layoutGutter([{ id: 'x', y: NaN, priority: 1 }], 0, 100, 14).placed.length, 0);
});
ok('before the engine read arrives the momentum lens quotes raw numbers, never "no momentum signal"', () => {
  const L = byKey(M.deriveLenses(null, tech(), 238.9));
  assert.match(L.momentum.detail, /^RSI 61 · MACD histogram \+0\.40$/);
  const after = byKey(M.deriveLenses(ctx('bullish', 'moderate', []), tech({ currentRSI: 50 }), 238.9));
  assert.match(after.momentum.detail, /no momentum signal/);
});
ok('what would settle a mixed call: the nearest real zone above and below spot, either may be absent', () => {
  const z = c => ({ center: c, low: c - 1, high: c + 1, kind: 'x', score: 1, n_sources: 2, sources: [], distance_pct: 0 });
  assert.deepEqual(M.settleLevels([z(255), z(249.5), z(222.2), z(214.96)], 238.9), { above: 249.5, below: 222.2 });
  assert.deepEqual(M.settleLevels([z(255)], 238.9), { above: 255, below: null });
  assert.deepEqual(M.settleLevels([{ ...z(238.95), kind: 'pivot' }, z(249.5), z(222.2)], 238.9), { above: 249.5, below: 222.2 }, 'a pivot (spot inside it) is not a level to wait for');
  assert.deepEqual(M.settleLevels([], 238.9), { above: null, below: null }); assert.deepEqual(M.settleLevels(undefined, 238.9), { above: null, below: null });
  assert.deepEqual(M.settleLevels([z(250)], null), { above: null, below: null });
});
ok('with no engine read (still loading, or it failed) the counted lenses are "not scored" — never a made-up "neutral"', () => {
  const L = byKey(M.deriveLenses(null, tech(), 238.9));
  assert.equal(L.trend.state, 'not_scored'); assert.equal(L.momentum.state, 'not_scored'); assert.equal(L.dealer.state, 'not_scored'); assert.equal(L.regime.state, 'not_scored');
  assert.equal(L.trend.detail, 'Needs the market read');
});
console.log(`\n${n} passed`);
