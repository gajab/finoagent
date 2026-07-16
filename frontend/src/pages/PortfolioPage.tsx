import React, { useState, useEffect, useCallback, useMemo } from 'react';
import { useNavigate } from 'react-router-dom';
import {
  Briefcase, Plus, Trash2, Loader2, AlertCircle, CheckCircle,
  TrendingUp, TrendingDown, ExternalLink, X, FileText, Pencil,
  Check, XCircle, RefreshCw, ChevronDown, ChevronUp, History,
  BarChart3, DollarSign, Activity, PieChart, Filter, Database,
  Sparkles, ClipboardList, Brain, Bot, Send, AlertTriangle,
  Calendar, Zap, Shield, Target, Clock, MessageSquare,
} from 'lucide-react';
import ReactMarkdown from 'react-markdown';
import remarkGfm from 'remark-gfm';
import {
  fetchEnhancedPortfolioSummary, addHolding, deleteHolding, updateHolding,
  bulkDeleteHoldings, addTransaction, fetchTransactions, deleteTransaction,
  fetchDividendView, fetchFundamentalView, fetchTechnicalView,
  refreshTicker, refreshAllEnrichment, fetchSectors,
  bulkParseTransactions, bulkSaveTransactions, analyzeHolding, createAgent,
  fetchPortfolioEvents, portfolioCopilot, fetchPortfolioBrief,
} from '../api';
import type { SectorMeta } from '../api';
import type {
  EnhancedPortfolioSummary, EnhancedHolding, HoldingInput,
  PortfolioTransaction, TransactionInput, AssetType, TransactionType,
  DividendData, FundamentalData, PortfolioTechnicalData, ParsedTransaction,
  PortfolioEvent, PortfolioBrief as PortfolioBriefData,
} from '../types';
import { BulkImport } from '../components/BulkImport';
import { OrderLogImport } from '../components/OrderLogImport';
import { PortfolioBrief } from '../components/PortfolioBrief';
import { PortfolioOptimizer } from '../components/PortfolioOptimizer';

// ─── Types ────────────────────────────────────────────────────────────────────

type TabId = 'overview' | 'dividends' | 'fundamentals' | 'technical';
// How the Overview holdings are presented. Default 'attention' keeps a large
// (300+) book smooth by leading with the handful that actually need a look.
type ViewMode = 'attention' | 'groups' | 'table';
type SortKey = string;
type SortDir = 'asc' | 'desc';

interface EditState {
  ticker: string; asset_type: string; shares: string;
  cost_basis: string; purchase_date: string;
}

// ─── Constants ────────────────────────────────────────────────────────────────

type AssetClass = 'ALL' | 'STOCKS' | 'FUNDS' | 'OPTIONS';

/** Sector label to show when yfinance returns no sector for non-equity types. */
const ASSET_SECTOR_MAP: Record<string, string> = {
  ETF: 'ETF', MUTUAL_FUND: 'Mutual Fund',
  CRYPTO: 'Crypto', BOND: 'Bond', CASH: 'Cash', OPTION: 'Option',
};

const ASSET_TYPE_LABELS: Record<string, string> = {
  STOCK: 'Stock', ETF: 'ETF', BOND: 'Bond',
  MUTUAL_FUND: 'MF', CASH: 'Cash', OPTION: 'Option', CRYPTO: 'Crypto', OTHER: 'Other',
};
const ASSET_TYPE_COLORS: Record<string, string> = {
  STOCK: 'badge-primary', ETF: 'badge-secondary', BOND: 'badge-accent',
  MUTUAL_FUND: 'badge-info', CASH: 'badge-success', OPTION: 'badge-warning',
  CRYPTO: 'badge-error', OTHER: 'badge-ghost',
};
const TXN_TYPE_LABELS: Record<string, string> = {
  BUY: 'Buy', SELL: 'Sell', OPTION_BUY: 'Opt Buy', OPTION_SELL: 'Opt Sell',
  TRANSFER_IN: 'In', TRANSFER_OUT: 'Out',
};
const TXN_TYPE_COLORS: Record<string, string> = {
  BUY: 'text-success', SELL: 'text-error', OPTION_BUY: 'text-info',
  OPTION_SELL: 'text-warning', TRANSFER_IN: 'text-success', TRANSFER_OUT: 'text-error',
};
const REC_COLORS: Record<string, string> = {
  'strong buy': 'text-success', 'buy': 'text-success', 'hold': 'text-warning',
  'underperform': 'text-error', 'sell': 'text-error',
};

// ─── Formatters ───────────────────────────────────────────────────────────────

const fmt$ = (v: number | null | undefined, d = 2) => {
  if (v == null) return '—';
  const abs = Math.abs(v).toLocaleString(undefined, { minimumFractionDigits: d, maximumFractionDigits: d });
  return v < 0 ? `-$${abs}` : `$${abs}`;
};
const fmtB = (v: number | null | undefined) => {
  if (v == null) return '—';
  if (Math.abs(v) >= 1000) return `$${(v / 1000).toFixed(1)}T`;
  if (Math.abs(v) >= 1) return `$${v.toFixed(2)}B`;
  return `$${(v * 1000).toFixed(0)}M`;
};
const fmtPct = (v: number | null | undefined, d = 1) => {
  if (v == null) return '—';
  return `${v >= 0 ? '+' : ''}${v.toFixed(d)}%`;
};
const fmtNum = (v: number | null | undefined, d = 2) => {
  if (v == null) return '—';
  return v.toLocaleString(undefined, { minimumFractionDigits: d, maximumFractionDigits: d });
};
const fmtShares = (v: number) =>
  v === Math.floor(v) ? v.toLocaleString() : v.toLocaleString(undefined, { maximumFractionDigits: 4 });
const gainCls = (v: number | null | undefined) =>
  v == null ? '' : v > 0 ? 'text-success' : v < 0 ? 'text-error' : '';

// ─── Sub-components ──────────────────────────────────────────────────────────

function StatCard({ label, value, sub, trend, loading }: {
  label: string; value: string; sub?: string; trend?: number | null; loading?: boolean;
}) {
  return (
    <div className="glass-card p-3.5">
      <div className="text-xs font-medium text-base-content/40 uppercase tracking-wider mb-1">{label}</div>
      {loading
        ? <div className="h-6 w-20 bg-base-200/60 rounded animate-pulse mt-1" />
        : <>
            <div className={`text-lg font-black tracking-tight leading-tight ${trend != null ? gainCls(trend) : ''}`}>
              {value}
            </div>
            {sub && <div className={`text-xs font-semibold mt-0.5 ${trend != null ? gainCls(trend) : 'text-base-content/50'}`}>{sub}</div>}
          </>
      }
    </div>
  );
}

function SortTh({ label, k, cur, dir, onSort, cls }: {
  label: string; k: SortKey; cur: SortKey; dir: SortDir; onSort: (k: SortKey) => void; cls?: string;
}) {
  const active = cur === k;
  return (
    <th className={`cursor-pointer select-none whitespace-nowrap hover:bg-base-200/20 ${cls || ''}`} onClick={() => onSort(k)}>
      <span className="flex items-center gap-1">
        {label}
        {active ? (dir === 'asc' ? <ChevronUp className="w-3 h-3 opacity-60" /> : <ChevronDown className="w-3 h-3 opacity-60" />) : <span className="opacity-20 text-xs">↕</span>}
      </span>
    </th>
  );
}

function EnrichBanner({ loading, onRefresh, lastLabel }: { loading: boolean; onRefresh: () => void; lastLabel: string }) {
  return (
    <div className="flex items-center gap-2 text-xs text-base-content/40 mb-2">
      <Database className="w-3.5 h-3.5" />
      <span>Cached data ({lastLabel})</span>
      <button className="btn btn-ghost btn-xs gap-1 rounded-lg" onClick={onRefresh} disabled={loading}>
        {loading ? <Loader2 className="w-3 h-3 animate-spin" /> : <RefreshCw className="w-3 h-3" />}
        Refresh all
      </button>
    </div>
  );
}

// ─── Transaction Panel ─────────────────────────────────────────────────────

type AddMode = 'none' | 'manual' | 'ai';

function TransactionPanel({ ticker, onClose, onRefresh }: {
  ticker: string; onClose: () => void; onRefresh: () => void;
}) {
  const [transactions, setTransactions] = useState<PortfolioTransaction[]>([]);
  const [loading, setLoading] = useState(true);
  const [deleting, setDeleting] = useState<number | null>(null);
  const [addMode, setAddMode] = useState<AddMode>('none');

  // Manual add state
  const [submitting, setSubmitting] = useState(false);
  const [txnType, setTxnType] = useState<TransactionType>('BUY');
  const [txnShares, setTxnShares] = useState('');
  const [txnPrice, setTxnPrice] = useState('');
  const [txnFees, setTxnFees] = useState('0');
  const [txnDate, setTxnDate] = useState(new Date().toISOString().slice(0, 10));
  const [txnNotes, setTxnNotes] = useState('');

  // AI import state
  const [aiText, setAiText] = useState('');
  const [aiParsing, setAiParsing] = useState(false);
  const [aiSaving, setAiSaving] = useState(false);
  const [aiParsed, setAiParsed] = useState<ParsedTransaction[] | null>(null);

  const [err, setErr] = useState<string | null>(null);

  const load = useCallback(async () => {
    setLoading(true);
    try { setTransactions(await fetchTransactions(ticker)); } finally { setLoading(false); }
  }, [ticker]);
  useEffect(() => { load(); }, [load]);

  const toggleMode = (mode: AddMode) => {
    setAddMode(prev => prev === mode ? 'none' : mode);
    setErr(null);
    setAiParsed(null);
  };

  const handleAdd = async (e: React.FormEvent) => {
    e.preventDefault();
    const shares = parseFloat(txnShares); const price = parseFloat(txnPrice);
    if (isNaN(shares) || shares <= 0 || isNaN(price) || price < 0 || !txnDate) {
      setErr('Fill in all required fields.'); return;
    }
    setSubmitting(true); setErr(null);
    try {
      await addTransaction({ ticker, transaction_type: txnType, shares, price_per_share: price, fees: parseFloat(txnFees) || 0, date: txnDate, notes: txnNotes || undefined });
      setTxnShares(''); setTxnPrice(''); setTxnFees('0'); setTxnNotes('');
      setAddMode('none');
      await load(); onRefresh();
    } catch (e2) { setErr(e2 instanceof Error ? e2.message : 'Error'); } finally { setSubmitting(false); }
  };

  const handleDel = async (id: number) => {
    if (!confirm('Delete this transaction?')) return;
    setDeleting(id);
    try { await deleteTransaction(id); await load(); onRefresh(); } finally { setDeleting(null); }
  };

  // AI parse
  const handleAiParse = async () => {
    if (aiText.trim().length < 5) { setErr('Please enter transaction details to parse.'); return; }
    setAiParsing(true); setErr(null); setAiParsed(null);
    try {
      const result = await bulkParseTransactions(aiText, ticker);
      if (result.transactions.length === 0) {
        setErr('No transactions found. Try adding more details (ticker, shares, price, date).');
        return;
      }
      setAiParsed(result.transactions);
    } catch (e2) { setErr(e2 instanceof Error ? e2.message : 'Parse failed'); }
    finally { setAiParsing(false); }
  };

  const updateAiRow = (i: number, field: keyof ParsedTransaction, value: string) => {
    if (!aiParsed) return;
    const updated = [...aiParsed];
    if (field === 'shares' || field === 'price_per_share' || field === 'fees') {
      (updated[i] as any)[field] = parseFloat(value) || 0;
    } else {
      (updated[i] as any)[field] = value;
    }
    setAiParsed(updated);
  };
  const removeAiRow = (i: number) => { if (aiParsed) setAiParsed(aiParsed.filter((_, idx) => idx !== i)); };

  const handleAiSave = async () => {
    if (!aiParsed || aiParsed.length === 0) return;
    for (const t of aiParsed) {
      if (!t.ticker || t.shares <= 0 || t.price_per_share < 0 || !t.date) {
        setErr('Please review: all rows need a ticker, shares, price and date.'); return;
      }
    }
    setAiSaving(true); setErr(null);
    try {
      const result = await bulkSaveTransactions(aiParsed);
      setAiParsed(null); setAiText(''); setAddMode('none');
      await load(); onRefresh();
      // brief success echo via parent refresh
      void result;
    } catch (e2) { setErr(e2 instanceof Error ? e2.message : 'Save failed'); }
    finally { setAiSaving(false); }
  };

  return (
    <div className="glass-card mt-0 border-t-2 border-primary/20 animate-fade-in-down">
      {/* Header */}
      <div className="flex items-center justify-between px-4 pt-3 pb-2 border-b border-white/[0.04]">
        <span className="flex items-center gap-2 font-bold text-sm">
          <History className="w-3.5 h-3.5 text-primary" />{ticker} Transactions
          {transactions.length > 0 && <span className="badge badge-xs badge-ghost">{transactions.length}</span>}
        </span>
        <div className="flex gap-1">
          <button
            className={`btn btn-xs rounded-lg gap-1 ${addMode === 'ai' ? 'btn-secondary' : 'btn-ghost border border-white/[0.06]'}`}
            onClick={() => toggleMode('ai')}
            title="Import multiple transactions with AI"
          >
            {addMode === 'ai' ? <X className="w-3 h-3" /> : <Sparkles className="w-3 h-3" />}
            <span className="hidden sm:inline">{addMode === 'ai' ? 'Cancel' : 'AI Import'}</span>
          </button>
          <button
            className={`btn btn-xs rounded-lg gap-1 ${addMode === 'manual' ? 'btn-primary' : 'btn-primary'}`}
            onClick={() => toggleMode('manual')}
          >
            {addMode === 'manual' ? <X className="w-3 h-3" /> : <Plus className="w-3 h-3" />}
            {addMode === 'manual' ? 'Cancel' : 'Add'}
          </button>
          <button className="btn btn-ghost btn-xs btn-square rounded-lg" onClick={onClose}><X className="w-3.5 h-3.5" /></button>
        </div>
      </div>

      {/* Manual add form */}
      {addMode === 'manual' && (
        <form onSubmit={handleAdd} className="px-4 py-3 border-b border-white/[0.04] bg-primary/5">
          {err && <p className="text-xs text-error mb-2">{err}</p>}
          <div className="grid grid-cols-3 sm:grid-cols-6 gap-2 mb-2">
            <div>
              <label className="text-xs text-base-content/40 mb-0.5 block">Type</label>
              <select className="select select-bordered select-xs w-full rounded-lg bg-base-200/50" value={txnType} onChange={e => setTxnType(e.target.value as TransactionType)}>
                {Object.entries(TXN_TYPE_LABELS).map(([k, v]) => <option key={k} value={k}>{v}</option>)}
              </select>
            </div>
            <div>
              <label className="text-xs text-base-content/40 mb-0.5 block">Shares</label>
              <input type="number" step="0.0001" min="0.0001" className="input input-bordered input-xs w-full rounded-lg bg-base-200/50" placeholder="100" value={txnShares} onChange={e => setTxnShares(e.target.value)} required />
            </div>
            <div>
              <label className="text-xs text-base-content/40 mb-0.5 block">Price</label>
              <input type="number" step="0.0001" min="0" className="input input-bordered input-xs w-full rounded-lg bg-base-200/50" placeholder="150.00" value={txnPrice} onChange={e => setTxnPrice(e.target.value)} required />
            </div>
            <div>
              <label className="text-xs text-base-content/40 mb-0.5 block">Fees</label>
              <input type="number" step="0.01" min="0" className="input input-bordered input-xs w-full rounded-lg bg-base-200/50" placeholder="0" value={txnFees} onChange={e => setTxnFees(e.target.value)} />
            </div>
            <div>
              <label className="text-xs text-base-content/40 mb-0.5 block">Date</label>
              <input type="date" className="input input-bordered input-xs w-full rounded-lg bg-base-200/50" value={txnDate} onChange={e => setTxnDate(e.target.value)} required />
            </div>
            <div className="flex flex-col justify-end">
              <button type="submit" className="btn btn-primary btn-xs rounded-lg w-full" disabled={submitting}>
                {submitting ? <Loader2 className="w-3 h-3 animate-spin" /> : 'Save'}
              </button>
            </div>
          </div>
          <input type="text" className="input input-bordered input-xs w-full rounded-lg bg-base-200/50" placeholder="Notes (optional)" value={txnNotes} onChange={e => setTxnNotes(e.target.value)} />
        </form>
      )}

      {/* AI import panel */}
      {addMode === 'ai' && (
        <div className="px-4 py-3 border-b border-white/[0.04] bg-secondary/5 space-y-3">
          <p className="text-xs text-base-content/50">
            Paste brokerage statements, CSV rows, or plain text — AI will extract transactions for review.
          </p>
          {err && <p className="text-xs text-error">{err}</p>}

          {!aiParsed ? (
            <div className="flex gap-2 items-start">
              <textarea
                className="textarea textarea-bordered textarea-xs flex-1 rounded-lg bg-base-200/50 resize-none text-xs font-mono"
                rows={4}
                placeholder={`e.g.\nBought 50 AAPL @ $182.50 on 2024-03-10\nSold 20 TSLA @ $200 on 2024-04-01, fees $1.99\nTransferred in 100 VTI @ $220 on Jan 15 2024`}
                value={aiText}
                onChange={e => setAiText(e.target.value)}
              />
              <button
                className="btn btn-secondary btn-xs rounded-lg gap-1 mt-0.5 shrink-0"
                onClick={handleAiParse}
                disabled={aiParsing || aiText.trim().length < 5}
              >
                {aiParsing ? <Loader2 className="w-3 h-3 animate-spin" /> : <Sparkles className="w-3 h-3" />}
                Parse
              </button>
            </div>
          ) : (
            <div className="space-y-2">
              <div className="flex items-center justify-between">
                <span className="text-xs font-semibold text-secondary">{aiParsed.length} transaction{aiParsed.length !== 1 ? 's' : ''} found — review &amp; edit before saving</span>
                <button className="btn btn-ghost btn-xs rounded gap-1" onClick={() => { setAiParsed(null); setErr(null); }}>
                  <RefreshCw className="w-3 h-3" /> Re-parse
                </button>
              </div>
              <div className="overflow-x-auto rounded-lg border border-base-300/30">
                <table className="table table-xs w-full">
                  <thead><tr className="text-base-content/40 bg-base-200/50">
                    <th>Ticker</th><th>Type</th><th>Shares</th><th>Price</th><th>Fees</th><th>Date</th><th>Notes</th><th></th>
                  </tr></thead>
                  <tbody>
                    {aiParsed.map((t, i) => (
                      <tr key={i} className="hover">
                        <td><input className="input input-xs w-20 rounded bg-base-200/50 uppercase font-bold" value={t.ticker} onChange={e => updateAiRow(i, 'ticker', e.target.value.toUpperCase())} /></td>
                        <td>
                          <select className="select select-xs rounded bg-base-200/50" value={t.transaction_type} onChange={e => updateAiRow(i, 'transaction_type', e.target.value)}>
                            {Object.entries(TXN_TYPE_LABELS).map(([k, v]) => <option key={k} value={k}>{v}</option>)}
                          </select>
                        </td>
                        <td><input type="number" step="0.0001" className="input input-xs w-20 rounded bg-base-200/50" value={t.shares} onChange={e => updateAiRow(i, 'shares', e.target.value)} /></td>
                        <td><input type="number" step="0.0001" className="input input-xs w-24 rounded bg-base-200/50" value={t.price_per_share} onChange={e => updateAiRow(i, 'price_per_share', e.target.value)} /></td>
                        <td><input type="number" step="0.01" className="input input-xs w-16 rounded bg-base-200/50" value={t.fees} onChange={e => updateAiRow(i, 'fees', e.target.value)} /></td>
                        <td><input type="date" className="input input-xs w-32 rounded bg-base-200/50" value={t.date} onChange={e => updateAiRow(i, 'date', e.target.value)} /></td>
                        <td><input className="input input-xs w-28 rounded bg-base-200/50" value={t.notes} onChange={e => updateAiRow(i, 'notes', e.target.value)} /></td>
                        <td><button className="btn btn-ghost btn-xs text-error rounded" onClick={() => removeAiRow(i)}><Trash2 className="w-3 h-3" /></button></td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
              <div className="flex justify-end gap-2">
                <button className="btn btn-ghost btn-xs rounded-lg" onClick={() => { setAiParsed(null); setAiText(''); }}>Clear</button>
                <button className="btn btn-secondary btn-xs rounded-lg gap-1" onClick={handleAiSave} disabled={aiSaving || aiParsed.length === 0}>
                  {aiSaving ? <Loader2 className="w-3 h-3 animate-spin" /> : <Check className="w-3 h-3" />}
                  Save {aiParsed.length} Transaction{aiParsed.length !== 1 ? 's' : ''}
                </button>
              </div>
            </div>
          )}
        </div>
      )}

      {/* Transaction history */}
      <div className="overflow-x-auto max-h-48 overflow-y-auto">
        {loading ? (
          <div className="flex justify-center py-4"><Loader2 className="w-4 h-4 animate-spin text-primary/40" /></div>
        ) : transactions.length === 0 ? (
          <p className="text-center py-4 text-xs text-base-content/40">No transactions yet.</p>
        ) : (
          <table className="table table-xs w-full">
            <thead><tr className="text-base-content/40">
              <th>Date</th><th>Type</th><th className="text-right">Shares</th>
              <th className="text-right">Price</th><th className="text-right">Fees</th>
              <th className="text-right">Total</th><th>Notes</th><th></th>
            </tr></thead>
            <tbody>
              {transactions.map(t => (
                <tr key={t.id} className="hover">
                  <td className="text-base-content/50">{t.date}</td>
                  <td><span className={`font-semibold text-xs ${TXN_TYPE_COLORS[t.transaction_type] || ''}`}>{TXN_TYPE_LABELS[t.transaction_type] || t.transaction_type}</span></td>
                  <td className="text-right font-mono">{fmtShares(t.shares)}</td>
                  <td className="text-right font-mono">{fmt$(t.price_per_share)}</td>
                  <td className="text-right font-mono text-base-content/40">{t.fees ? fmt$(t.fees) : '—'}</td>
                  <td className="text-right font-mono font-semibold">{fmt$(t.shares * t.price_per_share)}</td>
                  <td className="text-base-content/40 max-w-[100px] truncate">{t.notes || '—'}</td>
                  <td>
                    <button className="btn btn-ghost btn-xs text-error rounded" onClick={() => handleDel(t.id)} disabled={deleting === t.id}>
                      {deleting === t.id ? <Loader2 className="w-3 h-3 animate-spin" /> : <Trash2 className="w-3 h-3" />}
                    </button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </div>
    </div>
  );
}

// ─── Reusable sort hook ────────────────────────────────────────────────────

function useSortedData<T extends object>(data: T[], defaultKey: SortKey, defaultDir: SortDir = 'desc') {
  const [sortKey, setSortKey] = useState<SortKey>(defaultKey);
  const [sortDir, setSortDir] = useState<SortDir>(defaultDir);
  const handleSort = (k: SortKey) => {
    if (sortKey === k) setSortDir(d => d === 'asc' ? 'desc' : 'asc');
    else { setSortKey(k); setSortDir('desc'); }
  };
  const sorted = useMemo(() => {
    if (!sortKey) return data;
    return [...data].sort((a, b) => {
      const av = (a as Record<string, unknown>)[sortKey]; const bv = (b as Record<string, unknown>)[sortKey];
      if (av == null) return 1; if (bv == null) return -1;
      const cmp = av < bv ? -1 : av > bv ? 1 : 0;
      return sortDir === 'asc' ? cmp : -cmp;
    });
  }, [data, sortKey, sortDir]);
  return { sorted, sortKey, sortDir, handleSort };
}

// ─── Main Page ────────────────────────────────────────────────────────────────

export default function PortfolioPage() {
  const navigate = useNavigate();
  const [summary, setSummary] = useState<EnhancedPortfolioSummary | null>(null);
  const [loading, setLoading] = useState(true);
  const [refreshing, setRefreshing] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [success, setSuccess] = useState<string | null>(null);
  const [activeTab, setActiveTab] = useState<TabId>('overview');
  const [viewMode, setViewMode] = useState<ViewMode>(
    () => ((typeof localStorage !== 'undefined' && (localStorage.getItem('pf_view_mode') as ViewMode)) || 'attention')
  );
  React.useEffect(() => { try { localStorage.setItem('pf_view_mode', viewMode); } catch { /* ignore */ } }, [viewMode]);
  const tabsRef = React.useRef<HTMLDivElement>(null);
  const [showForm, setShowForm] = useState(false);
  const [showBulkImport, setShowBulkImport] = useState(false);
  const [showOrderLog, setShowOrderLog] = useState(false);
  const [showOptimizer, setShowOptimizer] = useState(false);
  const [submitting, setSubmitting] = useState(false);
  const [deletingId, setDeletingId] = useState<number | null>(null);
  const [editingId, setEditingId] = useState<number | null>(null);
  const [editState, setEditState] = useState<EditState>({ ticker: '', asset_type: 'STOCK', shares: '', cost_basis: '', purchase_date: '' });
  const [savingEdit, setSavingEdit] = useState(false);
  const [selectedIds, setSelectedIds] = useState<Set<number>>(new Set());
  const [bulkDeleting, setBulkDeleting] = useState(false);
  const [formTicker, setFormTicker] = useState('');
  const [formShares, setFormShares] = useState('');
  const [formCostBasis, setFormCostBasis] = useState('');
  const [formDate, setFormDate] = useState('');
  const [formAssetType, setFormAssetType] = useState<AssetType>('STOCK');
  const [sortKey, setSortKey] = useState<SortKey>('market_value');
  const [sortDir, setSortDir] = useState<SortDir>('desc');
  const [assetClassFilter, setAssetClassFilter] = useState<AssetClass>('ALL');
  const [sectorFilter, setSectorFilter] = useState('ALL');
  const [groupBySector, setGroupBySector] = useState(false);
  const [collapsedSectors, setCollapsedSectors] = useState<Set<string>>(new Set());
  const [tickerSearch, setTickerSearch] = useState('');
  const [txnTicker, setTxnTicker] = useState<string | null>(null);

  // Enrichment state
  const [dividendData, setDividendData] = useState<DividendData[]>([]);
  const [fundamentalData, setFundamentalData] = useState<FundamentalData[]>([]);
  const [technicalData, setTechnicalData] = useState<PortfolioTechnicalData[]>([]);
  const [enrichLoading, setEnrichLoading] = useState(false);
  const [enrichLoaded, setEnrichLoaded] = useState<TabId | null>(null);

  // Sector metadata (auto-fetched in background on load)
  const [sectorMap, setSectorMap] = useState<Record<string, SectorMeta>>({});
  const [sectorsLoading, setSectorsLoading] = useState(false);

  // Upcoming events (earnings + ex-div calendar)
  const [portfolioEvents, setPortfolioEvents] = useState<PortfolioEvent[]>([]);
  const [eventsLoading, setEventsLoading] = useState(false);

  // Daily briefing (the "Today" hero — new front door)
  const [brief, setBrief] = useState<PortfolioBriefData | null>(null);
  const [briefLoading, setBriefLoading] = useState(false);
  const [briefRefreshing, setBriefRefreshing] = useState(false);
  const [briefError, setBriefError] = useState(false);

  const loadSummary = useCallback(async (silent = false, forceRefresh = false) => {
    try {
      if (!silent) setLoading(true); else setRefreshing(true);
      const data = await fetchEnhancedPortfolioSummary(forceRefresh);
      setSummary(data); setError(null);
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to load portfolio');
    } finally { setLoading(false); setRefreshing(false); }
  }, []);

  const loadSectors = useCallback(async () => {
    setSectorsLoading(true);
    try {
      const rows = await fetchSectors();
      const map: Record<string, SectorMeta> = {};
      rows.forEach(r => { map[r.ticker] = r; });
      setSectorMap(map);
    } catch { /* silent */ } finally { setSectorsLoading(false); }
  }, []);

  const loadEvents = useCallback(async () => {
    setEventsLoading(true);
    try {
      const evs = await fetchPortfolioEvents();
      setPortfolioEvents(evs);
    } catch { /* silent */ } finally { setEventsLoading(false); }
  }, []);

  const loadBrief = useCallback(async (forceRefresh = false) => {
    if (forceRefresh) setBriefRefreshing(true); else setBriefLoading(true);
    try {
      const b = await fetchPortfolioBrief(forceRefresh);
      setBrief(b);
      setBriefError(false);
    } catch {
      setBriefError(true);
    } finally { setBriefLoading(false); setBriefRefreshing(false); }
  }, []);

  useEffect(() => { loadSummary(); }, [loadSummary]);

  // Auto-fetch sectors + events + brief in background after holdings load
  useEffect(() => {
    if (summary && summary.holdings.length > 0) {
      loadSectors();
      loadEvents();
      loadBrief();
    }
  }, [summary?.id, loadSectors, loadEvents, loadBrief]); // only re-run when portfolio id changes

  const loadEnrichedTab = useCallback(async (tab: TabId, force = false) => {
    if (tab === 'overview') return;
    if (!force && enrichLoaded === tab) return;
    setEnrichLoading(true);
    try {
      if (tab === 'dividends') setDividendData(await fetchDividendView(force));
      else if (tab === 'fundamentals') setFundamentalData(await fetchFundamentalView(force));
      else if (tab === 'technical') setTechnicalData(await fetchTechnicalView(force));
      setEnrichLoaded(tab);
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Failed to load data');
    } finally { setEnrichLoading(false); }
  }, [enrichLoaded]);

  useEffect(() => {
    if (activeTab !== 'overview' && summary && summary.holdings.length > 0) {
      loadEnrichedTab(activeTab);
    }
  }, [activeTab, summary, loadEnrichedTab]);

  const handleRefreshAll = async () => {
    setEnrichLoading(true);
    try {
      await refreshAllEnrichment();
      setEnrichLoaded(null);
      setSectorMap({});
      // refresh-all busts the price + brief caches too, so pull fresh headline P&L
      await Promise.all([loadSummary(true, true), loadEnrichedTab(activeTab, true), loadSectors(), loadEvents(), loadBrief(true)]);
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Refresh failed');
    } finally { setEnrichLoading(false); }
  };

  // Jump from an insight card to the relevant tab + pre-filter on its ticker.
  const focusInsight = (ticker?: string, tab?: TabId) => {
    if (tab) setActiveTab(tab);
    if (ticker) setTickerSearch(ticker);
    tabsRef.current?.scrollIntoView({ behavior: 'smooth', block: 'start' });
  };

  const toggleCollapsedSector = (s: string) => {
    setCollapsedSectors(prev => {
      const next = new Set(prev);
      next.has(s) ? next.delete(s) : next.add(s);
      return next;
    });
  };

  const handleSort = (k: SortKey) => {
    if (sortKey === k) setSortDir(d => d === 'asc' ? 'desc' : 'asc');
    else { setSortKey(k); setSortDir('desc'); }
  };

  // Enrich holdings with sector + yfinance-corrected asset_type from sectorMap.
  // The sectorMap /sectors endpoint auto-corrects asset_type via yfinance quote_type
  // (e.g. a ticker stored as STOCK but quote_type=ETF becomes ETF).
  const holdingsWithSector = useMemo(() =>
    (summary?.holdings ?? []).map(h => {
      const sm = sectorMap[h.ticker];
      // Prefer yfinance-derived asset_type over whatever is stored in DB
      const asset_type = (sm?.asset_type || h.asset_type) as AssetType;
      const sector = h.sector || sm?.sector || ASSET_SECTOR_MAP[asset_type] || null;
      return {
        ...h,
        asset_type,
        sector,
        industry: h.industry || sm?.industry || null,
        company_name: h.company_name || sm?.company_name || null,
      };
    }),
    [summary, sectorMap]
  );

  // Sectors present for the CURRENTLY selected asset class (so the sector
  // pills update when the user switches All / Stocks / Funds / Options)
  const sectorsPresent = useMemo(() => {
    let h = holdingsWithSector;
    if (assetClassFilter === 'STOCKS')  h = h.filter(x => x.asset_type === 'STOCK');
    else if (assetClassFilter === 'FUNDS')   h = h.filter(x => ['ETF', 'MUTUAL_FUND'].includes(x.asset_type));
    else if (assetClassFilter === 'OPTIONS') h = h.filter(x => x.asset_type === 'OPTION');
    return Array.from(new Set(h.map(x => x.sector).filter(Boolean) as string[])).sort();
  }, [holdingsWithSector, assetClassFilter]);

  const hasSectorData = Object.keys(sectorMap).length > 0 || (summary?.holdings.some(h => h.sector) ?? false);

  // When activating group-by-sector, pre-collapse all sectors so the user
  // sees a compact summary view immediately (minimises server load too).
  const handleGroupBySector = useCallback((v: boolean) => {
    setGroupBySector(v);
    if (v) setCollapsedSectors(new Set(sectorsPresent));
  }, [sectorsPresent]);

  const filteredHoldings = useMemo(() => {
    if (!summary) return [];
    let h = holdingsWithSector;
    if (assetClassFilter === 'STOCKS')       h = h.filter(x => x.asset_type === 'STOCK');
    else if (assetClassFilter === 'FUNDS')   h = h.filter(x => ['ETF', 'MUTUAL_FUND'].includes(x.asset_type));
    else if (assetClassFilter === 'OPTIONS') h = h.filter(x => x.asset_type === 'OPTION');
    if (sectorFilter !== 'ALL') h = h.filter(x => (x.sector || 'Unknown') === sectorFilter);
    if (tickerSearch) h = h.filter(x => x.ticker.toLowerCase().includes(tickerSearch.toLowerCase()) || (x.company_name || '').toLowerCase().includes(tickerSearch.toLowerCase()));
    return [...h].sort((a, b) => {
      if (!sortKey) return 0;
      const av = (a as unknown as Record<string, unknown>)[sortKey as string];
      const bv = (b as unknown as Record<string, unknown>)[sortKey as string];
      if (av == null) return 1; if (bv == null) return -1;
      const cmp = av < bv ? -1 : av > bv ? 1 : 0;
      return sortDir === 'asc' ? cmp : -cmp;
    });
  }, [holdingsWithSector, sortKey, sortDir, assetClassFilter, sectorFilter, tickerSearch, summary]);

  // ── Holdings for the top summary cards (asset-class + sector only, NO ticker search)
  // Ticker search is a row-highlight/filter tool and must not distort portfolio totals.
  const summaryHoldings = useMemo(() => {
    let h = holdingsWithSector;
    if (assetClassFilter === 'STOCKS')       h = h.filter(x => x.asset_type === 'STOCK');
    else if (assetClassFilter === 'FUNDS')   h = h.filter(x => ['ETF', 'MUTUAL_FUND'].includes(x.asset_type));
    else if (assetClassFilter === 'OPTIONS') h = h.filter(x => x.asset_type === 'OPTION');
    if (sectorFilter !== 'ALL') h = h.filter(x => (x.sector || 'Unknown') === sectorFilter);
    return h;
  }, [holdingsWithSector, assetClassFilter, sectorFilter]);

  // ── Filtered portfolio totals ─────────────────────────────────────────────
  // Uses summaryHoldings (no ticker search) so "What needs your attention"
  // focus clicks don't distort the top-level market value / P&L cards.
  const filteredStats = useMemo(() => {
    const holdings = summaryHoldings;
    if (!holdings.length) return null;

    let marketValue = 0, hasMV = 0;
    let dayPnl = 0, hasDayPnl = 0;
    let totalCost = 0;
    let unrealized = 0, hasUnreal = 0;
    let realized = 0;
    let weightedAnn = 0, totalWeight = 0;

    for (const h of holdings) {
      totalCost += h.total_cost;
      realized += h.realized_gain_loss ?? 0;
      if (h.market_value != null) { marketValue += h.market_value; hasMV++; }
      if (h.day_pnl != null) { dayPnl += h.day_pnl; hasDayPnl++; }
      if (h.unrealized_gain_loss != null) { unrealized += h.unrealized_gain_loss; hasUnreal++; }
      if (h.annualized_return_pct != null && h.market_value != null) {
        weightedAnn += h.annualized_return_pct * h.market_value;
        totalWeight += h.market_value;
      }
    }

    const mvOut = hasMV > 0 ? marketValue : null;
    const dayPnlOut = hasDayPnl > 0 ? dayPnl : null;
    const dayPnlBase = marketValue - dayPnl;
    const dayPnlPct = dayPnlOut != null && dayPnlBase > 0 ? (dayPnl / dayPnlBase) * 100 : null;
    const unrealOut = hasUnreal > 0 ? unrealized : null;
    const unrealPct = unrealOut != null && totalCost > 0 ? (unrealized / totalCost) * 100 : null;
    const annReturn = totalWeight > 0 ? weightedAnn / totalWeight : null;

    return {
      total_market_value: mvOut != null ? parseFloat(mvOut.toFixed(2)) : null,
      total_day_pnl: dayPnlOut != null ? parseFloat(dayPnlOut.toFixed(2)) : null,
      total_day_pnl_pct: dayPnlPct != null ? parseFloat(dayPnlPct.toFixed(2)) : null,
      total_cost: parseFloat(totalCost.toFixed(2)),
      total_unrealized_gain_loss: unrealOut != null ? parseFloat(unrealized.toFixed(2)) : null,
      total_unrealized_gain_loss_pct: unrealPct != null ? parseFloat(unrealPct.toFixed(2)) : null,
      total_realized_gain_loss: parseFloat(realized.toFixed(2)),
      portfolio_annualized_return: annReturn != null ? parseFloat(annReturn.toFixed(2)) : null,
    };
  }, [summaryHoldings]);

  const resetForm = () => { setFormTicker(''); setFormShares(''); setFormCostBasis(''); setFormDate(''); setFormAssetType('STOCK'); };

  const handleAdd = async (e: React.FormEvent) => {
    e.preventDefault();
    const data: HoldingInput = { ticker: formTicker.toUpperCase().trim(), asset_type: formAssetType, shares: parseFloat(formShares), cost_basis: parseFloat(formCostBasis), purchase_date: formDate };
    if (!data.ticker || isNaN(data.shares) || isNaN(data.cost_basis) || !data.purchase_date) { setError('Please fill in all fields.'); return; }
    try {
      setSubmitting(true); setError(null);
      await addHolding(data);
      setSuccess(`${data.ticker} added!`); resetForm(); setShowForm(false);
      await loadSummary(true); setTimeout(() => setSuccess(null), 3000);
    } catch (err) { setError(err instanceof Error ? err.message : 'Failed to add'); } finally { setSubmitting(false); }
  };

  const handleDelete = async (id: number, ticker: string) => {
    // No confirm() here — inline row confirmation handles single deletes
    try {
      setDeletingId(id); await deleteHolding(id);
      setSuccess(`${ticker} removed.`);
      setSelectedIds(p => { const n = new Set(p); n.delete(id); return n; });
      if (txnTicker === ticker) setTxnTicker(null);
      await loadSummary(true); setTimeout(() => setSuccess(null), 3000);
    } catch (err) { setError(err instanceof Error ? err.message : 'Failed'); } finally { setDeletingId(null); }
  };

  const startEdit = (h: EnhancedHolding) => {
    setEditingId(h.id);
    setEditState({ ticker: h.ticker, asset_type: h.asset_type || 'STOCK', shares: String(h.shares), cost_basis: String(h.cost_basis), purchase_date: h.purchase_date });
  };
  const cancelEdit = () => setEditingId(null);
  const saveEdit = async () => {
    if (editingId === null) return;
    const shares = parseFloat(editState.shares); const cost_basis = parseFloat(editState.cost_basis);
    const ticker = editState.ticker.toUpperCase().trim();
    if (!ticker || isNaN(shares) || shares <= 0 || isNaN(cost_basis) || cost_basis <= 0 || !editState.purchase_date) { setError('Fill in all fields.'); return; }
    try {
      setSavingEdit(true);
      await updateHolding(editingId, { ticker, asset_type: editState.asset_type as AssetType, shares, cost_basis, purchase_date: editState.purchase_date });
      setSuccess(`${ticker} updated!`); cancelEdit(); await loadSummary(true);
      setTimeout(() => setSuccess(null), 3000);
    } catch (err) { setError(err instanceof Error ? err.message : 'Failed'); } finally { setSavingEdit(false); }
  };

  const toggleSelect = (id: number) => setSelectedIds(p => { const n = new Set(p); n.has(id) ? n.delete(id) : n.add(id); return n; });
  const allSelected = summary ? selectedIds.size === summary.holdings.length && summary.holdings.length > 0 : false;
  const toggleSelectAll = () => {
    if (!summary) return;
    allSelected ? setSelectedIds(new Set()) : setSelectedIds(new Set(summary.holdings.map(h => h.id)));
  };
  const handleBulkDelete = async () => {
    if (!selectedIds.size || !confirm(`Remove ${selectedIds.size} holding(s)?`)) return;
    try {
      setBulkDeleting(true);
      const r = await bulkDeleteHoldings(Array.from(selectedIds));
      setSuccess(`${r.deleted} removed.`); setSelectedIds(new Set()); await loadSummary(true);
      setTimeout(() => setSuccess(null), 3000);
    } catch (err) { setError(err instanceof Error ? err.message : 'Failed'); } finally { setBulkDeleting(false); }
  };

  if (loading) return (
    <div className="empty-state">
      <div className="w-16 h-16 rounded-2xl bg-gradient-to-br from-primary/20 to-secondary/10 flex items-center justify-center animate-pulse-slow">
        <Loader2 className="w-8 h-8 animate-spin text-primary" />
      </div>
      <p className="text-base-content/50 mt-6 text-sm">Loading portfolio...</p>
    </div>
  );

  const s = summary;
  const hasHoldings = s && s.holdings.length > 0;

  const tabs: { id: TabId; label: string; icon: React.ReactNode }[] = [
    { id: 'overview', label: 'Overview', icon: <BarChart3 className="w-3.5 h-3.5" /> },
    { id: 'dividends', label: 'Dividends', icon: <DollarSign className="w-3.5 h-3.5" /> },
    { id: 'fundamentals', label: 'Fundamentals', icon: <Activity className="w-3.5 h-3.5" /> },
    { id: 'technical', label: 'Technical', icon: <PieChart className="w-3.5 h-3.5" /> },
  ];

  return (
    <div className="container-app py-6 sm:py-8 space-y-4 animate-fade-in">

      {/* Header */}
      <div className="page-header">
        <div>
          <h1 className="page-title">
            <div className="w-10 h-10 rounded-xl bg-gradient-to-br from-primary/20 to-secondary/10 flex items-center justify-center">
              <Briefcase className="w-5 h-5 text-primary" />
            </div>
            {s?.name || 'My Portfolio'}
          </h1>
          <p className="page-subtitle">Stocks · ETFs · Bonds · Mutual Funds · Options · Cash</p>
        </div>
        <div className="flex items-center gap-2">
          <button className="btn btn-sm btn-ghost btn-square rounded-xl border border-white/[0.06]" onClick={() => loadSummary(true)} disabled={refreshing} title="Refresh prices">
            <RefreshCw className={`w-4 h-4 ${refreshing ? 'animate-spin' : ''}`} />
          </button>
          <button
            className={`btn btn-sm rounded-xl gap-2 border ${showOptimizer ? 'btn-secondary border-secondary/30' : 'btn-ghost border-white/[0.06]'}`}
            onClick={() => { setShowOptimizer(v => !v); setShowOrderLog(false); setShowBulkImport(false); setShowForm(false); }}
            title="Optimize allocation (min-vol / HRP)"
          >
            {showOptimizer ? <X className="w-4 h-4" /> : <Target className="w-4 h-4" />}
            <span className="hidden sm:inline">{showOptimizer ? 'Cancel' : 'Optimize'}</span>
          </button>
          <button
            className={`btn btn-sm rounded-xl gap-2 border ${showOrderLog ? 'btn-secondary border-secondary/30' : 'btn-ghost border-white/[0.06]'}`}
            onClick={() => { setShowOrderLog(v => !v); setShowBulkImport(false); setShowForm(false); }}
            title="Import from brokerage order log"
          >
            {showOrderLog ? <X className="w-4 h-4" /> : <ClipboardList className="w-4 h-4" />}
            <span className="hidden sm:inline">{showOrderLog ? 'Cancel' : 'Order Log'}</span>
          </button>
          <button className="btn btn-sm btn-ghost rounded-xl gap-2 border border-white/[0.06]" onClick={() => { setShowBulkImport(!showBulkImport); if (!showBulkImport) { setShowForm(false); setShowOrderLog(false); } }}>
            {showBulkImport ? <X className="w-4 h-4" /> : <FileText className="w-4 h-4" />}
            <span className="hidden sm:inline">{showBulkImport ? 'Cancel' : 'Import'}</span>
          </button>
          <button className="btn btn-sm btn-primary rounded-xl gap-2 shadow-md shadow-primary/20" onClick={() => { setShowForm(!showForm); if (!showForm) { setShowBulkImport(false); setShowOrderLog(false); } }}>
            {showForm ? <X className="w-4 h-4" /> : <Plus className="w-4 h-4" />}
            <span className="hidden sm:inline">{showForm ? 'Cancel' : 'Add'}</span>
          </button>
        </div>
      </div>

      {/* Alerts */}
      {error && (
        <div className="glass-card p-3.5 border-error/20 flex items-center gap-3">
          <AlertCircle className="w-4 h-4 text-error flex-shrink-0" />
          <span className="text-sm text-error flex-1">{error}</span>
          <button className="btn btn-ghost btn-xs btn-square rounded-lg" onClick={() => setError(null)}><X className="w-3 h-3" /></button>
        </div>
      )}
      {success && (
        <div className="glass-card p-3.5 border-success/20 flex items-center gap-3">
          <CheckCircle className="w-4 h-4 text-success flex-shrink-0" />
          <span className="text-sm text-success">{success}</span>
        </div>
      )}

      {showOrderLog && (
        <div className="animate-fade-in-down">
          <OrderLogImport onComplete={() => { loadSummary(true); setShowOrderLog(false); }} />
        </div>
      )}
      {showBulkImport && <div className="animate-fade-in-down"><BulkImport onComplete={() => { loadSummary(true); setShowBulkImport(false); }} /></div>}
      {showOptimizer && <div className="animate-fade-in-down"><PortfolioOptimizer /></div>}

      {/* Add form */}
      {showForm && (
        <div className="glass-card p-5 animate-fade-in-down">
          <h2 className="text-sm font-bold mb-3 flex items-center gap-2"><Plus className="w-4 h-4 text-primary" />Add Position</h2>
          <form onSubmit={handleAdd} className="grid grid-cols-2 sm:grid-cols-3 lg:grid-cols-6 gap-3">
            {(['Ticker', 'Type', 'Shares', 'Avg Cost', 'Date'] as const).map((_, i) => null)}
            <div>
              <label className="text-xs font-semibold text-base-content/50 uppercase tracking-wider mb-1 block">Ticker</label>
              <input type="text" className="input input-bordered w-full rounded-xl bg-base-200/50 border-white/[0.06] text-sm uppercase" placeholder="AAPL" value={formTicker} onChange={e => setFormTicker(e.target.value.toUpperCase())} required />
            </div>
            <div>
              <label className="text-xs font-semibold text-base-content/50 uppercase tracking-wider mb-1 block">Type</label>
              <select className="select select-bordered w-full rounded-xl bg-base-200/50 border-white/[0.06] text-sm" value={formAssetType} onChange={e => setFormAssetType(e.target.value as AssetType)}>
                {Object.entries(ASSET_TYPE_LABELS).map(([k, v]) => <option key={k} value={k}>{v === 'MF' ? 'Mutual Fund' : v}</option>)}
              </select>
            </div>
            <div>
              <label className="text-xs font-semibold text-base-content/50 uppercase tracking-wider mb-1 block">Shares</label>
              <input type="number" className="input input-bordered w-full rounded-xl bg-base-200/50 border-white/[0.06] text-sm" placeholder="100" step="0.0001" min="0.0001" value={formShares} onChange={e => setFormShares(e.target.value)} required />
            </div>
            <div>
              <label className="text-xs font-semibold text-base-content/50 uppercase tracking-wider mb-1 block">Avg Cost</label>
              <input type="number" className="input input-bordered w-full rounded-xl bg-base-200/50 border-white/[0.06] text-sm" placeholder="150.00" step="0.0001" min="0.0001" value={formCostBasis} onChange={e => setFormCostBasis(e.target.value)} required />
            </div>
            <div>
              <label className="text-xs font-semibold text-base-content/50 uppercase tracking-wider mb-1 block">Date</label>
              <input type="date" className="input input-bordered w-full rounded-xl bg-base-200/50 border-white/[0.06] text-sm" value={formDate} onChange={e => setFormDate(e.target.value)} required />
            </div>
            <div className="flex flex-col justify-end">
              <button type="submit" className="btn btn-primary rounded-xl w-full" disabled={submitting}>
                {submitting ? <Loader2 className="w-4 h-4 animate-spin" /> : <Plus className="w-4 h-4" />} Add
              </button>
            </div>
          </form>
        </div>
      )}

      {/* Daily briefing hero — the rebuilt front door */}
      {hasHoldings && (
        <PortfolioBrief
          brief={brief}
          loading={briefLoading}
          refreshing={briefRefreshing}
          onRefresh={() => loadBrief(true)}
          onFocus={focusInsight}
        />
      )}

      {/* Summary cards — fallback shown only if the briefing failed to load */}
      {hasHoldings && briefError && filteredStats && (
        <div className="space-y-1">
          {(assetClassFilter !== 'ALL' || sectorFilter !== 'ALL') && (
            <div className="flex items-center gap-1.5 text-[11px] text-base-content/35 font-medium pl-0.5">
              <Filter className="w-3 h-3" />
              Showing {assetClassFilter === 'STOCKS' ? 'Stocks' : assetClassFilter === 'FUNDS' ? 'Funds' : assetClassFilter === 'OPTIONS' ? 'Options' : 'All'} only
              {sectorFilter !== 'ALL' && <> · {sectorFilter}</>}
              <span className="opacity-60">({summaryHoldings.length} of {s!.holdings.length} positions)</span>
            </div>
          )}
          <div className="grid grid-cols-3 lg:grid-cols-6 gap-2.5">
            <StatCard label="Market Value" value={fmt$(filteredStats.total_market_value)} loading={refreshing} />
            <StatCard label="Today P&L" value={fmt$(filteredStats.total_day_pnl)} sub={fmtPct(filteredStats.total_day_pnl_pct)} trend={filteredStats.total_day_pnl} loading={refreshing} />
            <StatCard label="Cost Basis" value={fmt$(filteredStats.total_cost)} />
            <StatCard label="Unrealized" value={fmt$(filteredStats.total_unrealized_gain_loss)} sub={fmtPct(filteredStats.total_unrealized_gain_loss_pct)} trend={filteredStats.total_unrealized_gain_loss} loading={refreshing} />
            <StatCard label="Realized" value={fmt$(filteredStats.total_realized_gain_loss)} trend={filteredStats.total_realized_gain_loss} />
            <StatCard label="Ann. Return" value={filteredStats.portfolio_annualized_return != null ? `${filteredStats.portfolio_annualized_return.toFixed(2)}%` : '—'} trend={filteredStats.portfolio_annualized_return} />
          </div>
        </div>
      )}

      {/* AI Copilot + Events + Analytics */}
      {hasHoldings && (
        <div className="space-y-2">
          <PortfolioCopilotPanel hasHoldings={hasHoldings} />
          <PortfolioInsights
            holdings={filteredHoldings}
            events={portfolioEvents}
            technicalData={technicalData}
            totalMarketValue={filteredStats?.total_market_value ?? null}
            onFocus={focusInsight}
          />
          <div className="glass-card px-4 py-2.5 space-y-2">
            <EventsStrip events={portfolioEvents} loading={eventsLoading} />
            <AnalyticsStrip
              holdings={filteredHoldings}
              dividendData={dividendData}
              technicalData={technicalData}
              totalMarketValue={filteredStats?.total_market_value ?? null}
            />
          </div>
        </div>
      )}

      {/* Tabs + (overview) view switcher */}
      {hasHoldings && (
        <div ref={tabsRef} className="flex flex-wrap items-center justify-between gap-2 scroll-mt-4">
          <div className="flex gap-1 p-1 bg-base-200/40 rounded-xl border border-white/[0.04] w-fit">
            {tabs.map(tab => (
              <button key={tab.id}
                className={`flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-sm font-medium transition-all ${activeTab === tab.id ? 'bg-primary text-primary-content shadow-sm' : 'text-base-content/60 hover:text-base-content hover:bg-base-200/60'}`}
                onClick={() => setActiveTab(tab.id)}
              >
                {tab.icon}<span className="hidden sm:inline">{tab.label}</span>
              </button>
            ))}
          </div>
          {activeTab === 'overview' && (
            <ViewSwitcher mode={viewMode} onChange={setViewMode} total={summaryHoldings.length} />
          )}
        </div>
      )}

      {/* Bulk actions */}
      {selectedIds.size > 0 && (
        <div className="glass-card p-3 flex items-center gap-3 border-error/10">
          <span className="text-xs font-semibold text-base-content/70">{selectedIds.size} selected</span>
          <button className="btn btn-error btn-sm rounded-xl gap-1.5" onClick={handleBulkDelete} disabled={bulkDeleting}>
            {bulkDeleting ? <Loader2 className="w-3.5 h-3.5 animate-spin" /> : <Trash2 className="w-3.5 h-3.5" />}Delete
          </button>
          <button className="btn btn-ghost btn-sm rounded-xl" onClick={() => setSelectedIds(new Set())}>Clear</button>
        </div>
      )}

      {/* Tab content */}
      {!hasHoldings ? (
        !loading && (
          <div className="empty-state">
            <div className="empty-state-icon animate-float"><Briefcase className="w-10 h-10 text-primary" /></div>
            <h2 className="text-2xl font-bold tracking-tight mb-2">Your Portfolio is Empty</h2>
            <p className="text-base-content/50 max-w-md text-sm leading-relaxed">Add stocks, ETFs, bonds, mutual funds, options, or cash to start tracking performance.</p>
            <button className="btn btn-primary rounded-xl gap-2 mt-6" onClick={() => setShowForm(true)}><Plus className="w-4 h-4" />Add First Position</button>
          </div>
        )
      ) : activeTab === 'overview' ? (
        viewMode === 'attention' ? (
          <AttentionHoldings
            holdings={filteredHoldings}
            events={portfolioEvents}
            technicalData={technicalData}
            totalMarketValue={filteredStats?.total_market_value ?? null}
            onOpen={(ticker, tab) => { if (tab === 'overview') setViewMode('table'); focusInsight(ticker, tab); }}
            onViewAll={() => setViewMode('table')}
          />
        ) : (
          <OverviewTab
            holdings={filteredHoldings} sortKey={sortKey} sortDir={sortDir} onSort={handleSort}
            assetClassFilter={assetClassFilter} onAssetClassFilter={setAssetClassFilter}
            sectorFilter={sectorFilter} onSectorFilter={setSectorFilter}
            sectorsPresent={sectorsPresent} hasSectorData={hasSectorData}
            sectorsLoading={sectorsLoading}
            groupBySector={viewMode === 'groups'} onGroupBySector={() => setViewMode(viewMode === 'groups' ? 'table' : 'groups')}
            collapsedSectors={collapsedSectors} onToggleCollapse={toggleCollapsedSector}
            totalPortfolioValue={filteredStats?.total_market_value ?? null}
            tickerSearch={tickerSearch} onTickerSearch={setTickerSearch}
            sectorMap={sectorMap}
            editingId={editingId} editState={editState} setEditState={setEditState}
            savingEdit={savingEdit} onSaveEdit={saveEdit} onCancelEdit={cancelEdit} onStartEdit={startEdit}
            deletingId={deletingId} onDelete={handleDelete}
            selectedIds={selectedIds} allSelected={allSelected} onToggleSelect={toggleSelect} onToggleSelectAll={toggleSelectAll}
            txnTicker={txnTicker} onToggleTxnPanel={t => setTxnTicker(txnTicker === t ? null : t)}
            onCloseTxnPanel={() => setTxnTicker(null)} onRefresh={() => loadSummary(true)} navigate={navigate}
            renderCap={120}
          />
        )
      ) : activeTab === 'dividends' ? (
        <DividendsTab data={dividendData} loading={enrichLoading} onRefresh={handleRefreshAll}
          assetClassFilter={assetClassFilter} onAssetClassFilter={setAssetClassFilter}
          sectorFilter={sectorFilter} onSectorFilter={setSectorFilter}
          sectorsPresent={sectorsPresent} hasSectorData={hasSectorData} sectorsLoading={sectorsLoading}
          groupBySector={groupBySector} onGroupBySector={handleGroupBySector}
          tickerSearch={tickerSearch} onTickerSearch={setTickerSearch}
          sectorMap={sectorMap} />
      ) : activeTab === 'fundamentals' ? (
        <FundamentalsTab data={fundamentalData} loading={enrichLoading} onRefresh={handleRefreshAll}
          assetClassFilter={assetClassFilter} onAssetClassFilter={setAssetClassFilter}
          sectorFilter={sectorFilter} onSectorFilter={setSectorFilter}
          sectorsPresent={sectorsPresent} hasSectorData={hasSectorData} sectorsLoading={sectorsLoading}
          groupBySector={groupBySector} onGroupBySector={handleGroupBySector}
          tickerSearch={tickerSearch} onTickerSearch={setTickerSearch}
          sectorMap={sectorMap} />
      ) : (
        <TechnicalTab data={technicalData} loading={enrichLoading} onRefresh={handleRefreshAll}
          assetClassFilter={assetClassFilter} onAssetClassFilter={setAssetClassFilter}
          sectorFilter={sectorFilter} onSectorFilter={setSectorFilter}
          sectorsPresent={sectorsPresent} hasSectorData={hasSectorData} sectorsLoading={sectorsLoading}
          groupBySector={groupBySector} onGroupBySector={handleGroupBySector}
          tickerSearch={tickerSearch} onTickerSearch={setTickerSearch}
          sectorMap={sectorMap} />
      )}
    </div>
  );
}

// ─── Shared filter/search bar ─────────────────────────────────────────────────

interface SectorBarProps {
  assetClassFilter: AssetClass; onAssetClassFilter: (v: AssetClass) => void;
  sectorFilter: string; onSectorFilter: (v: string) => void;
  sectorsPresent: string[]; hasSectorData: boolean;
  sectorsLoading?: boolean;
  groupBySector: boolean; onGroupBySector: (v: boolean) => void;
  tickerSearch: string; onTickerSearch: (v: string) => void;
  sectorMap: Record<string, SectorMeta>;
}

function SectorFilterBar({
  assetClassFilter, onAssetClassFilter,
  sectorFilter, onSectorFilter,
  sectorsPresent, hasSectorData, sectorsLoading,
  groupBySector, onGroupBySector,
  tickerSearch, onTickerSearch,
}: SectorBarProps) {
  return (
    <div className="space-y-2">
      {/* Row 1: All / Stocks / Funds toggle + search */}
      <div className="flex flex-wrap items-center gap-2">
        <div className="flex gap-0.5 p-0.5 bg-base-200/60 rounded-lg border border-white/[0.05]">
          {(['ALL', 'STOCKS', 'FUNDS', 'OPTIONS'] as const).map(cls => (
            <button key={cls} onClick={() => { onAssetClassFilter(cls); onSectorFilter('ALL'); }}
              className={`btn btn-xs rounded-md font-semibold transition-all px-3 ${assetClassFilter === cls ? 'bg-primary text-primary-content shadow-sm' : 'btn-ghost text-base-content/50 hover:text-base-content'}`}>
              {cls === 'ALL' ? 'All' : cls === 'STOCKS' ? 'Stocks' : cls === 'FUNDS' ? 'Funds' : 'Options'}
            </button>
          ))}
        </div>
        <div className="flex items-center gap-1.5 ml-auto">
          <Filter className="w-3.5 h-3.5 text-base-content/40" />
          <input type="text" className="input input-bordered input-xs rounded-lg bg-base-200/50 w-36" placeholder="Search ticker or name…" value={tickerSearch} onChange={e => onTickerSearch(e.target.value)} />
        </div>
      </div>
      {/* Row 2: Sector pills — always visible, shows loader while fetching */}
      <div className="flex flex-wrap items-center gap-1.5 min-h-[26px]">
        <span className="text-[10px] text-base-content/30 uppercase tracking-wider font-semibold">Sector</span>
        {sectorsLoading && !hasSectorData ? (
          <span className="flex items-center gap-1 text-[11px] text-base-content/30">
            <Loader2 className="w-3 h-3 animate-spin" />Loading sectors…
          </span>
        ) : hasSectorData ? (
          <>
            {['ALL', ...sectorsPresent].map(s => (
              <button key={s} onClick={() => onSectorFilter(s)}
                className={`btn btn-xs rounded-lg font-medium ${sectorFilter === s ? 'bg-secondary/20 text-secondary border border-secondary/30' : 'btn-ghost border border-white/[0.06] text-base-content/50 hover:text-base-content'}`}>
                {s === 'ALL' ? 'All Sectors' : s}
              </button>
            ))}
            <div className="ml-auto flex items-center gap-1.5">
              <span className="text-[10px] text-base-content/30 uppercase tracking-wider font-semibold">View</span>
              <button
                onClick={() => onGroupBySector(!groupBySector)}
                className={`btn btn-sm rounded-xl gap-1.5 font-medium ${groupBySector ? 'bg-secondary/20 text-secondary border border-secondary/30' : 'btn-ghost border border-white/[0.08] text-base-content/50'}`}>
                <PieChart className="w-3.5 h-3.5" />
                {groupBySector ? 'Grouped by Sector' : 'Group by Sector'}
              </button>
            </div>
          </>
        ) : (
          <span className="text-[11px] text-base-content/20">Sector data unavailable</span>
        )}
      </div>
    </div>
  );
}

// ─── Overview view-mode switcher (Attention / Groups / Table) ─────────────────

function ViewSwitcher({ mode, onChange, total }: {
  mode: ViewMode; onChange: (m: ViewMode) => void; total: number;
}) {
  const opts: { id: ViewMode; label: string; icon: React.ReactNode; hint: string }[] = [
    { id: 'attention', label: 'Attention', icon: <Sparkles className="w-3.5 h-3.5" />, hint: 'What needs a look' },
    { id: 'groups', label: 'Groups', icon: <PieChart className="w-3.5 h-3.5" />, hint: 'Rolled up by sector' },
    { id: 'table', label: 'Table', icon: <ClipboardList className="w-3.5 h-3.5" />, hint: `All ${total} positions` },
  ];
  return (
    <div className="flex items-center gap-1 p-1 bg-base-200/40 rounded-xl border border-white/[0.04]">
      {opts.map(o => (
        <button key={o.id} title={o.hint} onClick={() => onChange(o.id)}
          className={`flex items-center gap-1.5 px-2.5 py-1.5 rounded-lg text-xs font-medium transition-all ${mode === o.id ? 'bg-primary text-primary-content shadow-sm' : 'text-base-content/55 hover:text-base-content hover:bg-base-200/60'}`}>
          {o.icon}<span className="hidden sm:inline">{o.label}</span>
        </button>
      ))}
    </div>
  );
}

// ─── Attention view: the ~12 holdings that actually warrant a look ────────────
// Ranks the book by what moved today, what's stretched vs cost, what's a heavy
// weight, and what has a catalyst coming up — so a 300-name portfolio opens on
// signal instead of a wall of rows.

function AttentionHoldings({ holdings, events, technicalData, totalMarketValue, onOpen, onViewAll }: {
  holdings: EnhancedHolding[];
  events: PortfolioEvent[];
  technicalData: PortfolioTechnicalData[];
  totalMarketValue: number | null;
  onOpen: (ticker: string, tab: TabId) => void;
  onViewAll: () => void;
}) {
  const ranked = useMemo(() => {
    const earnSoon = new Map<string, number>();
    for (const e of events) {
      if (e.type === 'earnings' && e.days_until <= 14) {
        const prev = earnSoon.get(e.ticker);
        if (prev == null || e.days_until < prev) earnSoon.set(e.ticker, e.days_until);
      }
    }
    const upsideMap = new Map<string, number>();
    for (const t of technicalData) if (t.analyst_upside_pct != null) upsideMap.set(t.ticker, t.analyst_upside_pct);
    const tmv = totalMarketValue && totalMarketValue > 0 ? totalMarketValue : null;

    const scored = holdings.map(h => {
      const weight = h.weight_pct ?? (tmv && h.market_value != null ? (h.market_value / tmv) * 100 : 0);
      const dcp = h.day_change_pct ?? 0;
      const uglp = h.unrealized_gain_loss_pct ?? 0;
      const days = earnSoon.get(h.ticker);
      const upside = upsideMap.get(h.ticker);

      const score =
        Math.min(Math.abs(weight), 40) / 40 * 30 +     // concentration
        Math.min(Math.abs(dcp), 15) / 15 * 40 +        // today's move
        Math.min(Math.abs(uglp), 50) / 50 * 30 +       // P&L extremity
        (days != null ? Math.max(0, 18 - days) : 0) +  // imminent earnings
        (upside != null && Math.abs(upside) >= 20 ? 10 : 0);

      let reason = 'Worth a look';
      let tone: 'pos' | 'neg' | 'neutral' = 'neutral';
      let tab: TabId = 'overview';
      if (days != null) {
        reason = days === 0 ? 'Reports earnings today' : `Earnings in ${days}d`; tab = 'technical';
      } else if (Math.abs(dcp) >= 3) {
        reason = `${dcp >= 0 ? 'Up' : 'Down'} ${Math.abs(dcp).toFixed(1)}% today`; tone = dcp >= 0 ? 'pos' : 'neg';
      } else if (Math.abs(uglp) >= 15) {
        reason = `${uglp >= 0 ? 'Up' : 'Down'} ${Math.abs(uglp).toFixed(0)}% vs cost`; tone = uglp >= 0 ? 'pos' : 'neg';
      } else if (weight >= 10) {
        reason = `${weight.toFixed(0)}% of the book`;
      } else if (upside != null && Math.abs(upside) >= 20) {
        reason = upside >= 0 ? `${upside.toFixed(0)}% analyst upside` : `${Math.abs(upside).toFixed(0)}% over target`;
        tone = upside >= 0 ? 'pos' : 'neg'; tab = 'technical';
      }
      return { h, score, reason, tone, tab };
    });
    scored.sort((a, b) => b.score - a.score);
    return scored.slice(0, 12);
  }, [holdings, events, technicalData, totalMarketValue]);

  if (!holdings.length) return null;

  return (
    <div className="glass-card p-4 space-y-3">
      <div className="flex items-center justify-between gap-3">
        <div className="flex items-center gap-2">
          <span className="flex h-7 w-7 items-center justify-center rounded-lg bg-primary/15 ring-1 ring-primary/20">
            <Sparkles className="w-3.5 h-3.5 text-primary" />
          </span>
          <div>
            <div className="text-sm font-bold tracking-tight">Needs your attention</div>
            <div className="text-[11px] text-base-content/45">
              Top {ranked.length} of {holdings.length}, ranked by what moved and what's coming up
            </div>
          </div>
        </div>
        <button onClick={onViewAll}
          className="flex items-center gap-1.5 rounded-lg border border-white/[0.06] bg-base-200/50 px-2.5 py-1.5 text-[11px] font-medium text-base-content/65 transition-colors hover:text-base-content hover:bg-base-200/80">
          View all {holdings.length} <ExternalLink className="w-3 h-3" />
        </button>
      </div>
      <div className="space-y-1.5">
        {ranked.map(({ h, reason, tone, tab }) => (
          <button key={h.id} onClick={() => onOpen(h.ticker, tab)}
            className="group w-full flex items-center gap-3 rounded-xl border border-white/[0.05] bg-base-200/30 px-3 py-2.5 text-left transition-colors hover:bg-base-200/60">
            <div className="min-w-0 flex-1">
              <div className="flex items-center gap-2">
                <span className="font-bold text-sm text-base-content/90">{h.ticker}</span>
                {h.sector && <span className="hidden sm:inline text-[10px] text-base-content/35">{h.sector}</span>}
              </div>
              <div className={`text-[11px] font-medium ${tone === 'pos' ? 'text-success' : tone === 'neg' ? 'text-error' : 'text-base-content/55'}`}>{reason}</div>
            </div>
            <div className="text-right shrink-0">
              <div className="text-sm font-bold tabular-nums">{fmt$(h.market_value)}</div>
              <div className={`text-[11px] tabular-nums ${(h.day_change_pct ?? 0) >= 0 ? 'text-success' : 'text-error'}`}>{fmtPct(h.day_change_pct)}</div>
            </div>
            <ExternalLink className="w-3.5 h-3.5 text-base-content/20 group-hover:text-base-content/50 shrink-0" />
          </button>
        ))}
      </div>
    </div>
  );
}

// ─── Sector summary row for grouped view ──────────────────────────────────────

function SectorSummaryRow({
  label, rows, totalPortfolioValue, colSpan, collapsed, onToggle,
}: {
  label: string; rows: EnhancedHolding[]; totalPortfolioValue: number | null;
  colSpan: number; collapsed: boolean; onToggle: () => void;
}) {
  const totalValue = rows.reduce((s, h) => s + (h.market_value ?? 0), 0);
  const totalUnrealized = rows.reduce((s, h) => s + (h.unrealized_gain_loss ?? 0), 0);
  const totalCost = rows.reduce((s, h) => s + h.total_cost, 0);
  const unrealPct = totalCost > 0 ? (totalUnrealized / totalCost) * 100 : null;
  const totalDayPnl = rows.reduce((s, h) => s + (h.day_pnl ?? 0), 0);
  const weightPct = totalPortfolioValue && totalPortfolioValue > 0 ? (totalValue / totalPortfolioValue) * 100 : null;

  return (
    <tr onClick={onToggle} className="cursor-pointer select-none bg-base-200/40 hover:bg-base-200/60 transition-colors border-t-2 border-secondary/15">
      <td colSpan={colSpan} className="px-3 py-2">
        <div className="flex items-center gap-3 flex-wrap">
          <div className="flex items-center gap-2">
            <div className={`transition-transform ${collapsed ? '-rotate-90' : ''}`}>
              <ChevronDown className="w-3.5 h-3.5 text-base-content/40" />
            </div>
            <span className="text-xs font-bold uppercase tracking-wider text-secondary/80">{label}</span>
            <span className="text-[10px] text-base-content/30 bg-base-300/50 rounded px-1.5 py-0.5">{rows.length} position{rows.length !== 1 ? 's' : ''}</span>
          </div>
          <div className="flex items-center gap-4 ml-2 flex-wrap">
            {totalValue > 0 && (
              <span className="text-sm font-semibold text-base-content/80">{fmt$(totalValue, 0)}</span>
            )}
            {weightPct != null && (
              <span className="text-[11px] text-base-content/40">{weightPct.toFixed(1)}% of portfolio</span>
            )}
            {unrealPct != null && (
              <span className={`text-[11px] font-semibold ${gainCls(totalUnrealized)}`}>
                {totalUnrealized >= 0 ? '+' : ''}{fmt$(totalUnrealized, 0)} ({unrealPct.toFixed(1)}%)
              </span>
            )}
            {totalDayPnl !== 0 && (
              <span className={`text-[11px] ${gainCls(totalDayPnl)}`}>
                {totalDayPnl >= 0 ? '+' : ''}{fmt$(totalDayPnl, 0)} today
              </span>
            )}
          </div>
        </div>
      </td>
    </tr>
  );
}

// ─── Holding AI Analysis Panel ────────────────────────────────────────────────

type ChatMsg = { role: 'user' | 'assistant'; content: string };

function HoldingAnalysisPanel({ holding, onClose }: { holding: EnhancedHolding; onClose: () => void }) {
  const [messages, setMessages] = useState<ChatMsg[]>([]);
  const [question, setQuestion] = useState('');
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const bottomRef = React.useRef<HTMLDivElement>(null);

  const holdingCtx = {
    shares: holding.shares, cost_basis: holding.cost_basis,
    current_price: holding.current_price, market_value: holding.market_value,
    unrealized_gain_loss: holding.unrealized_gain_loss,
    unrealized_gain_loss_pct: holding.unrealized_gain_loss_pct,
    realized_gain_loss: holding.realized_gain_loss,
    total_return_pct: holding.total_return_pct,
    annualized_return_pct: holding.annualized_return_pct,
    day_change_pct: holding.day_change_pct,
    weight_pct: holding.weight_pct,
  } as Record<string, unknown>;

  useEffect(() => {
    let cancelled = false;
    setLoading(true); setError(null);
    analyzeHolding(holding.ticker, [], null, holdingCtx)
      .then(r => { if (!cancelled) setMessages([{ role: 'assistant', content: r.content }]); })
      .catch(e => { if (!cancelled) setError(e instanceof Error ? e.message : 'Analysis failed'); })
      .finally(() => { if (!cancelled) setLoading(false); });
    return () => { cancelled = true; };
  }, [holding.ticker]); // eslint-disable-line react-hooks/exhaustive-deps

  useEffect(() => { bottomRef.current?.scrollIntoView({ behavior: 'smooth' }); }, [messages]);

  const sendQuestion = async () => {
    const q = question.trim();
    if (!q || loading) return;
    const userMsg: ChatMsg = { role: 'user', content: q };
    const newHistory = [...messages, userMsg];
    setMessages(newHistory);
    setQuestion('');
    setLoading(true); setError(null);
    try {
      const r = await analyzeHolding(holding.ticker, newHistory, q, holdingCtx);
      setMessages([...newHistory, { role: 'assistant', content: r.content }]);
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Request failed');
    } finally { setLoading(false); }
  };

  return (
    <div className="glass-card mt-0 border-t-2 border-secondary/20 animate-fade-in-down">
      <div className="flex items-center justify-between px-4 pt-3 pb-2 border-b border-white/[0.04]">
        <span className="flex items-center gap-2 font-bold text-sm">
          <Brain className="w-3.5 h-3.5 text-secondary" />
          AI Analysis · <span className="text-secondary">{holding.ticker}</span>
          {holding.company_name && <span className="text-xs text-base-content/40 font-normal">{holding.company_name}</span>}
        </span>
        <button className="btn btn-ghost btn-xs btn-square rounded-lg" onClick={onClose}><X className="w-3.5 h-3.5" /></button>
      </div>

      {/* Chat window */}
      <div className="px-4 py-3 space-y-4 max-h-[50vh] overflow-y-auto">
        {loading && messages.length === 0 && (
          <div className="flex items-center gap-2 text-xs text-secondary/60 py-2">
            <Loader2 className="w-3.5 h-3.5 animate-spin" /> Analysing position data…
          </div>
        )}
        {error && (
          <div className="flex items-center gap-2 text-xs text-error">
            <AlertTriangle className="w-3.5 h-3.5" /> {error}
          </div>
        )}
        {messages.map((m, i) => (
          <div key={i} className={m.role === 'user' ? 'flex justify-end' : ''}>
            {m.role === 'user' ? (
              <div className="bg-primary/10 border border-primary/20 rounded-xl px-3 py-2 text-xs max-w-[70%]">{m.content}</div>
            ) : (
              <div className="prose prose-sm max-w-none text-xs leading-relaxed [&>*:first-child]:mt-0 [&>*:last-child]:mb-0">
                <ReactMarkdown remarkPlugins={[remarkGfm]}>{m.content}</ReactMarkdown>
              </div>
            )}
          </div>
        ))}
        {loading && messages.length > 0 && (
          <div className="flex items-center gap-1.5 text-xs text-secondary/50">
            <Loader2 className="w-3 h-3 animate-spin" /> Thinking…
          </div>
        )}
        <div ref={bottomRef} />
      </div>

      {/* Input */}
      <div className="px-4 pb-3 pt-1 border-t border-white/[0.04] flex gap-2">
        <input
          className="input input-bordered input-xs flex-1 rounded-lg bg-base-200/50"
          placeholder="Ask a follow-up: why is EPS down? what does the analyst target imply? …"
          value={question}
          onChange={e => setQuestion(e.target.value)}
          onKeyDown={e => e.key === 'Enter' && sendQuestion()}
          disabled={loading}
        />
        <button className="btn btn-secondary btn-xs rounded-lg gap-1" onClick={sendQuestion} disabled={loading || !question.trim()}>
          {loading ? <Loader2 className="w-3 h-3 animate-spin" /> : <Send className="w-3 h-3" />}
        </button>
      </div>
    </div>
  );
}

// ─── Create Agent Panel ────────────────────────────────────────────────────────

function CreateAgentPanel({ holding, onClose, onCreated }: {
  holding: EnhancedHolding; onClose: () => void; onCreated: () => void;
}) {
  const navigate = useNavigate();
  const defaultInstruction = `Monitor my ${holding.ticker} position (${holding.shares} shares, avg cost $${holding.cost_basis?.toFixed(2) ?? 'N/A'}, current value $${holding.market_value?.toFixed(0) ?? 'N/A'}).

Track and alert me about:
- Earnings results and EPS/revenue vs estimates
- Analyst rating upgrades/downgrades and target price changes
- Significant price moves (>5% in a day)
- Dividend changes or ex-dividend announcements
- Key news or events that could impact the position

For each event, provide a brief analysis of how it affects my position and whether I should take action.`;

  const [name, setName] = useState(`${holding.ticker} Position Monitor`);
  const [instruction, setInstruction] = useState(defaultInstruction);
  const [saving, setSaving] = useState(false);
  const [success, setSuccess] = useState<number | null>(null);
  const [err, setErr] = useState<string | null>(null);

  const handleCreate = async () => {
    if (!name.trim() || !instruction.trim()) return;
    setSaving(true); setErr(null);
    try {
      const agent = await createAgent({ name, instruction, description: `Portfolio monitor for ${holding.ticker}`, schedule_type: 'manual' });
      setSuccess(agent.id);
      onCreated();
    } catch (e) { setErr(e instanceof Error ? e.message : 'Failed to create agent'); }
    finally { setSaving(false); }
  };

  return (
    <div className="glass-card mt-0 border-t-2 border-accent/20 animate-fade-in-down">
      <div className="flex items-center justify-between px-4 pt-3 pb-2 border-b border-white/[0.04]">
        <span className="flex items-center gap-2 font-bold text-sm">
          <Bot className="w-3.5 h-3.5 text-accent" />
          Create Agent · <span className="text-accent">{holding.ticker}</span>
        </span>
        <button className="btn btn-ghost btn-xs btn-square rounded-lg" onClick={onClose}><X className="w-3.5 h-3.5" /></button>
      </div>

      {success !== null ? (
        <div className="px-4 py-4 space-y-3">
          <div className="flex items-center gap-2 text-success text-sm font-semibold">
            <CheckCircle className="w-4 h-4" /> Agent created!
          </div>
          <div className="flex gap-2">
            <button className="btn btn-sm btn-accent rounded-xl gap-1" onClick={() => navigate(`/agents`)}>
              <Bot className="w-3.5 h-3.5" /> View Agents
            </button>
            <button className="btn btn-sm btn-ghost rounded-xl" onClick={onClose}>Close</button>
          </div>
        </div>
      ) : (
        <div className="px-4 py-3 space-y-3">
          {err && <p className="text-xs text-error">{err}</p>}
          <div>
            <label className="text-xs text-base-content/40 mb-0.5 block">Agent Name</label>
            <input className="input input-bordered input-xs w-full rounded-lg bg-base-200/50" value={name} onChange={e => setName(e.target.value)} />
          </div>
          <div>
            <label className="text-xs text-base-content/40 mb-0.5 block">Instruction</label>
            <textarea className="textarea textarea-bordered w-full rounded-lg bg-base-200/50 text-xs resize-none" rows={6} value={instruction} onChange={e => setInstruction(e.target.value)} />
          </div>
          <div className="flex justify-end gap-2">
            <button className="btn btn-ghost btn-xs rounded-lg" onClick={onClose}>Cancel</button>
            <button className="btn btn-accent btn-xs rounded-lg gap-1" onClick={handleCreate} disabled={saving || !name.trim()}>
              {saving ? <Loader2 className="w-3 h-3 animate-spin" /> : <Bot className="w-3 h-3" />}
              Create Agent
            </button>
          </div>
        </div>
      )}
    </div>
  );
}

// ─── Portfolio Analytics Strip ────────────────────────────────────────────────
// Chip row: key risk/return metrics computed from locally-available data.

interface AnalyticsStripProps {
  holdings: EnhancedHolding[];
  dividendData: DividendData[];
  technicalData: PortfolioTechnicalData[];
  totalMarketValue: number | null;
}

function AnalyticsStrip({ holdings, dividendData, technicalData, totalMarketValue }: AnalyticsStripProps) {
  // Weighted portfolio beta (requires technicalData to be loaded)
  const betaChip = useMemo(() => {
    if (!technicalData.length || !totalMarketValue) return null;
    const techMap = Object.fromEntries(technicalData.map(t => [t.ticker, t]));
    let weighted = 0, weight = 0;
    for (const h of holdings) {
      const beta = techMap[h.ticker]?.beta;
      const mv = h.market_value;
      if (beta != null && mv != null) { weighted += beta * mv; weight += mv; }
    }
    return weight > 0 ? (weighted / weight) : null;
  }, [holdings, technicalData, totalMarketValue]);

  // Largest single position
  const topHolding = useMemo(() => {
    if (!holdings.length || !totalMarketValue) return null;
    const top = holdings.reduce((a, b) => (a.market_value ?? 0) > (b.market_value ?? 0) ? a : b, holdings[0]);
    const wt = top.market_value && totalMarketValue ? (top.market_value / totalMarketValue * 100) : null;
    return wt ? { ticker: top.ticker, pct: wt } : null;
  }, [holdings, totalMarketValue]);

  // Portfolio yield (from dividend data)
  const yieldChip = useMemo(() => {
    if (!dividendData.length || !totalMarketValue || totalMarketValue === 0) return null;
    const income = dividendData.reduce((s, d) => s + (d.annual_income || 0), 0);
    return income > 0 ? (income / totalMarketValue * 100) : null;
  }, [dividendData, totalMarketValue]);

  // Long-term positions (held > 1 year) by market value %
  const ltPct = useMemo(() => {
    if (!holdings.length || !totalMarketValue) return null;
    const cutoff = new Date(); cutoff.setFullYear(cutoff.getFullYear() - 1);
    const ltValue = holdings.reduce((s, h) => {
      const d = h.first_buy_date || h.purchase_date;
      return (d && new Date(d) < cutoff) ? s + (h.market_value ?? 0) : s;
    }, 0);
    return totalMarketValue > 0 ? ltValue / totalMarketValue * 100 : null;
  }, [holdings, totalMarketValue]);

  // Unique sectors
  const sectorCount = useMemo(() => {
    const sectors = new Set(holdings.map(h => h.sector).filter(Boolean));
    return sectors.size;
  }, [holdings]);

  const chips: { icon: React.ReactNode; label: string; value: string; color?: string; title: string }[] = [];

  if (betaChip != null) {
    const c = betaChip < 0.8 ? 'text-success' : betaChip < 1.3 ? 'text-base-content/70' : 'text-warning';
    chips.push({ icon: <Shield className="w-3 h-3" />, label: 'Portfolio β', value: betaChip.toFixed(2), color: c, title: 'Market-value weighted portfolio beta' });
  }
  if (yieldChip != null) {
    chips.push({ icon: <DollarSign className="w-3 h-3" />, label: 'Div Yield', value: `${yieldChip.toFixed(2)}%`, title: 'Estimated portfolio dividend yield' });
  }
  if (topHolding) {
    const c = topHolding.pct > 20 ? 'text-warning' : topHolding.pct > 10 ? 'text-base-content/70' : 'text-success';
    chips.push({ icon: <Target className="w-3 h-3" />, label: `Top: ${topHolding.ticker}`, value: `${topHolding.pct.toFixed(1)}%`, color: c, title: 'Largest single position by weight' });
  }
  if (sectorCount > 0) {
    chips.push({ icon: <PieChart className="w-3 h-3" />, label: 'Sectors', value: `${sectorCount}`, title: 'Number of distinct sectors represented' });
  }
  if (ltPct != null) {
    const c = ltPct > 80 ? 'text-success' : ltPct > 50 ? 'text-base-content/70' : 'text-warning';
    chips.push({ icon: <Clock className="w-3 h-3" />, label: 'Long-term', value: `${ltPct.toFixed(0)}%`, color: c, title: '% of portfolio held > 1 year (long-term capital gains)' });
  }

  if (!chips.length) return null;

  return (
    <div className="flex flex-wrap items-center gap-2">
      {chips.map((c, i) => (
        <div key={i} title={c.title}
          className="flex items-center gap-1.5 px-2.5 py-1 rounded-lg bg-base-200/50 border border-white/[0.05] text-xs">
          <span className={`${c.color || 'text-base-content/40'}`}>{c.icon}</span>
          <span className="text-base-content/40 font-medium">{c.label}</span>
          <span className={`font-bold ${c.color || 'text-base-content/80'}`}>{c.value}</span>
        </div>
      ))}
    </div>
  );
}

// ─── Portfolio Insights (deterministic, ambient) ──────────────────────────────
// Proactively surfaces what matters — concentration, income, catalysts, risk —
// computed entirely from already-loaded data. No API call, no LLM, no
// hallucination. The pull-based Copilot panel handles open-ended questions;
// this is the push-based layer that tells you what to look at without asking.

type InsightSeverity = 'alert' | 'warn' | 'good' | 'info';
interface InsightItem {
  id: string;
  severity: InsightSeverity;
  icon: React.ReactNode;
  title: string;
  detail: string;
  ticker?: string;
  tab?: TabId;
}

const INSIGHT_STYLE: Record<InsightSeverity, { wrap: string; icon: string }> = {
  alert: { wrap: 'border-error/25 bg-error/[0.06]',     icon: 'text-error' },
  warn:  { wrap: 'border-warning/25 bg-warning/[0.05]', icon: 'text-warning' },
  good:  { wrap: 'border-success/25 bg-success/[0.05]', icon: 'text-success' },
  info:  { wrap: 'border-white/[0.06] bg-base-200/40',  icon: 'text-primary/70' },
};

function PortfolioInsights({ holdings, events, technicalData, totalMarketValue, onFocus }: {
  holdings: EnhancedHolding[];
  events: PortfolioEvent[];
  technicalData: PortfolioTechnicalData[];
  totalMarketValue: number | null;
  onFocus: (ticker?: string, tab?: TabId) => void;
}) {
  const [expanded, setExpanded] = useState(false);

  const insights = useMemo<InsightItem[]>(() => {
    const out: InsightItem[] = [];
    const tmv = totalMarketValue || 0;
    const byMv = holdings.filter(h => (h.market_value ?? 0) > 0).sort((a, b) => (b.market_value ?? 0) - (a.market_value ?? 0));
    if (!byMv.length) return out;

    // ── Single-name concentration ──────────────────────────────────────────
    if (tmv > 0) {
      const top = byMv[0];
      const wt = (top.market_value! / tmv) * 100;
      if (wt >= 25) out.push({ id: 'conc1', severity: 'alert', icon: <Target className="w-3.5 h-3.5" />, title: `${top.ticker} is ${wt.toFixed(0)}% of your portfolio`, detail: 'Heavy single-name concentration — one bad print moves the whole book.', ticker: top.ticker, tab: 'overview' });
      else if (wt >= 15) out.push({ id: 'conc1', severity: 'warn', icon: <Target className="w-3.5 h-3.5" />, title: `${top.ticker} is ${wt.toFixed(0)}% of your portfolio`, detail: 'Above the typical 10–15% comfort zone for one position.', ticker: top.ticker, tab: 'overview' });
    }

    // ── Top-3 concentration ────────────────────────────────────────────────
    if (tmv > 0 && byMv.length >= 4) {
      const top3 = byMv.slice(0, 3).reduce((s, h) => s + (h.market_value ?? 0), 0) / tmv * 100;
      if (top3 >= 55) out.push({ id: 'conc3', severity: 'warn', icon: <PieChart className="w-3.5 h-3.5" />, title: `Top 3 holdings are ${top3.toFixed(0)}% of the book`, detail: `${byMv.slice(0, 3).map(h => h.ticker).join(', ')} concentrate most of your risk.` });
    }

    // ── Sector concentration ───────────────────────────────────────────────
    if (tmv > 0) {
      const buckets: Record<string, number> = {};
      for (const h of holdings) { if (h.sector && h.market_value) buckets[h.sector] = (buckets[h.sector] || 0) + h.market_value; }
      const top = Object.entries(buckets).sort((a, b) => b[1] - a[1])[0];
      if (top && top[1] / tmv * 100 >= 40) out.push({ id: 'sector', severity: 'warn', icon: <PieChart className="w-3.5 h-3.5" />, title: `${top[0]} is ${(top[1] / tmv * 100).toFixed(0)}% of your portfolio`, detail: 'Strong sector tilt — a sector-wide move hits you hard.' });
    }

    // ── Upcoming dividend income (next 30d) ────────────────────────────────
    const exDiv = events.filter(e => e.type === 'ex_div' && e.days_until >= 0 && e.days_until <= 30);
    const incomeSum = exDiv.reduce((s, e) => s + (e.estimated_income || 0), 0);
    if (incomeSum > 0) out.push({ id: 'income', severity: 'good', icon: <DollarSign className="w-3.5 h-3.5" />, title: `~${fmt$(incomeSum, 0)} in dividends over the next 30 days`, detail: `${exDiv.length} ex-dividend date${exDiv.length > 1 ? 's' : ''} approaching.`, tab: 'dividends' });

    // ── Earnings ahead (next 14d) ──────────────────────────────────────────
    const earn = events.filter(e => e.type === 'earnings' && e.days_until >= 0 && e.days_until <= 14);
    if (earn.length) out.push({ id: 'earn', severity: 'info', icon: <Calendar className="w-3.5 h-3.5" />, title: `${earn.length} holding${earn.length > 1 ? 's' : ''} report earnings within 2 weeks`, detail: earn.slice(0, 6).map(e => e.ticker).join(', '), tab: 'technical' });

    // ── Big day mover ──────────────────────────────────────────────────────
    const movers = byMv.filter(h => h.day_change_pct != null && Math.abs(h.day_change_pct) >= 5);
    if (movers.length) {
      const m = movers.sort((a, b) => Math.abs(b.day_change_pct!) - Math.abs(a.day_change_pct!))[0];
      const up = m.day_change_pct! > 0;
      out.push({ id: 'mover', severity: up ? 'good' : 'info', icon: up ? <TrendingUp className="w-3.5 h-3.5" /> : <TrendingDown className="w-3.5 h-3.5" />, title: `${m.ticker} ${up ? '+' : ''}${m.day_change_pct!.toFixed(1)}% today`, detail: `${fmt$(Math.abs(m.day_pnl || 0), 0)} ${up ? 'gain' : 'loss'} on the day.`, ticker: m.ticker, tab: 'overview' });
    }

    // ── Deep loser → tax-loss harvest candidate ────────────────────────────
    const losers = byMv.filter(h => h.unrealized_gain_loss_pct != null && h.unrealized_gain_loss_pct <= -15);
    if (losers.length) {
      const l = losers.sort((a, b) => a.unrealized_gain_loss_pct! - b.unrealized_gain_loss_pct!)[0];
      out.push({ id: 'loss', severity: 'info', icon: <AlertTriangle className="w-3.5 h-3.5" />, title: `${l.ticker} is down ${Math.abs(l.unrealized_gain_loss_pct!).toFixed(0)}%`, detail: `${fmt$(Math.abs(l.unrealized_gain_loss || 0), 0)} unrealized loss — potential tax-loss harvest.`, ticker: l.ticker, tab: 'overview' });
    }

    // ── Analyst signals (only when Technical tab data is loaded) ────────────
    if (technicalData.length) {
      const tmap = Object.fromEntries(technicalData.map(t => [t.ticker, t]));
      const stretched = byMv.map(h => ({ h, up: tmap[h.ticker]?.analyst_upside_pct })).filter(x => x.up != null && x.up <= -12);
      if (stretched.length) { const s = stretched.sort((a, b) => a.up! - b.up!)[0]; out.push({ id: 'stretch', severity: 'warn', icon: <AlertTriangle className="w-3.5 h-3.5" />, title: `${s.h.ticker} trades ${Math.abs(s.up!).toFixed(0)}% above analyst target`, detail: 'Price has run past the mean target — watch for mean reversion.', ticker: s.h.ticker, tab: 'technical' }); }
      const upside = byMv.map(h => ({ h, up: tmap[h.ticker]?.analyst_upside_pct })).filter(x => x.up != null && x.up >= 25);
      if (upside.length) { const s = upside.sort((a, b) => b.up! - a.up!)[0]; out.push({ id: 'upside', severity: 'good', icon: <TrendingUp className="w-3.5 h-3.5" />, title: `${s.h.ticker} has ${s.up!.toFixed(0)}% analyst upside`, detail: 'Trading well below the mean analyst target.', ticker: s.h.ticker, tab: 'technical' }); }
    }

    const rank: Record<InsightSeverity, number> = { alert: 0, warn: 1, good: 2, info: 3 };
    return out.sort((a, b) => rank[a.severity] - rank[b.severity]);
  }, [holdings, events, technicalData, totalMarketValue]);

  if (!insights.length) return null;

  const shown = expanded ? insights : insights.slice(0, 4);
  const hidden = insights.length - shown.length;

  return (
    <div className="glass-card p-3">
      <div className="flex items-center justify-between mb-2 px-0.5">
        <div className="flex items-center gap-1.5">
          <Sparkles className="w-3.5 h-3.5 text-primary" />
          <span className="text-[11px] font-bold uppercase tracking-wider text-base-content/55">What needs your attention</span>
          <span className="text-[10px] text-base-content/30">· {insights.length}</span>
        </div>
        {insights.length > 4 && (
          <button onClick={() => setExpanded(e => !e)} className="text-[11px] text-primary/70 hover:text-primary transition-colors">
            {expanded ? 'Show less' : `+${hidden} more`}
          </button>
        )}
      </div>
      <div className="grid grid-cols-1 sm:grid-cols-2 gap-2">
        {shown.map(item => {
          const s = INSIGHT_STYLE[item.severity];
          const clickable = !!(item.ticker || item.tab);
          return (
            <button key={item.id} disabled={!clickable} onClick={() => clickable && onFocus(item.ticker, item.tab)}
              className={`flex items-start gap-2.5 px-3 py-2.5 rounded-xl border text-left transition-all ${s.wrap} ${clickable ? 'hover:brightness-125 cursor-pointer' : 'cursor-default'}`}>
              <span className={`mt-0.5 shrink-0 ${s.icon}`}>{item.icon}</span>
              <span className="min-w-0">
                <span className="block text-xs font-semibold text-base-content/85 leading-snug">{item.title}</span>
                <span className="block text-[11px] text-base-content/45 leading-snug mt-0.5">{item.detail}</span>
              </span>
            </button>
          );
        })}
      </div>
    </div>
  );
}

// ─── Portfolio Events Strip ────────────────────────────────────────────────────

function EventsStrip({ events, loading }: { events: PortfolioEvent[]; loading: boolean }) {
  if (loading) return (
    <div className="flex items-center gap-2 text-xs text-base-content/30 py-1">
      <Loader2 className="w-3 h-3 animate-spin" />Loading upcoming events…
    </div>
  );
  if (!events.length) return null;

  const next5 = events.slice(0, 6);

  return (
    <div className="flex items-center gap-2 flex-wrap">
      <span className="flex items-center gap-1 text-[10px] font-bold uppercase tracking-widest text-base-content/30 shrink-0">
        <Calendar className="w-3 h-3" />Upcoming
      </span>
      {next5.map((ev, i) => {
        const isEarnings = ev.type === 'earnings';
        const urgency = ev.days_until <= 3 ? 'border-warning/40 bg-warning/5 text-warning' :
                        ev.days_until <= 7 ? 'border-primary/30 bg-primary/5 text-primary' :
                        'border-base-300/30 bg-base-200/30 text-base-content/60';
        return (
          <div key={i} className={`flex items-center gap-1.5 px-2 py-0.5 rounded-lg border text-xs ${urgency}`}>
            <span className="font-bold">{ev.ticker}</span>
            <span className={`text-[10px] font-semibold px-1 rounded ${isEarnings ? 'bg-secondary/20 text-secondary' : 'bg-success/15 text-success'}`}>
              {isEarnings ? 'Earnings' : 'Ex-Div'}
            </span>
            <span className="text-[10px] opacity-70">
              {ev.days_until === 0 ? 'Today' : ev.days_until === 1 ? 'Tomorrow' : `${ev.days_until}d`}
            </span>
            {ev.estimated_income && ev.estimated_income > 0 && (
              <span className="text-[10px] text-success/70">+${ev.estimated_income.toFixed(0)}</span>
            )}
          </div>
        );
      })}
      {events.length > 6 && (
        <span className="text-[10px] text-base-content/30">+{events.length - 6} more</span>
      )}
    </div>
  );
}

// ─── Portfolio AI Copilot Panel ────────────────────────────────────────────────

type CopilotMsg = { role: 'user' | 'assistant'; content: string };

function PortfolioCopilotPanel({ hasHoldings }: { hasHoldings: boolean }) {
  const [open, setOpen] = useState(false);
  const [messages, setMessages] = useState<CopilotMsg[]>([]);
  const [question, setQuestion] = useState('');
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [briefed, setBriefed] = useState(false);
  const bottomRef = React.useRef<HTMLDivElement>(null);

  const fetchBrief = async () => {
    if (briefed || loading) return;
    setLoading(true); setError(null);
    try {
      const r = await portfolioCopilot(null, []);
      setMessages([{ role: 'assistant', content: r.content }]);
      setBriefed(true);
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Failed to load briefing');
    } finally { setLoading(false); }
  };

  const handleOpen = () => {
    const next = !open;
    setOpen(next);
    if (next && !briefed) fetchBrief();
  };

  useEffect(() => { bottomRef.current?.scrollIntoView({ behavior: 'smooth' }); }, [messages]);

  const sendQuestion = async () => {
    const q = question.trim();
    if (!q || loading) return;
    const userMsg: CopilotMsg = { role: 'user', content: q };
    const newHistory = [...messages, userMsg];
    setMessages(newHistory);
    setQuestion('');
    setLoading(true); setError(null);
    try {
      const r = await portfolioCopilot(q, newHistory);
      setMessages([...newHistory, { role: 'assistant', content: r.content }]);
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Request failed');
    } finally { setLoading(false); }
  };

  const SUGGESTIONS = [
    'What are my biggest concentration risks?',
    'Which positions should I consider trimming?',
    'How is my portfolio positioned vs rising rates?',
    'Summarise upcoming catalysts this month',
  ];

  return (
    <div className={`glass-card transition-all duration-300 ${open ? 'border-secondary/20' : 'border-white/[0.04]'}`}>
      {/* Header bar — always visible */}
      <button
        onClick={handleOpen}
        className="w-full flex items-center gap-3 px-4 py-3 hover:bg-white/[0.02] transition-colors rounded-t-2xl"
      >
        <div className="flex items-center gap-2 flex-1">
          <div className="w-7 h-7 rounded-lg bg-gradient-to-br from-secondary/30 to-primary/20 flex items-center justify-center shrink-0">
            <Brain className="w-3.5 h-3.5 text-secondary" />
          </div>
          <div className="text-left">
            <div className="text-sm font-bold flex items-center gap-2">
              Portfolio AI Copilot
              {loading && <Loader2 className="w-3 h-3 animate-spin text-secondary/50" />}
              {!open && briefed && <span className="badge badge-xs bg-secondary/20 text-secondary border-0">1 briefing ready</span>}
            </div>
            {!open && (
              <div className="text-xs text-base-content/35">Ask anything about your portfolio — risks, catalysts, rebalancing…</div>
            )}
          </div>
        </div>
        <div className={`transition-transform duration-200 text-base-content/30 ${open ? 'rotate-180' : ''}`}>
          <ChevronDown className="w-4 h-4" />
        </div>
      </button>

      {/* Expanded body */}
      {open && (
        <div className="border-t border-white/[0.04]">
          {/* Messages */}
          <div className="px-4 py-3 space-y-4 max-h-[42vh] overflow-y-auto">
            {loading && !messages.length && (
              <div className="flex items-center gap-2 text-sm text-secondary/60 py-4">
                <Loader2 className="w-4 h-4 animate-spin" />
                Analysing your portfolio…
              </div>
            )}
            {error && (
              <div className="flex items-center gap-2 text-xs text-error">
                <AlertTriangle className="w-3.5 h-3.5" />{error}
                <button className="btn btn-ghost btn-xs rounded ml-auto" onClick={fetchBrief}>Retry</button>
              </div>
            )}
            {messages.map((m, i) => (
              <div key={i} className={m.role === 'user' ? 'flex justify-end' : ''}>
                {m.role === 'user' ? (
                  <div className="bg-primary/10 border border-primary/20 rounded-xl px-3 py-2 text-sm max-w-[75%]">{m.content}</div>
                ) : (
                  <div className="prose prose-sm max-w-none text-sm leading-relaxed [&>*:first-child]:mt-0 [&>*:last-child]:mb-0">
                    <ReactMarkdown remarkPlugins={[remarkGfm]}>{m.content}</ReactMarkdown>
                  </div>
                )}
              </div>
            ))}
            {loading && messages.length > 0 && (
              <div className="flex items-center gap-1.5 text-xs text-secondary/50 pl-1">
                <Loader2 className="w-3 h-3 animate-spin" />Thinking…
              </div>
            )}
            <div ref={bottomRef} />
          </div>

          {/* Suggestion chips (shown before first Q) */}
          {messages.length <= 1 && !loading && (
            <div className="px-4 pb-2 flex flex-wrap gap-1.5">
              {SUGGESTIONS.map(s => (
                <button key={s}
                  onClick={() => { setQuestion(s); }}
                  className="btn btn-ghost btn-xs rounded-full border border-secondary/20 text-secondary/70 hover:bg-secondary/10 text-[11px]">
                  {s}
                </button>
              ))}
            </div>
          )}

          {/* Input */}
          <div className="px-4 pb-3 pt-1 border-t border-white/[0.04] flex gap-2">
            <input
              className="input input-bordered input-sm flex-1 rounded-xl bg-base-200/50 text-sm"
              placeholder="Ask about concentration, risk, rebalancing, earnings impact…"
              value={question}
              onChange={e => setQuestion(e.target.value)}
              onKeyDown={e => e.key === 'Enter' && sendQuestion()}
              disabled={loading}
            />
            <button
              className="btn btn-secondary btn-sm rounded-xl gap-1"
              onClick={sendQuestion}
              disabled={loading || !question.trim()}
            >
              {loading ? <Loader2 className="w-3.5 h-3.5 animate-spin" /> : <Send className="w-3.5 h-3.5" />}
            </button>
            {messages.length > 1 && (
              <button
                className="btn btn-ghost btn-sm btn-square rounded-xl"
                onClick={() => { setMessages([]); setBriefed(false); }}
                title="Clear conversation"
              >
                <RefreshCw className="w-3.5 h-3.5" />
              </button>
            )}
          </div>
        </div>
      )}
    </div>
  );
}

// ─── Overview Tab ─────────────────────────────────────────────────────────────

function OverviewTab({ holdings, sortKey, sortDir, onSort, assetClassFilter, onAssetClassFilter, sectorFilter, onSectorFilter, sectorsPresent, hasSectorData, sectorsLoading, groupBySector, onGroupBySector, collapsedSectors, onToggleCollapse, totalPortfolioValue, tickerSearch, onTickerSearch, sectorMap, editingId, editState, setEditState, savingEdit, onSaveEdit, onCancelEdit, onStartEdit, deletingId, onDelete, selectedIds, allSelected, onToggleSelect, onToggleSelectAll, txnTicker, onToggleTxnPanel, onCloseTxnPanel, onRefresh, navigate, renderCap }: {
  holdings: EnhancedHolding[]; sortKey: SortKey; sortDir: SortDir; onSort: (k: SortKey) => void;
  assetClassFilter: AssetClass; onAssetClassFilter: (v: AssetClass) => void;
  sectorFilter: string; onSectorFilter: (v: string) => void;
  sectorsPresent: string[]; hasSectorData: boolean; sectorsLoading: boolean;
  groupBySector: boolean; onGroupBySector: (v: boolean) => void;
  collapsedSectors: Set<string>; onToggleCollapse: (s: string) => void;
  totalPortfolioValue: number | null;
  tickerSearch: string; onTickerSearch: (v: string) => void;
  sectorMap: Record<string, SectorMeta>;
  editingId: number | null; editState: EditState; setEditState: (s: EditState) => void; savingEdit: boolean;
  onSaveEdit: () => void; onCancelEdit: () => void; onStartEdit: (h: EnhancedHolding) => void;
  deletingId: number | null; onDelete: (id: number, t: string) => void;
  selectedIds: Set<number>; allSelected: boolean; onToggleSelect: (id: number) => void; onToggleSelectAll: () => void;
  txnTicker: string | null; onToggleTxnPanel: (t: string) => void; onCloseTxnPanel: () => void;
  onRefresh: () => void; navigate: (p: string) => void;
  renderCap?: number;
}) {
  const [analysisTicker, setAnalysisTicker] = useState<string | null>(null);
  const [agentHolding, setAgentHolding] = useState<EnhancedHolding | null>(null);
  const [confirmDeleteId, setConfirmDeleteId] = useState<number | null>(null);
  const [showAllFlat, setShowAllFlat] = useState(false);
  // Cap rendered rows in the flat (ungrouped) view so a 300-name book doesn't
  // mount 300 table rows at once; "show all" lifts the cap on demand.
  const flatCapped = !groupBySector && !showAllFlat && renderCap != null && holdings.length > renderCap;

  // Auto-dismiss inline delete confirm after 5s
  useEffect(() => {
    if (confirmDeleteId === null) return;
    const t = setTimeout(() => setConfirmDeleteId(null), 5000);
    return () => clearTimeout(t);
  }, [confirmDeleteId]);
  const groupedRows: { sectorLabel: string | null; rows: EnhancedHolding[] }[] = useMemo(() => {
    if (!groupBySector) return [{ sectorLabel: null, rows: holdings }];
    const map = new Map<string, EnhancedHolding[]>();
    for (const h of holdings) {
      const s = h.sector || 'Unknown';
      if (!map.has(s)) map.set(s, []);
      map.get(s)!.push(h);
    }
    return Array.from(map.entries()).sort(([a], [b]) => a.localeCompare(b)).map(([s, rows]) => ({ sectorLabel: s, rows }));
  }, [holdings, groupBySector]);

  return (
    <div className="space-y-2">
      <SectorFilterBar
        assetClassFilter={assetClassFilter} onAssetClassFilter={onAssetClassFilter}
        sectorFilter={sectorFilter} onSectorFilter={onSectorFilter}
        sectorsPresent={sectorsPresent} hasSectorData={hasSectorData} sectorsLoading={sectorsLoading}
        groupBySector={groupBySector} onGroupBySector={onGroupBySector}
        tickerSearch={tickerSearch} onTickerSearch={onTickerSearch}
        sectorMap={sectorMap}
      />

      {/* Table */}
      <div className="glass-card overflow-hidden">
        <div className="overflow-x-auto overflow-y-auto max-h-[72vh]">
          <table className="table-pro w-full" style={{ tableLayout: 'fixed', minWidth: '900px' }}>
            <colgroup>
              <col style={{ width: '36px' }} />  {/* checkbox */}
              <col style={{ width: '90px' }} />  {/* ticker */}
              <col style={{ width: '60px' }} />  {/* type */}
              <col style={{ width: '70px' }} />  {/* shares */}
              <col style={{ width: '80px' }} />  {/* avg cost */}
              <col style={{ width: '80px' }} />  {/* price */}
              <col style={{ width: '70px' }} />  {/* day% */}
              <col style={{ width: '90px' }} />  {/* mkt val */}
              <col style={{ width: '105px' }} /> {/* unrealized */}
              <col style={{ width: '80px' }} />  {/* realized */}
              <col style={{ width: '72px' }} />  {/* total ret */}
              <col style={{ width: '72px' }} />  {/* ann ret */}
              <col style={{ width: '55px' }} />  {/* weight */}
              <col style={{ width: '140px' }} /> {/* actions */}
            </colgroup>
            <thead className="sticky top-0 z-20">
              <tr>
                <th className="pl-3 bg-base-200/95 backdrop-blur-sm border-b border-base-300/40"><input type="checkbox" className="checkbox checkbox-sm checkbox-primary rounded-md" checked={allSelected} onChange={onToggleSelectAll} /></th>
                <SortTh label="Ticker" k="ticker" cur={sortKey} dir={sortDir} onSort={onSort} cls="bg-base-200/95 backdrop-blur-sm border-b border-base-300/40" />
                <th className="text-xs bg-base-200/95 backdrop-blur-sm border-b border-base-300/40">Type</th>
                <SortTh label="Shares" k="shares" cur={sortKey} dir={sortDir} onSort={onSort} cls="bg-base-200/95 backdrop-blur-sm border-b border-base-300/40" />
                <SortTh label="Avg Cost" k="cost_basis" cur={sortKey} dir={sortDir} onSort={onSort} cls="bg-base-200/95 backdrop-blur-sm border-b border-base-300/40" />
                <SortTh label="Price" k="current_price" cur={sortKey} dir={sortDir} onSort={onSort} cls="bg-base-200/95 backdrop-blur-sm border-b border-base-300/40" />
                <SortTh label="Day %" k="day_change_pct" cur={sortKey} dir={sortDir} onSort={onSort} cls="bg-base-200/95 backdrop-blur-sm border-b border-base-300/40" />
                <SortTh label="Mkt Val" k="market_value" cur={sortKey} dir={sortDir} onSort={onSort} cls="bg-base-200/95 backdrop-blur-sm border-b border-base-300/40" />
                <SortTh label="Unreal. P&L" k="unrealized_gain_loss" cur={sortKey} dir={sortDir} onSort={onSort} cls="bg-base-200/95 backdrop-blur-sm border-b border-base-300/40" />
                <SortTh label="Realized" k="realized_gain_loss" cur={sortKey} dir={sortDir} onSort={onSort} cls="bg-base-200/95 backdrop-blur-sm border-b border-base-300/40" />
                <SortTh label="Tot Ret" k="total_return_pct" cur={sortKey} dir={sortDir} onSort={onSort} cls="bg-base-200/95 backdrop-blur-sm border-b border-base-300/40" />
                <SortTh label="Ann Ret" k="annualized_return_pct" cur={sortKey} dir={sortDir} onSort={onSort} cls="bg-base-200/95 backdrop-blur-sm border-b border-base-300/40" />
                <SortTh label="Wt%" k="weight_pct" cur={sortKey} dir={sortDir} onSort={onSort} cls="bg-base-200/95 backdrop-blur-sm border-b border-base-300/40" />
                <th className="bg-base-200/95 backdrop-blur-sm border-b border-base-300/40"></th>
              </tr>
            </thead>
            <tbody>
              {groupedRows.map(({ sectorLabel, rows }) => {
                const collapsed = sectorLabel != null && collapsedSectors.has(sectorLabel);
                const visibleRows = (sectorLabel === null && flatCapped) ? rows.slice(0, renderCap) : rows;
                return (
                <React.Fragment key={sectorLabel ?? '__all'}>
                  {sectorLabel !== null && (
                    <SectorSummaryRow
                      label={sectorLabel} rows={rows}
                      totalPortfolioValue={totalPortfolioValue}
                      colSpan={14} collapsed={collapsed}
                      onToggle={() => onToggleCollapse(sectorLabel)}
                    />
                  )}
                  {!collapsed && visibleRows.map(h => editingId === h.id ? (
                <tr key={h.id} className="bg-primary/5 ring-inset ring-1 ring-primary/30">
                  <td className="pl-3"><input type="checkbox" className="checkbox checkbox-sm rounded-md" checked={selectedIds.has(h.id)} disabled /></td>
                  <td>
                    <div className="flex items-center gap-1">
                      <span className="font-bold text-primary text-sm truncate">{h.ticker}</span>
                      {h.transaction_count > 0 && <span className="badge badge-xs badge-ghost opacity-60">{h.transaction_count}</span>}
                    </div>
                  </td>
                  <td><span className={`badge badge-xs ${ASSET_TYPE_COLORS[h.asset_type] || 'badge-ghost'}`}>{ASSET_TYPE_LABELS[h.asset_type] || h.asset_type}</span></td>
                  <td><input type="number" step="0.0001" min="0.0001" className="input input-bordered input-xs w-full rounded-lg bg-base-200/50" value={editState.shares} onChange={e => setEditState({ ...editState, shares: e.target.value })} autoFocus title="Shares" /></td>
                  <td><input type="number" step="0.0001" min="0.0001" className="input input-bordered input-xs w-full rounded-lg bg-base-200/50" value={editState.cost_basis} onChange={e => setEditState({ ...editState, cost_basis: e.target.value })} title="Avg cost per share" /></td>
                  <td className="text-xs font-medium">{h.current_price != null ? fmt$(h.current_price) : <span className="opacity-30">—</span>}</td>
                  <td className={`text-xs font-semibold ${gainCls(h.day_change_pct)}`}>{fmtPct(h.day_change_pct)}</td>
                  <td className="text-xs">{fmt$(h.market_value)}</td>
                  <td>
                    <div className={`text-xs font-semibold ${gainCls(h.unrealized_gain_loss)}`}>{fmt$(h.unrealized_gain_loss)}</div>
                    {h.unrealized_gain_loss_pct != null && <div className={`text-xs opacity-70 ${gainCls(h.unrealized_gain_loss_pct)}`}>{fmtPct(h.unrealized_gain_loss_pct)}</div>}
                  </td>
                  <td className={`text-xs font-semibold ${gainCls(h.realized_gain_loss)}`}>{fmt$(h.realized_gain_loss)}</td>
                  <td className={`text-xs font-semibold ${gainCls(h.total_return_pct)}`}>{fmtPct(h.total_return_pct)}</td>
                  <td className={`text-xs font-semibold ${gainCls(h.annualized_return_pct)}`}>{fmtPct(h.annualized_return_pct)}</td>
                  <td className="text-xs text-base-content/50">{h.weight_pct != null ? `${h.weight_pct}%` : '—'}</td>
                  <td>
                    <div className="flex gap-1">
                      <button onClick={onSaveEdit} disabled={savingEdit} className="btn btn-ghost btn-xs text-success rounded-lg" title="Save">
                        {savingEdit ? <Loader2 className="w-3 h-3 animate-spin" /> : <Check className="w-3 h-3" />}
                      </button>
                      <button onClick={onCancelEdit} className="btn btn-ghost btn-xs text-error rounded-lg" title="Cancel"><XCircle className="w-3 h-3" /></button>
                    </div>
                  </td>
                </tr>
              ) : (
                <React.Fragment key={h.id}>
                  <tr className={`group ${selectedIds.has(h.id) ? 'bg-primary/5' : ''} ${txnTicker === h.ticker ? 'bg-base-200/30' : ''}`}>
                    <td className="pl-3"><input type="checkbox" className="checkbox checkbox-sm checkbox-primary rounded-md" checked={selectedIds.has(h.id)} onChange={() => onToggleSelect(h.id)} /></td>
                    <td>
                      <div className="flex items-center gap-1">
                        <span className="font-bold text-primary text-sm truncate">{h.ticker}</span>
                        {h.transaction_count > 0 && <span className="badge badge-xs badge-ghost opacity-60">{h.transaction_count}</span>}
                      </div>
                    </td>
                    <td><span className={`badge badge-xs ${ASSET_TYPE_COLORS[h.asset_type] || 'badge-ghost'}`}>{ASSET_TYPE_LABELS[h.asset_type] || h.asset_type}</span></td>
                    <td className="text-xs">{fmtShares(h.shares)}</td>
                    <td className="text-xs">{fmt$(h.cost_basis)}</td>
                    <td className="text-xs font-medium">{h.current_price != null ? fmt$(h.current_price) : <span className="opacity-30">—</span>}</td>
                    <td className={`text-xs font-semibold ${gainCls(h.day_change_pct)}`}>{fmtPct(h.day_change_pct)}</td>
                    <td className="text-xs">{fmt$(h.market_value)}</td>
                    <td>
                      <div className={`text-xs font-semibold ${gainCls(h.unrealized_gain_loss)}`}>{fmt$(h.unrealized_gain_loss)}</div>
                      {h.unrealized_gain_loss_pct != null && <div className={`text-xs opacity-70 ${gainCls(h.unrealized_gain_loss_pct)}`}>{fmtPct(h.unrealized_gain_loss_pct)}</div>}
                    </td>
                    <td className={`text-xs font-semibold ${gainCls(h.realized_gain_loss)}`}>{fmt$(h.realized_gain_loss)}</td>
                    <td className={`text-xs font-semibold ${gainCls(h.total_return_pct)}`}>{fmtPct(h.total_return_pct)}</td>
                    <td className={`text-xs font-semibold ${gainCls(h.annualized_return_pct)}`}>{fmtPct(h.annualized_return_pct)}</td>
                    <td className="text-xs text-base-content/50">{h.weight_pct != null ? `${h.weight_pct}%` : '—'}</td>
                    <td>
                      <div className="flex items-center gap-0.5 opacity-0 group-hover:opacity-100 transition-opacity">
                        {/* History */}
                        <button onClick={() => onToggleTxnPanel(h.ticker)} className={`btn btn-ghost btn-xs rounded ${txnTicker === h.ticker ? 'text-primary' : ''}`} title="Transactions"><History className="w-3 h-3" /></button>
                        {/* Research */}
                        <button onClick={() => navigate(`/dashboard?ticker=${h.ticker}`)} className="btn btn-ghost btn-xs text-primary rounded" title="Research"><ExternalLink className="w-3 h-3" /></button>
                        {/* AI Analysis */}
                        <button
                          onClick={() => { setAnalysisTicker(t => t === h.ticker ? null : h.ticker); setAgentHolding(null); }}
                          className={`btn btn-ghost btn-xs rounded ${analysisTicker === h.ticker ? 'text-secondary' : ''}`}
                          title="AI Deep Analysis"
                        ><Brain className="w-3 h-3" /></button>
                        {/* Create Agent */}
                        <button
                          onClick={() => { setAgentHolding(a => a?.id === h.id ? null : h); setAnalysisTicker(null); }}
                          className={`btn btn-ghost btn-xs rounded ${agentHolding?.id === h.id ? 'text-accent' : ''}`}
                          title="Create Agent"
                        ><Bot className="w-3 h-3" /></button>
                        {/* Edit */}
                        <button onClick={() => onStartEdit(h)} className="btn btn-ghost btn-xs text-info rounded" title="Edit"><Pencil className="w-3 h-3" /></button>
                        {/* Delete — inline confirmation */}
                        {confirmDeleteId === h.id ? (
                          <span className="flex items-center gap-0.5">
                            <span className="text-[10px] text-error font-semibold px-1">Del?</span>
                            <button
                              onClick={() => { setConfirmDeleteId(null); onDelete(h.id, h.ticker); }}
                              disabled={deletingId === h.id}
                              className="btn btn-ghost btn-xs text-success rounded"
                              title="Confirm delete"
                            >{deletingId === h.id ? <Loader2 className="w-3 h-3 animate-spin" /> : <Check className="w-3 h-3" />}</button>
                            <button onClick={() => setConfirmDeleteId(null)} className="btn btn-ghost btn-xs text-base-content/40 rounded" title="Cancel"><XCircle className="w-3 h-3" /></button>
                          </span>
                        ) : (
                          <button onClick={() => setConfirmDeleteId(h.id)} disabled={deletingId === h.id} className="btn btn-ghost btn-xs text-error rounded" title="Delete">
                            {deletingId === h.id ? <Loader2 className="w-3 h-3 animate-spin" /> : <Trash2 className="w-3 h-3" />}
                          </button>
                        )}
                      </div>
                    </td>
                  </tr>
                  {txnTicker === h.ticker && (
                    <tr key={`${h.id}-txn`}><td colSpan={14} className="p-0">
                      <TransactionPanel ticker={h.ticker} onClose={onCloseTxnPanel} onRefresh={onRefresh} />
                    </td></tr>
                  )}
                  {analysisTicker === h.ticker && (
                    <tr key={`${h.id}-analysis`}><td colSpan={14} className="p-0">
                      <HoldingAnalysisPanel holding={h} onClose={() => setAnalysisTicker(null)} />
                    </td></tr>
                  )}
                  {agentHolding?.id === h.id && (
                    <tr key={`${h.id}-agent`}><td colSpan={14} className="p-0">
                      <CreateAgentPanel holding={h} onClose={() => setAgentHolding(null)} onCreated={() => setAgentHolding(null)} />
                    </td></tr>
                  )}
                </React.Fragment>
              ))}
                </React.Fragment>
                );
              })}
              {flatCapped && (
                <tr>
                  <td colSpan={14} className="text-center py-3 bg-base-200/20">
                    <button onClick={() => setShowAllFlat(true)}
                      className="inline-flex items-center gap-1.5 rounded-lg border border-white/[0.06] bg-base-200/50 px-3 py-1.5 text-xs font-medium text-base-content/65 transition-colors hover:text-base-content hover:bg-base-200/80">
                      <ChevronDown className="w-3.5 h-3.5" />
                      Show all {holdings.length} positions ({holdings.length - (renderCap ?? 0)} more)
                    </button>
                  </td>
                </tr>
              )}
            </tbody>
          </table>
        </div>
        {holdings.length === 0 && <p className="text-center py-6 text-sm text-base-content/40">No positions match the filter.</p>}
      </div>
    </div>
  );
}

// ─── Dividends Tab ────────────────────────────────────────────────────────────

// ─── Enriched-tab helpers ─────────────────────────────────────────────────────

function GrpTh({ label, span }: { label: string; span: number }) {
  return (
    <th colSpan={span} className="text-center text-[10px] font-bold uppercase tracking-widest text-base-content/40 py-1.5 px-0 border-b border-base-300/40 bg-base-200/95 backdrop-blur-sm">
      {label}
    </th>
  );
}

function SubTh({ label, k, cur, dir, onSort, align = 'right' }: {
  label: string; k: SortKey; cur: SortKey; dir: SortDir;
  onSort: (k: SortKey) => void; align?: 'left' | 'right';
}) {
  const active = cur === k;
  return (
    <th
      className={`cursor-pointer select-none whitespace-nowrap text-[11px] font-semibold py-2 px-3 hover:bg-base-300/30 bg-base-200/95 backdrop-blur-sm border-b border-base-300/40 text-base-content/50 ${align === 'right' ? 'text-right' : 'text-left'}`}
      onClick={() => onSort(k)}
    >
      <span className={`inline-flex items-center gap-0.5 ${align === 'right' ? 'flex-row-reverse' : ''}`}>
        {label}
        {active ? (dir === 'asc' ? <ChevronUp className="w-3 h-3 text-primary" /> : <ChevronDown className="w-3 h-3 text-primary" />) : null}
      </span>
    </th>
  );
}

function MiniBar({ pct, color = 'bg-primary' }: { pct: number; color?: string }) {
  return (
    <div className="mt-1 h-0.5 w-full bg-base-300/50 rounded-full overflow-hidden">
      <div className={`h-full ${color} rounded-full`} style={{ width: `${Math.min(100, Math.max(0, pct))}%` }} />
    </div>
  );
}

const peColor = (v: number | null) => v == null ? 'text-base-content/30' : v < 15 ? 'text-success' : v < 30 ? 'text-base-content' : v < 45 ? 'text-warning' : 'text-error';
const growthColor = (v: number | null) => v == null ? 'text-base-content/30' : v > 20 ? 'text-success font-bold' : v > 0 ? 'text-success' : 'text-error';
const marginColor = (v: number | null) => v == null ? 'text-base-content/30' : v > 25 ? 'text-success' : v > 10 ? 'text-base-content' : v >= 0 ? 'text-warning' : 'text-error';
const roeColor = (v: number | null) => v == null ? 'text-base-content/30' : v > 20 ? 'text-success font-bold' : v > 10 ? 'text-success' : v >= 0 ? 'text-base-content' : 'text-error';
const deColor = (v: number | null) => v == null ? 'text-base-content/30' : v < 50 ? 'text-success' : v < 150 ? 'text-base-content' : v < 300 ? 'text-warning' : 'text-error';
const yieldColor = (v: number | null) => v == null ? 'text-base-content/30' : v > 4 ? 'text-success font-bold' : v > 2 ? 'text-success' : v > 0 ? 'text-base-content' : 'text-base-content/30';
const upsideColor = (v: number | null) => v == null ? 'text-base-content/30' : v > 20 ? 'text-success font-bold' : v > 5 ? 'text-success' : v >= 0 ? 'text-warning' : 'text-error';
const betaColor = (v: number | null) => v == null ? 'text-base-content/30' : v < 0.8 ? 'text-success' : v < 1.3 ? 'text-base-content' : 'text-warning';
const payoutColor = (v: number | null) => v == null ? 'text-base-content/30' : v < 40 ? 'text-success' : v < 70 ? 'text-base-content' : 'text-error';

const REC_PILL: Record<string, string> = {
  'strong buy': 'bg-success/15 text-success',
  'buy': 'bg-success/10 text-success',
  'hold': 'bg-warning/15 text-warning',
  'underperform': 'bg-error/10 text-error',
  'sell': 'bg-error/15 text-error',
};

// ─── Dividends Tab ────────────────────────────────────────────────────────────

interface EnrichTabFilterProps {
  assetClassFilter: AssetClass; onAssetClassFilter: (v: AssetClass) => void;
  sectorFilter: string; onSectorFilter: (v: string) => void;
  sectorsPresent: string[]; hasSectorData: boolean; sectorsLoading?: boolean;
  groupBySector: boolean; onGroupBySector: (v: boolean) => void;
  tickerSearch: string; onTickerSearch: (v: string) => void;
  sectorMap: Record<string, SectorMeta>;
}

function useEnrichFiltered<T extends { ticker: string; sector?: string }>(
  data: T[],
  sectorFilter: string,
  tickerSearch: string,
  assetClassFilter: AssetClass = 'ALL',
  sectorMap: Record<string, SectorMeta> = {},
) {
  return useMemo(() => data.filter(d => {
    const sector = d.sector || sectorMap[d.ticker]?.sector || '';
    const at = sectorMap[d.ticker]?.asset_type || 'STOCK';
    return (
      (sectorFilter === 'ALL' || (sector || 'Unknown') === sectorFilter) &&
      (!tickerSearch || d.ticker.toLowerCase().includes(tickerSearch.toLowerCase())) &&
      (assetClassFilter === 'ALL' ||
       (assetClassFilter === 'STOCKS'  && at === 'STOCK') ||
       (assetClassFilter === 'FUNDS'   && ['ETF', 'MUTUAL_FUND'].includes(at)) ||
       (assetClassFilter === 'OPTIONS' && at === 'OPTION'))
    );
  }), [data, sectorFilter, tickerSearch, assetClassFilter, sectorMap]);
}

function useEnrichGrouped<T extends { ticker: string; sector?: string }>(
  sorted: T[],
  groupBySector: boolean,
  sectorMap: Record<string, SectorMeta> = {},
  groupKeyFn?: (d: T) => string,
) {
  return useMemo((): { sectorLabel: string | null; rows: T[] }[] => {
    if (!groupBySector) return [{ sectorLabel: null, rows: sorted }];
    const map = new Map<string, T[]>();
    for (const d of sorted) {
      const s = groupKeyFn
        ? groupKeyFn(d)
        : (d.sector || sectorMap[d.ticker]?.sector || 'Unknown');
      if (!map.has(s)) map.set(s, []);
      map.get(s)!.push(d);
    }
    return Array.from(map.entries()).sort(([a], [b]) => a.localeCompare(b)).map(([s, rows]) => ({ sectorLabel: s, rows }));
  }, [sorted, groupBySector, sectorMap, groupKeyFn]);
}

function SectorGroupRow({ label, count, colSpan, collapsed, onToggle }: {
  label: string; count: number; colSpan: number;
  collapsed: boolean; onToggle: () => void;
}) {
  return (
    <tr onClick={onToggle}
      className="cursor-pointer select-none bg-secondary/5 hover:bg-secondary/10 transition-colors border-t-2 border-secondary/20">
      <td colSpan={colSpan} className="px-3 py-1.5">
        <div className="flex items-center gap-2">
          <div className={`transition-transform duration-150 ${collapsed ? '-rotate-90' : ''}`}>
            <ChevronDown className="w-3.5 h-3.5 text-secondary/50" />
          </div>
          <span className="text-[11px] font-bold uppercase tracking-widest text-secondary/70">{label}</span>
          <span className="text-[10px] text-base-content/30">{count} position{count !== 1 ? 's' : ''}</span>
        </div>
      </td>
    </tr>
  );
}

function DividendsTab({ data, loading, onRefresh, assetClassFilter, onAssetClassFilter, sectorFilter, onSectorFilter, sectorsPresent, hasSectorData, sectorsLoading, groupBySector, onGroupBySector, tickerSearch, onTickerSearch, sectorMap }: { data: DividendData[]; loading: boolean; onRefresh: () => void } & EnrichTabFilterProps) {
  const isFundsView = assetClassFilter === 'FUNDS';
  const filtered = useEnrichFiltered(data, sectorFilter, tickerSearch, assetClassFilter, sectorMap);
  const { sorted, sortKey, sortDir, handleSort } = useSortedData<DividendData>(filtered, 'annual_income', 'desc');
  const groupedRows = useEnrichGrouped(sorted, groupBySector, sectorMap);
  const totalIncome = filtered.reduce((s, d) => s + (d.annual_income || 0), 0);
  const divPayers = filtered.filter(d => d.is_dividend_payer).length;
  const yieldPayers = filtered.filter(d => d.dividend_yield_pct);
  const avgYield = yieldPayers.length > 0 ? yieldPayers.reduce((s, d) => s + (d.dividend_yield_pct || 0), 0) / yieldPayers.length : 0;

  const [localCollapsed, setLocalCollapsed] = useState<Set<string>>(new Set());
  // Pre-collapse all sector groups when group mode activates
  useEffect(() => {
    if (groupBySector) {
      setLocalCollapsed(new Set(groupedRows.map(g => g.sectorLabel).filter(Boolean) as string[]));
    } else {
      setLocalCollapsed(new Set());
    }
  }, [groupBySector]); // eslint-disable-line react-hooks/exhaustive-deps
  const toggleLocal = (s: string) => setLocalCollapsed(prev => {
    const next = new Set(prev); next.has(s) ? next.delete(s) : next.add(s); return next;
  });

  return (
    <div className="space-y-3">
      <SectorFilterBar assetClassFilter={assetClassFilter} onAssetClassFilter={onAssetClassFilter} sectorFilter={sectorFilter} onSectorFilter={onSectorFilter} sectorsPresent={sectorsPresent} hasSectorData={hasSectorData} sectorsLoading={sectorsLoading} groupBySector={groupBySector} onGroupBySector={onGroupBySector} tickerSearch={tickerSearch} onTickerSearch={onTickerSearch} sectorMap={sectorMap} />
      <div className="grid grid-cols-3 gap-2.5">
        <div className="glass-card p-4">
          <div className="text-[11px] text-base-content/40 uppercase tracking-wider mb-1.5">Est. Annual Income</div>
          <div className="text-2xl font-black text-success">{fmt$(totalIncome, 0)}</div>
        </div>
        <div className="glass-card p-4">
          <div className="text-[11px] text-base-content/40 uppercase tracking-wider mb-1.5">Dividend Payers</div>
          <div className="text-2xl font-black">{divPayers} <span className="text-sm font-normal text-base-content/30">/ {data.length}</span></div>
        </div>
        <div className="glass-card p-4">
          <div className="text-[11px] text-base-content/40 uppercase tracking-wider mb-1.5">Avg Yield (payers)</div>
          <div className={`text-2xl font-black ${yieldColor(avgYield)}`}>{avgYield > 0 ? `${avgYield.toFixed(2)}%` : '—'}</div>
        </div>
      </div>
      <EnrichBanner loading={loading} onRefresh={onRefresh} lastLabel="up to 24h old" />
      {loading && data.length === 0 ? (
        <div className="glass-card p-10 flex items-center justify-center gap-3">
          <Loader2 className="w-5 h-5 animate-spin text-primary/50" />
          <span className="text-sm text-base-content/50">Loading dividend data…</span>
        </div>
      ) : (
        <div className="glass-card overflow-hidden">
          <div className="overflow-x-auto overflow-y-auto max-h-[72vh]">
            {isFundsView ? (
            /* ── Lean FUND dividend view: only the columns ETFs actually populate ── */
            <table className="w-full border-collapse" style={{ tableLayout: 'fixed', minWidth: '660px' }}>
              <colgroup>
                <col style={{ width: '150px' }} /> {/* fund */}
                <col style={{ width: '120px' }} /> {/* investment */}
                <col style={{ width: '110px' }} /> {/* dist yield */}
                <col style={{ width: '130px' }} /> {/* annual income */}
                <col style={{ width: '110px' }} /> {/* % of income */}
              </colgroup>
              <thead className="sticky top-0 z-20">
                <tr>
                  <th className="px-3 py-2.5 text-left text-[11px] font-semibold uppercase tracking-wider text-base-content/45 bg-base-200/95 backdrop-blur-sm border-b border-base-300/40">Fund</th>
                  <SubTh label="Investment" k="market_value" cur={sortKey} dir={sortDir} onSort={handleSort} align="right" />
                  <SubTh label="Dist. Yield" k="dividend_yield_pct" cur={sortKey} dir={sortDir} onSort={handleSort} align="right" />
                  <SubTh label="Annual Income" k="annual_income" cur={sortKey} dir={sortDir} onSort={handleSort} align="right" />
                  <th className="px-3 py-2.5 text-right text-[11px] font-semibold text-base-content/45 bg-base-200/95 backdrop-blur-sm border-b border-base-300/40">% of Income</th>
                </tr>
              </thead>
              <tbody>
                {groupedRows.map(({ sectorLabel, rows }) => {
                  const collapsed = sectorLabel != null && localCollapsed.has(sectorLabel);
                  return (
                    <React.Fragment key={sectorLabel ?? '__all'}>
                      {sectorLabel && <SectorGroupRow label={sectorLabel} count={rows.length} colSpan={5} collapsed={collapsed} onToggle={() => toggleLocal(sectorLabel)} />}
                      {!collapsed && rows.map((d, i) => {
                        const incomeShare = totalIncome > 0 && d.annual_income > 0 ? (d.annual_income / totalIncome) * 100 : null;
                        return (
                        <tr key={d.ticker} className={`border-t border-base-300/30 hover:bg-base-200/20 transition-colors ${i % 2 === 0 ? '' : 'bg-base-300/5'} ${d.error ? 'opacity-40' : ''}`}>
                          <td className="px-3 py-3">
                            <div className="font-bold text-primary text-sm">{d.ticker}</div>
                            {d.company_name && <div className="text-[10px] text-base-content/30 truncate max-w-[130px]" title={d.company_name}>{d.company_name}</div>}
                            {!d.is_dividend_payer && <span className="text-[10px] text-base-content/25">no distribution</span>}
                          </td>
                          <td className="px-3 py-3 text-right text-sm text-base-content/70">
                            {d.market_value != null ? fmt$(d.market_value, 0) : <span className="text-base-content/30">—</span>}
                          </td>
                          <td className="px-3 py-3 text-right">
                            {d.dividend_yield_pct != null
                              ? <span className={`text-sm font-bold ${yieldColor(d.dividend_yield_pct)}`}>{d.dividend_yield_pct.toFixed(2)}%</span>
                              : <span className="text-base-content/20 text-sm">—</span>}
                          </td>
                          <td className="px-3 py-3 text-right">
                            {d.annual_income > 0
                              ? <span className="text-sm font-bold text-success">{fmt$(d.annual_income, 0)}</span>
                              : <span className="text-base-content/20 text-sm">—</span>}
                          </td>
                          <td className="px-3 py-3 text-right">
                            {incomeShare != null
                              ? <div className="flex items-center justify-end gap-1.5">
                                  <span className="text-sm text-base-content/60">{incomeShare.toFixed(1)}%</span>
                                  <div className="w-10"><MiniBar pct={Math.min(100, incomeShare)} color="bg-success/70" /></div>
                                </div>
                              : <span className="text-base-content/20 text-sm">—</span>}
                          </td>
                        </tr>
                        );
                      })}
                    </React.Fragment>
                  );
                })}
              </tbody>
            </table>
            ) : (
            <table className="w-full border-collapse" style={{ tableLayout: 'fixed', minWidth: '860px' }}>
              <colgroup>
                <col style={{ width: '90px' }} />
                <col style={{ width: '90px' }} />  {/* Investment (was Shares — wider for $ values) */}
                <col style={{ width: '88px' }} />
                <col style={{ width: '95px' }} />
                <col style={{ width: '90px' }} />
                <col style={{ width: '108px' }} />
                <col style={{ width: '110px' }} />
                <col style={{ width: '120px' }} />
                <col style={{ width: '90px' }} />
              </colgroup>
              <thead className="sticky top-0 z-20">
                <tr>
                  <th className="py-2 px-3 text-left bg-base-200/95 backdrop-blur-sm" rowSpan={2} />
                  <GrpTh label="Position" span={2} />
                  <GrpTh label="Dividend" span={3} />
                  <GrpTh label="Income" span={2} />
                  <GrpTh label="History" span={1} />
                </tr>
                <tr>
                  <SubTh label="Investment" k="market_value" cur={sortKey} dir={sortDir} onSort={handleSort} align="right" />
                  <SubTh label="Yield" k="dividend_yield_pct" cur={sortKey} dir={sortDir} onSort={handleSort} align="right" />
                  <SubTh label="Annual Rate" k="annual_dividend_rate" cur={sortKey} dir={sortDir} onSort={handleSort} align="right" />
                  <SubTh label="Per Share" k="last_dividend_per_share" cur={sortKey} dir={sortDir} onSort={handleSort} align="right" />
                  <SubTh label="Ex-Date" k="ex_dividend_date" cur={sortKey} dir={sortDir} onSort={handleSort} align="right" />
                  <SubTh label="Annual Income" k="annual_income" cur={sortKey} dir={sortDir} onSort={handleSort} align="right" />
                  <SubTh label="Payout Ratio" k="payout_ratio_pct" cur={sortKey} dir={sortDir} onSort={handleSort} align="right" />
                  <SubTh label="5yr Avg Yield" k="five_yr_avg_yield_pct" cur={sortKey} dir={sortDir} onSort={handleSort} align="right" />
                </tr>
              </thead>
              <tbody>
                {groupedRows.map(({ sectorLabel, rows }) => {
                  const collapsed = sectorLabel != null && localCollapsed.has(sectorLabel);
                  return (
                    <React.Fragment key={sectorLabel ?? '__all'}>
                      {sectorLabel && <SectorGroupRow label={sectorLabel} count={rows.length} colSpan={9} collapsed={collapsed} onToggle={() => toggleLocal(sectorLabel)} />}
                      {!collapsed && rows.map((d, i) => (
                        <tr key={d.ticker} className={`border-t border-base-300/30 hover:bg-base-200/20 transition-colors ${i % 2 === 0 ? '' : 'bg-base-300/5'} ${d.error ? 'opacity-40' : ''}`}>
                          <td className="px-3 py-3">
                            <div className="font-bold text-primary text-sm">{d.ticker}</div>
                            {d.company_name && <div className="text-[10px] text-base-content/30 truncate max-w-[80px]">{d.company_name}</div>}
                            {!d.is_dividend_payer && <span className="text-[10px] text-base-content/25">no div</span>}
                          </td>
                          <td className="px-3 py-3 text-right text-sm text-base-content/70">
                            {d.market_value != null ? fmt$(d.market_value, 0) : <span className="text-base-content/30">—</span>}
                          </td>
                          <td className="px-3 py-3 text-right">
                            {d.dividend_yield_pct != null
                              ? <span className={`text-sm font-bold ${yieldColor(d.dividend_yield_pct)}`}>{d.dividend_yield_pct.toFixed(2)}%</span>
                              : <span className="text-base-content/20 text-sm">—</span>}
                          </td>
                          <td className="px-3 py-3 text-right text-sm text-base-content/70">{fmt$(d.annual_dividend_rate)}</td>
                          <td className="px-3 py-3 text-right text-sm text-base-content/50">{fmt$(d.last_dividend_per_share, 4)}</td>
                          <td className="px-3 py-3 text-right text-sm text-base-content/60">{d.ex_dividend_date || <span className="text-base-content/20">—</span>}</td>
                          <td className="px-3 py-3 text-right">
                            {d.annual_income > 0
                              ? <span className="text-sm font-bold text-success">{fmt$(d.annual_income, 0)}</span>
                              : <span className="text-base-content/20 text-sm">—</span>}
                          </td>
                          <td className="px-3 py-3">
                            {d.payout_ratio_pct != null ? (
                              <div>
                                <div className={`text-sm text-right ${payoutColor(d.payout_ratio_pct)}`}>{d.payout_ratio_pct.toFixed(1)}%</div>
                                <MiniBar pct={Math.min(100, d.payout_ratio_pct)} color={d.payout_ratio_pct < 70 ? 'bg-success' : 'bg-error'} />
                              </div>
                            ) : <span className="text-base-content/20 text-sm float-right">—</span>}
                          </td>
                          <td className="px-3 py-3 text-right text-sm text-base-content/50">
                            {d.five_yr_avg_yield_pct != null ? `${d.five_yr_avg_yield_pct.toFixed(2)}%` : <span className="text-base-content/20">—</span>}
                          </td>
                        </tr>
                      ))}
                    </React.Fragment>
                  );
                })}
              </tbody>
            </table>
            )}
          </div>
        </div>
      )}
    </div>
  );
}

// ─── Fundamentals Tab ─────────────────────────────────────────────────────────

const expenseColor = (v: number | null) => v == null ? 'text-base-content/30' : v < 0.1 ? 'text-success font-bold' : v < 0.5 ? 'text-success' : v < 1 ? 'text-base-content' : 'text-warning';
const returnColor = (v: number | null) => v == null ? 'text-base-content/30' : v > 15 ? 'text-success font-bold' : v > 0 ? 'text-success' : 'text-error';

function StarRating({ n }: { n: number | null | undefined }) {
  if (!n) return <span className="text-base-content/20 text-sm">—</span>;
  return (
    <span className="text-warning text-sm tracking-tight">
      {'★'.repeat(Math.min(5, Math.max(1, Math.round(n))))}
      {'☆'.repeat(Math.max(0, 5 - Math.min(5, Math.max(1, Math.round(n)))))}
    </span>
  );
}

function FundamentalsTab({ data, loading, onRefresh, assetClassFilter, onAssetClassFilter, sectorFilter, onSectorFilter, sectorsPresent, hasSectorData, sectorsLoading, groupBySector, onGroupBySector, tickerSearch, onTickerSearch, sectorMap }: { data: FundamentalData[]; loading: boolean; onRefresh: () => void } & EnrichTabFilterProps) {
  const filtered = useEnrichFiltered(data, sectorFilter, tickerSearch, assetClassFilter, sectorMap);
  const isFundsView = assetClassFilter === 'FUNDS';
  const { sorted, sortKey, sortDir, handleSort } = useSortedData<FundamentalData>(
    filtered, isFundsView ? 'aum_b' : 'trailing_pe', isFundsView ? 'desc' : 'asc'
  );
  // For funds, group by Morningstar-style category (e.g. "Large Blend", "Foreign Large Blend")
  // instead of the generic "ETF" sector label — much more informative for funds.
  const fundGroupKey = React.useCallback(
    (d: FundamentalData) => (d.fund_category && d.fund_category.trim()) || sectorMap[d.ticker]?.sector || 'Uncategorized',
    [sectorMap],
  );
  const groupedRows = useEnrichGrouped(
    sorted, groupBySector, sectorMap, isFundsView ? fundGroupKey : undefined
  );

  const [localCollapsed, setLocalCollapsed] = useState<Set<string>>(new Set());
  useEffect(() => {
    if (groupBySector) {
      setLocalCollapsed(new Set(groupedRows.map(g => g.sectorLabel).filter(Boolean) as string[]));
    } else {
      setLocalCollapsed(new Set());
    }
  }, [groupBySector]); // eslint-disable-line react-hooks/exhaustive-deps
  const toggleLocal = (s: string) => setLocalCollapsed(prev => {
    const next = new Set(prev); next.has(s) ? next.delete(s) : next.add(s); return next;
  });

  return (
    <div className="space-y-3">
      <SectorFilterBar assetClassFilter={assetClassFilter} onAssetClassFilter={onAssetClassFilter} sectorFilter={sectorFilter} onSectorFilter={onSectorFilter} sectorsPresent={sectorsPresent} hasSectorData={hasSectorData} sectorsLoading={sectorsLoading} groupBySector={groupBySector} onGroupBySector={onGroupBySector} tickerSearch={tickerSearch} onTickerSearch={onTickerSearch} sectorMap={sectorMap} />
      <EnrichBanner loading={loading} onRefresh={onRefresh} lastLabel="up to 24h old" />
      {loading && data.length === 0 ? (
        <div className="glass-card p-10 flex items-center justify-center gap-3">
          <Loader2 className="w-5 h-5 animate-spin text-primary/50" />
          <span className="text-sm text-base-content/50">Loading fundamental data…</span>
        </div>
      ) : isFundsView ? (
        /* ── FUND / ETF view ─────────────────────────────────────────────── */
        <div className="glass-card overflow-hidden">
          <div className="overflow-x-auto overflow-y-auto max-h-[72vh]">
            <table className="w-full border-collapse" style={{ tableLayout: 'fixed', minWidth: '1040px' }}>
              <colgroup>
                <col style={{ width: '110px' }} /> {/* ticker/name */}
                <col style={{ width: '140px' }} /> {/* category/style */}
                <col style={{ width: '110px' }} /> {/* fund family */}
                <col style={{ width: '88px' }} />  {/* AUM */}
                <col style={{ width: '76px' }} />  {/* expense */}
                <col style={{ width: '76px' }} />  {/* yield */}
                <col style={{ width: '72px' }} />  {/* YTD */}
                <col style={{ width: '72px' }} />  {/* 3yr */}
                <col style={{ width: '72px' }} />  {/* 5yr */}
                <col style={{ width: '72px' }} />  {/* turnover */}
                <col style={{ width: '80px' }} />  {/* M* rating */}
              </colgroup>
              <thead className="sticky top-0 z-20">
                <tr>
                  <th rowSpan={2} className="px-3 py-2 text-left bg-base-200/95 backdrop-blur-sm border-b border-base-300/40" />
                  <GrpTh label="Profile" span={3} />
                  <GrpTh label="Cost & Income" span={2} />
                  <GrpTh label="Historical Returns" span={3} />
                  <GrpTh label="Quality" span={2} />
                </tr>
                <tr>
                  <SubTh label="Category / Style" k="fund_category" cur={sortKey} dir={sortDir} onSort={handleSort} align="left" />
                  <SubTh label="Fund Family" k="fund_family" cur={sortKey} dir={sortDir} onSort={handleSort} align="left" />
                  <SubTh label="AUM" k="aum_b" cur={sortKey} dir={sortDir} onSort={handleSort} />
                  <SubTh label="Expense" k="net_expense_ratio_pct" cur={sortKey} dir={sortDir} onSort={handleSort} />
                  <SubTh label="Yield" k="fund_yield_pct" cur={sortKey} dir={sortDir} onSort={handleSort} />
                  <SubTh label="YTD" k="ytd_return_pct" cur={sortKey} dir={sortDir} onSort={handleSort} />
                  <SubTh label="3yr Ann" k="three_yr_return_pct" cur={sortKey} dir={sortDir} onSort={handleSort} />
                  <SubTh label="5yr Ann" k="five_yr_return_pct" cur={sortKey} dir={sortDir} onSort={handleSort} />
                  <SubTh label="Turnover" k="fund_turnover_pct" cur={sortKey} dir={sortDir} onSort={handleSort} />
                  <SubTh label="M* Rating" k="morningstar_overall_rating" cur={sortKey} dir={sortDir} onSort={handleSort} />
                </tr>
              </thead>
              <tbody>
                {groupedRows.map(({ sectorLabel, rows }) => {
                  const collapsed = sectorLabel != null && localCollapsed.has(sectorLabel);
                  return (
                    <React.Fragment key={sectorLabel ?? '__all'}>
                      {sectorLabel && <SectorGroupRow label={sectorLabel} count={rows.length} colSpan={11} collapsed={collapsed} onToggle={() => toggleLocal(sectorLabel)} />}
                      {!collapsed && rows.map((d, i) => (
                        <tr key={d.ticker} className={`border-t border-base-300/30 hover:bg-base-200/20 transition-colors ${i % 2 === 0 ? '' : 'bg-base-300/5'} ${d.error ? 'opacity-40' : ''}`}>
                          <td className="px-3 py-3">
                            <div className="font-bold text-primary text-sm leading-tight">{d.ticker}</div>
                            {d.company_name && <div className="text-[10px] text-base-content/30 truncate max-w-[100px]" title={d.company_name}>{d.company_name}</div>}
                          </td>
                          <td className="px-3 py-3">
                            {d.fund_category
                              ? <span className="text-[11px] text-base-content/70 leading-tight">{d.fund_category}</span>
                              : <span className="text-base-content/20 text-sm">—</span>}
                          </td>
                          <td className="px-3 py-3">
                            {d.fund_family
                              ? <span className="text-[11px] text-base-content/60 truncate max-w-[100px]" title={d.fund_family}>{d.fund_family}</span>
                              : <span className="text-base-content/20 text-sm">—</span>}
                          </td>
                          <td className="px-3 py-3 text-right">
                            <span className="text-sm font-semibold text-base-content/80">{fmtB(d.aum_b)}</span>
                          </td>
                          <td className="px-3 py-3 text-right">
                            <span className={`text-sm font-semibold ${expenseColor(d.net_expense_ratio_pct ?? null)}`}>
                              {d.net_expense_ratio_pct != null ? `${d.net_expense_ratio_pct.toFixed(2)}%` : <span className="text-base-content/20">—</span>}
                            </span>
                          </td>
                          <td className="px-3 py-3 text-right">
                            <span className={`text-sm font-bold ${yieldColor(d.fund_yield_pct ?? null)}`}>
                              {d.fund_yield_pct != null ? `${d.fund_yield_pct.toFixed(2)}%` : <span className="text-base-content/20">—</span>}
                            </span>
                          </td>
                          <td className="px-3 py-3 text-right">
                            <span className={`text-sm font-semibold ${returnColor(d.ytd_return_pct ?? null)}`}>{fmtPct(d.ytd_return_pct ?? null)}</span>
                          </td>
                          <td className="px-3 py-3 text-right">
                            <span className={`text-sm font-semibold ${returnColor(d.three_yr_return_pct ?? null)}`}>{fmtPct(d.three_yr_return_pct ?? null)}</span>
                          </td>
                          <td className="px-3 py-3 text-right">
                            <span className={`text-sm font-semibold ${returnColor(d.five_yr_return_pct ?? null)}`}>{fmtPct(d.five_yr_return_pct ?? null)}</span>
                          </td>
                          <td className="px-3 py-3 text-right">
                            {d.fund_turnover_pct != null
                              ? <div>
                                  <div className="text-sm text-right text-base-content/70">{d.fund_turnover_pct.toFixed(0)}%</div>
                                  <MiniBar pct={Math.min(100, d.fund_turnover_pct)} color="bg-primary" />
                                </div>
                              : <span className="text-base-content/20 text-sm float-right">—</span>}
                          </td>
                          <td className="px-3 py-3 text-center"><StarRating n={d.morningstar_overall_rating} /></td>
                        </tr>
                      ))}
                    </React.Fragment>
                  );
                })}
              </tbody>
            </table>
          </div>
        </div>
      ) : (
        /* ── EQUITY / STOCK view ─────────────────────────────────────────── */
        <div className="glass-card overflow-hidden">
          <div className="overflow-x-auto overflow-y-auto max-h-[72vh]">
            <table className="w-full border-collapse" style={{ tableLayout: 'fixed', minWidth: '1060px' }}>
              <colgroup>
                <col style={{ width: '85px' }} />
                <col style={{ width: '68px' }} />
                <col style={{ width: '68px' }} />
                <col style={{ width: '55px' }} />
                <col style={{ width: '72px' }} />
                <col style={{ width: '72px' }} />
                <col style={{ width: '76px' }} />
                <col style={{ width: '76px' }} />
                <col style={{ width: '80px' }} />
                <col style={{ width: '80px' }} />
                <col style={{ width: '72px' }} />
                <col style={{ width: '65px' }} />
                <col style={{ width: '62px' }} />
                <col style={{ width: '82px' }} />
              </colgroup>
              <thead className="sticky top-0 z-20">
                <tr>
                  <th rowSpan={2} className="px-3 py-2 text-left bg-base-200/95 backdrop-blur-sm border-b border-base-300/40" />
                  <GrpTh label="Valuation" span={3} />
                  <GrpTh label="Earnings" span={4} />
                  <GrpTh label="Profitability" span={3} />
                  <GrpTh label="Balance Sheet" span={3} />
                </tr>
                <tr>
                  <SubTh label="P/E" k="trailing_pe" cur={sortKey} dir={sortDir} onSort={handleSort} />
                  <SubTh label="Fwd P/E" k="forward_pe" cur={sortKey} dir={sortDir} onSort={handleSort} />
                  <SubTh label="PEG" k="peg_ratio" cur={sortKey} dir={sortDir} onSort={handleSort} />
                  <SubTh label="EPS TTM" k="eps_ttm" cur={sortKey} dir={sortDir} onSort={handleSort} />
                  <SubTh label="Fwd EPS" k="eps_forward" cur={sortKey} dir={sortDir} onSort={handleSort} />
                  <SubTh label="EPS Grw" k="earnings_growth_yoy_pct" cur={sortKey} dir={sortDir} onSort={handleSort} />
                  <SubTh label="Rev Grw" k="revenue_growth_yoy_pct" cur={sortKey} dir={sortDir} onSort={handleSort} />
                  <SubTh label="Op Margin" k="operating_margin_pct" cur={sortKey} dir={sortDir} onSort={handleSort} />
                  <SubTh label="Net Margin" k="net_margin_pct" cur={sortKey} dir={sortDir} onSort={handleSort} />
                  <SubTh label="ROE" k="roe_pct" cur={sortKey} dir={sortDir} onSort={handleSort} />
                  <SubTh label="D/E" k="debt_to_equity" cur={sortKey} dir={sortDir} onSort={handleSort} />
                  <SubTh label="P/B" k="price_to_book" cur={sortKey} dir={sortDir} onSort={handleSort} />
                  <SubTh label="Mkt Cap" k="market_cap_b" cur={sortKey} dir={sortDir} onSort={handleSort} />
                </tr>
              </thead>
              <tbody>
                {groupedRows.map(({ sectorLabel, rows }) => {
                  const collapsed = sectorLabel != null && localCollapsed.has(sectorLabel);
                  return (
                    <React.Fragment key={sectorLabel ?? '__all'}>
                      {sectorLabel && <SectorGroupRow label={sectorLabel} count={rows.length} colSpan={14} collapsed={collapsed} onToggle={() => toggleLocal(sectorLabel)} />}
                      {!collapsed && rows.map((d, i) => (
                        <tr key={d.ticker} className={`border-t border-base-300/30 hover:bg-base-200/20 transition-colors ${i % 2 === 0 ? '' : 'bg-base-300/5'} ${d.error ? 'opacity-40' : ''}`}>
                          <td className="px-3 py-3.5">
                            <div className="font-bold text-primary text-sm leading-tight">{d.ticker}</div>
                            {d.industry ? (
                              <div className="text-[10px] text-base-content/30 leading-tight truncate max-w-[80px]">{d.industry}</div>
                            ) : d.quote_type && d.quote_type !== 'EQUITY' ? (
                              <div className="text-[10px] text-base-content/30 leading-tight">{d.quote_type}</div>
                            ) : null}
                          </td>
                          <td className="px-3 py-3.5 text-right">
                            <span className={`text-sm font-semibold ${peColor(d.trailing_pe)}`}>
                              {d.trailing_pe != null ? d.trailing_pe.toFixed(1) : <span className="text-base-content/20">—</span>}
                            </span>
                          </td>
                          <td className="px-3 py-3.5 text-right">
                            <span className={`text-sm ${peColor(d.forward_pe)}`}>
                              {d.forward_pe != null ? d.forward_pe.toFixed(1) : <span className="text-base-content/20">—</span>}
                            </span>
                          </td>
                          <td className="px-3 py-3.5 text-right text-sm text-base-content/60">
                            {d.peg_ratio != null ? d.peg_ratio.toFixed(2) : <span className="text-base-content/20">—</span>}
                          </td>
                          <td className="px-3 py-3.5 text-right text-sm text-base-content/70">{fmt$(d.eps_ttm)}</td>
                          <td className="px-3 py-3.5 text-right text-sm text-base-content/60">{fmt$(d.eps_forward)}</td>
                          <td className="px-3 py-3.5 text-right">
                            <span className={`text-sm ${growthColor(d.earnings_growth_yoy_pct)}`}>{fmtPct(d.earnings_growth_yoy_pct)}</span>
                          </td>
                          <td className="px-3 py-3.5 text-right">
                            <span className={`text-sm ${growthColor(d.revenue_growth_yoy_pct)}`}>{fmtPct(d.revenue_growth_yoy_pct)}</span>
                          </td>
                          <td className="px-3 py-3.5">
                            {d.operating_margin_pct != null ? (
                              <>
                                <div className={`text-sm text-right ${marginColor(d.operating_margin_pct)}`}>{d.operating_margin_pct.toFixed(1)}%</div>
                                <MiniBar pct={Math.max(0, d.operating_margin_pct)} color={d.operating_margin_pct > 15 ? 'bg-success' : d.operating_margin_pct > 0 ? 'bg-warning' : 'bg-error'} />
                              </>
                            ) : <span className="text-base-content/20 text-sm float-right">—</span>}
                          </td>
                          <td className="px-3 py-3.5">
                            {d.net_margin_pct != null ? (
                              <>
                                <div className={`text-sm text-right ${marginColor(d.net_margin_pct)}`}>{d.net_margin_pct.toFixed(1)}%</div>
                                <MiniBar pct={Math.max(0, d.net_margin_pct)} color={d.net_margin_pct > 15 ? 'bg-success' : d.net_margin_pct > 0 ? 'bg-warning' : 'bg-error'} />
                              </>
                            ) : <span className="text-base-content/20 text-sm float-right">—</span>}
                          </td>
                          <td className="px-3 py-3.5 text-right">
                            <span className={`text-sm ${roeColor(d.roe_pct)}`}>
                              {d.roe_pct != null ? `${d.roe_pct.toFixed(1)}%` : <span className="text-base-content/20">—</span>}
                            </span>
                          </td>
                          <td className="px-3 py-3.5 text-right">
                            <span className={`text-sm ${deColor(d.debt_to_equity)}`}>
                              {d.debt_to_equity != null ? d.debt_to_equity.toFixed(0) : <span className="text-base-content/20">—</span>}
                            </span>
                          </td>
                          <td className="px-3 py-3.5 text-right text-sm text-base-content/60">
                            {d.price_to_book != null ? d.price_to_book.toFixed(1) : <span className="text-base-content/20">—</span>}
                          </td>
                          <td className="px-3 py-3.5 text-right text-sm text-base-content/50">{fmtB(d.market_cap_b)}</td>
                        </tr>
                      ))}
                    </React.Fragment>
                  );
                })}
              </tbody>
            </table>
          </div>
        </div>
      )}
    </div>
  );
}

// ─── Technical Tab ────────────────────────────────────────────────────────────

const REC_LABEL: Record<string, string> = {
  'strong buy': 'Strong Buy', 'buy': 'Buy', 'hold': 'Hold',
  'underperform': 'Underperform', 'sell': 'Sell', '': '—',
};

function RangeBar({ pct }: { pct: number | null }) {
  if (pct == null) return <span className="text-xs text-base-content/20">—</span>;
  const clamped = Math.min(100, Math.max(0, pct));
  const color = clamped > 75 ? 'bg-success' : clamped > 25 ? 'bg-primary' : 'bg-warning';
  return (
    <div>
      <div className="flex items-center gap-1.5">
        <span className="text-[10px] text-base-content/30">L</span>
        <div className="flex-1 h-1.5 bg-base-300/60 rounded-full overflow-hidden">
          <div className={`h-full ${color} rounded-full`} style={{ width: `${clamped}%` }} />
        </div>
        <span className="text-[10px] text-base-content/30">H</span>
      </div>
      <div className="text-[10px] text-base-content/40 text-center mt-0.5">{pct.toFixed(0)}%</div>
    </div>
  );
}

function TechnicalTab({ data, loading, onRefresh, assetClassFilter, onAssetClassFilter, sectorFilter, onSectorFilter, sectorsPresent, hasSectorData, sectorsLoading, groupBySector, onGroupBySector, tickerSearch, onTickerSearch, sectorMap }: { data: PortfolioTechnicalData[]; loading: boolean; onRefresh: () => void } & EnrichTabFilterProps) {
  const isFundsView = assetClassFilter === 'FUNDS';
  const filtered = useEnrichFiltered(data, sectorFilter, tickerSearch, assetClassFilter, sectorMap);
  const { sorted, sortKey, sortDir, handleSort } = useSortedData<PortfolioTechnicalData>(
    filtered, isFundsView ? 'five_yr_return_pct' : 'analyst_upside_pct', 'desc'
  );
  const groupedRows = useEnrichGrouped(sorted, groupBySector, sectorMap);

  const [localCollapsed, setLocalCollapsed] = useState<Set<string>>(new Set());
  useEffect(() => {
    if (groupBySector) {
      setLocalCollapsed(new Set(groupedRows.map(g => g.sectorLabel).filter(Boolean) as string[]));
    } else {
      setLocalCollapsed(new Set());
    }
  }, [groupBySector]); // eslint-disable-line react-hooks/exhaustive-deps
  const toggleLocal = (s: string) => setLocalCollapsed(prev => {
    const next = new Set(prev); next.has(s) ? next.delete(s) : next.add(s); return next;
  });

  // Funds view: Risk + Moving Averages + 52-Week + Historical Returns (no analyst targets)
  // Stocks view: Risk + Moving Averages + 52-Week + Analyst Targets

  return (
    <div className="space-y-3">
      <SectorFilterBar assetClassFilter={assetClassFilter} onAssetClassFilter={onAssetClassFilter} sectorFilter={sectorFilter} onSectorFilter={onSectorFilter} sectorsPresent={sectorsPresent} hasSectorData={hasSectorData} sectorsLoading={sectorsLoading} groupBySector={groupBySector} onGroupBySector={onGroupBySector} tickerSearch={tickerSearch} onTickerSearch={onTickerSearch} sectorMap={sectorMap} />
      <EnrichBanner loading={loading} onRefresh={onRefresh} lastLabel="up to 1h old" />
      {loading && data.length === 0 ? (
        <div className="glass-card p-10 flex items-center justify-center gap-3">
          <Loader2 className="w-5 h-5 animate-spin text-primary/50" />
          <span className="text-sm text-base-content/50">Loading technical data…</span>
        </div>
      ) : (
        <div className="glass-card overflow-hidden">
          <div className="overflow-x-auto overflow-y-auto max-h-[72vh]">
            <table className="w-full border-collapse" style={{ tableLayout: 'fixed', minWidth: '1040px' }}>
              <colgroup>
                <col style={{ width: '85px' }} />
                <col style={{ width: '55px' }} />
                <col style={{ width: '75px' }} />
                <col style={{ width: '75px' }} />
                <col style={{ width: '70px' }} />
                <col style={{ width: '70px' }} />
                <col style={{ width: '75px' }} />
                <col style={{ width: '75px' }} />
                <col style={{ width: '72px' }} />
                <col style={{ width: '100px' }} />
                {isFundsView ? (
                  <>
                    <col style={{ width: '72px' }} /> {/* YTD */}
                    <col style={{ width: '72px' }} /> {/* 3yr */}
                    <col style={{ width: '72px' }} /> {/* 5yr */}
                  </>
                ) : (
                  <>
                    <col style={{ width: '105px' }} /> {/* target */}
                    <col style={{ width: '80px' }} />  {/* upside */}
                    <col style={{ width: '110px' }} /> {/* rating */}
                    <col style={{ width: '90px' }} />  {/* earnings */}
                  </>
                )}
              </colgroup>
              <thead className="sticky top-0 z-20">
                <tr>
                  <th rowSpan={2} className="px-3 py-2 text-left bg-base-200/95 backdrop-blur-sm border-b border-base-300/40" />
                  <GrpTh label="Risk" span={1} />
                  <GrpTh label="Moving Averages" span={4} />
                  <GrpTh label="52-Week" span={4} />
                  {isFundsView
                    ? <GrpTh label="Historical Returns" span={3} />
                    : <GrpTh label="Analyst Targets" span={4} />
                  }
                </tr>
                <tr>
                  <SubTh label="Beta" k="beta" cur={sortKey} dir={sortDir} onSort={handleSort} />
                  <SubTh label="50d MA" k="ma_50d" cur={sortKey} dir={sortDir} onSort={handleSort} />
                  <SubTh label="200d MA" k="ma_200d" cur={sortKey} dir={sortDir} onSort={handleSort} />
                  <SubTh label="vs 50d" k="vs_50d_pct" cur={sortKey} dir={sortDir} onSort={handleSort} />
                  <SubTh label="vs 200d" k="vs_200d_pct" cur={sortKey} dir={sortDir} onSort={handleSort} />
                  <SubTh label="High" k="week52_high" cur={sortKey} dir={sortDir} onSort={handleSort} />
                  <SubTh label="Low" k="week52_low" cur={sortKey} dir={sortDir} onSort={handleSort} />
                  <SubTh label="1yr Return" k="week52_change_pct" cur={sortKey} dir={sortDir} onSort={handleSort} />
                  <th className="px-3 py-2 text-right text-[11px] font-semibold text-base-content/50 bg-base-200/95 backdrop-blur-sm border-b border-base-300/40">Range</th>
                  {isFundsView ? (
                    <>
                      <SubTh label="YTD" k="ytd_return_pct" cur={sortKey} dir={sortDir} onSort={handleSort} />
                      <SubTh label="3yr Ann" k="three_yr_return_pct" cur={sortKey} dir={sortDir} onSort={handleSort} />
                      <SubTh label="5yr Ann" k="five_yr_return_pct" cur={sortKey} dir={sortDir} onSort={handleSort} />
                    </>
                  ) : (
                    <>
                      <SubTh label="Target" k="analyst_target_mean" cur={sortKey} dir={sortDir} onSort={handleSort} />
                      <SubTh label="Upside" k="analyst_upside_pct" cur={sortKey} dir={sortDir} onSort={handleSort} />
                      <SubTh label="Rating" k="recommendation_mean" cur={sortKey} dir={sortDir} onSort={handleSort} />
                      <SubTh label="Earnings" k="earnings_date" cur={sortKey} dir={sortDir} onSort={handleSort} align="left" />
                    </>
                  )}
                </tr>
              </thead>
              <tbody>
                {groupedRows.map(({ sectorLabel, rows }) => {
                  const collapsed = sectorLabel != null && localCollapsed.has(sectorLabel);
                  return (
                    <React.Fragment key={sectorLabel ?? '__all'}>
                      {sectorLabel && <SectorGroupRow label={sectorLabel} count={rows.length} colSpan={isFundsView ? 13 : 14} collapsed={collapsed} onToggle={() => toggleLocal(sectorLabel)} />}
                      {!collapsed && rows.map((d, i) => (
                        <tr key={d.ticker} className={`border-t border-base-300/30 hover:bg-base-200/20 transition-colors ${i % 2 === 0 ? '' : 'bg-base-300/5'} ${d.error ? 'opacity-40' : ''}`}>
                          <td className="px-3 py-3.5">
                            <div className="font-bold text-primary text-sm">{d.ticker}</div>
                            {d.company_name && <div className="text-[10px] text-base-content/30 truncate max-w-[80px]">{d.company_name}</div>}
                          </td>
                          <td className="px-3 py-3.5 text-right">
                            <span className={`text-sm font-semibold ${betaColor(d.beta)}`}>
                              {d.beta != null ? d.beta.toFixed(2) : <span className="text-base-content/20">—</span>}
                            </span>
                          </td>
                          <td className="px-3 py-3.5 text-right text-sm text-base-content/60">{fmt$(d.ma_50d)}</td>
                          <td className="px-3 py-3.5 text-right text-sm text-base-content/60">{fmt$(d.ma_200d)}</td>
                          <td className="px-3 py-3.5 text-right">
                            <span className={`text-sm font-semibold ${gainCls(d.vs_50d_pct)}`}>{fmtPct(d.vs_50d_pct)}</span>
                          </td>
                          <td className="px-3 py-3.5 text-right">
                            <span className={`text-sm font-semibold ${gainCls(d.vs_200d_pct)}`}>{fmtPct(d.vs_200d_pct)}</span>
                          </td>
                          <td className="px-3 py-3.5 text-right text-sm text-base-content/50">{fmt$(d.week52_high)}</td>
                          <td className="px-3 py-3.5 text-right text-sm text-base-content/50">{fmt$(d.week52_low)}</td>
                          <td className="px-3 py-3.5 text-right">
                            <span className={`text-sm font-semibold ${gainCls(d.week52_change_pct)}`}>{fmtPct(d.week52_change_pct)}</span>
                          </td>
                          <td className="px-3 py-3.5"><RangeBar pct={d.range_position_pct} /></td>
                          {isFundsView ? (
                            <>
                              <td className="px-3 py-3.5 text-right">
                                <span className={`text-sm font-semibold ${returnColor(d.ytd_return_pct ?? null)}`}>{fmtPct(d.ytd_return_pct ?? null)}</span>
                              </td>
                              <td className="px-3 py-3.5 text-right">
                                <span className={`text-sm font-semibold ${returnColor(d.three_yr_return_pct ?? null)}`}>{fmtPct(d.three_yr_return_pct ?? null)}</span>
                              </td>
                              <td className="px-3 py-3.5 text-right">
                                <span className={`text-sm font-bold ${returnColor(d.five_yr_return_pct ?? null)}`}>{fmtPct(d.five_yr_return_pct ?? null)}</span>
                              </td>
                            </>
                          ) : (
                            <>
                              <td className="px-3 py-3.5 text-right">
                                {d.analyst_target_mean != null ? (
                                  <>
                                    <div className="text-sm font-semibold">{fmt$(d.analyst_target_mean)}</div>
                                    {d.analyst_count ? <div className="text-[10px] text-base-content/30">{d.analyst_count} analysts</div> : null}
                                  </>
                                ) : <span className="text-base-content/20 text-sm">—</span>}
                              </td>
                              <td className="px-3 py-3.5 text-right">
                                <span className={`text-sm font-bold ${upsideColor(d.analyst_upside_pct)}`}>{fmtPct(d.analyst_upside_pct)}</span>
                              </td>
                              <td className="px-3 py-3.5">
                                {d.recommendation ? (
                                  <span className={`inline-block px-2 py-0.5 rounded-full text-[11px] font-semibold ${REC_PILL[d.recommendation.toLowerCase()] || 'bg-base-300/30 text-base-content/50'}`}>
                                    {REC_LABEL[d.recommendation.toLowerCase()] || d.recommendation}
                                  </span>
                                ) : <span className="text-base-content/20 text-sm">—</span>}
                              </td>
                              <td className="px-3 py-3.5">
                                {d.earnings_date ? (() => {
                                  const days = Math.round((new Date(d.earnings_date).getTime() - Date.now()) / 86400000);
                                  const urgent = days >= 0 && days <= 7;
                                  const soon = days >= 0 && days <= 21;
                                  return (
                                    <div>
                                      <div className={`text-xs font-semibold ${urgent ? 'text-error' : soon ? 'text-warning' : 'text-base-content/60'}`}>
                                        {d.earnings_date}
                                      </div>
                                      {days >= 0 && days <= 30 && (
                                        <div className="text-[10px] text-base-content/30">{days === 0 ? 'today' : `${days}d`}</div>
                                      )}
                                    </div>
                                  );
                                })() : <span className="text-base-content/20 text-sm">—</span>}
                              </td>
                            </>
                          )}
                        </tr>
                      ))}
                    </React.Fragment>
                  );
                })}
              </tbody>
            </table>
          </div>
        </div>
      )}
    </div>
  );
}
