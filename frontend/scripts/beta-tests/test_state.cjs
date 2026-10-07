// minimal browser globals so module-scope code in the bundled components can load
global.window = global; global.document = { createElement: () => ({}), addEventListener() {} };
global.localStorage = { getItem: () => null, setItem() {} };
global.navigator = { userAgent: 'node' };
const m = require(require('path').join(process.env.BUILD_DIR, 'betaState.cjs'));
const assert = require('assert');
let n = 0; const ok = (name, fn) => { fn(); n++; console.log('  ok -', name); };

const soon = (d) => new Date(Date.now() + d * 86400000).toISOString().slice(0, 10);
const optTrade = (legs, extra = {}) => ({ id: 1, ticker: 'T', strategy_type: 'options_spread', legs_data: legs, parameters: {}, ...extra });
const shortPut = (k, d) => ({ action: 'sell', type: 'put', strike: k, qty: 1, expiration: soon(d) });
const shortCall = (k, d) => ({ action: 'sell', type: 'call', strike: k, qty: 1, expiration: soon(d) });
const livePnl = (over = {}, an = {}) => ({
  unrealized_pnl: 100, pnl_pct: 5, underlying_price: 100, entry_cost: 1000, current_value: 900,
  current_quotes: [{ leg: 0, bid: 1, ask: 1.1, mid: 1.05 }], expiration_date: null,
  analysis: { exit_signal: 'HOLD', captured_pct: 20, ...an }, ...over,
});

console.log('betaState');
ok('hold: healthy, far from strike, long dated', () => {
  const s = m.deriveTradeState(optTrade([shortPut(70, 40)]), livePnl());
  assert.equal(s.state, 'hold');
});
ok('pending when no pnl', () => assert.equal(m.deriveTradeState(optTrade([shortPut(70, 40)]), null).state, 'pending'));
ok('DRAM case: option leg with no bid/ask/mid -> noquote (verdict withheld) even if analysis says STRONG_CLOSE', () => {
  const p = livePnl({ current_quotes: [{ leg: 0, bid: null, ask: null, mid: 0 }] }, { exit_signal: 'STRONG_CLOSE', captured_pct: 100 });
  const s = m.deriveTradeState(optTrade([shortCall(85, 45)]), p);
  assert.equal(s.state, 'noquote'); assert.equal(s.quote.missing, 1);
});
ok('missing quote entry for the leg (error row) -> noquote', () => {
  const p = livePnl({ current_quotes: [{ leg: 0, error: 'no quote' }] });
  assert.equal(m.deriveTradeState(optTrade([shortCall(85, 45)]), p).state, 'noquote');
});
ok('multi-leg: one priced one not -> noquote with 1 of 2', () => {
  const p = livePnl({ current_quotes: [{ leg: 0, bid: 1, ask: 1.2, mid: 1.1 }, { leg: 1, bid: 0, ask: 0, mid: 0 }] });
  const s = m.deriveTradeState(optTrade([shortPut(70, 30), { action: 'buy', type: 'put', strike: 65, qty: 1, expiration: soon(30) }]), p);
  assert.equal(s.state, 'noquote'); assert.equal(s.quote.missing, 1); assert.equal(s.quote.total, 2);
});
ok('cheap-but-real quote (bid 0, ask .05) is a market, not a gap', () => {
  const p = livePnl({ current_quotes: [{ leg: 0, bid: 0, ask: 0.05, mid: 0.025 }] });
  assert.equal(m.deriveTradeState(optTrade([shortCall(150, 30)]), p).quote.status, 'ok');
});

ok('AMD case: 8% from the strike, losing, quant HOLD -> Watch, never Act (headline must not contradict the reasons)', () => {
  const p = livePnl({ unrealized_pnl: -180, pnl_pct: -2.8, underlying_price: 631 }, { exit_signal: 'HOLD', captured_pct: -200, exit_reasons: ['Positive theta still accruing - let it work'] });
  const s = m.deriveTradeState(optTrade([shortCall(700, 10)]), p);
  assert.equal(s.tested, true); assert.equal(s.breached, false);
  assert.equal(s.state, 'watch'); assert.ok(/still Hold/.test(s.headline), s.headline);
});
ok('spot within 3% of the strike and losing -> Act even with a Hold quant read', () => {
  const p = livePnl({ unrealized_pnl: -300, underlying_price: 388 }, { exit_signal: 'HOLD' });
  const s = m.deriveTradeState(optTrade([shortPut(380, 20)]), p);
  assert.equal(s.breached, true); assert.equal(s.state, 'act');
});
ok('tested + losing -> act', () => {
  const p = livePnl({ unrealized_pnl: -420, pnl_pct: -40, underlying_price: 388 });
  const s = m.deriveTradeState(optTrade([shortPut(380, 20)]), p);
  assert.equal(s.state, 'act'); assert.equal(s.tested, true);
});
ok('tested but profitable -> watch (not act)', () => {
  const p = livePnl({ unrealized_pnl: 50, underlying_price: 388 });
  assert.equal(m.deriveTradeState(optTrade([shortPut(380, 20)]), p).state, 'watch');
});
ok('close signal + profitable -> harvest', () => {
  const p = livePnl({ unrealized_pnl: 230 }, { exit_signal: 'CLOSE', captured_pct: 74 });
  const s = m.deriveTradeState(optTrade([shortPut(70, 12)]), p);
  assert.equal(s.state, 'harvest'); assert.ok(/74%/.test(s.headline));
});
ok('close signal + losing and NOT tested -> act', () => {
  const p = livePnl({ unrealized_pnl: -50 }, { exit_signal: 'STRONG_CLOSE' });
  assert.equal(m.deriveTradeState(optTrade([shortPut(70, 30)]), p).state, 'act');
});
ok('does NOT reflexively close a winner: HOLD signal + profit + far strike stays hold', () => {
  const p = livePnl({ unrealized_pnl: 900 }, { exit_signal: 'HOLD', captured_pct: 90 });
  assert.equal(m.deriveTradeState(optTrade([shortPut(70, 30)]), p).state, 'hold');
});
ok('expires within 7d -> watch', () => {
  const p = livePnl({ expiration_date: soon(4) });
  assert.equal(m.deriveTradeState(optTrade([shortPut(70, 4)]), p).state, 'watch');
});
ok('covered call judged on the option overlay P&L, not the stock gain', () => {
  const p = livePnl({ unrealized_pnl: 5000, options_pnl: -80, underlying_price: 90 }, { exit_signal: 'CLOSE', exit_scope: 'options_overlay' });
  const t = optTrade([shortCall(100, 30)], { parameters: { shares: 100 } });
  assert.equal(m.deriveTradeState(t, p).state, 'act');   // overlay is losing even though total P&L is +5000
});
ok('cached SPXW-style -100% option book -> noquote (unpriced)', () => {
  const p = { unrealized_pnl: -29645, pnl_pct: -100, underlying_price: 6000, _cached: true, analysis: { exit_signal: 'HOLD' } };
  const s = m.deriveTradeState(optTrade([shortPut(5000, 60), shortCall(7000, 60)]), p);
  assert.equal(s.state, 'noquote');
});
ok('cached normal snapshot is NOT flagged', () => {
  const p = { unrealized_pnl: 12, pnl_pct: 1.2, underlying_price: 100, _cached: true, analysis: { exit_signal: 'HOLD' } };
  assert.equal(m.deriveTradeState(optTrade([shortPut(70, 40)]), p).state, 'hold');
});
ok('persisted quote_gap=true on a cached snapshot -> noquote; false -> ok', () => {
  const base = { unrealized_pnl: 12, pnl_pct: 1.2, underlying_price: 100, _cached: true, analysis: { exit_signal: 'HOLD' } };
  assert.equal(m.deriveTradeState(optTrade([shortPut(70, 40)]), { ...base, quote_gap: true }).state, 'noquote');
  assert.equal(m.deriveTradeState(optTrade([shortPut(70, 40)]), { ...base, quote_gap: false }).state, 'hold');
});

ok('cached DRAM-style snapshot: 100% captured with 45d left -> noquote (the false Harvest seen live)', () => {
  const p = { unrealized_pnl: 232, pnl_pct: 3.9, underlying_price: 61.67, _cached: true, expiration_date: soon(45),
    analysis: { exit_signal: 'STRONG_CLOSE', captured_pct: 100 } };
  assert.equal(m.deriveTradeState(optTrade([shortCall(85, 45)], { parameters: { shares: 100 } }), p).state, 'noquote');
});
ok('cached 100% captured but expiring in 2d is a real full capture, not a gap', () => {
  const p = { unrealized_pnl: 65, pnl_pct: 3, underlying_price: 60, _cached: true, expiration_date: soon(2),
    analysis: { exit_signal: 'STRONG_CLOSE', captured_pct: 100 } };
  assert.notEqual(m.deriveTradeState(optTrade([shortCall(85, 2)], { parameters: { shares: 100 } }), p).state, 'noquote');
});
ok('covered call (cached, no exit_scope): losing STOCK but profitable CALL is not Act', () => {
  const p = { unrealized_pnl: -190, options_pnl: 120, stock_pnl: -310, pnl_pct: -3, underlying_price: 631, _cached: true, expiration_date: soon(10),
    analysis: { exit_signal: 'HOLD', captured_pct: 40 } };
  const t = optTrade([shortCall(700, 10)], { parameters: { shares: 100 } });
  const s = m.deriveTradeState(t, p);
  assert.equal(s.tested, true); assert.notEqual(s.state, 'act');
});
ok('covered call: tested and the CALL itself is losing -> act', () => {
  const p = { unrealized_pnl: 900, options_pnl: -150, stock_pnl: 1050, pnl_pct: 4, underlying_price: 690, _cached: true, expiration_date: soon(20),
    analysis: { exit_signal: 'HOLD', captured_pct: -30 } };
  const t = optTrade([shortCall(700, 20)], { parameters: { shares: 100 } });
  assert.equal(m.deriveTradeState(t, p).state, 'act');
});
ok('stock only -> hold', () => {
  const t = { id: 2, ticker: 'NOK', strategy_type: 'stock_long', legs_data: [], parameters: { shares: 700 } };
  assert.equal(m.deriveTradeState(t, { unrealized_pnl: -1949, pnl_pct: -21, underlying_price: 10 }).state, 'hold');
});
ok('book summary excludes unpriced + counts states; reconciles to the priced rows', () => {
  const t1 = optTrade([shortPut(70, 40)], { id: 1, ticker: 'A' });
  const t2 = optTrade([shortCall(85, 45)], { id: 2, ticker: 'B' });
  const t3 = optTrade([shortPut(380, 20)], { id: 3, ticker: 'C' });
  const pm = {
    1: livePnl({ unrealized_pnl: 100 }),
    2: livePnl({ unrealized_pnl: 5000, current_quotes: [{ leg: 0, bid: null, ask: null, mid: 0 }] }),
    3: livePnl({ unrealized_pnl: -420, underlying_price: 388 }),
  };
  const st = {}; [t1, t2, t3].forEach(t => { st[t.id] = m.deriveTradeState(t, pm[t.id]); });
  const sum = m.summarizeBook([t1, t2, t3], pm, st);
  assert.equal(sum.openPnl, 100 - 420);       // the unpriced +5000 is NOT in the total
  assert.equal(sum.excluded, 1); assert.equal(sum.priced, 2);
  assert.equal(sum.actCount, 1); assert.equal(sum.noQuoteCount, 1);
});
ok('urgency sort: act < noquote < watch < harvest < hold, then soonest expiry', () => {
  const st = { 1: { state: 'hold', dte: 5 }, 2: { state: 'act', dte: 30 }, 3: { state: 'watch', dte: 3 }, 4: { state: 'hold', dte: 2 } };
  const arr = [1, 2, 3, 4].map(id => ({ id, ticker: 'X' + id })).sort((a, b) => m.urgencyCompare(a, b, st));
  assert.deepEqual(arr.map(x => x.id), [2, 3, 4, 1]);
});
console.log(`\n${n} passed`);
