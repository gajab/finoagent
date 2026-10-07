const { buildScore } = require(require('path').join(process.env.BUILD_DIR, 'scoreModel.cjs'));
const assert = require('assert');
const ORDER = ['Loss probability', 'Vol edge', 'Structural defense', 'Regime', 'Directional pressure', 'Consequence', 'Event', 'Execution'];
let n = 0; const ok = (name, fn) => { fn(); n++; console.log('  ok -', name); };
console.log('scoreModel');
// Modelled on the live AAPL $305 CSP row: "80 base +7 factors +15 TA = 102 -> 100 (capped at 100)".
const aapl = {
  algo_grade: 'A', desk_score: 100, base_quality: 80,
  grade_adjustments: [
    { label: 'Moneyness', points: 8, dimension: 'Loss probability' }, { label: 'Breach risk', points: 0, dimension: 'Loss probability' },
    { label: 'VRP', points: -3, dimension: 'Vol edge' }, { label: 'Skew / IV-edge', points: 4, dimension: 'Vol edge' },
    { label: 'Liquidity', points: -5, dimension: 'Execution' }, { label: 'Defensibility', points: -1, dimension: 'Execution' },
    { label: 'Earnings timing', points: 3, dimension: 'Event' },
  ],
  ta_factors: [
    { label: 'Structure', points: 4, dimension: 'Structural defense' }, { label: 'Gamma regime', points: 4, dimension: 'Structural defense' },
    { label: 'Trend drift', points: 8, dimension: 'Regime' }, { label: 'Beta', points: 0, dimension: 'Directional pressure' },
  ],
  desk_metrics: { quant: { lenses: [{ label: 'Safety', score: 95, weight: 34, contribution: 34 }] } },
};
ok('build-up reconciles: 80 + 6 + 16 = 102, shown as capped at 100', () => {
  const s = buildScore(aapl, ORDER);
  assert.equal(s.base, 80); assert.equal(s.optNet, 6); assert.equal(s.taNet, 16); assert.equal(s.raw, 102);
  assert.equal(s.final, 100); assert.equal(s.clamped, 'cap');
});
ok('raw === base + optNet + taNet always (the card never shows a build-up that does not add up)', () => {
  const s = buildScore(aapl, ORDER); assert.equal(s.raw, s.base + s.optNet + s.taNet);
});
ok('dimension nets sum to optNet + taNet', () => {
  const s = buildScore(aapl, ORDER);
  assert.equal(Math.round(s.dims.reduce((a, d) => a + d.net, 0) * 10) / 10, s.optNet + s.taNet);
});
ok('dimensions come out in the desk order', () => {
  const s = buildScore(aapl, ORDER);
  assert.deepEqual(s.dims.map(d => d.dim), ['Loss probability', 'Vol edge', 'Structural defense', 'Regime', 'Directional pressure', 'Event', 'Execution']);
});
ok('boosts are positive, sorted, max 3; drags negative, worst first', () => {
  const s = buildScore(aapl, ORDER);
  assert.deepEqual(s.boosts.map(b => b.label), ['Moneyness', 'Trend drift', 'Skew / IV-edge']);   // 8, 8, 4 (stable order for the tie)
  assert.deepEqual(s.drags.map(b => b.label), ['Liquidity', 'VRP', 'Defensibility']);
  assert.ok(s.boosts.every(b => b.points > 0) && s.drags.every(b => b.points < 0));
});
ok('not clamped when raw equals the score', () => {
  const s = buildScore({ ...aapl, desk_score: 102 }, ORDER); assert.equal(s.clamped, null);
});
ok('floor clamp', () => {
  const s = buildScore({ algo_grade: 'F', desk_score: 0, base_quality: 20, grade_adjustments: [{ label: 'x', points: -30 }], ta_factors: [] }, ORDER);
  assert.equal(s.raw, -10); assert.equal(s.clamped, 'floor');
});
ok('veto beats wait; wait only when not vetoed', () => {
  assert.equal(buildScore({ ...aapl, grade_blocking: ['crushed vol'], grade_timing_hold: ['momentum'] }, ORDER).vetoed, true);
  assert.equal(buildScore({ ...aapl, grade_blocking: ['crushed vol'], grade_timing_hold: ['momentum'] }, ORDER).waiting, false);
  assert.equal(buildScore({ ...aapl, grade_timing_hold: ['momentum'] }, ORDER).waiting, true);
});
ok('missing factor lists and lenses do not throw', () => {
  const s = buildScore({ algo_grade: 'B', desk_score: 70, base_quality: 70, desk_metrics: { quant: {} } }, ORDER);
  assert.equal(s.raw, 70); assert.deepEqual(s.lenses, []); assert.deepEqual(s.dims, []);
});
console.log(`\n${n} passed`);
