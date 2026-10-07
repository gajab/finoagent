const M = require(require('path').join(process.env.BUILD_DIR, 'datesEntry.cjs'));
const assert = require('assert');
let n = 0; const ok = (name, fn) => { fn(); n++; console.log('  ok -', name, `[TZ=${process.env.TZ}]`); };
console.log('dates');
const ymd = (d) => `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`;
const inDays = (k) => { const d = new Date(); d.setHours(12, 0, 0, 0); d.setDate(d.getDate() + k); return ymd(d); };
ok('an expiry date shows as that calendar day (Nov 20 stays Nov 20, never Nov 19)', () => {
  assert.equal(M.fmtDate('2026-11-20'), 'Nov 20, 2026'); assert.equal(M.fmtDate('2026-12-18'), 'Dec 18, 2026'); assert.equal(M.fmtDate('2026-01-01'), 'Jan 1, 2026');
});
ok('DTE = calendar days to the expiry date, independent of the time of day', () => {
  for (const k of [0, 1, 7, 10, 11, 45, 46, 120]) assert.equal(M.dteFrom(inDays(k)), k, `+${k}d`);
});
ok('a past date is 0, not negative; blank/garbage is null', () => {
  assert.equal(M.dteFrom(inDays(-3)), 0); assert.equal(M.dteFrom(null), null); assert.equal(M.dteFrom('not a date'), null);
});
ok('a full ISO timestamp still works (uses its instant)', () => {
  assert.ok(M.dteFrom(new Date(Date.now() + 5 * 86400000).toISOString()) >= 4);
});
ok('DTE agrees with a plain calendar difference across a DST boundary window', () => {
  for (const k of [20, 30, 40, 60, 90, 150, 200]) assert.equal(M.dteFrom(inDays(k)), k, `+${k}d`);
});
console.log(`\n${n} passed`);
