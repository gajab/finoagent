/**
 * MyTradesV2 — professional grouped trade journal with type-aware experiences.
 *
 * Groups:
 *   1. Income Options   — BOX, cash-secured puts, naked puts, credit spreads
 *                         Focus: annualized return, hold-vs-roll signal
 *   2. Long Stocks      — stock_long positions
 *                         Focus: unrealized P&L, cost basis, days held
 *   3. Covered Calls    — covered_call strategy (+ paired long stock if present)
 *                         Focus: combined income + underlying exposure
 *   4. Multi-leg Options — spreads, dual-direction buffer, custom multi-leg
 *                         Focus: Greeks, scenario chart, risk/reward
 *   5. Other            — catch-all
 *
 * Per-trade actions (all groups):
 *   - Refresh P&L  (live quotes)
 *   - Update Position  (add/reduce/close via ledger)
 *   - Transaction History  (full ledger panel)
 *   - Create Agent  (pre-populated monitor agent)
 *   - Ask Advisor  (type-aware inline AI analysis)
 */

import React, { useState, useEffect, useCallback, useMemo } from 'react';
import { Link } from 'react-router-dom';
import {
  RefreshCw, Loader2, AlertCircle, ChevronDown, ChevronUp,
  TrendingUp, TrendingDown, Clock, BarChart3, Activity,
  Brain, Send, Bot, History, PlusCircle, X, Target,
  DollarSign, Percent, Calendar, Shield, Zap, List, ExternalLink, Trash2, Pencil, Check,
  Layers, RotateCcw, Plus, Gauge, LogOut, LineChart, Filter, ArrowDownUp,
} from 'lucide-react';
import {
  fetchActiveTrades, fetchTradeLivePnl, fetchTradeAdvisor,
  appendTradeTransaction, fetchTradeTransactions, createAgent,
  deleteTrade, updateSavedStrategy,
} from '../../api';
import type { SavedStrategyItem, LivePnlResponse, TradeTransaction, LegAdvice, LegActionKind } from '../../api';
import { fmtMoney, fmtPct, fmtAnnualized, fmtDTE, fmtDate, fmtQty, pnlSummary } from '../../lib/tradeFormat';
import UpdatePositionModal from './UpdatePositionModal';
import TransactionHistoryPanel from './TransactionHistoryPanel';
import CreateAgentFromTradeModal from './CreateAgentFromTradeModal';
import PayoffChart from './PayoffChart';
import InstitutionalDesk from './InstitutionalDesk';
import QuantExitCard from './QuantExitCard';
import CloseTradeModal from './CloseTradeModal';
import BookTailRisk from './BookTailRisk';
import CollapsibleSection from './CollapsibleSection';
import { DeskDebate } from '../DeskDebate';
import { TickerChrome } from '../DerivativeIncome';
import { TraderGrid, PmGrid, RiskGrid } from './DeskMetrics';
import { fetchUnderlyingDesk } from '../../api';
import type { DeskFocusTrade, UnderlyingDeskResult } from '../../api';
import { updateTradeTransaction, deleteTradeTransaction, updateTradePurpose } from '../../api';

// ── Leg-action visuals (deterministic quant advisor) ─────────────────────────

const LEG_ACTION_STYLE: Record<LegActionKind, { label: string; cls: string }> = {
  CLOSE:      { label: 'CLOSE',  cls: 'badge-warning' },
  ROLL:       { label: 'ROLL',   cls: 'badge-error' },
  HOLD:       { label: 'HOLD',   cls: 'badge-success' },
  LET_EXPIRE: { label: 'EXPIRE', cls: 'badge-ghost' },
};

function LegActionBadge({ advice }: { advice?: LegAdvice }) {
  if (!advice) return <span className="text-base-content/25">—</span>;
  const s = LEG_ACTION_STYLE[advice.action] ?? LEG_ACTION_STYLE.HOLD;
  return (
    <span className={`badge badge-xs ${s.cls} text-[9px] font-semibold`} title={advice.reason}>
      {s.label}
    </span>
  );
}

// Whole-trade exit call — the 4-level vocabulary the Quant advises on.
const EXIT_STYLE: Record<string, { label: string; cls: string }> = {
  STRONG_HOLD:    { label: 'STRONG HOLD',    cls: 'badge-success' },
  HOLD:           { label: 'HOLD',           cls: 'badge-success badge-outline' },
  CLOSE:          { label: 'CLOSE',          cls: 'badge-warning' },
  STRONG_CLOSE:   { label: 'STRONG CLOSE',   cls: 'badge-error' },
};

/** Neutral intensity by magnitude — the Action badge carries the good/bad verdict. */
function pItmColor(p: number | null): string {
  if (p == null) return 'text-base-content/25';
  if (p >= 60) return 'text-base-content/90';
  if (p >= 30) return 'text-base-content/60';
  return 'text-base-content/40';
}

// ── Classification ───────────────────────────────────────────────────────────

type TradeGroup = 'income_options' | 'long_stocks' | 'short_stocks' | 'covered_calls' | 'multi_leg' | 'combo' | 'futures' | 'other';

const GROUP_META: Record<TradeGroup, {
  label: string;
  color: string;
  icon: React.ReactNode;
  desc: string;
}> = {
  income_options: {
    label: 'Income Options',
    color: 'success',
    icon: <Percent className="w-4 h-4" />,
    desc: 'BOX spreads, cash-secured puts, credit strategies',
  },
  long_stocks: {
    label: 'Long Stocks',
    color: 'primary',
    icon: <TrendingUp className="w-4 h-4" />,
    desc: 'Long equity positions',
  },
  short_stocks: {
    label: 'Short Stocks',
    color: 'error',
    icon: <TrendingDown className="w-4 h-4" />,
    desc: 'Short equity positions',
  },
  covered_calls: {
    label: 'Covered Calls',
    color: 'warning',
    icon: <Shield className="w-4 h-4" />,
    desc: 'Short calls against long stock holdings',
  },
  futures: {
    label: 'Futures',
    color: 'info',
    icon: <Activity className="w-4 h-4" />,
    desc: 'Standalone futures contract positions',
  },
  multi_leg: {
    label: 'Multi-leg Options',
    color: 'secondary',
    icon: <Zap className="w-4 h-4" />,
    desc: 'Spreads, straddles, buffers, complex structures',
  },
  combo: {
    label: 'Stock + Options Combos',
    color: 'warning',
    icon: <Layers className="w-4 h-4" />,
    desc: 'Stock position with hedging or enhancement options',
  },
  other: {
    label: 'Other Strategies',
    color: 'base-content',
    icon: <List className="w-4 h-4" />,
    desc: 'Pair trades, custom strategies',
  },
};

// ── Purpose (what the trade is FOR — the new top-level grouping) ──────────────

type TradePurpose = 'income' | 'hedge' | 'trade' | 'managed_floor' | 'managed_buffer' | 'dual_directional' | 'other';

const PURPOSE_ORDER: TradePurpose[] = ['income', 'hedge', 'managed_floor', 'managed_buffer', 'dual_directional', 'trade', 'other'];

const PURPOSE_META: Record<TradePurpose, { label: string; color: string; icon: React.ReactNode; desc: string }> = {
  income:           { label: 'Income',            color: 'success',      icon: <Percent className="w-4 h-4" />,      desc: 'Premium-selling / carry — covered calls, CSPs, credit spreads' },
  hedge:            { label: 'Hedge',             color: 'info',         icon: <Shield className="w-4 h-4" />,       desc: 'Downside protection / risk offset' },
  managed_floor:    { label: 'Managed Floor',     color: 'primary',      icon: <TrendingUp className="w-4 h-4" />,   desc: 'Floored downside with participation' },
  managed_buffer:   { label: 'Managed Buffer',    color: 'secondary',    icon: <Layers className="w-4 h-4" />,       desc: 'Buffered-outcome structures' },
  dual_directional: { label: 'Dual Directional',  color: 'warning',      icon: <Activity className="w-4 h-4" />,     desc: 'Profits either direction within a band' },
  trade:            { label: 'Trade',             color: 'error',        icon: <Zap className="w-4 h-4" />,          desc: 'Directional / tactical positions' },
  other:            { label: 'Other',             color: 'base-content', icon: <List className="w-4 h-4" />,         desc: 'Uncategorized' },
};

/** The trade's purpose (from parameters.purpose); legacy trades default to Income. */
function tradePurpose(trade: SavedStrategyItem): TradePurpose {
  const p = String(trade.parameters?.purpose || '').toLowerCase();
  return (p in PURPOSE_META) ? (p as TradePurpose) : 'income';
}

/** Infer the strategy_type to store when legs change. */
function inferStrategyType(
  baseType: string,
  allLegs: any[],
  hasStock: boolean,
): string {
  const optLegs = allLegs.filter(l => ['call', 'put'].includes((l.type || '').toLowerCase()));
  if (!hasStock) {
    if (optLegs.length === 0) return baseType;
    if (optLegs.length === 1) return `options_${optLegs[0].action}_${optLegs[0].type.toLowerCase()}`;
    return 'options_spread';
  }
  // Has stock: determine by option legs
  if (optLegs.length === 0) return baseType.includes('short') ? 'stock_short' : 'stock_long';
  const allShortCalls = optLegs.every(l => l.action === 'sell' && l.type?.toLowerCase() === 'call');
  return allShortCalls ? 'covered_call' : 'stock_combo';
}

function classifyTrade(trade: SavedStrategyItem): TradeGroup {
  const t = (trade.strategy_type || '').toLowerCase();
  if (t === 'futures') return 'futures';
  const legs = (trade.legs_data || []) as any[];
  const legCount = legs.length;

  const hasOptionLegs = legs.some(l => ['call', 'put'].includes((l.type || '').toLowerCase()));
  const hasStock = !!(trade.parameters?.shares && parseFloat(trade.parameters.shares) > 0);
  const allShortCalls = hasOptionLegs && legs.every(l => l.action === 'sell' && l.type?.toLowerCase() === 'call');

  if (t === 'box_spread') return 'income_options';

  // Stock-based types — re-evaluate if option legs exist (handles evolution)
  if (t === 'stock_long' || t === 'stock_short') {
    if (!hasOptionLegs) return t === 'stock_long' ? 'long_stocks' : 'short_stocks';
    if (allShortCalls) return 'covered_calls';
    return 'combo';
  }

  if (t === 'covered_call') {
    // If all option legs were removed, revert display to long stocks
    if (!hasOptionLegs && hasStock) return 'long_stocks';
    return 'covered_calls';
  }

  if (t === 'stock_combo') {
    if (!hasOptionLegs && hasStock) return 'long_stocks';
    if (allShortCalls) return 'covered_calls';
    return 'combo';
  }

  if (
    t.includes('short_put') || t.includes('sell_put') ||
    t.includes('cash_secured') || t.includes('naked_put') ||
    t.includes('csp') || t.includes('credit')
  ) return 'income_options';

  // Options trades that have stock added
  if (hasStock && hasOptionLegs) {
    if (allShortCalls) return 'covered_calls';
    return 'combo';
  }

  if (
    t.includes('spread') || t.includes('straddle') || t.includes('strangle') ||
    t.includes('dual_direction') || t.includes('buffer') || legCount > 1
  ) return 'multi_leg';
  if (t.startsWith('options_') || t.includes('option')) return 'multi_leg';
  return 'other';
}

// Map a saved trade's legs to a Desk-Review structure + primary short strike, so
// the desk debate can focus on THIS trade. Returns null for shapes the desk review
// doesn't rank (pure stock, box, long-only options) — the debate is hidden there.
function deskFocusForTrade(trade: SavedStrategyItem, pnl?: LivePnlResponse | null): DeskFocusTrade | null {
  const legs = (trade.legs_data || []) as any[];
  const opts = legs.filter(l => /call|put/i.test(l.type || ''));
  if (opts.length === 0) return null;
  const isShort = (l: any) => /sell|short/i.test(l.action || '');
  const isCall = (l: any) => /call/i.test(l.type || '');
  const isPut = (l: any) => /put/i.test(l.type || '');
  const sc = opts.filter(l => isShort(l) && isCall(l));
  const sp = opts.filter(l => isShort(l) && isPut(l));
  const lc = opts.filter(l => !isShort(l) && isCall(l));
  const lp = opts.filter(l => !isShort(l) && isPut(l));
  const hasStock = (trade.parameters?.shares || 0) > 0 || trade.strategy_type === 'covered_call';
  const expiration = pnl?.expiration_date || opts[0]?.expiration || opts[0]?.exp || null;

  let structure: string | null = null;
  let shortStrike: number | null = null;
  if (hasStock && sc.length === 1 && lp.length === 0) { structure = 'covered_call'; shortStrike = sc[0].strike; }
  else if (hasStock && sc.length === 1 && lp.length === 1) { structure = 'collar'; shortStrike = sc[0].strike; }
  else if (!hasStock && sp.length === 1 && opts.length === 1) { structure = 'cash_secured_put'; shortStrike = sp[0].strike; }
  // A bare short call — NAKED (no stock). Same short-call option-math as a covered call
  // (VRP / moneyness / delta / skew / keep-prob), but the deep read carries the true
  // unbounded-upside risk + naked advice, distinct from a covered call.
  else if (!hasStock && sc.length === 1 && opts.length === 1) { structure = 'naked_call'; shortStrike = sc[0].strike; }
  else if (sc.length === 1 && lc.length === 1 && sp.length === 0 && lp.length === 0) { structure = 'call_credit_spread'; shortStrike = sc[0].strike; }
  else if (sp.length === 1 && lp.length === 1 && sc.length === 0 && lc.length === 0) { structure = 'put_credit_spread'; shortStrike = sp[0].strike; }
  else if (sp.length === 1 && lp.length === 1 && sc.length === 1 && lc.length === 1) { structure = 'iron_condor'; shortStrike = sp[0].strike; }
  else if (sp.length === 1 && sc.length === 1 && lc.length === 1 && lp.length === 0) { structure = 'jade_lizard'; shortStrike = sc[0].strike; }
  else if (sp.length === 1 && sc.length === 1 && lc.length === 0 && lp.length === 0) { structure = 'short_strangle'; shortStrike = sp[0].strike; }

  // Unrecognized multi-leg / custom structure — STILL surface the button on every
  // options trade (the user's #1 ask). The backend prices the scan-supported
  // structures at exact legs and, for anything else, returns the honest "not
  // scorable — the algorithmic card above is the read" message.
  if (!structure) {
    structure = 'custom';
    shortStrike = sc[0]?.strike ?? sp[0]?.strike ?? opts[0]?.strike ?? null;
  }
  return { structure, expiration, short_strike: shortStrike != null ? Number(shortStrike) : null };
}

// User-facing option STRUCTURE of a trade (for filtering within a group).
function tradeStructure(trade: SavedStrategyItem): { key: string; label: string } {
  const legs = (trade.legs_data || []) as any[];
  const opts = legs.filter(l => /call|put/i.test(l.type || ''));
  const hasStock = (Number(trade.parameters?.shares) || 0) > 0;
  if (opts.length === 0) return hasStock ? { key: 'stock', label: 'Stock' } : { key: 'other', label: 'Other' };
  const short = (l: any) => /sell|short/i.test(l.action || '');
  const call = (l: any) => /call/i.test(l.type || '');
  const put = (l: any) => /put/i.test(l.type || '');
  const sc = opts.filter(l => short(l) && call(l)).length;
  const sp = opts.filter(l => short(l) && put(l)).length;
  const lc = opts.filter(l => !short(l) && call(l)).length;
  const lp = opts.filter(l => !short(l) && put(l)).length;
  const one = opts.length === 1;
  if (hasStock && sc === 1 && lp === 0) return { key: 'covered_call', label: 'Covered Call' };
  if (hasStock && sc === 1 && lp === 1) return { key: 'collar', label: 'Collar' };
  if (!hasStock && sp === 1 && one) return { key: 'cash_secured_put', label: 'Cash-Secured Put' };
  if (!hasStock && sc === 1 && one) return { key: 'naked_call', label: 'Naked Call' };
  if (!hasStock && lp === 1 && one) return { key: 'long_put', label: 'Long Put' };
  if (!hasStock && lc === 1 && one) return { key: 'long_call', label: 'Long Call' };
  if (sp === 1 && lp === 1 && sc === 0 && lc === 0) return { key: 'put_credit_spread', label: 'Put Credit Spread' };
  if (sc === 1 && lc === 1 && sp === 0 && lp === 0) return { key: 'call_credit_spread', label: 'Call Credit Spread' };
  if (sp === 1 && lp === 1 && sc === 1 && lc === 1) return { key: 'iron_condor', label: 'Iron Condor' };
  if (sp === 1 && sc === 1 && lc === 1 && lp === 0) return { key: 'jade_lizard', label: 'Jade Lizard' };
  if (sp === 1 && sc === 1 && lc === 0 && lp === 0) return { key: 'short_strangle', label: 'Short Strangle' };
  return { key: 'custom', label: 'Custom' };
}

// ── Shared helpers ───────────────────────────────────────────────────────────

function daysHeld(entryDate: string | null | undefined): number {
  if (!entryDate) return 0;
  return Math.max(0, Math.floor((Date.now() - new Date(entryDate).getTime()) / 86400000));
}

function expiryFrom(trade: SavedStrategyItem, pnl?: LivePnlResponse | null): string | null {
  return pnl?.expiration_date
    || trade.result_snapshot?.expirationDate
    || trade.result_snapshot?.spread?.expiration
    || trade.legs_data?.find((l: any) => l.expiration || l.expiry)?.expiration
    || null;
}

function dteFrom(expiry: string | null): number | null {
  if (!expiry) return null;
  const d = Math.floor((new Date(expiry).getTime() - Date.now()) / 86400000);
  return Math.max(0, d);
}

// ── Metric chip ──────────────────────────────────────────────────────────────

function Chip({ label, value, color = '' }: { label: string; value: string; color?: string }) {
  return (
    <div className="flex flex-col items-center px-3 py-1.5 rounded-lg bg-base-300/30 min-w-[72px]">
      <span className="text-[9px] uppercase tracking-wider text-base-content/40">{label}</span>
      <span className={`text-xs font-semibold mt-0.5 ${color}`}>{value}</span>
    </div>
  );
}

// ── Group summary bar ────────────────────────────────────────────────────────

function GroupSummary({ trades, pnlMap }: {
  trades: SavedStrategyItem[];
  pnlMap: Record<number, LivePnlResponse>;
}) {
  const anyFutures = trades.some(t => t.strategy_type === 'futures');
  const totalCapital = trades.reduce((s, t) => {
    const p = pnlMap[t.id];
    if (t.strategy_type === 'futures') {
      const margin = t.parameters?.margin_req ? parseFloat(t.parameters.margin_req) : 0;
      return s + (margin || Math.abs(p?.entry_cost ?? Math.abs(t.entry_net_debit ?? 0)));
    }
    return s + Math.abs(p?.entry_cost ?? Math.abs(t.entry_net_debit ?? 0));
  }, 0);

  const totalNotional = anyFutures ? trades.reduce((s, t) => {
    const p = pnlMap[t.id];
    const contracts = t.parameters?.contracts ?? t.parameters?.shares ?? 1;
    const multiplier = t.parameters?.multiplier ?? 1.0;
    const price = p?.underlying_price ?? t.entry_prices?.[0]?.price ?? 0;
    return s + (price * contracts * multiplier);
  }, 0) : 0;

  const totalPnl = trades.reduce((s, t) => {
    const p = pnlMap[t.id];
    return s + (p?.unrealized_pnl ?? 0);
  }, 0);

  const annReturns = trades
    .map(t => pnlMap[t.id]?.analysis?.annualized_return_to_expiry)
    .filter((v): v is number => v != null);
  const avgAnn = annReturns.length
    ? annReturns.reduce((a, b) => a + b, 0) / annReturns.length
    : null;

  return (
    <div className="flex items-center gap-2 flex-wrap text-xs text-base-content/50 px-4 pb-3">
      <span>{trades.length} position{trades.length !== 1 ? 's' : ''}</span>
      <span className="opacity-30">·</span>
      <span>
        {fmtMoney(totalCapital)} {anyFutures ? 'margin deployed' : 'deployed'}
      </span>
      {anyFutures && totalNotional > 0 && (
        <>
          <span className="opacity-30">·</span>
          <span>{fmtMoney(totalNotional)} notional exposure</span>
        </>
      )}
      {totalPnl !== 0 && (
        <>
          <span className="opacity-30">·</span>
          <span className={totalPnl >= 0 ? 'text-success' : 'text-error'}>
            {totalPnl >= 0 ? '+' : ''}{fmtMoney(Math.abs(totalPnl))} unrealized
          </span>
        </>
      )}
      {avgAnn != null && (
        <>
          <span className="opacity-30">·</span>
          <span className={`${avgAnn >= 0 ? 'text-success' : 'text-error'} font-medium`}>
            avg {fmtAnnualized(avgAnn)}
          </span>
        </>
      )}
    </div>
  );
}

// ── Trade card ───────────────────────────────────────────────────────────────

interface CardProps {
  trade: SavedStrategyItem;
  group: TradeGroup;
  pnl?: LivePnlResponse | null;
  isExpanded: boolean;
  onToggle: () => void;
  onRefreshPnl: () => void;
  pnlLoading: boolean;
  quoteSource: string;
  onQuoteSourceChange: (v: string) => void;
  onUpdatePosition: () => void;
  onShowHistory: () => void;
  onCreateAgent: () => void;
  advisorState: { loading: boolean; response: string; error: string | null; question: string };
  onAskAdvisor: (q?: string) => void;
  onAdvisorQuestion: (q: string) => void;
  showHistory: boolean;
  onFetchTransactions: (id: number) => Promise<TradeTransaction[]>;
  onPositionChanged: (id: number) => void;
  onDeleteTrade: (id: number) => void;
}

interface RollState {
  legIdx: number;
  closePrice: string;
  newAction: 'buy' | 'sell';
  newType: 'call' | 'put';
  newContracts: number;
  newStrike: string;
  newExpiration: string;
  newPremium: string;
  saving: boolean;
  error: string | null;
}

function TradeCard({
  trade, group, pnl, isExpanded, onToggle,
  onRefreshPnl, pnlLoading, quoteSource, onQuoteSourceChange,
  onUpdatePosition, onShowHistory, onCreateAgent,
  advisorState, onAskAdvisor, onAdvisorQuestion,
  showHistory, onFetchTransactions, onPositionChanged, onDeleteTrade,
}: CardProps) {
  const [deleteConfirm, setDeleteConfirm] = useState(false);
  const [deleting, setDeleting] = useState(false);
  // Close flow: null = closed modal; { preselect } = open, optionally with one leg pre-checked.
  const [closeModal, setCloseModal] = useState<{ preselect: number | null } | null>(null);
  // Underlying stock context (Income-desk chrome) — lazily fetched on first expand.
  const [underlying, setUnderlying] = useState<UnderlyingDeskResult | null>(null);
  const [underlyingLoading, setUnderlyingLoading] = useState(false);
  useEffect(() => {
    if (isExpanded && !underlying && !underlyingLoading) {
      setUnderlyingLoading(true);
      fetchUnderlyingDesk(trade.id, quoteSource)
        .then(setUnderlying)
        .catch(() => { /* chrome is best-effort; absence just hides it */ })
        .finally(() => setUnderlyingLoading(false));
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [isExpanded]);
  const [rollState, setRollState] = useState<RollState | null>(null);
  const [addingLeg, setAddingLeg] = useState(false);
  const [newLegState, setNewLegState] = useState({
    action: 'buy' as 'buy' | 'sell',
    type: 'call' as 'call' | 'put',
    contracts: 1, strike: '', expiration: '', premium: '', saving: false, error: null as string | null,
  });
  const [addingStock, setAddingStock] = useState(false);
  const [addStockState, setAddStockState] = useState({
    shares: '', price: '', saving: false, error: null as string | null,
  });
  const [editingLegIdx, setEditingLegIdx] = useState<number | null>(null);
  const [editLegState, setEditLegState] = useState({
    action: 'buy' as 'buy' | 'sell',
    type: 'call' as 'call' | 'put',
    strike: '',
    qty: 1,
    expiration: '',
    entry: '',
    saving: false,
    error: null as string | null,
  });

  const handleDeleteConfirmed = async (e: React.MouseEvent) => {
    e.stopPropagation();
    setDeleting(true);
    try {
      await deleteTrade(trade.id);
      onDeleteTrade(trade.id);
    } catch {
      setDeleting(false);
      setDeleteConfirm(false);
    }
  };

  const expiry = expiryFrom(trade, pnl);
  const dte = dteFrom(expiry);
  const held = daysHeld(trade.entry_date);
  const isIncome = group === 'income_options';
  const isStock = group === 'long_stocks' || group === 'short_stocks';
  const isFutures = group === 'futures';
  const params = trade.parameters || {};
  const contracts = params.contracts ?? params.shares ?? null;
  const multiplier = params.multiplier ?? 1.0;
  // isComboLike = any trade that has both a stock position AND option legs
  const isComboLike = group === 'combo' || group === 'covered_calls';
  const isCombo = group === 'combo';
  const isCoveredCall = group === 'covered_calls';
  // Show stock+options breakdown when position has both components
  const hasStockLeg = !!(trade.parameters?.shares && parseFloat(trade.parameters.shares) > 0);
  const hasOptionLegsNow = !!(trade.legs_data && trade.legs_data.length > 0);

  const startRoll = (legIdx: number) => {
    const leg = trade.legs_data?.[legIdx];
    if (!leg) return;
    setRollState({
      legIdx,
      closePrice: '',
      newAction: leg.action === 'buy' ? 'buy' : 'sell',
      newType: (leg.type?.toLowerCase() === 'put' ? 'put' : 'call'),
      newContracts: leg.qty ?? 1,
      newStrike: String(leg.strike ?? ''),
      newExpiration: leg.expiration || leg.expiry || '',
      newPremium: '',
      saving: false,
      error: null,
    });
  };

  const commitRoll = async () => {
    if (!rollState) return;
    const { legIdx, closePrice, newAction, newType, newContracts, newStrike, newExpiration, newPremium } = rollState;
    if (!newStrike || !newExpiration || !newPremium) {
      setRollState(s => s && ({ ...s, error: 'Fill in all new leg fields' }));
      return;
    }
    setRollState(s => s && ({ ...s, saving: true, error: null }));
    try {
      const oldLeg = trade.legs_data?.[legIdx];
      const newLegs = (trade.legs_data || []).map((l: any, i: number) =>
        i === legIdx
          ? { action: newAction, type: newType, qty: newContracts, strike: parseFloat(newStrike), expiration: newExpiration, premium: parseFloat(newPremium), label: l.label ?? `Leg ${i + 1}` }
          : l
      );
      await updateSavedStrategy(trade.id, { legs_data: newLegs });
      const closePriceNum = parseFloat(closePrice) || 0;
      if (closePriceNum > 0 && oldLeg) {
        const qty = oldLeg.qty ?? 1;
        const closeNote = `Rolled ${oldLeg.action} ${oldLeg.type} $${oldLeg.strike} @ $${closePriceNum}/share (${qty} contracts) → ${newAction} ${newType} $${newStrike} exp ${newExpiration}`;
        await appendTradeTransaction(trade.id, { action: 'adjust', quantity: 0, price: closePriceNum, note: closeNote, source: 'manual', executed_at: new Date().toISOString() });
      }
      setRollState(null);
      onPositionChanged(trade.id);
    } catch (e: any) {
      setRollState(s => s && ({ ...s, saving: false, error: e?.message || 'Failed to roll leg' }));
    }
  };

  const startEditingLeg = (idx: number) => {
    const leg = trade.legs_data?.[idx];
    if (!leg) return;
    const epIdx = isComboLike ? idx + 1 : idx;
    const ep = trade.entry_prices?.[epIdx]?.price ?? leg.premium ?? leg.mid ?? leg.price ?? '';
    setEditingLegIdx(idx);
    setEditLegState({
      action: leg.action || 'buy',
      type: leg.type || 'call',
      strike: String(leg.strike ?? ''),
      qty: leg.qty ?? 1,
      expiration: leg.expiration || leg.expiry || '',
      entry: String(ep),
      saving: false,
      error: null,
    });
  };

  const commitEditLeg = async (idx: number) => {
    const { action, type, strike, qty, expiration, entry } = editLegState;
    if (!strike || !expiration || !entry) {
      setEditLegState(s => ({ ...s, error: 'Fill in all fields' }));
      return;
    }
    setEditLegState(s => ({ ...s, saving: true, error: null }));
    try {
      const newLegs = (trade.legs_data || []).map((l: any, i: number) => {
        if (i === idx) {
          return {
            ...l,
            action,
            type,
            strike: parseFloat(strike),
            qty: parseInt(String(qty)) || 1,
            expiration,
            premium: parseFloat(entry),
          };
        }
        return l;
      });

      // Also update the entry prices array in trade
      const newEntryPrices = [...(trade.entry_prices || [])];
      const epIdx = isComboLike ? idx + 1 : idx;
      newEntryPrices[epIdx] = {
        ...newEntryPrices[epIdx],
        price: parseFloat(entry),
      };

      const hasStock = !!(trade.parameters?.shares && parseFloat(trade.parameters.shares) > 0);
      const newType = inferStrategyType(trade.strategy_type || '', newLegs, hasStock);

      await updateSavedStrategy(trade.id, {
        legs_data: newLegs,
        entry_prices: newEntryPrices,
        strategy_type: newType,
      });

      setEditingLegIdx(null);
      onPositionChanged(trade.id);
    } catch (e: any) {
      setEditLegState(s => ({ ...s, saving: false, error: e?.message || 'Failed to update leg' }));
    }
  };

  const commitAddLeg = async () => {
    const { action, type, contracts, strike, expiration, premium } = newLegState;
    if (!strike || !expiration || !premium) {
      setNewLegState(s => ({ ...s, error: 'Fill in all leg fields' }));
      return;
    }
    setNewLegState(s => ({ ...s, saving: true, error: null }));
    try {
      const existingLegs = trade.legs_data || [];
      const newLeg = { action, type, qty: contracts, strike: parseFloat(strike), expiration, premium: parseFloat(premium), label: `Leg ${existingLegs.length + 1}` };
      const newLegs = [...existingLegs, newLeg];
      const hasStock = !!(trade.parameters?.shares && parseFloat(trade.parameters.shares) > 0);
      const newType = inferStrategyType(trade.strategy_type || '', newLegs, hasStock);
      await updateSavedStrategy(trade.id, { legs_data: newLegs, strategy_type: newType });
      setAddingLeg(false);
      setNewLegState({ action: 'buy', type: 'call', contracts: 1, strike: '', expiration: '', premium: '', saving: false, error: null });
      onPositionChanged(trade.id);
    } catch (e: any) {
      setNewLegState(s => ({ ...s, saving: false, error: e?.message || 'Failed to add leg' }));
    }
  };

  const commitAddStock = async () => {
    const sharesNum = parseFloat(addStockState.shares);
    const priceNum = parseFloat(addStockState.price);
    if (!sharesNum || !priceNum) {
      setAddStockState(s => ({ ...s, error: 'Enter shares and price' }));
      return;
    }
    setAddStockState(s => ({ ...s, saving: true, error: null }));
    try {
      const existingLegs = trade.legs_data || [];
      const allShortCalls = existingLegs.length > 0 && existingLegs.every((l: any) => l.action === 'sell' && l.type?.toLowerCase() === 'call');
      const newType = allShortCalls ? 'covered_call' : 'stock_combo';
      await updateSavedStrategy(trade.id, {
        strategy_type: newType,
        parameters: { ...trade.parameters, shares: sharesNum, avg_cost: priceNum },
        entry_prices: [{ ticker: trade.ticker, price: priceNum }, ...(trade.entry_prices || [])],
      });
      setAddingStock(false);
      setAddStockState({ shares: '', price: '', saving: false, error: null });
      onPositionChanged(trade.id);
    } catch (e: any) {
      setAddStockState(s => ({ ...s, saving: false, error: e?.message || 'Failed to add stock' }));
    }
  };

  // Headline metrics for collapsed row
  const annReturn = pnl?.analysis?.annualized_return_to_expiry ?? null;
  const pnlAmt = pnl?.unrealized_pnl ?? null;
  const pnlPct = pnl?.pnl_pct ?? null;

  const entryNet = trade.entry_net_debit ?? 0;
  const shares = trade.parameters?.shares ?? null;
  // Prefer ledger-computed weighted avg cost (updated on every transaction)
  // over the frozen original entry price.
  const entryPrice = trade.parameters?.avg_cost ?? trade.entry_prices?.[0]?.price ?? null;

  const statusColor = pnlAmt == null ? '' : pnlAmt >= 0 ? 'text-success' : 'text-error';

  // Realized P&L — banked at close (whole or partial). Present on closed trades and
  // on active trades that have had a leg/stock closed. exit_net mirrors it for legacy.
  const isClosed = trade.trade_status === 'closed';
  const realizedPnl: number | null =
    trade.parameters?.realized_pnl != null ? Number(trade.parameters.realized_pnl)
      : isClosed ? (trade.exit_net ?? null) : null;
  const hasPartialRealized = !isClosed && realizedPnl != null && Math.abs(realizedPnl) > 0.005;
  const closedLegs: any[] = Array.isArray(trade.parameters?.closed_legs) ? trade.parameters.closed_legs : [];

  // Static class strings so Tailwind's JIT actually generates them (no safelist).
  const TINT_CLASSES: Record<string, string> = {
    success: 'border-l-success/50 bg-success/[0.03] hover:bg-success/[0.06]',
    primary: 'border-l-primary/50 bg-primary/[0.03] hover:bg-primary/[0.06]',
    error: 'border-l-error/50 bg-error/[0.03] hover:bg-error/[0.06]',
    warning: 'border-l-warning/50 bg-warning/[0.03] hover:bg-warning/[0.06]',
    info: 'border-l-info/50 bg-info/[0.03] hover:bg-info/[0.06]',
    secondary: 'border-l-secondary/50 bg-secondary/[0.03] hover:bg-secondary/[0.06]',
    'base-content': 'border-l-base-content/20 bg-base-100/30 hover:bg-base-100/50',
  };
  const tint = TINT_CLASSES[GROUP_META[group]?.color] || TINT_CLASSES['base-content'];
  return (
    <div className={`border border-white/[0.04] border-l-[3px] ${tint} rounded-xl overflow-hidden transition-colors`}>

      {/* Collapsed header row */}
      <div
        className="px-4 py-3 flex items-center gap-3 cursor-pointer"
        onClick={onToggle}
      >
        {/* Left: ticker + strategy */}
        <div className="flex-1 min-w-0">
          <div className="flex items-center gap-2 flex-wrap">
            <span className="font-bold text-sm">{trade.ticker}</span>
            <span className="badge badge-xs badge-ghost opacity-70">
              {trade.strategy_type?.replace(/_/g, ' ')}
            </span>
            {dte != null && dte <= 7 && (
              <span className="badge badge-xs badge-warning">⚠ {dte}d left</span>
            )}
            {/* Whole-trade exit call (Strong Hold / Hold / Consider Close / Close) */}
            {pnl?.analysis?.exit_signal && trade.strategy_type !== 'box_spread' && (
              <span className={`badge badge-xs font-semibold ${EXIT_STYLE[pnl.analysis.exit_signal]?.cls || 'badge-ghost'}`}
                title={pnl.analysis.exit_reasons?.[0]}>
                {EXIT_STYLE[pnl.analysis.exit_signal]?.label || pnl.analysis.exit_signal}
              </span>
            )}
          </div>
          <div className="flex items-center gap-3 mt-0.5 text-[10px] text-base-content/40 flex-wrap">
            {trade.entry_date && (
              <span className="flex items-center gap-1">
                <Calendar className="w-2.5 h-2.5" />
                {fmtDate(trade.entry_date)}
              </span>
            )}
            {held > 0 && <span className="flex items-center gap-1"><Clock className="w-2.5 h-2.5" />{held}d held</span>}
            {expiry && <span>exp {expiry}{dte != null ? ` (${fmtDTE(dte)})` : ''}</span>}
            {shares && (isStock || isComboLike) && (
              <span>
                {fmtQty(shares)} sh @{' '}
                {entryPrice ? fmtMoney(entryPrice) : '?'}
                {trade.parameters?.avg_cost ? ' avg' : ''}
                {isComboLike && hasOptionLegsNow && ` + ${(trade.legs_data || []).length} option leg${(trade.legs_data || []).length !== 1 ? 's' : ''}`}
              </span>
            )}
          </div>
        </div>

        {/* Right: current price (stocks/combos) + key P&L metric */}
        <div className="text-right shrink-0 space-y-0.5">
          {/* Current price badge — always show for stock-based trades when available */}
          {(isStock || isComboLike) && (pnl?.underlying_price ?? 0) > 0 && (
            <div className="text-[10px] font-mono text-base-content/50">
              {fmtMoney(pnl!.underlying_price)}
            </div>
          )}
          {isClosed ? (
            <>
              <div className={`font-bold text-sm ${realizedPnl != null && realizedPnl >= 0 ? 'text-success' : 'text-error'}`}>
                {realizedPnl != null ? `${realizedPnl >= 0 ? '+' : ''}${fmtMoney(Math.abs(realizedPnl))}` : '—'}
              </div>
              <div className="text-[9px] uppercase tracking-wider text-base-content/40">realized</div>
            </>
          ) : isIncome && annReturn != null ? (
            <>
              <div className={`font-bold text-sm ${annReturn >= 0 ? 'text-success' : 'text-error'}`}>
                {fmtAnnualized(annReturn)}
              </div>
              {pnlAmt != null && (
                <div className="text-[10px] text-base-content/40">
                  {pnlAmt >= 0 ? '+' : ''}{fmtMoney(Math.abs(pnlAmt))}
                </div>
              )}
            </>
          ) : pnlAmt != null ? (
            <>
              <div className={`font-bold text-sm ${statusColor}`}>
                {pnlAmt >= 0 ? '+' : ''}{fmtMoney(Math.abs(pnlAmt))}
              </div>
              {pnlPct != null && (
                <div className={`text-[10px] ${statusColor} opacity-70`}>
                  {pnlPct >= 0 ? '+' : ''}{pnlPct.toFixed(2)}%
                </div>
              )}
            </>
          ) : (
            <div className="text-xs text-base-content/30">
              {entryNet ? fmtMoney(Math.abs(entryNet)) : '—'}
            </div>
          )}
        </div>

        {isExpanded ? <ChevronUp className="w-3.5 h-3.5 shrink-0 text-base-content/30" /> : <ChevronDown className="w-3.5 h-3.5 shrink-0 text-base-content/30" />}
      </div>

      {/* Expanded detail */}
      {isExpanded && (
        <div className="border-t border-white/[0.04] px-4 py-4 space-y-4">

          {/* Realized P&L — closed trades and partial closes bank realized here */}
          {(isClosed || hasPartialRealized) && realizedPnl != null && (
            <div className={`rounded-xl border px-3 py-2.5 flex items-center gap-3 ${realizedPnl >= 0 ? 'border-success/25 bg-success/[0.05]' : 'border-error/25 bg-error/[0.05]'}`}>
              <LogOut className={`w-4 h-4 shrink-0 ${realizedPnl >= 0 ? 'text-success' : 'text-error'}`} />
              <div className="min-w-0">
                <div className="text-[9px] uppercase tracking-wider text-base-content/40">
                  {isClosed ? `Realized P&L · closed${trade.exit_date ? ` ${fmtDate(trade.exit_date)}` : ''}` : 'Realized so far · partial close'}
                </div>
                <div className={`text-lg font-bold ${realizedPnl >= 0 ? 'text-success' : 'text-error'}`}>
                  {realizedPnl >= 0 ? '+' : ''}{fmtMoney(Math.abs(realizedPnl))}
                </div>
              </div>
              {closedLegs.length > 0 && (
                <div className="ml-auto text-right text-[10px] text-base-content/50 max-w-[55%] space-y-0.5">
                  {closedLegs.slice(-4).map((c, i) => (
                    <div key={i} className="truncate">
                      {c.type === 'stock' ? `Stock ${fmtQty(c.qty)}sh` : `${c.action} ${c.type} $${c.strike}`}
                      {' @ '}{fmtMoney(c.exit_price)}{' → '}
                      <span className={Number(c.realized) >= 0 ? 'text-success' : 'text-error'}>
                        {Number(c.realized) >= 0 ? '+' : ''}{fmtMoney(Math.abs(Number(c.realized)))}
                      </span>
                    </div>
                  ))}
                  {!isClosed && <div className="text-base-content/30">remaining legs still open</div>}
                </div>
              )}
            </div>
          )}

          {/* Action bar */}
          <div className="flex items-center gap-2 flex-wrap">
            <div className="flex items-center gap-1 bg-base-300/30 rounded-lg p-0.5">
              <select
                className="select select-ghost select-xs text-[10px] h-7 min-h-0 pr-6"
                value={quoteSource}
                onChange={e => onQuoteSourceChange(e.target.value)}
                onClick={e => e.stopPropagation()}
              >
                <option value="yfinance">YFinance</option>
                <option value="ibkr">IBKR</option>
              </select>
              <button
                className="btn btn-ghost btn-xs h-7 min-h-0 gap-1 text-[10px]"
                onClick={e => { e.stopPropagation(); onRefreshPnl(); }}
                disabled={pnlLoading}
              >
                {pnlLoading ? <Loader2 className="w-3 h-3 animate-spin" /> : <RefreshCw className="w-3 h-3" />}
                Refresh P&L
              </button>
            </div>

            {/* Strategy purpose — drives grouping + which lifecycle view to show */}
            <div className="flex items-center gap-1" onClick={e => e.stopPropagation()} title="What is this trade for? Drives grouping.">
              <span className="text-[9px] uppercase tracking-wider text-base-content/30">Purpose</span>
              <select
                className="select select-ghost select-xs h-7 min-h-0 text-[10px] border border-white/[0.06]"
                value={tradePurpose(trade)}
                onChange={async e => {
                  try { await updateTradePurpose(trade.id, e.target.value as any); onPositionChanged(trade.id); } catch { /* noop */ }
                }}
              >
                {(['income', 'hedge', 'managed_floor', 'managed_buffer', 'dual_directional', 'trade', 'other'] as const).map(p => (
                  <option key={p} value={p}>{PURPOSE_META[p].label}</option>
                ))}
              </select>
            </div>

            <button
              className="btn btn-ghost btn-xs gap-1 text-[10px] border border-white/[0.06] hover:border-white/15"
              onClick={e => { e.stopPropagation(); onUpdatePosition(); }}
            >
              <PlusCircle className="w-3 h-3" /> Update Position
            </button>

            <button
              className="btn btn-ghost btn-xs gap-1 text-[10px] border border-white/[0.06] hover:border-white/15"
              onClick={e => { e.stopPropagation(); onShowHistory(); }}
            >
              <History className="w-3 h-3" /> History
            </button>

            {/* Add option leg — shown for stock trades that have no options yet */}
            {(isStock || isCoveredCall) && !hasOptionLegsNow && (
              <button
                className={`btn btn-ghost btn-xs gap-1 text-[10px] border ${addingLeg ? 'border-warning/30 text-warning' : 'border-white/[0.06] hover:border-warning/20 hover:text-warning'}`}
                onClick={e => { e.stopPropagation(); setAddingLeg(v => !v); setAddingStock(false); }}
              >
                <Plus className="w-3 h-3" /> Add Option Leg
              </button>
            )}

            {/* Add stock — shown for pure options trades without a stock component */}
            {(group === 'multi_leg' || group === 'income_options') && !hasStockLeg && (
              <button
                className={`btn btn-ghost btn-xs gap-1 text-[10px] border ${addingStock ? 'border-success/30 text-success' : 'border-white/[0.06] hover:border-success/20 hover:text-success'}`}
                onClick={e => { e.stopPropagation(); setAddingStock(v => !v); setAddingLeg(false); }}
              >
                <TrendingUp className="w-3 h-3" /> Add Stock
              </button>
            )}

            <button
              className="btn btn-ghost btn-xs gap-1 text-[10px] border border-white/[0.06] hover:border-white/15 text-secondary"
              onClick={e => { e.stopPropagation(); onCreateAgent(); }}
            >
              <Bot className="w-3 h-3" /> Create Agent
            </button>

            {/* Close position — priced close (records realized P&L); partial or full */}
            {trade.trade_status !== 'closed' && (hasOptionLegsNow || hasStockLeg) && (
              <button
                className="btn btn-ghost btn-xs gap-1 text-[10px] border border-white/[0.06] hover:border-error/30 hover:text-error ml-auto"
                onClick={e => { e.stopPropagation(); setCloseModal({ preselect: null }); }}
                title="Close all or part of this trade at a price (records realized P&L)"
              >
                <LogOut className="w-3 h-3" /> Close
              </button>
            )}

            {/* Delete trade — with inline 2-step confirmation */}
            {!deleteConfirm ? (
              <button
                className={`btn btn-ghost btn-xs gap-1 text-[10px] border border-white/[0.06] hover:border-error/30 hover:text-error ${(trade.trade_status !== 'closed' && (hasOptionLegsNow || hasStockLeg)) ? '' : 'ml-auto'}`}
                onClick={e => { e.stopPropagation(); setDeleteConfirm(true); }}
                title="Delete this trade"
              >
                <Trash2 className="w-3 h-3" /> Delete
              </button>
            ) : (
              <div
                className="flex items-center gap-1.5 bg-error/10 border border-error/20 rounded-lg px-2 py-1"
                onClick={e => e.stopPropagation()}
              >
                <span className="text-[10px] text-error font-medium">Delete trade?</span>
                <button
                  className="btn btn-error btn-xs h-6 min-h-0 gap-1 text-[10px]"
                  onClick={handleDeleteConfirmed}
                  disabled={deleting}
                >
                  {deleting ? <Loader2 className="w-3 h-3 animate-spin" /> : null}
                  Yes, delete
                </button>
                <button
                  className="btn btn-ghost btn-xs h-6 min-h-0 text-[10px]"
                  onClick={e => { e.stopPropagation(); setDeleteConfirm(false); }}
                  disabled={deleting}
                >
                  Cancel
                </button>
              </div>
            )}
          </div>

          {/* Underlying stock context — the SAME chrome the Income desk shows
              (price / earnings / SOFR / HV · IV rank·percentile·skew · events · TA) */}
          {underlyingLoading && !underlying && (
            <div className="flex items-center gap-2 text-xs text-base-content/40 py-3">
              <Loader2 className="w-3.5 h-3.5 animate-spin" /> Loading underlying context…
            </div>
          )}
          {underlying?.context && (
            <TickerChrome
              ticker={underlying.ticker}
              context={underlying.context}
              events={underlying.events}
              expirySummaries={underlying.expiry_summaries}
            />
          )}

          {/* P&L metrics grid */}
          {pnl && (
            <div className="space-y-3">
              {/* Main metrics — BOX + income get their own detailed panels below */}
              {trade.strategy_type !== 'box_spread' && !isIncome && (
                <div className="grid grid-cols-2 sm:grid-cols-4 gap-2">
                  <Chip label="Cost Basis" value={fmtMoney(Math.abs(pnl.entry_cost))} />
                  <Chip label="Current Value" value={fmtMoney(pnl.current_value)} />
                  <Chip
                    label="Unrealized P&L"
                    value={`${pnl.unrealized_pnl >= 0 ? '+' : ''}${fmtMoney(Math.abs(pnl.unrealized_pnl))}`}
                    color={pnl.unrealized_pnl >= 0 ? 'text-success' : 'text-error'}
                  />
                  <Chip label="Days Held" value={`${pnl.days_held}d`} />
                </div>
              )}

              {/* Income-specific analysis */}
              {isIncome && (() => {
                const isBox = trade.strategy_type === 'box_spread';
                const costBasis = Math.abs(pnl.entry_cost);
                const mtmPnl = pnl.unrealized_pnl;
                const guaranteedPnl = pnl.max_profit;  // for BOX this is fixed at expiry

                // Return Till Date: annualized MTM gain from entry to now
                const returnTillDate = costBasis > 0 && held > 0
                  ? (Math.pow(1 + mtmPnl / costBasis, 365 / held) - 1) * 100
                  : null;

                // Pending Ann. Return: from current market value → guaranteed expiry value, annualized over DTE
                // negative means the MTM premium erodes by expiry
                const pendingAnn = isBox && guaranteedPnl != null && dte != null && dte > 0 && pnl.current_value > 0
                  ? (Math.pow(1 + (costBasis + guaranteedPnl - pnl.current_value) / pnl.current_value, 365 / dte) - 1) * 100
                  : null;

                if (isBox) {
                  return (
                    <div className="bg-success/5 border border-success/10 rounded-xl p-3 space-y-3">
                      <div className="text-[9px] uppercase tracking-wider text-success/60 font-semibold">BOX Spread — Position Economics</div>

                      {/* Row 1: cost basis, current value, unrealized P&L */}
                      <div className="grid grid-cols-3 gap-2">
                        <Chip label="Cost Basis" value={fmtMoney(costBasis)} />
                        <Chip label="Current Value" value={fmtMoney(pnl.current_value)} />
                        <Chip
                          label="Unrealized P&L"
                          value={`${mtmPnl >= 0 ? '+' : ''}${fmtMoney(Math.abs(mtmPnl))}`}
                          color={mtmPnl >= 0 ? 'text-success' : 'text-error'}
                        />
                      </div>

                      {/* Row 2: Return Till Date, Guaranteed P&L at Expiry, DTE */}
                      <div className="grid grid-cols-3 gap-2">
                        {returnTillDate != null && (
                          <Chip
                            label="Return Till Date"
                            value={fmtAnnualized(returnTillDate)}
                            color={returnTillDate >= 0 ? 'text-success' : 'text-error'}
                          />
                        )}
                        {guaranteedPnl != null && (
                          <Chip
                            label="P&L at Expiry"
                            value={`+${fmtMoney(guaranteedPnl)}`}
                            color="text-success"
                          />
                        )}
                        {dte != null && (
                          <Chip label="DTE" value={fmtDTE(dte)} color={dte <= 14 ? 'text-warning' : ''} />
                        )}
                      </div>

                      {/* Row 3: Pending Ann. Return from now to expiry */}
                      {pendingAnn != null && (
                        <div className="rounded-lg border border-white/[0.06] px-3 py-2.5 bg-base-300/20">
                          <div className="flex items-center justify-between">
                            <div>
                              <div className="text-[9px] uppercase tracking-wider text-base-content/40">Pending Ann. Return (now → expiry)</div>
                              <div className={`text-sm font-bold mt-0.5 ${pendingAnn >= 0 ? 'text-success' : 'text-warning'}`}>
                                {fmtAnnualized(pendingAnn)}
                              </div>
                            </div>
                            {returnTillDate != null && pendingAnn != null && (
                              <div className="text-right text-[10px] text-base-content/40 max-w-[180px]">
                                {pendingAnn < returnTillDate
                                  ? `MTM gain of ${fmtMoney(Math.abs(mtmPnl))} will partially reverse to ${fmtMoney(guaranteedPnl ?? 0)} at expiry`
                                  : `Locking in ${fmtMoney(guaranteedPnl ?? 0)} guaranteed`
                                }
                              </div>
                            )}
                          </div>
                        </div>
                      )}
                    </div>
                  );
                }

                // Non-BOX income (CSP, credit spreads, covered calls): two clean metric lines
                const spot = pnl.underlying_price;
                const be = pnl.breakevens?.[0] ?? null;
                const cushionPct = (be != null && spot > 0) ? Math.abs((spot - be) / spot) * 100 : null;
                const capital = pnl.total_capital || pnl.margin_required || Math.abs(pnl.entry_cost) || 0;
                const returnAtExpiry = (guaranteedPnl != null && capital > 0) ? (guaranteedPnl / capital) * 100 : null;
                return (
                  <div className="bg-success/5 border border-success/10 rounded-xl p-3 space-y-2">
                    {/* Line 1 — position economics */}
                    <div className="grid grid-cols-3 sm:grid-cols-6 gap-2">
                      <Chip label="Cost Basis" value={fmtMoney(costBasis)} />
                      <Chip label="Current Value" value={fmtMoney(pnl.current_value)} />
                      <Chip label="Unrealized P&L" value={`${mtmPnl >= 0 ? '+' : ''}${fmtMoney(Math.abs(mtmPnl))}`} color={mtmPnl >= 0 ? 'text-success' : 'text-error'} />
                      <Chip label="Margin" value={pnl.margin_required > 0 ? fmtMoney(pnl.margin_required) : '—'} />
                      <Chip label="Ann. Return" value={annReturn != null ? fmtAnnualized(annReturn) : '—'} color={annReturn != null && annReturn >= 5 ? 'text-success' : 'text-warning'} />
                      <Chip label="Trade Date" value={trade.entry_date ? fmtDate(trade.entry_date) : '—'} />
                    </div>
                    {/* Line 2 — payoff at expiry */}
                    <div className="grid grid-cols-3 sm:grid-cols-5 gap-2">
                      <Chip label="Max Profit" value={guaranteedPnl != null ? `+${fmtMoney(guaranteedPnl)}` : '—'} color="text-success" />
                      <Chip label="Max Loss" value={(pnl.max_loss != null && pnl.max_loss < 0) ? fmtMoney(Math.abs(pnl.max_loss)) : (pnl.unbounded_loss ? 'Unbounded' : '—')} color="text-error" />
                      <Chip label="DTE" value={dte != null ? fmtDTE(dte) : '—'} color={dte != null && dte <= 7 ? 'text-warning' : ''} />
                      <div className="flex flex-col items-center px-3 py-1.5 rounded-lg bg-base-300/30 min-w-[72px]">
                        <span className="text-[9px] uppercase tracking-wider text-base-content/40">Breakeven</span>
                        <span className="text-xs font-semibold mt-0.5">{be != null ? fmtMoney(be) : '—'}</span>
                        {cushionPct != null && <span className="text-[9px] text-base-content/40 mt-0.5">{cushionPct.toFixed(1)}% cushion</span>}
                      </div>
                      <Chip label="Return at Expiry" value={returnAtExpiry != null ? `${returnAtExpiry.toFixed(1)}%` : '—'} color={returnAtExpiry != null && returnAtExpiry >= 0 ? 'text-success' : ''} />
                    </div>
                  </div>
                );
              })()}

              {/* Stock-specific: cost basis details + research link (pure stock or no-legs combo) */}
              {(isStock || (isComboLike && hasStockLeg && !hasOptionLegsNow)) && (
                <div className="bg-primary/5 border border-primary/10 rounded-xl p-3 space-y-3">
                  <div className="flex items-center justify-between">
                    <div className="text-[9px] uppercase tracking-wider text-primary/60 font-semibold">Position Details</div>
                    <Link
                      to={`/dashboard?ticker=${trade.ticker}`}
                      className="flex items-center gap-1 text-[10px] text-primary/70 hover:text-primary transition-colors font-medium"
                    >
                      <ExternalLink className="w-3 h-3" />
                      Full Research →
                    </Link>
                  </div>
                  <div className="grid grid-cols-2 sm:grid-cols-4 gap-2">
                    {shares && <Chip label="Shares" value={fmtQty(shares)} />}
                    {entryPrice && <Chip label="Avg Cost" value={fmtMoney(entryPrice)} />}
                    {pnl.underlying_price > 0 && (
                      <Chip label="Current Price" value={fmtMoney(pnl.underlying_price)} />
                    )}
                    <Chip label="Days Held" value={`${held}d`} />
                  </div>
                  {/* Per-share P&L breakdown */}
                  {pnl.underlying_price > 0 && entryPrice && shares && (
                    <div className="flex items-center gap-4 text-xs text-base-content/50 flex-wrap">
                      <span>
                        Per share:{' '}
                        <span className={pnl.underlying_price >= entryPrice ? 'text-success font-medium' : 'text-error font-medium'}>
                          {pnl.underlying_price >= entryPrice ? '+' : ''}{fmtMoney(pnl.underlying_price - entryPrice)}
                          {' '}({fmtPct((pnl.underlying_price - entryPrice) / entryPrice * 100, { signed: true })})
                        </span>
                      </span>
                      <span>
                        Total:{' '}
                        <span className={pnl.unrealized_pnl >= 0 ? 'text-success font-medium' : 'text-error font-medium'}>
                          {pnl.unrealized_pnl >= 0 ? '+' : ''}{fmtMoney(Math.abs(pnl.unrealized_pnl))}
                        </span>
                      </span>
                    </div>
                  )}
                  {/* Quick links to research modules on the dashboard */}
                  <div className="flex flex-wrap gap-1.5 pt-0.5">
                    {([
                      { label: 'Analyst Ratings',    tab: 'overview' },
                      { label: 'DCF Valuation',       tab: 'fundamental' },
                      { label: 'Technical Analysis',  tab: 'technical' },
                      { label: 'Financial Health',    tab: 'fundamental' },
                    ] as const).map(({ label, tab }) => (
                      <Link
                        key={label}
                        to={`/dashboard?ticker=${trade.ticker}&tab=${tab}`}
                        className="badge badge-ghost badge-sm text-[9px] hover:badge-primary transition-colors"
                      >
                        {label}
                      </Link>
                    ))}
                    {/* Exit link — pre-populates position data */}
                    <Link
                      to={`/dashboard?ticker=${trade.ticker}&tab=exit${
                        shares != null ? `&shares=${shares}` : ''
                      }${
                        entryPrice != null ? `&cost=${entryPrice}` : ''
                      }${
                        trade.entry_date ? `&date=${trade.entry_date.split('T')[0]}` : ''
                      }`}
                      className="badge badge-ghost badge-sm text-[9px] hover:badge-error transition-colors"
                    >
                      Exit Analysis
                    </Link>
                  </div>
                </div>
              )}

              {/* Futures-specific: contracts, avg cost, current price, multiplier, DTE, P&L */}
              {isFutures && (
                <div className="bg-primary/5 border border-primary/10 rounded-xl p-3 space-y-3">
                  <div className="flex items-center justify-between">
                    <div className="text-[9px] uppercase tracking-wider text-primary/60 font-semibold">Position Details</div>
                    <Link
                      to={`/dashboard?ticker=${trade.ticker}`}
                      className="flex items-center gap-1 text-[10px] text-primary/70 hover:text-primary transition-colors font-medium"
                    >
                      <ExternalLink className="w-3 h-3 animate-pulse" />
                      Full Research →
                    </Link>
                  </div>
                  <div className="grid grid-cols-2 sm:grid-cols-4 gap-2">
                    {contracts && <Chip label="Contracts" value={fmtQty(contracts)} />}
                    {entryPrice && <Chip label="Avg Price" value={fmtMoney(entryPrice)} />}
                    {pnl.underlying_price > 0 && (
                      <Chip label="Current Price" value={fmtMoney(pnl.underlying_price)} />
                    )}
                    {multiplier !== 1 && <Chip label="Multiplier" value={`×${multiplier}`} />}
                    <Chip label="Days Held" value={`${held}d`} />
                    {dte != null && (
                      <Chip label="DTE" value={fmtDTE(dte)} color={dte <= 7 ? 'text-warning' : ''} />
                    )}
                  </div>
                  {/* P&L breakdown */}
                  {pnl.underlying_price > 0 && entryPrice && contracts && (
                    <div className="flex items-center gap-4 text-xs text-base-content/50 flex-wrap">
                      <span>
                        Price Diff:{' '}
                        <span className={pnl.underlying_price >= entryPrice ? 'text-success font-medium' : 'text-error font-medium'}>
                          {pnl.underlying_price >= entryPrice ? '+' : ''}{(pnl.underlying_price - entryPrice).toFixed(2)}
                        </span>
                      </span>
                      <span>
                        Total P&L:{' '}
                        <span className={pnl.unrealized_pnl >= 0 ? 'text-success font-medium' : 'text-error font-medium'}>
                          {pnl.unrealized_pnl >= 0 ? '+' : ''}{fmtMoney(Math.abs(pnl.unrealized_pnl))}
                        </span>
                      </span>
                    </div>
                  )}
                </div>
              )}

              {/* Combo/CoveredCall breakdown: stock P&L + options P&L */}
              {isComboLike && hasStockLeg && hasOptionLegsNow && pnl && (() => {
                const ob = pnl.options_breakdown;
                const stockShares = trade.parameters?.shares;
                const stockAvg = trade.parameters?.avg_cost ?? trade.entry_prices?.[0]?.price;
                return (
                <div className="space-y-2">
                  <div className="flex items-center justify-between">
                    <div className="text-[9px] uppercase tracking-wider text-warning/60 font-semibold">
                      {isCoveredCall ? 'Covered Call Breakdown' : 'Stock + Options Breakdown'}
                    </div>
                    <Link
                      to={`/dashboard?ticker=${trade.ticker}`}
                      className="flex items-center gap-1 text-[10px] text-warning/70 hover:text-warning transition-colors font-medium"
                    >
                      <ExternalLink className="w-3 h-3" /> Full Research →
                    </Link>
                  </div>

                  {/* ── SECTION 1: Stock Leg ── */}
                  <div className="bg-primary/5 border border-primary/10 rounded-lg p-2.5 space-y-1.5">
                    <div className="text-[9px] uppercase text-primary/60 tracking-wider font-semibold">1 · Stock Leg</div>
                    <div className="grid grid-cols-2 sm:grid-cols-5 gap-2">
                      {stockShares && <Chip label="Shares" value={fmtQty(stockShares)} />}
                      {stockAvg && <Chip label="Avg Cost" value={fmtMoney(stockAvg)} />}
                      {pnl.underlying_price > 0 && <Chip label="Current Price" value={fmtMoney(pnl.underlying_price)} />}
                      {pnl.stock_pnl != null && (
                        <Chip label="Stock P&L"
                          value={`${pnl.stock_pnl >= 0 ? '+' : ''}${fmtMoney(Math.abs(pnl.stock_pnl))}`}
                          color={pnl.stock_pnl >= 0 ? 'text-success' : 'text-error'} />
                      )}
                      <Chip label="Days Held" value={`${held}d`} />
                    </div>
                  </div>

                  {/* ── SECTION 2: Option Leg — identical metric grids to a standalone options trade ── */}
                  <div className="bg-info/5 border border-info/10 rounded-lg p-2.5 space-y-2">
                    <div className="text-[9px] uppercase text-info/60 tracking-wider font-semibold">2 · Option Leg (options overlay)</div>
                    {ob && (
                      <>
                        {/* P&L grid — same as options-only */}
                        <div className="grid grid-cols-2 sm:grid-cols-4 gap-2">
                          <Chip label="Cost Basis" value={fmtMoney(ob.cost_basis)} />
                          <Chip label="Current Value" value={fmtMoney(ob.current_value)} />
                          <Chip label="Unrealized P&L"
                            value={`${ob.options_pnl >= 0 ? '+' : ''}${fmtMoney(Math.abs(ob.options_pnl))}`}
                            color={ob.options_pnl >= 0 ? 'text-success' : 'text-error'} />
                          <Chip label="Days Held" value={`${ob.days_held}d`} />
                        </div>
                        {/* Greeks grid — same as options-only */}
                        <div className="grid grid-cols-2 sm:grid-cols-4 gap-2">
                          <Chip label="Net Delta" value={`${ob.net_delta >= 0 ? '+' : ''}${ob.net_delta.toFixed(2)}`}
                            color={ob.net_delta >= 0 ? 'text-success/80' : 'text-error/80'} />
                          <Chip label="Net Theta" value={`$${ob.net_theta.toFixed(2)}/d`} color={ob.net_theta > 0 ? 'text-success/80' : 'text-warning/80'} />
                          <Chip label="Net Vega" value={ob.net_vega.toFixed(2)} />
                          <Chip label="Net Gamma" value={ob.net_gamma.toFixed(4)} />
                        </div>
                        {/* Quant grid — options-only max gain/loss, PoP, EV, Kelly */}
                        <div className="grid grid-cols-2 sm:grid-cols-4 gap-2">
                          <div className="bg-base-300/20 rounded-lg p-2 text-center">
                            <div className="text-[9px] uppercase text-base-content/30">Max Gain (opt)</div>
                            <div className="text-xs font-semibold mt-0.5 text-success">
                              {ob.unbounded_profit ? 'Unlimited' : ob.max_profit != null ? fmtMoney(ob.max_profit) : '—'}
                            </div>
                          </div>
                          <div className="bg-base-300/20 rounded-lg p-2 text-center">
                            <div className="text-[9px] uppercase text-base-content/30">Max Loss (opt)</div>
                            <div className="text-xs font-semibold mt-0.5 text-error">
                              {ob.unbounded_loss ? 'Unlimited' : ob.max_loss != null ? fmtMoney(ob.max_loss) : '—'}
                            </div>
                          </div>
                          {ob.pop != null && (
                            <div className="bg-base-300/20 rounded-lg p-2 text-center">
                              <div className="text-[9px] uppercase text-base-content/30">Prob of Profit</div>
                              <div className={`text-xs font-semibold mt-0.5 ${ob.pop >= 60 ? 'text-success' : ob.pop >= 40 ? 'text-warning' : 'text-error'}`}>{ob.pop.toFixed(1)}%</div>
                            </div>
                          )}
                          {ob.expected_value != null && (
                            <div className="bg-base-300/20 rounded-lg p-2 text-center">
                              <div className="text-[9px] uppercase text-base-content/30">Expected Value</div>
                              <div className={`text-xs font-semibold mt-0.5 ${ob.expected_value >= 0 ? 'text-success' : 'text-error'}`}>{fmtMoney(ob.expected_value)}</div>
                            </div>
                          )}
                          {ob.kelly_fraction != null && (
                            <div className="bg-base-300/20 rounded-lg p-2 text-center">
                              <div className="text-[9px] uppercase text-base-content/30">Kelly Size</div>
                              <div className="text-xs font-semibold mt-0.5">{(ob.kelly_fraction * 100).toFixed(1)}%</div>
                            </div>
                          )}
                        </div>
                      </>
                    )}
                    <div className="text-[9px] text-base-content/30">Per-leg P(ITM) &amp; close/hold/roll advice in the Option Legs table below.</div>
                  </div>

                  {/* ── SECTION 3: Stock + Option Combined ── */}
                  <div className="bg-warning/5 border border-warning/10 rounded-lg p-2.5 space-y-1.5">
                    <div className="text-[9px] uppercase text-warning/60 tracking-wider font-semibold">3 · Stock + Option Combined</div>
                    <div className="grid grid-cols-2 sm:grid-cols-4 gap-2">
                      <Chip label="Combined P&L"
                        value={`${pnl.unrealized_pnl >= 0 ? '+' : ''}${fmtMoney(Math.abs(pnl.unrealized_pnl))}`}
                        color={pnl.unrealized_pnl >= 0 ? 'text-success' : 'text-error'} />
                      {pnl.net_greeks?.delta != null && (
                        <Chip label="Portfolio Δ Delta"
                          value={`${pnl.net_greeks.delta >= 0 ? '+' : ''}${pnl.net_greeks.delta.toFixed(2)}`}
                          color={pnl.net_greeks.delta >= 0 ? 'text-success/80' : 'text-error/80'} />
                      )}
                      {pnl.breakevens?.length > 0 && (
                        <Chip label="Breakeven" value={pnl.breakevens.map(b => `$${b.toFixed(2)}`).join(' / ')} />
                      )}
                      {pnl.analysis?.probability_of_profit != null && (
                        <Chip label="Prob of Profit" value={`${pnl.analysis.probability_of_profit.toFixed(1)}%`}
                          color={pnl.analysis.probability_of_profit >= 60 ? 'text-success' : pnl.analysis.probability_of_profit >= 40 ? 'text-warning' : 'text-error'} />
                      )}
                      {pnl.analysis?.expected_value != null && (
                        <Chip label="Expected Value"
                          value={`${pnl.analysis.expected_value >= 0 ? '+' : ''}${fmtMoney(Math.abs(pnl.analysis.expected_value))}`}
                          color={pnl.analysis.expected_value >= 0 ? 'text-success' : 'text-error'} />
                      )}
                    </div>
                    <div className="text-[10px] text-base-content/40">
                      {pnl.pnl_pct >= 0 ? '+' : ''}{pnl.pnl_pct.toFixed(2)}% total return on deployed capital · max gain / loss &amp; payoff below.
                    </div>
                  </div>
                </div>
                );
              })()}

              {/* Futures Metrics — show only for futures */}
              {trade.strategy_type === 'futures' && (
                <div className="grid grid-cols-2 sm:grid-cols-4 gap-2">
                  <Chip
                    label="Notional Value"
                    value={fmtMoney((pnl.underlying_price || entryPrice || 0) * (contracts || 1) * multiplier)}
                  />
                  <Chip
                    label="Margin Required"
                    value={params.margin_req ? fmtMoney(parseFloat(params.margin_req)) : '—'}
                  />
                  <Chip
                    label="Leverage"
                    value={
                      params.margin_req && parseFloat(params.margin_req) > 0
                        ? `${(((pnl.underlying_price || entryPrice || 0) * (contracts || 1) * multiplier) / parseFloat(params.margin_req)).toFixed(1)}x`
                        : '—'
                    }
                  />
                  <Chip
                    label="Notional Delta"
                    value={`${params.action === 'short' ? '-' : '+'}${fmtMoney(
                      (pnl.underlying_price || entryPrice || 0) * (contracts || 1) * multiplier
                    )}`}
                    color={params.action === 'short' ? 'text-error/80' : 'text-success/80'}
                  />
                </div>
              )}

            </div>
          )}


          {/* Legs table — compact, with per-leg roll/close actions */}
          {trade.legs_data && trade.legs_data.length > 0 && (
            <div className="space-y-2">
              <div className="flex items-center justify-between">
                <div className="text-[9px] uppercase tracking-wider text-base-content/30">Option Legs</div>
                {/* Add leg available for all non-income trades */}
                {group !== 'income_options' && (
                  <button
                    className="btn btn-ghost btn-xs gap-1 text-[10px] text-base-content/50 hover:text-base-content"
                    onClick={e => { e.stopPropagation(); setAddingLeg(v => !v); setAddingStock(false); }}
                  >
                    <Plus className="w-3 h-3" /> Add option leg
                  </button>
                )}
              </div>
              <div className="overflow-x-auto">
                <table className="table table-xs text-xs w-full">
                  <thead>
                    <tr className="text-[8px] uppercase text-base-content/25">
                      <th>Action</th><th>Type</th><th>Strike</th><th>Qty</th><th>Expiry</th><th>Entry</th>
                      {pnl && <><th>Bid</th><th>Ask</th><th>Mid</th><th>Leg P&L</th><th>Δ Delta</th><th>Θ Decay/d</th><th title="Risk-neutral probability this leg finishes in-the-money at its strike">P(ITM)</th><th title="Deterministic quant advice for this leg">Advice</th></>}
                      <th></th>
                    </tr>
                  </thead>
                  <tbody>
                    {trade.legs_data.map((leg: any, i: number) => {
                      const q = pnl?.current_quotes?.find((cq: any) => cq.leg === i);
                      const gk = pnl?.greeks?.find((g: any) => g.leg === i);
                      const la = pnl?.leg_analysis?.find(a => a.leg === i);
                      // For combo trades, entry_prices[0] is the stock leg; option legs start at [1]
                      const epIdx = isComboLike ? i + 1 : i;
                      const ep = trade.entry_prices?.[epIdx]?.price ?? leg.premium ?? leg.mid ?? leg.price ?? null;
                      const isRolling = rollState?.legIdx === i;
                      
                      // Calculate option leg P&L: (current_mid - entry_price) * action_sign * qty * 100
                      const cm = q?.mid ?? null;
                      const sign = leg.action?.toUpperCase().includes('BUY') ? 1 : -1;
                      const legPnl = (cm != null && ep != null) ? (cm - ep) * sign * (leg.qty ?? 1) * 100 : null;
                      return (
                        <React.Fragment key={i}>
                          <tr className="hover:bg-base-300/10">
                            {editingLegIdx === i ? (
                              <>
                                <td>
                                  <select
                                    className="select select-bordered select-xs w-20"
                                    value={editLegState.action}
                                    onChange={e => setEditLegState(s => ({ ...s, action: e.target.value as 'buy' | 'sell' }))}
                                  >
                                    <option value="buy">Buy</option>
                                    <option value="sell">Sell</option>
                                  </select>
                                </td>
                                <td>
                                  <select
                                    className="select select-bordered select-xs w-20 capitalize"
                                    value={editLegState.type}
                                    onChange={e => setEditLegState(s => ({ ...s, type: e.target.value as 'call' | 'put' }))}
                                  >
                                    <option value="call">Call</option>
                                    <option value="put">Put</option>
                                  </select>
                                </td>
                                <td>
                                  <input
                                    type="number"
                                    step="0.5"
                                    className="input input-bordered input-xs w-20 font-mono"
                                    value={editLegState.strike}
                                    onChange={e => setEditLegState(s => ({ ...s, strike: e.target.value }))}
                                  />
                                </td>
                                <td>
                                  <input
                                    type="number"
                                    min="1"
                                    step="1"
                                    className="input input-bordered input-xs w-16"
                                    value={editLegState.qty}
                                    onChange={e => setEditLegState(s => ({ ...s, qty: parseInt(e.target.value) || 1 }))}
                                  />
                                </td>
                                <td>
                                  <input
                                    type="date"
                                    className="input input-bordered input-xs w-32 font-mono text-[10px]"
                                    value={editLegState.expiration}
                                    onChange={e => setEditLegState(s => ({ ...s, expiration: e.target.value }))}
                                  />
                                </td>
                                <td>
                                  <div className="relative">
                                    <span className="absolute left-1 top-1/2 -translate-y-1/2 opacity-40">$</span>
                                    <input
                                      type="number"
                                      step="0.01"
                                      className="input input-bordered input-xs w-20 pl-3.5 font-mono"
                                      value={editLegState.entry}
                                      onChange={e => setEditLegState(s => ({ ...s, entry: e.target.value }))}
                                    />
                                  </div>
                                </td>
                                {pnl && (
                                  <td colSpan={8} className="text-base-content/30 italic text-[10px] text-center">
                                    Quotes & greeks disabled during edit
                                  </td>
                                )}
                                <td>
                                  <div className="flex gap-1">
                                    <button
                                      className="btn btn-success btn-xs h-5 min-h-0 gap-0.5 text-[9px]"
                                      onClick={e => { e.stopPropagation(); commitEditLeg(i); }}
                                      disabled={editLegState.saving}
                                    >
                                      {editLegState.saving ? <Loader2 className="w-2.5 h-2.5 animate-spin" /> : <Check className="w-2.5 h-2.5" />} Save
                                    </button>
                                    <button
                                      className="btn btn-ghost btn-xs h-5 min-h-0 text-[9px]"
                                      onClick={e => { e.stopPropagation(); setEditingLegIdx(null); }}
                                    >
                                      Cancel
                                    </button>
                                  </div>
                                </td>
                              </>
                            ) : (
                              <>
                                <td className={leg.action?.toUpperCase().includes('BUY') ? 'text-success/80' : 'text-error/80'}>
                                  {leg.action?.toUpperCase()}
                                </td>
                                <td className="capitalize">{leg.type}</td>
                                <td className="font-mono">{fmtMoney(leg.strike)}</td>
                                <td>{leg.qty ?? 1}</td>
                                <td className="text-base-content/50 font-mono text-[10px]">{leg.expiration || leg.expiry || expiry || '—'}</td>
                                <td className="font-mono">{ep != null ? `$${Number(ep).toFixed(2)}` : '—'}</td>
                                {pnl && <>
                                  <td className="font-mono text-base-content/50">{q?.bid != null ? `$${q.bid.toFixed(2)}` : '—'}</td>
                                  <td className="font-mono text-base-content/50">{q?.ask != null ? `$${q.ask.toFixed(2)}` : '—'}</td>
                                  <td className="font-mono">{q?.mid != null ? `$${q.mid.toFixed(2)}` : q?.error || '—'}</td>
                                  <td className={`font-mono text-[10px] ${legPnl != null ? (legPnl >= 0 ? 'text-success' : 'text-error') : 'text-base-content/25'}`}>
                                    {legPnl != null ? fmtMoney(legPnl, { signed: true }) : '—'}
                                  </td>
                                  <td className={`font-mono text-[10px] ${gk?.delta != null ? (leg.action === 'buy' ? 'text-success/80' : 'text-error/80') : 'text-base-content/25'}`}>
                                    {gk?.delta != null
                                      ? `${(gk.delta * (leg.action === 'buy' ? 1 : -1)).toFixed(3)}`
                                      : '—'}
                                  </td>
                                  <td className={`font-mono text-[10px] ${gk?.theta != null ? 'text-warning/70' : 'text-base-content/25'}`}>
                                    {gk?.theta != null
                                      ? `$${(gk.theta * (leg.qty ?? 1) * 100).toFixed(2)}/d`
                                      : '—'}
                                  </td>
                                  <td className={`font-mono text-[10px] ${pItmColor(la?.p_itm_pct ?? null)}`}
                                      title={la?.prob_source ? `Source: ${la.prob_source === 'rnd' ? 'market-implied RND' : la.prob_source === 'lognormal' ? 'Black-Scholes' : 'delta proxy'}` : undefined}>
                                    {la?.p_itm_pct != null ? `${la.p_itm_pct.toFixed(0)}%` : '—'}
                                  </td>
                                  <td><LegActionBadge advice={la} /></td>
                                </>}
                                <td>
                                  <div className="flex gap-1">
                                    <button
                                      className="btn btn-ghost btn-xs h-5 min-h-0 gap-0.5 text-[9px] text-base-content/40 hover:text-info"
                                      onClick={e => { e.stopPropagation(); startEditingLeg(i); }}
                                      title="Edit this leg"
                                    >
                                      <Pencil className="w-2.5 h-2.5" /> Edit
                                    </button>
                                    <button
                                      className={`btn btn-ghost btn-xs h-5 min-h-0 gap-0.5 text-[9px] ${isRolling ? 'text-warning' : 'text-base-content/40 hover:text-warning'}`}
                                      onClick={e => { e.stopPropagation(); isRolling ? setRollState(null) : startRoll(i); }}
                                      title="Roll this leg (close + open new)"
                                    >
                                      <RotateCcw className="w-2.5 h-2.5" /> Roll
                                    </button>
                                    <button
                                      className="btn btn-ghost btn-xs h-5 min-h-0 gap-0.5 text-[9px] text-base-content/40 hover:text-error"
                                      onClick={e => { e.stopPropagation(); setCloseModal({ preselect: i }); }}
                                      title="Close this leg at a price (records realized P&L)"
                                    >
                                      <LogOut className="w-2.5 h-2.5" /> Close
                                    </button>
                                  </div>
                                </td>
                              </>
                            )}
                          </tr>
                          {editingLegIdx === i && editLegState.error && (
                            <tr onClick={e => e.stopPropagation()}>
                              <td colSpan={pnl ? 15 : 7} className="text-xs text-error p-1.5 bg-error/5 font-semibold">
                                {editLegState.error}
                              </td>
                            </tr>
                          )}
                          {/* Inline roll form */}
                          {isRolling && rollState && (
                            <tr>
                              <td colSpan={pnl ? 15 : 7} className="p-0">
                                <div className="bg-warning/5 border border-warning/15 rounded-lg m-1 p-2.5 space-y-2" onClick={e => e.stopPropagation()}>
                                  <div className="text-[9px] uppercase tracking-wider text-warning/60 font-semibold">Roll Leg {i + 1}</div>
                                  <div className="grid grid-cols-2 sm:grid-cols-4 gap-2">
                                    <div>
                                      <label className="text-[9px] text-base-content/40 block mb-0.5">Close at ($/share)</label>
                                      <input type="number" step="0.01" placeholder="current mid"
                                        className="input input-bordered input-xs w-full"
                                        value={rollState.closePrice}
                                        onChange={e => setRollState(s => s && ({ ...s, closePrice: e.target.value }))}
                                      />
                                    </div>
                                    <div>
                                      <label className="text-[9px] text-base-content/40 block mb-0.5">New action</label>
                                      <div className="flex gap-1">
                                        {(['buy', 'sell'] as const).map(a => (
                                          <button key={a} type="button"
                                            className={`px-2 py-1 rounded text-[10px] font-semibold border ${rollState.newAction === a ? (a === 'buy' ? 'border-success/40 bg-success/10 text-success' : 'border-error/40 bg-error/10 text-error') : 'border-white/[0.06] text-base-content/40'}`}
                                            onClick={() => setRollState(s => s && ({ ...s, newAction: a }))}
                                          >{a === 'buy' ? 'Buy' : 'Sell'}</button>
                                        ))}
                                      </div>
                                    </div>
                                    <div>
                                      <label className="text-[9px] text-base-content/40 block mb-0.5">Type</label>
                                      <div className="flex gap-1">
                                        {(['call', 'put'] as const).map(t => (
                                          <button key={t} type="button"
                                            className={`px-2 py-1 rounded text-[10px] font-semibold border capitalize ${rollState.newType === t ? 'border-primary/40 bg-primary/10 text-primary' : 'border-white/[0.06] text-base-content/40'}`}
                                            onClick={() => setRollState(s => s && ({ ...s, newType: t }))}
                                          >{t}</button>
                                        ))}
                                      </div>
                                    </div>
                                    <div>
                                      <label className="text-[9px] text-base-content/40 block mb-0.5">Contracts</label>
                                      <input type="number" min={1} step={1}
                                        className="input input-bordered input-xs w-full"
                                        value={rollState.newContracts}
                                        onChange={e => setRollState(s => s && ({ ...s, newContracts: parseInt(e.target.value) || 1 }))}
                                      />
                                    </div>
                                    <div>
                                      <label className="text-[9px] text-base-content/40 block mb-0.5">New strike</label>
                                      <input type="number" step="0.5" placeholder="Strike"
                                        className="input input-bordered input-xs w-full"
                                        value={rollState.newStrike}
                                        onChange={e => setRollState(s => s && ({ ...s, newStrike: e.target.value }))}
                                      />
                                    </div>
                                    <div>
                                      <label className="text-[9px] text-base-content/40 block mb-0.5">New expiry</label>
                                      <input type="date"
                                        className="input input-bordered input-xs w-full"
                                        value={rollState.newExpiration}
                                        onChange={e => setRollState(s => s && ({ ...s, newExpiration: e.target.value }))}
                                      />
                                    </div>
                                    <div>
                                      <label className="text-[9px] text-base-content/40 block mb-0.5">New premium/share</label>
                                      <input type="number" step="0.01" placeholder="12.50"
                                        className="input input-bordered input-xs w-full"
                                        value={rollState.newPremium}
                                        onChange={e => setRollState(s => s && ({ ...s, newPremium: e.target.value }))}
                                      />
                                    </div>
                                  </div>
                                  {rollState.error && <p className="text-xs text-error">{rollState.error}</p>}
                                  <div className="flex gap-2">
                                    <button
                                      className="btn btn-warning btn-xs gap-1"
                                      onClick={commitRoll}
                                      disabled={rollState.saving}
                                    >
                                      {rollState.saving ? <Loader2 className="w-3 h-3 animate-spin" /> : <RotateCcw className="w-3 h-3" />}
                                      Confirm Roll
                                    </button>
                                    <button className="btn btn-ghost btn-xs" onClick={() => setRollState(null)}>Cancel</button>
                                  </div>
                                </div>
                              </td>
                            </tr>
                          )}
                        </React.Fragment>
                      );
                    })}
                  </tbody>
                </table>
              </div>

              {/* Add leg inline form */}
              {addingLeg && (
                <div className="bg-base-300/20 border border-white/[0.06] rounded-lg p-2.5 space-y-2" onClick={e => e.stopPropagation()}>
                  <div className="text-[9px] uppercase tracking-wider text-base-content/30 font-semibold">New Option Leg</div>
                  <div className="grid grid-cols-2 sm:grid-cols-3 gap-2">
                    <div>
                      <label className="text-[9px] text-base-content/40 block mb-0.5">Action</label>
                      <div className="flex gap-1">
                        {(['buy', 'sell'] as const).map(a => (
                          <button key={a} type="button"
                            className={`px-2 py-1 rounded text-[10px] font-semibold border ${newLegState.action === a ? (a === 'buy' ? 'border-success/40 bg-success/10 text-success' : 'border-error/40 bg-error/10 text-error') : 'border-white/[0.06] text-base-content/40'}`}
                            onClick={() => setNewLegState(s => ({ ...s, action: a }))}
                          >{a === 'buy' ? 'Buy' : 'Sell'}</button>
                        ))}
                      </div>
                    </div>
                    <div>
                      <label className="text-[9px] text-base-content/40 block mb-0.5">Type</label>
                      <div className="flex gap-1">
                        {(['call', 'put'] as const).map(t => (
                          <button key={t} type="button"
                            className={`px-2 py-1 rounded text-[10px] font-semibold border capitalize ${newLegState.type === t ? 'border-primary/40 bg-primary/10 text-primary' : 'border-white/[0.06] text-base-content/40'}`}
                            onClick={() => setNewLegState(s => ({ ...s, type: t }))}
                          >{t}</button>
                        ))}
                      </div>
                    </div>
                    <div>
                      <label className="text-[9px] text-base-content/40 block mb-0.5">Contracts</label>
                      <input type="number" min={1} step={1}
                        className="input input-bordered input-xs w-full"
                        value={newLegState.contracts}
                        onChange={e => setNewLegState(s => ({ ...s, contracts: parseInt(e.target.value) || 1 }))}
                      />
                    </div>
                    <div>
                      <label className="text-[9px] text-base-content/40 block mb-0.5">Strike $</label>
                      <input type="number" step="0.5" placeholder="900"
                        className="input input-bordered input-xs w-full"
                        value={newLegState.strike}
                        onChange={e => setNewLegState(s => ({ ...s, strike: e.target.value }))}
                      />
                    </div>
                    <div>
                      <label className="text-[9px] text-base-content/40 block mb-0.5">Expiry</label>
                      <input type="date"
                        className="input input-bordered input-xs w-full"
                        value={newLegState.expiration}
                        onChange={e => setNewLegState(s => ({ ...s, expiration: e.target.value }))}
                      />
                    </div>
                    <div>
                      <label className="text-[9px] text-base-content/40 block mb-0.5">Premium/share $</label>
                      <input type="number" step="0.01" placeholder="12.50"
                        className="input input-bordered input-xs w-full"
                        value={newLegState.premium}
                        onChange={e => setNewLegState(s => ({ ...s, premium: e.target.value }))}
                      />
                    </div>
                  </div>
                  {newLegState.error && <p className="text-xs text-error">{newLegState.error}</p>}
                  <div className="flex gap-2">
                    <button
                      className="btn btn-primary btn-xs gap-1"
                      onClick={commitAddLeg}
                      disabled={newLegState.saving}
                    >
                      {newLegState.saving ? <Loader2 className="w-3 h-3 animate-spin" /> : <Plus className="w-3 h-3" />}
                      Add Leg
                    </button>
                    <button className="btn btn-ghost btn-xs" onClick={() => setAddingLeg(false)}>Cancel</button>
                  </div>
                </div>
              )}
            </div>
          )}

          {/* Quant advisor — overall recommendation folding per-leg + structure */}
          {pnl?.analysis?.recommendation && (() => {
            const exitSig = pnl.analysis.exit_signal || 'HOLD';
            const tone = exitSig === 'STRONG_CLOSE' ? 'error' : exitSig === 'CLOSE' ? 'warning' : 'success';
            return (
            <div className={`rounded-lg p-3 border bg-${tone}/5 border-${tone}/20`}>
              <div className="flex items-center gap-2 mb-1.5 flex-wrap">
                <span className={`badge badge-sm font-semibold ${EXIT_STYLE[exitSig]?.cls || 'badge-ghost'}`}>
                  {EXIT_STYLE[exitSig]?.label || exitSig}
                </span>
                <span className="text-[9px] uppercase tracking-wider text-base-content/40">Deterministic rules · whole-trade exit call</span>
                {pnl.analysis.captured_pct != null && (
                  <span className="text-[9px] text-base-content/40">{pnl.analysis.captured_pct.toFixed(0)}% of max profit captured</span>
                )}
                {pnl.analysis.pop_method === 'rnd' && (
                  <span className="text-[8px] uppercase tracking-wider text-primary/60" title="Probabilities from the market-implied risk-neutral density (SVI/RND)">RND</span>
                )}
              </div>
              {pnl.analysis.exit_reasons && pnl.analysis.exit_reasons.length > 0 && (
                <ul className="space-y-0.5 mb-2">
                  {pnl.analysis.exit_reasons.map((r, i) => (
                    <li key={i} className={`text-[10px] flex gap-1.5 ${i === 0 ? `text-${tone} font-medium` : 'text-base-content/60'}`}>
                      <span className="opacity-40">•</span><span>{r}</span>
                    </li>
                  ))}
                </ul>
              )}
              {pnl.analysis.recommendation.outcome && (
                <div className="text-[10px] text-base-content/50 mb-1.5">{pnl.analysis.recommendation.outcome}</div>
              )}
              <div className="text-xs text-base-content/90 mb-2 font-medium">{pnl.analysis.recommendation.headline}</div>
              {pnl.analysis.recommendation.leg_notes.length > 0 && (
                <ul className="space-y-1 mb-2">
                  {pnl.analysis.recommendation.leg_notes.map((n, i) => (
                    <li key={i} className="text-[10px] text-base-content/60 flex gap-1.5">
                      <span className="text-base-content/30">•</span><span>{n}</span>
                    </li>
                  ))}
                </ul>
              )}
              {pnl.analysis.recommendation.reasons.length > 0 && (
                <div className="flex flex-wrap gap-1.5">
                  {pnl.analysis.recommendation.reasons.map((rsn, i) => (
                    <span key={i} className="badge badge-ghost badge-xs text-[9px] text-base-content/50">{rsn}</span>
                  ))}
                </div>
              )}
            </div>
            );
          })()}

          {/* Tier 2 — QUANT ALGORITHMIC exit (scored, deterministic, auditable) */}
          {pnl?.analysis?.quant_exit && (
            <QuantExitCard q={pnl.analysis.quant_exit} trade={trade} pnl={pnl} deskFocus={deskFocusForTrade(trade, pnl)} />
          )}

          {/* Tier 3 — Institutional Desk (LLM quant PM manages the trade) */}
          {pnl?.analysis && (
            <InstitutionalDesk trade={trade} pnl={pnl} />
          )}

          {/* Desk sections — the SAME layout as the Income desk single-ticker view:
              Dynamic Greeks · Capital Risk · Risk-Adjusted Quality */}
          {pnl?.lifecycle && hasOptionLegsNow && (() => {
            const lc = pnl.lifecycle!;
            return (
              <div className="space-y-2">
                {lc.trader && (
                  <CollapsibleSection title="Dynamic Greeks" accent="info"
                    icon={<Activity className="w-3 h-3" />} subtitle="Δ · Γ · ν · Θ · Vanna · Charm · Volga">
                    <TraderGrid t={{ ...(lc.trader as any), avg_iv_pct: lc.avg_iv_pct }} />
                  </CollapsibleSection>
                )}
                {lc.risk && (
                  <CollapsibleSection title="Capital Risk" accent="warning"
                    icon={<Shield className="w-3 h-3" />} subtitle="VaR · CVaR · max loss · sizing">
                    <RiskGrid r={lc.risk} />
                  </CollapsibleSection>
                )}
                {lc.pm && (
                  <CollapsibleSection title="Risk Adjusted Quality" accent="success"
                    icon={<LineChart className="w-3 h-3" />} subtitle="Omega · Sortino · Calmar · PoP · EV · Kelly">
                    <PmGrid pm={{
                      ...(lc.pm as any),
                      pop: pnl.analysis?.probability_of_profit,
                      expected_value: pnl.analysis?.expected_value,
                      kelly_fraction: pnl.analysis?.kelly_fraction,
                    }} />
                  </CollapsibleSection>
                )}
              </div>
            );
          })()}

          {/* Payoff diagram — P&L vs underlying (all trade types), collapsed by default */}
          {pnl && pnl.scenarios && pnl.scenarios.length > 1 && (
            <CollapsibleSection title="Payoff diagram" accent="base-content"
              icon={<BarChart3 className="w-3 h-3" />} subtitle="P&L across underlying moves">
              <PayoffChart pnl={pnl} />
            </CollapsibleSection>
          )}

          {/* Continuous Lifecycle Management — Desk Debate (Explorer) */}
          {(() => {
            const deskFocus = deskFocusForTrade(trade, pnl);
            if (!deskFocus) return null;
            return (
              <div className="space-y-1.5 pt-2">
                <div className="text-[9px] uppercase tracking-wider text-base-content/40 font-semibold flex items-center gap-1.5 mb-2">
                  <Activity className="w-3 h-3" /> Continuous Lifecycle Management
                </div>
                <div className="rounded-xl border border-white/[0.06] bg-base-200/30 p-4 hover:border-secondary/30 transition-colors">
                  <DeskDebate
                    ticker={trade.ticker}
                    params={{
                      quote_source: quoteSource,
                      target_dte: dte ?? undefined,
                      structures: [deskFocus.structure],
                      min_prob: 0,
                      min_income: 0,
                    }}
                    focus={deskFocus}
                    label="Review hold or exit"
                  />
                  <p className="text-[11px] text-base-content/40 mt-2">
                    Run the institutional desk (Quant → Risk → Rebuttal → PM) to evaluate whether to hold, adjust, or exit this existing position.
                  </p>
                </div>
              </div>
            );
          })()}

          {/* Add stock inline form — for pure options trades adopting a stock leg */}
          {addingStock && (
            <div className="bg-success/5 border border-success/15 rounded-lg p-2.5 space-y-2" onClick={e => e.stopPropagation()}>
              <div className="text-[9px] uppercase tracking-wider text-success/60 font-semibold">Add Stock Position</div>
              <p className="text-[10px] text-base-content/40">
                {(trade.legs_data || []).every((l: any) => l.action === 'sell' && l.type?.toLowerCase() === 'call')
                  ? 'This will convert your position to a Covered Call.'
                  : 'This will convert your position to a Stock + Options combo.'
                }
              </p>
              <div className="grid grid-cols-2 gap-2">
                <div>
                  <label className="text-[9px] text-base-content/40 block mb-0.5">Shares</label>
                  <input type="number" min={1} step={1} placeholder="100"
                    className="input input-bordered input-xs w-full"
                    value={addStockState.shares}
                    onChange={e => setAddStockState(s => ({ ...s, shares: e.target.value }))}
                  />
                </div>
                <div>
                  <label className="text-[9px] text-base-content/40 block mb-0.5">Price paid / share $</label>
                  <input type="number" min={0} step={0.01} placeholder="875.23"
                    className="input input-bordered input-xs w-full"
                    value={addStockState.price}
                    onChange={e => setAddStockState(s => ({ ...s, price: e.target.value }))}
                  />
                </div>
              </div>
              {addStockState.shares && addStockState.price && (
                <p className="text-[10px] text-base-content/40">
                  Total cost: <span className="font-semibold text-base-content/70">
                    {fmtMoney(parseFloat(addStockState.shares) * parseFloat(addStockState.price))}
                  </span>
                </p>
              )}
              {addStockState.error && <p className="text-xs text-error">{addStockState.error}</p>}
              <div className="flex gap-2">
                <button
                  className="btn btn-success btn-xs gap-1"
                  onClick={commitAddStock}
                  disabled={addStockState.saving}
                >
                  {addStockState.saving ? <Loader2 className="w-3 h-3 animate-spin" /> : <TrendingUp className="w-3 h-3" />}
                  Add Stock
                </button>
                <button className="btn btn-ghost btn-xs" onClick={() => setAddingStock(false)}>Cancel</button>
              </div>
            </div>
          )}

          {/* Add option leg form — shown when no existing legs (stock trades adding first option) */}
          {addingLeg && !hasOptionLegsNow && (
            <div className="bg-base-300/20 border border-white/[0.06] rounded-lg p-2.5 space-y-2" onClick={e => e.stopPropagation()}>
              <div className="text-[9px] uppercase tracking-wider text-base-content/30 font-semibold">
                New Option Leg
                {isStock && <span className="ml-2 text-warning/70 normal-case font-normal">— will convert to {
                  '(sell Call → Covered Call, buy Put → Protective Combo)'
                }</span>}
              </div>
              <div className="grid grid-cols-2 sm:grid-cols-3 gap-2">
                <div>
                  <label className="text-[9px] text-base-content/40 block mb-0.5">Action</label>
                  <div className="flex gap-1">
                    {(['buy', 'sell'] as const).map(a => (
                      <button key={a} type="button"
                        className={`px-2 py-1 rounded text-[10px] font-semibold border ${newLegState.action === a ? (a === 'buy' ? 'border-success/40 bg-success/10 text-success' : 'border-error/40 bg-error/10 text-error') : 'border-white/[0.06] text-base-content/40'}`}
                        onClick={() => setNewLegState(s => ({ ...s, action: a }))}
                      >{a === 'buy' ? 'Buy' : 'Sell'}</button>
                    ))}
                  </div>
                </div>
                <div>
                  <label className="text-[9px] text-base-content/40 block mb-0.5">Type</label>
                  <div className="flex gap-1">
                    {(['call', 'put'] as const).map(t => (
                      <button key={t} type="button"
                        className={`px-2 py-1 rounded text-[10px] font-semibold border capitalize ${newLegState.type === t ? 'border-primary/40 bg-primary/10 text-primary' : 'border-white/[0.06] text-base-content/40'}`}
                        onClick={() => setNewLegState(s => ({ ...s, type: t }))}
                      >{t}</button>
                    ))}
                  </div>
                </div>
                <div>
                  <label className="text-[9px] text-base-content/40 block mb-0.5">Contracts</label>
                  <input type="number" min={1} step={1}
                    className="input input-bordered input-xs w-full"
                    value={newLegState.contracts}
                    onChange={e => setNewLegState(s => ({ ...s, contracts: parseInt(e.target.value) || 1 }))}
                  />
                </div>
                <div>
                  <label className="text-[9px] text-base-content/40 block mb-0.5">Strike $</label>
                  <input type="number" step="0.5" placeholder="900"
                    className="input input-bordered input-xs w-full"
                    value={newLegState.strike}
                    onChange={e => setNewLegState(s => ({ ...s, strike: e.target.value }))}
                  />
                </div>
                <div>
                  <label className="text-[9px] text-base-content/40 block mb-0.5">Expiry</label>
                  <input type="date"
                    className="input input-bordered input-xs w-full"
                    value={newLegState.expiration}
                    onChange={e => setNewLegState(s => ({ ...s, expiration: e.target.value }))}
                  />
                </div>
                <div>
                  <label className="text-[9px] text-base-content/40 block mb-0.5">Premium/share $</label>
                  <input type="number" step="0.01" placeholder="12.50"
                    className="input input-bordered input-xs w-full"
                    value={newLegState.premium}
                    onChange={e => setNewLegState(s => ({ ...s, premium: e.target.value }))}
                  />
                </div>
              </div>
              {newLegState.contracts && newLegState.premium && (
                <p className={`text-[10px] font-medium ${newLegState.action === 'buy' ? 'text-error/70' : 'text-success/70'}`}>
                  {newLegState.action === 'buy' ? 'Cost' : 'Credit'}: {fmtMoney(newLegState.contracts * parseFloat(newLegState.premium || '0') * 100)}
                </p>
              )}
              {newLegState.error && <p className="text-xs text-error">{newLegState.error}</p>}
              <div className="flex gap-2">
                <button className="btn btn-primary btn-xs gap-1" onClick={commitAddLeg} disabled={newLegState.saving}>
                  {newLegState.saving ? <Loader2 className="w-3 h-3 animate-spin" /> : <Plus className="w-3 h-3" />}
                  Add Leg
                </button>
                <button className="btn btn-ghost btn-xs" onClick={() => setAddingLeg(false)}>Cancel</button>
              </div>
            </div>
          )}

          {/* Transaction history (inline panel, shown on demand) */}
          {showHistory && (
            <div className="bg-base-300/20 rounded-xl p-3 border border-white/[0.04]">
              <TransactionHistoryPanel
                strategyId={trade.id}
                onFetch={onFetchTransactions}
                trade={trade}
                pnl={pnl}
                onPositionChanged={() => onPositionChanged(trade.id)}
              />
            </div>
          )}

          {/* Trade notes */}
          {trade.notes && (
            <div className="bg-base-300/15 rounded-xl px-3 py-2.5 text-xs text-base-content/60 leading-relaxed border border-white/[0.03]">
              <div className="text-[9px] uppercase tracking-wider text-base-content/25 mb-1">Thesis / Notes</div>
              {trade.notes}
            </div>
          )}

          {/* AI Trade Advisor */}
          <div className="bg-base-200/60 rounded-xl p-3 border border-white/[0.04] space-y-2">
            <div className="flex items-center justify-between">
              <div className="flex items-center gap-1.5 text-xs font-semibold text-secondary">
                <Brain className="w-3.5 h-3.5" />
                {isIncome ? 'Income Strategy Advisor' : isStock || isComboLike ? 'Position Analyst' : 'Options Advisor'}
              </div>
              <button
                className="btn btn-secondary btn-xs gap-1"
                onClick={() => onAskAdvisor()}
                disabled={advisorState.loading || !pnl}
                title={!pnl ? 'Refresh P&L first to enable advisor' : ''}
              >
                {advisorState.loading
                  ? <Loader2 className="w-3 h-3 animate-spin" />
                  : <Brain className="w-3 h-3" />}
                Full Analysis
              </button>
            </div>

            {/* Custom question */}
            {pnl && (
              <div className="flex gap-2">
                <input
                  className="input input-bordered input-xs flex-1 text-xs"
                  placeholder={
                    isIncome
                      ? 'Ask: Should I roll this before expiry? Is the ann. return still competitive?'
                      : isStock || isComboLike
                      ? 'Ask: Should I add to my position? Should I roll or close the options leg?'
                      : 'Ask: What are my Greeks risk? Should I close before earnings?'
                  }
                  value={advisorState.question}
                  onChange={e => onAdvisorQuestion(e.target.value)}
                  onKeyDown={e => { if (e.key === 'Enter' && advisorState.question.trim()) onAskAdvisor(advisorState.question); }}
                />
                <button
                  className="btn btn-secondary btn-xs gap-1"
                  onClick={() => advisorState.question.trim() && onAskAdvisor(advisorState.question)}
                  disabled={advisorState.loading || !advisorState.question.trim() || !pnl}
                >
                  <Send className="w-3 h-3" />
                </button>
              </div>
            )}

            {!pnl && (
              <p className="text-[10px] text-base-content/30 italic">Refresh P&L above to enable AI advisor.</p>
            )}

            {advisorState.loading && (
              <div className="flex items-center gap-2 text-xs text-secondary py-1">
                <Loader2 className="w-3.5 h-3.5 animate-spin" />
                Analyzing position with live quotes, Greeks, and market data…
              </div>
            )}
            {advisorState.error && (
              <div className="text-xs text-error bg-error/10 rounded px-2 py-1">{advisorState.error}</div>
            )}
            {advisorState.response && (
              <div className="bg-base-300/30 rounded-lg p-3 text-xs text-base-content/80 whitespace-pre-wrap leading-relaxed max-h-72 overflow-y-auto">
                {advisorState.response}
              </div>
            )}
          </div>

        </div>
      )}

      {closeModal && (
        <CloseTradeModal
          trade={trade}
          pnl={pnl}
          preselectLeg={closeModal.preselect}
          onClose={() => setCloseModal(null)}
          onClosed={() => { setCloseModal(null); onPositionChanged(trade.id); }}
        />
      )}
    </div>
  );
}

// ── Group section ─────────────────────────────────────────────────────────────

interface SharedCardProps {
  expandedIds: Set<number>;
  onToggle: (id: number) => void;
  onRefreshPnl: (id: number) => void;
  pnlLoadingMap: Record<number, boolean>;
  quoteSource: string;
  onQuoteSourceChange: (v: string) => void;
  onUpdatePosition: (t: SavedStrategyItem) => void;
  onShowHistory: (id: number) => void;
  openHistoryIds: Set<number>;
  onCreateAgent: (t: SavedStrategyItem) => void;
  advisorMap: Record<number, AdvisorState>;
  onAskAdvisor: (id: number, q?: string) => void;
  onAdvisorQuestion: (id: number, q: string) => void;
  onFetchTransactions: (id: number) => Promise<TradeTransaction[]>;
  onPositionChanged: (id: number) => void;
  onDeleteTrade: (id: number) => void;
}

function GroupSection({ purpose, trades, pnlMap, ...props }: {
  purpose: TradePurpose;
  trades: SavedStrategyItem[];
  pnlMap: Record<number, LivePnlResponse>;
} & SharedCardProps) {
  const [collapsed, setCollapsed] = useState(false);
  const [structFilter, setStructFilter] = useState<string>('all');
  const [sortBy, setSortBy] = useState<string>('added');
  const meta = PURPOSE_META[purpose];

  // Structures present in this group (for the filter chips) + counts.
  const structCounts = useMemo(() => {
    const m = new Map<string, { label: string; n: number }>();
    trades.forEach(t => {
      const s = tradeStructure(t);
      const cur = m.get(s.key);
      m.set(s.key, { label: s.label, n: (cur?.n ?? 0) + 1 });
    });
    return Array.from(m.entries()).map(([key, v]) => ({ key, ...v }));
  }, [trades]);

  const shown = useMemo(() => {
    const expMs = (t: SavedStrategyItem) => {
      const e = expiryFrom(t, pnlMap[t.id]);
      return e ? new Date(e).getTime() : Infinity;   // undated → sorts last on expiry
    };
    const n = (v: number | null | undefined) => (v == null ? -Infinity : v);
    const added = (t: SavedStrategyItem) => new Date(t.entry_date || 0).getTime();
    const list = structFilter === 'all' ? trades : trades.filter(t => tradeStructure(t).key === structFilter);
    const sorters: Record<string, (a: SavedStrategyItem, b: SavedStrategyItem) => number> = {
      added: (a, b) => added(b) - added(a),
      expiry: (a, b) => expMs(a) - expMs(b),
      pnl: (a, b) => n(pnlMap[b.id]?.unrealized_pnl) - n(pnlMap[a.id]?.unrealized_pnl),
      ann: (a, b) => n(pnlMap[b.id]?.analysis?.annualized_return_to_expiry) - n(pnlMap[a.id]?.analysis?.annualized_return_to_expiry),
      ticker: (a, b) => (a.ticker || '').localeCompare(b.ticker || ''),
    };
    return [...list].sort(sorters[sortBy] || sorters.added);
  }, [trades, structFilter, sortBy, pnlMap]);

  if (trades.length === 0) return null;
  const showFilters = trades.length > 2 && structCounts.length > 1;

  return (
    <section>
      {/* Group header */}
      <button
        onClick={() => setCollapsed(v => !v)}
        className={`w-full flex items-center gap-2.5 px-4 py-3 rounded-t-2xl bg-${meta.color}/8 border border-${meta.color}/15 hover:bg-${meta.color}/12 transition-colors text-left`}
      >
        <div className={`w-7 h-7 rounded-lg bg-${meta.color}/15 flex items-center justify-center text-${meta.color} shrink-0`}>
          {meta.icon}
        </div>
        <div className="flex-1 min-w-0">
          <div className={`font-semibold text-sm text-${meta.color}`}>{meta.label}</div>
          <div className="text-[10px] text-base-content/40">{meta.desc}</div>
        </div>
        <div className="flex items-center gap-2 shrink-0">
          <span className={`badge badge-sm badge-${meta.color} badge-outline`}>{trades.length}</span>
          {collapsed
            ? <ChevronDown className="w-3.5 h-3.5 text-base-content/30" />
            : <ChevronUp className="w-3.5 h-3.5 text-base-content/30" />}
        </div>
      </button>

      {!collapsed && (
        <div className={`border-x border-b border-${meta.color}/10 rounded-b-2xl overflow-hidden`}>
          <GroupSummary trades={trades} pnlMap={pnlMap} />

          {/* Filter by structure + sort — shown when the group holds a mix of types */}
          {showFilters && (
            <div className="flex items-center gap-1.5 flex-wrap px-3 pt-2.5">
              <Filter className="w-3 h-3 text-base-content/30 shrink-0" />
              <button
                className={`badge badge-sm cursor-pointer ${structFilter === 'all' ? `badge-${meta.color}` : 'badge-ghost'}`}
                onClick={() => setStructFilter('all')}>All {trades.length}</button>
              {structCounts.map(s => (
                <button key={s.key}
                  className={`badge badge-sm cursor-pointer ${structFilter === s.key ? `badge-${meta.color}` : 'badge-ghost'}`}
                  onClick={() => setStructFilter(s.key)}>{s.label} {s.n}</button>
              ))}
              <label className="flex items-center gap-1 ml-auto text-[10px] text-base-content/40">
                <ArrowDownUp className="w-3 h-3" />
                <select className="select select-bordered select-xs" value={sortBy} onChange={e => setSortBy(e.target.value)}>
                  <option value="added">Newest</option>
                  <option value="expiry">Expiry (soonest)</option>
                  <option value="pnl">Unrealized P&amp;L</option>
                  <option value="ann">Ann. return</option>
                  <option value="ticker">Ticker A–Z</option>
                </select>
              </label>
            </div>
          )}

          <div className="px-3 pb-3 pt-2 space-y-2">
            {shown.length === 0 && (
              <div className="text-center text-xs text-base-content/40 py-4">No {structCounts.find(s => s.key === structFilter)?.label} trades in this group.</div>
            )}
            {shown.map(trade => (
              <TradeCard
                key={trade.id}
                trade={trade}
                group={classifyTrade(trade)}
                pnl={pnlMap[trade.id]}
                {...props}
                isExpanded={props.expandedIds.has(trade.id)}
                onToggle={() => props.onToggle(trade.id)}
                onRefreshPnl={() => props.onRefreshPnl(trade.id)}
                pnlLoading={!!props.pnlLoadingMap[trade.id]}
                quoteSource={props.quoteSource}
                onQuoteSourceChange={props.onQuoteSourceChange}
                onUpdatePosition={() => props.onUpdatePosition(trade)}
                onShowHistory={() => props.onShowHistory(trade.id)}
                onCreateAgent={() => props.onCreateAgent(trade)}
                advisorState={props.advisorMap[trade.id] ?? { loading: false, response: '', error: null, question: '' }}
                onAskAdvisor={(q?: string) => props.onAskAdvisor(trade.id, q)}
                onAdvisorQuestion={(q: string) => props.onAdvisorQuestion(trade.id, q)}
                showHistory={props.openHistoryIds.has(trade.id)}
              />
            ))}
          </div>
        </div>
      )}
    </section>
  );
}

// ── Main orchestrator ─────────────────────────────────────────────────────────

interface AdvisorState {
  loading: boolean;
  response: string;
  error: string | null;
  question: string;
}

export default function MyTradesV2() {
  const [activeStatus, setActiveStatus] = useState<'active' | 'closed'>('active');
  const [trades, setTrades] = useState<SavedStrategyItem[]>([]);
  const [loading, setLoading] = useState(true);
  const [err, setErr] = useState<string | null>(null);
  // Each expanded / history panel is tracked independently — opening one card
  // never collapses another. User must explicitly click to close each one.
  const [expandedIds, setExpandedIds] = useState<Set<number>>(new Set());
  const [openHistoryIds, setOpenHistoryIds] = useState<Set<number>>(new Set());
  const [pnlMap, setPnlMap] = useState<Record<number, LivePnlResponse>>({});
  const [pnlLoadingMap, setPnlLoadingMap] = useState<Record<number, boolean>>({});
  const [quoteSource, setQuoteSource] = useState('yfinance');
  const [advisorMap, setAdvisorMap] = useState<Record<number, AdvisorState>>({});

  // Modals
  const [updateTrade, setUpdateTrade] = useState<SavedStrategyItem | null>(null);
  const [agentTrade, setAgentTrade] = useState<SavedStrategyItem | null>(null);

  const loadTrades = useCallback(async () => {
    setLoading(true);
    setErr(null);
    try {
      const data = await fetchActiveTrades(activeStatus);
      setTrades(data);
    } catch (e: any) {
      setErr(e?.message || 'Failed to load trades');
    } finally {
      setLoading(false);
    }
  }, [activeStatus]);

  useEffect(() => { loadTrades(); }, [loadTrades]);

  // Auto-fetch P&L for any newly expanded card that doesn't have data yet.
  // We watch a stable string key derived from the set so the effect fires
  // when the set contents change (sets are reference-equal after .add()).
  const expandedKey = Array.from(expandedIds).sort().join(',');
  useEffect(() => {
    expandedIds.forEach(id => {
      if (!pnlMap[id] && !pnlLoadingMap[id]) {
        handleRefreshPnl(id);
      }
    });
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [expandedKey]);

  const handleToggle = (id: number) => {
    setExpandedIds(prev => {
      const next = new Set(prev);
      if (next.has(id)) {
        next.delete(id);
        // Also close this card's history panel when collapsing
        setOpenHistoryIds(h => { const hn = new Set(h); hn.delete(id); return hn; });
      } else {
        next.add(id);
      }
      return next;
    });
  };

  const handleRefreshPnl = async (id: number) => {
    setPnlLoadingMap(prev => ({ ...prev, [id]: true }));
    try {
      const data = await fetchTradeLivePnl(id, quoteSource);
      setPnlMap(prev => ({ ...prev, [id]: data }));
    } catch (e: any) {
      setErr(`P&L refresh failed for trade ${id}: ${e.message}`);
    } finally {
      setPnlLoadingMap(prev => ({ ...prev, [id]: false }));
    }
  };

  const handleAskAdvisor = async (tradeId: number, question?: string) => {
    const pnl = pnlMap[tradeId];
    if (!pnl) return;
    setAdvisorMap(prev => ({
      ...prev,
      [tradeId]: { ...(prev[tradeId] ?? { question: '', response: '', error: null }), loading: true, error: null, response: '' },
    }));
    try {
      const result = await fetchTradeAdvisor(tradeId, pnl, question);
      setAdvisorMap(prev => ({
        ...prev,
        [tradeId]: { ...(prev[tradeId] ?? { question: '', response: '', error: null }), loading: false, response: result.content },
      }));
    } catch (e: any) {
      setAdvisorMap(prev => ({
        ...prev,
        [tradeId]: { ...(prev[tradeId] ?? { question: '', response: '', error: null }), loading: false, error: e.message || 'Advisor error' },
      }));
    }
  };

  const handleAdvisorQuestion = (tradeId: number, q: string) => {
    setAdvisorMap(prev => ({
      ...prev,
      [tradeId]: { ...(prev[tradeId] ?? { loading: false, response: '', error: null }), question: q },
    }));
  };

  const handleSaveTransaction = async (trade: SavedStrategyItem, data: Parameters<typeof appendTradeTransaction>[1]) => {
    await appendTradeTransaction(trade.id, data);

    // Evict the stale P&L snapshot so the card immediately shows a loading state
    // rather than the pre-update cost basis / current value.
    setPnlMap(prev => {
      const next = { ...prev };
      delete next[trade.id];
      return next;
    });

    // Reload trade list (parameters.shares + avg_cost updated by backend ledger walk)
    await loadTrades();

    // Auto-fetch fresh P&L so the updated cost basis + current value appear immediately.
    // Only if the card is still expanded — avoids a spurious network call on close.
    if (expandedIds.has(trade.id)) {
      handleRefreshPnl(trade.id);
    }
  };

  const handleCreateAgent = async (data: Parameters<typeof createAgent>[0]) => {
    return createAgent(data);
  };

  const handleDeleteTrade = (tradeId: number) => {
    // Remove from all local state immediately so the card disappears
    setExpandedIds(prev => { const n = new Set(prev); n.delete(tradeId); return n; });
    setOpenHistoryIds(prev => { const n = new Set(prev); n.delete(tradeId); return n; });
    setPnlMap(prev => { const n = { ...prev }; delete n[tradeId]; return n; });
    setPnlLoadingMap(prev => { const n = { ...prev }; delete n[tradeId]; return n; });
    setAdvisorMap(prev => { const n = { ...prev }; delete n[tradeId]; return n; });
    // Reload list to reflect deletion
    loadTrades();
  };

  // Group trades
  // Group by PURPOSE (what the trade is for), not structure type.
  const groups: Record<TradePurpose, SavedStrategyItem[]> = {
    income: [], hedge: [], managed_floor: [], managed_buffer: [],
    dual_directional: [], trade: [], other: [],
  };
  trades.forEach(t => { groups[tradePurpose(t)].push(t); });

  const totalCapital = trades.reduce((s, t) => {
    const p = pnlMap[t.id];
    if (t.strategy_type === 'futures') {
      const margin = t.parameters?.margin_req ? parseFloat(t.parameters.margin_req) : 0;
      return s + (margin || Math.abs(p?.entry_cost ?? t.entry_net_debit ?? 0));
    }
    return s + Math.abs(p?.entry_cost ?? t.entry_net_debit ?? 0);
  }, 0);
  const totalPnl = Object.values(pnlMap).reduce((s, p) => s + p.unrealized_pnl, 0);
  const hasPnl = Object.keys(pnlMap).length > 0;

  const groupOrder: TradePurpose[] = PURPOSE_ORDER;

  const sharedProps = {
    expandedIds,
    onToggle: handleToggle,
    onRefreshPnl: handleRefreshPnl,
    pnlLoadingMap,
    quoteSource,
    onQuoteSourceChange: (v: string) => {
      setQuoteSource(v);
      // Re-fetch P&L for every currently expanded card with the new source
      expandedIds.forEach(id => handleRefreshPnl(id));
    },
    onUpdatePosition: (trade: SavedStrategyItem) => setUpdateTrade(trade),
    onShowHistory: (id: number) => setOpenHistoryIds(prev => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id); else next.add(id);
      return next;
    }),
    openHistoryIds,
    onCreateAgent: (trade: SavedStrategyItem) => setAgentTrade(trade),
    advisorMap,
    onAskAdvisor: handleAskAdvisor,
    onAdvisorQuestion: handleAdvisorQuestion,
    onDeleteTrade: handleDeleteTrade,
    onFetchTransactions: fetchTradeTransactions,
    onPositionChanged: async (tradeId: number) => {
      // Evict stale P&L then reload trades and re-fetch live P&L
      setPnlMap(prev => { const next = { ...prev }; delete next[tradeId]; return next; });
      await loadTrades();
      if (expandedIds.has(tradeId)) handleRefreshPnl(tradeId);
    },
  };

  return (
    <div className="space-y-4">

      {/* Status tab + portfolio summary */}
      <div className="flex items-center justify-between flex-wrap gap-3">
        <div className="tabs tabs-boxed bg-base-200/50 p-0.5">
          <button
            className={`tab tab-sm ${activeStatus === 'active' ? 'tab-active' : ''}`}
            onClick={() => setActiveStatus('active')}
          >
            Active
          </button>
          <button
            className={`tab tab-sm ${activeStatus === 'closed' ? 'tab-active' : ''}`}
            onClick={() => setActiveStatus('closed')}
          >
            Closed
          </button>
        </div>

        {/* Portfolio summary strip */}
        {trades.length > 0 && (
          <div className="flex items-center gap-3 text-xs text-base-content/50 flex-wrap">
            <span>{trades.length} position{trades.length !== 1 ? 's' : ''}</span>
            <span className="opacity-30">·</span>
            <span>{fmtMoney(totalCapital)} total deployed</span>
            {hasPnl && totalPnl !== 0 && (
              <>
                <span className="opacity-30">·</span>
                <span className={totalPnl >= 0 ? 'text-success' : 'text-error'}>
                  {totalPnl >= 0 ? '+' : ''}{fmtMoney(Math.abs(totalPnl))} unrealized
                </span>
              </>
            )}
          </div>
        )}
      </div>

      {/* Error */}
      {err && (
        <div className="alert alert-error text-sm py-2">
          <AlertCircle className="w-4 h-4 shrink-0" />
          <span className="flex-1">{err}</span>
          <button className="btn btn-ghost btn-xs" onClick={() => setErr(null)}><X className="w-3 h-3" /></button>
        </div>
      )}

      {/* Loading */}
      {loading && (
        <div className="flex justify-center py-12">
          <Loader2 className="w-7 h-7 animate-spin text-base-content/20" />
        </div>
      )}

      {/* Empty state */}
      {!loading && trades.length === 0 && (
        <div className="text-center py-16 text-base-content/30">
          <BarChart3 className="w-12 h-12 mx-auto mb-3 opacity-20" />
          <p className="font-medium">No {activeStatus} trades</p>
          <p className="text-xs mt-1">
            {activeStatus === 'active'
              ? 'Use the Log Trade button above to record your first trade.'
              : 'Closed trades will appear here.'}
          </p>
        </div>
      )}

      {/* Book-level short-vol / tail-risk desk (active book only) */}
      {!loading && activeStatus === 'active' && trades.length > 0 && (
        <BookTailRisk quoteSource={quoteSource} />
      )}

      {/* Groups */}
      {!loading && (
        <div className="space-y-4">
          {groupOrder.map(g => (
            <GroupSection
              key={g}
              purpose={g}
              trades={groups[g]}
              pnlMap={pnlMap}
              {...sharedProps}
            />
          ))}
        </div>
      )}

      {/* Update Position Modal */}
      {updateTrade && (
        <UpdatePositionModal
          open={true}
          onClose={() => setUpdateTrade(null)}
          trade={updateTrade}
          onTransactionSaved={() => {
            setUpdateTrade(null);
            loadTrades();
          }}
          onSave={data => handleSaveTransaction(updateTrade, data)}
        />
      )}

      {/* Create Agent Modal */}
      {agentTrade && (
        <CreateAgentFromTradeModal
          open={true}
          onClose={() => setAgentTrade(null)}
          trade={agentTrade}
          pnl={pnlMap[agentTrade.id]}
          onCreateAgent={handleCreateAgent}
        />
      )}
    </div>
  );
}
