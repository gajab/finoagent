import React, { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { Activity, ArrowDown, ArrowUp, Minus, TrendingUp, TrendingDown, Loader2, Crosshair, Layers } from 'lucide-react';
import { fetchTechnicalForTimeframe, fetchTradeSetups } from '../api';
import {
  Chart as ChartJS,
  CategoryScale,
  LinearScale,
  PointElement,
  LineElement,
  BarElement,
  Filler,
  Title,
  Tooltip,
  Legend,
  type ChartOptions,
  type Plugin,
} from 'chart.js';
import { Line, Bar } from 'react-chartjs-2';
import { TechnicalData } from '../types';
import { SmartMoneyChartOverlay } from './SmartMoneyChartOverlay';
import { InstitutionalTA, MarketStateBanner } from './InstitutionalTA';
import MicrostructurePanel from './MicrostructurePanel';
import MarketStructurePanel from './MarketStructurePanel';
import RegimePanel from './RegimePanel';
import DealerPositioningPanel from './DealerPositioningPanel';
import MarketContextHero from './MarketContextHero';
import TradeSetupCards from './TradeSetupCards';
import { SectionIntro } from './taUi';
import type { TradeSetupsData } from '../types';

ChartJS.register(
  CategoryScale,
  LinearScale,
  PointElement,
  LineElement,
  BarElement,
  Filler,
  Title,
  Tooltip,
  Legend
);

interface TechnicalAnalysisProps {
  technical: TechnicalData;
  ticker: string;
}

interface TimeframePreset { key: string; label: string; short: string; group: string }

const TIMEFRAME_PRESETS: TimeframePreset[] = [
  { key: 'day_1m',      label: '1m',        short: '1d / 1m',           group: 'Day Trading' },
  { key: 'day_5m',      label: '5m',        short: '1d / 5m',           group: 'Day Trading' },
  { key: 'day_15m',     label: '15m',       short: '1d / 15m',          group: 'Day Trading' },
  { key: 'day_5d',      label: '5d/5m',     short: '5d / 5m',           group: 'Day Trading' },
  { key: 'short_term',  label: 'Short',     short: '15-Day / 30-min',   group: 'Swing' },
  { key: 'swing',       label: 'Swing',     short: '3mo / 1h',          group: 'Swing' },
  { key: 'medium_term', label: 'Medium',    short: '6mo / 1d',          group: 'Position' },
  { key: 'long_term',   label: 'Long',      short: '5y / 1wk',          group: 'Position' },
];

const TIMEFRAME_GROUPS: { label: string; keys: string[] }[] = [
  { label: 'Day Trading', keys: ['day_1m', 'day_5m', 'day_15m', 'day_5d'] },
  { label: 'Swing',       keys: ['short_term', 'swing'] },
  { label: 'Position',    keys: ['medium_term', 'long_term'] },
];

const DEFAULT_TIMEFRAME = 'medium_term';

function formatVolume(val: number): string {
  if (val >= 1e9) return `${(val / 1e9).toFixed(1)}B`;
  if (val >= 1e6) return `${(val / 1e6).toFixed(1)}M`;
  if (val >= 1e3) return `${(val / 1e3).toFixed(0)}K`;
  return val.toString();
}

function PhaseIndicator({ phase }: { phase: string }) {
  const normalized = phase.replace(/_/g, ' ');
  let color = 'badge-warning';
  let icon = <Minus size={14} />;
  if (phase === 'accumulation') {
    color = 'badge-success';
    icon = <ArrowUp size={14} />;
  } else if (phase === 'distribution') {
    color = 'badge-error';
    icon = <ArrowDown size={14} />;
  } else if (phase === 'weak_rally') {
    color = 'badge-warning';
    icon = <ArrowUp size={14} />;
  } else if (phase === 'weak_decline') {
    color = 'badge-info';
    icon = <ArrowDown size={14} />;
  }
  return (
    <span className={`badge ${color} badge-sm gap-1 font-semibold capitalize`}>
      {icon} {normalized}
    </span>
  );
}

function RsiGauge({ rsi }: { rsi: number | null }) {
  if (rsi === null) return <span className="text-base-content/50">N/A</span>;
  let color = 'text-warning';
  if (rsi > 70) color = 'text-error';
  else if (rsi > 60) color = 'text-success';
  else if (rsi < 30) color = 'text-success';
  else if (rsi < 40) color = 'text-error';

  const pct = Math.min(100, Math.max(0, rsi));
  return (
    <div className="flex flex-col items-center gap-0.5">
      <span className={`text-base font-bold leading-none ${color}`}>{rsi.toFixed(1)}</span>
      <div className="w-full bg-base-100 rounded-full h-1 mt-0.5">
        <div
          className={`h-1 rounded-full ${rsi > 70 ? 'bg-error' : rsi < 30 ? 'bg-success' : 'bg-warning'}`}
          style={{ width: `${pct}%` }}
        />
      </div>
      <div className="flex justify-between w-full text-[9px] text-base-content/40 leading-none">
        <span>0 <span className="hidden xl:inline">(OS)</span></span>
        <span>50</span>
        <span>100 <span className="hidden xl:inline">(OB)</span></span>
      </div>
    </div>
  );
}

function SignalBadge({ signal, label }: { signal: string; label?: string }) {
  const display = label || signal.replace(/_/g, ' ');
  let badgeClass = 'badge-warning';
  if (signal === 'bullish' || signal === 'bullish_crossover' || signal === 'golden_cross' || signal === 'above') {
    badgeClass = 'badge-success';
  } else if (signal === 'bearish' || signal === 'bearish_crossover' || signal === 'death_cross' || signal === 'below') {
    badgeClass = 'badge-error';
  } else if (signal === 'overbought' || signal === 'strongly_overbought') {
    badgeClass = 'badge-warning';
  } else if (signal === 'oversold') {
    badgeClass = 'badge-info';
  }
  return (
    <span className={`badge ${badgeClass} badge-sm gap-1 font-semibold capitalize`}>
      {(signal.includes('bullish') || signal.includes('golden') || signal === 'above') && <ArrowUp size={10} />}
      {(signal.includes('bearish') || signal.includes('death') || signal === 'below') && <ArrowDown size={10} />}
      {display}
    </span>
  );
}

export const TechnicalAnalysis: React.FC<TechnicalAnalysisProps> = ({ technical: initialTechnical, ticker }) => {
  const [timeframe, setTimeframe] = useState<string>(DEFAULT_TIMEFRAME);
  const [technical, setTechnical] = useState<TechnicalData>(initialTechnical);
  const [loading, setLoading] = useState(false);
  const [tfError, setTfError] = useState<string | null>(null);
  const [viewMode, setViewMode] = useState<'chart' | 'text'>('chart');
  const [activeChartTab, setActiveChartTab] = useState<'MACD' | 'RSI' | 'Price' | 'Volume'>('MACD');

  // Setups-first sub-navigation
  const [activeSection, setActiveSection] = useState<'setups' | 'indicators' | 'advanced'>('setups');
  const [setups, setSetups] = useState<TradeSetupsData | null>(null);
  const [setupsLoading, setSetupsLoading] = useState(false);
  const [setupsError, setSetupsError] = useState<string | null>(null);

  // Ref-guarded lazy load. NOTE: do NOT gate on setupsLoading in an effect that also
  // calls setSetupsLoading — that re-triggers the effect and (with a cleanup) cancels the
  // in-flight request, leaving the spinner stuck forever. The ref tracks the ticker we've
  // fetched so we fetch exactly once per ticker (and on explicit reload).
  const setupsFetchedRef = useRef<string | null>(null);
  const loadSetups = useCallback(() => {
    setupsFetchedRef.current = ticker;
    setSetups(null);
    setSetupsError(null);
    setSetupsLoading(true);
    fetchTradeSetups(ticker)
      .then(r => setSetups(r.trade_setups))
      .catch(e => setSetupsError(e?.message || 'Failed to load trade setups'))
      .finally(() => setSetupsLoading(false));
  }, [ticker]);

  useEffect(() => {
    if (activeSection === 'setups' && setupsFetchedRef.current !== ticker) loadSetups();
  }, [activeSection, ticker, loadSetups]);

  const SECTIONS = [
    { key: 'setups' as const, label: 'Setups', Icon: Crosshair },
    { key: 'indicators' as const, label: 'Indicators', Icon: Activity },
    { key: 'advanced' as const, label: 'Advanced', Icon: Layers },
  ];

  const handleSelect = async (newTf: string) => {
    if (newTf === timeframe || loading) return;
    setTimeframe(newTf);
    setLoading(true);
    setTfError(null);
    try {
      const resp = await fetchTechnicalForTimeframe(ticker, newTf);
      setTechnical(resp.technical as TechnicalData);
    } catch (err: any) {
      setTfError(err?.message || 'Failed to load timeframe');
    } finally {
      setLoading(false);
    }
  };

  const activePreset = TIMEFRAME_PRESETS.find(p => p.key === timeframe) ?? TIMEFRAME_PRESETS[1];

  // Thin out labels for readability — show ~15 labels
  const displayLabels = useMemo(() => {
    if (!technical.timestamps?.length) return [];
    const totalPoints = technical.timestamps.length;
    const step = Math.max(1, Math.floor(totalPoints / 15));
    return technical.timestamps.map((t, i) => {
      if (i % step === 0) {
        const parts = t.split(' ');
        return parts.length > 1 ? `${parts[0].slice(5)} ${parts[1]}` : t.slice(5);
      }
      return '';
    });
  }, [technical.timestamps]);

  const rsiLabels = useMemo(() => {
    if (!technical.rsiTimestamps?.length) return [];
    const rsiStep = Math.max(1, Math.floor(technical.rsiTimestamps.length / 15));
    return technical.rsiTimestamps.map((t, i) => {
      if (i % rsiStep === 0) {
        const parts = t.split(' ');
        return parts.length > 1 ? `${parts[0].slice(5)} ${parts[1]}` : t.slice(5);
      }
      return '';
    });
  }, [technical.rsiTimestamps]);

  // Price chart data
  const priceChartData = useMemo(() => {
    if (!technical.timestamps?.length) return null;
    const totalPoints = technical.timestamps.length;
    const supportLine = new Array(totalPoints).fill(technical.supportLevel);
    const resistanceLine = new Array(totalPoints).fill(technical.resistanceLevel);
    return {
      labels: displayLabels,
      datasets: [
        {
          label: 'Price',
          data: technical.prices,
          borderColor: 'rgb(56, 189, 248)',
          backgroundColor: 'rgba(56, 189, 248, 0.1)',
          fill: true,
          borderWidth: 1.5,
          pointRadius: 0,
          tension: 0.1,
        },
        {
          label: `Support ($${technical.supportLevel})`,
          data: supportLine,
          borderColor: 'rgb(34, 197, 94)',
          borderWidth: 1.5,
          borderDash: [6, 3],
          pointRadius: 0,
          fill: false,
        },
        {
          label: `Resistance ($${technical.resistanceLevel})`,
          data: resistanceLine,
          borderColor: 'rgb(239, 68, 68)',
          borderWidth: 1.5,
          borderDash: [6, 3],
          pointRadius: 0,
          fill: false,
        },
      ],
    };
  }, [technical, displayLabels]);

  const priceChartOptions: ChartOptions<'line'> = {
    responsive: true,
    maintainAspectRatio: false,
    interaction: { mode: 'index', intersect: false },
    plugins: {
      legend: {
        position: 'top',
        labels: { color: '#999', boxWidth: 12, usePointStyle: true },
      },
      tooltip: {
        callbacks: {
          label: (ctx) => `${ctx.dataset.label}: $${ctx.parsed.y?.toFixed(2) || 'N/A'}`,
        },
      },
    },
    scales: {
      x: {
        ticks: { color: '#999', maxRotation: 0, autoSkip: true, maxTicksLimit: 12 },
        grid: { display: false },
      },
      y: {
        ticks: {
          color: '#999',
          callback: (v) => `$${Number(v).toFixed(0)}`,
        },
        grid: { color: 'rgba(128,128,128,0.15)' },
      },
    },
  };



  // Volume chart data
  const volumeChartData = useMemo(() => {
    if (!technical.timestamps?.length) return null;
    const volColors = technical.prices.map((p, i) =>
      i > 0 && p >= technical.prices[i - 1]
        ? 'rgba(34, 197, 94, 0.6)'
        : 'rgba(239, 68, 68, 0.6)'
    );
    return {
      labels: displayLabels,
      datasets: [
        {
          label: 'Volume',
          data: technical.volumes,
          backgroundColor: volColors,
          borderWidth: 0,
        },
      ],
    };
  }, [technical, displayLabels]);

  const volumeChartOptions: ChartOptions<'bar'> = {
    responsive: true,
    maintainAspectRatio: false,
    plugins: {
      legend: { display: false },
      tooltip: {
        callbacks: {
          label: (ctx) => `Volume: ${formatVolume(ctx.parsed.y ?? 0)}`,
        },
      },
    },
    scales: {
      x: {
        ticks: { color: '#999', maxRotation: 0, autoSkip: true, maxTicksLimit: 12 },
        grid: { display: false },
      },
      y: {
        ticks: {
          color: '#999',
          callback: (v) => formatVolume(Number(v)),
        },
        grid: { color: 'rgba(128,128,128,0.15)' },
      },
    },
  };

  // RSI chart data
  const rsiChartData = useMemo(() => {
    if (!technical.rsiValues?.length) return null;
    const overboughtLine = new Array(technical.rsiValues.length).fill(70);
    const oversoldLine = new Array(technical.rsiValues.length).fill(30);
    return {
      labels: rsiLabels,
      datasets: [
        {
          label: 'RSI',
          data: technical.rsiValues,
          borderColor: 'rgb(251, 191, 36)',
          backgroundColor: 'rgba(251, 191, 36, 0.1)',
          fill: true,
          borderWidth: 1.5,
          pointRadius: 0,
          tension: 0.2,
        },
        {
          label: 'Overbought (70)',
          data: overboughtLine,
          borderColor: 'rgba(239, 68, 68, 0.5)',
          borderWidth: 1,
          borderDash: [4, 4],
          pointRadius: 0,
          fill: false,
        },
        {
          label: 'Oversold (30)',
          data: oversoldLine,
          borderColor: 'rgba(34, 197, 94, 0.5)',
          borderWidth: 1,
          borderDash: [4, 4],
          pointRadius: 0,
          fill: false,
        },
      ],
    };
  }, [technical.rsiValues, rsiLabels]);

  const rsiChartOptions: ChartOptions<'line'> = {
    responsive: true,
    maintainAspectRatio: false,
    plugins: {
      legend: {
        position: 'top',
        labels: { color: '#999', boxWidth: 12, usePointStyle: true },
      },
    },
    scales: {
      x: {
        ticks: { color: '#999', maxRotation: 0, autoSkip: true, maxTicksLimit: 12 },
        grid: { display: false },
      },
      y: {
        min: 0,
        max: 100,
        ticks: { color: '#999', stepSize: 10 },
        grid: { color: 'rgba(128,128,128,0.15)' },
      },
    },
  };

  // MACD chart data
  const macdChartData = useMemo(() => {
    if (!technical.macd?.macdValues?.length) return null;
    const macd = technical.macd;
    const step = Math.max(1, Math.floor(macd.timestamps.length / 15));
    const labels = macd.timestamps.map((t, i) => (i % step === 0 ? t.slice(5) : ''));
    const histColors = macd.histogramValues.map((v) =>
      v >= 0 ? 'rgba(34, 197, 94, 0.6)' : 'rgba(239, 68, 68, 0.6)'
    );
    return {
      labels,
      datasets: [
        {
          type: 'bar' as const,
          label: 'Histogram',
          data: macd.histogramValues,
          backgroundColor: histColors,
          borderWidth: 0,
          yAxisID: 'y',
          order: 2,
        },
        {
          type: 'line' as const,
          label: 'MACD',
          data: macd.macdValues,
          borderColor: 'rgb(56, 189, 248)',
          borderWidth: 1.5,
          pointRadius: 0,
          tension: 0.2,
          yAxisID: 'y',
          order: 1,
        },
        {
          type: 'line' as const,
          label: 'Signal',
          data: macd.signalValues,
          borderColor: 'rgb(251, 191, 36)',
          borderWidth: 1.5,
          pointRadius: 0,
          tension: 0.2,
          borderDash: [4, 4],
          yAxisID: 'y',
          order: 1,
        },
      ],
    };
  }, [technical.macd]);

  const macdChartOptions: ChartOptions<'bar'> = {
    responsive: true,
    maintainAspectRatio: false,
    interaction: { mode: 'index', intersect: false },
    plugins: {
      legend: {
        position: 'top',
        labels: { color: '#999', boxWidth: 12, usePointStyle: true },
      },
      tooltip: {
        callbacks: {
          label: (ctx) => `${ctx.dataset.label}: ${ctx.parsed.y?.toFixed(4) || 'N/A'}`,
        },
      },
    },
    scales: {
      x: {
        ticks: { color: '#999', maxRotation: 0, autoSkip: true, maxTicksLimit: 12 },
        grid: { display: false },
      },
      y: {
        ticks: { color: '#999' },
        grid: {
          color: (ctx) => ctx.tick.value === 0 ? 'rgba(128,128,128,0.4)' : 'rgba(128,128,128,0.1)',
        },
      },
    },
  };

  if (technical.error || !technical.timestamps?.length) {
    return (
      <div className="glass-card">
        <div className="p-5">
          <h3 className="font-bold text-sm flex items-center gap-2">
            <Activity size={20} /> Technical Analysis
          </h3>
          <p className="text-base-content/60">No technical data available.</p>
        </div>
      </div>
    );
  }

  const va = technical.volumeAnalysis;
  const macd = technical.macd;
  const bb = technical.bollingerBands;
  const ma = technical.movingAverages;
  const ema = technical.emaCrossover;
  const spot = technical.prices?.[technical.prices.length - 1];

  return (
    <div className="glass-card">
      <div className="p-5">
        {/* Setups-first sub-navigation */}
        <div className="flex items-center gap-1 mb-4 bg-base-200/40 p-1 rounded-xl w-fit">
          {SECTIONS.map(({ key, label, Icon }) => (
            <button
              key={key}
              onClick={() => setActiveSection(key)}
              className={`flex items-center gap-1.5 px-3.5 py-1.5 rounded-lg text-xs font-semibold transition-all duration-150 ${
                activeSection === key ? 'bg-primary/15 text-primary shadow-sm shadow-primary/10' : 'text-base-content/50 hover:text-base-content hover:bg-base-100/40'
              }`}
            >
              <Icon className="w-3.5 h-3.5" /> {label}
            </button>
          ))}
        </div>

        {activeSection === 'setups' && (
          <div className="space-y-4 animate-fade-in">
            {setupsLoading && (
              <div className="flex items-center gap-2 justify-center py-12 text-sm text-base-content/60">
                <Loader2 className="w-5 h-5 animate-spin" /> Fusing volume, structure, regime &amp; dealer flow into trade setups…
              </div>
            )}
            {setupsError && !setupsLoading && (
              <div className="alert alert-error text-sm flex items-center justify-between">
                <span>{setupsError}</span>
                <button className="btn btn-ghost btn-xs" onClick={loadSetups}>Retry</button>
              </div>
            )}
            {setups && !setupsLoading && (
              <>
                <MarketContextHero context={setups.context} spot={setups.price} ticker={ticker} />
                <SectionIntro icon={<Crosshair size={16} className="text-primary" />} title="Trade setups">
                  Ranked, concrete plans — entry, stop, target and reward-to-risk — built from where the four institutional reads line up. Highest-conviction first.
                </SectionIntro>
                <TradeSetupCards data={setups} ticker={ticker} />
              </>
            )}
          </div>
        )}

        {activeSection === 'indicators' && (
        <>
        <div className="flex items-start justify-between flex-wrap gap-2 mb-3">
          <div className="flex items-center min-w-[20px]">
            {loading && <Loader2 size={14} className="animate-spin text-primary" />}
          </div>
          <div className="flex flex-wrap items-center gap-2">
            {TIMEFRAME_GROUPS.map(group => (
              <div key={group.label} className="flex flex-col items-center gap-0.5">
                <span className="text-[9px] text-base-content/40 uppercase tracking-wide">{group.label}</span>
                <div className="join join-horizontal">
                  {group.keys.map(k => {
                    const p = TIMEFRAME_PRESETS.find(x => x.key === k)!;
                    return (
                      <button
                        key={p.key}
                        className={`btn btn-xs join-item ${timeframe === p.key ? 'btn-primary' : 'btn-ghost'}`}
                        onClick={() => handleSelect(p.key)}
                        disabled={loading}
                        title={p.short}
                      >
                        {p.label}
                      </button>
                    );
                  })}
                </div>
              </div>
            ))}
          </div>
        </div>
        {tfError && (
          <div className="alert alert-error alert-sm mb-2 text-xs">
            {tfError}
          </div>
        )}

        {/* Market Regime Banner (Common) */}
        {technical.institutional && (
          <div className="mb-2">
            <MarketStateBanner regime={technical.institutional.regime} />
          </div>
        )}

        <div className="bg-base-300 rounded-xl mb-4 overflow-hidden shadow-xl border border-white/[0.05]">
          <div className="px-4 py-3 border-b border-white/[0.05] bg-gradient-to-r from-base-200/50 to-transparent flex flex-wrap justify-between items-center gap-4">
            <div className="flex flex-col">
              <h4 className="text-sm font-semibold text-base-content/90">
                Smart-Money Analysis
              </h4>
              <div className="flex flex-wrap items-center gap-3 text-[10px] mt-1">
                <div className="flex gap-2">
                  <div className="flex items-center gap-1"><div className="w-2 h-2 rounded bg-emerald-500/50"></div> Demand</div>
                  <div className="flex items-center gap-1"><div className="w-2 h-2 rounded bg-rose-500/50"></div> Supply</div>
                  <div className="flex items-center gap-1"><div className="w-2 h-2 rounded bg-indigo-500/50"></div> FVG</div>
                </div>
              </div>
            </div>

            <div className="join bg-base-200/80 p-0.5 rounded-lg border border-white/[0.05]">
              <button 
                className={`join-item btn btn-xs ${viewMode === 'chart' ? 'btn-active bg-primary/20 text-primary border-primary/30' : 'btn-ghost text-base-content/70'}`}
                onClick={() => setViewMode('chart')}
              >
                Chart
              </button>
              <button 
                className={`join-item btn btn-xs ${viewMode === 'text' ? 'btn-active bg-primary/20 text-primary border-primary/30' : 'btn-ghost text-base-content/70'}`}
                onClick={() => setViewMode('text')}
              >
                Text
              </button>
            </div>
          </div>
          
          <div className="p-2">
            {viewMode === 'chart' ? (
              <SmartMoneyChartOverlay technical={technical} timeframeShortLabel={activePreset.short} />
            ) : (
              technical.institutional && (
                <div className="p-2">
                  <InstitutionalTA data={technical.institutional} price={technical.prices?.[technical.prices.length - 1]} />
                </div>
              )
            )}
          </div>
        </div>

        {/* Top indicators row */}
        <div className="grid grid-cols-2 md:grid-cols-4 gap-2 mb-3">
          <div className="bg-base-200/40 rounded-xl py-1.5 px-2 border border-white/[0.03] text-center flex flex-col justify-center">
            <div className="text-[11px] text-base-content/50 leading-none mb-1">RSI (14)</div>
            <RsiGauge rsi={technical.currentRSI} />
          </div>
          <div className="bg-base-200/40 rounded-xl py-1.5 px-2 border border-white/[0.03] text-center flex flex-col justify-center">
            <div className="text-[11px] text-base-content/50 leading-none mb-1">Support</div>
            <div className="text-base font-bold text-success tabular-nums leading-none">
              ${technical.supportLevel.toFixed(2)}
            </div>
          </div>
          <div className="bg-base-200/40 rounded-xl py-1.5 px-2 border border-white/[0.03] text-center flex flex-col justify-center">
            <div className="text-[11px] text-base-content/50 leading-none mb-1">Resistance</div>
            <div className="text-base font-bold text-error tabular-nums leading-none">
              ${technical.resistanceLevel.toFixed(2)}
            </div>
          </div>
          <div className="bg-base-200/40 rounded-xl py-1.5 px-2 border border-white/[0.03] text-center flex flex-col justify-center">
            <div className="text-[11px] text-base-content/50 leading-none mb-1">Market Phase</div>
            <div>
              <PhaseIndicator phase={va?.phase || 'neutral'} />
            </div>
          </div>
        </div>

        {/* Momentum Indicators Row */}
        {(macd || bb || ma || ema) && (
          <>
            <h4 className="text-sm font-bold text-base-content/70 mb-1.5 flex items-center gap-2">
              <TrendingUp size={16} className="text-primary" />
              Momentum Indicators
              <span className="badge badge-sm badge-outline">Daily</span>
            </h4>
            <div className="grid grid-cols-2 md:grid-cols-4 gap-2 mb-3">
              {/* MACD */}
              {macd && (
                <div className="bg-base-200/40 rounded-xl p-2.5 border border-white/[0.03]">
                  <div className="text-xs text-base-content/50 mb-0.5">MACD (12,26,9)</div>
                  <div className={`text-base font-bold tabular-nums ${macd.signal === 'bullish' ? 'text-success' : 'text-error'}`}>
                    {macd.histogram > 0 ? '+' : ''}{macd.histogram.toFixed(2)}
                  </div>
                  <div className="flex items-center gap-1 mt-0.5">
                    <SignalBadge signal={macd.signal} />
                    {macd.crossover !== 'none' && (
                      <SignalBadge signal={macd.crossover} label={macd.crossover.replace('_', ' ')} />
                    )}
                  </div>
                  <div className="text-[10px] text-base-content/40 mt-1 tabular-nums">
                    MACD: {macd.macdLine.toFixed(2)} | Sig: {macd.signalLine.toFixed(2)}
                  </div>
                </div>
              )}

              {/* Bollinger Bands */}
              {bb && (
                <div className="bg-base-200/40 rounded-xl p-2.5 border border-white/[0.03]">
                  <div className="text-xs text-base-content/50 mb-0.5">Bollinger Bands</div>
                  <div className={`text-base font-bold tabular-nums ${bb.position === 'overbought' ? 'text-error' : bb.position === 'oversold' ? 'text-success' : 'text-info'}`}>
                    {(bb.percentB * 100).toFixed(0)}%B
                  </div>
                  <div className="flex items-center gap-1 mt-0.5">
                    <SignalBadge signal={bb.position} label={bb.position.replace('_', ' ')} />
                  </div>
                  <div className="text-[10px] text-base-content/40 mt-1 tabular-nums">
                    ${bb.lower.toFixed(0)} — ${bb.middle.toFixed(0)} — ${bb.upper.toFixed(0)}
                  </div>
                  <div className="text-[10px] text-base-content/40 tabular-nums">
                    Width: {bb.bandwidthPct.toFixed(1)}%
                  </div>
                </div>
              )}

              {/* Moving Averages */}
              {ma && (
                <div className="bg-base-200/40 rounded-xl p-2.5 border border-white/[0.03]">
                  <div className="text-xs text-base-content/50 mb-0.5">Moving Averages</div>
                  {ma.sma50 && (
                    <div className="flex items-center justify-between text-xs mb-0.5">
                      <span className="text-base-content/60">SMA 50:</span>
                      <span className="font-bold tabular-nums">${ma.sma50.toFixed(0)}</span>
                      {ma.priceVsSma50 && <SignalBadge signal={ma.priceVsSma50} />}
                    </div>
                  )}
                  {ma.sma200 && (
                    <div className="flex items-center justify-between text-xs mb-0.5">
                      <span className="text-base-content/60">SMA 200:</span>
                      <span className="font-bold tabular-nums">${ma.sma200.toFixed(0)}</span>
                      {ma.priceVsSma200 && <SignalBadge signal={ma.priceVsSma200} />}
                    </div>
                  )}
                  {ma.goldenDeathCross && (
                    <div className="mt-0.5">
                      <SignalBadge signal={ma.goldenDeathCross} label={ma.goldenDeathCross.replace('_', ' ')} />
                    </div>
                  )}
                </div>
              )}

              {/* EMA Crossover */}
              {ema && (
                <div className="bg-base-200/40 rounded-xl p-2.5 border border-white/[0.03]">
                  <div className="text-xs text-base-content/50 mb-0.5">EMA Crossover</div>
                  <div className={`text-base font-bold ${ema.signal === 'bullish' ? 'text-success' : 'text-error'}`}>
                    {ema.signal === 'bullish' ? <TrendingUp size={16} className="inline mr-1" /> : <TrendingDown size={16} className="inline mr-1" />}
                    {ema.signal.charAt(0).toUpperCase() + ema.signal.slice(1)}
                  </div>
                  <div className="text-[10px] text-base-content/40 mt-1">
                    EMA 12: ${ema.ema12.toFixed(2)}
                  </div>
                  <div className="text-[10px] text-base-content/40">
                    EMA 26: ${ema.ema26.toFixed(2)}
                  </div>
                </div>
              )}
            </div>

          </>
        )}

        {/* Horizontal Tabs for Charts */}
        <div className="bg-base-300 rounded-xl mb-4 overflow-hidden shadow-xl border border-white/[0.05]">
          <div className="bg-base-200/30 p-2 border-b border-white/[0.05]">
            <div className="join bg-base-200/80 p-0.5 rounded-lg border border-white/[0.05] w-full flex">
              {['MACD', 'RSI', 'Price', 'Volume'].map((tab) => (
                <button
                  key={tab}
                  className={`join-item btn btn-xs flex-1 ${activeChartTab === tab ? 'btn-active bg-primary/20 text-primary border-primary/30' : 'btn-ghost text-base-content/70'}`}
                  onClick={() => setActiveChartTab(tab as any)}
                >
                  {tab}
                </button>
              ))}
            </div>
          </div>
          <div className="p-3">

          {activeChartTab === 'MACD' && (
            <div>
              {macdChartData ? (
                <>
                  <h4 className="text-sm font-semibold text-base-content/70 mb-2">
                    MACD (12, 26, 9)
                    {macd && (
                      <span className={`ml-2 text-xs ${macd.signal === 'bullish' ? 'text-success' : 'text-error'}`}>
                        ({macd.signal}{macd.crossover !== 'none' ? ` — ${macd.crossover.replace('_', ' ')}` : ''})
                      </span>
                    )}
                  </h4>
                  <div className="h-44">
                    <Bar data={macdChartData as any} options={macdChartOptions} />
                  </div>
                </>
              ) : (
                <div className="text-center text-sm text-base-content/50 py-10">No MACD Data Available</div>
              )}
            </div>
          )}

          {activeChartTab === 'RSI' && (
            <div>
              {rsiChartData ? (
                <>
                  <h4 className="text-sm font-semibold text-base-content/70 mb-2">RSI (14-Period)</h4>
                  <div className="h-40">
                    <Line data={rsiChartData} options={rsiChartOptions} />
                  </div>
                </>
              ) : (
                <div className="text-center text-sm text-base-content/50 py-10">No RSI Data Available</div>
              )}
            </div>
          )}

          {activeChartTab === 'Price' && (
            <div>
              {priceChartData ? (
                <>
                  <h4 className="text-sm font-semibold text-base-content/70 mb-2">
                    Price Action (Simplified)
                  </h4>
                  <div className="h-64">
                    <Line data={priceChartData} options={priceChartOptions} />
                  </div>
                </>
              ) : (
                <div className="text-center text-sm text-base-content/50 py-10">No Price Data Available</div>
              )}
            </div>
          )}

          {activeChartTab === 'Volume' && (
            <div>
              {volumeChartData ? (
                <>
                  <div className="flex justify-between items-center mb-2">
                    <h4 className="text-sm font-semibold text-base-content/70">
                      Volume
                    </h4>
                    {va && (
                      <div className={`text-xs font-semibold ${
                        va.volumeTrend === 'increasing' ? 'text-success' 
                        : va.volumeTrend === 'decreasing' ? 'text-error' 
                        : 'text-warning'
                      }`}>
                        Volume {va.volumeTrend?.charAt(0).toUpperCase()}{va.volumeTrend?.slice(1)} {Math.abs(va.volumeChangePct || 0).toFixed(1)}%
                      </div>
                    )}
                  </div>
                  <div className="h-40">
                    <Bar data={volumeChartData} options={volumeChartOptions} />
                  </div>
                </>
              ) : (
                <div className="text-center text-sm text-base-content/50 py-10">No Volume Data Available</div>
              )}
            </div>
          )}
          </div>
        </div>

        {/* Analysis Summary */}
        <div className="bg-base-300 rounded-xl px-3 py-2 border border-primary/20">
          <h4 className="text-xs font-bold text-primary mb-1">📊 Analysis Summary</h4>
          <div className="text-xs text-base-content/70 space-y-0.5">
            <p>
              <strong>RSI Signal:</strong> {technical.rsiSignal}
            </p>
            {macd && (
              <p>
                <strong>MACD:</strong>{' '}
                <span className={macd.signal === 'bullish' ? 'text-success' : 'text-error'}>
                  {macd.signal.charAt(0).toUpperCase() + macd.signal.slice(1)}
                </span>
                {macd.crossover !== 'none' && (
                  <span className={macd.crossover.includes('bullish') ? 'text-success' : 'text-error'}>
                    {' '}({macd.crossover.replace('_', ' ')})
                  </span>
                )}
                {' — '}Histogram: {macd.histogram > 0 ? '+' : ''}{macd.histogram.toFixed(4)}
              </p>
            )}
            {bb && (
              <p>
                <strong>Bollinger Bands:</strong>{' '}
                Price is{' '}
                <span className={bb.position === 'overbought' ? 'text-error' : bb.position === 'oversold' ? 'text-success' : 'text-info'}>
                  {bb.position.replace('_', ' ')}
                </span>
                {' '}at {(bb.percentB * 100).toFixed(1)}%B (Range: ${bb.lower.toFixed(2)} — ${bb.upper.toFixed(2)})
              </p>
            )}
            {ma && ma.goldenDeathCross && (
              <p>
                <strong>SMA Cross:</strong>{' '}
                <span className={ma.goldenDeathCross === 'golden_cross' ? 'text-success' : 'text-error'}>
                  {ma.goldenDeathCross.replace('_', ' ').replace(/\b\w/g, (c) => c.toUpperCase())}
                </span>
                {' '}(SMA50: ${ma.sma50}{ma.sma200 ? ` | SMA200: $${ma.sma200}` : ''})
              </p>
            )}
            {va && (
              <>
                <p>
                  <strong>Volume Trend:</strong>{' '}
                  {va.volumeTrend?.charAt(0).toUpperCase()}
                  {va.volumeTrend?.slice(1)} ({va.volumeChangePct > 0 ? '+' : ''}
                  {va.volumeChangePct?.toFixed(1)}%) —
                  <strong> Price Trend:</strong>{' '}
                  {va.priceTrend?.charAt(0).toUpperCase()}
                  {va.priceTrend?.slice(1)} ({va.priceChangePct > 0 ? '+' : ''}
                  {va.priceChangePct?.toFixed(1)}%)
                </p>
                <div className="alert mt-1 p-2 bg-base-200/50 border-white/[0.05]">
                  <div>
                    <span className="font-bold text-[11px]">🏦 Institutional Flow:</span>
                    <p className="mt-0.5 leading-snug text-[11px]">{va.bigMoneyAnalysis}</p>
                  </div>
                </div>
              </>
            )}
          </div>
        </div>
        </>
        )}

        {activeSection === 'advanced' && (
          <div className="space-y-2 animate-fade-in">
            <SectionIntro icon={<Layers size={16} className="text-secondary" />} title="Advanced — the evidence">
              The institutional reads the setups are built from. Expand any panel to drill into the data behind a signal.
            </SectionIntro>
            <MicrostructurePanel ticker={ticker} price={spot} />
            <MarketStructurePanel ticker={ticker} price={spot} />
            <RegimePanel ticker={ticker} price={spot} />
            <DealerPositioningPanel ticker={ticker} price={spot} />
          </div>
        )}
      </div>
    </div>
  );
};
