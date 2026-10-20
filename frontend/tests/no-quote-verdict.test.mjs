// A leg with no live quote must not render ANY verdict. Mounts the REAL expanded My Trades card (esbuild +
// react-dom/server, same harness as my-trades-render.test.mjs) for the DRAM covered call whose $85 call had no quote:
// the card used to show "STRONG CLOSE" / "100% of max profit captured — close to free capital" next to a HOLD leg badge.
//   cd frontend && npm test
import { test, before } from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { fileURLToPath, pathToFileURL } from 'node:url';
import { build } from 'esbuild';

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
let C;

before(async () => {
  const expose = {
    name: 'expose-private-components',
    setup(b) {
      b.onLoad({ filter: /MyTradesV2\.tsx$/ }, (a) => ({
        contents: fs.readFileSync(a.path, 'utf8')
          + "\nexport { TradeCard };\n"
          + "export { renderToStaticMarkup } from 'react-dom/server';\nexport { createElement } from 'react';\n"
          + "export { MemoryRouter } from 'react-router-dom';\n",
        loader: 'tsx', resolveDir: path.dirname(a.path),
      }));
    },
  };
  const out = await build({
    entryPoints: [path.join(root, 'src/components/trades/MyTradesV2.tsx')], bundle: true, write: false,
    format: 'esm', platform: 'node', jsx: 'automatic', define: { 'import.meta.env': '{}' }, plugins: [expose],
    loader: { '.css': 'empty', '.svg': 'empty', '.png': 'empty' }, logLevel: 'error', absWorkingDir: root,
    banner: { js: "import { createRequire as __cr } from 'node:module'; const require = __cr(import.meta.url);" },
  });
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'no-quote-verdict-'));
  const file = path.join(dir, 'bundle.mjs');
  fs.writeFileSync(file, out.outputFiles[0].text);
  C = await import(pathToFileURL(file).href);
  fs.rmSync(dir, { recursive: true, force: true });
});

const noop = () => {};
const EXP = '2026-11-20';
const trade = {
  id: 93, ticker: 'DRAM', name: 'DRAM Covered Call', strategy_type: 'covered_call', trade_status: 'active',
  legs_data: [{ action: 'sell', type: 'call', strike: 85, qty: 1, expiration: EXP, premium: 0.65 }],
  entry_prices: [{ ticker: 'DRAM', price: 60 }, { price: 0.65 }], entry_net_debit: 301,
  parameters: { shares: 100, avg_cost: 60, purpose: 'income' }, entry_date: '2026-08-01T00:00:00Z', purpose: 'income',
};
const props = (pnl) => ({
  trade, group: 'covered_calls', pnl, isExpanded: true, onToggle: noop, onRefreshPnl: noop, pnlLoading: false,
  quoteSource: 'yfinance', onQuoteSourceChange: noop, onUpdatePosition: noop, onShowHistory: noop, onCreateAgent: noop,
  advisorState: { loading: false, response: '', error: null, question: '' }, onAskAdvisor: noop, onAdvisorQuestion: noop,
  showHistory: false, onFetchTransactions: async () => [], onPositionChanged: noop, onDeleteTrade: noop,
});
// a priced card renders router <Link>s, so mount it under a MemoryRouter like the app does
const render = (pnl) => C.renderToStaticMarkup(C.createElement(C.MemoryRouter, null, C.createElement(C.TradeCard, props(pnl))));
const text = (html) => html.replace(/<[^>]+>/g, '|').replace(/\|+/g, '|');

const WARNING = 'No live quote for 1 of 1 option leg(s) — the option P&L is not computed; the stock P&L is unaffected.';
const base = {
  strategy_id: 93, ticker: 'DRAM', underlying_price: 61.67, entry_cost: 6065, days_held: 66,
  stock_pnl: 167, stock_value: 6167, stock_cost: 6000, current_quotes: [], greeks: [],
  net_greeks: { delta: 100, gamma: 0, theta: 0, vega: 0 }, scenarios: [], max_profit: 2565, max_loss: -5935,
  breakevens: [59.35], quote_source: 'yfinance',
};

const withheld = {
  ...base, current_value: null, unrealized_pnl: null, pnl_pct: null, options_pnl: null,
  pricing_complete: false, unpriced_legs: [0], pricing_warning: WARNING,
  leg_analysis: [{ leg: 0, strike: 85, type: 'CALL', right: 'C', action: 'NO_QUOTE', no_quote: true,
    reason: 'No live quote for this short call — it can\'t be marked, so no close/roll call is made (it is never priced at $0).',
    p_itm_pct: null, captured_pct: null, prob_source: 'delta' }],
  analysis: {
    hold_vs_close: 'NO_QUOTE', hold_vs_close_reasons: [WARNING], dte_remaining: 46, exit_signal: null, exit_reasons: [WARNING],
    captured_pct: null, quant_exit: null, verdict_withheld: true, probability_of_profit: null, expected_value: null,
    recommendation: { action: 'NO_QUOTE', headline: WARNING, outcome: 'P&L is unknown until the unquoted leg prices — nothing here is valued at $0.', leg_notes: [], reasons: [WARNING] },
  },
};

const priced = {
  ...base, current_value: 6203, unrealized_pnl: 196, pnl_pct: 3.2, options_pnl: 29, pricing_complete: true, unpriced_legs: [], pricing_warning: null,
  current_quotes: [{ leg: 0, strike: 85, type: 'CALL', bid: 0.34, ask: 0.38, mid: 0.36, oi: 500, volume: 50 }],
  leg_analysis: [{ leg: 0, strike: 85, type: 'CALL', right: 'C', action: 'HOLD', reason: 'Short call 8% ITM — theta is working for you, hold.', p_itm_pct: 8, captured_pct: 44.6, prob_source: 'lognormal' }],
  analysis: {
    hold_vs_close: 'HOLD', hold_vs_close_reasons: ['On track'], dte_remaining: 46, exit_signal: 'HOLD', exit_reasons: ['Positive theta still accruing with the edge intact — let it work'],
    captured_pct: 44.6, quant_exit: null, probability_of_profit: 90, expected_value: 120,
    recommendation: { action: 'HOLD', headline: 'Hold the structure — spot $61.67 vs breakeven $59.35.', outcome: 'Trade outcome — currently up $196.', leg_notes: [], reasons: [] },
  },
};

test('unquoted covered call: the card shows NO QUOTE · verdict withheld — no exit badge, no green HOLD, no capture claim', () => {
  const t = text(render(withheld));
  assert.ok(t.includes('NO QUOTE · verdict withheld'), t.slice(0, 1500));
  assert.ok(t.includes('No live quote for 1 of 1 option leg'));
  for (const bad of ['STRONG CLOSE', 'Hold the structure', 'close to free capital', 'of max profit captured']) {
    assert.ok(!t.includes(bad), `"${bad}" must not appear on a card whose leg has no quote`);
  }
});

test('unquoted covered call: the leg row reads NO QUOTE, not HOLD', () => {
  const html = render(withheld);
  assert.ok(html.includes('NO QUOTE'), 'the leg-advice badge should say NO QUOTE');
  assert.ok(!/badge[^>]*>HOLD</.test(html), 'a leg with no mark must not carry a HOLD badge');
});

test('priced control: the same card still shows its real verdict and capture', () => {
  const t = text(render(priced));
  assert.ok(!t.includes('verdict withheld'));
  assert.ok(t.includes('Hold the structure'), t.slice(0, 1200));
  assert.ok(t.includes('45% of max profit captured'));
});
