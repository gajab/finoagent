const { groupFixesByTrade, riskRemovedPct } = require(require('path').join(process.env.BUILD_DIR, 'betaBookFixes.cjs'));
const assert = require('assert');
let n = 0; const ok = (name, fn) => { fn(); n++; console.log('  ok -', name); };
console.log('betaBookFixes');

// Numbers read off the live Manage Book (5 Oct): the same trades repeat under several guardrails.
const rec = (legs, before, after, cost, kept, action = 'cap') => ({ action, legs, cost, tail_after: after, premium_kept: kept });
const tgt = (ticker, id, structure, before, r, left, extra = {}) => ({ ticker, name: ticker, trade_id: id, structure, risk: before, premium_left: left, why: 'w', tail_before: before, recommended: r, alt: null, short: { strike: 700, right: 'C', qty: 1 }, ...extra });
const AMD = () => tgt('AMD', 21, 'options', -49872, rec('BUY 1x 770C (vs your short 700C)', -49872, -6760, 11, 232), 242);
const SMH = () => tgt('SMH', 22, 'options_spread', -33212, rec('BUY 1x 825C (vs your short 750C)', -33212, -7081, 233, 601), 834);
const MU = () => tgt('MU', 23, 'options', -28598, rec('BUY 1x 1980C (vs your short 1800C)', -28598, -10908, 25, 68), 93);
const APP = () => tgt('APP', 11, 'cash_secured_put', -6829, rec('BUY 1x 205P (vs your short 230P)', -6829, -2272, 16, 112), 126);
const JBL = () => tgt('JBL', 12, 'cash_secured_put', -4578, rec('BUY 1x 200P (vs your short 220P)', -4578, -1471, 31, 81), 112);
const LRCX = () => tgt('LRCX', 24, 'options', -28340, rec('BUY 1x 800C (vs your short 700C)', -28340, -3624, 34, 371), 405);
const check = (key, label, status, targets, extra = {}) => ({ key, label, status, value_str: 'v', limit_str: 'l', fix: targets === null ? undefined : { headline: `fix for ${label}`, effect: 'e', cost: 'c', targets, ...extra } });
const sc = () => ({
  grade: 'At risk', n_breach: 4, n_warn: 0, n_checks: 5,
  checks: [
    check('cvar', 'CVaR 95% (1-mo)', 'breach', [APP(), JBL()]),
    check('var', 'VaR 95% (1-mo)', 'pass', null),
    check('sym', 'Up/down tail symmetry', 'breach', [AMD(), SMH()]),
    check('cluster', 'Correlated-cluster conc.', 'breach', [AMD(), SMH(), MU()]),
    check('sector', 'Sector concentration', 'breach', [AMD(), MU(), LRCX()]),
  ],
});

ok('each trade appears ONCE however many guardrails it breaches (10 cards today -> 6 trades)', () => {
  const g = groupFixesByTrade(sc());
  assert.equal(g.cardsToday, 10); assert.equal(g.trades.length, 6);
  assert.deepEqual(g.trades.map(t => t.ticker).sort(), ['AMD', 'APP', 'JBL', 'LRCX', 'MU', 'SMH']);
});
ok('AMD resolves three guardrails with ONE fix; SMH and MU two each', () => {
  const g = groupFixesByTrade(sc()); const by = Object.fromEntries(g.trades.map(t => [t.ticker, t]));
  assert.equal(by.AMD.checks.length, 3); assert.equal(by.AMD.variants.length, 1);
  assert.deepEqual(by.AMD.variants[0].checks.map(c => c.key).sort(), ['cluster', 'sector', 'sym']);
  assert.equal(by.SMH.checks.length, 2); assert.equal(by.MU.checks.length, 2);
  assert.equal(by.APP.checks.length, 1);
});
ok('ordered by guardrails addressed, then by risk: AMD, SMH, MU, then LRCX, APP, JBL', () => {
  assert.deepEqual(groupFixesByTrade(sc()).trades.map(t => t.ticker), ['AMD', 'SMH', 'MU', 'LRCX', 'APP', 'JBL']);
});
ok('numbers are copied, never recomputed: AMD risk now -49,872 -> after -6,760, cost 11, keeps 232', () => {
  const v = groupFixesByTrade(sc()).trades[0].variants[0];
  assert.equal(v.tailBefore, -49872); assert.equal(v.rec.tail_after, -6760); assert.equal(v.rec.cost, 11); assert.equal(v.rec.premium_kept, 232);
});
ok('two DIFFERENT fixes for one trade stay separate variants, the shared one first', () => {
  const s = sc();
  s.checks[4].fix.targets[1] = tgt('MU', 23, 'options', -28598, rec('SELL 1x 1800C (close)', -28598, -2000, 400, 0, 'close'), 93);   // MU under "sector" gets a close
  const mu = groupFixesByTrade(s).trades.find(t => t.ticker === 'MU');
  assert.equal(mu.variants.length, 2); assert.equal(mu.checks.length, 2);
  assert.deepEqual(mu.variants.map(v => v.rec.action).sort(), ['cap', 'close']);
});
ok('a guardrail whose fix has no per-trade targets becomes a book-level fix (e.g. add a VIXY sleeve)', () => {
  const s = sc(); s.checks.push(check('tail', 'Tail loss', 'warn', []));
  const g = groupFixesByTrade(s);
  assert.equal(g.portfolio.length, 1); assert.equal(g.portfolio[0].check.key, 'tail'); assert.equal(g.flagged, 5);
});
ok('passing guardrails are never turned into fixes', () => {
  const s = sc(); s.checks[1].fix = { headline: 'x', targets: [AMD()] };
  assert.equal(groupFixesByTrade(s).cardsToday, 10);
});
ok('trades without a trade_id are keyed by ticker+structure (not merged across structures)', () => {
  const s = { grade: 'Watch', n_breach: 1, n_warn: 0, n_checks: 1, checks: [check('a', 'A', 'breach', [tgt('XYZ', null, 'put_credit_spread', -100, rec('l1', -100, -50, 1, 1), 5), tgt('XYZ', null, 'iron_condor', -200, rec('l2', -200, -80, 2, 2), 9)])] };
  assert.equal(groupFixesByTrade(s).trades.length, 2);
});
ok('risk removed %: AMD 49,872 -> 6,760 is 86.4%; zero-loss and null scorecard are safe', () => {
  const v = groupFixesByTrade(sc()).trades[0].variants[0];
  assert.ok(Math.abs(riskRemovedPct(v) - 86.44) < 0.05, riskRemovedPct(v));
  assert.equal(riskRemovedPct({ tailBefore: 0, rec: { tail_after: 0 } }), null);
  assert.deepEqual(groupFixesByTrade(null), { trades: [], portfolio: [], flagged: 0, cardsToday: 0 });
});
console.log(`\n${n} passed`);
