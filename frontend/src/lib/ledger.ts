/**
 * ledger.ts — pure helpers behind the My Trades journal (History panel).
 *
 * Two different ledgers live on one trade row and need DIFFERENT math:
 *   • STOCK fills   — FIFO lots, per share (`walkTransactions`).
 *   • OPTION fills  — signed cash ×100 per contract, realized from the closed legs; built server-side
 *                     (`/campaign-ledger`), never by lot-matching.
 * Feeding option fills through the stock walk matched a bought-back short against an unrelated opening
 * "lot", so an open short put with −$747 realized read "Realized +$2.47 · Position Closed".
 */
import type { TradeTransaction, CampaignLedgerRow, CampaignLedger, RollSummary } from '../api';

/** Is this ledger row an OPTION fill (not a stock lot)? Option closes carry a `leg_index`; the replacement
 *  leg a roll opens is tagged `roll:` in its note. Stock opens/adds/closes have neither. */
export function isOptionFill(t: { leg_index?: number | null; note?: string | null }): boolean {
  return t.leg_index != null || /^roll:/.test(t.note || '');
}

export type LedgerMode = 'stock' | 'option' | 'combo';

/** Which ledgers a trade needs: stock lots only, option cash-ledger only, or both (covered call etc.). */
export function ledgerMode(trade: { legs_data?: any[] | null; parameters?: Record<string, any> | null; strategy_type?: string } | null | undefined): LedgerMode {
  if (!trade) return 'stock';
  const params = trade.parameters || {};
  const closed: any[] = Array.isArray(params.closed_legs) ? params.closed_legs : [];
  const hasOptions = (trade.legs_data || []).some(l => /^(call|put)$/i.test(String(l?.type || '')))
    || closed.some(l => String(l?.type || '').toLowerCase() !== 'stock');
  const hasStock = (Number(params.shares) || 0) > 0
    || closed.some(l => String(l?.type || '').toLowerCase() === 'stock')
    || /^stock/i.test(trade.strategy_type || '');
  if (!hasOptions) return 'stock';
  return hasStock ? 'combo' : 'option';
}

export interface RowState {
  txn: TradeTransaction;
  txnQty: number;
  txnValue: number;
  legRealized: number;
  runningQty: number;
  runningAvgCost: number;
  runningCostBasis: number;
}

/** FIFO stock-lot walk (per share). STOCK fills only — pass option fills through `isOptionFill` first. */
export function walkTransactions(txns: TradeTransaction[]): {
  rows: RowState[];
  totalFees: number;
  totalRealized: number;
  finalQty: number;
  finalAvgCost: number;
  finalCostBasis: number;
} {
  const openLots: { qty: number; price: number }[] = [];
  let runningQty = 0;
  let runningCostBasis = 0;
  let totalFees = 0;
  let totalRealized = 0;

  const rows: RowState[] = txns.map(t => {
    const qty = t.quantity;
    const absQty = Math.abs(qty);
    totalFees += t.fees || 0;
    let legRealized = 0;

    if (qty > 0) {
      openLots.push({ qty: absQty, price: t.price });
      runningQty += absQty;
      runningCostBasis += absQty * t.price;
    } else if (qty < 0) {
      let toMatch = absQty;
      let costOfSold = 0;
      while (toMatch > 1e-9 && openLots.length > 0) {
        const lot = openLots[0];
        const matched = Math.min(lot.qty, toMatch);
        costOfSold += matched * lot.price;
        legRealized += (t.price - lot.price) * matched;
        lot.qty -= matched;
        toMatch -= matched;
        if (lot.qty < 1e-9) openLots.shift();
      }
      totalRealized += legRealized;
      runningQty -= absQty;
      runningCostBasis -= costOfSold;
      if (runningQty < 1e-9) runningCostBasis = 0;
    }

    const runningAvgCost = runningQty > 1e-9 ? runningCostBasis / runningQty : 0;
    return {
      txn: t,
      txnQty: qty,
      txnValue: absQty * t.price,
      legRealized,
      runningQty: Math.max(0, runningQty),
      runningAvgCost,
      runningCostBasis: Math.max(0, runningCostBasis),
    };
  });

  const last = rows[rows.length - 1];
  return {
    rows,
    totalFees,
    totalRealized,
    finalQty: last?.runningQty ?? 0,
    finalAvgCost: last?.runningAvgCost ?? 0,
    finalCostBasis: last?.runningCostBasis ?? 0,
  };
}

// ── option journal wording ───────────────────────────────────────────────────────────────────────

/** What a fill IS, in the broker's own words: "Sold to open" · "Bought to close" … */
export function fillLabel(r: Pick<CampaignLedgerRow, 'kind' | 'side'>): string {
  if (r.kind === 'open') return r.side === 'sell' ? 'Sold to open' : 'Bought to open';
  return r.side === 'buy' ? 'Bought to close' : 'Sold to close';
}

/** "PUT $80" — the instrument a fill is on (strike formatted without a spurious .00). */
export function instrumentLabel(r: Pick<CampaignLedgerRow, 'type' | 'strike'>): string {
  const k = r.strike == null ? '' : ` $${Number.isInteger(Number(r.strike)) ? Number(r.strike) : Number(r.strike).toFixed(2)}`;
  return `${String(r.type || '').toUpperCase()}${k}`;
}

export const EVENT_KIND_LABEL: Record<'open' | 'close' | 'roll', string> = { open: 'Opened', close: 'Partial close', roll: 'Rolled' };

const money = (v: number) => `${v < 0 ? '\u2212' : '+'}$${Math.abs(v).toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`;
const plain = (v: number) => `$${Math.abs(v).toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`;

/** The campaign in three plain sentences: where you stand, what is banked vs open, and what to do about it. */
export function campaignHeadline(ledger: CampaignLedger, summary: RollSummary | null | undefined, live: boolean): string[] {
  const out: string[] = [];
  const fills = ledger.rows.length;
  out.push(`Counting all ${fills} fill${fills === 1 ? '' : 's'}, you are net ${ledger.net_cash >= 0 ? 'up' : 'down'} ${plain(ledger.net_cash)} in cash so far.`);
  if (live) {
    out.push(`${money(ledger.realized)} is already banked on closed legs; the legs still open carry a ${money(ledger.open_cash)} credit.`);
    const be = summary?.effective_breakevens;
    const ce = summary?.close_even;
    const bits: string[] = [];
    if (be && be.length) bits.push(`break-even at expiry ${be.map(b => `$${b.toFixed(2)}`).join(' / ')}`);
    if (ce && Math.abs(ce.debit) > 0.005) {
      const px = ce.per_share != null ? `${plain(ce.per_share)}/share` : plain(ce.debit);
      bits.push(ce.debit > 0 ? `closing everything for ≤ ${px} finishes the whole campaign ahead`
                             : `you must collect ≥ ${plain(ce.debit)} closing to get back to even`);
    }
    if (bits.length) out.push(bits[0].charAt(0).toUpperCase() + bits[0].slice(1) + (bits[1] ? ` — ${bits[1]}.` : '.'));
  } else {
    out.push(`The campaign is closed: ${money(ledger.realized)} realized.`);
  }
  return out;
}
