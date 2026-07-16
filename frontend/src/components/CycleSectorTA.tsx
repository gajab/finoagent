import React, { useEffect, useRef, useState } from 'react';
import {
  Chart as ChartJS, CategoryScale, LinearScale, PointElement, LineElement,
  BarElement, Title, Tooltip, Legend, Filler,
} from 'chart.js';
import { Line, Bar } from 'react-chartjs-2';
import {
  Loader2, AlertCircle, RefreshCw, TrendingUp, TrendingDown,
  Minus, Star, Activity, ChevronDown, ChevronUp,
} from 'lucide-react';
import { fetchCycleSectorTA } from '../api';
import type { CycleSectorTaResponse, SectorTA, TaSignal } from '../types';

ChartJS.register(CategoryScale, LinearScale, PointElement, LineElement, BarElement, Title, Tooltip, Legend, Filler);

// ---------------------------------------------------------------------------
// Small helpers
// ---------------------------------------------------------------------------

const fmtPct = (v: number | null | undefined) => {
  if (v == null) return '—';
  return `${v > 0 ? '+' : ''}${v.toFixed(2)}%`;
};

const fmtPrice = (v: number | null | undefined) =>
  v == null ? '—' : `$${v.toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`;

const retColor = (v: number | null | undefined) => {
  if (v == null) return 'text-base-content/40';
  return v > 0 ? 'text-success' : 'text-error';
};

// ---------------------------------------------------------------------------
// Overall signal badge
// ---------------------------------------------------------------------------

function OverallBadge({ signal }: { signal: SectorTA['overall'] }) {
  const map = {
    bullish: { label: 'Bullish',  cls: 'bg-success/15 text-success border-success/30',  Icon: TrendingUp   },
    neutral: { label: 'Neutral',  cls: 'bg-base-content/5 text-base-content/50 border-white/10', Icon: Minus },
    bearish: { label: 'Bearish',  cls: 'bg-error/15 text-error border-error/30',          Icon: TrendingDown },
  };
  const { label, cls, Icon } = map[signal] ?? map.neutral;
  return (
    <span className={`flex items-center gap-1 text-xs font-bold px-2 py-0.5 rounded-full border ${cls}`}>
      <Icon className="w-3 h-3" />
      {label}
    </span>
  );
}

// ---------------------------------------------------------------------------
// Signal pill
// ---------------------------------------------------------------------------

function SignalPill({ s }: { s: TaSignal }) {
  const color =
    s.type === 'bullish' ? 'text-success bg-success/10 border-success/25' :
    s.type === 'bearish' ? 'text-error bg-error/10 border-error/25' :
    'text-base-content/50 bg-white/[0.04] border-white/10';
  return (
    <div className={`flex items-center gap-1.5 px-2 py-1 rounded-lg border text-[10px] font-medium ${color}`}>
      <span className="w-1.5 h-1.5 rounded-full shrink-0 opacity-80"
        style={{ background: s.type === 'bullish' ? '#22c55e' : s.type === 'bearish' ? '#ef4444' : '#888' }} />
      {s.text}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Stars
// ---------------------------------------------------------------------------

function Stars({ n }: { n: number }) {
  return (
    <span className="flex gap-0.5">
      {[1,2,3,4,5].map(i => (
        <Star key={i} className="w-3 h-3" fill={i <= n ? 'currentColor' : 'none'} strokeWidth={1.5}
          style={{ color: i <= n ? '#f59e0b' : 'rgba(255,255,255,0.18)' }} />
      ))}
    </span>
  );
}

// ---------------------------------------------------------------------------
// RSI gauge (horizontal bar with zone coloring)
// ---------------------------------------------------------------------------

function RsiGauge({ rsi }: { rsi: number | null }) {
  if (rsi == null) return <span className="text-xs text-base-content/30">—</span>;
  const zone =
    rsi >= 70 ? { label: 'Overbought', color: '#f59e0b' } :
    rsi >= 50 ? { label: 'Bullish',    color: '#22c55e' } :
    rsi >= 30 ? { label: 'Bearish',    color: '#ef4444' } :
                { label: 'Oversold',   color: '#a855f7' };

  return (
    <div className="space-y-1">
      <div className="flex justify-between text-[10px] text-base-content/40">
        <span>Oversold</span><span>Neutral</span><span>Overbought</span>
      </div>
      <div className="relative h-2 bg-white/[0.06] rounded-full overflow-hidden">
        {/* Zone bands */}
        <div className="absolute inset-y-0 left-0 w-[30%] bg-purple-500/20 rounded-l-full" />
        <div className="absolute inset-y-0 left-[30%] w-[20%] bg-red-500/20" />
        <div className="absolute inset-y-0 left-[50%] w-[20%] bg-green-500/20" />
        <div className="absolute inset-y-0 left-[70%] w-[30%] bg-amber-500/20 rounded-r-full" />
        {/* Needle */}
        <div className="absolute top-0 h-full w-0.5 rounded-full -translate-x-1/2"
          style={{ left: `${rsi}%`, background: zone.color }} />
      </div>
      <div className="flex items-center justify-between">
        <span className="text-[10px] text-base-content/40">30</span>
        <span className="text-sm font-bold tabular-nums" style={{ color: zone.color }}>
          {rsi.toFixed(1)}
          <span className="text-[10px] font-normal ml-1 opacity-70">{zone.label}</span>
        </span>
        <span className="text-[10px] text-base-content/40">70</span>
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Price chart (90-day line + SMA overlays)
// ---------------------------------------------------------------------------

const CHART_OPTS = {
  responsive: true,
  maintainAspectRatio: false,
  animation: false as const,
  interaction: { mode: 'index' as const, intersect: false },
  plugins: {
    legend: { display: false },
    tooltip: {
      backgroundColor: 'rgba(15,17,21,0.92)',
      titleColor: 'rgba(255,255,255,0.6)',
      bodyColor: 'rgba(255,255,255,0.85)',
      padding: 8,
      callbacks: {
        label: (ctx: any) => `${ctx.dataset.label}: $${ctx.parsed.y?.toFixed(2) ?? '—'}`,
      },
    },
  },
  scales: {
    x: {
      ticks: { maxTicksLimit: 6, color: 'rgba(255,255,255,0.25)', font: { size: 9 } },
      grid:  { color: 'rgba(255,255,255,0.04)' },
    },
    y: {
      position: 'right' as const,
      ticks: { color: 'rgba(255,255,255,0.25)', font: { size: 9 } },
      grid:  { color: 'rgba(255,255,255,0.04)' },
    },
  },
};

const RSI_OPTS = {
  responsive: true,
  maintainAspectRatio: false,
  animation: false as const,
  plugins: { legend: { display: false }, tooltip: { enabled: false } },
  scales: {
    x: { display: false },
    y: {
      min: 0, max: 100,
      ticks: { values: [30, 50, 70], color: 'rgba(255,255,255,0.2)', font: { size: 8 } },
      grid:  { color: (ctx: any) => [30, 50, 70].includes(ctx.tick.value) ? 'rgba(255,255,255,0.08)' : 'transparent' },
      position: 'right' as const,
    },
  },
};

const MACD_OPTS = {
  responsive: true,
  maintainAspectRatio: false,
  animation: false as const,
  plugins: { legend: { display: false }, tooltip: { enabled: false } },
  scales: {
    x: { display: false },
    y: {
      ticks: { color: 'rgba(255,255,255,0.2)', font: { size: 8 } },
      grid:  { color: 'rgba(255,255,255,0.04)' },
      position: 'right' as const,
    },
  },
};

function PriceChart({ sector }: { sector: SectorTA }) {
  const { chart, phase_color } = { ...sector, phase_color: '#3b82f6' };
  const c = sector.chart;

  const labels = c.dates.map(d => {
    const [, m, day] = d.split('-');
    return `${m}/${day}`;
  });

  const priceData = {
    labels,
    datasets: [
      {
        label: 'Price',
        data: c.price,
        borderColor: '#3b82f6',
        backgroundColor: 'rgba(59,130,246,0.06)',
        borderWidth: 1.5,
        pointRadius: 0,
        fill: true,
        tension: 0.2,
        order: 1,
      },
      {
        label: 'SMA 20',
        data: c.sma20,
        borderColor: 'rgba(251,191,36,0.7)',
        borderWidth: 1,
        borderDash: [3, 2],
        pointRadius: 0,
        fill: false,
        order: 2,
      },
      {
        label: 'SMA 50',
        data: c.sma50,
        borderColor: 'rgba(168,85,247,0.7)',
        borderWidth: 1,
        borderDash: [4, 3],
        pointRadius: 0,
        fill: false,
        order: 3,
      },
      ...(c.sma200_ref != null ? [{
        label: 'SMA 200',
        data: c.dates.map(() => c.sma200_ref),
        borderColor: 'rgba(239,68,68,0.5)',
        borderWidth: 1,
        borderDash: [6, 4],
        pointRadius: 0,
        fill: false,
        order: 4,
      }] : []),
    ],
  };

  const rsiData = {
    labels,
    datasets: [{
      data: c.rsi,
      borderColor: (ctx: any) => {
        const v = ctx.raw;
        return v >= 70 ? '#f59e0b' : v <= 30 ? '#a855f7' : v >= 50 ? '#22c55e' : '#ef4444';
      },
      segment: {
        borderColor: (ctx: any) => {
          const v = ctx.p1.parsed.y;
          return v >= 70 ? '#f59e0b' : v <= 30 ? '#a855f7' : v >= 50 ? '#22c55e' : '#ef4444';
        },
      },
      borderWidth: 1.5,
      pointRadius: 0,
      fill: false,
    }],
  };

  const macdData = {
    labels,
    datasets: [
      {
        type: 'bar' as const,
        label: 'Histogram',
        data: c.macd_hist,
        backgroundColor: c.macd_hist.map(v => (v ?? 0) >= 0 ? 'rgba(34,197,94,0.5)' : 'rgba(239,68,68,0.5)'),
        borderWidth: 0,
        order: 2,
      },
      {
        type: 'line' as const,
        label: 'MACD',
        data: c.macd,
        borderColor: '#3b82f6',
        borderWidth: 1,
        pointRadius: 0,
        fill: false,
        order: 1,
      },
      {
        type: 'line' as const,
        label: 'Signal',
        data: c.macd_signal,
        borderColor: '#f59e0b',
        borderWidth: 1,
        borderDash: [3, 2],
        pointRadius: 0,
        fill: false,
        order: 1,
      },
    ],
  };

  return (
    <div className="space-y-2">
      {/* Price chart */}
      <div>
        <div className="flex items-center gap-3 mb-1 text-[9px] text-base-content/35">
          <span className="flex items-center gap-1"><span className="inline-block w-4 h-px bg-blue-400" /> Price</span>
          <span className="flex items-center gap-1"><span className="inline-block w-4 h-px bg-yellow-400/70" style={{borderTop:'1px dashed'}} /> SMA20</span>
          <span className="flex items-center gap-1"><span className="inline-block w-4 h-px bg-purple-500/70" style={{borderTop:'1px dashed'}} /> SMA50</span>
          {c.sma200_ref && <span className="flex items-center gap-1"><span className="inline-block w-4 h-px bg-red-500/50" style={{borderTop:'1px dashed'}} /> SMA200</span>}
        </div>
        <div className="h-36">
          <Line data={priceData} options={CHART_OPTS} />
        </div>
      </div>

      {/* RSI */}
      <div>
        <div className="text-[9px] text-base-content/30 mb-0.5 uppercase tracking-widest">RSI (14)</div>
        <div className="h-16">
          <Line data={rsiData} options={RSI_OPTS} />
        </div>
      </div>

      {/* MACD */}
      <div>
        <div className="flex items-center gap-3 text-[9px] text-base-content/30 mb-0.5">
          <span className="uppercase tracking-widest">MACD (12/26/9)</span>
          <span className="flex items-center gap-1"><span className="w-2 h-2 rounded-sm bg-success/50 inline-block" /> Hist</span>
          <span className="text-blue-400">MACD</span>
          <span className="text-yellow-400">Signal</span>
        </div>
        <div className="h-16">
          <Bar data={macdData as any} options={MACD_OPTS} />
        </div>
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Single sector TA card
// ---------------------------------------------------------------------------

function SectorCard({ sector, phaseColor }: { sector: SectorTA; phaseColor: string }) {
  const [showChart, setShowChart] = useState(false);

  const pricePctFrom200 = sector.vs_sma200_pct;
  const sma200Txt = pricePctFrom200 != null
    ? `${pricePctFrom200 > 0 ? '+' : ''}${pricePctFrom200.toFixed(1)}% vs SMA200`
    : null;

  return (
    <div className="rounded-2xl border border-white/[0.07] bg-base-100/50 p-4 flex flex-col gap-3">

      {/* ── Header ── */}
      <div className="flex items-start justify-between gap-2">
        <div>
          <div className="flex items-center gap-2 flex-wrap">
            <span className="text-base font-bold font-mono">{sector.ticker}</span>
            <Stars n={sector.stars} />
          </div>
          <div className="text-xs text-base-content/60 mt-0.5">{sector.category}</div>
          <div className="text-[10px] text-base-content/40 italic mt-0.5">{sector.note}</div>
        </div>
        <OverallBadge signal={sector.overall} />
      </div>

      {/* ── Price + returns ── */}
      <div className="rounded-xl bg-white/[0.03] border border-white/[0.05] p-3">
        <div className="flex items-baseline gap-3 flex-wrap">
          <span className="text-xl font-bold tabular-nums">{fmtPrice(sector.price)}</span>
          {sma200Txt && (
            <span className={`text-[10px] ${(pricePctFrom200 ?? 0) >= 0 ? 'text-success/70' : 'text-error/70'}`}>
              {sma200Txt}
            </span>
          )}
        </div>
        <div className="grid grid-cols-4 gap-2 mt-2">
          {[
            { label: '1M',  val: sector.pct_1m  },
            { label: '3M',  val: sector.pct_3m  },
            { label: '6M',  val: sector.pct_6m  },
            { label: 'YTD', val: sector.pct_ytd },
          ].map(({ label, val }) => (
            <div key={label} className="text-center">
              <div className="text-[9px] text-base-content/30 uppercase">{label}</div>
              <div className={`text-xs font-semibold tabular-nums ${retColor(val)}`}>{fmtPct(val)}</div>
            </div>
          ))}
        </div>
      </div>

      {/* ── RSI ── */}
      <div className="rounded-xl bg-white/[0.03] border border-white/[0.05] p-3 space-y-1">
        <div className="text-[10px] font-bold uppercase tracking-widest text-base-content/30">RSI (14)</div>
        <RsiGauge rsi={sector.rsi} />
      </div>

      {/* ── MACD indicator ── */}
      <div className="rounded-xl bg-white/[0.03] border border-white/[0.05] p-3">
        <div className="text-[10px] font-bold uppercase tracking-widest text-base-content/30 mb-1.5">MACD</div>
        <div className="flex items-center gap-3 text-xs">
          {sector.macd_bullish != null && (
            <span className={`flex items-center gap-1 font-medium ${sector.macd_bullish ? 'text-success' : 'text-error'}`}>
              {sector.macd_bullish ? <TrendingUp className="w-3.5 h-3.5" /> : <TrendingDown className="w-3.5 h-3.5" />}
              {sector.macd_bullish ? 'Above signal' : 'Below signal'}
            </span>
          )}
          {sector.macd_histogram != null && (
            <span className={`text-[10px] ${sector.macd_histogram >= 0 ? 'text-success/60' : 'text-error/60'}`}>
              hist: {sector.macd_histogram > 0 ? '+' : ''}{sector.macd_histogram.toFixed(4)}
            </span>
          )}
        </div>
      </div>

      {/* ── Signals ── */}
      {sector.signals.length > 0 && (
        <div className="space-y-1.5">
          <div className="text-[10px] font-bold uppercase tracking-widest text-base-content/30">Signals</div>
          <div className="space-y-1">
            {sector.signals.map((s, i) => <SignalPill key={i} s={s} />)}
          </div>
        </div>
      )}

      {/* ── Key levels ── */}
      {(sector.hi52 || sector.lo52 || sector.sma50 || sector.sma200) && (
        <div className="grid grid-cols-2 gap-1.5 text-[10px]">
          {sector.sma50  && <div className="rounded-lg bg-purple-500/8 border border-purple-500/15 px-2 py-1">
            <span className="text-base-content/35">SMA50 </span>
            <span className="font-mono font-semibold">${sector.sma50.toFixed(2)}</span>
          </div>}
          {sector.sma200 && <div className="rounded-lg bg-red-500/8 border border-red-500/15 px-2 py-1">
            <span className="text-base-content/35">SMA200 </span>
            <span className="font-mono font-semibold">${sector.sma200.toFixed(2)}</span>
          </div>}
          {sector.hi52   && <div className="rounded-lg bg-success/5 border border-success/15 px-2 py-1">
            <span className="text-base-content/35">52W Hi </span>
            <span className="font-mono font-semibold">${sector.hi52.toFixed(2)}</span>
          </div>}
          {sector.lo52   && <div className="rounded-lg bg-error/5 border border-error/15 px-2 py-1">
            <span className="text-base-content/35">52W Lo </span>
            <span className="font-mono font-semibold">${sector.lo52.toFixed(2)}</span>
          </div>}
        </div>
      )}

      {/* ── Chart toggle ── */}
      <button
        onClick={() => setShowChart(x => !x)}
        className="flex items-center justify-center gap-1.5 text-xs text-base-content/40
          hover:text-base-content/70 transition-colors border border-white/[0.06]
          rounded-xl py-2 hover:bg-white/[0.02]"
      >
        <Activity className="w-3.5 h-3.5" />
        {showChart ? 'Hide chart' : 'Show 90-day chart'}
        {showChart ? <ChevronUp className="w-3 h-3" /> : <ChevronDown className="w-3 h-3" />}
      </button>

      {showChart && (
        <div className="rounded-xl bg-black/20 border border-white/[0.05] p-3">
          <PriceChart sector={sector} />
        </div>
      )}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Main exported component
// ---------------------------------------------------------------------------

export default function CycleSectorTA({ usPhase }: { usPhase: string }) {
  const [data, setData]       = useState<CycleSectorTaResponse | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError]     = useState<string | null>(null);

  const load = async () => {
    setLoading(true); setError(null);
    try   { setData(await fetchCycleSectorTA(usPhase)); }
    catch (e: any) { setError(e?.message || 'Failed to load sector TA'); }
    finally { setLoading(false); }
  };

  useEffect(() => { load(); }, [usPhase]);

  return (
    <div className="space-y-4">
      {/* Header */}
      <div className="flex items-center justify-between">
        <div>
          <h3 className="text-sm font-semibold flex items-center gap-2">
            🇺🇸 US Cycle Sector Opportunity
            {data && (
              <span className="text-xs font-normal text-base-content/40">
                Top sectors in <span className="font-semibold" style={{ color: data.phase_color }}>{data.phase_label}</span> phase
              </span>
            )}
          </h3>
          <p className="text-[11px] text-base-content/35 mt-0.5">
            Technical analysis for the 3 strongest-performing sectors in the current US cycle phase
          </p>
        </div>
        <button onClick={load} disabled={loading} className="btn btn-xs btn-ghost gap-1">
          <RefreshCw className={`w-3.5 h-3.5 ${loading ? 'animate-spin' : ''}`} />
          Refresh
        </button>
      </div>

      {error && (
        <div className="alert alert-error rounded-xl text-sm py-2">
          <AlertCircle className="w-4 h-4" />
          {error}
          <button onClick={load} className="btn btn-xs btn-ghost gap-1"><RefreshCw className="w-3 h-3" /> Retry</button>
        </div>
      )}

      {loading && !data && (
        <div className="flex items-center justify-center py-10 gap-2 text-base-content/40">
          <Loader2 className="w-5 h-5 animate-spin" />
          <span className="text-sm">Fetching sector TA via yfinance…</span>
        </div>
      )}

      {data && (
        <div className="grid grid-cols-1 md:grid-cols-3 gap-4">
          {data.sectors.map(s => (
            <SectorCard key={s.ticker} sector={s} phaseColor={data.phase_color} />
          ))}
        </div>
      )}

      {data && (
        <p className="text-[10px] text-base-content/25 text-right">
          TA as of {data.as_of} · Data via Yahoo Finance · Not investment advice
        </p>
      )}
    </div>
  );
}
