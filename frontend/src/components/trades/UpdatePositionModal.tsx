/**
 * UpdatePositionModal — add to, reduce, or close an existing position.
 *
 * Writes a new transaction to the ledger via POST /api/saved-strategies/{id}/transactions.
 * Action can be:
 *   add    — bought more shares / contracts (adds to open lots)
 *   reduce — sold some (partial close, FIFO matched by backend)
 *   close  — close entire position (marks trade as closed)
 *   adjust — custom: fees, corporate action, note only
 */

import React, { useState } from 'react';
import {
  X, TrendingUp, TrendingDown, Scissors, Wrench, Loader2, AlertCircle, CheckCircle2,
} from 'lucide-react';
import type { SavedStrategyItem } from '../../api';
import { fmtMoney, fmtQty, fmtDate } from '../../lib/tradeFormat';

type Action = 'add' | 'reduce' | 'close' | 'adjust';

interface Props {
  open: boolean;
  onClose: () => void;
  trade: SavedStrategyItem;
  onTransactionSaved: () => void;
  /** Injected so modal doesn't need to import api directly — cleaner testing */
  onSave: (data: {
    action: string;
    quantity: number;
    price: number;
    fees?: number;
    executed_at: string;
    note?: string | null;
  }) => Promise<void>;
}

const today = () => new Date().toISOString().slice(0, 10);

const ACTION_CONFIG: Record<Action, {
  label: string;
  color: string;
  icon: React.ReactNode;
  desc: string;
  qtyLabel: string;
  priceLabel: string;
}> = {
  add: {
    label: 'Add to Position',
    color: 'success',
    icon: <TrendingUp className="w-4 h-4" />,
    desc: 'Bought more — new lot added at the specified price.',
    qtyLabel: 'Qty / Shares added',
    priceLabel: 'Price paid / share ($)',
  },
  reduce: {
    label: 'Partial Close',
    color: 'warning',
    icon: <Scissors className="w-4 h-4" />,
    desc: 'Sold some — reduces open position (FIFO lot matching).',
    qtyLabel: 'Qty / Shares sold',
    priceLabel: 'Price received / share ($)',
  },
  close: {
    label: 'Close Entire Position',
    color: 'error',
    icon: <CheckCircle2 className="w-4 h-4" />,
    desc: 'Close out all remaining shares or contracts. Marks trade as Closed.',
    qtyLabel: 'Qty (leave blank = all)',
    priceLabel: 'Exit price / share ($)',
  },
  adjust: {
    label: 'Adjust / Note',
    color: 'info',
    icon: <Wrench className="w-4 h-4" />,
    desc: 'Log fees, corporate actions, or an annotation without changing qty.',
    qtyLabel: 'Qty (0 = note only)',
    priceLabel: 'Price ($)',
  },
};

export default function UpdatePositionModal({ open, onClose, trade, onTransactionSaved, onSave }: Props) {
  const [action, setAction] = useState<Action>('add');
  const [qty, setQty] = useState('');
  const [price, setPrice] = useState('');
  const [fees, setFees] = useState('');
  const [date, setDate] = useState(today());
  const [note, setNote] = useState('');
  const [saving, setSaving] = useState(false);
  const [err, setErr] = useState<string | null>(null);

  if (!open) return null;

  const cfg = ACTION_CONFIG[action];
  const isStock = trade.strategy_type?.startsWith('stock');
  const isFutures = trade.strategy_type === 'futures';
  const multiplier = trade.parameters?.multiplier ?? 1.0;
  const qtyFloat = parseFloat(qty) || 0;
  const priceFloat = parseFloat(price) || 0;
  const feesFloat = parseFloat(fees) || 0;
  const total = qtyFloat > 0 && priceFloat > 0 ? qtyFloat * priceFloat * (isFutures ? multiplier : 1.0) : null;

  const canSave =
    date &&
    priceFloat >= 0 &&
    (action === 'adjust' ? true : qtyFloat > 0 || action === 'close');

  const handleSave = async () => {
    if (!canSave) return;
    setSaving(true);
    setErr(null);
    try {
      const finalQty = action === 'close' && !qty
        ? (trade.parameters?.contracts ?? trade.parameters?.shares ?? 1)
        : qtyFloat;
      // Reduce and close use negative quantity convention
      const signedQty = (action === 'reduce' || action === 'close') ? -Math.abs(finalQty) : finalQty;

      await onSave({
        action,
        quantity: signedQty,
        price: priceFloat,
        fees: feesFloat || undefined,
        executed_at: new Date(date + 'T16:00:00').toISOString(),
        note: note.trim() || null,
      });
      onTransactionSaved();
      onClose();
    } catch (e: any) {
      setErr(e?.message || 'Failed to save transaction');
    } finally {
      setSaving(false);
    }
  };

  // avg_cost in parameters is the ledger-computed weighted average (updated on every
  // transaction). Fall back to the original entry price only for the first trade.
  const avgCost = trade.parameters?.avg_cost ?? trade.entry_prices?.[0]?.price ?? null;
  const entryPrice = avgCost;
  const shares = trade.parameters?.contracts ?? trade.parameters?.shares ?? null;

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 backdrop-blur-sm p-4">
      <div className="bg-base-200 rounded-2xl border border-white/10 w-full max-w-md shadow-2xl flex flex-col">

        {/* Header */}
        <div className="flex items-center justify-between px-5 py-4 border-b border-white/[0.06]">
          <div>
            <p className="font-semibold text-sm">Update Position</p>
            <p className="text-xs text-base-content/40 mt-0.5">
              <span className="font-mono font-bold text-base-content/70">{trade.ticker}</span>
              {' · '}{trade.name}
            </p>
          </div>
          <button onClick={onClose} className="btn btn-ghost btn-sm btn-square">
            <X className="w-4 h-4" />
          </button>
        </div>

        {/* Current position summary */}
        {isFutures ? (
          <div className="px-5 py-3.5 bg-base-300/30 border-b border-white/[0.04] space-y-2 text-xs text-base-content/60">
            <div className="flex flex-wrap gap-x-4 gap-y-1">
              <span>Position: <span className="text-base-content font-medium">{shares ? fmtQty(shares) : '0'} contracts</span></span>
              {entryPrice && <span>Avg Cost: <span className="text-base-content font-medium">{fmtMoney(entryPrice)}</span></span>}
            </div>
            <div className="grid grid-cols-2 gap-x-4 gap-y-1 pt-1.5 border-t border-white/[0.04] text-[11px]">
              <div>Notional Value: <span className="text-base-content font-semibold">{fmtMoney((entryPrice || 0) * (shares || 1) * multiplier)}</span></div>
              <div>Margin Required: <span className="text-base-content font-semibold">{trade.parameters?.margin_req ? fmtMoney(parseFloat(trade.parameters.margin_req)) : '—'}</span></div>
              <div>Leverage: <span className="text-base-content font-semibold">{trade.parameters?.margin_req && parseFloat(trade.parameters.margin_req) > 0 ? `${(((entryPrice || 0) * (shares || 1) * multiplier) / parseFloat(trade.parameters.margin_req)).toFixed(1)}x` : '—'}</span></div>
              <div>Notional Delta: <span className={`font-semibold ${trade.parameters?.action === 'short' ? 'text-error/80' : 'text-success/80'}`}>{trade.parameters?.action === 'short' ? '-' : '+'}{fmtMoney((entryPrice || 0) * (shares || 1) * multiplier)}</span></div>
            </div>
          </div>
        ) : (
          <div className="px-5 py-3 bg-base-300/30 border-b border-white/[0.04] flex gap-4 text-xs text-base-content/60">
            {shares && <span>Position: <span className="text-base-content font-medium">{fmtQty(shares)} {isStock ? 'shares' : 'contracts'}</span></span>}
            {entryPrice && (
              <span>
                {trade.parameters?.avg_cost ? 'Avg Cost' : 'Entry'}:{' '}
                <span className="text-base-content font-medium">{fmtMoney(entryPrice)}</span>
              </span>
            )}
            {trade.entry_date && <span>Since: <span className="text-base-content font-medium">{fmtDate(trade.entry_date)}</span></span>}
          </div>
        )}

        {/* Body */}
        <div className="p-5 space-y-5">

          {/* Action selector */}
          <div>
            <p className="text-xs font-medium text-base-content/50 mb-2">Action</p>
            <div className="grid grid-cols-2 gap-2">
              {(Object.entries(ACTION_CONFIG) as [Action, typeof cfg][]).map(([a, c]) => (
                <button
                  key={a}
                  type="button"
                  onClick={() => setAction(a)}
                  className={`flex items-center gap-2 px-3 py-2.5 rounded-xl border text-xs font-semibold transition-all text-left
                    ${action === a
                      ? `border-${c.color}/40 bg-${c.color}/10 text-${c.color}`
                      : 'border-white/[0.08] text-base-content/50 hover:border-white/15 hover:text-base-content/80'
                    }`}
                >
                  {c.icon}
                  {c.label}
                </button>
              ))}
            </div>
            <p className="text-[10px] text-base-content/40 mt-1.5">{cfg.desc}</p>
          </div>

          {/* Fields */}
          <div className="grid grid-cols-2 gap-3">
            <div>
              <label className="text-[10px] uppercase text-base-content/40 mb-1 block">{cfg.qtyLabel}</label>
              <input
                type="number"
                min={0}
                step={isStock ? 1 : 1}
                placeholder={action === 'close' ? 'All' : '0'}
                className="input input-bordered input-sm w-full"
                value={qty}
                onChange={e => setQty(e.target.value)}
              />
            </div>
            <div>
              <label className="text-[10px] uppercase text-base-content/40 mb-1 block">{cfg.priceLabel}</label>
              <div className="relative">
                <span className="absolute left-3 top-1/2 -translate-y-1/2 text-base-content/40 text-sm">$</span>
                <input
                  type="number"
                  min={0}
                  step={0.01}
                  placeholder="0.00"
                  className="input input-bordered input-sm w-full pl-6"
                  value={price}
                  onChange={e => setPrice(e.target.value)}
                />
              </div>
            </div>
            <div>
              <label className="text-[10px] uppercase text-base-content/40 mb-1 block">Fees / Commission ($)</label>
              <div className="relative">
                <span className="absolute left-3 top-1/2 -translate-y-1/2 text-base-content/40 text-sm">$</span>
                <input
                  type="number"
                  min={0}
                  step={0.01}
                  placeholder="0.00"
                  className="input input-bordered input-sm w-full pl-6"
                  value={fees}
                  onChange={e => setFees(e.target.value)}
                />
              </div>
            </div>
            <div>
              <label className="text-[10px] uppercase text-base-content/40 mb-1 block">Date</label>
              <input
                type="date"
                className="input input-bordered input-sm w-full"
                value={date}
                onChange={e => setDate(e.target.value)}
              />
            </div>
          </div>

          {/* Total */}
          {total !== null && (
            <p className="text-xs text-base-content/50">
              {action === 'add' ? 'Total cost' : action === 'reduce' || action === 'close' ? 'Total proceeds' : 'Total'}:{' '}
              <span className="font-semibold text-base-content">{fmtMoney(total)}</span>
              {feesFloat > 0 && <span className="text-base-content/40 ml-1">(+{fmtMoney(feesFloat)} fees)</span>}
            </p>
          )}

          {/* Note */}
          <div>
            <label className="text-[10px] uppercase text-base-content/40 mb-1 block">Note (optional)</label>
            <input
              type="text"
              placeholder="Why this update? (e.g. earnings ahead, stop-loss triggered)"
              className="input input-bordered input-sm w-full"
              value={note}
              onChange={e => setNote(e.target.value)}
            />
          </div>

          {err && (
            <p className="text-xs text-error flex items-center gap-1.5">
              <AlertCircle className="w-3.5 h-3.5 shrink-0" />{err}
            </p>
          )}
        </div>

        {/* Footer */}
        <div className="px-5 py-4 border-t border-white/[0.06] flex items-center justify-end gap-2">
          <button onClick={onClose} className="btn btn-ghost btn-sm">Cancel</button>
          <button
            onClick={handleSave}
            disabled={!canSave || saving}
            className={`btn btn-sm gap-2 btn-${cfg.color}`}
          >
            {saving ? <Loader2 className="w-3.5 h-3.5 animate-spin" /> : cfg.icon}
            {saving ? 'Saving…' : cfg.label}
          </button>
        </div>

      </div>
    </div>
  );
}
