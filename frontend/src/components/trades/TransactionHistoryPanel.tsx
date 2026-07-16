/**
 * TransactionHistoryPanel — complete position ledger with inline edit / delete.
 *
 * Each row shows the transaction plus the running position snapshot after it:
 *   Running Qty · Avg Cost · Realized P&L per sale
 *
 * Editing: click the pencil icon → an inline form expands below the row.
 * Deleting: click the trash icon → inline confirm before deletion.
 *
 * After every mutation the panel re-fetches its own data and calls
 * onPositionChanged() so the parent can refresh trade list + live P&L.
 *
 * Synthetic "id: 0" rows (reconstructed from trade metadata) are display-only
 * and cannot be edited or deleted.
 */

import React, { useEffect, useState, useCallback } from 'react';
import {
  Loader2, AlertCircle, TrendingUp, TrendingDown,
  DollarSign, BarChart2, Activity, Hash,
  Pencil, Trash2, Check, X as XIcon, Save,
} from 'lucide-react';
import type { SavedStrategyItem, LivePnlResponse, TradeTransaction } from '../../api';
import { updateTradeTransaction, deleteTradeTransaction } from '../../api';
import { fmtMoney, fmtQty, fmtDate } from '../../lib/tradeFormat';

// ── Types ────────────────────────────────────────────────────────────────────

interface Props {
  strategyId: number;
  onFetch: (id: number) => Promise<TradeTransaction[]>;
  trade?: SavedStrategyItem;
  pnl?: LivePnlResponse | null;
  /** Called after any successful edit or delete so parent can refresh P&L */
  onPositionChanged?: () => void;
}

// ── Action display config ────────────────────────────────────────────────────

const ACTION_META: Record<string, { label: string; color: string; bgColor: string; sign: 1 | -1 | 0 }> = {
  open:   { label: 'Opened',        color: 'text-success',         bgColor: 'bg-success/10',  sign:  1 },
  add:    { label: 'Added',         color: 'text-success',         bgColor: 'bg-success/10',  sign:  1 },
  reduce: { label: 'Partial Close', color: 'text-warning',         bgColor: 'bg-warning/10',  sign: -1 },
  close:  { label: 'Closed',        color: 'text-error',           bgColor: 'bg-error/10',    sign: -1 },
  adjust: { label: 'Adjustment',    color: 'text-base-content/50', bgColor: 'bg-base-300/30', sign:  0 },
};

const ACTION_OPTIONS = ['open', 'add', 'reduce', 'close', 'adjust'] as const;

function ActionBadge({ action }: { action: string }) {
  const m = ACTION_META[action] ?? { label: action, color: 'text-base-content/50', bgColor: 'bg-base-300/30', sign: 0 as const };
  return (
    <span className={`inline-flex items-center gap-1 text-[10px] font-bold uppercase tracking-wide px-2 py-0.5 rounded-full ${m.color} ${m.bgColor}`}>
      {m.sign > 0  && <TrendingUp  className="w-2.5 h-2.5" />}
      {m.sign < 0  && <TrendingDown className="w-2.5 h-2.5" />}
      {m.label}
    </span>
  );
}

// ── Summary metric card ──────────────────────────────────────────────────────

function SummaryCell({ icon, label, value, subValue, color = '' }: {
  icon: React.ReactNode; label: string; value: string; subValue?: string; color?: string;
}) {
  return (
    <div className="bg-base-300/20 rounded-xl px-3 py-2.5 flex flex-col gap-0.5">
      <div className="flex items-center gap-1 text-[9px] uppercase tracking-wider text-base-content/40">
        {icon}{label}
      </div>
      <div className={`text-sm font-bold ${color}`}>{value}</div>
      {subValue && <div className="text-[10px] text-base-content/40">{subValue}</div>}
    </div>
  );
}

// ── Ledger walk ──────────────────────────────────────────────────────────────

interface RowState {
  txn: TradeTransaction;
  txnQty: number;
  txnValue: number;
  legRealized: number;
  runningQty: number;
  runningAvgCost: number;
  runningCostBasis: number;
}

function walkTransactions(txns: TradeTransaction[]): {
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

// ── Inline edit form ─────────────────────────────────────────────────────────

interface EditFormProps {
  txn: TradeTransaction;
  colSpan: number;
  onSave: (data: {
    action: string; quantity: number; price: number; fees: number;
    executed_at: string; note: string;
  }) => Promise<void>;
  onCancel: () => void;
}

function InlineEditForm({ txn, colSpan, onSave, onCancel }: EditFormProps) {
  const [action, setAction] = useState(txn.action);
  const [qty, setQty]       = useState(String(Math.abs(txn.quantity)));
  const [price, setPrice]   = useState(String(txn.price));
  const [fees, setFees]     = useState(String(txn.fees || 0));
  const [date, setDate]     = useState(txn.executed_at ? txn.executed_at.slice(0, 10) : '');
  const [note, setNote]     = useState(txn.note || '');
  const [saving, setSaving] = useState(false);
  const [err, setErr]       = useState<string | null>(null);

  // Preserve original sign convention (reduce/close are negative)
  const isSell = ACTION_META[action]?.sign === -1;

  const handleSave = async () => {
    setSaving(true);
    setErr(null);
    try {
      const signedQty = isSell ? -Math.abs(parseFloat(qty) || 0) : Math.abs(parseFloat(qty) || 0);
      await onSave({
        action,
        quantity: signedQty,
        price: parseFloat(price) || 0,
        fees: parseFloat(fees) || 0,
        executed_at: new Date(date + 'T16:00:00').toISOString(),
        note,
      });
    } catch (e: any) {
      setErr(e?.message || 'Save failed');
      setSaving(false);
    }
  };

  return (
    <tr className="bg-primary/5 border-y border-primary/15">
      <td colSpan={colSpan} className="px-3 py-3">
        <div className="space-y-3">
          <div className="text-[10px] uppercase tracking-wider text-primary/60 font-semibold">Edit Transaction</div>

          <div className="grid grid-cols-2 sm:grid-cols-3 md:grid-cols-6 gap-2">
            {/* Action */}
            <div>
              <label className="text-[9px] uppercase text-base-content/40 mb-0.5 block">Action</label>
              <select
                className="select select-bordered select-xs w-full"
                value={action}
                onChange={e => setAction(e.target.value)}
              >
                {ACTION_OPTIONS.map(a => (
                  <option key={a} value={a}>{ACTION_META[a]?.label ?? a}</option>
                ))}
              </select>
            </div>

            {/* Qty */}
            <div>
              <label className="text-[9px] uppercase text-base-content/40 mb-0.5 block">
                Qty {isSell ? '(will be negative)' : ''}
              </label>
              <input
                type="number" min={0} step="any"
                className="input input-bordered input-xs w-full"
                value={qty}
                onChange={e => setQty(e.target.value)}
              />
            </div>

            {/* Price */}
            <div>
              <label className="text-[9px] uppercase text-base-content/40 mb-0.5 block">Price / share</label>
              <div className="relative">
                <span className="absolute left-2 top-1/2 -translate-y-1/2 text-base-content/40 text-[10px]">$</span>
                <input
                  type="number" min={0} step="0.01"
                  className="input input-bordered input-xs w-full pl-5"
                  value={price}
                  onChange={e => setPrice(e.target.value)}
                />
              </div>
            </div>

            {/* Fees */}
            <div>
              <label className="text-[9px] uppercase text-base-content/40 mb-0.5 block">Fees</label>
              <div className="relative">
                <span className="absolute left-2 top-1/2 -translate-y-1/2 text-base-content/40 text-[10px]">$</span>
                <input
                  type="number" min={0} step="0.01"
                  className="input input-bordered input-xs w-full pl-5"
                  value={fees}
                  onChange={e => setFees(e.target.value)}
                />
              </div>
            </div>

            {/* Date */}
            <div>
              <label className="text-[9px] uppercase text-base-content/40 mb-0.5 block">Date</label>
              <input
                type="date"
                className="input input-bordered input-xs w-full"
                value={date}
                onChange={e => setDate(e.target.value)}
              />
            </div>

            {/* Note */}
            <div>
              <label className="text-[9px] uppercase text-base-content/40 mb-0.5 block">Note</label>
              <input
                type="text" placeholder="Optional"
                className="input input-bordered input-xs w-full"
                value={note}
                onChange={e => setNote(e.target.value)}
              />
            </div>
          </div>

          {err && (
            <p className="text-xs text-error flex items-center gap-1">
              <AlertCircle className="w-3 h-3 shrink-0" />{err}
            </p>
          )}

          <div className="flex items-center gap-2">
            <button
              className="btn btn-primary btn-xs gap-1"
              onClick={handleSave}
              disabled={saving || !qty || !price || !date}
            >
              {saving ? <Loader2 className="w-3 h-3 animate-spin" /> : <Save className="w-3 h-3" />}
              {saving ? 'Saving…' : 'Save changes'}
            </button>
            <button className="btn btn-ghost btn-xs gap-1" onClick={onCancel}>
              <XIcon className="w-3 h-3" /> Cancel
            </button>
          </div>
        </div>
      </td>
    </tr>
  );
}

// ── Main component ───────────────────────────────────────────────────────────

const TOTAL_COLS = 11; // number of <th> columns in the table

export default function TransactionHistoryPanel({ strategyId, onFetch, trade, pnl, onPositionChanged }: Props) {
  const [txns, setTxns]       = useState<TradeTransaction[] | null>(null);
  const [loading, setLoading] = useState(false);
  const [err, setErr]         = useState<string | null>(null);
  const [editingId, setEditingId]           = useState<number | null>(null);
  const [confirmDeleteId, setConfirmDeleteId] = useState<number | null>(null);
  const [mutating, setMutating] = useState(false);

  const loadTxns = useCallback(async () => {
    setLoading(true);
    setErr(null);
    try {
      const data = await onFetch(strategyId);
      setTxns(data);
    } catch (e: any) {
      setErr(e?.message || 'Failed to load history');
    } finally {
      setLoading(false);
    }
  }, [strategyId, onFetch]);

  useEffect(() => { loadTxns(); }, [loadTxns]);

  const handleEdit = async (txId: number, data: Parameters<typeof updateTradeTransaction>[2]) => {
    setMutating(true);
    try {
      await updateTradeTransaction(strategyId, txId, data);
      setEditingId(null);
      await loadTxns();
      onPositionChanged?.();
    } finally {
      setMutating(false);
    }
  };

  const handleDelete = async (txId: number) => {
    setMutating(true);
    try {
      await deleteTradeTransaction(strategyId, txId);
      setConfirmDeleteId(null);
      await loadTxns();
      onPositionChanged?.();
    } finally {
      setMutating(false);
    }
  };

  // ── Render states ──────────────────────────────────────────────────────────

  if (loading && !txns) {
    return (
      <div className="flex items-center gap-2 py-4 text-xs text-base-content/40">
        <Loader2 className="w-3.5 h-3.5 animate-spin" />Loading transaction history…
      </div>
    );
  }

  if (err) {
    return (
      <div className="flex items-center gap-1.5 text-xs text-error py-2">
        <AlertCircle className="w-3.5 h-3.5 shrink-0" />{err}
      </div>
    );
  }

  if (!txns || txns.length === 0) {
    return <p className="text-xs text-base-content/40 py-2 italic">No transactions recorded yet.</p>;
  }

  const { rows, totalFees, totalRealized, finalQty, finalAvgCost, finalCostBasis } =
    walkTransactions(txns);

  const isClosed       = finalQty < 1e-9;
  const unrealizedPnl  = pnl?.unrealized_pnl ?? null;
  const currentPrice   = pnl?.underlying_price ?? null;
  const totalReturn    = unrealizedPnl != null ? totalRealized + unrealizedPnl : null;
  const buyCount       = txns.filter(t => t.quantity > 0).length;
  const sellCount      = txns.filter(t => t.quantity < 0).length;

  return (
    <div className="space-y-4">

      {/* Section header */}
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-2">
          <Activity className="w-3.5 h-3.5 text-base-content/40" />
          <span className="text-[10px] uppercase tracking-widest text-base-content/40 font-semibold">
            Transaction History
          </span>
          {loading && <Loader2 className="w-3 h-3 animate-spin text-base-content/30" />}
        </div>
        <div className="flex items-center gap-2 text-[10px] text-base-content/30">
          <span>{txns.length} transaction{txns.length !== 1 ? 's' : ''}</span>
          {buyCount  > 0 && <span className="text-success/60">{buyCount}  buy</span>}
          {sellCount > 0 && <span className="text-error/60">{sellCount} sell</span>}
        </div>
      </div>

      {/* ── Summary ───────────────────────────────────────────────────────
           When live P&L is available the Position Details panel above already
           shows shares / avg cost / cost basis / unrealized P&L.  Show only
           ledger-history metrics here to avoid duplicating that information.
           When P&L hasn't been fetched yet show the full snapshot as a fallback.
      ─────────────────────────────────────────────────────────────────── */}
      {pnl ? (
        /* Compact history-only strip */
        <div className="flex flex-wrap items-center gap-x-5 gap-y-1.5 text-xs text-base-content/50 px-1">
          {isClosed && (
            <span className="font-semibold text-base-content/40 uppercase text-[10px] tracking-wide">Position Closed</span>
          )}
          {(totalRealized !== 0 || sellCount > 0) && (
            <span>
              Realized P&L:{' '}
              <span className={`font-semibold ${totalRealized > 0 ? 'text-success' : totalRealized < 0 ? 'text-error' : ''}`}>
                {totalRealized >= 0 ? '+' : ''}{fmtMoney(Math.abs(totalRealized))}
              </span>
            </span>
          )}
          {totalReturn != null && (totalRealized !== 0 || sellCount > 0) && (
            <span>
              Total return:{' '}
              <span className={`font-semibold ${totalReturn >= 0 ? 'text-success' : 'text-error'}`}>
                {totalReturn >= 0 ? '+' : ''}{fmtMoney(Math.abs(totalReturn))}
              </span>
            </span>
          )}
          {totalFees > 0 && (
            <span>Fees paid: <span className="font-medium text-base-content/60">{fmtMoney(totalFees)}</span></span>
          )}
          {totalRealized === 0 && sellCount === 0 && (
            <span className="text-base-content/30 italic text-[10px]">No realized P&L yet — all positions still open</span>
          )}
        </div>
      ) : (
        /* Full snapshot grid — shown when P&L hasn't been loaded yet */
        <div className="grid grid-cols-2 sm:grid-cols-3 lg:grid-cols-4 gap-2">
          <SummaryCell
            icon={<Hash className="w-2.5 h-2.5" />}
            label={isClosed ? 'Status' : 'Open Position'}
            value={isClosed ? 'CLOSED' : `${fmtQty(finalQty)} shares`}
            color={isClosed ? 'text-base-content/40' : 'text-primary'}
          />
          {!isClosed && finalAvgCost > 0 && (
            <SummaryCell
              icon={<DollarSign className="w-2.5 h-2.5" />}
              label="Avg Cost / Share"
              value={fmtMoney(finalAvgCost)}
            />
          )}
          {finalCostBasis > 0 && (
            <SummaryCell
              icon={<BarChart2 className="w-2.5 h-2.5" />}
              label={isClosed ? 'Was Invested' : 'Cost Basis'}
              value={fmtMoney(finalCostBasis)}
              subValue={!isClosed && finalQty > 0 ? `${fmtQty(finalQty)} × ${fmtMoney(finalAvgCost)}` : undefined}
            />
          )}
          {(totalRealized !== 0 || sellCount > 0) && (
            <SummaryCell
              icon={<DollarSign className="w-2.5 h-2.5" />}
              label="Realized P&L"
              value={`${totalRealized >= 0 ? '+' : ''}${fmtMoney(Math.abs(totalRealized))}`}
              color={totalRealized > 0 ? 'text-success' : totalRealized < 0 ? 'text-error' : 'text-base-content/50'}
            />
          )}
          {totalFees > 0 && (
            <SummaryCell
              icon={<DollarSign className="w-2.5 h-2.5" />}
              label="Total Fees"
              value={fmtMoney(totalFees)}
              color="text-base-content/50"
            />
          )}
        </div>
      )}

      {/* ── Transaction table ──────────────────────────────────────────────── */}
      <div className="overflow-x-auto -mx-1 rounded-xl border border-white/[0.04]">
        <table className="table table-xs text-xs w-full">
          <thead>
            <tr className="text-[9px] uppercase tracking-wider text-base-content/30 bg-base-300/20">
              <th className="pl-3">Date</th>
              <th>Action</th>
              <th className="text-right">Qty</th>
              <th className="text-right">Price</th>
              <th className="text-right">Value</th>
              <th className="text-right hidden sm:table-cell">Fees</th>
              <th className="text-right border-l border-white/[0.05]">After: Shares</th>
              <th className="text-right">After: Avg Cost</th>
              <th className="text-right hidden md:table-cell">Realized P&L</th>
              <th className="hidden lg:table-cell">Note</th>
              {/* Actions column — always visible */}
              <th className="w-14 text-center">Edit</th>
            </tr>
          </thead>
          <tbody>
            {rows.map(({ txn, txnQty, txnValue, legRealized, runningQty, runningAvgCost }, i) => {
              const isBuy      = txnQty > 0;
              const isSell     = txnQty < 0;
              const isLast     = i === rows.length - 1;
              const rowClosed  = runningQty < 1e-9;
              const isSynthetic = txn.id === 0; // reconstructed row — no DB record
              const isEditing  = editingId === txn.id && !isSynthetic;
              const isConfirm  = confirmDeleteId === txn.id && !isSynthetic;

              return (
                <React.Fragment key={`${txn.id}-${i}`}>
                  <tr className={`
                    transition-colors
                    ${isEditing ? 'bg-primary/5' : 'hover:bg-base-300/20'}
                    ${isLast ? '' : 'border-b border-white/[0.03]'}
                  `}>
                    {/* Date */}
                    <td className="pl-3 whitespace-nowrap text-base-content/50 text-[10px]">
                      {fmtDate(txn.executed_at)}
                      {isSynthetic && (
                        <span className="ml-1 text-[8px] text-base-content/25 italic">(reconstructed)</span>
                      )}
                    </td>

                    {/* Action */}
                    <td><ActionBadge action={txn.action} /></td>

                    {/* Qty */}
                    <td className={`text-right font-mono font-semibold ${isBuy ? 'text-success/80' : isSell ? 'text-error/80' : 'text-base-content/50'}`}>
                      {txnQty > 0 ? '+' : ''}{txnQty % 1 === 0 ? txnQty.toFixed(0) : txnQty.toFixed(2)}
                    </td>

                    {/* Price */}
                    <td className="text-right font-mono">{fmtMoney(txn.price)}</td>

                    {/* Value */}
                    <td className="text-right font-mono text-base-content/60">{fmtMoney(txnValue)}</td>

                    {/* Fees */}
                    <td className="text-right font-mono text-base-content/35 hidden sm:table-cell">
                      {txn.fees > 0 ? fmtMoney(txn.fees) : '—'}
                    </td>

                    {/* After: Shares */}
                    <td className="text-right font-mono font-semibold border-l border-white/[0.04]">
                      {rowClosed
                        ? <span className="text-base-content/30 text-[10px]">Closed</span>
                        : <span className="text-primary/80">{fmtQty(runningQty)}</span>}
                    </td>

                    {/* After: Avg Cost */}
                    <td className="text-right font-mono">
                      {runningAvgCost > 0
                        ? <span className="text-base-content/70">{fmtMoney(runningAvgCost)}</span>
                        : <span className="text-base-content/25">—</span>}
                    </td>

                    {/* Realized P&L */}
                    <td className={`text-right font-mono hidden md:table-cell ${
                      legRealized > 0 ? 'text-success' : legRealized < 0 ? 'text-error' : 'text-base-content/25'
                    }`}>
                      {legRealized !== 0 ? `${legRealized > 0 ? '+' : ''}${fmtMoney(Math.abs(legRealized))}` : '—'}
                    </td>

                    {/* Note */}
                    <td className="text-base-content/35 text-[10px] max-w-[100px] truncate hidden lg:table-cell">
                      {txn.note || ''}
                    </td>

                    {/* Edit / Delete actions */}
                    <td className="text-center">
                      {isSynthetic ? (
                        <span className="text-[9px] text-base-content/20 italic">auto</span>
                      ) : isConfirm ? (
                        /* Confirm delete inline */
                        <div className="flex items-center justify-center gap-1">
                          <button
                            className="btn btn-error btn-xs h-5 min-h-0 px-1.5 text-[9px] gap-0.5"
                            onClick={() => handleDelete(txn.id)}
                            disabled={mutating}
                          >
                            {mutating ? <Loader2 className="w-2.5 h-2.5 animate-spin" /> : <Check className="w-2.5 h-2.5" />}
                            Yes
                          </button>
                          <button
                            className="btn btn-ghost btn-xs h-5 min-h-0 px-1 text-[9px]"
                            onClick={() => setConfirmDeleteId(null)}
                            disabled={mutating}
                          >
                            <XIcon className="w-2.5 h-2.5" />
                          </button>
                        </div>
                      ) : (
                        /* Normal edit / delete icons */
                        <div className="flex items-center justify-center gap-1">
                          <button
                            className={`btn btn-ghost btn-xs h-6 min-h-0 px-1.5 ${isEditing ? 'text-primary' : 'text-base-content/30 hover:text-primary'}`}
                            onClick={() => setEditingId(prev => prev === txn.id ? null : txn.id)}
                            title="Edit transaction"
                            disabled={mutating}
                          >
                            <Pencil className="w-3 h-3" />
                          </button>
                          <button
                            className="btn btn-ghost btn-xs h-6 min-h-0 px-1.5 text-base-content/30 hover:text-error"
                            onClick={() => setConfirmDeleteId(txn.id)}
                            title="Delete transaction"
                            disabled={mutating}
                          >
                            <Trash2 className="w-3 h-3" />
                          </button>
                        </div>
                      )}
                    </td>
                  </tr>

                  {/* Inline edit form — expands below the row */}
                  {isEditing && (
                    <InlineEditForm
                      txn={txn}
                      colSpan={TOTAL_COLS}
                      onSave={data => handleEdit(txn.id, data)}
                      onCancel={() => setEditingId(null)}
                    />
                  )}
                </React.Fragment>
              );
            })}
          </tbody>

          {/* Totals footer */}
          {rows.length > 1 && (
            <tfoot>
              <tr className="border-t border-white/[0.08] bg-base-300/10 text-[10px] text-base-content/40">
                <td colSpan={5} className="pl-3 py-1.5 font-medium">Totals</td>
                <td className="text-right hidden sm:table-cell">{totalFees > 0 ? fmtMoney(totalFees) : '—'}</td>
                <td className="text-right border-l border-white/[0.04]">
                  {finalQty > 0
                    ? <span className="text-primary/70">{fmtQty(finalQty)} open</span>
                    : <span className="text-base-content/25">Closed</span>}
                </td>
                <td className="text-right">{finalAvgCost > 0 ? fmtMoney(finalAvgCost) : '—'}</td>
                <td className={`text-right hidden md:table-cell font-semibold ${
                  totalRealized > 0 ? 'text-success' : totalRealized < 0 ? 'text-error' : ''
                }`}>
                  {totalRealized !== 0 ? `${totalRealized > 0 ? '+' : ''}${fmtMoney(Math.abs(totalRealized))}` : '—'}
                </td>
                <td className="hidden lg:table-cell" />
                <td />
              </tr>
            </tfoot>
          )}
        </table>
      </div>
    </div>
  );
}
