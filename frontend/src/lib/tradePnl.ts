/**
 * tradePnl.ts — pure P&L aggregation for My Trades (no React, no I/O).
 *
 * Math-accuracy rule: every total shown on screen is produced HERE from the same per-row values the
 * rows display, so a strip can never disagree with the rows beneath it. An unpriced position (a leg had
 * no live quote) has NO P&L — it is left out of the sums and counted separately, never treated as $0.
 */
import type { LivePnlResponse } from '../api';

// A live-pnl payload whose mark is KNOWN. When ≥1 leg had no live quote the backend reports
// `unrealized_pnl: null` + `pricing_complete: false` — the structure has NO P&L (it is not worth $0).
// Such a row shows "no quote" and is EXCLUDED from every total, never counted as a loss/gain.
export type PricedPnl = LivePnlResponse & { unrealized_pnl: number; current_value: number; pnl_pct: number };
export const isUnpriced = (p: LivePnlResponse | null | undefined): boolean => !!p && p.pricing_complete === false;
// (the backend guarantees current_value / pnl_pct are numeric whenever unrealized_pnl is)
export const hasMark = (p: LivePnlResponse | null | undefined): p is PricedPnl =>
  !!p && p.pricing_complete !== false && p.unrealized_pnl != null;

/** The ONE place unrealized P&L is summed — group strips and the book strip both call it, so a total can
 *  never disagree with the rows beneath it. Priced rows only: an unpriced row (`pricing_complete: false`)
 *  contributes nothing and is COUNTED in `noQuote` instead, so the strip can say what it leaves out.
 *  `hasPnl` = at least one priced row carries a P&L (so an all-unpriced group shows no "$0.00"). */
export function pnlTotals(
  trades: { id: number }[], pnlMap: Record<number, LivePnlResponse>, excludeStock = false,
): { totalPnl: number; pricedCount: number; noQuote: number; hasPnl: boolean } {
  let totalPnl = 0, pricedCount = 0, noQuote = 0;
  for (const t of trades) {
    const p = pnlMap[t.id];
    if (isUnpriced(p)) { noQuote++; continue; }
    const v = effectivePnl(p, excludeStock);
    if (v == null) continue;
    totalPnl += v; pricedCount++;
  }
  return { totalPnl, pricedCount, noQuote, hasPnl: pricedCount > 0 };
}

// P&L to show: total by default, or DERIVATIVE-ONLY (strip the stock leg's P&L) when the user
// unchecks "include stock". A stock-less income trade's unrealized_pnl is already options-only.
// Prefer the backend's options_pnl; else derive it as total − stock_pnl (both persisted in the
// snapshot). If NEITHER split field is present (a pre-feature cached snapshot) we can't strip —
// a refresh repopulates them.
export function effectivePnl(p: LivePnlResponse | null | undefined, excludeStock: boolean): number | null {
  if (!p) return null;
  const total = p.unrealized_pnl ?? null;
  if (!excludeStock) return total;
  const op = (p as any).options_pnl;
  if (op != null) return Number(op);
  const sp = (p as any).stock_pnl;
  if (sp != null && total != null) return total - Number(sp);
  return total;
}

/** What option income a trade carries, independent of its stock: REALIZED / PARTIAL = banked non-stock
 *  `closed_legs` (rolls included — they are option income too), ONGOING = option legs still open.
 *  Falls back to `realized_pnl` for legacy trades that predate `closed_legs` bookkeeping. Drives the
 *  "remove the stock but keep the option income" choice — removing a stock must never drop this. */
export function optionIncome(trade: { legs_data?: any[] | null; parameters?: Record<string, any> | null }): {
  realized: number; closedLegs: number; openLegs: number; hasIncome: boolean;
} {
  const params = trade.parameters || {};
  const closed: any[] = Array.isArray(params.closed_legs) ? params.closed_legs : [];
  const optClosed = closed.filter(l => String(l?.type || '').toLowerCase() !== 'stock');
  const openLegs = (trade.legs_data || []).filter(l => /^(call|put)$/i.test(String(l?.type || ''))).length;
  let realized = optClosed.reduce((s, l) => s + (Number(l?.realized) || 0), 0);
  if (closed.length === 0 && Number(params.realized_pnl)) realized = Number(params.realized_pnl);
  realized = Math.round(realized * 100) / 100;
  return { realized, closedLegs: optClosed.length, openLegs, hasIncome: openLegs > 0 || optClosed.length > 0 || Math.abs(realized) > 0.005 };
}

/** What deleting a Closed-ledger ROW should do. A row of a still-ACTIVE trade is only its banked chunk —
 *  the live shares / open option legs behind it must survive — so it only ever removes that part
 *  (`delete_part`). Only a fully-closed single-part row deletes the whole trade (`delete_trade`). */
export function closedRowDeletion(tradeStatus: string | null | undefined, part: 'all' | 'options' | 'stock'):
  { kind: 'delete_trade' | 'delete_part'; live: boolean } {
  const live = tradeStatus !== 'closed';
  return { kind: part === 'all' && !live ? 'delete_trade' : 'delete_part', live };
}
