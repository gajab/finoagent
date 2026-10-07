const M = require(require('path').join(process.env.BUILD_DIR, 'betaLegEdit.cjs'));
const assert = require('assert');
let n = 0; const ok = (name, fn) => { fn(); n++; console.log('  ok -', name); };
console.log('betaLegEdit');
const call = (k, extra = {}) => ({ action: 'sell', type: 'call', strike: k, qty: 1, expiration: '2026-11-20', premium: 0.65, ...extra });
const stockCombo = (over = {}) => ({ id: 1, ticker: 'DRAM', strategy_type: 'covered_call', legs_data: [call(85)], parameters: { shares: 100, avg_cost: 60 },
  entry_prices: [{ ticker: 'DRAM', price: 60 }, { price: 0.65 }], ...over });
const optOnly = (over = {}) => ({ id: 2, ticker: 'NBIS', strategy_type: 'options_spread', legs_data: [call(170, { type: 'put', premium: 1.59 }), call(160, { type: 'put', action: 'buy', premium: 0.9 })],
  parameters: {}, entry_prices: [{ price: 1.59 }, { price: 0.9 }], ...over });

ok('entry alignment: stock row first → option i is at i+1, only when the arrays line up', () => {
  assert.equal(M.entryOffset(stockCombo()), 1);
  assert.equal(M.entryOffset(stockCombo({ entry_prices: [{ price: 0.65 }] })), 0);          // shares held, but no stock row recorded
  assert.equal(M.entryOffset(optOnly()), 0);
  assert.equal(M.entryOffset(stockCombo({ parameters: { covered: true }, entry_prices: [{ price: 0.65 }] })), 0);   // "marked covered": no stock row
});
ok('entry price for a covered call is the CALL (0.65), never the stock basis (60) — the Defend bug class', () => {
  assert.equal(M.legEntryPrice(stockCombo(), 0), 0.65);
  assert.equal(M.legEntryPrice(stockCombo({ parameters: { covered: true }, entry_prices: [{ price: 0.65 }] }), 0), 0.65);
});
ok('entry price falls back to the leg premium when no entry row exists', () => {
  assert.equal(M.legEntryPrice(optOnly({ entry_prices: null }), 0), 1.59);
});

const liveQ = (q) => ({ _cached: false, current_quotes: [{ leg: 0, ...q }] });
ok('roll defaults: live market → pre-fills the mid; same strike/expiry/type; side matches the old leg', () => {
  const d = M.rollDefaults(stockCombo(), 0, liveQ({ bid: 0.3, ask: 0.42, mid: 0.36 }));
  assert.equal(d.fields.closePrice, '0.36'); assert.equal(d.fields.newAction, 'sell'); assert.equal(d.fields.newType, 'call');
  assert.equal(d.fields.newStrike, '85'); assert.equal(d.fields.newExpiration, '2026-11-20'); assert.equal(d.fields.newContracts, 1); assert.equal(d.fields.newPremium, '');
});
ok('roll defaults: NO live market → buy-back left blank (a $0 pre-fill would read as a free close)', () => {
  const d = M.rollDefaults(stockCombo(), 0, liveQ({ bid: null, ask: null, mid: 0 }));
  assert.equal(d.fields.closePrice, ''); assert.ok(/No live market/.test(d.priceNote));
});
ok('roll defaults: cached snapshot → blank + says to refresh', () => {
  const d = M.rollDefaults(stockCombo(), 0, { _cached: true }); assert.equal(d.fields.closePrice, ''); assert.ok(/Refresh/.test(d.priceNote));
});
ok('roll defaults: a long leg rolls as a long leg', () => {
  assert.equal(M.rollDefaults(optOnly(), 1, null).fields.newAction, 'buy');
});

const rf = (o = {}) => ({ closePrice: '0.36', newAction: 'sell', newType: 'call', newContracts: 1, newStrike: '90', newExpiration: '2026-12-18', newPremium: '1.10', ...o });
ok('roll payload is exactly what the classic card posts (leg_index/exit_price + one opened leg)', () => {
  const r = M.buildRollPayload(0, rf());
  assert.deepEqual(r, { ok: true, data: { close_legs: [{ leg_index: 0, exit_price: 0.36 }], open_legs: [{ action: 'sell', type: 'call', strike: 90, expiration: '2026-12-18', qty: 1, premium: 1.10 }] } });
});
ok('roll validation: every bad input is refused with a reason, nothing is sent', () => {
  for (const bad of [{ closePrice: '' }, { closePrice: '-1' }, { newStrike: '0' }, { newStrike: 'abc' }, { newExpiration: '' }, { newExpiration: '2026-13-45' }, { newPremium: '' }, { newContracts: 0 }, { newContracts: 1.5 }]) {
    const r = M.buildRollPayload(0, rf(bad)); assert.equal(r.ok, false, JSON.stringify(bad)); assert.ok(r.error.length > 5);
  }
});
ok('a $0.00 buy-back that the USER typed is allowed (a worthless leg can really be closed for nothing)', () => {
  assert.equal(M.buildRollPayload(0, rf({ closePrice: '0' })).ok, true);
});

const ef = (o = {}) => ({ action: 'sell', type: 'call', strike: '90', qty: 1, expiration: '2026-12-18', entry: '0.80', ...o });
ok('edit: updates the leg AND its recorded entry at the right row (combo → row 1, stock row untouched)', () => {
  const t = stockCombo(); const r = M.buildEditPayload(t, 0, ef());
  assert.equal(r.ok, true);
  assert.equal(r.data.legs_data[0].strike, 90); assert.equal(r.data.legs_data[0].premium, 0.80); assert.equal(r.data.legs_data[0].expiration, '2026-12-18');
  assert.deepEqual(r.data.entry_prices, [{ ticker: 'DRAM', price: 60 }, { price: 0.80 }]);
  assert.equal(r.data.strategy_type, 'covered_call');
  assert.equal(t.legs_data[0].strike, 85);                      // the original trade is not mutated
});
ok('edit on a covered-mark trade (no stock row) edits row 0, not row 1', () => {
  const t = stockCombo({ parameters: { covered: true }, entry_prices: [{ price: 0.65 }] });
  assert.deepEqual(M.buildEditPayload(t, 0, ef()).data.entry_prices, [{ price: 0.80 }]);
});
ok('edit never punches a hole in entry_prices; omitted when the row does not exist', () => {
  assert.equal('entry_prices' in M.buildEditPayload(optOnly({ entry_prices: null }), 0, ef({ type: 'put' })).data, false);
  assert.equal('entry_prices' in M.buildEditPayload(optOnly({ entry_prices: [{ price: 1.59 }] }), 1, ef({ type: 'put' })).data, false);
});
ok('edit validation + missing leg', () => {
  assert.equal(M.buildEditPayload(optOnly(), 0, ef({ strike: '' })).ok, false);
  assert.equal(M.buildEditPayload(optOnly(), 5, ef()).ok, false);
});

const af = (o = {}) => ({ action: 'sell', type: 'call', contracts: 1, strike: '95', expiration: '2026-12-18', premium: '0.40', ...o });
ok('add leg: appends one leg with a label, nothing else changes (same body as classic)', () => {
  const t = stockCombo(); const r = M.buildAddPayload(t, af());
  assert.equal(r.ok, true); assert.equal(r.data.legs_data.length, 2);
  assert.deepEqual(r.data.legs_data[1], { action: 'sell', type: 'call', qty: 1, strike: 95, expiration: '2026-12-18', premium: 0.40, label: 'Leg 2' });
  assert.deepEqual(r.data.legs_data[0], t.legs_data[0]); assert.equal(Object.keys(r.data).sort().join(), 'legs_data,strategy_type');
});
ok('add a short call to a stock-only trade → becomes a covered call; add a leg to a one-leg options trade → spread', () => {
  const stockOnly = { id: 3, strategy_type: 'stock_long', legs_data: [], parameters: { shares: 100 }, entry_prices: [{ price: 60 }] };
  assert.equal(M.buildAddPayload(stockOnly, af()).data.strategy_type, 'covered_call');
  const single = { id: 4, strategy_type: 'options_sell_call', legs_data: [call(85)], parameters: {}, entry_prices: [{ price: 0.65 }] };
  assert.equal(M.buildAddPayload(single, af({ action: 'buy', strike: '95' })).data.strategy_type, 'options_spread');
});
ok('add validation', () => {
  for (const bad of [{ strike: '' }, { expiration: '' }, { premium: '' }, { contracts: 0 }]) assert.equal(M.buildAddPayload(optOnly(), af(bad)).ok, false);
});

ok('mark-covered: same rule as classic (no stock here, a short call, options-only group or already marked)', () => {
  assert.equal(M.canMarkCovered(false, true, 'multi_leg', false), true);
  assert.equal(M.canMarkCovered(false, true, 'income_options', false), true);
  assert.equal(M.canMarkCovered(false, true, 'covered_calls', true), true);    // stays visible once marked, so it can be undone
  assert.equal(M.canMarkCovered(true, true, 'covered_calls', false), false);   // real shares on the trade
  assert.equal(M.canMarkCovered(false, false, 'multi_leg', false), false);     // no short call
  assert.equal(M.canMarkCovered(false, true, 'futures', false), false);
});
ok('hasShortCall', () => {
  assert.equal(M.hasShortCall({ legs_data: [call(85)] }), true);
  assert.equal(M.hasShortCall({ legs_data: [call(85, { action: 'buy' })] }), false);
  assert.equal(M.hasShortCall({ legs_data: [call(85, { type: 'put' })] }), false);
});
console.log(`\n${n} passed`);
