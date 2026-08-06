import React, { useCallback, useEffect, useMemo, useState } from 'react';
import {
  GitBranch, Waves, ChevronDown, ChevronUp, Loader2, RefreshCw, Eye,
  TrendingUp, TrendingDown, Minus,
} from 'lucide-react';
import {
  Chart as ChartJS, CategoryScale, LinearScale, PointElement, LineElement,
  Filler, Tooltip, type ChartOptions,
} from 'chart.js';
import { Line } from 'react-chartjs-2';
import { fetchMarketStructure, analyzeTa } from '../api';
import type { MarketStructureData, TimeframeBlock } from '../types';
import IndicatorAIConsole from './IndicatorAIConsole';

ChartJS.register(CategoryScale, LinearScale, PointElement, LineElement, Filler, Tooltip);

// ── selectable layers (draw on chart + serialize to the AI JSON) ──
interface MSLine { price: number; label: string; dash?: number[] }
interface MSBand { top: number; bottom: number; label: string; color: string }
interface MSLayer {
  id: string; group: string; label: string; sub?: string;
  tone: string; color: string;
  lines?: MSLine[]; bands?: MSBand[];
  json: Record<string, unknown>;
}

const GREEN = 'rgb(34,197,94)';
const RED = 'rgb(239,68,68)';
const toFill = (rgb: string, a: number) => rgb.replace('rgb(', 'rgba(').replace(')', `,${a})`);

const TF_DEFS = [
  ['daily', 'rgb(139,92,246)', 'text-violet-400', 'D'],
  ['h4', 'rgb(56,189,248)', 'text-sky-400', '4H'],
  ['h1', 'rgb(251,191,36)', 'text-amber-400', '1H'],
] as const;

function buildLayers(d: MarketStructureData): MSLayer[] {
  const out: MSLayer[] = [];
  for (const [key, color, tone, tag] of TF_DEFS) {
    const tf = d.timeframes[key] as TimeframeBlock | null;
    if (!tf) continue;
    const g = `${tf.label} structure`;
    const st = tf.structure;

    const lines: MSLine[] = [];
    if (st.last_event) lines.push({ price: st.last_event.level, label: `${tag} ${st.last_event.type} ${st.last_event.direction === 'bullish' ? '▲' : '▼'}` });
    if (st.recent_swing_high != null) lines.push({ price: st.recent_swing_high, label: `${tag} swing H`, dash: [2, 2] });
    if (st.recent_swing_low != null) lines.push({ price: st.recent_swing_low, label: `${tag} swing L`, dash: [2, 2] });
    if (lines.length) out.push({
      id: `${key}_struct`, group: g, tone, color,
      label: `Structure — ${st.last_event ? `${st.last_event.type} ${st.last_event.direction}` : tf.trend}`,
      sub: st.last_event ? `last ${st.last_event.type} @ $${st.last_event.level}` : `trend ${tf.trend}`,
      lines, json: { indicator: 'market_structure', timeframe: key, trend: tf.trend, last_event: st.last_event, recent_swing_high: st.recent_swing_high, recent_swing_low: st.recent_swing_low, events: st.events },
    });

    const obs = (tf.order_blocks || []).filter(o => !o.mitigated);
    if (obs.length) out.push({
      id: `${key}_ob`, group: g, tone, color, label: `Order Blocks (${obs.length})`, sub: 'unmitigated demand/supply',
      bands: obs.map(o => ({ top: o.top, bottom: o.bottom, label: `${tag} ${o.type === 'bullish' ? 'Demand' : 'Supply'} OB`, color: o.type === 'bullish' ? GREEN : RED })),
      json: { indicator: 'order_blocks', timeframe: key, blocks: obs },
    });

    const fvgs = (tf.fair_value_gaps || []).filter(f => !f.filled);
    if (fvgs.length) out.push({
      id: `${key}_fvg`, group: g, tone, color, label: `Fair-Value Gaps (${fvgs.length})`, sub: 'unmitigated imbalances',
      bands: fvgs.map(f => ({ top: f.top, bottom: f.bottom, label: `${tag} ${f.type === 'bullish' ? 'Bull' : 'Bear'} FVG`, color: f.type === 'bullish' ? GREEN : RED })),
      json: { indicator: 'fair_value_gaps', timeframe: key, gaps: fvgs },
    });

    const pools = tf.liquidity_pools || [];
    if (pools.length) out.push({
      id: `${key}_pools`, group: g, tone, color, label: `Liquidity Pools (${pools.length})`, sub: 'unswept BSL / SSL',
      lines: pools.map(p => ({ price: p.price, label: `${p.type} $${p.price}${p.strength === 'strong' ? ' ★' : ''}`, dash: [6, 3] })),
      json: { indicator: 'liquidity_pools', timeframe: key, pools },
    });
  }

  const cf = d.confluence || [];
  if (cf.length) out.push({
    id: 'confluence', group: 'Confluence', tone: 'text-emerald-400', color: 'rgb(16,185,129)',
    label: `MTF Confluence (${cf.length})`, sub: 'stacked unmitigated zones',
    bands: cf.map(c => ({ top: c.zone[1], bottom: c.zone[0], label: `${c.bias === 'bullish' ? 'Bull' : 'Bear'} confluence`, color: c.bias === 'bullish' ? 'rgb(16,185,129)' : 'rgb(244,63,94)' })),
    json: { indicator: 'mtf_confluence', zones: cf },
  });
  return out;
}

function makeMsPlugin(layers: MSLayer[]): any {
  return {
    id: 'msLevels',
    afterDatasetsDraw(chart: any) {
      const { ctx, chartArea, scales } = chart;
      const y = scales?.y;
      if (!y || !chartArea) return;
      const L = chartArea.left, R = chartArea.right;
      const inRange = (p: number) => p >= y.min && p <= y.max;
      const py = (p: number) => Math.max(chartArea.top, Math.min(chartArea.bottom, y.getPixelForValue(p)));
      ctx.save();
      ctx.beginPath(); ctx.rect(L, chartArea.top, R - L, chartArea.bottom - chartArea.top); ctx.clip();

      const hline = (p: number, color: string, dash: number[] | undefined, label: string) => {
        if (!inRange(p)) return;
        const yy = py(p);
        ctx.strokeStyle = color; ctx.lineWidth = 1.25; ctx.setLineDash(dash || []);
        ctx.beginPath(); ctx.moveTo(L, yy); ctx.lineTo(R, yy); ctx.stroke(); ctx.setLineDash([]);
        ctx.font = '9px sans-serif'; ctx.fillStyle = color; ctx.textAlign = 'right'; ctx.textBaseline = 'bottom';
        ctx.fillText(label, R - 3, yy - 1);
      };
      const band = (a: number, b: number, color: string, label: string) => {
        if (!inRange(a) && !inRange(b)) return;
        const yt = py(Math.max(a, b)), yb = py(Math.min(a, b));
        ctx.fillStyle = toFill(color, 0.12); ctx.fillRect(L, yt, R - L, Math.max(2, yb - yt));
        ctx.font = '9px sans-serif'; ctx.fillStyle = color; ctx.textAlign = 'left'; ctx.textBaseline = 'top';
        ctx.fillText(label, L + 3, yt + 1);
      };
      for (const l of layers) {
        (l.bands || []).forEach(b => band(b.top, b.bottom, b.color, b.label));
        (l.lines || []).forEach(ln => hline(ln.price, l.color, ln.dash, ln.label));
      }
      ctx.restore();
    },
  };
}

function BiasBanner({ d }: { d: MarketStructureData }) {
  const b = d.bias;
  const Icon = b.overall === 'bullish' ? TrendingUp : b.overall === 'bearish' ? TrendingDown : Minus;
  const tone = b.overall === 'bullish' ? 'text-success border-success/30 bg-success/10'
    : b.overall === 'bearish' ? 'text-error border-error/30 bg-error/10'
      : 'text-warning border-warning/30 bg-warning/10';
  const trendIco = (t?: string) => t === 'up' ? '▲' : t === 'down' ? '▼' : '◦';
  return (
    <div className={`rounded-lg border p-2.5 ${tone}`}>
      <div className="flex items-center justify-between flex-wrap gap-2">
        <div className="flex items-center gap-2">
          <Icon className="w-4 h-4 shrink-0" />
          <div>
            <p className="text-sm font-bold leading-tight capitalize">{b.overall} structure {b.aligned && <span className="badge badge-xs badge-success ml-1 align-middle">aligned</span>}</p>
            <p className="text-[11px] opacity-70">{b.note}</p>
          </div>
        </div>
        <div className="flex gap-2 text-[11px]">
          {(['daily', 'h4', 'h1'] as const).map(k => {
            const tf = d.timeframes[k];
            const lbl = k === 'daily' ? 'D' : k === 'h4' ? '4H' : '1H';
            return <span key={k} className="badge badge-sm bg-base-100/50 border-base-content/10 tabular-nums">{lbl} {tf ? trendIco(tf.trend) : '—'}</span>;
          })}
        </div>
      </div>
    </div>
  );
}

export default function MarketStructurePanel({ ticker }: { ticker: string; price?: number }) {
  const [expanded, setExpanded] = useState(false);
  const [data, setData] = useState<MarketStructureData | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [selected, setSelected] = useState<Set<string>>(new Set());

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const r = await fetchMarketStructure(ticker);
      const md = r.market_structure;
      setData(md);
      const def = new Set<string>();
      if (md.timeframes.daily) { def.add('daily_struct'); if (md.timeframes.daily.liquidity_pools?.length) def.add('daily_pools'); }
      if (md.confluence?.length) def.add('confluence');
      setSelected(def);
    } catch (e: any) {
      setError(e?.message || 'Failed to load market structure');
    } finally {
      setLoading(false);
    }
  }, [ticker]);

  useEffect(() => { setData(null); setSelected(new Set()); setError(null); }, [ticker]);
  useEffect(() => { if (expanded && !data && !loading && !error) load(); }, [expanded, data, loading, error, load]);

  const layers = useMemo(() => (data ? buildLayers(data) : []), [data]);
  const selectedLayers = useMemo(() => layers.filter(l => selected.has(l.id)), [layers, selected]);
  const groups = useMemo(() => {
    const m = new Map<string, MSLayer[]>();
    for (const l of layers) { if (!m.has(l.group)) m.set(l.group, []); m.get(l.group)!.push(l); }
    return Array.from(m.entries());
  }, [layers]);

  const selectionJson = useMemo(() => ({
    ticker,
    spot: data?.price ?? null,
    as_of: data?.as_of,
    mtf_bias: data?.bias,
    selected_indicators: selectedLayers.map(l => l.json),
  }), [ticker, data, selectedLayers]);

  const toggle = (id: string) => setSelected(prev => { const n = new Set(prev); n.has(id) ? n.delete(id) : n.add(id); return n; });
  const setGroup = (gLayers: MSLayer[], on: boolean) => setSelected(prev => { const n = new Set(prev); gLayers.forEach(l => on ? n.add(l.id) : n.delete(l.id)); return n; });

  const chartData = useMemo(() => {
    const s = data?.price_series;
    if (!s?.closes?.length) return null;
    const step = Math.max(1, Math.floor(s.timestamps.length / 12));
    return {
      labels: s.timestamps.map((t, i) => (i % step === 0 ? t.slice(5) : '')),
      datasets: [{ label: 'Price', data: s.closes, borderColor: 'rgb(148,163,184)', backgroundColor: 'rgba(148,163,184,0.08)', fill: true, borderWidth: 1.25, pointRadius: 0, tension: 0.1 }],
    };
  }, [data]);
  const chartOptions: ChartOptions<'line'> = useMemo(() => ({
    responsive: true, maintainAspectRatio: false, animation: false as const, interaction: { mode: 'index', intersect: false },
    plugins: { legend: { display: false }, tooltip: { callbacks: { label: (c) => `$${Number(c.parsed.y).toFixed(2)}` } } },
    scales: {
      x: { ticks: { color: '#999', maxRotation: 0, autoSkip: true, maxTicksLimit: 10 }, grid: { display: false } },
      y: { position: 'right', ticks: { color: '#999', callback: (v) => `$${Number(v).toFixed(0)}` }, grid: { color: 'rgba(128,128,128,0.12)' } },
    },
  }), []);
  const msPlugin = useMemo(() => makeMsPlugin(selectedLayers), [selectedLayers]);

  return (
    <div className="bg-base-300 rounded-xl overflow-hidden shadow-xl border border-white/[0.05]">
      <button type="button" onClick={() => setExpanded(o => !o)}
        className="w-full flex items-center justify-between px-4 py-3 bg-gradient-to-r from-base-200/60 to-transparent">
        <div className="flex items-center gap-2 text-left">
          <GitBranch className="w-4 h-4 text-secondary" />
          <div>
            <h4 className="text-sm font-semibold text-base-content/90">Market Structure &amp; Liquidity</h4>
            <p className="text-[10px] text-base-content/50">BOS / CHOCH across Daily · 4H · 1H, liquidity pools (BSL/SSL) &amp; multi-timeframe confluence → AI trade read</p>
          </div>
        </div>
        {expanded ? <ChevronUp className="w-4 h-4 shrink-0" /> : <ChevronDown className="w-4 h-4 shrink-0" />}
      </button>

      {expanded && (
        <div className="p-3 border-t border-white/[0.05] space-y-3">
          {loading && (
            <div className="flex items-center gap-2 text-sm text-base-content/60 py-6 justify-center">
              <Loader2 className="w-4 h-4 animate-spin" /> Mapping structure across timeframes…
            </div>
          )}
          {error && !loading && (
            <div className="alert alert-error text-xs flex items-center justify-between">
              <span>{error}</span>
              <button className="btn btn-ghost btn-xs" onClick={load}><RefreshCw className="w-3.5 h-3.5" /> Retry</button>
            </div>
          )}

          {data && !loading && (
            <>
              <BiasBanner d={data} />
              <div className="grid grid-cols-1 lg:grid-cols-5 gap-3">
                {/* tray */}
                <div className="lg:col-span-2 space-y-2">
                  <div className="flex items-center gap-1.5 text-[11px] text-base-content/50 uppercase tracking-wide">
                    <Eye className="w-3.5 h-3.5" /> Indicators — shown on chart &amp; sent to AI
                  </div>
                  {groups.map(([g, gLayers]) => {
                    const allOn = gLayers.every(l => selected.has(l.id));
                    return (
                      <div key={g} className="rounded-lg border border-white/[0.06] bg-base-200/30 p-2">
                        <div className="flex items-center justify-between mb-1">
                          <span className="text-[11px] font-bold text-base-content/70">{g}</span>
                          <button className="text-[10px] text-primary hover:underline" onClick={() => setGroup(gLayers, !allOn)}>{allOn ? 'clear' : 'all'}</button>
                        </div>
                        <div className="space-y-0.5">
                          {gLayers.map(l => (
                            <label key={l.id} className="flex items-start gap-2 cursor-pointer py-0.5 hover:bg-base-100/30 rounded px-1">
                              <input type="checkbox" className="checkbox checkbox-xs mt-0.5" checked={selected.has(l.id)} onChange={() => toggle(l.id)} />
                              <span className="min-w-0 flex-1">
                                <span className={`text-[11px] font-semibold ${l.tone}`}>{l.label}</span>
                                {l.sub && <span className="block text-[10px] text-base-content/40 leading-tight truncate">{l.sub}</span>}
                              </span>
                            </label>
                          ))}
                        </div>
                      </div>
                    );
                  })}
                  {data.confluence.length > 0 && (
                    <div className="rounded-lg border border-white/[0.06] bg-base-200/20 p-2 space-y-1">
                      <span className="text-[10px] font-bold text-base-content/50 uppercase">Confluence read</span>
                      {data.confluence.slice(0, 3).map((c, i) => (
                        <p key={i} className={`text-[10px] leading-snug ${c.bias === 'bullish' ? 'text-success/80' : 'text-error/80'}`}>{c.summary}</p>
                      ))}
                    </div>
                  )}
                </div>

                {/* chart + AI */}
                <div className="lg:col-span-3 space-y-3">
                  <div className="rounded-lg border border-white/[0.06] bg-base-200/20 p-2">
                    <div className="flex items-center justify-between mb-1 px-1">
                      <span className="text-[11px] font-bold text-base-content/70 flex items-center gap-1"><Waves className="w-3.5 h-3.5 text-secondary" /> Structure map — daily</span>
                      <span className="text-[10px] text-base-content/40">{selectedLayers.length} layer{selectedLayers.length === 1 ? '' : 's'} shown</span>
                    </div>
                    {chartData ? (
                      <div className="h-64"><Line key={[...selected].sort().join('|')} data={chartData} options={chartOptions} plugins={[msPlugin]} /></div>
                    ) : (
                      <div className="text-center text-xs text-base-content/40 py-10">No price series available.</div>
                    )}
                  </div>

                  <IndicatorAIConsole
                    selectionJson={selectionJson}
                    chips={selectedLayers.map(l => ({ key: l.id, label: l.label, tone: l.tone, onRemove: () => toggle(l.id) }))}
                    analyzeFn={(sel, msgs) => analyzeTa(ticker, sel, msgs)}
                    emptyHint="Select structure, liquidity or confluence layers to include them in the AI read."
                  />
                </div>
              </div>
            </>
          )}
        </div>
      )}
    </div>
  );
}
