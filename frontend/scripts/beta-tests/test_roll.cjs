const { rollPreview } = require(require('path').join(process.env.BUILD_DIR, 'rollMath.cjs'));
const assert = require('assert');
let n = 0; const ok = (name, fn) => { fn(); n++; console.log('  ok -', name); };
const close = (a, b, msg) => assert.ok(Math.abs(a - b) < 1e-9, `${msg}: ${a} vs ${b}`);
console.log('rollMath');

// ORACLE: the classic card's roll-form maths, copied VERBATIM from MyTradesV2.tsx as it was before it was refactored
// onto lib/rollMath.ts. If the shared helper ever drifts from these formulas, this test goes red.
function classicInline({ isShortOld, oldQty, oldEntry, buyback, newAction, newPrem, newQty, priorRoll, priorCount }) {
  const hasBB = isFinite(buyback), hasNP = isFinite(newPrem);
  if (!hasBB && !hasNP) return null;
  const realizedClose = hasBB ? (isShortOld ? oldEntry - buyback : buyback - oldEntry) * 100 * oldQty : null;
  const buybackCost = hasBB ? buyback * 100 * oldQty : null;
  const oldEntryCredit = (isShortOld ? oldEntry : -oldEntry) * 100 * oldQty;
  const netBasisBefore = oldEntryCredit + priorRoll;
  const targetCredit = buybackCost != null ? buybackCost - netBasisBefore : null;
  const targetPerShare = targetCredit != null && newQty > 0 ? targetCredit / (100 * newQty) : null;
  const newCredit = hasNP ? (newAction === 'sell' ? 1 : -1) * newPrem * 100 * newQty : null;
  const campaignAfter = realizedClose != null ? priorRoll + realizedClose : null;
  const netRollCash = buybackCost != null && newCredit != null ? (isShortOld ? -buybackCost : buybackCost) + newCredit : null;
  const meets = newCredit != null && targetCredit != null ? newCredit >= targetCredit - 0.005 : null;
  return { realizedClose, netRollCash, campaignAfter, newCredit, targetCredit, targetPerShare, meets, priorCount, newQty };
}
ok('identical to the classic inline formulas across a 3,000+ case grid (incl. blank inputs)', () => {
  let cases = 0;
  for (const isShortOld of [true, false]) for (const oldQty of [1, 3]) for (const oldEntry of [0.65, 1.82, 12.5, 0])
    for (const buyback of [NaN, 0.36, 2.5, 0]) for (const newAction of ['buy', 'sell']) for (const newPrem of [NaN, 0.4, 3.1])
      for (const newQty of [1, 2]) for (const priorRoll of [0, -150, 320.55]) for (const priorCount of [0, 2]) {
        const inp = { isShortOld, oldQty, oldEntry, buyback, newAction, newPrem, newQty, priorRoll, priorCount };
        const want = classicInline(inp);
        const got = rollPreview({ oldIsShort: isShortOld, oldQty, oldEntry, buyback, newAction, newPremium: newPrem, newQty, priorRoll, priorCount });
        if (want == null) { assert.equal(got.hasAny, false); }
        else { assert.equal(got.hasAny, true); for (const k of Object.keys(want)) assert.ok(Object.is(got[k], want[k]) || got[k] === want[k], `${k} ${got[k]} vs ${want[k]} for ${JSON.stringify(inp)}`); }
        cases++;
      }
  assert.ok(cases > 3000, `only ${cases} cases`);
});
ok('worked example: short 380P sold at 2.10, bought back at 6.30, rolled to a 14.40 credit = +$810 net, campaign -$420', () => {
  const r = rollPreview({ oldIsShort: true, oldQty: 1, oldEntry: 2.10, buyback: 6.30, newAction: 'sell', newPremium: 14.40, newQty: 1, priorRoll: 0, priorCount: 0 });
  close(r.realizedClose, -420, 'realized'); close(r.buybackCost, 630, 'cost'); close(r.newCredit, 1440, 'new credit');
  close(r.netRollCash, 810, 'net'); close(r.campaignAfter, -420, 'campaign'); close(r.targetCredit, 420, 'target'); assert.equal(r.meets, true);
});
ok('worked example: the DRAM call (sold 0.65, now 0.36) — buy-back is below the basis, so any credit keeps the campaign green', () => {
  const r = rollPreview({ oldIsShort: true, oldQty: 1, oldEntry: 0.65, buyback: 0.36, newAction: 'sell', newPremium: NaN, newQty: 1, priorRoll: 0, priorCount: 0 });
  close(r.realizedClose, 29, 'realized'); assert.ok(r.targetCredit <= 0); assert.equal(r.newCredit, null); assert.equal(r.netRollCash, null);
});
ok('prior rolls lower the credit needed (their realized folds into the basis)', () => {
  const base = { oldIsShort: true, oldQty: 1, oldEntry: 2, buyback: 5, newAction: 'sell', newPremium: NaN, newQty: 1, priorCount: 0 };
  const a = rollPreview({ ...base, priorRoll: 0 }), b = rollPreview({ ...base, priorRoll: 150 });
  close(a.targetCredit - b.targetCredit, 150, 'difference');
});
ok('nothing typed yet -> hasAny is false (no preview box)', () => {
  assert.equal(rollPreview({ oldIsShort: true, oldQty: 1, oldEntry: 1, buyback: NaN, newAction: 'sell', newPremium: NaN, newQty: 1, priorRoll: 0, priorCount: 0 }).hasAny, false);
});
console.log(`\n${n} passed`);
