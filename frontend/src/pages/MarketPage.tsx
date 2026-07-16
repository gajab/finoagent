import React, { useEffect, useMemo, useRef, useState } from 'react';
import { Link, useSearchParams } from 'react-router-dom';
import {
  Activity, TrendingUp, TrendingDown, Loader2, AlertCircle, RefreshCw,
  BarChart3, Globe, Waves, Sparkles, Gauge, Flame, Snowflake,
  ChevronDown, ChevronRight, MessageSquare, Send, Bot,
  Sigma, Shield, Target, Zap, Scale, Droplets, CalendarRange, Compass, Landmark,
} from 'lucide-react';
import {
  Chart as ChartJS, CategoryScale, LinearScale, PointElement, LineElement,
  BarElement, Title, Tooltip, Legend, Filler,
} from 'chart.js';
import { Line, Bar } from 'react-chartjs-2';
import { fetchMarketOverview, fetchMarketNarrative, fetchSectorDashboard, fetchSectorNarrative, sectorChat, fetchIndexQuote } from '../api';
import SectorCalendarHeatmap from '../components/SectorCalendarHeatmap';
import StyleCalendarHeatmap, { clearStyleCalendarCache } from '../components/StyleCalendarHeatmap';
import LiquidityPanel from '../components/LiquidityPanel';
import EconomicCycles from '../components/EconomicCycles';
import DebtRadarPage from './DebtRadarPage';
import type {
  MarketOverviewResponse, SectorDashboardResponse, IndexData, SectorData,
  IndustryData, IndexReturns, SectorChatTurn, SectorQuantAnalytics,
} from '../types';

ChartJS.register(
  CategoryScale, LinearScale, PointElement, LineElement, BarElement,
  Title, Tooltip, Legend, Filler,
);

// ---------------------------------------------------------------------------
// Timeframes
// ---------------------------------------------------------------------------

const INDEX_SUB_TIMEFRAMES: { key: keyof IndexReturns; label: string }[] = [
  { key: '1d', label: 'Today' },
  { key: '7d', label: '7D' },
  { key: '1m', label: '1M' },
  { key: 'ytd', label: 'YTD' },
  { key: '1y', label: '1Y' },
];

const SECTOR_TIMEFRAMES: { key: keyof IndexReturns; label: string }[] = [
  { key: '1d', label: 'Today' },
  { key: '7d', label: '7D' },
  { key: '1m', label: '1M' },
  { key: '3m', label: '3M' },
  { key: 'ytd', label: 'YTD' },
  { key: '1y', label: '1Y' },
  { key: '3y', label: '3Y' },
  { key: '5y', label: '5Y' },
];

// Index groups — order and labels for grouped render on Major Indices section
const INDEX_GROUPS: { id: string; label: string; icon: string }[] = [
  { id: 'US Large Cap', label: 'US Large Cap',     icon: '🇺🇸' },
  { id: 'US Size',      label: 'US Broad & Size',  icon: '📊' },
  { id: 'Global',       label: 'International',     icon: '🌍' },
  { id: 'Rates',        label: 'Treasury Yields',   icon: '🏛️' },
  { id: 'FX',           label: 'Foreign Exchange',  icon: '💱' },
  { id: 'Crypto',       label: 'Crypto',            icon: '₿' },
  { id: 'Commodities',  label: 'Commodities',       icon: '🥇' },
  { id: 'Volatility',   label: 'Volatility',        icon: '⚡' },
];

const INDUSTRY_TIMEFRAMES: { key: keyof IndexReturns; label: string }[] = [
  { key: '1d', label: 'Today' },
  { key: '7d', label: '7D' },
  { key: '1m', label: '1M' },
  { key: '3m', label: '3M' },
  { key: 'ytd', label: 'YTD' },
  { key: '1y', label: '1Y' },
  { key: '3y', label: '3Y' },
];

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

const fmtPct = (v: number | null | undefined): string => {
  if (v === null || v === undefined || Number.isNaN(v)) return '—';
  const sign = v > 0 ? '+' : '';
  return `${sign}${v.toFixed(2)}%`;
};

const fmtNum = (v: number | null | undefined): string => {
  if (v === null || v === undefined) return '—';
  const abs = Math.abs(v);
  if (abs >= 1e12) return `${(v / 1e12).toFixed(2)}T`;
  if (abs >= 1e9)  return `${(v / 1e9).toFixed(2)}B`;
  if (abs >= 1e6)  return `${(v / 1e6).toFixed(2)}M`;
  if (abs >= 1e3)  return `${(v / 1e3).toFixed(2)}K`;
  return v.toFixed(2);
};

const fmtPrice = (v: number | null | undefined): string => {
  if (v === null || v === undefined) return '—';
  return v.toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 });
};

const retColor = (v: number | null | undefined): string => {
  if (v === null || v === undefined || Number.isNaN(v)) return 'text-base-content/40';
  if (v > 0.05) return 'text-success';
  if (v < -0.05) return 'text-error';
  return 'text-base-content/60';
};

// Smooth green→red gradient cell for the heatmap
const heatStyle = (v: number | null | undefined): React.CSSProperties => {
  if (v === null || v === undefined || Number.isNaN(v)) return { background: 'rgba(120,120,120,0.06)' };
  const clamped = Math.max(-15, Math.min(15, v));
  const alpha = Math.min(0.18 + Math.abs(clamped) / 32, 0.6);
  const color = clamped > 0 ? `rgba(34,197,94,${alpha})` : `rgba(239,68,68,${alpha})`;
  return { background: color };
};

// ---------------------------------------------------------------------------
// Index card
// ---------------------------------------------------------------------------

function IndexCard({ idx, onRefresh }: { idx: IndexData; onRefresh: () => Promise<void> }) {
  const [refreshing, setRefreshing] = useState(false);
  const day = idx.returns['1d'];
  const spark = idx.sparkline || [];
  const sparkData = useMemo(() => ({
    labels: spark.map((_, i) => i),
    datasets: [{
      data: spark,
      borderColor: (day ?? 0) >= 0 ? 'rgba(34,197,94,1)' : 'rgba(239,68,68,1)',
      backgroundColor: (day ?? 0) >= 0 ? 'rgba(34,197,94,0.10)' : 'rgba(239,68,68,0.10)',
      borderWidth: 1.5,
      tension: 0.3,
      fill: true,
      pointRadius: 0,
    }],
  }), [spark, day]);

  const sparkOpts = {
    responsive: true,
    maintainAspectRatio: false,
    animation: false as const,
    plugins: { legend: { display: false }, tooltip: { enabled: false } },
    scales: { x: { display: false }, y: { display: false } },
    elements: { line: { borderJoinStyle: 'round' as const } },
  };

  const hasData = idx.price !== null && idx.price !== undefined;
  const rangePct = (idx.low_52w != null && idx.high_52w != null && idx.price != null && idx.high_52w > idx.low_52w)
    ? Math.max(2, Math.min(98, ((idx.price - idx.low_52w) / (idx.high_52w - idx.low_52w)) * 100))
    : null;

  return (
    <div className="rounded-2xl border border-white/[0.06] bg-base-100/60 p-4 backdrop-blur-sm hover:border-primary/30 hover:shadow-lg hover:shadow-primary/5 transition-all relative">
      {/* Ticker badge + day return */}
      <div className="flex items-center justify-between gap-2 mb-0.5">
        <span className="text-[10px] font-mono font-bold tracking-wide text-primary/75 bg-primary/[0.08] px-1.5 py-0.5 rounded-md">{idx.short}</span>
        <div className="flex items-center gap-1.5">
          <span className={`text-sm font-bold tabular-nums ${retColor(day)}`}>{fmtPct(day)}</span>
          <button
            onClick={async (e) => {
              e.stopPropagation();
              if (refreshing) return;
              setRefreshing(true);
              try {
                await onRefresh();
              } catch (err) {
                console.error(err);
              } finally {
                setRefreshing(false);
              }
            }}
            disabled={refreshing}
            className="btn btn-ghost btn-xs h-5 min-h-0 w-5 p-0 text-base-content/40 hover:text-primary transition-colors"
            title="Refresh price"
          >
            <RefreshCw className={`w-3 h-3 ${refreshing ? 'animate-spin text-primary' : ''}`} />
          </button>
        </div>
      </div>

      {/* Full name */}
      <div className="text-xs text-base-content/45 leading-tight mb-2 truncate">{idx.name}</div>

      {/* Price */}
      <div className="text-2xl font-bold tabular-nums">
        {hasData ? fmtPrice(idx.price) : <span className="text-base-content/25">—</span>}
      </div>

      {/* Sparkline */}
      <div className="h-12 mt-2">
        {spark.length > 0
          ? <Line data={sparkData} options={sparkOpts} />
          : <div className="h-full flex items-center justify-center text-[10px] text-base-content/25">no data</div>}
      </div>

      {/* 52-week range bar with position dot */}
      {rangePct != null && (
        <div className="mt-3">
          <div className="relative h-[3px] rounded-full bg-white/[0.07]">
            <div className="absolute inset-y-0 left-0 rounded-full bg-base-content/20" style={{ width: `${rangePct}%` }} />
            <div
              className="absolute top-1/2 -translate-y-1/2 -translate-x-1/2 w-2 h-2 rounded-full bg-white shadow ring-2 ring-white/20"
              style={{ left: `${rangePct}%` }}
            />
          </div>
          <div className="flex justify-between text-[9px] mt-1.5">
            <span className="tabular-nums text-base-content/30">{fmtPrice(idx.low_52w)}</span>
            <span className="font-semibold uppercase tracking-wider text-base-content/30">52W</span>
            <span className="tabular-nums text-base-content/30">{fmtPrice(idx.high_52w)}</span>
          </div>
        </div>
      )}

      {/* Multi-timeframe return grid */}
      <div className="grid grid-cols-5 gap-0.5 mt-2">
        {INDEX_SUB_TIMEFRAMES.map(tf => (
          <div key={tf.key} className={`rounded-lg py-1.5 text-center ${retColor(idx.returns[tf.key])}`} style={heatStyle(idx.returns[tf.key])}>
            <div className="text-[8px] font-bold uppercase tracking-wide text-base-content/40 mb-0.5">{tf.label}</div>
            <div className="text-[10px] font-bold tabular-nums">{fmtPct(idx.returns[tf.key])}</div>
          </div>
        ))}
      </div>

      {/* Volume — only surfaced when elevated */}
      {idx.volume_ratio != null && idx.volume_ratio > 1.15 && (
        <div className="flex items-center gap-1.5 mt-2 text-[10px] text-warning/80">
          <Activity className="w-3 h-3" />
          <span className="font-semibold">{idx.volume_ratio.toFixed(1)}× avg vol</span>
        </div>
      )}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Industry row (nested under sector)
// ---------------------------------------------------------------------------

function IndustryRow({ industry }: { industry: IndustryData }) {
  // Industry rows are visually distinct from sectors:
  //  - indigo accent bar on the left (inside a separate colored column)
  //  - italic, slightly-smaller, lighter-weight text with a leaf icon
  //  - narrower heat cells with a dotted underline border so they don't read
  //    as "another sector"
  return (
    <tr className="bg-primary/[0.04] hover:bg-primary/[0.08] text-[11px] border-l-2 border-primary/40">
      <td className="sticky left-0 bg-primary/[0.04] backdrop-blur z-10 pl-10 border-l-2 border-primary/40">
        <div className="flex items-center gap-1.5 text-base-content/70">
          <span className="w-1.5 h-1.5 rounded-full bg-primary/50" />
          <span className="italic font-normal text-[11px]">{industry.name}</span>
          <span className="text-[9px] text-primary/60 font-mono">{industry.symbol}</span>
        </div>
      </td>
      <td className="text-right tabular-nums text-base-content/70">{fmtPrice(industry.price)}</td>
      {SECTOR_TIMEFRAMES.map(tf => {
        const v = industry.returns[tf.key];
        return (
          <td key={tf.key} className="text-center p-1">
            <div
              className={`rounded-sm px-1.5 py-0.5 text-[10px] font-medium tabular-nums italic ${retColor(v)}`}
              style={heatStyle(v)}
            >
              {fmtPct(v)}
            </div>
          </td>
        );
      })}
      <td className="text-right text-[10px] tabular-nums">
        {industry.volume_ratio ? (
          <span className={industry.volume_ratio > 1.2 ? 'text-warning font-semibold' : 'text-base-content/50'}>
            {industry.volume_ratio.toFixed(2)}×
          </span>
        ) : '—'}
      </td>
    </tr>
  );
}

// ---------------------------------------------------------------------------
// Sector row with expandable industries
// ---------------------------------------------------------------------------

function SectorHeatmap({
  sectors,
  expanded,
  onToggle,
}: {
  sectors: SectorData[];
  expanded: Set<string>;
  onToggle: (symbol: string) => void;
}) {
  return (
    <div className="overflow-x-auto rounded-2xl border border-white/[0.06] bg-base-100/60">
      <table className="table table-sm">
        <thead>
          <tr className="text-[11px] text-base-content/60">
            <th className="sticky left-0 bg-base-100/80 backdrop-blur z-10">Sector / Industry</th>
            <th className="text-right">Price</th>
            {SECTOR_TIMEFRAMES.map(tf => (
              <th key={tf.key} className="text-center">{tf.label}</th>
            ))}
            <th className="text-right">Vol Δ</th>
          </tr>
        </thead>
        <tbody>
          {sectors.map(s => {
            const isOpen = expanded.has(s.symbol);
            const industries = s.industries || [];
            return (
              <React.Fragment key={s.symbol}>
                <tr
                  className="hover:bg-base-200/30 cursor-pointer"
                  onClick={() => onToggle(s.symbol)}
                >
                  <td className="sticky left-0 bg-base-100/80 backdrop-blur z-10">
                    <div className="flex items-center gap-1.5">
                      {industries.length > 0 ? (
                        isOpen
                          ? <ChevronDown className="w-3.5 h-3.5 text-primary" />
                          : <ChevronRight className="w-3.5 h-3.5 text-base-content/40" />
                      ) : <span className="w-3.5" />}
                      <div>
                        <div className="font-semibold text-sm">{s.name}</div>
                        <div className="text-[10px] text-base-content/40">
                          {s.symbol}
                          {industries.length > 0 && <span className="ml-1">· {industries.length} industries</span>}
                          {s.error && <span className="ml-1 text-warning">· {s.error}</span>}
                        </div>
                      </div>
                    </div>
                  </td>
                  <td className="text-right tabular-nums text-sm">{fmtPrice(s.price)}</td>
                  {SECTOR_TIMEFRAMES.map(tf => {
                    const v = s.returns[tf.key];
                    return (
                      <td key={tf.key} className="text-center p-1">
                        <div className={`rounded px-2 py-1 text-xs font-semibold tabular-nums ${retColor(v)}`} style={heatStyle(v)}>
                          {fmtPct(v)}
                        </div>
                      </td>
                    );
                  })}
                  <td className="text-right text-xs tabular-nums">
                    {s.volume_ratio ? (
                      <span className={s.volume_ratio > 1.2 ? 'text-warning font-semibold' : 'text-base-content/60'}>
                        {s.volume_ratio.toFixed(2)}×
                      </span>
                    ) : '—'}
                  </td>
                </tr>
                {isOpen && industries.map(ind => (
                  <IndustryRow key={ind.symbol} industry={ind} />
                ))}
              </React.Fragment>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Rotation pill group
// ---------------------------------------------------------------------------

function RotationGroup({ title, items, icon: Icon, tone, emptyLabel }: {
  title: string;
  items: {
    name: string; return: number; signal?: string; volume_ratio: number;
    flow_score?: number;
  }[];
  icon: React.ComponentType<{ className?: string }>;
  tone: 'success' | 'error' | 'info' | 'warning';
  emptyLabel: string;
}) {
  const T = {
    success: { border: 'border-success/20', bg: 'bg-success/5',   text: 'text-success',   bar: 'bg-success' },
    error:   { border: 'border-error/20',   bg: 'bg-error/5',     text: 'text-error',     bar: 'bg-error'   },
    info:    { border: 'border-info/20',    bg: 'bg-info/5',      text: 'text-info',      bar: 'bg-info'    },
    warning: { border: 'border-warning/20', bg: 'bg-warning/5',   text: 'text-warning',   bar: 'bg-warning' },
  }[tone];

  return (
    <div className={`rounded-2xl border ${T.border} ${T.bg} overflow-hidden`}>
      {/* Top accent stripe */}
      <div className={`h-0.5 ${T.bar} opacity-50`} />
      <div className="p-4">
        <div className={`flex items-center gap-2 mb-3 ${T.text}`}>
          <Icon className="w-4 h-4" />
          <span className="text-xs font-bold uppercase tracking-wide">{title}</span>
        </div>
        {items.length === 0 ? (
          <div className="text-xs text-base-content/35 italic">{emptyLabel}</div>
        ) : (
          <div className="space-y-2">
            {items.map(it => (
              <div key={it.name} className="flex items-start justify-between gap-2">
                <div className="min-w-0 flex-1">
                  <div className="text-sm font-medium text-base-content/85 truncate leading-tight">{it.name}</div>
                  {it.signal && (
                    <div className="text-[9px] uppercase tracking-wider text-base-content/40 mt-0.5 font-medium">
                      {it.signal}
                    </div>
                  )}
                </div>
                <div className="flex items-center gap-1.5 flex-shrink-0 pt-0.5">
                  <span className={`tabular-nums font-bold text-sm ${retColor(it.return)}`}>{fmtPct(it.return)}</span>
                  {it.volume_ratio >= 1.2 && (
                    <span className="text-[9px] text-warning/80 font-bold">{it.volume_ratio.toFixed(1)}×</span>
                  )}
                </div>
              </div>
            ))}
          </div>
        )}
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Sector chat panel
// ---------------------------------------------------------------------------

const SUGGESTED_QUESTIONS = [
  'Which sectors are showing the clearest risk-on behavior?',
  'Is money rotating into defensives or cyclicals right now?',
  'Why might semiconductors be lagging the rest of tech?',
  'Which industry looks most contrarian vs its 1Y trend?',
];

function SectorChat({ timeframe }: { timeframe: string }) {
  const [turns, setTurns] = useState<SectorChatTurn[]>([]);
  const [input, setInput] = useState('');
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const listRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (listRef.current) listRef.current.scrollTop = listRef.current.scrollHeight;
  }, [turns, busy]);

  const ask = async (q: string) => {
    const question = q.trim();
    if (!question || busy) return;
    setErr(null);
    const nextHistory: SectorChatTurn[] = [...turns, { role: 'user', content: question }];
    setTurns(nextHistory);
    setInput('');
    setBusy(true);
    try {
      const res = await sectorChat(question, timeframe, turns);
      setTurns([...nextHistory, { role: 'assistant', content: res.answer }]);
    } catch (e: any) {
      setErr(e?.message || 'Chat failed');
      setTurns(nextHistory); // keep the question so user can retry
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="rounded-2xl border border-primary/20 bg-primary/5 p-4">
      <div className="flex items-center gap-2 mb-3">
        <Bot className="w-4 h-4 text-primary" />
        <span className="text-xs font-bold uppercase tracking-wide text-primary">
          Chat with the Sector Analyst
        </span>
        <span className="text-[10px] text-base-content/50 ml-auto">
          Grounded on {timeframe.toUpperCase()} live data
        </span>
      </div>

      {turns.length === 0 && (
        <div className="mb-3">
          <div className="text-[10px] text-base-content/50 mb-2 uppercase tracking-wide">Try asking</div>
          <div className="flex flex-wrap gap-1.5">
            {SUGGESTED_QUESTIONS.map(q => (
              <button
                key={q}
                onClick={() => ask(q)}
                disabled={busy}
                className="text-[11px] px-2.5 py-1 rounded-full border border-primary/20 bg-base-100/60
                  hover:bg-primary/10 hover:border-primary/40 transition-colors text-base-content/80"
              >
                {q}
              </button>
            ))}
          </div>
        </div>
      )}

      <div
        ref={listRef}
        className="space-y-2 max-h-80 overflow-y-auto pr-1"
      >
        {turns.map((t, i) => (
          <div
            key={i}
            className={`text-xs leading-relaxed rounded-xl px-3 py-2 ${
              t.role === 'user'
                ? 'bg-primary/10 border border-primary/20 text-base-content/90 ml-8'
                : 'bg-base-100/70 border border-white/[0.06] text-base-content/85 mr-8'
            }`}
          >
            <div className="text-[9px] font-semibold uppercase tracking-wider text-base-content/50 mb-1">
              {t.role === 'user' ? 'You' : 'Analyst'}
            </div>
            <div className="whitespace-pre-wrap">{t.content}</div>
          </div>
        ))}
        {busy && (
          <div className="flex items-center gap-2 text-xs text-base-content/50 px-3 py-2">
            <Loader2 className="w-3.5 h-3.5 animate-spin" />
            thinking…
          </div>
        )}
      </div>

      {err && (
        <div className="mt-2 text-[11px] text-error flex items-center gap-1.5">
          <AlertCircle className="w-3 h-3" /> {err}
        </div>
      )}

      <form
        onSubmit={(e) => { e.preventDefault(); ask(input); }}
        className="mt-3 flex items-center gap-2"
      >
        <input
          type="text"
          value={input}
          onChange={e => setInput(e.target.value)}
          placeholder="Ask anything about the current sector landscape…"
          disabled={busy}
          className="input input-sm input-bordered bg-base-100/70 flex-1 text-sm"
        />
        <button type="submit" disabled={busy || !input.trim()} className="btn btn-sm btn-primary gap-1.5">
          <Send className="w-3.5 h-3.5" />
          Ask
        </button>
      </form>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Quant analytics panel
// ---------------------------------------------------------------------------

type SortKey = 'name' | 'price' | 'rs' | 'vol' | 'sharpe' | 'beta' | 'dd' | 'persist' | 'stage';

function StatCard({ icon: Icon, label, value, sub, tone = 'neutral' }: {
  icon: React.ComponentType<{ className?: string }>;
  label: string;
  value: string;
  sub?: string;
  tone?: 'neutral' | 'good' | 'warn';
}) {
  const toneMap = {
    neutral: 'border-white/[0.06] bg-base-100/60',
    good:    'border-success/30 bg-success/5',
    warn:    'border-warning/30 bg-warning/5',
  };
  return (
    <div className={`rounded-2xl border p-3 ${toneMap[tone]}`}>
      <div className="flex items-center gap-1.5 text-[10px] uppercase tracking-wider text-base-content/50">
        <Icon className="w-3 h-3" />
        {label}
      </div>
      <div className="text-xl font-bold tabular-nums mt-1">{value}</div>
      {sub && <div className="text-[10px] text-base-content/50 mt-0.5">{sub}</div>}
    </div>
  );
}

function colorForSharpe(v: number | null | undefined): string {
  if (v === null || v === undefined || Number.isNaN(v)) return 'text-base-content/40';
  if (v >= 1.5) return 'text-success font-bold';
  if (v >= 0.5) return 'text-success';
  if (v <= -0.5) return 'text-error';
  return 'text-base-content/70';
}

function colorForRS(v: number | null | undefined): string {
  if (v === null || v === undefined) return 'text-base-content/40';
  if (v > 1) return 'text-success font-semibold';
  if (v < -1) return 'text-error font-semibold';
  return 'text-base-content/70';
}

function colorForDD(v: number | null | undefined): string {
  if (v === null || v === undefined) return 'text-base-content/40';
  if (v <= -15) return 'text-error font-semibold';
  if (v <= -7) return 'text-warning';
  return 'text-base-content/70';
}

function colorForStage(stage: string | null | undefined): string {
  if (!stage || stage === 'Neutral') return 'badge-ghost';
  if (stage === 'Saturated / Exhausted' || stage === 'Overextended' || stage === 'Laggard') return 'badge-error';
  if (stage === 'Accelerating' || stage === 'Early Uptrend' || stage === 'Recovering') return 'badge-success';
  if (stage === 'Maturing' || stage === 'Late Uptrend' || stage === 'Fading' || stage === 'Weakening' || stage === 'Consolidating') return 'badge-warning';
  return 'badge-ghost';
}

function SectorQuantPanel({
  sectors, summary, timeframe,
}: {
  sectors: SectorData[];
  summary: SectorDashboardResponse['summary'];
  timeframe: string;
}) {
  const [sortKey, setSortKey] = useState<SortKey>('rs');
  const [sortDir, setSortDir] = useState<'asc' | 'desc'>('desc');

  const rows = useMemo(() => {
    const arr = sectors.map(s => {
      const a: SectorQuantAnalytics = s.analytics || {};
      return {
        name: s.name,
        symbol: s.symbol,
        price: s.price,
        rs: a.rs_vs_spx,
        vol: a.volatility_ann,
        sharpe: a.sharpe,
        beta: a.beta_vs_spx,
        dd: a.max_drawdown,
        persist: a.trend_persistence,
        stage: a.rotation_stage,
        zscore: a.rs_zscore,
        momentum: a.momentum_status,
      };
    });
    const cmp = (a: any, b: any): number => {
      const av = a[sortKey];
      const bv = b[sortKey];
      if (av === null || av === undefined) return 1;
      if (bv === null || bv === undefined) return -1;
      if (sortKey === 'name') return String(av).localeCompare(String(bv));
      return (av - bv);
    };
    arr.sort((a, b) => {
      const r = cmp(a, b);
      return sortDir === 'asc' ? r : -r;
    });
    return arr;
  }, [sectors, sortKey, sortDir]);

  const toggle = (k: SortKey) => {
    if (sortKey === k) {
      setSortDir(d => d === 'asc' ? 'desc' : 'asc');
    } else {
      setSortKey(k);
      setSortDir(k === 'name' ? 'asc' : 'desc');
    }
  };

  const SortTh = ({ k, children, align = 'right' }: { k: SortKey; children: React.ReactNode; align?: 'left' | 'right' }) => (
    <th
      onClick={() => toggle(k)}
      className={`cursor-pointer hover:text-primary transition-colors select-none ${align === 'right' ? 'text-right' : 'text-left'}`}
    >
      <span className="inline-flex items-center gap-1">
        {children}
        {sortKey === k && (sortDir === 'asc' ? '▲' : '▼')}
      </span>
    </th>
  );

  const tf = timeframe.toUpperCase();

  return (
    <div className="space-y-3">
      {/* Summary stat cards */}
      <div className="grid grid-cols-2 sm:grid-cols-3 lg:grid-cols-6 gap-2">
        <StatCard
          icon={Target}
          label={`Beating SPX (${tf})`}
          value={summary ? `${summary.beating_spx_count}/${summary.total_with_rs}` : '—'}
          sub={summary && summary.total_with_rs
            ? `${Math.round(summary.beating_spx_count / summary.total_with_rs * 100)}% of sectors`
            : undefined}
          tone={summary && summary.beating_spx_count > (summary.total_with_rs / 2) ? 'good' : 'neutral'}
        />
        <StatCard
          icon={Sigma}
          label={`Avg Vol (${tf})`}
          value={summary?.avg_volatility != null ? `${summary.avg_volatility}%` : '—'}
          sub="annualised daily"
          tone={summary?.avg_volatility != null && summary.avg_volatility > 25 ? 'warn' : 'neutral'}
        />
        <StatCard
          icon={Zap}
          label={`Avg Sharpe (${tf})`}
          value={summary?.avg_sharpe != null ? summary.avg_sharpe.toFixed(2) : '—'}
          sub="risk-adj. return"
          tone={summary?.avg_sharpe != null && summary.avg_sharpe > 0.5 ? 'good' : 'neutral'}
        />
        <StatCard
          icon={Scale}
          label="Avg Beta vs SPX"
          value={summary?.avg_beta != null ? summary.avg_beta.toFixed(2) : '—'}
          sub="1.0 = market"
        />
        <StatCard
          icon={Activity}
          label="Trend Persistence"
          value={summary?.avg_trend_persistence != null ? `${summary.avg_trend_persistence}%` : '—'}
          sub="days closing up"
        />
        <StatCard
          icon={Shield}
          label="Dispersion"
          value={summary?.dispersion != null ? `${summary.dispersion}pp` : '—'}
          sub={summary?.dispersion != null && summary.dispersion > 6
            ? "stock-picker's tape"
            : 'macro-driven'}
          tone={summary?.dispersion != null && summary.dispersion > 6 ? 'good' : 'neutral'}
        />
      </div>

      {/* Sortable analytics table */}
      <div className="overflow-x-auto rounded-2xl border border-white/[0.06] bg-base-100/60">
        <table className="table table-sm">
          <thead>
            <tr className="text-[11px] text-base-content/60">
              <SortTh k="name" align="left">Sector</SortTh>
              <SortTh k="price">Price</SortTh>
              <SortTh k="rs">RS vs SPX ({tf})</SortTh>
              <SortTh k="vol">Vol (ann.)</SortTh>
              <SortTh k="sharpe">Sharpe</SortTh>
              <SortTh k="beta">β vs SPX</SortTh>
              <SortTh k="dd">Max DD</SortTh>
              <SortTh k="persist">Up-days %</SortTh>
              <SortTh k="stage">Rotation Stage</SortTh>
            </tr>
          </thead>
          <tbody>
            {rows.map(r => (
              <tr key={r.symbol} className="hover:bg-base-200/30 text-xs">
                <td>
                  <div className="font-semibold">{r.name}</div>
                  <div className="text-[9px] text-base-content/40">{r.symbol}</div>
                </td>
                <td className="text-right tabular-nums font-medium">
                  {r.price != null ? `$${fmtPrice(r.price)}` : '—'}
                </td>
                <td className={`text-right tabular-nums ${colorForRS(r.rs)}`}>
                  {r.rs != null ? `${r.rs > 0 ? '+' : ''}${r.rs.toFixed(2)}pp` : '—'}
                </td>
                <td className="text-right tabular-nums text-base-content/70">
                  {r.vol != null ? `${r.vol.toFixed(1)}%` : '—'}
                </td>
                <td className={`text-right tabular-nums ${colorForSharpe(r.sharpe)}`}>
                  {r.sharpe != null ? r.sharpe.toFixed(2) : '—'}
                </td>
                <td className="text-right tabular-nums text-base-content/70">
                  {r.beta != null ? r.beta.toFixed(2) : '—'}
                </td>
                <td className={`text-right tabular-nums ${colorForDD(r.dd)}`}>
                  {r.dd != null ? `${r.dd.toFixed(1)}%` : '—'}
                </td>
                <td className="text-right tabular-nums text-base-content/70">
                  {r.persist != null ? `${r.persist.toFixed(0)}%` : '—'}
                </td>
                <td className="text-right">
                  {r.stage ? (
                    <div className="flex flex-col items-end gap-1">
                      <span className={`badge badge-sm text-[10px] uppercase tracking-wider ${colorForStage(r.stage)}`}>
                        {r.stage}
                      </span>
                      {r.zscore != null && (
                        <span className="text-[9px] text-base-content/40 font-mono">
                          Z: {r.zscore > 0 ? '+' : ''}{r.zscore.toFixed(1)} · {r.momentum === 'Accelerating' ? 'Acc' : 'Dec'}
                        </span>
                      )}
                    </div>
                  ) : '—'}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      <div className="text-[10px] text-base-content/40 leading-relaxed px-1">
        <span className="font-semibold">RS vs SPX</span> = sector return minus SPX return over {tf} (pp) ·
        <span className="font-semibold"> Vol</span> = annualised stddev of daily returns ·
        <span className="font-semibold"> Sharpe</span> uses 4% risk-free ·
        <span className="font-semibold"> β</span> is OLS slope vs SPY ·
        <span className="font-semibold"> Max DD</span> is peak-to-trough within the {tf} window ·
        <span className="font-semibold"> Up-days %</span> is trend persistence ·
        <span className="font-semibold"> Stage</span> uses 50-day Z-Score of RS ratio + MACD slope.
      </div>
    </div>
  );
}


// ---------------------------------------------------------------------------
// Left-nav sections
// ---------------------------------------------------------------------------

type SectionId = 'indices' | 'sectors' | 'performance' | 'liquidity' | 'macro' | 'cycles' | 'debt';

const SECTIONS: { id: SectionId; label: string; icon: React.ReactNode; description: string }[] = [
  {
    id: 'indices',
    label: 'Major Indices',
    icon: <BarChart3 className="w-5 h-5" />,
    description: 'Global equity benchmarks, rates, FX, commodities & crypto with sparklines and multi-timeframe returns.',
  },
  {
    id: 'sectors',
    label: 'Sectors',
    icon: <Waves className="w-5 h-5" />,
    description: 'Sector rotation, leaders & laggards, ranked heatmap and quant analytics with AI diagnosis.',
  },
  {
    id: 'performance',
    label: 'Performance by Years',
    icon: <CalendarRange className="w-5 h-5" />,
    description: 'Calendar-year return heatmaps for sectors and investment styles, ranked best to worst.',
  },
  {
    id: 'liquidity',
    label: 'Liquidity & Funding',
    icon: <Droplets className="w-5 h-5" />,
    description: 'Net Fed Liquidity, bank reserves and SOFR — the Fed plumbing behind risk-asset expansions.',
  },
  {
    id: 'macro',
    label: 'Macro Regime',
    icon: <Compass className="w-5 h-5" />,
    description: 'Growth × Inflation regime, financial conditions, credit risk, volatility and cross-asset signals.',
  },
  {
    id: 'cycles',
    label: 'Business Cycle',
    icon: <Activity className="w-5 h-5" />,
    description: 'Which economic cycle phase each major economy is in — and what asset classes historically outperform.',
  },
  {
    id: 'debt',
    label: 'Debt Radar',
    icon: <Landmark className="w-5 h-5" />,
    description: 'Real-time entry-timing for debt instruments — score any bond/credit ETF across seven signal families.',
  },
];

// ---------------------------------------------------------------------------
// Main page
// ---------------------------------------------------------------------------

function NavItem({
  s, active, onClick,
}: {
  s: { id: string; label: string; icon: React.ReactNode };
  active: boolean;
  onClick: () => void;
}) {
  return (
    <button
      onClick={onClick}
      className={`w-full flex items-center gap-2.5 px-3 py-2.5 rounded-xl text-left transition-all relative
        ${active
          ? 'bg-primary/10 text-primary'
          : 'text-base-content/55 hover:bg-base-200/60 hover:text-base-content'}`}
    >
      {active && (
        <span className="absolute left-0.5 top-1/2 -translate-y-1/2 w-[3px] h-5 bg-primary rounded-full" />
      )}
      <span className={`shrink-0 transition-colors ${active ? 'text-primary' : 'text-base-content/35'}`}>
        {s.icon}
      </span>
      <span className="text-sm font-medium">{s.label}</span>
    </button>
  );
}

export default function MarketPage() {
  const [overview, setOverview] = useState<MarketOverviewResponse | null>(null);
  const [sectors, setSectors] = useState<SectorDashboardResponse | null>(null);

  const [loadingOverview, setLoadingOverview] = useState(false);
  const [loadingSectors, setLoadingSectors] = useState(false);

  const [errorOverview, setErrorOverview] = useState<string | null>(null);
  const [errorSectors, setErrorSectors] = useState<string | null>(null);

  // LLM narratives — loaded only on user action
  const [marketNarrative, setMarketNarrative] = useState<string | null>(null);
  const [sectorIntelligence, setSectorIntelligence] = useState<string | null>(null);
  const [loadingNarrative, setLoadingNarrative] = useState(false);
  const [loadingSectorNarrative, setLoadingSectorNarrative] = useState(false);

  const [sectorTf, setSectorTf] = useState<string>('1m');
  const [expandedSectors, setExpandedSectors] = useState<Set<string>>(new Set());
  const [performanceRefreshKey, setPerformanceRefreshKey] = useState(0);
  
  const [searchParams, setSearchParams] = useSearchParams();
  const [activeSection, setActiveSection] = useState<SectionId>('indices');

  useEffect(() => {
    const section = searchParams.get('section') as SectionId;
    if (section && ['indices', 'sectors', 'performance', 'liquidity', 'macro', 'cycles', 'debt'].includes(section)) {
      setActiveSection(section);
    } else if (!section) {
      setActiveSection('indices');
    }
  }, [searchParams]);

  const selectSection = (id: SectionId) => {
    setActiveSection(id);
    const next = new URLSearchParams(searchParams);
    next.set('section', id);
    setSearchParams(next, { replace: true });
  };

  const toggleSector = (symbol: string) => {
    setExpandedSectors(prev => {
      const next = new Set(prev);
      if (next.has(symbol)) next.delete(symbol); else next.add(symbol);
      return next;
    });
  };

  const loadOverview = async () => {
    setLoadingOverview(true);
    setErrorOverview(null);
    try { setOverview(await fetchMarketOverview()); }
    catch (e: any) { setErrorOverview(e?.message || 'Failed to load market overview'); }
    finally { setLoadingOverview(false); }
  };

  const handleRefreshIndex = async (symbol: string) => {
    try {
      const updatedIndex = await fetchIndexQuote(symbol);
      setOverview(prev => {
        if (!prev) return prev;
        return {
          ...prev,
          indices: prev.indices.map(idx => idx.symbol === symbol ? updatedIndex : idx),
        };
      });
    } catch (e: any) {
      console.error(`Failed to refresh index ${symbol}:`, e);
    }
  };

  const loadSectors = async (tf: string) => {
    setLoadingSectors(true);
    setErrorSectors(null);
    try { setSectors(await fetchSectorDashboard(tf)); }
    catch (e: any) { setErrorSectors(e?.message || 'Failed to load sector data'); }
    finally { setLoadingSectors(false); }
  };

  const getMarketNarrative = async () => {
    setLoadingNarrative(true);
    try {
      const r = await fetchMarketNarrative();
      setMarketNarrative(r.narrative || null);
    } catch (e: any) {
      setMarketNarrative(`Error: ${e?.message || 'Failed'}`);
    } finally {
      setLoadingNarrative(false);
    }
  };

  const getSectorNarrative = async () => {
    setLoadingSectorNarrative(true);
    try {
      const r = await fetchSectorNarrative(sectorTf);
      setSectorIntelligence(r.intelligence || null);
    } catch (e: any) {
      setSectorIntelligence(`Error: ${e?.message || 'Failed'}`);
    } finally {
      setLoadingSectorNarrative(false);
    }
  };

  useEffect(() => { loadOverview(); }, []);
  useEffect(() => { loadSectors(sectorTf); setSectorIntelligence(null); }, [sectorTf]);

  // Sector bar chart for selected timeframe
  const sectorBarData = useMemo(() => {
    if (!sectors) return null;
    const rows = [...sectors.sectors]
      .filter(s => s.returns[sectorTf as keyof IndexReturns] !== null && s.returns[sectorTf as keyof IndexReturns] !== undefined)
      .sort((a, b) => {
      const av = a.returns[sectorTf as keyof IndexReturns] ?? 0;
      const bv = b.returns[sectorTf as keyof IndexReturns] ?? 0;
      return (bv as number) - (av as number);
    });
    if (rows.length === 0) return null;
    return {
      labels: rows.map(r => r.name),
      datasets: [{
        label: `${sectorTf.toUpperCase()} Return %`,
        data: rows.map(r => r.returns[sectorTf as keyof IndexReturns] ?? 0),
        backgroundColor: rows.map(r => {
          const v = r.returns[sectorTf as keyof IndexReturns] as number | null | undefined;
          return (v ?? 0) >= 0 ? 'rgba(34,197,94,0.75)' : 'rgba(239,68,68,0.75)';
        }),
        borderRadius: 6,
        borderSkipped: false,
      }],
    };
  }, [sectors, sectorTf]);

  const sectorBarOpts = {
    indexAxis: 'y' as const,
    responsive: true,
    maintainAspectRatio: false,
    // Disable chart animations so the chart appears in-sync with the
    // rotation pills and heatmap (which render instantly).
    animation: false as const,
    plugins: {
      legend: { display: false },
      tooltip: {
        callbacks: {
          label: (ctx: any) => `${(ctx.parsed.x ?? 0).toFixed(2)}%`,
        },
      },
    },
    scales: {
      x: { grid: { color: 'rgba(255,255,255,0.04)' }, ticks: { callback: (v: any) => `${v}%` } },
      y: { grid: { display: false } },
    },
  };

  // Count of sectors with full timeframe data — health indicator
  const dataHealth = useMemo(() => {
    if (!sectors) return null;
    const total = sectors.sectors.length;
    const complete = sectors.sectors.filter(s =>
      SECTOR_TIMEFRAMES.every(tf => s.returns[tf.key] !== null && s.returns[tf.key] !== undefined)
    ).length;
    return { total, complete };
  }, [sectors]);

  return (
    <div className="container-app py-6 animate-fade-in">
      {/* Header */}
      <div className="flex items-center justify-between mb-6">
        <div>
          <h1 className="text-2xl lg:text-3xl font-bold tracking-tight flex items-center gap-2">
            <Globe className="w-7 h-7 text-primary" />
            Market
          </h1>
          <p className="text-sm text-base-content/60 mt-1">
            Real-time indices, sector rotation, and thematic supply-chain intelligence.
          </p>
        </div>
      </div>

      <div className="flex flex-col lg:flex-row gap-6">
        {/* Left navigation */}
        <div className="w-full lg:w-52 shrink-0">
          <nav className="lg:sticky lg:top-6 rounded-2xl border border-white/[0.06] bg-base-100/40 p-2 backdrop-blur-sm">
            {/* Markets group */}
            <div className="px-3 pt-2 pb-1">
              <span className="text-[9px] font-bold uppercase tracking-widest text-base-content/30">Markets</span>
            </div>
            <div className="space-y-0.5">
              {(['indices', 'sectors', 'performance'] as SectionId[]).map(id => {
                const s = SECTIONS.find(sec => sec.id === id)!;
                return <NavItem key={id} s={s} active={activeSection === id} onClick={() => selectSection(id)} />;
              })}
            </div>

            <div className="border-t border-white/[0.05] my-2 mx-1" />

            {/* Macro group */}
            <div className="px-3 pb-1">
              <span className="text-[9px] font-bold uppercase tracking-widest text-base-content/30">Macro</span>
            </div>
            <div className="space-y-0.5">
              {(['liquidity', 'macro', 'cycles', 'debt'] as SectionId[]).map(id => {
                const s = SECTIONS.find(sec => sec.id === id)!;
                return <NavItem key={id} s={s} active={activeSection === id} onClick={() => selectSection(id)} />;
              })}
            </div>
          </nav>
        </div>

        {/* Content pane */}
        <div className="w-full lg:flex-1 min-w-0 space-y-8">
          {/* ============ MAJOR INDICES ============ */}
          {activeSection === 'indices' && (
            <section>
              <div className="flex items-center justify-between gap-2 mb-3">
                <div className="flex items-center gap-2">
                  <BarChart3 className="w-5 h-5 text-primary" />
                  <h2 className="text-lg font-semibold">Major Indices</h2>
                </div>
                <button
                  onClick={loadOverview}
                  className="btn btn-xs btn-ghost gap-1.5"
                  disabled={loadingOverview}
                >
                  <RefreshCw className={`w-3.5 h-3.5 ${loadingOverview ? 'animate-spin' : ''}`} />
                  Refresh
                </button>
              </div>

        {errorOverview && (
          <div className="alert alert-error rounded-2xl mb-3">
            <AlertCircle className="w-4 h-4" />
            <span className="text-sm">{errorOverview}</span>
            <button onClick={loadOverview} className="btn btn-xs btn-ghost gap-1">
              <RefreshCw className="w-3 h-3" /> Retry
            </button>
          </div>
        )}

        {loadingOverview ? (
          <div className="flex flex-col items-center justify-center gap-2 py-20 text-gray-400">
            <Loader2 className="w-6 h-6 animate-spin text-primary" />
            <span className="text-sm">Loading market indices and quotes…</span>
            <span className="text-xs text-base-content/25">This may take a moment to fetch fresh data</span>
          </div>
        ) : overview ? (
          <>
            <div className="space-y-6">
              {INDEX_GROUPS.map(group => {
                const items = overview.indices.filter(idx => idx.group === group.id);
                if (items.length === 0) return null;
                return (
                  <div key={group.id}>
                    <div className="flex items-center gap-2 mb-3">
                      <span className="text-base">{group.icon}</span>
                      <span className="text-xs font-bold uppercase tracking-widest text-base-content/45">{group.label}</span>
                      <div className="flex-1 h-px bg-white/[0.05]" />
                      <span className="text-[10px] text-base-content/25 tabular-nums">{items.length}</span>
                    </div>
                    <div className="grid gap-3" style={{ gridTemplateColumns: 'repeat(auto-fill,minmax(190px,1fr))' }}>
                      {items.map(idx => <IndexCard key={idx.symbol} idx={idx} onRefresh={() => handleRefreshIndex(idx.symbol)} />)}
                    </div>
                  </div>
                );
              })}
            </div>

            <div className="mt-4 rounded-2xl border border-primary/20 bg-primary/5 p-4">
              <div className="flex items-center justify-between gap-2 mb-2">
                <div className="flex items-center gap-2">
                  <Sparkles className="w-4 h-4 text-primary" />
                  <span className="text-xs font-bold uppercase tracking-wide text-primary">AI Market Briefing</span>
                </div>
                {!marketNarrative && (
                  <button
                    onClick={getMarketNarrative}
                    disabled={loadingNarrative}
                    className="btn btn-xs btn-primary gap-1"
                  >
                    {loadingNarrative
                      ? <><Loader2 className="w-3 h-3 animate-spin" /> Generating…</>
                      : <><Sparkles className="w-3 h-3" /> Get AI Briefing</>}
                  </button>
                )}
                {marketNarrative && (
                  <button onClick={getMarketNarrative} disabled={loadingNarrative} className="btn btn-xs btn-ghost gap-1">
                    <RefreshCw className={`w-3 h-3 ${loadingNarrative ? 'animate-spin' : ''}`} /> Refresh
                  </button>
                )}
              </div>
              {marketNarrative
                ? <p className="text-sm text-base-content/85 leading-relaxed whitespace-pre-wrap">{marketNarrative}</p>
                : <p className="text-xs text-base-content/40 italic">Click "Get AI Briefing" for a GPT-4o market analysis based on live index data.</p>
              }
            </div>
          </>
        ) : null}
            </section>
          )}

          {/* ============ SECTORS ============ */}
          {activeSection === 'sectors' && (
            <section>
        <div className="flex items-center justify-between mb-3 flex-wrap gap-2">
          <div className="flex items-center gap-2">
            <Waves className="w-5 h-5 text-primary" />
            <h2 className="text-lg font-semibold">Sector Rotation</h2>
            {sectors && (
              <div className="badge badge-outline gap-1 ml-2">
                <Gauge className="w-3 h-3" />
                Breadth {sectors.rotation.breadth_pct}%
              </div>
            )}
            {dataHealth && (
              <div className={`badge badge-outline gap-1 ${
                dataHealth.complete === dataHealth.total
                  ? 'border-success/40 text-success'
                  : 'border-warning/40 text-warning'
              }`}>
                {dataHealth.complete}/{dataHealth.total} full history
              </div>
            )}
          </div>
          <div className="flex items-center gap-2">
            <div className="flex gap-1 bg-base-200/50 rounded-xl p-1">
              {SECTOR_TIMEFRAMES.map(tf => (
                <button
                  key={tf.key}
                  onClick={() => setSectorTf(tf.key as string)}
                  className={`px-2.5 py-1 text-xs font-semibold rounded-lg transition-colors ${
                    sectorTf === tf.key
                      ? 'bg-primary text-primary-content'
                      : 'text-base-content/60 hover:text-base-content hover:bg-base-200'
                  }`}
                >
                  {tf.label}
                </button>
              ))}
            </div>
            <button
              onClick={() => loadSectors(sectorTf)}
              className="btn btn-xs btn-ghost gap-1.5"
              disabled={loadingSectors}
            >
              <RefreshCw className={`w-3.5 h-3.5 ${loadingSectors ? 'animate-spin' : ''}`} />
              Refresh
            </button>
          </div>
        </div>

        {errorSectors && (
          <div className="alert alert-error rounded-2xl mb-3">
            <AlertCircle className="w-4 h-4" />
            <span className="text-sm">{errorSectors}</span>
            <button onClick={() => loadSectors(sectorTf)} className="btn btn-xs btn-ghost gap-1">
              <RefreshCw className="w-3 h-3" /> Retry
            </button>
          </div>
        )}

        {loadingSectors ? (
          <div className="flex flex-col items-center justify-center gap-2 py-20 text-gray-400">
            <Loader2 className="w-6 h-6 animate-spin text-primary" />
            <span className="text-sm">Loading sector rotations and performance metrics…</span>
            <span className="text-xs text-base-content/25">Fetching live sector index calculations</span>
          </div>
        ) : sectors ? (
          <div className="space-y-5">
            {/* Rotation pills */}
            <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-3">
              <RotationGroup title="Leaders"      items={sectors.rotation.leaders}      icon={Flame}         tone="success" emptyLabel="No clear leaders" />
              <RotationGroup title="Laggards"     items={sectors.rotation.laggards}     icon={Snowflake}     tone="error"   emptyLabel="No clear laggards" />
              <RotationGroup title="Rotating In"  items={sectors.rotation.rotating_in}  icon={TrendingUp}    tone="info"    emptyLabel="No fresh money-in signals" />
              <RotationGroup title="Rotating Out" items={sectors.rotation.rotating_out} icon={TrendingDown}  tone="warning" emptyLabel="No distribution signals" />
            </div>

            {/* Ranked bar chart */}
            {sectorBarData && (
              <div className="rounded-2xl border border-white/[0.06] bg-base-100/60 p-4">
                <div className="text-xs font-semibold text-base-content/70 mb-2 uppercase tracking-wide">
                  {sectorTf.toUpperCase()} Ranking
                </div>
                <div className="h-80">
                  <Bar data={sectorBarData} options={sectorBarOpts} />
                </div>
              </div>
            )}

            {/* Full heatmap with expandable industries */}
            <div>
              <div className="flex items-center justify-between mb-2">
                <div className="text-xs font-semibold text-base-content/70 uppercase tracking-wide">
                  Multi-Timeframe Heatmap · click a sector to expand industries
                </div>
                <div className="flex gap-2">
                  <button
                    onClick={() => setExpandedSectors(new Set(sectors.sectors.map(s => s.symbol)))}
                    className="btn btn-xs btn-ghost"
                  >
                    Expand all
                  </button>
                  <button
                    onClick={() => setExpandedSectors(new Set())}
                    className="btn btn-xs btn-ghost"
                  >
                    Collapse all
                  </button>
                </div>
              </div>
              <SectorHeatmap
                sectors={sectors.sectors}
                expanded={expandedSectors}
                onToggle={toggleSector}
              />
            </div>

            {/* Quant analytics panel */}
            <div>
              <div className="flex items-center gap-2 mb-2">
                <Sigma className="w-4 h-4 text-primary" />
                <div className="text-xs font-semibold text-base-content/70 uppercase tracking-wide">
                  Quant Analytics — {sectorTf.toUpperCase()}
                </div>
              </div>
              <SectorQuantPanel
                sectors={sectors.sectors}
                summary={sectors.summary}
                timeframe={sectorTf}
              />
            </div>

            {/* Rotation intelligence narrative — on-demand only */}
            <div className="rounded-2xl border border-primary/20 bg-primary/5 p-4">
              <div className="flex items-center justify-between gap-2 mb-2">
                <div className="flex items-center gap-2">
                  <Sparkles className="w-4 h-4 text-primary" />
                  <span className="text-xs font-bold uppercase tracking-wide text-primary">
                    AI Rotation Intelligence — {sectorTf.toUpperCase()}
                  </span>
                </div>
                {!sectorIntelligence && (
                  <button
                    onClick={getSectorNarrative}
                    disabled={loadingSectorNarrative}
                    className="btn btn-xs btn-primary gap-1"
                  >
                    {loadingSectorNarrative
                      ? <><Loader2 className="w-3 h-3 animate-spin" /> Analyzing…</>
                      : <><Sparkles className="w-3 h-3" /> Get AI Analysis</>}
                  </button>
                )}
                {sectorIntelligence && (
                  <button onClick={getSectorNarrative} disabled={loadingSectorNarrative} className="btn btn-xs btn-ghost gap-1">
                    <RefreshCw className={`w-3 h-3 ${loadingSectorNarrative ? 'animate-spin' : ''}`} /> Refresh
                  </button>
                )}
              </div>
              {sectorIntelligence
                ? <p className="text-sm text-base-content/85 leading-relaxed whitespace-pre-wrap">{sectorIntelligence}</p>
                : <p className="text-xs text-base-content/40 italic">Click "Get AI Analysis" for GPT-4o sector rotation diagnosis based on live data.</p>
              }
            </div>

            {/* Chat panel */}
            <SectorChat timeframe={sectorTf} />
          </div>
        ) : null}
            </section>
          )}

          {/* ============ PERFORMANCE BY YEARS ============ */}
          {activeSection === 'performance' && (
            <div className="space-y-6">
              <div className="flex justify-end">
                <button
                  onClick={() => {
                    clearStyleCalendarCache();
                    setPerformanceRefreshKey(prev => prev + 1);
                  }}
                  className="btn btn-xs btn-ghost gap-1.5"
                >
                  <RefreshCw className="w-3.5 h-3.5" />
                  Refresh
                </button>
              </div>
              <section className="bg-gray-900/60 rounded-2xl border border-white/10 p-6 shadow-lg">
                <SectorCalendarHeatmap key={`sector-${performanceRefreshKey}`} />
              </section>
              <section className="bg-gray-900/60 rounded-2xl border border-white/10 p-6 shadow-lg">
                <StyleCalendarHeatmap key={`style-${performanceRefreshKey}`} />
              </section>
            </div>
          )}

          {/* ============ LIQUIDITY & FUNDING ============ */}
          {activeSection === 'liquidity' && (
            <section className="bg-gray-900/60 rounded-2xl border border-white/10 p-6 shadow-lg">
              <LiquidityPanel view="liquidity" />
            </section>
          )}

          {/* ============ MACRO REGIME ============ */}
          {activeSection === 'macro' && (
            <section className="bg-gray-900/60 rounded-2xl border border-white/10 p-6 shadow-lg">
              <LiquidityPanel view="regime" />
            </section>
          )}

          {/* ============ BUSINESS CYCLE ============ */}
          {activeSection === 'cycles' && (
            <section>
              <EconomicCycles />
            </section>
          )}

          {/* ============ DEBT RADAR ============ */}
          {activeSection === 'debt' && (
            <section>
              <DebtRadarPage isEmbedded={true} />
            </section>
          )}

          {/* Pick & Shovel research has moved to /ai-research?module=pickshv */}

          <div className="text-[10px] text-base-content/30 text-center pt-4">
            <ChevronRight className="w-3 h-3 inline" /> Market data via Yahoo Finance · AI via OpenAI · Not investment advice
          </div>
        </div>
      </div>
    </div>
  );
}
