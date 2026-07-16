import React, { useState } from 'react';
import {
  Sparkles, Loader2, Check, Trash2, RefreshCw, X,
  AlertTriangle, CheckCircle2, ChevronDown, ChevronUp,
} from 'lucide-react';
import { bulkParseTransactions, bulkSaveTransactions } from '../api';
import type { ParsedTransaction } from '../types';

// ─── Constants ────────────────────────────────────────────────────────────────

const TXN_TYPES = ['BUY', 'SELL', 'TRANSFER_IN', 'TRANSFER_OUT', 'OPTION_BUY', 'OPTION_SELL'] as const;
const TXN_LABELS: Record<string, string> = {
  BUY: 'Buy', SELL: 'Sell',
  TRANSFER_IN: 'Transfer In', TRANSFER_OUT: 'Transfer Out',
  OPTION_BUY: 'Option Buy', OPTION_SELL: 'Option Sell',
};
const TXN_COLORS: Record<string, string> = {
  BUY: 'text-success', SELL: 'text-error',
  TRANSFER_IN: 'text-info', TRANSFER_OUT: 'text-warning',
  OPTION_BUY: 'text-success/80', OPTION_SELL: 'text-error/80',
};
const ASSET_TYPES = ['STOCK', 'ETF', 'MUTUAL_FUND', 'OPTION', 'BOND', 'CRYPTO', 'OTHER'] as const;
const ASSET_LABELS: Record<string, string> = {
  STOCK: 'Stock', ETF: 'ETF', MUTUAL_FUND: 'Fund', OPTION: 'Option',
  BOND: 'Bond', CRYPTO: 'Crypto', OTHER: 'Other',
};

const PLACEHOLDER = `Paste your brokerage order log in any format — Schwab, Fidelity, Robinhood, IBKR, etc.

Examples:
  Jun-09-2026  YOU BOUGHT WORLD GOLD TR SPDR GLD MINIS (GLDM) (Cash)
  Symbol: GLDM  Shares: +2.000  Price: $84.10  Amount: -$168.20

  Bought 50 shares of AAPL at $182.50 on 2024-03-10
  Sold 10 TSLA @ $220 on April 1 2024, fees $1.99`;

// ─── Component ────────────────────────────────────────────────────────────────

interface OrderLogImportProps {
  onComplete: () => void;
}

export function OrderLogImport({ onComplete }: OrderLogImportProps) {
  const [text, setText] = useState('');
  const [parsing, setParsing] = useState(false);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [success, setSuccess] = useState<string | null>(null);
  const [parsed, setParsed] = useState<ParsedTransaction[] | null>(null);
  const [showRaw, setShowRaw] = useState(false);

  // Group parsed rows by date for display
  const groupedByDate = React.useMemo(() => {
    if (!parsed) return [];
    const map = new Map<string, ParsedTransaction[]>();
    for (const t of parsed) {
      if (!map.has(t.date)) map.set(t.date, []);
      map.get(t.date)!.push(t);
    }
    return Array.from(map.entries()).sort(([a], [b]) => b.localeCompare(a)); // newest first
  }, [parsed]);

  const handleParse = async () => {
    if (text.trim().length < 10) {
      setError('Please paste your order log (at least a few transactions).');
      return;
    }
    setParsing(true);
    setError(null);
    setSuccess(null);
    setParsed(null);
    try {
      const result = await bulkParseTransactions(text);
      if (result.transactions.length === 0) {
        setError('No trades found. Make sure the text includes tickers, shares, price and date.');
        return;
      }
      setParsed(result.transactions);
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Parse failed');
    } finally {
      setParsing(false);
    }
  };

  const updateRow = (i: number, field: keyof ParsedTransaction, value: string) => {
    if (!parsed) return;
    const updated = [...parsed];
    if (field === 'shares' || field === 'price_per_share' || field === 'fees') {
      (updated[i] as any)[field] = parseFloat(value) || 0;
    } else {
      (updated[i] as any)[field] = value;
    }
    setParsed(updated);
  };

  const removeRow = (i: number) => {
    if (parsed) setParsed(parsed.filter((_, idx) => idx !== i));
  };

  const handleSave = async () => {
    if (!parsed || parsed.length === 0) return;

    for (const t of parsed) {
      if (!t.ticker || t.shares <= 0 || t.price_per_share < 0 || !t.date) {
        setError('Please fix all rows: each needs a ticker, shares > 0, price ≥ 0 and a date.');
        return;
      }
    }

    setSaving(true);
    setError(null);
    try {
      const result = await bulkSaveTransactions(parsed);
      setSuccess(`✓ ${result.count} transaction${result.count !== 1 ? 's' : ''} imported successfully!`);
      setParsed(null);
      setText('');
      setShowRaw(false);
      onComplete();
      setTimeout(() => setSuccess(null), 6000);
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Save failed');
    } finally {
      setSaving(false);
    }
  };

  const totalBuys = parsed?.filter(t => t.transaction_type === 'BUY' || t.transaction_type === 'OPTION_BUY').length ?? 0;
  const totalSells = parsed?.filter(t => t.transaction_type === 'SELL' || t.transaction_type === 'OPTION_SELL').length ?? 0;
  const uniqueTickers = parsed ? new Set(parsed.map(t => t.ticker)).size : 0;

  return (
    <div className="glass-card">
      <div className="p-5 space-y-4">

        {/* Title */}
        <div className="flex items-start justify-between">
          <div>
            <h2 className="font-bold text-sm flex items-center gap-2">
              <Sparkles className="w-4 h-4 text-secondary" />
              Import Order Log (AI)
            </h2>
            <p className="text-xs text-base-content/50 mt-0.5">
              Paste order logs from any broker — Schwab, Fidelity, IBKR, Robinhood, Vanguard&hellip;
              AI will parse trades into your portfolio automatically.
            </p>
          </div>
        </div>

        {/* Alerts */}
        {error && (
          <div className="alert alert-error py-2 text-xs gap-2">
            <AlertTriangle className="w-4 h-4 shrink-0" />
            {error}
          </div>
        )}
        {success && (
          <div className="alert alert-success py-2 text-xs gap-2">
            <CheckCircle2 className="w-4 h-4 shrink-0" />
            {success}
          </div>
        )}

        {/* Input area (shown before parse) */}
        {!parsed && (
          <div className="space-y-2">
            <textarea
              className="textarea textarea-bordered w-full rounded-xl bg-base-200/50 text-xs font-mono resize-none"
              rows={8}
              placeholder={PLACEHOLDER}
              value={text}
              onChange={e => { setText(e.target.value); setError(null); }}
            />
            <div className="flex justify-end">
              <button
                className="btn btn-secondary btn-sm rounded-xl gap-2 shadow-md shadow-secondary/20"
                onClick={handleParse}
                disabled={parsing || text.trim().length < 10}
              >
                {parsing ? <Loader2 className="w-4 h-4 animate-spin" /> : <Sparkles className="w-4 h-4" />}
                {parsing ? 'Parsing…' : 'Parse with AI'}
              </button>
            </div>
          </div>
        )}

        {/* Review table (shown after parse) */}
        {parsed && (
          <div className="space-y-3">
            {/* Summary bar */}
            <div className="flex flex-wrap items-center gap-3 py-2 px-3 rounded-xl bg-base-200/40 border border-base-300/30">
              <span className="text-xs font-semibold text-secondary">{parsed.length} trade{parsed.length !== 1 ? 's' : ''} found</span>
              {uniqueTickers > 0 && <span className="text-xs text-base-content/50">{uniqueTickers} ticker{uniqueTickers !== 1 ? 's' : ''}</span>}
              {totalBuys > 0 && <span className="text-xs text-success font-medium">↑ {totalBuys} buy{totalBuys !== 1 ? 's' : ''}</span>}
              {totalSells > 0 && <span className="text-xs text-error font-medium">↓ {totalSells} sell{totalSells !== 1 ? 's' : ''}</span>}
              <div className="ml-auto flex items-center gap-2">
                <button
                  className="btn btn-ghost btn-xs rounded-lg gap-1"
                  onClick={() => setShowRaw(!showRaw)}
                >
                  {showRaw ? <ChevronUp className="w-3 h-3" /> : <ChevronDown className="w-3 h-3" />}
                  {showRaw ? 'Hide source' : 'Show source'}
                </button>
                <button
                  className="btn btn-ghost btn-xs rounded-lg gap-1"
                  onClick={() => { setParsed(null); setError(null); }}
                >
                  <RefreshCw className="w-3 h-3" /> Re-paste
                </button>
              </div>
            </div>

            {/* Collapsible source text */}
            {showRaw && (
              <div className="rounded-xl bg-base-200/40 border border-base-300/20 p-3 max-h-32 overflow-y-auto">
                <pre className="text-[10px] text-base-content/40 whitespace-pre-wrap font-mono">{text}</pre>
              </div>
            )}

            {/* Grouped by date */}
            <div className="overflow-x-auto rounded-xl border border-base-300/30 max-h-[55vh] overflow-y-auto">
              <table className="table table-xs w-full" style={{ minWidth: '780px' }}>
                <thead className="sticky top-0 z-10">
                  <tr className="bg-base-200/95 text-base-content/50 text-[11px]">
                    <th className="w-24 bg-base-200/95">Date</th>
                    <th className="w-20 bg-base-200/95">Ticker</th>
                    <th className="w-24 bg-base-200/95">Asset</th>
                    <th className="w-28 bg-base-200/95">Type</th>
                    <th className="w-24 text-right bg-base-200/95">Shares</th>
                    <th className="w-28 text-right bg-base-200/95">Price / share</th>
                    <th className="w-20 text-right bg-base-200/95">Fees</th>
                    <th className="w-28 text-right bg-base-200/95">Total</th>
                    <th className="bg-base-200/95">Notes</th>
                    <th className="w-8 bg-base-200/95"></th>
                  </tr>
                </thead>
                <tbody>
                  {groupedByDate.map(([date, rows]) => (
                    <React.Fragment key={date}>
                      {/* Date group separator */}
                      <tr className="bg-base-200/30">
                        <td colSpan={10} className="py-1 px-3">
                          <span className="text-[10px] font-bold uppercase tracking-wider text-base-content/40">{date}</span>
                          <span className="text-[10px] text-base-content/25 ml-2">{rows.length} trade{rows.length !== 1 ? 's' : ''}</span>
                        </td>
                      </tr>
                      {rows.map((t, _) => {
                        const globalIdx = parsed.indexOf(t);
                        return (
                          <tr key={globalIdx} className="hover">
                            <td>
                              <input
                                type="date"
                                className="input input-xs w-full rounded bg-base-200/50"
                                value={t.date}
                                onChange={e => updateRow(globalIdx, 'date', e.target.value)}
                              />
                            </td>
                            <td>
                              <input
                                className="input input-xs w-full rounded bg-base-200/50 uppercase font-bold"
                                value={t.ticker}
                                onChange={e => updateRow(globalIdx, 'ticker', e.target.value.toUpperCase())}
                              />
                            </td>
                            <td>
                              <select
                                className="select select-xs w-full rounded bg-base-200/50"
                                value={t.asset_type}
                                onChange={e => updateRow(globalIdx, 'asset_type', e.target.value)}
                              >
                                {ASSET_TYPES.map(a => (
                                  <option key={a} value={a}>{ASSET_LABELS[a]}</option>
                                ))}
                              </select>
                            </td>
                            <td>
                              <select
                                className="select select-xs w-full rounded bg-base-200/50"
                                value={t.transaction_type}
                                onChange={e => updateRow(globalIdx, 'transaction_type', e.target.value)}
                              >
                                {TXN_TYPES.map(k => (
                                  <option key={k} value={k}>{TXN_LABELS[k]}</option>
                                ))}
                              </select>
                            </td>
                            <td className="text-right">
                              <input
                                type="number"
                                step="0.0001"
                                min="0.0001"
                                className="input input-xs w-full rounded bg-base-200/50 text-right"
                                value={t.shares}
                                onChange={e => updateRow(globalIdx, 'shares', e.target.value)}
                              />
                            </td>
                            <td className="text-right">
                              <input
                                type="number"
                                step="0.0001"
                                min="0"
                                className="input input-xs w-full rounded bg-base-200/50 text-right"
                                value={t.price_per_share}
                                onChange={e => updateRow(globalIdx, 'price_per_share', e.target.value)}
                              />
                            </td>
                            <td className="text-right">
                              <input
                                type="number"
                                step="0.01"
                                min="0"
                                className="input input-xs w-full rounded bg-base-200/50 text-right"
                                value={t.fees}
                                onChange={e => updateRow(globalIdx, 'fees', e.target.value)}
                              />
                            </td>
                            <td className="text-right font-mono text-xs font-semibold">
                              <span className={TXN_COLORS[t.transaction_type] || ''}>
                                {t.transaction_type === 'SELL' || t.transaction_type === 'OPTION_SELL' ? '+' : ''}
                                ${(t.shares * t.price_per_share + (t.fees || 0)).toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 2 })}
                              </span>
                            </td>
                            <td>
                              <input
                                className="input input-xs w-full rounded bg-base-200/50 text-xs"
                                value={t.notes}
                                placeholder="notes"
                                onChange={e => updateRow(globalIdx, 'notes', e.target.value)}
                              />
                            </td>
                            <td>
                              <button
                                className="btn btn-ghost btn-xs text-error rounded"
                                onClick={() => removeRow(globalIdx)}
                                title="Remove this row"
                              >
                                <X className="w-3 h-3" />
                              </button>
                            </td>
                          </tr>
                        );
                      })}
                    </React.Fragment>
                  ))}
                </tbody>
              </table>
            </div>

            {/* Action buttons */}
            <div className="flex items-center justify-between pt-1">
              <button
                className="btn btn-ghost btn-sm rounded-xl gap-1 text-base-content/50"
                onClick={() => { setParsed(null); setText(''); setError(null); }}
              >
                <Trash2 className="w-3.5 h-3.5" /> Clear all
              </button>
              <button
                className="btn btn-secondary btn-sm rounded-xl gap-2 shadow-md shadow-secondary/20"
                onClick={handleSave}
                disabled={saving || parsed.length === 0}
              >
                {saving ? <Loader2 className="w-4 h-4 animate-spin" /> : <Check className="w-4 h-4" />}
                {saving ? 'Importing…' : `Import ${parsed.length} Trade${parsed.length !== 1 ? 's' : ''}`}
              </button>
            </div>
          </div>
        )}
      </div>
    </div>
  );
}
