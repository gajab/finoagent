const { ladderSpec, ladderX } = require(require('path').join(process.env.BUILD_DIR, 'betaLadder.cjs'));
const assert = require('assert');
let n = 0; const ok = (name, fn) => { fn(); n++; console.log('  ok -', name); };
console.log('betaLadder');
const SPOT = 178.4;
ok('put credit spread: safe zone is open to the right, closed at the break-even, short strike marked', () => {
  const s = ladderSpec({ structure: 'put_credit_spread', short_strike: 140, breakeven: 139.56 }, SPOT);
  assert.equal(s.safeLow, 139.56); assert.equal(s.safeHigh, null); assert.deepEqual(s.shorts, [140]); assert.ok(s.hasZone);
});
ok('call credit spread / naked call: zone is closed on the right only', () => {
  const s = ladderSpec({ structure: 'call_credit_spread', short_strike: 200, breakeven: 201 }, SPOT);
  assert.equal(s.safeLow, null); assert.equal(s.safeHigh, 201);
});
ok('iron condor: zone is the profit band, both short strikes marked', () => {
  const s = ladderSpec({ structure: 'iron_condor', put_short: 145, call_short: 215, band_low: 144, band_high: 216 }, SPOT);
  assert.equal(s.safeLow, 144); assert.equal(s.safeHigh, 216); assert.deepEqual(s.shorts, [145, 215]);
});
ok('every marker falls inside the window (nothing clipped off the ladder)', () => {
  const s = ladderSpec({ structure: 'iron_condor', put_short: 100, call_short: 260, band_low: 99, band_high: 261 }, SPOT);
  for (const p of [...s.shorts, s.safeLow, s.safeHigh, s.spot]) assert.ok(p >= s.lo && p <= s.hi, `${p} outside ${s.lo}..${s.hi}`);
});
ok('window is centred on spot', () => {
  const s = ladderSpec({ structure: 'cash_secured_put', short_strike: 140, breakeven: 138.9 }, SPOT);
  assert.ok(Math.abs((s.lo + s.hi) / 2 - SPOT) < 1e-9);
});
ok('spot lands at the middle of the ladder; strikes below spot land left of it', () => {
  const s = ladderSpec({ structure: 'cash_secured_put', short_strike: 140, breakeven: 138.9 }, SPOT);
  assert.ok(Math.abs(ladderX(s, SPOT, 100) - 50) < 1e-9);
  assert.ok(ladderX(s, 140, 100) < ladderX(s, SPOT, 100));
});
ok('no break-even and no band -> no zone drawn (honest, not invented)', () => {
  assert.equal(ladderSpec({ structure: 'calendar', short_strike: 180 }, SPOT).hasZone, false);
});
ok('no spot -> null', () => assert.equal(ladderSpec({ structure: 'cash_secured_put', short_strike: 1 }, 0), null));
console.log(`\n${n} passed`);
