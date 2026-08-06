import React, { useCallback, useEffect, useMemo, useState } from 'react';
import {
  Layers, Target, ChevronDown, ChevronUp, Loader2, RefreshCw, Eye,
} from 'lucide-react';
import {
  Chart as ChartJS, CategoryScale, LinearScale, PointElement, LineElement,
  Filler, Tooltip, type ChartOptions,
} from 'chart.js';
import { Line } from 'react-chartjs-2';
import { fetchMicrostructure, analyzeTa } from '../api';
import type { MicrostructureData, AnchoredVWAP } from '../types';
import IndicatorAIConsole from './IndicatorAIConsole';

ChartJS.register(CategoryScale, LinearScale, PointElement, LineElement, Filler, Tooltip);

// ─────────────────────────────────────────────────────────────────────────
// Indicator "layers" — one selectable unit that both draws on the chart and
// serializes into the AI-summary JSON (unified selection).
// ─────────────────────────────────────────────────────────────────────────
type LayerKind = 'poc' | 'va' | 'lvn' | 'naked' | 'avwap';
interface Layer {
  id: string;
  group: string;
  label: string;
  sub?: string;
  kind: LayerKind;
  tone: string;                 // tailwind text colour for the chip
  color: string;                // rgb() for the chart
  dash?: number[];
  price?: number;
  band?: [number, number];
  points?: number[];
  json: Record<string, unknown>;
}

const distTxt = (level: number, spot: number) => {
  if (!spot) return '';
  const d = ((level - spot) / spot) * 100;
  return `${d >= 0 ? '+' : ''}${d.toFixed(1)}% vs spot`;
};
const toFill = (rgb: string, a: number) => rgb.replace('rgb(', 'rgba(').replace(')', `,${a})`);

function buildLayers(data: MicrostructureData): Layer[] {
  const out: Layer[] = [];
  const spot = data.price ?? 0;
  const tfDefs = [
    ['macro', data.timeframe_profiles.macro, 'rgb(139,92,246)', 'text-violet-400'],
    ['swing', data.timeframe_profiles.swing, 'rgb(56,189,248)', 'text-sky-400'],
    ['micro', data.timeframe_profiles.micro, 'rgb(251,191,36)', 'text-amber-400'],
  ] as const;

  for (const [key, tf, color, tone] of tfDefs) {
    if (!tf) continue;
    const g = tf.label;
    out.push({
      id: `${key}_poc`, group: g, kind: 'poc', tone, color, price: tf.poc,
      label: `POC $${tf.poc}`, sub: distTxt(tf.poc, spot),
      json: { indicator: 'volume_profile_poc', timeframe: key, label: tf.label, poc: tf.poc },
    });
    out.push({
      id: `${key}_va`, group: g, kind: 'va', tone, color, band: [tf.val, tf.vah],
      label: `Value Area $${tf.val}–$${tf.vah}`, sub: `${tf.value_area_pct}% of volume`,
      json: { indicator: 'value_area', timeframe: key, vah: tf.vah, val: tf.val, value_area_pct: tf.value_area_pct },
    });
    const lvnPts = (tf.lvns || []).map(n => n.price).filter((x): x is number => x != null);
    if (lvnPts.length) {
      out.push({
        id: `${key}_lvn`, group: g, kind: 'lvn', tone, color, dash: [2, 2], points: lvnPts,
        label: `LVNs (${lvnPts.length})`, sub: lvnPts.map(p => `$${p}`).join(', '),
        json: { indicator: 'low_volume_nodes', timeframe: key, nodes: tf.lvns },
      });
    }
  }

  const nk = data.naked_pocs || [];
  if (nk.length) {
    out.push({
      id: 'naked_pocs', group: 'Naked / Virgin POCs', kind: 'naked', tone: 'text-rose-400',
      color: 'rgb(244,63,94)', dash: [6, 3], points: nk.map(n => n.price),
      label: `Naked POCs (${nk.length})`, sub: 'untested — magnet targets',
      json: { indicator: 'naked_pocs', levels: nk },
    });
  }

  const avwapEntries: [string, AnchoredVWAP | null][] = [
    ['ytd', data.avwap.ytd], ['earnings', data.avwap.earnings],
    ['high_52w', data.avwap.high_52w], ['low_52w', data.avwap.low_52w],
  ];
  for (const [k, a] of avwapEntries) {
    if (!a) continue;
    out.push({
      id: `avwap_${k}`, group: 'Anchored VWAP', kind: 'avwap', tone: 'text-emerald-400',
      color: 'rgb(16,185,129)', dash: [8, 4], price: a.value,
      label: `${a.label} $${a.value}`, sub: distTxt(a.value, spot),
      json: {
        indicator: 'anchored_vwap', anchor: k, label: a.label, anchor_date: a.anchor_date,
        value: a.value, side: a.side, distance_pct: a.distance_pct,
      },
    });
  }
  return out;
}

// Chart.js plugin: draw only the selected layers as horizontal levels / bands.
function makeMicroPlugin(layers: Layer[]): any {
  return {
    id: 'microLevels',
    afterDatasetsDraw(chart: any) {
      const { ctx, chartArea, scales } = chart;
      const y = scales?.y;
      if (!y || !chartArea) return;
      const L = chartArea.left, R = chartArea.right;
      const inRange = (p: number) => p >= y.min && p <= y.max;
      const py = (p: number) => Math.max(chartArea.top, Math.min(chartArea.bottom, y.getPixelForValue(p)));

      ctx.save();
      ctx.beginPath();
      ctx.rect(L, chartArea.top, R - L, chartArea.bottom - chartArea.top);
      ctx.clip();

      const hline = (p: number, color: string, dash: number[] | undefined, label: string) => {
        if (!inRange(p)) return;
        const yy = py(p);
        ctx.strokeStyle = color; ctx.lineWidth = 1.25; ctx.setLineDash(dash || []);
        ctx.beginPath(); ctx.moveTo(L, yy); ctx.lineTo(R, yy); ctx.stroke(); ctx.setLineDash([]);
        ctx.font = '9px sans-serif'; ctx.fillStyle = color;
        ctx.textAlign = 'right'; ctx.textBaseline = 'bottom';
        ctx.fillText(label, R - 3, yy - 1);
      };
      const band = (a: number, b: number, fill: string, color: string, label: string) => {
        if (!inRange(a) && !inRange(b)) return;
        const yt = py(Math.max(a, b)), yb = py(Math.min(a, b));
        ctx.fillStyle = fill; ctx.fillRect(L, yt, R - L, Math.max(2, yb - yt));
        ctx.font = '9px sans-serif'; ctx.fillStyle = color;
        ctx.textAlign = 'left'; ctx.textBaseline = 'top';
        ctx.fillText(label, L + 3, yt + 1);
      };

      for (const l of layers) {
        if (l.kind === 'va' && l.band) band(l.band[0], l.band[1], toFill(l.color, 0.09), l.color, 'VA');
        else if (l.kind === 'lvn' && l.points) l.points.forEach(p => hline(p, l.color, [2, 2], 'LVN'));
        else if (l.kind === 'naked' && l.points) l.points.forEach(p => hline(p, l.color, [6, 3], `Naked POC $${p}`));
        else if (l.price != null) hline(l.price, l.color, l.dash, l.label);
      }
      ctx.restore();
    },
  };
}

// ─────────────────────────────────────────────────────────────────────────

export default function MicrostructurePanel({ ticker, price }: { ticker: string; price?: number }) {
  const [expanded, setExpanded] = useState(false);
  const [data, setData] = useState<MicrostructureData | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [selected, setSelected] = useState<Set<string>>(new Set());

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const r = await fetchMicrostructure(ticker);
      const md = r.microstructure;
      setData(md);
      // sensible default: each timeframe POC + naked POCs + the YTD AVWAP
      const def = new Set<string>();
      (['macro', 'swing', 'micro'] as const).forEach(k => { if (md.timeframe_profiles[k]) def.add(`${k}_poc`); });
      if (md.naked_pocs?.length) def.add('naked_pocs');
      if (md.avwap.ytd) def.add('avwap_ytd');
      setSelected(def);
    } catch (e: any) {
      setError(e?.message || 'Failed to load microstructure');
    } finally {
      setLoading(false);
    }
  }, [ticker]);

  // reset when the ticker changes so a re-expand refetches the new name
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
    ticker,
    spot: data?.price ?? price ?? null,
    as_of: data?.as_of,
    selected_indicators: selectedLayers.map(l => l.json),
  }), [ticker, data, price, selectedLayers]);

  const toggle = (id: string) => setSelected(prev => {
    const n = new Set(prev); n.has(id) ? n.delete(id) : n.add(id); return n;
  });
  const setGroup = (gLayers: Layer[], on: boolean) => setSelected(prev => {
    const n = new Set(prev); gLayers.forEach(l => on ? n.add(l.id) : n.delete(l.id)); return n;
  });

  // chart -------------------------------------------------------------------
  const chartData = useMemo(() => {
    const s = data?.price_series;
    if (!s?.closes?.length) return null;
    const step = Math.max(1, Math.floor(s.timestamps.length / 12));
    return {
      labels: s.timestamps.map((t, i) => (i % step === 0 ? t.slice(5) : '')),
      datasets: [{
        label: 'Price', data: s.closes, borderColor: 'rgb(148,163,184)',
        backgroundColor: 'rgba(148,163,184,0.08)', fill: true, borderWidth: 1.25,
        pointRadius: 0, tension: 0.1,
      }],
    };
  }, [data]);
  const chartOptions: ChartOptions<'line'> = useMemo(() => ({
    responsive: true, maintainAspectRatio: false, animation: false as const,
    interaction: { mode: 'index', intersect: false },
    plugins: { legend: { display: false }, tooltip: { callbacks: { label: (c) => `$${Number(c.parsed.y).toFixed(2)}` } } },
    scales: {
      x: { ticks: { color: '#999', maxRotation: 0, autoSkip: true, maxTicksLimit: 10 }, grid: { display: false } },
      y: { position: 'right', ticks: { color: '#999', callback: (v) => `$${Number(v).toFixed(0)}` }, grid: { color: 'rgba(128,128,128,0.12)' } },
    },
  }), []);
  const microPlugin = useMemo(() => makeMicroPlugin(selectedLayers), [selectedLayers]);

  // ── render ──
  return (
    <div className="bg-base-300 rounded-xl overflow-hidden shadow-xl border border-white/[0.05]">
      <button type="button" onClick={() => setExpanded(o => !o)}
        className="w-full flex items-center justify-between px-4 py-3 bg-gradient-to-r from-base-200/60 to-transparent">
        <div className="flex items-center gap-2 text-left">
          <Layers className="w-4 h-4 text-secondary" />
          <div>
            <h4 className="text-sm font-semibold text-base-content/90">Microstructure &amp; Multi-Timeframe Volume Profile</h4>
            <p className="text-[10px] text-base-content/50">POC · Value Area · LVNs across 3 horizons, naked POCs &amp; anchored-VWAP matrix → AI trade read</p>
          </div>
        </div>
        {expanded ? <ChevronUp className="w-4 h-4 shrink-0" /> : <ChevronDown className="w-4 h-4 shrink-0" />}
      </button>

      {expanded && (
        <div className="p-3 border-t border-white/[0.05] space-y-3">
          {loading && (
            <div className="flex items-center gap-2 text-sm text-base-content/60 py-6 justify-center">
              <Loader2 className="w-4 h-4 animate-spin" /> Building volume-profile hierarchy…
            </div>
          )}
          {error && !loading && (
            <div className="alert alert-error text-xs flex items-center justify-between">
              <span>{error}</span>
              <button className="btn btn-ghost btn-xs" onClick={load}><RefreshCw className="w-3.5 h-3.5" /> Retry</button>
            </div>
          )}

          {data && !loading && (
            <div className="grid grid-cols-1 lg:grid-cols-5 gap-3">
              {/* Left: indicator tray */}
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
                        <button className="text-[10px] text-primary hover:underline"
                          onClick={() => setGroup(gLayers, !allOn)}>{allOn ? 'clear' : 'all'}</button>
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
              </div>

              {/* Right: chart + AI */}
              <div className="lg:col-span-3 space-y-3">
                <div className="rounded-lg border border-white/[0.06] bg-base-200/20 p-2">
                  <div className="flex items-center justify-between mb-1 px-1">
                    <span className="text-[11px] font-bold text-base-content/70 flex items-center gap-1"><Target className="w-3.5 h-3.5 text-secondary" /> Level map — last ~6 months</span>
                    <span className="text-[10px] text-base-content/40">{selectedLayers.length} layer{selectedLayers.length === 1 ? '' : 's'} shown</span>
                  </div>
                  {chartData ? (
                    <div className="h-64"><Line key={[...selected].sort().join('|')} data={chartData} options={chartOptions} plugins={[microPlugin]} /></div>
                  ) : (
                    <div className="text-center text-xs text-base-content/40 py-10">No price series available.</div>
                  )}
                </div>

                {/* AI console (shared) */}
                <IndicatorAIConsole
                  selectionJson={selectionJson}
                  chips={selectedLayers.map(l => ({ key: l.id, label: l.label, tone: l.tone, onRemove: () => toggle(l.id) }))}
                  analyzeFn={(sel, msgs) => analyzeTa(ticker, sel, msgs)}
                  emptyHint="Select indicators on the left to include them in the AI read."
                />
              </div>
            </div>
          )}
        </div>
      )}
    </div>
  );
}
