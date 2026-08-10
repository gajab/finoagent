import React, { useState, useEffect, useCallback } from 'react';
import {
  Briefcase, TrendingUp, TrendingDown, RefreshCw, X, ChevronDown, ChevronUp,
  Clock, DollarSign, Activity, Plus, AlertCircle, CheckCircle2, Loader2,
  Target, BarChart3, Database, Pencil, Save, Brain, Send, Trash2, ClipboardList,
} from 'lucide-react';
import {
  fetchActiveTrades, fetchTradeLivePnl, closeTrackedTrade, createManualTrade,
  updateSavedStrategy, fetchTradeAdvisor,
} from '../api';
import type { SavedStrategyItem, LivePnlResponse } from '../api';
import LogTradeModal from './LogTradeModal';
import {
  Chart as ChartJS, CategoryScale, LinearScale, PointElement,
  LineElement, Title, Tooltip, Legend, Filler,
} from 'chart.js';
import { Line } from 'react-chartjs-2';

ChartJS.register(CategoryScale, LinearScale, PointElement, LineElement, Title, Tooltip, Legend, Filler);

type Tab = 'active' | 'closed';

interface ManualLeg {
  action: 'BUY' | 'SELL';
  type: 'CALL' | 'PUT';
  strike: number;
  qty: number;
  price: number;
  expiration: string;
}

export function TradePortfolio() {
  const [tab, setTab] = useState<Tab>('active');
  const [trades, setTrades] = useState<SavedStrategyItem[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [expandedId, setExpandedId] = useState<number | null>(null);
  const [pnlData, setPnlData] = useState<Record<number, LivePnlResponse>>({});
  const [loadingPnl, setLoadingPnl] = useState<Record<number, boolean>>({});
  const [quoteSource, setQuoteSource] = useState<string>('yfinance');

  // Manual trade form
  const [manualTicker, setManualTicker] = useState('');
  const [manualName, setManualName] = useState('');
  const [manualType, setManualType] = useState('box_spread');
  const [manualNetDebit, setManualNetDebit] = useState('');
  const [manualLegs, setManualLegs] = useState<ManualLeg[]>([
    { action: 'BUY', type: 'CALL', strike: 0, qty: 1, price: 0, expiration: '' },
  ]);
  const [submitting, setSubmitting] = useState(false);

  // Close trade form
  const [closingId, setClosingId] = useState<number | null>(null);
  const [closeNet, setCloseNet] = useState('');

  // Edit trade
  const [editingId, setEditingId] = useState<number | null>(null);
  const [editName, setEditName] = useState('');
  const [editNetDebit, setEditNetDebit] = useState('');
  const [editEntryPrices, setEditEntryPrices] = useState<{ strike: number; type: string; price: number }[]>([]);
  const [editNotes, setEditNotes] = useState('');
  const [editExpiry, setEditExpiry] = useState('');
  const [editStrategyType, setEditStrategyType] = useState('');
  const [savingEdit, setSavingEdit] = useState(false);

  // Edit legs
  const [editTicker, setEditTicker] = useState('');
  const [editEntryDate, setEditEntryDate] = useState('');
  const [editLegs, setEditLegs] = useState<ManualLeg[]>([]);

  // LLM Trade Advisor
  const [advisorTradeId, setAdvisorTradeId] = useState<number | null>(null);
  const [advisorQuestion, setAdvisorQuestion] = useState('');
  const [advisorResponse, setAdvisorResponse] = useState('');
  const [advisorLoading, setAdvisorLoading] = useState(false);
  const [advisorError, setAdvisorError] = useState<string | null>(null);

  // Manual entry date
  const [manualEntryDate, setManualEntryDate] = useState('');

  // Standalone log trade modal
  const [showLogModal, setShowLogModal] = useState(false);

  // Manual price overrides per leg: key = `${tradeId}_${legIndex}`
  const [manualPrices, setManualPrices] = useState<Record<string, number>>({});

  const loadTrades = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const data = await fetchActiveTrades(tab);
      setTrades(data);
    } catch (err: any) {
      setError(err.message || 'Failed to load trades');
    } finally {
      setLoading(false);
    }
  }, [tab]);

  useEffect(() => {
    loadTrades();
  }, [tab, loadTrades]);

  const handleRefreshPnl = async (id: number) => {
    setLoadingPnl(prev => ({ ...prev, [id]: true }));
    try {
      const data = await fetchTradeLivePnl(id, quoteSource);
      setPnlData(prev => ({ ...prev, [id]: data }));
    } catch (err: any) {
      setError(`P&L refresh failed: ${err.message}`);
    } finally {
      setLoadingPnl(prev => ({ ...prev, [id]: false }));
    }
  };

  // Auto-fetch P&L when a trade is expanded (if not already loaded)
  useEffect(() => {
    if (expandedId != null && !pnlData[expandedId] && !loadingPnl[expandedId] && tab === 'active') {
      handleRefreshPnl(expandedId);
    }
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [expandedId]);

  const handleCloseTrade = async (id: number) => {
    if (!closeNet) return;
    try {
      const trade = trades.find(t => t.id === id);
      const exitPrices = trade?.legs_data?.map(leg => ({
        strike: leg.strike, type: leg.type, price: 0,
      })) || [];
      await closeTrackedTrade(id, {
        exit_prices: exitPrices,
        exit_net: parseFloat(closeNet),
      });
      setClosingId(null);
      setCloseNet('');
      loadTrades();
    } catch (err: any) {
      setError(err.message || 'Failed to close trade');
    }
  };

  const startEditing = (trade: SavedStrategyItem) => {
    setEditingId(trade.id);
    setEditName(trade.name);
    setEditTicker(trade.ticker || '');
    setEditNetDebit(trade.entry_net_debit?.toString() || '');
    setEditEntryPrices(
      (trade.entry_prices || []).map((ep: any) => ({
        strike: ep.strike ?? 0, type: ep.type ?? '', price: ep.price ?? 0,
      }))
    );
    setEditNotes(trade.notes || '');
    setEditStrategyType(trade.strategy_type || '');
    // Entry date
    setEditEntryDate(trade.entry_date ? trade.entry_date.split('T')[0] : '');
    // Derive expiry from snapshot or legs
    const expiry = trade.result_snapshot?.expirationDate
      || trade.result_snapshot?.spread?.expiration
      || trade.legs_data?.find((l: any) => l.expiration)?.expiration
      || '';
    setEditExpiry(expiry);
    // Populate editable legs
    setEditLegs(
      (trade.legs_data || []).map((l: any) => ({
        action: l.action || 'BUY',
        type: l.type || 'CALL',
        strike: l.strike || 0,
        qty: l.qty || 1,
        price: trade.entry_prices?.find((ep: any) => ep.strike === l.strike && ep.type === l.type)?.price ?? l.mid ?? l.midPrice ?? 0,
        expiration: l.expiration || l.exp || expiry || '',
      }))
    );
  };

  const cancelEditing = () => {
    setEditingId(null);
    setEditName('');
    setEditTicker('');
    setEditNetDebit('');
    setEditEntryPrices([]);
    setEditNotes('');
    setEditExpiry('');
    setEditStrategyType('');
    setEditEntryDate('');
    setEditLegs([]);
  };

  const handleSaveEdit = async (id: number) => {
    setSavingEdit(true);
    try {
      const trade = trades.find(t => t.id === id);
      const updates: any = {
        name: editName.trim() || undefined,
        ticker: editTicker.trim().toUpperCase() || undefined,
        strategy_type: editStrategyType || undefined,
        entry_net_debit: editNetDebit ? parseFloat(editNetDebit) : undefined,
        notes: editNotes || undefined,
      };
      // Entry date
      if (editEntryDate) {
        updates.entry_date = new Date(editEntryDate + 'T00:00:00Z').toISOString();
      }
      // Build legs_data and entry_prices from editLegs
      if (editLegs.length > 0) {
        updates.legs_data = editLegs.map(l => ({
          action: l.action, type: l.type, strike: l.strike,
          qty: l.qty, expiration: l.expiration || editExpiry,
          bid: l.price, ask: l.price, mid: l.price,
        }));
        updates.entry_prices = editLegs.map(l => ({
          strike: l.strike, type: l.type, price: l.price,
        }));
      }
      // Save expiry into result_snapshot
      if (editExpiry && trade) {
        const snap = { ...(trade.result_snapshot || {}) };
        snap.expirationDate = editExpiry;
        if (snap.spread) snap.spread = { ...snap.spread, expiration: editExpiry };
        updates.result_snapshot = snap;
      }
      await updateSavedStrategy(id, updates);
      cancelEditing();
      loadTrades();
    } catch (err: any) {
      setError(err.message || 'Failed to save edit');
    } finally {
      setSavingEdit(false);
    }
  };

  const handleAddManualTrade = async () => {
    if (!manualTicker || !manualName || !manualNetDebit) return;
    setSubmitting(true);
    setError(null);
    try {
      const entryPrices = manualLegs.map(l => ({
        strike: l.strike, type: l.type, price: l.price,
      }));
      await createManualTrade({
        strategy_type: manualType,
        name: manualName.trim(),
        ticker: manualTicker.toUpperCase().trim(),
        parameters: {},
        legs_data: manualLegs.map(l => ({
          action: l.action, type: l.type, strike: l.strike,
          qty: l.qty, expiration: l.expiration,
          bid: l.price, ask: l.price, mid: l.price,
        })),
        result_snapshot: {},
        entry_prices: entryPrices,
        entry_net_debit: parseFloat(manualNetDebit),
        order_source: 'manual',
        notes: '',
        entry_date: manualEntryDate ? new Date(manualEntryDate + 'T00:00:00Z').toISOString() : undefined,
      });
      setManualTicker('');
      setManualName('');
      setManualNetDebit('');
      setManualLegs([{ action: 'BUY', type: 'CALL', strike: 0, qty: 1, price: 0, expiration: '' }]);
      setTab('active');
    } catch (err: any) {
      setError(err.message || 'Failed to create trade');
    } finally {
      setSubmitting(false);
    }
  };

  const addLeg = () => {
    setManualLegs(prev => [...prev, { action: 'BUY', type: 'CALL', strike: 0, qty: 1, price: 0, expiration: '' }]);
  };

  const removeLeg = (idx: number) => {
    setManualLegs(prev => prev.filter((_, i) => i !== idx));
  };

  const updateLeg = (idx: number, field: keyof ManualLeg, value: any) => {
    setManualLegs(prev => prev.map((l, i) => i === idx ? { ...l, [field]: value } : l));
  };

  const handleAskAdvisor = async (tradeId: number, question?: string) => {
    const pnl = pnlData[tradeId];
    if (!pnl) return;
    setAdvisorTradeId(tradeId);
    setAdvisorLoading(true);
    setAdvisorError(null);
    setAdvisorResponse('');
    try {
      const result = await fetchTradeAdvisor(tradeId, pnl, question);
      setAdvisorResponse(result.content);
    } catch (err: any) {
      setAdvisorError(err.message || 'Failed to get advisor response');
    } finally {
      setAdvisorLoading(false);
    }
  };

  const addEditLeg = () => {
    setEditLegs(prev => [...prev, { action: 'BUY', type: 'CALL', strike: 0, qty: 1, price: 0, expiration: editExpiry }]);
  };
  const removeEditLeg = (idx: number) => {
    setEditLegs(prev => prev.filter((_, i) => i !== idx));
    setEditEntryPrices(prev => prev.filter((_, i) => i !== idx));
  };
  const updateEditLeg = (idx: number, field: keyof ManualLeg, value: any) => {
    setEditLegs(prev => prev.map((l, i) => i === idx ? { ...l, [field]: value } : l));
  };

  // --- Chart builders ---
  const buildPnlChart = (pnl: LivePnlResponse) => {
    if (!pnl.scenarios || pnl.scenarios.length === 0) return null;
    const labels = pnl.scenarios.map(s => `${s.price_change_pct > 0 ? '+' : ''}${s.price_change_pct}%`);
    return {
      labels,
      datasets: [
        {
          label: 'P&L Now',
          data: pnl.scenarios.map(s => s.pnl_now),
          borderColor: 'rgba(99, 102, 241, 0.9)',
          backgroundColor: 'rgba(99, 102, 241, 0.08)',
          fill: false,
          tension: 0.3,
          pointRadius: 2,
          pointHoverRadius: 5,
          borderWidth: 2,
        },
        {
          label: 'P&L at Expiry',
          data: pnl.scenarios.map(s => s.pnl_at_expiry),
          borderColor: 'rgba(251, 146, 60, 0.9)',
          backgroundColor: 'rgba(251, 146, 60, 0.08)',
          fill: false,
          tension: 0.1,
          pointRadius: 2,
          pointHoverRadius: 5,
          borderWidth: 2,
          borderDash: [5, 3],
        },
        ...(pnl.margin_required > 0 ? [{
          label: 'Margin at Risk',
          data: pnl.scenarios.map(s => s.margin_at_risk),
          borderColor: 'rgba(255, 82, 82, 0.6)',
          backgroundColor: 'rgba(255, 82, 82, 0.05)',
          fill: true,
          tension: 0.3,
          pointRadius: 0,
          borderWidth: 1,
          borderDash: [2, 2],
        }] : []),
      ],
    };
  };

  const buildThetaChart = (pnl: LivePnlResponse) => {
    if (!pnl.theta_projection || pnl.theta_projection.length === 0) return null;
    const labels = pnl.theta_projection.map(t => {
      if (t.days_from_now === 0) return 'Now';
      if (t.dte_remaining === 0) return 'Expiry';
      return `+${t.days_from_now}d`;
    });
    const values = pnl.theta_projection.map(t => t.pnl);
    return {
      labels,
      datasets: [{
        label: 'P&L ($)',
        data: values,
        borderColor: 'rgba(251, 146, 60, 0.9)',
        backgroundColor: 'rgba(251, 146, 60, 0.1)',
        fill: true,
        tension: 0.3,
        pointBackgroundColor: pnl.theta_projection.map(t =>
          t.dte_remaining === 0 ? 'rgba(255, 82, 82, 0.9)' : 'rgba(251, 146, 60, 0.8)'
        ),
        pointRadius: pnl.theta_projection.map(t =>
          t.days_from_now === 0 || t.dte_remaining === 0 ? 5 : 2
        ),
        pointHoverRadius: 6,
      }],
    };
  };

  const chartOptions = (title: string, showLegend = false) => ({
    responsive: true,
    maintainAspectRatio: false,
    plugins: {
      legend: { display: showLegend, labels: { color: 'rgba(255,255,255,0.5)', font: { size: 9 }, boxWidth: 12 } },
      title: { display: true, text: title, color: 'rgba(255,255,255,0.6)', font: { size: 11 } },
      tooltip: {
        callbacks: {
          label: (ctx: any) => `${ctx.dataset.label}: $${ctx.raw.toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`,
        },
      },
    },
    scales: {
      x: { ticks: { color: 'rgba(255,255,255,0.4)', font: { size: 9 } }, grid: { color: 'rgba(255,255,255,0.05)' } },
      y: {
        ticks: { color: 'rgba(255,255,255,0.4)', font: { size: 9 }, callback: (v: any) => `$${v}` },
        grid: { color: 'rgba(255,255,255,0.05)' },
      },
    },
  });

  return (
    <div className="space-y-4">
      {/* Standalone log trade modal */}
      <LogTradeModal
        open={showLogModal}
        onClose={() => setShowLogModal(false)}
        onLogged={() => { setTab('active'); loadTrades(); }}
      />

      <div className="flex items-center justify-between">
        <h2 className="text-xl font-bold flex items-center gap-2">
          <Briefcase className="w-5 h-5 text-secondary" />
          Trade Portfolio
        </h2>
        <button
          className="btn btn-primary btn-sm gap-2"
          onClick={() => setShowLogModal(true)}
        >
          <ClipboardList className="w-3.5 h-3.5" /> Log Trade
        </button>
      </div>

      {/* Tabs */}
      <div className="tabs tabs-boxed bg-base-200/50 w-fit">
        <button className={`tab ${tab === 'active' ? 'tab-active' : ''}`} onClick={() => setTab('active')}>
          Active Trades
        </button>
        <button className={`tab ${tab === 'closed' ? 'tab-active' : ''}`} onClick={() => setTab('closed')}>
          Closed
        </button>
      </div>

      {error && (
        <div className="alert alert-error text-sm py-2">
          <AlertCircle className="w-4 h-4 shrink-0" />
          <span className="flex-1 min-w-0">{error}</span>
          <button className="btn btn-ghost btn-xs" onClick={() => setError(null)}><X className="w-3 h-3" /></button>
        </div>
      )}

      {/* Trade List */}
      <div className="space-y-3">
          {loading && (
            <div className="flex justify-center py-8">
              <Loader2 className="w-6 h-6 animate-spin text-secondary" />
            </div>
          )}

          {!loading && trades.length === 0 && (
            <div className="text-center py-12 text-base-content/40">
              <Briefcase className="w-12 h-12 mx-auto mb-3 opacity-30" />
              <p>No {tab} trades yet.</p>
              <p className="text-xs mt-1">
                {tab === 'active'
                  ? 'Place an order from a strategy or add a manual trade.'
                  : 'Close an active trade to see it here.'}
              </p>
            </div>
          )}

          {trades.map(trade => {
            const isExpanded = expandedId === trade.id;
            const pnl = pnlData[trade.id];
            const isLoadingPnl = loadingPnl[trade.id];
            const isClosing = closingId === trade.id;
            const daysHeld = trade.entry_date
              ? Math.floor((Date.now() - new Date(trade.entry_date).getTime()) / 86400000)
              : 0;
            // Derive expiry from pnl response, result_snapshot, or leg data
            const expiryDate = pnl?.expiration_date
              || trade.result_snapshot?.expirationDate
              || trade.result_snapshot?.spread?.expiration
              || trade.legs_data?.find((l: any) => l.expiration)?.expiration
              || null;
            const dteRemaining = expiryDate
              ? Math.max(0, Math.floor((new Date(expiryDate).getTime() - Date.now()) / 86400000))
              : null;

            const pnlChartData = pnl ? buildPnlChart(pnl) : null;
            const thetaChartData = pnl ? buildThetaChart(pnl) : null;

            return (
              <div key={trade.id} className="bg-base-200/40 rounded-xl border border-white/[0.03] overflow-hidden">
                {/* Trade Header */}
                <div
                  className="p-4 flex items-center gap-3 cursor-pointer hover:bg-base-200/60 transition-colors"
                  onClick={() => setExpandedId(isExpanded ? null : trade.id)}
                >
                  <div className="flex-1 min-w-0">
                    <div className="flex items-center gap-2 flex-wrap">
                      <span className="font-bold">{trade.ticker}</span>
                      <span className="badge badge-outline badge-xs">{trade.strategy_type.replace(/_/g, ' ')}</span>
                      <span className="text-xs text-base-content/50">{trade.name}</span>
                    </div>
                    <div className="flex items-center gap-3 mt-1 text-xs text-base-content/60">
                      <span className="flex items-center gap-1">
                        <DollarSign className="w-3 h-3" />
                        Entry: ${trade.entry_net_debit?.toLocaleString(undefined, { minimumFractionDigits: 2 })}
                      </span>
                      <span className="flex items-center gap-1">
                        <Clock className="w-3 h-3" />
                        {daysHeld}d held
                      </span>
                      {trade.entry_date && (
                        <span>Entered: {new Date(trade.entry_date).toLocaleDateString()}</span>
                      )}
                      {expiryDate && (
                        <span className={`font-medium ${dteRemaining != null && dteRemaining <= 7 ? 'text-warning' : ''}`}>
                          Exp: {expiryDate}{dteRemaining != null ? ` (${dteRemaining}d)` : ''}
                        </span>
                      )}
                    </div>
                  </div>

                  {/* P&L Badge */}
                  {pnl && (
                    <div className={`text-right ${pnl.unrealized_pnl >= 0 ? 'text-success' : 'text-error'}`}>
                      <div className="font-bold flex items-center gap-1">
                        {pnl.unrealized_pnl >= 0 ? <TrendingUp className="w-3.5 h-3.5" /> : <TrendingDown className="w-3.5 h-3.5" />}
                        ${Math.abs(pnl.unrealized_pnl).toFixed(2)}
                      </div>
                      <div className="text-xs">{pnl.pnl_pct >= 0 ? '+' : ''}{pnl.pnl_pct}%</div>
                    </div>
                  )}

                  {tab === 'closed' && trade.exit_net != null && trade.entry_net_debit != null && (
                    <div className={`text-right ${(trade.exit_net - trade.entry_net_debit) >= 0 ? 'text-success' : 'text-error'}`}>
                      <div className="font-bold">
                        ${Math.abs(trade.exit_net - trade.entry_net_debit).toFixed(2)}
                      </div>
                      <div className="text-xs">
                        {((trade.exit_net - trade.entry_net_debit) / Math.abs(trade.entry_net_debit) * 100).toFixed(1)}%
                      </div>
                    </div>
                  )}

                  {isExpanded ? <ChevronUp className="w-4 h-4 shrink-0" /> : <ChevronDown className="w-4 h-4 shrink-0" />}
                </div>

                {/* Expanded Details */}
                {isExpanded && (
                  <div className="border-t border-white/[0.05] p-4 space-y-4">
                    {/* Action Buttons */}
                    {tab === 'active' && (
                      <div className="flex gap-2 flex-wrap items-center">
                        <select
                          className="select select-bordered select-xs w-28"
                          value={quoteSource}
                          onChange={e => setQuoteSource(e.target.value)}
                          onClick={e => e.stopPropagation()}
                        >
                          <option value="yfinance">YFinance</option>
                          <option value="ibkr">IBKR</option>
                        </select>
                        <button
                          className="btn btn-outline btn-xs gap-1"
                          onClick={(e) => { e.stopPropagation(); handleRefreshPnl(trade.id); }}
                          disabled={isLoadingPnl}
                        >
                          {isLoadingPnl ? <Loader2 className="w-3 h-3 animate-spin" /> : <RefreshCw className="w-3 h-3" />}
                          Refresh Quotes
                        </button>
                        <button
                          className="btn btn-outline btn-xs gap-1"
                          onClick={(e) => { e.stopPropagation(); editingId === trade.id ? cancelEditing() : startEditing(trade); }}
                        >
                          <Pencil className="w-3 h-3" /> {editingId === trade.id ? 'Cancel Edit' : 'Edit'}
                        </button>
                        <button
                          className="btn btn-outline btn-error btn-xs gap-1"
                          onClick={(e) => { e.stopPropagation(); setClosingId(isClosing ? null : trade.id); }}
                        >
                          <CheckCircle2 className="w-3 h-3" /> Close Trade
                        </button>
                        {pnl && (
                          <span className="text-[10px] text-base-content/40 ml-auto flex items-center gap-1">
                            <Database className="w-3 h-3" /> Source: {pnl.quote_source}
                            {pnl.underlying_price > 0 && ` | ${trade.ticker}: $${pnl.underlying_price.toFixed(2)}`}
                          </span>
                        )}
                      </div>
                    )}

                    {/* Edit Trade Form */}
                    {editingId === trade.id && (
                      <div className="bg-info/5 border border-info/20 rounded-lg p-3 space-y-3">
                        <h4 className="text-xs font-bold uppercase tracking-wider text-info">Edit Trade</h4>
                        <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
                          <div>
                            <label className="label label-text text-xs">Trade Name</label>
                            <input className="input input-bordered input-sm w-full" value={editName}
                              onChange={e => setEditName(e.target.value)} />
                          </div>
                          <div>
                            <label className="label label-text text-xs">Underlying Ticker</label>
                            <input className="input input-bordered input-sm w-full" value={editTicker}
                              onChange={e => setEditTicker(e.target.value.toUpperCase())} placeholder="^SPX" />
                          </div>
                          <div>
                            <label className="label label-text text-xs">Strategy Type</label>
                            <select className="select select-bordered select-sm w-full" value={editStrategyType}
                              onChange={e => setEditStrategyType(e.target.value)}>
                              <option value="box_spread">Box Spread</option>
                              <option value="dual_direction_buffer">Dual Direction Buffer</option>
                              <option value="single_stock_long_short">Long/Short</option>
                              <option value="pair_trade">Pair Trade</option>
                              <option value="custom">Custom</option>
                            </select>
                          </div>
                          <div>
                            <label className="label label-text text-xs">Net Debit/Credit ($)</label>
                            <input className="input input-bordered input-sm w-full" type="number" value={editNetDebit}
                              onChange={e => setEditNetDebit(e.target.value)} />
                          </div>
                          <div>
                            <label className="label label-text text-xs">Entry Date</label>
                            <input className="input input-bordered input-sm w-full" type="date" value={editEntryDate}
                              onChange={e => setEditEntryDate(e.target.value)} />
                          </div>
                          <div>
                            <label className="label label-text text-xs">Expiration Date</label>
                            <input className="input input-bordered input-sm w-full" type="date" value={editExpiry}
                              onChange={e => setEditExpiry(e.target.value)} />
                          </div>
                          <div className="md:col-span-2">
                            <label className="label label-text text-xs">Notes</label>
                            <input className="input input-bordered input-sm w-full" value={editNotes}
                              onChange={e => setEditNotes(e.target.value)} placeholder="Optional notes" />
                          </div>
                        </div>

                        {/* Editable Legs */}
                        <div className="space-y-2">
                          <div className="flex items-center justify-between">
                            <span className="text-xs font-bold uppercase tracking-wider text-base-content/50">Legs</span>
                            <button className="btn btn-ghost btn-xs gap-1" onClick={addEditLeg}>
                              <Plus className="w-3 h-3" /> Add Leg
                            </button>
                          </div>
                          {editLegs.map((leg, idx) => (
                            <div key={idx} className="flex gap-2 items-end flex-wrap">
                              <select className="select select-bordered select-xs w-20" value={leg.action}
                                onChange={e => updateEditLeg(idx, 'action', e.target.value)}>
                                <option value="BUY">Buy</option>
                                <option value="SELL">Sell</option>
                              </select>
                              <select className="select select-bordered select-xs w-20" value={leg.type}
                                onChange={e => updateEditLeg(idx, 'type', e.target.value)}>
                                <option value="CALL">Call</option>
                                <option value="PUT">Put</option>
                              </select>
                              <div className="form-control">
                                <label className="label label-text text-[9px] py-0">Strike</label>
                                <input className="input input-bordered input-xs w-24" type="number" placeholder="Strike"
                                  value={leg.strike || ''} onChange={e => updateEditLeg(idx, 'strike', parseFloat(e.target.value) || 0)} />
                              </div>
                              <div className="form-control">
                                <label className="label label-text text-[9px] py-0">Qty</label>
                                <input className="input input-bordered input-xs w-16" type="number" placeholder="Qty"
                                  value={leg.qty || ''} onChange={e => updateEditLeg(idx, 'qty', parseInt(e.target.value) || 1)} />
                              </div>
                              <div className="form-control">
                                <label className="label label-text text-[9px] py-0">Entry Price</label>
                                <input className="input input-bordered input-xs w-24" type="number" step="0.01" placeholder="Price"
                                  value={leg.price || ''} onChange={e => updateEditLeg(idx, 'price', parseFloat(e.target.value) || 0)} />
                              </div>
                              <div className="form-control">
                                <label className="label label-text text-[9px] py-0">Expiry</label>
                                <input className="input input-bordered input-xs w-32" type="date"
                                  value={leg.expiration} onChange={e => updateEditLeg(idx, 'expiration', e.target.value)} />
                              </div>
                              {editLegs.length > 1 && (
                                <button className="btn btn-ghost btn-xs text-error" onClick={() => removeEditLeg(idx)}>
                                  <Trash2 className="w-3 h-3" />
                                </button>
                              )}
                            </div>
                          ))}
                        </div>

                        <button className="btn btn-info btn-sm gap-1" onClick={() => handleSaveEdit(trade.id)} disabled={savingEdit}>
                          {savingEdit ? <Loader2 className="w-3 h-3 animate-spin" /> : <Save className="w-3 h-3" />}
                          Save Changes
                        </button>
                      </div>
                    )}

                    {/* Close Trade Form */}
                    {isClosing && (
                      <div className="flex items-center gap-2 bg-error/10 rounded-lg p-2">
                        <span className="text-xs">Exit Net ($):</span>
                        <input className="input input-bordered input-xs w-28" type="number" value={closeNet}
                          onChange={e => setCloseNet(e.target.value)} placeholder="0.00" />
                        <button className="btn btn-error btn-xs" onClick={() => handleCloseTrade(trade.id)}
                          disabled={!closeNet}>Confirm Close</button>
                        <button className="btn btn-ghost btn-xs" onClick={() => { setClosingId(null); setCloseNet(''); }}>Cancel</button>
                      </div>
                    )}

                    {/* P&L Summary */}
                    {pnl && (() => {
                      // Annualized returns — pure math from existing data
                      const absEntry = Math.abs(pnl.entry_cost);
                      const currentRoi = absEntry > 0 ? pnl.unrealized_pnl / absEntry : 0;
                      const daysHeldSafe = Math.max(pnl.days_held, 1);
                      // Current annualized: compound the realized ROI to a full year
                      const currentAnnualized = absEntry > 0 && daysHeldSafe > 0
                        ? (Math.pow(1 + currentRoi, 365 / daysHeldSafe) - 1) * 100
                        : null;
                      // Cap at ±999% to avoid absurd numbers on day 1
                      const cappedCurrentAnn = currentAnnualized != null
                        ? Math.max(-999, Math.min(999, currentAnnualized)) : null;
                      // Pending annualized at expiry: if held to expiry, what's the annualized return on max profit
                      const totalDaysToExpiry = (pnl.days_held || 0) + (dteRemaining || 0);
                      const expiryRoi = absEntry > 0 && pnl.max_profit != null
                        ? pnl.max_profit / absEntry : null;
                      const pendingAnnualized = expiryRoi != null && totalDaysToExpiry > 0
                        ? (Math.pow(1 + expiryRoi, 365 / totalDaysToExpiry) - 1) * 100
                        : null;
                      const cappedPendingAnn = pendingAnnualized != null
                        ? Math.max(-999, Math.min(999, pendingAnnualized)) : null;

                      return (
                      <div className="space-y-3">
                        <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
                          <div className="bg-base-300/30 rounded-lg p-2 text-center">
                            <div className="text-[10px] text-base-content/50 uppercase">Entry Cost</div>
                            <div className="font-bold text-sm">${pnl.entry_cost.toLocaleString(undefined, { minimumFractionDigits: 2 })}</div>
                          </div>
                          <div className="bg-base-300/30 rounded-lg p-2 text-center">
                            <div className="text-[10px] text-base-content/50 uppercase">Current Value</div>
                            <div className="font-bold text-sm">${pnl.current_value.toLocaleString(undefined, { minimumFractionDigits: 2 })}</div>
                          </div>
                          <div className={`bg-base-300/30 rounded-lg p-2 text-center ${pnl.unrealized_pnl >= 0 ? 'text-success' : 'text-error'}`}>
                            <div className="text-[10px] uppercase opacity-70">Unrealized P&L</div>
                            <div className="font-bold text-sm">{pnl.unrealized_pnl >= 0 ? '+' : ''}${pnl.unrealized_pnl.toFixed(2)} ({pnl.pnl_pct >= 0 ? '+' : ''}{pnl.pnl_pct}%)</div>
                          </div>
                          <div className={`rounded-lg p-2 text-center ${dteRemaining != null && dteRemaining >= 0 && dteRemaining <= 21 ? 'bg-warning/15 ring-1 ring-warning/40' : 'bg-base-300/30'}`}
                            title={dteRemaining != null && dteRemaining <= 21 ? '21-DTE management window — gamma accelerates; take profit on winners / roll tested strikes rather than carrying into expiration.' : undefined}>
                            <div className="text-[10px] text-base-content/50 uppercase">Days Held</div>
                            <div className="font-bold text-sm">{pnl.days_held}{dteRemaining != null ? ` / ${dteRemaining} DTE` : ''}</div>
                            {dteRemaining != null && dteRemaining >= 0 && dteRemaining <= 21 && (
                              <div className="text-[8px] font-semibold text-warning uppercase tracking-wide">21-DTE · manage</div>
                            )}
                          </div>
                        </div>

                        {/* Annualized Returns */}
                        <div className="grid grid-cols-2 gap-3">
                          {cappedCurrentAnn != null && (
                            <div className={`bg-base-300/30 rounded-lg p-2 text-center ${cappedCurrentAnn >= 0 ? 'text-success' : 'text-error'}`}>
                              <div className="text-[10px] uppercase opacity-70">Current Annualized Return</div>
                              <div className="font-bold text-sm">{cappedCurrentAnn >= 0 ? '+' : ''}{cappedCurrentAnn.toFixed(1)}%</div>
                              <div className="text-[9px] opacity-50">Based on {daysHeldSafe}d held</div>
                            </div>
                          )}
                          {cappedPendingAnn != null && (
                            <div className={`bg-base-300/30 rounded-lg p-2 text-center ${cappedPendingAnn >= 0 ? 'text-success' : 'text-error'}`}>
                              <div className="text-[10px] uppercase opacity-70">Annualized Return at Expiry</div>
                              <div className="font-bold text-sm">{cappedPendingAnn >= 0 ? '+' : ''}{cappedPendingAnn.toFixed(1)}%</div>
                              <div className="text-[9px] opacity-50">If max profit held {totalDaysToExpiry}d</div>
                            </div>
                          )}
                        </div>

                        {/* Margin & Capital */}
                        {pnl.margin_required > 0 && (
                          <div className="grid grid-cols-3 gap-3">
                            <div className="bg-base-300/30 rounded-lg p-2 text-center">
                              <div className="text-[10px] text-base-content/50 uppercase">Margin Required</div>
                              <div className="font-bold text-sm text-warning">${pnl.margin_required.toLocaleString(undefined, { minimumFractionDigits: 2 })}</div>
                            </div>
                            <div className="bg-base-300/30 rounded-lg p-2 text-center">
                              <div className="text-[10px] text-base-content/50 uppercase">Total Capital Deployed</div>
                              <div className="font-bold text-sm">${pnl.total_capital.toLocaleString(undefined, { minimumFractionDigits: 2 })}</div>
                            </div>
                            <div className="bg-base-300/30 rounded-lg p-2 text-center">
                              <div className="text-[10px] text-base-content/50 uppercase">ROI on Capital</div>
                              <div className={`font-bold text-sm ${pnl.unrealized_pnl >= 0 ? 'text-success' : 'text-error'}`}>
                                {pnl.total_capital > 0 ? `${(pnl.unrealized_pnl / pnl.total_capital * 100).toFixed(2)}%` : '-'}
                              </div>
                            </div>
                          </div>
                        )}
                      </div>
                      );
                    })()}

                    {/* Max Profit / Max Loss / Breakevens */}
                    {pnl && (pnl.max_profit != null || pnl.max_loss != null || pnl.breakevens.length > 0) && (() => {
                      const isGuaranteed = pnl.max_loss != null && pnl.max_loss >= 0;
                      const isFixedOutcome = isGuaranteed && pnl.max_profit != null && pnl.max_loss != null && Math.abs(pnl.max_loss - pnl.max_profit) < 1;
                      return (
                        <div className={`grid ${isFixedOutcome ? 'grid-cols-1 md:grid-cols-2' : 'grid-cols-2 md:grid-cols-3'} gap-3`}>
                          {pnl.max_profit != null && (
                            <div className={`bg-base-300/30 rounded-lg p-2 text-center ${pnl.max_profit >= 0 ? 'text-success' : 'text-error'}`}>
                              <div className="text-[10px] uppercase opacity-70">
                                {isFixedOutcome ? 'Guaranteed Profit (at exp)' : 'Max Profit (at exp)'}
                              </div>
                              <div className="font-bold text-sm">+${pnl.max_profit.toLocaleString(undefined, { minimumFractionDigits: 2 })}</div>
                            </div>
                          )}
                          {pnl.max_loss != null && !isFixedOutcome && (
                            <div className={`bg-base-300/30 rounded-lg p-2 text-center ${pnl.max_loss >= 0 ? 'text-success' : 'text-error'}`}>
                              <div className="text-[10px] uppercase opacity-70">
                                {isGuaranteed ? 'Min Profit (at exp)' : 'Max Loss (at exp)'}
                              </div>
                              <div className="font-bold text-sm">
                                {pnl.max_loss >= 0 ? '+' : '-'}${Math.abs(pnl.max_loss).toLocaleString(undefined, { minimumFractionDigits: 2 })}
                              </div>
                            </div>
                          )}
                          {pnl.breakevens.length > 0 && (
                            <div className="bg-base-300/30 rounded-lg p-2 text-center">
                              <div className="text-[10px] text-base-content/50 uppercase flex items-center justify-center gap-1">
                                <Target className="w-3 h-3" /> Breakevens
                              </div>
                              <div className="font-bold text-sm">
                                {pnl.breakevens.map(b => `$${b.toFixed(2)}`).join(', ')}
                              </div>
                            </div>
                          )}
                          {isGuaranteed && (
                            <div className="bg-success/10 border border-success/20 rounded-lg p-2 text-center text-success">
                              <div className="text-[10px] uppercase opacity-70">Downside Risk</div>
                              <div className="font-bold text-sm">None</div>
                            </div>
                          )}
                        </div>
                      );
                    })()}

                    {/* Legs Table */}
                    <div>
                      <h4 className="text-xs font-bold uppercase tracking-wider text-base-content/50 mb-2">Legs</h4>
                      <div className="overflow-x-auto">
                        <table className="table table-xs table-zebra">
                          <thead>
                            <tr>
                              <th>Action</th>
                              <th>Type</th>
                              <th>Strike</th>
                              <th>Qty</th>
                              <th>Expiry</th>
                              <th>Entry</th>
                              {pnl && <th>Bid</th>}
                              {pnl && <th>Ask</th>}
                              {pnl && <th>Mid</th>}
                              {pnl && <th>Override</th>}
                              {pnl && <th>Leg P&L</th>}
                            </tr>
                          </thead>
                          <tbody>
                            {trade.legs_data?.map((leg: any, i: number) => {
                              const currentQuote = pnl?.current_quotes?.find((q: any) => q.leg === i);
                              const entryPrice = trade.entry_prices?.[i];
                              const legExpiry = leg.expiration || leg.exp || expiryDate || '-';
                              const ep = entryPrice?.price ?? leg.price ?? leg.midPrice ?? leg.mid ?? 0;
                              const manualKey = `${trade.id}_${i}`;
                              const manualVal = manualPrices[manualKey];
                              const cm = manualVal ?? currentQuote?.mid ?? null;
                              const sign = leg.action?.toUpperCase().includes('BUY') ? 1 : -1;
                              const legPnl = cm != null && ep ? (cm - ep) * sign * (leg.qty || 1) * 100 : null;
                              const bidAskSpread = currentQuote?.bid != null && currentQuote?.ask != null
                                ? (currentQuote.ask - currentQuote.bid).toFixed(2) : null;
                              return (
                                <tr key={i}>
                                  <td className={leg.action?.toUpperCase().includes('BUY') ? 'text-success' : 'text-error'}>
                                    {leg.action}
                                  </td>
                                  <td>{leg.type}</td>
                                  <td>${leg.strike}</td>
                                  <td>{leg.qty}</td>
                                  <td className="text-base-content/60">{legExpiry}</td>
                                  <td>${ep?.toFixed(2) ?? '-'}</td>
                                  {pnl && (
                                    <td className="text-base-content/60">
                                      {currentQuote?.bid != null ? `$${currentQuote.bid.toFixed(2)}` : '-'}
                                    </td>
                                  )}
                                  {pnl && (
                                    <td className="text-base-content/60">
                                      {currentQuote?.ask != null ? `$${currentQuote.ask.toFixed(2)}` : '-'}
                                      {bidAskSpread && <span className="text-[9px] opacity-40 ml-0.5">({bidAskSpread})</span>}
                                    </td>
                                  )}
                                  {pnl && (
                                    <td className={manualVal != null ? 'text-info' : ''}>
                                      {cm != null ? `$${cm.toFixed(2)}` : currentQuote?.error || '-'}
                                    </td>
                                  )}
                                  {pnl && (
                                    <td>
                                      <input
                                        className="input input-bordered input-xs w-16 text-right"
                                        type="number" step="0.01"
                                        placeholder={currentQuote?.mid != null ? currentQuote.mid.toFixed(2) : '-'}
                                        value={manualVal ?? ''}
                                        onChange={e => {
                                          const v = e.target.value;
                                          setManualPrices(prev => {
                                            const next = { ...prev };
                                            if (v === '') { delete next[manualKey]; } else { next[manualKey] = parseFloat(v) || 0; }
                                            return next;
                                          });
                                        }}
                                      />
                                    </td>
                                  )}
                                  {pnl && (
                                    <td className={legPnl != null ? (legPnl >= 0 ? 'text-success' : 'text-error') : ''}>
                                      {legPnl != null ? `${legPnl >= 0 ? '+' : ''}$${legPnl.toFixed(2)}` : '-'}
                                    </td>
                                  )}
                                </tr>
                              );
                            })}
                          </tbody>
                        </table>
                      </div>
                    </div>

                    {/* Per-Leg Greeks */}
                    {pnl && pnl.greeks && pnl.greeks.length > 0 && (
                      <div>
                        <h4 className="text-xs font-bold uppercase tracking-wider text-base-content/50 mb-2 flex items-center gap-1">
                          <Activity className="w-3 h-3" /> Per-Leg Greeks
                        </h4>
                        <div className="overflow-x-auto">
                          <table className="table table-xs table-zebra">
                            <thead>
                              <tr><th>Leg</th><th>IV</th><th>Delta</th><th>Gamma</th><th>Theta</th><th>Vega</th></tr>
                            </thead>
                            <tbody>
                              {pnl.greeks.map((g: any, i: number) => (
                                <tr key={i}>
                                  <td>{g.strike} {g.type}</td>
                                  <td>{g.iv != null ? `${g.iv}%` : '-'}</td>
                                  <td>{g.delta ?? '-'}</td>
                                  <td>{g.gamma ?? '-'}</td>
                                  <td>{g.theta ?? '-'}</td>
                                  <td>{g.vega ?? '-'}</td>
                                </tr>
                              ))}
                            </tbody>
                          </table>
                        </div>
                      </div>
                    )}

                    {/* Net Portfolio Greeks */}
                    {pnl && pnl.net_greeks && (
                      <div>
                        <h4 className="text-xs font-bold uppercase tracking-wider text-base-content/50 mb-2 flex items-center gap-1">
                          <BarChart3 className="w-3 h-3" /> Net Portfolio Greeks
                        </h4>
                        <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
                          <div className="bg-base-300/30 rounded-lg p-2 text-center">
                            <div className="text-[10px] text-base-content/50 uppercase">Net Delta</div>
                            <div className={`font-bold text-sm ${pnl.net_greeks.delta >= 0 ? 'text-success' : 'text-error'}`}>
                              {pnl.net_greeks.delta >= 0 ? '+' : ''}{pnl.net_greeks.delta.toFixed(2)}
                            </div>
                          </div>
                          <div className="bg-base-300/30 rounded-lg p-2 text-center">
                            <div className="text-[10px] text-base-content/50 uppercase">Net Gamma</div>
                            <div className="font-bold text-sm">{pnl.net_greeks.gamma.toFixed(4)}</div>
                          </div>
                          <div className="bg-base-300/30 rounded-lg p-2 text-center">
                            <div className="text-[10px] text-base-content/50 uppercase">Net Theta</div>
                            <div className={`font-bold text-sm ${pnl.net_greeks.theta >= 0 ? 'text-success' : 'text-error'}`}>
                              {pnl.net_greeks.theta >= 0 ? '+' : ''}{pnl.net_greeks.theta.toFixed(2)}/day
                            </div>
                          </div>
                          <div className="bg-base-300/30 rounded-lg p-2 text-center">
                            <div className="text-[10px] text-base-content/50 uppercase">Net Vega</div>
                            <div className="font-bold text-sm">{pnl.net_greeks.vega.toFixed(2)}</div>
                          </div>
                        </div>
                      </div>
                    )}

                    {/* P&L vs Price Chart */}
                    {pnlChartData && (
                      <div>
                        <div className="h-48 bg-base-300/20 rounded-lg p-2">
                          <Line data={pnlChartData} options={chartOptions('P&L vs Underlying Price Change (Now vs At Expiry)', true) as any} />
                        </div>
                      </div>
                    )}

                    {/* Theta Decay Chart */}
                    {thetaChartData && (
                      <div>
                        <div className="h-48 bg-base-300/20 rounded-lg p-2">
                          <Line data={thetaChartData} options={chartOptions(`P&L Over Time to Expiry${expiryDate ? ` (${expiryDate})` : ''}`) as any} />
                        </div>
                      </div>
                    )}

                    {/* Trade Intelligence — Institutional Analysis */}
                    {pnl?.analysis && (
                      <div className="space-y-2">
                        <div className="text-xs font-semibold text-base-content/70 uppercase tracking-wider">Trade Intelligence</div>

                        {/* Hold vs Close Signal */}
                        <div className={`rounded-lg p-3 text-center ${
                          pnl.analysis.hold_vs_close.includes('STRONG_HOLD') ? 'bg-success/20 border border-success/30' :
                          pnl.analysis.hold_vs_close === 'HOLD' ? 'bg-success/10 border border-success/20' :
                          pnl.analysis.hold_vs_close === 'CLOSE' ? 'bg-warning/15 border border-warning/30' :
                          'bg-error/20 border border-error/30'
                        }`}>
                          <div className={`text-lg font-bold ${
                            pnl.analysis.hold_vs_close.includes('HOLD') ? 'text-success' :
                            pnl.analysis.hold_vs_close === 'CLOSE' ? 'text-warning' : 'text-error'
                          }`}>
                            {pnl.analysis.hold_vs_close.replace('_', ' ')}
                          </div>
                          <div className="mt-1 space-y-0.5">
                            {pnl.analysis.hold_vs_close_reasons.map((reason, ri) => (
                              <div key={ri} className="text-[10px] text-base-content/60">{reason}</div>
                            ))}
                          </div>
                        </div>

                        {/* Metrics Grid */}
                        <div className="grid grid-cols-3 gap-2">
                          {pnl.analysis.probability_of_profit != null && (
                            <div className="bg-base-300/30 rounded-lg p-2 text-center">
                              <div className="text-[10px] text-base-content/50 uppercase">Prob of Profit</div>
                              <div className={`font-bold text-sm ${
                                pnl.analysis.probability_of_profit >= 60 ? 'text-success' :
                                pnl.analysis.probability_of_profit >= 40 ? 'text-warning' : 'text-error'
                              }`}>{pnl.analysis.probability_of_profit.toFixed(1)}%</div>
                              <div className="w-full bg-base-300 rounded-full h-1 mt-1">
                                <div className={`h-1 rounded-full ${
                                  pnl.analysis.probability_of_profit >= 60 ? 'bg-success' :
                                  pnl.analysis.probability_of_profit >= 40 ? 'bg-warning' : 'bg-error'
                                }`} style={{ width: `${Math.min(100, pnl.analysis.probability_of_profit)}%` }}></div>
                              </div>
                            </div>
                          )}
                          {pnl.analysis.expected_value != null && (
                            <div className="bg-base-300/30 rounded-lg p-2 text-center">
                              <div className="text-[10px] text-base-content/50 uppercase">Expected Value</div>
                              <div className={`font-bold text-sm ${pnl.analysis.expected_value >= 0 ? 'text-success' : 'text-error'}`}>
                                ${pnl.analysis.expected_value.toLocaleString(undefined, { minimumFractionDigits: 0 })}
                              </div>
                            </div>
                          )}
                          {pnl.analysis.risk_reward_ratio != null && (
                            <div className="bg-base-300/30 rounded-lg p-2 text-center">
                              <div className="text-[10px] text-base-content/50 uppercase">Risk/Reward</div>
                              <div className="font-bold text-sm">{pnl.analysis.risk_reward_ratio.toFixed(2)}x</div>
                            </div>
                          )}
                          {pnl.analysis.annualized_return_to_expiry != null && (
                            <div className="bg-base-300/30 rounded-lg p-2 text-center">
                              <div className="text-[10px] text-base-content/50 uppercase">Ann. Return</div>
                              <div className={`font-bold text-sm ${pnl.analysis.annualized_return_to_expiry >= 0 ? 'text-success' : 'text-error'}`}>
                                {pnl.analysis.annualized_return_to_expiry.toFixed(1)}%
                              </div>
                            </div>
                          )}
                          {pnl.analysis.kelly_fraction != null && (
                            <div className="bg-base-300/30 rounded-lg p-2 text-center">
                              <div className="text-[10px] text-base-content/50 uppercase">Kelly Size</div>
                              <div className="font-bold text-sm">{(pnl.analysis.kelly_fraction * 100).toFixed(1)}%</div>
                            </div>
                          )}
                          <div className="bg-base-300/30 rounded-lg p-2 text-center">
                            <div className="text-[10px] text-base-content/50 uppercase">Theta/Day</div>
                            <div className={`font-bold text-sm ${pnl.analysis.theta_burn_rate_day >= 0 ? 'text-success' : 'text-error'}`}>
                              ${pnl.analysis.theta_burn_rate_day.toFixed(2)}
                              <span className="text-[9px] opacity-60"> ({pnl.analysis.theta_burn_rate_pct.toFixed(2)}%)</span>
                            </div>
                          </div>
                        </div>

                        {/* Theta Breakeven + DTE warnings */}
                        <div className="flex gap-2 text-[10px]">
                          {pnl.analysis.days_to_theta_breakeven != null && (
                            <div className={`px-2 py-1 rounded ${pnl.analysis.days_to_theta_breakeven < 7 ? 'bg-error/20 text-error' : 'bg-base-300/30'}`}>
                              Theta erodes profit in ~{pnl.analysis.days_to_theta_breakeven.toFixed(0)} days
                            </div>
                          )}
                          <div className={`px-2 py-1 rounded ${pnl.analysis.dte_remaining <= 7 ? 'bg-warning/20 text-warning' : 'bg-base-300/30'}`}>
                            {pnl.analysis.dte_remaining} DTE
                          </div>
                        </div>

                        {/* LLM Trade Advisor */}
                        <div className="bg-base-200/50 rounded-lg p-3 border border-white/[0.05] space-y-2">
                          <div className="flex items-center justify-between">
                            <h5 className="text-xs font-bold uppercase tracking-wider flex items-center gap-1 text-secondary">
                              <Brain className="w-3.5 h-3.5" /> AI Trade Advisor
                            </h5>
                            <button
                              className="btn btn-secondary btn-xs gap-1"
                              onClick={() => handleAskAdvisor(trade.id)}
                              disabled={advisorLoading && advisorTradeId === trade.id}
                            >
                              {advisorLoading && advisorTradeId === trade.id
                                ? <Loader2 className="w-3 h-3 animate-spin" />
                                : <Brain className="w-3 h-3" />}
                              Full Analysis
                            </button>
                          </div>
                          <div className="flex gap-2">
                            <input
                              className="input input-bordered input-xs flex-1"
                              value={advisorTradeId === trade.id ? advisorQuestion : ''}
                              onChange={e => { setAdvisorTradeId(trade.id); setAdvisorQuestion(e.target.value); }}
                              placeholder="Ask: Should I roll this position? What's my gamma risk? ..."
                              onKeyDown={e => { if (e.key === 'Enter') handleAskAdvisor(trade.id, advisorQuestion); }}
                            />
                            <button
                              className="btn btn-secondary btn-xs gap-1"
                              onClick={() => handleAskAdvisor(trade.id, advisorQuestion)}
                              disabled={(advisorLoading && advisorTradeId === trade.id) || !(advisorTradeId === trade.id && advisorQuestion.trim())}
                            >
                              <Send className="w-3 h-3" /> Ask
                            </button>
                          </div>
                          {advisorLoading && advisorTradeId === trade.id && (
                            <div className="flex items-center gap-2 text-xs text-secondary py-2">
                              <Loader2 className="w-4 h-4 animate-spin" />
                              Analyzing position with all Greeks, scenarios, and market data...
                            </div>
                          )}
                          {advisorError && advisorTradeId === trade.id && (
                            <div className="text-xs text-error bg-error/10 rounded px-2 py-1">{advisorError}</div>
                          )}
                          {advisorResponse && advisorTradeId === trade.id && (
                            <div className="bg-base-300/30 rounded-lg p-3 text-xs text-base-content/80 whitespace-pre-wrap leading-relaxed max-h-96 overflow-y-auto">
                              {advisorResponse}
                            </div>
                          )}
                        </div>
                      </div>
                    )}

                    {/* Closed trade summary */}
                    {tab === 'closed' && trade.exit_net != null && (
                      <div className="grid grid-cols-3 gap-3">
                        <div className="bg-base-300/30 rounded-lg p-2 text-center">
                          <div className="text-[10px] text-base-content/50 uppercase">Entry</div>
                          <div className="font-bold text-sm">${trade.entry_net_debit?.toFixed(2)}</div>
                        </div>
                        <div className="bg-base-300/30 rounded-lg p-2 text-center">
                          <div className="text-[10px] text-base-content/50 uppercase">Exit</div>
                          <div className="font-bold text-sm">${trade.exit_net?.toFixed(2)}</div>
                        </div>
                        <div className={`bg-base-300/30 rounded-lg p-2 text-center ${
                          (trade.exit_net! - trade.entry_net_debit!) >= 0 ? 'text-success' : 'text-error'
                        }`}>
                          <div className="text-[10px] uppercase opacity-70">Realized P&L</div>
                          <div className="font-bold text-sm">
                            ${(trade.exit_net! - trade.entry_net_debit!).toFixed(2)}
                          </div>
                        </div>
                      </div>
                    )}
                  </div>
                )}
              </div>
            );
          })}
        </div>
    </div>
  );
}
