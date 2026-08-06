import React, { useCallback, useEffect, useMemo, useState } from 'react';
import {
  Gauge, ChevronDown, ChevronUp, Loader2, RefreshCw, Eye, TrendingUp, TrendingDown, Minus,
} from 'lucide-react';
import {
  Chart as ChartJS, CategoryScale, LinearScale, PointElement, LineElement,
  Filler, Tooltip, type ChartOptions,
} from 'chart.js';
import { Line } from 'react-chartjs-2';
import { fetchRegime, analyzeTa } from '../api';
import type { RegimeData } from '../types';
import IndicatorAIConsole from './IndicatorAIConsole';
import { makeLevelPlugin, type OverlayLine } from './taOverlay';

ChartJS.register(CategoryScale, LinearScale, PointElement, LineElement, Filler, Tooltip);

interface Layer { id: string; group: string; label: string; sub?: string; tone: string; color: string; lines?: OverlayLine[]; json: Record<string, unknown> }

const regimeTone = (r?: string) => r === 'trending' ? 'text-success border-success/30 bg-success/10'
  : r === 'mean_reverting' ? 'text-info border-info/30 bg-info/10'
    : 'text-warning border-warning/30 bg-warning/10';
const regimeLabel = (r?: string) => r === 'trending' ? 'Trending' : r === 'mean_reverting' ? 'Mean-Reverting' : 'Transitional';

function buildLayers(d: RegimeData): Layer[] {
  const out: Layer[] = [];
  const tfDefs = [['daily', d.timeframes.daily, 'text-violet-400', 'rgb(139,92,246)'],
    ['h4', d.timeframes.h4, 'text-sky-400', 'rgb(56,189,248)']] as const;
  for (const [key, tf, tone, color] of tfDefs) {
    if (!tf) continue;
    out.push({
      id: `regime_${key}`, group: 'Regime', tone, color,
      label: `${tf.label} — ${regimeLabel(tf.regime)}`,
      sub: `H ${tf.hurst ?? '—'} · ER ${tf.efficiency_ratio ?? '—'} · ${tf.confidence}`,
      json: { indicator: 'regime', timeframe: key, regime: tf.regime, hurst: tf.hurst, efficiency_ratio: tf.efficiency_ratio, confidence: tf.confidence },
    });
  }
  if (d.zscore) {
    const z = d.zscore;
    out.push({
      id: 'zscore', group: 'Statistical extremes', tone: 'text-amber-400', color: 'rgb(251,191,36)',
      label: `50-day VWAP z-score ${z.z}`,
      sub: `${z.state}${z.vwap != null ? ` · VWAP $${z.vwap}` : ''}`,
      lines: z.vwap != null ? [{ price: z.vwap, label: `50d VWAP $${z.vwap} (z ${z.z})`, dash: [6, 4] }] : undefined,
      json: { indicator: 'vwap_zscore', window: z.window, vwap: z.vwap, z: z.z, state: z.state, distance_pct: z.distance_pct },
    });
  }
  return out;
}

function Banner({ d }: { d: RegimeData }) {
  const r = d.regime;
  const Icon = r.overall === 'trending' ? TrendingUp : r.overall === 'mean_reverting' ? Minus : TrendingDown;
  return (
    <div className={`rounded-lg border p-2.5 ${regimeTone(r.overall)}`}>
      <div className="flex items-center justify-between flex-wrap gap-2">
        <div className="flex items-center gap-2">
          <Icon className="w-4 h-4 shrink-0" />
          <div>
            <p className="text-sm font-bold leading-tight">{regimeLabel(r.overall)} regime
              <span className="text-[10px] uppercase tracking-wider opacity-70 ml-1">· {r.confidence} confidence{r.aligned && ' · TF-aligned'}</span>
            </p>
            <p className="text-[11px] opacity-70 mt-0.5">{r.playbook}</p>
          </div>
        </div>
        <div className="flex gap-2 text-[11px] shrink-0">
          {(['daily', 'h4'] as const).map(k => {
            const tf = d.timeframes[k];
            return <span key={k} className="badge badge-sm bg-base-100/50 border-base-content/10 tabular-nums">{k === 'daily' ? 'D' : '4H'} H{tf?.hurst ?? '—'}</span>;
          })}
        </div>
      </div>
    </div>
  );
}

export default function RegimePanel({ ticker }: { ticker: string; price?: number }) {
  const [expanded, setExpanded] = useState(false);
  const [data, setData] = useState<RegimeData | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [selected, setSelected] = useState<Set<string>>(new Set());

  const load = useCallback(async () => {
    setLoading(true); setError(null);
    try {
      const r = await fetchRegime(ticker);
      setData(r.regime);
      const def = new Set<string>();
      if (r.regime.timeframes.daily) def.add('regime_daily');
      if (r.regime.timeframes.h4) def.add('regime_h4');
      if (r.regime.zscore) def.add('zscore');
      setSelected(def);
    } catch (e: any) {
      setError(e?.message || 'Failed to load regime');
    } finally { setLoading(false); }
  }, [ticker]);

  useEffect(() => { setData(null); setSelected(new Set()); setError(null); }, [ticker]);
  useEffect(() => { if (expanded && !data && !loading && !error) load(); }, [expanded, data, loading, error, load]);

  const layers = useMemo(() => (data ? buildLayers(data) : []), [data]);
  const selectedLayers = useMemo(() => layers.filter(l => selected.has(l.id)), [layers, selected]);
  const groups = useMemo(() => {
    const m = new Map<string, Layer[]>();
    for (const l of layers) { if (!m.has(l.group)) m.set(l.group, []); m.get(l.group)!.push(l); }
    return Array.from(m.entries());
  }, [layers]);

  const selectionJson = useMemo(() => ({
    ticker, spot: data?.price ?? null, as_of: data?.as_of,
    regime_overall: data?.regime.overall, favored: data?.regime.favored,
    selected_indicators: selectedLayers.map(l => l.json),
  }), [ticker, data, selectedLayers]);

  const toggle = (id: string) => setSelected(prev => { const n = new Set(prev); n.has(id) ? n.delete(id) : n.add(id); return n; });

  const chartData = useMemo(() => {
    const s = data?.price_series;
    if (!s?.closes?.length) return null;
    const step = Math.max(1, Math.floor(s.timestamps.length / 12));
    return { labels: s.timestamps.map((t, i) => (i % step === 0 ? t.slice(5) : '')), datasets: [{ label: 'Price', data: s.closes, borderColor: 'rgb(148,163,184)', backgroundColor: 'rgba(148,163,184,0.08)', fill: true, borderWidth: 1.25, pointRadius: 0, tension: 0.1 }] };
  }, [data]);
  const chartOptions: ChartOptions<'line'> = useMemo(() => ({
    responsive: true, maintainAspectRatio: false, animation: false as const, interaction: { mode: 'index', intersect: false },
    plugins: { legend: { display: false }, tooltip: { callbacks: { label: (c) => `$${Number(c.parsed.y).toFixed(2)}` } } },
    scales: { x: { ticks: { color: '#999', maxRotation: 0, autoSkip: true, maxTicksLimit: 10 }, grid: { display: false } }, y: { position: 'right', ticks: { color: '#999', callback: (v) => `$${Number(v).toFixed(0)}` }, grid: { color: 'rgba(128,128,128,0.12)' } } },
  }), []);
  const plugin = useMemo(() => makeLevelPlugin(selectedLayers.filter(l => l.lines).map(l => ({ color: l.color, lines: l.lines }))), [selectedLayers]);

  return (
    <div className="bg-base-300 rounded-xl overflow-hidden shadow-xl border border-white/[0.05]">
      <button type="button" onClick={() => setExpanded(o => !o)} className="w-full flex items-center justify-between px-4 py-3 bg-gradient-to-r from-base-200/60 to-transparent">
        <div className="flex items-center gap-2 text-left">
          <Gauge className="w-4 h-4 text-secondary" />
          <div>
            <h4 className="text-sm font-semibold text-base-content/90">Market Regime &amp; Statistical Extremes</h4>
            <p className="text-[10px] text-base-content/50">Hurst exponent + Efficiency Ratio (Daily/4H) &amp; 50-day VWAP z-score → trend-vs-mean-revert playbook</p>
          </div>
        </div>
        {expanded ? <ChevronUp className="w-4 h-4 shrink-0" /> : <ChevronDown className="w-4 h-4 shrink-0" />}
      </button>

      {expanded && (
        <div className="p-3 border-t border-white/[0.05] space-y-3">
          {loading && <div className="flex items-center gap-2 text-sm text-base-content/60 py-6 justify-center"><Loader2 className="w-4 h-4 animate-spin" /> Classifying the regime…</div>}
          {error && !loading && <div className="alert alert-error text-xs flex items-center justify-between"><span>{error}</span><button className="btn btn-ghost btn-xs" onClick={load}><RefreshCw className="w-3.5 h-3.5" /> Retry</button></div>}

          {data && !loading && (
            <>
              <Banner d={data} />
              {data.regime.favored?.length > 0 && (
                <div className="flex flex-wrap gap-1 items-center">
                  <span className="text-[10px] uppercase tracking-wider text-base-content/40">Favored:</span>
                  {data.regime.favored.map((f, i) => <span key={i} className="badge badge-sm bg-base-100/50 border-base-content/10">{f.replace(/_/g, ' ')}</span>)}
                </div>
              )}
              <div className="grid grid-cols-1 lg:grid-cols-5 gap-3">
                <div className="lg:col-span-2 space-y-2">
                  <div className="flex items-center gap-1.5 text-[11px] text-base-content/50 uppercase tracking-wide"><Eye className="w-3.5 h-3.5" /> Indicators — shown on chart &amp; sent to AI</div>
                  {groups.map(([g, gLayers]) => (
                    <div key={g} className="rounded-lg border border-white/[0.06] bg-base-200/30 p-2">
                      <span className="text-[11px] font-bold text-base-content/70">{g}</span>
                      <div className="space-y-0.5 mt-1">
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
                  ))}
                </div>

                <div className="lg:col-span-3 space-y-3">
                  <div className="rounded-lg border border-white/[0.06] bg-base-200/20 p-2">
                    <div className="flex items-center justify-between mb-1 px-1">
                      <span className="text-[11px] font-bold text-base-content/70 flex items-center gap-1"><Gauge className="w-3.5 h-3.5 text-secondary" /> Price &amp; 50-day VWAP</span>
                      <span className="text-[10px] text-base-content/40">{selectedLayers.filter(l => l.lines).length} on chart</span>
                    </div>
                    {chartData ? <div className="h-64"><Line key={[...selected].sort().join('|')} data={chartData} options={chartOptions} plugins={[plugin]} /></div>
                      : <div className="text-center text-xs text-base-content/40 py-10">No price series available.</div>}
                  </div>
                  <IndicatorAIConsole
                    selectionJson={selectionJson}
                    chips={selectedLayers.map(l => ({ key: l.id, label: l.label, tone: l.tone, onRemove: () => toggle(l.id) }))}
                    analyzeFn={(sel, msgs) => analyzeTa(ticker, sel, msgs)}
                    emptyHint="Select the regime or z-score layers to include them in the AI read."
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
