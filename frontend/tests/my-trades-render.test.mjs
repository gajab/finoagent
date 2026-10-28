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
          + "\nexport { GroupSummary, TradeCard, DeleteConfirm };\nexport { default as CampaignTimeline } from './CampaignTimeline';\n"
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
const text = (html) => html.replace(/<[^>]+>/g, '|').replace(/\|+/g, '|').replace(/&amp;/g, '&').replace(/&rsquo;/g, '\u2019');   // visible text, tag boundaries as |

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

// ── option journal + campaign card, rendered from the REAL API payload for the AAOI campaign ───────

const AAOI = JSON.parse(fs.readFileSync(path.join(root, 'tests/fixtures/aaoi-campaign.json'), 'utf8'));
const journal = (props = {}) => C.renderToStaticMarkup(C.createElement(C.CampaignTimeline, { data: AAOI.campaign, ...props }));
const titles = (html) => [...html.matchAll(/data-testid="event-title">([^<]*)</g)].map(m => m[1].replace(/&amp;/g, '&'));

test('journal: the numbers are the all-in ones — net cash +$413.00, realized −$747.00, BE $70.87, close-out ≤ $4.13 — never "+$2.47 / Closed"', () => {
  const t = text(journal());
  assert.ok(t.includes('+$413.00'), t);                       // net cash so far
  assert.ok(t.includes(`${MINUS}$747.00`), t);                // realized on closed legs
  assert.ok(t.includes('+$1,160.00'), t);                     // the open P75's credit
  assert.ok(t.includes('$70.87'), t);                         // break-even at expiry
  assert.ok(t.includes('≤ $4.13/sh'), t);                     // close-out price
  assert.ok(!t.includes('$2.47') && !t.includes('Position Closed'), t);
});

test('journal: the story reads Opened → Closed call spread → Closed put → Rolled, every event named', () => {
  assert.deepEqual(titles(journal()), [
    'Opened iron condor — puts $70/$80 · calls $155/$160',
    'Closed call credit spread $155/$160',
    'Closed long put $70',
    'Rolled short put $80 → short put $75',
  ]);
});

test('journal: the ORIGINAL opening is there — all four legs, each labelled sold/bought to open with its own cash', () => {
  const html = journal();
  const opening = html.split('data-testid="event"')[1];                       // the first event card
  const labels = [...opening.matchAll(/data-testid="fill-label">([^<]*)</g)].map(m => m[1]);
  assert.deepEqual(labels, ['Bought to open', 'Sold to open', 'Sold to open', 'Bought to open']);   // P70 · P80 · C155 · C160 — puts then calls, by strike
  const t = text(opening);
  for (const piece of ['PUT $70', 'PUT $80', 'CALL $155', 'CALL $160', '@ $0.25', '@ $0.59', '@ $2.34', '@ $1.85', `${MINUS}$25.00`, '+$59.00', '+$234.00', `${MINUS}$185.00`]) {
    assert.ok(t.includes(piece), `${piece} missing from the opening event: ${t}`);
  }
  assert.ok(t.includes('+$83.00'), 'opening net credit +$83.00');
});

test('journal: event subtitles show expiries as dates, not raw ISO', () => {
  const t = text(journal());
  assert.ok(t.includes('exp Oct 30, 2026') && t.includes('Oct 30, 2026 → Nov 20, 2026'), t);
  assert.ok(!t.includes('exp 2026-10-30') && !t.includes('2026-10-30 → 2026-11-20'), t);
});

test('journal: every leg says WHAT it was — sold/bought to open/close, opened → closed price, rolled out, open now', () => {
  const html = journal();
  const t = text(html);
  for (const l of ['Sold to open', 'Bought to open', 'Bought to close', 'Sold to close']) assert.ok(t.includes(l), `${l} missing`);
  assert.ok(t.includes('opened $0.59 → closed $12.10'), t);                 // the rolled-out P80
  assert.ok(t.includes('opened $1.85 → closed $0.12') && t.includes('opened $2.34 → closed $0.12'), t);
  assert.equal((html.match(/rolled out/g) || []).length, 1);
  assert.equal((html.match(/open now/g) || []).length >= 1, true);
  assert.ok(t.includes('P&L −$1,151.00') && t.includes('P&L +$355.00') && t.includes('P&L +$222.00') && t.includes(`P&L ${MINUS}$173.00`), t);
});

test('journal: each event ends with its running net cash, and the last one is the all-in +$413.00', () => {
  const t = text(journal());
  const running = [...t.matchAll(/Running net cash \|([^|]*)\|/g)].map(m => m[1]);
  assert.deepEqual(running, ['+$83.00', '+$83.00', '+$463.00', '+$413.00']);
});

test('journal: "Open now" names what is open; plain-English headline states the position and the close-out', () => {
  const html = journal();
  const t = text(html);
  assert.ok(/Open now\|short PUT \$75/.test(t) || t.includes('short PUT $75'), t);
  assert.ok(t.includes('Counting all 9 fills, you are net up $413.00 in cash so far.'), t);
  assert.ok(t.includes('closing everything for ≤ $4.13/share finishes the whole campaign ahead'), t);
});

test('journal: newest-first reverses the events; the statement view is a one-line-per-fill table', () => {
  assert.deepEqual(titles(journal({ initialNewestFirst: true })).slice(0, 2), ['Rolled short put $80 → short put $75', 'Closed long put $70']);
  const st = journal({ initialView: 'statement' });
  assert.ok(st.includes('data-testid="statement"') && !st.includes('data-testid="timeline"'));
  assert.equal((st.match(/<tr /g) || []).length, 1 + 9);                    // header + 9 fills
  assert.ok(text(st).includes('Bought to close') && text(st).includes(`${MINUS}$1,210.00`));
});

test('journal: compact (classic card) is events only — no tiles, headline or toggles', () => {
  const html = journal({ compact: true });
  assert.ok(html.includes('data-testid="timeline"') && !html.includes('data-testid="headline"') && !html.includes('role="tablist"'));
  assert.equal(titles(html).length, 4);
});

test('journal: a closed trade shows no break-even / close-out; loading, error and empty states render', () => {
  const t = text(journal({ data: { ...AAOI.campaign, trade_status: 'closed' } }));
  assert.ok(!t.includes('Close-out price') && !t.includes('Break-even at expiry') && t.includes('The campaign is closed'), t);
  assert.ok(text(journal({ data: null, loading: true })).includes('Loading the journal'));
  assert.ok(text(journal({ data: null, error: 'boom' })).includes('boom'));
  assert.ok(text(journal({ data: { ...AAOI.campaign, ledger: { rows: [], events: [], net_cash: 0, realized: 0, open_cash: 0 } } })).includes('No option fills'));
});

test('campaign card: the cost basis, break-even and close-out price count the partial closes; the journal sits inside it', () => {
  const html = C.renderToStaticMarkup(C.createElement(C.TradeCard, {
    ...cardProps(AAOI.trade, { unrealized_pnl: 100, pnl_pct: 1, current_value: -1060, entry_cost: 1160, days_held: 21, current_quotes: [], greeks: [], net_greeks: {}, scenarios: [], theta_projection: [], breakevens: [], pricing_complete: true }),
    isExpanded: true,
  }));
  const t = text(html);
  assert.ok(html.includes('data-testid="effective-net-credit"') && t.includes('+$413.00'), 'all-in basis is +$413 (was +$9)');
  assert.ok(t.includes('break-even $70.87'), t);
  assert.ok(!t.includes('$74.91'), 'the old roll-only break-even must be gone');
  assert.ok(t.includes('rolls') && t.includes(`${MINUS}$1,151.00`) && t.includes('partial closes') && t.includes('+$404.00'), t);
  assert.ok(html.includes('data-testid="close-out"') && t.includes('≤ $4.13/share'), t);
  assert.ok(t.includes(`${MINUS}$647.00`), 'campaign P&L = +100 unrealized − 747 banked');
  assert.ok(t.includes('Journal · everything done on this position') && t.includes('Loading the journal'), 'the card embeds the full journal (fetched lazily)');
  assert.ok(!t.includes('Transaction ledger'), 'the partial roll-only ledger is gone');
});
