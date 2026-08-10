import React, { useCallback, useEffect, useMemo, useState } from 'react';
import { Boxes, ChevronDown, ChevronUp, Loader2, RefreshCw, Eye, Zap } from 'lucide-react';
import {
  Chart as ChartJS, CategoryScale, LinearScale, PointElement, LineElement,
  Filler, Tooltip, type ChartOptions,
} from 'chart.js';
import { Line } from 'react-chartjs-2';
import { fetchDealerPositioning, analyzeTa } from '../api';
import type { DealerPositioningData } from '../types';
import IndicatorAIConsole from './IndicatorAIConsole';
import GexChart from './GexChart';
import { makeLevelPlugin, type OverlayLine, type OverlayBand } from './taOverlay';

ChartJS.register(CategoryScale, LinearScale, PointElement, LineElement, Filler, Tooltip);

const RED = 'rgb(239,68,68)';
const GREEN = 'rgb(34,197,94)';

interface Layer { id: string; group: string; label: string; sub?: string; tone: string; color: string; lines?: OverlayLine[]; bands?: OverlayBand[]; json: Record<string, unknown> }

function buildLayers(d: DealerPositioningData): Layer[] {
  const out: Layer[] = [];
  const g = d.net_gex;
  out.push({
    id: 'net_gex', group: 'Regime', tone: g.sign === 'long' ? 'text-info' : 'text-warning', color: 'rgb(148,163,184)',
    label: `Net GEX ${g.value_millions ?? '—'}M (${g.sign} gamma)`, sub: g.sign === 'long' ? 'vol suppressed / pin' : 'vol expansion / trend',
    json: { indicator: 'net_gex', value_millions: g.value_millions, sign: g.sign, label: g.label },
  });
  const gl = d.gamma_levels;
  if (d.gamma_flip?.level != null) {
    const f = d.gamma_flip;
    out.push({
      id: 'gamma_flip', group: 'Key levels', tone: 'text-amber-400', color: 'rgb(251,191,36)',
      label: `Gamma flip $${f.level}`, sub: `spot ${f.side} flip (${f.distance_pct}%) — vol-regime pivot`,
      lines: [{ price: f.level as number, label: `γ-flip $${f.level}`, color: 'rgb(251,191,36)', dash: [1, 2] }],
      json: { indicator: 'gamma_flip', level: f.level, side: f.side, distance_pct: f.distance_pct, note: f.note },
    });
  }
  const addLevel = (id: string, lvl: { strike: number | null; distance_pct: number | null; gex_millions?: number | null } | null | undefined,
                    name: string, color: string, tone: string, note: string) => {
    if (!lvl?.strike) return;
    out.push({
      id, group: 'Key levels', tone, color,
      label: `${name} $${lvl.strike}`,
      sub: `${lvl.distance_pct != null ? `${lvl.distance_pct > 0 ? '+' : ''}${lvl.distance_pct}% vs spot · ` : ''}${note}`,
      lines: [{ price: lvl.strike, label: `${name} $${lvl.strike}`, color, dash: [5, 3] }],
      json: { indicator: id, strike: lvl.strike, distance_pct: lvl.distance_pct, gex_millions: lvl.gex_millions ?? null },
    });
  };
  if (gl) {
    addLevel('call_resistance', gl.call_resistance, 'Call Resistance', RED, 'text-error', 'biggest +GEX — caps rallies');
    addLevel('put_support', gl.put_support, 'Put Support', GREEN, 'text-success', 'biggest −GEX — floors dips');
    addLevel('hvl', gl.hvl, 'HVL', 'rgb(139,92,246)', 'text-violet-400', 'dominant gamma magnet / pin');
  } else {
    const w = d.walls;
    if (w.call_wall?.strike != null) addLevel('call_resistance', { strike: w.call_wall.strike, distance_pct: null, gex_millions: w.call_wall.gex_millions }, 'Call Resistance', RED, 'text-error', 'call wall');
    if (w.put_wall?.strike != null) addLevel('put_support', { strike: w.put_wall.strike, distance_pct: null, gex_millions: w.put_wall.gex_millions }, 'Put Support', GREEN, 'text-success', 'put wall');
  }
  const em = d.expected_move;
  const emLayer = (id: string, e: typeof em.em_30d, color: string, tone: string, tag: string) => {
    if (!e || e.upper == null || e.lower == null) return;
    out.push({
      id, group: 'Expected move', tone, color,
      label: `${tag} expected move ±${e.move_pct}%`, sub: `$${e.lower}–$${e.upper} · ATM IV ${e.iv_atm_pct}%`,
      bands: [{ top: e.upper, bottom: e.lower, label: `${tag} ±1σ`, color }],
      json: { indicator: 'expected_move', horizon: tag, dte: e.dte, iv_atm_pct: e.iv_atm_pct, move_pct: e.move_pct, upper: e.upper, lower: e.lower },
    });
  };
  emLayer('em_30d', em.em_30d, 'rgb(56,189,248)', 'text-sky-400', '30d');
  emLayer('em_45d', em.em_45d, 'rgb(139,92,246)', 'text-violet-400', '45d');
  return out;
}

function Banner({ d }: { d: DealerPositioningData }) {
  const long = d.net_gex.sign === 'long';
  return (
    <div className={`rounded-lg border p-2.5 ${long ? 'text-info border-info/30 bg-info/10' : 'text-warning border-warning/30 bg-warning/10'}`}>
      <div className="flex items-center gap-2">
        <Zap className="w-4 h-4 shrink-0" />
        <div>
          <p className="text-sm font-bold leading-tight">Dealers {long ? 'LONG' : 'SHORT'} gamma · Net GEX {d.net_gex.value_millions ?? '—'}M</p>
          <p className="text-[11px] opacity-70 mt-0.5">{d.net_gex.label}{d.gamma_flip?.note ? ` ${d.gamma_flip.note}` : ''}</p>
        </div>
      </div>
    </div>
  );
}

export default function DealerPositioningPanel({ ticker }: { ticker: string; price?: number }) {
  const [expanded, setExpanded] = useState(false);
  const [data, setData] = useState<DealerPositioningData | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [selected, setSelected] = useState<Set<string>>(new Set());

  const load = useCallback(async () => {
    setLoading(true); setError(null);
    try {
      const r = await fetchDealerPositioning(ticker);
      const dp = r.dealer_positioning;
      setData(dp);
      const def = new Set<string>(['net_gex']);
      if (dp.gamma_flip?.level != null) def.add('gamma_flip');
      if (dp.gamma_levels?.call_resistance || dp.walls.call_wall) def.add('call_resistance');
      if (dp.gamma_levels?.put_support || dp.walls.put_wall) def.add('put_support');
      if (dp.gamma_levels?.hvl) def.add('hvl');
      if (dp.expected_move.em_30d) def.add('em_30d');
      setSelected(def);
    } catch (e: any) {
      setError(e?.message || 'Failed to load dealer positioning');
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
    net_gex_sign: data?.net_gex.sign, expirations_used: data?.expirations_used,
    selected_indicators: selectedLayers.map(l => l.json),
  }), [ticker, data, selectedLayers]);

  const toggle = (id: string) => setSelected(prev => { const n = new Set(prev); n.has(id) ? n.delete(id) : n.add(id); return n; });
  const setGroup = (gLayers: Layer[], on: boolean) => setSelected(prev => { const n = new Set(prev); gLayers.forEach(l => on ? n.add(l.id) : n.delete(l.id)); return n; });

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
  const plugin = useMemo(() => makeLevelPlugin(selectedLayers.filter(l => l.lines || l.bands).map(l => ({ color: l.color, lines: l.lines, bands: l.bands }))), [selectedLayers]);

  return (
    <div className="bg-base-300 rounded-xl overflow-hidden shadow-xl border border-white/[0.05]">
      <button type="button" onClick={() => setExpanded(o => !o)} className="w-full flex items-center justify-between px-4 py-3 bg-gradient-to-r from-base-200/60 to-transparent">
        <div className="flex items-center gap-2 text-left">
          <Boxes className="w-4 h-4 text-secondary" />
          <div>
            <h4 className="text-sm font-semibold text-base-content/90">Dealer Positioning <span className="text-[10px] text-base-content/40">(Gamma / GEX)</span></h4>
            <p className="text-[10px] text-base-content/50">Net GEX regime · gamma flip · Call Resistance / Put Support / HVL · expected move — where dealer hedging gates price</p>
          </div>
        </div>
        {expanded ? <ChevronUp className="w-4 h-4 shrink-0" /> : <ChevronDown className="w-4 h-4 shrink-0" />}
      </button>

      {expanded && (
        <div className="p-3 border-t border-white/[0.05] space-y-3">
          {loading && <div className="flex items-center gap-2 text-sm text-base-content/60 py-6 justify-center"><Loader2 className="w-4 h-4 animate-spin" /> Reading the option chain…</div>}
          {error && !loading && <div className="alert alert-error text-xs flex items-center justify-between"><span>{error}</span><button className="btn btn-ghost btn-xs" onClick={load}><RefreshCw className="w-3.5 h-3.5" /> Retry</button></div>}

          {data && !loading && (
            <>
              <Banner d={data} />
              <div className="grid grid-cols-1 lg:grid-cols-5 gap-3">
                <div className="lg:col-span-2 space-y-2">
                  <div className="flex items-center gap-1.5 text-[11px] text-base-content/50 uppercase tracking-wide"><Eye className="w-3.5 h-3.5" /> Indicators — shown on chart &amp; sent to AI</div>
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
                </div>

                <div className="lg:col-span-3 space-y-3">
                  {/* GEX-by-strike distribution — the hero chart */}
                  <div className="rounded-lg border border-white/[0.06] bg-base-200/20 p-2">
                    <GexChart profile={data.gex_profile || []} levels={data.gamma_levels} spot={data.price || 0} netGexMM={data.net_gex.value_millions} />
                  </div>
                  <div className="rounded-lg border border-white/[0.06] bg-base-200/20 p-2">
                    <div className="flex items-center justify-between mb-1 px-1">
                      <span className="text-[11px] font-bold text-base-content/70 flex items-center gap-1"><Boxes className="w-3.5 h-3.5 text-secondary" /> Price · gamma levels · expected move</span>
                      <span className="text-[10px] text-base-content/40">{selectedLayers.filter(l => l.lines || l.bands).length} on chart</span>
                    </div>
                    {chartData ? <div className="h-64"><Line key={[...selected].sort().join('|')} data={chartData} options={chartOptions} plugins={[plugin]} /></div>
                      : <div className="text-center text-xs text-base-content/40 py-10">No price series available.</div>}
                  </div>
                  <IndicatorAIConsole
                    selectionJson={selectionJson}
                    chips={selectedLayers.map(l => ({ key: l.id, label: l.label, tone: l.tone, onRemove: () => toggle(l.id) }))}
                    analyzeFn={(sel, msgs) => analyzeTa(ticker, sel, msgs)}
                    emptyHint="Select gamma or expected-move layers to include them in the AI read."
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
