// Accuracy tests for the Beta Technical odds (firstPassage.ts).
// Reference values come from INDEPENDENT numerics (golden/*.py): a Crank-Nicolson PDE for the two-sided exit and a
// Monte-Carlo on GBM PRICE paths for the full plan (fill, then target-vs-stop) — so the long/short handling is checked.
const path = require('path');
const { normCdf, exitProbs, touchProb, tradeOdds, classicTerminalEdge } = require(path.join(process.env.BUILD_DIR, 'firstPassage.cjs'));
const exitGold = require('./golden/fp_golden.json');
const planGold = require('./golden/joint_golden.json');
const assert = require('assert');
let n = 0; const ok = (name, fn) => { fn(); n++; console.log('  ok -', name); };
const near = (a, b, tol, msg) => assert.ok(Math.abs(a - b) <= tol, `${msg || ''} got ${a} want ${b} (tol ${tol})`);
console.log('firstPassage / tradeOdds');

ok('normCdf matches known values across the core and the tails', () => {
  near(normCdf(0), 0.5, 1e-15); near(normCdf(1.959963984540054), 0.975, 1e-12);
  near(normCdf(-3), 0.0013498980316301, 1e-13); near(normCdf(-6), 9.865876450377e-10, 1e-15);
  near(normCdf(6), 1 - 9.865876450377e-10, 1e-15); near(normCdf(1) + normCdf(-1), 1, 1e-15);
});

ok('two-sided exit odds equal the finite-difference solution in every reference case', () => {
  for (const c of exitGold) {
    const sig = c.sigma, T = c.days / 365, m = (c.r - 0.5 * sig * sig) / (sig * sig);
    const e = exitProbs(c.L, c.U, m, sig * sig * T);
    near(e.top, c.top, 0.002, `top L=${c.L} U=${c.U}`); near(e.bottom, c.bot, 0.002, `bottom L=${c.L} U=${c.U}`);
    near(e.top + e.bottom + e.inside, 1, 1e-12, 'sum');
  }
});

ok('given unlimited time the odds collapse to the closed form (and nothing is left inside)', () => {
  const e = exitProbs(0.03, 0.06, 0, 50);              // driftless: P(top) = L / (L+U)
  near(e.top, 0.03 / 0.09, 1e-9); near(e.bottom, 0.06 / 0.09, 1e-9); near(e.inside, 0, 1e-9);
  const d = exitProbs(0.03, 0.06, 1.5, 50);            // with drift: scale-function result
  const W = 0.09, want = (1 - Math.exp(-2 * 1.5 * 0.03)) / (1 - Math.exp(-2 * 1.5 * W));
  near(d.top, want, 1e-9);
});

ok('zero time or a degenerate barrier leaves everything inside, never NaN', () => {
  for (const e of [exitProbs(0.03, 0.06, 0, 0), exitProbs(0, 0.06, 0, 0.01), exitProbs(0.03, 0.06, NaN, 0.01)]) {
    assert.deepEqual(e, { top: 0, bottom: 0, inside: 1 });
  }
});

ok('a one-sided touch is the reflection-principle value (driftless: twice the terminal tail)', () => {
  const a = 0.05, sg = 0.3, T = 30 / 365;
  near(touchProb(a, 0, sg, T), 2 * normCdf(-a / (sg * Math.sqrt(T))), 1e-12);
  assert.ok(touchProb(a, 0.4, sg, T) > touchProb(a, 0, sg, T));        // drifting toward it makes a touch likelier
  assert.ok(touchProb(a, -0.4, sg, T) < touchProb(a, 0, sg, T));
});

const cases = planGold;
ok('the full plan (fill, then target vs stop) equals the Monte-Carlo reference for longs, shorts, pullbacks, breakouts and at-market entries', () => {
  for (const c of cases) {
    const o = tradeOdds({ direction: c.direction, spot: c.spot, entry: c.entry, stop: c.stop, target: c.target, iv: c.iv, days: c.days });
    assert.ok(o.ok, c.name);
    near(o.win, c.win, 0.012, `${c.name} win`); near(o.loss, c.loss, 0.015, `${c.name} loss`); near(o.inside, c.inside, 0.015, `${c.name} inside`);
    if (c.fill < 0.999) near(o.fillProb, c.fill, 0.012, `${c.name} fill`); else assert.equal(o.fillProb, null, `${c.name} market`);
    near(o.win + o.loss + o.inside, 1, 1e-9, `${c.name} sums to 1`);
  }
});

ok('fill kind follows the entry: below spot = pullback, above spot = breakout (mirrored for shorts), near spot = market', () => {
  const k = (dir, spot, entry, stop, target) => tradeOdds({ direction: dir, spot, entry, stop, target, iv: 0.3, days: 20 }).fillKind;
  assert.equal(k('long', 100, 97, 95, 110), 'pullback');   assert.equal(k('long', 100, 103, 99, 112), 'breakout');
  assert.equal(k('short', 100, 103, 106, 92), 'pullback'); assert.equal(k('short', 100, 97, 101, 90), 'breakout');
  assert.equal(k('long', 100, 100.1, 97, 108), 'market');
});

ok('the plan the classic engine rates best (tight stop, 6.3R, 30% vol) is NOT a +EV bet: win rate sits below break-even', () => {
  const o = tradeOdds({ direction: 'long', spot: 238.9, entry: 233.67, stop: 229.87, target: 259.31, iv: 0.30, days: 30 });
  const c = classicTerminalEdge(238.9, 233.67, 229.87, 259.31, 0.30, 30);
  near(c.pWin, 0.169, 0.002, 'classic win chance');                         // the figure the classic card shows
  assert.ok(c.ev > 0 && c.kelly > 0, 'classic reports a positive EV and a positive Kelly');
  assert.ok(o.win < c.pWin - 0.05, `path-aware win ${o.win} vs terminal ${c.pWin}`);
  assert.ok(o.edgePts < 0, `edge ${o.edgePts}`);                          // resolved win share is below the 13.6% the R:R needs
  assert.ok(o.resolvedWin < o.breakEven);
});

ok('market-implied odds are an EXACTLY fair bet: for a price martingale (r = 0) with unlimited time, P(T1 first) = (entry-stop)/(target-stop) = the break-even rate', () => {
  for (const [stop, target] of [[95, 112], [90, 130], [98, 103]]) {
    const o = tradeOdds({ direction: 'long', spot: 100, entry: 100, stop, target, iv: 0.3, days: 40000, r: 0 });
    assert.ok(Math.abs(o.edgePts) < 0.05, `stop ${stop}/target ${target}: edge ${o.edgePts} pts`);
  }
  const s = tradeOdds({ direction: 'short', spot: 100, entry: 100, stop: 105, target: 88, iv: 0.3, days: 40000, r: 0 });
  assert.ok(Math.abs(s.edgePts) < 0.05, `short edge ${s.edgePts} pts`);
});

ok('no resolution in the window -> no resolved win share, and no invented edge', () => {
  const o = tradeOdds({ direction: 'long', spot: 100, entry: 100, stop: 70, target: 160, iv: 0.05, days: 1 });
  assert.equal(o.resolvedWin, null); assert.equal(o.edgePts, null); near(o.inside, 1, 1e-6);
});

ok('break-even win rate and R:R are exact', () => {
  const o = tradeOdds({ direction: 'long', spot: 100, entry: 100, stop: 96, target: 112, iv: 0.3, days: 30 });
  near(o.rr, 3, 1e-12); near(o.breakEven, 0.25, 1e-12);
});

ok('bad inputs are refused with a reason, not guessed', () => {
  const base = { direction: 'long', spot: 100, entry: 100, stop: 95, target: 110, iv: 0.3, days: 30 };
  assert.deepEqual(tradeOdds({ ...base, stop: 101 }), { ok: false, reason: 'bad-levels' });        // stop on the profit side
  assert.deepEqual(tradeOdds({ ...base, target: 99 }), { ok: false, reason: 'bad-levels' });
  assert.deepEqual(tradeOdds({ ...base, direction: 'short' }), { ok: false, reason: 'bad-levels' }); // levels point the wrong way
  assert.deepEqual(tradeOdds({ ...base, iv: null }), { ok: false, reason: 'no-iv' });
  assert.deepEqual(tradeOdds({ ...base, iv: 0 }), { ok: false, reason: 'no-iv' });
  assert.deepEqual(tradeOdds({ ...base, days: 0 }), { ok: false, reason: 'no-window' });
  assert.deepEqual(tradeOdds({ ...base, spot: NaN }), { ok: false, reason: 'bad-levels' });
});

ok('a longer window never lowers the chance of resolving (win + loss rises with time)', () => {
  const f = d => { const o = tradeOdds({ direction: 'long', spot: 100, entry: 100, stop: 95, target: 110, iv: 0.3, days: d }); return o.win + o.loss; };
  assert.ok(f(5) < f(15) && f(15) < f(45) && f(45) < f(120));
});
console.log(`\n${n} passed`);
