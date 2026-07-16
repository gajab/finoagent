import React, { useState } from 'react';
import {
  ArrowLeftRight, Loader2, AlertTriangle, DollarSign,
  TrendingUp, TrendingDown, BarChart3, Info, GitCompareArrows,
  ChevronDown, ChevronUp, Sparkles, Search,
  Shield, Zap, Target, Activity, Gauge, Plus, Trash2,
  PieChart, Brain, AlertCircle, Layers,
} from 'lucide-react';
import { Line, Bar } from 'react-chartjs-2';
import { computeSingleStockLongShort, computePairTrade, fetchPairSuggestions, build130_30Portfolio } from '../api';
import type {
  SingleStockLongShortResponse, PairTradeResponse,
  LongShortStrategy as LongShortStrategyType,
  PairCandidate, PairSuggestionsResponse,
  QuantAnalytics, PairAnalytics,
  Portfolio130_30Response, PortfolioPosition,
} from '../types';

/* ------------------------------------------------------------------ */
/*  Quant Analytics Dashboard Sub-components                          */
/* ------------------------------------------------------------------ */

const scoreColor = (v: number) =>
  v >= 70 ? 'text-success' : v >= 40 ? 'text-warning' : 'text-error';

const scoreBg = (v: number) =>
  v >= 70 ? 'bg-success/20 border-success/30' : v >= 40 ? 'bg-warning/20 border-warning/30' : 'bg-error/20 border-error/30';

const signalBadge = (signal: string) => {
  switch (signal) {
    case 'STRONG_ENTRY': return 'badge-success';
    case 'ENTRY': return 'badge-success badge-outline';
    case 'NEUTRAL': return 'badge-ghost';
    case 'CAUTION': return 'badge-warning badge-outline';
    case 'AVOID': return 'badge-error';
    default: return 'badge-ghost';
  }
};

function QuantAnalyticsPanel({ qa, ticker, label }: { qa: QuantAnalytics; ticker: string; label?: string }) {
  const [expanded, setExpanded] = useState(true);
  const fs = qa.factor_scores;
  const rd = qa.risk_decomposition;
  const tr = qa.tail_risk;
  const sig = qa.signals;
  const ps = qa.position_sizing;

  return (
    <div className="bg-base-200/40 rounded-xl border border-white/[0.03] overflow-hidden">
      <button
        className="w-full flex items-center gap-2 p-3 text-left hover:bg-base-200/60 transition-colors"
        onClick={() => setExpanded(!expanded)}
      >
        <Activity className="w-4 h-4 text-info" />
        <span className="text-sm font-semibold">
          Quant Analytics{label ? ` — ${label}` : ''}: {ticker}
        </span>
        <span className={`badge badge-sm ml-auto mr-2 ${signalBadge(sig.entry_signal)}`}>
          {sig.entry_signal.replace('_', ' ')}
        </span>
        {expanded ? <ChevronUp className="w-4 h-4" /> : <ChevronDown className="w-4 h-4" />}
      </button>

      {expanded && (
        <div className="p-3 pt-0 space-y-3">
          {/* Factor Scores */}
          <div>
            <h5 className="text-xs font-semibold text-base-content/60 uppercase tracking-wider mb-2 flex items-center gap-1">
              <Target className="w-3 h-3" /> Factor Scores
            </h5>
            <div className="flex items-center gap-3 mb-2">
              <div className={`text-center px-3 py-2 rounded-lg border ${scoreBg(fs.composite)}`}>
                <p className="text-[10px] uppercase tracking-wider text-base-content/50">Composite</p>
                <p className={`text-2xl font-bold ${scoreColor(fs.composite)}`}>{fs.composite.toFixed(0)}</p>
              </div>
              <div className="flex-1 space-y-1">
                {(['value', 'momentum', 'quality', 'defensive'] as const).map((key) => (
                  <div key={key} className="flex items-center gap-2">
                    <span className="text-[10px] w-16 text-base-content/50 capitalize">{key}</span>
                    <div className="flex-1 bg-base-300 rounded-full h-2">
                      <div
                        className={`h-2 rounded-full ${fs[key] >= 70 ? 'bg-success' : fs[key] >= 40 ? 'bg-warning' : 'bg-error'}`}
                        style={{ width: `${Math.min(fs[key], 100)}%` }}
                      />
                    </div>
                    <span className={`text-xs font-mono w-8 text-right ${scoreColor(fs[key])}`}>{fs[key].toFixed(0)}</span>
                  </div>
                ))}
              </div>
            </div>
          </div>

          {/* Risk Decomposition */}
          <div>
            <h5 className="text-xs font-semibold text-base-content/60 uppercase tracking-wider mb-2 flex items-center gap-1">
              <Shield className="w-3 h-3" /> Risk Decomposition
            </h5>
            <div className="grid grid-cols-2 sm:grid-cols-4 gap-2">
              <div className="bg-base-200/60 rounded-lg p-2 text-center">
                <p className="text-[10px] text-base-content/40">Beta</p>
                <p className={`text-sm font-bold ${rd.beta > 1.2 ? 'text-error' : rd.beta < 0.8 ? 'text-info' : ''}`}>{rd.beta.toFixed(2)}</p>
              </div>
              <div className="bg-base-200/60 rounded-lg p-2 text-center">
                <p className="text-[10px] text-base-content/40">Alpha (Ann.)</p>
                <p className={`text-sm font-bold ${rd.alpha_annual > 0 ? 'text-success' : 'text-error'}`}>{(rd.alpha_annual * 100).toFixed(1)}%</p>
              </div>
              <div className="bg-base-200/60 rounded-lg p-2 text-center">
                <p className="text-[10px] text-base-content/40">R²</p>
                <p className="text-sm font-bold">{(rd.r_squared * 100).toFixed(0)}%</p>
              </div>
              <div className="bg-base-200/60 rounded-lg p-2 text-center">
                <p className="text-[10px] text-base-content/40">Info Ratio</p>
                <p className={`text-sm font-bold ${rd.information_ratio > 0.5 ? 'text-success' : rd.information_ratio > 0 ? 'text-warning' : 'text-error'}`}>{rd.information_ratio.toFixed(2)}</p>
              </div>
            </div>
            {rd.downside_beta > rd.beta * 1.1 && (
              <p className="text-[10px] text-warning mt-1">Downside beta ({rd.downside_beta.toFixed(2)}) exceeds upside — asymmetric tail risk</p>
            )}
          </div>

          {/* Tail Risk */}
          <div>
            <h5 className="text-xs font-semibold text-base-content/60 uppercase tracking-wider mb-2 flex items-center gap-1">
              <Zap className="w-3 h-3" /> Tail Risk
            </h5>
            <div className="grid grid-cols-3 sm:grid-cols-6 gap-2">
              {[
                { label: 'VaR 95%', val: `${tr.var_95.toFixed(1)}%`, warn: tr.var_95 < -3 },
                { label: 'CVaR 95%', val: `${tr.cvar_95.toFixed(1)}%`, warn: tr.cvar_95 < -5 },
                { label: 'Sortino', val: tr.sortino.toFixed(2), warn: tr.sortino < 1 },
                { label: 'Calmar', val: tr.calmar.toFixed(2), warn: tr.calmar < 0.5 },
                { label: 'Skew', val: tr.skewness.toFixed(2), warn: tr.skewness < -0.5 },
                { label: 'Tail Ratio', val: tr.tail_ratio.toFixed(2), warn: tr.tail_ratio < 0.8 },
              ].map((m) => (
                <div key={m.label} className="bg-base-200/60 rounded-lg p-1.5 text-center">
                  <p className="text-[9px] text-base-content/40">{m.label}</p>
                  <p className={`text-xs font-bold ${m.warn ? 'text-warning' : ''}`}>{m.val}</p>
                </div>
              ))}
            </div>
          </div>

          {/* Signals */}
          <div>
            <h5 className="text-xs font-semibold text-base-content/60 uppercase tracking-wider mb-2 flex items-center gap-1">
              <Gauge className="w-3 h-3" /> Signals
            </h5>
            <div className="flex flex-wrap gap-2 mb-2">
              <div className="bg-base-200/60 rounded-lg px-2 py-1">
                <span className="text-[9px] text-base-content/40">RSI</span>
                <span className={`text-xs font-bold ml-1 ${sig.rsi_14 < 30 ? 'text-success' : sig.rsi_14 > 70 ? 'text-error' : ''}`}>{sig.rsi_14}</span>
              </div>
              <div className="bg-base-200/60 rounded-lg px-2 py-1">
                <span className="text-[9px] text-base-content/40">Bollinger</span>
                <span className="text-xs font-bold ml-1">{(sig.bollinger_pct * 100).toFixed(0)}%</span>
              </div>
              {sig.above_sma_50 != null && (
                <div className={`rounded-lg px-2 py-1 text-[10px] font-medium ${sig.above_sma_50 ? 'bg-success/15 text-success' : 'bg-error/15 text-error'}`}>
                  {sig.above_sma_50 ? '> 50d MA' : '< 50d MA'}
                </div>
              )}
              {sig.above_sma_200 != null && (
                <div className={`rounded-lg px-2 py-1 text-[10px] font-medium ${sig.above_sma_200 ? 'bg-success/15 text-success' : 'bg-error/15 text-error'}`}>
                  {sig.above_sma_200 ? '> 200d MA' : '< 200d MA'}
                </div>
              )}
              <div className="bg-base-200/60 rounded-lg px-2 py-1">
                <span className="text-[9px] text-base-content/40">Mom 12-1</span>
                <span className={`text-xs font-bold ml-1 ${sig.momentum_12_1 > 0 ? 'text-success' : 'text-error'}`}>{sig.momentum_12_1 > 0 ? '+' : ''}{sig.momentum_12_1.toFixed(1)}%</span>
              </div>
              {sig.spread_regime && (
                <div className="bg-base-200/60 rounded-lg px-2 py-1">
                  <span className="text-[9px] text-base-content/40">Spread</span>
                  <span className="text-xs font-bold ml-1">{sig.spread_regime}</span>
                </div>
              )}
            </div>
            {sig.entry_reasons.length > 0 && (
              <div className="text-[10px] text-base-content/50 flex flex-wrap gap-1">
                {sig.entry_reasons.map((r, i) => (
                  <span key={i} className="bg-base-300/50 rounded px-1.5 py-0.5">{r}</span>
                ))}
              </div>
            )}
          </div>

          {/* Position Sizing */}
          <div>
            <h5 className="text-xs font-semibold text-base-content/60 uppercase tracking-wider mb-2 flex items-center gap-1">
              <DollarSign className="w-3 h-3" /> Position Sizing
            </h5>
            <div className="grid grid-cols-2 sm:grid-cols-4 gap-2">
              <div className="bg-base-200/60 rounded-lg p-2 text-center">
                <p className="text-[10px] text-base-content/40">Kelly (Half)</p>
                <p className="text-sm font-bold">{(ps.kelly_half * 100).toFixed(1)}%</p>
              </div>
              <div className="bg-base-200/60 rounded-lg p-2 text-center">
                <p className="text-[10px] text-base-content/40">Risk Parity</p>
                <p className="text-sm font-bold">{(ps.risk_parity_pct * 100).toFixed(1)}%</p>
              </div>
              <div className="bg-info/10 border border-info/20 rounded-lg p-2 text-center">
                <p className="text-[10px] text-info">Recommended</p>
                <p className="text-sm font-bold text-info">{(ps.recommended_pct * 100).toFixed(1)}%</p>
              </div>
              <div className="bg-info/10 border border-info/20 rounded-lg p-2 text-center">
                <p className="text-[10px] text-info">Dollar Size</p>
                <p className="text-sm font-bold text-info">${ps.recommended_dollars.toLocaleString(undefined, { maximumFractionDigits: 0 })}</p>
              </div>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}

function PairAnalyticsPanel({ pa, longTicker, shortTicker }: { pa: PairAnalytics; longTicker: string; shortTicker: string }) {
  return (
    <div className="bg-base-200/40 rounded-xl border border-white/[0.03] p-3">
      <h5 className="text-xs font-semibold text-base-content/60 uppercase tracking-wider mb-2 flex items-center gap-1">
        <GitCompareArrows className="w-3 h-3" /> Pair Factor Edge ({longTicker} vs {shortTicker})
      </h5>
      <div className="grid grid-cols-2 sm:grid-cols-4 gap-2">
        {[
          { label: 'Value Edge', val: pa.relative_value },
          { label: 'Momentum Edge', val: pa.relative_momentum },
          { label: 'Quality Edge', val: pa.relative_quality },
          { label: 'Composite Edge', val: pa.relative_composite },
        ].map((m) => (
          <div key={m.label} className="bg-base-200/60 rounded-lg p-2 text-center">
            <p className="text-[10px] text-base-content/40">{m.label}</p>
            <p className={`text-sm font-bold ${m.val > 5 ? 'text-success' : m.val < -5 ? 'text-error' : ''}`}>
              {m.val > 0 ? '+' : ''}{m.val.toFixed(1)}
            </p>
          </div>
        ))}
      </div>
      <div className="flex items-center gap-3 mt-2 text-xs">
        {pa.long_entry_signal && (
          <span>Long signal: <span className={`badge badge-xs ${signalBadge(pa.long_entry_signal)}`}>{pa.long_entry_signal.replace('_', ' ')}</span></span>
        )}
        {pa.short_entry_signal && (
          <span>Short signal: <span className={`badge badge-xs ${signalBadge(pa.short_entry_signal)}`}>{pa.short_entry_signal.replace('_', ' ')}</span></span>
        )}
        {pa.spread_z_score != null && (
          <span className="text-base-content/50">Spread Z: {pa.spread_z_score.toFixed(2)}</span>
        )}
      </div>
    </div>
  );
}

interface Props {
  ticker?: string;
}

type Mode = 'portfolio' | 'single' | 'pair';

interface PositionRow { ticker: string; shares: string }

export function LongShortStrategy({ ticker: defaultTicker }: Props) {
  const [mode, setMode] = useState<Mode>('portfolio');

  // --- 130/30 Portfolio form ---
  const [portInvestment, setPortInvestment] = useState(500000);
  const [portLeverage, setPortLeverage] = useState('130/30');
  const [portLongRows, setPortLongRows] = useState<PositionRow[]>([
    { ticker: defaultTicker || '', shares: '' },
  ]);
  const [portShortRows, setPortShortRows] = useState<PositionRow[]>([
    { ticker: '', shares: '' },
  ]);
  const [portTaxST, setPortTaxST] = useState(37);
  const [portTaxLT, setPortTaxLT] = useState(20);
  const [portLoading, setPortLoading] = useState(false);
  const [portError, setPortError] = useState<string | null>(null);
  const [portResult, setPortResult] = useState<Portfolio130_30Response | null>(null);
  const [portBookTab, setPortBookTab] = useState<'long' | 'short'>('long');
  const [expandedPosition, setExpandedPosition] = useState<string | null>(null);
  const [portScenarioTab, setPortScenarioTab] = useState<'chart' | 'table' | 'stress' | 'leverage' | 'impact'>('chart');

  // Single stock form
  const [singleTicker, setSingleTicker] = useState(defaultTicker || '');
  const [singleAmount, setSingleAmount] = useState(50000);
  const [singleLoading, setSingleLoading] = useState(false);
  const [singleError, setSingleError] = useState<string | null>(null);
  const [singleResult, setSingleResult] = useState<SingleStockLongShortResponse | null>(null);
  const [selectedStrategy, setSelectedStrategy] = useState(0);

  // Pair trade form
  const [longTicker, setLongTicker] = useState(defaultTicker || '');
  const [shortTicker, setShortTicker] = useState('');
  const [pairAmount, setPairAmount] = useState(50000);
  const [pairLoading, setPairLoading] = useState(false);
  const [pairError, setPairError] = useState<string | null>(null);
  const [pairResult, setPairResult] = useState<PairTradeResponse | null>(null);

  // Pair suggestions
  const [suggestLoading, setSuggestLoading] = useState(false);
  const [suggestError, setSuggestError] = useState<string | null>(null);
  const [suggestions, setSuggestions] = useState<PairCandidate[]>([]);
  const [showSuggestions, setShowSuggestions] = useState(false);
  const [suggestTicker, setSuggestTicker] = useState('');

  const [showExplanation, setShowExplanation] = useState(false);

  const handleSingleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    setSingleLoading(true);
    setSingleError(null);
    setSingleResult(null);
    setSelectedStrategy(0);
    try {
      const data = await computeSingleStockLongShort(singleTicker.toUpperCase(), singleAmount);
      if (data.error) {
        setSingleError(data.error);
      } else {
        setSingleResult(data);
      }
    } catch (err: any) {
      setSingleError(err?.message || 'Failed to compute strategy');
    } finally {
      setSingleLoading(false);
    }
  };

  const handlePairSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    setPairLoading(true);
    setPairError(null);
    setPairResult(null);
    try {
      const data = await computePairTrade(longTicker.toUpperCase(), shortTicker.toUpperCase(), pairAmount);
      if (data.error) {
        setPairError(data.error);
      } else {
        setPairResult(data);
      }
    } catch (err: any) {
      setPairError(err?.message || 'Failed to compute pair trade');
    } finally {
      setPairLoading(false);
    }
  };

  const handleSuggestPairs = async () => {
    if (!longTicker.trim()) return;
    setSuggestLoading(true);
    setSuggestError(null);
    setSuggestions([]);
    setShowSuggestions(true);
    setSuggestTicker(longTicker.toUpperCase());
    try {
      const data = await fetchPairSuggestions(longTicker.toUpperCase());
      if (data.message && data.candidates.length === 0) {
        setSuggestError(data.message);
      } else {
        setSuggestions(data.candidates);
      }
    } catch (err: any) {
      setSuggestError(err?.message || 'Failed to fetch pair suggestions');
    } finally {
      setSuggestLoading(false);
    }
  };

  const handleSelectCandidate = (candidate: PairCandidate) => {
    setShortTicker(candidate.ticker);
    setShowSuggestions(false);
  };

  // --- 130/30 Portfolio handlers ---
  const updatePortRow = (side: 'long' | 'short', idx: number, field: 'ticker' | 'shares', val: string) => {
    const setter = side === 'long' ? setPortLongRows : setPortShortRows;
    setter(prev => prev.map((r, i) => i === idx ? { ...r, [field]: field === 'ticker' ? val.toUpperCase() : val } : r));
  };
  const addPortRow = (side: 'long' | 'short') => {
    const setter = side === 'long' ? setPortLongRows : setPortShortRows;
    setter(prev => [...prev, { ticker: '', shares: '' }]);
  };
  const removePortRow = (side: 'long' | 'short', idx: number) => {
    const setter = side === 'long' ? setPortLongRows : setPortShortRows;
    setter(prev => prev.filter((_, i) => i !== idx));
  };

  const handlePortfolioSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    setPortLoading(true);
    setPortError(null);
    setPortResult(null);
    try {
      const longs = portLongRows.filter(r => r.ticker.trim()).map(r => ({
        ticker: r.ticker.trim(),
        shares: r.shares ? parseFloat(r.shares) : null,
      }));
      const shorts = portShortRows.filter(r => r.ticker.trim()).map(r => ({
        ticker: r.ticker.trim(),
        shares: r.shares ? parseFloat(r.shares) : null,
      }));
      if (longs.length === 0) { setPortError('Add at least one long position'); return; }
      if (shorts.length === 0) { setPortError('Add at least one short position'); return; }

      const data = await build130_30Portfolio({
        long_positions: longs,
        short_positions: shorts,
        investment_amount: portInvestment,
        leverage_ratio: portLeverage,
        tax_rate_st: portTaxST / 100,
        tax_rate_lt: portTaxLT / 100,
      });
      if (data.error) setPortError(data.error);
      else setPortResult(data);
    } catch (err: any) {
      setPortError(err?.message || 'Portfolio build failed');
    } finally {
      setPortLoading(false);
    }
  };

  const fmtMoney = (v: number | null) =>
    v != null ? `$${v.toLocaleString(undefined, { maximumFractionDigits: 0 })}` : '—';

  const fmtPct = (v: number | null, decimals = 1) =>
    v != null ? `${v.toFixed(decimals)}%` : '—';

  return (
    <div className="space-y-4">
      {/* Explanation Toggle */}
      <div className="bg-base-200/40 rounded-xl p-4 border border-white/[0.03]">
        <button
          className="flex items-center gap-2 text-sm w-full text-left"
          onClick={() => setShowExplanation(!showExplanation)}
        >
          <Info className="w-5 h-5 text-info flex-shrink-0" />
          <span className="font-semibold text-base-content">What is Long/Short Equity?</span>
          {showExplanation ? <ChevronUp className="w-4 h-4 ml-auto" /> : <ChevronDown className="w-4 h-4 ml-auto" />}
        </button>
        {showExplanation && (
          <div className="text-sm text-base-content/70 mt-2 space-y-2">
            <p>
              Long/Short equity strategies aim to profit from stock selection while reducing market risk.
              By going long on stocks you believe will outperform and short on related assets, you can
              isolate the alpha (stock-specific return) from the beta (market/sector return).
            </p>
            <div className="grid grid-cols-1 md:grid-cols-2 gap-3 mt-2">
              <div className="bg-base-200/30 rounded-xl p-3 border border-white/[0.03]">
                <p className="font-semibold text-xs mb-1 flex items-center gap-1">
                  <TrendingUp className="w-3.5 h-3.5 text-success" />
                  Single Stock Long/Short
                </p>
                <p className="text-xs text-base-content/60">
                  Go long on your conviction stock and short its sector ETF to hedge away sector risk.
                  You bet on the company, not the sector.
                </p>
              </div>
              <div className="bg-base-200/30 rounded-xl p-3 border border-white/[0.03]">
                <p className="font-semibold text-xs mb-1 flex items-center gap-1">
                  <GitCompareArrows className="w-3.5 h-3.5 text-info" />
                  Pair Trade
                </p>
                <p className="text-xs text-base-content/60">
                  Go long on one stock and short another correlated stock. Example: long a strong
                  media company, short a weak one. You profit from relative outperformance.
                </p>
              </div>
            </div>
          </div>
        )}
      </div>

      {/* Mode Tabs */}
      <div className="tabs tabs-boxed bg-base-200/40 p-1 w-fit border border-white/[0.03] rounded-xl">
        <button
          className={`tab tab-sm ${mode === 'portfolio' ? 'tab-active' : ''}`}
          onClick={() => setMode('portfolio')}
        >
          <Layers className="w-3.5 h-3.5 mr-1" />
          130/30 Portfolio
        </button>
        <button
          className={`tab tab-sm ${mode === 'single' ? 'tab-active' : ''}`}
          onClick={() => setMode('single')}
        >
          <TrendingUp className="w-3.5 h-3.5 mr-1" />
          Single Stock L/S
        </button>
        <button
          className={`tab tab-sm ${mode === 'pair' ? 'tab-active' : ''}`}
          onClick={() => setMode('pair')}
        >
          <GitCompareArrows className="w-3.5 h-3.5 mr-1" />
          Pair Trade
        </button>
      </div>

      {/* ========== 130/30 Portfolio Mode ========== */}
      {mode === 'portfolio' && (
        <div className="space-y-4">
          <form onSubmit={handlePortfolioSubmit} className="space-y-4">
            {/* Top row: Investment + Leverage + Tax Rates */}
            <div className="flex flex-wrap items-end gap-3">
              <div className="form-control">
                <label className="label py-1"><span className="label-text text-xs font-medium">Investment ($)</span></label>
                <div className="relative">
                  <DollarSign className="absolute left-2 top-1/2 -translate-y-1/2 w-3.5 h-3.5 text-base-content/40" />
                  <input type="number" className="input input-bordered input-sm w-40 pl-7"
                    value={portInvestment} onChange={e => setPortInvestment(Number(e.target.value))}
                    min={10000} step={10000} required />
                </div>
              </div>
              <div className="form-control">
                <label className="label py-1"><span className="label-text text-xs font-medium">Leverage</span></label>
                <div className="join">
                  {['120/20', '130/30', '150/50'].map(r => (
                    <button key={r} type="button"
                      className={`join-item btn btn-sm ${portLeverage === r ? 'btn-primary' : 'btn-outline'}`}
                      onClick={() => setPortLeverage(r)}>{r}</button>
                  ))}
                </div>
              </div>
              <div className="form-control">
                <label className="label py-1"><span className="label-text text-xs font-medium">ST Tax %</span></label>
                <input type="number" className="input input-bordered input-sm w-20" value={portTaxST}
                  onChange={e => setPortTaxST(Number(e.target.value))} min={0} max={60} />
              </div>
              <div className="form-control">
                <label className="label py-1"><span className="label-text text-xs font-medium">LT Tax %</span></label>
                <input type="number" className="input input-bordered input-sm w-20" value={portTaxLT}
                  onChange={e => setPortTaxLT(Number(e.target.value))} min={0} max={60} />
              </div>
            </div>

            {/* Positions Input: Long & Short side-by-side */}
            <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
              {/* Long Positions */}
              <div className="bg-success/5 border border-success/20 rounded-xl p-3">
                <div className="flex items-center justify-between mb-2">
                  <span className="text-xs font-semibold text-success flex items-center gap-1">
                    <TrendingUp className="w-3.5 h-3.5" /> Long Positions
                  </span>
                  <button type="button" className="btn btn-ghost btn-xs text-success" onClick={() => addPortRow('long')}>
                    <Plus className="w-3 h-3" /> Add
                  </button>
                </div>
                {portLongRows.map((row, i) => (
                  <div key={i} className="flex gap-2 mb-1.5 items-center">
                    <input type="text" placeholder="Ticker" className="input input-bordered input-xs w-24"
                      value={row.ticker} onChange={e => updatePortRow('long', i, 'ticker', e.target.value)} />
                    <input type="number" placeholder="Shares (opt)" className="input input-bordered input-xs w-28"
                      value={row.shares} onChange={e => updatePortRow('long', i, 'shares', e.target.value)} min={0} />
                    {portLongRows.length > 1 && (
                      <button type="button" className="btn btn-ghost btn-xs text-error" onClick={() => removePortRow('long', i)}>
                        <Trash2 className="w-3 h-3" />
                      </button>
                    )}
                  </div>
                ))}
                <p className="text-[9px] text-base-content/40 mt-1">Leave shares blank for auto-optimization</p>
              </div>

              {/* Short Positions */}
              <div className="bg-error/5 border border-error/20 rounded-xl p-3">
                <div className="flex items-center justify-between mb-2">
                  <span className="text-xs font-semibold text-error flex items-center gap-1">
                    <TrendingDown className="w-3.5 h-3.5" /> Short Positions
                  </span>
                  <button type="button" className="btn btn-ghost btn-xs text-error" onClick={() => addPortRow('short')}>
                    <Plus className="w-3 h-3" /> Add
                  </button>
                </div>
                {portShortRows.map((row, i) => (
                  <div key={i} className="flex gap-2 mb-1.5 items-center">
                    <input type="text" placeholder="Ticker" className="input input-bordered input-xs w-24"
                      value={row.ticker} onChange={e => updatePortRow('short', i, 'ticker', e.target.value)} />
                    <input type="number" placeholder="Shares (opt)" className="input input-bordered input-xs w-28"
                      value={row.shares} onChange={e => updatePortRow('short', i, 'shares', e.target.value)} min={0} />
                    {portShortRows.length > 1 && (
                      <button type="button" className="btn btn-ghost btn-xs text-error" onClick={() => removePortRow('short', i)}>
                        <Trash2 className="w-3 h-3" />
                      </button>
                    )}
                  </div>
                ))}
                <p className="text-[9px] text-base-content/40 mt-1">Leave shares blank for auto-optimization</p>
              </div>
            </div>

            <button type="submit" className="btn btn-primary btn-sm gap-2" disabled={portLoading}>
              {portLoading ? <Loader2 className="w-4 h-4 animate-spin" /> : <Layers className="w-4 h-4" />}
              {portLoading ? 'Building Portfolio...' : 'Build 130/30 Portfolio'}
            </button>
          </form>

          {portError && (
            <div className="alert alert-error text-sm"><AlertTriangle className="w-4 h-4" /><span>{portError}</span></div>
          )}

          {/* ===== RESULTS DASHBOARD ===== */}
          {portResult && (() => {
            const r = portResult;
            const rm = r.risk_metrics;
            const sa = r.scenario_analysis;
            const tp = r.tax_projections;
            const fe = r.factor_exposure;
            const params = r.parameters;
            const allPositions = portBookTab === 'long' ? r.long_positions : r.short_positions;

            return (
              <div className="space-y-5">

                {/* Section 1: Portfolio Overview Cards */}
                <div className="grid grid-cols-2 sm:grid-cols-3 lg:grid-cols-6 gap-3">
                  {[
                    { label: 'Net Exposure', val: fmtMoney(rm.net_exposure), sub: `${portLeverage} structure` },
                    { label: 'Gross Exposure', val: fmtMoney(rm.gross_exposure), sub: `${rm.long_count}L / ${rm.short_count}S` },
                    { label: 'Net Beta', val: rm.net_beta.toFixed(3), sub: rm.net_beta > 0.95 && rm.net_beta < 1.05 ? '✓ Beta-one' : '⚠ Off target', color: rm.net_beta > 0.95 && rm.net_beta < 1.05 ? 'text-success' : 'text-warning' },
                    { label: 'Volatility', val: fmtPct(rm.portfolio_volatility), sub: `TE: ${rm.estimated_tracking_error}%` },
                    { label: 'Sharpe', val: rm.sharpe_ratio.toFixed(2), color: rm.sharpe_ratio > 1 ? 'text-success' : rm.sharpe_ratio > 0 ? 'text-warning' : 'text-error' },
                    { label: 'Composite', val: fe.portfolio_composite.toFixed(0), sub: 'Factor Score', color: scoreColor(fe.portfolio_composite) },
                  ].map((c, i) => (
                    <div key={i} className="bg-base-200/40 rounded-xl p-3 text-center border border-white/[0.03]">
                      <p className="text-[10px] text-base-content/50 uppercase tracking-wider">{c.label}</p>
                      <p className={`text-lg font-bold ${c.color || ''}`}>{c.val}</p>
                      {c.sub && <p className="text-[9px] text-base-content/40">{c.sub}</p>}
                    </div>
                  ))}
                </div>

                {/* Section 2: Positions Table */}
                <div className="bg-base-200/40 rounded-xl border border-white/[0.03] overflow-hidden">
                  <div className="flex items-center justify-between p-3 border-b border-white/[0.03]">
                    <div className="tabs tabs-boxed bg-base-300/40 p-0.5">
                      <button className={`tab tab-xs ${portBookTab === 'long' ? 'tab-active' : ''}`} onClick={() => setPortBookTab('long')}>
                        Long Book ({r.long_positions.length})
                      </button>
                      <button className={`tab tab-xs ${portBookTab === 'short' ? 'tab-active' : ''}`} onClick={() => setPortBookTab('short')}>
                        Short Book ({r.short_positions.length})
                      </button>
                    </div>
                    <span className="text-xs text-base-content/50">
                      {portBookTab === 'long' ? fmtMoney(params.actual_long) : fmtMoney(params.actual_short)} total
                    </span>
                  </div>
                  <div className="overflow-x-auto">
                    <table className="table table-xs w-full">
                      <thead>
                        <tr className="text-[10px] text-base-content/50">
                          <th>#</th><th>Ticker</th><th>Name</th><th>Sector</th><th className="text-right">Price</th>
                          <th className="text-right">Shares</th><th className="text-right">Value</th><th className="text-right">Weight</th>
                          <th className="text-right">Composite</th><th className="text-right">Signal</th>
                        </tr>
                      </thead>
                      <tbody>
                        {allPositions.map((p: PortfolioPosition, i: number) => (
                          <React.Fragment key={p.ticker}>
                            <tr className="hover:bg-base-200/60 cursor-pointer text-xs"
                              onClick={() => setExpandedPosition(expandedPosition === p.ticker ? null : p.ticker)}>
                              <td className="text-base-content/40">{i + 1}</td>
                              <td className="font-bold">{p.ticker}</td>
                              <td className="text-base-content/60 max-w-[120px] truncate">{p.name}</td>
                              <td className="text-base-content/50">{p.sector}</td>
                              <td className="text-right">${p.price?.toFixed(2)}</td>
                              <td className="text-right font-medium">{p.shares}</td>
                              <td className="text-right">{fmtMoney(p.dollar_value)}</td>
                              <td className="text-right">{p.weight_pct.toFixed(1)}%</td>
                              <td className="text-right">
                                <span className={`font-bold ${scoreColor(p.factor_scores.composite)}`}>
                                  {p.factor_scores.composite.toFixed(0)}
                                </span>
                              </td>
                              <td className="text-right">
                                {p.quant_analytics?.signals && (
                                  <span className={`badge badge-xs ${signalBadge(p.quant_analytics.signals.entry_signal)}`}>
                                    {p.quant_analytics.signals.entry_signal.replace('_', ' ')}
                                  </span>
                                )}
                              </td>
                            </tr>
                            {expandedPosition === p.ticker && p.quant_analytics && (
                              <tr><td colSpan={10} className="p-0">
                                <QuantAnalyticsPanel qa={p.quant_analytics} ticker={p.ticker} />
                              </td></tr>
                            )}
                          </React.Fragment>
                        ))}
                      </tbody>
                      <tfoot>
                        <tr className="text-xs font-semibold border-t border-white/[0.05]">
                          <td colSpan={6}></td>
                          <td className="text-right">{fmtMoney(portBookTab === 'long' ? params.actual_long : params.actual_short)}</td>
                          <td className="text-right">100%</td>
                          <td className="text-right">
                            <span className={scoreColor(portBookTab === 'long' ? fe.long_composite : fe.short_composite)}>
                              {(portBookTab === 'long' ? fe.long_composite : fe.short_composite).toFixed(0)}
                            </span>
                          </td>
                          <td></td>
                        </tr>
                      </tfoot>
                    </table>
                  </div>
                </div>

                {/* Section 3: Scenario Analysis */}
                <div className="bg-base-200/40 rounded-xl border border-white/[0.03] overflow-hidden">
                  <div className="flex items-center gap-2 p-3 border-b border-white/[0.03]">
                    <BarChart3 className="w-4 h-4 text-info" />
                    <span className="text-sm font-semibold">Scenario Analysis</span>
                    <div className="ml-auto tabs tabs-boxed bg-base-300/40 p-0.5">
                      {(['chart', 'table', 'stress', 'leverage', 'impact'] as const).map(t => (
                        <button key={t} className={`tab tab-xs ${portScenarioTab === t ? 'tab-active' : ''}`}
                          onClick={() => setPortScenarioTab(t)}>
                          {t === 'chart' ? 'P&L Chart' : t === 'table' ? 'Matrix' : t === 'stress' ? 'Stress' : t === 'leverage' ? 'Leverage' : 'Impact'}
                        </button>
                      ))}
                    </div>
                  </div>
                  <div className="p-3">
                    {/* 3a: P&L Chart */}
                    {portScenarioTab === 'chart' && (
                      <div>
                        <div className="flex flex-wrap gap-3 mb-2 text-xs">
                          <span className="bg-success/10 border border-success/20 rounded px-2 py-0.5">Max Profit: {fmtMoney(sa.max_profit)}</span>
                          <span className="bg-error/10 border border-error/20 rounded px-2 py-0.5">Max Loss: {fmtMoney(sa.max_loss)}</span>
                          {sa.breakeven_market_move != null && (
                            <span className="bg-warning/10 border border-warning/20 rounded px-2 py-0.5">Breakeven: {sa.breakeven_market_move}%</span>
                          )}
                        </div>
                        <div style={{ height: 280 }}>
                          <Line
                            data={{
                              labels: sa.market_scenarios.map(s => `${s.market_move > 0 ? '+' : ''}${s.market_move}%`),
                              datasets: [
                                {
                                  label: 'Net Portfolio P&L',
                                  data: sa.market_scenarios.map(s => s.net_pnl),
                                  borderColor: '#6366f1',
                                  backgroundColor: 'rgba(99,102,241,0.1)',
                                  borderWidth: 2.5,
                                  pointRadius: 4,
                                  tension: 0.3,
                                  fill: true,
                                },
                                {
                                  label: 'Long Book P&L',
                                  data: sa.market_scenarios.map(s => s.long_pnl),
                                  borderColor: '#36d399',
                                  borderWidth: 1.5,
                                  pointRadius: 2,
                                  borderDash: [4, 2],
                                  tension: 0.3,
                                },
                                {
                                  label: 'Short Book P&L',
                                  data: sa.market_scenarios.map(s => s.short_pnl),
                                  borderColor: '#f87272',
                                  borderWidth: 1.5,
                                  pointRadius: 2,
                                  borderDash: [4, 2],
                                  tension: 0.3,
                                },
                              ],
                            }}
                            options={{
                              responsive: true, maintainAspectRatio: false,
                              plugins: {
                                legend: { labels: { font: { size: 10 }, usePointStyle: true, pointStyleWidth: 10 } },
                                tooltip: { callbacks: { label: ctx => `${ctx.dataset.label}: $${(ctx.parsed.y ?? 0).toLocaleString()}` } },
                              },
                              scales: {
                                x: { title: { display: true, text: 'Market Move', font: { size: 10 } }, ticks: { font: { size: 9 } }, grid: { color: 'rgba(255,255,255,0.05)' } },
                                y: { title: { display: true, text: 'P&L ($)', font: { size: 10 } }, ticks: { font: { size: 9 }, callback: v => `$${Number(v).toLocaleString()}` }, grid: { color: 'rgba(255,255,255,0.05)' } },
                              },
                            }}
                          />
                        </div>
                      </div>
                    )}

                    {/* 3b: Outcome Matrix */}
                    {portScenarioTab === 'table' && (
                      <div className="overflow-x-auto">
                        <table className="table table-xs w-full">
                          <thead>
                            <tr className="text-[10px] text-base-content/50">
                              <th>Market Move</th><th className="text-right">Long P&L</th><th className="text-right">Short P&L</th>
                              <th className="text-right">Net P&L</th><th className="text-right">Return %</th><th className="text-right">Portfolio Value</th>
                            </tr>
                          </thead>
                          <tbody>
                            {sa.market_scenarios.map(s => (
                              <tr key={s.market_move} className="text-xs">
                                <td className="font-medium">{s.market_move > 0 ? '+' : ''}{s.market_move}%</td>
                                <td className={`text-right ${s.long_pnl >= 0 ? 'text-success' : 'text-error'}`}>{fmtMoney(s.long_pnl)}</td>
                                <td className={`text-right ${s.short_pnl >= 0 ? 'text-success' : 'text-error'}`}>{fmtMoney(s.short_pnl)}</td>
                                <td className={`text-right font-bold ${s.net_pnl >= 0 ? 'text-success' : 'text-error'}`}>{fmtMoney(s.net_pnl)}</td>
                                <td className={`text-right ${s.return_pct >= 0 ? 'text-success' : 'text-error'}`}>{s.return_pct > 0 ? '+' : ''}{s.return_pct}%</td>
                                <td className="text-right">{fmtMoney(s.portfolio_value)}</td>
                              </tr>
                            ))}
                          </tbody>
                        </table>
                      </div>
                    )}

                    {/* 3c: Stress Tests */}
                    {portScenarioTab === 'stress' && (
                      <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
                        {sa.stress_scenarios.map(s => (
                          <div key={s.name} className={`rounded-xl p-4 border ${s.net_pnl >= 0 ? 'border-success/20 bg-success/5' : 'border-error/20 bg-error/5'}`}>
                            <h5 className="font-semibold text-sm mb-1">{s.name}</h5>
                            <p className="text-xs text-base-content/60 mb-2">{s.description}</p>
                            <div className="flex gap-4">
                              <div>
                                <p className="text-[10px] text-base-content/40">P&L</p>
                                <p className={`text-lg font-bold ${s.net_pnl >= 0 ? 'text-success' : 'text-error'}`}>{fmtMoney(s.net_pnl)}</p>
                              </div>
                              <div>
                                <p className="text-[10px] text-base-content/40">Return</p>
                                <p className={`text-lg font-bold ${s.return_pct >= 0 ? 'text-success' : 'text-error'}`}>{s.return_pct > 0 ? '+' : ''}{s.return_pct}%</p>
                              </div>
                            </div>
                          </div>
                        ))}
                      </div>
                    )}

                    {/* 3d: Leverage Comparison */}
                    {portScenarioTab === 'leverage' && (
                      <div className="grid grid-cols-1 sm:grid-cols-3 gap-3">
                        {sa.leverage_comparison.map(l => (
                          <div key={l.ratio} className={`rounded-xl p-4 border text-center ${l.ratio === portLeverage ? 'border-primary/40 bg-primary/5' : 'border-white/[0.03] bg-base-200/30'}`}>
                            <p className="text-xs text-base-content/50 uppercase tracking-wider mb-1">{l.ratio}</p>
                            {l.ratio === portLeverage && <span className="badge badge-primary badge-xs mb-2">Selected</span>}
                            <div className="space-y-2 mt-2">
                              <div>
                                <p className="text-[9px] text-base-content/40">Bull (+20%)</p>
                                <p className="text-success font-bold">{fmtMoney(l.best_case)} <span className="text-xs font-normal">({l.best_case_pct}%)</span></p>
                              </div>
                              <div>
                                <p className="text-[9px] text-base-content/40">Bear (-20%)</p>
                                <p className="text-error font-bold">{fmtMoney(l.worst_case)} <span className="text-xs font-normal">({l.worst_case_pct}%)</span></p>
                              </div>
                              <div>
                                <p className="text-[9px] text-base-content/40">Expected (+8%)</p>
                                <p className="text-info font-bold">{fmtMoney(l.expected)} <span className="text-xs font-normal">({l.expected_pct}%)</span></p>
                              </div>
                            </div>
                          </div>
                        ))}
                      </div>
                    )}

                    {/* 3e: Position Impact */}
                    {portScenarioTab === 'impact' && (
                      <div className="space-y-3">
                        <p className="text-xs text-base-content/60">What happens if each position moves ±20%</p>
                        <div style={{ height: 250 }}>
                          <Bar
                            data={{
                              labels: sa.position_impact.map(p => p.ticker),
                              datasets: [
                                {
                                  label: 'If Down 20%',
                                  data: sa.position_impact.map(p => p.if_down_20_pnl),
                                  backgroundColor: sa.position_impact.map(p => p.if_down_20_pnl >= 0 ? 'rgba(54,211,153,0.6)' : 'rgba(248,114,114,0.6)'),
                                },
                                {
                                  label: 'If Up 20%',
                                  data: sa.position_impact.map(p => p.if_up_20_pnl),
                                  backgroundColor: sa.position_impact.map(p => p.if_up_20_pnl >= 0 ? 'rgba(54,211,153,0.3)' : 'rgba(248,114,114,0.3)'),
                                },
                              ],
                            }}
                            options={{
                              responsive: true, maintainAspectRatio: false,
                              plugins: { legend: { labels: { font: { size: 10 } } } },
                              scales: {
                                x: { ticks: { font: { size: 9 } }, grid: { color: 'rgba(255,255,255,0.05)' } },
                                y: { ticks: { font: { size: 9 }, callback: v => `$${Number(v).toLocaleString()}` }, grid: { color: 'rgba(255,255,255,0.05)' } },
                              },
                            }}
                          />
                        </div>
                        <div className="overflow-x-auto">
                          <table className="table table-xs w-full">
                            <thead>
                              <tr className="text-[10px] text-base-content/50">
                                <th>Ticker</th><th>Side</th><th className="text-right">Weight</th><th className="text-right">Value</th>
                                <th className="text-right">If -20%</th><th className="text-right">If +20%</th><th className="text-right">Impact %</th>
                              </tr>
                            </thead>
                            <tbody>
                              {sa.position_impact.map(p => (
                                <tr key={p.ticker} className="text-xs">
                                  <td className="font-bold">{p.ticker}</td>
                                  <td><span className={`badge badge-xs ${p.side === 'long' ? 'badge-success' : 'badge-error'}`}>{p.side}</span></td>
                                  <td className="text-right">{p.weight.toFixed(1)}%</td>
                                  <td className="text-right">{fmtMoney(p.dollar_value)}</td>
                                  <td className={`text-right ${p.if_down_20_pnl >= 0 ? 'text-success' : 'text-error'}`}>{fmtMoney(p.if_down_20_pnl)}</td>
                                  <td className={`text-right ${p.if_up_20_pnl >= 0 ? 'text-success' : 'text-error'}`}>{fmtMoney(p.if_up_20_pnl)}</td>
                                  <td className="text-right">{p.portfolio_impact_pct.toFixed(1)}%</td>
                                </tr>
                              ))}
                            </tbody>
                          </table>
                        </div>
                      </div>
                    )}
                  </div>
                </div>

                {/* Section 4: Factor Exposure */}
                <div className="bg-base-200/40 rounded-xl border border-white/[0.03] p-3">
                  <h4 className="text-sm font-semibold mb-3 flex items-center gap-2">
                    <Target className="w-4 h-4 text-info" /> Factor Exposure — Long vs Short
                  </h4>
                  <div style={{ height: 220 }}>
                    <Bar
                      data={{
                        labels: ['Value', 'Momentum', 'Quality', 'Defensive', 'Composite'],
                        datasets: [
                          {
                            label: 'Long Book',
                            data: [fe.long_value, fe.long_momentum, fe.long_quality, fe.long_defensive, fe.long_composite],
                            backgroundColor: 'rgba(54,211,153,0.5)',
                            borderColor: '#36d399',
                            borderWidth: 1,
                          },
                          {
                            label: 'Short Book',
                            data: [fe.short_value, fe.short_momentum, fe.short_quality, fe.short_defensive, fe.short_composite],
                            backgroundColor: 'rgba(248,114,114,0.5)',
                            borderColor: '#f87272',
                            borderWidth: 1,
                          },
                        ],
                      }}
                      options={{
                        responsive: true, maintainAspectRatio: false,
                        plugins: { legend: { labels: { font: { size: 10 } } } },
                        scales: {
                          x: { ticks: { font: { size: 10 } }, grid: { color: 'rgba(255,255,255,0.05)' } },
                          y: { min: 0, max: 100, ticks: { font: { size: 9 } }, grid: { color: 'rgba(255,255,255,0.05)' } },
                        },
                      }}
                    />
                  </div>
                  <p className="text-[9px] text-base-content/40 mt-2 text-center">
                    Ideal: Long book scores higher across all factors. Short book scoring lower = good short candidates.
                  </p>
                </div>

                {/* Section 5: Sector Breakdown */}
                {rm.sector_breakdown.length > 0 && (
                  <div className="bg-base-200/40 rounded-xl border border-white/[0.03] p-3">
                    <h4 className="text-sm font-semibold mb-3 flex items-center gap-2">
                      <PieChart className="w-4 h-4 text-info" /> Sector Exposure
                    </h4>
                    <div style={{ height: Math.max(150, rm.sector_breakdown.length * 28) }}>
                      <Bar
                        data={{
                          labels: rm.sector_breakdown.map(s => s.sector),
                          datasets: [
                            {
                              label: 'Long %',
                              data: rm.sector_breakdown.map(s => s.long_weight),
                              backgroundColor: 'rgba(54,211,153,0.5)',
                            },
                            {
                              label: 'Short %',
                              data: rm.sector_breakdown.map(s => -s.short_weight),
                              backgroundColor: 'rgba(248,114,114,0.5)',
                            },
                          ],
                        }}
                        options={{
                          indexAxis: 'y',
                          responsive: true, maintainAspectRatio: false,
                          plugins: { legend: { labels: { font: { size: 9 } } } },
                          scales: {
                            x: { ticks: { font: { size: 9 }, callback: v => `${Math.abs(Number(v))}%` }, grid: { color: 'rgba(255,255,255,0.05)' } },
                            y: { ticks: { font: { size: 9 } }, grid: { display: false } },
                          },
                        }}
                      />
                    </div>
                  </div>
                )}

                {/* Section 6: Tax-Loss Harvesting Projection */}
                <div className="bg-base-200/40 rounded-xl border border-white/[0.03] p-3">
                  <h4 className="text-sm font-semibold mb-3 flex items-center gap-2">
                    <DollarSign className="w-4 h-4 text-success" /> Tax-Loss Harvesting Projection
                  </h4>
                  <div className="grid grid-cols-2 sm:grid-cols-4 gap-3 mb-3">
                    <div className="bg-success/10 border border-success/20 rounded-lg p-2 text-center">
                      <p className="text-[10px] text-success">Annual Tax Alpha</p>
                      <p className="text-lg font-bold text-success">{tp.avg_annual_tax_alpha_pct}%</p>
                    </div>
                    <div className="bg-success/10 border border-success/20 rounded-lg p-2 text-center">
                      <p className="text-[10px] text-success">10-Year Savings</p>
                      <p className="text-lg font-bold text-success">{fmtMoney(tp.total_10yr_savings)}</p>
                    </div>
                    <div className="bg-info/10 border border-info/20 rounded-lg p-2 text-center">
                      <p className="text-[10px] text-info">Loss Multiplier</p>
                      <p className="text-lg font-bold text-info">{tp.loss_multiplier_vs_long_only}x</p>
                      <p className="text-[9px] text-base-content/40">vs Long-Only</p>
                    </div>
                    <div className="bg-base-200/60 rounded-lg p-2 text-center">
                      <p className="text-[10px] text-base-content/50">Long-Only 10yr</p>
                      <p className="text-lg font-bold">{fmtMoney(tp.yearly[tp.yearly.length - 1]?.cumulative_long_only ?? 0)}</p>
                    </div>
                  </div>
                  <div style={{ height: 220 }}>
                    <Line
                      data={{
                        labels: tp.yearly.map(y => `Year ${y.year}`),
                        datasets: [
                          {
                            label: `${portLeverage} Cumulative Savings`,
                            data: tp.yearly.map(y => y.cumulative_ls),
                            borderColor: '#36d399',
                            backgroundColor: 'rgba(54,211,153,0.1)',
                            borderWidth: 2,
                            pointRadius: 3,
                            fill: true,
                            tension: 0.3,
                          },
                          {
                            label: 'Long-Only Cumulative Savings',
                            data: tp.yearly.map(y => y.cumulative_long_only),
                            borderColor: 'rgba(255,255,255,0.3)',
                            borderDash: [4, 4],
                            borderWidth: 1.5,
                            pointRadius: 2,
                            tension: 0.3,
                          },
                        ],
                      }}
                      options={{
                        responsive: true, maintainAspectRatio: false,
                        plugins: {
                          legend: { labels: { font: { size: 10 }, usePointStyle: true, pointStyleWidth: 10 } },
                          tooltip: { callbacks: { label: ctx => `${ctx.dataset.label}: $${(ctx.parsed.y ?? 0).toLocaleString()}` } },
                        },
                        scales: {
                          x: { ticks: { font: { size: 9 } }, grid: { color: 'rgba(255,255,255,0.05)' } },
                          y: { ticks: { font: { size: 9 }, callback: v => `$${(Number(v) / 1000).toFixed(0)}k` }, grid: { color: 'rgba(255,255,255,0.05)' } },
                        },
                      }}
                    />
                  </div>
                  <p className="text-[9px] text-base-content/40 mt-2 text-center">
                    Based on AQR research: 130/30 generates ~2.7× more capital losses than long-only. Actual results will vary.
                  </p>
                </div>

                {/* Section 7: AI Insights */}
                {r.llm_insights && (
                  <div className="bg-base-200/40 rounded-xl border border-white/[0.03] overflow-hidden">
                    <div className="flex items-center gap-2 p-3 border-b border-white/[0.03]">
                      <Brain className="w-4 h-4 text-secondary" />
                      <span className="text-sm font-semibold">AI Portfolio Analysis</span>
                    </div>
                    <div className="p-4 prose prose-sm prose-invert max-w-none text-xs leading-relaxed"
                      dangerouslySetInnerHTML={{ __html: r.llm_insights.replace(/\n/g, '<br/>').replace(/\*\*(.*?)\*\*/g, '<strong>$1</strong>').replace(/\*(.*?)\*/g, '<em>$1</em>') }}
                    />
                  </div>
                )}
              </div>
            );
          })()}
        </div>
      )}

      {/* Single Stock Mode */}
      {mode === 'single' && (
        <div className="space-y-4">
          <form onSubmit={handleSingleSubmit} className="flex flex-wrap items-end gap-3">
            <div className="form-control">
              <label className="label py-1"><span className="label-text text-xs font-medium">Long Ticker</span></label>
              <input
                type="text"
                className="input input-bordered input-sm w-28"
                value={singleTicker}
                onChange={(e) => setSingleTicker(e.target.value.toUpperCase())}
                placeholder="AAPL"
                required
              />
            </div>
            <div className="form-control">
              <label className="label py-1"><span className="label-text text-xs font-medium">Investment ($)</span></label>
              <div className="relative">
                <DollarSign className="absolute left-2 top-1/2 -translate-y-1/2 w-3.5 h-3.5 text-base-content/40" />
                <input
                  type="number"
                  className="input input-bordered input-sm w-36 pl-7"
                  value={singleAmount}
                  onChange={(e) => setSingleAmount(Number(e.target.value))}
                  min={1000}
                  step={1000}
                  required
                />
              </div>
            </div>
            <button type="submit" className="btn btn-primary btn-sm gap-2" disabled={singleLoading || !singleTicker}>
              {singleLoading ? <Loader2 className="w-4 h-4 animate-spin" /> : <BarChart3 className="w-4 h-4" />}
              {singleLoading ? 'Analyzing...' : 'Analyze Strategy'}
            </button>
          </form>

          {singleError && (
            <div className="alert alert-error text-sm">
              <AlertTriangle className="w-4 h-4" /><span>{singleError}</span>
            </div>
          )}

          {singleResult && singleResult.strategies.length > 0 && (
            <div className="space-y-4">
              {/* Stock Info */}
              <div className="bg-base-200/40 rounded-xl p-3 flex flex-wrap items-center gap-4 text-sm border border-white/[0.03]">
                <div>
                  <span className="font-bold text-base">{singleResult.stock_info.ticker}</span>
                  <span className="text-base-content/60 ml-2">{singleResult.stock_info.name}</span>
                </div>
                <span className="text-base-content/60">
                  ${singleResult.stock_info.price} • {singleResult.stock_info.sector}
                </span>
                {singleResult.stock_metrics.annualized_volatility && (
                  <span className="badge badge-outline badge-xs">
                    Vol: {singleResult.stock_metrics.annualized_volatility}%
                  </span>
                )}
                {singleResult.stock_metrics.max_drawdown_1y && (
                  <span className="badge badge-outline badge-xs">
                    Max DD: {singleResult.stock_metrics.max_drawdown_1y}%
                  </span>
                )}
              </div>

              {/* Strategy Selector */}
              {singleResult.strategies.length > 1 && (
                <div className="flex gap-1 flex-wrap">
                  {singleResult.strategies.map((s, i) => (
                    <button
                      key={i}
                      className={`btn btn-xs ${selectedStrategy === i ? 'btn-primary' : 'btn-ghost'}`}
                      onClick={() => setSelectedStrategy(i)}
                    >
                      Short {s.hedge_ticker} (Corr: {s.correlation?.toFixed(2) ?? '?'})
                    </button>
                  ))}
                </div>
              )}

              {/* Selected Strategy Detail */}
              {(() => {
                const strat = singleResult.strategies[selectedStrategy];
                if (!strat) return null;
                return (
                  <div className="space-y-4">
                    {/* Position Grid */}
                    <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
                      <div className="bg-success/10 border border-success/20 rounded-lg p-3">
                        <p className="text-[10px] text-success uppercase tracking-wider flex items-center gap-1">
                          <TrendingUp className="w-3 h-3" /> Long
                        </p>
                        <p className="font-bold">{singleResult.stock_info.ticker}</p>
                        <p className="text-xs text-base-content/60">
                          {strat.shares_long} shares @ ${singleResult.stock_info.price}
                        </p>
                        <p className="text-sm font-medium">{fmtMoney(strat.long_value)}</p>
                      </div>
                      <div className="bg-error/10 border border-error/20 rounded-lg p-3">
                        <p className="text-[10px] text-error uppercase tracking-wider flex items-center gap-1">
                          <TrendingDown className="w-3 h-3" /> Short
                        </p>
                        <p className="font-bold">{strat.hedge_ticker}</p>
                        <p className="text-xs text-base-content/60">
                          {strat.shares_short} shares @ ${strat.hedge_price}
                        </p>
                        <p className="text-sm font-medium">{fmtMoney(strat.short_value)}</p>
                      </div>
                      <div className="bg-base-200/40 rounded-xl p-3 border border-white/[0.03]">
                        <p className="text-[10px] text-base-content/50 uppercase tracking-wider">Correlation</p>
                        <p className="text-lg font-bold">{strat.correlation?.toFixed(3) ?? '—'}</p>
                        <p className="text-xs text-base-content/60">Hedge Ratio: {strat.hedge_ratio?.toFixed(3)}</p>
                      </div>
                      <div className="bg-base-200/40 rounded-xl p-3 border border-white/[0.03]">
                        <p className="text-[10px] text-base-content/50 uppercase tracking-wider">Exposure</p>
                        <p className="text-sm">Net: {fmtMoney(strat.net_exposure)}</p>
                        <p className="text-xs text-base-content/60">Gross: {fmtMoney(strat.gross_exposure)}</p>
                      </div>
                    </div>

                    {/* Correlation Chart */}
                    {strat.price_history_1 && strat.price_history_2 && (
                      <div>
                        <h4 className="text-sm font-semibold mb-2">Normalized Price Performance (Base 100)</h4>
                        <div className="bg-base-200/40 rounded-xl p-3 border border-white/[0.03]" style={{ height: 250 }}>
                          <Line
                            data={{
                              labels: strat.price_history_1.timestamps,
                              datasets: [
                                {
                                  label: singleResult.stock_info.ticker,
                                  data: strat.price_history_1.normalized,
                                  borderColor: '#36d399',
                                  borderWidth: 1.5,
                                  pointRadius: 0,
                                  tension: 0.3,
                                },
                                {
                                  label: strat.hedge_ticker,
                                  data: strat.price_history_2.normalized.slice(0, strat.price_history_1.timestamps.length),
                                  borderColor: '#f87272',
                                  borderWidth: 1.5,
                                  pointRadius: 0,
                                  tension: 0.3,
                                },
                              ],
                            }}
                            options={{
                              responsive: true,
                              maintainAspectRatio: false,
                              plugins: {
                                legend: { labels: { font: { size: 10 }, usePointStyle: true, pointStyleWidth: 10 } },
                              },
                              scales: {
                                x: { ticks: { font: { size: 9 }, maxTicksLimit: 8 }, grid: { color: 'rgba(255,255,255,0.05)' } },
                                y: { ticks: { font: { size: 9 } }, grid: { color: 'rgba(255,255,255,0.05)' } },
                              },
                            }}
                          />
                        </div>
                      </div>
                    )}

                    {/* Spread Chart */}
                    {strat.spread && (
                      <div>
                        <h4 className="text-sm font-semibold mb-1">Price Spread (Normalized Difference)</h4>
                        <div className="flex flex-wrap gap-3 text-xs text-base-content/60 mb-2">
                          <span>Mean: {strat.spread.mean}</span>
                          <span>Std: {strat.spread.std}</span>
                          <span>Current: {strat.spread.current}</span>
                          <span className={`font-medium ${Math.abs(strat.spread.z_score) > 2 ? 'text-error' : Math.abs(strat.spread.z_score) > 1 ? 'text-warning' : 'text-success'}`}>
                            Z-Score: {strat.spread.z_score}
                          </span>
                        </div>
                        <div className="bg-base-200/40 rounded-xl p-3 border border-white/[0.03]" style={{ height: 180 }}>
                          <Line
                            data={{
                              labels: strat.spread.timestamps,
                              datasets: [
                                {
                                  label: 'Spread',
                                  data: strat.spread.values,
                                  borderColor: '#6366f1',
                                  borderWidth: 1.5,
                                  pointRadius: 0,
                                  tension: 0.3,
                                  fill: true,
                                  backgroundColor: 'rgba(99,102,241,0.1)',
                                },
                                {
                                  label: 'Mean',
                                  data: strat.spread.timestamps.map(() => strat.spread!.mean),
                                  borderColor: 'rgba(255,255,255,0.3)',
                                  borderDash: [4, 4],
                                  borderWidth: 1,
                                  pointRadius: 0,
                                },
                                {
                                  label: '+1 Std',
                                  data: strat.spread.timestamps.map(() => strat.spread!.mean + strat.spread!.std),
                                  borderColor: 'rgba(251,189,35,0.3)',
                                  borderDash: [2, 2],
                                  borderWidth: 1,
                                  pointRadius: 0,
                                },
                                {
                                  label: '-1 Std',
                                  data: strat.spread.timestamps.map(() => strat.spread!.mean - strat.spread!.std),
                                  borderColor: 'rgba(251,189,35,0.3)',
                                  borderDash: [2, 2],
                                  borderWidth: 1,
                                  pointRadius: 0,
                                },
                              ],
                            }}
                            options={{
                              responsive: true,
                              maintainAspectRatio: false,
                              plugins: {
                                legend: { labels: { font: { size: 9 }, usePointStyle: true, pointStyleWidth: 8 } },
                              },
                              scales: {
                                x: { ticks: { font: { size: 9 }, maxTicksLimit: 8 }, grid: { color: 'rgba(255,255,255,0.05)' } },
                                y: { ticks: { font: { size: 9 } }, grid: { color: 'rgba(255,255,255,0.05)' } },
                              },
                            }}
                          />
                        </div>
                      </div>
                    )}

                    {/* Quant Analytics */}
                    {singleResult.quant_analytics && (
                      <QuantAnalyticsPanel qa={singleResult.quant_analytics} ticker={singleResult.ticker} />
                    )}
                  </div>
                );
              })()}
            </div>
          )}

          {singleResult && singleResult.strategies.length === 0 && (
            <div className="alert alert-warning text-sm">
              <AlertTriangle className="w-4 h-4" />
              <span>No sector ETFs found for this stock's sector. Try a different stock or use Pair Trade mode.</span>
            </div>
          )}
        </div>
      )}

      {/* Pair Trade Mode */}
      {mode === 'pair' && (
        <div className="space-y-4">
          <form onSubmit={handlePairSubmit} className="flex flex-wrap items-end gap-3">
            <div className="form-control">
              <label className="label py-1"><span className="label-text text-xs font-medium">Long Ticker</span></label>
              <input
                type="text"
                className="input input-bordered input-sm w-28"
                value={longTicker}
                onChange={(e) => setLongTicker(e.target.value.toUpperCase())}
                placeholder="AAPL"
                required
              />
            </div>
            <div className="form-control">
              <label className="label py-1"><span className="label-text text-xs font-medium">Short Ticker</span></label>
              <div className="flex gap-1.5">
                <input
                  type="text"
                  className="input input-bordered input-sm w-28"
                  value={shortTicker}
                  onChange={(e) => setShortTicker(e.target.value.toUpperCase())}
                  placeholder="MSFT"
                />
                <button
                  type="button"
                  className="btn btn-outline btn-secondary btn-sm gap-1 text-xs"
                  onClick={handleSuggestPairs}
                  disabled={suggestLoading || !longTicker.trim()}
                  title="Get system-recommended pair candidates"
                >
                  {suggestLoading ? <Loader2 className="w-3.5 h-3.5 animate-spin" /> : <Sparkles className="w-3.5 h-3.5" />}
                  Suggest
                </button>
              </div>
            </div>
            <div className="form-control">
              <label className="label py-1"><span className="label-text text-xs font-medium">Investment ($)</span></label>
              <div className="relative">
                <DollarSign className="absolute left-2 top-1/2 -translate-y-1/2 w-3.5 h-3.5 text-base-content/40" />
                <input
                  type="number"
                  className="input input-bordered input-sm w-36 pl-7"
                  value={pairAmount}
                  onChange={(e) => setPairAmount(Number(e.target.value))}
                  min={1000}
                  step={1000}
                  required
                />
              </div>
            </div>
            <button type="submit" className="btn btn-primary btn-sm gap-2" disabled={pairLoading || !longTicker || !shortTicker}>
              {pairLoading ? <Loader2 className="w-4 h-4 animate-spin" /> : <ArrowLeftRight className="w-4 h-4" />}
              {pairLoading ? 'Computing...' : 'Build Pair Trade'}
            </button>
          </form>

          {/* Pair Suggestions Panel */}
          {showSuggestions && (
            <div className="bg-base-200/40 border border-secondary/20 rounded-xl p-4">
              <div className="flex items-center justify-between mb-3">
                <h4 className="text-sm font-semibold flex items-center gap-2">
                  <Sparkles className="w-4 h-4 text-secondary" />
                  Suggested Pairs for {suggestTicker}
                </h4>
                <button
                  className="btn btn-ghost btn-xs"
                  onClick={() => setShowSuggestions(false)}
                >
                  ✕
                </button>
              </div>

              {suggestLoading && (
                <div className="flex items-center gap-2 text-sm text-base-content/60 py-4 justify-center">
                  <Loader2 className="w-4 h-4 animate-spin" />
                  Scanning peers and computing correlations…
                </div>
              )}

              {suggestError && (
                <div className="alert alert-warning text-sm py-2">
                  <AlertTriangle className="w-4 h-4" /><span>{suggestError}</span>
                </div>
              )}

              {!suggestLoading && suggestions.length > 0 && (
                <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-2">
                  {suggestions.map((c) => (
                    <button
                      key={c.ticker}
                      className={`text-left bg-base-200 hover:bg-primary/10 border rounded-lg p-3 transition-colors ${
                        shortTicker === c.ticker ? 'border-primary bg-primary/10' : 'border-base-content/10'
                      }`}
                      onClick={() => handleSelectCandidate(c)}
                    >
                      <div className="flex items-center justify-between mb-1">
                        <span className="font-bold text-sm">{c.ticker}</span>
                        <span className={`badge badge-xs ${
                          Math.abs(c.correlation) > 0.7 ? 'badge-success' :
                          Math.abs(c.correlation) > 0.4 ? 'badge-warning' : 'badge-error'
                        }`}>
                          ρ = {c.correlation.toFixed(2)}
                        </span>
                      </div>
                      <p className="text-xs text-base-content/60 truncate">{c.name}</p>
                      <div className="flex gap-2 mt-1 text-[10px] text-base-content/50">
                        {c.price && <span>${c.price.toFixed(2)}</span>}
                        {c.sector && <span>• {c.sector}</span>}
                      </div>
                      <div className="text-[10px] text-base-content/40 mt-0.5">
                        Hedge ratio: {c.hedge_ratio.toFixed(3)}
                      </div>
                    </button>
                  ))}
                </div>
              )}

              {!suggestLoading && suggestions.length > 0 && (
                <p className="text-[10px] text-base-content/40 mt-2">
                  Click a candidate to select it as your short ticker. Higher correlation = better pair candidate.
                </p>
              )}
            </div>
          )}

          {pairError && (
            <div className="alert alert-error text-sm">
              <AlertTriangle className="w-4 h-4" /><span>{pairError}</span>
            </div>
          )}

          {pairResult && (
            <div className="space-y-4">
              {/* Stock Comparison */}
              <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
                {/* Long Side */}
                <div className="bg-success/10 border border-success/20 rounded-lg p-4">
                  <div className="flex items-center gap-2 mb-3">
                    <TrendingUp className="w-4 h-4 text-success" />
                    <span className="text-xs text-success font-semibold uppercase">Long</span>
                  </div>
                  <p className="font-bold text-lg">{pairResult.long_info.ticker}</p>
                  <p className="text-sm text-base-content/60 mb-2">{pairResult.long_info.name}</p>
                  <div className="grid grid-cols-2 gap-2 text-xs">
                    <div><span className="text-base-content/50">Price:</span> <span className="font-medium">${pairResult.long_info.price}</span></div>
                    <div><span className="text-base-content/50">Beta:</span> <span className="font-medium">{pairResult.long_info.beta ?? '—'}</span></div>
                    <div><span className="text-base-content/50">P/E:</span> <span className="font-medium">{pairResult.long_info.pe_ratio?.toFixed(1) ?? '—'}</span></div>
                    <div><span className="text-base-content/50">Sector:</span> <span className="font-medium">{pairResult.long_info.sector}</span></div>
                    <div><span className="text-base-content/50">Shares:</span> <span className="font-medium">{pairResult.shares_long}</span></div>
                    <div><span className="text-base-content/50">Value:</span> <span className="font-medium">{fmtMoney(pairResult.long_value)}</span></div>
                    {pairResult.long_metrics.annualized_volatility && (
                      <div><span className="text-base-content/50">Volatility:</span> <span className="font-medium">{fmtPct(pairResult.long_metrics.annualized_volatility)}</span></div>
                    )}
                  </div>
                </div>

                {/* Short Side */}
                <div className="bg-error/10 border border-error/20 rounded-lg p-4">
                  <div className="flex items-center gap-2 mb-3">
                    <TrendingDown className="w-4 h-4 text-error" />
                    <span className="text-xs text-error font-semibold uppercase">Short</span>
                  </div>
                  <p className="font-bold text-lg">{pairResult.short_info.ticker}</p>
                  <p className="text-sm text-base-content/60 mb-2">{pairResult.short_info.name}</p>
                  <div className="grid grid-cols-2 gap-2 text-xs">
                    <div><span className="text-base-content/50">Price:</span> <span className="font-medium">${pairResult.short_info.price}</span></div>
                    <div><span className="text-base-content/50">Beta:</span> <span className="font-medium">{pairResult.short_info.beta ?? '—'}</span></div>
                    <div><span className="text-base-content/50">P/E:</span> <span className="font-medium">{pairResult.short_info.pe_ratio?.toFixed(1) ?? '—'}</span></div>
                    <div><span className="text-base-content/50">Sector:</span> <span className="font-medium">{pairResult.short_info.sector}</span></div>
                    <div><span className="text-base-content/50">Shares:</span> <span className="font-medium">{pairResult.shares_short}</span></div>
                    <div><span className="text-base-content/50">Value:</span> <span className="font-medium">{fmtMoney(pairResult.short_value)}</span></div>
                    {pairResult.short_metrics.annualized_volatility && (
                      <div><span className="text-base-content/50">Volatility:</span> <span className="font-medium">{fmtPct(pairResult.short_metrics.annualized_volatility)}</span></div>
                    )}
                  </div>
                </div>
              </div>

              {/* Summary Stats */}
              <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
                <div className="bg-base-200/40 rounded-xl p-3 text-center border border-white/[0.03]">
                  <p className="text-[10px] text-base-content/50 uppercase tracking-wider">Correlation</p>
                  <p className={`text-lg font-bold ${(pairResult.correlation ?? 0) > 0.7 ? 'text-success' : (pairResult.correlation ?? 0) > 0.4 ? 'text-warning' : 'text-error'}`}>
                    {pairResult.correlation?.toFixed(3) ?? '—'}
                  </p>
                </div>
                <div className="bg-base-200/40 rounded-xl p-3 text-center border border-white/[0.03]">
                  <p className="text-[10px] text-base-content/50 uppercase tracking-wider">Hedge Ratio</p>
                  <p className="text-lg font-bold">{pairResult.hedge_ratio.toFixed(3)}</p>
                </div>
                <div className="bg-base-200/40 rounded-xl p-3 text-center border border-white/[0.03]">
                  <p className="text-[10px] text-base-content/50 uppercase tracking-wider">Net Exposure</p>
                  <p className="text-lg font-bold">{fmtMoney(pairResult.net_exposure)}</p>
                </div>
                <div className="bg-base-200/40 rounded-xl p-3 text-center border border-white/[0.03]">
                  <p className="text-[10px] text-base-content/50 uppercase tracking-wider">Gross Exposure</p>
                  <p className="text-lg font-bold">{fmtMoney(pairResult.gross_exposure)}</p>
                </div>
              </div>

              {/* Price Performance Chart */}
              {pairResult.price_history && pairResult.price_history_2 && (
                <div>
                  <h4 className="text-sm font-semibold mb-2">Normalized Price Performance (Base 100)</h4>
                  <div className="bg-base-300 rounded-lg p-3" style={{ height: 250 }}>
                    <Line
                      data={{
                        labels: pairResult.price_history.timestamps,
                        datasets: [
                          {
                            label: pairResult.long_ticker,
                            data: pairResult.price_history.normalized,
                            borderColor: '#36d399',
                            borderWidth: 1.5,
                            pointRadius: 0,
                            tension: 0.3,
                          },
                          {
                            label: pairResult.short_ticker,
                            data: pairResult.price_history_2.normalized.slice(0, pairResult.price_history.timestamps.length),
                            borderColor: '#f87272',
                            borderWidth: 1.5,
                            pointRadius: 0,
                            tension: 0.3,
                          },
                        ],
                      }}
                      options={{
                        responsive: true,
                        maintainAspectRatio: false,
                        plugins: {
                          legend: { labels: { font: { size: 10 }, usePointStyle: true, pointStyleWidth: 10 } },
                        },
                        scales: {
                          x: { ticks: { font: { size: 9 }, maxTicksLimit: 8 }, grid: { color: 'rgba(255,255,255,0.05)' } },
                          y: { ticks: { font: { size: 9 } }, grid: { color: 'rgba(255,255,255,0.05)' } },
                        },
                      }}
                    />
                  </div>
                </div>
              )}

              {/* Spread Chart */}
              {pairResult.spread && (
                <div>
                  <h4 className="text-sm font-semibold mb-1">Price Spread</h4>
                  <div className="flex flex-wrap gap-3 text-xs text-base-content/60 mb-2">
                    <span>Mean: {pairResult.spread.mean}</span>
                    <span>Std: {pairResult.spread.std}</span>
                    <span>Current: {pairResult.spread.current}</span>
                    <span className={`font-medium ${Math.abs(pairResult.spread.z_score) > 2 ? 'text-error' : Math.abs(pairResult.spread.z_score) > 1 ? 'text-warning' : 'text-success'}`}>
                      Z-Score: {pairResult.spread.z_score}
                    </span>
                  </div>
                  <div className="bg-base-300 rounded-lg p-3" style={{ height: 180 }}>
                    <Line
                      data={{
                        labels: pairResult.spread.timestamps,
                        datasets: [
                          {
                            label: 'Spread',
                            data: pairResult.spread.values,
                            borderColor: '#6366f1',
                            borderWidth: 1.5,
                            pointRadius: 0,
                            tension: 0.3,
                            fill: true,
                            backgroundColor: 'rgba(99,102,241,0.1)',
                          },
                          {
                            label: 'Mean',
                            data: pairResult.spread.timestamps.map(() => pairResult.spread!.mean),
                            borderColor: 'rgba(255,255,255,0.3)',
                            borderDash: [4, 4],
                            borderWidth: 1,
                            pointRadius: 0,
                          },
                          {
                            label: '+1 Std',
                            data: pairResult.spread.timestamps.map(() => pairResult.spread!.mean + pairResult.spread!.std),
                            borderColor: 'rgba(251,189,35,0.3)',
                            borderDash: [2, 2],
                            borderWidth: 1,
                            pointRadius: 0,
                          },
                          {
                            label: '-1 Std',
                            data: pairResult.spread.timestamps.map(() => pairResult.spread!.mean - pairResult.spread!.std),
                            borderColor: 'rgba(251,189,35,0.3)',
                            borderDash: [2, 2],
                            borderWidth: 1,
                            pointRadius: 0,
                          },
                        ],
                      }}
                      options={{
                        responsive: true,
                        maintainAspectRatio: false,
                        plugins: {
                          legend: { labels: { font: { size: 9 }, usePointStyle: true, pointStyleWidth: 8 } },
                        },
                        scales: {
                          x: { ticks: { font: { size: 9 }, maxTicksLimit: 8 }, grid: { color: 'rgba(255,255,255,0.05)' } },
                          y: { ticks: { font: { size: 9 } }, grid: { color: 'rgba(255,255,255,0.05)' } },
                        },
                      }}
                    />
                  </div>
                </div>
              )}

              {/* Pair Trade Interpretation */}
              {pairResult.spread && (
                <div className={`rounded-lg p-4 border text-sm ${
                  Math.abs(pairResult.spread.z_score) > 2
                    ? 'border-error/30 bg-error/5'
                    : Math.abs(pairResult.spread.z_score) > 1
                    ? 'border-warning/30 bg-warning/5'
                    : 'border-success/30 bg-success/5'
                }`}>
                  <h4 className="font-semibold mb-1 flex items-center gap-2">
                    <BarChart3 className="w-4 h-4" />
                    Spread Interpretation
                  </h4>
                  <p className="text-base-content/70">
                    {pairResult.spread.z_score > 2
                      ? `The spread is extended ${pairResult.spread.z_score.toFixed(1)} standard deviations above the mean. ${pairResult.long_ticker} has significantly outperformed ${pairResult.short_ticker}. Consider this may be mean-reverting — potential entry for the reverse trade.`
                      : pairResult.spread.z_score < -2
                      ? `The spread is ${Math.abs(pairResult.spread.z_score).toFixed(1)} standard deviations below the mean. ${pairResult.short_ticker} has significantly outperformed ${pairResult.long_ticker}. This may present a convergence opportunity for the long/short pair.`
                      : pairResult.spread.z_score > 1
                      ? `The spread is moderately above the mean (${pairResult.spread.z_score.toFixed(1)} std). ${pairResult.long_ticker} has been outperforming. The pair may be approaching an entry point if you expect convergence.`
                      : pairResult.spread.z_score < -1
                      ? `The spread is moderately below the mean (${pairResult.spread.z_score.toFixed(1)} std). ${pairResult.short_ticker} has been outperforming. Current entry may be favorable if you believe ${pairResult.long_ticker} will catch up.`
                      : `The spread is near its historical mean (Z-Score: ${pairResult.spread.z_score.toFixed(1)}). The pair is relatively balanced. Entry at current levels is neutral — watch for deviation from the mean for timing.`
                    }
                  </p>
                  {pairResult.correlation != null && (
                    <p className="text-xs text-base-content/50 mt-2">
                      Correlation: {pairResult.correlation.toFixed(3)}
                      {pairResult.correlation > 0.7 ? ' (Strong — good pair candidate)' :
                       pairResult.correlation > 0.4 ? ' (Moderate — acceptable for pair trading)' :
                       ' (Weak — pair may not hedge effectively)'}
                    </p>
                  )}
                </div>
              )}

              {/* Pair Analytics */}
              {pairResult.pair_analytics && (
                <PairAnalyticsPanel pa={pairResult.pair_analytics} longTicker={pairResult.long_ticker} shortTicker={pairResult.short_ticker} />
              )}

              {/* Quant Analytics — Long Leg */}
              {pairResult.quant_analytics_long && (
                <QuantAnalyticsPanel qa={pairResult.quant_analytics_long} ticker={pairResult.long_ticker} label="Long" />
              )}

              {/* Quant Analytics — Short Leg */}
              {pairResult.quant_analytics_short && (
                <QuantAnalyticsPanel qa={pairResult.quant_analytics_short} ticker={pairResult.short_ticker} label="Short" />
              )}
            </div>
          )}
        </div>
      )}
    </div>
  );
}
