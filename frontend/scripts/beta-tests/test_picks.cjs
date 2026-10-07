const { choosePicks, capitalOf } = require(require('path').join(process.env.BUILD_DIR, 'betaPicks.cjs'));
const assert = require('assert');
let n = 0; const ok = (name, fn) => { fn(); n++; console.log('  ok -', name); };
console.log('betaPicks');
const T = (label, score, keep, premium, extra = {}) => ({ label, desk_score: score, prob_keep_pct: keep, premium, ...extra });
// ranked best -> worst (the NVDA example numbers from the design mockups)
const ranked = [
  T('PCS 140/135', 79, 90, 44, { max_loss: -456 }),
  T('PCS 145/140', 71, 86, 58, { max_loss: -442 }),
  T('CSP 140', 73, 90, 110, { collateral: 13890 }),
  T('PCS 135/130', 66, 93, 26, { max_loss: -474 }),
  T('STRANGLE', 60, 85, 227, { grade_blocking: ['naked + earnings'] }),
];
ok('best = desk #1, safest = highest keep chance, income = biggest premium', () => {
  const p = choosePicks(ranked);
  assert.deepEqual(p.map(x => [x.key, x.trade.label]), [['best', 'PCS 140/135'], ['safest', 'PCS 135/130'], ['income', 'CSP 140']]);
});
ok('a vetoed trade is never picked, even with the biggest premium', () => {
  assert.ok(!choosePicks(ranked).some(p => p.trade.label === 'STRANGLE'));
});
ok('picks are always distinct trades', () => {
  const p = choosePicks(ranked); assert.equal(new Set(p.map(x => x.index)).size, p.length);
});
ok('if best is also the safest, the 2nd card is labelled Runner-up (not a false "Safest") and says why', () => {
  const r = [T('A', 90, 99, 10), T('B', 80, 95, 20), T('C', 70, 90, 30)];
  const p = choosePicks(r);
  assert.deepEqual(p.map(x => [x.key, x.trade.label]), [['best', 'A'], ['runnerup', 'B'], ['income', 'C']]);
  assert.ok(/already the safest/.test(p[1].why));
});
ok('when best is NOT the safest, the safest card is a different, genuinely safer trade', () => {
  const p = choosePicks(ranked); const safest = p.find(x => x.key === 'safest');
  assert.ok(safest.trade.prob_keep_pct > p.find(x => x.key === 'best').trade.prob_keep_pct);
});
ok('fewer than three eligible trades -> fewer picks, no crash, no duplicates', () => {
  assert.equal(choosePicks([T('A', 90, 99, 10)]).length, 1);
  assert.equal(choosePicks([T('A', 90, 99, 10, { grade_blocking: ['x'] })]).length, 0);
  assert.equal(choosePicks([]).length, 0);
});
ok('capital: defined-risk shows max loss, else collateral; Reg-T naked shows margin with the full risk as subtext (like classic)', () => {
  assert.deepEqual(capitalOf(ranked[0]), { label: 'Max loss', value: 456 });
  assert.deepEqual(capitalOf(ranked[2]), { label: 'Collateral', value: 13890 });
  const reg = capitalOf(T('CSP', 80, 98, 24, { capital_basis: 'reg_t_margin', collateral: 3892, notional_capital: 30500, max_loss: -30477 }));
  assert.deepEqual(reg, { label: 'Margin (BPR)', value: 3892, sub: 'full risk $30,500' });
});
ok('keep chance is capped at 99.9% in the copy (never a false guarantee)', () => {
  const p = choosePicks([T('A', 90, 100, 10), T('B', 80, 100, 9)]);
  assert.ok(p.find(x => x.key === 'safest' || x.key === 'runnerup').why.includes('99.9%'));
});
console.log(`\n${n} passed`);
