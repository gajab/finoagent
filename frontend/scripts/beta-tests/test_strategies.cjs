// Tests for the Beta Strategies board's pure model, fed by the REAL backend payload shape (preview/traderStrategies.json is
// the output of trader_strategy_service.build_trader_strategies on a seeded synthetic series).
const path = require('path');
const M = require(path.join(process.env.BUILD_DIR, 'betaStrategyModel.cjs'));
const data = require('./preview/traderStrategies.json');
const assert = require('assert');
let n = 0; const ok = (name, fn) => { fn(); n++; console.log('  ok -', name); };
console.log('betaStrategyModel');

const tiles = M.traderTiles(data);
const row = k => data.strategies.find(s => s.key === k);

ok('13 trader tiles: Qullamäggie and Connors (already named screens) are not repeated; every other trader is present once', () => {
  assert.equal(tiles.length, 13);
  const ids = tiles.map(t => t.id);
  assert.ok(!ids.includes('qullamaggie') && !ids.includes('connors'));
  assert.equal(new Set(ids).size, 13);
  assert.deepEqual([...ids].sort(), data.strategies.filter(s => !s.named_elsewhere).map(s => s.key).sort());
});

ok('tiles run family by family in a fixed order, most bullish first inside a family', () => {
  const order = tiles.map(t => M.FAMILY_ORDER.indexOf(t.family));
  assert.deepEqual(order, [...order].sort((a, b) => a - b));
  for (const f of M.FAMILY_ORDER) {
    const ds = tiles.filter(t => t.family === f).map(t => row(t.id).d);
    assert.deepEqual(ds, [...ds].sort((a, b) => b - a), f);
  }
});

ok('a tile carries exactly the backend numbers: rules met / scored and the stance', () => {
  for (const t of tiles) { const s = row(t.id); assert.equal(t.met, s.met); assert.equal(t.total, s.total); assert.equal(t.stance, s.stance); assert.equal(t.label, s.trader); }
});

ok('a trader the backend could not score becomes an unavailable tile with no stance and no fake rules', () => {
  const d2 = JSON.parse(JSON.stringify(data)); const w = d2.strategies.find(s => s.key === 'weinstein');
  Object.assign(w, { available: false, reason: 'Needs about 200 daily bars', rules: [], met: 0, total: 0, grade: 'no_data', stance: 'neutral' });
  const t = M.traderTiles(d2).find(x => x.id === 'weinstein');
  assert.equal(t.available, false); assert.equal(t.stance, null); assert.equal(t.met, null); assert.equal(t.total, null); assert.match(t.hint, /200 daily bars/);
  assert.equal(M.traderTiles(null).length, 0); assert.equal(M.traderTiles(undefined).length, 0);
});

ok('filters: family and stance combine, counts add up, and a chip never promises an empty list', () => {
  const c = M.filterCounts(tiles);
  assert.equal(c.family.all, 13); assert.equal(c.stance.all, 13);
  assert.equal(Object.entries(c.family).filter(([k]) => k !== 'all').reduce((a, [, v]) => a + v, 0), 13);
  assert.equal(['bullish', 'neutral', 'bearish'].reduce((a, k) => a + (c.stance[k] || 0), 0), 13);
  for (const f of M.FAMILY_ORDER) assert.equal(M.filterTraders(tiles, { family: f, stance: 'all' }).length, c.family[f] || 0);
  for (const k of ['bullish', 'neutral', 'bearish']) assert.equal(M.filterTraders(tiles, { family: 'all', stance: k }).length, c.stance[k] || 0);
  const both = M.filterTraders(tiles, { family: 'trend', stance: 'bullish' });
  assert.ok(both.every(t => t.family === 'trend' && t.stance === 'bullish'));
  assert.equal(M.filterTraders(tiles, M.NO_FILTER).length, 13);
});

ok('engine and named tiles: counts shown as given (null = not loaded yet), ids stable', () => {
  const e = M.engineTiles({ swing: 2, position: 0, day: null });
  assert.deepEqual(e.map(t => [t.id, t.count]), [['swing', 2], ['position', 0], ['day', null]]);
  const nm = M.namedTiles([{ key: 'momentum', label: 'Momentum Swing', hint: 'x' }, { key: 'connors_rsi2', label: 'Connors RSI-2', hint: 'y' }]);
  assert.deepEqual(nm.map(t => [t.id, t.kind]), [['momentum', 'named'], ['connors_rsi2', 'named']]);
});

ok('consensus: percentages sum to 100, the lead is the plurality, a tie is "split", an empty set is null', () => {
  const v = M.consensusView(data.consensus);
  assert.ok(Math.abs(v.pct.bull + v.pct.neutral + v.pct.bear - 100) < 1e-9); assert.equal(v.n, data.consensus.n);
  assert.equal(M.consensusView({ d: 0, bull: 6, bear: 3, neutral: 6, n: 15 }).lead, 'split');
  assert.equal(M.consensusView({ d: 0, bull: 7, bear: 3, neutral: 5, n: 15 }).lead, 'bullish');
  assert.equal(M.consensusView({ d: 0, bull: 2, bear: 9, neutral: 4, n: 15 }).lead, 'bearish');
  assert.equal(M.consensusView({ d: 0, bull: 0, bear: 0, neutral: 0, n: 0 }), null); assert.equal(M.consensusView(null), null);
});

ok('rule values read as plain text: numbers trimmed, yes/no, python "None" never shown', () => {
  assert.equal(M.fmtRuleValue(61.2345), '61.23'); assert.equal(M.fmtRuleValue(8), '8'); assert.equal(M.fmtRuleValue(true), 'yes'); assert.equal(M.fmtRuleValue(false), 'no');
  assert.equal(M.fmtRuleValue(null), '—'); assert.equal(M.fmtRuleValue(''), '—'); assert.equal(M.fmtRuleValue(NaN), '—');
  assert.equal(M.fmtRuleValue('238.9 vs None/198.7'), '238.9 vs n/a/198.7');
  assert.equal(M.ruleState({ met: true }), 'met'); assert.equal(M.ruleState({ met: false }), 'unmet'); assert.equal(M.ruleState({ met: null }), 'unknown');
});

ok('rules line and grade words', () => {
  assert.equal(M.rulesLine({ available: true, met: 7, total: 8 }), '7 of 8 rules met'); assert.equal(M.rulesLine({ available: false, met: 0, total: 0 }), 'Not scored');
  assert.equal(M.rulesLine({ available: true, met: 0, total: 0 }), 'No rules scored');
  for (const g of new Set(data.strategies.map(s => s.grade))) assert.ok(M.GRADE_WORD[g], `no wording for grade ${g}`);
});

ok('an exit line still below price is a future stop; once lost it is a level to reclaim (never called a "stop")', () => {
  const live = M.stopRead({ level: 221.4, distance_pct: -7.3, atr_multiple: 2.9, breached: false }, 'Close below the 50d SMA', true);
  assert.equal(live.breached, false); assert.equal(live.tone, 'good'); assert.match(live.headline, /exit sits at \$221\.40/); assert.match(live.detail, /7\.3% below price \(2\.9 ATR\)/); assert.match(live.detail, /Close below the 50d SMA/);
  const lost = M.stopRead({ level: 241.15, distance_pct: 0.94, atr_multiple: -0.48, breached: true }, 'Close below the 50d SMA', true);
  assert.equal(lost.breached, true); assert.equal(lost.tone, 'warn'); assert.match(lost.headline, /closed below \$241\.15, the line their exit is built on/); assert.match(lost.detail, /0\.9% \(0\.5 ATR\) under it/); assert.match(lost.detail, /level to reclaim, not a future exit/);
  assert.ok(!/stop/i.test(lost.headline));
  assert.equal(M.stopRead(null, 'x'), null); assert.equal(M.stopRead({ level: NaN, distance_pct: 0, atr_multiple: null, breached: false }, 'x'), null);
  assert.doesNotMatch(M.stopRead({ level: 100, distance_pct: -5, atr_multiple: null, breached: false }, undefined).detail, /ATR|undefined/);
});

ok('when their exit is NOT a price line (relative strength, an oscillator, a bar colour) a lost line is only a reference — a breach never claims their exit fired', () => {
  const ref = M.stopRead({ level: 241.15, distance_pct: 0.94, atr_multiple: -0.48, breached: true }, 'Relative-strength line breaks below its 50d, or the benchmark loses its 200d', false);
  assert.equal(ref.isLevel, false); assert.equal(ref.tone, 'neutral'); assert.match(ref.headline, /below \$241\.15, a reference line/);
  assert.match(ref.detail, /not a price line — Relative-strength line breaks below its 50d/); assert.match(ref.detail, /has not triggered it/);
  assert.doesNotMatch(ref.headline + ref.detail, /failed|reclaim|closed below|built on/);
  const fine = M.stopRead({ level: 235, distance_pct: -1.6, atr_multiple: 0.8, breached: false }, 'Impulse turns red / weekly tide turns down', false);
  assert.match(fine.headline, /Reference line at \$235\.00/); assert.doesNotMatch(fine.headline, /exit/i);
});

ok('the wording agrees with the backend for EVERY trader in the payload (breached flag, distance)', () => {
  for (const s of data.strategies.filter(x => x.available && x.stop)) {
    const r = M.stopRead(s.stop, s.exit_rule, s.exit_is_level);
    assert.equal(r.breached, s.stop.breached, s.key); assert.equal(r.isLevel, s.exit_is_level, s.key); assert.ok(r.headline.includes(s.stop.level.toFixed(2)), s.key);
    assert.ok(r.detail.includes(`${Math.abs(s.stop.distance_pct).toFixed(1)}%`), s.key);
  }
});

ok('chart levels: one per drawable level at its exact price; ONLY a real price-line exit is drawn as the exit; unique ids, layer "strategy"', () => {
  for (const s of data.strategies.filter(x => x.available)) {
    const lv = M.strategyChartLevels(s);
    assert.equal(lv.length, s.levels.length, s.key); assert.equal(new Set(lv.map(l => l.id)).size, lv.length);
    lv.forEach((l, i) => { assert.equal(l.price, s.levels[i].price); assert.equal(l.layer, 'strategy'); assert.equal(l.kind, 'line'); assert.ok(l.label.endsWith(s.levels[i].price.toFixed(2)), l.label); });
    const isExit = (l, i) => s.levels[i].role === 'exit' && s.exit_is_level !== false;
    assert.ok(lv.filter(isExit).every(l => l.dashed === false && l.priority === 8), s.key);
    assert.ok(lv.filter((l, i) => !isExit(l, i)).every(l => l.dashed && l.priority === 7), s.key);
    if (s.stop && s.exit_is_level) assert.ok(lv.filter(isExit).some(l => l.price === s.stop.level), `${s.key}: the drawn exit is the trader's exit`);
    if (s.exit_is_level === false) assert.ok(lv.every(l => l.dashed), `${s.key}: a non-price exit is never drawn as THE exit`);
  }
  assert.deepEqual(M.strategyChartLevels(null), []); assert.deepEqual(M.strategyChartLevels({ key: 'x', levels: [{ label: 'a', price: NaN, role: 'exit' }, { label: 'b', price: -1, role: 'exit' }] }), []);
  const colors = new Set(M.strategyChartLevels(row('turtles')).map(l => l.color)); assert.ok(colors.size >= 2, 'roles are told apart by colour');
});

ok('gutter labels stay short enough to read (price included)', () => {
  for (const s of data.strategies.filter(x => x.available)) for (const l of M.strategyChartLevels(s)) assert.ok(l.label.length <= 20, `${s.key}: "${l.label}"`);
});

ok('level distance from price is signed; unknown spot gives null', () => {
  assert.equal(M.levelDistancePct(110, 100), 10); assert.equal(M.levelDistancePct(90, 100), -10); assert.equal(M.levelDistancePct(90, null), null); assert.equal(M.levelDistancePct(90, 0), null);
});
console.log(`\n${n} passed`);
