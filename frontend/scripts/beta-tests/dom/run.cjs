// DOM-level test of the Beta's WRITE actions (roll / edit / add leg / mark covered). It mounts the real
// BetaTradeInspector under jsdom with a MOCKED network and asserts the exact requests it would send — so these
// money-affecting actions are verified without touching real trades. Run via run-dom.sh (installs jsdom to a temp dir).
const path = require('path'); const fs = require('fs'); const os = require('os');
const FE = path.resolve(__dirname, '../../..');
const BUNDLE = path.join(os.tmpdir(), 'finoagent-beta-dom-bundle.cjs');
const esbuild = require(path.join(FE, 'node_modules/esbuild'));
esbuild.buildSync({ entryPoints: [path.join(__dirname, 'entry.tsx')], bundle: true, platform: 'node', format: 'cjs', outfile: BUNDLE,
  nodePaths: [path.join(FE, 'node_modules')], define: { 'import.meta.env': '{}' }, loader: { '.tsx': 'tsx', '.ts': 'ts' }, jsx: 'automatic', logLevel: 'error' });

// ---- DOM + mocked network (nothing here can reach the real app) ----
const { JSDOM } = require(path.join(process.env.JSDOM_DIR, 'node_modules/jsdom'));
const dom = new JSDOM('<!doctype html><html><body><div id="root"></div></body></html>', { url: 'http://localhost/my-trades?ui=beta', pretendToBeVisual: true });
const w = dom.window;
for (const k of ['window', 'document', 'HTMLElement', 'Node', 'Event', 'MouseEvent', 'KeyboardEvent', 'getComputedStyle', 'requestAnimationFrame', 'cancelAnimationFrame', 'SVGElement', 'Element'])
  Object.defineProperty(global, k, { value: k === 'window' ? w : w[k], configurable: true, writable: true });
Object.defineProperty(global, 'navigator', { value: w.navigator, configurable: true });
Object.defineProperty(global, 'localStorage', { value: w.localStorage, configurable: true });
global.IS_REACT_ACT_ENVIRONMENT = true;
global.ResizeObserver = class { observe() {} unobserve() {} disconnect() {} };
const calls = [];
global.fetch = async (url, opts = {}) => {
  const method = (opts.method || 'GET').toUpperCase();
  calls.push({ url: String(url), method, body: opts.body ? JSON.parse(opts.body) : undefined });
  const ok = (data) => ({ ok: true, status: 200, text: async () => JSON.stringify(data) });
  if (/\/underlying-desk/.test(url)) return { ok: false, status: 500, text: async () => '{"detail":"offline in test"}' };
  return ok({});
};

const B = require(BUNDLE);
const { React, createRoot, act, MemoryRouter, BetaTradeInspector, deriveTradeState } = B;
const assert = require('assert');
let n = 0; const ok = async (name, fn) => { await fn(); n++; console.log('  ok -', name); };
const writes = () => calls.filter(c => c.method !== 'GET' && !/underlying-desk/.test(c.url));

function setValue(el, v) { act(() => { setValueRaw(el, v); }); }
function setValueRaw(el, v) { const proto = Object.getPrototypeOf(el); Object.getOwnPropertyDescriptor(proto, 'value').set.call(el, String(v)); el.dispatchEvent(new w.Event('input', { bubbles: true })); el.dispatchEvent(new w.Event('change', { bubbles: true })); }
async function click(el) { await act(async () => { el.dispatchEvent(new w.MouseEvent('click', { bubbles: true, cancelable: true })); }); }
const q = (sel, root = document) => root.querySelector(sel);
const byText = (tag, text, root = document) => [...root.querySelectorAll(tag)].find(e => e.textContent.trim() === text);
const inputByLabel = (label) => { const l = [...document.querySelectorAll('label')].find(e => e.textContent.trim().startsWith(label)); return l && l.querySelector('input'); };

let root, changed;
async function mount(trade, pnl) {
  calls.length = 0; changed = 0;
  document.body.innerHTML = '<div id="root"></div>';
  root = createRoot(document.getElementById('root'));
  await act(async () => {
    root.render(React.createElement(MemoryRouter, null, React.createElement(BetaTradeInspector, {
      trade, pnl, state: deriveTradeState(trade, pnl), quoteSource: 'yfinance', loading: false,
      onRefresh() {}, onClose() {}, onChanged() { changed++; }, onCloseTrade() {}, onUpdatePosition() {},
    })));
  });
  await act(async () => { await new Promise(r => setTimeout(r, 10)); });
}
async function unmount() { await act(async () => { root.unmount(); }); }

const drTrade = () => ({ id: 7, ticker: 'DRAM', strategy_type: 'covered_call', name: 'DRAM cc', legs_data: [{ action: 'sell', type: 'call', strike: 85, qty: 1, expiration: '2026-11-20', premium: 0.65 }],
  parameters: { shares: 100, avg_cost: 60 }, entry_prices: [{ ticker: 'DRAM', price: 60 }, { price: 0.65 }], entry_date: '2026-08-12', trade_status: 'active' });
const livePnl = (quote) => ({ strategy_id: 7, ticker: 'DRAM', underlying_price: 61.67, entry_cost: 5935, current_value: 6167, unrealized_pnl: 232, pnl_pct: 3.9, _cached: false,
  current_quotes: [{ leg: 0, ...quote }], expiration_date: '2026-11-20', analysis: { exit_signal: 'HOLD', captured_pct: 45 }, breakevens: [59.35], scenarios: [] });
const GOOD = { bid: 0.30, ask: 0.42, mid: 0.36 };

(async () => {
  console.log('Beta leg actions (real components, mocked network)');

  await ok('Legs table shows Roll and Edit per leg, plus Add leg — no more "go to classic" for these', async () => {
    await mount(drTrade(), livePnl(GOOD));
    assert.ok(q('[aria-label="Roll leg 1"]')); assert.ok(q('[aria-label="Edit leg 1"]')); assert.ok(byText('button', 'Add leg'));
    assert.ok(!/To roll, edit or add a leg, use/.test(document.body.textContent));
    await unmount();
  });

  await ok('ROLL: pre-fills the live mid, shows the decision aids, and posts exactly the classic roll body', async () => {
    await mount(drTrade(), livePnl(GOOD));
    await click(q('[aria-label="Roll leg 1"]'));
    assert.equal(inputByLabel('Buy-back price').value, '0.36');
    setValue(inputByLabel('New strike'), '90'); setValue(inputByLabel('New expiry'), '2026-12-18'); setValue(inputByLabel('New premium'), '1.10');
    await act(async () => {});
    const t = document.body.textContent;
    assert.ok(/Realized on buy-back\+\$29\.00/.test(t), 'realized preview: ' + t.slice(t.indexOf('Realized'), t.indexOf('Realized') + 60));
    assert.ok(/Net roll credit\+\$74\.00/.test(t), 'net roll cash');        // −36 buy-back + 110 new credit
    await click(byText('button', 'Confirm roll'));
    const w2 = writes(); assert.equal(w2.length, 1);
    assert.equal(w2[0].method, 'POST'); assert.ok(/\/api\/saved-strategies\/7\/roll-position$/.test(w2[0].url));
    assert.deepEqual(w2[0].body, { close_legs: [{ leg_index: 0, exit_price: 0.36 }], open_legs: [{ action: 'sell', type: 'call', strike: 90, expiration: '2026-12-18', qty: 1, premium: 1.10 }] });
    assert.equal(changed, 1); assert.ok(!q('[aria-label="Roll leg 1"][aria-pressed]'));
    assert.ok(!/Confirm roll/.test(document.body.textContent), 'form closes after saving');
    await unmount();
  });

  await ok('ROLL: a leg with NO live market is not pre-filled with $0, and says so', async () => {
    await mount(drTrade(), livePnl({ bid: null, ask: null, mid: 0 }));
    await click(q('[aria-label="Roll leg 1"]'));
    assert.equal(inputByLabel('Buy-back price').value, '');
    assert.ok(/No live market for this leg/.test(document.body.textContent));
    await unmount();
  });

  await ok('ROLL: incomplete form is refused with a reason and sends NOTHING', async () => {
    await mount(drTrade(), livePnl(GOOD));
    await click(q('[aria-label="Roll leg 1"]'));
    setValue(inputByLabel('New expiry'), '2026-12-18');                       // premium left blank
    await click(byText('button', 'Confirm roll'));
    assert.equal(writes().length, 0); assert.ok(/Enter the premium on the new leg/.test(q('[role="alert"]').textContent));
    assert.equal(changed, 0);
    await unmount();
  });

  await ok('ROLL: a failed save shows the server message, keeps the form open, and does not refresh as if it worked', async () => {
    await mount(drTrade(), livePnl(GOOD));
    const real = global.fetch;
    global.fetch = async (url, opts = {}) => /roll-position/.test(url) ? { ok: false, status: 400, text: async () => '{"detail":"Leg already closed"}' } : real(url, opts);
    await click(q('[aria-label="Roll leg 1"]'));
    setValue(inputByLabel('New strike'), '90'); setValue(inputByLabel('New expiry'), '2026-12-18'); setValue(inputByLabel('New premium'), '1.10');
    await click(byText('button', 'Confirm roll'));
    assert.ok(/Leg already closed/.test(q('[role="alert"]').textContent)); assert.equal(changed, 0); assert.ok(byText('button', 'Confirm roll'));
    global.fetch = real; await unmount();
  });

  await ok('EDIT: saves the corrected leg and its entry at the right row (stock row untouched), same body shape as classic', async () => {
    await mount(drTrade(), livePnl(GOOD));
    await click(q('[aria-label="Edit leg 1"]'));
    assert.equal(inputByLabel('Entry price').value, '0.65');                  // the call's entry, NOT the stock's 60
    setValue(inputByLabel('Strike'), '90'); setValue(inputByLabel('Entry price'), '0.80');
    await click(byText('button', 'Save leg'));
    const w2 = writes(); assert.equal(w2.length, 1); assert.equal(w2[0].method, 'PUT'); assert.ok(/\/api\/saved-strategies\/7$/.test(w2[0].url));
    assert.equal(w2[0].body.legs_data[0].strike, 90); assert.equal(w2[0].body.legs_data[0].premium, 0.8); assert.equal(w2[0].body.strategy_type, 'covered_call');
    assert.deepEqual(w2[0].body.entry_prices, [{ ticker: 'DRAM', price: 60 }, { price: 0.8 }]);
    assert.equal(changed, 1);
    await unmount();
  });

  await ok('ADD LEG: appends one labelled leg; only legs_data + strategy_type are sent', async () => {
    await mount(drTrade(), livePnl(GOOD));
    await click(byText('button', 'Add leg'));
    setValue(inputByLabel('Strike'), '95'); setValue(inputByLabel('Expiry'), '2026-12-18'); setValue(inputByLabel('Premium'), '0.40');
    const confirm = [...document.querySelectorAll('button')].filter(b => b.textContent.trim() === 'Add leg').pop();
    await click(confirm);
    const w2 = writes(); assert.equal(w2.length, 1); assert.equal(w2[0].method, 'PUT');
    assert.deepEqual(Object.keys(w2[0].body).sort(), ['legs_data', 'strategy_type']);
    assert.equal(w2[0].body.legs_data.length, 2);
    assert.deepEqual(w2[0].body.legs_data[1], { action: 'sell', type: 'call', qty: 1, strike: 95, expiration: '2026-12-18', premium: 0.4, label: 'Leg 2' });
    assert.equal(changed, 1);
    await unmount();
  });

  await ok('only one form is open at a time (opening Edit closes Roll)', async () => {
    await mount(drTrade(), livePnl(GOOD));
    await click(q('[aria-label="Roll leg 1"]')); assert.ok(byText('button', 'Confirm roll'));
    await click(q('[aria-label="Edit leg 1"]')); assert.ok(!byText('button', 'Confirm roll')); assert.ok(byText('button', 'Save leg'));
    await unmount();
  });

  const naked = (covered) => ({ id: 9, ticker: 'MU', strategy_type: 'options_sell_call', legs_data: [{ action: 'sell', type: 'call', strike: 1980, qty: 1, expiration: '2026-11-19', premium: 68 }],
    parameters: covered ? { covered: true } : {}, entry_prices: [{ price: 68 }], entry_date: '2026-09-29', trade_status: 'active' });
  const nakedPnl = { strategy_id: 9, ticker: 'MU', underlying_price: 1063, entry_cost: -6800, current_value: -5000, unrealized_pnl: 270, pnl_pct: 2.4, _cached: false,
    current_quotes: [{ leg: 0, bid: 49, ask: 51, mid: 50 }], expiration_date: '2026-11-19', analysis: { exit_signal: 'HOLD', captured_pct: 26 }, breakevens: [], scenarios: [] };

  await ok('MARK COVERED: offered for a short call with no stock; sends PATCH {covered:true}; refreshes', async () => {
    await mount(naked(false), nakedPnl);
    const b = byText('button', 'Mark covered'); assert.ok(b);
    await click(b);
    const w2 = writes(); assert.equal(w2.length, 1); assert.equal(w2[0].method, 'PATCH'); assert.ok(/\/api\/saved-strategies\/9\/covered$/.test(w2[0].url));
    assert.deepEqual(w2[0].body, { covered: true }); assert.equal(changed, 1);
    await unmount();
  });
  await ok('MARK COVERED: when already marked it shows "Covered" and the click UNMARKS it', async () => {
    await mount(naked(true), nakedPnl);
    const b = byText('button', 'Covered'); assert.ok(b); assert.equal(b.getAttribute('aria-pressed'), 'true');
    await click(b);
    assert.deepEqual(writes()[0].body, { covered: false });
    await unmount();
  });
  await ok('MARK COVERED: NOT offered when the trade holds real shares (already covered by its own stock)', async () => {
    await mount(drTrade(), livePnl(GOOD));
    assert.ok(!byText('button', 'Mark covered') && !byText('button', 'Covered'));
    await unmount();
  });
  await ok('MARK COVERED: a failed save is reported and does not claim success', async () => {
    await mount(naked(false), nakedPnl);
    const real = global.fetch; global.fetch = async (url, opts = {}) => /covered/.test(url) ? { ok: false, status: 500, text: async () => '{"detail":"db down"}' } : real(url, opts);
    await click(byText('button', 'Mark covered'));
    assert.ok(/db down/.test(q('[role="alert"]').textContent)); assert.equal(changed, 0);
    global.fetch = real; await unmount();
  });

  console.log(`\n${n} passed`);
  process.exit(0);
})().catch(e => { console.error('FAILED:', e && e.stack || e); process.exit(1); });
