// Pure-logic tests for My Trades money formatting + P&L aggregation.
// Zero dependencies: Node ≥ 22.18 strips TS types natively, so this imports the real .ts modules.
//   cd frontend && npm test        (= node --test tests/*.test.mjs)
import { test } from 'node:test';
import assert from 'node:assert/strict';

import { fmtMoney, fmtAnnualized, isMeaningfulAnnualized, ANNUALIZED_CLIP_PCT } from '../src/lib/tradeFormat.ts';
import { pnlTotals, effectivePnl, isUnpriced, hasMark, optionIncome, closedRowDeletion, closeOutAdvice, campaignPnl } from '../src/lib/tradePnl.ts';
import { walkTransactions, isOptionFill, ledgerMode, fillLabel, instrumentLabel, campaignHeadline, EVENT_KIND_LABEL } from '../src/lib/ledger.ts';

const MINUS = '−';

// ── (a) a negative P&L must keep its sign ────────────────────────────────────────────────────────

test('negative money carries a real minus; signed adds + only to positives', () => {
  assert.equal(fmtMoney(-497.5), `${MINUS}$497.50`);
  assert.equal(fmtMoney(-497.5, { signed: true }), `${MINUS}$497.50`);
  assert.equal(fmtMoney(-1949.5, { signed: true }), `${MINUS}$1,949.50`);
  assert.equal(fmtMoney(-17461.38, { signed: true }), `${MINUS}$17,461.38`);
  assert.equal(fmtMoney(12827.6, { signed: true }), '+$12,827.60');
});

test('the old Math.abs() rendering is exactly the bug: it drops the minus', () => {
  const old = (n) => `${n >= 0 ? '+' : ''}${fmtMoney(Math.abs(n))}`;
  assert.equal(old(-497.5), '$497.50');                       // what the live app showed — colour was the only cue
  assert.notEqual(fmtMoney(-497.5, { signed: true }), old(-497.5));
});

test('a value that rounds to $0.00 never shows a sign (float residue must not read −$0.00)', () => {
  assert.equal(fmtMoney(0, { signed: true }), '$0.00');
  assert.equal(fmtMoney(-1e-12, { signed: true }), '$0.00');
  assert.equal(fmtMoney(1e-12, { signed: true }), '$0.00');
  assert.equal(fmtMoney(-0.004), '$0.00');
  assert.equal(fmtMoney(-0.01), `${MINUS}$0.01`);
  assert.equal(fmtMoney(null), '—');
});

// ── (c) meaningless annualised yields ────────────────────────────────────────────────────────────

test('a yield pinned at the ±999% rail is "—", never "999.00% ann."', () => {
  assert.equal(ANNUALIZED_CLIP_PCT, 999);
  assert.equal(fmtAnnualized(999), '—');
  assert.equal(fmtAnnualized(999.0), '—');
  assert.equal(fmtAnnualized(-999), '—');
  assert.equal(fmtAnnualized(12345), '—');
  assert.equal(fmtAnnualized(null), '—');
  assert.equal(fmtAnnualized(NaN), '—');
  assert.equal(fmtAnnualized(Infinity), '—');
  assert.equal(fmtAnnualized(12.5), '12.50% ann.');
  assert.equal(fmtAnnualized(-25), `${MINUS}25.00% ann.`);
  assert.equal(fmtAnnualized(998.9), '998.90% ann.');         // just under the rail is still a real number
});

test('isMeaningfulAnnualized narrows to a finite, un-clipped number', () => {
  for (const bad of [null, undefined, NaN, Infinity, -Infinity, 999, -999, 1500]) assert.equal(isMeaningfulAnnualized(bad), false);
  for (const ok of [0, 0.4, -73, 250.25, 998]) assert.equal(isMeaningfulAnnualized(ok), true);
});

// ── (b) totals reconcile with their rows; unpriced rows are excluded + counted ───────────────────

const priced = (id, pnl, extra = {}) => ({ [id]: { unrealized_pnl: pnl, current_value: 0, pnl_pct: 0, ...extra } });
// the 2026-10-05 book, with the box un-faked: Income +12,827.60 · Hedge −497.50 · Trade −146.48 · Other = unpriced box
const income = [{ id: 1 }, { id: 2 }];
const hedge = [{ id: 3 }];
const trade = [{ id: 4 }];
const other = [{ id: 148 }];
const pnlMap = {
  ...priced(1, 9000.1), ...priced(2, 3827.5),
  ...priced(3, -497.5), ...priced(4, -146.48),
  148: { unrealized_pnl: null, current_value: null, pnl_pct: null, pricing_complete: false, unpriced_legs: [0, 1, 2, 3] },
};
const all = [...income, ...hedge, ...trade, ...other];
const r2 = (x) => Math.round(x * 100) / 100;

test('book total = Σ of the priced rows, and equals the sum of the group strips', () => {
  const book = pnlTotals(all, pnlMap);
  const groups = [income, hedge, trade, other].map((g) => pnlTotals(g, pnlMap));
  assert.equal(r2(book.totalPnl), 12183.62);                 // NOT −17,461.38 (that had the box counted as −29,645)
  assert.equal(r2(book.totalPnl), r2(groups.reduce((s, g) => s + g.totalPnl, 0)));
  assert.equal(book.noQuote, 1);
  assert.equal(book.pricedCount, 4);
  assert.equal(groups.reduce((s, g) => s + g.noQuote, 0), book.noQuote);
});

test('the incident arithmetic: counting the unpriced box as a −100% loss reproduces the bogus −$17,461.38', () => {
  const phantom = { ...pnlMap, 148: { unrealized_pnl: -29645, current_value: 0, pnl_pct: -100 } };   // legacy payload, no flag
  assert.equal(r2(pnlTotals(all, phantom).totalPnl), -17461.38);
  assert.equal(pnlTotals(all, phantom).noQuote, 0);          // a legacy (flag-less) payload is indistinguishable from priced
});

test('an all-unpriced group has no P&L to show (no "$0.00") but still reports the count', () => {
  const t = pnlTotals(other, pnlMap);
  assert.deepEqual(t, { totalPnl: 0, pricedCount: 0, noQuote: 1, hasPnl: false });
});

test('rows with no payload yet are neither priced nor "no quote"', () => {
  const t = pnlTotals([{ id: 1 }, { id: 99 }], { ...priced(1, 100) });
  assert.deepEqual(t, { totalPnl: 100, pricedCount: 1, noQuote: 0, hasPnl: true });
});

test('derivatives-only view sums options_pnl; falls back to total − stock; unpriced stays excluded', () => {
  const m = {
    1: { unrealized_pnl: 1000, options_pnl: 400, stock_pnl: 600 },
    2: { unrealized_pnl: 500, stock_pnl: 200 },                                  // no options_pnl → 500 − 200
    3: { unrealized_pnl: null, options_pnl: null, stock_pnl: 50, pricing_complete: false },
  };
  const t = pnlTotals([{ id: 1 }, { id: 2 }, { id: 3 }], m, true);
  assert.equal(t.totalPnl, 400 + 300);
  assert.equal(t.noQuote, 1);
  assert.equal(pnlTotals([{ id: 1 }, { id: 2 }, { id: 3 }], m, false).totalPnl, 1500);
});

test('predicates', () => {
  assert.equal(isUnpriced({ pricing_complete: false }), true);
  assert.equal(isUnpriced({ pricing_complete: true }), false);
  assert.equal(isUnpriced({ unrealized_pnl: 5 }), false);        // legacy / stock / futures payloads omit the flag
  assert.equal(isUnpriced(null), false);
  assert.equal(hasMark({ unrealized_pnl: 0 }), true);            // a real $0 mark is a mark
  assert.equal(hasMark({ unrealized_pnl: null }), false);
  assert.equal(hasMark({ unrealized_pnl: 7, pricing_complete: false }), false);
  assert.equal(effectivePnl({ unrealized_pnl: null }, false), null);
});

// ── removing / deleting / closing a stock must keep its option income ─────────────────────────────

const call = { action: 'sell', type: 'call', strike: 85, qty: 1 };
const closedOpt = (realized, extra = {}) => ({ type: 'call', action: 'sell', qty: 1, realized, ...extra });

test('optionIncome: DRAM-shaped (open call + $164 realized from 2 closed legs)', () => {
  const t = { legs_data: [call], parameters: { shares: 100, realized_pnl: 164, closed_legs: [closedOpt(119), closedOpt(45)] } };
  assert.deepEqual(optionIncome(t), { realized: 164, closedLegs: 2, openLegs: 1, hasIncome: true });
});

test('optionIncome: NOK-shaped (no open call, $20 banked) still counts as income', () => {
  const t = { legs_data: [], parameters: { shares: 700, realized_pnl: 20, closed_legs: [closedOpt(20)] } };
  assert.deepEqual(optionIncome(t), { realized: 20, closedLegs: 1, openLegs: 0, hasIncome: true });
});

test('optionIncome: the STOCK realized is not option income; roll legs are', () => {
  const t = { legs_data: [], parameters: { realized_pnl: 80, closed_legs: [closedOpt(100), closedOpt(-20, { roll: true }), { type: 'stock', realized: -300 }] } };
  const r = optionIncome(t);
  assert.equal(r.realized, 80);
  assert.equal(r.closedLegs, 2);
});

test('optionIncome: legacy realized_pnl with no closed_legs is honoured; a bare stock has none', () => {
  assert.equal(optionIncome({ legs_data: [], parameters: { shares: 100, realized_pnl: 55 } }).hasIncome, true);
  assert.equal(optionIncome({ legs_data: [], parameters: { shares: 100 } }).hasIncome, false);
  assert.equal(optionIncome({ legs_data: null, parameters: null }).hasIncome, false);
  assert.equal(optionIncome({ legs_data: [{ type: 'stock' }], parameters: {} }).openLegs, 0);   // a stock leg is not an option leg
});

test('closed-journal row delete: a row of a LIVE trade only ever removes its banked part', () => {
  assert.deepEqual(closedRowDeletion('active', 'all'), { kind: 'delete_part', live: true });        // was: hard-deleted the live position
  assert.deepEqual(closedRowDeletion('active', 'stock'), { kind: 'delete_part', live: true });
  assert.deepEqual(closedRowDeletion('active', 'options'), { kind: 'delete_part', live: true });
  assert.deepEqual(closedRowDeletion('closed', 'all'), { kind: 'delete_trade', live: false });      // fully closed → drop the journal row
  assert.deepEqual(closedRowDeletion('closed', 'options'), { kind: 'delete_part', live: false });
  assert.deepEqual(closedRowDeletion(null, 'all'), { kind: 'delete_part', live: true });            // unknown status → be safe
});

// ── journal / campaign math (AAOI, trade 180) ────────────────────────────────────────────────────

// The six REAL ledger rows exactly as the API stores them for the AAOI campaign.
const AAOI_TXNS = [
  { id: 337, action: 'open', leg_index: null, quantity: 1, price: 0.83, fees: 0, executed_at: '2026-10-02T20:01:26Z', note: 'Initial position' },
  { id: 378, action: 'close', leg_index: 2, quantity: -1, price: 0.12, fees: 0, executed_at: '2026-10-15T16:10:38Z', note: 'Close buy call $160 @ $0.12' },
  { id: 379, action: 'close', leg_index: 3, quantity: 1, price: 0.12, fees: 0, executed_at: '2026-10-15T16:10:38Z', note: 'Close sell call $155 @ $0.12' },
  { id: 427, action: 'close', leg_index: 1, quantity: -1, price: 3.8, fees: 0, executed_at: '2026-10-23T18:00:00Z', note: 'Close buy put $70 @ $3.8' },
  { id: 431, action: 'close', leg_index: 0, quantity: 1, price: 12.1, fees: 0, executed_at: '2026-10-23T20:04:41Z', note: 'roll:989bef387503 · out sell put $80 @ $12.1' },
  { id: 432, action: 'add', leg_index: null, quantity: -1, price: 11.6, fees: 0, executed_at: '2026-10-23T20:04:41Z', note: 'roll:989bef387503 · into sell put $75 exp 2026-11-20' },
];

test('the stock-lot walk over option fills is exactly the bug: "+$2.47 · Position Closed" for a −$747 open campaign', () => {
  const w = walkTransactions(AAOI_TXNS);
  assert.equal(Math.round(w.totalRealized * 100) / 100, 2.47);      // what the journal showed
  assert.equal(w.finalQty, 0);                                      // …"Position Closed" while a short put is open
});

test('isOptionFill: option closes + the roll-opened leg are option fills; stock lots are not', () => {
  assert.deepEqual(AAOI_TXNS.slice(1).map(isOptionFill), [true, true, true, true, true]);
  assert.equal(isOptionFill({ leg_index: null, note: 'Initial position' }), false);      // a stock/seed row
  assert.equal(isOptionFill({ leg_index: null, note: null }), false);
});

test('a covered call\'s stock walk ignores its option fills (they used to be lot-matched against the shares)', () => {
  const txns = [
    { id: 1, action: 'open', leg_index: null, quantity: 100, price: 60, fees: 0, executed_at: '2026-08-01T00:00:00Z', note: 'Initial position' },
    { id: 2, action: 'close', leg_index: 0, quantity: 1, price: 1.82, fees: 0, executed_at: '2026-08-28T00:00:00Z', note: 'Close sell call' },   // short call bought back (+1)
    { id: 3, action: 'add', leg_index: null, quantity: -1, price: 0.65, fees: 0, executed_at: '2026-09-14T00:00:00Z', note: 'roll:abc · into sell call' },
  ];
  const polluted = walkTransactions(txns.slice(0, 2));              // shares + the call buy-back (+1) before the roll-in
  assert.equal(polluted.finalQty, 101);                             // a phantom 1-share "buy" corrupts the 100-share position…
  assert.notEqual(polluted.finalAvgCost, 60);                       // …and its average cost
  const clean = walkTransactions(txns.filter(t => !isOptionFill(t)));
  assert.equal(clean.finalQty, 100);
  assert.equal(clean.finalAvgCost, 60);
  assert.equal(clean.totalRealized, 0);
});

test('ledgerMode picks the right ledger(s) for the trade', () => {
  const call = { type: 'call', action: 'sell' };
  assert.equal(ledgerMode({ legs_data: [{ type: 'put' }], parameters: { realized_pnl: -747, closed_legs: [{ type: 'put', realized: -1151 }] }, strategy_type: 'options_spread' }), 'option');
  assert.equal(ledgerMode({ legs_data: [call], parameters: { shares: 100 }, strategy_type: 'covered_call' }), 'combo');
  assert.equal(ledgerMode({ legs_data: [], parameters: { shares: 100, closed_legs: [{ type: 'call', realized: 20 }] }, strategy_type: 'covered_call' }), 'combo');
  assert.equal(ledgerMode({ legs_data: [], parameters: { shares: 5 }, strategy_type: 'stock_long' }), 'stock');
  assert.equal(ledgerMode({ legs_data: [call], parameters: {}, strategy_type: 'stock_long' }), 'combo');       // options added to a stock trade
  assert.equal(ledgerMode(null), 'stock');
});

test('closeOutAdvice: pay up to / must collect / even', () => {
  assert.deepEqual(closeOutAdvice({ debit: 413, per_share: 4.13, qty: 1 }), { mode: 'pay', total: 413, perShare: 4.13 });
  assert.deepEqual(closeOutAdvice({ debit: -250, per_share: -1.25, qty: 2 }), { mode: 'collect', total: 250, perShare: 1.25 });
  assert.deepEqual(closeOutAdvice({ debit: 0, per_share: 0, qty: 1 }), { mode: 'even', total: 0, perShare: 0 });
  assert.deepEqual(closeOutAdvice({ debit: 300, per_share: null, qty: null }), { mode: 'pay', total: 300, perShare: null });
  assert.equal(closeOutAdvice(null), null);
});

test('campaignPnl = unrealized + EVERYTHING banked (rolls + partials), not roll-only', () => {
  const roll = { roll_realized_pnl: -1151, campaign_realized_pnl: -747 };
  assert.equal(campaignPnl(100, roll), -647);
  assert.equal(campaignPnl(100, { roll_realized_pnl: -1151 }), -1051);      // payload from before partials were counted
  assert.equal(campaignPnl(null, roll), null);
  assert.equal(campaignPnl(100, null), null);
});

test('fillLabel says what a fill IS in the broker\'s words', () => {
  assert.equal(fillLabel({ kind: 'open', side: 'sell' }), 'Sold to open');
  assert.equal(fillLabel({ kind: 'open', side: 'buy' }), 'Bought to open');
  assert.equal(fillLabel({ kind: 'close', side: 'buy' }), 'Bought to close');
  assert.equal(fillLabel({ kind: 'close', side: 'sell' }), 'Sold to close');
  assert.deepEqual(EVENT_KIND_LABEL, { open: 'Opened', close: 'Partial close', roll: 'Rolled' });
});

test('instrumentLabel formats strike without spurious decimals', () => {
  assert.equal(instrumentLabel({ type: 'put', strike: 80 }), 'PUT $80');
  assert.equal(instrumentLabel({ type: 'call', strike: 157.5 }), 'CALL $157.50');
  assert.equal(instrumentLabel({ type: 'put', strike: 75.0 }), 'PUT $75');
});

test('campaignHeadline: position, banked-vs-open, and the close-out sentence', () => {
  const ledger = { rows: new Array(9).fill({}), events: [], net_cash: 413, realized: -747, open_cash: 1160 };
  const live = campaignHeadline(ledger, { effective_breakevens: [70.87], close_even: { debit: 413, per_share: 4.13, qty: 1 } }, true);
  assert.equal(live[0], 'Counting all 9 fills, you are net up $413.00 in cash so far.');
  assert.equal(live[1], `${'\u2212'}$747.00 is already banked on closed legs; the legs still open carry a +$1,160.00 credit.`);
  assert.equal(live[2], 'Break-even at expiry $70.87 — closing everything for ≤ $4.13/share finishes the whole campaign ahead.');
  const under = campaignHeadline({ ...ledger, net_cash: -250 }, { effective_breakevens: [74], close_even: { debit: -250, per_share: -1.25, qty: 2 } }, true);
  assert.equal(under[0], 'Counting all 9 fills, you are net down $250.00 in cash so far.');
  assert.ok(under[2].includes('you must collect ≥ $250.00 closing to get back to even'), under[2]);
  assert.equal(campaignHeadline(ledger, null, false)[1], `The campaign is closed: ${'\u2212'}$747.00 realized.`);
});
