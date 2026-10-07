// Pure-logic tests for My Trades money formatting + P&L aggregation.
// Zero dependencies: Node ≥ 22.18 strips TS types natively, so this imports the real .ts modules.
//   cd frontend && npm test        (= node --test tests/*.test.mjs)
import { test } from 'node:test';
import assert from 'node:assert/strict';

import { fmtMoney, fmtAnnualized, isMeaningfulAnnualized, ANNUALIZED_CLIP_PCT } from '../src/lib/tradeFormat.ts';
import { pnlTotals, effectivePnl, isUnpriced, hasMark, optionIncome, closedRowDeletion } from '../src/lib/tradePnl.ts';

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
