// Renders the REAL My Trades components (collapsed card row + group summary strip) with the repo's own
// esbuild + react-dom/server — no browser, no login, no jsdom. The private components are exposed by an
// onLoad plugin that appends an export line; the source file is never modified.
//   cd frontend && npm test        (= node --test tests/*.test.mjs)
import { test, before } from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { fileURLToPath, pathToFileURL } from 'node:url';
import { build } from 'esbuild';

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const MINUS = '−';
let C;                                   // { GroupSummary, TradeCard, renderToStaticMarkup, createElement }

before(async () => {
  const expose = {
    name: 'expose-private-components',
    setup(b) {
      b.onLoad({ filter: /MyTradesV2\.tsx$/ }, (a) => ({
        contents: fs.readFileSync(a.path, 'utf8')
          + "\nexport { GroupSummary, TradeCard, DeleteConfirm };\n"
          + "export { renderToStaticMarkup } from 'react-dom/server';\nexport { createElement } from 'react';\n",
        loader: 'tsx', resolveDir: path.dirname(a.path),
      }));
    },
  };
  const out = await build({
    entryPoints: [path.join(root, 'src/components/trades/MyTradesV2.tsx')], bundle: true, write: false,
    format: 'esm', platform: 'node', jsx: 'automatic', define: { 'import.meta.env': '{}' }, plugins: [expose],
    loader: { '.css': 'empty', '.svg': 'empty', '.png': 'empty' }, logLevel: 'error', absWorkingDir: root,
    // react-dom/server is CJS and calls require('stream'); give the ESM bundle a require.
    banner: { js: "import { createRequire as __cr } from 'node:module'; const require = __cr(import.meta.url);" },
  });
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'my-trades-render-'));
  const file = path.join(dir, 'bundle.mjs');
  fs.writeFileSync(file, out.outputFiles[0].text);
  C = await import(pathToFileURL(file).href);
  fs.rmSync(dir, { recursive: true, force: true });
});

// ── fixtures ─────────────────────────────────────────────────────────────────────────────────────
const noop = () => {};
const trade = (over = {}) => ({
  id: 1, ticker: 'SMH', name: 'SMH hedge put', strategy_type: 'long_put', trade_status: 'active',
  legs_data: [{ action: 'buy', type: 'put', strike: 300, qty: 1, expiration: '2026-12-18', premium: 20 }],
  entry_prices: [{ price: 20 }], entry_net_debit: -2000, parameters: {}, entry_date: '2026-09-01T00:00:00Z',
  purpose: 'hedge', ...over,
});
const cardProps = (t, pnl) => ({
  trade: t, group: 'income_options', pnl, isExpanded: false, onToggle: noop, onRefreshPnl: noop, pnlLoading: false,
  quoteSource: 'yfinance', onQuoteSourceChange: noop, onUpdatePosition: noop, onShowHistory: noop, onCreateAgent: noop,
  advisorState: { loading: false, response: '', error: null, question: '' }, onAskAdvisor: noop, onAdvisorQuestion: noop,
  showHistory: false, onFetchTransactions: async () => [], onPositionChanged: noop, onDeleteTrade: noop,
});
const card = (t, pnl) => C.renderToStaticMarkup(C.createElement(C.TradeCard, cardProps(t, pnl)));
const strip = (trades, pnlMap, extra = {}) => C.renderToStaticMarkup(C.createElement(C.GroupSummary, { trades, pnlMap, ...extra }));
const text = (html) => html.replace(/<[^>]+>/g, '|').replace(/\|+/g, '|');   // visible text, tag boundaries as |

// ── (a) negative P&L keeps its minus in the collapsed row ────────────────────────────────────────

test('collapsed row: a −$497.50 hedge renders "−$497.50", not "$497.50"', () => {
  const html = card(trade(), { unrealized_pnl: -497.5, pnl_pct: -7.83, current_value: 1502.5, entry_cost: -2000 });
  assert.ok(text(html).includes(`${MINUS}$497.50`), text(html));
  assert.ok(!/\|\$497\.50\|/.test(text(html)), 'sign was dropped: bare $497.50 as the headline');
  assert.ok(html.includes('text-error'));
});

test('collapsed row: NOK-sized loss keeps the minus and the thousands separator', () => {
  const html = card(trade({ id: 2, ticker: 'NOK' }), { unrealized_pnl: -1949.5, pnl_pct: -21.42, current_value: 7000, entry_cost: -8949.5 });
  assert.ok(text(html).includes(`${MINUS}$1,949.50`));
});

test('collapsed row: a gain shows an explicit +', () => {
  const html = card(trade(), { unrealized_pnl: 1234.5, pnl_pct: 12.3, current_value: 3234.5, entry_cost: -2000 });
  assert.ok(text(html).includes('+$1,234.50'));
  assert.ok(html.includes('text-success'));
});

// ── (b) an unpriced row says "no quote" — never a dollar figure ──────────────────────────────────

test('collapsed row: unpriced box shows "no quote", not −$29,645.00 / −100%', () => {
  const box = trade({ id: 148, ticker: 'SPXW', strategy_type: 'options_spread', entry_net_debit: -29645,
    legs_data: [0, 1, 2, 3].map((i) => ({ action: i % 2 ? 'buy' : 'sell', type: 'put', strike: 7700 + i, qty: 1, expiration: '2026-12-18', premium: 10 })) });
  const html = card(box, {
    unrealized_pnl: null, current_value: null, pnl_pct: null, entry_cost: -29645, pricing_complete: false,
    unpriced_legs: [0, 1, 2, 3], pricing_warning: 'No live quote for 4 of 4 leg(s)',
  });
  const t = text(html);
  assert.ok(t.includes('no quote'), t);
  assert.ok(t.includes('4 of 4 legs'), t);
  assert.ok(!t.includes('29,645'), 'phantom loss leaked into the row');
  assert.ok(!t.includes('100'), 'a −100% leaked into the row');
  assert.ok(!html.includes('text-error'), 'unknown ≠ loss: must not be coloured as a loss');
});

// ── group summary strip ──────────────────────────────────────────────────────────────────────────

test('strip: negative unrealized keeps its minus; a priced+unpriced group excludes + flags the unpriced row', () => {
  const ts = [trade({ id: 3 }), trade({ id: 4 }), trade({ id: 148 })];
  const pm = {
    3: { unrealized_pnl: -497.5, current_value: 1, pnl_pct: -7, margin_required: 2000 },
    4: { unrealized_pnl: -146.48, current_value: 1, pnl_pct: -2, margin_required: 1000 },
    148: { unrealized_pnl: null, current_value: null, pnl_pct: null, pricing_complete: false, unpriced_legs: [0, 1, 2, 3], entry_cost: -29645 },
  };
  const t = text(strip(ts, pm));
  assert.ok(t.includes(`${MINUS}$643.98`), t);          // −497.50 + −146.48 only
  assert.ok(!t.includes('29,645.00') || t.includes('Deployed'), t);
  assert.ok(t.includes('No quote'), t);
  assert.ok(t.includes('|1|'), 'the no-quote count');
});

test('strip: an all-unpriced group shows No quote and NO Unrealized tile (not "$0.00")', () => {
  const t = text(strip([trade({ id: 148 })], { 148: { unrealized_pnl: null, pricing_complete: false, entry_cost: -29645 } }));
  assert.ok(t.includes('No quote'));
  assert.ok(!t.includes('Unrealized'), t);
});

// ── (c) yield tile + consistent labels ───────────────────────────────────────────────────────────

test('strip: a lone 999%-clipped yield shows "—", never "999.00% ann."', () => {
  const t = text(strip([trade({ id: 5 })], { 5: { unrealized_pnl: 10, current_value: 1, pnl_pct: 1, margin_required: 500, analysis: { annualized_return_to_expiry: 999 } } }));
  assert.ok(t.includes('Avg yield'), t);
  assert.ok(!t.includes('999'), t);
  assert.ok(t.includes('|—|'), t);
});

test('strip: clipped trades are left out of the capital-weighted average, real ones still count', () => {
  const ts = [trade({ id: 6 }), trade({ id: 7 })];
  const pm = {
    6: { unrealized_pnl: 1, current_value: 1, pnl_pct: 1, margin_required: 1000, analysis: { annualized_return_to_expiry: 999 } },
    7: { unrealized_pnl: 1, current_value: 1, pnl_pct: 1, margin_required: 1000, analysis: { annualized_return_to_expiry: 12 } },
  };
  const t = text(strip(ts, pm));
  assert.ok(t.includes('12.00% ann.'), t);              // NOT (999·1000 + 12·1000)/2000 = 505.5
});

test('strip: futures groups use the same "Deployed" label as every other group (no "Margin")', () => {
  const fut = trade({ id: 8, ticker: '/ES', strategy_type: 'futures', parameters: { margin_req: '12000' } });
  const t = text(strip([fut], { 8: { unrealized_pnl: 50, current_value: 1, pnl_pct: 1 } }));
  assert.ok(t.includes('Deployed'), t);
  assert.ok(!/\|Margin\|/.test(t), t);
  assert.ok(t.includes('$12,000.00'), t);
});

// ── deleting a stock must offer to KEEP its option income ─────────────────────────────────────────

const confirm = (props) => C.renderToStaticMarkup(C.createElement(C.DeleteConfirm, {
  keepIncome: false, income: { realized: 0, closedLegs: 0, openLegs: 0, hasIncome: false }, deleting: false, error: null,
  onRemoveStock: noop, onDeleteAll: noop, onCancel: noop, ...props,
}));

test('delete confirm: stock + option income → a choice that says exactly what is kept', () => {
  const t = text(confirm({ keepIncome: true, income: { realized: 164, closedLegs: 2, openLegs: 1, hasIncome: true } }));
  assert.ok(t.includes('Remove the stock?'), t);
  assert.ok(t.includes('Remove stock, keep options'), t);
  assert.ok(t.includes('Delete everything'), t);
  assert.ok(t.includes('+$164.00') && t.includes('realized'), t);
  assert.ok(t.includes('1 open option leg'), t);
  assert.ok(!t.includes('Yes, delete'), 'the plain delete must not be the only path for a stock+income trade');
});

test('delete confirm: banked-only income (no open call) and a realized LOSS are both stated', () => {
  const t = text(confirm({ keepIncome: true, income: { realized: -35.5, closedLegs: 1, openLegs: 0, hasIncome: true } }));
  assert.ok(t.includes(`${MINUS}$35.50`) && t.includes('realized'), t);
  assert.ok(!t.includes('open option leg'), t);
});

test('delete confirm: a trade with nothing to keep keeps the plain 2-step delete', () => {
  const t = text(confirm({ keepIncome: false }));
  assert.ok(t.includes('Delete trade?') && t.includes('Yes, delete'), t);
  assert.ok(!t.includes('keep options') && !t.includes('Delete everything'), t);
});

test('delete confirm: shows the error from a failed removal and disables the actions while working', () => {
  const html = confirm({ keepIncome: true, income: { realized: 20, closedLegs: 1, openLegs: 0, hasIncome: true }, error: 'No option income to keep', deleting: true });
  assert.ok(text(html).includes('No option income to keep'));
  assert.equal((html.match(/disabled=""/g) || []).length, 3);        // Remove stock · Delete everything · Cancel
});
