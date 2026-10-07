const { structureCounts, filterByStructure, rowDeployed, compactMoney, deployedCapital } = require(require('path').join(process.env.BUILD_DIR, 'betaRows.cjs'));
const assert = require('assert');
let n = 0; const ok = (name, fn) => { fn(); n++; console.log('  ok -', name); };
console.log('betaRows');

const L = (action, type, strike, qty = 1) => ({ action, type, strike, qty });
const T = (id, legs, parameters = {}, extra = {}) => ({ id, ticker: 'T' + id, strategy_type: 'options', legs_data: legs, parameters, entry_prices: [], ...extra });
const trades = [
  T(1, [L('sell', 'call', 110)], { shares: 100, avg_cost: 90 }),                              // covered call
  T(2, [L('sell', 'call', 110)], { covered: true }),                                            // marked covered (shares elsewhere)
  T(3, [L('sell', 'call', 120, 2)]),                                                            // naked call
  T(4, [L('sell', 'put', 90)]),                                                                 // cash-secured put
  T(5, [L('sell', 'call', 100), L('sell', 'put', 100)]),                                        // short straddle (same strike)
  T(6, [L('sell', 'call', 110), L('sell', 'put', 90)]),                                         // short strangle
  T(7, [L('buy', 'call', 100), L('buy', 'put', 100)]),                                          // long straddle
  T(8, [L('buy', 'call', 110), L('buy', 'put', 90)]),                                           // long strangle
  T(9, [L('sell', 'put', 95), L('buy', 'put', 90)]),                                            // put credit spread
  T(10, [L('sell', 'put', 90), L('buy', 'put', 85), L('sell', 'call', 110), L('buy', 'call', 115)]),  // iron condor
  T(11, [L('sell', 'call', 110), L('buy', 'put', 90)], { shares: 100, avg_cost: 95 }),          // collar
  T(12, [L('sell', 'call', 105)], {}),                                                           // another naked call
];

const keyOf = (t) => structureCounts([t])[0].key;
ok('every trade type is recognised — including straddles (same strike) vs strangles', () => {
  assert.deepEqual(trades.map(keyOf), ['covered_call', 'covered_call', 'naked_call', 'cash_secured_put', 'short_straddle', 'short_strangle',
    'long_straddle', 'long_strangle', 'put_credit_spread', 'iron_condor', 'collar', 'naked_call']);
});
ok('the filter lists each type present with its count, most common first', () => {
  const c = structureCounts(trades);
  assert.deepEqual(c.slice(0, 2).map(x => [x.key, x.n]), [['covered_call', 2], ['naked_call', 2]]);          // tie → alphabetical by label
  assert.equal(c.reduce((s, x) => s + x.n, 0), trades.length);
  assert.equal(c.find(x => x.key === 'short_straddle').label, 'Short Straddle');
});
ok('filtering by a type keeps exactly that type; "all" keeps everything', () => {
  assert.deepEqual(filterByStructure(trades, 'naked_call').map(t => t.id), [3, 12]);
  assert.deepEqual(filterByStructure(trades, 'short_straddle').map(t => t.id), [5]);
  assert.equal(filterByStructure(trades, 'all').length, trades.length);
  assert.deepEqual(filterByStructure(trades, 'no_such_type'), []);
});

ok('a covered call deploys its STOCK (shares × cost), not a margin guess', () => {
  const d = rowDeployed(trades[0], { margin_required: 5000 });
  assert.equal(d.value, 9000); assert.equal(d.tag, 'stock');
});
ok('a naked option deploys the Reg-T margin from the last refresh', () => {
  const d = rowDeployed(trades[2], { margin_required: 4321.5 });
  assert.equal(d.value, 4321.5); assert.equal(d.tag, 'Reg-T'); assert(/Reg-T margin/.test(d.title));
});
ok('a short option whose margin has not loaded shows NOTHING — never the premium, which understates a naked short', () => {
  const d = rowDeployed({ ...trades[2], entry_net_debit: -380 }, null);
  assert.equal(d.value, null); assert.equal(d.tag, 'no margin yet');
  const d2 = rowDeployed({ ...trades[3], entry_net_debit: -150 }, { entry_cost: -150 });
  assert.equal(d2.value, null);
});
ok('a defined-risk spread falls back to its max loss; a long option to its debit', () => {
  assert.deepEqual([rowDeployed(trades[8], { max_loss: -500 }).value, rowDeployed(trades[8], { max_loss: -500 }).tag], [500, 'max loss']);
  const d = rowDeployed(trades[6], { entry_cost: 640 });
  assert.equal(d.value, 640); assert.equal(d.tag, 'debit');                  // long straddle: debit is the money at work
});
ok('deployed per row equals the shared deployedCapital chain (one definition, shown wherever it has a value)', () => {
  for (const [t, p] of [[trades[0], null], [trades[2], { margin_required: 900 }], [trades[8], { max_loss: -500 }], [trades[6], { entry_cost: 640 }], [trades[10], null]]) {
    assert.equal(rowDeployed(t, p).value, deployedCapital(t, p));
  }
});
ok('compact money reads like a dense table cell', () => {
  assert.deepEqual([compactMoney(950), compactMoney(1234), compactMoney(12400), compactMoney(123456), compactMoney(1250000)], ['$950', '$1.2k', '$12.4k', '$123k', '$1.25M']);
});
console.log(`\n${n} passed`);
